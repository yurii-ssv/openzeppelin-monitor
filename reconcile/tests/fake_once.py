#!/usr/bin/env python3
"""`eb_reconcile.py once --serve-metrics` against the in-memory chain (plan §7 V1B.8).

Builds the #94775 PoC trace on FakeChain (one confirmed M5 incident), runs the real CLI entry point
with the RPC client swapped for FakeRpc, and keeps /metrics up for --hold seconds:

  reconcile/.venv/bin/python reconcile/tests/fake_once.py --port 9464 --hold 30 &
  curl -s http://127.0.0.1:9464/metrics
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

from fakechain import PROXY, FakeChain, FakeRpc, MiniSSV, keys  # noqa: E402

import eb_reconcile  # noqa: E402

OWNER = "0x" + "aa" * 20
OPS = (11, 12, 13, 14)


def poc_chain() -> tuple[FakeChain, int]:
    chain = FakeChain()
    start = chain.number
    ssv = MiniSSV(chain)
    ssv.register(OWNER, OPS, keys(1, 94_775_000))
    s0 = chain.number
    chain.mine()
    ssv.commit(s0)
    ssv.update(OWNER, OPS, 2048)
    s1 = chain.number
    chain.mine()
    dummies = keys(63, 94_775_001)
    ssv.register(OWNER, OPS, dummies)
    ssv.commit(s1)
    ssv.update(OWNER, OPS, 2048)
    ssv.remove(OWNER, OPS, dummies)
    chain.mine(3)
    return chain, start


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9464)
    ap.add_argument("--hold", type=float, default=30)
    args = ap.parse_args()
    chain, start = poc_chain()
    tmp = tempfile.mkdtemp(prefix="fake-once-")
    cfg = {
        "chain_id": 1, "proxy": PROXY, "activation_block": start, "start_block": start, "rpc_url_env": "FAKE_RPC_URL",
        "confirmations": 0, "db_path": f"{tmp}/eb.sqlite",
        "alerts": {"mode": "record", "record_path": f"{tmp}/alerts.jsonl", "label": "fake chain", "digest_interval_s": 0},
        "inventory": {"api_url": None}, "backfill": {"enabled": False},
        "checks": {"m5": {"sweep_interval_s": 0, "full_sweep_interval_s": 0}},
    }
    path = f"{tmp}/config.json"
    with open(path, "w") as fh:
        json.dump(cfg, fh)
    os.environ["FAKE_RPC_URL"] = "fake://"
    eb_reconcile.make_rpc = lambda _cfg: FakeRpc(chain)
    print(f"fake_once: state in {tmp}", file=sys.stderr, flush=True)
    return eb_reconcile.main(["once", "--config", path, "--serve-metrics", "--metrics-port", str(args.port),
                              "--hold", str(args.hold), "--log-level", "warning"])


if __name__ == "__main__":
    sys.exit(main())
