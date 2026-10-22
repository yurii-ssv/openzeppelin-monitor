# Worker: CP2a — Liquidation family (M3, M4, M14)

Task contract: [plan.md §8 CP2a](../../plan.md) · Attempt log: [attempts.md](attempts.md) · QA contract: [qa/QA-CONTRACT.md](../../qa/QA-CONTRACT.md)

- Actual starting worktree/branch/head/status: `/Users/marco/orca/workspaces/ssv-network-monitor/ms-cp2a`, branch `mtabasco/ms-cp2a`, head `96d1c32fe3446ec7092e849e401f851a0d9b2cf3` (= frozen base `plan/monitoring-service @ 96d1c32`), `git status --porcelain | wc -l` = 0 (2026-10-21T13:40Z).
- Live authority: Orca task `task_2d232c728871`, dispatch `ctx_c6e862b982c3`, coordinator `term_89e2fa6f-482b-4551-995f-1f0e41c726a2`, worker terminal `term_8d7dddc9-5b3e-432e-8ca1-91caa786f7a4`.
- Requested/effective model and effort: requested Claude Opus high (task packet); effective in this session: `claude-opus-5-5` (the start receipt is held by the coordinator).
- Allowed writes: `reconcile/checks/m3_*.py`, `m4_*.py`, `m14_*.py`, `reconcile/tests/test_m3.py`, `test_m4.py`, `test_m14.py`, `reconcile/tests/fixtures_liquidation.py`, `reconcile/config.example.json` (keys `checks.m3/m4/m14` only), one line in the CP2a block of `reconcile/checks/__init__.py` (one import line for the three modules), `plans/monitoring-service/workers/cp2a/**`, the CP2a status line in plan.md. Commits on the lane branch only; no push; mainnet read-only; record mode / TEST_* only.
- Observable completion and owned verification IDs: A3; V2x.1–V2x.6 for M3, M4, M14 (V2x.4 `rpc_budget` applies) and V2a.7 (M14 correlation of an inbox revert with a `ClusterDeposited` below one block of burn in the same or previous block).
- Dependencies and sources: plan §1, §2 (D5), §6; `reconcile/checks/README.md` (interface, §6.1/§6.2 reorg contract, inbox gate); spec M3, M4, M14, §4, §5; `ssv-network-triage/evidences/#89748/report-staking-insolvency-impact.ts`, `…/mainnet-self-operators/followup-20261020/followup.mjs`; contracts at the pin `f7ac487` (SSVViews, SSVClusters, SSVStaking, ProtocolLib, ClusterLib, SSVStorage).
- Commit capability checked: yes — `git config user.name` = Marco Tabasco, lane branch `mtabasco/ms-cp2a`.

| Step | Status | Latest evidence | Exact next action |
|---|---|---|---|
| S1 setup (venv, reading) | DONE | attempts §1 | — |
| S2 M3 check + tests | DONE | `21f1f91`, `756237b`, `2e47211` | — |
| S3 M4 check + tests + LIVE probe | DONE | `21f1f91`, `f74b8d4`; `live/m4-probe-26241393.json` | — |
| S4 M14 check + tests | DONE | `21f1f91` | — |
| S5 V rows, falsifiers, self-review | DONE | attempts §Verification, §Falsifiers | — |
| S6 commit, status, worker_done | DONE | plan.md CP2a status line | Codex QA |

## Design (as implemented)

See the module docstrings of `reconcile/checks/m3_liquidation_zone.py`, `m4_conservation_deficit.py`,
`m14_deposit_frontrun.py` (each states its triggers, inputs, UNKNOWN handling and its reorg rule per table).

## Completion handoff (≤ 250 words, also sent as the `worker_done` summary)

```text
Task / live dispatch: task_2d232c728871 / ctx_c6e862b982c3 (CP2a, M3 M4 M14).
Assignment outcome: implemented; acceptance pending Codex QA.
Base -> result: 96d1c32 -> lane mtabasco/ms-cp2a head (plan.md CP2a status line); coordinator merge 33bf02d (ruling v2.1).
Changed paths: reconcile/checks/{m3_views,m3_liquidation_zone,m4_conservation_deficit,m4_probe,m14_deposit_frontrun}.py,
  one line in checks/__init__.py, checks.m3/m4/m14 in config.example.json, tests/{fixtures_liquidation,test_m3,test_m4,
  test_m14}.py, workers/cp2a/**, CP2a status line. No CP1B file changed by this lane; no deletions.
Verification: V2x.1 M3 35 / M4 25 / M14 29 passed; V2x.2 10; V2x.3 4; V2x.4 3; V2a.7 9; V2x.6 329 passed, ruff clean.
  LIVE (publicnode): M4 A surplus +457,600,000 wei vs B -208.6 ETH at 26,241,393; M3 views 530 clusters, 0
  liquidatable, 0 zero at 26,241,455; validators 58,762 = 58,762. NOT RUN: none.
Falsifiers: 15, all red (F9 after a test fix), restored, porcelain 0.
Findings: M4 spec term "staking ETH owed" = getNetworkEarnings (ruled A; spec amendment at closure).
  Framework test gap resolved centrally (500abe4). Permission guard blocked my edits of CP1B tests and the merge.
Pending human input: none.
Next owner: Codex QA on the lane head.
Preservation: everything committed; scratch only in the session scratchpad.
```

---

## Repair 1 (QA attempt 1 FAIL → fixes H1–H3, M1–M3; DEFECT-CLASSES.md)

- Task / dispatch: `task_f5b9000cb000` / `ctx_90755f17174e`; coordinator `term_89e2fa6f-482b-4551-995f-1f0e41c726a2`;
  worker `term_cdfc247b-9fa7-4bd7-80d6-5daf87bb04d7`; fresh implementer `claude-opus-5-5`, effort high.
- Frozen start head `098c2cd27ac67dc767531cf258932ebe45222b3f` (clean); worktree/branch unchanged.
- Log: [attempts.md § Repair 1](attempts.md#repair-1) (changes, RED-on-frozen, V rows, falsifiers, LIVE, NOT RUN).

| Step | Status | Evidence |
|---|---|---|
| R1 reproduce QA Appendix A on the frozen head | DONE | 7 failed / 28 passed → after: 35 passed |
| R2 fix H1–H3, M1–M3 + port each reproducer | DONE | `165a764` |
| R3 state table, seeded sweep, own trigger ledger | DONE | below; `test_liquidation_sweep.py`; `a78f242`, `a9a1445`, `e0374e4` |
| R4 RED-on-frozen, V rows, falsifiers, LIVE probe | DONE | `repair1/red-on-frozen.txt`, `repair1/falsifiers.jsonl`, `live/ssv-views-26241773.json` |
| R5 record, status line, worker_done | DONE | this section; plan.md CP2a status |

### Spec-derived trigger ledger (from the spec text, not the plan summary)

| Spec line | Alert test | Silent twin |
|---|---|---|
| M3 `isLiquidatable` turns true → info, record the block | `test_onset_is_info_and_records_the_block`, `test_class5_fee_increase_onset_is_seen_at_its_block…` | `test_onset_benign_cluster_above_threshold_stays_silent` |
| M3 still liquidatable after N blocks → warn | `test_still_liquidatable_after_n_blocks_warns_exactly_at_n` | `test_n_blocks_benign_liquidated_before_n_resolves_without_warn` |
| M3 still liquidatable after N2 blocks → page | `test_still_liquidatable_after_n2_blocks_pages` | `test_n2_benign_lifted_by_deposit_before_n2_resolves_with_a_message` |
| M3 active and getBalance == 0 → page | `test_computed_zero_pages`, `…even_when_the_view_says_not_liquidatable`, `test_qa1_h1_empty_api…` | `test_computed_zero_benign_low_balance_and_liquidated_clusters_do_not_page` |
| M3 projected to cross within 24 h → info (digest) | `test_pre_alert_projected_within_24h_is_a_digest_line` | same test (8,000 blocks out) |
| M3 cluster set = API v4 + logs; view-computed balances | `test_qa1_h1_*` (API omission is not evidence), `test_unknown_incomplete_inventory…` | `test_qa1_h1_empty_api_then_a_real_liquidation_resolves_on_proof` |
| M4 deficit > 0 → page | `test_deficit_after_root_commit_pages`, `test_daily_interval_scans_without_a_root`, `test_class2_resolution_on_a_deleted_block_is_paged_again` | `test_deficit_benign_exact_cover_and_surplus_stay_silent` |
| M4 surplus trend → info | `test_surplus_trend_is_digest_info` | (info only) |
| M14 reverted liquidate / liquidateSSV on a liquidatable cluster + ClusterDeposited same/previous block below 1 block of burn → info | `test_v2a7_dust_deposit_same_or_previous_block_is_info[…]`, `test_qa1_h3_legacy_ssv_cluster_liquidate_ssv_front_run_is_info`, `test_qa1_m3_later_same_block_top_up_does_not_hide_the_attack` | `test_v2a7_benign_*` (6), `test_qa1_h3_legacy_benign_*` (2), `test_qa1_m3_benign_*` (2) |
| M14 three or more on the same cluster within 24 h → warn | `test_three_hits_within_the_window_warn`, `test_qa1_m2_…` (warn then silent info) | `test_qa1_m1_three_hits_over_26_hours_of_chain_time_do_not_warn`, `test_three_hits_benign_spread_beyond_the_window_stay_info` |
| M14 cluster also reaches computed zero → page | `test_front_run_cluster_at_computed_zero_pages`, `test_qa1_h3_legacy_cluster_at_computed_zero_pages` | `test_computed_zero_benign_without_a_hit_is_silent` |

### State table (DEFECT-CLASSES.md): every persisted table/marker and off-chain input

Columns: (a) empty, (b) error, (c) partial, (d) reorg depth 1 and N > confirmations, incl. resolved keys, (e) restart
between detection and handling, (f) same-block ordering, (g) time-window anchoring. **W** = wrong at `098c2cd`, fixed
in repair 1 (regression named); **U** = right but untested before, test added; otherwise the existing test is named;
n/a with the reason. Test names abbreviated (`qa1_` = `test_qa1_`, files `test_m3/m4/m14.py`, sweep =
`test_liquidation_sweep.py`).

**M3**

| State / input | (a) empty | (b) error | (c) partial | (d) reorg 1/N, resolved keys | (e) restart | (f) same block | (g) window |
|---|---|---|---|---|---|---|---|
| `m3_zone` (onset, zero_block, severity, tuple) | no row = not tracked; reorg keys without a row: identity from history/subject, else raise — **W** `qa1_h1_reorg_key_without_identity_raises…`, `…listed_nowhere…` | n/a (own table) | n/a | rows with onset > A deleted, zero_block cleared; rewound rows kept as candidates — `test_reorg_*` [0-1,12-20], **W** `qa1_h1_reorg_below_the_onset_with_an_empty_api…` | durable — `test_restart_does_not_duplicate_and_keeps_the_onset`, **U** `qa1_h1_empty_api_after_a_restart…` | end-of-block state at the checkpoint — `test_same_block_withdraw_then_deposit…` | age = checkpoint − onset (block-based, D5) — sweep I4 |
| `m3_tuples` (new) | no history → logs/persisted only | n/a | ≤ 8 tuples/cluster: beyond → UNKNOWN (kept), never clean | candidates only (hash-validated), not rewound; clusters observed above A re-checked in the reorg run — **W** `test_sweep_m3_resolution_on_a_deleted_block…`, `test_sweep_m3_reorg_removing_the_latest_change…` | durable | n/a | n/a |
| state `last_full_block`, `pre_alert` | first sweep: age lower bound — `test_restart_first_sweep_without_baseline…` | n/a | n/a | cleared if above A (rewind) — `test_reorg_*` | durable | n/a | digest labelled with its sweep block (stale only if sweeps fail; check-error health then) |
| in-memory near set / tuples / last_full | rebuilt by a full sweep | n/a | n/a | `on_reorg` drops them — `test_reorg_crash_between_rewind_and_reorg_run_is_replayed` | lost → first run is a full sweep — `qa1_h1_empty_api_after_a_restart…` | n/a | full-sweep interval on worker clock (scheduling) — `test_full_sweep_interval_holds…` |
| M3 incidents + `reorg:m3` marker (framework) | — | reorg run cannot evaluate → raise, marker kept — **W** `qa1_h1_reorg_key_without_identity_raises_and_keeps_the_marker` | — | re-find rule, re-check of resolved keys — **W** `test_sweep_m3_resolution…` | marker replayed — `test_reorg_crash_between…` | — | — |
| API listing (inventory) | `clusters: []` is UNKNOWN for tracked clusters (storage + persisted tuple) — **W** `qa1_h1_empty_api_does_not_resolve…[False,True]` | fetch error → check-error, nothing resolved — **U** `qa1_h1_api_error_resolves_nothing` | a subset → same as empty for the omitted; untracked omitted = completeness gap health — `test_unknown_incomplete_inventory…`, sweep "partial" mode | API with reorg — **W** `qa1_h1_empty_api…[True]`, sweep | refetched on start — `qa1_h1_empty_api_after_a_restart…` | n/a (state) | `api_refresh_s` (framework) |
| stored logs (tuples) | API-only clusters have none: persisted/history tuples — `qa1_h1_*` | n/a | latest log only + history | rescanned by the framework | durable | last log of the block | n/a |
| storage words | both slots empty = positive absence — **U** `qa1_h1_…absent_from_storage_on_the_new_chain_resolves` | transport raises — `test_rpc_failure_raises_and_resolves_nothing` | n/a | read at the reorg run's block | n/a | end of block | n/a |
| views (isLiquidatable, getBalance, getBurnRate) | empty result → UNKNOWN — `test_unknown_view_result_is_never_clean[empty]` | revert → UNKNOWN — `…[revert]`, `test_reorg_tracked_cluster_unknown_in_the_reorg_run_stays_open` | per-cluster isolation — `test_unknown_view_result…` | — | — | end of block | — |
| getNetworkValidatorsCount | n/a | revert → gap health | mismatch → health — `test_unknown_incomplete_inventory…` | — | — | — | — |
| params (projection only) | unreadable → run skipped — `test_thresholds_from_chain_unreadable_parameters_skip_the_run` | same | n/a | re-read after reorg (framework) | re-read | n/a | n/a |
| events (RootCommitted, cluster kinds, OperatorFeeExecuted) | n/a | n/a | n/a | cursor rewound (framework) | cursor durable | event triggers a run at that checkpoint | fee increase without cluster event — **W** class 5 `test_class5_fee_increase_onset…` |
| config | — | invalid → startup error — **W** class 11 `test_class11_invalid_configuration…` ×4 | — | — | — | — | — |

**M4**

| State / input | (a) empty | (b) error | (c) partial | (d) reorg 1/N, resolved keys | (e) restart | (f) same block | (g) window |
|---|---|---|---|---|---|---|---|
| `m4_scans` | first scan: digest "no scan yet" | n/a | n/a | rows above A deleted — `test_reorg_removing_the_deficit…` [F9] | durable | one block per scan | digest "≈ 24 h" by scan time, anchored at the latest scan |
| incident `deficit` | — | incomplete → UNKNOWN keep (page) | — | reorg run recomputes; resolution on a deleted block re-paged — **U** `test_class2_resolution_on_a_deleted_block_is_paged_again[0-1,12-20]` | complete each run — `test_restart*` | — | — |
| inventory / getters / lastOperatorId / proxy balance | no ETH cluster → UNKNOWN — `test_no_eth_cluster_is_unknown` | revert/empty → incomplete — `test_incomplete_inputs_are_unknown_not_resolved[…]` | hidden validators → incomplete — `…[hidden_cluster]` | pinned to the reorg run's block | — | all at one block (QA same-block earnings) | — |
| config | — | invalid → startup error — **W** class 11 ×2 | — | — | — | — | — |

**M14**

| State / input | (a) empty | (b) error | (c) partial | (d) reorg 1/N, resolved keys | (e) restart | (f) same block | (g) window |
|---|---|---|---|---|---|---|---|
| `m14_hits` (+ block_time, version) | no hit → resolve only without pending — **W** `qa1_h2_pending_entry_keeps…` | header unavailable → raise | NULL block_time (old DB) filled from headers — **U** `qa1_old_database_hits…` | hits above A deleted, canonical inbox rows re-derived — `test_reorg_*`, `test_reorg_reinclusion…`; resolution by window on deleted blocks re-found — **U** `test_class2_window_resolution…` | durable — `test_restart_does_not_duplicate_or_reprocess` | position-ordered — **W** `qa1_m3_*` | chain time at the checkpoint — **W** `qa1_m1_*`, `qa1_m2_*` |
| `m14_pending` (new) | — | unreadable tx / views / order → pending, health, retried — **W** `qa1_h2_unreadable_transaction_is_retried…` | cluster-scoped vs unknown cluster | rows above A deleted; unreadable tx in a reorg run → raise, marker kept — **W** `qa1_h2_reorg_missing_replacement…` | durable — **U** `test_class10_restart_while_an_entry_is_pending…`, `…while_the_reorg_run_raises…` | — | expires at the window (health says unevaluated) — `qa1_h2_pending_entry_keeps…` |
| `m14_open` (+ last_hit, owner, ops) | — | — | — | not rewound; re-evaluated every run | durable | — | silent downgrade (no re-send without a new hit) — **W** `qa1_m2_*` [R11] |
| inbox entries | empty file = nothing | unavailable → framework health (§5.2) | held entries (gate) | orphaned → canonical 0, never delivered; pending of an orphan dropped | cursor durable | file order | — |
| tx (input, transactionIndex) | no transaction / no input → pending, retried — **W** `qa1_h2_*`, `test_unknown_transaction_is_health_not_dismissed` | input present but not the liquidation call → health once, not pending (permanent) — **U** `test_class1_calldata_that_is_not_the_liquidation_is_permanent_not_pending` | no index: one event assumed before, several → UNKNOWN — `test_v2a7_same_block_order_unknown…`, **W** `qa1_m3_unknown_order…` | — | — | — | — |
| stored cluster logs | no deposit → no hit — `test_v2a7_benign_deposit_two_blocks_earlier` | — | no log before B → deposit-minus-value / API tuple — **W** `test_sweep_m14_cluster_without_earlier_logs…` | rescanned | — | **W** `qa1_m3_*`, `test_sweep_m14_no_deposit…` | — |
| views at B / B−1 (ETH or SSV), storage version | — | revert/empty → pending — `test_unknown_deposit_block_views_are_health` | — | — | — | version mismatch = no hit — **W** `test_v2a7_benign_liquidate_ssv_on_an_eth_cluster…`, legacy **W** `qa1_h3_*` | — |
| block headers (timestamps) | missing → RpcError (retried) | hash ≠ expected → RpcError | — | stamps of new blocks — `test_class2_window_resolution…` | — | — | the window's own clock — `qa1_m1_window_is_header_time_not_the_worker_clock` |
| zero test (inventory / storage) | omitted by the inventory → storage — **W** `qa1_h1_class_inventory_omission…` | getBalance revert → keep page — `test_unknown_zero_test_keeps_a_page` | — | — | — | end of checkpoint block | — |
| config | — | invalid → startup error — **W** class 11 ×3; `window_blocks` → `window_s` | — | — | — | — | — |

Seeded sweep (`test_liquidation_sweep.py`, 40 M3 seeds and 60 M14 seeds × 30 steps: API full/empty/partial, view
faults, unreadable transactions, same/previous-block dust with and without later top-ups, wrong-version liquidations,
legacy SSV, missed-slot gaps, reorgs of depth 1/2/4/5 onto shorter and longer chains, restarts) asserts after every
step, against the fixture's own model: no resolve without positive proof; no open incident without supporting
evidence or a named UNKNOWN; no dropped page/alert for surviving evidence (exact open set and severity when every input
is known); M3 age = checkpoint − onset with N/N2 severities; M14 count = hits within 24 h of the checkpoint header;
finally lived worker = fresh worker on the final chain. It found four defects (rows marked "sweep" above) before they
were fixed; S1–S6 show it reddens alone on each QA finding class.

### Completion handoff — Repair 1 (≤ 250 words, also the worker_done summary)

```text
Task / dispatch: task_f5b9000cb000 / ctx_90755f17174e (CP2a repair 1). Start head 098c2cd (clean).
Outcome: H1-H3, M1-M3 fixed; acceptance pending fresh Codex QA.
Base -> result: 098c2cd -> lane head on plan.md's CP2a status line.
Changed: checks/{m3_views,m3_liquidation_zone,m4_conservation_deficit,m14_deposit_frontrun}.py, config.example.json
  (checks.m14.window_s), tests/{fixtures_liquidation,test_m3,test_m4,test_m14,test_liquidation_sweep}.py,
  workers/cp2a/** (Repair 1, repair1/, live/ssv*). No CP1B file, qa/** or execution-log.md touched.
Verification: QA Appendix A 7 failed -> 35 passed; V2x.1 52/29/58; V2x.2 5/2/3; V2x.3 3/1/3; V2x.4 1/1/1;
  V2a.7 9; sweep 100; V2x.6 479 passed, ruff clean; falsifiers 41/41 red, restored, porcelain 0; 39/51 new cases red
  on frozen head (12 are controls). LIVE: SSV views on 5 legacy clusters at 26,241,773.
NOT RUN: M4 LIVE (equation unchanged), full M3 LIVE traversal, anvil e2e (CP3).
Notes: state table + own trigger ledger in workers/cp2a/plan.md; at the pin deposit() is ETH-only, so the legacy
  liquidateSSV path is fixture-only on mainnet; merge of plan/monitoring-service aborted (execution-log.md conflict,
  not writable here).
Next owner: fresh Codex QA (repair 1).
```

---

## Repair 1 of QA attempt 2 (repair round 2: QA cp2a-2 H1, M1 ruling; the H1 shape generalised)

- Task / dispatch: `task_2e1407c441dc` / `ctx_5c44cca7a3e5`; coordinator `term_89e2fa6f-482b-4551-995f-1f0e41c726a2`;
  worker `term_63c8bd46-2c3e-4e33-aee5-2c9a1cc668b1`; fresh implementer `claude-opus-5-5`, effort high.
- Frozen start head `92d8ff9ade5692b15ee1a3e9c183716dd948623e` (QA evidence commit on `1154333…`, clean); same worktree
  and branch. Evidence: `repair2/red-on-frozen.txt`, `repair2/falsifiers.jsonl`, `repair2/mutate.py`.
- Commits: `71cbdd1` (H1 awaiting entries, M1), `6efd0ad` (holds M3/M4/M14, sweep), `fd2531a` (freeze, last-sent
  time, expiry order, racing keepers), `561a4e0` (hold only without proof, `liq_runs`), `da2f928`/`bcb46fa` (evidence). Not touched: CP1B frozen files, `qa/**`, `execution-log.md`, README.

### Fixes

| Finding | Change | Regression (RED on frozen → GREEN) |
|---|---|---|
| QA2 H1 (BLOCKING) M14 hit lost when a reorg lowers the checkpoint below a surviving failed-liquidation block | Reorg run: every delivered liquidation entry above the ancestor still `canonical = NULL` (not re-confirmed: its block is above the lowered checkpoint) goes to `m14_pending` "awaiting confirmation" (cluster from calldata; no block time, never ages out); it holds its cluster's key (cluster unknown: every open key) at its severity; it is re-derived by the first run after the inbox confirms its block (`canonical = 1`), or dropped when orphaned (`canonical = 0` = proof). `near_set` makes M14 run at every checkpoint block while one waits. The last sent time is remembered while its hit awaits re-derivation (no duplicate info update). | `test_qa2_h1_shorter_chain_keeps_the_key_and_re_derives_the_surviving_hit[1-1-warn, 3-3-warn, 3-1-info, 3-2-info]` (QA reproducer, no clock advance), `…_that_replaces_the_failed_block_resolves_once_confirmed`, `…_restart_while_an_entry_awaits_confirmation…`, `…_second_reorg_below_an_awaiting_entry…`, `…_lagging_node_below_the_deposit_keeps_the_hit…` |
| QA2 M1 + coordinator ruling | A "time" = one distinct front-running deposit (`deposit_tx`) per cluster; warn at `repeat_count` distinct deposits whose hits are within the trailing `window_s` of chain time; evidence `count` (times), `liquidations`, `deposits: {deposit_tx: [failed txs]}`; body lists per deposit the liquidations it blocked; the info re-send (revision) is per new deposit. | `test_qa2_m1_one_deposit_reverting_three_liquidations_is_one_time`, `…_blocking_its_block_and_the_next_is_one_time_and_not_re_sent`, `…_three_distinct_deposits_warn` |
| H1 shape in M3 (found here) | A lagging node or a shorter chain put the reorg run below `m3_zone.last_block`: frozen head resolved a live page at the lower block and re-opened it at info (page dropped for N2 ≈ 1 day), or lowered it silently and re-sent warn + page. Now the row is held (`hold_block`, onset and zero block kept): no resolution, no lowering, no escalation until a run at or above `hold_block`. | `test_qa2_class2_lowered_checkpoint_keeps_the_page_and_never_re_escalates[4]`, `…_then_the_new_chain_lifts_the_cluster_resolves_once`, `test_qa2_class10_restart_while_the_checkpoint_is_lowered_keeps_the_hold` |
| H1 shape in M4 (found here) | Same: the deficit page resolved at the lower block and was re-paged as a new incident. Now state `hold` (latest scan above the run block) keeps the page until a scan at or above it; `near_set` while held. | `test_qa2_class2_lagging_node_below_the_deficit_keeps_the_page_and_never_re_pages`, `…_shorter_chain_without_the_trigger_resolves_once_past_the_old_scan`, `test_qa2_class10_restart_while_the_deficit_is_held…` |
| H1 shape in M14 zero page (found here) | `m14_open.hold_block`: a zero page seen above the lowered checkpoint is not lowered there and not re-sent. | `test_qa2_class2_lowered_checkpoint_keeps_a_zero_page_and_never_re_escalates[2]`, `test_qa2_class10_restart_while_a_zero_page_is_held_keeps_it` |
| Escalation on an earlier block (sweep M14 seed 10) | While held, the severity is frozen (an earlier block than the one already evaluated: e.g. computed zero before a top-up the worker had seen). | `test_qa2_class4_lowered_checkpoint_does_not_escalate_on_an_earlier_block` |
| Hold only without proof (keeps QA-1's shorter-replacement controls) | `liq_runs` (shared helper in `m3_views`): the blocks each check saw (its checkpoints + the framework's recorded blocks, copied at each run because the framework deletes its own above the ancestor). In the reorg run, if one of them in (ancestor, evidence block] is now another block on the node, the evidence provably left the chain: no hold, the old rules apply at once. Only when every probed block is missing (a lagging node, or a new chain shorter than them) is the incident held. | controls `test_qa2_class2_shorter_chain_with_a_replaced_block_resolves_in_the_reorg_run` (M3, M4); QA attempt-1 Appendix A `test_shorter_replacement_chain[6]` |
| Pending expiry ordering (sweep) | M14 expires pending entries after the retries, so an entry re-derived in a run with its block's time expires in that run. | sweep M14 seed 10 |

### Contract for CP3 (README §6.2; not edited by this lane)

- §6.2 item 5 misses **inbox entries above a lowered checkpoint**: after a reorg onto a shorter chain (or a node behind
  the checkpoint, which `check_reorg` treats the same: a missing block is not canonical) an already delivered entry
  whose block is above the new checkpoint is `canonical = NULL`, cannot be re-derived (its logs are not rescanned) and is
  never delivered again (cursor passed). A check must keep such an entry as UNKNOWN (keep the keys it may support) and
  re-derive it once `canonical = 1`, or drop it at `canonical = 0`. M14 does this (`await_confirmation`).
- **Lowered checkpoint, every check**: a reorg run whose block is below the block of a check's latest evidence for an
  open key must not resolve, lower or escalate that key on the earlier block unless it has positive proof that the
  evidence left the chain (a block it saw above the ancestor, at or below the evidence, is now another block). Without
  proof, hold until a run at or above the evidence block. The framework cannot tell "replaced" from "missing" after
  `check_reorg` (it deletes `blocks` above the ancestor), hence `liq_runs`.
- `Inbox.pending` stops at the first unconfirmed line: entries confirmed behind an entry above the checkpoint wait
  until that one is confirmed (a delay of a few blocks, not a loss; the sweep oracle models it).

### State table, repair 2 (adds to the repair-1 table; **W** = wrong at `92d8ff9`, fixed; **U** = untested, test added)

| State / input | (a) empty | (b) error | (c) reorg depth 1 / N, shorter chain, lagging node, resolved keys | (d) restart | (e) same block | (f) window anchor | (g) partial |
|---|---|---|---|---|---|---|---|
| inbox rows above a lowered checkpoint (M14) | — | tx unreadable → cluster unknown, holds every open key | **W** awaiting, re-derived/dropped on confirmation: `qa2_h1_*` (6 cases + lagging below the deposit) | **W** `qa2_h1_restart…` | racing keepers: sweep | never ages out while awaiting (no block time) | — |
| `m14_pending` awaiting rows | — | — | second reorg below: **W** `qa2_h1_second_reorg…` | durable | — | expiry after retries (sweep) | — |
| `m14_hits` per deposit (M1) | — | — | re-derived hits keep their deposit | — | **W** one deposit × 3 keepers, deposit in B and B+1: `qa2_m1_*` | count = distinct deposits within window_s of the checkpoint header | — |
| `m14_open` (+ `last_deposit`, `hold_block`) | — | zero test unavailable keeps page (repair 1) | **W** zero page held, frozen: `qa2_class2_…zero_page…[2]`, `qa2_class4_…` | **W** `qa2_class10_…zero_page…` | — | — | last sent time kept while awaiting: **W** `[3-2-info]` |
| `m3_zone.hold_block` | — | view fault while held → UNKNOWN keeps row | **W** below/above onset × lag/shorter: `qa2_class2_…[4]`, twin | **W** `qa2_class10_…` | — | age = run block − kept onset | API empty while held: as repair 1 (lookup) |
| M4 state `hold` | — | incomplete scan while held → page kept | **W** `qa2_class2_lagging…`, twin | **W** `qa2_class10_…` | — | — | — |
| `liq_runs` (proof blocks) | no rows → no proof → hold (safe) | header unavailable → treated as missing (no proof) | proof → resolve at once: controls ×2, App. A ×6 | durable | — | — | only 8 lowest probed; ≤ 1,024 kept per check |
| reorg of an already-resolved key | — | — | M14 `test_class2_window_resolution…` (re-opens), M3 `test_sweep_m3_resolution_on_a_deleted_block…`, M4 `test_class2_resolution_on_a_deleted_block…` (repair 1) | — | — | — | — |

### Adversarial sweep (`test_liquidation_sweep.py`, 150 seeds × 30 steps)

M3 seeds 0–39 (confirmations 0) and 40–59 (confirmations 3), M14 seeds 0–59 (0) and 60–89 (4); new actions: reorgs from
near the head onto shorter chains, steps against a lagging node (2 … 1,000 blocks behind), racing keepers (0–2 extra
liquidations per deposit, separate RNG). Invariants after every step: 1 no resolve without positive proof; 2 no open
incident without evidence (or a named UNKNOWN / hold); 3 no dropped alert for surviving evidence (exact set and severity
when every input is known); 4 window anchored at the run's checkpoint (M3 age, M14 count of distinct deposits and
chain time); 5 no event lost (a seen liquidation above the checkpoint holds, and is counted once confirmed again);
6 a lowered checkpoint changes nothing already evaluated (no resolution, lowering or escalation) unless a replaced
block exists. Final: lived = fresh worker (holds allowed only for UNKNOWN entries). RED on frozen: M3 20/60, M14 41/90.

### Verification (lane head, 2026-10-22, `reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider …`)

| Row | Result |
|---|---|
| V2x.1 M3 / M4 / M14 | 59 / 33 / 73 passed |
| sweep | 150 passed |
| V2x.2 (`-k thresholds_from_chain`) | 5 / 2 / 3 |
| V2x.3 (`-k restart`) | 4 / 2 / 5 |
| V2x.4 (`-k rpc_budget`) | 1 / 1 / 1 |
| V2a.7 (`-k v2a7`) | 9 |
| V2x.6 full suite; `ruff check reconcile` | 555 passed; All checks passed |
| RED on frozen (`repair2/red-on-frozen.txt`) | 25 of 26 new cases red (1 control passes; M3's control reds only on the new column) |
| QA2 Appendix A probes (`/tmp/cp2a-qa2/qa2/test_qa2.py`) | 22 passed (H1, M1 now green) |
| QA1 Appendix A (`/tmp/cp2a-qa2/appA/test_independent.py`) | 35 passed |
| Falsifiers `repair2/mutate.py` (repair-1 rows re-targeted + 15 R2 + 5 sweep) | 60/60 red, restored, porcelain `''`, exit 0 |
| QA2 own falsifiers `mutate_qa2.py` | 3/3 red, restored, porcelain `''` |

NOT RUN: LIVE (no on-chain read path changed except one header batch per reorg run; M4 equation unchanged), anvil e2e
(CP3), the repair-1 LIVE probes. No merge of `plan/monitoring-service` (not needed).

### Recorded interpretations and notes (for the coordinator)

- M14 interpretation (ruling, amend spec at closure): "three or more times" = three distinct front-running deposits on
  the same cluster within 24 h of chain time; a deposit's time is its failed liquidations' block time.
- Framework (CP1B, not this lane): a page lowered silently to info and later resolved sends no resolution
  (`alerts._apply_one` uses the current severity). M3 can still lower a page in normal flow (partial top-up below N2
  after a zero page); the holds above remove the reorg path to it.
- In a lowered window a key that was not open can be opened from the earlier (canonical) block's state; it is bounded
  by the window and resolved on proof later (not suppressed: the state is canonical).
- N3 (stall on an entry never served) unchanged; awaiting entries do not stall (they wait for confirmation only).
- `liq_runs` reads the framework's `blocks` table (read-only), as M14 already reads `inbox` and `checkpoint`.

### Completion handoff — repair round 2 (≤ 250 words, also the worker_done summary)

```text
Task / dispatch: task_2e1407c441dc / ctx_5c44cca7a3e5 (CP2a repair after QA attempt 2). Start head 92d8ff9 (clean).
Outcome: QA2 H1 fixed; M1 implemented per coordinator ruling; the H1 shape found and fixed in M3, M4 and the M14 zero
  page; acceptance pending a fresh Codex QA (capped round).
Changed: checks/{m3_views,m3_liquidation_zone,m4_conservation_deficit,m14_deposit_frontrun}.py, tests/{fixtures_
  liquidation,test_m3,test_m4,test_m14,test_liquidation_sweep}.py, workers/cp2a/** (this section, repair2/).
Verification: 555 passed, ruff clean; V2x.1 59/33/73; V2x.2 5/2/3; V2x.3 4/2/5; V2x.4 1/1/1; V2a.7 9; sweep 150
  (RED on frozen 61/150); 25/26 new cases red on frozen; falsifiers 60/60 + QA2 3/3; QA2 probes 22 passed; QA1 App. A 35.
NOT RUN: LIVE, anvil e2e (CP3).
Contract for CP3: README §6.2 item 5 inbox entries above a lowered checkpoint; hold-without-proof rule (this record).
Next owner: fresh Codex QA.
```
