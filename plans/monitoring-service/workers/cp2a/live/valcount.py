"""Scratch LIVE (read-only): getNetworkValidatorsCount vs the API's active ETH validator sum."""
import json, sys
sys.path.insert(0, sys.argv[1])
from inventory import fetch_api_clusters
from rpc import Rpc
from params import selector
rpc = Rpc(sys.argv[2], timeout=30, retries=4)
rows = fetch_api_clusters()
block = rpc.block_number() - 12
out = rpc.call("eth_call", [{"to": "0xafE830B6Ee262ba11cce5F32fDCd760FFE6a66e4", "data": "0x" + selector("getNetworkValidatorsCount()").hex()}, hex(block)])
eth_active = [r for r in rows if r.get("migrated") and r.get("active")]
print(json.dumps({"block": block, "getNetworkValidatorsCount": int(out, 16), "api_active_eth_validators": sum(int(r["validatorCount"]) for r in eth_active),
  "api_active_eth_clusters": len(eth_active), "api_max_block": max(int(r.get("blockNumber") or 0) for r in rows)}, indent=1))
