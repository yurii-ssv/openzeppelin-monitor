# Worker: CP2a — Liquidation family (M3, M4, M14)

Task contract: [plan.md §8 CP2a](../../plan.md) · Attempt log: [attempts.md](attempts.md) · QA contract: [qa/QA-CONTRACT.md](../../qa/QA-CONTRACT.md)

- Actual starting worktree/branch/head/status: `/Users/marco/orca/workspaces/ssv-network-monitor/ms-cp2a`, branch `mtabasco/ms-cp2a`, head `96d1c32fe3446ec7092e849e401f851a0d9b2cf3` (= frozen base `plan/monitoring-service @ 96d1c32`), `git status --porcelain | wc -l` = 0 (2026-10-21T13:40Z).
- Live authority: Orca task `task_2d232c728871`, dispatch `ctx_c6e862b982c3`, coordinator `term_89e2fa6f-482b-4551-995f-1f0e41c726a2`, worker terminal `term_8d7dddc9-5b3e-432e-8ca1-91caa786f7a4`.
- Requested/effective model and effort: requested Claude Opus high (task packet); effective in this session: `claude-opus-5-5` (the start receipt is held by the coordinator).
- Allowed writes: `reconcile/checks/m3_*.py`, `m4_*.py`, `m14_*.py`, `reconcile/tests/test_m3.py`, `test_m4.py`, `test_m14.py`, `reconcile/tests/fixtures_liquidation.py`, `reconcile/config.example.json` (keys `checks.m3/m4/m14` only), one line per check in the CP2a block of `reconcile/checks/__init__.py`, `plans/monitoring-service/workers/cp2a/**`, the CP2a status line in plan.md. Commits on the lane branch only; no push; mainnet read-only; record mode / TEST_* only.
- Observable completion and owned verification IDs: A3; V2x.1–V2x.6 for M3, M4, M14 (V2x.4 `rpc_budget` applies) and V2a.7 (M14 correlation of an inbox revert with a `ClusterDeposited` below one block of burn in the same or previous block).
- Dependencies and sources: plan §1, §2 (D5), §6; `reconcile/checks/README.md` (interface, §6.1/§6.2 reorg contract, inbox gate); spec M3, M4, M14, §4, §5; `ssv-network-triage/evidences/#89748/report-staking-insolvency-impact.ts`, `…/mainnet-self-operators/followup-20261020/followup.mjs`; contracts at the pin `f7ac487` (SSVViews, SSVClusters, SSVStaking, ProtocolLib, ClusterLib, SSVStorage).
- Commit capability checked: yes — `git config user.name` = Marco Tabasco, lane branch `mtabasco/ms-cp2a`.

| Step | Status | Latest evidence | Exact next action |
|---|---|---|---|
| S1 setup (venv, reading) | DONE | attempts §1 | — |
| S2 M3 check + tests | DONE | `21f1f91`, `756237b`, `2e47211` | — |
| S3 M4 check + tests + LIVE probe | DONE | `21f1f91`, `f74b8d4`; `live/m4-probe-26241393.json` | — |
| S4 M14 check + tests | DONE | `21f1f91` | — |
| S5 V rows, falsifiers, self-review | DONE | attempts §Verification, §Falsifiers | — |
| S6 commit, status, worker_done | DONE | plan.md CP2a status line | Codex QA |

## Design (as implemented)

See the module docstrings of `reconcile/checks/m3_liquidation_zone.py`, `m4_conservation.py`,
`m14_deposit_frontrun.py` (each states its triggers, inputs, UNKNOWN handling and its reorg rule per table).

## Completion handoff (≤ 250 words, also sent as the `worker_done` summary)

```text
Task / live dispatch: task_2d232c728871 / ctx_c6e862b982c3 (CP2a, M3 M4 M14).
Assignment outcome: implemented; acceptance pending Codex QA.
Base -> result: 96d1c32 -> lane mtabasco/ms-cp2a head (plan.md CP2a status line); coordinator merge 33bf02d (ruling v2.1).
Changed paths: reconcile/checks/{m3_views,m3_liquidation_zone,m4_conservation_deficit,m4_probe,m14_deposit_frontrun}.py,
  one line in checks/__init__.py, checks.m3/m4/m14 in config.example.json, tests/{fixtures_liquidation,test_m3,test_m4,
  test_m14}.py, workers/cp2a/**, CP2a status line. No CP1B file changed by this lane; no deletions.
Verification: V2x.1 M3 35 / M4 25 / M14 29 passed; V2x.2 10; V2x.3 4; V2x.4 3; V2a.7 9; V2x.6 329 passed, ruff clean.
  LIVE (publicnode): M4 A surplus +457,600,000 wei vs B -208.6 ETH at 26,241,393; M3 views 530 clusters, 0
  liquidatable, 0 zero at 26,241,455; validators 58,762 = 58,762. NOT RUN: none.
Falsifiers: 15, all red (F9 after a test fix), restored, porcelain 0.
Findings: M4 spec term "staking ETH owed" = getNetworkEarnings (ruled A; spec amendment at closure).
  Framework test gap resolved centrally (500abe4). Permission guard blocked my edits of CP1B tests and the merge.
Pending human input: none.
Next owner: Codex QA on the lane head.
Preservation: everything committed; scratch only in the session scratchpad.
```
