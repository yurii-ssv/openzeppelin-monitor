"""M3 — ETH cluster liquidation zone: one scenario per spec trigger line and a benign twin for each (V2x.1),
thresholds from the chain (V2x.2), restart (V2x.3), rpc_budget (V2x.4), reorg (README §6.2) and UNKNOWN inputs."""

from __future__ import annotations

import pytest
from fixtures_liquidation import (
    BURN,
    OPS,
    OPS2,
    OWNER2,
    THRESHOLD,
    Clusters,
    LiquidationChain,
    cid_hex,
    incidents,
    open_incidents,
    restart,
    sent,
    setup,
)
from harness import advance, outbox, stub_params

from eb_storage import u256

pytestmark = pytest.mark.m3

REORG_TEXT = "Resolved because its evidence left the canonical chain"
DIGEST = {"mode": "record", "label": "test", "digest_interval_s": 86_400}


def evidence(w, key=None):
    import json

    rows = incidents(w, "m3")
    row = next(r for r in reversed(rows) if key is None or r["key"] == key)
    return json.loads(row["evidence"])


def onset(tmp_path, ahead=2, **kw):
    """A cluster that becomes liquidatable `ahead + 1` blocks after it is created, and the run that sees it."""
    chain, w, cl = setup(tmp_path, **kw)
    cl.create(balance=THRESHOLD + ahead * BURN)
    created = chain.sealed
    advance(w)  # full sweep: safe, projected within near_blocks
    assert not open_incidents(w, "m3")
    chain.mine(ahead + 1)
    advance(w)  # near-set run at the crossing block
    return chain, w, cl, created + ahead + 1


# ---------------------------------------------------------------- V2x.1: trigger lines and benign twins


def test_onset_is_info_and_records_the_block(tmp_path):
    chain, w, cl, crossing = onset(tmp_path)
    assert sent(w, "m3") == [("open", "info")]
    inc = open_incidents(w, "m3")[cid_hex()]
    assert inc["severity"] == "info" and evidence(w)["onset_block"] == crossing == chain.sealed
    assert outbox(w, "kind='open'")[0]["delivery"] == "discord"
    assert not [r for r in outbox(w) if r["kind"] == "health"]  # complete inputs: no UNKNOWN noise


def test_onset_benign_cluster_above_threshold_stays_silent(tmp_path):
    chain, w, cl = setup(tmp_path)
    cl.create(balance=THRESHOLD + 100_000 * BURN)
    advance(w)
    chain.mine(400)
    advance(w)
    assert not incidents(w, "m3") and not sent(w, "m3")
    assert w.checks["m3"].near_set == frozenset()


def test_still_liquidatable_after_n_blocks_warns_exactly_at_n(tmp_path):
    chain, w, cl, crossing = onset(tmp_path)
    chain.mine(49)
    advance(w)
    assert chain.sealed - crossing == 49 and sent(w, "m3") == [("open", "info")]
    chain.mine(1)
    advance(w)
    assert sent(w, "m3") == [("open", "info"), ("escalate", "warn")]
    assert evidence(w)["age_blocks"] == 50
    assert outbox(w, "kind='escalate'")[0]["delivery"] == "telegram"


def test_n_blocks_benign_liquidated_before_n_resolves_without_warn(tmp_path):
    chain, w, cl, crossing = onset(tmp_path)
    chain.mine(10)
    cl.liquidate()
    chain.mine(60)
    advance(w)
    assert [r["state"] for r in incidents(w, "m3")] == ["resolved"]
    assert sent(w, "m3") == [("open", "info")]  # an info incident's resolution is not sent


def test_still_liquidatable_after_n2_blocks_pages(tmp_path):
    chain, w, cl, crossing = onset(tmp_path)
    chain.mine(7_199)
    advance(w)
    assert sent(w, "m3") == [("open", "info"), ("escalate", "warn")]
    chain.mine(1)
    advance(w)
    assert sent(w, "m3") == [("open", "info"), ("escalate", "warn"), ("escalate", "page")]
    assert evidence(w)["age_blocks"] == 7_200 and evidence(w)["balance_wei"] != "0"


def test_n2_benign_lifted_by_deposit_before_n2_resolves_with_a_message(tmp_path):
    chain, w, cl, crossing = onset(tmp_path)
    chain.mine(60)
    advance(w)  # warn
    cl.deposit(value=10**18)
    advance(w)
    assert [r["state"] for r in incidents(w, "m3")] == ["resolved"]
    assert sent(w, "m3") == [("open", "info"), ("escalate", "warn"), ("resolved", "info")]
    chain.mine(7_300)
    advance(w)
    assert len(sent(w, "m3")) == 3


def test_computed_zero_pages(tmp_path):
    chain, w, cl = setup(tmp_path)
    cl.create(balance=3 * BURN)  # below the collateral floor: liquidatable at once
    advance(w)
    assert sent(w, "m3") == [("open", "info")]
    chain.mine(3)
    advance(w)
    assert sent(w, "m3") == [("open", "info"), ("escalate", "page")]
    ev = evidence(w)
    assert ev["balance_wei"] == "0" and ev["zero_block"] == chain.sealed and ev["age_blocks"] < 50
    assert "computed zero" in outbox(w, "kind='escalate'")[0]["text"]


def test_computed_zero_pages_even_when_the_view_says_not_liquidatable(tmp_path):
    # spec: "active and getBalance == 0 -> page", independent of isLiquidatable (here: no collateral floor, no burn)
    chain, w, cl = setup(tmp_path)
    stub_params(chain, minimum_liquidation_collateral=0)  # before the first parameter read
    cl.create(balance=0, burn=0)
    advance(w)
    assert sent(w, "m3") == [("open", "page")] and evidence(w)["balance_wei"] == "0"


def test_computed_zero_benign_low_balance_and_liquidated_clusters_do_not_page(tmp_path):
    chain, w, cl = setup(tmp_path)
    cl.create(balance=100 * BURN)
    cl.create(OWNER2, OPS2, balance=10**18)
    cl.liquidate(OWNER2, OPS2)  # inactive, stored balance 0: not a computed-zero cluster
    advance(w)
    chain.mine(40)
    advance(w)
    assert sent(w, "m3") == [("open", "info")]
    assert set(open_incidents(w, "m3")) == {cid_hex()}


def test_pre_alert_projected_within_24h_is_a_digest_line(tmp_path):
    chain, w, cl = setup(tmp_path, alerts=DIGEST)
    cl.create(balance=THRESHOLD + 1_000 * BURN)
    created = chain.sealed
    cl.create(OWNER2, OPS2, balance=THRESHOLD + 8_000 * BURN)  # benign twin: beyond 7,200 blocks
    advance(w)
    # balance(b) = T + 1000 burn - burn (b - created) drops below T after (1000 - (b - created)) // 1 + 1 blocks
    ahead = 1_000 - (chain.sealed - created) + 1
    (digest,) = outbox(w, "kind='digest'")
    assert cid_hex() in digest["text"] and f"in {ahead} blocks" in digest["text"]
    assert cid_hex(OWNER2, OPS2) not in digest["text"]
    assert digest["delivery"] == "discord" and not sent(w, "m3")


# ---------------------------------------------------------------- V2x.2: thresholds from the chain and config


def raise_threshold_period(chain, value):
    stub_params(chain, liquidation_threshold_period=value)
    chain.emit_event("LiquidationThresholdPeriodUpdated", [], u256(value))
    chain.mine()


def test_thresholds_from_chain_threshold_period_change_opens_onset(tmp_path):
    chain, w, cl = setup(tmp_path)
    cl.create(balance=THRESHOLD + 1_000 * BURN)
    advance(w)
    assert not sent(w, "m3")
    raise_threshold_period(chain, 50_000)
    advance(w)
    assert sent(w, "m3") == [("open", "info")]
    assert evidence(w)["threshold_wei"] == str(50_000 * BURN)
    assert "params" in w.db.meta("run:m3")["reasons"]


def test_thresholds_from_chain_projection_follows_the_live_period(tmp_path):
    chain, w, cl = setup(tmp_path)
    cl.create(balance=THRESHOLD + 10_000 * BURN)
    created = chain.sealed
    advance(w)
    assert not w.db.meta("check:m3:pre_alert")["clusters"]
    raise_threshold_period(chain, 46_000)  # 4,040 blocks closer: now within the 7,200-block pre-alert
    advance(w)
    (pre,) = w.db.meta("check:m3:pre_alert")["clusters"]
    balance = THRESHOLD + 10_000 * BURN - (chain.sealed - created) * BURN
    assert pre["cluster_id"] == cid_hex() and pre["blocks"] == (balance - 46_000 * BURN) // BURN + 1


def test_thresholds_from_chain_minimum_collateral_change(tmp_path):
    chain, w, cl = setup(tmp_path)
    cl.create(balance=THRESHOLD + 1_000 * BURN)
    advance(w)
    stub_params(chain, minimum_liquidation_collateral=10**18)
    chain.emit_event("MinimumLiquidationCollateralUpdated", [], u256(10**18))
    chain.mine()
    advance(w)
    assert sent(w, "m3") == [("open", "info")]


def test_thresholds_from_chain_n_blocks_from_config(tmp_path):
    chain, w, cl, crossing = onset(tmp_path, cfg={"m3": {"n_blocks": 10, "n2_blocks": 20}})
    chain.mine(10)
    advance(w)
    assert sent(w, "m3")[-1] == ("escalate", "warn")
    chain.mine(10)
    advance(w)
    assert sent(w, "m3")[-1] == ("escalate", "page")


def test_thresholds_from_chain_unreadable_parameters_skip_the_run(tmp_path):
    chain, w, cl, crossing = onset(tmp_path)
    from params import GETTERS, selector

    chain.stub_call(w.cfg.views, selector(GETTERS["liquidation_threshold_period"]), revert="0x")
    chain.emit_event("LiquidationThresholdPeriodUpdated", [], u256(1))
    cl.deposit(value=10**18)  # would resolve, but the run must not evaluate without live parameters
    advance(w)
    assert [r["state"] for r in incidents(w, "m3")] == ["open"]
    assert w.db.meta("run:m3")["last_error"].startswith("ParamsUnavailable")


# ---------------------------------------------------------------- V2x.3: restart


def test_restart_does_not_duplicate_and_keeps_the_onset(tmp_path):
    chain, w, cl, crossing = onset(tmp_path)
    chain.mine(60)
    advance(w)
    assert sent(w, "m3") == [("open", "info"), ("escalate", "warn")]
    w2 = restart(tmp_path, w)
    advance(w2)
    chain.mine()
    advance(w2)
    assert sent(w2, "m3") == [("open", "info"), ("escalate", "warn")]
    assert len(open_incidents(w2, "m3")) == 1 and evidence(w2)["onset_block"] == crossing
    chain.mine(7_200 - (chain.sealed - crossing))
    advance(w2)
    assert sent(w2, "m3")[-1] == ("escalate", "page")


def test_restart_first_sweep_without_baseline_is_info_with_a_lower_bound(tmp_path):
    chain = LiquidationChain()
    stub_params(chain)
    chain.mine()
    clusters = Clusters(chain)
    clusters.create(balance=THRESHOLD - 30_000 * BURN)  # liquidatable for ~30,000 blocks before the worker
    chain.mine(100)
    chain, w, _ = setup(tmp_path, chain=chain, start_block=1000, activation_block=1000)
    advance(w)
    assert sent(w, "m3") == [("open", "info")]
    assert evidence(w)["not_liquidatable_before"] is None
    assert "age is a lower bound" in outbox(w, "kind='open'")[0]["text"]


# ---------------------------------------------------------------- V2x.4: rpc_budget


def test_rpc_budget_full_sweep_and_near_set_run_350_clusters(tmp_path):
    chain, w, cl = setup(tmp_path)
    for i in range(350):
        owner = "0x" + f"{i + 1:040x}"
        cl.create(owner, OPS, balance=(THRESHOLD + 2 * BURN) if i < 5 else 10**18, mine=False)
    chain.mine()
    advance(w)
    assert w.db.meta("run:m3")["rpc_calls"].get("eth_call", 0) <= 3 * 350 + 1  # + the completeness count
    assert len(w.checks["m3"].near_set) == 5
    builds = []
    real = w.inventory.get
    w.inventory.get = lambda b: builds.append(b) or real(b)
    chain.mine()
    advance(w)  # near-set run: views on the 5 near clusters only, no inventory rebuild
    run = w.db.meta("run:m3")
    assert run["reasons"] == ["block"] and run["rpc_calls"].get("eth_call", 0) <= 3 * 5 and not builds


# ---------------------------------------------------------------- reorg (README §6.2)


@pytest.mark.parametrize("confirmations,depth", [(0, 1), (12, 20)])
def test_reorg_removing_the_trigger_resolves_and_never_repages(tmp_path, confirmations, depth):
    chain, w, cl = setup(tmp_path, confirmations=confirmations, cfg={"m3": {"n_blocks": 2}})
    cl.create(balance=THRESHOLD + 1_000 * BURN)
    chain.mine(confirmations)
    advance(w)
    fork = chain.number
    cl.withdraw(value=1_100 * BURN)  # the trigger: liquidatable from this block
    chain.mine(confirmations)
    advance(w)  # onset observed
    chain.mine(depth + 2)
    advance(w)
    assert sent(w, "m3") == [("open", "info"), ("escalate", "warn")]
    assert w.checkpoint()["block"] - fork + 1 >= depth  # the reorg below replaces at least `depth` recorded blocks
    chain.reorg(fork)  # the withdrawal is gone
    chain.mine(depth + confirmations + 5)
    advance(w)
    assert [r["state"] for r in incidents(w, "m3")] == ["resolved"]
    res = outbox(w, "kind='resolved'")
    assert len(res) == 1 and "replaced by a chain reorganisation" in res[0]["text"]
    w.clock.advance(10 * 86_400)
    chain.mine(10)
    advance(w)
    w.housekeeping()
    assert not outbox(w, "kind='realert'") and not w.db.conn.execute("SELECT * FROM m3_zone").fetchall()


@pytest.mark.parametrize("confirmations,depth", [(0, 1), (12, 20)])
def test_reorg_above_a_surviving_onset_keeps_the_incident_silently(tmp_path, confirmations, depth):
    chain, w, cl, crossing = onset(tmp_path, confirmations=confirmations)
    chain.mine(confirmations)
    advance(w)
    chain.mine(depth + 2)
    advance(w)
    chain.reorg(chain.number - depth)
    chain.mine(depth + 2)
    advance(w)
    assert any(r["key"].startswith("health:reorg") for r in outbox(w)) and "reorg" in w.db.meta("run:m3")["reasons"]
    assert sent(w, "m3") == [("open", "info")] and len(open_incidents(w, "m3")) == 1
    assert evidence(w)["onset_block"] == crossing


@pytest.mark.parametrize("confirmations,depth", [(0, 1), (12, 20)])
def test_reorg_onset_above_ancestor_still_liquidatable_is_reported_again(tmp_path, confirmations, depth):
    chain, w, cl = setup(tmp_path, confirmations=confirmations)
    cl.create(balance=THRESHOLD + 1_000 * BURN)
    chain.mine(confirmations)
    advance(w)
    fork = chain.number
    cl.withdraw(value=1_100 * BURN)
    chain.mine(confirmations + depth)
    advance(w)
    assert sent(w, "m3") == [("open", "info")]
    chain.reorg(fork)
    cl.withdraw(value=1_200 * BURN)  # the new chain has its own trigger
    chain.mine(confirmations + depth + 2)
    advance(w)
    assert [r["state"] for r in incidents(w, "m3")] == ["open"] and sent(w, "m3") == [("open", "info")]
    assert evidence(w)["onset_block"] == w.checkpoint()["block"]  # re-observed on the new chain


def test_reorg_crash_between_rewind_and_reorg_run_is_replayed(tmp_path):
    chain, w, cl = setup(tmp_path, cfg={"m3": {"n_blocks": 2}})
    cl.create(balance=THRESHOLD + 1_000 * BURN)
    advance(w)
    fork = chain.number
    cl.withdraw(value=1_100 * BURN)
    advance(w)
    chain.mine(3)
    advance(w)
    assert sent(w, "m3") == [("open", "info"), ("escalate", "warn")]
    chain.reorg(fork)
    chain.mine(6)
    w.check_reorg()  # the rewind commits (marker written) and the process dies before the checks run
    assert w.db.meta("reorg:m3") is not None
    w2 = restart(tmp_path, w)
    advance(w2)
    assert [r["state"] for r in incidents(w2, "m3")] == ["resolved"] and w2.db.meta("reorg:m3") is None
    assert REORG_TEXT not in outbox(w2, "kind='resolved'")[0]["text"]  # the check's own text
    assert "replaced by a chain reorganisation" in outbox(w2, "kind='resolved'")[0]["text"]


def test_reorg_tracked_cluster_unknown_in_the_reorg_run_stays_open(tmp_path):
    chain, w, cl, crossing = onset(tmp_path)
    chain.mine(2)
    advance(w)
    chain.reorg(chain.number - 1)
    chain.view_faults[bytes.fromhex(cid_hex()[2:])] = "revert"
    chain.mine(2)
    advance(w)
    assert [r["state"] for r in incidents(w, "m3")] == ["open"]
    assert any(r["key"].startswith("health:m3-unknown") for r in outbox(w))


# ---------------------------------------------------------------- UNKNOWN inputs, ordering, RPC failure


@pytest.mark.parametrize("fault", ["revert", "empty"])
def test_unknown_view_result_is_never_clean(tmp_path, fault):
    chain, w, cl, crossing = onset(tmp_path)
    chain.view_faults[bytes.fromhex(cid_hex()[2:])] = fault
    cl.deposit(value=1)  # touches the cluster: evaluated, but the views fail
    w.clock.advance(601)
    advance(w)
    assert [r["state"] for r in incidents(w, "m3")] == ["open"]
    (h,) = [r for r in outbox(w) if r["key"].startswith("health:m3-unknown")]
    assert cid_hex() in h["text"] and "not treated as clean" in h["text"]


def test_unknown_unvalidated_tuple_is_reported_not_resolved(tmp_path):
    chain, w, cl, crossing = onset(tmp_path)
    cl.corrupt()
    chain.mine()
    w.clock.advance(601)
    advance(w)
    assert [r["state"] for r in incidents(w, "m3")] == ["open"]
    assert any(r["key"].startswith("health:m3-unknown") for r in outbox(w))


def test_unknown_incomplete_inventory_is_health_on_every_full_sweep(tmp_path):
    chain, w, cl = setup(tmp_path)
    cl.create(balance=10**18)
    advance(w)
    assert not [r for r in outbox(w) if r["key"].startswith("health:m3-unknown")]  # complete: silent
    chain.hidden_validators = 3  # the chain counts validators of a cluster neither the API nor the logs list
    chain.mine()
    w.clock.advance(600)
    advance(w)
    (h,) = [r for r in outbox(w) if r["key"].startswith("health:m3-unknown")]
    assert "inventory incomplete" in h["text"] and "getNetworkValidatorsCount() is 4" in h["text"]


def test_same_block_withdraw_then_deposit_uses_the_end_of_block_tuple(tmp_path):
    chain, w, cl = setup(tmp_path)
    cl.create(balance=THRESHOLD + 1_000 * BURN)
    advance(w)
    cl.withdraw(value=1_100 * BURN, mine=False)  # liquidatable after the first tx of the block …
    cl.deposit(value=10**18)  # … lifted by the second
    advance(w)
    assert not sent(w, "m3")
    cl.deposit(value=1, mine=False)
    cl.withdraw(value=10**18 + 1_200 * BURN)  # the reverse order ends liquidatable
    advance(w)
    assert sent(w, "m3") == [("open", "info")]


def test_rpc_failure_raises_and_resolves_nothing(tmp_path):
    chain, w, cl, crossing = onset(tmp_path)
    cl.deposit(value=10**18)
    chain.down = True
    w.step()
    assert [r["state"] for r in incidents(w, "m3")] == ["open"]
    chain.down = False
    w.step()
    assert [r["state"] for r in incidents(w, "m3")] == ["resolved"]


def test_burn_rate_increase_without_an_event_is_caught_by_the_full_sweep(tmp_path):
    chain, w, cl = setup(tmp_path)
    cl.create(balance=THRESHOLD + 20_000 * BURN)
    advance(w)
    cl.set_burn(burn=2 * BURN)  # an operator fee change: no cluster event
    chain.mine()
    advance(w)
    assert not sent(w, "m3")  # not near, no event: waits for the full sweep
    w.clock.advance(600)
    chain.mine()
    advance(w)
    assert sent(w, "m3") == [("open", "info")]


def test_full_sweep_interval_holds_while_the_near_set_runs_every_block(tmp_path):
    chain, w, cl = setup(tmp_path)
    cl.create(balance=THRESHOLD + 200 * BURN)  # near: the check runs at every block from now on
    cl.create(OWNER2, OPS2, balance=THRESHOLD + 20_000 * BURN)
    advance(w)
    cl.set_burn(OWNER2, OPS2, burn=2 * BURN)  # far cluster: no event, only the full sweep can see it
    for _ in range(5):
        chain.mine()
        w.clock.advance(120)
        advance(w)
    assert set(open_incidents(w, "m3")) == {cid_hex(OWNER2, OPS2)}
    assert w.db.meta("run:m3")["reasons"] == ["block"]  # found by a near-set run that was due for a full sweep


def test_missing_views_setting_is_a_startup_error(tmp_path):
    from checks.m3_liquidation_zone import M3LiquidationZone

    with pytest.raises(SystemExit, match="views"):
        M3LiquidationZone(dict(M3LiquidationZone.defaults), {"proxy": "0x" + "00" * 20})

