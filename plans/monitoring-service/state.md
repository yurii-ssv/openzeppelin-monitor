# Current state — monitoring-service

This is the sole current-state summary. History: [execution-log.md](execution-log.md). Contract: [plan.md](plan.md).

- Updated at: 2026-10-21T14:20Z
- Last recorded event / last processed Orca delivery: event-0028 / delivery_351f25060448 (acked)
- Repo/worktree/branch: `/Users/marco/ssv/repos/ssv-network-monitor` · `plan/monitoring-service` (created by CP0); QA worktree `/Users/marco/orca/workspaces/ssv-network-monitor/ms-qa-cp0` @ `9772b2d` (event-0010)
- Observed HEAD: `4fbb9f161005dd7634a7eaabee2b022d2cc96c2e` on `plan/monitoring-service` (CP0 VERIFIED + QA evidence merged); uncommitted by design: `.dockerignore`, `Dockerfile.production`, `.env copy.example`
- Runtime authority: Orca repo `316cdd8d-3d6f-4dd5-9b4b-34ac5792bf2a`. Run `run_96be3e0c7302` bound to this coordinator tab (monitor workspace, verified); old Run `run_6a94d8a4633c` retired. Workers use the full orca path if `orca` fails (F13)
- Mode: **CP1B VERIFIED + integrated; CP1A repair 2 running; awaiting Marco's wave-2 approval — attended**
- Overall acceptance: pending
- Current checkpoint: CP1A + CP1B (wave 1)
- Coordinator: this Claude Code session, repo root, monitor-workspace tab

| Task | State | Active dispatch / receipt | Frozen head | Owner | Evidence / next action |
|---|---|---|---|---|---|
| CP0.0 Orca loop proof | partial, stopped by owner | `ctx_8a4b4362736e` unsettled, terminal retained, idle | `86a17db` | coordinator | leave it; Codex completion proven at first QA |
| CP0 Baseline and hygiene | VERIFIED — PASS WITH NOTES, integrated (`4fbb9f1`) | QA `ctx_4bd11bbdbf4e` settled | `9772b2d` | Codex gpt-6.1-sol high | done; notes owned by CP1A (schema checks), CP3 (flake), coordinator (V0.4 slash) |
| CP1A Config lane | QA FAILED attempt 2 (head `9b3e749`; F1b M13 internal value/calls — not fixable in the Monitor; F8b indexed-layout ABI gate); repair 2 dispatched on lane head `6c94b27` | repair task `task_8f80ac8c19a9`/`ctx_da0703b996d4`, claude/sonnet/high effective | `6c94b27` | Claude Sonnet high (repair) → fresh Codex QA attempt 3 (route 2) | M13 scope decision pending Marco (recommend A: retired-key balance-delta check in worker CP2c); F5/F6/F7 deferrals |
| CP1B Worker framework | VERIFIED — PASS WITH NOTES (head `b07d2c6`, QA 4), integrated into `plan/monitoring-service` | QA-4 dispatch settled+released | `b07d2c6` | Codex gpt-6.1-sol high | done; CP2 packets must carry the reorg obligation; plan §6 amended |

## Next transition

- Exact next authorized action and owner: Marco approves the wave-2 transition (CP2a–e, Opus high, cap 4 + 1 queued; CP2 lanes depend only on CP1B). Coordinator then freezes the integration head, builds the five CP2 packets from `checks/README.md` + plan §7–§8 + spec, and dispatches. CP1A repair 2 delivery → QA attempt 3 (route 2) continues in parallel.
- Ready work with no active dispatch: none.
- Waiting for: Marco: wave-2 approval; M13 uncovered-part decision (A recommended: retired-key balance-delta watch in CP2c); F5 legacy Discord names. CP1A repair-2 worker_done. Other open owner items: live `config/` duplicate trigger names; `.gitignore` for `data/**/*.jsonl`; inbox file must pre-exist; cleanup of QA terminals/worktrees at closure.
- Next check/wake mechanism: attended; `orca orchestration check --wait` on the coordinator's Run (15 min) while workers run. Unattended recovery UNVERIFIED (DEF-4).
- Context resume: verify HEAD/status/runtime, read this file, inspect the next task's latest attempt, continue the first unproven action. Do not reload whole history.

## Decisions (mirrored in the log, event-0002)

- OG1 toolchain: installed by Marco (Foundry at `~/.foundry/bin`, not on PATH).
- AUTH-2: commits as `Marco Tabasco <marco@ssvlabs.io>` — granted; identity set.
- AUTH-3: Orca registration and child worktrees — granted; done.
- AUTH-4: all pages to the Telegram group — confirmed.
- QA: Codex GPT Sol 6.1 high for every QA verdict.
