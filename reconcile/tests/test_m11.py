"""M11 — EB updater liveness (spec §3 M11): each trigger line at its severity with a benign twin, the proof-API
escalation, the weekly full-state comparison, thresholds_from_chain (updater = live slot-4 key), restart,
rpc_budget (350 clusters), reorg (depth 1, depth N, crash between detection and handling) and inputs that must
never read as clean (RPC without a transaction, proof API down, inbox unavailable)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fixtures_oracle import SLOTS, UpdaterWorld, incidents, open_keys, restart, sent, tick

pytestmark = pytest.mark.m11

REORG_TEXT = "Evidence left the canonical chain"


def pw(w):
    return [(r["ikey"], r["severity"], r["kind"]) for r in sent(w, "m11", "severity IN ('page','warn')")]


def world(tmp_path, **kw):
    u = UpdaterWorld(tmp_path, **kw)
    tick(u.w)
    return u


# ---------------------------------------------------------------- V2x.1: zero updates within 60 min (warn)


def test_zero_updates_within_60_min_warns(tmp_path):
    u = world(tmp_path)
    ref = u.round(updates=())
    tick(u.w, u.oc, 250)
    assert open_keys(u.w, "m11") == {}  # window still open
    u.close()
    assert open_keys(u.w, "m11") == {"liveness": "warn"}
    inc = incidents(u.w, "m11")[0]
    assert str(ref) in inc["body"] and "all 3 checked clusters match their leaf" in inc["body"]


def test_benign_round_with_updater_txs_is_silent(tmp_path):
    u = world(tmp_path)
    u.round(updates=(0, 1))
    u.close()
    assert pw(u.w) == []


def test_updates_after_the_window_do_not_count(tmp_path):
    u = world(tmp_path)
    u.round(updates=())
    u.c.mine(310)
    u.update(0)  # 62 min after the commit
    u.close(0)
    assert open_keys(u.w, "m11") == {"liveness": "warn"}


def test_updates_from_another_sender_do_not_count(tmp_path):
    u = world(tmp_path)
    u.round(updates=(0,), sender=SLOTS[3])  # Ethernodes covering
    u.close()
    assert open_keys(u.w, "m11") == {"liveness": "warn"}
    assert "Other senders updated" in incidents(u.w, "m11")[0]["body"]


# ---------------------------------------------------------------- V2x.1: ... + proof API mismatches (page)


def test_zero_updates_with_leaf_mismatches_pages(tmp_path):
    u = world(tmp_path, checks={"full_state_interval_s": 10**9})
    tick(u.w, u.oc, 1)
    u.round(updates=(0,))
    u.close()  # first full-state comparison runs here (all match)
    u.set_eb(32, leaves={u.cids[1]: 64})  # the leaf of cluster 1 moved; nobody updates it
    u.round(updates=())
    u.close()
    assert open_keys(u.w, "m11") == {"liveness": "page"}
    inc = incidents(u.w, "m11")[-1]
    assert "1 of 3 compared clusters differ" in inc["body"] and u.cids[1][:12] in inc["body"]


def test_benign_zero_updates_with_every_leaf_matching_stays_warn(tmp_path):
    u = world(tmp_path)
    u.round(updates=())
    u.close()
    assert [s for _, s, _ in pw(u.w)] == ["warn"]


def test_zero_updates_with_the_proof_api_down_stays_warn_and_says_so(tmp_path):
    u = world(tmp_path)
    u.round(updates=(0,))
    u.close()
    u.api.down = True
    u.round(updates=())
    u.close()
    assert open_keys(u.w, "m11") == {"liveness": "warn"}
    assert "Proof API check not possible" in incidents(u.w, "m11")[-1]["body"]
    health = u.w.db.conn.execute("SELECT text FROM outbox WHERE key LIKE 'health:proof-api%'").fetchall()
    assert health and "unavailable" in health[0][0]
    u.api.down = False
    u.set_eb(32, leaves={u.cids[0]: 99})
    tick(u.w, u.oc, 1, seconds=300)  # retried: the API answers now and shows a mismatch
    assert open_keys(u.w, "m11")["liveness"] == "page"


# ---------------------------------------------------------------- V2x.1: two consecutive zero rounds (page)


def test_two_consecutive_zero_rounds_page(tmp_path):
    u = world(tmp_path)
    u.round(updates=())
    u.close()
    u.round(updates=())
    u.close()
    assert open_keys(u.w, "m11") == {"liveness": "page"}
    assert [kind for _, _, kind in pw(u.w)] == ["open", "escalate"]
    u.round(updates=(2,))
    u.close()
    assert open_keys(u.w, "m11") == {}
    assert "sent 1 update(s)" in sent(u.w, "m11", "kind='resolved'")[0]["text"]


def test_benign_zero_round_between_good_rounds_never_pages(tmp_path):
    u = world(tmp_path)
    for updates in [(0,), (), (1,), (), (2,)]:
        u.round(updates=updates)
        u.close()
    assert [s for _, s, _ in pw(u.w)] == ["warn", "warn"]
    assert open_keys(u.w, "m11") == {}


# ---------------------------------------------------------------- V2x.1: revert rate > 10 % (warn), M1 handoff


def test_revert_rate_above_10_percent_warns_and_hands_incorrect_cluster_state_to_m1(tmp_path):
    u = world(tmp_path)
    ref = u.round(updates=(0, 1))
    u.fail("MustUseLatestRoot", ref, i=2)
    tx = u.fail("IncorrectClusterState", ref, i=2)
    u.close()
    assert open_keys(u.w, "m11") == {"revert-rate": "warn"}
    inc = incidents(u.w, "m11")[0]
    assert "2 of 4" in inc["body"] and "go to M1" in inc["body"] and tx in inc["evidence"]
    assert f'"m1_handoff": ["{tx}"]' in inc["evidence"]


def test_benign_revert_rate_at_10_percent_is_silent(tmp_path):
    u = world(tmp_path, n=10)
    ref = u.round(updates=tuple(range(9)))
    u.fail("MustUseLatestRoot", ref, i=9)
    u.close()
    assert pw(u.w) == []


def test_benign_stale_update_after_another_sender_set_the_same_eb_is_excluded(tmp_path):
    u = world(tmp_path)
    ref = u.round(updates=(0,))
    st = u.ssv.clusters[bytes.fromhex(u.cids[1][2:])]
    cl = u.ssv._cluster(st)
    u.update(1, eb=40, sender=SLOTS[3])  # Ethernodes set cluster 1 to 40 for this root first
    u.fail("StaleUpdate", ref, i=1, eb=40)  # ... so our identical update reverts StaleUpdate
    assert cl is not None
    u.close()
    assert pw(u.w) == []


def test_stale_update_without_another_senders_update_counts(tmp_path):
    u = world(tmp_path)
    ref = u.round(updates=(0,))
    u.fail("StaleUpdate", ref, i=1, eb=40)
    u.close()
    assert open_keys(u.w, "m11") == {"revert-rate": "warn"}


def test_revert_rate_resolves_at_the_next_clean_round(tmp_path):
    u = world(tmp_path)
    ref = u.round(updates=(0,))
    u.fail("MustUseLatestRoot", ref, i=1)
    u.close()
    u.round(updates=(0, 1))
    u.close()
    assert open_keys(u.w, "m11") == {}
    assert "under 10%" in sent(u.w, "m11", "kind='resolved'")[0]["text"]


def test_inbox_unavailable_keeps_the_revert_rate_incident(tmp_path):
    u = world(tmp_path)
    ref = u.round(updates=(0,))
    u.fail("MustUseLatestRoot", ref, i=1)
    u.close()
    u.inbox.unlink()  # the Monitor's file is gone: later reverts cannot be seen
    tick(u.w, u.oc, 1)
    u.round(updates=(0, 1))
    u.close()
    assert open_keys(u.w, "m11") == {"revert-rate": "warn"}  # unknown, not clean
    u.inbox.write_text("")
    tick(u.w, u.oc, 1)
    tick(u.w, u.oc, 1, seconds=300)
    assert open_keys(u.w, "m11") == {}


# ---------------------------------------------------------------- V2x.1: weekly full-state comparison (warn)


def mismatch_older_than_one_round(u, cluster=2, leaf=48):
    """The weekly comparison at round 1 finds the cluster off its leaf; the updater leaves it through round 2."""
    u.round(updates=(0,))
    u.set_eb(32, leaves={u.cids[cluster]: leaf})
    u.close()
    assert "state-mismatch" not in open_keys(u.w, "m11")  # one round old: not yet
    u.round(updates=(0,))
    u.set_eb(32, leaves={u.cids[cluster]: leaf})
    u.close()


def test_weekly_full_state_mismatch_warns(tmp_path):
    u = world(tmp_path)
    mismatch_older_than_one_round(u)
    assert open_keys(u.w, "m11") == {"state-mismatch": "warn"}
    inc = incidents(u.w, "m11")[0]
    assert u.cids[2] in inc["evidence"] and "previous evaluated root" in inc["body"]
    u.set_eb(32)
    tick(u.w, u.oc, 1, seconds=3 * 86400)
    assert open_keys(u.w, "m11") == {"state-mismatch": "warn"}  # no new root, no weekly run: not compared again
    tick(u.w, u.oc, 1, seconds=4 * 86400)  # the weekly comparison: the reported cluster matches now
    assert open_keys(u.w, "m11") == {}
    assert "every reported cluster (1) was compared again" in sent(u.w, "m11", "kind='resolved'")[0]["text"]


def test_a_mismatch_fixed_by_the_next_round_never_warns(tmp_path):
    """QA cp2b-1 F6 twin: the mismatch found at one root is gone at the next (the updater fixed it): not older than
    one round, silent; the follow-up compares only that cluster."""
    u = world(tmp_path)
    u.round(updates=(0,))
    u.set_eb(32, leaves={u.cids[2]: 48})
    u.close()
    calls = u.api.calls
    u.round(updates=(0,))
    u.set_eb(32)
    u.close()
    assert pw(u.w) == [] and u.api.calls == calls + 1
    assert u.w.db.meta("check:m11:state_evals")[-1]["scope"] == [u.cids[2]]


def test_benign_weekly_full_state_all_match_is_silent(tmp_path):
    u = world(tmp_path)
    u.round(updates=(0,))
    u.close()
    assert pw(u.w) == [] and u.w.db.meta("check:m11:state_evals")[-1]["checked"] == 3


def test_weekly_full_state_deferred_while_the_api_is_unreachable(tmp_path):
    u = world(tmp_path)
    mismatch_older_than_one_round(u)
    u.api.down = True
    u.set_eb(32)
    tick(u.w, u.oc, 1, seconds=8 * 86400)
    assert open_keys(u.w, "m11") == {"state-mismatch": "warn"}  # DEFERRED, never clean
    assert u.w.db.conn.execute("SELECT text FROM outbox WHERE key LIKE 'health:proof-api-weekly%'").fetchone()[0]


def test_leaves_at_another_reference_block_are_not_compared(tmp_path):
    u = world(tmp_path)
    u.round(updates=(0,))
    u.api.ref = 1  # the API answers for an older root
    u.close()
    assert pw(u.w) == []
    assert not u.w.db.meta("check:m11:state_evals")  # no leaf at the right root: DEFERRED, not stored as "all match"


def test_cp3_lagging_api_is_probed_once_per_retry_interval_and_compared_once_it_caught_up(tmp_path):
    """CP3 ruling (local mainnet run: the proof API served the previous root hours after the commit, and every M11 run
    re-asked all 419 clusters): an undone weekly comparison is retried once per `full_state_retry_s`, each retry asks
    one cluster first and stops when the API answers for another root. DEFERRED, never "all match"."""
    u = world(tmp_path, n=5)
    u.round(updates=(0,))
    u.api.ref = 1  # the API still answers for an older root (behind the chain)
    calls = u.api.calls
    u.close()
    assert u.api.calls == calls + 1 and not u.w.db.meta("check:m11:state_evals")  # one probe, DEFERRED
    text = u.w.db.conn.execute("SELECT text FROM outbox WHERE key LIKE 'health:proof-api-weekly%'").fetchone()[0]
    assert "DEFERRED" in text and "retried every 1800 s" in text  # QA CP3 attempt 1 F3: the real retry interval
    for _ in range(5):  # M11 runs every 300 s; inside the 1800 s retry interval nothing is asked
        tick(u.w, u.oc, 1, seconds=300)
    assert u.api.calls == calls + 1
    tick(u.w, u.oc, 1, seconds=300)  # the retry interval passed: one more probe, still behind
    assert u.api.calls == calls + 2 and not u.w.db.meta("check:m11:state_evals")
    u.api.ref = u.w.db.meta("check:m11:state_full_tried")["ref"]  # the API caught up with the chain's root
    tick(u.w, u.oc, 1, seconds=1800)
    assert u.api.calls == calls + 2 + 5  # the full comparison ran once: every cluster
    assert u.w.db.meta("check:m11:state_evals")[-1]["checked"] == 5 and not u.w.db.meta("check:m11:state_full_tried")
    tick(u.w, u.oc, 1, seconds=1800)
    assert u.api.calls == calls + 7  # weekly: not again before full_state_interval_s
    assert pw(u.w) == []


# ---------------------------------------------------------------- V2x.2 thresholds_from_chain


def test_thresholds_from_chain_updater_is_the_live_slot_4_key(tmp_path):
    u = world(tmp_path)
    u.round(updates=(0,))
    u.close()
    new = "0x" + "4d" * 20
    u.oc.replace(4, new)  # OracleReplaced: the framework re-reads getOracle, M11's prepare follows
    u.round(updates=(1,))  # still sent by the old slot-4 key
    u.close()
    assert open_keys(u.w, "m11") == {"liveness": "warn"}
    u.round(updates=(2,), sender=new)
    u.close()
    assert open_keys(u.w, "m11") == {}


def test_config_updater_overrides_the_slot(tmp_path):
    other = "0x" + "4c" * 20
    u = world(tmp_path, checks={"updater": other})
    u.round(updates=(0,))
    u.close()
    assert open_keys(u.w, "m11") == {"liveness": "warn"}


# ---------------------------------------------------------------- V2x.3 restart


def test_restart_does_not_duplicate_the_liveness_warning(tmp_path):
    u = world(tmp_path)
    u.round(updates=())
    u.close()
    before = sent(u.w, "m11")
    w2 = u.restart()
    tick(w2)
    tick(w2, u.oc, 2, seconds=600)
    assert sent(w2, "m11") == before and len(before) == 1


def test_restart_with_prior_zero_round_escalates_on_the_second(tmp_path):
    u = world(tmp_path)
    u.round(updates=())
    u.close()
    u.w = u.restart()
    u.round(updates=())
    u.close()
    assert [kind for _, _, kind in pw(u.w)] == ["open", "escalate"]


# ---------------------------------------------------------------- V2x.4 rpc_budget


def test_rpc_budget_350_clusters(tmp_path):
    u = world(tmp_path, n=350)
    run = lambda: u.w.db.meta("run:m11")["rpc_calls"]  # noqa: E731
    u.round(updates=tuple(range(40)))
    u.close()  # 40 sender lookups once + the first full-state comparison (one eth_call per cluster)
    first = run()
    assert first.get("eth_getTransactionByHash", 0) == 40
    assert first.get("eth_call", 0) <= 350
    tick(u.w, u.oc, 1, seconds=300)
    assert run().get("eth_call", 0) == 0 and run().get("eth_getTransactionByHash", 0) == 0  # cached, not due
    u.round(updates=())
    u.close()  # zero round: the proof comparison, bounded by the inventory
    zero = run()
    assert 0 < zero.get("eth_call", 0) <= 350
    tick(u.w, u.oc, 1, seconds=300)
    assert run().get("eth_call", 0) == 0  # the zero-round result is persisted, not recomputed


# ---------------------------------------------------------------- reorg


def test_reorg_depth_1_adding_updates_resolves_the_zero_round(tmp_path):
    u = world(tmp_path)
    ref = u.round(updates=())
    fork = u.c.number
    u.close()
    assert open_keys(u.w, "m11") == {"liveness": "warn"}
    u.c.reorg(fork)
    u.ssv.clusters[bytes.fromhex(u.cids[0][2:])]["root"] = 0
    u.update(0)  # on the new chain the updater did send for this root
    assert u.ssv.latest_root == ref
    u.close(2)
    assert open_keys(u.w, "m11") == {}
    assert REORG_TEXT in sent(u.w, "m11", "kind='resolved'")[0]["text"]
    assert u.w.db.conn.execute("SELECT MAX(block) FROM m11_senders").fetchone()[0] >= fork


@pytest.mark.parametrize("confirmations", [2, 12])
def test_reorg_depth_n_moving_the_commit_invalidates_cached_senders_and_proof(tmp_path, confirmations):
    u = world(tmp_path, confirmations=confirmations)
    tick(u.w, u.oc, confirmations + 2)
    fork = u.c.number
    u.round(updates=(0,))
    u.close(confirmations + 5)
    assert u.w.db.conn.execute("SELECT COUNT(*) FROM m11_senders").fetchone()[0] == 1
    saved = u.w.db.meta("check:m11:state_evals")[-1]
    assert saved["commit_block"] >= fork
    u.c.reorg(fork)  # depth > confirmations: the commit and its update leave the chain
    u.ssv.latest_root = 0
    for st in u.ssv.clusters.values():
        st["root"] = 0
    u.c.mine(10)
    u.round(updates=())  # new chain: same kind of round, but no update
    u.close(confirmations + 5)
    assert open_keys(u.w, "m11") == {"liveness": "warn"}
    assert u.w.db.conn.execute("SELECT COUNT(*) FROM m11_senders WHERE block>=?", (fork,)).fetchone()[0] == 0
    assert u.w.db.meta("check:m11:state_evals")[-1]["commit_block"] != saved["commit_block"]


def test_reorg_below_the_evaluated_block_recomputes_persisted_proof_results(tmp_path):
    u = world(tmp_path)
    u.round(updates=())
    u.set_eb(32, leaves={u.cids[0]: 50})
    u.close()
    assert open_keys(u.w, "m11") == {"liveness": "page"}  # the weekly mismatch is one round old: not reported
    saved = u.w.db.meta("check:m11:zero_proof")
    calls = u.api.calls
    fork = u.c.number - 2  # above the commit and the window end, below the evaluated block
    assert fork > saved["commit_block"] + 300 and fork <= saved["block"]
    u.c.reorg(fork)
    u.set_eb(32)  # on the new chain every leaf matches
    u.c.mine(3)
    tick(u.w)
    again = u.w.db.meta("check:m11:zero_proof")
    assert again["block"] != saved["block"] and u.api.calls > calls  # recomputed, not reused from deleted blocks
    # README §6.2: re-reported at the lower severity the new chain supports (recorded silently); the weekly
    # comparison was also recomputed on the new chain
    assert again["result"]["mismatches"] == [] and open_keys(u.w, "m11") == {"liveness": "warn"}
    evals = u.w.db.meta("check:m11:state_evals")
    assert evals[-1]["block"] > fork and evals[-1]["mismatches"] == []


def test_reorg_crash_between_detection_and_handling(tmp_path):
    u = world(tmp_path)
    u.round(updates=())
    fork = u.c.number
    u.close()
    u.c.reorg(fork)
    u.ssv.clusters[bytes.fromhex(u.cids[1][2:])]["root"] = 0
    u.update(1)
    u.c.mine(305)
    u.w.check_reorg()
    saved = SimpleNamespace(clock=u.w.clock, cfg=u.w.cfg)
    del u.w
    w2 = u.restart(saved)
    tick(w2)
    assert open_keys(w2, "m11") == {}
    assert len(sent(w2, "m11", "kind='resolved'")) == 1


# ---------------------------------------------------------------- inputs that are never clean


def test_a_transaction_the_rpc_cannot_return_fails_the_run(tmp_path):
    u = world(tmp_path)
    u.round(updates=(0,))
    tx = max(u.c.senders)
    del u.c.senders[tx]  # eth_getTransactionByHash -> null
    u.close()
    run = u.w.db.meta("run:m11")
    assert run["errors"] >= 1 and "returned no transaction" in run["last_error"]
    assert open_keys(u.w, "m11") == {}  # nothing concluded from a missing input


def test_missing_updater_source_is_a_startup_error(tmp_path):
    from harness import make_worker

    with pytest.raises(SystemExit, match="checks.m11"):
        make_worker(tmp_path, only=["m11"])


def test_empty_get_effective_balance_answer_fails_the_run(tmp_path):
    from fixtures_oracle import VIEWS

    from checks.m11_updater_liveness import GET_EB

    u = world(tmp_path)
    u.c.stub_call(VIEWS, GET_EB, b"")  # a node answering "0x" for the view
    u.round(updates=())
    u.close()
    run = u.w.db.meta("run:m11")
    assert run["errors"] >= 1 and "returned 0 bytes" in run["last_error"]
    assert open_keys(u.w, "m11") == {}


def test_empty_inventory_is_deferred_not_clean(tmp_path):
    u = world(tmp_path, n=0)
    u.round(updates=())
    u.close()
    assert open_keys(u.w, "m11") == {"liveness": "warn"}
    assert "no active ETH cluster" in incidents(u.w, "m11")[0]["body"]
    assert not u.w.db.meta("check:m11:state_evals")
    assert u.w.db.conn.execute("SELECT 1 FROM outbox WHERE key LIKE 'health:proof-api-weekly%'").fetchone()


def test_missing_inbox_is_a_startup_error_unless_opted_out(tmp_path):
    from fixtures_oracle import VIEWS
    from harness import make_worker

    with pytest.raises(SystemExit, match="checks.m11: the revert-rate rule needs the failed-transaction inbox"):
        make_worker(tmp_path, only=["m11"], views=VIEWS)
    make_worker(tmp_path, only=["m11"], views=VIEWS, checks={"m11": {"require_inbox": False}})


# ---------------------------------------------------------------- QA cp2b attempt 1 reproducers (permanent)


def test_qa1_f1_partial_proof_answer_is_unknown_and_retried(tmp_path):
    """F1: three active clusters, the API answers two leaves and `cluster not found` for the third: UNKNOWN (never
    "all match"), persisted as such and retried every run; the retry finds the mismatch and pages."""
    u = UpdaterWorld(tmp_path)
    tick(u.w)
    u.round(updates=())
    del u.api.leaves[u.cids[1]]
    u.close()
    saved = u.w.db.meta("check:m11:zero_proof")
    assert saved["result"]["unknown"]
    inc = incidents(u.w, "m11")[0]
    assert "UNKNOWN" in inc["body"] and "all 2 checked clusters match" not in inc["body"]
    calls = u.api.calls
    u.api.leaves[u.cids[1]] = 99
    tick(u.w, u.oc, 1, seconds=300)
    assert u.api.calls > calls and open_keys(u.w, "m11").get("liveness") == "page", (u.api.calls, calls)
    assert u.api.calls == calls + 1  # only the unknown cluster is asked again (once: the weekly shares the answer)


def test_qa1_f1_partial_proof_answer_in_a_reorg_run_keeps_the_reported_mismatch(tmp_path):
    """F1 (reorg), set up so the mismatch is reported under F6 (older than one round): reorg below the evaluation
    that reported it, keeping the commit; the API has no proof for the affected cluster on the new chain: the key
    is re-reported (unknown), never resolved by the clusters that were compared."""
    u = UpdaterWorld(tmp_path)
    tick(u.w)
    mismatch_older_than_one_round(u, cluster=1, leaf=64)
    assert open_keys(u.w, "m11").get("state-mismatch") == "warn"
    fork = u.c.number - 2
    u.c.reorg(fork)
    u.set_eb(32)
    del u.api.leaves[u.cids[1]]
    u.c.mine(3)
    tick(u.w)
    assert "state-mismatch" in open_keys(u.w, "m11") or u.w.db.meta("reorg:m11"), sent(u.w, "m11")
    assert open_keys(u.w, "m11") == {"state-mismatch": "warn"} and not sent(u.w, "m11", "kind='resolved'")
    u.api.leaves[u.cids[1]] = 32  # the proof is back and matches: positive proof
    tick(u.w, u.oc, 1, seconds=300)
    assert open_keys(u.w, "m11") == {}


def test_qa1_f6_latest_round_mismatch_must_age_one_round(tmp_path):
    u = UpdaterWorld(tmp_path)
    tick(u.w)
    u.round(updates=(0,))
    u.set_eb(32, leaves={u.cids[1]: 64})
    u.close()
    assert "state-mismatch" not in open_keys(u.w, "m11"), open_keys(u.w, "m11")


def test_qa1_f2_rotation_then_update_in_the_same_block_uses_the_new_owner(tmp_path):
    u = UpdaterWorld(tmp_path)
    tick(u.w)
    u.commit()
    new = "0x" + "77" * 20
    u.oc.replace(4, new, mine=False)
    u.update(0, sender=new)
    u.close()
    assert "liveness" not in open_keys(u.w, "m11"), open_keys(u.w, "m11")


def test_qa1_f5_a_later_other_sender_update_cannot_explain_an_earlier_revert(tmp_path):
    u = UpdaterWorld(tmp_path)
    tick(u.w)
    ref = u.round(updates=(0,))
    u.fail("StaleUpdate", ref, i=1, eb=40)
    u.update(1, eb=40, sender=SLOTS[3])
    u.close()
    assert open_keys(u.w, "m11").get("revert-rate") == "warn", open_keys(u.w, "m11")


def test_qa1_f8_60_minute_window_closes_without_a_new_block(tmp_path):
    u = UpdaterWorld(tmp_path)
    tick(u.w)
    u.round(updates=())
    tick(u.w)
    tick(u.w, seconds=59 * 60)
    assert open_keys(u.w, "m11") == {}
    tick(u.w, seconds=5 * 60)  # the next interval run (300 s) after the window closed
    assert open_keys(u.w, "m11").get("liveness") == "warn", open_keys(u.w, "m11")


def test_qa1_control_health_only_reorg_with_the_api_down_keeps_the_episode(tmp_path):
    """QA control, set up under F6 (the mismatch is reported once older than one round)."""
    u = UpdaterWorld(tmp_path)
    tick(u.w)
    mismatch_older_than_one_round(u, cluster=1, leaf=64)
    old = open_keys(u.w, "m11")
    assert old == {"state-mismatch": "warn"}
    fork = u.c.number - 2
    u.c.reorg(fork)
    u.api.down = True
    u.c.mine(3)
    tick(u.w)
    assert open_keys(u.w, "m11") == old
    assert not sent(u.w, "m11", "kind='resolved'")
    assert u.w.db.conn.execute("SELECT COUNT(*) FROM m11_episodes WHERE closed=0").fetchone()[0] == 1


# ---------------------------------------------------------------- repair 1: chain time, ordering, partial answers


def test_update_window_is_chain_time_with_missed_slots(tmp_path):
    """61 min after the commit in only 280 blocks (missed slots): outside the window, the round has 0 updates."""
    u = world(tmp_path)
    u.round(updates=())
    u.c.elapse(280, seconds_per_block=13.1)
    u.update(0)
    u.close()
    assert open_keys(u.w, "m11") == {"liveness": "warn"}


def test_update_59_minutes_after_the_commit_in_more_than_300_blocks_counts(tmp_path):
    u = world(tmp_path)
    u.round(updates=())
    commit_ts = u.c.ts(u.c.sealed)
    u.c.mine(320)
    u.c.times[u.c.sealed] = commit_ts + 59 * 60 - 12  # a chain whose 320 blocks span 59 min
    u.update(0)
    u.close()
    assert pw(u.w) == []


def test_a_revert_in_the_rotation_block_counts_for_either_key(tmp_path):
    """A reverted tx carries no transaction index: in the block where slot 4 rotates, either key is the updater."""
    from fixtures_oracle import ERR, PROXY, inbox_line, update_calldata

    from checks.m9_oracle_common import UPDATE_CLUSTER_BALANCE_SELECTOR

    u = world(tmp_path)
    ref = u.round(updates=(0,))
    new = "0x" + "77" * 20
    st = u.ssv.clusters[bytes.fromhex(u.cids[1][2:])]
    data = update_calldata(ref, u.owners[1], (1, 2, 3, 4), u.ssv._cluster(st), 64)
    u.c.stub_call(PROXY, data, revert=ERR["MustUseLatestRoot"])
    u.c.mine()
    u.oc.replace(4, new, mine=False)
    tx = u.c.add_tx(SLOTS[4], PROXY, data)  # the old key, after the rotation, in the same block
    u.c.mine()
    with open(u.inbox, "a") as fh:
        fh.write(inbox_line(u.c, tx, SLOTS[4], UPDATE_CLUSTER_BALANCE_SELECTOR, "updateClusterBalance(uint64,address,"
                            "uint64[],(uint32,uint64,uint64,bool,uint256),uint32,bytes32[])"))
    u.close()
    assert open_keys(u.w, "m11").get("revert-rate") == "warn"


def same_block_stale_update(u, ref, revert_index=None):
    """An updater StaleUpdate revert and another sender's update of the same cluster/EB in one block: the revert
    first (lower transaction index), unless `revert_index` says the node put it later."""
    from fixtures_oracle import ERR, PROXY, inbox_line, update_calldata

    from checks.m9_oracle_common import UPDATE_CLUSTER_BALANCE_SELECTOR

    st = u.ssv.clusters[bytes.fromhex(u.cids[1][2:])]
    data = update_calldata(ref, u.owners[1], (1, 2, 3, 4), u.ssv._cluster(st), 40)
    u.c.stub_call(PROXY, data, revert=ERR["StaleUpdate"])
    u.c.mine()
    tx = u.c.add_tx(SLOTS[4], PROXY, data)
    if revert_index is not None:
        u.c.txs[tx]["transactionIndex"] = hex(revert_index)
    u.update(1, eb=40, sender=SLOTS[3])  # same block, later transaction index; mines it
    with open(u.inbox, "a") as fh:
        fh.write(inbox_line(u.c, tx, SLOTS[4], UPDATE_CLUSTER_BALANCE_SELECTOR, "updateClusterBalance(uint64,address,"
                            "uint64[],(uint32,uint64,uint64,bool,uint256),uint32,bytes32[])"))


def test_same_block_stale_update_before_the_other_senders_update_counts(tmp_path):
    u = world(tmp_path)
    ref = u.round(updates=(0,))
    same_block_stale_update(u, ref)  # no transactionIndex from the node: not provably after the other update
    u.close()
    assert open_keys(u.w, "m11").get("revert-rate") == "warn"


def test_same_block_stale_update_after_the_other_senders_update_is_benign(tmp_path):
    u = world(tmp_path)
    ref = u.round(updates=(0,))
    same_block_stale_update(u, ref, revert_index=99)  # the node says the revert came after the other update
    u.close()
    assert pw(u.w) == []


def test_partial_zero_round_proof_survives_a_restart_and_keeps_retrying(tmp_path):
    u = world(tmp_path)
    u.round(updates=())
    u.api.leaves.pop(u.cids[2])
    u.close()
    assert open_keys(u.w, "m11") == {"liveness": "warn"}
    w2 = u.restart()
    calls = u.api.calls
    tick(w2, u.oc, 1, seconds=300)
    assert u.api.calls == calls + 1 and w2.db.meta("check:m11:zero_proof")["result"]["unknown"]
    u.api.leaves[u.cids[2]] = 32
    tick(w2, u.oc, 1, seconds=300)
    assert not w2.db.meta("check:m11:zero_proof")["result"]["unknown"]
    assert "all 3 checked clusters match" in incidents(w2, "m11")[0]["body"]


def test_a_partial_weekly_answer_never_resolves_a_reported_mismatch(tmp_path):
    u = world(tmp_path)
    mismatch_older_than_one_round(u)
    assert open_keys(u.w, "m11") == {"state-mismatch": "warn"}
    u.set_eb(32)
    u.api.leaves.pop(u.cids[2])  # the reported cluster has no proof; the others match
    u.round(updates=(0,))
    u.api.leaves.pop(u.cids[2], None)
    u.close()
    assert open_keys(u.w, "m11") == {"state-mismatch": "warn"} and not sent(u.w, "m11", "kind='resolved'")
    u.api.leaves[u.cids[2]] = 32
    tick(u.w, u.oc, 1, seconds=300)
    assert open_keys(u.w, "m11") == {}


def test_reorg_orphaning_the_weekly_clearance_reopens_the_mismatch(tmp_path):
    """Class 2: the evaluation that resolved the mismatch leaves the chain (reorg below its evaluated block); the
    evaluations that established it survive and the API is down: the clearance is gone, the mismatch reopens."""
    u = world(tmp_path)
    mismatch_older_than_one_round(u)
    u.round(updates=(0,))
    u.set_eb(32)
    u.close()
    assert open_keys(u.w, "m11") == {} and len(sent(u.w, "m11", "kind='resolved'")) == 1
    fork = u.c.number - 2
    u.c.reorg(fork)
    u.api.down = True
    u.c.mine(3)
    tick(u.w)
    assert open_keys(u.w, "m11") == {"state-mismatch": "warn"}
    u.api.down = False
    tick(u.w, u.oc, 1, seconds=300)
    assert open_keys(u.w, "m11") == {}


def test_an_unvalidated_tuple_is_unknown_not_a_match(tmp_path):
    u = world(tmp_path)
    inv_get = u.w.inventory.get

    def broken(block):
        inv = inv_get(block)
        ci = inv.clusters[u.cids[0]]
        object.__setattr__(ci, "cluster", None) if hasattr(ci, "__dataclass_fields__") else None
        return inv

    u.round(updates=())
    u.w.inventory.get = broken
    u.close()
    res = u.w.db.meta("check:m11:zero_proof")["result"]
    assert [x["cluster"] for x in res["unknown"]] == [u.cids[0]] and res["checked"] == 2


def test_follow_ups_of_a_persisting_mismatch_do_not_restart_the_weekly_comparison(tmp_path):
    """Only the tracked cluster is compared at each later root; the full comparison stays weekly even after the
    evaluation that ran it is trimmed from the kept evaluations (three)."""
    u = world(tmp_path)
    mismatch_older_than_one_round(u)
    for _ in range(4):
        calls = u.api.calls
        u.round(updates=(0,))
        u.set_eb(32, leaves={u.cids[2]: 48})
        u.close()
        assert u.api.calls == calls + 1
    assert open_keys(u.w, "m11") == {"state-mismatch": "warn"}
    assert len(sent(u.w, "m11")) == 1  # the same clusters: no re-send per round


# ---------------------------------------------------------------- repair 1: state-table rows (worker record)


def test_first_sweep_reports_an_ongoing_zero_round_not_a_healed_one(tmp_path):
    u = UpdaterWorld(tmp_path)  # bootstrapped, the check has not run yet: everything below is before its first run
    u.round(updates=())
    u.c.elapse(310)
    u.round(updates=(0,))
    u.c.elapse(310)  # a zero round healed by the next one: history
    u.round(updates=())
    u.c.elapse(310)  # an ongoing zero round
    w2 = restart(tmp_path, u.w, u.oc, ids=("m11",), checks={"m11": u.cfg})
    w2.checks["m11"].http_get = u.api.get
    tick(w2)
    assert open_keys(w2, "m11") == {"liveness": "warn"}
    assert [r["kind"] for r in sent(w2, "m11")] == ["open"]


def test_reorg_orphaning_the_updates_that_resolved_a_zero_round_reopens_it(tmp_path):
    u = world(tmp_path)
    u.round(updates=())
    u.close()
    ref2 = u.commit()
    fork = u.c.number
    u.update(0)
    u.close()
    assert open_keys(u.w, "m11") == {}
    u.c.reorg(fork)  # the update that ended the zero run leaves the chain; the commit stays
    u.ssv.clusters[bytes.fromhex(u.cids[0][2:])]["root"] = 0
    u.oc.stub()
    u.close(2)
    assert u.ssv.latest_root == ref2
    assert open_keys(u.w, "m11") == {"liveness": "page"}  # two zero rounds on the new chain
    assert [r["kind"] for r in sent(u.w, "m11") if r["kind"] == "open"] == ["open", "open"]


def test_a_revert_after_the_window_counts_in_its_round(tmp_path):
    """Repair 4 (QA4-F1): the spec's revert-rate line has no window (the 60 min belong to the liveness line). Until
    repair 3 this test pinned the opposite (a revert 61 min after the commit counted nowhere)."""
    u = world(tmp_path)
    ref = u.round(updates=(0,))
    u.c.elapse(305)
    u.fail("MustUseLatestRoot", ref, i=1)  # 61 min after the commit, before the next root
    u.close()
    assert open_keys(u.w, "m11") == {"revert-rate": "warn"} and "1 of 2" in incidents(u.w, "m11")[0]["body"]


def test_benign_late_revert_of_the_round_at_10_percent_is_silent(tmp_path):
    """Benign twin: nine successful updates of the round, one revert of it 61 min after the commit: 1 of 10 = 10 %."""
    u = world(tmp_path, n=10)
    ref = u.round(updates=tuple(range(9)))
    u.c.elapse(305)
    u.fail("MustUseLatestRoot", ref, i=9)
    u.close()
    assert pw(u.w) == []


def test_an_empty_updater_slot_fails_the_run_loudly(tmp_path):
    from eb_storage import u256

    u = world(tmp_path)
    u.oc.slots[4] = "0x" + "00" * 20
    u.oc.stub()
    u.c.emit_event("QuorumUpdated", [], u256(7500))
    tick(u.w, u.oc, 1)
    health = u.w.db.conn.execute("SELECT text FROM outbox WHERE key LIKE 'health:check-error:%' AND check_id='m11'"
                                 ).fetchone()
    assert health and "oracle slot 4 is empty" in health[0]
    assert u.w.db.meta("run:m11")["errors"] >= 1


def test_unreadable_parameters_keep_the_liveness_incident(tmp_path):
    from fixtures_oracle import VIEWS

    from eb_storage import u256
    from params import GETTERS, selector

    u = world(tmp_path)
    u.round(updates=())
    u.close()
    assert open_keys(u.w, "m11") == {"liveness": "warn"}
    u.c.stub_call(VIEWS, selector(GETTERS["quorum_bps"]), revert="0x")
    u.c.emit_event("QuorumUpdated", [], u256(7500))
    u.round(updates=(0,))  # would heal it, but the parameters cannot be read
    u.close()
    assert open_keys(u.w, "m11") == {"liveness": "warn"}
    u.oc.stub()
    u.c.emit_event("QuorumUpdated", [], u256(7500))
    tick(u.w, u.oc, 1)
    assert open_keys(u.w, "m11") == {}


def test_proof_api_answer_shapes(monkeypatch):
    """http_get_json: a JSON error body (404 cluster not found) is an answer; anything else is ProofApiError."""
    import io
    import urllib.error
    import urllib.request

    from checks.m11_updater_liveness import ProofApiError, http_get_json

    def raising(body, code=404):
        def urlopen(req, timeout):
            raise urllib.error.HTTPError(req.full_url, code, "x", {}, io.BytesIO(body))
        return urlopen

    monkeypatch.setattr(urllib.request, "urlopen", raising(b'{"error": "cluster not found"}'))
    assert http_get_json("https://x.invalid/proof/1", 1) == {"error": "cluster not found"}
    for body in (b"<html>bad gateway</html>", b"[1, 2]"):
        monkeypatch.setattr(urllib.request, "urlopen", raising(body, 502))
        with pytest.raises(ProofApiError, match="HTTP 502"):
            http_get_json("https://x.invalid/proof/1", 1)

    def refused(req, timeout):
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(urllib.request, "urlopen", refused)
    with pytest.raises(ProofApiError, match="URLError"):
        http_get_json("https://x.invalid/proof/1", 1)


def test_weekly_tracking_survives_a_restart(tmp_path):
    u = world(tmp_path)
    u.round(updates=(0,))
    u.set_eb(32, leaves={u.cids[2]: 48})
    u.close()
    u.w = u.restart()
    calls = u.api.calls
    u.round(updates=(0,))
    u.set_eb(32, leaves={u.cids[2]: 48})
    u.close()
    assert open_keys(u.w, "m11") == {"state-mismatch": "warn"} and u.api.calls == calls + 1


# ---------------------------------------------------------------- repair 2: QA attempt 2 (qa/cp2b-attempt-2.md)


def dying_api(u, answers=1, error="TimeoutError: timed out"):
    """The proof API answers `answers` requests, then every request fails with a transport error."""
    from checks.m11_updater_liveness import ProofApiError

    calls = {"n": 0}
    real = u.api.get

    def flaky(url, timeout):
        calls["n"] += 1
        if calls["n"] > answers:
            raise ProofApiError(error)
        return real(url, timeout)

    u.w.checks["m11"].http_get = flaky
    return calls


def test_qa2_f2_proof_api_dying_mid_comparison_is_bounded(tmp_path):
    """QA2-F2 reproducer: the API answers once, then times out for every request: `proof_api_abort_after` (3)
    consecutive transport failures make it unreachable for the run, also after a leaf was received; the zero-round
    proof and the weekly comparison of the same run share that verdict (1 + 3 GETs for 20 clusters)."""
    u = UpdaterWorld(tmp_path, n=20)
    tick(u.w)
    calls = dying_api(u)
    u.round(updates=())
    u.close()
    assert open_keys(u.w, "m11").get("liveness") == "warn"
    assert calls["n"] == 1 + 3, f"{calls['n']} proof GETs for 20 clusters after the API died"
    proof = u.w.db.meta("check:m11:zero_proof")["result"]
    assert proof["checked"] == 1 and len(proof["unknown"]) == 19  # nothing dropped: UNKNOWN, retried
    assert sum("not asked" in x["reason"] for x in proof["unknown"]) == 16
    assert u.w.db.conn.execute("SELECT 1 FROM outbox WHERE key LIKE 'health:proof-api-partial%'").fetchone()
    tick(u.w, u.oc, 1, seconds=300)  # next run: at most abort_after more GETs while the API is still dead
    assert calls["n"] == 1 + 3 + 3
    u.w.checks["m11"].http_get = u.api.get  # the API is back: every unknown cluster is compared
    tick(u.w, u.oc, 1, seconds=300)
    assert not u.w.db.meta("check:m11:zero_proof")["result"]["unknown"]


def test_qa2_f2_a_mismatching_leaf_received_before_the_api_died_still_pages(tmp_path):
    """The abort keeps every answer in hand: a leaf that differs from the chain, received before the API died,
    escalates the zero round to a page (the unknown rest is retried)."""
    u = UpdaterWorld(tmp_path, n=10)
    tick(u.w)
    dying_api(u, answers=2)
    u.round(updates=())
    u.set_eb(32, leaves={min(u.cids): 64})  # the first cluster asked (inventory order)
    u.close()
    assert open_keys(u.w, "m11") == {"liveness": "page"}
    assert "not comparable yet" in incidents(u.w, "m11")[0]["body"]


def test_qa2_f2_slow_api_is_bounded_by_the_run_budget(tmp_path):
    """Bounded run time: an API that answers, but slowly (each answer 100 s of wall time here), stops being asked
    once the run has waited `proof_api_run_budget_s`; the clusters not asked are UNKNOWN and asked in later runs."""
    u = UpdaterWorld(tmp_path, n=20, checks={"proof_api_run_budget_s": 450})
    tick(u.w)
    chk = u.w.checks["m11"]
    wall = {"t": 0.0}
    chk.monotonic = lambda: wall["t"]
    real = u.api.get

    def slow(url, timeout):
        wall["t"] += 100
        return real(url, timeout)

    chk.http_get = slow
    u.round(updates=())
    u.close()
    assert u.api.calls == 5  # 5 x 100 s >= 450 s: the run stops asking
    proof = u.w.db.meta("check:m11:zero_proof")["result"]
    assert proof["checked"] == 5 and len(proof["unknown"]) == 15
    assert all("run budget" in x["reason"] for x in proof["unknown"])
    for _ in range(3):
        tick(u.w, u.oc, 1, seconds=300)
    assert not u.w.db.meta("check:m11:zero_proof")["result"]["unknown"] and u.api.calls == 20
    assert open_keys(u.w, "m11") == {"liveness": "warn"}


def test_qa2_f2_api_dead_from_the_start_stays_an_error_not_clean(tmp_path):
    """Control (class 1): no answer at all is the error path (warn, says so), never 'all match'."""
    u = UpdaterWorld(tmp_path, n=10)
    tick(u.w)
    calls = dying_api(u, answers=0)
    u.round(updates=())
    u.close()
    assert open_keys(u.w, "m11") == {"liveness": "warn"} and calls["n"] == 3
    assert "Proof API check not possible" in incidents(u.w, "m11")[0]["body"]


def test_qa2_f3_reorg_removing_the_round_that_aged_a_mismatch(tmp_path):
    """QA2-F3 reproducer: the mismatch is seen at R1 and R2 (warn); a reorg beyond the confirmation window removes
    R2's RootCommitted: on the canonical chain the mismatch is at one root only, so the key is not derived and the
    framework resolves it ("evidence left the canonical chain"). A later root that still mismatches warns again."""
    u = UpdaterWorld(tmp_path, confirmations=2)
    tick(u.w, u.oc, 4)
    u.round(updates=(0,))
    u.set_eb(32, leaves={u.cids[1]: 64})
    u.close()
    assert "state-mismatch" not in open_keys(u.w, "m11")
    tick(u.w, u.oc, 50)
    fork = u.c.number
    u.round(updates=(0,))
    u.set_eb(32, leaves={u.cids[1]: 64})
    u.close()
    assert open_keys(u.w, "m11") == {"state-mismatch": "warn"}
    u.c.reorg(fork)  # the second commit and everything after leave the chain
    u.api.ref = u.w.db.meta("check:m11:state_evals")[0]["ref"]
    u.c.mine(20)
    tick(u.w, u.oc, 5)
    assert len(u.w.db.meta("check:m11:state_evals")) == 1
    assert "state-mismatch" not in open_keys(u.w, "m11")
    (res,) = sent(u.w, "m11", "kind='resolved'")
    assert REORG_TEXT in res["text"]
    u.round(updates=(0,))  # the cluster still mismatches at the next root: older than one round again
    u.set_eb(32, leaves={u.cids[1]: 64})
    u.close(extra=10)
    assert open_keys(u.w, "m11") == {"state-mismatch": "warn"}


def test_qa2_f3_reorg_keeping_the_aging_round_with_the_api_down_keeps_the_mismatch(tmp_path):
    """Twin: the reorg orphans the evaluation of R2 but keeps R2's commit and the API is down: the latest root is not
    evaluated again, so the age is unknown, not disproved: the warn stays, nothing is resolved."""
    u = UpdaterWorld(tmp_path)
    tick(u.w)
    mismatch_older_than_one_round(u, cluster=1, leaf=64)
    assert open_keys(u.w, "m11") == {"state-mismatch": "warn"}
    u.c.reorg(u.c.number - 2)
    u.api.down = True
    u.c.mine(3)
    tick(u.w)
    assert open_keys(u.w, "m11") == {"state-mismatch": "warn"} and not sent(u.w, "m11", "kind='resolved'")


def downtime_rounds(u, normal, zero_first=True):
    u.round(updates=() if zero_first else (0,))
    u.c.elapse(400)
    for _ in range(normal):
        u.c.elapse(2000)
        u.round(updates=(0,))
        u.c.elapse(400)


def test_qa2_f4_zero_round_older_than_history_rounds_after_downtime(tmp_path):
    """QA2-F4 reproducer (class 6): the worker is down for 8 rounds; the first had 0 updater txs (warn), the rest
    are normal. Every closed round no run evaluated is evaluated once: the zero round is reported (open + its
    resolution), once, also across a later restart."""
    u = UpdaterWorld(tmp_path)
    tick(u.w)
    downtime_rounds(u, 7)
    w = u.restart()
    tick(w, u.oc, 2)
    kinds = [(r["ikey"], r["kind"]) for r in sent(w, "m11")]
    assert kinds == [("liveness", "open"), ("liveness", "resolved")], kinds
    w = u.restart(w)
    tick(w, u.oc, 2, seconds=600)
    assert len(sent(w, "m11")) == 2


def test_qa2_f4_zero_round_within_history_rounds_after_downtime_control(tmp_path):
    u = UpdaterWorld(tmp_path)
    tick(u.w)
    downtime_rounds(u, 5)
    w = u.restart()
    tick(w, u.oc, 2)
    kinds = [(r["ikey"], r["kind"]) for r in sent(w, "m11")]
    assert kinds == [("liveness", "open"), ("liveness", "resolved")], kinds


def test_qa2_f4_revert_rate_round_older_than_history_rounds_after_downtime(tmp_path):
    """The same cap for the revert-rate rule: a round whose updates mostly reverted, 8 rounds before the restart."""
    u = UpdaterWorld(tmp_path, n=4)
    tick(u.w)
    ref = u.round(updates=(0,))
    u.fail("MustUseLatestRoot", ref, i=1)
    u.fail("MustUseLatestRoot", ref, i=2)
    u.c.elapse(400)
    for _ in range(7):
        u.c.elapse(2000)
        u.round(updates=(0,))
        u.c.elapse(400)
    w = u.restart()
    tick(w, u.oc, 2)
    kinds = [(r["ikey"], r["kind"]) for r in sent(w, "m11")]
    assert ("revert-rate", "open") in kinds and ("revert-rate", "resolved") in kinds, kinds


def test_qa2_f4_rounds_before_the_first_run_stay_history(tmp_path):
    """Benign twin: the zero round closed before the check's first run: history (baseline), not reported, and the
    first run asks for no more than history_rounds rounds' senders."""
    u = UpdaterWorld(tmp_path)
    downtime_rounds(u, 7)
    tick(u.w, u.oc, 2)
    assert sent(u.w, "m11") == []
    assert u.w.db.meta("run:m11")["rpc_calls"].get("eth_getTransactionByHash", 0) <= 6


def test_qa2_f4_reorg_lowers_the_evaluated_mark(tmp_path):
    """Reorg rule of `check:m11:evaluated_upto`: a reorg below the newest evaluated commit lowers the mark to the
    ancestor, so a round re-included on the new chain below the old mark is still evaluated (history_rounds 1 here
    makes the mark decide): the new chain's zero round, healed by its next round before the worker runs, is
    reported once."""
    u = UpdaterWorld(tmp_path, n=4, checks={"history_rounds": 1})
    tick(u.w)
    fork = u.c.number
    u.round(updates=(0,))
    u.close()
    u.round(updates=(0,))
    u.close()
    assert sent(u.w, "m11") == [] and u.w.db.meta("check:m11:evaluated_upto") > fork + 300
    u.c.reorg(fork)
    u.ssv.latest_root = 0  # the model's root moves back with the chain
    u.round(updates=())  # re-included below the old mark, now without updater txs
    u.c.mine(305)
    u.round(updates=(1,))
    u.close(extra=20)
    kinds = [(r["ikey"], r["kind"]) for r in sent(u.w, "m11")]
    assert kinds == [("liveness", "open"), ("liveness", "resolved")], kinds


def test_a_reported_zero_run_that_grew_and_healed_while_a_new_one_began_is_sent(tmp_path):
    """Found by the repair-2 sweep (class 5, book level): zero round A is reported (warn); while the worker is down,
    zero round B makes it two in a row (page), C heals it and zero round D begins. The grown episode (page, A and B)
    is sent before the new active one (D, warn): the page-worthy second zero round is not lost."""
    u = UpdaterWorld(tmp_path)
    tick(u.w)
    a = u.round(updates=())
    u.close()
    assert open_keys(u.w, "m11") == {"liveness": "warn"}
    u.c.elapse(2000)
    b = u.round(updates=())
    u.c.elapse(2400)
    u.round(updates=(0,))
    u.c.elapse(2400)
    d = u.round(updates=())
    u.c.elapse(400)
    w = u.restart()
    tick(w, u.oc, 2)
    msgs = [(r["severity"], r["text"]) for r in sent(w, "m11")][1:]
    assert len(msgs) == 2 and msgs[0][0] == "page" and f"{a}, {b}" in msgs[0][1], msgs
    assert msgs[1][0] == "warn" and str(d) in msgs[1][1] and open_keys(w, "m11") == {"liveness": "warn"}


def test_qa2_f4_a_failing_run_after_downtime_does_not_advance_the_evaluated_mark(tmp_path):
    """(b) error input for `check:m11:evaluated_upto`: the first run after the downtime fails (a sender the RPC
    cannot return); the mark stays (rolled back with the run), so the next run still evaluates the old zero round."""
    u = UpdaterWorld(tmp_path)
    tick(u.w)
    mark = u.w.db.meta("check:m11:evaluated_upto")
    downtime_rounds(u, 7)
    tx = max(u.c.senders)
    saved = u.c.senders.pop(tx)  # eth_getTransactionByHash -> null for the newest update
    w = u.restart()
    tick(w, u.oc, 2)
    assert w.db.meta("run:m11")["errors"] >= 1 and w.db.meta("check:m11:evaluated_upto") == mark
    assert sent(w, "m11") == []
    u.c.senders[tx] = saved
    tick(w, u.oc, 1, seconds=300)
    kinds = [(r["ikey"], r["kind"]) for r in sent(w, "m11")]
    assert kinds == [("liveness", "open"), ("liveness", "resolved")], kinds


# ---------------------------------------------------------------- repair 3 (QA attempt 3)


def _kinds(w):
    return [(r["ikey"], r["severity"], r["kind"]) for r in sent(w, "m11")]


def _outage(u, zero_rounds):
    """One normal round, `zero_rounds` rounds without updater txs, then the round that heals them."""
    u.round(updates=(0,))
    u.close()
    for _ in range(zero_rounds):
        u.round(updates=())
        u.close()
    u.round(updates=(1,))
    u.close()


def test_qa3_f1_two_zero_rounds_sliding_out_of_history_are_not_reported_again(tmp_path):
    """QA3-F1 reproducer (classes 6, 10): two zero rounds page, the next round heals them; while the outage slides
    out of the last `history_rounds` closed rounds nothing is sent again (it kept the id of its first round)."""
    u = UpdaterWorld(tmp_path)
    tick(u.w)
    _outage(u, 2)
    before = _kinds(u.w)
    assert [k for _, _, k in before] == ["open", "escalate", "resolved"], before
    for _ in range(8):
        u.round(updates=(0,))
        u.close()
    assert _kinds(u.w)[len(before):] == [], [r["text"][:160] for r in sent(u.w, "m11")[len(before):]]


def test_qa3_f1_long_zero_outage_recovery_sends_no_further_pages(tmp_path):
    """QA3-F1 reproducer: a 7-round outage (longer than history_rounds 6) is one incident whose texts count all 7
    rounds (open page / its resolution not understated); after the resolution, eight normal rounds stay silent."""
    u = UpdaterWorld(tmp_path)
    tick(u.w)
    _outage(u, 7)
    rows = sent(u.w, "m11")
    assert len({r["incident_id"] for r in rows}) == 1 and rows[-1]["kind"] == "resolved", _kinds(u.w)
    assert "0 updates for 7 committed round(s)" in rows[-2]["text"], rows[-2]["text"][:200]
    assert "0 updates for 7 committed round(s)" in rows[-1]["text"], rows[-1]["text"][:200]
    n = len(rows)
    for _ in range(8):
        u.round(updates=(0,))
        u.close()
    assert _kinds(u.w)[n:] == [], [r["text"][:160] for r in sent(u.w, "m11")[n:]]


def test_qa3_f1_ongoing_outage_longer_than_history_keeps_one_incident_and_counts_every_round(tmp_path):
    """(f) window anchoring: an outage still going on after 9 rounds is one incident (no resolve/reopen as its first
    round leaves the window), re-sent per round with the full count."""
    u = UpdaterWorld(tmp_path)
    tick(u.w)
    u.round(updates=(0,))
    u.close()
    for _ in range(9):
        u.round(updates=())
        u.close()
    rows = sent(u.w, "m11")
    assert open_keys(u.w, "m11") == {"liveness": "page"}
    assert len({r["incident_id"] for r in rows}) == 1 and "resolved" not in [r["kind"] for r in rows], _kinds(u.w)
    assert "0 updates for 9 committed round(s)" in rows[-1]["text"], rows[-1]["text"][:200]


def test_qa3_f1_restart_while_a_resolved_outage_slides_out_is_silent(tmp_path):
    """(d) restart between detection and handling: the worker restarts (twice) while the resolved 7-round outage
    leaves the history window; nothing is sent again."""
    u = UpdaterWorld(tmp_path)
    tick(u.w)
    _outage(u, 7)
    n = len(sent(u.w, "m11"))
    w = u.w
    for i in range(8):
        u.round(updates=(0,))
        if i in (2, 5):
            w = u.restart(w)
            u.w = w
        u.close()
    assert len(sent(u.w, "m11")) == n, _kinds(u.w)[n:]


def test_qa3_f1_outage_during_downtime_is_reported_once_and_not_again_while_it_slides(tmp_path):
    """Downtime (QA2-F4 marker) with a 3-round outage inside it: reported once (page + resolution) after the restart,
    then silent while the outage leaves the history window."""
    u = UpdaterWorld(tmp_path)
    tick(u.w)
    u.round(updates=(0,))
    u.c.elapse(2400)
    for _ in range(3):
        u.round(updates=())
        u.c.elapse(2400)
    for _ in range(2):
        u.round(updates=(1,))
        u.c.elapse(2400)
    w = u.restart()
    u.w = w
    tick(w, u.oc, 2)
    kinds = _kinds(w)
    assert kinds == [("liveness", "page", "open"), ("liveness", "info", "resolved")], kinds
    for _ in range(8):
        u.round(updates=(0,))
        u.close()
    assert _kinds(u.w) == kinds, _kinds(u.w)


def test_qa3_f1_reorg_beyond_confirmations_removing_the_healing_round_of_a_resolved_outage_reopens_it(tmp_path):
    """(c) reorg that removes the evidence of an ALREADY RESOLVED key: the round that healed a 2-round outage is
    replaced (depth > confirmations) by a round without updater txs: the outage holds again on the canonical chain
    and is reported (supported by evidence); a later healing round resolves it once, and nothing follows."""
    u = UpdaterWorld(tmp_path, confirmations=2)
    tick(u.w)
    u.round(updates=(0,))
    u.close()
    u.round(updates=())
    u.close()
    u.round(updates=())
    u.close()
    fork = u.c.number
    latest = u.ssv.latest_root
    u.round(updates=(1,))
    u.close()
    assert open_keys(u.w, "m11") == {} and [k for _, _, k in _kinds(u.w)] == ["open", "escalate", "resolved"]
    u.c.reorg(fork)
    u.ssv.latest_root = latest
    u.round(updates=())  # the replacement round: no updater tx
    u.close()
    assert open_keys(u.w, "m11") == {"liveness": "page"}, _kinds(u.w)
    assert "0 updates for 3 committed round(s)" in sent(u.w, "m11")[-1]["text"]
    u.round(updates=(1,))
    u.close()
    n = len(sent(u.w, "m11"))
    assert open_keys(u.w, "m11") == {} and sent(u.w, "m11")[-1]["kind"] == "resolved"
    for _ in range(8):
        u.round(updates=(0,))
        u.close()
    assert len(sent(u.w, "m11")) == n, _kinds(u.w)[n:]


def test_qa3_f1_reorg_re_including_the_healing_round_of_a_resolved_outage_is_silent(tmp_path):
    """Benign twin of the row above (reorg depth 1 inside the confirmation window and N beyond it): the healing round
    is re-included with its updater txs: the resolved outage is not reported again."""
    for conf in (0, 2):
        (tmp_path / f"c{conf}").mkdir()
        u = UpdaterWorld(tmp_path / f"c{conf}", confirmations=conf)
        tick(u.w)
        u.round(updates=(0,))
        u.close()
        for _ in range(2):
            u.round(updates=())
            u.close()
        fork = u.c.number
        latest = u.ssv.latest_root
        u.round(updates=(1,))
        u.close()
        n = len(sent(u.w, "m11"))
        u.c.reorg(fork)
        u.ssv.latest_root = latest
        u.c.mine(2)  # the healing round again on the new chain (one blockNum later: the model keeps cluster roots)
        u.round(updates=(1,))
        u.close()
        for _ in range(7):
            u.round(updates=(0,))
            u.close()
        assert len(sent(u.w, "m11")) == n and open_keys(u.w, "m11") == {}, (conf, _kinds(u.w)[n:])


def test_r3_revert_rate_is_not_healed_by_a_round_without_any_updater_transaction(tmp_path):
    """Found by the repair-3 generalisation (class 1, the QA3-F2 class in M11): a round in which the updater sent
    nothing has no revert rate (0/0); it is not proof that the rate is back under 10 %. The warn stays (the zero round
    is the liveness rule's), and the next round with updater transactions heals it, naming that round."""
    u = UpdaterWorld(tmp_path)
    tick(u.w)
    ref = u.round(updates=(0,))
    u.fail("MustUseLatestRoot", ref, i=1)
    u.close()
    assert open_keys(u.w, "m11") == {"revert-rate": "warn"}
    u.round(updates=())
    u.close()
    assert open_keys(u.w, "m11") == {"revert-rate": "warn", "liveness": "warn"}, _kinds(u.w)
    clean = u.round(updates=(0, 1))
    u.close()
    assert open_keys(u.w, "m11") == {}
    res = [r["text"] for r in sent(u.w, "m11", "kind='resolved'") if r["ikey"] == "revert-rate"]
    assert len(res) == 1 and f"blockNum {clean} is under 10%" in res[0], res


def test_r3_revert_rate_open_while_it_slides_out_of_history_is_kept(tmp_path):
    """(class 6) The open revert-rate round leaves the last `history_rounds` closed rounds while every later round is
    a zero round: no positive proof, so it stays open (no "no longer observed" resolution); a clean round heals it."""
    u = UpdaterWorld(tmp_path)
    tick(u.w)
    ref = u.round(updates=(0,))
    u.fail("MustUseLatestRoot", ref, i=1)
    u.close()
    for _ in range(8):
        u.round(updates=())
        u.close()
    assert open_keys(u.w, "m11") == {"revert-rate": "warn", "liveness": "page"}, _kinds(u.w)
    assert not [r for r in sent(u.w, "m11", "kind='resolved'") if r["ikey"] == "revert-rate"]
    u.round(updates=(0,))
    u.close()
    assert open_keys(u.w, "m11") == {}
    w = u.restart()
    tick(w, u.oc, 2, seconds=600)
    assert open_keys(w, "m11") == {} and len([r for r in sent(w, "m11") if r["ikey"] == "revert-rate"]) == 2


def test_r3_reorg_removing_the_clean_round_that_healed_a_revert_rate_reopens_it(tmp_path):
    """Repair 3 state table (c) for `revert-rate`: the clean round that resolved it is replaced (depth beyond the
    confirmation window) by a round without updater transactions: no positive proof on the new chain, the warn holds
    again; the next clean round resolves it once."""
    u = UpdaterWorld(tmp_path, confirmations=2)
    tick(u.w)
    ref = u.round(updates=(0,))
    u.fail("MustUseLatestRoot", ref, i=1)
    u.close()
    assert open_keys(u.w, "m11") == {"revert-rate": "warn"}
    fork = u.c.number
    latest = u.ssv.latest_root
    u.round(updates=(0, 1))
    u.close()
    assert open_keys(u.w, "m11") == {}
    u.c.reorg(fork)
    u.ssv.latest_root = latest
    u.c.mine(2)
    u.round(updates=())
    u.close()
    assert open_keys(u.w, "m11") == {"revert-rate": "warn", "liveness": "warn"}, _kinds(u.w)
    u.round(updates=(2,))
    u.close()
    assert open_keys(u.w, "m11") == {}
    assert [k for i, _, k in _kinds(u.w) if i == "revert-rate"] == ["open", "resolved", "open", "resolved"]


def test_r3_outage_from_the_first_stored_round_counts_every_round(tmp_path):
    """(a) empty input for the run's backward walk: the outage starts at the first stored RootCommitted (nothing
    before it): one incident, every round counted, nothing sent again while it slides after recovery."""
    u = UpdaterWorld(tmp_path)
    tick(u.w)
    for _ in range(8):
        u.round(updates=())
        u.close()
    rows = sent(u.w, "m11")
    assert len({r["incident_id"] for r in rows}) == 1 and "for 8 committed round(s)" in rows[-1]["text"], _kinds(u.w)
    u.round(updates=(0,))
    u.close()
    n = len(sent(u.w, "m11"))
    for _ in range(7):
        u.round(updates=(0,))
        u.close()
    assert len(sent(u.w, "m11")) == n and open_keys(u.w, "m11") == {}, _kinds(u.w)[n:]


def test_r3_a_sender_lookup_failing_in_the_backward_walk_fails_the_run_closed(tmp_path):
    """(b) error input: another sender's update in the first (oldest) zero round, beyond history_rounds, cannot be
    read (eth_getTransactionByHash -> null) on a fresh store (restart with its sender cache dropped): the run raises
    and changes nothing (no resolve, no new incident); the next run with the RPC back continues the one incident."""
    u = UpdaterWorld(tmp_path)
    tick(u.w)
    u.round(updates=(0,))
    u.close()
    u.round(updates=())
    tx = u.update(1, eb=40, sender=SLOTS[3])  # another sender in the outage's first round
    u.close()
    for _ in range(7):
        u.round(updates=())
        u.close()
    rows = len(sent(u.w, "m11"))
    u.w.db.conn.execute("DELETE FROM m11_senders")
    u.w.db.conn.commit()
    saved = u.c.senders.pop(tx)
    u.round(updates=())
    u.close()
    assert u.w.db.meta("run:m11")["errors"] >= 1 and len(sent(u.w, "m11")) == rows
    assert open_keys(u.w, "m11") == {"liveness": "page"}
    u.c.senders[tx] = saved
    tick(u.w, u.oc, 1, seconds=300)
    new = sent(u.w, "m11")[rows:]
    assert [r["kind"] for r in new] == ["update"] and "for 9 committed round(s)" in new[0]["text"], _kinds(u.w)[rows:]


def test_r3_a_revert_counts_only_in_the_round_of_its_blocknum_when_windows_overlap(tmp_path):
    """Found by the repair-3 wider sweep (seed 37): round B commits 20 min after round A, inside A's 60 min window.
    A reverted update for B's root counts in B only (decoded blockNum), never in A as well: B warns, A stays clean."""
    u = UpdaterWorld(tmp_path)
    tick(u.w)
    a = u.round(updates=(0, 1))
    u.c.mine(100)
    b = u.round(updates=(0,))
    u.fail("MustUseLatestRoot", b, i=2)
    u.close()
    titles = [r["text"].splitlines()[0] for r in sent(u.w, "m11") if r["ikey"] == "revert-rate"]
    assert open_keys(u.w, "m11") == {"revert-rate": "warn"} and titles, _kinds(u.w)
    assert all(f"blockNum {b}" in t for t in titles) and not any(f"blockNum {a}" in t for t in titles), titles


def test_r3_a_late_revert_for_the_older_root_counts_in_the_older_round_only(tmp_path):
    """The reverse: after B committed, the updater sends an update for A's (stale) root, which reverts
    MustUseLatestRoot inside both windows: it counts in A (its blockNum), not in B."""
    u = UpdaterWorld(tmp_path)
    tick(u.w)
    a = u.round(updates=(0,))
    u.c.mine(100)
    b = u.round(updates=(0, 1))
    u.fail("MustUseLatestRoot", a, i=2)
    u.close()
    titles = [r["text"].splitlines()[0] for r in sent(u.w, "m11") if r["ikey"] == "revert-rate"]
    assert titles and all(f"blockNum {a}" in t for t in titles) and not any(f"blockNum {b}" in t for t in titles), \
        titles


def test_r3_reorg_orphaning_the_reverts_of_an_open_revert_rate_resolves_with_the_reorg_text(tmp_path):
    """README 6.2 for `revert-rate`: its evidence is the reverts (inbox rows), not the commit. A reorg beyond the
    confirmation window orphans the reverted transaction's block (the commit survives): resolved with the reorg text,
    not as "no longer observed"."""
    u = UpdaterWorld(tmp_path, confirmations=2)
    tick(u.w)
    ref = u.round(updates=(0,))
    u.c.mine(3)
    fork = u.c.number
    u.fail("MustUseLatestRoot", ref, i=1)
    u.close()
    assert open_keys(u.w, "m11") == {"revert-rate": "warn"}
    u.c.reorg(fork)
    u.oc.stub()
    u.c.mine(4)
    tick(u.w, u.oc, 3)
    res = [r["text"] for r in sent(u.w, "m11", "kind='resolved'") if r["ikey"] == "revert-rate"]
    assert open_keys(u.w, "m11") == {} and len(res) == 1 and REORG_TEXT in res[0], (res, _kinds(u.w))


# ---------------------------------------------------------------- repair 4 (QA attempt 4)


def test_qa4_f1_reverts_of_the_rounds_own_blocknum_after_60_min_count_in_the_round(tmp_path):
    """QA4-F1 reproducer, scenario A (missed warn; classes 9, 4): two successful updates right after the commit, then
    three updates for the same root revert 61-62 min after the commit, before the next root: 3 of 5 = 60 % -> warn."""
    u = world(tmp_path)
    ref = u.round(updates=(0, 1))
    u.c.elapse(305)
    for i in range(3):
        u.fail("IncorrectClusterState", ref, i=i)
    u.close()
    assert open_keys(u.w, "m11") == {"revert-rate": "warn"}, _kinds(u.w)
    assert "3 of 5" in incidents(u.w, "m11")[0]["body"] and f"round of blockNum {ref}" in incidents(u.w, "m11")[0]["body"]


def test_qa4_f1_late_successful_updates_count_in_the_denominator(tmp_path):
    """QA4-F1 reproducer, scenario B (false warn): one success and one revert in the first hour, nine more successful
    updates of the same root after 61 min (a slow updater, still the same round): 1 of 11 = 9.1 % -> silent."""
    u = world(tmp_path, n=11)
    ref = u.round(updates=(0,))
    u.fail("IncorrectClusterState", ref, i=10)
    u.c.elapse(305)
    for i in range(1, 10):
        u.update(i, eb=33)
    u.close()
    assert "revert-rate" not in open_keys(u.w, "m11") and not [p for p in pw(u.w) if p[0] == "revert-rate"], \
        _kinds(u.w)


def test_qa4_f1_round_with_only_reverted_updates_warns_liveness_and_revert_rate_control(tmp_path):
    """QA4 control: every updater transaction of the round reverts: 0 successful updates within 60 min (liveness) and
    a 100 % revert rate."""
    u = world(tmp_path)
    ref = u.round(updates=())
    u.fail("IncorrectClusterState", ref, i=0)
    u.fail("IncorrectClusterState", ref, i=1)
    u.close()
    keys = open_keys(u.w, "m11")
    assert keys.get("liveness") in ("warn", "page") and keys.get("revert-rate") == "warn", (keys, _kinds(u.w))


def test_qa4_f1_a_run_inside_a_slow_round_warns_then_resolves_on_the_full_count(tmp_path):
    """Scenario B with the worker running in between (every 5 min): at the window's close the round's updates so far
    are 1 of 2 reverted (warn, what was true then); the late successful updates of the same round bring it to 1 of
    11: resolved once, with that count (positive proof, never "no longer observed"); nothing more afterwards."""
    u = world(tmp_path, n=11)
    ref = u.round(updates=(0,))
    u.fail("IncorrectClusterState", ref, i=10)
    u.close()
    assert open_keys(u.w, "m11") == {"revert-rate": "warn"} and "1 of 2" in incidents(u.w, "m11")[0]["body"]
    for i in range(1, 10):
        u.update(i, eb=33)
    tick(u.w, u.oc, 5, seconds=300)  # the next interval run
    res = [r["text"] for r in sent(u.w, "m11", "kind='resolved'")]
    assert open_keys(u.w, "m11") == {} and len(res) == 1 and "is now 1 of 11" in res[0], _kinds(u.w)
    n = len(sent(u.w, "m11"))
    tick(u.w, u.oc, 30, seconds=600)
    w = u.restart()
    tick(w, u.oc, 2)
    assert len(sent(w, "m11")) == n


def test_qa4_f1_late_reverts_after_the_window_reopen_a_round_resolved_by_its_own_updates(tmp_path):
    """The rate keeps following the round: after the late successes resolved it, three more reverts of the same root
    (1+3 of 14 = 29 %) open it again (a new message; the round's evidence grew)."""
    u = world(tmp_path, n=14)
    ref = u.round(updates=(0,))
    u.fail("IncorrectClusterState", ref, i=13)
    u.close()
    for i in range(1, 10):
        u.update(i, eb=33)
    tick(u.w, u.oc, 5, seconds=300)  # the next interval run
    assert open_keys(u.w, "m11") == {}
    for i in (10, 11, 12):
        u.fail("MustUseLatestRoot", ref, i=i)
    tick(u.w, u.oc, 5)
    assert open_keys(u.w, "m11") == {"revert-rate": "warn"}
    assert [k for i, _, k in _kinds(u.w) if i == "revert-rate"] == ["open", "resolved", "open"], _kinds(u.w)


def test_qa4_f1_a_late_revert_for_the_older_root_after_the_next_commit_counts_in_the_older_round(tmp_path):
    """A revert of round A's blockNum lands after round B committed and after A's window closed: it counts in A (its
    round, at any time), not in B, and not nowhere."""
    u = world(tmp_path)
    a = u.round(updates=(0,))
    u.close()
    b = u.round(updates=(0, 1))
    u.fail("MustUseLatestRoot", a, i=2)
    u.close()
    titles = [r["text"].splitlines()[0] for r in sent(u.w, "m11") if r["ikey"] == "revert-rate"]
    assert titles and all(f"blockNum {a}" in t for t in titles) and not any(f"blockNum {b}" in t for t in titles), \
        _kinds(u.w)


def test_qa4_f1_a_revert_for_a_round_older_than_the_evaluated_rounds_is_not_lost(tmp_path):
    """(class 5, no event lost) A revert of round A's blockNum lands after A slid out of the last history_rounds
    evaluated rounds: A is brought back into the evaluation (1 of 2 reverted, healed by the later clean rounds): it is
    reported once (open + resolution) and never again."""
    u = world(tmp_path)
    a = u.round(updates=(0,))
    u.close()
    for _ in range(7):
        u.round(updates=(0, 1))
        u.close()
    assert pw(u.w) == []
    u.fail("MustUseLatestRoot", a, i=1)
    tick(u.w, u.oc, 5)
    kinds = [(i, k) for i, _, k in _kinds(u.w)]
    assert kinds == [("revert-rate", "open"), ("revert-rate", "resolved")], _kinds(u.w)
    assert f"blockNum {a}" in sent(u.w, "m11")[0]["text"]
    tick(u.w, u.oc, 30, seconds=600)
    u.round(updates=(0,))
    u.close()
    assert len(sent(u.w, "m11")) == 2


def _fail_undecodable(u, sender=SLOTS[4]):
    """An updateClusterBalance revert whose calldata does not decode (selector + 4 bytes)."""
    from fixtures_oracle import ERR, PROXY, inbox_line

    from checks.m9_oracle_common import UPDATE_CLUSTER_BALANCE_SELECTOR

    data = UPDATE_CLUSTER_BALANCE_SELECTOR + "deadbeef"
    u.c.stub_call(PROXY, data, revert=ERR["MustUseLatestRoot"])
    u.c.mine()
    tx = u.c.add_tx(sender, PROXY, data)
    u.c.mine()
    with open(u.inbox, "a") as fh:
        fh.write(inbox_line(u.c, tx, sender, UPDATE_CLUSTER_BALANCE_SELECTOR,
                            "updateClusterBalance(uint64,address,uint64[],(uint32,uint64,uint64,bool,uint256),uint32,"
                            "bytes32[])"))
    return tx


def test_qa4_f1_an_undecodable_revert_after_the_window_counts_in_the_round_whose_commit_precedes_it(tmp_path):
    """Fallback [commit, next commit): an updater revert whose input does not decode, 61 min after A's commit and
    before the next root, counts in A (1 of 2 -> warn); one after B's commit counts in B."""
    u = world(tmp_path)
    a = u.round(updates=(0,))
    u.c.elapse(305)
    _fail_undecodable(u)
    u.close()
    titles = [r["text"].splitlines()[0] for r in sent(u.w, "m11") if r["ikey"] == "revert-rate"]
    assert open_keys(u.w, "m11") == {"revert-rate": "warn"} and f"blockNum {a}" in titles[0], _kinds(u.w)
    b = u.round(updates=(0, 1))
    _fail_undecodable(u)
    u.close()
    titles = [r["text"].splitlines()[0] for r in sent(u.w, "m11") if r["ikey"] == "revert-rate"]
    assert any(f"blockNum {b}" in t for t in titles) and open_keys(u.w, "m11") == {"revert-rate": "warn"}, _kinds(u.w)


def _zero_round_page(tmp_path, **kw):
    u = world(tmp_path, confirmations=2, **kw)
    u.round(updates=(0,))
    u.close()
    u.round(updates=())
    u.set_eb(32, leaves={u.cids[1]: 40})  # the zero round's leaves: cluster 1 differs from the chain
    u.close()
    assert open_keys(u.w, "m11") == {"liveness": "page"}, _kinds(u.w)
    return u


def _sev(w, key="liveness"):
    return [tuple(r) for r in w.db.conn.execute(
        "SELECT severity, state FROM incidents WHERE check_id='m11' AND key=?", (key,)).fetchall()]


def test_qa4_f2_reorg_run_with_proof_api_down_keeps_the_zero_round_page(tmp_path):
    """QA4-F2 reproducer (classes 2, 1; README 6.2 rule 4): a zero round paged on a leaf mismatch; a reorg beyond the
    confirmation depth replaces only empty blocks after the evaluation while the proof API is down. The reorg run
    cannot evaluate the discarded proof again: it raises (rolled back, marker kept, retried), so the page stays a
    page and nothing is sent; when the API is back the retry re-evaluates on the new chain: no second page."""
    u = _zero_round_page(tmp_path)
    n = len(sent(u.w, "m11"))
    u.api.down = True
    u.oc.reorg(u.c.number - 5, latest=u.oc.latest)
    u.c.mine(8)
    tick(u.w)
    assert _sev(u.w) == [("page", "open")] and sent(u.w, "m11")[n:] == []
    assert u.w.db.meta("reorg:m11") is not None and "cannot be evaluated again" in u.w.db.meta("run:m11")["last_error"]
    tick(u.w, u.oc, 3, seconds=300)  # retried every step while the API is down
    assert _sev(u.w) == [("page", "open")] and sent(u.w, "m11")[n:] == [] and u.w.db.meta("reorg:m11") is not None
    u.api.down = False
    tick(u.w, u.oc, 3)
    tick(u.w, u.oc, 3, seconds=300)
    assert u.w.db.meta("reorg:m11") is None and _sev(u.w) == [("page", "open")] and sent(u.w, "m11")[n:] == []


def test_qa4_f2_non_reorg_run_with_proof_api_down_keeps_the_zero_round_page_control(tmp_path):
    """QA4 control: the same API outage without a reorg keeps the page (the persisted proof is used) and sends
    nothing."""
    u = _zero_round_page(tmp_path)
    n = len(sent(u.w, "m11"))
    u.api.down = True
    tick(u.w, u.oc, 8)
    u.api.down = False
    tick(u.w, u.oc, 3)
    assert _sev(u.w) == [("page", "open")] and sent(u.w, "m11")[n:] == []


def test_qa4_f2_reorg_run_with_a_partial_answer_omitting_the_mismatching_cluster_keeps_the_page(tmp_path):
    """The partial variant named in QA4-F2 (class 1): in the reorg run the API answers every cluster but the one that
    differed ("cluster not found"): no mismatch proven, one UNKNOWN; never a downgrade of what was proven: the run
    raises and is retried; when the leaf is served again the page holds, nothing is sent."""
    u = _zero_round_page(tmp_path)
    n = len(sent(u.w, "m11"))
    leaf = u.api.leaves.pop(u.cids[1])
    u.oc.reorg(u.c.number - 5, latest=u.oc.latest)
    u.c.mine(8)
    tick(u.w)
    tick(u.w, u.oc, 3, seconds=300)
    assert _sev(u.w) == [("page", "open")] and sent(u.w, "m11")[n:] == [] and u.w.db.meta("reorg:m11") is not None
    u.api.leaves[u.cids[1]] = leaf
    tick(u.w, u.oc, 3)
    tick(u.w, u.oc, 3, seconds=300)
    assert u.w.db.meta("reorg:m11") is None and _sev(u.w) == [("page", "open")] and sent(u.w, "m11")[n:] == []


def test_qa4_f2_restart_between_the_failed_reorg_run_and_its_retry(tmp_path):
    """(d) restart between detection and handling: the worker restarts while the reorg run is still failing on the
    proof API; the marker survives the restart and the page is kept; with the API back the retry sends nothing."""
    u = _zero_round_page(tmp_path)
    n = len(sent(u.w, "m11"))
    u.api.down = True
    u.oc.reorg(u.c.number - 5, latest=u.oc.latest)
    u.c.mine(8)
    tick(u.w)
    w = u.restart()
    u.w = w
    tick(w, u.oc, 2, seconds=300)
    assert w.db.meta("reorg:m11") is not None and _sev(w) == [("page", "open")] and sent(w, "m11")[n:] == []
    u.api.down = False
    tick(w, u.oc, 3)
    tick(w, u.oc, 3, seconds=300)
    assert w.db.meta("reorg:m11") is None and _sev(w) == [("page", "open")] and sent(w, "m11")[n:] == []


def test_qa4_f2_reorg_run_with_the_api_up_re_evaluates_on_the_new_chain(tmp_path):
    """Benign twin: the API answers in the reorg run: the proof is recomputed on the new chain (still mismatching):
    the page is kept under its id, nothing is sent; the reorg run commits."""
    u = _zero_round_page(tmp_path)
    n = len(sent(u.w, "m11"))
    calls = u.api.calls
    u.oc.reorg(u.c.number - 5, latest=u.oc.latest)
    u.c.mine(8)
    tick(u.w)
    tick(u.w, u.oc, 3, seconds=300)
    assert u.api.calls > calls and u.w.db.meta("reorg:m11") is None
    assert _sev(u.w) == [("page", "open")] and sent(u.w, "m11")[n:] == []


def test_qa4_f2_reorg_run_with_the_api_down_keeps_a_zero_round_warn_unchanged(tmp_path):
    """(b) error input for a warn-level zero round (every leaf matched): the reorg run that discarded its proof and
    cannot redo it raises as well (any unevaluable input of a reorg run): the warn stays, nothing is sent, the marker
    is kept until the API answers."""
    u = world(tmp_path, confirmations=2)
    u.round(updates=(0,))
    u.close()
    u.round(updates=())
    u.close()
    assert open_keys(u.w, "m11") == {"liveness": "warn"}
    n = len(sent(u.w, "m11"))
    u.api.down = True
    u.oc.reorg(u.c.number - 5, latest=u.oc.latest)
    u.c.mine(8)
    tick(u.w)
    assert u.w.db.meta("reorg:m11") is not None and _sev(u.w) == [("warn", "open")] and sent(u.w, "m11")[n:] == []
    u.api.down = False
    tick(u.w, u.oc, 3, seconds=300)
    assert u.w.db.meta("reorg:m11") is None and _sev(u.w) == [("warn", "open")] and sent(u.w, "m11")[n:] == []


def test_r4_reorg_removing_the_late_successes_that_resolved_a_round_reopens_it(tmp_path):
    """State table (c) for the within-round resolution: the late successful updates that brought the round under the
    threshold leave the chain (depth beyond the confirmation window; the commit and the revert stay): the clearance
    was orphaned, the round is again 1 of 2 reverted: the warn opens again; nothing is lost or duplicated after."""
    u = world(tmp_path, n=11, confirmations=2)
    ref = u.round(updates=(0,))
    u.fail("IncorrectClusterState", ref, i=10)
    u.close()
    assert open_keys(u.w, "m11") == {"revert-rate": "warn"}
    fork = u.c.number
    saved = {cid: dict(st) for cid, st in u.ssv.clusters.items()}
    for i in range(1, 10):
        u.update(i, eb=33)
    tick(u.w, u.oc, 5, seconds=300)
    assert open_keys(u.w, "m11") == {}
    u.c.reorg(fork)
    for cid, st in saved.items():
        u.ssv.clusters[cid].update(st)
    u.oc.stub()
    u.c.mine(6)
    tick(u.w, u.oc, 3)
    assert open_keys(u.w, "m11") == {"revert-rate": "warn"}, _kinds(u.w)
    assert [k for i, _, k in _kinds(u.w) if i == "revert-rate"] == ["open", "resolved", "open"], _kinds(u.w)
    n = len(sent(u.w, "m11"))
    tick(u.w, u.oc, 3, seconds=600)
    assert len(sent(u.w, "m11")) == n


def test_r4_reorg_orphaning_the_late_reverts_of_an_open_round_resolves_with_the_reorg_text(tmp_path):
    """(c) for the late reverts: an open revert-rate established by reverts 61 min after the commit; a reorg beyond
    the confirmation window orphans exactly those reverts (the commit and the successes stay): resolved with the
    reorg text (its evidence left the chain), not "no longer observed", not kept."""
    u = world(tmp_path, confirmations=2)
    ref = u.round(updates=(0, 1))
    u.c.elapse(305)
    fork = u.c.number
    for i in range(3):
        u.fail("IncorrectClusterState", ref, i=i)
    u.close()
    assert open_keys(u.w, "m11") == {"revert-rate": "warn"}
    u.c.reorg(fork)
    u.oc.stub()
    u.c.mine(6)
    tick(u.w, u.oc, 3)
    res = [r["text"] for r in sent(u.w, "m11", "kind='resolved'") if r["ikey"] == "revert-rate"]
    assert open_keys(u.w, "m11") == {} and len(res) == 1 and REORG_TEXT in res[0], (res, _kinds(u.w))


def test_r4_round_of_orders_an_undecodable_revert_by_block_and_transaction_index():
    """(e) same-block ordering for the [commit, next commit) fallback: a revert in the block of the next commit
    belongs to the earlier round when its transaction index is lower than the commit's, to the next round when it is
    higher, and (index unknown) to the next round; before the first stored commit: no round. A decoded blockNum of a
    stored commit wins over the position."""
    from checks.m11_updater_liveness import M11UpdaterLiveness

    commits = [SimpleNamespace(block=100, tx_index=5, ref_block=99), SimpleNamespace(block=200, tx_index=5,
                                                                                     ref_block=199)]
    by_ref = {99: 0, 199: 1}

    def of(block, call):
        return M11UpdaterLiveness.round_of({"block": block}, call, commits, by_ref)

    assert of(200, {"index": 4}) == 0 and of(200, {"index": 6}) == 1 and of(200, {"index": None}) == 1
    assert of(200, {}) == 1 and of(150, {}) == 0 and of(100, {"index": 4}) is None and of(99, {}) is None
    assert of(250, {"ref": 99, "index": 1}) == 0 and of(150, {"ref": 199}) == 1 and of(250, {"ref": 7}) == 1


def test_r4_state_mismatch_reorg_run_with_the_api_down_then_back_still_mismatching_is_silent(tmp_path):
    """(b) + (c) for `state_evals` (the QA4-F2 class in the weekly rule): a reorg orphans the latest evaluation while
    the API is down (the key is UNKNOWN: re-reported unchanged); when the API is back and the cluster still differs,
    the warn holds under its id and nothing is sent (no "no longer observed", no second warn)."""
    u = UpdaterWorld(tmp_path)
    tick(u.w)
    mismatch_older_than_one_round(u, cluster=1, leaf=64)
    n = len(sent(u.w, "m11"))
    u.c.reorg(u.c.number - 2)
    u.api.down = True
    u.c.mine(3)
    tick(u.w)
    assert open_keys(u.w, "m11") == {"state-mismatch": "warn"}
    u.api.down = False
    tick(u.w, u.oc, 1, seconds=300)
    tick(u.w, u.oc, 1, seconds=300)
    assert open_keys(u.w, "m11") == {"state-mismatch": "warn"} and _kinds(u.w)[n:] == [], _kinds(u.w)[n:]


# ---------------------------------------------------------------- repair 4: owner D14 switches (checks.m11.*_enabled)


def _health(w, part):
    return [r[0] for r in w.db.conn.execute("SELECT text FROM outbox WHERE severity='health' AND text LIKE ?",
                                            (f"%{part}%",))]


def test_r4_revert_rate_enabled_default_on_is_unchanged(tmp_path):
    u = world(tmp_path, checks={"revert_rate_enabled": True})
    ref = u.round(updates=(0,))
    u.fail("MustUseLatestRoot", ref, i=1)
    u.close()
    assert open_keys(u.w, "m11") == {"revert-rate": "warn"} and not _health(u.w, "revert_rate_enabled")


def test_r4_revert_rate_disabled_is_silent_and_says_so(tmp_path):
    u = world(tmp_path, checks={"revert_rate_enabled": False})
    ref = u.round(updates=(0,))
    u.fail("MustUseLatestRoot", ref, i=1)
    u.fail("MustUseLatestRoot", ref, i=2)
    u.close()
    assert open_keys(u.w, "m11") == {} and pw(u.w) == []
    assert _health(u.w, "checks.m11.revert_rate_enabled false")


def test_r4_revert_rate_disabled_needs_no_inbox(tmp_path):
    from fixtures_oracle import VIEWS
    from harness import make_worker

    make_worker(tmp_path, only=["m11"], views=VIEWS, checks={"m11": {"revert_rate_enabled": False}})


def test_r4_revert_rate_disabled_with_an_open_incident_resolves_it_once(tmp_path):
    u = world(tmp_path)
    ref = u.round(updates=(0,))
    u.fail("MustUseLatestRoot", ref, i=1)
    u.close()
    assert open_keys(u.w, "m11") == {"revert-rate": "warn"}
    n = len(sent(u.w, "m11"))
    u.cfg["revert_rate_enabled"] = False
    w = u.w = u.restart()
    tick(w, u.oc, 2, seconds=300)
    new = sent(w, "m11")[n:]
    assert open_keys(w, "m11") == {} and [r["kind"] for r in new] == ["resolved"], new
    assert "Rule disabled by config" in new[0]["text"]
    ref = u.round(updates=(0,))
    u.fail("MustUseLatestRoot", ref, i=1)
    u.close()
    w = u.w = u.restart()
    tick(w, u.oc, 2, seconds=300)
    assert len(sent(w, "m11")) == n + 1


def test_r4_zero_round_proof_enabled_default_on_is_unchanged(tmp_path):
    u = world(tmp_path, checks={"zero_round_proof_enabled": True})
    u.round(updates=(0,))
    u.close()
    u.round(updates=())
    u.set_eb(32, leaves={u.cids[1]: 40})
    u.close()
    assert open_keys(u.w, "m11") == {"liveness": "page"} and not _health(u.w, "zero_round_proof_enabled")


def test_r4_zero_round_proof_disabled_asks_nothing_and_stays_warn(tmp_path):
    """Off: a single zero round with a mismatching leaf stays warn (no proof API request for it, the text says the
    comparison is disabled); two consecutive zero rounds still page (that rule is not switched)."""
    u = world(tmp_path, checks={"zero_round_proof_enabled": False, "full_state_interval_s": 10**9})
    u.round(updates=(0,))
    u.close()  # the first weekly comparison (shared API) runs here
    calls = u.api.calls
    u.round(updates=())
    u.set_eb(32, leaves={u.cids[1]: 40})
    u.close()
    assert open_keys(u.w, "m11") == {"liveness": "warn"} and u.api.calls == calls
    assert "Proof API comparison disabled by config" in incidents(u.w, "m11")[-1]["body"]
    assert _health(u.w, "checks.m11.zero_round_proof_enabled false")
    u.round(updates=())
    u.close()
    assert open_keys(u.w, "m11") == {"liveness": "page"}


def test_r4_zero_round_proof_disabled_with_an_open_proof_page_sends_nothing(tmp_path):
    """Off while a zero round is open at page because of the proof: the liveness condition still holds (the incident
    is the liveness rule's, not the switched comparison's), so it is not resolved; its severity is recorded at warn
    silently (no message, the framework's rule for a lower severity)."""
    u = _zero_round_page(tmp_path)
    n = len(sent(u.w, "m11"))
    u.cfg["zero_round_proof_enabled"] = False
    w = u.w = u.restart()
    tick(w, u.oc, 2, seconds=300)
    assert "liveness" in open_keys(w, "m11") and sent(w, "m11")[n:] == []


def test_r4_reorg_removing_the_clean_round_of_a_resolved_revert_rate_older_than_history_reopens_it(tmp_path):
    """Found by the repair-4 sweep exploration (seeds 92, 332, 456, 569; class 2, a reorg removing the clearance of
    an ALREADY RESOLVED key): a revert-rate round, then 7 rounds without updater transactions (it slides out of
    history_rounds, kept open), then the clean round that resolves it; a reorg beyond the confirmation window removes
    that clean round. The resolved round is older than the evaluated rounds, yet the reorg run derives it again on
    the new chain: no positive proof is left, the warn opens again (once); a later clean round resolves it once."""
    u = UpdaterWorld(tmp_path, confirmations=2)
    tick(u.w)
    ref = u.round(updates=(0,))
    u.fail("MustUseLatestRoot", ref, i=1)
    u.close()
    for _ in range(7):
        u.round(updates=())
        u.close()
    fork = u.c.number
    latest = u.ssv.latest_root
    u.round(updates=(0,))
    u.close()
    assert "revert-rate" not in open_keys(u.w, "m11"), _kinds(u.w)
    u.c.reorg(fork)
    u.ssv.latest_root = latest
    for st in u.ssv.clusters.values():
        st["root"] = min(st["root"], latest)
    u.oc.stub()
    u.c.mine(4)
    tick(u.w, u.oc, 1)
    assert open_keys(u.w, "m11").get("revert-rate") == "warn", _kinds(u.w)
    assert [k for i, _, k in _kinds(u.w) if i == "revert-rate"] == ["open", "resolved", "open"], _kinds(u.w)
    u.round(updates=(0,))
    u.close()
    tick(u.w, u.oc, 2, seconds=600)
    assert "revert-rate" not in open_keys(u.w, "m11")
    assert [k for i, _, k in _kinds(u.w) if i == "revert-rate"] == ["open", "resolved", "open", "resolved"]
