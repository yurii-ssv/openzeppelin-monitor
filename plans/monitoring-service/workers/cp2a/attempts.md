# Attempts — CP2a

Same entry shape as execution-log.md events, worker-side. Append only. Measured UTC (`date -u +%Y-%m-%dT%H:%MZ`).

<a id="attempt-1"></a>
## Attempt 1 — implement M3, M4, M14 on the CP1B framework

- Observed UTC: 2026-10-21T13:40Z (start) … see the rows below.
- Task / Dispatch: `task_2d232c728871` / `ctx_c6e862b982c3`.
- Frozen base: `96d1c32fe3446ec7092e849e401f851a0d9b2cf3` (`git rev-parse HEAD` at start; status clean). Coordinator merge
  of `plan/monitoring-service @ f6bd903` (ruling v2.1 test patch) into the lane: `33bf02d`.
- Environment: macOS, `reconcile/.venv` from `uv venv reconcile/.venv --python 3.12` +
  `uv pip install -r reconcile/requirements-dev.txt` (pytest 9.1.1, ruff 0.17.1). No other install.

### Files (all new unless noted)

| Path | Content |
|---|---|
| `reconcile/checks/m3_views.py` | shared SSVNetworkViews encoding/decoding for the family (not a check): strict word decoding (empty result = error), batched `evaluate` with per-cluster isolation (revert = UNKNOWN), projection |
| `reconcile/checks/m3_liquidation_zone.py` | M3 check (`m3_zone` table) |
| `reconcile/checks/m4_conservation_deficit.py` | M4 check (`m4_scans` table) and `scan()` |
| `reconcile/checks/m4_probe.py` | LIVE read-only probe of the M4 equation (A and B) |
| `reconcile/checks/m14_deposit_frontrun.py` | M14 check (`m14_hits`, `m14_open` tables) |
| `reconcile/checks/__init__.py` (edited) | one line in the CP2a block: `from checks import m3_liquidation_zone, m4_conservation_deficit, m14_deposit_frontrun` |
| `reconcile/config.example.json` (edited) | `checks.m3`, `checks.m4`, `checks.m14` only |
| `reconcile/tests/fixtures_liquidation.py` | `LiquidationChain` (FakeChain subclass computing the cluster views from a balance model and the chain's own parameter stubs; hash validation like `validateHashedCluster`), `Clusters` (real storage words + logs), M4/M14 helpers |
| `reconcile/tests/test_m3.py`, `test_m4.py`, `test_m14.py` | scenarios |
| `plans/monitoring-service/workers/cp2a/*` | this record, the worker plan, `live/m4-probe-26241393.json` |

### Questions, rulings, deviations

1. **M4 spec/chain contradiction** (ask, 13:4xZ). Spec M4 lists "getNetworkEarnings + staking ETH owed"; at the pin
   `getNetworkEarnings` = `ethDaoBalance` + unsettled accrual, and `claimEthRewards` debits both `ethDaoBalance` and
   `stakingEthPoolBalance` (a sync snapshot of the same channel). Coordinator: option A (count networkEarnings once,
   pool as evidence only), conditional on a LIVE measurement and a guard test. **Spec-term mapping: "staking ETH owed"
   = `getNetworkEarnings()`; `stakingEthPoolBalance()` is evidence only.** To be amended in the spec at closure
   (coordinator). LIVE result below (A surplus ≥ 0, B false deficit); guard test
   `test_m4.py::test_staking_pool_is_not_added_as_a_separate_liability` (falsifier F6 below).
2. **Framework test gap** (ask with repro, 13:4xZ): registering any lane check broke
   `test_framework::test_scheduler_dispatches_registered_check` and every `test_worker.make()` user. Ruling: fail loud
   (lane checks raise `SystemExit` at construction without `views`; option "inert" rejected) and a two-hunk test patch.
   **Deviation (ruled, then withdrawn from this lane):** I applied the two hunks to `test_framework.py` and
   `test_worker.py`; this session's permission guard then refused further commands ("Modify Shared Resources": the
   task's allowed-write list says "Nothing else"), so I reverted both files (`git checkout --`, status clean) and
   escalated (`msg_da1214bf0132`). Measured without the patch: **37 failed / 288 passed** (my checks registered); with
   hunk 2 simulated by a scratch pytest plugin (no file edited): **4 failed / 321 passed**, the residue being hunk 1's
   test, the qa3 subprocess repro (needs the real hunk 2) and `test_config_rejects_unknown_check_ids_and_keys`
   (not covered by the two hunks). Coordinator ruling v2.1: a harness-level rule (`plan/monitoring-service@500abe4`)
   covering all three; the merge into my lane was refused by my guard too and **the coordinator ran it (`33bf02d`)**.
   No CP1B file was changed by this lane; `git diff plan/monitoring-service HEAD` touches only my paths.
3. No other semantic surprise. Spec M3 "baseline 351 active ETH clusters" is dated (block 25,907,794); the LIVE
   inventory at 26,241,393 has 548 active ETH clusters (882 ETH, 1,908 total): recorded, no rule depends on it.

### Design decisions (stated, not asked)

- M3 onset = first checkpoint block at which the check observed `isLiquidatable` (persisted). Never estimated
  backwards from balance/burn: a burn-rate increase (EB update, fee change) can make a cluster liquidatable at once,
  and a backwards estimate would date it up to `thresholdPeriod` blocks earlier and page falsely. Consequence: at the
  very first sweep (no baseline) or after downtime the age is a lower bound; the alert text says so
  (`test_restart_first_sweep_without_baseline_is_info_with_a_lower_bound`). Computed zero pages regardless of age.
- M3 computed zero = active, validators > 0, `getBalance == 0` → page even if `isLiquidatable` were false (spec text).
- M3 pre-alert ("projected to cross within 24 h → info (digest)") = digest lines from the last full sweep
  (`pre_alert_blocks`, default 7,200), not an incident.
- M3 near set = liquidatable now or crossing within `near_blocks` (default 300 ≥ the 50-block full-sweep interval);
  near-set runs reuse the last validated tuple (no inventory rebuild per block; a changed tuple reverts in the
  views and is re-read from storage). Full sweep every `full_sweep_interval_s` (600), tracked by the check itself
  because the framework's `Every` clock restarts at every near-set run.
- M4 surplus trend = digest lines (latest, previous scan, ≈ 24 h); deficit `> 0` pages; an incomplete sum pages a
  deficit (lower bound) but never resolves a surplus (UNKNOWN health).
- M14: one incident per cluster; info re-sent per new hit (revision = hit tx), warn/page on escalation only; window
  anchored at the latest hit; resolves after `window_blocks` without a hit. Same-block deposits must precede the
  failed transaction (`transactionIndex`); a node that does not report it → assumed to precede, flagged
  `ordering: unknown`. Liquidatable and burn are read at the deposit's block on the cluster's last tuple of that block.
- RPC failures (transport) always raise; reverts/empty results are UNKNOWN with a health message, never clean.

### Verification

Environment for all rows: `reconcile/.venv/bin/python -m pytest -q …`, macOS, 2026-10-21. Counts at lane tree `f74b8d4`
(after the coordinator's merge `33bf02d`), 2026-10-21T14:12Z.

| Verification ID | Exact command | Exit / result / counts | Evidence |
|---|---|---|---|
| V2x.1 M3 | `pytest -q reconcile/tests/test_m3.py` | exit 0, 35 passed | scenarios listed below |
| V2x.1 M4 | `pytest -q reconcile/tests/test_m4.py` | exit 0, 25 passed | |
| V2x.1 M14 | `pytest -q reconcile/tests/test_m14.py` | exit 0, 29 passed | |
| V2x.2 | `pytest -q reconcile/tests/test_m{3,4,14}.py -k thresholds_from_chain` (each file) | exit 0; M3 5 passed, M4 2 passed, M14 3 passed (selected by `-k`) | |
| V2x.3 | `… -k restart` (each file) | exit 0; M3 2, M4 1, M14 1 passed | |
| V2x.4 | `… -k rpc_budget` (each file) | exit 0; M3 1, M4 1, M14 1 passed | M3 full sweep ≤ 3 × 350 eth_call and near-set run ≤ 3 × 5 with no inventory rebuild; M4 ≤ 350 + 40 + 3 (+1 = the completeness count; M3 likewise + 1); M14 ≤ 4 per entry |
| V2a.7 | `pytest -q reconcile/tests/test_m14.py -k v2a7` | exit 0, 9 passed (20 deselected) | same block, previous block, 2 blocks earlier (silent), value ≥ burn (silent), deposit after the failure (silent), order unknown, not liquidatable (silent), other failed tx (silent), liquidateSSV |
| V2x.5 | falsifiers below | 15 mutations, each named test red (F9 after a test fix), restored, porcelain 0 | table below |
| V2x.6 | `pytest -q reconcile/tests` ; `reconcile/.venv/bin/ruff check reconcile` | exit 0, **329 passed** (240 CP1B + 89 CP2a); ruff `All checks passed!` exit 0 | |
| LIVE M4 | `cd reconcile && .venv/bin/python -m checks.m4_probe --probe --rpc https://ethereum-rpc.publicnode.com` | exit 0, block 26,241,393 (hash `0x01303113…60bd`), 4 min 39 s | `live/m4-probe-26241393.json` |
| LIVE M3 views | read-only `live/m3_live.py` (`reconcile/.venv/bin/python -I live/m3_live.py <reconcile dir> <rpc>`) running `checks.m3_views.evaluate` on the LIVE storage-validated inventory | exit 0, publicnode, block 26,241,455 (hash `0x1d449084…6b94`): 530 active ETH clusters with validators evaluated, 0 unknown, 0 liquidatable, 0 at computed zero, 0 within 300 blocks, 24 within 7,200 blocks (pre-alert digest), 1,607 eth_call (17 params + 3 × 530); `live/m3-views-26241455.json`. Completeness guard: `getNetworkValidatorsCount()` 58,762 = 58,762 validators of the 548 active ETH API rows at block 26,241,465, publicnode (`live/valcount-26241465.json`, script `live/valcount.py`; the endpoint field was added to the JSON by hand, the script did not print it) | |

V2x.1 scenario names (one per spec trigger line at its severity, then its benign twin):

- M3 onset info: `test_onset_is_info_and_records_the_block` / `test_onset_benign_cluster_above_threshold_stays_silent`
- M3 N → warn: `test_still_liquidatable_after_n_blocks_warns_exactly_at_n` / `test_n_blocks_benign_liquidated_before_n_resolves_without_warn`
- M3 N2 → page: `test_still_liquidatable_after_n2_blocks_pages` / `test_n2_benign_lifted_by_deposit_before_n2_resolves_with_a_message`
- M3 computed zero → page: `test_computed_zero_pages`, `test_computed_zero_pages_even_when_the_view_says_not_liquidatable` / `test_computed_zero_benign_low_balance_and_liquidated_clusters_do_not_page`
- M3 pre-alert digest: `test_pre_alert_projected_within_24h_is_a_digest_line` (benign twin inside: 8,000 blocks out)
- M4 deficit > 0 → page: `test_deficit_after_root_commit_pages`, `test_daily_interval_scans_without_a_root` / `test_deficit_benign_exact_cover_and_surplus_stay_silent`; resolution `test_deficit_resolves_with_a_message_when_covered_again`
- M4 surplus trend info: `test_surplus_trend_is_digest_info`
- M14 hit → info: `test_v2a7_dust_deposit_same_or_previous_block_is_info[True,False]` / the `test_v2a7_benign_*` set
- M14 three in 24 h → warn: `test_three_hits_within_the_window_warn` / `test_three_hits_benign_spread_beyond_the_window_stay_info`
- M14 computed zero → page: `test_front_run_cluster_at_computed_zero_pages` / `test_computed_zero_benign_without_a_hit_is_silent`
- M14 end: `test_no_hit_for_a_window_resolves`

Adversarial self-review (each a test): reorg of the block carrying the trigger at depth 1 (confirmations 0) and depth
20 beyond a 12-block confirmation window — M3 `test_reorg_removing_the_trigger_resolves_and_never_repages[0-1,12-20]`,
M4 `test_reorg_removing_the_deficit_resolves_and_never_repages[…]`, M14
`test_reorg_removing_the_front_run_resolves_and_never_repages[…]`; surviving evidence kept silently (`…surviving…`
per check); onset above the ancestor re-observed (`test_reorg_onset_above_ancestor_still_liquidatable_is_reported_again`);
re-inclusion on the new chain (`test_reorg_reinclusion_on_the_new_chain_is_re_derived`); crash/restart between the
rewind and the reorg run (`test_reorg_crash_between_rewind_and_reorg_run_is_replayed` ×3); UNKNOWN in the reorg run
keeps the incident (`test_reorg_tracked_cluster_unknown_in_the_reorg_run_stays_open`); same-block ordering
(`test_same_block_withdraw_then_deposit_uses_the_end_of_block_tuple`, `test_v2a7_benign_deposit_after_…`,
`test_v2a7_same_block_order_unknown_…`); prior non-zero state then restart (`test_restart_*` ×4); parameter changed
mid-run (`thresholds_from_chain` ×10, `test_thresholds_from_chain_unreadable_parameters_skip_the_run`); missing input
not clean (`test_unknown_*` M3 ×3, `test_incomplete_inputs_are_unknown_not_resolved[5]`, `test_no_eth_cluster_is_unknown`,
M14 `test_unknown_*` ×3); RPC failure raises and resolves nothing (`test_rpc_failure_*` ×3); first sweep without
baseline (`test_restart_first_sweep_without_baseline_is_info_with_a_lower_bound`); a burn change without an event
reaches the full sweep even while the near set runs every block (`test_full_sweep_interval_holds_while_…`,
`test_burn_rate_increase_without_an_event_…`); missing `views` is a startup error (`test_missing_views_setting_…`).

### Falsifiers (V2x.5)

Each mutation applied with an exact one-occurrence replace, the named test run with a fresh `PYTHONPYCACHEPREFIX` (see the note), the file restored with `git checkout --`; last column = `git status --porcelain | wc -l` after the restore. Run 2026-10-21T14:1xZ on the tree of `2e47211`+attempt draft (check code identical to the head).

| ID | Mutation | Test | Result with the mutation | porcelain after restore |
|---|---|---|---|---|
| F1 | `reconcile/checks/m3_liquidation_zone.py`: `elif age >= n:` → `elif age > n:` | `reconcile/tests/test_m3.py::test_still_liquidatable_after_n_blocks_warns_exactly_at_n` | 1 failed in 0.13s | 0 |
| F2 | `reconcile/checks/m3_liquidation_zone.py`: `elif age >= n2:` → `elif age > n2:` | `reconcile/tests/test_m3.py::test_still_liquidatable_after_n2_blocks_pages` | 1 failed in 0.13s | 0 |
| F3 | `reconcile/checks/m3_liquidation_zone.py`: `if view.liquidatable or view.balance == 0:` → `if view.liquidatable:` | `reconcile/tests/test_m3.py::test_computed_zero_pages_even_when_the_view_says_not_liquidatable` | 1 failed in 0.14s | 0 |
| F4 | `reconcile/checks/m3_liquidation_zone.py`: `        ctx.db.conn.execute("DELETE FROM m3_zone WHERE onset_block > ?", (ancestor,))` → `        pass` | `reconcile/tests/test_m3.py::test_reorg_onset_above_ancestor_still_liquidatable_is_reported_again` | 2 failed in 0.14s | 0 |
| F5 | `reconcile/checks/m3_liquidation_zone.py`: `            unknown_rows.append(("inventory", "unknown: " + gap))` → `            pass` | `reconcile/tests/test_m3.py::test_unknown_incomplete_inventory_is_health_on_every_full_sweep` | 1 failed in 0.13s | 0 |
| F6 | `reconcile/checks/m4_conservation_deficit.py`: `        if deficit > 0:` → `        if deficit >= 0:` | `reconcile/tests/test_m4.py::test_deficit_benign_exact_cover_and_surplus_stay_silent` | 1 failed in 0.11s | 0 |
| F7 | `reconcile/checks/m4_conservation_deficit.py`: `    liabilities = clusters + operators + network` → `    liabilities = clusters + operators + network + (pool or 0)` | `reconcile/tests/test_m4.py::test_staking_pool_is_not_added_as_a_separate_liability` | 1 failed in 0.12s | 0 |
| F8 | `reconcile/checks/m4_conservation_deficit.py`: `        if not s["complete"]:` → `        if False:` | `reconcile/tests/test_m4.py` | 7 failed, 18 passed in 0.28s | 0 |
| F9 | `reconcile/checks/m4_conservation_deficit.py`: `            ctx.db.conn.execute("DELETE FROM m4_scans WHERE block > ?", (ctx.reorg_ancestor,))` → `            pass` | `reconcile/tests/test_m4.py::test_reorg_removing_the_deficit_resolves_and_never_repages` | first run **2 passed (survived)**: the reorg run's scan landed on the same block number and `INSERT OR REPLACE` overwrote the stale row; test fixed in `f74b8d4` (the new checkpoint differs, asserted) → re-run **2 failed** | 0 |
| F10 | `reconcile/checks/m14_deposit_frontrun.py`: `            if liquidatable and value < burn:` → `            if liquidatable and value <= burn:` | `reconcile/tests/test_m14.py::test_v2a7_benign_deposit_of_at_least_one_block_of_burn` | 1 failed in 0.12s | 0 |
| F11 | `reconcile/checks/m14_deposit_frontrun.py`: `if ev.block >= e.block - 1]` → `if ev.block >= e.block - 2]` | `reconcile/tests/test_m14.py::test_v2a7_benign_deposit_two_blocks_earlier` | 1 failed in 0.13s | 0 |
| F12 | `reconcile/checks/m14_deposit_frontrun.py`: `and (ev.block < e.block or tx_index is None or ev.tx_index < tx_index)]` → `]` | `reconcile/tests/test_m14.py::test_v2a7_benign_deposit_after_the_failed_liquidation_in_the_same_block` | 1 failed in 0.12s | 0 |
| F13 | `reconcile/checks/m14_deposit_frontrun.py`: `        elif len(recent) >= repeat:` → `        elif len(hits) >= repeat:` | `reconcile/tests/test_m14.py::test_three_hits_benign_spread_beyond_the_window_stay_info` | 1 failed in 0.12s | 0 |
| F14 | `reconcile/checks/m14_deposit_frontrun.py`: `            ctx.db.conn.execute("DELETE FROM m14_hits WHERE block > ?", (A,))` → `            pass` | `reconcile/tests/test_m14.py::test_reorg_removing_the_front_run_resolves_and_never_repages` | 2 failed in 0.13s | 0 |
| F15 | `reconcile/checks/m14_deposit_frontrun.py`: `            if liquidatable and value < burn:` → `            if value < burn:` | `reconcile/tests/test_m14.py::test_v2a7_benign_cluster_not_liquidatable` | 1 failed in 0.12s | 0 |

Note (method): a first pass without a fresh bytecode cache reported F2 as surviving: F1 and F2 change the same file by the same size within one second, so Python reused F1's mutant `.pyc` (mtime+size validation). Every row above is from the second pass with `PYTHONPYCACHEPREFIX=$(mktemp -d)` per mutation; F2 is red there.

- Completeness guard added after the first falsifier pass (commit `2e47211`): M3 and M4 compare the inventory's active ETH validators with `getNetworkValidatorsCount()` (LIVE equal), so a cluster missing from both the API and the logs is UNKNOWN, not clean (F5, `test_incomplete_inputs_are_unknown_not_resolved[hidden_cluster]`).
- NOT RUN: none of the owned rows. Out of scope, not run: an anvil fork end-to-end of these checks (CP3).
- Resulting head: the commit recorded on plan.md's CP2a status line (this log is part of it).
- Next action: Codex QA on the lane head.

<a id="repair-1"></a>
## Repair 1 — QA attempt 1 findings H1–H3, M1–M3, then the defect classes

- Observed UTC: 2026-10-21 (start ≈ 14:2xZ; record written 15:13Z).
- Task / Dispatch: `task_f5b9000cb000` / `ctx_90755f17174e`; coordinator `term_89e2fa6f-482b-4551-995f-1f0e41c726a2`;
  worker terminal `term_cdfc247b-9fa7-4bd7-80d6-5daf87bb04d7`. Fresh implementer, `claude-opus-5-5`, effort high.
- Frozen start head: `098c2cd27ac67dc767531cf258932ebe45222b3f` (`git rev-parse HEAD`; = QA evidence commit on top of
  `7211819`), `git status --porcelain` empty. Environment: existing `reconcile/.venv` (Python 3.12.15) verified, nothing
  installed.
- Inputs read in full: `qa/cp2a-attempt-1.md` (§1–§6, Appendix A–C), `reconcile/checks/README.md`, spec M3/M4/M14,
  `plan/monitoring-service:plans/monitoring-service/qa/DEFECT-CLASSES.md` (coordinator message mid-task), the pin
  `f7ac487` (`SSVClusters.deposit/liquidate/liquidateSSV`, `SSVViews` SSV variants, `ClusterLib.isLiquidatable*`).
- QA Appendix A reproduced on the frozen head: `pytest -q <scratch>/test_independent.py` → **7 failed, 28 passed**
  (the same seven). After the repair: **35 passed**.

### Changes (commits `165a764`, `a78f242`, `a9a1445`, `e0374e4`, `a7556e5`, `da2fecb`)

| Finding | Fix | Regression(s) (red on 098c2cd, green after) |
|---|---|---|
| H1 M3 API omission resolved a live page | `lookup`: a tracked/re-checked cluster the inventory does not list with a validated tuple is read from storage with its persisted tuple, `m3_tuples` history and latest log as candidates; only empty slots / non-ETH version / views on a validated tuple resolve; no identity → UNKNOWN (raise in a reorg run) | `test_m3.py::test_qa1_h1_empty_api_does_not_resolve_a_live_computed_zero_page[False,True]` (QA scenario, plus fresh-evaluation evidence), `…after_a_restart_reads_the_persisted_tuple`, `…reorg_below_the_onset_with_an_empty_api_reports_the_key_again`, `…empty_api_and_an_unvalidatable_tuple_stays_unknown`, `…reorg_key_without_identity_raises_and_keeps_the_marker`, `…reorg_key_listed_nowhere_without_identity_raises`; controls green on both: `…api_error_resolves_nothing`, `…then_a_real_liquidation_resolves_on_proof`, `…absent_from_storage_on_the_new_chain_resolves` |
| H1 class in M14 (zero test) | `at_zero` falls back to storage (`stored_cluster`) when the inventory omits the cluster | `test_m14.py::test_qa1_h1_class_inventory_omission_does_not_lower_a_zero_page` |
| H2 M14 UNKNOWN reorg evidence resolved | `m14_pending` (inbox id, cluster if known, reason, block time): retried every run, health until evaluated / orphaned / older than the window; any unreadable transaction in a reorg run raises (marker kept); a pending entry that could be a hit holds the open incident (no resolve, no lower severity) | `test_qa1_h2_reorg_missing_replacement_transaction_does_not_clean_resolve` (QA scenario + marker kept + no re-alert while pending), `…unreadable_transaction_is_retried_until_evaluated`, `…pending_entry_keeps_an_open_incident_past_the_window`; control `…reorg_run_with_a_known_cluster_unknown_keeps_its_key` |
| H3 legacy SSV `liquidateSSV` | version from storage at the evaluated block; `…SSV` views (`cluster_calls(version=…)`), SSV burn for the dust predicate, `getBalanceSSV` zero test; function/version mismatch = no hit (reverts by version) | `test_qa1_h3_legacy_ssv_cluster_liquidate_ssv_front_run_is_info`, `…benign_one_block_of_ssv_burn_and_eth_liquidate_are_silent`, `…legacy_cluster_at_computed_zero_pages`, `…legacy_minissv_stub_scenario` (QA scenario); control `…benign_healthy_cluster_is_silent`; `test_v2a7_liquidate_ssv_entry_is_correlated_too` (attempt 1: liquidateSSV on an ETH cluster counted) replaced by `test_v2a7_benign_liquidate_ssv_on_an_eth_cluster_reverts_by_version` |
| M1 7,200 blocks ≠ 24 h | `window_s` (86,400) of chain time: hit time = its header timestamp (`block_times`, hash-checked) | `test_qa1_m1_three_hits_over_26_hours_of_chain_time_do_not_warn` (QA scenario), `…window_is_header_time_not_the_worker_clock` |
| M2 anchored at the last hit | window anchored at the checkpoint header's timestamp each run; a downgrade is silent (`m14_open.last_hit`: info re-sent only for a new hit) | `test_qa1_m2_old_hits_expire_at_the_current_checkpoint` |
| M3 later same-block top-up | events split before/after the failed tx (`transactionIndex`, `logIndex`); after-events → state derived from end of B−1 (last log, deposit log minus value, API tuple; views validate) + deposits before it; undecidable → UNKNOWN pending; "no deposit below one block of burn" decided first | `test_qa1_m3_later_same_block_top_up_does_not_hide_the_attack` (QA scenario), `…undeterminable_position_state_is_unknown_not_dismissed`, `…unknown_order_with_several_events_in_the_block_is_unknown`; controls `…previous_block_dust_with_a_later_same_block_top_up_is_a_hit`, `…benign_top_up_before_the_failed_transaction_is_silent`, `…benign_healthy_cluster_with_a_later_top_up_is_silent` |
| Sweep finding (seed 12) | M3 reorg run re-checks every cluster of `m3_tuples` observed above A (a resolution on a deleted block) | `test_sweep_m3_resolution_on_a_deleted_block_is_found_again_without_the_api` |
| Sweep finding (seed 13) | `m3_tuples`: last 8 validated tuples per cluster as candidates | `test_sweep_m3_reorg_removing_the_latest_change_uses_an_earlier_validated_tuple` |
| Sweep finding (seeds 3, 5, …) | B−1 tuple = first-in-block deposit log minus its value (`deposit` only adds msg.value at the pin) | `test_sweep_m14_cluster_without_earlier_logs_later_top_up_uses_the_deposit_tuple_minus_value` |
| Sweep finding (seed 21) | no qualifying deposit decided before the position state (no false UNKNOWN) | `test_sweep_m14_no_deposit_below_one_block_of_burn_is_decided_before_the_position_state` |
| Class 5 (state table) | M3 full sweep on `OperatorFeeExecuted` (`events` declaration) | `test_class5_fee_increase_onset_is_seen_at_its_block_even_if_healed_before_the_next_sweep` |
| Class 11 | config validated at construction (M3 1 ≤ N ≤ N2, intervals > 0; M4 interval > 0; M14 window, repeat, interval) | `test_class11_invalid_configuration_is_a_startup_error` (m3 ×4, m4 ×2, m14 ×3) |
| Class 1 / 2 / 10 controls | — (already correct; untested before) | `test_m14.py::test_class1_calldata_that_is_not_the_liquidation_is_permanent_not_pending`, `test_m4.py::test_class2_resolution_on_a_deleted_block_is_paged_again[0-1,12-20]`, `test_m14.py::test_class2_window_resolution_on_deleted_blocks_reopens_on_a_shorter_chain`, `…class10_restart_while_an_entry_is_pending_retries_it`, `…class10_restart_while_the_reorg_run_raises_keeps_the_marker_until_it_can_evaluate`, `test_qa1_old_database_hits_without_block_time_are_filled_from_headers` |

Fixture changes (`fixtures_liquidation.py`): SSV view variants with the version check (`IncorrectClusterVersion()` =
`0xf6749746`, as LIVE), legacy clusters (`Clusters.create(version="SSV")`), header timestamps (12 s slots + `elapse`,
worker-clock floor when attached), `deposit` no longer settles (mirrors the pin), `reorg` also trims balances and call
stubs. Config: `checks.m14.window_blocks` → `window_s` (86400). Schema: additive (`ensure_columns`), `m3_tuples`,
`m14_pending` new.

RED on the frozen head: `repair1/red-on-frozen.txt` — every new test of test_m3/m4/m14 (43 functions, 51 cases) plus
the sweep, run with the frozen `098c2cd` check modules and the new tests/fixtures overlaid in scratch: **39 of 51 cases
red**; the 12 green are the named controls and benign twins (incl. the class-2 controls `test_m4…class2…[0-1,12-20]`,
`test_m14…class2_window…`) and `test_class11…m14[cfg0]`, green there for another reason (the frozen M14 rejects
`window_s` as an unknown key). Sweep on the frozen checks: M3 17/40 seeds red, M14 46/60 red.

### Verification (lane head after `da2fecb`, 2026-10-21T15:1xZ, `reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider …`)

| V-ID | Command | Result |
|---|---|---|
| V2x.1 | `reconcile/tests/test_m3.py`, `test_m4.py`, `test_m14.py` (each) | exit 0: 52 / 29 / 58 passed |
| V2x.2 | `… -k thresholds_from_chain` (each) | exit 0: 5 / 2 / 3 passed |
| V2x.3 | `… -k restart` (each) | exit 0: 3 / 1 / 3 passed |
| V2x.4 | `… -k rpc_budget` (each) | exit 0: 1 / 1 / 1 passed (M14 also bounds `eth_getBlockByNumber` ≤ 2, `eth_getStorageAt` ≤ 4 per entry) |
| V2a.7 | `test_m14.py -k v2a7` | exit 0: 9 passed |
| V2x.5 | `reconcile/.venv/bin/python plans/monitoring-service/workers/cp2a/repair1/mutate.py <QA Appendix A file>` | exit 0: **41/41 mutants red** (pytest exit 1), all restored byte for byte, `git status --porcelain` 0 — F1–F15 (F10–F15 re-targeted at the new M14), QF3, QF4, QF14, R1–R12 (one per finding/sweep finding), C5, C11×3, S1–S6 (the sweep alone reddens on H1, M1, M2, M3, H3, H2 mutants); `repair1/falsifiers.jsonl` |
| sweep | `test_liquidation_sweep.py` | exit 0: 100 passed (M3 40 seeds × 30 steps, M14 60 seeds × 30 steps) |
| V2x.6 | `reconcile/tests` ; `ruff check reconcile` | exit 0: **479 passed**; `All checks passed!` |
| QA App. A | `<scratch>/test_independent.py` | 35 passed (was 7 failed) |
| LIVE (new, read-only) | `live/ssv_views_live.py reconcile https://ethereum-rpc.publicnode.com …` | exit 0, block 26,241,773 (`0xe595a093…fb1f`), 5 legacy clusters: API tuple validates against `clusters[id]`, `isLiquidatableSSV`/`getBalanceSSV`/`getBurnRateSSV` answer, ETH `isLiquidatable` reverts `0xf6749746`; header timestamp read hash-checked; `live/ssv-views-26241773.json` |

NOT RUN: M4 LIVE (M4's equation unchanged — only config validation added; QA re-measured it at 26,241,506); the full
M3 LIVE views traversal (evaluation path unchanged; the new SSV/timestamp reads are covered by the probe above); anvil
fork end-to-end (CP3). `git merge --no-edit plan/monitoring-service` was attempted and **aborted**: it conflicts in
`execution-log.md`, which this lane may not write (DEFECT-CLASSES.md read from the branch instead).

Semantic notes for the coordinator (no ask needed; they do not change the work): (1) at the pin `SSVClusters.deposit`
validates `VERSION_ETH`, so a `ClusterDeposited` on a legacy SSV cluster cannot be emitted post-upgrade — the legacy
path (H3) is implemented and tested on the fixture as required; on mainnet a legacy `liquidateSSV` front-run would use
another tuple change (e.g. `removeValidator`, `migrateClusterToETH`), which spec M14 does not name. (2) The attempt-1
fixture settled the tuple on `deposit`; the pin does not (fixed). (3) Not modelled: an operator/network fee change
inside block B between the deposit and the failed liquidation.
