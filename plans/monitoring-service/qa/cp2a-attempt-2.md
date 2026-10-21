FAIL

# CP2a QA attempt 2 (after repair 1) — Liquidation family (M3, M4, M14)

Independent QA, frozen head `115433386803855934525d73628b65ae6252c86a` (lane `mtabasco/ms-cp2a`), worktree
`ms-qa-cp2a-2`, branch `mtabasco/ms-qa-cp2a-2`, 2026-10-21T15:0x–15:45Z. Task `task_f41dfe954f70`, dispatch
`ctx_bde5259c7350`. QA model: Claude Opus 5.5 (`claude-opus-5-5`), effort high (D13). No implementation repair.

Conventions: run from the worktree root; `P=reconcile/.venv/bin/python`; scratch `S=/tmp/cp2a-qa2`. My independent
probes are `$S/qa2/test_qa2.py` (Appendix A, verbatim), my falsifier runner `$S/qa2/mutate_qa2.py` (Appendix B), the
LIVE probe `$S/live/live_deposit.py` (Appendix C). Attempt 1's Appendix A file was re-extracted byte-for-byte from
`qa/cp2a-attempt-1.md` into `$S/appA/test_independent.py`.

Summary: every implementer number reproduces (479 tests, ruff clean, V rows, 41/41 falsifiers, Appendix A 7 failed →
35 passed, 39 of 51 new cases red on the frozen head, sweep 63/100 red on the frozen head). All six attempt-1 findings
are fixed as far as I could attack them. Two new findings remain, one of which is a dropped alert under the reorg
obligation (README §6.2), so the verdict is FAIL. The round policy does not cap this attempt, so every finding is listed.

## 1. Findings

### H1 — M14 loses a hit, for good, when a reorg moves the checkpoint below a surviving failed-liquidation block (shorter replacement chain), and resolves the warn as "no front-running hit remains on the canonical chain"

- Class: DEFECT-CLASSES 2 (reorg of state outside the obvious set; shorter replacement chain) and 1 (an input that
  cannot be evaluated is treated as absent). BLOCKING: a dropped alert plus a false resolution.
- Reproducer: `$P -m pytest -q -p no:cacheprovider -s $S/qa2/test_qa2.py::test_m14_shorter_replacement_chain_keeps_a_hit_whose_block_survived`
  → exit 1. It fails the same way on the frozen head and after repair 1, so this is not a regression.
- Scenario: `confirmations = 12`, `repeat_count = 1` (one hit = warn, to keep it short; the same happens at info and
  with 3 hits). A 1-wei `ClusterDeposited` in block X−1 (the previous-block variant the spec names), then the reverted
  `liquidate` carrying the old tuple in block X (it emits no log, so X is not a recorded event block). Mine 20 blocks;
  the hit is recorded and the warn is sent. Then a reorg from X+3 onto a **shorter** chain: blocks ≤ X+2 keep their
  hashes, so block X, the deposit and the failed transaction stay canonical. The new checkpoint is the ancestor X−1 (the
  target head−12 is lower). Mine 30 blocks so the new chain grows past X again.
- Expected: the evidence never left the canonical chain. Either the key is kept while block X sits above the new
  checkpoint, or the hit is re-derived once X is confirmed again. The warn stays open.
- Actual: `hits == []`, `sent == [('open','warn'), ('resolved','info')]`, and the resolution reads "M14 deposit
  front-running ended … no front-running hit remains on the canonical chain." The inbox row is `canonical = 1, resolved
  = 1` at block X (= 1016 in the run) and `cursor:inbox:m14 = 1`, so the entry is never delivered again and never
  re-derived. The hit is gone for good, and later front-runs in the window count from zero.
- Mechanism: `check_reorg` sets `inbox.canonical = NULL` for every row above the ancestor (`reconcile/eb_reconcile.py:228`).
  The M14 reorg run deletes the hits above A (`reconcile/checks/m14_deposit_frontrun.py:290`). It then re-derives only
  rows with `canonical = 1 AND block > A` (`:296–298`). A delivered row whose block is above the new checkpoint stays
  `NULL` in that step, so it is neither re-derived nor kept in `m14_pending` (also deleted above A, `:291`). After the
  run commits, the row is re-confirmed, but the inbox cursor has already passed it (`inbox.py:308–320`). The reorg run
  saw an input it could not evaluate, yet it returned a clean `resolve=True` instead of keeping the key or raising
  (README §6.2 item 4).
- Note for the coordinator: README §6.2 item 5 ("re-derive from `inbox` rows with `canonical = 1` … re-confirmed before
  the run in the same step") does not cover entries above a lowered checkpoint, so the CP1B guidance has the same gap.
  The defect and the lost alert are in M14, which owns its inbox-derived state. Rare on mainnet: it needs a reorg
  deeper than `confirmations` onto a shorter chain. That is exactly the attack the packet names.

### M1 — M14 escalates to warn on a single front-run: one dust deposit that reverts three competing liquidations counts as "three times"

- Class: 9 (spec coverage by shorthand). This is a false warn (page route, Telegram) under the most natural reading of
  the spec. Before I rank it BLOCKING, the coordinator needs to rule on what a "time" is.
- Reproducer: `$P -m pytest -q -p no:cacheprovider -s $S/qa2/test_qa2.py::test_m14_one_dust_deposit_reverting_three_liquidators_in_one_block`
  → exit 1. Output: `hits 3 sent [('open', 'warn')]`. All three hits carry the same `deposit_tx`.
- Spec: "a `ClusterDeposited` … below its burn for 1 block → info. **Three or more times** on the same cluster within
  24 h → warn." The behaviour section names the concern: "Sustained, this can keep a cluster alive". The spec also says
  the competitive market liquidates at 0–1 blocks, so several keepers racing in the same block is the normal case. One
  1-wei deposit then makes every racing liquidation revert, and the check counts each revert as a hit
  (`m14_deposit_frontrun.py:342`, `recent` = hits, one per failed transaction).
- Expected (reading "times" as front-running deposits, or as distinct blocks): one occurrence → info. Warn only after
  three distinct front-runs within 24 h.
- Actual: warn on the first front-run whenever three keepers race. If the coordinator rules that "times" means failed
  liquidations, this becomes a recorded interpretation rather than a defect.

### Notes (not blocking; recorded with an owner suggestion)

- N1 (M3, first observation; owner: coordinator/spec). The onset is the first block at which the check observes
  `isLiquidatable`. A cluster that was already liquidatable at deployment, or became so while the worker was down, is
  info first, warn after N and page only N2 blocks (≈ 1 day) later, unless it is at computed zero (pages at once). The
  alert says "the age is a lower bound". The design avoids a false backwards estimate after burn-rate changes. It
  delays escalation once, at deployment, and does not drop it.
- N2 (M3 pre-alert unit). The spec's "projected to cross within the next 24 h" is implemented as `pre_alert_blocks =
  7,200`. With missed slots, 7,200 blocks take longer than 24 h, so the digest is a superset (conservative). No line is
  missed.
- N3 (fail-loud stalls). A cluster or transaction that can never be evaluated in a reorg run (M3 `unknown()` for a
  rewound row; M14 a re-derived entry whose transaction a node never serves) raises on every step. That holds the
  marker, keeps `healthy: false` and suppresses re-alerts of that check until the input appears. This is the README
  §6.2 choice (raise, never clean). It has no time-out.
- N4 (H3, informational). See §2. The legacy `liquidateSSV` + `ClusterDeposited` path is implemented and tested on the
  fixture, but it is unreachable on mainnet at the pin.

## 2. Claims I could NOT confirm, and judgements requested by the packet

- **"At the pin `deposit()` is ETH-only, so on mainnet nothing can trigger the legacy `liquidateSSV` path that H3 required"
  — CONFIRMED.** Pinned source: `git -C /Users/marco/ssv/repos/ssv-network show f7ac487:contracts/modules/SSVClusters.sol`.
  Lines 186–200: `deposit` calls `ClusterLib.validateClusterVersion(version, VERSION_ETH)` before it emits
  `ClusterDeposited` and writes `s.ethClusters`. No other module at the pin emits `ClusterDeposited`; the grep covered
  every non-test `.sol` file at `f7ac487`, and `SSVNetwork.sol:294` only delegates to SSVClusters. LIVE proof
  (Appendix C, `https://ethereum-rpc.publicnode.com`, block 26,241,936, hash `0x256a1993…bdc0b`): for 3 active legacy
  clusters whose API tuple validates against `clusters[id]` (for example `0x87ab407c…6f05`), `eth_call` of
  `deposit(owner, ops, tuple)` with value 1 wei reverts `0xf6749746` = `IncorrectClusterVersion()`. The control (one
  ETH cluster, validated tuple) returns `0x` (success). The spec's M14 trigger requires a `ClusterDeposited`, so its
  `liquidateSSV` half cannot fire on mainnet at the pin. The spec text names `liquidateSSV`, and the implementation
  supports it anyway (version from storage, SSV views, SSV burn, `getBalanceSSV`; tests `test_qa1_h3_*`, falsifiers
  R4-H3 and S5-H3 red). **H3 is closed: implemented, and moot on mainnet at the pin.** A front-run of a legacy
  liquidation by another tuple change (owner `removeValidator` / `migrateClusterToETH`) is not an M14 trigger in the
  spec.
- **M4 unchanged.** `git diff 7211819 HEAD -- reconcile/checks/m4_conservation_deficit.py reconcile/checks/m4_probe.py`
  shows one change: a construction guard `interval_s > 0` (class 11). `m3_views.py` (imported by M4) only gained
  additive helpers; the M4 imports are unchanged in behaviour. Attempt 1's interpretation and LIVE proof stand. I did
  not re-litigate them and did not re-run M4 LIVE (budget rule; equation unchanged). M4 wei-exactness was re-measured
  offline: Appendix A of attempt 1, `test_m4_exact_wei[-1,0,1]` pass, and my falsifier QA2-F3 (`deficit > 0` →
  `deficit > 1`) is red.
- **Implementer LIVE probes not re-run**: `live/ssv-views-26241773.json` (5 legacy clusters) is superseded by my own
  LIVE read above at a later block. The full M3 LIVE views traversal was not re-run by the implementer or by me
  (budget; the evaluation path is unchanged and offline rows cover it).
- Everything else claimed was reproduced (§3): 479 passed and ruff clean; V2x.1 52/29/58; V2x.2 5/2/3; V2x.3 3/1/3;
  V2x.4 1/1/1; V2a.7 9; sweep 100; 41/41 falsifiers red and restored; Appendix A 35 passed (7 failed on `098c2cd`);
  39 of 51 new cases red on `098c2cd` (my set equals `repair1/red-on-frozen.txt` exactly; two more reds are the
  modified pre-existing tests `test_rpc_budget_per_entry` and `test_thresholds_from_chain_window_and_repeat_from_config`,
  which are not "new"); sweep on `098c2cd`: 63 failed / 37 passed (= 17/40 M3 + 46/60 M14).

## 3. Verification matrix

Every command was run on its own; nothing is credited from an `&&` chain.

| V-ID | Command | Exit / measured | Result |
|---|---|---|---|
| Preflight | `git rev-parse HEAD`; `git status --porcelain`; `df -h .` | `1154333…` = frozen head; clean; 348 GiB free | PASS |
| Env | `uv venv reconcile/.venv --python 3.12`; `uv pip install --python reconcile/.venv/bin/python -r reconcile/requirements-dev.txt`; `$P --version` | 0; Python 3.12.15, pytest 9.1.1, ruff 0.17.1 (authorized, untracked) | PASS |
| Frozen patch | `diff <(git diff 96d1c32 HEAD -- reconcile/tests/{harness,test_framework,test_worker}.py) <(git diff bd531b4~1 500abe4 -- …)`; `git diff plan/monitoring-service -- …` | identical (`PATCH-IDENTICAL`); 0 lines vs integration | PASS |
| Frozen files | `git diff --name-only 96d1c32 HEAD` minus lane paths | only `checks/__init__.py` (1 import line), `config.example.json` (3 keys), the 3 patch files, plan docs from the coordinator merge; new `tests/test_liquidation_sweep.py` accepted (new file, edits nothing frozen) | PASS |
| V2a.1 (V2x.1) M3 | `$P -m pytest -q -p no:cacheprovider reconcile/tests/test_m3.py` | 0; 52 passed | supplied PASS |
| V2a.1 M4 | `… reconcile/tests/test_m4.py` | 0; 29 passed | PASS |
| V2a.1 M14 | `… reconcile/tests/test_m14.py` | 0; 58 passed | supplied PASS; acceptance FAIL (H1, M1) |
| sweep | `… reconcile/tests/test_liquidation_sweep.py` | 0; 100 passed | PASS |
| V2a.2 (V2x.2) | `… test_m3.py -k thresholds_from_chain` / m4 / m14 | 0/0/0; 5 / 2 / 3 passed | PASS |
| V2a.2 independent | `$P -m pytest -q $S/qa2/test_qa2.py -k 'n_and_n2 or parameter_change or n_bigger'` | 0; config N/N2 (5,9), (1,1), (60,61): severity equals info/warn/page at every age 0…N2; N > N2 is a startup error; raising the threshold period on the fake chain (event, no cluster event) opens the onset | PASS |
| V2a.3 (V2x.3) | `… -k restart` per file | 0/0/0; 3 / 1 / 3 passed | PASS |
| V2a.3 independent | `$S/qa2/test_qa2.py -k restart` | 0; M14 3 restarts + 3 duplicate inbox lines → one warn, one hit, one incident; M3 3 restarts → one page, one re-alert at 6 h | PASS |
| V2a.4 (V2x.4) | `… -k rpc_budget` per file | 0/0/0; 1 / 1 / 1 passed | PASS |
| V2a.4 independent | `$S/qa2/test_qa2.py -k rpc_budget` (350 clusters) | 0; M3 all 350 liquidatable: first full 1,051 eth_call, near-set run 1,050, full with 350 tracked rows 1,051 (bound 3 × 350 + 1); M14 with a 350-cluster inventory: 4 eth_call, 1 tx, 2 headers, 2 storage per entry. M4 bound (≤ 350 + 40 + 3, 1 eth_getBalance) re-run via the supplied test (module unchanged) | PASS |
| V2a.5 (V2x.5) | `$P plans/monitoring-service/workers/cp2a/repair1/mutate.py $S/appA/test_independent.py` | 0; 41/41 mutants pytest exit 1, all `restored: true`, porcelain `''` | PASS |
| V2a.5 own | `$P $S/qa2/mutate_qa2.py` | 0; 3/3 red, restored, porcelain `''` (§4) | PASS |
| V2a.6 (V2x.6) | `$P -m pytest -q -p no:cacheprovider reconcile/tests`; `reconcile/.venv/bin/ruff check reconcile` | 0; 479 passed in 13.5 s; 0, "All checks passed!" | PASS |
| V2a.7 | `… test_m14.py -k v2a7` | 0; 9 passed | supplied PASS; boundary exact in wei (`$S/qa2` `dust_boundary[BURN-1 hit, BURN silent, BURN+1 silent]` 0); same-block before/after ordering 0 |
| Attempt-1 App. A | `$P -m pytest -q $S/appA/test_independent.py` | 0; 35 passed (on `098c2cd` overlay: 7 failed / 28 passed) | PASS (H1–H3, M1–M3 closed) |
| RED on frozen | `/tmp/cp2a-qa2/frozen` = `git archive 098c2cd reconcile` + HEAD tests/fixtures + HEAD `m3_views.py` (additive helpers) | new monitor tests 41 failed / 98 passed (39 new + 2 modified); sweep 63 / 37 | claim confirmed |
| Independent probes | `$P -m pytest -q -p no:cacheprovider $S/qa2/test_qa2.py` | **1; 2 failed (H1, M1) / 20 passed** | FAIL |
| LIVE (H3 reachability) | `$P -I $S/live/live_deposit.py <worktree> https://ethereum-rpc.publicnode.com 3` | 0; block 26,241,936: legacy `deposit` reverts `0xf6749746` ×3, ETH control `0x` | measured (§2) |
| LIVE M4 | — | NOT RUN by instruction (M4 unchanged; attempt 1 LIVE at 26,241,506 stands) | skip, stated |

### Spec-derived trigger ledger (my own, from the spec text)

| Spec trigger line | Alert test (severity) | Silent twin | My measurement |
|---|---|---|---|
| M3 `isLiquidatable` turns true → info, record block | `test_onset_is_info_and_records_the_block` | `test_onset_benign_cluster_above_threshold_stays_silent` | config sweep, RootCommitted/cluster-event triggers: PASS |
| M3 still liquidatable after N (50, config) → warn | `test_still_liquidatable_after_n_blocks_warns_exactly_at_n` | `test_n_blocks_benign_liquidated_before_n_resolves_without_warn` | (5,9),(1,1),(60,61) exact ages: PASS; route Telegram |
| M3 after N2 (7,200, config) → page | `test_still_liquidatable_after_n2_blocks_pages` | `test_n2_benign_lifted_by_deposit_before_n2_resolves_with_a_message` | PASS; route Telegram, re-alert 6 h |
| M3 active and `getBalance == 0` → page | `test_computed_zero_pages*` | `test_computed_zero_benign_low_balance_and_liquidated_clusters_do_not_page` | API wrong tuple / API error / omission then real liquidation: PASS |
| M3 projected within 24 h → info digest | `test_pre_alert_projected_within_24h_is_a_digest_line` | same test, 8,000 blocks out | N2 |
| M3 cluster set = API v4 + logs; view-computed | `test_qa1_h1_*`, `test_unknown_incomplete_inventory…` | `…then_a_real_liquidation_resolves_on_proof` | PASS |
| M4 `deficit > 0` → page | `test_deficit_after_root_commit_pages` et al. | `test_deficit_benign_exact_cover_and_surplus_stay_silent` | exact wei ±1: PASS (module unchanged) |
| M4 surplus trend → info | `test_surplus_trend_is_digest_info` | info only | — |
| M14 revert of liquidate/liquidateSSV on a liquidatable cluster + `ClusterDeposited` same/previous block < 1 block of burn → info | `test_v2a7_dust_deposit_same_or_previous_block_is_info[…]`, `test_qa1_h3_*` | `test_v2a7_benign_*`, `test_qa1_h3_legacy_benign_*`, `test_qa1_m3_benign_*` | boundary BURN−1/BURN/BURN+1, before/after ordering: PASS; **shorter chain: FAIL (H1)** |
| M14 three or more times / 24 h → warn | `test_three_hits_within_the_window_warn`, `test_qa1_m2_*` | `test_qa1_m1_*`, `test_three_hits_benign_spread_beyond_the_window_stay_info` | window = 86,400 s chain time (equivalent to the spec's 24 h now; attempt-1 M1 closed); **one deposit × 3 keepers = warn: M1** |
| M14 page if also computed zero | `test_front_run_cluster_at_computed_zero_pages`, `test_qa1_h3_legacy_cluster_at_computed_zero_pages` | `test_computed_zero_benign_without_a_hit_is_silent` | PASS |

Defect classes attacked first on every persisted key (`m3_zone`, `m3_tuples`, `last_full_block`/`pre_alert`,
`m4_scans`, `m14_hits`, `m14_pending`, `m14_open`, incidents/markers):

- 1 (empty/partial/error inputs): M3 API `[]`, wrong API tuple, API fetch error: pass. M14 unreadable tx: pending. M14
  entry above a lowered checkpoint: **fails (H1)**.
- 2 (reorg): M3 shorter chain below the onset, then regrowth, keeps the page: pass. M14 shorter chain: **fails (H1)**.
  Depth 1, depth N, trigger block and crash were re-run via the supplied tests (pass); I did not duplicate them.
- 3 (same-block order): M14 before/after: pass.
- 4 (windows): M14 chain-time window anchored at the checkpoint: pass (App. A, supplied).
- 5 (between sweeps): RootCommitted and cluster events trigger a sweep at their block: pass.
- 6 (caps): `HISTORY = 8` beyond → UNKNOWN; display caps only.
- 10 (restart/duplicates): pass.
- 11 (config validation): N > N2 is a startup error: pass.

## 4. Falsifiers

| ID | Mutation (one token or line) | Named test | Red? | Restored |
|---|---|---|---|---|
| F1–F15, QF3, QF4, QF14, R1-H1…R12, C5, C11×3, S1–S6 (41 rows, implementer's list incl. the original 15) | see `repair1/mutate.py` | as listed there (QF3/QF4 on attempt-1 App. A) | 41/41 exit 1 | all `restored: true`; porcelain `''`; runner exit 0 |
| QA2-F1 M3 (hard-coded parameter) | `n, n2 = self.config["n_blocks"], self.config["n2_blocks"]` → `n, n2 = 50, 7_200` | `$S/qa2/test_qa2.py::test_m3_n_and_n2_from_config_move_the_alert` | exit 1 (3 failed) | yes |
| QA2-F2 M14 (same-block ordering) | `(ev.block == B - 1 or ev in before)` → `(ev.block == B - 1 or ev.block == B)` | `…::test_m14_failed_liquidation_before_and_after_the_dust_in_one_block` | exit 1 | yes |
| QA2-F3 M4 (tolerance) | `if deficit > 0:` → `if deficit > 1:` | `$S/appA/test_independent.py::test_m4_exact_wei` | exit 1 (`[-1]` red) | yes |

Runner `$S/qa2/mutate_qa2.py` exit 0, final `git status --porcelain` = `''`.

## 5. Evidence-plane writes

- `plans/monitoring-service/qa/cp2a-attempt-2.md` (this report).
- `plans/monitoring-service/plan.md`, CP2a `**Status:**` line → `QA FAILED` on frozen head `1154333…`, citing this report.
- `plans/monitoring-service/execution-log.md`: one appended event (lane-local; the coordinator assigns its number at
  merge, because integration already has 0051–0052).
- Untracked: `reconcile/.venv` (authorized). Scratch: `/tmp/cp2a-qa2/**`. No `config/`, `config.zip` or `.env*` was
  read or written. No mainnet transaction (eth_call / getStorageAt / getBlockByNumber and API GETs only). No real
  alert delivery (record mode, fake chain).

## 6. Repo proof

Before my writes, after every mutation run:

```text
$ git status --porcelain
(empty)
$ git diff --name-only 115433386803855934525d73628b65ae6252c86a
(empty)
```

After my writes (before the commit): `git status --porcelain` lists only
`plans/monitoring-service/qa/cp2a-attempt-2.md`, `plans/monitoring-service/plan.md` and
`plans/monitoring-service/execution-log.md`. `git diff --name-only 1154333` lists only `plan.md` and
`execution-log.md`. No implementation file appears in either list.

## Appendix A — independent probes (`/tmp/cp2a-qa2/qa2/test_qa2.py`, verbatim)

Run from the frozen worktree root (or set `CP2A_ROOT`). Expected on `1154333`: **2 failed (H1, M1), 20 passed**. On the `098c2cd` overlay: 4 failed (the same two, plus `test_m3_n_bigger_than_n2_is_a_startup_error` and `test_m3_api_omission_then_real_liquidation_on_chain_resolves`, which exercise repair-1 fixes).

```python
"""CP2a QA attempt 2 (Claude Opus 5.5) — independent probes on frozen head 1154333.

Run from the worktree root:  reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider /tmp/cp2a-qa2/qa2/test_qa2.py
"""

from __future__ import annotations

import os
import sys

ROOT = os.environ.get("CP2A_ROOT", os.getcwd())
sys.path.insert(0, os.path.join(ROOT, "reconcile", "tests"))
sys.path.insert(0, os.path.join(ROOT, "reconcile"))

import pytest  # noqa: E402
from fixtures_liquidation import (  # noqa: E402
    BURN,
    LTP,
    OPS,
    OPS2,
    OWNER,
    OWNER2,
    THRESHOLD,
    Clusters,
    LiquidationChain,
    cid_hex,
    failed_liquidation,
    incidents,
    open_incidents,
    sent,
    setup,
    stub_liquidation_reverts,
)
from harness import advance, api_row, outbox, stub_params  # noqa: E402

from eb_storage import u256  # noqa: E402


def m14(tmp_path, cfg=None, **kw):
    path = tmp_path / "failed-tx.jsonl"
    path.write_text("")
    chain = LiquidationChain()
    stub_liquidation_reverts(chain)
    chain, w, cl = setup(tmp_path, checks=("m14",), cfg=cfg, chain=chain, inbox={"path": str(path)}, **kw)
    cl.create(OWNER, OPS, balance=THRESHOLD - 1_000 * BURN)  # liquidatable, far from zero
    cl.create(OWNER2, OPS2, balance=10**18)
    advance(w)
    return chain, w, cl, path


def hits(w):
    return [dict(r) for r in w.db.conn.execute("SELECT * FROM m14_hits ORDER BY block")]


# ------------------------------------------------------------------ M14: class 2, shorter replacement chain


def test_m14_shorter_replacement_chain_keeps_a_hit_whose_block_survived(tmp_path):
    """The failed liquidation's block X stays canonical; the reorg forks above X onto a chain so short that the new
    checkpoint is below X. The hit must be re-found once X is confirmed again (or the key kept meanwhile)."""
    chain, w, cl, path = m14(tmp_path, cfg={"m14": {"repeat_count": 1}}, confirmations=12)
    chain.mine(12)
    advance(w)  # a recorded checkpoint below the attack
    old = cl.tuple()
    cl.deposit(value=1)  # block X - 1 (the previous block): the only recorded event block
    tx, _ = failed_liquidation(chain, cl, path, tuple_=old)  # block X: no log
    X = chain.number
    chain.mine(20)
    advance(w)
    assert [h["tx_hash"] for h in hits(w)] == [tx] and sent(w, "m14") == [("open", "warn")]
    assert w.checkpoint()["block"] > X
    chain.reorg(X + 3)  # blocks <= X + 2 unchanged; the new chain is shorter: checkpoint target < X
    advance(w)
    assert w.checkpoint()["block"] < X
    chain.mine(30)  # the new chain grows past X again; X (and the attack) is canonical
    advance(w)
    assert chain.hash(X) == _line_hash(path)
    for r in outbox(w):
        print("OUTBOX", r["kind"], r["severity"], r["key"], r["text"][:400].replace("\n", " | "))
    print("inbox", [dict(r) for r in w.db.conn.execute("SELECT id, block, canonical, resolved FROM inbox")])
    print("cursor", w.db.meta("cursor:inbox:m14"))
    # expected: the attack evidence is canonical, so a hit (and an open warn) exists
    assert len(hits(w)) == 1, (hits(w), sent(w, "m14"))
    assert len(open_incidents(w, "m14")) == 1, sent(w, "m14")


def _line_hash(path):
    import json

    return json.loads(path.read_text().splitlines()[0])["block_hash"]


# ------------------------------------------------------------------ M14: what "three times" counts


def test_m14_one_dust_deposit_reverting_three_liquidators_in_one_block(tmp_path):
    """One front-run (one 1-wei deposit) makes three competing liquidations in the same block revert."""
    chain, w, cl, path = m14(tmp_path)
    old = cl.tuple()
    cl.deposit(value=1, mine=False)
    for _ in range(3):
        failed_liquidation(chain, cl, path, tuple_=old)
    chain.mine()
    advance(w)
    print("hits", len(hits(w)), "sent", sent(w, "m14"))
    deposits = {h["deposit_tx"] for h in hits(w)}
    assert len(deposits) == 1
    # one front-running deposit is one time; the spec's warn is for three or more times
    assert sent(w, "m14") == [("open", "info")], sent(w, "m14")


# ------------------------------------------------------------------ M14: the dust boundary in wei


@pytest.mark.parametrize("value,hit", [(BURN - 1, True), (BURN, False), (BURN + 1, False)])
def test_m14_dust_boundary_exact_in_wei(tmp_path, value, hit):
    chain, w, cl, path = m14(tmp_path)
    old = cl.tuple()
    cl.deposit(value=value, mine=False)
    failed_liquidation(chain, cl, path, tuple_=old)
    chain.mine()
    advance(w)
    assert bool(hits(w)) is hit and (sent(w, "m14") == [("open", "info")]) is hit


def test_m14_failed_liquidation_before_and_after_the_dust_in_one_block(tmp_path):
    """Same-block ordering: liquidation L1 (before the deposit) is not front-run, L2 (after it) is."""
    chain, w, cl, path = m14(tmp_path)
    old = cl.tuple()
    t1, _ = failed_liquidation(chain, cl, path, tuple_=old)
    cl.deposit(value=1, mine=False)
    t2, _ = failed_liquidation(chain, cl, path, tuple_=old)
    chain.mine()
    advance(w)
    assert [h["tx_hash"] for h in hits(w)] == [t2]


def test_m14_rpc_budget_350_cluster_inventory_one_entry(tmp_path):
    chain, w, cl, path = m14(tmp_path)
    for i in range(350):
        cl.create("0x" + f"{i + 1:040x}", OPS, balance=10**18, mine=False)
    chain.mine()
    advance(w)
    old = cl.tuple()
    cl.deposit(value=1, mine=False)
    failed_liquidation(chain, cl, path, tuple_=old)
    chain.mine()
    advance(w)
    calls = w.db.meta("run:m14")["rpc_calls"]
    print("m14 rpc", calls)
    assert len(hits(w)) == 1
    assert calls.get("eth_call", 0) <= 8, calls  # 3 views at B + 1 zero test (+ slack); independent of the inventory


# ------------------------------------------------------------------ M3: N and N2 from config


@pytest.mark.parametrize("n,n2", [(5, 9), (1, 1), (60, 61)])
def test_m3_n_and_n2_from_config_move_the_alert(tmp_path, n, n2):
    chain, w, cl = setup(tmp_path, cfg={"m3": {"n_blocks": n, "n2_blocks": n2}})
    cl.create(balance=THRESHOLD + 2 * BURN)
    advance(w)
    chain.mine(3)
    advance(w)
    onset_block = chain.sealed
    sev = []
    for _ in range(n2 + 1):
        age = chain.sealed - onset_block
        s = open_incidents(w, "m3")[cid_hex()]["severity"]
        want = "page" if age >= n2 else "warn" if age >= n else "info"
        sev.append((age, s, want))
        chain.mine()
        advance(w)
    bad = [x for x in sev if x[1] != x[2]]
    assert not bad, bad


def test_m3_n_bigger_than_n2_is_a_startup_error(tmp_path):
    with pytest.raises(SystemExit):
        setup(tmp_path, cfg={"m3": {"n_blocks": 10, "n2_blocks": 9}})


# ------------------------------------------------------------------ M3: parameter changed on the fake chain mid-run


def test_m3_network_fee_parameter_change_mid_run_moves_onset(tmp_path):
    """Raise the threshold period on the fake chain (no cluster event): onset must appear at that block."""
    chain, w, cl = setup(tmp_path)
    cl.create(balance=THRESHOLD + 5_000 * BURN)
    advance(w)
    chain.mine(10)
    advance(w)
    assert not incidents(w, "m3")
    stub_params(chain, liquidation_threshold_period=LTP * 2)
    chain.emit_event("LiquidationThresholdPeriodUpdated", [], u256(LTP * 2))
    chain.mine()
    advance(w)
    assert sent(w, "m3") == [("open", "info")]


# ------------------------------------------------------------------ M3: class 1 variants of H1


def api_baseline(tmp_path, balance=0, **kw):
    chain = LiquidationChain()
    stub_params(chain)
    chain.mine()
    cl = Clusters(chain)
    cl.create(balance=balance)
    chain.mine()
    chain, w, _ = setup(tmp_path, chain=chain, inventory={"api_url": "https://api.invalid", "api_refresh_s": 0}, **kw)
    c = cl.tuple()
    response = {"clusters": [api_row(OWNER, OPS, c.validator_count, c.network_fee_index, c.index, c.active,
                                     c.balance)], "pagination": {"pages": 1}}
    w.inventory.http_get = lambda _: response
    advance(w)
    return chain, w, cl, response


def test_m3_api_lists_a_wrong_tuple_for_a_zero_cluster_keeps_the_page(tmp_path):
    chain, w, cl, response = api_baseline(tmp_path)
    assert sent(w, "m3") == [("open", "page")]
    response["clusters"][0]["balance"] = "7"
    response["clusters"][0]["ethBalance"] = "7"
    response["clusters"][0]["index"] = "999"
    chain.mine(2)
    w.clock.advance(600)
    advance(w)
    assert not outbox(w, "kind='resolved'") and list(open_incidents(w, "m3").values())[0]["severity"] == "page"


def test_m3_api_fetch_error_after_baseline_keeps_the_page(tmp_path):
    chain, w, cl, response = api_baseline(tmp_path)

    def boom(_):
        raise OSError("api down")

    w.inventory.http_get = boom
    chain.mine(2)
    w.clock.advance(600)
    advance(w)
    assert not outbox(w, "kind='resolved'") and list(open_incidents(w, "m3").values())[0]["severity"] == "page"


def test_m3_api_omission_then_real_liquidation_on_chain_resolves(tmp_path):
    chain, w, cl, response = api_baseline(tmp_path)
    response["clusters"] = []
    chain.mine()
    w.clock.advance(600)
    advance(w)
    assert open_incidents(w, "m3")
    cl.liquidate()
    chain.mine()
    advance(w)
    assert not open_incidents(w, "m3") and ("resolved", "info") in sent(w, "m3")


# ------------------------------------------------------------------ M3: reorg onto a shorter chain


def test_m3_reorg_onto_shorter_chain_below_the_onset_then_regrowth(tmp_path):
    """Onset observed at C; reorg onto a chain whose checkpoint is below the onset; the cluster is the same on the
    new chain, so it must be liquidatable again (a new onset) once the chain grows; never a page lost."""
    chain, w, cl = setup(tmp_path, confirmations=12, cfg={"m3": {"n_blocks": 3, "n2_blocks": 6}})
    cl.create(balance=THRESHOLD + 30 * BURN)
    for _ in range(60):
        chain.mine()
        advance(w)
    assert open_incidents(w, "m3")[cid_hex()]["severity"] == "page"
    chain.reorg(chain.number - 20)
    advance(w)
    print("after reorg", w.checkpoint(), sent(w, "m3"))
    for _ in range(30):
        chain.mine()
        advance(w)
    assert open_incidents(w, "m3")[cid_hex()]["severity"] == "page", sent(w, "m3")


# ------------------------------------------------------------------ M3: RPC budget, everything tracked


def test_m3_rpc_budget_350_all_liquidatable(tmp_path):
    chain, w, cl = setup(tmp_path)
    for i in range(350):
        cl.create("0x" + f"{i + 1:040x}", OPS, balance=THRESHOLD - 10 * BURN, mine=False)
    chain.mine()
    advance(w)
    first = w.db.meta("run:m3")["rpc_calls"]
    assert len(open_incidents(w, "m3")) == 350
    chain.mine()
    advance(w)  # near-set run: all 350 near
    near = w.db.meta("run:m3")["rpc_calls"]
    w.clock.advance(600)
    chain.mine()
    advance(w)  # full sweep with 350 tracked rows
    full = w.db.meta("run:m3")["rpc_calls"]
    print("first", first, "near", near, "full", full)
    for c in (first, near, full):
        assert c.get("eth_call", 0) <= 3 * 350 + 1, c


# ------------------------------------------------------------------ M3: sweep policy, one block, routing, cadence


def test_m3_root_committed_triggers_a_full_sweep_before_the_interval(tmp_path):
    chain, w, cl = setup(tmp_path)
    cl.create(balance=THRESHOLD + 100_000 * BURN)
    advance(w)
    cl.set_burn(burn=BURN * 1_000)  # an unannounced burn change (no cluster event): liquidatable now
    chain.mine()
    advance(w)
    assert not incidents(w, "m3")  # not near, interval not due
    chain.emit_event("RootCommitted", ["0x%064x" % 0xB1, "0x%064x" % chain.sealed])
    chain.mine()
    advance(w)
    assert sent(w, "m3") == [("open", "info")]


def test_m3_cluster_event_evaluates_that_cluster_at_its_block(tmp_path):
    chain, w, cl = setup(tmp_path)
    cl.create(balance=THRESHOLD + 100_000 * BURN)
    advance(w)
    cl.withdraw(value=100_500 * BURN)
    advance(w)
    assert sent(w, "m3") == [("open", "info")]
    assert w.db.meta("run:m3")["reasons"] == ["events"] or "events" in w.db.meta("run:m3")["reasons"]


def test_m3_views_batched_at_one_block_and_routing_and_cadence(tmp_path):
    chain, w, cl = setup(tmp_path, cfg={"m3": {"n_blocks": 2, "n2_blocks": 4}})
    for i in range(5):
        cl.create("0x" + f"{i + 1:040x}", OPS, balance=THRESHOLD + 2 * BURN, mine=False)
    chain.mine()
    advance(w)
    n0 = len(chain.calls)
    w.clock.advance(600)
    chain.mine(3)
    advance(w)
    blocks = {c[2] for c in chain.calls[n0:] if c[0] == "0x" + "00" * 0 + c[0][2:]}
    assert len(blocks) == 1, blocks
    for _ in range(5):
        chain.mine()
        advance(w)
    routes = {(r["severity"], r["delivery"]) for r in outbox(w) if r["kind"] in ("open", "escalate")}
    assert routes == {("info", "discord"), ("warn", "telegram"), ("page", "telegram")}, routes
    before = len(outbox(w, "kind='realert'"))
    w.clock.advance(6 * 3600 + 1)
    chain.mine()
    advance(w)
    w.housekeeping()
    assert len(outbox(w, "kind='realert'")) == before + 5  # each page re-alerted once after 6 h


# ------------------------------------------------------------------ restart idempotency (both monitors)


def test_m14_restart_and_duplicate_inbox_line_do_not_duplicate(tmp_path):
    from fixtures_liquidation import restart

    chain, w, cl, path = m14(tmp_path, cfg={"m14": {"repeat_count": 1}})
    old = cl.tuple()
    cl.deposit(value=1, mine=False)
    failed_liquidation(chain, cl, path, tuple_=old)
    chain.mine()
    advance(w)
    assert sent(w, "m14") == [("open", "warn")]
    line = path.read_text().splitlines()[0]
    for _ in range(3):
        with open(path, "a") as fh:
            fh.write(line + "\n")  # the Monitor re-appends the same (tx_hash, block_hash) after its own restart
        w = restart(tmp_path, w, checks=("m14",), cfg={"m14": {"repeat_count": 1}}, inbox={"path": str(path)})
        chain.mine()
        advance(w)
    assert sent(w, "m14") == [("open", "warn")] and len(hits(w)) == 1 and len(incidents(w, "m14")) == 1


def test_m3_restart_three_times_keeps_one_page_and_its_cadence(tmp_path):
    from fixtures_liquidation import restart

    chain, w, cl = setup(tmp_path)
    cl.create(balance=0)
    chain.mine()
    advance(w)
    assert sent(w, "m3") == [("open", "page")]
    for _ in range(3):
        w = restart(tmp_path, w)
        w.clock.advance(3600)
        chain.mine()
        advance(w)
    assert sent(w, "m3") == [("open", "page")] and len(incidents(w, "m3")) == 1
    w.clock.advance(3 * 3600)
    chain.mine()
    advance(w)
    w.housekeeping()
    assert len(outbox(w, "kind='realert'")) == 1
```

## Appendix B — own falsifier runner (`/tmp/cp2a-qa2/qa2/mutate_qa2.py`, verbatim)

```python
"""QA2 own falsifiers: one exact unique string per row, run the named test, restore byte for byte.
Run from the worktree root: reconcile/.venv/bin/python /tmp/cp2a-qa2/qa2/mutate_qa2.py"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile

Q = "/tmp/cp2a-qa2/qa2/test_qa2.py::"
A = "/tmp/cp2a-qa2/appA/test_independent.py::"
ROWS = [
    ("QA2-F1 M3 hard-coded N/N2", "reconcile/checks/m3_liquidation_zone.py",
     'n, n2 = self.config["n_blocks"], self.config["n2_blocks"]', "n, n2 = 50, 7_200",
     [Q + "test_m3_n_and_n2_from_config_move_the_alert"]),
    ("QA2-F2 M14 same-block order ignored", "reconcile/checks/m14_deposit_frontrun.py",
     "(ev.block == B - 1 or ev in before)", "(ev.block == B - 1 or ev.block == B)",
     [Q + "test_m14_failed_liquidation_before_and_after_the_dust_in_one_block"]),
    ("QA2-F3 M4 one-wei tolerance", "reconcile/checks/m4_conservation_deficit.py",
     "        if deficit > 0:", "        if deficit > 1:", [A + "test_m4_exact_wei"]),
]
out = []
for name, path, old, new, tests in ROWS:
    p = pathlib.Path(path)
    orig = p.read_bytes()
    src = orig.decode()
    assert src.count(old) == 1, (name, src.count(old))
    try:
        p.write_text(src.replace(old, new))
        env = {**os.environ, "PYTHONPYCACHEPREFIX": tempfile.mkdtemp(prefix="pyc-qa2-")}
        proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-W", "ignore", *tests],
                              env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    finally:
        p.write_bytes(orig)
    row = {"id": name, "exit": proc.returncode, "restored": p.read_bytes() == orig,
           "tail": proc.stdout.strip().splitlines()[-1]}
    out.append(row)
    print(json.dumps(row), flush=True)
por = subprocess.run(["git", "status", "--porcelain"], stdout=subprocess.PIPE, text=True).stdout
print("porcelain:", repr(por))
sys.exit(0 if all(r["exit"] == 1 and r["restored"] for r in out) and not por else 1)
```

## Appendix C — LIVE probe (`/tmp/cp2a-qa2/live/live_deposit.py`) and raw result

```python
"""QA2 LIVE (read-only): at the latest block, is deposit() reachable for a legacy SSV cluster?

eth_call only (no transaction). For up to N legacy (migrated=false, active) API clusters whose API tuple validates
against `clusters[id]` storage, eth_call proxy.deposit(owner, ops, tuple) with value 1 wei and record the revert.
Control: the same call on one ETH (migrated=true) cluster must succeed (empty return).
Usage: python live_deposit.py <worktree> <rpc_url> [n]
"""
import json
import sys
import urllib.request

root, rpc_url = sys.argv[1], sys.argv[2]
N = int(sys.argv[3]) if len(sys.argv) > 3 else 3
sys.path[:0] = [root + "/reconcile"]
from eb_storage import cluster_id, eth_cluster_slot, keccak, ssv_cluster_slot  # noqa: E402
from inventory import api_tuple  # noqa: E402
from checks.m3_views import encode_cluster_args  # noqa: E402

PROXY = "0xDD9BC35aE942eF0cFa76930954a156B3fF30a4E1"
DEPOSIT = keccak(b"deposit(address,uint64[],(uint32,uint64,uint64,bool,uint256))")[:4]
SENDER = "0x000000000000000000000000000000000000dEaD"


def rpc(method, params):
    req = urllib.request.Request(rpc_url, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                                                          "params": params}).encode(),
                                 headers={"content-type": "application/json", "user-agent": "cp2a-qa2"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


blk = rpc("eth_getBlockByNumber", ["latest", False])["result"]
B, H = blk["number"], blk["hash"]
out = {"endpoint": rpc_url, "block": int(B, 16), "block_hash": H, "deposit_selector": "0x" + DEPOSIT.hex(), "rows": []}


def api(page, migrated):
    url = f"https://api.ssv.network/api/v4/mainnet/clusters?page={page}&perPage=100"
    req = urllib.request.Request(url, headers={"user-agent": "cp2a-qa2"})
    with urllib.request.urlopen(req, timeout=60) as r:
        rows = json.loads(r.read())["clusters"]
    return [c for c in rows if bool(c.get("migrated")) is migrated and c.get("active") and int(c["validatorCount"]) > 0]


def probe(c, kind):
    owner, ops = c["ownerAddress"].lower(), [int(o) for o in c["operators"]]
    cid = cluster_id(owner, ops)
    t = api_tuple(c)
    slot = ssv_cluster_slot(cid) if kind == "SSV" else eth_cluster_slot(cid)
    word = int(rpc("eth_getStorageAt", [PROXY, "0x" + slot.hex() if isinstance(slot, bytes) else hex(slot), B])["result"], 16)
    if word != int.from_bytes(t.hash_data(), "big"):
        return None
    data = "0x" + (DEPOSIT + encode_cluster_args(owner, ops, t)).hex()
    res = rpc("eth_call", [{"from": SENDER, "to": PROXY, "data": data, "value": "0x1"}, B])
    return {"kind": kind, "cluster_id": "0x" + cid.hex(), "tuple_validates": True,
            "result": res.get("result"), "error": res.get("error")}


for kind, migrated in (("SSV", False), ("ETH", True)):
    got = 0
    for page in range(1, 8):
        for c in api(page, migrated):
            r = probe(c, kind)
            if r:
                out["rows"].append(r)
                got += 1
            if got >= (N if kind == "SSV" else 1):
                break
        if got >= (N if kind == "SSV" else 1):
            break
print(json.dumps(out, indent=1))
```

```json
{
 "endpoint": "https://ethereum-rpc.publicnode.com",
 "block": 26241936,
 "block_hash": "0x256a19931453dcc1f9f7594935d83ba2658d419b9cf1fa049e3bc05d599bdc0b",
 "deposit_selector": "0x45f47188",
 "rows": [
  {
   "kind": "SSV",
   "cluster_id": "0x87ab407cbdf02aba3f01e2a57667cf3524d219a5aac62b59782f25acae486f05",
   "tuple_validates": true,
   "result": null,
   "error": {
    "code": 3,
    "message": "execution reverted",
    "data": "0xf6749746"
   }
  },
  {
   "kind": "SSV",
   "cluster_id": "0x5ca59e1c40b1e70d81dbc071ffef6099f2a93ac0ad4fccafda6258fab6bb5fc0",
   "tuple_validates": true,
   "result": null,
   "error": {
    "code": 3,
    "message": "execution reverted",
    "data": "0xf6749746"
   }
  },
  {
   "kind": "SSV",
   "cluster_id": "0x00a3032ec10635c4918c845e3cceb304daf6c2212a9144ab453fff14a0d2016a",
   "tuple_validates": true,
   "result": null,
   "error": {
    "code": 3,
    "message": "execution reverted",
    "data": "0xf6749746"
   }
  },
  {
   "kind": "ETH",
   "cluster_id": "0xe9a5b0fb62ec5a4a0b4614bf7a6313a65e3713d487657749c816123b07222fba",
   "tuple_validates": true,
   "result": "0x",
   "error": null
  }
 ]
}
```
