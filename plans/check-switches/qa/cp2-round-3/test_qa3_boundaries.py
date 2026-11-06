"""Round-3 independent D16 boundaries; execute only inside disposable archives."""
from dataclasses import replace
import pytest
import eb_model as M
from fakechain import MiniSSV, keys as pubkeys
from harness import advance, outbox
from test_worker import make as make_m5, OWNER, OPS
from test_switch_ids import live_m5, flip, restart, heal, row, msgs, sent
from test_qa2_deep import snapshot, replay
from checks.base import Finding
from checks.m5_eb_desync import M5EbDesync


def unique_outbox(w):
    keys = [m['key'] for m in outbox(w)]
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize('state', ['open', 'resolved'])
def test_foreign_check_id_collision_does_not_suffix(tmp_path, state):
    chain, w = make_m5(tmp_path)
    a = Finding(check='foreign', key='a', severity='page', title='a', body='', incident_id='held')
    b = replace(a, check='m5', key='b')
    with w.db.tx():
        w.alerts.apply([a], 1001)
        if state == 'resolved':
            w.alerts.apply([replace(a, resolve=True)], 1002)
    before = list(w.db.conn.iterdump())
    with pytest.raises(ValueError, match='incident id held already exists'), w.db.tx():
        w.alerts.apply([b], 1003)
    assert list(w.db.conn.iterdump()) == before
    unique_outbox(w)


@pytest.mark.parametrize('count', [2, 5])
def test_many_disabled_suffixes_and_partial_apply_rollback(tmp_path, count):
    chain, w = make_m5(tmp_path)
    f = Finding(check='m5', key='base', severity='page', title='base', body='', incident_id='held')
    with w.db.tx():
        for n in range(1, count+1):
            inc_id = 'held' if n == 1 else f'held-{n}'
            w.alerts.apply([replace(f, key=str(n), incident_id=inc_id)],1001)
        w.db.conn.execute("UPDATE incidents SET state='disabled'")
        w.alerts.apply([replace(f, key='reserved', incident_id='reserved')],1001)
    before = list(w.db.conn.iterdump())
    with pytest.raises(ValueError), w.db.tx():
        w.alerts.apply([f, replace(f, key='collision', incident_id='reserved')],1002)
    assert list(w.db.conn.iterdump()) == before
    with w.db.tx():
        w.alerts.apply([f],1002)
    assert row(w, f'held-{count+1}')['state'] == 'open'
    unique_outbox(w)


@pytest.mark.parametrize('stage', ['silent', 'live'])
@pytest.mark.parametrize('supported', [True, False])
def test_real_m5_reorg_around_suffixed_incident(tmp_path, stage, supported):
    chain, w, ssv, page = live_m5(tmp_path)
    old = page['incident_id']
    fork = row(w, old)['opened_block']  # the confirming removal; retained or orphaned below the re-enable pin
    w = flip(w, chain, 'm5', False)
    frozen = row(w, old)
    chain.mine(3)
    advance(w)
    w = flip(w, chain, 'm5', True)
    before = len(sent(w, 'm5'))
    advance(w)
    assert row(w, old+'-2')['notify'] == 0 and msgs(w,before) == []
    if stage == 'live':
        chain.mine()
        advance(w)
        assert msgs(w,before) == [('page','open',old+'-2')]
    saved = snapshot(chain)
    w = restart(w, chain)
    replay(chain,saved,fork,None if supported else fork)
    advance(w)
    chain.mine()
    advance(w)
    w = restart(w,chain)
    chain.mine()
    advance(w)
    actual = msgs(w,before)
    expected = [('page','open',old+'-2')] if supported or stage == 'live' else []
    if not supported and stage == 'live':
        expected.append(('info','resolved',old+'-2'))
    print({'stage':stage,'supported':supported,'messages':actual,'framework':[(r['id'],r['state']) for r in w.db.conn.execute("SELECT * FROM incidents WHERE check_id='m5'")]})
    assert actual == expected
    assert row(w,old) == frozen
    assert w.db.meta('catchup:m5') is None and w.db.meta('reorg:m5') is None
    assert (w.db.meta('run:m5') or {}).get('errors',0) == 0
    unique_outbox(w)


def test_real_m5_disabled_reorg_unknown_allocates_suffix_and_recovers(tmp_path):
    chain,w,ssv,page=live_m5(tmp_path)
    old=page['incident_id']
    fork=chain.number  # confirmed condition below fork remains canonical
    w=flip(w,chain,'m5',False)
    frozen=row(w,old)
    chain.mine(4)
    advance(w)
    chain.reorg(fork)
    omitted=chain.number
    ssv.register(OWNER,OPS,pubkeys(1,7500))
    chain.drop_logs_in=(omitted,omitted)  # real model UNKNOWN: storage tuple lacks its supporting registration log
    chain.mine(4)
    advance(w)
    ancestor=w.db.meta('reorg:m5')
    assert ancestor == fork-1
    w=flip(w,chain,'m5',True)
    before=len(sent(w,'m5'))
    advance(w)
    (ev,)=w.checks['m5'].last_evals
    assert ev.status == M.UNKNOWN
    assert row(w,old+'-2')['notify'] == 0
    assert row(w,old+'-2')['state'] == 'open'
    assert 'UNKNOWN' in row(w,old+'-2')['body']
    assert w.db.meta('catchup:m5')['state'] == 'first_live'
    assert msgs(w,before) == [] and row(w,old) == frozen
    w=restart(w,chain)
    # Restore complete canonical input by a second supported replay; never invent proof of a clean state.
    saved=snapshot(chain)
    chain.drop_logs_in=None
    replay(chain,saved,fork)
    advance(w)
    chain.mine()
    advance(w)
    actual=msgs(w,before)
    print({'unknown_ancestor':ancestor,'unknown_reasons':ev.reasons,'messages':actual,'internal':[dict(r) for r in w.db.conn.execute('SELECT id,state,kind FROM m5_incidents')]})
    assert actual == [('page','open',old+'-2')]
    assert row(w,old) == frozen and row(w,old+'-2')['state'] == 'open'
    assert w.db.meta('catchup:m5') is None and w.db.meta('reorg:m5') is None
    assert (w.db.meta('run:m5') or {}).get('errors',0) == 0
    assert not outbox(w,"kind='health' AND key LIKE '%check-error%'")
    unique_outbox(w)


def test_real_m5_candidate_reenable_then_confirmed_uses_framework_suffix(tmp_path,monkeypatch):
    chain,w=make_m5(tmp_path,backfill={'enabled':True},max_log_range=1)
    ssv=MiniSSV(chain)
    ssv.register(OWNER,OPS,pubkeys(1,1))
    ref=chain.number
    chain.mine()
    ssv.commit(ref)
    ssv.update(OWNER,OPS,2048)
    advance(w)
    chain.mine()
    advance(w)
    evaluate=M5EbDesync.evaluate
    def incomplete(self,rpc,db,cids,block):
        return [replace(ev,status=M.UNKNOWN,reasons=['incomplete accounting fixture']) if ev.matched_keys else ev
                for ev in evaluate(self,rpc,db,cids,block)]
    monkeypatch.setattr(M5EbDesync,'evaluate',incomplete)
    ref=chain.number
    chain.mine()
    dummy=pubkeys(63,100)
    ssv.register(OWNER,OPS,dummy)
    ssv.commit(ref)
    ssv.update(OWNER,OPS,2048)
    ssv.remove(OWNER,OPS,dummy)
    advance(w)
    (candidate,)=w.db.conn.execute("SELECT * FROM m5_incidents WHERE state='open'").fetchall()
    old=candidate['id']
    assert candidate['kind']=='candidate'
    assert msgs(w)==[('warn','open',old)]
    w=flip(w,chain,'m5',False)
    frozen=row(w,old)
    w=flip(w,chain,'m5',True)
    before=len(sent(w,'m5'))
    advance(w)
    # An existing candidate proposes no finding on ordinary UNKNOWN; it must not resolve or fabricate a page.
    assert msgs(w,before)==[] and row(w,old)==frozen and row(w,old+'-2') is None
    assert w.db.meta('catchup:m5')['state']=='first_live'
    monkeypatch.setattr(M5EbDesync,'evaluate',evaluate)
    chain.mine()
    advance(w)
    assert msgs(w,before)==[('page','open',old+'-2')]
    assert w.db.meta('catchup:m5') is None
    own=w.db.conn.execute("SELECT * FROM m5_incidents WHERE state='open'").fetchone()
    assert (own['id'],own['kind'])==(old,'confirmed')
    heal(chain,ssv)
    advance(w)
    assert msgs(w,before)==[('page','open',old+'-2'),('info','resolved',old+'-2')]
    assert row(w,old)==frozen
    unique_outbox(w)
