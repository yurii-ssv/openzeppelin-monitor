PASS WITH NOTES

# CP2a QA attempt 3 (after repair round 2) — Liquidation family (M3, M4, M14)

Independent QA, frozen head `2b434cf99a9d33371039cd93ab273f4de852fee3` (lane `mtabasco/ms-cp2a`), worktree
`ms-qa-cp2a-3`, branch `mtabasco/ms-qa-cp2a-3`, 2026-10-22T08:0x–08:45Z. Task `task_3a589013815e`, dispatch
`ctx_e7ec763575a8`. QA model: Claude Opus 5.5 (`claude-opus-5-5`), effort high (D13). No implementation repair.
Round policy: CAPPED (third QA of the lane). Every finding is BLOCKING or NOTE, with the reason.

Conventions: run from the worktree root; `P=reconcile/.venv/bin/python`; scratch `S=/tmp/cp2a-qa3`. My probes are
`$S/qa3/test_qa3.py` (Appendix A, verbatim). Attempt 2's probes and falsifier runner, and attempt 1's Appendix A, were
re-extracted byte for byte from `qa/cp2a-attempt-2.md` and `qa/cp2a-attempt-1.md` into `$S/qa2/` and `$S/appA/`
(`cmp` equal to the copies the implementer used in `/tmp/cp2a-qa2`). The frozen overlay `$S/frozen` is
`git archive 92d8ff9 reconcile` with the HEAD tests, `fixtures_liquidation.py` and sweep copied in.

Summary: every implementer number reproduces (555 tests, ruff clean, V rows, sweep 150, 25 of 26 new cases and 61 of
150 sweep cases red on `92d8ff9`). Attempt 2's H1 is fixed and the M1 ruling is implemented (its reproducers are green
here and red on `92d8ff9`). I attacked classes 4 and 1 explicitly on every persisted key and found no blocking defect:
every 24 h window in M14 is chain time, and degenerate inputs give UNKNOWN or hold. The one exception is a node with no
`transactionIndex`, which I record as a NOTE. The new hold logic has one edge, also a NOTE: M3 keeps an onset from a
chain that a lagging node hid being replaced. It survives the hold and can escalate a genuinely liquidatable cluster to
warn early. No finding is BLOCKING, so the verdict is PASS WITH NOTES.

Scope under the owner's instruction (coordinator message `msg_71f30394d11c`, received mid-run): no mutation campaigns
and no long LIVE sweeps. The implementer's falsifier runner (60 rows) and attempt 2's 3 rows had already run, in about
one minute, before the message arrived. I report them. I ran no further mutations and wrote no QA-3 falsifiers of my
own (owner override of the contract's "one own falsifier per monitor"; the three QA2 rows, one per monitor, are
re-measured instead). M4 LIVE was not run (see §2).

## 1. Findings

No BLOCKING finding.

### N-A (NOTE) — M3: the repair-2 hold keeps an onset from a chain that the node later shows was replaced, and escalates a liquidatable cluster to warn on an overstated age

- Class: 2 (reorg; shorter replacement chain), on the new repair-2 hold.
- NOTE because the cluster really is liquidatable on the canonical chain; only the age, and so when warn fires, is
  wrong. Reaching it needs a node lagging below the fork point at the moment of a real fork. The winning branch must
  also contain a deposit that lifts the cluster above its threshold and lets it re-cross before the held block, which
  takes a deposit within (fork depth) blocks of burn of the shortfall. That is not a realistic mainnet event. The
  coordinator may overrule toward BLOCKING.
- Reproducer: `$P -m pytest -q -p no:cacheprovider -s $S/qa3/test_qa3.py::test_m3_hold_keeps_an_onset_from_a_chain_that_a_lagging_node_hid_being_replaced`
  gives exit 1 on `2b434cf`. The same test passes on the `92d8ff9` overlay (11/11 there).
- Scenario: `confirmations = 0`, N = 50, N2 = 7,200. Onset O = 1007, info; the cluster was last evaluated at
  L = 1047. Then one step against a node lagging below the onset (head 1004). This is a reorg run with no replaced block
  visible, so the row is held (`hold_block` 1047, onset 1007 kept). The real chain then turns out to be another chain
  from O−2 = 1005: a top-up of 36 blocks of burn at 1005 makes the cluster liquidatable again only from about 1043.
  Mine to 1059.
- Expected: onset re-observed on the canonical chain, age ≈ 16, info. On `92d8ff9`: `lived info (1043, 16)`.
- Actual on `2b434cf`: `lived warn (1007, 52)`, `sent = [('open','info'), ('escalate','warn')]`. That is a Telegram warn
  36 blocks early, saying "still liquidatable after 52 blocks".
- Mechanism: `rewind` holds the row and keeps its onset (`m3_liquidation_zone.py:164–174`). `forget_runs` deletes the
  `liq_runs` hashes above the ancestor in that same reorg run (`m3_views.py:249–251`). When the node later serves the
  other chain, nothing can prove the held evidence left the chain, and the framework sees no second reorg: its blocks
  above the ancestor were deleted too. When the hold ends at `block >= hold_block`, `liquidatable()` uses the kept onset.
  M4 (a fresh scan) and M14 (hits re-derived from the hash-checked inbox) have no stale input at the end of a hold, so
  they are not affected.
- Suggested owner and fix (repair or CP3): keep the `liq_runs` hashes of a held row's evidence, and re-probe them when
  the hold ends. If one now differs, apply the reorg rules (re-observe the onset) instead of the kept state.

### N-B (NOTE) — M14: a missing `transactionIndex` is read as "the deposit came first" (class 1, degenerate input as positive proof)

- NOTE because the result is an info, labelled "order unknown" in the hit (`ordering`) and in the message body. It is
  only reachable with a node that returns a mined, confirmed transaction without `transactionIndex`, and conforming
  clients report null only for pending transactions. The design has been documented since attempt 1. Three such
  deposits in 24 h would still warn.
- Reproducer: `…/test_qa3.py::test_m14_c1_no_transaction_index_and_the_only_same_block_deposit_after_the_failure`
  passes, and prints `hits [('0x00000000', 'unknown')] sent [('open', 'info')]`. The only dust deposit is after the
  failed liquidation in block B (truth: not a front-run), yet a hit is recorded.
- Code: `m14_deposit_frontrun.py:221–225`: with one cluster event in B it is assumed to come before. With several, the
  check already returns UNKNOWN.
- Suggested owner: repair or CP3. Treat a missing index with a same-block event as UNKNOWN (pending), as for several
  events.

### Carried NOTEs (unchanged by repair 2; re-checked in the code)

- N-C (class 4, M3 pre-alert). "Projected to cross within the next 24 h" is `ahead <= pre_alert_blocks` (7,200)
  (`m3_liquidation_zone.py:249`). No more than 7,200 slots fit in 24 h, so every cluster that crosses within 24 h is
  listed. The list is a superset (it can include crossings up to about 24 h plus missed slots), so no line is missed.
  The digest says "within 7200 blocks". Attempt 2 recorded this as N2.
- N-D (attempt 2 N1). The M3 onset at first observation is a lower bound, as the alert says.
- N-E (attempt 2 N3). Fail-loud stalls have no time-out: an entry that is never served raises on every step.
- N-F (implementer's framework note, not measured by me). A page silently lowered to info and later resolved sends no
  resolution (`alerts._apply_one`, CP1B). M3 can still lower a page in normal flow (a partial top-up after a zero page).
  Owner: CP3/framework.

## 2. Claims I could NOT confirm, and judgements the packet asks for

- **M4 LIVE: NOT RUN.** This follows the owner's instruction: no long LIVE sweeps, and a full scan needs about 2,400
  `eth_call`. The equation is unchanged: `git diff 1154333 HEAD -- reconcile/checks/m4_conservation_deficit.py` touches
  only the hold, `record_run` and the docstring, and `scan()` is byte-identical. `m4_probe.py` is unchanged since
  `7211819`. Attempt 1's LIVE re-derivation (publicnode block 26,241,506: A surplus +457,800,000 wei; adding the pool
  gives −208.6 ETH) stands under the coordinator ruling, which I did not re-litigate. Wei-exactness was re-measured
  offline: attempt-1 `test_m4_exact_wei[-1,0,1]` passes, and QA2-F3 (`deficit > 0` → `> 1`) is red.
- **Legacy `liquidateSSV` (H3): moot at the pin, confirmed again from source.**
  `git -C /Users/marco/ssv/repos/ssv-network show f7ac487:contracts/modules/SSVClusters.sol`, lines 186–200: `deposit`
  calls `validateClusterVersion(version, VERSION_ETH)` before it emits `ClusterDeposited`, and that is the only
  `emit ClusterDeposited` in the non-test contracts at `f7ac487`. Attempt 2's LIVE probe stands; I did not re-run it.
- **M14 window vs the spec's 24 h.** The window is `window_s` = 86,400 s of header time, anchored at the checkpoint
  header, so it matches the spec. I measured it: the window decays after 24 h of missed slots inside 2 blocks; the
  boundary is exact (86,399 s counts, 86,400 s does not); and two entries 30 h old, delivered in the same first run as a
  fresh one, do not count.
- **Implementer LIVE probes**: none were claimed for repair 2 ("NOT RUN: LIVE"), so none were re-run.
- **Everything else claimed was reproduced** (§3).

## 3. Verification matrix

Every command was run on its own; nothing is credited from an `&&` chain.

| V-ID | Command | Exit / measured | Result |
|---|---|---|---|
| Preflight | `git rev-parse HEAD`; `git status --porcelain`; `df -h .` | `2b434cf…` = frozen head; clean; 339 GiB free | PASS |
| Env | `uv venv reconcile/.venv --python 3.12`; `uv pip install --python reconcile/.venv/bin/python -r reconcile/requirements-dev.txt`; `$P --version` | 0; Python 3.12.15, pytest 9.1.1, ruff 0.17.1 (authorized, untracked) | PASS |
| Frozen patch | `diff <(git diff 96d1c32 HEAD -- reconcile/tests/{harness,test_framework,test_worker}.py) <(git diff bd531b4~1 500abe4 -- …)` | identical (`PATCH-IDENTICAL`) | PASS |
| Frozen files | `git diff --name-only 96d1c32 HEAD` | reconcile: `checks/__init__.py` (one import line), `config.example.json` (keys `m3`/`m4`/`m14`), m3/m4/m14 modules, `m3_views.py`, the lane's tests/fixtures, the new `test_liquidation_sweep.py` (accepted; edits nothing frozen) and the 3 patch files; plus plan docs. No other CP1B file; README untouched | PASS |
| Repair-2 test edits | `git diff 1154333 HEAD -- reconcile/tests/test_m{3,4,14}.py` | additions only (no removed or edited lines; collected ids: none removed); fixture adds `lagging_node` only; sweep invariants extended (held keys allowed, final lived vs fresh relaxed only for held UNKNOWN entries) | PASS |
| V2a.1 (V2x.1) | `$P -m pytest -q -p no:cacheprovider reconcile/tests/test_m3.py` / `test_m4.py` / `test_m14.py` | 0/0/0; 59 / 33 / 73 passed | PASS |
| sweep | `… reconcile/tests/test_liquidation_sweep.py` | 0; 150 passed | PASS |
| V2a.2 (V2x.2) | `… test_m{3,4,14}.py -k thresholds_from_chain` | 0/0/0; 5 / 2 / 3 | PASS |
| V2a.2 independent | `$P -m pytest -q $S/qa2/test_qa2.py` (N/N2 (5,9),(1,1),(60,61), N > N2 startup error, LTP change on the fake chain) and `$S/qa3/test_qa3.py::test_m14_c11_burn_change_on_the_fake_chain_moves_the_dust_line` | 0; pass | PASS |
| V2a.3 (V2x.3) | `… -k restart` per file | 0/0/0; 4 / 2 / 5 | PASS |
| V2a.4 (V2x.4) | `… -k rpc_budget` per file | 0/0/0; 1 / 1 / 1 | PASS |
| V2a.4 independent (350) | `$S/qa2/test_qa2.py -k rpc_budget`; `$S/qa3/test_qa3.py -k budget` | 0; M3 ≤ 3 × 350 + 1 eth_call (first/near/full); M14 4 eth_call per entry with a 350 inventory; **held reorg runs**: M3 1,051 eth_call + 1 header, M4 393 eth_call + 1 header + 1 storage + 1 balance (bound 350 + 40 + 3) | PASS |
| V2a.5 (V2x.5) | `$P plans/monitoring-service/workers/cp2a/repair2/mutate.py $S/appA/test_independent.py` | 0; 60/60 rows pytest exit 1, all `restored: true`, porcelain `''` | PASS (run before the owner's no-mutation note) |
| V2a.5 QA2 rows | `$P $S/qa2/mutate_qa2.py` (paths re-pointed to `$S`) | 0; 3/3 red, restored, porcelain `''` | PASS |
| V2a.6 (V2x.6) | `$P -m pytest -q -p no:cacheprovider reconcile/tests`; `reconcile/.venv/bin/ruff check reconcile` | 0; 555 passed in 22.0 s; 0, "All checks passed!" | PASS |
| V2a.7 | `… test_m14.py -k v2a7` | 0; 9 passed | PASS (spec correlation incl. ruling: §3 ledger) |
| QA2 reproducers | `$P -m pytest -q $S/qa2/test_qa2.py` | 0; 22 passed (H1, M1 green); on `$S/frozen`: 2 failed (H1, M1) / 20 passed | H1 fixed, M1 ruling applied |
| QA1 App. A | `$P -m pytest -q $S/appA/test_independent.py` | 0; 35 passed | PASS |
| RED on frozen | `$S/frozen`: `pytest test_m{3,4,14}.py -k qa2_ -rA`; sweep | new cases 25 failed / 1 passed, set equal to `repair2/red-on-frozen.txt` (`diff` empty); sweep 61 failed (M3 20, M14 41) / 89 passed | claim confirmed |
| Independent probes | `$P -m pytest -q -p no:cacheprovider $S/qa3/test_qa3.py` | 1; 1 failed (N-A) / 10 passed; on `$S/frozen`: 11 passed | NOTEs only |
| LIVE M4 | — | NOT RUN (owner: no long LIVE; equation unchanged; attempt 1 LIVE stands) | skip, stated |

### Spec-derived trigger ledger (derived from spec §3 M3, M4 and M14; re-measured by the runs above)

| Spec trigger line | Alert test (severity) | Silent twin | My measurement |
|---|---|---|---|
| M3 `isLiquidatable` turns true → info, record block | `test_onset_is_info_and_records_the_block` | `test_onset_benign_cluster_above_threshold_stays_silent` | PASS (QA2 config sweep, RootCommitted / cluster-event triggers) |
| M3 still liquidatable after N (config 50) → warn | `test_still_liquidatable_after_n_blocks_warns_exactly_at_n` | `test_n_blocks_benign_liquidated_before_n_resolves_without_warn` | exact ages at (5,9), (1,1), (60,61): PASS; Telegram |
| M3 after N2 (config 7,200) → page | `test_still_liquidatable_after_n2_blocks_pages` | `test_n2_benign_lifted_by_deposit_before_n2_resolves_with_a_message` | PASS; re-alert 6 h |
| M3 active and `getBalance == 0` → page | `test_computed_zero_pages*` | `test_computed_zero_benign_low_balance_and_liquidated_clusters_do_not_page` | PASS (API wrong tuple / error / omission) |
| M3 projected within 24 h → info digest | `test_pre_alert_projected_within_24h_is_a_digest_line` | same test, 8,000 blocks out | block-based superset (N-C) |
| M4 `deficit > 0` → page | `test_deficit_after_root_commit_pages` et al. | `test_deficit_benign_exact_cover_and_surplus_stay_silent` | exact ±1 wei PASS; held page not resolved on an incomplete scan (QA3) |
| M4 surplus trend → info | `test_surplus_trend_is_digest_info` | info only | — |
| M14 reverted liquidate/liquidateSSV on a liquidatable cluster + `ClusterDeposited` same/previous block < 1 block of burn → info | `test_v2a7_dust_deposit_same_or_previous_block_is_info[…]`, `test_qa1_h3_*` | `test_v2a7_benign_*`, `test_qa1_h3_legacy_benign_*`, `test_qa1_m3_benign_*` | BURN−1/BURN/BURN+1, before/after order, shorter chain (QA2 H1): PASS; no-index edge N-B |
| M14 three or more times (distinct deposits, ruling) / 24 h chain time → warn | `test_qa2_m1_three_distinct_deposits_warn`, `test_three_hits_within_the_window_warn` | `test_qa2_m1_one_deposit_reverting_three_liquidations_is_one_time`, `test_qa1_m1_*`, `…spread_beyond_the_window_stay_info` | chain-time decay, boundary, old entries at first run: PASS (QA3) |
| M14 page if also at computed zero | `test_front_run_cluster_at_computed_zero_pages` | `test_computed_zero_benign_without_a_hit_is_silent` | PASS; held zero page not re-escalated (supplied) |

### Defect classes attacked on every persisted key

Persisted keys: `m3_zone` (incl. `hold_block`), `m3_tuples`, M3 `last_full_block` and `pre_alert`, `m4_scans`, M4
`hold`, `m14_hits`, `m14_pending` (incl. awaiting rows), `m14_open` (incl. `hold_block`, `last_deposit`), `liq_runs`,
incidents and markers.

- **4 (windows; attacked explicitly).** I listed every window. M14 hit count and pending expiry use header time
  anchored at the checkpoint header (`:327`, `:369`, `:402`). Measured: 2 produced blocks carrying 24 h of missed
  slots age a hit out, which a block-count window would not do; the boundary is exact in seconds; entries 30 h old,
  delivered in one first run with a fresh one, do not leak in. Awaiting rows carry no time and do not count until
  re-derived with their header time. M3 N and N2 are block counts as the spec defines them. M3 pre-alert is block-based
  (N-C, superset). M3 sweep and M4 interval and digest are on the worker clock. There is no 7-day window in these
  monitors. **No defect.**
- **1 (degenerate or partial input as proof or clean; attacked explicitly).** An unreadable transaction on an ordinary
  run is pending and retried, never "no hit" (QA3, PASS). `first_replaced` treats a missing or unavailable header as no
  proof and then holds; only a differing hash proves anything, so truncation (`RUNS_KEPT`, probe 8) can only hold,
  never resolve. A held M4 page is not resolved by an incomplete surplus scan at or above the held block (QA3, PASS).
  The completeness guard (`getNetworkValidatorsCount`), the `HISTORY` cap giving UNKNOWN, and the single last-log tuple
  candidate validated by views (Unknown if not) are as before. A missing `transactionIndex` is the exception: N-B.
- **2 (reorg).** Shorter chain M14 (QA2 H1) and M3: PASS. Lagging node: supplied tests. Lag followed by a real fork:
  N-A. Depth 1, depth N, trigger block, crash between rewind and run, empty API and health-only results: supplied plus
  QA1/QA2 probes, all green.
- **3 (same-block order).** QA2 before/after and QA1 later top-up: PASS.
- **5 (events between sweeps), 6 (caps), 10 (restart and duplicates), 11 (config):** QA1 and QA2 probes plus supplied
  tests green; M4 and M3 held reorg runs stay within budget.

## 4. Falsifiers

| ID | Mutation | Named test | Red? | Restored |
|---|---|---|---|---|
| F1–F15, QF3, QF4, QF14, R1…R12, C5, C11×3, S1–S6, R2-* (15), S7–S11 (60 rows, the implementer's repair-2 runner incl. the original 15) | see `workers/cp2a/repair2/mutate.py` | as listed there (QF3/QF4 on attempt-1 App. A) | 60/60 exit 1 | all `restored: true`; porcelain of `reconcile/checks` `''`; runner exit 0 |
| QA2-F1 M3 hard-coded N/N2 | `n, n2 = self.config[…]` → `n, n2 = 50, 7_200` | `$S/qa2/test_qa2.py::test_m3_n_and_n2_from_config_move_the_alert` | exit 1 (3 failed) | yes |
| QA2-F2 M14 same-block order ignored | `(ev.block == B - 1 or ev in before)` → `(… or ev.block == B)` | `…::test_m14_failed_liquidation_before_and_after_the_dust_in_one_block` | exit 1 | yes |
| QA2-F3 M4 one-wei tolerance | `if deficit > 0:` → `if deficit > 1:` | `$S/appA/test_independent.py::test_m4_exact_wei` | exit 1 | yes |

Runner `$S/qa2/mutate_qa2.py` exit 0, final `git status --porcelain` = `''`. No QA-3 mutations of my own (owner
instruction, see the header). The QA3 probes instead show red/green against code: N-A is red on `2b434cf` and green on
`92d8ff9`, and the QA2 H1/M1 reproducers are green on `2b434cf` and red on `92d8ff9`.

## 5. Evidence-plane writes

- `plans/monitoring-service/qa/cp2a-attempt-3.md` (this report).
- `plans/monitoring-service/plan.md`, CP2a `**Status:**` line → `VERIFIED — PASS WITH NOTES` on `2b434cf`, citing
  this report and NOTEs N-A, N-B.
- `plans/monitoring-service/execution-log.md`: one appended event (lane-local; the coordinator numbers it at merge).
- Untracked: `reconcile/.venv` (authorized). Scratch: `/tmp/cp2a-qa3/**`. No `config/`, `config.zip` or `.env*` was
  read or written. No RPC or API call in this attempt (no mainnet transaction). No real alert delivery (record mode,
  fake chain).

## 6. Repo proof

Before my writes, after every mutation run:

```text
$ git status --porcelain
(empty)
$ git diff --name-only 2b434cf99a9d33371039cd93ab273f4de852fee3
(empty)
```

After my writes (before the commit): `git status --porcelain` lists only
`plans/monitoring-service/qa/cp2a-attempt-3.md`, `plans/monitoring-service/plan.md` and
`plans/monitoring-service/execution-log.md`. No implementation file appears in either list.

## Appendix A — independent probes (`/tmp/cp2a-qa3/qa3/test_qa3.py`, verbatim)

Run from the frozen worktree root (or set `CP2A_ROOT`). Expected on `2b434cf`: **1 failed (N-A), 10 passed**. On the
`92d8ff9` overlay: 11 passed.

```python
"""CP2a QA attempt 3 (Claude Opus 5.5) — independent probes on frozen head 2b434cf.

Focus: DEFECT-CLASSES 4 (windows by block count instead of chain time) and 1 (degenerate/partial input read as
positive proof), plus the repair-2 holds. Run from the worktree root (or set CP2A_ROOT):
    reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider -s /tmp/cp2a-qa3/qa3/test_qa3.py
"""

from __future__ import annotations

import json
import os
import sys

ROOT = os.environ.get("CP2A_ROOT", os.getcwd())
sys.path.insert(0, os.path.join(ROOT, "reconcile", "tests"))
sys.path.insert(0, os.path.join(ROOT, "reconcile"))

import pytest  # noqa: E402
from fixtures_liquidation import (  # noqa: E402
    BURN,
    OPS,
    OPS2,
    OWNER,
    THRESHOLD,
    cid_hex,
    failed_liquidation,
    incidents,
    lagging_node,
    open_incidents,
    sent,
    setup,
)
from harness import advance, outbox, stub_params  # noqa: E402
from test_m14 import frontrun, hits, make  # noqa: E402

DAY = 86_400


def ev(w, check, key):
    return json.loads(open_incidents(w, check)[key]["evidence"])


# ------------------------------------------------------------------ class 4: M14 window is chain time, not blocks


def test_m14_c4_warn_ages_out_by_chain_time_with_only_a_few_blocks(tmp_path):
    """Three distinct front-runs one hour apart warn; 24 h of missed slots inside four produced blocks age the first
    out (a block-count window would still hold all three); a fourth time warns again."""
    chain, w, cl, path = make(tmp_path)
    chain.clock = None  # chain time only from slots and missed-slot gaps
    for i in range(3):
        if i:
            chain.elapse(3600)
        frontrun(chain, cl, path)
        advance(w)
    assert [s for _, s in sent(w, "m14")][-1] == "warn", sent(w, "m14")
    first = hits(w)[0]
    chain.elapse(DAY - 2 * 3600)  # the first hit is now > 24 h old in chain time, after 2 more blocks only
    chain.mine(2)
    w.clock.advance(600)
    advance(w)
    e = ev(w, "m14", cid_hex())
    now = e["chain_time"]
    assert now - first["block_time"] >= DAY and chain.sealed - first["block"] < 10
    assert open_incidents(w, "m14")[cid_hex()]["severity"] == "info" and e["count"] == 2, e
    n = len(sent(w, "m14"))
    frontrun(chain, cl, path)
    advance(w)
    assert sent(w, "m14")[n:] == [("escalate", "warn")], sent(w, "m14")


@pytest.mark.parametrize("age,counted", [(DAY - 1, True), (DAY, False)])
def test_m14_c4_window_boundary_in_seconds(tmp_path, age, counted):
    chain, w, cl, path = make(tmp_path)
    chain.clock = None
    frontrun(chain, cl, path)
    frontrun(chain, cl, path)
    advance(w)
    t_first = hits(w)[0]["block_time"]
    frontrun(chain, cl, path)  # a third time: warn while the first one counts
    # the run's checkpoint is the third hit's block; put its timestamp at t_first + age
    third_block = chain.sealed
    want = t_first + age
    chain.gaps.append((third_block, want - chain.timestamp(third_block)))
    advance(w)
    e = ev(w, "m14", cid_hex())
    assert e["chain_time"] == want
    assert (e["count"] == 3) is counted and (open_incidents(w, "m14")[cid_hex()]["severity"] == "warn") is counted


def test_m14_c4_old_inbox_entries_delivered_at_the_first_run_do_not_leak_into_the_window(tmp_path):
    """Two front-runs 30 h before a third, all delivered in ONE run (first sweep, no baseline; ~20 blocks apart):
    only the fresh one counts."""
    chain, w, cl, path = make(tmp_path)
    chain.clock = None
    frontrun(chain, cl, path)
    chain.mine(5)
    frontrun(chain, cl, path)
    chain.mine(5)
    chain.elapse(30 * 3600)
    frontrun(chain, cl, path)
    advance(w)
    assert len(hits(w)) == 3
    assert sent(w, "m14") == [("open", "info")] and ev(w, "m14", cid_hex())["count"] == 1


# ------------------------------------------------------------------ class 1: M14 degenerate inputs


def test_m14_c1_no_transaction_index_and_the_only_same_block_deposit_after_the_failure(tmp_path):
    """A node that reports no transactionIndex; the dust deposit is AFTER the failed liquidation in block B (truth: not
    a front-run). Records what the check does with the unknown order (one event assumed before)."""
    chain, w, cl, path = make(tmp_path)
    old = cl.tuple()
    tx, _ = failed_liquidation(chain, cl, path, tuple_=old, tx_index=False)
    cl.deposit(value=1, mine=False)
    chain.mine()
    advance(w)
    print("hits", [(h["tx_hash"][:10], h["ordering"]) for h in hits(w)], "sent", sent(w, "m14"))
    hs = hits(w)
    assert not hs or hs[0]["ordering"] == "unknown"  # if counted, it must at least be labelled
    if hs:
        assert "order unknown" in outbox(w, "kind='open'")[0]["text"]


def test_m14_c1_unreadable_transaction_of_a_new_entry_is_not_dismissed(tmp_path):
    """An empty eth_getTransactionByHash on an ordinary (non-reorg) run: pending + health, never 'no hit'."""
    chain, w, cl, path = make(tmp_path, cfg={"m14": {"repeat_count": 1}})
    tx = frontrun(chain, cl, path)
    saved = chain.txs.pop(tx)
    advance(w)
    assert not hits(w) and w.db.conn.execute("SELECT COUNT(*) FROM m14_pending").fetchone()[0] == 1
    chain.txs[tx] = saved
    chain.mine()
    w.clock.advance(600)
    advance(w)
    assert len(hits(w)) == 1 and sent(w, "m14") == [("open", "warn")]


def test_m14_c11_burn_change_on_the_fake_chain_moves_the_dust_line(tmp_path):
    """A fee increase (no cluster event) raises the burn: a deposit between the old and the new burn is dust."""
    chain, w, cl, path = make(tmp_path)
    cl.set_burn(burn=3 * BURN)
    chain.mine()
    advance(w)
    frontrun(chain, cl, path, value=2 * BURN)
    advance(w)
    assert sent(w, "m14") == [("open", "info")] and hits(w)[0]["burn_rate"] == str(3 * BURN)


# ------------------------------------------------------------------ class 2 / repair-2 holds


def test_m3_hold_keeps_an_onset_from_a_chain_that_a_lagging_node_hid_being_replaced(tmp_path):
    """The node lags below the onset (no replaced block visible: hold), then serves a DIFFERENT chain from O-2 on, where
    the cluster was topped up and became liquidatable only ~35 blocks later. After the hold the kept onset O (an
    orphaned block) still dates the age: compare with a fresh worker on the final chain."""
    from fixtures_liquidation import Clusters, LiquidationChain  # noqa: F401

    chain, w, cl = setup(tmp_path, cfg={"m3": {"n_blocks": 50, "n2_blocks": 7200}})
    cl.create(balance=THRESHOLD + 5 * BURN)
    O = None
    for _ in range(60):
        chain.mine()
        advance(w)
        if O is None and open_incidents(w, "m3"):
            O = json.loads(open_incidents(w, "m3")[cid_hex()]["evidence"])["onset_block"]
        if O is not None and w.checkpoint()["block"] - O >= 40:
            break
    L = w.checkpoint()["block"]
    assert sent(w, "m3") == [("open", "info")]
    lagging_node(chain, w, chain.sealed - (O - 3))  # node head below the onset: a "reorg" without a replaced block
    row = w.db.conn.execute("SELECT * FROM m3_zone").fetchone()
    print("after lag", w.checkpoint(), row and {k: row[k] for k in ("onset_block", "last_block", "severity")}, L)
    chain.reorg(O - 2)  # the real chain: from O-2 on it is another chain
    cl.deposit(value=36 * BURN)  # topped up at O-2: liquidatable again only around O+35
    while chain.sealed < O + 52:
        chain.mine()
        advance(w)
    lived = open_incidents(w, "m3").get(cid_hex())
    lived_ev = json.loads(lived["evidence"]) if lived else None
    print("lived", lived and lived["severity"], lived_ev and (lived_ev["onset_block"], lived_ev["age_blocks"]),
          sent(w, "m3"))
    # a fresh worker on the final chain
    from harness import make_worker
    from fakechain import FakeClock
    (tmp_path / "fresh").mkdir()
    _, w2 = make_worker(tmp_path / "fresh", only=["m3"], chain=chain, views=w.cfg.views if hasattr(w.cfg, "views")
                        else None, checks={"m3": {"n_blocks": 50, "n2_blocks": 7200}}, clock=FakeClock(w.clock.now),
                        start_block=w.cfg.start_block, activation_block=w.cfg.activation_block)
    advance(w2)
    fresh = open_incidents(w2, "m3").get(cid_hex())
    print("fresh", fresh and fresh["severity"], fresh and json.loads(fresh["evidence"])["age_blocks"])
    assert lived is not None and lived["severity"] == "info", (lived and lived["severity"], lived_ev)


def test_m4_hold_resolution_needs_a_complete_scan_at_or_above_the_held_block(tmp_path):
    """Class 1 on the M4 hold: a held deficit page must not resolve on an incomplete scan at or above the held block."""
    from fixtures_liquidation import stub_m4

    chain, w, cl = setup(tmp_path, checks=("m4",), confirmations=3)
    cl.create(balance=10**18)
    stub_m4(chain, {1: 10**15, 2: 10**15, 3: 10**15, 4: 10**15}, network=10**15, pool=10**15,
            proxy_balance=10**18)  # deficit: 1e18 + 5e15 > 1e18
    chain.mine(4)
    advance(w)
    assert sent(w, "m4") == [("open", "page")]
    chain.mine(10)
    w.clock.advance(DAY)
    advance(w)
    S = w.checkpoint()["block"]
    lagging_node(chain, w, 8)  # lowered below the latest scan: held
    assert w.checkpoint()["block"] < S and open_incidents(w, "m4")
    chain.set_balance("0xdd9bc35ae942ef0cfa76930954a156b3ff30a4e1", 2 * 10**18)  # now a surplus
    chain.view_faults[list(chain.model)[0]] = "empty"  # the only cluster's getBalance is empty: incomplete
    chain.mine(10)
    advance(w)
    assert w.checkpoint()["block"] >= S
    assert open_incidents(w, "m4"), sent(w, "m4")  # surplus on an incomplete sum is UNKNOWN, never a resolution


# ------------------------------------------------------------------ V2x.4: budgets in a held (lowered) reorg run, 350 clusters


def test_m3_rpc_budget_350_in_a_held_reorg_run(tmp_path):
    chain, w, cl = setup(tmp_path, confirmations=3)
    for i in range(350):
        cl.create("0x" + f"{i + 1:040x}", OPS, balance=THRESHOLD - 10 * BURN, mine=False)
    chain.mine(5)
    advance(w)
    chain.mine(10)
    advance(w)
    assert len(open_incidents(w, "m3")) == 350
    lagging_node(chain, w, 6)  # lowered checkpoint: every row held
    m = w.db.meta("run:m3")
    print("held reorg run", m["reasons"], m["rpc_calls"])
    assert "reorg" in m["reasons"] and len(open_incidents(w, "m3")) == 350
    assert m["rpc_calls"].get("eth_call", 0) <= 3 * 350 + 1 and m["rpc_calls"].get("eth_getBlockByNumber", 0) <= 8 + 2


def test_m4_rpc_budget_350_in_a_held_reorg_run(tmp_path):
    from fixtures_liquidation import stub_m4

    chain, w, cl = setup(tmp_path, checks=("m4",), confirmations=3)
    for i in range(350):
        cl.create("0x" + f"{i + 1:040x}", OPS, balance=10**18, mine=False)
    stub_m4(chain, {op: 10**15 for op in range(1, 41)}, 10**15, 10**15, 10**18)  # deficit
    chain.mine(5)
    advance(w)
    chain.mine(10)
    w.clock.advance(DAY)
    advance(w)
    assert sent(w, "m4", "o.kind != 'realert'") == [("open", "page")]
    lagging_node(chain, w, 6)
    m = w.db.meta("run:m4")
    print("held reorg run", m["reasons"], m["rpc_calls"])
    assert m["rpc_calls"]["eth_call"] <= 350 + 40 + 3 and m["rpc_calls"].get("eth_getBlockByNumber", 0) <= 8 + 1
    assert sent(w, "m4", "o.kind != 'realert'") == [("open", "page")]
```
