FAIL

# CP2d independent QA — attempt 2, after repair 1

Frozen head: `8d5b6752f5a360a59918b8faed73dce41bf7ca9b`, lane `mtabasco/ms-cp2d`; QA branch `mtabasco/ms-qa-cp2d-2`. Task `task_a4056bd03c8e`, Dispatch `ctx_af1a03ee06d3`. Measured 2026-10-21. All commands run from this worktree root unless a scratch cwd is explicitly given; `PYTHONDONTWRITEBYTECODE=1` for independent/mutation runs. Only the three authorized evidence files are changed. No implementation repair.

## 1. Findings

The new independent suite returns **7 failed, 17 passed**, exit **1**. The original independent suite returns **29 passed**, exit **0**: attempt 1's four minimal reproducers are fixed in their original forms. The committed suites and the repair's 80 walks pass, but do not establish the broader claims below.

Reproduce by extracting Appendix A into `/tmp/cp2d-qa2/test_adversarial.py` using its extraction command. No product mutation is required for any finding.

### F1 — HIGH: a reorg does not fully re-evaluate already-resolved keys when the API becomes partial (M1 and M2)

```sh
PYTHONDONTWRITEBYTECODE=1 reconcile/.venv/bin/python -m pytest -q -s /tmp/cp2d-qa2/test_adversarial.py -k reorg_of_already_resolved
```

**Exit 1: four failures**, M1/M2 × normal/restart-between-rewind-and-handling. Expected: after orphaning the chain evidence used by a resolution, re-evaluate the persisted key; report its condition/UNKNOWN or raise and retain the durable reorg marker. A response listing an unrelated cluster is a **partial** inventory, not proof about the omitted cluster. Actual: both checks commit the reorg run and clear their markers while the affected incident stays resolved.

- **M1:** create an API-only on-chain cluster with a wrong-hash API tuple; it opens unknown/warn. In a later block change its storage tuple and supply that valid tuple, resolving it. Reorg away that storage change and resolution, and return only a different, benign cluster. `incidents` still contains the resolved key (`resolved_block=1006`), `reorg:m1` is `None`, and M1 returns neither a finding for that key nor a check-specific health message. The only health is the framework's general reorg notice. `_persisted_keys` enumerates active poison/unresolved revert rows; `_open_keys` enumerates open incidents. A resolved API-only unknown key is in neither set, so it is never storage-read/re-evaluated. The complete-API control reopens it.
- **M2:** begin with ETH count 2,981 and a known active legacy cluster of 20 validators; `hard:1` opens. Change the cluster's tuple and ETH count to 2,900, so M2 persists the new tuple and resolves hard. Reorg those changes, explicitly restore the fake-chain operator read to the canonical 2,981, and omit this cluster while still listing another cluster. The persisted tuple is now from the orphaned block and does not validate against canonical storage. M2 knows the missing count could block migration, but only creates `kept`/reorg failure for an **open** hard key. Actual open keys are `['early:1']`, `reorg:m2=None`, and hard remains resolved. Inventory/unvalidated health does not satisfy re-evaluation of that persisted hard key. The complete-API control reopens hard.

These failures survive a process restart between rewind and the reorg check. This is the dispatch's explicit already-resolved-key attack and README §6.2(5)'s requirement to enumerate persisted state, not only open keys. Rewinding `cleared_block` fixed the original inbox-settlement reproducer, but does not cover these other forms of resolution.

### F2 — HIGH: a genuine differing-tuple repair mid-block is not counted, so a later poisoning stays warn (M1's documented limit)

```sh
PYTHONDONTWRITEBYTECODE=1 reconcile/.venv/bin/python -m pytest -q -s /tmp/cp2d-qa2/test_adversarial.py -k 'three_transactions_known_limit_distinct_repair and poison'
```

**Exit 1: one failure.** Begin with a detected poisoning/warn. The next block contains three successful transactions: a helper calls two deposits (each writes/emits its own tuple; the second is canonical at transaction end), followed by two poisonings. Expected under spec M1: the successful repair followed by a poisoning records repair memory and pages. Actual: two `m1-intra-block` health messages, a warn update, `re_poisoned=false`, and no repair row. The final receipt's repair tuple itself is identified correctly; severity is wrong because the earlier repair was discarded. Replacing the third transaction with a real final repair correctly resolves (passing twin).

The implementer explicitly discloses this limitation. **Health instead of a repair is an honest UNKNOWN classification when only block-end state is available, but it is not acceptable as fulfillment of M1's unconditional page-on-re-poison requirement.** The spec has no health-only exception to this trigger, and DEF-3 defers a malicious-owner fork PoC, not monitor severity semantics. A trace/state-capable strategy or an explicit spec/owner amendment would be required before this can be credited; QA does not make that amendment.

### F3 — MEDIUM: poison → single-tuple repair → poison in the first flagged block loses the earlier poison/repair history (M1)

```sh
PYTHONDONTWRITEBYTECODE=1 reconcile/.venv/bin/python -m pytest -q -s '/tmp/cp2d-qa2/test_adversarial.py::test_three_transactions_poison_repair_poison_pages[False]'
```

**Exit 1: one failure.** Start from a clean tracked cluster. Three transactions in one block poison it, repair it by a successful zero-value deposit, then poison it again. Logs are delivered in reverse arrival order to test chain ordering. Expected from the per-transaction/re-poison semantics: preserve the earlier poisoning and intervening repair, then page; actual: only the final poison is persisted, a single warn opens, and `re_poisoned=false`. `_superseded` returns immediately unless an active poison was already persisted before that transaction. The `[True]` control, with a prior-block open poisoning, records the one-tuple repair and pages.

This case also exposes the block-end observability limitation for the earlier poisoning. It is distinct from F2: even a **single-tuple** repair is ignored when the first poisoning has not yet been recorded. This finding applies the spec's per-transaction trigger; if the owner intends re-poison to require a prior-run notification, that narrower meaning needs an explicit spec amendment. F1/F2/F4 independently require FAIL regardless of this interpretation.

### F4 — MEDIUM: an empty first API inventory is silently accepted by M1

```sh
PYTHONDONTWRITEBYTECODE=1 reconcile/.venv/bin/python -m pytest -q -s '/tmp/cp2d-qa2/test_adversarial.py::test_initial_empty_inventory_is_health_never_silent_clean[m1]'
```

**Exit 1: one failure.** Put a real cluster in fake-chain storage before the worker's baseline, with no stored logs, and return `{'clusters': [], 'pagination': {'pages': 1}}`. Expected: UNKNOWN/error/health, since an empty off-chain result cannot establish a clean full-state sweep. Actual: no incident, no M1 health, `last_sweep` accepts an empty inventory, and the run commits. The M2 initial-empty control emits `m2-inventory-empty` health and passes. The repaired open-key empty-inventory cases pass; this first-sweep case has no pre-existing key to save it.

## 2. Claims I could NOT confirm

- Broad complete reorg re-evaluation, same-block re-poison severity, and empty inventory always being UNKNOWN: falsified by F1–F4. The narrower original four reproductions are now all green.
- The known same-block differing-tuple limit meeting M1 semantics: it does not, for F2's measured case. Its disclosure is accurate; disclosure alone is not a spec exception.
- A universal absence of real poisoned receipts anywhere on mainnet: not established by this bounded corpus. I authenticated six supplied receipts freshly and replayed all twelve; all twelve faithful replays are benign. No exhaustive chain-history scan was repeated.
- Exact timestamp-based “last 24 h” velocity: tests measure the configurable block window (default 7,200 blocks), not exact elapsed time under missed slots. This unchanged limitation was also recorded in attempt 1; it is not newly measured as a product failure here.
- A malicious-owner fork PoC: **NOT RUN, DEF-3 explicitly deferred**. The worker plan/attempt record does not claim one; it explicitly labels the eleven poison replays synthetic.

Confirmed repair claims: **55 M1 + 42 M2 tests, 80 seeded walks, 29 unmodified prior independent tests** pass; the repair's regression files against pre-repair `588888b` yield **56 failed / 121 passed**, exactly the claimed 17 targeted regressions + 39 walk failures. Every one of the **23 committed mutations** goes red and restored green. These measurements confirm bounded tests, not completeness of the generalisation table.

**Receipt evidence judgment.** The twelve real multi-event receipts plus eleven clearly labeled synthetic stale-final-event replays are adequate unit evidence for ordinary decoding, the validate/no-validate predicate, and repair selection on this corpus. Attempt 1 authenticated the actual pre-call Cluster arguments used for the synthetic stale tuples and reproduced their archive Views/storage answers; the unchanged fixture hash proof below permits reusing those measurements. A real poisoned receipt would add evidence that the callback/event/storage ordering actually occurs in production, not just that a constructed sequence is correctly classified. No fork PoC is required by this assignment. This evidence does not cover the ordering and lifecycle defects above.

**D9 storage-hash judgment.** The repair correctly treats API tuple fields and `migrated` as untrusted: version comes from storage, the API tuple is admitted only if its hash matches storage at the evaluated block, and a cached M2 tuple omitted by the API is revalidated against storage. My partial/wrong-hash tests keep the open conditions and do not overwrite the persisted 20-validator tuple with the forged 1-validator tuple. The independent D9 mutation (invert hash equality in scratch) makes both valid/invalid first-sweep twins red. F1 is a lifecycle/enumeration failure despite these checks, not evidence of accepting a forged tuple as valid.

## 3. Verification matrix

Preflight: frozen HEAD equals the dispatch; initial `git status --porcelain` empty; 352 GiB free. `uv venv reconcile/.venv --python 3.12` and `uv pip install --python reconcile/.venv/bin/python -r reconcile/requirements-dev.txt` each exit 0; Python 3.12.15, seven pinned dependencies. `export PATH=$HOME/.foundry/bin:$PATH`; anvil 1.8.5 present. No fork/service is needed for the owned gated rows. Record-mode/Capture transports only. No mainnet transaction, production secret, config/, config.zip, or .env file touched.

Frozen-file proof: `git diff --exit-code plan/monitoring-service HEAD -- reconcile/tests/harness.py reconcile/tests/test_framework.py reconcile/tests/test_worker.py` and the same command against `500abe4` each return **0**, no output: exactly the centrally ruled bd531b4/500abe4 patch. A byte comparison of **every existing reconcile file in 500abe4** finds only the authorized `checks/__init__.py` import and `config.example.json` edits; no other frozen CP1B file is changed. New files are the lane's deliverables.

| V-ID / measurement | Exact command | Exit / measured result | Outcome |
|---|---|---|---|
| V2d.1 / V2x.1 M1 | `reconcile/.venv/bin/python -m pytest -q reconcile/tests/test_m1.py` | 0; 55 passed | Committed scenarios pass; semantic completeness FAIL F1–F4 |
| V2d.1 / V2x.1 M2 | `reconcile/.venv/bin/python -m pytest -q reconcile/tests/test_m2.py` | 0; 42 passed | Committed scenarios pass; completeness FAIL F1 |
| V2d.2 / V2x.2 | `reconcile/.venv/bin/python -m pytest -q reconcile/tests/test_m1.py reconcile/tests/test_m2.py -k thresholds_from_chain` | 0; 2 passed, 95 deselected | PASS |
| V2d.3 / V2x.3 | `reconcile/.venv/bin/python -m pytest -q reconcile/tests/test_m1.py reconcile/tests/test_m2.py reconcile/tests/test_m1_m2_sweep.py -k restart` | 0; 5 passed, 172 deselected | PASS for these cases; resolved-key restart FAIL F1 |
| V2d.5 / V2x.5 | `PYTHONDONTWRITEBYTECODE=1 reconcile/.venv/bin/python plans/monitoring-service/workers/cp2d/falsify_repair1.py /tmp/cp2d-qa2/mutant` | 0; 23/23 red (exit 1), restored green (exit 0) | PASS; all F-M1a–d / F-M2a–d included |
| V2d.6 / V2x.6 suite | `reconcile/.venv/bin/python -m pytest -q reconcile/tests` | 1; 440 passed, metrics localhost bind `PermissionError: [Errno 1] Operation not permitted` | Environment fault, isolated control below clears it |
| V2d.6 isolated metrics control | `reconcile/.venv/bin/python -m pytest -q reconcile/tests/test_framework.py::test_metrics_endpoint_exposes_required_series` | 0; 1 passed with escalated localhost permission | PASS: combined 441/441, no skipped code test |
| V2d.6 lint | `reconcile/.venv/bin/ruff check reconcile` | 0; All checks passed | PASS |
| V2d.7 receipt replay | `reconcile/.venv/bin/python -m pytest -q reconcile/tests/test_m1_receipts.py` | 0; 24 passed = count + 12 faithful + 11 synthetic poison | PASS for bounded corpus, including expected repair tuple/index |
| V2d.7 fresh LIVE authenticity | `PYTHONDONTWRITEBYTECODE=1 reconcile/.venv/bin/python /tmp/cp2d-qa2/authenticate_six.py` (Appendix C; endpoint `https://eth-pokt.nodies.app`) | 0 with read-only network permission; 6/6 PASS, 12 logs, block hashes/numbers and decoded tuple/cluster fields equal | PASS; sandbox first attempt exit 1, all six NOT RUN with `URLError(gaierror(8, 'nodename nor servname provided, or not known'))`, then successful permission-enabled rerun |
| V2d.7 remaining unchanged LIVE measurements | `git diff --exit-code 588888b HEAD -- reconcile/tests/fixtures/receipts` + SHA-256 old blob/current bytes for each file (Appendix D) | 0; 12/12 byte-identical, matching hashes | REUSED per addendum: attempt 1's remaining six receipts and 47 Views / 24 storage / 11 pre-call tuple measurements; no new LIVE claim for those calls |
| Committed adversarial sweep | `reconcile/.venv/bin/python -m pytest -q reconcile/tests/test_m1_m2_sweep.py` | 0; 80 passed | PASS bounded model |
| Original independent reproducers, unmodified | `PYTHONDONTWRITEBYTECODE=1 reconcile/.venv/bin/python -m pytest -q /tmp/cp2d-qa/test_independent.py` | 0; 29 passed, two outside-conftest marker warnings | PASS; source byte-equal to attempt 1 Appendix A (ignoring trailing newlines), SHA-256 `1d49099c98dde33e68c2da4e9d47928c15ffbfdc94940a7397da1fe5591cf25a` |
| New regression files against 588888b | Appendix B scratch setup, then current venv `-m pytest -q -p no:cacheprovider reconcile/tests/test_m1.py reconcile/tests/test_m2.py reconcile/tests/test_m1_m2_sweep.py` from `/tmp/cp2d-qa2/pre-repair` | 1; 56 failed, 121 passed | PASS sensitivity claim: 17 targeted + 39/80 walks fail |
| Independent new adversarial suite | `PYTHONDONTWRITEBYTECODE=1 reconcile/.venv/bin/python -m pytest -q -s /tmp/cp2d-qa2/test_adversarial.py` | 1; 7 failed, 17 passed, two outside-conftest marker warnings | FAIL |
| Resolved-key reorg | same independent file, `-k reorg_of_already_resolved` | 1; 4 failed, 20 deselected | FAIL F1 |
| Three-transaction ordering | same independent file, `-k three_transactions` | 1; 2 failed, 2 passed, 20 deselected | FAIL F2/F3 |
| Empty initial API | same independent file, `-k initial_empty_inventory` | 1; 1 failed (M1), 1 passed (M2), 22 deselected | FAIL F4 |
| Other independent controls | same independent file, `-k 'not reorg_of_already_resolved and not three_transactions and not initial_empty_inventory'` | 0; 14 passed, 10 deselected | PASS: partial API, forged hash tuple, first sweep valid/invalid, crash before handling, complete-API resolved-key recovery, live limit/config boundaries, public/private severity, cadence/routing |
| Own falsifiers | `PYTHONDONTWRITEBYTECODE=1 reconcile/.venv/bin/python /tmp/cp2d-qa2/falsify_independent.py` (Appendix E) | 0; 2/2 red, restored green, byte restoration asserted | PASS |
| V2x.4 | Not owned; no separate repeat | Full suite runs its 350-cluster budget case | No unserved required row |

Budget: ran the full suite once and repeated only the permission-blocked localhost test; targeted and mutation reruns serve distinct falsification evidence. Freshly re-authenticated six receipts; skipped a duplicate authentication of the other six and the unchanged 47 Views calls, 24 storage words and eleven pre-call inputs after the byte/hash proof. No broad mainnet-history scan or deferred fork PoC. No mandatory GATED row is NOT RUN.

### Spec-derived triggers and benign twins

I derived these from spec M1/M2, rather than using the worker's scenario list as the authority. All named committed tests ran above; severity/control limitations are stated explicitly.

| Spec trigger | Trigger / severity and benign twin measured | Result |
|---|---|---|
| M1 eight tuple-carrying event kinds, per cluster/tx, group >=2, stale final | `poisoned_receipt_warns_with_repair_tuple` warn; `multi_event_receipt_that_validates_is_silent`, `single_event_transactions_are_not_validated` silent; eight `repair_tuple_tie_is_the_earliest_log_for_every_event_kind` cases; prior independent interleaved-two-clusters/reversed-logs cases | Bounded tests PASS; per-tx ordering gaps F2/F3 |
| M1 earlier validating tuple / earliest tie | `repair_tuple_is_the_earliest_validating_log_of_the_receipt`, `earliest_repair_tuple_before_66_stale_snapshots`; earlier-invalid/later-valid and duplicate-valid ties; prior independent cap/tie reproducers | PASS; original F4 repaired, no candidate cap remains |
| M1 our updateClusterBalance IncorrectClusterState | `inbox_incorrect_cluster_state_from_our_sender_warns` warn; stranger/other_error/other_function twins silent | PASS |
| M1 our liquidate IncorrectClusterState | `inbox_liquidator_revert_on_poisoned_cluster_feeds_the_same_incident` warn/material update; same sender/error/function twins | PASS |
| M1 re-poison after repair | `re_poison_after_repair_pages`, repaired original same-block test page; `second_poisoning_without_repair_is_not_a_re_poison` stays warn, final repair resolves | PASS bounded; required page missing F2/F3 |
| M1 poisoned and liquidatable | `poisoned_and_liquidatable_pages` page, ordinary nonliquidatable poison warn and valid tuple silent; `thresholds_from_chain_liquidation_period_change_escalates_to_page` | PASS |
| M1 inventory hash mismatch/unknown | valid/invalid API first-sweep twins, `inventory_hash_mismatch_is_unknown_warn_and_valid_api_tuple_is_silent`; new partial/wrong-hash open-key controls | PASS bounded; empty initial API and resolved-key reorg FAIL |
| M2 active legacy cluster ETH+count > chain limit | `hard_blocked_active_legacy_cluster_warns` warn; exact-fit and liquidated legacy twins silent | PASS; live custom limits 1439/1440 and partial/forged API controls pass; resolved-key reorg FAIL F1 |
| M2 early gap < configurable 900 | private info/public warn tests; exact 900 silent; new custom threshold 11 tests gap 10 alert / gap 11 silent and public/private routes | PASS |
| M2 last-24h registrations > headroom | `velocity_registrations_above_headroom_is_info`; equal-to-headroom and out-of-window twins silent; own chain-limit drop crosses 51 == 51 silent to 51 > 50 info | PASS block-window semantics; timestamp equivalence unconfirmed |
| M2 migrateClusterToETH / reactivate ExceedValidatorLimitWithData | inbox warn tests and prior independent `each_inbox_function_warns` both functions; other_error/other_function twins silent | PASS |

Inherited attacks actually run: depth-1 and deeper-than-confirmations reorgs; trigger-block removals (M1 poison/liquidatable/re-poison/inbox; M2 hard/early/velocity/inbox); orphaned repair/settlement; shorter replacement chain; crash between rewind and handling; crash after scan before first handling; empty/error API and empty Views/operator answers; first sweep without baseline; chain parameter changed mid-run; repeated runs/restart duplicate suppression; same-block two and three transaction ordering; direct checks returning Findings without incident/outbox/transport writes; page/warn cadence and info/warn routing. The new failures are not covered by a passing nearby scenario. The full frozen framework suite also exercises empty/health-only reorg result behavior; check-specific F1 remains red despite that framework coverage.

## 4. Falsifiers

Every mutation runs **only on a scratch copy**, with byte restoration asserted against the worktree after the red and before the green. The worktree remained clean after all experiments (`git status --porcelain` empty before evidence writing). No mutation touched a tracked implementation file in this checkout.

Exact committed mutation definitions and selected tests are in `plans/monitoring-service/workers/cp2d/falsify_repair1.py`; executed command is in the matrix. Each row below has red exit **1**, restored exit **0** (no collection/environment errors credited):

| Mutation | Red tests | Restored tests |
|---|---|---|
| R1-M1-absence | 3 failed, 52 deselected in 0.16s | 3 passed, 52 deselected in 0.23s |
| R1-M2-persisted | 1 failed, 2 passed, 39 deselected in 0.12s | 3 passed, 39 deselected in 0.10s |
| R1-M2-bound | 1 failed, 1 passed, 40 deselected in 0.16s | 2 passed, 40 deselected in 0.09s |
| R1-M2-degraded | 1 failed, 41 deselected in 0.11s | 1 passed, 41 deselected in 0.09s |
| R1-M2-calldata | 1 failed, 41 deselected in 0.11s | 1 passed, 41 deselected in 0.09s |
| R1-M2-reorg-raise | 1 failed, 1 passed, 40 deselected in 0.11s | 2 passed, 40 deselected in 0.10s |
| R2-M1-intra-block | 3 failed, 2 passed, 50 deselected in 0.16s | 5 passed, 50 deselected in 0.12s |
| R3-M2-unsettle | 2 failed, 40 deselected in 0.12s | 2 passed, 40 deselected in 0.10s |
| R3-M1-unclear | 1 failed, 54 deselected in 0.14s | 1 passed, 54 deselected in 0.10s |
| R4-M1-earliest | 8 failed, 2 passed, 45 deselected in 0.23s | 10 passed, 45 deselected in 0.15s |
| S-M1-absence | 22 failed, 18 passed, 40 deselected in 0.74s | 40 passed, 40 deselected in 0.72s |
| S-M1-intra-block | 7 failed, 33 passed, 40 deselected in 1.05s | 40 passed, 40 deselected in 0.72s |
| S-M2-persisted | 4 failed, 36 passed, 40 deselected in 0.78s | 40 passed, 40 deselected in 0.78s |
| S-M2-unsettle | 2 failed, 38 passed, 40 deselected in 0.82s | 40 passed, 40 deselected in 0.83s |
| S-M2-degraded | 6 failed, 34 passed, 40 deselected in 0.88s | 40 passed, 40 deselected in 0.79s |
| F-M1a | 3 failed, 52 deselected in 0.17s | 3 passed, 52 deselected in 0.13s |
| F-M1b | 12 failed, 67 deselected in 0.23s | 12 passed, 67 deselected in 0.17s |
| F-M1c | 2 failed, 3 passed, 50 deselected in 0.15s | 5 passed, 50 deselected in 0.13s |
| F-M1d | 6 failed, 5 passed, 44 deselected in 0.23s | 11 passed, 44 deselected in 0.24s |
| F-M2a | 1 failed, 41 deselected in 0.11s | 1 passed, 41 deselected in 0.09s |
| F-M2b | 1 failed, 41 deselected in 0.11s | 1 passed, 41 deselected in 0.09s |
| F-M2c | 1 failed, 41 deselected in 0.11s | 1 passed, 41 deselected in 0.41s |
| F-M2d | 1 failed, 41 deselected in 0.10s | 1 passed, 41 deselected in 0.09s |

Own falsifiers (chosen from D9 off-chain trust and a nonstandard live limit across multiple boundaries):

| Mutation | Named independent test selection | Red → restored green |
|---|---|---|
| QA2-M1-D9: scratch `inventory.py` `_hash_word(cl) == stored` → `!= stored` | `first_sweep_has_no_baseline_and_wrong_hash_tuple` (valid and forged tuple twins) | exit 1, 2 failed → exit 0, 2 passed; bytes equal to worktree |
| QA2-M2-LIVE: scratch `limit = int(ctx.params.validators_per_operator_limit)` → `limit = 3000` | `live_limit_crosses_exact_hard_early_and_velocity_boundaries` (private/public) | exit 1, 2 failed → exit 0, 2 passed; bytes equal to worktree |

## 5. Evidence-plane writes

CP2d status: `IMPLEMENTED — AWAITING QA` → **QA FAILED**, frozen head `8d5b6752f5a360a59918b8faed73dce41bf7ca9b`, linked to this report. One event, **0037**, appended to `plans/monitoring-service/execution-log.md`. Changed tracked paths: this report, only the CP2d Status line in `plan.md`, and that appended event. The authorized local `reconcile/.venv` is ignored/untracked. All extra tests, copies, RPC scripts and logs live under `/tmp/cp2d-qa2`; no implementation repair, added repository tests, next checkpoint, push or external alert.

## 6. Repo proof

Initial and post-experiment `git status --porcelain`: empty. Before the evidence commit, `git status --porcelain`:

```text
 M plans/monitoring-service/execution-log.md
 M plans/monitoring-service/plan.md
?? plans/monitoring-service/qa/cp2d-attempt-2.md
```

`git diff --name-only 8d5b6752f5a360a59918b8faed73dce41bf7ca9b` before staging lists the two modified tracked files (the new report is then untracked); after staging/commit it lists exactly:

```text
plans/monitoring-service/execution-log.md
plans/monitoring-service/plan.md
plans/monitoring-service/qa/cp2d-attempt-2.md
```

No implementation file appears in either proof. No prohibited file was read for secrets or modified. The commit contains only these authorized evidence changes; final cleanliness is checked before worker_done.

## Appendix A — independent scenarios (durable reproducer)

Extract each appendix script by its marker (change name for another appendix):

```sh
reconcile/.venv/bin/python - <<'EXTRACT'
from pathlib import Path
import re
name = 'test_adversarial.py'
s = Path('plans/monitoring-service/qa/cp2d-attempt-2.md').read_text()
code = re.search(r'<!-- ' + re.escape(name) + r' -->\s*```python\n(.*?)\n```', s, re.S).group(1)
p = Path('/tmp/cp2d-qa2') / name
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(code + '\n')
EXTRACT
```

<!-- test_adversarial.py -->
```python
import sys,pathlib,json
ROOT=pathlib.Path.cwd()
sys.path[:0]=[str(ROOT/'reconcile'),str(ROOT/'reconcile/tests')]
import pytest
from fakechain import MiniSSV,keys
from fixtures_cluster import *
from fixtures_cluster import _emit_cluster
from harness import advance,outbox,api_row,make_worker,stub_params,VIEWS
from test_m1 import api_m1,incidents,kinds,evidence,detections,restart,api_only_key
from test_m2 import setup,tick,open_keys,alerts,legacy,LEG,LEG_OPS,ETH_OWNER,ETH_OPS
from checks.m1_stale_tuple import _tup
from eb_storage import Cluster,u256

# These scenarios and their expected results are derived independently from spec M1/M2 and README 6.2.
def row(ssv,owner,ops,migrated=True,delta=0):
 cl=current_tuple(ssv,owner,ops)
 return api_row(owner,ops,cl.validator_count,cl.network_fee_index,cl.index,cl.active,cl.balance+delta,migrated=migrated)

def repair_two_distinct(ssv):
 """A successful helper calls two deposits in one transaction; final tuple is canonical at tx end."""
 cid,st=ssv._get(OWNER,OPS); tx=ssv.c.new_tx(OWNER)
 for value in (3,5):
  ssv._touch(st); st['balance']+=value; ssv._persist(cid,st)
  _emit_cluster(ssv.c,tx,'ClusterDeposited',OWNER,OPS,ssv._cluster(st),value)
 return tx

@pytest.mark.parametrize('initial_poison',[False,True])
def test_three_transactions_poison_repair_poison_pages(tmp_path,initial_poison):
 c,w,s=make_m1(tmp_path); deposit(s,value=10**18); advance(w)
 if initial_poison: poison(s); advance(w)
 first,_,_=poison(s,mine=False)
 deposit(s,mine=False)
 last,_,repair=poison(s)
 c.logs.reverse(); advance(w)
 print('THREE',initial_poison,kinds(w),detections(w))
 assert evidence(w)['repair_tuple']==_tup(repair)
 assert evidence(w)['poison_tx']==last
 assert evidence(w)['re_poisoned'] is True, 'first poison, successful repair, re-poison within one block'
 assert incidents(w,'open')[0]['severity']=='page'

@pytest.mark.parametrize('third',['poison','repair'])
def test_three_transactions_known_limit_distinct_repair(tmp_path,third):
 c,w,s=make_m1(tmp_path); deposit(s,value=10**18); advance(w)
 poison(s); advance(w)
 repair_tx=repair_two_distinct(s)
 poison(s,mine=False)
 if third=='poison': poison(s)
 else: deposit(s)
 advance(w)
 print('DISTINCT',third,kinds(w),detections(w),evidence(w))
 if third=='repair': assert incidents(w,'open')==[]
 else:
  assert evidence(w)['re_poisoned'] is True, 'successful two-deposit repair must count even with differing tuples'
  assert incidents(w,'open')[0]['severity']=='page'

@pytest.mark.parametrize('monitor',['m1','m2'])
@pytest.mark.parametrize('restart_now',[False,True])
def test_reorg_of_already_resolved_key_with_partial_api(tmp_path,monitor,restart_now):
 if monitor=='m1':
  c,w,s,rows,_,cl=api_only_key(tmp_path)
  tick(w,c); assert len(incidents(w,'open'))==1
  # A benign unrelated API entry ensures the response is partial, not [].
  cid,st=s._get(OWNER,OPS); st['count']=1; s._persist(cid,st); c.mine()
  unrelated=row(s,OWNER,OPS)
  fork=c.number
  cid2,st2=s._get(OWNER2,OPS2);st2['balance']+=9;s._persist(cid2,st2)
  rows[0]=row(s,OWNER2,OPS2)
  tick(w,c); assert incidents(w,'open')==[]
  # Resolution's API tuple goes away; the cluster and original unknown state remain in storage.
  c.reorg(fork); c.mine(2); rows[:]=[unrelated]
  if restart_now:
   w.check_reorg(); w.scan()
   w=restart(tmp_path,c,w,inventory={'api_url':'https://api.invalid','api_refresh_s':0})
   w.inventory.http_get=lambda url:{'clusters':list(rows),'pagination':{'pages':1}}
  advance(w)
  print('RESOLVED-M1',incidents(w),w.db.meta('reorg:m1'),[r['key'] for r in outbox(w,"kind='health'")])
  assert incidents(w,'open') or w.db.meta('reorg:m1') is not None, 'persisted resolved unknown key must be revisited, unresolved input retains retry'
 else:
  c,w,s,rows=setup(tmp_path,eth=2981,private=True,m2={'early_headroom':1})
  advance(w); assert 'hard:1' in open_keys(w)
  legacy(s,rows,owner=OWNER2,ops=OPS2,count=1)
  for op in OPS2: set_operator(c,op,eth=0,ssv=1,private=True)
  c.mine(); tick(w,c)
  fork=c.number
  cid,st=s._get(LEG,LEG_OPS)
  st['balance']+=7; s._persist(cid,st)
  rows[0]=row(s,LEG,LEG_OPS,migrated=False)
  set_operator(c,1,eth=2900,ssv=20,private=True)
  tick(w,c); assert 'hard:1' not in open_keys(w)
  c.reorg(fork); set_operator(c,1,eth=2981,ssv=20,private=True); c.mine(2)
  rows[:]=[r for r in rows if r['ownerAddress']!=LEG]
  if restart_now:
   w.check_reorg(); w.scan()
   _,w=make_worker(tmp_path,only=['m2'],chain=c,views=VIEWS,clock=w.clock,checks={'m2':{'early_headroom':1,'velocity_window_blocks':20}},inventory={'api_url':'https://api.invalid','api_refresh_s':0},start_block=w.cfg.start_block,activation_block=w.cfg.activation_block)
   w.inventory.http_get=lambda url:{'clusters':list(rows),'pagination':{'pages':1}}
  advance(w)
  print('RESOLVED-M2',open_keys(w),w.db.meta('reorg:m2'),[dict(r) for r in w.db.conn.execute('SELECT * FROM m2_clusters')])
  assert 'hard:1' in open_keys(w) or w.db.meta('reorg:m2') is not None, 'resolved key, canonical blocked storage, no currently validating tuple: must re-evaluate or retain marker'

@pytest.mark.parametrize('monitor',['m1','m2'])
@pytest.mark.parametrize('api_mode',['partial','wrong_hash'])
def test_open_keys_partial_and_wrong_hash_api_are_not_recovery(tmp_path,monitor,api_mode):
 if monitor=='m1':
  c,w,s,rows,_,cl=api_only_key(tmp_path)
  tick(w,c); key=key_of(OWNER2,OPS2); assert len(incidents(w,'open'))==1
  cid,st=s._get(OWNER,OPS); st['count']=1; s._persist(cid,st); c.mine()
  unrelated=row(s,OWNER,OPS)
  rows[:]=[unrelated]+([row(s,OWNER2,OPS2,delta=-3)] if api_mode=='wrong_hash' else [])
  tick(w,c); assert [i['key'] for i in incidents(w,'open')]==[key]
  assert all(r['kind']!='resolved' for r in outbox(w,"check_id='m1'"))
 else:
  c,w,s,rows=setup(tmp_path,eth=2981,private=True,m2={'early_headroom':1})
  legacy(s,rows,owner=OWNER2,ops=OPS2,count=1)
  for op in OPS2: set_operator(c,op,eth=0,ssv=1,private=True)
  tick(w,c); assert 'hard:1' in open_keys(w)
  rows[:]=[r for r in rows if r['ownerAddress']!=LEG]
  if api_mode=='wrong_hash':
   forged=row(s,LEG,LEG_OPS,migrated=False,delta=1);forged['validatorCount']=1;rows.append(forged)
  tick(w,c); assert 'hard:1' in open_keys(w)
  # Incorrect API candidate cannot replace the persisted storage-validated 20-validator tuple.
  saved=json.loads(w.db.conn.execute('SELECT tuple FROM m2_clusters WHERE owner=?',(LEG,)).fetchone()[0]);assert saved[0]==20

@pytest.mark.parametrize('valid',[False,True])
def test_first_sweep_has_no_baseline_and_wrong_hash_tuple(tmp_path,valid):
 c=ClusterChain(); s=MiniSSV(c)
 cid,st=s._get(OWNER2,OPS2);st['count']=9;s._persist(cid,st);c.mine()
 rows=[row(s,OWNER2,OPS2,delta=0 if valid else 41)]
 c,w,s=api_m1(tmp_path,rows,chain=c,ssv=s)
 advance(w)
 assert len(incidents(w,'open'))==(0 if valid else 1)
 inv=w.inventory.get(w.checkpoint()['block']);assert inv.clusters[key_of(OWNER2,OPS2)].valid is valid

@pytest.mark.parametrize('monitor',['m1','m2'])
def test_crash_after_scan_before_handling_records_once(tmp_path,monitor):
 if monitor=='m1':
  c,w,s=make_m1(tmp_path);deposit(s,value=10**18);advance(w);poison(s)
  w.check_reorg();w.scan();w.poll_inbox();assert incidents(w,'open')==[]
  w=restart(tmp_path,c,w);advance(w);advance(w)
  assert kinds(w)==[('warn','open')]
 else:
  c,w,s,rows=setup(tmp_path,eth=2981,private=True,m2={'early_headroom':1})
  w.check_reorg();w.scan();w.poll_inbox();assert open_keys(w)==[]
  _,w=make_worker(tmp_path,only=['m2'],chain=c,views=VIEWS,clock=w.clock,checks={'m2':{'early_headroom':1,'velocity_window_blocks':20}},inventory={'api_url':'https://api.invalid','api_refresh_s':0},start_block=w.cfg.start_block,activation_block=w.cfg.activation_block)
  w.inventory.http_get=lambda url:{'clusters':list(rows),'pagination':{'pages':1}}
  advance(w);advance(w)
  assert [a for a in alerts(w) if a[2]=='hard:1']==[('warn','open','hard:1')]

@pytest.mark.parametrize('public',[False,True])
def test_m2_live_limit_crosses_exact_hard_early_and_velocity_boundaries(tmp_path,public):
 c,w,s,rows=setup(tmp_path,eth=1400,private=not public,count=40,m2={'early_headroom':11})
 advance(w)
 # Each round replaces the fake chain value; check under a non-default limit.
 for limit,expected in [(1451,[]),(1450,['early:1']),(1440,['early:1']),(1439,['early:1','hard:1']),(1451,[])]:
  stub_params(c,validators_per_operator_limit=limit)
  c.emit_event('ModuleUpgraded',['0x'+u256(1).hex()],u256(limit));c.mine();advance(w)
  assert open_keys(w)==expected
  if 'early:1' in expected:
   incident=w.db.conn.execute("SELECT severity FROM incidents WHERE check_id='m2' AND key='early:1' AND state='open'").fetchone()
   assert incident[0]==('warn' if public else 'info')
 # velocity: 51 registrations exactly fits, then chain limit drops by 1.
 s.register(ETH_OWNER,ETH_OPS,keys(51,0));c.mine();tick(w,c)
 assert 'velocity:1' not in open_keys(w)
 stub_params(c,validators_per_operator_limit=1450)
 c.emit_event('ModuleUpgraded',['0x'+u256(1).hex()],u256(1450));c.mine();advance(w)
 assert 'velocity:1' in open_keys(w)

@pytest.mark.parametrize('monitor',['m1','m2'])
def test_initial_empty_inventory_is_health_never_silent_clean(tmp_path,monitor):
 if monitor=='m1':
  c=ClusterChain();s=MiniSSV(c)
  cid,st=s._get(OWNER2,OPS2);st['count']=9;s._persist(cid,st);c.mine()
  c,w,s=make_m1(tmp_path,chain=c,ssv=s,inventory={'api_url':'https://api.invalid','api_refresh_s':0})
  w.inventory.http_get=lambda url:{'clusters':[],'pagination':{'pages':1}}
 else: c,w,s,rows=setup(tmp_path,eth=0);rows.clear()
 advance(w)
 print('EMPTY-FIRST',monitor,[r['key'] for r in outbox(w,"kind='health'")])
 assert outbox(w,"kind='health' AND check_id='"+monitor+"'"), 'empty API is UNKNOWN, including initial run without tracked keys'

@pytest.mark.parametrize('monitor',['m1','m2'])
def test_resolved_key_reorg_with_complete_api_recovers(tmp_path,monitor):
 if monitor=='m1':
  c,w,s,rows,_,cl=api_only_key(tmp_path)
  tick(w,c); assert incidents(w,'open')
  fork=c.number;cid,st=s._get(OWNER2,OPS2);st['balance']+=9;s._persist(cid,st)
  rows[0]=row(s,OWNER2,OPS2);tick(w,c);assert incidents(w,'open')==[]
  c.reorg(fork);c.mine(2)
  # A wrong-hash tuple positively lists the cluster, so the full inventory flags it.
  advance(w);assert len(incidents(w,'open'))==1 and w.db.meta('reorg:m1') is None
 else:
  c,w,s,rows=setup(tmp_path,eth=2981,private=True,m2={'early_headroom':1})
  advance(w);fork=c.number
  saved=dict(s._get(LEG,LEG_OPS)[1]);cid,st=s._get(LEG,LEG_OPS)
  st['balance']+=7;s._persist(cid,st);rows[0]=row(s,LEG,LEG_OPS,migrated=False)
  set_operator(c,1,eth=2900,ssv=20,private=True);tick(w,c)
  assert 'hard:1' not in open_keys(w)
  c.reorg(fork);st.update(saved);set_operator(c,1,eth=2981,ssv=20,private=True);c.mine(2)
  rows[0]=row(s,LEG,LEG_OPS,migrated=False)
  advance(w);assert 'hard:1' in open_keys(w) and w.db.meta('reorg:m2') is None

@pytest.mark.parametrize('private,severity,route',[(True,'info','discord'),(False,'warn','telegram')])
def test_m2_cadence_and_routing_with_repeated_sweeps(tmp_path,private,severity,route):
 c,w,s,rows=setup(tmp_path,eth=2100,private=private)
 advance(w)
 opened=[r for r in outbox(w) if r['check_id']=='m2' and r['kind']=='open']
 assert len(opened)==1 and opened[0]['severity']==severity and opened[0]['delivery']==route
 before=len(w.notifier.sent)
 tick(w,c,seconds=86399)
 assert len(w.notifier.sent)==before
 tick(w,c,seconds=1)
 assert len(w.notifier.sent)==before+(0 if private else 1)
```

## Appendix B — pre-repair sensitivity setup

The measured run copied the current `reconcile/` tree excluding caches/venv into `/tmp/cp2d-qa2/pre-repair`, then replaced the three implementation files below with their exact 588888b blobs. They are the complete executable-source change since that head; current repair regression tests are deliberately retained. `config.example.json` is not loaded by these selected tests (the harness supplies explicit configuration). Exact reproduction:

```python
import pathlib, shutil, subprocess, os
root = pathlib.Path.cwd()
dst = pathlib.Path('/tmp/cp2d-qa2/pre-repair')
shutil.copytree(root/'reconcile', dst/'reconcile', dirs_exist_ok=True,
               ignore=shutil.ignore_patterns('.venv', '__pycache__', '.pytest_cache'))
for name in ['m1_cluster_abi.py', 'm1_stale_tuple.py', 'm2_operator_capacity.py']:
    (dst/'reconcile/checks'/name).write_bytes(
        subprocess.check_output(['git', 'show', '588888b:reconcile/checks/'+name]))
result = subprocess.run([str(root/'reconcile/.venv/bin/python'), '-m', 'pytest', '-q',
    '-p', 'no:cacheprovider', 'reconcile/tests/test_m1.py', 'reconcile/tests/test_m2.py',
    'reconcile/tests/test_m1_m2_sweep.py'], cwd=dst,
    env={**os.environ, 'PYTHONDONTWRITEBYTECODE':'1'})
raise SystemExit(result.returncode)
```

## Appendix C — fresh independent archive authentication

<!-- authenticate_six.py -->
```python
import concurrent.futures,json,pathlib,sys,urllib.request
ROOT=pathlib.Path.cwd();sys.path[:0]=[str(ROOT/'reconcile'),str(ROOT/'reconcile/tests')]
from eb_events import decode_log
from eb_storage import cluster_id
endpoint='https://eth-pokt.nodies.app'
def check(path):
 d=json.loads(path.read_text());tx=d['tx_hash']
 try:
  req=urllib.request.Request(endpoint,json.dumps({'jsonrpc':'2.0','id':1,'method':'eth_getTransactionReceipt','params':[tx]}).encode(),{'Content-Type':'application/json','User-Agent':'ssv-cp2d-qa2/1.0'})
  with urllib.request.urlopen(req,timeout=25) as response:body=json.load(response)
  rec=body.get('result');assert rec,body
  assert rec['blockHash'].lower()==d['block_hash'].lower()
  assert int(rec['blockNumber'],16)==d['block']
  actual={int(l['logIndex'],16):l for l in rec['logs']}
  expected=[l for l in d['logs'] if l['transactionHash'].lower()==tx.lower()]
  for log in expected:
   a=actual[int(log['logIndex'],16)]
   for k in ('address','topics','data','blockHash','blockNumber','transactionHash','transactionIndex','logIndex'):assert a[k]==log[k],k
  for g in d['groups']:
   evs=sorted([decode_log(actual[i]) for i in g['log_indexes']],key=lambda e:e.log_index)
   ev=evs[-1];t=ev.cluster
   assert '0x'+cluster_id(ev.owner,ev.operator_ids).hex()==g['cluster_id']
   assert [t.validator_count,t.network_fee_index,t.index,t.active,str(t.balance)]==g['last_tuple']
  return dict(file=path.name,tx=tx,endpoint=endpoint,result='PASS',logs=len(expected))
 except Exception as e:return dict(file=path.name,tx=tx,endpoint=endpoint,result='NOT RUN' if isinstance(e,OSError) else 'FAIL',error=repr(e))
paths=sorted(pathlib.Path('reconcile/tests/fixtures/receipts').glob('*.json'))[:6]
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as p:rows=list(p.map(check,paths))
pathlib.Path('/tmp/cp2d-qa2/authenticity.json').write_text(json.dumps(rows,indent=2))
for row in rows:print(json.dumps(row),flush=True)
sys.exit(0 if all(x['result']=='PASS' for x in rows) else 1)
```

Measured results (`https://eth-pokt.nodies.app`, exit 0):

```json
[
  {
    "file": "24920792-26136c50.json",
    "tx": "0x26136c505e25df23dd6580119802e9d38dc1a548a677e9b7803d46177c6f93bd",
    "endpoint": "https://eth-pokt.nodies.app",
    "result": "PASS",
    "logs": 2
  },
  {
    "file": "24921171-147e813e.json",
    "tx": "0x147e813eca11f6e1a4a2724e057e912f71470b9e58ea074a3bcf0806bba3af19",
    "endpoint": "https://eth-pokt.nodies.app",
    "result": "PASS",
    "logs": 2
  },
  {
    "file": "24926844-77ea23fc.json",
    "tx": "0x77ea23fcacd9f3f47db52e37db0eddbf7e10690a95083be9decff2238ea42aaf",
    "endpoint": "https://eth-pokt.nodies.app",
    "result": "PASS",
    "logs": 2
  },
  {
    "file": "25014341-83ece9f7.json",
    "tx": "0x83ece9f73ec5700756916834fb0e8793ed3264f416199597ca0527e92fb86616",
    "endpoint": "https://eth-pokt.nodies.app",
    "result": "PASS",
    "logs": 2
  },
  {
    "file": "25298236-9b2cb18d.json",
    "tx": "0x9b2cb18dd15221d9b7ece2b4e3742faa9fe81a2c5d5ea7dada0304f96ba73e97",
    "endpoint": "https://eth-pokt.nodies.app",
    "result": "PASS",
    "logs": 2
  },
  {
    "file": "25431564-3e18fe4f.json",
    "tx": "0x3e18fe4f9cd2dede123196b43f774cab7f8ebd878c360cf435acbc1b486ee8c4",
    "endpoint": "https://eth-pokt.nodies.app",
    "result": "PASS",
    "logs": 2
  }
]
```

## Appendix D — fixture byte/hash identity against attempt 1's frozen head

Each file's SHA-256 equals SHA-256 of `git show 588888b:<path>` and the raw bytes compare equal (12/12). `git diff --exit-code 588888b HEAD -- reconcile/tests/fixtures/receipts` also returns 0. Hashes:

| File | SHA-256 (both revisions) |
|---|---|
| 24920792-26136c50.json | `80f9839bed31d34050fc86403c1563f7685f3816efeb29a1c85355bdc0ff24d3` |
| 24921171-147e813e.json | `940a300c0b39b4e80db27c6638686e848f3d5dae6586211daa43bdea15055f60` |
| 24926844-77ea23fc.json | `1b307e87bb26f784a7b9c52d2167742acb4ebc8f82d8244f6c44e33afb1942c5` |
| 25014341-83ece9f7.json | `8098f9f078d0280facac713b4401caf7cccb8a22fd7aee1ca1239c97639954ef` |
| 25298236-9b2cb18d.json | `0b056c187db50451f0d04b09cc12edd396f37b5ac1fd8aa7f0688f11caf233c3` |
| 25431564-3e18fe4f.json | `bb79a7f01f317eaa16b60f0ee9862d6e1860995e8b1d92458b782d9ad8e9ffb2` |
| 26232215-4fc63d09.json | `d29a390ef88d78d87527054f344ea639b113feb566c125d7225cfe728cae25de` |
| 26233130-f10f4666.json | `a5649d342bbfe4ce23fbdf36b82840b77a3351c2008045b9b6d6333de944ca69` |
| 26233260-38d732e6.json | `43d88d461e3e03b3ad37470a6738945c803274873ad898fa5c8806c4a91e6259` |
| 26233303-84ffbeb6.json | `3a2a1fd54910bed137d66a258a47725f0c71552d3a40f970a68a0f1d2d293c43` |
| 26233389-ff3292f8.json | `5947166aaebe1ea0f4d462b82e49297789a43a899200cc137de0df9881404472` |
| 26240953-ed9e6cb4.json | `b281d6f6a8c801ff747c2d2b657a71544aaa9baac2510e17068a23e772f30bd5` |

Reproduce identity:

```python
import pathlib, hashlib, subprocess
for p in sorted(pathlib.Path('reconcile/tests/fixtures/receipts').glob('*.json')):
    old = subprocess.check_output(['git','show','588888b:'+str(p)])
    assert old == p.read_bytes()
    print(p.name, hashlib.sha256(old).hexdigest())
```

The reused measurement source is [attempt 1 §3 and Appendix B](cp2d-attempt-1.md), explicitly allowed by this dispatch after byte/hash identity. Current scratch `/tmp/cp2d-qa/live_auth.json` contains an earlier partial drpc attempt rather than the durable report's nodies result; it is **not** used as successful evidence. The six new nodies authentications above are measured independently in this attempt. The prior pre-call-input script/result and durable report remain the stated basis for the unchanged synthetic input provenance; no new network call is claimed for it.

## Appendix E — independent mutation driver

<!-- falsify_independent.py -->
```python
import pathlib,subprocess,shutil,os,json
ROOT=pathlib.Path.cwd();dst=pathlib.Path('/tmp/cp2d-qa2/independent-mutant')
shutil.copytree(ROOT/'reconcile',dst/'reconcile',dirs_exist_ok=True,ignore=shutil.ignore_patterns('.venv','__pycache__','.pytest_cache'))
python=str(ROOT/'reconcile/.venv/bin/python');env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'}
cases=[('QA2-M1-D9','inventory.py','_hash_word(cl) == stored','_hash_word(cl) != stored','first_sweep_has_no_baseline_and_wrong_hash_tuple'),('QA2-M2-LIVE','checks/m2_operator_capacity.py','limit = int(ctx.params.validators_per_operator_limit)','limit = 3000','live_limit_crosses_exact_hard_early_and_velocity_boundaries')]
results=[]
for name,path,old,new,expr in cases:
 p=dst/'reconcile'/path;original=p.read_bytes();assert original.decode().count(old)==1
 cmd=[python,'-m','pytest','-q','-p','no:cacheprovider','/tmp/cp2d-qa2/test_adversarial.py','-k',expr]
 try:
  p.write_text(original.decode().replace(old,new));red=subprocess.run(cmd,cwd=dst,env=env,text=True,capture_output=True)
 finally:p.write_bytes(original)
 assert original==p.read_bytes()==(ROOT/'reconcile'/path).read_bytes()
 green=subprocess.run(cmd,cwd=dst,env=env,text=True,capture_output=True)
 row=dict(name=name,command=cmd,mutation=[old,new],red=red.returncode,green=green.returncode,red_summary=red.stdout.splitlines()[-1],green_summary=green.stdout.splitlines()[-1],restored=True)
 pathlib.Path('/tmp/cp2d-qa2/'+name+'.out').write_text(json.dumps(row,indent=2)+'\nRED\n'+red.stdout+red.stderr+'\nGREEN\n'+green.stdout+green.stderr)
 print(json.dumps(row),flush=True);results.append(row)
pathlib.Path('/tmp/cp2d-qa2/independent-falsifiers.json').write_text(json.dumps(results,indent=2))
assert all(r['red']==1 and r['green']==0 for r in results)
```
