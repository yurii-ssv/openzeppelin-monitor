FAIL

## 1. Findings

Independent QA of CP2a attempt 1, frozen head `7211819c1b0f5ebd612c35dd45cc9f4549554c59`, branch `mtabasco/ms-qa-cp2a`, 2026-10-21. Assignment `task_15e1c0e3a918`, dispatch `ctx_31ca1746c190`; Codex GPT-6.1-Sol, high, workspace-write with automatic approval review. No implementation repair was made.

Reproducer convention: run from the worktree root; `P=reconcile/.venv/bin/python` and `S=/private/tmp/cp2a-qa-7211819`. Appendix A preserves the exact independent test file, so the report remains reproducible after scratch is removed. Each command below was run against the frozen worktree implementation, not a mutant. The final independent run exited **1: 7 failed, 28 passed** (35 cases; the seven reds represent six distinct findings). Three harmless unknown-marker warnings occur because this external test file does not inherit the repo's conftest marker registration.

### H1 — M3 resolves a live computed-zero page when the API stops listing its cluster

- Reproducer: `$P -m pytest -q "$S/test_independent.py" -k empty_api` (the cases were also run in the complete 35-case run and the seven-case finding selection).
- Begin after the registration log, obtain the existing cluster through the API baseline, observe active computed balance zero, and open a page. Return a valid API response with `clusters: []`, advance the full-sweep clock by 600 seconds, and sweep again. Test both without a reorg and with a reorg of later blocks that leaves the original cluster and its storage intact.
- Expected: the missing inventory is UNKNOWN; retain/re-evaluate the page from persisted cluster identity or raise, with no clean resolution. `getNetworkValidatorsCount()` still proves one validator exists.
- Actual: both cases fail; incident `m3-228316cb1eca4dea-1003` becomes resolved and sends `('resolved', 'info')`. The check labels an inventory omission as “not in storage” without reading storage for the persisted identity; an inventory-gap health message does not prevent the resolution. This loses a real computed-zero page and its re-alerts, including during a reorg.
- Location: `reconcile/checks/m3_liquidation_zone.py:138` and `:215` (missing `ClusterInfo` is treated as absent).

### H2 — M14 commits a clean reorg resolution when canonical replacement evidence is UNKNOWN, and never retries it

- Reproducer: `$P -m pytest -q "$S/test_independent.py::test_m14_reorg_missing_transaction_does_not_clean_resolve"`; exit 1, one failure.
- Open a warn incident, replace its trigger block, re-include a genuine dust-deposit/failed-liquidation pair on the replacement chain, and run scan/inbox ingestion normally. Only for the check's reorg evaluation, make the replacement transaction lookup return `None`; then restore the lookup and sweep again after 600 seconds.
- Expected: UNKNOWN re-evaluation must preserve the key or raise; keep the durable reorg marker/cursor pending until the canonical replacement evidence can be evaluated (README §6.2). A successful retry should keep/re-find the incident.
- Actual: `m14-unknown` health is emitted, but the old hit was deleted and the check returns `resolve=True` for its open key. The warn resolves, `reorg:m14` is cleared, the replacement inbox entry is consumed, and restoring the transaction plus another sweep does not recover the hit. Measured messages: `[('open','warn'), ('resolved','info')]`; no open incident, marker `None`.
- Location: `reconcile/checks/m14_deposit_frontrun.py:134`–`:168`. A health message alongside an unevaluable reorg is not the required re-evaluation or retry.

### H3 — M14 cannot correlate a genuine `liquidateSSV` attack on a legacy SSV cluster

- Reproducer: `$P -m pytest -q "$S/test_independent.py::test_m14_real_legacy_ssv_liquidation_is_supported"`; exit 1, one failure.
- Create a legacy SSV cluster with `MiniSSV.legacy_ssv_cluster`, a real `ClusterDeposited` log and validated SSV storage tuple, a one-unit deposit below its SSV burn, and a reverted `liquidateSSV`. The strict fake exposes `isLiquidatableSSV`, `getBurnRateSSV`, and `getBalanceSSV`; ETH views fail against legacy storage. Assert the inventory itself reports `SSV` and `valid` before the expected alert assertion.
- Expected: info correlation (and appropriate SSV computed-zero handling for escalation).
- Actual: no hit or incident; only `m14-unknown`, because the check unconditionally invokes ETH view selectors. Pinned source and ABI expose separate SSV view variants; ETH variants enforce `VERSION_ETH`. The supplied “liquidateSSV” test uses an ETH cluster, so it does not cover this trigger.
- Location: `reconcile/checks/m14_deposit_frontrun.py:110`, `:209`; shared `cluster_calls` uses only ETH selectors.

### M1 — M14's 7,200 produced blocks are not the specified 24-hour window

- Reproducer: `$P -m pytest -q "$S/test_independent.py::test_m14_three_hits_over_26_hours_do_not_warn"`; exit 1, one failure.
- Three hits, 3,500 produced blocks and 13 elapsed hours apart (a feasible sequence with missed slots); first to third spans 26 hours but only about 7,002 blocks.
- Expected: no warn, since at most two hits are within any trailing 24 hours.
- Actual: `[('open','info'), ('update','info'), ('escalate','warn')]`. The implementation never consults time for this window. 7,200 × 12 seconds is 24 hours only with every slot producing a block; it is an approximation, not equivalence. The spec says “within 24 h,” unlike the explicit block-based N/N2 decision for M3.
- Location: `reconcile/checks/m14_deposit_frontrun.py:66`, `:145`, `:169`.

### M2 — M14 counts hits relative to the last hit rather than the current sweep

- Reproducer: `$P -m pytest -q "$S/test_independent.py::test_m14_old_hits_expire_from_warn_count_before_last_hit_expires"`; exit 1, one failure.
- Open a warn with hits roughly 3,500 blocks apart, then sweep 300 blocks later. The first hit is now more than 24 hours and more than 7,200 blocks old; the latest remains recent.
- Expected: two recent hits, incident recorded at info (a downgrade may be silent).
- Actual: evidence still reports `count: 3` and the incident remains warn, because `recent` is anchored at `last['block']`, not the checkpoint. This defect remains even if 7,200 blocks were an accepted proxy for a day.
- Location: `reconcile/checks/m14_deposit_frontrun.py:169`.

### M3 — A later same-block top-up hides an earlier qualifying M14 attack

- Reproducer: `$P -m pytest -q "$S/test_independent.py::test_m14_later_topup_cannot_erase_an_earlier_same_block_attack"`; exit 1, one failure.
- Within one block: a liquidatable cluster receives one wei; a liquidation carrying the prior tuple reverts; then a later large deposit makes it non-liquidatable by block end. Transaction and log ordering are known.
- Expected: info for the qualifying earlier dust-deposit/revert pair; if the check cannot reconstruct the transaction-time state, UNKNOWN rather than silently dismissing it.
- Actual: no hit, no incident, no UNKNOWN health. The code tests the last tuple of the block, which is healthy after the later top-up, and drops the earlier event sequence.
- Location: `reconcile/checks/m14_deposit_frontrun.py:106`–`:124`.

## 2. Claims I could NOT confirm

- **Acceptance of all spec triggers / all benign twins:** disproved by H3 and M1–M3. In particular, the implementer's `liquidateSSV` scenario is not a legacy-cluster scenario, and its “24 h” tests measure only blocks.
- **“UNKNOWN, never clean” and complete reorg re-evaluation:** disproved by H1/H2. The supplied tests do not attack an API-only pre-existing M3 identity disappearing from the API, or a canonical re-inclusion with a missing transaction response during M14's reorg run.
- **Exact historical LIVE M3 result at block 26,241,455 (530 nonempty active clusters, 0 liquidatable/zero, 24 projected): NOT RE-RUN.** This is supplementary implementer evidence, not a CP2a V-row or the specifically required fresh M4 LIVE proof. Budget: avoid another full API/storage/view traversal after one complete fresh conservation traversal; CP3 fork/composed coverage remains the integration lane's work. I make no independent claim for those M3 LIVE counts or their historical endpoint availability.
- **Exact historical M4 snapshot at 26,241,393:** not rerun at the old block on recent-only publicnode; substituted a complete fresh read at 26,241,506, with independent arithmetic and the same invariant, below. This is a time-specific reproduction limit, not an unserved required LIVE row.
- Confirmed independently: supplied 329 tests and ruff; all 15 recorded mutations; config N/N2 movement; RPC bounds; M4 one-wei boundaries and pool interpretation; normal restart, routing/cadence, trigger-block reorgs, deeper reorgs, shorter replacements, and crash/restart controls. Required owned V-rows all have explicit outcomes below. No required row is silently skipped.

## 3. Verification matrix

Commands below were run separately, with no success hidden behind `&&`. `P` and `S` expand as above; `F` below abbreviates the three explicit files `reconcile/tests/test_m3.py reconcile/tests/test_m4.py reconcile/tests/test_m14.py` for readability only.

| V-ID | Exact command/action | Exit and measured result | Verdict |
|---|---|---|---|
| Preflight | `git rev-parse HEAD`; `git status --porcelain`; `df -h .` | frozen head exactly `7211819…`; clean; 352 GiB available | PASS |
| Environment | `uv venv reconcile/.venv --python 3.12`; `uv pip install --python reconcile/.venv/bin/python -r reconcile/requirements-dev.txt`; `$P --version` | all 0; Python 3.12.15, pytest 9.1.1, ruff 0.17.1; only authorized local install | PASS |
| Frozen patch | `git diff plan/monitoring-service -- reconcile/tests/harness.py reconcile/tests/test_framework.py reconcile/tests/test_worker.py` | 0, empty diff; harness SHA256 `9d8a340115f649737edac0b2ffa543a41894af525ea9fce6e4473fd7f4dc0cb4`, identical to `500abe4` | PASS |
| Frozen files | `git diff --name-only 96d1c32fe3446ec7092e849e401f851a0d9b2cf3 HEAD -- reconcile`; compare `git show --stat bd531b4` / `500abe4` and original shared-file diffs | only shared changes are the authorized three test files, the lane's one registration line, and its three config.example keys; no other frozen CP1B implementation edited | PASS |
| V2a.1 M3 | `$P -m pytest -q reconcile/tests/test_m3.py` | 0; 35 passed | Supplied scenarios PASS; acceptance FAIL H1 |
| V2a.1 M4 | `$P -m pytest -q reconcile/tests/test_m4.py` | 0; 25 passed | PASS |
| V2a.1 M14 | `$P -m pytest -q reconcile/tests/test_m14.py` | 0; 29 passed | Supplied scenarios PASS; acceptance FAIL H2/H3/M1–M3 |
| V2a.2 | `$P -m pytest -q reconcile/tests/test_m3.py reconcile/tests/test_m4.py reconcile/tests/test_m14.py -k thresholds_from_chain` | 0; 10 passed, 79 deselected | PASS |
| V2a.2 independent | `$P -m pytest -q -s "$S/test_independent.py"` (Appendix A) | 1 overall; both alternate N/N2 cases pass: (2,4), (11,29), warning/page tested immediately before and exactly at the configured boundaries | PASS subcases |
| V2a.3 | `$P -m pytest -q reconcile/tests/test_m3.py reconcile/tests/test_m4.py reconcile/tests/test_m14.py -k restart` | 0; 4 passed, 85 deselected | PASS ordinary restart; H2 is a reorg retry failure |
| V2a.4 | `$P -m pytest -q reconcile/tests/test_m3.py reconcile/tests/test_m4.py reconcile/tests/test_m14.py -k rpc_budget` | 0; 3 passed, 86 deselected | PASS |
| V2a.4 independent | `$P -m pytest -q -s "$S/test_independent.py"` → `test_independent_350_inventory_call_counts` | 0 subcase; exactly 350 validated inventory entries each; M3 `eth_call=1051`; M4 `eth_call=393`, `eth_getBalance=1`, `eth_getStorageAt=1`; M14 one entry `eth_call=4`, `eth_getTransactionByHash=1` | PASS; M14 bound is per entry, not an assertion of a constant for arbitrary inbox size |
| V2a.5 | `$P "$S/mutate.py"`; `$P "$S/mutate_own.py"` (Appendix C) | both 0; 15/15 implementer mutations and 3/3 independent mutations produce pytest exit 1; restored byte-for-byte | PASS falsifier sensitivity |
| V2a.6 tests | `$P -m pytest -q reconcile/tests` | 0; **329 passed in 11.66 s** | PASS |
| V2a.6 lint | `reconcile/.venv/bin/ruff check reconcile` | 0; All checks passed | PASS |
| V2a.7 | `$P -m pytest -q reconcile/tests/test_m14.py -k v2a7` | 0; 9 passed, 20 deselected | ETH basic same/previous block PASS; complete correlation FAIL H3/M3 |
| Independent attacks | `PYTHONPATH=reconcile/tests:reconcile $P -m pytest -q -s "$S/test_independent.py"` | 1; **7 failed, 28 passed**, 3 harmless marker warnings, 0.33 s | FAIL, six findings |
| Finding selection | `$P -m pytest -q "$S/test_independent.py" -k 'empty_api or reorg_missing or real_legacy or three_hits_over or old_hits or later_topup'` | 1; 7 failed, 28 deselected | FAIL reproduced independently of the green controls |
| Independent mutant controls | `$P -m pytest -q "$S/test_independent.py" -k 'exact_600 or exact_wei or clock_inside'` | 0; 5 passed, 30 deselected | PASS unmutated controls |
| Restoration control | `$P -m pytest -q "$S/reconcile/tests/test_m3.py" "$S/reconcile/tests/test_m4.py" "$S/reconcile/tests/test_m14.py"` | 0; 89 passed in 0.47 s | PASS after all 18 mutations |
| LIVE M4 | `$P "$S/live.py" "$PWD" https://ethereum-rpc.publicnode.com "$S/live.json"` (Appendix B) | 0; endpoint and block recorded below; completeness 58,762 = 58,762; A surplus positive and B false deficit | PASS |

The first sandboxed LIVE reachability probe failed with `rpc.RpcError: transport error: URLError` (exit 1); the authorized public read was then rerun with network escalation and succeeded. The first sandboxed Orca check returned `runtime_access_denied`/EPERM; escalation succeeded. There was no automatic-approval rejection, no secret use, no transaction submission, no mainnet write, no real message transport, and no Anvil run required by the local V-rows. `anvil`, `cast`, `uv` are present after exporting the required Foundry PATH.

### Spec-derived trigger ledger

Derived directly from spec M3, M4, M14, before using the implementer's scenario list. Source precedence: pinned contracts > spec > triage > plan. M4 pool interpretation is the explicitly authorized contract-correct reading, independently confirmed below.

| Trigger line | Alert / silent control independently run | Coverage assessment |
|---|---|---|
| M3 liquidatable onset → info, record block | `test_onset_is_info_and_records_the_block` / `test_onset_benign_cluster_above_threshold_stays_silent`; first-sweep/restart lower-bound test | Correct for observed inventory; H1 lifecycle gap |
| M3 still liquidatable at N → warn | `test_still_liquidatable_after_n_blocks_warns_exactly_at_n` / `test_n_blocks_benign_liquidated_before_n_resolves_without_warn`; independent N=2/11 boundaries | PASS |
| M3 at N2 → page | `test_still_liquidatable_after_n2_blocks_pages` / `test_n2_benign_lifted_by_deposit_before_n2_resolves_with_a_message`; independent N2=4/29 | PASS |
| M3 active computed balance zero → page | both `test_computed_zero_pages*` / `test_computed_zero_benign_low_balance_and_liquidated_clusters_do_not_page`; API-loss and liquidation-reorg probes | Trigger PASS, persistence FAIL H1 |
| M3 projected crossing within 24 h → info digest | `test_pre_alert_projected_within_24h_is_a_digest_line`, including its >7,200-block silent twin | PASS projection estimate / Discord digest |
| M4 any deficit >0 → page | deficit/root/daily tests; exact-cover+surplus silent twin; independent surplus -1/0/+1 wei | PASS exact integer comparison |
| M4 surplus trend → info | `test_surplus_trend_is_digest_info`; surplus causes no page/warn in exact-cover/surplus twins | PASS daily info digest; trend itself is informational, without a benign suppression threshold |
| M14 failed liquidate OR liquidateSSV + same/previous dust deposit on liquidatable cluster → info | all nine `v2a7` cases; burn equality, older deposit, nonliquidatable cluster and unrelated tx stay silent; independent genuine legacy and later-top-up probes | FAIL H3/M3; original test's liquidateSSV is an ETH tuple |
| M14 three hits in 24 h → warn | supplied repeat/spread twin; independent within-day success, 26-hour silent twin and rolling-expiry probe | FAIL M1/M2 |
| M14 correlated cluster also computed zero → page | `test_front_run_cluster_at_computed_zero_pages` / `test_computed_zero_benign_without_a_hit_is_silent`; independent page route/cadence | ETH PASS; legacy path blocked by H3 |

Other measured inherited obligations: checks directly returned findings without adding incident outbox rows (3 cases); page routing to Telegram and 6-hour cadence (3); warn 24-hour cadence (2); info/ digest Discord in supplied tests. Exact depth-one trigger-block reorgs pass for all three checks (independent cases assert checkpoint equals the replaced trigger block). Deeper-than-confirmation and crash/restart tests run in the supplied suites, including confirmations=12/depth≥20. Independent shorter replacement chains pass for all three with/without restart (6), and M3 liquidation-block reorg at computed zero reopens correctly with/without restart (2). Normal reorg success does not offset the UNKNOWN-specific failures H1/H2. Existing missing/reverted/empty view-result, unreadable parameter, transport-loss, same-block withdraw/deposit, near-set per-block, RootCommitted/cluster-event, and 600-second full-sweep cases all ran; independent exact-600 and same-block final-earnings tests also pass.

### Independent M4 contract derivation and LIVE measurement

Read the exact pin with `git -C /Users/marco/ssv/repos/ssv-network show f7ac487:<path>`; full pin resolves to `f7ac4870074b9408855a7912f9c6e1f959996be8`. Consulted `contracts/modules/SSVViews.sol`, `SSVStaking.sol`, `contracts/libraries/ProtocolLib.sol`, `storage/SSVStorage.sol`, `ClusterLib.sol`, and the pinned `abis/SSVViews.json`. The ABI confirms ETH/SSV variant separation as well as M4 getter signatures. Traced the public-view accounting in the referenced triage insolvency script as a lower-precedence cross-check.

`SSVViews.getNetworkEarnings` (491–492) unpacks `networkTotalEarnings`. `ProtocolLib.networkTotalEarnings` (84–89) returns `ethDaoBalance` plus unsettled index × fee × ETH vUnits accrual. `SSVStaking._syncFees` (183–205) stores that same current total in both `ethDaoBalance` and `stakingEthPoolBalance`; `claimEthRewards` (137–147) checks and subtracts the identical payout from both before transferring ETH. Thus the pool is a snapshot/sub-account of network earnings, not an additional liability. StorageData places `lastOperatorId` at namespace member 8 (seven preceding mappings, then token address, then the counter). This independently yields A = active ETH computed cluster balances + all ETH operator earnings + network earnings, once. No source or ABI was edited.

Fresh LIVE endpoint **https://ethereum-rpc.publicnode.com**, checkpoint **26,241,506**, hash **`0x86d56441e477d625ed1f41ff2c201c6a84171c27a421159207ec8798908146b1`**. API GET **https://api.ssv.network/api/v4/mainnet/clusters**. Appendix B independently builds getter calldata and integer sums; it reuses the framework's storage-validating inventory and RPC client, **not** `m4.scan()` or the implementer's saved output. All calls explicitly use that one block; 1,908 API clusters validated (882 ETH / 1,026 legacy), 548 active ETH, listed validators = chain getter = 58,762; last operator ID 2,352. All arithmetic below is exact wei:

| Term | Measured wei |
|---|---:|
| Proxy ETH balance | 425460945991392730800 |
| Active ETH cluster balances | 209266339187317430800 |
| Operator earnings, IDs 1–2352 | 7504702103641200000 |
| Network earnings | 208689904699976300000 |
| Staking pool, evidence only | 208603988997805600000 |
| A surplus | **457800000** |
| B surplus (A minus pool) | **-208603988997347800000** |

Calls measured: 3,817 `eth_getStorageAt`, 2,903 `eth_call`, one `eth_getBalance`. A is nonnegative; adding the pool causes a false 208.604 ETH deficit. This fresh snapshot replaces, rather than copies, the implementer's earlier numbers. Independent fake-block boundary tests show surplus -1 wei pages, 0 and +1 wei stay silent. A fake block with two earnings changes uses the final +31-wei result, yields exact surplus -1, and all observed view calls carry the same checkpoint; no mid-sweep `latest` read. Integer decoding, summation and comparison preserve wei precision; there is no tolerance or floating-point conversion in the decision. Inventory-incomplete and empty/reverted getter supplied cases retain UNKNOWN rather than resolving M4.

## 4. Falsifiers

All mutations were made only in a byte-identical scratch copy (`$S/reconcile`), with each original file restored in `finally` and a fresh `PYTHONPYCACHEPREFIX` per mutant to defeat same-size/same-second `.pyc` reuse. The real worktree remained clean. Appendix C contains both exact runners and all replacement strings/test commands. Every mutant failed by assertion, not collection/import error; exit 1 is the expected red, and the runners themselves exit 0 only if all mutations are red and restored.

| ID | Mutation / selected test | Observed red |
|---|---|---|
| F1 | M3 `age >= n` → `age > n`; N boundary | 1 failed |
| F2 | M3 `age >= n2` → `age > n2`; N2 boundary | 1 failed |
| F3 | Drop computed-zero disjunct; zero even if not liquidatable | 1 failed |
| F4 | Drop M3 onset rewind DELETE; onset above ancestor | 2 failed |
| F5 | Drop inventory-gap UNKNOWN append; incomplete inventory | 1 failed |
| F6 | M4 deficit `>0` → `>=0`; exact cover | 1 failed |
| F7 | Add staking pool to liabilities; pool guard | 1 failed |
| F8 | Disable M4 incomplete branch; entire M4 file | 7 failed, 18 passed |
| F9 | Drop M4 scan rewind DELETE; deficit reorg | 2 failed |
| F10 | M14 deposit `< burn` → `<= burn`; equality silent twin | 1 failed |
| F11 | Previous-block range -1 → -2; two-block-old silent twin | 1 failed |
| F12 | Drop transaction order filter; deposit-after-failure silent twin | 1 failed |
| F13 | Recent hit count → all hits; spread-window twin | 1 failed |
| F14 | Drop M14 hit rewind DELETE; trigger reorg | 2 failed |
| F15 | Drop liquidatable guard; healthy-cluster silent twin | 1 failed |
| QF3 (mine) | Full sweep elapsed `>=600` → `>600`; exact-600 burn change | 1 failed |
| QF4 (mine) | Drop network earnings from sum; independent -1 wei deficit | 1 failed |
| QF14 (mine) | Repetition `>=repeat` → `>repeat`; independent three-hit clocked case | 1 failed |

The independently designed non-mutant falsifiers include API-only baseline disappearance, canonical replacement evidence unavailable, genuine legacy storage, 26 elapsed hours with missed slots, rolling expiry, later same-block top-up, same-block earnings changes, shorter chains, and exact depth one. These test expected behavior and expose seven reds on the unchanged implementation. Restoration SHA256s (scratch = worktree = frozen file):

```text
m3_liquidation_zone.py      bbf88f9499bf4b134fcfec3d9bfd63266cf7177296c69d4b04c7df94f08264e0
m4_conservation_deficit.py  e0888f88ed20501ad976bc823b3957e7e35ad8ea526d407a69c404f655ead5f1
m14_deposit_frontrun.py     191d2176080e7359cbac0ca0056f7c16adaf90e03eab9592e17d18103edd475f
```

Final restored scratch monitor-suite control: exit 0, 89 passed. Unmutated independent mutant-controls: exit 0, 5 passed. Original repository porcelain before evidence-plane writes: empty. Budget: the complete original 329-test suite ran once; only targeted changed probes and restoration controls were rerun. No redundant full regression rerun or second expensive LIVE traversal; supplementary M3 LIVE and CP3 fork/composed tests explicitly excluded above.

## 5. Evidence-plane writes

- CP2a status: IMPLEMENTED / AWAITING QA → **QA FAILED**, frozen head `7211819c1b0f5ebd612c35dd45cc9f4549554c59`, linked to this report. Preserve the existing implementer/QA/input suffix.
- Wrote only `plans/monitoring-service/qa/cp2a-attempt-1.md`, the CP2a `**Status:**` line in `plans/monitoring-service/plan.md`, and one appended event in `plans/monitoring-service/execution-log.md`.
- Authorized untracked environment: `reconcile/.venv`. All probe code, mutant copies, source-pin extracts and raw outputs are outside the repository under `/private/tmp/cp2a-qa-7211819`. Complete reproducer code and LIVE result are preserved here.
- Next owner: coordinator / fresh implementation repair worker. Six findings remain; this QA worker made no repairs. A FAIL verdict is a successfully completed independent QA assignment.

## 6. Repo proof

Frozen anchor is the dispatched lane head, not the earlier implementation starting base. Commands after the evidence writes (before the evidence commit):

```text
$ git status --porcelain
 M plans/monitoring-service/execution-log.md
 M plans/monitoring-service/plan.md
?? plans/monitoring-service/qa/cp2a-attempt-1.md

$ git diff --name-only 7211819c1b0f5ebd612c35dd45cc9f4549554c59
plans/monitoring-service/execution-log.md
plans/monitoring-service/plan.md
```

The new untracked report is in porcelain; after staging/commit the same diff includes that report as the third and only additional path. No implementation file appears in either proof. No config/, config.zip, .env*, another lane, or frozen implementation was touched. After the evidence commit, porcelain is empty; the frozen-anchor diff lists only the three evidence paths. Actual post-write and post-commit commands are executed before worker_done; no implementation repair is permitted or pending in this QA branch.

## Appendix A — durable independent probes

Save the following block as `/private/tmp/cp2a-qa-7211819/test_independent.py` and run from this frozen worktree root. No repo test or implementation edit is needed. Expected unchanged result: **7 failed, 28 passed**. Marker warnings are expected outside the original test directory.

```python
import sys, json
from pathlib import Path
sys.path[:0]=[str(Path.cwd()/'reconcile/tests'),str(Path.cwd()/'reconcile')]
import pytest
from fixtures_liquidation import (setup, sent, incidents, open_incidents, cid_hex, BURN, THRESHOLD, OWNER, OPS,
                                 stub_m4, Clusters, LiquidationChain, failed_liquidation, stub_liquidation_reverts)
from harness import advance, outbox, VIEWS, stub_params, api_row
from fakechain import PROXY, FakeRpc, _enc, _topic_addr
from eb_storage import u256, cluster_id, eth_cluster_slot, ssv_cluster_slot, Cluster
from checks.m3_views import GET_NETWORK_EARNINGS, GET_BALANCE, GET_OPERATOR_EARNINGS, selector
from test_m3 import onset
from test_m4 import balanced, root
from test_m14 import make, frontrun, hits

@pytest.mark.parametrize('surplus',[-1,0,1])
def test_m4_exact_wei(tmp_path,surplus):
    chain,w,cl=balanced(tmp_path,surplus=surplus)
    r=w.db.conn.execute('SELECT surplus,complete FROM m4_scans').fetchone()
    assert tuple(r)==(str(surplus),1)
    assert sent(w,'m4')==([('open','page')] if surplus<0 else [])
    print('M4_BOUNDARY',surplus,sent(w,'m4'))

def test_m4_two_updates_within_same_block_are_pinned(tmp_path):
    chain,w,cl=balanced(tmp_path,surplus=0)
    checkpoint=chain.sealed
    from test_m4 import NETWORK, liabilities
    # Pending block changes the final earnings twice; only its final state is canonical for eth_call.
    chain.stub_call(VIEWS,GET_NETWORK_EARNINGS,u256(NETWORK+17))
    chain.stub_call(VIEWS,GET_NETWORK_EARNINGS,u256(NETWORK+31))
    chain.set_balance(PROXY,liabilities(chain,chain.number,network=NETWORK+31)-1)
    root(chain)
    chain.calls.clear()
    advance(w)
    assert sent(w,'m4')==[('open','page')]
    ev=json.loads(incidents(w,'m4')[-1]['evidence'])
    assert ev['surplus']=='-1' and ev['network']==str(NETWORK+31)
    assert {b for _,_,b in chain.calls}=={chain.sealed}

@pytest.mark.parametrize('n,n2',[(2,4),(11,29)])
def test_m3_alternate_configuration_boundaries(tmp_path,n,n2):
    chain,w,cl,start=onset(tmp_path,cfg={'m3':{'n_blocks':n,'n2_blocks':n2}})
    chain.mine(n-1); advance(w)
    assert sent(w,'m3')==[('open','info')]
    chain.mine(); advance(w)
    assert sent(w,'m3')[-1]==('escalate','warn')
    chain.mine(n2-n-1); advance(w)
    assert sent(w,'m3')[-1]==('escalate','warn')
    chain.mine(); advance(w)
    assert sent(w,'m3')[-1]==('escalate','page')

@pytest.mark.parametrize('monitor',['m3','m4','m14'])
def test_check_returns_findings_without_alerting_directly(tmp_path,monitor):
    if monitor=='m3':
        chain,w,cl=setup(tmp_path)
        cl.create(balance=0)
    elif monitor=='m4':
        chain,w,cl=balanced(tmp_path,surplus=-1)
    else:
        chain,w,cl,path=make(tmp_path)
        frontrun(chain,cl,path)
    w.check_reorg(); w.scan(); w.poll_inbox()
    chk=w.checks[monitor]; block=w.checkpoint()['block']
    w.reload_params(block)
    ctx=w._ctx(chk,block,{'force'},inbox=lambda: w.inbox.pending(w.db.meta('cursor:inbox:'+monitor,0)) if w.inbox else [])
    chk.prepare(ctx)
    before=[tuple(x) for x in w.db.conn.execute('SELECT * FROM outbox')]
    result=chk.run(ctx,block)
    assert result
    assert [tuple(x) for x in w.db.conn.execute('SELECT * FROM outbox')]==before

@pytest.mark.parametrize('monitor',['m3','m4','m14'])
@pytest.mark.parametrize('restart',[False,True])
def test_shorter_replacement_chain(tmp_path,monitor,restart):
    from fixtures_liquidation import restart as restart_worker
    path=None
    if monitor=='m3':
        chain,w,cl=setup(tmp_path,cfg={'m3':{'n_blocks':1}})
        cl.create(balance=THRESHOLD+1000*BURN); advance(w)
        fork=chain.number
        cl.withdraw(value=1100*BURN); advance(w)
        chain.mine(3); advance(w)
    elif monitor=='m4':
        chain,w,cl=balanced(tmp_path,surplus=10000*BURN)
        fork=chain.number
        cl.deposit(value=20000*BURN); root(chain); advance(w)
        chain.mine(3)
    else:
        chain,w,cl,path=make(tmp_path,cfg={'m14':{'repeat_count':1}})
        fork=chain.number
        frontrun(chain,cl,path); advance(w)
        chain.mine(3)
    old=w.checkpoint()['block']
    assert open_incidents(w,monitor)
    chain.reorg(fork); chain.mine()
    assert chain.sealed < old or monitor!='m3'
    if restart:
        w.check_reorg()
        w=restart_worker(tmp_path,w,checks=(monitor,),inbox={'path':str(path)} if path else {})
    advance(w)
    assert not open_incidents(w,monitor)
    assert w.db.meta('reorg:'+monitor) is None
    assert not outbox(w,"kind='realert'")

# Empty API data after a real initial API-backed observation must be UNKNOWN, not a resolution.
@pytest.mark.parametrize('reorg',[False,True])
def test_m3_empty_api_does_not_resolve_live_cluster(tmp_path,reorg):
    chain=LiquidationChain(); stub_params(chain); chain.mine()
    cl=Clusters(chain); cl.create(balance=0)
    # Start after the registration log: the API is the baseline for this existing cluster.
    chain.mine()
    _,w,_=setup(tmp_path,chain=chain,inventory={'api_url':'https://api.invalid','api_refresh_s':0})
    c=cl.tuple()
    row=api_row(OWNER,OPS,c.validator_count,c.network_fee_index,c.index,c.active,c.balance)
    response={'clusters':[row],'pagination':{'pages':1}}
    w.inventory.http_get=lambda _:response
    advance(w)
    assert sent(w,'m3')==[('open','page')]
    original=incidents(w,'m3')[-1]['id']
    chain.mine(3); advance(w)
    response['clusters']=[]
    if reorg: chain.reorg(chain.number-1)
    chain.mine(2); w.clock.advance(600); advance(w)
    print('EMPTY_API',reorg,[(x['state'],x['severity']) for x in incidents(w,'m3')],sent(w,'m3'))
    assert original in {x['id'] for x in open_incidents(w,'m3').values()}, 'live computed-zero cluster falsely resolved'
    assert not outbox(w,"kind='resolved'")

# At 12-second slots plus misses, fewer than 7200 produced blocks can span >24 real hours.
def test_m14_three_hits_over_26_hours_do_not_warn(tmp_path):
    chain,w,cl,path=make(tmp_path)
    for i in range(3):
        if i:
            chain.mine(3500); w.clock.advance(13*3600)
        frontrun(chain,cl,path); advance(w)
    print('M14_26_HOURS',sent(w,'m14'))
    assert not [s for _,s in sent(w,'m14') if s in {'warn','page'}], 'three hits span 26h, outside spec 24h'

# Count recent hits at the current sweep, rather than forever at the last hit.
def test_m14_old_hits_expire_from_warn_count_before_last_hit_expires(tmp_path):
    chain,w,cl,path=make(tmp_path)
    frontrun(chain,cl,path); advance(w)
    chain.mine(3500); w.clock.advance(3500*12)
    frontrun(chain,cl,path); advance(w)
    chain.mine(3500); w.clock.advance(3500*12)
    frontrun(chain,cl,path); advance(w)
    assert list(open_incidents(w,'m14').values())[0]['severity']=='warn'
    chain.mine(300); w.clock.advance(300*12+600); advance(w)
    ev=json.loads(incidents(w,'m14')[-1]['evidence'])
    print('M14_ROLLING_COUNT',ev)
    assert ev['count']==2, 'first hit is now >24h old'
    assert list(open_incidents(w,'m14').values())[0]['severity']=='info'

# A true legacy cluster requires the SSV variants. Strict stubs reproduce source VERSION checks.
def test_m14_real_legacy_ssv_liquidation_is_supported(tmp_path):
    chain,w,cl,path=make(tmp_path)
    from fakechain import MiniSSV
    owner='0x'+'bb'*20; ops=(31,32,33,34)
    mini=MiniSSV(chain); cid=mini.legacy_ssv_cluster(owner,ops,1)
    st=mini.clusters[cid]; st['balance']=100
    mini._persist(cid,st)
    old=mini._cluster(st)
    chain.mine()
    # A genuine legacy deposit updates only SSV storage, with a liquidateSSV revert afterward.
    st['balance']+=1; mini._persist(cid,st)
    tx=chain.new_tx(owner)
    chain.emit(tx,'ClusterDeposited',[_topic_addr(owner)],_enc(['uint64[]','uint','cluster'],[list(ops),1,mini._cluster(st)]))
    for name,val in [('isLiquidatableSSV',1),('getBalanceSSV',100),('getBurnRateSSV',BURN)]:
        chain.stub_call(VIEWS,selector(name+'(address,uint64[],(uint32,uint64,uint64,bool,uint256))'),u256(val))
    failed_liquidation(chain,cl,path,owner=owner,ops=ops,fn='liquidateSSV',tuple_=old)
    chain.mine(); advance(w)
    inv=w.inventory.get(chain.sealed)
    assert inv.clusters['0x'+cid.hex()].version=='SSV' and inv.clusters['0x'+cid.hex()].valid
    print('LEGACY_M14',hits(w),[r['text'] for r in outbox(w) if r['key'].startswith('health:m14')])
    assert sent(w,'m14')==[('open','info')], 'liquidatable SSV dust deposit + failed liquidateSSV is missed'

# View state at the final block differs from the transaction-time liquidatable state.
def test_m14_later_topup_cannot_erase_an_earlier_same_block_attack(tmp_path):
    chain,w,cl,path=make(tmp_path)
    old=cl.tuple()
    cl.deposit(value=1,mine=False)
    failed_liquidation(chain,cl,path,tuple_=old)
    cl.deposit(value=10**18,mine=False)
    chain.mine(); advance(w)
    print('LATE_TOPUP',hits(w),sent(w,'m14'))
    assert sent(w,'m14')==[('open','info')], 'liquidatable at failure but healthy at block end'

def test_independent_350_inventory_call_counts(tmp_path):
    report={}
    for monitor in ['m3','m4','m14']:
        p=tmp_path/monitor; p.mkdir()
        if monitor=='m14':
            chain,w,cl,path=make(p)
            for i in range(348): cl.create('0x'+f'{i+1:040x}',OPS,balance=10**18,mine=False)
            chain.mine(); advance(w)
            frontrun(chain,cl,path); advance(w)
            bound=4
        else:
            chain,w,cl=setup(p,checks=(monitor,))
            for i in range(350): cl.create('0x'+f'{i+1:040x}',OPS,balance=10**18,mine=False)
            if monitor=='m4': stub_m4(chain,{i:10**15 for i in range(1,41)},10**15,10**15,10**30)
            chain.mine(); advance(w)
            bound=1051 if monitor=='m3' else 393
        inv=w.inventory.get(chain.sealed)
        assert len(inv.clusters)==350
        calls=w.db.meta('run:'+monitor)['rpc_calls']
        assert calls['eth_call']<=bound
        report[monitor]=calls
    print('QA_350_CALLS',json.dumps(report,sort_keys=True))

def test_m3_exact_600_second_sweep_detects_unannounced_burn_change(tmp_path):
    chain,w,cl=setup(tmp_path)
    cl.create(balance=THRESHOLD+100000*BURN)
    advance(w)
    assert not open_incidents(w,'m3')
    cl.set_burn(burn=100*BURN); chain.mine()
    w.clock.advance(599); advance(w)
    assert not open_incidents(w,'m3')
    w.clock.advance(1); advance(w)
    assert sent(w,'m3')==[('open','info')]

def test_m14_three_hits_with_clock_inside_one_day_warn(tmp_path):
    chain,w,cl,path=make(tmp_path)
    for i in range(3):
        if i: chain.mine(100); w.clock.advance(1200)
        frontrun(chain,cl,path); advance(w)
    assert sent(w,'m14')[-1]==('escalate','warn')
    ev=json.loads(incidents(w,'m14')[-1]['evidence'])
    assert ev['count']==3

@pytest.mark.parametrize('monitor',['m3','m4','m14'])
def test_page_realert_cadence(tmp_path,monitor):
    if monitor=='m3':
        chain,w,cl=setup(tmp_path); cl.create(balance=0); advance(w)
    elif monitor=='m4':
        chain,w,cl=balanced(tmp_path,surplus=-1)
    else:
        chain,w,cl,path=make(tmp_path)
        cl.withdraw(value=chain.balance_at(cluster_id(OWNER,OPS),chain.number)-1)
        frontrun(chain,cl,path); chain.mine(); advance(w)
    assert sent(w,monitor)==[('open','page')]
    assert outbox(w,"kind='open'")[-1]['delivery']=='telegram'
    w.clock.advance(6*3600-1); w.housekeeping()
    assert not outbox(w,"kind='realert'")
    w.clock.advance(1); w.housekeeping()
    assert sent(w,monitor)==[('open','page'),('realert','page')]

@pytest.mark.parametrize('monitor',['m3','m14'])
def test_warn_realert_cadence(tmp_path,monitor):
    if monitor=='m3':
        chain,w,cl,_=onset(tmp_path,cfg={'m3':{'n_blocks':1}})
        chain.mine(); advance(w)
    else:
        chain,w,cl,path=make(tmp_path)
        for _ in range(3): frontrun(chain,cl,path); advance(w)
    assert sent(w,monitor)[-1][1]=='warn'
    w.clock.advance(86400-1); w.housekeeping()
    assert not outbox(w,"kind='realert'")
    w.clock.advance(1); w.housekeeping()
    assert sent(w,monitor)[-1]==('realert','warn')

@pytest.mark.parametrize('restart',[False,True])
def test_m3_reorg_of_liquidation_reopens_at_computed_zero(tmp_path,restart):
    from fixtures_liquidation import restart as restart_worker
    chain,w,cl=setup(tmp_path)
    cl.create(balance=0); advance(w)
    assert sent(w,'m3')==[('open','page')]
    fork=chain.number; cl.liquidate(); advance(w)
    assert not open_incidents(w,'m3')
    chain.reorg(fork); chain.mine(2)
    if restart:
        w.check_reorg(); w=restart_worker(tmp_path,w)
    advance(w)
    assert list(open_incidents(w,'m3').values())[0]['severity']=='page'
    assert sent(w,'m3')==[('open','page'),('resolved','info'),('open','page')]

# Re-included canonical failed-tx evidence is temporarily unreadable in a reorg run.
def test_m14_reorg_missing_transaction_does_not_clean_resolve(tmp_path):
    from test_m14 import M14
    chain,w,cl,path=make(tmp_path,cfg={'m14':{'repeat_count':1}})
    fork=chain.number; old=cl.tuple()
    frontrun(chain,cl,path); chain.mine(2); advance(w)
    assert sent(w,'m14')==[('open','warn')]
    chain.reorg(fork)
    cl.state[cluster_id(OWNER,OPS)].update(index=old.index,nfi=old.network_fee_index,balance=old.balance)
    chain.mine()
    cl.deposit(value=1,mine=False)
    tx,line=failed_liquidation(chain,cl,path,tuple_=old)
    chain.mine(2)
    w.check_reorg(); w.scan(); w.poll_inbox()
    saved_tx=chain.txs.pop(tx)
    w.run_checks()
    chain.txs[tx]=saved_tx
    chain.mine(); w.clock.advance(600); advance(w)
    print('REORG_UNKNOWN_M14',sent(w,'m14'),w.db.meta('reorg:m14'))
    assert open_incidents(w,'m14'), 'unknown canonical replacement evidence was treated as clean'
    assert not outbox(w,"kind='resolved'")

@pytest.mark.parametrize('monitor',['m3','m4','m14'])
def test_trigger_block_reorg_exact_depth_one(tmp_path,monitor):
    if monitor=='m3':
        chain,w,cl=setup(tmp_path)
        cl.create(balance=THRESHOLD+1000*BURN); advance(w)
        trigger=chain.number
        cl.withdraw(value=chain.balance_at(cluster_id(OWNER,OPS),chain.number)); advance(w)
    elif monitor=='m4':
        chain,w,cl=balanced(tmp_path,surplus=10000*BURN)
        trigger=chain.number
        cl.deposit(value=20000*BURN,mine=False)
        root(chain); advance(w)
    else:
        chain,w,cl,path=make(tmp_path,cfg={'m14':{'repeat_count':1}})
        trigger=chain.number
        frontrun(chain,cl,path); advance(w)
    assert open_incidents(w,monitor)
    assert w.checkpoint()['block']==trigger, 'exactly the trigger block has been confirmed'
    chain.reorg(trigger); chain.mine()
    advance(w)
    assert not open_incidents(w,monitor)
    assert w.db.meta('reorg:'+monitor) is None
    print('EXACT_DEPTH_1',monitor,trigger,w.db.meta('run:'+monitor)['reasons'])

```

## Appendix B — durable LIVE script and raw result

Save as `/private/tmp/cp2a-qa-7211819/live.py`; command is in the matrix. Public reads only; a fresh run selects a new recent block, so live numbers may change.

```python
import sys, json, time
sys.path.insert(0, sys.argv[1] + '/reconcile')
from rpc import Rpc, Chain
from inventory import fetch_api_clusters, build, API_DEFAULT
from eb_storage import MAIN_BASE, u256, keccak
from checks.m3_views import encode_cluster_args
endpoint=sys.argv[2]
r=Rpc(endpoint, timeout=30, retries=2, batch_size=30)
block=r.block_number()-12
v='0xafe830b6ee262ba11cce5f32fdcd760ffe6a66e4'; p='0xdd9bc35ae942ef0cfa76930954a156b3ff30a4e1'
def sel(s): return keccak(s.encode())[:4]
def integer(x):
    assert len(x)==32, ('malformed',x)
    return int.from_bytes(x,'big')
print('endpoint',endpoint,'block',block,flush=True)
rows=fetch_api_clusters(API_DEFAULT)
c=Chain(r,block)
inv=build(c,p,block,rows,{})
active=[x for x in inv.clusters.values() if x.version=='ETH' and x.valid and x.cluster.active]
invalid=[x.cluster_id for x in inv.clusters.values() if x.version=='ETH' and not x.valid]
assert not invalid, invalid
count=integer(c.call(v,sel('getNetworkValidatorsCount()')))
listed=sum(x.cluster.validator_count for x in active)
assert count==listed, (count,listed)
last=c.storage(p,[MAIN_BASE+8])[0]
calls=[(v,sel('getBalance(address,uint64[],(uint32,uint64,uint64,bool,uint256))')+encode_cluster_args(x.owner,x.operator_ids,x.cluster)) for x in active]
calls += [(v,sel('getOperatorEarnings(uint64)')+u256(i)) for i in range(1,last+1)]
calls += [(v,sel('getNetworkEarnings()')),(v,sel('stakingEthPoolBalance()'))]
out=[integer(x) for x in c.call_many(calls)]
bal=c.balance(p); n=len(active)
cluster=sum(out[:n]); operator=sum(out[n:n+last]); network,pool=out[-2:]
a=bal-cluster-operator-network; b=a-pool
assert a>=0, a
assert b<0, b
result=dict(endpoint=endpoint, block=block, block_hash=r.block_hash(block), api_rows=len(rows),inventory=inv.stats(),active_eth_clusters=n, validators=count, listed_validators=listed,last_operator_id=last,proxy_balance=bal,cluster_balances=cluster,operator_earnings=operator,network_earnings=network,staking_pool=pool,A_surplus_wei=a,B_surplus_wei=b,rpc_calls=dict(c.counts))
print(json.dumps(result,indent=2),flush=True)
with open(sys.argv[3],'w') as f: json.dump(result,f,indent=2)

```

Raw independently measured result:

```json
{
  "endpoint": "https://ethereum-rpc.publicnode.com",
  "block": 26241506,
  "block_hash": "0x86d56441e477d625ed1f41ff2c201c6a84171c27a421159207ec8798908146b1",
  "api_rows": 1908,
  "inventory": {
    "block": 26241506,
    "clusters": 1908,
    "eth": 882,
    "ssv_legacy": 1026,
    "absent": 0,
    "valid": 1908,
    "invalid": 0,
    "from_api": 1908,
    "from_logs": 0,
    "tuple_from_logs": 0,
    "tuple_from_api": 1908,
    "operators": 1936
  },
  "active_eth_clusters": 548,
  "validators": 58762,
  "listed_validators": 58762,
  "last_operator_id": 2352,
  "proxy_balance": 425460945991392730800,
  "cluster_balances": 209266339187317430800,
  "operator_earnings": 7504702103641200000,
  "network_earnings": 208689904699976300000,
  "staking_pool": 208603988997805600000,
  "A_surplus_wei": 457800000,
  "B_surplus_wei": -208603988997347800000,
  "rpc_calls": {
    "eth_getStorageAt": 3817,
    "eth_call": 2903,
    "eth_getBalance": 1
  }
}
```

## Appendix C — exact mutation runners

Prepare the scratch source copy once with the following authorized scratch-only operation from the worktree root; omit this if the existing scratch copy is present and restored.

```python
import shutil
shutil.copytree('reconcile', '/private/tmp/cp2a-qa-7211819/reconcile',
                ignore=shutil.ignore_patterns('.venv', '__pycache__', '.pytest_cache', '.ruff_cache'))
```

Save the next two blocks as `mutate.py` and `mutate_own.py` under that scratch root. Both use the running venv interpreter; each row restores its original bytes, asserts red, and preserves full pytest output outside the repo.

```python
import subprocess, pathlib, os, json, sys, hashlib
base=pathlib.Path('/private/tmp/cp2a-qa-7211819'); tree=base/'reconcile'
m3='checks/m3_liquidation_zone.py'; m4='checks/m4_conservation_deficit.py'; m14='checks/m14_deposit_frontrun.py'
def t(m,n): return 'tests/test_'+m+'.py::'+n
rows=[
('F1',m3,'elif age >= n:','elif age > n:',t('m3','test_still_liquidatable_after_n_blocks_warns_exactly_at_n')),
('F2',m3,'elif age >= n2:','elif age > n2:',t('m3','test_still_liquidatable_after_n2_blocks_pages')),
('F3',m3,'if view.liquidatable or view.balance == 0:','if view.liquidatable:',t('m3','test_computed_zero_pages_even_when_the_view_says_not_liquidatable')),
('F4',m3,'        ctx.db.conn.execute("DELETE FROM m3_zone WHERE onset_block > ?", (ancestor,))','        pass',t('m3','test_reorg_onset_above_ancestor_still_liquidatable_is_reported_again')),
('F5',m3,'            unknown_rows.append(("inventory", "unknown: " + gap))','            pass',t('m3','test_unknown_incomplete_inventory_is_health_on_every_full_sweep')),
('F6',m4,'        if deficit > 0:','        if deficit >= 0:',t('m4','test_deficit_benign_exact_cover_and_surplus_stay_silent')),
('F7',m4,'    liabilities = clusters + operators + network','    liabilities = clusters + operators + network + (pool or 0)',t('m4','test_staking_pool_is_not_added_as_a_separate_liability')),
('F8',m4,'        if not s["complete"]:','        if False:','tests/test_m4.py'),
('F9',m4,'            ctx.db.conn.execute("DELETE FROM m4_scans WHERE block > ?", (ctx.reorg_ancestor,))','            pass',t('m4','test_reorg_removing_the_deficit_resolves_and_never_repages')),
('F10',m14,'            if liquidatable and value < burn:','            if liquidatable and value <= burn:',t('m14','test_v2a7_benign_deposit_of_at_least_one_block_of_burn')),
('F11',m14,'if ev.block >= e.block - 1]','if ev.block >= e.block - 2]',t('m14','test_v2a7_benign_deposit_two_blocks_earlier')),
('F12',m14,'and (ev.block < e.block or tx_index is None or ev.tx_index < tx_index)]',']',t('m14','test_v2a7_benign_deposit_after_the_failed_liquidation_in_the_same_block')),
('F13',m14,'        elif len(recent) >= repeat:','        elif len(hits) >= repeat:',t('m14','test_three_hits_benign_spread_beyond_the_window_stay_info')),
('F14',m14,'            ctx.db.conn.execute("DELETE FROM m14_hits WHERE block > ?", (A,))','            pass',t('m14','test_reorg_removing_the_front_run_resolves_and_never_repages')),
('F15',m14,'            if liquidatable and value < burn:','            if value < burn:',t('m14','test_v2a7_benign_cluster_not_liquidatable')),
]
results=[]
for name,file,old,new,test in rows:
    p=tree/file; orig=p.read_bytes(); source=orig.decode(); assert source.count(old)==1, name
    try:
        p.write_text(source.replace(old,new))
        env={**os.environ,'PYTHONPYCACHEPREFIX':str(base/('pycache-'+name))}
        cmd=[sys.executable,'-m','pytest','-q',str(tree/test)]
        proc=subprocess.run(cmd,cwd=base,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
        (base/(name+'.log')).write_text(proc.stdout)
    finally: p.write_bytes(orig)
    restored=p.read_bytes()==orig
    line=dict(id=name,mutation=old+' -> '+new,command=cmd,exit=proc.returncode,restored=restored,tail=proc.stdout.strip().splitlines()[-1])
    results.append(line); print(json.dumps(line),flush=True)
(base/'mutations.json').write_text(json.dumps(results,indent=2))
assert all(r['exit']==1 and r['restored'] for r in results)

```

```python
import subprocess, pathlib, sys, os, json
base=pathlib.Path('/private/tmp/cp2a-qa-7211819'); tree=base/'reconcile'
rows=[
('QF3','checks/m3_liquidation_zone.py','or now - self.last_full >= self.config["full_sweep_interval_s"])','or now - self.last_full > self.config["full_sweep_interval_s"])','test_m3_exact_600_second_sweep_detects_unannounced_burn_change'),
('QF4','checks/m4_conservation_deficit.py','    network = values[n + last_op] or 0','    network = 0','test_m4_exact_wei[-1]'),
('QF14','checks/m14_deposit_frontrun.py','        elif len(recent) >= repeat:','        elif len(recent) > repeat:','test_m14_three_hits_with_clock_inside_one_day_warn')]
results=[]
for name,file,old,new,test in rows:
    p=tree/file; orig=p.read_bytes(); assert orig.decode().count(old)==1
    try:
        p.write_text(orig.decode().replace(old,new))
        cmd=[sys.executable,'-m','pytest','-q',str(base/'test_independent.py')+'::'+test]
        out=subprocess.run(cmd,cwd=base,env={**os.environ,'PYTHONPYCACHEPREFIX':str(base/('pycache-'+name))},stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
        (base/(name+'.log')).write_text(out.stdout)
    finally: p.write_bytes(orig)
    result=dict(id=name,command=cmd,exit=out.returncode,restored=p.read_bytes()==orig,tail=out.stdout.strip().splitlines()[-1])
    results.append(result); print(json.dumps(result))
(base/'own-mutations.json').write_text(json.dumps(results,indent=2))
assert all(r['exit']==1 and r['restored'] for r in results)

```
