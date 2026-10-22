"""M6 — legacy SSV clusters: liquidation coverage (spec M6). Scenario names are listed in
plans/monitoring-service/workers/cp2e/attempts.md (V2x.1–V2x.5)."""

from __future__ import annotations

import itertools
import json
import random

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
    """No baseline (worker start): the start is back-dated from balance and burn. W + 2 blocks of burn below the
    threshold: one block back adds one block of burn, so the balance was below it (strict `<`,
    ClusterLib.isLiquidatable) at this block and the W + 1 before it: the first liquidatable block is W + 1 ago."""
    lw = world(tmp_path)
    a = lw.legacy(owner(1))
    lw.views(a, balance=lw.deficit(W + 2), liquidatable=True)
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
    """QA cp2e-1 F2 (PR1c): first liquidatable block exactly W blocks ago (balance W + 1 blocks of burn below the
    threshold, so W + 1 blocks ago it was at the threshold, not liquidatable): the rule is `>`, silent."""
    lw = world(tmp_path)
    a = lw.legacy(owner(1))
    lw.views(a, balance=lw.deficit(W + 1), liquidatable=True)
    lw.chain.mine()
    w = lw.start()
    advance(w)
    assert outbox(w) == [] and incidents(w) == []
    row = w.db.conn.execute("SELECT * FROM m6_clusters").fetchone()
    assert row["liquidatable_since"] == w.checkpoint()["block"] - W


def test_m6_backdated_start_of_a_cluster_that_just_crossed_is_now(tmp_path):
    """QA cp2e-1 F2 (PR1a): one block of burn below the threshold: the previous block held exactly the threshold,
    which the strict rule does not liquidate, so the first liquidatable block is this one (not estimated) — the
    same start as the chain observed block by block."""
    lw = world(tmp_path)
    a = lw.legacy(owner(1))
    lw.views(a, balance=lw.deficit(1), liquidatable=True)
    lw.chain.mine()
    w = lw.start()
    advance(w)
    row = w.db.conn.execute("SELECT * FROM m6_clusters").fetchone()
    assert row["liquidatable_since"] == w.checkpoint()["block"] and row["since_estimated"] == 0


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
    lw.views(a, balance=lw.deficit(W + 2), liquidatable=True)
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
    lw = world(tmp_path, params={"minimum_liquidation_collateral_ssv": PERIOD_SSV * BURN + (W + 2) * BURN})
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
    lw.views(a, balance=lw.deficit(W + 2), liquidatable=True)
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
    lw.views(a, balance=lw.deficit(W + 2), liquidatable=True)
    lw.chain.mine()
    w = lw.start()
    w.scan()  # the start block is recorded; no check run yet
    fork = lw.chain.number
    lw.views(a, balance=200 * SSV, liquidatable=False)  # deleted-chain view: clear
    lw.chain.mine()
    advance(w)
    assert outbox(w) == [] and w.db.conn.execute("SELECT last_clear_block FROM m6_clusters").fetchone()[0] >= fork
    lw.fork(fork)
    lw.views(a, balance=lw.deficit(W + 2), liquidatable=True)
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


# ---------------------------------------------------------------- QA cp2e-1 F1: an incomplete inventory is UNKNOWN


def _health(w):
    return [r["text"] for r in outbox(w, "severity='health'")]


def test_m6_first_sweep_with_an_empty_api_listing_is_unknown_not_clean(tmp_path):
    """QA cp2e-1 F1 (PR2): nothing persisted yet and the API answers 200 with an empty page: never read as 'no
    legacy clusters'. The chain's daoValidatorCount and the API's own pagination.total both say it is short."""
    lw = world(tmp_path)
    a = lw.legacy(owner(1))
    lw.views(a, balance=0, liquidatable=True)
    lw.chain.mine()
    w = lw.start()
    w.inventory.http_get = lambda url: {"clusters": [], "pagination": {"pages": 1, "total": 1}}
    advance(w)
    health = _health(w)
    assert len(health) == 1 and "UNKNOWN" in health[0] and "inventory incomplete" in health[0]
    assert "daoValidatorCount is 1" in health[0] and "pagination.total 1" in health[0]
    assert w.db.meta("check:m6:summary")["inventory_gap"]
    w.inventory.http_get = lw.http_get  # the API recovers (next refresh): the page goes out
    lw.sweep()
    assert [r["severity"] for r in pages(w)] == ["page"]


@pytest.mark.parametrize("fault", ["short", "no_total"])
def test_m6_listing_short_of_its_total_or_without_one_is_unknown(tmp_path, fault):
    lw = world(tmp_path)
    a, b = lw.legacy(owner(1)), lw.legacy(owner(2))
    lw.views(a, balance=0, liquidatable=True)  # the row the short page drops
    lw.views(b, balance=500 * SSV)
    lw.chain.mine()
    w = lw.start()
    lw.api_fault = fault
    advance(w)
    # the clusters listed are still evaluated: without a total the listing is whole but unconfirmed
    assert len(pages(w)) == (0 if fault == "short" else 1)
    health = _health(w)
    assert len(health) == 1 and "inventory incomplete" in health[0]
    assert ("pagination.total 2" if fault == "short" else "no pagination.total") in health[0]


def test_m6_unreadable_api_total_is_unknown_not_clean(tmp_path):
    """The listing is cached (api_refresh_s) and the total cannot be read: completeness is unknown."""
    lw = world(tmp_path)
    a = lw.legacy(owner(1))
    lw.views(a, balance=500 * SSV)
    lw.chain.mine()
    w = lw.start(inventory={"api_url": "https://api.invalid/api/v4/mainnet", "api_refresh_s": 10**9})
    advance(w)
    assert _health(w) == []
    lw.api_fault = "error"
    lw.sweep()
    health = _health(w)
    assert len(health) == 1 and "pagination.total unavailable" in health[0] and "SSV API unreachable" in health[0]


def test_m6_cluster_missing_from_the_api_and_its_total_is_found_by_the_dao_validator_count(tmp_path):
    """The API agrees with itself (one row, total 1) but its index lost a burning legacy cluster: the chain's
    SSV-side daoValidatorCount (ProtocolLib.updateDAOSSV) does not add up."""
    lw = world(tmp_path)
    a = lw.legacy(owner(1), count=2, api=False)
    b = lw.legacy(owner(2))
    lw.views(a, balance=0, liquidatable=True)
    lw.views(b, balance=500 * SSV)
    lw.chain.mine()
    w = lw.start()
    advance(w)
    health = _health(w)
    assert len(health) == 1 and "hold 1 validators, daoValidatorCount is 3" in health[0]


def test_m6_restart_on_a_lost_database_with_an_empty_api_is_unknown(tmp_path):
    """The persisted set is gone (a new data dir) and the API is empty at the first sweep of the new process."""
    lw = world(tmp_path)
    a = lw.legacy(owner(1))
    lw.views(a, balance=0, liquidatable=True)
    lw.chain.mine()
    w = lw.start()
    advance(w)
    assert len(pages(w)) == 1
    lw.tmp_path = tmp_path / "lost"
    lw.tmp_path.mkdir()
    lw.api_fault = "empty"
    w = lw.start()
    advance(w)
    assert pages(w) == [] and "inventory incomplete" in _health(w)[0]


def test_m6_digest_of_an_incomplete_inventory_says_unknown_not_zero(tmp_path):
    lw = world(tmp_path, alerts={"mode": "record", "label": "test", "digest_interval_s": 86_400})
    a = lw.legacy(owner(1), count=2)
    lw.views(a, balance=100 * SSV)
    lw.chain.mine()
    w = lw.start()
    lw.api_fault = "empty"
    advance(w)
    line = outbox(w, "kind='digest'")[0]["text"]
    assert "0 active" in line and "UNKNOWN: the legacy inventory is incomplete" in line


# ---------------------------------------------------------------- QA cp2e-1 F3: reorg enumeration, resolved keys


def test_m6_reorg_run_reports_an_open_key_without_a_persisted_row(tmp_path):
    """README §6.2: a reorg run enumerates the check's open incidents, not only its own rows (QA cp2e-1 QF6d). The
    row is lost and the API no longer lists the cluster; storage still says legacy: UNKNOWN, kept open."""
    lw, w, a, fork = _page_at_fork(tmp_path)
    inc = incidents(w)[0]["id"]
    w.db.conn.execute("DELETE FROM m6_clusters")
    lw.rows.clear()
    lw.fork(fork)
    lw.views(a, balance=0, liquidatable=True)
    lw.chain.mine()
    lw.clock.advance(601)  # past inventory.api_refresh_s
    advance(w)
    assert [i["id"] for i in incidents(w)] == [inc] and w.db.meta("reorg:m6") is None
    assert any("not in the inventory" in t for t in _health(w))


def test_m6_reorg_orphaning_the_liquidation_that_resolved_the_page_reopens_it(tmp_path):
    """QA cp2e-1 PR10: the resolution of an ALREADY RESOLVED key came from a deleted block (its liquidation): the
    reorg run re-evaluates the cluster on the new chain, where it is still active at zero."""
    lw = world(tmp_path)
    a = lw.legacy(owner(1))
    lw.views(a, balance=0, liquidatable=True)
    lw.chain.mine()
    w = lw.start()
    advance(w)
    st = lw.ssv.clusters[bytes.fromhex(a.cid[2:])]
    saved = dict(st)
    fork = lw.chain.number
    lw.ssv.liquidate(a.owner, a.ops)
    advance(w)
    assert incidents(w) == [] and outbox(w)[-1]["kind"] == "resolved"
    lw.fork(fork)
    st.update(saved)  # the new chain never liquidated it
    lw.chain.mine()
    advance(w)
    assert [i["key"] for i in incidents(w)] == [a.cid] and len(pages(w)) == 2


def test_m6_events_of_one_scan_step_are_evaluated_at_the_run_block(tmp_path):
    """Two cluster events between runs (a validator removal, then the liquidation): one event run at the
    checkpoint, one resolution, no page from the intermediate state."""
    lw = world(tmp_path)
    a = lw.legacy(owner(1), count=2)
    lw.views(a, balance=0, liquidatable=True)
    lw.chain.mine()
    w = lw.start()
    advance(w)
    lw.ssv.remove(a.owner, a.ops, [b"\x02" * 48])
    lw.ssv.liquidate(a.owner, a.ops)
    advance(w)
    assert [r["kind"] for r in outbox(w, "severity!='health'")] == ["open", "resolved"]
    assert "was liquidated" in outbox(w)[-1]["text"] and set(w.db.meta("run:m6")["reasons"]) == {"events"}


def _observed_start(tmp_path):
    """Clear at one sweep, liquidatable (deep below the threshold at once: a fee jump) from the next: the start is
    observed, clear + 1, and the warn is W blocks away."""
    lw = world(tmp_path)
    a = lw.legacy(owner(1))
    lw.views(a, balance=200 * SSV, liquidatable=False)
    lw.chain.mine()
    w = lw.start()
    advance(w)
    clear = w.checkpoint()["block"]
    lw.views(a, balance=lw.deficit(W + 50_000), liquidatable=True)
    lw.sweep()
    assert w.db.conn.execute("SELECT liquidatable_since FROM m6_clusters").fetchone()[0] == clear + 1
    return lw, w, a, clear


def _after_restore(w, clear):
    row = w.db.conn.execute("SELECT * FROM m6_clusters").fetchone()
    assert row["liquidatable_since"] == clear + 1 and row["since_estimated"] == 0
    assert outbox(w, "severity!='health'") == []  # lag < W: no warn from a re-estimated start


def test_m6_reorg_restores_the_canonical_start_a_deleted_clear_observation_overwrote(tmp_path):
    """A deleted block saw the cluster clear (overwriting its row); the new chain keeps it liquidatable. The rewind
    restores the canonical state at the ancestor (start clear + 1, observed), never a balance estimate that dates
    it before the canonical clear sweep (here: more than W blocks back, a false warn)."""
    lw, w, a, clear = _observed_start(tmp_path)
    lw.clock.advance(3600)
    fork = lw.chain.number
    lw.views(a, balance=lw.deficit(W + 50_000), liquidatable=False)  # deleted chain: clear (a validator removal)
    lw.chain.mine()
    advance(w)
    assert w.db.conn.execute("SELECT liquidatable_since FROM m6_clusters").fetchone()[0] is None
    lw.fork(fork)
    lw.chain.mine()
    advance(w)
    _after_restore(w, clear)


def test_m6_reorg_orphaning_a_liquidation_restores_the_observed_start(tmp_path):
    """The liquidation that deleted the cluster's row lived on a deleted block: the row comes back as it was."""
    lw, w, a, clear = _observed_start(tmp_path)
    st = lw.ssv.clusters[bytes.fromhex(a.cid[2:])]
    saved = dict(st)
    fork = lw.chain.number
    lw.ssv.liquidate(a.owner, a.ops)
    advance(w)
    assert w.db.conn.execute("SELECT COUNT(*) FROM m6_clusters").fetchone()[0] == 0
    lw.fork(fork)
    st.update(saved)
    lw.chain.mine()
    advance(w)
    _after_restore(w, clear)


# ---------------------------------------------------------------- seeded adversarial sweep (qa/DEFECT-CLASSES.md)

SWEEP_STORIES = ("page_orphaned", "page_survives", "resolution_orphaned", "clear_orphaned")
SWEEP_FAULTS = (None, "empty", "short", "error", "no_total", "view_revert")
BLINDING = {"empty", "short", "error", "view_revert"}  # faults that hide the cluster under attack from the run


def _sweep_cases(seed=20261022):
    """Every story × fault, twice, with confirmations, reorg depth and restart point drawn from a fixed seed."""
    rng = random.Random(seed)
    for story, fault in itertools.product(SWEEP_STORIES, SWEEP_FAULTS):
        for _ in range(2):
            yield story, fault, rng.choice((0, 3)), rng.choice(("1", "N")), rng.choice((None, "detected", "before"))


class _M6Sweep:
    """One cluster `a` (1 validator) under attack and a clear bystander `b`; `truth` is a's state on the canonical
    chain (zero: active at balance 0), `obs` the canonical blocks at which the check evaluated a."""

    def __init__(self, tmp_path, conf):
        self.lw = lw = world(tmp_path, confirmations=conf)
        self.conf = conf
        self.a, self.b = lw.legacy(owner(1)), lw.legacy(owner(2), count=2)
        lw.views(self.b, balance=500 * SSV)
        self.set_truth(False)
        lw.chain.mine()
        lw.start()
        self.truth_zero, self.obs = False, []

    @property
    def w(self):
        return self.lw.w

    def set_truth(self, zero, revert=False):
        self.lw.views(self.a, balance=0 if zero else 30 * SSV, liquidatable=zero, revert=revert)

    def last_run(self):  # a failed run only counts an error in run:m6
        r = self.w.db.meta("run:m6", {})
        return r.get("at"), r.get("block"), r.get("reasons")

    def snapshot(self):
        return {r["id"]: (r["key"], r["state"]) for r in self.w.db.conn.execute(
            "SELECT id, key, state FROM incidents WHERE check_id='m6'")}

    def step(self, seconds=3600, blocks=1, fault=None, run=True):
        lw, before, n_out = self.lw, self.snapshot(), len(outbox(self.w))
        prev_run = self.last_run()
        lw.clock.advance(seconds)
        lw.chain.mine(blocks + self.conf)
        if run:
            advance(self.w)
        new_rows, after = outbox(self.w)[n_out:], self.snapshot()
        ran = self.last_run() != prev_run
        if ran and fault not in BLINDING:
            self.obs.append((self.w.db.meta("run:m6")["block"], self.truth_zero))
        self.check(before, after, new_rows, fault, ran)
        return new_rows

    def check(self, before, after, new_rows, fault, ran):
        a = self.a.cid
        resolved = [i for i, (k, st) in after.items() if st != "open" and before.get(i, (k, "open"))[1] == "open"
                    and i in before]
        opened = {k for k, st in after.values() if st == "open"}
        # no resolve without positive proof
        for i in resolved:
            assert after[i][0] == a and not self.truth_zero and fault not in BLINDING, (i, fault)
        # no open incident without supporting evidence (b never; a only while it is at zero)
        assert opened <= ({a} if self.truth_zero else set()) | {k for k, st in before.values() if st == "open"}
        if fault is None:
            assert opened == ({a} if self.truth_zero else set()), (opened, self.truth_zero)
        else:  # an input the run cannot trust is reported, never silent
            assert any(r["severity"] == "health" and ("M6" in r["text"] or "m6" in r["text"]) for r in new_rows)
        # every incident id pages once (no duplicate, no re-page of a surviving incident)
        opens = [r["incident_id"] for r in outbox(self.w, "kind='open'")]
        assert len(opens) == len(set(opens))
        # no window anchored on stale data: the start lies after the last canonical clear observation and no
        # later than the first canonical observation at zero after it (a failed run decides nothing: its rewind
        # is rolled back with it and the reorg marker kept)
        if not ran:
            assert self.w.db.meta("reorg:m6") is not None or not resolved
            return
        row = self.w.db.conn.execute("SELECT * FROM m6_clusters WHERE cluster_id=?", (a,)).fetchone()
        clears = [b for b, z in self.obs if not z]
        if row is not None and row["last_clear_block"] is not None:
            assert row["last_clear_block"] in clears
        if row is not None and row["liquidatable_since"] is not None:
            last_clear = max(clears, default=None)
            zeros = [b for b, z in self.obs if z and (last_clear is None or b > last_clear)]
            assert last_clear is None or row["liquidatable_since"] > last_clear
            assert not zeros or row["liquidatable_since"] <= min(zeros)

    def reorg(self, fork):
        self.lw.fork(fork)
        self.obs = [(b, z) for b, z in self.obs if b < fork]


@pytest.mark.parametrize("story,fault,conf,depth,restart", list(_sweep_cases()))
def test_m6_adversarial_sweep(tmp_path, story, fault, conf, depth, restart):
    sw = _M6Sweep(tmp_path, conf)
    lw, a = sw.lw, sw.a
    raw = bytes.fromhex(a.cid[2:])
    sw.step(seconds=0, blocks=0)  # the first sweep: a and b clear
    if story in ("resolution_orphaned", "clear_orphaned"):  # canonical: a reaches zero and pages
        sw.set_truth(True)
        sw.truth_zero = True
        sw.step()
    fork = lw.chain.number
    saved = dict(lw.ssv.clusters[raw])
    if story in ("page_orphaned", "page_survives"):  # the zero balance appears on the chain about to be replaced
        sw.set_truth(True)
        sw.truth_zero = True
        sw.step()
    elif story == "resolution_orphaned":  # the liquidation that resolves it: an event run, no interval needed
        lw.ssv.liquidate(a.owner, a.ops)
        sw.truth_zero = False
        new = sw.step(seconds=0, blocks=0)
        assert [r["kind"] for r in new if r["severity"] != "health"] == ["resolved"]  # no event lost between sweeps
    else:  # clear_orphaned: a clear observation on the deleted chain
        sw.set_truth(False)
        sw.truth_zero = False
        sw.step()
    if depth == "N":  # the reorg reaches below the checkpoint: deeper than the confirmation window
        sw.step(blocks=6)
    sw.reorg(fork)
    lw.ssv.clusters[raw].update(saved)
    sw.truth_zero = story != "page_orphaned"
    sw.set_truth(sw.truth_zero, revert=fault == "view_revert")
    lw.api_fault = fault if fault != "view_revert" else None
    if restart == "before":
        lw.restart()
    elif restart == "detected":
        lw.chain.mine(1 + conf)
        sw.w.check_reorg()  # rewound and committed; the process dies before M6 runs
        lw.restart()
    sw.step(seconds=601, fault=fault)  # the reorg run, past api_refresh_s: the fault reaches the inventory
    lw.api_fault = None
    sw.set_truth(sw.truth_zero)
    sw.step()  # healed
    sw.step()  # and stable
    assert sw.w.db.meta("reorg:m6") is None
    keys = [k for k, _ in sw.snapshot().values() if k == a.cid]
    assert len(keys) == {"page_orphaned": 1, "page_survives": 1}.get(story, 2)
