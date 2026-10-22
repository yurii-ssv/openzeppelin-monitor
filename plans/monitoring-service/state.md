# Current state — monitoring-service

This is the sole current-state summary. History: [execution-log.md](execution-log.md). Contract: [plan.md](plan.md).

- Updated at: 2026-10-21T19:45Z (resumed, event-0055)
- Last recorded event / last processed Orca delivery: event-0055
- Repo/worktree/branch: `/Users/marco/ssv/repos/ssv-network-monitor` · `plan/monitoring-service` (created by CP0); QA worktree `/Users/marco/orca/workspaces/ssv-network-monitor/ms-qa-cp0` @ `9772b2d` (event-0010)
- Observed HEAD: integration branch carries the central test patch (ruling v2.1); see git log; uncommitted by design: `.dockerignore`, `Dockerfile.production`, `.env copy.example`
- Runtime authority: Orca repo `316cdd8d-3d6f-4dd5-9b4b-34ac5792bf2a`. Run `run_96be3e0c7302` bound to this coordinator tab (monitor workspace, verified); old Run `run_6a94d8a4633c` retired. Workers use the full orca path if `orca` fails (F13)
- Mode: attended; resumed by Marco (event-0055)
- Overall acceptance: pending
- Current checkpoint: CP1A repair; CP2a–e (wave 2)
- Coordinator: this Claude Code session, repo root, monitor-workspace tab

| Task | State | Active dispatch / receipt | Frozen head | Owner | Evidence / next action |
|---|---|---|---|---|---|
| CP0.0 Orca loop proof | partial, stopped by owner | `ctx_8a4b4362736e` unsettled, terminal retained, idle | `86a17db` | coordinator | leave it; Codex completion proven at first QA |
| CP0 Baseline and hygiene | VERIFIED — PASS WITH NOTES, integrated (`4fbb9f1`) | QA `ctx_4bd11bbdbf4e` settled | `9772b2d` | Codex gpt-6.1-sol high | done; notes owned by CP1A (schema checks), CP3 (flake), coordinator (V0.4 slash) |
| CP1A Config lane | VERIFIED — PASS WITH NOTES (QA 5, Opus; head `f5e1fc4`), integrated (`c209d40`) | settled+released | `f5e1fc4` | Claude Opus 5.5 high (QA) | notes N1-N9 to plan §8 at closure; F6/F7 deferred |
| CP2a Liquidation (M3, M4, M14) | VERIFIED — PASS WITH NOTES (QA 3, Opus; head `2b434cf`), integrated | settled+released | `2b434cf` | Claude Opus 5.5 high (QA) | notes to plan §8 at closure; M4 interpretation to amend spec |
| CP2b Oracle (M9, M10, M11) | QA FAILED attempt 4 (2 BLOCKING); repair 4 RUNNING (lane head `ffc3b27`; next QA capped) | task `task_2cf9324f8445`/`ctx_12b6ebc17f66`, opus/high | `ffc3b27` | Claude Opus high (repair) → Opus QA 5 capped | M11 revert-rate window; reorg+API down downgrade |
| CP2c Wallets (M12, M8 floor, M13 watch) | VERIFIED — PASS WITH NOTES (QA 4, Opus; head `dd9522b`), integrated | settled+released | `dd9522b` | Claude Opus 5.5 high (QA) | notes to plan §8 at closure |
| CP2d Cluster integrity (M1, M2) | VERIFIED — PASS WITH NOTES (QA 4, Opus; head `24b1599`), integrated | settled+released | `24b1599` | Claude Opus 5.5 high (QA) | notes to plan §8 at closure |
| CP2e Legacy SSV (M6, M7) | QA FAILED attempt 2 (1 BLOCKING); repair 2 RUNNING (lane head `8e07b0a`; next QA capped, D15) | task `task_c706fd249b26`/`ctx_3a5d60dc1b12`, opus/high | `8e07b0a` | Claude Opus high (repair) → Opus QA 3 capped | empty listing with total 0 must be UNKNOWN; premature integration merge still open |
| CP1B Worker framework | VERIFIED — PASS WITH NOTES (head `b07d2c6`, QA 4), integrated into `plan/monitoring-service` | QA-4 dispatch settled+released | `b07d2c6` | Codex gpt-6.1-sol high | done; CP2 packets must carry the reorg obligation; plan §6 amended |

## Next transition

- Exact next authorized action and owner: coordinator waits (`orca orchestration check --wait`) for CP2a repair 2, CP2d repair 2, CP2b repair 2, CP2c repair 2, CP2e QA 1; then fresh Opus QA per lane (capped where >=2 failed rounds). CP3 needs Marco's approval once CP1A and CP2a-e are VERIFIED. Open: premature CP2e merge (event-0055).
- Ready work with no active dispatch: none.
- Waiting for: running workers. Network-dependent rows run offline will be NOT RUN/stalled, never credited. Open owner items listed in event-0054.
- Next check/wake mechanism: attended; `orca orchestration check --wait` on the coordinator's Run (15 min) while workers run. Unattended recovery UNVERIFIED (DEF-4).
- Context resume: verify HEAD/status/runtime, read this file, inspect the next task's latest attempt, continue the first unproven action. Do not reload whole history.

## Decisions (mirrored in the log, event-0002)

- OG1 toolchain: installed by Marco (Foundry at `~/.foundry/bin`, not on PATH).
- AUTH-2: commits as `Marco Tabasco <marco@ssvlabs.io>` — granted; identity set.
- AUTH-3: Orca registration and child worktrees — granted; done.
- AUTH-4: all pages to the Telegram group — confirmed.
- QA: Codex GPT Sol 6.1 high for every QA verdict.
