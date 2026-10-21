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

**Cap.** At most `max_blocks_per_run` blocks are read per run (one batch). A longer gap (first run after an
outage, a slow RPC) is read at a bounded, evenly spaced sample that always ends at the checkpoint: any increase
between two sampled blocks pages, the finding says the gap was sampled and that transfers netting to zero inside
it could not be excluded, and an `m13-gap` health message names the unread range even when nothing increased.

**ACCEPTED GAP (D10, Marco 2026-10-21).** Not detectable here (the public RPC has no trace API): transfers that net
to zero within one block (value in and out in the same block, e.g. a top-up swept by the attacker in the same block;
also between two read blocks when `every_n_blocks` > 1 or the gap was sampled); the attacker/consolidation
addresses' internal calls and constructor interactions with SSV contracts or our wallets (spec M13 row 3).

**State and reorgs** (README §6.1/§6.2). `m13_watch` (last read block and balance per address) and `m13_topup`
(one row per top-up, its resolution block) are block-derived. In the run with reason `reorg` (ancestor A): top-ups
above A are deleted, resolutions above A are undone, and an address read above A is re-read at A; the run then
re-reads (A, checkpoint] on the new chain. A top-up the new chain still has is re-reported (same key, same incident,
no second page); one it does not have is not reported, so the framework resolves it as "evidence left the
canonical chain" (never re-paged). Every run re-reports every open top-up (page, or `resolve=True`).
"""

from __future__ import annotations

import json
import re

from checks.base import Check, Ctx, Every, Finding, NearSetEveryBlock
from checks.m12_wallet_burn import balances_at, headers, raw_batch
from checks.registry import register
from rpc import RpcError

ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS m13_watch (address TEXT PRIMARY KEY, block INTEGER NOT NULL, balance TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS m13_topup (
    address TEXT NOT NULL, block INTEGER NOT NULL, prev_block INTEGER NOT NULL, before TEXT NOT NULL,
    after TEXT NOT NULL, evidence TEXT NOT NULL, resolved_block INTEGER, resolved_balance TEXT,
    PRIMARY KEY (address, block));
"""


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
    }
    schema = SCHEMA

    def __init__(self, config: dict, settings: dict):
        super().__init__(config, settings)
        addrs = self.config["addresses"]
        if not isinstance(addrs, dict) or not addrs or not all(isinstance(a, str) and ADDRESS.match(a) for a in addrs):
            raise SystemExit(f"checks.m13.addresses is required ({{0x address: label}}; spec §3 M13, D10), got "
                             f"{addrs!r}")
        for k in ("every_n_blocks", "max_blocks_per_run", "attribution_max_blocks", "interval_s"):
            if not isinstance(self.config[k], int) or self.config[k] <= 0:
                raise SystemExit(f"checks.m13.{k} must be a positive integer, got {self.config[k]!r}")
        self.labels = {a.lower(): str(label) for a, label in addrs.items()}
        self.near_set = frozenset(self.labels)
        self.schedule = (NearSetEveryBlock(), Every(self.config["interval_s"]))

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
        pairs = [(a, b) for a, (blocks, _) in plans.items() for b in blocks]
        read = dict(zip(pairs, balances_at(ctx, pairs)))
        findings: list[Finding] = []
        for a, (blocks, sampled) in plans.items():
            last, prev = watch[a]
            if sampled:
                ctx.health("m13-gap", f"M13 read {len(blocks)} of the {block - last} blocks {last + 1}..{block} for "
                           f"{self.labels[a]} {a} (cap max_blocks_per_run): transfers netting to zero between the "
                           f"read blocks could not be excluded.", dedup=f"m13-gap:{a}:{last}:{block}")
            p = last
            for b in blocks:
                bal = read[(a, b)]
                if bal > prev:
                    ev = {"sampled_gap": [last + 1, block, len(blocks)] if sampled else None,
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
        # found and resolved in the same run is paged first, then resolved
        since = {a: last for a, (last, _) in watch.items()}
        for r in conn.execute("SELECT address, block, prev_block, before, after, evidence, resolved_block, "
                              "resolved_balance FROM m13_topup WHERE resolved_block IS NULL OR resolved_block > ? "
                              "ORDER BY block, address", (min(since.values(), default=block),)):
            findings += self.report(r, block, new=r[1] > since.get(r[0], block))
        self.prune(ctx, block)
        return findings

    def rewind(self, ctx: Ctx, ancestor: int) -> None:
        conn = ctx.db.conn
        conn.execute("DELETE FROM m13_topup WHERE block > ?", (ancestor,))
        conn.execute("UPDATE m13_topup SET resolved_block=NULL, resolved_balance=NULL WHERE resolved_block > ?",
                     (ancestor,))
        stale = [r[0] for r in conn.execute("SELECT address FROM m13_watch WHERE block > ?", (ancestor,))]
        for a, bal in zip(stale, balances_at(ctx, [(a, ancestor) for a in stale])):
            conn.execute("UPDATE m13_watch SET block=?, balance=? WHERE address=?", (ancestor, str(bal), a))

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
                for n, rcs in zip(blocks, raw_batch(ctx, "eth_getBlockReceipts", [[hex(n)] for n in blocks], blocks)):
                    if not isinstance(rcs, list):
                        raise RpcError(f"eth_getBlockReceipts({n}): no result")
                    for rc in rcs:
                        for lg in rc.get("logs") or []:
                            blob = " ".join([*lg.get("topics", []), lg.get("data", "")]).lower()
                            if needle in blob:
                                found.setdefault(rc["transactionHash"], f"log of {lg.get('address')} names it")
        except (RpcError, KeyError, TypeError, ValueError) as exc:
            return {"transactions": [{"hash": h, "how": how} for h, how in found.items()],
                    "attribution": f"lookup failed: {type(exc).__name__}: {exc}"[:300]}
        return {"transactions": [{"hash": h, "how": how} for h, how in found.items()],
                "attribution": None if found else "internal transfer or not attributable from blocks/receipts"}
