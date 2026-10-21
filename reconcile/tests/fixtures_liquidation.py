"""Liquidation-family fixtures (M3, M4, M14) on top of tests/fakechain.py (not edited).

`LiquidationChain` is a FakeChain whose SSVNetworkViews cluster views (`isLiquidatable`, `getBalance`,
`getBurnRate`) are computed from a per-cluster balance model, the way SSVViews computes them at the pin:

  balance(b) = max(0, settled balance - burn * (b - settled block))        (index-based, view-computed)
  isLiquidatable(b) = active and validators > 0 and (balance(b) < minimumLiquidationCollateral
                      or balance(b) < liquidationThresholdPeriod * burn)  (ClusterLib.isLiquidatableWithEB)
  getBalance reverts ClusterIsLiquidated for an inactive cluster; every view reverts IncorrectClusterState
  when the tuple's hash differs from the stored word at the block (validateHashedCluster).

The two parameters are read from the chain's own `params.GETTERS` stubs (`harness.stub_params`), so a
parameter change on the fake chain changes the views exactly as it would on mainnet. `Clusters` writes
real tuples to storage and emits real logs (ValidatorAdded, ClusterDeposited, ClusterWithdrawn,
ClusterLiquidated), so the framework's scan, inventory and storage validation run unmodified.
Dated values (spec §5, block 25,933,264) are the `harness.stub_params` defaults.
"""

from __future__ import annotations

import json

from fakechain import PROXY, FakeChain, FakeClock, Revert, _enc, _topic_addr
from harness import VIEWS, make_worker, stub_params

from checks.m3_views import (
    GET_BALANCE,
    GET_BURN_RATE,
    GET_NETWORK_EARNINGS,
    GET_OPERATOR_EARNINGS,
    IS_LIQUIDATABLE,
    LIQUIDATE,
    MAIN_LAST_OPERATOR_ID,
    STAKING_ETH_POOL_BALANCE,
    decode_cluster_args,
    encode_cluster_args,
)
from eb_storage import Cluster, cluster_id, eth_cluster_slot, u256
from params import GETTERS, selector

INCORRECT_CLUSTER_STATE = "0x12e04c87"
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
                  ((IS_LIQUIDATABLE, "isLiquidatable"), (GET_BALANCE, "getBalance"), (GET_BURN_RATE, "getBurnRate"))}


class LiquidationChain(FakeChain):
    def __init__(self, start_block: int = 1000, views: str = VIEWS):
        super().__init__(start_block)
        self.views = views.lower()
        self.model: dict[bytes, list[tuple[int, int, int]]] = {}  # cid -> [(from block, settled balance, burn)]
        self.view_faults: dict[bytes, str] = {}  # cid -> "revert" | "empty" (fault injection)

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

    def reorg(self, from_block: int) -> None:
        for cid in self.model:
            self.model[cid] = [s for s in self.model[cid] if s[0] < from_block]
        super().reorg(from_block)

    # ---- views

    def _eth_call(self, obj: dict, block: int) -> str:
        to, data = (obj.get("to") or "").lower(), obj.get("data", obj.get("input", "0x")).lower()
        name = VIEW_SELECTORS.get(data[:10]) if to == self.views else None
        if name is None:
            return super()._eth_call(obj, block)
        self.calls.append((to, data, block))
        owner, ops, cl = decode_cluster_args(bytes.fromhex(data[10:]))
        cid = cluster_id(owner, ops)
        if int.from_bytes(cl.hash_data(), "big") != self.storage(eth_cluster_slot(cid), block):
            raise Revert(INCORRECT_CLUSTER_STATE)
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
        liq = cl.active and cl.validator_count > 0 and (
            bal < self.param("minimum_liquidation_collateral", block)
            or bal < self.param("liquidation_threshold_period", block) * burn)
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
        self.c.write(eth_cluster_slot(cid), int.from_bytes(cl.hash_data(), "big"))
        self.c.settle(cid, st["balance"] if st["active"] else 0, st["burn"])
        return cl

    def _end(self, tx: str, mine: bool) -> str:
        if mine:
            self.c.mine()
        return tx

    def create(self, owner=OWNER, ops=OPS, count=1, balance=10**18, burn=BURN, mine=True) -> str:
        cid = cluster_id(owner, ops)
        st = self.state.setdefault(cid, {"owner": owner, "ops": tuple(ops), "count": 0, "nfi": 0, "index": 0,
                                         "active": True, "balance": 0, "burn": burn})
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
        cid = cluster_id(owner, ops)
        st = self.state[cid]
        tx = self.c.new_tx(sender or "0x" + "de" * 20)
        cl = self._persist(cid, st, self.c.balance_at(cid, self.c.number) + value)
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
    chain, w = make_worker(tmp_path, only=list(checks), chain=chain, views=VIEWS, checks=cfg or {}, **over)
    return chain, w, Clusters(chain)


def restart(tmp_path, w, checks=("m3",), cfg=None, **over):
    """A new worker process on the same database (V2x.3)."""
    over.setdefault("clock", w.clock)
    _, w2 = make_worker(tmp_path, only=list(checks), chain=w.rpc.chain, views=VIEWS, checks=cfg or {},
                        start_block=w.cfg.start_block, activation_block=w.cfg.activation_block, **over)
    return w2


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
