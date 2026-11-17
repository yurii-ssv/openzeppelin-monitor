import json,pathlib,sys
ROOT=pathlib.Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'reconcile'),str(ROOT/'reconcile/tests')]
import pytest
from fakechain import FakeChain,FakeClock,MiniSSV,keys,PROXY
from harness import make_worker,stub_params,VIEWS,outbox
from test_framework import Stub,finding,inbox_line
from test_worker import OWNER,OPS,ev_for,tick,make
from checks.base import Every,OnInbox,OnEvents
from eb_storage import u256
from rpc import RpcError
import eb_model as M

@pytest.mark.parametrize('depth',list(range(1,13))+[13,25])
@pytest.mark.parametrize('confirmations',[0,12])
def test_depth_params_back_and_forth_restart(tmp_path,depth,confirmations):
 c=FakeChain();stub_params(c);c.mine(1+confirmations)
 seen=[]
 class Consumer(Stub):
  def prepare(self,ctx):self.copy=ctx.params.network_fee
  def run(self,ctx,b):
   seen.append((self.copy,ctx.params.network_fee,ctx.reorg_ancestor));return []
 def new():return Consumer(schedule=Every(60))
 _,w=make_worker(tmp_path,[new()],chain=c,views=VIEWS,confirmations=confirmations,start_block=1000,activation_block=1000)
 w.step()
 for _ in range(30):c.mine();w.step()
 stub_params(c,network_fee=71);c.emit_event('NetworkFeeUpdated',[],u256(1)+u256(71));c.mine(1+confirmations);w.step()
 assert seen[-1][:2]==(71,71)
 oldcp=w.checkpoint()['block'];height=c.number;fork=oldcp-depth+1;c.reorg(fork)
 # Explicitly remove orphan fake eth_call timelines before installing canonical replacements.
 for key,rows in c.call_stubs.items():c.call_stubs[key]=[r for r in rows if r[0]<fork]
 stub_params(c,network_fee=9);c.mine(1);stub_params(c,network_fee=3);c.mine(height-c.number)
 w.check_reorg();ancestor=w.checkpoint()['block'];assert ancestor<fork
 assert w.db.meta('reorg:stub')==ancestor
 w.db.conn.close()
 _,w=make_worker(tmp_path,[new()],chain=c,views=VIEWS,confirmations=confirmations,start_block=1000,activation_block=1000)
 w.step();expected=9 if depth==1 else 3;assert seen[-1]==(expected,expected,ancestor)
 assert w.db.meta('reorg:stub') is None and not w.degraded()
 for _ in range(5):w.step()
 assert len(outbox(w,"key LIKE 'health:reorg:%'"))==1


def test_gate_equal_above_order_restart_reinclusion_and_hash_failure(tmp_path):
 c=FakeChain();c.stub_call(PROXY,'0xbc26e7e5',revert='0x12e04c87');c.mine();p=tmp_path/'in';p.write_text('');seen=[]
 def new():return Stub(schedule=OnInbox(),emit=lambda ctx,b:seen.extend((e.tx_hash,e.block,b) for e in ctx.inbox) or [])
 _,w=make_worker(tmp_path,[new()],chain=c,inbox={'path':str(p)})
 w.step();start=w.cfg.start_block
 tx=c.add_tx(OWNER,PROXY,'0xbc26e7e5');c.mine();b=c.sealed
 p.write_text(inbox_line(tx=tx,block=b,block_hash=c.hash(b)))
 w.step();w.step();assert seen==[(tx,b,b)]
 # Future line comes before a mature line: file ordering must hold both.
 tx2=c.add_tx(OWNER,PROXY,'0xbc26e7e5');c.mine();b2=c.sealed
 p.write_text(p.read_text()+inbox_line(tx=tx2,block=b2+2,block_hash=c.hash(b2+2))+inbox_line(tx=tx2,block=b2,block_hash=c.hash(b2)))
 w.step();assert len(seen)==1
 w.db.conn.close();_,w=make_worker(tmp_path,[new()],chain=c,inbox={'path':str(p)},start_block=start,activation_block=start)
 c.mine(2)
 real=w.rpc.block_hashes
 w.rpc.block_hashes=lambda ns: (_ for _ in ()).throw(RpcError('hash unavailable',transport=True))
 for _ in range(20):w.step();assert len(seen)==1 and w.degraded()
 w.rpc.block_hashes=real;w.step();w.step()
 assert seen[1:]==[(tx2,b2+2,b2+2),(tx2,b2,b2+2)]
 assert not w.degraded()
 # Drop a delivered entry's block, append reincluded transaction under the new hash twice.
 c.reorg(b2);c.mine(3)
 p.write_text(p.read_text()+2*inbox_line(tx=tx2,block=b2,block_hash=c.hash(b2)))
 w.step();w.step()
 assert len(seen)==4 and seen[-1][0]==tx2
 assert w.status()['inbox']['orphaned']==2


@pytest.mark.parametrize('confirmations',[0,12])
def test_m5_orphan_only_cluster_incident_removed(tmp_path,confirmations):
 c=FakeChain();c.mine(1+confirmations);_,w=make(tmp_path,c,start_block=1000,activation_block=1000,confirmations=confirmations)
 tick(w);fork=c.number;s=MiniSSV(c)
 s.register(OWNER,OPS,keys(1,94775000));ref=c.number;c.mine();s.commit(ref);s.update(OWNER,OPS,2048)
 ref=c.number;c.mine();d=keys(63,8000);s.register(OWNER,OPS,d);s.commit(ref);s.update(OWNER,OPS,2048);s.remove(OWNER,OPS,d)
 c.mine(confirmations);assert ev_for(tick(w)).status==M.CONFIRMED
 assert w.db.conn.execute("SELECT count(*) FROM incidents WHERE state='open'").fetchone()[0]==1
 height=c.number;c.reorg(fork);c.mine(height-c.number);tick(w)
 rows=[dict(r) for r in w.db.conn.execute('SELECT id,state,opened_block,last_seen_block FROM incidents')]
 assert w.db.conn.execute('SELECT count(*) FROM logs').fetchone()[0]==0
 assert all(r['state']=='resolved' for r in rows), {'checkpoint':w.checkpoint(),'incidents':rows,'evaluations':[dict(r) for r in w.db.conn.execute('SELECT * FROM evaluations')]}
