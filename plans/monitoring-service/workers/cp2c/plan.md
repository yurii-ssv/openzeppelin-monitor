# Worker: CP2c — Wallets and supply (M12, M8 floor) plus M13 retired-key balance watch (D10)

Task contract: [plan.md §8 CP2c](../../plan.md) · Attempt log: [attempts.md](attempts.md) · QA contract: [qa/QA-CONTRACT.md](../../qa/QA-CONTRACT.md)

- Actual starting worktree/branch/head/status: `/Users/marco/orca/workspaces/ssv-network-monitor/ms-cp2c`, branch `mtabasco/ms-cp2c` (Orca new-child lane branch), head `96d1c32fe3446ec7092e849e401f851a0d9b2cf3` (equals the frozen base `plan/monitoring-service @ 96d1c32`), `git status --porcelain | wc -l` = 0.
- Live authority: Orca task `task_8384f0c7778e`, dispatch `ctx_113e2440cd8e`, run `run_96be3e0c7302`, worker terminal `term_c3481f3f-a9aa-438b-8359-4a22b2c9d297`, coordinator `term_89e2fa6f-482b-4551-995f-1f0e41c726a2`.
- Requested/effective model and effort: requested Claude Opus, effort high (brief); the worker cannot read `launch.effective` — the coordinator's start receipt is authoritative (the worker self-identifies as claude-opus-5-5).
- Allowed writes / prohibited actions / local commit authority: `reconcile/checks/m12_*.py`, `m8_*.py`, `m13_*.py`, `reconcile/tests/test_m12.py`, `test_m8.py`, `test_m13.py`, `reconcile/tests/fixtures_wallets.py`, `checks.m12`/`checks.m8`/`checks.m13` in `reconcile/config.example.json`, one line per check in the CP2c block of `reconcile/checks/__init__.py`, `plans/monitoring-service/workers/cp2c/**`, the CP2c **Status:** line in plan.md. Commits on the lane branch only; no push; mainnet read-only (eth_call, eth_getLogs, eth_getBalance and other reads, Blockscout/SSV API GETs); anvil forks only; alerts only in record mode or `TEST_*`. Frozen: `reconcile/eb_*.py`, `checks/base.py`, `registry.py`, `alerts.py`, `db.py`, `inbox.py`, `params.py`, `inventory.py`, `metrics.py`, `notify.py`, `rpc.py`, `tests/fakechain.py`, `tests/harness.py`, `checks/README.md`, other lanes' files, `execution-log.md`, `qa/**`.
- Observable completion and owned verification IDs: A5; V2x.1–V2x.3, V2x.5, V2x.6 for M12, M8, M13; V2c.7 (runway from trailing spend and from current base fee); V2c.8 (M13 balance watch). V2x.4 (`rpc_budget`) is not listed for CP2c; per-run call counts are still asserted where the coordinator asked (M13 cap).
- Dependencies and relevant design/semantic sources: plan.md §1, §2 (D3, D4, D10), §6, §7 CP2 rows, §8 CP2c; `reconcile/checks/README.md` (all); spec §3 M8, M12, M13, §4, §5. The checkpoint names no triage evidence script.
- Commit capability checked: yes — `git config user.name` = Marco Tabasco, `user.email` = marco@ssvlabs.io.
- Coordinator design answer (ask, 2026-10-21T13:4xZ): lane-local RPC helper accepted (CP3 promotion item; per-method counts kept in `ctx.rpc.counts`); M13 per-block reads capped per sweep with an explicit beyond-cap behaviour (checkpoint + bounded sample, page on any increase, finding says intra-gap netting could not be excluded), cap and long-restart-gap tests; M13 value-left resolution says plainly it is not an all-clear, reorg rule applies to `<addr>:<block>` keys; M12 Blockscout-candidates/on-chain-verification/nonce-contiguity design approved.

| Step | Status | Latest evidence | Exact next action |
|---|---|---|---|
| S1 baseline: venv, read contract, design ask | DONE | [attempt-1](attempts.md#attempt-1) | — |
| S2 fixtures (`WalletChain`, `FakeBlockscout`) | NOT STARTED | — | write `reconcile/tests/fixtures_wallets.py` |
| S3 M12 check + tests (V2x.1–3, V2c.7) | NOT STARTED | — | — |
| S4 M8 floor check + tests | NOT STARTED | — | — |
| S5 M13 balance watch + tests (V2c.8) | NOT STARTED | — | — |
| S6 V2x.5 falsifiers, V2x.6 whole suite + ruff, adversarial self-review | NOT STARTED | — | — |
| S7 docs (lane README below), status line, handoff, worker_done | NOT STARTED | — | — |

Before long work: append intent and persist the current step. After work: append the measured command and result and update this checkpoint. On interruption: inspect processes, Git state and the latest attempt before retrying; an absent receipt is not proof an action did not run.

On unexpected semantic failure (a spec rule that cannot be satisfied, a chain fact that contradicts the spec, a test that can only pass by weakening): preserve the reproduction, stop the affected mutation, send an Orca `ask` to the coordinator; never adjust expected behaviour to match output. Read coordinator follow-ups (`orca orchestration check --terminal <handle> --json`) before each new file, after each test run, and before `worker_done`.

## Completion handoff (≤ 250 words, also sent as the `worker_done` summary)

```text
(filled at completion)
```
