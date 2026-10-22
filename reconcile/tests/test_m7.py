"""M7 — SSV-side solvency invariant (spec M7). Scenario names are listed in
plans/monitoring-service/workers/cp2e/attempts.md (V2x.1–V2x.5)."""

from __future__ import annotations

import itertools
import json
import random

import pytest
from fixtures_legacy import CSSV, SEL, SSV, VIEWS, LegacyWorld, owner
from harness import advance, outbox

from eb_storage import keccak, u256
from rpc import RpcError

pytestmark = pytest.mark.m7

DAY = 86_400
CLUSTER = 100 * SSV
OPS = {1: 5 * SSV, 2: 3 * SSV}
NETWORK = 2 * SSV
STAKED = 1_000 * SSV
BALANCED = STAKED + CLUSTER + sum(OPS.values()) + NETWORK + SSV  # surplus exactly 1 SSV (the activation seed)
USER = "0x" + "ab" * 20


def incidents(w, state="open"):
    return {r["key"]: dict(r) for r in w.db.conn.execute(
        "SELECT * FROM incidents WHERE check_id='m7' AND state=?", (state,))}


def findings_out(w):
    return outbox(w, "severity!='health' AND kind NOT IN ('digest', 'realert')")  # re-alerts: framework cadence


def world(tmp_path, m7=None, proxy=BALANCED, cssv=STAKED, count=2, bystander=False, **kw):
    lw = LegacyWorld(tmp_path, only=("m7",), **kw)
    lw.checks = {"m7": {"cssv": CSSV, "unstake_history_from": lw.chain.sealed, **(m7 or {})}}
    a = lw.legacy(owner(1), count=count)
    lw.views(a, balance=CLUSTER)
    if bystander:  # a listed legacy cluster without validators or balance (rows[1]: a short page keeps it)
        lw.views(lw.legacy(owner(2), count=0), balance=0, burn=0)
    lw.ledger(proxy_ssv=proxy, cssv_supply=cssv, network=NETWORK, operators=OPS)
    lw.chain.mine()
    lw.start()
    return lw, a


def day(lw):
    lw.sweep(seconds=DAY)


# ---------------------------------------------------------------- V2x.1: page — surplus < 1 SSV


def test_m7_surplus_below_one_ssv_pages(tmp_path):
    lw, _ = world(tmp_path, proxy=BALANCED - 1)
    advance(lw.w)
    rows = findings_out(lw.w)
    assert [(r["severity"], r["kind"]) for r in rows if r["severity"] == "page"] == [("page", "open")]
    page = next(r for r in rows if r["severity"] == "page")
    assert "0.999999999999999999 SSV, below 1 SSV" in page["text"] and page["delivery"] == "telegram"
    ev = json.loads(incidents(lw.w)["surplus-floor"]["evidence"])
    assert ev["surplus"] == str(SSV - 1) and ev["claims"] == str(BALANCED - SSV)
    assert ev["outstanding_unstake"] == "0" and ev["legacy_cluster_balances"] == str(CLUSTER)


def test_m7_benign_surplus_exactly_one_ssv_does_not_page(tmp_path):
    lw, _ = world(tmp_path)
    advance(lw.w)
    assert outbox(lw.w, "severity='page' AND kind!='realert'") == [] and "surplus-floor" not in incidents(lw.w)
    assert incidents(lw.w)["surplus"]["revision"] == str(SSV)


def test_m7_page_resolves_when_the_surplus_is_back_above_the_floor(tmp_path):
    lw, _ = world(tmp_path, proxy=BALANCED - SSV)
    advance(lw.w)
    lw.ledger(proxy_ssv=BALANCED + SSV, cssv_supply=STAKED, network=NETWORK, operators=OPS)
    day(lw)
    kinds = [(r["severity"], r["kind"]) for r in findings_out(lw.w)]
    assert ("page", "open") in kinds and kinds.count(("info", "resolved")) == 1
    assert "surplus-floor" not in incidents(lw.w)


# ---------------------------------------------------------------- V2x.1: info — any change in the surplus


def test_m7_surplus_change_sends_info_under_one_incident(tmp_path):
    lw, a = world(tmp_path, proxy=BALANCED + SSV)  # surplus 2 SSV: a change, not a page
    advance(lw.w)
    base = findings_out(lw.w)
    assert [(r["severity"], r["kind"]) for r in base] == [("info", "open")] and base[0]["delivery"] == "discord"
    assert "baseline: 2 SSV" in base[0]["text"]
    # a drained cluster: its balance is floored at 0 while its operators keep accruing (phantom credit, #91046)
    lw.views(a, balance=0)
    lw.ledger(proxy_ssv=BALANCED + SSV, cssv_supply=STAKED, network=NETWORK,
              operators={1: 5 * SSV + CLUSTER + SSV // 10, 2: 3 * SSV})  # the cluster's 100 SSV + 0.1 unbacked
    day(lw)
    rows = findings_out(lw.w)
    assert [(r["severity"], r["kind"]) for r in rows] == [("info", "open"), ("info", "update")]
    assert rows[0]["incident_id"] == rows[1]["incident_id"]
    assert "changed: 1.9 SSV" in rows[1]["text"] and "change -0.1 SSV" in rows[1]["text"]


def test_m7_benign_unchanged_surplus_sends_nothing(tmp_path):
    lw, a = world(tmp_path)
    advance(lw.w)
    # ordinary accrual moves value from the cluster to its operators and the DAO: the surplus is unchanged
    lw.views(a, balance=CLUSTER - 3 * SSV)
    lw.ledger(proxy_ssv=BALANCED, cssv_supply=STAKED, network=NETWORK + SSV, operators={1: 6 * SSV, 2: 4 * SSV})
    day(lw)
    day(lw)
    assert len(findings_out(lw.w)) == 1 and lw.w.db.meta("run:m7")["findings"] == 1


# ---------------------------------------------------------------- outstanding unstakes (spec "Careful")


def test_m7_unstake_request_burns_cssv_and_is_counted_until_withdrawn(tmp_path):
    lw, _ = world(tmp_path)
    advance(lw.w)
    lw.unstake(USER, 50 * SSV)  # requestUnstake burns cSSV now; the SSV stays in the proxy
    lw.ledger(proxy_ssv=BALANCED, cssv_supply=STAKED - 50 * SSV, network=NETWORK, operators=OPS)
    day(lw)
    ev = json.loads(incidents(lw.w)["surplus"]["evidence"])
    assert ev["outstanding_unstake"] == str(50 * SSV) and ev["surplus"] == str(SSV)
    lw.unstake(USER, 50 * SSV, withdraw=True)  # withdrawUnlocked: the SSV leaves
    lw.ledger(proxy_ssv=BALANCED - 50 * SSV, cssv_supply=STAKED - 50 * SSV, network=NETWORK, operators=OPS)
    day(lw)
    ev = json.loads(incidents(lw.w)["surplus"]["evidence"])
    assert ev["outstanding_unstake"] == "0" and ev["unstake_requests"] == 1 and ev["unstake_withdrawals"] == 1
    assert len(findings_out(lw.w)) == 1  # no change, no page


def test_m7_same_block_request_and_withdrawal(tmp_path):
    """Two unstake events in one block (any order) net out; the sums do not depend on log order."""
    lw, _ = world(tmp_path)
    advance(lw.w)
    lw.unstake(USER, 20 * SSV, withdraw=True)
    lw.unstake("0x" + "cd" * 20, 30 * SSV)
    lw.unstake(USER, 20 * SSV)
    lw.ledger(proxy_ssv=BALANCED - 20 * SSV, cssv_supply=STAKED - 50 * SSV, network=NETWORK, operators=OPS)
    day(lw)
    ev = json.loads(incidents(lw.w)["surplus"]["evidence"])
    assert ev["outstanding_unstake"] == str(30 * SSV) and ev["surplus"] == str(SSV)
    assert len(findings_out(lw.w)) == 1


def test_m7_unstake_history_before_start_block_is_read_once(tmp_path):
    lw = LegacyWorld(tmp_path, only=("m7",))
    frm = lw.chain.sealed
    lw.legacy(owner(1), count=2)
    lw.views(lw.refs[0], balance=CLUSTER)
    lw.chain.mine()
    lw.unstake(USER, 40 * SSV)  # before the worker's start block
    lw.chain.mine(3)
    lw.ledger(proxy_ssv=BALANCED, cssv_supply=STAKED - 40 * SSV, network=NETWORK, operators=OPS)
    lw.chain.mine()
    lw.checks = {"m7": {"cssv": CSSV, "unstake_history_from": frm}}
    w = lw.start()  # start_block = the sealed block, after the request
    assert w.cfg.start_block > frm + 1
    advance(w)
    ev = json.loads(incidents(w)["surplus"]["evidence"])
    assert ev["outstanding_unstake"] == str(40 * SSV) and ev["surplus"] == str(SSV)
    assert w.db.meta("run:m7")["rpc_calls"]["eth_getLogs"] == 1
    assert w.db.meta("check:m7:unstake_prefix")["n_req"] == 1
    day(lw)
    assert "eth_getLogs" not in w.db.meta("run:m7")["rpc_calls"] and len(findings_out(w)) == 1


# ---------------------------------------------------------------- UNKNOWN is never clean


def test_m7_unpriced_legacy_cluster_is_unknown_not_clean(tmp_path):
    lw, a = world(tmp_path)
    advance(lw.w)
    lw.views(a, balance=CLUSTER, revert=True)
    day(lw)
    assert len(findings_out(lw.w)) == 1  # no change claimed, no resolution
    health = outbox(lw.w, "severity='health'")
    assert len(health) == 1 and "UNKNOWN" in health[0]["text"] and a.cid in health[0]["text"]
    assert incidents(lw.w)["surplus"]["revision"] == str(SSV)


def test_m7_legacy_cluster_missing_from_the_inventory_is_unknown_not_a_smaller_liability(tmp_path):
    lw, a = world(tmp_path)
    advance(lw.w)
    lw.rows.clear()  # the API drops the row; storage still holds the legacy cluster
    lw.clock.advance(DAY)
    lw.chain.mine()
    advance(lw.w)
    assert len(findings_out(lw.w)) == 1 and incidents(lw.w)["surplus"]["revision"] == str(SSV)
    assert "missing from the inventory" in outbox(lw.w, "severity='health'")[0]["text"]
    lw.ssv.migrate(a.owner, a.ops)  # now it really left VERSION_SSV (and the ETH log lists it again)
    lw.views(a, balance=0)
    lw.ledger(proxy_ssv=BALANCED - CLUSTER, cssv_supply=STAKED, network=NETWORK, operators=OPS)
    day(lw)
    assert lw.w.db.conn.execute("SELECT COUNT(*) FROM m7_legacy").fetchone()[0] == 0
    assert incidents(lw.w)["surplus"]["revision"] == str(SSV) and len(findings_out(lw.w)) == 1


def test_m7_unknown_still_pages_when_the_upper_bound_is_below_the_floor(tmp_path):
    lw, a = world(tmp_path, proxy=BALANCED - CLUSTER - SSV)  # even without the cluster's claim: below
    lw.views(a, balance=CLUSTER, revert=True)
    lw.chain.mine()
    advance(lw.w)
    page = outbox(lw.w, "severity='page' AND kind!='realert'")
    assert len(page) == 1 and "surplus at most" in page[0]["text"] and "Unpriced claims" in page[0]["text"]
    assert "surplus" not in incidents(lw.w)  # no info value from a partial ledger


def test_m7_unknown_keeps_an_open_page(tmp_path):
    lw, a = world(tmp_path, proxy=BALANCED - SSV // 2)
    advance(lw.w)
    lw.views(a, balance=CLUSTER, revert=True)
    lw.ledger(proxy_ssv=BALANCED + 10 * SSV, cssv_supply=STAKED, network=NETWORK, operators=OPS)
    day(lw)
    assert "surplus-floor" in incidents(lw.w)  # not resolved from a partial ledger
    assert len(outbox(lw.w, "severity='page' AND kind!='realert'")) == 1


def test_m7_reverting_operator_earnings_is_unknown(tmp_path):
    lw, _ = world(tmp_path)
    advance(lw.w)
    sel = "0x" + keccak(b"getOperatorEarningsSSV(uint64)")[:4].hex()
    lw.chain.stub_call(VIEWS, sel + u256(2).hex(), revert="0x")
    day(lw)
    health = outbox(lw.w, "severity='health'")
    assert len(health) == 1 and "operator:2" in health[0]["text"]


@pytest.mark.parametrize("answer", ["revert", "empty"])
def test_m7_missing_head_value_is_a_check_error(tmp_path, answer):
    lw, _ = world(tmp_path)
    if answer == "revert":
        lw.chain.stub_call(CSSV, "0x18160ddd", revert="0x")
    else:
        lw.chain.stub_call(CSSV, "0x18160ddd", b"")
    lw.chain.mine()
    advance(lw.w)
    assert findings_out(lw.w) == [] and incidents(lw.w) == {}
    assert any("check-error" in r["text"] for r in outbox(lw.w, "severity='health'"))


def test_m7_operator_count_zero_next_to_legacy_clusters_is_unknown(tmp_path):
    lw, _ = world(tmp_path)
    lw.ledger(proxy_ssv=BALANCED, cssv_supply=STAKED, network=NETWORK, operators=OPS, last_op=0)
    lw.chain.mine()
    advance(lw.w)
    assert "surplus" not in incidents(lw.w) and "lastOperatorId is 0" in outbox(lw.w, "severity='health'")[0]["text"]


def test_m7_requires_cssv_and_unstake_history(tmp_path):
    lw = LegacyWorld(tmp_path, only=("m7",), checks={"m7": {"unstake_history_from": 1}})
    with pytest.raises(SystemExit, match="cssv"):
        lw.start()
    lw.checks = {"m7": {"cssv": CSSV}}
    with pytest.raises(SystemExit, match="unstake_history_from"):
        lw.start()


# ---------------------------------------------------------------- V2x.2 thresholds_from_chain


def test_m7_thresholds_from_chain_operator_count_and_token_come_from_storage(tmp_path):
    lw, _ = world(tmp_path)
    advance(lw.w)
    # a new operator id 3 with SSV earnings: lastOperatorId on chain moves, its claim is counted
    lw.ledger(proxy_ssv=BALANCED, cssv_supply=STAKED, network=NETWORK, operators={**OPS, 3: SSV // 2})
    day(lw)
    assert outbox(lw.w, "severity='page' AND kind!='realert'") and json.loads(
        incidents(lw.w)["surplus"]["evidence"])["operators"] == 3
    # the token address is read from proxy storage too
    other = "0x" + "77" * 20
    lw.ledger(proxy_ssv=BALANCED + SSV, cssv_supply=STAKED, network=NETWORK, operators={**OPS, 3: SSV // 2},
              token=other)
    day(lw)
    ev = json.loads(incidents(lw.w)["surplus"]["evidence"])
    assert ev["ssv_token"] == other and ev["surplus"] == str(SSV + SSV // 2)


def test_m7_thresholds_floor_is_config_not_code(tmp_path):
    lw, _ = world(tmp_path, m7={"surplus_floor_wei": 2 * SSV})
    advance(lw.w)
    assert len(outbox(lw.w, "severity='page' AND kind!='realert'")) == 1


# ---------------------------------------------------------------- V2x.3 restart


def test_m7_restart_does_not_duplicate_the_open_incidents(tmp_path):
    lw, _ = world(tmp_path, proxy=BALANCED - SSV)
    advance(lw.w)
    assert len(findings_out(lw.w)) == 2  # page + info baseline
    lw.clock.advance(600)
    lw.chain.mine()
    lw.restart()
    advance(lw.w)  # Every runs at start: the same ledger
    day(lw)
    assert len(findings_out(lw.w)) == 2 and len(incidents(lw.w)) == 2


# ---------------------------------------------------------------- V2x.4 rpc_budget


def test_m7_rpc_budget_350_cluster_inventory(tmp_path):
    lw = LegacyWorld(tmp_path, only=("m7",))
    lw.checks = {"m7": {"cssv": CSSV, "unstake_history_from": lw.chain.sealed}}
    for i in range(350):
        lw.legacy(owner(i + 1), count=1 + i % 3)
    lw.chain.stub_call(VIEWS, SEL["getBalanceSSV"], u256(SSV))
    n_ops = 2_352  # mainnet lastOperatorId at block 26,241,510 (CP2e attempt log)
    lw.ledger(proxy_ssv=350 * SSV + 2 * SSV, cssv_supply=0, network=SSV, op_default=0, last_op=n_ops)
    lw.chain.mine()
    w = lw.start()
    advance(w)
    calls = w.db.meta("run:m7")["rpc_calls"]
    assert calls["eth_call"] <= 3 + n_ops + 350, calls
    ev = json.loads(incidents(w)["surplus"]["evidence"])
    assert ev["legacy_clusters_priced"] == 350 and ev["surplus"] == str(SSV)


# ---------------------------------------------------------------- reorgs (README §6.2)


def _leak_at_fork(tmp_path, confirmations=0):
    lw, a = world(tmp_path, confirmations=confirmations)
    lw.chain.mine(confirmations)
    advance(lw.w)
    assert lw.w.checkpoint()["hash"]
    lw.clock.advance(DAY)
    fork = lw.chain.number
    lw.ledger(proxy_ssv=BALANCED - 5 * SSV, cssv_supply=STAKED, network=NETWORK, operators=OPS)  # SSV leaves
    lw.chain.mine(1 + confirmations)
    advance(lw.w)
    assert len(outbox(lw.w, "severity='page' AND kind!='realert'")) == 1
    return lw, fork


def _after_reorg_back_to_baseline(lw):
    inc = incidents(lw.w)
    assert "surplus-floor" not in inc and inc["surplus"]["revision"] == str(SSV)
    assert len(outbox(lw.w, "severity='page' AND kind!='realert'")) == 1 and lw.w.db.meta("reorg:m7") is None


def test_m7_reorg_depth_1_removing_the_leak_resolves(tmp_path):
    lw, fork = _leak_at_fork(tmp_path)
    lw.fork(fork)
    lw.chain.mine()
    advance(lw.w)
    _after_reorg_back_to_baseline(lw)
    lw.clock.advance(7 * 3600)
    advance(lw.w)
    assert len(outbox(lw.w, "severity='page' AND kind!='realert'")) == 1  # never re-paged


def test_m7_reorg_depth_n_beyond_the_confirmation_window(tmp_path):
    lw, fork = _leak_at_fork(tmp_path, confirmations=3)
    lw.chain.mine(6)
    advance(lw.w)
    lw.fork(fork)
    lw.chain.mine(5)
    advance(lw.w)
    _after_reorg_back_to_baseline(lw)


def test_m7_reorg_crash_between_detection_and_the_check_run(tmp_path):
    lw, fork = _leak_at_fork(tmp_path)
    lw.fork(fork)
    lw.chain.mine()
    lw.w.check_reorg()
    lw.restart()
    advance(lw.w)
    _after_reorg_back_to_baseline(lw)


def test_m7_reorg_deleting_an_unstake_request_rewinds_the_sum(tmp_path):
    """The request (event + cSSV burn) lives only on the deleted chain: the stored log is rewound with it, so the
    new chain's ledger balances again (a kept log would count 50 SSV of phantom unstakes and page)."""
    lw, _ = world(tmp_path)
    advance(lw.w)
    lw.clock.advance(DAY)
    fork = lw.chain.number
    lw.unstake(USER, 50 * SSV)
    lw.ledger(proxy_ssv=BALANCED, cssv_supply=STAKED - 50 * SSV, network=NETWORK, operators=OPS)
    lw.chain.mine()
    advance(lw.w)
    assert json.loads(incidents(lw.w)["surplus"]["evidence"])["outstanding_unstake"] == str(50 * SSV)
    lw.fork(fork)
    lw.chain.mine()
    advance(lw.w)
    ev = json.loads(incidents(lw.w)["surplus"]["evidence"])
    assert ev["outstanding_unstake"] == "0" and ev["surplus"] == str(SSV)
    assert outbox(lw.w, "severity='page' AND kind!='realert'") == [] and len(findings_out(lw.w)) == 1


def test_m7_reorg_with_the_leak_on_the_new_chain_keeps_the_page(tmp_path):
    lw, fork = _leak_at_fork(tmp_path)
    page_id = incidents(lw.w)["surplus-floor"]["id"]
    lw.fork(fork)
    lw.ledger(proxy_ssv=BALANCED - 5 * SSV, cssv_supply=STAKED, network=NETWORK, operators=OPS)
    lw.chain.mine()
    advance(lw.w)
    assert incidents(lw.w)["surplus-floor"]["id"] == page_id and len(outbox(lw.w, "severity='page' AND kind!='realert'")) == 1


# ---------------------------------------------------------------- QA cp2e-1 F1: an incomplete inventory is UNKNOWN


def _health(w):
    return [r["text"] for r in outbox(w, "severity='health'")]


def _opened(w):
    return [(r["severity"], r["kind"]) for r in findings_out(w) if r["kind"] == "open"]


def _short_world(tmp_path, proxy):
    lw = LegacyWorld(tmp_path, only=("m7",))
    lw.checks = {"m7": {"cssv": CSSV, "unstake_history_from": lw.chain.sealed}}
    a = lw.legacy(owner(1), count=2)
    lw.views(a, balance=CLUSTER)
    lw.ledger(proxy_ssv=proxy, cssv_supply=STAKED, network=NETWORK, operators=OPS)
    lw.chain.mine()
    lw.start()
    return lw, a


def test_m7_first_run_with_an_empty_api_listing_is_unknown_not_a_baseline(tmp_path):
    """QA cp2e-1 F1 (PR2): one legacy cluster of 100 SSV, the proxy 50 SSV short (true surplus -49 SSV). An empty
    API page at the first run must not drop the cluster's claim: no baseline of 51 SSV, UNKNOWN health."""
    lw, _ = _short_world(tmp_path, BALANCED - 50 * SSV)
    lw.w.inventory.http_get = lambda url: {"clusters": [], "pagination": {"pages": 1, "total": 1}}
    advance(lw.w)
    assert incidents(lw.w) == {}
    health = _health(lw.w)
    assert len(health) == 1 and "UNKNOWN" in health[0] and "inventory incomplete" in health[0]
    lw.w.inventory.http_get = lw.http_get  # the API recovers: the true surplus pages; one baseline, no "changed"
    day(lw)
    inc = incidents(lw.w)
    assert "surplus-floor" in inc and inc["surplus"]["revision"] == str(-49 * SSV)
    assert sorted(_opened(lw.w)) == [("info", "open"), ("page", "open")]


def test_m7_incomplete_inventory_still_pages_when_the_upper_bound_is_below_the_floor(tmp_path):
    lw, _ = _short_world(tmp_path, BALANCED - CLUSTER - 2 * SSV)  # below the floor even without the cluster
    lw.api_fault = "empty"
    advance(lw.w)
    page = outbox(lw.w, "severity='page' AND kind!='realert'")
    assert len(page) == 1 and "surplus at most" in page[0]["text"] and "surplus" not in incidents(lw.w)


@pytest.mark.parametrize("fault", ["short", "no_total"])
def test_m7_listing_short_of_its_total_or_without_one_is_unknown(tmp_path, fault):
    lw, _ = _short_world(tmp_path, BALANCED)
    lw.legacy(owner(2), count=0)  # inert, with a positive balance: a claim no validator count reveals
    lw.views(lw.refs[1], balance=7 * SSV, burn=0)
    lw.ledger(proxy_ssv=BALANCED + 7 * SSV, cssv_supply=STAKED, network=NETWORK, operators=OPS)
    lw.chain.mine()
    lw.api_fault = fault
    advance(lw.w)
    assert incidents(lw.w) == {}
    health = _health(lw.w)
    assert len(health) == 1 and "inventory incomplete" in health[0]
    assert ("pagination.total 2" if fault == "short" else "no pagination.total") in health[0]


def test_m7_unreadable_api_total_is_unknown_not_a_change(tmp_path):
    lw, _ = _short_world(tmp_path, BALANCED)
    lw.w = lw.start(inventory={"api_url": "https://api.invalid/api/v4/mainnet", "api_refresh_s": 10**9})
    advance(lw.w)
    assert incidents(lw.w)["surplus"]["revision"] == str(SSV)
    lw.api_fault = "error"
    day(lw)
    assert len(findings_out(lw.w)) == 1 and "pagination.total unavailable" in _health(lw.w)[0]


def test_m7_cluster_missing_from_the_api_and_its_total_is_found_by_the_dao_validator_count(tmp_path):
    lw = LegacyWorld(tmp_path, only=("m7",))
    lw.checks = {"m7": {"cssv": CSSV, "unstake_history_from": lw.chain.sealed}}
    a = lw.legacy(owner(1), count=2, api=False)  # the API's index lost it: no row, and its total says 0
    lw.views(a, balance=CLUSTER)
    lw.ledger(proxy_ssv=BALANCED - 50 * SSV, cssv_supply=STAKED, network=NETWORK, operators=OPS)
    lw.chain.mine()
    lw.start()
    advance(lw.w)
    assert incidents(lw.w) == {} and "hold 0 validators, daoValidatorCount is 2" in _health(lw.w)[0]


def test_m7_restart_on_a_lost_database_with_an_empty_api_is_unknown(tmp_path):
    lw, _ = _short_world(tmp_path, BALANCED - 50 * SSV)
    advance(lw.w)
    assert "surplus-floor" in incidents(lw.w)
    lw.tmp_path = tmp_path / "lost"
    lw.tmp_path.mkdir()
    lw.api_fault = "empty"
    lw.start()
    advance(lw.w)
    assert incidents(lw.w) == {} and "inventory incomplete" in _health(lw.w)[0]


# ---------------------------------------------------------------- QA cp2e-2 F1: an empty listing is never complete


def _inert_world(tmp_path, proxy):
    """Only an inert legacy cluster (no validators: daoValidatorCount 0) holding CLUSTER: no validator count
    reveals it, only the listing does."""
    lw = LegacyWorld(tmp_path, only=("m7",))
    lw.checks = {"m7": {"cssv": CSSV, "unstake_history_from": lw.chain.sealed}}
    a = lw.legacy(owner(1), count=0)
    lw.views(a, balance=CLUSTER)
    lw.ledger(proxy_ssv=proxy, cssv_supply=STAKED, network=NETWORK, operators=OPS)
    lw.chain.mine()
    lw.start()
    return lw, a


def test_m7_q10_consistent_empty_api_with_inert_legacy_balance(tmp_path):
    """QA cp2e-2 F1 (Q10): the proxy is 50 SSV short of an inert 100 SSV legacy balance (true surplus -49 SSV) and
    the API answers a self-consistent empty page (`clusters: []`, total 0). daoValidatorCount 0 agrees with it:
    an empty listing is still never complete (UNKNOWN, no baseline of 51 SSV)."""
    lw, _ = _inert_world(tmp_path, BALANCED - 50 * SSV)
    lw.api_fault, lw.api_total = "empty", 0
    advance(lw.w)
    assert incidents(lw.w) == {}
    health = _health(lw.w)
    assert len(health) == 1 and "UNKNOWN" in health[0] and "listed no clusters" in health[0]
    lw.api_fault, lw.api_total = None, None  # the API recovers: the true surplus pages; one baseline
    day(lw)
    inc = incidents(lw.w)
    assert "surplus-floor" in inc and inc["surplus"]["revision"] == str(-49 * SSV)
    assert sorted(_opened(lw.w)) == [("info", "open"), ("page", "open")]


@pytest.mark.parametrize("total", [0, 1, None])
@pytest.mark.parametrize("point", ["first", "lost_database", "after_baseline", "reorg"])
def test_m7_empty_api_listing_is_unknown_whatever_its_total(tmp_path, total, point):
    """The empty-input class at every point a run can meet it, in the inert-only world (daoValidatorCount 0):
    the total the API reports (0, the true 1, none) never makes an empty page complete. No resolve, no new
    baseline, no changed revision; health says UNKNOWN."""
    lw, _ = _inert_world(tmp_path, BALANCED)
    if point != "first":
        advance(lw.w)
        assert incidents(lw.w)["surplus"]["revision"] == str(SSV)
    if point == "lost_database":
        lw.tmp_path = tmp_path / "lost"
        lw.tmp_path.mkdir()
        lw.start()
    elif point == "reorg":
        fork = lw.chain.number
        lw.chain.mine(2)
        advance(lw.w)
        lw.fork(fork)
    if total is None:  # an empty page without a total
        lw.rows, lw.api_fault = [], "no_total"
    else:
        lw.api_fault, lw.api_total = "empty", total
    n = len(findings_out(lw.w))
    day(lw)
    assert findings_out(lw.w)[n:] == []
    inc = incidents(lw.w)
    assert (inc == {}) if point in ("first", "lost_database") else (inc["surplus"]["revision"] == str(SSV))
    assert any("UNKNOWN" in h for h in _health(lw.w))  # the gap, or (after a baseline) the m7_legacy id check


def test_m7_empty_api_next_to_a_cluster_from_the_logs_is_unknown(tmp_path):
    """The API lists nothing (total 0) while the scanned logs hold one legacy cluster (its last validator
    removed after the start block): the listing is not empty, but an API that lists none of the clusters the
    chain shows is the same degenerate answer — the other inert legacy balance (7 SSV) is missing."""
    lw = LegacyWorld(tmp_path, only=("m7",))
    lw.checks = {"m7": {"cssv": CSSV, "unstake_history_from": lw.chain.sealed}}
    a, b = lw.legacy(owner(1), count=1), lw.legacy(owner(2), count=0)
    lw.views(b, balance=7 * SSV, burn=0)
    # true surplus 1 + 2 - 7 = -4 SSV; without b's claim it would read 3 SSV, a clean baseline
    lw.ledger(proxy_ssv=BALANCED + 2 * SSV, cssv_supply=STAKED, network=NETWORK, operators=OPS)
    lw.chain.mine()
    lw.start()
    lw.ssv.remove(a.owner, a.ops, [b"\x02" * 48])  # in the scanned logs; daoValidatorCount -> 0
    lw.views(a, balance=CLUSTER)
    lw.chain.mine()
    lw.api_fault, lw.api_total = "empty", 0
    advance(lw.w)
    assert incidents(lw.w) == {}
    health = _health(lw.w)
    assert len(health) == 1 and "listed no clusters" in health[0]


# ---------------------------------------------------------------- QA cp2e-3 F1: log-only clusters never offset a short page


def _masked_world(tmp_path):
    """QA cp2e-3 F1 (P1): the inert legacy cluster b (0 validators, 7 SSV) is the row a short page drops (rows[0]);
    the burning legacy cluster a (1 validator) stays listed, so daoValidatorCount 1 = listed 1. True surplus
    1 + 2 - 7 = -4 SSV; without b's claim it reads +3 SSV."""
    lw = LegacyWorld(tmp_path, only=("m7",))
    lw.checks = {"m7": {"cssv": CSSV, "unstake_history_from": lw.chain.sealed}}
    b = lw.legacy(owner(2), count=0)
    a = lw.legacy(owner(1), count=1)
    lw.views(b, balance=7 * SSV, burn=0)
    lw.views(a, balance=CLUSTER)
    lw.ledger(proxy_ssv=BALANCED + 2 * SSV, cssv_supply=STAKED, network=NETWORK, operators=OPS)
    lw.chain.mine()
    lw.start()
    return lw


def _log_only(lw, n=1):
    """`n` ETH clusters registered after the start block: in the scanned logs, not (yet) in the API."""
    for i in range(n):
        lw.ssv.register(owner(9 + i), (5, 6, 7, 8), [bytes([1 + i]) * 48])
    lw.chain.mine()


def test_m7_p1_short_page_masked_by_a_log_only_cluster_first_run(tmp_path):
    """QA cp2e-3 F1 (P1): the API serves [a] with its true pagination.total 2 while one cluster is known only from
    the logs. The log-only cluster is not an API row: the page is short, UNKNOWN, no +3 SSV baseline."""
    lw = _masked_world(tmp_path)
    _log_only(lw)
    lw.api_fault = "short"
    advance(lw.w)
    assert incidents(lw.w) == {}
    health = _health(lw.w)
    assert len(health) == 1 and "UNKNOWN" in health[0] and "listed 1 clusters of its pagination.total 2" in health[0]
    lw.api_fault = None  # the API recovers: the true surplus pages; one baseline
    day(lw)
    inc = incidents(lw.w)
    assert "surplus-floor" in inc and inc["surplus"]["revision"] == str(-4 * SSV)
    assert sorted(_opened(lw.w)) == [("info", "open"), ("page", "open")]


def test_m7_p1_masking_persists_on_the_next_daily_run(tmp_path):
    """QA cp2e-3 F1 (P1 persistence): the API stays short and a 2 SSV leak lands the next day (true -6 SSV): still
    UNKNOWN, never a surplus of exactly 1 SSV; the page goes out once the listing is whole."""
    lw = _masked_world(tmp_path)
    _log_only(lw)
    lw.api_fault = "short"
    advance(lw.w)
    lw.ledger(proxy_ssv=BALANCED, cssv_supply=STAKED, network=NETWORK, operators=OPS)
    day(lw)
    assert incidents(lw.w) == {} and findings_out(lw.w) == []
    assert all("pagination.total 2" in h for h in _health(lw.w)) and len(_health(lw.w)) == 2
    lw.api_fault = None
    day(lw)
    inc = incidents(lw.w)
    assert "surplus-floor" in inc and inc["surplus"]["revision"] == str(-6 * SSV)


def test_m7_p1_control_short_page_alone_is_unknown(tmp_path):
    lw = _masked_world(tmp_path)
    lw.chain.mine()
    lw.api_fault = "short"
    advance(lw.w)
    assert incidents(lw.w) == {} and any("UNKNOWN" in h for h in _health(lw.w))


@pytest.mark.parametrize("log_only", [1, 2])
@pytest.mark.parametrize("point", ["first", "lost_database", "after_baseline", "reorg"])
def test_m7_short_page_offset_by_log_only_clusters_is_unknown_at_every_point(tmp_path, point, log_only):
    """F1's input at every point a run can meet it, with as many and with more log-only clusters than rows dropped:
    no new baseline, no changed revision, no resolve; health says UNKNOWN."""
    lw = _masked_world(tmp_path)
    if point != "first":
        advance(lw.w)
        assert incidents(lw.w)["surplus"]["revision"] == str(-4 * SSV)
    if point == "lost_database":
        lw.tmp_path = tmp_path / "lost"
        lw.tmp_path.mkdir()
        lw.start()
    elif point == "reorg":
        fork = lw.chain.number
        lw.chain.mine(2)
        advance(lw.w)
        lw.fork(fork)
    before = incidents(lw.w)
    _log_only(lw, log_only)
    lw.api_fault = "short"
    n = len(findings_out(lw.w))
    day(lw)
    assert findings_out(lw.w)[n:] == []
    after = incidents(lw.w)
    assert {k: v["revision"] for k, v in after.items()} == {k: v["revision"] for k, v in before.items()}
    assert any("UNKNOWN" in h for h in _health(lw.w))  # the gap, or (after a baseline) the m7_legacy id check


def test_m7_unstake_prefix_read_failure_is_retried_not_zero(tmp_path):
    """The range before start_block is read once with eth_getLogs; an error there fails the run (nothing
    persisted, retried at the next step), never 'no unstakes'."""
    lw = LegacyWorld(tmp_path, only=("m7",))
    frm = lw.chain.sealed
    lw.legacy(owner(1), count=2)
    lw.views(lw.refs[0], balance=CLUSTER)
    lw.chain.mine()
    lw.unstake(USER, 40 * SSV)
    lw.chain.mine(3)
    lw.ledger(proxy_ssv=BALANCED, cssv_supply=STAKED - 40 * SSV, network=NETWORK, operators=OPS)
    lw.chain.mine()
    lw.checks = {"m7": {"cssv": CSSV, "unstake_history_from": frm}}
    w = lw.start()
    handle = lw.chain._handle

    def pruned(m, p):
        if m == "eth_getLogs" and int(p[0]["toBlock"], 16) < w.cfg.start_block:
            raise ValueError("history pruned")
        return handle(m, p)

    lw.chain._handle = pruned
    with pytest.raises(RpcError, match="history pruned"):  # an RPC failure of the step (worker: rpc health)
        advance(w)
    assert incidents(w) == {} and w.db.meta("check:m7:unstake_prefix") is None
    assert w.db.meta("run:m7")["errors"] == 1
    lw.chain._handle = handle
    lw.clock.advance(60)
    advance(w)  # the failed run is retried at the next step
    ev = json.loads(incidents(w)["surplus"]["evidence"])
    assert ev["outstanding_unstake"] == str(40 * SSV) and ev["surplus"] == str(SSV)


# ---------------------------------------------------------------- QA cp2e-1 F3: reorg runs, resolved keys


def test_m7_reorg_run_whose_re_evaluation_is_unknown_keeps_both_keys_without_a_second_baseline(tmp_path):
    """QA cp2e-1 F3 (QF7b, PR12): both keys were last seen above the ancestor; the new chain's ledger cannot be
    priced (a reverting legacy view). Each key is re-reported unchanged under its id; no baseline is re-sent."""
    lw, fork = _leak_at_fork(tmp_path)
    ids = {k: v["id"] for k, v in incidents(lw.w).items()}
    assert set(ids) == {"surplus", "surplus-floor"}
    lw.fork(fork)
    lw.views(lw.refs[0], balance=CLUSTER, revert=True)
    lw.chain.mine()
    advance(lw.w)
    assert {k: v["id"] for k, v in incidents(lw.w).items()} == ids and lw.w.db.meta("reorg:m7") is None
    assert any("UNKNOWN" in t for t in _health(lw.w))
    lw.views(lw.refs[0], balance=CLUSTER)  # priced again: the new chain balances
    day(lw)
    inc = incidents(lw.w)
    assert set(inc) == {"surplus"} and inc["surplus"]["id"] == ids["surplus"]
    assert sorted(_opened(lw.w)) == [("info", "open"), ("page", "open")]


def test_m7_reorg_orphaning_the_repair_that_resolved_the_page_reopens_it(tmp_path):
    """An ALREADY RESOLVED key whose resolution (the SSV coming back) lived on a deleted block."""
    lw, _ = world(tmp_path, proxy=BALANCED - 5 * SSV)
    advance(lw.w)
    lw.clock.advance(DAY)
    fork = lw.chain.number
    lw.ledger(proxy_ssv=BALANCED, cssv_supply=STAKED, network=NETWORK, operators=OPS)
    lw.chain.mine()
    advance(lw.w)
    assert "surplus-floor" not in incidents(lw.w)
    lw.fork(fork)
    lw.chain.mine()
    advance(lw.w)
    assert "surplus-floor" in incidents(lw.w)
    assert len(outbox(lw.w, "severity='page' AND kind='open'")) == 2


# ---------------------------------------------------------------- seeded adversarial sweep (qa/DEFECT-CLASSES.md)

SWEEP_STORIES = ("leak_orphaned", "leak_survives", "repair_orphaned", "unstake_orphaned")
SWEEP_FAULTS = (None, "empty", "short", "error", "no_total", "view_revert", "op_revert", "empty_total0")
EXHAUSTIVE_FAULTS = ("short_masked",)  # QA cp2e-3 F1: every confirmation × depth × restart, outside the seed
OP_SEL = "0x" + keccak(b"getOperatorEarningsSSV(uint64)")[:4].hex()


def _sweep_cases(seed=20261022):
    """Every story × fault, twice, with confirmations, reorg depth and restart point drawn from a fixed seed (the
    draws of the original faults unchanged); the faults added later, every combination."""
    rng = random.Random(seed)
    for story, fault in itertools.product(SWEEP_STORIES, SWEEP_FAULTS):
        for _ in range(2):
            yield story, fault, rng.choice((0, 3)), rng.choice(("1", "N")), rng.choice((None, "detected", "before"))
    yield from itertools.product(SWEEP_STORIES, EXHAUSTIVE_FAULTS, (0, 3), ("1", "N"), (None, "detected", "before"))


class _M7Sweep:
    """The M7 ledger with `truth` = the canonical surplus; every step checks the sweep invariants."""

    def __init__(self, tmp_path, conf, count=2, bystander=False):
        self.lw, self.a = world(tmp_path, confirmations=conf, count=count, bystander=bystander)
        self.conf, self.proxy, self.cssv = conf, BALANCED, STAKED
        self.truth = SSV

    @property
    def w(self):
        return self.lw.w

    def ledger(self, proxy=None, cssv=None):
        self.proxy = self.proxy if proxy is None else proxy
        self.cssv = self.cssv if cssv is None else cssv
        self.lw.ledger(proxy_ssv=self.proxy, cssv_supply=self.cssv, network=NETWORK, operators=OPS)

    def last_run(self):
        r = self.w.db.meta("run:m7", {})
        return r.get("at"), r.get("block"), r.get("reasons")

    def snapshot(self):
        return {r["id"]: dict(r) for r in self.w.db.conn.execute(
            "SELECT * FROM incidents WHERE check_id='m7' ORDER BY rowid")}

    def step(self, seconds=DAY, blocks=1, fault=None):
        lw, before, n_out, prev = self.lw, self.snapshot(), len(outbox(self.w)), self.last_run()
        lw.clock.advance(seconds)
        lw.chain.mine(blocks + self.conf)
        advance(self.w)
        new_rows, after, ran = outbox(self.w)[n_out:], self.snapshot(), self.last_run() != prev
        open_after = {i: r for i, r in after.items() if r["state"] == "open"}
        open_keys = {r["key"] for r in open_after.values()}
        below = self.truth < SSV
        # no resolve without positive proof: a complete ledger at or above the floor
        for i, r in after.items():
            if r["state"] != "open" and before.get(i, {}).get("state") == "open":
                assert r["key"] == "surplus-floor" and not below and fault is None and ran, (r["key"], fault)
        # no open incident without supporting evidence; never a second baseline
        for i, r in open_after.items():
            if i not in before:
                assert (r["key"] == "surplus-floor" and below) or (r["key"] == "surplus" and fault is None), r["key"]
        assert len({i for i, r in after.items() if r["key"] == "surplus"}) <= 1
        if fault is None and ran:
            assert open_keys == {"surplus"} | ({"surplus-floor"} if below else set())
            inc = next(r for r in open_after.values() if r["key"] == "surplus")
            ev = json.loads(inc["evidence"])
            assert inc["revision"] == str(self.truth) and ev["surplus"] == str(self.truth)
            # no window anchored on stale data: the ledger is the one at this run's block
            assert ev["block"] == self.w.db.meta("run:m7")["block"]
        elif fault is not None:  # UNKNOWN or a failed run: reported, the surplus value never changes
            assert any(r["severity"] == "health" and ("M7" in r["text"] or "m7" in r["text"]) for r in new_rows)
            for i, r in open_after.items():
                if r["key"] == "surplus" and i in before:
                    assert r["revision"] == before[i]["revision"]
        # every incident id opens once (no duplicated page, no re-sent baseline)
        opens = [r["incident_id"] for r in outbox(self.w, "kind='open'")]
        assert len(opens) == len(set(opens))
        return new_rows


@pytest.mark.parametrize("story,fault,conf,depth,restart", list(_sweep_cases()))
def test_m7_adversarial_sweep(tmp_path, story, fault, conf, depth, restart):
    # a self-consistent empty page (total 0) meets an inert legacy cluster: daoValidatorCount 0 agrees with it;
    # short_masked (QA cp2e-3 F1): a short page drops the inert cluster, a log-only ETH cluster would offset it
    sw = _M7Sweep(tmp_path, conf, count=0 if fault in ("empty_total0", "short_masked") else 2,
                  bystander=fault == "short_masked")
    lw = sw.lw
    lw.chain.mine(conf)
    sw.step(seconds=0, blocks=0)  # the first run: the 1 SSV baseline
    # no event lost between sweeps: a request and its withdrawal both between two daily runs
    lw.unstake(USER, 20 * SSV)
    lw.chain.mine()
    lw.unstake(USER, 20 * SSV, withdraw=True)
    sw.ledger(proxy=sw.proxy - 20 * SSV, cssv=sw.cssv - 20 * SSV)
    sw.step()
    if story == "repair_orphaned":  # canonical: 5 SSV leave, the page opens
        sw.ledger(proxy=sw.proxy - 5 * SSV)
        sw.truth -= 5 * SSV
        sw.step()
    fork, proxy, cssv, truth = lw.chain.number, sw.proxy, sw.cssv, sw.truth
    if story in ("leak_orphaned", "leak_survives"):
        sw.ledger(proxy=sw.proxy - 5 * SSV)
        sw.truth -= 5 * SSV
    elif story == "repair_orphaned":
        sw.ledger(proxy=sw.proxy + 5 * SSV)
        sw.truth += 5 * SSV
    else:  # unstake_orphaned: request (cSSV burned, SSV kept) on the chain about to be replaced
        lw.unstake("0x" + "cd" * 20, 30 * SSV)
        sw.ledger(cssv=sw.cssv - 30 * SSV)
    sw.step()
    if depth == "N":
        sw.step(blocks=6)
    lw.fork(fork)
    sw.proxy, sw.cssv, sw.truth = proxy, cssv, truth
    if story == "leak_survives":
        sw.ledger(proxy=sw.proxy - 5 * SSV)
        sw.truth -= 5 * SSV
    if fault == "view_revert":
        lw.views(sw.a, balance=CLUSTER, revert=True)
    elif fault == "op_revert":
        lw.chain.stub_call(VIEWS, OP_SEL + u256(2).hex(), revert="0x")
    elif fault == "short_masked":
        _log_only(lw)
        lw.api_fault = "short"
    else:
        lw.api_fault = fault
    if restart == "before":
        lw.restart()
    elif restart == "detected":
        lw.chain.mine(1 + conf)
        sw.w.check_reorg()
        lw.restart()
    sw.step(seconds=601, fault=fault)  # the reorg run (reorg reason; past api_refresh_s)
    lw.api_fault = None
    lw.views(sw.a, balance=CLUSTER)
    lw.chain.stub_call(VIEWS, OP_SEL + u256(2).hex(), u256(OPS[2]))
    sw.step()  # healed
    sw.step()  # and stable
    assert sw.w.db.meta("reorg:m7") is None
    pages = [r for r in sw.snapshot().values() if r["key"] == "surplus-floor"]
    assert len(pages) == {"leak_orphaned": 1, "leak_survives": 1, "repair_orphaned": 2}.get(story, 0)
    assert [r["state"] for r in pages][-1:] == (["open"] if sw.truth < SSV else ["resolved"] if pages else [])
