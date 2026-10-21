# Worker: CP0 — Baseline and hygiene

Task contract: [plan.md §8 CP0](../../plan.md) · Attempt log: [attempts.md](attempts.md) · QA contract: [qa/QA-CONTRACT.md](../../qa/QA-CONTRACT.md)

- Actual starting worktree/branch/head/status: repo root `/Users/marco/ssv/repos/ssv-network-monitor`, branch `main`, head `86a17dbe1a004713798e08378bc9d59c35e86a45` (equals the frozen base), `git status --porcelain | wc -l` = 437 (428 mode-only, 5 content, untracked SSV material).
- Live authority: Orca task `task_afe9e8ca054e`, dispatch `ctx_80ac41873894`, run `run_6a94d8a4633c`, worker terminal `term_be9c7771-fd3b-409e-a769-25bf4473599f`, coordinator `term_b15807c1-69e8-45fd-ac56-58191d2cbff8`.
- Requested/effective model and effort: requested Claude Sonnet, effort high (brief); the worker cannot read `launch.effective` — the coordinator's start receipt is authoritative (the worker self-identifies as claude-sonnet-5-5).
- Allowed writes / prohibited actions / local commit authority: plan.md §5 CP0 row (`.gitignore`, `.claude/settings.json`, `deploy/config/**`, `docs/ssv-specs/*.md` §6 only, `plans/monitoring-service/workers/cp0/**`, the CP0 Status line, git metadata, the existing `src/`+`tests/` diff commit only). Commits on `plan/monitoring-service` only; no push; mainnet read-only; no alerts sent; `config/`, `config.zip`, `.env*`, `.local-dry/` read-only; `.dockerignore` and `Dockerfile.production` content diffs left uncommitted.
- Observable completion and owned verification IDs: V0.1–V0.12 incl. V0.3f (plan.md §7 CP0).
- Dependencies and relevant design/semantic sources: plan.md §1, §3 (F1–F13), §5, §6, §7, §8; spec §6; coordinator answer to the V0.3 question (event in attempts.md attempt 2).
- Commit capability checked: yes — `git config user.name` = Marco Tabasco, real commits made on `plan/monitoring-service`.

| Step | Status | Latest evidence | Exact next action |
|---|---|---|---|
| S1 re-measure V0.1 | DONE | [attempt-1](attempts.md#attempt-1) | — |
| S2 branch + mode revert + V0.2 | DONE | [attempt-1](attempts.md#attempt-1) | — |
| S3 `.gitignore` + V0.4 | DONE | [attempt-1](attempts.md#attempt-1) | — |
| S4 SSV material + V0.5 | DONE | [attempt-1](attempts.md#attempt-1) | — |
| S5 templates + V0.6, V0.3, V0.3f | DONE | [attempt-1](attempts.md#attempt-1), [attempt-2](attempts.md#attempt-2) | — |
| S6 V0.7–V0.9, Rust commit | DONE (V0.8 green with known pre-existing flake) | [attempt-3](attempts.md#attempt-3) | CP3 owns the flake fix |
| S7 spec §6 + V0.11 | DONE | [attempt-3](attempts.md#attempt-3) | — |
| S8 `.claude/settings.json` + V0.12 | DONE | [attempt-3](attempts.md#attempt-3) | — |
| S9 baselines and KNOWN RED signature | DONE | [attempt-3](attempts.md#attempt-3) | coordinator mirrors into execution-log.md |

Before long work: append intent and persist the current step. After work: append the measured command and result and update this checkpoint. On interruption: inspect processes, Git state and the latest attempt before retrying; an absent receipt is not proof an action did not run.

On unexpected semantic failure (a spec rule that cannot be satisfied, a chain fact that contradicts the spec, a test that can only pass by weakening): preserve the reproduction, stop the affected mutation, send an Orca `ask` to the coordinator; never adjust expected behaviour to match output. Read coordinator follow-ups (`orca orchestration check --terminal <handle> --json`) before each new file, after each test run, and before `worker_done`.

## Completion handoff (≤ 250 words, also sent as the `worker_done` summary)

```text
Task / live dispatch: task_afe9e8ca054e / ctx_80ac41873894
Assignment outcome (completed work != passing acceptance): steps 1-9 done; V0.1-V0.12 incl. V0.3f measured; QA owns the verdict.
Base -> resulting commit (lane branch head) or hashed manifest: 86a17db -> plan/monitoring-service work head @@X@@ (final commit adds only the status hash).
Actual changed paths / deletions / untracked exclusions: .gitignore; new reconcile/, plans/, docs/ssv-specs/, scripts/local-dry-run.sh, 2 compose files, deploy/config/** (55), .claude/settings.json; spec §6; src+tests filter diff; 43 mode normalisations. Uncommitted by design: .dockerignore, Dockerfile.production, ".env copy.example". Ignored: config.zip, .local-dry/, config/.
Verification IDs -> explicit results; counts/skips/known reds; NOT RUN rows named: V0.2 0 modes, 5 content files; V0.3 0 rows; V0.5 82 files; V0.6 empty; V0.7 exit 0; V0.8 GREEN WITH KNOWN FLAKE: 16/18 green, 2 red (pre-existing mockall race, main 1/60), serial 6/6 green, target test passes; V0.9 30 passed; V0.10 10 commits; V0.11 5 lines; V0.12 ok. None NOT RUN.
Falsifiers -> red/green as expected; restoration proof (git status --porcelain): V0.3f red, then porcelain identical to baseline.
Log anchor and evidence manifest: workers/cp0/attempts.md attempts 1-3.
New finding -> affected owners / blocking effect: F14 flaky mock tests (CP3, non-blocking); chat_id placeholder ${TELEGRAM_CHAT_ID} needs envsubst allowlist (CP1A); F3 stale (26 monitors); F4 venv stale.
Pending human input (only if required): none.
Next owner and exact action: Codex QA on the plan/monitoring-service head, rows V0.1-V0.12.
Preservation destination and remaining local-only material: branch plan/monitoring-service (no push); local-only: the two uncommitted diffs, config.zip, config/, .local-dry/.
```

Set the checkpoint status in plan.md to `IMPLEMENTED — AWAITING QA` with the head hash, send `worker_done --outcome succeeded|failed` exactly once from the dispatched terminal, then idle.
