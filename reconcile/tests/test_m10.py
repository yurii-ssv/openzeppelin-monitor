"""M10 — Oracle slot rotation during an open round (spec §3 M10): both page triggers with benign twins, same-block
ordering, thresholds_from_chain (live slot map), restart, rpc_budget, reorg (depth 1, depth N, crash between
detection and handling) and the first sweep after start."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fixtures_oracle import (
    SLOTS,
    MiniSSV,
    OracleChain,
    incidents,
    keys,
    open_keys,
    oracle_worker,
    restart,
    sent,
    tick,
)

from eb_storage import u256

pytestmark = pytest.mark.m10

NEW = "0x" + "4e" * 20
REORG_TEXT = "Evidence left the canonical chain"


def pages(w):
    return [(r["ikey"], r["severity"], r["kind"]) for r in sent(w, "m10", "severity IN ('page','warn')")]


def open_round(oc, voters=(1, 2)):
    ref = oc.c.number - 1
    for s in voters:
        oc.vote(ref, s)
    return ref


def setup(tmp_path, **kw):
    oc, w = oracle_worker(tmp_path, ids=("m10",), **kw)
    tick(w)
    return oc, w


# ---------------------------------------------------------------- V2x.1: rotation during an open round (page)


def test_rotation_during_an_open_round_pages_and_resolves_when_the_round_closes(tmp_path):
    oc, w = setup(tmp_path)
    ref = open_round(oc)
    oc.replace(3, NEW)
    tick(w, oc, 1)
    ((key, sev),) = open_keys(w, "m10").items()
    assert key.startswith("rotation:") and sev == "page"
    inc = incidents(w, "m10")[0]
    assert f"blockNum {ref}: 2 vote(s)" in inc["body"] and "getCommittedRoot" in inc["body"]
    oc.vote(ref, 4)  # the round commits
    tick(w, oc, 1)
    assert open_keys(w, "m10") == {}
    assert "has closed" in sent(w, "m10", "kind='resolved'")[0]["text"]


def test_benign_rotation_between_rounds_is_silent(tmp_path):
    oc, w = setup(tmp_path)
    oc.round()
    tick(w, oc, 1)
    oc.replace(3, NEW)
    tick(w, oc, 1)
    assert pages(w) == []


def test_rotation_while_an_old_round_is_superseded_is_silent(tmp_path):
    oc, w = setup(tmp_path)
    open_round(oc, voters=(1,))  # never reaches quorum ...
    oc.round()  # ... a later round commits: the old one can never commit
    oc.replace(2, NEW)
    tick(w, oc, 1)
    assert pages(w) == []


def test_same_block_commit_then_rotation_is_silent(tmp_path):
    oc, w = setup(tmp_path)
    ref = open_round(oc)
    oc.vote(ref, 3, mine=False)  # commits (log index 0, 1)
    oc.replace(4, NEW, mine=False)  # same block, later log index
    oc.c.mine()
    tick(w, oc, 1)
    assert pages(w) == []


def test_same_block_rotation_then_commit_pages_once_and_resolves(tmp_path):
    oc, w = setup(tmp_path)
    ref = open_round(oc)
    oc.replace(4, NEW, mine=False)  # earlier log index: the round was open at the rotation
    oc.vote(ref, 3, mine=False)
    oc.c.mine()
    tick(w, oc, 1)
    assert [(k.split(":")[0], s, kind) for k, s, kind in pages(w)] == [("rotation", "page", "open")]
    assert len(sent(w, "m10", "kind='resolved'")) == 1 and open_keys(w, "m10") == {}
    tick(w, oc, 2, seconds=60)
    assert len(sent(w, "m10")) == 2


# ---------------------------------------------------------------- V2x.1: newOracle already holds a slot (page)


def test_new_oracle_already_holding_another_slot_pages(tmp_path):
    oc, w = setup(tmp_path)
    oc.replace(2, SLOTS[1])  # slot 2 -> slot 1's key (the contract should revert OracleAlreadyAssigned)
    tick(w, oc, 1)
    assert open_keys(w, "m10") == {f"dup:{SLOTS[1]}": "page"}
    oc.replace(2, NEW)
    tick(w, oc, 1)
    assert open_keys(w, "m10") == {}


def test_benign_rotation_to_a_fresh_address_has_no_duplicate(tmp_path):
    oc, w = setup(tmp_path)
    oc.replace(2, NEW)
    tick(w, oc, 1)
    assert pages(w) == []


def test_thresholds_from_chain_slot_map_change_without_a_rotation_event(tmp_path):
    """The live slot map is read from getOracle: a changed answer (re-read at any parameter event) changes the
    alert with no code change."""
    oc, w = setup(tmp_path)
    tick(w, oc, 1)
    assert pages(w) == []
    oc.slots[3] = SLOTS[4]
    oc.stub()
    oc.c.emit_event("QuorumUpdated", [], u256(7500))
    tick(w, oc, 1)
    assert open_keys(w, "m10") == {f"dup:{SLOTS[4]}": "page"}


# ---------------------------------------------------------------- V2x.3 restart, V2x.4 rpc_budget


def test_restart_does_not_duplicate_the_rotation_page(tmp_path):
    oc, w = setup(tmp_path)
    open_round(oc)
    oc.replace(3, NEW)
    tick(w, oc, 1)
    w2 = restart(tmp_path, w, oc, ids=("m10",))
    tick(w2)
    tick(w2, oc, 2, seconds=120)
    assert len(sent(w2, "m10")) == 1 and len(incidents(w2, "m10")) == 1


def test_rpc_budget_no_eth_call_per_sweep_with_350_clusters(tmp_path):
    oc = OracleChain()
    ssv = MiniSSV(oc.c)
    for i in range(350):
        ssv.register("0x%040x" % (0x2000 + i), (1, 2, 3, 4), keys(1, 900 + i))
    oc, w = oracle_worker(tmp_path, ids=("m10",), oc=oc, start_block=oc.c.number - 400)
    tick(w)
    open_round(oc)
    oc.replace(3, NEW)
    tick(w, oc, 1, seconds=400)
    run = w.db.meta("run:m10")
    assert sum(run["rpc_calls"].values()) == 0  # bound: no RPC of its own (stored logs + live parameters)


# ---------------------------------------------------------------- reorg


def test_reorg_depth_1_removing_the_rotation_resolves_with_the_reorg_text(tmp_path):
    oc, w = setup(tmp_path)
    open_round(oc)
    fork = oc.c.number
    oc.replace(3, NEW)  # the block carrying the trigger event
    tick(w, oc, 1)
    assert len(open_keys(w, "m10")) == 1
    oc.slots[3] = SLOTS[3]
    oc.reorg(fork, latest=0)
    tick(w, oc, 2)
    assert open_keys(w, "m10") == {}
    (res,) = sent(w, "m10", "kind='resolved'")
    assert REORG_TEXT in res["text"]
    assert w.db.conn.execute("SELECT COUNT(*) FROM m10_episodes").fetchone()[0] == 0


@pytest.mark.parametrize("confirmations", [2, 12])
def test_reorg_depth_n_moving_the_rotation_after_the_commit_resolves(tmp_path, confirmations):
    oc, w = oracle_worker(tmp_path, ids=("m10",), confirmations=confirmations)
    tick(w, oc, confirmations + 2)
    fork = oc.c.number
    ref = open_round(oc)
    oc.replace(3, NEW)
    tick(w, oc, confirmations + 20)
    assert len(open_keys(w, "m10")) == 1
    oc.slots[3] = SLOTS[3]
    oc.reorg(fork, latest=0, refs={ref})  # depth > confirmations: on the new chain the round commits first
    oc.c.mine(5)
    oc.round(ref=ref)
    oc.replace(3, NEW)
    tick(w, oc, confirmations + 2)
    assert open_keys(w, "m10") == {}
    assert REORG_TEXT in sent(w, "m10", "kind='resolved'")[0]["text"]
    assert len(pages(w)) == 1  # never re-paged


def test_reorg_crash_between_detection_and_handling(tmp_path):
    oc, w = setup(tmp_path)
    open_round(oc)
    fork = oc.c.number
    oc.replace(3, NEW)
    tick(w, oc, 1)
    oc.slots[3] = SLOTS[3]
    oc.reorg(fork, latest=0)
    oc.c.mine(2)
    w.check_reorg()
    saved = SimpleNamespace(clock=w.clock, cfg=w.cfg)
    del w
    w2 = restart(tmp_path, saved, oc, ids=("m10",))
    tick(w2)
    assert open_keys(w2, "m10") == {}
    assert len(sent(w2, "m10", "kind='resolved'")) == 1


# ---------------------------------------------------------------- first sweep after start


def test_first_sweep_reports_a_rotation_whose_round_is_still_open_not_a_closed_one(tmp_path):
    oc = OracleChain()
    oc.c.mine(5)
    start = oc.c.number
    oc.c.mine()
    ref = open_round(oc)
    oc.replace(1, NEW)  # closed before the worker starts
    oc.vote(ref, 3)
    ref2 = open_round(oc)
    oc.replace(2, "0x" + "4f" * 20)  # still open at start
    oc, w = oracle_worker(tmp_path, ids=("m10",), oc=oc, start_block=start, activation_block=start)
    tick(w)
    assert len(pages(w)) == 1 and str(ref2) in incidents(w, "m10")[0]["body"]
