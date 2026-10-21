FAIL

# CP1B independent QA — attempt 3, after repair 2

Frozen tested head: `e13e3c8ffef600a1bde55a64b3b87a9624bf5735` (repair code `7bf01b4`), QA branch `mtabasco/ms-qa-cp1b-3`. Task `task_7df29cc21b84`, dispatch `ctx_e1df22ba7486`; independent Codex gpt-6.1-sol/high. Measured 2026-10-21. No implementation repair.

## 1. Findings

### HIGH — M5 never revisits a cluster whose entire history was removed by a reorg; orphan confirmed incidents keep paging

Minimal reproducer: save Appendix A as `/tmp/qa-cp1b3/orphan_repro.py`, then run from this worktree:

```sh
PYTHONPYCACHEPREFIX=/tmp/qa-cp1b3/cache-orphan12 reconcile/.venv/bin/python /tmp/qa-cp1b3/orphan_repro.py 12
```

Exit **1**, final assertion `orphan-only cluster is still open and pages again`. The same command with argument `0` also exits **1**. No RPC stubs, omitted logs, or parameter fixture changes are involved: MiniSSV creates real fake-chain storage/logs, then `FakeChain.reorg` removes both on the replacement chain.

Expected: after a confirmed cluster's entire registration/update/removal history is orphaned, the reorg run reconciles its persisted evaluation and both incident records against the replacement chain, closes the unsupported condition, and never sends a new confirmed-condition page for it. Historical sent messages can remain historical. README §6.1 explicitly promises that M5 re-evaluates and rewrites every cluster and resolves keys the new chain no longer supports.

Actual with **12 confirmations** and a **21-block chain reorg** (fork at 1013; old head 1033; checkpoint 1021): all logs are deleted and canonical storage contains no cluster, but `evaluations` retains `status=confirmed`, `block=1021`, `ref_block=1017`. Both `m5_incidents` and framework `incidents` stay open under `EB94775-228316cb1e-1017`; `healthy=true`, lag 0, check errors 0, last full sweep `evaluated=0`. Advancing the clock **21601 seconds** sends another `severity=page`, `kind=realert`, and increments `incidents.alerts` to **2**. With zero confirmations the same defect appears after a 9-block reorg at checkpoint 1009.

Cause measured against source: `M5EbDesync.on_reorg` only adds `"*"` to `dirty`; `run` expands this from `ctx.db.clusters(...)`, which enumerates surviving logs. With all cluster logs gone, no evaluation reaches `_incident`, and the orphan `evaluations`, `m5_incidents`, and framework incident are never reconciled. A forced full sweep, later steps, and the 6-hour cadence do not repair this. This falsifies the repair-2 audit's persisted-M5-state/open-incidents claims, despite the repository's green M5 reorg tests (which retain older cluster history).

No other blocking finding was reproduced. Attempts 1 and 2's original findings are fixed in the probes below; the new finding keeps CP1B unverified.

## 2. Claims I could NOT confirm

- **Every piece of cross-block state is handled, M5 rewrites every cluster, and orphan incidents are re-evaluated:** false for an orphan-only cluster (finding 1). The regression files cover reorging a later removal, not deleting the cluster's whole history.
- **Attempt-2 Appendix B passes unmodified:** I reproduced **13 passed / 2 failed**. Both failures use a deliberately synthetic `0xcdcd…` inbox hash and expect it delivered. Under the coordinator-approved GATE this is correctly orphaned. Changing only that fixture to `block_hash=chain.hash(chain.sealed)` gives **15 passed**, with all assertions unchanged. This is an approved contract/fixture change, not a new implementation failure.
- **Old-head regression count “14 failed, 18 passed”:** I independently extracted `bea73fd` and copied only the two new regression files; measured **14 failed, 19 passed** (33 cases), with the claimed 14 red tests all present. The worker's 18 appears to be an arithmetic/reporting error. Its 102-current-tests claim reproduces exactly.
- Historical implementation-session state, privately reported scratch hashes and timing were not adopted as QA evidence. All current V rows were remeasured, including both LIVE rows; no required row is NOT RUN after the network-allowed retries.

Preflight: HEAD equals the dispatch exactly; initial porcelain empty; free disk **353 GiB**. Fresh venv absent, created with `uv venv reconcile/.venv --python 3.12`, then `uv pip install --python reconcile/.venv/bin/python -r reconcile/requirements-dev.txt`: both **0**, Python **3.12.15**, 7 declared dev packages installed, no other installs. `PATH=$HOME/.foundry/bin:$PATH`; anvil **1.8.5** available. No secrets used, all notifications captured/recorded, all transactions restricted to the local fork.

## 3. Verification matrix

All pytest commands below also used `-p no:cacheprovider`; fresh `PYTHONPYCACHEPREFIX` directories were used for mutation and old/new runs. `P` below is exactly `reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider`, `F` is `reconcile/tests/test_framework.py`. Commands were run from the frozen QA worktree unless a scratch cwd is explicitly named.

| V-ID | Exact command / measurement | Exit and result |
|---|---|---|
| V1B.1 | `P reconcile/tests -rs` | **0**, **102 passed, 0 skipped** with networking allowed. Initial sandbox run: **1**, 101 passed / metrics test failed at `socket.bind`, `PermissionError: [Errno 1] Operation not permitted`; cleared environment, no code change. Base V0.9 count was 30. |
| V1B.2 | `reconcile/.venv/bin/ruff check reconcile` | **0**, All checks passed. Scope of new Ruff exemption independently inspected below. |
| V1B.3 | `P F -k scheduler_dispatches_registered_check` | **0**, 1 passed / 31 deselected; interval and matching-event dispatch assertions exercised. |
| V1B.4 | `P F -k restart_idempotent` | **0**, 2 passed / 30 deselected. Own nonzero-state/restart test also green. |
| V1B.4f | Appendix D mutation, `P F -k restart_idempotent` in frozen scratch copy | **1**, 1 failed / 1 passed; byte restoration then **0**, 2 passed. |
| V1B.5 | `P F -k routing` | **0**, 4 passed / 28 deselected; page/warn Telegram, info/digest Discord, record JSONL and transport behavior. Unknown severity fails closed in independent probes. |
| V1B.6 | `P reconcile/tests -k m5`; original names/assertions/decorators compared to `4fbb9f1` | **0**, 33 passed / 69 deselected; all **26 original function names**, **58 original assertion ASTs** preserved under the severity-label/table-move normalization; one assertion added; decorator/parameterization ASTs identical. **Additional M5 reorg falsifier FAILS** (finding 1); repository green does not discharge it. |
| V1B.7 | `P F -k inbox` | **0**, 6 passed / 26 deselected; parsing, offset, shrink, selector decode. Gate/repair edges measured separately below. |
| V1B.7f | Appendix D selector mutation, `P F -k inbox` in frozen scratch copy | **1**, 2 failed / 4 passed; byte restoration then **0**, 6 passed. |
| V1B.8 | `reconcile/.venv/bin/python reconcile/tests/fake_once.py --port 30467 --hold 8`; concurrent `curl --max-time 1 -s http://127.0.0.1:30467/metrics` | server **0**, curl **0** with networking allowed. Required series: checkpoint **1011**, lag **0**, open incidents `{check="m5",severity="page"} 1`, undelivered **0**, per-check last-run seconds present; input-unavailable 0/0, inbox held/orphaned 0/0. Same metrics bind was blocked in the initial V1B.1 sandbox run. |
| V1B.9 | `MAINNET_RPC_URL=https://ethereum-rpc.publicnode.com reconcile/.venv/bin/python reconcile/inventory.py --probe --rpc https://ethereum-rpc.publicnode.com` | **LIVE 0**, **1908 API rows**, 1026 unmigrated, **25/25 tuples validated**, 9 ETH / 16 legacy SSV / 0 invalid / 68 operators, block **26240967**. Original sandbox direct-script run **1**, `URLError: <urlopen error [Errno 8] nodename nor servname provided, or not known>`. The matrix's literal `python -m reconcile.inventory` also fails import `ModuleNotFoundError: No module named 'eb_storage'`; used the explicitly allowed equivalent direct-script CLI. |
| V1B.10 | `export PATH=$HOME/.foundry/bin:$PATH`; `FORK_RPC_URL=https://ethereum-rpc.publicnode.com PYTHONUNBUFFERED=1 reconcile/.venv/bin/python reconcile/scripts/fork_e2e.py` | **LIVE 0**, fork **26240977**, **5 PASS** stages. Original sandbox Python raised `URLError: <urlopen error [Errno 1] Operation not permitted>`; the initial log-printing shell wrapper returned 0 because `cat` was last, and that wrapper success was **not credited**. Network-allowed direct command passed; anvil stopped (`curl --max-time 2 -s http://127.0.0.1:8547` **7**). |
| V1B.11 | Read `reconcile/checks/README.md` alone as CP2 framework packet input | **PASS for packet construction**: interface, Ctx and pinned RPC methods, schedules/cursors, registration/import ownership, defaults/config validation, own SQL/state, findings/lifecycle/routing, live-param and inbox failure rules, reorg duties, fake-chain/harness helpers and V2 patterns present. It suffices for the framework part of CP2 packets, whose monitor semantics still come from each lane's required spec. Its assertion that M5 obeys the reorg duties is falsified by finding 1. |

LIVE M5 details: clean **640000/640000**; dummy registration clean **1270000/1270000**; consumed root latent **1270000 reference / 640000 observed / 630000 discrepancy**, no page; removal confirmed **640000/10000/630000**, **63** matched keys, one **page → Telegram**, receipt `recorded`; clean next root sends **info resolution → same Telegram route**, incident resolved. This matches Marco's first-confirmed-round decision. Live parameter values were read and the layout guard passed. Inspected current config/defaults/compose and notifier defaults for `MAINNET_RPC_URL`, `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`, `DISCORD_WEBHOOK_INFO`; did not read `.env*` or live `config/`.

### Prior-report and test-integrity measurements

Appendix C extracts Python code blocks from the prior reports with only ROOT changed to this worktree. Commands/results:

- `P /tmp/qa-cp1b3/test_qa1_0.py /tmp/qa-cp1b3/test_qa2_1.py`: **1**, **25 passed / 2 failed**, no skips (12 attempt-1 probes pass; attempt-2 B is 13 pass / 2 synthetic-hash failures as described). External-root pytest reports a harmless unregistered `m5` marker warning; repository suite has no skips.
- `reconcile/.venv/bin/python /tmp/qa-cp1b3/test_qa2_0.py`: **0**, attempt-2 Appendix A direct/cached values both **[42960, 50000, 42960, 42960, 42960]**; canonical getter **42960**, params ok and healthy true.
- `P /tmp/qa-cp1b3/test_qa2_corrected.py`: **0**, **15 passed**, fixture-only canonical hash change. Covers legacy severity/sent/double migration, partial late getter revert/empty result, invalidation/cursor rollback/recovery, actual missing/empty/directory/EACCES inbox and sustained/flapping health.
- `git diff bea73fd HEAD -- reconcile/tests/test_framework.py reconcile/ruff.toml`: only the inbox fixture changed (`chain.number` plus synthetic hash → sealed transaction block plus its canonical hash); **all 99 existing assertion ASTs identical**. No assertion removed or weakened. The two new regression files are additions.
- Ruff selects remain `E4,E7,E9,F,I`; the sole new exemption is **E701/E702 for new `tests/test_qa2_regressions.py` only**. `reconcile/.venv/bin/ruff check --isolated --select E701,E702 reconcile/tests/test_qa2_regressions.py --output-format concise`: **1**, **60** one-line/semicolon style diagnostics. These match the verbatim QA probe style. No production file or substantive error rule was exempted; the change does not hide an assertion removal or implementation defect.
- From `/tmp/qa-cp1b3/old`, run this worktree's absolute venv Python with `-m pytest -q -p no:cacheprovider reconcile/tests/test_reorg_state.py reconcile/tests/test_qa2_regressions.py --tb=no`: **1**, **14 failed / 19 passed**, no skips. Only those two new files were copied to extracted `bea73fd`; implementation remained old. All 14 red names appear in Appendix E. Current-head versions are included in the independently green 102-test run. Some old-head reds exercise absent new interface/schema (`reorg_ancestor`, `held`, `canonical`) in addition to behavioral assertions; the parameter, race, shorter-chain, ID, outbox, orphan-delivery and hash-failure reds demonstrate the old behavior too.

## 4. Falsifiers

Required mutations were performed in `/tmp/qa-cp1b3/mutation`, a tracked-files-only extraction of the exact frozen head, excluding `.env*`. Appendix D is the complete runner. Every mutated file was restored byte-for-byte in `finally`, asserted equal before the restored run, and rerun with a fresh bytecode cache. The real QA worktree was never source-mutated; porcelain and frozen-base diff were empty before evidence writes.

| Falsifier | Measured outcome |
|---|---|
| V1B.4f drop check from incident join | RED **1 failed / 1 passed**, collision assertion sees only ca instead of ca+cb; restored GREEN **2 passed**. |
| V1B.7f selector `0x12e04c87` → `0x12e04c88` | RED **2 failed / 4 passed**, decode and keccak assertions; restored GREEN **6 passed**. |
| Same-block ordering, reversed RPC logs; M5 update then remove in same block | attempt-1 Appendix A **2 PASS**, ordered positions and confirmed 630000 shortfall / 63 matches. |
| Restart with prior nonzero state | attempt-1 Appendix A **PASS**, counter progresses 1→2 while one outbox row remains. |
| Missing input treated as clean | attempt-1 and attempt-2 probes **PASS** for missing inbox and failed params. Actual EACCES and directory also unhealthy; an existing empty file is valid/healthy. |
| Empty RPC result read as no logs | attempt-1 probes **4 PASS**, None/empty string/object/`0x` all raise `RpcError(non-list)`. Empty late parameter getter also invalidates all values (attempt-2 corrected probes). |
| Two checks collide on one key | independent restart probe **PASS**, 2 incidents / 2 rows; required mutation proves the gate reddens. |
| Unnamed severity routing | **PASS**, `Finding(severity='emergency')` raises ValueError; direct enqueue raises KeyError and rolls back, no outbox row. |
| Reorg depths 1…12, 13, 25; confirmations 0 and 12; back-and-forth params; crash after rewind before handling | Appendix B **28 PASS**, one case per depth/config pair. Fresh worker consumes durable reorg marker, direct and prepare-cached values match canonical replacement, no stale params, one reorg health across repeated steps. At depth 1 the second replacement value is above checkpoint, so expected pinned value is 9; others read 3. |
| Inbox equality/above-checkpoint/file-order/restart/hash failure/reinclusion | Appendix B **PASS**, equality delivered once; future entry holds later mature line; 20 hash-failure steps unhealthy with no deliveries; recovery delivers once; orphan hashes marked 0 and repeated re-inclusion line deduplicated. |
| Whole M5 cluster history orphaned (confirmations 0 and 12) | Appendix B **2 FAIL**; Appendix A also proves 6-hour phantom page (finding 1). |
| Shorter replacement chain, durable lowest-ancestor notice, event cursor replay, M5 removal reorg, scan race, incident-ID/outbox-key reuse, reorg deeper than search | repository `test_reorg_state.py` included in **102 PASS**, independently executed, plus independent depth/restart probes. A sent historical alert remains historical by design; new ID/suffix sends are preserved. |
| Health flapping and alert storms | attempt-2 corrected probes **PASS**: stable down/restart does not emit per-step messages, next health window re-alerts, genuinely new inbox outages each get one; params flaps dedup within window. New M5 orphan page persists at cadence (finding 1), not a retry storm. |

Own-probe command: `PYTHONPYCACHEPREFIX=/tmp/qa-cp1b3/cache-independent2 reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider /tmp/qa-cp1b3/test_independent3.py --tb=short`: final source in Appendix B, **1**, **29 passed / 2 failed**, no skips. An earlier draft incorrectly expected the second replacement getter while it was still above the checkpoint in two depth-1 cases; a direct timeline inspection showed the correct pinned value was 9. Corrected that QA expectation before the final run; neither was reported as a product defect.

## 5. Evidence-plane writes

CP1B **Status:** IMPLEMENTED — AWAITING QA → **QA FAILED**, anchored to `e13e3c8ffef600a1bde55a64b3b87a9624bf5735` and this report. Wrote only `plans/monitoring-service/qa/cp1b-attempt-3.md`, the CP1B Status line in `plans/monitoring-service/plan.md`, and one appended event in `plans/monitoring-service/execution-log.md`. The ignored local venv and external scratch were authorized. Attempts 1 and 2, implementation, config and all `.env*` remain unchanged. No repairs, production sends, mainnet transactions, push or CP2 work. Coordinator owns fresh repair and fresh QA; CP2 stays gated.

## 6. Repo proof

QA lane base is the frozen tested head `e13e3c8ffef600a1bde55a64b3b87a9624bf5735`. Before evidence writes, `git status --porcelain` and `git diff --name-only e13e3c8ffef600a1bde55a64b3b87a9624bf5735` printed nothing. The implementation base `4fbb9f1` necessarily includes implementer changes and is not the attribution base for QA.

Final staging outputs (measured before commit):

```text
$ git status --porcelain
M  plans/monitoring-service/execution-log.md
M  plans/monitoring-service/plan.md
A  plans/monitoring-service/qa/cp1b-attempt-3.md
$ git diff --name-only e13e3c8ffef600a1bde55a64b3b87a9624bf5735
plans/monitoring-service/execution-log.md
plans/monitoring-service/plan.md
plans/monitoring-service/qa/cp1b-attempt-3.md
```

No implementation file appears in either QA output. After the local evidence commit, porcelain is empty and the frozen-base path list is the same three authorized evidence paths.

## Appendix A — minimal new finding reproducer

Save as `/tmp/qa-cp1b3/orphan_repro.py`; ROOT is this worktree. Invoke argument `0` or `12` as above. Assertions deliberately fail on the tested head.

```python
import pathlib,sys,json,tempfile
ROOT=pathlib.Path('/Users/marco/orca/workspaces/ssv-network-monitor/ms-qa-cp1b-3')
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
```

## Appendix B — independent depth and gate probes

Save as `/tmp/qa-cp1b3/test_independent3.py` and run the §4 command.

```python
import json,pathlib,sys
ROOT=pathlib.Path('/Users/marco/orca/workspaces/ssv-network-monitor/ms-qa-cp1b-3')
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
```

## Appendix C — reproduce prior-report extraction and old-head control

Run from the QA worktree; no .env files are extracted. The prior reports remain unchanged.

```python
import pathlib,re,subprocess
root=pathlib.Path.cwd();s=pathlib.Path('/tmp/qa-cp1b3');s.mkdir(exist_ok=True)
for report,prefix in [('cp1b-attempt-1.md','qa1'),('cp1b-attempt-2.md','qa2')]:
    doc=(root/'plans/monitoring-service/qa'/report).read_text()
    for i,code in enumerate(re.findall(r'```python\n(.*?)```',doc,re.S)):
        code=re.sub(r"ROOT\s*=\s*pathlib.Path\('[^']+'\)",f'ROOT = pathlib.Path({str(root)!r})',code)
        (s/f'test_{prefix}_{i}.py').write_text(code)
p=s/'test_qa2_1.py'
code=p.read_text()
before='chain.mine();path.write_text(inbox_line(tx=tx,block=chain.sealed))'
after='chain.mine();path.write_text(inbox_line(tx=tx,block=chain.sealed,block_hash=chain.hash(chain.sealed)))'
assert code.count(before)==1
(s/'test_qa2_corrected.py').write_text(code.replace(before,after))
for directory,rev in [('old','bea73fd'),('mutation','e13e3c8ffef600a1bde55a64b3b87a9624bf5735')]:
    target=s/directory
    for f in subprocess.check_output(['git','ls-tree','-r','--name-only',rev,'reconcile'],text=True).splitlines():
        if any(part.startswith('.env') for part in pathlib.Path(f).parts):continue
        p=target/f;p.parent.mkdir(parents=True,exist_ok=True)
        p.write_bytes(subprocess.check_output(['git','show',rev+':'+f]))
for name in ['test_reorg_state.py','test_qa2_regressions.py']:
    (s/'old/reconcile/tests'/name).write_bytes((root/'reconcile/tests'/name).read_bytes())
```

M5 assertion/name comparison: `reconcile/.venv/bin/python /tmp/qa-cp1b3/test_qa1_1.py`, **0**. This is the unchanged comparator from attempt 1 Appendix B / attempt 2 Appendix D. It prints all 26 original names and **58 preserved / 1 added** assertions; allows only critical→page, warning→warn and incidents→m5_incidents in SQL. Also compared every original decorator AST (identical) and manually read `git diff 4fbb9f1 HEAD -- reconcile/tests/test_worker.py` including the helpers. No numeric assertion, benign assertion, original scenario or original parameterization was lost.

## Appendix D — required mutation runner

Save as `/tmp/qa-cp1b3/falsify.py`, after Appendix C's frozen extraction; run `reconcile/.venv/bin/python /tmp/qa-cp1b3/falsify.py`. Runner exits **0** only if both expected reds, byte restorations and restored greens match.

```python
import subprocess,pathlib,os
root=pathlib.Path('/Users/marco/orca/workspaces/ssv-network-monitor/ms-qa-cp1b-3');scratch=pathlib.Path('/tmp/qa-cp1b3/mutation');python=str(root/'reconcile/.venv/bin/python')
for name,file,old,new,selection in [('V1B.4f','alerts.py','return ":".join((check, key))','return ":".join((key,))','restart_idempotent'),('V1B.7f','inbox.py','"0x12e04c87"','"0x12e04c88"','inbox')]:
 p=scratch/'reconcile'/file;original=p.read_bytes();assert old.encode() in original
 try:
  p.write_bytes(original.replace(old.encode(),new.encode(),1))
  env={**os.environ,'PYTHONPYCACHEPREFIX':f'/tmp/qa-cp1b3/cache-{name}-red'}
  r=subprocess.run([python,'-m','pytest','-q','-p','no:cacheprovider','reconcile/tests/test_framework.py','-k',selection],cwd=scratch,env=env,capture_output=True,text=True)
  print(name,'RED exit',r.returncode);print(r.stdout);assert r.returncode==1
 finally:p.write_bytes(original)
 assert p.read_bytes()==original
 env={**os.environ,'PYTHONPYCACHEPREFIX':f'/tmp/qa-cp1b3/cache-{name}-green'}
 r=subprocess.run([python,'-m','pytest','-q','-p','no:cacheprovider','reconcile/tests/test_framework.py','-k',selection],cwd=scratch,env=env,capture_output=True,text=True)
 print(name,'RESTORED byte-identical GREEN exit',r.returncode);print(r.stdout);assert r.returncode==0
```

## Appendix E — old-head regression control output

```text
FF...FFFFFFFFFF................FF                                        [100%]
=========================== short test summary info ============================
FAILED reconcile/tests/test_reorg_state.py::test_reorg_params_reread_failure_blocks_consumers_until_it_succeeds
FAILED reconcile/tests/test_reorg_state.py::test_reorg_depth_n_rereads_params_and_every_prepare_copy
FAILED reconcile/tests/test_reorg_state.py::test_scan_rejects_logs_of_a_block_reorganised_during_the_scan
FAILED reconcile/tests/test_reorg_state.py::test_reorg_marker_survives_a_restart_and_gives_the_lowest_ancestor
FAILED reconcile/tests/test_reorg_state.py::test_near_set_check_runs_after_a_reorg_onto_a_shorter_chain
FAILED reconcile/tests/test_reorg_state.py::test_default_incident_id_reused_by_a_reorganised_block_gets_a_suffix
FAILED reconcile/tests/test_reorg_state.py::test_escalation_replayed_on_a_reorganised_block_is_sent_again
FAILED reconcile/tests/test_reorg_state.py::test_inbox_entry_above_the_checkpoint_waits_then_is_delivered_once
FAILED reconcile/tests/test_reorg_state.py::test_inbox_entry_of_an_orphaned_block_is_never_delivered_and_its_reinclusion_is
FAILED reconcile/tests/test_reorg_state.py::test_inbox_hash_check_is_one_batch
FAILED reconcile/tests/test_reorg_state.py::test_inbox_hash_check_failure_holds_entries_and_degrades_health
FAILED reconcile/tests/test_reorg_state.py::test_reorg_below_the_checkpoint_rechecks_undelivered_inbox_entries
FAILED reconcile/tests/test_qa2_regressions.py::test_reorged_parameter_event_never_leaves_orphan_threshold
FAILED reconcile/tests/test_qa2_regressions.py::test_qa2_appendix_a_reorg_repro
14 failed, 19 passed in 0.36s
```
