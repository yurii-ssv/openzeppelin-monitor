"""Independent round-2 falsifiers, run only in disposable candidate archives."""
import copy
import pytest
from fakechain import FakeRpc, MiniSSV, keys as pubkeys
from fixtures_cluster import make_m1, deposit, poison, OWNER, OPS, set_operator
from harness import advance, outbox
from test_m1 import incidents as m1_incidents
from test_m2 import setup as m2_setup
from test_worker import make as make_m5, OWNER as EB_OWNER, OPS as EB_OPS
import checks as registry
from eb_reconcile import Worker, Config, DB


def restart(w, chain, only=None):
    built = registry.build(w.cfg.raw, only=only if only is not None else list(w.checks))
    new = Worker(Config(copy.deepcopy(w.cfg.raw)), FakeRpc(chain), DB(w.cfg.db_path), w.notifier,
                 clock=w.clock, checks=built)
    new.bootstrap()
    return new


def snapshot(chain):
    return copy.deepcopy((chain.logs, chain.writes, chain.sealed))


def replay(chain, saved, fork, drop_after=None):
    logs, writes, old = copy.deepcopy(saved)
    chain.reorg(fork)
    if drop_after is not None:
        logs = [lg for lg in logs if int(lg['blockNumber'], 16) < drop_after]
        writes = [wr for wr in writes if wr[0] < drop_after]
    for lg in logs:
        lg['blockHash'] = chain.hash(int(lg['blockNumber'], 16))
    chain.logs, chain.writes = logs, writes
    chain.mine(old + 2 - chain.number)


def sent(w, cid, after=0):
    return outbox(w, f"check_id='{cid}' AND kind!='health'")[after:]


def establish(w, chain, path):
    if path == 'percheck':
        # A completed global bootstrap with no checks, followed by the new-check start.
        w = restart(w, chain, only=[])
        advance(w)
        w = restart(w, chain, only=['m1'])
    advance(w)
    pin = (w.db.meta('catchup:m1') or {}).get('history_block') or w.phase()['history_block']
    chain.mine()
    advance(w)
    return w, pin


@pytest.mark.parametrize('path', ['global', 'percheck'])
@pytest.mark.parametrize('live_supported', [True, False])
def test_m1_live_poison_after_deep_repeat(tmp_path, path, live_supported):
    chain, w, ssv = make_m1(tmp_path, backfill={'enabled': True}, max_log_range=1)
    deposit(ssv, value=10**18)
    w, pin = establish(w, chain, path)
    chain.mine()
    fork = pin
    poison(ssv)
    poison_block = chain.sealed
    advance(w)
    assert [(m['severity'], m['kind']) for m in sent(w, 'm1')] == [('warn', 'open')]
    saved, mark = snapshot(chain), len(sent(w, 'm1'))
    w = restart(w, chain)
    replay(chain, saved, fork, None if live_supported else poison_block)
    advance(w)
    actual = [(m['severity'], m['kind']) for m in sent(w, 'm1', mark)]
    print({'m1': path, 'supported': live_supported, 'pin': pin, 'actual': actual,
           'rows': [(r['state'], r['severity']) for r in m1_incidents(w)]})
    assert actual == ([] if live_supported else [('info', 'resolved')])
    assert w.db.meta('reorg:m1') is None and w.db.meta('catchup:m1') is None


@pytest.mark.parametrize('path', ['global', 'percheck'])
def test_m1_historical_transient_stays_silent_across_deep_repeat(tmp_path, path):
    chain, w, ssv = make_m1(tmp_path, backfill={'enabled': True}, max_log_range=1)
    deposit(ssv, value=10**18)
    poison(ssv)
    deposit(ssv)
    w, pin = establish(w, chain, path)
    assert sent(w, 'm1') == []
    saved = snapshot(chain)
    w = restart(w, chain)
    replay(chain, saved, pin - 2)
    advance(w)
    assert sent(w, 'm1') == []


@pytest.mark.parametrize('supported', [True, False])
def test_m2_live_condition_after_global_repeat_with_empty_api(tmp_path, supported):
    chain, w, ssv, rows = m2_setup(tmp_path, eth=0, private=True, m2={'early_headroom': 1},
                                  backfill={'enabled': True}, max_log_range=1)
    advance(w)
    pin = w.phase()['history_block']
    chain.mine()
    advance(w)
    set_operator(chain, 1, eth=2981, ssv=20, private=True)
    chain.mine()
    w.clock.advance(601)
    advance(w)
    assert sorted((m['severity'], m['kind']) for m in sent(w, 'm2')) == [('info','open'),('warn','open')]
    live = chain.sealed
    saved, mark = snapshot(chain), len(sent(w, 'm2'))
    rows.clear()
    w = restart(w, chain)
    w.inventory.http_get = lambda url: {'clusters': [], 'pagination': {'pages': 1}}
    replay(chain, saved, pin, None if supported else live)
    if not supported:  # FakeChain reorg drops storage/logs but eth_call stubs need explicit rewind.
        chain.call_stubs = {key: [e for e in entries if e[0] < live]
                            for key, entries in chain.call_stubs.items()}
    advance(w)
    actual = [(m['severity'], m['kind']) for m in sent(w, 'm2', mark)]
    print({'m2_supported': supported, 'actual': actual, 'marker': w.db.meta('reorg:m2')})
    assert actual == ([] if supported else [('info', 'resolved')])
    assert w.db.meta('reorg:m2') is None and w.db.meta('catchup:m2') is None


@pytest.mark.parametrize('boundary_state', ['clean', 'latent', 'confirmed'])
@pytest.mark.parametrize('supported', [True, False])
def test_m5_real_state_after_global_repeat(tmp_path, boundary_state, supported):
    chain, w = make_m5(tmp_path, backfill={'enabled': True}, max_log_range=1)
    ssv = MiniSSV(chain)
    real, dummy = pubkeys(1, 1), pubkeys(63, 100)
    ssv.register(EB_OWNER, EB_OPS, real)
    root = chain.number
    chain.mine()
    ssv.commit(root)
    ssv.update(EB_OWNER, EB_OPS, 2048)
    attack_start = chain.number
    def latent():
        ref = chain.number
        chain.mine()
        ssv.register(EB_OWNER, EB_OPS, dummy)
        ssv.commit(ref)
        ssv.update(EB_OWNER, EB_OPS, 2048)
    if boundary_state != 'clean':
        latent()
    if boundary_state == 'confirmed':
        ssv.remove(EB_OWNER, EB_OPS, dummy)
    advance(w)
    pin = w.phase()['history_block']
    chain.mine()
    advance(w)
    if boundary_state == 'clean':
        latent()
    if boundary_state != 'confirmed':
        ssv.remove(EB_OWNER, EB_OPS, dummy)
    advance(w)
    assert len([m for m in sent(w, 'm5') if m['severity']=='page']) == 1
    saved, mark = snapshot(chain), len(sent(w, 'm5'))
    original = [dict(r) for r in w.db.conn.execute('SELECT * FROM incidents')]
    w = restart(w, chain)
    replay(chain, saved, attack_start, None if supported else attack_start)
    advance(w)
    actual = [(m['severity'], m['kind']) for m in sent(w, 'm5', mark)]
    internal = [dict(r) for r in w.db.conn.execute('SELECT * FROM m5_incidents')]
    print({'boundary': boundary_state, 'supported': supported, 'pin': pin, 'actual': actual,
           'original': [(r['id'],r['revision']) for r in original],
           'internal': [(r['id'],r['state'],r['kind']) for r in internal]})
    assert actual == ([] if supported else [('info','resolved')])
    assert w.db.meta('reorg:m5') is None and w.db.meta('catchup:m5') is None


def flip(w, chain, cid, enabled):
    raw = copy.deepcopy(w.cfg.raw)
    raw.setdefault('checks', {}).setdefault(cid, {})['enabled'] = enabled
    new = Worker(Config(raw), FakeRpc(chain), DB(w.cfg.db_path), w.notifier,
                 clock=w.clock, checks=registry.build(raw, only=[cid]))
    new.bootstrap()
    return new


def test_m5_real_disabled_key_gets_new_id_and_first_live_page(tmp_path):
    chain, w = make_m5(tmp_path, backfill={'enabled': True}, max_log_range=1)
    ssv = MiniSSV(chain)
    ssv.register(EB_OWNER, EB_OPS, pubkeys(1, 1))
    ref = chain.number
    chain.mine()
    ssv.commit(ref)
    ssv.update(EB_OWNER, EB_OPS, 2048)
    advance(w)
    chain.mine()
    advance(w)
    ref = chain.number
    chain.mine()
    dummy = pubkeys(63, 100)
    ssv.register(EB_OWNER, EB_OPS, dummy)
    ssv.commit(ref)
    ssv.update(EB_OWNER, EB_OPS, 2048)
    ssv.remove(EB_OWNER, EB_OPS, dummy)
    advance(w)
    (old_page,) = [m for m in sent(w, 'm5') if m['severity']=='page']
    w = flip(w, chain, 'm5', False)
    frozen = dict(w.db.conn.execute('SELECT * FROM incidents WHERE id=?', (old_page['incident_id'],)).fetchone())
    assert frozen['state'] == 'disabled'
    chain.mine()
    advance(w)
    w = flip(w, chain, 'm5', True)
    before = len(sent(w, 'm5'))
    advance(w)
    silent_record = w.db.meta('catchup:m5')
    chain.mine()
    advance(w)
    after_two_attempts = w.db.meta('catchup:m5')
    w = restart(w, chain)  # a restart cannot repair the retained internal id
    chain.mine()
    advance(w)
    assert w.db.meta('catchup:m5') == after_two_attempts
    assert dict(w.db.conn.execute('SELECT * FROM incidents WHERE id=?', (old_page['incident_id'],)).fetchone()) == frozen
    actual = [(m['severity'], m['kind'], m['incident_id']) for m in sent(w, 'm5', before)]
    print({'m5_reenable_messages': actual, 'record_after_silent': silent_record,
           'record_now': w.db.meta('catchup:m5'), 'run': w.db.meta('run:m5'),
           'health': [m['text'] for m in outbox(w, "kind='health'")]})
    assert len(actual) == 1 and actual[0][:2] == ('page', 'open'), 'surviving M5 condition missed its first-live page'
    assert actual[0][2] != old_page['incident_id']


from test_catchup import Env, ctl  # noqa: E402,F401
from test_history_silence import _boundary, restore_head, keys  # noqa: E402
from checks.base import Finding  # noqa: E402
from rpc import RpcError  # noqa: E402


@pytest.mark.parametrize('path', ['global', 'percheck'])
@pytest.mark.parametrize('stage', ['repeat', 'live'])
@pytest.mark.parametrize('failure', ['runtime', 'rpc', 'partial'])
def test_repeat_and_forced_live_failures_are_atomic_and_restartable(tmp_path, ctl, monkeypatch, path, stage, failure):
    env = Env(tmp_path, max_log_range=1)
    hist, pin = _boundary(env, path)
    env.step()
    env.step()
    mark, old_head = env.last_rowid(), env.chain.sealed
    env.start()
    original = type(env.check('cu_a')).run
    def broken(self, ctx, block):
        target = 'backfill' if stage == 'repeat' else 'live'
        if target not in ctx.reasons:
            yield from original(self, ctx, block)
            return
        if failure == 'partial':
            yield Finding(check=self.id, key='partial-uncommitted', severity='page', title='partial', body='')
        if failure == 'rpc':
            raise RpcError('repeat transport failure', transport=True)
        raise RuntimeError('repeat failure')
    monkeypatch.setattr(type(env.check('cu_a')), 'run', broken)
    env.chain.reorg(hist)
    assert env.event('cu_a') == hist
    env.chain.mine(pin - hist + 1)
    live_b = env.event('cu_b')
    restore_head(env, old_head)
    before = env.rec('cu_a')
    assert before['state'] == ('pending' if stage == 'repeat' else 'first_live')
    assert before['history_block'] == pin
    assert not any(r['key']=='partial-uncommitted' for r in env.rows('cu_a'))
    assert keys(env, after=mark) == [] and keys(env, after=mark, cid='cu_b') == [f'ev:{live_b}']
    env.start()
    env.step(mine=False)
    assert env.rec('cu_a') == before and keys(env, after=mark) == []
    monkeypatch.setattr(type(env.check('cu_a')), 'run', original)
    env.start()
    env.step()
    assert env.rec('cu_a') is None and env.w.db.meta('reorg:cu_a') is None
    assert keys(env, after=mark) == []


def test_silent_boundary_commit_failure_rolls_back_findings_cursor_and_record(tmp_path, ctl, monkeypatch):
    env = Env(tmp_path, max_log_range=1)
    env.go_live(only=('cu_b',))
    hist = env.event('cu_a')
    env.step()
    env.start()
    before_run = env.w.db.meta('run:cu_a')
    before_cursor = env.w.db.meta('cursor:events:cu_a')
    real = env.w.db.set_meta
    def broken(key, value):
        real(key, value)
        if key == 'silent:cu_a':
            raise RuntimeError('crash after durable boundary write')
    monkeypatch.setattr(env.w.db, 'set_meta', broken)
    env.step()
    pin = env.rec('cu_a')['history_block']
    assert env.rec('cu_a')['state']=='pending' and env.w.db.meta('silent:cu_a') is None
    assert env.rows('cu_a') == [] and keys(env)==[]
    assert env.w.db.meta('cursor:events:cu_a')==before_cursor
    assert (env.w.db.meta('run:cu_a') or {}).get('block')==(before_run or {}).get('block')
    env.start()
    env.step()
    assert env.rec('cu_a') is None and env.w.db.meta('silent:cu_a')==pin
    assert [r['key'] for r in env.rows('cu_a')]==[f'ev:{hist}'] and keys(env)==[]
