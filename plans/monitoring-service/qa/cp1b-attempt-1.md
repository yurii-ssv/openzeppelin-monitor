FAIL

Independent CP1B QA, attempt 1 · 2026-10-21 · Codex gpt-6.1-sol / high (requested in dispatch; launch receipt belongs to coordinator).
Frozen head: `48245069514795f667f2c642062148c09327ab4b` (code `aaf5c25`); QA branch `mtabasco/ms-qa-cp1b`; implementation base `4fbb9f1`.
Task `task_88833020edbb`, dispatch `ctx_1cd499ce42d0`. Baseline gates pass, but three independent failure probes block acceptance.

## 1. Findings

### High — migration leaves pending legacy alerts permanently undeliverable

Location: `reconcile/db.py:migrate`, `reconcile/eb_reconcile.py:deliver`.
Minimal command (script reproduced in Appendix A):
`reconcile/.venv/bin/python -m pytest -q /tmp/test_cp1b_independent.py -k migrated_pending_outbox_routes_to_telegram`

Seed an authentic pre-framework schema with an unsent `critical` outbox row whose `delivery` is NULL (the old `notify` inserts only key/time/severity/text). Instantiate the new DB and deliver using a Router with a working Telegram transport. Expected: normalize legacy severity, choose Telegram, deliver the queued alert, and preserve its identity. Actual: `sent_at` stays NULL, `delivery` stays NULL, attempts becomes 1, and last_error is `no transport configured for route None`; exit 1. Retrying cannot change the route. The baseline migration tests cover an already-sent row and miss this state. This breaks durable delivery across the framework upgrade.

### High — failed live-parameter reload permits checks to use stale thresholds

Location: `reconcile/eb_reconcile.py:reload_params/run_checks`.
Minimal command:
`reconcile/.venv/bin/python -m pytest -q /tmp/test_cp1b_independent.py -k failed_parameter_reload_does_not_use_stale_threshold`

Load threshold 42,960, emit `LiquidationThresholdPeriodUpdated(50000)`, make that getter revert, and run the worker again. Expected: checks dependent on these parameters cannot evaluate with the old threshold after the change; report degraded health and retry the reload. Actual: a health message is queued, but the check executes again with 42,960 (`observed == [42960, 42960]`); exit 1. `reload_params` catches non-transport RpcError and returns without invalidating the old values or stopping their consumers; the changed-event prepare path is not reached. CP2 threshold checks can consequently classify or resolve incidents using obsolete protocol values.

### Medium — a missing configured inbox is indistinguishable from a healthy empty input

Location: `reconcile/inbox.py:Inbox.read`, `reconcile/eb_reconcile.py:poll_inbox/step`.
Minimal command:
`reconcile/.venv/bin/python -m pytest -q /tmp/test_cp1b_independent.py -k missing_inbox_raises_health`

Configure an enabled inbox at a nonexistent path and step a worker hosting Every + OnInbox schedules. Expected: a monitor-health indication that failed-transaction input cannot be read. Actual: FileNotFoundError becomes `(0, [])`, no health outbox row is generated, and step writes healthy status; exit 1. A broken mount/path can silently remove the failed-transaction evidence CP2 needs. An empty existing file is valid, but an unavailable configured input must remain distinguishable.

## 2. Claims I could NOT confirm

I independently reproduced every named V1B row on this head, including both LIVE rows and both mutations. I could not confirm the broader claim that the framework safely preserves durable delivery and reloads live parameters for consumers: the reproducers above disprove those claims in uncovered states. Missing inbox health likewise fails independently. No claim about real Telegram/Discord delivery was tested: record mode and local capture transports only, as authorized; no secrets or production messages were used.

I did not reproduce historical intermediate implementer state or its exact prior LIVE block/count timings; fresh measurements follow. Docker image build and production deployment are outside CP1B's rows. The authoritative README supplies enough interface/config/helper detail to draft CP2 lane packets, but CP1B must be repaired and freshly verified before CP2 proceeds.

## 3. Verification matrix

Preflight: HEAD exactly matched dispatch; initial `git status --porcelain` empty; 356 GiB available; anvil symlink resolves to Foundry v1.8.5. `uv venv reconcile/.venv --python 3.12` and `uv pip install --python reconcile/.venv/bin/python -r reconcile/requirements-dev.txt` succeeded with cache permission escalation (initial sandbox attempts exit 2: `Could not acquire lock ... Operation not permitted` under `/Users/marco/.cache/uv`). Python 3.12.15; pytest 9.1.1, ruff 0.17.1, pycryptodome 3.24.0. PATH includes `$HOME/.foundry/bin` for fork commands. No other installs.

Each command below was run separately; `…` expands to `reconcile/.venv/bin/python -m pytest -q reconcile/tests/test_framework.py`.

| ID | Command | Exit / measured result |
|---|---|---|
| V1B.1 | `reconcile/.venv/bin/python -m pytest -q reconcile/tests -rs` | Initial sandbox exit 1: 61 passed, metrics bind PermissionError `[Errno 1] Operation not permitted`; environment-cleared rerun exit 0: **62 passed, 0 skipped** (30 old + 32 new). Final restored fresh-cache rerun exit 0: 62 passed in 0.91 s. Baseline PASS; independent defects above remain blocking. |
| V1B.2 | `reconcile/.venv/bin/ruff check reconcile` | 0; All checks passed. |
| V1B.3 | `… -k scheduler_dispatches_registered_check` | 0; 1 passed, 31 deselected; initial, +60s interval, matching event, no duplicate re-step. |
| V1B.4 | `… -k restart_idempotent` | 0; 2 passed, 30 deselected; persistent incident and cross-check same-key separation. Enhanced pending legacy-state restart FAIL (finding 1). |
| V1B.4f | Drop check from incident join; same selection | Mutated exit 1: 1 failed/1 passed; restored exit 0: 2 passed; clean porcelain. |
| V1B.5 | `… -k routing` | 0; 4 passed, 28 deselected; page/warn/health Telegram, info/digest Discord, JSONL receipts. Unknown severity probe rejects before enqueue. Legacy pending route FAIL (finding 1). |
| V1B.6 | `reconcile/.venv/bin/python -m pytest -q reconcile/tests -k m5` | 0; 32 passed, 30 deselected. All 26 original function names and 58 original assertions preserved under documented port renames; one assertion added. Details below. |
| V1B.7 | `… -k inbox` | 0; 6 passed, 26 deselected; parse, persistent offset, shrink, dedup, replay at block−1, selector, fallback. Missing enabled input FAIL (finding 3). |
| V1B.7f | Change `0x12e04c87` to `0x12e04c88`; inbox selection | Mutated exit 1: 2 failed/4 passed; restored fresh-cache exit 0: 6 passed; final ordinary-cache run also exit 0: 6 passed. Clean porcelain. |
| V1B.8 | `reconcile/.venv/bin/python reconcile/tests/fake_once.py --port 19464 --hold 30`; separately `curl -s http://127.0.0.1:19464/metrics` | Sandbox server exit 1 bind PermissionError; sandbox curl exit 7. With permissions server exit 0 and curl exit 0. checkpoint 1011, lag 0, m5/page open 1, undelivered 0, check_last_run_seconds m5 1792582286.105669; all five required series present. |
| V1B.9 | `reconcile/.venv/bin/python reconcile/inventory.py --probe --rpc https://ethereum-rpc.publicnode.com` (equivalent CLI) | Sandbox exit 1: `urllib.error.URLError: <urlopen error [Errno 8] nodename nor servname provided, or not known>`; allowed-network rerun exit 0. LIVE block **26,240,677**; 1,908 API rows; 1,026 migrated=false; 25/25 sampled tuples validated (9 ETH/16 SSV), 0 invalid/absent, 68 operators. F10 independently measured. |
| V1B.10 | `export PATH=$HOME/.foundry/bin:$PATH` then `FORK_RPC_URL=https://ethereum-rpc.publicnode.com PYTHONPYCACHEPREFIX=/tmp/cp1b-fork-cache reconcile/.venv/bin/python reconcile/scripts/fork_e2e.py` | Sandbox variant without cache prefix exit 1: `urllib.error.URLError: <urlopen error [Errno 1] Operation not permitted>`; allowed-network rerun exit 0. Fork block **26,240,700**, five PASS stages: clean 640000/640000, clean 1270000/1270000, latent 1270000/640000, confirmed 640000/10000 with 63 matches and 630000 discrepancy, clean next root 640000/640000. First confirmed page→telegram recorded; resolution info→telegram recorded; one resolved incident; params read; layout guard ok. anvil terminated by script finally. |
| V1B.11 | Read `reconcile/checks/README.md`; exercise its helpers in Appendix A | PASS (inspection, no executable exit): class/registration, schedule semantics, context fields, live parameters, validated inventory, inbox entries, defaults config schema, fixtures and V2x patterns are sufficiently specified to draft CP2 packets. The monitor-specific spec remains a listed CP2 input. The reference describes intended behavior; findings 1–3 expose runtime limitations rather than missing packet instructions. |

M5 decision/env checks: baseline and LIVE fork both page on the first confirmed active round, matching Marco's decision. Config/example, notifier defaults and compose use `MAINNET_RPC_URL`, `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`, `DISCORD_WEBHOOK_INFO`. Nothing in config/, config.zip or any .env file was read or written by this QA.

M5 preservation comparison: `git diff 4fbb9f1 -- reconcile/tests/test_worker.py` inspected directly, then AST comparison (`reconcile/.venv/bin/python /tmp/cp1b_assertion_diff.py`, exit 0). Function-name sets identical (26); all 58 original assert ASTs match after `critical`→`page`, `warning`→`warn`, and the moved M5 table `incidents`→`m5_incidents` normalization. The first PoC test adds a framework-resolution assertion. Helpers now invoke `run_checks` and read M5 last_evals, and Capture accepts route; thresholds/counts/benign twins are unchanged. 26 functions expand to 30 original test cases because the conversion test has 5 parameters; with 2 migration cases selection is 32. Full independent comparison output and script appear below.

## 4. Falsifiers

Required one-token experiments ran on actual implementation files, not test expectations; original bytes restored in finally, then green reruns. `/tmp/cp1b_mutations.py` ran both red probes: dropping check from join caused the two-check test to observe only one incident; altered selector caused decode mismatch and keccak mismatch. Its first immediate restored inbox run was unexpectedly red because equal-length mutation/restoration within one second reused stale timestamp-based Python bytecode. Source diff and porcelain were empty; a fresh bytecode prefix gave 6 green, then touching the restored source's timestamp and rerunning normally gave 6 green. This was a QA execution/cache artifact, not an implementation defect or a surviving mutation. The overall mutation harness exit was 1 due to that immediate cache artifact; individual red and restored-green outcomes are explicit above.

Independent probes (`PYTHONPYCACHEPREFIX=/tmp/cp1b-independent-cache reconcile/.venv/bin/python -m pytest -q /tmp/test_cp1b_independent.py`, exit 1, **9 passed/3 failed**, no skips; one unknown m5 marker warning because scratch collection does not load repo conftest):

| Probe | Outcome |
|---|---|
| Same-block framework events with reversed RPC log order and repeated advance | PASS; ctx.events yields `(1001,0),(1001,1),(1001,2)` exactly once. |
| M5 root update then 63 removals in the same block (separate transactions) | PASS; confirmed with 630000 shortfall and 63 matched keys. |
| Restart with stored nonzero per-check counter, open page and cursor | PASS; counter 1→2 and outbox remains one row. |
| Empty/malformed eth_getLogs results: None, empty string, dict, `0x` | 4 PASS; RpcError, never read as no logs. A valid empty list remains legitimate. |
| Severity outside table (`emergency`) | PASS; Finding raises ValueError; raw enqueue raises KeyError and transaction leaves no outbox row. |
| Two checks (`m1`, `m2`) returning same incident key before/after restart | PASS; two incidents, exactly two outbox rows. |
| Missing configured inbox | FAIL; finding 3. |
| Restart from old schema with an undelivered page | FAIL; finding 1. |
| Failed reload after parameter-change event | FAIL; finding 2. |

M5 missing-log scenario additionally reproduced in the unchanged `test_missing_logs_are_unknown_not_clean` through V1B.1/V1B.6: unknown + health, never clean. No implementation edits are proposed or performed. All independent DBs/fixtures live in pytest scratch outside the repo.

## 5. Evidence-plane writes

CP1B **Status:** transitioned IMPLEMENTED — AWAITING QA → **QA FAILED**, anchored to frozen head and this report. Wrote only this report, that CP1B status line, and one appended event in `plans/monitoring-service/execution-log.md`; local ignored `reconcile/.venv` was created as authorized. Temporary mutations restored byte-for-byte. No implementation repair, no production routes/secrets, no mainnet transactions, no push. Repair and a fresh independent QA are left to the coordinator.

## 6. Repo proof

QA lane base is frozen tested head `48245069514795f667f2c642062148c09327ab4b`; the implementer's earlier base `4fbb9f1` naturally includes its implementation changes, and is not attributed to QA. Before evidence writes: `git status --porcelain` and `git diff --name-only 48245069514795f667f2c642062148c09327ab4b` were both empty, proving source restoration. At final evidence staging (before commit), outputs are below; no implementation file appears in either QA output. The report is staged for the diff proof.

```text
$ git status --porcelain
M  plans/monitoring-service/execution-log.md
M  plans/monitoring-service/plan.md
A  plans/monitoring-service/qa/cp1b-attempt-1.md
$ git diff --name-only 48245069514795f667f2c642062148c09327ab4b
plans/monitoring-service/execution-log.md
plans/monitoring-service/plan.md
plans/monitoring-service/qa/cp1b-attempt-1.md
```

No implementation file appears in either output. Each minimal finding selection was also rerun independently: exit 1, 1 failed/11 deselected (each), with the same actual result. The Git staging sandbox initially refused the external worktree index lock; the authorized evidence-only stage succeeded after permission escalation.

## Appendix A — independent reproducible probes

Save this code to `/tmp/test_cp1b_independent.py` (adjust ROOT for a different checkout), then run the commands above.

```python
import sys, pathlib, sqlite3
ROOT = pathlib.Path('/Users/marco/orca/workspaces/ssv-network-monitor/ms-qa-cp1b')
sys.path[:0] = [str(ROOT/'reconcile'), str(ROOT/'reconcile/tests')]
import pytest
from test_framework import Stub, finding, LEGACY_SCHEMA
from harness import make_worker, advance, outbox, VIEWS, stub_params
from fakechain import FakeChain, FakeClock, MiniSSV, keys
from checks.base import Every, OnEvents, OnInbox, Finding
from rpc import Rpc, RpcError
from params import GETTERS, selector
from notify import Router
from test_worker import poc_setup, tick, ev_for, OWNER, OPS, make
import eb_model as M

def test_same_block_order_and_reversed_rpc(tmp_path):
    chain=FakeChain(); chain.mine()
    seen=[]
    stub=Stub(schedule=OnEvents({'NetworkFeeUpdated'}), emit=lambda ctx,b: seen.extend((e.block,e.log_index) for e in ctx.events()) or [])
    _,w=make_worker(tmp_path,[stub],chain=chain)
    from eb_storage import u256
    for n in range(3): chain.emit_event('NetworkFeeUpdated',[],u256(n)+u256(n+1))
    chain.mine(); chain.logs.reverse()
    advance(w); advance(w)
    assert seen == [(1001,0),(1001,1),(1001,2)]

def test_m5_same_block_update_then_remove(tmp_path):
    chain,w,ssv=poc_setup(tmp_path)
    s=chain.number; chain.mine(); d=keys(63,7000)
    ssv.register(OWNER,OPS,d); ssv.commit(s)
    original=chain.mine; chain.mine=lambda n=1: chain.number
    ssv.update(OWNER,OPS,2048); ssv.remove(OWNER,OPS,d)
    chain.mine=original; chain.mine()
    e=ev_for(tick(w))
    assert e.status == M.CONFIRMED and e.shortfall_vunits == 630000 and len(e.matched_keys)==63

def test_restart_prior_nonzero_state_and_event_cursor(tmp_path):
    chain=FakeChain(); chain.mine(); clock=FakeClock()
    def emit(ctx,b):
        prior=ctx.state('counter',0); ctx.set_state('counter',prior+1)
        return [finding(key='existing')]
    _,w=make_worker(tmp_path,[Stub(emit=emit)],chain=chain,clock=clock)
    advance(w)
    assert w.db.meta('check:stub:counter')==1 and len(outbox(w))==1
    _,w2=make_worker(tmp_path,[Stub(emit=emit)],chain=chain,clock=clock,start_block=w.cfg.start_block,activation_block=w.cfg.activation_block)
    advance(w2)
    assert w2.db.meta('check:stub:counter')==2 and len(outbox(w2))==1

@pytest.mark.parametrize('result',[None,'',{},'0x'])
def test_empty_rpc_result_rejected(result):
    r=Rpc('unused',retries=0); r._post=lambda payload: {'result': result}
    with pytest.raises(RpcError,match='non-list'):
        r.get_logs('0x'+'00'*20,1,1,10,[])

def test_unknown_severity_fails_closed(tmp_path):
    with pytest.raises(ValueError): Finding(check='x',key='k',severity='emergency',title='t',body='b')
    chain=FakeChain(); chain.mine(); _,w=make_worker(tmp_path,[],chain=chain)
    with pytest.raises(KeyError):
        with w.db.tx(): w.alerts.enqueue('bad','emergency','t','open')
    assert outbox(w)==[]

def test_two_checks_same_incident_key_after_restart(tmp_path):
    chain=FakeChain(); chain.mine()
    def checks(): return [Stub(check_id=c,emit=lambda ctx,b:[finding(check=ctx.check_id,key='same')]) for c in ['m1','m2']]
    _,w=make_worker(tmp_path,checks(),chain=chain); advance(w)
    _,w2=make_worker(tmp_path,checks(),chain=chain,start_block=w.cfg.start_block,activation_block=w.cfg.activation_block); advance(w2)
    assert len(outbox(w2))==2 and w2.db.conn.execute('SELECT COUNT(*) FROM incidents').fetchone()[0]==2

def test_missing_inbox_raises_health(tmp_path):
    chain=FakeChain(); chain.mine()
    _,w=make_worker(tmp_path,[Stub(schedule=(Every(60),OnInbox()))],chain=chain,inbox={'path':str(tmp_path/'missing.jsonl')})
    w.step(force=True)
    assert any(r['kind']=='health' for r in outbox(w)), 'missing configured inbox produced no health; worker reports healthy'

def test_migrated_pending_outbox_routes_to_telegram(tmp_path):
    path=tmp_path/'w.sqlite'; conn=sqlite3.connect(path); conn.executescript(LEGACY_SCHEMA)
    conn.execute("INSERT INTO outbox(key,created_at,severity,text) VALUES ('pending',1,'critical','old page')")
    conn.commit(); conn.close()
    class Transport:
        def send(self,*args): return 'telegram:123'
    chain=FakeChain(); chain.mine()
    _,w=make_worker(tmp_path,[],chain=chain,notifier=Router({'telegram':Transport()}))
    w.deliver(); row=outbox(w)[0]
    assert row['sent_at'] is not None and row['delivery']=='telegram', dict(row)

def test_failed_parameter_reload_does_not_use_stale_threshold(tmp_path):
    chain=FakeChain(); stub_params(chain); chain.mine(); observed=[]
    stub=Stub(emit=lambda ctx,b: observed.append(ctx.params.liquidation_threshold_period) or [])
    _,w=make_worker(tmp_path,[stub],chain=chain,views=VIEWS); advance(w)
    from eb_storage import u256
    chain.stub_call(VIEWS,selector(GETTERS['liquidation_threshold_period']),revert='0x12345678')
    chain.emit_event('LiquidationThresholdPeriodUpdated',[],u256(50000)); chain.mine(); w.step(force=True)
    assert observed==[42960], f'check ran using stale parameters after failed reload: {observed}'
```

## Appendix B — assertion comparison

```python
import ast, pathlib, subprocess
p='reconcile/tests/test_worker.py'
base=ast.parse(subprocess.check_output(['git','show','4fbb9f1:'+p],text=True))
head=ast.parse(pathlib.Path(p).read_text())
def funcs(tree): return {n.name:n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name.startswith('test_')}
b,h=funcs(base),funcs(head)
assert b.keys()==h.keys()
class Normalize(ast.NodeTransformer):
    def visit_Constant(self,n):
        if isinstance(n.value,str):
            n.value=n.value.replace('critical','page').replace('warning','warn')
            n.value=n.value.replace('SELECT * FROM incidents','SELECT * FROM m5_incidents')
        return n
def assertions(f): return [ast.dump(Normalize().visit(n),include_attributes=False) for n in ast.walk(f) if isinstance(n,ast.Assert)]
total=0
for name in b:
    before,after=assertions(b[name]),assertions(h[name])
    assert after[:len(before)]==before,name
    total+=len(before)
    print(name,len(before),'preserved;',len(after)-len(before),'added')
print('PASS:',len(b),'function names unchanged;',total,'assertions preserved after severity-label/table-move normalization')
```

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
