PASS WITH NOTES

# CP1B independent QA — attempt 4, after repair 3

Observed 2026-10-21; Codex gpt-6.1-sol / high as requested (coordinator owns launch.effective receipt). Task `task_c0ed11077a8a`, dispatch `ctx_b15e6904e62e`; branch `mtabasco/ms-qa-cp1b-4`. Frozen tested HEAD **b07d2c637fc66ce45b00f867b33085eaa3f3218c**, repair code `9101624`; pre-repair control `e13e3c8ffef600a1bde55a64b3b87a9624bf5735`; original implementation base `4fbb9f1`.

## 1. Findings

**No blocking finding.** All owned V1B rows pass after clearing sandbox faults; all prior Appendix A probes pass. The attempt-3 orphan-only M5 defect is independently cleared with confirmations 0 and 12, the committed sweep, and a separate sweep with different seeds, shapes and an independent storage-journal oracle.

**Informational — the approved reorg rule is conditional on check completeness, rather than a runtime proof of orphaned evidence.** Minimal command: `reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider /tmp/qa-cp1b4/test_contract_edges.py -k health_then_empty` (Appendix B); exit 0, 1 passed. A deliberately noncompliant check returns `ctx.health(...)` followed by `[]` in a reorg run although its finding survives below the ancestor. Expected under README §6.2 / the coordinator's approved rule: close its reorg key, send an info resolution, clear the marker; a later finding opens and pages a new unique incident. Actual: exactly that, including `healthy: true` after the empty run. Thus a CP2 check **can misuse this silently** with respect to status, and the resolution's claim that evidence left the chain need not be literally true for a noncompliant check. This is the explicitly documented `[]` behavior, also exercised by the implementer's `Flag(naive=True)` test and coordinator condition 2; it is not a deviation from the approved contract. CP2 packets must carry the complete-re-evaluation / raise-or-re-report obligation and test it, including their own persisted tables and inbox-derived keys. Calling `ctx.health` alone does not mark a run incomplete.

**Informational — approved interface amendment is still recorded in README §6.2 rather than mirrored in plan §6.** Exact check: `sed -n '104,123p' plans/monitoring-service/plan.md` and `sed -n '324,331p' reconcile/checks/README.md`. The plan's old lifecycle sentence remains, while the authoritative README contains the approved exception and Ctx additions. The dispatch explicitly supplies that approval; QA accepts it as authority and edits only the allowed CP1B Status line. Coordinator should mirror its approved text when integrating CP2 packets.

Framework judgment: the rule is sound **for compliant checks**: only that check's open keys last seen above the lowest durable ancestor are reconsidered; surviving findings retain their identity, omitted keys resolve once, errors roll back and retain the marker, re-alerts wait while pending, and health is false while pending. It cannot prove a check evaluated all necessary evidence. Outside reorg runs, default absence still never resolves (independent repeated-run test; the declared `resolve_on_absence=True` opt-in remains). Crash/restart after rewind and after rescan stays safe, nested reorgs preserve the lowest ancestor, failed writes roll back, and a reappearing condition after reorg resolution gets a fresh alert. UNKNOWN's documented re-report path preserves an incident while uncertainty remains; this is not a claim that UNKNOWN proves current exploitation.

Queued notifications are historical decisions: the committed suite explicitly verifies that an unsent pre-reorg alert is delivered in order before its info resolution. The no-orphan-page invariant below concerns **newly enqueued** page/warn decisions after reorg detection, not deleting historical outbox rows. Production delivery was never enabled.

## 2. Claims I could NOT confirm

No required V1B claim remains unmeasured. I ran all rows, both LIVE rows, the required red/green mutations, all earlier Appendix A probes, earlier Appendix B probes, both sweeps and old-head controls. I reproduced the 69-red / 21-green committed sweep on e13e3c8 and measured 19-red / 29-green for the final 48-case new regression selection (the implementer's earlier 18-red number preceded its additional digest regression). My own 48 cases all fail on e13e3c8 and all pass on the frozen head.

The unmodified attempt-2 Appendix B does **not** give 15 green: it gives 13 passed / 2 failed because its synthetic `0xcd…` inbox hash is orphaned under the approved confirmation GATE. A copy changing only `block_hash=chain.hash(chain.sealed)` at that fixture gives 15 passed, with every assertion unchanged. Both results are recorded, not counted as a new code failure.

Not claimed: real Telegram/Discord receipts, a production soak, Docker image build, deployment, or exhaustive correctness for arbitrary future CP2 checks. Record mode/local captures only; public RPC reads and local anvil-fork transactions only. No secrets or user config files were read or written. No unchanged expensive row was skipped: each LIVE row ran once successfully after its sandbox attempt; the 240-case full suite ran once, with targeted checks and independent probes afterward. I did not rerun the full suite after evidence-only documentation writes or repeat the unchanged successful LIVE rows.

## 3. Verification matrix

Preflight: `git rev-parse HEAD` exactly matched frozen head; `git status --porcelain` empty; `df -h .` showed 353 GiB available. Authorized setup `uv venv reconcile/.venv --python 3.12` and `uv pip install --python reconcile/.venv/bin/python -r reconcile/requirements-dev.txt` both exit 0. Python 3.12.15, pytest 9.1.1, Ruff 0.17.1, pycryptodome 3.24.0; anvil 1.8.5 (`~/.foundry/bin/anvil`). No other installs. Read QA contract, required plan sections, required spec/precedent sections, implementer records, and named deliverables. Commands executed separately; no gate hidden behind `&&`.

In this report `PY = reconcile/.venv/bin/python`, `S = /tmp/qa-cp1b4`, and `FW = PY -m pytest -q -p no:cacheprovider reconcile/tests/test_framework.py`; these are notation, not shell variables. Scratch scripts and full outputs were outside the repo; durable reproduction code follows. All pytest selections have 0 skips.

| ID | Exact command (notation expanded as above) | Exit / measured outcome |
|---|---|---|
| V1B.1 | `PY -m pytest -q -p no:cacheprovider reconcile/tests -rs` | **0; 240 passed, 0 skipped, 11.51 s**. Includes all 90 committed sweep cases, all 15 re-find tests, both QA3 A cases, 31 QA3 B cases and the prior 102 cases. |
| V1B.2 | `reconcile/.venv/bin/ruff check reconcile` | **0**, All checks passed. Ignore expansion restricted to the two verified verbatim QA3 files; details below. |
| V1B.3 | `FW -k scheduler_dispatches_registered_check` | **0; 1 passed, 31 deselected**. Initial, interval and matching-event dispatch. |
| V1B.4 | `FW -k restart_idempotent` | **0; 2 passed, 30 deselected**. Same key across checks and restart. Independent nonzero-state restart also passes. |
| V1B.4f | `PY S/mutate.py` on isolated `git archive HEAD reconcile` copy; drop check from `incident_key` | Mutation pytest **1; 1 failed / 1 passed**, two checks collide. Restored byte-identical pytest **0; 2 passed**. Harness 0. |
| V1B.5 | `FW -k routing` | **0; 4 passed, 28 deselected**. Page/warn/health → Telegram, info/digest → Discord, record JSON lines. Unknown severity rejected independently. |
| V1B.6 | `PY -m pytest -q -p no:cacheprovider reconcile/tests -k m5` | **0; 41 passed, 199 deselected**. Original 26 names and all 58 original assertions preserved after documented port-label/table renames; source diff and AST checked, one assertion added. Full original list in Appendix C. |
| V1B.7 | `FW -k inbox` | **0; 6 passed, 26 deselected**. Parsing, persistent offsets, shrink/reread, deduplication, selector, fallback. Missing input health and confirmation/reorg gates also measured independently. |
| V1B.7f | `PY S/mutate.py`; `0x12e04c87` → `0x12e04c88` in `inbox.py` isolated copy | Mutation pytest **1; 2 failed / 4 passed**; restored byte-identical pytest **0; 6 passed**. Harness 0; fresh PYTHONPYCACHEPREFIX for each red/green run. |
| V1B.8 | `PY reconcile/tests/fake_once.py --port 31464 --hold 8`; `curl --max-time 1 -s http://127.0.0.1:31464/metrics` (`PY S/metrics_probe.py` starts/fetches/waits) | Initial sandbox server **1**, `PermissionError: [Errno 1] Operation not permitted` at bind; initial curl **7**. Network/bind-allowed probe: **server 0 / curl 0**. checkpoint **1011**, lag **0**, m5/page open **1**, undelivered **0**, check-last-run seconds **1792587989.525805**, check errors **0**, unavailable params/inbox **0/0**. All five required series present. |
| V1B.9 | `PY reconcile/inventory.py --probe --rpc https://ethereum-rpc.publicnode.com` | Initial sandbox **1**: `urllib.error.URLError: <urlopen error [Errno 8] nodename nor servname provided, or not known>`. Allowed-network retry **0**, LIVE block **26,241,138**, **1,908 API rows**, **1,026 migrated=false**, **25/25 tuples validated**, ETH **9** / SSV **16**, invalid/absent **0/0**, **68 operators**. F10 independently remeasured, inventory unchanged but not assumed. |
| V1B.10 | `export PATH=$HOME/.foundry/bin:$PATH` then `FORK_RPC_URL=https://ethereum-rpc.publicnode.com PYTHONUNBUFFERED=1 PY reconcile/scripts/fork_e2e.py` | Sandbox **1**: `urllib.error.URLError: <urlopen error [Errno 1] Operation not permitted>`. Allowed-network retry **0**, LIVE fork **26,241,164**, **PASS ×5**: clean 640000/640000; clean 1270000/1270000; latent 1270000/640000/630000; confirmed 640000/10000/630000, 63 matches, first confirmed **page → telegram / recorded**; clean next root, **info resolution → telegram / recorded**. Live params read; layout guard ok. Allowed-network `curl --max-time 2 -s http://127.0.0.1:8547` **7** after script: anvil stopped. |
| V1B.11 | Read `reconcile/checks/README.md` in full; implement Appendix A/B using its described interface/helpers | **PASS (inspection, no process exit)**. It supplies class/registration ownership, schedules, all Ctx fields, parameter failures, inventory, inbox schema/gates, config defaults/schema, transaction/reorg obligations, own-table schema, helpers and V2x test patterns. Sufficient to build CP2 framework packets from this document; each monitor's spec/decisions remain its separate required domain input. Include §6.2's obligations verbatim in those packets. |

Coordinator decisions: M5 first confirmed active round pages (repository PoC and LIVE fork); no warn-then-page drift. Tracked example config, notifier defaults and compose all name **MAINNET_RPC_URL, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, DISCORD_WEBHOOK_INFO**. No `.env*`, `config/` or `config.zip` inspected or changed.

### Prior probes and negative controls

| Command | Exit / measured result |
|---|---|
| `PY -m pytest -q -p no:cacheprovider S/qa1_A.py S/qa3_B.py` | **0; 43 passed** = attempt-1 Appendix A 12 + attempt-3 Appendix B 31. Only ROOT changed in extracted report code. Scratch collection emits one harmless unregistered m5 marker warning. |
| `PY S/qa2_A.py` | **0**; direct and prepare-cached consumers both `[42960,50000,42960,42960,42960]`; canonical 42960, params.ok true, healthy true. |
| `PY S/qa3_A.py 0`; `PY S/qa3_A.py 12` | **0 / 0**; no logs/evaluations/open incidents after whole-history reorg; info resolution, no orphan re-page after +21601 s. |
| `PY -m pytest -q -p no:cacheprovider S/qa2_B.py` | **1; 13 passed / 2 failed**, both synthetic-hash partial reload cases, expected incompatibility with approved GATE. |
| `PY -m pytest -q -p no:cacheprovider S/qa2_B_canonical.py` | **0; 15 passed**; only add canonical `block_hash` at the single fixture line, all assertions unchanged. |
| `PY -m pytest -q -p no:cacheprovider S/test_adversarial.py` | **0; 48 passed** in 1.73 s; independently designed seeds/shapes/invariants (Appendix A). |
| `PY -m pytest -q -p no:cacheprovider S/test_contract_edges.py` | **0; 2 passed** (Appendix B). Default absence preserves; deliberate contract violation closes and later reopens/pages once. |
| `PY -m pytest -q -p no:cacheprovider S/old/reconcile/tests/test_qa3_regressions.py S/old/reconcile/tests/test_qa3_appendix_b.py S/old/reconcile/tests/test_reorg_refind.py` | **1; 19 failed / 29 passed** on e13e3c8 scratch copy with only new tests copied in. Includes behavioural M5 orphan failures; some stub tests also expose absent ctx.reorg_keys, not all failures are independent behavioural proofs. |
| `PY -m pytest -q -p no:cacheprovider S/old/reconcile/tests/test_reorg_sweep.py` | **1; 69 failed / 21 passed** on e13e3c8. Only test adaptation: `ctx.reorg_keys` → `getattr(ctx, "reorg_keys", frozenset())` so the absent new field cannot itself account for reds. Matches implementer claim. |
| `PYTHONPYCACHEPREFIX=S/cache-own-old PY -m pytest -q -p no:cacheprovider S/test_adversarial_old.py` | **1; 48 failed** on e13e3c8, only ROOT changed. The 36 framework cases detect healthy=true during pending failed reorg; 12 M5 cases detect open orphan incidents. Green head uses identical assertions. |
| `PY S/qa1_B.py`; `PY S/integrity.py`; `git diff 4fbb9f1 HEAD -- reconcile/tests/test_worker.py` | **0 / 0 / 0**. Names, assertions, raw port diff and repair-3 integrity checked (Appendix C). |

The committed 90-case sweep was measured as part of V1B.1; I did not redundantly rerun that unchanged file on the green head. The old-head sweep was run separately as the required falsifiable control.

### Independent sweep design and limits

My **36 framework cases** use seeds **811–822**, confirmations **0/1/7** (different from committed sweep), seven independently stored keys, arbitrary replacement slot values, two sequential/nested reorgs while handling is pending, two failures after a persistent write, +21601 s, and process reconstruction after rewind/rescan. The oracle reads the fake chain's write journal directly at the checkpoint, rather than instantiating another Worker. After the committing reorg run, open keys must equal that oracle; each surviving page incident must have a delivered page; every newly enqueued post-reorg page must have canonical support; failed transactions retain markers and roll back state; healthy is false pending and true after success; repeated 25 runs cannot add any message; the removed key's return must open one new uniquely identified page, and restart cannot duplicate it.

My **12 M5 cases**, seeds **91–102**, vary old-branch tail length, delete the entire history of a confirmed cluster, crash after committed rewind, wait over 6 h, verify all three persisted planes clear, then recreate the same cluster identity with new key sets and confirm it again. They require one fresh delivered page, unique incident id, matching M5/framework tables, and no storm over 20 repeated runs. These supplement, rather than duplicate, the committed survivor/latent/inbox/parameter sweep. Invariants on pending failed runs allow an explicitly unhealthy open incident to await evaluation, with its re-alert deferred; after success, no open unsupported key remains. The tests are not a proof over every future check.

I also explored a proposed same-root “shortfall growth on extra partial removals” probe (`S/growth_repro.py`); its assertion was invalid, because this sequence's shortfall stays **630000** at all three stages (extra removals reduce reference and stored together). Its exit 1 is a rejected QA hypothesis, not a product defect; I did not change code or expectations to count it as a passing case. A first scratch contract-edge run used the wrong default fixture key (`cluster` rather than `k1`); correcting that test-data name produced the 2-pass result above. No failing code probe was hidden by either correction.

## 4. Falsifiers

| Probe | Red / green and restoration evidence |
|---|---|
| V1B.4f remove check namespace | Isolated HEAD copy mutated `return ":".join((check, key))` → `return ":".join((key,))`; **1 failed / 1 passed**, then original bytes restored in finally, compare identical, **2 passed**. |
| V1B.7f selector hex digit | Isolated copy mutated `"0x12e04c87"` → `"0x12e04c88"`; **2 failed / 4 passed**, byte-identical restore, **6 passed**. Mutation runner inherited from attempt-3 Appendix D, with only root/scratch paths changed; fresh cache per run. |
| Same-block ordering, reversed RPC output | Attempt-1 A `test_same_block_order_and_reversed_rpc`: ordered `(1001,0),(1001,1),(1001,2)` exactly once; **PASS**. M5 update and removals within one block: confirmed/630000/63; **PASS**. |
| Restart with prior nonzero state | Attempt-1 A: persisted counter 1→2, one outbox row, durable cursor; **PASS**. Own sweep additionally rolls back failed counter changes and resumes after nested reorg/restart. |
| Missing input as clean | Attempt-1 A missing configured inbox reports health; attempt-2 B missing/directory/EACCES/empty and params revert/empty recovery all pass with canonical fixture. Empty existing file valid; missing/unreadable not healthy. M5 missing logs stays UNKNOWN, not clean, through V1B.6. |
| Empty RPC result as no logs | Attempt-1 A parameterized None / empty string / dict / `0x`: **4 PASS**, RpcError rather than no-logs success. Valid [] remains legitimate. |
| Two checks same incident key | Attempt-1 A plus required mutation: **PASS** before/after restart, exactly two incidents/outbox rows; sabotage demonstrably red. |
| Unnamed severity | Attempt-1 A `emergency`: Finding raises ValueError; direct enqueue raises KeyError and leaves no row after rollback; **PASS**. |
| Persisted orphan / pending health / no storm | Own 48-case sweep **48 green on frozen head / 48 red on old head**; no source mutation in QA worktree. |
| Absence / silent CP2 misuse / reopen | Appendix B **2 PASS**; absence outside reorg does not close, while intentionally invalid reorg `[]` does, then one fresh page on reopen. Approved contract limitation recorded in Findings. |

All mutation and old-head work occurred outside the repo. Both mutated source files were restored byte-for-byte in their isolated copy. `git status --porcelain` and `git diff --name-only b07d2c637fc66ce45b00f867b33085eaa3f3218c` were both empty before evidence writes; no implementation file was edited in this worktree.

## 5. Evidence-plane writes

CP1B Status changes **IMPLEMENTED — AWAITING QA → VERIFIED — PASS WITH NOTES**, anchored to frozen head and this report. Wrote only `plans/monitoring-service/qa/cp1b-attempt-4.md`, the single CP1B Status line in `plans/monitoring-service/plan.md`, and one appended event in `plans/monitoring-service/execution-log.md`. Created only the authorized ignored local venv and external scratch for execution. No implementation repair, earlier-report edit, production message, mainnet transaction, secret/config access, push or next-checkpoint work.

## 6. Repo proof

Lane base for QA attribution is the exact frozen tested head, not the earlier implementation base whose implementation diff is expected. Initial and pre-evidence status/diff were empty. Final staged proof before commit:

```text
$ git status --porcelain
M  plans/monitoring-service/execution-log.md
M  plans/monitoring-service/plan.md
A  plans/monitoring-service/qa/cp1b-attempt-4.md
$ git diff --name-only b07d2c637fc66ce45b00f867b33085eaa3f3218c
plans/monitoring-service/execution-log.md
plans/monitoring-service/plan.md
plans/monitoring-service/qa/cp1b-attempt-4.md
```

No implementation file appears in either output. Evidence commit leaves clean porcelain; local venv is ignored. Any Git index sandbox denial is handled by rerunning only the authorized evidence stage/commit outside the sandbox.

## Appendix A — independent adversarial sweep

Save as `/tmp/qa-cp1b4/test_adversarial.py` (adjust ROOT for another checkout) and run the command above. Old-head control changes only ROOT to the extracted old tree. No test expectation changes.

```python
"""Independent QA4: nested forks, crash gaps, multi-key conditions, transactional failure, reopening."""
import sys,pathlib,random,json
ROOT=pathlib.Path('/Users/marco/orca/workspaces/ssv-network-monitor/ms-qa-cp1b-4')
sys.path[:0]=[str(ROOT/'reconcile'),str(ROOT/'reconcile/tests')]
import pytest
from fakechain import FakeChain,FakeClock,PROXY,MiniSSV,keys
from harness import make_worker,outbox
from checks.base import Check,Every,Finding
from eb_storage import cluster_id
from test_worker import OWNER,OPS

SLOTS=list(range(0xBA900,0xBA907))
class Multi(Check):
 id='multi';schedule=Every(37)
 def __init__(self,fail=0):super().__init__({},{});self.fail=fail
 def run(self,ctx,block):
  ctx.set_state('n',ctx.state('n',0)+1)
  if ctx.reorg_ancestor is not None and self.fail:
   self.fail-=1;raise RuntimeError('injected after persistent write')
  return [Finding(self.id,str(s),'page','slot condition',str(v),resolve=not v) for s,v in zip(SLOTS,ctx.rpc.storage(PROXY,SLOTS))]

def expected(c,b):
 # Independent oracle from FakeChain's write journal, without a fresh Worker or incident machinery.
 vals={s:0 for s in SLOTS}
 for height,slot,value in c.writes:
  if height<=b and slot in vals:vals[slot]=value
 return {str(s) for s,v in vals.items() if v}

def opens(w):return {r['key'] for r in w.db.conn.execute("SELECT key FROM incidents WHERE check_id='multi' AND state='open'")}
def state(p):return json.loads((p/'w.status.json').read_text())

@pytest.mark.parametrize('confirmations',[0,1,7])
@pytest.mark.parametrize('seed',range(811,823))
def test_nested_crash_and_reopen(tmp_path,seed,confirmations):
 rng=random.Random(seed*101+confirmations);c=FakeChain();clock=FakeClock();c.mine(1+confirmations)
 start=c.sealed-confirmations
 def boot(fail=0):
  return make_worker(tmp_path,[Multi(fail)],chain=c,clock=clock,confirmations=confirmations,start_block=start,activation_block=start)[1]
 w=boot();w.step(force=True)
 for slot in SLOTS[:3]:c.write(slot,1)
 c.mine(confirmations+2);w.step(force=True)
 # Store many distinct checkpoints; the nested fork can cut through previously surviving conditions.
 earlier=c.number
 for _ in range(11):c.mine();w.step(force=True)
 fork=c.number
 for slot in SLOTS:c.write(slot,1)
 c.mine(confirmations+3);w.step(force=True)
 assert opens(w)==set(map(str,SLOTS))
 mark=len(outbox(w));oldheight=c.number
 c.reorg(fork)
 for slot in SLOTS[3:]:c.write(slot,rng.randrange(2))
 c.write(SLOTS[-1],0)
 c.mine(oldheight-c.number+rng.randrange(1,5));w.check_reorg();w.scan()
 assert w.db.meta('reorg:multi') is not None
 # Crash after durable rewind or after rescan, before any reorg check run.
 if seed%2:
  w.db.conn.close();w=boot(2)
 else:w.checks['multi'].fail=2
 clock.advance(21601)
 oldn=w.db.meta('check:multi:n')
 for _ in range(2):
  w.step(force=True)
  assert not state(tmp_path)['healthy'] and w.db.meta('reorg:multi') is not None
  assert w.db.meta('check:multi:n')==oldn # failed run is atomic
  assert not [r for r in outbox(w)[mark:] if r['severity']=='page']
 # A second reorg while the first marker is pending: half cut deeper, half replace the same branch.
 cut=earlier-1 if seed%3==0 else fork
 oldheight=c.number;c.reorg(cut)
 for slot in SLOTS:c.write(slot,rng.randrange(2))
 c.write(SLOTS[-1],0)
 c.mine(oldheight-c.number+2+confirmations)
 w.step(force=True);b=w.checkpoint()['block'];want=expected(c,b)
 assert opens(w)==want and state(tmp_path)['healthy'] and w.db.meta('reorg:multi') is None
 late=outbox(w)[mark:]
 for row in late:
  if row['severity']=='page':
   key=w.db.conn.execute('SELECT key FROM incidents WHERE id=?',(row['incident_id'],)).fetchone()[0]
   assert key in want,'page for orphaned evidence'
 for key in want:
  inc=w.db.conn.execute("SELECT id FROM incidents WHERE check_id='multi' AND key=? AND state='open'",(key,)).fetchone()[0]
  assert any(r['incident_id']==inc and r['severity']=='page' and r['sent_at'] is not None for r in outbox(w))
 count=len(outbox(w));
 for _ in range(25):w.step(force=True)
 assert len(outbox(w))==count,'alert storm on repeated runs'
 # A resolved orphan key reappears and gets one new page with a new incident id.
 key=str(SLOTS[-1]);oldids={r[0] for r in w.db.conn.execute('SELECT id FROM incidents WHERE key=?',(key,))}
 c.write(SLOTS[-1],1);c.mine(confirmations+1);w.step(force=True)
 row=w.db.conn.execute("SELECT * FROM incidents WHERE key=? AND state='open'",(key,)).fetchone()
 assert row['id'] not in oldids and row['alerts']==1
 new=outbox(w)[count:];assert len([r for r in new if r['incident_id']==row['id'] and r['severity']=='page'])==1
 count=len(outbox(w));w.db.conn.close();w=boot();w.step(force=True)
 assert len(outbox(w))==count and opens(w)==expected(c,w.checkpoint()['block'])

@pytest.mark.parametrize('seed',range(91,103))
def test_m5_nested_orphan_reinclude_same_identity(tmp_path,seed):
 rng=random.Random(seed);c=FakeChain();clock=FakeClock();c.mine();start=c.sealed
 from checks.m5_eb_desync import M5EbDesync
 def boot():
  settings={'proxy':PROXY,'start_block':start,'activation_block':start,'alerts':{}}
  m=M5EbDesync({**M5EbDesync.defaults,'sweep_interval_s':1,'full_sweep_interval_s':17},settings)
  return make_worker(tmp_path,[m],chain=c,clock=clock,start_block=start,activation_block=start)[1]
 w=boot();w.step(force=True);fork=c.number
 def poc(offset):
  s=MiniSSV(c);s.register(OWNER,OPS,keys(1,offset));ref=c.number;c.mine();s.commit(ref);s.update(OWNER,OPS,2048)
  ref=c.number;c.mine();d=keys(63,offset+100);s.register(OWNER,OPS,d);s.commit(ref);s.update(OWNER,OPS,2048);s.remove(OWNER,OPS,d)
 poc(seed*1000);c.mine(rng.randint(1,8));w.step(force=True)
 assert w.checks['m5'].last_evals[0].status=='confirmed'
 oldids={r[0] for r in w.db.conn.execute('SELECT id FROM incidents')};mark=len(outbox(w));height=c.number
 c.reorg(fork);c.mine(height-c.number+1);w.check_reorg();w.db.conn.close();w=boot();clock.advance(21601);w.step(force=True)
 assert not w.db.conn.execute("SELECT 1 FROM incidents WHERE state='open'").fetchone()
 assert not w.db.conn.execute('SELECT 1 FROM evaluations').fetchone()
 assert not [r for r in outbox(w)[mark:] if r['severity'] in ('page','warn')]
 assert state(tmp_path)['healthy']
 # Recreate the same cluster and root block neighborhood on the new chain; no default-id collision.
 poc(seed*1000+400);c.mine();w.step(force=True)
 row=w.db.conn.execute("SELECT * FROM incidents WHERE state='open'").fetchone();assert row and row['id'] not in oldids
 assert row['alerts']==1 and w.checks['m5'].last_evals[0].status=='confirmed'
 pages=[r for r in outbox(w)[mark:] if r['severity']=='page'];assert len(pages)==1 and pages[0]['sent_at'] is not None
 count=len(outbox(w));
 for _ in range(20):w.step(force=True)
 assert len(outbox(w))==count
 assert {r[0] for r in w.db.conn.execute("SELECT id FROM m5_incidents WHERE state='open'")}=={row['id']}
```

## Appendix B — default absence and deliberate contract misuse

```python
import pathlib,sys,json
ROOT=pathlib.Path('/Users/marco/orca/workspaces/ssv-network-monitor/ms-qa-cp1b-4')
sys.path[:0]=[str(ROOT/'reconcile'),str(ROOT/'reconcile/tests')]
from test_framework import Stub,finding
from harness import make_worker,outbox
from fakechain import FakeChain

def test_absence_without_reorg_never_resolves(tmp_path):
 c=FakeChain();c.mine();first=[finding()]
 def emit(ctx,b):out=list(first);first.clear();return out
 _,w=make_worker(tmp_path,[Stub(emit=emit)],chain=c);w.step(force=True)
 for _ in range(10):c.mine();w.step(force=True)
 assert w.db.conn.execute("SELECT state FROM incidents").fetchone()[0]=='open'
 assert len(outbox(w))==1 and w.db.meta('reorg:stub') is None

def test_health_then_empty_is_a_silent_contract_violation(tmp_path):
 c=FakeChain();c.mine();reorg_seen=[]
 def emit(ctx,b):
  if ctx.reorg_ancestor is not None:
   reorg_seen.append(ctx.reorg_keys)
   ctx.health('missing-custom-input','cannot evaluate','same-outage')
   return [] # violates README section 6.2, deliberately
  return [finding()]
 _,w=make_worker(tmp_path,[Stub(emit=emit)],chain=c)
 w.step(force=True);fork=c.number;c.mine(2);w.step(force=True)
 c.reorg(fork);c.mine(3);w.step(force=True)
 assert reorg_seen==[frozenset({'k1'})] # finding() default key
 assert w.db.conn.execute("SELECT state FROM incidents").fetchone()[0]=='resolved'
 assert json.loads((tmp_path/'w.status.json').read_text())['healthy'] is True
 assert 'Evidence left the canonical chain' in outbox(w,"kind='resolved'")[0]['text']
 # Same surviving finding reopens, using a unique id and one normal page.
 w.step(force=True)
 assert w.db.conn.execute("SELECT COUNT(*) FROM incidents WHERE state='open'").fetchone()[0]==1
 assert len(outbox(w,"kind='open'"))==2
```

## Appendix C — test integrity and scenario preservation

`git diff --name-status e13e3c8 HEAD -- reconcile/tests` is **only A** for `qa3_orphan_repro.py`, `test_qa3_appendix_b.py`, `test_qa3_regressions.py`, `test_reorg_refind.py`, `test_reorg_sweep.py`; no pre-existing test changed and no existing assertion was weakened. Byte comparison to extracted attempt-3 Appendix A/B ignores only the ROOT line and succeeds. Ruff's existing per-file rules remain unchanged (registration import order and QA2 one-line style); the only new exemptions are `tests/qa3_orphan_repro.py` (E401/E402/E702/F401/I001) and `tests/test_qa3_appendix_b.py` (E401/E402/E701/E702/F401/I001). These files are the verified appendix copies; no other file gained an exemption.

The original `test_worker.py` raw diff was reviewed: helper now invokes run_checks and retrieves M5 last_evals; Capture accepts route; assertion changes only critical→page, warning→warn, moved M5 incidents table, plus one added framework-resolution assert. `_no_pages` received the same severity rename. Decorators/parameterization, numeric expectations and benign scenarios remain unchanged. AST comparison source is attempt-1 Appendix B (extracted as `S/qa1_B.py`), run against original base `4fbb9f1`. Output:

```text
test_poc_trace_numbers_and_alerts 15 preserved; 1 added
test_poc_repeated_each_round_updates_same_incident 2 preserved; 0 added
test_partial_removal_still_confirms_with_exact_gap 1 preserved; 0 added
test_benign_post_s_removal_after_update 1 preserved; 0 added
test_benign_removal_between_s_and_update_is_overbilling 1 preserved; 0 added
test_benign_add_after_consumed_root 1 preserved; 0 added
test_benign_add_remove_without_root_update 1 preserved; 0 added
test_update_by_non_owner_is_classified_the_same 2 preserved; 0 added
test_two_clusters_same_owner_are_independent 2 preserved; 0 added
test_zero_snapshot_after_emptying_is_implicit_reset 1 preserved; 0 added
test_honest_add_before_update_still_registered_is_latent_only 1 preserved; 0 added
test_implicit_cluster_without_updates 1 preserved; 0 added
test_liquidated_cluster_confirmed_is_a_warning 3 preserved; 0 added
test_legacy_ssv_cluster_then_migration 2 preserved; 0 added
test_restart_resumes_and_keeps_incident_ids 3 preserved; 0 added
test_range_limits_are_split 1 preserved; 0 added
test_missing_logs_are_unknown_not_clean 3 preserved; 0 added
test_storage_layout_mismatch_suspends 2 preserved; 0 added
test_reorg_rewinds_and_replays 3 preserved; 0 added
test_rpc_outage_raises_health_alert 2 preserved; 0 added
test_delivery_retries_then_succeeds 2 preserved; 0 added
test_audit_reports_matched_window 2 preserved; 0 added
test_eb_to_vunits_matches_ceiling 1 preserved; 0 added
test_vunits_to_eb_floors 2 preserved; 0 added
test_telegram_errors_never_contain_the_token 2 preserved; 0 added
test_pre_hotfix_ssv_removal_keeps_snapshot 1 preserved; 0 added
PASS: 26 function names unchanged; 58 assertions preserved after severity-label/table-move normalization
```

## Appendix D — extraction, controls and restoration

The prior Appendix code was extracted from the committed reports, changing only ROOT. This setup also builds the isolated mutation and e13e3c8 trees:

```python
import pathlib,re,subprocess,tarfile,io
root=pathlib.Path.cwd(); s=pathlib.Path('/tmp/qa-cp1b4')
for n in (1,2,3):
 text=(root/f'plans/monitoring-service/qa/cp1b-attempt-{n}.md').read_text()
 for label in ('A','B','D'):
  if f'## Appendix {label}' not in text:continue
  section=text.split(f'## Appendix {label}',1)[1].split('\n## ',1)[0]
  m=re.search(r'```python\n(.*?)\n```',section,re.S)
  if not m:continue
  code=m[1]; code=re.sub(r"ROOT\s*=\s*pathlib.Path\('[^']+'\)",f'ROOT=pathlib.Path({str(root)!r})',code)
  (s/f'qa{n}_{label}.py').write_text(code)
# Isolated HEAD and old-head copies. Never mutate the review tree.
for name,rev in [('mutation','HEAD'),('old','e13e3c8')]:
 d=s/name;d.mkdir(exist_ok=True)
 tarfile.open(fileobj=io.BytesIO(subprocess.check_output(['git','archive',rev,'reconcile']))).extractall(d,filter='data')
 if name=='old':
  for f in ['test_qa3_regressions.py','qa3_orphan_repro.py','test_qa3_appendix_b.py','test_reorg_refind.py','test_reorg_sweep.py']:
   (d/'reconcile/tests'/f).write_bytes((root/'reconcile/tests'/f).read_bytes())
# Standalone old QA report B hash fixture correction, as approved by the confirmation GATE.
p=s/'qa2_B.py';(s/'qa2_B_canonical.py').write_text(p.read_text().replace("path.write_text(inbox_line(block=chain.sealed))", "path.write_text(inbox_line(block=chain.sealed,block_hash=chain.hash(chain.sealed)))"))
# prior mutation runner adapted for current scratch, same mutations and selections
code=(s/'qa3_D.py').read_text().replace('/Users/marco/orca/workspaces/ssv-network-monitor/ms-qa-cp1b-3',str(root)).replace('/tmp/qa-cp1b3','/tmp/qa-cp1b4')
(s/'mutate.py').write_text(code)
```

The final canonical fixture adaptation is exactly:

```python
code = original.replace('inbox_line(tx=tx,block=chain.sealed)',
                        'inbox_line(tx=tx,block=chain.sealed,block_hash=chain.hash(chain.sealed))')
```

Mutation runner (red assertions require pytest exit 1, restoration requires byte equality and pytest exit 0):

```python
import subprocess,pathlib,os
root=pathlib.Path('/Users/marco/orca/workspaces/ssv-network-monitor/ms-qa-cp1b-4');scratch=pathlib.Path('/tmp/qa-cp1b4/mutation');python=str(root/'reconcile/.venv/bin/python')
for name,file,old,new,selection in [('V1B.4f','alerts.py','return ":".join((check, key))','return ":".join((key,))','restart_idempotent'),('V1B.7f','inbox.py','"0x12e04c87"','"0x12e04c88"','inbox')]:
 p=scratch/'reconcile'/file;original=p.read_bytes();assert old.encode() in original
 try:
  p.write_bytes(original.replace(old.encode(),new.encode(),1))
  env={**os.environ,'PYTHONPYCACHEPREFIX':f'/tmp/qa-cp1b4/cache-{name}-red'}
  r=subprocess.run([python,'-m','pytest','-q','-p','no:cacheprovider','reconcile/tests/test_framework.py','-k',selection],cwd=scratch,env=env,capture_output=True,text=True)
  print(name,'RED exit',r.returncode);print(r.stdout);assert r.returncode==1
 finally:p.write_bytes(original)
 assert p.read_bytes()==original
 env={**os.environ,'PYTHONPYCACHEPREFIX':f'/tmp/qa-cp1b4/cache-{name}-green'}
 r=subprocess.run([python,'-m','pytest','-q','-p','no:cacheprovider','reconcile/tests/test_framework.py','-k',selection],cwd=scratch,env=env,capture_output=True,text=True)
 print(name,'RESTORED byte-identical GREEN exit',r.returncode);print(r.stdout);assert r.returncode==0```

Old-control sweep has only this compatibility replacement (the test's assertions stay unchanged):

```python
p = Path('/tmp/qa-cp1b4/old/reconcile/tests/test_reorg_sweep.py')
p.write_text(p.read_text().replace('ctx.reorg_keys & canonical',
                                  'getattr(ctx, "reorg_keys", frozenset()) & canonical'))
```

For portability, QA's ROOT and scratch paths are explicit above; the historical report files remain unmodified. Full old-control summaries are 19 failed / 29 passed (new regressions), 69 failed / 21 passed (committed sweep), and 48 failed (own sweep).

## Appendix E — output integrity

SHA256 of salient original scratch outputs, captured before report generation (scratch is supporting detail; durable commands/code and counts above suffice to reproduce):

```text
f4344cb8ce2f3c5531585f42131e7a1a8659e8fb7b96034bfc65f62f10a38c94  inventory-sandbox.out
725320f42646fba568c96206b27d14742ce370e418893827f6e188cb23c3e689  inventory-live.out
a568d81ec3061b4bb2273a9d4c8cda28487a0ace749003c0d1fc425c1ce34972  fork-sandbox.out
26a75d49aa8d19c6bca212c8eac74dd7ec21991ea95812c5b14182f3b73adfa1  fork-live.out
ce8120519bddf6fbc56ebb66e05796553abb0f7f685d6891bf77c10428d6ee80  metrics.txt
c9d3df98b79e0776dc67ca71f351bb1788c46711d4d1d69ad63dbd12443d0ae3  prior-qa1-3.out
cbf2846ab0b0e36c4513c89d1c5252238662ea7c7cc5f3865ad54523c48d325b  prior-qa2-A.out
19cef12b65263bcfc0569acd7c512bbd61cc299463d26e7897dcbc1c56b30f46  prior-qa3-A0.out
570ccea236b072f43f2bdcff50d92de374ab143c515ba5600a74e990ff9a719e  prior-qa3-A12.out
00930f820ef71af830d535d989e4b06de1a07ec0ae9cb555b3087830b085f6e0  prior-qa2-B-original.out
3505b1c4057954c7b899990d26c3bcdf5c6a1192ff677524c8c1d21a2047d4f7  prior-qa2-B-canonical.out
2ac2d14ca56f15af0a016ed9ad75c75de42594790e3064afcf03078b636f550a  adversarial.out
0a97b591ab7ce9644866a9aea8e19c37ff6647585418c546d0481e0cb92bfbc7  contract-edges.out
4c5eec080e4d08bbf4486a37ad2bdaf7aa1372266d4552d02bc60cf87df4386e  old-regressions.out
0569271fa4505b84c0c9a493f1601358191120cece0a360d28b319a3dc41df42  old-sweep.out
503be0d63005985feebb4700f1b0a00de26403c5bd355b38dfbd4fd47a24aa43  own-old.out
463a6f5bf6a0b823cc396c7b96f136a3946aa5216d6fc1cc42aea64eef7dcb5d  mutations.out
f15d68932db86043ac07724b04056af211237565833c36657ef30bd90c1d2c99  integrity.out
```
