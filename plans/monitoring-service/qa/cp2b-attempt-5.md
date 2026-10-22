PASS WITH NOTES

Independent QA of CP2b (M9, M10, M11), attempt 5 (after repair 4). Frozen head under test: `48e997d0dd66fe791e11f3152b422517876b20f2` (lane branch `mtabasco/ms-cp2b`, code `6835967`). QA worktree `ms-qa-cp2b-5`, branch `mtabasco/ms-qa-cp2b-5`. Task `task_80f043fd604e`, dispatch `ctx_72b394dcb3e3`. QA model: Claude Opus 5.5, effort high (D13). Measured 2026-10-22 10:00–10:20 UTC.

**Policy applied.** This is a capped round under D15. BLOCKING means a missed or lost page, a false clean, a false page, an unsafe action, data loss, a fail-open gate or a secret leak. Everything else is a NOTE. Owner scope: no mutation campaign and no long checks (§6 lists what I skipped). No implementation repair. Scratch is under `$S` = `/private/tmp/claude-501/-Users-marco-orca-workspaces-ssv-network-monitor-ms-qa-cp2b-5/2fde45dc-6a69-4ee1-bb4f-43428fb99970/scratchpad`.

**Summary.**
- **Every supplied row reproduces exactly:** M9 91, M10 24, M11 114, sweep 113, suite 582, ruff clean, V2b.7 2.
- **The attempt-4 reproducers are green.** Both QA4-F1 tests and the QA4-F2 test pass. The QA4 file as published runs 9 passed / 2 failed; the 2 failures are N4 and N5, which are NOTEs.
- **The repair-4 regressions are genuine.** On `ffc3b27` code, 26 of the 31 new or renamed tests are RED and 29 of 113 sweep cases fail.
- **Falsifiers:** the implementer's 18 repair-4 falsifiers go red/green, and so do my 4 own.
- **D14 switches:** each switch works as ruled. On leaves behaviour unchanged. Off is silent, writes a health line and resolves an open incident once. Off then on again re-derives the condition. The other page rules still fire with the switches off.
- **LIVE:** a re-read of the current mainnet rounds is silent. The 2026-08 outage replay is byte-identical to the repair-4 output.

My own 16 spec-derived scenarios give 13 passed / 3 failed. **None of the failures is BLOCKING under D15:**
- **QA5-F1 (NOTE):** the QA4-F2 symptom on a sibling path.
- **QA5-N2 (NOTE):** a message text.

## 1. Findings (ranked)

Reproducer file: `$S/tests/test_qa5.py`, embedded verbatim in Appendix A (extract it with the BEGIN/END split used by `workers/cp2b/evidence/verify.sh`). `Q` = `PYTHONPATH=reconcile:reconcile/tests reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider $S/tests/test_qa5.py`.
- Whole file on the head: **exit 1, 3 failed / 13 passed** (log `$S/qa5.log`).
- On `ffc3b27` code plus the head tests: the two QA5-F1 tests fail there too, so they predate repair 4. The switch tests cannot run there, because the switch keys are unknown and give a startup error.

### QA5-F1 — NOTE (QA4-F2 sibling path; classes 2 and 1): a reorg that moves the zero round's commit, while the proof API is down or partial, downgrades the page silently and pages again later

`Q -k moving_the_zero` → exit 1, 2 failed. The benign twin (`…api_up_sends_nothing_twin`) passes.

**Setup:**
1. A zero-update round B pages because its leaf differs for cluster 1.
2. A reorg beyond `confirmations` 2 removes B's `RootCommitted` block. On the new chain the same blockNum commits one block later, still with no updater transaction.
3. The proof API is down (or answers every cluster except the one that differed) during the reorg run.

**Expected:** README §6.2 rule 4 and DEFECT-CLASSES 1–2 say a reorg run that cannot re-evaluate a discarded proof raises. So the page is kept and no second page is sent. That is exactly what repair 4 claims for QA4-F2 ("page kept, no second page").

**Actual** (trace from the Appendix A setup):

```text
reorg run: [('warn', 'open')] marker None sent []
sent: page escalate 🚨 [PAGE] EB updater sent 0 updates for 1 committed round(s) — test
incident ids: [('m11-liveness-1713', 'page', 'open')]
```

- The reorg run commits: the marker is cleared and the open page is stored at **warn** without a message. A `proof-api` health line is sent.
- When the API answers again, a **second 🚨 page** goes out (`escalate`) for the same incident `m11-liveness-1713`.
- The partial variant behaves the same way: `(([('warn', 'open')], []), [('liveness', 'page', 'escalate')])`.

**Cause:**
1. `zero_round_proof` (`m11_updater_liveness.py:514-516`) nulls `raw` when `raw["commit_block"] != commit.block`, before the QA4-F2 guard (`:522`) checks `raw and not saved`.
2. A moved commit therefore never reaches the raise. `compare_leaves` returns `error` (or `unknown`), `_zero` builds a warn, and `Alerts.apply` stores the lower severity silently.

**Why NOTE and not BLOCKING (D15):**
- **No page is missed and none is false.** The original page was delivered. The incident stays open throughout. A health line says the proof API is unavailable. When the condition can be proven again, the operator is paged again for a true condition.
- **What happens is a transient, self-restoring severity downgrade plus a duplicated page.** D15 removed "duplicated" from the BLOCKING list. It lists a transient alert and a downgraded warn as NOTEs. The only concrete loss is the 6 h page re-alert (`DEFAULT_REALERT_S`), which becomes a 24 h warn re-alert while the API is down.
- **It is reachable only with a reorg deeper than the round's 60 min window.** The page exists only after the window has closed, so the removed commit is at least ~300 blocks deep: beyond Ethereum finality, and far beyond `confirmations` 12. That has to coincide with a proof API outage. (Attempt 4's QA4-F2 path needed only a reorg below the proof's evaluation block.)
- **What would flip it:** if the coordinator reads the stored page→warn downgrade as a "lost page", this becomes BLOCKING. The D14 reduced configuration (`checks.m11.zero_round_proof_enabled` false) removes this path entirely.
- **Owner:** coordinator/Marco. Suggested fix: apply the raise guard before discarding `raw` for a moved commit of the same `ref`.

### QA5-N2 — NOTE (message text): with `dissent_enabled` false, a split's resolution points at the disabled rule

`Q -k split_resolution_text` → exit 1.
- With dissent off, an open round showing two roots warns `split` (correct: spec "two different merkleRoot values … → warn").
- When the round commits, the split resolves with the text "the dissent is attributed per slot (dissent:<slot>)" (`m9_oracle_quorum.py:288-289`). That rule is disabled, so no per-slot dissent follows.
- No page and no clean are involved. This is text only.

### Other NOTEs (no missed, false or lost page; no false clean)

- **QA5-N3 (the QA4-F2 raise stalls the whole M11 check; fail-closed and bounded).**
  - While a reorg run cannot re-prove the zero round, every M11 rule pauses: revert-rate, the weekly comparison and the reorg re-find of other keys. This lasts until the API answers or the next round closes.
  - The stall is visible: the health line `Check m11 raised ProofApiError …` is re-alerted every 6 h.
  - The next zero round still pages, and its run commits: `Q -k stall` passes.
  - Nothing is lost; outcomes are delayed.
- **QA5-N4 (D14 ruling wording).** With `zero_round_proof_enabled` false and a proof page open, the page is recorded at warn silently instead of "resolved once".
  - The implementer documents why: the `liveness` incident belongs to the liveness rule, which stays on.
  - The health line "disabled by config" is sent. The operator chose the switch.
  - Owner: coordinator. Confirm or amend the ruling.
- **N1–N5 (carried, unchanged, NOTE).**
  - N1: the second weak commit is not sent individually.
  - N2: M1 incident link (CP3).
  - N3: the weekly comparison takes about 273 s, within the 600 s budget. `compare_leaves`/`state_mismatch` have no diff hunk since `ffc3b27`.
  - N4: `revert:<rotated-out key>` never resolves.
  - N5: a newly activated slot warns absent at once (spec question, Marco).
  - The QA4 file still fails exactly N4 and N5.

## 2. Claims I could NOT confirm / judgements on the implementer's claims

- **"QA4-F2 fixed: a reorg run that discarded the zero-round proof and cannot recompute it raises; page kept, no second page."** True for the path QA4 reported: the reorg ancestor is below the proof block and the commit is unchanged. My own run of `test_qa4_f2_*` gives 6 green. It is **not true in general:** a reorg that moves the commit block bypasses the guard (QA5-F1). The state-table cell "(c) reorg … raise when not recomputable [r4]" therefore overstates the coverage.
- **"QA4-F1 fixed: every updater tx of the round counts, at any time."** Confirmed.
  - Both QA4 reproducers are green.
  - My full-round boundary is green: 1 of 9 = 11.1 % stays open, and 1 of 10 = 10 % resolves once. The resolution is by positive proof, and nothing is re-sent across a restart.
  - A late revert after that resolution re-opens the round once.
  - A round judged clean in its first hour, followed by 3 late reverts of its blockNum, warns. No event is lost.
- **"26 of 31 new/renamed tests RED on the frozen code, 5 GREEN; 29 of 113 sweep cases RED."** Confirmed exactly.
  - Setup: `git archive ffc3b27 reconcile` overlaid with the head's `reconcile/tests`. 31 new `^def test_` names (`comm` against `ffc3b27`).
  - The 5 GREEN are the twins and controls listed in `red_green_repair4.txt`.
- **"Falsifiers repair 4: 18/18."** Confirmed: I re-ran `falsify_repair4.sh` (35 s). Every mutation exits 1 and every restore exits 0, with porcelain `''` 18 times (log `$S/falsify_repair4.qa5.log`).
- **"Saved-dump replays byte-identical to repair 3."** Confirmed for the 2026-08 outage: `cmp` gives identical against both `.repair4` and `.repair3` (89 logs / 21 rounds / 13 commits / 89 messages).
- **The adapted reproducers of earlier attempts** (attempt 1: 2, attempt 3: 1) are unchanged and fail as published. Attempts 2–4 judged them legitimate, and I did not re-judge them because their code paths are unchanged. Attempt 2's only failure is N1.
- **Trigger list.**
  - I re-derived it from spec §3: M9 6 lines / 7 rules, M10 3, M11 5 lines / 6 rules. The monitor-template info lines (every `OracleReplaced`, other-sender updates) are CP1A's.
  - The rows with tests at their severity and benign twins are unchanged from attempt 4's T1–T16 audit, with T14 now spec-faithful (QA4-F1).
  - With D14 off, the switched rules are deliberately inert (owner ruling).
- **Not re-measured, and therefore not confirmed by me:**
  - the 2016-case scratch exploration;
  - the repair 1–3 falsifier re-runs and the original `falsify.sh` five (owner scope; earlier QAs re-ran the repair-1/2 sets);
  - the LIVE M11 weekly comparison (unchanged path, 273 s);
  - the archive re-fetch of the 2026-08 outage (dump unchanged; attempt 2 proved it against the archive).

## 3. Verification matrix

**Preflight:**
- `git rev-parse HEAD` = `48e997d0dd66fe791e11f3152b422517876b20f2` (= dispatch head); porcelain empty at start; 335 GiB free.
- No `reconcile/.venv` existed. `uv venv reconcile/.venv --python 3.12` and `uv pip install --python reconcile/.venv/bin/python -r reconcile/requirements-dev.txt` both exit 0 (Python 3.12.15, pytest 9.1.1, ruff 0.17.1). No other install.
- **Frozen files:** `git diff --stat plan/monitoring-service HEAD -- reconcile/tests/{harness,test_framework,test_worker}.py` → empty (`plan/monitoring-service` = `37617ff`). `git log 96d1c32..HEAD` on those files lists only `500abe4` and `bd531b4`. `git diff 96d1c32 HEAD -- <3 files> | shasum -a 256` = `git diff bd531b4~1 500abe4 -- <3 files> | shasum -a 256` = `cd926cfa…e9ae`.
- **Lane scope:** `git diff --name-only 96d1c32 HEAD -- reconcile` lists only:
  - the 3 frozen files above;
  - `checks/{m9_oracle_common,m9_oracle_quorum,m10_oracle_rotation,m11_updater_liveness}.py`;
  - one import line in `checks/__init__.py`;
  - `config.example.json`;
  - `tests/{fixtures_oracle,test_m9,test_m10,test_m11,test_m9_m10_m11_sweep}.py`.

  **No other frozen CP1B file was edited.** Repair 4 (`ffc3b27..HEAD`) touches only `m11_updater_liveness.py`, `m9_oracle_common.py`, `m9_oracle_quorum.py`, `config.example.json` (3 switch keys, true), 3 test files and `plans/…/workers/cp2b/**` + plan.md. `config/`, `config.zip`, `.env*` untouched.

Rows were run one per pytest by `bash plans/monitoring-service/workers/cp2b/evidence/verify.sh` with `PYTHONPYCACHEPREFIX=$S/pyc` (log `$S/vrows.log`, 10:03Z, 38 s). I read the script first: every row is its own pytest with `tail -1` plus `PIPESTATUS`, so there is no `&&` chain. Porcelain was 0 before and after.

| V-ID | Command | Exit / result | Verdict |
|---|---|---|---|
| V2b.1 (V2x.1) M9 / M10 / M11 | `… reconcile/tests/test_m{9,10,11}.py` | 0 / 0 / 0; **91 / 24 / 114** passed | PASS; trigger coverage §2; QA5-F1 NOTE |
| sweep | `… test_m9_m10_m11_sweep.py` | 0; **113** passed (7.2 s) | PASS |
| V2b.2 (V2x.2) | `-k thresholds_from_chain` per file | 0; 2 / 1 / 1 | PASS |
| V2b.3 (V2x.3) | `-k restart` per file | 0; 3 / 2 / 7 | PASS; my restart controls (revert-rate resolution, switch flips) green |
| V2b.4 (V2x.4) | `-k rpc_budget` per file | 0; 1 / 1 / 1 | PASS |
| V2b.5 (V2x.5) | `falsify_repair4.sh` + own `$S/qfalsify.py` | 18/18 red/green; 4/4 own red/green; porcelain `''` | PASS |
| V2b.6 (V2x.6) suite | `… reconcile/tests` | 0; **582 passed** (19.8 s) | PASS |
| V2b.6 ruff | `reconcile/.venv/bin/ruff check reconcile` | 0; All checks passed! | PASS |
| V2b.7 | `… test_m9.py -k from_the_clock` | 0; **2 passed** | PASS; my control: dissent off, no event, clock-only 9.2 h → `cadence` page (green) |
| qa1 / qa2 / qa3 / qa4 regressions | `-k qa1` (m9/m10/m11), `-k qa2`, `-k qa3` (m9/m11), `-k qa4` (m11) | 0; 8 / 1 / 7; 8 / 12; 7 / 7; 14 | PASS |
| repair-3 / repair-4 rows | `-k r3_` per file; `-k r4_` (m9/m11) | 0; 14 / 1 / 8; 3 / 12 | PASS |
| QA attempt-1 / -2 / -3 / -4 Appendix A as published | extracted by `verify.sh` | 1; **23/2**, **15/1** (N1), **10/1** (adapted control), **9/2** (N4, N5) | as claimed; every QA4-F1/F2 reproducer green |
| RED on frozen `ffc3b27` | `git archive ffc3b27 reconcile` → `$S/frozen` + head tests | 31 new tests: **26 failed / 5 passed**; sweep **29 failed / 84 passed** | as claimed |
| QA5 independent scenarios | `Q` (Appendix A) | 1; **3 failed / 13 passed** | NOTEs only (QA5-F1, QA5-N2) |
| D14 switches (on / off / resolves once / round trip) | `-k r4_` (10 tests) + `Q -k "dissent_off or switch or proof_disabled or typo"` | all green except the QA5-N2 text | PASS: on = unchanged; off = silent + health + an open incident resolved once with "Rule disabled by config"; off→on re-opens a still-true condition once; other page rules (weak-commit, failed-round, cadence, M10 rotation, two consecutive zero rounds) still fire with switches off; a proof-off reorg run with the API down does not stall; misspelled switch keys fail at startup |
| LIVE M9/M10 current rounds (own read) | `python3 -I …/evidence/fetch_oracle_logs.py https://ethereum-rpc.publicnode.com 26239701 26247467 $S/live/recent.json 1000` (head 26,247,467 at 10:10Z; 8 × `eth_getLogs`); one `eth_getLogs` for `OracleReplaced` (topic `0x6113130a…c468`) over the same range on publicnode → `[]`. The first attempt on `https://eth.drpc.org` failed: `HTTP Error 400: Bad Request` on every 1000-block chunk | 0; **12 logs, 3 rounds, 3 commits**: blockNums 26,241,984 (slots 3, 2, 1), 26,244,378 (1, 2, 4), 26,246,774 (3, 2, 4); sha256 `3aa698f5…12bb`. Replay `live_replay.py recent.json 400` (head code): exit 0, **0 incidents, 0 messages, 0 health** | PASS (LIVE, publicnode) |
| LIVE outage replay (saved dump, head code) | `live_replay.py …/outage_2026_08_logs.json 400` | 0; 89 logs / 21 rounds / 13 commits / 89 messages; **byte-identical** to `outage_2026_08_replay.repair4.out.json` and `.repair3` | PASS (dump = archive read of attempt 2) |
| LIVE M11 weekly comparison | not re-run | `compare_leaves` / `state_mismatch` unchanged since `ffc3b27` (no diff hunk); QA3 measured it LIVE (506 compared, 0 unknown, 3 benign mismatches, 273 s) | skipped (owner scope; N3) |

## 4. Falsifiers

**Mine** (`$S/qfalsify.py`, Appendix B). Bytes are restored in `finally`, every process gets a fresh pycache, and `git status --porcelain` is checked after every pair. Log `$S/qfalsify.log`, result `ALL RED/GREEN`.

| ID (class) | Mutation | Test | Mutated / restored | porcelain |
|---|---|---|---|---|
| QF5-m11-f2-guard (2: reorg raise) | `if raw and not saved and (…` → `if False and raw and not saved and (…` | `test_m11.py::test_qa4_f2_reorg_run_with_proof_api_down_keeps_the_zero_round_page` | 1 / 0 | `''` |
| QF5-m11-rate-denominator (9: spec "in a round") | `total = len(rd["ours_all"]) + …` → `len(rd["ours"])` | QA5 `test_m11_revert_rate_full_round_boundary_11_percent_open_10_percent_resolves` | 1 / 0 | `''` |
| QF5-m9-dissent-switch (11: config) | `if self.config["dissent_enabled"]` → `if True` | `test_m9.py::test_r4_dissent_disabled_is_silent_and_says_so` | 1 / 0 | `''` |
| QF5-m10-vote-before-rotation (3: ordering) | `v.pos < ev.pos` → `v.pos > ev.pos` | QA5 `test_m10_rotation_during_open_round_pages_with_reduced_config` | 1 / 0 | `''` |

**Implementer's repair-4 set** (`bash …/evidence/falsify_repair4.sh`, 35 s): R4-F1-window, -blocknum, -older, -position, -drop-guard, -drop, R4-F2-error, -both, -partial, R4-G1, R4-SWEEP-rate, -SWEEP-G1, R4-D14-dissent, -revert, -proof, -resolve-once, R3-RR-lead-r4, R3-RR-attr-r4. Every mutated run exits 1 and every restored run exits 0, with porcelain `''` ×18.

**Not re-run** (owner scope): the repair 1–3 sets and the original five.

**QA5 controls that pass** (spec-derived, outside the implementer's list):
- **M11:**
  - A moved commit with the API up keeps the page and sends nothing.
  - Proof-off reorg run with the API down: no stall.
  - Revert-rate boundary over the full round (11.1 % open, 10 % resolves once).
  - Resolution then restart then a late revert: re-opens once.
  - A clean round's late reverts warn.
  - Off→on re-opens once.
  - The stall is reported and bounded.
- **M9 (dissent off):**
  - Weak-commit and failed-round pages fire.
  - The cadence page fires from the clock.
  - Off→on re-derives the streak page.
- **M10:**
  - Committing vote then rotation in one block: silent.
  - Rotation during an open round pages under the reduced config.
- **Config:** misspelled switch keys fail at startup, and the error names the key.

## 5. Evidence-plane writes

- CP2b **Status:** `IMPLEMENTED — AWAITING QA` (repair 4) → `VERIFIED — PASS WITH NOTES` on frozen head `48e997d0dd66fe791e11f3152b422517876b20f2`, linking this report.
  - NOTEs: QA5-F1, QA5-N2, QA5-N3, QA5-N4; N1–N5 carried.
  - plan §8 "Recorded notes" is outside my write scope; the coordinator should mirror the NOTEs there.
- Wrote `plans/monitoring-service/qa/cp2b-attempt-5.md` (this file) and appended one event `event-cp2b-qa-5` to `plans/monitoring-service/execution-log.md`.
- Local untracked venv `reconcile/.venv` (ignored). Scratch only under `$S`. No test was added to the repo, no implementation file was changed, no next checkpoint was started.

## 6. Repo proof

Measured after the falsifier restorations, the LIVE reads and the final QA5 run, **before** the evidence-plane writes:

```text
$ git status --porcelain
<empty>
$ git diff --name-only 48e997d0dd66fe791e11f3152b422517876b20f2
<empty>
```

The commit of this report changes only `plans/monitoring-service/{plan.md,execution-log.md,qa/cp2b-attempt-5.md}`. **No implementation file appears in either output.**

**Budget.** `verify.sh` ran once (38 s), and the suite (19.8 s) ran once inside it. `falsify_repair4.sh` ran once (35 s), and my 4 falsifiers ran once. The QA5 file ran on the head and on `ffc3b27`. LIVE: 9 `eth_getLogs` and 1 `eth_blockNumber`.

**Skipped, with the reason:**
- mutation testing beyond the named falsifiers (owner scope);
- the repair 1–3 falsifier re-runs and the original five (owner scope; their code paths were re-guarded by repair 4's set);
- the LIVE M11 weekly comparison (273 s, unchanged path, N3);
- the archive re-fetch of the 2026-08 outage (dump unchanged; replay byte-identical);
- the 2016-case seed exploration;
- the CP3 fork scenarios (V3.8, out of scope).

## Appendix A — `$S/tests/test_qa5.py`

<!-- BEGIN test_qa5.py -->
```python
"""CP2b QA attempt 5: independent spec-derived scenarios (QA scratch, not part of the repo).

Expectations come from the spec text (docs/ssv-specs/monitoring-service-spec.md M9-M11), README 6.2,
qa/DEFECT-CLASSES.md and the owner D14 switch ruling, not from the implementation's output.
Run: PYTHONPATH=reconcile:reconcile/tests reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider <this file>
"""
from __future__ import annotations

from fixtures_oracle import (
    UpdaterWorld,
    incidents,
    open_keys,
    oracle_worker,
    restart,
    sent,
    tick,
)

REORG_TEXT = "Evidence left the canonical chain"


def allk(w, check):
    return [(r["ikey"], r["severity"], r["kind"]) for r in sent(w, check)]


def world(tmp_path, **kw):
    u = UpdaterWorld(tmp_path, **kw)
    tick(u.w)
    return u


def sev(w, check, key):
    rows = w.db.conn.execute("SELECT severity, state FROM incidents WHERE check_id=? AND key=?", (check, key)).fetchall()
    return [tuple(r) for r in rows]


def health(w, part):
    return [r[0] for r in w.db.conn.execute("SELECT text FROM outbox WHERE severity='health' AND text LIKE ?",
                                            (f"%{part}%",))]


def _zero_round_page(tmp_path, **kw):
    """Round A with an update, then zero round B whose leaves differ from the chain for cluster 1: page."""
    u = world(tmp_path, confirmations=2, **kw)
    ref_a = u.round(updates=(0,))
    u.close()
    ref_b = u.round(updates=())
    commit_b = u.c.number - 1
    u.set_eb(32, leaves={u.cids[1]: 40})
    u.close()
    assert open_keys(u.w, "m11") == {"liveness": "page"}, (open_keys(u.w, "m11"), allk(u.w, "m11"))
    return u, ref_a, ref_b, commit_b


# ------------------------------------------------------------------------------------------------ M11 zero-round proof
# README 6.2 rule 4 / DEFECT-CLASSES 1+2: an input a reorg run cannot evaluate must raise; an unreachable API is
# UNKNOWN, never a downgrade of what was proven, and never a second page for the same incident later.


def test_m11_reorg_moving_the_zero_rounds_commit_with_api_down_keeps_the_page(tmp_path):
    """A reorg beyond the confirmation window replaces the zero round's commit block; on the new chain the same
    blockNum commits one block later (still no updater tx). The proof API is down in the reorg run. Expected: the page
    is not silently downgraded and no second page is sent for the same incident when the API answers again."""
    u, ref_a, ref_b, commit_b = _zero_round_page(tmp_path)
    n = len(sent(u.w, "m11"))
    u.api.down = True
    u.oc.reorg(commit_b, latest=u.oc.latest)  # drops B's commit and everything after it
    u.ssv.latest_root = ref_a
    u.c.mine(1)
    u.ssv.commit(ref_b)  # the same blockNum, committed one block later on the new chain
    u.api.ref = ref_b
    tick(u.w, u.oc, 320)  # past the new window
    after_reorg = (sev(u.w, "m11", "liveness"), allk(u.w, "m11")[n:], u.w.db.meta("reorg:m11"))
    u.api.down = False
    tick(u.w, u.oc, 3, seconds=300)
    tick(u.w, u.oc, 3, seconds=300)
    msgs = allk(u.w, "m11")[n:]
    assert after_reorg[0] == [("page", "open")] and [m for m in msgs if m[1] == "page"] == [], (after_reorg, msgs)


def test_m11_reorg_moving_the_zero_rounds_commit_with_api_up_sends_nothing_twin(tmp_path):
    """Benign twin: same reorg, the API answers (still mismatching): page kept, nothing sent."""
    u, ref_a, ref_b, commit_b = _zero_round_page(tmp_path)
    n = len(sent(u.w, "m11"))
    u.oc.reorg(commit_b, latest=u.oc.latest)
    u.ssv.latest_root = ref_a
    u.c.mine(1)
    u.ssv.commit(ref_b)
    u.api.ref = ref_b
    tick(u.w, u.oc, 320)
    tick(u.w, u.oc, 3, seconds=300)
    assert sev(u.w, "m11", "liveness") == [("page", "open")] and [
        m for m in allk(u.w, "m11")[n:] if m[1] == "page"] == [], allk(u.w, "m11")[n:]


def test_m11_reorg_moving_the_zero_rounds_commit_with_a_partial_answer_keeps_the_page(tmp_path):
    """Partial variant (class 1): same reorg, the API answers every cluster except the one that differed ("cluster
    not found"): no mismatch proven, one UNKNOWN. Expected: no silent downgrade, no second page later."""
    u, ref_a, ref_b, commit_b = _zero_round_page(tmp_path)
    n = len(sent(u.w, "m11"))
    leaf = u.api.leaves.pop(u.cids[1])
    u.oc.reorg(commit_b, latest=u.oc.latest)
    u.ssv.latest_root = ref_a
    u.c.mine(1)
    u.ssv.commit(ref_b)
    u.api.ref = ref_b
    tick(u.w, u.oc, 320)
    after_reorg = (sev(u.w, "m11", "liveness"), allk(u.w, "m11")[n:])
    u.api.leaves[u.cids[1]] = leaf
    tick(u.w, u.oc, 3, seconds=300)
    msgs = allk(u.w, "m11")[n:]
    assert after_reorg[0] == [("page", "open")] and [m for m in msgs if m[1] == "page"] == [], (after_reorg, msgs)

def test_m11_stall_after_reorg_with_api_down_is_reported_and_bounded(tmp_path):
    """The QA4-F2 raise stalls M11 while the API is down. The stall must be visible (a health message) and bounded:
    the next zero round closing still pages (two consecutive zero rounds), and the stall ends (marker cleared)."""
    u, ref_a, ref_b, commit_b = _zero_round_page(tmp_path)
    u.api.down = True
    u.oc.reorg(u.c.number - 5, latest=u.oc.latest)
    u.c.mine(8)
    tick(u.w)
    assert u.w.db.meta("reorg:m11") is not None
    assert health(u.w, "Check m11 raised"), "no health message for the stalled check"
    u.round(updates=())  # a second zero round while the API is still down
    u.close()
    tick(u.w, u.oc, 3, seconds=300)
    assert u.w.db.meta("reorg:m11") is None and sev(u.w, "m11", "liveness") == [("page", "open")]


def test_m11_zero_round_proof_disabled_reorg_with_api_down_does_not_stall(tmp_path):
    """Switch off (D14 reduced ship): no proof is asked, so a reorg run with the API down must not raise/stall."""
    u = world(tmp_path, confirmations=2, checks={"zero_round_proof_enabled": False, "full_state_interval_s": 10**9})
    u.round(updates=(0,))
    u.close()
    u.round(updates=())
    u.close()
    assert open_keys(u.w, "m11") == {"liveness": "warn"}
    u.api.down = True
    u.oc.reorg(u.c.number - 5, latest=u.oc.latest)
    u.c.mine(8)
    tick(u.w)
    tick(u.w, u.oc, 3, seconds=300)
    assert u.w.db.meta("reorg:m11") is None and open_keys(u.w, "m11") == {"liveness": "warn"}
    u.round(updates=())
    u.close()
    assert open_keys(u.w, "m11") == {"liveness": "page"}


# ------------------------------------------------------------------------------------------------ M11 revert rate
# Spec M11: "Revert rate on our updates > 10 % in a round -> warn" (every tx of the round, QA4-F1).


def test_m11_revert_rate_full_round_boundary_11_percent_open_10_percent_resolves(tmp_path):
    """1 success + 1 revert in the first hour (50 %: warn). Then 7 late successes: 1 of 9 = 11.1 % > 10 %: the warn
    stays open. One more: 1 of 10 = 10 % (not > 10 %): resolved once, by positive proof."""
    u = world(tmp_path, n=11)
    ref = u.round(updates=(0,))
    u.fail("IncorrectClusterState", ref, i=10)
    u.close()
    assert open_keys(u.w, "m11") == {"revert-rate": "warn"}
    n = len(sent(u.w, "m11"))
    for i in range(1, 8):
        u.update(i, eb=33)
    tick(u.w, u.oc, 3, seconds=300)
    assert open_keys(u.w, "m11") == {"revert-rate": "warn"}, allk(u.w, "m11")[n:]
    u.update(8, eb=33)
    tick(u.w, u.oc, 3, seconds=300)
    tick(u.w, u.oc, 3, seconds=300)
    new = allk(u.w, "m11")[n:]
    assert open_keys(u.w, "m11") == {} and [k for _, _, k in new] == ["resolved"], new


def test_m11_revert_rate_late_successes_then_restart_then_late_revert_reopens_once(tmp_path):
    """Restart idempotency + a parameter of the round changing after resolution: resolved at 1 of 10; restart (nothing
    re-sent); then a late revert of the same blockNum: 2 of 11 = 18 % -> the round warns again (once)."""
    u = world(tmp_path, n=12)
    ref = u.round(updates=(0,))
    u.fail("IncorrectClusterState", ref, i=10)
    u.close()
    for i in range(1, 9):
        u.update(i, eb=33)
    tick(u.w, u.oc, 3, seconds=300)
    assert open_keys(u.w, "m11") == {}
    n = len(sent(u.w, "m11"))
    u.w = u.restart()
    tick(u.w, u.oc, 3, seconds=300)
    assert len(sent(u.w, "m11")) == n
    u.fail("IncorrectClusterState", ref, i=11)
    tick(u.w, u.oc, 3, seconds=300)
    tick(u.w, u.oc, 3, seconds=300)
    new = allk(u.w, "m11")[n:]
    assert open_keys(u.w, "m11") == {"revert-rate": "warn"} and [k for _, _, k in new] == ["open"], new


def test_m11_revert_rate_switch_off_then_on_with_condition_still_true(tmp_path):
    """D14 switch round trip (probe): open warn; off -> resolved once; on again while the round is still > 10 % in
    history. Recorded: whether the warn is re-sent (spec: the condition holds)."""
    u = world(tmp_path)
    ref = u.round(updates=(0,))
    u.fail("MustUseLatestRoot", ref, i=1)
    u.close()
    n = len(sent(u.w, "m11"))
    u.cfg["revert_rate_enabled"] = False
    u.w = u.restart()
    tick(u.w, u.oc, 2, seconds=300)
    u.cfg["revert_rate_enabled"] = True
    u.w = u.restart()
    tick(u.w, u.oc, 2, seconds=300)
    new = allk(u.w, "m11")[n:]
    print("ROUNDTRIP", new, open_keys(u.w, "m11"))
    assert [k for _, _, k in new][:1] == ["resolved"]


# ------------------------------------------------------------------------------------------------ M9 with dissent off


def test_m9_dissent_off_keeps_weak_commit_and_failed_round_pages(tmp_path):
    """D14 reduced ship: with checks.m9.dissent_enabled false, the other page rules still fire: a round committed by
    2 slots after quorumBps drops (weak commit -> page) and a proposed round not committed within 30 min (page)."""
    oc, w = oracle_worker(tmp_path, checks={"m9": {"dissent_enabled": False}})
    tick(w)
    oc.round()
    tick(w, oc, 5)
    oc.set_quorum(5000)
    tick(w, oc, 2)
    oc.round(voters=(1, 2))
    tick(w, oc, 2)
    assert open_keys(w, "m9").get("weak-commit") == "page", open_keys(w, "m9")
    tick(w, oc, 20)
    x = oc.c.number - 1
    oc.vote(x, 1)
    tick(w, oc, 160)  # > 30 min
    assert open_keys(w, "m9").get("failed-round") == "page", open_keys(w, "m9")


def test_m9_dissent_off_open_round_split_resolution_text(tmp_path):
    """Probe (text): with dissent off, an open round showing two roots warns `split`; when it commits the split
    resolves saying the dissent 'is attributed per slot (dissent:<slot>)' although that rule is disabled."""
    oc, w = oracle_worker(tmp_path, checks={"m9": {"dissent_enabled": False}})
    tick(w)
    tick(w, oc, 10)
    x = oc.c.number - 1
    oc.vote(x, 1)
    oc.vote(x, 3, variant=1)
    tick(w, oc, 1)
    assert open_keys(w, "m9") == {"split": "warn"}, open_keys(w, "m9")
    oc.vote(x, 2)
    oc.vote(x, 4)
    tick(w, oc, 2)
    res = [r["text"] for r in sent(w, "m9") if r["kind"] == "resolved"]
    print("SPLIT-RESOLVED", res)
    assert open_keys(w, "m9") == {} and res and "dissent:<slot>" not in res[0], res


def test_m9_dissent_off_cadence_still_pages_from_the_clock(tmp_path):
    """V2b.7 under the reduced config: no RootCommitted for > 9 h, clock only, pages once."""
    oc, w = oracle_worker(tmp_path, confirmations=0, checks={"m9": {"dissent_enabled": False}})
    tick(w)
    oc.round()
    tick(w, oc, 5)
    tick(w, seconds=9.2 * 3600)
    assert open_keys(w, "m9") == {"cadence": "page"}, open_keys(w, "m9")


def test_m9_dissent_streak_page_then_switch_off_on_with_streak_still_in_history(tmp_path):
    """Probe: a dissent page, switch off (resolved once), switch on again while the streak is still in history and
    slot 3 keeps dissenting: recorded whether the page comes back (the condition still holds)."""
    oc, w = oracle_worker(tmp_path)
    tick(w)
    for _ in range(3):
        oc.round(variants={3: 1})
        tick(w, oc, 1)
    assert open_keys(w, "m9") == {"dissent:3": "page"}
    n = len(sent(w, "m9"))
    w = restart(tmp_path, w, oc, checks={"m9": {"dissent_enabled": False}})
    tick(w, oc, 2, seconds=60)
    w = restart(tmp_path, w, oc, checks={"m9": {"dissent_enabled": True}})
    tick(w, oc, 2, seconds=60)
    oc.round(variants={3: 1})
    tick(w, oc, 2, seconds=60)
    new = allk(w, "m9")[n:]
    print("DISSENT-ROUNDTRIP", new, open_keys(w, "m9"))
    assert new and new[0][2] == "resolved"


# ------------------------------------------------------------------------------------------------ M10


def test_m10_commit_then_rotation_in_the_same_block_is_silent(tmp_path):
    """Same-block ordering: the committing vote and then OracleReplaced in one block: the round closed before the
    rotation -> no page (rotation between rounds)."""
    oc, w = oracle_worker(tmp_path, ids=("m10",))
    tick(w)
    tick(w, oc, 10)
    x = oc.c.number - 1
    oc.vote(x, 1)
    oc.vote(x, 2)
    oc.vote(x, 3, mine=False)  # commits (3 of 4)
    oc.replace(4, "0x" + "44" * 20)
    tick(w, oc, 2)
    assert open_keys(w, "m10") == {} and allk(w, "m10") == [], allk(w, "m10")


def test_m10_rotation_during_open_round_pages_with_reduced_config(tmp_path):
    """M10 is not switched by D14: OracleReplaced while a round is open pages."""
    oc, w = oracle_worker(tmp_path, ids=("m10",), checks={"m9": {"dissent_enabled": False}})
    tick(w)
    tick(w, oc, 10)
    x = oc.c.number - 1
    oc.vote(x, 1)
    oc.replace(2, "0x" + "22" * 20)
    tick(w, oc, 2)
    assert list(open_keys(w, "m10").values()) == ["page"], open_keys(w, "m10")


def test_m11_clean_round_judged_before_its_late_reverts_then_reverts_arrive(tmp_path):
    """Probe (class 4/5): round N warns (1 of 2); round N+1 is clean in its first hour (3 of 3) -> N resolved by a
    later clean round. Then 3 late reverts of N+1's blockNum arrive (3 of 6 = 50 %). Expected: N+1 warns (no event
    lost); recorded whether N re-opens."""
    u = world(tmp_path, n=8)
    ref = u.round(updates=(0,))
    u.fail("MustUseLatestRoot", ref, i=1)
    u.close()
    assert open_keys(u.w, "m11") == {"revert-rate": "warn"}
    ref2 = u.round(updates=(2, 3, 4))
    u.close()
    n = len(sent(u.w, "m11"))
    assert open_keys(u.w, "m11") == {}, allk(u.w, "m11")
    for i in (5, 6, 7):
        u.fail("MustUseLatestRoot", ref2, i=i)
    tick(u.w, u.oc, 3, seconds=300)
    tick(u.w, u.oc, 3, seconds=300)
    new = allk(u.w, "m11")[n:]
    print("LATE-REVERTS", new, open_keys(u.w, "m11"))
    assert open_keys(u.w, "m11").get("revert-rate") == "warn", new


def test_switch_key_typo_fails_loud_at_startup(tmp_path):
    """Class 11: a misspelled D14 switch must not leave a rule silently on/off: startup error."""
    import pytest
    from fixtures_oracle import VIEWS
    from harness import make_worker

    for ids, cfg in ((["m9"], {"m9": {"dissent_enable": False}}), (["m11"], {"m11": {"revert_rate_enable": False}}),
                     (["m11"], {"m11": {"zero_round_proof": False}})):
        inbox = tmp_path / "inbox.jsonl"
        inbox.write_text("")
        with pytest.raises((SystemExit, ValueError, KeyError)) as exc:
            make_worker(tmp_path / ids[0], only=ids, views=VIEWS, checks=cfg, inbox={"path": str(inbox)})
        assert list(cfg[ids[0]])[0] in str(exc.value), exc.value
```
<!-- END test_qa5.py -->

## Appendix B — `$S/qfalsify.py` (run from the worktree root: `reconcile/.venv/bin/python $S/qfalsify.py $S/tests/test_qa5.py`)

```python
"""QA attempt 5 falsifiers: one-token mutation, run the named test (expect exit 1), restore bytes, re-run (expect 0),
git status --porcelain must be empty after each pair. Run from the worktree root with argv[1] = the QA5 test file."""
import os, subprocess, sys, tempfile
from pathlib import Path

PY = "reconcile/.venv/bin/python"
QA5 = sys.argv[1]
CASES = [
    ("QF5-m11-f2-guard (reorg-run raise disabled)", "reconcile/checks/m11_updater_liveness.py",
     "        if raw and not saved and (result.get(\"error\")", "        if False and raw and not saved and (result.get(\"error\")",
     ["reconcile/tests/test_m11.py::test_qa4_f2_reorg_run_with_proof_api_down_keeps_the_zero_round_page"]),
    ("QF5-m11-rate-denominator (all-round successes -> windowed)", "reconcile/checks/m11_updater_liveness.py",
     'total = len(rd["ours_all"]) + len(counted)', 'total = len(rd["ours"]) + len(counted)',
     [QA5 + "::test_m11_revert_rate_full_round_boundary_11_percent_open_10_percent_resolves"]),
    ("QF5-m9-dissent-switch (switch ignored)", "reconcile/checks/m9_oracle_quorum.py",
     '(self.dissent(states) if self.config["dissent_enabled"]', '(self.dissent(states) if True',
     ["reconcile/tests/test_m9.py::test_r4_dissent_disabled_is_silent_and_says_so"]),
    ("QF5-m10-vote-before-rotation (< -> >)", "reconcile/checks/m10_oracle_rotation.py",
     "before = [v for v in rnd.votes if v.pos < ev.pos]", "before = [v for v in rnd.votes if v.pos > ev.pos]",
     [QA5 + "::test_m10_rotation_during_open_round_pages_with_reduced_config"]),
]


def run(tests):
    env = dict(os.environ, PYTHONPATH="reconcile:reconcile/tests", PYTHONPYCACHEPREFIX=tempfile.mkdtemp())
    p = subprocess.run([PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests], env=env, capture_output=True, text=True)
    return p.returncode, (p.stdout.strip().splitlines() or [""])[-1]


ok = True
for name, path, old, new, tests in CASES:
    f = Path(path)
    orig = f.read_bytes()
    if orig.decode().count(old) != 1:
        print(f"{name}: anchor found {orig.decode().count(old)} times"); ok = False; continue
    try:
        f.write_text(orig.decode().replace(old, new))
        rc_m, out_m = run(tests)
    finally:
        f.write_bytes(orig)
    rc_r, out_r = run(tests)
    porc = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout
    print(f"{name}\n  mutated exit {rc_m} ({out_m})\n  restored exit {rc_r} ({out_r})\n  porcelain {porc!r}")
    ok &= rc_m == 1 and rc_r == 0 and porc == ""
print("ALL RED/GREEN" if ok else "FALSIFIER FAILURE")
sys.exit(0 if ok else 1)
```
