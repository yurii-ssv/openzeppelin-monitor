"""M6 — Legacy SSV clusters: liquidation coverage (spec M6, KI #5 SSV path; #91046, #81952, #79383).

Legacy `VERSION_SSV` clusters cannot be topped up and drain monotonically; unbacked SSV operator credit
appears only after a cluster hits zero. For every **active** legacy cluster of the inventory (SSV API v4
reconciled with logs, every tuple validated against storage at the evaluated block — D8, D9):

  isLiquidatableSSV true for more than `warn_after_window_bps` of the live SSV liquidation window
      (`liquidation_threshold_period_ssv` blocks; 5000 bps = half the window ≈ 7 days)     -> warn
  getBalanceSSV == 0 while active (and burning: validatorCount > 0)                         -> page
  weekly digest: legacy clusters, validators, SSV/yr burn                                    -> digest

One incident per cluster (key = cluster id): warn escalates to page under the same id; it resolves when the
cluster is liquidated, migrated to ETH or no longer meets either condition. Every value is read at the run's
block through Views (`getBalanceSSV`, `isLiquidatableSSV`, `getBurnRateSSV`) and `ctx.params`; nothing is
hard-coded.

"Liquidatable since" (persisted in `m6_clusters`): the first block the check observed the cluster
liquidatable. When there is no observation of the cluster being clear before it (worker start, or it crossed
between two sweeps), the start is back-dated from chain values: the live balance is
`ceil((threshold - balance) / burn)` blocks of burn below the liquidation threshold
`max(minimum_liquidation_collateral_ssv, liquidation_threshold_period_ssv * burn)`, never earlier than the
block after the last sweep that saw the cluster clear, never later than the observation.

A cluster the check cannot evaluate (no storage-validated tuple, a reverting or empty view) is UNKNOWN, never
clean: a health message names it and its open incident is re-reported unchanged.

Reorg rule (checks/README.md §6.2): a run with `ctx.reorg_ancestor` = A first rewinds `m6_clusters` (a
`last_clear_block` above A is forgotten; a liquidatable start first observed above A is dropped, and
re-derived by this run), then sweeps every legacy cluster of the inventory at the new block AND every key of
an open M6 incident (which covers `ctx.reorg_keys`) and every cluster with a persisted row; each open key
gets a finding, a `resolve=True` or an UNKNOWN re-report.
"""

from __future__ import annotations

import json

from checks.base import Check, Ctx, Every, Finding, OnEvents
from checks.registry import register
from eb_storage import eth_cluster_slot, keccak, ssv_cluster_slot, u256
from rpc import RpcError

CLUSTER_T = "(uint32,uint64,uint64,bool,uint256)"
GET_BALANCE_SSV = keccak(f"getBalanceSSV(address,uint64[],{CLUSTER_T})".encode())[:4]
IS_LIQUIDATABLE_SSV = keccak(f"isLiquidatableSSV(address,uint64[],{CLUSTER_T})".encode())[:4]
GET_BURN_RATE_SSV = keccak(f"getBurnRateSSV(address,uint64[],{CLUSTER_T})".encode())[:4]

EVENT_KINDS = {"ValidatorAdded", "ValidatorRemoved", "ClusterLiquidated", "ClusterReactivated", "ClusterMigratedToETH"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS m6_clusters (
  cluster_id TEXT PRIMARY KEY, last_clear_block INTEGER, liquidatable_since INTEGER,
  first_liquidatable_block INTEGER, since_estimated INTEGER, last_block INTEGER NOT NULL
)
"""


def cluster_call(selector: bytes, owner: str, operator_ids, cluster) -> bytes:
    """ABI calldata for `f(address, uint64[], Cluster)`: the static tuple sits inline in the head."""
    return (selector + bytes.fromhex(owner[2:]).rjust(32, b"\0") + u256(7 * 32)
            + b"".join(u256(int(x)) for x in cluster.as_tuple())
            + u256(len(operator_ids)) + b"".join(u256(o) for o in operator_ids))


def word(out: bytes, what: str) -> int:
    """The first return word; an empty or short answer is an error, never zero."""
    if len(out) < 32:
        raise ValueError(f"{what} returned {len(out)} bytes")
    return int.from_bytes(out[:32], "big")


def open_incidents(ctx: Ctx) -> dict[str, dict]:
    """This check's open incidents by key (read-only; the framework owns the table)."""
    rows = ctx.db.conn.execute(
        "SELECT key, severity, title, body, subject, evidence, revision FROM incidents "
        "WHERE check_id=? AND state='open'", (ctx.check_id,)).fetchall()
    return {r["key"]: dict(r) for r in rows}


def unknown_rereport(check_id: str, row: dict, why: str) -> Finding:
    """Keep an open incident open, unchanged (same severity, no new revision), because it cannot be evaluated."""
    return Finding(check=check_id, key=row["key"], severity=row["severity"], title=row["title"],
                   body=f"{row['body']}\nRe-evaluation UNKNOWN: {why}", subject=json.loads(row["subject"]),
                   evidence={**json.loads(row["evidence"]), "unknown": why})


def call_each(ctx: Ctx, calls: list[tuple[str, bytes]]) -> tuple[list[bytes | None], dict[int, str]]:
    """Batched eth_call that isolates failures: one batch for the whole list; on a JSON-RPC error (a revert)
    the calls are repeated one by one so only the failing ones are lost. Returns the outputs (None where a call
    failed) and {index: reason}. Transport errors propagate (the run is retried)."""
    try:
        return (ctx.rpc.call_many(calls) if calls else []), {}
    except RpcError as exc:
        if exc.transport:
            raise
    outs: list[bytes | None] = []
    failed: dict[int, str] = {}
    for i, (to, data) in enumerate(calls):
        try:
            outs.append(ctx.rpc.call(to, data))
        except RpcError as one:
            if one.transport:
                raise
            outs.append(None)
            failed[i] = f"eth_call failed: {one}"
    return outs, failed


def legacy_balances(ctx: Ctx, views: str, clusters: list, with_risk: bool) -> tuple[dict, dict]:
    """Live view values of active legacy clusters at the run's block.

    Returns ({cluster_id: {"balance"[, "liquidatable", "burn"]}}, {cluster_id: reason}); the second dict holds
    the clusters whose values could not be read (a revert or an empty answer: UNKNOWN, never clean)."""
    sels = [GET_BALANCE_SSV] + ([IS_LIQUIDATABLE_SSV, GET_BURN_RATE_SSV] if with_risk else [])
    n = len(sels)
    calls = [(views, cluster_call(s, c.owner, c.operator_ids, c.cluster)) for c in clusters for s in sels]
    outs, errors = call_each(ctx, calls)
    values: dict[str, dict] = {}
    failed: dict[str, str] = {}
    for i, c in enumerate(clusters):
        bad = [errors[j] for j in range(i * n, (i + 1) * n) if j in errors]
        if bad:
            failed[c.cluster_id] = bad[0]
            continue
        try:
            got = outs[i * n:(i + 1) * n]
            v = {"balance": word(got[0], "getBalanceSSV")}
            if with_risk:
                v["liquidatable"] = bool(word(got[1], "isLiquidatableSSV"))
                v["burn"] = word(got[2], "getBurnRateSSV")
            values[c.cluster_id] = v
        except ValueError as exc:
            failed[c.cluster_id] = str(exc)
    return values, failed


def require_views(settings: dict, check_id: str) -> str:
    views = settings.get("views")
    if not views:
        raise SystemExit(f"checks.{check_id}: the worker config has no `views` address (SSVNetworkViews); "
                         f"{check_id} cannot read legacy cluster balances without it")
    inv = settings.get("inventory") or {}
    if "api_url" in inv and not inv["api_url"]:
        raise SystemExit(f"checks.{check_id}: inventory.api_url is disabled; the legacy SSV cluster set comes from "
                         f"the SSV API v4 reconciled with logs (D8) — logs since start_block alone miss every legacy "
                         f"cluster without a recent event")
    return views.lower()


@register
class M6LegacyCoverage(Check):
    id = "m6"
    defaults = {
        "sweep_interval_s": 3600,
        "warn_after_window_bps": 5000,  # spec M6: liquidatable for more than half the SSV liquidation window
        "digest_interval_s": 7 * 86400,  # spec M6: weekly digest
        "blocks_per_year": 2_628_000,  # 12 s slots; digest display only
    }
    schema = SCHEMA

    def __init__(self, config: dict, settings: dict):
        super().__init__(config, settings)
        self.views = require_views(settings, self.id)
        if not 0 < int(config["warn_after_window_bps"]) <= 10_000:
            raise SystemExit("checks.m6.warn_after_window_bps must be in (0, 10000]")
        # the sweep, plus a targeted run for the clusters of new cluster events (a liquidation, a migration or a
        # validator change of a legacy cluster is evaluated at the block it is scanned)
        self.schedule = (Every(config["sweep_interval_s"]), OnEvents(EVENT_KINDS))

    def prepare(self, ctx: Ctx) -> None:
        self.period = ctx.params.liquidation_threshold_period_ssv
        self.min_collateral = ctx.params.minimum_liquidation_collateral_ssv

    # ---- the sweep

    def run(self, ctx: Ctx, block: int) -> list[Finding]:
        db = ctx.db.conn
        ancestor = ctx.reorg_ancestor
        if ancestor is not None:  # rows derived from deleted blocks (README §6.2 rule 5)
            db.execute("UPDATE m6_clusters SET last_clear_block=NULL WHERE last_clear_block>?", (ancestor,))
            db.execute("UPDATE m6_clusters SET liquidatable_since=NULL, first_liquidatable_block=NULL, "
                       "since_estimated=NULL WHERE first_liquidatable_block>?", (ancestor,))
        # copies from `prepare`: the framework re-runs it after every parameter reload and every reorg (§5.1)
        period, min_coll = self.period, self.min_collateral
        warn_blocks = period * int(self.config["warn_after_window_bps"]) // 10_000

        # a run only for new cluster events looks at those clusters; any other reason is a full sweep
        scope = None
        if ctx.reasons <= {"events"} and ancestor is None:
            scope = {"0x" + ev.cluster_id.hex() for ev in ctx.events() if ev.cluster_id}

        def in_scope(cid: str) -> bool:
            return scope is None or cid in scope

        inv = ctx.inventory(block)
        legacy = {cid: c for cid, c in inv.clusters.items() if c.version == "SSV" and in_scope(cid)}
        active = [c for c in legacy.values() if c.valid and c.cluster.active]
        values, failed = legacy_balances(ctx, self.views, active, with_risk=True)
        unknown = {cid: "no candidate tuple validates against storage" for cid, c in legacy.items() if not c.valid}
        unknown.update(failed)

        rows = {r["cluster_id"]: dict(r) for r in db.execute("SELECT * FROM m6_clusters") if in_scope(r["cluster_id"])}
        opened = {k: r for k, r in open_incidents(ctx).items() if in_scope(k)}
        findings: dict[str, Finding] = {}
        summary = {"block": block, "active": 0, "validators": 0, "burn_per_block": 0, "liquidatable": 0,
                   "warn": 0, "zero": 0, "inert": 0, "inert_zero": 0, "inert_zero_ids": [], "unknown": len(unknown),
                   "inactive": 0}

        for c in active:
            v = values.get(c.cluster_id)
            if v is None:
                continue
            vc = c.cluster.validator_count
            summary["active"] += 1
            summary["validators"] += vc
            summary["burn_per_block"] += v["burn"]
            summary["inert"] += vc == 0
            if vc == 0 and v["balance"] == 0:  # burns nothing: not paged (coordinator ruling), listed in the digest
                summary["inert_zero"] += 1
                summary["inert_zero_ids"] = sorted(summary["inert_zero_ids"] + [c.cluster_id])[:5]
            row = rows.get(c.cluster_id) or {}
            since, estimated, first = row.get("liquidatable_since"), row.get("since_estimated"), row.get(
                "first_liquidatable_block")
            if v["liquidatable"]:
                summary["liquidatable"] += 1
                if since is None:
                    since, estimated = self._back_date(block, v, period, min_coll, row.get("last_clear_block"))
                    first = block
                db.execute(
                    "INSERT INTO m6_clusters (cluster_id, last_clear_block, liquidatable_since, "
                    "first_liquidatable_block, since_estimated, last_block) VALUES (?,?,?,?,?,?) "
                    "ON CONFLICT(cluster_id) DO UPDATE SET liquidatable_since=excluded.liquidatable_since, "
                    "first_liquidatable_block=excluded.first_liquidatable_block, "
                    "since_estimated=excluded.since_estimated, last_block=excluded.last_block",
                    (c.cluster_id, row.get("last_clear_block"), since, first, int(estimated), block))
            else:
                since = None
                db.execute(
                    "INSERT INTO m6_clusters (cluster_id, last_clear_block, liquidatable_since, "
                    "first_liquidatable_block, since_estimated, last_block) VALUES (?,?,NULL,NULL,NULL,?) "
                    "ON CONFLICT(cluster_id) DO UPDATE SET last_clear_block=excluded.last_clear_block, "
                    "liquidatable_since=NULL, first_liquidatable_block=NULL, since_estimated=NULL, "
                    "last_block=excluded.last_block", (c.cluster_id, block, block))
            zero = v["balance"] == 0 and vc > 0
            lag = block - since if since is not None else None
            late = lag is not None and lag > warn_blocks
            summary["zero"] += zero
            summary["warn"] += late and not zero
            f = self._finding(c, v, block, since, bool(estimated), lag, warn_blocks, period, zero, late)
            if f is not None:
                findings[c.cluster_id] = f
            elif c.cluster_id in opened:
                findings[c.cluster_id] = self._resolved(c.cluster_id, opened[c.cluster_id], block,
                                                        "no longer meets an M6 condition (balance "
                                                        f"{_ssv(v['balance'])} SSV, liquidatable: {v['liquidatable']})")

        # every open incident and persisted row not settled above: liquidated, migrated, gone or unknown
        for cid in sorted((set(opened) | set(rows) | set(ctx.reorg_keys)) - set(findings)):
            if cid in values:
                continue  # evaluated and clear, no incident open
            c = inv.clusters.get(cid)
            if cid in unknown:
                if cid in opened:
                    findings[cid] = unknown_rereport(self.id, opened[cid], unknown[cid])
                continue
            state = self._fate(ctx, cid, c, block)
            if state is None:  # still legacy and active per storage, but not evaluable here
                why = "the cluster is not in the inventory at this block"
                unknown[cid] = why
                if cid in opened:
                    findings[cid] = unknown_rereport(self.id, opened[cid], why)
                continue
            db.execute("DELETE FROM m6_clusters WHERE cluster_id=?", (cid,))
            if cid in opened:
                findings[cid] = self._resolved(cid, opened[cid], block, state)
        summary["inactive"] = sum(1 for c in legacy.values() if c.valid and not c.cluster.active)
        summary["burn_per_block"] = str(summary["burn_per_block"])
        if scope is None:
            ctx.set_state("summary", summary)

        if unknown:
            names = ", ".join(sorted(unknown)[:10])
            ctx.health("m6-unknown", f"M6 cannot evaluate {len(unknown)} legacy SSV cluster(s) at block {block} "
                       f"(UNKNOWN, not clean): {names}{' …' if len(unknown) > 10 else ''}. "
                       f"First reason: {unknown[sorted(unknown)[0]]}",
                       dedup=f"m6-unknown:{','.join(sorted(unknown))}", realert=True)
        return list(findings.values())

    def _back_date(self, block: int, v: dict, period: int, min_coll: int, last_clear: int | None) -> tuple[int, bool]:
        """Estimated first liquidatable block from chain values (see the module docstring)."""
        burn = v["burn"]
        if burn <= 0:
            return block, False  # nothing to extrapolate from: observed now
        threshold = max(min_coll, period * burn)
        below = max(0, threshold - v["balance"])
        since = block - (below + burn - 1) // burn
        if last_clear is not None:
            since = max(since, last_clear + 1)
        return min(since, block), since < block

    def _finding(self, c, v, block, since, estimated, lag, warn_blocks, period, zero, late) -> Finding | None:
        if not zero and not late:
            return None
        subject = {"cluster_id": c.cluster_id, "owner": c.owner, "operator_ids": list(c.operator_ids),
                   "validator_count": c.cluster.validator_count}
        evidence = {"block": block, "balance_ssv_wei": str(v["balance"]), "burn_rate_ssv_wei": str(v["burn"]),
                    "liquidatable": v["liquidatable"], "liquidatable_since_block": since,
                    "since_estimated": estimated, "estimated_start": estimated, "liquidatable_blocks": lag, "warn_after_blocks": warn_blocks,
                    "liquidation_threshold_period_ssv": period, "tuple_source": c.tuple_source}
        who = (f"Legacy SSV cluster {c.cluster_id} (owner {c.owner}, operators "
               f"{','.join(map(str, c.operator_ids))}, {c.cluster.validator_count} validators)")
        when = (f"liquidatable since block {since}{' (estimated from balance and burn rate)' if estimated else ''}, "
                f"{lag} blocks ago")
        if zero:
            return Finding(
                check=self.id, key=c.cluster_id, severity="page",
                title=f"Legacy SSV cluster at zero balance while active: {c.cluster_id[:12]}",
                body=(f"{who} has a live SSV balance of 0 at block {block} and is still active: every block now "
                      f"credits its operators and the DAO {_ssv(v['burn'])} SSV that no cluster balance backs. "
                      f"It is {when}. Runbook: liquidate it with our keeper (liquidateSSV); the owner can also "
                      f"self-exit."),
                subject=subject, evidence=evidence)
        return Finding(
            check=self.id, key=c.cluster_id, severity="warn",
            title=f"Legacy SSV cluster liquidatable and not liquidated: {c.cluster_id[:12]}",
            body=(f"{who} is {when} — more than {warn_blocks} blocks ({self.config['warn_after_window_bps']} bps of "
                  f"the {period}-block SSV liquidation window) — and nobody has liquidated it. Live balance "
                  f"{_ssv(v['balance'])} SSV, burn {_ssv(v['burn'])} SSV/block. Liquidated before it reaches zero "
                  f"the loss is 0 SSV. Runbook: liquidate it with our keeper (liquidateSSV)."),
            subject=subject, evidence=evidence)

    def _resolved(self, cid: str, row: dict, block: int, why: str) -> Finding:
        return Finding(check=self.id, key=cid, severity=row["severity"], title=f"Resolved: {row['title']}",
                       body=f"Legacy SSV cluster {cid} {why} at block {block}.", subject=json.loads(row["subject"]),
                       evidence={"block": block, "reason": why}, resolve=True)

    def _fate(self, ctx: Ctx, cid: str, info, block: int) -> str | None:
        """Why a cluster is no longer an active legacy cluster, from storage at `block`; None = still is."""
        if info is not None and info.version == "ETH":
            return "was migrated to ETH"
        if info is not None and info.version == "SSV" and info.valid and not info.cluster.active:
            return "was liquidated"
        if info is not None and info.version is None:
            return "no longer exists in storage"
        raw = bytes.fromhex(cid[2:])
        eth_word, ssv_word = ctx.rpc.storage(ctx.settings["proxy"], [eth_cluster_slot(raw), ssv_cluster_slot(raw)])
        if eth_word:
            return "was migrated to ETH"
        if not ssv_word:
            return "no longer exists in storage"
        return None

    # ---- weekly digest

    def digest(self, ctx: Ctx) -> list[str]:
        s = ctx.state("summary")
        last = ctx.state("digest_at")
        now = ctx.clock()
        if not s or (last is not None and now - last < self.config["digest_interval_s"]):
            return []
        ctx.set_state("digest_at", now)
        burn = int(s["burn_per_block"])
        return [f"Legacy SSV clusters at block {s['block']}: {s['active']} active ({s['inert']} without validators), "
                f"{s['validators']} validators, burn {_ssv(burn * self.config['blocks_per_year'])} SSV/yr; "
                f"liquidatable {s['liquidatable']}, warn {s['warn']}, at zero {s['zero']}, unknown {s['unknown']}, "
                f"liquidated {s['inactive']}. The surface only shrinks.",
                f"Not evaluated for the zero-balance page (0 validators, so no burn): {s.get('inert_zero', 0)} active "
                f"legacy cluster(s) at balance 0" + (f", e.g. {', '.join(s['inert_zero_ids'])}" if s.get(
                    "inert_zero_ids") else "") + "."]


def _ssv(wei: int) -> str:
    """Exact decimal SSV for a wei amount (display only)."""
    whole, frac = divmod(abs(int(wei)), 10**18)
    return ("-" if int(wei) < 0 else "") + f"{whole}.{frac:018d}".rstrip("0").rstrip(".")
