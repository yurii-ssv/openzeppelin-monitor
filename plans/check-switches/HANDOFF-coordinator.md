# Worker check switches — coordinator handoff (2026-11-06)

For the session that writes this checkpointed plan. Status: scope agreed with Marco, plan NOT
written, no Orca Run. Follow the kit and stop after presenting the plan.

## Outcome (agreed with Marco, 2026-11-06)

Make the existing per-check switch `checks.<id>.enabled` (worker `reconcile/`, checks `m1`–`m14`)
safe to flip in both directions, so a whole monitor can be muted and brought back without stale
or endless alerts. A config change takes effect on worker restart.

| Situation | Today (base `9e94a1a`) | Required |
|---|---|---|
| Disable a check with open incidents | Its open pages re-alert every 6 h (warns 24 h) and never resolve | Its open incidents are closed as disabled (not "resolved"), re-alerts stop, it leaves the digest |
| Re-enable a check | It processes every event since its old cursor as live → burst of stale alerts | Silent catch-up to the current checkpoint, then live; a condition still true at the first live run alerts once (same rule as the D17 silent backfill) |
| Add a new check to a live database | Same as re-enable, from `start_block` (24,920,727) | Same silent catch-up (this is what M17's go-live needs) |
| Visibility | Not shown | `status` and the digest list the disabled checks |

**Out of scope (Marco):** a switch per alert type inside a check, severity overrides, live config
reload without restart, the OpenZeppelin Monitor rules in `config/monitors/` (their own switches;
note M8/M10/M11/M13 have rules on both sides, M15 is Monitor-only).

## Relationship to M17

The M17 plan (worktree `/Users/marco/orca/workspaces/ssv-network-monitor/m17-imp-credit`,
`plans/m17-imp-credit/`, written, uncommitted, not started) is being simplified separately: start at
M17's go-live block, validator keys from the SSV API (`/validators?ownerAddress=`), Safe self-detection
by `tx.to == owner`, no `m17 mark`/status, summary-only digest → 3 CPs. Its go-live silence should rely
on this plan's silent catch-up. Expected order: this plan first, M17 rebased on it. Confirm with Marco.

## Placement and repo

- Orca workspace: `ORCA_WORKSPACE_ID` must be `316cdd8d-3d6f-4dd5-9b4b-34ac5792bf2a::/Users/marco/ssv/repos/ssv-network-monitor`; pass `--repo id:316cdd8d-3d6f-4dd5-9b4b-34ac5792bf2a` on every start. Orca CLI `/Applications/Orca.app/Contents/Resources/bin/orca` (1.4.246); load the bundled `orca-cli` / `orchestration` guides with `orca skills get …`.
- Execution worktree (Orca-managed, created): `/Users/marco/orca/workspaces/ssv-network-monitor/check-switches`, branch `plan/check-switches`, base `9e94a1a` (`plan/monitoring-service` head; PR #1 open on `yurii-ssv/openzeppelin-monitor`).
- Run directory `plans/check-switches/`. `plans/` is gitignored (`.gitignore:68`), so child worktrees would not see the plan: the first coordinator commit replaces `plans/` with `plans/*` + `!plans/check-switches/`. At closure move the run dir to `/Users/marco/ssv/repos/ssv-research/checkpointed-plans/monitor-service/check-switches/`, remove it from the branch, restore the ignore line; then a separate PR (Marco pushes).
- Rules carried over: never push, never send a mainnet tx, alerts only to `TEST_*` or record mode, workers never write `config/`, `config.zip`, `.env`, `.local-dry/`; `export PATH=$HOME/.foundry/bin:$PATH` in every packet; commits end with the session's Co-Authored-By line.

## Kit and precedent

- Kit (revision 2026-10-29, uncommitted in ssv-research): `/Users/marco/ssv/repos/ssv-research/checkpointed-workflows/` — README, invariants `28da5733…`, operating-protocol `4f6ad173…`, plan-template `390361c1…`, common-pitfalls `3154922f…`, runtime-orca `2248d5b0…`, agents/checkpoint-implementer `6548b79b…`, agents/checkpoint-qa `ec154819…`, agents/evidence-runner `e52a719b…` (re-hash before pinning). Copy the three role documents into `plans/check-switches/agents/`.
- Tier: likely **Lite or small Standard** (one bounded change in the framework; 1–2 CPs). Precedent for format and QA policy: the M17 plan in the m17 worktree, and `/Users/marco/ssv/repos/ssv-research/checkpointed-plans/monitor-service/monitoring-service/` (`qa/DEFECT-CLASSES.md`, D15).

## Code map (base `9e94a1a`, paths under `reconcile/`)

- Switch: `checks/registry.py:39-55` `build()` skips `enabled: false`; `resolve_config` pops `enabled` (`:25-36`). Sub-switches exist only for `m9.dissent_enabled`, `m11.revert_rate_enabled`, `m11.zero_round_proof_enabled` (leave as they are unless Marco says otherwise).
- No check reads another check's tables or state (only own tables, e.g. `m1_detections`), so disabling one check does not break another.
- Re-alerts: `alerts.py:205-220` iterates **all** open incidents with `notify=1`, regardless of whether their check is built. Open-incident counts for the digest/status: `alerts.py:240`. Resolve paths: `alerts.py:113`, `resolve_absent :171`, `resolve_not_refound :184`. Incident states today: `open`, `resolved`.
- Event cursor per check: `meta cursor:events:<id>`, default `start_block − 1` (`eb_reconcile.py:276-277`); advanced after each run (`:617`); rewound on reorg (`:320-321`). Logs are kept in the `logs` table, so a re-enabled check can catch up from them.
- D17 silent backfill: phase meta `{"phase": "backfill"|"live", "evaluated": [...], "first_live": [...]}` (`eb_reconcile.py:208-271`); `due()` forces a full silent run for checks not yet `evaluated` during backfill and a forced first live run (`:488-500`); `_run` applies findings with `notify=False` when silent, then records `evaluated`/`first_live` (`:596-625`). Today the silence is tied to the global backfill phase, not to the check — the generalisation point.
- `status` command (`eb_reconcile.py:25`, read-only DB); config whitelist of top-level keys (`:121-125`); config is read at start only (no reload).
- Tests: `tests/harness.py` (`make_worker(tmp_path, only=[...], checks=..., backfill=...)`, `advance`, `outbox`), `fakechain.py`; run `reconcile/.venv/bin/python -m pytest -q reconcile/tests` and `ruff check reconcile` (2,313 tests collected at base).
- Docs: `reconcile/README.md`, `checks/README.md` (framework contract; framework files were frozen for lanes in the first plan), `deploy/README.md` runbook (add "disable / re-enable a monitor").

## Risks to turn into V rows (from DEFECT-CLASSES / pitfalls)

- Re-enable with a condition still true → exactly one alert at the first live run; a condition that appeared and healed while disabled → no alert (it was muted by choice; confirm with Marco).
- Re-enable after a reorg deeper than the confirmations while disabled; restart between disable and the incident closure; restart mid catch-up.
- Disable → enable → disable quickly (no duplicate closure/opening); an incident key reused after re-enable gets a new incident, not the closed one.
- A page open at disable time: closing it must be visible (one "disabled" message? or silent? — ask Marco).
- Silent catch-up of a check must not silence other checks, and a global backfill still behaves as before (regression of D17 tests).
- First run of a brand-new check on a live DB (M17 case) is silent and then live.

## Open decisions (ask Marco in one batch)

1. Models per role (M17 plan used: coordinator Opus 5.5 high, implementers Opus 5.5 high via Orca, QA Codex gpt-6.1-sol high in a fresh worktree). Same here?
2. QA policy: carry over D15 BLOCKING, no mutation testing, decision after two failed rounds?
3. On disable: send one "monitor disabled, N incidents closed" message (route?), or close silently?
4. On re-enable: alert only conditions still true at the first live run (proposed), or also report what happened while disabled?
5. Order with M17: this plan first and M17 rebased on it (proposed)?
