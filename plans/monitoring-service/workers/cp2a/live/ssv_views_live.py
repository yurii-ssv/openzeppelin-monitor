"""LIVE (read-only, repair 1): the legacy SSV view variants and header timestamps that M14 now reads.

For up to 5 active legacy (migrated=false) clusters with validators from SSV API v4 page 1, validate the API tuple
against storage (`clusters[id]`, not `ethClusters[id]`) at one block, then call isLiquidatableSSV / getBalanceSSV /
getBurnRateSSV (checks.m3_views.cluster_calls(version="SSV")) and the ETH variant (must revert: VERSION check), and
read the block's header timestamp with checks.m3_views.block_times (hash-checked).
    reconcile/.venv/bin/python plans/monitoring-service/workers/cp2a/live/ssv_views_live.py reconcile <rpc> <out.json>
"""
import json
import sys

sys.path.insert(0, sys.argv[1])
from checks.m3_views import IS_LIQUIDATABLE, block_times, cluster_calls, encode_cluster_args, word  # noqa: E402
from eb_storage import cluster_id, eth_cluster_slot, ssv_cluster_slot  # noqa: E402
from inventory import API_DEFAULT, _http_get_json, api_tuple  # noqa: E402
from rpc import Chain, Rpc, RpcError  # noqa: E402

VIEWS, PROXY = "0xafe830b6ee262ba11cce5f32fdcd760ffe6a66e4", "0xdd9bc35ae942ef0cfa76930954a156b3ff30a4e1"
rpc = Rpc(sys.argv[2], timeout=30, retries=2)
block = rpc.block_number() - 12
chain = Chain(rpc, block)
page = _http_get_json(f"{API_DEFAULT}/clusters?perPage=200&page=1")
legacy = [r for r in page["clusters"] if not r.get("migrated") and r.get("active") and int(r["validatorCount"]) > 0]
out = {"endpoint": sys.argv[2], "block": block, "block_hash": rpc.block_hash(block), "api_rows_page1": len(page["clusters"]),
       "legacy_active_on_page1": len(legacy), "clusters": []}
out["timestamp"] = block_times(chain, [block], {block: out["block_hash"]})[block]
for r in legacy[:5]:
    owner, ops = r["ownerAddress"].lower(), tuple(int(o) for o in r["operators"])
    cid = cluster_id(owner, ops)
    eth_word, ssv_word = chain.storage(PROXY, [eth_cluster_slot(cid), ssv_cluster_slot(cid)])
    tup = api_tuple(r)
    row = {"cluster_id": "0x" + cid.hex(), "eth_slot_set": bool(eth_word), "ssv_slot_set": bool(ssv_word),
           "api_tuple_validates": int.from_bytes(tup.hash_data(), "big") == ssv_word}
    if row["api_tuple_validates"]:
        liq, bal, burn = chain.call_many(cluster_calls(VIEWS, owner, ops, tup, "SSV"))
        row.update(isLiquidatableSSV=word(liq, "isLiquidatableSSV"), getBalanceSSV=str(word(bal, "getBalanceSSV")),
                   getBurnRateSSV=str(word(burn, "getBurnRateSSV")))
        try:
            chain.call(VIEWS, IS_LIQUIDATABLE + encode_cluster_args(owner, ops, tup))
            row["eth_variant"] = "answered (unexpected)"
        except RpcError as exc:
            if exc.transport:
                raise
            row["eth_variant"] = f"reverted {exc.data}"
    out["clusters"].append(row)
out["rpc_calls"] = dict(chain.counts)
print(json.dumps(out, indent=1))
with open(sys.argv[3], "w") as fh:
    json.dump(out, fh, indent=1)
