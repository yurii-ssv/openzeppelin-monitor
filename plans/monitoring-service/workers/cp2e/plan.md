# Worker: CP2e — Legacy SSV (M6, M7)

Task contract: [plan.md §8 CP2e](../../plan.md) · Attempt log: [attempts.md](attempts.md) · QA contract: [qa/QA-CONTRACT.md](../../qa/QA-CONTRACT.md)

- Actual starting worktree/branch/head/status: `/Users/marco/orca/workspaces/ssv-network-monitor/ms-cp2e`, branch `mtabasco/ms-cp2e`, head `cbe0d036752a391b227810d583505d911da3e259`, porcelain 0. The packet's frozen base `96d1c32` is an ancestor; the coordinator's addendum (msg `msg_1e4df50c659c`) makes the current `plan/monitoring-service` head (with the frozen-test patch `bd531b4`, `500abe4`) the base. `96d1c32..cbe0d03` touches only `plans/monitoring-service/{execution-log,state}.md` and `reconcile/tests/{harness,test_framework,test_worker}.py`.
- Live authority: Orca task `task_78dba27a67d8`, dispatch `ctx_146d942c81b2` (injected preamble).
- Requested/effective model and effort: Claude Opus high (packet); effective model id `claude-opus-5-5`.
- Allowed writes: `reconcile/checks/m6_*.py`, `m7_*.py`, `reconcile/tests/test_m6.py`, `test_m7.py`, `reconcile/tests/fixtures_legacy.py`, `checks.m6`/`checks.m7` in `reconcile/config.example.json`, one line in `reconcile/checks/__init__.py`, `plans/monitoring-service/workers/cp2e/**`, the CP2e status line in `plan.md`. Commits on the lane branch only; no push; mainnet read-only; alerts record mode only.
- Observable completion and owned verification IDs: A7; V2x.1–V2x.6 (§7 "CP2 lanes", V2x.4 `rpc_budget` applies) and V2e.7 (legacy inventory from API `migrated=false` agrees with the log-derived set at one block). Optional subgraph cross-check: needs `SSV_SUBGRAPH_API_KEY` — **DEFERRED** (absent on this host; owner Marco).
- Dependencies and sources: plan §1, §2 (D2, D6, D8, D9), §6; `reconcile/checks/README.md` (all of it, esp. §5.1, §6.1, §6.2); spec M6, M7, §4, §5; `ssv-network-triage/evidences/#91046/{ssv-solvency-snapshot,sum-ssv-cluster-balances,ssv-liquidation-timeline,ssv-phantom-projection}.mjs`; pinned source `ssv-network-triage/ssv-network@f7ac487` (`SSVViews.sol`, `ClusterLib.sol`, `SSVStaking.sol`, `SSVStorage.sol`).
- Commit capability checked: yes (`git config user.name` = Marco Tabasco; WIP commits `9cf8c96`, `3fc207d`).
- M7 completeness guard: table `m7_legacy` (ids only; not rewound on a reorg — a legacy cluster cannot be created after the activation; every run re-checks each id in storage).

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
| S3 LIVE V2e.7 + LIVE M6/M7 evaluation | DONE (V2e.7 log side NOT RUN: endpoint limits) | [attempt 3](attempts.md#attempt-3) | CP3/Marco: rerun `live_legacy.py` with an archive getLogs endpoint |
| S4 V-rows, falsifiers, self-review | DONE | [attempt 4](attempts.md#attempt-4) | Codex QA |

## Completion handoff (≤ 250 words, also sent as the `worker_done` summary)

```text
Task / live dispatch: task_78dba27a67d8 / ctx_146d942c81b2 (CP2e, Claude Opus high)
Assignment outcome: M6 and M7 implemented on the CP1B framework; unit rows green; V2e.7 partly LIVE.
Base -> resulting commit: cbe0d03 -> lane head in the CP2e status line of plan.md (code tested at b4f6c9e)
Actual changed paths: reconcile/checks/{m6_legacy_coverage,m7_ssv_solvency}.py, checks/__init__.py (1 line),
  config.example.json (checks.m6, checks.m7), tests/{test_m6,test_m7,fixtures_legacy}.py, workers/cp2e/**,
  plan.md CP2e status line; merge of plan/monitoring-service (docs). No deletions; venv untracked/ignored.
Verification: V2x.1 31+26 passed; V2x.2 3; V2x.3 3; V2x.4 2 (M6 <=3x350, M7 <=3+2352+350); reorg 12;
  V2x.6 297 passed, ruff clean; V2e.7 API side PASS (1026/1026 migrated=false legacy in storage, 0
  contradictions), log side NOT RUN (public getLogs limits/429, exact errors in attempt 3).
Falsifiers: F6a-c, F7a-d red then green; porcelain 0 after each.
Log anchor: workers/cp2e/attempts.md#attempt-3, #attempt-4; evidence/live-run{7,9}*.json.
New finding: LIVE M7 surplus = exactly 1 SSV at 26,242,622; 422 inert zero-balance legacy clusters (Q2 A).
Pending human input: ask msg_efdf8c0c88e7 (log source for V2e.7); subgraph cross-check DEFERRED (no key).
Next owner and exact action: Codex QA on the lane head.
Preservation: all committed; scratch LIVE logs local only.
```

<a id="repair-1"></a>
## Repair 1 — QA attempt 1 findings F1–F3 (fresh implementer, Claude Opus high)

- Live authority: Orca task `task_9a6318071980`, dispatch `ctx_b4ba8297cc36`. Model `claude-opus-5-5`, effort high.
- Frozen start head: `b6f90b104eafcfcd204a79bdd40252bf7f407221` (QA evidence commit on top of `c02557a`), porcelain 0. Read in full: `qa/cp2e-attempt-1.md` (F1–F3, §2, ledger, Appendix A probes), `qa/DEFECT-CLASSES.md` from `plan/monitoring-service`. Rulings Q1 A, Q2 A unchanged. Owner scope note applied: targeted repair, own regressions + one seeded sweep, no mutation campaign. Not merged with `plan/monitoring-service` (optional; not needed: no shared file changed).
- Changed paths (`git diff b6f90b1 --name-only`): `reconcile/checks/{m6_legacy_coverage,m7_ssv_solvency}.py`, `reconcile/tests/{test_m6,test_m7,fixtures_legacy}.py`, `workers/cp2e/**`, the CP2e status line in `plan.md`. No CP1B frozen file, no `qa/**`, no `execution-log.md`, no config key added.

### Design changes (amend the design table above)

| Item | Repair-1 decision | Why |
|---|---|---|
| Completeness (F1) | `inventory_gap(ctx, inv)` on every M6 full sweep (event-scoped runs excluded) and every M7 run: (1) chain — Σ validatorCount of the active, storage-validated legacy clusters must equal the SSV-side `daoValidatorCount` (StorageProtocol word 0, bits 32..63; maintained by `ProtocolLib.updateDAOSSV` on liquidation, migration and legacy validator removal at the pin); (2) API — the listing (API rows ∪ scanned logs) must hold the API's own `pagination.total` (one GET `perPage=1&page=1` through the worker's InventoryCache transport); a missing/non-integer total or a failed GET is a gap (unconfirmed, fail closed) | absence from the API is not evidence and a first run (or a new database) has no persisted set; (1) covers every cluster M6 can warn/page on (both need validators), (2) covers the inert legacy clusters whose balance is still an M7 claim. LIVE: gap none at 26,247,163 (346 = 346; 1,908 = 1,908), empty and one-row-short controls report a gap |
| Gap → behaviour | M6: same `m6-unknown` health message ("Legacy inventory incomplete: …"), summary `inventory_gap`, weekly digest line says "UNKNOWN … these counts are a lower bound"; listed clusters still evaluated, nothing resolved on absence. M7: `unknown["inventory"]` → the computed surplus is an upper bound: page if below the floor, else UNKNOWN health, open keys re-reported unchanged, **no baseline** | contract (4) "UNKNOWN, never clean" |
| Back-dated start (F2) | `since = block − max(0, ceil(D / burn) − 1)` with D = threshold − balance (strict `balance < threshold`), clamped after the last clear observation, never after the observation | the first liquidatable block, not the last clear one; exactly W stays silent (`>`) |
| M6 reorg restore (state table row c, found by the sweep design) | new table `m6_obs` (row snapshot per evaluation and a tombstone per deletion, kept `OBS_HISTORY_BLOCKS` = 7,200 plus the newest older one); a reorg run restores every row touched above A to its latest observation ≤ A (tombstone → row deleted); no observation ≤ A → the old rule (forget values derived above A) | a clear observation or a liquidation on a deleted block overwrote the canonical start, and the rewind re-estimated it from the balance (dated before the canonical clear sweep: a false warn) |

### State table (DEFECT-CLASSES: every persisted table/marker and every off-chain input)

Columns: (a) empty, (b) error, (c) reorg depth 1 / N beyond confirmations incl. an already resolved key, (d) restart between detection and handling, (e) same-block ordering, (f) time-window anchoring, (g) partial. Test names drop the `test_m6_` / `test_m7_` prefix; "sweep" = `test_m{6,7}_adversarial_sweep` (seeded, 48 + 56 cases). **R1** marks a row that was wrong at `b6f90b1` (regression red there, green now).

| State / input | (a) empty | (b) error | (c) reorg 1 / N, resolved key | (d) restart | (e) same block | (f) window | (g) partial |
|---|---|---|---|---|---|---|---|
| SSV API listing (inventory, both checks) | **R1** UNKNOWN, never "no clusters": `first_sweep_with_an_empty_api_listing_is_unknown_not_clean`, `first_run_with_an_empty_api_listing_is_unknown_not_a_baseline`, `incomplete_inventory_still_pages_when_the_upper_bound_is_below_the_floor`, `digest_of_an_incomplete_inventory_says_unknown_not_zero`; sweep fault `empty` | fetch raises → check-error, run retried, nothing resolved: `api_failure_is_a_check_error_not_an_empty_inventory`; sweep fault `error` | reorg run re-lists; open keys not listed are classified from storage: `reorg_run_reports_every_open_key_even_without_inventory_rows`; sweep faults in the reorg run | **R1** a new database at restart has no persisted set: `restart_on_a_lost_database_with_an_empty_api_is_unknown` (M6, M7) | n/a (snapshot at the run block) | API rows cached ≤ `api_refresh_s`; total read fresh every full run; a cluster created after the cached page is in the scanned logs (union) — residual: created and not yet scanned → one UNKNOWN run, fail closed | **R1** short of `pagination.total`: `listing_short_of_its_total_or_without_one_is_unknown[short]` (M6, M7); a cluster missing from the API **and** its total: `cluster_missing_from_the_api_and_its_total_is_found_by_the_dao_validator_count` (M6, M7); sweep fault `short` |
| SSV API `pagination.total` (new) | absent → gap: `…[no_total]` (M6, M7); sweep fault `no_total` | GET fails → gap (UNKNOWN, not check-error): `unreadable_api_total_is_unknown_not_clean`, `unreadable_api_total_is_unknown_not_a_change` | read at every reorg run (sweep) | stateless | n/a | fresh per run | non-integer/negative → gap (code path `api_total`) |
| Storage: `daoValidatorCount` (new), cluster slots, token, `lastOperatorId` | dao 0 vs listed → gap; cluster slots empty → "no longer exists" (positive fact); token 0 → raise; `lastOperatorId` 0 with legacy clusters → UNKNOWN: `operator_count_zero_next_to_legacy_clusters_is_unknown` | transport error → raise, retried (framework) | read at the run block of the new chain (sweep) | stateless | n/a | pinned to the run block | one cluster slot mismatching → that cluster UNKNOWN: `unvalidated_tuple_is_unknown_and_keeps_the_incident_open` |
| Views `eth_call` (M6 balance/liquidatable/burn; M7 earnings, balanceOf, totalSupply) | empty answer → UNKNOWN (cluster) / raise (head value): `reverting_or_empty_view_is_unknown_not_clean[empty]`, `missing_head_value_is_a_check_error[empty]` | revert → UNKNOWN per cluster/operator: `…[revert]`, `reverting_operator_earnings_is_unknown`, `unpriced_legacy_cluster_is_unknown_not_clean`; sweep faults `view_revert`, `op_revert` | **R1-adjacent** an UNKNOWN reorg run keeps both M7 keys, no second baseline: `reorg_run_whose_re_evaluation_is_unknown_keeps_both_keys_without_a_second_baseline` (kills QA QF7b) | n/a | n/a | pinned | batch error → per-call fallback isolates the failing cluster (`call_each`) |
| Params (`liquidation_threshold_period_ssv`, `minimum_liquidation_collateral_ssv`) | getter empty/revert → run skipped (framework `ParamsUnavailable`): `params_unavailable_skips_the_run` | same | re-read after every reorg (framework `prepare`) | re-read at start | a change takes effect at its block: `thresholds_from_chain_period_change_moves_the_warn` | warn = bps × live period | n/a |
| Stored logs (framework scan): cluster events for M6 event runs, unstake events for M7 | no events → interval sweeps only | n/a (framework) | rewound with the reorg: `reorg_deleting_an_unstake_request_rewinds_the_sum`; sweep story `unstake_orphaned` | cursor advances only with a committed run | M7 sums order-independent: `same_block_request_and_withdrawal`; M6 events evaluated at the run block (end-of-block state; an intermediate state lasts zero blocks): `events_of_one_scan_step_are_evaluated_at_the_run_block` | event run at once, no interval wait: `event_run_resolves_a_liquidation_at_once_…`; sweep (`resolution_orphaned` asserts the resolution in the event step) | n/a |
| eth_getLogs prefix (unstakes before `start_block`, `check:m7:unstake_prefix`) | **NOTE (residual)**: an endpoint that answers `[]` for a range holding logs is indistinguishable from "no unstakes" — no on-chain total exists (SSVStorageStaking keeps per-user arrays only); the same trust as the worker's own scan. Mitigation is the endpoint (QA's V2e.7 log source errors on caps instead of truncating). Owner: CP3 (endpoint choice) | error → run fails, nothing persisted, retried next step: `unstake_prefix_read_failure_is_retried_not_zero` | prefix covers blocks below `start_block` only (never rewound by the worker; a reorg that deep makes the worker SystemExit) | persisted once, reused: `unstake_history_before_start_block_is_read_once` | sums | fixed range | `rpc.get_logs` raises on a capped/oversized window (framework) |
| `m6_clusters` (start, last clear) | first sweep: back-dated start, flagged `estimated_start`: `liquidatable_longer_than_half_window_warns`, **R1** `backdated_start_of_a_cluster_that_just_crossed_is_now`, `benign_liquidatable_exactly_half_window_stays_silent` | n/a | **R1** restored to its canonical state at the ancestor: `reorg_restores_the_canonical_start_a_deleted_clear_observation_overwrote`, `reorg_orphaning_a_liquidation_restores_the_observed_start`; also `reorg_drops_a_liquidatable_start_observed_on_deleted_blocks`, `reorg_forgets_a_clear_observation_on_deleted_blocks`; sweep window invariant | persisted start survives: `restart_does_not_duplicate_the_open_incident`, `restart_with_a_prior_liquidatable_row_keeps_counting`; crash after `check_reorg`: rewind runs in the (rolled-back, retried) run transaction: `reorg_crash_between_detection_and_the_check_run` | n/a | anchored at the run block; clamp after the last canonical clear: `observed_start_is_not_back_dated_before_the_last_clear_sweep`; exactly W silent (`>`) | n/a |
| `m6_obs` (new: row history, tombstones) | no history (rows from before repair 1) → old forget rule | n/a | restore source; deleted above A first; tombstone keeps a deletion restorable (falsifiers R3a, R3b) | in the run transaction | n/a | pruned at `block − 7,200`, newest older snapshot kept | n/a |
| M6 open incidents (key = cluster id) | — | — | enumerated from open keys, persisted rows and `reorg_keys`: `reorg_run_reports_an_open_key_without_a_persisted_row` (kills QA QF6d); depth 1/N: `reorg_depth_1_…`, `reorg_depth_n_…`; survives: `reorg_where_the_condition_survives_…`; **already resolved key** whose liquidation is orphaned re-opens: `reorg_orphaning_the_liquidation_that_resolved_the_page_reopens_it`; sweep stories `resolution_orphaned`, `clear_orphaned` | no duplicate: `restart_does_not_duplicate_the_open_incident`; sweep restart points `detected`, `before` | — | — | UNKNOWN re-report keeps it (sweep faults) |
| `check:m6:summary`, `digest_at` (weekly digest) | **R1** incomplete inventory says UNKNOWN, not "0 active": `digest_of_an_incomplete_inventory_says_unknown_not_zero` | run failed → previous summary kept | rewritten by the reorg run (full sweep) | persisted | n/a | wall clock (`digest_interval_s`), not blocks: `weekly_digest_counts_clusters_validators_and_burn` | counts are a lower bound when gapped |
| `m7_legacy` (ids) | first run: nothing to compare → the F1 guard above | n/a | ids only, not rewound (a legacy cluster cannot be created after the activation; registration on a legacy cluster reverts `IncorrectClusterVersion` at the pin); re-checked in storage every run | persisted | n/a | n/a | id missing from the listing while legacy in storage → UNKNOWN: `legacy_cluster_missing_from_the_inventory_is_unknown_not_a_smaller_liability` |
| M7 incidents `surplus-floor`, `surplus` (revision = surplus wei) | first run: baseline only with a complete ledger (**R1** above) | run failed → kept | depth 1/N: `reorg_depth_1_…`, `reorg_depth_n_…`; survives: `reorg_with_the_leak_on_the_new_chain_keeps_the_page`; **already resolved key** whose repair is orphaned re-pages: `reorg_orphaning_the_repair_that_resolved_the_page_reopens_it`; sweep stories `leak_*`, `repair_orphaned`, `unstake_orphaned` | `restart_does_not_duplicate_the_open_incidents`; `reorg_crash_between_detection_and_the_check_run`; sweep restart points | — | daily by the clock (`Every(interval_s)`); the ledger is read at the run's checkpoint (sweep asserts evidence block = run block) | UNKNOWN → upper bound pages, else unchanged re-report: `unknown_still_pages_…`, `unknown_keeps_an_open_page` |
| Inbox, proof API, Blockscout, block timestamps | not consumed by M6/M7 (n/a) | | | | | | |

Other residual NOTE (spec interpretation, owner coordinator/Marco): a legacy cluster that drains to zero and is liquidated between two M6 sweeps (`sweep_interval_s` 3,600) is not paged — no event marks the drain, nothing is left to act on, and the unbacked credit it produced shows in M7 (info on any surplus change, page below 1 SSV).

### Seeded adversarial sweep

`test_m6_adversarial_sweep` (48 cases) and `test_m7_adversarial_sweep` (56 cases), seed `20261022`: every story × fault, twice, with confirmations {0, 3}, reorg depth {1, N > confirmations} and restart point {none, after `check_reorg`, before the run} drawn from the seed. M6 stories: zero balance on the deleted chain only / on both chains / canonical page resolved by a liquidation on the deleted chain / canonical page "cleared" on the deleted chain; M7 stories: leak deleted / leak on both / repair deleted / unstake request deleted, each after a request + withdrawal between two daily runs. Faults in the reorg run: none, empty API, short API, API error, no total, reverting cluster view (M7 also a reverting operator earnings view); then healed and stable steps. Asserted after every step: no resolve without positive proof; no open incident without supporting evidence (and, unfaulted, exactly the incidents the canonical chain supports); every incident id opens once (no duplicated page, never a second baseline); no surviving evidence left without its incident at the end; health on every untrusted input; no window anchored on deleted or stale data (M6 `last_clear_block` ∈ canonical clear observations, start after the last canonical clear and not after the first canonical zero observation; M7 evidence block = run block); no event lost between runs (M6 liquidation resolved in its event step; M7 request + withdrawal between runs priced exactly). On the frozen code (`b6f90b1` checks): M6 28/48 and M7 8/56 fail; now 104/104 pass.

### Repair-1 completion handoff (≤ 250 words, also the `worker_done` summary)

```text
Task / live dispatch: task_9a6318071980 / ctx_b4ba8297cc36 (CP2e repair 1, Claude Opus high)
Assignment outcome: F1-F3 repaired; fresh QA required.
Base -> resulting commit: b6f90b1 -> lane head in the CP2e status line (code tested at e6a67d9)
Changed paths: checks/{m6_legacy_coverage,m7_ssv_solvency}.py, tests/{test_m6,test_m7,fixtures_legacy}.py,
  workers/cp2e/** (plan.md Repair 1, attempts.md #attempt-5, live_completeness.py, evidence/), plan.md status.
F1: inventory_gap (daoValidatorCount vs listed legacy validators; pagination.total vs listing) -> UNKNOWN,
  M7 no baseline; F2: first liquidatable block = block - (ceil(D/burn) - 1); F3: state table + seeded sweep
  (104 cases), QF6d/QF7b killed; extra: M6 reorg restores rows from m6_obs history (R1 row c).
Verification: V2x.1 92+92 (files incl. sweeps); V2x.2 3; V2x.3 5; V2x.4 2; reorg 18; sweep 104;
  V2x.6 424 passed, ruff clean; 18 regressions red on frozen code; QA probes 4/4 green; falsifiers
  F6a-c, F7a-d, QF6d, QF7b, R1a-e, R2a, R3a-c red -> green, porcelain 0.
LIVE: completeness guard at 26,247,163: 346 = 346, 1,908 = 1,908, controls gap (evidence sha256 13b5fbfc…).
NOT RUN: V2e.7 / LIVE M6/M7 re-run (QA attempt 1 measured both at 26,246,821); subgraph DEFERRED.
Notes: unstake-prefix empty-logs residual; drain-then-liquidate between sweeps (spec owner).
Next: fresh Opus QA on the lane head.
```

<a id="repair-2"></a>
## Repair 2 — QA attempt 2 finding F1 (fresh implementer, Claude Opus high)

- Live authority: Orca task `task_c706fd249b26`, dispatch `ctx_3a5d60dc1b12`. Model `claude-opus-5-5`, effort high.
- Frozen start head: `8e07b0a814216dbbcf9e43bc5dfef1049c5d4107` (QA evidence commit on top of `6fafac6`), porcelain 0; venv `reconcile/.venv` present (Python 3.12.15). Read in full: `qa/cp2e-attempt-2.md` (F1, N1, N2, §2, ledger, Appendix A). Round policy: next QA capped, D15. Owner scope note applied: one narrow fix, its regressions, no mutation campaign. N1/N2 (NOTEs) not addressed. Not merged with `plan/monitoring-service` (allowed, not needed: the CP1B frozen files are identical there, QA §preflight; no shared file changed).
- Changed paths (`git diff 8e07b0a --name-only`): `reconcile/checks/{m6_legacy_coverage,m7_ssv_solvency}.py` (guard + docstrings), `reconcile/tests/{test_m6,test_m7,fixtures_legacy}.py`, `workers/cp2e/**` (this section, attempts.md #attempt-6, `live_completeness.py` control, `evidence/repair2-*`), the CP2e status line in `plan.md`. No CP1B frozen file, no `qa/**`, no `execution-log.md`, no config key.

### Design change (amends the repair-1 "Completeness (F1)" row)

| Item | Repair-2 decision | Why |
|---|---|---|
| Empty API answer | `inventory_gap` adds a gap whenever the API answer lists **no cluster** (`from_api == 0`: zero API rows), whatever `pagination.total` (0, the true count, absent) and `daoValidatorCount` say. Behaviour on the gap unchanged: M6 `m6-unknown` health + summary/digest "UNKNOWN … lower bound"; M7 `unknown["inventory"]` → health, no baseline, open keys re-reported unchanged, upper-bound page logic unchanged | **Coordinator ruling** (repair-2 brief): an empty listing is always UNKNOWN; next to a proxy with legacy storage it is a degenerate answer. A self-consistent empty page (total 0) with no validators left on legacy clusters (dao 0, the natural end state of a surface that only shrinks) passed both repair-1 legs |
| Scope of "empty" | zero **API rows**, not zero inventory entries: a listing whose only clusters come from the scanned logs while the API lists none is the same degenerate answer (every inert legacy cluster without a recent event is missing). Strictly stronger than "zero clusters" (`len(inv.clusters) == 0`); falsifier R4b shows the difference is tested | the ruling's reason applies unchanged; fail closed. On mainnet the API lists 1,908 clusters (ETH clusters included), so no false UNKNOWN |

### State table — repair-2 re-pass (every persisted table/marker and every off-chain input)

Same columns as the repair-1 table (a) empty, (b) error, (c) reorg 1 / N incl. an already resolved key, (d) restart between detection and handling, (e) same-block ordering, (f) window anchoring, (g) partial. Cells unchanged since repair 1 say "as R1" (the repair-1 table above, re-run green at this head). **R2** marks a cell that was wrong at `6fafac6` (regression red there, green now).

| State / input | (a) empty | (b) error | (c) reorg, resolved key | (d) restart | (e) same block | (f) window | (g) partial |
|---|---|---|---|---|---|---|---|
| SSV API listing | **R2** self-consistent empty (total 0) next to inert legacy balances only (dao 0) → UNKNOWN, no baseline: `test_m7_q10_consistent_empty_api_with_inert_legacy_balance` (QA Q10 port), `test_m6_consistent_empty_api_with_only_inert_legacy_clusters_is_unknown` (digest says UNKNOWN), **R2** `test_m7_empty_api_listing_is_unknown_whatever_its_total[first-0]`; totals 1 / none at first run: as R1 (green on both) | as R1 (check-error, retried) | `…whatever_its_total[reorg-{0,1,None}]`: the reorg run meets an empty page → UNKNOWN, revision unchanged, nothing resolved; sweep fault `empty_total0` in both sweeps | **R2** `…whatever_its_total[lost_database-0]` (new data dir + consistent empty page); `after_baseline-*`: the `m7_legacy` id check (as R1) | n/a | rows cached ≤ `api_refresh_s`, total fresh per full run (as R1) | **R2** API empty while the scanned logs list a legacy cluster (dao 0 after its last removal) → gap: `test_m7_empty_api_next_to_a_cluster_from_the_logs_is_unknown`; short page with the true total, duplicates (QA Q2/Q13): as R1. **Residual (NOTE, owner coordinator):** a *non-empty* partial page whose `pagination.total` agrees with it, missing only inert legacy clusters, with dao matching, at a first run or on a new database — no on-chain count of legacy clusters exists (only validators), so neither leg can see it; the ruling scopes repair 2 to the empty answer |
| SSV API `pagination.total` | absent → gap (as R1); **R2** 0 no longer makes an empty page complete | as R1 | read at every reorg run | stateless | n/a | fresh per run | non-integer/negative → gap (as R1) |
| Storage `daoValidatorCount` | **R2** dao 0 no longer vouches for an empty page; dao ≠ listed → gap (as R1) | read error → raise, no clean summary (QA Q6, as R1) | read at the run block | stateless | n/a | pinned | as R1 |
| Storage cluster slots, token, `lastOperatorId` | as R1 | as R1 | as R1 | stateless | n/a | pinned | as R1 |
| Views `eth_call` | as R1 | as R1 | as R1 | n/a | n/a | pinned | as R1 |
| Params | as R1 | as R1 | as R1 | as R1 | as R1 | as R1 | n/a |
| Stored logs (framework scan) | as R1 | n/a | as R1 | as R1 | as R1 | as R1 | n/a |
| eth_getLogs prefix (`check:m7:unstake_prefix`) | **NOTE N1 (QA, not in scope):** a lossy `[]` prefix can drive `outstanding_unstake` negative (Q11 still fails here, as on `6fafac6`); unreachable with the shipped config (`start_block == unstake_history_from`) | as R1 | as R1 | as R1 | as R1 | fixed range | as R1 |
| `m6_clusters`, `m6_obs` | as R1 (the gap changes no row write) | n/a | as R1 | as R1 | n/a | as R1 | n/a |
| M6 open incidents | a gap resolves nothing: `…consistent_empty…` asserts no page, sweep `empty_total0` asserts no resolve without proof | — | as R1 | as R1 | — | — | as R1 |
| `check:m6:summary`, digest | **R2** consistent empty: `inventory_gap` set, digest "UNKNOWN: the legacy inventory is incomplete" (M6 twin above) | as R1 | as R1 | as R1 | n/a | as R1 | as R1 |
| `m7_legacy` (ids) | first run / new database: empty table, so only the guard protects it — now an empty page is a gap (**R2** rows above) | n/a | as R1 | as R1 | n/a | n/a | as R1 |
| M7 incidents `surplus-floor`, `surplus` | **R2** no baseline from an empty page (Q10); after recovery one baseline at the true surplus and the page (Q10 port second half) | as R1 | `…whatever_its_total[reorg-*]`, sweep `empty_total0` | `[lost_database-0]` | — | as R1 | upper-bound page unchanged (`incomplete_inventory_still_pages_…`, green) |
| Inbox, proof API, Blockscout, block timestamps | not consumed by M6/M7 (n/a) | | | | | | |

### Seeded adversarial sweep (extended)

Both committed sweeps (seed `20261022`) gain the fault `empty_total0` (fixture `LegacyWorld.api_fault`: empty page with `pagination.total` 0): `test_m6_adversarial_sweep` 56 cases, `test_m7_adversarial_sweep` 64 cases; in the M7 `empty_total0` cases the legacy cluster is inert (count 0), so `daoValidatorCount` 0 agrees with the empty page — exactly F1's precondition — and the invariants (no resolve without positive proof, no open incident without supporting evidence, no dropped page for surviving evidence, no window anchored on stale data, no event lost between sweeps, never a second baseline) are asserted after every step. Honest limit: the sweep applies faults at the reorg run, after a baseline, where the `m7_legacy` id check already made the empty page UNKNOWN, so these cases are green on `6fafac6` too; F1's first-run / new-database shape is pinned by the parametrized class test `test_m7_empty_api_listing_is_unknown_whatever_its_total` (12 cases, 2 red on `6fafac6`).

### Repair-2 completion handoff (≤ 250 words, also the `worker_done` summary)

```text
Task / live dispatch: task_c706fd249b26 / ctx_3a5d60dc1b12 (CP2e repair 2, Claude Opus high)
Assignment outcome: QA cp2e-2 F1 repaired (one narrow fix); fresh capped QA (D15) required.
Base -> resulting commit: 8e07b0a -> lane head in the CP2e status line (code tested at c37afc9)
Changed paths: checks/{m6_legacy_coverage,m7_ssv_solvency}.py (inventory_gap + docstrings),
  tests/{test_m6,test_m7,fixtures_legacy}.py, workers/cp2e/** (plan.md Repair 2, attempts.md #attempt-6,
  live_completeness.py control, evidence/repair2-*, live-completeness-26247316.json), plan.md status.
F1: an SSV API answer with zero clusters is an inventory gap whatever pagination.total / daoValidatorCount
  say (coordinator ruling; zero API rows, so an API-empty + logs-only listing is a gap too) -> UNKNOWN,
  no M7 baseline, upper-bound page unchanged.
Regressions: Q10 port + M6 twin + 12-case empty-class test + logs-next-to-empty: 5 red on 6fafac6, green;
  QA Q10 verbatim green; sweeps + fault empty_total0 (M6 56, M7 64 cases).
Verification: V2x.1 101+114; V2x.2 3; V2x.3 5; V2x.4 2; reorg 21; sweep 120; V2x.6 455 passed, ruff clean;
  11 falsifiers red->green (R4a/R4b new), porcelain 0.
LIVE: guard at 26,247,316: 346 = 346, 1,908 = 1,908, gap none; 3 controls gap (incl. total 0).
NOT RUN: V2e.7 / LIVE ledger (long rows; carried from QA attempt 1), older falsifiers listed in #attempt-6;
  subgraph DEFERRED. N1/N2 not addressed (NOTEs). Residual NOTE: a non-empty partial page whose total agrees,
  missing only inert legacy clusters, at a first run (no on-chain legacy-cluster count).
Next: fresh Opus QA (capped, D15) on the lane head.
```

<a id="repair-3"></a>
## Repair 3 — QA attempt 3 finding F1 (fresh implementer, Claude Opus high)

- Live authority: Orca task `task_517a6c9b022b`, dispatch `ctx_116bf746a1a4`. Model `claude-opus-5-5`, effort high.
- Frozen start head: `00235da1a8dbd47e1b7bbd6629c192bc74617a18` (QA evidence commit on top of `31b7a77`), porcelain 0; venv `reconcile/.venv` present. Read in full: `qa/cp2e-attempt-3.md` (F1, §2, ledger, N1–N4, Appendix A; scratch `probes3/test_qa3.py`). Round policy: three failed QA rounds, the next QA is capped and uses D15. Owner scope note: one narrow fix, its regressions, no mutation campaign. N1–N4 (NOTEs) not addressed (N3 is closed as a side effect: M6 shares the guard). Not merged with `plan/monitoring-service` (allowed, not needed: no shared file changed).
- Changed paths (`git diff 00235da --name-only`): `reconcile/checks/{m6_legacy_coverage,m7_ssv_solvency}.py` (one comparison + docstrings), `reconcile/tests/{test_m6,test_m7}.py`, `workers/cp2e/**` (this section, attempts.md #attempt-7, `live_completeness.py` F1-shaped control, `evidence/repair3-*`, the LIVE json), the CP2e status line in `plan.md`. No CP1B frozen file, no `qa/**`, no `execution-log.md`, no config key, no fixture change.

### Design change (amends the repair-1 "Completeness (F1)" row, leg 2)

| Item | Repair-3 decision | Why |
|---|---|---|
| Leg (2) of `inventory_gap` | compares **only the distinct cluster ids the API pages listed** (`from_api`: inventory entries with `"api"` in `sources`, so a row repeated across pages counts once) with `pagination.total`; `from_api < total` → gap. Clusters only the scanned logs know are no longer counted. Missing / non-integer / negative / unreadable total → gap (unchanged); leg (1) `daoValidatorCount` and the repair-2 empty-listing rule unchanged | **Coordinator ruling** (repair-3 brief). `pagination.total` counts API rows only; `len(inv.clusters)` (API ∪ logs) let every log-only cluster (registered after the API's index point, or during an indexer stall) offset one dropped row, and an inert legacy row (0 validators, so invisible to leg 1) then missed the M7 first-run baseline (QA cp2e-3 F1; mainnet 42 such clusters, 2,235.6 SSV) |
| Behaviour on the gap | unchanged: M6 `m6-unknown` health + summary/digest "UNKNOWN … lower bound"; M7 `unknown["inventory"]` → health, no baseline, open keys re-reported unchanged, upper-bound page logic unchanged | — |

**Known residual (recorded, owner coordinator / spec; QA N4):** a *self-consistent* partial page — `pagination.total` agrees with the rows it serves — missing only inert legacy clusters, with `daoValidatorCount` matching, at a first run or on a new database, is invisible to both legs: there is no on-chain count of legacy clusters (only of their validators). After a baseline the `m7_legacy` id check catches a listed id that disappears.
**New fail-safe side effect (NOTE, not a false clean):** the listing is cached up to `inventory.api_refresh_s` (600 s) and the total is read fresh at each full run; a cluster the API indexes between the two reads makes `from_api < total` for that run only (UNKNOWN health, nothing resolved, no baseline). Before repair 3 the cluster's log entry masked it. The hourly M6 sweep and the daily M7 run find the cache older than 600 s and refetch in the same run, so the window is the seconds between the two GETs.

### State table — repair-3 re-pass (every persisted table/marker and every off-chain input)

Same columns as the repair-1/2 tables: (a) empty, (b) error, (c) reorg 1 / N incl. an already resolved key, (d) restart between detection and handling, (e) same-block ordering, (f) window anchoring, (g) partial. "as R2" = the repair-2 cell (re-run green at this head). **R3** marks a cell that was wrong at `31b7a77` (regression red there, green now).

| State / input | (a) empty | (b) error | (c) reorg, resolved key | (d) restart | (e) same block | (f) window | (g) partial |
|---|---|---|---|---|---|---|---|
| SSV API listing | as R2 (an answer without an API row is a gap whatever its total; logs next to it don't help) | as R2 (check-error, retried) | **R3** `…_at_every_point[reorg-{1,2}]`: the reorg run meets a short page offset by 1 / 2 log-only clusters → UNKNOWN, revisions unchanged, nothing resolved (green on `31b7a77` too: the `m7_legacy` id check); sweep fault `short_masked` (both sweeps, exhaustive) | **R3** `…_at_every_point[lost_database-{1,2}]` (new data dir: no `m7_legacy`, only the guard) — red on `31b7a77` | n/a | rows cached ≤ `api_refresh_s`, total fresh per full run (side effect above) | **R3** short page with the true total offset by log-only clusters (QA P1): `test_m7_p1_short_page_masked_by_a_log_only_cluster_first_run`, `test_m7_p1_masking_persists_on_the_next_daily_run`, `…_at_every_point[first-{1,2}]` (2 log-only clusters for 1 dropped row: more slack than needed), M6 `test_m6_short_page_offset_by_a_log_only_cluster_is_unknown` — all red on `31b7a77`; control (no log-only cluster) `test_m7_p1_control_short_page_alone_is_unknown` green on both; duplicates across pages: distinct ids (as R1); **residual** self-consistent partial page (above) |
| SSV API `pagination.total` | absent → gap (as R1); 0 never completes an empty page (as R2) | GET fails → gap (as R1) | read at every reorg run | stateless | n/a | fresh per run | non-integer/negative → gap (as R1); **R3** compared with API rows only |
| Scanned logs as inventory source (`db.latest_cluster_events`) | no log-only clusters → same result as before | n/a (framework) | rewound with the reorg (framework); a log-only cluster on a deleted block no longer influences completeness at all | as R2 | as R2 | as R2 | **R3** a log-only cluster is still evaluated (its tuple validates against storage) but never vouches for the API's completeness |
| Storage `daoValidatorCount` | as R2 | as R2 (raise) | read at the run block | stateless | n/a | pinned | as R2 |
| Storage cluster slots, token, `lastOperatorId` | as R1 | as R1 | as R1 | stateless | n/a | pinned | as R1 |
| Views `eth_call` | as R1 | as R1 | as R1 | n/a | n/a | pinned | as R1 |
| Params | as R1 | as R1 | as R1 | as R1 | as R1 | as R1 | n/a |
| Stored logs (cluster events for M6 event runs; unstake events for M7) | as R1 | n/a | as R1 | as R1 | as R1 | as R1 | n/a |
| eth_getLogs prefix (`check:m7:unstake_prefix`) | NOTE N1 (QA, not in scope; Q11 still fails) | as R1 | as R1 | as R1 | as R1 | fixed range | as R1 |
| `m6_clusters`, `m6_obs` | as R1 (the gap changes no row write) | n/a | as R1 | as R1 | n/a | as R1 | n/a |
| M6 open incidents | a gap resolves nothing (sweep `short_masked`: no resolve without proof, health every faulted run) | — | as R1 + sweep `short_masked` | as R1 + sweep restarts | — | — | as R1 |
| `check:m6:summary`, digest | as R2; **R3** short page offset by a log-only cluster sets `inventory_gap` (QA N3) | as R1 | as R1 | as R1 | n/a | as R1 | as R1 |
| `m7_legacy` (ids) | first run / new database: empty, only the guard protects it — **R3** the guard now sees F1's page | n/a | as R1 | as R1 | n/a | n/a | as R1 |
| M7 incidents `surplus-floor`, `surplus` | **R3** no baseline from F1's page; after recovery one baseline at the true surplus (−4 SSV) and the page (P1 port second half); the next-day leak is never read as exactly 1 SSV (persistence port) | as R1 | `[reorg-*]`, sweep `short_masked` | `[lost_database-*]` | — | as R1 | upper-bound page unchanged |
| Inbox, proof API, Blockscout, block timestamps | not consumed by M6/M7 (n/a) | | | | | | |

### Adversarial sweep (extended)

Both committed sweeps gain the fault `short_masked` (a short page with the true total — it drops the row of the cluster under attack — next to one ETH cluster registered on the new chain and known only from the logs). It runs **exhaustively** outside the seed (4 stories × confirmations {0, 3} × depth {1, N} × restart {none, detected, before} = 48 cases per check), so the seeded draws of the earlier faults are unchanged. In the M7 cases the attacked legacy cluster is inert (0 validators, 100 SSV) and a listed inert bystander (0 SSV) keeps the page non-empty — F1's shape exactly. Invariants after every step as before: no resolve without positive proof, no open incident without supporting evidence, no dropped page for surviving evidence (health on every faulted run, open pages kept), no window anchored on stale data, no event lost between sweeps, never a second baseline. Honest limit: as in repair 2, the sweep applies the fault after a baseline, where `m7_legacy` (M7) and `daoValidatorCount` (M6: the attacked cluster has a validator) already make the run UNKNOWN, so these 96 cases are green on `31b7a77` too; F1's first-run / new-database shape is pinned by the red tests above.

### Repair-3 completion handoff (≤ 250 words, also the `worker_done` summary)

```text
Task / live dispatch: task_517a6c9b022b / ctx_116bf746a1a4 (CP2e repair 3, Claude Opus high)
Assignment outcome: QA cp2e-3 F1 repaired (one narrow fix); fresh capped QA (D15) required.
Base -> resulting commit: 00235da -> lane head in the CP2e status line (code tested at 0117e54)
Changed paths: checks/{m6_legacy_coverage,m7_ssv_solvency}.py (one comparison + docstrings),
  tests/{test_m6,test_m7}.py, workers/cp2e/** (plan.md Repair 3, attempts.md #attempt-7,
  live_completeness.py F1-shaped control, evidence/repair3-*, live-completeness-26247429.json), plan.md status.
F1: leg (2) of inventory_gap compares only the distinct API rows with pagination.total; log-only clusters
  no longer offset a dropped row (coordinator ruling). Missing/unparseable total, leg (1), empty rule unchanged.
Regressions: QA P1 + persistence ported, control kept, 8-case point x log-only class test, M6 twin (N3):
  7 red on 31b7a77, green; QA probes3 7/7 green; sweeps + exhaustive fault short_masked (96 cases).
Verification: V2x.1 150+173; V2x.2 3; V2x.3 5; V2x.4 2; reorg 23; sweep 216; V2x.6 563 passed, ruff clean;
  12 falsifiers red->green (R5a/R5b new), porcelain 0.
LIVE: guard at 26,247,429: 346 = 346, 1,908 API rows = total 1,908, gap none; 4 controls gap incl. F1 shape.
NOT RUN: V2e.7 / LIVE ledger (long rows; carried from QA attempt 1), older falsifiers listed in #attempt-7;
  subgraph DEFERRED. N1/N2/N4 not addressed. Residual NOTE: a self-consistent partial page (total agrees with
  its rows) missing only inert legacy clusters at a first run (no on-chain legacy-cluster count).
Next: fresh Opus QA (capped, D15) on the lane head.
```
