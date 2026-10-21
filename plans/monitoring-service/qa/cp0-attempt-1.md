PASS WITH NOTES

# CP0 independent QA — attempt 1

Frozen head: `9772b2da2acd427aeaf6687c27656ee89bd895d8` (`plan/monitoring-service`). QA branch: `mtabasco/ms-qa-cp0`. Task / dispatch: `task_16bd4412f92a` / `ctx_4bd11bbdbf4e`. Measured 2026-10-21, approximately 10:33–10:43 UTC.

## Findings

No blocking implementation defect found. The plan's recorded event-0007 explicitly accepts the pre-existing parallel-test flake and names the serial variant as a separate deterministic control; this verdict preserves that exception rather than silently treating every invocation as green.

1. **Known red, non-blocking under event-0007; owner CP3:** the parallel Rust row can fail in unchanged static mock expectations. Reproducer: `cargo test --locked --test integration -- filters::evm::filter`. My first, sandboxed run exited **101**, **16 passed / 2 failed**: `test_handle_match_with_duplicate_event_signatures` panicked at `tests/integration/mocks/services.rs:20:1`, `MockTriggerExecutionService::new(?, ?): No matching expectation found`; `test_monitor_error_cases` panicked at `tests/integration/filters/evm/filter.rs:515:59`, `All RPC URLs failed to connect`. Expected: 18 passed. The latter test constructs an EVM client using fixture hosts `eth.drpc.org` and `ethereum-rpc.publicnode.com`, so it has a real network dependency. After the sandbox-related connection failure I reran the **same command outside the sandbox**, exit **0**, **18 passed**; then the separately named `--test-threads=1` control outside the sandbox exited **0**, **18 passed**. These are all three suite invocations; no run was discarded and there was no retry loop. The receipt regression test passed in all three. The mockall failure independently reproduces the implementer's known-red signature; the historical pristine-main flake rate remains an unconfirmed claim, below.

2. **Low, verification blind spot; owner CP1A:** V0.6 detects existing `plain` values but cannot detect missing credentials. Reproducer in an external scratch copy: delete `['telegram_oracle_failed']['config']['token']` from `deploy/config/triggers/telegram_oracle_failed.json`, then run C6 below. Expected for a complete credential gate: red. Actual: exit **0**, empty output. An independent required-key assertion raises `KeyError: token`. Restoring the original bytes leaves scratch porcelain empty. This is a gate limitation, not a missing field in the submitted templates: I parsed all 51 JSON files and independently verified credentials for **24 triggers**, `${TELEGRAM_CHAT_ID}` for all **9 Telegram** triggers, `MAINNET_RPC_URL` for the network, and trigger references for all **26 monitors**. All passed. CP1A should retain required-field/schema checks alongside the plain-value scan.

3. **Low, verification command portability; owner coordinator:** `git check-ignore -v config.zip .local-dry` exits **0** but prints only `config.zip` when `.local-dry` does not exist. Expected matrix output: both paths. Actual exact output is one line. Reproducer: run that command in this fresh child; the explicit directory form `git check-ignore -v config.zip .local-dry/` prints both `.gitignore:59:config.zip` and `.gitignore:60:.local-dry/`, exit **0**. The ignore rule is correct; the exact command alone is incomplete evidence in fresh children. No user-owned directory was created or modified.

## Claims I could NOT confirm

- The exact V0.9 path is unavailable in a fresh child: `reconcile/.venv/bin/python --version` and the exact test command both fail **127** (`no such file or directory`). No install or local venv write was made. The coordinator explicitly authorized using the existing main-checkout interpreter with bytecode/cache writes disabled, from **this** worktree, recording the exact row as environment-unavailable plus the equivalent run. That equivalent independently passed **30 tests** (0.20 s), exit **0**. Before running it I printed and asserted the resolved `__file__` paths of `eb_model`, `eb_storage`, `eb_events`, and `eb_reconcile`; all four are under `/Users/marco/orca/workspaces/ssv-network-monitor/ms-qa-cp0/reconcile/`, not the main checkout. Interpreter location is not source location.
- The historical intermediate **428 mode reverts / exactly five uncommitted content files** cannot be reconstructed from a committed child. Current mode count is zero and current numstat is empty. Independent base-to-frozen comparison of the two Rust paths reproduces **16/1** and **62/0**; `.gitignore` contains the preserved sqlite rule. The coordinator explicitly excluded its uncommitted `.dockerignore`, `Dockerfile.production`, and `.env copy.example` material from this tree, so their absence is not a defect. I did not infer a failed preservation check from their absence.
- I did not reproduce the implementer's **18 parallel runs / 60 pristine-main runs / 6 serial runs / 40+30 direct-binary loops**, or their frequencies. I ran the three suite invocations recorded above and a genuine red/restored-green receipt-test mutation. No statistical or pristine-main claim is credited as independently measured here.
- I did not reread user-owned production `config/`, `config.zip`, `.env*`, or `.local-dry/`, so byte identity to the original live config and fixed-string comparisons against its real secrets remain implementer claims. I verified the committed templates directly, including parsed structure, references, and the tracked-file pattern scan with its injected-token control.
- The handoff's **82 tracked SSV files** is stale at the dispatched head: I measured **84**, entirely explained by `plans` growing from 8 to 10. Per-path counts are below. F5 is independently measured as **30 passed**; I did not edit the coordinator's F ledger because it is outside QA write scope.

## Preflight and command environment

Read the QA contract first, then the scoped plan sections/required inputs, implementer handoff and attempts, and named deliverables. `git rev-parse HEAD` exactly equaled the frozen hash before any write; `git status --porcelain` was empty. Free disk: **367 GiB**, 17% used. `anvil` resolves to `/Users/marco/.foundry/bin/anvil`; no CP0 row requires a running anvil service. No live fork, transaction, install, push, or alert delivery was performed.

Every tool/test shell started with `export PATH=$HOME/.foundry/bin:$PATH` where required. Rust commands used `CARGO_TARGET_DIR=/tmp/ms-qa-cp0-attempt-1/target` to keep generated files outside the repository. The built executable exists and is executable at `$CARGO_TARGET_DIR/release/openzeppelin-monitor`; there is no repo-local `target` write. Build warning: future-incompatible `trie-db v0.30.0`; not a build error.

Exact reusable commands (table pipe escaping removed):

```sh
# C3: git-grep's exit 1 means the expected zero matches; exit 0 means a secret-pattern match.
git grep -nE 'discord\.com/api/webhooks/[0-9]+/|[0-9]{8,10}:AA[A-Za-z0-9_-]{30,}' -- . ':!tests/integration/fixtures'
# C6
jq -r '.. | objects | select(.type? == "plain") | .value' deploy/config/triggers/*.json deploy/config/networks/*.json
# C9 equivalent, authorized by coordinator; cwd is the frozen QA worktree root.
PYTHONDONTWRITEBYTECODE=1 /Users/marco/ssv/repos/ssv-network-monitor/reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider reconcile/tests
```

## Verification matrix

| V-ID | Command run | Exit | Independently measured outcome |
|---|---|---|---|
| V0.1 | `zsh -lic 'for t in cargo rustc uv docker anvil; do command -v $t; done'` | 0 | PASS: all five resolve; cargo/rustc under `~/.cargo/bin`, uv `~/.local/bin`, docker `~/.docker/bin`, anvil `~/.foundry/bin`. |
| V0.2 | `git diff --summary \| grep -c "mode change"`; `git diff --numstat` | 1; 0 | PASS for current state: count **0**, numstat empty; grep exit 1 is normal for zero matches. Historical five-file intermediate state NOT REPRODUCIBLE after commits; authorized exclusions and surviving Rust contents discussed above. |
| V0.3 | C3 | 1 | PASS: **0 rows**, expected no-match exit. |
| V0.3f | Runtime dummy token in a tracked scratch file, C3, remove from scratch index and disk, C3 | 0 red; 1 restored | PASS: **1 row** on injection, **0 rows** after restore; scratch and real worktree porcelain empty. |
| V0.4 | `git check-ignore -v config.zip .local-dry`; explicit directory form with `.local-dry/` | 0; 0 | PASS WITH NOTE: exact command shows only archive in absent-directory child; slash form confirms **both ignored**. |
| V0.5 | `git ls-files reconcile plans docs/ssv-specs deploy scripts/local-dry-run.sh docker-compose.local.yaml docker-compose.reconcile.yaml \| wc -l`; repeat per path; `git ls-files reconcile/.venv reconcile/data` | 0 each | PASS: **84 total**; respectively **15, 10, 1, 55, 1, 1, 1**. `.venv` and `data`: **0** each. |
| V0.6 | C6 | 0 | PASS: empty output; independent full JSON/credential/reference checks pass. Missing-field gate limitation is finding 2. |
| V0.7 | `cargo build --release --locked` | 0 | PASS: executable exists under external target dir; **1m13s** build. |
| V0.8 | `cargo test --locked --test integration -- filters::evm::filter` | 101 sandbox; 0 outside sandbox | PASS WITH KNOWN RED per event-0007: **16 passed / 2 failed**, then environment-cleared exact run **18 passed / 0 failed**. Target receipt test `ok` in both. No unreported runs. |
| V0.8 serial control | `cargo test --locked --test integration -- filters::evm::filter --test-threads=1` outside sandbox | 0 | PASS: **18 passed / 0 failed**; separately named deterministic control, not substituted silently for the required command. |
| V0.9 | `reconcile/.venv/bin/python -m pytest -q reconcile/tests`; C9 equivalent | 127 exact; 0 equivalent | Exact row **ENVIRONMENT-UNAVAILABLE**, no child venv / installs forbidden. Authorized equivalent **PASS: 30 passed**, with worktree import paths verified. No code-regression inference from missing environment. |
| V0.10 | `git log --oneline main..plan/monitoring-service` | 0 | PASS: **10 commits**, including hygiene `69b905d`, SSV material `3b62247`, templates `325ebfb`, Rust `0ced62d`, and final frozen status `9772b2d`. |
| V0.11 | `grep -n "DECIDED 2026-10-21" docs/ssv-specs/monitoring-service-spec.md` | 0 | PASS: **5 lines**, 445–449; D3, D2, D4, D6, D5 all resolved with agreed values. |
| V0.12 | `jq .worktree .claude/settings.json`; `jq '.permissions.allow \| length' .claude/settings.json`; jq selection of allow entries containing `git push` | 0 each | PASS: `{"baseRef":"head"}`, **36** allow entries, **0** push allow entries; explicit deny `Bash(git push:*)`. |

No required row is silent. Environment-unavailable and known-red invocations are explicit; their authorized equivalent/control outcomes are separate. CP1A/CP1B and later checkpoint tests, runtime rendering, production routes, and live services were not run as CP0 evidence.

## Falsifiers

All file mutations ran in `/tmp/ms-qa-cp0-attempt-1/source`, extracted from `git archive 9772b2da2acd427aeaf6687c27656ee89bd895d8`, initialized as a separate scratch git repository with its snapshot committed. No implementation path in the QA worktree was mutated. Restore checks used `git -C /tmp/ms-qa-cp0-attempt-1/source status --porcelain` and `git status --porcelain` in the QA root, both empty before evidence writes.

### Required V0.3f — tracked secret-pattern control

In the scratch git repository, created `qa-scratch-token.txt` at runtime using:

```sh
printf 'TOKEN=1234567890:AA%s\n' "$(printf 'x%.0s' {1..34})" > qa-scratch-token.txt
git add qa-scratch-token.txt
# Run C3: exit 0, exactly 1 matched row; token value is intentionally not retained here.
git update-index --force-remove qa-scratch-token.txt
# Unlink the scratch file, then run C3: exit 1, 0 matched rows.
git status --porcelain
```

I used Python `Path.unlink()` for the ordinary scratch file. The post-restore status printed nothing; the real QA worktree also remained clean. The scratch scanner baseline before injection was exit 1 / 0 rows. The literal dummy token was never written into this report or any tracked QA document.

### Own Rust receipt control — one-token mutation, genuine test red

In the external snapshot only, changed the sole occurrence of `should_fetch_receipt && touches_monitored_address` to `should_fetch_receipt || touches_monitored_address` in `src/services/filter/filters/evm/filter.rs`. Ran:

```sh
CARGO_TARGET_DIR=/tmp/ms-qa-cp0-attempt-1/target cargo test --locked --test integration -- test_failure_monitor_only_fetches_receipts_for_monitored_transactions --test-threads=1
```

Mutated run: **exit 101, 0 passed / 1 failed**; the assertion compared **3 fetched transaction hashes** with **1 expected**. Restored the original file bytes in `finally`; scratch porcelain was empty. Restored run: **exit 0, 1 passed / 0 failed**. This establishes that the regression test actually observes transport calls and can reject the defect. Logs: external scratch `rust-falsifier-red.log` and `rust-falsifier-restored.log`.

### Own template control — omitted required input

In the scratch snapshot only:

```python
import json
from pathlib import Path
p = Path('deploy/config/triggers/telegram_oracle_failed.json')
original = p.read_bytes()
try:
    obj = json.loads(original)
    del obj['telegram_oracle_failed']['config']['token']
    p.write_text(json.dumps(obj))
    # C6 exits 0 with no output: false-green demonstrated.
    assert json.loads(p.read_text())['telegram_oracle_failed']['config']['token']['type'] == 'environment'
    # This independent assertion raises KeyError: token, as expected.
finally:
    p.write_bytes(original)
```

C6 remained green as described in finding 2; the independent credential assertion reddened. The unmodified snapshot passes the independent assertion; restored scratch porcelain was empty.

### Own M5 control — corrupt state with an already-open incident

Used the frozen worktree's existing fake chain and unmodified worker, with temporary SQLite storage outside the repository. This extends the suite's corruption control with **prior nonzero incident state**, **repeat evaluation**, and **restoration**. Command:

```sh
PYTHONDONTWRITEBYTECODE=1 /Users/marco/ssv/repos/ssv-network-monitor/reconcile/.venv/bin/python /tmp/ms-qa-cp0-attempt-1/m5-prior-incident-control.py
```

Outcome: exit **0**. Genuine abnormal-chain state produced **CONFIRMED → UNKNOWN**, a health alert, the **same open incident id**, no resolution, and no duplicate critical on a repeat sweep; restoring the storage word produced **CONFIRMED** with no extra critical. Complete control script is retained below for reproduction.

### Own copied oracle-filter control — missing input and repeated runs

Command:

```sh
PYTHONDONTWRITEBYTECODE=1 /Users/marco/ssv/repos/ssv-network-monitor/reconcile/.venv/bin/python /tmp/ms-qa-cp0-attempt-1/oracle-template-controls.py
```

Outcome: exit **0**. A divergent vote followed by agreement in the same process gives **false → true**, so dissent state does not persist between invocations. A missing RPC result (`None`) raises **TypeError** rather than printing clean; absent `MAINNET_RPC_URL` raises **KeyError** before a clean result. RPC data was mocked locally; no alerts or network requests were sent. No source mutation or repository writes occurred. Complete control script follows.

## Evidence-plane writes

Changed only the CP0 `**Status:**` line in `plans/monitoring-service/plan.md` from IMPLEMENTED — AWAITING QA to **VERIFIED**, naming the frozen hash, this report, the accepted known flake, and the authorized Python equivalent. Wrote this report and appended exactly one QA event to `plans/monitoring-service/execution-log.md`. No implementation, config, compose, environment, or user-owned ignored path was changed. Commit is local to the QA worktree branch; no push.

## Repo proof

Before evidence-plane writes, both commands printed no paths:

```sh
git status --porcelain
git diff --name-only 9772b2da2acd427aeaf6687c27656ee89bd895d8
```

Before committing the evidence, the staged proof is:

```text
M  plans/monitoring-service/execution-log.md
M  plans/monitoring-service/plan.md
A  plans/monitoring-service/qa/cp0-attempt-1.md
```

`git diff --name-only 9772b2da2acd427aeaf6687c27656ee89bd895d8`:

```text
plans/monitoring-service/execution-log.md
plans/monitoring-service/plan.md
plans/monitoring-service/qa/cp0-attempt-1.md
```

No implementation file appears in either output. The lane base for this QA proof is the dispatch's frozen head, not the project's original `main` base whose implementation additions are intentionally under review. After the local evidence commit, final porcelain must be empty and the same base comparison must still name exactly these three evidence files; the actual final command output is also printed before completion delivery.

## Reproduction scripts

Place these scripts at the external paths used above. Their only writable state is temporary SQLite or process-local input. The oracle script expects the external frozen snapshot at `/tmp/ms-qa-cp0-attempt-1/source`, as described in Falsifiers.

### M5 prior-incident control

```python
import pathlib,sys,tempfile
root=pathlib.Path.cwd()
sys.path[:0]=[str(root/'reconcile/tests'),str(root/'reconcile')]
from test_worker import poc_setup,tick,ev_for,keys,OWNER,OPS
import eb_model as M
from eb_storage import cluster_eb_slot,cluster_id
with tempfile.TemporaryDirectory(prefix='cp0-m5-state-') as d:
 chain,w,ssv=poc_setup(pathlib.Path(d));s=chain.number;chain.mine();dummy=keys(63,55_000)
 ssv.register(OWNER,OPS,dummy);ssv.commit(s);ssv.update(OWNER,OPS,2048);ssv.remove(OWNER,OPS,dummy)
 e=ev_for(tick(w));assert e.status==M.CONFIRMED
 before=w.db.conn.execute('SELECT id,state FROM incidents').fetchall();assert len(before)==1 and before[0]['state']=='open'
 critical=len([s for s,_,_ in w.notifier.sent if s=='critical'])
 # Corrupt only on-chain snapshot at a new sealed block, preserving nonzero DB and old incident.
 chain.mine();slot=cluster_eb_slot(cluster_id(OWNER,OPS));old=chain.storage(slot,chain.number)
 chain.write(slot,old+1);chain.mine()
 e=ev_for(tick(w));assert e.status==M.UNKNOWN,e.status
 assert any(k.startswith('health:unknown') for k in w.notifier.keys())
 after=w.db.conn.execute('SELECT id,state FROM incidents').fetchall();assert [(r['id'],r['state']) for r in after]==[(r['id'],r['state']) for r in before]
 tick(w);assert len([s for s,_,_ in w.notifier.sent if s=='critical'])==critical
 assert not any(':resolved:' in k for k in w.notifier.keys())
 print('M5 state corruption: CONFIRMED -> UNKNOWN; same incident remains open; health alert; repeat adds no critical and no resolution: PASS')
 # Restore raw storage word at next block; observe original incident without re-page.
 chain.write(slot,old);chain.mine();e=ev_for(tick(w));assert e.status==M.CONFIRMED
 assert len([s for s,_,_ in w.notifier.sent if s=='critical'])==critical
 print('M5 restored state: CONFIRMED, same incident, no extra critical: PASS')
```

### Oracle template control

```python
import contextlib,importlib.util,io,json,os,pathlib,sys
root=pathlib.Path.cwd();path=root/'deploy/config/filters/oracle_dissent_check.py'
spec=importlib.util.spec_from_file_location('qa_oracle_filter',path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
os.chdir('/tmp/ms-qa-cp0-attempt-1/source/deploy')
root_hash='0x'+'11'*32;snapshot=hex(1000)
match={'monitor_match':{'EVM':{'network_slug':'evm_ethereum_mainnet','transaction':{'blockNumber':hex(1001)},'logs':[{'address':'0x'+'aa'*20,'topics':[m.ROOT_COMMITTED,root_hash,snapshot]}]}}}
os.environ['MAINNET_RPC_URL']='http://qa.invalid'
original=m.get_logs_range

def call(votes):
 m.get_logs_range=lambda *args,**kwargs:votes
 saved=sys.stdin;sys.stdin=io.StringIO(json.dumps(match));out=io.StringIO()
 try:
  with contextlib.redirect_stdout(out),contextlib.redirect_stderr(io.StringIO()):m.main()
 finally:sys.stdin=saved
 return out.getvalue().splitlines()[-1]
assert call([{'topics':[m.WEIGHTED_ROOT_PROPOSED,'0x'+'22'*32,snapshot],'data':'0x'+('00'*64)+format(3,'064x'),'transactionHash':'0x123'}])=='false'
assert call([{'topics':[m.WEIGHTED_ROOT_PROPOSED,root_hash,snapshot]}])=='true'
print('Oracle dissent -> agreement repeated in same process: false -> true, PASS')
try:
 call(None)
 raise AssertionError('missing RPC result was treated as clean')
except TypeError:print('Oracle missing RPC result: raises TypeError, not clean, PASS')
del os.environ['MAINNET_RPC_URL']
try:
 call([])
 raise AssertionError('missing environment was treated as clean')
except KeyError:print('Oracle missing RPC variable: raises KeyError before clean output, PASS')
finally:m.get_logs_range=original
```
