#!/usr/bin/env python3
"""SSV watch worker: the stateful half of the monitoring service (plan §6), grown from the #94775 worker.

The worker is read-only against the chain. Each step:
  1. checks for a reorg below the checkpoint and rewinds every piece of block-derived state (logs,
     cursors, inventory, live parameters, inbox confirmations; checks/README.md §6.1),
  2. scans confirmed SSVNetwork proxy logs into SQLite and advances a durable checkpoint
     (`eth_getLogs` in bounded windows; an error is never read as "no logs"; logs of a non-canonical
     block are rejected),
  3. ingests the Monitor's failed-transaction inbox, resolves revert selectors and confirms each entry's
     block hash once the checkpoint passes it (a configured inbox that cannot be read is monitor
     health, never an empty input),
  4. reloads live protocol parameters after a parameter event or a reorg (a failed read invalidates
     them and is retried every step), then runs every due check (interval, event, inbox and
     near-set-per-block schedules) and turns its findings into incidents and outbox messages,
  5. re-alerts open incidents, sends the daily digest, delivers the outbox,
  6. watches the Monitor's progress (its last processed block; monitor_watch.py, D20): a stall, a missing file or a
     skipped block range is a health message, never silence.
Checks live in checks/ (interface: checks/README.md); M5 (#94775) is checks/m5_eb_desync.py.

Commands:
  run           loop forever
  once          one step with every check forced, then exit (--serve-metrics --hold N keeps /metrics up)
  audit         M5 event-only history report of every root window (no storage reads)
  status        print phase (backfill/live), checkpoint, check runs, open incidents and undelivered messages
  export        print every incident and outbox message as JSON (soak evidence, audits); no state change
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
from dataclasses import dataclass, replace
from itertools import takewhile
from pathlib import Path

import checks as registry
from alerts import Alerts
from checks.base import Ctx, Every, NearSetEveryBlock, OnEvents, OnInbox
from checks.m5_eb_desync import audit  # noqa: F401 - re-exported (CLI, tests)
from db import DB  # noqa: F401 - re-exported
from eb_events import PARAM_KINDS, TOPIC0, decode_log
from inbox import Inbox, InboxUnavailable
from inventory import API_DEFAULT, InventoryCache
from monitor_watch import MonitorWatch
from monitor_watch import settings as monitor_settings
from notify import RecordOnly, Telegram, make_notifier  # noqa: F401 - re-exported
from params import Params, ParamsUnavailable
from rpc import DEFAULT_RPC_URLS, Chain, Rpc, RpcError, resolve_endpoints

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
    def rpc_endpoints(self) -> list[tuple[str, str]]:
        """[(label, url)] in failover order (D16): `rpc_urls` (URLs or environment variable names; default
        rpc.DEFAULT_RPC_URLS), the first replaced by the variable `rpc_url_env` names when it is set."""
        endpoints = resolve_endpoints(self.raw.get("rpc_urls") or DEFAULT_RPC_URLS, self.raw.get("rpc_url_env"))
        if not endpoints:
            raise SystemExit("no RPC endpoint: rpc_urls is empty and the variables it names are unset")
        return endpoints

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
    "rpc_primary_retry_s": 300,
    "lag_alert_blocks": 50,
    "reorg_search_depth": 256,
}

# Silent backfill (D17): history from start_block is stored and evaluated without alerting. The first checkpoint
# within `live_within_blocks` of the scan target is the caught-up checkpoint (`history_block`): every check
# evaluates history silently once, pinned there; blocks after it are live. A check alerts from its first run after
# its own silent evaluation, whatever the other checks do (one that keeps failing its silent run raises check-error
# health and does not hold the others back). `blocks_per_step` bounds the scan per step while backfilling (progress
# in `status`, the status file and /metrics stay fresh).
BACKFILL_DEFAULTS = {"enabled": True, "live_within_blocks": 12, "blocks_per_step": 50_000}

TOP_LEVEL_KEYS = set(DEFAULTS) | {
    "_comment", "chain_id", "proxy", "views", "activation_block", "start_block", "pr614_guards_from", "rpc_url_env",
    "rpc_urls", "rpc_primary_retry_s", "backfill", "db_path", "layout", "alerts", "checks", "inbox", "inventory", "metrics",
    "monitor",
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
    return Rpc(cfg.rpc_endpoints, cfg.rpc_timeout_s, cfg.rpc_retries, cfg.rpc_batch_size, cfg.rpc_primary_retry_s)


# --------------------------------------------------------------------------- worker


class Worker:
    def __init__(self, cfg: Config, rpc: Rpc, db: DB, notifier=None, clock=time.time, checks=None,
                 http_get=None):
        self.cfg, self.rpc, self.db, self.clock = cfg, rpc, db, clock
        self.notifier = notifier
        self.rpc_failures = 0
        self.rpc_outage_since = None  # clock of the first failed step of the current outage
        self.head_read = None  # (head, clock) of the last eth_blockNumber answer, for the Monitor watch
        self.bootstrapped = False
        self.alerts = Alerts(db, cfg.alerts, clock, cfg.alerts.get("label", f"chain {cfg.chain_id}"))
        built = registry.build(cfg.raw) if checks is None else checks
        self.checks = {c.id: c for c in built}
        if not getattr(db, "readonly", False):
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
        watch_cfg = monitor_settings(cfg.raw.get("monitor"))
        self.monitor = MonitorWatch(watch_cfg, db, self.alerts, clock) if watch_cfg else None
        self.backfill_cfg = {**BACKFILL_DEFAULTS, **cfg.section("backfill")}
        unknown = set(self.backfill_cfg) - set(BACKFILL_DEFAULTS)
        if unknown:
            raise SystemExit(f"backfill: unknown key(s) {sorted(unknown)}; accepted: {sorted(BACKFILL_DEFAULTS)}")
        self.last_run: dict[str, float] = {}
        self.last_block: dict[str, int] = {}
        self.prepared: set[str] = set()
        self.pending: dict[str, set[str]] = {c: set() for c in self.checks}
        for cid, check in self.checks.items():  # a reorg whose re-run did not commit before a restart
            ancestor = self.db.meta(f"reorg:{cid}")
            if ancestor is not None:
                check.on_reorg(ancestor)
                self.pending[cid].add("reorg")

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
                if self.backfill_cfg["enabled"]:
                    self.db.set_meta("phase", {"phase": "backfill", "since": self.clock(), "evaluated": []})
            if self.db.meta("phase") is None:  # backfill disabled, or a database that was already alerting
                self.db.set_meta("phase", {"phase": "live", "since": self.clock(), "first_live": []})
        known = self.db.meta("topics")
        new = sorted(set(self.topics) - set(known or []))
        if known is not None and new:
            self.backfill(new)
        with self.db.tx():
            self.db.set_meta("topics", self.topics)
        self.bootstrapped = True

    def checkpoint(self) -> dict:
        return self.db.meta("checkpoint")

    # ---- phase (D17 silent backfill)

    def phase(self) -> dict:
        return self.db.meta("phase") or {"phase": "live", "first_live": []}

    @property
    def backfilling(self) -> bool:
        return self.phase()["phase"] == "backfill"

    def caught_up(self, block: int) -> bool:
        target = self.db.meta("scan_target")
        return target is not None and target - block <= self.backfill_cfg["live_within_blocks"]

    def _phase_progress(self) -> dict:
        ph = self.phase()
        cp, target = (self.checkpoint() or {}).get("block"), self.db.meta("scan_target")
        out = {**ph, "checkpoint": cp, "target": target}
        if ph["phase"] == "backfill" and target is not None and cp is not None:
            start = self.cfg.start_block
            out["remaining_blocks"] = max(target - cp, 0)
            out["progress"] = round(min(max((cp - start + 1) / max(target - start + 1, 1), 0), 1), 4)
        return out

    def wait_s(self) -> float:
        """Seconds to wait before the next step: none while a backfill still has blocks to scan and the last step
        succeeded (bounded steps back to back); otherwise `poll_interval_s` (also while the caught-up backfill waits
        for a check that keeps failing its silent run: no tight retry loop)."""
        if self.bootstrapped and self.rpc_failures == 0 and self.backfilling \
                and not self.caught_up(self.checkpoint()["block"]):
            return 0
        return self.cfg.poll_interval_s

    def _history_block(self, block: int) -> int:
        """The caught-up checkpoint (D17): history ends there. Set once, at the first caught-up step."""
        ph = self.phase()
        if ph.get("history_block") is None:
            with self.db.tx():  # a backfill phase written before CP3 repair 1: its evaluated checks still run live
                ph = {**ph, "history_block": block, "first_live": ph.get("first_live", ph.get("evaluated", []))}
                self.db.set_meta("phase", ph)
            log.info("caught up at block %d: history ends there; each check alerts after its silent run", block)
        return ph["history_block"]

    def _go_live(self, block: int) -> None:
        """Every check has evaluated history silently at the caught-up checkpoint: the backfill phase ends. Each
        check's first run after its own silent evaluation is a forced full run (reasons `live`, `force`), at a
        block after the caught-up checkpoint, so a condition still true there is reported (and sent) while one
        that only existed in history stays unsent; the checks that have not had it yet keep it."""
        ph = self.phase()
        with self.db.tx():
            self.db.set_meta("phase", {"phase": "live", "since": self.clock(), "caught_up_block": block,
                                       "history_block": ph["history_block"], "first_live": ph.get("first_live", [])})
        log.info("backfill complete at block %d: every check evaluated history at block %d", block,
                 ph["history_block"])

    def _cursor(self, check_id: str) -> tuple[int, int]:
        return tuple(self.db.meta(f"cursor:events:{check_id}", [self.cfg.start_block - 1, END_OF_BLOCK]))

    # ---- scanning

    def target_block(self) -> int:
        if self.cfg.confirmations < 0:
            blk = self.rpc.call("eth_getBlockByNumber", ["finalized", False])
            return int(blk["number"], 16)
        head = self.rpc.block_number()
        self.head_read = (head, self.clock())
        return head - self.cfg.confirmations

    def check_reorg(self) -> None:
        """Rewind every piece of state derived from blocks above the last common ancestor (checks/README.md §6.1).

        Durable (one transaction): logs, recorded block hashes, the checkpoint, event cursors, the inbox
        entries' canonical flag, and a per-check `reorg:<id>` marker (lowest ancestor since the check's
        last successful run: `ctx.reorg_ancestor`, survives a crash). In memory, after the commit: the
        inventory cache, the live parameters and every `prepare` copy (re-read before the next run), each
        check's `on_reorg`, and the `reorg` run reason."""
        cp = self.checkpoint()
        if not cp["hash"]:
            return
        # A block the node does not have (the new chain is shorter) is not canonical either.
        if self.rpc.block_hash(cp["block"], missing_ok=True) == cp["hash"]:
            return
        rows = self.db.conn.execute(
            "SELECT number, hash FROM blocks WHERE number<=? ORDER BY number DESC LIMIT ?",
            (cp["block"], self.cfg.reorg_search_depth),
        ).fetchall()
        ancestor = None
        for r in rows:
            if self.rpc.block_hash(r["number"], missing_ok=True) == r["hash"]:
                ancestor = (r["number"], r["hash"])
                break
        if ancestor is None:
            raise SystemExit(f"reorg deeper than {self.cfg.reorg_search_depth} recorded blocks below {cp['block']}")
        with self.db.tx():
            n = self.db.conn.execute("DELETE FROM logs WHERE block>?", (ancestor[0],)).rowcount
            self.db.conn.execute("DELETE FROM blocks WHERE number>?", (ancestor[0],))
            self.db.conn.execute("UPDATE inbox SET canonical=NULL WHERE block>?", (ancestor[0],))
            self.db.set_meta("checkpoint", {"block": ancestor[0], "hash": ancestor[1]})
            for cid in self.checks:
                if self._cursor(cid)[0] > ancestor[0]:
                    self.db.set_meta(f"cursor:events:{cid}", [ancestor[0], END_OF_BLOCK])
                prev = self.db.meta(f"reorg:{cid}")
                self.db.set_meta(f"reorg:{cid}", ancestor[0] if prev is None else min(prev, ancestor[0]))
            self.alerts.health(
                "reorg",
                f"Chain reorganisation below the confirmation horizon: checkpoint {cp['block']} rewound to "
                f"{ancestor[0]}, {n} logs discarded and replayed. Incidents are re-evaluated on the new chain.",
                dedup=f"reorg:{cp['block']}:{ancestor[0]}",
            )
        self._after_reorg(ancestor[0])

    def _after_reorg(self, ancestor: int) -> None:
        self.inventory.invalidate()
        if self.params.enabled:
            # Values read above the ancestor may come from a deleted block; even without a parameter
            # event on the new chain they are re-read at the new checkpoint before any check runs.
            self.params.invalidate(f"chain reorganised above block {ancestor}; not yet re-read")
        self.prepared.clear()
        for cid, check in self.checks.items():
            check.on_reorg(ancestor)
            self.pending[cid].add("reorg")
            self.last_block.pop(cid, None)

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
        stop = target
        if self.backfilling:
            stop = min(target, cp["block"] + max(int(self.backfill_cfg["blocks_per_step"]), 1))
        self.db.set_meta("scan_target", target)
        while start <= stop:
            end = min(stop, start + step - 1)
            switches = self.rpc.switches
            events = self._ingest(self.rpc.get_logs(self.cfg.proxy, start, end, step, self.topics))
            end_hash = self.rpc.block_hash(end)
            if self.rpc.switches != switches:  # logs from one endpoint, the hash from another: not comparable
                raise RpcError(f"the RPC endpoint changed while reading blocks {start}-{end}; the window is re-read "
                               f"at the next step")
            self._verify_log_blocks(events, end, end_hash, target)
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

    def _verify_log_blocks(self, events: list, end: int, end_hash: str, target: int) -> None:
        """Every stored log must belong to the chain whose hash the checkpoint records: a reorg between
        `eth_getLogs` and the hash read (or a load-balanced RPC answering from another fork) would store
        logs of deleted blocks that no later hash comparison could find. Blocks deeper than
        `reorg_search_depth` below the target are final for the worker and not re-checked."""
        want = {ev.block: ev.block_hash for ev in events if ev.block > target - self.cfg.reorg_search_depth}
        others = [b for b in want if b != end]
        canonical = self.rpc.block_hashes(others) if others else {}
        canonical[end] = end_hash
        bad = sorted(b for b, h in want.items() if canonical.get(b) != h)
        if bad:
            raise RpcError(f"eth_getLogs returned logs of non-canonical block(s) {bad[:5]}; the window ending "
                           f"at {end} is re-read at the next step")

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
            try:
                new, bad = self.inbox.read()
            except InboxUnavailable as exc:
                new, bad = 0, []
                self._inbox_unavailable(exc)
            else:
                if self.db.meta("inbox:unavailable") is not None:
                    log.info("inbox %s is readable again", self.inbox.path)
                    self.db.set_meta("inbox:unavailable", None)
            for line in bad:
                self.alerts.health("inbox-malformed", f"Malformed failed-tx inbox line skipped:\n{line}", dedup=line)
            self.inbox.resolve(self.rpc)
            self._confirm_inbox()
        if new:
            log.info("inbox: %d new failed transaction(s)", new)

    def _confirm_inbox(self) -> None:
        """Entries reach checks only from canonical blocks at or below the checkpoint (inbox.py). A failed
        hash lookup holds them (not delivered) and degrades health until a lookup succeeds."""
        cp = self.checkpoint()["block"]
        try:
            orphaned = self.inbox.confirm(self.rpc, cp)
        except RpcError as exc:
            log.warning("inbox entries held, block hashes unavailable: %s", exc)
            self.db.set_meta("inbox:unverified", {"since": (self.db.meta("inbox:unverified") or {}).get(
                "since", self.clock()), "error": f"inbox block hashes could not be read at checkpoint {cp}: {exc}"})
            return
        self.db.set_meta("inbox:unverified", None)
        for r in orphaned:
            log.warning("inbox %s: block %d hash %s is not canonical; orphaned entry not delivered",
                        r["tx_hash"], r["block"], r["block_hash"])

    def _inbox_unavailable(self, exc: InboxUnavailable) -> None:
        """A configured inbox that cannot be read: health (once per outage, re-sent every health window)."""
        down = self.db.meta("inbox:unavailable")
        if down is None:
            log.error("%s", exc)
            down = {"since": self.clock(), "outage": self.db.meta("inbox:outages", 0) + 1}
            self.db.set_meta("inbox:outages", down["outage"])
        self.db.set_meta("inbox:unavailable", {**down, "error": str(exc)})
        self.alerts.health(
            "inbox-unavailable",
            f"{exc}. Failed-transaction evidence is not ingested and checks scheduled OnInbox see no new "
            f"entries until it is readable again (an existing empty file is a valid, empty inbox).",
            dedup=f"inbox-unavailable:{self.inbox.path}:{down['outage']}", realert=True)

    # ---- scheduler

    def _ctx(self, check, block: int, reasons, chain=None, events=None, inbox=None, reorg_ancestor=None,
             reorg_keys=frozenset()) -> Ctx:
        return Ctx(
            check_id=check.id, block=block, rpc=chain or Chain(self.rpc, block, self.cfg.max_log_range), db=self.db,
            params=self.params, config=check.config, settings=self.cfg.raw, clock=self.clock,
            log=logging.getLogger(f"eb-reconcile.{check.id}"), reasons=frozenset(reasons),
            reorg_ancestor=reorg_ancestor, reorg_keys=reorg_keys,
            _events=events, _inbox=inbox, _inventory=self.inventory.get, _health=self.alerts.health,
        )

    def _kinds(self, check):
        kinds = [s.kinds for s in check.schedules() if isinstance(s, OnEvents)]
        return frozenset().union(*kinds) if kinds else None

    def due(self, check, block: int, now: float, force: bool) -> set[str]:
        reasons = set(self.pending.get(check.id, ()))
        if force:
            reasons.add("force")
        ph = self.phase()
        if ph["phase"] == "backfill" and check.id not in ph.get("evaluated", ()):
            reasons |= {"backfill", "force"}  # the silent evaluation of history (D17), a full run
        elif check.id in ph.get("first_live", ()):
            reasons |= {"live", "force"}  # the first live evaluation after the backfill, a full run
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
        """Load the parameters at the first run and after a parameter event; retry every step after a failure.

        A failed read invalidates the values (every access raises ParamsUnavailable) and drops every
        check's `prepare`, so neither `ctx.params` nor a copy taken in `prepare` is used until a read
        succeeds; the runs that need them fail without advancing their cursors and are retried."""
        if not self.params.enabled:
            return
        changed = self.params.block is not None and self.db.has_events_after(
            (self.params.block, END_OF_BLOCK), block, PARAM_KINDS)
        if self.params.ok and self.params.block is not None and not changed:
            return
        recovered = not self.params.ok
        try:
            self.params.load(Chain(self.rpc, block, self.cfg.max_log_range), block)
        except (RpcError, ValueError) as exc:
            self.params.invalidate(f"read at block {block} failed: {exc}")
            self.prepared.clear()
            if isinstance(exc, RpcError) and exc.transport:
                raise
            log.error("live parameters unavailable: %s", self.params.error)
            with self.db.tx():
                self.alerts.health("params", f"Live parameters could not be read at block {block}: {exc}. Checks "
                                   f"that read them do not run until a read succeeds (retried every step).",
                                   dedup=f"params:{exc}", realert=True)
            return
        log.info("live parameters %s at block %d", "reloaded" if changed or recovered else "loaded", block)
        if changed or recovered:
            for cid in self.checks:
                self.pending[cid].add("params")

    def run_checks(self, force: bool = False) -> None:
        block = self.checkpoint()["block"]
        if block < self.cfg.start_block:
            return
        backfilling = self.backfilling
        if backfilling and not self.caught_up(block):
            return  # history is still being stored; checks evaluate it once the checkpoint caught up
        history = self._history_block(block) if backfilling else self.phase().get("history_block")
        self.reload_params(block)
        now = self.clock()
        first_rpc_error = None
        for check in self.checks.values():
            try:
                if backfilling and check.id not in self.phase().get("evaluated", ()):
                    # its silent evaluation of history, pinned to the caught-up checkpoint even when it is retried
                    # later (a failed run): the blocks after it are evaluated live by the run below
                    at = min(history, block)
                    self.run_check(check, at, self.due(check, at, now, force))
                if history is not None and block <= history:
                    continue  # alerting starts at the first block after the caught-up checkpoint
                reasons = self.due(check, block, now, force)
                if reasons:
                    self.run_check(check, block, reasons)
            except RpcError as exc:
                first_rpc_error = first_rpc_error or exc
                self._run_failed(check, exc, health=False)
            except ParamsUnavailable as exc:  # the "params" health message covers it; retried next step
                log.warning("check %s skipped at block %d: %s", check.id, block, exc)
                self._run_failed(check, exc, health=False)
            except Exception as exc:  # noqa: BLE001 - one broken check must not stop the others
                log.exception("check %s failed at block %d", check.id, block)
                self._run_failed(check, exc, health=True)
            finally:
                self.between_checks()
        if backfilling and set(self.checks) <= set(self.phase().get("evaluated", ())):
            self._go_live(block)
        if first_rpc_error is not None:
            raise first_rpc_error

    def run_check(self, check, block: int, reasons: set[str]) -> list:
        kinds = self._kinds(check)
        cursor = self._cursor(check.id)
        wants_inbox = any(isinstance(s, OnInbox) for s in check.schedules())
        entries = self.inbox.pending(self.db.meta(f"cursor:inbox:{check.id}", 0)) if wants_inbox and self.inbox else []
        entries = list(takewhile(lambda e: e.block <= block, entries))  # a run pinned below the checkpoint
        chain = Chain(self.rpc, block, self.cfg.max_log_range)
        events = (lambda: self.db.events_after(cursor, block, kinds)) if kinds else None
        reorg_ancestor = self.db.meta(f"reorg:{check.id}")
        reorg_keys = frozenset()
        if reorg_ancestor is not None:  # open incidents whose latest support may come from a deleted block
            reorg_keys = frozenset(r[0] for r in self.db.conn.execute(
                "SELECT key FROM incidents WHERE check_id=? AND state='open' AND last_seen_block>?",
                (check.id, reorg_ancestor)))
        ctx = self._ctx(check, block, reasons, chain=chain, events=events, inbox=lambda: entries,
                        reorg_ancestor=reorg_ancestor, reorg_keys=reorg_keys)
        now = self.clock()
        with self.db.tx():
            if check.id not in self.prepared or "params" in reasons:
                check.prepare(ctx)
            findings = list(check.run(ctx, block) or [])
            for f in findings:
                if f.check != check.id:
                    raise ValueError(f"check {check.id} returned a finding for {f.check!r}")
            ph = self.phase()
            silent = ph["phase"] == "backfill" and check.id not in ph.get("evaluated", ())
            # The check's silent evaluation of history (D17): incidents are opened, escalated and resolved without
            # a message; one still reported by a live run is sent then (an unsent incident is "unmuted" by its
            # first notifying finding).
            self.alerts.apply([replace(f, notify=False) if silent and not f.resolve else f for f in findings], block)
            if reorg_ancestor is not None:  # the reorg re-find rule (checks/README.md §6.2)
                self.alerts.resolve_not_refound(check.id, reorg_keys - {f.key for f in findings}, block,
                                                reorg_ancestor)
            if check.resolve_on_absence:
                self.alerts.resolve_absent(check.id, {f.key for f in findings if not f.resolve}, block)
            self.db.set_meta(f"cursor:events:{check.id}", [block, END_OF_BLOCK])
            if reorg_ancestor is not None:
                self.db.set_meta(f"reorg:{check.id}", None)
            if entries:
                self.db.set_meta(f"cursor:inbox:{check.id}", entries[-1].id)
            if silent:
                self.db.set_meta("phase", {**ph, "evaluated": sorted({*ph.get("evaluated", ()), check.id}),
                                           "first_live": sorted({*ph.get("first_live", ()), check.id})})
            elif check.id in ph.get("first_live", ()):
                self.db.set_meta("phase", {**ph, "first_live": [c for c in ph["first_live"] if c != check.id]})
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
            if self.alerts.digest_due() and block >= self.cfg.start_block and not self.backfilling:
                sections = {c.id: self._digest(c, block) for c in self.checks.values()}
                self.alerts.digest(block, sections)

    def _digest(self, check, block: int) -> list[str]:
        try:
            return check.digest(self._ctx(check, block, {"digest"}))
        except Exception as exc:  # noqa: BLE001 - e.g. ParamsUnavailable; one check must not stop the digest
            log.warning("digest of %s failed: %s", check.id, exc)
            return [f"(digest unavailable: {type(exc).__name__}: {exc})"[:300]]

    # ---- delivery

    def deliver(self) -> None:
        if self.notifier is None:
            return
        now = self.clock()
        rows = self.db.conn.execute(
            "SELECT * FROM outbox WHERE sent_at IS NULL AND next_attempt_at<=? ORDER BY created_at", (now,)
        ).fetchall()
        for r in rows:
            # A row without a route (queued by the pre-framework worker, db.migrate) takes its severity's.
            route = r["delivery"] or self.alerts.routes.get(r["severity"])
            try:
                receipt = self.notifier.send(r["severity"], r["text"], r["key"], route)
                self.db.conn.execute("UPDATE outbox SET sent_at=?, receipt=?, delivery=? WHERE key=?",
                                     (self.clock(), receipt, route, r["key"]))
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

    def lag_check(self) -> int | None:
        """The lag health check; returns the head it read (None when the RPC fails) for the Monitor watch."""
        try:
            head = self.rpc.block_number()
            target = self.target_block() if self.cfg.confirmations < 0 else head - self.cfg.confirmations
        except RpcError:
            return None
        self.head_read = (head, self.clock())
        cp = self.checkpoint()["block"]
        lag = target - cp
        self.db.set_meta("lag_blocks", lag)
        behind_by_design = self.backfilling and self.phase().get("history_block") is None  # still storing history
        if lag > self.cfg.lag_alert_blocks and not behind_by_design:
            with self.db.tx():
                self.alerts.health("lag", f"Checkpoint {cp} is {lag} blocks behind the confirmed head {target}.",
                                   dedup="lag", realert=True)
        return head

    def watch_monitor(self, head: int | None, estimated: bool = False) -> None:
        """One observation of the Monitor's last processed block (monitor_watch.py); runs every step, also when the
        step's RPC reads failed (then the head is unknown, which is a stall once it lasts), and between two check
        runs of a step (`between_checks`)."""
        if self.monitor is not None:
            with self.db.tx():
                self.monitor.observe(head, estimated)

    def between_checks(self) -> None:
        """A step can run for minutes (the silent evaluation after a backfill, a weekly comparison, public-RPC rate
        limits): observe the Monitor and deliver what is queued between two check runs, so a Monitor stall (or a
        page) is not held until the step ends. The head is extrapolated from the last one read (one block per
        `block_time_s`; a missed slot makes the estimate high, i.e. the lag larger: never a false recovery); a head
        older than 15 min is unknown."""
        if self.monitor is None:
            return
        head = None
        if self.head_read is not None and (age := self.clock() - self.head_read[1]) <= 900:
            head = self.head_read[0] + int(age // self.monitor.cfg["block_time_s"])
        self.watch_monitor(head, estimated=True)
        self.deliver()

    def _inbox_problem(self) -> dict | None:
        if self.inbox is None:
            return None
        return self.db.meta("inbox:unavailable") or self.db.meta("inbox:unverified")

    def status(self) -> dict:
        inbox_down = self._inbox_problem()
        counts = dict(self.db.conn.execute(
            "SELECT CASE WHEN canonical=0 THEN 'orphaned' WHEN canonical IS NULL THEN 'held' ELSE 'delivered' END, "
            "COUNT(*) FROM inbox GROUP BY 1").fetchall())
        return {
            "identity": self.db.meta("identity"), "schema_version": self.db.meta("schema_version"),
            "checkpoint": self.checkpoint(), "lag_blocks": self.db.meta("lag_blocks"),
            "phase": self._phase_progress(),
            "checks": {c: {**self.db.meta(f"run:{c}", {}), "state": self.db.meta(f"check:{c}:last_sweep")}
                       for c in self.checks},
            "params_block": self.params.block,
            "reorg_pending": {c: a for c in self.checks if (a := self.db.meta(f"reorg:{c}")) is not None},
            "params": {"enabled": self.params.enabled, "ok": self.params.ok, "block": self.params.block,
                       "error": self.params.error},
            "inbox": {"enabled": self.inbox is not None, "ok": not inbox_down,
                      "path": self.inbox.path if self.inbox else None, "error": (inbox_down or {}).get("error"),
                      "held": counts.get("held", 0), "orphaned": counts.get("orphaned", 0)},
            "open_incidents": {f"{c}/{s}": n for c, s, n in self.db.conn.execute(
                "SELECT check_id, severity, COUNT(*) FROM incidents WHERE state='open' GROUP BY check_id, severity")},
            "undelivered": self.db.conn.execute("SELECT COUNT(*) FROM outbox WHERE sent_at IS NULL").fetchone()[0],
            "monitor": self.monitor.status() if self.monitor else {"enabled": False},
        }

    def degraded(self) -> list[str]:
        """Inputs the checks need but cannot currently read, and reorgs whose re-evaluation has not committed
        (incidents may still rest on deleted blocks; the step still succeeded)."""
        out = []
        if not self.params.ok:
            out.append(f"live parameters unavailable: {self.params.error}")
        if down := self._inbox_problem():
            out.append(f"inbox unavailable: {down['error']}")
        if self.monitor is not None and (stalled := self.monitor.problem()):
            out.append(stalled)
        pending = sorted(c for c in self.checks if self.db.meta(f"reorg:{c}") is not None)
        if pending:
            out.append(f"reorg re-evaluation pending for check(s) {', '.join(pending)}")
        return out

    def write_status(self, healthy: bool, error: str | None = None) -> None:
        path = Path(self.cfg.db_path).with_suffix(".status.json")
        status = {"healthy": healthy, "at": self.clock(), "error": error, **self.status()}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(status, indent=1))
        tmp.replace(path)

    def step(self, force: bool = False) -> None:
        failed = None  # the RPC error of a failed step (the status file says healthy: false with it)
        try:
            if not self.bootstrapped:  # an RPC outage at start is an RPC failure like any other, not a crash loop
                self.bootstrap()
            self.check_reorg()
            self.scan()
            self.poll_inbox()
            self.run_checks(force=force)
            if self.rpc_failures >= 3:  # the outage was announced: say it is over
                with self.db.tx():
                    self.alerts.health("rpc-recovered", f"RPC reads succeed again after {self.rpc_failures} "
                                       f"consecutive failures; checkpoint {self.checkpoint()['block']}.",
                                       dedup=f"rpc-recovered:{self.checkpoint()['block']}")
            self.rpc_failures = 0
            self.db.set_meta("rpc_failures", 0)
        except RpcError as exc:
            failed = exc
            self.rpc_failures += 1
            if self.rpc_failures == 1:
                self.rpc_outage_since = self.clock()
            self.db.set_meta("rpc_failures", self.rpc_failures)
            log.warning("RPC failure %d: %s", self.rpc_failures, exc)
            if self.rpc_failures in (3, 20, 100):
                cp = self.checkpoint()
                with self.db.tx():
                    self.alerts.health("rpc", f"{self.rpc_failures} consecutive RPC failures; last: {exc}. "
                                       f"Checkpoint {cp['block'] if cp else 'not set (bootstrap)'}.",
                                       dedup=f"rpc:{self.rpc_outage_since}:{self.rpc_failures}")  # per outage
            if not self.bootstrapped:  # no checkpoint yet: no status file (the healthcheck stays red)
                self.watch_monitor(None)
                self.deliver()
                return
        head = self.lag_check()
        self.watch_monitor(head)
        if failed is None:
            problems = self.degraded()
            self.write_status(not problems, "; ".join(problems) or None)
        else:
            self.write_status(False, str(failed))
        inbox_down = bool(self._inbox_problem())
        self.db.set_meta("inputs_unavailable", {"params": not self.params.ok, "inbox": inbox_down,
                                                "monitor": bool(self.monitor and self.monitor.problem())})
        self.housekeeping()
        self.deliver()


# --------------------------------------------------------------------------- cli


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["run", "once", "audit", "status", "export", "verify-layout"])
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
    db = DB(cfg.db_path, readonly=args.command in ("audit", "status", "export"))

    if args.command == "audit":
        print(json.dumps(audit(db, cfg.start_block), indent=1))
        return 0

    if args.command == "export":
        print(json.dumps({
            "incidents": [dict(r) for r in db.conn.execute("SELECT * FROM incidents ORDER BY opened_at, rowid")],
            "outbox": [dict(r) for r in db.conn.execute("SELECT * FROM outbox ORDER BY created_at, rowid")],
        }, indent=1))
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
    log.info("RPC endpoints in failover order: %s", ", ".join(label for label, _ in rpc.endpoints))

    if args.command == "verify-layout":
        worker.bootstrap()
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
    log.info("starting: proxy %s from block %d (checkpoint %s), checks %s, alerts=%s", cfg.proxy, cfg.start_block,
             (worker.checkpoint() or {}).get("block"), sorted(worker.checks), cfg.alerts.get("mode", "record"))
    while not stop["flag"]:
        worker.step()
        for _ in range(int(worker.wait_s())):
            if stop["flag"]:
                break
            time.sleep(1)
    log.info("stopped at checkpoint %s", (worker.checkpoint() or {}).get("block"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
