# CP1 — Clean disable: implementer handoff (attempt 1)

Run `run_3869f8caafaa` · task `task_1068470b2829` · implementer Claude Opus 5.5 (high, per packet) · 2026-11-06

## Identity

- Worktree `/Users/marco/orca/workspaces/ssv-network-monitor/cs-cp1` (Orca), branch **`cs/cp1`**. I created it from the dispatch head because the worktree was on `mtabasco/cs-cp1` at the same sha. `plan/check-switches` was not touched.
- BASE = `61307ddf3f813cfbe38ef062490224862ccd2b91` (`chore(plan): CP1 dispatch`, parent `00fe82315cb9…`), with a clean tree at start. I verified both before editing. The code is identical to `9e94a1a`.
- Inputs re-hashed at start: role document `6548b79b…`, DEFECT-CLASSES `7daa55f6…`, plan `c396a859…`, `test_backfill.py` `28da7d49…`. All are equal to the packet.
- HEAD = the single candidate commit on `cs/cp1` that adds this file. Its sha is in the `worker_done` message (`git log -1 cs/cp1`).
- Environment: `uv venv reconcile/.venv --python 3.12` plus `requirements-dev.txt` (Python 3.12.15, uv 0.13.4), with no other installs. The base suite ran green before any edit: **2,313 passed** (= F1).

## Changes (Allowed writes only)

| File | Change |
|---|---|
| `reconcile/checks/registry.py` | New `disabled(settings)` lists the registered ids that `build` skips for `enabled`, sorted. It uses the same test as `build` (`not checks.<id>.get("enabled", True)`) and ignores `only`. `build` is byte-unchanged. |
| `reconcile/eb_reconcile.py` | `Worker.__init__`: `self.disabled` = `disabled(cfg)` minus the built ids (D6: a not-built check is never disabled, and a built one never is). `bootstrap()`: `_apply_switches()` runs inside the identity/checkpoint/phase transaction (after their init, before the topics backfill, so incidents and marker commit together — D7). For each disabled id it moves `open` → `disabled`, sets `resolved_block` = the checkpoint and `updated_at` = now, adds `evidence.disabled = {block, at}`, and writes `disabled:<id>` = `{block, at, closed}` when the marker is absent. If the marker is present, nothing changes, unless open rows exist: those are closed and `closed` is incremented. Every built check's `disabled:<id>` is deleted. No outbox row is written. `check_reorg()`: the rewind loop covers the built ids ∪ every id with a `cursor:events:*` or `reorg:*` meta row (D11). `housekeeping()`: `realert(set(self.checks))` (D8) and `digest(…, disabled=self.disabled)`. `status()`: `"disabled": self.disabled`. |
| `reconcile/alerts.py` | `realert(check_ids=None)` skips incidents of checks outside `check_ids` (None = all, unchanged for direct callers). `digest(…, disabled=())` adds the line `Disabled checks: a, b` after "Open incidents: …", and omits it when none. The module docstring now names the `disabled` state. |
| `reconcile/tests/test_switches.py` | New, 53 tests (below). |
| `reconcile/checks/README.md` §4 | Re-alerts only for built checks; a new "Incident states" paragraph (`disabled`, no message, readers, not-built ≠ disabled). |
| `reconcile/README.md` | Loop step 4 (re-alerts of the checks it runs); `status` gains `disabled`; a "Disabling a check" paragraph. |
| `plans/check-switches/workers/cp1/` | `handoff.md`, `suite.txt`, `red-on-base.txt`, `falsifiers.txt`. |

**Exclusions (unchanged):** `db.py` (no schema change: `state` is TEXT and the partial unique index covers `open` only), `metrics.py`, `checks/m*.py`, `checks/__init__.py`, `tests/test_m*.py`, `tests/test_backfill.py`, `tests/harness.py`. The tests use a local `Env` helper instead of a harness keyword. Also unchanged: `deploy/README.md` and `config.example.json` (both CP2 / D13), §3/§7 of `checks/README.md` (CP2), the D9 catch-up and the Monitor rules. `suite.txt` checks the guarded paths against `9e94a1a`: none changed.

## Verification results

| V | Result | Tests (`reconcile/tests/test_switches.py`) |
|---|---|---|
| V1.1 | PASS | `test_disabled_check_closes_its_incidents_and_is_never_realerted[False/True]` (restart `backfill.enabled` false/true; sent page, sent warn, silent warn and sent info, the info's `open` row still undelivered at closure; 26 × 1 h steps; `digest_interval_s: 1`). It asserts: zero `realert` rows; 0 rows created by the start; 4 × `disabled`; `resolved_block` and `evidence.disabled.block` = the checkpoint; marker `closed: 4`; no `resolved`/`health` row; the queued info row delivered (D7); `run:sw_a` unchanged; `status.disabled == ["sw_a"]` and `open_incidents` without sw_a; last digest has "Open incidents: none" and the line "Disabled checks: sw_a". Must-reject control: `test_a_check_that_is_merely_not_built_is_not_disabled` (`only=` without the check, no `enabled: false`: rows stay `open`, no marker, `status.disabled == []`). Also `test_digest_has_no_disabled_line_without_disabled_checks`. |
| V1.2 | PASS | `test_disable_enable_disable_round_trip_sends_nothing_at_start[False/True]`: every start creates 0 outbox rows; the first disable gives `closed: 2`; a second disabled start leaves marker and rows identical; the enabling start clears the marker; the re-found keys open new ids; the second disable gives `closed: 2` for exactly the new rows; the earlier disabled rows are byte-identical (never closed twice). Also `test_disabled_with_no_open_incident_writes_a_zero_marker` (`closed: 0`, block = checkpoint), `test_disabled_on_a_brand_new_database_anchors_at_start_block_minus_one` (`block = start_block − 1`) and `test_reopened_key_at_the_closure_block_gets_a_new_incident_id` (re-found at the same block → `<id>-2`, the disabled row untouched). |
| V1.3 | PASS | `test_enabled_check_realerts_and_resolves_beside_a_disabled_one`: A disabled, B enabled; `list(w.checks) == ["sw_b"]`; the realert rows are B's only; B's resolution is sent; A stays `disabled`. `test_build_order_unchanged_and_disabled_ids` (registration order with and without a disabled id, `disabled()` on the example config, sorted, `only=`). `test_built_check_with_the_same_id_is_never_disabled`. `test_example_config_with_a_disabled_check_lists_it_in_status` (copy of `config.example.json` with `m7.enabled: false` → `load_config` + `Worker` → `status()["disabled"] == ["m7"]`, the other checks in registration order). The existing `test_framework.py::test_config_example_loads_and_builds_every_check` is green and unchanged. |
| V1.4 | PASS | `test_reorg_while_disabled_rewinds_the_disabled_cursor[1/4]` (reorg depth 1 and 4 blocks; X = sw_a disabled, its cursor above the fork). It asserts: `cursor:events:sw_a == [ancestor, END_OF_BLOCK]`; `reorg:sw_a == ancestor`; a built unregistered stub `y` runs with reason `reorg` and commits; no `cursor:events:`/`reorg:` row for `sw_b`/`sw_c` (checked in the `meta` table); not in `status.reorg_pending`/`degraded()`. `test_two_reorgs_while_disabled_keep_the_lowest_ancestor`. `test_reenabled_after_a_reorg_while_disabled_replays_the_new_chain`: the first run after the re-enable carries `reorg` and `ctx.reorg_ancestor == ancestor`, and sees only the new chain's event. The base test `test_scheduler_reorg_resets_event_cursor` is green and unchanged. |
| V1.5 | PASS | (a) `test_failed_closure_commits_nothing_and_the_next_start_closes_once`: `DB.set_meta` raises once on `disabled:*`, after the incident UPDATEs. The start raises; the rows stay `open` with no marker. The next start closes all 4 and writes `closed: 4` once with no outbox row; a third start changes nothing. Also `test_rpc_outage_at_the_disabling_start_closes_later_and_never_realerts`: `eth_chainId` fails at the disabling start for 7 h. The rows stay open and `housekeeping()` sends no realert (D8). After recovery `step()` bootstraps and closes. (b) `test_readonly_status_lists_the_disabled_check_and_closes_nothing`: `main(["status", …])` on the example-config copy with `sw_a.enabled: false` before any run start exits 0, has `disabled == ["sw_a"]`, closes nothing (4 rows `open`, no marker, no outbox row) and lists the 4 rows in `open`. |
| V1.6 | PASS | State table and reader table below. `test_status_and_export_after_the_closure` covers `export` printing `state: disabled` and the CLI `open`/`open_incidents` without them. `test_metrics_open_count_excludes_disabled_rows` covers the metrics reader. `test_reenabled_check_never_touches_its_disabled_rows` covers the re-find rule and `resolve_on_absence`. |
| V1.7 | PASS | Full suite **2,366 passed** (2,313 + 53), exit 0. `ruff check reconcile`: all checks passed. `test_backfill.py` sha256 `28da7d49e950a0bdc51ee7d4f6993ad757fa72d60524c39a7c28efcc24702d35` (= base) and 67 passed. No guarded file changed. Red on `9e94a1a`: 51 of 53 fail. The named regressions fail behaviourally: V1.1 on the 6 h page `realert` row (`sw_a-kpage-1001:realert:1`, created at clock + 6 h 02 min, "Still open (alert 2)"); V1.4 on the un-rewound cursor (`[1004, END] == [1002, END]` at depth 1, `[1007, END] == [1005, END]` at depth 4). Evidence: `suite.txt`, `red-on-base.txt`. |

Also run: a seeded adversarial sweep, `test_seeded_switch_sweep[0..29]`. It does random restarts (enable/disable, backfill on/off), 1–600 s and 7 h steps, reorgs of depth 1–4, findings that open, escalate, mute or resolve, an RPC outage at a start, and confirmations 0/2. After every action it checks these invariants: a disabled period creates no outbox row of the check; no `open` row while disabled; the marker exists while disabled and is absent while enabled; a `disabled` row never changes again; at most one open row per key. Seven falsifiers, one per mechanism, each turn named tests red (`falsifiers.txt`): the D8 filter, the closure UPDATE, the D11 rewind, D6 derived from not-built, the marker clear, the digest line, and marker idempotency.

## State table (DEFECT-CLASSES; V1.6)

| Key / state | (a) empty | (b) error | (c) partial | (d) reorg depth 1 / N, incl. resolved keys | (e) restart between detection and handling | (f) same-block order | (g) time anchoring |
|---|---|---|---|---|---|---|---|
| `incidents.state='disabled'` | No open row at the disabling start: nothing is updated → `test_disabled_with_no_open_incident_writes_a_zero_marker` | The closure transaction fails → rollback, rows stay `open`, the next start closes them → `test_failed_closure_commits_nothing…`; RPC down at start → no closure and no realert → `test_rpc_outage_at_the_disabling_start…` | Every `open` row of the check is closed in one statement set within one tx; rows of other checks untouched → `test_enabled_check_realerts_and_resolves_beside_a_disabled_one`; resolved rows untouched → `test_disable_enable_disable_round_trip…` (earlier rows byte-identical) | `disabled` rows are excluded from `reorg_keys` and from the re-find rule (`open` only), so they are never resolved or reopened by a reorg run, including rows last seen above the ancestor → `test_reenabled_check_never_touches_its_disabled_rows`; across depth 1–4 → `test_seeded_switch_sweep[*]` (frozen-row invariant) | Closure is at the start, never at detection. A crash before its commit leaves the rows `open` and the next start closes them → `test_failed_closure…`. The first disabled start that commits closes. | By design: the closure is per start, not per event; ids keep their `opened_block` suffix; a key re-found at the closure block gets `-2` → `test_reopened_key_at_the_closure_block…` | `resolved_block` = the checkpoint at the closing start (`start_block − 1` on a new database), `updated_at`/`evidence.disabled.at` = start clock → `test_disabled_check_closes…`, `test_disabled_on_a_brand_new_database…`; never re-alerted (no cadence applies) → V1.1 26 h loop |
| meta `disabled:<id>` = `{block, at, closed}` | `closed: 0` → zero-marker test | Written in the same tx as the UPDATEs: both or neither → `test_failed_closure…` | Marker present and new open rows (not produced by this worker) → closed and `closed` incremented (by design, defensive; not reachable through the worker since a disabled check never runs) | Untouched by `check_reorg` (it records the disable, not chain data) → by design; the sweep keeps it present while disabled across reorgs → `test_seeded_switch_sweep[*]` | Second disabled start → unchanged (`test_disable_enable_disable…` asserts dict equality); enabling start deletes it, also with `backfill.enabled` false → `[False]` variant; RPC failure before bootstrap → not cleared yet, cleared at the first successful bootstrap (sweep `outage` action) | n/a (one per check) | `block`/`at` = the first disabling start; kept across later disabled starts → round-trip test |
| meta `cursor:events:<id>` / `reorg:<id>` of a non-built id (D11) | An id with no row gets no row → `test_reorg_while_disabled…` (`meta` table asserted) | The rewind is in the reorg transaction (unchanged); a failure rolls back everything as at base → by design (`check_reorg` tx unchanged) | Only ids with a cursor/marker row; `cursor:inbox:*` not matched (GLOB on `cursor:events:*`) → by design + `meta_keys` assertion | Depth 1 and 4 → `[1]`, `[4]`; two reorgs keep the minimum ancestor → `test_two_reorgs…`; the replay on re-enable sees only new-chain events → `test_reenabled_after_a_reorg…` | Marker pending while disabled, replayed by `Worker.__init__` at the enabling start (`on_reorg`, reason `reorg`) → `test_reenabled_after_a_reorg…`; not in `status.reorg_pending`/`degraded()` while disabled → `[1]`/`[4]` | n/a | Cursor anchored at the ancestor (`[A, END_OF_BLOCK]`) → `[1]`/`[4]` |
| D8 re-alert filter (`realert(set(self.checks))`) | No built check → no re-alert at all (V1.1 builds none) | Before bootstrap the step returns early; `housekeeping()` still filters → `test_rpc_outage…` | Built checks keep their cadence → `test_enabled_check_realerts…` | Unchanged reorg gate for built checks (base tests green) | A not-built, not-disabled check's rows stay open and silent while it does not run, and resume at their cadence when it is built again (unchanged lifecycle) → `test_a_check_that_is_merely_not_built…` | n/a | 6 h / 24 h cadence unchanged for built checks → `test_enabled_check_realerts…` |
| `status.disabled`, digest line | `[]` / line omitted → `test_digest_has_no_disabled_line…`, merely-not-built test | Read-only CLI before any start → lists it, closes nothing → `test_readonly_status…` | Sorted config ids minus built → `test_build_order…`, example-config test | n/a | Listed from config at once, also before the closing start (by design: it shows the configuration) | n/a | The digest uses the existing `digest_interval_s` and `not backfilling` rules (unchanged) |
| Outbox rows queued before the closure | — | Delivery retries unchanged | — | — | Still delivered after the closure (`deliver()` is state-agnostic: D7, by design) → V1.1 asserts `sent_at` and the capture | — | — |

### Readers of `incidents` (V1.6; re-derive with `grep -rn "FROM incidents" reconcile`; line numbers at HEAD)

| Reader | Predicate | Behaviour for `disabled` rows | Evidence |
|---|---|---|---|
| `alerts.py:98` `open_incident` | `state='open'` | Never matched: a re-found key opens a new incident | `test_disable_enable_disable…` (new ids), `test_reopened_key_at_the_closure_block…` |
| `alerts.py:173` `_id_taken` | any state | The id stays taken: a new incident at the same block gets `-2` | `test_reopened_key_at_the_closure_block…` |
| `alerts.py:180` `resolve_absent` | `state='open'` | Never resolved by absence | `test_reenabled_check_never_touches_its_disabled_rows` |
| `alerts.py:217` `realert` | `state='open'` + D8 filter | Never re-alerted | V1.1, `test_seeded_switch_sweep[*]` |
| `alerts.py:249` `digest` counts | `state='open'` | Not counted ("Open incidents: none") | V1.1 |
| `eb_reconcile.py:233` `_apply_switches` (new) | `state='open'` | Never closed twice | `test_disable_enable_disable…` |
| `eb_reconcile.py:632` `run_check` `reorg_keys` | `state='open'` | Never in `reorg_keys`, never resolved by the re-find rule (`resolve_not_refound` → `open_incident`, open only) | `test_reenabled_check_never_touches_its_disabled_rows` |
| `eb_reconcile.py:795` `status.open_incidents` | `state='open'` | Excluded | V1.1, `test_status_and_export_after_the_closure` |
| `eb_reconcile.py:894` `export` | any state | Printed with `state: disabled` | `test_status_and_export_after_the_closure` |
| `eb_reconcile.py:911` CLI `status.open` | `state='open'` | Excluded | `test_status_and_export_after_the_closure` |
| `metrics.py:47` `ssv_watch_open_incidents` | `state='open'` | Excluded | `test_metrics_open_count_excludes_disabled_rows` |
| `checks/m1_stale_tuple.py:380,388` | `state='open'` | Invisible to M1 (code unchanged) | by design: open-only predicate; M1 runs only when built, and then its rows are not `disabled` until a later disable |
| `checks/m1_stale_tuple.py:384` `_resolved_since` | `state='resolved' AND resolved_block>?` | Not treated as a clearance to re-check after a reorg | by design: a `disabled` row is not a clearance; on re-enable the check re-evaluates from its own state (§6.2 item 5) — CP2 V2.4 composes this |
| `checks/m2_operator_capacity.py:123` | `state='resolved' AND resolved_block>?` | As M1:384 | by design, as above |
| `checks/m2_operator_capacity.py:135,179` | `state='open'` | Invisible | by design: open-only predicate |
| `checks/m3_liquidation_zone.py:310` | `state='open'` | Invisible | by design: open-only predicate |
| `checks/m4_conservation_deficit.py:231` | `state='open'` | Invisible | by design: open-only predicate |
| `checks/m5_eb_desync.py:310` | id only (any state, plus `m5_incidents`) | The id stays taken (unique-id loop) | by design: same as `_id_taken` |
| `checks/m6_legacy_coverage.py:109` `open_incidents` | `state='open'` | Invisible | by design: open-only predicate |
| `scripts/fork_e2e.py:160` | any state, newest first | May return a `disabled` row | by design: evidence script only; never in the worker |

## `git diff --stat BASE..HEAD` (untracked files included: all were added to the commit)

```
 plans/check-switches/workers/cp1/falsifiers.txt  |  66 +++
 plans/check-switches/workers/cp1/handoff.md      | 104 ++++
 plans/check-switches/workers/cp1/red-on-base.txt | 172 +++++++
 plans/check-switches/workers/cp1/suite.txt       |  32 ++
 reconcile/README.md                              |  14 +-
 reconcile/alerts.py                              |  17 +-
 reconcile/checks/README.md                       |  12 +-
 reconcile/checks/registry.py                     |  11 +-
 reconcile/eb_reconcile.py                        |  46 +-
 reconcile/tests/test_switches.py                 | 583 +++++++++++++++++++++++
 10 files changed, 1046 insertions(+), 11 deletions(-)
```

## New findings and risks

1. **`verify-layout` and `once` also close.** Every command that calls `bootstrap()` (`run`, `once`, `verify-layout`) is a "start" for D7. A `verify-layout` with `m5.enabled: false` would close M5's open incidents. This matches "the next start", but the runbook (CP2, D13) should say it. Owner: CP2 docs.
2. **`enabled` follows `build`'s truthiness.** `disabled()` uses the same test as `build`, so `enabled: 0` or `null` also count as disabled. The alternative was to treat only literal `false` as disabled; that would leave a check skipped by `build` but not listed (not built, rows open, silently not re-alerted). Owner: coordinator, if D6 "explicit `false`" must mean the JSON literal only.
3. **A not-built, not-disabled check's incidents are now silent (D8).** Before CP1, its open incidents kept re-alerting; now they wait until it is built again. Production always builds every enabled check, so this affects tests' `only=` and stub workers. It is D8 as decided. Recorded so QA can confirm it is not a missed page.
4. **M1/M2 `_resolved_since` ignores `disabled` rows.** After a reorg while disabled, a re-enabled M1/M2 does not re-check its disabled keys as "resolved keys whose clearance may be orphaned". By design, a disabled row is not a clearance: the check re-finds from its own tables and the catch-up. This matters for CP2's composition test (V2.4). Owner: CP2.
5. **Undelivered rows are delivered after the closure** (D7, by design). A page queued just before a disabling restart is still sent after it. This is a runbook sentence for CP2.
6. **No `checks/m*.py`, `db.py` or metrics change was needed.** I raised no coordinator question.

## Next owner

The coordinator dispatches the CP1 QA (Codex GPT Sol 6.1 high, fresh `new-child` worktree of the candidate head). QA should attack DEFECT-CLASSES 1, 2, 10 and 12 first. CP2 is not started.
