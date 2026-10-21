FAIL

Independent QA of CP2b (M9, M10, M11), attempt 1. Frozen head: `4e64eb1064c3e33a5df8e090544dc11ac6ee485b`, branch under test `mtabasco/ms-cp2b`; QA branch `mtabasco/ms-qa-cp2b`. Task `task_1b5c38cb2950`, dispatch `ctx_543277fe2df3`. Measurements: 2026-10-21, approximately 14:32–14:45 UTC. No implementation repair.

## 1. Findings

The supplied tests pass, but independent spec-derived scenarios fail. Appendix A contains the **complete executable reproducer**, rather than relying on ephemeral scratch files. Extract it using the command there; the commands below run from the worktree root. `Q` below denotes the exact command prefix `PYTHONPATH=reconcile:reconcile/tests reconcile/.venv/bin/python -X pycache_prefix=/private/tmp/cp2b-qa/report-repro-cache -m pytest -q -p no:cacheprovider /private/tmp/cp2b-qa/test_independent.py`.

### F1 — HIGH: partial proof results fail open, suppress retries and resolve persisted evidence during a reorg

Reproduce: `PYTHONPATH=reconcile:reconcile/tests reconcile/.venv/bin/python -X pycache_prefix=/private/tmp/cp2b-qa/report-repro-cache -m pytest -q -p no:cacheprovider /private/tmp/cp2b-qa/test_independent.py -k partial` (exit 1, **2 failed**).

- `test_m11_partial_empty_api_is_unknown_not_clean`: start a zero-update round with three active clusters, return two matching leaves and `cluster not found` for the third. Restore that third leaf with a mismatching EB and run again after 300 seconds. Expected: UNKNOWN for the missing cluster and a retry that discovers the mismatch and pages. Actual: the partial result is saved as `check:m11:zero_proof` with `error: null`; subsequent API calls do not increase and `liveness` stays **warn**. The incident says all two checked clusters match, while its evidence has an unknown third cluster.
- `test_m11_partial_api_during_reorg_keeps_known_mismatch`: first establish a persisted `state-mismatch` warning, then reorg below its evaluated block, keeping the commit and returning no proof for the affected cluster. Expected: re-report the unknown key or raise and retain the reorg marker (README §6.2). Actual: **no open incident**, marker cleared, and a resolution says “every checked cluster matches its leaf.” The affected cluster was never evaluated. This is the inherited fail-open/reorg failure, independently reproduced without replacing the check's `run` method.

The all-unreachable control behaves better: `test_m11_health_only_reorg_with_api_down_keeps_episode` passes and keeps the persisted warning. The defect is partial completeness being treated as success.

### F2 — MEDIUM: same-block slot rotation assigns a successful updater transaction to the old owner

Reproduce: `PYTHONPATH=reconcile:reconcile/tests reconcile/.venv/bin/python -X pycache_prefix=/private/tmp/cp2b-qa/report-repro-cache -m pytest -q -p no:cacheprovider /private/tmp/cp2b-qa/test_independent.py -k test_m11_rotation_then_update_same_block_uses_new_owner` (exit 1, 1 failed).

Commit a round, emit `OracleReplaced` for slot 4, and successfully update a cluster from the **new** slot owner later in the same block. Expected: count that update and stay silent for liveness. Actual: `{'liveness': 'warn'}`; the transaction is classified as another sender because `updater_at` only considers rotations in strictly earlier blocks. The requested mid-round owner-change attack therefore fails.

### F3 — MEDIUM: M9 does not implement the unconditional two-root warning

Reproduce: `PYTHONPATH=reconcile:reconcile/tests reconcile/.venv/bin/python -X pycache_prefix=/private/tmp/cp2b-qa/report-repro-cache -m pytest -q -p no:cacheprovider /private/tmp/cp2b-qa/test_independent.py -k 'test_m9_dissent_before_timeout_same_block or test_m9_two_roots_from_one_slot_after_midround_owner_change'` (exit 1, 2 failed).

- Two different roots proposed for one blockNum in one block, without quorum: expected **warn** as soon as the second root is observed; actual **no incident/message** until settlement/timeout. The spec gives this trigger no 30-minute waiting condition.
- One slot votes root A, changes owner mid-round, then votes root B; slots 2 and 3 commit A: expected **warn** for two proposed roots; actual **no M9 incident/message**, even after commit. A slot that also voted for the committed root is removed from dissent attribution, leaving the two-root trigger uncovered.

The benign unanimous-round tests pass. The failure is in the positive trigger, not general silence.

### F4 — MEDIUM: M10 misses an OracleReplaced duplicate that heals before the next sweep

Reproduce: `PYTHONPATH=reconcile:reconcile/tests reconcile/.venv/bin/python -X pycache_prefix=/private/tmp/cp2b-qa/report-repro-cache -m pytest -q -p no:cacheprovider /private/tmp/cp2b-qa/test_independent.py -k test_m10_transient_duplicate_before_next_sweep` (exit 1, 1 failed).

After establishing the baseline, emit two replacements in one block: slot 2 first receives slot 1's address, then receives a fresh address. Expected: the first `OracleReplaced` meets the already-held-slot **page** trigger (and may then resolve). Actual: **zero incidents/messages** because only the final live slot map is inspected. The pinned contract ordinarily rejects duplicate assignments; this synthetic anomaly is exactly the anomaly the promised duplicate trigger detects. The permanent-duplicate supplied test alone cannot catch this miss.

### F5 — MEDIUM: M11 excludes an earlier revert based on a later other-sender update

Reproduce: `PYTHONPATH=reconcile:reconcile/tests reconcile/.venv/bin/python -X pycache_prefix=/private/tmp/cp2b-qa/report-repro-cache -m pytest -q -p no:cacheprovider /private/tmp/cp2b-qa/test_independent.py -k test_m11_later_other_sender_cannot_explain_earlier_revert` (exit 1, 1 failed).

In a round with one successful updater transaction, record an updater `StaleUpdate` revert, then have another sender successfully set the same cluster EB **after** that revert. Expected: the later success cannot explain the earlier failure, so 1/2 counted transactions reverted and **warn** fires. Actual: **no incident**; any matching other-sender event anywhere in the round makes the revert benign. The spec's exception is for another sender having **already** set that EB.

### F6 — MEDIUM: weekly comparison warns on a mismatch that has not aged one round

Reproduce: `PYTHONPATH=reconcile:reconcile/tests reconcile/.venv/bin/python -X pycache_prefix=/private/tmp/cp2b-qa/report-repro-cache -m pytest -q -p no:cacheprovider /private/tmp/cp2b-qa/test_independent.py -k test_m11_latest_round_mismatch_must_age_one_round` (exit 1, 1 failed).

Create the first/current committed round, make one leaf differ, and advance 305 blocks beyond its update window without another committed round. Expected under M11's weekly trigger: no weekly warning until a mismatch is **older than one round**. Actual: `{'state-mismatch': 'warn'}` immediately after the 60-minute window. There is no mismatch-age observation across rounds in the persisted result. This is distinct from the immediate **page** escalation of a zero-update round, which the scenario avoids by sending an updater transaction.

### F7 — MEDIUM: dissent streak spans a non-dissenting round

Reproduce: `PYTHONPATH=reconcile:reconcile/tests reconcile/.venv/bin/python -X pycache_prefix=/private/tmp/cp2b-qa/report-repro-cache -m pytest -q -p no:cacheprovider /private/tmp/cp2b-qa/test_independent.py -k test_m9_streak_requires_consecutive_rounds` (exit 1, 1 failed).

Run slot 3 dissent, dissent, skipped vote, dissent. Expected from the spec's “three dissenting rounds in a row”: the last three rounds are dissent/skip/dissent, so **warn**, not a three-consecutive-dissent page. Actual: **page**. The worker's local decision that an absent vote neither counts nor resets a streak, and its supplied test asserting that decision, do not override the spec. This is a spec-interpretation difference requiring a repair or explicit spec amendment, not approval inferred from the implementer's record.

### F8 — MEDIUM: minute-based failed-round and updater deadlines stop with the block number

Reproduce: `PYTHONPATH=reconcile:reconcile/tests reconcile/.venv/bin/python -X pycache_prefix=/private/tmp/cp2b-qa/report-repro-cache -m pytest -q -p no:cacheprovider /private/tmp/cp2b-qa/test_independent.py -k 'test_m9_30_minute_timeout_without_new_block or test_m11_60_minute_window_without_new_block'` (exit 1, 2 failed).

After observing a proposal, advance the fake worker clock 31 minutes with no further block; after observing a RootCommitted with no updater transaction, advance it 61 minutes with no further block. Expected: M9 failed-round **page**, M11 zero-update **warn**, respectively. Actual: **both remain silent**. Both rules convert minutes to a fixed count of 12-second blocks. Configurability does not make that elapsed-time measurement correct when block production stops or slots are missed. Separately, V2b.7's **9-hour** clock-driven rule works, including no events, restart and daily configuration controls.

## 2. Claims I could NOT confirm

- “Every trigger line has its severity and benign twin” is not confirmed: F1–F8 show missing or contradictory positive/negative cases. In particular, the M9 immediate split and same-slot two-root cases, event-time M10 duplicate, and weekly mismatch **age** condition are absent from the supplied coverage.
- “Never clean” for proof inputs and the episode book's complete re-evaluation obligation are false for **partial** proof results (F1). Empty/all-down proof responses are covered and pass, but that is insufficient.
- “Updater address at each transaction's block” is insufficient for transaction order within that block (F2); benign revert attribution also loses order (F5).
- M11's complete `IncorrectClusterState` **M1 incident link** was not demonstrated: the supplied passing scenario asserts a `m1_handoff` list of transaction hashes and prose “go to M1,” not an M1 incident ID/link. No M1 implementation is present on this lane. End-to-end linking remains for integration; do not credit it from these tests.
- I **did** independently reproduce the outage replay and the three recorded current rounds from new archive reads, and ran the LIVE full-state leaf comparison: see the LIVE rows below. None of those green measurements removes the synthetic failures above.
- The Monitor-owned info notifications for every replacement and other-sender updates belong to CP1A, not this check lane; they were not independently re-run here and are not credited to CP2b.

## 3. Verification matrix

### Preflight and frozen-file measurements

- `git rev-parse HEAD`: exact frozen head `4e64eb1064c3e33a5df8e090544dc11ac6ee485b`, exit 0. `git status --porcelain`: empty at start. `df -h .`: 352 GiB available.
- `export PATH=$HOME/.foundry/bin:$PATH; command -v anvil`: `/Users/marco/.foundry/bin/anvil`. No mainnet transactions; no real alert transport; fixtures use record mode/Capture.
- Fresh venv absent. `uv --cache-dir /private/tmp/cp2b-qa-uv venv reconcile/.venv --python 3.12`: exit 0; Python 3.12.15. `uv --cache-dir /private/tmp/cp2b-qa-uv pip install --python reconcile/.venv/bin/python -r reconcile/requirements-dev.txt`: exit 0 with network escalation. Initial default-cache attempt hit EPERM outside writable roots; initial sandboxed pip hit DNS restrictions; neither was treated as a code failure. No other installs.
- `git diff --exit-code plan/monitoring-service HEAD -- reconcile/tests/harness.py reconcile/tests/test_framework.py reconcile/tests/test_worker.py`: exit 0, no output. `git diff bd531b4^ 500abe4 --` those three files: exactly the ruled patch, +7 / +2−1 / +3 lines respectively. `git diff --name-only f92e2b7 HEAD -- reconcile` lists only the four lane modules, lane fixtures/tests, the lane import, and the three own config objects; **no other frozen CP1B file changed**. The two shared-file diffs are one import line and only `checks.m9/m10/m11` objects.
- Required triage scripts were read from `/Users/marco/ssv/repos/ssv-network-triage/evidences/eb-oracle-cadence/scan{,2,3,4}.mjs`; they are not in this worktree. Their private-env-consuming fetch commands were not executed; all LIVE reads below use public endpoints. No config/, config.zip or .env file was touched.

Each command below was run separately. The selected tests are intentionally repeated to meet the exact matrix selectors; the full suite was run once under the sandbox and once with only the required local-port permission. No redundant expensive full-suite or LIVE comparison reruns were performed after unchanged passing evidence.

| V-ID | Exact command | Exit / salient result | Verdict |
|---|---|---|---|
| V2b.1-m9 | `reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider reconcile/tests/test_m9.py` | 0; 37 passed in 1.72s | PASS supplied tests; independent failures F1–F8 |
| V2b.2-m9 | `reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider reconcile/tests/test_m9.py -k thresholds_from_chain` | 0; 2 passed, 35 deselected in 0.05s | PASS |
| V2b.3-m9 | `reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider reconcile/tests/test_m9.py -k restart` | 0; 2 passed, 35 deselected in 0.05s | PASS |
| V2b.4-m9 | `reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider reconcile/tests/test_m9.py -k rpc_budget` | 0; 1 passed, 36 deselected in 0.08s | PASS |
| V2b.1-m10 | `reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider reconcile/tests/test_m10.py` | 0; 15 passed in 0.14s | PASS supplied tests; independent failures F1–F8 |
| V2b.2-m10 | `reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider reconcile/tests/test_m10.py -k thresholds_from_chain` | 0; 1 passed, 14 deselected in 0.04s | PASS |
| V2b.3-m10 | `reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider reconcile/tests/test_m10.py -k restart` | 0; 1 passed, 14 deselected in 0.04s | PASS |
| V2b.4-m10 | `reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider reconcile/tests/test_m10.py -k rpc_budget` | 0; 1 passed, 14 deselected in 0.06s | PASS |
| V2b.1-m11 | `reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider reconcile/tests/test_m11.py` | 0; 34 passed in 0.32s | PASS supplied tests; independent failures F1–F8 |
| V2b.2-m11 | `reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider reconcile/tests/test_m11.py -k thresholds_from_chain` | 0; 1 passed, 33 deselected in 0.04s | PASS |
| V2b.3-m11 | `reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider reconcile/tests/test_m11.py -k restart` | 0; 2 passed, 32 deselected in 0.05s | PASS |
| V2b.4-m11 | `reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider reconcile/tests/test_m11.py -k rpc_budget` | 0; 1 passed, 33 deselected in 0.11s | PASS |
| V2b.7 | `reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider reconcile/tests/test_m9.py -k from_the_clock` | 0; 1 passed, 36 deselected in 0.04s | PASS |
| V2b.6-suite | `reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider reconcile/tests` | first sandbox run: 1, 325 passed / 1 metrics-bind EPERM; authorized rerun: 0, **326 passed in 12.59 s** | PASS (environment cleared) |
| V2b.6-ruff | `reconcile/.venv/bin/ruff check reconcile` | 0; All checks passed! | PASS |
| V2b.5 | mutation commands and exact replacements in §4 / Appendix B | each mutated node exit 1, restored node exit 0 | PASS sensitivity; does not prove completeness |
| Independent spec/reorg sweep | `PYTHONPATH=reconcile:reconcile/tests reconcile/.venv/bin/python -X pycache_prefix=/private/tmp/cp2b-qa/final-all-cache -m pytest -q -p no:cacheprovider /private/tmp/cp2b-qa/test_independent.py` | 1; **11 failed / 14 passed** across 25 scenarios; revised partial-proof reproducer separately re-run: 2 failed / 23 deselected | FAIL |

### Trigger derivation from the spec (not the implementer's enumeration)

The authoritative text read was spec §3 M9–M11 (lines 267–337). M9 has seven conditions when the split/streak line is separated; M10 has two worker page conditions plus the Monitor-owned info condition; M11 has five worker conditions when zero-update warn and mismatch escalation are separated, plus Monitor-owned other-sender info. The weekly condition includes an age clause, and the revert condition includes a **prior** other-sender exception.

| Spec trigger | Required severity / silent twin measurement | Outcome |
|---|---|---|
| M9 proposal without commit in 30 min | supplied timeout page + timely-commit silent twin pass; independent clock-only timeout fails | F8 |
| M9 no commit >9 h, 25 h if daily | supplied clock page / 8-hour cadence silent twin pass; own no-event daily restart, first-sweep-old-baseline, routing controls pass | PASS V2b.7 |
| M9 two roots for same blockNum | supplied committed-dissent warn / unanimous silent twin pass; own unsettled and same-slot split fail | F3 |
| M9 three consecutive dissenting rounds from slot | supplied page / two-then-agreement twin pass; own skipped-round interruption wrongly pages | F7 |
| M9 committed with <3 distinct voting slots | supplied page / three distinct silent twin pass; own proposal-after-commit weak-quorum control passes | PASS |
| M9 slot absent from committed root in last 6 rounds | supplied warn / routine skipped-vote twin pass; live slot-set reload tests pass | PASS |
| M9 slot commitRoot reverts | supplied warn / non-slot sender silent twin pass; inbox missing/unreadable and rotated-address tests pass | PASS |
| M10 replacement while round open | supplied page / between-rounds silent twin, both same-block commit/rotation orders pass | PASS |
| M10 newOracle already holds slot | supplied persistent-duplicate page / fresh-address silent twin pass; own transient duplicate fails | F4 |
| M11 zero updater tx within60 min | supplied warn / success silent twin pass; own same-block owner and clock-only window fail | F2, F8 |
| M11 zero-update leaf mismatch | supplied page / matching-leaves warn twin and all-down health pass; own partial-proof recovery fails | F1 |
| M11 two consecutive zero rounds | supplied page / zero separated by good round twin pass; restart with prior zero state passes | PASS |
| M11 revert rate >10%, benign prior other-sender exclusion, M1 handoff | >10% warn and exact10% silent twin pass; own later-sender exclusion fails; actual M1 incident link not measured | F5 / integration claim unconfirmed |
| M11 weekly every active ETH cluster, mismatch older than one round | supplied mismatch warn / matching silent twin and all-down defer pass; own age twin and partial-input reorg fail; LIVE full-state comparison green | F1, F6 |

### LIVE re-measurements

**Outage replay — independently measured result:**

```json
{
 "source": "outage.json",
 "rpc": "https://eth-pokt.nodies.app",
 "logs": 89,
 "rounds": 21,
 "commits": 13,
 "checkpoint": 25728449,
 "incidents": {
  "m9": [
   [
    "dissent:3",
    "page",
    "open"
   ],
   [
    "absent:3",
    "warn",
    "open"
   ],
   [
    "failed-round",
    "page",
    "resolved"
   ],
   [
    "cadence",
    "page",
    "resolved"
   ],
   [
    "split",
    "warn",
    "resolved"
   ]
  ],
  "m10": []
 },
 "messages": {
  "m9": 75,
  "m10": 0
 },
 "health": [],
 "runs": {
  "m9": {
   "at": 1800582216.0,
   "block": 25728449,
   "reasons": [
    "interval"
   ],
   "findings": 2,
   "rpc_calls": {},
   "errors": 0,
   "last_error": null
  },
  "m10": {
   "at": 1800582216.0,
   "block": 25728449,
   "reasons": [
    "interval"
   ],
   "findings": 0,
   "rpc_calls": {},
   "errors": 0,
   "last_error": null
  }
 }
}
```

**Three recent rounds replay — independently measured result:**

```json
{
 "source": "current.json",
 "rpc": "https://eth-mainnet.public.blastapi.io",
 "logs": 12,
 "rounds": 3,
 "commits": 3,
 "checkpoint": 26240049,
 "incidents": {
  "m9": [],
  "m10": []
 },
 "messages": {
  "m9": 0,
  "m10": 0
 },
 "health": [],
 "runs": {
  "m9": {
   "at": 1800065748.0,
   "block": 26240049,
   "reasons": [
    "interval"
   ],
   "findings": 0,
   "rpc_calls": {},
   "errors": 0,
   "last_error": null
  },
  "m10": {
   "at": 1800065748.0,
   "block": 26240049,
   "reasons": [
    "interval"
   ],
   "findings": 0,
   "rpc_calls": {},
   "errors": 0,
   "last_error": null
  }
 }
}
```

**Weekly full-state comparison — independently measured result:**

```json
{
 "rpc": "ethereum-rpc.publicnode.com",
 "block": 26241594,
 "latest_committed_ref": 26239590,
 "api_rows_active_eth": 530,
 "api_tuple_stale_skipped": 0,
 "compared": 530,
 "mismatches": [],
 "unknown": 0,
 "unknown_reasons": [],
 "error": null,
 "rpc_calls": {
  "eth_getStorageAt": 531,
  "eth_call": 530
 },
 "seconds": 280.0
}
```

- **Outage archive reads:** own script `reconcile/.venv/bin/python /private/tmp/cp2b-qa/live_reads.py` (Appendix C), archive endpoint `https://eth-pokt.nodies.app`; outage phase succeeded: **89 logs, 21 reference rounds, 13 commits**, event blocks **25,680,231–25,728,058**. For each of the 21 recorded reference blocks, fetched `[ref+1,ref+200]` in 50-block chunks (84 read requests). Own fetched logs exactly equal the recorded logs after normalizing order. Canonical JSON log SHA-256: `606a94335c00c9575e1cedb54b629be812760229ffee53ce33b1b1cbc9c6c44f`. Replay command: `reconcile/.venv/bin/python -X pycache_prefix=/private/tmp/cp2b-qa/replay-final-cache plans/monitoring-service/workers/cp2b/evidence/live_replay.py /private/tmp/cp2b-qa/outage.json 400`, exit 0: failed-round/cadence pages resolve; dissent:3 page and absent:3 warn remain; split warn resolves; M10 silent. A final replay ran **after all mutations were restored**.
- **Three recorded recent mainnet rounds:** own script `reconcile/.venv/bin/python /private/tmp/cp2b-qa/current_recorded.py` (Appendix C), endpoint `https://eth-mainnet.public.blastapi.io`; exit 0, **12 logs / 3 rounds / 3 commits**, reference blocks **26,234,805 / 26,237,195 / 26,239,590**, event blocks **26,234,870–26,239,655**. Fetch `[ref+1,ref+200]` in 10-block chunks (60 read requests); logs exactly equal the saved record. Canonical JSON log SHA-256: `98a18fdca78744b1ef1a285de562324bc4778f01dc50b2125ec026854de4f913`. Replay command: same `live_replay.py` command with `/private/tmp/cp2b-qa/current.json 400`, exit 0, **0 incidents / 0 messages / 0 health errors** for M9/M10.
- **Weekly full-state LIVE:** `reconcile/.venv/bin/python plans/monitoring-service/workers/cp2b/evidence/live_m11_leaves.py https://ethereum-rpc.publicnode.com`, exit 0; RPC pin **26,241,594**, latest reference **26,239,590**; SSV API v4 inventory and `https://ssv-oracle.ssv.network/api/v1/proof/{clusterId}`; **530 active ETH clusters, 0 stale tuples, 530 compared, 0 mismatches, 0 unknown**, 531 storage reads / 530 eth_call, comparison **280.0 seconds**. This runs the actual check's `compare_leaves`; it is not a substitute for the failed weekly age/reorg semantics. The synchronous ~280-second worker stall concern is reproduced and remains an integration performance risk.
- **Endpoint failures / incomplete attempts, explicitly not credited as passes:** initial full-state command against `https://eth-pokt.nodies.app` exited 1, `rpc.RpcError: transport error: HTTP 429`; first combined archive script's current phase rejected 200-block ranges, JSON-RPC `-32001: Block range too large: maximum allowed is 50 blocks on your current plan`; a fresh broad recent scan on nodies returned `HTTP Error 429: Too Many Requests`; plain-urllib publicnode head request returned `HTTP Error 403: Forbidden`. These subattempts are NOT RUN/incomplete for their intended rows. The successful narrower Blast reads and worker-RPC publicnode full-state run above complete the required LIVE rows. No DEFERRED row was fabricated and no secret endpoint was used.

## 4. Falsifiers

The complete byte-restoring runner is in Appendix B. Command: `reconcile/.venv/bin/python /private/tmp/cp2b-qa/falsify.py`. Every file was restored from its pre-mutation bytes in a `finally` block; `git status --porcelain` was empty after **each** final red/green pair.

| Falsifier | Exact change | Named test | Mutated / restored result |
|---|---|---|---|
| F9-streak | `m9_oracle_quorum.py`: `n >= streak_n else` → `n > streak_n else` | `reconcile/tests/test_m9.py::test_three_dissenting_rounds_in_a_row_from_one_slot_page` | exit 1: 1 failed in 0.16s; exit 0: 1 passed in 0.33s; bytes identical, porcelain empty |
| F9-clock | `m9_oracle_quorum.py`: `age = ctx.clock() - mark["at"]` → `age = (block - last_block) * self.spb` | `reconcile/tests/test_m9.py::test_no_root_committed_for_9h_pages_from_the_clock_without_any_event` | exit 1: 1 failed in 0.15s; exit 0: 1 passed in 0.16s; bytes identical, porcelain empty |
| F10-open | `m9_oracle_common.py`: `own = rnd.commit if rnd.commit is not None and (before is None or rnd.commit.pos < before) else None` → `own = rnd.commit` | `reconcile/tests/test_m10.py::test_same_block_rotation_then_commit_pages_once_and_resolves` | exit 1: 1 failed in 0.12s; exit 0: 1 passed in 0.12s; bytes identical, porcelain empty |
| F11-window | `m11_updater_liveness.py`: `blocks_for(config["update_window_s"], config["seconds_per_block"])` → `blocks_for(100 * config["update_window_s"], config["seconds_per_block"])` | `reconcile/tests/test_m11.py::test_zero_updates_within_60_min_warns` | exit 1: 1 failed in 0.13s; exit 0: 1 passed in 0.12s; bytes identical, porcelain empty |
| F-book-reorg | `m9_oracle_common.py`: `r["evidence_block"] > ctx.reorg_ancestor` → `r["evidence_block"] < ctx.reorg_ancestor` | `reconcile/tests/test_m9.py::test_reorg_depth_n_removing_the_votes_resolves_with_the_reorg_text` | exit 1: 2 failed in 0.16s; exit 0: 2 passed in 0.13s; bytes identical, porcelain empty |

The first book mutation restoration control incorrectly stayed red because a same-size `<`/`>` mutation reused Python bytecode within one timestamp tick. I rejected that pair, then reran **all five** mutations with a separate `-X pycache_prefix` for every mutated and restored process: the table above is the final measured pair, including the book's 2 red / 2 green parametrizations. No green was credited from the stale-bytecode run. An initial replay overlapped mutation execution and was discarded; the archived/recent replays reported above ran against restored source with isolated bytecode caches.

**My additional falsifiers (outside the supplied scenarios):** M9 immediate same-block two-root and same-slot owner-change splits (red; F3), M10 duplicate healed before the next sweep (red; F4), M11 partial proof recovery/reorg and same-block owner update (red; F1/F2). These run against unmodified implementation source, so their required “red” is a real product failure rather than a mutation sensitivity result. Other independent probes include later-sender revert attribution, one-round mismatch age, interrupted dissent streak and clock-only deadlines (all red, listed in findings).

**Independent passing controls:** six shorter replacement-chain book cases (M9/M10/M11 × confirmations0/12), daily25-hour no-event silence with a restart during the silence and after the page, first sweep with no baseline and >9-hour-old chain history, proposal landing after its commit (correct weak-commit page), page/warn Telegram routing and 6h/24h re-alert cadence, three direct check calls with unchanged incident/outbox/notifier snapshots, and the all-down health-only M11 reorg. Supplied suites additionally re-measure depth1, depth beyond confirmation windows, event-block removal, persisted proof invalidation, crash/restart between detection and handling, and chain-parameter reloads. F1 remains the counterexample to a complete green reorg claim.

## 5. Evidence-plane writes

- CP2b status only: `IMPLEMENTED — AWAITING QA` → `QA FAILED` on frozen head `4e64eb1064c3e33a5df8e090544dc11ac6ee485b`, linking this report. No other checkpoint status changed.
- Wrote `plans/monitoring-service/qa/cp2b-attempt-1.md` and appended one `event-cp2b-qa-1` in `plans/monitoring-service/execution-log.md`.
- Local untracked/ignored venv: `reconcile/.venv`; all additional scripts, cache prefixes, RPC dumps and console logs under `/private/tmp/cp2b-qa` (and the explicitly relocated uv cache). No QA tests added to tracked source and no implementation repair. No next checkpoint started.

## 6. Repo proof

Measured after all source mutation restorations, **before** permitted evidence-plane writes:

```text
$ git status --porcelain
<empty>
$ git diff --name-only 4e64eb1064c3e33a5df8e090544dc11ac6ee485b
<empty>
$ git diff --exit-code HEAD -- reconcile
<empty>, exit 0
```

Final precommit measurements after the permitted report/status/event writes:

```text
$ git status --porcelain
 M plans/monitoring-service/execution-log.md
 M plans/monitoring-service/plan.md
?? plans/monitoring-service/qa/cp2b-attempt-1.md
$ git diff --name-only 4e64eb1064c3e33a5df8e090544dc11ac6ee485b
plans/monitoring-service/execution-log.md
plans/monitoring-service/plan.md
$ git diff --exit-code HEAD -- reconcile
<empty>, exit 0
```

The report is still untracked at this measurement, so it appears in porcelain and enters the base diff once staged. **No implementation file appears in either output.** Only these three documentation paths are staged and committed. This report's verdict concerns the frozen head, not its subsequent QA documentation commit. Four embedded scripts were extracted and byte-compared to the executed scratch scripts; the extracted independent suite re-ran at exit 1, **11 failed / 14 passed in 0.31 s**. The passing full suite and LIVE full-state comparison were not repeated after unchanged source. Final source restores were byte-identical and the source-only diff stayed empty.

## Appendix A — self-contained independent reproducer

<!-- BEGIN test_independent.py -->
```python
import pytest,json
from fixtures_oracle import oracle_worker,tick,open_keys,sent,SLOTS,UpdaterWorld,restart,WRP
from eb_storage import u256

# Each test states its expected behavior from the spec/README, not from implementation output.
def test_m9_dissent_before_timeout_same_block(tmp_path):
 oc,w=oracle_worker(tmp_path);tick(w)
 ref=oc.c.number-1
 oc.vote(ref,1,mine=False);oc.vote(ref,3,variant=1,mine=False);oc.c.mine();tick(w,oc,1)
 assert any(r['severity']=='warn' for r in sent(w,'m9')),open_keys(w,'m9')

def test_m9_streak_requires_consecutive_rounds(tmp_path):
 oc,w=oracle_worker(tmp_path);tick(w)
 for variants,voters in [({3:1},(1,2,3,4)),({3:1},(1,2,3,4)),({},(1,2,4)),({3:1},(1,2,3,4))]:
  oc.round(variants=variants,voters=voters);tick(w,oc,1)
 assert open_keys(w,'m9').get('dissent:3')=='warn',open_keys(w,'m9')

def test_m10_transient_duplicate_before_next_sweep(tmp_path):
 oc,w=oracle_worker(tmp_path,ids=('m10',));tick(w)
 oc.replace(2,SLOTS[1],mine=False);oc.replace(2,'0x'+'77'*20,mine=False);oc.c.mine();tick(w,oc,1)
 assert any(r['severity']=='page' and r['ikey'].startswith('dup:') for r in sent(w,'m10')),sent(w,'m10')

def test_m11_partial_empty_api_is_unknown_not_clean(tmp_path):
 u=UpdaterWorld(tmp_path);tick(u.w)
 u.round(updates=());del u.api.leaves[u.cids[1]];u.close()
 saved=u.w.db.meta('check:m11:zero_proof');assert saved['result']['unknown']
 calls=u.api.calls
 u.api.leaves[u.cids[1]]=99;tick(u.w,u.oc,1,seconds=300)
 assert u.api.calls>calls and open_keys(u.w,'m11').get('liveness')=='page',(u.api.calls,calls,open_keys(u.w,'m11'),saved)

def test_m11_partial_api_during_reorg_keeps_known_mismatch(tmp_path):
 u=UpdaterWorld(tmp_path);tick(u.w)
 u.round(updates=(0,));u.set_eb(32,leaves={u.cids[1]:64});u.close()
 assert open_keys(u.w,'m11').get('state-mismatch')=='warn'
 fork=u.c.number-2;u.c.reorg(fork);u.set_eb(32);del u.api.leaves[u.cids[1]];u.c.mine(3);tick(u.w)
 assert 'state-mismatch' in open_keys(u.w,'m11') or u.w.db.meta('reorg:m11'),(open_keys(u.w,'m11'),sent(u.w,'m11'))

def test_m11_latest_round_mismatch_must_age_one_round(tmp_path):
 u=UpdaterWorld(tmp_path);tick(u.w)
 u.round(updates=(0,));u.set_eb(32,leaves={u.cids[1]:64});u.close()
 assert 'state-mismatch' not in open_keys(u.w,'m11'),open_keys(u.w,'m11')

def test_m11_rotation_then_update_same_block_uses_new_owner(tmp_path):
 u=UpdaterWorld(tmp_path);tick(u.w)
 u.commit();new='0x'+'77'*20
 u.oc.replace(4,new,mine=False);u.update(0,sender=new);u.close()
 assert 'liveness' not in open_keys(u.w,'m11'),open_keys(u.w,'m11')

def test_m9_no_events_restart_silence_daily_config(tmp_path):
 cfg={'m9':{'max_commit_gap_s':25*3600}}
 oc,w=oracle_worker(tmp_path,checks=cfg);tick(w);tick(w,seconds=24*3600)
 assert open_keys(w,'m9')=={}
 w=restart(tmp_path,w,oc,checks=cfg);tick(w);tick(w,seconds=1.1*3600)
 assert open_keys(w,'m9')=={'cadence':'page'}
 n=len(sent(w,'m9'));w=restart(tmp_path,w,oc,checks=cfg);tick(w)
 assert len(sent(w,'m9'))==n

def test_m9_first_sweep_without_baseline_long_silence(tmp_path):
 oc,w=oracle_worker(tmp_path);oc.c.mine(3000);tick(w)
 assert open_keys(w,'m9')=={'cadence':'page'}

def test_m9_proposal_after_commit_is_not_counted_as_quorum(tmp_path):
 oc,w=oracle_worker(tmp_path);tick(w);ref=oc.c.number-1;root=oc.root(ref)
 oc.c.emit_event('RootCommitted',[root,'0x%064x'%ref],b'')
 for slot in (1,2,3):
  oc.c.emit_event(WRP,[root,'0x%064x'%ref],u256(10)+u256(30)+u256(slot)+u256(int(SLOTS[slot],16)))
 oc.c.mine();tick(w,oc,1)
 assert open_keys(w,'m9').get('weak-commit')=='page'

@pytest.mark.parametrize('monitor',['m9','m10','m11'])
@pytest.mark.parametrize('confirmations',[0,12])
def test_shorter_replacement_chain_rechecks_books(tmp_path,monitor,confirmations):
 if monitor=='m11':
  u=UpdaterWorld(tmp_path,confirmations=confirmations);w=u.w;oc=u.oc;tick(w,oc,confirmations+2);fork=oc.c.number
  u.round(updates=());u.close(confirmations+5)
 else:
  oc,w=oracle_worker(tmp_path,ids=(monitor,),confirmations=confirmations);tick(w,oc,confirmations+2);fork=oc.c.number
  if monitor=='m9':oc.round(variants={3:1})
  else:
   ref=oc.c.number-1;oc.vote(ref,1);oc.replace(3,'0x'+'77'*20)
  tick(w,oc,confirmations+20)
 assert open_keys(w,monitor)
 oc.reorg(fork,latest=0,refs={fork-1})
 tick(w,oc,confirmations+1)
 assert not open_keys(w,monitor),open_keys(w,monitor)
 assert not w.db.meta('reorg:'+monitor)
 assert w.db.conn.execute(f'SELECT COUNT(*) FROM {monitor}_episodes WHERE closed=0').fetchone()[0]==0

def test_m9_two_roots_from_one_slot_after_midround_owner_change(tmp_path):
 oc,w=oracle_worker(tmp_path);tick(w);ref=oc.c.number-1
 oc.vote(ref,1);oc.replace(1,'0x'+'77'*20);oc.vote(ref,1,variant=1);oc.vote(ref,2);oc.vote(ref,3);tick(w,oc,1)
 assert any(r['severity']=='warn' for r in sent(w,'m9')),open_keys(w,'m9')

def test_m11_later_other_sender_cannot_explain_earlier_revert(tmp_path):
 u=UpdaterWorld(tmp_path);tick(u.w);ref=u.round(updates=(0,))
 u.fail('StaleUpdate',ref,i=1,eb=40)
 u.update(1,eb=40,sender=SLOTS[3]);u.close()
 assert open_keys(u.w,'m11').get('revert-rate')=='warn',open_keys(u.w,'m11')

def test_m9_30_minute_timeout_without_new_block(tmp_path):
 oc,w=oracle_worker(tmp_path);tick(w);oc.vote(oc.c.number-1,1);tick(w)
 tick(w,seconds=31*60)
 assert open_keys(w,'m9').get('failed-round')=='page',open_keys(w,'m9')

def test_m11_60_minute_window_without_new_block(tmp_path):
 u=UpdaterWorld(tmp_path);tick(u.w);u.round(updates=());tick(u.w)
 tick(u.w,seconds=61*60)
 assert open_keys(u.w,'m11').get('liveness')=='warn',open_keys(u.w,'m11')

def test_m9_page_and_warn_routing_realert_cadence(tmp_path):
 oc,w=oracle_worker(tmp_path);tick(w);ref=oc.c.number-1
 oc.vote(ref,1);oc.vote(ref,3,variant=1);tick(w,oc,155)
 initial=sent(w,'m9');assert {r['severity'] for r in initial}=={'warn','page'}
 assert all(r['delivery']=='telegram' for r in initial)
 tick(w,seconds=6*3600+1);w.housekeeping()
 rows=sent(w,'m9');assert sum(r['kind']=='realert' and r['severity']=='page' for r in rows)==1
 assert not any(r['kind']=='realert' and r['severity']=='warn' for r in rows)
 tick(w,seconds=18*3600);w.housekeeping()
 assert sum(r['kind']=='realert' and r['severity']=='warn' for r in sent(w,'m9'))==1

@pytest.mark.parametrize('monitor',['m9','m10','m11'])
def test_checks_return_findings_without_direct_alerts(tmp_path,monitor):
 if monitor=='m11':
  u=UpdaterWorld(tmp_path);w=u.w;oc=u.oc;tick(w);u.round(updates=());u.close()
 else:
  oc,w=oracle_worker(tmp_path,ids=(monitor,));tick(w)
  if monitor=='m9':oc.round(variants={3:1})
  else:
   ref=oc.c.number-1;oc.vote(ref,1);oc.replace(3,'0x'+'77'*20)
  tick(w,oc,1)
 check=w.checks[monitor];block=w.checkpoint()['block'];ctx=w._ctx(check,block,frozenset({'force'}))
 before=list(w.db.conn.execute('SELECT * FROM outbox'));inc_before=list(w.db.conn.execute('SELECT * FROM incidents'));messages=list(w.notifier.sent)
 findings=check.run(ctx,block)
 assert findings
 assert list(w.db.conn.execute('SELECT * FROM outbox'))==before
 assert list(w.db.conn.execute('SELECT * FROM incidents'))==inc_before
 assert w.notifier.sent==messages

def test_m11_health_only_reorg_with_api_down_keeps_episode(tmp_path):
 u=UpdaterWorld(tmp_path);tick(u.w);u.round(updates=(0,));u.set_eb(32,leaves={u.cids[1]:64});u.close()
 old=open_keys(u.w,'m11');assert old=={'state-mismatch':'warn'}
 fork=u.c.number-2;u.c.reorg(fork);u.api.down=True;u.c.mine(3);tick(u.w)
 assert open_keys(u.w,'m11')==old
 assert not sent(u.w,'m11',"kind='resolved'")
 assert u.w.db.conn.execute('SELECT COUNT(*) FROM m11_episodes WHERE closed=0').fetchone()[0]==1
```
<!-- END test_independent.py -->

## Appendix B — mutation runner

<!-- BEGIN falsify.py -->
```python
import subprocess,pathlib,json
base=pathlib.Path('reconcile/checks');py='reconcile/.venv/bin/python'
mutations=[
('F9-streak','m9_oracle_quorum.py','n >= streak_n else','n > streak_n else','reconcile/tests/test_m9.py::test_three_dissenting_rounds_in_a_row_from_one_slot_page'),
('F9-clock','m9_oracle_quorum.py','age = ctx.clock() - mark["at"]','age = (block - last_block) * self.spb','reconcile/tests/test_m9.py::test_no_root_committed_for_9h_pages_from_the_clock_without_any_event'),
('F10-open','m9_oracle_common.py','own = rnd.commit if rnd.commit is not None and (before is None or rnd.commit.pos < before) else None','own = rnd.commit','reconcile/tests/test_m10.py::test_same_block_rotation_then_commit_pages_once_and_resolves'),
('F11-window','m11_updater_liveness.py','blocks_for(config["update_window_s"], config["seconds_per_block"])','blocks_for(100 * config["update_window_s"], config["seconds_per_block"])','reconcile/tests/test_m11.py::test_zero_updates_within_60_min_warns'),
('F-book-reorg','m9_oracle_common.py','r["evidence_block"] > ctx.reorg_ancestor','r["evidence_block"] < ctx.reorg_ancestor','reconcile/tests/test_m9.py::test_reorg_depth_n_removing_the_votes_resolves_with_the_reorg_text')]
results=[]
for name,f,old,new,node in mutations:
 path=base/f;original=path.read_bytes();s=original.decode();assert s.count(old)==1,(name,s.count(old))
 try:
  path.write_text(s.replace(old,new))
  red=subprocess.run([py,'-X','pycache_prefix=/private/tmp/cp2b-qa/cache-'+name+'-red','-m','pytest','-q','-p','no:cacheprovider',node],text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
  pathlib.Path('/private/tmp/cp2b-qa/'+name+'-red.log').write_text(red.stdout)
 finally:path.write_bytes(original)
 green=subprocess.run([py,'-X','pycache_prefix=/private/tmp/cp2b-qa/cache-'+name+'-green','-m','pytest','-q','-p','no:cacheprovider',node],text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
 clean=subprocess.run(['git','status','--porcelain'],text=True,stdout=subprocess.PIPE).stdout
 result={'id':name,'file':f,'old':old,'new':new,'test':node,'red':red.returncode,'green':green.returncode,'restored_bytes':path.read_bytes()==original,'porcelain':clean,'red_summary':red.stdout.splitlines()[-1],'green_summary':green.stdout.splitlines()[-1]}
 results.append(result);print(json.dumps(result),flush=True)
pathlib.Path('/private/tmp/cp2b-qa/falsifiers.json').write_text(json.dumps(results,indent=2))
```
<!-- END falsify.py -->

## Appendix C1 — independent outage archive reads

<!-- BEGIN live_reads.py -->
```python
import json,urllib.request,concurrent.futures,time,pathlib
P=pathlib.Path('/private/tmp/cp2b-qa')
proxy='0xDD9BC35aE942eF0cFa76930954a156B3fF30a4E1'
topics=['0xc0516bad4be527b56ad33b7087029ffee230e390e2f2d1076597820c9e170b67','0x6965b9c8509097ad68ba0eb17fcb9436f4a72a057fe5ac556737580bce80d840']
url='https://eth-pokt.nodies.app'
def rpc(method,params):
 req=urllib.request.Request(url,json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params}).encode(),{'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=35) as r:b=json.load(r)
 if 'error' in b:raise RuntimeError(str(b['error']))
 if 'result' not in b or b['result'] is None:raise RuntimeError(str(b))
 return b['result']
def logs(ab):
 a,b=ab
 return rpc('eth_getLogs',[{'address':proxy,'fromBlock':hex(a),'toBlock':hex(b),'topics':[topics]}])
old=json.load(open('plans/monitoring-service/workers/cp2b/evidence/outage_2026_08_logs.json'))
refs=sorted({int(x['topics'][2],16) for x in old['logs']})
for name,ranges in [('outage',[(r+i,r+i+49) for r in refs for i in (1,51,101,151)]),('current',[(r+1,r+200) for r in [26234805,26237195,26239590]])]:
 try:
  with concurrent.futures.ThreadPoolExecutor(max_workers=4) as e: chunks=list(e.map(logs,ranges))
  rows=sorted([x for c in chunks for x in c],key=lambda x:(int(x['blockNumber'],16),int(x['logIndex'],16)))
  out={'rpc':url,'ranges':ranges,'logs':rows,'observed':time.time()}
  (P/(name+'.json')).write_text(json.dumps(out,indent=2))
  print(name,len(rows),len({int(x['topics'][2],16) for x in rows}),sum(x['topics'][0]==topics[1] for x in rows),flush=True)
 except Exception as e: print(name,type(e).__name__,str(e),flush=True)
```
<!-- END live_reads.py -->

## Appendix C2 — independent recent-round archive reads

<!-- BEGIN current_recorded.py -->
```python
import json,urllib.request,time,pathlib
url='https://eth-mainnet.public.blastapi.io';logs=[];ranges=[]
for ref in [26234805,26237195,26239590]:
 # independently fetched outage confirms commits normally land at ref+65: cover ref+1..200 without assuming it.
 for i in range(1,201,10):
  a,b=ref+i,ref+i+9;ranges.append([a,b])
  data={'jsonrpc':'2.0','id':1,'method':'eth_getLogs','params':[{'address':'0xDD9BC35aE942eF0cFa76930954a156B3fF30a4E1','fromBlock':hex(a),'toBlock':hex(b),'topics':[['0xc0516bad4be527b56ad33b7087029ffee230e390e2f2d1076597820c9e170b67','0x6965b9c8509097ad68ba0eb17fcb9436f4a72a057fe5ac556737580bce80d840']]}]}
  req=urllib.request.Request(url,json.dumps(data).encode(),{'Content-Type':'application/json','User-Agent':'ssv-eb-reconcile/1.0'})
  with urllib.request.urlopen(req,timeout=30) as r:body=json.load(r)
  if 'error' in body:raise RuntimeError(body['error'])
  logs+=body['result']
pathlib.Path('/private/tmp/cp2b-qa/current.json').write_text(json.dumps({'rpc':url,'ranges':ranges,'logs':logs,'observed':time.time()},indent=2))
print('current recorded',len(logs),sorted({int(l['topics'][2],16) for l in logs}))
```
<!-- END current_recorded.py -->

To extract the durable scripts and re-run any single finding (from the worktree root):

```sh
python3 - <<'EXTRACT'
from pathlib import Path
s = Path('plans/monitoring-service/qa/cp2b-attempt-1.md').read_text()
p = Path('/private/tmp/cp2b-qa')
p.mkdir(exist_ok=True)
for name in ('test_independent.py', 'falsify.py', 'live_reads.py', 'current_recorded.py'):
    code = s.split('<!-- BEGIN ' + name + ' -->\n```python\n', 1)[1].split('\n```\n<!-- END ' + name + ' -->', 1)[0]
    (p / name).write_text(code + '\n')
EXTRACT
PYTHONPATH=reconcile:reconcile/tests reconcile/.venv/bin/python -X pycache_prefix=/private/tmp/cp2b-qa/report-repro-cache -m pytest -q -p no:cacheprovider /private/tmp/cp2b-qa/test_independent.py
```

Expected at the frozen implementation: **exit 1, 11 failed / 14 passed**, with the failure names matching F1–F8. Use the `-k` expressions in each finding for minimal runs. Public read scripts need the session's network permission; they perform only GET/read-only JSON-RPC requests. They intentionally expose exact endpoint errors rather than treating an unsuccessful read as a pass.
