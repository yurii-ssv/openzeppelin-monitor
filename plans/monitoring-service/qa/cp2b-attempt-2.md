FAIL

Independent QA of CP2b (M9, M10, M11), attempt 2 (after repair 1). Frozen head under test: `ed421e65bda81bf9f85b2c69dff6faed13c5f02b` (lane branch `mtabasco/ms-cp2b`, code head `92a7a3c`); QA worktree `ms-qa-cp2b-2`, branch `mtabasco/ms-qa-cp2b-2`. Task `task_5a45c31518e4`, dispatch `ctx_d9aae67bba36`. QA model: Claude Opus 5.5 (D13), effort high. Measured 2026-10-21, about 15:40–16:30 UTC. No implementation repair; QA scratch under `/tmp/qa-cp2b-2`.

Summary. Every supplied row is green and reproduces the implementer's counts exactly (M9 60, M10 23, M11 59, sweep 44, suite 426, ruff clean; 87 failed / 99 passed on the frozen code), and all 15 repair falsifiers redden and restore. F1–F8 of attempt 1 are fixed and their reproducers pass, except two whose own setup contradicts F6; I judge that adaptation legitimate (§2). My spec-derived scenarios still find two blocking defects and three lesser ones: **QA2-F1** a `WeightedRootProposed` whose blockNum never commits is silent when a higher blockNum commits within 30 min (spec trigger M9-1 missed); **QA2-F2** a proof API that hangs after one answer is asked for every cluster (unbounded run time of the single-threaded worker, every monitor's alerts delayed). The LIVE M11 mismatch (cluster `0xc6d52a5b…`) is real and benign: 16 validators were removed from that cluster after the root.

## 1. Findings (ranked)

Reproducer file: `/tmp/qa-cp2b-2/tests/test_qa2.py`, embedded verbatim in Appendix A (extraction command there). `Q` = `PYTHONPATH=reconcile:reconcile/tests reconcile/.venv/bin/python -X pycache_prefix=/tmp/qa-cp2b-2/pyc -m pytest -q -p no:cacheprovider /tmp/qa-cp2b-2/tests/test_qa2.py`. Full file at the frozen head: **exit 1, 5 failed / 11 passed** (the 5 failures are QA2-F1..F4 and the NOTE N1 probe; the 11 passes are controls listed in §4).

### QA2-F1 — MEDIUM (BLOCKING class: missed page) — M9 failed-round trigger silenced by a superseding commit of a *higher* blockNum

`Q -k lone_vote_for_an_earlier` → exit 1. Slot 3 proposes blockNum X−1 (one `WeightedRootProposed`); slots 1, 2, 4 propose X and commit X three blocks later; 160 more blocks (32 min) pass. **Expected (spec M9, line 1):** "`WeightedRootProposed` for a `blockNum` with no `RootCommitted` within 30 min → page": blockNum X−1 never gets a `RootCommitted`, so failed-round **page**. **Actual:** `open_keys == {}`, zero page/warn messages from M9. The only remaining signal is `absent:3` (warn) after six committed rounds (48 h) — the exact zero-margin state that preceded the 2026-08 outage (one slot not contributing to quorum).

Cause: `m9_oracle_quorum.py:139-141` (`in_time = close is not None and self.times(close.block) <= deadline`, where `close` may be a *superseding* commit of a higher blockNum, `m9_oracle_common.py:119-124`). Repair 1 introduced this as a worker-local "spec reading" (workers/cp2b/plan.md, "Failed round superseded within its 30 min"); its falsifier `S-superseded` (mutating to the literal rule `rnd.commit is not None and …`) reddens the implementer's own test `test_a_round_superseded_within_its_30_minutes_is_not_failed_and_one_superseded_later_is`, i.e. that test asserts the non-literal behaviour. Control `Q -k lone_vote_for_a_later` (the same anomaly with blockNum X+1, not superseded) **pages** — the rule is asymmetric in the direction of the wrong blockNum. Like attempt 1's F7, a worker-local reading that removes a spec trigger needs a repair or an explicit spec amendment by the owner; it is not approved by the worker record. (The same silence exists on the attempt-1 code `4e64eb1`; not a regression of repair 1, but uncovered until now.)

### QA2-F2 — MEDIUM (BLOCKING class: alerts of every monitor delayed) — proof API that dies mid-comparison is asked for every cluster

`Q -k proof_api_dying` → exit 1: `20 proof GETs for 20 clusters after the API died`. The API answers one cluster, then every request fails with a transport error (`ProofApiError("TimeoutError: timed out")`). **Expected:** `proof_api_abort_after` (3) consecutive transport failures make the API "unreachable" (config comment, `m11_updater_liveness.py:121`) and the comparison stops (≤ ~4–5 GETs). **Actual:** all 20 clusters are asked. Cause: `compare_leaves` aborts only when `failures >= abort_after and not leaves` (`m11_updater_liveness.py:485`): once one leaf was received, no number of failures stops it. Impact on mainnet: 530 clusters × `proof_api_timeout_s` 15 s ≈ 2.2 h inside one M11 run; `Worker.run_checks` is sequential (`eb_reconcile.py:441-463`) and `deliver()` runs after it, so **no other check runs and no alert of any monitor (M3 pages, M5, M9 …) is delivered** for that time; the unknown set is re-asked at every M11 run (`zero_round_proof` retries `only=unknown`; the weekly re-comparison retries unknown clusters every run), so a hanging API stalls the worker repeatedly. A 429/timeout pattern after N requests is exactly how an overloaded API behaves under 530 sequential GETs. Class 6 (bounded cost) of DEFECT-CLASSES. The normal 285 s weekly run (implementer note → CP3) is a separate, known item.

### QA2-F3 — LOW (class 2: open incident without canonical evidence) — state-mismatch kept after a reorg removed the round that aged it

`Q -k reorg_removing_the_round_that_aged` → exit 1: `({'state-mismatch': 'warn'}, [one evaluation at ref 1106])`. Cluster 1 mismatches at root R1 and again at R2 → warn (correct, F6). A reorg (confirmations 2, depth > window) removes R2's `RootCommitted` and everything after it. **Expected (README §6.2, spec "older than one round"):** the canonical chain now holds the mismatch at one root only — the condition is definitively not established, so the key is not reported again and the framework resolves it ("evidence left the canonical chain"). **Actual:** `state_mismatch` returns `unknown=True` (`m11_updater_liveness.py:430-432`: the reported cluster's newest status is `mismatch`, not `match/gone`), the book re-reports the open row, the warn stays open on orphaned evidence until the next round closes. This also contradicts the sweep's own invariant "state-mismatch open ⇒ a mismatch at two consecutive roots" (the sweep never removes the aging round). Window ≤ one round; no extra message (24 h warn cadence).

### QA2-F4 — LOW (class 6: a cap drops the item the spec needs) — zero-update round older than `history_rounds` is never reported after downtime

`Q -k zero_round_older_than_history` → exit 1: `assert ('liveness', 'open') in []`. Round 1 has 0 updater txs; the worker is down for 7 further normal rounds (~2.5 days of chain time), then restarts. **Expected:** the zero round (a spec M11 warn) that occurred and healed while the worker was down is reported once (open + resolution), as M9 does for failed rounds (`test_catch_up_after_downtime_reports_a_healed_failed_round_once`). **Actual:** nothing: `closed` keeps only the last `history_rounds` (6) closed commits (`m11_updater_liveness.py:183-188`). Control `Q -k zero_round_within_history` (5 normal rounds) passes: open + resolved. Same cap applies to revert-rate rounds. Low because downtime ≥ 6 rounds is itself a health/lag outage.

### N1 — NOTE — a second instance of a key with an unchanged revision is not sent

`Q -k second_weak_commit` → exit 1: two consecutive rounds commit with 2 distinct slots (quorumBps lowered to 5000 on the fake chain); only the first produces a message (`weak-commit` open); the second updates the open incident silently (`revision` None in `weak_commits` and in `_gap` for cadence, `m9_oracle_quorum.py:212-220, 330-341`; `alerts.py:150-161` sends only on escalation/new revision). The page stays open and re-alerts every 6 h with the new body, so nothing is lost for the operator's paging; NOTE (each spec trigger instance is not individually reported). The same merge happens for two cadence gaps between sweeps (book: an earlier episode is sent first only when its revision differs, `m9_oracle_common.py:329-332`).

### N2 — NOTE — M1 incident link not implemented (carried from attempt 1)

`IncorrectClusterState` reverts are counted and listed as `evidence.m1_handoff` (tx hashes); my `test_m11_revert_rate_boundary` asserts the key is present. No M1 incident id/link exists on this lane; the implementer does not claim it (CP3). Not credited.

## 2. Claims I could NOT confirm / judgements on the implementer's claims

- **"Every spec trigger line has a test at its severity and a benign twin":** not confirmed for M9 line 1 (QA2-F1: the implementer's benign twin `test_a_round_superseded_within_its_30_minutes…` asserts the silence the spec forbids). My own trigger derivation from spec §3 (lines 267–337) gives the same 16 lines as the implementer's table (T1–T16; T10 and T15 are Monitor/CP1A, not credited here; the M8 line "commitRoot revert with ZeroCSSVSupply → page (also caught by M9)" is CP2c's page, M9 warns).
- **The two ADAPTED reproducers of attempt 1** (`test_m11_partial_api_during_reorg_keeps_known_mismatch`, `test_m11_health_only_reorg_with_api_down_keeps_episode`): re-measured — the published attempt-1 file runs **23 passed / 2 failed** on this head, and both fail at their *setup* assertion (`state-mismatch == warn` right after one round), the exact setup that attempt-1's own `test_m11_latest_round_mismatch_must_age_one_round` requires to be silent. As published the file is self-contradictory: no implementation can pass all 25. The ported versions (`test_qa1_f1_partial_proof_answer_in_a_reorg_run_keeps_the_reported_mismatch`, `test_qa1_control_health_only_reorg_with_the_api_down_keeps_the_episode`) only age the mismatch by one more round (`mismatch_older_than_one_round`), keep the same reorg (fork = number − 2, below the evaluated block, commit kept), the same API attack (proof deleted / API down) and the same assertions, and add stronger ones (no resolution, positive proof resolves later). **Legitimate; it does not hide a defect.** The adjacent case they do not cover (a reorg that removes the aging round) is QA2-F3.
- **"39 new tests RED on the frozen head":** the aggregate re-measured exactly (frozen code `git archive a1e4775 reconcile` + HEAD tests: **87 failed / 99 passed**, = `evidence/red_green_repair1.txt`). I did not re-derive the per-test 39/20 split of "new" tests (that needs the list of tests new in repair 1); the aggregate and every named QA1 regression are confirmed.
- **"Unknown inputs in a reorg run re-report the open key":** confirmed for the API-down and partial cases; wrong for a *definite* negative re-evaluation (QA2-F3).
- **M11 LIVE "1 mismatch younger than one round":** confirmed and explained (§3 LIVE).
- **Implementer's LIVE replays used saved dumps** (no archive re-fetch): I re-fetched independently — see §3.
- Not run: CP3 fork scenarios (V3.8) — out of this checkpoint.

## 3. Verification matrix

Preflight: `git rev-parse HEAD` = `ed421e65bda81bf9f85b2c69dff6faed13c5f02b` (= dispatch head), exit 0; `git status --porcelain` empty at start; `df -h .` 347 GiB free. No `reconcile/.venv` in the fresh worktree: `uv venv reconcile/.venv --python 3.12` exit 0 (Python 3.12.15); `uv pip install --python reconcile/.venv/bin/python -r reconcile/requirements-dev.txt` exit 0 (pytest 9.1.1, ruff 0.17.1); no other install. Frozen-file check: `git diff --stat plan/monitoring-service HEAD -- reconcile/tests/harness.py reconcile/tests/test_framework.py reconcile/tests/test_worker.py` → empty (identical to the integration branch); `git log 96d1c32..HEAD` on those three files lists exactly `bd531b4` and `500abe4`; `git diff --name-status f92e2b7 HEAD` (lane start after the ruled merges) touches only the four lane modules, the four lane test files + `fixtures_oracle.py`, one import line in `checks/__init__.py`, the `checks.m9/m10/m11` objects of `config.example.json`, and plans files — **no other frozen CP1B file edited**. `config/`, `config.zip`, `.env*` untouched.

Each row run on its own (`/tmp/qa-cp2b-2/vrows.sh`, log `/tmp/qa-cp2b-2/vrows.log`); command prefix `reconcile/.venv/bin/python -X pycache_prefix=/tmp/qa-cp2b-2/pyc-v -m pytest -q -p no:cacheprovider`.

| V-ID | Command | Exit / result | Verdict |
|---|---|---|---|
| V2b.1 (V2x.1) M9 | `… reconcile/tests/test_m9.py` | 0; 60 passed | supplied PASS; QA2-F1 against it |
| V2b.1 M10 | `… reconcile/tests/test_m10.py` | 0; 23 passed | PASS |
| V2b.1 M11 | `… reconcile/tests/test_m11.py` | 0; 59 passed | supplied PASS; QA2-F2..F4 against it |
| sweep | `… reconcile/tests/test_m9_m10_m11_sweep.py` | 0; 44 passed | PASS (can fail: SWEEP-* falsifiers red) |
| V2b.2 (V2x.2) | `… test_m9.py -k thresholds_from_chain` / m10 / m11 | 0; 2 / 1 / 1 passed | PASS |
| V2b.3 (V2x.3) | `… -k restart` per file | 0; 3 / 2 / 5 passed | PASS (+ my restart controls, §4) |
| V2b.4 (V2x.4) | `… -k rpc_budget` per file | 0; 1 / 1 / 1 passed (M11: 350 clusters, eth_call ≤ 350, 40 sender lookups once, 0 on a cached run) | PASS for eth_call; GET count under API failure unbounded (QA2-F2) |
| V2b.5 (V2x.5) | falsifiers, §4 | 15 + 3 legacy anchors + 6 mine | PASS sensitivity |
| V2b.6 (V2x.6) suite | `reconcile/.venv/bin/python -m pytest -q reconcile/tests -p no:cacheprovider` | 0; **426 passed in 21.92 s** | PASS |
| V2b.6 ruff | `reconcile/.venv/bin/ruff check reconcile` | 0; All checks passed! | PASS |
| V2b.7 | `… test_m9.py -k from_the_clock` | 0; 1 passed (checkpoint and log count unchanged, run reason `interval`, page at 9.1 h, silent at 8.9 h) | PASS; plus my clock-jump/restart test (§4) |
| qa1 regressions | `… -k qa1` per file | 0; 8 / 1 / 7 passed | PASS |
| Attempt-1 Appendix A as published | extracted to `/tmp/qa-cp2b-2/a1/test_independent.py` | 1; **23 passed / 2 failed** (both at their setup assertion) | as claimed; judged in §2 |
| RED on frozen | frozen code `a1e4775` + HEAD tests, lane files | **87 failed / 99 passed** | as claimed |
| QA2 independent scenarios | `Q` (Appendix A) | 1; 5 failed / 11 passed | **FAIL** (QA2-F1..F4, N1) |
| LIVE M11 weekly comparison | `reconcile/.venv/bin/python plans/monitoring-service/workers/cp2b/evidence/live_m11_leaves.py https://ethereum-rpc.publicnode.com` (runs the head's `compare_leaves`) at 2026-10-21T15:52Z | 0; block **26,241,981**, latest committed blockNum 26,239,590 (= proof API `referenceBlock`), 530 active ETH clusters, 0 stale tuples, 530 compared, **1 mismatch** `0xc6d52a5b…574b` on-chain 230,930 vs leaf 231,442, 0 unknown; 530 eth_call + 531 eth_getStorageAt; 284.6 s | PASS (LIVE) |
| LIVE mismatch root cause | own scripts `/tmp/qa-cp2b-2/live/cluster.py`, `cluster4.py` (Appendix B): proof API, owner-topic logs (publicnode, 26,239,500 → 26,241,998), archive `eth_call` (eth.drpc.org) | proof: referenceBlock 26,239,590, EB 231,442. Logs: our updater's `ClusterBalanceUpdated` for this cluster at **26,239,661** (ref 26,239,590, EB 231,442, 2,487 validators); **16 `ValidatorRemoved` of this cluster in one tx at 26,241,743** (`0xcc25c4f3…`); `getEffectiveBalance` = **231,442 at 26,241,742**, **230,930 at 26,241,743**; 231,442 − 230,930 = 512 = 16 × 32 | the mismatch is a validator exit after the root (on-chain EB follows removals), younger than one round; attempt 1 saw 530/530 at 26,241,594, before the removal. Correctly not warned (F6). |
| LIVE outage replay (own full-range archive read) | below | 0; 89 logs / 21 rounds / 13 commits, replay = spec record | PASS (LIVE) |
| LIVE current rounds | below | 0; 12 logs / 3 rounds, 0 incidents | PASS (LIVE) |

**LIVE-OUTAGE (own full-range archive read, not targeted windows):** `reconcile/.venv/bin/python -I /tmp/qa-cp2b-2/live/fullscan.py 25680100 25728500 /tmp/qa-cp2b-2/live/outage_full.json` (Appendix B) — every `WeightedRootProposed`/`RootCommitted` log of the proxy in blocks **25,680,100–25,728,500**, 969 × 50-block `eth_getLogs` chunks, endpoints **eth-pokt.nodies.app (918 chunks) and eth.drpc.org (51)**, exit 0, 827.5 s: **89 logs, 21 blockNums, 13 commits**, event blocks 25,680,231–25,728,058; canonical SHA-256 `128d8aae…7f383`. The set is **identical** to the implementer's saved dump (block, log index, tx, topics, data) — and, because this read is full-range rather than `[ref+1, ref+200]` per known round, it also shows there is no oracle log outside those windows. Replay: `reconcile/.venv/bin/python plans/monitoring-service/workers/cp2b/evidence/live_replay.py /tmp/qa-cp2b-2/live/outage_full.json 400`, exit 0: M9 `failed-round` page (8 failed rounds in a row) resolved, `cadence` page resolved (gap 258,096 s = 71.7 h; spec record: 72 h outage, 8 failed rounds), `dissent:3` page open, `absent:3` warn open, six `split` warns each resolved; M10 none; 89 messages; no health message; every field identical to `outage_2026_08_replay.repair1.out.json`. **Reproduces the spec record.**

**LIVE-RECENT:** same script, blocks **26,234,427–26,242,026** (head − 20 on eth.drpc.org; ≈ 25 h), 152 chunks (nodies 143, drpc 9), exit 0, 128.7 s: **12 logs, 3 rounds** (blockNums 26,234,805 / 26,237,195 / 26,239,590), 3 commits, event blocks 26,234,870–26,239,655 — no newer round had landed yet; identical to the saved `live_recent_logs.json`. Replay exit 0: **0 incidents, 0 messages, 0 health** for M9/M10.

Endpoint failures (recorded, not credited): eth.drpc.org and blastapi rejected `eth_getLogs` ranges > 50 blocks (`HTTP 400`; blastapi also at 50); eth-pokt.nodies.app `-32001 Block range too large: maximum allowed is 50 blocks`; ethereum-rpc.publicnode.com `-32602 block range extends beyond current head block` for a range ending at head, and `HTTP 403 Forbidden` for blocks ≳ 4 days old and, after the 285 s M11 run, for 100–1000-block log ranges (rate limit).

## 4. Falsifiers

**Implementer's 15 (repair 1):** `bash plans/monitoring-service/workers/cp2b/evidence/falsify_repair1.sh` (log `/tmp/qa-cp2b-2/falsify_repair1.log`), exit 0. Every mutation: mutated exit **1**, restored (`git checkout -- <file>`) exit **0**, `git status --porcelain` empty after each pair (15/15): F1-retry, F1-proof, F2-order, F3-split, F3-sameslot, F4-events, F5-before, F6-age, F7-consecutive, F8-clock, F8-slots, S-superseded, SWEEP-m9, SWEEP-m11, SWEEP-m10. Fresh bytecode cache per process. Note: S-superseded reddening is the evidence for QA2-F1 (the guarded test asserts the non-literal rule). The brief's "5 falsifiers" of the first implementer: F9-streak, F10-open and F-book-reorg anchors still exist and their tests are in the green rows; F9-clock and F11-window anchors were removed by the F8 repair (successors F8-clock, F8-slots, both red/green above). Not re-run separately (unchanged anchors, covered tests green).

**Mine** (`/tmp/qa-cp2b-2/qfalsify.py`, Appendix C; byte restore in `finally`, fresh pycache per process; log `/tmp/qa-cp2b-2/qfalsify.log`):

| ID | Mutation | Test | Mutated / restored | porcelain |
|---|---|---|---|---|
| QF-book-reorg | `m9_oracle_common.py` `if ctx.reorg_ancestor is not None and r["evidence_block"] > ctx.reorg_ancestor:` → `if False:` | QA2 `test_m10_rotation_during_open_round_then_reorg_moves_it_after_the_commit` | 1 / 0 | empty |
| QF-m9-cadence-clock | `age = self.now - since_at` → `age = self.times(block) - since_at` | QA2 `test_m9_cadence_restart_and_clock_jumps_no_duplicate` | 1 / 0 | empty |
| QF-m9-commit-order (same-block order) | `if v.root == rnd.commit.root and v.pos < rnd.commit.pos]` → `if v.root == rnd.commit.root]` | `test_m9.py::test_qa1_control_proposal_after_commit_is_not_counted_as_quorum` | 1 / 0 | empty |
| QF-m10-open-at-event | `hist.closing_event(rnd, before=ev.pos)` → `hist.closing_event(rnd)` | QA2 M10 reorg test: **survived** (0 / 0) — my test checks the page while the round is still open; separately the implementer's `test_m10.py` reddens (2 failed: `test_rotation_during_an_open_round_pages_and_resolves_when_the_round_closes`, `test_same_block_rotation_then_commit_pages_once_and_resolves`), restored 23 passed | covered by supplied tests | empty |
| QF-m11-gt-vs-ge (10 % boundary) | `if rate <= self.config["revert_rate"]:` → `if rate < …` | QA2 `test_m11_revert_rate_boundary` | 1 / 0 | empty |
| QF-m11-empty-inventory (empty input read as clean) | `if not clusters and only is None:` → `if False:` | `test_m11.py::test_empty_inventory_is_deferred_not_clean` | 1 / 0 | empty |

**QA2 controls that pass** (spec-derived, outside the implementer's scenario list): M9 clock jump +9.5 h with no block → page; restart during the silence and a −2 h NTP step → no duplicate, no resolve; one 6 h re-alert; health-only tip reorg (`[]` for the check) keeps the open dissent with no message; reorg beyond the confirmation window (conf 2) removing the third vote and the `RootCommitted` → failed-round page on the new chain, no duplicate after restart; same-block dissenting vote logged after the commit → warn; the X+1 control of QA2-F1 pages. M10: rotation inside an open round pages, a reorg re-including it after the commit resolves it with "Evidence left the canonical chain" and never re-pages; rotation with no open round silent through a restart. M11: 10 % revert rate silent, 11.1 % warn with `m1_handoff`; `{}` answers for every cluster → warn, never "all match"; two zero rounds → page, restart → no new message; downtime control (QA2-F4). Attempt-1's remaining 23 reproducers pass on this head (F1–F8 fixed).

## 5. Evidence-plane writes

- CP2b **Status:** `IMPLEMENTED — AWAITING QA — repair 1` → `QA FAILED` on frozen head `ed421e65bda81bf9f85b2c69dff6faed13c5f02b`, linking this report.
- Wrote `plans/monitoring-service/qa/cp2b-attempt-2.md` (this file) and appended one event `event-cp2b-qa-2` to `plans/monitoring-service/execution-log.md`.
- Local untracked venv `reconcile/.venv` (ignored); scratch only under `/tmp/qa-cp2b-2`. No test added to the repo, no implementation file changed, no next checkpoint started.

## 6. Repo proof

After all mutation restorations, **before** the evidence-plane writes (measured after the falsifier runs and again after the LIVE runs):

```text
$ git status --porcelain
<empty>
$ git diff --name-only ed421e65bda81bf9f85b2c69dff6faed13c5f02b
<empty>
```

After the permitted writes (report, status line, log event), before the commit:

```text
$ git status --porcelain
 M plans/monitoring-service/execution-log.md
 M plans/monitoring-service/plan.md
?? plans/monitoring-service/qa/cp2b-attempt-2.md
$ git diff --name-only ed421e65bda81bf9f85b2c69dff6faed13c5f02b
plans/monitoring-service/execution-log.md
plans/monitoring-service/plan.md
$ git diff --exit-code HEAD -- reconcile
<empty>, exit 0
```

**No implementation file appears in either output.** The report is untracked at this measurement; only these three documentation paths are committed. Budget: the full suite (22 s) and the 285 s LIVE M11 comparison were each run once; the M9/M10 replays of the implementer's saved dumps were re-run once (cheap, identical output) and then replaced by my own archive reads; attempt-1's legacy falsifiers F9-streak/F10-open/F-book-reorg were not re-run (anchors unchanged, tests green), F9-clock/F11-window anchors no longer exist.

## Appendix A — `/tmp/qa-cp2b-2/tests/test_qa2.py`

<!-- BEGIN test_qa2.py -->
```python
"""CP2b QA attempt 2: independent spec-derived scenarios (QA scratch, not part of the repo).

Each test states its expectation from the spec text / README 6.2, not from the implementation's output.
Run: PYTHONPATH=reconcile:reconcile/tests reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider <this file>
"""
from __future__ import annotations

from fixtures_oracle import (
    HOUR,
    SLOTS,
    UpdaterWorld,
    open_keys,
    oracle_worker,
    restart,
    sent,
    tick,
)

REORG_TEXT = "Evidence left the canonical chain"


def pw(w, check):
    return [(r["ikey"], r["severity"], r["kind"]) for r in sent(w, check, "severity IN ('page','warn')")]


# ------------------------------------------------------------------------------------------------ M9


def test_m9_lone_vote_for_an_earlier_blocknum_that_never_commits_pages(tmp_path):
    """Spec M9: 'WeightedRootProposed for a blockNum with no RootCommitted within 30 min -> page'. Slot 3 (bad
    reference-block computation) proposes blockNum X-1; slots 1, 2, 4 propose X and commit X a few blocks later.
    blockNum X-1 never gets a RootCommitted: 30 min after its proposal this is a failed round (page)."""
    oc, w = oracle_worker(tmp_path)
    tick(w)
    x = oc.c.number - 1
    oc.vote(x - 1, 3)  # slot 3: wrong (earlier) blockNum
    oc.round(ref=x, voters=(1, 2, 4))  # X commits 3 blocks later
    assert oc.committed(x)
    tick(w, oc, 1)
    tick(w, oc, 160)  # > 30 min after the X-1 proposal
    assert open_keys(w, "m9").get("failed-round") == "page" or any(
        k == "failed-round" for k, _, _ in pw(w, "m9")), (open_keys(w, "m9"), pw(w, "m9"))


def test_m9_lone_vote_for_a_later_blocknum_pages_control(tmp_path):
    """Control for the test above: the same anomaly with the wrong blockNum ABOVE X pages (not superseded)."""
    oc, w = oracle_worker(tmp_path)
    tick(w)
    x = oc.c.number - 2
    oc.vote(x + 1, 3)
    oc.round(ref=x, voters=(1, 2, 4))
    tick(w, oc, 161)
    assert open_keys(w, "m9").get("failed-round") == "page", (open_keys(w, "m9"), pw(w, "m9"))


def test_m9_second_weak_commit_is_sent(tmp_path):
    """Spec M9: 'A round committed with fewer than 3 distinct slots ... -> page'. Two consecutive rounds commit with 2
    slots: each is a trigger instance; the second must produce a message (not only update a silent incident)."""
    oc, w = oracle_worker(tmp_path)
    tick(w)
    oc.set_quorum(5000)
    tick(w, oc, 1)
    r1 = oc.round()
    tick(w, oc, 5)
    first = pw(w, "m9")
    assert first == [("weak-commit", "page", "open")], first
    tick(w, oc, 200)
    r2 = oc.round()
    tick(w, oc, 5)
    msgs = [r for r in sent(w, "m9") if r["ikey"] == "weak-commit"]
    assert len(msgs) >= 2 and str(r2) in msgs[-1]["text"], [(m["kind"], m["text"][:120]) for m in msgs] + [r1, r2]


def test_m9_cadence_restart_and_clock_jumps_no_duplicate(tmp_path):
    """V2b.7 attack: no events at all; the clock jumps 9.5 h in one step (no blocks) -> page from the clock; restart
    during the silence -> no duplicate; a backwards clock jump must not resolve or duplicate; the 6 h re-alert."""
    oc, w = oracle_worker(tmp_path)
    tick(w)
    oc.round()
    tick(w, oc, 5)
    assert open_keys(w, "m9") == {}
    tick(w, seconds=9.5 * HOUR)
    assert open_keys(w, "m9") == {"cadence": "page"}
    n = len(sent(w, "m9"))
    w = restart(tmp_path, w, oc)
    tick(w)
    w.clock.now -= 2 * HOUR  # NTP step backwards
    tick(w)
    assert open_keys(w, "m9") == {"cadence": "page"} and len(sent(w, "m9")) == n
    w.clock.now += 2 * HOUR
    tick(w, seconds=6 * HOUR + 60)
    w.housekeeping()
    assert sum(r["kind"] == "realert" for r in sent(w, "m9")) == 1


def test_m9_health_only_reorg_keeps_open_dissent_silently(tmp_path):
    """Reorg of the tip with no oracle event on either side ('[]' reorg result for the check): open incidents stay,
    nothing is resolved or re-sent."""
    oc, w = oracle_worker(tmp_path, confirmations=0)
    tick(w)
    oc.round(variants={3: 1}, voters=(3, 1, 2, 4))
    tick(w, oc, 5)
    before = open_keys(w, "m9")
    assert before.get("dissent:3") == "warn", before
    n = len(sent(w, "m9"))
    oc.c.reorg(oc.c.number - 2)
    oc.c.mine(4)
    tick(w)
    assert open_keys(w, "m9") == before and len(sent(w, "m9")) == n, (open_keys(w, "m9"), sent(w, "m9")[n:])


def test_m9_reorg_beyond_confirmations_removing_the_commit_turns_round_into_failed(tmp_path):
    """Reorg deeper than the confirmation window removes the third vote and the RootCommitted of a round that
    committed in time: on the new chain the round never commits -> failed-round page once its 30 min pass; one
    message, no duplicate after a restart."""
    oc, w = oracle_worker(tmp_path, confirmations=2)
    tick(w, oc, 4)
    ref = oc.c.number - 1
    oc.vote(ref, 1)
    oc.vote(ref, 2)
    fork = oc.c.number
    oc.vote(ref, 3)  # commits
    assert oc.committed(ref)
    tick(w, oc, 40)
    assert open_keys(w, "m9") == {}
    oc.c.reorg(fork)
    oc.latest = 0
    oc.c.mine(10)
    tick(w, oc, 160)
    assert open_keys(w, "m9").get("failed-round") == "page", open_keys(w, "m9")
    n = len(sent(w, "m9"))
    w = restart(tmp_path, w, oc)
    tick(w, oc, 2)
    assert len(sent(w, "m9")) == n


def test_m9_same_block_two_roots_then_commit_orders(tmp_path):
    """Same-block ordering: slot 3's dissenting vote and slots 1,2,4's votes incl. the commit in ONE block, the
    dissenting vote AFTER the commit (log order). The two-root trigger is a warn regardless of order."""
    oc, w = oracle_worker(tmp_path)
    tick(w)
    ref = oc.c.number - 1
    for s in (1, 2, 4):
        oc.vote(ref, s, mine=False)
    assert oc.committed(ref)
    # a vote after the commit would revert StaleBlockNumber on chain; emit it raw to probe order handling
    from eb_storage import u256
    from fixtures_oracle import WRP
    root = oc.root(ref, 1)
    oc.c.emit_event(WRP, [root, "0x%064x" % ref], u256(10) + u256(30) + u256(3) + u256(int(SLOTS[3], 16)))
    oc.c.mine()
    tick(w, oc, 2)
    assert open_keys(w, "m9").get("dissent:3") == "warn" or any(s == "warn" for _, s, _ in pw(w, "m9")), pw(w, "m9")


# ------------------------------------------------------------------------------------------------ M10


def test_m10_rotation_during_open_round_then_reorg_moves_it_after_the_commit(tmp_path):
    """Page for OracleReplaced inside an open round (kept open while the round is uncommitted); a reorg (depth >
    confirmations) re-includes the rotation after the round's commit: the page resolves with the reorg text and is
    never re-paged."""
    oc, w = oracle_worker(tmp_path, ids=("m10",), confirmations=1)
    tick(w, oc, 3)
    ref = oc.c.number - 1
    oc.vote(ref, 1)
    fork = oc.c.number
    oc.replace(3, "0x" + "77" * 20)
    tick(w, oc, 5)
    assert list(open_keys(w, "m10").values()) == ["page"], open_keys(w, "m10")
    oc.c.reorg(fork)
    oc.slots[3] = SLOTS[3]
    oc.stub()
    oc.vote(ref, 2)
    oc.vote(ref, 4)
    assert oc.committed(ref)
    oc.replace(3, "0x" + "77" * 20)
    tick(w, oc, 5)
    assert open_keys(w, "m10") == {}, open_keys(w, "m10")
    res = sent(w, "m10", "kind='resolved'")
    assert len(res) == 1 and REORG_TEXT in res[0]["text"], [r["text"][:100] for r in res]
    assert [k for k in pw(w, "m10") if k[2] != "resolved"] == [pw(w, "m10")[0]], pw(w, "m10")


def test_m10_rotation_with_no_open_round_is_silent_through_restart(tmp_path):
    oc, w = oracle_worker(tmp_path, ids=("m10",))
    tick(w)
    oc.round()
    tick(w, oc, 3)
    oc.replace(2, "0x" + "66" * 20)
    tick(w, oc, 2)
    w = restart(tmp_path, w, oc, ids=("m10",))
    tick(w, oc, 2)
    assert open_keys(w, "m10") == {} and sent(w, "m10") == []


# ------------------------------------------------------------------------------------------------ M11


def test_m11_proof_api_dying_mid_comparison_is_bounded(tmp_path):
    """The proof API answers once, then times out for every request: `proof_api_abort_after` (3) consecutive transport
    failures make it unreachable. The comparison must stop asking (each real timeout is 15 s; 530 clusters would
    block the single-threaded worker for ~2 h)."""
    u = UpdaterWorld(tmp_path, n=20)
    tick(u.w)
    calls = {"n": 0}
    real = u.api.get

    def flaky(url, timeout):
        calls["n"] += 1
        if calls["n"] > 1:
            from checks.m11_updater_liveness import ProofApiError
            raise ProofApiError("TimeoutError: timed out")
        return real(url, timeout)

    u.w.checks["m11"].http_get = flaky
    u.round(updates=())
    u.close()
    assert open_keys(u.w, "m11").get("liveness") == "warn"
    assert calls["n"] <= 1 + 3 + 1, f"{calls['n']} proof GETs for 20 clusters after the API died"


def test_m11_revert_rate_boundary(tmp_path):
    """> 10 %: 1 revert of 10 (=10 %) silent; 1 revert of 9 (11.1 %) warn. Reverts count in the denominator."""
    u = UpdaterWorld(tmp_path, n=10)
    tick(u.w)
    ref = u.round(updates=tuple(range(9)))
    u.fail("IncorrectClusterState", ref, i=9)
    u.close()
    assert "revert-rate" not in open_keys(u.w, "m11"), open_keys(u.w, "m11")
    tick(u.w, u.oc, 2400)
    ref = u.round(updates=tuple(range(8)))
    u.fail("IncorrectClusterState", ref, i=9)
    u.close()
    assert open_keys(u.w, "m11").get("revert-rate") == "warn", open_keys(u.w, "m11")
    inc = [r for r in u.w.db.conn.execute("SELECT evidence FROM incidents WHERE key='revert-rate' AND state='open'")]
    assert "m1_handoff" in inc[0]["evidence"]


def test_m11_zero_round_with_empty_api_objects_is_unknown_not_clean(tmp_path):
    """The API answers `{}` for every cluster: no leaf, UNKNOWN; the zero round stays warn, the proof is not 'all
    match', and the evidence lists the unknown clusters."""
    u = UpdaterWorld(tmp_path)
    tick(u.w)
    u.w.checks["m11"].http_get = lambda url, timeout: {}
    u.round(updates=())
    u.close()
    assert open_keys(u.w, "m11") == {"liveness": "warn"}
    body = u.w.db.conn.execute("SELECT body FROM incidents WHERE key='liveness'").fetchone()[0]
    assert "all" not in body.split("Proof API")[-1][:20] and "match their leaf" not in body.split("Proof API:")[-1][:30], body


def test_m11_reorg_removing_the_round_that_aged_a_mismatch(tmp_path):
    """state-mismatch warns once a mismatch is seen at two consecutive roots. A reorg beyond the confirmation window
    removes the second round's RootCommitted (and its evaluation): the canonical chain now has the mismatch at ONE
    root only, i.e. not 'older than one round'. README 6.2: the key's evidence left the canonical chain -> resolved
    by the re-find rule (or kept only with a definite re-evaluation); it must not stay open on no evidence."""
    u = UpdaterWorld(tmp_path, confirmations=2)
    tick(u.w, u.oc, 4)
    u.round(updates=(0,))
    u.set_eb(32, leaves={u.cids[1]: 64})
    u.close()
    assert "state-mismatch" not in open_keys(u.w, "m11")
    tick(u.w, u.oc, 50)
    fork = u.c.number
    u.round(updates=(0,))
    u.set_eb(32, leaves={u.cids[1]: 64})
    u.close()
    assert open_keys(u.w, "m11") == {"state-mismatch": "warn"}
    u.c.reorg(fork)  # the second commit and everything after leave the chain
    u.api.ref = u.w.db.meta("check:m11:state_evals")[0]["ref"]
    u.c.mine(20)
    tick(u.w, u.oc, 5)
    evals = u.w.db.meta("check:m11:state_evals")
    assert len(evals) == 1
    assert "state-mismatch" not in open_keys(u.w, "m11"), (open_keys(u.w, "m11"), evals)


def test_m11_zero_round_older_than_history_rounds_after_downtime(tmp_path):
    """Class 6 (cap): the worker is down for 7 rounds; the first of them had 0 updater txs (warn), the rest are
    normal. After restart the zero round must still be reported once (open + resolution)."""
    u = UpdaterWorld(tmp_path)
    tick(u.w)
    u.round(updates=())
    u.c.elapse(400)
    for _ in range(7):
        u.c.elapse(2000)
        u.round(updates=(0,))
        u.c.elapse(400)
    w = u.restart()
    tick(w, u.oc, 2)
    kinds = [(r["ikey"], r["kind"]) for r in sent(w, "m11")]
    assert ("liveness", "open") in kinds, kinds


def test_m11_restart_idempotent_with_two_zero_rounds_page(tmp_path):
    u = UpdaterWorld(tmp_path)
    tick(u.w)
    u.round(updates=())
    u.close()
    tick(u.w, u.oc, 2000)
    u.round(updates=())
    u.close()
    assert open_keys(u.w, "m11") == {"liveness": "page"}
    n = len(sent(u.w, "m11"))
    w = u.restart()
    tick(w, u.oc, 3)
    tick(w, u.oc, 3, seconds=600)
    assert len(sent(w, "m11")) == n and open_keys(w, "m11") == {"liveness": "page"}


def test_m11_zero_round_within_history_rounds_after_downtime_control(tmp_path):
    """Control: the same downtime with 5 normal rounds after the zero round (inside history_rounds = 6)."""
    u = UpdaterWorld(tmp_path)
    tick(u.w)
    u.round(updates=())
    u.c.elapse(400)
    for _ in range(5):
        u.c.elapse(2000)
        u.round(updates=(0,))
        u.c.elapse(400)
    w = u.restart()
    tick(w, u.oc, 2)
    kinds = [(r["ikey"], r["kind"]) for r in sent(w, "m11")]
    assert ("liveness", "open") in kinds and ("liveness", "resolved") in kinds, kinds
```
<!-- END test_qa2.py -->

Extract and run (worktree root):

```sh
mkdir -p /tmp/qa-cp2b-2/tests && python3 -c "import pathlib,re;s=pathlib.Path('plans/monitoring-service/qa/cp2b-attempt-2.md').read_text();[pathlib.Path(p).write_text(s.split('<!-- BEGIN '+n+' -->\n\x60\x60\x60python\n',1)[1].split('\n\x60\x60\x60\n<!-- END '+n+' -->',1)[0]+'\n') for n,p in (('test_qa2.py','/tmp/qa-cp2b-2/tests/test_qa2.py'),('qfalsify.py','/tmp/qa-cp2b-2/qfalsify.py'))]"
PYTHONPATH=reconcile:reconcile/tests reconcile/.venv/bin/python -X pycache_prefix=/tmp/qa-cp2b-2/pyc -m pytest -q -p no:cacheprovider /tmp/qa-cp2b-2/tests/test_qa2.py
```

Expected at `ed421e6`: exit 1, 5 failed / 11 passed.

## Appendix B — LIVE scripts

`/tmp/qa-cp2b-2/live/qrpc.py` (identical to `rpc.py`, which `fullscan.py` imports):

```python
import json, urllib.request, sys, time
PROXY = "0xDD9BC35aE942eF0cFa76930954a156B3fF30a4E1"
VIEWS = "0xafE830B6Ee262ba11cce5F32fDCd760FFE6a66e4"
WRP = "0xc0516bad4be527b56ad33b7087029ffee230e390e2f2d1076597820c9e170b67"
RC = "0x6965b9c8509097ad68ba0eb17fcb9436f4a72a057fe5ac556737580bce80d840"
def call(url, method, params, timeout=40):
    req = urllib.request.Request(url, json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
                                 {"Content-Type": "application/json", "User-Agent": "qa-cp2b/2"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        b = json.load(r)
    if "error" in b:
        raise RuntimeError(f"{method}: {b['error']}")
    if b.get("result") is None:
        raise RuntimeError(f"{method}: null result {b}")
    return b["result"]
def logs(url, a, b, topics=None):
    return call(url, "eth_getLogs", [{"address": PROXY, "fromBlock": hex(a), "toBlock": hex(b), "topics": [topics or [WRP, RC]]}])
```

`/tmp/qa-cp2b-2/live/fullscan.py`:

```python
"""Read-only: every WRP/RootCommitted log of the SSV proxy in [a, b], 50-block chunks, round-robin over public
archive endpoints; records the endpoint of every chunk. Usage: fullscan.py a b out.json"""
import json, sys, time, concurrent.futures, hashlib
sys.path.insert(0, "/tmp/qa-cp2b-2/live")
import rpc
URLS = ["https://eth.drpc.org", "https://eth-pokt.nodies.app"]
a, b, out = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
chunks = [(s, min(s + 49, b)) for s in range(a, b + 1, 50)]
def get(i_ch):
    i, (s, e) = i_ch
    last = None
    for attempt in range(10):
        u = URLS[(i + attempt) % len(URLS)]
        try:
            r = rpc.logs(u, s, e)
            if not isinstance(r, list):
                raise RuntimeError("non-list")
            return (s, e, u, r)
        except Exception as exc:
            last = exc
            time.sleep(1 + 2 * attempt)
    raise SystemExit(f"chunk {s}-{e} failed: {last}")
t0 = time.time()
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
    res = list(ex.map(get, enumerate(chunks)))
logs = sorted([l for *_, r in res for l in r], key=lambda x: (int(x["blockNumber"], 16), int(x["logIndex"], 16)))
endpoints = {}
for s, e, u, r in res:
    endpoints[u] = endpoints.get(u, 0) + 1
canon = [{k: l[k] for k in ("blockNumber", "logIndex", "transactionHash", "topics", "data", "blockHash")} for l in logs]
json.dump({"range": [a, b], "chunks": len(chunks), "endpoints": endpoints, "logs": logs, "seconds": time.time() - t0}, open(out, "w"))
print(json.dumps({"range": [a, b], "chunks": len(chunks), "endpoints": endpoints, "logs": len(logs),
                  "sha256_canon": hashlib.sha256(json.dumps(canon, sort_keys=True).encode()).hexdigest(),
                  "seconds": round(time.time() - t0, 1)}))
```

`/tmp/qa-cp2b-2/live/cluster.py`:

```python
import sys, json, time, urllib.request
sys.path[:0] = ['/tmp/qa-cp2b-2/live', 'reconcile']
import rpc
from eb_events import TOPIC0
CID = '0xc6d52a5b3f6d7945f0d6bd31a2962ac729ecefbee564687315278ac85372574b'
OWNER = '0x5ce6430e01a9cd1b9c5389211a3e001fb0e215bd'
u = 'https://ethereum-rpc.publicnode.com'
names = {v: k for k, v in TOPIC0.items()}
proof = json.load(urllib.request.urlopen(urllib.request.Request(f'https://ssv-oracle.ssv.network/api/v1/proof/{CID}', headers={'user-agent': 'qa'}), timeout=30))
print('proof', {k: proof.get(k) for k in ('referenceBlock', 'effectiveBalance', 'merkleRoot')})
h = int(rpc.call(u, 'eth_blockNumber', []), 16) - 20
out = []
s = 26239500
while s <= h:
    e = min(s + 999, h)
    out += rpc.call(u, 'eth_getLogs', [{'address': rpc.PROXY, 'fromBlock': hex(s), 'toBlock': hex(e), 'topics': [None, '0x' + OWNER[2:].rjust(64, '0')]}])
    s = e + 1; time.sleep(0.3)
print('head-20', h, 'owner logs since 26239500:', len(out))
for l in out:
    print(int(l['blockNumber'], 16), int(l['logIndex'], 16), names.get(l['topics'][0], l['topics'][0][:12]), l['transactionHash'])
```

`/tmp/qa-cp2b-2/live/cluster4.py`:

```python
import sys
sys.path[:0] = ['reconcile', '/tmp/qa-cp2b-2/live']
import qrpc as rpc
from eb_events import decode_log
from checks.m11_updater_liveness import get_eb_calldata
from types import SimpleNamespace
CID = 'c6d52a5b3f6d7945f0d6bd31a2962ac729ecefbee564687315278ac85372574b'
OWNER = '0x5ce6430e01a9cd1b9c5389211a3e001fb0e215bd'
arch = 'https://eth.drpc.org'
for b in (26239661, 26239692):
    for l in rpc.call(arch, 'eth_getLogs', [{'address': rpc.PROXY, 'fromBlock': hex(b), 'toBlock': hex(b), 'topics': [None, '0x' + OWNER[2:].rjust(64, '0')]}]):
        ev = decode_log(l)
        print(b, ev.kind, ev.cluster_id.hex()[:12], 'ref', ev.ref_block, 'eb', ev.eb, 'validators', ev.cluster.validator_count)
        if ev.cluster_id.hex() == CID:
            ci = SimpleNamespace(owner=OWNER, operator_ids=ev.operator_ids, cluster=ev.cluster)
            res = rpc.call(arch, 'eth_call', [{'to': rpc.VIEWS, 'data': '0x' + get_eb_calldata(ci).hex()}, hex(26241742)])
            print('  our cluster: getEffectiveBalance at 26241742 (before the removals) =', int(res[2:66], 16))
```

Only `eth_blockNumber`, `eth_getLogs`, `eth_call`, `eth_getStorageAt` (via the implementer's `live_m11_leaves.py`) and HTTPS GETs to api.ssv.network / ssv-oracle.ssv.network were sent. No transaction, no secret.

## Appendix C — `/tmp/qa-cp2b-2/qfalsify.py`

<!-- BEGIN qfalsify.py -->
```python
import subprocess, pathlib, json, tempfile, os
os.chdir('/Users/marco/orca/workspaces/ssv-network-monitor/ms-qa-cp2b-2')
py = 'reconcile/.venv/bin/python'; C = pathlib.Path('reconcile/checks'); Q = '/tmp/qa-cp2b-2/tests/test_qa2.py'
M = [
 ('QF-book-reorg', 'm9_oracle_common.py', 'if ctx.reorg_ancestor is not None and r["evidence_block"] > ctx.reorg_ancestor:',
  'if False:', Q + '::test_m10_rotation_during_open_round_then_reorg_moves_it_after_the_commit'),
 ('QF-m9-cadence-clock', 'm9_oracle_quorum.py', 'age = self.now - since_at', 'age = self.times(block) - since_at',
  Q + '::test_m9_cadence_restart_and_clock_jumps_no_duplicate'),
 ('QF-m9-commit-order', 'm9_oracle_quorum.py', 'if v.root == rnd.commit.root and v.pos < rnd.commit.pos]',
  'if v.root == rnd.commit.root]', 'reconcile/tests/test_m9.py::test_qa1_control_proposal_after_commit_is_not_counted_as_quorum'),
 ('QF-m10-open-at-event', 'm10_oracle_rotation.py', 'if before and hist.closing_event(rnd, before=ev.pos) is None:',
  'if before and hist.closing_event(rnd) is None:', Q + '::test_m10_rotation_during_open_round_then_reorg_moves_it_after_the_commit'),
 ('QF-m11-gt-vs-ge', 'm11_updater_liveness.py', 'if rate <= self.config["revert_rate"]:', 'if rate < self.config["revert_rate"]:',
  Q + '::test_m11_revert_rate_boundary'),
 ('QF-m11-empty-inventory', 'm11_updater_liveness.py', 'if not clusters and only is None:', 'if False:',
  'reconcile/tests/test_m11.py::test_empty_inventory_is_deferred_not_clean'),
]
def pt(node):
    r = subprocess.run([py, '-X', 'pycache_prefix=' + tempfile.mkdtemp(), '-m', 'pytest', '-q', '-p', 'no:cacheprovider', node],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env={**os.environ, 'PYTHONPATH': 'reconcile:reconcile/tests'})
    return r.returncode, r.stdout.strip().splitlines()[-1]
for name, f, old, new, node in M:
    p = C / f; orig = p.read_bytes(); s = orig.decode(); assert s.count(old) == 1, (name, s.count(old))
    try:
        p.write_text(s.replace(old, new)); red = pt(node)
    finally:
        p.write_bytes(orig)
    green = pt(node)
    por = subprocess.run(['git', 'status', '--porcelain'], stdout=subprocess.PIPE, text=True).stdout
    print(json.dumps({'id': name, 'file': f, 'old': old, 'new': new, 'test': node.split('::')[-1], 'red': red, 'green': green,
                      'restored': p.read_bytes() == orig, 'porcelain': por}))
```
<!-- END qfalsify.py -->
