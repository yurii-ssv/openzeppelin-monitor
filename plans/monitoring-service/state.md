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
| CP2a Liquidation (M3, M4, M14) | IMPLEMENTED (repair 2) — QA 3 RUNNING on Opus, capped (lane head `2b434cf`) | QA dispatch id in event-0064 start receipt | `2b434cf` | Claude Opus 5.5 high (QA) | wait worker_done |
| CP2b Oracle (M9, M10, M11) | QA FAILED attempt 3 (2 BLOCKING); repair 3 RUNNING (lane head `8bd225e`; next QA capped) | task `task_91e058ddc685`/`ctx_7ddd5de19cbc`, opus/high | `8bd225e` | Claude Opus high (repair) → Opus QA 4 capped | QA3-F1 M11 re-report, QA3-F2 M9 lone-voter heal |
| CP2c Wallets (M12, M8 floor, M13 watch) | VERIFICATION REVOKED (event-0062); repair 3 RUNNING on lane head `f779b45` | task `task_d8441a07b246`/`ctx_30d9cb9479da`, opus/high | `f779b45` | Claude Opus high (repair) → fresh Opus QA 4 capped | F1 M12 block-count windows, F2 partial feeHistory |
| CP2d Cluster integrity (M1, M2) | QA FAILED attempt 3 (1 BLOCKING); repair 3 RUNNING (lane head `ec58434`; next QA capped) | task `task_e0f7da3f20d9`/`ctx_21446fcd0af4`, opus/high | `ec58434` | Claude Opus high (repair) → Opus QA 4 capped | transient-report revision after reorg |
| CP2e Legacy SSV (M6, M7) | IMPLEMENTED — QA 1 RUNNING on Opus (lane head `c02557a`) — NOTE: lane already merged into integration prematurely (`e95d729`, local) | QA task `task_283fff5f1b19`/`ctx_ba0224588278`, opus/high | `c02557a` | Claude Opus 5.5 high (QA) | wait worker_done; Marco to decide on the premature merge |
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
