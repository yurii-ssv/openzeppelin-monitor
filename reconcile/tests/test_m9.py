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
    assert set(run["rpc_calls"]) <= {"eth_getBlockByNumber"} and run["rpc_calls"]["eth_getBlockByNumber"] <= 4
    tick(w, seconds=120)  # clock only: every header is cached; one uncached probe of the block after the checkpoint
    assert w.db.meta("run:m9")["rpc_calls"] == {"eth_getBlockByNumber": 1}  # (repair 3, horizon)


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


def test_a_round_superseded_within_its_30_minutes_still_fails_at_its_deadline(tmp_path):
    """QA2-F1 (repair 2; replaces repair 1's superseded-in-time reading, which silenced the trigger): spec M9 line 1
    is literal, "WeightedRootProposed for a blockNum with no RootCommitted within 30 min -> page"; a newer blockNum's
    commit makes the round un-committable but is not its RootCommitted. The round pages at its deadline (with its
    resolution: it can no longer commit), as its own incident even when the commit that closed it also resolved an
    open run of failed rounds."""
    oc, w = oracle_worker(tmp_path)
    tick(w)
    a = oc.c.number - 1
    oc.vote(a, 1)
    oc.vote(a, 2)  # round a: no quorum
    tick(w, oc, 5)
    oc.round()  # a newer round commits 1 min later
    tick(w, oc, 100)  # 21 min after a's first vote: not failed yet
    assert pages_warns(w) == []
    tick(w, oc, 100)  # past a's 30 min: page, resolved in the same run (a can no longer commit)
    assert pages_warns(w) == [("failed-round", "page", "open")]
    assert [(r["ikey"], r["kind"]) for r in sent(w, "m9")] == [("failed-round", "open"), ("failed-round", "resolved")]
    first = [r["text"] for r in sent(w, "m9") if r["ikey"] == "failed-round"]
    assert f"Oracle round {a} not committed" in first[0] and "inside this round's 30 min" in first[0]
    assert "can no longer commit" in first[1] and open_keys(w, "m9") == {}
    b = oc.c.number - 1
    oc.vote(b, 1)
    oc.vote(b, 2)
    tick(w, oc, 155)  # b failed
    c = oc.c.number - 1
    oc.vote(c, 3)
    oc.vote(c, 4)  # c: no quorum yet
    d = oc.round(ref=oc.c.number - 1)  # d commits: closes b (late) and c (inside its 30 min)
    tick(w, oc, 1)
    texts = [r["text"] for r in sent(w, "m9") if r["ikey"] == "failed-round"][2:]
    assert len(texts) == 2 and f"round {b}" in texts[0] and "can no longer commit" in texts[1]
    tick(w, oc, 200)  # past c's deadline: c pages as its own incident, already resolved by d
    texts = [r["text"] for r in sent(w, "m9") if r["ikey"] == "failed-round"][4:]
    assert len(texts) == 2 and f"Oracle round {c} not committed" in texts[0] and f"blockNum {d}" in texts[0]
    assert "in a row" not in texts[0].splitlines()[0]  # its own incident, not b's run grown by c (repair 3)
    assert "can no longer commit" in texts[1] and open_keys(w, "m9") == {}
    tick(w, oc, 5)
    assert len([r for r in sent(w, "m9") if r["ikey"] == "failed-round"]) == 6  # no duplicate


def test_qa2_f1_lone_vote_for_an_earlier_blocknum_that_never_commits_pages(tmp_path):
    """QA2-F1 reproducer (qa/cp2b-attempt-2.md Appendix A): slot 3 proposes blockNum X-1, slots 1, 2, 4 propose X
    and commit it 3 blocks later. X-1 never gets a RootCommitted: 30 min after its proposal it is a failed round
    (page). X-1 is below start_block here: the failed-round rule still applies (a stored vote proves no commit of
    X-1 before it)."""
    oc, w = oracle_worker(tmp_path)
    tick(w)
    x = oc.c.number - 1
    oc.vote(x - 1, 3)  # slot 3: wrong (earlier) blockNum
    oc.round(ref=x, voters=(1, 2, 4))
    assert oc.committed(x)
    tick(w, oc, 1)
    assert pages_warns(w) == []
    tick(w, oc, 160)  # > 30 min after the X-1 proposal
    assert pages_warns(w) == [("failed-round", "page", "open")]
    assert [(r["ikey"], r["kind"]) for r in sent(w, "m9")] == [("failed-round", "open"), ("failed-round", "resolved")]
    opened = sent(w, "m9", "kind='open'")[0]["text"]
    assert f"Oracle round {x - 1} not committed" in opened and "slot 3" in opened
    n = len(sent(w, "m9"))
    w = restart(tmp_path, w, oc)
    tick(w, oc, 5)
    assert len(sent(w, "m9")) == n  # reported once, also across a restart


def test_qa2_f1_lone_vote_for_a_later_blocknum_pages_control(tmp_path):
    """QA2-F1 control: the same anomaly with the wrong blockNum above X (not superseded) pages and stays open."""
    oc, w = oracle_worker(tmp_path)
    tick(w)
    x = oc.c.number - 2
    oc.vote(x + 1, 3)
    oc.round(ref=x, voters=(1, 2, 4))
    tick(w, oc, 161)
    assert open_keys(w, "m9").get("failed-round") == "page", open_keys(w, "m9")


def test_qa2_f1_superseded_round_whose_deadline_passes_after_the_first_sweep_is_reported(tmp_path):
    """The check starts between the superseding commit and the stray round's deadline (the commit is at or below the
    baseline): the failure is established at the deadline, after the baseline, so it is still reported."""
    oc = OracleChain()
    oc.c.mine(100)
    x = oc.c.number - 1
    oc.vote(x - 1, 3)
    oc.round(ref=x, voters=(1, 2, 4))
    oc.c.mine(20)
    oc, w = oracle_worker(tmp_path, oc=oc, start_block=1000, activation_block=1000)
    tick(w)  # first run (baseline) after the commit, before X-1's deadline
    assert pages_warns(w) == []
    tick(w, oc, 160)
    assert [(r["ikey"], r["kind"]) for r in sent(w, "m9")] == [("failed-round", "open"), ("failed-round", "resolved")]


def test_qa2_f1_superseded_round_failed_before_the_first_sweep_is_history(tmp_path):
    """Benign twin: the stray round's deadline passed before the check's first run: history, not paged."""
    oc = OracleChain()
    oc.c.mine(100)
    x = oc.c.number - 1
    oc.vote(x - 1, 3)
    oc.round(ref=x, voters=(1, 2, 4))
    oc.c.mine(200)
    oc, w = oracle_worker(tmp_path, oc=oc, start_block=1000, activation_block=1000)
    tick(w)
    tick(w, oc, 5)
    assert pages_warns(w) == []


def stray_then_commit(oc, same_block=False):
    """Slot 3 proposes blockNum X-1; slots 1, 2, 4 propose X and commit it (optionally all in one block)."""
    oc.c.mine(2)
    x = oc.c.number - 1
    oc.vote(x - 1, 3, mine=not same_block)
    for s in (1, 2, 4):
        oc.vote(x, s, mine=not same_block)
    if same_block:
        oc.c.mine()
    assert oc.committed(x)
    return x


def test_qa2_f1_same_block_stray_vote_and_superseding_commit_pages_at_the_deadline(tmp_path):
    """(f) same-block ordering: the stray vote and the commit of X in one block (the stray vote first: after the
    commit it would revert StaleBlockNumber). Still a failed round at X-1's deadline."""
    oc, w = oracle_worker(tmp_path, start_block=1000, activation_block=1000)
    tick(w)
    x = stray_then_commit(oc, same_block=True)
    tick(w, oc, TIMEOUT_BLOCKS - 5)
    assert pages_warns(w) == []
    tick(w, oc, 10)
    assert pages_warns(w) == [("failed-round", "page", "open")] and str(x - 1) in sent(w, "m9")[0]["text"]


def test_qa2_f1_superseded_round_fails_from_the_clock_when_blocks_stop(tmp_path):
    """(g) anchoring: no block after the commit; the clock passes X-1's deadline: the failure is established at the
    checkpoint (no later block exists) and reported."""
    oc, w = oracle_worker(tmp_path, start_block=1000, activation_block=1000)
    tick(w)
    stray_then_commit(oc)
    tick(w, oc, 1)
    tick(w, seconds=29 * 60)
    assert pages_warns(w) == []
    tick(w, seconds=3 * 60)
    assert pages_warns(w) == [("failed-round", "page", "open")]


def test_qa2_f1_reorg_removing_the_stray_vote_of_a_resolved_failed_round_is_silent(tmp_path):
    """(c) reorg removing the evidence of an ALREADY RESOLVED key: the stray round was paged and resolved; a reorg
    beyond the confirmation window replaces the block of the stray vote (the new chain has X's votes only): the
    episode vanishes, nothing is re-sent, nothing is opened."""
    oc, w = oracle_worker(tmp_path, confirmations=2, start_block=1000, activation_block=1000)
    tick(w, oc, 4)
    fork = oc.c.number
    x = stray_then_commit(oc)
    tick(w, oc, TIMEOUT_BLOCKS + 10)
    n = len(sent(w, "m9"))
    assert n == 2 and open_keys(w, "m9") == {}
    oc.reorg(fork, latest=0, refs={x - 1, x})
    oc.c.mine(2)
    oc.round(ref=x, voters=(1, 2, 4))
    tick(w, oc, TIMEOUT_BLOCKS + 10)
    assert len(sent(w, "m9")) == n and open_keys(w, "m9") == {}


def test_qa2_f1_reorg_removing_the_superseding_commit_reopens_the_failed_round(tmp_path):
    """(c) a reorg removes X's commit (the stray round's closure) after the stray round was resolved: on the new chain
    neither X-1 nor X commits: a failed-round page opens again (surviving evidence is never dropped)."""
    oc, w = oracle_worker(tmp_path, confirmations=2, start_block=1000, activation_block=1000)
    tick(w, oc, 4)
    x = stray_then_commit(oc)
    tick(w, oc, TIMEOUT_BLOCKS + 10)
    assert open_keys(w, "m9") == {}
    commit_block = next(e.block for e in w.db.events_after((-1, 0), w.checkpoint()["block"], frozenset({"RootCommitted"})))
    oc.reorg(commit_block, latest=0, refs={x})
    oc.c.mine(TIMEOUT_BLOCKS + 5)  # the new chain passes the 30 min in chain time (repair 3: the clock alone never
    tick(w, oc, 5)                 # passes a block that exists above the checkpoint)
    assert open_keys(w, "m9") == {"failed-round": "page"}


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


# ---------------------------------------------------------------- repair 1: state-table rows (worker record)


def test_inbox_unavailable_keeps_the_commit_root_revert_incident(tmp_path):
    oc, w = oracle_worker(tmp_path, inbox=True)
    tick(w)
    failed_commit_root(oc, tmp_path / "failed-tx.jsonl", SLOTS[3], "AlreadyVoted")
    tick(w, oc, 1)
    key = f"revert:{SLOTS[3]}"
    assert open_keys(w, "m9") == {key: "warn"}
    (tmp_path / "failed-tx.jsonl").unlink()  # the Monitor's file is gone: reverts cannot be read
    tick(w, oc, 1)
    oc.round(voters=(3, 1, 2))  # would heal it, but the input is unknown
    tick(w, oc, 1)
    assert open_keys(w, "m9") == {key: "warn"}
    (tmp_path / "failed-tx.jsonl").write_text("")
    tick(w, oc, 1)
    tick(w, oc, 1, seconds=60)
    assert open_keys(w, "m9") == {}


def test_reorg_orphaning_a_commit_root_revert_resolves_it_with_the_reorg_text(tmp_path):
    oc, w = oracle_worker(tmp_path, inbox=True)
    tick(w, oc, 2)
    fork = oc.c.number
    failed_commit_root(oc, tmp_path / "failed-tx.jsonl", SLOTS[3], "AlreadyVoted")
    tick(w, oc, 1)
    assert open_keys(w, "m9") == {f"revert:{SLOTS[3]}": "warn"}
    oc.c.reorg(fork)  # the reverted transaction's block is replaced: the inbox entry is orphaned
    oc.stub()
    tick(w, oc, 3)
    assert open_keys(w, "m9") == {}
    (res,) = sent(w, "m9", "kind='resolved'")
    assert REORG_TEXT in res["text"]
    assert w.db.conn.execute("SELECT canonical FROM inbox").fetchone()[0] == 0


def test_reorg_replacing_a_vote_block_rereads_its_timestamp(tmp_path):
    """m9_block_times: the cached timestamp of a replaced block is dropped in the reorg run; the deadline follows the
    new block's time (here 10 min later on the new chain)."""
    oc, w = oracle_worker(tmp_path)
    tick(w, oc, 2)
    ref = oc.c.number - 1
    vote_block = oc.c.number
    oc.vote(ref, 1)
    tick(w)
    t_old = oc.c.ts(vote_block)
    assert w.db.conn.execute("SELECT ts FROM m9_block_times WHERE block=?", (vote_block,)).fetchone()[0] == t_old
    oc.c.reorg(vote_block)
    oc.voted.clear()
    oc.acc.clear()
    oc.stub()
    oc.c.replaced[vote_block] = t_old + 600
    oc.vote(ref, 1)  # the same vote, in a block 10 min later on the new chain
    tick(w)
    w.clock.now = t_old + 1900
    tick(w)  # past the old deadline, before the new one
    assert open_keys(w, "m9") == {}
    tick(w, seconds=600)
    assert open_keys(w, "m9") == {"failed-round": "page"}


# ---------------------------------------------------------------- repair 3 (QA attempt 3)


def _dissent_page(oc, w, n=3):
    for _ in range(n):
        tick(w, oc, 10)
        oc.round(variants={3: 1}, voters=(3, 1, 2, 4))
        tick(w, oc, 5)
    assert open_keys(w, "m9").get("dissent:3") == ("page" if n >= 3 else "warn"), open_keys(w, "m9")


def _dissent_resolutions(w):
    return [r["text"] for r in sent(w, "m9", "kind='resolved'") if r["ikey"] == "dissent:3"]


def test_qa3_f2_stray_single_vote_round_is_not_positive_proof_that_a_dissenting_slot_agrees(tmp_path):
    """QA3-F2 reproducer (class 1: degenerate input read as positive proof): after a dissent page, slot 3 alone
    proposes blockNum X-1 while slots 1, 2, 4 commit X. The stray round has one voter: no reference to agree with;
    the page stays open (also across a restart)."""
    oc, w = oracle_worker(tmp_path)
    tick(w)
    _dissent_page(oc, w)
    tick(w, oc, 10)
    x = oc.c.number - 1
    oc.vote(x - 1, 3, variant=1)
    oc.round(ref=x, voters=(1, 2, 4))
    tick(w, oc, 200)  # past X-1's 30 min (a failed round of its own, QA2-F1)
    assert open_keys(w, "m9").get("dissent:3") == "page" and not _dissent_resolutions(w), open_keys(w, "m9")
    w = restart(tmp_path, w, oc)
    tick(w, oc, 5, seconds=60)
    assert open_keys(w, "m9").get("dissent:3") == "page" and not _dissent_resolutions(w)


def test_qa3_f2_timed_out_round_with_only_the_dissenter_voting_does_not_heal_its_page(tmp_path):
    """QA3-F2 reproducer: the other three slots are down, only slot 3 votes, the round times out (failed round,
    page). Slot 3's lone root is not a reference: dissent:3 stays at page."""
    oc, w = oracle_worker(tmp_path)
    tick(w)
    _dissent_page(oc, w)
    tick(w, oc, 10)
    x = oc.c.number - 1
    oc.vote(x, 3, variant=1)
    tick(w, oc, 200)
    keys = open_keys(w, "m9")
    assert keys.get("failed-round") == "page" and keys.get("dissent:3") == "page" and not _dissent_resolutions(w), keys


def test_qa3_f2_self_referential_committed_root_does_not_heal(tmp_path):
    """Self-referential reference: quorum lowered to one slot, slot 3 alone commits its own root (a weak commit,
    page). Slot 3 "voting the committed root" proves nothing, so dissent:3 is not healed."""
    oc, w = oracle_worker(tmp_path)
    tick(w)
    _dissent_page(oc, w)
    oc.set_quorum(2500)
    tick(w, oc, 10)
    oc.round(voters=(3,), variants={3: 1})
    tick(w, oc, 5)
    keys = open_keys(w, "m9")
    assert keys.get("weak-commit") == "page" and keys.get("dissent:3") == "page" and not _dissent_resolutions(w), keys


def test_qa3_f2_lone_voter_round_does_not_extend_the_dissent_run(tmp_path):
    """Neither heal nor extend: dissent, dissent, a round only slot 3 voted (times out), dissent: the lone round is
    no attributable dissent, it breaks the run like a tied round: warn, not page; nothing resolved."""
    oc, w = oracle_worker(tmp_path)
    tick(w)
    _dissent_page(oc, w, 2)
    tick(w, oc, 10)
    oc.vote(oc.c.number - 1, 3, variant=1)
    tick(w, oc, 200)
    tick(w, oc, 10)
    oc.round(variants={3: 1}, voters=(3, 1, 2, 4))
    tick(w, oc, 5)
    assert open_keys(w, "m9").get("dissent:3") == "warn" and not _dissent_resolutions(w), open_keys(w, "m9")


def test_qa3_f2_timed_out_round_where_another_slot_votes_the_same_root_heals_control(tmp_path):
    """Benign twin: a timed-out round in which slots 3 and 1 vote the same root (two distinct slots: a reference)
    is positive proof that slot 3 agrees: the page resolves with its agreement text."""
    oc, w = oracle_worker(tmp_path)
    tick(w)
    _dissent_page(oc, w)
    tick(w, oc, 10)
    x = oc.c.number - 1
    oc.vote(x, 3)
    oc.vote(x, 1)
    tick(w, oc, 200)
    assert "dissent:3" not in open_keys(w, "m9"), open_keys(w, "m9")
    assert _dissent_resolutions(w) and f"in round {x}" in _dissent_resolutions(w)[0], _dissent_resolutions(w)


def test_qa3_f2_one_slot_voting_one_root_twice_is_no_reference(tmp_path):
    """Distinct slots, not votes: slot 3 votes its root from its old and from its new address (an owner rotation
    inside the round); still one slot: no reference, the dissent page stays."""
    oc, w = oracle_worker(tmp_path)
    tick(w)
    _dissent_page(oc, w)
    tick(w, oc, 10)
    x = oc.c.number - 1
    oc.vote(x, 3, variant=1)
    oc.voted.discard((x, oc.root(x, 1), 3))  # the contract keys AlreadyVoted by address: the new key may vote
    oc.vote(x, 3, variant=1, oracle="0x" + "33" * 20)
    tick(w, oc, 200)
    assert open_keys(w, "m9").get("dissent:3") == "page" and not _dissent_resolutions(w), open_keys(w, "m9")


# ---- repair 3 generalisation, state table (c): a reorg beyond the confirmation window that removes the CLEARANCE of
# an already resolved key (the condition's own evidence survives): the key holds again on the new chain and is
# reported again (supported by evidence), and a restart sends nothing more


def _reopened(tmp_path, w, oc, key, sev):
    keys = open_keys(w, "m9")
    assert keys.get(key) == sev, (key, keys, pages_warns(w))
    n = len(sent(w, "m9"))
    w = restart(tmp_path, w, oc)
    tick(w, oc, 2, seconds=60)
    assert open_keys(w, "m9").get(key) == sev and len([r for r in sent(w, "m9")[n:] if r["ikey"] == key]) == 0
    return w


def test_r3_reorg_removing_the_commit_that_ended_a_resolved_cadence_gap_reopens_it(tmp_path):
    oc, w = oracle_worker(tmp_path, confirmations=2)
    tick(w)
    oc.round()
    tick(w, oc, 5)
    oc.c.elapse(2860)  # 9.5 h of blocks without a RootCommitted (with confirmations the clock alone stops at the tail)
    tick(w, oc, 3)
    assert open_keys(w, "m9") == {"cadence": "page"}
    fork = oc.c.number
    latest = oc.latest
    ref = oc.round()
    tick(w, oc, 5)
    assert open_keys(w, "m9") == {}
    oc.reorg(fork, latest=latest, refs={ref})
    oc.c.mine(6)  # the new chain has no RootCommitted after the gap
    tick(w, oc, 2)
    _reopened(tmp_path, w, oc, "cadence", "page")


def test_r3_reorg_removing_the_adequate_commit_that_healed_a_weak_commit_reopens_it(tmp_path):
    oc, w = oracle_worker(tmp_path, confirmations=2)
    tick(w)
    oc.set_quorum(2500)
    oc.round(voters=(1,))  # committed by one slot
    tick(w, oc, 5)
    assert open_keys(w, "m9").get("weak-commit") == "page"
    oc.set_quorum(7500)
    fork = oc.c.number
    latest = oc.latest
    ref = oc.round()
    tick(w, oc, 5)
    assert "weak-commit" not in open_keys(w, "m9")
    oc.reorg(fork, latest=latest, refs={ref})
    oc.c.mine(6)
    tick(w, oc, 2)
    _reopened(tmp_path, w, oc, "weak-commit", "page")


def test_r3_reorg_removing_the_vote_that_healed_an_absent_slot_reopens_it(tmp_path):
    oc, w = oracle_worker(tmp_path, confirmations=2)
    tick(w)
    for _ in range(6):
        oc.round(voters=(1, 2, 4))
        tick(w, oc, 3)
    assert open_keys(w, "m9") == {"absent:3": "warn"}
    fork = oc.c.number
    latest = oc.latest
    ref = oc.round(voters=(3, 1, 2))
    tick(w, oc, 5)
    assert open_keys(w, "m9") == {}
    oc.reorg(fork, latest=latest, refs={ref})
    oc.c.mine(3)
    oc.round(voters=(1, 2, 4))  # the new chain's round: slot 3 absent again
    tick(w, oc, 5)
    _reopened(tmp_path, w, oc, "absent:3", "warn")


def test_r3_reorg_removing_the_vote_that_healed_a_commit_root_revert_reopens_it(tmp_path):
    oc, w = oracle_worker(tmp_path, inbox=True, confirmations=2)
    tick(w, oc, 3)
    failed_commit_root(oc, tmp_path / "failed-tx.jsonl", SLOTS[3], "AlreadyVoted")
    tick(w, oc, 3)
    key = f"revert:{SLOTS[3]}"
    assert open_keys(w, "m9") == {key: "warn"}
    oc.c.mine(2)
    fork = oc.c.number
    latest = oc.latest
    ref = oc.round(voters=(3, 1, 2))
    tick(w, oc, 5)
    assert open_keys(w, "m9") == {}
    oc.reorg(fork, latest=latest, refs={ref})
    oc.c.mine(6)
    tick(w, oc, 2)
    _reopened(tmp_path, w, oc, key, "warn")


def test_r3_reorg_removing_the_commit_that_settled_a_split_reopens_it(tmp_path):
    oc, w = oracle_worker(tmp_path, confirmations=2)
    tick(w)
    ref = oc.c.number - 1
    oc.vote(ref, 1)
    oc.vote(ref, 3, variant=1)
    tick(w, oc, 3)
    assert open_keys(w, "m9") == {"split": "warn"}
    fork = oc.c.number
    oc.vote(ref, 2)
    oc.vote(ref, 4)
    tick(w, oc, 3)
    assert open_keys(w, "m9") == {"dissent:3": "warn"}
    oc.reorg(fork, latest=0)
    oc.acc = {k: a for k, a in oc.acc.items() if k[0] != ref or k[1] != oc.root(ref)} | {(ref, oc.root(ref)): 0}
    oc.voted -= {(ref, oc.root(ref), 2), (ref, oc.root(ref), 4)}
    oc.c.mine(4)
    tick(w, oc, 2)
    keys = open_keys(w, "m9")
    assert keys == {"split": "warn"}, keys


def test_r3_lone_voter_round_settled_by_the_clock_does_not_heal(tmp_path):
    """(f) window anchoring: blocks stop after slot 3's lone vote; the round settles from the clock (horizon) at its
    deadline: still no reference, the dissent page stays (also across a restart)."""
    oc, w = oracle_worker(tmp_path)
    tick(w)
    _dissent_page(oc, w)
    tick(w, oc, 10)
    oc.vote(oc.c.number - 1, 3, variant=1)
    tick(w, oc, 2)
    tick(w, seconds=40 * 60)  # no block: the clock passes the 30 min
    keys = open_keys(w, "m9")
    assert keys.get("failed-round") == "page" and keys.get("dissent:3") == "page" and not _dissent_resolutions(w), keys
    w = restart(tmp_path, w, oc)
    tick(w, seconds=120)
    assert open_keys(w, "m9").get("dissent:3") == "page" and not _dissent_resolutions(w)


def test_r3_one_slot_voting_twice_does_not_make_another_slot_a_dissenter(tmp_path):
    """Distinct slots decide the reference (generalisation of QA3-F2): slot 3 votes root X from its old and its new
    address, slot 1 votes Y, the round times out. One slot each: tied, no reference: unattributed split, and slot 1
    is not made a dissenter by slot 3's double vote."""
    oc, w = oracle_worker(tmp_path)
    tick(w)
    x = oc.c.number - 1
    oc.vote(x, 3, variant=1)
    oc.voted.discard((x, oc.root(x, 1), 3))
    oc.vote(x, 3, variant=1, oracle="0x" + "33" * 20)
    oc.vote(x, 1)
    tick(w, oc, 200)
    assert open_keys(w, "m9") == {"failed-round": "page", "split": "warn"}, open_keys(w, "m9")


def test_r3_a_lower_blocknum_failing_after_a_higher_one_is_sent_before_the_heal(tmp_path):
    """Found by the repair-3 wider sweep (class 5, event lost between sweeps): a stray vote for X+1 comes first,
    round X's first vote 3 blocks later. X+1 fails first (page); X fails 36 s later and a superseding commit heals
    both before the next sweep. The incident's revision named only the highest member, so X was never sent: now
    every member is part of the revision and X is sent before the resolution."""
    oc, w = oracle_worker(tmp_path)
    tick(w)
    x = oc.c.number - 2
    b = oc.c.number
    oc.vote(x + 1, 4)  # mined in block b
    oc.c.mine(2)
    oc.vote(x, 1)  # mined in block b + 3
    tick(w, oc, b + TIMEOUT_BLOCKS + 2 - oc.c.number)  # sealed b + 151: past X+1's deadline only
    texts = [r["text"] for r in sent(w, "m9") if r["ikey"] == "failed-round"]
    assert open_keys(w, "m9") == {"failed-round": "page"} and all(f"blockNum {x} " not in t for t in texts), texts
    oc.c.mine(10)  # past X's deadline, no sweep
    oc.round()  # a later blockNum commits: both can no longer commit
    tick(w, oc, 2)
    texts = [r["text"] for r in sent(w, "m9", "kind != 'resolved'") if r["ikey"] == "failed-round"]
    assert any(f"{x}, {x + 1}" in t for t in texts), texts
    assert open_keys(w, "m9") == {} and sent(w, "m9")[-1]["kind"] == "resolved"


def test_r3_reorg_growing_an_already_resolved_failed_round_run_sends_the_new_member(tmp_path):
    """Found by the repair-3 wider sweep (class 2: reorg of a resolved key): round A failed (page) and was resolved by
    round B's commit. A reorg beyond the confirmation window removes B's commit (B's votes stay): on the new chain B
    fails too and a later commit closes both. B is a failed round never sent: the resolved run is reported again
    with B (once), then resolved; a restart sends nothing more."""
    oc, w = oracle_worker(tmp_path, confirmations=2)
    tick(w)
    a = oc.c.number - 1
    oc.vote(a, 1)
    tick(w, oc, TIMEOUT_BLOCKS + 5)
    assert open_keys(w, "m9") == {"failed-round": "page"}
    b = oc.c.number - 1
    oc.vote(b, 1)
    oc.vote(b, 2)
    fork = oc.c.number
    oc.vote(b, 4)  # B commits: A can no longer commit
    tick(w, oc, 4)
    assert open_keys(w, "m9") == {} and sent(w, "m9")[-1]["kind"] == "resolved"
    oc.reorg(fork, latest=0, refs={b})  # B's commit (and fourth vote) leave the chain
    oc.c.mine(TIMEOUT_BLOCKS + 5)  # B's 30 min pass on the new chain
    oc.round()  # a later blockNum commits: A and B can no longer commit
    tick(w, oc, 4)
    texts = [r["text"] for r in sent(w, "m9", "kind != 'resolved'") if r["ikey"] == "failed-round"]
    assert any(f"{a}, {b}" in t for t in texts), texts
    assert open_keys(w, "m9") == {} and sent(w, "m9")[-1]["kind"] == "resolved"
    n = len(sent(w, "m9"))
    w = restart(tmp_path, w, oc)
    tick(w, oc, 4, seconds=60)
    assert len(sent(w, "m9")) == n


# ---- repair 3, horizon (coordinator ruling A): the clock never moves the horizon past a block that exists above the
# checkpoint; a total chain halt with confirmations > 0 then keeps the clock rules waiting (accepted limitation)


def test_r3_commit_seconds_before_the_deadline_in_an_unconfirmed_block_is_not_a_failed_round(tmp_path):
    """Class 4 (window anchored past the stored data): round X commits ~29.6 min after its first vote, in a block
    still above the checkpoint (confirmations 2) when missed slots let the clock pass X's 30 min. The horizon stops
    before that block: no failed-round page (before: a false page, resolved once the commit was confirmed)."""
    oc, w = oracle_worker(tmp_path, confirmations=2)
    tick(w, oc, 3)
    x = oc.c.number - 1
    oc.vote(x, 1)
    tick(w, oc, TIMEOUT_BLOCKS - 4)
    oc.vote(x, 2)
    oc.vote(x, 4)  # the commit, inside the 30 min by chain time
    assert oc.committed(x) and oc.c.ts(oc.c.sealed) <= oc.c.ts(oc.c.sealed - TIMEOUT_BLOCKS + 2) + 1800
    tick(w, seconds=150)  # missed slots: no block for 150 s, the clock passes X's deadline
    tick(w, oc, 3)  # blocks again: the commit is confirmed
    assert [r for r in sent(w, "m9") if r["ikey"] == "failed-round"] == [] and open_keys(w, "m9") == {}


def test_r3_seed_295_late_confirmed_votes_do_not_retract_an_attribution(tmp_path):
    """Sweep seed 295 (confirmations 3): votes of round R in blocks above the checkpoint while the chain stalls. The
    horizon no longer settles R by the clock before those votes are stored, so no dissent is attributed on a partial
    vote set and later retracted ('Condition no longer observed')."""
    import test_m9_m10_m11_sweep as sweep

    sweep.Sweep(tmp_path, 295, 3).run(20)


def test_r3_chain_halt_with_confirmations_waits_for_the_tail_control(tmp_path):
    """The accepted limitation, pinned: confirmations 2, the chain halts with two unconfirmed blocks; 9.5 h of clock
    do not open the cadence page (a block above the checkpoint may hold a RootCommitted). With confirmations 0 the
    clock still fires it (V2b.7, `test_no_root_committed_for_9h_pages_from_the_clock_without_any_event`)."""
    oc, w = oracle_worker(tmp_path, confirmations=2)
    tick(w, oc, 3)
    oc.round()
    tick(w, oc, 5)
    tick(w, seconds=9.5 * HOUR)
    assert open_keys(w, "m9") == {}


def test_r3_reorg_removing_the_commit_that_settled_a_dissent_resolves_with_the_reorg_text(tmp_path):
    """Found by the repair-3 wider sweep (seed 123): a dissent is attributed when its round settles, here by a later
    round's commit that supersedes it (every vote of the round is older). A reorg beyond the confirmation window
    removes that commit only: the round is open again (inside its 30 min) and the dissent's evidence, its
    settlement, left the chain: resolved with the reorg text, not as "no longer observed"."""
    oc, w = oracle_worker(tmp_path, confirmations=2)
    tick(w)
    ref = oc.c.number - 1
    oc.vote(ref, 3, variant=1)
    oc.vote(ref, 1)
    oc.vote(ref, 2)  # two slots for A, slot 3 for B: no quorum
    oc.c.mine(2)
    fork = oc.c.number
    later = oc.round(voters=(1, 2, 4))  # a later blockNum commits: the round is settled (plurality A)
    tick(w, oc, 3)
    assert open_keys(w, "m9") == {"dissent:3": "warn"}, open_keys(w, "m9")
    oc.reorg(fork, latest=0, refs={later})
    oc.c.mine(4)
    tick(w, oc, 2)
    res = [r["text"] for r in sent(w, "m9", "kind='resolved'") if r["ikey"] == "dissent:3"]
    assert "dissent:3" not in open_keys(w, "m9") and len(res) == 1 and REORG_TEXT in res[0], (res, open_keys(w, "m9"))


def test_r3_qa3_control_cadence_page_resolved_by_a_reorg_that_adds_the_commit_is_not_reopened(tmp_path):
    """QA attempt-3 control, ADAPTED to the horizon ruling (repair 3): the 9.5 h gap is produced in chain time (blocks
    without a RootCommitted) instead of a clock-only halt with two unconfirmed blocks (which now waits, the accepted
    limitation). A deep reorg brings a chain with a RootCommitted inside the gap: the page resolves and is not re-sent
    across a restart."""
    oc, w = oracle_worker(tmp_path, confirmations=2)
    tick(w)
    oc.round()
    tick(w, oc, 5)
    oc.c.elapse(2860)
    tick(w, oc, 3)
    assert open_keys(w, "m9") == {"cadence": "page"}
    fork = oc.c.number - 1
    oc.reorg(fork, latest=oc.latest)
    oc.c.mine(3)
    oc.round()
    tick(w, oc, 5)
    assert open_keys(w, "m9") == {}, open_keys(w, "m9")
    n = len(sent(w, "m9"))
    w = restart(tmp_path, w, oc)
    tick(w, oc, 5, seconds=60)
    assert len(sent(w, "m9")) == n and open_keys(w, "m9") == {}


# ---------------------------------------------------------------- repair 4: owner D14 switch checks.m9.dissent_enabled


def _health(w, part):
    return [r[0] for r in w.db.conn.execute("SELECT text FROM outbox WHERE severity='health' AND text LIKE ?",
                                            (f"%{part}%",))]


def test_r4_dissent_enabled_default_on_is_unchanged(tmp_path):
    oc, w = oracle_worker(tmp_path, checks={"m9": {"dissent_enabled": True}})
    tick(w)
    oc.round(variants={3: 1})
    tick(w, oc, 1)
    assert open_keys(w, "m9") == {"dissent:3": "warn"} and not _health(w, "dissent_enabled")


def test_r4_dissent_disabled_is_silent_and_says_so(tmp_path):
    oc, w = oracle_worker(tmp_path, checks={"m9": {"dissent_enabled": False}})
    tick(w)
    for _ in range(3):
        oc.round(variants={3: 1})
        tick(w, oc, 1)
    assert open_keys(w, "m9") == {} and pages_warns(w) == []
    assert _health(w, "checks.m9.dissent_enabled false")


def test_r4_dissent_disabled_with_an_open_incident_resolves_it_once(tmp_path):
    oc, w = oracle_worker(tmp_path)
    tick(w)
    for _ in range(3):
        oc.round(variants={3: 1})
        tick(w, oc, 1)
    assert open_keys(w, "m9") == {"dissent:3": "page"}
    n = len(sent(w, "m9"))
    w = restart(tmp_path, w, oc, checks={"m9": {"dissent_enabled": False}})
    tick(w, oc, 2, seconds=60)
    new = sent(w, "m9")[n:]
    assert open_keys(w, "m9") == {} and [r["kind"] for r in new] == ["resolved"], new
    assert "Rule disabled by config" in new[0]["text"]
    oc.round(variants={3: 1})
    tick(w, oc, 2, seconds=60)
    w = restart(tmp_path, w, oc, checks={"m9": {"dissent_enabled": False}})
    tick(w, oc, 2, seconds=60)
    assert len(sent(w, "m9")) == n + 1
