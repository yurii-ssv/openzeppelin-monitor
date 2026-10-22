# Worker: CP2d — Cluster integrity and capacity (M1, M2)

Task contract: [plan.md §8 CP2d](../../plan.md) · Attempt log: [attempts.md](attempts.md) · QA contract: [qa/QA-CONTRACT.md](../../qa/QA-CONTRACT.md)

- Actual starting worktree/branch/head/status: `/Users/marco/orca/workspaces/ssv-network-monitor/ms-cp2d`, branch `mtabasco/ms-cp2d`, head `96d1c32fe3446ec7092e849e401f851a0d9b2cf3`, `git status --porcelain | wc -l` = 0. Integration merges (coordinator rulings, fast-forward): `438904e` (bd531b4), `1154c38` (500abe4).
- Live authority: Orca Task `task_aa8cd71dc363`, Dispatch `ctx_f28f28892d56` (from the injected preamble).
- Requested/effective model and effort: requested Claude Opus high (task title); the worker cannot read the start receipt — coordinator records `launch.effective`.
- Allowed writes / prohibited actions / local commit authority: `reconcile/checks/m1_*.py`, `m2_*.py`, their tests, `reconcile/tests/fixtures_cluster.py`, `reconcile/tests/fixtures/receipts/*.json`, `checks.m1`/`checks.m2` in `reconcile/config.example.json`, one line in `reconcile/checks/__init__.py`, `plans/monitoring-service/workers/cp2d/**`, the CP2d status line in plan.md. Commits on the lane branch only; never push; mainnet read-only; record mode / TEST_* only.
- Observable completion and owned verification IDs: A6; V2x.1–V2x.3, V2x.5, V2x.6, V2d.7 (V2x.4 not owned, run anyway).
- Dependencies and relevant design/semantic sources: plan §1, §2, §6; `reconcile/checks/README.md` (interface, §6.1/§6.2 reorg rule, inbox gate); spec M1, M2, §4, §5; `ssv-network-triage/evidences/#91419/report-family-stale-event-scan.ts`, `#88326/report-operator-capacity-impact.ts`; ssv-network `f7ac487` (`SSVViews.isLiquidated/isLiquidatable(SSV)/getOperatorById(SSV)`, `ClusterLib.validateHashedCluster`, ISSVClusters/ISSVValidators signatures).
- Commit capability checked: yes (`git config user.name` = Marco Tabasco; commits `f0b146c`, `7bf6df7`, `ad912ce`, `7cf0bf1`).

| Step | Status | Latest evidence | Exact next action |
|---|---|---|---|
| S1 read + venv + baseline | DONE | [attempt 1](attempts.md#attempt-1): 240 passed at base | — |
| S2 M1 + M2 + fixtures + scenarios | DONE | `f0b146c`, `7bf6df7`, `7cf0bf1` | — |
| S3 V2d.7 receipts LIVE once, replay GATED | DONE | `ad912ce`; evidence/record-receipts.out, receipts.sha256 | — |
| S4 V rows, falsifiers, adversarial review | DONE | evidence/v-rows.txt, evidence/falsifiers.txt | — |
| S5 records, status, worker_done | DONE | this file | Codex QA |

## Design summary (for QA)

**M1 (`checks/m1_stale_tuple.py`, schedules OnEvents(cluster kinds) + OnInbox + Every(600 s)).** Receipts: per block and cluster the final event; if its transaction carries ≥ 2 events of the cluster, `isLiquidated(lastTuple)` at that block (IncorrectClusterState = poisoned; the earliest log of the same receipt whose tuple validates = repair tuple). State: each run re-validates, at the run's block, the last stored log of every key M1 tracks (open incident, unrepaired poisoning, unresolved revert, reorg key); the interval/reorg/force/params sweep adds the inventory's flagged clusters (last log stale or no candidate validates = `unknown`). Inbox: IncorrectClusterState reverts of `inbox_functions` from `our_senders` feed the cluster's incident. Severity: warn; page on re-poison (poisoning after a recorded repair) or poisoned and `isLiquidatable(SSV)(canonical tuple)`. Table `m1_detections` (poison / repair / revert); reorg rule: rows above the ancestor deleted (cursor rewound, re-detected from the new chain), inbox rows follow `inbox.canonical` (0 → dropped, any run), `cleared_block` above the ancestor reset; every open/persisted/reorg key is evaluated in the reorg run, a key that needs a missing inventory raises.

**M2 (`checks/m2_operator_capacity.py`, Every(600 s) + OnInbox).** Every run: operators of legacy clusters in the inventory, `getOperatorById` + `getOperatorByIdSSV` batched at the block, limit from `ctx.params`; keys `hard:<op>` (warn), `early:<op>` (info; warn if public), `velocity:<op>` (info; ValidatorAdded in `velocity_window_blocks`), `revert:<op>` (warn, ExceedValidatorLimitWithData of migrate/reactivate, any sender; settles at a later run once no legacy cluster on the operator is over the limit). Every open key is re-reported or resolved each run; unvalidated legacy clusters keep their operators' hard incident open (health). Table `m2_reverts` follows `inbox.canonical`.

Both raise a config error at construction without `views` (M1 also without `our_senders`); an RPC error, an empty answer or an unexpected revert raises; unresolved inbox reverts of watched functions are health.

## Completion handoff (≤ 250 words, also sent as the `worker_done` summary)

```text
Task / live dispatch: CP2d M1+M2 / task_aa8cd71dc363, ctx_f28f28892d56
Assignment outcome: implemented; acceptance pending Codex QA
Base -> resulting commit: 96d1c32 -> lane head named in plan.md CP2d status (implementation 7cf0bf1 + records)
Actual changed paths: reconcile/checks/{m1_cluster_abi,m1_stale_tuple,m2_operator_capacity}.py, checks/__init__.py (1 line), config.example.json (checks.m1/m2), tests/{fixtures_cluster,test_m1,test_m2,test_m1_receipts}.py, tests/fixtures/receipts/*.json (12), workers/cp2d/**; no deletions; frozen-file patches only via integration merges bd531b4, 500abe4
Verification: V2x.1 m1 38 / m2 29 passed; V2x.2 2; V2x.3 4; V2x.4 1 (extra); V2d.7 24 (12 receipts + 11 synthetic poison); suite 331 passed, 0 skips; ruff clean. NOT RUN: none
Falsifiers: F-M1a..d, F-M2a..d red then green; git status --porcelain empty after each (stale-pyc note in attempts)
Log anchor: workers/cp2d/attempts.md#attempt-1; evidence/
New finding: no real poisoned receipt on mainnet; no-validate path = real logs + the tx's own pre-call tuple emitted last (live revert). Frozen test conflict resolved centrally.
Pending human input: none
Next owner and exact action: Codex QA on the lane head
Preservation: all committed on mtabasco/ms-cp2d; venv untracked
```

Set the checkpoint status in plan.md to `IMPLEMENTED — AWAITING QA` with the head hash, send `worker_done --outcome succeeded|failed` exactly once from the dispatched terminal, then idle.

## Repair 1 (QA attempt 1 FAIL: F1–F4)

- Task / Dispatch: `task_43b3490895e5` / `ctx_ff676fc7f96b` (worker `term_53449dcf-…`, coordinator `term_89e2fa6f-…`). Requested Claude Opus high (fresh implementer); effective launch recorded by the coordinator.
- Frozen start head: `52fbb46a17ba20ba0c46a92b2407a695b8a02e44` (`git rev-parse HEAD`; QA evidence commit on top of `588888b`), tree clean, venv `reconcile/.venv` present (Python 3.12.15). No `plan/monitoring-service` merge: it only gained plan/log docs since `1154c38`.
- Implementation commit: `b94632b`. Changed paths since the start head: `reconcile/checks/{m1_cluster_abi,m1_stale_tuple,m2_operator_capacity}.py`, `reconcile/config.example.json` (`checks.m1.repair_candidates_max` removed), `reconcile/tests/{test_m1,test_m2,test_m1_m2_sweep}.py`, `plans/monitoring-service/workers/cp2d/**`. No frozen CP1B file changed (`git diff --quiet plan/monitoring-service -- reconcile/tests/{harness,test_framework,test_worker,fakechain}.py reconcile/{inventory,eb_reconcile}.py reconcile/checks/README.md` exit 0). Attempt log: [attempts.md#repair-1](attempts.md#repair-1).

### Findings → fix

| QA finding | Root cause | Fix | Regression (RED on the start head, GREEN at `b94632b`) |
|---|---|---|---|
| F1 HIGH (M1) empty API = recovery, clears `reorg:m1` | `_evaluate`: no stored log + not in inventory → `stale = False` | a key with no stored log the inventory does not list is clean only when **storage** holds no cluster word (`_in_storage`, both slots at the run's block); otherwise reorg run raises (marker kept), other runs keep the open incident + `m1-inventory-missing` health | `test_m1_reorg_with_empty_or_failed_inventory_keeps_the_unknown_key_and_marker[empty,error]` (then recovers on a valid API tuple), `test_m1_empty_inventory_outside_a_reorg_keeps_the_unknown_incident`; twin `test_m1_cluster_absent_from_storage_resolves_with_an_empty_inventory` |
| F1 HIGH (M2) `clusters: []` resolves `hard:1` | legacy list only from the inventory | new table `m2_clusters` (every validated legacy tuple, plus a reverted call's own calldata tuple), re-validated against storage when the inventory does not give a validated tuple; `hard` resolves only when the on-chain legacy count proves no unknown cluster can be blocked (`eth + missing <= limit`); unproven `ctx.reorg_keys` entry raises in the reorg run | `test_m2_empty_api_is_not_recovery_hard_kept_from_the_last_validated_tuple`, `…_during_a_reorg_keeps_hard`, `test_m2_restart_with_an_empty_api_keeps_hard`, `test_m2_unknown_tuple_with_an_empty_api_keeps_hard_and_a_reorg_run_raises[False,True]`, `test_m2_partial_api_hard_resolves_only_when_the_on_chain_count_proves_no_block[2985-True]`; twins `…[2975-False]`, `test_m2_empty_api_after_a_silent_migration_resolves_on_storage_proof` |
| F2 HIGH (M1) same-block repair + re-poison stays warn | `_scan` kept only the block's final event per cluster | per block and cluster every transaction in log order; an earlier transaction whose events all carry one tuple wrote and emitted it (valid by construction) → repair of an open poisoning; one with differing tuples cannot be validated mid-block → health `m1-intra-block`, not a repair | `test_m1_same_block_repair_then_re_poison_pages` (QA case), `test_m1_same_block_bulk_repair_then_re_poison_pages`, `test_m1_same_block_unreadable_transaction_before_a_poisoning_is_health_not_a_repair` |
| F3 HIGH (M2) orphaned settlement leaves revert cleared | `cleared_block` never rewound | reorg run: `UPDATE m2_reverts SET cleared_block = NULL WHERE cleared_block > ancestor`; revert settlement also needs a proven operator (no unknown legacy cluster, count bound) and a non-empty API | `test_m2_reorg_orphaning_the_settlement_reopens_the_surviving_revert[0,3]` (depth 1 and beyond 3 confirmations, operator stubs rewound with the chain), `test_m2_revert_does_not_settle_while_the_api_is_empty`, `test_m2_revert_of_a_cluster_the_api_omits_is_settled_on_its_own_calldata_tuple` |
| F4 MEDIUM (M1) 64-candidate cap loses log 0 | backward search truncated to `repair_candidates_max` | one storage read of the cluster's two slots at the block, every candidate hashed locally, earliest match confirmed by one `isLiquidated` (if Views disagreed: every distinct candidate by call, earliest first); config key removed | `test_m1_earliest_repair_tuple_before_66_stale_snapshots` (3 Views calls at the block: final, repair, state check), `test_m1_repair_tuple_tie_is_the_earliest_log_for_every_event_kind[8 kinds]` (QA independent case, logs delivered reversed) |

### Generalisation: every persisted table/marker and every input from outside the chain

Places each check consumes API/inventory/log data (F1 "list them"): **M1** (1) `run` sweep: inventory `flagged` adds keys only, receipt-window `skipped` clusters the inventory does not list are added too; (2) `_evaluate`: inventory `info` (absent / unknown / API tuple) — now storage-proved; (3) `_latest_log` (stored logs) — validated against storage at the run's block; (4) API tuple as repair tuple — storage-validated by the inventory, liquidatable check returns unknown if it no longer validates; (5) inbox entries. **M2** (1) legacy cluster list (hard) — inventory ∪ `m2_clusters`, count bound; (2) operator set — inventory ∪ persisted ∪ reverts ∪ open keys; (3) `_revert` settlement — same proof + unknown clusters + empty API; (4) `m2-inventory` mismatch health; (5) `_registrations` (stored `ValidatorAdded` logs; window coverage health); (6) inbox entries + transaction calldata; (7) `validators_per_operator_limit` from live params.

| State / input | (a) empty | (b) error | (c) reorg depth 1 / N (> confirmations) | (d) restart between detection and handling | (e) same-block ordering | Before repair 1 → regression |
|---|---|---|---|---|---|---|
| M1 `m1_detections` poison/repair, source receipt | no events: nothing | Views/storage RPC error or empty answer raises (rolled back) | rows with `block > A` deleted, cursor rewound, re-detected | detection and incident in one run transaction | **every transaction in log order** (was: last only) | **wrong (F2)** → same-block tests above; reorg `test_m1_reorg_*` (existing), sweep |
| M1 `m1_detections` source state (sweep) | — | raises | rows **ingested above A** now also deleted (judged on deleted storage) and re-derived from persisted/open keys | same transaction | n/a | defensive, not reachable on a consistent chain (all cluster writes emit logs) → existing reorg tests + sweep |
| M1 `m1_detections` revert, `cleared_block` | empty inbox: nothing; unresolved revert: health | tx lookup nothing raises (`test_m1_inbox_transaction_lookup_returning_nothing_raises`) | `canonical = 0` drops; `cleared_block > A` reset | inbox cursor in the run transaction | ordered by block, id | correct, **untested** → `test_m1_reorg_orphaning_the_clearing_run_reopens_the_surviving_revert` |
| M1 incidents (cluster keys, `tx:` keys) | — | kept on error (check-error) | every open/persisted/reorg key evaluated; unevaluable → raise | `test_m1_restart_*` (existing) | — | correct → existing + sweep |
| `meta reorg:m1` | — | kept while the run raises | kept until a committed reorg run | `test_m1_crash_between_the_rewind_and_the_reorg_run_*` | — | **wrong on empty API (F1)** → F1 tests above |
| M1 input: SSV API / inventory | **empty → unknown**, absence only by storage | health, sweep skipped; reorg run raises | inventory cache cleared by the framework; keys from persisted rows | API cache is memory, refetched | n/a | **wrong (F1)** → F1 tests, sweep (`api_empty`/`api_error` steps) |
| M1 input: repair search (receipt + storage) | no candidate → `repair_tuple: null` | storage/Views error raises | — | — | earliest log wins regardless of RPC arrival order | **wrong (F4)** → F4 tests |
| M2 `m2_reverts` + `cleared_block` | empty inbox: nothing; unresolved: health | tx lookup nothing raises | `canonical = 0` drops; **`cleared_block > A` reset** | ingest + evaluation in one transaction | latest by block, rowid | **wrong (F3)**; tx lookup **new** → F3 tests, `test_m2_inbox_transaction_lookup_returning_nothing_raises` |
| M2 `m2_clusters` (new) | empty API: recovered rows used, unknown rows block resolution | — | never trusted without a storage match at the run's block (no rewind needed) | persists | n/a | new → F1 M2 tests, `restart_with_an_empty_api` |
| M2 incidents `hard` | **empty/partial API → persisted + count bound** | inventory error: check-error, incidents kept | recomputed; unproven reorg key raises | `test_m2_restart_*` | n/a | **wrong (F1)** → F1 M2 tests, sweep |
| M2 incidents `early` / `velocity` | on-chain counts only; operator set kept from persisted/open keys; velocity window coverage health | operator read error/empty raises (existing tests) | velocity from rewound logs (existing depth 1/N tests) | existing | n/a | correct → existing + sweep (`early`) |
| `meta reorg:m2` | — | kept while the run raises | kept on unproven reorg key | `test_m2_crash_between_the_rewind_and_the_reorg_run` | — | **wrong on empty API (F1)** → `…_reorg_run_raises[True]` |
| M2 input: params `validators_per_operator_limit` | — | framework `ParamsUnavailable` (run rolled back) | re-read after reorg (framework) | — | — | correct → `thresholds_from_chain` |

Known semantic limit (documented, not a regression): an earlier same-block transaction whose events carry **differing** tuples cannot be validated at block granularity (storage at the block is the later transaction's); it is not counted as a repair (re-poison memory may be missing → warn instead of page) and is reported as `m1-intra-block` health naming the transaction. A delivered inbox entry of a deleted block above the new checkpoint keeps its revert open until the README §5.2 gate re-checks it (then dropped); the sweep models both.

### Adversarial sweep (`reconcile/tests/test_m1_m2_sweep.py`, committed)

40 + 40 seeded walks × 14 steps (+ recovery): deposits, bulk benign, poisonings (1–3 nested), same-block repair→poison and poison→repair, reorgs depth 1–5 (also below 2 confirmations), crash between rewind and reorg run, restarts, SSV API ok/empty/error, API-only cluster flips (M1); operator ETH count blocked/fitting, inbox reverts (`reactivate`/`migrateClusterToETH`), reorgs with operator stubs rewound, API ok/empty/error, crash, restart (M2). Asserted after every step against a model built from the walk's own action log: no resolve without positive proof, no open incident without evidence, every held condition open at its model severity (page/warn), uncommitted runs change nothing. Start head: **39 of 80 walks fail** (17 M1, 22 M2); `b94632b`: 80 passed.

### Verification (head `b94632b`, local, fake chain; [evidence/repair1-v-rows.txt](evidence/repair1-v-rows.txt))

| V-ID | Command | Result |
|---|---|---|
| V2x.1 M1 | `reconcile/.venv/bin/python -m pytest -q reconcile/tests/test_m1.py` | exit 0, 55 passed |
| V2x.1 M2 | `… reconcile/tests/test_m2.py` | exit 0, 42 passed |
| V2x.2 | `… test_m1.py test_m2.py -k thresholds_from_chain` | exit 0, 2 passed |
| V2x.3 | `… test_m1.py test_m2.py test_m1_m2_sweep.py -k restart` | exit 0, 5 passed |
| V2x.4 (not owned) | `… reconcile/tests -k rpc_budget` | exit 0, 1 passed |
| V2x.5 | `reconcile/.venv/bin/python plans/monitoring-service/workers/cp2d/falsify_repair1.py <scratch>` | exit 0; 23/23 mutations red then green (10 repair, 5 sweep-only, 8 attempt-1 F-M1a–d/F-M2a–d; F-M1c re-targeted to the block's final transaction group) — [evidence/repair1-falsifiers.txt](evidence/repair1-falsifiers.txt) |
| V2x.6 suite | `… reconcile/tests` | exit 0, 441 passed, 0 skips (the localhost metrics test included) |
| V2x.6 lint | `reconcile/.venv/bin/ruff check reconcile` | exit 0 |
| V2d.7 | `… reconcile/tests/test_m1_receipts.py` | exit 0, 24 passed (12 faithful + 11 synthetic + count) |
| sweep | `… reconcile/tests/test_m1_m2_sweep.py` | exit 0, 80 passed |
| RED on start head | implementation files restored from `52fbb46` in a scratch copy, repair-1 tests | 56 failed (17 targeted + 39 sweep walks) — [evidence/repair1-red-on-frozen.txt](evidence/repair1-red-on-frozen.txt) |
| QA reproducers | `/tmp/cp2d-qa/test_independent.py` (QA Appendix A, unmodified) | start head 6 failed / 23 passed; `b94632b` 29 passed |

NOT RUN: V2d.7 LIVE re-authentication (receipt fixtures unchanged; QA attempt 1 authenticated all 12); DEF-3 fork PoC (deferred). No RPC or network used.

### Completion handoff — repair 1 (≤ 250 words, also the `worker_done` summary)

```text
Task / dispatch: CP2d repair 1 / task_43b3490895e5, ctx_ff676fc7f96b
Outcome: F1-F4 repaired; acceptance pending fresh Codex QA
Base -> head: 52fbb46 -> b94632b (code+tests) + records commit (head in plan.md status)
Changed: checks/{m1_cluster_abi,m1_stale_tuple,m2_operator_capacity}.py, config.example.json (m1.repair_candidates_max removed), tests/{test_m1,test_m2,test_m1_m2_sweep}.py, workers/cp2d/**; no frozen file
F1: absence only from storage (M1); M2 m2_clusters + on-chain count bound; unproven reorg key raises
F2: per-transaction order within a block; one-tuple earlier tx = repair; differing tuples = health
F3: reorg un-settles cleared_block > ancestor (M2); M1 rewind now tested
F4: storage-hash search of every candidate, earliest confirmed; no cap
Verification: m1 55, m2 42, thresholds 2, restart 5, rpc_budget 1, receipts 24, sweep 80, suite 441, ruff clean; QA Appendix A 29/29; RED on start head 56; falsifiers 23/23
NOT RUN: V2d.7 LIVE re-auth (fixtures unchanged), DEF-3
Limit: mid-block multi-tuple tx not validatable (health)
Next: fresh Codex QA attempt 2
```

## Repair 2 (QA attempt 2 FAIL: F1–F4)

Dispatched as "CP2d repair 1 … fix the QA attempt-2 findings"; recorded here as **repair 2** (the lane's second repair) so it does not collide with the section above.

- Task / Dispatch: `task_1aa9f92f025e` / `ctx_e0f0fb329a9f` (worker `term_aa00fd3f-…`, coordinator `term_89e2fa6f-…`). Requested Claude Opus high (fresh implementer); effective launch recorded by the coordinator.
- Frozen start head: `cba2fb3c6c42204435fb215163a3a11d0c198b1f` (`git rev-parse HEAD`; QA evidence commit on top of `8d5b6752f5a360a59918b8faed73dce41bf7ca9b`), tree clean, venv `reconcile/.venv` present (Python 3.12.15). QA Appendix A extracted unmodified to scratch: **7 failed, 17 passed** on the start head (exactly the report's F1×4, F2, F3, F4). No `plan/monitoring-service` merge (read `qa/DEFECT-CLASSES.md` from that branch; nothing else needed).
- Implementation commit: `560f92f`. Changed paths: `reconcile/checks/{m1_stale_tuple,m2_operator_capacity}.py`, `reconcile/tests/{test_m1,test_m2,test_m1_m2_sweep}.py`, `plans/monitoring-service/workers/cp2d/**`. No frozen CP1B file, no config change (`checks.m1.receipt_window_blocks` keeps its key; its meaning narrowed, see F-gen-3).

### Findings → fix

| QA finding | Root cause | Fix | Regression (RED on `cba2fb3`, GREEN at `560f92f`) |
|---|---|---|---|
| F1 HIGH (M1) resolved key not revisited in the reorg run | keys enumerated from active rows and open incidents only | reorg run adds every key **resolved above the ancestor** (`_resolved_since`); an API-only key in storage with no inventory tuple (partial/empty API) then raises (marker kept, also across a restart) | `test_m1_reorg_orphaning_the_resolution_of_an_api_only_key_with_a_partial_api_raises[False,True]` (then a complete API reopens it); twin `test_m1_reorg_of_a_resolved_key_with_logs_reopens_without_the_inventory` |
| F1 HIGH (M2) resolved hard not revisited; the canonical tuple overwritten by the orphaned one | `m2_clusters` kept one tuple per cluster (replaced); resolved keys not enumerated | `m2_clusters` is a **history** (PK cluster, tuple; pruned below the newest row ≥ `reorg_search_depth` old), every row matched against storage, newest first; reorg run adds keys resolved above the ancestor; an unproven one raises | `test_m2_reorg_orphaning_the_resolution_restores_the_canonical_tuple_and_reopens_hard[False,True]`, `test_m2_reorg_of_a_resolved_hard_without_a_canonical_tuple_raises_and_keeps_the_marker`, `test_m2_tuple_history_is_bounded_and_keeps_the_newest_tuple_below_the_reorg_depth` |
| F2 HIGH (M1) differing-tuple repair mid-block not counted; re-poison stays warn | such a transaction was health only (`m1-intra-block`) | **coordinator ruling** (below): page under the existing incident as `unverified intra-block re-poison (no trace)`; health removed | `test_m1_unverified_two_tuple_transaction_after_a_poisoning_then_poison_pages[poison,repair]` (QA case + twin), `test_m1_same_block_unreadable_transaction_before_a_poisoning_pages_as_unverified_re_poison` (repair 1's health test, rewritten to the ruling), `…unverified_re_poison_page_survives_restart_and_resolves_on_a_validating_block`, `…reorg_removing_the_unverified_re_poison_block_returns_to_warn`, `…unverified_page_stays_for_a_further_poisoning_without_a_repair`; twin `test_m1_unverified_transaction_before_a_first_poisoning_stays_warn` |
| F3 MEDIUM (M1) poison → repair → poison in the first flagged block loses history | earlier transactions only mattered with a persisted poisoning | per-transaction walk (`_block_of_cluster`): each earlier transaction classed `clean` (one tuple: written and emitted; a repair row at its position when a poisoning is active) or `unverified`; all kept in the poisoning's `intra_block` evidence in log order; an unverified one followed by any transaction before a final poisoning pages (unverified) | `test_m1_three_transactions_poison_repair_poison_in_one_block_pages[False,True]` (logs delivered reversed) |
| F4 MEDIUM (M1) empty first API inventory silent | no M1 check of an empty API answer | `m1-inventory-empty` health whenever the API is used and returns no cluster; `last_sweep.degraded`, digest says `DEGRADED` | `test_m1_initial_empty_inventory_is_health_never_a_clean_sweep[empty,listed]` |

**F2 behaviour (coordinator ruling, to be amended into spec M1 at closure).** Only the block's last transaction on a cluster can be validated (storage at the block is its result; public endpoints have no trace RPC). An earlier transaction with differing tuples is `unverified` — it may be a poisoning or a repair. A final poisoning pages, under the existing incident, with evidence `re_poisoned: true`, `re_poison_unverified: true`, `note: "unverified intra-block re-poison (no trace)"` and `intra_block` = every earlier transaction (hash, log indexes, class, distinct tuples), whenever some reading of the unverified transactions gives poison → repair → poison: an unverified transaction after an unrepaired poisoning, a warn/page incident, or an earlier unverified one (read as the repair), or an unverified transaction followed by any transaction before the last (read as the poisoning). The page holds for the unrepaired episode (a further poisoning does not downgrade it); a later block whose last log validates resolves it plainly; a block-end state that validates in the same block resolves it (`[repair]` twin). **False-positive cost:** a benign helper with differing tuples (e.g. two deposits in one transaction; none exists in the recorded mainnet corpus, where every multi-event group carries one tuple) in the same block as a poisoning of an already flagged cluster, or before a clean transaction and a poisoning, pages instead of warning. A lone unverified transaction before a first poisoning stays warn (no such reading).

### Generalisation (beyond the four findings, each with a regression RED on `cba2fb3`)

- **G1 events between runs (class 5):** a poisoning repaired in a later block (or transaction) of the same run was silent. Now reported once (warn; page when re-poisoned or unverified) and resolved at the next run; a reorg that re-includes the same transactions does not re-send it (deduplicated against earlier alerts of the cluster naming the transaction). → `test_m1_poison_repaired_before_the_run_still_alerts`, `test_m1_poison_repaired_before_the_run_is_not_re_alerted_after_a_reorg_re_detects_it`.
- **G2 window anchored on the wrong thing (class 4/5):** `receipt_window_blocks` was anchored on each run's block, so a downtime backlog longer than 1,000 blocks skipped per-receipt validation (a poisoning repaired inside it was lost). Now anchored on the **first run** (durable `check:m1:first_run_block`): only a first start on history is bounded. → `test_m1_downtime_backlog_beyond_the_receipt_window_still_validates_every_receipt`.
- **G3 unknown is never silent (class 1):** M2 reported `m2-hard-unknown` only for an *open* hard key; an unproven operator with nothing open was silent. Now health for every unproven operator (no incident opened without a validated blocked tuple). → `test_m2_unknown_tuple_on_a_blocked_operator_without_an_open_incident_is_health`.

### State table — every persisted table/marker and every off-chain input

(a) empty · (b) error · (c) reorg depth 1 / N beyond the confirmations, incl. a resolved key · (d) restart between detection and handling · (e) same-block ordering · (f) time-window anchoring · (g) partial. "sweep" = `test_m1_m2_sweep.py`.

| State / input | (a) empty | (b) error | (c) reorg 1 / N, resolved keys | (d) restart | (e) same block | (f) window | (g) partial | Status → test |
|---|---|---|---|---|---|---|---|---|
| M1 `m1_detections` poison/repair (receipt) | no events: nothing | Views/storage RPC error or empty answer raises | rows > A deleted, cursor rewound, re-detected; re-included poisoning not re-sent | detection + incident one transaction | **per transaction, position kept, unverified page** | receipts validated for every block after the first run | — | F2/F3/G1/G2 **wrong** → tests above; reorg `test_m1_reorg_*`, sweep |
| M1 `m1_detections` state rows, revert rows, `cleared_block` | empty inbox: nothing | tx lookup nothing raises | state rows ingested > A deleted; reverts follow `inbox.canonical`; `cleared_block > A` reset | inbox cursor in run tx | inbox revert reported before it clears (`ingested_block < block`) | — | — | correct → `test_m1_reorg_orphaning_the_clearing_run…`, `test_m1_inbox_*` |
| M1 incidents (cluster, `tx:` keys), incl. **resolved** | — | kept (check-error) | open + persisted + `reorg_keys` + **resolved above A**; unevaluable → raise | `test_m1_restart_*`, F1 `[True]` | unverified page sticky per episode | — | — | F1 **wrong** → F1 tests, sweep `k2_fix`→`reorg_partial` |
| `meta reorg:m1` | — | kept while the run raises | kept until a committed reorg run | F1 `[True]`, `test_m1_crash_between…` | — | — | kept on partial API | F1 **wrong** → F1 tests, sweep |
| `meta check:m1:first_run_block` (new) | set on the first run | rolled back with the run | not rewound (a start fact) | durable | — | anchors the receipt window | — | new → G2 test |
| M1 `last_sweep` (memory) | `degraded: true` + health | not updated; `m1-inventory` health | rebuilt by the reorg run (sweep reason) | rebuilt next sweep | — | per sweep | `api_clusters` count | F4 **wrong** → F4 test |
| M1 input: SSV API / inventory | **health, never clean**; keys only kept | health, sweep skipped; reorg run raises | cache cleared by framework; keys from persisted + resolved rows | memory, refetched | — | — | omitted tracked key: kept + `m1-inventory-missing`; reorg: raise | F1/F4 **wrong** → tests above, sweep `api_empty/partial/error` |
| M1 input: stored logs | — | — | rewound by the framework | durable | log order within block, reversed RPC order tested | — | — | correct → F3 test (reversed), `tie_is_the_earliest` |
| M1 input: inbox + tx lookup | nothing | lookup nothing raises; unresolved revert health | `canonical = 0` drops | cursor in run tx | — | — | — | correct → existing |
| M1 input: params (liquidation period via Views) | Views `0x` raises | raises | re-read by framework | — | — | at the run's block | — | correct → `thresholds_from_chain` |
| M2 `m2_clusters` (**history**) | empty API: history used; unknown blocks resolution | — | never trusted without storage match; older tuple matches again after a reorg; pruned below depth | durable | — | — | partial API: history used | F1 **wrong** → M2 F1 tests, prune test, sweep `leg_fix`→`reorg_partial` |
| M2 `m2_reverts` + `cleared_block` | nothing; unresolved health | tx lookup nothing raises | `canonical = 0` drops; `cleared_block > A` reset | one transaction | latest by block, rowid | settles only at a later run | no settle while unknown / API empty | correct → repair-1 tests, sweep |
| M2 incidents hard (incl. **resolved**) | empty API: history + on-chain count | inventory error: check-error, kept | open + `reorg_keys` + **resolved above A**; unproven → raise | `test_m2_restart_*` | — | — | unknown: kept + **health even with nothing open** | F1/G3 **wrong** → tests above |
| M2 incidents early / velocity | on-chain counts only | operator read error/empty raises | velocity from rewound logs; resolved keys add their operator | existing | — | **velocity window ends at the run's checkpoint** (blocks; a 7,200-block window spans ≥ 24 h with missed slots, a superset) | operators from inventory ∪ history ∪ reverts ∪ open/resolved keys | correct → existing + sweep `burst` (window ageing) |
| `meta reorg:m2` | — | kept while raising | kept on unproven reorg/resolved key | `test_m2_crash_between…`, F1 `[True]` | — | — | — | F1 **wrong** → tests above |
| M2 input: SSV API | `m2-inventory-empty` health | check-error | — | — | — | — | `m2-inventory` mismatch health | correct → existing, sweep |
| M2 input: params `validators_per_operator_limit` | — | `ParamsUnavailable` | re-read after reorg | — | — | at the run's block | — | correct → `thresholds_from_chain` |
| Block timestamps | not consumed by M1/M2 | — | — | — | — | intervals are wall-clock `Every`; velocity in blocks (above) | — | by design |

Known limits (not regressions): (1) **storage cannot be enumerated**: a cluster with no stored log since `start_block` that the API never lists is unknown to M1/M2 (health covers empty answers and missing tracked keys). (2) Pre-existing observation, asked of the coordinator (no answer before worker_done; my recommendation A applied: no code change): when the SSV API is **ahead of the confirmed checkpoint**, a cluster with no stored log that transacts above the checkpoint gets a transient `Cluster tuple unknown` warn that resolves at the next sweep. Repro: [evidence/repro_api_ahead_of_checkpoint.py](evidence/repro_api_ahead_of_checkpoint.py). Fix options for the owner: (B) re-check a non-validating API tuple against storage at the chain head only to suppress, or (C) defer a new unknown incident one sweep (changes QA attempt 2's `first_sweep_has_no_baseline_and_wrong_hash_tuple[False]` expectation).

### Adversarial sweep (`reconcile/tests/test_m1_m2_sweep.py`, committed)

60 + 60 seeded walks × 14 steps (+ recovery), models built from the walk's own action log. New: M1 three-transaction blocks (poison/repair/poison; two-tuple helper + poisoning + poison or repair; helper + poisoning), poisoning repaired in the next block of one run, K2 storage changes without a log, partial API (only an unrelated cluster), empty **first** inventory, the composite F1 attack (`k2_fix` then `reorg_partial`: orphan the resolution, API partial/empty); M2 LEG tuple changes without a log, registration bursts (velocity window ageing), partial API, the composite F1 attack (`leg_fix` then `reorg_partial`). Asserted after every step: no resolve without positive proof; no open incident without evidence; every held condition open at its model severity, with the same-block re-poison decided by **brute force over every reading** of the unverified transactions (independent of the implementation's rule); a poisoning repaired inside one run is reported; velocity counted on the window ending at the checkpoint; empty API and unknown hard rule are health; a committed reorg run never keeps an orphaned API-only resolution without API data; uncommitted runs change nothing. Start head: **72 of 120 walks fail** (53 M1, 19 M2); `560f92f`: 120 passed.

### Verification (head `560f92f`, local, fake chain; [evidence/repair2-v-rows.txt](evidence/repair2-v-rows.txt))

| V-ID | Command | Result |
|---|---|---|
| V2x.1 M1 | `reconcile/.venv/bin/python -m pytest -q reconcile/tests/test_m1.py` | exit 0, 71 passed |
| V2x.1 M2 | `… reconcile/tests/test_m2.py` | exit 0, 47 passed |
| V2x.2 | `… test_m1.py test_m2.py -k thresholds_from_chain` | exit 0, 2 passed |
| V2x.3 | `… test_m1.py test_m2.py test_m1_m2_sweep.py -k restart` | exit 0, 6 passed |
| V2x.4 (not owned) | `… reconcile/tests -k rpc_budget` | exit 0, 1 passed |
| V2d.7 (lane row, offline replay) | `… reconcile/tests/test_m1_receipts.py` | exit 0, 24 passed (12 faithful + 11 synthetic + count) |
| reorg | `… test_m1.py test_m2.py -k reorg` | exit 0, 28 passed |
| sweep | `… reconcile/tests/test_m1_m2_sweep.py` | exit 0, 120 passed |
| V2x.6 suite | `… reconcile/tests` | exit 0, 502 passed, 0 skips (localhost metrics test included) |
| V2x.6 lint | `reconcile/.venv/bin/ruff check reconcile` | exit 0 |
| V2x.5 falsifiers | `reconcile/.venv/bin/python plans/monitoring-service/workers/cp2d/falsify_repair2.py <scratch>` | exit 0; **41/41** red then green (18 repair-2 incl. 6 sweep-only, 15 repair-1 re-run (R1-M2-persisted, R2, R3-M2 re-targeted), 8 attempt-1 F-M1a–d/F-M2a–d (F-M1a, F-M1c re-targeted)) — [evidence/repair2-falsifiers.txt](evidence/repair2-falsifiers.txt) |
| RED on start head | implementation files from `cba2fb3` in a scratch copy, final tests | **92 failed** / 146 passed (20 targeted + 72 sweep walks) — [evidence/repair2-red-on-frozen.txt](evidence/repair2-red-on-frozen.txt) |
| QA attempt 2 Appendix A | extracted unmodified | start head 7 failed / 17 passed; `560f92f` 24 passed |
| QA attempt 1 Appendix A | `/tmp/cp2d-qa/test_independent.py` (SHA-256 `1d49099c…`) | 29 passed |

Of the 20 targeted reds on the start head, three are evidence-shape reds rather than behaviour reds: `three_transactions…[True]` (new `intra_block` field), `unverified_two_tuple…[repair]` (the old `m1-intra-block` health row), `initial_empty_inventory…[listed]` (new `degraded` field).

NOT RUN: V2d.7 LIVE re-authentication (receipt fixtures unchanged; QA attempt 2 re-authenticated six and hash-matched all twelve); DEF-3 fork PoC (deferred); no RPC or network used.

### Completion handoff — repair 2 (≤ 250 words, also the `worker_done` summary)

```text
Task / dispatch: CP2d repair (QA attempt 2) / task_1aa9f92f025e, ctx_e0f0fb329a9f
Outcome: F1-F4 repaired; acceptance pending fresh Codex QA (capped round)
Base -> head: cba2fb3 -> 560f92f (code+tests) + records commit (head in plan.md status)
Changed: checks/{m1_stale_tuple,m2_operator_capacity}.py, tests/{test_m1,test_m2,test_m1_m2_sweep}.py, workers/cp2d/**; no frozen file, no config
F1: reorg runs re-check keys resolved above the ancestor (M1, M2); M2 tuple history; unevaluable -> raise, marker kept
F2: coordinator ruling: unverified intra-block re-poison (no trace) pages under the incident; false-positive cost recorded
F3: per-transaction walk, each poison/repair at its position, intra_block evidence
F4: m1-inventory-empty health, degraded sweep
Generalised: poisoning repaired within one run reported; receipt window anchored on the first run; M2 unknown hard always health
Verification: m1 71, m2 47, thresholds 2, restart 6, receipts 24, reorg 28, sweep 120, suite 502, ruff clean; QA2 App. A 24/24, QA1 29/29; RED on start head 92; falsifiers 41/41
NOT RUN: V2d.7 LIVE re-auth, DEF-3
Open: pre-existing API-ahead-of-checkpoint transient unknown warn (repro recorded; question pending, no code change)
Next: fresh Codex QA attempt 3
```
