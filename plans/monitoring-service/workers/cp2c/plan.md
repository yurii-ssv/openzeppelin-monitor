# Worker: CP2c — Wallets and supply (M12, M8 floor) plus M13 retired-key balance watch (D10)

Task contract: [plan.md §8 CP2c](../../plan.md) · Attempt log: [attempts.md](attempts.md) · QA contract: [qa/QA-CONTRACT.md](../../qa/QA-CONTRACT.md)

- Actual starting worktree/branch/head/status: `/Users/marco/orca/workspaces/ssv-network-monitor/ms-cp2c`, branch `mtabasco/ms-cp2c` (Orca new-child lane branch), head `96d1c32fe3446ec7092e849e401f851a0d9b2cf3` (equals the frozen base `plan/monitoring-service @ 96d1c32`), `git status --porcelain | wc -l` = 0.
- Live authority: Orca task `task_8384f0c7778e`, dispatch `ctx_113e2440cd8e`, run `run_96be3e0c7302`, worker terminal `term_c3481f3f-a9aa-438b-8359-4a22b2c9d297`, coordinator `term_89e2fa6f-482b-4551-995f-1f0e41c726a2`.
- Requested/effective model and effort: requested Claude Opus, effort high (brief); the worker cannot read `launch.effective` — the coordinator's start receipt is authoritative (the worker self-identifies as claude-opus-5-5).
- Allowed writes / prohibited actions / local commit authority: `reconcile/checks/m12_*.py`, `m8_*.py`, `m13_*.py`, `reconcile/tests/test_m12.py`, `test_m8.py`, `test_m13.py`, `reconcile/tests/fixtures_wallets.py`, `checks.m12`/`checks.m8`/`checks.m13` in `reconcile/config.example.json`, one line per check in the CP2c block of `reconcile/checks/__init__.py`, `plans/monitoring-service/workers/cp2c/**`, the CP2c **Status:** line in plan.md. Commits on the lane branch only; no push; mainnet read-only (eth_call, eth_getLogs, eth_getBalance and other reads, Blockscout/SSV API GETs); anvil forks only; alerts only in record mode or `TEST_*`. Frozen: `reconcile/eb_*.py`, `checks/base.py`, `registry.py`, `alerts.py`, `db.py`, `inbox.py`, `params.py`, `inventory.py`, `metrics.py`, `notify.py`, `rpc.py`, `tests/fakechain.py`, `tests/harness.py`, `checks/README.md`, other lanes' files, `execution-log.md`, `qa/**`.
- Observable completion and owned verification IDs: A5; V2x.1–V2x.3, V2x.5, V2x.6 for M12, M8, M13; V2c.7 (runway from trailing spend and from current base fee); V2c.8 (M13 balance watch). V2x.4 (`rpc_budget`) is not listed for CP2c; per-run call counts are still asserted where the coordinator asked (M13 cap).
- Dependencies and relevant design/semantic sources: plan.md §1, §2 (D3, D4, D10), §6, §7 CP2 rows, §8 CP2c; `reconcile/checks/README.md` (all); spec §3 M8, M12, M13, §4, §5. The checkpoint names no triage evidence script.
- Commit capability checked: yes — `git config user.name` = Marco Tabasco, `user.email` = marco@ssvlabs.io.
- Coordinator design answer (ask, 2026-10-21T13:4xZ): lane-local RPC helper accepted (CP3 promotion item; per-method counts kept in `ctx.rpc.counts`); M13 per-block reads capped per sweep with an explicit beyond-cap behaviour (checkpoint + bounded sample, page on any increase, finding says intra-gap netting could not be excluded), cap and long-restart-gap tests; M13 value-left resolution says plainly it is not an all-clear, reorg rule applies to `<addr>:<block>` keys; M12 Blockscout-candidates/on-chain-verification/nonce-contiguity design approved.

| Step | Status | Latest evidence | Exact next action |
|---|---|---|---|
| S1 baseline: venv, read contract, design ask | DONE | [attempt-1](attempts.md#attempt-1) | — |
| S2 fixtures (`WalletChain`, `FakeBlockscout`) | DONE | [attempt-2](attempts.md#attempt-2) | — |
| S3 M12 check + tests (V2x.1–3, V2c.7) | DONE | [attempt-3](attempts.md#attempt-3) | — |
| S4 M8 floor check + tests | DONE | [attempt-3](attempts.md#attempt-3) | — |
| S5 M13 balance watch + tests (V2c.8) | DONE | [attempt-3](attempts.md#attempt-3) | — |
| S6 V2x.5 falsifiers (8/8), V2x.6 whole suite (337) + ruff, adversarial self-review, LIVE smoke | DONE | [attempt-3](attempts.md#attempt-3) | — |
| S7 lane README (below), status line, handoff, worker_done | DONE | this file | Codex QA |
| R1 repair 1: QA attempt 1 F1 + generalised audit, sweeps, records | DONE | [attempt-4](attempts.md#attempt-4), [Repair 1](#repair-1) | fresh Codex QA |
| R2 repair 2: QA attempt 2 F1–F5 + generalised state table, sweeps, records | DONE | [attempt-5](attempts.md#attempt-5), [Repair 2](#repair-2) | fresh QA (Claude Opus 5.5) |

Before long work: append intent and persist the current step. After work: append the measured command and result and update this checkpoint. On interruption: inspect processes, Git state and the latest attempt before retrying; an absent receipt is not proof an action did not run.

On unexpected semantic failure (a spec rule that cannot be satisfied, a chain fact that contradicts the spec, a test that can only pass by weakening): preserve the reproduction, stop the affected mutation, send an Orca `ask` to the coordinator; never adjust expected behaviour to match output. Read coordinator follow-ups (`orca orchestration check --terminal <handle> --json`) before each new file, after each test run, and before `worker_done`.

## Lane README (CP2c) — M12, M8 floor, M13 balance watch

Not in `reconcile/checks/README.md` (frozen). Module docstrings are the authoritative per-check contracts.

| Check | Schedule | Keys and severities | Inputs |
|---|---|---|---|
| `m12` `checks/m12_wallet_burn.py` | `Every(interval_s=300)` | `<oracle>:balance` page < 0.15 ETH; `<oracle>:runway` warn < 14 d, page < 7 d (lower of trailing-spend and base-fee+observed-tip runways); `<oracle>:daily-spend` warn > 0.04 ETH; `<liquidator>:balance` warn < 0.2, page < 0.05 (D4 thresholds are `defaults`) | `eth_getBalance`; Blockscout v2 candidates verified by `eth_getTransactionByHash` + `eth_getTransactionReceipt` + canonical header, nonce contiguity to `eth_getTransactionCount`; headers (timestamps, `baseFeePerGas`); `eth_feeHistory`; **repair 3:** windows by chain time (`window_s` 7 d, `day_s` 24 h before the evaluated block's timestamp; header search), `eth_feeHistory` must cover the whole sample (else a check error), `m12_tx` keeps `prune_margin_blocks` (256) below the window |
| `m8` `checks/m8_supply_floor.py` | `Every(300)` | `supply-floor` warn when `cSSV.totalSupply()` < 2 × the DAO Safe's cSSV balance (measured at start, re-measured daily), or = 0 | `eth_call` `totalSupply()`, `balanceOf(seed_holder)` |
| `m13` `checks/m13_retired_keys.py` | `NearSetEveryBlock` + `Every(60)` | `<address>:<block>` page per top-up; resolved when the value LEFT (plainly "not an all-clear") or by the §6.2 rule when a reorg removed it | `eth_getBalance` at every block of (last, checkpoint] (cap `max_blocks_per_run` 256, beyond: bounded sample + `m13-gap` health); attribution: full block + `eth_getBlockReceipts` (best effort); **repair 2:** a non-archive RPC that refuses old state for `state_fallback_after` (3) consecutive runs → the newest `state_window_blocks` (64, then ÷4) are read against the last balance + `m13-state` health naming the unread range |

Required settings (raise `SystemExit` at construction when absent; dated values in `config.example.json`): `checks.m12.oracle_wallet`, `liquidator_wallet`, `fallback_tip_wei` (with `projection_tip: "observed"`); `checks.m8.cssv`, `seed_holder`; `checks.m13.addresses` ({address: label}).

State and reorg rules (README §6.1/§6.2; every table has a regression test at depth 1, depth N beyond the confirmation window, and a crash between the rewind and the run):

| State | Rule in the run with reason `reorg` (ancestor A) | Tests |
|---|---|---|
| `m12_tx` (verified txs in the window, with each block's base fee) | rows with `block > A` deleted | `test_m12_reorg_depth_1_…`, `…_depth_n_beyond_confirmations_…`, `…_marker_survives_a_crash…` |
| `m12_sync` (`synced_block`, `next_nonce`, `history_from`) | synced above A → rewound to A, nonce re-read at A (history starting above A → full re-backfill); then re-synced on the new chain; **repair 1:** any UNKNOWN spend in the reorg run raises, whatever `ctx.reorg_keys` holds (was: only with a runway/spend key in it — QA attempt 1 F1) | same + `…_reorg_run_that_cannot_measure_raises_and_keeps_the_incident`, the repair 1 rows below |
| `meta check:m12:unknown_since:<w>` | time, not block-derived | `test_m12_unknown_health_waits_for_the_grace_period` |
| `meta check:m8:floor` (seed, floor, block, time) | measured above A → re-measured at the new checkpoint; also at every start (D3) | `test_m8_reorg_*` (4), `test_m8_restart_*`; **repair 2:** the measurement carries the process's random token, so a floor of an earlier process (or of a run that rolled back after measuring) is never used (QA attempt 2 F3 → `test_qa2_m8_restart_whose_first_run_fails_after_the_seed_read_still_measures_at_start`) |
| `m13_watch` (last read block, balance) | read above A → re-read at A, then (A, checkpoint] re-read | `test_m13_reorg_*` (7) |
| `m13_topup` (one row per top-up, resolution block) | rows above A deleted (their keys, not re-found, are resolved by the framework: "evidence left the canonical chain", never re-paged); resolutions above A undone (value at risk again → new incident) | `…_removing_the_top_up_resolves_and_never_re_pages`, `…_keeps_the_top_up_…`, `…_moving_the_top_up_…`, `…_removing_the_drain_…`, `…_below_an_older_top_up_keeps_it` |
| `m13_orphan` (repair 2: what the operator was last told about a key whose block or resolution a reorg deleted) | written by the rewind (rows above A and rows resolved above A; an older entry of the key wins); settled only once the checkpoint passed the deleted block it depends on: told "arrived" → same key re-found = same incident, absent → own "evidence left the canonical chain" resolution, checkpoint still below → re-reported (kept open, says pending); told "left" → drained again / absent → silent, value still there past the deleted drain block → paged once (new incident) | `test_m13_qa2_f1a_…`, `test_m13_qa2_f1b_…`, `…_shorter_chain_without_the_top_up_…`, `…_orphaning_only_the_drain_waits_…[True/False]`, `…_reincluding_a_resolved_top_up_without_its_drain_…`, `…_pending_orphan_survives_a_restart`, `…_second_reorg_while_an_orphan_is_pending_…`, sweep (200 cases) |
| incidents | every run re-reports every managed key (finding or `resolve=True`); M12 UNKNOWN keys are skipped in normal runs (incident untouched); a reorg run with an UNKNOWN spend raises (repair 1) | all of the above + repair 1 |

**ACCEPTED GAP (D10, Marco 2026-10-21) — M13.** Not detectable with the public RPC (no trace API): transfers that net to zero within one block (`test_m13_d10_accepted_gap_within_block_net_zero_is_not_detected` documents the silence); between two read blocks when `every_n_blocks` > 1 or a gap beyond the cap was sampled (then said in the page and in an `m13-gap` health message); the attacker/consolidation addresses' internal calls and constructor interactions with SSV contracts or our wallets (spec M13 row 3). Latency: the watch runs at the worker checkpoint (`confirmations` 12 ≈ 2.4 min behind the head), not at the head.

Interpretations to amend in the spec at closure (coordinator-recorded): M12 base-fee runway adds the wallet's observed priority fee (ruling (c), attempt 2); M8 also warns at zero supply when the seed is gone (floor 0); M13 incidents stay open (re-paged 6 h) while the value sits on the retired key and resolve when it left.

CP3 items (not changed here; frozen files):
1. Promote `m12_wallet_burn.raw_batch` / `nonces` / `headers` / `balances_at` / `base_fee` into `rpc.Chain` (pinned, counted), and the `WalletChain` RPC methods + reorg of balances/stubs into `FakeChain`.
2. `tests/fake_once.py` builds every registered check with only m5 config: with any lane's required settings it now exits with a config error (needs the disable map or the example settings).
3. `config.example.json`: every lane appends after `checks.m5` (textual merge conflict point).
4. Production RPC (OG2) must answer `eth_getTransactionByHash` for 7-day-old transactions: publicnode returned null for part of them (LIVE L1), which M12 reports as UNKNOWN (never clean); drpc served all 738. **Repair 2 (QA attempt 2 §2, F2):** publicnode returns *receipts* null intermittently (transactions 0/60 null): name receipts too. M13 needs historical `eth_getBalance` (archive state) for full coverage; on a pruned node it degrades loudly (`m13-state`) after 3 failed runs instead of stalling (LIVE publicnode, attempt 5); a reorg deeper than the node's state window raises a `check-error` naming the archive requirement. Recommend an archive RPC for OG2.
5. Falsifier runs: use `PYTHONDONTWRITEBYTECODE=1` (stale `.pyc` after a same-length mutation restored within one second; attempt 3).

## Completion handoff (≤ 250 words, also sent as the `worker_done` summary)

```text
Task / live dispatch: task_8384f0c7778e / ctx_113e2440cd8e (CP2c implement).
Assignment outcome: completed; acceptance pending Codex QA.
Base -> resulting commit: 96d1c32 (+ coordinator merges bd531b4, 500abe4) -> code b24e53e; lane head = the docs commit on top (named in worker_done).
Actual changed paths: reconcile/checks/{m12_wallet_burn,m8_supply_floor,m13_retired_keys}.py, checks/__init__.py (1 line), tests/{fixtures_wallets,test_m12,test_m8,test_m13}.py, config.example.json (checks.m12/m8/m13), workers/cp2c/**, plan.md CP2c status. No deletions; frozen files unchanged vs plan/monitoring-service.
Verification: V2x.1 48/17/32 passed; V2x.2 2/1/1; V2x.3 1/1/3; V2c.7 12 passed; V2c.8 32 passed; V2x.6 337 passed, ruff clean. V2x.4 not owned (NOT RUN). No skips, no known reds.
Falsifiers: 8/8 red then green (F12a-d, F8, F13a-c), porcelain clean; run with PYTHONDONTWRITEBYTECODE=1.
Log anchor: attempts.md#attempt-3 (rows, falsifiers, LIVE publicnode UNKNOWN / drpc 0.01755 ETH/day, 18.98 d).
New finding: oracle wallet pays a fixed 1 gwei tip; base-fee-only runway never alarms -> ruling (c) 'observed' tip; spec M12 amendment at closure. CP3 items 1-5 in Lane README.
Pending human input: none.
Next owner and exact action: Codex QA on the lane head.
Preservation: all committed; LIVE scratch DBs local-only (not evidence-critical).
```

<a id="repair-1"></a>
## Repair 1 — QA attempt 1 F1 (fresh implementer, 2026-10-21)

- Live authority: Orca task `task_afcc65d1aea9`, dispatch `ctx_df1e784ecc1b`, worker terminal `term_6ebcae37-c740-455a-a2fc-968a6d1b38d9`, coordinator `term_89e2fa6f-482b-4551-995f-1f0e41c726a2`. Requested Claude Opus high (self-identifies as claude-opus-5-5; the coordinator's start receipt is authoritative).
- Frozen start head: `git rev-parse HEAD` = `326d7ff5971c3ed958af732e521c899c53716856` (QA evidence commit on `a7be022`), status clean; baseline suite 337 passed. Read `qa/cp2c-attempt-1.md` in full (F1, §2, ledger, appendices).
- Fix (code head `56a26a9`): `M12WalletBurn.run` — a `SpendUnknown` in a run with `ctx.reorg_ancestor` set raises `RuntimeError` for every spend-tracking wallet, whatever `ctx.reorg_keys` holds (README §6.2 item 4): rollback, `reorg:m12` kept, retried every step, `check-error` health, `healthy: false`. A new chain with no wallet transaction above the ancestor needs no listing (nonce at the checkpoint = nonce re-read at A), so a Blockscout outage alone does not stall a reorg run. `raw_batch` (shared by M12 and M13 reads) rejects a batch answered with fewer results than calls (`RpcError`, never a silently shorter `zip`). M8 and M13 code unchanged (their audit found no wrong row; rows were untested → tests added). Coordinator rulings untouched (observed tip, D10, M13 cap, value-left wording).
- Accepted consequence (documented in the M12 docstring): while a reorg run is pending on an UNKNOWN spend, that run's balance keys are retried with it (not evaluated) until the listing is back; the operator sees `check-error` and `healthy: false`.

### Generalised audit (every persisted state and outside input × (a) empty, (b) error, (c) reorg depth 1 / N beyond confirmations incl. a removed resolution, (d) restart between detection and handling, (e) same-block ordering, (f) window anchoring, (g) partial responses)

"tested" = a named regression; **wrong** = behaviour fixed in this repair (RED on `326d7ff`, GREEN on `56a26a9`); "untested" = correct before, regression added now.

| Check · state / input | (a) empty | (b) error | (c) reorg 1 / N, resolved key, keys not in `reorg_keys` | (d) restart | (e) same block | (f) anchoring | (g) partial |
|---|---|---|---|---|---|---|---|
| M12 `m12_tx`, `m12_sync` | zero nonce → proven zero spend (untested → `test_m12_zero_transactions_is_proven_by_a_zero_nonce`) | — | rows > A deleted, sync rewound (tested); history above A → re-backfill (untested branch → `…_reorg_below_the_backfill_start_re_backfills_the_whole_window`) | durable, continues from `synced_block` (tested) | 2 txs / block (untested → `…_two_transactions_in_one_block_are_both_counted`) | window (C−W, C] of the current run; sweep compares with a fresh worker (new) | — |
| M12 spend evaluation in the reorg run (benign persisted, open last seen ≤ A, resolved on deleted blocks) | — | UNKNOWN committed, marker cleared (**wrong**, F1 → `test_m12_qa1_f1_reorg_unknown_with_an_older_open_runway_keeps_the_marker`, `…_with_benign_persisted_spend_keeps_the_marker`, `…_reorg_removing_the_evidence_of_a_resolved_runway_reopens_it_and_unknown_waits`, `…_reorg_run_with_non_increasing_timestamps_raises`) | same | marker + UNKNOWN across a restart (**wrong** → `…_reorg_unknown_marker_survives_a_restart_and_the_new_process_still_raises`) | — | no listing needed when the nonce is unchanged (untested → `…_reorg_with_no_transaction_on_the_new_chain_needs_no_listing`) | — |
| M12 `meta unknown_since:<w>` | — | — | time, not block-derived; rolled back with a raising run | durable (tested) | — | grace from the first UNKNOWN (tested) | — |
| M12 incidents (balance, runway, daily-spend) | — | — | every key reported or the run raises; the §6.2 absence rule never fires (sweep asserts) | no duplicate (tested) | — | — | — |
| M12 Blockscout listing | empty with a nonce → UNKNOWN (tested) | down/HTTP/JSON → UNKNOWN (tested) | needed after a reorg → raise while down (F1 rows) | — | pagination by (block, index) (tested) | lag → nonce gap → UNKNOWN (sweep) | truncated/forged early stop → UNKNOWN (tested); duplicated items counted once (untested → `…_duplicated_listing_items_are_counted_once`) |
| M12 RPC tx / receipt / header / nonce / balance / fee history | null tx → UNKNOWN, balances still alert (untested → `…_null_transaction_lookup_is_unknown_and_balances_still_alert`); null balance/nonce/fee history → raise (tested) | raise (tested) | header hash must be canonical (tested) | — | — | headers at C, C−W, C−D of the run | short batch → `KeyError`/unpack check-error before, `RpcError` now (untested + unspecific → `…_short_batch_response_raises_never_a_partial_evaluation[3]`) |
| M8 `meta floor` (seed, floor, block, at) | — | re-measure fails in the reorg run → raise, orphan floor kept until it succeeds (untested → `test_m8_reorg_run_that_cannot_re_measure_raises_and_keeps_the_marker`) | measured > A → re-measured (tested); recovery on deleted blocks → re-warns (untested → `…_reorg_removing_the_recovery_of_a_resolved_key_reopens_it`); open last seen ≤ A → evaluated (untested → `…_reorg_re_evaluates_an_open_incident_last_seen_below_the_ancestor`) | re-measured at start (tested) | stubs of one block: the block's final state | floor of a canonical block (sweep asserts) | — |
| M8 `eth_call` totalSupply / balanceOf | `0x` → raise (tested) | revert → raise (tested) | — | — | — | at the checkpoint | 31/64 bytes → raise (untested → `…_partial_or_oversized_call_result_raises[2]`) |
| M13 `m13_watch` | — | re-read at A fails → raise, marker kept, also after a restart (untested → `test_m13_reorg_run_that_cannot_re_read_raises_keeps_the_marker_and_survives_a_restart`) | read > A → re-read at A (tested); baseline above A (m13 enabled on a running DB) → (A, C] of the new chain read, a top-up there pages — conservative, the baseline block is not stored (untested → `…_reorg_below_the_baseline_reads_the_new_chain_from_the_ancestor`) | durable (tested) | — | at the checkpoint with its canonical balance (sweep asserts) | — |
| M13 `m13_topup`, incidents | — | — | top-up > A deleted (tested), resolution > A undone (tested), top-up and drain both > A → silent (untested → `…_reorg_removing_a_top_up_and_its_drain_stays_silent`); open last seen ≤ A → evaluated, drained on the new chain → value-left (untested → `…_reorg_re_evaluates_an_open_top_up_last_seen_below_the_ancestor`, `…_keeps_an_open_top_up_last_seen_below_the_ancestor_without_a_second_page`) | no duplicate (tested) | two top-ups one block → one page, summed (untested → `…_two_top_ups_in_one_block_are_one_page_with_the_sum`); in+out one block: D10 accepted gap (tested) | every block of (last, C] within the cap; beyond: sample + `m13-gap` (tested) | — |
| M13 `eth_getBalance`, attribution reads | null → raise (tested); attribution empty → still pages (tested) | raise (tested); attribution failure → still pages (tested) | — | — | — | — | short batch → `KeyError` check-error before, `RpcError` now (untested + unspecific → `test_m13_short_batch_response_raises_never_a_partial_read`) |
| Logs, inbox, live params | not consumed by M12/M8/M13 | | | | | | |
| Config change (watched address removed) | not chain-derived: M12/M13 keep the old address's open incidents (re-alerted) until re-added or closed by hand; no code change (out of the reorg contract) | | | | | | |

**Adversarial sweeps (committed, deterministic, seeded; 40 cases each: seeds 0–19 × confirmations 0/2).** `test_m12_adversarial_reorg_sweep`, `test_m8_adversarial_reorg_sweep`, `test_m13_adversarial_reorg_sweep` (the class comment above each lists what is drawn). Each scripts history with outages/restarts, one reorg 1–24 blocks below the checkpoint (beyond confirmations), re-inclusion, shorter/longer replacement chains, a crash between detection and run and an input outage in the reorg run, then compares with an oracle of the final canonical chain: no resolve without positive proof, no open incident without supporting evidence, no dropped page for surviving evidence, no window anchored on stale data, no event lost between sweeps, healthy at the end (exact assertions in the tests). On `326d7ff` the M12 sweep fails 5/40 (F1); M8/M13 sweeps pass (their code is unchanged); mutations F12e-sweep, F12g-sweep, F8b-sweep, F13d-sweep turn them red (below).

### Verification (repair 1, measured on `56a26a9`, clean `__pycache__`, `PYTHONDONTWRITEBYTECODE=1`)

| Row | Command (`P` = `reconcile/.venv/bin/python -m pytest -q -p no:logging`) | Result |
|---|---|---|
| V2x.1 | `P reconcile/tests/test_m12.py`; `test_m8.py`; `test_m13.py` | exit 0; 102 / 62 / 79 passed |
| V2x.2 | `P reconcile/tests/test_m<N>.py -k thresholds_from_chain` | 2/100, 1/61, 1/78 passed/deselected |
| V2x.3 | `P … -k restart` | 2/100, 1/61, 4/75 |
| V2x.4 | not owned by CP2c | NOT RUN |
| V2x.5 | `PYTHONDONTWRITEBYTECODE=1 python3 plans/monitoring-service/workers/cp2c/falsify.py "$PWD"` | 14/14: before exit 0, mutated exit 1, restored exit 0 (F12a–d, F8, F13a–c as before; new F12e, F12e-sweep 5 failed, F12f, F12g-sweep 37 failed, F8b-sweep 13 failed, F13d-sweep 18 failed); re-run on the committed docs head: `git status --porcelain` = '' after each |
| V2x.6 | `P reconcile/tests`; `reconcile/.venv/bin/ruff check reconcile` | exit 0; 483 passed (337 + 146); ruff "All checks passed!" |
| V2c.7 (lane row) | `P reconcile/tests/test_m12.py -k "runway or projection or tip"` | 14 passed / 88 deselected |
| V2c.8 (lane row) | `P reconcile/tests/test_m13.py` | 79 passed |
| RED/GREEN | each new test function with `reconcile/checks/m12_wallet_burn.py` from `326d7ff`, then restored | RED on frozen: the 4 F1/restart tests, `…short_batch…` (3), M13 short batch, M12 sweep 5/40; the other new tests pass on both (correct-but-untested rows) |
| NOT RUN | LIVE (no network behaviour changed; QA attempt 1 re-measured LIVE on Blast); V2x.4 | — |

## Completion handoff — repair 1 (≤ 250 words, also the `worker_done` summary)

```text
Task / dispatch: task_afcc65d1aea9 / ctx_df1e784ecc1b (CP2c repair 1, fresh implementer).
Outcome: completed; acceptance pending a fresh Codex QA. Never cleared a finding myself.
Base -> result: 326d7ff (QA evidence on a7be022) -> code 56a26a9; lane head = the docs commit on top (named in worker_done).
Changed: reconcile/checks/m12_wallet_burn.py (reorg run raises on any UNKNOWN spend; raw_batch rejects short batches), tests/test_m12.py, test_m8.py, test_m13.py, workers/cp2c/{plan,attempts,falsify}, plan.md CP2c status. M8/M13 code unchanged; no frozen file touched.
F1: both QA reproducers ported (RED on 326d7ff, GREEN now) plus resolved-key, restart, metrics-UNKNOWN and no-listing rows.
Generalised audit: table in workers/cp2c/plan.md#repair-1 (M12/M8/M13 × empty/error/reorg/restart/same-block/anchoring/partial); every untested row has a regression; one more wrong row (short batch, unspecific error) fixed.
Sweeps: three seeded adversarial reorg sweeps (40 cases each); M12 sweep 5/40 red on frozen.
Verification: V2x.1 102/62/79; V2x.2 2/1/1; V2x.3 2/1/4; V2c.7 14; V2c.8 79; V2x.6 483 passed, ruff clean; V2x.5 14/14 falsifiers. NOT RUN: V2x.4 (not owned), LIVE.
Pending human input: none. Next owner: fresh Codex QA on the lane head.
```

<a id="repair-2"></a>
## Repair 2 — QA attempt 2 F1–F5 (fresh implementer, 2026-10-22)

- Live authority: Orca task `task_6cd7ace72d0e`, dispatch `ctx_3324b5e85486`, run `run_96be3e0c7302`, worker terminal `term_aaf9bdae-7674-4d1e-a2be-e5a7078dea82`, coordinator `term_89e2fa6f-482b-4551-995f-1f0e41c726a2`. Requested Claude Opus high (self-identifies as claude-opus-5-5; the coordinator's start receipt is authoritative). One interruption (laptop sleep, API error); resumed from the committed worktree state on the coordinator's "Resume" message.
- Frozen start head: `git rev-parse HEAD` = `80e174b308e7452642cc5c6a8b986598221a5193` (QA evidence commit on `c6b0a63`), status clean; `reconcile/.venv` present (CPython 3.12.15), baseline suite 483 passed. Read `qa/cp2c-attempt-2.md` in full (F1–F5, §2, §3 ledger, appendices) and `DEFECT-CLASSES.md` (from `plan/monitoring-service`). No merge of `plan/monitoring-service` was needed.
- Code head `1752b33` (fix + tests), falsifiers `6ab3441`, docs on top. Changed: `checks/m13_retired_keys.py`, `checks/m8_supply_floor.py`, `tests/test_m13.py`, `test_m8.py`, `test_m12.py`, `tests/fixtures_wallets.py` (`WalletChain.state_window`: a pruned node), `workers/cp2c/{plan,attempts,falsify}`, plan.md CP2c status. `m12_wallet_burn.py` and `config.example.json` unchanged (the new M13 settings have defaults). No frozen file touched.

### Fixes

| Finding | Root cause | Fix | Regression (RED on `80e174b` → GREEN) |
|---|---|---|---|
| F1(a) re-page of a resolved top-up re-included above A | rewind deleted the row; the re-found top-up was "new" | new table `m13_orphan` remembers what the operator was last told per orphaned key; a re-found key told "left" and drained again is silent | `test_m13_qa2_f1a_…_is_silent` (A1 verbatim), `test_qa2_m13_reorg_reincluding_a_resolved_top_up_and_its_drain_does_not_page_again` (C1) |
| F1(b) premature "evidence left" when the reorg run's checkpoint is below the top-up block | key not reported → §6.2 rule resolved it | an orphan told "arrived" is re-reported (kept open, evidence/body say pending) while the checkpoint is below its block; settled after (same incident, or own evidence-left text) | `test_m13_qa2_f1b_…_pages_once` (A2), `…_shorter_chain_without_the_top_up_resolves_only_once_the_checkpoint_passed_its_block`, sweep (24/200 red, e.g. seeds 20-0, 21-0, 30-2, 42-2, 92-0, the QA ids ≤ 99) |
| F1 generalised: an orphaned *resolution* on a shorter chain | same class (resolved key) | told "left": nothing reported until the checkpoint passes the deleted drain block; then value still there → one new page | `…_orphaning_only_the_drain_waits_for_the_drain_block[True,False]`, `…_reincluding_a_resolved_top_up_without_its_drain_pages_again_once`, `…_pending_orphan_survives_a_restart`, `…_second_reorg_while_an_orphan_is_pending_…`, `…_pending_orphan_6h_reminder_says_it_is_pending` |
| F2 stall on a non-archive RPC | every run re-read (last, C] from `last + 1` | after `state_fallback_after` (3) consecutive failed runs: re-read restricted to the newest `state_window_blocks` (64, then 16, 4, 1), compared with the last read balance (an increase in the unread range pages, located there, "archive state needed"), `m13-state` health per address naming the range; an RPC serving nothing keeps raising; a transient error (< 3 runs, reset on success) loses no block; a reorg run that cannot read the ancestor raises a `check-error` "configure an archive RPC" after 3 runs (re-sent 6 h, `healthy: false`, nothing resolved) | `test_m13_qa2_f2_pruned_rpc_restart_gap_recovers_after_bounded_failures_and_says_so`, `…_gap_without_increase_is_a_health_notice_only`, `…_state_window_config_moves_the_fallback`, `…_reorg_deeper_than_a_pruned_rpc_state_raises_a_check_error_naming_archive`; controls green on both heads: `…_archive_rpc_long_gap_never_uses_the_state_fallback`, `…_rpc_down_never_uses_the_state_fallback`, `…_transient_read_errors_below_the_threshold_lose_no_block`; LIVE publicnode (attempt 5) |
| F3 M8 previous process's floor after a failed first run | in-memory `measured` flag survived the rollback | the floor carries a per-process random token; any other token (earlier process, or none) is stale | `test_qa2_m8_restart_whose_first_run_fails_after_the_seed_read_still_measures_at_start` (A3/C1) |
| F4 attribution blocks the page | `except` listed 4 exception types | `except Exception` (noted in the page as "lookup failed") | `test_qa2_m13_malformed_receipt_entry_must_not_block_the_page` (A4/C1), `test_m13_malformed_attribution_input_never_blocks_the_page[null_receipt,junk_receipt,null_log]` (red), `[junk_logs,junk_block_txs]` (green on both: already caught) |
| F5 canonical-hash check untested | — (code correct) | QA's test ported | `test_qa2_m12_transaction_from_a_non_canonical_block_is_not_counted`: green on both heads, red under Q12a (falsifier F12h) |

All of QA attempt 2's Appendix C1 (21 scenarios) is ported verbatim (module prefix removed) into the three test files; on `80e174b` exactly F1a, F4 and F3 fail, as QA measured.

**Accepted behaviour (documented, test `…_pending_orphan_6h_reminder_says_it_is_pending`).** If the new chain does not reach a replaced top-up block within 6 h (impossible while blocks are produced: ~1,800 blocks per 6 h), the open incident's 6 h reminder is sent with the body sentence "the new chain has not reached it yet … kept open until that block is re-read". Raising instead would hold all M13 evaluation (a missed page elsewhere); re-reporting at a lower severity would re-escalate (a second page) when the key is re-found.

**Known limit (documented).** Keys are `<address>:<first block the increase was read at>`. With `every_n_blocks` > 1, or a sampled/state-window read in the reorg run, the re-found top-up can land on another key: then one "evidence left" resolution plus one new page (a duplicate, never a missed page). Default `every_n_blocks` 1 and reorgs within the cap avoid it.

### Generalised state table (every persisted table/marker and every off-chain input)

Cells: behaviour, then the test. **wrong→fixed** = RED on `80e174b`, GREEN now; "(r1)" = repair-1 test still green; "n/a" with the reason.

| State / input | (a) empty | (b) error | (c) reorg depth 1 / N > confirmations, incl. a resolved key | (d) restart between detection and handling | (e) same-block ordering | (f) window anchoring | (g) partial |
|---|---|---|---|---|---|---|---|
| M12 `m12_tx`, `m12_sync` | zero nonce = proven zero spend (r1 `…_zero_transactions_is_proven_by_a_zero_nonce`) | RPC error raises (r1 `test_m12_rpc_error_raises`) | rows > A deleted, nonce re-read at A (r1 depth-1/N tests; QA Q12c); resolved runway reopened (r1 `…_evidence_of_a_resolved_runway_reopens_it…`); C1 `test_qa2_m12_reorg_run_with_{an_empty_blockscout_page,a_partial_page,duplicated_items,a_stale_orphan_item_only}…` (green) | durable cursor (r1 `…_restart_does_not_duplicate…`, `…_reorg_unknown_marker_survives_a_restart…`) | two txs per block both counted (r1); top-up and spend in one block → end-of-block balance (C1 `test_qa2_m12_same_block_top_up_and_spend_balance_is_end_of_block`) | trailing window = chain timestamps at the checkpoint (r1 `…_window_drops_old_transactions`, `…_non_increasing_timestamps_raises`) | short batch → `RpcError` (r1) |
| M12 `meta unknown_since:<w>` | — | — | time, not block-derived; rolled back with a raising run | durable (r1) | — | grace from the first UNKNOWN (r1) | — |
| M12 incidents | — | — | every key reported or the run raises; liquidator page during a pending Blockscout-down reorg run = accepted, `check-error` + `healthy: false` (C1 `test_qa2_m12_liquidator_page_while_a_reorg_run_waits_on_blockscout`) | no duplicate (r1) | — | — | — |
| M12 Blockscout listing (off-chain, untrusted) | empty with nonce → UNKNOWN (r1) | down → UNKNOWN; in a reorg run → raise (r1, C1) | stale orphan item fails on-chain verification → raise (C1) | — | pagination by (block, index) (r1) | indexer lag → nonce gap → UNKNOWN (r1 sweep) | truncated/forged → UNKNOWN; duplicates counted once (r1, C1) |
| M12 RPC tx / receipt / header / nonce / balance / fee history (incl. block timestamps) | null tx or receipt → UNKNOWN (r1); null balance/nonce/feeHistory → raise (r1) | raise (r1) | **header hash must be canonical: now tested** (F5 → `test_qa2_m12_transaction_from_a_non_canonical_block_is_not_counted`, falsifier F12h) | — | — | headers at C, C−W, C−D of the run; base fee = mean of the last 300 at C (r1) | short batch → `RpcError` (r1) |
| M8 `meta floor` (seed, floor, block, time, **process**) | — | re-measure fails in a reorg run → raise, marker kept (r1); **first run of a process fails after the measurement → next run re-measures (wrong→fixed, F3)** | measured > A → re-measured (r1); resolved key whose recovery was orphaned reopens (r1); C1 `test_qa2_m8_daily_remeasure_on_a_deleted_block_then_reorg…`, `…_resolved_key_reorg_where_the_new_chain_still_recovers_is_silent` (green) | **re-measured at start, also after a failed first run (wrong→fixed, F3)** | — | daily re-measure by wall clock at the checkpoint (r1) | 31/64-byte result → raise (r1) |
| M8 `eth_call` totalSupply / balanceOf | `0x` → raise (r1) | revert → raise (r1); transient error after the seed read → rolled back, re-measured (F3 test) | — | — | — | at the checkpoint | → raise (r1) |
| M13 `m13_watch` | null balance → raise (r1, C1 `test_qa2_m13_empty_reorg_result_on_shorter_chain_never_clean`) | **old state refused by a pruned node → bounded fallback + `m13-state` (wrong→fixed, F2)**; RPC down → raise, watch kept (`…_rpc_down_never_uses_the_state_fallback`) | read > A → re-read at A (r1); **ancestor deeper than the node's state → loud `check-error` naming archive, marker kept (wrong→fixed, F2)** | **restart gap beyond the node's state window (wrong→fixed, F2)**; beyond the cap → sampled + `m13-gap` (r1, C1 `test_qa2_m13_long_restart_gap_bounded_reads_and_page`) | — | always (last read, checkpoint]; the fallback window ends at the checkpoint | short batch → `RpcError` (r1) |
| M13 `m13_topup` | — | — | top-up / resolution > A rewound (r1) | durable | two top-ups one block = one page with the sum (r1); top-up and spend in one block = net (D10, C1 `test_qa2_m13_same_block_top_up_and_spend`) | — | — |
| M13 `m13_orphan` (new) | — | a raising reorg run rolls the copy back (r1 `…_cannot_re_read_raises…`, sweep outage branch) | **re-included resolved pair → silent; shorter chain → kept open until passed; orphaned drain → waits, then one page (wrong→fixed, F1)**; second reorg while pending keeps the older entry (`…_second_reorg_while_an_orphan_is_pending…`) | **durable across a restart while pending** (`…_pending_orphan_survives_a_restart`; sweep restarts in the regrow phase) | settled after the run's other findings (`…_reorg_moving_the_top_up_to_another_block` keeps its order) | settled against the run's checkpoint, never earlier | — |
| M13 incidents `<address>:<block>` | — | — | ≤ 1 page per canonical top-up unless its value-left came from a deleted block and the value then sat past it; orphaned evidence paged at most once (sweep, 200 cases) | no duplicate (r1 `test_m13_restart_does_not_duplicate_the_page`) | — | 6 h re-page while the value sits (r1) | — |
| M13 attribution (`eth_getBlockByNumber` full, `eth_getBlockReceipts`) | empty → still pages (r1) | error → still pages (r1) | — | — | — | — | **null/junk receipt, null log → page with "lookup failed" (wrong→fixed, F4)**; junk logs/transactions (green on both) |
| M13 in-memory `read_failures` | — | counts consecutive failed runs; reset on a successful read | — | reset by a restart: at most 3 failed runs after any start | — | — | — |
| Logs, inbox, live params, SSV API | not consumed by M12/M8/M13 (n/a) | | | | | | |
| Config (watched address removed) | unchanged from repair 1: old incidents kept until closed by hand; an orphan of a removed address is never settled (inert) | | | | | | |

### Adversarial sweeps

`test_m13_adversarial_reorg_sweep` now runs 200 cases (seeds 0–99 × confirmations 0/2; was 40). After the random post-reorg phase the replacement chain regrows past every deleted block (restarts drawn while keys are pending), then the 6 h re-alert step. Added invariants: at most one page per canonical top-up unless its value-left resolution came from a deleted block and the canonical value sat past it; orphaned evidence paged at most once (before the reorg); a re-alert of a non-canonical key only as the pending reminder; `m13_orphan` empty. Existing invariants kept (no resolve without positive proof, no open incident without canonical evidence, no dropped page, watch at the checkpoint with its canonical balance, `m13_topup` = the oracle's top-ups, healthy). On `80e174b` (orphan-table check stubbed in a scratch copy): **24/200 red**, every one a semantic invariant (second page for identical evidence, evidence-left of a canonical top-up). The M12 and M8 sweeps (40 each) are unchanged. Wide run (scratch, not committed): M13 seeds 100–399, M12 and M8 seeds 20–219, × 0/2: **1,400 passed**.

### Verification (measured on `6ab3441`, `__pycache__` removed, `PYTHONDONTWRITEBYTECODE=1`; `P` = `reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider`)

| Row | Command | Result |
|---|---|---|
| V2x.1 | `P reconcile/tests/test_m12.py`; `test_m8.py`; `test_m13.py` | exit 0; 111 / 65 / 270 passed |
| V2x.2 | `P … -k thresholds_from_chain` | 2/109, 1/64, 1/269 passed/deselected |
| V2x.3 | `P … -k restart` | 2/109, 2/63, 7/263 |
| V2x.4 | not owned by CP2c | NOT RUN |
| V2x.5 | `PYTHONDONTWRITEBYTECODE=1 python3 plans/monitoring-service/workers/cp2c/falsify.py "$PWD"` | **21/21**: before exit 0, mutated exit 1, restored exit 0, `porcelain=''` after each. New: F13e (2 failed), F13f, F13g-sweep (24/200 failed), F13h, F13i, F8c, F12h (= QA's Q12a, now red); F13d-sweep now 81/200 |
| V2x.6 | `P reconcile/tests`; `reconcile/.venv/bin/ruff check reconcile` | exit 0; **686 passed** (483 + 203); "All checks passed!" |
| V2c.7 (lane row) | `P reconcile/tests/test_m12.py -k "runway or projection or tip"` | 16 passed / 95 deselected |
| V2c.8 (lane row) | `P reconcile/tests/test_m13.py` | 270 passed (incl. the README §6.2 row "a reorg that removes the top-up resolves/never re-pages", now also on shorter chains and for resolved keys) |
| RED/GREEN | `git archive 80e174b reconcile` in scratch, head test files + fixtures copied in | M12 111 passed (F5 test green on both: code was right; red under F12h); M8 **1 failed** (F3); M13 **42 failed** (18 named regressions + 24 sweep cases; the QA reproducers among them) |
| QA earlier falsifiers | QA attempt 1 Appendix A (`repro_a.py`) and Appendix B (`test_independent_v2.py`, line 89 → `pass` as QA did); QA attempt 2 Appendix A1–A4 scripts and C1 file | A: `marker= 164804`, incident kept; B + C1: 48 passed; A1, A2, A3, A4: each exits 0 (defect absent) |
| LIVE (F2) | `https://ethereum-rpc.publicnode.com`, scratch DB, record mode, m13 only (`live_f2.py`: baseline, `m13_watch.block −= 300`, restart, 4 steps), 2026-10-22T07:10Z | steps 1–2: `RpcError … Archive requests require a personal token`, watch kept at 26,246,262; step 3 (checkpoint 26,246,566): watch at the checkpoint, two `m13-state` messages "could not read … at blocks 26246263..26246502 …", 640 `eth_getBalance` (512 refused + 128 served); step 4: normal (2 reads), no new health |
| NOT RUN | V2x.4 (not owned); LIVE M12/M8 (unchanged behaviour; QA attempt 2 L1 on blast); anvil fork (no change to the top-up path) | — |

## Completion handoff — repair 2 (≤ 250 words, also the `worker_done` summary)

```text
Task / dispatch: task_6cd7ace72d0e / ctx_3324b5e85486 (CP2c repair 2, fresh implementer).
Outcome: completed; acceptance pending a fresh QA (Claude Opus 5.5). No finding cleared by me.
Base -> result: 80e174b (QA evidence on c6b0a63) -> code 1752b33, falsifiers 6ab3441; lane head = docs commit on top.
Changed: checks/m13_retired_keys.py (m13_orphan settlement; non-archive fallback; attribution catches all), checks/m8_supply_floor.py (per-process floor token), tests/test_m13|m8|m12.py, fixtures_wallets.py (pruned-node knob), workers/cp2c/**, plan.md status.
F1-F5: every QA reproducer ported (A1-A4, all 21 C1 scenarios); RED on 80e174b: M8 1, M13 42 (18 tests + 24/200 sweep); F5 test red under the Q12a mutation.
Generalised state table + accepted/limits: workers/cp2c/plan.md#repair-2.
Sweep: M13 200 cases with regrow, restart-while-pending, <=1 page per canonical top-up; wide 1,400 extra cases green.
Verification: V2x.1 111/65/270; V2x.2 2/1/1; V2x.3 2/2/7; V2c.7 16; V2c.8 270; V2x.6 686 passed, ruff clean; V2x.5 21/21; LIVE publicnode F2 recovers at run 3 with m13-state. NOT RUN: V2x.4, LIVE M12/M8, anvil.
Pending human input: OG2 should be an archive RPC (M13 degrades loudly otherwise).
Next owner: fresh QA on the lane head.
```

<a id="repair-3"></a>
## Repair 3 — late Codex QA attempt 2 F1–F2 (fresh implementer, 2026-10-22)

- Live authority: Orca task `task_d8441a07b246`, dispatch `ctx_30d9cb9479da`, run `run_96be3e0c7302`, worker terminal `term_e7a11e68-f722-4021-b5d7-a80944721fbb`, coordinator `term_89e2fa6f-482b-4551-995f-1f0e41c726a2`. Requested Claude Opus high (self-identifies as claude-opus-5-5; the coordinator's start receipt is authoritative).
- Frozen start head: `git rev-parse HEAD` = `f779b459bfb4faffd028c9c1b320aa5f1720372a` (QA import on `945fd4d`), status clean, `reconcile/.venv` working (686 passed). Read `qa/cp2c-attempt-2-codex.md` in full and `DEFECT-CLASSES.md` (`plan/monitoring-service`). Reproducer `qa/cp2c-attempt-2-codex-test_new.py` on `f779b45`: **6 failed, 11 passed** (as reported). No merge of `plan/monitoring-service` (not needed: no shared file changed).
- Commits: code + tests `e15e341`, falsifiers `ab84934`, restart regression `8e5cbc9`, docstring `b1b1486`, docs on top. Changed: `checks/m12_wallet_burn.py`, `tests/test_m12.py`, `test_m8.py`, `test_m13.py` (ports only), `workers/cp2c/{plan,attempts,falsify}`, plan.md CP2c status. `m8_*.py`, `m13_*.py`, `fixtures_wallets.py`, `config.example.json` unchanged (new M12 settings have defaults; the example sets none of them). No frozen file touched.

### Fixes

| Finding | Root cause | Fix | Regression (RED on `f779b45` → GREEN) |
|---|---|---|---|
| F1 windows by block count | `window_blocks` 50,400 / `day_blocks` 7,200 selected the rows; the timestamp span only normalised them (a missed slot diluted, an older tx stayed in) | settings `window_s` 604,800 / `day_s` 86,400; each window = blocks with a timestamp in (T − seconds, T], T = the evaluated block's timestamp; lower bound = the last block with timestamp ≤ T − seconds, found by a header search (12 s-slot guess, gallop down, alternating interpolation/bisection, every probe strictly between its neighbours or `SpendUnknown`); per day = spend × 86,400 ÷ window seconds (exactly 7 d / 24 h; a window reaching below block 0 uses the chain's age: test chains only). `window_blocks`/`day_blocks` are now unknown keys (config error, fail loud) | QA ports `test_qa2c_m12_daily_is_actual_24h_not_7200_blocks`, `…_daily_single_missed_slot_at_boundary`, `…_trailing_window_excludes_transactions_older_than_seven_days`; own `test_m12_r3_daily_spend_excludes_a_transaction_older_than_24h_inside_7200_blocks`, `…_missed_slots_never_dilute_the_trailing_average`, `…_daily_spend_resolves_exactly_when_the_spend_is_24h_old_by_chain_time`, `…_window_search_is_exact_and_bounded_under_a_burst_of_missed_slots`, `…_time_window_settings_are_validated` |
| F2 partial fee history | `base_fee` accepted any `baseFeePerGas` list of ≥ 2 | the answer must have `oldestBlock` = C − N + 1 (0 near genesis), N + 1 base fees, N gas ratios, every fee a hex quantity; else `PartialResult` (a check error: `check-error` health, run rolled back, incidents untouched, reorg marker kept). A null answer stays an `RpcError` | QA ports `test_qa2c_m12_partial_feehistory_is_error_never_clean`, `…_partial_history_clears_real_page`, `…_partial_history_reorg_keeps_retry_marker`; own `test_m12_r3_every_partial_fee_history_raises_and_keeps_the_page[short,shift,ratios,missing,junk,long × normal,reorg]` (12, all RED: five accepted, junk escaped the step as an `RpcError`); control `…_fee_history_of_a_chain_younger_than_the_sample_is_complete` |
| Generalised (c)+(f): a shorter replacement chain moves T, and the window, back | rows pruned at `block − window` were gone; the reorg run's quick path (nonce unchanged) undercounted a transaction at the window's edge | prune keeps `prune_margin_blocks` (256) below the window and records it in `history_from` (`m12_tx` = exactly the wallet's txs in (`history_from`, `synced_block`]); a window starting below `history_from` all the same → backfill (Blockscout; in a reorg run UNKNOWN raises, marker kept) | `test_m12_r3_shorter_chain_moving_the_window_back_still_counts_the_edge_transaction` (RED), `…_restart_between_the_shorter_chain_reorg_and_its_run_still_counts_the_edge_transaction` (RED), `…_window_below_the_stored_set_is_backfilled_never_undercounted` (margin 0: new setting) |

All 17 tests of the QA's Appendix A are ported (module prefix removed): 13 into `test_m12.py` (`test_qa2c_m12_*`, incl. the 4 listing variants), 2 into `test_m8.py`, 5 into `test_m13.py`. Of these, exactly the 6 F1/F2 tests fail on `f779b45`; all pass now.

**Interface change (QA note).** The spend metrics/evidence keys `window_blocks`, `day_blocks` became `window_from_block`, `day_from_block`, `day_seconds`, `day_fees_wei` (`window_seconds` stays, now always 604,800 on a chain older than 7 days). QA attempt 1 Appendix B's `test_runway_exact_14_and_daily_exact_004` builds the dict by hand with `day_blocks`: KeyError `day_seconds`; with `'day_seconds': 86400, 'day_from_block': 1` instead it passes (27/27).

**Base fee sample.** `base_fee_blocks` (300, ≈ 1 h) stays a block-count sample: the spec says "current base fee" with no time window; eth_feeHistory is block-indexed. Documented in the module docstring.

### Generalised state table (every persisted table/marker and every off-chain input; M12 rows re-derived, M8/M13 re-checked)

Cells: behaviour, then the test. **wrong→fixed** = RED on `f779b45`, GREEN now; "(r1)/(r2)" = repair-1/2 test still green.

| State / input | (a) empty | (b) error | (c) reorg depth 1 / N beyond confirmations, incl. a resolved key | (d) restart between detection and handling | (e) same-block ordering | (f) time-window anchoring | (g) partial |
|---|---|---|---|---|---|---|---|
| M12 `m12_tx`, `m12_sync` | zero nonce = proven zero spend (r1) | RPC error raises (r1) | rows > A deleted, nonce re-read at A (r1); resolved runway reopened (r1); **shorter chain moving the window back: margin + `history_from` (wrong→fixed)**; window below `history_from` → backfill, UNKNOWN raises (new) | durable; **crash between the shorter-chain reorg and its run (wrong→fixed)**; marker survives (r1) | two txs per block both counted (r1); end-of-block balance (r2) | **(T − 7 d, T] and (T − 24 h, T] of the evaluated block (wrong→fixed, F1)**; anchored per run, recomputed every run | short batch → `RpcError` (r1) |
| M12 time-window search (block headers, new) | missing header → `RpcError` (raises) | `RpcError` raises | headers read in the run, ≤ checkpoint: always the new chain | no state | — | exact boundary under a 3,000-slot burst, ≤ 40 search reads (`…_window_search_is_exact_and_bounded…`); LIVE mainnet: 13 reads | non-increasing timestamps on the path → `SpendUnknown` (normal run: UNKNOWN, warn kept, `…_non_increasing_timestamps_in_a_normal_run…`; reorg run: raises (r1)) |
| M12 `eth_feeHistory` | null → `RpcError` (r1) | `RpcError` | **partial in a reorg run → raises, marker kept (wrong→fixed, F2)** | — | — | sample ends at the evaluated block | **short / shifted / short ratios / missing field / junk / long → `PartialResult`, page kept (wrong→fixed, F2)**; young chain (oldest 0) accepted |
| M12 `meta unknown_since:<w>` | — | — | time, not block-derived; rolled back with a raising run (r1) | durable (r1) | — | wall clock, grace from the first UNKNOWN (r1) | — |
| M12 incidents | — | a failing run sends nothing and changes no incident (sweep, fee faults) | every key reported or the run raises (r1); resolved key reopened (r1) | no duplicate (r1) | — | daily-spend resolves at the first checkpoint ≥ 86,400 s after the spend (wrong→fixed) | — |
| M12 Blockscout listing (untrusted) | empty + nonce → UNKNOWN; reorg run raises (r2, `test_qa2c_m12_reorg_listing_variants[empty]`) | down → UNKNOWN / raise (r1) | stale orphan item fails verification (r2, `[orphan]`) | — | (block, index) pagination (r1) | lists down to the time boundary | partial → nonce gap → UNKNOWN / raise (`[partial]`); duplicates once (`[duplicate]`) |
| M12 RPC tx / receipt / header / nonce / balance | null tx/receipt → UNKNOWN (r1) | raise (r1) | canonical hash (r2) | — | — | — | short batch → `RpcError` (r1) |
| M8 `meta floor` | — | (r1, r2 F3) | (r1, r2), QA port `test_qa2c_m8_daily_floor_reorg_resolved_key[False,True]` (green on both) | per-process token (r2) | — | daily re-measure by the worker clock (D3; DEFECT-CLASSES §4 allows the wall clock) | 31/64-byte → raise (r1) |
| M13 `m13_watch`, `m13_topup`, `m13_orphan`, incidents | (r1, r2) | (r2 fallback) | (r2), QA port `test_qa2c_m13_resolved_topup_reorg_replacement[both keys]` | (r2), QA port `test_qa2c_m13_restart_gap_bounded_two_keys` | QA port `test_qa2c_m13_same_block_topup_and_spend[both orders]` (D10 net) | block-indexed by design (every block); 6 h re-page by the engine clock | (r1, r2) |
| Logs, inbox, live params, SSV API, proof API | not consumed by M12/M8/M13 (n/a) | | | | | | |

### Adversarial sweep (M12, committed, deterministic)

`test_m12_adversarial_reorg_sweep`: **200 cases** (seeds 0–99 × confirmations 0/2; was 40). New per case: a timestamp profile (12 s; a missed slot every 7 or 50 blocks; a burst of 50–3,000 missed slots in the history) and partial fee-history answers in ~8 % of runs and in 30 % of reorg runs (six fault kinds). Invariants added: the window and the last 24 h equal an **independent** computation from the chain's timestamps (count, fees, per-day average, daily spend, window seconds) at every agreement point (no window anchored on stale data or block counts); a run with a partial fee history raises (errors + 1), sends nothing but health, changes no incident row and keeps the reorg marker (no resolve without positive proof); `m12_tx` = the canonical transactions of (`history_from`, checkpoint] with `history_from` below the window start (no event lost between sweeps). Kept: open incidents = a fresh worker's on the final chain (no open incident without evidence, no dropped page), no §6.2 absence resolution, UNKNOWN reorg runs keep the marker exactly when the new chain needs a listing, healthy at the end. Coverage over the 200: profiles 12s 38 / every7 30 / every50 46 / burst 86; fee faults in 148 normal and 67 reorg runs; daily-spend open at the end in 119, runway in 111.
On `f779b45` (scratch copy, the sweep's assertions limited to keys the old code has): **184/200 red** with fee faults; **142/200 red** without them (every7 30/30, every50 46/46, burst 66/86 — bursts outside the window stay green, 12s 0/38); current code 200/200 both ways.

### Verification (measured on `b1b1486` = code `e15e341` + tests; `__pycache__` removed, `PYTHONDONTWRITEBYTECODE=1`; `P` = `reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider`)

| Row | Command | Result |
|---|---|---|
| V2x.1 | `P reconcile/tests/test_m12.py`; `test_m8.py`; `test_m13.py` | exit 0; **303 / 67 / 275** passed |
| V2x.2 | `P … -k thresholds_from_chain` | 2/301, 1/66, 1/274 passed/deselected |
| V2x.3 | `P … -k restart` | 3/300, 2/65, 8/267 |
| V2x.4 | not owned by CP2c | NOT RUN |
| V2x.5 | `PYTHONDONTWRITEBYTECODE=1 python3 plans/monitoring-service/workers/cp2c/falsify.py "$PWD"` (on `ab84934`) | **27/27**: before 0, mutated 1, restored 0, `porcelain=''` each. New: F12i (block-count windows), F12i-sweep (30/200), F12j (fee-history check off), F12j-sweep (123/200), F12k (no prune margin), F12l (no backfill below `history_from`); F12c now mutates the denominator to the window's block count; F12e-sweep 10/200, F12g-sweep 187/200 |
| V2x.6 | `P reconcile/tests`; `reconcile/.venv/bin/ruff check reconcile` | exit 0; **885 passed** (686 + 199); "All checks passed!" |
| V2c.7 (lane row) | `P reconcile/tests/test_m12.py -k "runway or projection or tip"` | 16 passed / 287 deselected |
| V2c.8 (lane row) | `P reconcile/tests/test_m13.py` | 275 passed |
| QA reproducer | `PYTHONPATH=reconcile:reconcile/tests P plans/monitoring-service/qa/cp2c-attempt-2-codex-test_new.py` | `f779b45`: 6 failed / 11 passed; now **17 passed** |
| RED/GREEN | `git archive f779b45 reconcile` in scratch + the new test files | 226 failed / 418 passed on `f779b45` (before the restart regression, itself red there: 1 failed): the 6 F1/F2 ports + 19 `r3` cases + the 200 sweep cases (also by new keys; semantic split above) + `test_m12_reorg_below_the_backfill_start…` (renamed setting) = 226; green there: the listing variants, M8/M13 ports, the young-chain control, the normal-run timestamp test. Own `r3` cases: 22, 20 RED (2 only because they use the new settings) |
| Earlier QA reproducers | attempt 1 Appendix A, B (line 89 → `pass`, line 23 dict adapted, see interface change); attempt 2 A1–A4, C1; attempt 3 scenarios (`test_qa3.py`) and its falsifier runner | A, A1–A4: exit 0 (defects absent); B + C1 + QA3 + Codex file: 27 + 94 passed; QA3 runner: 8/8 red under mutation, restored green, porcelain clean |
| LIVE (F1/F2) | `live_r3.py` (scratch): `base_fee(300)` and the window search at head − 20 on publicnode, drpc, blast; 2026-10-22T08:30Z, block 26,246,947 | all three accept the fee history (153,437,108 wei); windows: `lo_w` 26,196,706 (block count would say 26,196,547: **159 missed slots in 7 days**), `lo_d` 26,239,764 (count 26,239,747), spans 604,800 / 86,400 s, 13 header reads, boundary headers checked independently (true/true) |
| NOT RUN | V2x.4 (not owned); LIVE M12 full backfill, M8, M13 (unchanged paths); anvil fork (no change to an on-chain path) | — |

## Completion handoff — repair 3 (≤ 250 words, also the `worker_done` summary)

```text
Task / dispatch: task_d8441a07b246 / ctx_30d9cb9479da (CP2c repair 3, fresh implementer).
Outcome: completed; acceptance pending a fresh QA (Claude Opus 5.5, round-capped). No finding cleared by me.
Base -> result: f779b45 -> code e15e341, falsifiers ab84934, tests 8e5cbc9/b1b1486; lane head = docs commit on top.
Changed: checks/m12_wallet_burn.py (time windows by chain timestamps via a header search, settings window_s/day_s;
complete eth_feeHistory or PartialResult; prune margin + backfill when the window starts below the stored set),
tests/test_m12|m8|m13.py, workers/cp2c/**, plan.md status.
F1/F2: all 17 Codex Appendix A tests ported; the 6 F1/F2 ones RED on f779b45, GREEN now; 22 own regression
cases (20 RED) incl. 12 fee-fault variants, shorter-chain edge + restart.
State table + interface change (metric keys): workers/cp2c/plan.md#repair-3.
Sweep: M12 200 cases with slot profiles and fee faults, independent time-window oracle; 184/200 red on f779b45.
Verification: V2x.1 303/67/275; V2x.2 2/1/1; V2x.3 3/2/8; V2c.7 16; V2c.8 275; V2x.6 885 passed, ruff clean;
V2x.5 27/27; earlier QA reproducers green; LIVE: 3 RPCs accept fee history, mainnet had 159 missed slots in 7 d.
NOT RUN: V2x.4, LIVE M8/M13/full backfill, anvil.
Next owner: fresh QA on the lane head.
```
