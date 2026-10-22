# Attempts — CP3

Same entry shape as execution-log.md events, worker-side. Append only. Measured UTC (`date -u +%Y-%m-%dT%H:%MZ`).

<a id="attempt-1"></a>
## Attempt 1 — integration proof, RPC failover, silent backfill, compose, fork e2e, local run

- Observed UTC: 2026-10-22T19:55Z – 2026-10-22T21:20Z
- Task / Dispatch: `task_e6121a51fcdb` / `ctx_ad9a6e4a6775` (coordinator `term_89e2fa6f-…`, worker terminal `term_3422e453-…`)
- Frozen base: `0c388ec3bc594501c54077d58e72c58e697bd538` (`plan/monitoring-service`, all CP1/CP2 lanes merged)
- Action and concrete outcome: work items 1–9 of the CP3 packet, in commits `7357eaa` (RPC failover, silent
  backfill, promoted helpers, explicit test check selection), `ec2894e` (compose, read-only status/export, fork e2e
  scenarios, runbook) and the closing commit (records, docs, local-run fixes). Results per row: [plan.md](plan.md) §V.
- Evidence (local, not tracked): the scratchpad `ev/` directory of this session holds the logs named in plan.md §V
  (`v3.6-m5.log`, `v3.7-m3.log`, `v3.8-m9.log`, `v3.9-m12.log`, `v3.3-config.yaml`, `v0.8-*.log`, `falsifiers.log`,
  `status-*.json`); each row below quotes the decisive lines.

| Verification ID | Exact command | Exit / result / counts / skips | Environment | Evidence |
|---|---|---|---|---|
| V3.1 | `git merge-base --is-ancestor <verified head> HEAD` for the 7 lanes | 0 for all 7 (table in plan.md) | local git | plan.md §V3.1 |
| V3.2 | `reconcile/.venv/bin/python -m pytest -q reconcile/tests` | base 2138 passed; head 2202 passed, 0 skipped | py 3.12 venv | — |
| V3.2 | `reconcile/.venv/bin/ruff check reconcile` | exit 0 | ruff 0.17.1 | — |
| V3.2 | `cargo test --locked --test integration -- filters::evm::filter` | exit 101: 17 passed / 1 failed (`test_handle_match_with_key_collision`, `tests/integration/mocks/services.rs:20:1`, the CP0 mockall race); serial control `-- --test-threads=1`: exit 0, 18/18; target test `test_failure_monitor_only_fetches_receipts_for_monitored_transactions` ok in both | cargo 1.90 | `v0.8-parallel.log`, `v0.8-serial.log` |
| V3.3 | `docker compose -f docker-compose.yaml -f docker-compose.reconcile.yaml config` | exit 0; read_only, named volumes, inbox subpath read-only, healthcheck, env names only | Docker 29.9.1, Compose 5.6.0 | `v3.3-config.yaml` |
| V3.4 | `… build`, `… up -d`, `… exec -T eb-reconcile python eb_reconcile.py status` at +15 min | exit 0 / 0 / 0; checkpoint 26,250,623 → 26,250,719, healthy, lag 0, live | local Docker, public RPCs (D16) | `v3.4-*` |
| V3.5 | `docker network disconnect/connect ms-cp3_default ms-cp3-eb-reconcile-1` (5 min) | `health:rpc` row at +2.6 min; recovered (`rpc-recovered`, healthy, checkpoint advanced) | local Docker | `v3.5-cut.log` |
| V3.6–V3.9 | `FORK_RPC_URL=https://mainnet.gateway.tenderly.co reconcile/.venv/bin/python reconcile/scripts/fork_e2e.py --scenario <s> [--port N]` | m5 exit 0 (fork 26250672, re-run on the final script), m3 exit 0 (fork 26250544), m9 exit 0 (fork 26250546), m12 exit 0 (fork 26250545) | anvil 1.8.5 | plan.md §V |

- Falsifiers: eight one-token mutations (F1–F8), each reddening its named test, file restored by `git checkout --`, porcelain
  of the mutated file 0 afterwards (plan.md §Falsifiers).
- Deviations and findings: plan.md §Findings (F-1 Monitor dev image jq/glibc, F-2 SQLite temp files on a read-only
  root, F-3 status blocked by a running check, F-4 M11 weekly comparison retried every run against a lagging proof
  API (coordinator ruling A + probe, asked via Orca), F-5 public-RPC rate limits; notes N-1–N-6).
- Commits: `7357eaa`, `ec2894e`, `a363f1b` (M11 ruling, docs), `1e9d982` (`wait_s`), `e71b1cb` (compose
  Monitor Dockerfile default), then the record commit (head reported in `worker_done`).
- Next action: Claude Opus QA on the CP3 head (D13).
