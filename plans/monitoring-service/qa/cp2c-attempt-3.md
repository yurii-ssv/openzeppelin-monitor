PASS WITH NOTES

# CP2c independent QA — attempt 3 (after repair 2, round-capped)

Frozen head under test: `945fd4de3a0f8ad89fafe190c0b167869c82403e` (lane `mtabasco/ms-cp2c`, code `1752b33`, falsifiers `6ab3441`). QA branch `mtabasco/ms-qa-cp2c-4`. Task `task_d3cb50328678`, dispatch `ctx_5995635f687c`. QA executor: Claude Opus 5.5, effort high (D13). Measured 2026-10-22, 07:0xZ–07:4xZ UTC. This is an independent re-measurement. I made no implementation repair.

**Round policy (owner, attempt 3 is capped).** Every finding below is classified BLOCKING or NOTE, each with a one-line reason. A BLOCKING finding means FAIL. **No BLOCKING finding survived**, so the verdict is **PASS WITH NOTES**. The notes are N1–N5 in §1 and go to plan §8 "Recorded notes". Repair 2 is confirmed:
- all four attempt-2 reproducers (A1–A4) now exit 0 and all 21 C1 scenarios pass;
- the new regressions are red on `80e174b` (M8 1, M13 42) and green on the head;
- F5's test reddens under the canonical-hash mutation;
- the LIVE F2 reproducer recovers on publicnode.

## 1. Findings (ranked; none BLOCKING)

No BLOCKING finding. Each note has its reproducer in the appendices or in the named command.

### N1 — NOTE — M13 full coverage needs archive state; on a pruned RPC the fallback trades coverage for liveness (OG2)

The repair-2 fallback (QA attempt 2 F2) works LIVE (§3.4 L1). It engages after `state_fallback_after` (3) consecutive failed runs and reads only the newest `state_window_blocks` (64). It compares those blocks with the last balance it read. An increase that is still present pages, located in the unread range. Value that arrives and leaves inside the unread range is not seen. The engagement trigger is any `RpcError` on the full read when a narrower read succeeds; the trigger is not specific to "state pruned".

Reproducer: §3.4 L1 (publicnode, 300-block gap). Steps 1 and 2 raise `Archive requests require a personal token`. Step 3 sets the watch at the checkpoint and sends two `m13-state` health messages that name the unread range.

**NOTE, because** the loss is announced every time (`m13-state` health per address, and the page text says so) and an increase that sits on the key still pages. It is the documented degraded mode of a non-archive RPC. Owner: Marco (OG2). Recommendation: an archive RPC for production, which the lane README CP3 item 4 already states.

### N2 — NOTE — M8 floor follows the Safe's balance at each daily re-measure and at every restart (D3 semantics; spec amendment)

The rule is D3 ("2 × the Safe's cSSV measured at worker start, re-measured daily"). Two consequences follow:
- If the Safe's cSSV leaves, the floor drops to 0 at the next re-measure or restart, and an open `supply-floor` warn resolves (unless supply is 0).
- If cSSV arrives, the floor rises only at that point.

Reproducers:
- `test_q3_m8_safe_moves_cssv_out_mid_day_floor_is_the_start_measure_until_re_measured`: warn while the start floor applies, then resolved at the 24 h re-measure with seed 0.
- `test_q3_m8_safe_receives_cssv_mid_day_floor_rises_only_at_the_daily_re_measure`.

**NOTE, because** this is the owner's D3 decision implemented as written. The seed holder's `UnstakeRequested` page (spec M8 trigger 2) is a CP1A Monitor rule that covers the unstake itself. Owner: coordinator, for the spec M8 amendment at closure: say that the floor tracks the Safe's re-measured balance.

### N3 — NOTE — `fallback_tip_wei` is "dated" only in the docs, and it never changes a runway

`config.example.json` carries `"fallback_tip_wei": 1000000000` with no date marker (JSON has no comments). The date (2026-10-21) appears only in `fixtures_wallets.py` and in the worker record. As attempt 2 observed, the fallback applies only when the window has no transaction. In that case window gas is 0, the projection is 0 and `runway_base_fee_days` is `None`, so the fallback is evidence-only (`tip_source` flags it). **NOTE, because** it is documentation and has no alert effect. Owner: CP3 integrator, to add a dated `_comment` key or a README line.

### N4 — NOTE — M12 trailing and daily windows are block-count windows normalised by chain timestamps (DEFECT-CLASSES 4)

`window_blocks` (50,400) and `day_blocks` (7,200) select the transactions. The spend is then scaled by the actual timestamp span (`fees × 86,400 / span`), so missed slots do not inflate the rate, and the result is a per-day rate over the last 7,200 blocks rather than over the last wall-clock 24 h. Measured: the exact-boundary tests (§3.1) give exactly 0.04 ETH and 14 d with 12 s blocks. **NOTE, because** the rate stays correct under missed slots: it is a measurement-window choice, not a wrong threshold. Owner: coordinator (spec M12 wording "daily spend").

### N5 — NOTE — hygiene

`pytest.mark.m8/m12/m13` are unregistered, so every run prints `PytestUnknownMarkWarning`. A malformed `eth_getBalance` result takes two error paths: `'0x'` raises `ValueError` (`check-error`, rolled back) and `''` raises `RpcError` (the worker's RPC-failure path). Both are errors and never "no change" (`test_q3_m13_empty_string_balance_is_an_error_not_no_change`). **NOTE, because** it is cosmetic and both paths fail closed. Owner: CP3 integrator (register marks in `pytest.ini`/`conftest`, frozen to lanes).

### Observations (not findings)

- **Repeated pages per key in the reorg sweep.** My QA invariant "≤ 1 incident per M13 key" fails on 133/400 new seeds (1000–1199 × conf 0/2). All 133 are the allowed case that the implementer's own sweep checks on the same seeds (400/400 green): the first incident was resolved by a drain on a deleted block, and the value then sat on the canonical chain past that block. A second page is the conservative, truthful outcome there: the earlier "value left" message became false.
- **Top-up moved to another block by a reorg.** The result is "evidence left the canonical chain" for the old block plus a new page for the new block (`test_m13_reorg_moving_the_top_up_to_another_block`). The brief anticipates this: `<addr>:<block>` keys, "replaced by a different top-up". A top-up re-included at the same block keeps one incident and no second page (`test_q3_m13_reorg_of_internal_top_up_block_replaced_by_a_different_direct_top_up`, whose evidence follows the new chain's delta).

## 2. Claims I could NOT confirm

- **"Wide run (scratch, not committed): M13 seeds 100–399, M12 and M8 seeds 20–219 × 0/2: 1,400 passed."** I did not re-run those seeds. I ran the three implementer sweeps on 200 new seeds each (1000–1199 × conf 0/2): **1,200 passed** (M12 400, M8 400, M13 400).
- **Repair-2 statement "publicnode returns receipts null intermittently".** I did not re-measure it (budget). Attempt 2 measured it (L2), M12 is unchanged since then (`git diff --stat 80e174b HEAD -- reconcile/checks/m12_wallet_burn.py` is empty), and the lane README CP3 item 4 now names receipts.
- **LIVE M12/M8 and the anvil-fork top-up.** NOT RUN by me. M12 code is unchanged since attempt 2's LIVE L1 (blast, 738 verified transactions, runway 18.98 d trailing and 11.8 d base-fee+tip giving a warn). M8 changed only in its per-process token, which is covered GATED. The M13 top-up path (`report`, `attribute` apart from the `except` widening) is unchanged since attempt 2's anvil L5.

I reproduced everything else in the repair-2 handoff myself (§3, §4).

## 3. Verification matrix

**Preflight.**
- `git rev-parse HEAD` = `945fd4de3a0f8ad89fafe190c0b167869c82403e`, equal to the dispatch. `git status --porcelain` was empty at start. 346 GiB free.
- `uv venv reconcile/.venv --python 3.12` and `uv pip install --python reconcile/.venv/bin/python -r reconcile/requirements-dev.txt` exited 0 (CPython 3.12.15, pytest 9.1.1, ruff 0.17.1; authorized, untracked).
- `__pycache__` was removed, and `PYTHONDONTWRITEBYTECODE=1` was set for every run.
- Orca: `/Applications/Orca.app/Contents/Resources/bin/orca`.

**Frozen CP1B files.**
- `git diff --stat plan/monitoring-service HEAD -- reconcile/tests/harness.py reconcile/tests/test_framework.py reconcile/tests/test_worker.py` printed nothing (rc 0).
- `bd531b4` and `500abe4` are ancestors of both HEAD and `plan/monitoring-service` (`git merge-base --is-ancestor` → 0 for each). The lane carries that patch through the integration merge and does not hand-edit it.
- `git diff --name-only $(git merge-base HEAD plan/monitoring-service) HEAD -- reconcile` lists only these:
  - `checks/__init__.py`: one line in the CP2c block;
  - `checks/m8_supply_floor.py`, `m12_wallet_burn.py`, `m13_retired_keys.py`;
  - `config.example.json`: `checks.m12/m8/m13` appended;
  - `tests/fixtures_wallets.py`, `test_m8.py`, `test_m12.py`, `test_m13.py`.

  No other frozen CP1B file was edited.

`P` = `$S/p.sh` = `PYTHONDONTWRITEBYTECODE=1 reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider <args>`, which prints pytest's own exit code. Each row was run on its own.

| V-ID | Command | Exit; result | Outcome |
|---|---|---|---|
| V2x.1 M12 | `P reconcile/tests/test_m12.py` | 0; 111 passed | PASS (trigger map §3.1) |
| V2x.1 M8 | `P reconcile/tests/test_m8.py` | 0; 65 passed | PASS |
| V2x.1 M13 | `P reconcile/tests/test_m13.py` | 0; 270 passed | PASS |
| V2x.2 | `P reconcile/tests/test_m<N>.py -k thresholds_from_chain` (12, 8, 13) | 0/0/0; 2/109, 1/64, 1/269 passed/deselected | PASS; plus my config-override row (§3.1) |
| V2x.3 | `P … -k restart` (12, 8, 13) | 0/0/0; 2/109, 2/63, 7/263 | PASS; plus my crash-before-delivery rows |
| V2x.4 | not owned by CP2c | NOT RUN (not owned) | — |
| V2x.5 | `PYTHONDONTWRITEBYTECODE=1 python3 plans/monitoring-service/workers/cp2c/falsify.py "$PWD"` | 0; **21/21**, each line "before exit 0 / mutated exit 1 / restored exit 0 / porcelain=''" (the original 8: F12a–d, F8, F13a–c; sweep counts F12e-sweep 5/40, F12g-sweep 37/40, F8b-sweep 13/40, F13d-sweep 81/200, F13g-sweep 24/200) | PASS (each line inspected; the script does not assert) |
| V2x.6 suite | `P reconcile/tests` | 0; **686 passed** in 17.5 s | PASS |
| V2x.6 lint | `reconcile/.venv/bin/ruff check reconcile` | 0; "All checks passed!" | PASS |
| V2c.7 | `P reconcile/tests/test_m12.py -k "runway or projection or tip"` | 0; 16 passed / 95 deselected | PASS + §3.2 |
| V2c.8 | `P reconcile/tests/test_m13.py` | 0; 270 passed | PASS; also covers the README §6.2 row on shorter chains and resolved keys (§3.3) |
| Config example | `P reconcile/tests/test_framework.py -k config_example` | 0; 1 passed | PASS; plus a production-config probe (§3.5) |
| Repair-2 RED/GREEN | scratch `git archive 80e174b reconcile`, head `test_m12/m8/m13.py` + `fixtures_wallets.py` copied in; the sweep's `m13_orphan` count line replaced by `pass` (no such table on `80e174b`) | M12 111 passed (F5 test green on both heads; it is red only under F12h); M8 **1 failed** (`test_qa2_m8_restart_whose_first_run_fails_after_the_seed_read_still_measures_at_start`); M13 **42 failed** / 228 (18 named regressions + 24 sweep cases). Before the stub: 218 failed, all extra failures being `no such table: m13_orphan` | CONFIRMED as claimed |
| Attempt-2 A1–A4 | `PYTHONPATH=reconcile:reconcile/tests python $S/qa2/qa2_F1a.py` (`F1b`, `F3`, `F4`), verbatim from attempt 2 | exit 0, 0, 0, 0. F1a: no new message after the re-inclusion. F1b: one page in total, no resolution. F3: seed re-measured `2000…000` and a warn. F4: one page, no health | F1, F3, F4 **FIXED** |
| Attempt-2 C1 | `pytest $S/qa2/test_qa2.py` (verbatim) | 0; 21 passed | PASS |
| Attempt-2 F5 | falsifier F12h (= attempt-2 Q12a) in V2x.5 | before 0 / mutated 1 / restored 0 | **FIXED** (test now fails without the check) |
| Attempt-2 F2 LIVE | §3.4 L1 | recovers at run 3 | **FIXED** |
| Wide sweeps (new seeds) | `pytest $S/qa2/test_sweeps_wide3.py` (attempt-2 C2 with `SEEDS = range(1000, 1200)`) | implementer invariants: M12 400/400, M8 400/400, **M13 400/400** passed. QA "≤ 1 incident per key": 133/400 (§1 observations) | PASS |
| QA3 scenarios | `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=reconcile:reconcile/tests reconcile/.venv/bin/python -m pytest -q $S/test_qa3.py` (Appendix A) | 0; **29 passed** | PASS |
| Fail-open reorg results | `QA3_MODE=empty\|resolves_only pytest -p failopen_qa3 reconcile/tests/test_<N>.py test_qa3.py -k <N>` (Appendix C) | empty: M8 33, M12 44, M13 198 failed. Resolves-only: M8 15, M12 42, M13 197 failed. Without the plugin all green | PASS (the suites catch a check that returns `[]` or only resolutions in a reorg run) |

### 3.1 Spec trigger list (derived from spec §3 M8, M12, M13, §4, D3, D4, D10)

Out of this lane:
- M8 `FeesSynced(0)` and the seed `UnstakeRequested` are Monitor rules (CP1A).
- The `ZeroCSSVSupply` revert belongs to M9.
- The M13 `commitRoot`/`updateClusterBalance` attempts and the row-3 attacker interactions are Monitor rules (CP1A). Row-3 internal calls are the D10 gap.

| Trigger (spec) | Severity | Alert test | Silent twin / exact boundary | Result |
|---|---|---|---|---|
| M12 oracle balance < 0.15 ETH | page | `test_m12_oracle_balance_below_015_pages`; mine `test_q3_m12_oracle_balance_exactly_015[0.15-1 wei]` | `…_above_015_is_silent`; exactly 0.15 silent (mine) | PASS |
| M12 runway < 14 d | warn | `…_runway_below_14_days_warns`; mine `test_q3_m12_runway_exactly_14_days[-1]` | `…_runway_of_20_days_is_silent`; exactly 14.000000 d silent (mine) | PASS |
| M12 runway < 7 d | page | `…_runway_below_7_days_pages` | `…_exactly_7_days_warns_not_pages` (F12a red under `<=`) | PASS |
| M12 daily spend > 0.04 ETH | warn | `…_daily_spend_above_004_warns`; mine `test_q3_m12_daily_spend_exactly_004[1]` (+1 gas unit) | `…_below_004_is_silent`; exactly 0.04 silent (mine) | PASS |
| M12 liquidator < 0.2 ETH | warn | `…_liquidator_below_02_warns`; mine at 0.2 − 1 wei | `…_above_02_is_silent`; exactly 0.2 silent (mine) | PASS |
| M12 liquidator < 0.05 ETH | page | `…_liquidator_below_005_pages`; mine at 0.05 − 1 wei | `…_at_006_warns_only`; exactly 0.05 warns only (mine) | PASS |
| M12 runway from trailing 7-day spend AND from gas × current base fee | — | `…_from_trailing_spend_and_from_current_base_fee`, `…_trailing_spend_drives_when_the_base_fee_fell` | §3.2 | PASS |
| M8 `totalSupply()` < 2 × Safe cSSV (start, daily) | warn | `test_m8_supply_below_twice_the_seed_warns`; mine: transfer in/out, restart mid-day, crash before delivery, reorg of the drop | `…_at_the_floor_is_silent` (my Q8b `<=` red) | PASS; N2 |
| M13 any incoming value to 0x3A98… / 0x4BC6… | page | `…_direct_top_up_of_either…[both]`, `…_internal_transfer_from_a_helper_contract_pages`, `…_without_any_log…`; mine `test_q3_m13_both_keys_same_block_then_drain_one` (direct + internal in one block) | `…_zero_delta_blocks_stay_silent`, `…_outgoing_value_stays_silent`, `…_first_observation_is_a_silent_baseline` | PASS |
| M13 "value left" wording | resolution | `test_q3_m13_both_keys_same_block_then_drain_one`: text contains "LEFT" and "not an all-clear"; the other key stays open | — | PASS |
| §4 "confirm alerts ≥ 2 deep, except M13 and M12 pages" | — | checks run at the worker checkpoint (12 deep, documented latency, lane README) | — | integration item (unchanged) |

**Thresholds from config (class 11).** `test_q3_m12_every_threshold_is_config_overridable[6]` uses the spec-status scenario, which is silent with the D4 defaults. Moving each of `oracle_balance_page_wei`, `oracle_runway_warn_days`, `oracle_runway_page_days`, `oracle_daily_spend_warn_wei`, `liquidator_balance_warn_wei` and `liquidator_balance_page_wei` in config alone makes exactly that key alert at its severity. Falsifier Q12a (hard-coded 0.15) reddens it.

**Severity routing and cadence.** My crash-before-delivery rows assert `delivery`: M13 page → `telegram`, M12 pages → `telegram`, M8 warn → `telegram` (D2). The M13 6 h re-page is covered by `test_m13_open_while_the_value_sits_then_re_paged_every_6h`, the M12 6 h re-alert by `test_m12_restart_does_not_duplicate_…`. These pass in V2x.1.

### 3.2 Runway decision (event-0040)

M12 code is unchanged since attempt 2 (`git diff --stat 80e174b HEAD -- reconcile/checks/m12_wallet_burn.py` is empty), so attempt 2's part-by-part verification still applies. I re-checked it against the source and the green suite:
- the default is `projection_tip: "observed"`;
- the alarm uses `min(trailing, base-fee + tip)` (F12d `max` reddens);
- the evidence carries both runways and their inputs (`test_qa2_m12_runway_evidence_carries_both_runways_and_inputs`, green);
- the tip is clamped `≥ 0`, so `"none"` can only be quieter (`test_qa2_m12_none_tip_is_never_louder_than_observed`, `test_m12_observed_tip_can_only_make_the_check_louder`, green);
- an empty tip sample falls back to `fallback_tip_wei`, required and positive (§3.5), and is flagged in `tip_source` (`test_m12_empty_tip_sample_uses_the_configured_fallback_never_zero`). The fallback is evidence-only (N3).

### 3.3 Defect classes attacked on every persisted key (my scenarios, Appendix A)

| Key / state | Attack | Result |
|---|---|---|
| M13 `m13_topup` / `m13_orphan` / incidents | reorg of the block carrying an **internal** top-up, replaced by a **different direct** top-up at the same block: one incident, no second page, evidence = the new delta; Q13c (settle with the stored row) reddens it | PASS |
| same | **shorter** replacement chain (reorg run checkpoint below the top-up), **crash** right after reorg detection, restart, re-inclusion at the same block: one page in total, never an evidence-left resolution; Q13a (pending key not re-reported, the "empty reorg result" of that key) reddens it | PASS |
| M13 same-block ordering | direct top-up of one key and internal top-up of the other in one block, then a drain of one: two pages, one value-left resolution, the other open | PASS |
| M13 `eth_getBalance` empty | `'0x'` and `''` results: no advance, no page, no "no change"; the page goes out once the RPC answers | PASS (N5) |
| M13 / M8 / M12 outbox | crash after the committed run, before delivery; restart: exactly one message per key, `telegram`, one incident | PASS |
| M8 `meta floor` | Safe cSSV out and in mid-day; restart mid-day (re-measured, new process token, same incident, no new message); reorg (depth 2, beyond 0 confirmations) of the block carrying the supply drop → resolved, no re-warn | PASS (N2) |
| M12 `m12_tx` / `m12_sync` | reorg depth 1 of the block carrying a 0.06 ETH tx (daily-spend warn) not re-included, another tx on the new chain: warn resolved, the new tx counted once, marker cleared; Q12c (rows kept) reddens it | PASS |
| M12 Blockscout empty page | open runway warn, the wallet sends a tx, Blockscout answers `{"items": []}`: spend UNKNOWN, the warn is neither resolved nor re-sent, a balance drop below 0.15 still pages, recovery once the listing is back; Q12b (nonce gap ignored) reddens it | PASS |
| Required settings | §3.5 | PASS |

Repair-2 claims that the attempt-2 classes (2, 10, 1, 12) are closed: the attempt-2 reproducers hold, the 200-case committed sweep plus my 400 new M13 cases pass, and the fail-open plugin reddens all three suites.

### 3.4 LIVE (public read-only; record mode; scratch DB)

| Step | Endpoint | Command | Result |
|---|---|---|---|
| L1 M13 restart gap on a pruned node (attempt-2 F2 reproducer) | `https://ethereum-rpc.publicnode.com`, 2026-10-22T07:27:51Z–07:28:59Z | attempt-2 Appendix B procedure with my scratch `live.py` (Appendix D): baseline 1 step (checkpoint 26,246,647), `UPDATE m13_watch SET block=block-300`, then 4 steps | Steps 1–2: `RpcError: eth_getBalance: Archive requests require a personal token`, watch kept at 26,246,347. Step 3 (checkpoint 26,246,650): watch at the checkpoint for both keys, two `m13-state` health messages "M13 could not read the balance of … 0x3a98…" / "… 0x4bc6…". Step 4: normal, watch at 26,246,651, no new health. No page (no increase). **F2 FIXED LIVE** |

Budget: no M12 backfill, no anvil fork and no drpc/nodies. The reasons are in §2: M12 is unchanged, and the M13 top-up path is unchanged since attempt 2's L5. The 686-test suite was run once; no code changed afterwards.

### 3.5 Constructor-validated settings and config example

I probed `checks.registry.build` on `reconcile/config.example.json`:
- the example builds `m5, m8, m12, m13`;
- removing any of `m12.oracle_wallet`, `m12.liquidator_wallet`, `m12.fallback_tip_wei` (with the default `observed`), `m8.cssv`, `m8.seed_holder` or `m13.addresses`, or a whole `checks.m12/m8/m13` key, raises `SystemExit` naming the setting;
- `m13.addresses = {}` raises `SystemExit`;
- `projection_tip: "none"` without `fallback_tip_wei` builds, as intended.

The example's values match spec §3/§5: oracle `0x16A5…b617`, liquidator `0x0431…95E0`, cSSV `0xe018…0546`, DAO Safe `0xb350…E6b6`, retired keys `0x3A98…3bB4` and `0x4BC6…01aF`.

### 3.6 Accepted gap D10 (reported, not failures)

These are confirmed in code (the `m13_retired_keys.py` docstring "ACCEPTED GAP (D10…)"), in the lane README (workers/cp2c/plan.md) and in `deploy/README.md` on `plan/monitoring-service` (§"Row by row": the M13 row 1/2 column "a transfer that nets to zero within a block", and row 3 internal/constructor interactions):
- within-block net-zero transfers (`test_m13_d10_accepted_gap_within_block_net_zero_is_not_detected`, `test_qa2_m13_same_block_top_up_and_spend`);
- transfers between sampled blocks beyond the cap (`test_qa2_m13_long_gap_top_up_then_drain_inside_the_gap_is_the_sampled_d10_gap`);
- attacker/consolidation internal calls and constructor interactions.

All are **accepted gap D10**. I report them, not as failures. N1 is the related degraded mode on a non-archive RPC.

## 4. Falsifiers

**Matrix (implementer, V2x.5).** 21/21 were red then green, clean after each (§3 row V2x.5). That includes the original 8 (F12a–d, F8, F13a–c).

**Mine** (`python3 $S/falsify_qa3.py "$PWD"`, Appendix B). One mutation at a time, restored with `git checkout -- <file>`, `git status --porcelain` printed after each. Final line: `all red under mutation, restored green and clean: True`.

| ID | Mutation | Named test | before / mutated / restored |
|---|---|---|---|
| Q13a | a pending orphan (reorg run below its block) is not re-reported (`return False, []`) | `test_q3_m13_reorg_shorter_chain_then_stall_crash_restart_keeps_open` | 0 / 1 / 0; porcelain '' |
| Q13b | a "told left" orphan re-found without its drain is never re-paged | `test_m13_reorg_reincluding_a_resolved_top_up_without_its_drain_pages_again_once` | 0 / 1 / 0; '' |
| Q13c | a re-found orphan is reported from the stored (old-chain) row, not the new chain's row | `test_q3_m13_reorg_of_internal_top_up_block_replaced_by_a_different_direct_top_up` | 0 / 1 / 0; '' |
| Q8a | drop the daily re-measure clause | `test_q3_m8_safe_receives_cssv_mid_day_floor_rises_only_at_the_daily_re_measure` | 0 / 1 / 0; '' |
| Q8b | floor `<` → `<=` | `test_m8_supply_at_the_floor_is_silent` | 0 / 1 / 0; '' |
| Q12a | oracle page threshold hard-coded to 0.15 ETH | `test_q3_m12_every_threshold_is_config_overridable` (1 of 6 red) | 0 / 1 / 0; '' |
| Q12b | nonce gap ignored (`missing = []`) | `test_q3_m12_empty_blockscout_page_with_open_runway_warn_keeps_it_and_balance_still_pages` | 0 / 1 / 0; '' |
| Q12c | reorg keeps `m12_tx` rows above the ancestor | `test_q3_m12_reorg_depth_1_of_the_block_carrying_a_spend_tx_recounts` | 0 / 1 / 0; '' |

Cross-check: the implementer's `test_m13.py` also catches Q13a (13 failed) and Q13c (5 failed); restored with `git checkout`, porcelain clean. The fail-open plugin (Appendix C) is a non-source falsifier; its results are in §3.

## 5. Evidence-plane writes

- CP2c **Status:** `IMPLEMENTED — AWAITING QA (repair 2 …)` → `VERIFIED — PASS WITH NOTES` on head `945fd4d`, naming this report.
- I wrote only `plans/monitoring-service/qa/cp2c-attempt-3.md`, the CP2c **Status:** line in `plans/monitoring-service/plan.md`, and one appended event in `plans/monitoring-service/execution-log.md`. Outside those: the authorized ignored `reconcile/.venv`, plus scratch outside the repo under `/private/tmp/claude-501/-Users-marco-orca-workspaces-ssv-network-monitor-ms-qa-cp2c-4/929800ca-729f-42ce-b9cb-2ee8e9385661/scratchpad/q3` (`$S`: test file, falsifier runner, plugin, frozen-code copy, attempt-2 scripts, LIVE DB).
- No `config/`, `config.zip`, `.env*`, implementation, test, fixture or other-lane file was touched. No mainnet transaction, no real alert secret; record mode only.

## 6. Repo proof

After all mutation experiments and before the evidence-plane writes, `git status --porcelain` was empty. The output immediately before the commit is appended below.

```text
$ git status --porcelain
M  plans/monitoring-service/execution-log.md
M  plans/monitoring-service/plan.md
A  plans/monitoring-service/qa/cp2c-attempt-3.md

$ git diff --cached --name-only 945fd4de3a0f8ad89fafe190c0b167869c82403e
plans/monitoring-service/execution-log.md
plans/monitoring-service/plan.md
plans/monitoring-service/qa/cp2c-attempt-3.md

$ git diff --cached --exit-code 945fd4de3a0f8ad89fafe190c0b167869c82403e -- reconcile deploy docs src; echo $?
0
```

**No implementation file appears in either output**; only the three authorized evidence-plane paths (the report itself appears as added).

## Appendices (scratch sources; `$S` = the scratch directory in §5)

Run from the worktree root with `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=reconcile:reconcile/tests`. Attempt-2 scripts are the verbatim blocks of `qa/cp2c-attempt-2.md` Appendices A1–A4, C1, C2 and E. They were saved under `$S/qa2/` by name, with only the scratch path in `live.py` changed and `SEEDS = range(1000, 1200)` in `test_sweeps_wide3.py`.

### A QA3 scenarios — `$S/test_qa3.py`

```python
"""CP2c QA attempt 3 — independent scenarios (scratch, not committed).

Expected outcomes come from the spec (M8, M12, M13), D3/D4/D10 and checks/README.md §6.2, not from the code.
Run from the worktree root:
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=reconcile:reconcile/tests reconcile/.venv/bin/python -m pytest -q <this file>
"""

from __future__ import annotations

import json

import pytest
import test_m12 as t12
import test_m13 as t13
import test_m8 as t8
from fixtures_wallets import (
    LIQUIDATOR,
    ORACLE,
    RETIRED_LIQUIDATOR,
    RETIRED_SLOT4,
    eth,
    stub_cssv,
    wallet_worker,
)
from harness import advance, outbox

from rpc import RpcError

S4, RL = RETIRED_SLOT4, RETIRED_LIQUIDATOR


def crash_after_run(w):
    """Everything up to and including the checks' run (committed), then the process dies before delivery."""
    w.check_reorg()
    w.scan()
    w.poll_inbox()
    w.run_checks()


def restart(tmp_path, w, chain, only, checks=None):
    _, w2 = wallet_worker(tmp_path, only, chain=chain, checks=checks, start_block=w.cfg.start_block,
                          activation_block=w.cfg.activation_block, clock=w.clock)
    return w2


# ============================================================================================================ M13


def test_q3_m13_both_keys_same_block_then_drain_one(tmp_path):
    """Class 3: a direct top-up of one key and an internal top-up of the other in ONE block are two pages; a
    drain of one in the next block resolves only that key (value-left wording), the other stays open."""
    chain, w = t13.setup(tmp_path)
    t13.step(w, chain, blocks=2)
    b = chain.number
    t13.top_up(chain, S4, eth(0.3))
    chain.internal_transfer(RL, eth(0.2))
    t13.step(w, chain)
    assert sorted(t13.sent(w)) == sorted([("page", "open", f"{S4}:{b}"), ("page", "open", f"{RL}:{b}")])
    t13.drain(chain, S4, eth(0.3))
    t13.step(w, chain)
    assert t13.sent(w)[-1] == ("info", "resolved", f"{S4}:{b}") or t13.sent(w)[-1][1:] == ("resolved", f"{S4}:{b}")
    assert t13.open_keys(w) == [f"{RL}:{b}"]
    (res,) = outbox(w, "kind='resolved'")
    assert "not an all-clear" in res["text"] and "LEFT" in res["text"]


def test_q3_m13_reorg_of_internal_top_up_block_replaced_by_a_different_direct_top_up(tmp_path):
    """§6.2 on `<addr>:<block>`: the block carrying an internal top-up is reorged (depth 1) and the new chain has a
    DIFFERENT top-up (direct, other value) at the same block: the value is at risk at that key on the canonical
    chain, the incident stays open, no second page, no evidence-left resolution."""
    chain, w = t13.setup(tmp_path)
    t13.step(w, chain, blocks=2)
    b = chain.number
    chain.internal_transfer(S4, eth(0.4))
    t13.step(w, chain)
    assert t13.sent(w) == [("page", "open", f"{S4}:{b}")]
    chain.reorg(b)
    t13.top_up(chain, S4, eth(0.1))
    t13.step(w, chain, blocks=2)
    assert t13.sent(w) == [("page", "open", f"{S4}:{b}")] and t13.open_keys(w) == [f"{S4}:{b}"]
    assert w.db.meta("reorg:m13") is None
    ev = json.loads(w.db.conn.execute("SELECT evidence FROM incidents WHERE key=?", (f"{S4}:{b}",)).fetchone()[0])
    assert ev["delta_wei"] == str(eth(0.1))  # evidence follows the canonical chain


def test_q3_m13_crash_between_detection_and_delivery_sends_one_page(tmp_path):
    """Class 10: the run that detects the top-up commits, the process dies before delivery; the restarted
    process delivers exactly one page (telegram) and never a second incident."""
    chain, w = t13.setup(tmp_path)
    t13.step(w, chain, blocks=2)
    b = chain.number
    t13.top_up(chain)
    chain.mine()
    crash_after_run(w)
    w2 = restart(tmp_path, w, chain, ["m13"])
    advance(w2)
    t13.step(w2, chain, blocks=3)
    rows = outbox(w2, "kind NOT IN ('health','digest')")
    assert [(r["severity"], r["kind"], r["delivery"]) for r in rows] == [("page", "open", "telegram")]
    assert w2.db.conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 1 and t13.open_keys(w2) == [
        f"{S4}:{b}"]


def test_q3_m13_empty_string_balance_is_an_error_not_no_change(tmp_path):
    """Class 1: a provider answering '0x' / '' for eth_getBalance is an error (raise, retried), never zero and
    never 'no change'; once it answers, the top-up still pages."""
    chain, w = t13.setup(tmp_path)
    t13.step(w, chain, blocks=2)
    orig, bad = chain._handle, ["0x"]
    chain._handle = lambda m, p: (bad[0] if m == "eth_getBalance" and bad else orig(m, p))
    t13.top_up(chain)
    for v in ("0x", ""):
        bad[:] = [v]
        try:  # '0x' -> ValueError (check-error, rolled back); '' -> RpcError (worker RPC failure path)
            t13.step(w, chain)
        except RpcError:
            pass
        assert t13.sent(w) == []
        assert w.db.conn.execute("SELECT block FROM m13_watch").fetchall()[0][0] < chain.sealed  # not advanced
    bad.clear()
    t13.step(w, chain)
    assert [k for _, k, _ in t13.sent(w)] == ["open"]


def test_q3_m13_reorg_shorter_chain_then_stall_crash_restart_keeps_open(tmp_path):
    """§6.2 + class 10: top-up paged, reorg deeper than confirmations onto a SHORTER chain (checkpoint below the
    top-up block), crash right after the reorg detection, restart, the chain then re-includes the top-up at the
    same block: one page in total, never an evidence-left resolution, incident open at the end."""
    chain, w = t13.setup(tmp_path, confirmations=2)
    t13.step(w, chain, blocks=3)
    b = chain.number
    t13.top_up(chain)
    t13.step(w, chain, blocks=6)
    chain.reorg(b - 1)
    chain.mine()
    w.check_reorg()
    w2 = restart(tmp_path, w, chain, ["m13"])
    advance(w2)
    assert w2.checkpoint()["block"] < b and chain.number == b  # the pending block is b again
    t13.top_up(chain)
    t13.step(w2, chain, blocks=5)
    assert [k for _, k, _ in t13.sent(w2)] == ["open"] and t13.open_keys(w2) == [f"{S4}:{b}"]
    assert outbox(w2, "kind='resolved'") == []


# ============================================================================================================ M8

SEED = t8.SEED


def test_q3_m8_safe_moves_cssv_out_mid_day_floor_is_the_start_measure_until_re_measured(tmp_path):
    """D3: floor = 2 × Safe cSSV measured at start, re-measured daily. The Safe's cSSV leaves mid-day (supply
    drops below the start floor): warn now; the daily re-measure then sees seed 0 → floor 0 → resolved unless
    supply is 0 (D3 semantics; the seed's UnstakeRequested page is the CP1A Monitor rule)."""
    chain, w = t8.setup(tmp_path, supply=3 * SEED)
    advance(w)
    assert t8.alerts(w) == []
    stub_cssv(chain, 3 * SEED // 2, 0)
    t8.tick(w, chain, seconds=3600)
    assert t8.alerts(w) == [("warn", "open")]
    assert t8.state(w)["seed"] == str(SEED)
    t8.tick(w, chain, seconds=86_400)
    assert t8.state(w)["seed"] == "0"
    assert [k for _, k in t8.alerts(w)] == ["open", "resolved"]


def test_q3_m8_safe_receives_cssv_mid_day_floor_rises_only_at_the_daily_re_measure(tmp_path):
    chain, w = t8.setup(tmp_path, supply=3 * SEED)
    advance(w)
    stub_cssv(chain, 3 * SEED, 2 * SEED)
    t8.tick(w, chain, seconds=3600)
    assert t8.alerts(w) == []  # floor still 2 × the start measure
    t8.tick(w, chain, seconds=86_400)
    assert t8.state(w)["floor"] == str(4 * SEED) and t8.alerts(w) == [("warn", "open")]


def test_q3_m8_restart_mid_day_re_measures_and_does_not_duplicate(tmp_path):
    """Restart mid-day with an open warn: re-measured at start (D3), same incident, no second message."""
    chain, w = t8.setup(tmp_path, supply=2 * SEED - 1)
    advance(w)
    assert t8.alerts(w) == [("warn", "open")]
    m1 = t8.state(w)
    chain.mine()
    w2 = restart(tmp_path, w, chain, ["m8"])
    w2.clock.advance(3600)
    advance(w2)
    m2 = t8.state(w2)
    assert m2["process"] != m1["process"] and m2["block"] > m1["block"]
    assert t8.alerts(w2) == [("warn", "open")]


def test_q3_m8_crash_between_detection_and_delivery_sends_one_warn(tmp_path):
    chain, w = t8.setup(tmp_path, supply=SEED)
    crash_after_run(w)
    chain.mine()
    w2 = restart(tmp_path, w, chain, ["m8"])
    w2.clock.advance(301)
    advance(w2)
    rows = outbox(w2, "kind NOT IN ('health','digest')")
    assert [(r["severity"], r["kind"], r["delivery"]) for r in rows] == [("warn", "open", "telegram")]


def test_q3_m8_reorg_of_the_block_carrying_a_supply_drop(tmp_path):
    """§6.2: the supply drop that opened the warn is reorged away (depth 2, beyond confirmations 0): the run with
    reason reorg re-evaluates the key → resolved; nothing re-warns."""
    chain, w = t8.setup(tmp_path, supply=3 * SEED)
    advance(w)
    t8.tick(w, chain, seconds=301)
    fork = chain.number
    stub_cssv(chain, SEED, SEED)
    t8.tick(w, chain, blocks=2, seconds=301)
    assert t8.alerts(w) == [("warn", "open")]
    chain.reorg(fork)
    t8.tick(w, chain, blocks=2, seconds=301)
    assert [k for _, k in t8.alerts(w)] == ["open", "resolved"] and w.db.meta("reorg:m8") is None
    t8.tick(w, chain, blocks=2, seconds=86_400)
    assert [k for _, k in t8.alerts(w)] == ["open", "resolved"]


# ============================================================================================================ M12


@pytest.mark.parametrize("cfg, expected", [
    ({"oracle_balance_page_wei": eth(0.4)}, ("page", f"{ORACLE}:balance")),
    ({"oracle_runway_warn_days": 25}, ("warn", f"{ORACLE}:runway")),
    ({"oracle_runway_warn_days": 30, "oracle_runway_page_days": 25}, ("page", f"{ORACLE}:runway")),
    ({"oracle_daily_spend_warn_wei": eth(0.01)}, ("warn", f"{ORACLE}:daily-spend")),
    ({"liquidator_balance_warn_wei": eth(2)}, ("warn", f"{LIQUIDATOR}:balance")),
    ({"liquidator_balance_page_wei": eth(2)}, ("page", f"{LIQUIDATOR}:balance")),
], ids=["oracle_page", "runway_warn", "runway_page", "daily_spend", "liq_warn", "liq_page"])
def test_q3_m12_every_threshold_is_config_overridable(tmp_path, cfg, expected):
    """Class 11: the spec-status scenario is silent with the D4 defaults; each threshold moved in config alone
    makes exactly its key alert at its severity (no code change)."""
    chain, w, _ = t12.setup(tmp_path / "default")
    advance(w)
    assert t12.alerts(w) == []
    chain, w, _ = t12.setup(tmp_path / "over", checks=cfg)
    advance(w)
    assert t12.alerts(w) == [(*expected, "open")]


def test_q3_m12_crash_between_detection_and_delivery_one_page(tmp_path):
    chain, w, _ = t12.setup(tmp_path, balance=eth(0.1))
    crash_after_run(w)
    chain.mine()
    w2 = restart(tmp_path, w, chain, ["m12"])
    w2.clock.advance(301)
    advance(w2)
    rows = outbox(w2, "kind NOT IN ('health','digest')")
    assert sorted((r["severity"], r["kind"], r["delivery"]) for r in rows) == [("page", "open", "telegram")] * 2


def test_q3_m12_empty_blockscout_page_with_open_runway_warn_keeps_it_and_balance_still_pages(tmp_path):
    """Class 1: runway warn open; the wallet sends a tx; Blockscout then answers an EMPTY page: spend UNKNOWN,
    the warn is neither resolved nor re-sent early, and a balance drop below 0.15 still pages."""
    chain, w, _ = t12.setup(tmp_path, balance=eth(0.17825))
    advance(w)
    assert t12.alerts(w) == [("warn", f"{ORACLE}:runway", "open")]
    orig = w.bs.__call__
    w.checks["m12"].http_get = lambda url: {"items": [], "next_page_params": None}
    chain.send(ORACLE, t12.PROXY, gas_used=150_000)
    chain.set_balance(ORACLE, eth(0.149))
    t12.tick(w, chain)
    assert t12.alerts(w) == [("warn", f"{ORACLE}:runway", "open"), ("page", f"{ORACLE}:balance", "open")]
    assert t12.open_incidents(w)[f"{ORACLE}:runway"] == "warn"
    assert "UNKNOWN" in w.checks["m12"].last[ORACLE]["spend"]
    w.checks["m12"].http_get = orig
    t12.tick(w, chain)
    assert "UNKNOWN" not in str(w.checks["m12"].last[ORACLE].get("spend", ""))


def test_q3_m12_reorg_depth_1_of_the_block_carrying_a_spend_tx_recounts(tmp_path):
    """§6.2: a big-gas tx (daily spend > 0.04 → warn) is reorged away and NOT re-included; the reorg run re-measures
    on the new chain and resolves the warn; the new chain's own tx is counted once."""
    chain, w, _ = t12.setup(tmp_path, balance=eth(10))
    advance(w)
    assert t12.alerts(w) == []
    chain.mine(10)
    fork = chain.number
    chain.send(ORACLE, t12.PROXY, gas_used=30_000_000, gas_price=10**9 * 2)  # 0.06 ETH in one tx
    t12.tick(w, chain)
    assert t12.alerts(w) == [("warn", f"{ORACLE}:daily-spend", "open")]
    chain.reorg(fork)
    chain.send(ORACLE, t12.PROXY, gas_used=150_000)
    t12.tick(w, chain, blocks=2)
    assert [k for _, _, k in t12.alerts(w)] == ["open", "resolved"]
    n = w.db.conn.execute("SELECT COUNT(*) FROM m12_tx WHERE block >= ?", (fork,)).fetchone()[0]
    assert n == 1 and w.db.meta("reorg:m12") is None


# ---------------------------------------------------------------- M12 exact D4 boundaries (`<` / `>` per spec text)


@pytest.mark.parametrize("bal, expected", [(eth(0.15), []), (eth(0.15) - 1, [("page", f"{ORACLE}:balance", "open")])])
def test_q3_m12_oracle_balance_exactly_015(tmp_path, bal, expected):
    chain, w, _ = t12.setup(tmp_path, balance=bal, gas_per_day=1_000)
    advance(w)
    assert t12.alerts(w) == expected


@pytest.mark.parametrize("bal, expected", [
    (eth(0.2), []), (eth(0.2) - 1, [("warn", f"{LIQUIDATOR}:balance", "open")]),
    (eth(0.05), [("warn", f"{LIQUIDATOR}:balance", "open")]), (eth(0.05) - 1, [("page", f"{LIQUIDATOR}:balance", "open")])])
def test_q3_m12_liquidator_exact_boundaries(tmp_path, bal, expected):
    chain, w, _ = t12.setup(tmp_path, liquidator=bal)
    advance(w)
    assert t12.alerts(w) == expected


@pytest.mark.parametrize("extra, expected", [(0, []), (1, [("warn", f"{ORACLE}:daily-spend", "open")])])
def test_q3_m12_daily_spend_exactly_004(tmp_path, extra, expected):
    """Zero-fee history, then one tx whose fee is exactly 0.04 ETH (+extra wei) in the last 24 h (7,200 blocks of
    12 s = 86,400 s): exactly 0.04 is silent (`>`), one wei more warns."""
    chain, w, _ = t12.setup(tmp_path, balance=eth(10), gas_per_day=0)
    advance(w)
    chain.mine(5)
    chain.send(ORACLE, t12.PROXY, gas_used=40_000_000 + extra, gas_price=10**9)
    t12.tick(w, chain)
    assert t12.alerts(w) == ([] if not extra else expected)


@pytest.mark.parametrize("delta, expected", [(0, []), (-1, [("warn", f"{ORACLE}:runway", "open")])])
def test_q3_m12_runway_exactly_14_days(tmp_path, delta, expected):
    """Balance = 14 × the trailing daily average and the base-fee projection equal (txs at the base fee, no tip):
    exactly 14 d is silent (`<`), one wei less warns."""
    fees = 14 * (15_500_000 // 2) * t12.gwei(1.15)  # the window's fees = 7 × the daily average
    chain, w, _ = t12.setup(tmp_path, balance=2 * fees + delta)
    advance(w)
    ev = t12.evidence(w, f"{ORACLE}:runway")
    assert float(ev["runway_trailing_days"]) == pytest.approx(14, abs=1e-6)
    assert t12.alerts(w) == expected
```

### B own falsifier runner — `$S/falsify_qa3.py`

```python
"""QA3 own falsifiers: one mutation at a time; run the named tests before / mutated / restored; git checkout; porcelain."""
import os, subprocess, sys
ROOT, S = sys.argv[1], os.path.dirname(os.path.abspath(__file__))
Q = f"{S}/test_qa3.py"
MUT = [
 ("Q13a-pending-not-reported", "reconcile/checks/m13_retired_keys.py",
  "            return False, self.report((a, b, p, before, after, ev, None, None), block, new=False)",
  "            return False, []",
  [f"{Q}::test_q3_m13_reorg_shorter_chain_then_stall_crash_restart_keeps_open"]),
 ("Q13b-told-left-never-repaged", "reconcile/checks/m13_retired_keys.py",
  "        if cur is not None and cur[6] is None:  # told",
  "        if False:  # told",
  ["reconcile/tests/test_m13.py::test_m13_reorg_reincluding_a_resolved_top_up_without_its_drain_pages_again_once"]),
 ("Q13c-same-key-evidence-frozen", "reconcile/checks/m13_retired_keys.py",
  "                return True, self.report(cur, block, new=False)\n            if block >= b:",
  "                return True, self.report((a, b, p, before, after, ev, None, None), block, new=False)\n            if block >= b:",
  [f"{Q}::test_q3_m13_reorg_of_internal_top_up_block_replaced_by_a_different_direct_top_up"]),
 ("Q8a-no-daily-remeasure", "reconcile/checks/m8_supply_floor.py",
  ' or ctx.clock() - st["at"] >= self.config["remeasure_s"]', "",
  [f"{Q}::test_q3_m8_safe_receives_cssv_mid_day_floor_rises_only_at_the_daily_re_measure"]),
 ("Q8b-floor-boundary", "reconcile/checks/m8_supply_floor.py", "if supply < floor or supply == 0:",
  "if supply <= floor or supply == 0:", ["reconcile/tests/test_m8.py::test_m8_supply_at_the_floor_is_silent"]),
 ("Q12a-hardcoded-oracle-page", "reconcile/checks/m12_wallet_burn.py",
  'c["oracle_balance_page_wei"], c["oracle_runway_warn_days"]', '150_000_000_000_000_000, c["oracle_runway_warn_days"]',
  [f"{Q}::test_q3_m12_every_threshold_is_config_overridable"]),
 ("Q12b-nonce-gap-ignored", "reconcile/checks/m12_wallet_burn.py",
  "        missing = sorted(set(range(start, n_now)) - set(window))", "        missing = []",
  [f"{Q}::test_q3_m12_empty_blockscout_page_with_open_runway_warn_keeps_it_and_balance_still_pages"]),
 ("Q12c-reorg-rows-kept", "reconcile/checks/m12_wallet_burn.py",
  '        conn.execute("DELETE FROM m12_tx WHERE block > ?", (ancestor,))\n', "        pass\n",
  [f"{Q}::test_q3_m12_reorg_depth_1_of_the_block_carrying_a_spend_tx_recounts"]),
]
py = f"{ROOT}/reconcile/.venv/bin/python"
env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=f"{ROOT}/reconcile:{ROOT}/reconcile/tests")
def run(tests):
    r = subprocess.run([py, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:logging", "-W", "ignore", *tests],
                       cwd=ROOT, capture_output=True, text=True, env=env)
    return r.returncode, r.stdout.strip().splitlines()[-1]
ok = True
for name, path, old, new, tests in MUT:
    full = f"{ROOT}/{path}"; src = open(full).read()
    assert src.count(old) == 1, (name, old)
    g = run(tests); open(full, "w").write(src.replace(old, new)); r = run(tests)
    subprocess.run(["git", "checkout", "--", path], cwd=ROOT, check=True)
    a = run(tests)
    st = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout
    ok &= g[0] == 0 and r[0] != 0 and a[0] == 0 and st == ""
    print(f"{name}: before {g} | mutated {r} | restored {a} | porcelain={st!r}")
print("all red under mutation, restored green and clean:", ok)
```

### C fail-open reorg plugin — `$S/failopen_qa3.py`

```python
"""pytest plugin: in runs with reason reorg, the CP2c checks return [] (QA3_MODE=empty) or only their resolve=True
findings (QA3_MODE=resolves_only): a check that fails open in a reorg run. Every M8/M12/M13 suite must redden."""
import os
from checks.m8_supply_floor import M8SupplyFloor
from checks.m12_wallet_burn import M12WalletBurn
from checks.m13_retired_keys import M13RetiredKeys

MODE = os.environ.get("QA3_MODE", "empty")
for cls in (M8SupplyFloor, M12WalletBurn, M13RetiredKeys):
    orig = cls.run
    def run(self, ctx, block, _orig=orig):
        out = _orig(self, ctx, block)
        if ctx.reorg_ancestor is None:
            return out
        return [] if MODE == "empty" else [f for f in out if f.resolve]
    cls.run = run
```

### D LIVE runner — `$S/live/live.py`

Attempt-2 Appendix E verbatim except `scratch = Path('$S/live')`. L1 commands:

```sh
export PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/reconcile"; U=https://ethereum-rpc.publicnode.com; rm -f $S/live/pn.sqlite
out=$(reconcile/.venv/bin/python -u $S/live/live.py $U pn.sqlite m13 1 2>/dev/null); SB=$(print -r -- "$out" | jq -r '.checkpoint.block')
reconcile/.venv/bin/python -c "import sqlite3; c=sqlite3.connect('$S/live/pn.sqlite'); c.execute('UPDATE m13_watch SET block=block-300'); c.commit()"
reconcile/.venv/bin/python -u $S/live/live.py $U pn.sqlite m13 4 $SB 2>/dev/null | jq -c '{step, cp: .checkpoint.block, rpc_failures, m13_last_read: [.m13[].block], health: [.outbox[]|select(.kind=="health")|.t[0:150]]}'
```
