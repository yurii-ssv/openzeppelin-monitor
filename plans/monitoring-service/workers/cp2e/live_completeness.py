"""CP2e repair 1 LIVE evidence (read-only; eth_getStorageAt and SSV API GETs only).

  reconcile/.venv/bin/python plans/monitoring-service/workers/cp2e/live_completeness.py --rpc <url> [--block N]
      [--out <json>]

The completeness guard of M6/M7 (`m6_legacy_coverage.inventory_gap`, QA cp2e-1 F1) on mainnet at one block, through
the worker's own InventoryCache (all SSV API v4 pages + storage validation, D8/D9) and a real `Ctx`:
  * positive: the validators of the active, storage-validated legacy clusters must equal the SSV-side
    `daoValidatorCount` (StorageProtocol word 0), and the listing must hold the API's `pagination.total` — the
    production guard must say None (complete) on a healthy mainnet, or the checks would be UNKNOWN for ever;
  * negative controls on the same block: an empty API page (with the true total), a self-consistent empty page
    (total 0; repair 2, QA cp2e-2 F1) and a listing one row short must each report a gap.
Exit 0 when the positive case is complete and both controls report a gap; 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "reconcile"))

from checks.base import Ctx  # noqa: E402
from checks.m6_legacy_coverage import PROTOCOL_BASE, api_total, inventory_gap  # noqa: E402
from db import DB  # noqa: E402
from inventory import API_DEFAULT, InventoryCache, _http_get_json  # noqa: E402
from rpc import Chain, Rpc  # noqa: E402

PROXY = "0xdd9bc35ae942ef0cfa76930954a156b3ff30a4e1"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rpc", required=True)
    ap.add_argument("--block", type=int)
    ap.add_argument("--api", default=API_DEFAULT)
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    rpc = Rpc(args.rpc, timeout=60, retries=4, batch_size=10)
    block = args.block or rpc.block_number() - 12
    pages: list[str] = []

    def http_get(url):
        pages.append(url)
        return _http_get_json(url)

    with tempfile.TemporaryDirectory() as tmp:
        db = DB(str(Path(tmp) / "live.sqlite"))
        cache = InventoryCache(db, lambda b: Chain(rpc, b), PROXY, clock=lambda: 0.0, api_base=args.api,
                               http_get=http_get)

        def ctx(cache_):
            return Ctx(check_id="m6", block=block, rpc=Chain(rpc, block), db=db, params=None, config={},
                       settings={"proxy": PROXY}, clock=lambda: 0.0, log=logging.getLogger("live"),
                       reasons=frozenset({"force"}), _inventory=cache_.get, _health=lambda *a, **k: True)

        inv = cache.get(block)
        c = ctx(cache)
        gap = inventory_gap(c, inv)
        total, why = api_total(c)
        (word0,) = c.rpc.storage(PROXY, [PROTOCOL_BASE])
        legacy = [x for x in inv.clusters.values() if x.version == "SSV"]
        report = {
            "block": block, "rpc": args.rpc.split("?")[0], "api": args.api, "api_get_urls": pages,
            "inventory": inv.stats(), "api_pagination_total": total, "api_total_error": why,
            "protocol_word0": hex(word0), "dao_validator_count": (word0 >> 32) & 0xFFFF_FFFF,
            "legacy_active_valid_validators": sum(x.cluster.validator_count for x in legacy
                                                  if x.valid and x.cluster.active),
            "legacy": len(legacy), "legacy_active": sum(1 for x in legacy if x.valid and x.cluster.active),
            "gap": gap,
        }
        rows = cache._api  # the listing the inventory used (read-only)
        controls = {}
        short = [r for r in rows if r is not next(
            (r2 for r2 in rows if not r2.get("migrated") and int(r2.get("validatorCount", 0)) > 0), None)]
        for name, served, t in (("empty", [], total), ("empty_total_0", [], 0),
                                ("short_by_one_legacy_row", short, total)):
            neg = InventoryCache(db, lambda b: Chain(rpc, b), PROXY, clock=lambda: 0.0, api_base=args.api,
                                 http_get=lambda url, s=served, t=t: {"clusters": s, "pagination": {
                                     "pages": 1, "total": t}})
            controls[name] = {"rows": len(served), "gap": inventory_gap(ctx(neg), neg.get(block))}
        report["controls"] = controls
    ok = gap is None and all(v["gap"] for v in controls.values())
    report["pass"] = ok
    text = json.dumps(report, indent=1, default=str)
    if args.out:
        Path(args.out).write_text(text)
    print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    logging.basicConfig(level="WARNING", stream=sys.stderr)
    sys.exit(main())
