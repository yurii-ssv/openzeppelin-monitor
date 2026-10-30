"""Regression tests for CP1B QA attempt 2 (plans/monitoring-service/qa/cp1b-attempt-2.md).

`test_qa2_appendix_a_reorg_repro` is Appendix A (the HIGH finding's reproducer) as a test; the rest is
Appendix B unchanged in every assertion. Fixture change (inbox gate, checks/README.md §5.2): the inbox line
of `test_partial_reload_atomic_cursors_recovery` carries the fake chain's canonical hash of its block
instead of the synthetic `0xcdcd…` default; entries of non-canonical blocks are no longer delivered.
"""

import errno
import json
import sqlite3

import pytest
from fakechain import PROXY, FakeChain, FakeClock
from harness import VIEWS, make_worker, outbox, stub_params
from test_framework import LEGACY_SCHEMA, Stub, finding, inbox_line

from checks.base import Every, OnEvents, OnInbox
from db import DB
from eb_storage import u256
from params import GETTERS, ParamsUnavailable, selector
from rpc import Chain


@pytest.mark.parametrize('old,new,route', [('critical','page','telegram'), ('warning','warn','telegram'), ('info','info','discord')])
@pytest.mark.parametrize('sent', [False, True])
def test_legacy_all_severities_twice_and_existing_delivery(tmp_path, old, new, route, sent):
    p = tmp_path/'w.sqlite'
    con = sqlite3.connect(p)
    con.executescript(LEGACY_SCHEMA)
    con.execute('INSERT INTO outbox(key,created_at,severity,text,attempts,next_attempt_at,sent_at,delivery,last_error) VALUES(?,?,?,?,?,?,?,?,?)',
                ('old-key', 1, old, 'original text', 2, 123, 7 if sent else None, 'telegram:42', 'prior error'))
    con.commit(); con.close()
    first = DB(str(p))
    a = dict(first.conn.execute('SELECT * FROM outbox').fetchone())
    assert (a['key'], a['created_at'], a['severity'], a['text'], a['attempts'], a['next_attempt_at'], a['last_error']) == ('old-key',1,new,'original text',2,123,'prior error')
    assert (a['delivery'],a['receipt']) == (('telegram','telegram:42') if sent else (None,None))
    first.conn.close()
    second = DB(str(p))
    assert second.migrated_from == 1
    assert dict(second.conn.execute('SELECT * FROM outbox').fetchone()) == a
    second.conn.close()
    chain=FakeChain();chain.mine()
    _,w=make_worker(tmp_path,[],chain=chain)
    w.deliver();w.deliver()
    assert len(w.notifier.sent) == (0 if sent else 1)
    r=outbox(w)[0]
    assert r['sent_at'] is not None
    assert r['delivery'] == ('telegram' if sent else route)
    assert r['receipt'] == ('telegram:42' if sent else 'captured')


@pytest.mark.parametrize('failure', ['revert','empty'])
def test_partial_reload_atomic_cursors_recovery(tmp_path, failure):
    chain=FakeChain();clock=FakeClock();stub_params(chain);chain.mine()
    path=tmp_path/'in.jsonl';path.write_text('')
    seen=[]
    class Consumer(Stub):
        def prepare(self,ctx):
            self.copy=ctx.params.liquidation_threshold_period
        def run(self,ctx,b):
            seen.append((self.copy,ctx.params.network_fee,[(e.block,e.log_index) for e in ctx.events()],len(ctx.inbox)))
            ctx.set_state('n',ctx.state('n',0)+1)
            return [finding()]
    consumer=Consumer(schedule=(Every(60),OnEvents({'LiquidationThresholdPeriodUpdated'}),OnInbox()))
    _,w=make_worker(tmp_path,[consumer],chain=chain,clock=clock,views=VIEWS,inbox={'path':str(path)})
    w.step(); assert [(x[0],x[1]) for x in seen] == [(42960,3366600000)]
    event_cursor=w.db.meta('cursor:events:stub');inbox_cursor=w.db.meta('cursor:inbox:stub')
    good_block=w.params.block
    stub_params(chain,liquidation_threshold_period=50000,network_fee=123456)
    # One getter fails while all others have new successful answers.
    kw={'revert':'0x12345678'} if failure=='revert' else {'result':b''}
    chain.stub_call(VIEWS,selector(GETTERS['minimum_operator_eth_fee']),**kw)
    chain.emit_event('LiquidationThresholdPeriodUpdated',[],u256(50000))
    tx=chain.add_tx('0x'+'11'*20,PROXY,'0xbc26e7e5')
    chain.stub_call(PROXY,'0xbc26e7e5',revert='0x12e04c87')
    chain.mine();path.write_text(inbox_line(tx=tx,block=chain.sealed,block_hash=chain.hash(chain.sealed)))
    for _ in range(5):
        w.step(force=True)
        assert len(seen)==1 and w.params.values=={} and w.params.block==good_block
        assert w.db.meta('cursor:events:stub')==event_cursor
        assert w.db.meta('cursor:inbox:stub')==inbox_cursor
        assert w.db.meta('check:stub:n')==1
        assert w.status()['params']['ok'] is False
        for access in (lambda:w.params.network_fee,lambda:w.params['network_fee'],lambda:w.params.get('network_fee',99)):
            with pytest.raises(ParamsUnavailable):access()
    assert len(outbox(w,"key LIKE 'health:params:%'"))==1
    assert not outbox(w,"key LIKE 'health:check-error:%'")
    stub_params(chain,liquidation_threshold_period=50000,network_fee=123456);chain.mine()
    w.step()  # no force and clock unchanged: recovery itself must schedule the consumer
    assert [(x[0],x[1]) for x in seen] == [(42960,3366600000),(50000,123456)]
    assert len(seen[-1][2])==1 and seen[-1][3]==1
    assert w.db.meta('check:stub:n')==2 and w.db.meta('cursor:inbox:stub')==1
    assert 'params' in w.db.meta('run:stub')['reasons'] and w.params.ok
    assert len(outbox(w,"kind='open'"))==1
    w.step();assert len(seen)==2


@pytest.mark.parametrize('state',['missing','permission','directory','empty'])
def test_real_inbox_path_states(tmp_path,state):
    chain=FakeChain();chain.mine();p=tmp_path/'in.jsonl'
    if state in ('empty','permission'):p.write_text('')
    if state=='directory':p.mkdir()
    if state=='permission':
        p.chmod(0)
        try:
            with pytest.raises(PermissionError) as err:p.read_bytes()
            assert err.value.errno==errno.EACCES
        except BaseException:
            p.chmod(0o600);raise
    try:
        _,w=make_worker(tmp_path,[Stub(schedule=(Every(60),OnInbox()))],chain=chain,inbox={'path':str(p)})
        for _ in range(6):w.step(force=True)
        s=json.loads((tmp_path/'w.status.json').read_text())
        assert s['healthy'] is (state=='empty')
        assert s['inbox']['ok'] is (state=='empty')
        assert len(outbox(w,"key LIKE 'health:inbox-unavailable:%'"))==(0 if state=='empty' else 1)
        if state=='permission':assert 'PermissionError' in s['inbox']['error']
        assert w.db.conn.execute('SELECT COUNT(*) FROM inbox').fetchone()[0]==0
    finally:
        if state=='permission':p.chmod(0o600)


def test_inbox_sustained_outage_realert_restart_and_flapping(tmp_path):
    chain=FakeChain();chain.mine();clock=FakeClock();p=tmp_path/'in.jsonl'
    alerts={'mode':'record','digest_interval_s':0,'health_realert_s':100}
    _,w=make_worker(tmp_path,[],chain=chain,clock=clock,inbox={'path':str(p)},alerts=alerts)
    for _ in range(20):w.step()
    assert len(outbox(w))==1
    _,w2=make_worker(tmp_path,[],chain=chain,clock=clock,inbox={'path':str(p)},alerts=alerts,
                     start_block=w.cfg.start_block,activation_block=w.cfg.activation_block)
    w2.step();assert len(outbox(w2))==1
    clock.advance(101);w2.step();assert len(outbox(w2))==2
    for _ in range(5):w2.step()
    assert len(outbox(w2))==2
    # Every recovered/new outage is a separately documented outage; stable down steps never storm.
    for i in range(3):
        p.write_text('');w2.step();assert not w2.degraded()
        p.unlink();w2.step()
        assert len(outbox(w2))==3+i
        for _ in range(5):w2.step()
        assert len(outbox(w2))==3+i
    assert w2.db.meta('inbox:outages')==4


def test_params_flap_dedup_and_window(tmp_path):
    chain=FakeChain();stub_params(chain);chain.mine();clock=FakeClock()
    _,w=make_worker(tmp_path,[],chain=chain,clock=clock,views=VIEWS,
                    alerts={'mode':'record','digest_interval_s':0,'health_realert_s':100})
    w.step()
    for i in range(3):
        chain.stub_call(VIEWS,selector(GETTERS['liquidation_threshold_period']),revert='0x12345678')
        chain.emit_event('LiquidationThresholdPeriodUpdated',[],u256(50000+i));chain.mine()
        for _ in range(5):w.step()
        assert len(outbox(w,"key LIKE 'health:params:%'"))==1
        stub_params(chain,liquidation_threshold_period=50000+i);chain.mine();w.step()
        assert w.params.liquidation_threshold_period==50000+i
    clock.advance(101)
    chain.stub_call(VIEWS,selector(GETTERS['liquidation_threshold_period']),revert='0x12345678')
    chain.emit_event('LiquidationThresholdPeriodUpdated',[],u256(60000));chain.mine();w.step()
    assert len(outbox(w,"key LIKE 'health:params:%'"))==2


def test_reorged_parameter_event_never_leaves_orphan_threshold(tmp_path):
    chain=FakeChain();stub_params(chain);chain.mine();observed=[]
    stub=Stub(emit=lambda ctx,b:observed.append(ctx.params.liquidation_threshold_period) or [])
    _,w=make_worker(tmp_path,[stub],chain=chain,views=VIEWS)
    w.step();assert observed==[42960]
    fork_point=chain.number
    stub_params(chain,liquidation_threshold_period=50000)
    chain.emit_event('LiquidationThresholdPeriodUpdated',[],u256(50000));chain.mine()
    w.step();assert observed==[42960,50000]
    chain.reorg(fork_point)
    # FakeChain.reorg drops logs/storage, but not call stubs: explicitly restore canonical getter state.
    stub_params(chain,liquidation_threshold_period=42960);chain.mine()
    assert int.from_bytes(Chain(w.rpc,chain.sealed).call(VIEWS,selector(GETTERS['liquidation_threshold_period'])),'big')==42960
    w.step()
    assert observed==[42960,50000,42960], {'observed':observed,'params_block':w.params.block,'checkpoint':w.checkpoint(),'last_run':w.db.meta('run:stub')}


def test_qa2_appendix_a_reorg_repro(tmp_path):
    seen={'direct':[],'cached':[]}
    class Cached(Stub):
        def prepare(self,ctx):self.threshold=ctx.params.liquidation_threshold_period
        def run(self,ctx,b):seen['cached'].append(self.threshold);return []
    c=FakeChain();stub_params(c);c.mine()
    checks=[Stub(check_id='direct',emit=lambda ctx,b:seen['direct'].append(ctx.params.liquidation_threshold_period) or []),Cached(check_id='cached')]
    p=tmp_path
    _,w=make_worker(p,checks,chain=c,views=VIEWS)
    w.step();fork=c.number
    stub_params(c,liquidation_threshold_period=50000)
    c.emit_event('LiquidationThresholdPeriodUpdated',[],u256(50000));c.mine();w.step()
    c.reorg(fork)
    stub_params(c,liquidation_threshold_period=42960);c.mine();w.step()
    for _ in range(2):c.mine();w.step(force=True)
    canonical=int.from_bytes(Chain(w.rpc,c.sealed).call(VIEWS,selector(GETTERS['liquidation_threshold_period'])),'big')
    status=json.loads((p/'w.status.json').read_text())
    assert seen['direct'][2:]==[canonical]*3 and seen['cached'][2:]==[canonical]*3
    assert w.params.ok and status['healthy']
