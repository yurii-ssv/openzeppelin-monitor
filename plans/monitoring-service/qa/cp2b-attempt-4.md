FAIL

Independent QA of CP2b (M9, M10, M11), attempt 4 (after repair 3). Frozen head under test: `52b1ae66565c78229ad097b05c1391990a88cdc7` (lane branch `mtabasco/ms-cp2b`, code head `a17872d`). QA worktree `ms-qa-cp2b-4`, branch `mtabasco/ms-qa-cp2b-4`. Task `task_ca0b592245b7`, dispatch `ctx_6791b388d5cc`. QA model: Claude Opus 5.5, effort high (D13). Measured 2026-10-22 08:45–09:10 UTC. **Capped round:** each finding is BLOCKING or NOTE, and any BLOCKING finding means FAIL. Owner scope note applied: no mutation campaign, no long checks (§6 lists what I skipped). No implementation repair. Scratch is under `/tmp/qa-cp2b-4`.

**Summary.** Every supplied row reproduces exactly: M9 88, M10 24, M11 87, sweep 109, suite 548, ruff clean, V2b.7 2.
- The repair-3 regressions are genuine. On the frozen code `8bd225e`, 28 of the 35 new tests are RED, and 73 of 109 sweep cases fail.
- QA3-F1 and QA3-F2 are fixed: their reproducers are green.
- The one attempt-3 control that still fails is a legitimate adaptation to coordinator ruling A.
- A LIVE re-read of the current mainnet rounds is silent.

My own spec-derived scenarios (11 cases) find **two BLOCKING defects** in M11. Both exist in the same form on `8bd225e`, so they are not regressions of repair 3. They also find two NOTEs in M9.
- **QA4-F1 (BLOCKING):** the M11 revert rate is computed only over the first 60 min after the commit. A revert or success carrying the round's own `blockNum` that lands later in the round counts in no round. The result is a missed warn (3 of 5 reverted → silent) and a false warn (1 of 11 reverted → "50 %" warn).
- **QA4-F2 (BLOCKING):** a reorg run while the proof API is down silently downgrades an open zero-round **page** to warn. The next run re-escalates it from the proof result the reorg run had discarded, and sends a second `🚨 [PAGE]` for the same incident.

## 1. Findings (ranked)

Reproducer file: `/tmp/qa-cp2b-4/tests/test_qa4.py`, embedded verbatim in Appendix A (extract it with the BEGIN/END split used by `workers/cp2b/evidence/verify.sh`). `Q` = `PYTHONPATH=reconcile:reconcile/tests reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider /tmp/qa-cp2b-4/tests/test_qa4.py`.
- Whole file on the head: **exit 1, 5 failed / 6 passed**. The 5 failures are QA4-F1 (2 tests), QA4-F2 (1), N4 (1) and N5 (1).
- Same file on the frozen code `8bd225e` (`git archive` + head lane tests): **5 failed / 6 passed**, i.e. the same defects predate repair 3.

### QA4-F1 — BLOCKING (missed warn and false warn; classes 9 and 4): the M11 revert rate counts only the first 60 min of a round

`Q -k "after_60_min or late_successful"` → exit 1, 2 failed.

**Spec M11:** "Revert rate on our updates > 10 % in a round → **warn**." The 60 min window belongs to the line before it ("0 `updateClusterBalance` txs from `0x16A5…` **within 60 min** → warn"). The revert-rate line has no window. An update that carries the round's own `blockNum` and lands before the next `RootCommitted` is "our update in this round" under any reading.

**Code:**
- `revert_rate` attributes an inbox revert only to a round whose window holds it: `rd["start"] <= at[r["block"]] <= rd["end"]` with `end = commit time + update_window_s` (`m11_updater_liveness.py:361-367`).
- The denominator `ours` likewise counts only successful updates inside the same window (`round_stats`, `:262-264`).
- After the repair-3 `blockNum` filter (`:363-365`), a revert whose decoded `blockNum` is a round whose window has closed is counted nowhere.
- The implementer pinned this as a benign twin: `test_a_revert_after_the_window_does_not_count`. That is a 2-transaction round, 1 success + 1 revert = 50 %, kept silent.

**Scenario A (missed warn):** two successful updates right after the commit; then three updates for the same root revert 61–62 min after the commit, before the next root.
- Expected: 3 of 5 = 60 % → warn.
- Actual: no incident, no message (`{} []`).

**Scenario B (false warn):** one success and one revert in the first hour; then nine more successful updates for the same root after 61 min (a slow updater, still the same round).
- Expected: 1 of 11 = 9.1 % ≤ 10 % → silent.
- Actual: `⚠️ [WARN] EB updater revert rate 50% for blockNum 1110` opens.

Both outcomes are printed by `/tmp/qa-cp2b-4/probe/show_f1.py`.

**Why BLOCKING:** under the round policy, a missed alert and a false alert are both BLOCKING. A late, failing updater is exactly the degraded state this rule exists to catch: the liveness rule only covers the case where *no* update lands in the first hour. A spec-faithful rule would count every updater transaction of the round, for example by decoded `blockNum`, falling back to [commit, next commit) when the input does not decode.

### QA4-F2 — BLOCKING (duplicated page plus a silent severity downgrade; classes 2 and 1): a reorg run while the proof API is down downgrades a zero-round page

`Q -k reorg_run_with_proof_api_down` → exit 1. The control `…non_reorg_run_with_proof_api_down…` passes.

**Setup:**
1. A zero-update round pages because the proof API showed a leaf mismatch (`liveness` = page).
2. A reorg 5 deep, beyond `confirmations` 2, replaces only empty blocks *after* the evaluation. It does not touch the round, its commit or any update.
3. The proof API is down during that reorg run.

**Expected:**
- README §6.2 and DEFECT-CLASSES class 2: "any input you cannot evaluate in a reorg run must raise (rollback, marker kept, retry)".
- Class 1: an unreachable API is UNKNOWN, never a downgrade of what was proven.
- So the page stays a page, and no message is sent.

**Actual** (`/tmp/qa-cp2b-4/probe/show_f3c.py`, Appendix B):

```text
API back=False: reorg run -> [('warn', 'open')]; next interval run -> [('page', 'open')]
   sent: page escalate 🚨 [PAGE] EB updater sent 0 updates for 1 committed round(s) — test
API back=True: reorg run -> [('warn', 'open')]; next interval run -> [('page', 'open')]
   sent: page escalate 🚨 [PAGE] EB updater sent 0 updates for 1 committed round(s) — test
```

The reorg run silently stores the incident as **warn**: no message, and the re-alert cadence becomes 24 h. The next interval run re-escalates it and sends a **second page** for the same incident, whether or not the API has recovered.

**Cause:**
1. `zero_round_proof` (`m11_updater_liveness.py:454-466`) drops the persisted proof via `fresh()`, because the reorg ancestor is below the block where it was evaluated.
2. `compare_leaves` then returns `error`, and `return saved["result"] if saved else result` hands `_zero` an error result. `_zero` reports the episode at `warn` (`:303-309`).
3. `Alerts.apply` stores a lower severity without a message (`alerts.py:149-158`).
4. The discarded `check:m11:zero_proof` is never deleted or replaced on that path. The next non-reorg run reads it as fresh, returns its mismatch and escalates again.

The reorg run treated the persisted proof as untrustworthy, but every later run trusts it, so the reorg rule for that key is applied only for the one run.

**Scope:**
- Needs a detected reorg whose ancestor is below the zero round's evaluation block, together with a proof API outage.
- With `confirmations` 12 every detected reorg is beyond the window, so this is narrow on mainnet. It is still exactly the class-2 attack the brief requires: depth beyond the confirmation window, and an input that cannot be evaluated in the reorg run.
- The same outcome follows from a partial API answer that omits the mismatching cluster in that run.

### NOTEs

- **N4 (NOTE: no missed or false alert; an incident-lifecycle gap).** `revert:<address>` for a **rotated-out** slot key never resolves.
  - Scenario: slot 4 is rotated to a new key, and the old key's process reverts `NotOracle` once. The warn is correct (a former slot address).
  - After that, the warn re-alerts every 24 h indefinitely. The only heal is "that address's next successful vote" (`m9_oracle_quorum.py:441-453`), and a rotated-out key can never vote again.
  - Reproducer: `Q -k rotated_out_key`. After 8 normal rounds the key is still open.
  - Owner: repair / CP3, e.g. heal on the rotation away or after N clean rounds.
- **N5 (NOTE: spec ambiguity; literal text satisfied; owner Marco).** A slot that has just been **activated** warns `absent:<slot>` at once ("has not voted the committed root in 7 rounds") for rounds before it existed.
  - Reproducer: `Q -k newly_activated`. Slots 1–3 run 7 rounds, then slot 4 is added to `active_oracle_ids`: an immediate `absent:4` warn.
  - The spec says "a slot that has not voted … in any of the last 6 rounds". Whether rounds before activation count is Marco's call; it is in the same family as the recorded `absent:3`-for-a-dissenter question.
- **N1–N3 (carried, NOTE, unchanged).**
  - N1: the second weak commit is not sent as its own message. The attempt-2 file still fails only `test_m9_second_weak_commit_is_sent`.
  - N2: no M1 incident link, only `evidence.m1_handoff` (CP3).
  - N3: the weekly comparison takes about 273 s, bounded by `proof_api_run_budget_s`. It is not re-measured: the `compare_leaves` path is unchanged since `8bd225e` (no diff hunk in it).
- The accepted coordinator ruling A (a total chain halt with confirmations > 0 makes the clock rules wait) is not a finding.

## 2. Claims I could NOT confirm / judgements on the implementer's claims

- **"Every spec trigger line has a test at its severity and a benign twin."** I derived the trigger list myself from spec §3 M9 (6 trigger lines, 7 rules), M10 (3) and M11 (5 lines, 6 rules) and got the same 16 rows as T1–T16. T10 and T15 belong to the Monitor/CP1A templates and are not credited here. The claim does **not** hold in substance for T14 (revert rate):
  - The benign twin `test_a_revert_after_the_window_does_not_count` encodes the plan shorthand ("within 60 min"), not the spec line (QA4-F1).
  - The reorg cell of the `zero_proof` row in the state table ("discarded … then recomputed on the new chain") is not true when the recompute fails (QA4-F2).
- **"QA3-F1, QA3-F2 fixed."** Confirmed:
  - `-k qa3` gives 7 (M9) and 7 (M11) green.
  - The attempt-3 file as published runs **10 passed / 1 failed**, and all four QA3 reproducers pass.
- **The ADAPTED attempt-3 control** (`test_m9_cadence_page_resolved_by_a_reorg_that_adds_the_commit_is_not_reopened`): **the adaptation is legitimate, no defect is hidden.**
  - It fails at its first assertion (`/tmp/qa-cp2b-4/a3/test_qa3.py:180`, `{} == {'cadence': 'page'}`). Its 9.5 h gap is a clock-only halt with two unconfirmed blocks at confirmations 2: exactly the case coordinator ruling A defines as "waits".
  - Its intent (a deep reorg adding a commit resolves the cadence page; a restart re-sends nothing) is preserved by `test_r3_qa3_control_…` with the gap in chain time. That test is green.
  - In the original, too, the replacement chain's blocks are stamped after the halt, so neither version places a commit "inside" the gap.
- **"28 of 35 new tests RED on the frozen code, 7 GREEN; 73 of 109 sweep cases RED."** Confirmed:
  - `comm` of `^def test_` names between `8bd225e` and the head gives 36 new names: the 35 plus the adapted control.
  - On `git archive 8bd225e reconcile` + head lane tests: **28 failed / 8 passed**. The 8 GREEN are the 7 named in `red_green_repair3.txt` plus the adapted control.
  - Lane files on frozen: 29 failed / 170 passed (28 + the changed `test_rpc_budget_no_eth_call_per_sweep_with_350_clusters`).
  - Sweep on frozen: **73 failed / 36 passed**. Total 102 / 206, as claimed.
- **Attempt-1 / attempt-2 files as published:** 23 / 2 and 15 / 1, as claimed. The two attempt-1 failures are the pre-F6 setups judged legitimate in attempts 2 and 3; the attempt-2 failure is N1.
- **Not re-measured, and therefore not confirmed by me:**
  - the implementer's 18 + 18 + 15 falsifier runs (owner scope note);
  - the 700 + 1200-seed exploration;
  - the LIVE M11 weekly comparison (unchanged code path, 273 s);
  - the 827 s archive re-read of the 2026-08 outage (dump unchanged since `ea8d720`; attempt 2 proved it against the archive).

## 3. Verification matrix

**Preflight:**
- `git rev-parse HEAD` = `52b1ae66565c78229ad097b05c1391990a88cdc7` (= dispatch head); porcelain empty at start; `df -h .` 337 GiB free.
- No `reconcile/.venv` existed: `uv venv reconcile/.venv --python 3.12` and `uv pip install --python reconcile/.venv/bin/python -r reconcile/requirements-dev.txt` exit 0 (Python 3.12.15, pytest 9.1.1, ruff 0.17.1). No other install.
- **Frozen files:** `git diff --stat plan/monitoring-service HEAD -- reconcile/tests/{harness,test_framework,test_worker}.py` → empty (`plan/monitoring-service` = `e6b0024`). `git log 96d1c32..HEAD` on those files lists only `500abe4` and `bd531b4`, and the patch is byte-identical: `git diff 96d1c32 HEAD -- <3 files> | sha256` = `git diff bd531b4~1 500abe4 -- <3 files> | sha256` = `cd926cfa…e9ae`.
- **Lane scope:** `git diff --name-only 96d1c32 HEAD -- reconcile` lists only:
  - the three frozen files above;
  - `checks/{m9_oracle_common,m9_oracle_quorum,m10_oracle_rotation,m11_updater_liveness}.py`;
  - one import line in `checks/__init__.py`;
  - `config.example.json` (`checks.m9/m10/m11` objects);
  - `tests/{fixtures_oracle,test_m9,test_m10,test_m11,test_m9_m10_m11_sweep}.py`.

  **No other frozen CP1B file was edited.** `config/`, `config.zip`, `.env*` untouched.

Rows were run one by one by `/tmp/qa-cp2b-4/vrows.sh` (Appendix D; log `/tmp/qa-cp2b-4/vrows.log`, 08:50:51–08:51:31Z). Porcelain was 0 before and after.

| V-ID | Command | Exit / result | Verdict |
|---|---|---|---|
| V2b.1 (V2x.1) M9 / M10 / M11 | `… reconcile/tests/test_m{9,10,11}.py` | 0 / 0 / 0; **88 / 24 / 87** passed | supplied PASS; trigger coverage: QA4-F1 against T14 |
| sweep | `… test_m9_m10_m11_sweep.py` | 0; **109** passed (7.4 s) | PASS |
| V2b.2 (V2x.2) | `-k thresholds_from_chain` per file | 0; 2 / 1 / 1 | PASS |
| V2b.3 (V2x.3) | `-k restart` per file | 0; 3 / 2 / 6 | PASS; QA4 M9 and M10 restart controls green |
| V2b.4 (V2x.4) | `-k rpc_budget` per file | 0; 1 / 1 / 1 | PASS |
| V2b.5 (V2x.5) | falsifiers, §4 | 3 own red/green; implementer sets not re-run (owner scope) | PASS for what was run |
| V2b.6 (V2x.6) suite | `… reconcile/tests` | 0; **548 passed** (22.5 s) | PASS |
| V2b.6 ruff | `reconcile/.venv/bin/ruff check --no-cache reconcile` | 0; All checks passed! | PASS |
| V2b.7 | `… test_m9.py -k from_the_clock -v` | 0; **2 passed** | PASS; my control (no commit since start, restart at 8 h, clock-only jump to 9.5 h at confirmations 0 → one page; restart → nothing; commit → one resolution) green |
| qa1 / qa2 / qa3 regressions | `-k qa1` (m9/m10/m11), `-k qa2`, `-k qa3` (m9/m11) | 0; 8 / 1 / 7; 8 / 12; 7 / 7 | PASS |
| repair-3 rows | `-k r3_` per file | 0; 14 / 1 / 8 | PASS |
| Attempt-1 / -2 / -3 Appendix A as published | extracted to `/tmp/qa-cp2b-4/a{1,2,3}/` | 1; **23/2**, **15/1** (N1), **10/1** (adapted control) | as claimed (§2) |
| RED on frozen `8bd225e` | `git archive 8bd225e reconcile` → `/tmp/qa-cp2b-4/frozen` + head lane tests and `fixtures_oracle.py` | new tests **28 failed / 8 passed**; lane files 29 / 170; sweep **73 / 36** | as claimed |
| QA4 independent scenarios | `Q` (Appendix A) | 1; **5 failed / 6 passed** (same on `8bd225e`) | **FAIL** (QA4-F1, QA4-F2; N4, N5) |
| LIVE M9/M10 current rounds (own read) | `python3 -I …/evidence/fetch_oracle_logs.py https://ethereum-rpc.publicnode.com 26239701 26247101 /tmp/qa-cp2b-4/live/recent.json 1000` (head 26,247,121 at 09:00:51Z; 8 × 1000-block `eth_getLogs`), plus one `eth_getLogs` for `OracleReplaced` over the same range on publicnode → `[]` | 0; **12 logs, 3 rounds, 3 commits**: blockNums 26,241,984 (slots 3, 2, 1), 26,244,378 (1, 2, 4), 26,246,774 (3, 2, 4); the fourth vote skipped each time; sha256 `2d0c160a…48d2`. Replay `live_replay.py recent.json 400` (head code) exit 0: **0 incidents, 0 messages, 0 health** for M9 and M10 | PASS (LIVE, publicnode) |
| LIVE outage replay (saved dump, head code) | `live_replay.py …/evidence/outage_2026_08_logs.json 400` | 0; 89 logs / 21 rounds / 13 commits / 89 messages. Failed-round page resolved; cadence page resolved; `dissent:3` page open; `absent:3` warn open; split warns resolved. Output **byte-identical** to `outage_2026_08_replay.repair3.out.json` | PASS (dump = archive read of attempt 2) |
| LIVE M11 weekly comparison | not re-run | `compare_leaves` unchanged since `8bd225e`; QA3 measured it LIVE (506 compared, 0 unknown, 3 benign mismatches, 273 s) | skipped (owner scope; N3) |

## 4. Falsifiers

**Mine** (`/tmp/qa-cp2b-4/qfalsify.py`, Appendix C). Bytes are restored in `finally`, each process gets a fresh pycache, and `git status --porcelain` is checked after every pair. Log `/tmp/qa-cp2b-4/qfalsify.log`, result `ALL RED/GREEN`.

| ID (class) | Mutation | Test | Mutated / restored | porcelain |
|---|---|---|---|---|
| QF4-m9-absent-count (spec "6 rounds") | `m9_oracle_quorum.py` `if len(missed) == k:` → `== k + 1` | `test_m9.py::test_slot_absent_from_six_committed_rounds_warns` | 1 / 0 | `''` |
| QF4-m10-same-block (3: same-block order) | `m10_oracle_rotation.py` `v.pos < ev.pos` → `v.block < ev.block` | QA4 `test_m10_rotation_after_the_first_vote_in_the_same_block_pages` | 1 / 0 | `''` |
| QF4-m11-revert-boundary (`>` vs `>=` at 10 %) | `m11_updater_liveness.py` `rate <= self.config["revert_rate"]` → `rate <` | `test_m11.py::test_benign_revert_rate_at_10_percent_is_silent` | 1 / 0 | `''` |

**Implementer's sets** (`falsify_repair1.sh`, `falsify_repair2.sh`, `falsify_repair3.sh`, the original 5 in `falsify.sh`): **not re-run.** The owner scope note for this attempt says "do not re-run the implementer's falsifier campaign". QA3 re-ran the repair-2 and repair-1 sets red/green, and the repair-3 log is `evidence/falsifiers_repair3.log` (18/18, claimed, not re-measured).

**QA4 controls that pass** (spec-derived, outside the implementer's list):
- **M11:** a round whose every updater transaction reverts gives liveness warn **and** a 100 % revert-rate warn. A proof-API outage with no reorg keeps the zero-round page and sends nothing.
- **M9:**
  - V2b.7 from the clock with no commit since start, across two restarts.
  - Same-block dissent then commit (`dissent:3` warn). A depth-1 reorg of exactly that block onto a unanimous round resolves it with the reorg text, and it is never re-sent.
- **M10:**
  - A vote and then `OracleReplaced` in the same block pages (the reverse order is QA3's silent control).
  - A rotation page survives a restart without a re-send and resolves exactly once.

## 5. Evidence-plane writes

- CP2b **Status:** `IMPLEMENTED — AWAITING QA — repair 3` → `QA FAILED` on frozen head `52b1ae66565c78229ad097b05c1391990a88cdc7`, linking this report (QA4-F1, QA4-F2 BLOCKING; N4, N5 new NOTEs; N1–N3 carried).
- Wrote `plans/monitoring-service/qa/cp2b-attempt-4.md` (this file) and appended one event `event-cp2b-qa-4` to `plans/monitoring-service/execution-log.md`.
- Local untracked venv `reconcile/.venv` (ignored). Scratch only under `/tmp/qa-cp2b-4`. No test was added to the repo, no implementation file was changed, no next checkpoint was started.

## 6. Repo proof

Measured after the falsifier restorations, the LIVE reads and the final QA4 run, **before** the evidence-plane writes:

```text
$ git status --porcelain
<empty>
$ git diff --name-only 52b1ae66565c78229ad097b05c1391990a88cdc7
<empty>
```

The commit of this report changes only `plans/monitoring-service/{plan.md,execution-log.md,qa/cp2b-attempt-4.md}`. **No implementation file appears in either output.**

**Budget.** Each V row ran once, and the suite (22.5 s) ran once. The QA4 file ran on the head and on `8bd225e`. Falsifiers: 3 own, run twice (re-run after the N5 test was made non-vacuous).

**Skipped, with the reason:**
- the implementer's falsifier campaign (owner scope note);
- the LIVE M11 weekly comparison (273 s; `compare_leaves` unchanged since `8bd225e`; N3);
- the 827 s archive re-read of the 2026-08 outage (dump unchanged since `ea8d720`; the head replay is byte-identical to repair 3's);
- the wider seed exploration;
- mutation testing;
- the CP3 fork scenarios (V3.8, out of scope).

## Appendix A — `/tmp/qa-cp2b-4/tests/test_qa4.py`

<!-- BEGIN test_qa4.py -->
```python
"""CP2b QA attempt 4: independent spec-derived scenarios (QA scratch, not part of the repo).

Expectations come from the spec text (docs/ssv-specs/monitoring-service-spec.md M9-M11), README 6.2 and
qa/DEFECT-CLASSES.md, not from the implementation's output.
Run: PYTHONPATH=reconcile:reconcile/tests reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider <this file>
"""
from __future__ import annotations

from fixtures_oracle import (
    SLOTS,
    UpdaterWorld,
    failed_commit_root,
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


def sev(w, check, key):
    rows = w.db.conn.execute("SELECT severity, state FROM incidents WHERE check_id=? AND key=?", (check, key)).fetchall()
    return [tuple(r) for r in rows]


# ------------------------------------------------------------------------------------------------ M11 revert rate
# Spec M11: "Revert rate on our updates > 10 % in a round -> warn." The 60 min window belongs to the liveness line
# ("0 updateClusterBalance txs ... within 60 min"), not to the revert-rate line (class 9: plan shorthand).


def test_m11_reverts_of_the_rounds_own_blocknum_after_60_min_count_in_the_round(tmp_path):
    """Two successful updates right after the commit, then three updates for the same root revert 61-70 min after
    the commit (before the next root). Our updates in this round: 5, reverted 3 (60 %) -> warn."""
    u = world(tmp_path)
    ref = u.round(updates=(0, 1))
    u.c.elapse(305)  # 61 min after the commit, no new root
    for i in range(3):
        u.fail("IncorrectClusterState", ref, i=i)
    u.close()
    assert open_keys(u.w, "m11").get("revert-rate") == "warn", (open_keys(u.w, "m11"), pw(u.w, "m11"))


def test_m11_late_successful_updates_count_in_the_denominator(tmp_path):
    """One success and one revert in the first hour, then nine more successful updates for the same root after
    61 min (a slow updater, still the same round). Our updates in this round: 11, reverted 1 (9.1 % <= 10 %):
    silent per spec."""
    u = world(tmp_path, n=11)
    ref = u.round(updates=(0,))
    u.fail("IncorrectClusterState", ref, i=10)
    u.c.elapse(305)
    for i in range(1, 10):
        u.update(i, eb=33)
    u.close()
    assert "revert-rate" not in open_keys(u.w, "m11") and not [p for p in pw(u.w, "m11") if p[0] == "revert-rate"], (
        open_keys(u.w, "m11"), pw(u.w, "m11"))


def test_m11_round_with_only_reverted_updates_warns_liveness_and_revert_rate(tmp_path):
    """Control (spec, 'a round with no updater tx'): every updater tx of the round reverts: 0 successful updates
    within 60 min (warn) and a 100 % revert rate (warn)."""
    u = world(tmp_path)
    ref = u.round(updates=())
    u.fail("IncorrectClusterState", ref, i=0)
    u.fail("IncorrectClusterState", ref, i=1)
    u.close()
    keys = open_keys(u.w, "m11")
    assert keys.get("liveness") in ("warn", "page") and keys.get("revert-rate") == "warn", (keys, pw(u.w, "m11"))


# ------------------------------------------------------------------------------------------------ M11 zero-round proof
# Class 2 (README 6.2): "Any input you cannot evaluate in a reorg run must raise (rollback, marker kept, retry each
# step)". Class 1: an unreachable API is UNKNOWN, never a downgrade of what was proven.


def _zero_round_page(tmp_path):
    u = world(tmp_path, confirmations=2)
    u.round(updates=(0,))
    u.close()
    u.round(updates=())
    u.set_eb(32, leaves={u.cids[1]: 40})  # the zero round's leaves: cluster 1 differs from the chain
    u.close()
    assert open_keys(u.w, "m11") == {"liveness": "page"}, (open_keys(u.w, "m11"), pw(u.w, "m11"))
    return u


def test_m11_reorg_run_with_proof_api_down_keeps_the_zero_round_page(tmp_path):
    """A zero round paged because the proof API showed a leaf mismatch. A reorg beyond the confirmation depth that
    does NOT touch the round (only empty blocks after the evaluation are replaced) happens while the proof API is
    down. Expected: the page stays a page (nothing new was proven or disproven), no message; when the API is back,
    no second page is sent for the same incident."""
    u = _zero_round_page(tmp_path)
    n = len(sent(u.w, "m11"))
    fork = u.c.number - 5  # below the block the proof comparison was made at (checkpoint = head - 2)
    u.api.down = True
    u.oc.reorg(fork, latest=u.oc.latest)
    u.c.mine(8)
    tick(u.w)
    after_reorg = (sev(u.w, "m11", "liveness"), allk(u.w, "m11")[n:])
    u.api.down = False
    tick(u.w, u.oc, 3)
    tick(u.w, u.oc, 3)
    msgs = allk(u.w, "m11")[n:]
    assert after_reorg[0] == [("page", "open")] and msgs == [], (after_reorg, msgs)


def test_m11_non_reorg_run_with_proof_api_down_keeps_the_zero_round_page_control(tmp_path):
    """Control: the same API outage without a reorg keeps the page and sends nothing."""
    u = _zero_round_page(tmp_path)
    n = len(sent(u.w, "m11"))
    u.api.down = True
    tick(u.w, u.oc, 8)
    u.api.down = False
    tick(u.w, u.oc, 3)
    assert sev(u.w, "m11", "liveness") == [("page", "open")] and allk(u.w, "m11")[n:] == []


# ------------------------------------------------------------------------------------------------ M9


def test_m9_commit_root_revert_from_a_rotated_out_key_does_not_stay_open_forever(tmp_path):
    """Spec M9: commitRoot from a slot address reverts -> warn. Slot 4 is rotated to a new key; the old key's oracle
    process tries once more and reverts NotOracle (warn, correct). The new key then votes normally for many rounds.
    Probe: does the warn ever resolve? (healed only by the old address voting again, which it never will)."""
    oc, w = oracle_worker(tmp_path)
    tick(w)
    oc.round()
    tick(w, oc, 5)
    old = SLOTS[4]
    oc.replace(4, "0x" + "44" * 20)
    tick(w, oc, 2)
    failed_commit_root(oc, tmp_path / "failed-tx.jsonl", old, "NotOracle")
    tick(w, oc, 2)
    key = f"revert:{old}"
    assert open_keys(w, "m9").get(key) == "warn", open_keys(w, "m9")
    for _ in range(8):
        tick(w, oc, 50)
        oc.round(voters=(4, 1, 2))
        tick(w, oc, 5)
    assert key not in open_keys(w, "m9"), open_keys(w, "m9")


def test_m9_newly_activated_slot_is_not_absent_for_rounds_before_it_existed(tmp_path):
    """Spec M9: 'A slot that has not voted for the committed root in any of the last 6 rounds (48 h) -> warn. This
    tolerates the routine skipped fourth vote.' Slots 1-3 are active for 7 committed rounds; slot 4 is then activated
    (a parameter changed on the fake chain). Before any round in which slot 4 could vote, nothing may warn: it was not
    a slot during the earlier rounds (probe; the literal text can be read either way -> NOTE)."""
    from fixtures_oracle import OracleChain

    oc, w = oracle_worker(tmp_path, oc=OracleChain(active=(1, 2, 3)))
    tick(w)
    for _ in range(7):
        tick(w, oc, 50)
        oc.round(voters=(1, 2, 3))
        tick(w, oc, 5)
    assert open_keys(w, "m9") == {}
    oc.set_active((1, 2, 3, 4))
    tick(w, oc, 2)
    tick(w, oc, 2, seconds=120)
    assert "absent:4" not in open_keys(w, "m9"), (open_keys(w, "m9"), pw(w, "m9"))


def test_m9_cadence_first_sweep_restart_and_clock_jump_control(tmp_path):
    """V2b.7 (clock, not event), confirmations 0: no RootCommitted at all since start; a restart at 8 h; the clock
    jumps to 9.5 h without any block: page once; a further restart sends nothing more; the next commit resolves."""
    oc, w = oracle_worker(tmp_path, confirmations=0)
    tick(w)
    tick(w, seconds=8 * 3600)
    assert open_keys(w, "m9") == {}
    w = restart(tmp_path, w, oc)
    tick(w, seconds=1.5 * 3600)
    assert open_keys(w, "m9") == {"cadence": "page"}
    n = len(sent(w, "m9"))
    w = restart(tmp_path, w, oc)
    tick(w, seconds=600)
    assert len(sent(w, "m9")) == n
    oc.round()
    tick(w, oc, 5)
    assert open_keys(w, "m9") == {} and [r["kind"] for r in sent(w, "m9")[n:]] == ["resolved"]


def test_m9_same_block_two_roots_then_commit_and_reorg_depth_1_of_that_block(tmp_path):
    """Same-block ordering + class 2: slot 3's dissenting vote and the committing votes land in one block (warn
    dissent:3); a depth-1 reorg replaces exactly that block with a unanimous round: the warn resolves with the reorg
    text and is never re-sent."""
    oc, w = oracle_worker(tmp_path, confirmations=0)
    tick(w)
    tick(w, oc, 10)
    x = oc.c.number - 1
    blk = oc.c.number
    oc.vote(x, 3, variant=1, mine=False)
    oc.vote(x, 1, mine=False)
    oc.vote(x, 2, mine=False)
    oc.vote(x, 4, mine=True)
    tick(w, oc, 2)
    assert open_keys(w, "m9") == {"dissent:3": "warn"}, open_keys(w, "m9")
    n = len(sent(w, "m9"))
    oc.reorg(blk, latest=0, refs=(x,))
    oc.round(ref=x)
    tick(w, oc, 2)
    tick(w, oc, 200)
    new = sent(w, "m9")[n:]
    assert open_keys(w, "m9") == {} and [r["kind"] for r in new] == ["resolved"] and REORG_TEXT in new[0]["text"], (
        open_keys(w, "m9"), [(r["kind"], r["text"][:100]) for r in new])


# ------------------------------------------------------------------------------------------------ M10


def test_m10_rotation_after_the_first_vote_in_the_same_block_pages(tmp_path):
    """Same-block ordering: the first vote of a round and then OracleReplaced in one block (vote first): the round is
    open at the rotation -> page (the reverse order is silent, QA3 control)."""
    oc, w = oracle_worker(tmp_path, ids=("m10",))
    tick(w)
    tick(w, oc, 10)
    x = oc.c.number - 1
    oc.vote(x, 1, mine=False)
    oc.replace(3, "0x" + "33" * 20)
    tick(w, oc, 2)
    assert list(open_keys(w, "m10").values()) == ["page"], open_keys(w, "m10")


def test_m10_rotation_page_survives_restart_then_heals_once(tmp_path):
    """Restart idempotency: a rotation page opened before a restart is not re-sent; the commit that closes the round
    after the restart resolves it exactly once."""
    oc, w = oracle_worker(tmp_path, ids=("m10",))
    tick(w)
    tick(w, oc, 10)
    x = oc.c.number - 1
    oc.vote(x, 1)
    oc.replace(3, "0x" + "33" * 20)
    tick(w, oc, 2)
    n = len(sent(w, "m10"))
    w = restart(tmp_path, w, oc, ids=("m10",))
    tick(w, oc, 2)
    assert len(sent(w, "m10")) == n
    oc.round(ref=x, voters=(2, 4))
    tick(w, oc, 2)
    tick(w, oc, 2)
    assert open_keys(w, "m10") == {} and [r["kind"] for r in sent(w, "m10")[n:]] == ["resolved"]
```
<!-- END test_qa4.py -->

## Appendix B — `/tmp/qa-cp2b-4/probe/show_f3c.py` (QA4-F2 message trace; run from the worktree root)

```python
import sys, tempfile, pathlib
sys.path[:0] = ["reconcile", "reconcile/tests", "/tmp/qa-cp2b-4/tests"]
from test_qa4 import _zero_round_page, sev  # noqa: E402
from fixtures_oracle import tick, sent  # noqa: E402

for api_back in (False, True):
    tmp = pathlib.Path(tempfile.mkdtemp())
    u = _zero_round_page(tmp)
    n = len(sent(u.w, "m11"))
    u.api.down = True
    u.oc.reorg(u.c.number - 5, latest=u.oc.latest)
    u.c.mine(8)
    tick(u.w)
    s1 = sev(u.w, "m11", "liveness")
    u.api.down = not api_back
    tick(u.w, u.oc, 3, seconds=300)  # the next interval run (no reorg)
    print(f"API back={api_back}: reorg run -> {s1}; next interval run -> {sev(u.w, 'm11', 'liveness')}")
    for r in sent(u.w, "m11")[n:]:
        print("   sent:", r["severity"], r["kind"], r["text"].splitlines()[0][:120])
```

## Appendix C — `/tmp/qa-cp2b-4/qfalsify.py`

```python
"""QA attempt 4 falsifiers: one-token mutation, run the named test (expect exit 1), restore bytes, re-run (expect 0),
git status --porcelain must be empty after each pair. Run from the worktree root."""
import os, subprocess, sys, tempfile
from pathlib import Path

PY = "reconcile/.venv/bin/python"
QA4 = "/tmp/qa-cp2b-4/tests/test_qa4.py"
CASES = [
    ("QF4-m9-absent-count (6 rounds -> 7)", "reconcile/checks/m9_oracle_quorum.py",
     "                if len(missed) == k:\n", "                if len(missed) == k + 1:\n",
     ["reconcile/tests/test_m9.py::test_slot_absent_from_six_committed_rounds_warns"]),
    ("QF4-m10-same-block-vote-before-rotation (log order -> block order)", "reconcile/checks/m10_oracle_rotation.py",
     "before = [v for v in rnd.votes if v.pos < ev.pos]", "before = [v for v in rnd.votes if v.block < ev.block]",
     [QA4 + "::test_m10_rotation_after_the_first_vote_in_the_same_block_pages"]),
    ("QF4-m11-revert-boundary (> 10 % -> >= 10 %)", "reconcile/checks/m11_updater_liveness.py",
     'if rate is None or rate <= self.config["revert_rate"]:', 'if rate is None or rate < self.config["revert_rate"]:',
     ["reconcile/tests/test_m11.py::test_benign_revert_rate_at_10_percent_is_silent"]),
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

## Appendix D — `/tmp/qa-cp2b-4/vrows.sh`

```bash
#!/bin/bash
# QA attempt 4: each row its own pytest; run from the worktree root
set -u
PY=reconcile/.venv/bin/python
P="$PY -X pycache_prefix=/tmp/qa-cp2b-4/pyc-v -m pytest -q -p no:cacheprovider"
run() { local label=$1; shift; echo "== $label: pytest $*"; $P "$@" 2>&1 | tail -1; echo "exit ${PIPESTATUS[0]}"; }
echo "head $(git rev-parse HEAD) $(date -u +%FT%TZ) porcelain=$(git status --porcelain | wc -l | tr -d ' ')"
for f in m9 m10 m11; do run "V2b.1 $f" reconcile/tests/test_$f.py; done
for k in thresholds_from_chain restart rpc_budget; do for f in m9 m10 m11; do run "$k $f" reconcile/tests/test_$f.py -k $k; done; done
run "V2b.7" reconcile/tests/test_m9.py -k from_the_clock -v
for f in m9 m10 m11; do run "qa1 $f" reconcile/tests/test_$f.py -k qa1; done
for f in m9 m11; do run "qa2 $f" reconcile/tests/test_$f.py -k qa2; done
for f in m9 m11; do run "qa3 $f" reconcile/tests/test_$f.py -k qa3; done
for f in m9 m10 m11; do run "r3 $f" reconcile/tests/test_$f.py -k r3_; done
run "sweep" reconcile/tests/test_m9_m10_m11_sweep.py
for n in 1 2 3; do
  case $n in 1) name=test_independent.py;; 2) name=test_qa2.py;; 3) name=test_qa3.py;; esac
  echo "== attempt-$n Appendix A as published"
  PYTHONPATH=reconcile:reconcile/tests $P -rf /tmp/qa-cp2b-4/a$n/$name 2>&1 | grep -E "^FAILED|passed|failed"; echo "exit ${PIPESTATUS[0]}"
done
run "V2b.6 suite" reconcile/tests
echo "== ruff"; reconcile/.venv/bin/ruff check --no-cache reconcile; echo "exit $?"
echo "end $(date -u +%FT%TZ) porcelain=$(git status --porcelain | wc -l | tr -d ' ')"
```
