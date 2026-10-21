FAIL

Frozen head: `9a241bdfc495d439fa8ea532b43410365dfe5711` (repair code `4819c9e`); QA branch `mtabasco/ms-qa-cp1b-2`; task `task_44cf85dd5e43`, dispatch `ctx_2df97d41a0e5`. Independent QA attempt 2; no implementation repairs. All V1B rows were re-measured; the three attempt-1 reproducers now pass, but a new live-parameter/reorg falsifier blocks A2 and CP1B verification.

## 1. Findings

### HIGH — a reorg leaves orphaned live parameters visible to checks indefinitely

Reproducer: save Appendix A as `/tmp/qa-cp1b2/reorg_repro.py` and run:

```sh
PYTHONPYCACHEPREFIX=/tmp/qa-cp1b2/reorg-repro-cache reconcile/.venv/bin/python /tmp/qa-cp1b2/reorg_repro.py
```

Measured exit **1**. Load threshold 42,960 at block 1000, scan a change to 50,000 at block 1001, then reorg that block away and seal a replacement with canonical threshold 42,960 and no parameter-change event. The probe explicitly reads the canonical getter through the same block-pinned Chain used by checks and confirms 42,960. `Worker.check_reorg` detects the changed hash and rewinds to 1000, but the next check reads 50,000; two further mined/forced steps still read 50,000 at checkpoint 1003. Both direct `ctx.params` reads and the README's `prepare`-cached pattern produce `[42960, 50000, 50000, 50000, 50000]`. Expected after the reorg: refresh at the new canonical checkpoint before checking, or block parameter consumers until refresh succeeds; the final three values must be 42,960. Actual: `params.ok == True`, status `healthy == True`, both runs commit with zero errors and stale values. An independent pytest version was also run twice: exit 1, same mismatch (Appendix B, `-k reorged_parameter_event`).

Relevant code: `reconcile/eb_reconcile.py:192` (`check_reorg`) invalidates inventory and rewinds cursors but leaves Params and prepared copies intact; `reload_params` at line 348 skips loading once `params.block` is set and no later parameter event exists. The event that justified the cached value was deleted by the reorg. Thus the stale state survives further blocks until another parameter event or restart; CP2 checks may use a wrong threshold, quorum or oracle roster while reporting healthy. This is separate from the repaired failed-getter path. Repair and fresh QA are required; no source change is made here.

## 2. Claims I could NOT confirm

I cannot confirm the broad claim that checks always use live canonical parameters: finding 1 disproves it after a handled reorg. Every named V1B command, both required mutations, both LIVE rows, attempt 1's Appendix A (12 tests), the repair regression file (7 tests), and the additional edge probes were independently run. There are no silent or NOT RUN required rows. The implementer's historical block numbers/timings were not reused as evidence; current measurements are below. Production Telegram/Discord delivery, image build, deployment and the later soak remain outside this QA; all alerts used record mode or local capture and no real secrets were used.

The 3 attempt-1 failures are independently cleared on this repaired head: migrated pending legacy alerts deliver, failed parameter reloads suppress stale reads and recover, and missing configured inboxes raise health. This does not clear the new reorg finding. The regression file was copied unmodified onto scratch code from `c21a51f`: all **7 fail** there and all **7 pass** here. The original 3 fail there on their behavioral assertions (NULL route, stale `[42960,42960]`, no missing-inbox health); 2 newer tests fail on old code's absent `params.ok`, and the other 2 fail on normalization/missing-inbox assertions. Import paths were checked: db, params, inbox, harness and eb_reconcile all came from `/tmp/qa-cp1b2/old/reconcile`, never the current checkout.

Deployment consequence: requiring an existing readable inbox file is **acceptable**. Missing, permission-denied and directory paths must not silently mean no failed transactions; an existing empty file is healthy. The worker's mount is read-only, so CP1A/CP3 must create the empty file before startup. Both the authoritative README and compose comment document this prerequisite. No deployment file was changed by QA.

## 3. Verification matrix

Preflight: HEAD exactly equals the dispatch anchor; initial porcelain empty; 354 GiB available; anvil 1.8.5 present at `~/.foundry/bin/anvil`; no venv existed. Authorized `uv venv reconcile/.venv --python 3.12` exited 0. `uv pip install --python reconcile/.venv/bin/python -r reconcile/requirements-dev.txt` first exited 2 (`Could not acquire lock`, `Operation not permitted` in `/Users/marco/.cache/uv`); permission-cleared retry exited 0 and installed only requirements-dev dependencies. Python 3.12.15, pytest 9.1.1, ruff 0.17.1, pycryptodome 3.24.0. No other installs.

For brevity, `PF` below is exactly `reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider reconcile/tests/test_framework.py`; scratch caches use fresh `PYTHONPYCACHEPREFIX`. Each row was a separate command/subprocess, with no earlier failure masking it.

| ID | Command | Exit / outcome / counts |
|---|---|---|
| V1B.1 | `reconcile/.venv/bin/python -m pytest -q reconcile/tests -rs` | **0; 69 passed, 0 skipped** (initial 1.38 s; after mutations restored 0.85 s). Baseline 30 + 32 framework + 7 repair tests. This suite is green but misses finding 1. |
| V1B.2 | `reconcile/.venv/bin/ruff check reconcile` | **0; All checks passed**, including final restored check. |
| V1B.3 | `PF -k scheduler_dispatches_registered_check` | **0; 1 passed, 31 deselected**; Every(60), matching event, no early/duplicate run. |
| V1B.4 | `PF -k restart_idempotent` | **0; 2 passed, 30 deselected**; one durable outbox row and two checks isolated. Added nonzero-state/restart probe also green. |
| V1B.4f | Drop check from incident-key join; `PF -k restart_idempotent` | **1; 1 failed, 1 passed, 30 deselected** (two-check collision). Restore: **0; 2 passed**, porcelain empty. |
| V1B.5 | `PF -k routing` | **0; 4 passed, 28 deselected**; page/warn/health Telegram, info/digest Discord, record JSONL + receipt. Added unknown severity rejects without enqueue. |
| V1B.6 | `reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider reconcile/tests -k m5` | **0; 32 passed, 37 deselected**. All 26 pre-existing function names and 58 assert ASTs preserved after documented severity/table renames; 1 assertion added. See Appendix D. |
| V1B.7 | `PF -k inbox` | **0; 6 passed, 26 deselected**; parse, offset/restart/shrink/dedup, partial line, eth_call at block−1, `0x12e04c87` → IncorrectClusterState, fallback. Missing/unreadable edge probes also green. |
| V1B.7f | Change selector `0x12e04c87` → `0x12e04c88`; `PF -k inbox` | **1; 2 failed, 4 passed, 26 deselected** (decode and keccak). Restore: **0; 6 passed**, porcelain empty. |
| V1B.8 | `reconcile/.venv/bin/python reconcile/tests/fake_once.py --port 29464 --hold 12`; separately `curl -s http://127.0.0.1:29464/metrics` | **server 0; curl 0**. checkpoint 1011; lag 0; open m5/page 1; undelivered 0; last_run_seconds m5 1792583524.659246; all 5 required series present, plus both input-unavailable gauges 0. Runs `once --serve-metrics` on fake chain. |
| V1B.9 | `reconcile/.venv/bin/python reconcile/inventory.py --probe --rpc https://ethereum-rpc.publicnode.com` (equivalent CLI) | **0, LIVE** at block **26,240,776**: API v4 **1,908 rows**, **1,026 migrated=false**, **25 sampled/25 validated**, 9 ETH/16 legacy SSV, 0 absent/invalid, 68 operators. API `pagination.pages` fetches both pages. F10 independently MEASURED; no outbound failure for this row. |
| V1B.10 | `export PATH=$HOME/.foundry/bin:$PATH` then `FORK_RPC_URL=https://ethereum-rpc.publicnode.com reconcile/.venv/bin/python reconcile/scripts/fork_e2e.py` | Initial sandbox **1**, `urllib.error.URLError: <urlopen error [Errno 1] Operation not permitted>` connecting to local anvil. Permission-cleared rerun (same command plus `PYTHONUNBUFFERED=1`) **0, LIVE**; fork **26,240,842**, all 5 stages PASS (below). No mainnet transaction. |
| V1B.11 | Read `reconcile/checks/README.md` and exercise its declared helpers in Appendices A–C | **PASS (inspection)**: sufficient framework contract for CP2 packets: file ownership/import registration, Check lifecycle, schedules/reasons/cursors, Finding and routes, all Ctx fields and params/inventory/inbox types, config defaults/schema validation, fixtures and V2x patterns. Monitor-specific triggers still come from the spec already listed in each lane's inputs. No additional framework source is needed to draft packets. Runtime safety still blocked by finding 1. |

LIVE fork outcomes: clean **640000/640000** → clean after 63 dummies **1270000/1270000** → latent **1270000/640000**, discrepancy **630000** → confirmed **640000/10000**, **63 matched**, discrepancy **630000**, one **page → telegram**, receipt `recorded` → clean next-root **640000/640000**, resolution **info → telegram**, receipt `recorded`, same incident resolved/max shortfall 630000. Live params read; layout guard OK; script exited 0 and its anvil stopped (`curl --max-time 2 -s http://127.0.0.1:8547` then exit 7). This follows Marco's first-confirmed-round page decision. The spec's warn-then-page wording is not used to reject the authorized preserved precedent.

Config/notifier/compose names inspected: `MAINNET_RPC_URL`, `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`, `DISCORD_WEBHOOK_INFO`, matching the dispatch. No `config/`, `config.zip` or `.env*` path was read or written. No TEST_* credentials were required.

QA harness correction: the first batched row runner incorrectly used `Path.resolve()` on the venv interpreter symlink and invoked base Python, so those subprocesses exited 1 with `No module named pytest`. They were not credited; using the absolute venv path without resolving the symlink reproduced all rows above. This was an execution-path mistake, not a code failure. Orca inbox check also once returned `runtime_access_denied` (sandbox EPERM); the permission-cleared retry succeeded without restarting Orca.

## 4. Falsifiers

Both named source mutations were made on actual files, not expectations, then restored from original bytes in `finally`. Each red and green used a fresh bytecode prefix. After each restoration, the file diff was empty and `git status --porcelain` was `''`; no surviving mutation. V1B.4f changed `return ":".join((check, key))` to `return ":".join((key,))`; V1B.7f changed the IncorrectClusterState selector by one hex digit. Red/green counts are in the matrix. Final full suite and lint also passed after restoration.

Attempt 1's Appendix A was extracted unchanged, with only ROOT adjusted to this worktree, to `/tmp/qa-cp1b2/test_cp1b_independent.py`. Command: `PYTHONPYCACHEPREFIX=/tmp/qa-cp1b2/cache-appendixA reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider /tmp/qa-cp1b2/test_cp1b_independent.py`: **0; 12 passed**, 1 unknown-m5-marker warning from scratch collection not loading repository conftest. The code is already durable in [attempt 1](cp1b-attempt-1.md#appendix-a--independent-reproducible-probes).

| Independent falsifier | Measured outcome |
|---|---|
| Same-block framework events, reversed RPC log order, repeated step | PASS; observed log indexes 0,1,2 once, in chain order. |
| M5 update then 63 removals in the same block | PASS; confirmed, gap 630000, 63 matched. |
| Restart with prior nonzero counter, open page and event cursor | PASS; counter 1→2, one outbox row. |
| Missing log range vs clean | PASS in preserved M5 scenario; UNKNOWN/health. |
| Empty/malformed RPC log results `None`, `''`, `{}`, `'0x'` | 4 PASS; non-list RpcError, never treated as an empty log list. |
| Two checks return the same incident key across restart | PASS; 2 incidents, exactly 2 outbox rows. Required join mutation reddens this property. |
| Severity outside table (`emergency`) | PASS; Finding raises ValueError; raw enqueue raises KeyError and leaves no row. |
| Three original repair reproducers | 3 PASS on new head; each behavioral assertion fails on old code. |
| Legacy `critical`, `warning`, `info`, sent and unsent, each with pre-set delivery; migrate twice | 6 PASS; severity normalization, byte-equivalent state on second migration, identity/text/retry state retained; sent telegram receipt preserved/no re-delivery, pending rows deliver once on current route. |
| Partial parameter reload: one late getter reverts or returns no data while other getters return changed values | 2 PASS; 5 failed steps each expose no values, attribute/item/get all raise, event+inbox cursors and state unchanged; no extra check-error storm. Next step resumes with 50000/123456, re-prepares, consumes held event+inbox once, preserves one incident/outbox. |
| Actual OS inbox states: missing, permission denied, directory, existing empty file | 4 PASS; real chmod(000) open raises EACCES/PermissionError (restored mode 0600 in finally); first 3 report unhealthy/one health across 6 steps, empty file healthy/no health. |
| Inbox dedup/restart/window/flapping | PASS; 20 down steps + restart produce 1 health; next window produces exactly a second; 3 recoveries/new outages produce one per new outage, no repeats during each 5-step sustained outage. Persistent outage counter is 4. |
| Params health flapping/window | PASS; 3 fail/recover cycles and 5 repeated down steps each produce 1 health within the window; next window produces a second. |
| Parameter-change block reorged away, then continued steps | **FAIL; finding 1**; direct and prepare-cached consumers retain orphan value while healthy. |

Additional edges command: `PYTHONPYCACHEPREFIX=/tmp/qa-cp1b2/edge-cache reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider /tmp/qa-cp1b2/test_repair_edges.py`: **1; 14 passed, 1 failed**, zero skips. Source in Appendix B. Isolated reorg test: same command with `-k reorged_parameter_event`, **1; 1 failed, 14 deselected**. Inbox flapping emits one message per genuinely new outage (as documented); it does not emit on every retry step. This is distinct from a rate cap across separate outages and is acceptable for CP1B; operational flapping remains visible rather than hidden.

Regression falsification command: from `/tmp/qa-cp1b2/old`, invoke this QA worktree's absolute `reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider reconcile/tests/test_qa_regressions.py` with `PYTHONPYCACHEPREFIX=/tmp/qa-cp1b2/cache-old`: **1; 7 failed**. On frozen repaired head, same test file selection: **0; 7 passed**. Appendix C reproduces the scratch extraction; only the new regression file was added there, with no old-code modifications or installs.

## 5. Evidence-plane writes

CP1B **Status:** IMPLEMENTED — AWAITING QA → **QA FAILED**, anchored to `9a241bdfc495d439fa8ea532b43410365dfe5711` and this report. Wrote only `plans/monitoring-service/qa/cp1b-attempt-2.md`, that CP1B Status line in `plans/monitoring-service/plan.md`, and one appended event in `plans/monitoring-service/execution-log.md`. Ignored local `reconcile/.venv` and external scratch were authorized. Attempt 1 report and all implementation/config files remain unchanged. No repairs, production messages, mainnet transactions, push or next-checkpoint work. Coordinator owns a fresh repair and subsequent QA; CP2 remains gated.

## 6. Repo proof

QA lane base is the frozen tested head `9a241bdfc495d439fa8ea532b43410365dfe5711`; implementation base `4fbb9f1` naturally includes the implementer's changes and is not attributed to QA. Before evidence writes, `git status --porcelain` and `git diff --name-only 9a241bdfc495d439fa8ea532b43410365dfe5711` both printed nothing. Evidence staging/final outputs:

```text
$ git status --porcelain
M  plans/monitoring-service/execution-log.md
M  plans/monitoring-service/plan.md
A  plans/monitoring-service/qa/cp1b-attempt-2.md
$ git diff --name-only 9a241bdfc495d439fa8ea532b43410365dfe5711
plans/monitoring-service/execution-log.md
plans/monitoring-service/plan.md
plans/monitoring-service/qa/cp1b-attempt-2.md
```

No implementation file appears in either QA output. Both temporary source mutations were restored byte-for-byte before these measurements. The final evidence commit contains only the three authorized paths.

## Appendix A — minimal new finding reproducer

Adjust ROOT if using a different checkout; run from the worktree root with its venv. FakeChain's reorg helper drops logs/storage but does not rewind call stubs, so the probe explicitly installs and directly reads the canonical replacement getter; its failure is not caused by leaving orphan stubs in the fake chain.

```python
import pathlib, sys, json
ROOT=pathlib.Path('/Users/marco/orca/workspaces/ssv-network-monitor/ms-qa-cp1b-2')
sys.path[:0]=[str(ROOT/'reconcile'),str(ROOT/'reconcile/tests')]
from fakechain import FakeChain
from harness import make_worker, stub_params, VIEWS
from test_framework import Stub
from params import GETTERS,selector
from rpc import Chain
from eb_storage import u256
seen={'direct':[],'cached':[]}
class Cached(Stub):
    def prepare(self,ctx):self.threshold=ctx.params.liquidation_threshold_period
    def run(self,ctx,b):seen['cached'].append(self.threshold);return []
c=FakeChain();stub_params(c);c.mine()
checks=[Stub(check_id='direct',emit=lambda ctx,b:seen['direct'].append(ctx.params.liquidation_threshold_period) or []),Cached(check_id='cached')]
import tempfile
p=pathlib.Path(tempfile.mkdtemp(prefix='cp1b2-reorg-'))
_,w=make_worker(p,checks,chain=c,views=VIEWS)
w.step();fork=c.number
stub_params(c,liquidation_threshold_period=50000)
c.emit_event('LiquidationThresholdPeriodUpdated',[],u256(50000));c.mine();w.step()
c.reorg(fork)
stub_params(c,liquidation_threshold_period=42960);c.mine();w.step()
for _ in range(2):c.mine();w.step(force=True)
canonical=int.from_bytes(Chain(w.rpc,c.sealed).call(VIEWS,selector(GETTERS['liquidation_threshold_period'])),'big')
status=json.loads((p/'w.status.json').read_text())
print(json.dumps({'canonical':canonical,'seen':seen,'params_block':w.params.block,'checkpoint':w.checkpoint()['block'],'params_ok':w.params.ok,'status_healthy':status['healthy'],'runs':{k:w.db.meta('run:'+k) for k in seen}},indent=2))
assert seen['direct'][2:]==[canonical]*3 and seen['cached'][2:]==[canonical]*3
```

## Appendix B — independent repair-edge probes

Save as `/tmp/qa-cp1b2/test_repair_edges.py` (adjust ROOT); run the exact command in §4.

```python
import errno
import json
import pathlib
import sqlite3
import sys

ROOT = pathlib.Path('/Users/marco/orca/workspaces/ssv-network-monitor/ms-qa-cp1b-2')
sys.path[:0] = [str(ROOT/'reconcile'), str(ROOT/'reconcile/tests')]

import pytest
from fakechain import FakeChain, FakeClock, PROXY
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
    chain.mine();path.write_text(inbox_line(tx=tx,block=chain.sealed))
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
```

## Appendix C — extract old code without altering the QA worktree

This is the extraction performed before the red regression run; `.env*` paths are excluded.

```python
from pathlib import Path
import subprocess
s=Path('/tmp/qa-cp1b2'); old=s/'old'; old.mkdir(parents=True,exist_ok=True)
files=subprocess.check_output(['git','ls-tree','-r','--name-only','c21a51f','reconcile'],text=True).splitlines()
for f in files:
    if any(p.startswith('.env') for p in Path(f).parts): continue
    target=old/f; target.parent.mkdir(parents=True,exist_ok=True)
    target.write_bytes(subprocess.check_output(['git','show','c21a51f:'+f]))
(old/'reconcile/tests/test_qa_regressions.py').write_bytes(Path('reconcile/tests/test_qa_regressions.py').read_bytes())
```

## Appendix D — M5 assertions, not only names

Ran `reconcile/.venv/bin/python /tmp/qa-cp1b2/assertion_diff.py`, exit **0**. Compared the actual diff against `4fbb9f1`, then parsed both files and required every original assert AST to be the corresponding prefix in its original function, allowing only `critical`→`page`, `warning`→`warn`, and the SQL table move `incidents`→`m5_incidents`. All numeric thresholds, benign-case assertions, incident IDs and parameterization remain. The added assert checks the framework's resolved incident. Helper edits invoke `run_checks(force=True)`/M5 `last_evals` instead of sweep, and Capture accepts the route. The 26 functions expand to 30 original cases due to the 5-way conversion parameterization; 2 migration cases yield the 32 selected M5 items.

Comparison code (extracted from attempt 1 Appendix B, not edited):

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

Measured output (all names present and all original assertions retained):

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
