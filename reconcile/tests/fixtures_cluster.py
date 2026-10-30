"""CP2d fixtures (M1, M2): scenario builders on FakeChain.

`ClusterChain` is a FakeChain whose SSVNetworkViews answers `isLiquidated`, `isLiquidatable` and
`isLiquidatableSSV` the way SSVViews does at the pin (ssv-network f7ac487, `validateHashedCluster`): the tuple's
`hashClusterData` is compared with the cluster word MiniSSV wrote to storage at the call's block; a mismatch reverts
`IncorrectClusterState`, a missing cluster `ClusterDoesNotExist`, the wrong version `IncorrectClusterVersion`.
Liquidatable = active and balance < liquidationThresholdPeriod(_SSV) (the chain's own Views getter at that block,
see `harness.stub_params`) x `chain.burn[cluster id]` wei/block. Everything else is FakeChain unchanged.

Spec-dated values (spec §5, 2026-10-13) live here only: SENDERS, the validators-per-operator limit in stub_params.
"""

from __future__ import annotations

import json

from fakechain import PROXY, FakeChain, MiniSSV, Revert, _enc, _topic_addr
from harness import VIEWS, make_worker, stub_params

from checks.m1_cluster_abi import (
    ERR_CLUSTER_DOES_NOT_EXIST,
    ERR_EXCEED_VALIDATOR_LIMIT,
    ERR_INCORRECT_CLUSTER_STATE,
    ERR_INCORRECT_CLUSTER_VERSION,
    FUNCTIONS,
    GET_OPERATOR_BY_ID,
    GET_OPERATOR_BY_ID_SSV,
    IS_LIQUIDATABLE,
    IS_LIQUIDATABLE_SSV,
    IS_LIQUIDATED,
    selector,
)
from eb_storage import Cluster, cluster_id, eth_cluster_slot, ssv_cluster_slot, u256
from params import GETTERS

UPDATER = "0x16a503c901615e6e21aa78cfcaff11822fc2b617"  # spec §5: oracle slot 4 + EB updater
LIQUIDATOR = "0x0431371ad8eda67ba2ef6489ab003b3a7f0d95e0"  # spec §5: liquidator
SENDERS = [UPDATER, LIQUIDATOR]
STRANGER = "0x" + "5a" * 20
OWNER = "0x" + "a1" * 20
OWNER2 = "0x" + "b2" * 20
OPS = (1, 2, 3, 4)
OPS2 = (5, 6, 7, 8)


def key_of(owner, ops) -> str:
    return "0x" + cluster_id(owner, ops).hex()


class ClusterChain(FakeChain):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.burn: dict[str, int] = {}  # cluster key -> wei per block (isLiquidatable)
        self.views_down = False  # every SSVNetworkViews cluster call answers a JSON-RPC error
        self.views_empty = False  # every SSVNetworkViews cluster call answers 0x

    def _getter(self, name: str, block: int) -> int:
        return int(super()._eth_call({"to": VIEWS, "data": "0x" + selector(GETTERS[name]).hex()}, block), 16)

    def _eth_call(self, obj: dict, block: int) -> str:
        to, data = (obj.get("to") or "").lower(), obj.get("data", obj.get("input", "0x")).lower()
        sel = bytes.fromhex(data[2:10]) if len(data) >= 10 else b""
        if to != VIEWS or sel not in (IS_LIQUIDATED, IS_LIQUIDATABLE, IS_LIQUIDATABLE_SSV):
            return super()._eth_call(obj, block)
        self.calls.append((to, data, block))
        if self.views_down:
            raise ValueError("views unavailable")
        if self.views_empty:
            return "0x"
        body = bytes.fromhex(data[10:])
        w = [int.from_bytes(body[i:i + 32], "big") for i in range(0, len(body), 32)]
        owner, off = "0x%040x" % w[0], w[1]
        cl = Cluster(w[2], w[3], w[4], bool(w[5]), w[6])
        n = w[off // 32]
        ops = tuple(w[off // 32 + 1: off // 32 + 1 + n])
        cid = cluster_id(owner, ops)
        eth_word, ssv_word = self.storage(eth_cluster_slot(cid), block), self.storage(ssv_cluster_slot(cid), block)
        stored = eth_word or ssv_word
        if not stored:
            raise Revert(ERR_CLUSTER_DOES_NOT_EXIST)
        if (eth_word and ssv_word) or int.from_bytes(cl.hash_data(), "big") != stored:
            raise Revert(ERR_INCORRECT_CLUSTER_STATE)
        if sel == IS_LIQUIDATED:
            return "0x" + u256(int(not cl.active)).hex()
        if (sel == IS_LIQUIDATABLE) != bool(eth_word):
            raise Revert(ERR_INCORRECT_CLUSTER_VERSION)
        period = self._getter("liquidation_threshold_period" if eth_word else "liquidation_threshold_period_ssv",
                              block)
        liquidatable = cl.active and cl.balance < period * self.burn.get("0x" + cid.hex(), 0)
        return "0x" + u256(int(liquidatable)).hex()


# ---------------------------------------------------------------- workers


def m1_config(**over) -> dict:
    return {"our_senders": SENDERS, "receipt_window_blocks": 1000, **over}


def make_m1(tmp_path, chain=None, ssv=None, m1=None, **over):
    """A worker running only M1 (views set, live parameters stubbed) on a ClusterChain with MiniSSV."""
    chain = chain or ClusterChain()
    stub_params(chain)
    chain.mine()
    _, w = make_worker(tmp_path, only=["m1"], chain=chain, views=VIEWS, checks={"m1": m1_config(**(m1 or {}))},
                       **over)
    return chain, w, ssv or MiniSSV(chain)


def make_m2(tmp_path, chain=None, m2=None, api_rows=None, **over):
    """A worker running only M2 with the SSV API served from `api_rows` (a list the test may mutate)."""
    chain = chain or ClusterChain()
    stub_params(chain)
    chain.mine()
    rows = api_rows if api_rows is not None else []
    inventory = {"api_url": "https://api.invalid", "api_refresh_s": 0}
    _, w = make_worker(tmp_path, only=["m2"], chain=chain, views=VIEWS, checks={"m2": dict(m2 or {})},
                       inventory=inventory, **over)
    w.inventory.http_get = lambda url: {"clusters": list(rows), "pagination": {"pages": 1}}
    return chain, w, MiniSSV(chain), rows


# ---------------------------------------------------------------- cluster transactions (M1)


def _emit_cluster(chain, tx, kind, owner, ops, cl, value=0):
    if kind in ("ClusterDeposited", "ClusterWithdrawn"):
        data = _enc(["uint64[]", "uint", "cluster"], [list(ops), value, cl])
    elif kind in ("ClusterLiquidated", "ClusterReactivated"):
        data = _enc(["uint64[]", "cluster"], [list(ops), cl])
    elif kind == "ValidatorAdded":
        data = _enc(["uint64[]", "bytes", "bytes", "cluster"], [list(ops), b"\x01" * 48, b"", cl])
    elif kind == "ValidatorRemoved":
        data = _enc(["uint64[]", "bytes", "cluster"], [list(ops), b"\x01" * 48, cl])
    else:
        raise ValueError(kind)
    chain.emit(tx, kind, [_topic_addr(owner)], data)


def poison(ssv: MiniSSV, owner=OWNER, ops=OPS, outer="ClusterWithdrawn", nested="ClusterDeposited", nested_n=1,
           sender=None, mine=True):
    """The #91419 receipt shape: the outer function writes its result T_a and makes an external call; the callback
    runs `nested` (n times), each writing and emitting a newer tuple; the outer function then emits its cached T_a
    LAST. Storage holds the last nested tuple (the repair tuple). Returns (tx, stale T_a, repair tuple)."""
    cid, st = ssv._get(owner, ops)
    tx = ssv.c.new_tx(sender or owner)
    ssv._touch(st)
    st["balance"] -= 10**15
    ssv._persist(cid, st)
    stale = ssv._cluster(st)
    for _ in range(nested_n):
        ssv._touch(st)
        st["balance"] += 3 * 10**15
        ssv._persist(cid, st)
        _emit_cluster(ssv.c, tx, nested, owner, ops, ssv._cluster(st), 3 * 10**15)
    _emit_cluster(ssv.c, tx, outer, owner, ops, stale, 10**15)
    if mine:
        ssv.c.mine()
    return tx, stale, ssv._cluster(st)


def deposit(ssv: MiniSSV, owner=OWNER, ops=OPS, value=0, sender=None, mine=True):
    """deposit(owner, ops, storage's tuple): one ClusterDeposited with the settled tuple (a zero value is the M1
    repair)."""
    cid, st = ssv._get(owner, ops)
    tx = ssv.c.new_tx(sender or owner)
    ssv._touch(st)
    st["balance"] += value
    ssv._persist(cid, st)
    _emit_cluster(ssv.c, tx, "ClusterDeposited", owner, ops, ssv._cluster(st), value)
    if mine:
        ssv.c.mine()
    return tx, ssv._cluster(st)


def multi_benign(ssv: MiniSSV, owner=OWNER, ops=OPS, n=3, mine=True):
    """A bulk registration in one transaction: n ValidatorAdded, every one with the final tuple (validates)."""
    cid, st = ssv._get(owner, ops)
    tx = ssv.c.new_tx(owner)
    st["count"] += n
    ssv._touch(st)
    ssv._persist(cid, st)
    for _ in range(n):
        _emit_cluster(ssv.c, tx, "ValidatorAdded", owner, ops, ssv._cluster(st))
    if mine:
        ssv.c.mine()
    return tx, ssv._cluster(st)


def current_tuple(ssv: MiniSSV, owner=OWNER, ops=OPS) -> Cluster:
    return ssv._cluster(ssv._get(owner, ops)[1])


# ---------------------------------------------------------------- calldata and the inbox


def calldata(fn: str, owner, ops, cl: Cluster, block_num: int = 0) -> str:
    sig, owner_word, _ = FUNCTIONS[fn]
    tuple_words = [cl.validator_count, cl.network_fee_index, cl.index, int(cl.active), cl.balance]
    if fn == "updateClusterBalance":
        head_n = 1 + 1 + 1 + 5 + 1 + 1
        ops_off = 32 * head_n
        proof_off = ops_off + 32 * (1 + len(ops))
        words = [block_num, int(owner, 16), ops_off, *tuple_words, 64, proof_off]
        tail = [len(ops), *ops, 0]
    elif owner_word is not None:
        words = [int(owner, 16), 32 * 7, *tuple_words]
        tail = [len(ops), *ops]
    else:
        words = [32 * 6, *tuple_words]
        tail = [len(ops), *ops]
    return "0x" + (selector(sig) + b"".join(u256(x) for x in words + tail)).hex()


def failed_tx(chain, path, fn: str, owner, ops, cl: Cluster, sender: str, revert: str, mine=True) -> str:
    """A reverted call mined in the next block, appended to the inbox file like the Monitor does, with the
    proxy's eth_call answer (the revert) stubbed so the framework resolves the revert selector."""
    data = calldata(fn, owner, ops, cl)
    chain.stub_call(PROXY, data, revert=revert)
    chain.mine()  # the framework replays the call at `block - 1`: the stub must already answer there
    tx = chain.add_tx(sender, PROXY, data)
    block = chain.number
    if mine:
        chain.mine()
    line = {"schema": 1, "tx_hash": tx, "block": block, "block_hash": chain.hash(block), "from": sender, "to": PROXY,
            "selector": data[:10], "function": FUNCTIONS[fn][0], "status": "failure", "monitor": "SSV Failed Tx Inbox",
            "observed_at": "2026-10-21T00:00:00Z"}
    with open(path, "a") as fh:
        fh.write(json.dumps(line) + "\n")
    return tx


def exceed_limit(op: int) -> str:
    return ERR_EXCEED_VALIDATOR_LIMIT + u256(op).hex()


INCORRECT_STATE = ERR_INCORRECT_CLUSTER_STATE


# ---------------------------------------------------------------- operators (M2)


def operator_data(owner: str, fee: int, count: int, private: bool, active: bool = True) -> bytes:
    return b"".join(u256(x) for x in (int(owner, 16), fee, count, 0, int(private), int(active)))


def set_operator(chain, op: int, eth: int, ssv: int, private: bool = False, active: bool = True) -> None:
    """getOperatorById / getOperatorByIdSSV of `op`, effective from the pending block."""
    owner = "0x" + ("%02x" % (op % 256)) * 20
    chain.stub_call(VIEWS, GET_OPERATOR_BY_ID + u256(op), operator_data(owner, 1, eth, private, active))
    chain.stub_call(VIEWS, GET_OPERATOR_BY_ID_SSV + u256(op), operator_data(owner, 1, ssv, private, active))
