"""CP2c fixtures (M12, M8, M13): a FakeChain with transactions, receipts, nonces, block headers and base fees,
and a fake Blockscout v2 address-transactions API served from it.

`FakeChain` (frozen) answers none of `eth_getTransactionReceipt`, `eth_getTransactionCount`,
`eth_getBlockReceipts`, `eth_feeHistory` or a block header with `timestamp`/`baseFeePerGas`, and its `reorg()`
keeps `set_balance` and `stub_call` entries above the fork. `WalletChain` adds them here (test-local, README §8
"lane fixtures"); balances, call stubs and transactions of the deleted blocks leave with the reorg.

  chain = WalletChain(); chain.mine()
  chain.send(ORACLE, PROXY, gas_used=150_000, gas_price=gwei(1.15))   # mined at the pending block; fee debited
  chain.internal_transfer(RETIRED_SLOT4, wei)                          # value arrives with no tx to the address
  chain.set_base_fee(gwei(5))                                          # from the pending block on
  bs = FakeBlockscout(chain); check.http_get = bs                      # M12 transaction candidates
"""

from __future__ import annotations

import urllib.parse

from fakechain import PROXY, FakeChain
from harness import make_worker

from eb_storage import u256

# Spec §3 M12, §5, D3, D10 (dated values: tests only; the checks read them from config)
ORACLE = "0x16a503c901615e6e21aa78cfcaff11822fc2b617"
LIQUIDATOR = "0x0431371ad8eda67ba2ef6489ab003b3a7f0d95e0"
DAO_SAFE = "0xb35096b074fdb9bbac63e3adae0bbde512b2e6b6"
CSSV = "0xe018d31f120a637828f46afd6c64ec099d960546"
RETIRED_SLOT4 = "0x3a98ee5f80268ed91f8a5880d93468b76a9f3bb4"
RETIRED_LIQUIDATOR = "0x4bc656b34de23896fa6069c9862f355b740401af"
FUNDER = "0xd63983cea76a13d7b01d8f7f14d5837330382d42"
HELPER = "0x" + "4e" * 20  # a contract that forwards value (internal transfer)
ETH = 10**18
DAY = 7_200  # blocks at 12 s


def gwei(x: float) -> int:
    return int(round(x * 10**9))


def eth(x: float) -> int:
    return int(round(x * ETH))


M12_CFG = {"oracle_wallet": ORACLE, "liquidator_wallet": LIQUIDATOR, "fallback_tip_wei": 10**9}  # 1 gwei, 2026-10-21
M8_CFG = {"cssv": CSSV, "seed_holder": DAO_SAFE}
M13_CFG = {"addresses": {RETIRED_SLOT4: "old oracle slot 4 + EB updater (key leaked)",
                         RETIRED_LIQUIDATOR: "old liquidator (drained)"}}

TOTAL_SUPPLY = "0x18160ddd"
BALANCE_OF = "0x70a08231"


def _topic(addr: str) -> str:
    return "0x" + addr[2:].lower().rjust(64, "0")


class WalletChain(FakeChain):
    def __init__(self, start_block: int = 1000, genesis_ts: int = 1_790_000_000, base_fee: int = gwei(1.15)):
        super().__init__(start_block)
        self.genesis_ts = genesis_ts
        self.base_fees: list[tuple[int, int]] = [(0, base_fee)]
        self.wtxs: dict[str, dict] = {}  # tx hash -> tx + receipt fields
        self.fail_methods: set[str] = set()  # JSON-RPC methods answered with an error
        self.empty_methods: set[str] = set()  # JSON-RPC methods answered with a null result

    # ---- scenario builders (effective from the pending block, like stub_call/set_balance)

    def timestamp(self, n: int) -> int:
        return self.genesis_ts + 12 * n

    def set_base_fee(self, wei: int) -> None:
        self.base_fees.append((self.number, wei))

    def base_fee(self, n: int) -> int:
        return [w for b, w in self.base_fees if b <= n][-1]

    def balance_now(self, addr: str) -> int:
        return self._balance(addr, self.number)

    def credit(self, addr: str, delta: int) -> None:
        self.set_balance(addr, self.balance_now(addr) + delta)

    def send(self, sender: str, to: str, gas_used: int = 150_000, gas_price: int | None = None, value: int = 0,
             status: int = 1, logs: list[dict] | None = None, credit_to: str | None = None) -> str:
        """A transaction mined in the pending block: nonce = the sender's count so far; the fee (and the value
        on success) is debited from the sender, the value credited to `credit_to` (default `to`)."""
        sender, to = sender.lower(), to.lower()
        gas_price = self.base_fee(self.number) if gas_price is None else gas_price
        nonce = sum(1 for t in self.wtxs.values() if t["from"] == sender)
        h = self.new_tx(sender)
        fee = gas_used * gas_price
        self.credit(sender, -(fee + (value if status else 0)))
        if status and value:
            self.credit((credit_to or to).lower(), value)
        self.wtxs[h] = {"hash": h, "from": sender, "to": to, "value": value, "nonce": nonce, "block": self.number,
                        "index": self.tx_index, "gas_used": gas_used, "gas_price": gas_price, "status": status,
                        "logs": [dict(lg, address=lg.get("address", to).lower()) for lg in (logs or [])]}
        return h

    def internal_transfer(self, to: str, value: int, via: str = HELPER, funder: str = FUNDER) -> str:
        """Value reaches `to` through a contract's internal call: the transaction goes to `via`, never to `to`;
        `via` logs a Forwarded(to) event naming the recipient (best-effort attribution)."""
        log = {"address": via, "topics": ["0x" + "fe" * 32, _topic(to)], "data": "0x" + u256(value).hex()}
        return self.send(funder, via, gas_used=60_000, value=value, logs=[log], credit_to=to)

    def reorg(self, from_block: int) -> None:
        super().reorg(from_block)
        self.balances = {a: [(b, w) for b, w in e if b < from_block] for a, e in self.balances.items()}
        self.base_fees = [(b, w) for b, w in self.base_fees if b < from_block] or self.base_fees[:1]
        self.wtxs = {h: t for h, t in self.wtxs.items() if t["block"] < from_block}
        self.call_stubs = {k: [e for e in v if e[0] < from_block] for k, v in self.call_stubs.items()}

    # ---- json-rpc

    def _tx_obj(self, t: dict) -> dict:
        return {"hash": t["hash"], "from": t["from"], "to": t["to"], "value": hex(t["value"]), "nonce": hex(t["nonce"]),
                "blockNumber": hex(t["block"]), "blockHash": self.hash(t["block"]), "transactionIndex": hex(t["index"]),
                "input": "0x", "gas": hex(t["gas_used"] * 2), "gasPrice": hex(t["gas_price"])}

    def _receipt(self, t: dict) -> dict:
        logs = [{"address": lg["address"], "topics": lg["topics"], "data": lg["data"], "blockNumber": hex(t["block"]),
                 "blockHash": self.hash(t["block"]), "transactionHash": t["hash"], "logIndex": hex(i),
                 "removed": False} for i, lg in enumerate(t["logs"])]
        return {"transactionHash": t["hash"], "from": t["from"], "to": t["to"], "blockNumber": hex(t["block"]),
                "blockHash": self.hash(t["block"]), "transactionIndex": hex(t["index"]), "gasUsed": hex(t["gas_used"]),
                "effectiveGasPrice": hex(t["gas_price"]), "status": hex(t["status"]), "logs": logs}

    def _mined(self, h: str) -> dict | None:
        t = self.wtxs.get(h)
        return t if t is not None and t["block"] <= self.sealed else None

    def _handle(self, m: str, p: list):
        if m in self.fail_methods:
            raise ValueError(f"{m} failed (test)")
        if m in self.empty_methods:
            return None
        if m == "eth_getBlockByNumber":
            n = self._blk(p[0])
            if n > self.sealed:
                return None
            txs = sorted((t for t in self.wtxs.values() if t["block"] == n), key=lambda t: t["index"])
            return {"number": hex(n), "hash": self.hash(n), "timestamp": hex(self.timestamp(n)),
                    "baseFeePerGas": hex(self.base_fee(n)),
                    "transactions": [self._tx_obj(t) if p[1] else t["hash"] for t in txs]}
        if m == "eth_getTransactionCount":
            n = self._blk(p[1])
            return hex(sum(1 for t in self.wtxs.values() if t["from"] == p[0].lower() and t["block"] <= n))
        if m == "eth_getTransactionByHash" and p[0] in self.wtxs:
            t = self._mined(p[0])
            return self._tx_obj(t) if t else None
        if m == "eth_getTransactionReceipt":
            t = self._mined(p[0])
            return self._receipt(t) if t else None
        if m == "eth_getBlockReceipts":
            n = self._blk(p[0])
            if n > self.sealed:
                return None
            txs = sorted((t for t in self.wtxs.values() if t["block"] == n), key=lambda t: t["index"])
            return [self._receipt(t) for t in txs]
        if m == "eth_feeHistory":
            count, newest = int(p[0], 16), self._blk(p[1])
            oldest = max(0, newest - count + 1)
            return {"oldestBlock": hex(oldest),
                    "baseFeePerGas": [hex(self.base_fee(n)) for n in range(oldest, newest + 2)],
                    "gasUsedRatio": [0.5] * (newest - oldest + 1)}
        return super()._handle(m, p)


class FakeBlockscout:
    """`GET {base}/api/v2/addresses/{addr}/transactions?filter=from[&block_number=…&index=…]` served from a
    WalletChain, newest first, `page_size` items per page. Knobs: `down` (raise), `hide` (hashes not listed:
    indexer gap), `lag` (blocks below the head not indexed yet), `forged` (extra untrusted items, sorted in), `prepend` (untrusted
    items put first on the first page, unsorted), `calls`."""

    def __init__(self, chain: WalletChain, page_size: int = 50):
        self.chain, self.page_size = chain, page_size
        self.down: Exception | None = None
        self.hide: set[str] = set()
        self.lag = 0
        self.forged: list[dict] = []
        self.prepend: list[dict] = []
        self.calls: list[str] = []

    def item(self, t: dict) -> dict:
        return {"hash": t["hash"], "block_number": t["block"], "position": t["index"], "nonce": t["nonce"],
                "from": {"hash": t["from"]}, "to": {"hash": t["to"]}, "status": "ok" if t["status"] else "error",
                "gas_used": str(t["gas_used"]), "gas_price": str(t["gas_price"]),
                "fee": {"type": "actual", "value": str(t["gas_used"] * t["gas_price"])}}

    def __call__(self, url: str) -> dict:
        self.calls.append(url)
        if self.down is not None:
            raise self.down
        parts = urllib.parse.urlparse(url)
        q = dict(urllib.parse.parse_qsl(parts.query))
        addr = parts.path.split("/addresses/")[1].split("/")[0].lower()
        assert q.get("filter") == "from", url
        head = self.chain.sealed - self.lag
        items = [self.item(t) for t in self.chain.wtxs.values()
                 if t["from"] == addr and t["block"] <= head and t["hash"] not in self.hide]
        items += [dict(f) for f in self.forged if f["from"]["hash"].lower() == addr]
        items.sort(key=lambda i: (i["block_number"], i["position"]), reverse=True)
        if "block_number" in q:
            cur = (int(q["block_number"]), int(q["index"]))
            items = [i for i in items if (i["block_number"], i["position"]) < cur]
        else:
            items = [dict(f) for f in self.prepend] + items
        page, rest = items[: self.page_size], items[self.page_size:]
        nxt = None
        if rest:
            last = page[-1]
            nxt = {"block_number": last["block_number"], "index": last["position"], "items_count": self.page_size,
                   "filter": "from"}
        return {"items": page, "next_page_params": nxt}


def spend_history(chain: WalletChain, wallet: str = ORACLE, days: int = 7, txs_per_day: int = 2,
                  gas_per_day: int = 15_500_000, gas_price: int | None = None, older: int = 2) -> list[str]:
    """`older` transactions one day apart, then `days` days of `txs_per_day` transactions (gas_per_day split
    evenly), the last one in the pending block; mine once afterwards to seal it. Spec §3 M12 measured burn:
    15.5 Mgas/day at ~1.15 gwei (the WalletChain default base fee)."""
    out = []
    for _ in range(older):
        out.append(chain.send(wallet, PROXY, gas_used=gas_per_day // txs_per_day, gas_price=gas_price))
        chain.mine(DAY)
    for _ in range(days * txs_per_day):
        chain.mine(DAY // txs_per_day)
        out.append(chain.send(wallet, PROXY, gas_used=gas_per_day // txs_per_day, gas_price=gas_price))
    return out


def stub_cssv(chain: FakeChain, supply: int, seed: int, cssv: str = CSSV, holder: str = DAO_SAFE) -> None:
    """cSSV.totalSupply() and cSSV.balanceOf(holder), from the pending block on."""
    chain.stub_call(cssv, TOTAL_SUPPLY, u256(supply))
    chain.stub_call(cssv, BALANCE_OF + _topic(holder)[2:], u256(seed))


def wallet_worker(tmp_path, only, chain=None, checks=None, blockscout=True, **over):
    """make_worker on a WalletChain with the lane's mainnet-shaped config for `only`; M12 gets a FakeBlockscout
    (returned as w.bs)."""
    if chain is None:
        chain = WalletChain()
        chain.mine()
    cfg = {"m12": M12_CFG, "m8": M8_CFG, "m13": M13_CFG}
    cfg = {cid: dict(cfg[cid], **(checks or {}).get(cid, {})) for cid in only}
    chain, w = make_worker(tmp_path, only=only, chain=chain, checks=cfg, **over)
    w.bs = None
    if "m12" in w.checks and blockscout:
        w.bs = FakeBlockscout(chain)
        w.checks["m12"].http_get = w.bs
    return chain, w


__all__ = ["PROXY"]
