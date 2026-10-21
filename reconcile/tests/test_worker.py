"""Validation gates 1-3 of plans/94775-eb-collapse-monitoring.md against an in-memory chain.

Gate 1 reproduces the fork PoC trace (63 dummy keys, 2,048 ETH root, 32 ETH billed) and checks the
worker's numbers at each step; the same trace runs against a real mainnet fork in scripts/fork_e2e.py.
"""

from __future__ import annotations

import json

import pytest
from fakechain import PROXY, FakeChain, FakeRpc, MiniSSV, keys

import eb_model as M
from eb_reconcile import DB, Config, Worker, load_config  # noqa: F401
from eb_storage import cluster_id

# The ported #94775 scenarios (V1B.6): `pytest -k m5` selects every test in this module.
pytestmark = pytest.mark.m5

OWNER = "0x" + "aa" * 20
OTHER = "0x" + "bb" * 20
OPS = (11, 12, 13, 14)
OPS2 = (11, 12, 13, 15)


class Capture:
    def __init__(self, fail: int = 0):
        self.sent: list[tuple[str, str, str]] = []
        self.fail = fail

    def send(self, severity, text, key, route=None):
        if self.fail:
            self.fail -= 1
            raise RuntimeError("telegram HTTP 502")
        self.sent.append((severity, key, text))
        return "captured"

    def keys(self, prefix=""):
        return [k for _, k, _ in self.sent if k.startswith(prefix)]

    def severities(self):
        return [s for s, _, _ in self.sent]


def make(tmp_path, chain=None, notifier=None, **over):
    chain = chain or FakeChain()
    raw = {
        "chain_id": 1, "proxy": PROXY, "activation_block": chain.number, "start_block": chain.number,
        "rpc_url_env": "UNUSED", "confirmations": 0, "sweep_interval_s": 0, "full_sweep_interval_s": 0,
        "max_log_range": 10_000, "db_path": str(tmp_path / "eb.sqlite"), "layout": {},
        "alerts": {"mode": "record", "label": "test", "digest_interval_s": 0},
    }
    from checks import registry as _registry

    raw.setdefault("checks", {cid: {"enabled": False} for cid in _registry.REGISTRY if cid != "m5"})
    raw.update(over)
    from eb_reconcile import DEFAULTS

    cfg = Config({**DEFAULTS, **raw})
    w = Worker(cfg, FakeRpc(chain), DB(cfg.db_path), notifier or Capture())
    w.bootstrap()
    return chain, w


def tick(w: Worker):
    w.check_reorg()
    w.scan()
    w.run_checks(force=True)
    w.deliver()
    return {e.cluster_id: e for e in w.checks["m5"].last_evals}


def ev_for(evals, owner=OWNER, ops=OPS) -> M.Evaluation:
    return evals[cluster_id(owner, ops)]


# ---------------------------------------------------------------- gate 1: the PoC trace


def poc_setup(tmp_path):
    chain, w = make(tmp_path)
    ssv = MiniSSV(chain)
    real = keys(1, 94_775_000)
    ssv.register(OWNER, OPS, real)
    s0 = chain.number
    chain.mine()
    ssv.commit(s0)
    ssv.update(OWNER, OPS, 2048)
    return chain, w, ssv


def test_poc_trace_numbers_and_alerts(tmp_path):
    chain, w, ssv = poc_setup(tmp_path)
    e = ev_for(tick(w))
    assert (e.status, e.observed_vunits, e.reference_vunits) == (M.CLEAN, 640_000, 640_000)

    s1 = chain.number  # root reference block precedes the dummy registrations
    chain.mine()
    dummies = keys(63, 94_775_001)
    ssv.register(OWNER, OPS, dummies, shares=b"")
    e = ev_for(tick(w))
    assert (e.status, e.observed_vunits, e.reference_vunits) == (M.CLEAN, 1_270_000, 1_270_000)

    ssv.commit(s1)
    ssv.update(OWNER, OPS, 2048)  # unchanged EB, consumed by the owner
    e = ev_for(tick(w))
    assert e.status == M.LATENT
    assert (e.reference_vunits, e.observed_vunits, e.discrepancy_vunits) == (1_270_000, 640_000, 630_000)
    assert not [s for s in w.notifier.severities() if s in ("page", "warn")]

    ssv.remove(OWNER, OPS, dummies)
    e = ev_for(tick(w))
    assert e.status == M.CONFIRMED
    assert (e.reference_vunits, e.observed_vunits, e.discrepancy_vunits) == (640_000, 10_000, 630_000)
    assert len(e.matched_keys) == 63
    crit = [t for s, k, t in w.notifier.sent if s == "page"]
    assert len(crit) == 1
    assert "Shortfall: 630,000 vUnits (2016 ETH of fee weight)" in crit[0]
    assert "with empty shares" in crit[0] and "(63 with empty shares)" in crit[0]

    # later sweeps at new blocks do not re-page
    chain.mine(5)
    tick(w)
    assert len([s for s in w.notifier.severities() if s == "page"]) == 1

    # a clean next-round root restores 2,048 ETH and resolves the incident
    s2 = chain.number
    chain.mine()
    ssv.commit(s2)
    ssv.update(OWNER, OPS, 2048)
    e = ev_for(tick(w))
    assert (e.status, e.observed_vunits) == (M.CLEAN, 640_000)
    assert any(":resolved:" in k for k in w.notifier.keys())
    inc = w.db.conn.execute("SELECT * FROM m5_incidents").fetchall()
    assert len(inc) == 1 and inc[0]["state"] == "resolved" and inc[0]["max_shortfall_vunits"] == 630_000
    assert [r["state"] for r in w.db.conn.execute("SELECT state FROM incidents")] == ["resolved"]


def test_poc_repeated_each_round_updates_same_incident(tmp_path):
    chain, w, ssv = poc_setup(tmp_path)
    for rnd in range(2):
        s = chain.number
        chain.mine()
        d = keys(63, 1_000_000 * (rnd + 1))
        ssv.register(OWNER, OPS, d)
        ssv.commit(s)
        ssv.update(OWNER, OPS, 2048)  # the beacon EB of the real validator is unchanged each round
        ssv.remove(OWNER, OPS, d)
        tick(w)
    incidents = w.db.conn.execute("SELECT * FROM incidents").fetchall()
    assert len(incidents) == 1 and incidents[0]["state"] == "open"
    crit = w.notifier.keys("EB94775")
    assert any(":confirmed:" in k for k in crit) and any(":update:" in k for k in crit)


def test_partial_removal_still_confirms_with_exact_gap(tmp_path):
    chain, w, ssv = poc_setup(tmp_path)
    s = chain.number
    chain.mine()
    d = keys(10, 5_000)
    ssv.register(OWNER, OPS, d)
    ssv.commit(s)
    ssv.update(OWNER, OPS, 2048)
    ssv.remove(OWNER, OPS, d[:4])
    e = ev_for(tick(w))
    # reference: 640,000 + 6*10,000 ; stored: 640,000 - 4*10,000
    assert (e.status, e.reference_vunits, e.observed_vunits, e.discrepancy_vunits) == (
        M.CONFIRMED, 700_000, 600_000, 100_000)


# ---------------------------------------------------------------- gate 2: benign sequences


def _no_pages(w):
    return not [s for s in w.notifier.severities() if s in ("page", "warn")]


def test_benign_post_s_removal_after_update(tmp_path):
    chain, w = make(tmp_path)
    ssv = MiniSSV(chain)
    k = keys(2, 1)
    ssv.register(OWNER, OPS, k)
    s = chain.number
    chain.mine()
    ssv.commit(s)
    ssv.update(OWNER, OPS, 64)
    ssv.remove(OWNER, OPS, k[:1])
    e = ev_for(tick(w))
    assert (e.status, e.observed_vunits) == (M.CLEAN, 10_000) and _no_pages(w)


def test_benign_removal_between_s_and_update_is_overbilling(tmp_path):
    chain, w = make(tmp_path)
    ssv = MiniSSV(chain)
    k = keys(2, 1)
    ssv.register(OWNER, OPS, k)
    s = chain.number
    chain.mine()
    ssv.remove(OWNER, OPS, k[:1])
    ssv.commit(s)
    ssv.update(OWNER, OPS, 64)  # root at S still counts the removed validator
    e = ev_for(tick(w))
    assert e.status == M.OVERBILLED and e.discrepancy_vunits == -10_000 and _no_pages(w)


def test_benign_add_after_consumed_root(tmp_path):
    chain, w = make(tmp_path)
    ssv = MiniSSV(chain)
    ssv.register(OWNER, OPS, keys(1, 1))
    s = chain.number
    chain.mine()
    ssv.commit(s)
    ssv.update(OWNER, OPS, 100)
    ssv.register(OWNER, OPS, keys(3, 10))
    ssv.remove(OWNER, OPS, keys(3, 10))
    e = ev_for(tick(w))
    assert e.status == M.CLEAN and _no_pages(w)


def test_benign_add_remove_without_root_update(tmp_path):
    chain, w = make(tmp_path)
    ssv = MiniSSV(chain)
    ssv.register(OWNER, OPS, keys(1, 1))
    s = chain.number
    chain.mine()
    ssv.commit(s)
    ssv.update(OWNER, OPS, 40)
    s2 = chain.number
    ssv.register(OWNER, OPS, keys(5, 10))
    ssv.commit(s2)  # roots committed but never consumed by this cluster
    ssv.commit(chain.number - 1)
    ssv.remove(OWNER, OPS, keys(5, 10))
    e = ev_for(tick(w))
    assert e.status == M.CLEAN and _no_pages(w)


def test_update_by_non_owner_is_classified_the_same(tmp_path):
    chain, w = make(tmp_path)
    ssv = MiniSSV(chain)
    ssv.register(OWNER, OPS, keys(1, 1))
    s = chain.number
    chain.mine()
    ssv.commit(s)
    ssv.update(OWNER, OPS, 2048, sender="0x" + "0c" * 20)
    s1 = chain.number
    chain.mine()
    ssv.register(OWNER, OPS, keys(63, 100))
    ssv.commit(s1)
    ssv.update(OWNER, OPS, 2048, sender="0x" + "dd" * 20)  # a third party consumes the root
    ssv.remove(OWNER, OPS, keys(63, 100))
    e = ev_for(tick(w))
    assert e.status == M.CONFIRMED
    crit = [t for s, k, t in w.notifier.sent if s == "page"][0]
    assert "0x" + "dd" * 20 in crit


def test_two_clusters_same_owner_are_independent(tmp_path):
    chain, w = make(tmp_path)
    ssv = MiniSSV(chain)
    for ops in (OPS, OPS2):
        ssv.register(OWNER, ops, keys(1, ops[-1] * 1000))
    s = chain.number
    chain.mine()
    ssv.commit(s)
    for ops in (OPS, OPS2):
        ssv.update(OWNER, ops, 2048)
    s1 = chain.number
    chain.mine()
    ssv.register(OWNER, OPS, keys(63, 500))
    ssv.register(OWNER, OPS2, keys(2, 900))  # honest additions in the second cluster
    ssv.commit(s1)
    ssv.update(OWNER, OPS, 2048)
    ssv.remove(OWNER, OPS, keys(63, 500))
    evals = tick(w)
    assert ev_for(evals, OWNER, OPS).status == M.CONFIRMED
    assert ev_for(evals, OWNER, OPS2).status == M.CLEAN


def test_zero_snapshot_after_emptying_is_implicit_reset(tmp_path):
    chain, w = make(tmp_path)
    ssv = MiniSSV(chain)
    k = keys(1, 1)
    ssv.register(OWNER, OPS, k)
    s = chain.number
    chain.mine()
    ssv.commit(s)
    ssv.update(OWNER, OPS, 2048)
    ssv.remove(OWNER, OPS, k)  # count 0 -> vUnits 0
    ssv.register(OWNER, OPS, keys(1, 2))  # re-registered at the implicit 32 ETH baseline (SSV-4)
    e = ev_for(tick(w))
    assert (e.status, e.observed_vunits, e.billed_vunits) == (M.IMPLICIT_RESET, 0, 10_000) and _no_pages(w)


def test_honest_add_before_update_still_registered_is_latent_only(tmp_path):
    chain, w = make(tmp_path)
    ssv = MiniSSV(chain)
    ssv.register(OWNER, OPS, keys(1, 1))
    s = chain.number
    chain.mine()
    ssv.commit(s)
    ssv.update(OWNER, OPS, 100)
    s1 = chain.number
    chain.mine()
    ssv.register(OWNER, OPS, keys(1, 2))
    ssv.commit(s1)
    ssv.update(OWNER, OPS, 100)  # 100 ETH at S passes the 2-validator floor of 64 ETH
    e = ev_for(tick(w))
    assert (e.status, e.discrepancy_vunits) == (M.LATENT, 10_000) and _no_pages(w)


def test_implicit_cluster_without_updates(tmp_path):
    chain, w = make(tmp_path)
    ssv = MiniSSV(chain)
    ssv.register(OWNER, OPS, keys(3, 1))
    ssv.remove(OWNER, OPS, keys(1, 1))
    e = ev_for(tick(w))
    assert (e.status, e.billed_vunits) == (M.IMPLICIT, 20_000)


def test_liquidated_cluster_confirmed_is_a_warning(tmp_path):
    chain, w, ssv = poc_setup(tmp_path)
    s = chain.number
    chain.mine()
    d = keys(63, 7_000)
    ssv.register(OWNER, OPS, d)
    ssv.commit(s)
    ssv.update(OWNER, OPS, 2048)
    ssv.liquidate(OWNER, OPS)
    ssv.remove(OWNER, OPS, d)
    e = ev_for(tick(w))
    assert e.status == M.CONFIRMED and e.active is False
    assert "warn" in w.notifier.severities() and "page" not in w.notifier.severities()
    ssv.reactivate(OWNER, OPS)
    e = ev_for(tick(w))
    assert e.status == M.CONFIRMED and e.active is True and "page" in w.notifier.severities()


def test_legacy_ssv_cluster_then_migration(tmp_path):
    chain, w = make(tmp_path)
    ssv = MiniSSV(chain)
    ssv.legacy_ssv_cluster(OTHER, OPS, 3)  # pre-activation SSV cluster: no logs yet
    s = chain.number
    chain.mine()
    ssv.commit(s)
    ssv.update(OTHER, OPS, 96)
    ssv.remove(OTHER, OPS, keys(1, 1))  # SSV removal does not touch vUnits
    e = ev_for(tick(w), OTHER, OPS)
    assert (e.status, e.observed_vunits) == (M.NOT_ETH, 30_000)
    ssv.migrate(OTHER, OPS)
    e = ev_for(tick(w), OTHER, OPS)
    assert e.status in (M.OVERBILLED, M.CLEAN) and e.version == M.ETH


# ---------------------------------------------------------------- gate 3: restart, ranges, gaps, reorgs


def test_restart_resumes_and_keeps_incident_ids(tmp_path):
    chain, w, ssv = poc_setup(tmp_path)
    s = chain.number
    chain.mine()
    d = keys(63, 3_000)
    ssv.register(OWNER, OPS, d)
    ssv.commit(s)
    ssv.update(OWNER, OPS, 2048)
    tick(w)
    ssv.remove(OWNER, OPS, d)
    # crash before the worker sees the removal; a new process resumes from the durable checkpoint
    cap = Capture()
    _, w2 = make(tmp_path, chain=chain, notifier=cap,
                 activation_block=w.cfg.activation_block, start_block=w.cfg.start_block)
    tick(w2)
    tick(w2)
    ids = {r["id"] for r in w2.db.conn.execute("SELECT id FROM incidents")}
    assert len(ids) == 1
    assert len([k for k in cap.keys() if ":confirmed:" in k]) == 1
    assert w2.db.conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0] == len(chain.logs)


def test_range_limits_are_split(tmp_path):
    chain = FakeChain()
    chain.max_range = 3
    chain, w = make(tmp_path, chain=chain, max_log_range=50)
    ssv = MiniSSV(chain)
    ssv.register(OWNER, OPS, keys(1, 1))
    chain.mine(20)
    s = chain.number
    chain.mine()
    ssv.commit(s)
    ssv.update(OWNER, OPS, 64)
    tick(w)
    assert w.db.conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0] == len(chain.logs)


def test_missing_logs_are_unknown_not_clean(tmp_path):
    chain, w, ssv = poc_setup(tmp_path)
    s = chain.number
    chain.mine()
    d = keys(63, 4_000)
    ssv.register(OWNER, OPS, d)
    ssv.commit(s)
    ssv.update(OWNER, OPS, 2048)
    rm_block = chain.number
    ssv.remove(OWNER, OPS, d)
    chain.drop_logs_in = (rm_block, rm_block)  # provider silently truncates the removal
    e = ev_for(tick(w))
    assert e.status == M.UNKNOWN
    assert any("hash" in r or "stored" in r for r in e.reasons)
    assert any(k.startswith("health:unknown") for k in w.notifier.keys())


def test_storage_layout_mismatch_suspends(tmp_path):
    chain, w = make(tmp_path, layout={"implementation": "0x" + "12" * 20})
    MiniSSV(chain).register(OWNER, OPS, keys(1, 1))
    assert tick(w) == {}
    assert any(k.startswith("health:layout") for k in w.notifier.keys())


def test_reorg_rewinds_and_replays(tmp_path):
    chain, w, ssv = poc_setup(tmp_path)
    s = chain.number
    chain.mine()
    d = keys(63, 6_000)
    ssv.register(OWNER, OPS, d)
    ssv.commit(s)
    ssv.update(OWNER, OPS, 2048)
    fork_point = chain.number
    ssv.remove(OWNER, OPS, d)
    assert ev_for(tick(w)).status == M.CONFIRMED
    # the removal is reorged out: the cluster is back to the latent state
    chain.reorg(fork_point)
    chain.mine()
    e = ev_for(tick(w))
    assert e.status == M.LATENT
    assert any(k.startswith("health:reorg") for k in w.notifier.keys())


def test_rpc_outage_raises_health_alert(tmp_path):
    chain, w, ssv = poc_setup(tmp_path)
    tick(w)
    chain.down = True
    for _ in range(3):
        w.step()
    chain.down = False
    w.step()
    assert any(k.startswith("health:rpc") for k in w.notifier.keys())
    status = json.loads((tmp_path / "eb.status.json").read_text())
    assert status["healthy"] is True


def test_delivery_retries_then_succeeds(tmp_path):
    cap = Capture(fail=2)
    chain, w = make(tmp_path, notifier=cap)
    ssv = MiniSSV(chain)
    ssv.register(OWNER, OPS, keys(1, 1))
    s = chain.number
    chain.mine()
    ssv.commit(s)
    ssv.update(OWNER, OPS, 2048)
    s1 = chain.number
    chain.mine()
    ssv.register(OWNER, OPS, keys(63, 50))
    ssv.commit(s1)
    ssv.update(OWNER, OPS, 2048)
    ssv.remove(OWNER, OPS, keys(63, 50))
    tick(w)
    row = w.db.conn.execute("SELECT * FROM outbox WHERE severity='page'").fetchone()
    assert row["sent_at"] is None and row["attempts"] == 1
    w.db.conn.execute("UPDATE outbox SET next_attempt_at=0")
    w.deliver()
    w.db.conn.execute("UPDATE outbox SET next_attempt_at=0")
    w.deliver()
    row = w.db.conn.execute("SELECT * FROM outbox WHERE severity='page'").fetchone()
    assert row["sent_at"] is not None and row["attempts"] == 2


def test_audit_reports_matched_window(tmp_path):
    from eb_reconcile import audit

    chain, w, ssv = poc_setup(tmp_path)
    s = chain.number
    chain.mine()
    d = keys(63, 8_000)
    ssv.register(OWNER, OPS, d)
    ssv.commit(s)
    ssv.update(OWNER, OPS, 2048)
    ssv.remove(OWNER, OPS, d)
    tick(w)
    rep = audit(w.db, w.cfg.start_block)
    assert rep["windows_with_matched_removal"] == 1
    win = rep["windows"][0]
    assert win["matched_removals_before_next_update"] == 63 and win["overwritten_baseline_vunits"] == 630_000


@pytest.mark.parametrize("eb,v", [(32, 10_000), (33, 10_313), (2048, 640_000), (0, 0), (1, 313)])
def test_eb_to_vunits_matches_ceiling(eb, v):
    from eb_storage import eb_to_vunits

    assert eb_to_vunits(eb) == v


def test_vunits_to_eb_floors():
    from eb_storage import vunits_to_eb, vunits_to_eth_str

    assert (vunits_to_eb(10_000), vunits_to_eb(10_313), vunits_to_eb(640_000)) == (32, 33, 2048)
    assert (vunits_to_eth_str(630_000), vunits_to_eth_str(10_313)) == ("2016", "33.0016")


def test_telegram_errors_never_contain_the_token(monkeypatch):
    import http.client
    import urllib.request

    from eb_reconcile import Telegram

    def boom(*a, **k):
        raise http.client.InvalidURL("URL can't contain control characters: /bot123:SECRET /sendMessage")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    t = Telegram(" 123:SECRET' ", "42")
    assert t.token == "123:SECRET"
    with pytest.raises(RuntimeError) as exc:
        t.send("info", "x", "k")
    assert "SECRET" not in str(exc.value)


def test_pre_hotfix_ssv_removal_keeps_snapshot(tmp_path):
    """Mainnet before PR #614 (block 25,037,749): emptying a legacy SSV cluster left vUnits set."""
    chain = FakeChain()
    chain, w = make(tmp_path, chain=chain, pr614_guards_from=[10**9, 0])
    ssv = MiniSSV(chain)
    ssv.guards = False
    ssv.legacy_ssv_cluster(OTHER, OPS, 1)
    s = chain.number
    chain.mine()
    ssv.commit(s)
    ssv.update(OTHER, OPS, 34)
    ssv.remove(OTHER, OPS, keys(1, 1))
    e = ev_for(tick(w), OTHER, OPS)
    assert (e.status, e.observed_vunits, e.replay_vunits) == (M.NOT_ETH, 10_625, 10_625)
