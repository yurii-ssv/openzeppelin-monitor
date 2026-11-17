import pathlib,sys,json,tempfile
ROOT=pathlib.Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'reconcile'),str(ROOT/'reconcile/tests')]
from fakechain import FakeChain,FakeClock,MiniSSV,keys
from test_worker import make,tick,ev_for,OWNER,OPS
p=pathlib.Path(tempfile.mkdtemp(prefix='qa3-orphan-'))
confirmations=int(sys.argv[1]) if len(sys.argv)>1 else 0
c=FakeChain();clock=FakeClock();c.mine(1+confirmations);_,w=make(p,c,start_block=1000,activation_block=1000,confirmations=confirmations)
w.clock=clock;w.alerts.clock=clock
w.step(force=True);fork=c.number;s=MiniSSV(c)
s.register(OWNER,OPS,keys(1,94775000));ref=c.number;c.mine();s.commit(ref);s.update(OWNER,OPS,2048)
ref=c.number;c.mine();d=keys(63,8000);s.register(OWNER,OPS,d);s.commit(ref);s.update(OWNER,OPS,2048);s.remove(OWNER,OPS,d)
c.mine(confirmations);w.step(force=True);assert w.checks['m5'].last_evals[0].status=='confirmed'
height=c.number;c.reorg(fork);c.mine(height-c.number);w.step(force=True)
clock.advance(21601);w.step(force=True)
result={'checkpoint':w.checkpoint(),'status':w.status(),'healthy':json.loads((p/'eb.status.json').read_text())['healthy'],'logs':w.db.conn.execute('SELECT COUNT(*) FROM logs').fetchone()[0], 'incidents':[dict(r) for r in w.db.conn.execute('SELECT id,state,severity,alerts FROM incidents')], 'm5_incidents':[dict(r) for r in w.db.conn.execute('SELECT id,state,kind FROM m5_incidents')], 'evaluations':[dict(r) for r in w.db.conn.execute('SELECT block,status,ref_block FROM evaluations')],'outbox':[dict(r) for r in w.db.conn.execute('SELECT key,kind,severity FROM outbox')]}
print(json.dumps(result,indent=2))
assert not result['logs']
assert not [r for r in result['incidents'] if r['state']=='open'], 'orphan-only cluster is still open and pages again'
