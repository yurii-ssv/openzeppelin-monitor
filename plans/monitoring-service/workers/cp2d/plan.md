# Worker: CP2d — Cluster integrity and capacity (M1, M2)

Task contract: [plan.md §8 CP2d](../../plan.md) · Attempt log: [attempts.md](attempts.md) · QA contract: [qa/QA-CONTRACT.md](../../qa/QA-CONTRACT.md)

- Actual starting worktree/branch/head/status: `/Users/marco/orca/workspaces/ssv-network-monitor/ms-cp2d`, branch `mtabasco/ms-cp2d`, head `96d1c32fe3446ec7092e849e401f851a0d9b2cf3`, `git status --porcelain | wc -l` = 0. Integration merges (coordinator rulings, fast-forward): `438904e` (bd531b4), `1154c38` (500abe4).
- Live authority: Orca Task `task_aa8cd71dc363`, Dispatch `ctx_f28f28892d56` (from the injected preamble).
- Requested/effective model and effort: requested Claude Opus high (task title); the worker cannot read the start receipt — coordinator records `launch.effective`.
- Allowed writes / prohibited actions / local commit authority: `reconcile/checks/m1_*.py`, `m2_*.py`, their tests, `reconcile/tests/fixtures_cluster.py`, `reconcile/tests/fixtures/receipts/*.json`, `checks.m1`/`checks.m2` in `reconcile/config.example.json`, one line in `reconcile/checks/__init__.py`, `plans/monitoring-service/workers/cp2d/**`, the CP2d status line in plan.md. Commits on the lane branch only; never push; mainnet read-only; record mode / TEST_* only.
- Observable completion and owned verification IDs: A6; V2x.1–V2x.3, V2x.5, V2x.6, V2d.7 (V2x.4 not owned, run anyway).
- Dependencies and relevant design/semantic sources: plan §1, §2, §6; `reconcile/checks/README.md` (interface, §6.1/§6.2 reorg rule, inbox gate); spec M1, M2, §4, §5; `ssv-network-triage/evidences/#91419/report-family-stale-event-scan.ts`, `#88326/report-operator-capacity-impact.ts`; ssv-network `f7ac487` (`SSVViews.isLiquidated/isLiquidatable(SSV)/getOperatorById(SSV)`, `ClusterLib.validateHashedCluster`, ISSVClusters/ISSVValidators signatures).
- Commit capability checked: yes (`git config user.name` = Marco Tabasco; commits `f0b146c`, `7bf6df7`, `ad912ce`, `7cf0bf1`).

| Step | Status | Latest evidence | Exact next action |
|---|---|---|---|
| S1 read + venv + baseline | DONE | [attempt 1](attempts.md#attempt-1): 240 passed at base | — |
| S2 M1 + M2 + fixtures + scenarios | DONE | `f0b146c`, `7bf6df7`, `7cf0bf1` | — |
| S3 V2d.7 receipts LIVE once, replay GATED | DONE | `ad912ce`; evidence/record-receipts.out, receipts.sha256 | — |
| S4 V rows, falsifiers, adversarial review | DONE | evidence/v-rows.txt, evidence/falsifiers.txt | — |
| S5 records, status, worker_done | DONE | this file | Codex QA |

## Design summary (for QA)

**M1 (`checks/m1_stale_tuple.py`, schedules OnEvents(cluster kinds) + OnInbox + Every(600 s)).** Receipts: per block and cluster the final event; if its transaction carries ≥ 2 events of the cluster, `isLiquidated(lastTuple)` at that block (IncorrectClusterState = poisoned; the earliest log of the same receipt whose tuple validates = repair tuple). State: each run re-validates, at the run's block, the last stored log of every key M1 tracks (open incident, unrepaired poisoning, unresolved revert, reorg key); the interval/reorg/force/params sweep adds the inventory's flagged clusters (last log stale or no candidate validates = `unknown`). Inbox: IncorrectClusterState reverts of `inbox_functions` from `our_senders` feed the cluster's incident. Severity: warn; page on re-poison (poisoning after a recorded repair) or poisoned and `isLiquidatable(SSV)(canonical tuple)`. Table `m1_detections` (poison / repair / revert); reorg rule: rows above the ancestor deleted (cursor rewound, re-detected from the new chain), inbox rows follow `inbox.canonical` (0 → dropped, any run), `cleared_block` above the ancestor reset; every open/persisted/reorg key is evaluated in the reorg run, a key that needs a missing inventory raises.

**M2 (`checks/m2_operator_capacity.py`, Every(600 s) + OnInbox).** Every run: operators of legacy clusters in the inventory, `getOperatorById` + `getOperatorByIdSSV` batched at the block, limit from `ctx.params`; keys `hard:<op>` (warn), `early:<op>` (info; warn if public), `velocity:<op>` (info; ValidatorAdded in `velocity_window_blocks`), `revert:<op>` (warn, ExceedValidatorLimitWithData of migrate/reactivate, any sender; settles at a later run once no legacy cluster on the operator is over the limit). Every open key is re-reported or resolved each run; unvalidated legacy clusters keep their operators' hard incident open (health). Table `m2_reverts` follows `inbox.canonical`.

Both raise a config error at construction without `views` (M1 also without `our_senders`); an RPC error, an empty answer or an unexpected revert raises; unresolved inbox reverts of watched functions are health.

## Completion handoff (≤ 250 words, also sent as the `worker_done` summary)

```text
Task / live dispatch: CP2d M1+M2 / task_aa8cd71dc363, ctx_f28f28892d56
Assignment outcome: implemented; acceptance pending Codex QA
Base -> resulting commit: 96d1c32 -> lane head named in plan.md CP2d status (implementation 7cf0bf1 + records)
Actual changed paths: reconcile/checks/{m1_cluster_abi,m1_stale_tuple,m2_operator_capacity}.py, checks/__init__.py (1 line), config.example.json (checks.m1/m2), tests/{fixtures_cluster,test_m1,test_m2,test_m1_receipts}.py, tests/fixtures/receipts/*.json (12), workers/cp2d/**; no deletions; frozen-file patches only via integration merges bd531b4, 500abe4
Verification: V2x.1 m1 38 / m2 29 passed; V2x.2 2; V2x.3 4; V2x.4 1 (extra); V2d.7 24 (12 receipts + 11 synthetic poison); suite 331 passed, 0 skips; ruff clean. NOT RUN: none
Falsifiers: F-M1a..d, F-M2a..d red then green; git status --porcelain empty after each (stale-pyc note in attempts)
Log anchor: workers/cp2d/attempts.md#attempt-1; evidence/
New finding: no real poisoned receipt on mainnet; no-validate path = real logs + the tx's own pre-call tuple emitted last (live revert). Frozen test conflict resolved centrally.
Pending human input: none
Next owner and exact action: Codex QA on the lane head
Preservation: all committed on mtabasco/ms-cp2d; venv untracked
```

Set the checkpoint status in plan.md to `IMPLEMENTED — AWAITING QA` with the head hash, send `worker_done --outcome succeeded|failed` exactly once from the dispatched terminal, then idle.
