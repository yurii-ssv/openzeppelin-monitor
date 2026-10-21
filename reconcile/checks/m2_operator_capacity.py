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
read raises (RPC error); a legacy cluster without a validated tuple leaves its operators' hard rule unknown (the
open incident is kept, a health message names them).

Reorg rule (checks/README.md §6.2): the conditions are recomputed from the chain and the new chain's stored logs at
every run, so the reorg run is an ordinary complete run; `m2_reverts` rows follow their inbox entry
(`canonical = 0` drops them, in any run).
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
    decode_operator,
    decode_uint_arg,
)
from checks.registry import register
from eb_storage import keccak, u256

SCHEMA = """
CREATE TABLE IF NOT EXISTS m2_reverts (
  tx_hash TEXT NOT NULL, block_hash TEXT NOT NULL, block INTEGER NOT NULL, key TEXT NOT NULL, operator_id INTEGER,
  function TEXT, sender TEXT, ingested_block INTEGER NOT NULL, cleared_block INTEGER,
  PRIMARY KEY (tx_hash, block_hash)
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
        self.explorer = settings.get("alerts", {}).get("explorer", "https://etherscan.io")
        self.schedule = (Every(config["interval_s"]), OnInbox())
        self.last: dict = {}

    # ---------------------------------------------------------------- run

    def run(self, ctx: Ctx, block: int) -> list[Finding]:
        db = ctx.db
        db.conn.execute(
            "DELETE FROM m2_reverts WHERE EXISTS (SELECT 1 FROM inbox i WHERE i.tx_hash = m2_reverts.tx_hash AND "
            "i.block_hash = m2_reverts.block_hash AND i.canonical = 0)")
        self._ingest_inbox(ctx, block)
        limit = int(ctx.params.validators_per_operator_limit)
        inv = ctx.inventory(block)
        legacy = [c for c in inv.clusters.values() if c.legacy]
        valid = [c for c in legacy if c.valid and c.cluster.validator_count > 0]
        invalid = [c for c in legacy if not c.valid]
        reverts = db.conn.execute("SELECT * FROM m2_reverts WHERE cleared_block IS NULL ORDER BY block, rowid").fetchall()
        open_keys = {r[0] for r in db.conn.execute("SELECT key FROM incidents WHERE check_id = ? AND state = 'open'",
                                                   (self.id,))}
        keys = open_keys | set(ctx.reorg_keys) | {r["key"] for r in reverts}
        ops = {op for c in legacy for op in c.operator_ids} | {r["operator_id"] for r in reverts if r["operator_id"]}
        ops |= {int(k.split(":")[1]) for k in keys if not k.startswith("revert:tx:")}
        ops = sorted(ops)

        calls = [(self.views, sel + u256(op)) for op in ops for sel in (GET_OPERATOR_BY_ID, GET_OPERATOR_BY_ID_SSV)]
        out = ctx.rpc.call_many(calls) if calls else []
        eth = {op: decode_operator(out[2 * i], f"getOperatorById({op})") for i, op in enumerate(ops)}
        ssv = {op: decode_operator(out[2 * i + 1], f"getOperatorByIdSSV({op})") for i, op in enumerate(ops)}
        regs = self._registrations(ctx, block)

        findings: dict[str, Finding] = {}
        mismatched, unknown_ops = [], set()
        for op in ops:
            e, s = eth[op], ssv[op]
            on_op = [c for c in valid if op in c.operator_ids]
            if e["active"] and s["validator_count"] > 0:
                active_legacy = sum(c.cluster.validator_count for c in on_op if c.cluster.active)
                if active_legacy != s["validator_count"]:
                    mismatched.append(op)
                findings.update(self._operator(op, e, s, on_op, limit, regs.get(op, 0), block))
                if any(op in c.operator_ids for c in invalid) and f"hard:{op}" not in findings:
                    unknown_ops.add(op)
        for r in reverts:
            findings.setdefault(r["key"], None)
        for key in sorted(keys | set(findings)):
            if key.startswith("revert:"):
                findings[key] = self._revert(ctx, key, block, eth, valid, limit)
            elif findings.get(key) is None:
                kind, op = key.split(":")
                if kind == "hard" and int(op) in unknown_ops:
                    row = db.conn.execute("SELECT * FROM incidents WHERE check_id = ? AND key = ? AND state = 'open'",
                                          (self.id, key)).fetchone()
                    findings[key] = Finding(check=self.id, key=key, severity=row["severity"], title=row["title"],
                                            body=row["body"], evidence=json.loads(row["evidence"]),
                                            revision=row["revision"]) if row else None
                    continue
                findings[key] = Finding(check=self.id, key=key, severity="info", resolve=True,
                                        title=f"Operator capacity condition cleared (M2): {key}",
                                        body=f"{key}: the condition no longer holds at block {block} "
                                             f"(limit {limit}).")

        if mismatched:
            ctx.health("m2-inventory", f"M2: the inventory's active legacy validators differ from getOperatorByIdSSV "
                       f"at block {block} for operator(s) {mismatched[:20]}; the legacy cluster list may be incomplete "
                       f"(hard rule), the early rule uses the on-chain count.",
                       dedup="m2-inventory:" + keccak(json.dumps(mismatched).encode()).hex()[:16], realert=True)
        if invalid:
            ctx.health("m2-unvalidated", f"M2: {len(invalid)} legacy cluster(s) have no tuple that validates against "
                       f"storage at block {block} (e.g. {invalid[0].cluster_id}); the hard rule is unknown for them "
                       f"and open hard incidents of their operators are kept.",
                       dedup="m2-unvalidated:" + keccak(",".join(sorted(c.cluster_id for c in invalid)).encode())
                       .hex()[:16], realert=True)
        self.last = {"block": block, "operators": len(ops), "legacy_clusters": len(valid), "limit": limit,
                     "open": sorted(k for k, f in findings.items() if f is not None and not f.resolve)}
        return [f for f in findings.values() if f is not None]

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
            key = f"revert:{op}" if op else f"revert:tx:{e.tx_hash}"
            ctx.db.conn.execute(
                "INSERT OR IGNORE INTO m2_reverts (tx_hash, block_hash, block, key, operator_id, function, sender, "
                "ingested_block) VALUES (?,?,?,?,?,?,?,?)",
                (e.tx_hash, e.block_hash, e.block, key, op, fn, (e.sender or "").lower(), block))

    def _revert(self, ctx: Ctx, key: str, block: int, eth: dict, valid, limit: int) -> Finding | None:
        db = ctx.db
        rows = db.conn.execute("SELECT * FROM m2_reverts WHERE key = ? AND cleared_block IS NULL ORDER BY block, "
                               "rowid", (key,)).fetchall()
        blocked = []
        if key.startswith("revert:tx:"):
            settled = bool(rows) and all(r["ingested_block"] < block for r in rows)
        else:
            op = int(key.split(":")[1])
            n = eth[op]["validator_count"]
            blocked = [c.cluster_id for c in valid if op in c.operator_ids and n + c.cluster.validator_count > limit]
            settled = bool(rows) and all(r["ingested_block"] < block for r in rows) and not blocked
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
