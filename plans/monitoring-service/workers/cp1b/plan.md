# Worker: CP1B — Worker framework

Task contract: [plan.md §8 CP1B](../../plan.md) · Attempt log: [attempts.md](attempts.md) · QA contract: [qa/QA-CONTRACT.md](../../qa/QA-CONTRACT.md)

- Actual starting worktree/branch/head/status: `/Users/marco/orca/workspaces/ssv-network-monitor/ms-cp1b`, branch `mtabasco/ms-cp1b` (the Orca new-child lane branch), head `4fbb9f161005dd7634a7eaabee2b022d2cc96c2e` (equals the frozen base `plan/monitoring-service @ 4fbb9f1`), `git status --porcelain | wc -l` = 0.
- Live authority: Orca task `task_3a2ee3e27b00`, dispatch `ctx_98a50f4c4736`, run `run_96be3e0c7302`, worker terminal `term_b2097fa2-22c4-4943-9fc9-4645889b4598`, coordinator `term_89e2fa6f-482b-4551-995f-1f0e41c726a2`.
- Requested/effective model and effort: requested Claude Opus, effort high (brief); the worker cannot read `launch.effective` — the coordinator's start receipt is authoritative (the worker self-identifies as claude-opus-5-5).
- Allowed writes / prohibited actions / local commit authority: `reconcile/**`, `docker-compose.reconcile.yaml`, `plans/monitoring-service/workers/cp1b/**`, the CP1B **Status:** line in plan.md. Commits on the lane branch only; no push; mainnet read-only (eth_call, eth_getLogs, eth_getBalance, SSV API GETs); anvil forks only; alerts only in record mode or to `TEST_*`; never `config/`, `config.zip`, `.env`, `.local-dry/`; no `src/`, `tests/`, `deploy/`, `docs/`, `docker-compose.yaml`.
- Observable completion and owned verification IDs: A2; V1B.1–V1B.11 with falsifiers V1B.4f and V1B.7f (plan.md §7 CP1B). V1B.10 is LIVE (anvil + fork RPC).
- Dependencies and relevant design/semantic sources: plan.md §1, §6 (check interface, incident lifecycle, inbox contract, routing D2), F10; spec §3 M5, M15, §4, §5; `reconcile/` at the base and `plans/94775-eb-collapse-monitoring.md` (precedent); ssv-network at pin `f7ac487` (read-only, for event, error and view signatures).
- Commit capability checked: yes — `git config user.name` = Marco Tabasco, `user.email` = marco@ssvlabs.io.

| Step | Status | Latest evidence | Exact next action |
|---|---|---|---|
| S1 baseline: venv, V0.9 re-run (30 passed) | DONE | [attempt-1](attempts.md#attempt-1) | — |
| S2 framework modules (checks/, alerts, notify, inbox, inventory, params, metrics, scheduler, migrations) | NOT STARTED | — | write modules |
| S3 M5 port to `checks/m5_eb_desync.py`, scenarios kept | NOT STARTED | — | — |
| S4 fakechain extensions + `test_framework.py` | NOT STARTED | — | — |
| S5 V1B.1–V1B.9, V1B.11 + falsifiers | NOT STARTED | — | — |
| S6 V1B.10 fork e2e (LIVE) | NOT STARTED | — | — |
| S7 docs, status line, handoff | NOT STARTED | — | — |

Before long work: append intent and persist the current step. After work: append the measured command and result and update this checkpoint. On interruption: inspect processes, Git state and the latest attempt before retrying; an absent receipt is not proof an action did not run.

On unexpected semantic failure (a spec rule that cannot be satisfied, a chain fact that contradicts the spec, a test that can only pass by weakening): preserve the reproduction, stop the affected mutation, send an Orca `ask` to the coordinator; never adjust expected behaviour to match output. Read coordinator follow-ups (`orca orchestration check --terminal <handle> --json`) before each new file, after each test run, and before `worker_done`.

## Completion handoff (≤ 250 words, also sent as the `worker_done` summary)

```text
(filled at completion)
```
