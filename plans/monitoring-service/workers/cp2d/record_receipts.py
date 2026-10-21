"""CP2d V2d.7: record real mainnet multi-event cluster receipts for the offline M1 replay (LIVE, read-only).

  reconcile/.venv/bin/python plans/monitoring-service/workers/cp2d/record_receipts.py scan --rpc URL FROM TO
  reconcile/.venv/bin/python plans/monitoring-service/workers/cp2d/record_receipts.py record --rpc URL TX [TX ...]

`scan` lists same-transaction, same-cluster groups of >= 2 cluster events (the M1 receipt shape) in [FROM, TO].
`record` writes reconcile/tests/fixtures/receipts/<block>-<tx8>.json per transaction with everything the replay
needs, all read at the receipt's block from the named endpoint: the cluster logs of the block for the clusters the
transaction touches (every transaction of the block, so "final in block" is replayed faithfully),
`SSVNetworkViews.isLiquidated` for every distinct tuple of those logs (result or revert data), `isLiquidatable` and
`isLiquidatableSSV` of each cluster's final tuple, and both storage words of each cluster. The expected outcome is
computed here from the live answers only (not from the M1 module): whether the last tuple of each group validates,
which logs validate, and, for a synthetic poisoning (the latest non-validating earlier log moved to the end of its
group), the log index of the earliest validating tuple after the move. Only eth_getLogs, eth_getTransactionReceipt,
eth_getBlockByNumber, eth_call and eth_getStorageAt are issued; nothing is signed or sent.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "reconcile"))

from checks.m1_cluster_abi import (  # noqa: E402
    IS_LIQUIDATABLE,
    IS_LIQUIDATABLE_SSV,
    IS_LIQUIDATED,
    encode_cluster_call,
)
from eb_events import CLUSTER_KINDS, TOPIC0, decode_log  # noqa: E402
from eb_storage import Cluster, eth_cluster_slot, keccak, ssv_cluster_slot, u256  # noqa: E402

PROXY = "0xdd9bc35ae942ef0cfa76930954a156b3ff30a4e1"
VIEWS = "0xafe830b6ee262ba11cce5f32fdcd760ffe6a66e4"
OUT = os.path.join(ROOT, "reconcile", "tests", "fixtures", "receipts")
TOPICS = [TOPIC0[k] for k in sorted(CLUSTER_KINDS)]


class Rpc:
    def __init__(self, url):
        self.url, self.n = url, 0

    def call(self, method, params, allow_error=False):
        self.n += 1
        body = json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params}).encode()
        for attempt in range(6):
            try:
                req = urllib.request.Request(self.url, body, {"content-type": "application/json",
                                                              "user-agent": "ssv-cp2d-recorder/1.0"})
                with urllib.request.urlopen(req, timeout=60) as resp:
                    out = json.load(resp)
                break
            except urllib.error.HTTPError as exc:  # several providers answer JSON-RPC errors with HTTP 4xx
                try:
                    out = json.loads(exc.read())
                except ValueError:
                    out = None
                if isinstance(out, dict) and exc.code != 429:
                    break
                if attempt == 5:
                    raise
                print(f"  retry {method}: {exc}", file=sys.stderr)
                time.sleep(2 ** attempt)
            except Exception as exc:  # noqa: BLE001 - public endpoints rate-limit; retry with backoff
                if attempt == 5:
                    raise
                print(f"  retry {method}: {exc}", file=sys.stderr)
                time.sleep(2 ** attempt)
        if "error" in out:
            if allow_error:
                return {"error": out["error"]}
            raise RuntimeError(f"{method}: {out['error']}")
        return out["result"]


def get_logs(rpc, lo, hi, step):
    out, cur = [], lo
    while cur <= hi:
        end = min(hi, cur + step - 1)
        try:
            out += rpc.call("eth_getLogs", [{"address": PROXY, "fromBlock": hex(cur), "toBlock": hex(end),
                                             "topics": [TOPICS]}])
            cur = end + 1
        except RuntimeError as exc:
            if step == 1:
                raise
            step = max(1, step // 2)
            print(f"  getLogs {cur}-{end} rejected ({str(exc)[:80]}); step {step}", file=sys.stderr)
    return out


def cmd_scan(args):
    rpc = Rpc(args.rpc)
    logs = get_logs(rpc, args.start, args.end, args.step)
    groups = {}
    for lg in logs:
        ev = decode_log(lg)
        if ev is None or ev.cluster_id is None:
            continue
        groups.setdefault((ev.tx_hash, ev.cluster_id.hex()), []).append(ev)
    for (tx, cid), evs in sorted(groups.items(), key=lambda kv: kv[1][0].block):
        if len(evs) < 2:
            continue
        evs.sort(key=lambda e: e.log_index)
        shape = " > ".join(e.kind for e in evs[:4]) + (f" ... ({len(evs)})" if len(evs) > 4 else "")
        print(json.dumps({"block": evs[0].block, "tx": tx, "cluster": "0x" + cid[:12], "events": len(evs),
                          "distinct_tuples": len({e.cluster for e in evs}), "shape": shape}))


def answer(rpc, data, block):
    r = rpc.call("eth_call", [{"to": VIEWS, "data": "0x" + data.hex()}, hex(block)], allow_error=True)
    if isinstance(r, dict) and "error" in r:
        err = r["error"]
        rdata = err.get("data")
        if isinstance(rdata, dict):
            rdata = rdata.get("data")
        return {"revert": rdata, "message": err.get("message")}
    return {"result": r}


def tup(cl):
    return [cl.validator_count, cl.network_fee_index, cl.index, cl.active, str(cl.balance)]


# Cluster functions at the pin -> first head word of their inline Cluster argument (ISSVValidators, ISSVClusters).
CLUSTER_ARG = {
    "registerValidator(bytes,uint64[],bytes,(uint32,uint64,uint64,bool,uint256))": 3,
    "bulkRegisterValidator(bytes[],uint64[],bytes[],(uint32,uint64,uint64,bool,uint256))": 3,
    "removeValidator(bytes,uint64[],(uint32,uint64,uint64,bool,uint256))": 2,
    "bulkRemoveValidator(bytes[],uint64[],(uint32,uint64,uint64,bool,uint256))": 2,
    "migrateClusterToETH(uint64[],(uint32,uint64,uint64,bool,uint256))": 1,
    "reactivate(uint64[],(uint32,uint64,uint64,bool,uint256))": 1,
    "deposit(address,uint64[],(uint32,uint64,uint64,bool,uint256))": 2,
    "withdraw(uint64[],uint256,(uint32,uint64,uint64,bool,uint256))": 2,
}
CLUSTER_ARG_BY_SELECTOR = {"0x" + keccak(sig.encode())[:4].hex(): (sig, w) for sig, w in CLUSTER_ARG.items()}
# Event data head word where the Cluster tuple starts (eb_events.decode_log).
EVENT_TUPLE_WORD = {"ValidatorAdded": 3, "ValidatorRemoved": 2, "ClusterBalanceUpdated": 2, "ClusterLiquidated": 1,
                    "ClusterReactivated": 1, "ClusterDeposited": 2, "ClusterWithdrawn": 2, "ClusterMigratedToETH": 4}


def input_tuple(tx: dict):
    """The Cluster argument of a direct call to the proxy: storage's tuple before the transaction (the outer
    function's cached snapshot in the #91419 shape). None for another target or function."""
    if (tx.get("to") or "").lower() != PROXY:
        return None, None
    hit = CLUSTER_ARG_BY_SELECTOR.get(tx["input"][:10].lower())
    if hit is None:
        return None, None
    sig, w0 = hit
    body = bytes.fromhex(tx["input"][10:])
    w = [int.from_bytes(body[32 * i:32 * (i + 1)], "big") for i in range(w0, w0 + 5)]
    return sig, Cluster(w[0], w[1], w[2], bool(w[3]), w[4])


def with_tuple(lg: dict, kind: str, cl: Cluster) -> dict:
    data = bytearray(bytes.fromhex(lg["data"][2:]))
    w0 = EVENT_TUPLE_WORD[kind]
    for i, v in enumerate((cl.validator_count, cl.network_fee_index, cl.index, int(cl.active), cl.balance)):
        data[32 * (w0 + i):32 * (w0 + i + 1)] = u256(v)
    return {**lg, "data": "0x" + data.hex()}


def cmd_record(args):
    rpc, logs_rpc = Rpc(args.rpc), Rpc(args.logs_rpc or args.rpc)
    os.makedirs(OUT, exist_ok=True)
    for tx in args.txs:
        tx = tx.lower()
        rcpt = rpc.call("eth_getTransactionReceipt", [tx])
        txn = rpc.call("eth_getTransactionByHash", [tx])
        block = int(rcpt["blockNumber"], 16)
        tx_events = [e for e in (decode_log(lg) for lg in rcpt["logs"] if lg["address"].lower() == PROXY) if e and
                     e.cluster_id is not None]
        cids = {e.cluster_id for e in tx_events}
        block_logs = [lg for lg in get_logs(logs_rpc, block, block, 1)
                      if (ev := decode_log(lg)) is not None and ev.cluster_id in cids]
        block_logs.sort(key=lambda lg: int(lg["logIndex"], 16))
        events = [decode_log(lg) for lg in block_logs]
        if {e.log_index for e in tx_events} - {e.log_index for e in events}:
            raise SystemExit(f"{tx}: eth_getLogs of block {block} misses logs of the receipt")
        fn, stale_input = input_tuple(txn)
        calls, words, validates = [], {}, {}
        distinct = {(e.owner, e.operator_ids, e.cluster): None for e in events}
        if stale_input is not None:
            for e in tx_events:
                distinct.setdefault((e.owner, e.operator_ids, stale_input), None)
        for (owner, ops, cl) in distinct:
            data = encode_cluster_call(IS_LIQUIDATED, owner, ops, cl)
            a = answer(rpc, data, block)
            calls.append({"to": VIEWS, "data": "0x" + data.hex(), "block": block, **a})
            validates[(owner, ops, cl)] = "result" in a
        finals = {}
        for e in events:
            finals[e.cluster_id] = e
        groups = []
        for cid in sorted(cids):
            final = finals[cid]
            for sel in (IS_LIQUIDATABLE, IS_LIQUIDATABLE_SSV):
                data = encode_cluster_call(sel, final.owner, final.operator_ids, final.cluster)
                calls.append({"to": VIEWS, "data": "0x" + data.hex(), "block": block, **answer(rpc, data, block)})
            for slot in (eth_cluster_slot(cid), ssv_cluster_slot(cid)):
                words[hex(slot)] = rpc.call("eth_getStorageAt", [PROXY, hex(slot), hex(block)])
            g = [e for e in events if e.cluster_id == cid and e.tx_hash == tx]
            key = (final.owner, final.operator_ids)
            group = {
                "cluster_id": "0x" + cid.hex(), "owner": final.owner, "operator_ids": list(final.operator_ids),
                "log_indexes": [e.log_index for e in g], "kinds": [e.kind for e in g],
                "distinct_tuples": len({e.cluster for e in g}), "final_in_block": final.tx_hash == tx,
                "last_tuple": tup(g[-1].cluster), "last_validates": validates[key + (g[-1].cluster,)],
                "validating_log_indexes": [e.log_index for e in g if validates[key + (e.cluster,)]],
            }
            # Synthetic poisoning from real data: the outer function emits its cached snapshot (the transaction's own
            # Cluster argument, storage's tuple before the call) after the real logs of the group.
            if (len(g) >= 2 and group["final_in_block"] and stale_input is not None
                    and not validates[key + (stale_input,)] and stale_input not in {e.cluster for e in g}):
                last_pos = next(i for i, e in enumerate(events) if e.log_index == g[-1].log_index)
                synthetic = with_tuple(block_logs[last_pos], g[-1].kind, stale_input)
                order = list(block_logs)
                order.insert(last_pos + 1, synthetic)
                renumbered = [{**lg, "logIndex": hex(i)} for i, lg in enumerate(order)]
                first_valid = next(i for i, lg in enumerate(order) if lg is not synthetic
                                   and (ev := decode_log(lg)).cluster_id == cid and ev.tx_hash == tx
                                   and validates[key + (ev.cluster,)])
                group["synthetic_poison"] = {
                    "function": fn, "stale_tuple": tup(stale_input), "logs": renumbered,
                    "expected": {"warn": True, "repair_log_index": first_valid, "repair_tuple": tup(g[-1].cluster),
                                 "stale_log_index": last_pos + 1},
                }
            groups.append(group)
        doc = {
            "_comment": "Recorded LIVE once by plans/monitoring-service/workers/cp2d/record_receipts.py; replayed "
                        "offline by reconcile/tests/test_m1_receipts.py. Expected values come from the live answers.",
            "tx_hash": tx, "block": block, "block_hash": rcpt["blockHash"], "source_endpoint": args.rpc,
            "logs_endpoint": args.logs_rpc or args.rpc, "function": fn,
            "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "proxy": PROXY, "views": VIEWS,
            "logs": [{k: lg[k] for k in ("address", "topics", "data", "blockNumber", "blockHash", "logIndex",
                                         "transactionIndex", "transactionHash")} for lg in block_logs],
            "eth_calls": calls, "storage": words, "groups": groups,
        }
        path = os.path.join(OUT, f"{block}-{tx[2:10]}.json")
        with open(path, "w") as fh:
            json.dump(doc, fh, indent=1)
            fh.write("\n")
        print(json.dumps({"file": os.path.relpath(path, ROOT), "block": block, "function": fn, "groups": [
            {k: g[k] for k in ("kinds", "distinct_tuples", "last_validates", "final_in_block")}
            | {"synthetic": "synthetic_poison" in g} for g in groups]}))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("--rpc", required=True)
    s.add_argument("--step", type=int, default=2000)
    s.add_argument("start", type=int)
    s.add_argument("end", type=int)
    r = sub.add_parser("record")
    r.add_argument("--rpc", required=True, help="archive endpoint: receipts, transactions, eth_call, storage")
    r.add_argument("--logs-rpc", help="endpoint for the one-block eth_getLogs (default --rpc)")
    r.add_argument("txs", nargs="+")
    args = ap.parse_args()
    (cmd_scan if args.cmd == "scan" else cmd_record)(args)


if __name__ == "__main__":
    main()
