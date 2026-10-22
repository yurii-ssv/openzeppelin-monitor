"""SSVNetworkViews reads shared by the liquidation family (M3, M4, M14). Not a check.

Signatures are the ones at the pin (ssv-network v2.0.0 + PR #614, f7ac487: ISSVViews.sol,
ISSVClusters.sol, SSVStorage.sol). Every result is decoded strictly: an empty or short `eth_call`
result is an error (`ValueError`), never a zero. A cluster whose views revert is reported as
UNKNOWN by `evaluate`, never as clean; a transport failure propagates (`RpcError`, the run is retried).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from eb_storage import MAIN_BASE, Cluster, cluster_id, eth_cluster_slot, keccak, ssv_cluster_slot, u256
from inventory import ClusterInfo
from rpc import RpcError

CLUSTER_ARGS = "(address,uint64[],(uint32,uint64,uint64,bool,uint256))"


def selector(signature: str) -> bytes:
    return keccak(signature.encode())[:4]


IS_LIQUIDATABLE = selector("isLiquidatable" + CLUSTER_ARGS)
GET_BALANCE = selector("getBalance" + CLUSTER_ARGS)
GET_BURN_RATE = selector("getBurnRate" + CLUSTER_ARGS)
# legacy SSV clusters: the ETH variants enforce VERSION_ETH (SSVViews at the pin), these enforce VERSION_SSV;
# balances and burn rates are in SSV (token wei, wei/block)
IS_LIQUIDATABLE_SSV = selector("isLiquidatableSSV" + CLUSTER_ARGS)
GET_BALANCE_SSV = selector("getBalanceSSV" + CLUSTER_ARGS)
GET_BURN_RATE_SSV = selector("getBurnRateSSV" + CLUSTER_ARGS)
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


def cluster_calls(views: str, owner: str, operator_ids, cluster: Cluster, version: str = "ETH") -> list[tuple[str, bytes]]:
    """isLiquidatable, getBalance, getBurnRate — the `SSV` variants for a legacy cluster."""
    args = encode_cluster_args(owner, operator_ids, cluster)
    sels = (IS_LIQUIDATABLE, GET_BALANCE, GET_BURN_RATE) if version == "ETH" else \
        (IS_LIQUIDATABLE_SSV, GET_BALANCE_SSV, GET_BURN_RATE_SSV)
    return [(views, s + args) for s in sels]


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


def stored_cluster(rpc, proxy: str, owner: str, operator_ids, candidates, block: int | None = None) -> ClusterInfo:
    """A cluster the inventory does not list (an API page that omits it is not evidence of anything), read from
    storage at `block`: the version from which slot is set (None: not in storage, a positive fact), the tuple from
    `candidates` [(source, Cluster)] (persisted or log tuples) only if its hash equals the stored word."""
    cid = cluster_id(owner, operator_ids)
    eth_word, ssv_word = rpc.storage(proxy, [eth_cluster_slot(cid), ssv_cluster_slot(cid)], block)
    version = "ETH" if eth_word else "SSV" if ssv_word else None
    stored = eth_word or ssv_word
    src, cl = next(((s, c) for s, c in candidates if c is not None and stored
                    and int.from_bytes(c.hash_data(), "big") == stored), (None, None))
    return ClusterInfo(cluster_id="0x" + cid.hex(), owner=owner.lower(), operator_ids=tuple(operator_ids),
                       version=version, cluster=cl, tuple_source=src, sources=frozenset({"persisted"}),
                       api_migrated=None)


def tuple_json(cl: Cluster) -> str:
    return json.dumps([cl.validator_count, cl.network_fee_index, cl.index, cl.active, str(cl.balance)])


def tuple_from_json(text: str | None) -> Cluster | None:
    if not text:
        return None
    c = json.loads(text)
    return Cluster(int(c[0]), int(c[1]), int(c[2]), bool(c[3]), int(c[4]))


def block_times(rpc, blocks, hashes: dict | None = None) -> dict[int, int]:
    """{block: timestamp} from the blocks' own headers (chain time, not the worker clock). A block the node does not
    have, or whose hash differs from the expected one in `hashes` (a reorg while reading), raises RpcError: the run
    is retried, never evaluated on another fork's time."""
    blocks = sorted(set(blocks))
    if not blocks:
        return {}
    rpc.counts["eth_getBlockByNumber"] += len(blocks)
    out = rpc.rpc.batch([("eth_getBlockByNumber", [hex(b), False]) for b in blocks])
    times = {}
    for b, blk in zip(blocks, out):
        want = (hashes or {}).get(b)
        if not blk or blk.get("timestamp") is None:
            raise RpcError(f"block {b}: header or timestamp unavailable")
        if want and blk["hash"].lower() != want.lower():
            raise RpcError(f"block {b}: hash {blk['hash']} is not the expected {want} (reorganised while reading)")
        times[b] = int(blk["timestamp"], 16)
    return times


# The block hashes each liquidation-family check saw when it ran (QA cp2a-2 H1 shape): its checkpoint and the
# framework's recorded blocks (event blocks, scan ends) up to it, copied because the framework deletes its own
# above the ancestor before the reorg run. After a reorg that lowers the checkpoint below a check's evidence, one of
# these blocks that the node now has with another hash proves that evidence left the canonical chain; when every
# one probed is missing (a shorter chain, or a node behind the checkpoint) nothing is proven and the check holds its
# incidents until it evaluates that block again.
RUNS_SCHEMA = """
CREATE TABLE IF NOT EXISTS liq_runs (check_id TEXT NOT NULL, block INTEGER NOT NULL, hash TEXT NOT NULL,
                                     PRIMARY KEY (check_id, block));
"""
RUNS_KEPT = 1_024  # per check; with older evidence nothing below it may be left to probe (held until re-evaluated)


def record_run(ctx, block: int) -> None:
    """Remember the blocks seen up to this run's checkpoint (at the end of a run; rolled back with it)."""
    cp = ctx.db.meta("checkpoint") or {}
    if cp.get("block") != block or not cp.get("hash"):
        return
    last = ctx.db.conn.execute("SELECT MAX(block) FROM liq_runs WHERE check_id=?", (ctx.check_id,)).fetchone()[0]
    ctx.db.conn.execute("INSERT OR REPLACE INTO liq_runs SELECT ?, number, hash FROM blocks WHERE number > ? AND "
                        "number < ?", (ctx.check_id, block - RUNS_KEPT if last is None else last, block))
    ctx.db.conn.execute("INSERT OR REPLACE INTO liq_runs VALUES (?,?,?)", (ctx.check_id, block, cp["hash"]))
    ctx.db.conn.execute("DELETE FROM liq_runs WHERE check_id=? AND block NOT IN (SELECT block FROM liq_runs WHERE "
                        "check_id=? ORDER BY block DESC LIMIT ?)", (ctx.check_id, ctx.check_id, RUNS_KEPT))


def first_replaced(ctx, ancestor: int, upto: int, probe: int = 8) -> int | None:
    """The lowest block in (ancestor, upto] that this check saw on the old chain and that the node now has with another
    hash (everything the check saw from there on left the canonical chain), or None: no such block, or every one
    probed is missing. Reorg runs only, before `forget_runs`."""
    rows = ctx.db.conn.execute("SELECT block, hash FROM liq_runs WHERE check_id=? AND block > ? AND block <= ? "
                               "ORDER BY block LIMIT ?", (ctx.check_id, ancestor, upto, probe)).fetchall()
    if not rows:
        return None
    ctx.rpc.counts["eth_getBlockByNumber"] += len(rows)
    out = ctx.rpc.rpc.batch([("eth_getBlockByNumber", [hex(r[0]), False]) for r in rows])
    return next((r[0] for r, blk in zip(rows, out) if blk and blk["hash"].lower() != r[1].lower()), None)


def forget_runs(ctx, ancestor: int) -> None:
    """Drop the blocks of the old chain (above the ancestor)."""
    ctx.db.conn.execute("DELETE FROM liq_runs WHERE check_id=? AND block > ?", (ctx.check_id, ancestor))


def ensure_columns(conn, table: str, columns: dict) -> None:
    """Additive migration of a check's own table on a database created by an earlier version of the check."""
    have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    for name, decl in columns.items():
        if name not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def short(cid: str) -> str:
    return cid[:10] + "…"
