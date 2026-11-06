"""Independent CP1 probes; executed only after copying into a scratch archive."""
import json

import pytest

from test_switches import Env, IDS, PLAN, _register, open_four
import checks as registry
from eb_reconcile import Config, DB, DEFAULTS, END_OF_BLOCK, Worker
from fakechain import FakeRpc
from harness import outbox
from test_framework import finding


@pytest.fixture(autouse=True)
def registered():
    PLAN.clear()
    for cid in IDS:
        _register(cid)
    yield
    for cid in IDS:
        registry.REGISTRY.pop(cid, None)
    PLAN.clear()


@pytest.mark.parametrize('value', [0, None, '', False, True])
def test_d6_only_literal_false_may_close(tmp_path, value):
    env = Env(tmp_path)
    open_four(env)
    raw = env.raw()
    raw['checks'] = {'sw_a': {'enabled': value}}
    cfg = Config({**DEFAULTS, **raw})
    built = registry.build(cfg.raw, only=['sw_a'])
    w = Worker(cfg, FakeRpc(env.chain), DB(cfg.db_path), env.notifier, clock=env.clock, checks=built)
    w.bootstrap()
    rows = list(w.db.conn.execute("SELECT state FROM incidents WHERE check_id='sw_a'"))
    expected = 'disabled' if value is False else 'open'
    assert [r[0] for r in rows] == [expected] * 4, f'D6: enabled={value!r} must yield {expected}, actual={[r[0] for r in rows]}'
    assert w.disabled == (['sw_a'] if value is False else [])


def test_partial_closure_bad_evidence_rolls_back_all_checks(tmp_path):
    env = Env(tmp_path)
    PLAN['sw_a'] = [finding('sw_a', 'ka', 'page')]
    PLAN['sw_b'] = [finding('sw_b', 'kb', 'page')]
    env.start_worker(only=('sw_a', 'sw_b'))
    env.step()
    env.w.db.conn.execute("UPDATE incidents SET evidence='invalid-json' WHERE check_id='sw_b'")
    with pytest.raises(json.JSONDecodeError):
        env.start_worker(only=(), disabled=('sw_a', 'sw_b'))
    assert [r['state'] for r in env.rows()] == ['open', 'open']
    assert env.w.db.meta('disabled:sw_a') is None
    assert env.w.db.meta('disabled:sw_b') is None


def test_resolved_rows_and_times_are_preserved(tmp_path):
    env = Env(tmp_path)
    PLAN['sw_a'] = [finding('sw_a', 'resolved', 'page')]
    env.start_worker()
    env.step()
    PLAN['sw_a'] = [finding('sw_a', 'resolved', 'page', resolve=True), finding('sw_a', 'open', 'page')]
    env.step()
    old = env.rows()[0]
    env.clock.advance(37)
    cp, now = env.w.checkpoint()['block'], env.clock()
    env.start_worker(only=(), disabled=('sw_a',))
    rows = env.rows()
    assert rows[0] == old and old['state'] == 'resolved'
    assert rows[1]['updated_at'] == now and rows[1]['resolved_block'] == cp
    assert json.loads(rows[1]['evidence'])['disabled'] == {'block': cp, 'at': now}
    assert env.w.db.meta('disabled:sw_a') == {'block': cp, 'at': now, 'closed': 1}


def test_marker_only_unregistered_id_is_rewound_and_persists_restart(tmp_path):
    env = Env(tmp_path)
    env.start_worker()
    env.step()
    fork = env.chain.number
    env.step()
    env.step()
    env.w.db.set_meta('reorg:retired', env.w.checkpoint()['block'])
    env.start_worker(only=())
    env.chain.reorg(fork)
    env.step()
    marker = env.w.db.meta('reorg:retired')
    assert marker < fork
    env.start_worker(only=())
    assert env.w.db.meta('reorg:retired') == marker
    assert env.w.db.meta('cursor:events:retired') is None


def test_identity_failure_closes_nothing(tmp_path):
    env = Env(tmp_path)
    open_four(env)
    env.start_worker(only=(), disabled=('sw_a',), bootstrap=False)
    env.w.cfg.chain_id = 999
    with pytest.raises(SystemExit):
        env.w.bootstrap()
    assert [r['state'] for r in env.rows()] == ['open'] * 4
    assert env.w.db.meta('disabled:sw_a') is None


@pytest.mark.parametrize('command', ['once', 'verify-layout'])
def test_cli_bootstrap_callers_close_silently(tmp_path, monkeypatch, command):
    import eb_reconcile
    env = Env(tmp_path)
    open_four(env)
    cfg = Config({**DEFAULTS, **env.raw(disabled=('sw_a',))})
    real_build = registry.build
    monkeypatch.setattr(eb_reconcile, 'load_config', lambda _: cfg)
    monkeypatch.setattr(eb_reconcile, 'make_rpc', lambda _: FakeRpc(env.chain))
    monkeypatch.setattr(eb_reconcile, 'make_notifier', lambda _: env.notifier)
    monkeypatch.setattr(registry, 'build', lambda settings: real_build(settings, only=['sw_a', 'm5']))
    monkeypatch.setattr(registry.REGISTRY['m5'], 'verify_layout', lambda *a, **k: [])
    before = env.n_outbox()
    assert eb_reconcile.main([command, '--config', 'scratch-unused']) == 0
    assert [r['state'] for r in env.rows()] == ['disabled'] * 4
    assert env.w.db.meta('disabled:sw_a')['closed'] == 4
    assert env.n_outbox() == before


def test_m1_disabled_transient_repair_orphaned_rebuilds_from_own_state(tmp_path):
    from fixtures_cluster import make_m1, deposit, poison, OWNER, OPS
    from test_m1 import restart, incidents
    from harness import make_worker, advance, VIEWS
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18)
    advance(w)
    poison(ssv)
    after_poison = dict(ssv._get(OWNER, OPS)[1])
    deposit(ssv)
    repair_block = chain.sealed
    advance(w)
    _, disabled = make_worker(tmp_path, only=[], chain=chain, clock=w.clock, views=VIEWS,
        start_block=w.cfg.start_block, activation_block=w.cfg.activation_block, checks={'m1': {'enabled': False}})
    old = incidents(disabled)[0]
    assert old['state'] == 'disabled'
    chain.reorg(repair_block)
    ssv._get(OWNER, OPS)[1].update(after_poison)
    chain.mine(2)
    advance(disabled)
    live = restart(tmp_path, chain, w)
    advance(live)
    assert incidents(live)[0] == old
    assert len(incidents(live, 'open')) == 1
    assert 'Stale cluster tuple' in outbox(live, "check_id='m1'")[-1]['text']
    assert live.db.meta('reorg:m1') is None


def test_m2_disabled_hard_condition_reorg_empty_api_rebuilds_from_own_state(tmp_path):
    from test_m2 import setup, restart_m2, open_keys
    from harness import make_worker, advance, VIEWS
    chain, w, ssv, rows = setup(tmp_path, eth=2981, private=True, m2={'early_headroom': 1})
    advance(w)
    assert 'hard:1' in open_keys(w)
    fork = chain.number
    chain.mine(3)
    advance(w)
    _, disabled = make_worker(tmp_path, only=[], chain=chain, clock=w.clock, views=VIEWS,
        start_block=w.cfg.start_block, activation_block=w.cfg.activation_block, checks={'m2': {'enabled': False}})
    old = [dict(r) for r in w.db.conn.execute("SELECT * FROM incidents WHERE check_id='m2'")]
    assert old and all(r['state'] == 'disabled' for r in old)
    rows.clear()
    chain.reorg(fork)
    chain.mine(2)
    advance(disabled)
    live = restart_m2(tmp_path, chain, w, rows)
    advance(live)
    assert 'hard:1' in open_keys(live)
    for r in old:
        assert dict(live.db.conn.execute('SELECT * FROM incidents WHERE id=?', (r['id'],)).fetchone()) == r
    assert live.db.meta('reorg:m2') is None
