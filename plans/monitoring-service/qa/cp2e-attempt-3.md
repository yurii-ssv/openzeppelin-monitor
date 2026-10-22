FAIL

# CP2e (Legacy SSV: M6, M7) — independent QA, attempt 3 (capped, D15)

- QA: Claude Opus 5.5 (`claude-opus-5-5`), effort high (D13; attempt-3 addendum). Task `task_5acf6026d077`, dispatch `ctx_8184634bdb45`. Observed 2026-10-22T09:4xZ–10:1xZ (host clock, UTC).
- Frozen head under test: `31b7a77520fb2dc6e6dd3c531f6c0a115eb9c0ba` (lane `mtabasco/ms-cp2e`, repair 2 of QA attempt 2 on `6fafac6`; code tested at `c37afc9`). Worktree `ms-qa-cp2e-3`, branch `mtabasco/ms-qa-cp2e-3`; `git rev-parse HEAD` = frozen head at start, porcelain 0 lines.
- Packet: `qa/QA-CONTRACT.md` at the frozen head; `qa/DEFECT-CLASSES.md` read from `plan/monitoring-service` (`fb23a2e`; the lane copy lacks only the D15 paragraph). Classification: **D15** (BLOCKING = missed/lost page, false clean, false page, unsafe action, data loss, fail-open gate, secret leak; everything else a NOTE). Owner scope note applied: no mutation campaign, no long rows; skips named in §2 and §3.
- Preflight: disk 335 GiB free; venv created per plan §7 (`uv venv reconcile/.venv --python 3.12`; `uv pip install --python reconcile/.venv/bin/python -r reconcile/requirements-dev.txt`, exit 0), Python 3.12.15, `reconcile/.venv` git-ignored (`git check-ignore`); anvil not needed (no fork row in CP2e).
- Frozen-file check: `git diff HEAD plan/monitoring-service -- reconcile/tests/{harness,test_framework,test_worker}.py` → 0 lines each, and 0 lines against `500abe4`; `diff <(git diff 96d1c32 HEAD -- <3 files>) <(git diff bd531b4^ 500abe4 -- <3 files>)` empty (the lane carries exactly that patch). `git diff --name-status 96d1c32 HEAD -- reconcile/`: `checks/__init__.py`, `config.example.json`, the 3 patched files (M) and the lane's own new files (`checks/m6_legacy_coverage.py`, `checks/m7_ssv_solvency.py`, `tests/fixtures_legacy.py`, `tests/test_m6.py`, `tests/test_m7.py`). Since `6fafac6` only `checks/m6_legacy_coverage.py` (`inventory_gap` + docstring), `checks/m7_ssv_solvency.py` (docstring), `tests/fixtures_legacy.py`, `tests/test_m6.py`, `tests/test_m7.py` changed in `reconcile/`. No other frozen CP1B file edited.

## Spec trigger list (re-derived from docs/ssv-specs/monitoring-service-spec.md §M6, §M7 + rulings Q1 A / Q2 A; spec unchanged since `6fafac6`, `git diff 6fafac6 HEAD -- docs/` empty)

| # | Trigger (spec line) | Severity | Firing test | Silent twin |
|---|---|---|---|---|
| M6.1 | `isLiquidatableSSV` true > 7 d (ruling: > 5000 bps × live `liquidation_threshold_period_ssv` blocks) | warn | `test_m6_liquidatable_longer_than_half_window_warns` | `test_m6_benign_liquidatable_exactly_half_window_stays_silent` |
| M6.2 | `getBalanceSSV == 0` while active (ruling: and vc > 0; 0→>0 at balance 0 pages at its block) | page | `test_m6_zero_balance_while_active_pages`, `test_m6_validator_count_zero_to_positive_at_zero_balance_pages_on_that_block` | `test_m6_benign_zero_balance_without_validators_stays_silent`, `test_m6_benign_positive_balance_not_liquidatable_stays_silent` |
| M6.3 | weekly digest: count, validators, SSV/yr burn (+ ruling: vc = 0 zero-balance clusters only here) | digest | `test_m6_weekly_digest_counts_clusters_validators_and_burn` | same test |
| M7.1 | daily `surplus < 1 SSV` | page | `test_m7_surplus_below_one_ssv_pages` | `test_m7_benign_surplus_exactly_one_ssv_does_not_page` |
| M7.2 | any change in the surplus | info | `test_m7_surplus_change_sends_info_under_one_incident` | `test_m7_benign_unchanged_surplus_sends_nothing` |

Each named test exists exactly once at the head (`grep -c "def <name>\b"` = 1 each). My own boundary probe P2 confirms M7.1 at 1 wei: `floor − 1 wei` pages, exactly the floor resolves (the LIVE surplus sits at exactly 1 SSV, so `<` is the spec and the code).

## 1. Findings (ranked)

### F1 (BLOCKING — false clean / missed page; D15) — a short SSV API page that reports its true `pagination.total` passes the completeness guard whenever the scanned logs hold at least as many clusters the API does not list: M7 takes a first-run baseline without the dropped inert legacy balances, and a later leak never pages

Defect classes 1, 6 and 10 (partial input read as complete; a count that silently drops the item the spec needs; first run without a baseline). Pre-existing since repair 1 (`inventory_gap` was added there); not introduced by repair 2 and not covered by it.

- Mechanism (`reconcile/checks/m6_legacy_coverage.py:214`): leg (2) of `inventory_gap` compares `len(inv.clusters)` — **API rows ∪ log-derived clusters** — against the API's own `pagination.total`, which counts **API rows only**. Every cluster the logs know and the API does not (any cluster registered after the API's index point; any cluster during an indexer stall) adds one unit of slack, so the API can drop that many rows while reporting its true total and the guard still says "complete". Leg (1) (`daoValidatorCount`) does not see the dropped row when it is an **inert** legacy cluster (0 validators), whose SSV balance is still an M7 claim — exactly the class leg (2) exists for (worker record, repair-1 design row "(2) covers the inert legacy clusters whose balance is still an M7 claim"). `m7_legacy` only remembers ids that were listed once, so at a first run or on a new database nothing else catches it, and while the API stays short every daily run repeats the false clean.
- Reproducer `test_p1_short_page_masked_by_a_log_only_cluster_first_run` (Appendix A): inert legacy cluster b (0 validators, 7 SSV) is the row the short page drops; burning legacy cluster a (1 validator) is listed, so `daoValidatorCount` 1 = listed 1; an ETH cluster is registered after the start block (in the scanned logs, not in the API); the API serves `[a]` with `pagination.total` 2 (`api_fault="short"`). True surplus −4 SSV. Expected: `surplus-floor` page or UNKNOWN health, no baseline. Actual: `AssertionError: (['surplus'], '3000000000000000000', [])` — an info baseline of +3 SSV, no page, no health message.
- Persistence `test_p1_masking_persists_on_the_next_daily_run`: next day a 2 SSV leak lands (true −6 SSV) → `(['surplus'], '1000000000000000000', [])`: the surplus reads exactly 1 SSV, no page.
- Control `test_p1_control_short_page_alone_is_unknown`: the same short page without the log-only cluster → UNKNOWN, no baseline (passes). So the log-only cluster is what defeats the guard.
- Command: `QA_ROOT=$PWD reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider --rootdir <probes3> <probes3>` → 2 failed, 5 passed (the 2 are F1). Same 2 fail on the `6fafac6` checks (scratch copy `frozen/`), so pre-existing.
- Mainnet exposure (measured, not argued): the SSV API at 2026-10-22T09:53:54Z (`/clusters?perPage=1000&page=1..2`, total 1,908) lists **42 active legacy clusters with 0 validators and a positive balance, 36 of them ≥ 1 SSV, 2,235.6 SSV in all (largest 685.6 SSV)**, against a surplus of exactly 1 SSV. Unlike attempt 2's F1 this needs no future state (`daoValidatorCount` 0): one dropped inert row hides a leak of up to its balance. Precondition: a first run or a new database (the deployment's data dir is not yet durable) that meets an API page short of its own total **while** at least one cluster is known only from the logs (index lag after a registration, or a stalled indexer with new registrations). The implementer's LIVE run saw 0 log-only clusters, so the coincidence is not the steady state; that bounds likelihood, not kind. The API answer is outside the lane's trust model by its own rule ("absence from the SSV API is not evidence", module docstring), and "short page with the true total" is a fault the lane itself models and claims to catch (`listing_short_of_its_total_or_without_one_is_unknown[short]`, sweep fault `short`).
- Why BLOCKING: D15 names false clean / missed page without an exposure qualifier, and attempt 2 classified an unreachable-today first-run false clean of the same input class as BLOCKING; this one is reachable with today's balances. Owner: lane (repair worker). Not a repair suggestion, only the shape of the gap: the count compared with `pagination.total` mixes two populations (API ∪ logs vs API); the listing response itself carries the `pagination.total` of the same index snapshot.
- Distinct from the implementer's recorded residual (repair 2, state table "(g) partial"): that one is a *self-consistent* partial page (`total` agrees with the rows), which neither leg can see without an on-chain legacy-cluster count. F1 is visible to the guard (API rows 1 < API total 2) and is hidden by its own arithmetic.

## 2. Claims I could NOT confirm

- Repair-1/2 claim "short page with the true total → UNKNOWN" (state table, SSV API listing (g); plan.md status line "empty/short API … UNKNOWN"): **refuted** when log-only clusters offset the missing API rows (F1). Confirmed without that offset (P1 control, the lane's `[short]` cases).
- Everything repair 2 claims, reproduced: F1-of-attempt-2 fixed — attempt-2 reproducer Q10 now **green**; the 5 new regressions are **red on the `6fafac6` checks** (scratch copy, assertion failures: `assert {'surplus': …} == {}`, `assert (0 == 1)`, info `m7-surplus-1002` emitted) and green at the head; 455 passed; ruff clean; V2x.1 101/114; V2x.2 3; V2x.3 5; V2x.4 2; reorg 21; sweep 120; LIVE guard (§3).
- N1, N2 (attempt 2 NOTEs): still present (probes Q11, Q15 fail as before), not addressed by repair 2 by design; remain NOTEs.
- Not re-run (owner scope note: long rows; no code on their path changed since attempt 1 — `git diff c02557a HEAD -- reconcile/inventory.py reconcile/eb_events.py reconcile/rpc.py reconcile/eb_reconcile.py reconcile/eb_storage.py` empty): V2e.7 both sides (attempt 1 PASS at 26,246,821: 1,026 legacy in storage = API 1,026 `migrated=false`, 0 differences; ~25 min archive log scan); LIVE M6/M7 ledger (attempt 1: surplus exactly 1.000000000000000000 SSV at 26,246,821). Mutation campaign: skipped (scope note). Subgraph cross-check: DEFERRED by the plan (no `SSV_SUBGRAPH_API_KEY`).

## 3. Verification matrix (each row run on its own, exit code per command, no `&&` credit; `P = reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider`)

| V-ID | Command | Exit | Result |
|---|---|---|---|
| V2x.1 M6 | `$P reconcile/tests/test_m6.py` | 0 | 101 passed; trigger map above complete (firing test at severity + silent twin each) |
| V2x.1 M7 | `$P reconcile/tests/test_m7.py` | 0 | 114 passed — but F1: the guard misses a short page offset by log-only clusters |
| V2x.2 | `$P reconcile/tests/test_m6.py reconcile/tests/test_m7.py -k thresholds_from_chain` | 0 | 3 passed, 212 deselected |
| V2x.3 | same files `-k restart` | 0 | 5 passed, 210 deselected |
| V2x.4 | same files `-k rpc_budget` | 0 | 2 passed, 213 deselected (M6 ≤ 3 eth_call per cluster; M7 bounded) |
| reorg | same files `-k reorg` | 0 | 21 passed, 194 deselected |
| sweep | same files `-k adversarial` | 0 | 120 passed, 95 deselected |
| V2x.5 | falsifiers, §4 | — | 7/7 matrix falsifiers + 2 QA-3 falsifiers red→green; porcelain 0 after each |
| V2x.6 | `$P reconcile/tests` | 0 | **455 passed** in 11.70 s, 0 skipped |
| V2x.6 | `reconcile/.venv/bin/ruff check reconcile` | 0 | All checks passed! |
| config | `$P reconcile/tests/test_framework.py -k config_example` | 0 | 1 passed, 31 deselected |
| required settings | `test_m6_requires_views_and_the_api`, `test_m7_requires_cssv_and_unstake_history` (inside V2x.1) | 0 | missing/empty `views`, `inventory.api_url`, `checks.m7.cssv`, `checks.m7.unstake_history_from` → `SystemExit` at construction; malformed present value still N2 |
| repair-2 regressions on frozen code | scratch: `git archive HEAD reconcile` + `reconcile/checks/m{6,7}_*.py` from `6fafac6`; `$P reconcile/tests/test_m6.py reconcile/tests/test_m7.py` | 1 | **5 failed, 210 passed**: `test_m6_consistent_empty_api_with_only_inert_legacy_clusters_is_unknown`, `test_m7_q10_consistent_empty_api_with_inert_legacy_balance`, `test_m7_empty_api_listing_is_unknown_whatever_its_total[first-0]`, `[lost_database-0]`, `test_m7_empty_api_next_to_a_cluster_from_the_logs_is_unknown` — all assertion failures; matches `workers/cp2e/evidence/repair2-red-on-frozen-code.txt` |
| attempt-1 reproducers | `QA_ROOT=$PWD $P --rootdir <probes1> <probes1>` (attempt-1 Appendix A, copied verbatim from the attempt-2 scratch) | 0 | 17 passed |
| attempt-2 reproducers | `QA_ROOT=$PWD $P --rootdir <probes2> <probes2>` | 1 | 14 probes: **12 passed, 2 failed** — Q10 (attempt-2 F1) **green**; Q11 (N1) and Q15 (N2) fail as recorded (NOTEs, not in repair scope) |
| QA-3 probes | `QA_ROOT=$PWD $P --rootdir <probes3> <probes3>` | 1 | 7 probes: **5 passed, 2 failed** (F1); §4 |
| LIVE guard (re-measured) | `reconcile/.venv/bin/python plans/monitoring-service/workers/cp2e/live_completeness.py --rpc https://ethereum-rpc.publicnode.com --out <scratch>/live/live-completeness.json` (2026-10-22T09:54:42Z–10:00:08Z) | 0 | **PASS at block 26,247,377** (endpoint publicnode, recent block): production `inventory_gap` = None; 1,908 clusters (882 ETH, 1,026 legacy, 567 legacy active, 0 invalid), **from_api 1,908, from_logs 0**; `daoValidatorCount` 346 = listed 346; `pagination.total` 1,908 = listing 1,908; controls on the same block: empty → gap, empty with total 0 → gap (new repair-2 leg), one burning legacy row dropped → gap. sha256 `45230732f0ae50c7…`. The script has no control for F1's shape (a dropped inert row offset by a log-only cluster); from_logs 0 here means the coincidence is not present at this block |
| LIVE API exposure | `curl https://api.ssv.network/api/v4/mainnet/clusters?perPage=1000&page={1,2}` (2026-10-22T09:53:54Z) | 0 | `pagination.total` 1,908 on both pages, 1,908 rows; legacy (`migrated=false`) 1,026, active 567, validators 346, active with 0 validators 464, of which **42 with balance > 0 (36 ≥ 1 SSV), Σ 2,235.6048 SSV, max 685.5676 SSV** (API-stored balance; inert clusters burn nothing) |
| V2e.7 | — | — | NOT RE-RUN (owner scope: long row; carried from attempt 1, PASS both sides at 26,246,821; §2) |
| LIVE M6/M7 ledger | — | — | NOT RE-RUN (owner scope; attempt 1 at 26,246,821; §2) |
| Subgraph cross-check | — | — | DEFERRED (plan; no key) |

## 4. Falsifiers and probes

Falsifiers (scratch `tools/falsify.py`: one exact-string mutation → named test(s) → `git checkout -- <file>` → rerun → `git status --porcelain`); every row ended with porcelain 0 lines (`fals.out`).

| ID | Mutation | Named test(s) | Mutated | Restored |
|---|---|---|---|---|
| F6a | M6 `lag > warn_blocks` → `>=` | `test_m6_benign_liquidatable_exactly_half_window_stays_silent` | 1 failed | 1 passed |
| F6b | M6 drop `and vc > 0` | `test_m6_benign_zero_balance_without_validators_stays_silent` | 1 failed | 1 passed |
| F6c | M6 `self._rewind(db, ancestor)` → `pass` | `test_m6_reorg_forgets_a_clear_observation_on_deleted_blocks` | 1 failed | 1 passed |
| F7a | M7 claims without `outstanding` | `test_m7_unstake_request_burns_cssv_and_is_counted_until_withdrawn` | 1 failed | 1 passed |
| F7b | M7 `surplus < floor` → `<=` | `test_m7_benign_surplus_exactly_one_ssv_does_not_page` | 1 failed | 1 passed |
| F7c | M7 `unknown = {}` after the ledger | `test_m7_unpriced_legacy_cluster_is_unknown_not_clean` | 1 failed | 1 passed |
| F7d | M7 `if gone:` → `if False:` | `test_m7_legacy_cluster_missing_from_the_inventory_is_unknown_not_a_smaller_liability` | 1 failed | 1 passed |
| QA3-a | repair-2 leg off: `if from_api == 0:` → `if False:` | `test_m6.py test_m7.py` | 5 failed / 215 | 215 passed |
| QA3-b | "empty" = no inventory entry: `from_api = len(inv.clusters)` | `test_m6.py test_m7.py` | 1 failed / 215 | 215 passed |

Older falsifiers (QF6d, QF7b, QA2-a…g) not re-run: their code is unchanged since attempt 2 (`git diff 6fafac6 HEAD -- reconcile/checks` touches only `inventory_gap` and two docstrings) and the owner scope excludes a campaign.

QA-3 targeted probes (scratch `probes3/test_qa3.py`, Appendix A; classes from DEFECT-CLASSES, aimed at degenerate/partial input and the floor boundary):

| Probe | Class / key attacked | Result |
|---|---|---|
| **P1** short page (true total) + one log-only ETH cluster, inert legacy row dropped, first run (M7) | 1, 6, 10 / inventory guard, baseline | **FAIL → F1** (`surplus` baseline +3 SSV, true −4 SSV) |
| **P1 persistence** next daily run after a 2 SSV leak | 1, 10 / `surplus-floor` | **FAIL → F1** (reads exactly 1 SSV, true −6 SSV, no page) |
| P1 control: the same short page without the log-only cluster | 1 / guard | pass — UNKNOWN, no baseline |
| P2 surplus `floor − 1 wei` pages; exactly the floor resolves | boundary `<` vs `<=` (LIVE value is exactly 1 SSV) | pass |
| P3 open `surplus-floor` page, then a self-consistent empty API answer (`total` 0, dao 0) whose upper bound is +91 SSV | 1 / open page under UNKNOWN | pass — page kept open, no resolve message |
| P5 first sweep, vc 2, balance 0, `getBurnRateSSV` 0 (degenerate view) | 1 / M6 zero page | pass — pages |
| P6 open M6 zero-balance page, then an `empty_total0` full sweep | 1 / M6 open key | pass — kept open, UNKNOWN health, nothing resolved |

## 5. NOTEs (recorded, not blocking; owner and reason)

- **N1 (carried from attempt 2; owner lane / CP3)** — negative `outstanding_unstake` accepted after a lossy prefix `eth_getLogs` (Q11 still fails). NOTE: unreachable with the shipped config (`start_block == unstake_history_from`).
- **N2 (carried; owner lane / CP3)** — a malformed present required setting fails at the first run as a `check-error` health message, not at construction (Q15 still fails). NOTE: loud, not fail-open.
- **N3 (new; owner lane)** — under the same F1 precondition M6's weekly digest says the legacy inventory is complete while an inert legacy cluster is unlisted (no UNKNOWN line). NOTE: digest text only; M6 warns/pages need validators, which `daoValidatorCount` covers.
- **N4 (carried from the worker record; owner coordinator / spec)** — a self-consistent partial API page (rows = `total`) missing only inert legacy clusters at a first run is invisible to both legs. NOTE: the coordinator's repair-2 ruling scoped the empty answer only, and no on-chain legacy-cluster count exists; recorded so the residual is explicit next to F1.

## 6. Evidence-plane writes

- Status transition: CP2e `IMPLEMENTED — AWAITING QA` → **`QA FAILED`** on `31b7a77` with this report (plan.md §8 CP2e Status line).
- Paths written: `plans/monitoring-service/qa/cp2e-attempt-3.md` (this report), the CP2e `**Status:**` line in `plans/monitoring-service/plan.md`, one appended event in `plans/monitoring-service/execution-log.md`. Untracked/ignored: `reconcile/.venv`. Scratch (outside the repo): `/private/tmp/claude-501/-Users-marco-orca-workspaces-ssv-network-monitor-ms-qa-cp2e-3/82420051-259f-4263-b0e0-b8e7c797a10e/scratchpad/` (`probes1/`, `probes2/` copied from attempt 2, `probes3/` this attempt, `frozen/` regression copy, `tools/falsify.py`, `fals.json`, `fals.out`, `live/`).
- No implementation repair, no `config/`/`config.zip`/`.env*` access, no mainnet transaction, no alert sent (record mode in tests), no secret used. Skipped by the owner scope note: mutation campaign, older falsifiers, V2e.7 and LIVE ledger re-runs.

## 7. Repo proof

Recorded at report time (before the commit; the report itself is the untracked file):

```
$ git status --porcelain
 M plans/monitoring-service/execution-log.md
 M plans/monitoring-service/plan.md
?? plans/monitoring-service/qa/cp2e-attempt-3.md
$ git diff --name-only 31b7a77
plans/monitoring-service/execution-log.md
plans/monitoring-service/plan.md
```

No implementation file (`reconcile/**`, `deploy/**`, config, templates) appears in either list; every falsifier ended with `git status --porcelain` at 0 lines (`fals.out`). `reconcile/.venv` is ignored and untracked. F1's probes also fail on the `6fafac6` checks (scratch copy), so F1 is pre-existing, not a regression of repair 2.

## Appendix A — QA-3 probe reproducers (scratch `probes3/`; `conftest.py` as in attempts 1–2: puts `$QA_ROOT/reconcile` and `$QA_ROOT/reconcile/tests` on `sys.path`)

Run: `QA_ROOT=$PWD reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider --rootdir <probes3> <probes3>`.

```python
from fixtures_legacy import CSSV, SSV, LegacyWorld, owner
from harness import advance, outbox

CLUSTER, OPS, NETWORK, STAKED = 100 * SSV, {1: 5 * SSV, 2: 3 * SSV}, 2 * SSV, 1_000 * SSV
BALANCED = STAKED + CLUSTER + sum(OPS.values()) + NETWORK + SSV  # surplus exactly 1 SSV

def inc(w, check, state="open"):
    return {r["key"]: dict(r) for r in w.db.conn.execute(
        "SELECT * FROM incidents WHERE check_id=? AND state=?", (check, state))}

def health(w):
    return [r["text"] for r in outbox(w, "severity='health'")]

def m7_world(tmp_path):
    lw = LegacyWorld(tmp_path, only=("m7",))
    lw.checks = {"m7": {"cssv": CSSV, "unstake_history_from": lw.chain.sealed}}
    return lw

def test_p1_short_page_masked_by_a_log_only_cluster_first_run(tmp_path):      # F1
    lw = m7_world(tmp_path)
    b = lw.legacy(owner(2), count=0)          # inert legacy, 7 SSV: the row the short page drops (rows[0])
    a = lw.legacy(owner(1), count=1)          # burning legacy, listed; daoValidatorCount 1 = listed 1
    lw.views(b, balance=7 * SSV, burn=0)
    lw.views(a, balance=CLUSTER)
    lw.ledger(proxy_ssv=BALANCED + 2 * SSV, cssv_supply=STAKED, network=NETWORK, operators=OPS)  # true -4 SSV
    lw.chain.mine()
    w = lw.start()
    lw.ssv.register(owner(9), (5, 6, 7, 8), [b"\x01" * 48])  # in the scanned logs only, not in the API
    lw.chain.mine()
    lw.api_fault = "short"                    # serves [a] with pagination.total 2
    advance(w)
    got = inc(w, "m7")
    assert "surplus-floor" in got or any("UNKNOWN" in h for h in health(w)), \
        (sorted(got), got.get("surplus", {}).get("revision"), health(w))
    # actual: (['surplus'], '3000000000000000000', [])

# test_p1_masking_persists_on_the_next_daily_run: as above, then
#     lw.ledger(proxy_ssv=BALANCED, ...)  # a 2 SSV leak: true -6 SSV
#     lw.sweep(86_400)                    # actual: (['surplus'], '1000000000000000000', [])
# test_p1_control_short_page_alone_is_unknown: as above without the register line -> UNKNOWN, no baseline (passes)
```

P2, P3, P5, P6 follow the same pattern (`api_fault="empty_total0"`, `lw.sweep`); full source in scratch `probes3/test_qa3.py`.
