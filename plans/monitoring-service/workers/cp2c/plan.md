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

Before long work: append intent and persist the current step. After work: append the measured command and result and update this checkpoint. On interruption: inspect processes, Git state and the latest attempt before retrying; an absent receipt is not proof an action did not run.

On unexpected semantic failure (a spec rule that cannot be satisfied, a chain fact that contradicts the spec, a test that can only pass by weakening): preserve the reproduction, stop the affected mutation, send an Orca `ask` to the coordinator; never adjust expected behaviour to match output. Read coordinator follow-ups (`orca orchestration check --terminal <handle> --json`) before each new file, after each test run, and before `worker_done`.

## Lane README (CP2c) — M12, M8 floor, M13 balance watch

Not in `reconcile/checks/README.md` (frozen). Module docstrings are the authoritative per-check contracts.

| Check | Schedule | Keys and severities | Inputs |
|---|---|---|---|
| `m12` `checks/m12_wallet_burn.py` | `Every(interval_s=300)` | `<oracle>:balance` page < 0.15 ETH; `<oracle>:runway` warn < 14 d, page < 7 d (lower of trailing-spend and base-fee+observed-tip runways); `<oracle>:daily-spend` warn > 0.04 ETH; `<liquidator>:balance` warn < 0.2, page < 0.05 (D4 thresholds are `defaults`) | `eth_getBalance`; Blockscout v2 candidates verified by `eth_getTransactionByHash` + `eth_getTransactionReceipt` + canonical header, nonce contiguity to `eth_getTransactionCount`; headers (timestamps, `baseFeePerGas`); `eth_feeHistory` |
| `m8` `checks/m8_supply_floor.py` | `Every(300)` | `supply-floor` warn when `cSSV.totalSupply()` < 2 × the DAO Safe's cSSV balance (measured at start, re-measured daily), or = 0 | `eth_call` `totalSupply()`, `balanceOf(seed_holder)` |
| `m13` `checks/m13_retired_keys.py` | `NearSetEveryBlock` + `Every(60)` | `<address>:<block>` page per top-up; resolved when the value LEFT (plainly "not an all-clear") or by the §6.2 rule when a reorg removed it | `eth_getBalance` at every block of (last, checkpoint] (cap `max_blocks_per_run` 256, beyond: bounded sample + `m13-gap` health); attribution: full block + `eth_getBlockReceipts` (best effort) |

Required settings (raise `SystemExit` at construction when absent; dated values in `config.example.json`): `checks.m12.oracle_wallet`, `liquidator_wallet`, `fallback_tip_wei` (with `projection_tip: "observed"`); `checks.m8.cssv`, `seed_holder`; `checks.m13.addresses` ({address: label}).

State and reorg rules (README §6.1/§6.2; every table has a regression test at depth 1, depth N beyond the confirmation window, and a crash between the rewind and the run):

| State | Rule in the run with reason `reorg` (ancestor A) | Tests |
|---|---|---|
| `m12_tx` (verified txs in the window, with each block's base fee) | rows with `block > A` deleted | `test_m12_reorg_depth_1_…`, `…_depth_n_beyond_confirmations_…`, `…_marker_survives_a_crash…` |
| `m12_sync` (`synced_block`, `next_nonce`, `history_from`) | synced above A → rewound to A, nonce re-read at A (history starting above A → full re-backfill); then re-synced on the new chain; **repair 1:** any UNKNOWN spend in the reorg run raises, whatever `ctx.reorg_keys` holds (was: only with a runway/spend key in it — QA attempt 1 F1) | same + `…_reorg_run_that_cannot_measure_raises_and_keeps_the_incident`, the repair 1 rows below |
| `meta check:m12:unknown_since:<w>` | time, not block-derived | `test_m12_unknown_health_waits_for_the_grace_period` |
| `meta check:m8:floor` (seed, floor, block, time) | measured above A → re-measured at the new checkpoint; also at every start (D3) | `test_m8_reorg_*` (4), `test_m8_restart_*` |
| `m13_watch` (last read block, balance) | read above A → re-read at A, then (A, checkpoint] re-read | `test_m13_reorg_*` (7) |
| `m13_topup` (one row per top-up, resolution block) | rows above A deleted (their keys, not re-found, are resolved by the framework: "evidence left the canonical chain", never re-paged); resolutions above A undone (value at risk again → new incident) | `…_removing_the_top_up_resolves_and_never_re_pages`, `…_keeps_the_top_up_…`, `…_moving_the_top_up_…`, `…_removing_the_drain_…`, `…_below_an_older_top_up_keeps_it` |
| incidents | every run re-reports every managed key (finding or `resolve=True`); M12 UNKNOWN keys are skipped in normal runs (incident untouched); a reorg run with an UNKNOWN spend raises (repair 1) | all of the above + repair 1 |

**ACCEPTED GAP (D10, Marco 2026-10-21) — M13.** Not detectable with the public RPC (no trace API): transfers that net to zero within one block (`test_m13_d10_accepted_gap_within_block_net_zero_is_not_detected` documents the silence); between two read blocks when `every_n_blocks` > 1 or a gap beyond the cap was sampled (then said in the page and in an `m13-gap` health message); the attacker/consolidation addresses' internal calls and constructor interactions with SSV contracts or our wallets (spec M13 row 3). Latency: the watch runs at the worker checkpoint (`confirmations` 12 ≈ 2.4 min behind the head), not at the head.

Interpretations to amend in the spec at closure (coordinator-recorded): M12 base-fee runway adds the wallet's observed priority fee (ruling (c), attempt 2); M8 also warns at zero supply when the seed is gone (floor 0); M13 incidents stay open (re-paged 6 h) while the value sits on the retired key and resolve when it left.

CP3 items (not changed here; frozen files):
1. Promote `m12_wallet_burn.raw_batch` / `nonces` / `headers` / `balances_at` / `base_fee` into `rpc.Chain` (pinned, counted), and the `WalletChain` RPC methods + reorg of balances/stubs into `FakeChain`.
2. `tests/fake_once.py` builds every registered check with only m5 config: with any lane's required settings it now exits with a config error (needs the disable map or the example settings).
3. `config.example.json`: every lane appends after `checks.m5` (textual merge conflict point).
4. Production RPC (OG2) must answer `eth_getTransactionByHash` for 7-day-old transactions: publicnode returned null for part of them (LIVE L1), which M12 reports as UNKNOWN (never clean); drpc served all 738.
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
