"""Confirm the missing M5 reorg resolution; no message oracle weakened."""
import pytest
import eb_model as M
from harness import advance, outbox
from test_switch_ids import live_m5, flip, restart, row, msgs, sent, heal
from test_worker import make as make_m5, OWNER, OPS
from fakechain import MiniSSV, keys as pubkeys
from test_qa2_deep import snapshot, replay

@pytest.mark.parametrize('with_flip',[True,False])
def test_m5_live_page_becomes_latent_during_silent_repeat(tmp_path,with_flip):
    if with_flip:
        chain,w,ssv,page=live_m5(tmp_path)
        fork=row(w,page['incident_id'])['opened_block']
    else:
        # Public global path, latent at its historical pin and a live confirming removal.
        chain,w=make_m5(tmp_path,backfill={'enabled':True},max_log_range=1)
        ssv=MiniSSV(chain)
        ssv.register(OWNER,OPS,pubkeys(1,1))
        ref=chain.number
        chain.mine()
        ssv.commit(ref)
        ssv.update(OWNER,OPS,2048)
        fork=chain.number
        ref=chain.number
        chain.mine()
        dummy=pubkeys(63,100)
        ssv.register(OWNER,OPS,dummy)
        ssv.commit(ref)
        ssv.update(OWNER,OPS,2048)
        advance(w)
        chain.mine()
        advance(w)
        drop=chain.number
        ssv.remove(OWNER,OPS,dummy)
        advance(w)
        (page,)=[m for m in sent(w,'m5') if m['severity']=='page']
    old=page['incident_id']
    target=old
    if with_flip:
        w=flip(w,chain,'m5',False)
        frozen=row(w,old)
        chain.mine(3)
        advance(w)
        w=flip(w,chain,'m5',True)
        advance(w)
        chain.mine()
        advance(w)
        target=old+'-2'
    before=len(sent(w,'m5'))
    saved=snapshot(chain)
    w=restart(w,chain)
    replay(chain,saved,fork,fork if with_flip else drop)
    advance(w)
    chain.mine()
    advance(w)
    w=restart(w,chain)
    chain.mine()
    advance(w)
    evaluated=w.checks['m5'].last_evals
    print({'flip':with_flip,'target':target,'evals':[(e.status,e.matched_keys) for e in evaluated],
           'framework':[{k:r[k] for k in ('id','state','severity','notify','alerts','body')} for r in w.db.conn.execute("SELECT * FROM incidents WHERE check_id='m5'")],
           'own':[dict(r) for r in w.db.conn.execute('SELECT id,state,kind FROM m5_incidents')],
           'messages_after_reorg':msgs(w,before),'catchup':w.db.meta('catchup:m5'),'reorg':w.db.meta('reorg:m5')})
    assert evaluated and evaluated[0].status==M.LATENT
    now=w.clock()
    w.clock=w.alerts.clock=lambda: now+7*3600
    chain.mine()
    advance(w)
    print({'after_7h':msgs(w,before),'target':row(w,target)})
    assert not outbox(w,"check_id='m5' AND kind='realert'")
    heal(chain,ssv)
    advance(w)
    print({'after_heal':msgs(w,before),'target':row(w,target)})
    keys=[m['key'] for m in outbox(w)]
    assert len(keys)==len(set(keys))
    if with_flip:
        assert row(w,old)==frozen
    assert (w.db.meta('run:m5') or {}).get('errors',0)==0
    assert msgs(w,before)==[('info','resolved',target)], 'the canonical confirmed-to-latent transition must resolve the sent page once'
