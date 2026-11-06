# CP1 — Clean disable: repair 1 handoff (after Codex QA round 1 = FAIL)

Run `run_3869f8caafaa` · task `task_5ce75920d977` · dispatch `ctx_7a0b9bf65c45` · implementer Claude Opus 5.5 (high, per packet) · 2026-11-06

This file plus the original [`workers/cp1/handoff.md`](../handoff.md) (unchanged, immutable) describe the candidate. The
state table and the reader table below are **complete and corrected**: they replace the original's tables.

## Identity

- Worktree `/Users/marco/orca/workspaces/ssv-network-monitor/cs-cp1-r1` (Orca). It was on `mtabasco/cs-cp1-r1` at
  `7639cffb4991ce6bd536cfc607b27ce1aa78032d` with a clean tree; I verified both before editing, then created branch
  **`cs/cp1-r1`** from it. `cs/cp1` and `plan/check-switches` were not moved.
- Base = the CP1 candidate `7639cff` (`cs/cp1`). HEAD = the single repair commit on `cs/cp1-r1` that adds this file;
  its sha is in the `worker_done` message (`git log -1 cs/cp1-r1`).
- Inputs re-hashed at start: role `agents/checkpoint-implementer.md` `6548b79b…`, `qa/DEFECT-CLASSES.md` `7daa55f6…`
  (both equal to the packet). QA report read from `plan/check-switches:plans/check-switches/qa/cp1-round-1.md`,
  including its `test_cp1_independent.py`.
- Environment: `uv venv reconcile/.venv --python 3.12` plus `requirements-dev.txt`. No other installs. Python 3.12.15,
  pytest 9.1.1, ruff 0.17.1. `PATH` prefixed with `$HOME/.foundry/bin`. No network, RPC, notification or Docker use.

## Changes (Allowed writes only)

| File | Change |
|---|---|
| `reconcile/checks/registry.py` | New `validate_switches(settings)`, beside `disabled()`. For every configured id (registered or not), `checks.<id>` must be an object, and `checks.<id>.enabled`, when present, must be a JSON `bool`. Otherwise it raises `SystemExit("checks.<id>.enabled: must be true or false, got <value as JSON>")`, the same error type as an unknown id. `disabled()` calls it first and then selects `enabled is False`, so literal `false` is the only value that disables. `build` is byte-unchanged: the sha256 of its source is identical at `9e94a1a`, `7639cff` and the repair (`suite.txt`). |
| `reconcile/eb_reconcile.py` | In `Worker.__init__`, `disabled_ids(cfg.raw)` (which validates) is now the **first** statement after the scalar attributes. It runs before `Alerts`, before `registry.build` and before the checks' schema DDL. A malformed switch therefore raises before anything touches the database, on every path that constructs a `Worker`. `self.disabled` keeps its definition (switched off minus built). |
| `reconcile/tests/test_switches.py` | 29 new tests (below). The existing 53 are unchanged. |
| `reconcile/checks/README.md` §4 | The blanket sentence "checks that read `incidents` select `open`/`resolved`" is corrected. It now names the two any-state check readers (M1 `_reported`, M5 id uniqueness) and why they are correct. One sentence on D14. |
| `reconcile/README.md` | Disable paragraph (QN1): every start command (`run`, `once`, `verify-layout`) performs the closure; the read-only `status` and `export` never close anything; a non-boolean `enabled` stops every command that builds a worker. |
| `plans/check-switches/workers/cp1/repair-1/` | `handoff.md`, `suite.txt`, `red-on-7639cff.txt`. |

**Unchanged:** `alerts.py`, `db.py`, `metrics.py`, `checks/m*.py`, `tests/test_m*.py`, `tests/test_backfill.py`,
`tests/harness.py`, `deploy/README.md` (runbook = CP2), `config.example.json` and the original `workers/cp1/*` records.
`suite.txt` shows an empty diff of the guarded paths against `7639cff`. No `checks/m*.py` change was needed, so I sent
no coordinator `ask`.

## QF1 (BLOCKING) — D14: `enabled` must be a boolean

### Behaviour

`checks.<id>.enabled` ∈ {`0`, `null`, `""`, `"false"`, `1`, `"true"`, `0.0`, `[]`, `{}`} → `SystemExit` at `Worker`
construction. The message names the id and the value. A non-object `checks.<id>` (e.g. `"m7": false`) is rejected the
same way: before the repair it crashed in `build` with an `AttributeError`, so this is the same fail-loud outcome with a
clear message. `true`/`false`/absent behave exactly as at `7639cff`.

Callers, at the revision:

- `run`, `once`, `verify-layout`: `main()` → `Worker(cfg, rpc, db, …)` raises before `bootstrap()`.
- `status`: `main()` → `Worker(cfg, None, db)` raises as well.
- `export` and `audit` construct **no** `Worker` and read no switch, so they are unaffected and print the database as
  before. D14's parenthetical "the read-only `status`/`export` CLI alike" is therefore met for `status` only. The
  packet said "if they construct a `Worker`", so I did not add a check to `export`. Coordinator: if `export` must also
  refuse a malformed config, it is a one-line `validate_switches(cfg.raw)` in `main()`. Neither command can close
  anything, so I recommend leaving them unchanged.

### Tests (`reconcile/tests/test_switches.py`), green on the repair

| Test | Asserts |
|---|---|
| `test_non_boolean_enabled_is_a_configuration_error_and_changes_nothing[0/None/''/'false'/1]` | Four open incidents (`open_four`: sent page, sent warn, silent warn, a sent info still undelivered). Then a `Worker` is built with the value, bootstrapped, and run one step, all inside `try`. Assertions, behavioural first: the four rows are still `open`; the `incidents` dump is unchanged; there is no `disabled:` meta row and the outbox is unchanged; **the whole database dump is unchanged** (every table, read through a separate `sqlite3` connection); the `SystemExit` message is exactly `checks.sw_a.enabled: must be true or false, got <value as JSON>`. |
| `test_non_boolean_enabled_stops_every_cli_command_that_builds_a_worker[value × run/once/verify-layout/status]` (20) | The same, through `main()` with `load_config`/`make_rpc`/`make_notifier`/`build` patched onto the FakeChain environment (`verify-layout` builds `m5` with a stubbed `verify_layout`). `run` is bounded by a patched `Worker.step` that raises after one step, and `signal.signal` is a no-op. Asserts that the rows are still `open`, the database dump is unchanged, and a `SystemExit` names `checks.sw_a.enabled`. |
| `test_boolean_enabled_controls_are_unchanged[True/False]` | Controls: `true` → built, rows `open`, `disabled == []`, no marker; `false` → not built, four rows `disabled`, marker `{block, at, closed: 4}`. No outbox row in either case. |
| `test_validate_switches_rejects_every_non_boolean_and_names_the_id` | Unit: 9 non-boolean values for `sw_b` beside a valid `sw_a: false`; an unregistered id; a non-object entry; `{}` and a valid all-boolean config accepted; `disabled()` = the `false` ids; `build` of the same config unchanged. |

### Red on `7639cff` (scratch copy, removed): `red-on-7639cff.txt`

26 of 29 new tests fail on `7639cff`; the 3 that pass are the two boolean controls and the M1 pin (QF2).

- `0` / `None` / `""`, direct and via `run`/`once`/`verify-layout`: **12 fail on the first assertion**,
  `assert ['disabled', …] == ['open', …]`. The malformed value closed the four rows, which is QF1's behavioural reason.
- `"false"` / `1`: direct and via the start commands, 8 fail on the unchanged-`incidents`/database assertion. On
  `7639cff` the check is built and **runs** (`last_seen_block` moves, blocks are scanned): a non-boolean silently
  counts as enabled.
- `status`: 5 fail on `error is not None`. `status` is read-only on `7639cff` too, so it closes nothing there; its
  wrong outcome is that it lists `0`/`null`/`""` as disabled with no error.
- The unit test fails with `ImportError` (new symbol). It is not counted as behavioural evidence.

### QA's reproducer under D14

QA's `test_d6_only_literal_false_may_close[0|None|'']` expects the rows to stay `open` after `Worker(...)` +
`bootstrap()`. Under D14 the `Worker(...)` construction itself raises `SystemExit`, so that test, run unchanged on the
repair (scratch copy, removed), fails at construction for `[0]`/`[None]`/`['']` with
`SystemExit: checks.sw_a.enabled: must be true or false, got 0` / `got null` / `got ""` instead of asserting `open`
(3 failed, 10 passed). **D14 supersedes QA's "must yield open" expectation**: the
equivalent oracle is "the construction raises and the database is unchanged", which the tests above assert. That
oracle includes the rows staying `open`, and it is checked on a separate connection after the failed construction.
QA's `[False]`/`[True]` controls still pass (= `test_boolean_enabled_controls_are_unchanged`).

## QF2 (NOTE; V1.6) — reader table re-derived

Commands (non-test code; tests excluded):
`grep -rnE "(FROM|JOIN|UPDATE|INTO|TABLE( IF NOT EXISTS)?)\s+incidents\b" --include='*.py' reconcile | grep -v '/tests/\|/\.venv/'`,
plus `grep -rn incidents` for helper indirection (`m7` uses `m6.open_incidents`) and non-Python files (only a
comment in `docker-compose.reconcile.yaml`). Line numbers are at HEAD.

### Readers and writers of `incidents` (complete; replaces the original table)

| # | Access (HEAD) | Predicate | Behaviour for `disabled` rows | Evidence |
|---|---|---|---|---|
| 1 | `alerts.py:98` `open_incident` | `state='open'` | Never matched: a key found again opens a new incident | `test_disable_enable_disable_round_trip…`, `test_reopened_key_at_the_closure_block…` |
| 2 | `alerts.py:117` `_apply_one` resolve `UPDATE … WHERE id=?` | id of a row from #1 | Never resolved (only open rows reach it) | `test_reenabled_check_never_touches_its_disabled_rows` |
| 3 | `alerts.py:140` `_apply_one` `INSERT` | new id via #5 | A new `open` row; the disabled row is untouched | as #1 |
| 4 | `alerts.py:158` `_apply_one` refresh/escalate `UPDATE … WHERE id=?` | id of a row from #1 | Never updated | `test_seeded_switch_sweep[*]` (a `disabled` row never changes) |
| 5 | `alerts.py:173` `_id_taken` | any state | The id stays taken: a new incident at the same block gets `-2` | `test_reopened_key_at_the_closure_block…` |
| 6 | `alerts.py:180` `resolve_absent` | `state='open'` | Never resolved by absence | `test_reenabled_check_never_touches_its_disabled_rows` |
| 7 | `alerts.py:188` `resolve_not_refound` (via #1) | open only | Never resolved by the reorg re-find rule | as #6 |
| 8 | `alerts.py:217`, `:231` `realert` select + `UPDATE` | `state='open'` + D8 filter | Never re-alerted | V1.1 (26 h), `test_seeded_switch_sweep[*]` |
| 9 | `alerts.py:249` `digest` counts | `state='open'` | Not counted ("Open incidents: none") | V1.1 |
| 10 | `db.py:33-40` schema | partial unique index on `open` | A `disabled` row does not block a new open row for its key | as #1 |
| 11 | `db.py:80` legacy rename, `db.py:130` `_backfill_m5` `INSERT OR IGNORE` | legacy `m5_incidents` open rows | Not reached: it runs only when migrating a pre-framework database (`incidents` with `cluster_id`), which has no `disabled` rows; `OR IGNORE` keeps any existing id | by design: one-shot migration of a pre-CP1 schema |
| 12 | `eb_reconcile.py:235`, `:240` `_apply_switches` | `state='open'` | Never closed twice | `test_disable_enable_disable…` |
| 13 | `eb_reconcile.py:634` `run_check` `reorg_keys` | `state='open'` | Never in `reorg_keys` | `test_reenabled_check_never_touches_its_disabled_rows` |
| 14 | `eb_reconcile.py:797` `status.open_incidents` | `state='open'` | Excluded | V1.1, `test_status_and_export_after_the_closure` |
| 15 | `eb_reconcile.py:896` CLI `export` | any state | Printed with `state: disabled` | `test_status_and_export_after_the_closure` |
| 16 | `eb_reconcile.py:913` CLI `status.open` | `state='open'` | Excluded | `test_status_and_export_after_the_closure` |
| 17 | `metrics.py:47` `ssv_watch_open_incidents` | `state='open'` | Excluded | `test_metrics_open_count_excludes_disabled_rows` |
| 18 | `checks/m1_stale_tuple.py:380` (open keys), `:388` (open row) | `state='open'` | Invisible to M1 | by design: open-only predicate |
| 19 | `checks/m1_stale_tuple.py:384` `_resolved_since` | `state='resolved' AND resolved_block>?` | Not treated as a clearance to re-check after a reorg | by design: a `disabled` row is not a clearance. On re-enable M1 re-derives from its own detection tables (§6.2). QA's real-M1 probe (round 1) passes; the composition with the D9 catch-up is CP2 (V2.4) |
| 20 | **`checks/m1_stale_tuple.py:568-571` `_reported`** (new row) | `outbox JOIN incidents`, **any state** | A message linked to a `disabled` incident still counts as "already reported". A transient poisoning (poisoned and repaired between two runs) whose warn was sent before the disable, and which is re-detected after a reorg while M1 was off (same transactions re-included), is **not** sent again by the re-enabled M1. | `test_m1_reported_counts_a_message_of_a_disabled_incident` (real M1 on `ClusterChain`). It pins the behaviour, and a falsifier (`_reported` limited to `open`/`resolved`) reddens it with a second warn (`red-on-7639cff.txt`). By design, see below. |
| 21 | `checks/m2_operator_capacity.py:123` | `state='resolved' AND resolved_block>?` | As #19 | by design, as #19 (QA's real-M2 probe passes; CP2 V2.4) |
| 22 | `checks/m2_operator_capacity.py:135`, `:179` | `state='open'` | Invisible | by design: open-only predicate |
| 23 | `checks/m3_liquidation_zone.py:310` | `state='open'` | Invisible | by design: open-only predicate |
| 24 | `checks/m4_conservation_deficit.py:231` | `state='open'` | Invisible | by design: open-only predicate |
| 25 | `checks/m5_eb_desync.py:310` | id only (any state, with `m5_incidents`) | The id stays taken (unique-id loop) | by design: same rule as #5 |
| 26 | `checks/m6_legacy_coverage.py:108-110` `open_incidents` (also used by `m7_ssv_solvency.py:108`) | `state='open'` | Invisible to M6/M7 | by design: open-only predicate |
| 27 | **`scripts/fork_e2e.py:151-156` `sent`** (new row) | `outbox JOIN incidents`, any state | Lists the messages of a check's incidents whatever their state, including one whose incident was later closed as `disabled` | by design: evidence script for the anvil fork run, never imported by the worker. Listing every message a check produced is what it exists to show, and the messages were really queued or sent |
| 28 | `scripts/fork_e2e.py:160` `incident` | any state, newest first | May return a `disabled` row | by design: evidence only, as #27 |

**Why M1 `_reported` matching `disabled` rows is correct for a re-enabled M1.** `_reported` answers "did ops already
receive a message naming this poisoning transaction?". That is a fact about the outbox, and it does not depend on what
later happened to the incident: resolved rows are matched for the same reason. It is used only by `_transient`. The
still-true path (a stale tuple at the run's block) does not use it, so a condition still true after re-enable is still
reported, on a new incident, because `open_incident` is open-only: no missed page. Matching only open or resolved rows
would re-send an already-delivered transient warn after a reorg, a duplicate. A transient poisoning of the disabled
period was never sent and has no message, so `_reported` is false for it. Whether it is sent at re-enable is the D4/D9
catch-up question owned by CP2, and `_reported` does not change that. No `checks/m*.py` change, so no `ask`.

## QN1 (NOTE) — documentation

`reconcile/README.md` disable paragraph: "the next start, i.e. the next `run`, `once` **or `verify-layout`** (each
bootstraps the worker), closes …; the read-only `status` and `export` never close anything". QA's CLI probes
(`once`, `verify-layout`) already cover the behaviour. The runbook sentence in `deploy/README.md` stays CP2 (D13).

## State table (DEFECT-CLASSES; V1.6), complete and corrected

Rows 1–6 are the original rows. Wording is updated where the repair or QA round 1 added evidence. Row 7 is new (D14).

| Key / state | (a) empty | (b) error | (c) partial | (d) reorg depth 1 / N, incl. resolved keys | (e) restart between detection and handling | (f) same-block order | (g) time anchoring |
|---|---|---|---|---|---|---|---|
| `incidents.state='disabled'` | No open row at the disabling start: nothing is updated → `test_disabled_with_no_open_incident_writes_a_zero_marker` | Closure tx fails → rollback, rows stay `open`, next start closes them → `test_failed_closure_commits_nothing…`; RPC down at start → no closure, no realert → `test_rpc_outage_at_the_disabling_start…`; malformed switch → no closure at all, construction fails → `test_non_boolean_enabled_*` | Every `open` row of the check is closed in one tx; rows of other checks untouched → `test_enabled_check_realerts_and_resolves_beside_a_disabled_one`; resolved rows untouched → `test_disable_enable_disable_round_trip…` (earlier rows byte-identical) | Excluded from `reorg_keys` and the re-find rule (#7, #13), never resolved or reopened by a reorg run → `test_reenabled_check_never_touches_its_disabled_rows`; depth 1–4 → `test_seeded_switch_sweep[*]`; any-state readers keep their meaning across a reorg (M1 `_reported`, #20) → `test_m1_reported_counts_a_message_of_a_disabled_incident` | Closure is at start, never at detection; a crash before its commit leaves the rows `open`, the next start closes them → `test_failed_closure…` | By design: closure per start, not per event; a key re-found at the closure block gets `-2` → `test_reopened_key_at_the_closure_block…` | `resolved_block` = checkpoint at the closing start (`start_block − 1` on a new database), `updated_at`/`evidence.disabled.at` = start clock → `test_disabled_check_closes…`, `test_disabled_on_a_brand_new_database…`; never re-alerted → V1.1 26 h loop |
| meta `disabled:<id>` = `{block, at, closed}` | `closed: 0` → zero-marker test | Same tx as the UPDATEs: both or neither → `test_failed_closure…`; malformed switch → never written → `test_non_boolean_enabled_*` (no `disabled:` row) | Marker present + new open rows (not produced by this worker) → closed, `closed` incremented (by design, defensive; unreachable through the worker since a disabled check never runs) | Untouched by `check_reorg` (by design: it records the disable, not chain data); kept across reorgs → `test_seeded_switch_sweep[*]` | Second disabled start → unchanged (`test_disable_enable_disable…`); enabling start deletes it, also with `backfill.enabled` false → `[False]` variant; RPC failure before bootstrap → cleared at the first successful bootstrap (sweep `outage` action) | n/a (one per check) | `block`/`at` = the first disabling start; kept across later disabled starts → round-trip test |
| meta `cursor:events:<id>` / `reorg:<id>` of a non-built id (D11) | An id with no row gets no row → `test_reorg_while_disabled…` (`meta` table asserted) | Rewind inside the reorg tx (unchanged); failure rolls back as at base → by design | Only ids with a cursor/marker row; `cursor:inbox:*` not matched → by design + `meta_keys` assertion | Depth 1 and 4 → `[1]`, `[4]`; two reorgs keep the minimum ancestor → `test_two_reorgs…`; the replay on re-enable sees only new-chain events → `test_reenabled_after_a_reorg…`; real M1 re-detects from the rewound cursor → `test_m1_reported_counts…` | Marker pending while disabled, replayed by `Worker.__init__` at the enabling start → `test_reenabled_after_a_reorg…`; not in `status.reorg_pending`/`degraded()` → `[1]`/`[4]` | n/a | Cursor anchored at the ancestor `[A, END_OF_BLOCK]` → `[1]`/`[4]` |
| D8 re-alert filter (`realert(set(self.checks))`) | No built check → no re-alert (V1.1 builds none) | Before bootstrap the step returns early; `housekeeping()` still filters → `test_rpc_outage…` | Built checks keep their cadence → `test_enabled_check_realerts…` | Unchanged reorg gate for built checks (base tests green) | A not-built, not-disabled check's rows stay open and silent, resuming at their cadence when it is built again → `test_a_check_that_is_merely_not_built…` | n/a | 6 h / 24 h cadence unchanged for built checks → `test_enabled_check_realerts…` |
| `status.disabled`, digest line | `[]` / line omitted → `test_digest_has_no_disabled_line…`, merely-not-built test | Read-only CLI before any start → lists it, closes nothing → `test_readonly_status…`; malformed switch → `status` refuses → `test_non_boolean_enabled_stops_every_cli_command…[*-status]` | Sorted config ids (literal `false` only) minus built → `test_build_order…`, example-config test, `test_validate_switches…` | n/a | Listed from config at once, also before the closing start (by design: it shows the configuration) | n/a | Digest uses the existing `digest_interval_s` / `not backfilling` rules (unchanged) |
| Outbox rows queued before the closure | — | Delivery retries unchanged | — | Still matched by M1 `_reported` (#20): an already-sent transient report is not re-sent → `test_m1_reported_counts…` | Still delivered after the closure (`deliver()` state-agnostic, D7 by design) → V1.1 asserts `sent_at` and the capture | — | — |
| **Config `checks.<id>.enabled` (D14, new)** | Absent → enabled (unchanged) → `test_build_order…`, `test_validate_switches…` (`{}` accepted) | Non-boolean (`0`, `null`, `""`, `"false"`, `1`, `"true"`, `0.0`, `[]`, `{}`) or non-object entry → `SystemExit` naming the id at `Worker` construction, before any DB access, for `run`/`once`/`verify-layout`/`status` → `test_non_boolean_enabled_*` (DB dump unchanged), `test_validate_switches…`; `export`/`audit` build no worker, unaffected (by design, see QF1) | One malformed id among valid ones → whole config rejected → `test_validate_switches…` (`sw_a: false` beside a malformed `sw_b`) | n/a (config, read at start) | Every start re-validates; a corrected config then behaves as `true`/`false` → `test_boolean_enabled_controls_are_unchanged` | n/a | n/a |

## Verification results (repair)

| V | Result | Note |
|---|---|---|
| V1.1 | PASS | Unchanged tests green. The not-built control is unchanged. |
| V1.2 | PASS | Unchanged tests green. |
| V1.3 | PASS | QF1 repaired: non-booleans are rejected at construction with the DB unchanged (25 tests + unit); `true`/`false` controls unchanged; `build` byte-identical; `test_config_example_loads_and_builds_every_check` and the example-config copy test green. |
| V1.4 | PASS | Unchanged tests green; the real-M1 test adds a reorg-while-disabled replay. |
| V1.5 | PASS | Unchanged tests green; a malformed switch closes nothing on any command. |
| V1.6 | PASS (claimed) | Reader table re-derived (28 rows, incl. writers): QA's two missing readers added (#20, #27) with behaviour and a test or design reason. `checks/README.md` §4 prose corrected. State table complete with the D14 row. |
| V1.7 | PASS | Full suite **2,395 passed** (2,366 + 29), exit 0; `ruff check reconcile`: all checks passed (`suite.txt`). `test_backfill.py` sha256 `28da7d49e950a0bdc51ee7d4f6993ad757fa72d60524c39a7c28efcc24702d35` (= base). Guarded paths unchanged vs `7639cff`. New QF1 tests red on `7639cff` for the behavioural reason (`red-on-7639cff.txt`). |

The original 53 tests' red-on-`9e94a1a` evidence (`workers/cp1/red-on-base.txt`) is reused unchanged. Those tests are
byte-identical, and the repair touches only the switch validation, which they do not exercise with non-booleans.

## `git diff --stat`

`7639cff..HEAD` (the repair commit):

```
 .../check-switches/workers/cp1/repair-1/handoff.md | 225 +++++++++++++++++++++
 .../workers/cp1/repair-1/red-on-7639cff.txt        |  58 ++++++
 .../check-switches/workers/cp1/repair-1/suite.txt  |  21 ++
 reconcile/README.md                                |  19 +-
 reconcile/checks/README.md                         |  10 +-
 reconcile/checks/registry.py                       |  25 ++-
 reconcile/eb_reconcile.py                          |   8 +-
 reconcile/tests/test_switches.py                   | 182 +++++++++++++++++
 8 files changed, 529 insertions(+), 19 deletions(-)
```

`61307dd..HEAD` (the whole CP1 candidate, from the frozen dispatch base):

```
 plans/check-switches/workers/cp1/falsifiers.txt    |  66 ++
 plans/check-switches/workers/cp1/handoff.md        | 104 +++
 plans/check-switches/workers/cp1/red-on-base.txt   | 172 +++++
 .../check-switches/workers/cp1/repair-1/handoff.md | 225 ++++++
 .../workers/cp1/repair-1/red-on-7639cff.txt        |  58 ++
 .../check-switches/workers/cp1/repair-1/suite.txt  |  21 +
 plans/check-switches/workers/cp1/suite.txt         |  32 +
 reconcile/README.md                                |  17 +-
 reconcile/alerts.py                                |  17 +-
 reconcile/checks/README.md                         |  16 +-
 reconcile/checks/registry.py                       |  26 +-
 reconcile/eb_reconcile.py                          |  48 +-
 reconcile/tests/test_switches.py                   | 765 +++++++++++++++++++++
 13 files changed, 1556 insertions(+), 11 deletions(-)
```

## New findings and risks

1. **`export`/`audit` do not validate the switches** (no `Worker`). This differs from D14's parenthetical; neither
   command can close anything. Owner: coordinator, if D14 must cover them too (one line in `main()`).
2. **A non-object `checks.<id>` now fails with a clear `SystemExit`.** Before, it crashed in `build`. It fails loud in
   both versions; only the message is new.
3. **D14 rejects `"enabled": "true"` and `1`.** Before the repair these were silently enabled. A deployed config with
   such a value would now stop at start. Shipped configs (`config.example.json`,
   `deploy/config/networks/evm_ethereum_mainnet.json`) contain no `checks.<id>.enabled` at all, so nothing shipped is
   affected. The runbook (CP2, D13) should name the error.
4. Carried over unchanged from the original handoff (owners as stated there): not-built ≠ disabled silence (D8), the
   M1/M2 `_resolved_since` composition (CP2 V2.4), and pending outbox delivery after closure (runbook, CP2).

## Next owner

The coordinator dispatches CP1 QA round 2 (Codex, fresh `new-child` worktree of `cs/cp1-r1`). QA needs this file plus
the original handoff. Suggested first attacks: QF1 across every `Worker` caller, using the DB-unchanged oracle; the
reader re-derivation with the commands above; DEFECT-CLASSES 1, 10 and 11 on the new D14 row. CP2 is not started.
