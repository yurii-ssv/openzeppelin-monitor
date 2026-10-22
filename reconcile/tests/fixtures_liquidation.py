"""Liquidation-family fixtures (M3, M4, M14) on top of tests/fakechain.py (not edited).

`LiquidationChain` is a FakeChain whose SSVNetworkViews cluster views (`isLiquidatable`, `getBalance`,
`getBurnRate`) are computed from a per-cluster balance model, the way SSVViews computes them at the pin:

  balance(b) = max(0, settled balance - burn * (b - settled block))        (index-based, view-computed)
  isLiquidatable(b) = active and validators > 0 and (balance(b) < minimumLiquidationCollateral
                      or balance(b) < liquidationThresholdPeriod * burn)  (ClusterLib.isLiquidatableWithEB)
  getBalance reverts ClusterIsLiquidated for an inactive cluster; every view reverts IncorrectClusterState
  when the tuple's hash differs from the stored word at the block (validateHashedCluster);
  getNetworkValidatorsCount = the validators of active clusters (ethDaoValidatorCount), from `Clusters`.

Legacy SSV clusters (`Clusters.create(..., version="SSV")`, stored in `clusters[id]`, not `ethClusters[id]`)
answer the `…SSV` variants (`isLiquidatableSSV`, `getBalanceSSV`, `getBurnRateSSV`) from the same model in SSV
units, with the SSV parameters; each variant reverts `IncorrectClusterVersion()` on the other version, as
`ClusterLib.validateClusterVersion` does at the pin (a legacy cluster outside the model, e.g. `MiniSSV`'s, answers
the `…SSV` views from `stub_call`).

The parameters are read from the chain's own `params.GETTERS` stubs (`harness.stub_params`), so a
parameter change on the fake chain changes the views exactly as it would on mainnet.

Block headers carry a `timestamp` (chain time, which M14's 24-hour window counts): 12 s per slot from
`T0`, plus missed-slot gaps added with `elapse(seconds)`; when a `clock` is attached (`setup` attaches the
worker's), a block first read at worker time t is never earlier than t, so `w.clock.advance(13 * 3600)`
between blocks models 13 hours of chain time too. Timestamps never decrease with the block number. `Clusters` writes
real tuples to storage and emits real logs (ValidatorAdded, ClusterDeposited, ClusterWithdrawn,
ClusterLiquidated), so the framework's scan, inventory and storage validation run unmodified.
Dated values (spec §5, block 25,933,264) are the `harness.stub_params` defaults.
"""

from __future__ import annotations

import json

from fakechain import PROXY, FakeChain, FakeClock, Revert, _enc, _topic_addr
from harness import VIEWS, advance, make_worker, stub_params

from checks.m3_views import (
    GET_BALANCE,
    GET_BALANCE_SSV,
    GET_BURN_RATE,
    GET_BURN_RATE_SSV,
    GET_NETWORK_EARNINGS,
    GET_NETWORK_VALIDATORS_COUNT,
    GET_OPERATOR_EARNINGS,
    IS_LIQUIDATABLE,
    IS_LIQUIDATABLE_SSV,
    LIQUIDATE,
    MAIN_LAST_OPERATOR_ID,
    STAKING_ETH_POOL_BALANCE,
    decode_cluster_args,
    encode_cluster_args,
)
from eb_storage import Cluster, cluster_id, eth_cluster_slot, ssv_cluster_slot, u256
from params import GETTERS, selector

INCORRECT_CLUSTER_STATE = "0x12e04c87"
INCORRECT_CLUSTER_VERSION = "0x" + selector("IncorrectClusterVersion()").hex()
CLUSTER_IS_LIQUIDATED = "0x" + selector("ClusterIsLiquidated()").hex()
OWNER = "0x" + "aa" * 20
OWNER2 = "0x" + "ab" * 20
OPS = (11, 12, 13, 14)
OPS2 = (21, 22, 23, 24)
BURN = 10**10  # wei/block of a 1-validator cluster (≈ 4 × 1.7788 + 3.3666 gwei, spec §5)
LTP = 42_960  # spec §5 liquidation threshold period (blocks), the stub_params default
MIN_COLL = 325_000_000_000_000  # spec §5 minimum liquidation collateral (wei)
THRESHOLD = LTP * BURN  # 0.0004296 ETH > MIN_COLL
VIEW_SELECTORS = {"0x" + s.hex(): name for s, name in
                  ((IS_LIQUIDATABLE, "isLiquidatable"), (GET_BALANCE, "getBalance"), (GET_BURN_RATE, "getBurnRate"),
                   (IS_LIQUIDATABLE_SSV, "isLiquidatableSSV"), (GET_BALANCE_SSV, "getBalanceSSV"),
                   (GET_BURN_RATE_SSV, "getBurnRateSSV"))}
T0 = 1_800_000_000  # FakeClock's start: slot time and worker time agree at the start
SLOT_S = 12


class LiquidationChain(FakeChain):
    def __init__(self, start_block: int = 1000, views: str = VIEWS):
        super().__init__(start_block)
        self.views = views.lower()
        self.model: dict[bytes, list[tuple[int, int, int]]] = {}  # cid -> [(from block, settled balance, burn)]
        self.view_faults: dict[bytes, str] = {}  # cid -> "revert" | "empty" (fault injection)
        self.dao_validators: list[tuple[int, int]] = []  # [(from block, ethDaoValidatorCount)]
        self.hidden_validators = 0  # validators of clusters the inventory cannot see (completeness fault)
        self.start = start_block
        self.clock = None  # worker clock (FakeClock): blocks are first read no earlier than its time
        self.gaps: list[tuple[int, int]] = []  # [(from block, seconds)] missed slots before that block
        self.stamps: dict[int, float] = {}  # block -> worker time when its header was first read

    # ---- chain time

    def elapse(self, seconds: int) -> None:
        """Missed slots: the pending block (and every later one) is `seconds` later than its slot time."""
        self.gaps.append((self.number, seconds))

    def timestamp(self, n: int) -> int:
        t = T0 + SLOT_S * (n - self.start) + sum(s for b, s in self.gaps if b <= n)
        if self.clock is not None:
            if n not in self.stamps:
                self.stamps[n] = min([self.clock()] + [v for m, v in self.stamps.items() if m > n])
            t = max(t, int(self.stamps[n]))
        return t

    def _handle(self, m: str, p: list):
        res = super()._handle(m, p)
        if m == "eth_getBlockByNumber" and res:
            res = dict(res, timestamp=hex(self.timestamp(int(res["number"], 16))))
        return res

    # ---- model

    def balance_at(self, cid: bytes, block: int) -> int:
        segs = [s for s in self.model.get(cid, []) if s[0] <= block]
        if not segs:
            return 0
        start, bal, burn = segs[-1]
        return max(0, bal - burn * (block - start))

    def burn_at(self, cid: bytes, block: int) -> int:
        segs = [s for s in self.model.get(cid, []) if s[0] <= block]
        return segs[-1][2] if segs else 0

    def settle(self, cid: bytes, balance: int, burn: int) -> None:
        """A cluster interaction at the pending block: the settled balance and burn rate from here on."""
        self.model.setdefault(cid, []).append((self.number, balance, burn))

    def param(self, name: str, block: int) -> int:
        return int(FakeChain._eth_call(self, {"to": self.views, "data": "0x" + selector(GETTERS[name]).hex()},
                                       block), 16)

    def add_dao_validators(self, delta: int) -> None:
        now = self.dao_validators[-1][1] if self.dao_validators else 0
        self.dao_validators.append((self.number, now + delta))

    def reorg(self, from_block: int) -> None:
        """FakeChain.reorg, plus everything this chain models per block: balances, call stubs, chain time."""
        self.balances = {a: [x for x in v if x[0] < from_block] for a, v in self.balances.items()}
        self.call_stubs = {k: [x for x in v if x[0] < from_block] for k, v in self.call_stubs.items()}
        self.gaps = [g for g in self.gaps if g[0] < from_block]
        self.stamps = {b: t for b, t in self.stamps.items() if b < from_block}
        self.dao_validators = [d for d in self.dao_validators if d[0] < from_block]
        for cid in self.model:
            self.model[cid] = [s for s in self.model[cid] if s[0] < from_block]
        super().reorg(from_block)

    # ---- views

    def _eth_call(self, obj: dict, block: int) -> str:
        to, data = (obj.get("to") or "").lower(), obj.get("data", obj.get("input", "0x")).lower()
        if to == self.views and data == "0x" + GET_NETWORK_VALIDATORS_COUNT.hex():
            self.calls.append((to, data, block))
            n = [v for b, v in self.dao_validators if b <= block]
            return "0x" + u256((n[-1] if n else 0) + self.hidden_validators).hex()
        name = VIEW_SELECTORS.get(data[:10]) if to == self.views else None
        if name is None:
            return super()._eth_call(obj, block)
        owner, ops, cl = decode_cluster_args(bytes.fromhex(data[10:]))
        cid = cluster_id(owner, ops)
        if name.endswith("SSV") and cid not in self.model:  # a legacy cluster outside the model: generic stubs
            return super()._eth_call(obj, block)
        self.calls.append((to, data, block))
        word = int.from_bytes(cl.hash_data(), "big")
        version = ("ETH" if word == self.storage(eth_cluster_slot(cid), block) else
                   "SSV" if word == self.storage(ssv_cluster_slot(cid), block) else None)
        if version is None:  # validateHashedCluster
            raise Revert(INCORRECT_CLUSTER_STATE)
        ssv = name.endswith("SSV")
        if version != ("SSV" if ssv else "ETH"):  # validateClusterVersion
            raise Revert(INCORRECT_CLUSTER_VERSION)
        name = name.removesuffix("SSV")
        fault = self.view_faults.get(cid)
        if fault == "revert":
            raise Revert("0x4e487b710000000000000000000000000000000000000000000000000000000000000011")
        if fault == "empty":
            return "0x"
        bal, burn = self.balance_at(cid, block), self.burn_at(cid, block)
        if name == "getBurnRate":
            return "0x" + u256(burn).hex()
        if name == "getBalance":
            if not cl.active:
                raise Revert(CLUSTER_IS_LIQUIDATED)
            return "0x" + u256(bal).hex()
        suffix = "_ssv" if ssv else ""
        liq = cl.active and cl.validator_count > 0 and (
            bal < self.param("minimum_liquidation_collateral" + suffix, block)
            or bal < self.param("liquidation_threshold_period" + suffix, block) * burn)
        return "0x" + u256(int(liq)).hex()


class Clusters:
    """ETH clusters with real storage words and logs; every method is one transaction, mined unless mine=False."""

    def __init__(self, chain: LiquidationChain):
        self.c = chain
        self.state: dict[bytes, dict] = {}
        self.pk = 0

    def tuple(self, owner=OWNER, ops=OPS) -> Cluster:
        st = self.state[cluster_id(owner, ops)]
        return Cluster(st["count"], st["nfi"], st["index"], st["active"], st["balance"])

    def _persist(self, cid: bytes, st: dict, balance: int | None = None) -> Cluster:
        st["index"] += 1
        st["nfi"] += 1
        st["balance"] = self.c.balance_at(cid, self.c.number) if balance is None else balance
        cl = Cluster(st["count"], st["nfi"], st["index"], st["active"], st["balance"])
        slot = eth_cluster_slot(cid) if st["version"] == "ETH" else ssv_cluster_slot(cid)
        self.c.write(slot, int.from_bytes(cl.hash_data(), "big"))
        self.c.settle(cid, st["balance"] if st["active"] else 0, st["burn"])
        return cl

    def _end(self, tx: str, mine: bool) -> str:
        if mine:
            self.c.mine()
        return tx

    def create(self, owner=OWNER, ops=OPS, count=1, balance=10**18, burn=BURN, mine=True, version="ETH") -> str:
        """An ETH cluster with `count` ValidatorAdded logs; `version="SSV"`: a legacy SSV cluster registered before
        the scan starts (storage only, no log: only the API or a later log can list it; balance/burn in SSV)."""
        cid = cluster_id(owner, ops)
        st = self.state.setdefault(cid, {"owner": owner, "ops": tuple(ops), "count": 0, "nfi": 0, "index": 0,
                                         "active": True, "balance": 0, "burn": burn, "version": version})
        if version == "SSV":
            st.update(count=st["count"] + count, burn=burn, active=True)
            self._persist(cid, st, balance)
            return self._end(self.c.new_tx(owner), mine)
        self.c.add_dao_validators(count + (0 if st["active"] else st["count"]))
        st.update(count=st["count"] + count, burn=burn, active=True)
        tx = self.c.new_tx(owner)
        cl = self._persist(cid, st, balance)
        for _ in range(count):
            self.pk += 1
            self.c.emit(tx, "ValidatorAdded", [_topic_addr(owner)],
                        _enc(["uint64[]", "bytes", "bytes", "cluster"], [list(ops), self.pk.to_bytes(48, "big"), b"",
                                                                        cl]))
        return self._end(tx, mine)

    def deposit(self, owner=OWNER, ops=OPS, value=1, sender=None, mine=True) -> str:
        """`ClusterDeposited` with `value` in the cluster's own unit (wei of ETH, or of SSV for a legacy cluster: the
        pre-v2 SSV deposit emitted the same event; at the pin `deposit` is ETH-only). As `SSVClusters.deposit` at
        the pin, it does not settle the cluster: the tuple keeps its indexes, its stored balance grows by `value`
        (the view-computed balance grows by `value` too)."""
        cid = cluster_id(owner, ops)
        st = self.state[cid]
        tx = self.c.new_tx(sender or "0x" + "de" * 20)
        st["balance"] += value
        cl = Cluster(st["count"], st["nfi"], st["index"], st["active"], st["balance"])
        slot = eth_cluster_slot(cid) if st["version"] == "ETH" else ssv_cluster_slot(cid)
        self.c.write(slot, int.from_bytes(cl.hash_data(), "big"))
        self.c.settle(cid, self.c.balance_at(cid, self.c.number) + value, st["burn"])
        self.c.emit(tx, "ClusterDeposited", [_topic_addr(owner)], _enc(["uint64[]", "uint", "cluster"],
                                                                       [list(ops), value, cl]))
        return self._end(tx, mine)

    def withdraw(self, owner=OWNER, ops=OPS, value=1, mine=True) -> str:
        cid = cluster_id(owner, ops)
        st = self.state[cid]
        tx = self.c.new_tx(owner)
        cl = self._persist(cid, st, self.c.balance_at(cid, self.c.number) - value)
        self.c.emit(tx, "ClusterWithdrawn", [_topic_addr(owner)], _enc(["uint64[]", "uint", "cluster"],
                                                                       [list(ops), value, cl]))
        return self._end(tx, mine)

    def liquidate(self, owner=OWNER, ops=OPS, mine=True) -> str:
        cid = cluster_id(owner, ops)
        st = self.state[cid]
        tx = self.c.new_tx("0x" + "11" * 20)
        self.c.add_dao_validators(-st["count"])
        st["active"] = False
        cl = self._persist(cid, st, 0)
        self.c.emit(tx, "ClusterLiquidated", [_topic_addr(owner)], _enc(["uint64[]", "cluster"], [list(ops), cl]))
        return self._end(tx, mine)

    def set_burn(self, owner=OWNER, ops=OPS, burn=BURN) -> None:
        """An operator/network fee change: the burn rate changes without a cluster event."""
        cid = cluster_id(owner, ops)
        self.state[cid]["burn"] = burn
        self.c.settle(cid, self.c.balance_at(cid, self.c.number), burn)

    def corrupt(self, owner=OWNER, ops=OPS) -> None:
        """Make the stored word match no known tuple (an unvalidatable cluster)."""
        self.c.write(eth_cluster_slot(cluster_id(owner, ops)), 0xBAD)


def cid_hex(owner=OWNER, ops=OPS) -> str:
    return "0x" + cluster_id(owner, ops).hex()


def setup(tmp_path, checks=("m3",), cfg=None, chain=None, **over):
    """A LiquidationChain with the spec §5 parameters and a worker running only `checks`."""
    chain = chain or LiquidationChain()
    stub_params(chain)
    chain.mine()
    over.setdefault("clock", FakeClock())
    chain.clock = over["clock"]
    chain, w = make_worker(tmp_path, only=list(checks), chain=chain, views=VIEWS, checks=cfg or {}, **over)
    return chain, w, Clusters(chain)


def restart(tmp_path, w, checks=("m3",), cfg=None, **over):
    """A new worker process on the same database (V2x.3)."""
    over.setdefault("clock", w.clock)
    _, w2 = make_worker(tmp_path, only=list(checks), chain=w.rpc.chain, views=VIEWS, checks=cfg or {},
                        start_block=w.cfg.start_block, activation_block=w.cfg.activation_block, **over)
    return w2


def lagging_node(chain, w, behind: int) -> None:
    """One worker step against a node whose head is `behind` blocks below the chain's (a stale backend behind a load
    balancer): the framework treats a checkpoint the node does not have as not canonical, a reorg onto a shorter
    chain, although no block was replaced; the checkpoint falls to the last recorded block the node has."""
    saved = chain.number
    chain.number -= behind
    try:
        advance(w)
    finally:
        chain.number = saved


def incidents(w, check):
    return [dict(r) for r in w.db.conn.execute("SELECT * FROM incidents WHERE check_id=? ORDER BY rowid", (check,))]


def open_incidents(w, check):
    return {r["key"]: r for r in incidents(w, check) if r["state"] == "open"}


def sent(w, check, where="1=1"):
    """(kind, severity) of the outbox rows of one check's incidents, in order."""
    rows = w.db.conn.execute(
        f"SELECT o.kind, o.severity FROM outbox o JOIN incidents i ON o.incident_id = i.id WHERE i.check_id=? AND "
        f"{where} ORDER BY o.created_at, o.rowid", (check,)).fetchall()
    return [(r[0], r[1]) for r in rows]


# ---- M4 inputs


def stub_m4(chain, operators: dict[int, int], network: int, pool: int, proxy_balance: int, last_operator=None):
    """getOperatorEarnings(id), getNetworkEarnings(), stakingEthPoolBalance(), lastOperatorId, proxy ETH."""
    for op, wei in operators.items():
        chain.stub_call(VIEWS, GET_OPERATOR_EARNINGS + u256(op), u256(wei))
    chain.stub_call(VIEWS, GET_NETWORK_EARNINGS, u256(network))
    chain.stub_call(VIEWS, STAKING_ETH_POOL_BALANCE, u256(pool))
    chain.write(MAIN_LAST_OPERATOR_ID, max(operators) if last_operator is None else last_operator)
    chain.set_balance(PROXY, proxy_balance)


# ---- M14 inputs


def liquidate_calldata(owner, ops, cl: Cluster, fn="liquidate") -> str:
    return "0x" + (LIQUIDATE[fn] + encode_cluster_args(owner, ops, cl)).hex()


def stub_liquidation_reverts(chain) -> None:
    """The inbox resolves a revert by re-running the call at `block - 1`: answer it from the pending block on."""
    for sel in LIQUIDATE.values():
        chain.stub_call(PROXY, sel, revert=INCORRECT_CLUSTER_STATE)


def failed_liquidation(chain, clusters, inbox_path, owner=OWNER, ops=OPS, fn="liquidate", tuple_=None, tx_index=True,
                       write=True):
    """A reverted liquidate/liquidateSSV in the pending block, carrying the tuple the liquidator saw (by default the
    tuple before any change in this block), and its failed-tx inbox line (written by the Monitor at its own depth)."""
    cl = tuple_ or clusters.tuple(owner, ops)
    tx = chain.add_tx("0x" + "16" * 20, PROXY, liquidate_calldata(owner, ops, cl, fn))
    if tx_index is not False:  # True: its position in the block (as mainnet nodes report it); False: not reported
        chain.txs[tx]["transactionIndex"] = hex(chain.tx_index if tx_index is True else tx_index)
    line = {"schema": 1, "tx_hash": tx, "block": chain.number, "block_hash": chain.hash(chain.number),
            "from": "0x" + "16" * 20, "to": PROXY, "selector": "0x" + LIQUIDATE[fn].hex(),
            "function": f"{fn}(address,uint64[],(uint32,uint64,uint64,bool,uint256))", "status": "failure",
            "monitor": "SSV Failed Tx Inbox", "observed_at": "2026-10-21T00:00:00Z"}
    if write:
        with open(inbox_path, "a") as fh:
            fh.write(json.dumps(line) + "\n")
    return tx, line
