#!/usr/bin/env python3
"""Fork end-to-end scenarios (plan §7 V3.6–V3.9): the worker against real contracts on an anvil mainnet fork.

Nothing is sent to mainnet: anvil impersonates the live oracles, wallets and fresh accounts locally, and every
alert is recorded (record mode) unless --telegram sends it to the TEST_* channel.

  FORK_RPC_URL=<mainnet RPC> .venv/bin/python scripts/fork_e2e.py --scenario m5|m3|m9|m12 [--telegram]

  m5   #94775 PoC trace (63 empty-shares dummy keys, a 2,048 ETH root, removal): latent -> confirmed -> clean
  m3   an ETH cluster (registered on the fork) is driven past its threshold and nobody liquidates it: info at the
       onset, warn at N (50 blocks), page at N2 (7,200), computed zero recorded, resolved after an impersonated
       liquidation
  m9   impersonated oracles: one slot dissents in three consecutive committed rounds (warn, then page for the
       streak); then a round gets one vote and no commit (page once its 30 min have passed on the chain clock)
  m12  an impersonated transfer drains the oracle wallet below 0.15 ETH (page); a top-up resolves it

The fork is taken at the RPC's latest block (the endpoint is not guaranteed to be archive: run promptly). The
worker starts at fork block + 1 with no silent backfill (D17 is for mainnet history) and only the checks of the
scenario. --telegram delivers to TEST_TELEGRAM_TOKEN / TEST_TELEGRAM_CHAT_ID (labelled as a fork test).
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

import checks as registry  # noqa: E402
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
LIQUIDATOR = "0x00000000000000000000000000000000009477C3"
SINK = "0x00000000000000000000000000000000009477D4"
FEE = 1_778_800_000
CLUSTER_T = "(uint32,uint64,uint64,bool,uint256)"
ETH = 10**18


def rpc(method, params):
    req = urllib.request.Request(URL, json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
                                 {"content-type": "application/json"})
    out = json.load(urllib.request.urlopen(req, timeout=300))
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
    while n > 0:  # anvil mines ~25 empty fork blocks/s: chunks keep each request short
        rpc("anvil_mine", [hex(min(n, 500))])
        n -= 500


def arr(xs) -> str:
    return "[" + ",".join(str(x) for x in xs) + "]"


def key(seed: int) -> str:
    return "0x" + format(seed, "x").rjust(96, "0")


def leaf_root(cid: bytes, eb: int) -> str:
    return "0x" + keccak(keccak(cid + u256(eb))).hex()


def fund(*addresses, wei=10**22):
    for a in addresses:
        rpc("anvil_setBalance", [a, hex(wei)])
        rpc("anvil_impersonateAccount", [a])


def expect(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'} {label}" + (f": {detail}" if detail else ""), flush=True)
    if not ok:
        raise SystemExit(1)


class Run:
    """The worker on the fork: config.example.json, start at fork block + 1, the scenario's checks only."""

    def __init__(self, tmp: str, fork_block: int, only: list[str], telegram: bool, checks: dict | None = None,
                 **over):
        cfg = Config({**DEFAULTS, **json.loads((HERE / "config.example.json").read_text())})
        cfg.raw.update(start_block=fork_block + 1, activation_block=fork_block + 1, confirmations=0,
                       db_path=f"{tmp}/eb.sqlite", proxy=PROXY.lower(), inbox={}, inventory={"api_url": None},
                       metrics={}, backfill={"enabled": False}, **over)
        for cid, extra in (checks or {}).items():
            cfg.raw["checks"].setdefault(cid, {}).update(extra)
        cfg.raw["alerts"] = {**cfg.alerts, "label": "MAINNET FORK TEST (not a real incident)", "digest_interval_s": 0}
        notifier = RecordOnly(f"{tmp}/alerts.jsonl")
        if telegram:
            env = dict(line.split("=", 1) for line in (HERE.parent / ".env").read_text().splitlines()
                       if "=" in line and not line.startswith("#"))
            notifier = Telegram(env["TEST_TELEGRAM_TOKEN"], env["TEST_TELEGRAM_CHAT_ID"])
        self.w = Worker(cfg, Rpc(URL, batch_size=50), DB(cfg.db_path), notifier,
                        checks=registry.build(cfg.raw, only=only))
        self.w.bootstrap()

    def step(self):
        w = self.w
        w.check_reorg()
        w.scan()
        w.poll_inbox()
        w.run_checks(force=True)
        w.housekeeping()
        w.deliver()

    def sent(self, check: str) -> list[dict]:
        """Delivered outbox rows of `check`'s incidents, in order: kind, severity, incident key, first line."""
        rows = self.w.db.conn.execute(
            "SELECT o.kind, o.severity, i.key, o.text, o.sent_at FROM outbox o JOIN incidents i ON i.id = o.incident_id "
            "WHERE o.check_id=? ORDER BY o.created_at, o.rowid", (check,)).fetchall()
        return [{"kind": r[0], "severity": r[1], "key": r[2], "head": r[3].split("\n")[0], "sent": r[4] is not None}
                for r in rows]

    def incident(self, check: str, key: str):
        r = self.w.db.conn.execute("SELECT * FROM incidents WHERE check_id=? AND key=? ORDER BY opened_at DESC",
                                   (check, key)).fetchone()
        return dict(r) if r else None

    def health(self) -> list[str]:
        return [r[0].split("\n")[1][:200] for r in self.w.db.conn.execute(
            "SELECT text FROM outbox WHERE kind='health' ORDER BY created_at")]


def register_operators(n=4) -> list[int]:
    ops = []
    for i in range(n):
        r = send(OP_OWNER, "registerOperator(bytes,uint256,bool)", key(94_775 + 1000 + i + int(time.time()) % 100_000),
                 str(FEE), "false")
        added = [lg for lg in r["logs"] if lg["topics"][0] == "0x" + keccak(b"OperatorAdded(uint64,address,bytes,uint256)").hex()]
        ops.append(int(added[0]["topics"][1], 16))
    return sorted(ops)


# ---------------------------------------------------------------- m5


def scenario_m5(fork_block: int, tmp: str, telegram: bool) -> int:
    fund(OP_OWNER, OWNER)
    oracle_ids = cast("call", VIEWS, "getActiveOracleIds()(uint32[4])").strip("[]").split(", ")
    oracles = [cast("call", VIEWS, "getOracle(uint32)(address)", i) for i in oracle_ids[:3]]
    fund(*oracles, wei=10**19)
    ops = register_operators()
    cid = cluster_id(OWNER, ops)
    print("operators", ops, "cluster", "0x" + cid.hex())
    run = Run(tmp, fork_block, ["m5"], telegram, checks={"m5": {"sweep_interval_s": 0, "full_sweep_interval_s": 0}})
    w = run.w

    def check(label, status, ref=None, obs=None, disc=None):
        run.step()
        evals = {e.cluster_id: e for e in w.checks["m5"].last_evals}
        e = evals[cid]
        got = (e.status, e.reference_vunits, e.observed_vunits, e.discrepancy_vunits)
        want = (status, ref, obs, disc)
        ok = all(b is None or a == b for a, b in zip(got, want))
        expect(label, ok, f"status={e.status} reference={e.reference_vunits} observed={e.observed_vunits} "
               f"discrepancy={e.discrepancy_vunits} matched={len(e.matched_keys)}" + (f" reasons={e.reasons}" if e.reasons else ""))
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

    sent = run.sent("m5")
    print("alerts:", json.dumps(sent, indent=1))
    expect("M5 alerts: latent (silent) escalated to a confirmed page, then its resolution",
           [(s["kind"], s["severity"]) for s in sent] == [("escalate", "page"), ("resolved", "info")], str(sent))
    b = block()
    layout = w.checks["m5"].verify_layout(Chain(w.rpc, b), w.db, b, deep=True)
    expect("layout guard on fork", not layout, str(layout))
    return 0


# ---------------------------------------------------------------- m3


def view(sig: str, *args) -> str:
    return cast("call", VIEWS, sig, *args).split(" ")[0]


def scenario_m3(fork_block: int, tmp: str, telegram: bool) -> int:
    fund(OP_OWNER, OWNER, LIQUIDATOR)
    ops = register_operators()
    cid = "0x" + cluster_id(OWNER, ops).hex()
    print("operators", ops, "cluster", cid)
    r = send(OWNER, f"registerValidator(bytes,uint64[],bytes,{CLUSTER_T})", key(94_775_300 + int(time.time()) % 10_000),
             arr(ops), "0x", "(0,0,0,true,0)", value=10 * ETH)
    cl = cluster_from(r)
    args = (OWNER, arr(ops))
    burn = int(view(f"getBurnRate(address,uint64[],{CLUSTER_T})(uint256)", *args, cl))
    period = int(view("getLiquidationThresholdPeriod()(uint64)"))
    min_coll = int(view("getMinimumLiquidationCollateral()(uint256)"))
    balance = int(view(f"getBalance(address,uint64[],{CLUSTER_T})(uint256)", *args, cl))
    keep = max(burn * period, min_coll) + burn * 4  # liquidatable about four blocks from now
    r = send(OWNER, f"withdraw(uint64[],uint256,{CLUSTER_T})", arr(ops), str(balance - keep - burn), cl)
    cl = cluster_from(r)
    print(f"burn {burn} wei/block, threshold period {period}, min collateral {min_coll}, balance kept {keep}")

    run = Run(tmp, fork_block, ["m3"], telegram)
    run.step()
    expect("not liquidatable yet: no M3 alert", run.sent("m3") == [], str(run.sent("m3")))
    for _ in range(20):
        mine()
        if view(f"isLiquidatable(address,uint64[],{CLUSTER_T})(bool)", *args, cl) == "true":
            break
    onset = block()
    run.step()
    s = run.sent("m3")
    expect("onset: info", [(x["kind"], x["severity"], x["key"]) for x in s] == [("open", "info", cid)], str(s))
    mine(50)
    run.step()
    s = run.sent("m3")
    expect("N = 50 blocks after the onset: warn", [(x["kind"], x["severity"]) for x in s][1:] == [("escalate", "warn")],
           str(s))
    mine(7_200 - 50)
    run.step()
    s = run.sent("m3")
    expect("N2 = 7,200 blocks: page", [(x["kind"], x["severity"]) for x in s][2:] == [("escalate", "page")], str(s))
    zero_at = None
    for _ in range(12):
        mine(5_000)
        if int(view(f"getBalance(address,uint64[],{CLUSTER_T})(uint256)", *args, cl)) == 0:
            zero_at = block()
            break
    expect("the view-computed balance reached zero on the fork", zero_at is not None, f"block {zero_at}")
    run.step()
    inc = run.incident("m3", cid)
    s = run.sent("m3")
    expect("computed zero: page incident records it (title, zero_block)",
           inc["severity"] == "page" and "computed zero" in inc["title"]
           and json.loads(inc["evidence"]).get("zero_block") is not None, f"{inc['title']} / sent {len(s)} rows")
    print("NOTE computed zero after the N2 page: same incident, same severity ->",
          "a new message" if len(s) > 3 else "recorded without a new message (the 6 h re-alert carries the new title)")
    rcpt = send(LIQUIDATOR, f"liquidate(address,uint64[],{CLUSTER_T})", OWNER, arr(ops), cl)
    print("liquidated in block", int(rcpt["blockNumber"], 16))
    run.step()
    s = run.sent("m3")
    expect("liquidated: resolved (info resolution on the page route)", s[-1]["kind"] == "resolved"
           and run.incident("m3", cid)["state"] == "resolved", str(s[-1]))
    print("alerts:", json.dumps(s, indent=1))
    print("onset block", onset, "health:", run.health())
    return 0


# ---------------------------------------------------------------- m9


def scenario_m9(fork_block: int, tmp: str, telegram: bool) -> int:
    ids = [int(i) for i in cast("call", VIEWS, "getActiveOracleIds()(uint32[4])").strip("[]").split(", ") if int(i)]
    oracles = {i: cast("call", VIEWS, "getOracle(uint32)(address)", str(i)) for i in ids}
    print("active oracle slots", oracles)
    if len(ids) < 4:
        raise SystemExit(f"NOT RUN: the dissent streak needs 4 active slots (3 to commit, 1 to dissent); found {ids}")
    fund(*oracles.values(), wei=10**19)
    dissenter, honest = ids[-1], ids[:-1]
    run = Run(tmp, fork_block, ["m9"], telegram, checks={"m9": {"require_inbox": False}})
    _, min_blocks = decode_latest_committed(int(rpc("eth_getStorageAt", [PROXY, hex(EB_LATEST_COMMITTED), "latest"]), 16))
    print("min blocks between updates", min_blocks)

    def round_(n: int, voters: list[int], dissent: bool) -> int:
        mine(max(min_blocks, 1))
        ref = block()
        mine()
        if dissent:
            send(oracles[dissenter], "commitRoot(bytes32,uint64)", "0x" + keccak(f"dissent {n}".encode()).hex(), str(ref))
        root = "0x" + keccak(f"round {n} {ref}".encode()).hex()
        for i in voters:
            send(oracles[i], "commitRoot(bytes32,uint64)", root, str(ref))
        mine()
        return ref

    dkey = f"dissent:{dissenter}"
    for n in (1, 2, 3):
        ref = round_(n, honest, dissent=True)
        run.step()
        s = [x for x in run.sent("m9") if x["key"] == dkey]
        print(f"round {n} (blockNum {ref}): {[(x['kind'], x['severity']) for x in s]}")
        if n == 1:
            expect("first dissenting round: warn", [(x["kind"], x["severity"]) for x in s] == [("open", "warn")], str(s))
        if n == 3:
            expect("third consecutive dissenting round: page (streak)", s and s[-1]["severity"] == "page"
                   and s[-1]["kind"] == "escalate", str(s))
    ref = round_(4, honest[:1], dissent=False)  # one vote, no quorum, no commit
    run.step()
    expect("round without commit, inside its 30 min: no failed-round page",
           not any(x["key"] == "failed-round" for x in run.sent("m9")), str(run.sent("m9")))
    rpc("evm_increaseTime", [1_900])
    mine()
    run.step()
    s = [x for x in run.sent("m9") if x["key"] == "failed-round"]
    expect("30 min later on the chain clock: failed-round page", [(x["kind"], x["severity"]) for x in s][:1]
           == [("open", "page")], str(s))
    print("alerts:", json.dumps(run.sent("m9"), indent=1))
    print("health:", run.health())
    return 0


# ---------------------------------------------------------------- m12


def scenario_m12(fork_block: int, tmp: str, telegram: bool) -> int:
    cfg = json.loads((HERE / "config.example.json").read_text())["checks"]["m12"]
    wallet = cfg["oracle_wallet"]
    # Blockscout does not know fork transactions: the spend is UNKNOWN on the fork whatever it answers (the balance
    # keys are evaluated anyway), so the scenario does not query it.
    run = Run(tmp, fork_block, ["m12"], telegram, checks={"m12": {"blockscout_url": "https://blockscout.invalid"}})
    bal = int(rpc("eth_getBalance", [wallet, "latest"]), 16)
    print(f"oracle wallet {wallet} balance {bal / ETH:.6f} ETH")
    key = f"{wallet.lower()}:balance"
    mine()
    run.step()
    before = [x for x in run.sent("m12") if x["key"].lower() == key]
    if bal >= 15 * ETH // 100:
        expect("balance above 0.15 ETH: no balance page", before == [], str(before))
    rpc("anvil_impersonateAccount", [wallet])
    drain = bal - 10 * ETH // 100 - 10**16  # leave ~0.1 ETH after gas
    if drain > 0:
        rcpt = json.loads(cast("send", "--unlocked", "--from", wallet, "--json", "--value", str(drain), SINK))
        expect("impersonated transfer mined on the fork", int(rcpt["status"], 16) == 1, rcpt["transactionHash"])
    mine()
    left = int(rpc("eth_getBalance", [wallet, "latest"]), 16)
    run.step()
    s = [x for x in run.sent("m12") if x["key"].lower() == key]
    expect(f"balance {left / ETH:.4f} ETH < 0.15: page", [(x["kind"], x["severity"]) for x in s][-1:]
           == [("open", "page")], str(s))
    fund(SINK)
    cast("send", "--unlocked", "--from", SINK, "--json", "--value", str(ETH), wallet)  # a top-up
    mine()
    run.step()
    s = [x for x in run.sent("m12") if x["key"].lower() == key]
    expect("topped up: resolved", s[-1]["kind"] == "resolved", str(s))
    print("alerts:", json.dumps(run.sent("m12"), indent=1))
    print("health:", run.health())
    return 0


SCENARIOS = {"m5": scenario_m5, "m3": scenario_m3, "m9": scenario_m9, "m12": scenario_m12}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", choices=sorted(SCENARIOS), default="m5")
    ap.add_argument("--telegram", action="store_true")
    ap.add_argument("--keep", action="store_true", help="leave anvil running")
    ap.add_argument("--port", type=int, default=PORT, help="anvil port (run scenarios in parallel on distinct ports)")
    args = ap.parse_args()
    global URL
    URL = f"http://127.0.0.1:{args.port}"
    fork_url = os.environ.get("FORK_RPC_URL")
    if not fork_url or not shutil.which("anvil"):
        raise SystemExit("set FORK_RPC_URL and install foundry")

    anvil = subprocess.Popen(["anvil", "--fork-url", fork_url, "--port", str(args.port), "--silent",
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
        print(f"scenario {args.scenario}: fork block {fork_block}", flush=True)
        with tempfile.TemporaryDirectory() as tmp:
            return SCENARIOS[args.scenario](fork_block, tmp, args.telegram)
    finally:
        if not args.keep:
            anvil.terminate()


if __name__ == "__main__":
    sys.exit(main())
