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
- Self-review additions (commits `51803c9`, `b292091`): M7 `m7_legacy` completeness guard — a legacy cluster id once listed that disappears from the inventory while storage still holds it as `VERSION_SSV` is an unpriced claim (UNKNOWN), never a smaller liability (`test_m7_legacy_cluster_missing_from_the_inventory_is_unknown_not_a_smaller_liability`); M6 docstring records the rulings and the event-scoped schedule.

Scenario names (V2x.1: one test per spec trigger line at its severity, plus a benign twin):

| Spec trigger | Severity | Fires | Benign twin (silent) |
|---|---|---|---|
| M6 `isLiquidatableSSV` > 7 d (half window) | warn | `test_m6_liquidatable_longer_than_half_window_warns`, `test_m6_observed_start_is_not_back_dated_before_the_last_clear_sweep` | `test_m6_benign_liquidatable_exactly_half_window_stays_silent` |
| M6 `getBalanceSSV == 0` while active | page | `test_m6_zero_balance_while_active_pages`, `test_m6_validator_count_zero_to_positive_at_zero_balance_pages_on_that_block` | `test_m6_benign_zero_balance_without_validators_stays_silent`, `test_m6_benign_positive_balance_not_liquidatable_stays_silent` |
| M6 weekly digest | digest | `test_m6_weekly_digest_counts_clusters_validators_and_burn` | same test: the six daily digests in between carry no M6 section |
| M7 surplus < 1 SSV | page | `test_m7_surplus_below_one_ssv_pages` | `test_m7_benign_surplus_exactly_one_ssv_does_not_page` |
| M7 any change in the surplus | info | `test_m7_surplus_change_sends_info_under_one_incident` | `test_m7_benign_unchanged_surplus_sends_nothing` |

Further scenarios: escalation/resolution (`test_m6_warn_escalates_to_page_…_resolves_when_liquidated`, `…migration_to_eth_resolves`, `…no_longer_liquidatable_resolves`, `test_m7_page_resolves_…`), event runs (`test_m6_event_run_resolves_a_liquidation_at_once_and_ignores_eth_clusters`), UNKNOWN (`test_m6_unvalidated_tuple_…`, `test_m6_reverting_or_empty_view_…[revert,empty]`, `test_m6_api_failure_…`, `test_m7_unpriced_legacy_cluster_…`, `test_m7_unknown_still_pages_…`, `test_m7_unknown_keeps_an_open_page`, `test_m7_reverting_operator_earnings_…`, `test_m7_missing_head_value_…[revert,empty]`, `test_m7_operator_count_zero_…`), config (`test_m6_requires_views_and_the_api`, `test_m7_requires_cssv_and_unstake_history`), params outage (`test_m6_params_unavailable_skips_the_run`), unstakes (`test_m7_unstake_request_burns_cssv_…`, `test_m7_same_block_request_and_withdrawal`, `test_m7_unstake_history_before_start_block_is_read_once`), thresholds (`test_m6_thresholds_from_chain_period_change_moves_the_warn`, `test_m6_thresholds_from_chain_back_dating_uses_the_live_minimum_collateral`, `test_m7_thresholds_from_chain_operator_count_and_token_come_from_storage`, `test_m7_thresholds_floor_is_config_not_code`), restart (`test_m6_restart_does_not_duplicate_the_open_incident`, `test_m6_restart_with_a_prior_liquidatable_row_keeps_counting`, `test_m7_restart_does_not_duplicate_the_open_incidents`), rpc budget (`test_m6_rpc_budget_350_cluster_inventory`, `test_m7_rpc_budget_350_cluster_inventory`), reorgs (`test_m6_reorg_depth_1_…`, `…depth_n_beyond_the_confirmation_window_…`, `…crash_between_detection_and_the_check_run`, `…condition_survives_…`, `…drops_a_liquidatable_start_observed_on_deleted_blocks`, `…forgets_a_clear_observation_on_deleted_blocks`, `…reports_every_open_key_even_without_inventory_rows`; `test_m7_reorg_depth_1_…`, `…depth_n_…`, `…crash_…`, `…deleting_an_unstake_request_rewinds_the_sum`, `…with_the_leak_on_the_new_chain_keeps_the_page`).

<a id="attempt-3"></a>
## Attempt 3 — LIVE: V2e.7 and the M6/M7 evaluation code on mainnet (read-only)

- Observed UTC: 2026-10-21T14:36Z – 18:24Z (several runs; endpoint limits below)
- Script: [`live_legacy.py`](live_legacy.py) (same `inventory.build`, `legacy_balances`, `M6LegacyCoverage.run`, `M7SsvSolvency.ledger/run` as the worker; a real `Ctx` on a scratch database; `eth_call`/`eth_getStorageAt` on `https://eth-mainnet.public.blastapi.io`; logs on Blockscout v1 `getLogs` GETs `https://eth.blockscout.com`; SSV API v4 GETs).
- Command (run 9, code of the final head): `reconcile/.venv/bin/python plans/monitoring-service/workers/cp2e/live_legacy.py --rpc https://eth-mainnet.public.blastapi.io --blockscout https://eth.blockscout.com --out <scratch>/live9.json` → **exit 2** (= log side NOT RUN), 2026-10-21T18:00Z–18:16Z, block **26,242,622**. Evidence: [`evidence/live-run9-26242622.json`](evidence/live-run9-26242622.json) (sha256 `2a8a2791…6775`), stages [`evidence/live-run9.stages.txt`](evidence/live-run9.stages.txt); run 7 (block 26,242,020, same results) [`evidence/live-run7-26242020.json`](evidence/live-run7-26242020.json) (sha256 `1fcadc67…b90b`).

| Verification ID | Result |
|---|---|
| **V2e.7 API side** | **PASS**: 1,908 API rows; 1,026 `migrated=false`; all 1,026 are `VERSION_SSV` in storage at the block; storage-legacy among all API rows = 1,026; API flag contradicting storage **0**; API rows absent from storage **0**; inventory at the block: 1,026 legacy, **1,026 valid** (every API tuple validates against storage), 0 invalid. |
| **V2e.7 log side** | **NOT RUN** — no public source served the historical proxy logs: blastapi `"You can make eth_getLogs requests with up to a 10 block range"`; drpc `"ranges over 10000 blocks are not supported on free plan"` (also for a 2,000-block historical range) and `"Request timeout on the free plan, please upgrade to paid plan"`; nodies `"Block range too large: maximum allowed is 50 blocks on your current plan"` + HTTP 429; publicnode `"Archive requests require a personal token"`; Blockscout v1 getLogs served one run, then `HTTP Error 429: Too Many Requests` (runs 5, 6, 9, 10; 2 s and 6 s pacing). Blockscout also ignores `page` for getLogs (block 24,970,652 holds ≥ 1,000 `ValidatorRemoved` logs; handled by a one-block RPC read in the script). `ask` sent (msg `msg_efdf8c0c88e7`, options A wait / B Routescan / C keyed archive RPC / D NOT RUN), unanswered: the coordinator is paused (event 0054). Owner: CP3 / Marco (needs an archive endpoint with getLogs). |
| LIVE M6 (run 9) | live params: `liquidation_threshold_period_ssv` 100,240 (= spec §5), `minimum_liquidation_collateral_ssv` 0.385795451 SSV; 567 active legacy clusters priced, 0 unknown, 459 liquidated; 346 validators; burn **3,239.28 SSV/yr** (spec baseline 4,062.82 on 2026-09-02: the surface shrinks); 0 liquidatable; 0 zero-balance burning; **422 active vc=0 clusters at balance 0** (would page 422 times without ruling Q2 A); findings **0**. eth_call 3,402 = 2 × 3 × 567 (the script prices once itself, then `run` prices again). |
| LIVE M7 (run 9) | proxy SSV 4,466,280.7215… ; cSSV supply 4,409,339.8651…; outstanding unstakes 45,918.6586… (180 requests / 157 withdrawals since 24,920,727); legacy balances 7,175.2561… (567 clusters); operator SSV earnings 3,730.2153… (2,352 operators); network SSV earnings 115.7264…; **surplus = 1.000000000000000000 SSV exactly** (spec: "reconciles to a 1.0 SSV surplus"); unknown 0; findings: info baseline only (no page). eth_call 5,844 = 2 × (3 + 2,352 + 567) (ledger evaluated twice by the script); eth_getStorageAt 4; eth_getLogs 1 (unstake prefix, Blockscout). |
| Subgraph cross-check | **DEFERRED** — `SSV_SUBGRAPH_API_KEY` absent on this host; owner Marco. Not faked. |

- Note for QA: the API lists 1,908 clusters in total (`pagination.total`), and the inventory therefore costs 2 storage reads per API row per block (framework, not this lane).

<a id="attempt-4"></a>
## Attempt 4 — verification rows and falsifiers (final head `b4f6c9e`, porcelain 0 before each row)

- Observed UTC: 2026-10-21T18:24Z (rows re-run on the final head; first run on `582cd47` at 14:44Z had the same results with 25 M7 tests / 296 total)
- `b4f6c9e` = lane commits + `git merge --no-edit plan/monitoring-service` (docs only: execution-log, state, `qa/DEFECT-CLASSES.md`).

| Verification ID | Exact command | Exit / result / counts / skips | Environment | Evidence |
|---|---|---|---|---|
| V2x.1 M6 | `reconcile/.venv/bin/python -m pytest -q reconcile/tests/test_m6.py` | exit 0; 31 passed | local, fake chain | `b4f6c9e` |
| V2x.1 M7 | `… -m pytest -q reconcile/tests/test_m7.py` | exit 0; 26 passed | local | `b4f6c9e` |
| V2x.2 | `… -m pytest -q reconcile/tests/test_m6.py reconcile/tests/test_m7.py -k thresholds_from_chain` | exit 0; 3 passed, 54 deselected | local | `b4f6c9e` |
| V2x.3 | `… -k restart` (same files) | exit 0; 3 passed, 54 deselected | local | `b4f6c9e` |
| V2x.4 | `… -k rpc_budget` (same files) | exit 0; 2 passed; bounds: M6 eth_call ≤ 3 × 350; M7 eth_call ≤ 3 + 2,352 + 350 | local | `b4f6c9e` |
| reorg (contract 3) | `… -k reorg` (same files) | exit 0; 12 passed | local | `b4f6c9e` |
| V2x.6 | `… -m pytest -q reconcile/tests` | exit 0; **297 passed** (240 + 31 + 26), 0 skipped | local | `b4f6c9e` |
| V2x.6 | `reconcile/.venv/bin/ruff check reconcile` | exit 0; All checks passed | local | `b4f6c9e` |
| lint (script) | `reconcile/.venv/bin/ruff check --config reconcile/ruff.toml plans/monitoring-service/workers/cp2e/live_legacy.py` | exit 0 | local | `b4f6c9e` |
| config completeness | `… -m pytest -q reconcile/tests/test_framework.py -k config_example` | exit 0; 1 passed | local | `b4f6c9e` |
| V2e.7 | attempt 3 | API side PASS; log side **NOT RUN** (exact errors above) | mainnet read-only | `evidence/` |

- Falsifiers (V2x.5), each: mutation → named test → `git checkout -- <file>` → named test green → `git status --porcelain` 0 lines:

| ID | Mutation | Named test | Mutated | Restored |
|---|---|---|---|---|
| F6a | M6 `lag > warn_blocks` → `>=` | `test_m6_benign_liquidatable_exactly_half_window_stays_silent` | 1 failed | 1 passed, porcelain 0 |
| F6b | M6 drop `and vc > 0` from the page condition | `test_m6_benign_zero_balance_without_validators_stays_silent` | 1 failed | 1 passed, porcelain 0 |
| F6c | M6 drop the reorg rewind of `last_clear_block` | `test_m6_reorg_forgets_a_clear_observation_on_deleted_blocks` | 1 failed | 1 passed, porcelain 0 |
| F7a | M7 drop the outstanding-unstake term | `test_m7_unstake_request_burns_cssv_and_is_counted_until_withdrawn` | 1 failed | 1 passed, porcelain 0 |
| F7b | M7 `surplus < floor` → `<=` | `test_m7_benign_surplus_exactly_one_ssv_does_not_page` | 1 failed | 1 passed, porcelain 0 |
| F7c | M7 treat an UNKNOWN ledger as clean | `test_m7_unpriced_legacy_cluster_is_unknown_not_clean` | 1 failed | 1 passed, porcelain 0 |
| F7d | M7 drop the missing-from-inventory UNKNOWN | `test_m7_legacy_cluster_missing_from_the_inventory_is_unknown_not_a_smaller_liability` | 1 failed | 1 passed, porcelain 0 |

- Adversarial self-review (QA list), where each is covered: reorg of the block carrying the trigger (`test_m6_reorg_depth_1_…`, `test_m7_reorg_depth_1_…`, `…deleting_an_unstake_request…`); same-block ordering (`test_m7_same_block_request_and_withdrawal`; M6 has no event-ordered state); prior non-zero state then restart (`test_m6_restart_with_a_prior_liquidatable_row_keeps_counting`, `test_m7_restart_…`); parameter changed mid-run (`test_m6_thresholds_from_chain_period_change_moves_the_warn`; params outage `test_m6_params_unavailable_skips_the_run`); missing input treated as clean (UNKNOWN tests, F7c, F7d, API failure, required config); empty RPC result (`…reverting_or_empty_view…[empty]`, `…missing_head_value…[empty]`; `rpc.get_logs` raises on a non-list); first sweep without baseline (back-dating tests, M7 baseline info).
- Known limits (not defects of this lane, recorded for QA/CP3): the legacy set is the API ∪ logs since `start_block` (D8); a legacy cluster never listed by the API and without activity since the activation is invisible to both checks until V2e.7's log side runs; M6 back-dating assumes the current burn rate (flagged `estimated_start`); M7 runs daily, so a page can lag a leak by up to `interval_s`.
- Resulting head: `b4f6c9e` + this record.

<a id="attempt-5"></a>
## Attempt 5 — repair 1 (QA attempt 1: F1, F2, F3)

- Observed UTC: 2026-10-22T08:50Z – 09:40Z
- Task / Dispatch: `task_9a6318071980` / `ctx_b4ba8297cc36` (fresh implementer, Claude Opus high, `claude-opus-5-5`)
- Frozen start head: `b6f90b104eafcfcd204a79bdd40252bf7f407221` (`git rev-parse HEAD`), porcelain 0; venv `reconcile/.venv` present (Python 3.12.15); baseline `pytest -q reconcile/tests` → 297 passed.
- Design and state table: [plan.md § Repair 1](plan.md#repair-1).
- Commits: `a7563d3` (regressions + fixture, code untouched), `cf82a48` (F1 completeness guard, F2 back-date), `c87839c` (M6 reorg restore from `m6_obs` + its 2 regressions), `83d6bf5` (seeded sweeps), `6fca0d8` (the open-key enumeration regression also loses the row history), `e6a67d9` (LIVE script + evidence), then this record.
- Fixture (`fixtures_legacy.py`): `LegacySSV(MiniSSV)` keeps `daoValidatorCount` in StorageProtocol word 0 as the contract does (Σ validators of active legacy clusters, written with every cluster persist); the fake API serves `pagination.total` and `api_fault` ∈ {empty, short, error, no_total}.

RED on the frozen code (`a7563d3`: new tests and fixture, checks of `b6f90b1`), [evidence/repair1-red-on-frozen-code.txt](evidence/repair1-red-on-frozen-code.txt): **18 failed, 60 passed** — F1: `test_m6_first_sweep_with_an_empty_api_listing_is_unknown_not_clean` (QA PR2-M6 port), `test_m6_listing_short_of_its_total_or_without_one_is_unknown[short,no_total]`, `test_m6_unreadable_api_total_is_unknown_not_clean`, `test_m6_cluster_missing_from_the_api_and_its_total_is_found_by_the_dao_validator_count`, `test_m6_restart_on_a_lost_database_with_an_empty_api_is_unknown`, `test_m6_digest_of_an_incomplete_inventory_says_unknown_not_zero`, `test_m7_first_run_with_an_empty_api_listing_is_unknown_not_a_baseline` (QA PR2-M7 port), `test_m7_incomplete_inventory_still_pages_when_the_upper_bound_is_below_the_floor`, `test_m7_listing_short_of_its_total_or_without_one_is_unknown[short,no_total]`, `test_m7_unreadable_api_total_is_unknown_not_a_change`, `test_m7_cluster_missing_from_the_api_and_its_total_is_found_by_the_dao_validator_count`, `test_m7_restart_on_a_lost_database_with_an_empty_api_is_unknown`; F2: `test_m6_backdated_start_of_a_cluster_that_just_crossed_is_now` (PR1a port), `test_m6_benign_liquidatable_exactly_half_window_stays_silent` (now PR1c: true duration exactly W), `test_m6_liquidatable_longer_than_half_window_warns` and `test_m6_thresholds_from_chain_back_dating_uses_the_live_minimum_collateral` (the lane tests that encoded the off-by-one, corrected to W + 2 blocks of deficit = W + 1 liquidatable blocks). Green at `cf82a48`. Coverage tests green on both (F3: they pin correct behaviour; shown to kill QA's surviving mutants below): `test_m7_reorg_run_whose_re_evaluation_is_unknown_keeps_both_keys_without_a_second_baseline` (PR12), `test_m6_reorg_run_reports_an_open_key_without_a_persisted_row` (QF6d), `test_m6_reorg_orphaning_the_liquidation_that_resolved_the_page_reopens_it` (PR10), `test_m7_reorg_orphaning_the_repair_that_resolved_the_page_reopens_it`, `test_m6_events_of_one_scan_step_are_evaluated_at_the_run_block` (PR9 shape), `test_m7_unstake_prefix_read_failure_is_retried_not_zero`.
- State-table row found wrong while generalising (c, M6): `test_m6_reorg_restores_the_canonical_start_a_deleted_clear_observation_overwrote` and `test_m6_reorg_orphaning_a_liquidation_restores_the_observed_start` — **2 failed** before `c87839c` (`assert (-99116 == (1001 + 1))`: the start re-estimated from the balance, before the canonical clear sweep, and a false warn), green after.
- QA Appendix A probes re-created verbatim in scratch (`conftest.py`, `test_probe_m6.py` PR1a/PR1c/PR2-M6, `test_probe_m7.py` PR2-M7), `QA_ROOT=$PWD … pytest --rootdir <probes> <probes>` → **4 passed** (QA: 4 failed on `c02557a`).

| Verification ID | Exact command (`P=reconcile/.venv/bin/python`, `-q -p no:cacheprovider`) | Exit / result | Head |
|---|---|---|---|
| V2x.1 M6 | `$P -m pytest reconcile/tests/test_m6.py` | 0; 92 passed (44 + 48 sweep) | `e6a67d9` |
| V2x.1 M7 | `$P -m pytest reconcile/tests/test_m7.py` | 0; 92 passed (36 + 56 sweep) | `e6a67d9` |
| V2x.2 | `… test_m6.py test_m7.py -k thresholds_from_chain` | 0; 3 passed, 181 deselected | `e6a67d9` |
| V2x.3 | `… -k restart` | 0; 5 passed, 179 deselected | `e6a67d9` |
| V2x.4 | `… -k rpc_budget` | 0; 2 passed (M6 eth_call ≤ 3 × 350; M7 ≤ 3 + 2,352 + 350; the guard adds 1 eth_getStorageAt and 1 API GET per full run) | `e6a67d9` |
| reorg | `… -k reorg` | 0; 18 passed | `e6a67d9` |
| sweep | `… -k adversarial` | 0; 104 passed (frozen checks: M6 28/48, M7 8/56 failed) | `e6a67d9` |
| V2x.6 | `$P -m pytest reconcile/tests` | 0; **424 passed**, 0 skipped | `e6a67d9` |
| V2x.6 | `reconcile/.venv/bin/ruff check reconcile` (+ `--config reconcile/ruff.toml` on both workers/cp2e scripts) | 0; All checks passed | `e6a67d9` |
| config | `$P -m pytest reconcile/tests/test_framework.py -k config_example` | 0; 1 passed | `e6a67d9` |
| LIVE completeness (new) | `$P plans/monitoring-service/workers/cp2e/live_completeness.py --rpc https://ethereum-rpc.publicnode.com --out <scratch>/live-completeness.json` | 0 (09:11Z–09:17Z); block **26,247,163**: full InventoryCache 1,908 clusters (1,026 legacy, 567 active, 0 invalid); `daoValidatorCount` **346** = listed **346**; `pagination.total` **1,908** = listing 1,908; **gap none**; controls: empty page → gap, one burning legacy row dropped → gap (306 vs 346; 1,907 of 1,908). [evidence/live-completeness-26247163.json](evidence/live-completeness-26247163.json) sha256 `13b5fbfc8117dcc4678c38b78dde330750feedece6daae35b8dfccb65bd27934` | `e6a67d9` |
| V2e.7, LIVE M6/M7 | — | **NOT RUN** this attempt: QA attempt 1 measured both sides PASS and the M6/M7 numbers at 26,246,821; the repair changes no V2e.7 logic. Note: `live_legacy.py` feeds the checks a legacy-only inventory without an InventoryCache, so its M6/M7 section now reports an inventory gap by construction (V2e.7 part unaffected); the guard's LIVE behaviour is the row above | — |
| Subgraph cross-check | — | DEFERRED (no `SSV_SUBGRAPH_API_KEY`; owner Marco) | — |

Falsifiers (scratch `falsify.py`: one exact-string mutation → named test(s) → `git checkout -- <file>` → rerun → `git status --porcelain`), on the code of `6fca0d8` (= `e6a67d9`; R1a–R3c, F6a–F7d after `83d6bf5`, QF6d re-run after `6fca0d8`), porcelain 0 after each:

| ID | Mutation | Named test(s) | Mutated | Restored |
|---|---|---|---|---|
| F6a | `lag > warn_blocks` → `>=` | `test_m6_benign_liquidatable_exactly_half_window_stays_silent` | 1 failed | 1 passed |
| F6b | drop `and vc > 0` | `test_m6_benign_zero_balance_without_validators_stays_silent` | 1 failed | 1 passed |
| F6c | drop the reorg rewind (`self._rewind` → `pass`) | `test_m6_reorg_forgets_a_clear_observation_on_deleted_blocks` | 1 failed | 1 passed |
| F7a | claims without `outstanding` | `test_m7_unstake_request_burns_cssv_and_is_counted_until_withdrawn` | 1 failed | 1 passed |
| F7b | `surplus < floor` → `<=` | `test_m7_benign_surplus_exactly_one_ssv_does_not_page` | 1 failed | 1 passed |
| F7c | `unknown = {}` after the ledger | `test_m7_unpriced_legacy_cluster_is_unknown_not_clean` | 1 failed | 1 passed |
| F7d | `m7_legacy` guard `if False:` | `test_m7_legacy_cluster_missing_from_the_inventory_is_unknown_not_a_smaller_liability` | 1 failed | 1 passed |
| QF6d (QA survivor) | second loop enumerates only `rows` | `test_m6_reorg_run_reports_an_open_key_without_a_persisted_row` | 1 failed | 1 passed |
| QF7b (QA survivor) | drop the `surplus` re-report on UNKNOWN | `test_m7_reorg_run_whose_re_evaluation_is_unknown_keeps_both_keys_without_a_second_baseline` | 1 failed | 1 passed |
| R1a | `inventory_gap` returns None | M6 + M7 first-run empty-API regressions | 2 failed | 2 passed |
| R1b | no `daoValidatorCount` comparison | both `…_found_by_the_dao_validator_count` | 2 failed | 2 passed |
| R1c | no `pagination.total` comparison | both `…listing_short_of_its_total_or_without_one…` | 2 failed / 4 | 4 passed |
| R1d | missing total accepted as complete | same | 2 failed / 4 | 4 passed |
| R1e | M6 `gap = None` | `test_m6.py -k adversarial` | 8 failed / 48 | 48 passed |
| R2a | frozen back-date formula | PR1a + PR1c ports | 2 failed | 2 passed |
| R3a | rewind without history (`prev = None`) | `test_m6_reorg_restores_the_canonical_start_…` | 1 failed | 1 passed |
| R3b | no tombstone on deletion | `test_m6_reorg_orphaning_a_liquidation_restores_the_observed_start` | 1 failed | 1 passed |
| R3c | rewind dropped | `test_m6.py -k adversarial` | 24 failed / 48 | 48 passed |

- Self-review notes: QF6d first survived again after `c87839c` (the history restored the deleted row, so the mutant became equivalent); the regression now also deletes `m6_obs`, then kills it. The `pagination.total` GET goes through the worker's InventoryCache (`ctx._inventory.__self__`: same base and transport as the listing; a Ctx without it is a gap, fail closed) — a read of a framework object, no write; CP3 may prefer to expose the total from `inventory.fetch_api_clusters` (frozen to this lane).
- Resulting head: the CP2e status line in `plan.md` (this record's commit).

<a id="attempt-6"></a>
## Attempt 6 — repair 2 (QA attempt 2: F1)

- Observed UTC: 2026-10-22T09:35Z – 09:55Z
- Task / Dispatch: `task_c706fd249b26` / `ctx_3a5d60dc1b12` (fresh implementer, Claude Opus high, `claude-opus-5-5`)
- Frozen start head: `8e07b0a814216dbbcf9e43bc5dfef1049c5d4107` (`git rev-parse HEAD`), porcelain 0; venv `reconcile/.venv` present (Python 3.12.15).
- Design and state table: [plan.md § Repair 2](plan.md#repair-2).
- Commits: `7d30c15` (regressions + fixture fault `empty_total0` + sweep fault, checks of `6fafac6` untouched), `0982836` (the fix: `inventory_gap` gap on an API answer without a single cluster; docstrings), `c37afc9` (LIVE script: `empty_total_0` control), then this record.

RED on the frozen code (`7d30c15`: new tests, checks of `6fafac6`), [evidence/repair2-red-on-frozen-code.txt](evidence/repair2-red-on-frozen-code.txt): **5 failed, 210 passed** — `test_m6_consistent_empty_api_with_only_inert_legacy_clusters_is_unknown` (health 0 ≠ 1), `test_m7_q10_consistent_empty_api_with_inert_legacy_balance` (QA Q10 port: baseline `surplus 51 SSV`, `legacy cluster balances 0 (0 clusters)`), `test_m7_empty_api_listing_is_unknown_whatever_its_total[first-0]`, `[lost_database-0]` (a baseline of 101 SSV), `test_m7_empty_api_next_to_a_cluster_from_the_logs_is_unknown` (baseline 3 SSV, true −4 SSV); all assertion failures, none a collection error. Re-checked in a scratch copy (`git archive HEAD reconcile` + both checks from `6fafac6`): same 5 failed. Green at `0982836`.
- QA Appendix A copied verbatim to scratch `probes2/test_qa2_m7.py` (+ the attempt-1 `conftest.py`): on the `6fafac6` checks Q10 and Q11 fail; at this head **Q10 passes**, Q11 (N1, NOTE, out of scope) still fails.

| Verification ID | Exact command (`P=reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider`) | Exit / result | Head |
|---|---|---|---|
| V2x.1 M6 | `$P reconcile/tests/test_m6.py` | 0; 101 passed (45 + 56 sweep) | `c37afc9` |
| V2x.1 M7 | `$P reconcile/tests/test_m7.py` | 0; 114 passed (50 + 64 sweep) | `c37afc9` |
| V2x.2 | `$P reconcile/tests/test_m6.py reconcile/tests/test_m7.py -k thresholds_from_chain` | 0; 3 passed, 212 deselected | `c37afc9` |
| V2x.3 | same files `-k restart` | 0; 5 passed, 210 deselected | `c37afc9` |
| V2x.4 | same files `-k rpc_budget` | 0; 2 passed, 213 deselected (the fix adds no call) | `c37afc9` |
| reorg | same files `-k reorg` | 0; 21 passed, 194 deselected | `c37afc9` |
| sweep | same files `-k adversarial` | 0; 120 passed, 95 deselected | `c37afc9` |
| V2x.6 | `$P reconcile/tests` | 0; **455 passed**, 0 skipped | `c37afc9` |
| V2x.6 | `reconcile/.venv/bin/ruff check reconcile`; `ruff check --config reconcile/ruff.toml plans/monitoring-service/workers/cp2e/*.py` | 0; All checks passed | `c37afc9` |
| config | `$P reconcile/tests/test_framework.py -k config_example` | 0; 1 passed, 31 deselected | `c37afc9` |
| LIVE completeness (re-measured, guard changed) | `reconcile/.venv/bin/python plans/monitoring-service/workers/cp2e/live_completeness.py --rpc https://ethereum-rpc.publicnode.com --out <scratch>/live-completeness.json` | 0 (09:42:25Z–09:47:50Z); block **26,247,316**: InventoryCache 1,908 clusters (882 ETH, 1,026 legacy, 567 legacy active, 0 invalid, all from the API); `daoValidatorCount` **346** = listed **346**; `pagination.total` **1,908** = listing; **gap none** (the new leg is silent on mainnet: no false UNKNOWN); controls on the same block: empty page with the true total → gap (now also "listed no clusters"), **new** self-consistent empty page (total 0) → gap ("listed no clusters (pagination.total 0)" + dao 0 vs 346), one burning legacy row dropped → gap (306 vs 346; 1,907 of 1,908). [evidence/live-completeness-26247316.json](evidence/live-completeness-26247316.json) sha256 `d7c0bff4763e06c146d40914771822324e70cc342e3c7c89d842233f81bb4636` | `c37afc9` |
| V2e.7, LIVE M6/M7 ledger | — | **NOT RUN** (owner scope: long rows; QA attempt 1 measured both at 26,246,821; the fix touches no V2e.7 / ledger logic, only the guard, re-measured LIVE above) | — |
| Subgraph cross-check | — | DEFERRED (no `SSV_SUBGRAPH_API_KEY`; owner Marco) | — |

Falsifiers (scratch `falsify.py`: one exact-string mutation → named test(s) → `git checkout -- <file>` → rerun → `git status --porcelain`), code of `c37afc9`, porcelain 0 after each:

| ID | Mutation | Named test(s) | Mutated | Restored |
|---|---|---|---|---|
| R4a (new) | `if from_api == 0:` → `if False:` | Q10 port; M6 twin; logs-next-to-empty; `-k whatever_its_total` | 1 / 1 / 1 failed; 2 failed of 12 | all passed |
| R4b (new) | `if from_api == 0:` → `if len(inv.clusters) == 0:` | `test_m7_empty_api_next_to_a_cluster_from_the_logs_is_unknown` | 1 failed | 1 passed |
| F6a | `lag > warn_blocks` → `>=` | `test_m6_benign_liquidatable_exactly_half_window_stays_silent` | 1 failed | 1 passed |
| F6b | drop `and vc > 0` | `test_m6_benign_zero_balance_without_validators_stays_silent` | 1 failed | 1 passed |
| F6c | `self._rewind(db, ancestor)` → `pass` | `test_m6_reorg_forgets_a_clear_observation_on_deleted_blocks` | 1 failed | 1 passed |
| F7a | claims without `outstanding` | `test_m7_unstake_request_burns_cssv_and_is_counted_until_withdrawn` | 1 failed | 1 passed |
| F7b | `surplus < floor` → `<=` | `test_m7_benign_surplus_exactly_one_ssv_does_not_page` | 1 failed | 1 passed |
| F7d | `if gone:` → `if False:` | `test_m7_legacy_cluster_missing_from_the_inventory_is_unknown_not_a_smaller_liability` | 1 failed | 1 passed |
| R1b | `if listed_vc != dao:` → `if False:` | both `…_found_by_the_dao_validator_count` | 1 / 1 failed | passed |
| R1c | `elif len(inv.clusters) < total:` → `elif False:` | both `-k listing_short_of_its_total` | 1 failed of 2 each | 2 passed each |
| QA2-d | M7 `if gap:` → `if False:` | `test_m7.py` | 23 failed / 114 | 114 passed |

NOT RUN this attempt (code on their path unchanged; green at their named tests): falsifiers F7c, QF6d, QF7b, R1a, R1d, R1e, R2a, R3a–c, QA2-a/b/c/e/f/g.
- Resulting head: the CP2e status line in `plan.md` (this record's commit).
