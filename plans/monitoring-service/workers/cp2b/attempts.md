# Attempts — CP2b

Same entry shape as execution-log.md events, worker-side. Append only. Measured UTC.

<a id="attempt-1"></a>
## Attempt 1 — environment and baseline

- Observed UTC: 2026-10-21T13:39Z–13:50Z
- Task / Dispatch: `task_13f583847d79` / `ctx_e0d798da26ac` (run `run_96be3e0c7302`)
- Frozen base: `96d1c32fe3446ec7092e849e401f851a0d9b2cf3` (`git rev-parse HEAD` at start; porcelain 0)
- Action and concrete outcome: `export PATH=$HOME/.foundry/bin:$PATH`; `uv venv reconcile/.venv --python 3.12`; `uv pip install --python reconcile/.venv/bin/python -r reconcile/requirements-dev.txt` (exit 0). `orca` not on PATH: `/Applications/Orca.app/Contents/Resources/bin/orca` used.
- **Frozen-file patch merged from the integration branch** (coordinator rulings v2 / v2.1, messages `msg_e5376232b77c`, answer to my ask about `test_config_rejects_unknown_check_ids_and_keys`): `git merge --no-edit plan/monitoring-service` twice, both fast-forward: `438904e` (bd531b4: tests/harness.py, test_framework.py, test_worker.py hunks) and `f92e2b7` (500abe4: harness builds a lane check only when a test names it). No lane file edited a frozen file.

| Verification ID | Exact command | Exit / result / counts / skips | Environment | Evidence |
|---|---|---|---|---|
| base suite | `reconcile/.venv/bin/python -m pytest -q reconcile/tests` | exit 0, 240 passed (at 96d1c32 and at 438904e) | local venv 3.12 | console |
| with lane checks registered, before the v2.1 merge | same | exit 1, 239 passed / 1 failed (`test_config_rejects_unknown_check_ids_and_keys`: M9's construction error fired before the stub's unknown-key error) → `ask` → ruled fixed centrally by 500abe4 | | console |
| after the v2.1 merge (`f92e2b7`), lane checks registered | same | exit 0, 240 passed; ruff clean | | console |

- Resulting head: `f92e2b7` (+ uncommitted lane files)

<a id="attempt-2"></a>
## Attempt 2 — implementation and scenarios

- Observed UTC: 2026-10-21T13:50Z–14:20Z
- Action and concrete outcome: `checks/m9_oracle_common.py` (round model from stored logs, `EpisodeBook`, block-time estimate, gated inbox reads), `checks/m9_oracle_quorum.py`, `checks/m10_oracle_rotation.py`, `checks/m11_updater_liveness.py`; one import line in `checks/__init__.py`; `tests/fixtures_oracle.py` (`OracleChain` following `SSVDAO.commitRoot` at f7ac487, inbox lines, `UpdaterWorld`, `FakeProofApi`, recorded mainnet rows, `replay_mainnet`); `tests/test_m9.py`, `test_m10.py`, `test_m11.py`; `config.example.json` `checks.m9/m10/m11`. Design decisions: [plan.md § Design](plan.md#design-worker-local-decisions-none-changes-a-spec-trigger-or-severity).
- Semantic notes found while building (facts, not adjustments): (1) at the pin a vote after its round committed reverts `StaleBlockNumber`, so a committed higher `blockNum` closes every older open round (used for "superseded"); (2) `replaceOracle` already reverts `OracleAlreadyAssigned` for a duplicate holder, so M10's duplicate rule detects a contract/upgrade anomaly; (3) live proof API shape (2026-10-21): `{clusterId, effectiveBalance, proof, merkleRoot, referenceBlock}`, unknown id → `{"error": "cluster not found"}`; (4) recorded mainnet rounds put all votes of a round in one block.
- Expected values: from the spec and the triage record only. Two expectations of mine were incomplete and were corrected with the spec/README text, not to match output: the 2026-08 replay also raises `absent:3` (spec M9: "has not voted for the committed root in any of the last 6 rounds" — slot 3 voted another root in all of them), and a reorg run that re-reports at a lower severity records it silently (README §4/§6.2).

### Scenario enumeration (V2x.1: one test per spec trigger at its severity + a benign twin)

| Monitor / spec trigger | Severity | Trigger test | Benign twin (stays silent) |
|---|---|---|---|
| M9 proposed, no commit within 30 min | page | `test_m9::test_failed_round_pages_after_the_timeout` | `test_benign_round_committed_within_the_timeout_is_silent` |
| M9 no RootCommitted > 9 h (V2b.7, clock) | page | `test_no_root_committed_for_9h_pages_from_the_clock_without_any_event` | `test_benign_cadence_every_8h_is_silent` |
| M9 two roots for one blockNum | warn | `test_two_roots_for_one_blocknum_warns_for_the_dissenting_slot` | `test_benign_unanimous_round_is_silent` |
| M9 three dissenting rounds in a row, one slot | page | `test_three_dissenting_rounds_in_a_row_from_one_slot_page` | `test_benign_two_dissents_then_agreement_never_pages_and_resolves` |
| M9 committed with < 3 distinct slots | page | `test_commit_with_fewer_than_three_distinct_slots_pages`, `…fewer_slots_than_its_own_quorum_data_requires_pages` | `test_benign_commit_with_three_slots_is_silent` |
| M9 slot absent from the committed root in the last 6 rounds | warn | `test_slot_absent_from_six_committed_rounds_warns` | `test_benign_routine_skipped_fourth_vote_is_silent` |
| M9 commitRoot revert from a slot address (inbox) | warn | `test_commit_root_revert_from_a_slot_address_warns` | `test_benign_commit_root_revert_from_a_non_slot_sender_is_silent` |
| M10 OracleReplaced during an open round | page | `test_m10::test_rotation_during_an_open_round_pages_and_resolves_when_the_round_closes`, `test_same_block_rotation_then_commit_pages_once_and_resolves` | `test_benign_rotation_between_rounds_is_silent`, `test_same_block_commit_then_rotation_is_silent`, `test_rotation_while_an_old_round_is_superseded_is_silent` |
| M10 newOracle already holds another slot | page | `test_new_oracle_already_holding_another_slot_pages` | `test_benign_rotation_to_a_fresh_address_has_no_duplicate` |
| M11 0 updater txs within 60 min | warn | `test_m11::test_zero_updates_within_60_min_warns` (+ `…after_the_window_do_not_count`, `…from_another_sender_do_not_count`) | `test_benign_round_with_updater_txs_is_silent` |
| M11 … and proof API mismatches | page | `test_zero_updates_with_leaf_mismatches_pages` | `test_benign_zero_updates_with_every_leaf_matching_stays_warn` |
| M11 two consecutive zero rounds | page | `test_two_consecutive_zero_rounds_page` | `test_benign_zero_round_between_good_rounds_never_pages` |
| M11 revert rate > 10 %, IncorrectClusterState → M1 | warn | `test_revert_rate_above_10_percent_warns_and_hands_incorrect_cluster_state_to_m1`, `test_stale_update_without_another_senders_update_counts` | `test_benign_revert_rate_at_10_percent_is_silent`, `test_benign_stale_update_after_another_sender_set_the_same_eb_is_excluded` |
| M11 weekly full-state comparison (LIVE, DEFERRED if API unreachable) | warn | `test_weekly_full_state_mismatch_warns`, `test_weekly_full_state_deferred_while_the_api_is_unreachable` | `test_benign_weekly_full_state_all_match_is_silent` |
| M10 every OracleReplaced → info; M11 other sender → info | info | not this lane: CP1A Monitor templates (brief) | — |

Other rows: thresholds_from_chain `test_m9::test_thresholds_from_chain_active_slot_set_drives_absence`, `…rotated_slot_address_is_a_slot_for_reverts`, `test_m10::test_thresholds_from_chain_slot_map_change_without_a_rotation_event`, `test_m11::test_thresholds_from_chain_updater_is_the_live_slot_4_key`; restart `test_m9::test_restart_does_not_duplicate_open_incidents`, `…with_prior_non_zero_state_keeps_the_streak`, `test_m10::test_restart_does_not_duplicate_the_rotation_page`, `test_m11::test_restart_does_not_duplicate_the_liveness_warning`, `…with_prior_zero_round_escalates_on_the_second`; rpc_budget `test_m9/test_m10::test_rpc_budget_no_eth_call_per_sweep_with_350_clusters` (bound 0 RPC per sweep), `test_m11::test_rpc_budget_350_clusters` (sender lookups once = 40, then 0; eth_call ≤ 350 = one per inventory cluster on a comparison sweep, 0 otherwise). Recorded mainnet: `test_m9::test_replay_mainnet_2026_08_outage`.

### Adversarial self-review (brief list) → where covered

| Attack | Covered by |
|---|---|
| reorg of the block carrying each trigger event | M9 late commit `test_reorg_depth_1_removing_the_late_commit_reopens_the_failed_round`, votes `…depth_n_removing_the_votes…[0,12]`; M10 `test_reorg_depth_1_removing_the_rotation…`, `…depth_n_moving_the_rotation_after_the_commit…[2,12]`; M11 `test_reorg_depth_1_adding_updates…`, `…depth_n_moving_the_commit…[2,12]`, `…below_the_evaluated_block_recomputes_persisted_proof_results` |
| same-block ordering of two events | `test_m10::test_same_block_commit_then_rotation_is_silent`, `…rotation_then_commit_pages_once_and_resolves`; vote + commit in one tx throughout (`OracleChain.vote`) |
| prior non-zero state then restart | `test_m9::test_restart_with_prior_non_zero_state_keeps_the_streak`, `test_m11::test_restart_with_prior_zero_round_escalates_on_the_second` |
| parameter changed mid-run | quorum `test_commit_with_fewer_than_three_distinct_slots_pages`; active slots, slot address, slot map, updater key (thresholds_from_chain tests) |
| missing input treated as clean | `test_unreadable_parameters_skip_the_run_instead_of_resolving`, `test_malformed_vote_log_raises…`, `test_inbox_unavailable_keeps_the_revert_rate_incident`, `test_zero_updates_with_the_proof_api_down…`, `test_weekly_full_state_deferred…`, `test_leaves_at_another_reference_block_are_not_compared`, `test_empty_inventory_is_deferred_not_clean`, startup errors (`test_missing_views…`, `test_missing_inbox…`, `test_missing_updater_source…`) |
| empty RPC result read as "no logs"/clean | `test_a_transaction_the_rpc_cannot_return_fails_the_run`, `test_empty_get_effective_balance_answer_fails_the_run`; stored logs are the framework's reorg-checked scan |
| first sweep after start (no baseline) | `test_first_sweep_after_start_reports_ongoing_conditions_not_healed_history`, `test_m10::test_first_sweep_reports_a_rotation_whose_round_is_still_open…`, `test_cadence_with_no_commit_since_start_block_pages_after_9h`; downtime: `test_catch_up_after_downtime_reports_a_healed_failed_round_once` |
| crash between detection and handling | `test_reorg_crash_between_detection_and_handling` (M9, M10, M11) |

<a id="attempt-3"></a>
## Attempt 3 — verification rows, falsifiers, LIVE

- Observed UTC: 2026-10-21T14:04Z–14:30Z; code head `8354917ad09bcabcbef202673f4dd0cf4af5a972`.
- Every row run on its own by `bash plans/monitoring-service/workers/cp2b/evidence/verify.sh` → [`evidence/verification.log`](evidence/verification.log).

| Verification ID | Exact command | Exit / result / counts / skips | Environment | Evidence |
|---|---|---|---|---|
| V2x.1 M9 | `reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider reconcile/tests/test_m9.py` | exit 0, 37 passed, 0 skipped | local | verification.log |
| V2x.1 M10 | `… reconcile/tests/test_m10.py` | exit 0, 15 passed | local | verification.log |
| V2x.1 M11 | `… reconcile/tests/test_m11.py` | exit 0, 34 passed | local | verification.log |
| V2x.2 | `… test_m9.py -k thresholds_from_chain` / m10 / m11 | exit 0 each: 2 / 1 / 1 passed | local | verification.log |
| V2x.3 | `… -k restart` m9 / m10 / m11 | exit 0 each: 2 / 1 / 2 passed | local | verification.log |
| V2x.4 | `… -k rpc_budget` m9 / m10 / m11 | exit 0 each: 1 / 1 / 1 passed | local | verification.log |
| V2b.7 | `… test_m9.py -k from_the_clock` | exit 0, 1 passed (checkpoint and stored-log count unchanged, run reason `interval`) | local | verification.log |
| V2x.5 | `bash …/evidence/falsify.sh <id> <file> <old> <new> <test>` ×5 | F9-streak (`>=`→`>` on the streak), F9-clock (clock age → block age), F10-open (drop "before the rotation" on the closing commit), F11-window (60 min → 100×), F-book-reorg (never forget a vanished episode): each named test exit 1 mutated, exit 0 restored, `git status --porcelain` empty | local | [`evidence/falsifiers.log`](evidence/falsifiers.log) |
| V2x.6 | `… -m pytest -q -p no:cacheprovider reconcile/tests`; `reconcile/.venv/bin/ruff check reconcile` | exit 0, 326 passed (240 CP1B unchanged + 86 lane); ruff "All checks passed!" exit 0 | local | verification.log |
| LIVE M9 record | `python3 evidence/fetch_round_logs.py https://eth-pokt.nodies.app outage_2026_08_logs.json 50 200 <21 refs>` then `reconcile/.venv/bin/python evidence/live_replay.py evidence/outage_2026_08_logs.json 400` | exit 0; 89 logs, 21 rounds, 13 commits (84 eth_getLogs of 50 blocks); m9: failed-round page (8 rounds) resolved, cadence page resolved, split warn resolved (25,718,430), dissent:3 page open, absent:3 warn open; m10 none. Same data as `test_replay_mainnet_2026_08_outage` | eth-pokt.nodies.app (archive) | `outage_2026_08_logs.json` sha256 e074dc6b…, `outage_2026_08_replay.out.json` |
| LIVE M9/M10 current | `python3 evidence/fetch_oracle_logs.py https://ethereum-rpc.publicnode.com <head-7900> <head> live_recent_logs.json 1000` (head 26,241,475, 14:09Z) then `live_replay.py` | exit 0; 12 logs, 3 rounds (26,234,805 / 26,237,195 / 26,239,590), unanimous, slot addresses = spec §5; 0 incidents, 0 messages | ethereum-rpc.publicnode.com | `live_recent_logs.json` sha256 009093dc…, `live_recent_replay.out.json` |
| LIVE M11 weekly comparison | `reconcile/.venv/bin/python evidence/live_m11_leaves.py https://ethereum-rpc.publicnode.com` (runs `M11UpdaterLiveness.compare_leaves`) | exit 0; block 26,241,467, latest committed blockNum 26,239,590 = API referenceBlock; 530 active ETH clusters, 0 stale tuples, 530 compared, 0 mismatches, 0 unknown; 530 eth_call + 531 eth_getStorageAt; 283.6 s | publicnode + SSV API v4 + ssv-oracle proof API | `live_m11_leaves.out.json`; re-run at the final code head: see below |

- LIVE M11 re-run at code head `8354917` (2026-10-21T14:23Z; the first attempt at that head exited 1 with the new construction error "checks.m11: the revert-rate rule needs the failed-transaction inbox", fixed in the script by `require_inbox: false` for its leaves-only instance): exit 0, block 26,241,534, blockNum 26,239,590, 530 compared, 0 mismatches, 0 unknown, 530 eth_call, 298.6 s → [`evidence/live_m11_leaves.final.out`](evidence/live_m11_leaves.final.out).
- Public RPC facts (recorded, no row weakened): `eth.drpc.org` refuses eth_getLogs on the free plan ("ranges over 10000 blocks are not supported", even for 256-block ranges, code 35); `ethereum-rpc.publicnode.com` serves eth_getLogs only within ≈ 6,000–10,000 blocks of head ("Archive requests require a personal token") and ≤ 3,000-block ranges near head; `eth-pokt.nodies.app` 50-block ranges on history; `eth-mainnet.public.blastapi.io` 10-block ranges.
- Deviations and findings: (1) **M11 comparison latency**: 530 sequential proof GETs ≈ 285 s inside one check run (one DB transaction; the worker loop waits) — weekly and on zero-update rounds only; owner CP3 (concurrency or a smaller timeout) / Marco. (2) spec M9 "absent from the committed root" also fires for a persistently dissenting slot (replay: `absent:3` next to `dissent:3`): by the spec's wording; Marco may want it scoped to non-voting slots. (3) Catch-up semantics: M11 reports the updater's current state (a zero round healed during downtime is not reported); M9/M10 report healed episodes after the baseline once. (4) Failed rounds are one incident per run of failures (re-sent per round) instead of one per round.
- NOT RUN: none of the owned rows.
- Next action: records commit, status line, worker_done.

<a id="attempt-4"></a>
## Attempt 4 — repair 1 (QA attempt 1 FAIL, F1–F8)

- Observed UTC: 2026-10-21T14:55Z–15:45Z
- Task / Dispatch: `task_5b0bbc8b8e34` / `ctx_edbc5b0b243a` (run `run_96be3e0c7302`), worker `term_df241ade-a8c6-4971-a87c-3d75ad64dee3`
- Frozen start head: `a1e4775d8e6eda545460b34c9ee961d77c27001f` (porcelain 0); code head after repair: `92a7a3c84c5fd358285f3fcbf8f0fa86c52b81cb`
- Action and concrete outcome: fixes, regressions, state table, sweep and decisions in [plan.md § Repair 1](plan.md#repair-1-qa-attempt-1-fail--fresh-implementer). QA Appendix A extracted and run on the start head: exit 1, 11 failed / 14 passed (= the report).
- `git merge --no-edit plan/monitoring-service`: CONFLICT in `plans/monitoring-service/execution-log.md` (not writable for this lane) → `git merge --abort`, tree clean; the integration branch differs only in documents; `qa/DEFECT-CLASSES.md` read with `git show`.

| Verification ID | Exact command | Exit / result / counts / skips | Environment | Evidence |
|---|---|---|---|---|
| V2x.1 M9 / M10 / M11 | `bash plans/monitoring-service/workers/cp2b/evidence/verify.sh` (each row its own pytest) | exit 0: 60 / 23 / 59 passed, 0 skipped | local | `evidence/verification.repair1.log` |
| V2x.2 thresholds_from_chain | same script | exit 0: 2 / 1 / 1 | local | same |
| V2x.3 restart | same script | exit 0: 3 / 2 / 5 | local | same |
| V2x.4 rpc_budget | same script | exit 0: 1 / 1 / 1 (M9: 0 eth_call, ≤ 3 headers per sweep, 0 on a clock-only sweep; M10: 0 RPC; M11: senders once, eth_call ≤ 350) | local | same |
| V2b.7 | same script, `-k from_the_clock` | exit 0, 1 passed | local | same |
| qa1 regressions | same script, `-k qa1` per file | exit 0: 8 / 1 / 7 | local | same |
| adversarial sweep | `… reconcile/tests/test_m9_m10_m11_sweep.py` | exit 0, 44 passed | local | same |
| QA Appendix A as published | extracted by the script from `qa/cp2b-attempt-1.md` | exit 1, 23 passed / 2 failed: the two reproducers whose setup predates F6 (fail at their own setup assertion; ported adapted, plan.md decision 4) | local | same |
| V2x.6 | `… reconcile/tests`; `reconcile/.venv/bin/ruff check reconcile` | exit 0, 426 passed (240 CP1B unchanged + 186 lane); ruff exit 0 | local | same |
| RED on frozen / GREEN now | frozen code (`git archive a1e4775 reconcile`) + repaired tests, see file header | frozen: 87 failed / 99 passed (incl. 36 sweep cases); 39 new tests RED there, 20 new tests GREEN there (new coverage of rows that were right, QA controls); repaired head: all pass | local | `evidence/red_green_repair1.txt` |
| V2x.5 falsifiers (repair 1) | `bash plans/monitoring-service/workers/cp2b/evidence/falsify_repair1.sh` | 15 mutations (F1-retry, F1-proof, F2-order, F3-split, F3-sameslot, F4-events, F5-before, F6-age, F7-consecutive, F8-clock, F8-slots, S-superseded, SWEEP-m9, SWEEP-m10, SWEEP-m11): every mutated test exit 1, every restored exit 0, no `reconcile/` path in porcelain; a fresh bytecode cache per process | local | `evidence/falsifiers_repair1.log` |
| LIVE M9 record replay (saved dump, no network) | `reconcile/.venv/bin/python …/evidence/live_replay.py …/evidence/outage_2026_08_logs.json 400` | exit 0; 89 logs, 21 rounds, 13 commits; m9: failed-round page resolved, cadence page resolved, dissent:3 page open, absent:3 warn open, **six `split` warns** (immediate two-root warnings of the failed outage rounds, F3) each resolved; m10 none; 89 messages (75 before) | local | `evidence/outage_2026_08_replay.repair1.out.json` |
| LIVE M9/M10 current rounds (saved dump) | same with `live_recent_logs.json` | exit 0; 12 logs, 3 rounds, 3 commits; 0 incidents, 0 messages | local | `evidence/live_recent_replay.repair1.out.json` |

| LIVE M11 weekly comparison (network, read-only) | `reconcile/.venv/bin/python …/evidence/live_m11_leaves.py https://ethereum-rpc.publicnode.com` (runs the repaired `compare_leaves`) | exit 0; block 26,241,891, latest blockNum 26,239,590 = API referenceBlock; 530 active ETH clusters, 0 stale tuples, 530 compared, **1 mismatch** (cluster `0xc6d52a5b…574b`, owner `0x5ce6430e…15bd`: on-chain EB 230,930 vs leaf 231,442), 0 unknown; 530 eth_call + 531 eth_getStorageAt; 291.6 s. The same root compared 0 mismatches at 14:23Z (worker) and 14:4xZ (QA): the cluster's on-chain EB moved after the root, i.e. a mismatch younger than one round — exactly what F6 does not warn on; the next root's re-comparison decides | ethereum-rpc.publicnode.com + SSV API v4 + ssv-oracle proof API, 2026-10-21T15:34Z | `evidence/live_m11_leaves.repair1.out` |

- Earlier falsifiers F9-clock (its anchor `age = ctx.clock() - mark["at"]` no longer exists: the clock estimate was replaced by chain time; its successor is F8-clock) and F11-window (`blocks_for` removed; successor F8-slots) are retired; F9-streak, F10-open and F-book-reorg anchors still exist and were not re-run separately (their tests are in the green V2x.1 rows).
- NOT RUN: archive re-fetch of the 2026-08 outage logs and of the current rounds (the saved dumps were replayed; the QA re-fetched them on 2026-10-21 and matched them byte for byte); the anvil-fork M9 scenario is CP3 (V3.8).
- Next action: records commit, worker_done; fresh Codex QA.
