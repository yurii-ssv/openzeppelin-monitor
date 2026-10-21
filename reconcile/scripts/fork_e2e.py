#!/usr/bin/env python3
"""Validation gate 1 on real contracts: replay the #94775 fork PoC on an anvil mainnet fork and
check the worker at each step (latent before removal, confirmed after, resolved after a clean root).

Nothing is sent to mainnet: anvil impersonates the live oracles and fresh accounts locally.

  FORK_RPC_URL=<archive mainnet RPC> .venv/bin/python scripts/fork_e2e.py [--telegram]

--telegram delivers the alerts to TEST_TELEGRAM_TOKEN / TEST_TELEGRAM_CHAT_ID (labelled as a fork test).
Requires foundry (anvil, cast) on PATH.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import eb_model as M  # noqa: E402
from eb_events import decode_log  # noqa: E402
from eb_reconcile import DB, DEFAULTS, Config, RecordOnly, Rpc, Telegram, Worker  # noqa: E402
from eb_storage import EB_LATEST_COMMITTED, cluster_id, decode_latest_committed, keccak, u256  # noqa: E402
from rpc import Chain  # noqa: E402

PROXY = "0xDD9BC35aE942eF0cFa76930954a156B3fF30a4E1"
VIEWS = "0xafE830B6Ee262ba11cce5F32fDCd760FFE6a66e4"
PORT = 8547
URL = f"http://127.0.0.1:{PORT}"
OP_OWNER = "0x00000000000000000000000000000000009477A1"
OWNER = "0x00000000000000000000000000000000009477B2"
FEE = 1_778_800_000
CLUSTER_T = "(uint32,uint64,uint64,bool,uint256)"


def rpc(method, params):
    req = urllib.request.Request(URL, json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
                                 {"content-type": "application/json"})
    out = json.load(urllib.request.urlopen(req, timeout=120))
    if "error" in out:
        raise RuntimeError(f"{method}: {out['error']}")
    return out["result"]


def cast(*args) -> str:
    return subprocess.run(["cast", *args, "--rpc-url", URL], check=True, capture_output=True, text=True).stdout.strip()


def send(sender, sig, *args, value=None) -> dict:
    extra = ["--value", str(value)] if value else []
    out = cast("send", "--unlocked", "--from", sender, "--json", *extra, PROXY, sig, *args)
    rcpt = json.loads(out)
    if int(rcpt["status"], 16) != 1:
        raise RuntimeError(f"{sig} reverted")
    return rcpt


def cluster_from(rcpt) -> str:
    evs = [e for e in (decode_log(lg) for lg in rcpt["logs"]) if e and e.cluster]
    c = evs[-1].cluster
    return f"({c.validator_count},{c.network_fee_index},{c.index},{str(c.active).lower()},{c.balance})"


def block() -> int:
    return int(rpc("eth_blockNumber", []), 16)


def mine(n=1):
    rpc("anvil_mine", [hex(n)])


def arr(xs) -> str:
    return "[" + ",".join(str(x) for x in xs) + "]"


def key(seed: int) -> str:
    return "0x" + format(seed, "x").rjust(96, "0")


def leaf_root(cid: bytes, eb: int) -> str:
    return "0x" + keccak(keccak(cid + u256(eb))).hex()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--telegram", action="store_true")
    ap.add_argument("--keep", action="store_true", help="leave anvil running")
    args = ap.parse_args()
    fork_url = os.environ.get("FORK_RPC_URL")
    if not fork_url or not shutil.which("anvil"):
        raise SystemExit("set FORK_RPC_URL and install foundry")

    anvil = subprocess.Popen(["anvil", "--fork-url", fork_url, "--port", str(PORT), "--silent",
                              "--fork-retry-backoff", "2000", "--retries", "8"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(120):
            try:
                block()
                break
            except Exception:  # noqa: BLE001
                time.sleep(1)
        fork_block = block()
        print("fork block", fork_block)
        for a in (OP_OWNER, OWNER):
            rpc("anvil_setBalance", [a, hex(10**22)])
            rpc("anvil_impersonateAccount", [a])

        oracle_ids = cast("call", VIEWS, "getActiveOracleIds()(uint32[4])").strip("[]").split(", ")
        oracles = [cast("call", VIEWS, "getOracle(uint32)(address)", i) for i in oracle_ids[:3]]
        for o in oracles:
            rpc("anvil_setBalance", [o, hex(10**19)])
            rpc("anvil_impersonateAccount", [o])

        ops = []
        for i in range(4):
            r = send(OP_OWNER, "registerOperator(bytes,uint256,bool)", key(94_775 + 1000 + i), str(FEE), "false")
            added = [lg for lg in r["logs"] if lg["topics"][0] == "0x" + keccak(b"OperatorAdded(uint64,address,bytes,uint256)").hex()]
            ops.append(int(added[0]["topics"][1], 16))
        ops.sort()
        cid = cluster_id(OWNER, ops)
        print("operators", ops, "cluster", "0x" + cid.hex())

        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config({**DEFAULTS, **json.loads((HERE / "config.example.json").read_text())})
            cfg.raw.update(start_block=fork_block + 1, activation_block=fork_block + 1, confirmations=0,
                           db_path=f"{tmp}/eb.sqlite", proxy=PROXY.lower(), inbox={}, inventory={"api_url": None},
                           metrics={})
            cfg.raw["checks"]["m5"].update(sweep_interval_s=0, full_sweep_interval_s=0)
            cfg.raw["alerts"] = {**cfg.alerts, "label": "MAINNET FORK TEST (not a real incident)", "digest_interval_s": 0}
            notifier = RecordOnly()
            if args.telegram:
                env = dict(line.split("=", 1) for line in (HERE.parent / ".env").read_text().splitlines()
                           if "=" in line and not line.startswith("#"))
                notifier = Telegram(env["TEST_TELEGRAM_TOKEN"], env["TEST_TELEGRAM_CHAT_ID"])
            w = Worker(cfg, Rpc(URL, batch_size=50), DB(cfg.db_path), notifier)
            w.bootstrap()

            def check(label, status, ref=None, obs=None, disc=None):
                w.check_reorg()
                w.scan()
                w.run_checks(force=True)
                w.deliver()
                evals = {e.cluster_id: e for e in w.checks["m5"].last_evals}
                e = evals[cid]
                got = (e.status, e.reference_vunits, e.observed_vunits, e.discrepancy_vunits)
                want = (status, ref, obs, disc)
                ok = all(b is None or a == b for a, b in zip(got, want))
                print(f"{'PASS' if ok else 'FAIL'} {label}: status={e.status} reference={e.reference_vunits} "
                      f"observed={e.observed_vunits} discrepancy={e.discrepancy_vunits} matched={len(e.matched_keys)}"
                      + (f" reasons={e.reasons}" if e.reasons else ""))
                if not ok:
                    raise SystemExit(1)
                return e

            r = send(OWNER, f"registerValidator(bytes,uint64[],bytes,{CLUSTER_T})", key(94_775_000), arr(ops), "0x",
                     "(0,0,0,true,0)", value=10**21)
            cl = cluster_from(r)
            mine()
            s0 = block()
            mine()
            for o in oracles:
                send(o, "commitRoot(bytes32,uint64)", leaf_root(cid, 2048), str(s0))
            r = send(OWNER, f"updateClusterBalance(uint64,address,uint64[],{CLUSTER_T},uint32,bytes32[])",
                     str(s0), OWNER, arr(ops), cl, "2048", "[]")
            cl = cluster_from(r)
            check("honest 2,048 ETH root", M.CLEAN, 640_000, 640_000, 0)

            _, min_blocks = decode_latest_committed(int(rpc("eth_getStorageAt", [PROXY, hex(EB_LATEST_COMMITTED), "latest"]), 16))
            mine(max(min_blocks, 1))
            s1 = block()
            mine()
            dummies = [key(94_775_001 + i) for i in range(63)]
            r = send(OWNER, f"bulkRegisterValidator(bytes[],uint64[],bytes[],{CLUSTER_T})", arr(dummies), arr(ops),
                     arr(["0x"] * 63), cl)
            cl = cluster_from(r)
            check("63 dummies registered after S", M.CLEAN, 1_270_000, 1_270_000, 0)

            for o in oracles:
                send(o, "commitRoot(bytes32,uint64)", leaf_root(cid, 2048), str(s1))
            r = send(OWNER, f"updateClusterBalance(uint64,address,uint64[],{CLUSTER_T},uint32,bytes32[])",
                     str(s1), OWNER, arr(ops), cl, "2048", "[]")
            cl = cluster_from(r)
            check("root@S consumed with dummies registered", M.LATENT, 1_270_000, 640_000, 630_000)

            r = send(OWNER, f"bulkRemoveValidator(bytes[],uint64[],{CLUSTER_T})", arr(dummies), arr(ops), cl)
            cl = cluster_from(r)
            check("dummies removed", M.CONFIRMED, 640_000, 10_000, 630_000)

            mine(max(min_blocks, 1))
            s2 = block()
            mine()
            for o in oracles:
                send(o, "commitRoot(bytes32,uint64)", leaf_root(cid, 2048), str(s2))
            send(OWNER, f"updateClusterBalance(uint64,address,uint64[],{CLUSTER_T},uint32,bytes32[])",
                 str(s2), OWNER, arr(ops), cl, "2048", "[]")
            check("clean next-round root", M.CLEAN, 640_000, 640_000, 0)

            rows = w.db.conn.execute("SELECT key, severity, delivery, receipt, attempts, last_error FROM outbox "
                                     "ORDER BY created_at").fetchall()
            print("alerts:", json.dumps([dict(r) for r in rows], indent=1))
            inc = w.db.conn.execute("SELECT id, kind, state, max_shortfall_vunits FROM m5_incidents").fetchall()
            print("incidents:", json.dumps([dict(r) for r in inc], indent=1))
            print("live params on fork:", json.dumps(w.params.values, default=str))
            b = block()
            layout = w.checks["m5"].verify_layout(Chain(w.rpc, b), w.db, b, deep=True)
            print("layout guard on fork:", layout or "ok")
            return 0 if not layout else 1
    finally:
        if not args.keep:
            anvil.terminate()


if __name__ == "__main__":
    sys.exit(main())
