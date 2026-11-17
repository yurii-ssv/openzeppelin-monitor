"""M2 — operator capacity blocks legacy migration (#88326 / KI #6): hard / early / velocity / inbox revert trigger
lines with benign twins, limit from the chain, restart, reorg, errors, RPC budget.

Run: reconcile/.venv/bin/python -m pytest -q reconcile/tests/test_m2.py
"""

from __future__ import annotations

import json

import pytest
from fakechain import keys
from fixtures_cluster import (
    ClusterChain,
    exceed_limit,
    failed_tx,
    make_m2,
    set_operator,
)
from harness import VIEWS, advance, api_row, make_worker, outbox, stub_params

from checks.m1_cluster_abi import GET_OPERATOR_BY_ID, GET_OPERATOR_BY_ID_SSV
from eb_storage import u256
from rpc import RpcError

pytestmark = pytest.mark.m2

LEG = "0x" + "c3" * 20  # owner of the legacy cluster
LEG_OPS = (1, 2, 3, 4)
ETH_OWNER = "0x" + "d4" * 20
ETH_OPS = (1, 9, 10, 11)


def alerts(w):
    """(severity, kind, incident key) of M2's incident messages, in order."""
    return [(r["severity"], r["kind"], r["key"]) for r in w.db.conn.execute(
        "SELECT o.severity, o.kind, i.key FROM outbox o JOIN incidents i ON i.id = o.incident_id "
        "WHERE o.check_id = 'm2' ORDER BY o.created_at, o.rowid")]


def health(w):
    return [r["key"] for r in outbox(w, "kind='health'")]


def open_keys(w):
    return sorted(r[0] for r in w.db.conn.execute("SELECT key FROM incidents WHERE check_id='m2' AND state='open'"))


def legacy(ssv, rows, owner=LEG, ops=LEG_OPS, count=20, active=True):
    cid = ssv.legacy_ssv_cluster(owner, ops, count)
    st = ssv._get(owner, ops)[1]
    if not active:
        st.update(active=False, balance=0)
        ssv._persist(cid, st)
    rows.append(api_row(owner, ops, count, st["nfi"], st["index"], st["active"], st["balance"], migrated=False))
    return cid


def setup(tmp_path, eth=0, private=False, count=20, others_eth=0, m2=None, **over):
    """One active legacy cluster of `count` validators on operators 1-4; operator 1 has `eth` ETH validators."""
    chain = ClusterChain()
    rows: list = []
    from fakechain import MiniSSV

    ssv = MiniSSV(chain)
    legacy(ssv, rows, count=count)
    set_operator(chain, 1, eth=eth, ssv=count, private=private)
    for op in LEG_OPS[1:]:
        set_operator(chain, op, eth=others_eth, ssv=count)
    chain.mine()
    cfg = {"velocity_window_blocks": 20, **(m2 or {})}
    chain, w, _, rows = make_m2(tmp_path, chain=chain, api_rows=rows, m2=cfg, **over)
    chain.mine(25)  # the velocity window is covered by stored logs
    return chain, w, ssv, rows


def tick(w, chain, seconds=601, mine=True):
    w.clock.advance(seconds)
    if mine:
        chain.mine()
    advance(w)


# ---------------------------------------------------------------- hard


def test_m2_hard_blocked_active_legacy_cluster_warns(tmp_path):
    chain, w, ssv, rows = setup(tmp_path, eth=2_981, private=True)  # 2,981 + 20 > 3,000
    advance(w)
    assert ("warn", "open", "hard:1") in alerts(w)
    text = outbox(w, "kind='open' AND text LIKE '%Legacy migration blocked%'")[0]["text"]
    assert "cannot migrate now" in text and "needs 1 more ETH slots" in text and "go private" in text


def test_m2_hard_boundary_cluster_that_exactly_fits_is_silent(tmp_path):
    chain, w, ssv, rows = setup(tmp_path, eth=2_980, private=True)  # 2,980 + 20 == 3,000: migrate succeeds
    advance(w)
    assert [k for _, _, k in alerts(w) if k.startswith("hard:")] == []


def test_m2_hard_ignores_liquidated_legacy_clusters(tmp_path):
    chain = ClusterChain()
    rows: list = []
    from fakechain import MiniSSV

    ssv = MiniSSV(chain)
    legacy(ssv, rows, count=20, active=False)
    for op in LEG_OPS:  # a liquidated cluster's validators are not in the operators' legacy count
        set_operator(chain, op, eth=2_990, ssv=0, private=True)
    chain.mine()
    chain, w, _, rows = make_m2(tmp_path, chain=chain, api_rows=rows, m2={"velocity_window_blocks": 20,
                                                                          "early_headroom": 1})
    chain.mine(25)
    advance(w)
    assert alerts(w) == []


def test_m2_hard_resolves_when_the_cluster_migrates(tmp_path):
    chain, w, ssv, rows = setup(tmp_path, eth=2_981, private=True, m2={"early_headroom": 1})
    advance(w)
    assert open_keys(w) == ["early:1", "hard:1"]
    ssv.migrate(LEG, LEG_OPS)  # storage moves to the ETH slot: no longer legacy
    rows[0]["migrated"] = True
    set_operator(chain, 1, eth=2_981, ssv=0, private=True)
    tick(w, chain)
    assert open_keys(w) == [] and ("info", "resolved", "hard:1") in alerts(w)


# ---------------------------------------------------------------- early


@pytest.mark.parametrize("private,severity", [(True, "info"), (False, "warn")])
def test_m2_early_headroom_below_900_is_info_and_warn_if_public(tmp_path, private, severity):
    chain, w, ssv, rows = setup(tmp_path, eth=2_100, private=private)  # 3,000 - 2,100 - 20 = 880 < 900
    advance(w)
    assert alerts(w) == [(severity, "open", "early:1")]
    row = outbox(w, "kind='open'")[0]
    assert row["delivery"] == ("discord" if severity == "info" else "telegram")


def test_m2_early_headroom_exactly_900_is_silent(tmp_path):
    chain, w, ssv, rows = setup(tmp_path, eth=2_080)  # 3,000 - 2,080 - 20 = 900, not < 900
    advance(w)
    assert alerts(w) == []


def test_m2_early_escalates_when_the_operator_goes_public(tmp_path):
    chain, w, ssv, rows = setup(tmp_path, eth=2_100, private=True)
    advance(w)
    set_operator(chain, 1, eth=2_100, ssv=20, private=False)
    tick(w, chain)
    assert alerts(w) == [("info", "open", "early:1"), ("warn", "escalate", "early:1")]


# ---------------------------------------------------------------- velocity


def test_m2_velocity_registrations_above_headroom_is_info(tmp_path):
    chain, w, ssv, rows = setup(tmp_path, eth=2_800, private=True, m2={"early_headroom": 1})  # headroom 200
    advance(w)
    ssv.register(ETH_OWNER, ETH_OPS, keys(201, 0))
    tick(w, chain)
    assert alerts(w) == [("info", "open", "velocity:1")]
    assert outbox(w, "kind='open'")[0]["delivery"] == "discord"


def test_m2_velocity_registrations_equal_to_headroom_are_silent(tmp_path):
    chain, w, ssv, rows = setup(tmp_path, eth=2_800, private=True, m2={"early_headroom": 1})
    advance(w)
    ssv.register(ETH_OWNER, ETH_OPS, keys(200, 0))
    tick(w, chain)
    assert alerts(w) == []


def test_m2_velocity_counts_only_the_window(tmp_path):
    chain, w, ssv, rows = setup(tmp_path, eth=2_800, private=True, m2={"early_headroom": 1})
    ssv.register(ETH_OWNER, ETH_OPS, keys(150, 0))
    chain.mine(10)
    ssv.register(ETH_OWNER, ETH_OPS, keys(60, 500))
    advance(w)
    assert alerts(w) == [("info", "open", "velocity:1")]  # 210 > 200 inside the 20-block window
    chain.mine(12)  # the first burst leaves the window: 60 <= 200
    tick(w, chain)
    assert open_keys(w) == []


def test_m2_velocity_window_not_covered_by_stored_logs_is_health(tmp_path):
    chain, w, ssv, rows = setup(tmp_path, eth=0, m2={"velocity_window_blocks": 7_200})
    advance(w)
    assert any(k.startswith("health:m2-velocity-window") for k in health(w))


# ---------------------------------------------------------------- inbox revert


def inbox_setup(tmp_path, **kw):
    path = tmp_path / "failed-tx.jsonl"
    path.write_text("")
    chain, w, ssv, rows = setup(tmp_path, inbox={"path": str(path)}, **kw)
    return chain, w, ssv, rows, path


def test_m2_inbox_exceed_validator_limit_revert_warns_then_settles(tmp_path):
    chain, w, ssv, rows, path = inbox_setup(tmp_path, eth=2_981, private=True, m2={"early_headroom": 1})
    advance(w)
    cl = ssv._cluster(ssv._get(LEG, LEG_OPS)[1])
    tx = failed_tx(chain, path, "migrateClusterToETH", LEG, LEG_OPS, cl, LEG, exceed_limit(1))
    advance(w)
    advance(w)
    assert ("warn", "open", "revert:1") in alerts(w)
    tick(w, chain)
    assert "revert:1" in open_keys(w), "still over the limit: the revert stays open"
    set_operator(chain, 1, eth=2_900, ssv=20, private=True)
    tick(w, chain)
    assert open_keys(w) == [] and ("info", "resolved", "revert:1") in alerts(w)
    assert tx in outbox(w, "kind='open' AND text LIKE '%ExceedValidatorLimitWithData%'")[0]["text"]


@pytest.mark.parametrize("case", ["other_error", "other_function"])
def test_m2_inbox_revert_benign_twins_are_silent(tmp_path, case):
    chain, w, ssv, rows, path = inbox_setup(tmp_path, eth=0)
    advance(w)
    cl = ssv._cluster(ssv._get(LEG, LEG_OPS)[1])
    fn, revert = "reactivate", exceed_limit(1)
    if case == "other_error":
        revert = "0x95a0cf33"
    else:
        fn = "liquidate"
    failed_tx(chain, path, fn, LEG, LEG_OPS, cl, LEG, revert)
    advance(w)
    advance(w)
    assert alerts(w) == []


# ---------------------------------------------------------------- thresholds from the chain


def test_m2_thresholds_from_chain_limit_change_moves_the_hard_rule(tmp_path):
    chain, w, ssv, rows = setup(tmp_path, eth=2_990, private=True, m2={"early_headroom": 1})
    advance(w)
    assert open_keys(w) == ["early:1", "hard:1"]
    stub_params(chain, validators_per_operator_limit=3_100)
    chain.emit_event("ModuleUpgraded", ["0x" + u256(1).hex()], u256(0xBEEF))  # any M15 event reloads the parameters
    chain.mine()
    advance(w)
    assert open_keys(w) == []
    stub_params(chain, validators_per_operator_limit=3_000)
    chain.emit_event("ModuleUpgraded", ["0x" + u256(1).hex()], u256(0xBEEF))
    chain.mine()
    advance(w)
    assert open_keys(w) == ["early:1", "hard:1"]


# ---------------------------------------------------------------- restart


def test_m2_restart_does_not_duplicate_open_incidents(tmp_path):
    chain, w, ssv, rows = setup(tmp_path, eth=2_981, private=False)
    advance(w)
    before = alerts(w)
    assert {k for _, _, k in before} == {"hard:1", "early:1"}
    _, w2 = make_worker(tmp_path, only=["m2"], chain=chain, views=VIEWS, clock=w.clock,
                        checks={"m2": {"velocity_window_blocks": 20}},
                        inventory={"api_url": "https://api.invalid", "api_refresh_s": 0},
                        start_block=w.cfg.start_block, activation_block=w.cfg.activation_block)
    w2.inventory.http_get = w.inventory.http_get
    chain.mine()
    advance(w2)
    assert alerts(w2) == before


# ---------------------------------------------------------------- reorg


def test_m2_reorg_depth_1_removing_the_registration_burst_resolves_velocity(tmp_path):
    chain, w, ssv, rows = setup(tmp_path, eth=2_800, private=True, m2={"early_headroom": 1})
    advance(w)
    fork = chain.number
    ssv.register(ETH_OWNER, ETH_OPS, keys(201, 0))
    tick(w, chain)
    assert open_keys(w) == ["velocity:1"]
    chain.reorg(fork)
    chain.mine(2)
    advance(w)
    assert open_keys(w) == [] and w.db.meta("reorg:m2") is None
    state = w.db.conn.execute("SELECT state FROM incidents WHERE key='velocity:1'").fetchone()[0]
    assert state == "resolved", "an info incident resolves without a message (README §4)"


def test_m2_reorg_depth_n_beyond_confirmations_resolves_velocity(tmp_path):
    chain, w, ssv, rows = setup(tmp_path, eth=2_800, private=True, confirmations=3,
                                m2={"early_headroom": 1, "velocity_window_blocks": 30})
    advance(w)
    fork = chain.number
    ssv.register(ETH_OWNER, ETH_OPS, keys(201, 0))
    chain.mine(8)
    tick(w, chain)
    assert open_keys(w) == ["velocity:1"]
    assert w.checkpoint()["block"] - fork + 1 > 3
    chain.reorg(fork)
    chain.mine(10)
    advance(w)
    assert open_keys(w) == []


def test_m2_crash_between_the_rewind_and_the_reorg_run(tmp_path):
    chain, w, ssv, rows = setup(tmp_path, eth=2_800, private=True, m2={"early_headroom": 1})
    advance(w)
    fork = chain.number
    ssv.register(ETH_OWNER, ETH_OPS, keys(201, 0))
    tick(w, chain)
    chain.reorg(fork)
    chain.mine(2)
    w.check_reorg()
    w.scan()
    assert w.db.meta("reorg:m2") is not None
    _, w2 = make_worker(tmp_path, only=["m2"], chain=chain, views=VIEWS, clock=w.clock,
                        checks={"m2": {"velocity_window_blocks": 20, "early_headroom": 1}},
                        inventory={"api_url": "https://api.invalid", "api_refresh_s": 0},
                        start_block=w.cfg.start_block, activation_block=w.cfg.activation_block)
    w2.inventory.http_get = w.inventory.http_get
    advance(w2)
    assert open_keys(w2) == [] and w2.db.meta("reorg:m2") is None


def test_m2_inbox_revert_of_an_orphaned_block_is_dropped_and_resolved(tmp_path):
    chain, w, ssv, rows, path = inbox_setup(tmp_path, eth=2_981, private=True, m2={"early_headroom": 1})
    advance(w)
    cl = ssv._cluster(ssv._get(LEG, LEG_OPS)[1])
    fork = chain.number
    failed_tx(chain, path, "reactivate", LEG, LEG_OPS, cl, LEG, exceed_limit(1))
    advance(w)
    advance(w)
    assert "revert:1" in open_keys(w)
    chain.reorg(fork)
    chain.mine(2)
    advance(w)
    assert open_keys(w) == ["early:1", "hard:1"]
    assert w.db.conn.execute("SELECT COUNT(*) FROM m2_reverts").fetchone()[0] == 0


# ---------------------------------------------------------------- errors and unknowns


def test_m2_operator_read_failure_raises_and_keeps_incidents(tmp_path):
    chain, w, ssv, rows = setup(tmp_path, eth=2_981, private=True, m2={"early_headroom": 1})
    advance(w)
    chain.stub_call(VIEWS, GET_OPERATOR_BY_ID + u256(1), revert="0x961e3e8c")
    w.clock.advance(601)
    chain.mine()
    with pytest.raises(RpcError):
        advance(w)
    assert open_keys(w) == ["early:1", "hard:1"]


def test_m2_empty_operator_answer_is_an_error_not_zero(tmp_path):
    chain, w, ssv, rows = setup(tmp_path, eth=2_981, private=True, m2={"early_headroom": 1})
    advance(w)
    chain.stub_call(VIEWS, GET_OPERATOR_BY_ID_SSV + u256(1), b"")
    w.clock.advance(601)
    chain.mine()
    with pytest.raises(RpcError):
        advance(w)
    assert open_keys(w) == ["early:1", "hard:1"]


def test_m2_inventory_failure_is_a_check_error_and_keeps_incidents(tmp_path):
    chain, w, ssv, rows = setup(tmp_path, eth=2_981, private=True, m2={"early_headroom": 1})
    advance(w)

    def down(url):
        raise OSError("api down")

    w.inventory.http_get = down
    w.inventory._api_at = None
    tick(w, chain)
    assert open_keys(w) == ["early:1", "hard:1"]
    assert outbox(w, "kind='health' AND key LIKE 'health:check-error:%' AND check_id='m2'")


def test_m2_unvalidated_legacy_cluster_keeps_the_hard_incident_and_reports_health(tmp_path):
    chain, w, ssv, rows = setup(tmp_path, eth=2_981, private=True, m2={"early_headroom": 1})
    advance(w)
    rows[0] = {**rows[0], "balance": "1"}  # the API tuple no longer validates against storage
    tick(w, chain)
    assert open_keys(w) == ["early:1", "hard:1"]
    assert any(k.startswith("health:m2-unvalidated") for k in health(w))


def test_m2_inventory_count_mismatch_is_health(tmp_path):
    chain, w, ssv, rows = setup(tmp_path, eth=0)
    set_operator(chain, 2, eth=0, ssv=25)  # chain says 25 legacy validators, the inventory lists 20
    tick(w, chain)
    assert any(k.startswith("health:m2-inventory") for k in health(w))


def test_m2_rpc_budget_two_calls_per_operator_per_sweep(tmp_path):
    chain = ClusterChain()
    rows: list = []
    from fakechain import MiniSSV

    ssv = MiniSSV(chain)
    n_clusters = 350
    for i in range(n_clusters):
        ops = (4 * i + 1, 4 * i + 2, 4 * i + 3, 4 * i + 4)
        legacy(ssv, rows, owner="0x%040x" % (0xA000 + i), ops=ops, count=1)
        for op in ops:
            set_operator(chain, op, eth=0, ssv=1, private=True)
    chain.mine()
    chain, w, _, rows = make_m2(tmp_path, chain=chain, api_rows=rows, m2={"velocity_window_blocks": 20})
    chain.mine(25)
    advance(w)
    calls = w.db.meta("run:m2")["rpc_calls"]
    assert calls.get("eth_call", 0) <= 2 * 4 * n_clusters
    assert alerts(w) == []


def test_m2_missing_views_is_a_config_error(tmp_path):
    chain = ClusterChain()
    chain.mine()
    with pytest.raises(SystemExit, match="views"):
        make_worker(tmp_path, only=["m2"], chain=chain, checks={"m2": {}})


def test_m2_unresolved_inbox_revert_is_health_not_clean(tmp_path):
    chain, w, ssv, rows, path = inbox_setup(tmp_path, eth=0)
    advance(w)
    cl = ssv._cluster(ssv._get(LEG, LEG_OPS)[1])
    tx = failed_tx(chain, path, "migrateClusterToETH", LEG, LEG_OPS, cl, LEG, exceed_limit(1))
    chain.call_stubs.clear()  # no answer for the replay at block - 1, no Blockscout: unresolved
    stub_params(chain)
    for op in LEG_OPS:
        set_operator(chain, op, eth=0, ssv=20)
    for _ in range(6):
        chain.mine()
        advance(w)
    assert w.db.conn.execute("SELECT revert_source FROM inbox WHERE tx_hash=?", (tx,)).fetchone()[0] == "unresolved"
    assert outbox(w, "kind='health' AND check_id='m2' AND text LIKE '%could not be resolved%'")
    assert alerts(w) == []


# ---------------------------------------------------------------- QA attempt 1 regressions (repair 1)


def hard_keys(w):
    """Open M2 keys other than early:<op> (on-chain counts only, not what these scenarios vary)."""
    return [k for k in open_keys(w) if not k.startswith("early:")]


def test_m2_empty_api_is_not_recovery_hard_kept_from_the_last_validated_tuple(tmp_path):
    """QA F1 (M2): `clusters: []` cannot prove the legacy cluster migrated; its last validated tuple still matches
    storage, so the hard condition is found again."""
    chain, w, ssv, rows = setup(tmp_path, eth=2_981, private=True, m2={"early_headroom": 1})
    advance(w)
    assert hard_keys(w) == ["hard:1"]
    rows.clear()
    tick(w, chain)
    tick(w, chain)
    assert hard_keys(w) == ["hard:1"] and ("info", "resolved", "hard:1") not in alerts(w)
    assert any(k.startswith("health:m2-inventory-empty") for k in health(w))


def test_m2_empty_api_during_a_reorg_keeps_hard(tmp_path):
    chain, w, ssv, rows = setup(tmp_path, eth=2_981, private=True, m2={"early_headroom": 1})
    advance(w)
    fork = chain.number
    chain.mine(3)
    tick(w, chain)
    chain.reorg(fork)
    chain.mine(2)
    rows.clear()
    advance(w)
    assert hard_keys(w) == ["hard:1"] and w.db.meta("reorg:m2") is None


def test_m2_restart_with_an_empty_api_keeps_hard(tmp_path):
    chain, w, ssv, rows = setup(tmp_path, eth=2_981, private=True, m2={"early_headroom": 1})
    advance(w)
    _, w2 = make_worker(tmp_path, only=["m2"], chain=chain, views=VIEWS, clock=w.clock,
                        checks={"m2": {"velocity_window_blocks": 20, "early_headroom": 1}},
                        inventory={"api_url": "https://api.invalid", "api_refresh_s": 0},
                        start_block=w.cfg.start_block, activation_block=w.cfg.activation_block)
    w2.inventory.http_get = lambda url: {"clusters": [], "pagination": {"pages": 1}}
    tick(w2, chain)
    assert hard_keys(w2) == ["hard:1"] and ("info", "resolved", "hard:1") not in alerts(w2)


def test_m2_empty_api_after_a_silent_migration_resolves_on_storage_proof(tmp_path):
    """Twin: storage, not the API, proves the cluster is no longer legacy."""
    chain, w, ssv, rows = setup(tmp_path, eth=2_981, private=True, m2={"early_headroom": 1})
    advance(w)
    cid, st = ssv._get(LEG, LEG_OPS)
    st["version"] = "ETH"
    ssv._persist(cid, st)  # the ETH slot is written, the legacy slot cleared (no log)
    set_operator(chain, 1, eth=2_981, ssv=0, private=True)
    rows.clear()
    tick(w, chain)
    assert hard_keys(w) == [] and ("info", "resolved", "hard:1") in alerts(w)


@pytest.mark.parametrize("reorg", [False, True])
def test_m2_unknown_tuple_with_an_empty_api_keeps_hard_and_a_reorg_run_raises(tmp_path, reorg):
    """The legacy cluster changed in storage while the API is empty: its tuple is unknown and its validators could be
    blocked (2,981 + 20 > 3,000): hard is kept (health); in the reorg run it raises and keeps the marker."""
    chain, w, ssv, rows = setup(tmp_path, eth=2_981, private=True, m2={"early_headroom": 1})
    advance(w)
    fork = chain.number
    cid, st = ssv._get(LEG, LEG_OPS)
    saved = list(rows)
    rows.clear()
    st["balance"] += 1
    ssv._persist(cid, st)
    if reorg:
        chain.mine(3)
        tick(w, chain)
        chain.reorg(fork)
        st["balance"] += 1
        ssv._persist(cid, st)
        chain.mine(2)
    tick(w, chain)
    assert hard_keys(w) == ["hard:1"]
    if reorg:
        assert w.db.meta("reorg:m2") is not None
        assert outbox(w, "kind='health' AND check_id='m2' AND text LIKE '%cannot re-evaluate%'")
    else:
        assert any(k.startswith("health:m2-hard-unknown") for k in health(w))
    rows.extend(api_row(LEG, LEG_OPS, 20, st["nfi"], st["index"], True, st["balance"], migrated=False)
                for _ in saved[:1])
    tick(w, chain)
    assert hard_keys(w) == ["hard:1"] and w.db.meta("reorg:m2") is None


@pytest.mark.parametrize("eth,kept", [(2_985, True), (2_975, False)])
def test_m2_partial_api_hard_resolves_only_when_the_on_chain_count_proves_no_block(tmp_path, eth, kept):
    """A second legacy cluster (20 validators) the API never listed: after the listed one (30, blocked) migrates,
    the on-chain legacy count still shows 20 validators in no known cluster; 2,985 + 20 > 3,000 keeps hard,
    2,975 + 20 <= 3,000 resolves it."""
    chain, w, ssv, rows = setup(tmp_path, eth=eth, private=True, count=30, m2={"early_headroom": 1})
    ssv.legacy_ssv_cluster("0x" + "e5" * 20, LEG_OPS, 20)  # in storage, never listed, never logged
    set_operator(chain, 1, eth=eth, ssv=50, private=True)
    tick(w, chain)
    assert hard_keys(w) == ["hard:1"]
    ssv.migrate(LEG, LEG_OPS)
    rows[0]["migrated"] = True
    set_operator(chain, 1, eth=eth, ssv=20, private=True)
    tick(w, chain)
    assert hard_keys(w) == (["hard:1"] if kept else [])


@pytest.mark.parametrize("confirmations", [0, 3])
def test_m2_reorg_orphaning_the_settlement_reopens_the_surviving_revert(tmp_path, confirmations):
    """QA F3: README §6.2 for every persisted key: a revert settled on a deleted block is evaluated again."""
    chain, w, ssv, rows, path = inbox_setup(tmp_path, eth=2_981, private=True, m2={"early_headroom": 1},
                                            confirmations=confirmations)
    advance(w)
    cl = ssv._cluster(ssv._get(LEG, LEG_OPS)[1])
    failed_tx(chain, path, "reactivate", LEG, LEG_OPS, cl, LEG, exceed_limit(1))
    chain.mine(confirmations)
    advance(w)
    advance(w)
    assert "revert:1" in hard_keys(w)
    fork = chain.number
    set_operator(chain, 1, eth=2_900, ssv=20, private=True)
    chain.mine(confirmations)
    tick(w, chain)
    assert hard_keys(w) == [] and w.db.conn.execute("SELECT cleared_block FROM m2_reverts").fetchone()[0]
    chain.reorg(fork)
    chain.call_stubs = {k: [e for e in v if e[0] < fork] for k, v in chain.call_stubs.items()}  # stubs follow too
    chain.mine(2 + confirmations)
    advance(w)
    assert "revert:1" in hard_keys(w) and "hard:1" in hard_keys(w)
    assert w.db.conn.execute("SELECT cleared_block FROM m2_reverts").fetchone()[0] is None


def test_m2_revert_does_not_settle_while_the_api_is_empty(tmp_path):
    chain, w, ssv, rows, path = inbox_setup(tmp_path, eth=2_981, private=True, m2={"early_headroom": 1})
    advance(w)
    cl = ssv._cluster(ssv._get(LEG, LEG_OPS)[1])
    failed_tx(chain, path, "migrateClusterToETH", LEG, LEG_OPS, cl, LEG, exceed_limit(1))
    advance(w)
    advance(w)
    assert "revert:1" in hard_keys(w)
    saved = list(rows)
    rows.clear()
    set_operator(chain, 1, eth=2_900, ssv=20, private=True)
    tick(w, chain)
    assert hard_keys(w) == ["revert:1"], "hard resolves on proof; the revert cannot settle without API rows"
    rows.extend(saved)
    tick(w, chain)
    assert hard_keys(w) == []


def test_m2_revert_of_a_cluster_the_api_omits_is_settled_on_its_own_calldata_tuple(tmp_path):
    """The reactivate revert of a liquidated legacy cluster the API does not list: its calldata tuple (validated
    against storage) shows it is over the limit, so the revert stays until the operator has room."""
    chain = ClusterChain()
    rows: list = []
    from fakechain import MiniSSV

    ssv = MiniSSV(chain)
    legacy(ssv, rows, count=20, active=False)
    rows.clear()
    legacy(ssv, rows, owner=ETH_OWNER, ops=(5, 6, 7, 8), count=1)  # the API answers, without the reverted cluster
    for op in LEG_OPS:
        set_operator(chain, op, eth=2_990, ssv=0, private=True)
    for op in (5, 6, 7, 8):
        set_operator(chain, op, eth=0, ssv=1, private=True)
    chain.mine()
    path = tmp_path / "failed-tx.jsonl"
    path.write_text("")
    chain, w, _, rows = make_m2(tmp_path, chain=chain, api_rows=rows, inbox={"path": str(path)},
                                m2={"velocity_window_blocks": 20, "early_headroom": 1})
    chain.mine(25)
    advance(w)
    cl = ssv._cluster(ssv._get(LEG, LEG_OPS)[1])
    failed_tx(chain, path, "reactivate", LEG, LEG_OPS, cl, LEG, exceed_limit(1))
    advance(w)
    advance(w)
    tick(w, chain)
    assert hard_keys(w) == ["revert:1"], "2,990 + 20 > 3,000: not settled"
    for op in LEG_OPS:
        set_operator(chain, op, eth=2_950, ssv=0, private=True)
    tick(w, chain)
    assert hard_keys(w) == []


def test_m2_inbox_transaction_lookup_returning_nothing_raises(tmp_path):
    chain, w, ssv, rows, path = inbox_setup(tmp_path, eth=0)
    advance(w)
    cl = ssv._cluster(ssv._get(LEG, LEG_OPS)[1])
    tx = failed_tx(chain, path, "migrateClusterToETH", LEG, LEG_OPS, cl, LEG, exceed_limit(1))
    w.check_reorg()
    w.scan()
    w.poll_inbox()
    assert w.db.conn.execute("SELECT revert_selector FROM inbox WHERE tx_hash=?", (tx,)).fetchone()[0]
    del chain.txs[tx]
    del chain.senders[tx]
    with pytest.raises(RpcError, match="returned nothing"):
        w.run_checks()
    assert w.db.conn.execute("SELECT COUNT(*) FROM m2_reverts").fetchone()[0] == 0
    assert w.db.meta("cursor:inbox:m2", 0) == 0


# ---------------------------------------------------------------- QA attempt 2 regressions (repair 2)


def api_row_now(ssv, owner, ops, delta=0):
    st = ssv._get(owner, ops)[1]
    return api_row(owner, ops, st["count"], st["nfi"], st["index"], st["active"], st["balance"] + delta,
                   migrated=False)


def restart_m2(tmp_path, chain, w, rows, **m2):
    _, w2 = make_worker(tmp_path, only=["m2"], chain=chain, views=VIEWS, clock=w.clock,
                        checks={"m2": {"velocity_window_blocks": 20, "early_headroom": 1, **m2}},
                        inventory={"api_url": "https://api.invalid", "api_refresh_s": 0},
                        start_block=w.cfg.start_block, activation_block=w.cfg.activation_block)
    w2.inventory.http_get = lambda url: {"clusters": list(rows), "pagination": {"pages": 1}}
    return w2


@pytest.mark.parametrize("restart_now", [False, True])
def test_m2_reorg_orphaning_the_resolution_restores_the_canonical_tuple_and_reopens_hard(tmp_path, restart_now):
    """QA attempt 2 F1 (M2): hard resolved on a tuple and an ETH count of an orphaned block; the API then omits the
    cluster (another one still listed: a partial answer). The tuple validated before the fork is kept (history), so
    the reorg run finds the cluster blocked again on canonical storage (also after a restart between rewind and
    run)."""
    chain, w, ssv, rows = setup(tmp_path, eth=2_981, private=True, m2={"early_headroom": 1})
    advance(w)
    assert "hard:1" in open_keys(w)
    legacy(ssv, rows, owner="0x" + "b2" * 20, ops=(5, 6, 7, 8), count=1)
    for op in (5, 6, 7, 8):
        set_operator(chain, op, eth=0, ssv=1, private=True)
    chain.mine()
    tick(w, chain)
    fork = chain.number
    cid, st = ssv._get(LEG, LEG_OPS)
    st["balance"] += 7
    ssv._persist(cid, st)
    rows[0] = api_row_now(ssv, LEG, LEG_OPS)
    set_operator(chain, 1, eth=2_900, ssv=20, private=True)
    tick(w, chain)
    assert "hard:1" not in open_keys(w)
    chain.reorg(fork)
    st["balance"] -= 7
    set_operator(chain, 1, eth=2_981, ssv=20, private=True)
    chain.mine(2)
    rows[:] = [r for r in rows if r["ownerAddress"] != LEG]
    if restart_now:
        w.check_reorg()
        w.scan()
        w = restart_m2(tmp_path, chain, w, rows)
    advance(w)
    assert "hard:1" in open_keys(w) and w.db.meta("reorg:m2") is None
    assert [a for a in alerts(w) if a[2] == "hard:1"][-1] == ("warn", "open", "hard:1")


def test_m2_reorg_of_a_resolved_hard_without_a_canonical_tuple_raises_and_keeps_the_marker(tmp_path):
    """The only validated tuple came from the orphaned blocks: the resolved hard key cannot be re-evaluated on the
    new chain (blocked count, unknown tuple), so the reorg run raises; a valid API tuple then reopens it."""
    chain, w, ssv, rows = setup(tmp_path, eth=2_981, private=True, m2={"early_headroom": 1})
    rows[0] = api_row_now(ssv, LEG, LEG_OPS, delta=1)  # wrong hash before the fork: never validated
    advance(w)
    assert "hard:1" not in open_keys(w)
    fork = chain.number
    cid, st = ssv._get(LEG, LEG_OPS)
    st["balance"] += 7
    ssv._persist(cid, st)
    rows[0] = api_row_now(ssv, LEG, LEG_OPS)
    tick(w, chain)
    assert "hard:1" in open_keys(w)
    set_operator(chain, 1, eth=2_900, ssv=20, private=True)
    tick(w, chain)
    assert "hard:1" not in open_keys(w)
    chain.reorg(fork)
    st["balance"] -= 7
    set_operator(chain, 1, eth=2_981, ssv=20, private=True)
    chain.mine(2)
    rows[0] = api_row_now(ssv, LEG, LEG_OPS, delta=1)
    advance(w)
    assert w.db.meta("reorg:m2") is not None and "hard:1" not in open_keys(w)
    assert outbox(w, "kind='health' AND check_id='m2' AND text LIKE '%cannot re-evaluate%hard:1%'")
    rows[0] = api_row_now(ssv, LEG, LEG_OPS)
    chain.mine()
    advance(w)
    assert w.db.meta("reorg:m2") is None and "hard:1" in open_keys(w)


def test_m2_tuple_history_is_bounded_and_keeps_the_newest_tuple_below_the_reorg_depth(tmp_path):
    chain, w, ssv, rows = setup(tmp_path, eth=2_000, private=True, m2={"early_headroom": 1}, reorg_search_depth=6)
    advance(w)
    cid, st = ssv._get(LEG, LEG_OPS)
    for _ in range(12):
        st["balance"] += 1
        ssv._persist(cid, st)
        rows[0] = api_row_now(ssv, LEG, LEG_OPS)
        tick(w, chain)
    hist = [(r["block"], json.loads(r["tuple"])[4]) for r in w.db.conn.execute(
        "SELECT * FROM m2_clusters WHERE cluster_id = ? ORDER BY block", ("0x" + cid.hex(),))]
    cp = w.checkpoint()["block"]
    assert len(hist) <= 8, hist
    assert hist[0][0] <= cp - 6 and all(b > cp - 6 for b, _ in hist[1:]), (cp, hist)
    assert hist[-1][1] == st["balance"]


def test_m2_unknown_tuple_on_a_blocked_operator_without_an_open_incident_is_health(tmp_path):
    """Class 1: the hard rule of an operator whose legacy cluster has no validating tuple is UNKNOWN; with no incident
    open it is still reported (health), never silent; it is not opened without a validated blocked tuple."""
    chain, w, ssv, rows = setup(tmp_path, eth=2_900, private=True, m2={"early_headroom": 1})
    legacy(ssv, rows, owner="0x" + "b2" * 20, ops=(5, 6, 7, 8), count=1)
    for op in (5, 6, 7, 8):
        set_operator(chain, op, eth=0, ssv=1, private=True)
    chain.mine()
    advance(w)
    assert hard_keys(w) == []
    rows[:] = [r for r in rows if r["ownerAddress"] != LEG]  # partial: the other cluster is still listed
    cid, st = ssv._get(LEG, LEG_OPS)
    st["balance"] += 1
    ssv._persist(cid, st)
    set_operator(chain, 1, eth=2_981, ssv=20, private=True)
    tick(w, chain)
    assert hard_keys(w) == []
    assert outbox(w, "kind='health' AND check_id='m2' AND key LIKE 'health:m2-hard-unknown%' AND text LIKE '%hard:1%'")
    rows.insert(0, api_row_now(ssv, LEG, LEG_OPS))
    tick(w, chain)
    assert hard_keys(w) == ["hard:1"]
