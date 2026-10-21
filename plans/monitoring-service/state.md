# Current state — monitoring-service

This is the sole current-state summary. History: [execution-log.md](execution-log.md). Contract: [plan.md](plan.md).

- Updated at: 2026-10-21T10:55Z
- Last recorded event / last processed Orca delivery: event-0011 / delivery_e0c9e0d09d02
- Repo/worktree/branch: `/Users/marco/ssv/repos/ssv-network-monitor` · `plan/monitoring-service` (created by CP0); QA worktree `/Users/marco/orca/workspaces/ssv-network-monitor/ms-qa-cp0` @ `9772b2d` (event-0010)
- Observed HEAD: `9772b2da2acd427aeaf6687c27656ee89bd895d8` (10 commits over `main@86a17db`); uncommitted by design: `.dockerignore`, `Dockerfile.production`, `.env copy.example`; plus coordinator edits to state.md / execution-log.md
- Runtime authority: Orca repo `316cdd8d-3d6f-4dd5-9b4b-34ac5792bf2a`. Run `run_96be3e0c7302` bound to this coordinator tab (monitor workspace, verified); old Run `run_6a94d8a4633c` retired. Workers use the full orca path if `orca` fails (F13)
- Mode: **CP0 VERIFIED (PASS WITH NOTES) — awaiting Marco's wave-transition approval; attended**
- Overall acceptance: pending
- Current checkpoint: CP0
- Coordinator: this Claude Code session, repo root, monitor-workspace tab

| Task | State | Active dispatch / receipt | Frozen head | Owner | Evidence / next action |
|---|---|---|---|---|---|
| CP0.0 Orca loop proof | partial, stopped by owner | `ctx_8a4b4362736e` unsettled, terminal retained, idle | `86a17db` | coordinator | leave it; Codex completion proven at first QA |
| CP0 Baseline and hygiene | VERIFIED — PASS WITH NOTES (head `9772b2d`; QA commit `3821104` on `mtabasco/ms-qa-cp0`, not yet imported) | QA dispatch `ctx_4bd11bbdbf4e` settled, released/retained | `9772b2d` | Codex gpt-6.1-sol high | Marco approves wave 1; import `3821104` |

## Next transition

- Exact next authorized action and owner: Marco approves the wave transition (CP1A Sonnet, CP1B Opus, parallel cap 2) and exports `MAINNET_RPC_URL`, `TEST_RPC_URL`; then coordinator imports QA commit `3821104` (after committing/merging its own uncommitted plan/state/log edits), freezes the new head, and dispatches both implementers.
- Ready work with no active dispatch: none.
- Waiting for: Marco (wave-1 approval; OG2 RPC variables — not exported in the coordinator shell).
- Next check/wake mechanism: attended; `orca orchestration check --wait` on the coordinator's Run (15 min) while workers run. Unattended recovery UNVERIFIED (DEF-4).
- Context resume: verify HEAD/status/runtime, read this file, inspect the next task's latest attempt, continue the first unproven action. Do not reload whole history.

## Decisions (mirrored in the log, event-0002)

- OG1 toolchain: installed by Marco (Foundry at `~/.foundry/bin`, not on PATH).
- AUTH-2: commits as `Marco Tabasco <marco@ssvlabs.io>` — granted; identity set.
- AUTH-3: Orca registration and child worktrees — granted; done.
- AUTH-4: all pages to the Telegram group — confirmed.
- QA: Codex GPT Sol 6.1 high for every QA verdict.
