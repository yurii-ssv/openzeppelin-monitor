"""M2 — Operator capacity blocks legacy migration (#88326 / KI #6).

`validatorsPerOperatorLimit` is checked against an operator's ETH validator count only, so filling a public
operator's ETH capacity blocks `migrateClusterToETH` and `reactivate` of the legacy SSV clusters on it
(`ExceedValidatorLimitWithData(operatorId)`).

Every run (each `interval_s`, and on new inbox entries) evaluates every operator of a legacy cluster in the
inventory at the run's block, with `getOperatorById` (ETH count, isPrivate, isActive) and `getOperatorByIdSSV`
(legacy count) batched at that block and the limit from the live parameters. For an active operator with legacy
validators (spec M2):
  hard:<op>      an active legacy cluster on it cannot migrate now (eth + cluster.validatorCount > limit) -> warn
  early:<op>     ETH headroom (limit - eth) minus its legacy validators < `early_headroom` (900) -> info; warn if
                 the operator is public
  velocity:<op>  ValidatorAdded registrations to it in the last `velocity_window_blocks` (stored logs) exceed its
                 headroom -> info
  revert:<op>    a reverted `inbox_functions` call with ExceedValidatorLimitWithData(op), any sender -> warn; it
                 resolves at a later run once no legacy cluster on the operator (active or liquidated) is over
                 the limit; `revert:tx:<hash>` when the revert names no operator: reported once.
Each run reports every open key: a condition that no longer holds is resolved. An operator whose counts cannot be
read raises (RPC error).

Off-chain input never proves recovery. The legacy cluster list comes from the inventory (SSV API rows + stored
logs, tuples validated against storage) and from `m2_clusters`: every legacy cluster M2 has seen validated (and
the cluster of a reverted call, from its calldata), re-validated against storage at the run's block whenever the
inventory does not give it a validated tuple (an empty or partial API answer). A `hard:<op>` not found again is
resolved only on proof: the active legacy validators in no known cluster (`getOperatorByIdSSV` count minus the
known active clusters') cannot make one cluster blocked (eth + missing <= limit; an unknown cluster with known
clusters already above the on-chain count is not proven). Otherwise the open incident is kept (health), and in
the reorg run an unproven `ctx.reorg_keys` entry raises (marker kept, retried). A named `revert:<op>` settles on
the same proof, with no cluster on the operator lacking a validated tuple (liquidated clusters have no on-chain
count to check against), and not while the SSV API returned no cluster at all.

Reorg rule (checks/README.md §6.2): the conditions are recomputed from the chain and the new chain's stored logs at
every run, so the reorg run is an ordinary complete run; `m2_reverts` rows follow their inbox entry
(`canonical = 0` drops them, in any run), and a settlement (`cleared_block`) above the ancestor is undone, so a
surviving revert is evaluated again. `m2_clusters` rows are never trusted without a storage match at the run's
block, so they need no rewind.
"""

from __future__ import annotations

import json
import re

from checks.base import Check, Ctx, Every, Finding, OnInbox
from checks.m1_cluster_abi import (
    ERR_EXCEED_VALIDATOR_LIMIT,
    FUNCTION_BY_SELECTOR,
    FUNCTIONS,
    GET_OPERATOR_BY_ID,
    GET_OPERATOR_BY_ID_SSV,
    decode_cluster_calldata,
    decode_cluster_tuple,
    decode_operator,
    decode_uint_arg,
)
from checks.registry import register
from eb_storage import Cluster, cluster_id, eth_cluster_slot, keccak, ssv_cluster_slot, u256
from inventory import ClusterInfo
from rpc import RpcError

SCHEMA = """
CREATE TABLE IF NOT EXISTS m2_reverts (
  tx_hash TEXT NOT NULL, block_hash TEXT NOT NULL, block INTEGER NOT NULL, key TEXT NOT NULL, operator_id INTEGER,
  function TEXT, sender TEXT, ingested_block INTEGER NOT NULL, cleared_block INTEGER,
  PRIMARY KEY (tx_hash, block_hash)
);
CREATE TABLE IF NOT EXISTS m2_clusters (
  cluster_id TEXT PRIMARY KEY, owner TEXT NOT NULL, operator_ids TEXT NOT NULL, tuple TEXT NOT NULL,
  block INTEGER NOT NULL, source TEXT NOT NULL
)
"""

ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")


@register
class M2OperatorCapacity(Check):
    id = "m2"
    defaults = {
        "interval_s": 600,
        "early_headroom": 900,  # spec M2: the shock size used by the #88326 scanner
        "velocity_window_blocks": 7200,  # 24 h at 12 s
        "inbox_functions": ["migrateClusterToETH", "reactivate"],
    }
    schema = SCHEMA

    def __init__(self, config: dict, settings: dict):
        super().__init__(config, settings)
        views = settings.get("views") or ""
        if not ADDRESS.match(views):
            raise SystemExit("checks.m2: the top-level `views` (SSVNetworkViews address) is required; M2 reads "
                             "getOperatorById / getOperatorByIdSSV")
        for k in ("interval_s", "early_headroom", "velocity_window_blocks"):
            if not isinstance(config[k], int) or config[k] < 1:
                raise SystemExit(f"checks.m2.{k}: a positive integer is required")
        unknown = set(config["inbox_functions"]) - set(FUNCTIONS)
        if unknown:
            raise SystemExit(f"checks.m2.inbox_functions: unknown {sorted(unknown)}; known: {sorted(FUNCTIONS)}")
        self.views = views.lower()
        self.functions = frozenset(config["inbox_functions"])
        self.start_block = settings["start_block"]
        self.proxy = settings["proxy"].lower()
        self.use_api = bool((settings.get("inventory") or {}).get("api_url", "default"))  # as eb_reconcile reads it
        self.explorer = settings.get("alerts", {}).get("explorer", "https://etherscan.io")
        self.schedule = (Every(config["interval_s"]), OnInbox())
        self.last: dict = {}

    # ---------------------------------------------------------------- run

    def run(self, ctx: Ctx, block: int) -> list[Finding]:
        db = ctx.db
        reorg = ctx.reorg_ancestor is not None
        if reorg:  # README §6.2: a settlement on a deleted block is undone, the surviving revert is evaluated again
            db.conn.execute("UPDATE m2_reverts SET cleared_block = NULL WHERE cleared_block > ?", (ctx.reorg_ancestor,))
        db.conn.execute(
            "DELETE FROM m2_reverts WHERE EXISTS (SELECT 1 FROM inbox i WHERE i.tx_hash = m2_reverts.tx_hash AND "
            "i.block_hash = m2_reverts.block_hash AND i.canonical = 0)")
        self._ingest_inbox(ctx, block)
        limit = int(ctx.params.validators_per_operator_limit)
        inv = ctx.inventory(block)
        known, unknown, listed_invalid = self._legacy(ctx, inv, block)
        valid = [c for c in known if c.cluster.validator_count > 0]
        degraded = self.use_api and not any("api" in c.sources for c in inv.clusters.values())
        reverts = db.conn.execute("SELECT * FROM m2_reverts WHERE cleared_block IS NULL ORDER BY block, rowid").fetchall()
        open_keys = {r[0] for r in db.conn.execute("SELECT key FROM incidents WHERE check_id = ? AND state = 'open'",
                                                   (self.id,))}
        keys = open_keys | set(ctx.reorg_keys) | {r["key"] for r in reverts}
        ops = {op for c in known + unknown for op in c.operator_ids}
        ops |= {r["operator_id"] for r in reverts if r["operator_id"]}
        ops |= {int(k.split(":")[1]) for k in keys if not k.startswith("revert:tx:")}
        ops = sorted(ops)

        calls = [(self.views, sel + u256(op)) for op in ops for sel in (GET_OPERATOR_BY_ID, GET_OPERATOR_BY_ID_SSV)]
        out = ctx.rpc.call_many(calls) if calls else []
        eth = {op: decode_operator(out[2 * i], f"getOperatorById({op})") for i, op in enumerate(ops)}
        ssv = {op: decode_operator(out[2 * i + 1], f"getOperatorByIdSSV({op})") for i, op in enumerate(ops)}
        regs = self._registrations(ctx, block)

        findings: dict[str, Finding] = {}
        mismatched = []
        unproven: dict[int, str] = {}  # operator -> why "no blocked legacy cluster" is not proven at this block
        for op in ops:
            e, s = eth[op], ssv[op]
            on_op = [c for c in valid if op in c.operator_ids]
            if e["active"] and s["validator_count"] > 0:
                active_legacy = sum(c.cluster.validator_count for c in on_op if c.cluster.active)
                missing = s["validator_count"] - active_legacy
                if missing:
                    mismatched.append(op)
                findings.update(self._operator(op, e, s, on_op, limit, regs.get(op, 0), block))
                if f"hard:{op}" in findings:
                    continue
                # an active cluster of no known tuple holds at most `missing` validators (the on-chain count)
                if missing > 0 and e["validator_count"] + missing > limit:
                    unproven[op] = (f"{missing} of its {s['validator_count']} active legacy validators are in no known "
                                    f"cluster and {e['validator_count']} + {missing} > limit {limit}")
                elif missing < 0 and any(op in c.operator_ids for c in unknown):
                    unproven[op] = ("a legacy cluster on it has no tuple that validates against storage and the known "
                                    "clusters exceed the on-chain legacy count")
        for r in reverts:
            findings.setdefault(r["key"], None)
        kept = {}
        for key in sorted(keys | set(findings)):
            if key.startswith("revert:"):
                findings[key] = self._revert(ctx, key, block, eth, valid, unknown, limit, unproven, degraded)
            elif findings.get(key) is None:
                kind, op = key.split(":")
                if kind == "hard" and int(op) in unproven:
                    row = db.conn.execute("SELECT * FROM incidents WHERE check_id = ? AND key = ? AND state = 'open'",
                                          (self.id, key)).fetchone()
                    findings[key] = Finding(check=self.id, key=key, severity=row["severity"], title=row["title"],
                                            body=row["body"], evidence=json.loads(row["evidence"]),
                                            revision=row["revision"]) if row else None
                    if row:
                        kept[key] = unproven[int(op)]
                    continue
                findings[key] = Finding(check=self.id, key=key, severity="info", resolve=True,
                                        title=f"Operator capacity condition cleared (M2): {key}",
                                        body=f"{key}: the condition no longer holds at block {block} "
                                             f"(limit {limit}).")
        stuck = sorted(k for k in kept if k in ctx.reorg_keys)
        if stuck:  # README §6.2 (4): not re-found, not disproven on the new chain: roll back, keep the marker, retry
            raise RuntimeError(f"M2 cannot re-evaluate {stuck} after the reorg at block {block}: "
                               f"{kept[stuck[0]]} (the legacy cluster list is incomplete)")

        if kept:
            ctx.health("m2-hard-unknown", f"M2: at block {block} the hard rule of {sorted(kept)} is unknown: "
                       f"{'; '.join(f'{k}: {v}' for k, v in sorted(kept.items()))}. The open incident(s) are kept.",
                       dedup="m2-hard-unknown:" + keccak(json.dumps(sorted(kept)).encode()).hex()[:16], realert=True)
        if degraded:
            ctx.health("m2-inventory-empty", f"M2: the SSV API returned no cluster at block {block}; legacy clusters "
                       f"come from stored logs and the {len(known)} cluster(s) M2 last validated, re-checked against "
                       f"storage. Nothing is resolved for lack of API rows.", dedup="m2-inventory-empty", realert=True)
        if mismatched:
            ctx.health("m2-inventory", f"M2: the inventory's active legacy validators differ from getOperatorByIdSSV "
                       f"at block {block} for operator(s) {mismatched[:20]}; the legacy cluster list may be incomplete "
                       f"(hard rule), the early rule uses the on-chain count.",
                       dedup="m2-inventory:" + keccak(json.dumps(mismatched).encode()).hex()[:16], realert=True)
        if listed_invalid:
            ctx.health("m2-unvalidated", f"M2: {len(listed_invalid)} legacy cluster(s) in the inventory have no tuple "
                       f"that validates against storage at block {block} (e.g. {listed_invalid[0]}); "
                       f"{len(unknown)} remain unknown after re-checking M2's last validated tuples; the hard rule is "
                       f"unknown for their operators and open hard incidents are kept.",
                       dedup="m2-unvalidated:" + keccak(",".join(sorted(listed_invalid)).encode()).hex()[:16],
                       realert=True)
        self.last = {"block": block, "operators": len(ops), "legacy_clusters": len(valid), "limit": limit,
                     "open": sorted(k for k, f in findings.items() if f is not None and not f.resolve)}
        return [f for f in findings.values() if f is not None]

    def _legacy(self, ctx: Ctx, inv, block: int) -> tuple[list[ClusterInfo], list[ClusterInfo], list[str]]:
        """(legacy clusters with a tuple validated at `block`, legacy clusters without one, ids the inventory lists
        as legacy without a validating tuple). `m2_clusters` keeps every validated legacy tuple; a persisted cluster
        the inventory does not validate is re-checked against its storage words at `block`."""
        db = ctx.db
        known = [c for c in inv.clusters.values() if c.legacy and c.valid]
        listed_invalid = [c for c in inv.clusters.values() if c.legacy and not c.valid]
        for c in known:
            self._persist(db, c.cluster_id, c.owner, c.operator_ids, c.cluster, block, "inventory")
        done = {c.cluster_id for c in inv.clusters.values() if c.valid or not c.legacy}  # storage read by the inventory
        rows = [r for r in db.conn.execute("SELECT * FROM m2_clusters ORDER BY cluster_id") if r["cluster_id"] not in done]
        unknown = {c.cluster_id: c for c in listed_invalid}
        if rows:
            slots = []
            for r in rows:
                cid = bytes.fromhex(r["cluster_id"][2:])
                slots += [eth_cluster_slot(cid), ssv_cluster_slot(cid)]
            words = ctx.rpc.storage(self.proxy, slots, block)
            for i, r in enumerate(rows):
                eth_word, ssv_word = words[2 * i], words[2 * i + 1]
                if eth_word or not ssv_word:
                    unknown.pop(r["cluster_id"], None)  # storage holds no legacy cluster (migrated, or never on this chain)
                    continue
                cl = Cluster(*json.loads(r["tuple"]))
                ops = tuple(json.loads(r["operator_ids"]))
                if int.from_bytes(cl.hash_data(), "big") == ssv_word:
                    unknown.pop(r["cluster_id"], None)
                    known.append(ClusterInfo(r["cluster_id"], r["owner"], ops, "SSV", cl, "persisted",
                                             frozenset({"m2"}), None))
                elif r["cluster_id"] not in unknown:
                    unknown[r["cluster_id"]] = ClusterInfo(r["cluster_id"], r["owner"], ops, "SSV", None, None,
                                                           frozenset({"m2"}), None)
        return known, list(unknown.values()), [c.cluster_id for c in listed_invalid]

    def _persist(self, db, cid: str, owner: str, operator_ids, cl: Cluster, block: int, source: str,
                 replace: bool = True) -> None:
        tup = json.dumps([cl.validator_count, cl.network_fee_index, cl.index, cl.active, cl.balance])
        db.conn.execute(
            "INSERT INTO m2_clusters (cluster_id, owner, operator_ids, tuple, block, source) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(cluster_id) DO " + ("UPDATE SET tuple = excluded.tuple, block = excluded.block, source = "
                                             "excluded.source WHERE tuple != excluded.tuple" if replace else "NOTHING"),
            (cid, owner.lower(), json.dumps(list(operator_ids)), tup, block, source))

    # ---------------------------------------------------------------- rules

    def _operator(self, op, e, s, on_op, limit, regs, block) -> dict[str, Finding]:
        out = {}
        headroom = limit - e["validator_count"]
        legacy_validators = s["validator_count"]
        public = not e["private"]
        subject = {"operator_id": op}
        counts = {"limit": limit, "eth_validators": e["validator_count"], "legacy_validators": legacy_validators,
                  "eth_headroom": headroom, "public": public, "block": block}
        blocked = sorted((c for c in on_op if c.cluster.active and e["validator_count"] + c.cluster.validator_count
                          > limit), key=lambda c: c.cluster_id)
        if blocked:
            lines = [f"Operator {op} ({'public' if public else 'private'}): ETH validators {e['validator_count']} of "
                     f"limit {limit}; {len(blocked)} active legacy cluster(s) cannot migrate now:"]
            lines += [f"  {c.cluster_id} owner {c.owner}: {c.cluster.validator_count} validators (needs "
                      f"{e['validator_count'] + c.cluster.validator_count - limit} more ETH slots)" for c in blocked[:20]]
            lines.append("Runbook: tell the operator it can go private until its legacy clusters migrate (KI #6); "
                         "tell blocked owners to re-shard or self-liquidate (liquidateSSV as owner).")
            ids = [c.cluster_id for c in blocked]
            out[f"hard:{op}"] = Finding(
                check=self.id, key=f"hard:{op}", severity="warn",
                title=f"Legacy migration blocked by operator capacity (M2 #88326): operator {op}",
                body="\n".join(lines), subject=subject, evidence={**counts, "blocked": ids},
                revision="blocked:" + keccak(",".join(ids).encode()).hex()[:16])
        gap = headroom - legacy_validators
        if gap < self.config["early_headroom"]:
            out[f"early:{op}"] = Finding(
                check=self.id, key=f"early:{op}", severity="warn" if public else "info",
                title=f"Operator near ETH capacity with legacy validators (M2): operator {op}",
                body=(f"Operator {op} ({'public' if public else 'private'}): ETH headroom {headroom} (limit {limit} - "
                      f"{e['validator_count']} ETH validators) minus {legacy_validators} legacy validators = {gap}, "
                      f"below {self.config['early_headroom']}."),
                subject=subject, evidence={**counts, "gap": gap, "early_headroom": self.config["early_headroom"]})
        if regs > headroom:
            out[f"velocity:{op}"] = Finding(
                check=self.id, key=f"velocity:{op}", severity="info",
                title=f"Registration velocity exceeds ETH headroom (M2): operator {op}",
                body=(f"Operator {op}: {regs} ValidatorAdded registrations in the last "
                      f"{self.config['velocity_window_blocks']} blocks exceed its ETH headroom {headroom} "
                      f"(limit {limit}, {legacy_validators} legacy validators)."),
                subject=subject, evidence={**counts, "registrations": regs,
                                           "window_blocks": self.config["velocity_window_blocks"]})
        return out

    def _registrations(self, ctx: Ctx, block: int) -> dict[int, int]:
        window = self.config["velocity_window_blocks"]
        lo = block - window
        if self.start_block > lo + 1:
            ctx.health("m2-velocity-window", f"M2: stored logs start at block {self.start_block}, inside the "
                       f"{window}-block velocity window ending at {block}; registrations before it are not counted.",
                       dedup=f"m2-velocity-window:{self.start_block}")
        counts: dict[int, int] = {}
        for (ops,) in ctx.db.conn.execute("SELECT operator_ids FROM logs WHERE kind = 'ValidatorAdded' AND block > ? "
                                          "AND block <= ?", (lo, block)):
            for op in json.loads(ops):
                counts[op] = counts.get(op, 0) + 1
        return counts

    # ---------------------------------------------------------------- inbox

    def _ingest_inbox(self, ctx: Ctx, block: int) -> None:
        for e in ctx.inbox:
            fn = FUNCTION_BY_SELECTOR.get((e.selector or "").lower()) or (e.function or "").split("(", 1)[0]
            if fn not in self.functions:
                continue
            if e.revert_selector is None:  # it may be ExceedValidatorLimitWithData: never read as clean
                ctx.health("m2-inbox-unresolved", f"M2: the revert of {fn} {e.tx_hash} (block {e.block}) could not "
                           f"be resolved ({e.revert_source}); check it by hand.", dedup=f"m2-inbox-unresolved:{e.tx_hash}")
                continue
            if e.revert_selector != ERR_EXCEED_VALIDATOR_LIMIT:
                continue
            row = ctx.db.conn.execute("SELECT revert_data FROM inbox WHERE id = ?", (e.id,)).fetchone()
            op = decode_uint_arg(row["revert_data"] if row else None)
            tx = ctx.rpc.transaction(e.tx_hash)
            if not tx:
                raise RpcError(f"inbox {e.tx_hash}: eth_getTransactionByHash returned nothing")
            dec, cl = decode_cluster_calldata(tx), decode_cluster_tuple(tx)
            if dec and cl:  # the reverted call's cluster: settlement re-checks it even if the inventory omits it
                self._persist(ctx.db, "0x" + cluster_id(dec[1], dec[2]).hex(), dec[1], dec[2], cl, e.block, "inbox",
                              replace=False)
            key = f"revert:{op}" if op else f"revert:tx:{e.tx_hash}"
            ctx.db.conn.execute(
                "INSERT OR IGNORE INTO m2_reverts (tx_hash, block_hash, block, key, operator_id, function, sender, "
                "ingested_block) VALUES (?,?,?,?,?,?,?,?)",
                (e.tx_hash, e.block_hash, e.block, key, op, fn, (e.sender or "").lower(), block))

    def _revert(self, ctx: Ctx, key: str, block: int, eth: dict, valid, unknown, limit: int, unproven: dict,
                degraded: bool) -> Finding | None:
        db = ctx.db
        rows = db.conn.execute("SELECT * FROM m2_reverts WHERE key = ? AND cleared_block IS NULL ORDER BY block, "
                               "rowid", (key,)).fetchall()
        blocked, why = [], None
        if key.startswith("revert:tx:"):
            settled = bool(rows) and all(r["ingested_block"] < block for r in rows)
        else:
            op = int(key.split(":")[1])
            n = eth[op]["validator_count"]
            blocked = [c.cluster_id for c in valid if op in c.operator_ids and n + c.cluster.validator_count > limit]
            if any(op in c.operator_ids for c in unknown):
                why = "a legacy cluster on the operator has no tuple that validates against storage"
            elif op in unproven:
                why = unproven[op]
            elif degraded:
                why = "the SSV API returned no cluster, so liquidated legacy clusters on the operator are not known"
            settled = bool(rows) and all(r["ingested_block"] < block for r in rows) and not blocked and why is None
        if settled:
            db.conn.execute("UPDATE m2_reverts SET cleared_block = ? WHERE key = ? AND cleared_block IS NULL",
                            (block, key))
            rows = []
        if not rows:
            return Finding(check=self.id, key=key, severity="info", resolve=True,
                           title=f"ExceedValidatorLimit revert settled (M2): {key}",
                           body=f"{key}: no legacy cluster on the operator is over the limit {limit} at block {block}"
                           if not key.startswith("revert:tx:") else f"{key}: reported once.")
        r = rows[-1]
        who = f"operator {r['operator_id']}" if r["operator_id"] else "an operator the revert does not name"
        lines = [f"{r['function']} by {r['sender']} reverted with ExceedValidatorLimitWithData ({who}): {r['tx_hash']} "
                 f"(block {r['block']}).", f"{self.explorer}/tx/{r['tx_hash']}"]
        if blocked:
            lines.insert(1, f"Legacy clusters on the operator over the limit {limit} at block {block}: {blocked[:20]}")
        elif why:
            lines.insert(1, f"Not settled at block {block}: {why}.")
        return Finding(check=self.id, key=key, severity="warn",
                       title=f"Legacy migration/reactivation reverted on operator capacity (M2): {who}",
                       body="\n".join(lines), subject={"operator_id": r["operator_id"]},
                       evidence={"block": block, "reverts": [x["tx_hash"] for x in rows], "blocked": blocked},
                       revision=f"revert:{r['tx_hash']}")

    def digest(self, ctx: Ctx) -> list[str]:
        if not self.last:
            return []
        return [f"M2: block {self.last['block']}: {self.last['operators']} operator(s) with legacy clusters, "
                f"{self.last['legacy_clusters']} legacy cluster(s), limit {self.last['limit']}; open: "
                f"{', '.join(self.last['open']) or 'none'}"]
