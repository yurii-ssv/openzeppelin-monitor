# Worker: CP2e — Legacy SSV (M6, M7)

Task contract: [plan.md §8 CP2e](../../plan.md) · Attempt log: [attempts.md](attempts.md) · QA contract: [qa/QA-CONTRACT.md](../../qa/QA-CONTRACT.md)

- Actual starting worktree/branch/head/status: `/Users/marco/orca/workspaces/ssv-network-monitor/ms-cp2e`, branch `mtabasco/ms-cp2e`, head `cbe0d036752a391b227810d583505d911da3e259`, porcelain 0. The packet's frozen base `96d1c32` is an ancestor; the coordinator's addendum (msg `msg_1e4df50c659c`) makes the current `plan/monitoring-service` head (with the frozen-test patch `bd531b4`, `500abe4`) the base. `96d1c32..cbe0d03` touches only `plans/monitoring-service/{execution-log,state}.md` and `reconcile/tests/{harness,test_framework,test_worker}.py`.
- Live authority: Orca task `task_78dba27a67d8`, dispatch `ctx_146d942c81b2` (injected preamble).
- Requested/effective model and effort: Claude Opus high (packet); effective model id `claude-opus-5-5`.
- Allowed writes: `reconcile/checks/m6_*.py`, `m7_*.py`, `reconcile/tests/test_m6.py`, `test_m7.py`, `reconcile/tests/fixtures_legacy.py`, `checks.m6`/`checks.m7` in `reconcile/config.example.json`, one line in `reconcile/checks/__init__.py`, `plans/monitoring-service/workers/cp2e/**`, the CP2e status line in `plan.md`. Commits on the lane branch only; no push; mainnet read-only; alerts record mode only.
- Observable completion and owned verification IDs: A7; V2x.1–V2x.6 (§7 "CP2 lanes", V2x.4 `rpc_budget` applies) and V2e.7 (legacy inventory from API `migrated=false` agrees with the log-derived set at one block). Optional subgraph cross-check: needs `SSV_SUBGRAPH_API_KEY` — **DEFERRED** (absent on this host; owner Marco).
- Dependencies and sources: plan §1, §2 (D2, D6, D8, D9), §6; `reconcile/checks/README.md` (all of it, esp. §5.1, §6.1, §6.2); spec M6, M7, §4, §5; `ssv-network-triage/evidences/#91046/{ssv-solvency-snapshot,sum-ssv-cluster-balances,ssv-liquidation-timeline,ssv-phantom-projection}.mjs`; pinned source `ssv-network-triage/ssv-network@f7ac487` (`SSVViews.sol`, `ClusterLib.sol`, `SSVStaking.sol`, `SSVStorage.sol`).
- Commit capability checked: yes (`git config user.name` = Marco Tabasco; WIP commits `9cf8c96`, `3fc207d`).

## Design (for QA)

| Item | Decision | Why |
|---|---|---|
| M6 inventory | `ctx.inventory(block)` clusters with storage version `SSV`; evaluated if the tuple validates and `active` | D8/D9; version from storage, not the API flag |
| M6 reads | per active legacy cluster `getBalanceSSV`, `isLiquidatableSSV`, `getBurnRateSSV` (one batch; on a JSON-RPC error, one by one to isolate the failing cluster) | chain decides liquidatability; ≤ 3 eth_call per cluster |
| M6 warn | liquidatable for more than `warn_after_window_bps` (5000) × live `liquidation_threshold_period_ssv` blocks | **coordinator ruling Q1 A**: half the window from the chain (50,120 blocks ≈ 6.96 d at the dated value) |
| M6 "since" | first block observed liquidatable (persisted); without an earlier clear observation back-dated `ceil((max(minColl, period×burn) − balance)/burn)` blocks, clamped to after the last clear sweep, never later than the observation; flagged `estimated_start` in the evidence | Q1 A conditions; first sweep after start has no baseline |
| M6 page | `getBalanceSSV == 0` while active **and validatorCount > 0** | **coordinator ruling Q2 A**; vc=0 at balance 0 listed in the weekly digest (count + ids), a 0→>0 validator change pages at its block |
| M6 schedule | `Every(3600)` full sweep + `OnEvents` (ValidatorAdded/Removed, ClusterLiquidated/Reactivated/MigratedToETH) scoped to the event clusters | page/resolve at the event block without a sweep per event |
| M6 incidents | one per cluster; warn escalates to page under the same id; resolves on liquidation, migration, or no condition | §6 lifecycle |
| M6 digest | weekly (`digest_interval_s` 604800, clock) lines in the framework digest: count, validators, SSV/yr burn, liquidatable/warn/zero/unknown/liquidated, inert-at-zero sample | spec "weekly digest" |
| M7 ledger | proxy SSV (token from `StorageData.token`) − (cSSV supply + Σ UnstakeRequested − Σ UnstakedWithdrawn + Σ getBalanceSSV of active legacy clusters + Σ getOperatorEarningsSSV(1..`lastOperatorId` from storage) + getNetworkEarningsSSV) | spec M7 formula; evidence script `ssv-solvency-snapshot.mjs` |
| M7 unstakes | proxy events declared by M7 and stored by the worker scan (reorg-rewound); range before `start_block` read once as a prefix sum | no table of our own to rewind |
| M7 alerts | `surplus-floor` page if < `surplus_floor_wei` (1 SSV), resolved above; `surplus` info, revision = surplus wei (baseline at the first evaluation, update on any change) | spec M7 |
| UNKNOWN | invalid tuple, revert/empty view, `lastOperatorId` 0 next to legacy clusters → health message + unchanged re-report; M7 still pages when its upper bound is below the floor; a missing head value (balanceOf, totalSupply, network earnings) or an API failure raises (check-error, retried) | contract (4) |
| Required config | `views` and `inventory.api_url` (M6, M7), `checks.m7.cssv`, `checks.m7.unstake_history_from` → `SystemExit` at construction when absent | addendum: fail loud |

Reorg rules: M6 `m6_clusters` — on a reorg run, `last_clear_block > A` → NULL, a liquidatable start first observed above A → dropped; the reorg run is a full sweep over the inventory **and** every open M6 key and persisted row (not-in-inventory keys are classified from storage: migrated/absent → resolved; still legacy → UNKNOWN re-report). M7 has no table: every run recomputes and reports both keys. Tests: `test_m6_reorg_*` (7), `test_m7_reorg_*` (5): depth 1, depth N beyond confirmations 3, crash between `check_reorg` and the run, surviving condition, deleted unstake log.

| Step | Status | Latest evidence | Exact next action |
|---|---|---|---|
| S1 read inputs, rulings | DONE | [attempt 1](attempts.md#attempt-1) | — |
| S2 implement M6, M7, fixtures, tests | DONE | [attempt 2](attempts.md#attempt-2) | — |
| S3 LIVE V2e.7 + LIVE M6/M7 evaluation | see attempts | [attempt 3](attempts.md#attempt-3) | — |
| S4 V-rows, falsifiers, self-review | see attempts | [attempt 4](attempts.md#attempt-4) | — |

## Completion handoff (≤ 250 words, also sent as the `worker_done` summary)

(filled at completion)
