"""Scratch LIVE smoke (read-only): M3 views via checks.m3_views.evaluate on the storage-validated inventory."""
import json, sys
sys.path.insert(0, sys.argv[1])
from checks.m3_views import evaluate, blocks_until_liquidatable
from inventory import build, fetch_api_clusters
from params import Params
from rpc import Chain, Rpc
VIEWS, PROXY = "0xafe830b6ee262ba11cce5f32fdcd760ffe6a66e4", "0xdd9bc35ae942ef0cfa76930954a156b3ff30a4e1"
rpc = Rpc(sys.argv[2], timeout=30, retries=4, batch_size=20)
block = rpc.block_number() - 12
chain = Chain(rpc, block)
p = Params(VIEWS, PROXY); p.load(chain, block)
inv = build(chain, PROXY, block, fetch_api_clusters(), {})
targets = [c for c in inv.clusters.values() if c.version == "ETH" and c.valid and c.cluster.active and c.cluster.validator_count]
views, unknown = evaluate(chain, VIEWS, targets)
ahead = {cid: blocks_until_liquidatable(v, p.liquidation_threshold_period, p.minimum_liquidation_collateral) for cid, v in views.items()}
print(json.dumps({"endpoint": sys.argv[2], "block": block, "block_hash": rpc.block_hash(block), "targets": len(targets),
  "evaluated": len(views), "unknown": len(unknown), "liquidatable": sorted(c for c, v in views.items() if v.liquidatable),
  "computed_zero": sorted(c for c, v in views.items() if v.balance == 0),
  "within_300_blocks": sum(1 for a in ahead.values() if a is not None and a <= 300),
  "within_7200_blocks": sum(1 for a in ahead.values() if a is not None and a <= 7200),
  "params": {"ltp": p.liquidation_threshold_period, "min_coll": p.minimum_liquidation_collateral},
  "rpc_calls": dict(chain.counts)}, indent=1))
