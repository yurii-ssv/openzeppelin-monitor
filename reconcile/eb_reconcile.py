#!/usr/bin/env python3
"""EB reconciliation worker for Immunefi #94775 (register-after-root EB collapse).

Implements plans/94775-eb-collapse-monitoring.md:
  - scans finalized SSVNetwork logs into SQLite (the durable checkpoint is the no-gap recovery path),
  - replays each cluster's validator changes since its last consumed root (reference block S),
  - reads clusterEB / cluster records at the same block B with eth_getStorageAt,
  - classifies clean / latent / confirmed / unknown and routes alerts through a durable outbox.

Commands:
  run           loop forever (scan, evaluate, alert)
  once          one scan + full evaluation, then exit
  audit         event-only history report of every root window (no storage reads)
  status        print checkpoint, open incidents and the last evaluation summary
  verify-layout check the pinned storage layout against the deployed proxy

Read-only against the chain. Secrets come from environment variables named in the config.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import signal
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import eb_model as M
from eb_events import CLUSTER_KINDS, TOPIC0, Event, decode_log
from eb_storage import (
    IMPLEMENTATION_SLOT,
    Cluster,
    cluster_eb_slot,
    eth_cluster_slot,
    keccak,
    module_slot,
    ssv_cluster_slot,
    u256,
    vunits_to_eb,
    vunits_to_eth_str,
)

log = logging.getLogger("eb-reconcile")

TRACKED_TOPICS = [TOPIC0[k] for k in sorted(CLUSTER_KINDS | {"RootCommitted", "OracleReplaced"})]
GET_EB_SELECTOR = keccak(b"getEffectiveBalance(address,uint64[],(uint32,uint64,uint64,bool,uint256))")[:4]


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
    def guards_from(self) -> tuple[int, int]:
        """Log position of the PR #614 hotfix module upgrade (zero-validator EB guards)."""
        return tuple(self.raw.get("pr614_guards_from", (0, 0)))

    @property
    def alerts(self) -> dict:
        return self.raw.get("alerts", {})


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


def load_config(path: str) -> Config:
    raw = dict(DEFAULTS)
    raw.update(json.loads(Path(path).read_text()))
    raw["proxy"] = raw["proxy"].lower()
    raw.setdefault("start_block", raw["activation_block"])
    return Config(raw)


# --------------------------------------------------------------------------- rpc


class RpcError(Exception):
    """transport=True: the endpoint was unreachable; False: it answered with a JSON-RPC error."""

    def __init__(self, msg: str, transport: bool = False):
        super().__init__(msg)
        self.transport = transport


class Rpc:
    def __init__(self, url: str, timeout: float = 30, retries: int = 4, batch_size: int = 50):
        self.url, self.timeout, self.retries, self.batch_size = url, timeout, retries, batch_size
        self._id = 0
        self.batch_ok = True

    def _post(self, payload):
        req = urllib.request.Request(
            self.url,
            json.dumps(payload).encode(),
            {"content-type": "application/json", "user-agent": "ssv-eb-reconcile/1.0"},
        )
        delay = 1.0
        for attempt in range(self.retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.load(resp)
            except urllib.error.HTTPError as exc:
                # Several providers answer JSON-RPC errors (range too large, rate limits) with HTTP 4xx.
                try:
                    body = json.loads(exc.read())
                except (ValueError, OSError):
                    body = None
                if isinstance(body, (dict, list)) and exc.code != 429:
                    return body
                if attempt == self.retries or exc.code in (401, 403, 404):
                    raise RpcError(f"transport error: HTTP {exc.code}", transport=True) from None
                time.sleep(delay)
                delay = min(delay * 2, 20)
            except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as exc:
                status = getattr(exc, "code", None)
                if attempt == self.retries or status in (400, 401, 403, 404):
                    raise RpcError(f"transport error: {type(exc).__name__} {status or ''}".strip(), transport=True) from None
                time.sleep(delay)
                delay = min(delay * 2, 20)

    def call(self, method: str, params: list):
        self._id += 1
        out = self._post({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params})
        if not isinstance(out, dict):
            raise RpcError(f"{method}: malformed response")
        if out.get("error"):
            raise RpcError(f"{method}: {out['error'].get('message', out['error'])}")
        if "result" not in out:
            raise RpcError(f"{method}: no result")
        return out["result"]

    def batch(self, calls: list[tuple[str, list]]) -> list:
        results: list = []
        for i in range(0, len(calls), self.batch_size):
            chunk = calls[i : i + self.batch_size]
            if self.batch_ok and len(chunk) > 1:
                payload = [
                    {"jsonrpc": "2.0", "id": j, "method": m, "params": p} for j, (m, p) in enumerate(chunk)
                ]
                out = self._post(payload)
                if isinstance(out, list) and len(out) == len(chunk):
                    by_id = {o.get("id"): o for o in out}
                    if all(j in by_id and "result" in by_id[j] for j in range(len(chunk))):
                        results.extend(by_id[j]["result"] for j in range(len(chunk)))
                        continue
                    errs = [by_id.get(j, {}).get("error") for j in range(len(chunk)) if "result" not in by_id.get(j, {})]
                    log.debug("batch had errors (%s); retrying sequentially", errs[:1])
                else:
                    log.info("RPC batch unsupported or rejected; falling back to sequential calls")
                    self.batch_ok = False
            results.extend(self.call(m, p) for m, p in chunk)
        return results

    # -- helpers

    def block_number(self) -> int:
        return int(self.call("eth_blockNumber", []), 16)

    def block_hash(self, number: int) -> str:
        blk = self.call("eth_getBlockByNumber", [hex(number), False])
        if not blk:
            raise RpcError(f"block {number} not found")
        return blk["hash"].lower()

    def get_logs(self, address: str, start: int, end: int, max_range: int) -> list[dict]:
        """Bounded windows; a window the endpoint rejects is split in halves.

        Transport failures are raised, never split, and an error is never read as "no logs".
        """
        out: list[dict] = []
        lo = start
        while lo <= end:
            hi = min(end, lo + max_range - 1)
            out.extend(self._logs_range(address, lo, hi))
            lo = hi + 1
        return out

    def _logs_range(self, address: str, lo: int, hi: int) -> list[dict]:
        try:
            logs = self.call(
                "eth_getLogs",
                [{"address": address, "fromBlock": hex(lo), "toBlock": hex(hi), "topics": [TRACKED_TOPICS]}],
            )
        except RpcError as exc:
            if exc.transport or hi == lo:
                raise
            log.debug("getLogs %d-%d rejected (%s); splitting", lo, hi, exc)
            mid = (lo + hi) // 2
            return self._logs_range(address, lo, mid) + self._logs_range(address, mid + 1, hi)
        if not isinstance(logs, list):
            raise RpcError("eth_getLogs returned a non-list result")
        return logs

    def storage_many(self, address: str, slots: list[int], block: int) -> list[int]:
        res = self.batch([("eth_getStorageAt", [address, hex(s), hex(block)]) for s in slots])
        return [int(r, 16) for r in res]


# --------------------------------------------------------------------------- database

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS logs (
  block INTEGER NOT NULL, log_index INTEGER NOT NULL, tx_index INTEGER NOT NULL,
  tx_hash TEXT NOT NULL, block_hash TEXT NOT NULL, kind TEXT NOT NULL,
  cluster_id TEXT, owner TEXT, operator_ids TEXT, cluster TEXT, pubkey TEXT, shares_len INTEGER,
  ref_block INTEGER, eb INTEGER, root TEXT, extra TEXT,
  PRIMARY KEY (block, log_index)
);
CREATE INDEX IF NOT EXISTS logs_cluster ON logs (cluster_id, block, log_index);
CREATE INDEX IF NOT EXISTS logs_kind ON logs (kind, block);
CREATE TABLE IF NOT EXISTS blocks (number INTEGER PRIMARY KEY, hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS evaluations (
  cluster_id TEXT PRIMARY KEY, block INTEGER NOT NULL, status TEXT NOT NULL, ref_block INTEGER,
  reference_vunits INTEGER, observed_vunits INTEGER, billed_vunits INTEGER, discrepancy_vunits INTEGER,
  matched_keys INTEGER, active INTEGER, validator_count INTEGER, reasons TEXT
);
CREATE TABLE IF NOT EXISTS incidents (
  id TEXT PRIMARY KEY, cluster_id TEXT NOT NULL, kind TEXT NOT NULL, severity TEXT NOT NULL,
  state TEXT NOT NULL, first_ref_block INTEGER NOT NULL, last_ref_block INTEGER NOT NULL,
  first_seen_block INTEGER NOT NULL, last_seen_block INTEGER NOT NULL,
  shortfall_vunits INTEGER NOT NULL, max_shortfall_vunits INTEGER NOT NULL,
  resolved_block INTEGER, evidence TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS incidents_cluster ON incidents (cluster_id, state);
CREATE TABLE IF NOT EXISTS outbox (
  key TEXT PRIMARY KEY, created_at REAL NOT NULL, severity TEXT NOT NULL, text TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at REAL NOT NULL DEFAULT 0,
  sent_at REAL, delivery TEXT, last_error TEXT
);
"""


class DB:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, isolation_level=None, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.executescript(SCHEMA)

    def tx(self):
        return _Tx(self.conn)

    def meta(self, key: str, default=None):
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key: str, value) -> None:
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    def insert_event(self, ev: Event) -> None:
        c = ev.cluster
        self.conn.execute(
            "INSERT OR REPLACE INTO logs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                ev.block, ev.log_index, ev.tx_index, ev.tx_hash, ev.block_hash, ev.kind,
                ev.cluster_id.hex() if ev.cluster_id else None, ev.owner,
                json.dumps(list(ev.operator_ids)) if ev.cluster_id else None,
                json.dumps([c.validator_count, c.network_fee_index, c.index, c.active, str(c.balance)]) if c else None,
                ev.pubkey, ev.shares_len, ev.ref_block, ev.eb, ev.root,
                json.dumps(ev.extra, default=str) if ev.extra else None,
            ),
        )

    @staticmethod
    def row_event(r: sqlite3.Row) -> Event:
        c = json.loads(r["cluster"]) if r["cluster"] else None
        return Event(
            kind=r["kind"], block=r["block"], log_index=r["log_index"], tx_index=r["tx_index"],
            tx_hash=r["tx_hash"], block_hash=r["block_hash"],
            cluster_id=bytes.fromhex(r["cluster_id"]) if r["cluster_id"] else None, owner=r["owner"],
            operator_ids=tuple(json.loads(r["operator_ids"])) if r["operator_ids"] else (),
            cluster=Cluster(c[0], c[1], c[2], bool(c[3]), int(c[4])) if c else None,
            pubkey=r["pubkey"], shares_len=r["shares_len"], ref_block=r["ref_block"], eb=r["eb"], root=r["root"],
            extra=json.loads(r["extra"]) if r["extra"] else {},
        )

    def cluster_events(self, cid: str, upto: int) -> list[Event]:
        rows = self.conn.execute(
            "SELECT * FROM logs WHERE cluster_id=? AND block<=? ORDER BY block, log_index", (cid, upto)
        ).fetchall()
        return [self.row_event(r) for r in rows]

    def clusters(self, explicit_only: bool) -> list[str]:
        if explicit_only:
            q = "SELECT DISTINCT cluster_id FROM logs WHERE kind='ClusterBalanceUpdated'"
        else:
            q = "SELECT DISTINCT cluster_id FROM logs WHERE cluster_id IS NOT NULL"
        return [r[0] for r in self.conn.execute(q)]


class _Tx:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        self.conn.execute("BEGIN IMMEDIATE")
        return self

    def __exit__(self, exc_type, exc, tb):
        self.conn.execute("ROLLBACK" if exc_type else "COMMIT")
        return False


# --------------------------------------------------------------------------- worker


class Worker:
    def __init__(self, cfg: Config, rpc: Rpc, db: DB, notifier=None, clock=time.time):
        self.cfg, self.rpc, self.db, self.clock = cfg, rpc, db, clock
        self.notifier = notifier
        self.last_sweep = 0.0
        self.last_full_sweep = 0.0
        self.rpc_failures = 0
        self.layout_ok: bool | None = None
        self.dirty: set[str] = set()

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

    def checkpoint(self) -> dict:
        return self.db.meta("checkpoint")

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
            self.health(
                "reorg",
                f"Chain reorganisation below the confirmation horizon: checkpoint {cp['block']} rewound to "
                f"{ancestor[0]}, {n} logs discarded and replayed. Incidents are re-evaluated on the new chain.",
                dedup=f"reorg:{cp['block']}:{ancestor[0]}",
            )
        self.dirty.update(self.db.clusters(explicit_only=False))

    def scan(self) -> int:
        """Advance the checkpoint to the target block; returns the number of new logs."""
        target = self.target_block()
        cp = self.checkpoint()
        total = 0
        start = cp["block"] + 1
        step = self.cfg.max_log_range
        while start <= target:
            end = min(target, start + step - 1)
            logs = self.rpc.get_logs(self.cfg.proxy, start, end, step)
            end_hash = self.rpc.block_hash(end)
            events = []
            for lg in logs:
                if lg.get("removed"):
                    continue
                if lg["address"].lower() != self.cfg.proxy:
                    raise RpcError("eth_getLogs returned a log for another address")
                ev = decode_log(lg)
                if ev is not None:
                    events.append(ev)
            with self.db.tx():
                for ev in events:
                    self.db.insert_event(ev)
                    if ev.cluster_id:
                        self.dirty.add(ev.cluster_id.hex())
                for bn, bh in {(ev.block, ev.block_hash) for ev in events} | {(end, end_hash)}:
                    self.db.conn.execute("INSERT OR REPLACE INTO blocks VALUES (?, ?)", (bn, bh))
                self.db.set_meta("checkpoint", {"block": end, "hash": end_hash})
                self.db.set_meta("last_scan_at", self.clock())
            total += len(events)
            if events:
                log.info("scanned %d-%d: %d events", start, end, len(events))
            start = end + 1
        return total

    # ---- layout guard

    def verify_layout(self, block: int, deep: bool = False) -> list[str]:
        problems = []
        layout = self.cfg.raw.get("layout") or {}
        expected_impl = layout.get("implementation")
        modules = layout.get("modules", {})
        slots = [IMPLEMENTATION_SLOT] + [module_slot(m) for m in modules]
        words = self.rpc.storage_many(self.cfg.proxy, slots, block)
        impl = "0x%040x" % words[0]
        if expected_impl and impl != expected_impl.lower():
            problems.append(f"implementation {impl} != pinned {expected_impl.lower()}")
        for (name, spec), word in zip(modules.items(), words[1:]):
            addr = "0x%040x" % word
            if addr != spec["address"].lower():
                problems.append(f"{name} module {addr} != pinned {spec['address'].lower()}")
            elif deep and spec.get("codehash"):
                code = self.rpc.call("eth_getCode", [addr, hex(block)])
                h = "0x" + keccak(bytes.fromhex(code[2:])).hex()
                if h != spec["codehash"].lower():
                    problems.append(f"{name} codehash {h} != pinned {spec['codehash'].lower()}")
        if deep and self.cfg.raw.get("views"):
            problems.extend(self._cross_check_views(block))
        return problems

    def _cross_check_views(self, block: int, sample: int = 5) -> list[str]:
        """Compare the raw-slot billing value with SSVNetworkViews.getEffectiveBalance for a few clusters."""
        problems = []
        checked = 0
        for cid in self.db.clusters(explicit_only=True):
            events = self.db.cluster_events(cid, block)
            ev = self._evaluate_one(cid, events, block)
            if ev.status not in (M.CLEAN, M.LATENT, M.CONFIRMED, M.OVERBILLED) or not ev.active:
                continue
            latest = events[-1]
            ops = latest.operator_ids
            data = (
                GET_EB_SELECTOR
                + bytes.fromhex(latest.owner[2:]).rjust(32, b"\0")
                + u256(7 * 32)
                + b"".join(u256(int(x)) for x in latest.cluster.as_tuple())
                + u256(len(ops))
                + b"".join(u256(o) for o in ops)
            )
            try:
                res = self.rpc.call("eth_call", [{"to": self.cfg.views, "data": "0x" + data.hex()}, hex(block)])
            except RpcError as exc:
                problems.append(f"getEffectiveBalance call failed: {exc}")
                break
            got = int(res, 16)
            want = vunits_to_eb(ev.billed_vunits)
            if got != want:
                problems.append(f"cluster {cid[:10]}: views EB {got} != raw-slot EB {want}")
            checked += 1
            if checked >= sample:
                break
        if checked:
            log.info("views cross-check: %d clusters agree with raw storage", checked - len(problems))
        return problems

    # ---- evaluation

    def _evaluate_one(self, cid: str, events: list[Event], block: int, storage: M.StorageView | None = None):
        if storage is None:
            cb = bytes.fromhex(cid)
            w = self.rpc.storage_many(
                self.cfg.proxy, [cluster_eb_slot(cb), eth_cluster_slot(cb), ssv_cluster_slot(cb)], block
            )
            storage = M.StorageView(block, w[0], u256(w[1]), u256(w[2]))
        return M.evaluate(events, storage, self.cfg.start_block, self.cfg.history_complete,
                          self.cfg.guards_from)

    def evaluate(self, cids: list[str], block: int) -> list[M.Evaluation]:
        cids = sorted(set(cids))
        slots = []
        for cid in cids:
            cb = bytes.fromhex(cid)
            slots += [cluster_eb_slot(cb), eth_cluster_slot(cb), ssv_cluster_slot(cb)]
        words = self.rpc.storage_many(self.cfg.proxy, slots, block) if slots else []
        out = []
        for i, cid in enumerate(cids):
            w = words[3 * i : 3 * i + 3]
            storage = M.StorageView(block, w[0], u256(w[1]), u256(w[2]))
            events = self.db.cluster_events(cid, block)
            if events:
                out.append(M.evaluate(events, storage, self.cfg.start_block, self.cfg.history_complete,
                          self.cfg.guards_from))
        return out

    def sweep(self, full: bool = False, force: bool = False) -> list[M.Evaluation] | None:
        now = self.clock()
        cp = self.checkpoint()
        block = cp["block"]
        if block < self.cfg.start_block:
            return None
        periodic = force or now - self.last_sweep >= self.cfg.sweep_interval_s
        full = full or force or now - self.last_full_sweep >= self.cfg.full_sweep_interval_s
        if not (periodic or full or self.dirty):
            return None

        problems = self.verify_layout(block, deep=self.layout_ok is None)
        if problems:
            self.layout_ok = False
            self.health("layout", "Storage layout guard failed; reconciliation suspended:\n- " + "\n- ".join(problems),
                        dedup="layout:" + "|".join(problems))
            return None
        if self.layout_ok is False:
            self.health("layout-ok", "Storage layout guard passes again; reconciliation resumed.",
                        dedup=f"layout-ok:{block}")
        self.layout_ok = True

        if full:
            cids = self.db.clusters(explicit_only=False)
        elif periodic:
            cids = self.db.clusters(explicit_only=True) + list(self.dirty)
        else:
            cids = list(self.dirty)
        evals = self.evaluate(cids, block)
        self.dirty.clear()
        self.last_sweep = now
        if full:
            self.last_full_sweep = now
        self.record(evals, block, full)
        return evals

    # ---- incidents and alerts

    def record(self, evals: list[M.Evaluation], block: int, full: bool) -> None:
        with self.db.tx():
            for ev in evals:
                self._store_evaluation(ev)
                self._incident(ev, block)
            counts = Counter(e.status for e in evals)
            unknown = [e for e in evals if e.status == M.UNKNOWN]
            summary = {"block": block, "at": self.clock(), "full": full, "evaluated": len(evals),
                       "counts": dict(counts)}
            self.db.set_meta("last_sweep", summary)
            if full:
                self.digest(evals, block)
            if unknown:
                sample = "\n".join(f"- {e.cluster_id.hex()[:10]}…: {e.reasons[0]}" for e in unknown[:5])
                self.health(
                    "unknown",
                    f"{len(unknown)} cluster(s) could not be reconciled at block {block}; they are NOT "
                    f"treated as clean.\n{sample}",
                    dedup="unknown:" + ",".join(sorted(e.cluster_id.hex()[:10] for e in unknown)),
                    realert=True,
                )
        log.info("evaluated %d clusters at %d: %s", len(evals), block, dict(Counter(e.status for e in evals)))

    def _store_evaluation(self, ev: M.Evaluation) -> None:
        self.db.conn.execute(
            "INSERT OR REPLACE INTO evaluations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (ev.cluster_id.hex(), ev.block, ev.status, ev.ref_block, ev.reference_vunits, ev.observed_vunits,
             ev.billed_vunits, ev.discrepancy_vunits, len(ev.matched_keys), int(bool(ev.active)),
             ev.validator_count, json.dumps(ev.reasons)),
        )

    def _incident(self, ev: M.Evaluation, block: int) -> None:
        cid = ev.cluster_id.hex()
        now = self.clock()
        row = self.db.conn.execute(
            "SELECT * FROM incidents WHERE cluster_id=? AND state='open' ORDER BY created_at DESC LIMIT 1", (cid,)
        ).fetchone()

        if ev.status == M.CONFIRMED:
            severity = "critical" if ev.active else "warning"
            evidence = json.dumps(self._evidence(ev))
            if row is None or row["kind"] != "confirmed":
                if row is not None:  # latent/candidate escalates into the same incident id
                    inc_id = row["id"]
                    self.db.conn.execute(
                        "UPDATE incidents SET kind='confirmed', severity=?, last_ref_block=?, last_seen_block=?, "
                        "shortfall_vunits=?, max_shortfall_vunits=MAX(max_shortfall_vunits, ?), evidence=?, "
                        "updated_at=? WHERE id=?",
                        (severity, ev.ref_block, block, ev.shortfall_vunits, ev.shortfall_vunits, evidence, now, inc_id),
                    )
                else:
                    inc_id = f"EB94775-{cid[:10]}-{ev.ref_block}"
                    self._open(inc_id, cid, "confirmed", severity, ev, block, evidence)
                self.notify(f"{inc_id}:confirmed:{ev.ref_block}", severity, self._msg_confirmed(inc_id, ev, block))
                return
            inc_id = row["id"]
            repeated = ev.ref_block != row["last_ref_block"]
            grew = ev.shortfall_vunits > row["max_shortfall_vunits"]
            self.db.conn.execute(
                "UPDATE incidents SET severity=?, last_ref_block=?, last_seen_block=?, shortfall_vunits=?, "
                "max_shortfall_vunits=MAX(max_shortfall_vunits, ?), evidence=?, updated_at=? WHERE id=?",
                (severity, ev.ref_block, block, ev.shortfall_vunits, ev.shortfall_vunits, evidence, now, inc_id),
            )
            escalated = severity == "critical" and row["severity"] != "critical"
            if repeated or grew or escalated:
                why = (f"repeated at root S={ev.ref_block}" if repeated else "shortfall grew" if grew
                       else "cluster reactivated; the collapsed EB is billed again")
                self.notify(f"{inc_id}:update:{ev.ref_block}:{ev.shortfall_vunits}:{severity}", severity,
                            self._msg_confirmed(inc_id, ev, block, update=why))
            return

        if ev.status == M.UNKNOWN and ev.matched_keys:
            # Fast-path candidate: the add -> consumed root -> remove sequence exists but the
            # accounting check could not run. Mark pending; monitor-health covers the cause.
            if row is None:
                inc_id = f"EB94775-{cid[:10]}-{ev.ref_block}"
                self._open(inc_id, cid, "candidate", "warning", ev, block, json.dumps(self._evidence(ev)))
                self.notify(f"{inc_id}:candidate:{ev.ref_block}", "warning", self._msg_candidate(inc_id, ev, block))
            return

        if ev.status == M.LATENT:
            if row is None:
                inc_id = f"EB94775-{cid[:10]}-{ev.ref_block}"
                self._open(inc_id, cid, "latent", "info", ev, block, json.dumps(self._evidence(ev)))
            else:
                self.db.conn.execute(
                    "UPDATE incidents SET last_ref_block=?, last_seen_block=?, shortfall_vunits=?, "
                    "max_shortfall_vunits=MAX(max_shortfall_vunits, ?), updated_at=? WHERE id=?",
                    (ev.ref_block, block, ev.shortfall_vunits, ev.shortfall_vunits, now, row["id"]),
                )
            return

        if ev.status == M.UNKNOWN or row is None:
            return  # keep any open incident open while inputs are incomplete

        # Discrepancy closed (clean / overbilled / implicit / reset / not ETH): resolve.
        self.db.conn.execute(
            "UPDATE incidents SET state='resolved', resolved_block=?, last_seen_block=?, shortfall_vunits=0, "
            "updated_at=? WHERE id=?",
            (block, block, now, row["id"]),
        )
        if row["kind"] in ("confirmed", "candidate"):
            self.notify(f"{row['id']}:resolved:{block}", "info", self._msg_resolved(row, ev, block))

    def _open(self, inc_id, cid, kind, severity, ev, block, evidence) -> None:
        now = self.clock()
        self.db.conn.execute(
            "INSERT OR IGNORE INTO incidents VALUES (?,?,?,?, 'open', ?,?,?,?,?,?, NULL, ?, ?, ?)",
            (inc_id, cid, kind, severity, ev.ref_block or 0, ev.ref_block or 0, block, block,
             ev.shortfall_vunits, ev.shortfall_vunits, evidence, now, now),
        )

    def _evidence(self, ev: M.Evaluation) -> dict:
        u = ev.update
        return {
            "cluster_id": "0x" + ev.cluster_id.hex(), "owner": ev.owner, "operator_ids": list(ev.operator_ids),
            "block": ev.block, "status": ev.status, "active": ev.active, "validator_count": ev.validator_count,
            "ref_block": ev.ref_block, "eb_at_ref": ev.eb_at_ref,
            "update": {"tx": u.tx_hash, "block": u.block, "log_index": u.log_index} if u else None,
            "reference_vunits": ev.reference_vunits, "observed_vunits": ev.observed_vunits,
            "billed_vunits": ev.billed_vunits, "discrepancy_vunits": ev.discrepancy_vunits,
            "matched_keys": len(ev.matched_keys),
            "add_txs": sorted({e.tx_hash for e in ev.adds_in_window}),
            "remove_txs": sorted({e.tx_hash for e in ev.removes_after_update}),
            "empty_shares_adds": sum(1 for e in ev.adds_in_window if e.shares_len == 0),
            "reasons": ev.reasons,
        }

    # ---- messages (plain text; Telegram parse_mode is not used, so no escaping)

    def _tx(self, h: str) -> str:
        return f"{self.cfg.alerts.get('explorer', 'https://etherscan.io')}/tx/{h}"

    def _head(self, sev: str, title: str) -> str:
        icon = {"critical": "🚨", "warning": "⚠️", "info": "ℹ️", "health": "🩺"}.get(sev, "")
        return f"{icon} [{sev.upper()}] {title} — {self.cfg.alerts.get('label', 'chain ' + str(self.cfg.chain_id))}"

    def _cluster_lines(self, ev: M.Evaluation) -> list[str]:
        return [
            f"Cluster: 0x{ev.cluster_id.hex()}",
            f"Owner: {ev.owner}",
            f"Operators: {list(ev.operator_ids)}",
            f"Validators now: {ev.validator_count} · active: {ev.active} · version: {ev.version}",
        ]

    def _sender(self, tx_hash: str) -> str:
        try:
            tx = self.rpc.call("eth_getTransactionByHash", [tx_hash])
            return tx["from"] if tx else "?"
        except RpcError:
            return "?"

    def _msg_confirmed(self, inc_id, ev: M.Evaluation, block: int, update: str | None = None) -> str:
        u = ev.update
        lines = [self._head("critical" if ev.active else "warning",
                            ("#94775 incident update: " + update) if update else "#94775 EB collapse confirmed")]
        lines += self._cluster_lines(ev)
        lines += [
            f"Root reference block S: {ev.ref_block} · consumed in block {u.block} with E = {ev.eb_at_ref} ETH",
            f"Update sent by: {self._sender(u.tx_hash)} (clue only; updates are permissionless)",
            f"Keys added after S and before the update: {len({e.pubkey for e in ev.adds_in_window})} "
            f"({sum(1 for e in ev.adds_in_window if e.shares_len == 0)} with empty shares)",
            f"Of those, removed after the update: {len(ev.matched_keys)}",
            f"Reference-adjusted: {ev.reference_vunits:,} vUnits ({vunits_to_eth_str(ev.reference_vunits)} ETH)",
            f"Stored (billed): {ev.observed_vunits:,} vUnits ({vunits_to_eth_str(ev.billed_vunits)} ETH)",
            f"Shortfall: {ev.shortfall_vunits:,} vUnits ({vunits_to_eth_str(ev.shortfall_vunits)} ETH of fee weight)",
            f"Evaluated at finalized block {block}",
        ]
        if not ev.active:
            lines.append("Cluster is liquidated: not billed now; the shortfall applies again on reactivation.")
        lines.append("Txs:")
        lines += [f"  register {self._tx(h)}" for h in sorted({e.tx_hash for e in ev.adds_in_window})[:3]]
        lines.append(f"  update   {self._tx(u.tx_hash)}")
        lines += [f"  remove   {self._tx(h)}" for h in sorted({e.tx_hash for e in ev.removes_after_update})[:3]]
        lines.append(f"Incident: {inc_id}")
        return "\n".join(lines)

    def _msg_candidate(self, inc_id, ev: M.Evaluation, block: int) -> str:
        lines = [self._head("warning", "#94775 candidate sequence (accounting check pending)")]
        lines += self._cluster_lines(ev)
        lines += [
            f"Root reference block S: {ev.ref_block}; update tx {self._tx(ev.update.tx_hash) if ev.update else '?'}",
            f"Keys added after S, removed after the update: {len(ev.matched_keys)}",
            f"Accounting result: UNKNOWN at block {block} ({'; '.join(ev.reasons[:2])})",
            f"Incident: {inc_id}",
        ]
        return "\n".join(lines)

    def _msg_resolved(self, row, ev: M.Evaluation, block: int) -> str:
        return "\n".join([
            self._head("info", "#94775 incident resolved"),
            f"Incident: {row['id']}",
            f"Cluster: 0x{row['cluster_id']}",
            f"Discrepancy closed at block {block}: status {ev.status}, last consumed root S={ev.ref_block}",
            f"Max shortfall seen: {row['max_shortfall_vunits']:,} vUnits "
            f"({vunits_to_eth_str(row['max_shortfall_vunits'])} ETH) from block {row['first_seen_block']} to {block}",
        ])

    def health(self, kind: str, text: str, dedup: str, realert: bool = False) -> None:
        """Monitor-health alerts are distinct from exploit alerts. Must run inside a db transaction."""
        key = f"health:{kind}:{keccak(dedup.encode()).hex()[:16]}"
        if realert:
            window = int(self.clock() // self.cfg.alerts.get("health_realert_s", 21600))
            key += f":{window}"
        self.notify(key, "health", self._head("health", f"EB reconciler: {kind}") + "\n" + text)

    def notify(self, key: str, severity: str, text: str) -> None:
        self.db.conn.execute(
            "INSERT OR IGNORE INTO outbox(key, created_at, severity, text) VALUES (?,?,?,?)",
            (key, self.clock(), severity, text),
        )

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
                delivery = self.notifier.send(r["severity"], r["text"], r["key"])
                self.db.conn.execute("UPDATE outbox SET sent_at=?, delivery=? WHERE key=?",
                                     (self.clock(), delivery, r["key"]))
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
                self.health("lag", f"Checkpoint {cp} is {lag} blocks behind the confirmed head {target}.",
                            dedup="lag", realert=True)

    def digest(self, evals: list[M.Evaluation], block: int) -> None:
        """Daily info digest of non-paging states (latent candidates, overbilling, implicit resets)."""
        interval = self.cfg.alerts.get("digest_interval_s", 86400)
        if not interval or self.clock() - (self.db.meta("last_digest") or 0) < interval:
            return
        counts = Counter(e.status for e in evals)
        latent = sorted((e for e in evals if e.status == M.LATENT), key=lambda e: -e.shortfall_vunits)
        lines = [self._head("info", "EB reconciler daily digest"),
                 f"Block {block}: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))]
        if latent:
            lines.append("Largest latent overwritten baselines (added keys still registered; not a confirmed #94775):")
            lines += [f"- 0x{e.cluster_id.hex()[:10]}… S={e.ref_block} {e.shortfall_vunits:,} vUnits "
                      f"({vunits_to_eth_str(e.shortfall_vunits)} ETH)" for e in latent[:5]]
        self.notify(f"digest:{block}", "info", "\n".join(lines))
        self.db.set_meta("last_digest", self.clock())

    def write_status(self, healthy: bool, error: str | None = None) -> None:
        path = Path(self.cfg.db_path).with_suffix(".status.json")
        status = {
            "healthy": healthy, "at": self.clock(), "error": error, "checkpoint": self.checkpoint(),
            "lag_blocks": self.db.meta("lag_blocks"), "last_sweep": self.db.meta("last_sweep"),
            "open_incidents": self.db.conn.execute(
                "SELECT kind, COUNT(*) FROM incidents WHERE state='open' GROUP BY kind").fetchall(),
            "undelivered": self.db.conn.execute("SELECT COUNT(*) FROM outbox WHERE sent_at IS NULL").fetchone()[0],
        }
        status["open_incidents"] = {k: n for k, n in status["open_incidents"]}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(status, indent=1))
        tmp.replace(path)

    def step(self) -> None:
        try:
            self.check_reorg()
            self.scan()
            self.sweep()
            self.rpc_failures = 0
            self.write_status(True)
        except RpcError as exc:
            self.rpc_failures += 1
            log.warning("RPC failure %d: %s", self.rpc_failures, exc)
            if self.rpc_failures in (3, 20, 100):
                with self.db.tx():
                    self.health("rpc", f"{self.rpc_failures} consecutive RPC failures; last: {exc}. "
                                f"Checkpoint {self.checkpoint()['block']}.", dedup=f"rpc:{self.rpc_failures}",
                                realert=True)
            self.write_status(False, str(exc))
        self.lag_check()
        self.deliver()


# --------------------------------------------------------------------------- notifiers


class RecordOnly:
    """Logs alerts as JSON lines; nothing leaves the host."""

    def send(self, severity: str, text: str, key: str) -> str:
        print(json.dumps({"alert": key, "severity": severity, "text": text}), flush=True)
        return "recorded"


class Telegram:
    def __init__(self, token: str, chat_id: str, timeout: float = 15):
        self.token, self.chat_id, self.timeout = _clean_secret(token), _clean_secret(chat_id), timeout

    def send(self, severity: str, text: str, key: str) -> str:
        body = json.dumps({"chat_id": self.chat_id, "text": text[:4000], "disable_web_page_preview": True}).encode()
        try:
            req = urllib.request.Request(f"https://api.telegram.org/bot{self.token}/sendMessage", body,
                                         {"content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                out = json.load(resp)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"telegram HTTP {exc.code}") from None
        except Exception as exc:  # noqa: BLE001 - messages may embed the URL, which holds the token
            raise RuntimeError(f"telegram delivery error: {type(exc).__name__}") from None
        if not out.get("ok"):
            raise RuntimeError(f"telegram rejected: {str(out.get('description', '?')).replace(self.token, '<token>')}")
        return f"telegram:{out['result']['message_id']}"


def _clean_secret(value: str) -> str:
    return value.strip().strip("\"'").strip()


def make_notifier(cfg: Config):
    mode = cfg.alerts.get("mode", "record")
    if mode == "record":
        return RecordOnly()
    if mode == "telegram":
        token = _clean_secret(os.environ.get(cfg.alerts["telegram_token_env"], ""))
        chat = _clean_secret(os.environ.get(cfg.alerts["telegram_chat_id_env"], ""))
        if not token or not chat:
            raise SystemExit("telegram mode needs the token and chat id environment variables set")
        return Telegram(token, chat)
    raise SystemExit(f"unknown alerts.mode {mode!r}")


# --------------------------------------------------------------------------- audit


def audit(db: DB, start_block: int) -> dict:
    """Event-only history: every consumed root window with post-S additions, and matched removals."""
    windows = []
    for cid in db.clusters(explicit_only=True):
        events = db.cluster_events(cid, 1 << 62)
        updates = [e for e in events if e.kind == "ClusterBalanceUpdated"]
        for i, u in enumerate(updates):
            nxt = updates[i + 1].pos if i + 1 < len(updates) else (1 << 62, 0)
            adds = [e for e in events if e.kind == "ValidatorAdded" and e.block > u.ref_block and e.pos < u.pos]
            rems_before = [e for e in events if e.kind == "ValidatorRemoved" and e.block > u.ref_block and e.pos < u.pos]
            if not adds:
                continue
            added = {e.pubkey for e in adds}
            matched = [e for e in events if e.kind == "ValidatorRemoved" and u.pos < e.pos < nxt and e.pubkey in added]
            windows.append({
                "cluster_id": "0x" + cid, "owner": u.owner, "operator_ids": list(u.operator_ids),
                "ref_block": u.ref_block, "update_block": u.block, "update_tx": u.tx_hash, "eb": u.eb,
                "adds_between_s_and_update": len(adds), "removes_between_s_and_update": len(rems_before),
                "overwritten_baseline_vunits": 10_000 * (len(adds) - len(rems_before)),
                "empty_shares_adds": sum(1 for e in adds if e.shares_len == 0),
                "matched_removals_before_next_update": len({e.pubkey for e in matched}),
                "remove_txs": sorted({e.tx_hash for e in matched})[:5],
                "superseded": i + 1 < len(updates),
            })
    windows.sort(key=lambda w: (-w["matched_removals_before_next_update"], -w["overwritten_baseline_vunits"]))
    kinds = Counter(r[0] for r in db.conn.execute("SELECT kind FROM logs"))
    return {
        "checkpoint": db.meta("checkpoint"), "history_start": start_block, "event_counts": dict(kinds),
        "clusters_seen": len(db.clusters(explicit_only=False)),
        "clusters_with_eb_update": len(db.clusters(explicit_only=True)),
        "windows_with_post_s_adds": len(windows),
        "windows_with_matched_removal": sum(1 for w in windows if w["matched_removals_before_next_update"]),
        "windows": windows,
    }


# --------------------------------------------------------------------------- cli


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["run", "once", "audit", "status", "verify-layout"])
    ap.add_argument("--config", default=os.environ.get("EB_RECONCILE_CONFIG", "config.json"))
    ap.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "info"))
    args = ap.parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        stream=sys.stderr)

    cfg = load_config(args.config)
    db = DB(cfg.db_path)

    if args.command == "status":
        out = {
            "identity": db.meta("identity"), "checkpoint": db.meta("checkpoint"), "last_sweep": db.meta("last_sweep"),
            "open_incidents": [dict(r) for r in db.conn.execute(
                "SELECT id, kind, severity, last_ref_block, shortfall_vunits, max_shortfall_vunits, first_seen_block, "
                "last_seen_block FROM incidents WHERE state='open' ORDER BY created_at")],
            "undelivered": db.conn.execute("SELECT COUNT(*) FROM outbox WHERE sent_at IS NULL").fetchone()[0],
        }
        print(json.dumps(out, indent=1))
        return 0
    if args.command == "audit":
        print(json.dumps(audit(db, cfg.start_block), indent=1))
        return 0

    lock = open(cfg.db_path + ".lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log.error("another worker holds %s.lock", cfg.db_path)
        return 2

    rpc = Rpc(cfg.rpc_url, cfg.rpc_timeout_s, cfg.rpc_retries, cfg.rpc_batch_size)
    worker = Worker(cfg, rpc, db, make_notifier(cfg))
    worker.bootstrap()

    if args.command == "verify-layout":
        block = worker.target_block()
        problems = worker.verify_layout(block, deep=True)
        print(json.dumps({"block": block, "ok": not problems, "problems": problems}, indent=1))
        return 0 if not problems else 1

    if args.command == "once":
        worker.check_reorg()
        worker.scan()
        evals = worker.sweep(force=True)
        worker.deliver()
        worker.write_status(evals is not None)
        print(json.dumps(db.meta("last_sweep"), indent=1))
        return 0 if evals is not None else 1

    stop = {"flag": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(flag=True))
    signal.signal(signal.SIGINT, lambda *_: stop.update(flag=True))
    log.info("starting: proxy %s from block %d (checkpoint %d), alerts=%s", cfg.proxy, cfg.start_block,
             worker.checkpoint()["block"], cfg.alerts.get("mode", "record"))
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
