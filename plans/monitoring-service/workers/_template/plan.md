# Worker: <CPx — title>

Task contract: [plan.md §8 CPx](../../plan.md) · Attempt log: [attempts.md](attempts.md) · QA contract: [qa/QA-CONTRACT.md](../../qa/QA-CONTRACT.md)

- Actual starting worktree/branch/head/status: <observed: `git rev-parse --abbrev-ref HEAD`, `git rev-parse HEAD`, `git status --porcelain | wc -l`>
- Live authority: <Orca Task and Dispatch IDs from the injected preamble; never copied from elsewhere>
- Requested/effective model and effort: <from the start receipt `launch.requested` / `launch.effective`>
- Allowed writes / prohibited actions / local commit authority: <copy from plan.md §5 row; commits on the lane branch only; no push; mainnet read-only; TEST_* channels only>
- Observable completion and owned verification IDs: <copy from plan.md §7 / §8>
- Dependencies and relevant design/semantic sources: <plan.md §6; spec sections; triage evidence scripts; `reconcile/checks/README.md` for wave 2>
- Commit capability checked: <yes/no — `git config user.name`, a no-op commit on the lane branch; manifest handoff fallback if no>

| Step | Status | Latest evidence | Exact next action |
|---|---|---|---|
| S1 | NOT STARTED | <attempt anchor> | <…> |

Before long work: append intent and persist the current step. After work: append the measured command and result and update this checkpoint. On interruption: inspect processes, Git state and the latest attempt before retrying; an absent receipt is not proof an action did not run.

On unexpected semantic failure (a spec rule that cannot be satisfied, a chain fact that contradicts the spec, a test that can only pass by weakening): preserve the reproduction, stop the affected mutation, send an Orca `ask` to the coordinator; never adjust expected behaviour to match output. Read coordinator follow-ups (`orca orchestration check --terminal <handle> --json`) before each new file, after each test run, and before `worker_done`.

## Completion handoff (≤ 250 words, also sent as the `worker_done` summary)

```text
Task / live dispatch:
Assignment outcome (completed work != passing acceptance):
Base -> resulting commit (lane branch head) or hashed manifest:
Actual changed paths / deletions / untracked exclusions:
Verification IDs -> explicit results; counts/skips/known reds; NOT RUN rows named:
Falsifiers -> red/green as expected; restoration proof (git status --porcelain):
Log anchor and evidence manifest:
New finding -> affected owners / blocking effect:
Pending human input (only if required):
Next owner and exact action: Codex QA on <lane branch head>
Preservation destination and remaining local-only material:
```

Set the checkpoint status in plan.md to `IMPLEMENTED — AWAITING QA` with the head hash, send `worker_done --outcome succeeded|failed` exactly once from the dispatched terminal, then idle.
