# Packet — CP1 independent QA, round 2 (check-switches)

Run `run_3869f8caafaa` · Coordinator: Claude Opus 5.5 medium · Issued 2026-11-06
Role: checkpoint QA — read `plans/check-switches/agents/checkpoint-qa.md` (sha256 `ec154819…`) and `plans/check-switches/qa/DEFECT-CLASSES.md` (sha256 `7daa55f6…`) first. Both are in your worktree.

## Assignment

- Independently verify **CP1 — Clean disable** after **repair 1** (round 2; round 1 was FAIL). Repair nothing.
- Audited candidate: **`62ed4c243edf451915b7c652d571b8294a315285`** (branch `cs/cp1-r1`, one repair commit on the round-1 candidate `7639cffb4991ce6bd536cfc607b27ce1aa78032d`). CP1 base: **`61307ddf3f813cfbe38ef062490224862ccd2b91`** (code identical to `9e94a1a`). Your worktree was created at the candidate: verify `git rev-parse HEAD` equals it and the tree is clean first; otherwise BLOCKED.
- Inputs: round-1 report `git show plan/check-switches:plans/check-switches/qa/cp1-round-1.md` (sha256 `c63de872…`) and its evidence `git show plan/check-switches:plans/check-switches/qa/cp1-round-1/<file>` (incl. your `test_cp1_independent.py`); original handoff `plans/check-switches/workers/cp1/handoff.md`; repair handoff `plans/check-switches/workers/cp1/repair-1/handoff.md` (claims, not evidence).
- New decision **D14** (in the D rows below; added after round 1 by the coordinator): `checks.<id>.enabled`, when present, must be a JSON boolean; anything else is a configuration error at `Worker` construction, so nothing is closed. D14 supersedes round 1's "must yield open" expectation for `0`/`null`/`""`: the oracle is now "construction raises, database unchanged (no row closed, no marker, no outbox row)".
- Do:
  1. Re-run the round-1 reproducers (QF1 with D14's oracle; QF2's reader inventory re-derived independently: `FROM incidents`, `JOIN incidents`, `UPDATE`/`INSERT INTO incidents`, other accesses) and verify the repair handoff's complete reader and state tables.
  2. Re-measure V1.1–V1.7 on the candidate (full suite, ruff, `test_backfill.py` sha256 `28da7d49…`, diff `61307dd..62ed4c2` and `7639cff..62ed4c2` against CP1 Allowed writes, red-on-`7639cff` for the new QF1 tests in a scratch copy).
  3. Attack D14's sibling paths: every caller that constructs a `Worker` (`run`, `once`, `verify-layout`, `status`) raises before any database write; a non-object `checks.<id>` entry; an unknown id with a bad `enabled`; `True`/`False` controls unchanged; shipped configs (`reconcile/config.example.json`, `deploy/config/networks/*.json`) still load.
  4. Judge the repair's disclosed deviation: `export`/`audit` construct no `Worker`, so they do not validate `enabled` (D14's text lists "`status`/`export` CLI alike"). Say whether this is a defect (D2 severity) or harmless (they never close anything).
  5. Check the QN1 README sentence (`run`/`once`/`verify-layout` close; `status`/`export` do not).
- Policy D2: no mutation campaigns or slow checks; targeted falsifiers fine; state what you skipped. If the full suite needs local sockets outside the sandbox, request the escalation (an automatic reviewer answers it).
- Environment: `export PATH=$HOME/.foundry/bin:$PATH`; `uv venv reconcile/.venv --python 3.12` and `uv pip install --python reconcile/.venv/bin/python -r reconcile/requirements-dev.txt` (no other installs). Suite `reconcile/.venv/bin/python -m pytest -q reconcile/tests`; lint `reconcile/.venv/bin/ruff check reconcile`. Experiments only in scratch copies under `/private/tmp`, removed afterwards.
- Report: write only `plans/check-switches/qa/cp1-round-2.md` (first line exactly PASS, PASS WITH NOTES, FAIL or BLOCKED; one result per V1.1–V1.7; each round-1 finding QF1/QF2/QN1 marked closed or open with evidence; new findings with D2 severity) and optional evidence under `plans/check-switches/qa/cp1-round-2/`. Commit only those paths on a new branch `cs/cp1-qa2`, attribution line at the end of the message; never push. Do not edit plan/state/log, implementation, tests or config.
- Completion: `worker_done` with the report path, verdict and commit sha. Blocking question → `orca orchestration ask` and wait.


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

## Checkpoint index

| CP | Outcome | Depends on |
|---|---|---|
| [CP1](#cp1) | Clean disable: closure as `disabled`, no re-alerts, reorg rewind for non-built checks, status/digest listing (A1, A4) | none |
| [CP2](#cp2) | Silent catch-up for re-enabled and new checks, upgrade rule, docs/runbook, composed QA (A2, A3, A4, A5, A6) | CP1 ACCEPTED |

Pitfall catalog: `common-pitfalls.md` sha256 `3154922ff8c9091cc9a3be73031627de0dc1b18c366e5886538c53239cde0ff5` (kit rev 2026-10-29), selected per CP. PF-07, PF-08, PF-09, PF-12 are coordinator obligations (policy fixed here; state ≤ 100 lines; a receipt per start; closure manifest).

Common to both CPs: the handoff includes the DEFECT-CLASSES **state table** for every meta key / incident state it adds or changes (empty, error, partial, reorg depth 1 and N, restart between detection and handling, same-block order, time anchoring — a cell without a test is a defect), the full-suite count (base + new), the `ruff` result, `git diff --stat <base>..HEAD` including untracked files, and the list of new tests shown **red on the frozen base** (DEFECT-CLASSES 12). QA attacks DEFECT-CLASSES 1, 2, 10 and 12 first.

<a id="cp1"></a>
## CP1 — Clean disable

Status: REPAIR
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
