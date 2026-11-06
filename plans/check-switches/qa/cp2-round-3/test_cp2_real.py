"""D11 / D9 composition using the real M1 and M2 persisted-state readers."""
from fixtures_cluster import make_m1, deposit, poison, OWNER, OPS
from harness import make_worker, advance, VIEWS, outbox
from test_m1 import restart, incidents
from test_m2 import setup


def test_real_m1_orphaned_repair_is_silent_then_live_once(tmp_path):
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18)
    advance(w)
    poison(ssv)
    after_poison = dict(ssv._get(OWNER, OPS)[1])
    deposit(ssv)
    repair_block = chain.sealed
    advance(w)
    _, disabled = make_worker(tmp_path, only=[], chain=chain, clock=w.clock, views=VIEWS,
        start_block=w.cfg.start_block, activation_block=w.cfg.activation_block, checks={"m1": {"enabled": False}})
    frozen = incidents(disabled)[0]
    assert frozen["state"] == "disabled"
    chain.reorg(repair_block)
    ssv._get(OWNER, OPS)[1].update(after_poison)
    chain.mine(2)
    advance(disabled)
    live = restart(tmp_path, chain, w, backfill={"enabled": True})
    prior = len(outbox(live, "check_id='m1'"))
    advance(live)
    assert live.db.meta("catchup:m1")["state"] == "first_live"
    assert live.db.meta("reorg:m1") is None
    assert incidents(live)[0] == frozen
    assert len(outbox(live, "check_id='m1'")) == prior
    chain.mine()
    advance(live)
    assert live.db.meta("catchup:m1") is None
    assert len(outbox(live, "check_id='m1'")) == prior + 1
    assert outbox(live, "check_id='m1'")[-1]["kind"] == "open"
    assert "Stale cluster tuple" in outbox(live, "check_id='m1'")[-1]["text"]
    assert incidents(live)[0] == frozen
    chain.mine()
    advance(live)
    assert len(outbox(live, "check_id='m1'")) == prior + 1


def test_real_m2_empty_api_persisted_hard_condition_is_silent_then_live_once(tmp_path):
    chain, w, ssv, rows = setup(tmp_path, eth=2981, private=True, m2={"early_headroom": 1})
    advance(w)
    fork = chain.number
    chain.mine(3)
    advance(w)
    _, disabled = make_worker(tmp_path, only=[], chain=chain, clock=w.clock, views=VIEWS,
        start_block=w.cfg.start_block, activation_block=w.cfg.activation_block, checks={"m2": {"enabled": False}})
    frozen = [dict(r) for r in w.db.conn.execute("SELECT * FROM incidents WHERE check_id='m2'")]
    assert frozen and all(r["state"] == "disabled" for r in frozen)
    rows.clear()
    chain.reorg(fork)
    chain.mine(2)
    advance(disabled)
    _, live = make_worker(tmp_path, only=["m2"], chain=chain, clock=w.clock, views=VIEWS,
        start_block=w.cfg.start_block, activation_block=w.cfg.activation_block,
        checks={"m2": {"velocity_window_blocks": 20, "early_headroom": 1}},
        inventory={"api_url": "https://api.invalid", "api_refresh_s": 0}, backfill={"enabled": True})
    live.inventory.http_get = lambda url: {"clusters": [], "pagination": {"pages": 1}}
    prior = len(outbox(live, "check_id='m2' AND kind != 'health'"))
    advance(live)
    assert live.db.meta("catchup:m2")["state"] == "first_live" and live.db.meta("reorg:m2") is None
    assert len(outbox(live, "check_id='m2' AND kind != 'health'")) == prior
    chain.mine()
    advance(live)
    assert live.db.meta("catchup:m2") is None
    sent = outbox(live, "check_id='m2' AND kind != 'health'")[prior:]
    # M2 reports two surviving conditions: hard:1 (warn) and early:1 (info).
    assert len(sent) == 2 and all(row["kind"] == "open" for row in sent)
    assert sorted(live.db.conn.execute("SELECT key FROM incidents WHERE id=?", (row["incident_id"],)).fetchone()[0]
                  for row in sent) == ["early:1", "hard:1"]
    for row in frozen:
        assert dict(live.db.conn.execute("SELECT * FROM incidents WHERE id=?", (row["id"],)).fetchone()) == row
    chain.mine()
    advance(live)
    assert len(outbox(live, "check_id='m2' AND kind != 'health'")) == prior + 2
