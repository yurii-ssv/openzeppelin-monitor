"""CP2e LIVE evidence (read-only; eth_call / eth_getStorageAt / eth_getLogs and SSV API GETs only).

  reconcile/.venv/bin/python plans/monitoring-service/workers/cp2e/live_legacy.py --rpc <archive url> [--block N]
      [--logs-from 24920727] [--window 10000] [--out <json>]

V2e.7 — the legacy inventory from the SSV API v4 (`migrated=false`) agrees with the log-derived set at one block:
  * API side: every row's `migrated` flag is compared with storage at the block (`clusters[id]` legacy slot vs
    `ethClusters[id]`), the way inventory.py decides the version;
  * log side: every cluster that emitted a proxy cluster event since `--logs-from` (the v2 activation: a legacy
    cluster cannot be created after it, so a legacy cluster with any activity since appears here) and is legacy in
    storage at the block must be an API `migrated=false` row. ValidatorAdded and ClusterDeposited are skipped:
    after the activation both are ETH-only (registration and deposits require VERSION_ETH at the pin).
  Pass: no API row whose flag contradicts storage among existing clusters, and no log-derived legacy cluster
  missing from the API's migrated=false set.

It also runs the M6 and M7 evaluation code of the checks (the same functions, a real `Ctx` on a scratch
database) over the API's legacy rows at the block and prints what they would report, for the attempt log.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "reconcile"))

from checks.base import Ctx  # noqa: E402
from checks.m6_legacy_coverage import M6LegacyCoverage, legacy_balances  # noqa: E402
from checks.m7_ssv_solvency import M7SsvSolvency  # noqa: E402
from db import DB  # noqa: E402
from eb_events import CLUSTER_KINDS, TOPIC0, decode_log  # noqa: E402
from eb_storage import cluster_id, eth_cluster_slot, ssv_cluster_slot  # noqa: E402
from inventory import API_DEFAULT, build, fetch_api_clusters  # noqa: E402
from params import Params  # noqa: E402
from rpc import Chain, Rpc  # noqa: E402

PROXY = "0xdd9bc35ae942ef0cfa76930954a156b3ff30a4e1"
VIEWS = "0xafe830b6ee262ba11cce5f32fdcd760ffe6a66e4"
CSSV = "0xe018d31f120a637828f46afd6c64ec099d960546"
ACTIVATION = 24_920_727


def versions(chain: Chain, cids: list[str], block: int) -> dict[str, str | None]:
    slots = []
    for cid in cids:
        raw = bytes.fromhex(cid[2:])
        slots += [eth_cluster_slot(raw), ssv_cluster_slot(raw)]
    words = chain.storage(PROXY, slots, block)
    return {cid: "ETH" if words[2 * i] else "SSV" if words[2 * i + 1] else None for i, cid in enumerate(cids)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rpc", default=os.environ.get("MAINNET_RPC_URL"))
    ap.add_argument("--block", type=int)
    ap.add_argument("--logs-from", type=int, default=ACTIVATION)
    ap.add_argument("--window", type=int, default=10_000)
    ap.add_argument("--api", default=API_DEFAULT)
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    rpc = Rpc(args.rpc, timeout=60, retries=4, batch_size=10)
    block = args.block or rpc.block_number() - 12
    chain = Chain(rpc, block, args.window)
    report: dict = {"block": block, "rpc": args.rpc.split("?")[0], "api": args.api}

    # ---- V2e.7
    rows = fetch_api_clusters(args.api)
    by_cid = {}
    for r in rows:
        cid = "0x" + cluster_id(r["ownerAddress"].lower(), tuple(int(o) for o in r["operators"])).hex()
        by_cid[cid] = r
    api_false = {cid for cid, r in by_cid.items() if not r.get("migrated")}
    kinds = sorted(CLUSTER_KINDS - {"ValidatorAdded", "ClusterDeposited"})
    logs = rpc.get_logs(PROXY, args.logs_from, block, args.window, [TOPIC0[k] for k in kinds])
    log_cids = {"0x" + decode_log(lg).cluster_id.hex() for lg in logs}
    ver = versions(chain, sorted(set(by_cid) | log_cids), block)
    api_flag_wrong = sorted(cid for cid, r in by_cid.items()
                            if ver[cid] is not None and (ver[cid] == "SSV") != (not r.get("migrated")))
    api_absent = sorted(cid for cid in by_cid if ver[cid] is None)
    log_legacy = {cid for cid in log_cids if ver[cid] == "SSV"}
    missing_from_api = sorted(log_legacy - api_false)
    report["v2e7"] = {
        "api_rows": len(rows), "api_migrated_false": len(api_false),
        "api_migrated_false_legacy_in_storage": sum(1 for c in api_false if ver[c] == "SSV"),
        "storage_legacy_among_api_rows": sum(1 for c in by_cid if ver[c] == "SSV"),
        "api_flag_contradicts_storage": api_flag_wrong, "api_rows_absent_from_storage": api_absent,
        "logs_from": args.logs_from, "log_kinds": kinds, "logs": len(logs), "log_clusters": len(log_cids),
        "log_legacy_clusters": len(log_legacy), "log_legacy_missing_from_api_false": missing_from_api,
        "pass": not api_flag_wrong and not missing_from_api,
    }

    # ---- M6 / M7 evaluation code on the legacy rows, at the same block
    legacy_rows = [r for cid, r in by_cid.items() if ver[cid] == "SSV" or not r.get("migrated")]
    inv = build(chain, PROXY, block, legacy_rows, {})
    with tempfile.TemporaryDirectory() as tmp:
        settings = {"proxy": PROXY, "views": VIEWS, "start_block": block + 1, "inventory": {"api_url": args.api}}
        db = DB(str(Path(tmp) / "live.sqlite"))
        params = Params(VIEWS, PROXY)
        params.load(chain, block)

        def ctx_for(check):
            return Ctx(check_id=check.id, block=block, rpc=Chain(rpc, block, args.window), db=db, params=params,
                       config=check.config, settings=settings, clock=lambda: 0.0, log=logging.getLogger("live"),
                       reasons=frozenset({"force"}), _inventory=lambda b: inv,
                       _health=lambda *a, **k: True)

        m6 = M6LegacyCoverage({**M6LegacyCoverage.defaults}, settings)
        ctx6 = ctx_for(m6)
        m6.prepare(ctx6)
        active = [c for c in inv.clusters.values() if c.version == "SSV" and c.valid and c.cluster.active]
        values, failed = legacy_balances(ctx6, VIEWS, active, with_risk=True)
        m7 = M7SsvSolvency({**M7SsvSolvency.defaults, "cssv": CSSV, "unstake_history_from": ACTIVATION}, settings)
        with db.tx():
            for stmt in (m6.schema, m7.schema):
                db.conn.execute(stmt)
            findings6 = m6.run(ctx6, block)
        ctx7 = ctx_for(m7)
        with db.tx():
            ledger, unknown = m7.ledger(ctx7, block)
            findings7 = m7.run(ctx7, block)
        report["inventory"] = inv.stats()
        report["m6"] = {
            "params": {"liquidation_threshold_period_ssv": m6.period,
                       "minimum_liquidation_collateral_ssv": m6.min_collateral},
            "active_legacy": len(active), "priced": len(values), "unknown": failed,
            "invalid_legacy": [c.cluster_id for c in inv.clusters.values() if c.version == "SSV" and not c.valid],
            "inactive_legacy": sum(1 for c in inv.clusters.values() if c.version == "SSV" and c.valid
                                   and not c.cluster.active),
            "liquidatable": sum(v["liquidatable"] for v in values.values()),
            "zero_balance_burning": [cid for cid, v in values.items() if v["balance"] == 0
                                     and inv.clusters[cid].cluster.validator_count > 0],
            "zero_balance_inert": sum(1 for cid, v in values.items() if v["balance"] == 0
                                      and inv.clusters[cid].cluster.validator_count == 0),
            "validators": sum(inv.clusters[cid].cluster.validator_count for cid in values),
            "burn_ssv_per_year": sum(v["burn"] for v in values.values()) * 2_628_000 / 1e18,
            "findings": [(f.severity, f.key, f.title) for f in findings6],
            "rpc_calls": dict(ctx6.rpc.counts),
        }
        report["m7"] = {"ledger": {k: str(v) for k, v in ledger.items()}, "unknown": unknown,
                        "surplus_ssv": ledger["surplus"] / 1e18,
                        "findings": [(f.severity, f.key, f.title) for f in findings7],
                        "rpc_calls": dict(ctx7.rpc.counts)}
    text = json.dumps(report, indent=1, default=str)
    if args.out:
        Path(args.out).write_text(text)
    print(text)
    return 0 if report["v2e7"]["pass"] else 1


if __name__ == "__main__":
    logging.basicConfig(level="WARNING", stream=sys.stderr)
    sys.exit(main())
