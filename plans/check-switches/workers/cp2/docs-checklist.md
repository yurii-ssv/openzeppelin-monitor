# CP2 docs checklist (V2.8, D13): every claim → its test or V row

Tests: `T:` = `reconcile/tests/test_catchup.py` (CP2), `S:` = `reconcile/tests/test_switches.py` (CP1, unchanged and
green on the CP2 head), `B:` = `reconcile/tests/test_backfill.py` (frozen D17 oracle). "Fact" rows are checked
against the repository, not a test (named command).

## `deploy/README.md` → "Disable / re-enable a monitor" (runbook)

| # | Claim | Evidence |
|---|---|---|
| R1 | The switch is `checks.<id>.enabled` in `reconcile/config.json`, read at start, no live reload; it takes effect at the restart | Every T/S test sets the switch through the config of a new `Worker` (`Env.start`); nothing re-reads it in a running worker (`Worker.__init__` → `disabled_ids(cfg.raw)` and `registry.build`) |
| R2 | `status` shows `"disabled": ["m7"]` and `checks.<id>.catchup` while catching up | S: `test_readonly_status_lists_the_disabled_check_and_closes_nothing`, `test_example_config_with_a_disabled_check_lists_it_in_status`; T: `test_status_cli_during_a_catch_up_shows_it_and_changes_nothing` (V2.6) |
| R3 | `enabled` must be `true`/`false`; any other value stops the worker at start with `checks.m7.enabled: must be true or false`, before it opens the database (D14, DF1); `status` refuses it too | S: `test_non_boolean_enabled_is_a_configuration_error_and_changes_nothing[*]` (exact message), `test_non_boolean_enabled_stops_every_cli_command_that_builds_a_worker[*]`; T: `test_malformed_switch_writes_nothing_to_a_current_database[run/once/verify-layout]`, `test_malformed_switch_creates_no_database[*]`, `test_malformed_switch_leaves_a_pre_version_database_unchanged[*]`, `test_read_only_commands_with_a_malformed_switch_are_unchanged[status]` |
| R4 | Every start command (`run`, `once`, `verify-layout`) performs the closure; `status` and `export` never do | T: `test_every_start_command_closes_and_status_export_never_do[run/once/verify-layout/status/export]` (CP1 follow-up) |
| R5 | At that start the open incidents become `disabled` in one transaction, with no message of any kind | S: `test_disabled_check_closes_its_incidents_and_is_never_realerted[*]` (V1.1), `test_failed_closure_commits_nothing_and_the_next_start_closes_once` (V1.5), `test_disable_enable_disable_round_trip_sends_nothing_at_start[*]` (V1.2) |
| R6 | A message queued before the disabling restart is still delivered | S: `test_disabled_check_closes_its_incidents_and_is_never_realerted[*]` (asserts `sent_at` and the capture of the undelivered `open` row) |
| R7 | Closed incidents are never re-alerted; they leave `status.open_incidents`/`open`, the digest's open counts and `ssv_watch_open_incidents`; `export` prints `state: disabled` | S: V1.1 test (26 × 1 h, digest), `test_status_and_export_after_the_closure`, `test_metrics_open_count_excludes_disabled_rows` |
| R8 | `status.disabled` is read from the config (shows as soon as the file is edited); the digest adds `Disabled checks: m7` after "Open incidents" | S: `test_readonly_status_lists_the_disabled_check_and_closes_nothing` (listed before any closing start), V1.1 test (digest line), `test_digest_has_no_disabled_line_without_disabled_checks` |
| R9 | A disabled check has no `ssv_watch_check_last_run_*` / `ssv_watch_check_errors_total` series; no metric says it is disabled | T: `test_metrics_have_no_run_series_for_a_disabled_check` (`render` over the built checks, as `MetricsServer(..., list(worker.checks))` in `main()`) |
| R10 | While off, a reorg still rewinds its event cursor; the replay happens at re-enable | S: `test_reorg_while_disabled_rewinds_the_disabled_cursor[1/4]`, `test_reenabled_after_a_reorg_while_disabled_replays_the_new_chain`; T: `test_reorg_while_disabled_then_reenabled_catches_up_on_the_new_chain` (V2.4) |
| R11 | Re-enable with `backfill.enabled` true on a live database: the first run evaluates everything since the last committed run silently (events of the disabled period from its cursor, state at the catch-up block = checkpoint of the first step after the restart) | T: `test_reenabled_check_catches_up_silently_then_sends_what_is_still_true_once` (V2.1: run block = checkpoint of that step, events = the disabled period's, reasons ⊇ {backfill, force}, nothing sent) |
| R12 | The default is true and the production config has it | Fact: `BACKFILL_DEFAULTS["enabled"] = True` (`eb_reconcile.py`), `reconcile/config.example.json` `"backfill": {"enabled": true, …}` (the runbook's "Configure" step copies it) |
| R13 | Its first run at a later block is forced and sends a condition still true then once, as `open`, under a new incident id | T: V2.1 (`{live, force}`, one `open` per still-true level, new ids, the `disabled` rows untouched, nothing twice over 4 more steps) |
| R14 | An event of the disabled period and a condition that appeared and healed while off are never sent; the event is visible in `export` with `alerts = 0`; a healed level condition leaves no incident | T: V2.1 (events opened with `notify = 0`, `alerts = 0`; healed `lv0`: no row, never sent), `test_seeded_catchup_sweep[*]` (no event at or below the catch-up block ever sent) |
| R15 | No message about the re-enable itself | T: V2.1 (`health` rows: none), `test_flips_without_steps_close_once_per_disable_and_send_nothing` (no outbox row across three flips) |
| R16 | `status.checks.<id>.catchup` = `{state: pending|first_live, history_block, since, cause}`; it disappears when the first live run commits | T: V2.1 (`pending` before the first step, exact keys; `first_live` after it; absent after the live run), `test_status_cli_during_a_catch_up_shows_it_and_changes_nothing` |
| R17 | The catch-up survives restarts (kept in the database with its block) | T: `test_restarts_and_a_failed_silent_run_keep_the_record_and_its_pinned_block[*]` (V2.3), `test_new_check_restarted_after_a_failed_silent_run_keeps_its_pinned_block` |
| R18 | A failing catch-up run raises `check-error` health (an RPC error counts as an RPC failure), keeps its block, is retried every step; other checks alert as usual | T: V2.3 `[runtime]` (check-error) / `[rpc]` (`rpc_failures == 1`), `test_live_alerts_of_another_check_are_sent_while_a_catch_up_fails[reenabled/new]` (V2.5) |
| R19 | A reorg below the catch-up block before the first live run repeats the silent run on the new chain | T: `test_reorg_below_the_catch_up_block_before_the_first_live_run_stays_silent` |
| R20 | With `backfill.enabled: false` there is no catch-up: the first run alerts on everything since the last run | T: `test_backfill_disabled_keeps_the_base_behaviour[new/reenabled]` (V2.2 d) |
| R21 | While a new database is in phase `backfill`, a check enabled or disabled there is handled by the global backfill | T: `test_new_check_while_the_database_is_backfilling_takes_the_global_path` (V2.2 c), `test_check_disabled_during_the_backfill_is_caught_up_after_go_live` (V2.2 f); B: unchanged and green |
| R22 | Monitor rules are separate: M8, M10, M11, M13 also have OpenZeppelin Monitor rules (`deploy/config/monitors/`, own `"paused"` field), M15 is Monitor-only | Fact (plan scope, excluded from the worker switch): `grep -c '"paused"' deploy/config/monitors/*.json` → 32 files, each `"paused": false`; no worker check `m15` is registered (`checks/__init__.py`, `registry.REGISTRY` = m1–m14). Not a worker behaviour: no test |
| R23 | The sub-switches `m9.dissent_enabled`, `m11.revert_rate_enabled`, `m11.zero_round_proof_enabled` are unchanged (health line once per run, open episodes resolved once) | Unchanged code (`checks/m*.py` not in the diff) and their existing tests: `test_m9.py::test_r4_dissent_enabled_*`, `test_m11.py::test_r4_revert_rate_enabled_*`, `test_r4_zero_round_proof_enabled_*` (all green in the suite) |

## `deploy/README.md` → "Adding a check to a live database"

| # | Claim | Evidence |
|---|---|---|
| N1 | A check new to the database gets the same silent catch-up at the first start that builds it (no completed run): every stored event from `start_block − 1` up to its catch-up block, silently; alerts from its next run, a condition still true sent once | T: `test_new_check_on_a_live_database_catches_up_from_start_block` (V2.2 a: run events = every stored event, nothing sent, then `lv1` once, a live event once) |
| N2 | Its go-live block is `status.checks.<id>.catchup.history_block` | T: V2.1/V2.2 a (`history_block` = the silent run's block; live runs only above it) |
| N3 | Needs `backfill.enabled` true | T: `test_backfill_disabled_keeps_the_base_behaviour[new]` |
| N4 | Upgrading a live database changes nothing for checks that completed a run: no catch-up, no forced run, no message | T: `test_upgraded_live_database_gives_no_catch_up_to_a_check_that_ran` (V2.2 b), V2.2 a (`cu_b` gets no record and no forced run) |
| N5 | The catch-up reads state at its catch-up block (a late retry needs historical state) | T: V2.3/V2.5 (the retried run's `block` = the pinned block; the check reads `ctx.rpc` at it). The archive-endpoint remark is the D17 section's, unchanged |

## `reconcile/checks/README.md` §3, §4, §7

| # | Claim | Evidence |
|---|---|---|
| C1 | §3 row: per-check catch-up, `backfill`/`force`, pinned (durable `catchup:<id>`, kept on retry/restart, repeated after a reorg below it), `notify=False` | T: V2.1, V2.3, `test_reorg_below_the_catch_up_block_before_the_first_live_run_stays_silent`, sweep |
| C2 | §3 row: first run after the catch-up block, `live`/`force`, same step after a retry, never holds another check, no message of its own, record deleted on commit | T: V2.1, V2.5, V2.3 |
| C3 | §3: a new check's first run sees every stored event (silently when live + backfill) | T: V2.2 a; `test_backfill_disabled_keeps_the_base_behaviour[new]` (not silent without backfill) |
| C4 | §4: re-enabled, the check catches up silently; nothing sent about the switch, either direction | T: V2.1, `test_flips_without_steps_close_once_per_disable_and_send_nothing`; S: V1.2 |
| C5 | §7: `enabled` boolean only, read at start; `false` → not built, closed at next start; `true` → catch-up; not built ≠ disabled | S: D14 tests, V1.1, `test_a_check_that_is_merely_not_built_is_not_disabled`; T: V2.1 |

## `reconcile/README.md`

| # | Claim | Evidence |
|---|---|---|
| M1 | `status` has `checks.<id>.catchup` (`state`, `history_block`, `since`, `cause`) | T: V2.1 (exact keys), V2.6 status CLI |
| M2 | Malformed switch: `run`/`once`/`verify-layout` stop before the database is opened or created (no file, no row); `status` stops when it builds the worker; `export`/`audit` read no switch (DF1 corrects "before it touches the database") | T: DF1 tests (three cases × three commands), `test_read_only_commands_with_a_malformed_switch_are_unchanged[status/export/audit]` |
| M3 | Re-enabled (or new), with backfill on a live database: silent catch-up, then still-true sent once; disabled-period events and conditions never sent; the switch never announced | T: V2.1, V2.2 a, sweep |

## `reconcile/config.example.json` `_comment`

| # | Claim | Evidence |
|---|---|---|
| X1 | Names `checks.<id>.enabled` (true/false only, read at start): false closes silently as disabled; true again or a new check on a live database catches up silently (needs backfill.enabled), then alerts | As R3, R5, R11, N1, R20. The file still loads and builds every check: `test_framework.py::test_config_example_loads_and_builds_every_check` (green) |

## Secret scan

`git diff d7b3da6..HEAD` adds no secret: no URL with a key, token, chat id or webhook (checked with
`git diff d7b3da6..HEAD | grep -inE '^\+.*(token|secret|webhook|api[_-]?key|password|https?://)'` → two added lines,
both prose: the `checks/README.md` §7 line "env names of secrets" (rewrapped, unchanged words) and the
`config.example.json` `_comment` ("Secrets are read from the environment variables named here", unchanged words); see
`suite.txt`).
