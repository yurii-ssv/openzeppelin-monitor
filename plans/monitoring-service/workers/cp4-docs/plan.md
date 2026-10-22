# Worker: CP4 preparation — closure documents (docs only)

Task contract: coordinator brief (CP4 preparation) · Plan: [plan.md](../../plan.md) §8 "Recorded notes", §9 "Deferral table" · Spec: [monitoring-service-spec.md](../../../../docs/ssv-specs/monitoring-service-spec.md)

- Starting worktree/branch/head/status: `/Users/marco/orca/workspaces/ssv-network-monitor/ms-cp4-docs`, branch `mtabasco/ms-cp4-docs`, `git rev-parse HEAD` = `947ac44f0d3449df92581637b0e75fb986c4605c` (fresh child of `plan/monitoring-service`), porcelain clean.
- Live authority: Orca task `task_e7e6028f8b7a`, dispatch `ctx_5d0cae1e558b` (injected preamble); coordinator `term_89e2fa6f-482b-4551-995f-1f0e41c726a2`.
- Model and effort: Claude Opus 5.5, high (requested in the brief).
- Allowed writes: `plans/monitoring-service/plan.md` (§8 Recorded notes, §9 Deferral table only; no Status line or decision edited), `docs/ssv-specs/monitoring-service-spec.md` (amendment blocks only), `plans/monitoring-service/workers/cp4-docs/**`. Not touched: code, config, `qa/**`, `execution-log.md`, `state.md`, `config/`, `config.zip`, `.env*`. Commits on this branch only; no push.
- Diff check: `git diff --numstat` before the commit = plan.md 106 added / 0 deleted, spec 82 added / 0 deleted (additions only).

## Sources read

- `plans/monitoring-service/plan.md`: §1, §2 (D1–D19), §3, §4, §5, §6, §7, §8 (every Status line), §9, §10, §11.
- `plans/monitoring-service/execution-log.md`: all 914 lines (events 0001–0080 and every QA-numbered and lane-local event), searched in full for rulings, interpretations, amendments and owner decisions.
- `docs/ssv-specs/monitoring-service-spec.md`: §1–§4 and §6 in full (§5 headings only).
- Final passing QA reports, findings, NOTEs, carried items and "claims not confirmed": [qa/cp0-attempt-1.md](../../qa/cp0-attempt-1.md), [qa/cp1a-attempt-5.md](../../qa/cp1a-attempt-5.md), [qa/cp1b-attempt-4.md](../../qa/cp1b-attempt-4.md), [qa/cp2a-attempt-3.md](../../qa/cp2a-attempt-3.md), [qa/cp2b-attempt-5.md](../../qa/cp2b-attempt-5.md), [qa/cp2c-attempt-4.md](../../qa/cp2c-attempt-4.md), [qa/cp2d-attempt-4.md](../../qa/cp2d-attempt-4.md), [qa/cp2e-attempt-4.md](../../qa/cp2e-attempt-4.md), [qa/cp3-attempt-2.md](../../qa/cp3-attempt-2.md).
- Earlier reports, NOTE and deferred sections, read by heading and grep: cp1a attempts 1–4 (F5/F6/F7 deferrals, F1b, empty-RPC trust limit), cp2a attempts 1–2 (N1–N4), cp2b attempts 1–4 (N1–N5), cp2c attempt 3 (N1–N5) and the Codex attempt 2, cp2d attempts 1–3 (N1–N6, DEF-3), cp2e attempts 1–3 (N1–N4, subgraph deferral), cp3 attempt 1 (F2–F5, N-1/N-4/N-6).
- Worker records for note definitions: [workers/cp3/plan.md](../cp3/plan.md) (F-1..F-5, N-1..N-6, design table), [workers/cp2b/plan.md](../cp2b/plan.md) (repair 3 ruling A), [workers/cp2d/plan.md](../cp2d/plan.md) (API-ahead-of-checkpoint question).
- Repository probes at `947ac44` (read-only): `git check-ignore -v data/inbox/failed-tx.jsonl` → `.gitignore:63 data/**/*.jsonl` (F7 closed); `scripts/local-dry-run.sh:90` still has the unchecked `jq 'length'` (CP1A-QA5-N1 open); `reconcile/config.example.json:86` `fallback_tip_wei` still undated (CP2c-QA4-N3 open); `evidence.m1_handoff` only, no incident link (CP2b-N2 open).

## Deliverable 1 — plan.md §8 "Recorded notes"

75 rows. Coverage of the brief's list: CP0 notes 1–3; CP1A N1–N9 plus F5, F6, F7, F1b/D10, the event-0013 `--check` deviation and the duplicate trigger names; CP1B QA4 notes 1–3 plus the inbox pre-exist and GATE notes; CP2a N-A..N-F plus QA2-N4 and the M4 exactness note; CP2b QA5-F1, QA5-N2..N4, carried N1–N5, ruling A of event-0064; CP2c N1–N5 (attempt 3 merged) plus the revoked-VERIFIED episode; CP2d N1–N6 (N2 = coordinator ruling A, the API-ahead-of-checkpoint transient warn) plus DEF-3; CP2e N1, N2, N3 (closed), N4 residual, N5, N6, N7 plus the subgraph deferral; CP3 QA2 N1, N2, carried N-1, N-4 (merged with CP0 note 1 and event-0007), N-6, worker note N-2, F-1 (Dockerfile.development jq), the fresh-database go-live and V3.7 NOT RUN items; coordinator process notes (ledger numbering, guard/merge packets, DEFECT-CLASSES promotion, premature CP2e merge, worktree cleanup).

Answer to "F7 resolved?": yes, closed at CP3 (`.gitignore:63`, measured above).

## Deliverable 2 — plan.md §9 "Deferral table"

17 rows (DT-1..DT-17): F6; subgraph cross-check; archive RPC (M13 coverage, history re-checks); Dockerfile.development jq; Kubernetes manifests; live routes (record → TEST_* → live); duplicate trigger names in the live `config/`; soak evidence (T0 2026-10-22T22:51Z, project `ms-cp3`, T48 due 2026-10-24T22:51Z); accepted gap D10; D14 switches; fresh-database go-live; V3.7 tail steps; DEF-3 (M1 fork PoC; the M11 part is implemented); mockall known red; the "later repair" NOTE backlog; cleanup; DEF-1/4/5.

## Deliverable 3 — spec amendments (12 blocks, additions only)

| # | Spec location | Ruling (exact source) | Event |
|---|---|---|---|
| 1 | M1, after **Severity** | Coordinator ruling for CP2d QA2 F2: page under the existing incident, evidence "unverified intra-block re-poison (no trace)" | event-0050 |
| 2 | M4, after the page line | Interpretation A approved (liabilities = clusters + operator earnings + `getNetworkEarnings`, pool evidence only); LIVE +457,600,000 wei; QA confirmation | event-0031, event-0037, event-0043 |
| 3 | M5, after the trigger list | Owner decision: M5 severity = page on the first confirmed round | event-0016 |
| 4 | M6, after the trigger list | CP2e Q1 A / Q2 A: 5000 bps × live `liquidation_threshold_period_ssv`; page only vc > 0 and balance 0; vc = 0 zero-balance in the digest; 0 → > 0 pages | event-0042 |
| 5 | M8, after the trigger list | D3 floor semantics (follows the Safe's re-measured balance) | plan §2 D3; QA note event-0058, event-cp2c-qa-attempt-4 |
| 6 | M9, after the trigger list | D14 switch `checks.m9.dissent_enabled` | event-0068, event-0069, event-0074 |
| 7 | M11, after the trigger list | D14 switches `checks.m11.revert_rate_enabled`, `checks.m11.zero_round_proof_enabled` (+ measured exception CP2b-QA5-N4) | event-0068, event-0069, event-0074 |
| 8 | M12, after the runway paragraph | Coordinator decision: `projection_tip = "observed"` default, alarm on the lower projection, dated fallback tip | event-0040 |
| 9 | M13, after the table | D10 option A: Monitor direct txs + worker balance-delta watch; accepted gap | event-0030 (options event-0026) |
| 10 | M14, after the trigger | Coordinator ruling M1: one distinct deposit tx = one time; 24 h of chain time | event-0053 (window: event-0043, event-0051) |
| 11 | §4 Implementation notes | D11 Discord routing | event-0030 |
| 12 | §4 Implementation notes | D17 silent backfill | event-0076 |

Not found (no amendment made):
- D3 has no execution-log event of its own: it is a plan §2 row from plan creation (event-0001 writes the plan) and spec §6 already records it as DECIDED 2026-10-21. The M8 block cites D3 and the QA-note events instead.

Rulings found but not amended (they do not contradict the spec text or are not monitor-section rulings): event-0013 `--check` deviation (plan oracle V1A.1); event-0020 inbox GATE (plan §6); event-0056 CP2d ruling A (a NOTE, no behaviour change); event-0064 CP2b horizon ruling A (implementation limit; recorded as CP2b-E-0064); event-0076 M11 weekly-comparison retry gate (retry cadence of a DEFERRED weekly check). Not checked and not amended: spec §4 "confirm alerts on blocks ≥ 2 deep" versus the worker's confirmation setting, and M15 "warn if not announced"; I found no ruling for either.

## Completion handoff (≤ 250 words, also sent as the `worker_done` summary)

```text
Task / live dispatch: task_e7e6028f8b7a / ctx_5d0cae1e558b
Assignment outcome: completed (docs only).
Base -> resulting commit: 947ac44 -> the commit that adds this record (branch mtabasco/ms-cp4-docs).
Actual changed paths: plans/monitoring-service/plan.md (§8 Recorded notes, 75 rows; §9 Deferral table, 17 rows), docs/ssv-specs/monitoring-service-spec.md (12 amendment blocks), this record. Additions only; no deletion; nothing untracked.
Verification: git diff --numstat additions only (106/0, 82/0); 12 "> Amended 2026-10-23" blocks; every ruling cited with its execution-log event.
Not found: a dedicated execution-log event for D3 (cited plan §2 D3 + QA-note events 0058, cp2c-qa-attempt-4).
New findings for owners: CP2b-QA5-N4 (proof switch off downgrades instead of resolving, vs the event-0069 ruling) needs Marco; CP1A-QA5-N4 (discordapp.com not in the secret pattern) matters for V4.3; F7 is closed (.gitignore:63).
Next owner and exact action: coordinator merges this branch, then CP4 final QA (V4.1, V4.3, V4.4; V4.2 after T48 2026-10-24T22:51Z).
Preservation: commit on mtabasco/ms-cp4-docs only; not pushed.
```
