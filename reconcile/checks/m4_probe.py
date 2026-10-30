"""LIVE probe for M4 (read-only: eth_call, eth_getStorageAt, eth_getBalance, SSV API GETs). Not a check.

Computes the conservation equation of `m4_conservation_deficit.scan` at one recent block, with the staking
pool counted once inside `getNetworkEarnings` (A, the check) and as an extra term (B, the literal spec text):

  cd reconcile && .venv/bin/python -m checks.m4_probe --probe --rpc "$MAINNET_RPC_URL" [--block N]
"""

from __future__ import annotations

import argparse
import json
import sys

from checks.m4_conservation_deficit import scan
from inventory import API_DEFAULT, build, fetch_api_clusters
from rpc import Chain, Rpc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="M4 LIVE probe (read-only).")
    ap.add_argument("--probe", action="store_true", required=True)
    ap.add_argument("--rpc", required=True)
    ap.add_argument("--api", default=API_DEFAULT)
    ap.add_argument("--views", default="0xafE830B6Ee262ba11cce5F32fDCd760FFE6a66e4")
    ap.add_argument("--proxy", default="0xDD9BC35aE942eF0cFa76930954a156B3fF30a4E1")
    ap.add_argument("--block", type=int)
    ap.add_argument("--batch", type=int, default=20)
    args = ap.parse_args(argv)
    rpc = Rpc(args.rpc, timeout=30, retries=4, batch_size=args.batch)
    block = args.block or rpc.block_number() - 12
    rows = fetch_api_clusters(args.api)
    chain = Chain(rpc, block)
    inv = build(chain, args.proxy.lower(), block, rows, {})
    s = scan(chain, inv, args.views.lower(), args.proxy.lower(), block)
    pool = s["staking_pool"] or 0
    out = {"endpoint": args.rpc, "block": block, "block_hash": rpc.block_hash(block), "api_rows": len(rows),
           "inventory": inv.stats(), **{k: v for k, v in s.items() if k != "gaps"}, "gap_count": len(s["gaps"]),
           "gaps": s["gaps"][:20], "A_liabilities": s["liabilities"], "A_surplus": s["surplus"],
           "B_liabilities": s["liabilities"] + pool, "B_surplus": s["surplus"] - pool, "rpc_calls": dict(chain.counts)}
    print(json.dumps(out, indent=1, default=str))
    return 0 if s["complete"] else 1


if __name__ == "__main__":
    sys.exit(main())
