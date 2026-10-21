"""M1 — stale final cluster event (#91419 family): spec trigger lines, benign twins, thresholds from the chain,
restart, reorg (depth 1, depth N beyond the confirmation window, crash between detection and handling), inbox.

Run: reconcile/.venv/bin/python -m pytest -q reconcile/tests/test_m1.py
"""

from __future__ import annotations

import json

import pytest
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
from eb_storage import u256
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
