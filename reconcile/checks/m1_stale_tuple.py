"""M1 — Stale final cluster event (#91419 family): a cluster whose last log carries a tuple storage rejects.

A re-entrant callback (deposit, validator add/remove) during an ETH transfer can emit a newer tuple while the
outer function emits its cached, older one last. Last-log-wins readers (oracle updater, liquidator, subgraph,
web app) then hold a stale tuple and their calls revert with `IncorrectClusterState`.

Detection (spec M1, plan §8 CP2d):
  receipts   per block and cluster, the transactions touching the cluster in log order. The block's last one: when
             it carries >= 2 events of the cluster (or the cluster has an unrepaired poisoning),
             `SSVNetworkViews.isLiquidated(owner, operatorIds, lastTuple)` at that block. A revert with
             IncorrectClusterState is a poisoning; the earliest log of the same transaction whose tuple equals the
             stored cluster word (hashed locally against one storage read, every candidate, then confirmed with
             isLiquidated) is the repair tuple. An earlier transaction of the block cannot be validated at block
             granularity (a later one changed storage, no trace RPC): one whose events all carry the same tuple
             wrote and emitted that tuple (`clean`: a repair of an unrepaired poisoning, recorded at its position);
             one with differing tuples is `unverified` (a poisoning or a repair). Every earlier transaction is kept
             in log order in the poisoning's evidence (`intra_block`). Every block's receipts are validated after
             the first run (a downtime backlog included); `receipt_window_blocks` only bounds the archive calls of
             a first start on history (older groups: state sweep).
  state      every run validates, at the run's block, the last stored log of every cluster M1 tracks (open
             incident, unrepaired poisoning, unresolved revert, reorg key); every `interval_s` (and in reorg, force
             and params runs) the shared inventory flags clusters whose last log does not validate or for which no
             candidate validates (`unknown`), and those are validated the same way. An SSV API answer with no
             cluster is health (`m1-inventory-empty`), never a clean full-state sweep.
  inbox      `IncorrectClusterState` reverts of `inbox_functions` sent by `our_senders` -> the cluster's incident.
Severity: warn; page while the cluster is re-poisoned (a poisoning after a recorded repair), poisoned and
liquidatable (`isLiquidatable`/`isLiquidatableSSV` of the canonical tuple at the run's block), or possibly
re-poisoned within one block: coordinator ruling on QA attempt 2 F2 (spec M1 re-poison read louder when
ambiguous), a final poisoning pages as an `unverified intra-block re-poison (no trace)` when some reading of the
block's unverified transactions gives poison -> repair -> poison (an unverified transaction after an unrepaired
poisoning or a warn/page incident, or after an earlier unverified one; or one followed by another transaction
before the last). The page holds for the unrepaired episode; a later block-end validation (repair) resolves it.
False-positive cost: a benign helper transaction with differing tuples (two deposits) followed in the same block
by a poisoning of an already flagged cluster pages instead of warning. A repair (the last log validates again)
resolves the incident; a revert alone resolves at the first later run whose validation of the last log succeeds;
a poisoning repaired before the run that processed it is still reported once (warn, or page when re-poisoned) and
resolved at the next run. An RPC error or an unexpected revert raises (never "clean"). Off-chain input never proves
recovery: a key with no stored log that the inventory does not list (an empty or partial SSV API answer) is clean
only when storage holds no such cluster; otherwise its incident is kept (health), and a reorg run raises.

Reorg rule (checks/README.md §6.2) for `m1_detections`: rows from blocks above the ancestor are deleted (the
event cursor is rewound, so the reorg run re-detects them from the new chain's logs; a poisoning an earlier alert
already named is not re-sent), and so are state-sweep rows ingested above it (judged against storage of a deleted
block), except inbox-derived reverts, which follow their inbox row (`canonical = 0` drops them, in any run);
`cleared_block` above the ancestor is reset. The reorg run then evaluates every open incident key, every persisted
key, every `ctx.reorg_keys` entry and every key resolved above the ancestor (its resolution may be orphaned) at the
new checkpoint; a key it cannot evaluate (no stored log, the cluster in storage, no inventory tuple) raises.
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
from eb_storage import Cluster, cluster_id, eth_cluster_slot, ssv_cluster_slot
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
UNVERIFIED = "unverified intra-block re-poison (no trace)"
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
        "receipt_window_blocks": 1000,  # groups older than this before the first run (a start on history): state sweep
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
        for k in ("interval_s", "receipt_window_blocks"):
            if not isinstance(config[k], int) or config[k] < (0 if k == "receipt_window_blocks" else 1):
                raise SystemExit(f"checks.m1.{k}: a positive integer is required")
        self.views = views.lower()
        self.proxy = settings["proxy"].lower()
        self.use_api = bool((settings.get("inventory") or {}).get("api_url", "default"))  # as eb_reconcile reads it
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
                keys |= flagged | (skipped - set(inv.clusters))
                api_n = sum("api" in i.sources for i in inv.clusters.values())
                degraded = self.use_api and api_n == 0
                if degraded:  # an empty off-chain answer is UNKNOWN, never a clean full-state sweep
                    ctx.health("m1-inventory-empty", f"M1: the SSV API returned no cluster at block {block}; the "
                               f"state sweep covers only the {len(inv.clusters)} cluster(s) with stored logs, clusters "
                               f"known only to the API are unchecked (unknown) and nothing is resolved for lack of API "
                               f"rows.", dedup="m1-inventory-empty", realert=True)
                self.last_sweep = {"block": block, "clusters": len(inv.clusters), "flagged": len(flagged),
                                   "api_clusters": api_n, "degraded": degraded}
            else:  # no inventory: validate the clusters of the groups the receipt window skipped one by one
                keys |= skipped
        keys |= self._persisted_keys(db) | self._open_keys(db) | ctx.reorg_keys | dropped
        if reorg:  # README §6.2 (5): a resolution on a deleted block may be orphaned, so resolved keys are re-checked
            keys |= self._resolved_since(db, ctx.reorg_ancestor)
        out = []
        for key in sorted(keys):
            f = self._evaluate(ctx, key, block, inv, reorg, key in dropped)
            if f is not None:
                out.append(f)
        return out

    # ---------------------------------------------------------------- reorg

    def _rewind(self, db, ancestor: int) -> None:
        db.conn.execute("DELETE FROM m1_detections WHERE kind != 'revert' AND (block > ? OR (source = 'state' AND "
                        "ingested_block > ?))", (ancestor, ancestor))
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
        # The window bounds the archive calls of a first start on history only; after the first run every block's
        # receipts are validated (a downtime backlog included: a poisoning repaired inside it is still an event).
        first = ctx.state("first_run_block")
        if first is None:
            first = block
            ctx.set_state("first_run_block", block)
        horizon = first - self.config["receipt_window_blocks"]
        for b in sorted(by_block):
            evs = sorted(by_block[b], key=lambda e: e.log_index)
            txs: dict[bytes, dict[str, list[Event]]] = {}  # cluster -> transaction -> its events, both in log order
            for ev in evs:
                txs.setdefault(ev.cluster_id, {}).setdefault(ev.tx_hash, []).append(ev)
            for cid, groups in sorted(txs.items(), key=lambda kv: kv[1][next(reversed(kv[1]))][-1].log_index):
                key = "0x" + cid.hex()
                ordered = list(groups.values())
                active = self._active_poison(ctx.db, key)
                if len(ordered[-1]) < 2 and active is None and all(len(g) < 2 for g in ordered):
                    continue  # one event each: every function validated its input and emitted its result
                if b < horizon:
                    skipped.add(key)
                    continue
                self._block_of_cluster(ctx, key, ordered, active, b, block, keys)
        return keys, skipped

    def _block_of_cluster(self, ctx: Ctx, key: str, ordered: list[list[Event]], active, b: int, block: int,
                          keys: set[str]) -> None:
        """The transactions on one cluster in block `b`, in log order. Storage at `b` is the last one's, so only that
        one is validated by a call. An earlier transaction whose events all carry one tuple wrote and emitted it
        (`clean`, a repair of an unrepaired poisoning, recorded at its position). One with differing tuples cannot
        be validated without a trace (`unverified`: a poisoning or a repair). Coordinator ruling on QA attempt 2 F2
        (spec M1 re-poison, louder when ambiguous): a final poisoning pages as an unverified re-poison when some
        reading of the unverified transactions gives poison -> repair -> poison, i.e. an unverified transaction is
        a possible repair after a possible poisoning (an unrepaired poisoning or a warn/page incident before the
        block, or an earlier unverified one) or a possible poisoning followed by any transaction before the last."""
        db = ctx.db
        prior = self._open_incident(db, key)
        maybe_poisoned = active is not None or (prior is not None and prior["severity"] in ("warn", "page"))
        maybe_by_unverified = False  # a possible poisoning that is one of this block's unverified transactions
        unverified_re_poison = False
        earlier = []
        for group in ordered[:-1]:
            last = group[-1]
            tuples = list(dict.fromkeys(e.cluster for e in group))
            cls = "clean" if len(tuples) == 1 else "unverified"
            if maybe_by_unverified or (cls == "unverified" and maybe_poisoned):
                unverified_re_poison = True
            if cls == "clean" and active is not None:
                self._insert(db, key, "repair", last, block, source="receipt",
                             detail={"events": len(group), "intra_block": True})
                active = None
                keys.add(key)
            if cls == "unverified":
                maybe_poisoned = maybe_by_unverified = True
            earlier.append({"tx": last.tx_hash, "class": cls, "log_indexes": [e.log_index for e in group],
                            "tuples": [_tup(t) for t in tuples]})
        group = ordered[-1]
        final = group[-1]
        if len(group) < 2 and active is None:
            return  # one event: the function validated its input against storage and emitted its result
        if self._validate(ctx, final.owner, final.operator_ids, final.cluster, b):
            if active is not None:
                self._insert(db, key, "repair", final, block, source="receipt", detail={"events": len(group)})
                keys.add(key)
            return
        repair = self._repair_event(ctx, group[:-1], final, b)
        detail = {"events": len(group), "shape": [e.kind for e in group][-6:],
                  "candidates": len({e.cluster for e in group[:-1]})}
        if earlier:
            detail["intra_block"] = earlier
        if unverified_re_poison:
            detail["unverified_re_poison"] = True
        self._insert(db, key, "poison", final, block, source="receipt", repair=repair, detail=detail)
        keys.add(key)

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
        """The earliest log of the transaction whose tuple validates at `block`. Validating tuples are equal (storage
        holds one hash): every candidate is hashed against the stored cluster word (one storage read, no cap), the
        earliest match is confirmed with isLiquidated. Should Views disagree with the local hash, every distinct
        candidate is validated by call, earliest first."""
        cid = final.cluster_id
        eth_word, ssv_word = ctx.rpc.storage(self.proxy, [eth_cluster_slot(cid), ssv_cluster_slot(cid)], block)
        stored = (eth_word or ssv_word) if not (eth_word and ssv_word) else 0
        cands = [e for e in earlier if e.cluster != final.cluster]
        match = next((e for e in cands if stored and int.from_bytes(e.cluster.hash_data(), "big") == stored), None)
        if match is None:
            return None
        if self._validate(ctx, final.owner, final.operator_ids, match.cluster, block):
            return match
        tried: list[Cluster] = [match.cluster]
        for e in cands:
            if e.cluster not in tried:
                tried.append(e.cluster)
                if self._validate(ctx, final.owner, final.operator_ids, e.cluster, block):
                    return next(x for x in earlier if x.cluster == e.cluster)
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

    def _in_storage(self, ctx: Ctx, key: str, block: int) -> bool:
        """Whether storage holds a cluster word (ETH or legacy slot) for `key` at `block`."""
        cid = bytes.fromhex(key[2:])
        return any(ctx.rpc.storage(self.proxy, [eth_cluster_slot(cid), ssv_cluster_slot(cid)], block))

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

    def _resolved_since(self, db, ancestor: int) -> set[str]:
        return {r[0] for r in db.conn.execute("SELECT DISTINCT key FROM incidents WHERE check_id = ? AND state = "
                                              "'resolved' AND resolved_block > ?", (self.id, ancestor))}

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

    def _episode_unverified(self, db, poison) -> bool:
        """Whether a poisoning of the current unrepaired episode (since the last repair) paged as an unverified
        re-poison: the page stays until a repair, as a verified re-poison's does."""
        rows = db.conn.execute(
            "SELECT p.detail FROM m1_detections p WHERE p.cluster_id = ? AND p.kind = 'poison' AND NOT EXISTS (SELECT 1 "
            "FROM m1_detections r WHERE r.cluster_id = p.cluster_id AND r.kind = 'repair' AND (r.block > p.block OR "
            "(r.block = p.block AND r.log_index > p.log_index)))", (poison["cluster_id"],)).fetchall()
        return any(json.loads(r[0]).get("unverified_re_poison") for r in rows)

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
        absent = False
        if latest is not None:
            stale = not self._validate(ctx, latest.owner, latest.operator_ids, latest.cluster, block)
        elif info is not None:
            stale = False  # the inventory read both storage slots at `block`
            absent = info.version is None
            if info.version and not info.valid:
                unknown = "no stored log of this cluster, and the SSV API tuple does not validate against storage"
        elif not self._in_storage(ctx, key, block):
            stale, absent = False, True  # no stored log left (e.g. every log was on reorganised blocks), no cluster
        elif reorg:
            raise RuntimeError(f"M1 cannot re-evaluate {key} after the reorg: no stored log, the cluster is in "
                               f"storage, and the inventory {'gives no tuple for it' if inv else 'is unavailable'}")
        elif inv is not None:
            # An empty or partial SSV API answer is not evidence that the cluster recovered: keep what is open.
            ctx.health("m1-inventory-missing", f"M1: cluster {key} is in storage at block {block} but has no stored "
                       f"log and the inventory does not list it; its state is unknown and its incident is kept.",
                       dedup=f"m1-inventory-missing:{key}", realert=True)
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
        transient = self._transient(db, key, block) if latest is not None and not stale else None
        if not stale and unknown is None and not reverts and transient is None:
            if open_row is None:
                return None
            if latest is not None:
                state = f"the last-log tuple from tx {latest.tx_hash} (block {latest.block}) validates against storage"
            elif not absent:
                state = "it has no stored log and the SSV API tuple validates against storage"
            else:
                state = "no log of this cluster remains on the canonical chain and storage holds no such cluster"
            body = (f"Cluster {key}: at block {block} {state}; no unresolved IncorrectClusterState revert of our "
                    f"senders remains.")
            if dropped:
                body += " An inbox revert of this cluster was in a block that left the canonical chain."
            return Finding(check=self.id, key=key, severity="info", resolve=True,
                           title=f"Cluster tuple validates again (M1): {_short(key)}", body=body, subject=subject,
                           evidence={"block": block, "latest_tx": latest.tx_hash if latest else None})
        return self._finding(ctx, key, block, subject, latest, poison, reverts, unknown, bool(stale), transient)

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

    def _transient(self, db, key: str, block: int):
        """Poisonings detected in this run whose cluster validates again by the run's block (repaired in a later
        transaction or block of the same batch): still reported once (events between runs), resolved at the next
        run. One whose transaction an earlier alert of this cluster named (re-detected after a reorg) is not.
        Returns (rows, re_poisoned) or None."""
        rows = [r for r in db.conn.execute(
            "SELECT * FROM m1_detections WHERE cluster_id = ? AND kind = 'poison' AND ingested_block = ? "
            "ORDER BY block, log_index, id", (key, block)) if not self._reported(db, key, r["tx_hash"])]
        if not rows:
            return None
        return rows, any(self._repaired_before(db, r) or json.loads(r["detail"]).get("unverified_re_poison")
                         for r in rows)

    def _reported(self, db, key: str, tx_hash: str) -> bool:
        return db.conn.execute(
            "SELECT 1 FROM outbox o JOIN incidents i ON i.id = o.incident_id WHERE i.check_id = ? AND i.key = ? AND "
            "instr(o.text, ?) > 0 LIMIT 1", (self.id, key, tx_hash)).fetchone() is not None

    def _finding(self, ctx: Ctx, key, block, subject, latest, poison, reverts, unknown, stale,
                 transient=None) -> Finding:
        db = ctx.db
        lines, evidence, title = [f"Cluster {key}"], {"block": block}, None
        if subject.get("owner"):
            lines.append(f"Owner {subject['owner']}, operators {subject.get('operator_ids')}")
        severity, revision = "warn", None
        newest = None
        if stale:
            detail = json.loads(poison["detail"])
            repaired_before = self._repaired_before(db, poison)
            unverified = not repaired_before and self._episode_unverified(db, poison)
            canonical = _cl(poison["repair_tuple"])
            liquidatable = self._liquidatable(ctx, latest.owner, latest.operator_ids, canonical, block)
            if repaired_before or unverified or liquidatable:
                severity = "page"
            title = ("Cluster re-poisoned after a repair (M1 #91419)" if repaired_before else
                     f"Cluster possibly re-poisoned within one block (M1 #91419, {UNVERIFIED})" if unverified else
                     "Poisoned cluster is liquidatable (M1 #91419)" if liquidatable else
                     "Stale cluster tuple (M1 #91419)") + f": {_short(key)}"
            lines.append(
                f"Last log does not validate at block {block}: {poison['tx_hash']} (block {poison['block']}, log "
                f"{poison['log_index']}, {detail.get('kind')}), stale tuple {json.loads(poison['stale_tuple'])}.")
            if detail.get("intra_block"):
                lines.append(f"Earlier transactions on the cluster in block {poison['block']} (log order; storage at "
                             f"the block is the last one's, so an `unverified` one may have repaired or poisoned it):")
                lines += [f"  {t['tx']} logs {t['log_indexes']}: {t['class']}, tuples {t['tuples']}"
                          for t in detail["intra_block"]]
            if unverified:
                lines.append(f"Paged as an {UNVERIFIED}: a reading of the unverified transaction(s) gives poison -> "
                             f"repair -> poison; a later block whose last log validates resolves it.")
            lines += [
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
                            liquidatable=liquidatable, re_poisoned=repaired_before or unverified,
                            re_poison_unverified=unverified, intra_block=detail.get("intra_block", []))
            if unverified:
                evidence["note"] = UNVERIFIED
            newest = (poison["block"], poison["log_index"], f"poison:{poison['tx_hash']}:{poison['log_index']}")
        elif transient is not None:
            rows, re_poisoned = transient
            if re_poisoned:
                severity = "page"
            title = ("Cluster re-poisoned and repaired before this run (M1 #91419)" if re_poisoned else
                     "Cluster poisoned and repaired before this run (M1 #91419)") + f": {_short(key)}"
            lines += [f"Poisoned by {r['tx_hash']} (block {r['block']}, log {r['log_index']}): last log tuple "
                      f"{json.loads(r['stale_tuple'])} did not validate at its block." for r in rows]
            lines += [f"At block {block} the last-log tuple from tx {latest.tx_hash} (block {latest.block}) "
                      f"validates again; the poisoning was repaired before this run evaluated it. Resolved at the "
                      f"next run.", f"{self.explorer}/tx/{rows[-1]['tx_hash']}"]
            last = rows[-1]
            evidence.update(poison_tx=last["tx_hash"], poison_block=last["block"], poison_log_index=last["log_index"],
                            stale_tuple=json.loads(last["stale_tuple"]),
                            repair_tuple=json.loads(last["repair_tuple"]) if last["repair_tuple"] else None,
                            re_poisoned=re_poisoned, transient=True, poisonings=[r["tx_hash"] for r in rows])
            newest = (last["block"], last["log_index"], f"poison:{last['tx_hash']}:{last['log_index']}")
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
                       f"{self.last_sweep['clusters']} clusters, {self.last_sweep['flagged']} flagged"
                       + (" — DEGRADED: the SSV API returned no cluster" if self.last_sweep["degraded"] else ""))
        return out
