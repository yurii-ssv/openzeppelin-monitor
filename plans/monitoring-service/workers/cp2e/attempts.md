# Attempts — CP2e

Same entry shape as execution-log.md events, worker-side. Append only. Measured UTC (`date -u +%Y-%m-%dT%H:%MZ`).

<a id="attempt-1"></a>
## Attempt 1 — baseline, inputs, rulings

- Observed UTC: 2026-10-21T14:12Z – 14:30Z
- Task / Dispatch: `task_78dba27a67d8` / `ctx_146d942c81b2`
- Frozen base: `cbe0d036752a391b227810d583505d911da3e259` (branch `mtabasco/ms-cp2e`, porcelain 0). `96d1c32` (packet base) is an ancestor; coordinator addendum `msg_1e4df50c659c` makes the current integration head the base (frozen-test patch `bd531b4`, `500abe4` included). `git diff --stat 96d1c32..cbe0d03`: execution-log.md, state.md, tests/harness.py, tests/test_framework.py, tests/test_worker.py only.
- Action and concrete outcome: venv per plan §7 (`uv venv reconcile/.venv --python 3.12`; `uv pip install … -r reconcile/requirements-dev.txt`: pytest 9.1.1, ruff 0.17.1, pygments 2.21.0, pycryptodome). Read plan §1, §2, §5, §6, §7 CP2, §8 CP2e; `reconcile/checks/README.md` in full; spec M6, M7, §4, §5; evidence scripts `#91046/*.mjs`; pinned source (`SSVViews.sol` isLiquidatableSSV/getBalanceSSV/getBurnRateSSV/getOperatorEarningsSSV, `ClusterLib.isLiquidatable`, `SSVStaking.requestUnstake/withdrawUnlocked`, `SSVStorage.StorageData`: `token` = main+7, `lastOperatorId` = main+8, `ethClusters` = main+10 matches `eb_storage.MAIN_ETH_CLUSTERS`).
- LIVE probe (read-only, endpoint `https://eth-mainnet.public.blastapi.io`, block 26,241,510): `lastOperatorId` = **2352**; `StorageData.token` = `0x9d65…7f54` (= spec §5 SSV); `UnstakeRequested`/`UnstakedWithdrawn` logs in [24,912,723 (v2 upgrade), 24,920,726] = **0** → `checks.m7.unstake_history_from` = 24,920,727 (= `activation_block`). `https://eth.drpc.org`: `eth_getLogs: Request timeout on the free plan`; `https://eth-pokt.nodies.app`: HTTP 429; publicnode: `Archive requests require a personal token` (recorded, not used).

| Verification ID | Exact command | Exit / result / counts / skips | Environment | Evidence path/hash |
|---|---|---|---|---|
| baseline | `reconcile/.venv/bin/python -m pytest -q reconcile/tests` | exit 0; **240 passed** | Python 3.12, fresh venv | this entry |

- Rulings asked (`orca orchestration ask`, msg `msg_b7ce7ecd3bef`), coordinator answer verbatim: "Q1 A, Q2 A, with conditions. Q1: duration counted in blocks since first observed liquidatable; the back-dated start on first observation must be clamped as you describe AND flagged in the finding evidence as 'estimated start'; never later than the observed first block; test the boundary at exactly the threshold block (> vs >=) and a threshold-period change on the fake chain moving the warn point. Q2: page only when validatorCount > 0 and live balance == 0 while active; the vc=0 and balance==0 clusters are counted in the weekly digest AND the page/warn text must never claim they were checked clean: add them as an explicit digest line (count and ids sample). A cluster whose validatorCount goes 0 -> >0 while balance is 0 must page on that block (test it). Both are spec M6 interpretations recorded by the coordinator for amendment at closure."
- Resulting head: `cbe0d03` (no commit). Next: S2.

<a id="attempt-2"></a>
## Attempt 2 — implementation

- Observed UTC: 2026-10-21T14:20Z – 14:43Z
- Action and concrete outcome: commits `9cf8c96`, `3fc207d`, `582cd47`. New: `reconcile/checks/m6_legacy_coverage.py`, `reconcile/checks/m7_ssv_solvency.py`, `reconcile/tests/{test_m6,test_m7,fixtures_legacy}.py`, `plans/monitoring-service/workers/cp2e/{plan.md,attempts.md,live_legacy.py}`. Changed: `reconcile/checks/__init__.py` (one line in the CP2e block), `reconcile/config.example.json` (`checks.m6`, `checks.m7` only). Design table and reorg rules: [plan.md](plan.md#design-for-qa). Ruling conditions implemented: `estimated_start` in the evidence; `>` boundary test; period-change test; vc=0 pages never, a 0→>0 change at balance 0 pages at its block (event-scoped run); digest line "Not evaluated for the zero-balance page (0 validators, so no burn): N … e.g. <ids>".
- No framework file edited (`git diff cbe0d03 --name-only` = the paths above).

Scenario names (V2x.1: one test per spec trigger line at its severity, plus a benign twin):

| Spec trigger | Severity | Fires | Benign twin (silent) |
|---|---|---|---|
| M6 `isLiquidatableSSV` > 7 d (half window) | warn | `test_m6_liquidatable_longer_than_half_window_warns`, `test_m6_observed_start_is_not_back_dated_before_the_last_clear_sweep` | `test_m6_benign_liquidatable_exactly_half_window_stays_silent` |
| M6 `getBalanceSSV == 0` while active | page | `test_m6_zero_balance_while_active_pages`, `test_m6_validator_count_zero_to_positive_at_zero_balance_pages_on_that_block` | `test_m6_benign_zero_balance_without_validators_stays_silent`, `test_m6_benign_positive_balance_not_liquidatable_stays_silent` |
| M6 weekly digest | digest | `test_m6_weekly_digest_counts_clusters_validators_and_burn` | same test: the six daily digests in between carry no M6 section |
| M7 surplus < 1 SSV | page | `test_m7_surplus_below_one_ssv_pages` | `test_m7_benign_surplus_exactly_one_ssv_does_not_page` |
| M7 any change in the surplus | info | `test_m7_surplus_change_sends_info_under_one_incident` | `test_m7_benign_unchanged_surplus_sends_nothing` |

Further scenarios: escalation/resolution (`test_m6_warn_escalates_to_page_…_resolves_when_liquidated`, `…migration_to_eth_resolves`, `…no_longer_liquidatable_resolves`, `test_m7_page_resolves_…`), event runs (`test_m6_event_run_resolves_a_liquidation_at_once_and_ignores_eth_clusters`), UNKNOWN (`test_m6_unvalidated_tuple_…`, `test_m6_reverting_or_empty_view_…[revert,empty]`, `test_m6_api_failure_…`, `test_m7_unpriced_legacy_cluster_…`, `test_m7_unknown_still_pages_…`, `test_m7_unknown_keeps_an_open_page`, `test_m7_reverting_operator_earnings_…`, `test_m7_missing_head_value_…[revert,empty]`, `test_m7_operator_count_zero_…`), config (`test_m6_requires_views_and_the_api`, `test_m7_requires_cssv_and_unstake_history`), params outage (`test_m6_params_unavailable_skips_the_run`), unstakes (`test_m7_unstake_request_burns_cssv_…`, `test_m7_same_block_request_and_withdrawal`, `test_m7_unstake_history_before_start_block_is_read_once`), thresholds (`test_m6_thresholds_from_chain_period_change_moves_the_warn`, `test_m6_thresholds_from_chain_back_dating_uses_the_live_minimum_collateral`, `test_m7_thresholds_from_chain_operator_count_and_token_come_from_storage`, `test_m7_thresholds_floor_is_config_not_code`), restart (`test_m6_restart_does_not_duplicate_the_open_incident`, `test_m6_restart_with_a_prior_liquidatable_row_keeps_counting`, `test_m7_restart_does_not_duplicate_the_open_incidents`), rpc budget (`test_m6_rpc_budget_350_cluster_inventory`, `test_m7_rpc_budget_350_cluster_inventory`), reorgs (`test_m6_reorg_depth_1_…`, `…depth_n_beyond_the_confirmation_window_…`, `…crash_between_detection_and_the_check_run`, `…condition_survives_…`, `…drops_a_liquidatable_start_observed_on_deleted_blocks`, `…forgets_a_clear_observation_on_deleted_blocks`, `…reports_every_open_key_even_without_inventory_rows`; `test_m7_reorg_depth_1_…`, `…depth_n_…`, `…crash_…`, `…deleting_an_unstake_request_rewinds_the_sum`, `…with_the_leak_on_the_new_chain_keeps_the_page`).

<a id="attempt-4"></a>
## Attempt 4 — verification rows and falsifiers (tree `582cd47`, porcelain 0 before each row)

- Observed UTC: 2026-10-21T14:44Z

| Verification ID | Exact command | Exit / result / counts / skips | Environment | Evidence path/hash |
|---|---|---|---|---|
| V2x.1 M6 | `reconcile/.venv/bin/python -m pytest -q reconcile/tests/test_m6.py` | exit 0; 31 passed | local, fake chain | `582cd47` |
| V2x.1 M7 | `… -m pytest -q reconcile/tests/test_m7.py` | exit 0; 25 passed | local | `582cd47` |
| V2x.2 | `… -m pytest -q reconcile/tests/test_m6.py reconcile/tests/test_m7.py -k thresholds_from_chain` | exit 0; 3 passed, 53 deselected | local | `582cd47` |
| V2x.3 | `… -k restart` (same files) | exit 0; 3 passed, 53 deselected | local | `582cd47` |
| V2x.4 | `… -k rpc_budget` (same files) | exit 0; 2 passed; bounds: M6 eth_call ≤ 3 × 350; M7 eth_call ≤ 3 + 2352 + 350 | local | `582cd47` |
| reorg (contract 3) | `… -k reorg` (same files) | exit 0; 12 passed | local | `582cd47` |
| V2x.6 | `… -m pytest -q reconcile/tests` | exit 0; **296 passed** (240 + 31 + 25), 0 skipped | local | `582cd47` |
| V2x.6 | `reconcile/.venv/bin/ruff check reconcile` | exit 0; All checks passed | local | `582cd47` |
| config completeness | `… -m pytest -q reconcile/tests/test_framework.py -k config_example` | exit 0; 1 passed | local | `582cd47` |

- Falsifiers (V2x.5): mutation → named test → restored (`git checkout -- <file>`) → named test green → `git status --porcelain` = 0 lines, each:

| ID | Mutation | Named test | Mutated | Restored |
|---|---|---|---|---|
| F6a | M6 `lag > warn_blocks` → `>=` | `test_m6_benign_liquidatable_exactly_half_window_stays_silent` | 1 failed | 1 passed, porcelain 0 |
| F6b | M6 drop `and vc > 0` from the page condition | `test_m6_benign_zero_balance_without_validators_stays_silent` | 1 failed | 1 passed, porcelain 0 |
| F6c | M6 drop the reorg rewind of `last_clear_block` | `test_m6_reorg_forgets_a_clear_observation_on_deleted_blocks` | 1 failed | 1 passed, porcelain 0 |
| F7a | M7 drop the outstanding-unstake term | `test_m7_unstake_request_burns_cssv_and_is_counted_until_withdrawn` | 1 failed | 1 passed, porcelain 0 |
| F7b | M7 `surplus < floor` → `<=` | `test_m7_benign_surplus_exactly_one_ssv_does_not_page` | 1 failed | 1 passed, porcelain 0 |
| F7c | M7 treat an UNKNOWN ledger as clean (skip the unknown branch) | `test_m7_unpriced_legacy_cluster_is_unknown_not_clean` | 1 failed | 1 passed, porcelain 0 |
