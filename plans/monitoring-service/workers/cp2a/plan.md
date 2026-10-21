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
