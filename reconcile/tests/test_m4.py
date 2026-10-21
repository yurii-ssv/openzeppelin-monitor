"""M4 — ETH conservation-deficit scan: trigger and benign twin (V2x.1), live inputs (V2x.2), restart (V2x.3),
rpc_budget (V2x.4), the staking-pool interpretation (coordinator ruling 2026-10-21), UNKNOWN inputs, reorg."""

from __future__ import annotations

import json

import pytest
from fixtures_liquidation import (
    OPS,
    OPS2,
    OWNER,
    OWNER2,
    incidents,
    open_incidents,
    restart,
    sent,
    setup,
    stub_m4,
)
from fakechain import PROXY
from harness import VIEWS, advance, outbox

from checks.m3_views import GET_NETWORK_EARNINGS, GET_OPERATOR_EARNINGS
from eb_storage import cluster_id, u256

pytestmark = pytest.mark.m4

M4 = ("m4",)
OPERATORS = {11: 3 * 10**15, 12: 2 * 10**15, 13: 10**15, 14: 10**15, 21: 0, 22: 0, 23: 0, 24: 5 * 10**14}
NETWORK = 7 * 10**15


def liabilities(chain, block, clusters=((OWNER, OPS), (OWNER2, OPS2)), operators=OPERATORS, network=NETWORK):
    return sum(chain.balance_at(cluster_id(o, p), block) for o, p in clusters) + sum(operators.values()) + network


def root(chain, ref=None):
    ref = chain.sealed if ref is None else ref
    chain.emit_event("RootCommitted", ["0x%064x" % (0xB0 + ref), "0x%064x" % ref])
    chain.mine()


def balanced(tmp_path, surplus=0, pool=None, **kw):
    """Two ETH clusters, eight operators; the proxy holds liabilities + `surplus` at the first scan's block."""
    chain, w, cl = setup(tmp_path, checks=M4, **kw)
    cl.create(OWNER, OPS, balance=10**18)
    cl.create(OWNER2, OPS2, balance=2 * 10**18, mine=False)
    at = chain.number  # the pending block becomes the scanned block
    stub_m4(chain, OPERATORS, NETWORK, NETWORK - 10**12 if pool is None else pool,
            liabilities(chain, at) + surplus, last_operator=30)
    for op in range(1, 31):
        if op not in OPERATORS:
            chain.stub_call(VIEWS, GET_OPERATOR_EARNINGS + u256(op), u256(0))
    chain.mine()
    advance(w)  # first run (interval)
    return chain, w, cl


def evidence(w):
    return json.loads(incidents(w, "m4")[-1]["evidence"])


# ---------------------------------------------------------------- V2x.1


def test_deficit_after_root_commit_pages(tmp_path):
    chain, w, cl = balanced(tmp_path, surplus=10**15)
    assert not incidents(w, "m4")
    cl.deposit(OWNER, OPS, value=2 * 10**15)  # liabilities +0.002 ETH, the proxy's ETH unchanged (phantom)
    root(chain)
    advance(w)
    assert sent(w, "m4") == [("open", "page")]
    ev = evidence(w)
    assert int(ev["surplus"]) < 0 and int(ev["surplus"]) > -10**15 - 1 and ev["complete"] is True
    assert "ETH conservation deficit" in outbox(w, "kind='open'")[0]["text"]
    assert w.db.meta("run:m4")["reasons"] == ["events"]


def test_deficit_benign_exact_cover_and_surplus_stay_silent(tmp_path):
    chain, w, cl = balanced(tmp_path, surplus=0)  # proxy ETH == liabilities: deficit 0 is not > 0
    assert not incidents(w, "m4") and not sent(w, "m4")
    s = w.db.conn.execute("SELECT surplus, complete FROM m4_scans").fetchone()
    assert s["surplus"] == "0" and s["complete"] == 1 and not [r for r in outbox(w) if r["kind"] == "health"]
    root(chain)
    advance(w)  # balances burnt since: a growing surplus
    assert not sent(w, "m4")


def test_daily_interval_scans_without_a_root(tmp_path):
    chain, w, cl = balanced(tmp_path, surplus=10**15)
    cl.deposit(OWNER, OPS, value=2 * 10**15)
    chain.mine()
    advance(w)
    assert not sent(w, "m4")  # no root, interval not elapsed
    w.clock.advance(86_400)
    advance(w)
    assert sent(w, "m4") == [("open", "page")] and w.db.meta("run:m4")["reasons"] == ["interval"]


def test_deficit_resolves_with_a_message_when_covered_again(tmp_path):
    chain, w, cl = balanced(tmp_path, surplus=10**15)
    cl.deposit(OWNER, OPS, value=2 * 10**15)
    root(chain)
    advance(w)
    cl.liquidate(OWNER, OPS)  # its balance leaves the liabilities
    root(chain)
    advance(w)
    assert sent(w, "m4") == [("open", "page"), ("resolved", "info")]
    assert not open_incidents(w, "m4")


def test_surplus_trend_is_digest_info(tmp_path):
    chain, w, cl = balanced(tmp_path, surplus=10**15, alerts={"mode": "record", "label": "t",
                                                               "digest_interval_s": 86_400})
    chain.mine(100)
    root(chain)
    advance(w)
    w.clock.advance(86_400)
    chain.mine(10)
    advance(w)
    digests = outbox(w, "kind='digest'")
    assert len(digests) == 2 and digests[-1]["delivery"] == "discord"
    text = digests[-1]["text"]
    assert "[m4]" in text and "Surplus" in text and "Change over ≈ 24 h" in text and not sent(w, "m4")


# ---------------------------------------------------------------- the staking-pool term (ruling 2026-10-21)


def test_staking_pool_is_not_added_as_a_separate_liability(tmp_path):
    # mainnet shape (LIVE block 26,241,393): stakingEthPoolBalance ≈ getNetworkEarnings, surplus ≈ 0. Counting
    # the pool again would report a deficit of the whole pool.
    chain, w, cl = balanced(tmp_path, surplus=457_600_000, pool=NETWORK - 23_681_892_265_000)
    root(chain)
    advance(w)
    assert not incidents(w, "m4") and not sent(w, "m4")
    row = w.db.conn.execute("SELECT * FROM m4_scans ORDER BY block DESC").fetchone()
    assert int(row["surplus"]) > 0 and int(row["surplus"]) - int(row["staking_pool"]) < 0
    assert row["staking_pool"] == str(NETWORK - 23_681_892_265_000)


def test_staking_pool_is_reported_as_evidence_in_a_deficit(tmp_path):
    chain, w, cl = balanced(tmp_path, surplus=-1)
    (msg,) = outbox(w, "kind='open'")
    assert "stakingEthPoolBalance" in msg["text"] and "not added" in msg["text"]
    assert evidence(w)["staking_pool"] == str(NETWORK - 10**12)


# ---------------------------------------------------------------- V2x.2: live inputs, no constants


def test_thresholds_from_chain_network_earnings_change_flips_the_result(tmp_path):
    chain, w, cl = balanced(tmp_path, surplus=10**15)
    chain.stub_call(VIEWS, GET_NETWORK_EARNINGS, u256(NETWORK + 2 * 10**15))
    root(chain)
    advance(w)
    assert sent(w, "m4") == [("open", "page")]


def test_thresholds_from_chain_operator_count_is_read_from_storage(tmp_path):
    from checks.m3_views import MAIN_LAST_OPERATOR_ID

    chain, w, cl = balanced(tmp_path, surplus=10**15)
    chain.stub_call(VIEWS, GET_OPERATOR_EARNINGS + u256(31), u256(2 * 10**15))
    chain.write(MAIN_LAST_OPERATOR_ID, 31)  # a new operator with earnings
    root(chain)
    advance(w)
    assert sent(w, "m4") == [("open", "page")] and evidence(w)["operator_count"] == "31"


# ---------------------------------------------------------------- V2x.3: restart


def test_restart_does_not_duplicate_the_open_deficit(tmp_path):
    chain, w, cl = balanced(tmp_path, surplus=-10**15)
    assert sent(w, "m4") == [("open", "page")]
    w2 = restart(tmp_path, w, checks=M4)
    advance(w2)  # first run after the restart (interval)
    root(chain)
    advance(w2)
    assert sent(w2, "m4") == [("open", "page")] and len(open_incidents(w2, "m4")) == 1
    w2.clock.advance(6 * 3600)
    w2.housekeeping()
    assert [k for k, _ in sent(w2, "m4")] == ["open", "realert"]


# ---------------------------------------------------------------- V2x.4: rpc_budget


def test_rpc_budget_350_clusters(tmp_path):
    chain, w, cl = setup(tmp_path, checks=M4)
    for i in range(350):
        cl.create("0x" + f"{i + 1:040x}", OPS, balance=10**18, mine=False)
    ops = {op: 10**15 for op in range(1, 41)}
    stub_m4(chain, ops, NETWORK, NETWORK, 10**30)
    chain.mine()
    advance(w)
    calls = w.db.meta("run:m4")["rpc_calls"]
    assert calls["eth_call"] <= 350 + 40 + 3 and calls.get("eth_getBalance") == 1
    assert w.db.conn.execute("SELECT complete, eth_clusters FROM m4_scans").fetchone()[:] == (1, 350)


# ---------------------------------------------------------------- UNKNOWN inputs: never clean


@pytest.mark.parametrize("fault", ["revert", "empty", "corrupt", "operators", "last_operator", "hidden_cluster"])
def test_incomplete_inputs_are_unknown_not_resolved(tmp_path, fault):
    chain, w, cl = balanced(tmp_path, surplus=-10**15)
    assert sent(w, "m4") == [("open", "page")]
    chain.set_balance("0xdd9bc35ae942ef0cfa76930954a156b3ff30a4e1", 10**21)  # would resolve if complete
    if fault in ("revert", "empty"):
        chain.view_faults[cluster_id(OWNER, OPS)] = fault
    elif fault == "corrupt":
        cl.corrupt(OWNER2, OPS2)
    elif fault == "hidden_cluster":
        chain.hidden_validators = 1  # a cluster missing from the inventory: its balance is not in the sum
    elif fault == "operators":
        chain.stub_call(VIEWS, GET_OPERATOR_EARNINGS + u256(12), "0x")
    else:
        from checks.m3_views import MAIN_LAST_OPERATOR_ID

        chain.write(MAIN_LAST_OPERATOR_ID, 13)  # below operator 24, referenced by a cluster
    root(chain)
    advance(w)
    assert [r["state"] for r in incidents(w, "m4")] == ["open"] and sent(w, "m4") == [("open", "page")]
    (h,) = [r for r in outbox(w) if r["key"].startswith("health:m4-unknown")]
    assert "not evidence of no deficit" in h["text"]


def test_incomplete_inputs_with_a_deficit_still_page(tmp_path):
    chain, w, cl = balanced(tmp_path, surplus=10**15)
    chain.view_faults[cluster_id(OWNER2, OPS2)] = "revert"  # 2 ETH of liabilities unknown
    chain.stub_call(VIEWS, GET_OPERATOR_EARNINGS + u256(11), u256(3 * 10**18))
    root(chain)
    advance(w)
    assert sent(w, "m4") == [("open", "page")]
    assert "at least this" in outbox(w, "kind='open'")[0]["text"]


def test_no_eth_cluster_is_unknown(tmp_path):
    chain, w, cl = setup(tmp_path, checks=M4)
    stub_m4(chain, {1: 0}, 0, 0, 0)
    chain.mine()
    advance(w)
    assert not incidents(w, "m4") and any(r["key"].startswith("health:m4-unknown") for r in outbox(w))


def test_rpc_failure_raises_and_keeps_the_incident(tmp_path):
    chain, w, cl = balanced(tmp_path, surplus=-10**15)
    chain.set_balance("0xdd9bc35ae942ef0cfa76930954a156b3ff30a4e1", 10**21)
    root(chain)
    chain.down = True
    w.step()
    assert [r["state"] for r in incidents(w, "m4")] == ["open"]
    chain.down = False
    w.step()
    assert [r["state"] for r in incidents(w, "m4")] == ["resolved"]


# ---------------------------------------------------------------- reorg (README §6.2)


@pytest.mark.parametrize("confirmations,depth", [(0, 1), (12, 20)])
def test_reorg_removing_the_deficit_resolves_and_never_repages(tmp_path, confirmations, depth):
    chain, w, cl = balanced(tmp_path, surplus=10**15, confirmations=confirmations)
    chain.mine(confirmations)
    advance(w)
    fork = chain.number
    cl.deposit(OWNER, OPS, value=2 * 10**15)  # the trigger
    root(chain)
    chain.mine(confirmations + depth)
    advance(w)
    assert sent(w, "m4") == [("open", "page")]
    assert w.db.conn.execute("SELECT COUNT(*) FROM m4_scans WHERE block >= ?", (fork,)).fetchone()[0] == 1
    old_scan = w.checkpoint()["block"]
    chain.reorg(fork)
    chain.mine(confirmations + depth + 5)  # the new checkpoint differs from the deleted scan's block
    advance(w)
    assert w.checkpoint()["block"] != old_scan
    assert [r["state"] for r in incidents(w, "m4")] == ["resolved"]
    assert sent(w, "m4") == [("open", "page"), ("resolved", "info")]
    assert [r[0] for r in w.db.conn.execute("SELECT block FROM m4_scans WHERE block >= ?", (fork,))] == \
        [w.checkpoint()["block"]]  # the scan of the deleted block is gone, the reorg run's scan replaces it
    w.clock.advance(10 * 86_400)
    w.housekeeping()
    assert not outbox(w, "kind='realert'")


@pytest.mark.parametrize("confirmations,depth", [(0, 1), (12, 20)])
def test_reorg_with_a_surviving_deficit_keeps_it_open_silently(tmp_path, confirmations, depth):
    chain, w, cl = balanced(tmp_path, surplus=-10**15, confirmations=confirmations)
    chain.mine(confirmations + depth + 2)
    root(chain)
    chain.mine(confirmations)
    advance(w)
    chain.reorg(chain.number - depth)
    chain.mine(depth + 2)
    advance(w)
    assert "reorg" in w.db.meta("run:m4")["reasons"]
    assert sent(w, "m4") == [("open", "page")] and len(open_incidents(w, "m4")) == 1


def test_reorg_crash_between_rewind_and_reorg_run_is_replayed(tmp_path):
    chain, w, cl = balanced(tmp_path, surplus=10**15)
    fork = chain.number
    cl.deposit(OWNER, OPS, value=2 * 10**15)
    root(chain)
    advance(w)
    assert sent(w, "m4") == [("open", "page")]
    chain.reorg(fork)
    chain.mine(4)
    w.check_reorg()
    w2 = restart(tmp_path, w, checks=M4)
    advance(w2)
    assert [r["state"] for r in incidents(w2, "m4")] == ["resolved"] and w2.db.meta("reorg:m4") is None



# ---------------------------------------------------------------- DEFECT-CLASSES.md gaps found by the state table


@pytest.mark.parametrize("confirmations,depth", [(0, 1), (12, 20)])
def test_class2_resolution_on_a_deleted_block_is_paged_again(tmp_path, confirmations, depth):
    """The block that covered the deficit is replaced: the reorg run recomputes the equation and pages again."""
    chain, w, cl = balanced(tmp_path, surplus=10**15, confirmations=confirmations)
    cl.deposit(OWNER, OPS, value=2 * 10**15)
    root(chain)
    chain.mine(confirmations)
    advance(w)
    assert sent(w, "m4") == [("open", "page")]
    fork = chain.number
    chain.set_balance(PROXY, liabilities(chain, chain.number) + 10**16)  # covered from this block on
    root(chain)
    chain.mine(confirmations + depth)
    advance(w)
    assert sent(w, "m4") == [("open", "page"), ("resolved", "info")]
    chain.reorg(fork)
    chain.mine(confirmations + depth + 2)
    advance(w)
    assert [r["state"] for r in incidents(w, "m4")] == ["resolved", "open"]
    assert sent(w, "m4") == [("open", "page"), ("resolved", "info"), ("open", "page")]


@pytest.mark.parametrize("interval", [0, -1])
def test_class11_invalid_configuration_is_a_startup_error(tmp_path, interval):
    with pytest.raises(SystemExit, match="checks.m4"):
        setup(tmp_path, checks=M4, cfg={"m4": {"interval_s": interval}})
