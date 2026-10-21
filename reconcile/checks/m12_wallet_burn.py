"""M12 — operational wallet balances and burn rate (spec §3 M12, D4; plan §8 CP2c).

Every `interval_s` (5 min) at the checkpoint block:

- balance of the oracle wallet (slot 4 + EB updater) and of the liquidator (`eth_getBalance`);
- the oracle wallet's spend from the receipts of its own transactions (`gasUsed × effectiveGasPrice`, failed
  transactions included): trailing `window_blocks` (7 days) and last `day_blocks` (24 h), per day by the
  blocks' timestamps;
- runway = balance ÷ trailing 7-day average daily spend, and balance ÷ (average daily gas × current base fee,
  the mean of the last `base_fee_blocks` blocks' `baseFeePerGas`, plus the wallet's own gas-weighted priority fee
  over the window, `effectiveGasPrice − baseFeePerGas` of each verified transaction's block; `projection_tip:
  "none"` drops it); the shorter one drives the alert (V2c.7). Measured 2026-10-21: the oracle wallet pays a fixed
  1 gwei tip over a 0.03–0.36 gwei base fee, so the base fee alone would project a runway 3× too long.

| Key | Rule (thresholds are D4 decisions: `defaults`, overridable) | Severity |
|---|---|---|
| `<oracle>:balance` | balance < `oracle_balance_page_wei` (0.15 ETH) | page |
| `<oracle>:runway` | runway < `oracle_runway_page_days` (7) / < `oracle_runway_warn_days` (14) | page / warn |
| `<oracle>:daily-spend` | last-24 h spend > `oracle_daily_spend_warn_wei` (0.04 ETH) | warn |
| `<liquidator>:balance` | < `liquidator_balance_page_wei` (0.05 ETH) / < `liquidator_balance_warn_wei` (0.2 ETH) | page / warn |

Every key is evaluated in every run (a finding or `resolve=True`), so a reorg run re-reports them all.

**Which transactions.** Candidates come from Blockscout v2 (`/api/v2/addresses/<w>/transactions?filter=from`),
which is untrusted: each candidate is verified on chain (transaction by hash: sender, nonce, block; receipt:
sender, block, `gasUsed`, `effectiveGasPrice`; the block hash is the canonical one; block ≤ checkpoint), and the
set is complete only if the verified nonces are contiguous up to `eth_getTransactionCount(w, checkpoint)` (the
first backfill also proves the nonce just before the window, or nonce 0). An API failure, a page budget overrun or
a nonce gap makes the spend **UNKNOWN**: the runway and daily-spend keys are not evaluated (their open incidents
stay as they are; balance keys still are; a `m12-spend` health message once it has lasted `unknown_grace_s`, so a
Blockscout indexing lag of a few blocks stays quiet), nothing is advanced, retried at the next run. Never "no
spend". In a run with reason `reorg` an UNKNOWN spend **raises**, whatever `ctx.reorg_keys` holds (README §6.2
item 4): the persisted spend (benign evaluations included), open incidents last seen at or below the ancestor and
keys resolved on the deleted blocks all depend on the new chain's transaction set, so the run rolls back, the
`reorg:m12` marker stays and it is retried every step (balance keys of that run with it; `check-error` health,
`healthy: false`). A new chain with no transaction of the wallet above the ancestor needs no listing: the nonce at
the checkpoint equals the nonce re-read at the ancestor.

**State and reorgs** (README §6.1/§6.2). `m12_tx` (verified transactions in the window) and `m12_sync`
(`synced_block`, `next_nonce`, `history_from`) are derived from blocks: in the run with reason `reorg` (ancestor
A), rows above A are deleted and a wallet synced above A is rewound to A with its nonce re-read at A; the run then
re-syncs (A, checkpoint] on the new chain. Restart: both tables are durable; the next run continues from
`synced_block`.

The chain reads `rpc.Chain` lacks (nonce, receipts, headers, fee history, block receipts) go through
`raw_batch` below: pinned to blocks ≤ `ctx.block`, counted in `ctx.rpc.counts` (CP3 promotion item).
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from fractions import Fraction

from checks.base import Check, Ctx, Every, Finding
from checks.registry import register
from rpc import RpcError

ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
ETH = 10**18


# ---------------------------------------------------------------- chain reads rpc.Chain does not offer (lane-local)


def raw_batch(ctx: Ctx, method: str, params: list[list], blocks: list[int] = ()) -> list:
    """Batched read-only JSON-RPC `method`, one call per params list. `blocks`: every block number the calls
    name; each must be at or below the run's pinned block (never read above the checkpoint)."""
    for b in blocks:
        if b > ctx.block or b < 0:
            raise ValueError(f"{method} at block {b} is outside [0, pinned block {ctx.block}]")
    if not params:
        return []
    ctx.rpc.counts[method] += len(params)
    res = ctx.rpc.rpc.batch([(method, p) for p in params])
    if not isinstance(res, list) or len(res) != len(params):  # a partial answer is an error, never fewer items
        raise RpcError(f"{method}: {len(res) if isinstance(res, list) else 'no'} results for {len(params)} calls")
    return res


def _int(value, what: str) -> int:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise RpcError(f"{what}: missing or malformed result {value!r}")
    return int(value, 16)


def nonces(ctx: Ctx, addresses: list[str], block: int) -> list[int]:
    res = raw_batch(ctx, "eth_getTransactionCount", [[a, hex(block)] for a in addresses], [block])
    return [_int(r, f"eth_getTransactionCount({a}, {block})") for a, r in zip(addresses, res)]


def headers(ctx: Ctx, blocks: list[int], full: bool = False) -> dict[int, dict]:
    """Block headers (or full blocks) at or below the pinned block; a missing block is an RPC error."""
    blocks = sorted(set(blocks))
    res = raw_batch(ctx, "eth_getBlockByNumber", [[hex(b), full] for b in blocks], blocks)
    out = {}
    for b, r in zip(blocks, res):
        if not isinstance(r, dict) or not r.get("hash"):
            raise RpcError(f"eth_getBlockByNumber({b}): no block")
        out[b] = r
    return out


def balances_at(ctx: Ctx, pairs: list[tuple[str, int]]) -> list[int]:
    """eth_getBalance for (address, block) pairs, one batch; an empty result is an RPC error, never 0."""
    res = raw_batch(ctx, "eth_getBalance", [[a, hex(b)] for a, b in pairs], [b for _, b in pairs])
    return [_int(r, f"eth_getBalance({a}, {b})") for (a, b), r in zip(pairs, res)]


def base_fee(ctx: Ctx, blocks: int) -> int:
    """Mean `baseFeePerGas` of the `blocks` blocks ending at the pinned block (eth_feeHistory)."""
    (res,) = raw_batch(ctx, "eth_feeHistory", [[hex(blocks), hex(ctx.block), []]], [ctx.block])
    fees = res.get("baseFeePerGas") if isinstance(res, dict) else None
    if not isinstance(fees, list) or len(fees) < 2:
        raise RpcError(f"eth_feeHistory({blocks}, {ctx.block}): malformed result {res!r:.200}")
    fees = [_int(f, "baseFeePerGas") for f in fees[:-1]]  # the last entry is the next block's
    return sum(fees) // len(fees)


def http_get_json(url: str, timeout: float) -> dict:
    req = urllib.request.Request(url, headers={"accept": "application/json", "user-agent": "ssv-watch/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


# ---------------------------------------------------------------- the check


class SpendUnknown(Exception):
    """The wallet's transaction set in the window could not be proven complete."""


@dataclass(frozen=True)
class Wallet:
    role: str
    address: str  # lower case
    label: str
    balance_warn: int | None
    balance_page: int | None
    runway_warn_days: float | None = None
    runway_page_days: float | None = None
    spend_warn: int | None = None

    @property
    def tracks_spend(self) -> bool:
        return any(x is not None for x in (self.runway_warn_days, self.runway_page_days, self.spend_warn))


SCHEMA = """
CREATE TABLE IF NOT EXISTS m12_tx (
    wallet TEXT NOT NULL, tx_hash TEXT NOT NULL, block INTEGER NOT NULL, block_hash TEXT NOT NULL,
    nonce INTEGER NOT NULL, gas_used TEXT NOT NULL, gas_price TEXT NOT NULL, fee TEXT NOT NULL,
    status INTEGER NOT NULL, base_fee TEXT NOT NULL, PRIMARY KEY (wallet, tx_hash));
CREATE INDEX IF NOT EXISTS m12_tx_block ON m12_tx (wallet, block);
CREATE TABLE IF NOT EXISTS m12_sync (
    wallet TEXT PRIMARY KEY, synced_block INTEGER NOT NULL, next_nonce INTEGER NOT NULL,
    history_from INTEGER NOT NULL);
"""


def evidence_of(m: dict) -> dict:
    """Spend metrics as JSON evidence: integers as strings, ratios to 6 significant digits."""
    return {k: (str(v) if isinstance(v, int) else v if v is None or isinstance(v, str) else f"{float(v):.6g}")
            for k, v in m.items()}


def _eth(wei: int | Fraction) -> str:
    return f"{float(wei) / ETH:.4f} ETH"


def _days(x: Fraction | None) -> str:
    return "∞" if x is None else f"{float(x):.1f} d"


@register
class M12WalletBurn(Check):
    id = "m12"
    defaults = {
        "interval_s": 300,
        # Required (operational facts, spec §3 M12): config.example.json carries the dated addresses.
        "oracle_wallet": None,
        "liquidator_wallet": None,
        # D4 decisions
        "oracle_balance_page_wei": 150_000_000_000_000_000,
        "oracle_runway_warn_days": 14,
        "oracle_runway_page_days": 7,
        "oracle_daily_spend_warn_wei": 40_000_000_000_000_000,
        "liquidator_balance_warn_wei": 200_000_000_000_000_000,
        "liquidator_balance_page_wei": 50_000_000_000_000_000,
        # spend measurement
        "window_blocks": 50_400,  # trailing average: 7 days of 12 s slots (per day by the blocks' timestamps)
        "day_blocks": 7_200,  # daily spend: the last 24 h
        "base_fee_blocks": 300,  # "current base fee": mean over the last hour (eth_feeHistory, at most 1024)
        "projection_tip": "observed",  # + the window's gas-weighted priority fee ("none": base fee only)
        # Required with "observed": the tip used when the window has no verified transaction (dated value in
        # config.example.json; never 0)
        "fallback_tip_wei": None,
        "blockscout_url": "https://eth.blockscout.com",
        "max_pages": 40,  # Blockscout pages per wallet per run (50 items each); beyond: UNKNOWN, retried
        "http_timeout_s": 20,
        "unknown_grace_s": 900,  # an UNKNOWN spend lasting this long sends the m12-spend health message
    }
    schema = SCHEMA

    def __init__(self, config: dict, settings: dict):
        super().__init__(config, settings)
        c = self.config
        for k in ("oracle_wallet", "liquidator_wallet"):
            if not isinstance(c[k], str) or not ADDRESS.match(c[k]):
                raise SystemExit(f"checks.m12.{k} is required (an 0x address; spec §3 M12), got {c[k]!r}")
        for k in ("window_blocks", "day_blocks", "base_fee_blocks", "max_pages", "interval_s"):
            if not isinstance(c[k], int) or c[k] <= 0:
                raise SystemExit(f"checks.m12.{k} must be a positive integer, got {c[k]!r}")
        if c["day_blocks"] > c["window_blocks"] or c["base_fee_blocks"] > 1024:
            raise SystemExit("checks.m12: need day_blocks <= window_blocks and base_fee_blocks <= 1024")
        if c["projection_tip"] not in ("observed", "none"):
            raise SystemExit(f"checks.m12.projection_tip must be 'observed' or 'none', got {c['projection_tip']!r}")
        if c["projection_tip"] == "observed" and (not isinstance(c["fallback_tip_wei"], int)
                                                  or c["fallback_tip_wei"] <= 0):
            raise SystemExit(f"checks.m12.fallback_tip_wei is required with projection_tip 'observed' (a positive "
                             f"wei value), got {c['fallback_tip_wei']!r}")
        self.wallets = [
            Wallet("oracle", c["oracle_wallet"].lower(), "oracle slot 4 + EB updater", None,
                   c["oracle_balance_page_wei"], c["oracle_runway_warn_days"], c["oracle_runway_page_days"],
                   c["oracle_daily_spend_warn_wei"]),
            Wallet("liquidator", c["liquidator_wallet"].lower(), "liquidator", c["liquidator_balance_warn_wei"],
                   c["liquidator_balance_page_wei"]),
        ]
        self.schedule = Every(c["interval_s"])
        self.http_get = lambda url: http_get_json(url, c["http_timeout_s"])
        self.last: dict[str, dict] = {}  # digest input: the last evaluation per wallet

    # ---- run

    def run(self, ctx: Ctx, block: int) -> list[Finding]:
        if ctx.reorg_ancestor is not None:
            self.rewind(ctx, ctx.reorg_ancestor)
        bals = dict(zip((w.address for w in self.wallets),
                        balances_at(ctx, [(w.address, block) for w in self.wallets])))
        findings: list[Finding] = []
        for w in self.wallets:
            bal = bals[w.address]
            self.last[w.address] = {"block": block, "balance": bal}
            findings.append(self.balance_finding(w, bal, block))
            if not w.tracks_spend:
                continue
            try:
                self.sync(ctx, w.address, block)
                m = self.metrics(ctx, w.address, bal, block)
            except SpendUnknown as exc:
                if ctx.reorg_ancestor is not None:  # README §6.2 item 4, whatever ctx.reorg_keys holds
                    raise RuntimeError(f"m12 cannot re-measure the spend of {w.address} on the new chain after the "
                                       f"reorg (ancestor {ctx.reorg_ancestor}): {exc}") from exc
                since = ctx.state(f"unknown_since:{w.address}")
                if since is None:
                    since = ctx.clock()
                    ctx.set_state(f"unknown_since:{w.address}", since)
                if ctx.clock() - since >= self.config["unknown_grace_s"]:  # a transient indexer lag stays quiet
                    ctx.health("m12-spend", f"M12 cannot measure the spend of the {w.label} {w.address} at block "
                               f"{block} (since {int(ctx.clock() - since)} s): {exc}. Runway and daily-spend "
                               f"alerts are not evaluated until it can (retried every run); balance alerts still "
                               f"are.", dedup=f"m12-spend:{w.address}", realert=True)
                self.last[w.address]["spend"] = f"UNKNOWN ({exc})"
                continue
            if ctx.state(f"unknown_since:{w.address}") is not None:
                ctx.set_state(f"unknown_since:{w.address}", None)
            self.last[w.address].update(m)
            findings += self.spend_findings(w, m, block)
        return findings

    def rewind(self, ctx: Ctx, ancestor: int) -> None:
        """State derived from blocks above the reorg's ancestor came from deleted blocks."""
        conn = ctx.db.conn
        conn.execute("DELETE FROM m12_tx WHERE block > ?", (ancestor,))
        rows = conn.execute("SELECT wallet, history_from FROM m12_sync WHERE synced_block > ?", (ancestor,)).fetchall()
        for wallet, history_from in rows:
            if history_from >= ancestor:
                conn.execute("DELETE FROM m12_sync WHERE wallet=?", (wallet,))
                conn.execute("DELETE FROM m12_tx WHERE wallet=?", (wallet,))
                continue
            (n,) = nonces(ctx, [wallet], ancestor)
            conn.execute("UPDATE m12_sync SET synced_block=?, next_nonce=? WHERE wallet=?", (ancestor, n, wallet))

    # ---- findings

    def balance_finding(self, w: Wallet, bal: int, block: int) -> Finding:
        key = f"{w.address}:balance"
        subject = {"wallet": w.address, "role": w.role}
        evidence = {"block": block, "balance_wei": str(bal), "warn_below_wei": str(w.balance_warn),
                    "page_below_wei": str(w.balance_page)}
        sev = ("page" if w.balance_page is not None and bal < w.balance_page else
               "warn" if w.balance_warn is not None and bal < w.balance_warn else None)
        if sev is None:
            return Finding(check=self.id, key=key, severity="info", title=f"{w.label} balance restored",
                           body=f"{w.label} {w.address} balance {_eth(bal)} at block {block} is above the alert "
                                f"thresholds.", subject=subject, evidence=evidence, resolve=True)
        limit = w.balance_page if sev == "page" else w.balance_warn
        return Finding(check=self.id, key=key, severity=sev, title=f"{w.label} balance low: {_eth(bal)}",
                       body=f"{w.label} {w.address} holds {_eth(bal)} at block {block}, below {_eth(limit)}. "
                            f"Top it up (spec M12; D4).", subject=subject, evidence=evidence)

    def spend_findings(self, w: Wallet, m: dict, block: int) -> list[Finding]:
        subject = {"wallet": w.address, "role": w.role}
        evidence = {**evidence_of(m), "block": block}
        out = []
        runway = min((r for r in (m["runway_trailing_days"], m["runway_base_fee_days"]) if r is not None),
                     default=None)
        sev = None
        if runway is not None:
            if w.runway_page_days is not None and runway < w.runway_page_days:
                sev = "page"
            elif w.runway_warn_days is not None and runway < w.runway_warn_days:
                sev = "warn"
        text = (f"balance {_eth(m['balance_wei'])}; trailing 7-day spend {_eth(m['avg_daily_spend_wei'])}/day → "
                f"runway {_days(m['runway_trailing_days'])}; at the current base fee "
                f"{float(m['base_fee_wei']) / 1e9:.3f} gwei + observed priority fee "
                f"{float(m['avg_priority_fee_wei']) / 1e9:.3f} gwei × {float(m['avg_daily_gas']) / 1e6:.2f} Mgas/day → "
                f"runway {_days(m['runway_base_fee_days'])}")
        key = f"{w.address}:runway"
        if sev is None:
            out.append(Finding(check=self.id, key=key, severity="info", title=f"{w.label} runway restored",
                               body=f"{w.label} {w.address} at block {block}: {text}.", subject=subject,
                               evidence=evidence, resolve=True))
        else:
            which = "trailing spend" if runway == m["runway_trailing_days"] else "current base fee"
            limit = w.runway_page_days if sev == "page" else w.runway_warn_days
            out.append(Finding(check=self.id, key=key, severity=sev,
                               title=f"{w.label} runway {_days(runway)} (< {limit} d, from {which})",
                               body=f"{w.label} {w.address} at block {block}: {text}. Fund it (spec M12).",
                               subject=subject, evidence=evidence))
        if w.spend_warn is not None:
            key = f"{w.address}:daily-spend"
            if m["day_spend_wei"] > w.spend_warn:
                out.append(Finding(check=self.id, key=key, severity="warn",
                                   title=f"{w.label} daily spend {_eth(m['day_spend_wei'])} > {_eth(w.spend_warn)}",
                                   body=f"{w.label} {w.address} spent {_eth(m['day_spend_wei'])} per day over the "
                                        f"last {m['day_blocks']} blocks (to block {block}), above "
                                        f"{_eth(w.spend_warn)}; {text}.", subject=subject, evidence=evidence))
            else:
                out.append(Finding(check=self.id, key=key, severity="info", title=f"{w.label} daily spend normal",
                                   body=f"{w.label} {w.address} spent {_eth(m['day_spend_wei'])} per day over the "
                                        f"last {m['day_blocks']} blocks (to block {block}).", subject=subject,
                                   evidence=evidence, resolve=True))
        return out

    # ---- spend: transaction set, proven complete

    def sync(self, ctx: Ctx, wallet: str, block: int) -> None:
        """Bring m12_tx for `wallet` up to `block`, or raise SpendUnknown (nothing written)."""
        conn = ctx.db.conn
        (n_now,) = nonces(ctx, [wallet], block)
        row = conn.execute("SELECT synced_block, next_nonce, history_from FROM m12_sync WHERE wallet=?",
                           (wallet,)).fetchone()
        lo_window = max(block - self.config["window_blocks"], 0)
        backfill = (row is None or row[0] < lo_window or row[0] > block or n_now < row[1])
        if not backfill and n_now == row[1]:
            conn.execute("UPDATE m12_sync SET synced_block=? WHERE wallet=?", (block, wallet))
            self.prune(ctx, wallet, block)
            return
        lo = lo_window if backfill else row[0]
        first = None if backfill else row[1]
        verified = self.verified(ctx, wallet, self.candidates(wallet, lo, block), block)
        window = {t["nonce"]: t for t in verified if lo < t["block"] <= block}
        if backfill:
            start = min(window) if window else n_now
            before = [t for t in verified if t["block"] <= lo and t["nonce"] == start - 1]
            if start > 0 and not before:
                raise SpendUnknown(f"no verified transaction with nonce {start - 1} at or before block {lo} "
                                   f"(the window's lower bound)")
        else:
            start = first
        missing = sorted(set(range(start, n_now)) - set(window))
        extra = sorted(set(window) - set(range(start, n_now)))
        if missing or extra:
            raise SpendUnknown(f"transaction list incomplete for nonces {start}..{n_now - 1} in blocks "
                               f"{lo + 1}..{block}: missing {missing[:5]}{'…' if len(missing) > 5 else ''}"
                               f"{f', unexpected {extra[:5]}' if extra else ''}")
        if backfill:
            conn.execute("DELETE FROM m12_tx WHERE wallet=?", (wallet,))
        conn.executemany(
            "INSERT OR REPLACE INTO m12_tx (wallet, tx_hash, block, block_hash, nonce, gas_used, gas_price, fee, "
            "status, base_fee) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(wallet, t["hash"], t["block"], t["block_hash"], t["nonce"], str(t["gas_used"]), str(t["gas_price"]),
              str(t["gas_used"] * t["gas_price"]), t["status"], str(t["base_fee"])) for t in window.values()])
        conn.execute("INSERT INTO m12_sync (wallet, synced_block, next_nonce, history_from) VALUES (?,?,?,?) "
                     "ON CONFLICT(wallet) DO UPDATE SET synced_block=excluded.synced_block, "
                     "next_nonce=excluded.next_nonce, history_from=CASE WHEN ? THEN excluded.history_from "
                     "ELSE history_from END", (wallet, block, n_now, lo, int(backfill)))
        self.prune(ctx, wallet, block)

    def prune(self, ctx: Ctx, wallet: str, block: int) -> None:
        ctx.db.conn.execute("DELETE FROM m12_tx WHERE wallet=? AND block <= ?",
                            (wallet, block - self.config["window_blocks"]))

    def candidates(self, wallet: str, lo: int, block: int) -> list[dict]:
        """Blockscout items sent by `wallet`, newest first, down to the first page reaching block <= lo.
        Untrusted: only used to name hashes to verify."""
        base = self.config["blockscout_url"].rstrip("/") + f"/api/v2/addresses/{wallet}/transactions"
        params: dict = {"filter": "from"}
        out: list[dict] = []
        for _ in range(self.config["max_pages"]):
            url = base + "?" + urllib.parse.urlencode(params)
            try:
                page = self.http_get(url)
            except (OSError, ValueError, urllib.error.URLError) as exc:
                raise SpendUnknown(f"Blockscout request failed ({type(exc).__name__}: {exc})") from None
            items = page.get("items") if isinstance(page, dict) else None
            if not isinstance(items, list):
                raise SpendUnknown(f"Blockscout returned an unexpected shape: {str(page)[:200]}")
            reached = False
            for it in items:
                try:
                    h, b = str(it["hash"]).lower(), int(it["block_number"])
                    sender = str((it.get("from") or {}).get("hash", "")).lower()
                except (KeyError, TypeError, ValueError):
                    raise SpendUnknown(f"Blockscout item without hash/block_number: {str(it)[:200]}") from None
                if sender == wallet and b <= block:
                    out.append({"hash": h, "claimed_block": b})
                reached = reached or b <= lo
            nxt = page.get("next_page_params")
            if reached or not nxt:
                return out
            params = {"filter": "from", **{k: v for k, v in nxt.items() if v is not None}}
        raise SpendUnknown(f"more than {self.config['max_pages']} Blockscout pages needed to reach block {lo}")

    def verified(self, ctx: Ctx, wallet: str, cands: list[dict], block: int) -> list[dict]:
        """The candidates the chain confirms: mined by `wallet` in a canonical block <= `block`."""
        hashes = list(dict.fromkeys(c["hash"] for c in cands))
        if not hashes:
            return []
        txs = raw_batch(ctx, "eth_getTransactionByHash", [[h] for h in hashes])
        rcs = raw_batch(ctx, "eth_getTransactionReceipt", [[h] for h in hashes])
        good = []
        for h, tx, rc in zip(hashes, txs, rcs):
            if not isinstance(tx, dict) or not isinstance(rc, dict) or not tx.get("blockNumber"):
                continue  # not a mined transaction (an untrusted candidate)
            b = _int(tx["blockNumber"], "blockNumber")
            if (str(tx.get("from", "")).lower() != wallet or str(rc.get("from", "")).lower() != wallet
                    or _int(rc.get("blockNumber"), "receipt blockNumber") != b or b > block
                    or str(tx.get("blockHash", "")).lower() != str(rc.get("blockHash", "")).lower()):
                continue
            good.append({"hash": h, "block": b, "block_hash": str(tx["blockHash"]).lower(),
                         "nonce": _int(tx["nonce"], "nonce"), "gas_used": _int(rc["gasUsed"], "gasUsed"),
                         "gas_price": _int(rc["effectiveGasPrice"], "effectiveGasPrice"),
                         "status": _int(rc.get("status", "0x1"), "status")})
        canon = headers(ctx, [t["block"] for t in good]) if good else {}
        out = []
        for t in good:
            hdr = canon[t["block"]]
            if hdr["hash"].lower() == t["block_hash"]:
                out.append({**t, "base_fee": _int(hdr.get("baseFeePerGas"), f"baseFeePerGas of block {t['block']}")})
        return out

    def metrics(self, ctx: Ctx, wallet: str, bal: int, block: int) -> dict:
        c = self.config
        lo_w, lo_d = max(block - c["window_blocks"], 0), max(block - c["day_blocks"], 0)
        hdr = headers(ctx, [block, lo_w, lo_d])
        ts = {b: _int(h.get("timestamp"), f"timestamp of block {b}") for b, h in hdr.items()}
        span_w, span_d = ts[block] - ts[lo_w], ts[block] - ts[lo_d]
        if span_w <= 0 or span_d <= 0:
            raise SpendUnknown(f"non-increasing block timestamps ({lo_w}: {ts[lo_w]}, {block}: {ts[block]})")
        rows = ctx.db.conn.execute("SELECT block, gas_used, fee, base_fee FROM m12_tx WHERE wallet=? AND block > ? "
                                   "AND block <= ?", (wallet, lo_w, block)).fetchall()
        fees_w = sum(int(r[2]) for r in rows)
        gas_w = sum(int(r[1]) for r in rows)
        fees_d = sum(int(r[2]) for r in rows if r[0] > lo_d)
        burnt_w = sum(int(r[1]) * int(r[3]) for r in rows)
        fee_now = base_fee(ctx, c["base_fee_blocks"])
        # the wallet's priority fee (effectiveGasPrice - the block's base fee), gas-weighted over the window; with
        # no transaction in the window, the configured fallback (flagged); never below 0, so the projection with a
        # tip is never more optimistic than the base fee alone
        if c["projection_tip"] == "none":
            tip, tip_source = Fraction(0), "none (base fee only)"
        elif gas_w:
            tip, tip_source = max(Fraction(fees_w - burnt_w, gas_w), Fraction(0)), f"observed over {len(rows)} txs"
        else:
            tip, tip_source = Fraction(c["fallback_tip_wei"]), "fallback_tip_wei (no transaction in the window)"
        avg_spend = Fraction(fees_w * 86_400, span_w)
        avg_gas = Fraction(gas_w * 86_400, span_w)
        projected = avg_gas * (fee_now + tip)
        return {
            "balance_wei": bal, "window_blocks": c["window_blocks"], "day_blocks": c["day_blocks"],
            "window_txs": len(rows), "window_fees_wei": fees_w, "window_gas": gas_w, "window_seconds": span_w,
            "avg_daily_spend_wei": avg_spend, "avg_daily_gas": avg_gas, "day_spend_wei": Fraction(fees_d * 86_400, span_d),
            "base_fee_wei": fee_now, "base_fee_blocks": c["base_fee_blocks"], "avg_priority_fee_wei": tip,
            "tip_source": tip_source, "projected_daily_spend_wei": projected,
            "runway_trailing_days": Fraction(bal) / avg_spend if avg_spend else None,
            "runway_base_fee_days": Fraction(bal) / projected if projected else None,
        }

    def digest(self, ctx: Ctx) -> list[str]:
        out = []
        for w in self.wallets:
            m = self.last.get(w.address)
            if not m:
                continue
            line = f"M12 {w.label} {w.address}: {_eth(m['balance'])} at block {m['block']}"
            if "runway_trailing_days" in m:
                line += (f", spend {_eth(m['avg_daily_spend_wei'])}/day (7 d), runway "
                         f"{_days(m['runway_trailing_days'])} trailing / {_days(m['runway_base_fee_days'])} at "
                         f"{float(m['base_fee_wei']) / 1e9:.3f} gwei")
            elif "spend" in m:
                line += f", spend {m['spend']}"
            out.append(line)
        return out
