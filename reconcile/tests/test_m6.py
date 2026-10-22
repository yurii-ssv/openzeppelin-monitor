"""M6 — legacy SSV clusters: liquidation coverage (spec M6). Scenario names are listed in
plans/monitoring-service/workers/cp2e/attempts.md (V2x.1–V2x.5)."""

from __future__ import annotations

import json

import pytest
from fixtures_legacy import BURN, PERIOD_SSV, SSV, VIEWS, LegacyWorld, owner, param_change
from harness import advance, outbox, stub_params

from eb_storage import u256

pytestmark = pytest.mark.m6

W = PERIOD_SSV * 5000 // 10_000  # half the SSV liquidation window at the dated value (50,120 blocks)


def pages(w):
    return outbox(w, "severity='page'")


def incidents(w, state="open"):
    return [dict(r) for r in w.db.conn.execute(
        "SELECT * FROM incidents WHERE check_id='m6' AND state=? ORDER BY opened_block", (state,))]


def world(tmp_path, **kw):
    return LegacyWorld(tmp_path, only=("m6",), **kw)


# ---------------------------------------------------------------- V2x.1: page — zero balance while active


def test_m6_zero_balance_while_active_pages(tmp_path):
    lw = world(tmp_path)
    a = lw.legacy(owner(1), count=2)
    lw.views(a, balance=0, liquidatable=True)
    lw.chain.mine()
    w = lw.start()
    advance(w)
    rows = outbox(w)
    assert [(r["severity"], r["kind"]) for r in rows] == [("page", "open")]
    assert a.cid in rows[0]["text"] and "balance of 0" in rows[0]["text"] and rows[0]["delivery"] == "telegram"
    inc = incidents(w)
    assert [(i["key"], i["severity"]) for i in inc] == [(a.cid, "page")]
    ev = json.loads(inc[0]["evidence"])
    assert ev["balance_ssv_wei"] == "0" and ev["burn_rate_ssv_wei"] == str(BURN)


def test_m6_benign_zero_balance_without_validators_stays_silent(tmp_path):
    """An inert legacy cluster (validatorCount 0) burns nothing: a zero balance creates no unbacked credit."""
    lw = world(tmp_path)
    a = lw.legacy(owner(1), count=0)
    lw.views(a, balance=0, liquidatable=False, burn=0)
    lw.chain.mine()
    w = lw.start()
    advance(w)
    assert outbox(w) == [] and incidents(w) == []
    assert w.db.meta("check:m6:summary")["inert"] == 1


def test_m6_benign_positive_balance_not_liquidatable_stays_silent(tmp_path):
    lw = world(tmp_path)
    a = lw.legacy(owner(1), count=3)
    lw.views(a, balance=500 * SSV, liquidatable=False)
    lw.chain.mine()
    w = lw.start()
    advance(w)
    lw.sweep()
    assert outbox(w) == [] and incidents(w) == []
    assert w.db.meta("run:m6")["findings"] == 0


# ---------------------------------------------------------------- V2x.1: warn — liquidatable > half the window


def test_m6_liquidatable_longer_than_half_window_warns(tmp_path):
    """No baseline (worker start): the start is back-dated from balance and burn: W + 1 blocks below the threshold."""
    lw = world(tmp_path)
    a = lw.legacy(owner(1))
    lw.views(a, balance=lw.deficit(W + 1), liquidatable=True)
    lw.chain.mine()
    w = lw.start()
    advance(w)
    rows = outbox(w)
    assert [(r["severity"], r["kind"]) for r in rows] == [("warn", "open")]
    ev = json.loads(incidents(w)[0]["evidence"])
    blk = w.checkpoint()["block"]
    assert ev["liquidatable_since_block"] == blk - (W + 1) and ev["since_estimated"] is True
    assert ev["liquidatable_blocks"] == W + 1 and ev["warn_after_blocks"] == W


def test_m6_benign_liquidatable_exactly_half_window_stays_silent(tmp_path):
    lw = world(tmp_path)
    a = lw.legacy(owner(1))
    lw.views(a, balance=lw.deficit(W), liquidatable=True)
    lw.chain.mine()
    w = lw.start()
    advance(w)
    assert outbox(w) == [] and incidents(w) == []
    row = w.db.conn.execute("SELECT * FROM m6_clusters").fetchone()
    assert row["liquidatable_since"] == w.checkpoint()["block"] - W


def test_m6_observed_start_is_not_back_dated_before_the_last_clear_sweep(tmp_path):
    """Seen clear at one sweep, deep below the threshold at the next: the start is the block after the clear
    sweep, so the warn waits for more than W blocks of observed liquidatability."""
    lw = world(tmp_path)
    a = lw.legacy(owner(1))
    lw.views(a, balance=200 * SSV, liquidatable=False)
    lw.chain.mine()
    w = lw.start()
    advance(w)
    clear = w.checkpoint()["block"]
    lw.views(a, balance=lw.deficit(80_000), liquidatable=True)  # an operator fee jump, say
    lw.sweep()
    assert outbox(w) == []
    assert w.db.conn.execute("SELECT liquidatable_since FROM m6_clusters").fetchone()[0] == clear + 1
    lw.sweep(blocks=W - (w.checkpoint()["block"] - clear - 1))  # lag == W: still silent
    assert outbox(w) == []
    lw.sweep()  # lag == W + 1
    assert [r["severity"] for r in outbox(w)] == ["warn"]


def test_m6_warn_escalates_to_page_under_the_same_incident_and_resolves_when_liquidated(tmp_path):
    lw = world(tmp_path)
    a = lw.legacy(owner(1))
    lw.views(a, balance=lw.deficit(W + 10), liquidatable=True)
    lw.chain.mine()
    w = lw.start()
    advance(w)
    lw.views(a, balance=0, liquidatable=True)
    lw.sweep()
    rows = outbox(w)
    assert [(r["severity"], r["kind"]) for r in rows] == [("warn", "open"), ("page", "escalate")]
    assert rows[0]["incident_id"] == rows[1]["incident_id"]
    lw.ssv.liquidate(a.owner, a.ops)  # ClusterLiquidated log: inactive tuple validates from logs
    lw.sweep()
    rows = outbox(w)
    assert [(r["severity"], r["kind"]) for r in rows][-1] == ("info", "resolved")
    assert "was liquidated" in rows[-1]["text"] and incidents(w) == []
    assert w.db.conn.execute("SELECT COUNT(*) FROM m6_clusters").fetchone()[0] == 0


def test_m6_migration_to_eth_resolves(tmp_path):
    lw = world(tmp_path)
    a = lw.legacy(owner(1))
    lw.views(a, balance=0, liquidatable=True)
    lw.chain.mine()
    w = lw.start()
    advance(w)
    lw.ssv.migrate(a.owner, a.ops)
    lw.sweep()
    assert outbox(w)[-1]["kind"] == "resolved" and "migrated to ETH" in outbox(w)[-1]["text"]
    assert incidents(w) == []


def test_m6_cluster_no_longer_liquidatable_resolves(tmp_path):
    """E.g. a validator removal lowers the burn and with it the threshold."""
    lw = world(tmp_path)
    a = lw.legacy(owner(1))
    lw.views(a, balance=lw.deficit(W + 5), liquidatable=True)
    lw.chain.mine()
    w = lw.start()
    advance(w)
    lw.views(a, balance=lw.deficit(W + 6), liquidatable=False)
    lw.sweep()
    assert [r["kind"] for r in outbox(w)] == ["open", "resolved"]
    assert w.db.conn.execute("SELECT liquidatable_since FROM m6_clusters").fetchone()[0] is None


def test_m6_validator_count_zero_to_positive_at_zero_balance_pages_on_that_block(tmp_path):
    """Coordinator ruling (Q2): an inert zero-balance cluster is not paged, but the block that gives it a validator
    while the balance is 0 pages (an event-scoped run, without waiting for the hourly sweep)."""
    lw = world(tmp_path)
    a = lw.legacy(owner(1), count=0)
    lw.views(a, balance=0, liquidatable=False, burn=0)
    lw.chain.mine()
    w = lw.start()
    advance(w)
    assert outbox(w) == []
    blk = lw.chain.number
    lw.add_validators(a, 1)
    lw.views(a, balance=0, liquidatable=True)  # the views answer for the new tuple
    lw.chain.mine()
    advance(w)  # no clock advance: the interval is not due, the ValidatorAdded event is
    assert [(r["severity"], r["kind"]) for r in outbox(w)] == [("page", "open")]
    inc = incidents(w)[0]
    assert inc["opened_block"] == blk and set(w.db.meta("run:m6")["reasons"]) == {"events"}


def test_m6_event_run_resolves_a_liquidation_at_once_and_ignores_eth_clusters(tmp_path):
    lw = world(tmp_path)
    a = lw.legacy(owner(1))
    lw.views(a, balance=0, liquidatable=True)
    lw.chain.mine()
    w = lw.start()
    advance(w)
    lw.ssv.register(owner(9), (5, 6, 7, 8), [b"\x01" * 48])  # an ETH cluster event: in scope, not legacy
    advance(w)
    assert w.db.meta("run:m6")["rpc_calls"].get("eth_call", 0) == 0 and len(outbox(w)) == 1
    lw.ssv.liquidate(a.owner, a.ops)
    advance(w)
    assert [r["kind"] for r in outbox(w)] == ["open", "resolved"] and incidents(w) == []


# ---------------------------------------------------------------- UNKNOWN is never clean


def test_m6_unvalidated_tuple_is_unknown_and_keeps_the_incident_open(tmp_path):
    lw = world(tmp_path)
    a = lw.legacy(owner(1))
    lw.views(a, balance=0, liquidatable=True)
    lw.chain.mine()
    w = lw.start()
    advance(w)
    # storage moves on, the API row does not (and no log carries the new tuple): no candidate validates
    st = lw.ssv.clusters[bytes.fromhex(a.cid[2:])]
    st["index"] += 5
    lw.ssv._persist(bytes.fromhex(a.cid[2:]), st)
    lw.sweep()
    assert [i["severity"] for i in incidents(w)] == ["page"]
    health = outbox(w, "severity='health'")
    assert len(health) == 1 and "UNKNOWN" in health[0]["text"] and a.cid in health[0]["text"]
    assert [r["kind"] for r in outbox(w, "severity!='health'")] == ["open"]


@pytest.mark.parametrize("answer", ["revert", "empty"])
def test_m6_reverting_or_empty_view_is_unknown_not_clean(tmp_path, answer):
    lw = world(tmp_path)
    a, b = lw.legacy(owner(1)), lw.legacy(owner(2))
    lw.views(a, balance=lw.deficit(W + 1), liquidatable=True)
    lw.views(b, balance=0, liquidatable=True)
    lw.chain.mine()
    w = lw.start()
    advance(w)
    assert {i["key"]: i["severity"] for i in incidents(w)} == {a.cid: "warn", b.cid: "page"}
    if answer == "revert":
        lw.views(b, balance=0, liquidatable=True, revert=True)
    else:
        lw.chain.stub_call(VIEWS, lw.calldata("getBalanceSSV", b), b"")
    lw.sweep()
    assert {i["key"]: i["severity"] for i in incidents(w)} == {a.cid: "warn", b.cid: "page"}
    health = outbox(w, "severity='health'")
    assert len(health) == 1 and b.cid in health[0]["text"] and a.cid not in health[0]["text"]
    assert w.db.meta("check:m6:summary")["unknown"] == 1


def test_m6_api_failure_is_a_check_error_not_an_empty_inventory(tmp_path):
    lw = world(tmp_path)
    a = lw.legacy(owner(1))
    lw.views(a, balance=0, liquidatable=True)
    lw.chain.mine()
    w = lw.start()
    advance(w)

    def down(url):
        raise OSError("SSV API unreachable")

    w.inventory.http_get = down
    lw.sweep()
    assert [i["severity"] for i in incidents(w)] == ["page"]
    assert any("check-error" in r["text"] for r in outbox(w, "severity='health'"))
    assert w.db.meta("run:m6")["errors"] == 1


def test_m6_requires_views_and_the_api(tmp_path):
    lw = world(tmp_path)
    with pytest.raises(SystemExit, match="views"):
        lw.start(views=None)
    with pytest.raises(SystemExit, match="api_url"):
        lw.start(inventory={"api_url": None})


# ---------------------------------------------------------------- V2x.2 thresholds_from_chain


def test_m6_thresholds_from_chain_period_change_moves_the_warn(tmp_path):
    lw = world(tmp_path)
    a = lw.legacy(owner(1))
    lw.views(a, balance=lw.deficit(40_000), liquidatable=True)
    lw.chain.mine()
    w = lw.start()
    advance(w)
    assert outbox(w) == []  # 40,000 <= 50,120
    param_change(lw.chain, liquidation_threshold_period_ssv=70_000)  # half window 35,000
    lw.chain.mine()
    advance(w)
    assert [r["severity"] for r in outbox(w)] == ["warn"]
    ev = json.loads(incidents(w)[0]["evidence"])
    assert ev["warn_after_blocks"] == 35_000 and ev["liquidation_threshold_period_ssv"] == 70_000


def test_m6_thresholds_from_chain_back_dating_uses_the_live_minimum_collateral(tmp_path):
    """Above period × burn, the live minimum collateral sets the threshold the back-dating starts from."""
    lw = world(tmp_path, params={"minimum_liquidation_collateral_ssv": PERIOD_SSV * BURN + (W + 1) * BURN})
    a = lw.legacy(owner(1))
    lw.views(a, balance=PERIOD_SSV * BURN, liquidatable=True)  # exactly period × burn: below the collateral rule
    lw.chain.mine()
    w = lw.start()
    advance(w)
    assert [r["severity"] for r in outbox(w)] == ["warn"]
    assert json.loads(incidents(w)[0]["evidence"])["liquidatable_blocks"] == W + 1


def test_m6_params_unavailable_skips_the_run(tmp_path):
    lw = world(tmp_path)
    a = lw.legacy(owner(1))
    lw.views(a, balance=0, liquidatable=True)
    lw.chain.mine()
    w = lw.start()
    lw.chain.stub_call(VIEWS, "0x" + "00" * 4, b"")  # unrelated
    from params import GETTERS, selector
    lw.chain.stub_call(VIEWS, selector(GETTERS["liquidation_threshold_period_ssv"]), revert="0x")
    lw.chain.mine()
    advance(w)
    assert outbox(w, "severity!='health'") == [] and w.db.meta("run:m6", {}).get("block") is None


# ---------------------------------------------------------------- V2x.3 restart


def test_m6_restart_does_not_duplicate_the_open_incident(tmp_path):
    lw = world(tmp_path)
    a, b = lw.legacy(owner(1)), lw.legacy(owner(2))
    lw.views(a, balance=lw.deficit(W + 1), liquidatable=True)
    lw.views(b, balance=0, liquidatable=True)
    lw.chain.mine()
    w = lw.start()
    advance(w)
    since = w.db.conn.execute("SELECT liquidatable_since FROM m6_clusters WHERE cluster_id=?", (a.cid,)).fetchone()[0]
    assert len(outbox(w)) == 2
    lw.clock.advance(600)
    lw.chain.mine()
    w = lw.restart()
    advance(w)
    lw.sweep()
    assert len(outbox(w)) == 2 and len(incidents(w)) == 2
    assert w.db.conn.execute("SELECT liquidatable_since FROM m6_clusters WHERE cluster_id=?",
                             (a.cid,)).fetchone()[0] == since  # the observed start survives the restart


def test_m6_restart_with_a_prior_liquidatable_row_keeps_counting(tmp_path):
    """Prior non-zero state then restart: the persisted start, not a new back-dating, decides the warn."""
    lw = world(tmp_path)
    a = lw.legacy(owner(1))
    lw.views(a, balance=200 * SSV, liquidatable=False)
    lw.chain.mine()
    w = lw.start()
    advance(w)
    clear = w.checkpoint()["block"]
    lw.views(a, balance=lw.deficit(90_000), liquidatable=True)
    lw.sweep()
    w = lw.restart()
    lw.clock.advance(3600)
    lw.chain.mine(W)
    advance(w)
    assert w.checkpoint()["block"] - (clear + 1) == W and outbox(w) == []
    lw.sweep()
    assert [r["severity"] for r in outbox(w)] == ["warn"]


# ---------------------------------------------------------------- V2x.4 rpc_budget


def test_m6_rpc_budget_350_cluster_inventory(tmp_path):
    lw = world(tmp_path)
    for i in range(350):
        lw.legacy(owner(i + 1), count=1 + i % 3)
    from fixtures_legacy import SEL
    lw.chain.stub_call(VIEWS, SEL["getBalanceSSV"], u256(100 * SSV))
    lw.chain.stub_call(VIEWS, SEL["isLiquidatableSSV"], u256(0))
    lw.chain.stub_call(VIEWS, SEL["getBurnRateSSV"], u256(BURN))
    lw.chain.mine()
    w = lw.start()
    advance(w)
    calls = w.db.meta("run:m6")["rpc_calls"]
    assert calls.get("eth_call", 0) <= 3 * 350, calls
    assert w.db.meta("check:m6:summary")["active"] == 350 and outbox(w) == []


# ---------------------------------------------------------------- digest


def test_m6_weekly_digest_counts_clusters_validators_and_burn(tmp_path):
    lw = world(tmp_path, alerts={"mode": "record", "label": "test", "digest_interval_s": 86_400})
    a, b, c = lw.legacy(owner(1), count=2), lw.legacy(owner(2), count=3), lw.legacy(owner(3), count=0)
    d = lw.legacy(owner(4), count=0)
    lw.views(a, balance=100 * SSV, burn=BURN)
    lw.views(b, balance=100 * SSV, burn=2 * BURN)
    lw.views(c, balance=7 * SSV, burn=0)
    lw.views(d, balance=0, burn=0)  # inert at zero: not paged, named in the digest
    lw.chain.mine()
    w = lw.start()
    advance(w)
    digests = outbox(w, "kind='digest'")
    assert len(digests) == 1 and digests[0]["delivery"] == "discord"
    line = digests[0]["text"]
    assert "4 active (2 without validators), 5 validators" in line and "7.884 SSV/yr" in line  # 3e12 × 2,628,000
    assert f"Not evaluated for the zero-balance page (0 validators, so no burn): 1 active legacy cluster(s) at " \
           f"balance 0, e.g. {d.cid}." in line
    for _ in range(6):  # daily digests within the week carry no M6 section
        lw.sweep(seconds=86_400)
    assert len(outbox(w, "kind='digest'")) == 7 and all("[m6]" not in r["text"] for r in outbox(w, "kind='digest'")[1:])
    lw.sweep(seconds=86_400)
    assert "[m6]" in outbox(w, "kind='digest'")[-1]["text"]


# ---------------------------------------------------------------- reorgs (README §6.2)


def _page_at_fork(tmp_path, confirmations=0):
    lw = world(tmp_path, confirmations=confirmations)
    a = lw.legacy(owner(1))
    lw.views(a, balance=30 * SSV, liquidatable=False)
    lw.chain.mine()
    w = lw.start()
    lw.chain.mine(confirmations)
    advance(w)
    assert w.checkpoint()["hash"]  # a canonical block below the fork is recorded
    lw.clock.advance(3600)
    fork = lw.chain.number
    lw.views(a, balance=0, liquidatable=True)  # the zero balance appears in block `fork`
    lw.chain.mine(1 + confirmations)
    advance(w)
    assert [r["severity"] for r in pages(w)] == ["page"] and w.checkpoint()["block"] >= fork
    return lw, w, a, fork


def test_m6_reorg_depth_1_removing_the_zero_balance_resolves(tmp_path):
    lw, w, a, fork = _page_at_fork(tmp_path)
    lw.fork(fork)
    lw.chain.mine()
    advance(w)
    assert incidents(w) == [] and len(pages(w)) == 1
    assert outbox(w)[-1]["kind"] == "resolved"
    lw.sweep()
    lw.clock.advance(7 * 3600)
    advance(w)
    assert len(pages(w)) == 1  # never re-paged


def test_m6_reorg_depth_n_beyond_the_confirmation_window_resolves(tmp_path):
    lw, w, a, fork = _page_at_fork(tmp_path, confirmations=3)
    lw.chain.mine(6)
    advance(w)
    lw.fork(fork)  # depth > confirmations: below the checkpoint
    lw.chain.mine(5)
    advance(w)
    assert w.db.meta("reorg:m6") is None and incidents(w) == []
    assert len(pages(w)) == 1 and outbox(w)[-1]["kind"] == "resolved"


def test_m6_reorg_crash_between_detection_and_the_check_run(tmp_path):
    lw, w, a, fork = _page_at_fork(tmp_path)
    lw.fork(fork)
    lw.chain.mine()
    w.check_reorg()  # rewound and committed; the process dies before M6 runs
    assert w.db.meta("reorg:m6") is not None
    w = lw.restart()
    advance(w)
    assert w.db.meta("reorg:m6") is None and incidents(w) == [] and len(pages(w)) == 1


def test_m6_reorg_where_the_condition_survives_keeps_the_incident_without_a_new_page(tmp_path):
    lw, w, a, fork = _page_at_fork(tmp_path)
    inc = incidents(w)[0]["id"]
    lw.fork(fork)
    lw.views(a, balance=0, liquidatable=True)  # the new chain zeroes it too
    lw.chain.mine()
    advance(w)
    assert [i["id"] for i in incidents(w)] == [inc] and len(pages(w)) == 1


def test_m6_reorg_drops_a_liquidatable_start_observed_on_deleted_blocks(tmp_path):
    """Liquidatable first observed above the ancestor: the start is re-derived on the new chain."""
    lw = world(tmp_path)
    a = lw.legacy(owner(1))
    lw.views(a, balance=200 * SSV, liquidatable=False)
    lw.chain.mine()
    w = lw.start()
    advance(w)
    lw.clock.advance(3600)
    fork = lw.chain.number
    lw.views(a, balance=lw.deficit(10), liquidatable=True)
    lw.chain.mine()
    advance(w)
    assert w.db.conn.execute("SELECT first_liquidatable_block FROM m6_clusters").fetchone()[0] == fork
    lw.fork(fork)
    lw.chain.mine()
    advance(w)
    row = w.db.conn.execute("SELECT * FROM m6_clusters").fetchone()
    assert row["liquidatable_since"] is None and row["last_clear_block"] == w.checkpoint()["block"]


def test_m6_reorg_forgets_a_clear_observation_on_deleted_blocks(tmp_path):
    """The clamp `after the last clear sweep` must not come from a deleted block: the new chain's cluster has
    been liquidatable all along, so the back-dated start is used."""
    lw = world(tmp_path)
    a = lw.legacy(owner(1))
    lw.views(a, balance=lw.deficit(W + 1), liquidatable=True)
    lw.chain.mine()
    w = lw.start()
    w.scan()  # the start block is recorded; no check run yet
    fork = lw.chain.number
    lw.views(a, balance=200 * SSV, liquidatable=False)  # deleted-chain view: clear
    lw.chain.mine()
    advance(w)
    assert outbox(w) == [] and w.db.conn.execute("SELECT last_clear_block FROM m6_clusters").fetchone()[0] >= fork
    lw.fork(fork)
    lw.views(a, balance=lw.deficit(W + 1), liquidatable=True)
    lw.chain.mine()
    advance(w)
    assert [r["severity"] for r in outbox(w, "severity!='health'")] == ["warn"]
    ev = json.loads(incidents(w)[0]["evidence"])
    assert ev["since_estimated"] is True and ev["estimated_start"] is True


def test_m6_reorg_run_reports_every_open_key_even_without_inventory_rows(tmp_path):
    """An open incident whose cluster left the API and the logs is still re-evaluated (from storage)."""
    lw, w, a, fork = _page_at_fork(tmp_path)
    lw.rows.clear()  # the API no longer lists it; storage still says legacy: UNKNOWN, kept open
    lw.fork(fork)
    lw.views(a, balance=0, liquidatable=True)
    lw.chain.mine()
    lw.clock.advance(601)  # past inventory.api_refresh_s: the API pages are fetched again
    advance(w)
    assert [i["key"] for i in incidents(w)] == [a.cid]
    assert any("not in the inventory" in r["text"] for r in outbox(w, "severity='health'"))


def test_m6_stub_params_dated_values(tmp_path):
    """The fixture carries the spec's dated SSV window; the check reads it from the chain."""
    lw = world(tmp_path)
    lw.legacy(owner(1))
    stub_params(lw.chain, liquidation_threshold_period_ssv=PERIOD_SSV, minimum_liquidation_collateral_ssv=1)
    lw.views(lw.refs[0], balance=SSV)
    lw.chain.mine()
    w = lw.start()
    advance(w)
    assert w.checks["m6"].period == PERIOD_SSV and w.checks["m6"].min_collateral == 1
