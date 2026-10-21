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
| `m12_sync` (`synced_block`, `next_nonce`, `history_from`) | synced above A → rewound to A, nonce re-read at A (history starting above A → full re-backfill); then re-synced on the new chain; a re-sync that is UNKNOWN while a runway/spend key is in `ctx.reorg_keys` raises | same + `…_reorg_run_that_cannot_measure_raises_and_keeps_the_incident` |
| `meta check:m12:unknown_since:<w>` | time, not block-derived | `test_m12_unknown_health_waits_for_the_grace_period` |
| `meta check:m8:floor` (seed, floor, block, time) | measured above A → re-measured at the new checkpoint; also at every start (D3) | `test_m8_reorg_*` (4), `test_m8_restart_*` |
| `m13_watch` (last read block, balance) | read above A → re-read at A, then (A, checkpoint] re-read | `test_m13_reorg_*` (7) |
| `m13_topup` (one row per top-up, resolution block) | rows above A deleted (their keys, not re-found, are resolved by the framework: "evidence left the canonical chain", never re-paged); resolutions above A undone (value at risk again → new incident) | `…_removing_the_top_up_resolves_and_never_re_pages`, `…_keeps_the_top_up_…`, `…_moving_the_top_up_…`, `…_removing_the_drain_…`, `…_below_an_older_top_up_keeps_it` |
| incidents | every run re-reports every managed key (finding or `resolve=True`); M12 UNKNOWN keys are skipped in normal runs (incident untouched) and raise in reorg runs | all of the above |

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
