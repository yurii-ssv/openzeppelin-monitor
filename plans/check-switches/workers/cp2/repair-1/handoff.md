# CP2 repair 1 — handoff (check-switches, D15)

Run `run_3869f8caafaa`, task `task_b45d25b0c809`. Worker: Claude Opus 5.5 (`claude-opus-5-5`), effort high, in Orca
worktree `/Users/marco/orca/workspaces/ssv-network-monitor/cs-cp2-r1`. Role document `agents/checkpoint-implementer.md`
(sha256 `6548b79b…`), defect classes `qa/DEFECT-CLASSES.md` (`7daa55f6…`) and QA report `qa/cp2-round-1.md`
(`6d23504a…`) verified against the hashes in the packet before editing.

- **Base:** `718606c5ee2d43c260240101c06642340cabc1a0` (`cs/cp2`), verified `git rev-parse HEAD` and a clean tree
  before editing. Branch `cs/cp2-r1` created from it (the Orca worktree had opened `mtabasco/cs-cp2-r1` at the same
  commit; it is left untouched). `cs/cp2` and `plan/check-switches` were not moved.
- **Head:** the single repair commit on `cs/cp2-r1` that contains this file (its sha is in the `worker_done` message;
  this file cannot name its own commit).
- Not pushed; no PR; no Slack/GitHub post; no mainnet call; no Docker; no helper agent.

## Mechanism (D15)

One durable notion: a check's **silent boundary** B = max(`meta silent:<id>`, `phase.history_block`). `silent:<id>`
(new, an integer block) is written in the transaction that completes a per-check silent evaluation at its pin: the
catch-up run that moves `catchup:<id>` to `first_live`, and the global silent run of a check re-enabled during the
backfill (D15 (c)). The global boundary needs no new key: it is `phase.history_block` (also on databases upgraded from
`9e94a1a`, which have no `silent:*` rows). Events at or below B are history; above B, live.

1. **Repeat after a reorg below B (D15 (a), (b)) — `Worker._rearm_silence`, called at the start of each check's turn in
   `run_checks`.** The check's event cursor is the durable "evaluated through" mark (a reorg rewinds it to the
   ancestor; every committed run sets it to its block). When `cursor < B`:
   - phase `live`, no record: a record `catchup:<id>` = `{state: pending, history_block: B, since, cause: "reorg"}`
     is written (and a stale `phase.first_live` entry removed), so the existing D9 machinery runs a silent run pinned
     at B (reasons `backfill`, `force`, plus `reorg`), then a forced live run (`live`, `force`) at a later block and
     deletes the record; `silent:<id>` is rewritten with the same B. A record in `first_live` is set back to `pending`
     (this replaces the CP2 code in `check_reorg`, which only existed while a record existed: QF1's cause).
   - phase `backfill`, check in `phase.evaluated`: it is removed from `evaluated` and `first_live` (list contents
     only), so the global path repeats its silent run, pinned at `max(history_block, silent:<id>)`.
   - A check that never completed a run (`run:<id>` without `block`) is not concerned: D9 (a)/(d) decide for it (so
     `backfill.enabled: false` still means "first run alerts as at base").
2. **The repeat does not judge what live runs saw (`run_check`, `deferred`).** In any muted run (global silent or
   pending catch-up), the open incidents of the check last seen above the run's block can only exist after a repeat
   (alerts of earlier live runs): their findings and resolutions are not applied, they are left out of
   `ctx.reorg_keys` and of the `resolve_on_absence` sweep, and when the run commits `reorg:<id>` becomes the run's
   block instead of being cleared (and `pending` keeps `reorg`). The forced live run after it is therefore a reorg run
   from B: re-found incidents stay open under their id with no message, not re-found ones are resolved once as
   "evidence left the canonical chain" (§6.2 item 6). Without this, the repeat would resolve every live incident above
   B and the live run would page them again (falsifiers F4, F5, F11).
3. **No new unmute path.** A muted incident at or below B (`notify = alerts = 0`) is re-found only by a silent run
   (muted findings never unmute); `alerts.py` is unchanged (not needed).
4. **Re-enabled while backfilling (D15 (c) + sibling).** In `_plan_catchups`, phase `backfill`, once
   `history_block` is known: every built check carrying the D7 marker is removed from `evaluated`/`first_live` and gets
   `catchup:<id>` = `{pending, history_block: null, cause: "reenabled"}`. The global path pins its new silent run at
   `_backfill_pin` = max(`history_block`, `silent:<id>`, the record's pin = the checkpoint of its first step, set once
   by `_catchup_block`). When that run commits: `evaluated` + `first_live` (as at base), `silent:<id>` = the pin, the
   record deleted; a new live-gate line skips its live run while `block <= B`, so its first live run is at the next
   block, forced. Sibling covered too: a check disabled **before** `history_block` was set (never evaluated) and
   re-enabled after it while still backfilling (its disabled interval lies after `history_block`;
   `test_check_disabled_before_the_caught_up_checkpoint_and_reenabled_while_backfilling`). Before `history_block`
   exists nothing changes (the global pin is later than the re-enable): `test_reenabled_while_backfilling_clears_the_
   marker_without_a_record` stays green.
5. **Pin floor.** `_catchup_block` pins at `max(checkpoint, B)`: a re-enable after a reorg onto a shorter chain stays
   `pending` until the chain passes the earlier boundary.

Why a record in phase `backfill` (point 4): D15 (c) as worded ("removed from `evaluated`/`first_live` … the global path
re-evaluates it silently") re-evaluates at `history_block`, which is **below** the disabled interval in QA's NF3 case
(event 1002 > `history_block` 1001): the next live run would still send it. Pinning at the checkpoint of the first step
(D9's rule) needs a durable per-check pin before the run commits; the existing record shape carries it and makes it
visible in `status`. Consequence for QA's original reproducer: its NF3 `[True]` case asserts, as a setup step, the
defective candidate's state "`env.rec("cu_a") is None`"; on this candidate that one line fails (a pending record
exists by design) while its oracle ("disabled-interval event not sent") holds. The permanent copy replaces that line
with D15 (c)'s own assertions (`cu_a` left `evaluated` and `first_live`; pending record, `cause: reenabled`), placed
after the oracle. **Coordinator to confirm** this reading of D15 (c) (a record visible during the backfill phase).

## State table (DEFECT-CLASSES; every key the repair adds or changes)

Tests are in `reconcile/tests/test_history_silence.py` unless prefixed; "sweep" = `test_catchup.py::
test_seeded_catchup_sweep` (30 seeds; now with deep reorgs at or below every boundary and `max_log_range` 1).

| Key / state | (a) empty | (b) error | (c) partial | (d) reorg depth 1 / N, incl. resolved keys | (e) restart between detection and handling | (f) same-block order | (g) time anchoring |
|---|---|---|---|---|---|---|---|
| meta `silent:<id>` = B (new; int) | Absent until a per-check silent evaluation completes; then the boundary is `phase.history_block` (global) or none (`test_never_run_check_…`: no repeat without a completed run) | Written only in the committing transaction of the silent run (a failed run writes nothing): `test_repeat_survives_…` (`silent:cu_a` = pin only after success) | Same transaction as the run's findings, cursor and record change | Never moved by a reorg; the reorg rewinds the cursor and the repeat re-evaluates up to B: depth to the history event with confirmations 0/2/12 (`test_nf1_…`), depth 1 at the pin (`test_nf2_…`), deep onto a shorter chain (`test_reenable_after_a_reorg_onto_a_shorter_chain_…`), depth 1–4 and "deep" in the sweep. Ancestor ≥ B: no repeat (`test_reorg_above_the_boundary_needs_no_repeat`) | Durable: `test_nf1_…` restarts after the record is deleted, then reorgs | n/a (a block number) | Anchored at the pin (checkpoint of the first step, or `history_block`), never at the clock |
| meta `catchup:<id>` (changed) — new `cause: "reorg"` record | Repeat with no event and no finding: `test_condition_healed_…` (nothing to send) | Failing repeat: record kept `pending` at B, retried every step, `check-error` health, the other check's page sent the same step (A4): `test_repeat_survives_…[percheck/global]` | Written in its own transaction before any run; the run's state change commits with the run | Re-armed from the rewound cursor at every depth (rows above); a `first_live` record set back to `pending` (CP2's case, `test_catchup.py::test_reorg_below_the_catch_up_block_before_the_first_live_run_stays_silent`, `test_nf2_…[percheck]`) | `test_repeat_survives_…`: record identical after a restart and after a same-block failed retry; `status()` shows it | The repeat covers events up to and **including** B; the event at B stays silent (`test_nf2_…`: replayed event at the pin, live event at pin+1 sent once) | Pinned at B, not at the step's checkpoint |
| meta `catchup:<id>` (changed) — written in phase `backfill` (D15 (c)) | Not written before `history_block` exists (`test_catchup.py::test_reenabled_while_backfilling_clears_the_marker_without_a_record`) | A failing re-evaluation keeps it `pending` and pinned (global path retry, `_backfill_pin`); the other checks unaffected | Written in bootstrap's transaction with the marker clear (CP2 rollback test `test_catchup_planning_and_d7_marker_clear_roll_back_together` green) | A reorg before its run: the pin (`max(checkpoint, B)`) holds; after it: row `silent:<id>` | Survives restarts; dropped by a disabling start (D7): `test_reenabled_while_backfilling_and_disabled_again_…` | Its events up to and including the pin are silent; the next block is live (`test_nf3_…[True]`: one run at the pin, the live event of the next step sent once) | Pinned at the checkpoint of the first step after the start (D9's rule) |
| `catchup:<id>.history_block` floor (changed) | n/a | n/a | n/a | `max(checkpoint, B)`: shorter chain after a reorg while disabled: `test_reenable_after_a_reorg_onto_a_shorter_chain_…`; sweep derives `max(checkpoint, previous floor)` and asserts equality with the record | Set once, durable (unchanged) | n/a | Never below an earlier boundary |
| `phase.evaluated` / `phase.first_live` list contents (changed; shape unchanged) | No reorg below `history_block`, no re-enable in backfill: untouched (`test_backfill.py` byte-identical, 67 green) | Removal is its own transaction; the repeat is retried by the global path like any silent run | Only the affected id is removed | Backfill phase, evaluated check, reorg below `history_block` → removed, silent run repeated, live event re-included not re-sent, new event once: `test_reorg_below_the_caught_up_checkpoint_while_backfilling_…`. Live phase: a `first_live` entry is removed when the `reorg` record is written (`test_nf2_…[global]`) | Durable list contents: a restart sees the removal and repeats | n/a | n/a |
| meta `reorg:<id>` after a silent run (changed) | No deferred incident: cleared at commit as before (all CP2/CP1 reorg tests green) | A failing repeat keeps the original ancestor marker (rollback) | Same transaction | With deferred incidents (open, last seen above the run's block): set to the run's block, so the live run judges them: re-found → same id, nothing sent (`test_repeat_does_not_resolve_or_resend_…`, 4 variants, incl. a level true or false at B); orphaned → resolved once, no re-alert after 7 h (`test_live_event_orphaned_…`); `resolve_on_absence` keeps them (`test_repeat_keeps_an_absence_resolved_…`) | Durable: `Worker.__init__` replays it (`on_reorg(B)`, reason `reorg`) as for any marker | n/a | Re-alerts of the deferred incidents held until the live run commits (existing `realert` rule on the marker) |
| Muted incidents at or below B (`notify = alerts = 0`) — unmute rule (behaviour, `alerts.py` unchanged) | n/a | n/a | n/a | Re-found only by the silent repeat: same id, still `notify = alerts = 0` after the reorg (`test_nf1_…`); a resolved key replayed and healed again stays unsent (`test_condition_healed_…`); a condition the new chain turns on below B and still true at the live run is sent once (`test_condition_new_…`) | n/a | n/a | n/a |

The CP1 rows and the other CP2 rows (D7 marker, `phase.first_live` stale-entry removal at re-enable, the D14 config
cell, `backfill.enabled` switched off mid catch-up) are unchanged; `test_switches.py` (82) and `test_catchup.py`
(69) are green on this head.

## Per finding

| Finding | Result | Evidence |
|---|---|---|
| QF1 / NF1 (per-check and global, ×9) | FIXED: no message after record deletion / `first_live` consumption and a deep reorg; same incident id stays muted | `test_nf1_…[new/reenabled/global × 0/2/12]` green; red on `718606c` (`red-on-718606c.txt`) with the QA message (`['ev:1003']`, `['ev:1005']`, `['ev:1015']`, `['ev:1001']`); global ×3 red on `9e94a1a` (KNOWN RED provenance, `known-red-global-on-9e94a1a.txt`) |
| QF2 / NF2 global | FIXED: the global silent run is repeated (live phase: `cause: reorg` record; backfill phase: leaves `evaluated`) | `test_nf2_…[global]` green (only `ev:1005` sent); red on `718606c` and `9e94a1a`: `['ev:1004', 'ev:1005']`; backfill-phase variant `test_reorg_below_the_caught_up_checkpoint_while_backfilling_…` red on both (`[('open','ev:1004'), ('open','ev:1007')]`) |
| QF3 / NF3 | FIXED (with the record reading above; coordinator to confirm) | `test_nf3_…[True]` green; red on `718606c`: `disabled-interval event sent on first live run: ['ev:1002']`. QA's original file on this candidate: 20 pass, 1 fail = that setup line (`candidate-qa-probes.txt`) |
| QA controls (10) | PASS, unchanged oracles | `nf2[percheck]`, `nf3[False]`, 6 partial/failed, empty success, planning rollback: green on the candidate and on `718606c` |
| QN1 (`.gitignore`) | Not this worker's (coordinator) | — |
| DF1 | Still closed: CP1 probes `test_cp1_independent.py` + `test_cp1_siblings.py` (QA round 2 files, unchanged) 107 pass | `candidate-qa-probes.txt` |

## V rows re-run on this head

All runs on the candidate tree (worktree; file hashes in `suite.txt`). Full suite **2,505 passed** (= 2,464 + 41 new,
0 skipped, exit 0); `ruff check reconcile`: all checks passed. Targeted `test_history_silence.py test_catchup.py
test_backfill.py test_switches.py`: 259 passed.

| V | Result | What was re-run / changed |
|---|---|---|
| V1.1–V1.6 | PASS | `test_switches.py` 82 green (unchanged file); QA's CP1 probes (107) green on this head |
| V1.7 | PASS | suite/ruff above; guarded paths unchanged vs `718606c` (`suite.txt`: empty `git diff --stat` for `alerts.py`, `test_backfill.py`, `test_m*.py`, `test_switches.py`, `checks/m*.py`, `registry.py`, `db.py`, `metrics.py`, `harness.py`, `config.example.json`) |
| V2.1 | PASS | `test_catchup.py` V2.1 test green; NF1 `[reenabled-*]` adds "no message ever for disabled-period events" after record deletion + deep reorg |
| V2.2 | PASS | (a)–(f) tests green; (a) extended by NF1 `[new-*]`; (c)/(f) extended by NF3 and its sibling; (d) extended by `test_never_run_check_with_backfill_off_…` on a database with `history_block` |
| V2.3 | PASS | CP2 boundary tests green; the repeat's own failure/restart/same-block boundaries: `test_repeat_survives_…` |
| V2.4 | PASS | CP2 D11 test green; deep-reorg lifetime extension: NF1, repeat/orphaned/new/healed tests, sweep without the former exclusion |
| V2.5 | PASS | CP2 A4 tests green; A4 during a failing repeat: `test_repeat_survives_…` (cu_b's page in the same step), QA's 6 partial/failed controls |
| V2.6 | PASS | CP2 flip tests green; flip inside backfill after a silent run: NF3 `[True]`; disable again during backfill: `test_reenabled_while_backfilling_and_disabled_again_…` |
| V2.7 | PASS | `test_backfill.py` sha256 `28da7d49…` (byte-identical) and 67 green; no `test_m*.py`/`checks/m*.py` edit; phase meta keys unchanged (only list contents); red runs below |
| V2.8 | PASS (claimed; QA to re-map) | `docs-checklist.md` maps every changed claim (deploy/README D17 + re-enable + backfill-phase paragraphs, reconcile/README, checks/README §3/§4/§6.2/§7) to tests, incl. the deep-reorg case |

## Red evidence (DEFECT-CLASSES 12)

`run_evidence.py red` (archives of the revision + the two test files):

- **On `718606c` (CP2 candidate):** 32 failed / 78 passed (`red-on-718606c.txt`). Failing for the behavioural reason
  (a message that must not be sent, or a missing resolution): NF1 ×9, NF2 global, NF3 `[True]`, repeat ×4,
  orphaned ×2 (no "resolved", a history `open` instead), condition-new ×2, condition-healed ×2, repeat-survives ×2
  (`['ev:1003']` / `['ev:1001']` sent after recovery), backfill-phase repeat, NF3 sibling (`['ev:1002']`),
  shorter-chain pin (`['ev:1003']`); sweep seeds 12, 17, 18, 20 (history event at or below the floor sent). Not
  behavioural (state only): sweep seed 13 (record pinned at 1007, below the earlier boundary 1008: the F6 floor) and
  `test_reenabled_while_backfilling_and_disabled_again_…` (a control: no backfill-phase record exists on `718606c`).
  Green on `718606c` by design (controls / new-mechanism tests): QA's 10 controls, `test_reorg_above_the_boundary_…`
  ×2, `test_never_run_check_with_backfill_off_…`, `test_repeat_keeps_an_absence_resolved_…` (no repeat exists there;
  it is falsified by F4/F11 instead).
- **On `9e94a1a` (code base), global subset:** 11 failed / 1 passed (`known-red-global-on-9e94a1a.txt`): NF1 global ×3,
  NF2 global, repeat global ×2, orphaned global, condition-new/healed global, repeat-survives global, backfill-phase
  repeat — all on a history message; the control `test_reorg_above_the_boundary_…[global]` passes. KNOWN RED
  provenance: the global D17 path had the gap before CP1/CP2.

## Falsifiers (one sabotage per mechanism, `run_evidence.py falsifiers`, each in a scratch archive)

| # | Sabotage | Named tests reddened |
|---|---|---|
| F1 | no `reorg` record in phase live | 25: NF1 ×9, NF2 global, repeat ×4, orphaned ×2, condition-new ×2, condition-healed ×2, repeat-survives ×2, sweep 12, 18, 20 |
| F2 | no removal from `evaluated` in phase backfill | `test_reorg_below_the_caught_up_checkpoint_while_backfilling_…` |
| F3 | no backfill-phase record at re-enable | NF3 `[True]`, NF3 sibling, disabled-again control |
| F4 | no deferral | 11: repeat ×4, backfill-phase repeat, absence test, sweep 12, 15, 16, 17, 18 |
| F5 | marker cleared instead of handed to the live run | 8: orphaned ×2 (no resolution), repeat ×4 (live run not a reorg run), sweep 16, 18 |
| F6 | pin without the floor | shorter-chain test, sweep 13 |
| F7 | repeat also for never-run checks | `test_never_run_check_with_backfill_off_…` |
| F8 | `silent:<id>` not written | 19: NF1 per-check ×6, repeat per-check ×2, orphaned/condition-new/condition-healed per-check, repeat-survives ×2, shorter-chain pin, sweep 12, 13, 17, 18, 20 |
| F9 | global pin ignores the record | NF3 `[True]`, NF3 sibling |
| F10 | no boundary live gate | NF3 `[True]` (a live run at the pin), NF3 sibling |
| F11 | `resolve_on_absence` resolves deferred keys | `test_repeat_keeps_an_absence_resolved_incident_seen_above_the_boundary` |

Each falsifier file records the sabotage, the exit code and every failing assertion. All scratch copies were under
`/private/tmp/cs-cp2-r1-*` and removed (`scratch-restoration.txt`: 14 × `removed=True`; none left). The worktree was
never sabotaged.

## `git diff --stat 718606c..HEAD` (all paths in the commit; this handoff file excluded from the count)

```
 deploy/README.md                                                                           |  25 ++--
 plans/check-switches/workers/cp2/repair-1/candidate-qa-probes.txt                          | 140 +++++++++++++++++++++++
 plans/check-switches/workers/cp2/repair-1/docs-checklist.md                                |  32 ++++++
 plans/check-switches/workers/cp2/repair-1/falsifier-F1-no-live-repeat.txt                  | 144 +++++++++++++++++++++++
 plans/check-switches/workers/cp2/repair-1/falsifier-F10-no-boundary-live-gate.txt          | 121 ++++++++++++++++++++
 plans/check-switches/workers/cp2/repair-1/falsifier-F11-absence-resolves-deferred-keys.txt | 120 +++++++++++++++++++
 plans/check-switches/workers/cp2/repair-1/falsifier-F2-no-backfill-repeat.txt              | 120 +++++++++++++++++++
 plans/check-switches/workers/cp2/repair-1/falsifier-F3-no-backfill-reenable-record.txt     | 122 ++++++++++++++++++++
 plans/check-switches/workers/cp2/repair-1/falsifier-F4-no-deferral.txt                     | 130 +++++++++++++++++++++
 plans/check-switches/workers/cp2/repair-1/falsifier-F5-marker-cleared-not-handed-over.txt  | 127 +++++++++++++++++++++
 plans/check-switches/workers/cp2/repair-1/falsifier-F6-no-floor-on-the-pin.txt             | 121 ++++++++++++++++++++
 plans/check-switches/workers/cp2/repair-1/falsifier-F7-repeat-for-never-run-checks.txt     | 120 +++++++++++++++++++
 plans/check-switches/workers/cp2/repair-1/falsifier-F8-no-durable-boundary.txt             | 138 ++++++++++++++++++++++
 plans/check-switches/workers/cp2/repair-1/falsifier-F9-backfill-pin-ignores-record.txt     | 121 ++++++++++++++++++++
 plans/check-switches/workers/cp2/repair-1/known-red-global-on-9e94a1a.txt                  |  32 ++++++
 plans/check-switches/workers/cp2/repair-1/red-on-718606c.txt                               | 151 ++++++++++++++++++++++++
 plans/check-switches/workers/cp2/repair-1/run_evidence.py                                  | 120 +++++++++++++++++++
 plans/check-switches/workers/cp2/repair-1/scratch-restoration.txt                          |  14 +++
 plans/check-switches/workers/cp2/repair-1/suite.txt                                        |  29 +++++
 reconcile/README.md                                                                        |  10 +-
 reconcile/checks/README.md                                                                 |  23 +++-
 reconcile/eb_reconcile.py                                                                  | 145 ++++++++++++++++++-----
 reconcile/tests/test_catchup.py                                                            |  31 ++---
 reconcile/tests/test_history_silence.py                                                    | 633 +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
 24 files changed, 2710 insertions(+), 59 deletions(-)
```

## `git diff --stat d7b3da6..HEAD` (CP2 base → repair head; this handoff file excluded from the count)

```
 deploy/README.md                                                                           |  77 +++++++-
 plans/check-switches/workers/cp2/docs-checklist.md                                         |  75 ++++++++
 plans/check-switches/workers/cp2/falsifiers.txt                                            | 275 ++++++++++++++++++++++++++++
 plans/check-switches/workers/cp2/handoff.md                                                | 165 +++++++++++++++++
 plans/check-switches/workers/cp2/red-on-cp1.txt                                            | 244 +++++++++++++++++++++++++
 plans/check-switches/workers/cp2/repair-1/candidate-qa-probes.txt                          | 140 +++++++++++++++
 plans/check-switches/workers/cp2/repair-1/docs-checklist.md                                |  32 ++++
 plans/check-switches/workers/cp2/repair-1/falsifier-F1-no-live-repeat.txt                  | 144 +++++++++++++++
 plans/check-switches/workers/cp2/repair-1/falsifier-F10-no-boundary-live-gate.txt          | 121 +++++++++++++
 plans/check-switches/workers/cp2/repair-1/falsifier-F11-absence-resolves-deferred-keys.txt | 120 +++++++++++++
 plans/check-switches/workers/cp2/repair-1/falsifier-F2-no-backfill-repeat.txt              | 120 +++++++++++++
 plans/check-switches/workers/cp2/repair-1/falsifier-F3-no-backfill-reenable-record.txt     | 122 +++++++++++++
 plans/check-switches/workers/cp2/repair-1/falsifier-F4-no-deferral.txt                     | 130 ++++++++++++++
 plans/check-switches/workers/cp2/repair-1/falsifier-F5-marker-cleared-not-handed-over.txt  | 127 +++++++++++++
 plans/check-switches/workers/cp2/repair-1/falsifier-F6-no-floor-on-the-pin.txt             | 121 +++++++++++++
 plans/check-switches/workers/cp2/repair-1/falsifier-F7-repeat-for-never-run-checks.txt     | 120 +++++++++++++
 plans/check-switches/workers/cp2/repair-1/falsifier-F8-no-durable-boundary.txt             | 138 ++++++++++++++
 plans/check-switches/workers/cp2/repair-1/falsifier-F9-backfill-pin-ignores-record.txt     | 121 +++++++++++++
 plans/check-switches/workers/cp2/repair-1/known-red-global-on-9e94a1a.txt                  |  32 ++++
 plans/check-switches/workers/cp2/repair-1/red-on-718606c.txt                               | 151 ++++++++++++++++
 plans/check-switches/workers/cp2/repair-1/run_evidence.py                                  | 120 +++++++++++++
 plans/check-switches/workers/cp2/repair-1/scratch-restoration.txt                          |  14 ++
 plans/check-switches/workers/cp2/repair-1/suite.txt                                        |  29 +++
 plans/check-switches/workers/cp2/suite.txt                                                 |  60 +++++++
 reconcile/README.md                                                                        |  16 +-
 reconcile/checks/README.md                                                                 |  32 +++-
 reconcile/config.example.json                                                              |   2 +-
 reconcile/eb_reconcile.py                                                                  | 199 +++++++++++++++++++--
 reconcile/tests/test_catchup.py                                                            | 994 +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
 reconcile/tests/test_history_silence.py                                                    | 633 ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
 30 files changed, 4647 insertions(+), 27 deletions(-)
```

## New risks, gaps, NOTE candidates

1. **Backfill-phase record (D15 (c) reading)** — above; QA's original NF3 `[True]` setup line fails by design.
   Owner: coordinator (confirm or redirect).
2. **Real checks in a repeat with deferred incidents** — the live run after a repeat carries `ctx.reorg_ancestor` = B
   (not the original ancestor A; the repeat already corrected the check's own state above A) and reasons
   `live, force, reorg`. Exercised with the test checks only; QA's `test_cp2_real.py` (M1/M2, D11 + D9) passes but
   does not cover a deep reorg below a boundary after a live run. §6.2 contract checks rewind above B (a no-op). NOTE
   candidate; owner: QA round 2 (targeted M1/M2/M5 probe) or coordinator.
3. **Global path changes (A4 / `test_backfill.py`)** — only after a reorg below `history_block` (an evaluated check
   repeats its silent run; a failing repeat holds the go-live like any failing silent run; other checks keep alerting)
   or a re-enable during the backfill. `test_backfill.py` byte-identical and green.
4. **Upgraded databases** — no `silent:*` rows; their boundary is `phase.history_block` for every check that
   completed a run; a reorg below it (months deep in production; beyond `reorg_search_depth` the worker stops anyway)
   would repeat the silent run. No behaviour change otherwise (D10).
5. **Re-enable during the backfill before `history_block` exists** — unchanged (the later global pin covers the
   disabled interval).
6. **Cost** — `_rearm_silence` reads 3–4 `meta` rows per check per step; the deferral query runs only in muted runs.

## Next owner

Coordinator: integrate decision on the D15 (c) reading (risk 1), then the next composed QA round (Codex) on this head.
