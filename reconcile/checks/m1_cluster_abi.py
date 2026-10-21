"""ABI helpers shared by the CP2d checks (M1, M2): SSVNetworkViews calls on a cluster tuple, OperatorData,
revert selectors and the calldata of the cluster functions the failed-transaction inbox carries.

Signatures are the ones at the pin (ssv-network f7ac487: ISSVViews.sol, ISSVClusters.sol, ISSVNetworkCore.sol);
selectors are derived from them, never written as constants.
"""

from __future__ import annotations

from eb_events import CLUSTER_T
from eb_storage import Cluster, keccak, u256
from rpc import RpcError


def selector(signature: str) -> bytes:
    return keccak(signature.encode())[:4]


def error_selector(signature: str) -> str:
    return "0x" + selector(signature).hex()


IS_LIQUIDATED = selector(f"isLiquidated(address,uint64[],{CLUSTER_T})")
IS_LIQUIDATABLE = selector(f"isLiquidatable(address,uint64[],{CLUSTER_T})")
IS_LIQUIDATABLE_SSV = selector(f"isLiquidatableSSV(address,uint64[],{CLUSTER_T})")
GET_OPERATOR_BY_ID = selector("getOperatorById(uint64)")
GET_OPERATOR_BY_ID_SSV = selector("getOperatorByIdSSV(uint64)")

ERR_INCORRECT_CLUSTER_STATE = error_selector("IncorrectClusterState()")
ERR_CLUSTER_DOES_NOT_EXIST = error_selector("ClusterDoesNotExist()")
ERR_INCORRECT_CLUSTER_VERSION = error_selector("IncorrectClusterVersion()")
ERR_EXCEED_VALIDATOR_LIMIT = error_selector("ExceedValidatorLimitWithData(uint64)")

# Cluster functions carried by the failed-transaction inbox (plan §6): name -> (signature, owner word index or None
# when the owner is msg.sender, word index of the operatorIds offset).
FUNCTIONS = {
    "updateClusterBalance": (f"updateClusterBalance(uint64,address,uint64[],{CLUSTER_T},uint32,bytes32[])", 1, 2),
    "liquidate": (f"liquidate(address,uint64[],{CLUSTER_T})", 0, 1),
    "liquidateSSV": (f"liquidateSSV(address,uint64[],{CLUSTER_T})", 0, 1),
    "migrateClusterToETH": (f"migrateClusterToETH(uint64[],{CLUSTER_T})", None, 0),
    "reactivate": (f"reactivate(uint64[],{CLUSTER_T})", None, 0),
}
FUNCTION_BY_SELECTOR = {"0x" + selector(sig).hex(): name for name, (sig, _, _) in FUNCTIONS.items()}


def encode_cluster_call(sel: bytes, owner: str, operator_ids, cluster: Cluster) -> bytes:
    """`f(address owner, uint64[] operatorIds, Cluster cluster)`: the cluster tuple is static, so the head is
    owner, the array offset (7 words) and the five tuple words; the array follows."""
    ops = list(operator_ids)
    head = u256(int(owner, 16)) + u256(32 * 7) + b"".join(
        u256(int(x)) for x in (cluster.validator_count, cluster.network_fee_index, cluster.index, cluster.active,
                               cluster.balance))
    return sel + head + u256(len(ops)) + b"".join(u256(o) for o in ops)


def revert_selector(exc: RpcError) -> str | None:
    return exc.data[:10].lower() if exc.data and len(exc.data) >= 10 else None


def decode_bool(out: bytes, what: str) -> bool:
    """A view's bool; an empty or short answer is an RPC error, never False."""
    if len(out) < 32:
        raise RpcError(f"{what}: returned {len(out)} bytes, expected a bool")
    word = int.from_bytes(out[:32], "big")
    if word > 1:
        raise RpcError(f"{what}: malformed bool {word:#x}")
    return bool(word)


def decode_operator(out: bytes, what: str) -> dict:
    """ISSVViews.OperatorData (owner, fee, validatorCount, whitelistedAddress, isPrivate, isActive): six static words."""
    if len(out) < 6 * 32:
        raise RpcError(f"{what}: returned {len(out)} bytes, expected OperatorData")
    w = [int.from_bytes(out[i * 32:(i + 1) * 32], "big") for i in range(6)]
    if w[2] >> 32 or w[4] > 1 or w[5] > 1:
        raise RpcError(f"{what}: malformed OperatorData")
    return {"owner": "0x%040x" % w[0], "fee": w[1], "validator_count": w[2], "whitelisted": "0x%040x" % w[3],
            "private": bool(w[4]), "active": bool(w[5])}


def decode_cluster_calldata(tx: dict) -> tuple[str, str, tuple[int, ...]] | None:
    """(function name, owner, operatorIds) from a transaction calling one of FUNCTIONS; None for another function
    or calldata that does not decode."""
    data = tx.get("input") or tx.get("data") or "0x"
    name = FUNCTION_BY_SELECTOR.get(data[:10].lower())
    if name is None:
        return None
    _, owner_word, ops_word = FUNCTIONS[name]
    try:
        body = bytes.fromhex(data[10:])

        def word(i: int) -> int:
            if len(body) < 32 * (i + 1):
                raise ValueError("truncated calldata")
            return int.from_bytes(body[32 * i:32 * (i + 1)], "big")

        owner = ("0x%040x" % word(owner_word)) if owner_word is not None else (tx.get("from") or "").lower()
        off = word(ops_word)
        n = int.from_bytes(body[off:off + 32], "big")
        if n == 0 or n > 64 or len(body) < off + 32 * (n + 1):
            return None
        ops = tuple(int.from_bytes(body[off + 32 * (i + 1):off + 32 * (i + 2)], "big") for i in range(n))
    except (ValueError, IndexError):
        return None
    if not owner.startswith("0x") or len(owner) != 42 or any(o >> 64 for o in ops):
        return None
    return name, owner, ops


def decode_uint_arg(revert_data: str | None) -> int | None:
    """The first argument word of an `Error(uint…)` revert (e.g. ExceedValidatorLimitWithData's operatorId)."""
    if not revert_data or len(revert_data) < 10 + 64:
        return None
    try:
        return int(revert_data[10:74], 16)
    except ValueError:
        return None
