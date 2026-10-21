"""Pinned SSVNetwork v2.0.0 storage layout and EB conversions.

Pin: ssv-network v2.0.0 + PR #614 (f7ac487), the code behind the mainnet proxy.
Every value here mirrors a line in that source; a storage-layout upgrade must
revalidate this file (see `verify_layout` in eb_reconcile.py).

  contracts/libraries/storage/SSVStorageEB.sol   ClusterEBSnapshot, StorageEB
  contracts/libraries/storage/SSVStorage.sol     StorageData (clusters, ethClusters, ssvContracts)
  contracts/libraries/ClusterLib.sol             ebToVUnits, vUnitsToEB, hashClusterData
"""

from __future__ import annotations

from dataclasses import dataclass

from Crypto.Hash import keccak as _keccak

BPS_DENOMINATOR = 10_000
DEFAULT_EB_PER_VALIDATOR_ETH = 32
MAX_EB_PER_VALIDATOR_ETH = 2048
UINT64_MASK = (1 << 64) - 1

# EIP-1967 implementation slot (UUPS proxy).
IMPLEMENTATION_SLOT = 0x360894A13BA1A3210667C828492DB98DCA3E2076CC3735A920A3CA505D382BBC

# SSVModules enum order (SSVStorage.sol).
MODULE_INDEX = {
    "SSV_OPERATORS": 0,
    "SSV_CLUSTERS": 1,
    "SSV_DAO": 2,
    "SSV_VIEWS": 3,
    "SSV_OPERATORS_WHITELIST": 4,
    "SSV_STAKING": 5,
    "SSV_VALIDATORS": 6,
}


def keccak(data: bytes) -> bytes:
    return _keccak.new(digest_bits=256, data=data).digest()


def u256(value: int) -> bytes:
    return value.to_bytes(32, "big")


def namespace(name: str) -> int:
    """`uint256(keccak256(name)) - 1`, the diamond-storage base slot."""
    return int.from_bytes(keccak(name.encode()), "big") - 1


EB_BASE = namespace("ssv.network.storage.eb")
MAIN_BASE = namespace("ssv.network.storage.main")

# StorageEB member offsets.
EB_CLUSTER_EB = EB_BASE + 1  # mapping(bytes32 => ClusterEBSnapshot)
EB_LATEST_COMMITTED = EB_BASE + 3  # uint64 latestCommittedBlock | uint32 minBlocksBetweenUpdates

# StorageData member offsets.
MAIN_CLUSTERS = MAIN_BASE + 1  # mapping(bytes32 => bytes32) legacy SSV clusters
MAIN_SSV_CONTRACTS = MAIN_BASE + 3  # mapping(SSVModules => address)
MAIN_ETH_CLUSTERS = MAIN_BASE + 10  # mapping(bytes32 => bytes32) ETH clusters


def mapping_slot(key: bytes, base: int) -> int:
    return int.from_bytes(keccak(key.rjust(32, b"\0") + u256(base)), "big")


def cluster_eb_slot(cluster_id: bytes) -> int:
    return mapping_slot(cluster_id, EB_CLUSTER_EB)


def eth_cluster_slot(cluster_id: bytes) -> int:
    return mapping_slot(cluster_id, MAIN_ETH_CLUSTERS)


def ssv_cluster_slot(cluster_id: bytes) -> int:
    return mapping_slot(cluster_id, MAIN_CLUSTERS)


def module_slot(module: str) -> int:
    return mapping_slot(u256(MODULE_INDEX[module]), MAIN_SSV_CONTRACTS)


@dataclass(frozen=True)
class EBSnapshot:
    """ClusterEBSnapshot packed in one word: vUnits | lastRootBlockNum | lastUpdateBlock."""

    v_units: int
    last_root_block: int
    last_update_block: int

    @classmethod
    def decode(cls, word: int) -> "EBSnapshot":
        return cls(word & UINT64_MASK, (word >> 64) & UINT64_MASK, (word >> 128) & UINT64_MASK)


def decode_latest_committed(word: int) -> tuple[int, int]:
    """Returns (latestCommittedBlock, minBlocksBetweenUpdates)."""
    return word & UINT64_MASK, (word >> 64) & 0xFFFFFFFF


def eb_to_vunits(effective_balance_eth: int) -> int:
    """ClusterLib.ebToVUnits: ceiling division."""
    v = effective_balance_eth * BPS_DENOMINATOR
    return 0 if v == 0 else (v - 1) // DEFAULT_EB_PER_VALIDATOR_ETH + 1


def vunits_to_eb(v_units: int) -> int:
    """ClusterLib.vUnitsToEB: floor division, ETH."""
    return v_units * DEFAULT_EB_PER_VALIDATOR_ETH // BPS_DENOMINATOR


def vunits_to_eth_str(v_units: int) -> str:
    """Display only: exact decimal ETH for a vUnit amount (10,000 vUnits = 32 ETH)."""
    whole, rem = divmod(v_units * DEFAULT_EB_PER_VALIDATOR_ETH, BPS_DENOMINATOR)
    return f"{whole}" if rem == 0 else f"{whole}.{rem:04d}".rstrip("0")


def cluster_id(owner: str, operator_ids: list[int] | tuple[int, ...]) -> bytes:
    """keccak256(abi.encodePacked(owner, operatorIds)); packed array elements are 32-byte padded."""
    return keccak(bytes.fromhex(owner[2:].rjust(40, "0")) + b"".join(u256(i) for i in operator_ids))


@dataclass(frozen=True)
class Cluster:
    validator_count: int
    network_fee_index: int
    index: int
    active: bool
    balance: int

    def hash_data(self) -> bytes:
        """ClusterLib.hashClusterData: packed (count, networkFeeIndex, index, balance, active)."""
        return keccak(
            self.validator_count.to_bytes(4, "big")
            + self.network_fee_index.to_bytes(8, "big")
            + self.index.to_bytes(8, "big")
            + u256(self.balance)
            + (b"\x01" if self.active else b"\x00")
        )

    def as_tuple(self) -> tuple:
        return (self.validator_count, self.network_fee_index, self.index, self.active, self.balance)
