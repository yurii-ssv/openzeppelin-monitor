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
| S2 framework modules (checks/, alerts, notify, inbox, inventory, params, metrics, scheduler, migrations) | DONE | [attempt-2](attempts.md#attempt-2) | — |
| S3 M5 port to `checks/m5_eb_desync.py`, scenarios kept | DONE | [attempt-2](attempts.md#attempt-2), V1B.6 | — |
| S4 fakechain extensions + `test_framework.py` + `harness.py` | DONE | [attempt-2](attempts.md#attempt-2) | — |
| S5 V1B.1–V1B.9, V1B.11 + falsifiers | DONE | [attempt-3](attempts.md#attempt-3) | — |
| S6 V1B.10 fork e2e (LIVE) | DONE (PASS on anvil fork of block 26,240,643) | [attempt-3](attempts.md#attempt-3) | — |
| S7 docs, status line, handoff | DONE | this file | Codex QA |
| R1 repair of QA attempt 1 (legacy outbox routing, stale params, missing inbox) | DONE (code `4819c9e`) | [repair-1](attempts.md#repair-1) | fresh Codex QA |
| R2 repair of QA attempt 2 (reorg leaves orphan params) + reorg-state audit (README §6.1) + inbox GATE | DONE (code `7bf01b4`) | [repair-2](attempts.md#repair-2--qa-attempt-2-finding-fresh-implementer) | fresh Codex QA |
| R3 repair of QA attempt 3 (orphan-only M5 cluster kept paging) + persisted-key audit + reorg re-find rule (README §6.2) + adversarial sweep | DONE (code `9101624`) | [repair-3](attempts.md#repair-3) | fresh Codex QA |

Before long work: append intent and persist the current step. After work: append the measured command and result and update this checkpoint. On interruption: inspect processes, Git state and the latest attempt before retrying; an absent receipt is not proof an action did not run.

On unexpected semantic failure (a spec rule that cannot be satisfied, a chain fact that contradicts the spec, a test that can only pass by weakening): preserve the reproduction, stop the affected mutation, send an Orca `ask` to the coordinator; never adjust expected behaviour to match output. Read coordinator follow-ups (`orca orchestration check --terminal <handle> --json`) before each new file, after each test run, and before `worker_done`.

## Completion handoff (≤ 250 words, also sent as the `worker_done` summary)

```text
Task / live dispatch: task_3a2ee3e27b00 / ctx_98a50f4c4736 (run_96be3e0c7302)
Assignment outcome: completed; all owned rows have an outcome, none red.
Base -> resulting commit: 4fbb9f1 -> code aaf5c25 (records commit on top = lane head, reported in worker_done) on mtabasco/ms-cp1b.
Actual changed paths: reconcile/** and docker-compose.reconcile.yaml (+ workers/cp1b/**, CP1B status line); no deletions outside reconcile (Worker.sweep, in-file Rpc/DB/notifiers moved to modules).
Verification: V1B.1 62 passed/0 skipped; V1B.2 ruff clean; V1B.3 1; V1B.4 2; V1B.5 4; V1B.6 32 (all 26 pre-existing names identical); V1B.7 6; V1B.8 curl OK, 5 required series; V1B.9 LIVE 1,908 API rows, 25/25 tuples validated; V1B.10 LIVE anvil PASS x5; V1B.11 README present. NOT RUN: image build (CP3), --telegram.
Falsifiers: V1B.4f red (two-check collision), V1B.7f red (2 inbox tests); restored, porcelain 0.
Log anchor: workers/cp1b/attempts.md#attempt-3.
New findings: (1) spec M5 warn/page/info grading differs from the preserved precedent (page on first confirmed) -> coordinator/Marco; (2) env names now MAINNET_RPC_URL/TELEGRAM_*/DISCORD_WEBHOOK_INFO -> CP1A .env.example, CP3; (3) F10 MEASURED; (4) two wrong selector comments at the pin (informational).
Pending human input: M5 severity decision (non-blocking).
Next owner: Codex QA on the lane head.
Preservation: everything committed; reconcile/.venv untracked (ignored).
```

## Repair 1 handoff (≤ 250 words, also sent as the `worker_done` summary)

```text
Task / live dispatch: task_713a86050a2e / ctx_d9005050e993 (run_96be3e0c7302)
Assignment outcome: completed; the three QA attempt-1 findings repaired, no reproducer weakened.
Base -> resulting commit: c21a51f -> code 4819c9e (records commit on top = lane head) on mtabasco/ms-cp1b.
Changed paths: reconcile/{db,eb_reconcile,params,inbox,metrics}.py, reconcile/README.md, reconcile/checks/README.md, reconcile/tests/test_qa_regressions.py (new), docker-compose.reconcile.yaml (comment), workers/cp1b/**, CP1B status line.
Mechanism: legacy outbox severities normalised at migration, NULL route resolved by severity at delivery; failed param read invalidates values (ParamsUnavailable on access), drops prepare, skips dependent runs without advancing cursors, healthy=false, retried every step (README §5.1); unreadable/missing inbox -> inbox-unavailable health per outage, healthy=false, empty file valid (§5.2).
Verification: 3 QA probes red on c21a51f, green after; Appendix A verbatim 12/12; V1B.1 69 passed/0 skipped; V1B.2 clean; V1B.3 1; V1B.4 2; V1B.5 4; V1B.6 32; V1B.7 6; V1B.8 curl OK; V1B.4f/V1B.7f red then restored green; V1B.10 LIVE PASS x5 (fork 26,240,758). NOT RE-RUN: V1B.9 (inventory untouched).
Log anchor: workers/cp1b/attempts.md#repair-1.
New finding: deployment must create the empty inbox file before start (CP1A/CP3).
Next owner: fresh Codex QA.
Preservation: everything committed; reconcile/.venv ignored.
```

## Repair 2 handoff (≤ 250 words, also sent as the `worker_done` summary)

```text
Task / live dispatch: task_f48565e119a7 / ctx_8f265023f59d (run_96be3e0c7302)
Assignment outcome: completed; QA attempt-2 HIGH repaired, reorg-state audit done, no QA assertion weakened.
Base -> resulting commit: bea73fd -> code 7bf01b4 (records commit on top = lane head) on mtabasco/ms-cp1b.
Changed paths: reconcile/{eb_reconcile,alerts,db,inbox,metrics,rpc}.py, checks/{base.py,README.md}, README.md, ruff.toml, tests/{test_framework.py (1 fixture), test_qa2_regressions.py, test_reorg_state.py}; workers/cp1b/**; CP1B status line.
Mechanism: a reorg invalidates live params and every prepare copy (re-read at the new checkpoint before any check; failed re-read = §5.1). Audit table README §6.1; same-class fixes: scan rejects non-canonical log blocks, shorter new chain rewinds, durable reorg marker + ctx.reorg_ancestor, near-set reset, incident-id/outbox-key suffix instead of error/silent drop, inbox GATE (coordinator-approved: canonical, at/below checkpoint; orphaned never delivered; lookup failure holds + degrades).
Verification: QA Appendix A + B -k reorged_parameter_event red on bea73fd, green now (unmodified); new tests 14 red on bea73fd; V1B.1 102/0 skipped; .2 clean; .3 1; .4 2; .4f red/green; .5 4; .6 33; .7 6; .7f red/green; .8 curl OK; .10 LIVE PASS x5 (fork 26,240,956). NOT RUN: V1B.9 (inventory.py unchanged).
Note: QA Appendix B partial-reload probe needs its inbox fixture to carry the canonical block hash (GATE).
Log anchor: workers/cp1b/attempts.md#repair-2.
Next owner: fresh Codex QA.
```

## Repair 3 handoff (≤ 250 words, also sent as the `worker_done` summary)

```text
Task / live dispatch: task_9a03e44ccd1f / ctx_6a127ac3b775 (run_96be3e0c7302)
Assignment outcome: completed; QA attempt-3 HIGH repaired as a class; no QA assertion weakened.
Base -> resulting commit: 7eb2df3 -> code 9101624 (records commit on top = lane head) on mtabasco/ms-cp1b.
Changed paths: reconcile/{eb_reconcile,alerts}.py, checks/{base,m5_eb_desync}.py, checks/README.md, README.md, ruff.toml, tests/{qa3_orphan_repro,test_qa3_regressions,test_qa3_appendix_b,test_reorg_refind,test_reorg_sweep}.py (new); workers/cp1b/**; CP1B status line.
Mechanism (coordinator-approved A): reorg re-find rule - in a reorg run ctx.reorg_keys = open incidents last seen above the ancestor; unreported -> resolved "evidence left the canonical chain" (info); re-alerts wait, healthy:false while pending; a check that cannot evaluate raises. M5 reorg run = full sweep over logs + persisted keys: vanished cluster forgotten, confirmed->latent resolved + latent, UNKNOWN kept, layout failure raises. Also fixed: latent de-escalation, digest.
Verification: QA3 Appendix A 0/12 red on 7eb2df3, green now; Appendix B 31/31; new tests 18 red on frozen head; sweep 90 cases (69 red on frozen head, 0 now); V1B.1 240/0 skipped; .2 clean; .3 1; .4 2; .4f red/green; .5 4; .6 41; .7 6; .7f red/green; .8 curl OK; .10 LIVE PASS x5 (fork 26,241,130). NOT RUN: V1B.9 (inventory.py unchanged).
Coordinator: amend plan §6 lifecycle + Ctx list per checks/README.md §6.2 end.
Log anchor: workers/cp1b/attempts.md#repair-3.
Next owner: fresh Codex QA.
```
