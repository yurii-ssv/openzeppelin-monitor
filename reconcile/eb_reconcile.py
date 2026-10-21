#!/usr/bin/env python3
"""SSV watch worker: the stateful half of the monitoring service (plan §6), grown from the #94775 worker.

The worker is read-only against the chain. Each step:
  1. checks for a reorg below the checkpoint and rewinds (logs, cursors, inventory),
  2. scans confirmed SSVNetwork proxy logs into SQLite and advances a durable checkpoint
     (`eth_getLogs` in bounded windows; an error is never read as "no logs"),
  3. ingests the Monitor's failed-transaction inbox and resolves revert selectors,
  4. reloads live protocol parameters after a parameter event, then runs every due check
     (interval, event, inbox and near-set-per-block schedules) and turns its findings into
     incidents and outbox messages,
  5. re-alerts open incidents, sends the daily digest, delivers the outbox.
Checks live in checks/ (interface: checks/README.md); M5 (#94775) is checks/m5_eb_desync.py.

Commands:
  run           loop forever
  once          one step with every check forced, then exit (--serve-metrics --hold N keeps /metrics up)
  audit         M5 event-only history report of every root window (no storage reads)
  status        print checkpoint, check runs, open incidents and undelivered messages
  verify-layout check the pinned storage layout against the deployed proxy (M5 guard)

Secrets come from environment variables named in the config.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import checks as registry
from alerts import Alerts
from checks.base import Ctx, Every, NearSetEveryBlock, OnEvents, OnInbox
from checks.m5_eb_desync import audit  # noqa: F401 - re-exported (CLI, tests)
from db import DB  # noqa: F401 - re-exported
from eb_events import PARAM_KINDS, TOPIC0, decode_log
from inbox import Inbox
from inventory import API_DEFAULT, InventoryCache
from notify import RecordOnly, Telegram, make_notifier  # noqa: F401 - re-exported
from params import Params
from rpc import Chain, Rpc, RpcError

log = logging.getLogger("eb-reconcile")

END_OF_BLOCK = 2**31  # log_index sentinel: "after every log of this block"


# --------------------------------------------------------------------------- config


@dataclass
class Config:
    raw: dict

    def __getattr__(self, name):
        try:
            return self.raw[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    @property
    def rpc_url(self) -> str:
        url = os.environ.get(self.raw["rpc_url_env"], "").strip().strip("\"'")
        if not url:
            raise SystemExit(f"environment variable {self.raw['rpc_url_env']} is not set")
        return url

    @property
    def history_complete(self) -> bool:
        return self.raw["start_block"] <= self.raw["activation_block"]

    @property
    def alerts(self) -> dict:
        return self.raw.get("alerts", {})

    def section(self, name: str) -> dict:
        return self.raw.get(name) or {}


DEFAULTS = {
    "confirmations": 12,
    "poll_interval_s": 30,
    "sweep_interval_s": 300,
    "full_sweep_interval_s": 3600,
    "max_log_range": 5000,
    "rpc_batch_size": 50,
    "rpc_timeout_s": 30,
    "rpc_retries": 4,
    "lag_alert_blocks": 50,
    "reorg_search_depth": 256,
}

TOP_LEVEL_KEYS = set(DEFAULTS) | {
    "_comment", "chain_id", "proxy", "views", "activation_block", "start_block", "pr614_guards_from", "rpc_url_env",
    "db_path", "layout", "alerts", "checks", "inbox", "inventory", "metrics",
}


def load_config(path: str) -> Config:
    raw = dict(DEFAULTS)
    raw.update(json.loads(Path(path).read_text()))
    unknown = set(raw) - TOP_LEVEL_KEYS
    if unknown:
        raise SystemExit(f"config: unknown key(s) {sorted(unknown)}")
    raw["proxy"] = raw["proxy"].lower()
    raw.setdefault("start_block", raw["activation_block"])
    return Config(raw)


def make_rpc(cfg: Config) -> Rpc:
    return Rpc(cfg.rpc_url, cfg.rpc_timeout_s, cfg.rpc_retries, cfg.rpc_batch_size)


# --------------------------------------------------------------------------- worker


class Worker:
    def __init__(self, cfg: Config, rpc: Rpc, db: DB, notifier=None, clock=time.time, checks=None,
                 http_get=None):
        self.cfg, self.rpc, self.db, self.clock = cfg, rpc, db, clock
        self.notifier = notifier
        self.rpc_failures = 0
        self.alerts = Alerts(db, cfg.alerts, clock, cfg.alerts.get("label", f"chain {cfg.chain_id}"))
        built = registry.build(cfg.raw) if checks is None else checks
        self.checks = {c.id: c for c in built}
        with self.db.tx():
            for c in self.checks.values():
                for stmt in filter(str.strip, c.schema.split(";")):
                    self.db.conn.execute(stmt)
        self.topics = sorted(set(TOPIC0.values()))
        self.params = Params(cfg.raw.get("views"), cfg.proxy)
        inbox_cfg = cfg.section("inbox")
        self.inbox = None
        if inbox_cfg.get("enabled", True) and inbox_cfg.get("path"):
            kw = {"http_get": http_get} if http_get else {}
            self.inbox = Inbox(db, inbox_cfg["path"], inbox_cfg.get("blockscout"), **kw)
        inv_cfg = cfg.section("inventory")
        kw = {"http_get": http_get} if http_get else {}
        self.inventory = InventoryCache(
            db, lambda b: Chain(self.rpc, b, cfg.max_log_range), cfg.proxy, clock,
            api_base=inv_cfg.get("api_url", API_DEFAULT) or API_DEFAULT, use_api=bool(inv_cfg.get("api_url", API_DEFAULT)),
            api_refresh_s=inv_cfg.get("api_refresh_s", 600), **kw)
        self.last_run: dict[str, float] = {}
        self.last_block: dict[str, int] = {}
        self.prepared: set[str] = set()
        self.pending: dict[str, set[str]] = {c: set() for c in self.checks}

    # ---- identity / bootstrap

    def bootstrap(self) -> None:
        chain_id = int(self.rpc.call("eth_chainId", []), 16)
        if chain_id != self.cfg.chain_id:
            raise SystemExit(f"RPC chain id {chain_id} != configured {self.cfg.chain_id}")
        stored = self.db.meta("identity")
        ident = {"chain_id": chain_id, "proxy": self.cfg.proxy, "start_block": self.cfg.start_block}
        if stored and stored != ident:
            raise SystemExit(f"database belongs to {stored}, config is {ident}; use a new db_path")
        with self.db.tx():
            if not stored:
                self.db.set_meta("identity", ident)
            if self.db.meta("checkpoint") is None:
                self.db.set_meta("checkpoint", {"block": self.cfg.start_block - 1, "hash": None})
        known = self.db.meta("topics")
        new = sorted(set(self.topics) - set(known or []))
        if known is not None and new:
            self.backfill(new)
        with self.db.tx():
            self.db.set_meta("topics", self.topics)

    def checkpoint(self) -> dict:
        return self.db.meta("checkpoint")

    def _cursor(self, check_id: str) -> tuple[int, int]:
        return tuple(self.db.meta(f"cursor:events:{check_id}", [self.cfg.start_block - 1, END_OF_BLOCK]))

    # ---- scanning

    def target_block(self) -> int:
        if self.cfg.confirmations < 0:
            blk = self.rpc.call("eth_getBlockByNumber", ["finalized", False])
            return int(blk["number"], 16)
        return self.rpc.block_number() - self.cfg.confirmations

    def check_reorg(self) -> None:
        cp = self.checkpoint()
        if not cp["hash"]:
            return
        if self.rpc.block_hash(cp["block"]) == cp["hash"]:
            return
        rows = self.db.conn.execute(
            "SELECT number, hash FROM blocks WHERE number<=? ORDER BY number DESC LIMIT ?",
            (cp["block"], self.cfg.reorg_search_depth),
        ).fetchall()
        ancestor = None
        for r in rows:
            if self.rpc.block_hash(r["number"]) == r["hash"]:
                ancestor = (r["number"], r["hash"])
                break
        if ancestor is None:
            raise SystemExit(f"reorg deeper than {self.cfg.reorg_search_depth} recorded blocks below {cp['block']}")
        with self.db.tx():
            n = self.db.conn.execute("DELETE FROM logs WHERE block>?", (ancestor[0],)).rowcount
            self.db.conn.execute("DELETE FROM blocks WHERE number>?", (ancestor[0],))
            self.db.set_meta("checkpoint", {"block": ancestor[0], "hash": ancestor[1]})
            for cid in self.checks:
                if self._cursor(cid)[0] > ancestor[0]:
                    self.db.set_meta(f"cursor:events:{cid}", [ancestor[0], END_OF_BLOCK])
            self.alerts.health(
                "reorg",
                f"Chain reorganisation below the confirmation horizon: checkpoint {cp['block']} rewound to "
                f"{ancestor[0]}, {n} logs discarded and replayed. Incidents are re-evaluated on the new chain.",
                dedup=f"reorg:{cp['block']}:{ancestor[0]}",
            )
        self.inventory.invalidate()
        for cid, check in self.checks.items():
            check.on_reorg(ancestor[0])
            self.pending[cid].add("reorg")

    def _ingest(self, logs: list[dict]) -> list:
        events = []
        for lg in logs:
            if lg.get("removed"):
                continue
            if lg["address"].lower() != self.cfg.proxy:
                raise RpcError("eth_getLogs returned a log for another address")
            ev = decode_log(lg)
            if ev is not None:
                events.append(ev)
        return events

    def scan(self) -> int:
        """Advance the checkpoint to the target block; returns the number of new logs."""
        target = self.target_block()
        cp = self.checkpoint()
        total = 0
        start = cp["block"] + 1
        step = self.cfg.max_log_range
        while start <= target:
            end = min(target, start + step - 1)
            events = self._ingest(self.rpc.get_logs(self.cfg.proxy, start, end, step, self.topics))
            end_hash = self.rpc.block_hash(end)
            with self.db.tx():
                for ev in events:
                    self.db.insert_event(ev)
                for bn, bh in {(ev.block, ev.block_hash) for ev in events} | {(end, end_hash)}:
                    self.db.conn.execute("INSERT OR REPLACE INTO blocks VALUES (?, ?)", (bn, bh))
                self.db.set_meta("checkpoint", {"block": end, "hash": end_hash})
                self.db.set_meta("last_scan_at", self.clock())
            total += len(events)
            if events:
                log.info("scanned %d-%d: %d events", start, end, len(events))
            start = end + 1
        return total

    def backfill(self, topics: list[str]) -> int:
        """Events newly tracked on an existing database: fetch their history up to the checkpoint once."""
        upto = self.checkpoint()["block"]
        step = self.cfg.max_log_range
        total = 0
        start = self.cfg.start_block
        log.info("backfilling %d new event topic(s) from %d to %d", len(topics), start, upto)
        while start <= upto:
            end = min(upto, start + step - 1)
            events = self._ingest(self.rpc.get_logs(self.cfg.proxy, start, end, step, topics))
            with self.db.tx():
                for ev in events:
                    self.db.insert_event(ev)
            total += len(events)
            start = end + 1
        return total

    # ---- inbox

    def poll_inbox(self) -> None:
        if self.inbox is None:
            return
        with self.db.tx():
            new, bad = self.inbox.read()
            for line in bad:
                self.alerts.health("inbox-malformed", f"Malformed failed-tx inbox line skipped:\n{line}", dedup=line)
            self.inbox.resolve(self.rpc)
        if new:
            log.info("inbox: %d new failed transaction(s)", new)

    # ---- scheduler

    def _ctx(self, check, block: int, reasons, chain=None, events=None, inbox=None) -> Ctx:
        return Ctx(
            check_id=check.id, block=block, rpc=chain or Chain(self.rpc, block, self.cfg.max_log_range), db=self.db,
            params=self.params, config=check.config, settings=self.cfg.raw, clock=self.clock,
            log=logging.getLogger(f"eb-reconcile.{check.id}"), reasons=frozenset(reasons),
            _events=events, _inbox=inbox, _inventory=self.inventory.get, _health=self.alerts.health,
        )

    def _kinds(self, check):
        kinds = [s.kinds for s in check.schedules() if isinstance(s, OnEvents)]
        return frozenset().union(*kinds) if kinds else None

    def due(self, check, block: int, now: float, force: bool) -> set[str]:
        reasons = set(self.pending.get(check.id, ()))
        if force:
            reasons.add("force")
        for s in check.schedules():
            if isinstance(s, Every):
                if now - self.last_run.get(check.id, float("-inf")) >= s.seconds:
                    reasons.add("interval")
            elif isinstance(s, OnEvents):
                if self.db.has_events_after(self._cursor(check.id), block, s.kinds):
                    reasons.add("events")
            elif isinstance(s, OnInbox):
                if self.inbox is not None and self.inbox.pending(self.db.meta(f"cursor:inbox:{check.id}", 0)):
                    reasons.add("inbox")
            elif isinstance(s, NearSetEveryBlock):
                if check.near_set and block > self.last_block.get(check.id, -1):
                    reasons.add("block")
        return reasons

    def reload_params(self, block: int) -> None:
        if not self.params.enabled:
            return
        changed = self.params.block is not None and self.db.has_events_after(
            (self.params.block, END_OF_BLOCK), block, PARAM_KINDS)
        if self.params.block is not None and not changed:
            return
        try:
            self.params.load(Chain(self.rpc, block, self.cfg.max_log_range), block)
        except RpcError as exc:
            if exc.transport:
                raise
            with self.db.tx():
                self.alerts.health("params", f"Live parameters could not be read at block {block}: {exc}",
                                   dedup=f"params:{exc}", realert=True)
            return
        log.info("live parameters %s at block %d", "reloaded" if changed else "loaded", block)
        if changed:
            for cid in self.checks:
                self.pending[cid].add("params")

    def run_checks(self, force: bool = False) -> None:
        block = self.checkpoint()["block"]
        if block < self.cfg.start_block:
            return
        self.reload_params(block)
        now = self.clock()
        first_rpc_error = None
        for check in self.checks.values():
            reasons = self.due(check, block, now, force)
            if not reasons:
                continue
            try:
                self.run_check(check, block, reasons)
            except RpcError as exc:
                first_rpc_error = first_rpc_error or exc
                self._run_failed(check, exc, health=False)
            except Exception as exc:  # noqa: BLE001 - one broken check must not stop the others
                log.exception("check %s failed at block %d", check.id, block)
                self._run_failed(check, exc, health=True)
        if first_rpc_error is not None:
            raise first_rpc_error

    def run_check(self, check, block: int, reasons: set[str]) -> list:
        kinds = self._kinds(check)
        cursor = self._cursor(check.id)
        wants_inbox = any(isinstance(s, OnInbox) for s in check.schedules())
        entries = self.inbox.pending(self.db.meta(f"cursor:inbox:{check.id}", 0)) if wants_inbox and self.inbox else []
        chain = Chain(self.rpc, block, self.cfg.max_log_range)
        events = (lambda: self.db.events_after(cursor, block, kinds)) if kinds else None
        ctx = self._ctx(check, block, reasons, chain=chain, events=events, inbox=lambda: entries)
        now = self.clock()
        with self.db.tx():
            if check.id not in self.prepared or "params" in reasons:
                check.prepare(ctx)
            findings = list(check.run(ctx, block) or [])
            for f in findings:
                if f.check != check.id:
                    raise ValueError(f"check {check.id} returned a finding for {f.check!r}")
            self.alerts.apply(findings, block)
            if check.resolve_on_absence:
                self.alerts.resolve_absent(check.id, {f.key for f in findings if not f.resolve}, block)
            self.db.set_meta(f"cursor:events:{check.id}", [block, END_OF_BLOCK])
            if entries:
                self.db.set_meta(f"cursor:inbox:{check.id}", entries[-1].id)
            prev = self.db.meta(f"run:{check.id}", {})
            self.db.set_meta(f"run:{check.id}", {
                "at": now, "block": block, "reasons": sorted(reasons), "findings": len(findings),
                "rpc_calls": dict(chain.counts), "errors": prev.get("errors", 0), "last_error": prev.get("last_error"),
            })
        self.prepared.add(check.id)
        self.last_run[check.id] = now
        self.last_block[check.id] = block
        self.pending[check.id] = set()
        return findings

    def _run_failed(self, check, exc: Exception, health: bool) -> None:
        with self.db.tx():
            prev = self.db.meta(f"run:{check.id}", {})
            self.db.set_meta(f"run:{check.id}", {**prev, "errors": prev.get("errors", 0) + 1,
                                                 "last_error": f"{type(exc).__name__}: {exc}"[:300]})
            if health:
                self.alerts.health("check-error", f"Check {check.id} raised {type(exc).__name__}: {exc}"[:1000],
                                   dedup=f"check-error:{check.id}:{type(exc).__name__}", realert=True,
                                   check_id=check.id)

    def housekeeping(self) -> None:
        """Re-alerts of open incidents and the daily digest."""
        block = self.checkpoint()["block"]
        with self.db.tx():
            self.alerts.realert()
            if self.alerts.digest_due() and block >= self.cfg.start_block:
                sections = {c.id: c.digest(self._ctx(c, block, {"digest"})) for c in self.checks.values()}
                self.alerts.digest(block, sections)

    # ---- delivery

    def deliver(self) -> None:
        if self.notifier is None:
            return
        now = self.clock()
        rows = self.db.conn.execute(
            "SELECT * FROM outbox WHERE sent_at IS NULL AND next_attempt_at<=? ORDER BY created_at", (now,)
        ).fetchall()
        for r in rows:
            try:
                receipt = self.notifier.send(r["severity"], r["text"], r["key"], r["delivery"])
                self.db.conn.execute("UPDATE outbox SET sent_at=?, receipt=? WHERE key=?",
                                     (self.clock(), receipt, r["key"]))
            except Exception as exc:  # noqa: BLE001 - delivery errors must never stop the worker
                attempts = r["attempts"] + 1
                backoff = min(30 * 2 ** attempts, 1800)
                self.db.conn.execute(
                    "UPDATE outbox SET attempts=?, next_attempt_at=?, last_error=? WHERE key=?",
                    (attempts, now + backoff, str(exc)[:300], r["key"]),
                )
                log.error("alert delivery failed (%s, attempt %d): %s", r["key"], attempts, exc)
                if attempts == 5:
                    log.critical("ALERT DELIVERY EXHAUSTED for %s; alert text follows\n%s", r["key"], r["text"])

    # ---- health

    def lag_check(self) -> None:
        try:
            target = self.target_block()
        except RpcError:
            return
        cp = self.checkpoint()["block"]
        lag = target - cp
        self.db.set_meta("lag_blocks", lag)
        if lag > self.cfg.lag_alert_blocks:
            with self.db.tx():
                self.alerts.health("lag", f"Checkpoint {cp} is {lag} blocks behind the confirmed head {target}.",
                                   dedup="lag", realert=True)

    def status(self) -> dict:
        return {
            "identity": self.db.meta("identity"), "schema_version": self.db.meta("schema_version"),
            "checkpoint": self.checkpoint(), "lag_blocks": self.db.meta("lag_blocks"),
            "checks": {c: {**self.db.meta(f"run:{c}", {}), "state": self.db.meta(f"check:{c}:last_sweep")}
                       for c in self.checks},
            "params_block": self.params.block,
            "open_incidents": {f"{c}/{s}": n for c, s, n in self.db.conn.execute(
                "SELECT check_id, severity, COUNT(*) FROM incidents WHERE state='open' GROUP BY check_id, severity")},
            "undelivered": self.db.conn.execute("SELECT COUNT(*) FROM outbox WHERE sent_at IS NULL").fetchone()[0],
        }

    def write_status(self, healthy: bool, error: str | None = None) -> None:
        path = Path(self.cfg.db_path).with_suffix(".status.json")
        status = {"healthy": healthy, "at": self.clock(), "error": error, **self.status()}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(status, indent=1))
        tmp.replace(path)

    def step(self, force: bool = False) -> None:
        try:
            self.check_reorg()
            self.scan()
            self.poll_inbox()
            self.run_checks(force=force)
            self.rpc_failures = 0
            self.db.set_meta("rpc_failures", 0)
            self.write_status(True)
        except RpcError as exc:
            self.rpc_failures += 1
            self.db.set_meta("rpc_failures", self.rpc_failures)
            log.warning("RPC failure %d: %s", self.rpc_failures, exc)
            if self.rpc_failures in (3, 20, 100):
                with self.db.tx():
                    self.alerts.health("rpc", f"{self.rpc_failures} consecutive RPC failures; last: {exc}. "
                                       f"Checkpoint {self.checkpoint()['block']}.", dedup=f"rpc:{self.rpc_failures}",
                                       realert=True)
            self.write_status(False, str(exc))
        self.lag_check()
        self.housekeeping()
        self.deliver()


# --------------------------------------------------------------------------- cli


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["run", "once", "audit", "status", "verify-layout"])
    ap.add_argument("--config", default=os.environ.get("EB_RECONCILE_CONFIG", "config.json"))
    ap.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "info"))
    ap.add_argument("--serve-metrics", action="store_true", help="serve /metrics (also on with metrics.enabled)")
    ap.add_argument("--metrics-host", default=None)
    ap.add_argument("--metrics-port", type=int, default=None)
    ap.add_argument("--hold", type=float, default=0, help="once: keep serving metrics this many seconds")
    args = ap.parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        stream=sys.stderr)

    cfg = load_config(args.config)
    db = DB(cfg.db_path)

    if args.command == "audit":
        print(json.dumps(audit(db, cfg.start_block), indent=1))
        return 0

    lock = open(cfg.db_path + ".lock", "w")
    if args.command != "status":
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log.error("another worker holds %s.lock", cfg.db_path)
            return 2

    if args.command == "status":
        worker = Worker(cfg, None, db)
        out = worker.status()
        out["open"] = [dict(r) for r in db.conn.execute(
            "SELECT id, check_id, key, severity, title, opened_block, last_seen_block, alerts FROM incidents "
            "WHERE state='open' ORDER BY opened_at")]
        print(json.dumps(out, indent=1))
        return 0

    rpc = make_rpc(cfg)
    worker = Worker(cfg, rpc, db, make_notifier(cfg.alerts))
    worker.bootstrap()

    if args.command == "verify-layout":
        block = worker.target_block()
        m5 = worker.checks["m5"]
        problems = m5.verify_layout(Chain(rpc, block), db, block, deep=True)
        print(json.dumps({"block": block, "ok": not problems, "problems": problems}, indent=1))
        return 0 if not problems else 1

    mcfg = cfg.section("metrics")
    server = None
    if args.serve_metrics or mcfg.get("enabled"):
        from metrics import MetricsServer

        server = MetricsServer(cfg.db_path, list(worker.checks), args.metrics_host or mcfg.get("host", "127.0.0.1"),
                               args.metrics_port if args.metrics_port is not None else mcfg.get("port", 9464)).start()
        log.info("metrics on http://%s:%d/metrics", server.httpd.server_address[0], server.port)

    if args.command == "once":
        worker.step(force=True)
        print(json.dumps(worker.status(), indent=1), flush=True)
        if server and args.hold:
            time.sleep(args.hold)
        return 0 if worker.rpc_failures == 0 else 1

    stop = {"flag": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(flag=True))
    signal.signal(signal.SIGINT, lambda *_: stop.update(flag=True))
    log.info("starting: proxy %s from block %d (checkpoint %d), checks %s, alerts=%s", cfg.proxy, cfg.start_block,
             worker.checkpoint()["block"], sorted(worker.checks), cfg.alerts.get("mode", "record"))
    while not stop["flag"]:
        worker.step()
        for _ in range(int(cfg.poll_interval_s)):
            if stop["flag"]:
                break
            time.sleep(1)
    log.info("stopped at checkpoint %d", worker.checkpoint()["block"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
