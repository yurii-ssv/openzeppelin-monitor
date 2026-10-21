# Current state — monitoring-service

This is the sole current-state summary. History: [execution-log.md](execution-log.md). Contract: [plan.md](plan.md).

- Updated at: 2026-10-21T09:29Z
- Last recorded event / last processed Orca delivery: event-0005 / none
- Repo/worktree/branch: `/Users/marco/ssv/repos/ssv-network-monitor` · `main` (integration branch `plan/monitoring-service` not yet created)
- Observed HEAD: `86a17dbe1a004713798e08378bc9d59c35e86a45`; working tree dirty (F1, F2), user-owned, untouched
- Runtime authority: Orca repo `316cdd8d-3d6f-4dd5-9b4b-34ac5792bf2a`; active Run `run_6a94d8a4633c` (coordinator `term_b15807c1-69e8-45fd-ac56-58191d2cbff8`). Workers use the full orca path (F13); starts use exact worktree selectors, never `current` (F14)
- Mode: **active — CP0 implementing**
- Overall acceptance: pending
- Current checkpoint: CP0
- Coordinator: next Claude Code session in the repo root

| Task | State | Active dispatch / receipt | Frozen head | Owner | Evidence / next action |
|---|---|---|---|---|---|
| CP0.0 Orca loop proof | partial, stopped by owner | `ctx_8a4b4362736e` unsettled, terminal retained, idle | `86a17db` | coordinator | leave it; Codex completion proven at first QA |
| CP0 Baseline and hygiene | DISPATCHED | task `task_afe9e8ca054e`, dispatch `ctx_80ac41873894` (retry of abandoned `ctx_44ccd3cf0895`, event-0005); effective claude/sonnet/high | `86a17db` | Claude sonnet high (implement), Codex gpt-6.1-sol high (QA) | await worker_done; then inspect + Codex QA |

## Next transition

- Exact next authorized action and owner: coordinator waits for CP0 `worker_done` on `run_6a94d8a4633c`, inspects diff/untracked vs allowed writes, re-runs V0.4, then starts Codex QA (`--agent codex --model gpt-6.1-sol --effort high`) in a new child of the `plan/monitoring-service` head.
- Ready work with no active dispatch: none.
- Waiting for: nothing from Marco until CP1A/CP1B, which need OG2 (`MAINNET_RPC_URL`, `TEST_RPC_URL` exported in the coordinator shell).
- Next check/wake mechanism: attended; `orca orchestration check --wait` on the coordinator's Run (15 min) while workers run. Unattended recovery UNVERIFIED (DEF-4).
- Context resume: verify HEAD/status/runtime, read this file, inspect the next task's latest attempt, continue the first unproven action. Do not reload whole history.

## Decisions (mirrored in the log, event-0002)

- OG1 toolchain: installed by Marco (Foundry at `~/.foundry/bin`, not on PATH).
- AUTH-2: commits as `Marco Tabasco <marco@ssvlabs.io>` — granted; identity set.
- AUTH-3: Orca registration and child worktrees — granted; done.
- AUTH-4: all pages to the Telegram group — confirmed.
- QA: Codex GPT Sol 6.1 high for every QA verdict.
