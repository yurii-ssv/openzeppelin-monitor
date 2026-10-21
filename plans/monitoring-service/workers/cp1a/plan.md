# Worker: CP1A — Config lane

Task contract: [plan.md §8 CP1A](../../plan.md) · Attempt log: [attempts.md](attempts.md) · QA contract: [qa/QA-CONTRACT.md](../../qa/QA-CONTRACT.md)

- Actual starting worktree/branch/head/status: `mtabasco/ms-cp1a` (Orca worktree `/Users/marco/orca/workspaces/ssv-network-monitor/ms-cp1a`), head `4fbb9f161005dd7634a7eaabee2b022d2cc96c2e` (= frozen base, confirmed), porcelain 0 before this record was created.
- Live authority: Task `task_cadc69413a4e`, Dispatch `ctx_42eae2d9dfb0` (injected preamble), coordinator terminal `term_89e2fa6f-482b-4551-995f-1f0e41c726a2`, worker terminal `term_529f3199-0953-4041-8630-89aa9be6a08c`.
- Requested/effective model and effort: Claude Sonnet 5.5, high (per brief; the start receipt is held by the coordinator).
- Allowed writes / prohibited actions / local commit authority: `deploy/**`, `docker-compose.yaml`, `scripts/local-dry-run.sh`, `.env.example`, `plans/monitoring-service/workers/cp1a/**`, the CP1A status line of plan.md. Nothing else (no `src/`, `tests/`, `reconcile/`, `docs/`, `config/`, `config.zip`, `.env`). Commits on `mtabasco/ms-cp1a` only; never push; mainnet read-only (eth_getLogs, block/receipt reads, Blockscout GETs); alerts only to TEST_* or none.
- Observable completion and owned verification IDs: A1; V1A.1, V1A.1f, V1A.2, V1A.3, V1A.4, V1A.4f, V1A.5, V1A.6, V1A.7, V1A.8 plus the CP0 QA follow-up (required-field assertions beside the V0.6 scan, committed read-only checker under `deploy/`).
- Dependencies and relevant design/semantic sources: plan §6 (inbox contract, template layout), spec M8, M10, M11, M13, M15, §4, §5; CP0 attempt 1 deviation (a) (chat_id placeholder).
- Commit capability checked: yes — `git config user.name` = Marco Tabasco; real commits on this branch are the proof.

| Step | Status | Latest evidence | Exact next action |
|---|---|---|---|
| S1 matching semantics one-shot | DONE | [attempt 1](attempts.md#attempt-1), [attempt 2](attempts.md#attempt-2) | — |
| S2 render.sh + templates | DONE | `58240a5`, `ad7ce32`, `4eee016` | — |
| S3 checker script | DONE | `deploy/check-templates.sh`, falsifiers in attempt 2 | — |
| S4 dry-run + compose + .env.example | DONE | `58240a5` | — |
| S5 V rows + falsifiers | DONE | attempt 2 (all V1A rows run; none NOT RUN) | — |
| S6 handoff | DONE | this block, plan.md status line | Codex QA |
| R1 repair 1 (F1, F2, F3, F4, F8) | DONE — QA attempt 2 FAILED (F1b, F8b) | [repair 1](attempts.md#repair-1), code head `10c7a8f`, runner `evidence/repair-1/vrows.sh` | superseded by R2 |
| R2 repair 2 (F8b fixed, F1b documented) | DONE — QA attempt 3 FAILED (F1b, F9) | [repair 2](attempts.md#repair-2), code head `f04c79e`, runner `evidence/repair-2/vrows.sh` | superseded by R3 |
| R3 repair 3 (F9 fixed, F1b closed by D10, D11 routing) | DONE — AWAITING FRESH QA | [repair 3](attempts.md#repair-3), code head `e981687`, runner `evidence/repair-3/vrows.sh` | fresh Codex QA decides; F6/F7 untouched |

Before long work: append intent and persist the current step. After work: append the measured command and result and update this checkpoint. On interruption: inspect processes, Git state and the latest attempt before retrying; an absent receipt is not proof an action did not run.

On unexpected semantic failure (a spec rule that cannot be satisfied, a chain fact that contradicts the spec, a test that can only pass by weakening): preserve the reproduction, stop the affected mutation, send an Orca `ask` to the coordinator; never adjust expected behaviour to match output. Read coordinator follow-ups (`orca orchestration check --terminal <handle> --json`) before each new file, after each test run, and before `worker_done`.

## Completion handoff (≤ 250 words, also sent as the `worker_done` summary)

```text
Task / live dispatch: task_cadc69413a4e / ctx_42eae2d9dfb0
Assignment outcome: all deliverables built; V1A.1-V1A.8 run, none NOT RUN. Completed work != acceptance.
Base -> head: 4fbb9f1 -> HEAD of mtabasco/ms-cp1a (status-line commit after 9de75ec)
Changed paths: deploy/{render.sh,check-templates.sh,check-m15-coverage.sh}; deploy/config: 5 monitors, 5 triggers, inbox_append.sh, network template (max_past_blocks 3600, recovery_config, optional 2nd RPC), 2 trigger names; docker-compose.yaml; scripts/local-dry-run.sh; .env.example; workers/cp1a/*. No deletions except scripts/.gitkeep.
V rows: V1A.1 PASS (render gate + raw check, 31 monitors); V1A.1f raw exit 0 + ERROR line / render.sh exit 1 (accepted deviation: raw --check exit is never non-zero); V1A.2 0 rows + checker OK; V1A.3 1 match + 1 inbox line; V1A.4 1 match; V1A.4f 0; V1A.5 1+1 (0x3A98, 0x4BC6); V1A.6 red/green; V1A.7 all 16 present; V1A.8 valid. Archive rows on eth.drpc.org (publicnode is recent-only).
Falsifiers: all red then restored, porcelain clean.
Findings: --check exit always 0; expression grammar is AND/OR (no &&), gas_used empty without receipt; status Success mislabels reverts; live config has duplicate trigger names (Marco); .gitignore lacks data/**/*.jsonl; M13 split into two monitors, attacker zero-value interaction uncovered.
Pending human input: none.
Next owner: Codex QA on the lane branch head.
Local-only: .local-dry/, target/ (ignored).
```


## Repair 1 handoff (≤ 250 words, also the `worker_done` summary)

```text
Task / live dispatch: task_2888efd1d913 / ctx_01667b707874
Assignment outcome: F1, F2, F3, F4, F8 repaired; V1A.1-V1A.8 + falsifiers re-run, none NOT RUN. Completed work != acceptance; fresh QA decides.
Base -> head: 3f9bf7a -> docs commit after code head 10c7a8f (branch mtabasco/ms-cp1a)
Changed paths: deploy/{check-abi.sh(new),check-templates.sh,check-m15-coverage.sh,render.sh}; deploy/config: monitors ssv_attacker_interaction (new), ssv_retired_addresses, ssv_dao_success; trigger telegram_attacker_interaction (new), telegram_retired_addresses; deploy/tests/{selftest.sh,m13-fork-regression.sh}; workers/cp1a/**.
Evidence: evidence/repair-1/vrows-run.log (every row exit+counts), red-baseline-3f9bf7a.log. selftest 31/0; anvil fork regression 15/0 (old tree 15/15 red); F8 Nonexistent(uint256): render exit 1, restored 0, tree clean. Endpoint eth.drpc.org (archive), publicnode for render.
Decisions for coordinator: Telegram DAO monitor lost the M15 events AND their functions; non-M15 extras (ownership/upgrade-admin/initialize/rescue) still on Telegram; attacker rule is tx-level (internal calls invisible); attacker plain transfers to unrelated addresses no longer page.
Not touched: F5 (CP3), F6, F7.
Pending human input: none.
Next owner: fresh Codex QA.
```


## Repair 2 handoff (≤ 250 words, also the `worker_done` summary)

```text
Task / live dispatch: task_8f80ac8c19a9 / ctx_da0703b996d4
Assignment outcome: F8b fixed; F1b documented as an M13 coverage boundary (not repairable in the Monitor). V1A rows + falsifiers re-run, none NOT RUN. Completed work != acceptance; fresh QA decides.
Base -> head: 6c94b27 -> docs commit after code head f04c79e (branch mtabasco/ms-cp1a)
Changed paths: deploy/{check-abi.sh,check-templates.sh,render.sh,README.md}; deploy/abi/{reference.json,provenance.json,normalize.jq,derive.sh}; deploy/tests/{selftest.sh,abi-mutation-sweep.sh}; workers/cp1a/**. No template changed.
F8b: A8/A9 compare every contract_spec event/function (3356 entries, 30 monitors) with a committed reference derived from ssv-network f7ac487 (forge build, codehash = lock) and Safe 1.3.0, verified on-chain by real logs. Six tracked-template mutations red then green; 16-kind sweep 510 runs 0 failures; same sweep on the repair-1 gate: 359 cells accepted. selftest 51/0/0, fork suite 15/0, evidence/repair-2/vrows-run.log (endpoints per row: eth.drpc.org, publicnode for render).
F1b: README "M13 coverage boundary" lists covered vs uncovered (internal CALLs, constructors, selfdestruct) with QA reproducers; no scope change.
Not touched: F5, F6, F7.
Pending human input: none (Marco decides worker balance-delta watching).
Next owner: fresh Codex QA.
```


## Repair 3 handoff (≤ 250 words, also the `worker_done` summary)

```text
Task / live dispatch: task_a4c6f85f4579 / ctx_b8fbc82c69b7
Assignment outcome: F9 fixed; F1b closed by D10 (README only); D11 routing applied. V1A rows + falsifiers re-run, none NOT RUN. Completed work != acceptance; fresh QA decides.
Base -> head: 204d528 -> docs commit after code head e981687 (branch mtabasco/ms-cp1a)
Changed paths: deploy/{check-m15-coverage.sh,check-templates.sh,README.md,tests/selftest.sh}; six deploy/config/triggers/discord_*_success.json; .env.example; docker-compose.yaml; workers/cp1a/**.
F9: required set = spec M15 names + ModuleUpgraded (17), full signatures from the reference ABI; old gate accepted the ModuleUpgraded deletion (exit 0), new gate red for each of 17 deletions on the tracked template, restored green; Telegram DAO monitor has 0 unconditional M15 events (17 additions red).
D11: six *_SUCCESS triggers on DISCORD_WEBHOOK_INFO; nine keep own variables; names unique; grep of removed names outside plans/: 0; S4 asserts the split, missing discord_url still red.
F1b: README row-by-row Monitor / CP2c watch / accepted gap, cites D10.
Evidence: evidence/repair-3/vrows-run.log; selftest 119/0/0 (baseline 51); sweep 510/0; fork suite 15/0; live rows on eth.drpc.org, render on publicnode.
Open: D10/D11 rows absent from this lane's plan.md §2 (exist on CP2 branches); F6/F7 untouched; removed names persist only in append-only records.
Pending human input: none.
Next owner: fresh Codex QA.
```
