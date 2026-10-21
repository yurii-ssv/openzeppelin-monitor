"""M9 — Oracle quorum health (spec §3 M9). One test per trigger line at its severity plus a benign twin, then
thresholds_from_chain, restart, rpc_budget, reorg (depth 1, depth N, crash between detection and handling),
first sweep after start, catch-up after downtime, and inputs that must never read as clean."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fixtures_oracle import (
    HOUR,
    SLOTS,
    WRP,
    MiniSSV,
    OracleChain,
    failed_commit_root,
    incidents,
    keys,
    open_keys,
    oracle_worker,
    restart,
    sent,
    tick,
)

from eb_storage import u256

pytestmark = pytest.mark.m9

TIMEOUT_BLOCKS = 150  # round_timeout_s 1800 / 12 s
REORG_TEXT = "Evidence left the canonical chain"


def pages_warns(w):
    return [(r["ikey"], r["severity"], r["kind"]) for r in sent(w, "m9", "severity IN ('page','warn')")]


def failed_round(oc, w, voters=(1, 2)):
    """Votes that do not reach quorum, then past the timeout: the round failed."""
    ref = oc.c.number - 1
    for s in voters:
        oc.vote(ref, s)
    tick(w, oc, TIMEOUT_BLOCKS + 5)
    return ref


# ---------------------------------------------------------------- V2x.1: failed round (page)


def test_failed_round_pages_after_the_timeout(tmp_path):
    oc, w = oracle_worker(tmp_path)
    tick(w)
    ref = oc.c.number - 1
    oc.vote(ref, 1)
    oc.vote(ref, 3)
    tick(w, oc, TIMEOUT_BLOCKS - 10)  # still inside the 30 min: votes may arrive
    assert open_keys(w, "m9") == {}
    tick(w, oc, 20)
    assert open_keys(w, "m9") == {"failed-round": "page"}
    assert pages_warns(w) == [("failed-round", "page", "open")]
    tick(w, oc, 5)
    assert len(sent(w, "m9")) == 1  # no duplicate while it stays open


def test_benign_round_committed_within_the_timeout_is_silent(tmp_path):
    oc, w = oracle_worker(tmp_path)
    tick(w)
    ref = oc.round(voters=(1, 2, 3), gap=40)  # 3 votes, 80 blocks apart in total: commits at ~16 min
    assert oc.committed(ref)
    tick(w, oc, 3 * TIMEOUT_BLOCKS)
    assert pages_warns(w) == []


def test_failed_round_resolves_when_superseded_or_committed_late(tmp_path):
    oc, w = oracle_worker(tmp_path)
    tick(w)
    ref = failed_round(oc, w)
    assert open_keys(w, "m9") == {"failed-round": "page"}
    oc.vote(ref, 3)  # the third vote arrives late: RootCommitted after the timeout
    tick(w, oc, 1)
    assert open_keys(w, "m9") == {}
    (res,) = sent(w, "m9", "kind='resolved'")
    assert "committed late" in res["text"] and res["delivery"] == "telegram"

    ref2 = failed_round(oc, w)
    oc.round()  # the next round commits: ref2 can never commit (StaleBlockNumber)
    tick(w, oc, 1)
    assert open_keys(w, "m9") == {}
    assert "can no longer commit" in sent(w, "m9", "kind='resolved'")[-1]["text"]
    assert pages_warns(w) == [("failed-round", "page", "open")] * 2  # two runs, two incidents
    assert str(ref2) in incidents(w, "m9")[-1]["title"]


# ---------------------------------------------------------------- V2x.1 + V2b.7: cadence, from the clock (page)


def test_no_root_committed_for_9h_pages_from_the_clock_without_any_event(tmp_path):
    oc, w = oracle_worker(tmp_path)
    tick(w)
    oc.round()
    tick(w, oc, 1)
    logs_before = w.db.conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
    block_before = w.checkpoint()["block"]
    tick(w, seconds=8 * HOUR)
    tick(w, seconds=0.9 * HOUR)  # 8.9 h: inside the margin
    assert open_keys(w, "m9") == {}
    tick(w, seconds=0.2 * HOUR)  # 9.1 h, no block mined, no event stored: only the clock moved
    assert w.checkpoint()["block"] == block_before
    assert w.db.conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0] == logs_before
    assert w.db.meta("run:m9")["reasons"] == ["interval"]
    assert open_keys(w, "m9") == {"cadence": "page"}
    assert pages_warns(w) == [("cadence", "page", "open")]
    oc.round()
    tick(w, oc, 1)
    assert open_keys(w, "m9") == {}
    assert "ended the gap" in sent(w, "m9", "kind='resolved'")[0]["text"]


def test_benign_cadence_every_8h_is_silent(tmp_path):
    oc, w = oracle_worker(tmp_path)
    tick(w)
    for _ in range(4):
        oc.round()
        tick(w, oc, 1)
        tick(w, oc, 2400)  # 8 h of blocks and clock
    assert pages_warns(w) == []


def test_cadence_with_no_commit_since_start_block_pages_after_9h(tmp_path):
    oc, w = oracle_worker(tmp_path)
    tick(w)
    tick(w, seconds=9.5 * HOUR)
    inc = incidents(w, "m9")
    assert open_keys(w, "m9") == {"cadence": "page"} and "start_block" in inc[0]["evidence"]


# ---------------------------------------------------------------- V2x.1: dissent (warn), streak (page)


def test_two_roots_for_one_blocknum_warns_for_the_dissenting_slot(tmp_path):
    oc, w = oracle_worker(tmp_path)
    tick(w)
    oc.round(variants={3: 1})  # slots 1, 2, 4 agree; slot 3 on another build
    tick(w, oc, 1)
    assert open_keys(w, "m9") == {"dissent:3": "warn"}
    assert pages_warns(w) == [("dissent:3", "warn", "open")]


def test_benign_unanimous_round_is_silent(tmp_path):
    oc, w = oracle_worker(tmp_path)
    tick(w)
    oc.round()
    tick(w, oc, 1)
    assert pages_warns(w) == [] and open_keys(w, "m9") == {}


def test_three_dissenting_rounds_in_a_row_from_one_slot_page(tmp_path):
    oc, w = oracle_worker(tmp_path)
    tick(w)
    for _ in range(3):
        oc.round(variants={3: 1})
        tick(w, oc, 1)
    assert open_keys(w, "m9") == {"dissent:3": "page"}
    assert pages_warns(w) == [("dissent:3", "warn", "open"), ("dissent:3", "warn", "update"),
                              ("dissent:3", "page", "escalate")]
    assert "quorum margin is gone" in sent(w, "m9")[-1]["text"]


def test_a_skipped_vote_breaks_the_dissent_run_without_healing_it(tmp_path):
    """QA cp2b-1 F7: "in a row" means consecutive settled rounds. A round the slot did not vote in is not a dissent
    (it breaks the run: no page for dissent/dissent/skip/dissent) and not an agreement (it does not resolve)."""
    oc, w = oracle_worker(tmp_path)
    tick(w)
    for _ in range(2):
        oc.round(variants={3: 1})
        tick(w, oc, 1)
    oc.round(voters=(1, 2, 4))  # slot 3 skipped
    tick(w, oc, 1)
    assert open_keys(w, "m9") == {"dissent:3": "warn"}  # still open: no positive proof of agreement
    oc.round(variants={3: 1})
    tick(w, oc, 1)
    assert open_keys(w, "m9") == {"dissent:3": "warn"}  # dissent / dissent / skip / dissent: not three in a row
    for _ in range(2):
        oc.round(variants={3: 1})
        tick(w, oc, 1)
    assert open_keys(w, "m9")["dissent:3"] == "page"  # three consecutive dissents after the skip
    assert sent(w, "m9", "kind='resolved'") == []
    assert [s for k, s, _ in pages_warns(w) if k == "dissent:3"] == ["warn"] * 4 + ["page"]


def test_benign_two_dissents_then_agreement_never_pages_and_resolves(tmp_path):
    oc, w = oracle_worker(tmp_path)
    tick(w)
    for _ in range(2):
        oc.round(variants={3: 1})
        tick(w, oc, 1)
    oc.round(voters=(3, 1, 2))  # slot 3 votes the committed root again
    tick(w, oc, 1)
    assert open_keys(w, "m9") == {}
    assert [s for _, s, _ in pages_warns(w)] == ["warn", "warn"]
    assert "voted the reference root (and no other)" in sent(w, "m9", "kind='resolved'")[0]["text"]


def test_dissent_in_a_failed_round_uses_the_plurality_root(tmp_path):
    """The 2026-08 outage shape: slots 1 and 4 agree, slot 3 differs, slot 2 absent -> no quorum."""
    oc, w = oracle_worker(tmp_path)
    tick(w)
    ref = oc.c.number - 1
    oc.vote(ref, 1)
    oc.vote(ref, 4)
    oc.vote(ref, 3, variant=1)
    tick(w, oc, TIMEOUT_BLOCKS + 5)
    assert open_keys(w, "m9") == {"failed-round": "page", "dissent:3": "warn"}


def test_tied_roots_without_reference_warn_unattributed(tmp_path):
    oc, w = oracle_worker(tmp_path)
    tick(w)
    ref = oc.c.number - 1
    oc.vote(ref, 1)
    oc.vote(ref, 3, variant=1)
    tick(w, oc, TIMEOUT_BLOCKS + 5)
    assert open_keys(w, "m9") == {"failed-round": "page", "split": "warn"}
    oc.round()
    tick(w, oc, 1)
    assert open_keys(w, "m9") == {}


# ---------------------------------------------------------------- V2x.1: committed with < 3 slots (page)


def test_commit_with_fewer_than_three_distinct_slots_pages(tmp_path):
    oc, w = oracle_worker(tmp_path)
    tick(w)
    oc.set_quorum(5000)  # governance lowers quorumBps: 2 of 4 commit
    tick(w, oc, 1)
    ref = oc.round()
    assert oc.committed(ref) and len([v for v in oc.voted if v[0] == ref]) == 2
    tick(w, oc, 1)
    assert open_keys(w, "m9") == {"weak-commit": "page"}
    oc.set_quorum(7500)
    oc.round()
    tick(w, oc, 1)
    assert open_keys(w, "m9") == {}


def test_benign_commit_with_three_slots_is_silent(tmp_path):
    oc, w = oracle_worker(tmp_path)
    tick(w)
    oc.round()
    tick(w, oc, 1)
    assert "weak-commit" not in {k for k, *_ in pages_warns(w)}


def test_commit_with_fewer_slots_than_its_own_quorum_data_requires_pages(tmp_path):
    """A commit whose own events say 3 votes are needed but only 2 distinct slots voted (one slot counted twice)."""
    oc, w = oracle_worker(tmp_path, checks={"m9": {"min_committing_slots": 2}})
    tick(w)
    ref = oc.c.number - 1
    oc.vote(ref, 1)
    oc.vote(ref, 2)
    # a third vote by slot 2 under another address (the #92676 mid-round rotation double vote)
    oc.voted.discard((ref, oc.root(ref), 2))
    oc.vote(ref, 2, oracle="0x" + "77" * 20)
    assert oc.committed(ref)
    tick(w, oc, 1)
    assert open_keys(w, "m9") == {"weak-commit": "page"}
    assert '"votes_needed_by_round_data": 3' in incidents(w, "m9")[0]["evidence"]


# ---------------------------------------------------------------- V2x.1: slot absent in the last 6 rounds (warn)


def test_slot_absent_from_six_committed_rounds_warns(tmp_path):
    oc, w = oracle_worker(tmp_path)
    tick(w)
    for i in range(6):
        oc.round(voters=(1, 2, 3))
        tick(w, oc, 1)
        assert ("absent:4" in open_keys(w, "m9")) == (i == 5)
    assert open_keys(w, "m9") == {"absent:4": "warn"}
    oc.round(voters=(4, 1, 2))
    tick(w, oc, 1)
    assert open_keys(w, "m9") == {}
    assert "voted the committed root" in sent(w, "m9", "kind='resolved'")[0]["text"]


def test_benign_routine_skipped_fourth_vote_is_silent(tmp_path):
    oc, w = oracle_worker(tmp_path)
    tick(w)
    orders = [(1, 2, 3), (1, 2, 4), (1, 3, 4), (2, 3, 4)] * 3  # every slot skipped now and then, never 6 in a row
    for voters in orders:
        oc.round(voters=voters)
        tick(w, oc, 1)
    assert pages_warns(w) == []


def test_absence_is_not_evaluated_before_six_committed_rounds(tmp_path):
    oc, w = oracle_worker(tmp_path, checks={"m9": {"absent_rounds": 6}})
    tick(w)
    for _ in range(5):
        oc.round(voters=(1, 2, 3))
        tick(w, oc, 1)
    assert open_keys(w, "m9") == {}


# ---------------------------------------------------------------- V2x.1: commitRoot revert from a slot (warn)


def test_commit_root_revert_from_a_slot_address_warns(tmp_path):
    oc, w = oracle_worker(tmp_path, inbox=True)
    tick(w)
    tx = failed_commit_root(oc, tmp_path / "failed-tx.jsonl", SLOTS[3], "AlreadyVoted")
    tick(w, oc, 1)
    key = f"revert:{SLOTS[3]}"
    assert open_keys(w, "m9") == {key: "warn"}
    assert tx in incidents(w, "m9")[0]["evidence"] and "AlreadyVoted" in incidents(w, "m9")[0]["evidence"]
    failed_commit_root(oc, tmp_path / "failed-tx.jsonl", SLOTS[3], "ZeroCSSVSupply")
    tick(w, oc, 1)
    assert [k for k, s, kind in pages_warns(w)] == [key, key]  # re-sent per revert (revision)
    oc.round(voters=(3, 1, 2))  # slot 3 votes successfully again
    tick(w, oc, 1)
    assert open_keys(w, "m9") == {}


def test_benign_commit_root_revert_from_a_non_slot_sender_is_silent(tmp_path):
    oc, w = oracle_worker(tmp_path, inbox=True)
    tick(w)
    failed_commit_root(oc, tmp_path / "failed-tx.jsonl", "0x" + "99" * 20, "NotOracle")
    tick(w, oc, 1)
    assert open_keys(w, "m9") == {}
    assert w.db.conn.execute("SELECT COUNT(*) FROM inbox WHERE canonical=1").fetchone()[0] == 1


# ---------------------------------------------------------------- V2x.2 thresholds_from_chain


def test_thresholds_from_chain_active_slot_set_drives_absence(tmp_path):
    oc, w = oracle_worker(tmp_path)
    tick(w)
    for _ in range(6):
        oc.round(voters=(1, 2, 3))
        tick(w, oc, 1)
    assert open_keys(w, "m9") == {"absent:4": "warn"}
    oc.set_active((1, 2, 3))  # slot 4 deactivated on chain (getActiveOracleIds) + a parameter event
    tick(w, oc, 1)
    assert open_keys(w, "m9") == {}  # no code change: the live slot set changed the alert


def test_thresholds_from_chain_rotated_slot_address_is_a_slot_for_reverts(tmp_path):
    oc, w = oracle_worker(tmp_path, inbox=True)
    tick(w)
    new = "0x" + "5e" * 20
    failed_commit_root(oc, tmp_path / "failed-tx.jsonl", new, "NotOracle")
    tick(w, oc, 1)
    assert open_keys(w, "m9") == {}  # not a slot address yet
    oc.replace(2, new)  # OracleReplaced: the framework re-reads getOracle; M9's prepare picks it up
    failed_commit_root(oc, tmp_path / "failed-tx.jsonl", new, "StaleBlockNumber")
    tick(w, oc, 1)
    assert open_keys(w, "m9") == {f"revert:{new}": "warn"}
    assert '"slot": 2' in incidents(w, "m9")[0]["subject"]


# ---------------------------------------------------------------- V2x.3 restart


def test_restart_does_not_duplicate_open_incidents(tmp_path):
    oc, w = oracle_worker(tmp_path)
    tick(w)
    failed_round(oc, w)
    oc.round(variants={3: 1})
    tick(w, oc, 1)
    tick(w, seconds=10 * HOUR)
    before = sent(w, "m9")
    assert open_keys(w, "m9") == {"dissent:3": "warn", "cadence": "page"}
    assert "failed-round" in {k for k, *_ in pages_warns(w)}
    w2 = restart(tmp_path, w, oc)
    tick(w2)
    tick(w2, oc, 3, seconds=60)
    assert sent(w2, "m9") == before
    assert w2.db.conn.execute("SELECT COUNT(*) FROM incidents WHERE check_id='m9'").fetchone()[0] == len(
        incidents(w, "m9"))


def test_restart_with_prior_non_zero_state_keeps_the_streak(tmp_path):
    oc, w = oracle_worker(tmp_path)
    tick(w)
    for _ in range(2):
        oc.round(variants={3: 1})
        tick(w, oc, 1)
    w2 = restart(tmp_path, w, oc)
    oc.round(variants={3: 1})
    tick(w2, oc, 1)
    assert open_keys(w2, "m9") == {"dissent:3": "page"}
    assert [s for _, s, _ in pages_warns(w2)] == ["warn", "warn", "page"]


# ---------------------------------------------------------------- V2x.4 rpc_budget


def test_rpc_budget_no_eth_call_per_sweep_with_350_clusters(tmp_path):
    oc = OracleChain()
    ssv = MiniSSV(oc.c)
    for i in range(350):
        ssv.register("0x%040x" % (0x1000 + i), (1, 2, 3, 4), keys(1, 100 + i))
    oc, w = oracle_worker(tmp_path, oc=oc, start_block=oc.c.number - 400)
    tick(w)
    for _ in range(3):
        oc.round(variants={3: 1})
        tick(w, oc, 1, seconds=120)
    run = w.db.meta("run:m9")
    assert run["rpc_calls"].get("eth_call", 0) == 0  # bound: 0 eth_call (all from stored logs + live parameters)
    # chain time: one header per block a rule newly needs (checkpoint, first vote, commit), cached afterwards
    assert set(run["rpc_calls"]) <= {"eth_getBlockByNumber"} and run["rpc_calls"]["eth_getBlockByNumber"] <= 3
    tick(w, seconds=120)  # clock only: every header is cached
    assert sum(w.db.meta("run:m9")["rpc_calls"].values()) == 0


# ---------------------------------------------------------------- reorg (README §6.2)


def test_reorg_depth_1_removing_the_late_commit_reopens_the_failed_round(tmp_path):
    oc, w = oracle_worker(tmp_path)
    tick(w)
    ref = failed_round(oc, w)
    fork = oc.c.number
    oc.vote(ref, 3)  # late commit in block `fork`
    tick(w, oc, 1)
    assert open_keys(w, "m9") == {}
    oc.reorg(fork, latest=0)  # the block carrying the RootCommitted is replaced; nothing in it on the new chain
    oc.voted.discard((ref, oc.root(ref), 3))
    tick(w, oc, 2)
    assert open_keys(w, "m9") == {"failed-round": "page"}
    ids = [r["id"] for r in incidents(w, "m9")]
    assert len(ids) == 2 and ids[0] != ids[1]  # a new incident: the condition is back on the canonical chain


@pytest.mark.parametrize("confirmations", [0, 12])
def test_reorg_depth_n_removing_the_votes_resolves_with_the_reorg_text(tmp_path, confirmations):
    oc, w = oracle_worker(tmp_path, confirmations=confirmations)
    tick(w, oc, confirmations + 2)  # a recorded block below the fork
    first = oc.c.number
    ref = first - 1
    oc.vote(ref, 1)
    oc.vote(ref, 2, variant=1)
    tick(w, oc, TIMEOUT_BLOCKS + 10 + confirmations)
    assert open_keys(w, "m9") == {"failed-round": "page", "split": "warn"}
    depth = oc.c.number - first
    assert depth > confirmations + 100
    oc.reorg(first, latest=0, refs={ref})  # depth N: both votes leave the canonical chain
    tick(w, oc, confirmations + 2)
    assert open_keys(w, "m9") == {}
    res = sent(w, "m9", "kind='resolved'")
    assert len(res) == 2 and all(REORG_TEXT in r["text"] for r in res)
    assert w.db.conn.execute("SELECT COUNT(*) FROM m9_episodes").fetchone()[0] == 0
    n = len(sent(w, "m9"))
    tick(w, oc, 5, seconds=7 * HOUR)  # past the 6 h page cadence: nothing re-pages
    w.housekeeping()
    assert len(sent(w, "m9")) == n


def test_reorg_keeps_a_condition_that_survives_on_the_new_chain(tmp_path):
    oc, w = oracle_worker(tmp_path)
    tick(w)
    failed_round(oc, w)
    fork = oc.c.number
    oc.c.mine(3)
    tick(w, oc, 1)
    oc.reorg(fork, latest=0)  # blocks after the deadline replaced; the votes (and the failure) survive
    tick(w, oc, 2)
    assert open_keys(w, "m9") == {"failed-round": "page"}
    assert sent(w, "m9", "kind='resolved'") == [] and len(pages_warns(w)) == 1


def test_reorg_crash_between_detection_and_handling(tmp_path):
    oc, w = oracle_worker(tmp_path)
    tick(w)
    first = oc.c.number
    oc.round(variants={3: 1})
    tick(w, oc, 1)
    assert open_keys(w, "m9") == {"dissent:3": "warn"}
    oc.reorg(first, latest=0, refs={first - 1})
    oc.round(ref=first - 1)  # slot 3 agrees on the new chain
    w.check_reorg()  # detected and rewound (durable marker) ...
    saved = SimpleNamespace(clock=w.clock, cfg=w.cfg)
    del w  # ... and the process dies before the check's reorg run
    w2 = restart(tmp_path, saved, oc)
    tick(w2)
    assert open_keys(w2, "m9") == {}
    (res,) = sent(w2, "m9", "kind='resolved'")
    assert REORG_TEXT in res["text"]
    tick(w2, oc, 2)
    assert len(sent(w2, "m9", "kind='resolved'")) == 1


# ---------------------------------------------------------------- first sweep, downtime, never-clean inputs


def test_first_sweep_after_start_reports_ongoing_conditions_not_healed_history(tmp_path):
    oc = OracleChain()
    oc.c.mine(5)
    start = oc.c.number
    ref = oc.c.number - 1 + 1
    oc.c.mine()
    oc.vote(ref, 1)
    oc.vote(ref, 2)
    oc.c.mine(TIMEOUT_BLOCKS + 5)  # failed ...
    oc.round()  # ... and healed before the worker ever ran
    for _ in range(2):
        oc.round(variants={3: 1})  # an ongoing dissent streak at start
    oc, w = oracle_worker(tmp_path, oc=oc, start_block=start, activation_block=start)
    tick(w)
    assert open_keys(w, "m9") == {"dissent:3": "warn"}
    assert "failed-round" not in {k for k, *_ in pages_warns(w)}


def test_catch_up_after_downtime_reports_a_healed_failed_round_once(tmp_path):
    oc, w = oracle_worker(tmp_path)
    tick(w)  # baseline
    ref = oc.c.number - 1
    oc.vote(ref, 1)
    oc.vote(ref, 2)
    oc.c.mine(TIMEOUT_BLOCKS + 5)
    oc.round()  # the worker was down for the failure and the recovery
    tick(w, oc, 1)
    assert pages_warns(w) == [("failed-round", "page", "open")]
    assert [r["kind"] for r in sent(w, "m9", "kind='resolved'")] == ["resolved"]
    tick(w, oc, 3, seconds=60)
    w2 = restart(tmp_path, w, oc)
    tick(w2, oc, 3, seconds=60)
    assert len(sent(w2, "m9")) == 2 and open_keys(w2, "m9") == {}


def test_malformed_vote_log_raises_and_is_never_read_as_no_vote(tmp_path):
    oc, w = oracle_worker(tmp_path)
    tick(w)
    oc.c.emit_event(WRP, ["0x" + "ab" * 32, "0x%064x" % (oc.c.number - 1)], u256(1))  # one data word, not four
    oc.c.mine()
    tick(w, oc, 1)
    health = [r for r in w.db.conn.execute("SELECT key, text FROM outbox WHERE key LIKE 'health:check-error%'")]
    assert health and "malformed WeightedRootProposed" in health[0][1]
    assert w.db.meta("run:m9")["errors"] >= 1


def test_unreadable_parameters_skip_the_run_instead_of_resolving(tmp_path):
    from fixtures_oracle import VIEWS

    from params import GETTERS, selector

    oc, w = oracle_worker(tmp_path)
    tick(w)
    oc.round(variants={3: 1})
    tick(w, oc, 1)
    assert open_keys(w, "m9") == {"dissent:3": "warn"}
    oc.c.stub_call(VIEWS, selector(GETTERS["quorum_bps"]), revert="0x")
    oc.c.emit_event("QuorumUpdated", [], u256(7500))
    oc.c.mine()
    oc.round(voters=(3, 1, 2))  # slot 3 agrees again, but the live parameters cannot be read
    tick(w, oc, 1)
    assert open_keys(w, "m9") == {"dissent:3": "warn"}
    oc.stub()
    oc.c.emit_event("QuorumUpdated", [], u256(7500))
    tick(w, oc, 1)
    assert open_keys(w, "m9") == {}


def test_missing_views_setting_is_a_startup_error(tmp_path):
    from harness import make_worker

    with pytest.raises(SystemExit, match="views"):
        make_worker(tmp_path, only=["m9"])


# ---------------------------------------------------------------- recorded mainnet replay (2026-08 outage)


def test_replay_mainnet_2026_08_outage(tmp_path):
    """The spec's record (triage scan2, 2026-09-08): slot 3 dissenting in every round, then 8 failed rounds
    25,706,485 .. 25,723,213 (slots 1 + 4 agree, slot 3 differs, slot 2 absent; one round with 3 roots) and a 72 h
    gap between the commits of 25,704,098 and 25,725,603."""
    from fixtures_oracle import replay_mainnet

    failed = [25706485, 25708863, 25711251, 25713643, 25716040, 25718430, 25720821, 25723213]
    oc, w = replay_mainnet(tmp_path)
    opened = {r["key"]: r for r in incidents(w, "m9")}
    fr = opened["failed-round"]  # one incident for the run of failed rounds, re-sent per failed round
    assert fr["severity"] == "page" and fr["state"] == "resolved"
    assert f'"failed_rounds": {json.dumps(failed)}' in fr["evidence"]
    assert [r["kind"] for r in sent(w, "m9", "kind IN ('open','update')") if r["ikey"] == "failed-round"] == (
        ["open"] + ["update"] * 7)
    resolved = [r for r in sent(w, "m9", "kind='resolved'") if r["ikey"] == "failed-round"]
    assert "can no longer commit: round 25725603" in resolved[0]["text"]
    assert opened["cadence"]["severity"] == "page" and opened["cadence"]["state"] == "resolved"
    assert opened["split"]["severity"] == "warn" and "25718430" in opened["split"]["body"]
    assert opened["dissent:3"]["severity"] == "page" and opened["dissent:3"]["state"] == "open"
    escalation = sent(w, "m9", "kind='escalate'")
    assert [r["ikey"] for r in escalation] == ["dissent:3"]
    # spec M9 "a slot that has not voted for the committed root in any of the last 6 rounds": slot 3 voted another
    # root in every committed round, so it is also absent from the committed root from the 6th one (25,692,127)
    assert opened["absent:3"]["severity"] == "warn"
    assert w.db.conn.execute("SELECT closed FROM m9_episodes WHERE episode='absent:3:25692127'").fetchone()[0] == 0
    assert set(opened) == {"failed-round", "cadence", "split", "dissent:3", "absent:3"}
    cadence_open = sent(w, "m9", "kind='open'")
    assert [r["ikey"] for r in cadence_open if r["ikey"] == "cadence"] == ["cadence"]


def test_missing_inbox_is_a_startup_error_unless_opted_out(tmp_path):
    from fixtures_oracle import VIEWS
    from harness import make_worker

    with pytest.raises(SystemExit, match="checks.m9: the commitRoot-revert rule needs the failed-transaction inbox"):
        make_worker(tmp_path, only=["m9"], views=VIEWS)
    oc, w = oracle_worker(tmp_path, inbox=False, checks={"m9": {"require_inbox": False}})
    tick(w)
    assert w.db.conn.execute("SELECT 1 FROM outbox WHERE key LIKE 'health:m9-inbox%'").fetchone()


# ---------------------------------------------------------------- QA cp2b attempt 1 reproducers (permanent)


def test_qa1_f3_two_roots_in_one_block_warn_before_the_timeout(tmp_path):
    """F3: two roots for one blockNum, no quorum yet: warn as soon as the second root is observed."""
    oc, w = oracle_worker(tmp_path)
    tick(w)
    ref = oc.c.number - 1
    oc.vote(ref, 1, mine=False)
    oc.vote(ref, 3, variant=1, mine=False)
    oc.c.mine()
    tick(w, oc, 1)
    assert any(r["severity"] == "warn" for r in sent(w, "m9")), open_keys(w, "m9")
    assert open_keys(w, "m9") == {"split": "warn"}


def test_qa1_f3_two_roots_from_one_slot_after_a_midround_owner_change(tmp_path):
    """F3: slot 1 votes root A, changes owner mid-round, votes root B; slots 2 and 3 commit A: the slot that also
    voted the committed root still proposed a second root (not suppressed)."""
    oc, w = oracle_worker(tmp_path)
    tick(w)
    ref = oc.c.number - 1
    oc.vote(ref, 1)
    oc.replace(1, "0x" + "77" * 20)
    oc.vote(ref, 1, variant=1)
    oc.vote(ref, 2)
    oc.vote(ref, 3)
    tick(w, oc, 1)
    assert any(r["severity"] == "warn" for r in sent(w, "m9")), open_keys(w, "m9")
    assert open_keys(w, "m9") == {"dissent:1": "warn"}


def test_qa1_f7_streak_requires_consecutive_rounds(tmp_path):
    """F7: dissent, dissent, skipped vote, dissent: the last three rounds are not three dissents: warn, not page."""
    oc, w = oracle_worker(tmp_path)
    tick(w)
    for variants, voters in [({3: 1}, (1, 2, 3, 4)), ({3: 1}, (1, 2, 3, 4)), ({}, (1, 2, 4)), ({3: 1}, (1, 2, 3, 4))]:
        oc.round(variants=variants, voters=voters)
        tick(w, oc, 1)
    assert open_keys(w, "m9").get("dissent:3") == "warn", open_keys(w, "m9")


def test_qa1_f8_30_minute_timeout_fires_without_a_new_block(tmp_path):
    """F8: a proposal, then 31 minutes of clock and no block: the failed-round page fires from the clock."""
    oc, w = oracle_worker(tmp_path)
    tick(w)
    oc.vote(oc.c.number - 1, 1)
    tick(w)
    tick(w, seconds=29 * 60)
    assert open_keys(w, "m9") == {}
    tick(w, seconds=2 * 60)
    assert open_keys(w, "m9").get("failed-round") == "page", open_keys(w, "m9")


def test_qa1_control_no_events_restart_silence_daily_config(tmp_path):
    cfg = {"m9": {"max_commit_gap_s": 25 * 3600}}
    oc, w = oracle_worker(tmp_path, checks=cfg)
    tick(w)
    tick(w, seconds=24 * 3600)
    assert open_keys(w, "m9") == {}
    w = restart(tmp_path, w, oc, checks=cfg)
    tick(w)
    tick(w, seconds=1.1 * 3600)
    assert open_keys(w, "m9") == {"cadence": "page"}
    n = len(sent(w, "m9"))
    w = restart(tmp_path, w, oc, checks=cfg)
    tick(w)
    assert len(sent(w, "m9")) == n


def test_qa1_control_first_sweep_without_baseline_long_silence(tmp_path):
    oc, w = oracle_worker(tmp_path)
    oc.c.mine(3000)
    tick(w)
    assert open_keys(w, "m9") == {"cadence": "page"}


def test_qa1_control_proposal_after_commit_is_not_counted_as_quorum(tmp_path):
    from fixtures_oracle import WRP

    oc, w = oracle_worker(tmp_path)
    tick(w)
    ref = oc.c.number - 1
    root = oc.root(ref)
    oc.c.emit_event("RootCommitted", [root, "0x%064x" % ref], b"")
    for slot in (1, 2, 3):
        oc.c.emit_event(WRP, [root, "0x%064x" % ref], u256(10) + u256(30) + u256(slot) + u256(int(SLOTS[slot], 16)))
    oc.c.mine()
    tick(w, oc, 1)
    assert open_keys(w, "m9").get("weak-commit") == "page"


def test_qa1_control_page_and_warn_routing_realert_cadence(tmp_path):
    oc, w = oracle_worker(tmp_path)
    tick(w)
    ref = oc.c.number - 1
    oc.vote(ref, 1)
    oc.vote(ref, 3, variant=1)
    tick(w, oc, 155)
    initial = sent(w, "m9")
    assert {r["severity"] for r in initial} == {"warn", "page"}
    assert all(r["delivery"] == "telegram" for r in initial)
    tick(w, seconds=6 * 3600 + 1)
    w.housekeeping()
    rows = sent(w, "m9")
    assert sum(r["kind"] == "realert" and r["severity"] == "page" for r in rows) == 1
    assert not any(r["kind"] == "realert" and r["severity"] == "warn" for r in rows)
    tick(w, seconds=18 * 3600)
    w.housekeeping()
    assert sum(r["kind"] == "realert" and r["severity"] == "warn" for r in sent(w, "m9")) == 1


# ---------------------------------------------------------------- repair 1: chain time, split lifecycle, inputs


def test_failed_round_deadline_is_chain_time_not_a_block_count(tmp_path):
    """Missed slots: 120 blocks 16 s apart are 32 min of chain time (> 30 min) although fewer than 150 blocks."""
    oc, w = oracle_worker(tmp_path)
    tick(w)
    oc.vote(oc.c.number - 1, 1)
    oc.vote(oc.c.number - 1, 2)
    tick(w)
    oc.c.elapse(100, seconds_per_block=16)  # 26.7 min of chain time in 100 blocks
    tick(w)
    assert open_keys(w, "m9") == {}
    oc.c.elapse(20, seconds_per_block=16)  # 32 min
    tick(w)
    assert open_keys(w, "m9") == {"failed-round": "page"}


def test_commit_29_minutes_after_the_first_vote_in_more_than_150_blocks_is_in_time(tmp_path):
    """The other direction: 160 blocks (more than 150) that span 29 min of chain time are inside the 30 min."""
    oc, w = oracle_worker(tmp_path)
    tick(w)
    ref = oc.c.number - 1
    oc.vote(ref, 1)
    oc.vote(ref, 2)
    for _ in range(160):
        oc.c.mine()  # 12 s per block on the chain ...
    oc.c.times[oc.c.sealed] = oc.c.ts(oc.c.sealed - 160) + 29 * 60  # ... except that the head is at +29 min
    oc.vote(ref, 3)  # commits at +29 min (the block after the head)
    tick(w, oc, 1)
    assert pages_warns(w) == []


def test_split_warns_immediately_and_resolves_when_attributed_at_settlement(tmp_path):
    oc, w = oracle_worker(tmp_path)
    tick(w)
    ref = oc.c.number - 1
    oc.vote(ref, 3, variant=1)
    oc.vote(ref, 1)
    tick(w, oc, 1)
    assert open_keys(w, "m9") == {"split": "warn"}  # one vote each: not attributable yet
    oc.vote(ref, 2)
    oc.vote(ref, 4)  # commits root A
    tick(w, oc, 1)
    assert open_keys(w, "m9") == {"dissent:3": "warn"}
    (res,) = sent(w, "m9", "kind='resolved'")
    assert res["ikey"] == "split" and "attributed per slot" in res["text"]


def test_a_round_settled_before_any_sweep_reports_its_dissent_not_a_split(tmp_path):
    """The split of an open round that settles before a sweep sees it is not lost: it is the slot's dissent."""
    oc, w = oracle_worker(tmp_path)
    tick(w)
    oc.round(variants={3: 1}, voters=(3, 1, 2, 4))
    tick(w, oc, 1)
    assert open_keys(w, "m9") == {"dissent:3": "warn"} and [k for k, *_ in pages_warns(w)] == ["dissent:3"]


def test_a_tied_round_breaks_the_run_without_healing(tmp_path):
    oc, w = oracle_worker(tmp_path)
    tick(w)
    for _ in range(2):
        oc.round(variants={3: 1})
        tick(w, oc, 1)
    ref = oc.c.number - 1
    oc.vote(ref, 1)
    oc.vote(ref, 3, variant=1)  # tied, never commits
    tick(w, oc, 160)
    oc.round(variants={3: 1})  # supersedes the tied round
    tick(w, oc, 1)
    assert open_keys(w, "m9")["dissent:3"] == "warn"  # dissent, dissent, tie, dissent: not three in a row
    assert not [r for r in sent(w, "m9", "kind='resolved'") if r["ikey"] == "dissent:3"]


def test_a_header_without_timestamp_fails_the_run_instead_of_guessing(tmp_path):
    oc, w = oracle_worker(tmp_path)
    tick(w)
    oc.vote(oc.c.number - 1, 1)
    real = oc.c._handle

    def no_time(m, p):
        res = real(m, p)
        if m == "eth_getBlockByNumber" and res is not None:
            res = {k: v for k, v in res.items() if k != "timestamp"}
        return res

    oc.c._handle = no_time
    tick(w, oc, 200)
    run = w.db.meta("run:m9")
    assert run["errors"] >= 1 and "no header timestamp" in run["last_error"]
    assert open_keys(w, "m9") == {}  # nothing concluded from a missing time
    oc.c._handle = real
    tick(w, oc, 1)
    assert open_keys(w, "m9") == {"failed-round": "page"}


def test_a_header_from_another_fork_fails_the_run(tmp_path):
    """The header's hash must be the scanned block's: a node answering from another fork is not read as time."""
    oc, w = oracle_worker(tmp_path)
    tick(w)
    oc.vote(oc.c.number - 1, 1)
    tick(w, oc, 1)
    w.db.conn.execute("DELETE FROM m9_block_times")
    w.db.conn.execute("UPDATE blocks SET hash='0xdead' WHERE number=(SELECT MAX(number) FROM blocks)")
    from rpc import RpcError

    with pytest.raises(RpcError, match="differs from the scanned"):  # an RPC failure: the step fails, retried
        w.run_checks(force=True)
    assert "differs from the scanned" in w.db.meta("run:m9")["last_error"]


def test_reorg_removing_the_evidence_of_an_already_resolved_failed_round_is_silent(tmp_path):
    oc, w = oracle_worker(tmp_path)
    tick(w, oc, 2)
    first = oc.c.number
    ref = failed_round(oc, w)
    oc.vote(ref, 3)  # committed late: resolved
    tick(w, oc, 1)
    assert open_keys(w, "m9") == {} and len(sent(w, "m9", "kind='resolved'")) == 1
    n = len(sent(w, "m9"))
    oc.reorg(first, latest=0, refs={ref})  # the votes, the failure and the late commit leave the chain
    tick(w, oc, 3)
    assert open_keys(w, "m9") == {} and len(sent(w, "m9")) == n
    assert w.db.conn.execute("SELECT COUNT(*) FROM m9_episodes WHERE key='failed-round'").fetchone()[0] == 0


def test_reorg_orphaning_the_clearance_of_a_resolved_dissent_reopens_it(tmp_path):
    """Class 2: the agreement round that resolved a dissent leaves the chain; the dissent rounds survive."""
    oc, w = oracle_worker(tmp_path)
    tick(w)
    oc.round(variants={3: 1})
    tick(w, oc, 1)
    fork = oc.c.number
    ref = oc.round(voters=(3, 1, 2))  # slot 3 agrees: resolved
    tick(w, oc, 1)
    assert open_keys(w, "m9") == {}
    oc.reorg(fork, latest=ref - 2, refs={ref})
    tick(w, oc, 2)
    assert open_keys(w, "m9") == {"dissent:3": "warn"}
    assert [k for k, s, kind in pages_warns(w)] == ["dissent:3", "dissent:3"]  # a new incident on the new chain


def test_a_round_superseded_within_its_30_minutes_is_not_failed_and_one_superseded_later_is(tmp_path):
    """Found by the adversarial sweep: a newer blockNum committed within the 30 min delivered a root (the older round
    can no longer commit, nothing failed); a round still uncommitted at its deadline is a failed round, listed in the
    page of its run even when the run's other rounds were already resolved by the same commit."""
    oc, w = oracle_worker(tmp_path)
    tick(w)
    a = oc.c.number - 1
    oc.vote(a, 1)
    oc.vote(a, 2)  # round a: no quorum
    tick(w, oc, 5)
    oc.round()  # a newer round commits 1 min later
    tick(w, oc, 200)
    assert pages_warns(w) == []
    b = oc.c.number - 1
    oc.vote(b, 1)
    oc.vote(b, 2)
    tick(w, oc, 155)  # b failed
    c = oc.c.number - 1
    oc.vote(c, 3)
    oc.vote(c, 4)  # c: no quorum yet
    oc.round(ref=oc.c.number - 1)  # d commits: closes b (late) and c (inside its 30 min)
    tick(w, oc, 200)
    texts = [r["text"] for r in sent(w, "m9") if r["ikey"] == "failed-round"]
    assert len(texts) == 2 and str(b) in texts[0] and "can no longer commit" in texts[1]
    assert all(f"blockNum {c}" not in t for t in texts) and open_keys(w, "m9") == {}


def test_a_failed_round_added_and_healed_between_two_sweeps_is_still_sent(tmp_path):
    """Found by the adversarial sweep (class 5): the open failed-round incident gains a second failed round and the
    whole run heals before the next sweep: the grown incident is sent (update) before its resolution."""
    oc, w = oracle_worker(tmp_path)
    tick(w)
    failed_round(oc, w)
    b = oc.c.number - 1
    oc.vote(b, 1)
    oc.vote(b, 2)
    oc.c.elapse(160)  # the worker is not stepping: b fails ...
    oc.round()  # ... and a newer round commits
    tick(w, oc, 1)
    kinds = [(r["kind"], f"blockNum {b}" in r["text"] or f", {b}" in r["text"]) for r in sent(w, "m9")
             if r["ikey"] == "failed-round"]
    assert kinds == [("open", False), ("update", True), ("resolved", True)]
