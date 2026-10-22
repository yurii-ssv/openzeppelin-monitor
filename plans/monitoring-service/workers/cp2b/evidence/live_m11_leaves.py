"""LIVE, read-only: M11's leaf comparison (M11UpdaterLiveness.compare_leaves, the code the check runs) on mainnet.

Clusters: SSV API v4 (migrated, active, with validators), each tuple validated against the proxy's ETH cluster
hash slot at the pinned block (what inventory.py does); the leaf from the oracle proof API; the on-chain EB from
SSVNetworkViews.getEffectiveBalance at the same block. Only GETs, eth_blockNumber, eth_getStorageAt and eth_call.

  reconcile/.venv/bin/python plans/monitoring-service/workers/cp2b/evidence/live_m11_leaves.py <rpc_url> [limit]
"""
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[5]
sys.path[:0] = [str(ROOT / "reconcile")]

from checks.m11_updater_liveness import M11UpdaterLiveness  # noqa: E402
from eb_storage import EB_LATEST_COMMITTED, cluster_id, decode_latest_committed, eth_cluster_slot  # noqa: E402
from inventory import API_DEFAULT, ClusterInfo, _hash_word, _http_get_json, api_tuple, fetch_api_clusters  # noqa: E402
from rpc import Chain, Rpc  # noqa: E402

PROXY = "0xDD9BC35aE942eF0cFa76930954a156B3fF30a4E1"
VIEWS = "0xafE830B6Ee262ba11cce5F32fDCd760FFE6a66e4"


def main():
    url = sys.argv[1]
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10**9
    rpc = Rpc(url, timeout=30, retries=3, batch_size=20)
    block = rpc.block_number() - 12
    chain = Chain(rpc, block)
    latest, _ = decode_latest_committed(chain.storage(PROXY, [EB_LATEST_COMMITTED], block)[0])
    rows = [c for c in fetch_api_clusters(API_DEFAULT, http_get=lambda u: _http_get_json(u, 60))
            if c.get("migrated") and c.get("active") and int(c["validatorCount"]) > 0]
    rows = rows[:limit]
    words = chain.storage(PROXY, [eth_cluster_slot(cluster_id(c["ownerAddress"], c["operators"])) for c in rows], block)
    clusters, stale = {}, 0
    for c, word in zip(rows, words):
        cl = api_tuple(c)
        cid = "0x" + cluster_id(c["ownerAddress"], c["operators"]).hex()
        if word != _hash_word(cl):
            stale += 1  # the API row is behind the chain: inventory.py would use the log tuple instead
            continue
        clusters[cid] = ClusterInfo(cid, c["ownerAddress"].lower(), tuple(c["operators"]), "ETH", cl, "api",
                                    frozenset({"api"}), True, c)
    settings = {"views": VIEWS, "proxy": PROXY}
    check = M11UpdaterLiveness({**M11UpdaterLiveness.defaults, "require_inbox": False}, settings)  # leaves only
    ctx = SimpleNamespace(inventory=lambda b: SimpleNamespace(clusters=clusters), rpc=chain)
    t0 = time.time()
    result = check.compare_leaves(ctx, latest, block)
    print(json.dumps({
        "rpc": url.split("//")[1].split("/")[0], "block": block, "latest_committed_ref": latest,
        "api_rows_active_eth": len(rows), "api_tuple_stale_skipped": stale, "compared": result["checked"],
        "mismatches": result["mismatches"], "unknown": len(result["unknown"]),
        "unknown_reasons": sorted({u["reason"] for u in result["unknown"]})[:5], "error": result["error"],
        "rpc_calls": dict(chain.counts), "seconds": round(time.time() - t0, 1)}, indent=1))


if __name__ == "__main__":
    main()
