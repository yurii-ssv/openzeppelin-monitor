"""M11 — EB updater liveness (spec §3 M11): each trigger line at its severity with a benign twin, the proof-API
escalation, the weekly full-state comparison, thresholds_from_chain (updater = live slot-4 key), restart,
rpc_budget (350 clusters), reorg (depth 1, depth N, crash between detection and handling) and inputs that must
never read as clean (RPC without a transaction, proof API down, inbox unavailable)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fixtures_oracle import SLOTS, UpdaterWorld, incidents, open_keys, restart, sent, tick

from rpc import RpcError

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
    assert "1 of 3 clusters differ" in inc["body"] and u.cids[1][:12] in inc["body"]


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


def test_weekly_full_state_mismatch_warns(tmp_path):
    u = world(tmp_path)
    u.round(updates=(0,))
    u.set_eb(32, leaves={u.cids[2]: 48})
    u.close()
    assert open_keys(u.w, "m11") == {"state-mismatch": "warn"}
    u.set_eb(32)
    tick(u.w, u.oc, 1, seconds=3 * 86400)
    assert open_keys(u.w, "m11") == {"state-mismatch": "warn"}  # weekly: not re-compared yet
    tick(u.w, u.oc, 1, seconds=4 * 86400)
    assert open_keys(u.w, "m11") == {}


def test_benign_weekly_full_state_all_match_is_silent(tmp_path):
    u = world(tmp_path)
    u.round(updates=(0,))
    u.close()
    assert pw(u.w) == [] and u.w.db.meta("check:m11:full_state")["result"]["checked"] == 3


def test_weekly_full_state_deferred_while_the_api_is_unreachable(tmp_path):
    u = world(tmp_path)
    u.round(updates=(0,))
    u.set_eb(32, leaves={u.cids[2]: 48})
    u.close()
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
    res = u.w.db.meta("check:m11:full_state") or {}
    assert not res  # no leaf at the right root: DEFERRED, not stored as "all match"


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
    saved = u.w.db.meta("check:m11:full_state")
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
    assert u.w.db.meta("check:m11:full_state")["commit_block"] != saved["commit_block"]


def test_reorg_below_the_evaluated_block_recomputes_persisted_proof_results(tmp_path):
    u = world(tmp_path)
    u.round(updates=())
    u.set_eb(32, leaves={u.cids[0]: 50})
    u.close()
    assert open_keys(u.w, "m11") == {"liveness": "page", "state-mismatch": "warn"}
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
    # comparison's mismatch was also recomputed on the new chain and resolved
    assert again["result"]["mismatches"] == [] and open_keys(u.w, "m11") == {"liveness": "warn"}
    assert sent(u.w, "m11", "kind='resolved'")[0]["ikey"] == "state-mismatch"


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
    assert isinstance(RpcError("x"), Exception)


def test_missing_updater_source_is_a_startup_error(tmp_path):
    from harness import make_worker

    with pytest.raises(SystemExit, match="checks.m11"):
        make_worker(tmp_path, only=["m11"])


def test_restart_helper_is_the_shared_one():
    assert restart.__module__ == "fixtures_oracle"


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
    assert u.w.db.meta("check:m11:full_state") is None
    assert u.w.db.conn.execute("SELECT 1 FROM outbox WHERE key LIKE 'health:proof-api-weekly%'").fetchone()
