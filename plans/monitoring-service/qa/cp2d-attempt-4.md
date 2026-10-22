PASS WITH NOTES

# CP2d independent QA — attempt 4 (capped round), after repair 3

**Head and run.**
- Frozen head under test: `24b1599d2489193ef1fa59149b8e6e6b4bbe02bb` (lane `mtabasco/ms-cp2d`; implementation `337329a`, records `563970e`; the status commit `24b1599` touches only `plan.md`).
- QA branch `mtabasco/ms-qa-cp2d-4`. Task `task_e56bd3237a8c`, Dispatch `ctx_50414673e4ea`. QA owner: Claude Opus 5.5 high (D13).
- Measured 2026-10-22, UTC 08:35–09:00. All commands run from the worktree root, with `PYTHONDONTWRITEBYTECODE=1` and `-p no:cacheprovider`. Scratch: `/tmp/cp2d-qa4`. No implementation repair.

**Round policy.** This round is capped, so every finding is classified BLOCKING or NOTE, each with a one-line reason. **No BLOCKING finding.** The verdict is PASS WITH NOTES.

## 1. Findings

**Attempt 3's F1 is fixed.** Attempt 3's Appendix A, extracted unmodified, gives **14 passed and 1 failed**. The one failure is N1, a NOTE that was not required to be fixed. All four F1 reproducers are green.

**The new regressions fail on the start head.** I put `ec58434`'s `checks/m1_stale_tuple.py` into a scratch copy; M2 and the ABI module are unchanged since `ec58434`. On that copy the regressions give **24 failed / 178 passed**: 10 targeted tests in `test_m1.py` and 14 sweep walks. This is the implementer's claim, reproduced exactly.

**My own suite: 14/14 pass.** It is Appendix A, built from DEFECT-CLASSES 1–6 and 10 and aimed mainly at the repair's new revision string. On `ec58434`, 4 of the 14 fail.

**No new defect found.**

### N1 — NOTE (M2, carried from attempt 3, re-measured): a first-run partial API answer that omits every legacy cluster of an operator leaves that operator unchecked

**Why NOTE:**
- It needs a fresh database, an API answer that omits all of the operator's legacy clusters, and no stored log for any of them.
- After one complete answer, `m2_clusters` covers the case.
- The spec names the API as the legacy-membership source.
- The implementer discloses it as known limit (1).

**Reproducer:**

```sh
PYTHONDONTWRITEBYTECODE=1 reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider /tmp/cp2d-qa4/prior/qa3_test_attack3.py -k first_run_partial
```

- **Expected:** an early alert or M2 health.
- **Actual:** `open_keys=[]` and no M2 health. Exit 1 on `24b1599`, as on `6a93e1a`.
- **Fix:** unchanged from attempt 3: enumerate operators on chain for the early rule and the missing-cluster health.

### N3 — NOTE (M2, class 4, re-attacked as the brief asks): the velocity "24 h" is `velocity_window_blocks = 7200`, not chain time

**Why NOTE, not BLOCKING:**
- Post-merge blocks are at most one per 12 s slot, so 7,200 blocks always span **at least** 24 h. The window can only include more registrations than the spec, never fewer, so no velocity alert is ever missed.
- The extra span is the missed-slot time, which I measured LIVE (Appendix C, `https://eth.drpc.org`):
  - 7,200 blocks ending at 26,247,123 span **24.057 h** (17 missed slots);
  - ending at 26,197,123: **24.090 h** (27 missed slots);
  - ending at 26,047,123: **24.220 h** (66 missed slots).
- So a false positive needs the registrations of the last 3–13 minutes beyond 24 h to tip the count over the headroom.
- That false positive is an **info** message (Discord). Its body states the measure honestly: "… registrations in the last 7200 blocks exceed its ETH headroom".
- The window is anchored at the run's checkpoint, not at the last hit (`test_m2_velocity_counts_only_the_window` passes). A reorg that removes the registrations resolves the condition, and re-inclusion reopens it (Appendix A, `test_m2_velocity_reorg_…`, passes).
- The worker reads no block timestamps anywhere: `grep -n timestamp reconcile/*.py reconcile/checks/*.py` finds nothing. A timestamp window would need new RPC calls.
- DEFECT-CLASSES 4 targets windows that under-cover, which delay or miss alerts. This one only over-covers.
- The coordinator may escalate it.

**Reproducer:**

```sh
reconcile/.venv/bin/python /tmp/cp2d-qa4/scripts/live_qa4.py https://eth.drpc.org
```

The window lines are the last 3 lines of output.

### Other NOTEs carried from attempt 3 (not re-measured beyond the suites named; no change in this repair)

- **N2:** coordinator ruling A, transient API-ahead-of-checkpoint warn. An accepted NOTE.
- **N4:** the ruling's "louder when ambiguous" stops at a block boundary.
  - My `test_m1_three_txs_two_clusters_…` also shows the same class inside one block. Cluster A is poisoned and then repaired by a later transaction of the same block, and stays silent: storage at block granularity cannot see it, and no trace RPC is available. Cluster B, poisoned in between, warns with its own repair tuple.
  - The spec validates at block granularity, and the block's last log is canonical, so the readers are unaffected.
- **N5:** the repair-tuple search returns `None` when both storage words are set; the alert is still sent, with "unknown".
- **N6:** an equivalent mutant in attempt 3's tooling.

## 2. Claims I could NOT confirm

- **"No real poisoned receipt exists on mainnet":** not re-established. I ran no history scan, the same as attempts 1–3.
- **DEF-3 fork PoC:** NOT RUN (deferred). The implementer does not claim one: repair 3 lists "NOT RUN: … DEF-3 fork PoC (deferred)".
- **Implementer falsifiers (`falsify_repair3.py` 4/4, `falsify_repair2.py` 41/41):** not re-run. The owner scope note says not to re-run the implementer's falsifier campaign or any mutation campaign. They are unconfirmed in this attempt; attempt 3 confirmed 41/41 on the code before repair 3.
- **"Considered, not reachable" (repair 3: revert-only → unknown; re-poisoned page → plain warn with the same poisoning):** checked by reasoning only, not by a test.
  - A related reachable path, page re-poison → plain poisoning after a reorg, sends one warn update (my `…page_then_reorg_leaves_only_the_first_poisoning…`).
- **Observation (reasoning, no reproducer, so not a finding):** `alerts.py` (frozen CP1B) keys a revised message `{incident}:{revision}` with `unique=True`. A meaning that returns to an earlier revision of the same incident (A → B → A) is therefore not re-sent.
  - The one path I found: a liquidator revert of a poisoned cluster is later orphaned. The revision goes `…|revert:T` → back to `poison:P|repair:R`.
  - That path only removes detail from a still-open poisoning, so nothing safety-relevant is hidden.
  - I found no path where the repeated revision hides a live poisoning.

Everything else in repair 3's verification table was reproduced (§3).

**Receipt evidence judgment (unchanged fixtures).**
- `git diff --exit-code 588888b HEAD -- reconcile/tests/fixtures/receipts` exits 0.
- I re-authenticated 6 of the 12 fixtures, the alternate files 1, 3, 5, 7, 9 and 11, from a third endpoint, `https://eth.drpc.org`. Block hash, block number, every log field of the transaction, the cluster id and the last-tuple fields are equal: **6/6 PASS, exit 0**.
- The checker can fail: one flipped log-data nibble → FAIL, and a last-tuple balance + 1 → FAIL.
- Attempts 2 and 3 authenticated all 12 from nodies.
- The evidence is adequate for decoding, for the validate/no-validate predicate and for repair-tuple selection. This is attempt 3's judgment, which I share.
- What a real poisoned receipt would add: production proof of the callback, event and storage ordering of the actual re-entry paths.
- My synthetic cases outside the implementer's model: two transient poisonings with only the last repair orphaned; a repair replaced by a new poisoning on the fork; unknown → receipt poisoning; three transactions on two clusters in one block. These are in addition to attempt 3's interleaved-cluster, reorder, decoy/earliest and re-poison cases, all re-run green.

**D9 (API tuples untrusted).** Unchanged by repair 3 (no inventory or M2 change). Attempt 3's measurements still pass: the QA3 forged-tuple test and QA2's partial/wrong-hash tests are green. Repair 3's G2 takes the repair tuple from the API only when `info.valid and info.tuple_source == "api"`, which means a storage-hash match (`m1_stale_tuple.py:484`).

**Known limit / ruling.** The intra-block re-poison pages under the coordinator's ruling. Re-measured: QA2's F2/F3 cases and QA3's U→C→P case pass. This is acceptable for spec M1 under the ruling.

## 3. Verification matrix

**Preflight:**
- HEAD `24b1599d2489193ef1fa59149b8e6e6b4bbe02bb` equals the dispatch head.
- Initial `git status --porcelain` was empty.
- 337 GiB free.
- `uv venv reconcile/.venv --python 3.12` and `uv pip install --python reconcile/.venv/bin/python -r reconcile/requirements-dev.txt` both exit 0; Python 3.12.15.
- anvil is present at `$HOME/.foundry/bin` but was not needed: no fork row is owned.
- No mainnet transaction. Alerts use record/Capture only. I did not touch config/, config.zip or .env*.

**Frozen-file proof:**
- `git diff --exit-code plan/monitoring-service HEAD -- reconcile/tests/harness.py reconcile/tests/test_framework.py reconcile/tests/test_worker.py` → 0. The same command against `500abe4` → 0, so the lane's edits to these files are exactly the bd531b4 + 500abe4 patch.
- `git diff --name-status 500abe4 HEAD -- reconcile` lists only `checks/__init__.py` (M), `config.example.json` (M) and the lane's added files. No other frozen CP1B file was edited.
- `git diff --name-status ec58434 HEAD` lists only `checks/m1_stale_tuple.py`, `tests/test_m1.py`, `tests/test_m1_m2_sweep.py` and plan/worker records.

| V-ID / measurement | Exact command | Exit / counts | Outcome |
|---|---|---|---|
| V2d.1 / V2x.1 M1 | `reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider reconcile/tests/test_m1.py` | 0; 82 passed | PASS |
| V2d.1 / V2x.1 M2 | `… reconcile/tests/test_m2.py` | 0; 47 passed | PASS (N1) |
| V2d.2 / V2x.2 | `… test_m1.py test_m2.py -k thresholds_from_chain` | 0; 2 passed, 127 deselected | PASS; plus mine: hard, early and velocity all follow a limit moved 3000→3100→2110 on the fake chain (falsified, §4) |
| V2d.3 / V2x.3 | `… test_m1.py test_m2.py test_m1_m2_sweep.py -k restart` | 0; 6 passed, 243 deselected | PASS; plus mine: no duplicate in 5 M1 states × re-runs + 2 restarts + sweeps; re-alert cadence after the F1 update |
| reorg | `… test_m1.py test_m2.py -k reorg` | 0; 38 passed, 91 deselected | PASS |
| repair 3 regressions | `… test_m1.py -k "orphaning_the_repair or found_after"` | 0; 11 passed | PASS |
| V2d.7 replay | `… reconcile/tests/test_m1_receipts.py` | 0; 24 passed (count + 12 faithful + 11 synthetic) | PASS |
| V2d.7 LIVE authenticity | `reconcile/.venv/bin/python /tmp/cp2d-qa4/scripts/live_qa4.py https://eth.drpc.org` (Appendix C) | 0; 6/6 PASS (2, 2, 2, 5, 22, 24 logs) | PASS; checker self-falsified (2 tampered copies → FAIL) |
| committed sweep | `… reconcile/tests/test_m1_m2_sweep.py` | 0; 120 passed | PASS |
| V2d.6 / V2x.6 suite | `… -m pytest -q -p no:cacheprovider reconcile/tests` | 0; 513 passed, 0 skips | PASS |
| V2d.6 lint | `reconcile/.venv/bin/ruff check reconcile` | 0; All checks passed! | PASS |
| regressions vs `ec58434` | scratch `/tmp/cp2d-qa4/pre-repair3` (rsync of `reconcile/` minus .venv, `checks/m1_stale_tuple.py` from `git show ec58434:…`), then `… reconcile/tests/test_m1.py reconcile/tests/test_m1_m2_sweep.py` | 1; 24 failed / 178 passed (10 targeted + 14 walks) | PASS (claim reproduced) |
| attempt 3 reproducers | extracted unmodified → `/tmp/cp2d-qa4/prior/qa3_test_attack3.py` | 1; 14 passed, 1 failed (N1) | PASS for F1 (4/4 green) |
| attempt 2 reproducers | → `/tmp/cp2d-qa4/prior/qa2_test_adversarial.py` | 0; 24 passed | PASS |
| attempt 1 reproducers | → `/tmp/cp2d-qa4/prior/qa1_test_independent.py` | 0; 29 passed | PASS |
| QA4 independent suite | `… -m pytest -q -p no:cacheprovider /tmp/cp2d-qa4/test_attack4.py` (Appendix A) | 0; 14 passed (on `ec58434`: 4 failed / 10 passed) | PASS |
| QA4 falsifiers | `reconcile/.venv/bin/python /tmp/cp2d-qa4/scripts/falsify_qa4.py` (Appendix B) | 0; 4/4 red → restored green | PASS |
| V2x.5 implementer falsifiers | not re-run (owner scope note) | — | SKIPPED (see §2) |
| V2x.4 | not owned; part of the suite run | `rpc_budget` passes inside the 513 | — |

**Skipped (budget/owner scope):**
- the implementer's falsifier campaigns (`falsify_repair2.py`, `falsify_repair3.py`);
- any mutation campaign;
- receipt files 2, 4, 6, 8, 10 and 12, which are byte-identical to `588888b` and were authenticated by attempts 2 and 3;
- attempt 1's Views, storage and pre-call LIVE measurements (inputs unchanged);
- the history scan;
- the fork PoC (DEF-3).

The four QA4 mutants are a sensitivity check of my own new tests only, as DEFECT-CLASSES 12 asks (2 s in total), not a campaign.

### Spec-derived triggers (my derivation from spec M1/M2) → trigger test at severity / benign twin (all present on `24b1599` and green in the runs above)

| Spec line | Trigger (severity) | Benign twin (silent) |
|---|---|---|
| M1 1–3: group ≥2 per tx and cluster, last tuple does not validate → alert + repair tuple | `poisoned_receipt_warns_with_repair_tuple` (warn); `repair_tuple_tie_is_the_earliest_log_for_every_event_kind` (8 kinds); QA3 decoy/earliest; QA4 three-tx two-cluster | `multi_event_receipt_that_validates_is_silent`, `single_event_transactions_are_not_validated`; QA3 reordered canonical-last |
| M1 4: our `updateClusterBalance`/`liquidate` IncorrectClusterState revert | `inbox_incorrect_cluster_state_from_our_sender_warns`, `inbox_liquidator_revert_on_poisoned_cluster_feeds_the_same_incident` (warn) | `inbox_revert_benign_twins_are_silent[stranger,other_error,other_function]` |
| M1 5: re-poison after repair → page | `re_poison_after_repair_pages`, `same_block_repair_then_re_poison_pages`; QA4 two-transient reorg (page update) | `second_poisoning_without_repair_is_not_a_re_poison` (warn) |
| M1 severity: poisoned and liquidatable → page | `poisoned_and_liquidatable_pages`, `thresholds_from_chain_liquidation_period_change_escalates_to_page` | non-liquidatable poisoning stays warn (the first row's test) |
| M1 plan: inventory hash mismatch (`unknown`) feeds the incident | `inventory_hash_mismatch_is_unknown_warn…[False]`; QA4 unknown → receipt poisoning (update) | `…[True]`; QA3 forged API tuple vs valid last log |
| M1 events between sweeps (class 5) | `poison_repaired_before_the_run_still_alerts`; F1 regressions; QA4 transient variants | — |
| M2 hard → warn | `hard_blocked_active_legacy_cluster_warns`; QA4 limit 2,110 | `hard_boundary_cluster_that_exactly_fits_is_silent`, `hard_ignores_liquidated_legacy_clusters` |
| M2 early < 900 → info, warn if public | `early_headroom_below_900_is_info_and_warn_if_public`; QA4 public warn follows the limit | `early_headroom_exactly_900_is_silent` |
| M2 velocity > headroom → info | `velocity_registrations_above_headroom_is_info`; QA4 limit and reorg | `velocity_registrations_equal_to_headroom_are_silent`, window ageing (N3) |
| M2 `migrateClusterToETH`/`reactivate` ExceedValidatorLimitWithData → warn | `inbox_exceed_validator_limit_revert_warns_then_settles`; QA1 `each_inbox_function_warns` (both) | `inbox_revert_benign_twins_are_silent[other_error,other_function]` |

### DEFECT-CLASSES attack on every persisted key (classes 1–6, 10); new in attempt 4 marked QA4

| Persisted key / input | Attacks run | Result |
|---|---|---|
| M1 `incidents.revision` (repair 3's new meaning string) | QA4: stability across re-runs, 2 restarts and interval sweeps in 5 states (poison, re-poison, unknown, poison + revert, after the F1 update); re-alert cadence 23 h silent / 25 h one realert after a restart; meaning changes: two transient poisonings with the last repair orphaned (depth 1 and a **shorter** replacement chain, page update, Telegram route), repair replaced by a new poisoning, page re-poison → plain poisoning (warn update), unknown → receipt poisoning | pass |
| M1 open transient incident | QA3 F1 ×4 (depth 1, beyond confirmations, restart, page) + QA4 variants above | pass |
| M1 `m1_detections` poison/repair/revert, `cleared_block` | QA1/QA2/QA3 suites (same block with 2/3 transactions, interleaved clusters, orphaned settlement, re-inclusion); QA4 three transactions on two clusters | pass (N4 noted) |
| M1 incidents incl. resolved, `meta reorg:m1`, `first_run_block` | QA2 resolved-key reorg with partial API (normal/restart); committed downtime and first-sweep tests | pass |
| M1 inputs: SSV API empty / partial / wrong-hash / error; `0x` views answer | QA2/QA3 suites; committed `failed_or_empty_validation_never_resolves` | pass |
| M2 `m2_clusters`, `m2_reverts` + `cleared_block`, incidents hard/early/velocity/revert incl. resolved, `meta reorg:m2` | QA1–QA3 suites; QA4: all three rules follow the live limit, restart silent; velocity reorg removes and re-includes the registrations | pass |
| M2 input: API partial on first run | QA3 | **N1** |
| M2 time window (class 4) | QA4 LIVE span of 7,200 blocks | **N3** |

## 4. Falsifiers

**Own (Appendix B).** Each mutant runs on the scratch copy `/tmp/cp2d-qa4/qa4-mutant`. The driver asserts that the bytes were restored and equal to the worktree file before the green run. Log: `/tmp/cp2d-qa4/falsify_qa4.out`.

| Mutation | Tests | Red → green |
|---|---|---|
| QA4-M1-no-revision: `revision="\|".join(parts) or None` → `revision=None` | two-transient ×2, replaced-repair, unknown→poison, page→warn | exit 1 (5 failed) → exit 0 |
| QA4-M1-unstable-revision: append the run's block to the revision | revision stability ×5, re-alert cadence | exit 1 (6 failed) → exit 0 |
| QA4-M1-pre-repair3-scheme: stale and transient back to `poison:{tx}:{log}`, no repair part | two-transient ×2 | exit 1 (2 failed) → exit 0 |
| QA4-M2-hardcoded-limit-headroom: `headroom = limit - …` → `3000 - …` | all three rules follow the limit | exit 1 (1 failed) → exit 0 |

- `git status --porcelain` in the worktree was empty after the driver.
- The implementer's committed falsifiers were not re-run (§2, owner scope).

## 5. Evidence-plane writes

- The CP2d `**Status:**` line changed from `IMPLEMENTED — AWAITING QA` to **VERIFIED** (PASS WITH NOTES) at frozen head `24b1599d2489193ef1fa59149b8e6e6b4bbe02bb`, linked to this report. NOTEs N1–N6 are recorded here; §8 "Recorded notes" is outside my write scope, so the coordinator carries them forward.
- One event is appended to `plans/monitoring-service/execution-log.md`, lane-local and numbered by the coordinator at merge (the integration branch's convention; the lane log ends at 0038 while the integration log is at 0066).
- This report: `plans/monitoring-service/qa/cp2d-attempt-4.md`.
- `reconcile/.venv` is local and ignored.
- Scratch is all under `/tmp/cp2d-qa4`: tests, copies, RPC scripts and logs.

## 6. Repo proof

`git status --porcelain` was empty after every run and mutation. The proof taken before the evidence commit is reproduced below the appendices.

## Appendix A — QA4 independent scenarios (durable reproducer)

Extract by marker, then run from the worktree root. It needs `reconcile/tests` on the path, which the file adds itself.

```sh
reconcile/.venv/bin/python - <<'EXTRACT'
from pathlib import Path
import re
s = Path('plans/monitoring-service/qa/cp2d-attempt-4.md').read_text()
for name, sub in (('test_attack4.py', ''), ('falsify_qa4.py', 'scripts/'), ('live_qa4.py', 'scripts/')):
    code = re.search(r'<!-- ' + re.escape(name) + r' -->\s*```python\n(.*?)\n```', s, re.S).group(1)
    p = Path('/tmp/cp2d-qa4') / sub / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(code + '\n')
EXTRACT
PYTHONDONTWRITEBYTECODE=1 reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider -s /tmp/cp2d-qa4/test_attack4.py
```

Measured on `24b1599`: exit 0, **14 passed**. On `ec58434` (scratch): 4 failed / 10 passed.

<!-- test_attack4.py -->
```python
"""QA attempt 4 (CP2d) independent adversarial scenarios, outside the implementer's state table and QA 1-3.
Expectations come from spec M1/M2, checks/README.md 6.2, qa/DEFECT-CLASSES.md and the coordinator rulings.
Focus: the repair-3 revision ("the revision names the meaning") must send every change of meaning once, and must
never duplicate a message across re-runs, restarts and re-alert windows; same-block ordering across clusters;
M2 rules following the live limit; M2 velocity under reorg."""
import json
import pathlib
import sys

ROOT = pathlib.Path.cwd()
sys.path[:0] = [str(ROOT / 'reconcile'), str(ROOT / 'reconcile/tests')]
import pytest  # noqa: E402
from fakechain import MiniSSV, keys  # noqa: E402
from fixtures_cluster import (  # noqa: E402
    INCORRECT_STATE, LIQUIDATOR, OPS, OPS2, OWNER, OWNER2, ClusterChain, current_tuple, deposit, failed_tx, key_of,
    make_m1, poison,
)
from harness import VIEWS, advance, api_row, make_worker, outbox  # noqa: E402
from test_m1 import (  # noqa: E402
    api_only_key, evidence, incidents, inbox_m1, kinds, m1_config, out, transient_then_orphaned_repair,
)
from test_m2 import ETH_OPS, ETH_OWNER, alerts, open_keys, setup  # noqa: E402
from test_m2 import tick as tick2  # noqa: E402
from checks.m1_stale_tuple import _tup  # noqa: E402
from eb_storage import u256  # noqa: E402
from harness import stub_params  # noqa: E402

KEY = key_of(OWNER, OPS)
KEY2 = key_of(OWNER2, OPS2)


def msgs(w):
    """M1 incident messages (health excluded)."""
    return [r for r in outbox(w, "check_id='m1' AND kind != 'health'")]


def restart_m1(tmp_path, chain, w, **over):
    _, w2 = make_worker(tmp_path, only=["m1"], chain=chain, views=VIEWS, clock=w.clock, checks={"m1": m1_config()},
                        start_block=w.cfg.start_block, activation_block=w.cfg.activation_block, **over)
    return w2


def churn(tmp_path, chain, w, rebuild):
    """Re-runs without a chain change of the cluster: same block, new blocks, a restart, the interval sweep."""
    advance(w)
    chain.mine(); advance(w)
    w = rebuild(w)
    advance(w)
    w.clock.advance(601); chain.mine(); advance(w)
    w = rebuild(w)
    w.clock.advance(601); chain.mine(); advance(w)
    return w


# ------------------------------------------------------------------ M1: revision idempotency in every state (class 10)

def _state_poison(tmp_path):
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18); advance(w)
    poison(ssv); advance(w)
    assert kinds(w) == [('warn', 'open')]
    return chain, w, (lambda w_: restart_m1(tmp_path, chain, w_))


def _state_re_poison(tmp_path):
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18); advance(w)
    poison(ssv); advance(w)
    deposit(ssv); advance(w)
    poison(ssv); advance(w)
    assert kinds(w)[-1] == ('page', 'open')
    return chain, w, (lambda w_: restart_m1(tmp_path, chain, w_))


def _state_unknown(tmp_path):
    chain, w, ssv, rows, fork, cl = api_only_key(tmp_path, api_valid=False)
    w.clock.advance(601); chain.mine(); advance(w)
    assert [(i['severity'], i['title'][:22]) for i in incidents(w, 'open')] == [('warn', 'Cluster tuple unknown ')]
    inv = {"api_url": "https://api.invalid", "api_refresh_s": 0}

    def rebuild(w_):
        w2 = restart_m1(tmp_path, chain, w_, inventory=inv)
        w2.inventory.http_get = lambda url: {"clusters": list(rows), "pagination": {"pages": 1}}
        return w2
    return chain, w, rebuild


def _state_poison_with_revert(tmp_path):
    chain, w, ssv, path = inbox_m1(tmp_path)
    deposit(ssv, value=10**18); advance(w)
    _, stale, _ = poison(ssv); advance(w)
    failed_tx(chain, path, "liquidate", OWNER, OPS, stale, LIQUIDATOR, INCORRECT_STATE)
    advance(w); advance(w)
    assert kinds(w) == [('warn', 'open'), ('warn', 'update')]
    return chain, w, (lambda w_: restart_m1(tmp_path, chain, w_, inbox={"path": str(path)}))


def _state_after_f1_update(tmp_path):
    chain, w, ssv, n, _, sev = transient_then_orphaned_repair(tmp_path)
    assert len(out(w)) == n + 1 and out(w)[-1]['kind'] == 'update'
    return chain, w, (lambda w_: restart_m1(tmp_path, chain, w_))


@pytest.mark.parametrize('state', ['poison', 're_poison', 'unknown', 'poison_with_revert', 'after_f1_update'])
def test_m1_revision_is_stable_no_duplicate_on_reruns_and_restarts(tmp_path, state):
    chain, w, rebuild = globals()[f'_state_{state}'](tmp_path)
    before = [(r['severity'], r['kind'], r['key']) for r in msgs(w)]
    rev = [i['revision'] for i in incidents(w, 'open')]
    w = churn(tmp_path, chain, w, rebuild)
    after = [(r['severity'], r['kind'], r['key']) for r in msgs(w)]
    print(state, rev, after[len(before):])
    assert after == before, f'{state}: duplicate message(s) {after[len(before):]}'
    assert [i['revision'] for i in incidents(w, 'open')] == rev


def test_m1_f1_update_then_realert_cadence_is_kept_across_restart(tmp_path):
    """After the F1 update (warn), the re-alert follows the 24 h warn cadence from the update, once."""
    chain, w, ssv, n, _, sev = transient_then_orphaned_repair(tmp_path)
    w = restart_m1(tmp_path, chain, w)
    w.clock.advance(23 * 3600); chain.mine(); advance(w)
    assert len(out(w)) == n + 1, 'no re-alert before 24 h'
    w.clock.advance(2 * 3600); chain.mine(); advance(w); advance(w)
    tail = [(r['severity'], r['kind']) for r in out(w)[n + 1:]]
    assert tail == [('warn', 'realert')], tail


# ------------------------------------------------------------------ M1: transient meaning changes not in the regressions

@pytest.mark.parametrize('shorter', [False, True])
def test_m1_two_transient_poisonings_reorg_orphans_only_the_last_repair_pages_the_re_poison(tmp_path, shorter):
    """Run 1 sees P1, R1, P2, R2 (transient, re-poisoned: page). A reorg removes R2 only: P2 survives unrepaired after
    the recorded repair R1, a live re-poison. Expected: a page update naming P2, the repair tuple and the runbook."""
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18); advance(w)
    poison(ssv)
    deposit(ssv)
    tx2, stale2, rep2 = poison(ssv)
    after_p2 = dict(ssv._get(OWNER, OPS)[1])
    deposit(ssv)
    b4 = chain.sealed
    if shorter:
        chain.mine(5)  # the replacement chain below is shorter than the one the run saw
    advance(w)
    assert kinds(w) == [('page', 'open')] and evidence(w)['transient'] is True
    n = len(out(w))
    seen = w.checkpoint()['block']
    chain.reorg(b4); ssv._get(OWNER, OPS)[1].update(after_p2); chain.mine(1 if shorter else 2); advance(w)
    if shorter:
        assert chain.sealed < seen, (chain.sealed, seen)
    print('TWO-TRANSIENT', kinds(w), evidence(w).get('poison_tx') == tx2)
    assert len(out(w)) == n + 1 and (out(w)[-1]['severity'], out(w)[-1]['kind']) == ('page', 'update')
    assert 're-poisoned after a repair' in out(w)[-1]['text'] and 'repaired before this run' not in out(w)[-1]['text']
    assert out(w)[-1]['delivery'] == 'telegram', out(w)[-1]['delivery']
    ev = evidence(w)
    assert ev['poison_tx'] == tx2 and ev['stale_tuple'] == _tup(stale2) and ev['repair_tuple'] == _tup(rep2)


def test_m1_transient_then_reorg_replaces_the_repair_with_a_new_poisoning(tmp_path):
    """The reorg replaces the repair block by a block carrying a different poisoning P' (no repair in between):
    Expected: the live state is sent (P', its repair tuple), not 'repaired before this run'."""
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18); advance(w)
    poison(ssv)
    after = dict(ssv._get(OWNER, OPS)[1])
    deposit(ssv); b2 = chain.sealed
    advance(w)
    assert evidence(w)['transient'] is True
    n = len(out(w))
    chain.reorg(b2); ssv._get(OWNER, OPS)[1].update(after)
    txp, stalep, repp = poison(ssv)
    chain.mine(); advance(w)
    print('REPLACED', kinds(w), evidence(w).get('poison_tx') == txp)
    assert len(out(w)) == n + 1 and out(w)[-1]['severity'] in ('warn', 'page')
    assert 'repaired before this run' not in out(w)[-1]['text'] and txp in out(w)[-1]['text']
    assert evidence(w)['repair_tuple'] == _tup(repp)


def test_m1_unknown_incident_becomes_a_receipt_poisoning_is_sent(tmp_path):
    """An API-only 'tuple unknown' warn; then a poisoning receipt of that cluster arrives: the meaning changes from
    'unknown' to 'stale tuple + repair tuple' and must be sent (same incident, update)."""
    chain, w, ssv, rows, fork, cl = api_only_key(tmp_path, api_valid=False)
    w.clock.advance(601); chain.mine(); advance(w)
    assert incidents(w, 'open') and 'unknown' in incidents(w, 'open')[0]['title']
    n = len(msgs(w))
    tx, stale, rep = poison(ssv, owner=OWNER2, ops=OPS2)
    advance(w)
    print('UNKNOWN->POISON', [(r['severity'], r['kind']) for r in msgs(w)])
    assert len(msgs(w)) == n + 1 and tx in msgs(w)[-1]['text']
    assert evidence(w, KEY2)['repair_tuple'] == _tup(rep)


# ------------------------------------------------------------------ M1: same-block ordering across clusters (class 3)

def test_m1_three_txs_two_clusters_poison_a_poison_b_repair_a_in_one_block(tmp_path):
    """tx1 poisons A, tx2 poisons B, tx3 is a zero deposit of A, all in one block. B's last tx is a poisoning: warn with
    B's repair tuple. A's poisoning was repaired inside the block (no trace: not observable; QA3 N4 class)."""
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18); deposit(ssv, owner=OWNER2, ops=OPS2, value=10**18); advance(w)
    poison(ssv, mine=False)
    txb, staleb, repb = poison(ssv, owner=OWNER2, ops=OPS2, mine=False)
    deposit(ssv, mine=True)
    advance(w)
    print('A-B-A', [(i['key'] == KEY, i['severity']) for i in incidents(w, 'open')])
    assert KEY2 in [i['key'] for i in incidents(w, 'open')]
    ev = evidence(w, KEY2)
    assert ev['poison_tx'] == txb and ev['repair_tuple'] == _tup(repb) and ev['stale_tuple'] == _tup(staleb)


# ------------------------------------------------------------------ M2: every rule follows the live limit (V2x.2)

def _limit(chain, value):
    stub_params(chain, validators_per_operator_limit=value)
    chain.emit_event("ModuleUpgraded", ["0x" + u256(1).hex()], u256(value))
    chain.mine()


def test_m2_hard_early_velocity_all_follow_the_limit_on_chain_then_restart_is_silent(tmp_path):
    """Public operator 1: eth 2,100, legacy 20, limit 3,000: headroom 900, gap 880 < 900 (early warn, public); 901
    registrations > 900 (velocity info). Limit 3,100: gap 980, headroom 1,000: both clear. Limit 2,110: 2,100 + 20 >
    2,110 (hard warn), gap -10 (early warn), 901 > 10 (velocity info). Then restart: nothing re-sent."""
    chain, w, ssv, rows = setup(tmp_path, eth=2_100, private=False, m2={"velocity_window_blocks": 20})
    advance(w)
    ssv.register(ETH_OWNER, ETH_OPS, keys(901, 0))
    tick2(w, chain)
    assert open_keys(w) == ['early:1', 'velocity:1'], open_keys(w)
    _limit(chain, 3_100); advance(w)
    assert open_keys(w) == [], open_keys(w)
    _limit(chain, 2_110); advance(w)
    assert open_keys(w) == ['early:1', 'hard:1', 'velocity:1'], open_keys(w)
    seq = [a for a in alerts(w) if a[2].endswith(':1')]
    print('LIMIT', seq)
    assert [a for a in seq if a[2] == 'early:1'] == [('warn', 'open', 'early:1'), ('info', 'resolved', 'early:1'),
                                                     ('warn', 'open', 'early:1')]
    assert [a for a in seq if a[2] == 'hard:1'] == [('warn', 'open', 'hard:1')]
    n = len(alerts(w))
    from fixtures_cluster import make_m2  # noqa: F401
    _, w2 = make_worker(tmp_path, only=["m2"], chain=chain, views=VIEWS, clock=w.clock,
                        checks={"m2": {"velocity_window_blocks": 20}},
                        inventory={"api_url": "https://api.invalid", "api_refresh_s": 0},
                        start_block=w.cfg.start_block, activation_block=w.cfg.activation_block)
    w2.inventory.http_get = lambda url: {"clusters": list(rows), "pagination": {"pages": 1}}
    advance(w2); tick2(w2, chain)
    assert len(alerts(w2)) == n, alerts(w2)[n:]


def test_m2_velocity_reorg_removing_the_registrations_resolves_and_reinclusion_reopens(tmp_path):
    chain, w, ssv, rows = setup(tmp_path, eth=2_800, private=True, m2={"early_headroom": 1})  # headroom 200
    advance(w)
    rb = chain.number
    ssv.register(ETH_OWNER, ETH_OPS, keys(201, 0))
    tick2(w, chain)
    assert open_keys(w) == ['velocity:1']
    chain.reorg(rb); chain.mine(2); advance(w)
    print('VEL-REORG', open_keys(w))
    assert open_keys(w) == [], 'registrations left the canonical chain'
    ssv.register(ETH_OWNER, ETH_OPS, keys(201, 900))
    tick2(w, chain)
    assert open_keys(w) == ['velocity:1']


def test_m1_transient_re_poison_page_then_reorg_leaves_only_the_first_poisoning_sends_a_warn_update(tmp_path):
    """Run 1: P1, R1, P2, R2 (transient re-poison, page). A reorg from R1's block removes R1, P2 and R2: P1 survives
    unrepaired (a plain poisoning). The meaning changes (and severity drops to warn): one update naming P1 is sent."""
    chain, w, ssv = make_m1(tmp_path)
    deposit(ssv, value=10**18); advance(w)
    tx1, stale1, rep1 = poison(ssv)
    after_p1 = dict(ssv._get(OWNER, OPS)[1])
    deposit(ssv); b2 = chain.sealed
    poison(ssv)
    deposit(ssv)
    advance(w)
    assert kinds(w) == [('page', 'open')] and evidence(w)['transient'] is True
    n = len(out(w))
    chain.reorg(b2); ssv._get(OWNER, OPS)[1].update(after_p1); chain.mine(2); advance(w)
    print('PAGE->P1', kinds(w))
    assert len(out(w)) == n + 1 and out(w)[-1]['kind'] == 'update' and tx1 in out(w)[-1]['text']
    assert out(w)[-1]['delivery'] == 'telegram'
    assert evidence(w)['poison_tx'] == tx1 and evidence(w)['repair_tuple'] == _tup(rep1)
    assert 'repaired before this run' not in out(w)[-1]['text']
```

## Appendix B — QA4 mutation driver (scratch copy only)

<!-- falsify_qa4.py -->
```python
"""QA4 mutation driver: scratch copy only; each mutant must redden the named QA4 test(s), restore, then green."""
import json, os, pathlib, shutil, subprocess, sys
ROOT = pathlib.Path.cwd(); dst = pathlib.Path('/tmp/cp2d-qa4/qa4-mutant')
shutil.rmtree(dst, ignore_errors=True)
shutil.copytree(ROOT / 'reconcile', dst / 'reconcile', ignore=shutil.ignore_patterns('.venv', '__pycache__', '.pytest_cache'))
shutil.copy('/tmp/cp2d-qa4/test_attack4.py', dst / 'test_attack4.py')
python = str(ROOT / 'reconcile/.venv/bin/python'); env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}
M1 = 'checks/m1_stale_tuple.py'; M2 = 'checks/m2_operator_capacity.py'
cases = [
    ('QA4-M1-no-revision', M1, [('revision="|".join(parts) or None)', 'revision=None)')],
     'two_transient or replaces_the_repair or unknown_incident_becomes or page_then_reorg'),
    ('QA4-M1-unstable-revision', M1, [('revision="|".join(parts) or None)', 'revision="|".join(parts + [str(block)]) or None)')],
     'revision_is_stable or realert_cadence'),
    ('QA4-M1-pre-repair3-scheme', M1,
     [('parts.append(f"{state}:{poison[\'tx_hash\']}:{poison[\'log_index\']}")', 'parts.append(f"poison:{poison[\'tx_hash\']}:{poison[\'log_index\']}")'),
      ('parts.append("repair:" + (keccak(poison["repair_tuple"].encode()).hex()[:12] if canonical else "unknown"))', 'pass'),
      ("parts.append(f\"{'transient-re-poison' if re_poisoned else 'transient'}:{last['tx_hash']}:\"", "parts.append(f\"poison:{last['tx_hash']}:\"")],
     'two_transient'),
    ('QA4-M2-hardcoded-limit-headroom', M2, [('headroom = limit - e["validator_count"]', 'headroom = 3000 - e["validator_count"]')],
     'all_follow_the_limit'),
]
rows = []
for name, path, subs, expr in cases:
    p = dst / 'reconcile' / path; orig = p.read_bytes(); text = orig.decode()
    for old, new in subs:
        assert text.count(old) == 1, (name, old); text = text.replace(old, new)
    cmd = [python, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', 'test_attack4.py', '-k', expr]
    try:
        p.write_text(text); red = subprocess.run(cmd, cwd=dst, env=env, text=True, capture_output=True)
    finally:
        p.write_bytes(orig)
    assert orig == p.read_bytes() == (ROOT / 'reconcile' / path).read_bytes()
    green = subprocess.run(cmd, cwd=dst, env=env, text=True, capture_output=True)
    r = dict(name=name, test=expr, red=red.returncode, red_summary=red.stdout.strip().splitlines()[-1],
             green=green.returncode, green_summary=green.stdout.strip().splitlines()[-1])
    print(json.dumps(r)); rows.append(r)
sys.exit(0 if all(r['red'] == 1 and r['green'] == 0 for r in rows) else 1)
```

## Appendix C — LIVE receipt re-authentication and velocity-window span

<!-- live_qa4.py -->
```python
"""QA4 LIVE: (1) re-authenticate 6 of the 12 receipt fixtures (alternate files) from an archive RPC by tx hash;
(2) class-4 measurement: wall time spanned by M2's 7,200-block velocity window on mainnet now."""
import concurrent.futures, json, pathlib, sys, urllib.request
ROOT = pathlib.Path.cwd(); sys.path[:0] = [str(ROOT / 'reconcile'), str(ROOT / 'reconcile/tests')]
from eb_events import decode_log
from eb_storage import cluster_id
EP = sys.argv[1] if len(sys.argv) > 1 else 'https://eth.drpc.org'

def rpc(method, params):
    req = urllib.request.Request(EP, json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}).encode(),
                                 {'Content-Type': 'application/json', 'User-Agent': 'ssv-cp2d-qa4/1.0'})
    with urllib.request.urlopen(req, timeout=30) as r:
        body = json.load(r)
    if 'result' not in body or body['result'] is None:
        raise AssertionError(f'{method}: {body}')
    return body['result']

def check(path):
    d = json.loads(path.read_text()); tx = d['tx_hash']
    try:
        rec = rpc('eth_getTransactionReceipt', [tx])
        assert rec['blockHash'].lower() == d['block_hash'].lower(), 'blockHash'
        assert int(rec['blockNumber'], 16) == d['block'], 'blockNumber'
        actual = {int(l['logIndex'], 16): l for l in rec['logs']}
        expected = [l for l in d['logs'] if l['transactionHash'].lower() == tx.lower()]
        assert expected, 'no expected logs'
        for log in expected:
            a = actual[int(log['logIndex'], 16)]
            for k in ('address', 'topics', 'data', 'blockHash', 'blockNumber', 'transactionHash', 'logIndex'):
                norm = lambda v: [x.lower() for x in v] if isinstance(v, list) else v.lower()
                assert norm(a[k]) == norm(log[k]), (k, a[k], log[k])
        for g in d['groups']:
            evs = sorted([decode_log(actual[i]) for i in g['log_indexes']], key=lambda e: e.log_index)
            ev = evs[-1]; t = ev.cluster
            assert '0x' + cluster_id(ev.owner, ev.operator_ids).hex() == g['cluster_id'], 'cluster_id'
            assert [t.validator_count, t.network_fee_index, t.index, t.active, str(t.balance)] == g['last_tuple'], 'last_tuple'
        return dict(file=path.name, tx=tx, endpoint=EP, result='PASS', logs=len(expected), groups=len(d['groups']))
    except Exception as e:
        return dict(file=path.name, tx=tx, endpoint=EP, result='NOT RUN' if isinstance(e, OSError) else 'FAIL', error=repr(e))

paths = sorted(pathlib.Path('reconcile/tests/fixtures/receipts').glob('*.json'))[::2]
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as p:
    rows = list(p.map(check, paths))
for r in rows:
    print(json.dumps(r), flush=True)
# class 4: time spanned by 7,200 blocks
head = int(rpc('eth_blockNumber', []), 16)
spans = []
for end in (head, head - 50_000, head - 200_000):
    t1 = int(rpc('eth_getBlockByNumber', [hex(end), False])['timestamp'], 16)
    t0 = int(rpc('eth_getBlockByNumber', [hex(end - 7200), False])['timestamp'], 16)
    spans.append(dict(end=end, seconds=t1 - t0, hours=round((t1 - t0) / 3600, 3), missed_slots=(t1 - t0) // 12 - 7200))
for s in spans:
    print(json.dumps({'velocity_window_span': s, 'endpoint': EP}), flush=True)
pathlib.Path('/tmp/cp2d-qa4/live.json').write_text(json.dumps({'auth': rows, 'spans': spans}, indent=2))
sys.exit(0 if all(x['result'] == 'PASS' for x in rows) else 1)
```

Output (`https://eth.drpc.org`, exit 0):

```text
{"file": "24920792-26136c50.json", "tx": "0x26136c505e25df23dd6580119802e9d38dc1a548a677e9b7803d46177c6f93bd", "endpoint": "https://eth.drpc.org", "result": "PASS", "logs": 2, "groups": 1}
{"file": "24926844-77ea23fc.json", "tx": "0x77ea23fcacd9f3f47db52e37db0eddbf7e10690a95083be9decff2238ea42aaf", "endpoint": "https://eth.drpc.org", "result": "PASS", "logs": 2, "groups": 1}
{"file": "25298236-9b2cb18d.json", "tx": "0x9b2cb18dd15221d9b7ece2b4e3742faa9fe81a2c5d5ea7dada0304f96ba73e97", "endpoint": "https://eth.drpc.org", "result": "PASS", "logs": 2, "groups": 1}
{"file": "26232215-4fc63d09.json", "tx": "0x4fc63d094786e3885d7f03d0b1cdbe13f46ec1b7e7335b0e7ad76b82785cb875", "endpoint": "https://eth.drpc.org", "result": "PASS", "logs": 5, "groups": 1}
{"file": "26233260-38d732e6.json", "tx": "0x38d732e6024171258bcd3aca4b0834673fda71b8d3815b881007a3390c5a8ac0", "endpoint": "https://eth.drpc.org", "result": "PASS", "logs": 22, "groups": 1}
{"file": "26233389-ff3292f8.json", "tx": "0xff3292f86ff0e59d67d1229ee8df0364843edf56bce03d908c3cde5018b6d1d4", "endpoint": "https://eth.drpc.org", "result": "PASS", "logs": 24, "groups": 1}
{"velocity_window_span": {"end": 26247123, "seconds": 86604, "hours": 24.057, "missed_slots": 17}, "endpoint": "https://eth.drpc.org"}
{"velocity_window_span": {"end": 26197123, "seconds": 86724, "hours": 24.09, "missed_slots": 27}, "endpoint": "https://eth.drpc.org"}
{"velocity_window_span": {"end": 26047123, "seconds": 87192, "hours": 24.22, "missed_slots": 66}, "endpoint": "https://eth.drpc.org"}
```

## Repo proof (taken before the evidence commit)

`git status --porcelain` (the report is written in the same step):

```text
 M plans/monitoring-service/execution-log.md
 M plans/monitoring-service/plan.md
?? plans/monitoring-service/qa/cp2d-attempt-4.md
```

`git diff --name-only 24b1599d2489193ef1fa59149b8e6e6b4bbe02bb` (tracked changes; the report is the one untracked file above):

```text
plans/monitoring-service/execution-log.md
plans/monitoring-service/plan.md
```

No implementation, test, config or fixture file appears in either list. After the commit, `git diff --name-only 24b1599d2489193ef1fa59149b8e6e6b4bbe02bb HEAD` lists exactly `plans/monitoring-service/execution-log.md`, `plans/monitoring-service/plan.md`, `plans/monitoring-service/qa/cp2d-attempt-4.md`.
