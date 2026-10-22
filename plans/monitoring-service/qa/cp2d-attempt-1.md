FAIL

# CP2d independent QA — attempt 1

Frozen head: `588888bdcb7c215f798a23e86cc9475a85b22995` (lane `mtabasco/ms-cp2d`). QA branch: `mtabasco/ms-qa-cp2d`. Task `task_913d3dbc2cdc`, Dispatch `ctx_c6554db292c9`. Measurements: 2026-10-21, approximately 14:12–14:23 UTC. This report measures M1/M2 and does not repair implementation.

## 1. Findings

The independent suite finishes **6 failed, 23 passed**, exit **1**. The six cases expose four issues below. The committed suite passes once its localhost permission is supplied; passing committed tests do not cover these failures.

Reproduction setup: run the extraction command in Appendix A from this checkout root. Each command below uses that extracted, durable copy of the tests; no implementation mutation is needed.

### F1 — HIGH: empty cluster API results are interpreted as recovery, including in reorg runs (M1 and M2)

Commands (each exits 1):

```sh
PYTHONDONTWRITEBYTECODE=1 reconcile/.venv/bin/python -m pytest -q -s /tmp/cp2d-qa/test_independent.py -k 'empty_api or api_only_key_reorg'
```

Expected: an empty/missing inventory is UNKNOWN/error, cannot prove a stored cluster disappeared or that migration now fits; keep the incident and, when unevaluated during reorg, retain the retry marker. Actual: **3 failed, 1 passed** in this selection. `test_m2_empty_api_not_clean` starts with 2,981 ETH validators and a 20-validator legacy cluster against limit 3,000, then returns a syntactically valid API response with `clusters: []`: `hard:1` resolves although on-chain counts are unchanged. A health message `m2-inventory` is emitted, but the hard incident is still falsely closed. `test_m2_empty_api_during_reorg_not_clean` repeats this during reorg and also clears `reorg:m2`.

`test_m1_api_only_key_reorg_missing_inventory_is_unknown[empty]` starts with an on-chain cluster whose API tuple fails its hash and has no stored log. After a reorg and an empty API response, the open unknown/warn incident resolves and `reorg:m1` clears, without reading the persisted cluster's storage to prove absence. Its `[error]` twin raises through the check-error path, keeps the incident and marker, and **passes**. Thus error handling itself works; empty data bypasses it. This violates the inherited missing-input and complete re-evaluation contracts. Relevant code: M1 `_evaluate` treats absence from inventory as clean; M2 derives `on_op` only from the now-empty inventory, reports mismatch health, then emits a resolve finding for `hard:1`.

### F2 — HIGH: same-block repair followed by re-poison stays warn instead of page (M1)

```sh
PYTHONDONTWRITEBYTECODE=1 reconcile/.venv/bin/python -m pytest -q -s /tmp/cp2d-qa/test_independent.py::test_m1_repair_then_repoison_same_block_pages
```

Exit 1. Open a poison/warn, then execute a successful zero-value repair deposit and a new poisoning transaction in the same next block. Expected: repair memory and `re_poisoned=true`, followed by page escalation under the existing incident. Actual: `[(warn, open), (warn, update)]`, `re_poisoned=false`, and the detection table contains two poison rows with no intervening repair. `_scan` keeps only the final transaction per cluster per block and discards the earlier successful repair. The separate-block repair/re-poison test passes, so this is specifically the ordering boundary the committed test misses.

### F3 — HIGH: orphaning a settlement fails to re-evaluate the surviving persisted revert (M2)

```sh
PYTHONDONTWRITEBYTECODE=1 reconcile/.venv/bin/python -m pytest -q -s /tmp/cp2d-qa/test_independent.py::test_m2_reorg_unclears_surviving_revert
```

Exit 1. Ingest `reactivate` → `ExceedValidatorLimitWithData(1)` and open `revert:1`; lower ETH count on the next block so it settles; orphan only that settlement block and restore the original over-limit counts. Expected: the canonical inbox revert is re-evaluated/reopened, because its clearance left the chain. Actual: only `early:1` and `hard:1` reopen; the persisted canonical revert remains `cleared_block=1029` with no `revert:1` incident. Its original inbox block (1028) survives. The event cursor has already consumed it, and M2 selects only uncleared rows without rewinding `cleared_block` above the ancestor. This violates README §6.2 for **every persisted key**, even one whose incident was resolved before the reorg. FakeChain does not rewind call stubs, so the reproducer explicitly reinstates the canonical counts after reorg; this is not a stale stub artifact.

### F4 — MEDIUM: the default repair-candidate cap loses an available earliest validating tuple (M1)

```sh
PYTHONDONTWRITEBYTECODE=1 reconcile/.venv/bin/python -m pytest -q -s /tmp/cp2d-qa/test_independent.py::test_m1_earliest_repair_before_64_other_candidates
```

Exit 1. One receipt carries a validating tuple at log 0 followed by 66 distinct stale snapshots, with stale last. Expected: repair tuple from log 0. Actual: warn with `repair_tuple=null`, `repair_log_index=null`; the tuple is present and validates against the fake chain's storage. `_repair_event` searches backward and truncates to 64 distinct candidates before it reaches the validating tuple. This synthetic ordering stress does not claim that such a receipt has been observed on mainnet. The default configuration has no documented semantic exception to the spec's repair identification requirement. Eight independent duplicate-valid-tuple tie cases at smaller sizes pass and choose the earliest log correctly.

## 2. Claims I could NOT confirm

- General reorg completeness, same-block re-poison severity, and unconditional repair-tuple identification: falsified by F1–F4. No implementation repair was attempted.
- A universal claim that no poisoned receipt exists anywhere on mainnet: this QA authenticates the twelve supplied receipts, not an exhaustive historical scan. They are all benign; the implementer's absence claim is consistent with the spec baseline but is not independently proved here.
- Real reentrancy exploitation / fork reproduction: **NOT RUN, explicitly deferred DEF-3**, not required by this dispatch. The worker record explicitly says it remains deferred and makes no completed-PoC claim.
- Exact wall-clock 24-hour velocity under missed slots: not independently established. The implementation and tests use configurable `velocity_window_blocks` (default 7,200), a 12-second-block approximation. Measured boundary/window checks below establish block-window behavior, not exact timestamp-window equivalence.

Receipt-evidence judgement: the twelve authenticated real multi-event receipts are adequate evidence for ordinary receipt decoding and the validate/silent branch. The eleven synthetic poison replays are clearly labeled synthetic; their stale tuple is the transaction's actual pre-call Cluster argument, and the canonical/incorrect-state Views answers reproduce from archive RPC. Together with independent storage-backed synthetic tests, that is adequate **unit-level evidence** of the intended stale-final-tuple predicate and basic repair identification; a real poisoned receipt would additionally establish the actual callback/event/storage sequence and production reachability of the no-validate path. It would not cure F1–F4. Requiring a malicious-owner fork PoC here would override the explicit DEF-3 deferral, so this verdict does not depend on one.

## 3. Verification matrix

Preflight: `git rev-parse HEAD` = frozen head above; initial `git status --porcelain` empty. Disk: 352 GiB available. `uv venv reconcile/.venv --python 3.12` and `uv pip install --python reconcile/.venv/bin/python -r reconcile/requirements-dev.txt` each exit 0; Python **3.12.15**, seven pinned dependencies installed from the authorized requirement set. `PATH=$HOME/.foundry/bin:$PATH`; anvil **1.8.5**, present. No service/fork needed by the owned rows. No secret, production config, signed transaction, or real alert transport used.

Frozen-file preflight: `git diff --quiet plan/monitoring-service -- reconcile/tests/harness.py reconcile/tests/test_framework.py reconcile/tests/test_worker.py` exits 0; the same command against `500abe4` exits 0. `git diff --name-only 500abe4 HEAD -- reconcile` contains only lane deliverables (M1/M2 modules, their tests and receipts, fixtures_cluster, config example and the lane import). No other frozen CP1B file changed. These are exactly the centrally applied `bd531b4` + `500abe4` patch, not extra worker edits.

| V-ID | Exact command | Exit / measured result | Verdict |
|---|---|---|---|
| V2d.1 / V2x.1 M1 | `reconcile/.venv/bin/python -m pytest -q reconcile/tests/test_m1.py` | 0; 38 passed | Committed scenarios pass; FAIL overall on F1/F2/F4 |
| V2d.1 / V2x.1 M2 | `reconcile/.venv/bin/python -m pytest -q reconcile/tests/test_m2.py` | 0; 29 passed | Committed scenarios pass; FAIL overall on F1/F3 |
| V2d.2 / V2x.2 | `reconcile/.venv/bin/python -m pytest -q reconcile/tests/test_m1.py reconcile/tests/test_m2.py -k thresholds_from_chain` | 0; 2 passed, 65 deselected | PASS |
| V2d.3 / V2x.3 | `reconcile/.venv/bin/python -m pytest -q reconcile/tests/test_m1.py reconcile/tests/test_m2.py -k restart` | 0; 4 passed, 63 deselected | PASS |
| V2d.5 / V2x.5 | `reconcile/.venv/bin/python /tmp/cp2d-qa/falsify.py` (Appendix C) | 10 mutations: each targeted suite exit 1 under mutation, 0 restored; details below | PASS |
| V2d.6 / V2x.6 suite | `reconcile/.venv/bin/python -m pytest -q reconcile/tests` | 1 in sandbox: 330 passed, one localhost bind EPERM | Environment-limited; resolved by next row |
| V2d.6 localhost control | `reconcile/.venv/bin/python -m pytest -q reconcile/tests/test_framework.py::test_metrics_endpoint_exposes_required_series` | 0, 1 passed with escalated localhost permission | PASS; combined coverage 331/331, no code change |
| V2d.6 lint | `reconcile/.venv/bin/ruff check reconcile` | 0; All checks passed | PASS |
| V2d.7 recorded replay | `reconcile/.venv/bin/python -m pytest -q reconcile/tests/test_m1_receipts.py` | 0; 24 passed = count check + 12 faithful + 11 synthetic | PASS for this bounded corpus; repair stress F4 fails |
| V2d.7 authenticity LIVE | `reconcile/.venv/bin/python /tmp/cp2d-qa/live_auth.py https://eth-pokt.nodies.app` | 0; 12/12 receipts, logs/block hashes/tuple fields, 47 recorded eth_calls and 24 storage words reproduced | PASS |
| V2d.7 input authenticity LIVE | `reconcile/.venv/bin/python /tmp/cp2d-qa/verify_input.py` | 0; 11/11 synthetic stale tuples match real tx input; one contract-target tx N/A | PASS |
| Inherited reorg scenarios | `reconcile/.venv/bin/python -m pytest -q reconcile/tests/test_m1.py reconcile/tests/test_m2.py -k reorg` | 0; 10 passed, 57 deselected | Bounded scenarios pass; completeness FAIL F1/F3 |
| Independent adversarial suite | `PYTHONDONTWRITEBYTECODE=1 reconcile/.venv/bin/python -m pytest -q -s /tmp/cp2d-qa/test_independent.py` | 1; 6 failed, 23 passed; two harmless unregistered-marker warnings because the file is outside the repo conftest tree | FAIL |
| V2x.4 | Not owned by CP2d; no separate repeat | The full suite ran and passed its 350-cluster / two-operator-calls test | No required row skipped |

Budget: ran the full suite once, then only reran the permission-blocked metrics case. No redundant full-suite or broad mainnet-history/fork run. Targeted repeats were needed to add new independent scenarios and retain reproducible output. The initial falsifier driver halted before applying F-M1d because its proposed substring matched twice; narrowed to the exact rewind branch and ran the remaining mutations, preserving the first three successful measurements. This was a QA driver guard, not a product defect.

### Spec-derived trigger coverage (independently enumerated from spec M1/M2)

| Trigger / severity | Executed trigger and benign twin | Result / limitation |
|---|---|---|
| M1 group >=2, stale last, warn + same-receipt repair | `poisoned_receipt_warns_with_repair_tuple`; `multi_event_receipt_that_validates_is_silent`, `single_event_transactions_are_not_validated` | Pass; independent eight event-kind decoding/earliest-tie cases and two-clusters-in-one-tx pass; capped search F4 fails |
| M1 our updateClusterBalance IncorrectClusterState, warn | `inbox_incorrect_cluster_state_from_our_sender_warns`; benign stranger/other_error/other_function | Pass |
| M1 our liquidate IncorrectClusterState, warn | `inbox_liquidator_revert_on_poisoned_cluster_feeds_the_same_incident`; same sender/error/function twins | Pass, same incident and material-update message |
| M1 re-poison after repair, page | `re_poison_after_repair_pages`, `repair_and_re_poison_in_one_batch_pages`; `second_poisoning_without_repair_is_not_a_re_poison` stays warn | Pass across blocks; same-block repair then poison F2 fails |
| M1 poisoned + liquidatable, page | `poisoned_and_liquidatable_pages`; ordinary nonliquidatable poison warns, benign valid receipts silent | Pass; live-parameter fake-chain liquidation-period change escalates |
| M1 inventory hash mismatch/unknown, warn (inherited lane requirement) | `inventory_hash_mismatch_is_unknown_warn_and_valid_api_tuple_is_silent[False/True]` | Pass; empty inventory reorg F1 fails |
| M2 hard active legacy cluster eth+cluster > live limit, warn | `hard_blocked_active_legacy_cluster_warns`; exact-fit and liquidated legacy twins | Pass; dynamic limit and custom non-3000 tests pass; missing membership F1 fails |
| M2 early headroom-legacy < configured 900, private info / public warn | both `early_headroom_below_900` variants; `early_headroom_exactly_900_is_silent`; public transition | Pass; independent threshold 11 and gap ==11 silent pass |
| M2 registrations > headroom, info | `velocity_registrations_above_headroom_is_info`; equal-headroom twin; `velocity_counts_only_the_window` | Pass in block-window model; timestamp qualification above |
| M2 migrateClusterToETH ExceedValidatorLimitWithData, warn | committed inbox warn/settles; other_error / other_function twins | Pass |
| M2 reactivate ExceedValidatorLimitWithData, warn | independent `each_inbox_function_warns[reactivate]`; committed benign reactivate-other_error twin | Pass; canonical persisted revert settlement reorg F3 fails |

Additional measured attacks: depth-1 and beyond-confirmation reorgs; deletion of poisoning, repair, registration burst and inbox trigger blocks; independent deletion of M1 page triggers and M2 hard/early count changes; shorter replacement chains for both monitors; scan-before-run restart, rewind-before-run restart, repeat-run idempotency; failed/empty Views/operator RPC responses; unresolved inbox reverts; no-baseline first sweep; same-block ordering; reversed RPC log arrival; two clusters in one transaction; duplicate validating repair tuples; inventory hash mismatch. The API exception / API-only-key reorg case produces health plus a raised check error and keeps the marker (pass); an empty inventory produces false resolve findings and clears the marker (fail). These attacks include a complete run with no surviving trigger logs, rather than assuming a health-only or empty check result is adequate.

Direct independent check calls for both monitors return warn findings without changing incidents, outbox counts or notifier sends. Independent M1 cadence checks pass at warn 24h and page 6h boundaries; the committed full suite also runs info-once, severity routing, record-mode and restart tests. M1 warn/page go to Telegram, private M2 early/velocity info goes to Discord, public early warns to Telegram. No real message was sent.

### Archive receipt authenticity measurements

Endpoint for every PASS below: **https://eth-pokt.nodies.app**. Each tx was fetched independently with `eth_getTransactionReceipt(tx_hash)`; all committed fields (`address`, `topics`, `data`, `blockHash`, `blockNumber`, `transactionHash`, `transactionIndex`, `logIndex`) matched. Decoded last tuples, cluster IDs and operator arrays matched the fixture groups. Every recorded Views answer (including IncorrectClusterState/incorrect-version reverts) and both cluster storage slots also matched at the pinned block. Extra provider metadata such as blockTimestamp was not compared. No recorder expectation function was called. The independent ABI pre-call-argument check uses one/two/three preceding argument words for migration/bulk-remove/bulk-register respectively.

| Receipt file / tx prefix (full hash is in fixture) | Matched proxy cluster logs | Matched Views calls | Result |
|---|---:|---:|---|
| `24920792-26136c50.json` / `0x26136c505e25df23dd6580119802e9d38dc1a548a677e9b7803d46177c6f93bd` | 2 | 4 | PASS |
| `24921171-147e813e.json` / `0x147e813eca11f6e1a4a2724e057e912f71470b9e58ea074a3bcf0806bba3af19` | 2 | 4 | PASS |
| `24926844-77ea23fc.json` / `0x77ea23fcacd9f3f47db52e37db0eddbf7e10690a95083be9decff2238ea42aaf` | 2 | 4 | PASS |
| `25014341-83ece9f7.json` / `0x83ece9f73ec5700756916834fb0e8793ed3264f416199597ca0527e92fb86616` | 2 | 4 | PASS |
| `25298236-9b2cb18d.json` / `0x9b2cb18dd15221d9b7ece2b4e3742faa9fe81a2c5d5ea7dada0304f96ba73e97` | 2 | 4 | PASS |
| `25431564-3e18fe4f.json` / `0x3e18fe4f9cd2dede123196b43f774cab7f8ebd878c360cf435acbc1b486ee8c4` | 2 | 4 | PASS |
| `26232215-4fc63d09.json` / `0x4fc63d094786e3885d7f03d0b1cdbe13f46ec1b7e7335b0e7ad76b82785cb875` | 5 | 4 | PASS |
| `26233130-f10f4666.json` / `0xf10f46660fb5edd7fbdf3b07bc8cb4640291f24ed8358e05b6cf35f585568dfd` | 60 | 4 | PASS |
| `26233260-38d732e6.json` / `0x38d732e6024171258bcd3aca4b0834673fda71b8d3815b881007a3390c5a8ac0` | 22 | 4 | PASS |
| `26233303-84ffbeb6.json` / `0x84ffbeb6811c47f82430cf0b2cad78fac64a3ec1558951c6e345651a637be1f1` | 16 | 4 | PASS |
| `26233389-ff3292f8.json` / `0xff3292f86ff0e59d67d1229ee8df0364843edf56bce03d908c3cde5018b6d1d4` | 24 | 4 | PASS |
| `26240953-ed9e6cb4.json` / `0xed9e6cb466660c25318ac27b86cfa502b24bdf97d20233a7328fb67c15d441f5` | 63 | 3 | PASS |

Endpoint failures were not counted as passes: sandbox DNS initially returned `URLError(gaierror(8, 'nodename nor servname provided, or not known'))`; escalation permitted networking. Python's default user agent on `https://eth.drpc.org` returned HTTP 403, an explicit user agent reached the service, then the parallel drpc attempt was rate-limited (HTTP 429; two complete receipts passed). The alternative nodies run completed **all twelve**, so no required LIVE row remains NOT RUN. The twelve-file nodies stdout was captured in this session; an overlapping drpc attempt overwrote its scratch JSON, so the durable table above records the measured nodies success rather than relabeling the later drpc errors. Input-auth results are separate and retained.

## 4. Falsifiers

All mutations ran on an ordinary copy under `/tmp/cp2d-qa/mutant/reconcile`, with `PYTHONDONTWRITEBYTECODE=1`. Each mutation was restored in a `finally` block, byte-compared with the frozen source, then the targeted tests reran green. Root-worktree porcelain and diff remained empty throughout; no implementation repair or mutation occurred in the checkout.

| ID | Mutation | Targeted `pytest -k` expression (file under scratch reconcile/tests) | Mutated / restored |
|---|---|---|---|
| F-M1a | re-poison OR liquidatable → AND | test_m1.py: `re_poison_after_repair_pages or poisoned_and_liquidatable_pages or thresholds_from_chain` | exit 1: 3 failed; restored exit 0: 3 passed |
| F-M1b | IncorrectClusterState returns True | test_m1.py + test_m1_receipts.py: `poisoned_receipt_warns or flags_recorded` | 1: 12 failed; 0: 12 passed |
| F-M1c | final event overwrite → setdefault | test_m1.py: `same_block` | 1: 2 failed; 0: 2 passed |
| F-M1d | disable reorg rewind branch | test_m1.py: `reorg` | 1: 5 failed, 2 passed; 0: 7 passed |
| F-M2a | hard > → >= | test_m2.py: `hard_boundary` | 1: 1 failed; 0: 1 passed |
| F-M2b | early < → <= | test_m2.py: `exactly_900` | 1: 1 failed; 0: 1 passed |
| F-M2c | velocity > → >= | test_m2.py: `equal_to_headroom` | 1: 1 failed; 0: 1 passed |
| F-M2d | orphan canonical=0 → 9 | test_m2.py: `inbox_revert_of_an_orphaned_block` | 1: 1 failed; 0: 1 passed |
| QA-M1-tie (own) | return latest duplicate validating log instead of earliest | Appendix A: `all_event_kinds_and_earliest_tie` | 1: 8 failed; 0: 8 passed |
| QA-M2-limit (own) | replace live limit read with 3000 | Appendix A: `nonstandard_limit_and_early_config` | 1: 1 failed; 0: 1 passed |

Additional own behavioral falsifiers on the unmodified implementation are F1–F4 (six red cases); all are preserved in Appendix A. Their passing controls establish normal ordering, RPC failures, chain-derived limits and ordinary reorg recovery before the failing edge is introduced. No restoration is necessary for these fake-chain inputs.

## 5. Evidence-plane writes

CP2d status changes from IMPLEMENTED — AWAITING QA to **QA FAILED** on frozen head `588888bdcb7c215f798a23e86cc9475a85b22995`, linking this report. Only tracked writes: this report, CP2d's single `**Status:**` line in `plans/monitoring-service/plan.md`, and appended event-0036 in `plans/monitoring-service/execution-log.md`. Authorized untracked venv: `reconcile/.venv`; test scripts, mutation copies and outputs: `/tmp/cp2d-qa`. No config/, config.zip, .env*, implementation or frozen test file was written. Next owner: fresh repair worker for F1–F4, then independent QA attempt 2; no next checkpoint started.

## 6. Repo proof

Before evidence writes, both commands printed no paths:

```text
$ git status --porcelain
$ git diff --name-only 588888bdcb7c215f798a23e86cc9475a85b22995
```

Pre-commit evidence-write proof:

```text
$ git status --porcelain
 M plans/monitoring-service/execution-log.md
 M plans/monitoring-service/plan.md
?? plans/monitoring-service/qa/cp2d-attempt-1.md
$ git diff --name-only 588888bdcb7c215f798a23e86cc9475a85b22995
plans/monitoring-service/execution-log.md
plans/monitoring-service/plan.md
```

The new report is untracked in the second listing until staged. After commit, porcelain is empty and diff from the frozen lane head lists exactly these three evidence files (including the report). **No implementation file appears in either proof.** The shared test files also remain byte-identical to integration head `500abe4`. This statement is checked after committing, before worker_done.

## Appendix A — independent tests (durable reproducers)

Extract the three executable appendices into scratch from this report, then run the exact commands in the matrix/findings:

```sh
mkdir -p /tmp/cp2d-qa
reconcile/.venv/bin/python - <<'PY'
from pathlib import Path
r=Path('plans/monitoring-service/qa/cp2d-attempt-1.md').read_text()
for name in ('test_independent.py','live_auth.py','verify_input.py','falsify.py'):
    code=r.split('<!-- '+name+' -->\n```python\n',1)[1].split('\n```',1)[0]
    Path('/tmp/cp2d-qa',name).write_text(code+'\n')
PY
```

<!-- test_independent.py -->
```python
import sys, pathlib, json
ROOT=pathlib.Path.cwd()
sys.path[:0]=[str(ROOT/'reconcile'),str(ROOT/'reconcile/tests')]
import pytest
from fixtures_cluster import *
from fixtures_cluster import _emit_cluster
from fakechain import _enc, _topic_addr, keys
from harness import advance, outbox, stub_params, VIEWS, make_worker, api_row
from eb_storage import Cluster,u256
from test_m1 import evidence, kinds, incidents, detections, restart
from test_m2 import setup, tick, open_keys, alerts, inbox_setup, LEG, LEG_OPS
from checks.m1_stale_tuple import _tup
from fakechain import MiniSSV

def test_m1_repair_then_repoison_same_block_pages(tmp_path):
    c,w,s=make_m1(tmp_path)
    deposit(s,value=10**18); advance(w)
    poison(s); advance(w)
    deposit(s,value=0,mine=False)
    poison(s); advance(w)
    print('actual',kinds(w),detections(w))
    assert evidence(w)['re_poisoned'] is True
    assert kinds(w)[-1]==('page','escalate')

def test_m2_empty_api_not_clean(tmp_path):
    c,w,s,rows=setup(tmp_path,eth=2981,private=True,m2={'early_headroom':1})
    advance(w); assert 'hard:1' in open_keys(w)
    rows.clear(); tick(w,c)
    print('actual',open_keys(w), alerts(w),[r['key'] for r in outbox(w,"kind='health'")])
    assert 'hard:1' in open_keys(w), 'empty API is UNKNOWN, not proof migration now fits'

def test_m2_reorg_unclears_surviving_revert(tmp_path):
    c,w,s,rows,path=inbox_setup(tmp_path,eth=2981,private=True,m2={'early_headroom':1})
    advance(w)
    failed_tx(c,path,'reactivate',LEG,LEG_OPS,current_tuple(s,LEG,LEG_OPS),LEG,exceed_limit(1))
    advance(w); advance(w); assert 'revert:1' in open_keys(w)
    fork=c.number
    set_operator(c,1,eth=2900,ssv=20,private=True); tick(w,c)
    assert 'revert:1' not in open_keys(w)
    c.reorg(fork)
    # FakeChain reorg drops writes/logs but not call stubs; make the canonical counts explicit.
    set_operator(c,1,eth=2981,ssv=20,private=True); c.mine(2)
    advance(w)
    print('actual',open_keys(w),[dict(r) for r in w.db.conn.execute('SELECT * FROM m2_reverts')])
    assert 'revert:1' in open_keys(w), 'surviving inbox revert must be re-evaluated after settlement block orphaned'

@pytest.mark.parametrize('answer',['empty','error'])
def test_m1_api_only_key_reorg_missing_inventory_is_unknown(tmp_path,answer):
    c=ClusterChain(); s=MiniSSV(c)
    cid,st=s._get(OWNER,OPS); st['count']=2; s._persist(cid,st); c.mine()
    cl=current_tuple(s)
    rows=[api_row(OWNER,OPS,cl.validator_count,cl.network_fee_index,cl.index,True,cl.balance+1)]
    c,w,s=make_m1(tmp_path,chain=c,ssv=s,inventory={'api_url':'https://api.invalid','api_refresh_s':0})
    w.inventory.http_get=lambda url:{'clusters':list(rows),'pagination':{'pages':1}}
    advance(w); assert len(incidents(w,'open'))==1
    fork=c.number; c.mine(3); w.clock.advance(601); advance(w)
    c.reorg(fork); c.mine(2)
    if answer=='empty': rows.clear()
    else:
        def down(url): raise OSError('QA inventory unavailable')
        w.inventory.http_get=down
    advance(w)
    print('actual',answer,incidents(w),w.db.meta('reorg:m1'))
    assert len(incidents(w,'open'))==1, 'empty/missing inventory cannot resolve an API-only unknown key'
    assert w.db.meta('reorg:m1') is not None, 'unevaluated key must retain durable retry marker'

def test_m2_empty_api_during_reorg_not_clean(tmp_path):
    c,w,s,rows=setup(tmp_path,eth=2981,private=True,m2={'early_headroom':1})
    advance(w); fork=c.number
    c.mine(3); tick(w,c); c.reorg(fork); c.mine(2); rows.clear()
    advance(w)
    print('actual',open_keys(w),w.db.meta('reorg:m2'))
    assert 'hard:1' in open_keys(w)

@pytest.mark.parametrize('kind',['ClusterDeposited','ClusterWithdrawn','ClusterLiquidated','ClusterReactivated','ValidatorAdded','ValidatorRemoved','ClusterBalanceUpdated','ClusterMigratedToETH'])
def test_m1_all_event_kinds_and_earliest_tie(tmp_path,kind):
    c,w,s=make_m1(tmp_path)
    deposit(s,value=10**18); advance(w)
    canonical=current_tuple(s)
    stale=Cluster(canonical.validator_count,canonical.network_fee_index,canonical.index,canonical.active,canonical.balance+17)
    tx=c.new_tx(OWNER)
    for _ in range(3): _emit_cluster(c,tx,'ClusterDeposited',OWNER,OPS,canonical)
    if kind=='ClusterBalanceUpdated':
        c.emit(tx,kind,[_topic_addr(OWNER),'0x'+u256(c.number).hex()],_enc(['uint64[]','uint32','cluster'],[list(OPS),32,stale]))
    elif kind=='ClusterMigratedToETH':
        c.emit(tx,kind,[_topic_addr(OWNER)],_enc(['uint64[]','uint256','uint256','uint32','cluster'],[list(OPS),0,0,32,stale]))
    else: _emit_cluster(c,tx,kind,OWNER,OPS,stale)
    # Arrival order cannot decide chain order.
    c.logs.reverse(); c.mine(); advance(w)
    assert kinds(w)==[('warn','open')]
    assert evidence(w)['repair_tuple']==_tup(canonical)
    assert evidence(w)['repair_log_index']==0

def test_m1_two_clusters_in_one_tx(tmp_path):
    c,w,s=make_m1(tmp_path)
    deposit(s,value=10**18); deposit(s,owner=OWNER2,ops=OPS2,value=10**18); advance(w)
    tx,_,repair=poison(s,mine=False)
    canonical=current_tuple(s,OWNER2,OPS2)
    for _ in range(2): _emit_cluster(c,tx,'ClusterDeposited',OWNER2,OPS2,canonical)
    c.mine(); advance(w)
    assert [r['key'] for r in incidents(w,'open')]==[key_of(OWNER,OPS)]
    assert evidence(w)['repair_tuple']==_tup(repair)

def test_m1_earliest_repair_before_64_other_candidates(tmp_path):
    c,w,s=make_m1(tmp_path)
    deposit(s,value=10**18); advance(w)
    canonical=current_tuple(s); tx=c.new_tx(OWNER)
    _emit_cluster(c,tx,'ClusterDeposited',OWNER,OPS,canonical)
    for delta in range(1,67):
        stale=Cluster(canonical.validator_count,canonical.network_fee_index,canonical.index,True,canonical.balance+delta)
        _emit_cluster(c,tx,'ClusterDeposited',OWNER,OPS,stale)
    c.mine(); advance(w)
    print('actual repair',evidence(w)['repair_tuple'],evidence(w)['repair_log_index'])
    assert evidence(w)['repair_tuple']==_tup(canonical)
    assert evidence(w)['repair_log_index']==0

@pytest.mark.parametrize('case',['liquidatable','re_poison'])
def test_m1_reorg_removes_each_page_trigger(tmp_path,case):
    c,w,s=make_m1(tmp_path)
    deposit(s,value=10**18); advance(w)
    if case=='re_poison':
        poison(s); advance(w); deposit(s); advance(w)
    else: c.burn[key_of(OWNER,OPS)]=10**18
    saved=dict(s._get(OWNER,OPS)[1]); fork=c.number
    poison(s); advance(w)
    assert kinds(w)[-1][0]=='page'
    c.reorg(fork); s._get(OWNER,OPS)[1].update(saved); c.mine(2); advance(w)
    assert incidents(w,'open')==[]

@pytest.mark.parametrize('eth',[2100,2981])
def test_m2_reorg_removes_hard_and_early_counts(tmp_path,eth):
    c,w,s,rows=setup(tmp_path,eth=0,private=True)
    advance(w); fork=c.number
    set_operator(c,1,eth=eth,ssv=20,private=True); tick(w,c)
    assert 'early:1' in open_keys(w)
    if eth==2981: assert 'hard:1' in open_keys(w)
    c.reorg(fork); set_operator(c,1,eth=0,ssv=20,private=True); c.mine(2); advance(w)
    assert open_keys(w)==[]

@pytest.mark.parametrize('monitor',['m1','m2'])
def test_shorter_replacement_chain(monitor,tmp_path):
    if monitor=='m1':
        c,w,s=make_m1(tmp_path); deposit(s,value=10**18); advance(w)
        saved=dict(s._get(OWNER,OPS)[1]); fork=c.number
        poison(s); c.mine(8); advance(w)
        c.reorg(fork); s._get(OWNER,OPS)[1].update(saved); c.mine(2)
    else:
        c,w,s,rows=setup(tmp_path,eth=2800,private=True,m2={'early_headroom':1})
        advance(w); fork=c.number
        s.register('0x'+'d4'*20,(1,9,10,11),keys(201,0)); c.mine(8); tick(w,c)
        c.reorg(fork); c.mine(2)
    advance(w)
    assert w.db.meta('reorg:'+monitor) is None
    assert not w.db.conn.execute('SELECT 1 FROM incidents WHERE check_id=? AND state="open"',(monitor,)).fetchone()

@pytest.mark.parametrize('fn',['migrateClusterToETH','reactivate'])
def test_m2_each_inbox_function_warns(tmp_path,fn):
    c,w,s,rows,path=inbox_setup(tmp_path,eth=0)
    advance(w)
    failed_tx(c,path,fn,LEG,LEG_OPS,current_tuple(s,LEG,LEG_OPS),LEG,exceed_limit(1))
    advance(w); advance(w)
    assert ('warn','open','revert:1') in alerts(w)

def test_m2_nonstandard_limit_and_early_config(tmp_path):
    c,w,s,rows=setup(tmp_path,eth=1400,private=True,count=40,m2={'early_headroom':11})
    stub_params(c,validators_per_operator_limit=1450)
    c.emit_event('ModuleUpgraded',['0x'+u256(1).hex()],u256(1)); c.mine(); advance(w)
    assert open_keys(w)==['early:1'] # gap 10 < config 11
    stub_params(c,validators_per_operator_limit=1439)
    c.emit_event('ModuleUpgraded',['0x'+u256(1).hex()],u256(2)); c.mine(); advance(w)
    assert open_keys(w)==['early:1','hard:1']
    stub_params(c,validators_per_operator_limit=1451)
    c.emit_event('ModuleUpgraded',['0x'+u256(1).hex()],u256(3)); c.mine(); advance(w)
    assert open_keys(w)==[] # gap 11 == config, hard fits

@pytest.mark.parametrize('severity,delay',[('warn',86400),('page',21600)])
def test_m1_cadence_and_check_has_no_delivery(tmp_path,severity,delay):
    c,w,s=make_m1(tmp_path)
    deposit(s,value=10**18); advance(w)
    if severity=='page': c.burn[key_of(OWNER,OPS)]=10**18
    poison(s); advance(w)
    assert kinds(w)==[(severity,'open')]
    sent=len(w.notifier.sent)
    # run_checks owns outbox; the check itself must have no transport effects
    w.clock.advance(delay-1); c.mine(); advance(w)
    assert len(w.notifier.sent)==sent
    w.clock.advance(1); c.mine(); advance(w)
    assert len(w.notifier.sent)==sent+1

@pytest.mark.parametrize('monitor',['m1','m2'])
def test_checks_return_findings_without_alert_side_effects(tmp_path,monitor):
    if monitor=='m1':
        c,w,s=make_m1(tmp_path); deposit(s,value=10**18); advance(w); poison(s)
    else: c,w,s,rows=setup(tmp_path,eth=2981,private=True)
    w.check_reorg(); w.scan(); w.poll_inbox()
    b=w.checkpoint()['block']; w.reload_params(b); check=w.checks[monitor]
    before=(len(outbox(w)),w.db.conn.execute('SELECT COUNT(*) FROM incidents').fetchone()[0],len(w.notifier.sent))
    ctx=w._ctx(check,b,{'force'},events=lambda:w.db.events_after(w._cursor(monitor),b,w._kinds(check)))
    with w.db.tx():
        findings=check.run(ctx,b)
    after=(len(outbox(w)),w.db.conn.execute('SELECT COUNT(*) FROM incidents').fetchone()[0],len(w.notifier.sent))
    assert findings and any(f.severity=='warn' and not f.resolve for f in findings)
    assert before==after


```

## Appendix B — independent LIVE verification

<!-- live_auth.py -->
```python
import concurrent.futures, glob, json, pathlib, sys, urllib.request
ROOT = pathlib.Path.cwd()
sys.path[:0] = [str(ROOT/'reconcile'), str(ROOT/'reconcile/tests')]
from eb_events import decode_log
from eb_storage import cluster_id
ENDPOINT = sys.argv[1] if len(sys.argv)>1 else 'https://eth.drpc.org'
def rpc(method, params):
    req=urllib.request.Request(ENDPOINT, json.dumps(dict(jsonrpc='2.0',id=1,method=method,params=params)).encode(), {'Content-Type':'application/json','User-Agent':'ssv-cp2d-qa/1.0'})
    with urllib.request.urlopen(req, timeout=25) as r: return json.load(r)
def check(path):
    d=json.load(open(path)); tx=d['tx_hash']
    try:
        response=rpc('eth_getTransactionReceipt',[tx]); rec=response.get('result')
        assert rec, response
        assert rec['blockHash'].lower()==d['block_hash'].lower()
        assert int(rec['blockNumber'],16)==d['block']
        actual={int(l['logIndex'],16):l for l in rec['logs']}
        expected=[l for l in d['logs'] if l['transactionHash'].lower()==tx.lower()]
        for l in expected:
            a=actual[int(l['logIndex'],16)]
            for k in ('address','topics','data','blockHash','blockNumber','transactionHash','transactionIndex','logIndex'):
                assert a[k]==l[k], (k,a[k],l[k])
        for g in d['groups']:
            evs=[decode_log(actual[i]) for i in g['log_indexes']]
            evs.sort(key=lambda e:e.log_index)
            ev=evs[-1]
            assert '0x'+cluster_id(ev.owner,ev.operator_ids).hex()==g['cluster_id']
            t=ev.cluster
            assert [t.validator_count,t.network_fee_index,t.index,t.active,str(t.balance)]==g['last_tuple']
        answers=0
        for c in d['eth_calls']:
            a=rpc('eth_call',[{'to':c['to'],'data':c['data']},hex(d['block'])])
            if 'result' in c: assert a.get('result')==c['result'],a
            else:
                data=a.get('error',{}).get('data')
                assert data==c['revert'], (a,c)
            answers+=1
        for slot,w in d['storage'].items():
            a=rpc('eth_getStorageAt',[d['proxy'],slot,hex(d['block'])])
            assert int(a['result'],16)==int(w,16),a
        return dict(file=pathlib.Path(path).name,tx=tx,endpoint=ENDPOINT,result='PASS',logs=len(expected),calls=answers)
    except Exception as e:
        return dict(file=pathlib.Path(path).name,tx=tx,endpoint=ENDPOINT,result='NOT RUN' if isinstance(e,(OSError,urllib.error.URLError)) else 'FAIL',error=repr(e))
paths=sorted(glob.glob('reconcile/tests/fixtures/receipts/*.json'))
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
    rows=list(pool.map(check,paths))
for r in rows: print(json.dumps(r),flush=True)
pathlib.Path('/tmp/cp2d-qa/live_auth.json').write_text(json.dumps(rows,indent=2))
sys.exit(0 if all(r['result']=='PASS' for r in rows) else 1)

```

<!-- verify_input.py -->
```python
import pathlib,json,urllib.request,concurrent.futures
endpoint='https://eth-pokt.nodies.app'
def check(p):
    d=json.loads(p.read_text()); groups=[g for g in d['groups'] if 'synthetic_poison' in g]
    if not groups: return {'file':p.name,'result':'N/A (no synthetic pre-call tuple)'}
    request=urllib.request.Request(endpoint,json.dumps({'jsonrpc':'2.0','id':1,'method':'eth_getTransactionByHash','params':[d['tx_hash']]}).encode(),{'Content-Type':'application/json','User-Agent':'ssv-cp2d-qa/1.0'})
    with urllib.request.urlopen(request,timeout=25) as r: tx=json.load(r)['result']
    assert tx['to'].lower()==d['proxy']
    fn=d['function'].split('(')[0]
    offset={'migrateClusterToETH':1,'bulkRemoveValidator':2,'bulkRegisterValidator':3}[fn]
    body=bytes.fromhex(tx['input'][10:]); words=[int.from_bytes(body[32*i:32*(i+1)],'big') for i in range(offset,offset+5)]
    actual=words[:3]+[bool(words[3]),str(words[4])]
    for g in groups: assert actual==g['synthetic_poison']['stale_tuple']
    return {'file':p.name,'result':'PASS','endpoint':endpoint,'tuple':actual}
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    rows=list(pool.map(check,sorted(pathlib.Path('reconcile/tests/fixtures/receipts').glob('*.json'))))
pathlib.Path('/tmp/cp2d-qa/input-auth.json').write_text(json.dumps(rows,indent=2))
for r in rows: print(json.dumps(r))

```

## Appendix C — mutation driver

<!-- falsify.py -->
```python
import pathlib, shutil, subprocess, os, json
ROOT=pathlib.Path.cwd()
scratch=pathlib.Path('/tmp/cp2d-qa/mutant')
scratch.mkdir(exist_ok=True)
shutil.copytree(ROOT/'reconcile',scratch/'reconcile',dirs_exist_ok=True,ignore=shutil.ignore_patterns('.venv','__pycache__','.pytest_cache'))
python=str(ROOT/'reconcile/.venv/bin/python')
cases=[
 ('F-M1a','m1_stale_tuple.py','if repaired_before or liquidatable:','if repaired_before and liquidatable:', 'test_m1.py','re_poison_after_repair_pages or poisoned_and_liquidatable_pages or thresholds_from_chain'),
 ('F-M1b','m1_stale_tuple.py','                return False\n            raise','                return True\n            raise','test_m1.py test_m1_receipts.py','poisoned_receipt_warns or flags_recorded'),
 ('F-M1c','m1_stale_tuple.py','finals[ev.cluster_id] = ev','finals.setdefault(ev.cluster_id, ev)','test_m1.py','same_block'),
 ('F-M1d','m1_stale_tuple.py','if reorg:\n            self._rewind','if False:\n            self._rewind','test_m1.py','reorg'),
 ('F-M2a','m2_operator_capacity.py','> limit), key=lambda','>= limit), key=lambda','test_m2.py','hard_boundary'),
 ('F-M2b','m2_operator_capacity.py','if gap < self.config','if gap <= self.config','test_m2.py','exactly_900'),
 ('F-M2c','m2_operator_capacity.py','if regs > headroom:','if regs >= headroom:','test_m2.py','equal_to_headroom'),
 ('F-M2d','m2_operator_capacity.py','i.canonical = 0','i.canonical = 9','test_m2.py','inbox_revert_of_an_orphaned_block'),
 ('QA-M1-tie','m1_stale_tuple.py','return next(e for e in earlier if e.cluster == cl)','return next(e for e in reversed(earlier) if e.cluster == cl)','/tmp/cp2d-qa/test_independent.py','all_event_kinds_and_earliest_tie'),
 ('QA-M2-limit','m2_operator_capacity.py','limit = int(ctx.params.validators_per_operator_limit)','limit = 3000','/tmp/cp2d-qa/test_independent.py','nonstandard_limit_and_early_config'),
]
env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'}
results=[]
for name,file,old,new,tests,expr in cases:
    if (pathlib.Path('/tmp/cp2d-qa')/(name+'.out')).exists():
        continue
    p=scratch/'reconcile/checks'/file; original=p.read_bytes()
    assert original.decode().count(old)==1,(name,original.decode().count(old))
    cmd=[python,'-m','pytest','-q',*[t if t.startswith('/') else f'reconcile/tests/{t}' for t in tests.split()],'-k',expr]
    try:
        p.write_text(original.decode().replace(old,new))
        red=subprocess.run(cmd,cwd=scratch,env=env,capture_output=True,text=True)
    finally: p.write_bytes(original)
    assert p.read_bytes()==(ROOT/'reconcile/checks'/file).read_bytes()
    green=subprocess.run(cmd,cwd=scratch,env=env,capture_output=True,text=True)
    (pathlib.Path('/tmp/cp2d-qa')/(name+'.out')).write_text('COMMAND '+repr(cmd)+'\nRED EXIT '+str(red.returncode)+'\n'+red.stdout+red.stderr+'\nRESTORED EXIT '+str(green.returncode)+'\n'+green.stdout+green.stderr)
    row=dict(name=name,red=red.returncode,green=green.returncode,red_summary=red.stdout.splitlines()[-1],green_summary=green.stdout.splitlines()[-1],restored=True)
    print(json.dumps(row),flush=True); results.append(row)
pathlib.Path('/tmp/cp2d-qa/falsifiers.json').write_text(json.dumps(results,indent=2))
assert all(r['red']==1 and r['green']==0 for r in results)

```
