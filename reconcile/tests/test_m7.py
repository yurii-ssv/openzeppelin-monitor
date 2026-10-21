"""M7 — SSV-side solvency invariant (spec M7). Scenario names are listed in
plans/monitoring-service/workers/cp2e/attempts.md (V2x.1–V2x.5)."""

from __future__ import annotations

import json

import pytest
from fixtures_legacy import CSSV, SEL, SSV, VIEWS, LegacyWorld, owner
from harness import advance, outbox

from eb_storage import keccak, u256

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


def world(tmp_path, m7=None, proxy=BALANCED, cssv=STAKED, **kw):
    lw = LegacyWorld(tmp_path, only=("m7",), **kw)
    lw.checks = {"m7": {"cssv": CSSV, "unstake_history_from": lw.chain.sealed, **(m7 or {})}}
    a = lw.legacy(owner(1), count=2)
    lw.views(a, balance=CLUSTER)
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
