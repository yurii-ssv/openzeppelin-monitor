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
| `plans/monitoring-service/workers/cp2a/*` | this record, the worker plan, `live-m4-probe-26241393.json` |

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

Environment for all rows: `reconcile/.venv/bin/python -m pytest -q …`, macOS, 2026-10-21. Counts at lane head
(after the coordinator's merge `33bf02d`).

| Verification ID | Exact command | Exit / result / counts | Evidence |
|---|---|---|---|
| V2x.1 M3 | `pytest -q reconcile/tests/test_m3.py` | RESULT_M3 | scenarios listed below |
| V2x.1 M4 | `pytest -q reconcile/tests/test_m4.py` | RESULT_M4 | |
| V2x.1 M14 | `pytest -q reconcile/tests/test_m14.py` | RESULT_M14 | |
| V2x.2 | `pytest -q reconcile/tests/test_m{3,4,14}.py -k thresholds_from_chain` (each file) | RESULT_V2 | |
| V2x.3 | `… -k restart` (each file) | RESULT_V3 | |
| V2x.4 | `… -k rpc_budget` (each file) | RESULT_V4 | M3 full sweep ≤ 3 × 350 eth_call and near-set run ≤ 3 × 5 with no inventory rebuild; M4 ≤ 350 + 40 + 2; M14 ≤ 4 per entry |
| V2a.7 | `pytest -q reconcile/tests/test_m14.py -k v2a7` | RESULT_V2A7 | same block, previous block, 2 blocks earlier (silent), value ≥ burn (silent), deposit after the failure (silent), order unknown, not liquidatable (silent), other failed tx (silent), liquidateSSV |
| V2x.5 | falsifiers below | 13 mutations, each named test red, restored, `git status --porcelain` empty | `scratchpad/falsifiers.log` (copied below) |
| V2x.6 | `pytest -q reconcile/tests` ; `reconcile/.venv/bin/ruff check reconcile` | RESULT_V6 | |
| LIVE M4 | `cd reconcile && .venv/bin/python -m checks.m4_probe --probe --rpc https://ethereum-rpc.publicnode.com` | exit 0, block 26,241,393 (hash `0x01303113…60bd`), 4 min 39 s | `live-m4-probe-26241393.json` |
| LIVE M3 views | scratch read-only script (not committed) running `checks.m3_views.evaluate` on the LIVE storage-validated inventory | RESULT_LIVE_M3 | |

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

FALSIFIERS

- Resulting head: HEAD_HASH
- Next action: Codex QA on the lane head.
