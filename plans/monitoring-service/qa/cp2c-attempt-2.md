FAIL

# CP2c independent QA — attempt 2 (after repair 1)

Frozen head under test: `c6b0a63dc590aad22910525583da0389687c2424` (lane `mtabasco/ms-cp2c`, code `56a26a9`); QA branch `mtabasco/ms-qa-cp2c-3`. Task `task_642f4a7fb280`, dispatch `ctx_a1b733b25f20`. QA executor: Claude Opus 5.5, effort high (D13). Measured 2026-10-21, 15:2xZ–15:55Z UTC. Independent re-measurement; no implementation repair; the dead Codex attempt-2 session's scratch was not used.

Round policy (owner, 2026-10-21): this is the lane's second QA round, so every finding below is **BLOCKING** (correctness/safety or not). Verdict: **FAIL** on F1–F5. Repair 1 itself is confirmed: attempt 1's F1 is fixed (both of its reproducers now hold), the new regressions are red on `326d7ff` and green on the head, the three 40-case sweeps pass, 14/14 implementer falsifiers redden and restore.

## 1. Findings (ranked; every one has a reproducer in the appendices)

### F1 — MEDIUM — M13 reorg handling re-pages, and closes as "evidence left the canonical chain", top-ups that survive on the final chain (DEFECT-CLASSES 2 and 10)

Two root causes, one effect (duplicate page plus a false resolution message for canonical evidence):

- **(a) A resolved top-up re-included above the ancestor is paged again.** Top-up at T and its drain at R, both above the reorg ancestor A, were paged and resolved. A reorg re-includes the same transfers at the same blocks (the usual reorg outcome; the real tx hash would even be identical). `rewind()` deletes the `m13_topup` row (block > A); the reorg run re-reads (A, C], finds the "new" top-up with `new=True`, `report()` emits a page finding for a key whose incident is closed, so the framework opens a second incident and pages, then resolves it again. Canonical history has one top-up and one drain; the operator gets two pages and two value-left resolutions.
  Command: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/reconcile:$PWD/reconcile/tests" reconcile/.venv/bin/python /tmp/cp2c-qa2/repro/qa2_F1a.py` (Appendix A1) → **exit 1**. Expected: no new message after the reorg. Actual new messages: `('page', 'open', '0x3a98…3bb4:1004'), ('info', 'resolved', '0x3a98…3bb4:1004')` (ancestor 1003, marker cleared).
- **(b) A key whose block is above the reorg run's checkpoint is resolved without proof.** With a shorter replacement chain the reorg run's checkpoint C1 is below the top-up block T. M13 cannot evaluate `addr:T` at C1, yet it neither re-reports the key nor raises (README §6.2 item 4), so the framework resolves it "Evidence left the canonical chain". When the checkpoint passes T, the re-included top-up opens a new incident and pages again.
  Command: same prefix, `/tmp/cp2c-qa2/repro/qa2_F1b.py` (Appendix A2) → **exit 1**. Reorg-run checkpoint 1002, top-up block 1004. Messages: `page open :1004` → (reorg run) `info resolved :1004` "Evidence left the canonical chain" → (chain regrows) `page open :1004`. Expected: the key stays open, one page in total.
- **The implementer's own sweep invariant catches (b) on new seeds.** `test_m13_adversarial_reorg_sweep` run unchanged on 200 new seeds × confirmations 0/2 (Appendix C, `test_wide_m13`): **14 failed / 386 passed**, every failure `AssertionError: evidence-left resolution of a canonical top-up <addr>:<block>` (ids `20-0 21-0 30-2 42-2 49-2 71-2 85-2 89-2 92-0 152-0 192-2 194-2 197-2 203-0`). The committed seeds 0–19 happen to miss it. The sweep never asserts "at most one page per canonical top-up", so it cannot see (a).
- Final open-incident state is correct in both cases (no missed page). The impact is duplicate pages and a false "evidence left" claim after a reorg deeper than the confirmation window. That window is rare on mainnet, but this is the defect class the lane was required to close.

### F2 — MEDIUM — M13 stalls permanently on a non-archive RPC after a restart gap deeper than the node's state window (LIVE)

M13 reads `eth_getBalance` at every block of (last read, checkpoint]. Above the 256 cap it reads a sample, but always from `last + 1`. A pruned node cannot serve those blocks, so every run raises `RpcError`. `m13_watch` never advances, the gap only grows, and the check never recovers. The operator gets the generic `rpc` health message at 3, 20 and 100 consecutive failures, and then nothing more. Measured on publicnode (Appendix B, `qa2_F2.sh`; endpoint `https://ethereum-rpc.publicnode.com`, 15:52Z): it serves historical `eth_getBalance` only about 100 blocks deep. Depth 100 returned a result; depths 127/128/200/1,000/10,000/100,000 returned `-32602 "Archive requests require a personal token"`. That is about 20 minutes, and the worker's checkpoint is already 12 blocks deep. After a simulated 300-block gap, steps 1–3 all failed with that error, `m13_watch` stayed at 26,241,679, and the `rpc` health message came at failure 3. Control: the same procedure on the archive endpoint `https://eth-mainnet.public.blastapi.io` recovers on the first step (256 of 301 blocks sampled, `m13-gap` health per address, watch at the checkpoint). Neither the code nor the lane README nor its CP3 list states that M13 requires archive state, has a start-up probe for it, or has a bounded fallback (read from the oldest servable block and report the unread range). The lane README's CP3 item 4 covers only M12 transaction lookups. Production RPC is OG2 (owner). The coordinator should decide whether this is fixed in code (fallback plus health) or as a hard deployment precondition with a probe. Either way it is unhandled at this head.

### F3 — LOW — M8 uses the previous process's floor after a restart whose first run fails partway (D3 "measured at worker start")

`M8SupplyFloor.floor()` sets `self.measured = True` in memory before the run commits. If `totalSupply()` then fails, the run rolls back the new `ctx.set_state("floor")`, but the flag stays True. Every later run of this process then uses the previous process's DB floor until `remeasure_s` passes (up to 24 h). Command: `/tmp/cp2c-qa2/repro/qa2_F3.py` (Appendix A3) → **exit 1**. While the worker was down the Safe's cSSV doubled, so the floor is 4 × SEED and supply is 3 × SEED. Expected: re-measured seed 2 × SEED and a warn. Actual: seed `1000000000000000000000` (process 1's, block 1000) and no alert. Impact is a missed or false warn for at most 24 h after a transient RPC error at start. The staleness bound is no worse than normal daily operation, but the start-measure rule is violated.

### F4 — LOW — M13 best-effort attribution can block the page

The module promises that attribution is "best effort: a failed lookup is noted, never blocks the page". However, `attribute()` catches only `RpcError, KeyError, TypeError, ValueError`. If `eth_getBlockReceipts` returns a list with a null entry, `rc.get("logs")` raises `AttributeError`, so the whole run raises (`check-error` health). The same input recurs, so the page never goes out and the whole M13 watch is held. Command: `/tmp/cp2c-qa2/repro/qa2_F4.py` (Appendix A4) → **exit 1**. After 3 steps the incident messages are `[]` and the only health message is `Check m13 raised AttributeError: 'NoneType' object has no attribute 'get'`. Expected: one page. Likelihood is low (malformed provider output), but the impact is a missed page.

### F5 — LOW — M12's canonical-block-hash verification has no test that fails without it (DEFECT-CLASSES 12)

The brief requires untrusted Blockscout candidates to be verified on chain, including the canonical block hash. The repair audit table marks "header hash must be canonical (tested)". Mutation Q12a (`if hdr["hash"].lower() == t["block_hash"]:` → `if True:`) survives every M12 test: 102 implementer tests plus attempt 1's 27 independent ones, **129 passed mutated** (Appendix D). The check itself works: my test `test_qa2_m12_transaction_from_a_non_canonical_block_is_not_counted` makes the tx and its receipt carry a non-canonical block hash, which the code correctly turns into UNKNOWN. It passes on the head, fails under Q12a and passes when restored, and the tree was clean afterwards. A regression test is missing, and the "tested" claim is not confirmed.

## 2. Claims I could NOT confirm

- **"No resolve without positive proof … for M13 (sweep asserts)"**: holds only on the committed 40 seeds; 14/400 new seeds fail the sweep's own assertion (F1b). The audit-table row "M13 `m13_topup`, incidents … top-up > A deleted … resolution > A undone (tested)" is correct as code but does not cover re-inclusion of a resolved pair (F1a).
- **"header hash must be canonical (tested)"** (audit table, M12 RPC row): not tested (F5).
- **M13 "bounded batched reads with a defined behaviour beyond the cap / long restart gap"**: holds on the fake chain and on an archive RPC (LIVE control), not on a pruned RPC (F2).
- **"publicnode gives null txs"**: refined, not confirmed as stated. 60 hashes from the 7-day window on publicnode returned 0/60 null `eth_getTransactionByHash`. `eth_getTransactionReceipt` was **intermittently** null: 20/60 on the first batch, then 0/60 and 10/60 on two further batches. The itemized batch's nulls were the oldest nonces, 279–288, at blocks 26,191,814–26,191,824. M12 turns a null receipt into "not verified", hence a nonce gap, hence UNKNOWN, never clean, so the safety property holds. The CP3 item should name receipts, not only transactions.
- D3/D10/other rulings: not re-litigated. The **accepted gaps D10** are confirmed present in code (M13 docstring) and in the lane README (workers/cp2c/plan.md): within-block net-zero transfers, transfers between sampled blocks beyond the cap, and attacker/consolidation internal calls and constructor interactions. They are reported here as **accepted gap D10, not failures** (§3.3). `deploy/README.md` does not exist on the lane or on `plan/monitoring-service`, so the D10 note there is a CP1A/integration item I could not check.

Everything else claimed in the repair-1 handoff I reproduced myself (§3, §4).

## 3. Verification matrix

**Preflight.** HEAD = `c6b0a63dc590aad22910525583da0389687c2424` (equals the dispatch); `git status --porcelain` empty at start; 348 GiB free; `uv venv reconcile/.venv --python 3.12` and `uv pip install --python reconcile/.venv/bin/python -r reconcile/requirements-dev.txt` exit 0 (CPython 3.12.15, pytest 9.1.1, ruff 0.17.1; authorized, untracked); `anvil 1.8.5`; `__pycache__` removed and `PYTHONDONTWRITEBYTECODE=1` for every run. Orca CLI from `/Applications/Orca.app/Contents/Resources/bin/orca`.

**Frozen CP1B files.** `git diff plan/monitoring-service -- reconcile/tests/harness.py reconcile/tests/test_framework.py reconcile/tests/test_worker.py` printed nothing. `git merge-base --is-ancestor bd531b4 HEAD` → 0 and `… 500abe4 HEAD` → 0. The lane's edit of those three files since the wave-2 base equals exactly the coordinator patch: `diff <(git diff 96d1c32 HEAD -- <3 files>) <(git diff 96d1c32 500abe4 -- <3 files>)` → identical. `git diff --name-status $(git merge-base HEAD plan/monitoring-service) HEAD -- reconcile deploy docs scripts src` lists only `checks/__init__.py` (M), `checks/m8_supply_floor.py`, `m12_wallet_burn.py`, `m13_retired_keys.py` (A), `config.example.json` (M), `tests/fixtures_wallets.py`, `test_m8.py`, `test_m12.py`, `test_m13.py` (A). No other frozen CP1B file was edited.

`P` = `/tmp/cp2c-qa2/p.sh` = `PYTHONDONTWRITEBYTECODE=1 reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider <args>`, printing pytest's own exit code (no `&&` chain; each row run separately).

| V-ID | Command | Exit; result | Outcome |
|---|---|---|---|
| V2x.1 M12 | `P reconcile/tests/test_m12.py` | 0; 102 passed | supplied scenarios PASS; trigger coverage §3.1 PASS; F5 coverage gap |
| V2x.1 M8 | `P reconcile/tests/test_m8.py` | 0; 62 passed | PASS (F3 not covered) |
| V2x.1 M13 | `P reconcile/tests/test_m13.py` | 0; 79 passed | supplied PASS; F1, F4 not covered |
| V2x.2 | `P reconcile/tests/test_m12.py -k thresholds_from_chain`; same for `test_m8.py`, `test_m13.py` | 0 / 0 / 0; 2/100, 1/61, 1/78 passed/deselected | PASS (base-fee change, receipts, daily Safe re-measure, config addresses move the alert) |
| V2x.3 | `P … -k restart` (each file) | 0 / 0 / 0; 2/100, 1/61, 4/75 | PASS for the named rows; F3 is a restart defect outside them |
| V2x.4 | not owned by CP2c | NOT RUN (not owned) | — |
| V2x.5 | `PYTHONDONTWRITEBYTECODE=1 python3 plans/monitoring-service/workers/cp2c/falsify.py "$PWD"` | 0; **14/14**: before exit 0, mutated exit 1, restored exit 0, `porcelain=''` after each (sweep mutations: F12e-sweep 5 failed, F12g-sweep 37, F8b-sweep 13, F13d-sweep 18) | PASS (the script does not assert; each line inspected) |
| V2x.6 suite | `P reconcile/tests` | 0; **483 passed** in 16.07 s | PASS |
| V2x.6 lint | `reconcile/.venv/bin/ruff check reconcile` | 0; "All checks passed!" | PASS |
| V2c.7 | `P reconcile/tests/test_m12.py -k "runway or projection or tip"` | 0; 14 passed / 88 deselected | PASS + §3.2 |
| V2c.8 | `P reconcile/tests/test_m13.py` | 0; 79 passed | supplied rows PASS; **FAIL on the README §6.2 row** ("a reorg that removes the top-up resolves/never re-pages"): F1 (re-page after re-inclusion, premature evidence-left) |
| Repair-1 RED/GREEN | scratch copy `git archive 326d7ff reconcile` in `/tmp/cp2c-qa2/frozen`, head test files copied in, `pytest` each file | M12 **12 failed** / 90 passed (the 4 F1/restart tests, 3 short-batch cases, sweep 5/40: `[2-2] [6-0] [6-2] [13-2] [14-0]`); M8 62 passed; M13 **1 failed** (short batch) / 78 | CONFIRMED as claimed |
| Attempt-1 Appendix A | `PYTHONPATH=reconcile:reconcile/tests python /tmp/cp2c-qa2/a1/repro_a.py` (verbatim from attempt 1) | 0; `marker= 164804`, runway incident kept, run raised RuntimeError (logged) | attempt-1 F1 **FIXED** |
| Attempt-1 Appendix B | `pytest /tmp/cp2c-qa2/a1/test_independent.py` (verbatim) | 1; 26 passed, 1 failed: `test_m12_reorg_unknown_without_open_spend_must_retry` at its precondition `'UNKNOWN' in last[ORACLE]['spend']` (KeyError: the repair raises before recording UNKNOWN). Same file with only that precondition line replaced by `pass` (`test_independent_v2.py`, diff printed): **27 passed**, the marker assertion holds | **FIXED** (obsolete precondition, not a regression) |
| Attempt-1 fail-open plugin | `pytest -p failopen_plugin test_independent_v2.py -k shorter` (empty `[]` and health-only reorg returns) | mutated 3 failed (both modes); without the plugin 3 passed | PASS |
| QA2 adversarial | `PYTHONPATH=reconcile:reconcile/tests pytest -q /tmp/cp2c-qa2/test_qa2.py` (Appendix C) | 1; **3 failed / 18 passed**: failures = F1a, F4, F3 | FAIL (F1, F3, F4) |
| QA2 wide sweeps | `pytest -q /tmp/cp2c-qa2/test_sweeps_wide.py` (200 new seeds × conf 0/2 per sweep) | 1; M12 400/400 passed, M8 400/400 passed, **M13 14 failed / 386** (F1b); QA invariant "≤ 1 page per key" 165/400 cases with a repeated key (includes legitimate re-pages after an orphaned drain; not used as evidence) | FAIL (F1) |

### 3.1 Spec trigger list (derived from spec §3 M8, M12, M13, §4, D3, D4, D10; not from the implementer's list)

Out of this lane: M8 `FeesSynced(0)` / seed `UnstakeRequested` (Monitor, CP1A); `ZeroCSSVSupply` revert (M9); M13 `commitRoot`/`updateClusterBalance` attempts and attacker interactions (Monitor, CP1A; row 3 internal calls = D10 gap).

| Trigger (spec) | Severity | Alert test | Silent twin / boundary | Result |
|---|---|---|---|---|
| M12 oracle balance < 0.15 ETH | page | `test_m12_oracle_balance_below_015_pages` | `…_above_015_is_silent`; exactly 0.15 silent (attempt-1 `test_balance_exact_boundaries`, re-run green) | PASS |
| M12 runway < 14 d | warn | `…_runway_below_14_days_warns` | `…_runway_of_20_days_is_silent`; exactly 14 resolves (`test_runway_exact_14_and_daily_exact_004`, green) | PASS |
| M12 runway < 7 d | page | `…_runway_below_7_days_pages` | `…_exactly_7_days_warns_not_pages` (F12a red under `<=`) | PASS |
| M12 daily spend > 0.04 ETH | warn | `…_daily_spend_above_004_warns` | `…_below_004_is_silent`; exactly 0.04 silent (Q12b `>=` red) | PASS |
| M12 liquidator < 0.2 ETH | warn | `…_liquidator_below_02_warns` | `…_above_02_is_silent`; exactly 0.2 silent | PASS |
| M12 liquidator < 0.05 ETH | page | `…_liquidator_below_005_pages` | `…_at_006_warns_only`; exactly 0.05 warns only | PASS |
| M12 runway from trailing 7-day spend AND from gas × current base fee | — | `…_from_trailing_spend_and_from_current_base_fee`, `…_trailing_spend_drives_when_the_base_fee_fell` | §3.2 | PASS |
| M8 `totalSupply()` < 2 × Safe cSSV (start, daily) | warn | `test_m8_supply_below_twice_the_seed_warns` | `…_at_the_floor_is_silent`, `…_well_above…`; zero supply warns (ruled interpretation) | PASS; F3 |
| M13 any incoming value to 0x3A98… / 0x4BC6… | page | `…_direct_top_up_of_either…[both]`, `…_internal_transfer_from_a_helper_contract_pages`, `…_without_any_log…`; my `test_qa2_m13_internal_transfer_from_helper_on_both_keys_in_one_block`; LIVE anvil fork (§3.4) | `…_zero_delta_blocks_stay_silent`, `…_outgoing_value_stays_silent`, `…_first_observation_is_a_silent_baseline` | PASS; F1, F2, F4 |
| §4 "confirm alerts ≥ 2 deep, except M13 and M12 pages" | — | checks run at the worker checkpoint (12 deep, documented latency in the lane README) | — | documented integration item (unchanged from attempt 1) |

Severity routing measured: page → `delivery = telegram` (M13 pages on the anvil fork; M12 health); warn → telegram with exact 24 h cadence (attempt-1 `test_warn_route_and_24h_cadence[m8,m12]`, re-run green); M13 6 h re-page (`test_m13_open_while_the_value_sits_then_re_paged_every_6h`).

### 3.2 Runway decision (event-0040), each part verified

- Default `projection_tip = "observed"` (`defaults`); "none" accepted (`projection_tip` validated). Base fee = mean of the last `base_fee_blocks` (300) `baseFeePerGas` from `eth_feeHistory` (last entry dropped: the next block's) — `test_m12_base_fee_is_the_mean_of_the_last_base_fee_blocks`.
- Alarm on the lower runway: `min(...)` (F12d `max` red). Both runways and all inputs in the evidence: `test_qa2_m12_runway_evidence_carries_both_runways_and_inputs` (green: `runway_trailing_days`, `runway_base_fee_days`, `avg_daily_spend_wei`, `base_fee_wei`, `avg_priority_fee_wei`, `tip_source`, `avg_daily_gas`, `balance_wei`, `window_txs`).
- "none" can only be quieter: the tip is clamped `≥ 0`, so the base-fee projection with "observed" ≥ with "none", and the trailing runway is identical — `test_qa2_m12_none_tip_is_never_louder_than_observed` (green; same chain: none's base-fee runway ≥ observed's, trailing equal).
- Empty tip sample → `fallback_tip_wei` (required, positive), flagged `tip_source = "fallback_tip_wei (no transaction in the window)"` — `test_m12_empty_tip_sample_uses_the_configured_fallback_never_zero`. **Observation (not a finding):** the fallback can never change a runway — an empty sample means zero window gas, hence a zero projection (`runway_base_fee_days = None`); it is evidence-only.
- LIVE (§3.4): at base fee 0.81 gwei (300-block mean) + observed 1 gwei tip, the oracle wallet's base-fee runway is **11.8 d < 14 d → warn**, trailing 18.98 d. Under the default policy M12 would warn on mainnet now (recorded only).

### 3.3 Accepted gap D10 (reported, not failures)

`test_qa2_m13_same_block_top_up_and_spend[…]`: a top-up of 0.5 and a spend of 0.2 by the leaked key in one block page with the net (+0.29997 ETH after the key's own fee); a spend of 0.6 in the same block is silent (the net is negative). `test_qa2_m13_long_gap_top_up_then_drain_inside_the_gap_is_the_sampled_d10_gap`: a top-up and drain between two sampled blocks of a 10,000-block gap is not paged, and the `m13-gap` health says "could not be excluded". Both are the documented D10 gap (within-block netting; sampled gap). Note for the spec amendment: within one block, M13 sees only the net, so incoming value swept in the same block by a larger outflow is invisible too. "Netting to zero" understates this; it is the same accepted mechanism.

### 3.4 LIVE (public read-only; record mode; scratch DBs under `/tmp/cp2c-qa2/live`)

| Step | Endpoint | Command | Result |
|---|---|---|---|
| L1 full sweep, m12+m8+m13, `config.example.json` | `https://eth-mainnet.public.blastapi.io` (archive) | `live.py <endpoint> blast.sqlite m12,m8,m13 2` (Appendix E), 15:40:59Z–15:42:00Z | exit 0. Step 1 (32.9 s), checkpoint 26,241,922: M12 738 verified txs (nonces 279–1016), 0.123223177465857884 ETH over 606,780 s = 0.017546 ETH/day, 750 tx + 750 receipts + 753 headers + 1 feeHistory; oracle 0.333001140643349157 ETH, liquidator 0.301417970739720016 ETH; base fee 0.81 gwei + observed tip 1 gwei → runway **18.98 d trailing / 11.8 d base-fee → one `warn` (telegram)** "runway 11.8 d (< 14 d, from current base fee)". M8 seed 1 cSSV, floor 2, supply 4,409,339.87 cSSV, silent. M13 baselines 22,691,702,922,000 / 31,560,987,664,500 wei, silent. Step 2 (1.5 s), checkpoint 26,241,926: incremental (no tx lookups, 3 headers, 1 feeHistory), runway incident kept, no new message; M13 8 balance reads (4 blocks × 2), silent; all run errors 0 |
| L2 publicnode null probe | `https://ethereum-rpc.publicnode.com` | Python probe over 60 hashes from L1's `m12_tx` (oldest 40 + 20 from the middle) | `eth_getTransactionByHash` 0/60 null; receipts null 20/60, then 0/60, then 10/60 (itemized: nonces 279–288): intermittent (§2) |
| L3 publicnode state depth | publicnode, drpc, blast | `curl eth_getBalance(0x3A98…, head − d)` | publicnode: d ≤ 100 OK, d ≥ 127 `-32602 Archive requests require a personal token`; drpc and blast OK at d = 200 … 100,000 |
| L4 M13 restart gap | publicnode / blast | `qa2_F2.sh [endpoint]` (Appendix B) | publicnode: every step `RpcError … Archive requests require a personal token`, `m13_watch` stuck at 26,241,679, `rpc` health at failure 3 (F2). Blast control: recovers, 256/301 sampled, `m13-gap` health, watch at the checkpoint |
| L5 anvil fork | `anvil --fork-url https://eth-mainnet.public.blastapi.io --port 8547` (fork at 26,241,951) | baseline `live.py http://127.0.0.1:8547 anvil.sqlite m13 1 <start>`; deploy a forwarder (runtime `CALL(gas, 0x3A98…, callvalue)`) and `cast send` it 0.25 ETH; direct `cast send 0x4BC6… 0.1 ETH`; `anvil_mine 13`; second run | two pages (telegram): "+0.250000 ETH at block 26241966 … none found (internal transfer or not attributable…)" and "+0.100000 ETH at block 26241967 … 0x33ce…afdbf (direct transfer to the address)"; 32 `eth_getBalance` (16 blocks × 2), 2 full blocks, 2 block-receipts |

Incidental LIVE observation for the coordinator (outside CP2c): the oracle wallet `0x16A5…` sent its last transaction at 08:12:11Z (nonce 1016, block 26,239,696). As of 15:41Z its nonce is still 1017 (≈ 7.5 h with no `updateClusterBalance`/`commitRoot`, against about 6 transactions per hour in the window). M11/M9 own this.

Budget: drpc was not retried (attempt 1 measured HTTP 429 there), and nodies was not used. There was one full M12 backfill (blast); the publicnode M12 backfill was replaced by the cheap 60-hash probe. The 483-test suite was run once, since no code changed afterwards. Attempt 1's LIVE rows were not repeated beyond L1.

## 4. Falsifiers

Matrix (implementer, V2x.5): 14/14 red then green, clean after each (§3 row V2x.5).

Mine (`python3 /tmp/cp2c-qa2/falsify_qa2.py "$PWD"`, Appendix D; one mutation at a time, restored with `git checkout -- <file>`, `git status --porcelain` printed after each; final line `all restored green and clean: True`):

| ID | Mutation | Suites | before / mutated / restored |
|---|---|---|---|
| Q12a | drop the canonical block-hash comparison in `verified` | test_m12 + attempt-1 independent (129) | 0 / **0 (129 passed: SURVIVES → F5)** / 0 |
| Q12a′ | same mutation | `test_qa2_m12_transaction_from_a_non_canonical_block_is_not_counted` | 1 passed / 1 failed / 1 passed; porcelain empty |
| Q12b | daily spend `>` → `>=` | same 129 | 0 / 1 (1 failed) / 0 |
| Q12c | rewind re-reads the nonce at the checkpoint instead of the ancestor | test_m12 | 0 / 1 (37 failed) / 0 |
| Q8a | drop the daily re-measure clause | test_m8 | 0 / 1 (3 failed) / 0 |
| Q8b | drop the zero-supply clause | test_m8 | 0 / 1 (7 failed) / 0 |
| Q13a | value-left `<=` → `<` | test_m13 | 0 / 1 (1 failed) / 0 |
| Q13b | rewind re-reads the watch at the checkpoint, not at the ancestor | test_m13 | 0 / 1 (19 failed) / 0 |
| Q-reorg-empty / health-only | attempt-1 plugin returns `[]` / health only in reorg runs | `-k shorter` | 3 failed / restored 3 passed |

Every restoration ended with `porcelain=''`.

## 5. Evidence-plane writes

- CP2c **Status:** `IMPLEMENTED — AWAITING QA (repair 1 …)` → `QA FAILED` naming head `c6b0a63` and this report.
- Wrote only `plans/monitoring-service/qa/cp2c-attempt-2.md`, the CP2c **Status:** line in `plans/monitoring-service/plan.md`, and one appended event in `plans/monitoring-service/execution-log.md`; plus the authorized ignored `reconcile/.venv` and scratch under `/tmp/cp2c-qa2` (reproducers, LIVE DBs, the frozen-code copy). No `config/`, `config.zip`, `.env*`, implementation, test, fixture or other-lane file touched. No mainnet transaction (anvil fork only), no real alert secret, record mode only.

## 6. Repo proof

Before the evidence-plane writes (after all mutation experiments): `git status --porcelain` → empty; `git diff --name-only c6b0a63dc590aad22910525583da0389687c2424` → empty. The output immediately before the commit is appended below.

```text
$ git status --porcelain
M  plans/monitoring-service/execution-log.md
M  plans/monitoring-service/plan.md
A  plans/monitoring-service/qa/cp2c-attempt-2.md

$ git diff --cached --name-only c6b0a63dc590aad22910525583da0389687c2424
plans/monitoring-service/execution-log.md
plans/monitoring-service/plan.md
plans/monitoring-service/qa/cp2c-attempt-2.md

$ git diff --cached --exit-code c6b0a63dc590aad22910525583da0389687c2424 -- reconcile; echo $?
0
```

**No implementation file appears in either output**; only the three authorized evidence-plane paths.

## Appendices (scratch sources; save each block to the named path to reproduce)

### A1 F1(a) — `/tmp/cp2c-qa2/repro/qa2_F1a.py`

```python
"""F1(a) reproducer: M13 re-pages a top-up that was already paged AND resolved, when a reorg re-includes the same
top-up and drain at the same blocks above the ancestor. Exit 1 = defect present."""
import sys, tempfile
from pathlib import Path
import test_m13 as t

with tempfile.TemporaryDirectory() as d:
    chain, w = t.setup(Path(d) / "a")
    t.step(w, chain, blocks=3)
    fork = chain.number
    t.top_up(chain); chain.mine(); t.drain(chain)       # top-up at fork, drain at fork + 1
    t.step(w, chain)
    before = t.sent(w)
    chain.reorg(fork)                                    # depth-2 reorg (checkpoint fork + 1 > ancestor fork - 1)
    t.top_up(chain); chain.mine(); t.drain(chain)       # the same transfers re-included at the same blocks
    t.step(w, chain)
    after = t.sent(w)
    print("ancestor:", fork - 1, "| marker after:", w.db.meta("reorg:m13"))
    print("messages before the reorg:", before)
    print("messages after the reorg :", after)
    print("expected (same evidence, already paged and resolved): no new message;",
          "actual new messages:", after[len(before):])
    sys.exit(1 if len(after) > len(before) else 0)
```

### A2 F1(b) — `/tmp/cp2c-qa2/repro/qa2_F1b.py`

```python
"""F1(b) reproducer: in a reorg run whose checkpoint is BELOW a top-up's block (shorter replacement chain), M13
does not re-report the key, so the framework resolves it as "evidence left the canonical chain"; the re-included
top-up (same block) is then paged again as a new incident. Exit 1 = defect present."""
import sys, tempfile
from pathlib import Path
import test_m13 as t
from harness import outbox

with tempfile.TemporaryDirectory() as d:
    chain, w = t.setup(Path(d), confirmations=2)
    t.step(w, chain, blocks=3)
    fork = chain.number
    t.top_up(chain)                     # top-up at block `fork`
    t.step(w, chain, blocks=5)          # checkpoint fork + 3: paged
    before = t.sent(w)
    chain.reorg(fork)                   # 4 blocks deep, beyond the 2-block confirmation window
    t.top_up(chain)                     # the same top-up re-included at the same block
    t.step(w, chain, blocks=1)          # shorter chain: sealed = fork, checkpoint = fork - 2 < fork (the reorg run)
    mid = t.sent(w)
    print("reorg run checkpoint:", w.checkpoint()["block"], "top-up block:", fork, "marker:", w.db.meta("reorg:m13"))
    t.step(w, chain, blocks=3)          # the checkpoint passes the re-included top-up
    after = t.sent(w)
    print("before:", before)
    print("after reorg run:", mid[len(before):])
    print("after the chain regrew:", after[len(mid):])
    left = [r["text"].splitlines()[0][:110] for r in outbox(w, "kind='resolved'")]
    print("resolutions:", left)
    print("expected: the key stays open (its block is above the reorg run's checkpoint: no proof it left), one page "
          "in total; actual pages:", sum(1 for s in after if s[1] == "open"))
    sys.exit(1 if sum(1 for s in after if s[1] == "open") > 1 else 0)
```

### A3 F3 — `/tmp/cp2c-qa2/repro/qa2_F3.py`

```python
"""F3 reproducer: after a restart, if the first M8 run fails after reading the seed balance (totalSupply errors),
the in-memory `measured` flag survives the rollback and every later run uses the PREVIOUS process's floor (D3:
measured at worker start). Exit 1 = defect present."""
import sys, tempfile
from pathlib import Path
import test_m8 as t
from fixtures_wallets import stub_cssv, wallet_worker
from harness import advance

with tempfile.TemporaryDirectory() as d:
    chain, w = t.setup(Path(d), supply=3 * t.SEED)
    advance(w)
    print("process 1 floor:", t.state(w))
    stub_cssv(chain, 3 * t.SEED, 2 * t.SEED)   # while the worker is down the Safe's cSSV doubles: floor 4 x SEED > supply 3 x SEED
    chain.mine()
    _, w2 = wallet_worker(Path(d), ["m8"], chain=chain, start_block=w.cfg.start_block,
                          activation_block=w.cfg.activation_block, clock=w.clock)
    orig, fail = chain._handle, [1]
    def handle(m, p):
        if m == "eth_call" and p[0].get("data", p[0].get("input", "")).startswith("0x18160ddd") and fail[0]:
            fail[0] -= 1
            raise ValueError("one transient totalSupply error")
        return orig(m, p)
    chain._handle = handle
    w2.step()                                   # restart's first run: balanceOf ok, totalSupply fails → rolled back
    chain.mine(); w2.clock.advance(301); w2.step()
    st = t.state(w2)
    print("process 2 floor after its first successful run:", st)
    print("expected seed", 2 * t.SEED, "(measured in this process) and a warn; actual seed", st["seed"],
          "alerts", t.alerts(w2))
    sys.exit(1 if st["seed"] != str(2 * t.SEED) else 0)
```

### A4 F4 — `/tmp/cp2c-qa2/repro/qa2_F4.py`

```python
"""F4 reproducer: M13's best-effort attribution raises AttributeError on a receipts list with a null entry, so the
whole run raises and the page is never sent (retried with the same failure every step). Exit 1 = defect present."""
import sys, tempfile
from pathlib import Path
import test_m13 as t
from harness import outbox

with tempfile.TemporaryDirectory() as d:
    chain, w = t.setup(Path(d))
    orig = chain._handle
    chain._handle = lambda m, p: ([None, *r] if m == "eth_getBlockReceipts" and isinstance(r := orig(m, p), list) else orig(m, p))
    t.top_up(chain)
    for _ in range(3):
        t.step(w, chain)
    print("incident messages:", t.sent(w))
    print("health:", [r["text"].splitlines()[-1][:120] for r in outbox(w, "kind='health'")])
    print("expected one page (attribution is best effort, 'never blocks the page'); actual pages:",
          len([1 for s in t.sent(w) if s[1] == "open"]))
    sys.exit(1 if not t.sent(w) else 0)
```

### B F2 (LIVE; uses Appendix E) — `/tmp/cp2c-qa2/repro/qa2_F2.sh`

```sh
#!/bin/zsh
# F2 reproducer (LIVE, read-only): M13 on a non-archive RPC never recovers from a restart gap deeper than the node's
# state window. Step 1 baselines; the scratch DB is then set to "last read 300 blocks ago" (what a ~1 h outage
# leaves); steps 2-4 must read (last, checkpoint] and fail with the node's archive error every time.
cd /Users/marco/orca/workspaces/ssv-network-monitor/ms-qa-cp2c-3
export PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/reconcile"
U=${1:-https://ethereum-rpc.publicnode.com}; rm -f /tmp/cp2c-qa2/live/pn.sqlite
out=$(reconcile/.venv/bin/python -u /tmp/cp2c-qa2/live/live.py $U pn.sqlite m13 1 2>/dev/null); print -r -- "$out" | jq -c '{step, cp: .checkpoint.block, m13}'
SB=$(print -r -- "$out" | jq -r '.checkpoint.block')
reconcile/.venv/bin/python -c "import sqlite3; c=sqlite3.connect('/tmp/cp2c-qa2/live/pn.sqlite'); c.execute('UPDATE m13_watch SET block=block-300'); c.commit()"
reconcile/.venv/bin/python -u /tmp/cp2c-qa2/live/live.py $U pn.sqlite m13 3 $SB 2>/dev/null | jq -c '{step, cp: .checkpoint.block, rpc_failures, m13_last_read: [.m13[].block], err: (.run_meta["run:m13"]|fromjson|.last_error), health: [.outbox[]|select(.kind=="health")|.t[0:60]]}'
```

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/reconcile:$PWD/reconcile/tests" reconcile/.venv/bin/python /tmp/cp2c-qa2/repro/<file>.py` from the worktree root; each exits 1 while its defect is present. F2: `/tmp/cp2c-qa2/repro/qa2_F2.sh` (publicnode, stalls) and `/tmp/cp2c-qa2/repro/qa2_F2.sh https://eth-mainnet.public.blastapi.io` (archive control, recovers).

### C1 QA2 adversarial scenarios — `/tmp/cp2c-qa2/test_qa2.py`

```python
"""CP2c QA attempt 2 — independent adversarial scenarios (scratch, not committed to the repo).

Expected outcomes come from the spec (M8, M12, M13), D3/D4/D10 and checks/README.md §6.2, not from the code.
Run: PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=reconcile:reconcile/tests reconcile/.venv/bin/python -m pytest -q \
     /tmp/cp2c-qa2/test_qa2.py
"""

from __future__ import annotations

import json

import pytest
import test_m12 as t12
import test_m13 as t13
import test_m8 as t8
from fixtures_wallets import (
    FUNDER,
    HELPER,
    LIQUIDATOR,
    ORACLE,
    PROXY,
    RETIRED_LIQUIDATOR,
    RETIRED_SLOT4,
    eth,
    gwei,
    stub_cssv,
    wallet_worker,
)
from harness import advance, outbox

REORG_TEXT = "Resolved because its evidence left the canonical chain"


# =====================================================================================================  M13


def test_qa2_m13_reorg_reincluding_a_resolved_top_up_and_its_drain_does_not_page_again(tmp_path):
    """Class 2/10: top-up at T and drain at R, both above the ancestor A, both paged/resolved. A deep reorg
    re-includes the SAME two transfers at the SAME blocks (the usual outcome of a reorg). The canonical evidence is
    identical to what was already paged and resolved: no second page, no second resolution."""
    chain, w = t13.setup(tmp_path, confirmations=2)
    t13.step(w, chain, blocks=3)
    fork = chain.number
    t13.top_up(chain)
    chain.mine()
    t13.drain(chain)
    t13.step(w, chain, blocks=4)
    assert [k for _, k, _ in t13.sent(w)] == ["open", "resolved"]
    chain.reorg(fork)
    t13.top_up(chain)  # the same top-up, re-included at the same block (new tx hash)
    chain.mine()
    t13.drain(chain)  # the same drain, at the same block
    t13.step(w, chain, blocks=4)
    assert w.db.meta("reorg:m13") is None
    assert [k for _, k, _ in t13.sent(w)] == ["open", "resolved"], t13.sent(w)


def test_qa2_m13_reorg_reincluding_only_the_drain_of_an_older_top_up_does_not_resolve_twice(tmp_path):
    """Class 2: top-up at T <= A survives; drain at R > A re-included at R. One resolution in total."""
    chain, w = t13.setup(tmp_path, confirmations=2)
    t13.step(w, chain, blocks=3)  # baseline taken (checkpoint reached start_block)
    t13.top_up(chain)
    t13.step(w, chain, blocks=4)
    t13.step(w, chain, blocks=2)
    fork = chain.number
    t13.drain(chain)
    t13.step(w, chain, blocks=4)
    assert [k for _, k, _ in t13.sent(w)] == ["open", "resolved"]
    chain.reorg(fork)
    t13.drain(chain)
    t13.step(w, chain, blocks=4)
    assert [k for _, k, _ in t13.sent(w)] == ["open", "resolved"], t13.sent(w)
    assert t13.open_keys(w) == []


@pytest.mark.parametrize("out_value,expected", [(eth(0.2), "+0.29997"), (eth(0.6), None)])
def test_qa2_m13_same_block_top_up_and_spend(tmp_path, out_value, expected):
    """Class 3: a top-up and a spend by the leaked key in ONE block. Net +0.3 pages with the net delta; net -0.1
    is silent — D10 accepted gap (within-block transfers are only seen by their net)."""
    chain, w = t13.setup(tmp_path)
    chain.set_balance(RETIRED_SLOT4, eth(1))  # value present before (baseline is 0.01: make a new baseline)
    t13.step(w, chain)
    base = len(t13.sent(w))
    t13.top_up(chain, value=eth(0.5))
    t13.drain(chain, value=out_value)
    t13.step(w, chain)
    msgs = outbox(w, "kind='open'")[base:]
    if expected is None:
        assert msgs == []
    else:
        assert len(msgs) == 1 and expected in msgs[0]["text"]


def test_qa2_m13_long_restart_gap_bounded_reads_and_page(tmp_path):
    """Class 6: a 10,000-block restart gap with a top-up in the middle (kept): one page with the sampled-gap wording,
    an m13-gap health message, eth_getBalance per address bounded by the cap."""
    chain, w = t13.setup(tmp_path)
    chain.mine(4_000)
    t13.top_up(chain, RETIRED_LIQUIDATOR, eth(0.3))
    chain.mine(6_000)
    _, w2 = wallet_worker(tmp_path, ["m13"], chain=chain, start_block=w.cfg.start_block,
                          activation_block=w.cfg.activation_block, clock=w.clock)
    advance(w2)
    pages = outbox(w2, "kind='open'")
    assert len(pages) == 1 and "sampled" in pages[0]["text"] and "+0.300000 ETH" in pages[0]["text"]
    assert any(": m13-gap" in r["text"] for r in outbox(w2, "kind='health'"))
    run = w2.db.meta("run:m13")
    assert run["rpc_calls"].get("eth_getBalance", 0) <= 2 * 256, run


def test_qa2_m13_long_gap_top_up_then_drain_inside_the_gap_is_the_sampled_d10_gap(tmp_path):
    """Class 6 beyond the cap: a top-up and its drain between two sampled blocks is not seen (documented: the
    page/health says netting could not be excluded; accepted gap D10)."""
    chain, w = t13.setup(tmp_path)
    chain.mine(4_000)
    t13.top_up(chain, RETIRED_SLOT4, eth(0.3))
    chain.mine(2)
    t13.drain(chain, RETIRED_SLOT4, eth(0.3))
    chain.mine(6_000)
    _, w2 = wallet_worker(tmp_path, ["m13"], chain=chain, start_block=w.cfg.start_block,
                          activation_block=w.cfg.activation_block, clock=w.clock)
    advance(w2)
    assert outbox(w2, "kind='open'") == []
    gap = [r for r in outbox(w2, "kind='health'") if ": m13-gap" in r["text"]]
    assert gap and "could not be excluded" in gap[0]["text"]


def test_qa2_m13_malformed_receipt_entry_must_not_block_the_page(tmp_path):
    """Best-effort attribution must never block the page: an eth_getBlockReceipts list with a null entry."""
    chain, w = t13.setup(tmp_path)
    orig = chain._handle

    def handle(m, p):
        r = orig(m, p)
        if m == "eth_getBlockReceipts" and isinstance(r, list):
            return [None, *r]
        return r

    chain._handle = handle
    t13.top_up(chain)
    t13.step(w, chain)
    assert [k for _, k, _ in t13.sent(w)] == ["open"], [r["text"][:200] for r in outbox(w)]


def test_qa2_m13_internal_transfer_from_helper_on_both_keys_in_one_block(tmp_path):
    chain, w = t13.setup(tmp_path)
    chain.internal_transfer(RETIRED_SLOT4, eth(0.1))
    chain.internal_transfer(RETIRED_LIQUIDATOR, eth(0.2))
    b = chain.number
    t13.step(w, chain)
    assert sorted(k for _, _, k in t13.sent(w)) == sorted([f"{RETIRED_SLOT4}:{b}", f"{RETIRED_LIQUIDATOR}:{b}"])
    assert all(HELPER[2:] in r["text"] or "log of" in r["text"] for r in outbox(w, "kind='open'"))


def test_qa2_m13_empty_reorg_result_on_shorter_chain_never_clean(tmp_path):
    """Class 1 in a reorg run: eth_getBalance null at the ancestor re-read → raise, marker kept, page kept."""
    chain, w, fork = t13._top_up_then(tmp_path)
    t13.step(w, chain)
    chain.reorg(fork)
    chain.empty_methods.add("eth_getBalance")
    w.step()
    assert w.db.meta("reorg:m13") is not None and t13.open_keys(w) == [f"{RETIRED_SLOT4}:{fork}"]
    chain.empty_methods.discard("eth_getBalance")
    chain.mine()
    advance(w)
    assert w.db.meta("reorg:m13") is None and t13.open_keys(w) == []


# =====================================================================================================  M8


def test_qa2_m8_restart_whose_first_run_fails_after_the_seed_read_still_measures_at_start(tmp_path):
    """D3 'measured at worker start' + class 10: restart; the Safe's cSSV moved while the worker was down; the
    first run reads balanceOf, then totalSupply fails (rolled back). The next run must use a floor measured in
    THIS process, not the previous process's floor."""
    chain, w = t8.setup(tmp_path, supply=3 * t8.SEED)
    advance(w)
    assert t8.state(w)["seed"] == str(t8.SEED)
    stub_cssv(chain, 3 * t8.SEED, 2 * t8.SEED)  # Safe staked more while down: floor 4 x SEED > supply
    chain.mine()
    _, w2 = wallet_worker(tmp_path, ["m8"], chain=chain, start_block=w.cfg.start_block,
                          activation_block=w.cfg.activation_block, clock=w.clock)
    orig = chain._handle
    fail = {"n": 1}

    def handle(m, p):
        if m == "eth_call" and p[0].get("data", p[0].get("input", "")).startswith("0x18160ddd") and fail["n"]:
            fail["n"] -= 1
            raise ValueError("totalSupply transport error (test)")
        return orig(m, p)

    chain._handle = handle
    w2.step()  # balanceOf ok, totalSupply fails: rolled back
    assert fail["n"] == 0 and w2.db.meta("run:m8")["errors"] >= 1
    chain.mine()
    w2.clock.advance(301)
    w2.step()
    assert t8.state(w2)["seed"] == str(2 * t8.SEED), t8.state(w2)
    assert t8.alerts(w2)[-1:] == [("warn", "open")], t8.alerts(w2)


def test_qa2_m8_daily_remeasure_on_a_deleted_block_then_reorg_with_a_different_safe_balance(tmp_path):
    chain, w = t8.setup(tmp_path, supply=3 * t8.SEED, confirmations=2)
    advance(w)
    t8.tick(w, chain, blocks=3)
    fork = chain.number
    stub_cssv(chain, 3 * t8.SEED, t8.SEED // 2)  # deleted chain: Safe sold half
    t8.tick(w, chain, blocks=5, seconds=86_400)  # daily re-measure on the deleted chain
    assert t8.state(w)["seed"] == str(t8.SEED // 2)
    chain.reorg(fork)
    stub_cssv(chain, 3 * t8.SEED, 2 * t8.SEED)  # new chain: Safe staked more → floor 4 x SEED > supply
    t8.tick(w, chain, blocks=6)
    assert t8.state(w)["seed"] == str(2 * t8.SEED) and t8.state(w)["block"] > fork
    assert [a for a in t8.alerts(w)] == [("warn", "open")]


def test_qa2_m8_resolved_key_reorg_where_the_new_chain_still_recovers_is_silent(tmp_path):
    chain, w = t8.setup(tmp_path, supply=t8.SEED, confirmations=2)
    advance(w)
    t8.tick(w, chain, blocks=3)
    fork = chain.number
    stub_cssv(chain, 3 * t8.SEED, t8.SEED)
    t8.tick(w, chain, blocks=5)
    assert t8.alerts(w) == [("warn", "open"), ("info", "resolved")]
    chain.reorg(fork)
    stub_cssv(chain, 3 * t8.SEED, t8.SEED)  # the recovery re-included
    t8.tick(w, chain, blocks=6)
    assert t8.alerts(w) == [("warn", "open"), ("info", "resolved")] and w.db.meta("reorg:m8") is None


# =====================================================================================================  M12


def _m12_reorg(tmp_path, page_mutator):
    chain, w, _ = t12.setup(tmp_path, balance=eth(10))
    advance(w)
    t12.tick(w, chain, blocks=5)
    fork = chain.number
    chain.send(ORACLE, PROXY, gas_used=1_000_000, gas_price=gwei(40))  # 0.04 ETH daily spend → warn
    t12.tick(w, chain)
    assert t12.open_incidents(w) == {f"{ORACLE}:daily-spend": "warn"}
    chain.reorg(fork)
    chain.send(ORACLE, PROXY, gas_used=150_000)  # the new chain: a cheap transaction instead
    real = w.bs

    def bs(url):
        return page_mutator(real(url))

    w.checks["m12"].http_get = bs
    t12.tick(w, chain)
    return chain, w, real


def test_qa2_m12_reorg_run_with_an_empty_blockscout_page_raises(tmp_path):
    chain, w, real = _m12_reorg(tmp_path, lambda page: {"items": [], "next_page_params": None})
    assert w.db.meta("reorg:m12") is not None
    assert t12.open_incidents(w) == {f"{ORACLE}:daily-spend": "warn"}
    w.checks["m12"].http_get = real
    t12.tick(w, chain)
    assert w.db.meta("reorg:m12") is None and t12.open_incidents(w) == {}


def test_qa2_m12_reorg_run_with_a_partial_page_raises(tmp_path):
    chain, w, real = _m12_reorg(tmp_path, lambda page: dict(page, items=page["items"][1:]))
    assert w.db.meta("reorg:m12") is not None
    assert t12.open_incidents(w) == {f"{ORACLE}:daily-spend": "warn"}


def test_qa2_m12_reorg_run_with_duplicated_items_commits_and_counts_once(tmp_path):
    chain, w, real = _m12_reorg(tmp_path, lambda page: dict(page, items=page["items"] * 2))
    assert w.db.meta("reorg:m12") is None and t12.open_incidents(w) == {}
    rows = w.db.conn.execute("SELECT COUNT(*), COUNT(DISTINCT nonce) FROM m12_tx").fetchone()
    assert rows[0] == rows[1]


def test_qa2_m12_reorg_run_with_a_stale_orphan_item_only_raises(tmp_path):
    """Blockscout still lists the orphaned costly tx (stale index) and not the new chain's tx: the orphan fails
    on-chain verification, the nonce is missing → raise, never 'clean'."""
    chain, w, _ = t12.setup(tmp_path, balance=eth(10))
    advance(w)
    t12.tick(w, chain, blocks=5)
    fork = chain.number
    orphan = chain.send(ORACLE, PROXY, gas_used=1_000_000, gas_price=gwei(40))
    t12.tick(w, chain)
    orphan_item = w.bs.item(chain.wtxs[orphan])
    chain.reorg(fork)
    new = chain.send(ORACLE, PROXY, gas_used=150_000)
    w.bs.hide.add(new)
    w.bs.forged.append(orphan_item)
    t12.tick(w, chain)
    assert w.db.meta("reorg:m12") is not None
    assert t12.open_incidents(w) == {f"{ORACLE}:daily-spend": "warn"}


def test_qa2_m12_same_block_top_up_and_spend_balance_is_end_of_block(tmp_path):
    chain, w, _ = t12.setup(tmp_path, balance=eth(0.149), gas_per_day=1_000_000)
    advance(w)
    assert t12.open_incidents(w).get(f"{ORACLE}:balance") == "page"
    chain.send(FUNDER, ORACLE, gas_used=21_000, value=0)  # noise
    chain.credit(ORACLE, eth(0.5))  # funding and a spend in one block
    chain.send(ORACLE, PROXY, gas_used=150_000)
    t12.tick(w, chain)
    assert f"{ORACLE}:balance" not in t12.open_incidents(w)


def test_qa2_m12_liquidator_page_while_a_reorg_run_waits_on_blockscout(tmp_path):
    """Observation (documented accepted consequence): during a pending M12 reorg run with Blockscout down, balance
    pages are not evaluated; the operator sees check-error health on Telegram and healthy: false."""
    chain, w, real = _m12_reorg(tmp_path, lambda page: (_ for _ in ()).throw(OSError("down")))
    chain.set_balance(LIQUIDATOR, eth(0.01))
    t12.tick(w, chain)
    assert f"{LIQUIDATOR}:balance" not in t12.open_incidents(w)
    hs = [r for r in outbox(w, "kind='health'") if "Check m12 raised" in r["text"]]
    assert hs and hs[0]["delivery"] == "telegram"
    assert w.degraded()
    w.checks["m12"].http_get = real
    t12.tick(w, chain)
    assert t12.open_incidents(w).get(f"{LIQUIDATOR}:balance") == "page"


def test_qa2_m12_none_tip_is_never_louder_than_observed(tmp_path):
    """RUNWAY DECISION: for the same chain, 'none' gives a runway >= 'observed'."""
    out = {}
    for mode in ("observed", "none"):
        d = tmp_path / mode
        d.mkdir()
        chain, w, _ = t12.setup(d, balance=eth(0.3), checks={"projection_tip": mode}, gas_price=gwei(1.5))
        advance(w)
        m = w.checks["m12"].last[ORACLE]
        out[mode] = (m["runway_trailing_days"], m["runway_base_fee_days"], m["tip_source"])
    assert out["none"][1] >= out["observed"][1] and out["none"][0] == out["observed"][0]
    assert out["none"][2].startswith("none") and out["observed"][2].startswith("observed")


def test_qa2_m12_runway_evidence_carries_both_runways_and_inputs(tmp_path):
    chain, w, _ = t12.setup(tmp_path, balance=eth(0.17825))
    advance(w)
    inc = w.db.conn.execute("SELECT evidence FROM incidents WHERE key=?", (f"{ORACLE}:runway",)).fetchone()
    ev = json.loads(inc[0])
    for k in ("runway_trailing_days", "runway_base_fee_days", "avg_daily_spend_wei", "base_fee_wei",
              "avg_priority_fee_wei", "tip_source", "avg_daily_gas", "balance_wei", "window_txs"):
        assert k in ev, k


def test_qa2_m12_transaction_from_a_non_canonical_block_is_not_counted(tmp_path):
    """Untrusted-input rule 'canonical block hash': a node (e.g. a load-balanced backend on another fork) answers
    the tx AND its receipt with a block hash that is not the canonical one at that height → not verified → the
    nonce is missing → UNKNOWN, never counted."""
    chain, w, _ = t12.setup(tmp_path)
    tx_obj, receipt = chain._tx_obj, chain._receipt
    victim = sorted(chain.wtxs.values(), key=lambda t: t["block"])[-1]["hash"]
    fork_hash = "0x" + "fa" * 32
    chain._tx_obj = lambda t: dict(tx_obj(t), blockHash=fork_hash) if t["hash"] == victim else tx_obj(t)
    chain._receipt = lambda t: dict(receipt(t), blockHash=fork_hash) if t["hash"] == victim else receipt(t)
    advance(w)
    assert "UNKNOWN" in w.checks["m12"].last[ORACLE].get("spend", ""), w.checks["m12"].last[ORACLE]
    assert w.db.conn.execute("SELECT COUNT(*) FROM m12_tx WHERE tx_hash=?", (victim,)).fetchone()[0] == 0
```

### C2 wide sweeps (implementer sweeps on new seeds) — `/tmp/cp2c-qa2/test_sweeps_wide.py`

```python
"""The implementer's three sweeps on 200 NEW seeds each (20..219) × confirmations 0/2, plus a QA invariant for
M13: a canonical top-up is paged at most once unless its drain was orphaned (counted, reported)."""
import collections
import pytest
import test_m12 as t12
import test_m13 as t13
import test_m8 as t8

SEEDS = range(20, 220)
DUP = collections.Counter()


@pytest.mark.parametrize("confirmations", [0, 2])
@pytest.mark.parametrize("seed", SEEDS)
def test_wide_m12(tmp_path, seed, confirmations):
    t12.test_m12_adversarial_reorg_sweep(tmp_path, seed, confirmations)


@pytest.mark.parametrize("confirmations", [0, 2])
@pytest.mark.parametrize("seed", SEEDS)
def test_wide_m8(tmp_path, seed, confirmations):
    t8.test_m8_adversarial_reorg_sweep(tmp_path, seed, confirmations)


@pytest.mark.parametrize("confirmations", [0, 2])
@pytest.mark.parametrize("seed", SEEDS)
def test_wide_m13(tmp_path, seed, confirmations):
    t13.test_m13_adversarial_reorg_sweep(tmp_path, seed, confirmations)


@pytest.mark.parametrize("confirmations", [0, 2])
@pytest.mark.parametrize("seed", SEEDS)
def test_wide_m13_no_duplicate_page_for_identical_evidence(tmp_path, seed, confirmations):
    s = t13.M13Sweep(tmp_path, seed, confirmations)
    s.run()
    opens = collections.Counter(k for sev, kind, k in t13.sent(s.w) if kind == "open")
    dups = {k: n for k, n in opens.items() if n > 1}
    assert not dups, dups
```

### D own falsifier runner — `/tmp/cp2c-qa2/falsify_qa2.py`

```python
"""QA2 own falsifiers: one-token mutations, run the named suites, restore, prove a clean tree."""
import os, subprocess, sys
ROOT = sys.argv[1]
PY = f"{ROOT}/reconcile/.venv/bin/python"
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": f"{ROOT}/reconcile:{ROOT}/reconcile/tests"}
MUT = [
 ("Q12a", "reconcile/checks/m12_wallet_burn.py", 'if hdr["hash"].lower() == t["block_hash"]:', "if True:",
  ["reconcile/tests/test_m12.py", "/tmp/cp2c-qa2/a1/test_independent_v2.py"]),
 ("Q12b", "reconcile/checks/m12_wallet_burn.py", 'if m["day_spend_wei"] > w.spend_warn:', 'if m["day_spend_wei"] >= w.spend_warn:',
  ["reconcile/tests/test_m12.py", "/tmp/cp2c-qa2/a1/test_independent_v2.py"]),
 ("Q12c", "reconcile/checks/m12_wallet_burn.py", "            (n,) = nonces(ctx, [wallet], ancestor)\n", "            (n,) = nonces(ctx, [wallet], ctx.block)\n",
  ["reconcile/tests/test_m12.py"]),
 ("Q8a", "reconcile/checks/m8_supply_floor.py", ' or ctx.clock() - st["at"] >= self.config["remeasure_s"]', "",
  ["reconcile/tests/test_m8.py"]),
 ("Q8b", "reconcile/checks/m8_supply_floor.py", "if supply < floor or supply == 0:", "if supply < floor:",
  ["reconcile/tests/test_m8.py"]),
 ("Q13a", "reconcile/checks/m13_retired_keys.py", "if bal <= int(row[1]):", "if bal < int(row[1]):",
  ["reconcile/tests/test_m13.py"]),
 ("Q13b", "reconcile/checks/m13_retired_keys.py", "        for a, bal in zip(stale, balances_at(ctx, [(a, ancestor) for a in stale])):\n",
  "        for a, bal in zip(stale, balances_at(ctx, [(a, ctx.block) for a in stale])):\n", ["reconcile/tests/test_m13.py"]),
]
def run(files):
    r = subprocess.run([PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-W", "ignore", "--tb=no", *files], cwd=ROOT,
                       capture_output=True, text=True, env=ENV)
    return r.returncode, r.stdout.strip().splitlines()[-1]
ok = True
for name, path, old, new, files in MUT:
    full = f"{ROOT}/{path}"
    src = open(full).read()
    assert src.count(old) == 1, (name, old)
    before = run(files)
    open(full, "w").write(src.replace(old, new))
    red = run(files)
    subprocess.run(["git", "checkout", "--", path], cwd=ROOT, check=True)
    after = run(files)
    st = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout
    print(f"{name}: before {before} | mutated {red} | restored {after} | porcelain={st!r}", flush=True)
    ok &= before[0] == 0 and after[0] == 0 and st == ""
print("all restored green and clean:", ok)
```

### E LIVE runner — `/tmp/cp2c-qa2/live/live.py`

```python
"""CP2c QA2 LIVE: config.example.json checks (record mode, scratch DB), two steps; argv: endpoint, db-name, checks."""
import json, sys, time
from pathlib import Path
from eb_reconcile import Config, DEFAULTS, DB, Worker
from rpc import Rpc
root = Path.cwd(); scratch = Path('/tmp/cp2c-qa2/live')
endpoint, dbname, only = sys.argv[1], sys.argv[2], sys.argv[3].split(',')
steps = int(sys.argv[4]) if len(sys.argv) > 4 else 2
rpc = Rpc(endpoint, timeout=20, retries=1, batch_size=20)
head = rpc.block_number()
raw = json.loads((root / 'reconcile/config.example.json').read_text())
db_path = scratch / dbname
fresh = not db_path.exists()
sb = int(sys.argv[5]) if len(sys.argv) > 5 else head - 12
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
           'outbox': [dict(r) for r in w.db.conn.execute('SELECT key, severity, kind, delivery, substr(text,1,160) t FROM outbox')]}
    if 'm12' in w.checks: out['m12'] = w.checks['m12'].last
    if 'm8' in w.checks: out['m8'] = w.checks['m8'].last
    if 'm13' in w.checks: out['m13'] = [dict(r) for r in w.db.conn.execute('SELECT * FROM m13_watch')]
    print(json.dumps(out, default=str), flush=True)
    time.sleep(13)
```

### F pytest wrapper — `/tmp/cp2c-qa2/p.sh`

```sh
#!/bin/zsh
# usage: p.sh <pytest args...>; prints the last line and the pytest exit code
cd /Users/marco/orca/workspaces/ssv-network-monitor/ms-qa-cp2c-3
export PYTHONDONTWRITEBYTECODE=1
out=$(reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider "$@" 2>&1); rc=$?
print -r -- "$out" | tail -${TAILN:-1}
echo "exit=$rc"
```

Attempt-1 inputs re-used verbatim: Appendix A → `/tmp/cp2c-qa2/a1/repro_a.py`, Appendix B → `/tmp/cp2c-qa2/a1/test_independent.py` (and `test_independent_v2.py`: line 89 `assert 'UNKNOWN' in …['spend']` → `pass`), Appendix D plugin → `/tmp/cp2c-qa2/a1/failopen_plugin.py`, all extracted from `qa/cp2c-attempt-1.md` with awk/sed.
