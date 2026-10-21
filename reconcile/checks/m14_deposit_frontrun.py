"""M14 — Deposit front-running of liquidations (KI #3, #94187; spec M14, V2a.7).

`deposit` is permissionless: a 1-wei deposit changes the cluster tuple, so a pending liquidation carrying
the old tuple reverts (IncorrectClusterState). Sustained, this keeps a cluster alive past zero (feeds M3/M4).

  a reverted liquidate / liquidateSSV (failed-tx inbox) on a liquidatable cluster, with a ClusterDeposited
  for that cluster in the same block (before the failed transaction) or the previous block whose value is
  below the cluster's burn for one block                                                   info (a "hit")
  ≥ `repeat_count` (3) hits on the same cluster within `window_blocks` (7,200 ≈ 24 h)           warn
  a cluster with a hit in the window is at computed zero (active, validators, getBalance == 0)  page
  no hit in the last `window_blocks` and not at zero                                   resolves the incident

One incident per cluster (key = cluster id). While it is info, each new hit re-sends it (revision = the
hit's transaction); warn and page are sent on escalation only (later hits are recorded and listed in the
evidence). Inputs: the inbox entry (gated: canonical, at or below the checkpoint, README §5.2), the failed
transaction's calldata (`eth_getTransactionByHash`; its `transactionIndex` orders it against a same-block
deposit — when the node does not report it the deposit is assumed to precede, `ordering: "unknown"`), the
stored `ClusterDeposited` logs, and `isLiquidatable` / `getBurnRate` at the deposit's block on the tuple of
the cluster's last log in that block (the views validate the tuple's hash; earlier logs of the block are
tried when the last one does not validate). The computed-zero test reads `getBalance` at the checkpoint on
the storage-validated tuple (`ctx.inventory`). Runs `OnInbox` and every `sweep_interval_s` (zero, window).

UNKNOWN, never clean: an entry whose transaction or calldata cannot be read, or whose deposit-block views
revert for every candidate tuple, is not a hit and not dismissed: `m14-unknown` health names it (re-sent
every health window). An open cluster whose zero test cannot be evaluated keeps its incident at its
current severity. Transport errors raise (the run is retried, the inbox cursor does not advance).

Reorg rule (README §6.2), tables `m14_hits` and `m14_open`, identical for depth 1 and N: hits whose failed
transaction's block is above the ancestor A are deleted (a deposit is at or before its failed transaction,
so no surviving hit depends on a deleted block); the inbox rows above A that are still canonical are
re-derived (`canonical = 1`, re-confirmed in the same step; delivered entries are not delivered again)
against the rescanned logs of the new chain; then every cluster of `m14_open`, of the remaining hits in
the window and of `ctx.reorg_keys` is re-evaluated: a finding, or resolve=True when no hit remains.
"""

from __future__ import annotations

import json

from checks.base import Check, Ctx, Every, Finding, OnInbox
from checks.m3_views import GET_BALANCE, LIQUIDATE, cluster_calls, decode_cluster_args, encode_cluster_args, short, word
from checks.registry import register
from eb_storage import cluster_id
from inbox import InboxEntry
from rpc import RpcError

SCHEMA = """
CREATE TABLE IF NOT EXISTS m14_hits (
  tx_hash TEXT NOT NULL, block_hash TEXT NOT NULL, block INTEGER NOT NULL, tx_index INTEGER,
  cluster_id TEXT NOT NULL, owner TEXT NOT NULL, operator_ids TEXT NOT NULL, function TEXT NOT NULL,
  revert_name TEXT, deposit_tx TEXT NOT NULL, deposit_block INTEGER NOT NULL, deposit_log_index INTEGER NOT NULL,
  value TEXT NOT NULL, burn_rate TEXT NOT NULL, ordering TEXT NOT NULL,
  PRIMARY KEY (tx_hash, block_hash)
);
CREATE INDEX IF NOT EXISTS m14_hits_cluster ON m14_hits (cluster_id, block);
CREATE TABLE IF NOT EXISTS m14_open (cluster_id TEXT PRIMARY KEY, severity TEXT NOT NULL, block INTEGER NOT NULL);
"""

SELECTORS = {"0x" + s.hex(): name for name, s in LIQUIDATE.items()}


@register
class M14DepositFrontrun(Check):
    id = "m14"
    defaults = {"window_blocks": 7_200, "repeat_count": 3, "sweep_interval_s": 600}
    schema = SCHEMA

    def __init__(self, config: dict, settings: dict):
        super().__init__(config, settings)
        if not settings.get("views"):
            raise SystemExit("checks.m14 needs the SSVNetworkViews address in the top-level 'views' setting")
        self.views, self.proxy = settings["views"].lower(), settings["proxy"].lower()
        self.schedule = (OnInbox(), Every(config["sweep_interval_s"]))

    # ---- hits

    def liquidation(self, e) -> str | None:
        """The liquidation function of an inbox entry, or None for any other failed transaction."""
        if (e.target or "").lower() != self.proxy:
            return None
        name = SELECTORS.get((e.selector or "").lower())
        if name is None and e.function:
            name = e.function.split("(", 1)[0] if e.function.split("(", 1)[0] in LIQUIDATE else None
        return name

    def derive(self, ctx: Ctx, e, unknown: list) -> None:
        """Record the hit of one failed liquidation, if any (idempotent on (tx_hash, block_hash))."""
        fn = self.liquidation(e)
        if fn is None:
            return
        tx = ctx.rpc.transaction(e.tx_hash)
        data = (tx or {}).get("input") or ""
        try:
            if not data.lower().startswith("0x" + LIQUIDATE[fn].hex()):
                raise ValueError("transaction input is not the inbox's liquidation call")
            owner, ops, _ = decode_cluster_args(bytes.fromhex(data[10:]))
        except ValueError as exc:
            unknown.append(f"{e.tx_hash} (block {e.block}): calldata unavailable ({exc})")
            return
        cid = cluster_id(owner, ops).hex()
        tx_index = int(tx["transactionIndex"], 16) if tx.get("transactionIndex") is not None else None
        events = [ev for ev in ctx.db.cluster_events(cid, e.block) if ev.block >= e.block - 1]
        deposits = [ev for ev in events if ev.kind == "ClusterDeposited"
                    and (ev.block < e.block or tx_index is None or ev.tx_index < tx_index)]
        for dep in reversed(deposits):  # the latest deposit before the failed transaction first
            same_block = [ev for ev in events if ev.block == dep.block and ev.cluster is not None]
            view, why = None, "no tuple"
            for cand in reversed(same_block):  # the tuple at the end of the deposit's block
                try:
                    out = ctx.rpc.call_many(cluster_calls(self.views, owner, ops, cand.cluster), dep.block)
                    view = (word(out[0], "isLiquidatable"), word(out[2], "getBurnRate"))
                    break
                except RpcError as exc:
                    if exc.transport:
                        raise
                    why = f"views at block {dep.block} reverted ({exc.data or exc})"
                except ValueError as exc:
                    why = str(exc)
            if view is None:
                unknown.append(f"{e.tx_hash} (block {e.block}): deposit {dep.tx_hash} cannot be evaluated: {why}")
                continue
            liquidatable, burn = view
            value = int(dep.extra.get("value", 0))
            if liquidatable and value < burn:
                ctx.db.conn.execute(
                    "INSERT OR IGNORE INTO m14_hits VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (e.tx_hash, e.block_hash, e.block, tx_index, "0x" + cid, owner, json.dumps(list(ops)), fn,
                     e.revert_name, dep.tx_hash, dep.block, dep.log_index, str(value), str(burn),
                     "unknown" if tx_index is None and dep.block == e.block else "before"))
                return

    # ---- run

    def run(self, ctx: Ctx, block: int) -> list[Finding]:
        unknown: list[str] = []
        A = ctx.reorg_ancestor
        if A is not None:
            ctx.db.conn.execute("DELETE FROM m14_hits WHERE block > ?", (A,))
            for r in ctx.db.conn.execute("SELECT * FROM inbox WHERE canonical = 1 AND resolved = 1 AND block > ? "
                                         "ORDER BY id", (A,)).fetchall():
                self.derive(ctx, InboxEntry.from_row(r), unknown)
        for e in ctx.inbox:
            self.derive(ctx, e, unknown)

        window = self.config["window_blocks"]
        keys = {r[0] for r in ctx.db.conn.execute("SELECT cluster_id FROM m14_open")}
        keys |= {r[0] for r in ctx.db.conn.execute("SELECT DISTINCT cluster_id FROM m14_hits WHERE block > ?",
                                                   (block - window,))}
        keys |= set(ctx.reorg_keys)
        findings = [self.evaluate(ctx, block, cid, unknown) for cid in sorted(keys)]
        if unknown:
            ctx.health("m14-unknown", "M14 cannot evaluate (not treated as clean):\n- "
                       + "\n- ".join(u[:300] for u in unknown[:20]), dedup="m14-unknown:" + "|".join(unknown)[:2000],
                       realert=True)
        return findings

    def evaluate(self, ctx: Ctx, block: int, cid: str, unknown: list) -> Finding:
        window, repeat = self.config["window_blocks"], self.config["repeat_count"]
        hits = [dict(r) for r in ctx.db.conn.execute(
            "SELECT * FROM m14_hits WHERE cluster_id=? ORDER BY block, tx_index", (cid,))]
        last = hits[-1] if hits else None
        if last is None or block - last["block"] >= window:
            ctx.db.conn.execute("DELETE FROM m14_open WHERE cluster_id=?", (cid,))
            why = "no front-running hit remains on the canonical chain" if last is None else \
                f"no front-running hit for {block - last['block']} blocks (≥ {window})"
            return Finding(check=self.id, key=cid, severity="info", resolve=True,
                           title=f"M14 deposit front-running ended: {short(cid)}",
                           body=f"Cluster {cid}: {why} (block {block}).", evidence={"block": block})
        recent = [h for h in hits if h["block"] > last["block"] - window]
        zero, zero_note = self.at_zero(ctx, block, cid)
        prev = ctx.db.conn.execute("SELECT severity FROM m14_open WHERE cluster_id=?", (cid,)).fetchone()
        if zero is None:
            unknown.append(f"cluster {cid}: computed-zero test unavailable at block {block} ({zero_note})")
        if zero:
            sev = "page"
        elif zero is None and prev and prev[0] == "page":
            sev = "page"  # cannot re-evaluate: keep the page
        elif len(recent) >= repeat:
            sev = "warn"
        else:
            sev = "info"
        ctx.db.conn.execute("INSERT OR REPLACE INTO m14_open VALUES (?,?,?)", (cid, sev, block))
        owner, ops = last["owner"], json.loads(last["operator_ids"])
        lines = [f"- failed {h['function']} {h['tx_hash']} (block {h['block']}) after deposit {h['deposit_tx']} "
                 f"of {h['value']} wei (block {h['deposit_block']}, burn {h['burn_rate']} wei/block, "
                 f"{'same block' if h['deposit_block'] == h['block'] else 'previous block'}"
                 f"{', order unknown' if h['ordering'] == 'unknown' else ''})" for h in recent[-10:]]
        titles = {"info": f"M14 liquidation front-run by a dust deposit: {short(cid)}",
                  "warn": f"M14 repeated deposit front-running ({len(recent)} in {window} blocks): {short(cid)}",
                  "page": f"M14 front-run cluster at computed zero: {short(cid)}"}
        body = (f"Cluster {cid} (owner {owner}, operators {ops}): {len(recent)} liquidation(s) reverted after a "
                f"deposit below one block of burn within {window} blocks.\n" + "\n".join(lines)
                + (f"\n{zero_note}" if zero_note else "")
                + "\nRunbook: liquidate with the current tuple (re-read it at the pending block); if the deposits "
                  "continue, escalate (KI #3).")
        return Finding(check=self.id, key=cid, severity=sev, title=titles[sev], body=body,
                       subject={"cluster_id": cid, "owner": owner, "operator_ids": ops},
                       evidence={"block": block, "hits": [h["tx_hash"] for h in recent], "count": len(recent),
                                 "window_blocks": window, "zero": zero},
                       revision=last["tx_hash"] if sev == "info" else None)

    def at_zero(self, ctx: Ctx, block: int, cid: str) -> tuple[bool | None, str]:
        info = ctx.inventory(block).clusters.get(cid)
        if info is None or info.version is None:
            return False, "The cluster is not in storage at the checkpoint."
        if not info.valid:
            return None, "no candidate tuple validates against storage"
        if not info.cluster.active or info.cluster.validator_count == 0:
            return False, ("The cluster is liquidated." if not info.cluster.active else "")
        try:
            bal = word(ctx.rpc.call(self.views, GET_BALANCE + encode_cluster_args(info.owner, info.operator_ids,
                                                                                info.cluster)), "getBalance")
        except RpcError as exc:
            if exc.transport:
                raise
            return None, f"getBalance reverted ({exc.data or exc})"
        except ValueError as exc:
            return None, str(exc)
        return bal == 0, (f"Computed zero at block {block}: phantom accrual has started (M3/M4)." if bal == 0 else
                          f"View-computed balance {bal} wei at block {block}.")
