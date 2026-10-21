# Worker: CP2b — Oracle family (M9, M10, M11)

Task contract: [plan.md §8 CP2b](../../plan.md) · Attempt log: [attempts.md](attempts.md) · QA contract: [qa/QA-CONTRACT.md](../../qa/QA-CONTRACT.md)

- Actual starting worktree/branch/head/status: `/Users/marco/orca/workspaces/ssv-network-monitor/ms-cp2b`, branch `mtabasco/ms-cp2b`, head `96d1c32fe3446ec7092e849e401f851a0d9b2cf3` (= the frozen base `plan/monitoring-service @ 96d1c32`), `git status --porcelain | wc -l` = 0. Coordinator-ruled merges of `plan/monitoring-service` on top: `438904e` (test patch bd531b4, ruling v2) and `f92e2b7` (harness 500abe4, ruling v2.1), both fast-forward, no lane file touched.
- Live authority: Orca task `task_13f583847d79`, dispatch `ctx_e0d798da26ac`, run `run_96be3e0c7302`, worker terminal `term_94477f93-84f9-4fef-8d89-8933bd7c9944`, coordinator `term_89e2fa6f-482b-4551-995f-1f0e41c726a2`.
- Requested/effective model and effort: requested Claude Opus, effort high (brief); the worker cannot read `launch.effective` (the coordinator's start receipt is authoritative); it self-identifies as claude-opus-5-5.
- Allowed writes: `reconcile/checks/m9_*.py`, `m10_*.py`, `m11_*.py`, `reconcile/tests/test_m9.py`, `test_m10.py`, `test_m11.py`, `reconcile/tests/fixtures_oracle.py`, `checks.m9/m10/m11` in `reconcile/config.example.json`, one line in `reconcile/checks/__init__.py`, `plans/monitoring-service/workers/cp2b/**`, the CP2b status line in plan.md. Commits on the lane branch only; no push; mainnet read-only; record mode only.
- Observable completion and owned verification IDs: A4; V2x.1–V2x.6 for M9, M10, M11 (V2x.4 rpc_budget applies) and V2b.7 (no RootCommitted for 9 h fires from the clock).
- Sources read: plan.md §1, §2, §5–§8; `reconcile/checks/README.md` in full; spec §3 M9–M11, §4, §5; `ssv-network-triage/evidences/eb-oracle-cadence/{scan,scan2,scan3,scan4,probe}.mjs` + README + scan_out.json; `deploy/config/filters/oracle_dissent_check.py`; ssv-network `f7ac487` `SSVDAO.commitRoot`/`replaceOracle`, `ISSVDAO` events, `SSVNetworkViews.getEffectiveBalance`, `SSVClusters.updateClusterBalance` (signatures and revert semantics only).
- Commit capability checked: yes (`Marco Tabasco <marco@ssvlabs.io>`).

| Step | Status | Latest evidence | Exact next action |
|---|---|---|---|
| S1 env: venv per §7, base suite 240 passed | DONE | [attempt-1](attempts.md#attempt-1) | — |
| S2 checks M9/M10/M11 + shared round model / episode book | DONE | [attempt-2](attempts.md#attempt-2) | — |
| S3 scenarios, fixtures, recorded mainnet replay | DONE | [attempt-2](attempts.md#attempt-2) | — |
| S4 V2x.1–V2x.6, V2b.7, falsifiers, LIVE probes | DONE | [attempt-3](attempts.md#attempt-3) | — |
| S5 records, status line, handoff | DONE | this file | Codex QA on the lane head |

## Design (worker-local decisions; none changes a spec trigger or severity)

1. **Everything is re-derived every run** from the stored proxy logs (`WeightedRootProposed` declared in `events`, `RootCommitted`, `OracleReplaced`), the gated inbox rows (`canonical = 1`) and the live parameters. The only state is an *episode book* per check (`m9_episodes`, `m10_episodes`, `m11_episodes`; `checks/m9_oracle_common.py`) that remembers which condition instances were reported, so (a) a condition that started and ended while the worker was not evaluating is reported once (open + resolution), (b) history before the check's first run (the *baseline*) is not paged, (c) a reorg run re-derives every episode and every persisted key (book rows) — README §6.2.
2. **Time base.** `ctx.rpc` offers no block timestamps (and FakeChain has none), so: rules phrased in minutes after an on-chain event (30 min round timeout, 60 min update window) are block distances (`seconds_per_block` 12; missed slots only lengthen real time); the 9 h cadence rule is clock-driven (V2b.7): worker clock − the estimated wall time of the last commit block, estimated at first sight as `clock − (checkpoint + confirmations − block) × 12 s` and persisted, so the age keeps growing while blocks or events stop.
3. **Keys.** Failed rounds: one `failed-round` incident per run of consecutive failed rounds (re-sent per further failed round), not one per round (the recorded 2026-08 outage would otherwise hold 8 pages re-alerting every 6 h). Dissent: per slot (`dissent:<slot>`), warn per dissenting round, escalated at 3 in a row; rounds the slot did not vote in neither count nor reset; tied uncommitted rounds are `split` (warn, unattributed). Reference root of an uncommitted round = unique plurality root.
4. **Chain-derived inputs.** M9 slots (`active_oracle_ids`) and slot addresses (`oracles`); M10 the slot map; M11 the updater = live address of slot 4 (config `updater` overrides), with the slot's address *at each transaction's block* from the stored `OracleReplaced` history. `weak-commit` also pages when distinct slots < the votes the round's own `quorum`/weight event data require.
5. **Never clean:** malformed vote log, unreadable parameters, RPC without a transaction, empty `getEffectiveBalance`, proof API down / at another `referenceBlock` / empty inventory, inbox unavailable or gate pending → raise, skip or keep the open incident (`unknown_keys`), with a health message where the input is the problem.
6. Configuration validated at construction (coordinator ruling): M9/M10 need `settings.views`; M11 needs `updater` or `views`, and `proof_api`.

### Reorg rules of the tables this lane adds (README §6.1 rows)

| State | Where | Handling on a reorg (depth 1 and N) | Test |
|---|---|---|---|
| episode book | `m9_episodes`, `m10_episodes`, `m11_episodes` | reorg run re-derives every episode from the rewound logs: still derived → row kept (never reported twice); vanished with evidence above the ancestor → row deleted, key left to the framework re-find rule ("evidence left the canonical chain"); open row whose key is unknown this run → re-reported | `test_m9::test_reorg_*` (depth 1, depth N at 0/12 confirmations, survivor, crash), `test_m10::test_reorg_*`, `test_m11::test_reorg_*` |
| baseline | `meta check:<id>:baseline` | lowered to the ancestor | same |
| last-commit time estimate | `meta check:m9:last_commit` | recomputed when its block is no longer the latest stored commit | `test_m9::test_reorg_depth_1_removing_the_late_commit_reopens_the_failed_round` (cadence path: `test_no_root_committed_for_9h...`) |
| sender cache | `m11_senders` | rows above the ancestor deleted in the reorg run, re-read | `test_m11::test_reorg_depth_n_moving_the_commit_invalidates_cached_senders_and_proof[2,12]` |
| proof results | `meta check:m11:zero_proof`, `check:m11:full_state` | discarded when their commit is not the stored one or the ancestor is below their evaluated block; recomputed | same + `test_reorg_below_the_evaluated_block_recomputes_persisted_proof_results` |
| crash between detection and handling | framework marker + book in one run transaction | replayed at start | `test_*::test_reorg_crash_between_detection_and_handling` (M9, M10, M11) |

## Completion handoff (≤ 250 words, also sent as the `worker_done` summary)

```text
Task / live dispatch: task_13f583847d79 / ctx_e0d798da26ac (run_96be3e0c7302)
Assignment outcome: completed; every owned row has an outcome, none red.
Base -> resulting commit: 96d1c32 (+ ruled merges 438904e, f92e2b7) -> lane head in worker_done, branch mtabasco/ms-cp2b.
Changed paths: reconcile/checks/{m9_oracle_common,m9_oracle_quorum,m10_oracle_rotation,m11_updater_liveness}.py, checks/__init__.py (1 line), tests/{test_m9,test_m10,test_m11,fixtures_oracle}.py, config.example.json (checks.m9/m10/m11), workers/cp2b/**. No deletions, no frozen file.
Verification: V2x.1 m9 36 / m10 15 / m11 33 passed; V2x.2 2/1/1; V2x.3 2/1/2; V2x.4 1/1/1 (M9/M10 0 RPC per sweep, M11 eth_call <= inventory, 350); V2b.7 1; V2x.6 324 passed, ruff clean. LIVE: 2026-08 outage replay (nodies archive), 3 recent rounds silent (publicnode), M11 530/530 leaves match at blockNum 26,239,590. NOT RUN: none.
Falsifiers: F9-streak, F9-clock, F10-open, F11-window, F-book-reorg red; restored; porcelain clean.
Log anchor: workers/cp2b/attempts.md#attempt-3.
Findings: (1) M11 proof comparison takes ~285 s for 530 clusters (sequential GETs inside one run) -> CP3; (2) spec M9 absence also fires for a persistently dissenting slot (absent:3 in the replay) -> Marco; (3) public archive RPC limits recorded.
Next owner: Codex QA on the lane head.
```
