"""M13 — incoming value to the retired keys, per-block balance watch (spec §3 M13, D10; plan §8 CP2c).

The OpenZeppelin Monitor rules (CP1A) see direct transactions only. This check reads `eth_getBalance` of each
retired key at every block of (last read block, checkpoint] (every `every_n_blocks`), so value that arrives
through an internal call of a contract is seen too:

- any increase over the previous read block → **page**, key `<address>:<block>` (the first block the increase
  was read at), with the delta, the balances and the transactions of that block that touch the address
  (sent from/to it, or naming it in a receipt log; best effort: a failed lookup is noted, never blocks the page);
- the first observation of an address stores a baseline silently (value present before the worker started is not
  an alert);
- the incident stays open (re-paged every 6 h) while the balance stays above the balance before that top-up: value
  on a leaked key is at risk. Once a later read is at or below it, it resolves with a message that says the value
  LEFT the address and that this is not an all-clear;
- an RPC error or an empty `eth_getBalance` result raises (the run is retried), never "no change".

**Non-archive RPC (repair 2, QA attempt 2 F2).** A pruned node serves historical state only a short way below its
head (publicnode: about 100 blocks). When the read of (last read block, checkpoint] has failed for
`state_fallback_after` consecutive runs, the run re-tries it restricted to the newest `state_window_blocks` (then
a quarter of that, … down to the checkpoint alone): the first readable block is compared with the last read balance,
so an increase anywhere in the unread range still pages (located in it, "the RPC did not serve that state"); an
`m13-state` health message names the unread range and says archive state is needed for full coverage. An RPC that
serves none of it keeps raising (an RPC failure, as before). A reorg run that cannot re-read the ancestor's state for
`state_fallback_after` consecutive runs raises a `check-error` naming the archive requirement (re-sent every 6 h,
`healthy: false`) instead of guessing: a reorg that deep needs an archive RPC.

**Cap.** At most `max_blocks_per_run` blocks are read per run (one batch). A longer gap (first run after an
outage, a slow RPC) is read at a bounded, evenly spaced sample that always ends at the checkpoint: any increase
between two sampled blocks pages, the finding says the gap was sampled and that transfers netting to zero inside
it could not be excluded, and an `m13-gap` health message names the unread range even when nothing increased.

**ACCEPTED GAP (D10, Marco 2026-10-21).** Not detectable here (the public RPC has no trace API): transfers that net
to zero within one block (value in and out in the same block, e.g. a top-up swept by the attacker in the same block;
also between two read blocks when `every_n_blocks` > 1 or the gap was sampled); the attacker/consolidation
addresses' internal calls and constructor interactions with SSV contracts or our wallets (spec M13 row 3).

**State and reorgs** (README §6.1/§6.2). `m13_watch` (last read block and balance per address) and `m13_topup`
(one row per top-up, its resolution block) are block-derived. `m13_orphan` remembers, per key, what the operator
was last told about a top-up whose block or resolution a reorg deleted (repair 2, QA attempt 2 F1). In the run
with reason `reorg` (ancestor A): every top-up row above A, and every row resolved above A, is copied to
`m13_orphan` (a key already there keeps its older entry: nothing was sent for it since); top-ups above A are
deleted, resolutions above A are undone, and an address read above A is re-read at A; the run then re-reads
(A, checkpoint] on the new chain. An orphan is settled once the checkpoint has passed the deleted block it depends
on, never before (a shorter new chain keeps it pending):

- operator told "value arrived" (incident open): the same key on the new chain is reported as usual (same
  incident, no second page; a drain there resolves it with the value-left text); no such key once the checkpoint
  passed its block → resolved "evidence left the canonical chain" (never re-paged); while the checkpoint is below
  its block → re-reported from the stored row (the incident stays open, nothing is sent);
- operator told "value left" (incident resolved): the key re-found and drained again, or not re-found → silent (as
  already told); re-found with the value still there once the checkpoint passed the deleted drain block → paged
  (a new incident: the value is at risk on the canonical chain); before that → nothing is reported yet.

Every run re-reports every open top-up (page, or `resolve=True`) and every orphan it settles or keeps open.
"""

from __future__ import annotations

import json
import re

from checks.base import Check, Ctx, Every, Finding, NearSetEveryBlock
from checks.m12_wallet_burn import balances_at, headers
from checks.registry import register
from rpc import RpcError

ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS m13_watch (address TEXT PRIMARY KEY, block INTEGER NOT NULL, balance TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS m13_topup (
    address TEXT NOT NULL, block INTEGER NOT NULL, prev_block INTEGER NOT NULL, before TEXT NOT NULL,
    after TEXT NOT NULL, evidence TEXT NOT NULL, resolved_block INTEGER, resolved_balance TEXT,
    PRIMARY KEY (address, block));
CREATE TABLE IF NOT EXISTS m13_orphan (
    address TEXT NOT NULL, block INTEGER NOT NULL, prev_block INTEGER NOT NULL, before TEXT NOT NULL,
    after TEXT NOT NULL, evidence TEXT NOT NULL, closed_block INTEGER, PRIMARY KEY (address, block));
"""
TOPUP = "address, block, prev_block, before, after, evidence, resolved_block, resolved_balance"


def _eth(wei: int) -> str:
    return f"{wei / 1e18:.6f} ETH"


def sample(lo: int, hi: int, stride: int, cap: int) -> tuple[list[int], bool]:
    """Blocks to read in (lo, hi]: every `stride`-th plus `hi`; at most `cap`, evenly spaced, ending at `hi`."""
    blocks = sorted(set(range(lo + stride, hi + 1, stride)) | {hi})
    if len(blocks) <= cap:
        return blocks, False
    step = (hi - lo) / cap
    return sorted({lo + max(1, round(step * (i + 1))) for i in range(cap - 1)} | {hi}), True


@register
class M13RetiredKeys(Check):
    id = "m13"
    defaults = {
        # Required (spec §3 M13, D10): {address: label}; config.example.json carries the dated retired keys.
        "addresses": None,
        "every_n_blocks": 1,
        "max_blocks_per_run": 256,  # eth_getBalance reads per address per run (one batch); beyond: sampled
        "attribution_max_blocks": 3,  # look for the transactions only when the increase is located this narrowly
        "receipt_logs": True,  # also attribute through receipt logs naming the address (eth_getBlockReceipts)
        "interval_s": 60,  # backstop schedule besides every checkpoint block
        "state_window_blocks": 64,  # non-archive RPC: the newest blocks read when older state is not served
        "state_fallback_after": 3,  # consecutive failed runs before that fallback (a transient error never)
    }
    schema = SCHEMA

    def __init__(self, config: dict, settings: dict):
        super().__init__(config, settings)
        addrs = self.config["addresses"]
        if not isinstance(addrs, dict) or not addrs or not all(isinstance(a, str) and ADDRESS.match(a) for a in addrs):
            raise SystemExit(f"checks.m13.addresses is required ({{0x address: label}}; spec §3 M13, D10), got "
                             f"{addrs!r}")
        for k in ("every_n_blocks", "max_blocks_per_run", "attribution_max_blocks", "interval_s",
                  "state_window_blocks", "state_fallback_after"):
            if not isinstance(self.config[k], int) or self.config[k] <= 0:
                raise SystemExit(f"checks.m13.{k} must be a positive integer, got {self.config[k]!r}")
        self.labels = {a.lower(): str(label) for a, label in addrs.items()}
        self.near_set = frozenset(self.labels)
        self.schedule = (NearSetEveryBlock(), Every(self.config["interval_s"]))
        self.read_failures = 0  # consecutive runs whose historical balance reads failed (in memory: a run rolls back)

    # ---- run

    def run(self, ctx: Ctx, block: int) -> list[Finding]:
        conn = ctx.db.conn
        if ctx.reorg_ancestor is not None:
            self.rewind(ctx, ctx.reorg_ancestor)
        watch = {r[0]: (r[1], int(r[2])) for r in conn.execute("SELECT address, block, balance FROM m13_watch")}
        new = [a for a in self.labels if a not in watch]
        if new:  # first observation: a silent baseline at the checkpoint
            for a, bal in zip(new, balances_at(ctx, [(a, block) for a in new])):
                conn.execute("INSERT INTO m13_watch (address, block, balance) VALUES (?,?,?)", (a, block, str(bal)))
        plans = {}
        for a, (last, _) in watch.items():
            if a not in self.labels:
                continue
            if last > block:
                raise RuntimeError(f"m13: {a} was read at block {last}, above the checkpoint {block}, without a "
                                   f"reorg marker")
            if last < block:
                plans[a] = sample(last, block, self.config["every_n_blocks"], self.config["max_blocks_per_run"])
        plans, read, unread = self.read(ctx, plans, watch, block)
        findings: list[Finding] = []
        for a, (blocks, sampled) in plans.items():
            last, prev = watch[a]
            if a in unread:
                lo, hi, why = unread[a]
                ctx.health("m13-state", f"M13 could not read the balance of {self.labels[a]} {a} at blocks {lo}..{hi} "
                           f"for {self.config['state_fallback_after']} consecutive runs (the RPC did not serve that "
                           f"historical state: {why}). Archive state is needed for full coverage. It compared the "
                           f"balance read at block {last} with the blocks {hi + 1}..{block} it could read: an increase "
                           f"in the unread range still pages, but value that came and went inside it could not be "
                           f"excluded.", dedup=f"m13-state:{a}:{last}:{block}")
            elif sampled:
                ctx.health("m13-gap", f"M13 read {len(blocks)} of the {block - last} blocks {last + 1}..{block} for "
                           f"{self.labels[a]} {a} (cap max_blocks_per_run): transfers netting to zero between the "
                           f"read blocks could not be excluded.", dedup=f"m13-gap:{a}:{last}:{block}")
            p = last
            for b in blocks:
                bal = read[(a, b)]
                if bal > prev:
                    ev = {"sampled_gap": [last + 1, block, len(blocks)] if sampled else None,
                          "unread": list(unread[a]) if a in unread and p == last else None,
                          "located_in": [p + 1, b]}
                    ev.update(self.attribute(ctx, a, p, b))
                    conn.execute("INSERT OR REPLACE INTO m13_topup (address, block, prev_block, before, after, "
                                 "evidence) VALUES (?,?,?,?,?,?)", (a, b, p, str(prev), str(bal), json.dumps(ev)))
                for row in conn.execute("SELECT block, before FROM m13_topup WHERE address=? AND block < ? AND "
                                        "resolved_block IS NULL", (a, b)).fetchall():
                    if bal <= int(row[1]):
                        conn.execute("UPDATE m13_topup SET resolved_block=?, resolved_balance=? WHERE address=? "
                                     "AND block=?", (b, str(bal), a, row[0]))
                prev, p = bal, b
            conn.execute("UPDATE m13_watch SET block=?, balance=? WHERE address=?", (block, str(prev), a))
        # every top-up still open, or resolved in this run, is reported (README §6.2: re-evaluated each run); one
        # found and resolved in the same run is paged first, then resolved. A key a reorg orphaned is settled by
        # what the operator was last told about it (`settle`), after the other findings.
        orphans = {(r[0], r[1]): r for r in conn.execute(
            "SELECT address, block, prev_block, before, after, evidence, closed_block FROM m13_orphan "
            "ORDER BY block, address") if r[0] in self.labels}
        since = {a: last for a, (last, _) in watch.items()}
        for r in conn.execute(f"SELECT {TOPUP} FROM m13_topup WHERE resolved_block IS NULL OR resolved_block > ? "
                              f"ORDER BY block, address", (min(since.values(), default=block),)).fetchall():
            if (r[0], r[1]) not in orphans:
                findings += self.report(r, block, new=r[1] > since.get(r[0], block))
        for (a, b), o in orphans.items():
            cur = conn.execute(f"SELECT {TOPUP} FROM m13_topup WHERE address=? AND block=?", (a, b)).fetchone()
            settled, found = self.settle(o, cur, block)
            findings += found
            if settled:
                conn.execute("DELETE FROM m13_orphan WHERE address=? AND block=?", (a, b))
        self.prune(ctx, block)
        return findings

    def read(self, ctx: Ctx, plans: dict, watch: dict, block: int) -> tuple[dict, dict, dict]:
        """The planned balance reads; after `state_fallback_after` consecutive failed runs, the same plan restricted
        to the newest `state_window_blocks` (then a quarter of that, … the checkpoint alone) — a pruned node's
        state window. Returns (plans, {(address, block): balance}, {address: (lo, hi, error) not read})."""
        pairs = [(a, b) for a, (blocks, _) in plans.items() for b in blocks]
        try:
            read = dict(zip(pairs, balances_at(ctx, pairs)))
        except RpcError as exc:
            self.read_failures += 1
            if self.read_failures < self.config["state_fallback_after"]:
                raise
            window = self.config["state_window_blocks"]
            while window >= 1:
                lo = block - window
                short = {a: (sample(max(lo, watch[a][0]), block, self.config["every_n_blocks"],
                                    self.config["max_blocks_per_run"]) if watch[a][0] < lo else plan)
                         for a, plan in plans.items()}
                window //= 4
                if short == plans:
                    continue
                pairs = [(a, b) for a, (blocks, _) in short.items() for b in blocks]
                try:
                    read = dict(zip(pairs, balances_at(ctx, pairs)))
                except RpcError:
                    continue
                self.read_failures = 0
                why = f"{type(exc).__name__}: {exc}"[:200]
                return short, read, {a: (watch[a][0] + 1, lo, why) for a in plans if watch[a][0] < lo}
            raise
        self.read_failures = 0
        return plans, read, {}

    def rewind(self, ctx: Ctx, ancestor: int) -> None:
        conn = ctx.db.conn
        # what the operator was told about keys whose block or resolution is being deleted (an older entry of the
        # same key wins: nothing was sent for it since)
        conn.execute("INSERT OR IGNORE INTO m13_orphan (address, block, prev_block, before, after, evidence, "
                     "closed_block) SELECT address, block, prev_block, before, after, evidence, resolved_block FROM "
                     "m13_topup WHERE block > ? OR resolved_block > ?", (ancestor, ancestor))
        conn.execute("DELETE FROM m13_topup WHERE block > ?", (ancestor,))
        conn.execute("UPDATE m13_topup SET resolved_block=NULL, resolved_balance=NULL WHERE resolved_block > ?",
                     (ancestor,))
        stale = [r[0] for r in conn.execute("SELECT address FROM m13_watch WHERE block > ?", (ancestor,))]
        try:
            balances = balances_at(ctx, [(a, ancestor) for a in stale])
        except RpcError as exc:
            self.read_failures += 1
            if self.read_failures < self.config["state_fallback_after"]:
                raise
            raise RuntimeError(f"m13: the RPC did not serve eth_getBalance at the reorg ancestor {ancestor} for "
                               f"{self.read_failures} consecutive runs ({exc}). A non-archive node cannot serve state "
                               f"that deep: configure an archive RPC. The reorg run is retried every step; its open "
                               f"incidents stay open.") from exc
        for a, bal in zip(stale, balances):
            conn.execute("UPDATE m13_watch SET block=?, balance=? WHERE address=?", (ancestor, str(bal), a))

    def settle(self, o, cur, block: int) -> tuple[bool, list[Finding]]:
        """An orphaned key (rewind): (settled, findings) from what the operator was last told and the new chain
        read up to `block`. `cur` is the key's m13_topup row on the new chain, if any."""
        a, b, p, before, after, ev, closed = o
        if closed is None:  # told "value arrived"; the incident is open
            if cur is not None:
                return True, self.report(cur, block, new=False)
            if block >= b:
                return True, [self.left_chain(o, block)]
            ev = json.dumps({**json.loads(ev), "pending_reorg_recheck": {"block": b, "checkpoint": block}})
            return False, self.report((a, b, p, before, after, ev, None, None), block, new=False)
        if cur is not None and cur[6] is None:  # told "value left"; the new chain still has it at the address
            if block >= closed:
                return True, self.report(cur, block, new=False)  # past the deleted drain: at risk again, paged
            return False, []
        if cur is None and block < b:
            return False, []
        return True, []  # not re-found, or drained again on the new chain: as already told

    def left_chain(self, o, block: int) -> Finding:
        a, b, _, before, after, ev, _ = o
        label = self.labels.get(a, a)
        return Finding(
            check=self.id, key=f"{a}:{b}", severity="page", resolve=True,
            title=f"Evidence left the canonical chain: incoming value to retired key {label} at block {b}",
            body=f"Resolved because its evidence left the canonical chain: a chain reorganisation replaced block {b}, "
                 f"and the new chain has no increase of the balance of {label} {a} there (re-read up to block "
                 f"{block}). The earlier page (+{_eth(int(after) - int(before))}) was based on a block that is no "
                 f"longer canonical.",
            subject={"address": a, "label": label, "block": b},
            evidence={**json.loads(ev), "block": b, "resolved_by_reorg": {"checkpoint": block}})

    def prune(self, ctx: Ctx, block: int) -> None:
        keep = 2 * int(ctx.settings.get("reorg_search_depth", 256))
        ctx.db.conn.execute("DELETE FROM m13_topup WHERE resolved_block IS NOT NULL AND resolved_block < ?",
                            (block - keep,))

    def report(self, r, block: int, new: bool) -> list[Finding]:
        a, b, p, before, after, ev, resolved, resolved_bal = r
        before, after, label = int(before), int(after), self.labels.get(a, a)
        ev = json.loads(ev)
        key = f"{a}:{b}"
        subject = {"address": a, "label": label, "block": b}
        evidence = {**ev, "block": b, "balance_before": str(before), "balance_after": str(after),
                    "delta_wei": str(after - before)}
        if resolved is not None and resolved <= block:
            paged = self.report((a, b, p, str(before), str(after), json.dumps(ev), None, None), block, new) if new else []
            return paged + [Finding(
                check=self.id, key=key, severity="page", resolve=True,
                title=f"Value LEFT retired key {label} (top-up at block {b}) — not an all-clear",
                body=f"The balance of {label} {a} fell to {_eth(int(resolved_bal))} at block {resolved}, at or below "
                     f"its {_eth(before)} before the top-up of {_eth(after - before)} at block {b}. The value LEFT the "
                     f"address; this is NOT an all-clear: confirm who moved it (our rescue, or theft with the leaked "
                     f"key).",
                subject=subject, evidence={**evidence, "resolved_block": resolved,
                                           "resolved_balance": str(resolved_bal)})]
        txs = ev.get("transactions")
        where = (f"blocks {ev['located_in'][0]}..{ev['located_in'][1]}" if ev["located_in"][0] != b else f"block {b}")
        body = (f"{label} {a} received {_eth(after - before)} in {where} (balance {_eth(before)} → {_eth(after)}). "
                f"The key is retired/compromised: a top-up there can be stolen. Move the value out with the "
                f"rescue procedure (spec M13). Transactions touching the address: "
                f"{', '.join(t['hash'] + ' (' + t['how'] + ')' for t in txs) if txs else 'none found'}"
                f"{' (' + ev['attribution'] + ')' if ev.get('attribution') else ''}.")
        if ev.get("sampled_gap"):
            lo, hi, n = ev["sampled_gap"]
            body += (f" The gap {lo}..{hi} was read at {n} sampled blocks (cap): transfers netting to zero between "
                     f"them could not be excluded.")
        if ev.get("unread"):
            lo, hi, why = ev["unread"]
            body += (f" The RPC did not serve the state of blocks {lo}..{hi} ({why}; archive state needed): the increase "
                     f"is measured against the balance read before them; value that came and went inside them could "
                     f"not be excluded.")
        if ev.get("pending_reorg_recheck"):
            body += (f" A chain reorganisation replaced block {b} and the new chain has not reached it yet (checkpoint "
                     f"{ev['pending_reorg_recheck']['checkpoint']}): kept open until that block is re-read.")
        return [Finding(check=self.id, key=key, severity="page",
                        title=f"Incoming value to retired key {label}: +{_eth(after - before)} at block {b}",
                        body=body, subject=subject, evidence=evidence)]

    def attribute(self, ctx: Ctx, addr: str, prev: int, b: int) -> dict:
        """Best effort: the transactions of the blocks (prev, b] sent from/to `addr` or whose receipt logs name it."""
        if b - prev > self.config["attribution_max_blocks"]:
            return {"transactions": [], "attribution": f"not searched: the increase lies somewhere in {b - prev} "
                                                       f"blocks ({prev + 1}..{b})"}
        needle = addr[2:].lower()
        found: dict[str, str] = {}
        try:
            blocks = list(range(prev + 1, b + 1))
            for blk in headers(ctx, blocks, full=True).values():
                for tx in blk.get("transactions") or []:
                    if not isinstance(tx, dict):
                        continue
                    if str(tx.get("to") or "").lower() == addr:
                        found.setdefault(tx["hash"], "direct transfer to the address")
                    elif str(tx.get("from") or "").lower() == addr:
                        found.setdefault(tx["hash"], "sent by the address")
            if self.config["receipt_logs"]:
                for n, rcs in zip(blocks, ctx.rpc.batch("eth_getBlockReceipts", [[hex(n)] for n in blocks], blocks)):
                    if not isinstance(rcs, list):
                        raise RpcError(f"eth_getBlockReceipts({n}): no result")
                    for rc in rcs:
                        for lg in rc.get("logs") or []:
                            blob = " ".join([*lg.get("topics", []), lg.get("data", "")]).lower()
                            if needle in blob:
                                found.setdefault(rc["transactionHash"], f"log of {lg.get('address')} names it")
        except Exception as exc:  # noqa: BLE001 — best effort: malformed provider output never blocks the page
            return {"transactions": [{"hash": h, "how": how} for h, how in found.items()],
                    "attribution": f"lookup failed: {type(exc).__name__}: {exc}"[:300]}
        return {"transactions": [{"hash": h, "how": how} for h, how in found.items()],
                "attribution": None if found else "internal transfer or not attributable from blocks/receipts"}
