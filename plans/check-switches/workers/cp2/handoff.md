# CP2: silent catch-up for re-enabled and new checks, docs, DF1 (implementer handoff, attempt 1)

Run `run_3869f8caafaa` · task `task_b2312d8a0353` · dispatch `ctx_db6390806a7a` · implementer Claude Opus 5.5 (high,
per packet) · 2026-11-06

## Identity

- Worktree `/Users/marco/orca/workspaces/ssv-network-monitor/cs-cp2` (Orca). At start it was on `mtabasco/cs-cp2`
  at BASE with a clean tree. I verified `git log -1 --format='%P %s'` = parent `6b2045758aa7…`, subject
  `chore(plan): CP2 dispatch`, then created branch **`cs/cp2`** from it. `plan/check-switches` was not touched.
- BASE = `d7b3da6a353b2e5f54ff95af00d099cacf291e4c`. It holds the CP1 merge `16432ad`, so it is the "CP1 head"
  for V2.7.
- HEAD = the single candidate commit on `cs/cp2` that adds this file. Its sha is in the `worker_done` message
  (`git log -1 cs/cp2`).
- Inputs re-hashed at start: role `agents/checkpoint-implementer.md` `6548b79b…`, `qa/DEFECT-CLASSES.md`
  `7daa55f6…`, `test_backfill.py` `28da7d49…`. All equal the packet. I read the CP1 handoffs (original and repair 1),
  `qa/cp1-round-1.md`, `qa/cp1-round-2.md`, the QF3 reproducer `qa/cp1-round-2/test_cp1_siblings.py` and
  `test_switches.py`.
- Environment: `uv venv reconcile/.venv --python 3.12` plus `requirements-dev.txt` (Python 3.12.15, pytest 9.1.1,
  ruff 0.17.1, uv 0.13.4), with no other installs. `PATH` was prefixed with `$HOME/.foundry/bin`. The BASE suite ran
  green before any edit: **2,395 passed**, ruff clean. There was no network, RPC, notification, Docker or push
  use. The only scratch paths under `/private/tmp` (`cs-cp2-red`, `cs-cp2-falsify`) were removed; `suite.txt` and
  `red-on-cp1.txt` record this.

## Changes (Allowed writes only)

| File | Change |
|---|---|
| `reconcile/eb_reconcile.py` | **D9 catch-up record.** `_apply_switches()` deletes a disabled check's `catchup:<id>`, then calls `_plan_catchups()`, then clears the D7 markers of the built checks (all in bootstrap's transaction). `_plan_catchups()` acts only when `backfill.enabled` is true and the phase is `live`. A built check that carries `disabled:<id>` gets a fresh record `{state: pending, history_block: null, since, cause: "reenabled"}`. A check with no completed run (`run:<id>` without `block`) gets one with `cause: "new"` only when it has none yet, so a restart keeps the pinned block. Planned checks are removed from a stale `phase.first_live`. **Pinned silent run.** `run_checks()`: while live, for a `pending` record it pins `history_block` at the first attempt (`_catchup_block`, its own transaction, like `_history_block`) and runs the check at `min(pin, block)`. Then it skips the check's live run while `block <= history_block`. `run_check()` mutes the findings of a `pending` run (`notify=False`, resolutions kept). On commit at or above the pin, the record becomes `first_live`; a silent run below the pin (after a reorg) leaves it `pending`. The first committed run above the pin deletes the record. **Forced live run.** `due()` adds `{backfill, force}` (pending) or `{live, force}` (first_live), ahead of the global `first_live`. **Reorg.** `check_reorg()` sets a `first_live` record whose `history_block` > ancestor back to `pending`, so the silent run is repeated at the same block on the new chain. **Status.** `status.checks.<id>.catchup` is the record while it exists. **DF1.** `main()` calls `validate_switches(cfg.raw)` for `run`/`once`/`verify-layout` before `DB(...)`. |
| `reconcile/tests/test_catchup.py` | New, 69 tests (below). It registers two checks `cu_a`/`cu_b` per test (fixture `ctl`): a level rule (3 words) and an event rule each, each with its own event kind. |
| `deploy/README.md` | New runbook sections "Disable / re-enable a monitor" and "Adding a check to a live database" (D13, plus the CP1 docs follow-ups). |
| `reconcile/checks/README.md` | §3: two schedule rows (per-check catch-up and its first live run) and the new-check sentence. §4: re-enable sentence (D3). §7: `enabled` semantics. |
| `reconcile/README.md` | `status` gains `checks.<id>.catchup`. The D14 wording is corrected (DF1): start commands stop before the database is opened or created; `status` stops when it builds the worker; `export`/`audit` read no switch. Re-enable summary plus a runbook link. |
| `reconcile/config.example.json` | `_comment` only: one sentence naming `checks.<id>.enabled`. |
| `plans/check-switches/workers/cp2/` | `handoff.md`, `suite.txt`, `red-on-cp1.txt`, `falsifiers.txt`, `docs-checklist.md`. |

**Unchanged:** `tests/test_backfill.py` (sha256 `28da7d49…`), `tests/test_m*.py`, `checks/m*.py`,
`checks/registry.py`, `db.py`, `metrics.py`, `tests/harness.py`, `alerts.py`, `tests/test_switches.py`,
`config/`, `.env`, `.local-dry/`. `suite.txt` shows an empty guarded diff against BASE and against `9e94a1a`. The
phase meta shape is unchanged: only the existing `first_live` list is filtered. I asked the coordinator no
question, because none of the ask-first paths was needed.

## Verification results

All test names below are in `reconcile/tests/test_catchup.py`.

| V | Result | Tests and what they assert |
|---|---|---|
| V2.1 | PASS | `test_reenabled_check_catches_up_silently_then_sends_what_is_still_true_once`. Setup: live with sent pages (`lv1` on, an event). Disabled with closure. While disabled: two events, `lv0` on then off (healed), `lv2` on, `lv1` still on, 7 h. Then re-enabled. **Catch-up step:** nothing sent (first assertion). The run has reasons ⊇ {backfill, force} without `live`, its block = that step's checkpoint and its events = the two disabled-period events. The record is `pending` (exact keys) before the step and `first_live` (with `history_block`) after it. Open rows: the two events, `lv1` and `lv2`, all with `notify=0, alerts=0`. Healed `lv0`: no row. **Next step:** reasons ⊇ {live, force}. Exactly one `open` each for `lv1` (a **new** id; the `disabled` rows are byte-identical) and `lv2`. The record is gone from meta and `status`, and there is no `health` row (D3). A live event is sent once, nothing is sent twice over 4 more steps, and the disabled-period events and `lv0` are never sent. Red on the CP1 head: the burst `['ev:1004', 'ev:1006', 'lv1', 'lv2']`. |
| V2.2 | PASS | (a) `test_new_check_on_a_live_database_catches_up_from_start_block`: history built with `cu_b`, then `cu_a` added. Its first run sees **every** stored event (from `start_block − 1`) and nothing is sent. `cu_b` gets no record and no forced run. Next step: `lv1` once, then a live event once. Red on the CP1 head: `['ev:1003', 'ev:1004', 'ev:1005', 'ev:1006', 'lv1']` sent. (b) `test_upgraded_live_database_gives_no_catch_up_to_a_check_that_ran`: no `catchup:*` row, no run with backfill/live/force, no message; one `realert` at 6 h only. (c) `test_new_check_while_the_database_is_backfilling_takes_the_global_path`: no `catchup:*` row at any step; the global silent run (`backfill`, `force`) evaluates it; nothing sent. (d) `test_backfill_disabled_keeps_the_base_behaviour[new/reenabled]`: no record, marker cleared, the first run has neither `backfill` nor `live` and sends every event since the cursor (base behaviour, the documented dependency). Also `test_reenabled_while_backfilling_clears_the_marker_without_a_record` (marker cleared in phase `backfill`, no record) and `test_harness_default_is_unchanged`. (e) `test_check_disabled_after_go_live_before_its_first_live_run`: the stale `first_live` entry is removed at the re-enabling start; the catch-up run has no `live` reason and is silent; then `lv1`/`lv2` once each. (f) `test_check_disabled_during_the_backfill_is_caught_up_after_go_live`: go-live without `cu_a` (no `run:cu_a`), then a re-enable (`cause: reenabled`) with a silent catch-up from `start_block` (all history events) and live `lv0` once. |
| V2.3 | PASS | `test_restarts_and_a_failed_silent_run_keep_the_record_and_its_pinned_block[runtime/rpc]`: restart between disable and closure (RPC down at the start, closure at recovery). After the re-enable the record exists before any run attempt. The silent run fails once: `check-error` health (runtime) or `rpc_failures == 1` (rpc), with the record `pending` and pinned. After a restart the record is identical. The same block is stepped twice: the retry is pinned, silent, with the disabled-period event; the second same-block step runs no live run. Restart between the silent and the first live run: one forced live run, exactly one `open` (`lv1`), record deleted. Restart after it: no record, no forced run, nothing re-sent. `test_new_check_restarted_after_a_failed_silent_run_keeps_its_pinned_block`: a new check fails twice, a block arrives in between, then a restart. The record is kept, the retry stays pinned to the first block (the earlier event silent), and the live run in the same step sends the later event once. Outbox keys are unique (primary key), and no incident sends `open` twice (sweep). |
| V2.4 | PASS | `test_reorg_while_disabled_then_reenabled_catches_up_on_the_new_chain`: a reorg below the old cursor while disabled, with an orphaned event plus `lv0`, and on the new chain an event plus `lv2`. `on_reorg(ancestor)` is replayed at `__init__`. The catch-up run has reasons ⊇ {backfill, force, reorg}, `ctx.reorg_ancestor` = ancestor and only the new-chain event; nothing is sent; `reorg:cu_a` is cleared on commit. The first live run sends `lv2` once and never the orphaned event or `lv0`. `test_reorg_below_the_catch_up_block_before_the_first_live_run_stays_silent`: a reorg replacing the catch-up block after the silent run committed. The silent run is repeated at the pinned block (reasons include `reorg`), so the replayed event at that height is never sent and the event after it is sent once. |
| V2.5 | PASS | `test_live_alerts_of_another_check_are_sent_while_a_catch_up_fails[reenabled/new]`: A's event is sent **in the step** of B's failing silent run. A keeps alerting through B's 3 failures, while B has `check-error`, its record `pending` and the same pinned block. When B recovers, it runs silent at the pin and then live in the same step: `lv1` and B's post-pin event once each, and B's disabled-period or history event never. Nothing is duplicated afterwards. |
| V2.6 | PASS | `test_flips_without_steps_close_once_per_disable_and_send_nothing`: disable → enable → disable over three restarts with no steps. Closure 1 has `closed: 1`; the enable clears the marker and writes a `pending` record; the second disable writes a new marker with `closed: 0` (later `at`) and drops the record. The disabled row is unchanged and no outbox row is written. `test_reenable_with_no_new_block_pins_the_current_checkpoint_and_a_second_disable_closes_it`: a catch-up with no new block is pinned at the current checkpoint and opens `lv1` silently; a disable at the same checkpoint closes it (`closed: 1`); the re-enable is silent again and goes live at the next block, sending `lv1` once. `test_status_cli_during_a_catch_up_shows_it_and_changes_nothing`: `main(["status"])` shows `catchup` as `pending`, then `first_live`, then absent, and the database dump is unchanged around each call. |
| V2.7 | PASS | Full suite **2,464 passed** (2,395 + 69), exit 0; ruff clean (`suite.txt`). `test_backfill.py` sha256 `28da7d49…` and 67 passed. `test_switches.py` (CP1) 82 passed on this head. The guarded diff is empty. Red on the CP1 head (`red-on-cp1.txt`, scratch copy, removed): 41 failed, 28 passed. V2.1 and V2.2 (a) fail on the burst message lists quoted above, which is the behavioural reason. Two tests fail only on the absent record (API; classified in the file). The passing tests are the controls. `test_catchup.py` imports only symbols present at BASE and collects there with 69 cases. |
| V2.8 | PASS (claimed) | `docs-checklist.md` maps every claim of the new runbook sections, `checks/README.md` §3/§4/§7, `reconcile/README.md` and the `_comment` to a test or a repository fact (Monitor `paused`, registered ids, `BACKFILL_DEFAULTS`). CP1 follow-ups: start commands close and `status`/`export` do not (`test_every_start_command_closes_and_status_export_never_do[*]`, new); queued messages are still delivered (CP1 V1.1); `enabled` must be boolean (D14 tests plus DF1). Metrics claim: `test_metrics_have_no_run_series_for_a_disabled_check`. No secret in the added lines (`suite.txt`). QA judges V2.8. |

**DF1 (QF3): done.** `main()` validates the switches before the writable `DB` is opened or created, for `run`,
`once` and `verify-layout`. `status` is unchanged: its read-only `DB` opens without writing and its `Worker`
raises. `export` and `audit` are unchanged and read no switch. There are three permanent tests, each parametrized
over `run`/`once`/`verify-layout`, and all are red on the CP1 head for QF3's reason:

- `test_malformed_switch_writes_nothing_to_a_current_database` traces INSERT/UPDATE/DELETE/ALTER, as in QA's
  reproducer. On the CP1 head: `INSERT INTO meta(key, value) VALUES('schema_version', '1') ON CONFLICT … `.
- `test_malformed_switch_creates_no_database` checks that the directory stays empty. On the CP1 head:
  `['w.sqlite', …, 'w.sqlite.lock']`.
- `test_malformed_switch_leaves_a_pre_version_database_unchanged` takes a dump with no `schema_version` row. On the
  CP1 head the row is added.

Controls: `test_read_only_commands_with_a_malformed_switch_are_unchanged[status/export/audit]`. QA's reproducer
file was not copied: its QF3 cases are reproduced in the suite. The `reconcile/README.md` wording and the D14 cell
below are corrected.

**Adversarial sweep:** `test_seeded_catchup_sweep[0..29]`, 40 random actions per seed. The actions are disable
and enable flips, plain restarts, events, level toggles, failing runs (an exception or a transport `RpcError`, 1–3
times), same-block steps and reorgs of depth 1–4 (also during a catch-up), with confirmations 0 or 2. The catch-up
block is derived **independently of the record**: it is the checkpoint of the first step after the re-enabling
start. Invariants after every action:

- Nothing is sent for a check while it is disabled, in its catch-up step, or while its record exists.
- The record's block equals the derived one.
- No event at or below the catch-up block is ever sent.
- Every canonical event above it, up to the committed cursor, is sent (no missed page; nothing is held by the other
  check).
- No incident sends `open` twice, and there is at most one open row per key.

Exercised in the 30 seeds (instrumented run, scratch): 111 runs with a `pending` record, 46 with `first_live`, 64
failed catch-up runs, 88 reorgs (11 during `pending`, 1 during `first_live`, reverted to `pending`; the
deterministic `test_reorg_below…` covers that case explicitly). On the CP1 head, 17 of the 30 seeds fail on the
burst.

**Falsifiers** (`falsifiers.txt`, in a scratch copy): there are 12, one per mechanism, and each reddens named
tests. They cover: the mute; the stale `first_live` removal; the pin set once; the re-enable trigger; the record
kept on restart; the skip of the live run at or below the pin; the reorg repeat; DF1; the record deletion; the
`backfill.enabled` gate; the phase gate (it also reddens 6 `test_backfill.py` tests); and the record drop at
disable.

## State table (DEFECT-CLASSES; keys and records this CP adds or changes)

| Key / state | (a) empty | (b) error | (c) partial | (d) reorg depth 1 / N, incl. resolved keys | (e) restart between detection and handling | (f) same-block order | (g) time anchoring |
|---|---|---|---|---|---|---|---|
| meta `catchup:<id>` = `{state, history_block, since, cause}` (new) | A catch-up with no event and no finding still goes `pending` → `first_live` → deleted, sending nothing: `test_flips_without_steps…`, `test_reenable_with_no_new_block…`, `test_reorg_below…` (no disabled-period event) | Failed silent run: rollback, record stays `pending` and pinned, retried every step; `check-error` or RPC accounting as today. Tests: V2.3 `[runtime/rpc]`, V2.5. Failed live run: the record stays `first_live` and the next step retries it forced (by design; sweep `fail` action) | Written in bootstrap's transaction with the closure and the marker clear: all or nothing (CP1 V1.5 rollback test still green). Its state changes commit in the same transaction as the run's findings and cursor (`run_check`). Tests: V2.3 and the sweep | **Depth 1 and N:** sweep reorgs of depth 1–4 during `pending` and `first_live`. A `first_live` record whose pin is above the ancestor goes back to `pending` and the silent run is repeated at the pin, so replayed events stay silent: `test_reorg_below…`. A reorg while disabled leaves no record (it was dropped at disable), and the catch-up then carries `reorg_ancestor`: V2.4. **Resolved keys:** catch-up incidents resolved silently, then re-found after a reorg, are re-opened silently while the record exists (mute) (sweep). **Limit:** after the record is deleted, see NF1 | Written before any run attempt (V2.3, V2.1 `pending` before the first step). A restart keeps it: a re-enabled check's marker is cleared in the same transaction, and a new check is re-planned only when it has no record (`test_new_check_restarted…`, falsifier F5). A restart between the silent and the live run still gives one forced live run (V2.3). After it, no record (V2.3, falsifier F9) | The silent run covers events up to and **including** the pin, and the live run starts after it, ordered by (block, log_index) from the cursor. The replayed event **at** the pin stays silent: `test_reorg_below…`. An event the block after the pin is live: V2.5, `test_new_check_restarted…` | Anchored at the checkpoint of the first step in which the check runs, never at the wall clock or block arrival: V2.1 (`history_block` = that step's checkpoint), V2.6 (no new block: pinned at the current checkpoint). `since` is informational (start clock) |
| Pinned catch-up block `history_block` (in the record) | `null` until the first attempt; then set once (`_catchup_block`). Test: V2.1 `pending` record with `history_block: null` | A failure after the pin keeps it: V2.3, V2.5, falsifier F3 | The pin is its own committed transaction before the run, like `_history_block`. If the run then fails, the pin survives: V2.3 | `min(pin, block)` after a rewind; the record stays `pending` until a silent run at or above the pin (`test_reorg_below…`, sweep) | Survives restarts: V2.3, `test_new_check_restarted…` | n/a | As above |
| `phase.first_live` entry removal (changed) | No stale entry: phase untouched (V2.1, V2.2 a; the phase meta is only rewritten when a stale entry exists) | Same transaction as the record | Only the planned ids are removed; the global `first_live` of other checks is kept (`test_check_disabled_after_go_live…`: `cu_b` ran its own first live run before) | Unaffected by reorgs (phase meta is not chain data) | Removed at the re-enabling start; a restart afterwards finds nothing to remove (V2.2 e) | n/a | n/a |
| D7 marker `disabled:<id>` clearing (changed order) | No marker: nothing (`test_harness_default_is_unchanged`, V2.2 b) | Same transaction as the records: a failed bootstrap leaves the marker, so the next start re-plans (CP1 V1.5) | Read before it is cleared, so (b) is evaluated for every built check | n/a | Cleared at the enabling start in every case. Record written: V2.1. Phase `backfill`: `test_reenabled_while_backfilling…`. `backfill.enabled` false: V2.2 d. Re-planned only while the marker exists | n/a | n/a |
| Disabled check's `catchup:<id>` (dropped at the disabling start) | No record: nothing | Same transaction as the closure | n/a | n/a | Re-enable decides afresh (a fresh record, or none with backfill off): `test_flips_without_steps…`, falsifier F12 | n/a | n/a |
| `backfill.enabled` switched off with a record pending (durable decision) | n/a | n/a | n/a | n/a | No new record is planned, and the existing one is honoured, so the catch-up stays silent: `test_backfill_switched_off_mid_catch_up_keeps_the_durable_record` | n/a | n/a |
| Config `checks.<id>.enabled` (D14), **corrected cell (DF1)** | Absent: enabled (unchanged) | Non-boolean or non-object: `SystemExit` naming the id. **`run`/`once`/`verify-layout`:** raised in `main()` before the writable database is opened or created, so no file, lock file, `schema_version` upsert or row is written (`test_malformed_switch_*[run/once/verify-layout]`). **`status`:** at `Worker` construction (read-only `DB`, no write; `test_read_only_commands…[status]`, CP1 D14 tests). **`export`/`audit`:** read no switch and answer (`…[export/audit]`). A direct `Worker(...)` still validates first (CP1) | One malformed id among valid ones rejects the whole config (CP1 `test_validate_switches…`) | n/a | Every start re-validates | n/a | n/a |

The CP1 rows (incidents `disabled`, `disabled:<id>` marker semantics, D11 rewind, D8 filter, status/digest, outbox
before closure) are unchanged. `test_switches.py` is green on this head.

## `git diff --stat d7b3da6..HEAD` (untracked files included: all are in the commit)

```
 deploy/README.md                                   |  64 ++
 plans/check-switches/workers/cp2/docs-checklist.md |  75 ++
 plans/check-switches/workers/cp2/falsifiers.txt    | 275 ++++++
 plans/check-switches/workers/cp2/handoff.md        | 165 ++++
 plans/check-switches/workers/cp2/red-on-cp1.txt    | 244 +++++
 plans/check-switches/workers/cp2/suite.txt         |  60 ++
 reconcile/README.md                                |  12 +-
 reconcile/checks/README.md                         |  17 +-
 reconcile/config.example.json                      |   2 +-
 reconcile/eb_reconcile.py                          |  94 +-
 reconcile/tests/test_catchup.py                    | 991 +++++++++++++++++++++
 11 files changed, 1984 insertions(+), 15 deletions(-)
```

## New findings and risks

1. **NF1: a deep reorg after the first live run can unmute catch-up incidents.** This is a NOTE candidate, and the
   global D17 path shares it. Once a check's record is deleted, a reorg below its catch-up block re-runs the check
   from the ancestor with reason `reorg`. A re-included event of the catch-up period re-finds its silent
   (`notify=0`) incident, and `Alerts._apply_one` "unmutes" it, so it is sent. The same holds today for history
   events after a reorg below `phase.history_block`. It needs a reorg beyond `confirmations` (12) that reaches back
   past the catch-up block. I did not fix it: the fix touches the unmute rule in `alerts.py` or the global path,
   both out of scope. The sweep excludes it explicitly. Owner: coordinator/Marco. Affects A2/A3 only under a
   >12-block reorg.
2. **NF2: the global D17 path has the reorg gap fixed here for the per-check path.** A reorg below
   `history_block` after a check's silent run but before its first live run replays history events live. I left the
   global path unchanged (frozen oracle). The per-check path repeats its silent run instead
   (`test_reorg_below…`). This goes slightly beyond D9's literal text: it is a reorg rule for the record, inside
   Allowed writes. Owner: coordinator (accept, or record as a deviation).
3. **NF3: disable and re-enable within phase `backfill`, after the check's own silent run.** D9 leaves phase
   `backfill` untouched. A check that has already evaluated history (it is in `evaluated` and `first_live`), is
   disabled, and is re-enabled while another check still holds the backfill open gets no catch-up. Its first live
   run then sends the events of its disabled interval. This is a narrow window (a stuck backfill plus a flip). Owner:
   coordinator, if D9 should also cover it.
4. **Runbook default "normally the next step".** The first live run waits for the first step whose checkpoint is
   above the catch-up block, about one block. With `confirmations` 12, that is the next step that sees a new
   confirmed block.
5. **Composition note M1 `_reported` (CP1 QA).** My scenarios use registered stubs and no real M1. A re-enabled M1's
   catch-up applies M1's findings silently, and `_reported` only gates M1's transient re-reports. CP1's
   `test_m1_reported_counts_a_message_of_a_disabled_incident` stays green. I made no `checks/m*.py` change and
   asked no question.
6. **`status.checks.<id>.catchup`** exposes the whole record (`since` and `cause` in addition to the plan's
   `history_block` and `state`).

## Next owner

The coordinator runs the composed Codex QA (V2.9) on the CP2 head over `9e94a1a`. QA should re-run V1.1–V1.7 and
V2.1–V2.8, attack DEFECT-CLASSES 1, 2, 10 and 12, and re-run the QF3 reproducer for DF1. Suggested first attacks:
the record lifecycle across restart and reorg (the derived catch-up block in the sweep); NF1–NF3; the
`docs-checklist.md` mapping. CP2 is not merged and nothing else is started.
