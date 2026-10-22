"""V2d.7: real mainnet multi-event receipts replayed offline through M1 (GATED; recorded LIVE once).

Each `fixtures/receipts/*.json` was written by `plans/monitoring-service/workers/cp2d/record_receipts.py` from an
archive endpoint (named in the file with the tx hash and block): the block's cluster logs for the clusters of the
transaction, the live `isLiquidated` answers for every distinct tuple at that block, `isLiquidatable(SSV)` of the
final tuple, and the clusters' storage words. `ReplayChain` serves exactly those answers; any other call at those
contracts fails the test, so nothing is filled in by the test.

  faithful   the real logs: M1 must validate the last tuple at the receipt's block (one recorded isLiquidated call)
             and find what the live answer says (every recorded receipt validates: no finding, no detection row);
  synthetic  the real logs plus the outer function's cached snapshot emitted last — the transaction's own Cluster
             argument, storage's tuple before the call, whose live isLiquidated answer at the block is the revert:
             M1 must warn with the recorder's expected repair tuple and log index.
"""

from __future__ import annotations

import glob
import json
import os

import pytest
from fakechain import FakeChain, Revert
from fixtures_cluster import m1_config
from harness import VIEWS, advance, make_worker, outbox, stub_params

from checks.m1_cluster_abi import IS_LIQUIDATED, encode_cluster_call
from eb_storage import Cluster

pytestmark = pytest.mark.m1

RECEIPTS = sorted(glob.glob(os.path.join(os.path.dirname(__file__), "fixtures", "receipts", "*.json")))


def load(path):
    with open(path) as fh:
        return json.load(fh)


class ReplayChain(FakeChain):
    def __init__(self, doc: dict, logs: list[dict]):
        super().__init__(start_block=doc["block"] - 1)
        self.doc = doc
        self.answers = {(c["to"].lower(), c["data"].lower()): c for c in doc["eth_calls"]}
        self.words = {int(k, 16): int(v, 16) for k, v in doc["storage"].items()}
        self.logs = [{**lg, "address": lg["address"].lower(), "removed": False} for lg in logs]
        self.replayed: list[tuple[str, str, int]] = []

    def _eth_call(self, obj: dict, block: int) -> str:
        to, data = (obj.get("to") or "").lower(), obj.get("data", "0x").lower()
        rec = self.answers.get((to, data))
        if rec is None:
            if to == VIEWS and data[:10] in {"0x" + c["data"][2:10] for c in self.doc["eth_calls"]}:
                raise ValueError(f"unrecorded SSVNetworkViews call {data[:74]}…")
            return super()._eth_call(obj, block)  # live-parameter getters (harness.stub_params)
        self.calls.append((to, data, block))
        if block != self.doc["block"]:
            raise ValueError(f"recorded at block {self.doc['block']}, asked at {block}")
        self.replayed.append((to, data, block))
        if "result" in rec:
            return rec["result"]
        raise Revert(rec["revert"])

    def storage(self, slot: int, block: int) -> int:
        return self.words.get(slot, 0) if block >= self.doc["block"] else 0


def replay(tmp_path, doc, logs):
    chain = ReplayChain(doc, logs)
    stub_params(chain)
    _, w = make_worker(tmp_path, only=["m1"], chain=chain, views=VIEWS, checks={"m1": m1_config()})
    chain.mine(2)  # sealed = the receipt's block
    assert chain.sealed == doc["block"]
    advance(w)
    return chain, w


def as_m1(t):
    """The recorder's tuple [count, nfi, index, active, "balance"] in M1's evidence format (all strings)."""
    return [str(t[0]), str(t[1]), str(t[2]), str(t[3]).lower(), str(t[4])]


def test_at_least_ten_recorded_receipts():
    docs = [load(p) for p in RECEIPTS]
    assert len(docs) >= 10
    for d in docs:
        assert d["tx_hash"].startswith("0x") and d["block"] > 24_920_727 and d["source_endpoint"].startswith("https://")
        assert any(len(g["log_indexes"]) >= 2 for g in d["groups"])
    assert sum("synthetic_poison" in g for d in docs for g in d["groups"]) >= 10


@pytest.mark.parametrize("path", RECEIPTS, ids=[os.path.basename(p)[:-5] for p in RECEIPTS])
def test_m1_replays_recorded_mainnet_receipt(tmp_path, path):
    doc = load(path)
    chain, w = replay(tmp_path, doc, doc["logs"])
    expect_warn = any(len(g["log_indexes"]) >= 2 and g["final_in_block"] and not g["last_validates"]
                      for g in doc["groups"])
    rows = outbox(w, "check_id='m1'")
    assert [r["severity"] for r in rows] == (["warn"] if expect_warn else [])
    if not expect_warn:
        assert w.db.conn.execute("SELECT COUNT(*) FROM m1_detections").fetchone()[0] == 0
    replayed = {d for _, d, _ in chain.replayed}
    for g in doc["groups"]:
        if len(g["log_indexes"]) >= 2 and g["final_in_block"]:
            t = g["last_tuple"]
            last = "0x" + encode_cluster_call(IS_LIQUIDATED, g["owner"], g["operator_ids"],
                                              Cluster(t[0], t[1], t[2], t[3], int(t[4]))).hex()
            assert last in replayed, "the group's last tuple is validated at the receipt's block"


SYNTHETIC = [(p, i) for p in RECEIPTS for i, g in enumerate(load(p)["groups"]) if "synthetic_poison" in g]


@pytest.mark.parametrize("path,group", SYNTHETIC, ids=[os.path.basename(p)[:-5] for p, _ in SYNTHETIC])
def test_m1_flags_recorded_receipt_with_a_stale_snapshot_emitted_last(tmp_path, path, group):
    doc = load(path)
    g = doc["groups"][group]
    syn = g["synthetic_poison"]
    chain, w = replay(tmp_path, doc, syn["logs"])
    rows = outbox(w, "check_id='m1'")
    assert [(r["severity"], r["kind"]) for r in rows] == [("warn", "open")]
    inc = w.db.conn.execute("SELECT * FROM incidents WHERE check_id='m1'").fetchone()
    assert inc["key"] == g["cluster_id"]
    ev = json.loads(inc["evidence"])
    assert ev["source"] == "receipt" and ev["poison_tx"] == doc["tx_hash"] and ev["poison_block"] == doc["block"]
    assert ev["stale_tuple"] == as_m1(syn["stale_tuple"])
    assert ev["poison_log_index"] == syn["expected"]["stale_log_index"]
    assert ev["repair_tuple"] == as_m1(syn["expected"]["repair_tuple"])
    assert ev["repair_log_index"] == syn["expected"]["repair_log_index"]
    assert ev["liquidatable"] is not None, "isLiquidatable of the repair tuple answered from the recording"
