FAIL

# CP2e (Legacy SSV: M6, M7) — independent QA, attempt 2

- QA: Claude Opus 5.5 (`claude-opus-5-5`), effort high (D13; attempt-2 addendum). Task `task_391141be8e2b`, dispatch `ctx_b95e2f8c7ac2`. Observed 2026-10-22T09:2xZ–09:5xZ (host clock, UTC).
- Frozen head under test: `6fafac6127498aeaf830c31994367b4dd1ee9d82` (lane `mtabasco/ms-cp2e`, repair 1 of QA attempt 1 on `c02557a`). Worktree `ms-qa-cp2e-2`, branch `mtabasco/ms-qa-cp2e-2`, `git rev-parse HEAD` = frozen head at start, porcelain 0 lines.
- Packet: `qa/QA-CONTRACT.md` at the frozen head; `qa/DEFECT-CLASSES.md` identical to `plan/monitoring-service` (`diff` empty). Owner scope note applied (no mutation campaign, no long rows; skips named below).
- Classification: the dispatch says every finding blocks (one failed round). The coordinator's follow-up `msg_f89e81cab2f9` (09:32Z, "QA: classify with this rule") and `plan/monitoring-service` (`dd17e6d`) now carry **D15** (owner, 2026-10-22: BLOCKING = missed/lost page, false clean, false page, unsafe action, data loss, fail-open gate, secret leak; a false/duplicated warn, text or transient alert is a NOTE). I applied D15 to classify; the one BLOCKING finding is a false clean (missed page) under both rules; the NOTEs below are outside D15's BLOCKING kinds or outside the lane's trust model (reason given per item).
- Preflight: disk 336 GiB free; venv created per plan §7 (`uv venv reconcile/.venv --python 3.12`; `uv pip install --python reconcile/.venv/bin/python -r reconcile/requirements-dev.txt`, exit 0), Python 3.12.15, `reconcile/.venv` git-ignored; anvil not needed (no fork row in CP2e).
- Frozen-file check: `git diff HEAD plan/monitoring-service -- reconcile/tests/{harness,test_framework,test_worker}.py` → 0 lines each (equal to `500abe4` too); `git diff 96d1c32 HEAD -- <the 3 files>` is byte-identical (`cmp`) to `git diff bd531b4^ 500abe4 -- <the 3 files>` (46 lines). `git diff --name-status 96d1c32 HEAD -- reconcile/`: only `checks/__init__.py`, `config.example.json` (M) and the lane's new files (`checks/m6_legacy_coverage.py`, `checks/m7_ssv_solvency.py`, `tests/fixtures_legacy.py`, `tests/test_m6.py`, `tests/test_m7.py`) besides the 3 patched files. Since `c02557a` only the two checks, the fixture and the two test files changed in `reconcile/`; framework files (`inventory.py`, `eb_events.py`, `eb_storage.py`, `rpc.py`, `eb_reconcile.py`) have an empty diff. No other frozen CP1B file edited.

## Spec trigger list (re-derived from docs/ssv-specs/monitoring-service-spec.md §M6, §M7 + rulings Q1 A / Q2 A; spec unchanged since attempt 1)

| # | Trigger (spec line) | Severity | Firing test | Silent twin |
|---|---|---|---|---|
| M6.1 | `isLiquidatableSSV` true > 7 d (ruling: > 5000 bps × live `liquidation_threshold_period_ssv` blocks of continuous liquidatability) | warn | `test_m6_liquidatable_longer_than_half_window_warns` | `test_m6_benign_liquidatable_exactly_half_window_stays_silent` (now true duration exactly W) |
| M6.2 | `getBalanceSSV == 0` while active (ruling: and vc > 0; 0→>0 at balance 0 pages at its block) | page | `test_m6_zero_balance_while_active_pages`, `test_m6_validator_count_zero_to_positive_at_zero_balance_pages_on_that_block` | `test_m6_benign_zero_balance_without_validators_stays_silent`, `test_m6_benign_positive_balance_not_liquidatable_stays_silent` |
| M6.3 | weekly digest: count, validators, SSV/yr burn (+ ruling: vc = 0 zero-balance clusters only here) | digest | `test_m6_weekly_digest_counts_clusters_validators_and_burn` | same test (no M6 section on the non-weekly digests) |
| M7.1 | daily `surplus < 1 SSV` | page | `test_m7_surplus_below_one_ssv_pages` | `test_m7_benign_surplus_exactly_one_ssv_does_not_page` |
| M7.2 | any change in the surplus | info | `test_m7_surplus_change_sends_info_under_one_incident` | `test_m7_benign_unchanged_surplus_sends_nothing` |

Each named test exists exactly once at the head (`grep -c "def <name>\b"`). Pinned semantics re-checked at `ssv-network@f7ac487`: `ClusterLib.isLiquidatable` = vc ≠ 0 and (`balance < minimumLiquidationCollateral` or `balance < period × (burn + networkFee) × vc`), strict; `deposit`/`withdraw` are `VERSION_ETH`-only, so a legacy balance only drains (the back-date model holds); `StorageProtocol` word 0 = `uint32 networkFeeIndexBlockNumber | uint32 daoValidatorCount | …`, decremented by `updateDAOSSV` on `liquidateSSV`, legacy `removeValidator` and `migrateClusterToETH` of an active cluster.

## 1. Findings (ranked)

### F1 (BLOCKING — false clean / missed page; D15 and round policy) — the repaired completeness guard still accepts a self-consistent empty SSV API answer (`clusters: []`, `pagination.total: 0`) whenever `daoValidatorCount` is 0: M7 records a clean baseline without the legacy balances

Defect classes 1 and 10 (empty input read as clean; first run without a baseline). This is attempt-1 F1 at a narrower precondition. `inventory_gap` has two legs: (1) chain — Σ validators of the listed active legacy clusters == `daoValidatorCount`; (2) API — `len(inv.clusters) >= pagination.total`. An empty page that also reports `total: 0` passes leg (2) (0 ≥ 0), and leg (1) passes whenever no listed **and no real** active legacy cluster has validators (0 == 0). The repair's design row says leg (2) "covers the inert legacy clusters whose balance is still an M7 claim"; for an empty-and-consistent answer it does not.

- Reproducer `test_q10_consistent_empty_api_with_inert_legacy_balance` (Appendix A): one legacy cluster with 0 validators (inert) holding 100 SSV, proxy 50 SSV short (true surplus −49 SSV); the fake API (`api_fault="empty"`, `api_total=0`) answers `{"clusters": [], "pagination": {"pages": 1, "total": 0}}` at the first run. Expected: `surplus-floor` page or UNKNOWN health, no baseline. Actual: `AssertionError: (['surplus'], '51000000000000000000', 0)` — only the info baseline `surplus = 51 SSV`, `legacy_clusters_priced = 0`, no page, no health. `m7_legacy` stays empty, so as long as the API keeps answering that way every daily run repeats the false clean (the attempt-1 regression `test_m7_first_run_with_an_empty_api_listing_is_unknown_not_a_baseline` uses `total: 1`, which is why it passes).
- Command: `QA_ROOT=$PWD reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider --rootdir <probes2> <probes2>/test_qa2_m7.py::test_q10_consistent_empty_api_with_inert_legacy_balance` → 1 failed.
- Exposure: not reachable on mainnet today — at block 26,247,266 `daoValidatorCount` = 346 (independent `cast storage`, below), so an empty page is a gap through leg (1). It becomes reachable once no active legacy cluster has validators while inert legacy clusters still hold SSV, which is the natural end state of a surface that "only shrinks": a legacy cluster cannot `withdraw` (ETH-only at the pin), so inert balances stay until migration. Then any first run or new database (the deployment's data dir is not yet durable) meeting an API that answers 200-empty-with-total-0 (re-index, wrong network path) masks every legacy SSV claim. M6 is not affected in the page sense (inert clusters never page; with dao 0 there is no burning cluster), but its weekly digest would report "0 active" without the UNKNOWN line.
- Why BLOCKING and not NOTE: the SSV API is outside the lane's trust model by its own rule ("absence from the SSV API is not evidence", module docstring) and an empty page is exactly the input F1 was about; the repair claims "empty/short API … now UNKNOWN" (plan.md status line), which this refutes. Owner: lane (repair worker). An empty listing or a total of 0 next to a proxy that has legacy clusters is a recognisable degenerate answer.

## 2. Claims I could NOT confirm

- "F1 incomplete legacy inventory (empty/short API, lost database) now UNKNOWN" (plan.md status line; worker record design row "Completeness (F1)"): **refuted** for an empty page that reports `pagination.total: 0` while `daoValidatorCount` is 0 (F1 above). Empty/short pages with the true total, a missing/unreadable total, and a cluster missing from both API and total with validators are UNKNOWN as claimed (regressions red on the frozen checks, green now; my Q1, Q2, Q13 probes pass).
- State-table cell "eth_getLogs prefix (a) empty: an endpoint that answers `[]` for a range holding logs is indistinguishable from 'no unstakes'": **partly refuted** — when the scanned range holds a withdrawal whose request sat in the lost prefix, the ledger goes negative (N1), a distinguishable impossible state.
- Not re-run (carried from attempt 1, reason: no code on their path changed and the owner scope excludes long rows): V2e.7 both sides (attempt 1 PASS at 26,246,821: 1,908 clusters, 1,026 legacy = API 1,026 `migrated=false`, 0 differences; ~25 min archive log scan); LIVE M7 surplus (attempt 1: exactly 1.000000000000000000 SSV at 26,246,821). The repair changes no inventory/V2e.7 logic (`git diff c02557a HEAD -- reconcile/inventory.py reconcile/eb_events.py reconcile/rpc.py reconcile/eb_reconcile.py reconcile/eb_storage.py` empty); the new LIVE-facing logic (the guard) is re-measured below.
- Subgraph cross-check: DEFERRED by the plan (no `SSV_SUBGRAPH_API_KEY`).
- Everything else the repair claims reproduced: 424 passed, ruff clean, V2x.1 92/92, V2x.2 3, V2x.3 5, V2x.4 2, reorg 18, sweep 104; 57 red on the frozen checks (21 named regressions + M6 sweep 28/48 + M7 sweep 8/56, all assertion failures); attempt-1 probes 17/17 green (4 were red on `c02557a`); falsifiers red→green.

## 3. Verification matrix (each row run on its own; exit code captured per command, no `&&` credit; `P = reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider`)

| V-ID | Command | Exit | Result |
|---|---|---|---|
| V2x.1 M6 | `$P reconcile/tests/test_m6.py` | 0 | 92 passed (44 + 48 sweep); trigger map above — every trigger has a firing test at its severity and a silent twin |
| V2x.1 M7 | `$P reconcile/tests/test_m7.py` | 0 | 92 passed (36 + 56 sweep) — but F1: the first-run guard misses an empty page with total 0 |
| V2x.2 | `$P reconcile/tests/test_m6.py reconcile/tests/test_m7.py -k thresholds_from_chain` | 0 | 3 passed, 181 deselected |
| V2x.3 | same files `-k restart` | 0 | 5 passed, 179 deselected |
| V2x.4 | same files `-k rpc_budget` | 0 | 2 passed, 182 deselected (M6 eth_call ≤ 3 per cluster; M7 ≤ 3 + lastOperatorId + legacy) |
| reorg | same files `-k reorg` | 0 | 18 passed, 166 deselected |
| sweep | same files `-k adversarial` | 0 | 104 passed, 80 deselected |
| V2x.5 | falsifiers, section 4 | — | 9/9 matrix/attempt-1 falsifiers red→green; 7/7 QA-2 falsifiers red→green; porcelain 0 after each |
| V2x.6 | `$P reconcile/tests` | 0 | **424 passed** in 10.74 s, 0 skipped |
| V2x.6 | `reconcile/.venv/bin/ruff check reconcile` | 0 | All checks passed! |
| config | `$P reconcile/tests/test_framework.py -k config_example` | 0 | 1 passed, 31 deselected |
| required settings | `test_m6_requires_views_and_the_api`, `test_m7_requires_cssv_and_unstake_history` (inside V2x.1) | 0 | missing/empty `views`, `inventory.api_url`, `checks.m7.cssv`, `checks.m7.unstake_history_from` → `SystemExit` at construction. A malformed present value passes construction (N2) |
| frozen regressions | scratch copy: `git archive HEAD reconcile` + `reconcile/checks/m{6,7}_*.py` from `c02557a`; `$P reconcile/tests/test_m6.py reconcile/tests/test_m7.py` | 1 | **57 failed, 127 passed**: the 21 named regressions (F1 12, F2 4 incl. the two corrected lane tests, m6_obs restore 2, QF6d 1, …) + M6 sweep 28/48 + M7 sweep 8/56; failure lines are assertions (`assert (1001 is None or -99227 > 1001)`, `assert (-99117 == (1001 + 1))`, `{'surplus': …} == {}`, …), none an import/collection error. `test_m6_reorg_run_reports_an_open_key_without_a_persisted_row` fails there on the missing `m6_obs` table (it now also deletes the history), consistent with the record |
| attempt-1 reproducers | `QA_ROOT=$PWD $P --rootdir <probes1> <probes1>` (Appendix A of attempt 1, verbatim) | 0 | **17 passed** (PR1a, PR1c, PR2-M6, PR2-M7 were red on `c02557a`) |
| QA-2 probes | `QA_ROOT=$PWD $P --rootdir <probes2> <probes2>` | 1 | 14 probes: **11 passed, 3 failed**: Q10 (F1), Q11 (N1), Q15 (N2); see section 4 |
| LIVE guard (re-measured) | `reconcile/.venv/bin/python plans/monitoring-service/workers/cp2e/live_completeness.py --rpc https://ethereum-rpc.publicnode.com --out <scratch>/live-completeness.json` (2026-10-22T09:31:07Z–09:36:33Z) | 0 | **PASS at block 26,247,259**: production `inventory_gap` = None through a real InventoryCache/Ctx: 1,908 clusters (882 ETH, 1,026 legacy, 567 legacy active, 0 invalid, all from the API); `daoValidatorCount` 346 = listed legacy validators 346; `pagination.total` 1,908 = listing 1,908; controls on the same block: empty page → gap (0 vs 346; 0 of 1,908), one burning legacy row dropped → gap (306 vs 346; 1,907 of 1,908). Endpoint publicnode (recent block, no archive read needed). sha256 `f12f8a02613ab981…ff46a657d`. Reproduces the implementer's 26,247,163 evidence. (The controls keep the true total, so they do not exercise F1's total-0 case.) |
| LIVE guard (independent) | `cast block-number` (publicnode) − 12 = 26,247,266; `cast storage 0xDD9B…4E1 0xf1d8…aa15 --block 26247266 --rpc-url https://ethereum-rpc.publicnode.com`; `curl https://api.ssv.network/api/v4/mainnet/clusters?perPage=1&page=1` | 0 | word0 `0x…015a016bc530` → `daoValidatorCount` **346**, `networkFeeIndexBlockNumber` 23,840,048 (slot = keccak("ssv.network.storage.protocol") − 1, from `cast keccak`); API `pagination` `{'total': 1908, 'pages': 1908, 'page': 1, 'per_page': 1}` — the `perPage=1` total read the guard relies on works on the real API |
| V2e.7 | — | — | NOT RE-RUN (carried from attempt 1, PASS both sides at 26,246,821; see section 2) |
| LIVE M6/M7 ledger | — | — | NOT RE-RUN (attempt 1 at 26,246,821; see section 2) |
| Subgraph cross-check | — | — | DEFERRED (plan; no key) |

## 4. Falsifiers and probes

Falsifiers (scratch `tools/falsify.py`: one exact-string mutation → named test(s) → `git checkout -- <file>` → rerun → `git status --porcelain`); every row ended with porcelain 0 lines.

| ID | Mutation | Named test(s) | Mutated | Restored |
|---|---|---|---|---|
| F6a | M6 `lag > warn_blocks` → `>=` | `test_m6_benign_liquidatable_exactly_half_window_stays_silent` | 1 failed | 1 passed |
| F6b | M6 drop `and vc > 0` | `test_m6_benign_zero_balance_without_validators_stays_silent` | 1 failed | 1 passed |
| F6c | M6 `self._rewind(db, ancestor)` → `pass` | `test_m6_reorg_forgets_a_clear_observation_on_deleted_blocks` | 1 failed | 1 passed |
| F7a | M7 claims without `outstanding` | `test_m7_unstake_request_burns_cssv_and_is_counted_until_withdrawn` | 1 failed | 1 passed |
| F7b | M7 `surplus < floor` → `<=` | `test_m7_benign_surplus_exactly_one_ssv_does_not_page` | 1 failed | 1 passed |
| F7c | M7 `unknown = {}` after the ledger | `test_m7_unpriced_legacy_cluster_is_unknown_not_clean` | 1 failed | 1 passed |
| F7d | M7 `if gone:` → `if False:` | `test_m7_legacy_cluster_missing_from_the_inventory_is_unknown_not_a_smaller_liability` | 1 failed | 1 passed |
| QF6d (att. 1 survivor) | M6 second loop enumerates only `rows` | `test_m6.py` | 1 failed / 92 | 92 passed |
| QF7b (att. 1 survivor) | M7 drop the `surplus` re-report on UNKNOWN | `test_m7.py` | 41 failed / 92 | 92 passed |
| QA2-a | M6 back-date reverts to attempt-1 formula (`- 1` removed) | `test_m6.py` | 4 failed / 92 | 92 passed |
| QA2-b | guard: `elif len(inv.clusters) < total:` → `elif False:` | `test_m6.py test_m7.py` | 3 failed / 184 | 184 passed |
| QA2-c | guard: `if listed_vc != dao:` → `if False:` | `test_m6.py test_m7.py` | 3 failed / 184 | 184 passed |
| QA2-d | M7 `if gap:` → `if False:` | `test_m7.py` | 15 failed / 92 | 92 passed |
| QA2-e | M7 page only when nothing is unknown (`and not unknown`) | `test_m7.py` | 2 failed / 92 | 92 passed |
| QA2-f | M6 `m6_obs` pruned to the newest row (`horizon = block + 1`) | `test_m6.py` | 21 failed / 92 | 92 passed |
| QA2-g | M7 storage never says "still legacy" for a vanished id | `test_m7.py` | 1 failed / 92 | 92 passed |

QA-2 targeted probes (scratch `probes2/`, Appendix A; written before reading the lane's new tests; classes from DEFECT-CLASSES):

| Probe | Class / key attacked | Result |
|---|---|---|
| Q1 consistent empty API (total 0), burning zero-balance cluster, first sweep (M6) | 1, 10 / inventory | pass — gap via `daoValidatorCount` (1 ≠ 0), UNKNOWN health |
| Q2 duplicate API rows fill `pagination.total`, inert cluster missing (M6) | 1 / inventory | pass — unique 1 < total 2 → gap |
| Q13 same for M7 with the missing inert cluster holding 50 SSV | 1 / inventory, ledger | pass — UNKNOWN, no baseline |
| **Q10** consistent empty API (total 0), only an inert legacy cluster (dao 0) holding 100 SSV, first run (M7) | 1, 10 / inventory, baseline | **FAIL → F1** |
| Q3a/Q3b back-date under a minimum-collateral threshold: deficit (W+1)·burn → since = block − W, silent; one wei more → since = block − (W+1), warn | 4 / `m6_clusters.liquidatable_since` | pass (exact boundary, both paths) |
| Q3c observed start after a clear sweep: since = clear + 1, not estimated; lag exactly W silent, W + 1 warns | 4 / window anchoring | pass |
| Q4 page resolved by `ClusterMigratedToETH` (event run), reorg orphans the migration → page re-opens | 2 / resolved key | pass |
| Q18 inert zero-balance cluster re-validates (0→1 pages at its block), removes it (resolved), reorg orphans the removal → page re-opens | 2, 3 / resolved key, event run | pass |
| Q5 warn, restart, two sweeps: one warn opened | 10 / incidents | pass |
| Q6 `daoValidatorCount` storage read fails (`'0x'`) at the first sweep → run errors, no clean summary | 1 / guard input | pass |
| **Q11** prefix `eth_getLogs` answers `[]` for a range holding a 40 SSV request; its withdrawal is in the scanned range; proxy 30 SSV short | 1 / `unstake_prefix`, ledger | **FAIL → N1** (`outstanding_unstake = -40 SSV`, surplus +11 SSV, true −29 SSV) |
| Q15 `unstake_history_from: "soon"` at construction | 11 / settings | FAIL → N2 (no `SystemExit`); Q15b: first run → `check-error` health `ValueError: invalid literal for int() … 'soon'` (loud, not inert) |

## 5. NOTEs (recorded, not blocking; owner and reason)

- **N1 (NOTE; owner lane / CP3)** — M7 accepts an impossible negative `outstanding_unstake`. Reproducer Q11 above (`AssertionError: (['surplus'], '-40000000000000000000', '11000000000000000000')`). The prefix is also persisted once (`check:m7:unstake_prefix`), so one lossy answer is permanent. NOTE, not BLOCKING, because: the shipped config has `start_block == unstake_history_from == 24,920,727`, so the prefix is never read; the precondition is an RPC endpoint returning a successful-but-incomplete `eth_getLogs`, the same endpoint-honesty trust the CP1B (VERIFIED) scan relies on for every check; and it would only catch the subset where withdrawals exceed recorded requests. A cheap hardening: outstanding < 0, or a withdrawal by an address with no recorded request, → UNKNOWN.
- **N2 (NOTE; owner lane / CP3)** — a present but malformed required setting (`checks.m7.unstake_history_from: "soon"`; similarly a non-numeric `surplus_floor_wei`) passes construction and fails on the first run as a `check-error` health message each step (Q15b) — loud, not inert, so outside D15 "fail-open". Validating the type at construction would match the rule "required settings fail loud at construction".

## 6. Evidence-plane writes

- Status transition: CP2e `IMPLEMENTED — AWAITING QA` → **`QA FAILED`** on `6fafac6` with this report (plan.md §8 CP2e Status line).
- Paths written: `plans/monitoring-service/qa/cp2e-attempt-2.md` (this report), the CP2e `**Status:**` line in `plans/monitoring-service/plan.md`, one appended event in `plans/monitoring-service/execution-log.md`. Untracked/ignored: `reconcile/.venv`. Scratch (outside the repo): `/private/tmp/claude-501/-Users-marco-orca-workspaces-ssv-network-monitor-ms-qa-cp2e-2/70d39973-d7cd-48dc-ab4f-c2b6922ccdaa/scratchpad/` (`probes1/` attempt-1 probes, `probes2/` QA-2 probes, `frozen/` regression copy, `tools/falsify.py`, `fals.py`/`fals.json`/`fals.out`, `frozen-run.log`, `live-completeness.json`).
- No implementation repair, no `config/`/`config.zip`/`.env*` access, no mainnet transaction, no alert sent (record mode in tests), no secret used. Skipped by the owner scope note: mutation campaign, V2e.7 re-run, LIVE ledger re-run.

## 7. Repo proof

Recorded at report time (before the commit; the report itself is the untracked file):

```
$ git status --porcelain
 M plans/monitoring-service/execution-log.md
 M plans/monitoring-service/plan.md
?? plans/monitoring-service/qa/cp2e-attempt-2.md
$ git diff --name-only 6fafac6
plans/monitoring-service/execution-log.md
plans/monitoring-service/plan.md
```

No implementation file (`reconcile/**`, `deploy/**`, config, templates) appears in either list; every falsifier ended with `git status --porcelain` at 0 lines. `reconcile/.venv` is ignored and untracked. Q10 and Q11 also fail on the frozen `c02557a` checks (scratch copy), so both are pre-existing and not regressions of repair 1.

## Appendix A — QA-2 probe reproducers (scratch `probes2/`; `conftest.py` as in attempt 1: puts `$QA_ROOT/reconcile` and `$QA_ROOT/reconcile/tests` on `sys.path`)

Run: `QA_ROOT=$PWD reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider --rootdir <probes2> <probes2>`.

```python
# test_qa2_m7.py (the failing probes)
import json
from fixtures_legacy import CSSV, SSV, LegacyWorld, owner
from harness import advance, outbox

CLUSTER, OPS, NETWORK, STAKED = 100 * SSV, {1: 5 * SSV, 2: 3 * SSV}, 2 * SSV, 1_000 * SSV
BALANCED = STAKED + CLUSTER + sum(OPS.values()) + NETWORK + SSV
USER = "0x" + "ab" * 20

def incidents(w):
    return {r["key"]: dict(r) for r in w.db.conn.execute("SELECT * FROM incidents WHERE check_id='m7' AND state='open'")}

def health(w):
    return [r["text"] for r in outbox(w, "severity='health'")]

def test_q10_consistent_empty_api_with_inert_legacy_balance(tmp_path):        # F1
    lw = LegacyWorld(tmp_path, only=("m7",))
    lw.checks = {"m7": {"cssv": CSSV, "unstake_history_from": lw.chain.sealed}}
    a = lw.legacy(owner(1), count=0)                    # inert: daoValidatorCount stays 0
    lw.views(a, balance=CLUSTER)
    lw.ledger(proxy_ssv=BALANCED - 50 * SSV, cssv_supply=STAKED, network=NETWORK, operators=OPS)
    lw.chain.mine(); lw.start()
    lw.api_fault, lw.api_total = "empty", 0            # {"clusters": [], "pagination": {"pages": 1, "total": 0}}
    advance(lw.w)
    assert "surplus-floor" in incidents(lw.w) or any("UNKNOWN" in h for h in health(lw.w))
    # actual: only the 'surplus' info baseline, surplus 51 SSV, legacy_clusters_priced 0

def test_q11_empty_prefix_logs_negative_outstanding(tmp_path):                # N1
    lw = LegacyWorld(tmp_path, only=("m7",))
    frm = lw.chain.sealed
    a = lw.legacy(owner(1), count=2); lw.views(a, balance=CLUSTER); lw.chain.mine()
    lw.unstake(USER, 40 * SSV)                          # before the worker's start block
    lw.chain.mine(3)
    lw.ledger(proxy_ssv=BALANCED - 30 * SSV, cssv_supply=STAKED, network=NETWORK, operators=OPS)
    lw.chain.mine()
    lw.checks = {"m7": {"cssv": CSSV, "unstake_history_from": frm}}
    w = lw.start(); start = w.cfg.start_block; orig = w.rpc.get_logs
    w.rpc.get_logs = lambda address, lo, hi, step, topics: [] if hi < start else orig(address, lo, hi, step, topics)
    lw.unstake(USER, 40 * SSV, withdraw=True); lw.chain.mine()
    advance(w)
    assert "surplus-floor" in incidents(w) or any("UNKNOWN" in h for h in health(w))
    # actual: outstanding_unstake -40 SSV, surplus 11 SSV (true -29 SSV), no page, no health
```

The passing probes (Q1–Q6, Q13, Q18, Q15b) follow the same pattern with `fixtures_legacy.LegacyWorld` (`api_fault`, `api_total`, `rows`, `ssv.migrate`, `ssv.remove`, `add_validators`, `fork`, `restart`); full sources in scratch `probes2/test_qa2_m6.py`, `probes2/test_qa2_m7.py`.
