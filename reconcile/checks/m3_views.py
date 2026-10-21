"""SSVNetworkViews reads shared by the liquidation family (M3, M4, M14). Not a check.

Signatures are the ones at the pin (ssv-network v2.0.0 + PR #614, f7ac487: ISSVViews.sol,
ISSVClusters.sol, SSVStorage.sol). Every result is decoded strictly: an empty or short `eth_call`
result is an error (`ValueError`), never a zero. A cluster whose views revert is reported as
UNKNOWN by `evaluate`, never as clean; a transport failure propagates (`RpcError`, the run is retried).
"""

from __future__ import annotations

from dataclasses import dataclass

from eb_storage import MAIN_BASE, Cluster, keccak, u256
from rpc import RpcError

CLUSTER_ARGS = "(address,uint64[],(uint32,uint64,uint64,bool,uint256))"


def selector(signature: str) -> bytes:
    return keccak(signature.encode())[:4]


IS_LIQUIDATABLE = selector("isLiquidatable" + CLUSTER_ARGS)
GET_BALANCE = selector("getBalance" + CLUSTER_ARGS)
GET_BURN_RATE = selector("getBurnRate" + CLUSTER_ARGS)
GET_OPERATOR_EARNINGS = selector("getOperatorEarnings(uint64)")
GET_NETWORK_EARNINGS = selector("getNetworkEarnings()")
STAKING_ETH_POOL_BALANCE = selector("stakingEthPoolBalance()")
# ProtocolStorage.ethDaoValidatorCount: the validators of active ETH clusters (inventory completeness)
GET_NETWORK_VALIDATORS_COUNT = selector("getNetworkValidatorsCount()")
# SSVClusters entry points whose reverts the failed-transaction inbox carries (M14)
LIQUIDATE = {"liquidate": selector("liquidate" + CLUSTER_ARGS), "liquidateSSV": selector("liquidateSSV" + CLUSTER_ARGS)}

# StorageData.lastOperatorId (Counters.Counter): member 8 of ssv.network.storage.main
MAIN_LAST_OPERATOR_ID = MAIN_BASE + 8


def encode_cluster_args(owner: str, operator_ids, cluster: Cluster) -> bytes:
    """ABI encoding of (address owner, uint64[] operatorIds, Cluster cluster); the tuple is static (inline)."""
    head = u256(int(owner, 16)) + u256(7 * 32) + b"".join(u256(int(x)) for x in cluster.as_tuple())
    return head + u256(len(operator_ids)) + b"".join(u256(i) for i in operator_ids)


def decode_cluster_args(data: bytes) -> tuple[str, tuple[int, ...], Cluster]:
    """Inverse of `encode_cluster_args` (calldata without the selector); raises ValueError when malformed."""
    if len(data) < 8 * 32:
        raise ValueError("calldata too short for (address,uint64[],Cluster)")
    w = [int.from_bytes(data[i : i + 32], "big") for i in range(0, (len(data) // 32) * 32, 32)]
    owner_word, offset = w[0], w[1]
    count, nfi, index, active, balance = w[2:7]
    if owner_word >> 160 or offset % 32 or count >> 32 or nfi >> 64 or index >> 64 or active > 1:
        raise ValueError("malformed (address,uint64[],Cluster) calldata")
    start = offset // 32
    if start >= len(w):
        raise ValueError("operatorIds offset out of range")
    n = w[start]
    if n > 64 or start + 1 + n > len(w):
        raise ValueError("implausible operatorIds length")
    ops = tuple(w[start + 1 : start + 1 + n])
    if any(o >> 64 for o in ops):
        raise ValueError("operatorId overflows uint64")
    return "0x%040x" % owner_word, ops, Cluster(count, nfi, index, bool(active), balance)


def word(out: bytes, what: str) -> int:
    """The first 32-byte word of an eth_call result; an empty result is an error, not 0."""
    if len(out) < 32:
        raise ValueError(f"{what} returned {len(out)} bytes")
    return int.from_bytes(out[:32], "big")


@dataclass(frozen=True)
class ClusterView:
    """`isLiquidatable`, `getBalance` (view-computed, not the stale stored balance) and `getBurnRate` (wei/block)."""

    liquidatable: bool
    balance: int
    burn_rate: int


def cluster_calls(views: str, owner: str, operator_ids, cluster: Cluster) -> list[tuple[str, bytes]]:
    args = encode_cluster_args(owner, operator_ids, cluster)
    return [(views, IS_LIQUIDATABLE + args), (views, GET_BALANCE + args), (views, GET_BURN_RATE + args)]


def _decode(out: list[bytes]) -> ClusterView:
    liq = word(out[0], "isLiquidatable")
    if liq > 1:
        raise ValueError(f"isLiquidatable returned {liq}")
    return ClusterView(bool(liq), word(out[1], "getBalance"), word(out[2], "getBurnRate"))


def evaluate(rpc, views: str, targets: list, block: int | None = None) -> tuple[dict, dict]:
    """Batched views for active ETH clusters (`ClusterInfo` with a storage-validated tuple) at one block.

    Returns ({cluster_id: ClusterView}, {cluster_id: reason}) — the second map holds the clusters that could
    not be evaluated (a revert or malformed result): UNKNOWN, never clean. Transport errors propagate."""
    views_out: dict[str, ClusterView] = {}
    unknown: dict[str, str] = {}
    if not targets:
        return views_out, unknown
    calls = [c for t in targets for c in cluster_calls(views, t.owner, t.operator_ids, t.cluster)]
    try:
        out = rpc.call_many(calls, block)
    except RpcError as exc:
        if exc.transport:
            raise
        out = None  # one call reverted: isolate it per cluster
    for i, t in enumerate(targets):
        try:
            part = out[3 * i : 3 * i + 3] if out is not None else rpc.call_many(calls[3 * i : 3 * i + 3], block)
            views_out[t.cluster_id] = _decode(part)
        except RpcError as exc:
            if exc.transport:
                raise
            unknown[t.cluster_id] = f"view reverted: {exc} {exc.data or ''}".strip()
        except ValueError as exc:
            unknown[t.cluster_id] = str(exc)
    return views_out, unknown


def inventory_gap(rpc, views: str, eth_infos, block: int | None = None) -> str | None:
    """None when the validators of the active, storage-validated ETH clusters add up to the chain's own
    `getNetworkValidatorsCount()`; otherwise why the inventory cannot be taken as complete (a cluster missing from
    the API and the logs would otherwise never be evaluated: a missing input, not a clean one)."""
    try:
        count = word(rpc.call(views, GET_NETWORK_VALIDATORS_COUNT, block), "getNetworkValidatorsCount")
    except RpcError as exc:
        if exc.transport:
            raise
        return f"getNetworkValidatorsCount() reverted ({exc.data or exc})"
    except ValueError as exc:
        return str(exc)
    listed = sum(c.cluster.validator_count for c in eth_infos if c.valid and c.cluster.active)
    if listed != count:
        return (f"inventory incomplete: its active ETH clusters hold {listed} validators, "
                f"getNetworkValidatorsCount() is {count}")
    return None


def blocks_until_liquidatable(view: ClusterView, threshold_period: int, minimum_collateral: int) -> int | None:
    """Blocks until `balance < max(thresholdPeriod * burnRate, minimumCollateral)` at the current burn rate
    (ClusterLib.isLiquidatableWithEB); 0 when liquidatable now; None when it never crosses (no burn)."""
    if view.liquidatable:
        return 0
    if view.burn_rate == 0:
        return None
    threshold = max(threshold_period * view.burn_rate, minimum_collateral)
    if view.balance < threshold:
        return 0
    return (view.balance - threshold) // view.burn_rate + 1


def short(cid: str) -> str:
    return cid[:10] + "…"
