"""M4 — ETH conservation-deficit scan (#89748; spec M4).

Phantom earnings (M3: clusters past zero whose vUnits keep accruing) show up as liabilities larger than
the ETH the proxy holds. After every `RootCommitted` and every `interval_s` (daily), at one block:

  liabilities = Σ getBalance(active ETH clusters) + Σ getOperatorEarnings(1 … lastOperatorId)
                + getNetworkEarnings()
  deficit     = liabilities − proxy ETH balance
  deficit > 0                                   page (key "deficit"; resolves when the scan is complete and ≤ 0)
  surplus (−deficit) trend                      digest lines (info): latest, previous scan, ≈ 24 h earlier

The spec's "staking ETH owed" term is `getNetworkEarnings()` itself, counted once (coordinator ruling
2026-10-21, to be amended in the spec at closure): at the pin, `ProtocolLib.networkTotalEarnings` is
`ethDaoBalance` plus the unsettled accrual, and `SSVStaking.claimEthRewards` subtracts every payout from both
`ethDaoBalance` and `stakingEthPoolBalance`, which is only the last-sync snapshot of the same channel
(≤ networkEarnings). Adding `stakingEthPoolBalance()` as a fourth term would double-count it; it is read and
reported as evidence only. This is the public-view equation of `evidences/#89748/report-staking-insolvency-impact.ts`.

Inputs and UNKNOWN: cluster tuples are storage-validated (`ctx.inventory`, D9); `lastOperatorId` is
StorageData member 8 of the proxy. A view that reverts or returns no data, an ETH cluster without a validated
tuple, no ETH cluster at all, an inventory whose active ETH validators do not add up to
`getNetworkValidatorsCount()`, or operators referenced by clusters beyond `lastOperatorId` make the liability
sum incomplete (a lower bound): a deficit of the lower bound still pages (it can only be larger), a surplus
of an incomplete sum is UNKNOWN — a health message (`m4-unknown`), never a resolution; an open deficit
incident is re-reported (kept open). Transport errors raise (the run is retried).

Reorg rule (README §6.2), table `m4_scans`, identical for depth 1 and N: rows of blocks above the ancestor A
are deleted in the reorg run (they were computed on deleted blocks), and the reorg run recomputes the
equation at the new checkpoint, so the key "deficit" is always re-reported (page), resolved (complete, no
deficit), or re-reported as UNKNOWN (kept open). The run is complete by construction (every term every run),
so a restart before the reorg run (durable marker) needs no extra state.

Lowered checkpoint (QA cp2a-2 H1 shape): a reorg onto a shorter chain, or a node lagging behind the checkpoint
(the framework treats a block the node does not have as not canonical), can put the reorg run's block below the
latest scan of an open deficit. A scan there is about an earlier block, not about that evidence: the page is kept
(state `hold` = the latest scan block above the run's block) and not resolved until a complete scan at or above
that block shows no deficit; while held the check runs at every checkpoint block (`near_set`).
"""

from __future__ import annotations

from checks.base import Check, Ctx, Every, Finding, NearSetEveryBlock, OnEvents
from checks.m3_views import (
    GET_BALANCE,
    GET_NETWORK_EARNINGS,
    GET_OPERATOR_EARNINGS,
    MAIN_LAST_OPERATOR_ID,
    STAKING_ETH_POOL_BALANCE,
    encode_cluster_args,
    inventory_gap,
    word,
)
from checks.registry import register
from eb_storage import u256
from rpc import RpcError

SCHEMA = """
CREATE TABLE IF NOT EXISTS m4_scans (
  block INTEGER PRIMARY KEY, at REAL NOT NULL, proxy_balance TEXT NOT NULL, clusters TEXT NOT NULL,
  operators TEXT NOT NULL, network TEXT NOT NULL, staking_pool TEXT, surplus TEXT NOT NULL,
  complete INTEGER NOT NULL, eth_clusters INTEGER NOT NULL, operator_count INTEGER NOT NULL
);
"""

WEI = 10**18
KEY = "deficit"


def eth(wei: int) -> str:
    sign, wei = ("-", -wei) if wei < 0 else ("", wei)
    whole, rem = divmod(wei, WEI)
    return sign + (f"{whole}" if rem == 0 else f"{whole}.{rem:018d}".rstrip("0"))


def _isolated(rpc, calls: list, block: int | None) -> tuple[list, dict]:
    """call_many; when one call reverts, retry one by one: (results with None for failures, {index: reason})."""
    try:
        return rpc.call_many(calls, block), {}
    except RpcError as exc:
        if exc.transport:
            raise
    out, bad = [], {}
    for i, c in enumerate(calls):
        try:
            out.append(rpc.call_many([c], block)[0])
        except RpcError as exc:
            if exc.transport:
                raise
            out.append(None)
            bad[i] = f"reverted: {exc} {exc.data or ''}".strip()
    return out, bad


def scan(rpc, inventory, views: str, proxy: str, block: int | None = None) -> dict:
    """The conservation equation at one block (also used by the LIVE probe). Raises RpcError on transport."""
    eth_clusters = [c for c in inventory.clusters.values() if c.version == "ETH"]
    gaps = [f"{c.cluster_id}: no candidate tuple validates against storage" for c in eth_clusters if not c.valid]
    active = [c for c in eth_clusters if c.valid and c.cluster.active]
    if not eth_clusters:
        gaps.append("no ETH cluster in the inventory")
    gap = inventory_gap(rpc, views, eth_clusters, block)
    if gap:
        gaps.append(gap)
    last_op = rpc.storage(proxy, [MAIN_LAST_OPERATOR_ID], block)[0]
    referenced = max((op for c in eth_clusters for op in c.operator_ids), default=0)
    if referenced > last_op:
        gaps.append(f"lastOperatorId {last_op} is below operator {referenced} referenced by a cluster")
    calls = [(views, GET_BALANCE + encode_cluster_args(c.owner, c.operator_ids, c.cluster)) for c in active]
    calls += [(views, GET_OPERATOR_EARNINGS + u256(i)) for i in range(1, last_op + 1)]
    calls += [(views, GET_NETWORK_EARNINGS), (views, STAKING_ETH_POOL_BALANCE)]
    out, bad = _isolated(rpc, calls, block)
    values = []
    for i, raw in enumerate(out):
        if raw is None:
            values.append(None)
            continue
        try:
            values.append(word(raw, "eth_call"))
        except ValueError as exc:
            values.append(None)
            bad[i] = str(exc)
    n = len(active)
    for i, reason in sorted(bad.items()):
        what = (active[i].cluster_id if i < n else f"operator {i - n + 1}" if i < n + last_op
                else "getNetworkEarnings" if i == n + last_op else "stakingEthPoolBalance")
        if what != "stakingEthPoolBalance":  # evidence only: not a liability term
            gaps.append(f"{what}: {reason}")
    clusters = sum(v for v in values[:n] if v is not None)
    operators = sum(v for v in values[n : n + last_op] if v is not None)
    network = values[n + last_op] or 0
    pool = values[n + last_op + 1]
    proxy_balance = rpc.balance(proxy, block)
    liabilities = clusters + operators + network
    return {"proxy_balance": proxy_balance, "clusters": clusters, "operators": operators, "network": network,
            "staking_pool": pool, "liabilities": liabilities, "surplus": proxy_balance - liabilities,
            "complete": not gaps, "gaps": gaps, "eth_clusters": len(eth_clusters), "active_eth_clusters": n,
            "operator_count": last_op}


@register
class M4ConservationDeficit(Check):
    id = "m4"
    defaults = {"interval_s": 86_400}
    schema = SCHEMA

    def __init__(self, config: dict, settings: dict):
        super().__init__(config, settings)
        if not settings.get("views"):
            raise SystemExit("checks.m4 needs the SSVNetworkViews address in the top-level 'views' setting")
        if not config["interval_s"] > 0:
            raise SystemExit(f"checks.m4: need interval_s > 0; got {config}")
        self.views, self.proxy = settings["views"].lower(), settings["proxy"].lower()
        self.schedule = (Every(config["interval_s"]), OnEvents({"RootCommitted"}), NearSetEveryBlock())
        self.near_set: frozenset = frozenset()  # {KEY} while a deficit is held above the checkpoint

    def run(self, ctx: Ctx, block: int) -> list[Finding]:
        hold = ctx.state("hold")
        if ctx.reorg_ancestor is not None:
            if KEY in ctx.reorg_keys or self.open(ctx):  # its latest scan above this block: not disproved here
                top = ctx.db.conn.execute("SELECT MAX(block) FROM m4_scans WHERE block > ?", (block,)).fetchone()[0]
                if top is not None:
                    hold = max(top, hold or 0)
            ctx.db.conn.execute("DELETE FROM m4_scans WHERE block > ?", (ctx.reorg_ancestor,))
        if hold is not None and block >= hold:
            hold = None
        ctx.set_state("hold", hold)
        self.near_set = frozenset({KEY}) if hold is not None else frozenset()
        s = scan(ctx.rpc, ctx.inventory(block), self.views, self.proxy)
        ctx.db.conn.execute(
            "INSERT OR REPLACE INTO m4_scans VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (block, ctx.clock(), str(s["proxy_balance"]), str(s["clusters"]), str(s["operators"]), str(s["network"]),
             None if s["staking_pool"] is None else str(s["staking_pool"]), str(s["surplus"]), int(s["complete"]),
             s["eth_clusters"], s["operator_count"]))
        evidence = {k: (str(v) if isinstance(v, int) and not isinstance(v, bool) else v) for k, v in s.items()}
        evidence["block"] = block
        terms = (f"Proxy ETH {eth(s['proxy_balance'])}; liabilities {eth(s['liabilities'])} = clusters "
                 f"{eth(s['clusters'])} ({s['active_eth_clusters']} active ETH) + operator earnings "
                 f"{eth(s['operators'])} ({s['operator_count']} operators) + network earnings {eth(s['network'])}. "
                 f"stakingEthPoolBalance {eth(s['staking_pool']) if s['staking_pool'] is not None else 'unreadable'} "
                 f"(evidence only: the same DAO channel as network earnings, not added).")
        deficit = -s["surplus"]
        if deficit > 0:
            note = "" if s["complete"] else ("\nThe liability sum is incomplete (a lower bound), so the deficit is at "
                                             "least this: " + "; ".join(s["gaps"][:5]))
            return [Finding(
                check=self.id, key=KEY, severity="page", title=f"M4 ETH conservation deficit: {eth(deficit)} ETH",
                body=(f"At block {block} the proxy's ETH does not cover its public-view liabilities: deficit "
                      f"{eth(deficit)} ETH.\n{terms}{note}\nRunbook: find the computed-zero or long-liquidatable "
                      f"clusters (M3) and liquidate them; escalate (accrued phantom earnings are not clawed back)."),
                subject={"proxy": self.proxy}, evidence=evidence)]
        if hold is not None and (KEY in ctx.reorg_keys or self.open(ctx)):
            return [Finding(check=self.id, key=KEY, severity="page", title="M4 ETH conservation deficit (evidence "
                            "above the checkpoint)",
                            body=(f"At block {block} the proxy's ETH covers its liabilities (surplus "
                                  f"{eth(s['surplus'])} ETH), but the deficit was scanned at block {hold}, above this "
                                  f"checkpoint (a reorg onto a shorter chain, or a node behind the checkpoint): not "
                                  f"proof about that block. Kept open until a scan at or above it.\n{terms}"),
                            subject={"proxy": self.proxy}, evidence={**evidence, "hold_block": hold})]
        if not s["complete"]:
            ctx.health("m4-unknown", (f"M4 cannot conclude at block {block}: the liability sum is incomplete, so a "
                                      f"surplus of {eth(s['surplus'])} ETH is not evidence of no deficit.\n- "
                                      + "\n- ".join(g[:200] for g in s["gaps"][:20])),
                       dedup="m4-unknown:" + "|".join(sorted(s["gaps"]))[:2000], realert=True)
            if KEY in ctx.reorg_keys or self.open(ctx):
                return [Finding(check=self.id, key=KEY, severity="page", title="M4 ETH conservation deficit (UNKNOWN)",
                                body=(f"At block {block} the deficit cannot be re-evaluated (incomplete inputs); "
                                      f"kept open.\n{terms}"), subject={"proxy": self.proxy}, evidence=evidence)]
            return []
        return [Finding(check=self.id, key=KEY, severity="info", resolve=True,
                        title="M4 ETH conservation restored",
                        body=f"At block {block} the proxy's ETH covers its liabilities: surplus {eth(s['surplus'])} "
                             f"ETH.\n{terms}", subject={"proxy": self.proxy}, evidence=evidence)]

    def open(self, ctx: Ctx) -> bool:
        return ctx.db.conn.execute("SELECT 1 FROM incidents WHERE check_id=? AND key=? AND state='open'",
                                   (self.id, KEY)).fetchone() is not None

    def digest(self, ctx: Ctx) -> list[str]:
        rows = ctx.db.conn.execute("SELECT * FROM m4_scans ORDER BY block DESC LIMIT 200").fetchall()
        if not rows:
            return ["No conservation scan yet."]
        last = rows[0]
        lines = [f"Surplus {eth(int(last['surplus']))} ETH at block {last['block']}"
                 + ("" if last["complete"] else " (incomplete inputs)")]
        if len(rows) > 1:
            prev = rows[1]
            lines.append(f"Change since block {prev['block']}: {eth(int(last['surplus']) - int(prev['surplus']))} ETH")
        day = next((r for r in rows if last["at"] - r["at"] >= 86_400), None)
        if day is not None:
            lines.append(f"Change over ≈ 24 h (since block {day['block']}): "
                         f"{eth(int(last['surplus']) - int(day['surplus']))} ETH")
        return lines

