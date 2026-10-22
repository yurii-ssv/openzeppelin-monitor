FAIL

# CP2d independent QA — attempt 3 (capped round), after repair 2

Frozen head under test: `6a93e1a9c0f0c345da68eaab8cc5358852e5f9aa` (lane `mtabasco/ms-cp2d`; implementation `560f92f`, records `1594764`, status commit `6a93e1a` touches only `plan.md`). QA branch `mtabasco/ms-qa-cp2d-3`. Task `task_527ba7657316`, Dispatch `ctx_6a9a342fb10e`. QA owner: Claude Opus 5.5 high (D13). Measured 2026-10-22 (UTC 08:00–08:20). All commands run from the worktree root; independent and mutation runs use `PYTHONDONTWRITEBYTECODE=1` and `-p no:cacheprovider`. Scratch: `/tmp/cp2d-qa3`. No implementation repair.

**Round policy.** The lane has had two failed rounds, so every finding is classified BLOCKING (correctness or safety) or NOTE, each with a one-line reason. One BLOCKING finding remains, so the verdict is **FAIL**.

## 1. Findings

All four findings from attempt 2 are fixed. On this head, attempt 2's Appendix A gives **24 passed** and attempt 1's suite gives **29 passed**. My own suite (Appendix A, 15 cases) gives **5 failed, 10 passed**. Four of those failures are one BLOCKING defect (F1). The fifth is a NOTE (N1).

### F1 — BLOCKING (M1): a reorg that orphans the repair behind a "poisoned and repaired before this run" report leaves the cluster poisoned with no message sent

**Class:** reorg of resolved or cleared state (DEFECT-CLASSES 2) together with events between sweeps (class 5). **Why BLOCKING:** the alert is dropped. The last message the operators received says the cluster was repaired and will resolve at the next run. The canonical chain now holds an unrepaired poisoning, but the incident is changed silently in the database and nothing is sent. The warn re-alert can send it up to 24 h later (6 h for a page).

```sh
PYTHONDONTWRITEBYTECODE=1 reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider -s /tmp/cp2d-qa3/test_attack3.py -k "transient and (reorg or deep)"
```

**Result: exit 1, 4 failed, 1 passed** (the passing case is the control).

**Scenario:** run 1 sees a poisoning in block b1 and a zero-value repair in block b2. It sends `⚠️ [WARN] Cluster poisoned and repaired before this run … Resolved at the next run` (the repair-2 G1 feature, `transient=True`). Before the next run, a reorg replaces the chain from b2 onwards. The poisoning in b1 survives and is not repaired.

- **Expected:** the reorg run sends the live state, "Stale cluster tuple … send a zero-value deposit with the repair tuple", as an update or a new incident.
- **Actual:** the reorg run rewrites the open incident's title to `Stale cluster tuple (M1 #91419)`, removes `transient` from the evidence and keeps severity warn. The `outbox` holds only the earlier transient message and the framework's generic `reorg` health message. The M1 outbox count stays 1 → 1.

**Variants that fail the same way:**
- `[True]`: a restart between the rewind and the reorg run.
- `test_m1_control_transient_page_then_reorg_orphans_the_repair`: a page-severity transient re-poison.
- `test_m1_transient_then_deep_reorg_with_confirmations`: confirmations 3 and a reorg deeper than the confirmation window. The incident title is `Stale cluster tuple`, but no message is sent.

**Control (passes):** if the transient incident has already been resolved at the next run, the same reorg reopens it and sends `('warn','open')` (`test_m1_control_transient_resolved_then_reorg_orphans_the_repair_reopens_and_sends`).

**Root cause** (reading, after measuring):
- `_finding` gives the transient branch the revision `poison:{tx}:{log_index}` (`reconcile/checks/m1_stale_tuple.py:636`). The stale branch uses the identical string (`:619`).
- `alerts.py` re-sends only on a severity escalation or a new revision (`alerts.py:150-152`).
- The change from "repaired" to "poisoned now" therefore counts as neither.
- No repair-2 test covers a reorg while a transient incident is still open. `test_m1_poison_repaired_before_the_run_is_not_re_alerted_after_a_reorg_re_detects_it` only covers a reorg after the transient incident resolved, where the same transactions are re-included.

### N1 — NOTE (M2): on a first run, a partial SSV API answer that omits every legacy cluster of an operator leaves that operator unchecked, with no alert or health message

**Why NOTE:**
- The case needs a fresh database, an API that omits all of one operator's legacy clusters, and no stored log for those clusters.
- After any complete answer, `m2_clusters` keeps the clusters, and a later partial answer is then covered: attempt 2's partial and wrong-hash tests pass.
- Spec M2 names the API as the source of legacy membership, and the record discloses this as known limit (1).
- No incident is resolved by it.
- The coordinator may escalate it.

```sh
PYTHONDONTWRITEBYTECODE=1 reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider -s /tmp/cp2d-qa3/test_attack3.py -k first_run_partial
```

**Result: exit 1.** Operator 5 has an ETH count of 2,990 and 20 legacy validators on chain, in one active legacy cluster that the API does not list (the API lists one other cluster).
- **Expected:** the early rule fires (gap = 10 − 20 < 900, which needs only on-chain counts), or `m2-inventory` / `m2-hard-unknown` health.
- **Actual:** `open_keys=[]` and no M2 health message.

**Cause:** `ops` is derived only from inventory clusters, persisted history, reverts and keys (`m2_operator_capacity.py:138-141`), so an operator that none of these mention is never read.

**Suggested owner fix:** enumerate operator ids on chain (`getOperatorByIdSSV`) for the early rule and the `missing` health check.

### Other NOTEs (no reproducer of a defect; each recorded with its reason)

- **N2 — NOTE (coordinator ruling A):** a transient `Cluster tuple unknown` warn appears while the SSV API is ahead of the confirmed checkpoint. This is an accepted, pre-existing NOTE. I did not re-measure it beyond reading the committed repro (`workers/cp2d/evidence/repro_api_ahead_of_checkpoint.py`).
- **N3 — NOTE (M2 velocity window counts blocks, not 24 h):** `velocity_window_blocks` is 7,200 blocks and is anchored at the run's checkpoint. Measured: `test_m2_velocity_counts_only_the_window` passes, and attempt 2's live-limit test crosses `51 == 51` (silent) and `51 > 50` (info).
  - Reason: blocks never outnumber 12 s slots, so 7,200 blocks always cover at least 24 h. The window can only be a superset of the spec's, so it never misses a registration the spec counts. The worst case is an info message on a borderline count.
  - DEFECT-CLASSES 4 prefers timestamps; this is for the owner to decide.
- **N4 — NOTE (ruling consistency across a block boundary):** in one block, unverified → clean → poison pages as an `unverified intra-block re-poison (no trace)`, as the ruling requires (`test_m1_unverified_then_clean_then_poison_in_one_block_pages`, passes). The same three transactions with a block boundary before the poisoning give **warn**: `U-C | P [('warn','open')]`, `re_poisoned=False`.
  - Reason: spec M1 line 5 says re-poison is "flagged again after a repair", and nothing was flagged in the earlier block, so warn is defensible. The ruling's "louder when ambiguous" reading stops at the block boundary.
  - For the owner when the ruling is amended into spec M1. A silently ignored unverified transaction followed by a clean one (no health message since repair 2) falls under the same note.
- **N5 — NOTE (repair-tuple search when both storage words are set):** `_repair_event` sets `stored = 0` when both the ETH and the legacy cluster word are set. It then returns `None` without the Views fallback (`m1_stale_tuple.py:298-302`).
  - Reason: the alert is still sent, with "Repair tuple: unknown". The fake chain models that state as IncorrectClusterState and the protocol should never produce it. Only the runbook convenience is lost.
- **N6 — NOTE (equivalent mutant, QA tooling):** my first draft of falsifier QA3-M1-interleaved (drop the `e.cluster != final.cluster` filter) stayed green. It is an equivalent mutant: a stale final tuple can never equal the storage hash. I replaced it (§4). This is not a product defect.

## 2. Claims I could NOT confirm

- **"F1–F4 fixed" in full generality:**
  - I confirmed attempt 2's reproducers (24/24) and attempt 1's (29/29).
  - I confirmed the 20 targeted regressions and 72 sweep walks are red on `cba2fb3`.
  - My new F1 shows that the repair-2 G1 feature (transient report) adds a reorg/lifecycle path that neither the state table nor the 120-walk sweep covers. In the state table, row "M1 incidents (cluster, `tx:` keys), incl. resolved", column (c), does not cover an open transient incident.
- **State table "every cell has a test":** not confirmed, for the cell above.
- **The sweep's "no dropped page for surviving evidence":** not confirmed, because the sweep does not assert that a message is sent when an open incident's meaning changes.
- **No real poisoned receipt exists on mainnet:** not independently re-established. I ran no history scan; this is the implementer's and the spec baseline's claim.
- **DEF-3 fork PoC:** NOT RUN (deferred). I checked that the implementer does not claim one: the record says "Fork reproduction with a malicious owner contract stays DEF-3" and "NOT RUN: … DEF-3".

Everything else in the repair-2 verification table was reproduced (§3).

**Receipt evidence judgment.** All 12 fixtures are now authenticated from chain:
- Attempt 2 checked files 1–6.
- This attempt checked files 7–12 against `https://eth-pokt.nodies.app`: logs, block hash, block number, cluster id and last-tuple fields are equal, 6/6 PASS, exit 0.
- The fixtures are byte-identical to `588888b` (`git diff --exit-code 588888b HEAD -- reconcile/tests/fixtures/receipts` exits 0). The SHA-256 hashes match attempt 2's Appendix D.

The evidence is adequate for decoding, for the validate/no-validate predicate and for repair-tuple selection:
- 12 faithful replays validate.
- 11 synthetic stale-final-tuple replays are built from each transaction's own pre-call Cluster argument, whose archive answer is a real IncorrectClusterState revert (attempt 1 measured this; the inputs are unchanged).

What a real poisoned receipt would add: proof that the callback, event and storage ordering occurs in production, and that the real callback paths (`bulkRegisterValidator` or a deposit inside an ETH transfer) emit the shape M1 groups.

My own synthetic cases cover:
- two clusters interleaved in one transaction (each gets its own repair tuple);
- reordered events, canonical last (silent);
- a decoy before a canonical tuple that appears twice (the earliest validating log is chosen);
- same-block unverified → clean → poison;
- re-poison after repair across restarts.

All of these pass.

**D9 (API tuples untrusted).** The reasoning holds and was measured:
- The inventory admits an API tuple only when its hash equals the storage word at the evaluated block, and the version is taken from storage.
- M1 never lets an API tuple override a stored last log: `test_m1_inventory_wrong_hash_for_a_cluster_with_a_valid_last_log_is_silent` passes.
- M2's tuple history is used only on a storage-hash match, and a forged 1-validator tuple does not replace the 20-validator one (attempt 2's `open_keys_partial_and_wrong_hash_api_are_not_recovery`, 4 cases, pass).
- The calldata tuple of a reverted call is persisted unvalidated, but it is used only on a storage-hash match (`_legacy`), which is safe.

**Known limit (intra-block differing tuples).** Health "not counted as repair" is replaced by the coordinator's ruling: a page as `unverified intra-block re-poison (no trace)`. Measured:
- attempt 2's F2 and F3 cases page;
- my unverified → clean → poison case pages.

This is acceptable for spec M1 under the ruling. N4 records the cross-block boundary of that ruling.

## 3. Verification matrix

**Preflight:**
- HEAD `6a93e1a9c0f0c345da68eaab8cc5358852e5f9aa` equals the dispatch head.
- Initial `git status --porcelain` was empty.
- 343 GiB free.
- `uv venv reconcile/.venv --python 3.12` and `uv pip install --python reconcile/.venv/bin/python -r reconcile/requirements-dev.txt` both exit 0; Python 3.12.15.
- anvil is at `$HOME/.foundry/bin` but is not needed: no fork row is owned.
- No mainnet transaction. Alerts use record/Capture only. I did not touch config/, config.zip or .env*.

**Frozen-file proof:**
- `git diff --exit-code plan/monitoring-service HEAD -- reconcile/tests/harness.py reconcile/tests/test_framework.py reconcile/tests/test_worker.py` → 0. The same command against `500abe4` → 0.
- `git diff --name-status 500abe4 HEAD -- reconcile` lists only `checks/__init__.py` (M), `config.example.json` (M) and the lane's new files (A).
- No other frozen CP1B file was edited.
- Since `cba2fb3`, only `checks/m1_stale_tuple.py`, `checks/m2_operator_capacity.py`, the three lane test files and `workers/cp2d/**` changed.

| V-ID / measurement | Exact command | Exit / counts | Outcome |
|---|---|---|---|
| V2d.1 / V2x.1 M1 | `reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider reconcile/tests/test_m1.py` | 0; 71 passed | committed scenarios pass; completeness FAIL F1 |
| V2d.1 / V2x.1 M2 | `… reconcile/tests/test_m2.py` | 0; 47 passed | pass; N1 |
| V2d.2 / V2x.2 | `… test_m1.py test_m2.py -k thresholds_from_chain` | 0; 2 passed, 116 deselected | PASS; plus my live limit 3000→3001→3000 test (pass, falsified §4) |
| V2d.3 / V2x.3 | `… test_m1.py test_m2.py test_m1_m2_sweep.py -k restart` | 0; 6 passed, 232 deselected | PASS for these; my transient restart-once passes; F1 `[True]` restart variant FAIL |
| reorg | `… test_m1.py test_m2.py -k reorg` | 0; 28 passed, 90 deselected | committed pass; F1 |
| V2d.7 replay | `… reconcile/tests/test_m1_receipts.py` | 0; 24 passed (count + 12 faithful + 11 synthetic) | PASS |
| V2d.7 LIVE authenticity (files 7–12) | `reconcile/.venv/bin/python -I /tmp/cp2d-qa3/authenticate_last6.py` (attempt 2 Appendix C with `[6:]`; endpoint `https://eth-pokt.nodies.app`) | 0; 6/6 PASS (5, 60, 22, 16, 24, 63 logs) | PASS; files 1–6 reused from attempt 2 after the byte-identity proof |
| committed sweep | `… reconcile/tests/test_m1_m2_sweep.py` | 0; 120 passed | PASS (bounded model; misses F1) |
| V2d.6 / V2x.6 suite | `… -m pytest -q -p no:cacheprovider reconcile/tests` | 0; 502 passed, 0 skips (localhost metrics test passed in this environment) | PASS |
| V2d.6 lint | `reconcile/.venv/bin/ruff check reconcile` | 0; All checks passed! | PASS |
| V2d.5 / V2x.5 falsifiers | `PYTHONDONTWRITEBYTECODE=1 reconcile/.venv/bin/python plans/monitoring-service/workers/cp2d/falsify_repair2.py /tmp/cp2d-qa3/mutant` | 0; 41/41 mutated exit 1 → restored exit 0 | PASS (incl. F-M1a–d, F-M2a–d) |
| new regressions vs `cba2fb3` | scratch copy `/tmp/cp2d-qa3/pre-repair2` with `checks/{m1_stale_tuple,m2_operator_capacity,m1_cluster_abi}.py` from `git show cba2fb3:…`, then `… -m pytest -q -p no:cacheprovider reconcile/tests/test_m1.py reconcile/tests/test_m2.py reconcile/tests/test_m1_m2_sweep.py` | 1; 92 failed, 146 passed (20 targeted in test_m1/test_m2 + 72 walks) | PASS (sensitivity claim reproduced exactly) |
| attempt 2 reproducers | extracted unmodified from `qa/cp2d-attempt-2.md` Appendix A → `/tmp/cp2d-qa3/qa2_test_adversarial.py`; `… -m pytest -q -p no:cacheprovider` | 0; 24 passed | PASS (F1–F4 of attempt 2 fixed) |
| attempt 1 reproducers | `/tmp/cp2d-qa3/qa1_test_independent.py` (extracted; differs from `/tmp/cp2d-qa/test_independent.py`, SHA-256 `1d49099c…`, only by a trailing newline) | 0; 29 passed | PASS |
| QA3 independent suite | `… -m pytest -q -p no:cacheprovider /tmp/cp2d-qa3/test_attack3.py` (Appendix A) | 1; 5 failed, 10 passed | FAIL: F1 (4), N1 (1) |
| QA3 falsifiers | `reconcile/.venv/bin/python /tmp/cp2d-qa3/falsify_qa3.py` (Appendix B) | 0; 3/3 red → restored green | PASS |
| V2x.4 | not owned; part of the suite run | `rpc_budget` passes inside the 502 | — |

**Budget:**
- The full suite ran once.
- The committed falsifier driver ran once.
- The receipts were authenticated only for the six that attempt 2 did not fetch. The other six, and attempt 1's 47 Views, 24 storage and 11 pre-call measurements, were reused after confirming byte identity of the fixtures.
- No history scan and no fork.

### Spec-derived triggers (my derivation from spec M1/M2) → trigger test at severity / benign twin

| Spec line | Trigger (severity) | Benign twin (silent) | Measured |
|---|---|---|---|
| M1 1–3: group ≥2 per tx and cluster, last tuple does not validate → alert, repair tuple | `test_m1_poisoned_receipt_warns_with_repair_tuple` (warn); 8 event kinds `repair_tuple_tie_is_the_earliest_log_for_every_event_kind`; mine: two-cluster interleaved, decoy + double-canonical earliest | `multi_event_receipt_that_validates_is_silent`, `single_event_transactions_are_not_validated`; mine: reordered canonical-last | pass |
| M1 4: our `updateClusterBalance` / `liquidate` IncorrectClusterState revert | `inbox_incorrect_cluster_state_from_our_sender_warns`, `inbox_liquidator_revert_on_poisoned_cluster_feeds_the_same_incident` (warn) | `inbox_revert_benign_twins_are_silent[stranger,other_error,other_function]` | pass |
| M1 5: re-poison after repair → page | `re_poison_after_repair_pages`, `same_block_repair_then_re_poison_pages`; mine: across restarts (page once) | `second_poisoning_without_repair_is_not_a_re_poison` (warn) | pass |
| M1 severity: poisoned and liquidatable → page | `poisoned_and_liquidatable_pages`, `thresholds_from_chain_liquidation_period_change_escalates_to_page` | non-liquidatable poisoning stays warn | pass |
| M1 (plan): inventory unknown feeds the incident | `inventory_hash_mismatch_is_unknown_warn…[False]` | `[True]`; mine: forged API tuple vs valid last log | pass |
| M1 ruling: unverified intra-block re-poison | attempt 2 F2/F3 cases; mine: U→C→P one block (page) | `unverified_transaction_before_a_first_poisoning_stays_warn` | pass; N4 |
| M1 events between sweeps (class 5) | `poison_repaired_before_the_run_still_alerts` (warn/page transient) | — | pass; **its reorg path FAIL F1** |
| M2 hard → warn | `hard_blocked_active_legacy_cluster_warns`; mine: limit moved on the chain | `hard_boundary_cluster_that_exactly_fits_is_silent`, `hard_ignores_liquidated_legacy_clusters` | pass |
| M2 early < 900 → info, warn if public | `early_headroom_below_900_is_info_and_warn_if_public`; attempt 2 custom-threshold boundaries and routing | `early_headroom_exactly_900_is_silent` | pass; N1 |
| M2 velocity > headroom → info | `velocity_registrations_above_headroom_is_info` | `velocity_registrations_equal_to_headroom_are_silent`, window ageing | pass; N3 |
| M2 `migrateClusterToETH` / `reactivate` ExceedValidatorLimitWithData → warn | `inbox_exceed_validator_limit_revert_warns_then_settles`; attempt 1 `each_inbox_function_warns` (both functions) | `inbox_revert_benign_twins_are_silent[other_error,other_function]` | pass |

### DEFECT-CLASSES attack on every persisted key (classes 1–6, 10)

| Persisted key / input | Attacks run (by whom) | Result |
|---|---|---|
| M1 `m1_detections` receipt poison/repair rows | same block with two and three transactions (attempt 2 + mine); interleaved clusters (mine); reorg depth 1 and N; re-inclusion; repair orphaned (committed); restart (mine) | pass |
| M1 transient report (G1, poison rows `ingested_block = run`) | reorg orphaning the repair while open, depth 1 and beyond confirmations, restart, page variant (mine) | **F1** |
| M1 revert rows and `cleared_block` | orphaned inbox, orphaned clearing run (committed); attempt 1's settlement reproducer | pass |
| M1 incidents incl. resolved | attempt 2's resolved-key reorg, partial API, normal and restart; complete-API control | pass |
| `meta reorg:m1` | attempt 2's partial-API reorg (marker kept until evaluable) | pass |
| `check:m1:first_run_block` / receipt window | `downtime_backlog…` (committed); first sweep without a baseline (attempt 2's tests) | pass |
| M1 inputs: SSV API empty / partial / wrong-hash / error | attempt 2's open-key partial and wrong-hash; initial-empty (F4 fixed); mine: forged tuple vs log | pass; N2 |
| M2 `m2_clusters` history | attempt 2's resolved-hard reorg with partial API (normal and restart); wrong-hash forged tuple | pass |
| M2 `m2_reverts` and `cleared_block` | committed orphaned settlement; attempt 1's reproducers | pass |
| M2 incidents hard / early / velocity incl. resolved | attempt 2's resolved-key reorg; crash between scan and handling; live limit (attempt 2 and mine) | pass |
| M2 inputs: API partial on first run | mine | **N1** |
| `meta reorg:m2` | attempt 2's resolved-key reorg | pass |

## 4. Falsifiers

**Committed (41).** All red then restored green on the scratch copy `/tmp/cp2d-qa3/mutant`; log `/tmp/cp2d-qa3/falsifiers.out`.
- Every row: `mutated exit=1` and `restored exit=0`. Checked with `grep -A2 '^###' … | grep -E 'mutated exit=0|restored exit=1'` (no match, exit 1); the driver exits 0.
- They include F-M1a–d (3, 12, 3, 9 red) and F-M2a–d (1 red each).
- Also: R5/R6/R7/R8 (repair 2), R1–R4 (repair 1) and six sweep-only mutations (S-M1-absence 25/60 walks, S-M1-intra-block 33/60, S-M2-persisted 30/60, S-M2-unsettle 11/60, S-M2-degraded 13/60).

**Own (Appendix B).** Each runs on a scratch copy and asserts that the bytes are restored and equal to the worktree file before the green run:

| Mutation | Test | Red → green |
|---|---|---|
| QA3-M1-earliest: `_repair_event` `next((e for e in cands …` → `reversed(cands)` | `earliest_validating_tuple_when_the_canonical_appears_twice` | exit 1 (1 failed) → exit 0 |
| QA3-M1-interleaved: group by `evs[-1].cluster_id` instead of `ev.cluster_id` | `two_clusters_interleaved_in_one_tx…` | exit 1 (1 failed) → exit 0 |
| QA3-M2-live-limit: `limit = int(ctx.params.validators_per_operator_limit)` → `limit = 3000` | `limit_raised_then_lowered_on_chain…` | exit 1 (1 failed) → exit 0 |

`git status --porcelain` in the worktree was empty after every mutation run.

## 5. Evidence-plane writes

- CP2d `**Status:**` changed from `IMPLEMENTED — AWAITING QA` to **QA FAILED** at frozen head `6a93e1a9c0f0c345da68eaab8cc5358852e5f9aa`, linked to this report.
- One event, **0038**, appended to `plans/monitoring-service/execution-log.md`.
- This report: `plans/monitoring-service/qa/cp2d-attempt-3.md`.
- The NOTEs (N1–N6) are recorded here. §8 "Recorded notes" is outside my write scope, so the coordinator carries them forward.
- `reconcile/.venv` is local and ignored.
- All tests, copies, RPC scripts and logs are under `/tmp/cp2d-qa3`.

## 6. Repo proof

Before the evidence writes, `git status --porcelain` was empty, after all runs and mutations. The proof taken before the commit is reproduced below the appendices.

## Appendix A — QA3 independent scenarios (durable reproducer)

Extract by marker (as in attempt 2), then run from the worktree root:

```sh
reconcile/.venv/bin/python - <<'EXTRACT'
from pathlib import Path
import re
s = Path('plans/monitoring-service/qa/cp2d-attempt-3.md').read_text()
for name in ('test_attack3.py', 'falsify_qa3.py'):
    code = re.search(r'<!-- ' + re.escape(name) + r' -->\s*```python\n(.*?)\n```', s, re.S).group(1)
    p = Path('/tmp/cp2d-qa3') / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(code + '\n')
EXTRACT
PYTHONDONTWRITEBYTECODE=1 reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider -s /tmp/cp2d-qa3/test_attack3.py
```

Measured on `6a93e1a`: exit 1, **5 failed, 10 passed** (F1 ×4, N1 ×1).

<!-- test_attack3.py -->
```python
"""QA attempt 3 (CP2d) independent adversarial scenarios. Expectations derived from spec M1/M2, README 6.2,
qa/DEFECT-CLASSES.md and the coordinator rulings; not from the implementer's scenario list."""
import sys, pathlib, json
ROOT = pathlib.Path.cwd()
sys.path[:0] = [str(ROOT / 'reconcile'), str(ROOT / 'reconcile/tests')]
import pytest
from fakechain import MiniSSV
from fixtures_cluster import *  # noqa
from fixtures_cluster import _emit_cluster
from harness import advance, outbox, api_row, make_worker, VIEWS
from test_m1 import kinds, incidents, evidence, detections, restart, out
from test_m2 import setup, tick as tick2, open_keys, alerts, health, legacy, LEG, LEG_OPS
from checks.m1_stale_tuple import _tup

KEY = key_of(OWNER, OPS)
KEY2 = key_of(OWNER2, OPS2)


def two_tuple_tx(ssv, owner=OWNER, ops=OPS, mine=False):
    """A benign-or-not helper: two deposits in one tx, two distinct tuples, storage at the second (unverified)."""
    cid, st = ssv._get(owner, ops); tx = ssv.c.new_tx(owner)
    for v in (3, 5):
        ssv._touch(st); st['balance'] += v; ssv._persist(cid, st)
        _emit_cluster(ssv.c, tx, 'ClusterDeposited', owner, ops, ssv._cluster(st), v)
    if mine:
        ssv.c.mine()
    return tx


# ------------------------------------------------------------------ M1

@pytest.mark.parametrize('restart_now', [False, True])
def test_m1_transient_report_then_reorg_orphans_the_repair_sends_the_real_poisoning(tmp_path, restart_now):
    """Run 1 sees poison (block b1) and repair (block b2): 'poisoned and repaired before this run' (transient).
    Before the next run a reorg removes b2 only; the poisoning survives on the canonical chain, unrepaired.
    Expected: operators are told the cluster is poisoned now (a message), not left with 'repaired, resolved at next run'."""
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18); advance(w)
    tx, stale, repair = poison(ssv)
    after_poison = dict(ssv._get(OWNER, OPS)[1])
    deposit(ssv)
    b2 = chain.sealed
    advance(w)
    assert kinds(w) == [('warn', 'open')] and evidence(w)['transient'] is True
    n_before = len(out(w))
    chain.reorg(b2)
    ssv._get(OWNER, OPS)[1].update(after_poison)
    chain.mine(2)
    if restart_now:
        w.check_reorg(); w.scan()
        w = restart(tmp_path, chain, w)
    advance(w)
    print("ALL", [(r["kind"], r["check_id"], r["text"][:90]) for r in outbox(w)]); print("INC", [(i["state"], i["severity"], i["title"], i["revision"]) for i in incidents(w)])
    assert incidents(w, 'open') and evidence(w).get('transient') is not True
    assert len(out(w)) > n_before, 'the unrepaired poisoning must be sent; the last message said it was repaired'


def test_m1_unverified_then_clean_then_poison_in_one_block_pages(tmp_path):
    """Ruling reading: unverified (poison) -> clean (repair) -> poison: page, no prior incident needed."""
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18); advance(w)
    two_tuple_tx(ssv)
    deposit(ssv, mine=False)
    poison(ssv)
    advance(w)
    print('U-C-P', kinds(w), evidence(w))
    assert kinds(w) == [('page', 'open')] and evidence(w)['re_poison_unverified'] is True


def test_m1_unverified_and_clean_then_poison_next_block(tmp_path):
    """Same three transactions with a block boundary before the poisoning. Recorded for consistency of the ruling."""
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18); advance(w)
    two_tuple_tx(ssv)
    deposit(ssv)          # mines: block N has unverified + clean
    poison(ssv)           # block N+1
    advance(w)
    print('U-C | P', kinds(w), evidence(w).get('re_poisoned'))
    assert kinds(w) in ([('warn', 'open')], [('page', 'open')])


def test_m1_two_clusters_interleaved_in_one_tx_each_gets_its_own_repair(tmp_path):
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18); deposit(ssv, owner=OWNER2, ops=OPS2, value=10**18); advance(w)
    tx = chain.new_tx(OWNER)
    stales, repairs = {}, {}
    sts = {}
    for o, ops in ((OWNER, OPS), (OWNER2, OPS2)):
        cid, st = ssv._get(o, ops); sts[o] = (cid, st)
        ssv._touch(st); st['balance'] -= 10**15; ssv._persist(cid, st); stales[o] = ssv._cluster(st)
    for o, ops in ((OWNER, OPS), (OWNER2, OPS2)):  # interleaved nested deposits
        cid, st = sts[o]
        ssv._touch(st); st['balance'] += 3 * 10**15; ssv._persist(cid, st); repairs[o] = ssv._cluster(st)
        _emit_cluster(chain, tx, 'ClusterDeposited', o, ops, repairs[o], 3 * 10**15)
    for o, ops in ((OWNER2, OPS2), (OWNER, OPS)):  # stale outers, reverse order
        _emit_cluster(chain, tx, 'ClusterWithdrawn', o, ops, stales[o], 10**15)
    chain.mine(); advance(w)
    assert sorted(i['key'] for i in incidents(w, 'open')) == sorted([KEY, KEY2])
    for o, ops in ((OWNER, OPS), (OWNER2, OPS2)):
        ev = evidence(w, key_of(o, ops))
        assert ev['repair_tuple'] == _tup(repairs[o]) and ev['stale_tuple'] == _tup(stales[o])


def test_m1_reordered_events_stale_first_then_canonical_last_is_silent(tmp_path):
    """Same two tuples, canonical emitted last: validates, silent (benign twin of the poison shape)."""
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18); advance(w)
    cid, st = ssv._get(OWNER, OPS); tx = chain.new_tx(OWNER)
    ssv._touch(st); st['balance'] -= 10**15; ssv._persist(cid, st); stale = ssv._cluster(st)
    _emit_cluster(chain, tx, 'ClusterWithdrawn', OWNER, OPS, stale, 10**15)
    ssv._touch(st); st['balance'] += 3 * 10**15; ssv._persist(cid, st)
    _emit_cluster(chain, tx, 'ClusterDeposited', OWNER, OPS, ssv._cluster(st), 3 * 10**15)
    chain.mine(); advance(w)
    assert kinds(w) == []


def test_m1_earliest_validating_tuple_when_the_canonical_appears_twice_and_a_decoy_precedes(tmp_path):
    """Logs: decoy (non-validating) , canonical C (log 1), stale X, canonical C (log 3), stale final.
    The earliest log whose tuple validates is log 1."""
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18); advance(w)
    cid, st = ssv._get(OWNER, OPS); tx = chain.new_tx(OWNER)
    ssv._touch(st); st['balance'] -= 10**15; ssv._persist(cid, st); stale = ssv._cluster(st)
    decoy = Cluster(stale.validator_count, stale.network_fee_index, stale.index, stale.active, stale.balance + 7)
    _emit_cluster(chain, tx, 'ClusterDeposited', OWNER, OPS, decoy, 7)
    ssv._touch(st); st['balance'] += 3 * 10**15; ssv._persist(cid, st); canon = ssv._cluster(st)
    _emit_cluster(chain, tx, 'ClusterDeposited', OWNER, OPS, canon, 3)
    _emit_cluster(chain, tx, 'ClusterWithdrawn', OWNER, OPS, stale, 1)
    _emit_cluster(chain, tx, 'ClusterDeposited', OWNER, OPS, canon, 0)
    _emit_cluster(chain, tx, 'ClusterWithdrawn', OWNER, OPS, stale, 1)
    chain.mine(); advance(w)
    ev = evidence(w)
    assert ev['repair_tuple'] == _tup(canon) and ev['repair_log_index'] == 1


def test_m1_re_poison_after_repair_across_three_runs_and_restart_pages_once(tmp_path):
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18); advance(w)
    poison(ssv); advance(w)
    deposit(ssv); advance(w)
    w = restart(tmp_path, chain, w)
    poison(ssv); advance(w); advance(w)
    w = restart(tmp_path, chain, w); advance(w)
    assert kinds(w) == [('warn', 'open'), ('info', 'resolved'), ('page', 'open')]


def test_m1_inventory_wrong_hash_for_a_cluster_with_a_valid_last_log_is_silent(tmp_path):
    """D9: a forged API tuple never overrides a stored, validating last log."""
    c = ClusterChain(); s = MiniSSV(c)
    from test_m1 import api_m1
    rows = []
    c, w, s = api_m1(tmp_path, rows, chain=c, ssv=s)
    deposit(s, value=10**18)
    cl = current_tuple(s)
    rows.append(api_row(OWNER, OPS, cl.validator_count, cl.network_fee_index, cl.index, cl.active, cl.balance + 99))
    advance(w); w.clock.advance(10**4); c.mine(); advance(w)
    assert incidents(w, 'open') == []


# ------------------------------------------------------------------ M2

def test_m2_first_run_partial_api_omitting_every_legacy_cluster_of_an_operator(tmp_path):
    """Operator 5's only legacy cluster (20 validators) is omitted by the API, which lists another cluster; it has
    no stored log. Operator 5: eth 2,990 (hard: 2,990 + 20 > 3,000; early gap < 900). Expected under defect class 1:
    health or an alert, never silence. Recorded (spec data source for legacy membership is the API)."""
    c = ClusterChain(); s = MiniSSV(c); rows = []
    legacy(s, rows, count=20)                                    # LEG on ops 1-4, listed
    for op in LEG_OPS:
        set_operator(c, op, eth=0, ssv=20)
    hidden = []
    legacy(s, hidden, owner=OWNER2, ops=OPS2, count=20)         # not listed
    for op in OPS2:
        set_operator(c, op, eth=2990, ssv=20)
    c.mine()
    c, w, _, rows = make_m2(tmp_path, chain=c, api_rows=rows, m2={'velocity_window_blocks': 20})
    c.mine(25); advance(w)
    print('PARTIAL-FIRST', open_keys(w), health(w))
    assert open_keys(w) or any(h for h in health(w) if 'm2' in h)


def test_m2_limit_raised_then_lowered_on_chain_tracks_without_code_change(tmp_path):
    from harness import stub_params
    from eb_storage import u256
    chain, w, ssv, rows = setup(tmp_path, eth=2981, private=True)
    advance(w); assert 'hard:1' in open_keys(w)
    stub_params(chain, validators_per_operator_limit=3001); chain.emit_event('ModuleUpgraded', ['0x' + u256(1).hex()], u256(3001)); chain.mine(); advance(w)
    assert 'hard:1' not in open_keys(w)  # 2981 + 20 = 3001 > 3001 is false: resolved
    stub_params(chain, validators_per_operator_limit=3000); chain.emit_event('ModuleUpgraded', ['0x' + u256(1).hex()], u256(3000)); chain.mine(); advance(w)
    assert 'hard:1' in open_keys(w)
    assert [a for a in alerts(w) if a[2] == 'hard:1'] == [('warn', 'open', 'hard:1'), ('info', 'resolved', 'hard:1'), ('warn', 'open', 'hard:1')]


def test_m1_control_transient_resolved_then_reorg_orphans_the_repair_reopens_and_sends(tmp_path):
    """Control for the transient finding: once the transient incident is resolved, the same reorg reopens and sends."""
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18); advance(w)
    poison(ssv)
    after_poison = dict(ssv._get(OWNER, OPS)[1])
    deposit(ssv)
    b2 = chain.sealed
    advance(w)
    w.clock.advance(601); advance(w)
    assert kinds(w) == [('warn', 'open'), ('info', 'resolved')]
    chain.reorg(b2)
    ssv._get(OWNER, OPS)[1].update(after_poison)
    chain.mine(2); advance(w)
    print('CONTROL', kinds(w))
    assert kinds(w)[-1] == ('warn', 'open') and incidents(w, 'open')


def test_m1_control_transient_page_then_reorg_orphans_the_repair(tmp_path):
    """Transient re-poison (page) then the repair is orphaned: the incident stays page; is the live state sent?"""
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18); advance(w)
    poison(ssv); advance(w)
    deposit(ssv); advance(w)          # resolved
    poison(ssv)
    after_poison = dict(ssv._get(OWNER, OPS)[1])
    deposit(ssv); b2 = chain.sealed
    advance(w)
    assert kinds(w)[-1] == ('page', 'open') and evidence(w)['transient'] is True
    n = len(out(w))
    chain.reorg(b2); ssv._get(OWNER, OPS)[1].update(after_poison); chain.mine(2); advance(w)
    print('PAGE-TRANSIENT-REORG', kinds(w), evidence(w).get('transient'), evidence(w).get('re_poisoned'))
    assert len(out(w)) > n


def test_m1_transient_restart_resolves_once(tmp_path):
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18); advance(w)
    poison(ssv); deposit(ssv); advance(w)
    w = restart(tmp_path, chain, w); advance(w); w.clock.advance(601); chain.mine(); advance(w)
    w = restart(tmp_path, chain, w); w.clock.advance(601); chain.mine(); advance(w)
    assert kinds(w) == [('warn', 'open'), ('info', 'resolved')]


def test_m1_transient_then_deep_reorg_with_confirmations(tmp_path):
    """Same as the transient finding with confirmations=3 and a reorg deeper than the confirmation window."""
    chain, w, ssv = make_m1(tmp_path, confirmations=3)
    deposit(ssv, value=10**18); chain.mine(4); advance(w)
    poison(ssv)
    after_poison = dict(ssv._get(OWNER, OPS)[1])
    deposit(ssv); b2 = chain.sealed
    chain.mine(6); advance(w)
    assert kinds(w) == [('warn', 'open')] and evidence(w)['transient'] is True
    assert w.checkpoint()['block'] - b2 + 1 > 3
    n = len(out(w))
    chain.reorg(b2); ssv._get(OWNER, OPS)[1].update(after_poison); chain.mine(8); advance(w)
    print('DEEP', kinds(w), evidence(w).get('transient'), [i['title'] for i in incidents(w, 'open')])
    assert len(out(w)) > n
```

## Appendix B — QA3 mutation driver (scratch copy only)

<!-- falsify_qa3.py -->
```python
import pathlib, subprocess, shutil, os, json, sys
ROOT = pathlib.Path.cwd(); dst = pathlib.Path('/tmp/cp2d-qa3/qa3-mutant')
shutil.rmtree(dst, ignore_errors=True)
shutil.copytree(ROOT/'reconcile', dst/'reconcile', ignore=shutil.ignore_patterns('.venv', '__pycache__', '.pytest_cache'))
shutil.copy('/tmp/cp2d-qa3/test_attack3.py', dst/'test_attack3.py')
python = str(ROOT/'reconcile/.venv/bin/python'); env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}
cases = [
 ('QA3-M1-earliest', 'checks/m1_stale_tuple.py',
  'match = next((e for e in cands if stored', 'match = next((e for e in reversed(cands) if stored',
  'earliest_validating_tuple_when_the_canonical_appears_twice'),
 ('QA3-M1-interleaved', 'checks/m1_stale_tuple.py',
  'txs.setdefault(ev.cluster_id, {}).setdefault(ev.tx_hash, [])', 'txs.setdefault(evs[-1].cluster_id, {}).setdefault(ev.tx_hash, [])',
  'interleaved'),
 ('QA3-M2-live-limit', 'checks/m2_operator_capacity.py',
  'limit = int(ctx.params.validators_per_operator_limit)', 'limit = 3000',
  'limit_raised_then_lowered'),
]
rows = []
for name, path, old, new, expr in cases:
    p = dst/'reconcile'/path; orig = p.read_bytes(); assert orig.decode().count(old) == 1, name
    cmd = [python, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', 'test_attack3.py', '-k', expr]
    try:
        p.write_text(orig.decode().replace(old, new)); red = subprocess.run(cmd, cwd=dst, env=env, text=True, capture_output=True)
    finally:
        p.write_bytes(orig)
    assert orig == p.read_bytes() == (ROOT/'reconcile'/path).read_bytes()
    green = subprocess.run(cmd, cwd=dst, env=env, text=True, capture_output=True)
    r = dict(name=name, mutation=[old, new], test=expr, red=red.returncode, red_summary=red.stdout.strip().splitlines()[-1],
             green=green.returncode, green_summary=green.stdout.strip().splitlines()[-1])
    print(json.dumps(r)); rows.append(r)
sys.exit(0 if all(r['red'] == 1 and r['green'] == 0 for r in rows) else 1)
```

## Repo proof (taken before the evidence commit)

`git status --porcelain`:

```text
 M plans/monitoring-service/execution-log.md
 M plans/monitoring-service/plan.md
?? plans/monitoring-service/qa/cp2d-attempt-3.md
```

`git diff --name-only 6a93e1a9c0f0c345da68eaab8cc5358852e5f9aa` (tracked changes; the report is the one untracked file above):

```text
plans/monitoring-service/execution-log.md
plans/monitoring-service/plan.md
```

No implementation, test, config or fixture file appears in either list. After the commit, `git diff --name-only 6a93e1a9c0f0c345da68eaab8cc5358852e5f9aa HEAD` lists exactly `plans/monitoring-service/execution-log.md`, `plans/monitoring-service/plan.md`, `plans/monitoring-service/qa/cp2d-attempt-3.md`.
