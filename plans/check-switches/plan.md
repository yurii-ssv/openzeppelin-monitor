# Plan: safe `checks.<id>.enabled` switches in the watch worker (clean disable, silent catch-up)

Tier: Standard (two sequential CPs, one implementer at a time, one independent QA round per CP)
Owner: Marco · Created: 2026-11-06 · Run directory: `plans/check-switches/` in the execution worktree
Current state: [state.md](state.md) · History: [execution-log.md](execution-log.md) · Planner input: [HANDOFF-coordinator.md](HANDOFF-coordinator.md) · Reviews: [reviews/](reviews/)

## Global rules (context packet)

Every dispatched packet contains this section verbatim, the D rows, the assigned CP block, the local role document and [qa/DEFECT-CLASSES.md](qa/DEFECT-CLASSES.md).

- **Roots and starting identities.**
  - Execution worktree (Orca-managed): `/Users/marco/orca/workspaces/ssv-network-monitor/check-switches`, branch `plan/check-switches`, code base `9e94a1a` (= `plan/monitoring-service` head; PR #1 open on `yurii-ssv/openzeppelin-monitor`). Tree clean at plan time except the run directory and `reconcile/.venv` (both ignored; the run directory until the plan commit, see Dispatch).
  - Every CP starts from the frozen base named in its dispatch (the `plan/check-switches` head after the previous CP's integration). Workers verify `git rev-parse HEAD` equals it before editing.
  - Reference, read-only for every role: `/Users/marco/ssv/repos/ssv-research/checkpointed-workflows/` (kit), the M17 plan worktree `/Users/marco/orca/workspaces/ssv-network-monitor/m17-imp-credit` (consumer of A3; never edited here).
- **Inputs (pinned).**

  | Input | Path | sha256 |
  |---|---|---|
  | Coordinator handoff (scope agreed with Marco 2026-11-06) | [HANDOFF-coordinator.md](HANDOFF-coordinator.md) | `cad51e5cee272e36449221bb260b88f5d2fb573e985839a16dd3703510a4ecbc` |
  | Kit (rev 2026-10-29, ssv-research `8390099` + uncommitted) | `checkpointed-workflows/`: invariants `28da5733…`, operating-protocol `4f6ad173…`, plan-template `390361c1…`, common-pitfalls `3154922f…`, runtime-orca `2248d5b0…`, README `c246f80c…` | as listed (re-hashed 2026-11-06, equal to the handoff) |
  | Recurring defect classes (copy of the previous runs' file) | [qa/DEFECT-CLASSES.md](qa/DEFECT-CLASSES.md) | `7daa55f69bdd3218f83283a0bd752075a84141888196869977c046819def5876` |
  | Framework contract and D17 behaviour (base `9e94a1a`) | `reconcile/checks/README.md` §3, §4, §6, §7; `reconcile/tests/test_backfill.py`; `deploy/README.md` "Backfill phase (D17)" | at base (the oracle for A4 is the committed file, see V2.8) |

- **Scope and consumer.** Make the existing per-check switch `checks.<id>.enabled` (worker `reconcile/`, checks `m1`–`m14`) safe in both directions: disabling a check closes its open incidents as `disabled`, stops their re-alerts and lists the check in `status` and the digest; re-enabling a check, or adding a new check to a live database, evaluates everything since the check's last committed run **silently** and alerts from the next run (one alert for a condition still true then). A config change takes effect at worker restart. Consumers: SSV ops (Marco), DevOps (runbook), and the M17 plan (its go-live silence relies on A3). **Excluded (Marco):** a switch per alert type inside a check; severity overrides; live config reload; the OpenZeppelin Monitor rules in `config/monitors/` (own switches; M8/M10/M11/M13 have rules on both sides, M15 is Monitor-only: runbook note only); the existing sub-switches `m9.dissent_enabled`, `m11.revert_rate_enabled`, `m11.zero_round_proof_enabled` (unchanged); metrics changes (NOTE candidate only); any `checks/m*.py` change (needs a coordinator decision).
- **Authority.**
  - Workers commit only on their own CP branch inside their own Orca worktree; one candidate commit per CP (fix-ups squashed or listed; the handoff names the final head).
  - Never `git push`, never open a PR (Marco pushes). Never send a mainnet transaction. No mainnet call is needed by any CP: every V row runs on `FakeChain`; the delivery smoke (Closure) uses read-only JSON-RPC on the public archive endpoints only.
  - Alerts only in record mode or to `TEST_*` channels. Never write `config/`, `config.zip`, `.env`, `.local-dry/` (user-owned, ignored). Never modify, delete or mount read-write an existing Docker volume or retag an existing image (delivery smoke: own `cs-*` names only).
  - Never print secrets into evidence files.
  - No installs beyond `uv venv reconcile/.venv --python 3.12` and `uv pip install --python reconcile/.venv/bin/python -r reconcile/requirements-dev.txt`.
  - Every shell: `export PATH=$HOME/.foundry/bin:$PATH`. Orca: `/Applications/Orca.app/Contents/Resources/bin/orca` (1.4.250 measured 2026-11-06; the handoff said 1.4.246: load the version-matched guides).
  - Commits end with the committing session's attribution line (`Co-Authored-By: <model name> <noreply@anthropic.com>`; execution coordinator: `Claude Opus 5.5`).
  - Decision owner: Marco. External actions and deletions need his concrete instruction.
- **Coordinator model / effort:** a new Claude Code session started by Marco for execution, Claude Opus 5.5 (`claude-opus-5-5`), effort medium (D1, Marco 2026-11-06). The plan was written by Claude Fable 5.1 high and revised by Opus 5.5 medium.
- **Worker model / effort (D1):** every CP implementer and repair worker Claude Opus 5.5 high via Orca, `--agent claude --model opus --effort high`. Record requested and `launch.effective` on every start; a mismatch stops the start (no silent fallback).
- **QA model / effort (D1):** every independent QA round Codex GPT Sol 6.1 high via Orca, `--agent codex --model gpt-6.1-sol --effort high`, in a fresh `new-child` worktree of the candidate head. No other agent renders a QA verdict. No evidence runner is assigned (the delivery smoke is run by the coordinator, see Closure). If Codex cannot start or `launch.effective` differs, the coordinator stops and asks Marco.
- **Local role documents** (copied from the kit 2026-11-06; Marco authorized the copy by asking for the plan):

  | Role | Path | sha256 (= kit source) |
  |---|---|---|
  | Implementer | [agents/checkpoint-implementer.md](agents/checkpoint-implementer.md) | `6548b79b35aa7ea8fd0bd9db33ab7161d02916b8cb061faa3c4ecd6af2c3b406` |
  | QA | [agents/checkpoint-qa.md](agents/checkpoint-qa.md) | `ec15481953f5defa91e6da9089b51a7e7ed53a8a54d8554ac6f970ba64c35cbf` |
  | Evidence runner (not assigned) | [agents/evidence-runner.md](agents/evidence-runner.md) | `e52a719b09ef1f3aa2a94f03a898cf122b9a925d77394d27e789c2606772a733` |

- **Policy (D2).**
  - BLOCKING = a missed or lost page, a false clean, a false page, an unsafe action, data loss, a fail-open gate, a secret leak. For this plan, "false page" includes **any** message (page, warn or info) sent for an event or condition that belongs to a disabled period or to a check's catch-up (the stale burst this plan exists to remove), and a re-alert of an incident closed as `disabled`; "missed page" includes a condition still true at a check's first live run that is not sent, and a live alert of another check silenced by one check's catch-up; "false clean" includes an incident left `open` (re-alerting) after its check was disabled, and a D17 regression (`test_backfill.py` red).
  - NOTE = a false, missed or duplicated **warn** or info outside the cases above, a changed message text, a transient self-resolving alert, a performance observation within budget. Each NOTE gets an owner and a revisit trigger in [Decisions and closure](#decisions-and-closure).
  - QA does not run mutation testing or other time-consuming checks: it re-runs owned V rows, earlier reproducers and targeted adversarial scenarios, and states what it skipped.
- **Review budget.** One QA round per CP. Two failed QA rounds on the same CP → decision by Marco (no third repair without it, no waiver). Timeboxes: CP1 ½ day, CP2 1 day. Full suite `reconcile/.venv/bin/python -m pytest -q reconcile/tests` and `reconcile/.venv/bin/ruff check reconcile` at every candidate.
- **Execution.** Attended; unattended recovery UNVERIFIED and not promised. Parallel cap: 1 implementer or 1 QA at a time. Runtime: Orca 1.4.250. Orca repo `316cdd8d-3d6f-4dd5-9b4b-34ac5792bf2a`; coordinator tab `ORCA_WORKSPACE_ID` = `316cdd8d-3d6f-4dd5-9b4b-34ac5792bf2a::/Users/marco/orca/workspaces/ssv-network-monitor/check-switches` (measured 2026-11-06; F6). Every start passes `--repo id:316cdd8d-3d6f-4dd5-9b4b-34ac5792bf2a --worktree new-child --base-branch <frozen head>`; a start counts only when its receipt shows a worktree under `/Users/marco/orca/workspaces/ssv-network-monitor/` whose `HEAD` equals the frozen head. One candidate commit per CP on branch `cs/cpN` (repairs `cs/cpN-rK`); the coordinator integrates with `git merge --no-ff` into `plan/check-switches` after acceptance and re-runs suite + ruff on the merge.
- **Status vocabulary.** NOT STARTED → IN PROGRESS → AWAITING QA → ACCEPTED; REPAIR, BLOCKED, PROVISIONAL, ACCEPTED WITH DEFERRALS. Only the coordinator sets them. QA verdicts: PASS, PASS WITH NOTES, FAIL, BLOCKED. Results: PASS, FAIL, KNOWN RED, NOT RUN, NOT REQUIRED.
- **Invariants:**
  1. Completion and integration do not establish acceptance.
  2. Preserve measured PASS, FAIL, KNOWN RED, NOT RUN and NOT REQUIRED results; a disposition never changes a result.
  3. Never change an oracle merely to match output; justify any correction from independent evidence and rerun affected checks.
  4. QA re-measures, repairs no implementation, and cannot independently clear a finding on a repair it authored.
  5. Only the coordinator edits shared plan/status/log; QA writes its own report, immutable once issued, with corrections in a new report.
  6. Assign one writer per shared path and serialize integration.
  7. Bind evidence to the candidate, inputs, tools, environment and policy; justify reuse when any relevant identity changes.
  8. Name a wrong outcome each check must reject; missing evidence or ineffective required controls block the affected acceptance claim.
  9. Test and freeze shared interface behavior before dependent work fans out; coordinate changes and verify composition.
  10. Respect agreed scope and authority, record policy changes and deferrals explicitly, and reopen affected acceptance on material contrary evidence.

## Decisions (D rows)

D1–D5 are Marco's answers to the handoff's open questions (2026-11-06). D6–D13 freeze the design the CPs implement.

| ID | Decision | Source | Date |
|---|---|---|---|
| D1 | Models: coordinator Claude Opus 5.5 **medium** (a new execution session); every CP implementer/repair Claude Opus 5.5 high (Orca `claude`/`opus`/`high`); every QA Codex GPT Sol 6.1 high (Orca `codex`/`gpt-6.1-sol`/`high`) in a fresh child worktree; no evidence runner. | Marco | 2026-11-06 |
| D2 | QA policy carried over: D15 BLOCKING list with the plan-specific inclusions in Global rules › Policy, no mutation/slow checks, two failed rounds → decision. | Marco | 2026-11-06 |
| D3 | **No disable / enable notice.** Disabling a check sends no message (its incidents close silently, D7) and a re-enabled or new check sends no message when it starts alerting; the only messages are the check's own findings after its catch-up (D4, D9). The state is visible in `status`, `export` and the digest (D12). Rejected alternatives: a `check-disabled` / `check-enabled` health message. Consequence for tests: no `health` row of those kinds may exist (V1.1, V1.2, V2.1). | Marco | 2026-11-06 |
| D4 | **Re-enable semantics.** Exactly the D17 rule, per check: everything from the check's last committed run to the catch-up block is evaluated once, silently (incidents opened, escalated and resolved with `notify=False`); at the check's first run at a later block (forced) a condition still reported is sent once as `open`; a condition that appeared and healed while disabled, or an event of the disabled period, is never sent (visible in `status`/`export` as an incident with `alerts = 0`). Nothing is reported about the disabled period (D3). | Marco | 2026-11-06 |
| D5 | **Order with M17.** This plan runs first and independently of M17; the M17 plan is rebased on `plan/check-switches` after closure. M17's go-live silence then comes from A3 (a new check on a live database catches up silently from its default cursor `start_block − 1`); M17's F12 and its own go-live baseline rows become obsolete, its D13 "first live run block" is this plan's catch-up block. | Marco | 2026-11-06 |
| D6 | **Disabled = explicit.** A check is disabled only by `checks.<id>.enabled: false` in the config read at start; a registered check absent from `checks` is enabled (unchanged); a check that is merely not built (tests' `only=`, stub instances) is **not** disabled and nothing happens to its incidents. `registry` exposes the disabled ids (registered ids with an explicit `false`); unknown ids remain a startup error. | Coordinator | 2026-11-06 |
| D7 | **Closure.** In `Worker.bootstrap()` — after the identity check and the checkpoint/phase initialisation (`eb_reconcile.py:195-210`), before the topics backfill; never in `__init__`, which the read-only `status` worker also runs — one transaction per start: for every disabled check, every `incidents` row with `state='open'` becomes `state='disabled'` (a third state beside `open`/`resolved`), `resolved_block` = the checkpoint block (`start_block − 1` on a brand-new database), `updated_at` = now, `evidence` extended with `{"disabled": {"block": B, "at": t}}`; no message of any kind (D3); in the same transaction a durable per-check marker `disabled:<id>` = `{"block": B, "at": t, "closed": N}` (N counts every closed row, silent ones included) records the disabled state (used by D9 to recognise a re-enable). The same `bootstrap()` **clears the marker of every built check** (the enabled → disabled → enabled round trip is complete at the re-enabling start even when no catch-up applies; D9 refines what else happens then). Idempotent: a second start with the same config closes nothing and leaves the marker unchanged. Undelivered outbox rows of the disabled check's incidents (queued before the closure) are still delivered (`deliver()` is state-agnostic): by design, a state-table cell and a runbook sentence. Readers of `incidents` treat `disabled` rows as neither open nor resolved: `open_incident()` matches `open` only, so a key re-found after re-enable opens a **new** incident with a new id; the checks' own readers (`m1_stale_tuple.py:380-388`, `m2_operator_capacity.py:123-179`, `m3_liquidation_zone.py:310`, `m4_conservation_deficit.py:231`, `m6_legacy_coverage.py:109`), the framework's (`alerts.py:94,169,176,210,240`, `eb_reconcile.py:594,757,856,873`, `metrics.py:47`, `m5_eb_desync.py:310` id only) and the evidence script `scripts/fork_e2e.py:160` (any state, newest first: by design, evidence only) are unchanged in code and listed in the CP1 state table with their behaviour for `disabled` rows. No `db.py` schema change is expected (`state` is TEXT; the partial unique index covers `open` only); if one is needed, it is additive with a `SCHEMA_VERSION` bump and a coordinator decision first. | Coordinator; review S1, S4, N1, N3 | 2026-11-06 |
| D8 | **Re-alerts.** `Alerts.realert()` considers only incidents of the checks built in this worker (defence in depth beside D7): an open incident of a check that is not built is never re-alerted. Health messages are unaffected. | Coordinator | 2026-11-06 |
| D9 | **Per-check silent catch-up (D17 generalised).** Applies when `backfill.enabled` is true (default; the harness default `false` keeps every existing test's first run alerting; the production config has it true — the runbook states the dependency and the delivery smoke records the value) and the database phase is `live`. At `bootstrap()`, for every built check that (a) has no completed run on this database (`meta run:<id>` absent or without `block`: `_run_failed` writes no `block`) or (b) carries the D7 disabled marker, a durable catch-up record is written **before any run attempt** (so a failed first run cannot flip the decision); the D7 marker is cleared at that bootstrap in every case (record written; phase `backfill`, where the global path handles the check; `backfill.enabled` false, where no record is written). A stale `phase.first_live` entry of the check (disabled after go-live, before its first live run) is removed when the record is written, so the catch-up run never carries `live`. The check's first run is then pinned to the catch-up block = the checkpoint of the first `run_checks` step in which it runs (recorded in the record, like `_history_block`), a forced full run with reasons ⊇ {`backfill`, `force`}, its events from its cursor to that block, its findings applied with `notify=False` (resolutions kept); a failed silent run keeps the record and the pinned block and is retried every step (check-error / RPC accounting as today). Its first run at a block after the catch-up block is forced (reasons ⊇ {`live`, `force`}) and sends what it still reports; when it commits, the record is deleted (no message of its own, D3). Other checks are unaffected in the same step (one check's catch-up or its failure never delays another's live alert). While the phase is `backfill`, nothing changes: a new or re-enabled check is handled by the global backfill (`phase.evaluated`/`first_live`) exactly as at base; a check disabled during the backfill is never `evaluated` and `_go_live` proceeds without it (built set); re-enabled after go-live it falls under (b). The record is visible in `status.checks.<id>.catchup` (`history_block`, `state: pending|first_live`). | Coordinator (handoff "generalisation point"); review S1, S5, S6 | 2026-11-06 |
| D10 | **Upgrade rule (D9 (a) on an existing live database).** The first start of this version on a live database gives no catch-up to any built check that completed a run before (its `run:<id>` has `block`): no silent run, no forced run, no message. A check that never completed a run gets one (harmless and correct; in production nearly impossible, since every `m*` has an `Every` schedule — the case is a new check, i.e. M17). | Coordinator; review N4 | 2026-11-06 |
| D11 | **Reorg while disabled.** `check_reorg()` rewinds the event cursor and writes the `reorg:<id>` marker (lowest ancestor) for the built checks as today **plus every id that has an existing `cursor:events:<id>` or `reorg:<id>` meta row** (iterate the `meta` keys, not `REGISTRY`: unregistered built stubs keep working, and no row is created for an id without state); a disabled check's marker stays pending while it is disabled and is not reported by `status.reorg_pending`/`degraded()` (which iterate built checks, unchanged). On re-enable, `Worker.__init__` replays the marker as today (`on_reorg(ancestor)`, pending reason `reorg`), so the catch-up run carries `ctx.reorg_ancestor` and the check corrects its persisted state per `checks/README.md` §6.2; events of the new chain after the ancestor are seen, orphaned ones are not. | Coordinator (handoff risk list); review S2 | 2026-11-06 |
| D12 | **Visibility.** `status` (worker and the read-only CLI) gains `disabled: [ids]` (sorted, from config) and `checks.<id>.catchup` while a record exists; `open_incidents` and `open` naturally exclude `disabled` rows; `export` prints them with `state: disabled`. The daily digest gets the line `Disabled checks: m3, m7` after "Open incidents: …" (omitted when none). Metrics unchanged: a disabled check is not in the metrics server's `check_ids`, so no stale `last_run` series (documented in the runbook). | Coordinator | 2026-11-06 |
| D13 | **Docs.** `deploy/README.md` runbook section "Disable / re-enable a monitor" (steps, what to expect, the Monitor-side note: M8/M10/M11/M13 have rules on both sides and M15 is Monitor-only, so disabling a worker check does not silence the Monitor's rule; the sub-switches of M9/M11; metrics note) and "Adding a check to a live database" (M17 go-live); `checks/README.md` §3 (two schedule rows for the per-check catch-up), §4 (state `disabled`; no notice, D3), §7 (`enabled` semantics); `reconcile/README.md` (`status` fields, digest line, disable summary); `config.example.json` `_comment` one sentence. | Coordinator | 2026-11-06 |
| D14 | **`enabled` must be a boolean** (clarifies D6 after CP1-QA1 QF1). When present, `checks.<id>.enabled` must be a JSON boolean; any other value (`0`, `null`, `""`, a string, …) is a configuration error raised when the `Worker` is constructed (the `run`/`once`/`verify-layout` start and the read-only `status`/`export` CLI alike), like an unknown check id, so a malformed value never closes incidents and never silently skips a check. `registry.build` stays byte-unchanged; the check lives beside `disabled()`. Shipped configs use booleans only (`config.example.json`, `deploy/config/networks/evm_ethereum_mainnet.json`). Rejected: treating only literal `false` as disabled while `build` still skips other falsy values (a skipped-but-not-disabled check would keep open, silent incidents). | Coordinator (within D6's intent; reported to Marco) | 2026-11-06 |
| D15 | **Durable historical silence** (after CP2-QA1 QF1–QF3; Marco chose to fix the global path too). (a) An event, or a condition that appeared and healed, at or below a check's silent boundary — its per-check catch-up block, or the global `phase.history_block` for a check evaluated by the global backfill — is never sent, also after the catch-up record is deleted or `first_live` is consumed, and after any reorg the worker handles (any depth within the ancestor search): a reorg re-find of a muted incident does not unmute it. A condition still true at the check's first live run is sent once there (unchanged); a condition first seen above the boundary is live. The boundary is durable per check (a new meta key is allowed; the `phase` meta shape stays unchanged). (b) Global NF2: a reorg below `phase.history_block` after a check's global silent run and before its first live run repeats that silent run (as the per-check path does), so replayed history is not sent. (c) NF3 (amends D9's "phase `backfill` untouched"): a check re-enabled while the phase is `backfill` that is already in `phase.evaluated` is removed from `evaluated` and `first_live` (list contents only), so the global path re-evaluates it silently and its disabled-interval events are never sent. `test_backfill.py` stays byte-identical and green; new coverage in new tests. | Marco (scope: fix both paths, 2026-11-06); coordinator (wording) | 2026-11-06 |
| D16 | **A proposed incident id held by a `disabled` row** (after CP2-QA2 QF4; Marco chose the framework fix). In `Alerts._apply_one`, when a check proposes `incident_id` for a new incident and that id is held **only** by `disabled` rows, the incident opens under the first free deterministic suffix `<id>-<n>` (n ≥ 2, the scheme the framework already uses for its own ids) instead of raising; a collision with an `open` or `resolved` row still raises (unchanged). No `checks/m*.py` change. Consequence (accepted, same kind as QN2): M5's own `m5_incidents` id and the framework id differ after a re-enable; messages use the framework id. | Marco (route), coordinator (wording) | 2026-11-06 |

## Measured facts (F rows)

| ID | Fact | Status | How | Date |
|---|---|---|---|---|
| F1 | Base `9e94a1a` suite: **2,313 passed** in 60 s, 0 failed/skipped, exit 0; `ruff check reconcile` clean. `reconcile/tests/test_backfill.py` sha256 `28da7d49e950a0bdc51ee7d4f6993ad757fa72d60524c39a7c28efcc24702d35` (the A4 oracle). Tree clean after the run (`.venv` and `plans/` ignored). | MEASURED | `pytest -q reconcile/tests`, `ruff check reconcile` in a fresh `reconcile/.venv` (uv 0.13.4, Python 3.12.15); `evidence/pre-plan/suite-base.txt`, `ruff-base.txt` | 2026-11-06 |
| F2 | Switch: `checks/registry.py:39-55` — `build()` skips `enabled: false` (also under `only=`), `resolve_config` pops `enabled` (`:25-36`). A check absent from `checks` is enabled. The Worker never learns which ids were skipped. | MEASURED | read | 2026-11-06 |
| F3 | Re-alerts iterate **all** open incidents with `notify=1` regardless of their check (`alerts.py:205-223`); digest/status counts `state='open'` (`alerts.py:239-241`, `eb_reconcile.py:756-757`, `metrics.py:47`). Incident states today: `open`, `resolved`; `open_incident()` matches `open` only (`alerts.py:92-95`); the partial unique index `incidents_open` covers `open` only (`db.py:39`). | MEASURED | read | 2026-11-06 |
| F4 | D17 mechanism is global: `phase` meta `{"phase", "evaluated", "first_live", "history_block", "caught_up_block"}` (`eb_reconcile.py:208-274`); `due()` forces the silent run / first live run from those lists (`:488-496`); `run_check` silences with `notify=False` when `phase == backfill and id not in evaluated` (`:606-611`) and records `evaluated`/`first_live` (`:622-626`); `run_checks` pins the silent run to `min(history, block)` and skips live runs at `block <= history` (`:556-562`). A new check on a live database gets no silent pass (M17 F12). | MEASURED | read | 2026-11-06 |
| F5 | Reorg rewind covers only built checks' cursors and markers (`for cid in self.checks`, `eb_reconcile.py:319-323`); the marker is replayed at `__init__` for built checks (`:186-190`). Event cursor default `start_block − 1` (`:276-277`); logs are kept in `logs`, so a check can catch up from them (`db.events_after`). | MEASURED | read | 2026-11-06 |
| F6 | This tab: `ORCA_WORKSPACE_ID` = `316cdd8d-3d6f-4dd5-9b4b-34ac5792bf2a::/Users/marco/orca/workspaces/ssv-network-monitor/check-switches`; Orca CLI 1.4.250 at `/Applications/Orca.app/Contents/Resources/bin/orca`; worktrees: main checkout `9e94a1a [plan/monitoring-service]`, `check-switches` `9e94a1a [plan/check-switches]`, `m17-imp-credit` `9e94a1a [plan/m17-imp-credit]`. | MEASURED | `echo`, `orca --version`, `git worktree list` | 2026-11-06 |
| F7 | `_run_failed` writes `run:<id>` with `errors`/`last_error` only (`eb_reconcile.py:638-642`); a successful run writes `at`, `block`, `reasons`, … (`:627-631`). The read-only `status` CLI constructs `Worker(cfg, None, db)` without `bootstrap()` (`:869-876`); `__init__` runs the schema statements only when the DB is writable (`:158-162`). | MEASURED | read | 2026-11-06 |
| F8 | Sub-switch precedent (D14): `m9.dissent_enabled`, `m11.revert_rate_enabled`, `m11.zero_round_proof_enabled` — a rule off by config is said once per run by a health line and its open episodes are resolved once (`m9_oracle_common.py:329-340`, `m11_updater_liveness.py:232-234,318-321`). | MEASURED | read | 2026-11-06 |
| F9 | Harness: `make_worker` sets `backfill: {"enabled": False}`, `start_block = chain.sealed`, confirmations 0 (`tests/harness.py:41-45`); `only=[…]` builds only named registered checks and still honours `enabled: false` (F2); a stub can be registered per test (`test_framework.py::registered_stub`). `test_backfill.py` reads `phase()["evaluated"]`, `["first_live"]`, `["history_block"]`, `["caught_up_block"]` (lines 100-132, 178, 199, 427-429, 547, 588-596, 615). Markers `m1`–`m16` (`tests/conftest.py`). | MEASURED | read | 2026-11-06 |

## Acceptance (A rows)

| ID | Observable outcome | Source | Blocks |
|---|---|---|---|
| A1 | **Clean disable.** With `checks.<id>.enabled: false` and open incidents of that check, the next start closes every one of them as `disabled` (no `resolved` message), none is ever re-alerted, they leave `status.open_incidents`/`open` and the digest's open counts, the check is listed as disabled in `status` and the digest, no message is sent for the disable (D3), and every other check behaves exactly as before. | Handoff table rows 1 and 4; D3, D6–D8, D12 | CP2 (re-enable builds on it), delivery |
| A2 | **Silent re-enable.** A re-enabled check evaluates everything since its last committed run silently (events of the disabled period, state at the catch-up block), then alerts from its first run at a later block: a condition still true then is sent exactly once; a condition that appeared and healed while disabled, and every event of the disabled period, is never sent; a key of an incident closed as `disabled` gets a new incident; correct across a restart between disable and closure, mid catch-up, between the silent run and the first live run, and across a reorg while disabled. | Handoff table row 2; D4, D9, D11 | delivery, M17 |
| A3 | **New check on a live database.** A registered check with no completed run on a live database (backfill enabled) gets the same silent catch-up from its default cursor (`start_block − 1`: every stored event) and alerts from its next run; on an existing live database upgraded to this version, checks that completed a run before get no catch-up, no forced run and no message. | Handoff table row 3; D9, D10 | delivery, M17 go-live |
| A4 | **No regression.** The global D17 backfill is unchanged: `reconcile/tests/test_backfill.py` byte-identical to base and green; one check's catch-up (or its failure) never silences or delays another check's live alert; full suite and ruff green at every candidate; no `checks/m*.py` or `test_m*.py` edited. | Handoff risk list; D9 | every CP |
| A5 | **Docs.** D13 complete; every runbook claim is backed by a test or a V row. | Handoff "Docs"; D13 | delivery |
| A6 | **Composed independent QA** on the CP2 head (base `9e94a1a` → CP2): PASS or PASS WITH NOTES, every note owned; the delivery smoke (Closure) recorded. | Kit | delivery |

## Checkpoint index

| CP | Outcome | Depends on |
|---|---|---|
| [CP1](#cp1) | Clean disable: closure as `disabled`, no re-alerts, reorg rewind for non-built checks, status/digest listing (A1, A4) | none |
| [CP2](#cp2) | Silent catch-up for re-enabled and new checks, upgrade rule, docs/runbook, composed QA (A2, A3, A4, A5, A6) | CP1 ACCEPTED |

Pitfall catalog: `common-pitfalls.md` sha256 `3154922ff8c9091cc9a3be73031627de0dc1b18c366e5886538c53239cde0ff5` (kit rev 2026-10-29), selected per CP. PF-07, PF-08, PF-09, PF-12 are coordinator obligations (policy fixed here; state ≤ 100 lines; a receipt per start; closure manifest).

Common to both CPs: the handoff includes the DEFECT-CLASSES **state table** for every meta key / incident state it adds or changes (empty, error, partial, reorg depth 1 and N, restart between detection and handling, same-block order, time anchoring — a cell without a test is a defect), the full-suite count (base + new), the `ruff` result, `git diff --stat <base>..HEAD` including untracked files, and the list of new tests shown **red on the frozen base** (DEFECT-CLASSES 12). QA attacks DEFECT-CLASSES 1, 2, 10 and 12 first.

<a id="cp1"></a>
## CP1 — Clean disable

Status: ACCEPTED WITH DEFERRALS
Goal: a check with `enabled: false` leaves no open incident, no re-alert and no stale cursor behind, and is visible as disabled.
Required inputs: Global rules; D3, D6–D8, D11, D12 (D3: no notice); F2, F3, F5, F7, F8; `reconcile/checks/registry.py`, `reconcile/alerts.py`, `reconcile/eb_reconcile.py` (`bootstrap` `:194-217`, `check_reorg` `:289-342`, `status` `:738-760`, CLI `:834-876`), `reconcile/db.py:33-40`, `reconcile/checks/README.md` §4, `reconcile/tests/harness.py`, `reconcile/tests/test_framework.py` (`Stub`, `registered_stub`).
Allowed writes: `reconcile/checks/registry.py` (disabled-ids helper; `build` unchanged), `reconcile/eb_reconcile.py` (closure in `bootstrap`, D11 rewind, `status`), `reconcile/alerts.py` (state `disabled`, D8 filter, digest line), `reconcile/tests/test_switches.py` (new), `reconcile/tests/harness.py` (additive keyword only, if needed), `reconcile/checks/README.md` §4 (state `disabled`), `reconcile/README.md` (`status` fields), `plans/check-switches/workers/cp1/**`. Not: `db.py` (D7: coordinator decision first), `checks/m*.py`, `tests/test_m*.py`, `tests/test_backfill.py`, `metrics.py`.
Constraints: D6 exactly (never derive "disabled" from "not built"); closure and marker in one transaction in `bootstrap()` (F7); no outbox row at all for a disabled closure (D3); `test_backfill.py` and every `test_m*.py` unchanged and green.
Pitfalls: PF-02 (closure is persisted state; restart between closure and its commit), PF-04 (regressions red on base), PF-05 (shared framework files: one writer, full suite), PF-10 (bind to base).
Candidate: frozen `plan/check-switches` head (the plan commit) → `cs/cp1`.

### Verification

#### V1.1 → A1 · PF-02, PF-04
Action: harness scenario with a registered stub (or `Stub` instances under a registered id) that opened, in live mode, one sent `page`, one sent `warn`, one sent `info` and one silent (`notify=False`) incident; restart the worker on the same database with `checks.<id>.enabled: false` (and `backfill.enabled` both false and true); then `clock.advance` past 6 h and 24 h with steps. For the digest, pass `alerts={"mode": "record", "label": "test", "digest_interval_s": 1}` (the harness default `0` disables it and `make_worker` replaces the whole `alerts` dict) and advance the clock; the digest runs only when not backfilling.
Expected: all four rows `state='disabled'`, `resolved_block` = the checkpoint at closure, evidence carries `disabled`; the marker says `closed: 4`; no outbox row created by the closure (no `resolved`, no `health` of any `check-*` kind) and no `realert` for them, ever; an outbox row of theirs still undelivered at closure is delivered (D7, by design); `run:<id>` not updated (the check did not run); `status()` has `disabled == [<id>]`, `open_incidents` and `open` without the check; the digest says "Open incidents: none" and lists "Disabled checks: <id>".
Must reject: an incident left `open` (the re-alert at 6 h): the test is **red on `9e94a1a`**; a `resolved` message; closure of a check that is merely not built (control: the same database restarted with `only=` excluding the check and no `enabled: false` → rows stay `open`).
Evidence: test names and results; the red-on-base list.

#### V1.2 → A1 · PF-02 (D3: no notice; marker lifecycle)
Action: the V1.1 restart, then a second restart with the same config, then a restart with the check enabled again and, later, disabled again (with new open incidents); also a check disabled with zero open incidents. Count outbox rows created by each start.
Expected: zero outbox rows created by any of these starts (D3); each disable transition closes exactly the rows open at that start and writes `disabled:<id>` with the right `closed` count (`0` for the empty case); the second restart while disabled changes nothing; the enabling restart clears the marker (D7).
Must reject: any message caused by a disable or enable; a marker left after the enabling restart (with `backfill.enabled` false too); rows closed twice.
Evidence: test names and results.

#### V1.3 → A1, A4 · PF-05
Action: two registered checks A (disabled) and B (enabled) with open sent incidents each; steps past the re-alert cadence; a B finding resolving its key; `registry.build` order; `test_config_example_loads_and_builds_every_check` plus a copied `config.example.json` with one `enabled: false` loaded through `load_config` + `Worker` (`status()` lists it).
Expected: B re-alerts and resolves as at base, A does neither; built order unchanged; the example config still builds every check and the copy lists the disabled one.
Must reject: B's re-alert dropped by the D8 filter (control: B stays in `self.checks`).
Evidence: test names and results.

#### V1.4 → A2 (D11) · PF-02
Action: with check X disabled (not built) and its `cursor:events:X` above block N, `chain.reorg(N)` then a step; inspect `meta cursor:events:X` and `meta reorg:X`; also an id with no `cursor:events:`/`reorg:` row at all and a built, unregistered stub Y (unchanged behaviour: `test_scheduler_reorg_resets_event_cursor`).
Expected: X's cursor rewound to `[N, END_OF_BLOCK]`, `reorg:X` = N (and the minimum across two reorgs); Y as at base; no `meta` row created for the id without state (assert on the `meta` table, not on `_cursor()`, which returns a default).
Must reject: X's cursor left above N (red on `9e94a1a`).
Evidence: test names and results.

#### V1.5 → A1 · PF-02
Action: restart idempotency and atomicity: (a) the closure transaction fails after the incident `UPDATE` and before the marker write (monkeypatch the marker write to raise once during `bootstrap`) → next start; (b) `main(["status", …])` on the database with the check disabled before any `run` start.
Expected: (a) nothing committed by the failed start; the next start closes the rows and writes the marker exactly once, no message; (b) `status` lists the check as disabled, closes nothing (read-only) and exits 0.
Must reject: rows closed without the marker, or the marker without the rows.
Evidence: test names and results.

#### V1.6 → A1 (state table)
Action: in the handoff, the DEFECT-CLASSES state table for `incidents.state = 'disabled'`, the disabled marker and the D11 rows, including every reader of `incidents` listed in D7 with its behaviour for `disabled` rows (expected: never re-opened, never enumerated as `open` or `resolved`, printed by `export`), each cell with its test or "by design: <reason>".
Expected: complete table; `export` prints `state: disabled`.
Must reject: a reader not listed; a cell without a test or reason.
Evidence: handoff section; QA re-derives the reader list with `grep -n "FROM incidents" reconcile` and compares.

#### V1.7 → A4 · PF-04
Action: full suite, ruff, `git diff --stat <base>..HEAD` incl. untracked; `sha256sum reconcile/tests/test_backfill.py` equals base (`28da7d49…`); run the new test file on `9e94a1a` in a scratch copy and record, per named regression test, the **failing assertion text** (the 6 h `realert` row for V1.1, the un-rewound cursor for V1.4). Regression tests put the behavioural assertion first and import new symbols lazily inside the tests that need them, so the file collects on the base revision.
Expected: base count + new tests all green; ruff clean; diff only in Allowed writes; V1.1 and V1.4 regressions red on base **for the behavioural reason** (not ImportError/KeyError).
Must reject: an edited `test_m*.py`, `test_backfill.py`, `checks/m*.py`, `db.py`.
Evidence: `workers/cp1/suite.txt`, `red-on-base.txt`.

Done when: V1.1–V1.7 PASS and Codex QA returns PASS/PASS WITH NOTES.
Handoff destination: `plans/check-switches/workers/cp1/handoff.md`
QA report destination: `plans/check-switches/qa/cp1-round-N.md`

### Implementer handoff

`workers/cp1/handoff.md` (candidate `7639cff`); repair 1 `workers/cp1/repair-1/handoff.md` (head `62ed4c2`).

### QA evidence

Round 1 `qa/cp1-round-1.md` (`c63de872…`): FAIL — QF1 BLOCKING (falsy `enabled` closes incidents), QF2 NOTE (reader table), QN1 NOTE (docs). Round 2 `qa/cp1-round-2.md` (`98ed5e09…`): FAIL — QF1/QF2/QN1 closed; QF3 NOTE (CLI migrates the DB before rejecting a malformed `enabled`). V1.1, V1.2, V1.4, V1.5, V1.7 PASS in both rounds; V1.3/V1.6 FAIL in round 2 on QF3 only.

### Coordinator decision

2026-11-06, Marco (option A after the second failed round): **ACCEPTED WITH DEFERRALS**. QF3 deferred to CP2 (DF1 below). Integration: `git merge --no-ff cs/cp1-r1` → `16432ad` on `plan/check-switches`; suite on the merge 2,395 passed, ruff clean, `test_backfill.py` `28da7d49…`. Results unchanged: QA verdicts stay FAIL (invariant 2).

<a id="cp2"></a>
## CP2 — Silent catch-up for re-enabled and new checks; docs; composed QA

Status: ACCEPTED WITH DEFERRALS
Goal: a re-enabled or newly added check on a live database is caught up silently and then alerts, the global D17 backfill is unchanged, and the runbook tells DevOps how to flip a monitor.
Required inputs: Global rules; D3, D4, D5, D9–D13; F4, F5, F7, F9; CP1 handoff and QA report; `reconcile/eb_reconcile.py` (`bootstrap`, `phase` helpers `:222-277`, `due` `:488-510`, `run_checks` `:543-580`, `run_check` `:582-636`, `status`), `reconcile/checks/README.md` §3, §4, §7, `reconcile/tests/test_backfill.py` (as the model for the scenarios and as the frozen oracle), `deploy/README.md` "Backfill phase (D17)", `reconcile/README.md`, `reconcile/config.example.json`.
Allowed writes: `reconcile/eb_reconcile.py` (catch-up record, pinned silent run, forced first live run, `status.checks.<id>.catchup`), `reconcile/tests/test_switches.py` (extend) and/or `reconcile/tests/test_catchup.py` (new), `reconcile/tests/harness.py` (additive keyword only), `deploy/README.md`, `reconcile/checks/README.md` §3/§4/§7, `reconcile/README.md`, `reconcile/config.example.json` (`_comment` only), `plans/check-switches/workers/cp2/**`. Not: `tests/test_backfill.py`, `tests/test_m*.py`, `checks/m*.py`, `checks/registry.py`, `db.py`, `metrics.py`.
Constraints: D9 exactly (record written before any run attempt; silent run pinned; forced live run; record deleted on commit of the first live run; `backfill.enabled` gate; phase `backfill` untouched); the phase meta shape and the global path's behaviour unchanged (`test_backfill.py` is the oracle); one check's catch-up never silences another's live alert in the same step (F4 CP3-QA1-F1 precedent).
Pitfalls: PF-01 (a failed silent run is not "evaluated"), PF-02 (restart at each boundary; reorg marker replay), PF-03 (appear-and-heal is muted by D4: assert it explicitly), PF-04 (red on the CP1 head), PF-05 (D17 regression; shared `eb_reconcile.py`), PF-10 (CP1 rows re-run on the composed head).
Candidate: frozen head after CP1 integration → `cs/cp2`.

### Verification

#### V2.1 → A2 · PF-02, PF-03, PF-04
Action: harness, `backfill.enabled: true` on a database already `live`, a registered check with a level rule and an event rule (as `test_backfill.py::Watch`): live with a sent `page`; restart disabled (CP1 closure); while disabled: events that would page, a level that turns on and off again (healed), a second level that turns on and stays on; restart enabled; step; then `chain.mine()` + step; then a live event + step.
Expected: the catch-up run has reasons ⊇ {`backfill`, `force`}, is pinned at the checkpoint of that step, opens the event incidents and the still-on level with `notify = 0` (the healed level: nothing, or opened and resolved silently), sends nothing; `status.checks.<id>.catchup` present (`pending` then `first_live`); the next step's run has reasons ⊇ {`live`, `force`} and sends exactly one `open` for the still-on level, under a **new** incident id (the `disabled` row untouched); the catch-up record is gone and no `health` row of any `check-*` kind exists (D3); the live event is sent once; no message ever for the disabled-period events or the healed level.
Must reject: any message for a disabled-period event (the burst: the test is **red on the CP1 head**); zero messages for the still-true level; the `disabled` incident re-opened; a second `open` on later steps.
Evidence: test names and results; red-on-CP1 list.

#### V2.2 → A3 · PF-01, PF-04 (D9 (a), D10)
Action: (a) a live database with history (events since `start_block`, built with another check), then a start that adds a new registered check: its first run; (b) a live database where the check already completed runs (`run:<id>` with `block`) and has an open sent incident, restarted on the candidate (upgrade): count forced runs and messages; (c) a database in phase `backfill` with a new check: the global path handles it (`phase.evaluated`), no per-check record; (d) `backfill.enabled: false` (harness default) with a new check and with a re-enabled check: first run alerts as at base (`test_scheduler_dispatches_registered_check` unchanged) and the D7 marker is cleared at the enabling start; (e) a check disabled after `_go_live` while still listed in `phase.first_live`, then re-enabled; (f) a check disabled during phase `backfill` (never `evaluated`; `_go_live` proceeds without it), re-enabled after go-live.
Expected: (a) silent catch-up from `start_block − 1` (every stored event seen, nothing sent), then live from the next block, no message about the catch-up itself; (b) no record, no silent or forced run, no message, the open incident re-alerts on its cadence only; (c) no record, D17 behaviour; (d) no record, marker gone; (e) the catch-up run carries no `live` reason (the stale `first_live` entry is removed), silent, then live once; (f) go-live happens without the check; its re-enable gives a silent catch-up, then live.
Must reject: (a) a history event sent (red on the CP1 head); (b) any check on an upgraded live database re-evaluated silently or re-sent; (e) a `live` reason leaking into the silent run (a message during the catch-up).
Evidence: test names and results.

#### V2.3 → A2 · PF-01, PF-02 (restart and failure boundaries)
Action: restarts at every boundary: between disable and closure (CP1), after the record is written and before the silent run commits (the silent run raises `RuntimeError` once, and separately a transport `RpcError` once, as `test_one_transient_rpc_error_in_a_silent_run_silences_the_next_live_event`), between the silent run and the first live run, and after the first live run; plus the same blocks stepped twice.
Expected: the record and its pinned block survive the restarts and the failures; the silent run is retried pinned to the same block and stays silent; `check-error` health for the `RuntimeError`; the first live run is forced once and sends once; the record exists only until that commit; no duplicate outbox key.
Must reject: a second silent run at a different block; a live alert sent twice; a record left after the first live run; a failed silent run recorded as evaluated.
Evidence: test names and results.

#### V2.4 → A2 · PF-02 (D11 composition)
Action: disable; while disabled, `chain.reorg()` below the check's old cursor and mine a new chain with different events (one condition still true on the new chain, one only on the orphaned branch); a step (CP1 rewind); re-enable.
Expected: at re-enable the catch-up run carries `ctx.reorg_ancestor` = the ancestor, sees only the new-chain events after it, `on_reorg` was called at init, the marker is cleared when the run commits, nothing sent; the first live run sends once the new-chain condition and never the orphaned one.
Must reject: an orphaned-branch event sent; `reorg:<id>` left pending after the catch-up commits.
Evidence: test names and results.

#### V2.5 → A4 · PF-05
Action: check A live (ran before) and check B re-enabled or new: a page-worthy live event for A in the same step as B's silent run; then B's silent run failing for several steps (as `Flaky`) while A's events keep coming; then B recovers.
Expected: A's event is sent in that step; A keeps alerting while B fails; B raises `check-error`, keeps its pinned block, goes live when it succeeds and sends what is still true, once.
Must reject: A's live alert held until B's catch-up commits (mirror of CP3-QA1-F1).
Evidence: test names and results.

#### V2.6 → A1, A2 · PF-02 (quick flips)
Action: disable → enable → disable across three restarts without steps in between; enable immediately after disable with no new block; `status` CLI during a catch-up.
Expected: one closure per disable transition (the second one closes what the enabled interval opened, although the checkpoint did not move), no message; the re-enable with no new block pins the catch-up at the current checkpoint and goes live at the next block; the CLI shows `catchup` and closes nothing.
Must reject: a duplicate closure; a message for a flip; a catch-up skipped because no block arrived.
Evidence: test names and results.

#### V2.7 → A4 · PF-05, PF-10
Action: `sha256sum reconcile/tests/test_backfill.py` equals base (`28da7d49…`); full suite, ruff; `git diff --stat <CP1 head>..HEAD` incl. untracked; new tests run on the CP1 head in a scratch copy, recording per named regression test the **failing assertion text** (the burst message for V2.1, the history event sent for V2.2 (a)); lazy imports of new symbols as in V1.7; re-run CP1's `test_switches.py` on this head.
Expected: `test_backfill.py` byte-identical and green; CP1 + base + new tests green; diff only in Allowed writes; V2.1 and V2.2 (a) red on the CP1 head for the behavioural reason.
Must reject: an edited `test_backfill.py`, `test_m*.py`, `checks/m*.py`; a phase-meta shape change.
Evidence: `workers/cp2/suite.txt`, `red-on-cp1.txt`.

#### V2.8 → A5
Action: docs per D13; QA reads the runbook section and maps every claim (what happens at disable, at re-enable, for a new check, the dependency on `backfill.enabled` true, undelivered rows still delivered, the Monitor-side note, the sub-switches, metrics, `status` fields) to a V row or a test.
Expected: every claim backed; the `config.example.json` comment names `enabled`; no secret in any tracked file. If the implementer's timebox runs out, the coordinator may accept the docs as a short follow-up commit on `cs/cp2` before QA starts (not a repair round).
Must reject: a runbook claim with no test ("silent" claimed where a test shows a message, or the reverse).
Evidence: `workers/cp2/docs-checklist.md`.

#### V2.9 → A6 (composed QA, Codex)
Action: Codex QA over `9e94a1a` → CP2 head: re-run V1.1–V1.7 and V2.1–V2.8, attack DEFECT-CLASSES 1, 2, 10 and 12 on the composed diff, verify scope against both CPs' Allowed writes.
Expected: PASS or PASS WITH NOTES.
Must reject: a PASS without re-measurement on the CP2 head.
Evidence: `qa/cp2-round-N.md`.

Done when: V2.1–V2.8 PASS and the composed Codex QA (V2.9) returns PASS/PASS WITH NOTES.
Handoff destination: `plans/check-switches/workers/cp2/handoff.md`
QA report destination: `plans/check-switches/qa/cp2-round-N.md`

### Implementer handoff

`workers/cp2/handoff.md` (candidate `718606c`); repair 1 `workers/cp2/repair-1/handoff.md` (`ff3841f`, D15); repair 2 `workers/cp2/repair-2/handoff.md` (`4205b00`, D16).

### QA evidence

Composed Codex QA (V2.9): round 1 `qa/cp2-round-1.md` (`6d23504a…`) FAIL — NF1/NF2 global/NF3 BLOCKING, DF1 closed; round 2 `qa/cp2-round-2.md` (`a026b88f…`) FAIL — round-1 findings closed, QF4 BLOCKING (M5 re-enable missed page), QN2 NOTE; round 3 `qa/cp2-round-3.md` (`14fb1e3a…`) FAIL with NOTE-only findings — QF4 closed, QN3/QN4 NOTE, QN2 open. Round 3: suite 2,519 passed, ruff clean, `test_backfill.py` `28da7d49…`; V2.4/V2.8 FAIL on QN3, every other V1/V2 row PASS.

### Coordinator decision

2026-11-06, Marco: third repair authorized after round 2 (D16); after round 3 (NOTE-only): **ACCEPTED WITH DEFERRALS** (DF2–DF4 below). QA verdicts stay FAIL as measured (invariant 2). Integration: see event 0023.

## Dispatch and recovery

- **Before the first start:** `orca skills get orca-cli` and `orca skills get orchestration` (1.4.250); check `echo $ORCA_WORKSPACE_ID` (F6); create the Run. IDs from previous runs are never reused.
- **Plan commit (first coordinator commit on `plan/check-switches`):** replace `.gitignore`'s `plans/` with `plans/*` and `!plans/check-switches/`, commit the run directory so `new-child` worktrees contain it (Marco accepted that the plan stays in history, 2026-11-06).
- **Per start:** the coordinator writes the packet (`workers/<cp>/packet-<role>-<n>.md`: Global rules, D rows, CP block, role-document path, DEFECT-CLASSES path; for QA also the handoff path and candidate head), commits it, and starts with `--spec-file`. Logged: Run/Task/Dispatch ids, worktree path, branch, `HEAD`, requested vs `launch.effective`.
- **Completion:** `worker_done` with the report path is the signal (not a live PTY or a sent message). The coordinator checks the candidate head, `git diff --stat` incl. untracked files, and scope; then starts QA in a fresh `new-child` of the candidate head. QA commits only its report on its own branch; the coordinator copies it into `qa/` with its sha256.
- **Integration:** after ACCEPTED, `git merge --no-ff cs/cpN` into `plan/check-switches`, suite + ruff on the merge, integration identity in the CP decision. Failed or uncertain launches are inspected before any retry.
- **Retention:** worker worktrees released after their report is preserved and the branch merged or recorded; QA worktrees after their report is copied. Owner until closure: coordinator.

## Decisions and closure

Limitations (owner Marco; revisit trigger: first production observation):
- L1 (D4): a condition that appears and heals while a check is disabled is never reported; the disabled period is a gap by choice, visible only as `alerts = 0` incidents in `export`.
- L2 (scope): the Monitor's rules are not switched by `checks.<id>.enabled`; the runbook names the dual-sided monitors.
- L3 (D12): no metric says a check is disabled; `status.disabled` and the digest do.

Findings, NOTEs and deferrals are added here by the coordinator with ID, evidence, owner, affected A rows, authority and trigger.

- **DF1 (QF3, CP1-QA2, NOTE)** — `main()` opens the writable `DB` (migration: `schema_version` upsert, or a new file with its schema) before `Worker` validates `enabled` (D14), so `run`/`once`/`verify-layout` with a malformed switch write schema bookkeeping before the config error; no incident/marker/outbox change. Evidence `qa/cp1-round-2.md` QF3, reproducer `qa/cp1-round-2/test_cp1_siblings.py`. Affected: A1 (D14 sibling paths), V1.3, V1.6. Authority: Marco, 2026-11-06 (option A). Owner: CP2 implementer — validate the switches before the writable `DB` is opened (or created) in `main()`; correct `reconcile/README.md`'s "before it touches the database" wording and the D14 state-table cell. Trigger: CP2 composed QA (V2.9) re-runs the QF3 reproducer; DF1 closes only on its PASS.
- **D14 clarification (QA-2 judgment)** — `export` and `audit` construct no `Worker` and open the database read-only; they do not validate `enabled` and need not (they never close or evaluate). D14's "`status`/`export` CLI alike" reads as "every command that constructs a `Worker`".
- **QN1 (CP2-QA1, NOTE)** — the composed diff includes `.gitignore` (`plans/` → `plans/*` + `!plans/check-switches/`, commit `00fe823`): coordinator plan provisioning, outside both CPs' Allowed writes by design. Disposition: accepted as a plan-provisioning exception; reverted at closure (`plans/` restored). Owner: coordinator; trigger: closure commit.
- **DF2 (QN3, CP2-QA3, NOTE)** — after a reorg deeper than `confirmations` that turns an already-sent live M5 confirmed page into latent through a D15 silent repeat, the info resolution of that page is never sent (the deferred-key filter in `run_check` suppresses the framework resolution while M5's own state moves on); predates D16 (also on `ff3841f`). Evidence `qa/cp2-round-3.md` QN3, reproducers `qa/cp2-round-3/test_qa3_boundaries.py`, `test_qa3_diagnostic.py`. Affected A2, A5 (V2.4, V2.8). Authority: Marco, 2026-11-06 (accept with deferrals). Owner: Marco / follow-up plan (M5 lifecycle with the D15 deferral; may need `checks/m5_eb_desync.py`). Trigger: first deep (> confirmations) reorg observed in production, or the next change to D15/M5.
- **DF3 (QN2, CP2-QA2/QA3, NOTE)** — M5's own `m5_incidents` id and the framework incident id can differ after a silent repeat and after a D16 re-enable (`<id>-2`); messages use the framework id; no message impact measured except DF2. Owner: Marco / the same follow-up as DF2. Trigger: any tool or runbook step that joins `m5_incidents` and `incidents` by id, or DF2's fix.
- **DF4 (QN4, CP2-QA3, NOTE)** — wording: `checks/README.md` §6.1 still says a proposed id "must still be unique" without the D16 exception; the M5 module docstring says the two rows share the id. Owner: the DF2 follow-up (the docstring is a check file). Trigger: DF2's fix or the next `checks/README.md` edit.

Delivery checks (after CP2 ACCEPTED, before closure; coordinator, Marco's go needed because it uses Docker and the public RPCs): a **record-mode upgrade smoke** on a copy of a live worker volume (never the original; `cs-smoke_*` names, own image tag `cs-smoke-reconcile:<head>`, plain `docker run`, ≤ 2 h): (1) record the copy's config `backfill.enabled` (must be true for D9 to apply; the production `config.example.json` has it true) and start the candidate on the copy → expected no catch-up record for any check, no message (A3 upgrade rule, D10); (2) restart with `m3` disabled → closure of its open incidents, no message, `status.disabled == ["m3"]`; (3) restart with `m3` enabled → `catchup` in `status`, no message, then at the next step at most the still-true M3 conditions sent once. Evidence under `evidence/smoke/`; source volume hashes before and after. Local evidence only (Docker on the Mac, public RPCs); the production Kubernetes deployment is DevOps' (DT-11 precedent).

Closure (after CP2 ACCEPTED and the smoke recorded): record the delivery head and integration identity, final composed results, open NOTEs with owners. Move `plans/check-switches/` to `/Users/marco/ssv/repos/ssv-research/checkpointed-plans/monitor-service/check-switches/` (manifest with hashes, destination readback), remove it from the branch, restore `plans/` in `.gitignore`, commit; Marco pushes and opens the PR (his authority). Update the memory notes `check-switches-plan-pending` and `m17-plan-pending` (M17 rebase, D5).

## Readiness

Result: **READY** after review 1's fixes (below) and Marco's answers (2026-11-06: D1–D5, plan commit in history accepted).
Review 1: fresh general-purpose Claude agent (no planning context), plan sha256 `51cfdcdab1329b9cd3e41103d78676cc5206211d4186d8e076b762f721b96999`, [reviews/readiness-1.md](reviews/readiness-1.md): READY AFTER FIXES. It verified every pinned hash and every F/D `file:line` reference, the D7 reader list against `grep`, that a write transaction exists in `bootstrap()` for D3, that the D9 gate is inert for existing tests and `test_backfill.py` can stay byte-identical, that D11 touches neither `reorg_pending` nor `degraded()` nor `realert`, the read-only `status` path, and that all seven handoff risks map to V rows. Disposition, applied in this revision:
- B1 → D3 dedup counters; superseded by Marco's D3 (no notices at all), so the finding no longer applies; V1.2/V2.6 now assert that no message is sent.
- S1 → D7: `bootstrap()` clears the marker of every built check; D9: cleared in every case; V2.2 (d) asserts it.
- S2 → D11: built checks plus every id with an existing `cursor:events:`/`reorg:` meta row (iterate `meta`, not `REGISTRY`); V1.4 asserts on the `meta` table.
- S3 → V1.7/V2.7: per-test failing assertion text, behavioural assertion first, lazy imports.
- S4 → D7: closure after the identity check and checkpoint/phase init, before the topics backfill; `resolved_block` on a new database.
- S5 → D9: stale `phase.first_live` entry removed when the record is written; a check disabled during the backfill; V2.2 (e), (f).
- S6 → D9 names the production dependency on `backfill.enabled`; the delivery smoke records it; V2.8 maps the runbook line. A separate switch was not added (scope).
- S7 → V1.1 names the `alerts` override for the digest.
- N1 → D7 lists `scripts/fork_e2e.py:160` (by design). N2 → D7 marker `closed` counts silent incidents; V1.1. N3 → D7 and V1.1 (undelivered rows delivered). N4 → D10 note. N5 → V2.8 docs follow-up commit allowed. N6 → below.
The fixes were checked against the review text by the coordinator; no second review was run (the changes are local to the named rows).
Answers (Marco, 2026-11-06): D1 coordinator Opus 5.5 medium, implementers and QA as proposed; D2 carried over; D3 no disable/enable notice at all (D3, D7, D9, V1.1, V1.2, V1.5, V2.1, V2.2, V2.6, smoke revised by the coordinator, no new review: the change removes behaviour); D4 as proposed; D5 this plan first and independently; plan commit may stay in history. Open decisions: none.
Execution authorization: Marco starts a new session (Opus 5.5 medium) to execute; that session's first steps are the plan commit and the Orca Run.
