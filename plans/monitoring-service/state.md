# Current state — monitoring-service

This is the sole current-state summary. History: [execution-log.md](execution-log.md). Contract: [plan.md](plan.md).

- Updated at: 2026-10-21T18:10Z
- Last recorded event / last processed Orca delivery: event-0039 / delivery_5db92534fc97 (acked)
- Repo/worktree/branch: `/Users/marco/ssv/repos/ssv-network-monitor` · `plan/monitoring-service` (created by CP0); QA worktree `/Users/marco/orca/workspaces/ssv-network-monitor/ms-qa-cp0` @ `9772b2d` (event-0010)
- Observed HEAD: integration branch carries the central test patch (ruling v2.1); see git log; uncommitted by design: `.dockerignore`, `Dockerfile.production`, `.env copy.example`
- Runtime authority: Orca repo `316cdd8d-3d6f-4dd5-9b4b-34ac5792bf2a`. Run `run_96be3e0c7302` bound to this coordinator tab (monitor workspace, verified); old Run `run_6a94d8a4633c` retired. Workers use the full orca path if `orca` fails (F13)
- Mode: **Wave 2: CP2a, CP2b, CP2c, CP2e implementing; CP2d QA running; CP1A QA 4 running — attended**
- Overall acceptance: pending
- Current checkpoint: CP1A repair; CP2a–e (wave 2)
- Coordinator: this Claude Code session, repo root, monitor-workspace tab

| Task | State | Active dispatch / receipt | Frozen head | Owner | Evidence / next action |
|---|---|---|---|---|---|
| CP0.0 Orca loop proof | partial, stopped by owner | `ctx_8a4b4362736e` unsettled, terminal retained, idle | `86a17db` | coordinator | leave it; Codex completion proven at first QA |
| CP0 Baseline and hygiene | VERIFIED — PASS WITH NOTES, integrated (`4fbb9f1`) | QA `ctx_4bd11bbdbf4e` settled | `9772b2d` | Codex gpt-6.1-sol high | done; notes owned by CP1A (schema checks), CP3 (flake), coordinator (V0.4 slash) |
| CP1A Config lane | IMPLEMENTED (repair 3) — QA ATTEMPT 4 RUNNING (lane head `45fe940`) | repair `ctx_b8fbc82c69b7` settled+released; QA dispatch in event-0036, codex gpt-6.1-sol high, Approve for me verified | `45fe940` | Codex gpt-6.1-sol high (QA) | wait worker_done; F6/F7 to CP3 |
| CP2a Liquidation (M3, M4, M14) | DISPATCHED (M4 interpretation A approved, event-0031; merging central test patch) | task `task_2d232c728871`/`ctx_c6e862b982c3`, opus/high | `96d1c32` | Claude Opus high | wait worker_done |
| CP2b Oracle (M9, M10, M11) | DISPATCHED | task `task_13f583847d79`/`ctx_e0d798da26ac`, opus/high effective | `96d1c32` | Claude Opus high | wait worker_done |
| CP2c Wallets (M12, M8 floor, M13 balance watch) | DISPATCHED | task `task_8384f0c7778e`/`ctx_113e2440cd8e`, opus/high effective | `96d1c32` | Claude Opus high | wait worker_done; V2c.8 added (D10) |
| CP2d Cluster integrity (M1, M2) | IMPLEMENTED — QA RUNNING (lane head `588888b`) | impl `ctx_f28f28892d56` settled+released; QA dispatch in event-0039, codex approve-for-me verified | `588888b` | Codex gpt-6.1-sol high (QA) | wait worker_done; DEF-3 fork PoC deferred |
| CP2e Legacy SSV (M6, M7) | DISPATCHED | task `task_78dba27a67d8`/`ctx_146d942c81b2`, opus/high effective; base `cbe0d03` | `cbe0d03` | Claude Opus high | wait worker_done |
| CP1B Worker framework | VERIFIED — PASS WITH NOTES (head `b07d2c6`, QA 4), integrated into `plan/monitoring-service` | QA-4 dispatch settled+released | `b07d2c6` | Codex gpt-6.1-sol high | done; CP2 packets must carry the reorg obligation; plan §6 amended |

## Next transition

- Exact next authorized action and owner: coordinator waits (`orca orchestration check --wait`) for worker_done; per delivery inspect diff vs allowed writes, re-run cheapest V row, log, start that lane's fresh Codex QA via approval-mode route 2 and, when a wave-2 slot frees, start CP2e (task `task_78dba27a67d8`, worktree new-child `ms-cp2e` from `plan/monitoring-service`). CP3 needs CP1A and CP2a–e VERIFIED and Marco's approval.
- Ready work with no active dispatch: none.
- Waiting for: worker_done from CP1A repair 3 and CP2a–d. Open owner items (non-blocking): live `config/` duplicate trigger names; `.gitignore` for `data/**/*.jsonl`; inbox file must pre-exist; spec M13 row 3 amendment at closure; cleanup of QA terminals/worktrees at closure.
- Next check/wake mechanism: attended; `orca orchestration check --wait` on the coordinator's Run (15 min) while workers run. Unattended recovery UNVERIFIED (DEF-4).
- Context resume: verify HEAD/status/runtime, read this file, inspect the next task's latest attempt, continue the first unproven action. Do not reload whole history.

## Decisions (mirrored in the log, event-0002)

- OG1 toolchain: installed by Marco (Foundry at `~/.foundry/bin`, not on PATH).
- AUTH-2: commits as `Marco Tabasco <marco@ssvlabs.io>` — granted; identity set.
- AUTH-3: Orca registration and child worktrees — granted; done.
- AUTH-4: all pages to the Telegram group — confirmed.
- QA: Codex GPT Sol 6.1 high for every QA verdict.
