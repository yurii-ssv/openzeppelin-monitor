PASS WITH NOTES

# CP2e (Legacy SSV: M6, M7) — independent QA, attempt 4 (capped, D15)

- QA: Claude Opus 5.5 (`claude-opus-5-5`), effort high (D13; attempt-4 addendum). Task `task_d5a79232b7af`, dispatch `ctx_ff8a170155e1`. Observed 2026-10-22T10:1xZ–10:4xZ (host clock, UTC).
- Frozen head under test: `45cc77462ae7e7436a18481ecd05fb193fc88f08` (lane `mtabasco/ms-cp2e`, repair 3 of QA attempt 3; code tested at `0117e54`, no code change after it). Worktree `ms-qa-cp2e-4`, branch `mtabasco/ms-qa-cp2e-4`; `git rev-parse HEAD` = frozen head at start, porcelain 0 lines.
- Packet: `qa/QA-CONTRACT.md` at the frozen head; `qa/DEFECT-CLASSES.md` read from `plan/monitoring-service`. Classification **D15** (BLOCKING = missed/lost page, false clean, false page, unsafe action, data loss, fail-open gate, secret leak; everything else a NOTE). Owner scope note applied: no mutation campaign, no long rows; skips named in §2 and §3.
- Preflight: disk 335 GiB free; venv created per plan §7 (`uv venv reconcile/.venv --python 3.12`; `uv pip install --python reconcile/.venv/bin/python -r reconcile/requirements-dev.txt`, exit 0), Python 3.12.15, `reconcile/.venv` git-ignored (`git check-ignore`); anvil not needed (no fork row in CP2e).
- Frozen-file check: `git diff HEAD plan/monitoring-service -- reconcile/tests/{harness,test_framework,test_worker}.py` → 0 lines each, and 0 lines against `500abe4`; `diff <(git diff 96d1c32 HEAD -- <3 files>) <(git diff bd531b4^ 500abe4 -- <3 files>)` empty (the lane carries exactly that patch). `git diff --name-status 96d1c32 HEAD -- reconcile/`: `checks/__init__.py`, `config.example.json`, the 3 patched files (M), and the lane's own new files (`checks/m6_legacy_coverage.py`, `checks/m7_ssv_solvency.py`, `tests/fixtures_legacy.py`, `tests/test_m6.py`, `tests/test_m7.py`). Since `31b7a77` only `checks/m6_legacy_coverage.py` (leg (2) of `inventory_gap` + docstrings), `checks/m7_ssv_solvency.py` (docstring), `tests/test_m6.py`, `tests/test_m7.py` changed in `reconcile/`; nothing in `deploy/` or config. No other frozen CP1B file edited.

## Spec trigger list (re-derived from docs/ssv-specs/monitoring-service-spec.md §M6 l.200, §M7 l.222 + rulings; `git diff 31b7a77 HEAD -- docs/` empty)

| # | Trigger (spec line) | Severity | Firing test | Silent twin |
|---|---|---|---|---|
| M6.1 | `isLiquidatableSSV` true > 7 d (ruling: > 5000 bps × live `liquidation_threshold_period_ssv` blocks) | warn | `test_m6_liquidatable_longer_than_half_window_warns` | `test_m6_benign_liquidatable_exactly_half_window_stays_silent` |
| M6.2 | `getBalanceSSV == 0` while active (ruling: and vc > 0; 0→>0 at balance 0 pages at its block) | page | `test_m6_zero_balance_while_active_pages`, `test_m6_validator_count_zero_to_positive_at_zero_balance_pages_on_that_block` | `test_m6_benign_zero_balance_without_validators_stays_silent`, `test_m6_benign_positive_balance_not_liquidatable_stays_silent` |
| M6.3 | weekly digest: count, validators, SSV/yr burn (ruling: vc = 0 zero-balance clusters only here) | digest | `test_m6_weekly_digest_counts_clusters_validators_and_burn` | same test |
| M7.1 | daily `surplus < 1 SSV` | page | `test_m7_surplus_below_one_ssv_pages` | `test_m7_benign_surplus_exactly_one_ssv_does_not_page` |
| M7.2 | any change in the surplus | info | `test_m7_surplus_change_sends_info_under_one_incident` | `test_m7_benign_unchanged_surplus_sends_nothing` |

Each named test exists exactly once at the head (`grep -c "def <name>\b"` = 1 each). `>` vs `>=` at the LIVE floor (surplus exactly 1 SSV): attempt-3 probe P2 re-run green (floor − 1 wei pages, exactly the floor resolves); F7b red→green.

## 1. Findings (ranked)

No BLOCKING finding. Attempt 3's F1 is fixed: its reproducers P1 and P1-persistence are green, and the 7 repair-3 regressions are red on the `31b7a77` checks.

### N5 (NOTE, new; owner lane / coordinator): an API row whose identity is corrupted fills the slot of the inert legacy row it should name, inside the recorded residual's envelope

- Mechanism (`reconcile/checks/m6_legacy_coverage.py:212`, `reconcile/inventory.py:107-117`): `from_api` counts every inventory entry with `"api"` in `sources`, including an entry whose `keccak(owner, operators)` has no storage word (`version None`). If the API serves cluster b's row with wrong `operators`/`ownerAddress` (its own `clusterId` still names b), the row counts toward `pagination.total` while b is missing under its real id. `build()` already logs `SSV API clusterId … does not match keccak(owner, operators)` and the storage read already shows the computed id has no cluster, but neither signal feeds `inventory_gap`.
- Reproducer `test_r1_corrupted_identity_row_fills_the_total_first_run` (Appendix A): P1's world (inert legacy b with 0 validators and 7 SSV, burning legacy a with 1 validator, true surplus −4 SSV). The API serves `[b with operators (1,2,3,5), a]` with `pagination.total` 2. Expected: UNKNOWN health or a `surplus-floor` page. Actual: `AssertionError: (['surplus'], '3000000000000000000', [])`, an info baseline of +3 SSV with no page and no health message. Control `test_r1_control_dropping_the_row_is_unknown` (same page with the row omitted, total 2) → UNKNOWN (passes).
- Why NOTE and not BLOCKING (the addendum's test: a second path the recorded residual does not cover): the page meets every condition of the recorded residual (worker record "Repair 3", QA-3 N4). Its rows (2) equal `pagination.total` (2), only an inert legacy cluster is missing, `daoValidatorCount` matches, and it happens at a first run or on a new database. It needs an API data fault (a corrupted cluster identity), not a normal chain condition. Attempt-3 F1 was BLOCKING because it needed only one API fault plus ordinary index lag. Here the API alone has to be wrong: cluster identity (owner, operator ids) is immutable on chain, so an honest-but-stale API cannot produce this row. Measured on mainnet now: 0 such rows. The LIVE guard below finds `absent` 0 among 1,908 rows (attempt 1 and the implementer's run likewise 0 absent). API ids run 1,385…3,292 with no hole (= `total` 1,908), sorted by id ascending (`/clusters?perPage=3&page=1` and `page=636`, 2026-10-22T10:19Z).
- What separates it from N4: N4 has no signal anywhere. N5 has two signals the inventory already holds, at no extra RPC cost: the computed id has no storage word, and the clusterId mismatch is already logged. Counting only API rows whose computed id has a storage word, or treating either signal as a gap, would close it. On mainnet that would add no false UNKNOWN (absent 0). Recorded so the coordinator can overrule toward BLOCKING if it judges a corrupted-identity API row as reachable as the residual's missing row.

### Other NOTEs

- **N6 (new; owner lane, coverage only)**: leg (1) is pinned in one direction only. My falsifier QA4-b (`listed_vc != dao` → `listed_vc < dao`) leaves all 323 M6/M7 tests green. NOTE: listed validators can exceed `daoValidatorCount` only if the chain itself is inconsistent (tuples are storage-validated, phantom rows have no version), so no false clean follows. This is a test-coverage gap only.
- **N7 (new; owner lane, documented side effect)**: when a cluster is indexed between the cached listing (≤ `api_refresh_s`) and the fresh `pagination.total`, the run is UNKNOWN for that one run. Probe R4 confirms it still pages when the listed claims already put the surplus below the floor (upper-bound rule). NOTE: transient and fail-safe; it can delay only a baseline, info or resolve.
- **N1, N2 (carried from attempt 2; owner lane / CP3)**: Q11 (negative outstanding after a lossy prefix getLogs; unreachable with the shipped config) and Q15 (a malformed present required setting fails at the first run as `check-error`, not at construction) still fail as recorded. Both remain NOTEs (not fail-open).
- **N3 (attempt 3)**: closed. Repair 3 sets `inventory_gap` for the M6 digest in that shape (`test_m6_short_page_offset_by_a_log_only_cluster_is_unknown`, red on `31b7a77`).
- **N4 (carried, the recorded residual; owner coordinator / spec)**: a self-consistent partial page missing only inert legacy clusters at a first run is invisible to both legs. NOTE per the addendum. No second path found, apart from the variant N5 inside its envelope (§4 lists the paths I tried).

## 2. Claims I could NOT confirm

- None of repair 3's claims was refuted. Reproduced: 563 passed; V2x.1 150/173; V2x.2 3; V2x.3 5; V2x.4 2; reorg 23; sweep 216; ruff clean (both commands); 7 regressions red on `31b7a77` (exact list matches `workers/cp2e/evidence/repair3-red-on-frozen-code.txt`); probes1 17/17, probes2 12/14 (Q11/Q15 = N1/N2), probes3 7/7; falsifiers R5a/R5b and the matrix F6a–c, F7a–d red→green; LIVE guard (§3).
- The implementer's "honest limit" is confirmed by reading, not run separately: the 96 `short_masked` sweep cases apply the fault after a baseline, so they are green on `31b7a77` too (they ran green inside the 216). The first-run shape is pinned by the 7 red tests.
- Not re-run (owner scope: long rows; nothing on their path changed since attempt 1: `git diff c02557a HEAD -- reconcile/inventory.py reconcile/eb_events.py reconcile/rpc.py reconcile/eb_reconcile.py reconcile/eb_storage.py` is empty, and repair 3 changes only the guard): **V2e.7** both sides (attempt 1 PASS at 26,246,821: 1,026 legacy in storage = API 1,026 `migrated=false`, 0 differences; ~25 min archive log scan) and the **LIVE M6/M7 ledger** (attempt 1: surplus exactly 1.000000000000000000 SSV at 26,246,821). Mutation campaign: skipped (scope note). Older falsifiers (QF6d, QF7b, R1a–e, R2a, R3a–c, R4a/b, QA2-*, QA3-*): not re-run, their code is unchanged since attempt 3 (`git diff 31b7a77 HEAD -- reconcile/checks` touches only the leg-(2) line and docstrings). Subgraph cross-check: DEFERRED by the plan (no `SSV_SUBGRAPH_API_KEY`).

## 3. Verification matrix (each row run on its own, exit code per command, no `&&` credit; `P = reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider`)

| V-ID | Command | Exit | Result |
|---|---|---|---|
| V2x.1 M6 | `$P reconcile/tests/test_m6.py` | 0 | 150 passed; trigger map above complete |
| V2x.1 M7 | `$P reconcile/tests/test_m7.py` | 0 | 173 passed |
| V2x.2 | `$P reconcile/tests/test_m6.py reconcile/tests/test_m7.py -k thresholds_from_chain` | 0 | 3 passed, 320 deselected |
| V2x.3 | same files `-k restart` | 0 | 5 passed, 318 deselected |
| V2x.4 | same files `-k rpc_budget` | 0 | 2 passed, 321 deselected (M6 ≤ 3 eth_call per cluster; M7 bounded) |
| reorg | same files `-k reorg` | 0 | 23 passed, 300 deselected |
| sweep | same files `-k adversarial` | 0 | 216 passed, 107 deselected (120 seeded + 96 `short_masked`) |
| V2x.5 | falsifiers, §4 | — | 9 matrix falsifiers + 3 of mine run: 11 red→green, QA4-b survives (N6); porcelain 0 after each |
| V2x.6 | `$P reconcile/tests` | 0 | **563 passed** in 12.61 s, 0 skipped |
| V2x.6 | `reconcile/.venv/bin/ruff check reconcile`; `… --config reconcile/ruff.toml plans/monitoring-service/workers/cp2e/*.py` | 0, 0 | All checks passed! (both) |
| config | `$P reconcile/tests/test_framework.py -k config_example` | 0 | 1 passed, 31 deselected |
| required settings | same files `-k "requires_views_and_the_api or requires_cssv_and_unstake_history"` | 0 | 2 passed (missing/empty `views`, `inventory.api_url`, `checks.m7.cssv`, `checks.m7.unstake_history_from` → `SystemExit` at construction); malformed present value still N2 |
| repair-3 regressions on frozen code | scratch `frozen/`: `git archive HEAD reconcile` + `reconcile/checks/m{6,7}_*.py` from `31b7a77`; `$P reconcile/tests/test_m6.py reconcile/tests/test_m7.py` | 1 | **7 failed, 316 passed**: `test_m6_short_page_offset_by_a_log_only_cluster_is_unknown`, `test_m7_p1_short_page_masked_by_a_log_only_cluster_first_run`, `test_m7_p1_masking_persists_on_the_next_daily_run`, `…_at_every_point[first-1]`, `[first-2]`, `[lost_database-1]`, `[lost_database-2]`. All are assertion failures (`Block 1003: surplus 3 SSV (first evaluation)`, `Block 1004: surplus 1 SSV (previous 3 SSV, change -2 SSV)`), with no collection error |
| attempt-1 reproducers | `QA_ROOT=$PWD $P --rootdir <probes1> <probes1>` (byte-identical copy of attempt 3's scratch) | 0 | 17 passed |
| attempt-2 reproducers | `QA_ROOT=$PWD $P --rootdir <probes2> <probes2>` | 1 | 14 probes: 12 passed, 2 failed = Q11 (N1), Q15 (N2), as recorded |
| attempt-3 reproducers | `QA_ROOT=$PWD $P --rootdir <probes3> <probes3>` | 0 | **7 passed** (P1 and P1-persistence, F1, now green) |
| QA-4 probes | `QA_ROOT=$PWD $P --rootdir <probes4> <probes4>` | 1 | 5 probes: 4 passed, 1 failed (R1 = N5); §4 |
| LIVE guard (re-measured) | `reconcile/.venv/bin/python plans/monitoring-service/workers/cp2e/live_completeness.py --rpc https://ethereum-rpc.publicnode.com --out <scratch>/live/live-completeness.json` | 0 | **PASS at block 26,247,511** (endpoint publicnode, recent block; 2026-10-22T10:21:57Z–10:30:45Z): production `inventory_gap` = None; 1,908 clusters (882 ETH, 1,026 legacy, 567 legacy active, **absent 0**), from_api 1,908, from_logs 0; `daoValidatorCount` 346 = listed 346; `pagination.total` 1,908 = distinct API rows 1,908, so the repaired leg raises no false UNKNOWN on mainnet. Controls on the same block all gap: empty; empty with total 0; one burning legacy row dropped (306 vs 346); F1 shape (inert legacy row `0x0012…ca27` dropped plus one log-only cluster) → "listed 1907 clusters of its pagination.total 1908". sha256 `3cab10d3baf898c6…`. Observation: **4 invalid** tuples this time (attempts 1–3 and the implementer's run saw 0). These are API rows whose tuple no longer hashes to storage, the normal index lag after a cluster event. The script scans no logs, and in production the worker's stored logs supply the fresh tuple. M7 treats a non-validating legacy tuple as UNKNOWN (F7c), which is fail-safe. Leg (1) still agrees, so none of the 4 is an active legacy cluster holding validators |
| LIVE API shape | `curl …/clusters?perPage=1&page=1` ×2, `perPage=3&page={1,636}` (2026-10-22T10:18:55Z–10:19Z) | 0 | `pagination.total` 1,908 on all; no cache headers (only `Date`, weak `ETag`), so I found no stale-total path; rows sorted by `id` ascending, ids 1,385…3,292 (contiguous, = total), so rows are appended and the listing has had no deletion that could shift pagination |
| V2e.7 | — | — | NOT RE-RUN (owner scope: long row; carried from attempt 1, PASS both sides at 26,246,821; §2) |
| LIVE M6/M7 ledger | — | — | NOT RE-RUN (owner scope; attempt 1 at 26,246,821; §2) |
| Subgraph cross-check | — | — | DEFERRED (plan; no key) |

## 4. Falsifiers and probes

Falsifiers (scratch `tools/falsify.py`, same tool as attempt 3: one exact-string mutation → named test(s) → `git checkout -- <file>` → rerun → `git status --porcelain`). Every row ended with porcelain 0 lines (`fals4.out`).

| ID | Mutation | Named test(s) | Mutated | Restored |
|---|---|---|---|---|
| F6a | M6 `lag > warn_blocks` → `>=` | `test_m6_benign_liquidatable_exactly_half_window_stays_silent` | 1 failed | 1 passed |
| F6b | M6 drop `and vc > 0` | `test_m6_benign_zero_balance_without_validators_stays_silent` | 1 failed | 1 passed |
| F6c | M6 `self._rewind(db, ancestor)` → `pass` | `test_m6_reorg_forgets_a_clear_observation_on_deleted_blocks` | 1 failed | 1 passed |
| F7a | M7 claims without `outstanding` | `test_m7_unstake_request_burns_cssv_and_is_counted_until_withdrawn` | 1 failed | 1 passed |
| F7b | M7 `surplus < floor` → `<=` | `test_m7_benign_surplus_exactly_one_ssv_does_not_page` | 1 failed | 1 passed |
| F7c | M7 `unknown = {}` after the ledger | `test_m7_unpriced_legacy_cluster_is_unknown_not_clean` | 1 failed | 1 passed |
| F7d | M7 `if gone:` → `if False:` | `test_m7_legacy_cluster_missing_from_the_inventory_is_unknown_not_a_smaller_liability` | 1 failed | 1 passed |
| R5a (F1 back) | `elif from_api < total:` → `elif len(inv.clusters) < total:` | `test_m6.py test_m7.py` | 7 failed / 323 | 323 passed |
| R5b | `elif from_api < total:` → `elif False:` | `test_m6.py test_m7.py` | 10 failed / 323 | 323 passed |
| QA4-a (mine) | off-by-one tolerance `from_api < total - 1` | `test_m6.py test_m7.py` | 10 failed / 323 | 323 passed |
| QA4-b (mine) | leg (1) one-sided `listed_vc < dao` | `test_m6.py test_m7.py` | **0 failed (survives) → N6** | 323 passed |
| QA4-c (mine) | M7 `if legacy and last_op == 0:` → `if False:` | `test_m7_operator_count_zero_next_to_legacy_clusters_is_unknown` | 1 failed | 1 passed |

QA-4 targeted probes (scratch `probes4/test_qa4.py`, Appendix A), aimed at a second path past the repaired leg (2) and at the repair's side effect:

| Probe | Class / key attacked | Result |
|---|---|---|
| **R1** API serves b's row with a corrupted identity (operators that hash to no cluster), total = rows, first M7 run | 1, 6 / `from_api` counts a row with no storage word | **FAIL → N5** (baseline +3 SSV, true −4 SSV) |
| R1 control: the same page without that row, total 2 | 1 / leg (2) | pass, UNKNOWN |
| R2 ghost row (no storage word, counted in the API's own total 3) next to a short page that drops b | 1 / a ghost does not offset a dropped row | pass, UNKNOWN (2 < 3) |
| R3 M6: corrupted-identity row of a cluster with 2 validators at balance 0 | 1 / leg (1) | pass, UNKNOWN health (`daoValidatorCount` 3 vs listed 1) |
| R4 cluster indexed between cached listing and fresh total, listed claims put surplus at −2 SSV | side effect N7 / upper-bound page | pass, pages and reports UNKNOWN health |

Paths for a second masking route I examined and found closed (reasoning backed by the probes and API measurements above):
- A pagination race. Rows are sorted by immutable `id` and appended, so a registration cannot shift earlier pages. A shifted row produces an exact duplicate, which dedupes, so `from_api < total`.
- A stale `pagination.total`. The total is read after the listing, and the API sends no cache headers.
- A reorg ghost row. It is counted in the API's own total (R2).
- Duplicate rows. They collapse by computed id (attempt-2 Q2/Q13 green).
- A log-only legacy cluster. The logs know it, so it is priced.
- A corrupted identity row. Open, recorded as N5.

## 5. Evidence-plane writes

- Status transition: CP2e `IMPLEMENTED — AWAITING QA` → **`VERIFIED`** on `45cc774` with NOTEs N1, N2, N4–N7, by this report (plan.md §8 CP2e Status line). The NOTEs belong in plan §8 "Recorded notes" with their owners. That is a coordinator write, outside my write scope.
- Paths written: `plans/monitoring-service/qa/cp2e-attempt-4.md` (this report), the CP2e `**Status:**` line in `plans/monitoring-service/plan.md`, one appended event in `plans/monitoring-service/execution-log.md`. Untracked/ignored: `reconcile/.venv`. Scratch (outside the repo): `/private/tmp/claude-501/-Users-marco-orca-workspaces-ssv-network-monitor-ms-qa-cp2e-4/a787db3a-2eaa-4173-b70b-b20d485b9bae/scratchpad/` (`probes1-3/` copied from attempt 3, `probes4/` this attempt, `frozen/` regression copy, `tools/falsify.py`, `fals4.json`, `fals4.out`, `api/`, `live/`).
- None of the following happened: an implementation repair, any access to `config/`, `config.zip` or `.env*`, a mainnet transaction, a real alert (tests use record mode), or use of a secret. Skipped under the owner scope note: the mutation campaign, the older falsifiers, and the V2e.7 and LIVE ledger re-runs.

## 6. Repo proof

Recorded at report time (before the commit; the report itself is the untracked file):

```
$ git status --porcelain
 M plans/monitoring-service/execution-log.md
 M plans/monitoring-service/plan.md
?? plans/monitoring-service/qa/cp2e-attempt-4.md
$ git diff --name-only 45cc774
plans/monitoring-service/execution-log.md
plans/monitoring-service/plan.md
```

Neither list contains an implementation file (`reconcile/**`, `deploy/**`, config, templates). Every falsifier ended with `git status --porcelain` at 0 lines (`fals4.out`). `reconcile/.venv` is ignored and untracked.

## Appendix A — QA-4 probe reproducers (scratch `probes4/`; `conftest.py` as in attempts 1–3: puts `$QA_ROOT/reconcile` and `$QA_ROOT/reconcile/tests` on `sys.path`)

Run: `QA_ROOT=$PWD reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider --rootdir <probes4> <probes4>`.

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

def p1_world(tmp_path):
    lw = LegacyWorld(tmp_path, only=("m7",))
    lw.checks = {"m7": {"cssv": CSSV, "unstake_history_from": lw.chain.sealed}}
    b = lw.legacy(owner(2), count=0)          # inert legacy, 7 SSV (rows[0])
    a = lw.legacy(owner(1), count=1)          # burning legacy; daoValidatorCount 1 = listed 1
    lw.views(b, balance=7 * SSV, burn=0)
    lw.views(a, balance=CLUSTER)
    lw.ledger(proxy_ssv=BALANCED + 2 * SSV, cssv_supply=STAKED, network=NETWORK, operators=OPS)  # true -4 SSV
    lw.chain.mine()
    return lw, a, b

def test_r1_corrupted_identity_row_fills_the_total_first_run(tmp_path):     # N5
    lw, a, b = p1_world(tmp_path)
    w = lw.start()
    lw.rows[0] = {**lw.rows[0], "operators": [1, 2, 3, 5]}  # b's row (clusterId still b), wrong operator id
    advance(w)
    got = inc(w, "m7")
    assert "surplus-floor" in got or any("UNKNOWN" in h for h in health(w)), \
        (sorted(got), got.get("surplus", {}).get("revision"), health(w))
    # actual: (['surplus'], '3000000000000000000', [])

# test_r1_control_dropping_the_row_is_unknown: lw.api_total = 2; lw.rows[0] = None -> UNKNOWN (passes)
# test_r2_ghost_row_does_not_offset_a_dropped_row: rows += [a's row with ownerAddress owner(77)], api_total 3,
#     rows[0] = None -> UNKNOWN (passes)
# test_r3_m6_corrupted_identity_row_with_validators_is_unknown: M6, cluster with 2 validators at balance 0,
#     its row's operators corrupted -> UNKNOWN health (passes)
# test_r4_transient_total_gap_still_pages_below_the_floor: api_total = rows + 1, surplus -2 SSV -> page +
#     UNKNOWN health (passes)
```
