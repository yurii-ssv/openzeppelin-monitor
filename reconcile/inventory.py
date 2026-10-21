"""Cluster inventory: SSV API v4 pages reconciled with scanned logs, every tuple validated against storage.

Cluster tuples from the API (and the last log of a transaction, spec M1) are untrusted (D9): a tuple
is used only if `hashClusterData(tuple)` equals the stored cluster word at the evaluated block
(`ethClusters[id]` for ETH clusters, `clusters[id]` for legacy SSV ones). The version comes from
which of the two slots is set; `api_migrated` is the API's own flag, kept for cross-checks (D8).

  reconcile/.venv/bin/python reconcile/inventory.py --probe --rpc "$MAINNET_RPC_URL"   # LIVE, read-only
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import sys
import urllib.request
from dataclasses import dataclass, field

from eb_storage import Cluster, cluster_id, eth_cluster_slot, ssv_cluster_slot

log = logging.getLogger("eb-reconcile")

API_DEFAULT = "https://api.ssv.network/api/v4/mainnet"


@dataclass(frozen=True)
class ClusterInfo:
    cluster_id: str  # 0x-prefixed lowercase hex
    owner: str
    operator_ids: tuple[int, ...]
    version: str | None  # "ETH" | "SSV" from storage; None when neither slot is set (no such cluster)
    cluster: Cluster | None  # the storage-validated tuple; None when no candidate validates
    tuple_source: str | None  # "logs" | "api"
    sources: frozenset[str]  # where the cluster was listed: {"api", "logs"}
    api_migrated: bool | None
    api: dict | None = None

    @property
    def valid(self) -> bool:
        return self.cluster is not None

    @property
    def legacy(self) -> bool:
        return self.version == "SSV"


@dataclass
class Inventory:
    block: int
    clusters: dict[str, ClusterInfo]
    operators: dict[int, dict] = field(default_factory=dict)  # id -> {"clusters": n, "validators": n}

    @property
    def invalid(self) -> list[ClusterInfo]:
        """Listed clusters that exist in storage but for which no candidate tuple validates (M1 input)."""
        return [c for c in self.clusters.values() if c.version and not c.valid]

    def stats(self) -> dict:
        cs = list(self.clusters.values())
        return {
            "block": self.block, "clusters": len(cs), "eth": sum(c.version == "ETH" for c in cs),
            "ssv_legacy": sum(c.version == "SSV" for c in cs), "absent": sum(c.version is None for c in cs),
            "valid": sum(c.valid for c in cs), "invalid": len(self.invalid),
            "from_api": sum("api" in c.sources for c in cs), "from_logs": sum("logs" in c.sources for c in cs),
            "tuple_from_logs": sum(c.tuple_source == "logs" for c in cs),
            "tuple_from_api": sum(c.tuple_source == "api" for c in cs),
            "operators": len(self.operators),
        }


def _http_get_json(url: str, timeout: float = 30) -> dict:
    req = urllib.request.Request(url, headers={"accept": "application/json", "user-agent": "ssv-watch/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def fetch_api_clusters(base: str = API_DEFAULT, per_page: int = 1000, http_get=_http_get_json,
                       max_pages: int = 100) -> list[dict]:
    """All clusters of the SSV API v4 (`/clusters?perPage=&page=`), following `pagination.pages`."""
    out: list[dict] = []
    page, pages = 1, 1
    while page <= pages:
        body = http_get(f"{base.rstrip('/')}/clusters?perPage={per_page}&page={page}")
        if not isinstance(body, dict) or not isinstance(body.get("clusters"), list):
            raise ValueError(f"SSV API page {page}: unexpected shape {sorted(body) if isinstance(body, dict) else type(body)}")
        out.extend(body["clusters"])
        pages = int(body.get("pagination", {}).get("pages", page))
        if pages > max_pages:
            raise ValueError(f"SSV API reports {pages} pages; refusing more than {max_pages}")
        page += 1
    return out


def api_tuple(c: dict) -> Cluster:
    """API v4 fields -> Cluster; ETH clusters carry `ethBalance`, legacy SSV ones `balance`."""
    balance = c["ethBalance"] if c.get("migrated") else c["balance"]
    return Cluster(int(c["validatorCount"]), int(c["networkFeeIndex"]), int(c["index"]), bool(c["active"]),
                   int(balance))


def _hash_word(cl: Cluster) -> int:
    return int.from_bytes(cl.hash_data(), "big")


def build(chain, proxy: str, block: int, api_clusters: list[dict], log_events: dict) -> Inventory:
    """Reconcile API rows with the latest log tuple per cluster and validate against storage at `block`.

    `log_events`: cluster id hex (no 0x) -> the last stored event carrying a Cluster tuple (db.latest_cluster_events).
    """
    entries: dict[str, dict] = {}
    for c in api_clusters:
        owner, ops = c["ownerAddress"].lower(), tuple(int(o) for o in c["operators"])
        cid = cluster_id(owner, ops).hex()
        if c.get("clusterId") and c["clusterId"].lower().removeprefix("0x") != cid:
            log.warning("SSV API clusterId %s does not match keccak(owner, operators) %s", c["clusterId"], cid)
        e = entries.setdefault(cid, {"owner": owner, "ops": ops, "sources": set(), "candidates": []})
        e["sources"].add("api")
        e["api"] = c
        try:
            e["candidates"].append(("api", api_tuple(c)))
        except (KeyError, ValueError, TypeError) as exc:
            log.warning("SSV API cluster %s: unusable tuple (%s)", cid[:10], exc)
    for cid, ev in log_events.items():
        e = entries.setdefault(cid, {"owner": ev.owner, "ops": tuple(ev.operator_ids), "sources": set(), "candidates": []})
        e["sources"].add("logs")
        if ev.cluster is not None and ev.block <= block:
            e["candidates"].insert(0, ("logs", ev.cluster))

    cids = sorted(entries)
    slots = []
    for cid in cids:
        cb = bytes.fromhex(cid)
        slots += [eth_cluster_slot(cb), ssv_cluster_slot(cb)]
    words = chain.storage(proxy, slots, block) if slots else []

    clusters: dict[str, ClusterInfo] = {}
    operators: dict[int, dict] = {}
    for i, cid in enumerate(cids):
        e = entries[cid]
        eth_word, ssv_word = words[2 * i], words[2 * i + 1]
        version = "ETH" if eth_word else "SSV" if ssv_word else None
        stored = eth_word or ssv_word
        chosen = next(((src, cl) for src, cl in e["candidates"] if stored and _hash_word(cl) == stored), (None, None))
        api = e.get("api")
        info = ClusterInfo(
            cluster_id="0x" + cid, owner=e["owner"], operator_ids=e["ops"], version=version, cluster=chosen[1],
            tuple_source=chosen[0], sources=frozenset(e["sources"]),
            api_migrated=bool(api["migrated"]) if api and "migrated" in api else None, api=api,
        )
        clusters[info.cluster_id] = info
        if info.valid:
            for op in info.operator_ids:
                o = operators.setdefault(op, {"clusters": 0, "validators": 0})
                o["clusters"] += 1
                o["validators"] += info.cluster.validator_count
    return Inventory(block, clusters, operators)


class InventoryCache:
    """Per-block cache; API pages are re-fetched at most every `api_refresh_s` seconds."""

    def __init__(self, db, rpc_factory, proxy: str, clock, api_base: str = API_DEFAULT, api_refresh_s: float = 600,
                 http_get=_http_get_json, use_api: bool = True):
        self.db, self.rpc_factory, self.proxy, self.clock = db, rpc_factory, proxy, clock
        self.api_base, self.api_refresh_s, self.http_get, self.use_api = api_base, api_refresh_s, http_get, use_api
        self._api: list[dict] = []
        self._api_at: float | None = None
        self._by_block: dict[int, Inventory] = {}

    def api_clusters(self) -> list[dict]:
        if self.use_api and (self._api_at is None or self.clock() - self._api_at >= self.api_refresh_s):
            self._api = fetch_api_clusters(self.api_base, http_get=self.http_get)
            self._api_at = self.clock()
        return self._api

    def get(self, block: int) -> Inventory:
        inv = self._by_block.get(block)
        if inv is None:
            inv = build(self.rpc_factory(block), self.proxy, block, self.api_clusters(), self.db.latest_cluster_events())
            self._by_block = {block: inv}  # keep only the latest block
        return inv

    def invalidate(self) -> None:
        self._by_block = {}


def main(argv=None) -> int:
    from rpc import Chain, Rpc

    ap = argparse.ArgumentParser(description="LIVE inventory probe (read-only): API v4 pages + storage-hash validation.")
    ap.add_argument("--probe", action="store_true", required=True)
    ap.add_argument("--rpc", required=True)
    ap.add_argument("--api", default=API_DEFAULT)
    ap.add_argument("--proxy", default="0xDD9BC35aE942eF0cFa76930954a156B3fF30a4E1")
    ap.add_argument("--sample", type=int, default=25, help="clusters whose tuple is validated against storage")
    ap.add_argument("--seed", type=int, default=94775)
    args = ap.parse_args(argv)

    rows = fetch_api_clusters(args.api)
    fields = sorted(set().union(*(r.keys() for r in rows))) if rows else []
    rpc = Rpc(args.rpc, timeout=30, retries=4, batch_size=10)
    block = rpc.block_number() - 12
    sample = random.Random(args.seed).sample(rows, min(args.sample, len(rows)))
    inv = build(Chain(rpc, block), args.proxy.lower(), block, sample, {})
    validated = [c for c in inv.clusters.values() if c.valid]
    print(json.dumps({
        "block": block, "api_rows": len(rows), "api_fields": fields,
        "api_migrated_false": sum(1 for r in rows if not r.get("migrated")),
        "sampled": len(sample), "sample_stats": inv.stats(),
        "validated": len(validated),
        "not_validated": [{"cluster_id": c.cluster_id, "version": c.version,
                           "api_block": (c.api or {}).get("blockNumber")} for c in inv.clusters.values() if not c.valid],
        "examples": [{"cluster_id": c.cluster_id, "version": c.version, "api_migrated": c.api_migrated,
                      "tuple": list(map(str, c.cluster.as_tuple()))} for c in validated[:3]],
    }, indent=1))
    return 0 if len(validated) >= 5 else 1


if __name__ == "__main__":
    logging.basicConfig(level="INFO", stream=sys.stderr)
    sys.exit(main())
