FAIL

Independent QA of CP2b (M9, M10, M11), attempt 3 (after repair 2). Frozen head under test: `6191a3677c34949e4facce3e1206d78158022084` (lane branch `mtabasco/ms-cp2b`, code head `3fc0c7b`); QA worktree `ms-qa-cp2b-3`, branch `mtabasco/ms-qa-cp2b-3`. Task `task_c06575dde1da`, dispatch `ctx_e51adc5a9a75`. QA model: Claude Opus 5.5, effort high (D13). Measured 2026-10-22, 07:25–07:45 UTC. **Capped round** (owner round policy: third QA of the lane): each finding is BLOCKING or NOTE; any BLOCKING ⇒ FAIL. No implementation repair; scratch under `/tmp/qa-cp2b-3`.

Summary. I re-measured every supplied row and got the implementer's counts exactly: M9 68, M10 23, M11 72, sweep 44, suite 447, ruff clean. The 15 new tests are RED on the frozen code `28981fc` (aggregate 43 failed / 164 passed). All 18 repair-2 falsifiers turn red and restore. Attempt 2's reproducers now run 15 passed / 1 failed, and the one failure is the NOTE N1. QA2-F1..F4 are fixed. My own spec-derived scenarios (11 cases) still find **two BLOCKING defects**. Both were already in the code before repair 2; they are not regressions:
- **QA3-F1:** after an M11 updater outage of two or more rounds is resolved, M11 sends one extra page/warn plus a resolution every round while the outage slides out of the `history_rounds` window. That is 4 extra alerts (3 of them pages) after a 7-round outage.
- **QA3-F2:** a round in which the dissenting slot is the only voter counts as "positive proof" that the slot agrees. A 3-in-a-row dissent page is resolved even though the slot never voted the quorum's root.

I re-measured LIVE myself. M9/M10 stay silent on a fresh archive read of the last 5 mainnet rounds. M11 finds 3 mainnet leaf mismatches, and all 3 come from validators added or removed after the root (benign, correctly not warned).

## 1. Findings (ranked)

Reproducer file: `/tmp/qa-cp2b-3/tests/test_qa3.py`, embedded verbatim in Appendix A. To extract it, use the same `split` on the BEGIN/END markers as `workers/cp2b/evidence/verify.sh` does for attempts 1 and 2. `Q` = `PYTHONPATH=reconcile:reconcile/tests reconcile/.venv/bin/python -X pycache_prefix=/tmp/qa-cp2b-3/pyc -m pytest -q -p no:cacheprovider /tmp/qa-cp2b-3/tests/test_qa3.py`. The full file on the head: **exit 1, 4 failed / 7 passed**. The 4 failures are QA3-F1 (2 tests) and QA3-F2 (2 tests); the 7 passes are controls listed in §4. The same file on the frozen code `28981fc` (`git archive` + head tests): the same 4 failed / 7 passed. Both defects predate repair 2.

### QA3-F1 — BLOCKING (duplicated/false page; classes 6 and 10): a resolved M11 zero-update outage is re-reported every round as it slides out of `history_rounds`

`Q -k "two_zero_rounds_sliding or long_zero_outage"` → exit 1, 2 failed.

**Scenario 1 (two zero rounds):** one normal round, two zero-update rounds (warn → page), then a round with updater txs (resolved), then 8 normal rounds.
- **Expected (spec M11):** two consecutive zero rounds → page, healed by the next update. Nothing happens afterwards, so nothing more is sent.
- **Actual:** a new incident `⚠️ [WARN] EB updater sent 0 updates for 1 committed round(s)` opens and is resolved (`The updater sent 1 update(s) for blockNum …`), about 5 rounds (≈ 40 h) after the outage was over.

**Scenario 2 (7 zero rounds, `/tmp/qa-cp2b-3/tests/show_f1.py 7`):** after the correct page lifecycle (`m11-liveness-1715` open → escalate → updates → resolved), the next normal rounds send:
- `🚨 [PAGE] … 0 updates for 4 committed round(s)` (new incident `m11-liveness-4165`) + resolution;
- `🚨 [PAGE] … for 3 …` (`-4472`) + resolution;
- `🚨 [PAGE] … for 2 …` (`-4779`) + resolution;
- `⚠️ [WARN] … for 1 …` (`-5086`) + resolution.

That is **three false pages and one false warn**, one per 8 h round on mainnet. The spec's own record (2026-08-29: 0 updates for three rounds) would produce one false page and one false warn after recovery.

**Cause:**
1. `M11UpdaterLiveness.run` (`m11_updater_liveness.py:206-213`) keeps only the last `history_rounds` closed rounds once the evaluated mark has passed them.
2. `liveness()` groups the trailing zero rounds into a run, and `_zero` keys the episode `liveness:<first round of the run>` (`:303`). When the first zero round leaves the window, the run starts at the next round, so a *new* episode id is derived for the same, already-resolved outage.
3. That episode is healed, has no book row, and its `evidence_block` (the healing commit) is above the baseline. The episode book's rule "healed, never reported, `evidence_block` > baseline → finding then `resolve=True`" (`m9_oracle_common.py:355-358`) therefore reports it again as a new incident.

The same window slide also under-states an ongoing outage: an open page says "0 updates for 6 committed round(s)" at 7 rounds, and its resolution says "for 5". That is a text issue inside this finding.

### QA3-F2 — BLOCKING (false clean of a page; class 1: degenerate input read as positive proof): a settled round in which the dissenting slot is the only voter heals its dissent page

`Q -k "stray_single_vote or timed_out_round_with_only"` → exit 1, 2 failed.

**Setup (both tests):** slot 3 dissents in 3 consecutive rounds → `dissent:3` page ("the quorum margin is gone"). Then either:
- (a) slot 3 alone proposes the wrong blockNum X−1 while slots 1, 2, 4 commit X (the QA2-F1 stray vote, which the sweep models as `stray_early`); or
- (b) only slot 3 votes in a round, which times out (failed round, page).

**Expected:** neither round shows slot 3 agreeing with anyone. Slot 3 never voted the quorum's root, so the dissent page stays open. The module's own rule (`m9_oracle_quorum.py:11`) is "healed only by positive proof".

**Actual:** `✅ [RESOLVED] Oracle slot 3 dissent (3 rounds, 3 in a row)`, with the text "Slot 3 voted the reference root (and no other) in round X−1". `open_keys` = `{}` in (a) and `{'failed-round': 'page'}` in (b). Slot 3's next dissent opens a new *warn* with its streak reset, so the page comes back only after 3 more dissenting rounds (≈ 24 h).

**Cause:** for an uncommitted settled round, `round_state` takes "the unique plurality root" as the reference (`m9_oracle_quorum.py:134-140`). With one vote, that is the dissenter's own root. `dissent()` then counts `roots == {st.reference}` as agreement (`:332-334`). A one-vote, one-slot, or self-referential round is not evidence of agreement, so it should neither heal nor extend the streak (compare tied rounds, which only break it).

### NOTEs

- **N1 (carried from attempt 2, NOTE: no page is lost).** A second consecutive weak commit is not sent as its own message. Attempt 2's `test_m9_second_weak_commit_is_sent` still fails, unchanged and not in the repair-2 brief. The `weak-commit` page stays open and re-alerts every 6 h with the new body. The same merge applies to two cadence gaps between sweeps (equal `revision` None).
- **N2 (carried, NOTE: cross-lane, CP3).** No M1 incident link exists for `IncorrectClusterState` reverts: only `evidence.m1_handoff` (tx hashes). M1 is CP2d and is not on this lane or on the integration branch (`git ls-tree plan/monitoring-service reconcile/checks/`: no `m1_*`).
- **N3 (NOTE: performance within a configured budget).** One weekly full comparison held the single-threaded worker for **273.0 s** on mainnet (LIVE row, 506 clusters, 506 sequential proof GETs). A slow API is now bounded by `proof_api_run_budget_s` (600 s per run, rest UNKNOWN and retried next run; the QA2-F2 fix, falsifier R2-F2-budget red/green). Other checks' alerts can therefore wait up to ~10 min during a weekly comparison. This is the CP3 item already recorded by the implementer.

## 2. Claims I could NOT confirm / judgements on the implementer's claims

- **"Every spec trigger line has a test at its severity and a benign twin."** I derived the trigger list myself from spec §3 M9/M10/M11 (lines 267–337) and got the same 16 lines as the implementer's T1–T16. T10 and T15 are Monitor/CP1A templates and are not credited here. The M8 line "commitRoot revert with ZeroCSSVSupply → page (also caught by M9)" is CP2c's page; M9 warns (T7). Every named trigger and benign-twin test exists and is green (checked by name). The claim does **not** hold in substance for:
  - T13/T11: no test covers the silence after a resolved multi-round outage (QA3-F1);
  - T4: the page's healing rule (QA3-F2).
- **"QA2-F1..F4 fixed."** Confirmed. Attempt 2's Appendix A as published runs **15 passed / 1 failed** (only N1); the ported regressions `-k qa2` are 8 (M9) / 12 (M11) green.
- **"15 of the 22 new tests RED on the frozen code, 7 GREEN."** Confirmed exactly. I listed the 22 tests whose names are new relative to `28981fc` (`comm` of `^def test_` in the three lane test files) and checked each against the frozen run (`git archive 28981fc reconcile` + head lane tests): 15 RED (incl. the rewritten `test_a_round_superseded_within_its_30_minutes_still_fails_at_its_deadline` and the book regression `test_a_reported_zero_run_that_grew_and_healed_while_a_new_one_began_is_sent`), 7 GREEN (the controls and benign twins named in the record). Aggregate: **43 failed / 164 passed**, = `evidence/red_green_repair2.txt`.
- **The two ADAPTED attempt-1 reproducers.** The published attempt-1 file still runs **23 passed / 2 failed**, and the two failures are the ones whose setup contradicts F6. Attempt 2's judgement (legitimate adaptation, no hidden defect) stands; their ported versions are green in the `-k qa1` rows. I did not re-derive it further.
- **"F1-proof survives / S-superseded anchor error; both re-guarded."** Confirmed. In `falsify_repair1.sh`, 13 of 15 are red/green, F1-proof survives (mutated exit 0) and S-superseded fails on its anchor (`mutation anchor found 0 times`). R2-F1-proof and R2-F1-definite are red/green in `falsify_repair2.sh`.
- **LIVE M11 "3 one-root mismatches".** Confirmed and explained (§3): all three come from validator add/remove after root 26,244,378.
- **Implementer's LIVE M9 replays used saved dumps.** I replayed them with the head code (outage output byte-identical to `outage_2026_08_replay.repair2.out.json`). I also archive-read the recent window myself (§3). I did not repeat attempt 2's 827 s full-range archive read of the 2026-08 outage: the dump was proven identical then and is unchanged in git (budget note, §6).
- Not run: CP3 fork scenarios (V3.8), which are outside this checkpoint.

## 3. Verification matrix

**Preflight:**
- `git rev-parse HEAD` = `6191a3677c34949e4facce3e1206d78158022084` (= dispatch head); `git status --porcelain` empty at start; `df -h .` 345 GiB free.
- No `reconcile/.venv` existed: `uv venv reconcile/.venv --python 3.12` and `uv pip install --python reconcile/.venv/bin/python -r reconcile/requirements-dev.txt` both exit 0 (Python 3.12.15, pytest 9.1.1, ruff 0.17.1). No other install. anvil present (`~/.foundry/bin/anvil`) but no row of this checkpoint needs it.
- Frozen files: `git diff --stat plan/monitoring-service HEAD -- reconcile/tests/harness.py reconcile/tests/test_framework.py reconcile/tests/test_worker.py` → empty. `git log 96d1c32..HEAD` on those files lists exactly `bd531b4` and `500abe4`, i.e. the integration branch's patch.
- Lane scope: `git diff --name-status f92e2b7 HEAD` (lane start after the ruled merges) touches only `checks/{m9_oracle_common,m9_oracle_quorum,m10_oracle_rotation,m11_updater_liveness}.py`, the four lane test files, `fixtures_oracle.py`, one import line in `checks/__init__.py`, the `checks.m9/m10/m11` objects of `config.example.json` (adds `proof_api_run_budget_s`), and plans files. **No other frozen CP1B file was edited.** `config/`, `config.zip`, `.env*` untouched.

Rows are run one by one by `/tmp/qa-cp2b-3/vrows.sh` (log `/tmp/qa-cp2b-3/vrows.log`) with prefix `reconcile/.venv/bin/python -X pycache_prefix=/tmp/qa-cp2b-3/pyc-v -m pytest -q -p no:cacheprovider`. Porcelain was 0 before and after.

| V-ID | Command | Exit / result | Verdict |
|---|---|---|---|
| V2b.1 (V2x.1) M9 | `… reconcile/tests/test_m9.py` | 0; 68 passed | supplied PASS; QA3-F2 against it |
| V2b.1 M10 | `… reconcile/tests/test_m10.py` | 0; 23 passed | PASS |
| V2b.1 M11 | `… reconcile/tests/test_m11.py` | 0; 72 passed | supplied PASS; QA3-F1 against it |
| sweep | `… reconcile/tests/test_m9_m10_m11_sweep.py` | 0; 44 passed | PASS (SWEEP2-* falsifiers red) |
| V2b.2 (V2x.2) | `… test_m{9,10,11}.py -k thresholds_from_chain` | 0; 2 / 1 / 1 passed | PASS |
| V2b.3 (V2x.3) | `… -k restart` per file | 0; 3 / 2 / 5 passed | PASS |
| V2b.4 (V2x.4) | `… -k rpc_budget` per file | 0; 1 / 1 / 1 passed | PASS |
| V2b.5 (V2x.5) | falsifiers, §4 | 18 + 15 (repair 1) + 4 mine | PASS sensitivity (2 legacy anchors retired, as claimed) |
| V2b.6 (V2x.6) suite | `… reconcile/tests` | 0; **447 passed** in 14.8 s | PASS |
| V2b.6 ruff | `reconcile/.venv/bin/ruff check --no-cache reconcile` | 0; All checks passed! | PASS |
| V2b.7 | `… test_m9.py -k from_the_clock` | 0; **2 passed** (the V2b.7 test plus `test_qa2_f1_superseded_round_fails_from_the_clock_when_blocks_stop`; the record says 1) | PASS; QF3-m9-horizon-clock reddens both; attempt-2 clock-jump/restart control green |
| qa1 regressions | `… -k qa1` per file | 0; 8 / 1 / 7 | PASS |
| qa2 regressions | `… -k qa2` (M9, M11) | 0; 8 / 12 | PASS |
| Attempt-1 Appendix A as published | extracted to `/tmp/qa-cp2b-3/a1/test_independent.py` | 1; 23 passed / 2 failed (the two pre-F6 setups) | as claimed (§2) |
| Attempt-2 Appendix A as published | extracted to `/tmp/qa-cp2b-3/a2/test_qa2.py` | 1; **15 passed / 1 failed** (`test_m9_second_weak_commit_is_sent`, N1) | QA2-F1..F4 fixed; N1 open (NOTE) |
| RED on frozen | `git archive 28981fc reconcile` → `/tmp/qa-cp2b-3/frozen` + head lane tests and `fixtures_oracle.py` | 1; **43 failed / 164 passed**; 15 of 22 new tests RED, 7 GREEN | as claimed |
| QA3 independent scenarios | `Q` (Appendix A) | 1; **4 failed / 7 passed** | **FAIL** (QA3-F1, QA3-F2) |
| LIVE M11 weekly comparison | `reconcile/.venv/bin/python plans/monitoring-service/workers/cp2b/evidence/live_m11_leaves.py https://ethereum-rpc.publicnode.com`, run from a `git archive HEAD` copy (byte-identical head code, so falsifier runs in the worktree could not interfere), 2026-10-22T07:30:41–07:35:19Z | 0; block **26,246,661**, latest committed blockNum 26,244,378 (= API `referenceBlock`), 506 active ETH clusters, 0 stale tuples, **506 compared, 0 unknown**, 3 mismatches (`0x07bdd21f…` chain 16,192 / leaf 16,160; `0x778c05ae…` 31,296 / 31,360; `0xa9f7bfa1…` 18,080 / 18,048); 506 eth_call + 507 eth_getStorageAt; 273.0 s | PASS (LIVE) |
| LIVE mismatch root cause | own `/tmp/qa-cp2b-3/live/owners.py` (Appendix B): proxy logs with topic1 = owner, blocks 26,244,378–26,246,661, 50-block chunks, **eth-pokt.nodies.app (92 requests)**. publicnode answered `HTTP 403 Forbidden` right after the 273 s run; eth.drpc.org answered `HTTP 408 Request Timeout` | `0x07bdd21f…`: 1 `ValidatorAdded` at **26,244,588** (+32); `0xa9f7bfa1…`: 1 `ValidatorAdded` at **26,244,734** (+32); `0x778c05ae…`: 2 `ValidatorRemoved` at **26,244,738** (−64). All are after root 26,244,378, and each EB delta equals chain − leaf | benign: on-chain EB follows validator changes after the root, younger than one round, correctly not warned (F6) |
| LIVE M9/M10 recent rounds (own archive read) | `/tmp/qa-cp2b-3/live/scan.py 26233575 26246683 recent.json` (Appendix B; head 26,246,703 − 20): 263 × 50-block chunks, **eth-pokt.nodies.app 253, eth.drpc.org 10**, 209.7 s | 0; **20 logs, 5 rounds, 5 commits** (blockNums 26,234,805 / 26,237,195 / 26,239,590 / 26,241,984 / 26,244,378; 3 votes each, the 4th skipped; slot 3 votes the committed root in 3 of them), **0 OracleReplaced**, sha256 `5acd4aba…92531`. The implementer's saved 12-log dump is an exact subset. Replay `live_replay.py recent.json 400` (head copy) exit 0: **0 incidents, 0 messages, 0 health** for M9 and M10 | PASS (LIVE) |
| LIVE outage replay (saved dump, head code) | `live_replay.py …/evidence/outage_2026_08_logs.json 400` (head copy) | 0; 89 logs / 21 rounds / 13 commits; failed-round page resolved, cadence page resolved, dissent:3 page open, absent:3 warn open, six split warns resolved; 89 messages; output **byte-identical** to `outage_2026_08_replay.repair2.out.json` | PASS (dump = archive read of attempt 2) |

## 4. Falsifiers

**Implementer's repair-2 set:** `bash plans/monitoring-service/workers/cp2b/evidence/falsify_repair2.sh` (log `/tmp/qa-cp2b-3/falsify_repair2.log`), exit 0.
- **18 / 18** mutations: mutated exit **1**, restored exit **0**, `git status --porcelain` `''` after every pair: R2-F1-literal, R2-F1-start, R2-F1-own, R2-F1-evidence, R2-F2-abort, R2-F2-budget, R2-F3-definite, R2-F3-latest, R2-F4-mark, R2-F4-lower, R2-book, SWEEP2-m9-literal, SWEEP2-m11-abort, SWEEP2-m11-mark, SWEEP2-m11-definite, SWEEP2-book, R2-F1-proof, R2-F1-definite.
- Fresh bytecode cache per process (`PYTHONPYCACHEPREFIX=$(mktemp -d)`).

**Implementer's repair-1 set, re-run:** `bash …/falsify_repair1.sh` (log `/tmp/qa-cp2b-3/falsify_repair1.log`), exit 0.
- 13 red/green: F1-retry, F2-order, F3-split, F3-sameslot, F4-events, F5-before, F6-age, F7-consecutive, F8-clock, F8-slots, SWEEP-m9, SWEEP-m11, SWEEP-m10.
- **F1-proof survives** (mutated exit 0) and **S-superseded: anchor error**, both as the record states. Their lines are re-guarded by R2-F1-proof / R2-F1-definite (red/green above), and S-superseded's reading was withdrawn by the QA2-F1 fix.
- Porcelain was `''` after all 14 pairs that mutated.

**Mine** (`/tmp/qa-cp2b-3/qfalsify.py`, Appendix C): byte restore in `finally`, fresh pycache per process, log `/tmp/qa-cp2b-3/qfalsify.log`.

| ID (class) | Mutation | Test | Mutated / restored | porcelain |
|---|---|---|---|---|
| QF3-m9-horizon-clock (4: the clock must fire with no block) | `m9_oracle_common.py` `return max(float(self(self.ctx.block)), self.ctx.clock() - lag)` → `return float(self(self.ctx.block))` | `test_m9.py -k from_the_clock` (V2b.7) | 1 (2 failed) / 0 | `''` |
| QF3-m10-same-block-position (3: same-block order) | `m10_oracle_rotation.py` `v.pos < ev.pos` → `v.block <= ev.block` | QA3 `test_m10_same_block_rotation_then_first_vote_of_a_new_round_is_silent` | 1 / 0 | `''` |
| QF3-m11-zero-rounds-boundary (`>=` vs `>` on N) | `if len(run) >= self.config["zero_rounds_page"]:` → `>` | `test_m11.py::test_two_consecutive_zero_rounds_page` | 1 / 0 | `''` |
| QF3-m11-reorg-text (2: README 6.2 obligation of the episode book) | `m9_oracle_common.py` `if ctx.reorg_ancestor is not None and r["evidence_block"] > ctx.reorg_ancestor:` → `if False and …` | QA3 `test_m11_reorg_beyond_confirmations_removing_the_commit_of_an_open_zero_round` | 1 / 0 | `''` |

**QA3 controls that pass** (spec-derived, outside the implementer's list):
- **M11:** a reorg beyond the confirmation window (conf 2) removes the commit of an open zero round, and the warn resolves with "Evidence left the canonical chain" and nothing else. A mismatch at R1, UNKNOWN at R2 (API forgets the cluster) and mismatching at R3 warns at R3.
- **M9:**
  - A cadence page whose gap a deep reorg fills with a RootCommitted resolves and is not re-sent across a restart.
  - Two roots in one block (dissent first), then commit, gives `dissent:3` warn only.
  - A reorg onto a **shorter** chain without the dissenting round resolves the warn with the reorg text and nothing else, and a restart sends nothing more.
- **M10:**
  - A rotation with no open round is silent.
  - A rotation inside an open round pages once and does not duplicate across a restart.
  - A rotation followed by the first vote of a new round in the **same block** is silent.

Attempt 2's 15 passing controls (clock jump +9.5 h, restart during silence, −2 h NTP step, one 6 h re-alert, health-only reorg, beyond-window reorg, M10 reorg, `{}` proof answers, 10 %/11.1 % revert boundary, two zero rounds and a restart) all still pass on this head.

## 5. Evidence-plane writes

- CP2b **Status:** `IMPLEMENTED — AWAITING QA — repair 2` → `QA FAILED` on frozen head `6191a3677c34949e4facce3e1206d78158022084`, linking this report (QA3-F1, QA3-F2 BLOCKING; N1–N3 NOTE).
- Wrote `plans/monitoring-service/qa/cp2b-attempt-3.md` (this file) and appended one event `event-cp2b-qa-3` to `plans/monitoring-service/execution-log.md`.
- Local untracked venv `reconcile/.venv` (ignored). Scratch only under `/tmp/qa-cp2b-3`. No test was added to the repo, no implementation file was changed, and no next checkpoint was started.

## 6. Repo proof

Measured after all falsifier restorations, the LIVE runs and the final QA3 run, **before** the evidence-plane writes:

```text
$ git status --porcelain
<empty>
$ git diff --name-only 6191a3677c34949e4facce3e1206d78158022084
<empty>
```

The output after the permitted writes, before the commit, is recorded in the commit of this report: only `plans/monitoring-service/{plan.md,execution-log.md,qa/cp2b-attempt-3.md}`, and `git diff --exit-code HEAD -- reconcile` is empty. **No implementation file appears in either output.**

**Budget.** Each row ran once. The suite (15 s) ran once. The LIVE M11 comparison (273 s) ran once. The recent-rounds archive read ran once (210 s).
- **Skipped:** the 827 s full-range archive re-read of the 2026-08 outage. Attempt 2 proved the saved dump identical to the archive (sha256 `128d8aae…7f383`), and the dump is unchanged in git, so I replayed it with the head code instead.
- **Not re-run separately:** attempt 2's own six falsifiers. Their anchors are unchanged except QF-m9-cadence-clock, which is covered here by QF3-m9-horizon-clock. Their tests are green.

## Appendix A — `/tmp/qa-cp2b-3/tests/test_qa3.py`

<!-- BEGIN test_qa3.py -->
```python
"""CP2b QA attempt 3: independent spec-derived scenarios (QA scratch, not part of the repo).

Each expectation comes from the spec text (docs/ssv-specs/monitoring-service-spec.md M9-M11), README 6.2 and
DEFECT-CLASSES, not from the implementation's output.
Run: PYTHONPATH=reconcile:reconcile/tests reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider <this file>
"""
from __future__ import annotations

from fixtures_oracle import (
    HOUR,
    SLOTS,
    UpdaterWorld,
    incidents,
    open_keys,
    oracle_worker,
    restart,
    sent,
    tick,
)

REORG_TEXT = "Evidence left the canonical chain"


def pw(w, check):
    return [(r["ikey"], r["severity"], r["kind"]) for r in sent(w, check, "severity IN ('page','warn')")]


def allk(w, check):
    return [(r["ikey"], r["severity"], r["kind"]) for r in sent(w, check)]


def world(tmp_path, **kw):
    u = UpdaterWorld(tmp_path, **kw)
    tick(u.w)
    return u


# ------------------------------------------------------------------------------------------------ M11


def test_m11_two_zero_rounds_sliding_out_of_history_are_not_reported_again(tmp_path):
    """Spec M11: two consecutive zero rounds -> page; the next round with updates heals it. Nothing new happens
    afterwards (every later round has updater txs): no further M11 page/warn may be sent (class 10: no duplicated
    alert; class 6: a cap must not change what was already reported)."""
    u = world(tmp_path)
    u.round(updates=(0,))
    u.close()
    u.round(updates=())
    u.close()
    u.round(updates=())
    u.close()
    assert open_keys(u.w, "m11") == {"liveness": "page"}
    u.round(updates=(1,))
    u.close()
    assert open_keys(u.w, "m11") == {}
    before = allk(u.w, "m11")
    assert [k for _, _, k in before] == ["open", "escalate", "resolved"], before
    for _ in range(8):  # eight normal rounds: the zero run leaves the last `history_rounds` (6) closed rounds
        u.round(updates=(0,))
        u.close()
    after = allk(u.w, "m11")[len(before):]
    assert after == [], (after, [r["text"][:160] for r in sent(u.w, "m11")[len(before):]])


def test_m11_long_zero_outage_recovery_sends_no_further_pages(tmp_path):
    """The updater is dead for 7 rounds (page), then recovers. After its resolution, normal rounds must stay
    silent: a resolved outage is not re-paged round after round while it slides out of the history window."""
    u = world(tmp_path)
    u.round(updates=(0,))
    u.close()
    for _ in range(7):
        u.round(updates=())
        u.close()
    assert open_keys(u.w, "m11") == {"liveness": "page"}
    u.round(updates=(1,))
    u.close()
    assert open_keys(u.w, "m11") == {}
    n = len(allk(u.w, "m11"))
    for _ in range(8):
        u.round(updates=(0,))
        u.close()
    extra = allk(u.w, "m11")[n:]
    assert extra == [], (extra, [r["text"][:160] for r in sent(u.w, "m11")[n:]])


def test_m11_reorg_beyond_confirmations_removing_the_commit_of_an_open_zero_round(tmp_path):
    """README 6.2: an open liveness warn whose RootCommitted leaves the canonical chain (depth > confirmations)
    is resolved with the reorg text and never re-sent while the new chain has no such round."""
    u = world(tmp_path, confirmations=2)
    u.round(updates=(0,))
    u.close()
    fork = u.c.number
    u.round(updates=())
    u.close()
    assert open_keys(u.w, "m11") == {"liveness": "warn"}
    n = len(sent(u.w, "m11"))
    u.oc.reorg(fork, latest=u.oc.latest)  # replaces every block from before the zero round's commit
    u.ssv.latest_root = getattr(u.ssv, "latest_root", 0)
    u.c.mine(20)
    tick(u.w, u.oc, 5)
    tick(u.w, u.oc, 320)
    new = sent(u.w, "m11")[n:]
    assert open_keys(u.w, "m11") == {}, (open_keys(u.w, "m11"), [(r["kind"], r["text"][:100]) for r in new])
    assert [r["kind"] for r in new] == ["resolved"] and REORG_TEXT in new[0]["text"], [
        (r["kind"], r["text"][:120]) for r in new]


def test_m11_mismatch_aged_across_a_partially_unknown_round(tmp_path):
    """Spec M11 weekly: 'any mismatch older than one round -> warn'. Cluster 0 mismatches at R1 (weekly), is
    UNKNOWN at R2 (the API forgets it), mismatches at R3: it has mismatched at two evaluated roots with nothing
    showing a match between -> warn at R3 (probe; a one-round delay is reported as a NOTE)."""
    u = world(tmp_path)
    cid = u.cids[0]
    u.round(updates=(1,))
    u.set_eb(32, leaves={cid: 40})
    u.close()  # R1: weekly full comparison, cluster 0 mismatches
    u.round(updates=(1,))
    u.set_eb(32, leaves={cid: 40})
    saved = u.api.leaves.pop(cid)
    u.close()  # R2: cluster 0 not found -> unknown
    u.api.leaves[cid] = saved
    u.round(updates=(1,))
    u.set_eb(32, leaves={cid: 40})
    u.close()  # R3: mismatches again
    assert open_keys(u.w, "m11").get("state-mismatch") == "warn", (open_keys(u.w, "m11"), pw(u.w, "m11"))


# ------------------------------------------------------------------------------------------------ M9


def _dissent_rounds(oc, w, n):
    for _ in range(n):
        tick(w, oc, 10)
        oc.round(variants={3: 1}, voters=(3, 1, 2, 4))
        tick(w, oc, 5)


def test_m9_stray_single_vote_round_is_not_positive_proof_that_a_dissenting_slot_agrees(tmp_path):
    """Spec M9: three dissenting rounds in a row from one slot -> page. The paging slot then proposes a wrong
    blockNum alone (X-1) while slots 1, 2, 4 commit X. That stray round has one vote: slot 3 'agreeing with
    itself' is no evidence that it runs the right tree build, so the dissent page must not be resolved."""
    oc, w = oracle_worker(tmp_path)
    tick(w)
    _dissent_rounds(oc, w, 3)
    assert open_keys(w, "m9").get("dissent:3") == "page", open_keys(w, "m9")
    tick(w, oc, 10)
    x = oc.c.number - 1
    oc.vote(x - 1, 3, variant=1)
    oc.round(ref=x, voters=(1, 2, 4))
    tick(w, oc, 200)  # past X-1's 30 min (it also pages as a failed round: QA2-F1)
    res = [r for r in sent(w, "m9", "kind='resolved'") if r["ikey"] == "dissent:3"]
    assert open_keys(w, "m9").get("dissent:3") == "page" and not res, (open_keys(w, "m9"), [r["text"] for r in res])


def test_m9_timed_out_round_with_only_the_dissenter_voting_does_not_heal_its_page(tmp_path):
    """Same as above with a round in which the three other slots are down: only slot 3 votes, the round times out
    (failed round, page). Slot 3's lone root is not a reference it 'agrees' with: dissent:3 stays at page."""
    oc, w = oracle_worker(tmp_path)
    tick(w)
    _dissent_rounds(oc, w, 3)
    assert open_keys(w, "m9").get("dissent:3") == "page", open_keys(w, "m9")
    tick(w, oc, 10)
    x = oc.c.number - 1
    oc.vote(x, 3, variant=1)
    tick(w, oc, 200)
    keys = open_keys(w, "m9")
    res = [r for r in sent(w, "m9", "kind='resolved'") if r["ikey"] == "dissent:3"]
    assert keys.get("failed-round") == "page" and keys.get("dissent:3") == "page" and not res, (
        keys, [r["text"] for r in res])


def test_m9_cadence_page_resolved_by_a_reorg_that_adds_the_commit_is_not_reopened(tmp_path):
    """Cadence page (> 9 h, clock-driven), then a deep reorg brings a chain that has a RootCommitted inside the
    gap: the page resolves and is not re-sent (no duplicate) across a restart."""
    oc, w = oracle_worker(tmp_path, confirmations=2)
    tick(w)
    oc.round()
    tick(w, oc, 5)
    tick(w, seconds=9.5 * HOUR)
    assert open_keys(w, "m9") == {"cadence": "page"}
    fork = oc.c.number - 1
    oc.reorg(fork, latest=oc.latest)
    oc.c.mine(3)
    oc.round()
    tick(w, oc, 5)
    assert open_keys(w, "m9") == {}, open_keys(w, "m9")
    n = len(sent(w, "m9"))
    w = restart(tmp_path, w, oc)
    tick(w, oc, 5, seconds=60)
    assert len(sent(w, "m9")) == n and open_keys(w, "m9") == {}


# ------------------------------------------------------------------------------------------------ M10


def test_m10_rotation_with_no_open_round_is_silent_and_rotation_in_open_round_pages_once_across_restart(tmp_path):
    """Spec M10: OracleReplaced while a round is open -> page (once, restart-idempotent); with no open round:
    nothing from the check (info is the Monitor's)."""
    oc, w = oracle_worker(tmp_path, ids=("m10",))
    tick(w)
    oc.round()
    tick(w, oc, 5)
    oc.replace(2, "0x" + "22" * 20)
    tick(w, oc, 2)
    assert open_keys(w, "m10") == {} and pw(w, "m10") == []
    x = oc.c.number - 1
    oc.vote(x, 1)
    oc.replace(3, "0x" + "33" * 20)
    tick(w, oc, 2)
    assert list(open_keys(w, "m10").values()) == ["page"], open_keys(w, "m10")
    w = restart(tmp_path, w, oc, ids=("m10",))
    tick(w, oc, 2, seconds=60)
    assert [k for _, _, k in pw(w, "m10")] == ["open"], pw(w, "m10")


def test_m9_same_block_two_roots_then_commit_attributes_the_dissent(tmp_path):
    """Class 3: slot 3's root B and slot 1's root A land in the same block (B first), then slots 2 and 4 commit A:
    two roots for one blockNum -> warn, attributed to slot 3; nothing for slots 1, 2, 4."""
    oc, w = oracle_worker(tmp_path)
    tick(w)
    x = oc.c.number - 1
    oc.vote(x, 3, variant=1, mine=False)
    oc.vote(x, 1, mine=True)
    oc.vote(x, 2)
    oc.vote(x, 4)
    assert oc.committed(x)
    tick(w, oc, 5)
    assert open_keys(w, "m9") == {"dissent:3": "warn"}, open_keys(w, "m9")


def test_m10_same_block_rotation_then_first_vote_of_a_new_round_is_silent(tmp_path):
    """Class 3: the previous round is committed; in ONE block the DAO rotates slot 2 and then slot 1 casts the first
    vote of the next round. At the rotation's position no round is open -> no page (the vote comes after it)."""
    oc, w = oracle_worker(tmp_path, ids=("m10",))
    tick(w)
    oc.round()
    tick(w, oc, 5)
    x = oc.c.number - 1
    oc.replace(2, "0x" + "22" * 20, mine=False)
    oc.vote(x, 1, mine=True)
    tick(w, oc, 2)
    assert open_keys(w, "m10") == {} and pw(w, "m10") == [], (open_keys(w, "m10"), pw(w, "m10"))


def test_m9_reorg_onto_a_shorter_chain_without_the_dissenting_round_resolves_with_the_reorg_text(tmp_path):
    """README 6.2 + 6.1 (shorter chain): the dissent warn's round (votes and commit) is replaced by a SHORTER chain
    with no oracle event: the warn resolves with 'Evidence left the canonical chain', nothing else is sent, and a
    restart sends nothing more."""
    oc, w = oracle_worker(tmp_path, confirmations=0)
    tick(w)
    first = oc.c.number
    oc.round(variants={3: 1}, voters=(3, 1, 2, 4))
    tick(w)
    assert open_keys(w, "m9") == {"dissent:3": "warn"}, open_keys(w, "m9")
    n = len(sent(w, "m9"))
    oc.reorg(first, latest=0, refs=())
    oc.c.mine(1)  # shorter than the replaced chain
    tick(w)
    new = sent(w, "m9")[n:]
    assert open_keys(w, "m9") == {} and [r["kind"] for r in new] == ["resolved"] and REORG_TEXT in new[0]["text"], (
        open_keys(w, "m9"), [(r["kind"], r["text"][:100]) for r in new])
    w = restart(tmp_path, w, oc)
    tick(w, oc, 2)
    assert len(sent(w, "m9")) == n + 1
```
<!-- END test_qa3.py -->

## Appendix B — LIVE scripts (read-only)

`/tmp/qa-cp2b-3/live/scan.py`:

```python
"""Read-only: every WeightedRootProposed / RootCommitted (and, counted separately, OracleReplaced) log of the SSV
proxy in [a, b], 50-block chunks, rotating public archive endpoints; records the endpoint of every chunk.
Usage: scan.py a b out.json"""
import concurrent.futures, hashlib, json, sys, time, urllib.request
sys.path.insert(0, "/tmp/qa-cp2b-3/head/reconcile")
from eb_events import TOPIC0  # noqa: E402
PROXY = "0xDD9BC35aE942eF0cFa76930954a156B3fF30a4E1"
WRP = "0xc0516bad4be527b56ad33b7087029ffee230e390e2f2d1076597820c9e170b67"
RC, OR = TOPIC0["RootCommitted"], TOPIC0["OracleReplaced"]
URLS = ["https://eth-pokt.nodies.app", "https://eth.drpc.org", "https://eth-mainnet.public.blastapi.io"]
a, b, out = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
def call(u, m, p):
    req = urllib.request.Request(u, json.dumps({"jsonrpc": "2.0", "id": 1, "method": m, "params": p}).encode(),
                                 {"content-type": "application/json", "user-agent": "qa"})
    r = json.load(urllib.request.urlopen(req, timeout=60))
    if "error" in r:
        raise RuntimeError(r["error"])
    if not isinstance(r.get("result"), list):
        raise RuntimeError(f"non-list result {str(r)[:80]}")
    return r["result"]
def get(i_ch):
    i, (s, e) = i_ch
    last = None
    for k in range(12):
        u = URLS[(i + k) % len(URLS)]
        try:
            return s, e, u, call(u, "eth_getLogs", [{"address": PROXY, "fromBlock": hex(s), "toBlock": hex(e),
                                                    "topics": [[WRP, RC, OR]]}])
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1 + k)
    raise SystemExit(f"chunk {s}-{e} failed: {last}")
t0 = time.time()
chunks = [(s, min(s + 49, b)) for s in range(a, b + 1, 50)]
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
    res = list(ex.map(get, enumerate(chunks)))
logs = sorted([l for *_, r in res for l in r], key=lambda x: (int(x["blockNumber"], 16), int(x["logIndex"], 16)))
rotations = [l for l in logs if l["topics"][0] == OR]
logs = [l for l in logs if l["topics"][0] != OR]
ends = {}
for *_, u, _r in res:
    ends[u] = ends.get(u, 0) + 1
canon = [{k: l[k] for k in ("blockNumber", "logIndex", "transactionHash", "topics", "data")} for l in logs]
json.dump({"rpc": ",".join(sorted(ends)), "from": a, "to": b, "logs": logs}, open(out, "w"))
print(json.dumps({"range": [a, b], "chunks": len(chunks), "endpoints": ends, "logs": len(logs),
                  "oracle_replaced": len(rotations), "sha256_canon": hashlib.sha256(json.dumps(canon, sort_keys=True).encode()).hexdigest(),
                  "seconds": round(time.time() - t0, 1)}))
```

`/tmp/qa-cp2b-3/live/owners.py` (run as `owners.py https://eth-pokt.nodies.app,https://eth.drpc.org,https://eth-mainnet.public.blastapi.io 26244378 26246661 50`):

```python
"""Read-only: proxy logs with topic1 = owner in [a, b] for the LIVE mismatch clusters; decode kind + cluster id."""
import json, sys, urllib.request
sys.path.insert(0, "/tmp/qa-cp2b-3/head/reconcile")
from eb_events import TOPIC0, decode_log  # noqa
URL = sys.argv[1]; a, b = int(sys.argv[2]), int(sys.argv[3])
PROXY = "0xDD9BC35aE942eF0cFa76930954a156B3fF30a4E1"
WANT = {"0x07bdd21fbc840d6c7b9e8c15af6103d89402b389a5b5d91fd8af0089ff46b13c": "0x9d934341b6a837b87904139719afb231b130a5a9",
        "0x778c05aeb4d2542af24242a1eebc1176bfb35147b1a2bb8bea87335e30112233": "0xe2758939507b35a22c30039a7da04b54cdc4c9a0",
        "0xa9f7bfa12dd9c3803f03d5f640f2aaa459cf29e11a5afbe0196c99836d920dff": "0xe2758939507b35a22c30039a7da04b54cdc4c9a0"}
names = {v: k for k, v in TOPIC0.items()}
import time
URLS = URL.split(",")
USED = {}
def rpc(m, p):
    last = None
    for i in range(12):
        u = URLS[i % len(URLS)]
        try:
            r = _rpc(u, m, p)
            USED[u] = USED.get(u, 0) + 1
            return r
        except Exception as exc:  # noqa
            last = exc
            time.sleep(1 + i)
    raise SystemExit(f"{m} failed: {last}")
def _rpc(URL, m, p):
    req = urllib.request.Request(URL, json.dumps({"jsonrpc": "2.0", "id": 1, "method": m, "params": p}).encode(),
                                 {"content-type": "application/json", "user-agent": "qa"})
    r = json.load(urllib.request.urlopen(req, timeout=60))
    if "error" in r: raise RuntimeError(r["error"])
    return r["result"]
for owner in sorted(set(WANT.values())):
    logs = []
    for s in range(a, b + 1, int(sys.argv[4]) if len(sys.argv) > 4 else 1000):
        logs += rpc("eth_getLogs", [{"address": PROXY, "fromBlock": hex(s), "toBlock": hex(min(b, s + (int(sys.argv[4]) if len(sys.argv) > 4 else 1000) - 1)),
                                     "topics": [None, "0x" + owner[2:].rjust(64, "0")]}])
    for l in logs:
        kind = names.get(l["topics"][0], l["topics"][0][:10])
        try:
            ev = decode_log(l)
            cid = "0x" + ev.cluster_id.hex() if ev.cluster_id else None
            eb = ev.eb
        except Exception as exc:  # noqa
            cid, eb = None, f"undecoded {exc}"
        print(int(l["blockNumber"], 16), owner[:10], kind, (cid or "")[:12], "MATCH" if cid in WANT else "", "eb", eb,
              "ref", getattr(ev, "ref_block", None), l["transactionHash"][:12])
print("endpoints", USED)
```

## Appendix C — `/tmp/qa-cp2b-3/qfalsify.py`

```python
"""QA3 falsifiers: one-token mutations of the worktree, each must redden its named test; byte restore in finally;
fresh bytecode cache per pytest process; git status --porcelain recorded after each pair."""
import os, subprocess, sys, tempfile
ROOT = "/Users/marco/orca/workspaces/ssv-network-monitor/ms-qa-cp2b-3"
PY = ROOT + "/reconcile/.venv/bin/python"
Q = "/tmp/qa-cp2b-3/tests/test_qa3.py"
M = [
    ("QF3-m9-horizon-clock", "reconcile/checks/m9_oracle_common.py",
     "return max(float(self(self.ctx.block)), self.ctx.clock() - lag)", "return float(self(self.ctx.block))",
     ["reconcile/tests/test_m9.py", "-k", "from_the_clock"]),
    ("QF3-m10-same-block-position", "reconcile/checks/m10_oracle_rotation.py",
     "before = [v for v in rnd.votes if v.pos < ev.pos]", "before = [v for v in rnd.votes if v.block <= ev.block]",
     [Q, "-k", "same_block_rotation_then_first_vote"]),
    ("QF3-m11-zero-rounds-boundary", "reconcile/checks/m11_updater_liveness.py",
     'if len(run) >= self.config["zero_rounds_page"]:', 'if len(run) > self.config["zero_rounds_page"]:',
     ["reconcile/tests/test_m11.py::test_two_consecutive_zero_rounds_page"]),
    ("QF3-m11-reorg-text", "reconcile/checks/m9_oracle_common.py",
     'if ctx.reorg_ancestor is not None and r["evidence_block"] > ctx.reorg_ancestor:',
     'if False and r["evidence_block"] > ctx.reorg_ancestor:',
     [Q, "-k", "reorg_beyond_confirmations_removing_the_commit_of_an_open_zero_round"]),
]
def pt(args):
    env = dict(os.environ, PYTHONPATH="reconcile:reconcile/tests", PYTHONPYCACHEPREFIX=tempfile.mkdtemp(dir="/tmp/qa-cp2b-3"))
    r = subprocess.run([PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args], cwd=ROOT, env=env,
                       capture_output=True, text=True)
    return r.returncode, (r.stdout.strip().splitlines() or [""])[-1]
for name, f, old, new, args in M:
    path = os.path.join(ROOT, f)
    orig = open(path, "rb").read()
    s = orig.decode()
    assert s.count(old) == 1, (name, s.count(old))
    try:
        open(path, "w").write(s.replace(old, new))
        m = pt(args)
    finally:
        open(path, "wb").write(orig)
    r = pt(args)
    por = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    print(f"{name}: mutated exit {m[0]} ({m[1]}); restored exit {r[0]} ({r[1]}); porcelain '{por}'")
```

`/tmp/qa-cp2b-3/tests/show_f1.py` (QA3-F1 message listing; run from the worktree root with `PYTHONPATH=reconcile:reconcile/tests reconcile/.venv/bin/python /tmp/qa-cp2b-3/tests/show_f1.py 7`):

```python
import sys, tempfile, pathlib
from fixtures_oracle import UpdaterWorld, sent, tick, open_keys
tmp = pathlib.Path(tempfile.mkdtemp(dir="/tmp/qa-cp2b-3"))
u = UpdaterWorld(tmp); tick(u.w)
refs = []
refs.append(u.round(updates=(0,))); u.close()
for _ in range(int(sys.argv[1])):
    refs.append(u.round(updates=())); u.close()
refs.append(u.round(updates=(1,))); u.close()
print("rounds:", refs, "zero:", refs[1:-1], "recovery:", refs[-1])
for _ in range(8):
    r = u.round(updates=(0,)); u.close()
    for row in sent(u.w, "m11"):
        if row.get("_seen"): pass
seen = 0
for row in sent(u.w, "m11"):
    print(row["incident_id"], row["severity"], row["kind"], "|", row["text"].splitlines()[0][:110], "|", (row["text"].splitlines()[1] if len(row["text"].splitlines())>1 else "")[:160])
```
