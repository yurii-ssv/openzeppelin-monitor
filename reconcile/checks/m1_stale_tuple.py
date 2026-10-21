"""M1 — Stale final cluster event (#91419 family): a cluster whose last log carries a tuple storage rejects.

A re-entrant callback (deposit, validator add/remove) during an ETH transfer can emit a newer tuple while the
outer function emits its cached, older one last. Last-log-wins readers (oracle updater, liquidator, subgraph,
web app) then hold a stale tuple and their calls revert with `IncorrectClusterState`.

Detection (spec M1, plan §8 CP2d):
  receipts   per block and cluster, the cluster's final event; when its transaction carries >= 2 events of the
             cluster, `SSVNetworkViews.isLiquidated(owner, operatorIds, lastTuple)` at that block. A revert with
             IncorrectClusterState is a poisoning; the earliest log of the same transaction whose tuple validates
             is the repair tuple. A group superseded by a later transaction of the same block is not what readers
             keep and is not validated (the later transaction passed storage's tuple, so its own tuple decides).
  state      every run validates, at the run's block, the last stored log of every cluster M1 tracks (open
             incident, unrepaired poisoning, unresolved revert, reorg key); every `interval_s` (and in reorg, force
             and params runs) the shared inventory flags clusters whose last log does not validate or for which no
             candidate validates (`unknown`), and those are validated the same way.
  inbox      `IncorrectClusterState` reverts of `inbox_functions` sent by `our_senders` -> the cluster's incident.
Severity: warn; page while the cluster is re-poisoned (a poisoning after a recorded repair) or poisoned and
liquidatable (`isLiquidatable`/`isLiquidatableSSV` of the canonical tuple at the run's block). A repair (the last
log validates again) resolves the incident; a revert alone resolves at the first later run whose validation of
the last log succeeds. An RPC error or an unexpected revert raises (never "clean").

Reorg rule (checks/README.md §6.2) for `m1_detections`: rows from blocks above the ancestor are deleted (the
event cursor is rewound, so the reorg run re-detects them from the new chain's logs), except inbox-derived
reverts, which follow their inbox row (`canonical = 0` drops them, in any run); `cleared_block` above the
ancestor is reset. The reorg run then evaluates every open incident key, every persisted key and every
`ctx.reorg_keys` entry at the new checkpoint; a key that needs the inventory when it is unavailable raises.
"""

from __future__ import annotations

import json
import re

from checks.base import Check, Ctx, Every, Finding, OnEvents, OnInbox
from checks.m1_cluster_abi import (
    ERR_INCORRECT_CLUSTER_STATE,
    ERR_INCORRECT_CLUSTER_VERSION,
    FUNCTIONS,
    IS_LIQUIDATABLE,
    IS_LIQUIDATABLE_SSV,
    IS_LIQUIDATED,
    decode_bool,
    decode_cluster_calldata,
    encode_cluster_call,
    revert_selector,
)
from checks.registry import register
from db import DB
from eb_events import CLUSTER_KINDS, Event
from eb_storage import Cluster, cluster_id
from rpc import RpcError

SCHEMA = """
CREATE TABLE IF NOT EXISTS m1_detections (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cluster_id TEXT NOT NULL, kind TEXT NOT NULL, block INTEGER NOT NULL, log_index INTEGER NOT NULL,
  tx_hash TEXT, block_hash TEXT, owner TEXT, operator_ids TEXT, stale_tuple TEXT, repair_tuple TEXT,
  repair_log_index INTEGER, source TEXT NOT NULL, detail TEXT, ingested_block INTEGER NOT NULL, cleared_block INTEGER
);
CREATE INDEX IF NOT EXISTS m1_detections_cluster ON m1_detections (cluster_id, kind, block, log_index)
"""

# kinds: poison (a last log that does not validate), repair (the last log validates again after a poisoning),
# revert (an inbox IncorrectClusterState revert of one of our senders). source: receipt | state | inbox.

SWEEP_REASONS = frozenset({"interval", "reorg", "force", "params"})
ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")


def _tup(cl: Cluster) -> list[str]:
    return [str(cl.validator_count), str(cl.network_fee_index), str(cl.index), str(cl.active).lower(), str(cl.balance)]


def _cl(raw: str | None) -> Cluster | None:
    if not raw:
        return None
    c = json.loads(raw)
    return Cluster(int(c[0]), int(c[1]), int(c[2]), c[3] in (True, "true"), int(c[4]))


def _short(key: str) -> str:
    return key[:10] + "…" + key[-4:] if len(key) > 16 else key


@register
class M1StaleTuple(Check):
    id = "m1"
    defaults = {
        "interval_s": 600,
        "receipt_window_blocks": 1000,  # older groups (a backlog, a first start on full history): state sweep only
        "repair_candidates_max": 64,  # distinct earlier tuples validated to find the repair tuple
        "our_senders": [],  # spec §5 (oracle updater, liquidator); required, see config.example.json
        "inbox_functions": ["updateClusterBalance", "liquidate", "liquidateSSV"],
    }
    schema = SCHEMA

    def __init__(self, config: dict, settings: dict):
        super().__init__(config, settings)
        views = settings.get("views") or ""
        if not ADDRESS.match(views):
            raise SystemExit("checks.m1: the top-level `views` (SSVNetworkViews address) is required; M1 validates "
                             "tuples with isLiquidated")
        senders = config["our_senders"]
        if not senders or not all(isinstance(a, str) and ADDRESS.match(a) for a in senders):
            raise SystemExit("checks.m1.our_senders: a non-empty list of addresses is required (spec §5: oracle "
                             "updater, liquidator)")
        unknown = set(config["inbox_functions"]) - set(FUNCTIONS)
        if unknown:
            raise SystemExit(f"checks.m1.inbox_functions: unknown {sorted(unknown)}; known: {sorted(FUNCTIONS)}")
        for k in ("interval_s", "receipt_window_blocks", "repair_candidates_max"):
            if not isinstance(config[k], int) or config[k] < (0 if k == "receipt_window_blocks" else 1):
                raise SystemExit(f"checks.m1.{k}: a positive integer is required")
        self.views = views.lower()
        self.senders = frozenset(a.lower() for a in senders)
        self.functions = frozenset(config["inbox_functions"])
        self.explorer = settings.get("alerts", {}).get("explorer", "https://etherscan.io")
        self.schedule = (OnEvents(CLUSTER_KINDS), OnInbox(), Every(config["interval_s"]))
        self.last_sweep: dict = {}

    # ---------------------------------------------------------------- run

    def run(self, ctx: Ctx, block: int) -> list[Finding]:
        db = ctx.db
        reorg = ctx.reorg_ancestor is not None
        if reorg:
            self._rewind(db, ctx.reorg_ancestor)
        dropped = self._drop_orphaned_reverts(db)
        keys, skipped = self._scan(ctx, [ev for ev in ctx.events() if ev.cluster_id is not None], block)
        keys |= self._ingest_inbox(ctx, block)
        inv = None
        if skipped or ctx.reasons & SWEEP_REASONS:
            inv = self._inventory(ctx, block)
            if inv is not None:
                flagged = {cid for cid, i in inv.clusters.items()
                           if i.version and (not i.valid or (i.tuple_source == "api" and "logs" in i.sources))}
                keys |= flagged
                self.last_sweep = {"block": block, "clusters": len(inv.clusters), "flagged": len(flagged)}
            else:  # no inventory: validate the clusters of the groups the receipt window skipped one by one
                keys |= skipped
        keys |= self._persisted_keys(db) | self._open_keys(db) | ctx.reorg_keys | dropped
        out = []
        for key in sorted(keys):
            f = self._evaluate(ctx, key, block, inv, reorg, key in dropped)
            if f is not None:
                out.append(f)
        return out

    # ---------------------------------------------------------------- reorg

    def _rewind(self, db, ancestor: int) -> None:
        db.conn.execute("DELETE FROM m1_detections WHERE block > ? AND kind != 'revert'", (ancestor,))
        db.conn.execute("UPDATE m1_detections SET cleared_block = NULL WHERE cleared_block > ?", (ancestor,))

    def _drop_orphaned_reverts(self, db) -> set[str]:
        """Inbox-derived rows follow their inbox entry: an entry of an orphaned block (canonical = 0) is dropped."""
        rows = db.conn.execute(
            "SELECT DISTINCT d.cluster_id FROM m1_detections d JOIN inbox i ON i.tx_hash = d.tx_hash "
            "AND i.block_hash = d.block_hash WHERE d.kind = 'revert' AND i.canonical = 0").fetchall()
        if rows:
            db.conn.execute(
                "DELETE FROM m1_detections WHERE kind = 'revert' AND EXISTS (SELECT 1 FROM inbox i WHERE "
                "i.tx_hash = m1_detections.tx_hash AND i.block_hash = m1_detections.block_hash AND i.canonical = 0)")
        return {r[0] for r in rows}

    # ---------------------------------------------------------------- receipts

    def _scan(self, ctx: Ctx, events: list[Event], block: int) -> tuple[set[str], set[str]]:
        keys, skipped = set(), set()
        by_block: dict[int, list[Event]] = {}
        for ev in events:
            by_block.setdefault(ev.block, []).append(ev)
        horizon = block - self.config["receipt_window_blocks"]
        for b in sorted(by_block):
            evs = sorted(by_block[b], key=lambda e: e.log_index)
            finals: dict[bytes, Event] = {}
            for ev in evs:
                finals[ev.cluster_id] = ev
            for cid, final in sorted(finals.items(), key=lambda kv: kv[1].log_index):
                key = "0x" + cid.hex()
                group = [e for e in evs if e.cluster_id == cid and e.tx_hash == final.tx_hash]
                active = self._active_poison(ctx.db, key)
                if len(group) < 2 and active is None:
                    continue  # one event: the function validated its input against storage and emitted its result
                if b < horizon:
                    skipped.add(key)
                    continue
                if self._validate(ctx, final.owner, final.operator_ids, final.cluster, b):
                    if active is not None:
                        self._insert(ctx.db, key, "repair", final, block, source="receipt",
                                     detail={"events": len(group)})
                        keys.add(key)
                    continue
                repair = self._repair_event(ctx, group[:-1], final, b)
                self._insert(ctx.db, key, "poison", final, block, source="receipt", repair=repair,
                             detail={"events": len(group), "shape": [e.kind for e in group][-6:],
                                     "candidates": len({e.cluster for e in group[:-1]})})
                keys.add(key)
        return keys, skipped

    def _validate(self, ctx: Ctx, owner: str, operator_ids, cl: Cluster, block: int) -> bool:
        """True when storage holds this tuple at `block`; False on IncorrectClusterState; anything else raises."""
        try:
            out = ctx.rpc.call(self.views, encode_cluster_call(IS_LIQUIDATED, owner, operator_ids, cl), block)
        except RpcError as exc:
            if not exc.transport and revert_selector(exc) == ERR_INCORRECT_CLUSTER_STATE:
                return False
            raise
        decode_bool(out, "isLiquidated")
        return True

    def _repair_event(self, ctx: Ctx, earlier: list[Event], final: Event, block: int) -> Event | None:
        """The earliest log of the transaction whose tuple validates at `block` (validating tuples are equal: storage
        holds one hash), searching distinct tuples from the latest, bounded by `repair_candidates_max`."""
        distinct: list[Cluster] = []
        for e in reversed(earlier):
            if e.cluster != final.cluster and e.cluster not in distinct:
                distinct.append(e.cluster)
        for cl in distinct[: self.config["repair_candidates_max"]]:
            if self._validate(ctx, final.owner, final.operator_ids, cl, block):
                return next(e for e in earlier if e.cluster == cl)
        return None

    # ---------------------------------------------------------------- inbox

    def _ingest_inbox(self, ctx: Ctx, block: int) -> set[str]:
        keys = set()
        for e in ctx.inbox:
            if (e.sender or "").lower() not in self.senders:
                continue
            if e.revert_selector is None and (e.function or "").split("(", 1)[0] in self.functions:
                # unresolved revert of one of our senders: it may be IncorrectClusterState, never read as clean
                ctx.health("m1-inbox-unresolved", f"M1: the revert of {e.function} {e.tx_hash} (block {e.block}) from "
                           f"our sender {e.sender} could not be resolved ({e.revert_source}); check it by hand.",
                           dedup=f"m1-inbox-unresolved:{e.tx_hash}")
                continue
            if e.revert_selector != ERR_INCORRECT_CLUSTER_STATE:
                continue
            tx = ctx.rpc.transaction(e.tx_hash)
            if not tx:
                raise RpcError(f"inbox {e.tx_hash}: eth_getTransactionByHash returned nothing")
            dec = decode_cluster_calldata(tx)
            fn = dec[0] if dec else (e.function or "").split("(", 1)[0]
            if fn not in self.functions:
                continue
            key = "0x" + cluster_id(dec[1], dec[2]).hex() if dec else f"tx:{e.tx_hash}"
            seen = ctx.db.conn.execute("SELECT 1 FROM m1_detections WHERE kind='revert' AND tx_hash=? AND "
                                       "block_hash=?", (e.tx_hash, e.block_hash)).fetchone()
            if seen:
                continue
            ctx.db.conn.execute(
                "INSERT INTO m1_detections (cluster_id, kind, block, log_index, tx_hash, block_hash, owner, operator_ids,"
                " source, detail, ingested_block) VALUES (?, 'revert', ?, -1, ?, ?, ?, ?, 'inbox', ?, ?)",
                (key, e.block, e.tx_hash, e.block_hash, dec[1] if dec else None,
                 json.dumps(list(dec[2])) if dec else None,
                 json.dumps({"function": fn, "sender": (e.sender or "").lower(), "revert_source": e.revert_source}),
                 block))
            keys.add(key)
        return keys

    # ---------------------------------------------------------------- state

    def _inventory(self, ctx: Ctx, block: int):
        try:
            return ctx.inventory(block)
        except RpcError:
            raise
        except Exception as exc:  # noqa: BLE001 - the SSV API is off-chain input; receipts still get validated
            ctx.health("m1-inventory", f"M1: the cluster inventory could not be built at block {block} "
                       f"({type(exc).__name__}: {exc}). The full-state sweep is skipped; receipts and the clusters M1 "
                       f"tracks are still validated.", dedup=f"m1-inventory:{type(exc).__name__}", realert=True)
            return None

    def _persisted_keys(self, db) -> set[str]:
        rows = db.conn.execute(
            "SELECT cluster_id FROM m1_detections WHERE kind = 'revert' AND cleared_block IS NULL UNION "
            "SELECT p.cluster_id FROM m1_detections p WHERE p.kind = 'poison' AND NOT EXISTS (SELECT 1 FROM "
            "m1_detections r WHERE r.cluster_id = p.cluster_id AND r.kind = 'repair' AND (r.block > p.block OR "
            "(r.block = p.block AND r.log_index > p.log_index)))").fetchall()
        return {r[0] for r in rows}

    def _open_keys(self, db) -> set[str]:
        return {r[0] for r in db.conn.execute("SELECT key FROM incidents WHERE check_id = ? AND state = 'open'",
                                              (self.id,))}

    def _open_incident(self, db, key: str):
        return db.conn.execute("SELECT * FROM incidents WHERE check_id = ? AND key = ? AND state = 'open'",
                               (self.id, key)).fetchone()

    def _active_poison(self, db, key: str):
        row = db.conn.execute(
            "SELECT * FROM m1_detections WHERE cluster_id = ? AND kind IN ('poison', 'repair') "
            "ORDER BY block DESC, log_index DESC, id DESC LIMIT 1", (key,)).fetchone()
        return row if row is not None and row["kind"] == "poison" else None

    def _repaired_before(self, db, poison) -> bool:
        return db.conn.execute(
            "SELECT 1 FROM m1_detections WHERE cluster_id = ? AND kind = 'repair' AND (block < ? OR (block = ? AND "
            "log_index < ?))", (poison["cluster_id"], poison["block"], poison["block"], poison["log_index"])
        ).fetchone() is not None

    def _active_reverts(self, db, key: str) -> list:
        return db.conn.execute("SELECT * FROM m1_detections WHERE cluster_id = ? AND kind = 'revert' AND "
                               "cleared_block IS NULL ORDER BY block, id", (key,)).fetchall()

    def _latest_log(self, db, key: str, block: int) -> Event | None:
        r = db.conn.execute("SELECT * FROM logs WHERE cluster_id = ? AND block <= ? AND cluster IS NOT NULL "
                            "ORDER BY block DESC, log_index DESC LIMIT 1", (key[2:], block)).fetchone()
        return DB.row_event(r) if r else None

    def _insert(self, db, key: str, kind: str, ev: Event, block: int, source: str, repair: Event | None = None,
                repair_tuple: Cluster | None = None, detail: dict | None = None) -> None:
        rt = repair.cluster if repair is not None else repair_tuple
        db.conn.execute(
            "INSERT INTO m1_detections (cluster_id, kind, block, log_index, tx_hash, block_hash, owner, operator_ids, "
            "stale_tuple, repair_tuple, repair_log_index, source, detail, ingested_block) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (key, kind, ev.block, ev.log_index, ev.tx_hash, ev.block_hash, ev.owner, json.dumps(list(ev.operator_ids)),
             json.dumps(_tup(ev.cluster)) if kind == "poison" else None, json.dumps(_tup(rt)) if rt else None,
             repair.log_index if repair is not None else None, source, json.dumps({**(detail or {}), "kind": ev.kind}),
             block))

    def _liquidatable(self, ctx: Ctx, owner: str, operator_ids, cl: Cluster | None, block: int) -> bool | None:
        """isLiquidatable (ETH) or isLiquidatableSSV (legacy) of the canonical tuple; None when it is unknown or no
        longer validates."""
        if cl is None:
            return None
        for sel in (IS_LIQUIDATABLE, IS_LIQUIDATABLE_SSV):
            try:
                out = ctx.rpc.call(self.views, encode_cluster_call(sel, owner, operator_ids, cl), block)
            except RpcError as exc:
                if exc.transport:
                    raise
                if revert_selector(exc) == ERR_INCORRECT_CLUSTER_VERSION:
                    continue
                if revert_selector(exc) == ERR_INCORRECT_CLUSTER_STATE:
                    return None
                raise
            return decode_bool(out, "isLiquidatable")
        return None

    # ---------------------------------------------------------------- evaluation of one key

    def _evaluate(self, ctx: Ctx, key: str, block: int, inv, reorg: bool, dropped: bool) -> Finding | None:
        db = ctx.db
        open_row = self._open_incident(db, key)
        if key.startswith("tx:"):
            return self._evaluate_tx_key(db, key, block, open_row)
        latest = self._latest_log(db, key, block)
        info = inv.clusters.get(key) if inv is not None else None
        stale: bool | None = None
        unknown = None
        if latest is not None:
            stale = not self._validate(ctx, latest.owner, latest.operator_ids, latest.cluster, block)
        elif info is not None:
            stale = False
            if info.version and not info.valid:
                unknown = "no stored log of this cluster, and the SSV API tuple does not validate against storage"
        elif inv is not None:
            stale = False  # no stored log left (e.g. every log was on reorganised blocks) and not listed by the API
        elif reorg:
            raise RuntimeError(f"M1 cannot re-evaluate {key} after the reorg: no stored log and no inventory")
        reverts = self._active_reverts(db, key)

        poison = self._active_poison(db, key)
        api_tuple = info.cluster if info is not None and info.valid and info.tuple_source == "api" else None
        if latest is not None and stale and (poison is None or (latest.block, latest.log_index) >
                                             (poison["block"], poison["log_index"])):
            self._insert(db, key, "poison", latest, block, source="state", repair_tuple=api_tuple)
            poison = self._active_poison(db, key)
        elif latest is not None and not stale and poison is not None:
            self._insert(db, key, "repair", latest, block, source="state")
            poison = None
        if poison is not None and poison["repair_tuple"] is None and api_tuple is not None:
            db.conn.execute("UPDATE m1_detections SET repair_tuple = ? WHERE id = ?",
                            (json.dumps(_tup(api_tuple)), poison["id"]))
            poison = self._active_poison(db, key)
        if stale is False:
            db.conn.execute("UPDATE m1_detections SET cleared_block = ? WHERE cluster_id = ? AND kind = 'revert' AND "
                            "cleared_block IS NULL AND ingested_block < ?", (block, key, block))
            reverts = self._active_reverts(db, key)

        subject = {"cluster_id": key}
        if latest is not None:
            subject.update(owner=latest.owner, operator_ids=list(latest.operator_ids))
        elif info is not None:
            subject.update(owner=info.owner, operator_ids=list(info.operator_ids))
        if stale is None and not reverts:
            # Not evaluable now (no stored log, inventory not read this run): keep an open incident as it is.
            if open_row is None:
                return None
            return Finding(check=self.id, key=key, severity=open_row["severity"], title=open_row["title"],
                           body=open_row["body"], subject=subject, evidence=json.loads(open_row["evidence"]),
                           notify=bool(open_row["notify"]), revision=open_row["revision"])
        if not stale and unknown is None and not reverts:
            if open_row is None:
                return None
            if latest is not None:
                state = f"the last-log tuple from tx {latest.tx_hash} (block {latest.block}) validates against storage"
            elif info is not None:
                state = "it has no stored log and the SSV API tuple validates against storage"
            else:
                state = "no log of this cluster remains on the canonical chain and the inventory does not list it"
            body = (f"Cluster {key}: at block {block} {state}; no unresolved IncorrectClusterState revert of our "
                    f"senders remains.")
            if dropped:
                body += " An inbox revert of this cluster was in a block that left the canonical chain."
            return Finding(check=self.id, key=key, severity="info", resolve=True,
                           title=f"Cluster tuple validates again (M1): {_short(key)}", body=body, subject=subject,
                           evidence={"block": block, "latest_tx": latest.tx_hash if latest else None})
        return self._finding(ctx, key, block, subject, latest, poison, reverts, unknown, bool(stale))

    def _evaluate_tx_key(self, db, key: str, block: int, open_row) -> Finding | None:
        """A revert whose calldata names no cluster: a one-shot warn, resolved at the next run."""
        rows = self._active_reverts(db, key)
        if rows and all(r["ingested_block"] < block for r in rows):
            db.conn.execute("UPDATE m1_detections SET cleared_block = ? WHERE cluster_id = ? AND kind = 'revert' "
                            "AND cleared_block IS NULL", (block, key))
            rows = []
        if not rows:
            if open_row is None:
                return None
            return Finding(check=self.id, key=key, severity="info", resolve=True,
                           title=f"IncorrectClusterState revert reported (M1): {key[3:13]}…",
                           body=f"Transaction {key[3:]}: reported once; its calldata names no cluster to track.")
        r = rows[-1]
        d = json.loads(r["detail"])
        return Finding(check=self.id, key=key, severity="warn",
                       title=f"IncorrectClusterState revert from our sender (M1): {d['function']}",
                       body=(f"Transaction {r['tx_hash']} (block {r['block']}) from {d['sender']} reverted with "
                             f"IncorrectClusterState; the cluster could not be decoded from its calldata.\n"
                             f"{self.explorer}/tx/{r['tx_hash']}"),
                       subject={"tx_hash": r["tx_hash"]}, evidence={"block": r["block"], **d},
                       revision=f"revert:{r['tx_hash']}")

    def _finding(self, ctx: Ctx, key, block, subject, latest, poison, reverts, unknown, stale) -> Finding:
        db = ctx.db
        lines, evidence, title = [f"Cluster {key}"], {"block": block}, None
        if subject.get("owner"):
            lines.append(f"Owner {subject['owner']}, operators {subject.get('operator_ids')}")
        severity, revision = "warn", None
        newest = None
        if stale:
            repaired_before = self._repaired_before(db, poison)
            canonical = _cl(poison["repair_tuple"])
            liquidatable = self._liquidatable(ctx, latest.owner, latest.operator_ids, canonical, block)
            if repaired_before or liquidatable:
                severity = "page"
            title = ("Cluster re-poisoned after a repair (M1 #91419)" if repaired_before else
                     "Poisoned cluster is liquidatable (M1 #91419)" if liquidatable else
                     "Stale cluster tuple (M1 #91419)") + f": {_short(key)}"
            lines += [
                f"Last log does not validate at block {block}: {poison['tx_hash']} (block {poison['block']}, log "
                f"{poison['log_index']}, {json.loads(poison['detail']).get('kind')}), stale tuple "
                f"{json.loads(poison['stale_tuple'])}.",
                (f"Repair tuple {json.loads(poison['repair_tuple'])}" + (
                    f" (log {poison['repair_log_index']} of the same transaction)"
                    if poison["repair_log_index"] is not None else f" (from {poison['source']}/SSV API)"))
                if canonical else "Repair tuple: unknown (no earlier tuple of the receipt validates).",
                f"Liquidatable with the canonical tuple: {'unknown' if liquidatable is None else liquidatable}.",
                "Runbook: send a zero-value deposit(owner, operatorIds, repairTuple); anyone can, it re-emits the "
                "canonical tuple. Repair again and escalate if the owner re-poisons.",
                f"{self.explorer}/tx/{poison['tx_hash']}",
            ]
            evidence.update(poison_tx=poison["tx_hash"], poison_block=poison["block"],
                            poison_log_index=poison["log_index"], stale_tuple=json.loads(poison["stale_tuple"]),
                            repair_tuple=json.loads(poison["repair_tuple"]) if canonical else None,
                            repair_log_index=poison["repair_log_index"], source=poison["source"],
                            liquidatable=liquidatable, re_poisoned=repaired_before)
            newest = (poison["block"], poison["log_index"], f"poison:{poison['tx_hash']}:{poison['log_index']}")
        elif unknown:
            title = f"Cluster tuple unknown (M1): {_short(key)}"
            lines.append(f"Status unknown at block {block}: {unknown}.")
            evidence["unknown"] = unknown
            revision = "unknown"
        if reverts:
            for r in reverts:
                d = json.loads(r["detail"])
                lines.append(f"IncorrectClusterState revert of {d['function']} from {d['sender']}: {r['tx_hash']} "
                             f"(block {r['block']}).")
            evidence["reverts"] = [r["tx_hash"] for r in reverts]
            last = reverts[-1]
            if newest is None or (last["block"], 1 << 30) > newest[:2]:
                newest = (last["block"], 1 << 30, f"revert:{last['tx_hash']}")
            title = title or f"IncorrectClusterState revert from our sender (M1): {_short(key)}"
        return Finding(check=self.id, key=key, severity=severity, title=title, body="\n".join(lines),
                       subject=subject, evidence=evidence, revision=newest[2] if newest else revision)

    # ---------------------------------------------------------------- digest

    def digest(self, ctx: Ctx) -> list[str]:
        n = len(self._persisted_keys(ctx.db))
        out = [f"M1: {n} cluster(s) with an unrepaired stale tuple or an unresolved revert"]
        if self.last_sweep:
            out.append(f"M1: last full-state sweep at block {self.last_sweep['block']}: "
                       f"{self.last_sweep['clusters']} clusters, {self.last_sweep['flagged']} flagged")
        return out
