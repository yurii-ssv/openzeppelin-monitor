"""M14 — Deposit front-running of liquidations (KI #3, #94187; spec M14, V2a.7).

`deposit` is permissionless: a 1-wei deposit changes the cluster tuple, so a pending liquidation carrying
the old tuple reverts (IncorrectClusterState). Sustained, this keeps a cluster alive past zero (feeds M3/M4).

  a reverted liquidate / liquidateSSV (failed-tx inbox) on a cluster that is liquidatable at the failed
  transaction's position, with a ClusterDeposited for that cluster in the previous block or earlier in the
  same block whose value is below the cluster's burn for one block                         info (a "hit")
  ≥ `repeat_count` (3) hits on the same cluster within `window_s` (86,400) of chain time        warn
  a cluster with a hit in the window is at computed zero (active, validators, balance == 0)     page
  no hit in the window (and nothing UNKNOWN that could be one)                         resolves the incident

One incident per cluster (key = cluster id). While it is info, each new hit re-sends it (revision = the
hit's transaction); warn and page are sent on escalation only (later hits are recorded and listed in the
evidence); a lower severity (hits aged out of the window) is recorded silently.

Legacy SSV clusters (`liquidateSSV`) use the `…SSV` views throughout: `isLiquidatableSSV`, `getBurnRateSSV`
(SSV wei per block: the deposit value is compared in SSV units) and `getBalanceSSV` for the zero test; the
version comes from storage (`clusters` vs `ethClusters` slot) at the evaluated block, and a liquidation whose
function does not match the stored version reverted for its version, not for a deposit (no hit). At the pin
`deposit` is ETH-only, so a legacy hit needs a ClusterDeposited the pin cannot emit (pre-v2 SSV deposits
emitted the same event); the path is kept because the spec names liquidateSSV.

The window is chain time (QA cp2a-1 M1, M2): a hit's time is its block's header timestamp, and the window is
anchored at the timestamp of the run's checkpoint block (not at the last hit, not at the worker clock, not
at a block count: missed slots make 7,200 blocks longer than 24 h). Hits age out as the checkpoint advances,
so the count can fall below `repeat_count` while hits remain (recorded silently at the lower severity).

State at the failed transaction's position (QA cp2a-1 M3; logs ordered by `transactionIndex`, `logIndex`):
the cluster events of block B (the failed transaction's) are split into those before and after it.
  - nothing of the cluster after it in B: the cluster at the failed transaction is the end-of-B state, so the
    views at B on the last tuple at or before it decide (exact);
  - a later event in B (e.g. a top-up after the attack): the end-of-B state is not the one the liquidation
    met. The views at B−1 on the tuple stored at the end of B−1 (the cluster's last log before B; or, when
    the cluster's first event in B is a deposit, that log's tuple minus its value — `deposit` only adds
    msg.value to the validated tuple; or the inventory's API tuple; the views validate whichever is used)
    give liquidatable₀ and burn₀; if every cluster
    event of B before the failed transaction is a ClusterDeposited (a deposit changes neither indexes, burn
    rate nor threshold) of total Σ, the computed balance at the failed transaction is the B−1 balance + Σ −
    one block of burn: liquidatable₀ ∧ Σ < burn₀ proves liquidatable, ¬liquidatable₀ ∧ Σ ≥ burn₀ proves not;
    anything else is UNKNOWN;
  - a node that reports no `transactionIndex`: one cluster event in B is assumed to precede (`ordering:
    unknown`, as before); several are UNKNOWN.
Operator and network fee changes are not cluster events; one in block B between the deposit and the failed
transaction is not modelled.

Inputs: the inbox entry (gated: canonical, at or below the checkpoint, README §5.2), the failed
transaction's calldata and index (`eth_getTransactionByHash`), the stored cluster logs, the views above,
storage (version, tuple validation), block headers (timestamps; hash-checked against the inbox entry and the
checkpoint). The zero test reads the storage-validated tuple from the inventory or, when the inventory does
not list it (an API page omitting a cluster is not evidence), from storage with the cluster's logs as
candidates. Runs `OnInbox` and every `sweep_interval_s` (zero test, window expiry).

UNKNOWN, never clean, and retried (QA cp2a-1 H2): an entry that cannot be evaluated (transaction or header
unavailable, views revert for the needed tuple, order unknown) is not a hit and not dismissed: it is kept in
`m14_pending` (keyed by inbox id, with its cluster when the calldata was readable), retried at every run and
named in `m14-unknown` health until it is evaluated, leaves the canonical chain, or is older than the window.
While a pending entry in the window could be a hit on an open cluster (its own, or any cluster when the
calldata is unknown), that incident is neither resolved nor lowered. Calldata that is present but is not the
inbox's liquidation call is permanent: health once, no retry. Transport errors raise.

Reorg rule (README §6.2), tables `m14_hits`, `m14_pending`, `m14_open`, identical for depth 1 and N: hits and
pending entries of blocks above the ancestor A are deleted (a deposit is at or before its failed
transaction, so no surviving hit depends on a deleted block); the inbox rows above A that are still canonical
are re-derived (`canonical = 1`, re-confirmed in the same step) against the rescanned logs; if any entry of
that run (re-derived, pending or new) has a transaction that cannot be read, the run **raises** (rolled back,
the framework's reorg marker is kept, retried every step) because it could support any reorg key; one whose
cluster is known but cannot be evaluated is pending and keeps that cluster's key at its severity. Then every cluster of `m14_open`, of the hits in the window and of
`ctx.reorg_keys` is re-evaluated: a finding, or resolve=True only when no hit remains in the window and no
pending entry could be one.
"""

from __future__ import annotations

import json

from checks.base import Check, Ctx, Every, Finding, OnInbox
from checks.m3_views import (
    GET_BALANCE,
    GET_BALANCE_SSV,
    LIQUIDATE,
    block_times,
    cluster_calls,
    decode_cluster_args,
    encode_cluster_args,
    ensure_columns,
    short,
    stored_cluster,
    word,
)
from checks.registry import register
from eb_storage import Cluster, cluster_id, eth_cluster_slot, ssv_cluster_slot
from inbox import InboxEntry
from inventory import api_tuple
from rpc import RpcError

SCHEMA = """
CREATE TABLE IF NOT EXISTS m14_hits (
  tx_hash TEXT NOT NULL, block_hash TEXT NOT NULL, block INTEGER NOT NULL, tx_index INTEGER,
  cluster_id TEXT NOT NULL, owner TEXT NOT NULL, operator_ids TEXT NOT NULL, function TEXT NOT NULL,
  revert_name TEXT, deposit_tx TEXT NOT NULL, deposit_block INTEGER NOT NULL, deposit_log_index INTEGER NOT NULL,
  value TEXT NOT NULL, burn_rate TEXT NOT NULL, ordering TEXT NOT NULL, block_time INTEGER, version TEXT,
  PRIMARY KEY (tx_hash, block_hash)
);
CREATE INDEX IF NOT EXISTS m14_hits_cluster ON m14_hits (cluster_id, block);
CREATE TABLE IF NOT EXISTS m14_pending (
  inbox_id INTEGER PRIMARY KEY, tx_hash TEXT NOT NULL, block INTEGER NOT NULL, block_time INTEGER,
  cluster_id TEXT, reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS m14_open (cluster_id TEXT PRIMARY KEY, severity TEXT NOT NULL, block INTEGER NOT NULL);
"""

SELECTORS = {"0x" + s.hex(): name for name, s in LIQUIDATE.items()}
VERSION = {"liquidate": "ETH", "liquidateSSV": "SSV"}
RANK = {"info": 0, "warn": 1, "page": 2}
HIT_COLUMNS = ("tx_hash", "block_hash", "block", "tx_index", "cluster_id", "owner", "operator_ids", "function",
               "revert_name", "deposit_tx", "deposit_block", "deposit_log_index", "value", "burn_rate", "ordering",
               "block_time", "version")


class Unknown(Exception):
    """An entry that cannot be evaluated now: `cluster` is its cluster id when the calldata was readable."""

    def __init__(self, reason: str, cluster: str | None = None):
        super().__init__(reason)
        self.reason, self.cluster = reason, cluster


@register
class M14DepositFrontrun(Check):
    id = "m14"
    defaults = {"window_s": 86_400, "repeat_count": 3, "sweep_interval_s": 600}
    schema = SCHEMA

    def __init__(self, config: dict, settings: dict):
        super().__init__(config, settings)
        if not settings.get("views"):
            raise SystemExit("checks.m14 needs the SSVNetworkViews address in the top-level 'views' setting")
        self.views, self.proxy = settings["views"].lower(), settings["proxy"].lower()
        self.schedule = (OnInbox(), Every(config["sweep_interval_s"]))

    def prepare(self, ctx: Ctx) -> None:
        ensure_columns(ctx.db.conn, "m14_hits", {"block_time": "INTEGER", "version": "TEXT"})
        ensure_columns(ctx.db.conn, "m14_open", {"owner": "TEXT", "operator_ids": "TEXT", "last_hit": "TEXT"})

    # ---- one inbox entry

    def liquidation(self, e) -> str | None:
        """The liquidation function of an inbox entry, or None for any other failed transaction."""
        if (e.target or "").lower() != self.proxy:
            return None
        name = SELECTORS.get((e.selector or "").lower())
        if name is None and e.function:
            name = e.function.split("(", 1)[0] if e.function.split("(", 1)[0] in LIQUIDATE else None
        return name

    def version_at(self, ctx: Ctx, cid: bytes, block: int) -> str | None:
        eth_word, ssv_word = ctx.rpc.storage(self.proxy, [eth_cluster_slot(cid), ssv_cluster_slot(cid)], block)
        return "ETH" if eth_word else "SSV" if ssv_word else None

    def views_at(self, ctx: Ctx, owner: str, ops, tup, version: str, block: int) -> tuple[bool, int]:
        """(liquidatable, burn rate) of a tuple at the end of `block`; Unknown when the views cannot answer."""
        cid = "0x" + cluster_id(owner, ops).hex()
        try:
            out = ctx.rpc.call_many(cluster_calls(self.views, owner, ops, tup, version), block)
            liq = word(out[0], "isLiquidatable")
            if liq > 1:
                raise ValueError(f"isLiquidatable returned {liq}")
            return bool(liq), word(out[2], "getBurnRate")
        except RpcError as exc:
            if exc.transport:
                raise
            raise Unknown(f"views at block {block} reverted ({exc.data or exc})", cid) from None
        except ValueError as exc:
            raise Unknown(f"views at block {block}: {exc}", cid) from None

    def derive(self, ctx: Ctx, e) -> dict | None:
        """The hit of one failed liquidation (a row for m14_hits), None when it is not one; raises Unknown."""
        fn = self.liquidation(e)
        if fn is None:
            return None
        tx = ctx.rpc.transaction(e.tx_hash)
        data = (tx or {}).get("input")
        if not data:
            raise Unknown("transaction or its calldata unavailable")
        if not data.lower().startswith("0x" + LIQUIDATE[fn].hex()):
            raise ValueError("transaction input is not the inbox's liquidation call")
        owner, ops, _ = decode_cluster_args(bytes.fromhex(data[10:]))
        cid = cluster_id(owner, ops)
        key = "0x" + cid.hex()
        B = e.block
        pos = int(tx["transactionIndex"], 16) if tx.get("transactionIndex") is not None else None
        events = ctx.db.cluster_events(cid.hex(), B)
        in_block = [ev for ev in events if ev.block == B]
        if pos is None:
            if len(in_block) > 1:
                raise Unknown(f"no transactionIndex reported and block {B} has {len(in_block)} events of the "
                              f"cluster: their order against the failed transaction is unknown", key)
            before, after = in_block, []
        else:
            before = [ev for ev in in_block if ev.tx_index < pos]
            after = [ev for ev in in_block if ev.tx_index > pos]
        deposits = [ev for ev in events if ev.kind == "ClusterDeposited" and (ev.block == B - 1 or ev in before)]
        if not deposits:
            return None
        earlier = [ev for ev in events if ev.block < B and ev.cluster is not None]
        if after:  # the end-of-B state is not the state the liquidation met: derive it from the end of B-1
            if any(ev.kind != "ClusterDeposited" for ev in before):
                raise Unknown(f"state at the failed transaction not derivable (block {B} changes the cluster "
                              f"before it by more than deposits, and again after it)", key)
            at, tuples = B - 1, [ev.cluster for ev in earlier[-1:]]
            first = in_block[0] if in_block else None
            if first is not None and first.kind == "ClusterDeposited" and first.cluster is not None:
                # deposit() only adds msg.value to the caller's (validated) tuple: the tuple before it is exact
                c = first.cluster
                tuples.append(Cluster(c.validator_count, c.network_fee_index, c.index, c.active,
                                      c.balance - int(first.extra.get("value", 0))))
            info = ctx.inventory().clusters.get(key)
            if info is not None and info.api:  # an untrusted candidate: the views validate its hash at B-1
                try:
                    tuples.append(api_tuple(info.api))
                except (KeyError, ValueError, TypeError):
                    pass
        else:
            at, tuples = B, [ev.cluster for ev in earlier + before if ev.cluster is not None][-1:]
        if not tuples:
            raise Unknown(f"no stored tuple of the cluster at block {at}", key)
        version = self.version_at(ctx, cid, at)
        if version is None:
            raise Unknown(f"the cluster is not in storage at block {at}", key)
        if version != VERSION[fn]:
            return None  # e.g. liquidate on a legacy cluster: reverted for its version, not for a deposit
        for i, tup in enumerate(tuples):  # the views validate the tuple's hash: the first that validates
            try:
                liq, burn = self.views_at(ctx, owner, ops, tup, version, at)
                break
            except Unknown:
                if i == len(tuples) - 1:
                    raise
        dust = [ev for ev in deposits if int(ev.extra.get("value", 0)) < burn]
        if not dust:
            return None  # no deposit below one block of burn: not this attack, whatever the cluster's state
        if after:
            total = sum(int(ev.extra.get("value", 0)) for ev in before)
            if not (liq and total < burn) and not (not liq and total >= burn):
                raise Unknown(f"liquidatable at block {B - 1}: {liq}, deposits before the failed transaction "
                              f"{total} vs burn {burn}: the state at the failed transaction is undetermined", key)
        if not liq:
            return None
        dep = dust[-1]  # the latest qualifying deposit before the failed transaction
        return {"tx_hash": e.tx_hash, "block_hash": e.block_hash, "block": B, "tx_index": pos, "cluster_id": key,
                "owner": owner, "operator_ids": json.dumps(list(ops)), "function": fn, "revert_name": e.revert_name,
                "deposit_tx": dep.tx_hash, "deposit_block": dep.block, "deposit_log_index": dep.log_index,
                "value": str(int(dep.extra.get("value", 0))), "burn_rate": str(burn),
                "ordering": "unknown" if pos is None and dep.block == B else "before", "version": version}

    def handle(self, ctx: Ctx, e, notes: list) -> Unknown | None:
        """Derive one entry: record its hit, or keep it pending (returned) when it cannot be evaluated."""
        try:
            hit = self.derive(ctx, e)
        except Unknown as unk:
            t = block_times(ctx.rpc, [e.block], {e.block: e.block_hash})[e.block]
            ctx.db.conn.execute("INSERT OR REPLACE INTO m14_pending VALUES (?,?,?,?,?,?)",
                                (e.id, e.tx_hash, e.block, t, unk.cluster, unk.reason))
            return unk
        except ValueError as exc:  # the calldata is there and is not a decodable liquidation: permanent
            notes.append(f"{e.tx_hash} (block {e.block}): calldata unusable ({exc}); not a hit, not retried")
            hit = None
        ctx.db.conn.execute("DELETE FROM m14_pending WHERE inbox_id=?", (e.id,))
        if hit is not None:
            hit["block_time"] = block_times(ctx.rpc, [e.block], {e.block: e.block_hash})[e.block]
            ctx.db.conn.execute(f"INSERT OR IGNORE INTO m14_hits ({', '.join(HIT_COLUMNS)}) VALUES "
                                f"({', '.join('?' * len(HIT_COLUMNS))})", [hit[c] for c in HIT_COLUMNS])
        return None

    # ---- run

    def run(self, ctx: Ctx, block: int) -> list[Finding]:
        notes: list[str] = []
        unknown: list[Unknown] = []
        window = self.config["window_s"]
        cp = ctx.db.meta("checkpoint") or {}
        now = block_times(ctx.rpc, [block], {block: cp.get("hash")} if cp.get("block") == block else None)[block]
        new = ctx.inbox
        done = {e.id for e in new}  # each entry is derived once per run
        A = ctx.reorg_ancestor
        if A is not None:
            ctx.db.conn.execute("DELETE FROM m14_hits WHERE block > ?", (A,))
            ctx.db.conn.execute("DELETE FROM m14_pending WHERE block > ?", (A,))
        for p in ctx.db.conn.execute("SELECT * FROM m14_pending WHERE block_time <= ?", (now - window,)).fetchall():
            ctx.db.conn.execute("DELETE FROM m14_pending WHERE inbox_id=?", (p["inbox_id"],))
            notes.append(f"{p['tx_hash']} (block {p['block']}): left the {window} s window unevaluated ({p['reason']})")
        rows = []
        if A is not None:  # entries delivered before the reorg whose block is still canonical: re-derive
            rows += ctx.db.conn.execute("SELECT * FROM inbox WHERE canonical = 1 AND resolved = 1 AND block > ? "
                                        "ORDER BY id", (A,)).fetchall()
        for p in ctx.db.conn.execute("SELECT inbox_id FROM m14_pending ORDER BY inbox_id").fetchall():
            r = ctx.db.conn.execute("SELECT * FROM inbox WHERE id=?", (p[0],)).fetchone()
            if r is None or r["canonical"] == 0:  # its block left the canonical chain
                ctx.db.conn.execute("DELETE FROM m14_pending WHERE inbox_id=?", (p[0],))
            else:
                rows.append(r)
        for r in rows:
            if r["id"] not in done:
                done.add(r["id"])
                unknown.append(self.handle(ctx, InboxEntry.from_row(r), notes))
        unknown += [self.handle(ctx, e, notes) for e in new]
        if A is not None and any(u is not None and u.cluster is None for u in unknown):
            raise RuntimeError(f"m14 reorg run at block {block}: a failed liquidation cannot be read on the new chain "
                               f"({next(u.reason for u in unknown if u is not None and u.cluster is None)}); it could "
                               f"support any reorg key, so the run is retried (README §6.2)")

        self.fill_times(ctx)
        pending = [dict(r) for r in ctx.db.conn.execute("SELECT * FROM m14_pending ORDER BY inbox_id")]
        keys = {r[0] for r in ctx.db.conn.execute("SELECT cluster_id FROM m14_open")} | set(ctx.reorg_keys)
        keys |= {r[0] for r in ctx.db.conn.execute("SELECT DISTINCT cluster_id FROM m14_hits WHERE block_time > ?",
                                                   (now - window,))}
        findings = [f for cid in sorted(keys) if (f := self.evaluate(ctx, block, now, cid, pending, notes))]
        notes += [f"{p['tx_hash']} (block {p['block']}"
                  + (f", cluster {p['cluster_id']}" if p["cluster_id"] else ", cluster unknown")
                  + f"): {p['reason']}; retried every run" for p in pending]
        if notes:
            ctx.health("m14-unknown", "M14 cannot evaluate (not treated as clean):\n- "
                       + "\n- ".join(n[:300] for n in notes[:20]), dedup="m14-unknown:" + "|".join(notes)[:2000],
                       realert=True)
        return findings

    def fill_times(self, ctx: Ctx) -> None:
        """Header timestamps of hits recorded without one (a database from before the time window)."""
        rows = ctx.db.conn.execute("SELECT DISTINCT block, block_hash FROM m14_hits WHERE block_time IS NULL").fetchall()
        if rows:
            times = block_times(ctx.rpc, [r[0] for r in rows], {r[0]: r[1] for r in rows})
            for b, t in times.items():
                ctx.db.conn.execute("UPDATE m14_hits SET block_time=? WHERE block=?", (t, b))

    def evaluate(self, ctx: Ctx, block: int, now: int, cid: str, pending: list, notes: list) -> Finding | None:
        window, repeat = self.config["window_s"], self.config["repeat_count"]
        hits = [dict(r) for r in ctx.db.conn.execute(
            "SELECT * FROM m14_hits WHERE cluster_id=? ORDER BY block, tx_index", (cid,))]
        recent = [h for h in hits if now - h["block_time"] < window]
        maybe = [p for p in pending if p["cluster_id"] in (None, cid)]
        row = ctx.db.conn.execute("SELECT * FROM m14_open WHERE cluster_id=?", (cid,)).fetchone()
        prev = row["severity"] if row else None
        if not recent:
            if maybe and (prev or cid in ctx.reorg_keys):
                if prev is None:
                    raise RuntimeError(f"m14: open cluster {cid} has no recorded state and pending entries")
                return Finding(check=self.id, key=cid, severity=prev,
                               title=f"M14 deposit front-running, current state UNKNOWN: {short(cid)}",
                               body=(f"Cluster {cid}: no evaluated hit remains in the last {window} s of chain time "
                                     f"(block {block}), but {len(maybe)} failed liquidation(s) in the window cannot "
                                     f"be evaluated yet and may be hits. Kept open."),
                               evidence={"block": block, "chain_time": now, "count": 0, "window_s": window,
                                         "pending": [p["tx_hash"] for p in maybe]})
            if not row and cid not in ctx.reorg_keys:
                return None
            ctx.db.conn.execute("DELETE FROM m14_open WHERE cluster_id=?", (cid,))
            last = hits[-1] if hits else None
            why = ("no front-running hit remains on the canonical chain" if last is None else
                   f"no front-running hit in the last {window} s of chain time (latest at block {last['block']}, "
                   f"{now - last['block_time']} s before block {block})")
            return Finding(check=self.id, key=cid, severity="info", resolve=True,
                           title=f"M14 deposit front-running ended: {short(cid)}",
                           body=f"Cluster {cid}: {why}.", evidence={"block": block, "chain_time": now})
        last = recent[-1]
        owner, ops = last["owner"], json.loads(last["operator_ids"])
        zero, zero_note = self.at_zero(ctx, block, cid, owner, ops)
        if zero is None:
            notes.append(f"cluster {cid}: computed-zero test unavailable at block {block} ({zero_note})")
        if zero:
            sev = "page"
        elif zero is None and prev == "page":
            sev = "page"  # cannot re-evaluate: keep the page
        elif len(recent) >= repeat:
            sev = "warn"
        else:
            sev = "info"
        if maybe and prev and RANK[prev] > RANK[sev]:
            sev = prev  # a pending entry may restore the count or the zero: no lower severity without proof
        new_hit = last["tx_hash"] != (row["last_hit"] if row else None)
        ctx.db.conn.execute("INSERT OR REPLACE INTO m14_open (cluster_id, severity, block, owner, operator_ids, "
                            "last_hit) VALUES (?,?,?,?,?,?)", (cid, sev, block, owner, json.dumps(ops), last["tx_hash"]))
        lines = [f"- failed {h['function']} {h['tx_hash']} (block {h['block']}, {now - h['block_time']} s ago) after "
                 f"deposit {h['deposit_tx']} of {h['value']} {'SSV' if h['version'] == 'SSV' else 'wei'} "
                 f"(block {h['deposit_block']}, burn {h['burn_rate']} per block, "
                 f"{'same block' if h['deposit_block'] == h['block'] else 'previous block'}"
                 f"{', order unknown' if h['ordering'] == 'unknown' else ''})" for h in recent[-10:]]
        titles = {"info": f"M14 liquidation front-run by a dust deposit: {short(cid)}",
                  "warn": f"M14 repeated deposit front-running ({len(recent)} in 24 h): {short(cid)}",
                  "page": f"M14 front-run cluster at computed zero: {short(cid)}"}
        body = (f"Cluster {cid} (owner {owner}, operators {ops}): {len(recent)} liquidation(s) reverted after a "
                f"deposit below one block of burn within the last {window} s of chain time (block {block}).\n"
                + "\n".join(lines)
                + (f"\n{len(maybe)} more failed liquidation(s) in the window cannot be evaluated yet." if maybe else "")
                + (f"\n{zero_note}" if zero_note else "")
                + "\nRunbook: liquidate with the current tuple (re-read it at the pending block); if the deposits "
                  "continue, escalate (KI #3).")
        return Finding(check=self.id, key=cid, severity=sev, title=titles[sev], body=body,
                       subject={"cluster_id": cid, "owner": owner, "operator_ids": ops},
                       evidence={"block": block, "chain_time": now, "hits": [h["tx_hash"] for h in recent],
                                 "count": len(recent), "window_s": window, "zero": zero,
                                 "pending": [p["tx_hash"] for p in maybe]},
                       revision=last["tx_hash"] if sev == "info" and new_hit else None)

    def at_zero(self, ctx: Ctx, block: int, cid: str, owner: str, ops) -> tuple[bool | None, str]:
        info = ctx.inventory(block).clusters.get(cid)
        if info is None or not info.valid:  # not listed (or no validated tuple): storage decides, never the listing
            logs = [("logs", ev.cluster) for ev in reversed(ctx.db.cluster_events(cid[2:], block))
                    if ev.cluster is not None][:1]
            info = stored_cluster(ctx.rpc, self.proxy, owner, ops, logs, block)
        if info.version is None:
            return False, "The cluster is not in storage at the checkpoint."
        if not info.valid:
            return None, "no candidate tuple validates against storage"
        if not info.cluster.active or info.cluster.validator_count == 0:
            return False, ("The cluster is liquidated." if not info.cluster.active else "")
        sel = GET_BALANCE if info.version == "ETH" else GET_BALANCE_SSV
        try:
            bal = word(ctx.rpc.call(self.views, sel + encode_cluster_args(info.owner, info.operator_ids,
                                                                          info.cluster)), "getBalance")
        except RpcError as exc:
            if exc.transport:
                raise
            return None, f"getBalance reverted ({exc.data or exc})"
        except ValueError as exc:
            return None, str(exc)
        unit = "wei" if info.version == "ETH" else "SSV wei"
        return bal == 0, (f"Computed zero at block {block}: phantom accrual has started (M3/M4)." if bal == 0 else
                          f"View-computed balance {bal} {unit} at block {block}.")
