"""Decoding of the SSVNetwork events the EB reconciler replays.

Signatures are the ones at the pin (contracts/interfaces/ISSVValidators.sol,
ISSVClusters.sol, ISSVDAO.sol). Cluster = (uint32 validatorCount,
uint64 networkFeeIndex, uint64 index, bool active, uint256 balance); it is a
static tuple, so it is encoded inline in the event data head.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from eb_storage import Cluster, cluster_id, keccak

CLUSTER_T = "(uint32,uint64,uint64,bool,uint256)"

SIGNATURES = {
    "ValidatorAdded": f"ValidatorAdded(address,uint64[],bytes,bytes,{CLUSTER_T})",
    "ValidatorRemoved": f"ValidatorRemoved(address,uint64[],bytes,{CLUSTER_T})",
    "ClusterBalanceUpdated": f"ClusterBalanceUpdated(address,uint64[],uint64,uint32,{CLUSTER_T})",
    "RootCommitted": "RootCommitted(bytes32,uint64)",
    "ClusterLiquidated": f"ClusterLiquidated(address,uint64[],{CLUSTER_T})",
    "ClusterReactivated": f"ClusterReactivated(address,uint64[],{CLUSTER_T})",
    "ClusterDeposited": f"ClusterDeposited(address,uint64[],uint256,{CLUSTER_T})",
    "ClusterWithdrawn": f"ClusterWithdrawn(address,uint64[],uint256,{CLUSTER_T})",
    "ClusterMigratedToETH": f"ClusterMigratedToETH(address,uint64[],uint256,uint256,uint32,{CLUSTER_T})",
    "OracleReplaced": "OracleReplaced(uint32,address,address)",
}

TOPIC0 = {name: "0x" + keccak(sig.encode()).hex() for name, sig in SIGNATURES.items()}
KIND_BY_TOPIC0 = {topic: name for name, topic in TOPIC0.items()}

CLUSTER_KINDS = {
    "ValidatorAdded",
    "ValidatorRemoved",
    "ClusterBalanceUpdated",
    "ClusterLiquidated",
    "ClusterReactivated",
    "ClusterDeposited",
    "ClusterWithdrawn",
    "ClusterMigratedToETH",
}


@dataclass
class Event:
    kind: str
    block: int
    log_index: int
    tx_index: int
    tx_hash: str
    block_hash: str
    cluster_id: bytes | None = None
    owner: str | None = None
    operator_ids: tuple[int, ...] = ()
    cluster: Cluster | None = None
    pubkey: str | None = None  # ValidatorAdded / ValidatorRemoved
    shares_len: int | None = None  # ValidatorAdded
    ref_block: int | None = None  # ClusterBalanceUpdated (the root's reference block S), RootCommitted
    eb: int | None = None  # ClusterBalanceUpdated / ClusterMigratedToETH, in ETH
    root: str | None = None  # RootCommitted
    extra: dict = field(default_factory=dict)

    @property
    def pos(self) -> tuple[int, int]:
        return (self.block, self.log_index)


def _words(data: bytes) -> list[int]:
    return [int.from_bytes(data[i : i + 32], "big") for i in range(0, len(data), 32)]


def _dyn_bytes(data: bytes, offset: int) -> bytes:
    n = int.from_bytes(data[offset : offset + 32], "big")
    out = data[offset + 32 : offset + 32 + n]
    if len(out) != n:
        raise ValueError("truncated bytes")
    return out


def _uint64_array(data: bytes, offset: int) -> tuple[int, ...]:
    n = int.from_bytes(data[offset : offset + 32], "big")
    if n > 64:
        raise ValueError("implausible operatorIds length")
    vals = tuple(int.from_bytes(data[offset + 32 * (i + 1) : offset + 32 * (i + 2)], "big") for i in range(n))
    if any(v >> 64 for v in vals):
        raise ValueError("operatorId overflows uint64")
    return vals


def _cluster(words: list[int], start: int) -> Cluster:
    count, nfi, idx, active, bal = words[start : start + 5]
    if count >> 32 or nfi >> 64 or idx >> 64 or active > 1:
        raise ValueError("malformed Cluster tuple")
    return Cluster(count, nfi, idx, bool(active), bal)


def _address(topic: str) -> str:
    return "0x" + topic[-40:].lower()


def decode_log(log: dict) -> Event | None:
    """Decode one eth_getLogs entry; returns None for topics this worker does not track."""
    topics = log["topics"]
    kind = KIND_BY_TOPIC0.get(topics[0].lower()) if topics else None
    if kind is None:
        return None
    data = bytes.fromhex(log["data"][2:])
    w = _words(data[: (len(data) // 32) * 32])
    ev = Event(
        kind=kind,
        block=int(log["blockNumber"], 16),
        log_index=int(log["logIndex"], 16),
        tx_index=int(log["transactionIndex"], 16),
        tx_hash=log["transactionHash"].lower(),
        block_hash=log["blockHash"].lower(),
    )
    if kind == "RootCommitted":
        ev.root = topics[1].lower()
        ev.ref_block = int(topics[2], 16)
        return ev
    if kind == "OracleReplaced":
        ev.extra = {"oracleId": int(topics[1], 16), "old": _address(topics[2]), "new": _address(topics[3])}
        return ev

    ev.owner = _address(topics[1])
    ev.operator_ids = _uint64_array(data, w[0])
    if kind == "ValidatorAdded":
        ev.pubkey = "0x" + _dyn_bytes(data, w[1]).hex()
        ev.shares_len = len(_dyn_bytes(data, w[2]))
        ev.cluster = _cluster(w, 3)
    elif kind == "ValidatorRemoved":
        ev.pubkey = "0x" + _dyn_bytes(data, w[1]).hex()
        ev.cluster = _cluster(w, 2)
    elif kind == "ClusterBalanceUpdated":
        ev.ref_block = int(topics[2], 16)
        ev.eb = w[1]
        ev.cluster = _cluster(w, 2)
    elif kind in ("ClusterLiquidated", "ClusterReactivated"):
        ev.cluster = _cluster(w, 1)
    elif kind in ("ClusterDeposited", "ClusterWithdrawn"):
        ev.extra = {"value": w[1]}
        ev.cluster = _cluster(w, 2)
    elif kind == "ClusterMigratedToETH":
        ev.extra = {"ethDeposited": w[1], "ssvRefunded": w[2]}
        ev.eb = w[3]
        ev.cluster = _cluster(w, 4)
    ev.cluster_id = cluster_id(ev.owner, ev.operator_ids)
    return ev
