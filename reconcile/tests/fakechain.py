"""In-memory chain + minimal SSVNetwork EB model for worker tests.

MiniSSV is written from the pinned Solidity (SSVValidators._bulkRegisterValidator,
_bulkRemoveValidator, SSVClusters._updateClusterBalanceInternal, liquidate,
reactivate, migrateClusterToETH), independently of eb_model.py. It emits ABI-encoded
logs and writes the raw storage words the worker reads, so the worker's decoder,
RPC client, database, replay and alerting run unmodified against it.

Generic helpers for any check (checks/README.md "Test helpers"):
  chain.emit_event(kind_or_topic0, topics, data, address)   inject any log
  chain.stub_call(to, data_or_selector, result=… | revert=…) eth_call answers, effective from the pending block
  chain.set_balance(address, wei)                           eth_getBalance, effective from the pending block
  chain.add_tx(sender, to, input, value)                    a mined transaction for eth_getTransactionByHash
  FakeClock                                                 a controllable clock for Worker(clock=…)
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eb_events import TOPIC0  # noqa: E402
from eb_storage import (  # noqa: E402
    Cluster,
    cluster_eb_slot,
    cluster_id,
    eth_cluster_slot,
    ssv_cluster_slot,
    u256,
)
from rpc import Rpc, RpcError  # noqa: E402

PROXY = "0xdd9bc35ae942ef0cfa76930954a156b3ff30a4e1"
BASE = 10_000


# ---------------------------------------------------------------- ABI encoding (tests only)


def _enc(types: list[str], values: list) -> bytes:
    head, tail = [], []
    n_head = sum(5 if t == "cluster" else 1 for t in types)
    for t, v in zip(types, values):
        if t == "uint":
            head.append(u256(v))
        elif t == "cluster":
            head += [u256(x) for x in (v.validator_count, v.network_fee_index, v.index, int(v.active), v.balance)]
        else:
            offset = 32 * n_head + sum(len(x) for x in tail)
            head.append(u256(offset))
            if t == "bytes":
                pad = (-len(v)) % 32
                tail.append(u256(len(v)) + v + b"\0" * pad)
            elif t == "uint64[]":
                tail.append(u256(len(v)) + b"".join(u256(x) for x in v))
    return b"".join(head) + b"".join(tail)


def _topic_addr(a: str) -> str:
    return "0x" + a[2:].rjust(64, "0")


# ---------------------------------------------------------------- chain


class FakeChain:
    def __init__(self, start_block: int = 1000, chain_id: int = 1):
        self.chain_id = chain_id
        self.number = start_block
        self.salt = {}  # block -> fork salt (changes the hash on a reorg)
        self.logs: list[dict] = []
        self.writes: list[tuple[int, int, int]] = []  # (block, slot, value)
        self.tx_count = 0
        self.log_index = 0
        self.tx_index = 0
        self.max_range: int | None = None
        self.down = False
        self.drop_logs_in: tuple[int, int] | None = None  # silently omit logs in this range (truncation bug)
        self.senders: dict[str, str] = {}
        self.txs: dict[str, dict] = {}  # tx hash -> {from, to, input, value, gas, blockNumber}
        self.call_stubs: dict[tuple[str, str], list[tuple[int, str, str]]] = {}  # (to, data) -> [(from, kind, hex)]
        self.balances: dict[str, list[tuple[int, int]]] = {}  # address -> [(from block, wei)]
        self.calls: list[tuple[str, str, int]] = []  # every eth_call (to, data, block) the chain answered

    def hash(self, n: int) -> str:
        return "0x%02x%062x" % (self.salt.get(n, 0), n)

    def mine(self, n: int = 1) -> int:
        self.number += n
        self.log_index = 0
        self.tx_index = 0
        return self.number

    def new_tx(self, sender: str) -> str:
        self.tx_count += 1
        h = "0x%064x" % (0xA0000000 + self.tx_count)
        self.senders[h] = sender
        self.tx_index += 1
        return h

    def emit(self, tx: str, kind: str, topics: list[str], data: bytes) -> None:
        self.logs.append({
            "address": PROXY, "topics": [TOPIC0[kind]] + topics, "data": "0x" + data.hex(),
            "blockNumber": hex(self.number), "blockHash": self.hash(self.number), "logIndex": hex(self.log_index),
            "transactionIndex": hex(self.tx_index), "transactionHash": tx, "removed": False,
        })
        self.log_index += 1

    def emit_event(self, kind: str, topics: list[str], data: bytes = b"", address: str = PROXY,
                   tx: str | None = None) -> str:
        """Inject any log; `kind` is an eb_events name or a raw 0x topic0. Returns the tx hash."""
        tx = tx or self.new_tx("0x" + "ee" * 20)
        topic0 = kind if kind.startswith("0x") else TOPIC0[kind]
        self.logs.append({
            "address": address.lower(), "topics": [topic0] + topics, "data": "0x" + data.hex(),
            "blockNumber": hex(self.number), "blockHash": self.hash(self.number), "logIndex": hex(self.log_index),
            "transactionIndex": hex(self.tx_index), "transactionHash": tx, "removed": False,
        })
        self.log_index += 1
        return tx

    def stub_call(self, to: str, data: str | bytes, result: bytes | str | None = None,
                  revert: bytes | str | None = None) -> None:
        """Answer eth_call to `to` whose calldata equals `data` (or starts with it when `data` is a
        4-byte selector) from the pending block on: return `result`, or revert with `revert` data."""
        data = data if isinstance(data, str) else "0x" + data.hex()
        kind, val = ("revert", revert) if revert is not None else ("result", result if result is not None else b"")
        val = val if isinstance(val, str) else "0x" + val.hex()
        self.call_stubs.setdefault((to.lower(), data.lower()), []).append((self.number, kind, val.lower()))

    def set_balance(self, address: str, wei: int) -> None:
        self.balances.setdefault(address.lower(), []).append((self.number, wei))

    def add_tx(self, sender: str, to: str, input: str, value: int = 0) -> str:
        h = self.new_tx(sender)
        self.txs[h] = {"hash": h, "from": sender, "to": to, "input": input, "value": hex(value), "gas": hex(500_000),
                       "blockNumber": hex(self.number)}
        return h

    def _eth_call(self, obj: dict, block: int) -> str:
        to, data = (obj.get("to") or "").lower(), obj.get("data", obj.get("input", "0x")).lower()
        self.calls.append((to, data, block))
        entries = self.call_stubs.get((to, data)) or self.call_stubs.get((to, data[:10])) or []
        live = [e for e in entries if e[0] <= block]
        if not live:
            raise ValueError(f"no eth_call stub for {to} {data[:10]} at {block}")
        _, kind, val = live[-1]
        if kind == "revert":
            raise Revert(val)
        return val

    def _balance(self, address: str, block: int) -> int:
        val = 0
        for b, wei in self.balances.get(address.lower(), []):
            if b <= block:
                val = wei
        return val

    def write(self, slot: int, value: int) -> None:
        self.writes.append((self.number, slot, value))

    def storage(self, slot: int, block: int) -> int:
        val = 0
        for b, s, v in self.writes:
            if s == slot and b <= block:
                val = v
        return val

    def reorg(self, from_block: int) -> None:
        """Drop every log/write at or above from_block and change those blocks' hashes."""
        self.logs = [lg for lg in self.logs if int(lg["blockNumber"], 16) < from_block]
        self.writes = [w for w in self.writes if w[0] < from_block]
        for n in range(from_block, self.number + 1):
            self.salt[n] = self.salt.get(n, 0) + 1
        self.number = from_block - 1
        self.mine()

    # ---- json-rpc

    def dispatch(self, req: dict) -> dict:
        m, p = req["method"], req["params"]
        try:
            res = self._handle(m, p)
            return {"jsonrpc": "2.0", "id": req["id"], "result": res}
        except Revert as exc:
            return {"jsonrpc": "2.0", "id": req["id"],
                    "error": {"code": 3, "message": "execution reverted", "data": exc.data}}
        except ValueError as exc:
            return {"jsonrpc": "2.0", "id": req["id"], "error": {"code": -32000, "message": str(exc)}}

    @property
    def sealed(self) -> int:
        """`number` is the pending block that receives new txs; everything below it is sealed."""
        return self.number - 1

    def _blk(self, tag) -> int:
        if tag in ("latest", "finalized", "safe"):
            return self.sealed
        return int(tag, 16)

    def _handle(self, m: str, p: list):
        if m == "eth_chainId":
            return hex(self.chain_id)
        if m == "eth_blockNumber":
            return hex(self.sealed)
        if m == "eth_getBlockByNumber":
            n = self._blk(p[0])
            return None if n > self.sealed else {"number": hex(n), "hash": self.hash(n)}
        if m == "eth_getLogs":
            f = p[0]
            lo, hi = int(f["fromBlock"], 16), int(f["toBlock"], 16)
            if self.max_range and hi - lo + 1 > self.max_range:
                raise ValueError(f"block range too large, max {self.max_range}")
            wanted = set(f["topics"][0])
            out = []
            for lg in self.logs:
                b = int(lg["blockNumber"], 16)
                if lo <= b <= hi and lg["topics"][0] in wanted and lg["address"] == f["address"]:
                    if self.drop_logs_in and self.drop_logs_in[0] <= b <= self.drop_logs_in[1]:
                        continue
                    out.append(dict(lg, blockHash=self.hash(b)))
            return out
        if m == "eth_getStorageAt":
            return hex(self.storage(int(p[1], 16), self._blk(p[2])))
        if m == "eth_getTransactionByHash":
            if p[0] in self.txs:
                return self.txs[p[0]]
            return {"from": self.senders[p[0]]} if p[0] in self.senders else None
        if m == "eth_getCode":
            return "0x"
        if m == "eth_call":
            return self._eth_call(p[0], self._blk(p[1]))
        if m == "eth_getBalance":
            return hex(self._balance(p[0], self._blk(p[1])))
        raise ValueError(f"unsupported method {m}")


class Revert(Exception):
    def __init__(self, data: str):
        super().__init__(data)
        self.data = data


class FakeClock:
    """Controllable time for Worker(clock=…): starts at `start`, moves only with advance()."""

    def __init__(self, start: float = 1_800_000_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> float:
        self.now += seconds
        return self.now


class FakeRpc(Rpc):
    def __init__(self, chain: FakeChain, batch_size: int = 50):
        super().__init__("fake://", timeout=1, retries=0, batch_size=batch_size)
        self.chain = chain
        self.calls = 0

    def _post(self, payload):
        if self.chain.down:
            raise RpcError("transport error: URLError", transport=True)
        self.calls += 1
        if isinstance(payload, list):
            return [self.chain.dispatch(r) for r in payload]
        return self.chain.dispatch(payload)


# ---------------------------------------------------------------- contract model


def eb_to_vunits(eb: int) -> int:
    return -(-eb * BASE // 32)


class MiniSSV:
    def __init__(self, chain: FakeChain):
        self.c = chain
        self.clusters: dict[bytes, dict] = {}
        self.latest_root = 0
        self.roots: dict[int, str] = {}
        self.guards = True  # PR #614 zero-validator guards deployed

    def _get(self, owner, ops) -> tuple[bytes, dict]:
        cid = cluster_id(owner, ops)
        st = self.clusters.setdefault(cid, {
            "owner": owner, "ops": tuple(ops), "version": "ETH", "count": 0, "active": True, "balance": 10**21,
            "nfi": 0, "index": 0, "v": 0, "root": 0, "upd": 0,
        })
        return cid, st

    def _cluster(self, st) -> Cluster:
        return Cluster(st["count"], st["nfi"], st["index"], st["active"], st["balance"])

    def _persist(self, cid, st) -> None:
        cl = self._cluster(st)
        h = int.from_bytes(cl.hash_data(), "big")
        if st["version"] == "ETH":
            self.c.write(eth_cluster_slot(cid), h)
            self.c.write(ssv_cluster_slot(cid), 0)
        else:
            self.c.write(ssv_cluster_slot(cid), h)
        self.c.write(cluster_eb_slot(cid), st["v"] | (st["root"] << 64) | (st["upd"] << 128))

    def _touch(self, st) -> None:
        # fee settlement moves the indexes on most cluster operations
        st["index"] += 1
        st["nfi"] += 1

    def legacy_ssv_cluster(self, owner, ops, count) -> bytes:
        cid, st = self._get(owner, ops)
        st.update(version="SSV", count=count)
        self._persist(cid, st)
        return cid

    def register(self, owner, ops, pubkeys: list[bytes], shares: bytes = b"", sender=None) -> str:
        cid, st = self._get(owner, ops)
        assert st["version"] == "ETH" and st["active"]
        tx = self.c.new_tx(sender or owner)
        n = len(pubkeys)
        st["count"] += n
        self._touch(st)
        if st["v"] > 0:
            st["v"] += n * BASE
        self._persist(cid, st)
        cl = self._cluster(st)
        for pk in pubkeys:
            self.c.emit(tx, "ValidatorAdded", [_topic_addr(owner)],
                        _enc(["uint64[]", "bytes", "bytes", "cluster"], [list(ops), pk, shares, cl]))
        self.c.mine()
        return tx

    def remove(self, owner, ops, pubkeys: list[bytes]) -> str:
        cid, st = self._get(owner, ops)
        tx = self.c.new_tx(owner)
        n = len(pubkeys)
        if st["version"] == "ETH":
            if st["active"]:
                self._touch(st)
            st["count"] -= n
            if st["v"] > 0:
                st["v"] -= n * BASE
                if st["count"] == 0:
                    st["v"] = 0
        else:
            st["count"] -= n
            if st["count"] == 0 and self.guards:
                st["v"] = 0
        self._persist(cid, st)
        cl = self._cluster(st)
        for pk in pubkeys:
            self.c.emit(tx, "ValidatorRemoved", [_topic_addr(owner)],
                        _enc(["uint64[]", "bytes", "cluster"], [list(ops), pk, cl]))
        self.c.mine()
        return tx

    def commit(self, ref_block: int) -> str:
        assert ref_block > self.latest_root and ref_block < self.c.number
        tx = self.c.new_tx("0x" + "0c" * 20)
        root = "0x%064x" % (0xB0000000 + ref_block)
        self.roots[ref_block] = root
        self.latest_root = ref_block
        self.c.emit(tx, "RootCommitted", [root, "0x%064x" % ref_block], b"")
        self.c.mine()
        return tx

    def update(self, owner, ops, eb: int, ref_block: int | None = None, sender=None) -> str:
        cid, st = self._get(owner, ops)
        ref_block = self.latest_root if ref_block is None else ref_block
        assert ref_block == self.latest_root and ref_block > st["root"], "MustUseLatestRoot/StaleUpdate"
        assert st["count"] * 32 <= eb <= st["count"] * 2048, "EB limits"
        tx = self.c.new_tx(sender or owner)
        if st["version"] == "ETH" and st["active"]:
            self._touch(st)
        st.update(v=eb_to_vunits(eb), root=ref_block, upd=self.c.number)
        self._persist(cid, st)
        self.c.emit(tx, "ClusterBalanceUpdated", [_topic_addr(owner), "0x%064x" % ref_block],
                    _enc(["uint64[]", "uint", "cluster"], [list(ops), eb, self._cluster(st)]))
        self.c.mine()
        return tx

    def liquidate(self, owner, ops) -> str:
        cid, st = self._get(owner, ops)
        tx = self.c.new_tx("0x" + "11" * 20)
        st.update(active=False, balance=0, index=0, nfi=0)
        self._persist(cid, st)
        self.c.emit(tx, "ClusterLiquidated", [_topic_addr(owner)],
                    _enc(["uint64[]", "cluster"], [list(ops), self._cluster(st)]))
        self.c.mine()
        return tx

    def reactivate(self, owner, ops) -> str:
        cid, st = self._get(owner, ops)
        tx = self.c.new_tx(owner)
        st.update(active=True, balance=10**21, index=7, nfi=7)
        self._persist(cid, st)
        self.c.emit(tx, "ClusterReactivated", [_topic_addr(owner)],
                    _enc(["uint64[]", "cluster"], [list(ops), self._cluster(st)]))
        self.c.mine()
        return tx

    def migrate(self, owner, ops) -> str:
        cid, st = self._get(owner, ops)
        assert st["version"] == "SSV"
        tx = self.c.new_tx(owner)
        st["version"] = "ETH"
        self._touch(st)
        if st["count"] == 0 and st["v"] > 0 and self.guards:
            st["v"] = 0
        self._persist(cid, st)
        eb = (st["v"] if st["v"] else st["count"] * BASE) * 32 // BASE
        self.c.emit(tx, "ClusterMigratedToETH", [_topic_addr(owner)],
                    _enc(["uint64[]", "uint", "uint", "uint", "cluster"], [list(ops), 10**18, 0, eb, self._cluster(st)]))
        self.c.mine()
        return tx

    def vunits(self, owner, ops) -> int:
        return self._get(owner, ops)[1]["v"]


def keys(n: int, start: int) -> list[bytes]:
    return [(start + i).to_bytes(48, "big") for i in range(n)]
