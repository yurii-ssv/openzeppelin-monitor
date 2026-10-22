PASS WITH NOTES

# CP2c independent QA — attempt 4 (after repair 3, round-capped)

Frozen head under test: `dd9522b72df84939fa17b32a7c6637b81cf2c1cb` (lane `mtabasco/ms-cp2c`; code `e15e341`, falsifiers `ab84934`, tests `8e5cbc9`/`b1b1486`, docs `dd9522b`), start head of repair 3 `f779b45`. QA branch `mtabasco/ms-qa-cp2c-4-2`. Task `task_812997eac1bf`, dispatch `ctx_6ee9e307ca8b`. QA executor: Claude Opus 5.5, effort high (D13). Measured 2026-10-22, 08:45Z–09:05Z UTC. This is an independent re-measurement. I made no implementation repair.

**Round policy (capped attempt).** I classify every finding as BLOCKING or NOTE and give a one-line reason for each. **No BLOCKING finding.** The verdict is therefore **PASS WITH NOTES**.

Repair 3 is confirmed:
- The late Codex reproducer (`qa/cp2c-attempt-2-codex-test_new.py`) is **17/17 green** on the head. On a scratch copy of `f779b45` it gives **6 failed / 11 passed**, exactly the F1/F2 tests.
- Of the 39 repair-3 regressions, 26 are red on `f779b45` (the 6 ports and 20 own `r3` cases) and all are green on the head.
- In my own targeted scenarios, a depth-1 reorg that removes a run of missed slots moves the 7-day window back by 30 slots (inside the prune margin) or 300 slots (beyond it, so it backfills). Both count the edge transaction and reopen the page.
- On 12 random timestamp profiles, the windows match a brute-force oracle at every run.
- LIVE (blast, cross-checked on nodies): the window starts 160 blocks later than a block count would put it. The nonce difference across the window equals the check's 748 transactions.

## 1. Findings (ranked; none BLOCKING)

### N1 — NOTE — M13 full coverage needs archive state (carried from attempt 3, OG2)

M13 code is unchanged since attempt 3 (`git diff 945fd4d HEAD -- reconcile/checks/m13_retired_keys.py` is empty). After 3 failed runs on a pruned RPC, the check falls back to the newest 64 blocks, and it announces the unread range in an `m13-state` health message. Reproducer: attempt 3 §3.4 L1. I did not re-run it, because the code is unchanged.

**NOTE, because** the loss of coverage is announced and an increase that is still on the key still pages. It is the documented degraded mode of a non-archive RPC. Owner: Marco (OG2: an archive RPC for production).

### N2 — NOTE — M8 floor follows the Safe's balance at each daily re-measure and every restart (carried; D3)

The floor is measured at worker start and re-measured daily. A cSSV inflow raises the floor only at the next measure, and a restart re-measures it. Reproducer: `test_q4e_m8_first_run_baseline_then_safe_inflow_mid_day_and_restart` (Appendix A):
1. At start, 1000 ≥ 800: silent.
2. The Safe goes to 600 mid-day: still silent.
3. After a restart, the floor is 1200 > 1000: warn.

The attempt-3 QA3 M8 scenarios are 5/5 green.

**NOTE, because** this is D3 implemented as written. Owner: coordinator (spec M8 amendment at closure).

### N3 — NOTE — `fallback_tip_wei` is dated only in the docs, and it is evidence-only (carried)

`config.example.json` has `"fallback_tip_wei": 1000000000` with no date. The tip applies only when the window holds no transaction. Then window gas is 0, so the projection is 0 and `runway_base_fee_days` is `None`; `tip_source` flags this in the evidence.

**NOTE, because** it has no effect on any alert. Owner: CP3 integrator (a dated `_comment` key or a README line).

### N4 — NOTE — the base-fee sample is still a block-count sample (`base_fee_blocks` 300 ≈ 1 h)

The trailing and daily windows now use chain time (the old attempt-3 N4 is FIXED). The "current base fee" is still the mean of the last 300 blocks' `baseFeePerGas` from `eth_feeHistory`, documented in the module docstring (`m12_wallet_burn.py:14-16`).

**NOTE, because** spec M12 names no time window for "current base fee", and this is a smoothing sample, not a time-based trigger (DEFECT-CLASSES 4 covers time-based rules such as "24 h"). The sample is also validated as complete (F2 repair). Owner: coordinator (spec wording, if a time-defined sample is wanted).

### N5 — NOTE — hygiene: unregistered marks; malformed RPC reads take two fail-closed paths (carried, extended)

- `pytest.mark.m8/m12/m13` are still unregistered, so every run prints `PytestUnknownMarkWarning`.
- A null `eth_getTransactionCount` or a header without `timestamp` raises `RpcError`. The worker's RPC-outage path then aborts the step, which is retried.
- A receipt `gasUsed: "0x"` raises `ValueError`, which is a `check-error`; the run is rolled back.

Reproducer: `test_q4d_null_and_wrong_shaped_reads_are_errors_not_clean` (Appendix A) prints `nonce_null rpc-outage`, `ts_missing rpc-outage`, `gasused_0x check-error`. In all three cases the open runway page is unchanged and no resolution is sent.

**NOTE, because** both paths fail closed; this is cosmetic or diagnostic. Owner: CP3 integrator.

### Observations (not findings)

- **Header-search cost.** In the LIVE first run, the backfill read 763 headers (750 for transaction verification plus the two searches). An incremental run reads 13 headers, 1 `eth_feeHistory`, 2 balances and 1 nonce. Bounded; in my random profiles the searches stay ≤ 120 reads per run.
- **Interface change.** The metric and evidence keys `window_blocks`/`day_blocks` became `window_from_block`, `day_from_block`, `day_seconds` and `day_fees_wei`. The settings `window_blocks`/`day_blocks` are now unknown keys, which fail loudly at construction. `config.example.json` sets neither.

## 2. Claims I could NOT confirm

- **"publicnode returns null txs/receipts intermittently."** Not reproduced. 3 rounds × 40 hashes from my LIVE `m12_tx` (the 20 oldest and 20 newest in the window), `eth_getTransactionByHash` and `eth_getTransactionReceipt` on `https://ethereum-rpc.publicnode.com` at 2026-10-22T08:59:51Z gave **0 null, 0 error out of 240**. Attempt 2 saw intermittent null receipts; the Codex attempt saw 11/11 non-null. The code path is covered on the fake chain: a null tx or receipt is UNKNOWN and is never counted as no spend.
- **The implementer's M12 sweep reddening on `f779b45` (184/200 with fee faults, 142/200 without).** I did not re-run it, per the owner scope note (no falsifier campaign). The sweep's assertions use the new metric keys, so on the old code it fails by key as well as by semantics. I relied on the 26 named regressions above, the 6 Codex tests and my own scenarios instead.
- **V2x.5 falsifiers (implementer 27/27).** NOT RUN, per the owner scope note "do NOT run mutation testing". My sensitivity evidence uses no mutation: the RED/GREEN of the regressions on `f779b45`, and the fail-open reorg plugin (§4), which reddens all three suites.
- **LIVE M8, M13 and the anvil fork top-up.** NOT RUN: the M8/M13 code is unchanged since attempt 3, which measured them (LIVE L1), and the brief asks me to avoid redundant expensive rows.

I reproduced every other repair-3 claim myself: the V row counts, the Codex reproducer, the RED/GREEN split, LIVE fee-history completeness on the public RPCs, and LIVE window bounds.

## 3. Verification matrix

**Preflight.**
- `git rev-parse HEAD` = `dd9522b72df84939fa17b32a7c6637b81cf2c1cb` (matches the dispatch). `git status --porcelain` was empty at the start. 337 GiB free.
- Venv created per plan §7, both commands exit 0: `uv venv reconcile/.venv --python 3.12` and `uv pip install --python reconcile/.venv/bin/python -r reconcile/requirements-dev.txt` (Python 3.12.15, pytest 9.1.1, ruff 0.17.1). No other install.
- `export PATH=$HOME/.foundry/bin:$PATH` was set; anvil was not needed (no fork row run).

**Frozen interfaces.**
- `git diff --exit-code plan/monitoring-service -- reconcile/tests/harness.py reconcile/tests/test_framework.py reconcile/tests/test_worker.py` exits 0. `git merge-base --is-ancestor bd531b4 HEAD` and `… 500abe4 HEAD` both exit 0.
- CP1B tree `b07d2c6` ∩ files changed on the lane = `checks/__init__.py`, `config.example.json` and the three test files above. The integration branch changed the same five files.
- The lane's diff against `plan/monitoring-service` in `checks/__init__.py` and `config.example.json` is only the other lanes' registrations and config, which the lane lacks.
- Repair 3 changed `m12_wallet_burn.py`, `test_m12/m8/m13.py` and `workers/cp2c/**`/plan.md only (`git diff --name-only f779b45 HEAD`). No frozen file was edited.

`P` = `PYTHONDONTWRITEBYTECODE=1 reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider`. Each command was run separately from a bash script (`vrows.sh`), with its exit code captured.

| V-ID | Command | Exit / measured | Outcome |
|---|---|---|---|
| V2x.1 / V2c.1 M12 | `P reconcile/tests/test_m12.py` | 0; **303 passed** (incl. the 200-case sweep) | PASS |
| V2x.1 / V2c.1 M8 | `P reconcile/tests/test_m8.py` | 0; **67 passed** | PASS |
| V2x.1 / V2c.1 M13 | `P reconcile/tests/test_m13.py` | 0; **275 passed** | PASS (accepted gap D10) |
| V2x.2 | `P reconcile/tests/test_m<N>.py -k thresholds_from_chain` ×3 | 0/0/0; 2/301, 1/66, 1/274 passed/deselected | PASS |
| V2x.3 | `P reconcile/tests/test_m<N>.py -k restart` ×3 | 0/0/0; 3/300, 2/65, 8/267 | PASS |
| V2x.4 | not owned by CP2c | NOT RUN | N/A |
| V2x.5 | implementer falsifier runner | **NOT RUN** (owner scope: no mutation testing); non-source falsifier in §4 | skip, by instruction |
| V2x.6 suite | `P reconcile/tests` | 0; **885 passed** in 22.2 s | PASS |
| V2x.6 lint | `reconcile/.venv/bin/ruff check reconcile` | 0; "All checks passed!" | PASS |
| V2c.7 | `P reconcile/tests/test_m12.py -k "runway or projection or tip"` | 0; **16 passed**, 287 deselected | PASS (see §3.2) |
| V2c.8 | `P reconcile/tests/test_m13.py` | 0; 275 passed | PASS |
| Codex reproducer on the head | `PYTHONPATH=reconcile:reconcile/tests P $S/codex/test_new.py` (a byte copy of `qa/cp2c-attempt-2-codex-test_new.py`) | 0; **17 passed** | F1/F2 FIXED |
| Codex reproducer on `f779b45` | `git archive f779b45 reconcile` → `$S/old`, then the same command with `PYTHONPATH=$S/old/reconcile:…` | 1; **6 failed / 11 passed**: `daily_is_actual_24h_not_7200_blocks`, `partial_feehistory_is_error_never_clean`, `daily_single_missed_slot_at_boundary`, `partial_history_clears_real_page`, `trailing_window_excludes_transactions_older_than_seven_days`, `partial_history_reorg_keeps_retry_marker` | RED as reported |
| Repair-3 regressions on `f779b45` | `$S/old` + the head's `test_m12/m8/m13.py` (M8/M13 modules and `fixtures_wallets.py` byte-identical): `P … -k "r3 or qa2c"` | 1; **26 failed / 13 passed**: the 6 `qa2c` F1/F2 ports and 20 `r3` (8 window/margin/settings plus 12 fee-fault variants). Green there: listing variants, M8/M13 ports, young-chain control, normal-run timestamp test | RED on start, GREEN on head |
| Attempt-3 QA3 scenarios | `PYTHONPATH=reconcile:reconcile/tests P $S/qa3/test_qa3.py` (extracted verbatim from attempt-3 Appendix A) | 0; **29 passed** | PASS (exact D4 boundaries, overrides, crash before delivery, M8/M13 keys) |
| Own attempt-4 scenarios | `PYTHONPATH=reconcile:reconcile/tests P $S/qa4/test_qa4.py` (Appendix A) | 0; **20 passed** | PASS (§3.3) |

### 3.1 Spec trigger list (derived by me from spec §3 M8, M12, M13, D3, D4, D10)

| Spec trigger (owned part) | Severity | Positive test | Benign or boundary twin |
|---|---|---|---|
| M12 oracle balance < 0.15 ETH | page | `test_m12_oracle_balance_below_015_pages` | `…above_015_is_silent`; QA3 `oracle_balance_exactly_015` (silent at exactly 0.15) |
| M12 oracle runway < 14 d | warn | `…runway_below_14_days_warns` | `…runway_of_20_days_is_silent`; QA3 `runway_exactly_14_days` |
| M12 oracle runway < 7 d | page | `…runway_below_7_days_pages` | `…runway_exactly_7_days_warns_not_pages` |
| M12 oracle daily spend > 0.04 ETH | warn | `…daily_spend_above_004_warns`; `test_m12_r3_daily_spend_resolves_exactly_when_the_spend_is_24h_old_by_chain_time` | `…daily_spend_below_004_is_silent`; QA3 `daily_spend_exactly_004` |
| M12 liquidator < 0.2 ETH | warn | `…liquidator_below_02_warns` | `…liquidator_above_02_is_silent`; QA3 `liquidator_exact_boundaries` |
| M12 liquidator < 0.05 ETH | page | `…liquidator_below_005_pages` | `…liquidator_at_006_warns_only`; QA3 exact 0.05 → warn only |
| M12 runway from trailing 7-day spend **and** current base fee | (as above) | `…runway_from_trailing_spend_and_from_current_base_fee`, `…trailing_spend_drives_when_the_base_fee_fell` | V2c.7 selection (16) |
| M8 `cSSV.totalSupply()` < 2 × Safe cSSV (D3) | warn | `test_m8_supply_below_twice_the_seed_warns` | `…supply_at_the_floor_is_silent`, `…well_above_the_floor_is_silent` |
| M13 (D10) any incoming value to `0x3A98…` / `0x4BC6…` | page | `test_m13_direct_top_up_of_either_retired_key_pages_at_the_checkpoint[both]`, `…internal_transfer_from_a_helper_contract_pages`, `…internal_transfer_without_any_log_still_pages` | `…zero_delta_blocks_stay_silent`, `…outgoing_value_stays_silent`, `…first_observation_is_a_silent_baseline` |

The other M8 triggers (`FeesSynced(0)`, the seed holder's `UnstakeRequested`, the `ZeroCSSVSupply` revert) and the other M13 rows (`commitRoot`/`updateClusterBalance` attempts, attacker interactions) are CP1A Monitor rules and outside this lane.

**Thresholds are config-overridable:** QA3 `test_q3_m12_every_threshold_is_config_overridable` is green. **Strict `<` / `>` at every boundary:** read in `balance_finding`/`spend_findings` and measured by the QA3 exact tests.

### 3.2 Runway decision (event-0040)

- The default is `projection_tip: "observed"` (`defaults`).
- The alarm takes `min(trailing, base_fee)` over the non-`None` runways (`spend_findings`, lines 348-349).
- The evidence carries both runways and their inputs. LIVE evidence: `runway_trailing_days`, `runway_base_fee_days`, `base_fee_wei`, `base_fee_blocks`, `avg_priority_fee_wei`, `avg_daily_gas`, `tip_source: "observed over 748 txs"`, `window_seconds`, `day_seconds`.
- An empty tip sample falls back to `fallback_tip_wei`, which is required and > 0 at construction, and `tip_source` flags it.
- `"none"` sets tip = 0, while observed is `max(observed, 0)`. So `"none"` can only lengthen `runway_base_fee_days` or leave it unchanged, never shorten it: it is quieter or equal, never louder.

### 3.3 Own targeted scenarios (Appendix A)

Each scenario names the defect class it attacks.

| Scenario | Class / key | Result |
|---|---|---|
| `test_q4a_reorg_removing_a_missed_slot_gap_moves_the_window_back[30]` | 4 + 2: a depth-1 reorg replaces a block that came after 30 missed slots with one that did not. T moves back 360 s without the chain shrinking, so the window starts 30 blocks earlier. `m12_tx` and `m12_sync.history_from` stay inside the 256-block margin. | PASS. Edge tx counted from the margin with **0 Blockscout calls** in the reorg run; runway page reopened (diag: `history_from` 99,755 < new lo 99,981) |
| `…[300]` | same, 300 missed slots (beyond the margin) | PASS. `history_from` 100,025 > new lo 99,981, so the run backfills (**1 Blockscout call**). Window = the brute-force set; page reopened |
| `test_q4a_beyond_margin_with_blockscout_down_raises_and_keeps_the_marker` | 1 + 2: the reorg run needs a listing, and Blockscout is down | PASS. `reorg:m12` kept; no false page from guesses, no false clean. Next run with Blockscout up: counted, page |
| `test_q4b_random_timestamps_windows_equal_the_oracle[0..11]` | 4: irregular seconds (1–30 s, not 12 s multiples), outages up to 1 h, 0.5 % jumps of 86,399/86,400/86,401/100,000 s; 6 runs at random increments | PASS. Every run: `window_from_block`/`day_from_block` = a brute-force linear scan + 1; `window_txs`, `window_fees_wei` and `day_fees_wei` equal the independent sets; `window_seconds` = 604,800; `day_spend_wei` = the last 24 h fees; header reads ≤ 120; final incidents = a fresh worker's. (Illustrative diag on the same profile generator: block-count bounds differ from the time bounds at 77/84 sampled checkpoints) |
| `test_q4c_restart_with_a_longer_window_backfills` | 11 + 2: `window_s` changed from 7 d to 8 d across a restart (a parameter changed mid-run) | PASS. The window starts below `history_from`, so the run backfills and counts the 7.5-day-old 0.5 ETH tx; no undercount |
| `test_q4d_zero_tx_window_is_clean_only_with_a_proven_older_nonce` | 1: the wallet has zero txs in the window | PASS. With the 8-day-old tx listed: proven zero spend. A fresh process whose Blockscout hides it: **UNKNOWN** plus `m12-spend` health, never clean |
| `test_q4d_null_and_wrong_shaped_reads_are_errors_not_clean` | 1: null nonce, a header without timestamp, `gasUsed "0x"` during a forced backfill with a page open | PASS: error every time, page unchanged, no resolution (N5) |
| `test_q4e_m8_first_run_baseline_then_safe_inflow_mid_day_and_restart` | 10 + 11 on `meta floor`: first-run baseline, a mid-day Safe inflow, a restart | PASS (D3; N2) |
| `test_q4e_m13_reorg_moves_topup_to_a_helper_transfer_in_the_same_block` | 2 + 3 on `<addr>:<block>`: a direct top-up is reorged away and replaced in the same block by an internal transfer | PASS. One open incident, **no second page**, marker cleared |

**Sensitivity.** On the `f779b45` scratch copy the same file gives 16 failed / 4 passed. The Q4a cases fail on the old block-count window (line 70: E still counted on the old chain); Q4b and Q4c fail by the old keys and settings. This shows the Q4a assertions are not vacuous.

**Fail-open reorg plugin.** Attempt-3 Appendix C, extracted verbatim, makes each CP2c check return `[]` (or only its resolutions) in a reorg run. This is a non-source falsifier of README §6.2.
- `QA3_MODE=empty`: M8 **35**, M12 **201**, M13 **198** failed.
- `QA3_MODE=resolves_only`: M8 **17**, M12 **185**, M13 **197** failed.
- Without the plugin, all suites are green (V2x.1).

The lane suites therefore catch a check that fails open in a reorg run, including an empty `[]` result and a health-only result.

**Other contracts attacked or re-checked:**
- Checks never alert directly: they return findings, and alerts are routed through the shared incident/outbox engine. This holds in all scenarios.
- Required settings raise at construction: `P reconcile/tests -k 'config_example or required or raise_at_construction or settings_are_validated'` gives 7 passed, including `test_config_example_loads_and_builds_every_check` and `test_m12_r3_time_window_settings_are_validated`.
- D10 accepted gap: stated in `m13_retired_keys.py:32-35`, in the lane README (`workers/cp2c/plan.md:55`) and in `deploy/README.md` on `plan/monitoring-service` (line 64 ff.). The silence is documented by `test_m13_d10_accepted_gap_within_block_net_zero_is_not_detected`. **Accepted gap D10**, not a failure.
- M13 resolution wording: "Value LEFT retired key … — not an all-clear" (`m13_retired_keys.py:304`).

### 3.4 LIVE (public read-only; record mode; scratch DB; no transaction)

| Step | Endpoint, UTC | Command | Result |
|---|---|---|---|
| L1 M12 full backfill + incremental, `config.example.json` | `https://eth-mainnet.public.blastapi.io`, 08:57:48Z–08:59:17Z | `PYTHONPATH=reconcile reconcile/.venv/bin/python $S/live/live.py <ep> blast.sqlite m12 2` (Appendix B) | exit 0. Step 1, checkpoint 26,247,094, 59.3 s, `run:m12.errors` 0: **748 verified txs** (750 tx + 750 receipt lookups, 763 headers, 1 feeHistory), 0.125004 ETH over **604,800 s** = 0.017858 ETH/day; last 24 h 0.019965 ETH; oracle 0.613037 ETH, so runway 34.3 d trailing and 32.2 d at base fee 0.199 gwei + observed tip 1 gwei; liquidator 0.301395 ETH; **no alert**, which is correct. Step 2, checkpoint 26,247,100, 3.0 s: incremental (13 headers, 0 tx lookups), same 748 |
| L2 independent window cross-check | `https://eth.drpc.org` 08:59:36Z → HTTP 429 (NOT RUN there); `https://eth-pokt.nodies.app` 08:59:41Z | `python3 $S/live/xcheck.py <ep> blast.sqlite 26247100 26196861 26239918 748` (Appendix B) | Both bounds hold: ts(lo) ≤ T − 604,800 < ts(lo+1) and the same for 86,400. Block counting would start the week at 26,196,701 instead of 26,196,861 (**160 missed slots in 7 days**). **Nonce difference over the week = 748** = `window_txs`; over the day 113 = the DB's day rows |
| L3 fee-history completeness under the F2 rule | publicnode, nodies, blast, drpc, 09:00:11Z | `python3 $S/live/feehist.py …` | All 4: `oldestBlock` = cp − 299, 301 base fees, 300 ratios, so complete; identical mean 199,949,417 wei. The stricter check does not make public RPCs error |
| L4 null tx/receipt probe | publicnode, 08:59:51Z | `python3 $S/live/nullprobe.py <ep> blast.sqlite` | 0 null / 0 error in 3 × (40 tx + 40 receipts) (§2) |

## 4. Falsifiers

- **Implementer V2x.5 (27):** NOT RUN, per the owner scope note (no mutation testing; do not re-run the falsifier campaign).
- **Mine, no source mutation:** the fail-open reorg plugin (§3.3) reddens all three suites in both modes. The repaired code is "falsified" by the `f779b45` scratch copy: 6 Codex tests and 26 lane regressions are red there and green on the head.
- No file in the worktree was mutated at any point, so no restoration was needed. The scratch copies live under `$S` = `/private/tmp/claude-501/-Users-marco-orca-workspaces-ssv-network-monitor-ms-qa-cp2c-4-2/5a5268f8-5ff0-45cc-81c1-9ad48527df93/scratchpad`.

**Skipped for budget, and why:**
- No mutation runs: owner scope.
- No implementer sweep on new seeds: the 200 committed cases ran inside V2x.1.
- No LIVE M8/M13 and no anvil fork: M8/M13 code is unchanged since attempt 3's LIVE.
- The suite was run once (no code changed afterwards).

## 5. Evidence-plane writes

- `plans/monitoring-service/qa/cp2c-attempt-4.md` (this report).
- CP2c `**Status:**` line in `plans/monitoring-service/plan.md` → **VERIFIED — PASS WITH NOTES** on `dd9522b`.
- One appended event in `plans/monitoring-service/execution-log.md`.
- Local untracked `reconcile/.venv` (ignored).

Notes N1–N5 are for the coordinator to mirror into plan §8 "Recorded notes". That section is outside my write scope.

## 6. Repo proof

Before writing evidence: `git status --porcelain` was empty. After writing (before commit), `git status --porcelain` and `git diff --name-only dd9522b` list only:

```text
 M plans/monitoring-service/execution-log.md
 M plans/monitoring-service/plan.md
?? plans/monitoring-service/qa/cp2c-attempt-4.md
```

No implementation, config, template or test file appears in either list. `config/`, `config.zip` and `.env*` were not touched.

---

## Appendix A — own scenarios `$S/qa4/test_qa4.py`

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/reconcile:$PWD/reconcile/tests" reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider $S/qa4/test_qa4.py`

```python
"""CP2c QA attempt 4 — own targeted scenarios (Claude Opus 5.5). Focus: DEFECT-CLASSES 4 (time windows by chain
time, not block count) and 1 (degenerate/partial input read as clean or as positive proof), on every M12 persisted
key (m12_tx, m12_sync, unknown_since, incidents), plus M8/M13 spot checks. Scratch only; never committed as code."""

from __future__ import annotations

import random
from fractions import Fraction

import pytest
import test_m12 as t12
from fixtures_wallets import (
    CSSV,
    DAO_SAFE,
    LIQUIDATOR,
    ORACLE,
    PROXY,
    RETIRED_SLOT4,
    WalletChain,
    eth,
    gwei,
    stub_cssv,
    wallet_worker,
)
from harness import advance
from rpc import RpcError

WEEK_S, DAY_S = 604_800, 86_400


def brute_lo(ts, cp, seconds):
    """Independent: the last block whose timestamp is <= T - seconds (linear scan, no search)."""
    t = ts(cp) - seconds
    n = cp
    while n > 0 and ts(n) > t:
        n -= 1
    return n


def m(w):
    return w.checks["m12"].last[ORACLE]


# ------------------------------------------------------------------ Q4a: a depth-1 reorg that removes a missed-slot
# gap moves T (and the window) BACK without shortening the chain: by 30 slots (inside the prune margin) and by 300
# slots (beyond it: must backfill). E is excluded on the old chain, inside the window on the new one.


def _gap_reorg(tmp_path, gap_slots, checks=None):
    state = {"old": True}
    c = WalletChain(start_block=100_000)
    g = c.genesis_ts
    c.set_balance(ORACLE, eth(5))
    c.set_balance(LIQUIDATOR, eth(1))
    c.mine()
    edge = c.number
    c1 = edge + 50_380
    c.timestamp = lambda n: g + 12 * n + (12 * gap_slots if state["old"] and n >= c1 else 0)
    c.send(ORACLE, PROXY, gas_used=1_000_000_000, gas_price=gwei(1))  # 1 ETH at block E
    c.set_balance(ORACLE, eth(0.5))  # 0.5 ETH / (1 ETH / 7 d) = 3.5 d: page while E is in the window
    c.mine(10)
    c, w = wallet_worker(tmp_path, ["m12"], chain=c, checks={"m12": {"unknown_grace_s": 0, **(checks or {})}})
    advance(w)
    assert m(w)["window_txs"] == 1 and t12.open_incidents(w).get(f"{ORACLE}:runway") == "page"
    for _ in range(4):
        t12.tick(w, c, blocks=12_000)
    t12.tick(w, c, blocks=c1 - c.sealed)
    assert c.sealed == c1
    # old chain: T = ts(c1) shifted by the gap; E is older than 7 days
    assert m(w)["window_txs"] == 0, m(w)
    assert f"{ORACLE}:runway" not in t12.open_incidents(w)
    c.reorg(c1)  # depth 1: block c1 replaced by one proposed without the missed slots
    state["old"] = False
    assert c.sealed == c1 - 1
    c.mine()
    assert c.sealed == c1
    lo_new = brute_lo(c.timestamp, c1, WEEK_S)
    assert lo_new < edge  # E is inside the new chain's window
    return c, w, edge


@pytest.mark.parametrize("gap_slots", [30, 300])
def test_q4a_reorg_removing_a_missed_slot_gap_moves_the_window_back(tmp_path, gap_slots):
    c, w, edge = _gap_reorg(tmp_path, gap_slots)
    w.clock.advance(301)
    advance(w)
    assert w.db.meta("reorg:m12") is None
    week = t12.time_window(c, c.sealed, WEEK_S)
    assert set(week) == {h for h, x in c.wtxs.items() if x["block"] == edge}
    assert (m(w)["window_txs"], m(w)["window_fees_wei"]) == (1, eth(1)), m(w)
    assert t12.open_incidents(w).get(f"{ORACLE}:runway") == "page"


def test_q4a_beyond_margin_with_blockscout_down_raises_and_keeps_the_marker(tmp_path):
    c, w, edge = _gap_reorg(tmp_path, 300)
    real = w.bs
    w.checks["m12"].http_get = lambda url: (_ for _ in ()).throw(OSError("down"))
    w.clock.advance(301)
    advance(w)
    assert w.db.meta("reorg:m12") is not None
    assert f"{ORACLE}:runway" not in t12.open_incidents(w)  # not re-opened from guesses, not falsely cleared
    w.checks["m12"].http_get = real
    w.clock.advance(301)
    advance(w)
    assert w.db.meta("reorg:m12") is None and m(w)["window_txs"] == 1
    assert t12.open_incidents(w).get(f"{ORACLE}:runway") == "page"


# ------------------------------------------------------------------ Q4b: random timestamp profiles (independent
# brute-force oracle): irregular seconds (not 12 s multiples), outages of hours, a day and longer than 24 h, runs at
# random increments; both windows' bounds and contents equal the oracle at every run; final incidents equal a fresh
# worker's.


def _profile(rng, base, length, g):
    ts, t = [], g + 12 * base
    for _ in range(length):
        r = rng.random()
        t += (rng.randint(1, 30) if r < 0.85 else rng.randint(31, 3_600) if r < 0.995
              else rng.choice((86_399, 86_400, 86_401, 100_000)))
        ts.append(t)
    return lambda n: g + 12 * n if n < base else ts[n - base]


@pytest.mark.parametrize("seed", range(12))
def test_q4b_random_timestamps_windows_equal_the_oracle(tmp_path, seed):
    rng = random.Random(4_000 + seed)
    c = WalletChain(start_block=100_000)
    base = 100_000
    c.timestamp = _profile(rng, base, 140_000, c.genesis_ts)
    c.set_balance(ORACLE, eth(3))
    c.set_balance(LIQUIDATOR, eth(1))
    c.mine()
    for _ in range(rng.randint(5, 40)):
        c.send(ORACLE, PROXY, gas_used=rng.randint(21_000, 30_000_000), gas_price=gwei(rng.choice((0.5, 1, 3))))
        c.mine(rng.randint(1, 4_000))
    c.mine()
    c, w = wallet_worker(tmp_path / "a", ["m12"], chain=c, checks={"m12": {"unknown_grace_s": 0}})
    advance(w)
    for i in range(6):
        if i:
            if rng.random() < 0.5:
                c.send(ORACLE, PROXY, gas_used=rng.randint(21_000, 30_000_000), gas_price=gwei(1))
            t12.tick(w, c, blocks=rng.randint(1, 9_000))
        cp = c.sealed
        x = m(w)
        assert x.get("block") == cp and "spend" not in x, x
        for name, secs in (("window", WEEK_S), ("day", DAY_S)):
            assert x[f"{name}_from_block"] == brute_lo(c.timestamp, cp, secs) + 1, (name, x)
        week, day = t12.time_window(c, cp, WEEK_S), t12.time_window(c, cp, DAY_S)
        assert (x["window_txs"], x["window_fees_wei"], x["day_fees_wei"]) == (len(week), t12.fees(week),
                                                                              t12.fees(day))
        assert x["window_seconds"] == WEEK_S and x["day_seconds"] == DAY_S
        assert x["day_spend_wei"] == t12.fees(day)  # per day over exactly 86,400 s
        reads = w.db.meta("run:m12")["rpc_calls"].get("eth_getBlockByNumber", 0)
        assert reads <= x["window_txs"] + 2 + 120, reads
    c2, w2 = wallet_worker(tmp_path / "b", ["m12"], chain=c, checks={"m12": {"unknown_grace_s": 0}})
    advance(w2)
    assert t12.open_incidents(w) == t12.open_incidents(w2)


# ------------------------------------------------------------------ Q4c: a window/day setting changed between two
# processes (parameter changed mid-run): a longer window starts below the stored set -> backfill, never undercount.


def test_q4c_restart_with_a_longer_window_backfills(tmp_path):
    c = WalletChain(start_block=100_000)
    c.set_balance(ORACLE, eth(5))
    c.set_balance(LIQUIDATOR, eth(1))
    c.mine()
    old = c.number
    c.send(ORACLE, PROXY, gas_used=500_000_000, gas_price=gwei(1))  # 0.5 ETH, 7.5 days before the checkpoint
    c.mine(54_000)
    c.send(ORACLE, PROXY, gas_used=10_000_000, gas_price=gwei(1))
    c.mine()
    c, w = wallet_worker(tmp_path, ["m12"], chain=c)
    advance(w)
    assert m(w)["window_txs"] == 1
    clock, start, act = w.clock, w.cfg.start_block, w.cfg.activation_block
    w.db.conn.close()
    c, w = wallet_worker(tmp_path, ["m12"], chain=c, clock=clock, start_block=start, activation_block=act,
                         checks={"m12": {"window_s": 8 * DAY_S}})
    clock.advance(301)
    c.mine()
    advance(w)
    x = m(w)
    assert x["window_txs"] == 2 and x["window_fees_wei"] == eth(0.5) + eth(0.01), x
    assert x["window_from_block"] <= old


# ------------------------------------------------------------------ Q4d: degenerate input is never positive proof


def test_q4d_zero_tx_window_is_clean_only_with_a_proven_older_nonce(tmp_path):
    """The wallet's only transaction is 8 days old. Listed -> proven zero spend (runway resolved). A fresh process
    whose Blockscout page is empty (`items: []`) cannot prove it -> UNKNOWN, the open runway warn stays."""
    c = WalletChain(start_block=100_000)
    c.set_balance(ORACLE, eth(5))
    c.set_balance(LIQUIDATOR, eth(1))
    c.mine()
    h = c.send(ORACLE, PROXY, gas_used=500_000_000, gas_price=gwei(1))
    c.mine(8 * 7_200)
    c, w = wallet_worker(tmp_path / "a", ["m12"], chain=c)
    advance(w)
    assert m(w)["window_txs"] == 0 and m(w)["runway_trailing_days"] is None
    c2, w2 = wallet_worker(tmp_path / "b", ["m12"], chain=c, checks={"m12": {"unknown_grace_s": 0}})
    w2.bs.hide = {h}
    advance(w2)
    assert "UNKNOWN" in m(w2)["spend"] and t12.health(w2)


def test_q4d_null_and_wrong_shaped_reads_are_errors_not_clean(tmp_path):
    """eth_getTransactionCount null, a header without timestamp, a receipt gasUsed '0x': each is a check error
    (run rolled back, the open page untouched), never a clean resolution."""
    for fault in ("nonce_null", "ts_missing", "gasused_0x"):
        c, w, _ = t12.setup(tmp_path / fault, balance=eth(0.05), checks={"projection_tip": "none"})
        advance(w)
        before = t12.open_incidents(w)
        assert before.get(f"{ORACLE}:runway") == "page"
        w.db.conn.execute("DELETE FROM m12_sync")  # force a backfill so every read is exercised
        w.db.conn.commit()
        handle = c._handle
        if fault == "nonce_null":
            c._handle = lambda mm, p: None if mm == "eth_getTransactionCount" else handle(mm, p)
        elif fault == "ts_missing":
            c._handle = lambda mm, p: ({k: v for k, v in handle(mm, p).items() if k != "timestamp"}
                                       if mm == "eth_getBlockByNumber" and handle(mm, p) else handle(mm, p))
        else:
            rc = c._receipt
            c._receipt = lambda t: dict(rc(t), gasUsed="0x")
        errs = w.db.meta("run:m12")["errors"]
        try:
            t12.tick(w, c)
            outage = False
        except RpcError:  # the framework's RPC-outage path: the step aborts, retried
            outage = True
        assert outage or w.db.meta("run:m12")["errors"] > errs, fault
        print(fault, "rpc-outage" if outage else "check-error")
        assert t12.open_incidents(w) == before, fault
        assert [a for a in t12.alerts(w) if a[2] == "resolved"] == [], fault


# ------------------------------------------------------------------ Q4e: M8 / M13 spot checks (code unchanged since
# attempt 3; persisted keys re-attacked briefly)


def test_q4e_m8_first_run_baseline_then_safe_inflow_mid_day_and_restart(tmp_path):
    c = WalletChain()
    stub_cssv(c, supply=eth(1000), seed=eth(400))
    c.mine()
    c, w = wallet_worker(tmp_path, ["m8"], chain=c)
    advance(w)
    assert t12.open_incidents(w) == {}  # 1000 >= 800
    stub_cssv(c, supply=eth(1000), seed=eth(600))  # inflow: floor 1200 only at the next measure
    c.mine()
    w.clock.advance(3_600)
    advance(w)
    assert t12.open_incidents(w) == {}
    clock, start, act = w.clock, w.cfg.start_block, w.cfg.activation_block
    w.db.conn.close()
    c, w = wallet_worker(tmp_path, ["m8"], chain=c, clock=clock, start_block=start, activation_block=act)
    clock.advance(60)
    c.mine()
    advance(w)  # D3: measured at worker start -> 1200 > 1000 -> warn
    assert list(t12.open_incidents(w).values()) == ["warn"]


def test_q4e_m13_reorg_moves_topup_to_a_helper_transfer_in_the_same_block(tmp_path):
    c = WalletChain()
    c.mine()
    c, w = wallet_worker(tmp_path, ["m13"], chain=c)
    advance(w)
    c.send(PROXY, RETIRED_SLOT4, gas_used=21_000, value=eth(1))
    blk = c.number
    c.mine()
    advance(w)
    assert t12.open_incidents(w) == {f"{RETIRED_SLOT4}:{blk}": "page"}
    n_msgs = len(t12.alerts(w))
    c.reorg(blk)
    assert c.number == blk
    c.internal_transfer(RETIRED_SLOT4, eth(2))
    c.mine()
    advance(w)
    assert t12.open_incidents(w) == {f"{RETIRED_SLOT4}:{blk}": "page"}
    assert len([a for a in t12.alerts(w)[n_msgs:] if a[2] == "open"]) == 0  # same key: no duplicate page
    assert w.db.meta("reorg:m13") is None


__all__ = ["CSSV", "DAO_SAFE", "Fraction"]
```

Diagnostic (paths exercised; illustrative block-count comparison): `reconcile/.venv/bin/python $S/qa4/diag.py $PWD $S/qa4`

```python
import random, sys, tempfile, pathlib
sys.path[:0] = [sys.argv[1] + "/reconcile", sys.argv[1] + "/reconcile/tests", sys.argv[2]]
import test_qa4 as q, test_m12 as t12
from harness import advance
from fixtures_wallets import WalletChain, ORACLE, LIQUIDATOR, PROXY, eth, gwei, wallet_worker
for gap in (30, 300):
    d = pathlib.Path(tempfile.mkdtemp())
    c, w, edge = q._gap_reorg(d, gap)
    calls = len(w.bs.calls)
    hf = w.db.conn.execute("SELECT history_from FROM m12_sync").fetchone()[0]
    w.clock.advance(301); advance(w)
    print(f"gap {gap}: edge {edge} history_from(before) {hf} new lo {q.brute_lo(c.timestamp, c.sealed, q.WEEK_S)} "
          f"blockscout calls in reorg run {len(w.bs.calls)-calls} window_txs {q.m(w)['window_txs']} open {t12.open_incidents(w)}")
diff_runs = tx_diff = daily_tx = 0
for seed in range(12):
    rng = random.Random(4_000 + seed)
    c = WalletChain(start_block=100_000)
    c.timestamp = q._profile(rng, 100_000, 140_000, c.genesis_ts)
    for _ in range(rng.randint(5, 40)):
        c.send(ORACLE, PROXY, gas_used=21_000); c.mine(rng.randint(1, 4_000))
    for cp in range(c.sealed - 30_000, c.sealed + 1, 5_000):
        lo_t = q.brute_lo(c.timestamp, cp, q.WEEK_S); lo_b = cp - 50_400
        d_t = q.brute_lo(c.timestamp, cp, q.DAY_S); d_b = cp - 7_200
        diff_runs += (lo_t != lo_b) or (d_t != d_b)
        tw = {h for h, x in c.wtxs.items() if lo_t < x["block"] <= cp}; bw = {h for h, x in c.wtxs.items() if lo_b < x["block"] <= cp}
        td = {h for h, x in c.wtxs.items() if d_t < x["block"] <= cp}; bd = {h for h, x in c.wtxs.items() if d_b < x["block"] <= cp}
        tx_diff += (tw != bw) or (td != bd); daily_tx += bool(td)
print("random profiles: checkpoints sampled", 12*7, "bounds differ from block-count", diff_runs, "tx sets differ", tx_diff, "daily non-empty", daily_tx)
```

## Appendix B — LIVE scripts

`$S/live/live.py` (QA attempt 2 Appendix E with the scratch path changed and the m12 evidence printed):

```python
"""CP2c QA4 LIVE: config.example.json m12 (record mode, scratch DB), N steps; argv: endpoint, db-name, checks, steps.
Adapted from QA attempt 2 Appendix E (scratch path only)."""
import json, sys, time
from pathlib import Path
from eb_reconcile import Config, DEFAULTS, DB, Worker
from rpc import Rpc
root = Path.cwd(); scratch = Path(sys.argv[0]).resolve().parent
endpoint, dbname, only = sys.argv[1], sys.argv[2], sys.argv[3].split(',')
steps = int(sys.argv[4]) if len(sys.argv) > 4 else 2
rpc = Rpc(endpoint, timeout=20, retries=1, batch_size=20)
head = rpc.block_number()
raw = json.loads((root / 'reconcile/config.example.json').read_text())
db_path = scratch / dbname
fresh = not db_path.exists()
sb = head - 12
raw.update(start_block=sb, activation_block=sb, views=None, db_path=str(db_path), rpc_batch_size=20,
           rpc_retries=1, rpc_timeout_s=20)
for cid in list(raw['checks']):
    if cid not in only:
        raw['checks'][cid] = {'enabled': False}
raw['inventory'] = {'api_url': None}; raw['inbox'] = {}; raw['metrics'] = {'enabled': False}
raw['alerts'] = {'mode': 'record', 'record_path': str(scratch / 'alerts.jsonl'), 'digest_interval_s': 0}
w = Worker(Config({**DEFAULTS, **raw}), rpc, DB(raw['db_path']))
w.bootstrap()
for i in range(steps):
    t0 = time.time(); w.step(force=True)
    out = {'endpoint': endpoint, 'step': i + 1, 'head_at_start': head, 'fresh_db': fresh, 'checkpoint': w.checkpoint(),
           'seconds': round(time.time() - t0, 2),
           'run_meta': {r[0]: r[1] for r in w.db.conn.execute("SELECT key,value FROM meta WHERE key LIKE 'run:%'")},
           'rpc_failures': w.db.meta('rpc_failures'),
           'outbox': [dict(r) for r in w.db.conn.execute('SELECT key, severity, kind, delivery, substr(text,1,200) t FROM outbox')]}
    if 'm12' in w.checks: out['m12'] = w.checks['m12'].last
    print(json.dumps(out, default=str), flush=True)
    time.sleep(13)
```

`$S/live/xcheck.py`:

```python
"""Independent cross-check of the LIVE M12 windows on another endpoint: boundary header timestamps and the
nonce difference over each window (= the wallet's transaction count there)."""
import json, sqlite3, sys, urllib.request
ep, db, cp, wf, df, wtx = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5]), int(sys.argv[6])
O = "0x16a503c901615e6e21aa78cfcaff11822fc2b617"
def call(m, p):
    req = urllib.request.Request(ep, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": m, "params": p}).encode(),
                                 headers={"content-type": "application/json", "user-agent": "qa4"})
    r = json.load(urllib.request.urlopen(req, timeout=30))
    if "error" in r: raise RuntimeError(f"{m} {p}: {r['error']}")
    return r["result"]
ts = lambda b: int(call("eth_getBlockByNumber", [hex(b), False])["timestamp"], 16)
T = ts(cp)
print("T", T, "week bound ok:", ts(wf - 1) <= T - 604800 < ts(wf), "day bound ok:", ts(df - 1) <= T - 86400 < ts(df))
print("block-count would start week at", cp - 50400 + 1, "vs time", wf, "; day", cp - 7200 + 1, "vs", df)
n = lambda b: int(call("eth_getTransactionCount", [O, hex(b)]), 16)
print("nonce diff week", n(cp) - n(wf - 1), "check window_txs", wtx, "| nonce diff day", n(cp) - n(df - 1))
con = sqlite3.connect(db)
print("db day txs", con.execute("SELECT count(*) FROM m12_tx WHERE block >= ? AND block <= ?", (df, cp)).fetchone()[0])
```

`$S/live/feehist.py`:

```python
import json, sys, urllib.request
def call(ep, m, p):
    req = urllib.request.Request(ep, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": m, "params": p}).encode(),
                                 headers={"content-type": "application/json", "user-agent": "qa4"})
    return json.load(urllib.request.urlopen(req, timeout=30))
for ep in sys.argv[1:]:
    try:
        cp = int(call(ep, "eth_blockNumber", [])["result"], 16) - 20
        r = call(ep, "eth_feeHistory", [hex(300), hex(cp), []])["result"]
        ok = (int(r["oldestBlock"], 16) == cp - 299 and len(r["baseFeePerGas"]) == 301 and len(r["gasUsedRatio"]) == 300)
        print(ep, "cp", cp, "complete per the check's rule:", ok, "mean base fee", sum(int(x, 16) for x in r["baseFeePerGas"][:-1]) // 300)
    except Exception as e:
        print(ep, "ERROR", type(e).__name__, e)
```

`$S/live/nullprobe.py`:

```python
import json, sqlite3, sys, urllib.request, time
ep, db = sys.argv[1], sys.argv[2]
con = sqlite3.connect(db)
hs = [r[0] for r in con.execute("SELECT tx_hash FROM m12_tx ORDER BY block ASC LIMIT 20")] + \
     [r[0] for r in con.execute("SELECT tx_hash FROM m12_tx ORDER BY block DESC LIMIT 20")]
def batch(m):
    body = [{"jsonrpc": "2.0", "id": i, "method": m, "params": [h]} for i, h in enumerate(hs)]
    req = urllib.request.Request(ep, data=json.dumps(body).encode(), headers={"content-type": "application/json", "user-agent": "qa4"})
    r = json.load(urllib.request.urlopen(req, timeout=30))
    return sum(1 for x in r if x.get("result") is None), sum(1 for x in r if "error" in x), len(r)
for rnd in range(3):
    print(rnd + 1, "tx null/err/n", batch("eth_getTransactionByHash"), "receipt null/err/n", batch("eth_getTransactionReceipt"))
    time.sleep(3)
```
