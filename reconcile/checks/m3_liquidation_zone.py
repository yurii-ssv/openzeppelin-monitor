"""M3 — ETH cluster liquidation zone (KI #5, #89748 family; spec M3, D5).

The contract never liquidates by itself: an ETH cluster past its threshold that nobody liquidates
keeps burning until its view-computed balance floors at 0, while operator and DAO earnings keep
accruing on its vUnits (phantom earnings, M4). For every active ETH cluster with validators:

  isLiquidatable turns true                          info  (onset; the block is recorded)
  still liquidatable N blocks after the onset        warn  (fallback lag; `n_blocks`, default 50)
  still liquidatable N2 blocks after the onset       page  (`n2_blocks`, default 7,200)
  active and getBalance == 0 (computed zero)         page  (phantom accrual has started)
  projected to cross within `pre_alert_blocks`       digest line (info, daily digest)
  no longer liquidatable (deposit, liquidated, …)    resolves (a resolution is sent for warn/page)

Inputs: `SSVNetworkViews.isLiquidatable`, `getBalance`, `getBurnRate` (view-computed balances; the
stored balance is stale between interactions), batched at the checkpoint block, on storage-validated
tuples (`ctx.inventory`, D9); the projection reads `liquidation_threshold_period` and
`minimum_liquidation_collateral` from `ctx.params` (live). N and N2 are config (D5), never constants.

Sweep policy: a full sweep (every ETH cluster of the inventory) every `full_sweep_interval_s`, after
every `RootCommitted` and `OperatorFeeExecuted` (an operator fee increase raises burn rates without any
cluster event: a cluster it pushes over its threshold is seen at that block, not up to one interval later,
so an onset that a top-up heals before the next interval is not lost), after a parameter change, after a
reorg and at the first run; the clusters of
any stored cluster event at the next run; the projected-near set (liquidatable now or crossing within
`near_blocks`) at every checkpoint block. A near-set run reuses the tuple of the last validated read:
the views themselves validate the tuple hash, so a changed tuple reverts and is re-read from the
inventory (storage-validated), never evaluated stale.

Onset: the first checkpoint block at which this check observed `isLiquidatable` (persisted in
`m3_zone`). Age = block - onset. The onset is never estimated backwards from the balance (a burn-rate
increase can make a cluster liquidatable at once, which a backwards estimate would date days earlier):
after downtime or at the very first sweep the age is a lower bound, and the alert says so.

UNKNOWN, never clean: a cluster with no validated tuple, or whose views revert or return no data, is
not evaluated; a health message (`m3-unknown`, re-sent every health window) names it, and an open
incident of it is re-reported at its last severity in a reorg run (kept open). Every full sweep also
checks that the inventory is complete: the validators of its active, validated ETH clusters must add up
to `getNetworkValidatorsCount()` (a cluster missing from both the API and the logs would otherwise never be
evaluated); a mismatch is the same health message. Transport errors raise.

Absence from the inventory is not evidence (QA cp2a-1 H1): a cluster this check tracks (`m3_zone`, rows
rewound in this run, or an open incident's subject) that the inventory does not list with a validated tuple
is read from storage at the evaluated block with its persisted tuple (`m3_zone.tuple`, the last tuple the
views accepted) and its stored logs as candidates. It leaves the zone only on positive on-chain proof: the
views on a storage-validated tuple say not liquidatable (or it is inactive, has no validators), or both
cluster slots are empty, or the stored version is not ETH. Otherwise it is UNKNOWN (kept, health).
`m3_tuples` keeps the last tuples the check validated for every cluster it tracked, re-checked or had in its
near set (with owner and operators): candidates only, never trusted without the storage hash, so they are
not rewound; a reorg that removes the latest change of a cluster the API omits still finds its tuple.

Reorg rule (README §6.2), table `m3_zone` and state `last_full_block`, identical for depth 1 and N:
rows whose `onset_block` is above the ancestor A are deleted (their onset was observed on a deleted
block) and `zero_block` above A is cleared; the reorg run is a full sweep over the inventory, every
`m3_zone` row and `ctx.reorg_keys`, so every persisted or opened key gets a finding, a resolve=True,
or (UNKNOWN) a re-report. It also re-checks every cluster of `m3_tuples` observed above A: one resolved on
a deleted block (e.g. by a liquidation the reorg removed) is found again even if the API omits it. A key found again opens/keeps the incident with its onset re-observed at
the reorg run's block (age restarts, a lower severity is recorded silently). `on_reorg` drops the
in-memory near set and tuples; a restart before the reorg run replays it (durable marker).

Lowered checkpoint (QA cp2a-2 H1 shape): a reorg onto a shorter chain, or a node lagging behind the checkpoint
(the framework treats a block the node does not have as not canonical), can put the reorg run's block below
the block at which a tracked cluster was last evaluated (`m3_zone.last_block`). An evaluation there is about an
earlier block, not about that evidence: the row is kept with its onset and zero block (`hold_block` = the
evidence block) and its incident is neither resolved nor lowered until a run at or above `hold_block`
evaluates it (then the normal rules apply, with the kept onset). While held, the cluster is in the near set (a
run at every checkpoint block).
"""

from __future__ import annotations

import json
import logging

from checks.base import Check, Ctx, Every, Finding, NearSetEveryBlock, OnEvents
from checks.m3_views import (
    ClusterView,
    blocks_until_liquidatable,
    ensure_columns,
    evaluate,
    inventory_gap,
    short,
    stored_cluster,
    tuple_from_json,
    tuple_json,
)
from checks.registry import register
from eb_events import CLUSTER_KINDS

log = logging.getLogger("eb-reconcile.m3")

SCHEMA = """
CREATE TABLE IF NOT EXISTS m3_zone (
  cluster_id TEXT PRIMARY KEY, owner TEXT NOT NULL, operator_ids TEXT NOT NULL,
  onset_block INTEGER NOT NULL, not_liquidatable_before INTEGER, zero_block INTEGER,
  last_block INTEGER NOT NULL, severity TEXT NOT NULL, balance TEXT NOT NULL, burn_rate TEXT NOT NULL,
  tuple TEXT, hold_block INTEGER
);
CREATE TABLE IF NOT EXISTS m3_tuples (
  cluster_id TEXT NOT NULL, owner TEXT NOT NULL, operator_ids TEXT NOT NULL, tuple TEXT NOT NULL,
  block INTEGER NOT NULL, PRIMARY KEY (cluster_id, tuple)
);
"""
HISTORY = 8  # validated tuples kept per cluster (candidates after a reorg removes the latest change)

WEI = 10**18
RANK = {"info": 0, "warn": 1, "page": 2}


def eth(wei: int) -> str:
    whole, rem = divmod(wei, WEI)
    return f"{whole}" if rem == 0 else f"{whole}.{rem:018d}".rstrip("0")


@register
class M3LiquidationZone(Check):
    id = "m3"
    defaults = {"n_blocks": 50, "n2_blocks": 7_200, "full_sweep_interval_s": 600, "near_blocks": 300,
                "pre_alert_blocks": 7_200}
    schema = SCHEMA
    events = {"OperatorFeeExecuted": "OperatorFeeExecuted(address,uint64,uint256,uint256)"}

    def __init__(self, config: dict, settings: dict):
        super().__init__(config, settings)
        if not settings.get("views"):
            raise SystemExit("checks.m3 needs the SSVNetworkViews address in the top-level 'views' setting")
        c = config
        if not (1 <= c["n_blocks"] <= c["n2_blocks"] and c["full_sweep_interval_s"] > 0 and c["near_blocks"] >= 0
                and c["pre_alert_blocks"] >= 0):
            raise SystemExit(f"checks.m3: need 1 <= n_blocks <= n2_blocks, full_sweep_interval_s > 0, near_blocks >= "
                             f"0, pre_alert_blocks >= 0; got {c}")
        self.views, self.proxy = settings["views"].lower(), settings["proxy"].lower()
        self.schedule = (Every(config["full_sweep_interval_s"]),
                         OnEvents(CLUSTER_KINDS | {"RootCommitted", "OperatorFeeExecuted"}), NearSetEveryBlock())
        self.near_set: frozenset = frozenset()
        self.tuples: dict = {}  # cluster id -> ClusterInfo of the last validated read (near-set runs)
        self.last_full: float | None = None
        self.rewound: dict[str, dict] = {}  # cluster id -> its m3_zone row deleted by this run's rewind

    def on_reorg(self, ancestor: int) -> None:
        self.near_set, self.tuples, self.last_full = frozenset(), {}, None

    def prepare(self, ctx: Ctx) -> None:
        ensure_columns(ctx.db.conn, "m3_zone", {"tuple": "TEXT", "hold_block": "INTEGER"})
        self.threshold_period = ctx.params.liquidation_threshold_period
        self.minimum_collateral = ctx.params.minimum_liquidation_collateral

    # ---- persisted state

    def rows(self, ctx: Ctx) -> dict:
        return {r["cluster_id"]: dict(r) for r in ctx.db.conn.execute("SELECT * FROM m3_zone")}

    def rewind(self, ctx: Ctx, ancestor: int, block: int) -> dict[str, dict]:
        """Drop what was derived from blocks above the ancestor; returns the rows whose onset was there (their
        identity and tuple stay candidates for this run: a tuple is used only if storage validates it). A row last
        evaluated above `block` (the checkpoint was lowered below its evidence) is held instead, onset and zero
        block kept."""
        ctx.db.conn.execute("UPDATE m3_zone SET hold_block = MAX(COALESCE(hold_block, 0), last_block) "
                            "WHERE last_block > ?", (block,))
        held = "COALESCE(hold_block, 0) <= ?"  # rows not held (hold_block at or below this run's block)
        gone = {r["cluster_id"]: dict(r) for r in
                ctx.db.conn.execute(f"SELECT * FROM m3_zone WHERE onset_block > ? AND {held}", (ancestor, block))}
        ctx.db.conn.execute(f"DELETE FROM m3_zone WHERE onset_block > ? AND {held}", (ancestor, block))
        ctx.db.conn.execute(f"UPDATE m3_zone SET zero_block = NULL WHERE zero_block > ? AND {held}", (ancestor, block))
        ctx.db.conn.execute("UPDATE m3_zone SET not_liquidatable_before = NULL WHERE not_liquidatable_before > ?",
                            (ancestor,))
        if (ctx.state("last_full_block") or -1) > ancestor:
            ctx.set_state("last_full_block", None)
        pre = ctx.state("pre_alert")
        if pre and pre.get("block", -1) > ancestor:
            ctx.set_state("pre_alert", None)
        return gone

    # ---- run

    def run(self, ctx: Ctx, block: int) -> list[Finding]:
        reorg = ctx.reorg_ancestor is not None
        self.rewound = self.rewind(ctx, ctx.reorg_ancestor, block) if reorg else {}
        touched, root = set(), False
        for ev in ctx.events():
            if ev.kind in ("RootCommitted", "OperatorFeeExecuted"):
                root = True
            elif ev.cluster_id:
                touched.add("0x" + ev.cluster_id.hex())
        now = ctx.clock()
        full = (reorg or root or self.last_full is None or bool(ctx.reasons & {"params", "force", "reorg"})
                or now - self.last_full >= self.config["full_sweep_interval_s"])
        tracked = self.rows(ctx)
        must_report = set(tracked) | set(ctx.reorg_keys)  # a finding each (kept, resolved or raise)
        recheck = set(must_report)  # evaluated even when the inventory omits them
        if reorg:
            recheck |= {r[0] for r in ctx.db.conn.execute("SELECT DISTINCT cluster_id FROM m3_tuples WHERE block > ?",
                                                          (ctx.reorg_ancestor,))}

        def fetch(cids) -> dict:
            inv = ctx.inventory(block)
            got = {cid: inv.clusters.get(cid) for cid in cids}
            got.update(self.lookup(ctx, block, [cid for cid in cids if cid in recheck], inv, tracked))
            return got

        gap = None
        if full:
            inv = ctx.inventory(block)
            infos = {cid: c for cid, c in inv.clusters.items() if c.version == "ETH"}
            gap = inventory_gap(ctx.rpc, self.views, infos.values())
            infos.update(self.lookup(ctx, block, recheck, inv, tracked))
            cached = set()
        else:
            wanted = set(self.near_set) | touched | recheck
            infos = {cid: self.tuples.get(cid) for cid in wanted}
            fresh = {cid for cid in wanted if cid in touched or infos[cid] is None}
            if fresh:
                infos.update(fetch(fresh))
            cached = set(wanted) - fresh

        status, views = self.evaluate(ctx, infos)
        stale = [cid for cid in cached if status.get(cid, "").startswith("unknown")]
        if stale:  # a cached tuple that changed reverts in the views: re-read it from storage once
            infos.update(fetch(stale))
            st2, v2 = self.evaluate(ctx, {cid: infos[cid] for cid in stale})
            status.update(st2)
            views.update(v2)

        findings: list[Finding] = []
        near, pre_alert, unknown_rows = set(), [], []
        last_full_block = ctx.state("last_full_block")
        for cid, info in sorted(infos.items()):
            view = views.get(cid)
            if view is not None:
                self.tuples[cid] = info
                if view.liquidatable or view.balance == 0:  # computed zero pages even if not liquidatable
                    findings.append(self.liquidatable(ctx, block, info, view, tracked.get(cid), last_full_block))
                    near.add(cid)
                    continue
                ahead = blocks_until_liquidatable(view, self.threshold_period, self.minimum_collateral)
                if ahead is not None and ahead <= self.config["near_blocks"]:
                    near.add(cid)
                if full and ahead is not None and ahead <= self.config["pre_alert_blocks"]:
                    pre_alert.append({"cluster_id": cid, "owner": info.owner, "operator_ids": list(info.operator_ids),
                                      "blocks": ahead, "at_block": block + ahead, "balance": str(view.balance),
                                      "burn_rate": str(view.burn_rate)})
                if cid in recheck:
                    findings.append(self.leave(ctx, block, cid, tracked.get(cid), "is no longer liquidatable"))
                    if self.held(tracked.get(cid), block):
                        near.add(cid)
                continue
            st = status[cid]
            if st.startswith("unknown"):
                unknown_rows.append((cid, st))
                if cid in tracked:
                    near.add(cid)
                if cid in must_report:
                    findings.append(self.unknown(ctx, block, cid, tracked.get(cid), st))
            elif cid in recheck:
                findings.append(self.leave(ctx, block, cid, tracked.get(cid), st))
                if self.held(tracked.get(cid), block):
                    near.add(cid)
        for cid, info in infos.items():
            if (info is not None and info.valid and not status.get(cid, "").startswith("unknown")
                    and (cid in recheck or cid in near)):
                self.remember(ctx, info, block)

        if gap:
            unknown_rows.append(("inventory", "unknown: " + gap))
        if unknown_rows:
            text = (f"M3 cannot evaluate {len(unknown_rows)} ETH cluster(s) at block {block} (not treated as clean):\n"
                    + "\n".join(f"- {cid}: {st}"[:300] for cid, st in unknown_rows[:20]))
            ctx.health("m3-unknown", text, dedup="m3-unknown:" + ",".join(c for c, _ in unknown_rows), realert=True)
        if full:
            self.near_set = frozenset(near)
            self.last_full = now
            ctx.set_state("last_full_block", block)
            pre_alert.sort(key=lambda p: p["blocks"])
            ctx.set_state("pre_alert", {"block": block, "clusters": pre_alert})
        else:
            evaluated = set(infos)
            self.near_set = frozenset((set(self.near_set) - evaluated) | near)
        return findings

    def remember(self, ctx: Ctx, info, block: int) -> None:
        cid, t = info.cluster_id, tuple_json(info.cluster)
        ctx.db.conn.execute("INSERT INTO m3_tuples VALUES (?,?,?,?,?) ON CONFLICT (cluster_id, tuple) DO UPDATE SET "
                            "block = excluded.block", (cid, info.owner, json.dumps(list(info.operator_ids)), t, block))
        ctx.db.conn.execute("DELETE FROM m3_tuples WHERE cluster_id=? AND tuple NOT IN (SELECT tuple FROM m3_tuples "
                            "WHERE cluster_id=? ORDER BY block DESC LIMIT ?)", (cid, cid, HISTORY))

    def identity(self, ctx: Ctx, cid: str, tracked: dict) -> tuple[str, list, list] | None:
        """(owner, operator ids, candidate tuples) of a cluster this check tracks, re-checks or has an open incident
        on: the persisted row's tuple first, then the validated tuples of `m3_tuples`, newest first."""
        history = ctx.db.conn.execute("SELECT owner, operator_ids, tuple FROM m3_tuples WHERE cluster_id=? "
                                      "ORDER BY block DESC", (cid,)).fetchall()
        tuples = [tuple_from_json(h["tuple"]) for h in history]
        row = tracked.get(cid) or self.rewound.get(cid)
        if row is not None:
            return row["owner"], json.loads(row["operator_ids"]), [tuple_from_json(row.get("tuple"))] + tuples
        if history:
            return history[0]["owner"], json.loads(history[0]["operator_ids"]), tuples
        inc = ctx.db.conn.execute("SELECT subject FROM incidents WHERE check_id=? AND key=? AND state='open'",
                                  (self.id, cid)).fetchone()
        subject = json.loads(inc[0]) if inc and inc[0] else {}
        if subject.get("owner") and subject.get("operator_ids") is not None:
            return subject["owner"], subject["operator_ids"], []
        return None

    def lookup(self, ctx: Ctx, block: int, cids, inv, tracked: dict) -> dict:
        """ClusterInfo of the clusters that must be reported: the inventory's entry when it has a validated tuple
        or proves the cluster absent from storage; otherwise storage at `block`, with the persisted tuple and the
        stored logs' tuples as candidates. An inventory that omits a cluster proves nothing (QA cp2a-1 H1)."""
        out = {}
        for cid in cids:
            info = inv.clusters.get(cid)
            if info is not None and (info.valid or info.version is None):
                out[cid] = info
                continue
            ident = self.identity(ctx, cid, tracked)
            if ident is None:
                out[cid] = info
                continue
            owner, ops, persisted = ident
            logs = [("logs", ev.cluster) for ev in reversed(ctx.db.cluster_events(cid[2:], block))
                    if ev.cluster is not None][:1]
            out[cid] = stored_cluster(ctx.rpc, self.proxy, owner, ops, [("persisted", t) for t in persisted] + logs,
                                      block)
        return out

    def evaluate(self, ctx: Ctx, infos: dict) -> tuple[dict, dict]:
        """({cluster id: status text}, {cluster id: ClusterView}); a status starting with "unknown" is UNKNOWN."""
        status, targets = {}, []
        for cid, info in infos.items():
            if info is None:
                status[cid] = "unknown: not listed by the inventory, and no persisted identity to read it by"
            elif info.version is None:
                status[cid] = "is not in storage (absent)"
            elif info.version != "ETH":
                status[cid] = "is not an ETH cluster"
            elif not info.valid:
                status[cid] = "unknown: no candidate tuple validates against storage"
            elif not info.cluster.active:
                status[cid] = "is inactive (liquidated)"
            elif info.cluster.validator_count == 0:
                status[cid] = "has no validators"
            else:
                targets.append(info)
        views, unknown = evaluate(ctx.rpc, self.views, targets)
        status.update({cid: "unknown: " + reason for cid, reason in unknown.items()})
        return status, views

    # ---- findings

    def liquidatable(self, ctx: Ctx, block: int, info, view: ClusterView, row: dict | None,
                     last_full_block: int | None) -> Finding:
        cid = info.cluster_id
        if row is None:
            row = {"onset_block": block, "zero_block": None, "not_liquidatable_before": last_full_block}
        hold = row.get("hold_block") if self.held(row, block) else None
        zero = view.balance == 0
        zero_block = (row["zero_block"] or block) if zero else None
        age = block - row["onset_block"]
        n, n2 = self.config["n_blocks"], self.config["n2_blocks"]
        if zero:
            sev, title = "page", f"M3 cluster at computed zero balance: {short(cid)} (phantom accrual)"
        elif age >= n2:
            sev, title = "page", f"M3 cluster liquidatable for {age} blocks (≥ N2 = {n2}): {short(cid)}"
        elif age >= n:
            sev, title = "warn", f"M3 cluster still liquidatable after {age} blocks (≥ N = {n}): {short(cid)}"
        else:
            sev, title = "info", f"M3 cluster liquidatable (onset at block {row['onset_block']}): {short(cid)}"
        if hold is not None and RANK[row["severity"]] > RANK[sev]:  # a lowered checkpoint never lowers it
            sev, title = row["severity"], (f"M3 cluster liquidatable ({row['severity']} kept until block {hold} is "
                                           f"re-evaluated): {short(cid)}")
        before = row["not_liquidatable_before"]
        onset_note = (f"first observed liquidatable at block {row['onset_block']} "
                      + (f"(not liquidatable at the sweep of block {before})" if before is not None else
                         "(no earlier evaluation: the onset is at or before this block, so the age is a lower bound)"))
        threshold = max(self.threshold_period * view.burn_rate, self.minimum_collateral)
        body = (f"Cluster {cid}\nOwner {info.owner}, operators {list(info.operator_ids)}, "
                f"{info.cluster.validator_count} validator(s).\n"
                f"View-computed balance {eth(view.balance)} ETH, burn {view.burn_rate} wei/block, liquidation "
                f"threshold {eth(threshold)} ETH.\nLiquidatable for {age} block(s): {onset_note}."
                + (f"\nComputed zero since block {zero_block}: operator and DAO earnings accrue without backing "
                   f"(feeds M4)." if zero else "")
                + (f"\nThe checkpoint is below block {hold}, where this incident was last evaluated (a reorg onto a "
                   f"shorter chain, or a node behind the checkpoint): its severity is kept until that block is "
                   f"re-evaluated." if hold is not None else "")
                + "\nRunbook: check that our keeper attempted the liquidation; if not, liquidate manually.")
        ctx.db.conn.execute(
            "INSERT OR REPLACE INTO m3_zone (cluster_id, owner, operator_ids, onset_block, not_liquidatable_before, "
            "zero_block, last_block, severity, balance, burn_rate, tuple, hold_block) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, info.owner, json.dumps(list(info.operator_ids)), row["onset_block"], before, zero_block,
             block if hold is None else row["last_block"], sev, str(view.balance), str(view.burn_rate),
             tuple_json(info.cluster), hold))
        return Finding(
            check=self.id, key=cid, severity=sev, title=title, body=body,
            subject={"cluster_id": cid, "owner": info.owner, "operator_ids": list(info.operator_ids)},
            evidence={"block": block, "onset_block": row["onset_block"], "age_blocks": age, "zero_block": zero_block,
                      "not_liquidatable_before": before, "balance_wei": str(view.balance),
                      "burn_rate_wei_per_block": str(view.burn_rate), "threshold_wei": str(threshold),
                      "n_blocks": n, "n2_blocks": n2, "validators": info.cluster.validator_count, "hold_block": hold})

    @staticmethod
    def held(row: dict | None, block: int) -> bool:
        """The row's evidence is above `block` (a lowered checkpoint): not resolved or lowered before it."""
        return bool(row and row.get("hold_block") and block < row["hold_block"])

    def leave(self, ctx: Ctx, block: int, cid: str, row: dict | None, why: str) -> Finding:
        """Out of the zone at `block`: resolved, unless the row is held (its evidence is above `block`)."""
        if not self.held(row, block):
            return self.resolved(ctx, block, cid, row, why)
        return Finding(check=self.id, key=cid, severity=row["severity"],
                       title=f"M3 cluster liquidatable, evidence above the checkpoint: {short(cid)}",
                       body=(f"Cluster {cid} was liquidatable since block {row['onset_block']} (last evaluated at "
                             f"block {row['hold_block']}). The checkpoint is now block {block}, below that evidence (a "
                             f"reorg onto a shorter chain, or a node behind the checkpoint), where the cluster {why}: "
                             f"that is not proof about block {row['hold_block']}. Kept open until it is re-evaluated."),
                       evidence={"block": block, "status": why, "onset_block": row["onset_block"],
                                 "hold_block": row["hold_block"]})

    def resolved(self, ctx: Ctx, block: int, cid: str, row: dict | None, why: str) -> Finding:
        ctx.db.conn.execute("DELETE FROM m3_zone WHERE cluster_id=?", (cid,))
        onset = f" (liquidatable since block {row['onset_block']})" if row else ""
        if ctx.reorg_ancestor is not None and (cid in self.rewound or row is None):
            why += (f"; its liquidatable state was observed on blocks after {ctx.reorg_ancestor}, which were replaced "
                    f"by a chain reorganisation")
        return Finding(check=self.id, key=cid, severity="info", resolve=True,
                       title=f"M3 cluster left the liquidation zone: {short(cid)}",
                       body=f"Cluster {cid}{onset} {why} (re-evaluated at block {block}).",
                       evidence={"block": block, "status": why})

    def unknown(self, ctx: Ctx, block: int, cid: str, row: dict | None, why: str) -> Finding:
        if row is None:  # an open incident without a row: nothing to keep it at; re-evaluate next step
            raise RuntimeError(f"m3: cluster {cid} has an open incident but cannot be evaluated at block {block}: "
                               f"{why}")
        return Finding(check=self.id, key=cid, severity=row["severity"],
                       title=f"M3 cluster liquidatable, current state UNKNOWN: {short(cid)}",
                       body=(f"Cluster {cid} was liquidatable since block {row['onset_block']} (last evaluated at "
                             f"block {row['last_block']}); it cannot be evaluated at block {block}: {why}. Kept open."),
                       evidence={"block": block, "status": why, "onset_block": row["onset_block"],
                                 "last_block": row["last_block"]})

    # ---- digest

    def digest(self, ctx: Ctx) -> list[str]:
        lines = []
        rows = sorted(self.rows(ctx).values(), key=lambda r: r["onset_block"])
        if rows:
            lines.append(f"Liquidatable ETH clusters: {len(rows)}")
            lines += [f"- {r['cluster_id']} since block {r['onset_block']} ({r['severity']}), balance "
                      f"{eth(int(r['balance']))} ETH" for r in rows[:20]]
        pre = ctx.state("pre_alert") or {}
        if pre.get("clusters"):
            lines.append(f"Projected to become liquidatable within {self.config['pre_alert_blocks']} blocks "
                         f"(sweep of block {pre['block']}): {len(pre['clusters'])}")
            lines += [f"- {p['cluster_id']} in {p['blocks']} blocks (≈ block {p['at_block']})"
                      for p in pre["clusters"][:20]]
        return lines
