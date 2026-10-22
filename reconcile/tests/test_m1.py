"""M1 — stale final cluster event (#91419 family): spec trigger lines, benign twins, thresholds from the chain,
restart, reorg (depth 1, depth N beyond the confirmation window, crash between detection and handling), inbox.

Run: reconcile/.venv/bin/python -m pytest -q reconcile/tests/test_m1.py
"""

from __future__ import annotations

import json

import pytest
from fakechain import _enc, _topic_addr
from fixtures_cluster import (
    INCORRECT_STATE,
    LIQUIDATOR,
    OPS,
    OPS2,
    OWNER,
    OWNER2,
    STRANGER,
    UPDATER,
    ClusterChain,
    _emit_cluster,
    current_tuple,
    deposit,
    failed_tx,
    key_of,
    m1_config,
    make_m1,
    multi_benign,
    poison,
)
from harness import VIEWS, advance, api_row, make_worker, outbox, stub_params

from checks.m1_cluster_abi import IS_LIQUIDATED
from checks.m1_stale_tuple import _tup
from eb_storage import Cluster, u256
from rpc import RpcError

pytestmark = pytest.mark.m1

KEY = key_of(OWNER, OPS)


def out(w):
    return outbox(w, "check_id='m1'")


def kinds(w):
    return [(r["severity"], r["kind"]) for r in out(w)]


def incidents(w, state=None):
    q = "SELECT * FROM incidents WHERE check_id='m1'" + (" AND state=?" if state else "") + " ORDER BY rowid"
    return [dict(r) for r in w.db.conn.execute(q, (state,) if state else ())]


def evidence(w, key=KEY):
    return json.loads(w.db.conn.execute("SELECT evidence FROM incidents WHERE check_id='m1' AND key=? "
                                        "ORDER BY rowid DESC LIMIT 1", (key,)).fetchone()[0])


def detections(w, kind=None):
    q = "SELECT * FROM m1_detections" + (" WHERE kind=?" if kind else "") + " ORDER BY id"
    return [dict(r) for r in w.db.conn.execute(q, (kind,) if kind else ())]


def views_calls(chain, block=None):
    return [c for c in chain.calls if c[0] == VIEWS and c[1].startswith("0x" + IS_LIQUIDATED.hex())
            and (block is None or c[2] == block)]


def tick(w, chain, seconds=601, mine=True):
    w.clock.advance(seconds)
    if mine:
        chain.mine()
    advance(w)


def restart(tmp_path, chain, w, **over):
    _, w2 = make_worker(tmp_path, only=["m1"], chain=chain, views=VIEWS, clock=w.clock, checks={"m1": m1_config()},
                        start_block=w.cfg.start_block, activation_block=w.cfg.activation_block, **over)
    return w2


# ---------------------------------------------------------------- trigger lines 1-3: receipts


def test_m1_poisoned_receipt_warns_with_repair_tuple(tmp_path):
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18)
    advance(w)
    tx, stale, repair = poison(ssv)
    blk = chain.sealed
    advance(w)
    assert kinds(w) == [("warn", "open")]
    ev = evidence(w)
    assert ev["poison_tx"] == tx and ev["poison_block"] == blk and ev["source"] == "receipt"
    assert ev["stale_tuple"] == _tup(stale) and ev["repair_tuple"] == _tup(repair) and ev["repair_log_index"] == 0
    assert ev["liquidatable"] is False and ev["re_poisoned"] is False
    assert views_calls(chain, blk), "the receipt is validated at its own block"
    text = out(w)[0]["text"]
    assert "Stale cluster tuple" in text and "deposit(owner, operatorIds, repairTuple)" in text
    assert out(w)[0]["delivery"] == "telegram"


def test_m1_repair_tuple_is_the_earliest_validating_log_of_the_receipt(tmp_path):
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18)
    advance(w)
    # three nested deposits: only the last nested tuple is in storage; the stale outer tuple is emitted last
    tx, stale, repair = poison(ssv, nested_n=3)
    advance(w)
    ev = evidence(w)
    assert ev["repair_tuple"] == _tup(repair) and ev["repair_log_index"] == 2 and ev["stale_tuple"] == _tup(stale)


def test_m1_multi_event_receipt_that_validates_is_silent(tmp_path):
    chain, w, ssv = make_m1(tmp_path)
    multi_benign(ssv, n=3)
    blk = chain.sealed
    advance(w)
    assert out(w) == [] and detections(w) == []
    assert len(views_calls(chain, blk)) == 1, "one validation of the last tuple, at the receipt's block"


def test_m1_single_event_transactions_are_not_validated(tmp_path):
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=1)
    deposit(ssv, value=2)
    advance(w)
    assert out(w) == [] and views_calls(chain) == []


def test_m1_same_block_poison_then_canonical_transaction_is_silent(tmp_path):
    """Same-block ordering: a later transaction of the block passed storage's tuple, so its log is what
    last-log readers keep; the superseded poisoned group is not an alert."""
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18)
    advance(w)
    poison(ssv, mine=False)
    deposit(ssv, value=0)  # same block, after the poisoning transaction
    advance(w)
    assert out(w) == [] and detections(w) == []


def test_m1_same_block_canonical_transaction_then_poison_warns(tmp_path):
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18)
    advance(w)
    deposit(ssv, value=0, mine=False)
    tx, _, _ = poison(ssv)  # same block, last
    advance(w)
    assert kinds(w) == [("warn", "open")] and evidence(w)["poison_tx"] == tx


def test_m1_two_clusters_poisoned_and_clean_in_one_block(tmp_path):
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18)
    deposit(ssv, owner=OWNER2, ops=OPS2, value=10**18)
    advance(w)
    poison(ssv, mine=False)
    multi_benign(ssv, owner=OWNER2, ops=OPS2)
    advance(w)
    assert [i["key"] for i in incidents(w, "open")] == [KEY]


# ---------------------------------------------------------------- trigger line 4: inbox reverts


def inbox_m1(tmp_path, **kw):
    path = tmp_path / "failed-tx.jsonl"
    path.write_text("")
    chain, w, ssv = make_m1(tmp_path, inbox={"path": str(path)}, **kw)
    return chain, w, ssv, path


def test_m1_inbox_incorrect_cluster_state_from_our_sender_warns(tmp_path):
    chain, w, ssv, path = inbox_m1(tmp_path)
    deposit(ssv, value=10**18)
    advance(w)
    tx = failed_tx(chain, path, "updateClusterBalance", OWNER, OPS, current_tuple(ssv), UPDATER, INCORRECT_STATE)
    advance(w)
    advance(w)
    assert kinds(w) == [("warn", "open")]
    assert evidence(w)["reverts"] == [tx]
    assert "IncorrectClusterState revert from our sender" in out(w)[0]["text"]
    # the cluster's last log validates: the next run resolves the transient revert
    tick(w, chain)
    assert kinds(w) == [("warn", "open"), ("info", "resolved")]
    assert detections(w, "revert")[0]["cleared_block"] is not None


def test_m1_inbox_liquidator_revert_on_poisoned_cluster_feeds_the_same_incident(tmp_path):
    chain, w, ssv, path = inbox_m1(tmp_path)
    deposit(ssv, value=10**18)
    advance(w)
    _, stale, _ = poison(ssv)
    advance(w)
    tx = failed_tx(chain, path, "liquidate", OWNER, OPS, stale, LIQUIDATOR, INCORRECT_STATE)
    advance(w)
    advance(w)
    assert len(incidents(w)) == 1
    assert kinds(w) == [("warn", "open"), ("warn", "update")]
    assert evidence(w)["reverts"] == [tx]
    tick(w, chain)
    assert len(incidents(w, "open")) == 1, "the poisoning persists, so the revert stays explained and open"


@pytest.mark.parametrize("case", ["stranger", "other_error", "other_function"])
def test_m1_inbox_revert_benign_twins_are_silent(tmp_path, case):
    chain, w, ssv, path = inbox_m1(tmp_path)
    deposit(ssv, value=10**18)
    advance(w)
    sender, revert, fn = UPDATER, INCORRECT_STATE, "updateClusterBalance"
    if case == "stranger":
        sender = STRANGER
    elif case == "other_error":
        revert = "0x95a0cf33"  # ClusterIsLiquidated()
    else:
        fn = "migrateClusterToETH"  # not one of M1's inbox_functions
    failed_tx(chain, path, fn, OWNER, OPS, current_tuple(ssv), sender, revert)
    advance(w)
    advance(w)
    assert out(w) == [] and detections(w) == []


def test_m1_inbox_revert_without_decodable_cluster_is_reported_once(tmp_path):
    chain, w, ssv, path = inbox_m1(tmp_path)
    advance(w)
    chain.stub_call("0xdd9bc35ae942ef0cfa76930954a156b3ff30a4e1", "0xbc26e7e5", revert=INCORRECT_STATE)
    chain.mine()
    tx = chain.add_tx(UPDATER, "0xdd9bc35ae942ef0cfa76930954a156b3ff30a4e1", "0xbc26e7e5")
    blk = chain.number
    chain.mine()
    path.write_text(json.dumps({"schema": 1, "tx_hash": tx, "block": blk, "block_hash": chain.hash(blk),
                                "from": UPDATER, "to": "0xdd9bc35ae942ef0cfa76930954a156b3ff30a4e1",
                                "selector": "0xbc26e7e5", "function": "liquidate(address,uint64[],(uint32,uint64,"
                                "uint64,bool,uint256))", "status": "failure", "monitor": "SSV Failed Tx Inbox",
                                "observed_at": "2026-10-21T00:00:00Z"}) + "\n")
    advance(w)
    advance(w)
    assert kinds(w) == [("warn", "open")] and incidents(w)[0]["key"] == f"tx:{tx}"
    tick(w, chain)
    assert kinds(w) == [("warn", "open"), ("info", "resolved")]


# ---------------------------------------------------------------- trigger line 5 and severity: re-poison, liquidatable


def test_m1_re_poison_after_repair_pages(tmp_path):
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18)
    advance(w)
    poison(ssv)
    advance(w)
    deposit(ssv, value=0)  # the runbook's zero-value deposit with the repair tuple
    advance(w)
    assert kinds(w) == [("warn", "open"), ("info", "resolved")]
    poison(ssv)
    advance(w)
    assert kinds(w) == [("warn", "open"), ("info", "resolved"), ("page", "open")]
    ev = evidence(w)
    assert ev["re_poisoned"] is True
    assert "re-poisoned after a repair" in out(w)[-1]["text"]


def test_m1_second_poisoning_without_repair_is_not_a_re_poison(tmp_path):
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18)
    advance(w)
    poison(ssv)
    advance(w)
    tx2, _, _ = poison(ssv)
    advance(w)
    assert kinds(w) == [("warn", "open"), ("warn", "update")]
    assert evidence(w)["poison_tx"] == tx2 and evidence(w)["re_poisoned"] is False


def test_m1_repair_and_re_poison_in_one_batch_pages(tmp_path):
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18)
    advance(w)
    poison(ssv)
    deposit(ssv, value=0)
    poison(ssv)
    advance(w)
    assert kinds(w) == [("page", "open")]
    assert [d["kind"] for d in detections(w)] == ["poison", "repair", "poison"]


def test_m1_poisoned_and_liquidatable_pages(tmp_path):
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18)
    advance(w)
    chain.burn[KEY] = 10**18  # liquidationThresholdPeriod x burn far above the balance
    poison(ssv)
    advance(w)
    assert kinds(w) == [("page", "open")]
    assert evidence(w)["liquidatable"] is True and "liquidatable" in out(w)[0]["text"]


def test_m1_thresholds_from_chain_liquidation_period_change_escalates_to_page(tmp_path):
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18)
    advance(w)
    chain.burn[KEY] = 10**16  # 42,960 x 1e16 < balance (~1e21) < 429,600 x 1e16
    poison(ssv)
    advance(w)
    assert kinds(w) == [("warn", "open")] and evidence(w)["liquidatable"] is False
    stub_params(chain, liquidation_threshold_period=429_600)
    chain.emit_event("LiquidationThresholdPeriodUpdated", [], u256(429_600))
    chain.mine()
    advance(w)
    assert kinds(w) == [("warn", "open"), ("page", "escalate")] and evidence(w)["liquidatable"] is True


# ---------------------------------------------------------------- inventory: unknown status, first sweep


def api_m1(tmp_path, rows, **kw):
    chain, w, ssv = make_m1(tmp_path, inventory={"api_url": "https://api.invalid", "api_refresh_s": 0}, **kw)
    w.inventory.http_get = lambda url: {"clusters": list(rows), "pagination": {"pages": 1}}
    return chain, w, ssv


@pytest.mark.parametrize("valid", [False, True])
def test_m1_inventory_hash_mismatch_is_unknown_warn_and_valid_api_tuple_is_silent(tmp_path, valid):
    chain = ClusterChain()
    from fakechain import MiniSSV

    ssv = MiniSSV(chain)
    cid, st = ssv._get(OWNER2, OPS2)  # in storage, never logged since start
    st["count"] = 2
    ssv._persist(cid, st)
    chain.mine()
    cl = current_tuple(ssv, OWNER2, OPS2)
    rows = [api_row(OWNER2, OPS2, 2, cl.network_fee_index, cl.index, True, cl.balance + (0 if valid else 1))]
    chain, w, _ = api_m1(tmp_path, rows, chain=chain, ssv=ssv)
    advance(w)
    if valid:
        assert out(w) == []
    else:
        assert kinds(w) == [("warn", "open")]
        assert "unknown" in evidence(w, key_of(OWNER2, OPS2))
        rows[0] = api_row(OWNER2, OPS2, 2, cl.network_fee_index, cl.index, True, cl.balance)
        tick(w, chain)
        assert kinds(w) == [("warn", "open"), ("info", "resolved")]


def test_m1_first_sweep_after_start_finds_old_poison_outside_the_receipt_window(tmp_path):
    chain = ClusterChain()
    from fakechain import MiniSSV

    ssv = MiniSSV(chain)
    chain.mine()
    start = chain.number
    deposit(ssv, value=10**18)
    poison(ssv)
    old = chain.sealed
    chain.mine(30)
    chain, w, _ = make_m1(tmp_path, chain=chain, ssv=ssv, m1={"receipt_window_blocks": 5}, start_block=start,
                          activation_block=start)
    advance(w)
    assert views_calls(chain, old) == [], "outside the window the receipt is not validated at its block"
    assert kinds(w) == [("warn", "open")]
    ev = evidence(w)
    assert ev["source"] == "state" and ev["poison_block"] == old and ev["repair_tuple"] is None


# ---------------------------------------------------------------- restart


def test_m1_restart_does_not_duplicate_the_open_incident(tmp_path):
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18)
    advance(w)
    poison(ssv)
    advance(w)
    w2 = restart(tmp_path, chain, w)
    chain.mine()
    advance(w2)
    w2.clock.advance(601)
    advance(w2)
    assert kinds(w2) == [("warn", "open")] and len(incidents(w2)) == 1
    assert len(detections(w2, "poison")) == 1


def test_m1_restart_between_scan_and_run_detects_once(tmp_path):
    """Crash after the scan stored the poisoning logs and before the check ran: nothing of the run was committed,
    the restarted worker delivers the events again and alerts once."""
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18)
    advance(w)
    poison(ssv)
    w.check_reorg()
    w.scan()
    w2 = restart(tmp_path, chain, w)
    advance(w2)
    advance(w2)
    assert kinds(w2) == [("warn", "open")] and len(detections(w2, "poison")) == 1


# ---------------------------------------------------------------- reorg (README §6.1/§6.2)


def test_m1_reorg_depth_1_removing_the_poisoning_block_resolves(tmp_path):
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18)
    advance(w)
    saved = dict(ssv._get(OWNER, OPS)[1])
    poison(ssv)
    fork = chain.sealed
    advance(w)
    assert kinds(w) == [("warn", "open")]
    chain.reorg(fork)
    ssv._get(OWNER, OPS)[1].update(saved)
    chain.mine(2)
    advance(w)
    assert [i["state"] for i in incidents(w)] == ["resolved"]
    assert kinds(w) == [("warn", "open"), ("info", "resolved")]
    assert "validates against storage" in out(w)[-1]["text"]
    assert detections(w) == []


def test_m1_reorg_depth_n_beyond_the_confirmation_window_resolves(tmp_path):
    chain, w, ssv = make_m1(tmp_path, confirmations=3)
    deposit(ssv, value=10**18)
    chain.mine(4)
    advance(w)
    saved = dict(ssv._get(OWNER, OPS)[1])
    poison(ssv)
    fork = chain.sealed
    chain.mine(8)
    advance(w)
    assert kinds(w) == [("warn", "open")]
    assert w.checkpoint()["block"] - fork + 1 > 3, "the reorg reaches below the checkpoint's confirmation depth"
    chain.reorg(fork)
    ssv._get(OWNER, OPS)[1].update(saved)
    chain.mine(12)
    advance(w)
    assert [i["state"] for i in incidents(w)] == ["resolved"] and detections(w) == []


def test_m1_reorg_depth_n_reincluded_poison_stays_open_without_a_new_alert(tmp_path):
    chain, w, ssv = make_m1(tmp_path, confirmations=2)
    deposit(ssv, value=10**18)
    chain.mine(3)
    advance(w)
    saved = dict(ssv._get(OWNER, OPS)[1])
    poison(ssv)
    fork = chain.sealed
    chain.mine(6)
    advance(w)
    assert kinds(w) == [("warn", "open")]
    chain.reorg(fork)
    ssv._get(OWNER, OPS)[1].update(saved)
    chain.mine(2)
    poison(ssv)  # the same poisoning, re-included two blocks later on the new chain
    chain.mine(6)
    advance(w)
    assert [i["state"] for i in incidents(w)] == ["open"]
    assert kinds(w) == [("warn", "open"), ("warn", "update")]  # new tx hash/position: revision re-sent
    assert len(detections(w, "poison")) == 1 and detections(w, "poison")[0]["block"] > fork


def test_m1_reorg_above_the_poisoning_keeps_the_incident_open(tmp_path):
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18)
    advance(w)
    poison(ssv)
    chain.mine(3)
    advance(w)
    fork = chain.sealed
    chain.mine()
    advance(w)
    chain.reorg(fork)
    chain.mine(2)
    advance(w)
    assert [i["state"] for i in incidents(w)] == ["open"] and kinds(w) == [("warn", "open")]
    assert w.db.meta("reorg:m1") is None, "the reorg run committed"


def test_m1_reorg_removing_the_repair_reopens_the_surviving_poison(tmp_path):
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18)
    advance(w)
    poison(ssv)
    advance(w)
    saved = dict(ssv._get(OWNER, OPS)[1])
    deposit(ssv, value=0)
    fork = chain.sealed
    advance(w)
    assert kinds(w) == [("warn", "open"), ("info", "resolved")]
    chain.reorg(fork)
    ssv._get(OWNER, OPS)[1].update(saved)
    chain.mine(2)
    advance(w)
    assert kinds(w) == [("warn", "open"), ("info", "resolved"), ("warn", "open")]
    assert [d["kind"] for d in detections(w)] == ["poison"]


def test_m1_crash_between_the_rewind_and_the_reorg_run_resolves_after_restart(tmp_path):
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18)
    advance(w)
    saved = dict(ssv._get(OWNER, OPS)[1])
    poison(ssv)
    fork = chain.sealed
    advance(w)
    chain.reorg(fork)
    ssv._get(OWNER, OPS)[1].update(saved)
    chain.mine(2)
    w.check_reorg()  # rewinds and writes the durable reorg marker, then the process dies
    w.scan()
    assert w.db.meta("reorg:m1") is not None
    w2 = restart(tmp_path, chain, w)
    advance(w2)
    assert [i["state"] for i in incidents(w2)] == ["resolved"] and detections(w2) == []
    assert kinds(w2) == [("warn", "open"), ("info", "resolved")]


def test_m1_inbox_revert_of_an_orphaned_block_is_dropped(tmp_path):
    chain, w, ssv, path = inbox_m1(tmp_path)
    deposit(ssv, value=10**18)
    chain.mine()
    advance(w)
    _, stale, _ = poison(ssv)
    advance(w)
    fork = chain.number
    failed_tx(chain, path, "liquidate", OWNER, OPS, stale, LIQUIDATOR, INCORRECT_STATE)
    advance(w)
    advance(w)
    assert len(detections(w, "revert")) == 1
    chain.reorg(fork)
    chain.mine(2)
    advance(w)
    assert detections(w, "revert") == [], "the entry's block left the canonical chain"
    assert [i["state"] for i in incidents(w)] == ["open"], "the poisoning itself survives"


def test_m1_reorg_run_that_cannot_evaluate_raises_and_keeps_the_incident(tmp_path):
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18)
    advance(w)
    poison(ssv)
    chain.mine(2)
    advance(w)
    fork = chain.sealed
    chain.reorg(fork)
    chain.mine(2)
    chain.views_down = True
    with pytest.raises(RpcError):
        advance(w)
    assert w.db.meta("reorg:m1") is not None and [i["state"] for i in incidents(w)] == ["open"]
    chain.views_down = False
    advance(w)
    assert w.db.meta("reorg:m1") is None and [i["state"] for i in incidents(w)] == ["open"]
    assert kinds(w) == [("warn", "open")]


# ---------------------------------------------------------------- errors are never "clean"


def test_m1_rpc_error_on_the_receipt_validation_raises_and_detects_after_recovery(tmp_path):
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18)
    advance(w)
    chain.views_down = True
    poison(ssv)
    with pytest.raises(RpcError):
        advance(w)
    assert out(w) == [] and detections(w) == []
    chain.views_down = False
    advance(w)
    assert kinds(w) == [("warn", "open")]


@pytest.mark.parametrize("failure", ["views_down", "views_empty"])
def test_m1_failed_or_empty_validation_never_resolves(tmp_path, failure):
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18)
    advance(w)
    poison(ssv)
    advance(w)
    setattr(chain, failure, True)
    w.clock.advance(601)
    chain.mine()
    with pytest.raises(RpcError):
        advance(w)
    assert [i["state"] for i in incidents(w)] == ["open"] and kinds(w) == [("warn", "open")]


def test_m1_missing_required_settings_are_config_errors(tmp_path):
    chain = ClusterChain()
    chain.mine()
    with pytest.raises(SystemExit, match="views"):
        make_worker(tmp_path / "a", only=["m1"], chain=chain, checks={"m1": m1_config()})
    with pytest.raises(SystemExit, match="our_senders"):
        make_worker(tmp_path / "b", only=["m1"], chain=chain, views=VIEWS, checks={"m1": {}})
    with pytest.raises(SystemExit, match="inbox_functions"):
        make_worker(tmp_path / "c", only=["m1"], chain=chain, views=VIEWS,
                    checks={"m1": m1_config(inbox_functions=["deposit"])})


def test_m1_unresolved_inbox_revert_of_our_sender_is_health_not_clean(tmp_path):
    chain, w, ssv, path = inbox_m1(tmp_path)
    deposit(ssv, value=10**18)
    advance(w)
    data_tx = failed_tx(chain, path, "updateClusterBalance", OWNER, OPS, current_tuple(ssv), UPDATER, INCORRECT_STATE)
    chain.call_stubs.clear()  # the replay at block - 1 has no answer and there is no Blockscout: unresolved
    stub_params(chain)
    for _ in range(6):
        chain.mine()
        advance(w)
    row = w.db.conn.execute("SELECT revert_source FROM inbox WHERE tx_hash=?", (data_tx,)).fetchone()
    assert row["revert_source"] == "unresolved"
    assert outbox(w, "kind='health' AND check_id='m1' AND text LIKE '%could not be resolved%'")
    assert [r for r in out(w) if r["kind"] != "health"] == [] and detections(w) == []


def test_m1_inbox_transaction_lookup_returning_nothing_raises(tmp_path):
    chain, w, ssv, path = inbox_m1(tmp_path)
    deposit(ssv, value=10**18)
    advance(w)
    tx = failed_tx(chain, path, "liquidate", OWNER, OPS, current_tuple(ssv), LIQUIDATOR, INCORRECT_STATE)
    w.check_reorg()
    w.scan()
    w.poll_inbox()
    assert w.db.conn.execute("SELECT revert_name FROM inbox WHERE tx_hash=?", (tx,)).fetchone()[0] == \
        "IncorrectClusterState"
    del chain.txs[tx]
    del chain.senders[tx]
    with pytest.raises(RpcError, match="returned nothing"):
        w.run_checks()
    assert detections(w) == [] and w.db.meta("cursor:inbox:m1", 0) == 0


def test_m1_inventory_failure_is_health_and_receipts_are_still_validated(tmp_path):
    chain, w, ssv = make_m1(tmp_path, inventory={"api_url": "https://api.invalid", "api_refresh_s": 0})

    def down(url):
        raise OSError("api down")

    w.inventory.http_get = down
    deposit(ssv, value=10**18)
    poison(ssv)
    advance(w)
    assert outbox(w, "kind='health' AND check_id='m1' AND text LIKE '%inventory could not be built%'")
    assert [(r["severity"], r["kind"]) for r in out(w) if r["kind"] != "health"] == [("warn", "open")]


# ---------------------------------------------------------------- QA attempt 1 regressions (repair 1)


def test_m1_same_block_repair_then_re_poison_pages(tmp_path):
    """QA F2: a repair followed by a re-poisoning in one block keeps both, in log order: page under the incident."""
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18)
    advance(w)
    poison(ssv)
    advance(w)
    deposit(ssv, value=0, mine=False)
    poison(ssv)
    advance(w)
    assert kinds(w) == [("warn", "open"), ("page", "escalate")]
    assert evidence(w)["re_poisoned"] is True and len(incidents(w)) == 1
    assert [d["kind"] for d in detections(w)] == ["poison", "repair", "poison"]
    rep, pois = detections(w)[1:]
    assert rep["block"] == pois["block"] and rep["log_index"] < pois["log_index"]


def test_m1_same_block_bulk_repair_then_re_poison_pages(tmp_path):
    """A multi-event transaction whose events all carry one tuple wrote and emitted it: a repair, even mid-block."""
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18)
    advance(w)
    poison(ssv)
    advance(w)
    multi_benign(ssv, n=3, mine=False)
    poison(ssv)
    advance(w)
    assert kinds(w)[-1] == ("page", "escalate") and evidence(w)["re_poisoned"] is True


def test_m1_same_block_unreadable_transaction_before_a_poisoning_pages_as_unverified_re_poison(tmp_path):
    """Repair 1 reported this as health (warn kept); QA attempt 2 F2 and the coordinator ruling: a transaction with
    differing tuples between a recorded poisoning and a new one cannot be validated at block granularity, so it is
    read as a possible repair and the poisoning pages (unverified), never health only."""
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18)
    advance(w)
    poison(ssv)
    advance(w)
    first, _, _ = poison(ssv, mine=False)
    poison(ssv)
    advance(w)
    assert kinds(w)[-1] == ("page", "escalate")
    ev = evidence(w)
    assert ev["re_poisoned"] is True and ev["re_poison_unverified"] is True
    assert [t["tx"] for t in ev["intra_block"]] == [first]
    assert not outbox(w, "kind='health' AND key LIKE 'health:m1-intra-block:%'")


def api_only_key(tmp_path, api_valid=False, after_fork=False):
    """A cluster in storage with no stored log; the SSV API lists it (tuple valid or not)."""
    chain = ClusterChain()
    from fakechain import MiniSSV

    ssv = MiniSSV(chain)
    chain.mine()
    rows: list = []
    chain, w, _ = api_m1(tmp_path, rows, chain=chain, ssv=ssv)
    chain.mine()
    advance(w)
    fork = chain.number
    cid, st = ssv._get(OWNER2, OPS2)
    st["count"] = 2
    ssv._persist(cid, st)
    chain.mine()
    cl = current_tuple(ssv, OWNER2, OPS2)
    rows.append(api_row(OWNER2, OPS2, 2, cl.network_fee_index, cl.index, True, cl.balance + (0 if api_valid else 1)))
    return chain, w, ssv, rows, fork, cl


@pytest.mark.parametrize("answer", ["empty", "error"])
def test_m1_reorg_with_empty_or_failed_inventory_keeps_the_unknown_key_and_marker(tmp_path, answer):
    """QA F1 (M1): an empty API answer is no proof that an API-only cluster recovered; the reorg run raises (marker
    kept, incident kept) until a tuple is known again."""
    chain, w, ssv, rows, _, cl = api_only_key(tmp_path)
    tick(w, chain)
    key2 = key_of(OWNER2, OPS2)
    assert [i["key"] for i in incidents(w, "open")] == [key2]
    fork = chain.number
    chain.mine(3)
    tick(w, chain)
    chain.reorg(fork)
    chain.mine(2)
    good = w.inventory.http_get
    if answer == "empty":
        saved = list(rows)
        rows.clear()
    else:
        def down(url):
            raise OSError("api down")

        w.inventory.http_get = down
    advance(w)
    assert outbox(w, "kind='health' AND key LIKE 'health:check-error:%' AND check_id='m1' AND text LIKE "
                     "'%cannot re-evaluate%'")
    assert [i["key"] for i in incidents(w, "open")] == [key2] and w.db.meta("reorg:m1") is not None
    # the API answers again with the canonical tuple: the key is evaluated and resolves on positive proof
    if answer == "empty":
        rows.extend(saved)
    w.inventory.http_get = good
    rows[0] = api_row(OWNER2, OPS2, 2, cl.network_fee_index, cl.index, True, cl.balance)
    w.inventory._api_at = None
    chain.mine()  # the inventory is cached per block
    advance(w)
    assert w.db.meta("reorg:m1") is None and incidents(w, "open") == []


def test_m1_empty_inventory_outside_a_reorg_keeps_the_unknown_incident(tmp_path):
    chain, w, ssv, rows, _, _ = api_only_key(tmp_path)
    tick(w, chain)
    rows.clear()
    tick(w, chain)
    tick(w, chain)
    assert len(incidents(w, "open")) == 1 and [k for k in kinds(w) if k[1] != "health"] == [("warn", "open")]
    assert outbox(w, "kind='health' AND key LIKE 'health:m1-inventory-missing%'")


def test_m1_cluster_absent_from_storage_resolves_with_an_empty_inventory(tmp_path):
    """Twin: storage proves absence (the cluster was written only on orphaned blocks), API rows are not needed."""
    chain, w, ssv, rows, fork, _ = api_only_key(tmp_path)
    tick(w, chain)
    assert len(incidents(w, "open")) == 1
    chain.reorg(fork)
    chain.mine(2)
    rows.clear()
    advance(w)
    assert incidents(w, "open") == [] and w.db.meta("reorg:m1") is None


def test_m1_earliest_repair_tuple_before_66_stale_snapshots(tmp_path):
    """QA F4: the validating tuple at log 0 is found however many distinct stale snapshots follow it, with one
    storage read and one confirming call (no candidate cap)."""
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18)
    advance(w)
    canonical = current_tuple(ssv)
    tx = chain.new_tx(OWNER)
    _emit_cluster(chain, tx, "ClusterDeposited", OWNER, OPS, canonical)
    for delta in range(1, 67):
        stale = Cluster(canonical.validator_count, canonical.network_fee_index, canonical.index, True,
                        canonical.balance + delta)
        _emit_cluster(chain, tx, "ClusterDeposited", OWNER, OPS, stale)
    chain.mine()
    blk = chain.sealed
    advance(w)
    ev = evidence(w)
    assert kinds(w) == [("warn", "open")]
    assert ev["repair_tuple"] == _tup(canonical) and ev["repair_log_index"] == 0
    assert len(views_calls(chain, blk)) == 3, "final tuple, repair tuple, the run's state check: no candidate scan"


def test_m1_reorg_orphaning_the_clearing_run_reopens_the_surviving_revert(tmp_path):
    """README §6.2 for every persisted key: a revert cleared on a deleted block is evaluated again."""
    chain, w, ssv, path = inbox_m1(tmp_path)
    deposit(ssv, value=10**18)
    advance(w)
    _, stale, _ = poison(ssv)
    advance(w)
    tx = failed_tx(chain, path, "liquidate", OWNER, OPS, stale, LIQUIDATOR, INCORRECT_STATE)
    advance(w)
    advance(w)
    saved = dict(ssv._get(OWNER, OPS)[1])
    fork = chain.number
    deposit(ssv, value=0)  # the repair clears the poisoning and the revert
    advance(w)
    assert incidents(w, "open") == [] and detections(w, "revert")[0]["cleared_block"] is not None
    chain.reorg(fork)
    ssv._get(OWNER, OPS)[1].update(saved)
    chain.mine(2)
    advance(w)
    assert detections(w, "revert")[0]["cleared_block"] is None
    assert len(incidents(w, "open")) == 1 and evidence(w)["reverts"] == [tx]


@pytest.mark.parametrize("kind", ["ClusterDeposited", "ClusterWithdrawn", "ClusterLiquidated", "ClusterReactivated",
                                  "ValidatorAdded", "ValidatorRemoved", "ClusterBalanceUpdated",
                                  "ClusterMigratedToETH"])
def test_m1_repair_tuple_tie_is_the_earliest_log_for_every_event_kind(tmp_path, kind):
    """QA attempt 1 independent case: three logs carry the validating tuple, a stale one of any kind is last, and
    the RPC delivers the logs in reverse: the repair is log 0."""
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18)
    advance(w)
    canonical = current_tuple(ssv)
    stale = Cluster(canonical.validator_count, canonical.network_fee_index, canonical.index, canonical.active,
                    canonical.balance + 17)
    tx = chain.new_tx(OWNER)
    for _ in range(3):
        _emit_cluster(chain, tx, "ClusterDeposited", OWNER, OPS, canonical)
    if kind == "ClusterBalanceUpdated":
        chain.emit(tx, kind, [_topic_addr(OWNER), "0x" + u256(chain.number).hex()],
                   _enc(["uint64[]", "uint32", "cluster"], [list(OPS), 32, stale]))
    elif kind == "ClusterMigratedToETH":
        chain.emit(tx, kind, [_topic_addr(OWNER)], _enc(["uint64[]", "uint256", "uint256", "uint32", "cluster"],
                                                         [list(OPS), 0, 0, 32, stale]))
    else:
        _emit_cluster(chain, tx, kind, OWNER, OPS, stale)
    chain.logs.reverse()
    chain.mine()
    advance(w)
    assert kinds(w) == [("warn", "open")]
    assert evidence(w)["repair_tuple"] == _tup(canonical) and evidence(w)["repair_log_index"] == 0


# ---------------------------------------------------------------- QA attempt 2 regressions (repair 2)


def api_row_now(ssv, owner, ops, delta=0):
    """The API row of the cluster's tuple as MiniSSV holds it now (`delta` makes its hash wrong)."""
    cl = current_tuple(ssv, owner, ops)
    return api_row(owner, ops, cl.validator_count, cl.network_fee_index, cl.index, cl.active, cl.balance + delta)


def two_deposit_helper(ssv, owner=OWNER, ops=OPS, mine=False):
    """A successful helper contract calling deposit twice in one transaction: two tuples, the second canonical."""
    cid, st = ssv._get(owner, ops)
    tx = ssv.c.new_tx(owner)
    for value in (3, 5):
        ssv._touch(st)
        st["balance"] += value
        ssv._persist(cid, st)
        _emit_cluster(ssv.c, tx, "ClusterDeposited", owner, ops, ssv._cluster(st), value)
    if mine:
        ssv.c.mine()
    return tx


@pytest.mark.parametrize("restart_now", [False, True])
def test_m1_reorg_orphaning_the_resolution_of_an_api_only_key_with_a_partial_api_raises(tmp_path, restart_now):
    """QA attempt 2 F1 (M1): a resolved key whose resolution came from an orphaned block is re-evaluated in the reorg
    run; an API answer listing only an unrelated cluster is partial, no proof: the run raises, the marker stays (also
    across a restart between the rewind and the run); a complete answer then reopens the unknown key."""
    chain, w, ssv, rows, _, cl = api_only_key(tmp_path)
    tick(w, chain)
    key2 = key_of(OWNER2, OPS2)
    assert [i["key"] for i in incidents(w, "open")] == [key2]
    cid, st = ssv._get(OWNER, OPS)
    st["count"] = 1
    ssv._persist(cid, st)
    chain.mine()
    unrelated = api_row_now(ssv, OWNER, OPS)
    fork = chain.number
    cid2, st2 = ssv._get(OWNER2, OPS2)
    st2["balance"] += 9
    ssv._persist(cid2, st2)
    rows[0] = api_row_now(ssv, OWNER2, OPS2)
    tick(w, chain)
    assert incidents(w, "open") == [] and incidents(w, "resolved")[0]["resolved_block"] >= fork
    chain.reorg(fork)
    st2["balance"] -= 9
    chain.mine(2)
    rows[:] = [unrelated]
    if restart_now:
        w.check_reorg()
        w.scan()
        w = restart(tmp_path, chain, w, inventory={"api_url": "https://api.invalid", "api_refresh_s": 0})
        w.inventory.http_get = lambda url: {"clusters": list(rows), "pagination": {"pages": 1}}
    advance(w)
    assert w.db.meta("reorg:m1") is not None
    assert outbox(w, "kind='health' AND key LIKE 'health:check-error:%' AND check_id='m1' AND text LIKE "
                     "'%cannot re-evaluate%'")
    # the complete answer lists the canonical (wrong-hash) tuple: positive evidence, the unknown key reopens
    rows.append(api_row(OWNER2, OPS2, 2, cl.network_fee_index, cl.index, True, cl.balance + 1))
    w.inventory._api_at = None
    chain.mine()
    advance(w)
    assert w.db.meta("reorg:m1") is None and [i["key"] for i in incidents(w, "open")] == [key2]


def test_m1_reorg_of_a_resolved_key_with_logs_reopens_without_the_inventory(tmp_path):
    """Twin: a resolved key with a stored log needs no API; the reorg run re-evaluates it from the chain alone."""
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18)
    advance(w)
    poison(ssv)
    advance(w)
    saved = dict(ssv._get(OWNER, OPS)[1])
    fork = chain.number
    deposit(ssv, value=0)
    advance(w)
    assert incidents(w, "open") == []
    chain.reorg(fork)
    ssv._get(OWNER, OPS)[1].update(saved)
    chain.mine(2)
    advance(w)
    assert w.db.meta("reorg:m1") is None and [i["key"] for i in incidents(w, "open")] == [KEY]


@pytest.mark.parametrize("initial_poison", [False, True])
def test_m1_three_transactions_poison_repair_poison_in_one_block_pages(tmp_path, initial_poison):
    """QA attempt 2 F3: poison, single-tuple repair, poison in one block (logs delivered reversed); each transaction
    is kept at its position and the re-poison pages, with or without a poisoning open before the block."""
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18)
    advance(w)
    if initial_poison:
        poison(ssv)
        advance(w)
    first, _, _ = poison(ssv, mine=False)
    rep, _ = deposit(ssv, mine=False)
    last, _, repair = poison(ssv)
    chain.logs.reverse()
    advance(w)
    ev = evidence(w)
    assert ev["repair_tuple"] == _tup(repair) and ev["poison_tx"] == last
    assert ev["re_poisoned"] is True and incidents(w, "open")[0]["severity"] == "page"
    assert [(t["tx"], t["class"]) for t in ev["intra_block"]] == [(first, "unverified"), (rep, "clean")]
    assert ev["re_poison_unverified"] is (not initial_poison)


@pytest.mark.parametrize("third", ["poison", "repair"])
def test_m1_unverified_two_tuple_transaction_after_a_poisoning_then_poison_pages(tmp_path, third):
    """QA attempt 2 F2, coordinator ruling: after a recorded poisoning, a block whose earlier transactions carry
    differing tuples (cannot be validated at block granularity, no trace) followed by a poisoning pages under the
    existing incident, marked 'unverified intra-block re-poison (no trace)'; a block-end state that validates
    resolves it plainly (twin)."""
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18)
    advance(w)
    poison(ssv)
    advance(w)
    helper = two_deposit_helper(ssv)
    second, _, _ = poison(ssv, mine=False)
    if third == "poison":
        poison(ssv)
    else:
        deposit(ssv)
    advance(w)
    if third == "repair":
        assert incidents(w, "open") == [] and kinds(w) == [("warn", "open"), ("info", "resolved")]
        return
    ev = evidence(w)
    assert kinds(w) == [("warn", "open"), ("page", "escalate")] and len(incidents(w)) == 1
    assert ev["re_poisoned"] is True and ev["re_poison_unverified"] is True
    assert ev["note"] == "unverified intra-block re-poison (no trace)"
    assert [t["tx"] for t in ev["intra_block"]] == [helper, second]
    assert all(len(t["tuples"]) == 2 for t in ev["intra_block"])
    assert "unverified intra-block re-poison (no trace)" in out(w)[-1]["text"]
    assert not outbox(w, "kind='health' AND key LIKE 'health:m1-intra-block%'"), "never downgraded to health"


def test_m1_unverified_transaction_before_a_first_poisoning_stays_warn(tmp_path):
    """Benign twin: no poisoning or incident before the block, one unverifiable transaction, then the poisoning: no
    poison-repair-poison reading exists, so warn."""
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18)
    advance(w)
    two_deposit_helper(ssv)
    poison(ssv)
    advance(w)
    assert kinds(w) == [("warn", "open")] and evidence(w)["re_poisoned"] is False


def test_m1_unverified_re_poison_page_survives_restart_and_resolves_on_a_validating_block(tmp_path):
    """The unverified page is persisted with the poisoning (not re-derived from the block): a restart and later runs
    keep it at page; a later block-end validation resolves it plainly."""
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18)
    advance(w)
    poison(ssv)
    advance(w)
    poison(ssv, mine=False)
    poison(ssv)
    advance(w)
    assert incidents(w, "open")[0]["severity"] == "page"
    w = restart(tmp_path, chain, w)
    tick(w, chain)
    tick(w, chain)
    assert incidents(w, "open")[0]["severity"] == "page" and kinds(w)[-1] == ("page", "escalate")
    deposit(ssv)
    advance(w)
    assert incidents(w, "open") == [] and kinds(w)[-1] == ("info", "resolved")


def test_m1_reorg_removing_the_unverified_re_poison_block_returns_to_warn(tmp_path):
    """README §6.2: the page came from a deleted block; the surviving poisoning is reported again at warn."""
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18)
    advance(w)
    poison(ssv)
    advance(w)
    saved = dict(ssv._get(OWNER, OPS)[1])
    fork = chain.number
    poison(ssv, mine=False)
    poison(ssv)
    advance(w)
    assert incidents(w, "open")[0]["severity"] == "page"
    chain.reorg(fork)
    ssv._get(OWNER, OPS)[1].update(saved)
    chain.mine(2)
    advance(w)
    assert w.db.meta("reorg:m1") is None
    assert incidents(w, "open")[0]["severity"] == "warn" and evidence(w)["re_poisoned"] is False


def test_m1_poison_repaired_before_the_run_still_alerts(tmp_path):
    """Events between runs: a poisoning repaired in a later block of the same run is reported (warn), then resolved
    at the next run; a re-poison repaired within one run pages."""
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18)
    advance(w)
    tx, _, _ = poison(ssv)
    deposit(ssv)
    advance(w)
    assert kinds(w) == [("warn", "open")] and evidence(w)["poison_tx"] == tx and evidence(w)["transient"] is True
    tick(w, chain)
    assert kinds(w) == [("warn", "open"), ("info", "resolved")]
    tick(w, chain)
    assert len(kinds(w)) == 2, "reported once"
    poison(ssv)
    deposit(ssv)
    advance(w)
    assert kinds(w)[-1] == ("page", "open") and evidence(w)["re_poisoned"] is True


def test_m1_poison_repaired_before_the_run_is_not_re_alerted_after_a_reorg_re_detects_it(tmp_path):
    """A reorg re-includes the same two transactions (same hashes): the re-detected transient poisoning was already
    reported, so the reorg run sends nothing new."""
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18)
    advance(w)
    saved = dict(ssv._get(OWNER, OPS)[1])
    fork = chain.number
    tx, _, _ = poison(ssv)
    rep, _ = deposit(ssv)
    advance(w)
    tick(w, chain)
    assert kinds(w) == [("warn", "open"), ("info", "resolved")]
    chain.reorg(fork)
    ssv._get(OWNER, OPS)[1].update(saved)
    new_tx, hashes = chain.new_tx, iter([tx, rep])
    chain.new_tx = lambda sender: next(hashes)
    poison(ssv)
    deposit(ssv)
    chain.new_tx = new_tx
    chain.mine()
    advance(w)
    assert w.db.meta("reorg:m1") is None and kinds(w) == [("warn", "open"), ("info", "resolved")]
    assert [d["kind"] for d in detections(w)] == ["poison", "repair"]


@pytest.mark.parametrize("api", ["empty", "listed"])
def test_m1_initial_empty_inventory_is_health_never_a_clean_sweep(tmp_path, api):
    """QA attempt 2 F4: an empty first API inventory is UNKNOWN (health), never a clean full-state sweep; a listed
    cluster (twin) gives no such health."""
    chain = ClusterChain()
    from fakechain import MiniSSV

    ssv = MiniSSV(chain)
    cid, st = ssv._get(OWNER2, OPS2)
    st["count"] = 9
    ssv._persist(cid, st)
    chain.mine()
    rows = [] if api == "empty" else [api_row_now(ssv, OWNER2, OPS2)]
    chain, w, ssv = api_m1(tmp_path, rows, chain=chain, ssv=ssv)
    advance(w)
    health = outbox(w, "kind='health' AND check_id='m1' AND key LIKE 'health:m1-inventory-empty%'")
    assert bool(health) is (api == "empty")
    assert w.checks["m1"].last_sweep["degraded"] is (api == "empty")
    assert ("DEGRADED" in " ".join(w.checks["m1"].digest(w._ctx(w.checks["m1"], w.checkpoint()["block"], set()))))\
        is (api == "empty")


def test_m1_downtime_backlog_beyond_the_receipt_window_still_validates_every_receipt(tmp_path):
    """The receipt window bounds a first start on history only: after a downtime longer than the window, a poisoning
    repaired inside the backlog is still reported (events between runs)."""
    chain, w, ssv = make_m1(tmp_path, m1={"receipt_window_blocks": 5})
    deposit(ssv, value=10**18)
    advance(w)
    chain.mine(10)
    tx, _, _ = poison(ssv)
    old = chain.sealed
    deposit(ssv)
    chain.mine(30)
    advance(w)
    assert views_calls(chain, old), "validated at its own block"
    assert kinds(w) == [("warn", "open")] and evidence(w)["poison_tx"] == tx


def test_m1_unverified_page_stays_for_a_further_poisoning_without_a_repair(tmp_path):
    """The unverified page belongs to the unrepaired episode, like a verified re-poison: a further poisoning in a later
    block does not downgrade it to warn."""
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18)
    advance(w)
    poison(ssv)
    advance(w)
    poison(ssv, mine=False)
    poison(ssv)
    advance(w)
    poison(ssv)
    advance(w)
    assert incidents(w, "open")[0]["severity"] == "page" and evidence(w)["re_poison_unverified"] is True


# ---------------------------------------------------------------- QA attempt 3 F1: a change of meaning is sent


def transient_then_orphaned_repair(tmp_path, restart_now=False, re_poison=False, confirmations=0):
    """Run 1 processes a poisoning (block b1) and its repair (block b2): 'poisoned and repaired before this run'
    (transient). Before the next run a reorg replaces the chain from b2; the poisoning survives, unrepaired."""
    chain, w, ssv = make_m1(tmp_path, confirmations=confirmations)
    deposit(ssv, value=10**18)
    chain.mine(confirmations + 1)
    advance(w)
    if re_poison:  # an earlier poisoning and its repair, resolved: the transient one is a re-poison (page)
        poison(ssv)
        chain.mine(confirmations)
        advance(w)
        deposit(ssv)
        chain.mine(confirmations)
        advance(w)
        assert kinds(w) == [("warn", "open"), ("info", "resolved")]
    tx, stale, repair = poison(ssv)
    after_poison = dict(ssv._get(OWNER, OPS)[1])
    deposit(ssv)
    b2 = chain.sealed
    chain.mine(confirmations + 3)
    advance(w)
    sev = "page" if re_poison else "warn"
    assert kinds(w)[-1] == (sev, "open") and evidence(w)["transient"] is True
    assert "repaired before this run" in out(w)[-1]["text"]
    assert w.checkpoint()["block"] - b2 + 1 > confirmations, "the reorg reaches below the confirmation window"
    n = len(out(w))
    chain.reorg(b2)
    ssv._get(OWNER, OPS)[1].update(after_poison)
    chain.mine(confirmations + 2)
    if restart_now:  # the rewind commits, the process dies before the reorg run
        w.check_reorg()
        w.scan()
        w = restart(tmp_path, chain, w, confirmations=confirmations)
    advance(w)
    return chain, w, ssv, n, (tx, stale, repair), sev


@pytest.mark.parametrize("restart_now", [False, True])
@pytest.mark.parametrize("re_poison", [False, True])
@pytest.mark.parametrize("confirmations", [0, 3])
def test_m1_transient_report_then_a_reorg_orphaning_the_repair_sends_the_live_poisoning(tmp_path, restart_now,
                                                                                         re_poison, confirmations):
    """QA attempt 3 F1: the last message said 'repaired, resolved at the next run'; the canonical chain now holds an
    unrepaired poisoning. The reorg run sends the live state (stale tuple, runbook with the repair tuple) under the
    same incident, at warn (page for a re-poison), also across a restart between the rewind and the reorg run and
    with a reorg deeper than the confirmation window."""
    chain, w, ssv, n, (tx, stale, repair), sev = transient_then_orphaned_repair(
        tmp_path, restart_now=restart_now, re_poison=re_poison, confirmations=confirmations)
    assert w.db.meta("reorg:m1") is None
    assert len(incidents(w, "open")) == 1 and evidence(w).get("transient") is None
    assert len(out(w)) == n + 1, "the live poisoning is sent once"
    msg = out(w)[-1]
    assert (msg["severity"], msg["kind"]) == (sev, "update")
    assert msg["incident_id"] == incidents(w, "open")[0]["id"]
    assert "repaired before this run" not in msg["text"] and "deposit(owner, operatorIds, repairTuple)" in msg["text"]
    assert ("re-poisoned after a repair" if re_poison else "Stale cluster tuple") in msg["text"]
    ev = evidence(w)
    assert ev["poison_tx"] == tx and ev["stale_tuple"] == _tup(stale) and ev["repair_tuple"] == _tup(repair)
    advance(w)
    tick(w, chain)
    assert len(out(w)) == n + 1, "not re-sent while the meaning stays"
    deposit(ssv)
    chain.mine(confirmations)
    advance(w)
    assert kinds(w)[-1] == ("info", "resolved")


def test_m1_transient_report_resolved_then_a_reorg_orphaning_the_repair_reopens(tmp_path):
    """QA attempt 3 control: the transient incident already resolved; the same reorg reopens it and sends."""
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18)
    advance(w)
    poison(ssv)
    after_poison = dict(ssv._get(OWNER, OPS)[1])
    deposit(ssv)
    b2 = chain.sealed
    advance(w)
    tick(w, chain)
    assert kinds(w) == [("warn", "open"), ("info", "resolved")]
    chain.reorg(b2)
    ssv._get(OWNER, OPS)[1].update(after_poison)
    chain.mine(2)
    advance(w)
    assert kinds(w)[-1] == ("warn", "open") and incidents(w, "open")
    assert "Stale cluster tuple" in out(w)[-1]["text"]


def test_m1_transient_report_with_a_revert_then_a_reorg_orphaning_the_repair_sends_the_live_poisoning(tmp_path):
    """Generalised F1: the revision was anchored on the newest of poisoning and revert, so with our liquidator's revert
    after the poisoning both the transient and the stale report carried `revert:<tx>` and the change was silent."""
    chain, w, ssv, path = inbox_m1(tmp_path)
    deposit(ssv, value=10**18)
    advance(w)
    _, stale, repair = poison(ssv)
    after_poison = dict(ssv._get(OWNER, OPS)[1])
    rtx = failed_tx(chain, path, "liquidate", OWNER, OPS, stale, LIQUIDATOR, INCORRECT_STATE)
    deposit(ssv)
    b2 = chain.sealed
    chain.mine()
    advance(w)
    assert kinds(w) == [("warn", "open")]
    ev = evidence(w)
    assert ev["transient"] is True and ev["reverts"] == [rtx]
    chain.reorg(b2)
    ssv._get(OWNER, OPS)[1].update(after_poison)
    chain.mine(2)
    advance(w)
    assert w.db.meta("reorg:m1") is None and kinds(w) == [("warn", "open"), ("warn", "update")]
    assert "Stale cluster tuple" in out(w)[-1]["text"] and evidence(w)["reverts"] == [rtx]
    assert evidence(w)["repair_tuple"] == _tup(repair)


def test_m1_repair_tuple_found_after_the_alert_is_sent(tmp_path):
    """Generalised F1: a state-sweep poisoning alerted with 'Repair tuple: unknown' (the SSV API answered nothing);
    when the API later gives the canonical tuple the runbook becomes actionable, so the update is sent."""
    chain = ClusterChain()
    from fakechain import MiniSSV

    ssv = MiniSSV(chain)
    chain.mine()
    start = chain.number
    deposit(ssv, value=10**18)
    _, _, repair = poison(ssv)
    chain.mine(30)
    rows = []
    chain, w, _ = api_m1(tmp_path, rows, chain=chain, ssv=ssv, m1={"receipt_window_blocks": 5}, start_block=start,
                         activation_block=start)
    advance(w)
    alerts = outbox(w, "check_id='m1' AND kind != 'health'")
    assert [(r["severity"], r["kind"]) for r in alerts] == [("warn", "open")] and evidence(w)["repair_tuple"] is None
    assert "Repair tuple: unknown" in alerts[0]["text"]
    assert outbox(w, "key LIKE 'health:m1-inventory-empty%'"), "the empty API answer is health"
    rows.append(api_row_now(ssv, OWNER, OPS))
    tick(w, chain)
    alerts = outbox(w, "check_id='m1' AND kind != 'health'")
    assert [(r["severity"], r["kind"]) for r in alerts] == [("warn", "open"), ("warn", "update")]
    assert evidence(w)["repair_tuple"] == _tup(repair) and str(_tup(repair)) in alerts[-1]["text"]
    tick(w, chain)
    assert len(outbox(w, "check_id='m1' AND kind != 'health'")) == 2
