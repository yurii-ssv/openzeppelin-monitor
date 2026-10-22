FAIL

# CP2e (Legacy SSV: M6, M7) — independent QA, attempt 1

- QA: Claude Opus 5.5 (`claude-opus-5-5`), effort high (D13; coordinator addendum `msg_3486fb6cfa8b`). Task `task_283fff5f1b19`, dispatch `ctx_ba0224588278`.
- Frozen head under test: `c02557a0b17e557581e8c1af198dd8b675fbb33f` (lane `mtabasco/ms-cp2e`). Worktree `ms-qa-cp2e`, branch `mtabasco/ms-qa-cp2e`, HEAD = frozen head at start, porcelain 0 lines. Observed 2026-10-22T07:5xZ–08:4xZ (host clock, UTC).
- Round policy: lane has 0 failed QA rounds, so every finding blocks (addendum). Packet: `qa/QA-CONTRACT.md` at the frozen head; `qa/DEFECT-CLASSES.md` identical to `plan/monitoring-service` (empty diff).
- Preflight: disk 346 GiB free; venv created per plan §7 (`uv venv reconcile/.venv --python 3.12`; `uv pip install --python reconcile/.venv/bin/python -r reconcile/requirements-dev.txt`, exit 0), Python 3.12.15; anvil not needed (no fork row in CP2e).
- Frozen-file check: `git diff 96d1c32 c02557a -- reconcile/tests/{harness,test_framework,test_worker}.py` is **byte-identical** to `git diff bd531b4^ 500abe4 --` the same files (46 lines each, `cmp` equal; blobs equal to `500abe4`). Other `reconcile/` paths changed since `96d1c32`: only `checks/__init__.py` (one import line in the CP2e block), `config.example.json` (`checks.m6`, `checks.m7` only) and new lane files (`checks/m6_legacy_coverage.py`, `checks/m7_ssv_solvency.py`, `tests/test_m6.py`, `tests/test_m7.py`, `tests/fixtures_legacy.py`). No other frozen CP1B file edited.

## Spec trigger list (derived by QA from docs/ssv-specs/monitoring-service-spec.md M6, M7, with coordinator rulings Q1 A / Q2 A)

| # | Trigger | Severity | Firing test | Silent twin | QA check |
|---|---|---|---|---|---|
| M6.1 | active legacy cluster `isLiquidatableSSV` > 7 d (ruling: > 5000 bps × live `liquidation_threshold_period_ssv` blocks) | warn | `test_m6_liquidatable_longer_than_half_window_warns` | `test_m6_benign_liquidatable_exactly_half_window_stays_silent` | present; **boundary wrong on the back-dated path (F2)**; warn route Telegram (probe PR4 pass) |
| M6.2 | `getBalanceSSV == 0` while active (ruling: and validatorCount > 0; 0→>0 at balance 0 pages at its block) | page | `test_m6_zero_balance_while_active_pages`, `…validator_count_zero_to_positive…` | `…benign_zero_balance_without_validators…`, `…benign_positive_balance…` | present |
| M6.3 | weekly digest: count, validators, SSV/yr burn (+ ruling: inert-at-zero line) | digest | `test_m6_weekly_digest_counts_clusters_validators_and_burn` | same test (6 daily digests without M6 section) | present |
| M7.1 | daily surplus < 1 SSV | page | `test_m7_surplus_below_one_ssv_pages` | `test_m7_benign_surplus_exactly_one_ssv_does_not_page` | present; `<` correct per spec ("< 1 SSV"); LIVE value at the boundary |
| M7.2 | any change in the surplus | info | `test_m7_surplus_change_sends_info_under_one_incident` | `test_m7_benign_unchanged_surplus_sends_nothing` | present; A→B→A sends 3 infos on Discord (probe PR3 pass) |

Pinned-source semantics checked (`ssv-network@f7ac487`): `ClusterLib.isLiquidatable` returns false for `validatorCount == 0` and uses strict `balance < threshold`, threshold = `minimumBlocksBeforeLiquidation × (Σfee + networkFee) × vc` (= period × `getBurnRateSSV`); `updateBalanceSSV` saturates at 0 (the page condition is reachable, not a revert); `getBalanceSSV` reverts for inactive clusters (the checks call it only for active ones); `UnstakeRequested(address indexed, uint256 amount, uint256 unlockTime)` / `UnstakedWithdrawn(address indexed, uint256 amount)`: the first data word is the amount, as `_amount` reads it.

## 1. Findings (ranked)

### F1 (BLOCKING) — an empty SSV API listing at the first evaluation is read as "no legacy clusters": M7 records a clean baseline and misses a page; M6 evaluates nothing and says nothing

Defect classes 1 and 10 (empty input read as clean; first sweep with no baseline). The inventory accepts an HTTP-200 answer `{"clusters": [], "pagination": {"pages": 1, …}}` (and any short listing: `fetch_api_clusters` ignores `pagination.total`). After a good run the lane's own guards catch it (M6 persisted rows → `_fate` → UNKNOWN; M7 `m7_legacy` → UNKNOWN; probe PR2b passes), but on the **first** evaluation there is no persisted set, so:

- **M7**: the legacy balances drop out of the claims. Reproducer `test_pr2_m7_first_run_with_an_empty_api_listing` (Appendix A): one legacy cluster of 100 SSV, proxy short by 50 SSV (true surplus −49 SSV). Expected: page `surplus-floor` (or UNKNOWN health). Actual: `[('info', 'open', 'discord')]`, evidence `surplus = 51000000000000000000`, `legacy_clusters_priced = 0`, no page, no health. The wrong baseline also makes the next good run send a spurious "changed" info. On mainnet the omitted term is ≈ 7,175 SSV (live legacy balances), so a leak up to that size is masked for one `interval_s` (24 h) after the first start.
- **M6**: reproducer `test_pr2_m6_first_sweep_with_an_empty_api_listing`: an active legacy cluster at balance 0 with validators. Expected: page or UNKNOWN health. Actual: outbox empty, summary `active 0, unknown 0` (the digest would say "0 active" — a false clean), until the next API refresh.

Command: `QA_ROOT=$PWD reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider --rootdir <probes> <probes>/test_probe_m6.py::test_pr2_m6_first_sweep_with_an_empty_api_listing <probes>/test_probe_m7.py::test_pr2_m7_first_run_with_an_empty_api_listing` → 2 failed. Owner: lane (the checks own "UNKNOWN, never clean"; the real API exposes `pagination.total`, 1,908 at the LIVE block, which a completeness test can use); CP3 may prefer to fix it once in `inventory.fetch_api_clusters` (frozen to this lane).

### F2 (BLOCKING by round policy; low impact) — the back-dated "liquidatable since" is the last clear block, one block before the first liquidatable block, so the estimated path warns at exactly the half window (`>=`), the observed path one block later (`>`)

`_back_date` sets `since = block − ceil((threshold − balance) / burn)`. With the contract's strict `balance < threshold` and a balance that grows by `burn` per block going back, the balance at `block − k` (k = that ceil) is ≥ the threshold, i.e. **not** liquidatable; the first liquidatable block is `block − k + 1`. The coordinator ruling (Q1 A) asked for the boundary at exactly the threshold block (`>` vs `>=`) and a start "never later than the observed first block"; the estimate is consistently one block early, so:

- `test_pr1a_backdated_start_of_a_cluster_that_just_crossed`: balance `T − BURN` at block 1001 (so 1000 had balance `T`, not liquidatable). Expected `liquidatable_since == 1001`; actual `1000`, `since_estimated = 1`. The same chain observed block by block (`test_pr1b_…`, passes) records 1001, not estimated.
- `test_pr1c_true_duration_exactly_w_backdated_stays_silent`: first liquidatable block `b − W` (true duration exactly W = 50,120 blocks). Expected silent (rule `>`); actual one `warn` opened.
- The lane's own tests encode the off-by-one: `test_m6_liquidatable_longer_than_half_window_warns` asserts `since == blk − (W+1)` for a deficit of W+1 blocks (true first liquidatable block `blk − W`), and the "exactly half window" benign twin actually exercises a true duration of W − 1. Impact: one block (≈ 12 s) early on a 7-day warn, only on estimated starts; recorded because the ruling made this boundary explicit and the round policy makes every finding blocking.

### F3 (BLOCKING by round policy; test coverage) — plan §6 / DEFECT-CLASSES require a state table and a seeded adversarial sweep before `worker_done`; neither exists, and two QA mutations survive the lane's suite

`workers/cp2e/plan.md` has a design table and reorg rules but no state table with the (a)–(g) columns; `test_m6.py`/`test_m7.py` contain no seeded sweep (grep for seed/adversarial: none). DEFECT-CLASSES: "A cell without a test is a defect." Concrete gaps found by QA mutations (both restored, porcelain 0):

- QF7b: deleting the `surplus` info re-report in M7's UNKNOWN branch → `test_m7.py` 26 passed. The behaviour is correct at the head (probe PR12: an UNKNOWN reorg run keeps both keys under their ids and sends no second baseline), but no lane test covers a reorg run whose re-evaluation is UNKNOWN for M7.
- QF6d: dropping `opened | reorg_keys` from M6's second-loop enumeration → `test_m6.py` 31 passed (largely an equivalent mutant today because every open key has an `m6_clusters` row, but the §6.2 "enumerate from opened keys" rule has no test of its own).

## 2. Claims I could NOT confirm

- None of the implementer's claims failed to reproduce. The LIVE M7 "surplus = exactly 1 SSV" (attempt 3, block 26,242,622) re-measured at block 26,246,821 on an archive endpoint: **exactly 1.000000000000000000 SSV** again (matrix row LIVE M7). A first attempt on publicnode was NOT RUN (`eth_getStorageAt: Archive requests require a personal token`: the pinned block left publicnode's recent window during the M6 stage).
- Subgraph cross-check: DEFERRED by the plan (no `SSV_SUBGRAPH_API_KEY`); not attempted.
- Everything else the implementer claimed reproduced: 297 tests, ruff clean, V2x.1–V2x.4 counts, 12 reorg tests, F6a–c and F7a–d red→green, the V2e.7 API side, and the LIVE M6 numbers (below).

## 3. Verification matrix (every row run on its own; exit codes captured per command, no `&&` credit)

| V-ID | Command | Exit | Result |
|---|---|---|---|
| V2x.1 M6 | `reconcile/.venv/bin/python -m pytest -q reconcile/tests/test_m6.py` | 0 | 31 passed; trigger mapping above (QA-derived): every trigger has a firing test at its severity and a silent twin — but F2 (twin tests the wrong boundary on the estimated path) |
| V2x.1 M7 | `… -m pytest -q reconcile/tests/test_m7.py` | 0 | 26 passed; mapping above |
| V2x.2 | `… -m pytest -q reconcile/tests/test_m6.py reconcile/tests/test_m7.py -k thresholds_from_chain` | 0 | 3 passed, 54 deselected (M6 period change moves the warn; M6 live min collateral; M7 token + lastOperatorId from storage). M7 floor is config: `test_m7_thresholds_floor_is_config_not_code` (in the 26) |
| V2x.3 | same files `-k restart` | 0 | 3 passed, 54 deselected |
| V2x.4 | same files `-k rpc_budget` | 0 | 2 passed, 55 deselected; asserted M6 eth_call ≤ 3 × 350, M7 ≤ 3 + 2,352 + 350. Note: on a non-transport batch error `call_each` repeats the batch one by one (≤ 2× the bound; LIVE M6 run below used 5,103 = 3 × 1,701 calls for 2 pricings, i.e. one fallback occurred, 0 unknown) — bounded, recorded only |
| reorg | same files `-k reorg` | 0 | 12 passed, 45 deselected |
| V2x.5 | falsifiers (section 4) | — | 7/7 implementer falsifiers red→green; 6/8 QA falsifiers red, 2 survivors (F3) |
| V2x.6 | `… -m pytest -q reconcile/tests` | 0 | 297 passed in 10.52 s, 0 skipped |
| V2x.6 | `reconcile/.venv/bin/ruff check reconcile` | 0 | All checks passed! |
| config | `… -m pytest -q reconcile/tests/test_framework.py -k config_example` | 0 | 1 passed, 31 deselected |
| required settings | `test_m6_requires_views_and_the_api`, `test_m7_requires_cssv_and_unstake_history` (in V2x.1) | 0 | `SystemExit` at construction for missing `views`, `inventory.api_url` null/empty, `checks.m7.cssv`, `checks.m7.unstake_history_from`. An absent `inventory.api_url` key means the framework default API URL (eb_reconcile.py:152), not disabled — consistent |
| **V2e.7** (both sides) | `python -I v2e7_logside.py <repo> https://rpc.mevblocker.io https://eth-mainnet.public.blastapi.io <out> 26246821` (QA script, Appendix B) | 0 | **PASS at block 26,246,821**: 527,206 proxy cluster logs (all kinds except ClusterBalanceUpdated: ValidatorAdded 294,889, ValidatorRemoved 223,254, ClusterDeposited 6,037, ClusterLiquidated 1,368, ClusterWithdrawn 685, ClusterReactivated 551, ClusterMigratedToETH 422) from block 17,400,000 (before the proxy deployment) to the block → **1,908 clusters**, **1,026 legacy in storage**; SSV API v4: 1,908 rows, 1,026 `migrated=false`, all 1,026 legacy in storage. Log-legacy missing from API `migrated=false`: **0**; API `migrated=false` not in logs: **0**; API `migrated=false` not legacy in storage: **0**; API `migrated=true` but legacy in storage: **0**; log clusters absent from the API: 0. Logs: `rpc.mevblocker.io` (serves historical getLogs; windows split on its 10K-result cap, which it reports as an error, never truncates); storage: blastapi (archive). Output sha256 `2872611995dbd509…a613d`. This is a stronger check than the implementer's planned log side (since-activation activity only): it derives every cluster ever created |
| V2e.7 API side (implementer script) | `python live_legacy.py --rpc https://ethereum-rpc.publicnode.com --logs-rpc https://rpc.mevblocker.io --block 26246821 --logs-from 26246821 --cbu-blocks 1 --window 400000 --out <scratch>/live-m67.json` | 0 | 1,908 API rows; 1,026 `migrated=false`, all legacy in storage; contradictions 0; absent 0; inventory 1,026 valid / 0 invalid |
| LIVE M6 | same run (publicnode) | 0 | live `liquidation_threshold_period_ssv` 100,240, `minimum_liquidation_collateral_ssv` 0.385795451 SSV; 567 active legacy priced, 0 unknown, 459 liquidated; 346 validators; burn 3,239.28 SSV/yr; 0 liquidatable; 0 zero-balance burning; 422 inert at 0; **findings 0** — reproduces attempt 3 run 9. sha256 `03756da7…cee7e` |
| LIVE M7 (publicnode) | run above | — | NOT RUN: `RpcError: eth_getStorageAt: Archive requests require a personal token. Get one at: https://www.allnodes.com/publicnode` |
| LIVE M7 (archive) | `python live_legacy.py --rpc https://eth.drpc.org --logs-rpc https://rpc.mevblocker.io --block 26246821 --logs-from 26246821 --cbu-blocks 1 --window 400000 --out <scratch>/live-m7.json` | 0 | 2026-10-22T08:23Z–08:47Z. Ledger at 26,246,821: proxy SSV 4,471,105.813856436731202855; cSSV supply 4,413,503.637235544386919314; outstanding unstakes 46,579.978787956791529931 (181 requests / 157 withdrawals since 24,920,727); legacy balances 7,169.338396340962753610 (567 priced, 459 inactive); operator SSV earnings 3,732.10804556685 (lastOperatorId 2,352); network SSV earnings 119.75139102774; **surplus = 1.000000000000000000 SSV** exactly, unknown 0; findings: info baseline only, no page (`<` floor, spec "< 1 SSV"). eth_call 5,844 (= 2 × (3 + 2,352 + 567): the script prices the ledger twice), eth_getStorageAt 4, eth_getLogs 1 (unstake prefix via mevblocker). M6 in the same run: 567 priced, 0 unknown, findings 0. sha256 `8d60566e…726ca` |
| Subgraph cross-check | — | — | DEFERRED (plan; no key) |

## 4. Falsifiers (QA-applied; harness: apply one exact-string mutation, run the named test(s), `git checkout -- <file>`, re-run, `git status --porcelain`)

| ID | Mutation | Named test | Mutated | Restored | Porcelain |
|---|---|---|---|---|---|
| F6a | M6 `lag > warn_blocks` → `>=` | `test_m6_benign_liquidatable_exactly_half_window_stays_silent` | 1 failed | 1 passed | 0 |
| F6b | M6 drop `and vc > 0` | `test_m6_benign_zero_balance_without_validators_stays_silent` | 1 failed | 1 passed | 0 |
| F6c | M6 drop the `last_clear_block` reorg rewind | `test_m6_reorg_forgets_a_clear_observation_on_deleted_blocks` | 1 failed | 1 passed | 0 |
| F7a | M7 claims without `outstanding` | `test_m7_unstake_request_burns_cssv_and_is_counted_until_withdrawn` | 1 failed | 1 passed | 0 |
| F7b | M7 `surplus < floor` → `<=` | `test_m7_benign_surplus_exactly_one_ssv_does_not_page` | 1 failed | 1 passed | 0 |
| F7c | M7 `unknown = {}` after the ledger | `test_m7_unpriced_legacy_cluster_is_unknown_not_clean` | 1 failed | 1 passed | 0 |
| F7d | M7 completeness guard `if False:` | `test_m7_legacy_cluster_missing_from_the_inventory_is_unknown_not_a_smaller_liability` | 1 failed | 1 passed | 0 |
| QF6a | M6 `_fate` storage fallback always "no longer exists" | `test_m6.py` | 1 failed / 30 | 31 passed | 0 |
| QF6b | M6 drop the clamp after the last clear sweep | `test_m6.py` | 2 failed / 29 | 31 passed | 0 |
| QF6c | M6 event runs not scoped | `test_m6.py` | 1 failed / 30 | 31 passed | 0 |
| QF6d | M6 second loop enumerates only `rows` | `test_m6.py` | **31 passed (survives)** | 31 passed | 0 |
| QF7a | M7 every unstake log counted as a request | `test_m7.py` | 2 failed / 24 | 26 passed | 0 |
| QF7b | M7 drop the `surplus` re-report on UNKNOWN | `test_m7.py` | **26 passed (survives)** | 26 passed | 0 |
| QF7c | M7 guard only active legacy ids | `test_m7.py` | 26 passed (survives; arguably equivalent — liquidated clusters hold 0 SSV) | 26 passed | 0 |
| QF7d | M7 operator ids 0..n−1 | `test_m7.py` | 20 failed / 6 | 26 passed | 0 |

Each falsifier ran a single file or test (seconds); no long mutation campaign.

Targeted adversarial probes (scratch, Appendix A): 17 probes, **4 failed** (PR1a, PR1c → F2; PR2-M6, PR2-M7 → F1), 13 passed: PR1b observed start exact; PR4 warn → Telegram; PR5 reorg of the page block onto a shorter chain resolves, marker cleared, never re-paged; PR6 reorg deleting the `ValidatorAdded` 0→1 that paged resolves; PR9 same block add-validator + liquidate: no page; PR10 a liquidation resolution orphaned by a reorg re-opens the page on the new chain; PR2b empty API after a good run → UNKNOWN health (guard holds); PR3 A→B→A sends 3 infos on Discord; PR7 reorg deleting an `UnstakedWithdrawn` while the SSV stays gone pages; PR8 M7 reorg run with a reverting `balanceOf` raises, marker and page kept; PR11 page resolves at exactly 1 SSV; PR12 M7 UNKNOWN reorg run keeps both keys, no second baseline; PR13 M6 UNKNOWN reorg run keeps the page.

## 5. Evidence-plane writes

- Status transition: CP2e `IMPLEMENTED — AWAITING QA` → **`QA FAILED`** on `c02557a` with this report (plan.md §8 CP2e Status line).
- Paths written: `plans/monitoring-service/qa/cp2e-attempt-1.md` (this report), the CP2e `**Status:**` line in `plans/monitoring-service/plan.md`, one appended event in `plans/monitoring-service/execution-log.md`. Untracked/ignored: `reconcile/.venv`. Scratch (outside the repo): `/private/tmp/claude-501/-Users-marco-orca-workspaces-ssv-network-monitor-ms-qa-cp2e/5ac05e58-518b-45cc-967c-efd846817692/scratchpad/` (`probes/`, `tools/falsify.py`, `tools/v2e7_logside.py`, `v2e7-qa.json`, `live-m67.json`, `live-m7.json`).
- No implementation repair, no config/`config.zip`/`.env*` access, no mainnet transaction, no alert sent (record mode in tests), no secret used.

## 6. Repo proof

Recorded at report time (before the commit; the report itself is the untracked file):

```
$ git status --porcelain
 M plans/monitoring-service/execution-log.md
 M plans/monitoring-service/plan.md
?? plans/monitoring-service/qa/cp2e-attempt-1.md
$ git diff --name-only c02557a
plans/monitoring-service/execution-log.md
plans/monitoring-service/plan.md
```

No implementation file (`reconcile/**`, `deploy/**`, config, templates) appears in either list; every falsifier ended with
`git status --porcelain` at 0 lines. `reconcile/.venv` is ignored and untracked.

## Appendix A — probe reproducers (scratch; `conftest.py` puts `$QA_ROOT/reconcile` and `$QA_ROOT/reconcile/tests` on `sys.path`)

Run: `QA_ROOT=$PWD reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider --rootdir <probes> <probes>`.

```python
# conftest.py
import os, sys
ROOT = os.environ["QA_ROOT"]
sys.path.insert(0, os.path.join(ROOT, "reconcile", "tests"))
sys.path.insert(0, os.path.join(ROOT, "reconcile"))
```

```python
# test_probe_m6.py (failing probes; the passing ones follow the same pattern)
from fixtures_legacy import BURN, PERIOD_SSV, SSV, LegacyWorld, owner
from harness import advance, outbox

W = PERIOD_SSV * 5000 // 10_000
T = PERIOD_SSV * BURN  # isLiquidatable: balance < period * burn (ClusterLib, strict <)

def world(tmp_path, **kw):
    return LegacyWorld(tmp_path, only=("m6",), **kw)

def row(w):
    return dict(w.db.conn.execute("SELECT * FROM m6_clusters").fetchone())

def test_pr1a_backdated_start_of_a_cluster_that_just_crossed(tmp_path):   # F2
    lw = world(tmp_path); a = lw.legacy(owner(1))
    lw.views(a, balance=T - BURN, liquidatable=True); lw.chain.mine()
    w = lw.start(); advance(w)
    assert row(w)["liquidatable_since"] == w.checkpoint()["block"]        # actual: block - 1, estimated

def test_pr1c_true_duration_exactly_w_backdated_stays_silent(tmp_path):    # F2
    lw = world(tmp_path); a = lw.legacy(owner(1))
    lw.views(a, balance=T - (W + 1) * BURN, liquidatable=True); lw.chain.mine()
    w = lw.start(); advance(w)
    assert outbox(w) == []                                                # actual: one warn

def test_pr2_m6_first_sweep_with_an_empty_api_listing(tmp_path):         # F1
    lw = world(tmp_path); a = lw.legacy(owner(1))
    lw.views(a, balance=0, liquidatable=True); lw.chain.mine()
    w = lw.start()
    w.inventory.http_get = lambda url: {"clusters": [], "pagination": {"pages": 1, "total": 1}}
    advance(w)
    assert any(r["severity"] in ("page", "health") for r in outbox(w))   # actual: outbox empty
```

```python
# test_probe_m7.py (failing probe)
from fixtures_legacy import CSSV, SSV, LegacyWorld, owner
from harness import advance, outbox

CLUSTER, OPS, NETWORK, STAKED = 100 * SSV, {1: 5 * SSV, 2: 3 * SSV}, 2 * SSV, 1_000 * SSV
BALANCED = STAKED + CLUSTER + sum(OPS.values()) + NETWORK + SSV

def test_pr2_m7_first_run_with_an_empty_api_listing(tmp_path):           # F1
    lw = LegacyWorld(tmp_path, only=("m7",))
    lw.checks = {"m7": {"cssv": CSSV, "unstake_history_from": lw.chain.sealed}}
    a = lw.legacy(owner(1), count=2); lw.views(a, balance=CLUSTER)
    lw.ledger(proxy_ssv=BALANCED - 50 * SSV, cssv_supply=STAKED, network=NETWORK, operators=OPS)
    lw.chain.mine(); lw.start()
    lw.w.inventory.http_get = lambda url: {"clusters": [], "pagination": {"pages": 1, "total": 1}}
    advance(lw.w)
    keys = {r["key"] for r in lw.w.db.conn.execute("SELECT key FROM incidents WHERE state='open'")}
    assert "surplus-floor" in keys or any(r["severity"] == "health" for r in outbox(lw.w))
    # actual: only ('info','open') baseline with surplus 51 SSV, legacy_clusters_priced 0
```

## Appendix B — V2e.7 QA script (scratch `tools/v2e7_logside.py`, summary)

Reads every proxy log with topic0 in {ValidatorAdded, ValidatorRemoved, ClusterLiquidated, ClusterReactivated, ClusterDeposited, ClusterWithdrawn, ClusterMigratedToETH} from 17,400,000 to the block in 250,000-block windows (a window that errors with a result/size cap or returns ≥ 5,000 logs is halved; a single block is split by topic), decodes cluster ids with `eb_events.decode_log`, reads `ethClusters[id]`/`clusters[id]` at the block (`eth_getStorageAt`, the inventory's version rule), fetches the SSV API v4 with `inventory.fetch_api_clusters`, and compares the log-derived legacy set with the API `migrated=false` set in both directions.
