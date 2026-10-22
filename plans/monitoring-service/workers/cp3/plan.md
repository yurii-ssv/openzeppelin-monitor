# Worker: CP3 — Integration, compose, fork e2e, soak start

Task contract: [plan.md §8 CP3](../../plan.md) · Attempt log: [attempts.md](attempts.md) · QA contract: [qa/QA-CONTRACT.md](../../qa/QA-CONTRACT.md)

- Actual starting worktree/branch/head/status: `/Users/marco/orca/workspaces/ssv-network-monitor/ms-cp3`, branch
  `mtabasco/ms-cp3`, head `0c388ec3bc594501c54077d58e72c58e697bd538` (= `plan/monitoring-service`), porcelain 0.
  This head is the **frozen base**.
- Live authority: Orca task `task_e6121a51fcdb`, dispatch `ctx_ad9a6e4a6775` (injected preamble).
- Requested/effective model and effort: Claude Opus high (packet); effective model id `claude-opus-5-5`.
- Allowed writes: `reconcile/**` (framework unfrozen for CP3), `docker-compose*.yaml`, `deploy/**` except
  `deploy/config/**` (untouched), `.gitignore`, `plans/monitoring-service/workers/cp3/**`, the CP3 status line of
  plan.md; `.local-dry/config` for rendering only. Not written: `config/`, `config.zip`, `.env*`,
  `Dockerfile.production`, `Dockerfile.development`, other lanes' records, `qa/**`, execution-log.md. Commits on
  `mtabasco/ms-cp3` only; no push; mainnet read-only; anvil forks only; alerts in record mode.
- Observable completion and owned verification IDs: A8, A9 (A10 started); V3.1–V3.10.
- Dependencies and sources: plan §1, §2 (D1, D10, D11, D13–D19), §5, §6, §7 V3, §8 CP3; `reconcile/checks/README.md`;
  `qa/DEFECT-CLASSES.md`.
- Commit capability checked: yes (`git config user.name` = Marco Tabasco; commits `7357eaa`, `ec2894e`, `a363f1b`, …).

## Name mapping (packet item 1)

The plan's `docker-compose.watch.yaml` / service `watch` / image `ssv-watch` are kept as the existing
`docker-compose.reconcile.yaml` / service `eb-reconcile` / image `ssv-eb-reconcile:local` (no churn). Every V3 command
below uses these names; `COMPOSE_FILE=docker-compose.yaml:docker-compose.reconcile.yaml`.

## What changed (design, for QA)

| Item | Change | Files |
|---|---|---|
| 1 Compose (D18) | Monitor data → named volume `monitor-data`; worker `/data` → `watch-data`; the worker mounts only `inbox/` of `monitor-data`, read-only (`volume.subpath`); `inbox-init` (worker image, uid 0, no network) creates `inbox/failed-tx.jsonl` with `touch` (never truncates) and the worker `depends_on` its success; worker `read_only: true`, `tmpfs /tmp`, healthcheck on the status file (healthy and written < 10 min), `stop_grace_period 60s`; secrets by env names only; `MAINNET_RPC_URL` optional; `MONITOR_CONFIG_DIR` (local: `./.local-dry/config`) and `WATCH_CONFIG` overrides; `docker-compose.local.yaml` uses the same named volume. Monitor build default `Dockerfile.production` (finding F-1). `.gitignore`: `data/**/*.jsonl`, `-wal`, `-shm`. `reconcile/.dockerignore`: + `scripts`, `*.status.json` | `docker-compose*.yaml`, `.gitignore`, `reconcile/.dockerignore` |
| 2 RPC (D16) | `rpc.Rpc(endpoints)`: ordered failover. Order = the endpoint that answered last, then the others in config order; the first is retried after `rpc_primary_retry_s` (300). Transport failure (unreachable, timeout, HTTP 429/5xx, non-JSON) or a non-revert JSON-RPC error → next endpoint; a revert → answer (raised as before, never retried elsewhere); `[]`/`null` → result; all fail → `RpcError(transport=True)` naming endpoints by host/env name (never a URL path); every endpoint that answered gave a JSON-RPC error → the highest-priority answer (so `get_logs` still halves on a range error). `retries` = rounds over all endpoints with backoff. Config `rpc_urls` (URLs or env names; default the three D16 URLs), `rpc_url_env` (`MAINNET_RPC_URL`) replaces the first when set. The scan re-reads a window whose `eth_getLogs` and end-hash read were answered by different endpoints (`Rpc.switches`). `max_log_range` 5000 (unchanged), halving on error (unchanged). Lazy bootstrap in `step` (an RPC outage at start is an RPC failure + health, not a crash loop); `rpc-recovered` health after an announced outage | `reconcile/rpc.py`, `reconcile/eb_reconcile.py`, `config.example.json` |
| 3 Helpers promoted | `rpc.Chain.batch(method, params, blocks)` (the former m12 `raw_batch`: block guard, counts, short answer = `RpcError`), `Chain.headers(blocks, full)`, `Chain.request(method, params)`; m12/m13 use them; m3_views (`first_replaced`, `block_times`) and m9_oracle_common (`BlockTimes.get`, `horizon`) drop their manual `ctx.rpc.rpc.batch` + counting. Same calls, same counts (lane suites green unchanged) | `rpc.py`, `m12_wallet_burn.py`, `m13_retired_keys.py`, `m3_views.py`, `m9_oracle_common.py` |
| 4 Frozen-test patch | `registry.build(settings, only=…)`; `harness.make_worker` builds the given instances, or `only`, or M5 + the checks named in `checks=`; `test_worker.make` builds M5 + named checks; `test_framework` registered-stub test uses `only=`. No test enumerates `REGISTRY` to disable checks (bd531b4, 500abe4 replaced) | `checks/registry.py`, `tests/harness.py`, `tests/test_worker.py`, `tests/test_framework.py` |
| 5 Silent backfill (D17) | Phase in `meta phase`: a new database starts `backfill` (`backfill.enabled`, default true); a database with a checkpoint but no phase (pre-CP3) is `live`. While backfilling the scan is bounded to `backfill.blocks_per_step` (50,000) per step and the run loop does not sleep between steps; checks do not run until the checkpoint is within `backfill.live_within_blocks` (12) of the scan target; then every check runs once with reasons `backfill`+`force` and its findings are applied with `notify=False` (incidents open/escalate/resolve, no message; health still sent); when every check has committed that run, phase `live` (`caught_up_block`, `first_live` = all ids); each check's next run is forced (`live`+`force`) and removed from `first_live` when it commits (durable across restarts). An incident tracked silently is sent by its first notifying finding (kind `open`, alerts.py), so a condition still true at the first live run alerts once; history-only incidents stay open with `alerts = 0`. Lag health and the digest are off while backfilling. `status.phase` (progress, remaining_blocks, caught_up_block), metrics `ssv_watch_backfill`, `ssv_watch_backfill_remaining_blocks` | `eb_reconcile.py`, `alerts.py` (kind `open` for a first send), `metrics.py` |
| 6 Fork e2e | `fork_e2e.py --scenario m5|m3|m9|m12 [--port]`, each with only its check, record mode, `backfill` off, fork at the RPC head | `reconcile/scripts/fork_e2e.py` |
| 7 Local run fixes | SQLite `temp_store=MEMORY` (F-2); `status`/`export`/`audit` open the database read-only (F-3); `export` command (incidents + outbox JSON); M11 weekly-comparison retry gate + probe (F-4, coordinator ruling A) | `db.py`, `eb_reconcile.py`, `checks/m11_updater_liveness.py` |
| 8 Runbook | `deploy/README.md` "Runbook": configure, start/stop, status/metrics, backfill phase, RPC failover, rotate secrets, record → live (TEST_* first, route test one-liner), Kubernetes (D18) | `deploy/README.md`, `reconcile/README.md`, `reconcile/checks/README.md` |

## Findings (integration)

| ID | Finding | Effect | Action |
|---|---|---|---|
| F-1 | `Dockerfile.development` (compose's old default) pins `wolfi-base@sha256:17ab07…` (glibc 2.42) but installs the current `jq`, which needs GLIBC 2.43/2.44: `jq` fails to load, so **every script trigger fails** in that image, the failed-tx inbox bridge (`inbox_append.sh`) included (Monitor log 20:23:39Z and 20:28:51Z: `jq: /usr/lib/libm.so.6: version 'GLIBC_2.43' not found`). Inbox lines are never written → M1/M2/M9/M11/M14 inbox rules see nothing (a silent input loss; `inbox-unavailable` does not fire because the file exists) | BLOCKING-class for any deployment built from `Dockerfile.development` | compose default switched to `Dockerfile.production` (its image: `jq-1.8.2` runs). `Dockerfile.development` itself is not in CP3's writes: owner Marco (pin `jq` or bump the base digest, or delete the file). Runbook says to build with the default |
| F-2 | The worker container's root filesystem is read-only; SQLite's first large `ORDER BY` needs a temp file → `sqlite3.OperationalError: disk I/O error` in `due()` → crash loop (20:18:28Z onward) | worker down | `PRAGMA temp_store=MEMORY` + `tmpfs /tmp` |
| F-3 | `status` opened the database read-write (migration, schema DDL) and waited for the write lock of a running check (runs last minutes on public RPCs) → `database is locked` | runbook command unusable during a run | `DB(readonly=True)` for `status`/`export`/`audit`; test `test_status_and_export_answer_while_a_run_holds_the_write_lock` (falsifier F5: 32 s wait, red) |
| F-4 | M11 weekly comparison DEFERRED on mainnet (proof API at `referenceBlock` 26246774 while the chain's latest root is 26249165) and retried every run: every M11 run (300 s) asked all 419 clusters, 230–264 s per run, ~120k API requests/day, worker starved | latency, external load (no false clean) | asked the coordinator; ruling A + probe-first implemented; `test_cp3_lagging_api_is_probed_once_per_retry_interval_and_compared_once_it_caught_up` (falsifiers F7, F8). QA re-checks M11 (CP3 change to a CP2b check) |
| F-5 | Public RPC rate limits (HTTP 429 from both Tenderly endpoints and mevblocker during the catch-up evaluation) | failover exercised for real: worker kept stepping | none (D16 design); runbook names it |
| N-1 | M3 computed zero after the N2 page: same incident, already `page` → recorded (title `… at computed zero balance …`, `evidence.zero_block`) without a new message; the 6 h re-alert carries the new title (fork run V3.7) | NOTE (D15: no lost page — the cluster paged at N2) | owner CP2a/CP4: make `zero_block` a `revision` if a separate zero message is wanted |
| N-2 | README §6.2 item 5, inbox entries above a lowered checkpoint: M14 holds them (CP2a); M1, M2 drop inbox-derived rows only at `canonical = 0` (kept meanwhile); **M9 `revert:<address>` and M11 `revert-rate`** derive from `canonical = 1` rows at or below the checkpoint, so in that reorg run they resolve ("evidence left the canonical chain") and reopen once the entry is re-confirmed | NOTE (transient warn, D15) | documented in checks/README §6.2 item 5; owner CP2b/CP4 |
| N-3 | Events and inbox entries up to the caught-up block are history (D17), including failed transactions the Monitor appended during the backfill window (minutes) | by design (D17) | runbook "Backfill phase" |
| N-4 | Rust `cargo test --locked --test integration -- filters::evm::filter`: the known CP0 mockall race (parallel 17/18, `test_handle_match_with_key_collision`, `tests/integration/mocks/services.rs:20:1`); serial control 18/18 | known red, not fixed | as CP0 |
| N-5 | CP1A F6 (Rust labels reverted txs `Success`) | DEFERRED, not touched | — |
| N-6 | While every RPC endpoint is down, `lag_check` cannot read the head and `lag_blocks` keeps its last value (V3.5 showed 0 during the cut); the outage is visible as `rpc_consecutive_failures`, the status file and the `rpc` health message | NOTE (pre-existing) | owner CP4: set lag unknown on failure if wanted |

## State table (DEFECT-CLASSES obligations) — new state of CP3

| State / input | (a) empty | (b) error | (c) partial | (d) reorg 1 / N | (e) restart | (f) same block | (g) time anchor |
|---|---|---|---|---|---|---|---|
| `meta phase` (backfill: `evaluated`; live: `caught_up_block`, `first_live`) | no phase + no checkpoint → backfill (if enabled); no phase + checkpoint → live: `test_database_that_was_already_alerting_is_not_put_back_into_backfill` | RPC error in a backfill step: checkpoint keeps the committed windows, phase unchanged, `rpc` health sent: `test_rpc_outage_while_backfilling_is_reported` | a check failing its silent run: phase stays backfill, `check-error` health, go-live after it succeeds: `test_check_failing_in_its_silent_evaluation_keeps_the_backfill_and_sends_health` | reorg while backfilling: rewind as usual, everything silent: `test_reorg_while_backfilling_stays_silent`; after go-live: normal reorg rules (the first live run also carries `reorg`) | mid-backfill: resumes from the checkpoint, silent: `test_restart_mid_backfill_stays_silent_and_resumes`; between go-live and the first live run: `test_restart_between_go_live_and_the_first_live_run_still_runs_it`; random restarts: sweep | n/a (findings are silenced per finding, order unchanged) | go-live anchored on the checkpoint of the silent runs (`caught_up_block`); events above it are live: sweep asserts both sides |
| silent incidents (`notify=0`, `alerts=0`) | resolve of a never-sent incident sends nothing: `test_history_only_condition_resolved_before_live_is_never_sent` | — | — | reorg re-find rule unchanged (a silent incident is resolved silently) | durable (incidents table) | — | first notifying live finding sends `open` once, then the normal cadence: `test_first_live_evaluation_of_a_still_true_condition_sends_once`; M5 real check: `test_m5_confirmed_in_history_and_still_open_pages_at_the_first_live_run` |
| `meta scan_target` | none → not caught up (checks wait) | stale target (failed step) → `caught_up` measured against the last scan's target | — | target re-read each step | durable | — | per step |
| `Rpc.active/switched_at/switches` (memory) | `[]`/`null` = result: `test_an_empty_answer_is_a_result_not_a_failure[2]` | transport → next; all → raise, labels only: `test_all_endpoints_down_raises_transport_error_without_urls`; 401 not retried; rate-limit JSON error → next: `test_rate_limit_error_moves_to_the_next_endpoint`; revert = answer: `test_revert_is_an_answer_and_is_not_retried_elsewhere` | batch item error → single calls with failover: `test_batch_item_error_falls_back_to_single_calls_with_failover`; short batch → `RpcError`: `test_chain_batch_counts_guards_and_rejects_short_answers`; all endpoints erroring → primary's error, `get_logs` still splits: `test_every_endpoint_erroring_returns_the_primarys_error_so_get_logs_still_splits` | endpoints on different heads/forks: a window read across a switch is re-read: `test_scan_window_straddling_an_endpoint_switch_is_reread`; logs still verified against the canonical hash (unchanged) | memory only: a restart starts at the first endpoint | — | primary retried after `rpc_primary_retry_s` (monotonic clock): `test_primary_down_fails_over_and_sticks_then_returns_to_primary` |
| `check:m11:state_full_tried` (M11, CP3) | none → the weekly comparison runs | API down/behind → DEFERRED + health, next try after `full_state_retry_s` | API behind the chain → probe of one cluster, stop: `test_cp3_lagging_api_is_probed_once_per_retry_interval_and_compared_once_it_caught_up` | a time gate only (no chain evidence): unchanged by reorgs; the comparison itself keeps its reorg rules | durable (`ctx.set_state`): a restart does not re-trigger 419 requests | — | wall clock (`ctx.clock`), cleared on a successful full comparison |
| read-only `DB` (status/export/audit) | missing file → sqlite error (no state created) | — | — | — | — | — | — |

Seeded sweeps: `test_backfill.py::test_seeded_backfill_sweep[0-23]` (history size, events, level toggles, scan bounds,
`live_within_blocks`, 3 random restarts during the backfill and an optional restart between go-live and the first live
run; asserts nothing but health before the first live run, the level page iff it holds then, no history event ever
sent, every live event sent once, stored logs = chain logs) and
`test_rpc_failover.py::test_seeded_failover_sweep_never_loses_or_invents_logs[0-11]` (per-request endpoint behaviour
down / rate-limited / ok across three endpoints, node range caps, window sizes; asserts after every step: checkpoint
monotonic, stored logs = chain logs up to it, full catch-up).

## Falsifiers (one-token mutations; each restored with `git checkout -- <file>`, porcelain of the file 0 after)

| ID | Mutation | Named test | Result |
|---|---|---|---|
| F1 | `rpc.py` failover on JSON errors disabled (`if False:`) | `test_rate_limit_error_moves_to_the_next_endpoint` | 1 failed |
| F2 | backfill not silent (`silent = False`) | `test_backfill_over_history_with_trigger_events_sends_nothing` | 1 failed |
| F3 | no forced first live run | `test_first_live_evaluation_of_a_still_true_condition_sends_once` | 1 failed |
| F4 | no endpoint-switch guard in `scan` | `test_scan_window_straddling_an_endpoint_switch_is_reread` | 1 failed |
| F5 | `status` opens the database read-write | `test_status_and_export_answer_while_a_run_holds_the_write_lock` | 1 failed (32 s lock wait) |
| F6 | all-endpoints-errored answer replaced by `[]` (false clean) | `test_rpc_failover.py` | 6 failed |
| F7 | M11 retry gate off | `test_cp3_lagging_api_is_probed_once_per_retry_interval_and_compared_once_it_caught_up` | 1 failed |
| F8 | M11 probe off | same | 1 failed |

## Verification (V3)

| V-ID | Command (exact) | Exit / result | Evidence |
|---|---|---|---|
| V3.1 | `for p in "cp1a f5e1fc4" "cp1b b07d2c6" "cp2a 2b434cf" "cp2b 48e997d" "cp2c dd9522b" "cp2d 24b1599" "cp2e 45cc774"; do …; git merge-base --is-ancestor <verified> HEAD; done` at base `0c388ec` | **PASS**: exit 0 for all 7 verified heads (and every lane branch head: CP1A f5e1fc4, CP1B d872350, CP2a 2b434cf, CP2b 48e997d, CP2c dd9522b, CP2d 24b1599, CP2e 45cc774 are ancestors); no lane re-merge needed | session log |
| V3.2 | `reconcile/.venv/bin/python -m pytest -q reconcile/tests` | **PASS**: base 2138 passed → head **2202 passed**, 0 skipped (+64: 26 failover, 37 backfill/CLI, 1 M11) | — |
| V3.2 | `reconcile/.venv/bin/ruff check reconcile` | **PASS** exit 0 | — |
| V3.2 (V0.8) | `cargo test --locked --test integration -- filters::evm::filter` | exit 101, 17/18: the known CP0 mockall race (`test_handle_match_with_key_collision`, `mocks/services.rs:20:1`); control `… -- --test-threads=1` exit 0, **18/18**; `test_failure_monitor_only_fetches_receipts_for_monitored_transactions` ok in both. KNOWN RED (N-4) | `v0.8-parallel.log`, `v0.8-serial.log` |
| V3.3 | `MONITOR_CONFIG_DIR=./.local-dry/config docker compose -f docker-compose.yaml -f docker-compose.reconcile.yaml config` (no secret in the shell) | **PASS** exit 0: worker `read_only: true`, `tmpfs /tmp`, `watch-data:/data` (named), `monitor-data` subpath `inbox` at `/inbox` `read_only: true`, healthcheck present, every secret an empty/null env pass-through (names only); `inbox-init` with `network_mode: none`; the Monitor config mount read-only, `monitor-data:/app/data` | `v3.3-config.yaml` |
| V3.4 build | `… build` | **PASS** exit 0 (Monitor 3 min 26 s dev / 2 min 16 s production; worker seconds) | `monitor-build.log`, `monitor-prod-build.log` |
| V3.4 up | `… up -d` with `alerts.mode: "record"`, `record_path /data/alerts-record.jsonl`, `MAINNET_RPC_URL=https://mainnet.gateway.tenderly.co` (the plan's `TEST_RPC_URL` is replaced by the D16 list), Monitor config from `scripts/local-dry-run.sh --prepare` (= `deploy/render.sh` into `.local-dry/config`; no `TEST_*` set → only the inbox bridge and a local log trigger remain; image `--check`: 32 monitors, success line) | **PASS** exit 0. Fresh start 20:16:11Z: backfill 24,920,727 → 26,250,488 (1.33M blocks, 111,021 logs) in ~2.5 min; crash loop on F-2 fixed (rebuild 20:29:48Z); silent evaluation 20:29:48–20:37:45Z (8 min, public-RPC 429s); live from `caught_up_block` 26,250,543; first live runs until 20:45Z (M11 F-4) | `status-*.json`, logs |
| V3.4 15 min | `docker compose … exec -T eb-reconcile python eb_reconcile.py status` 15 min after the restart of 20:50:29Z (M11 fix, production Monitor) → 21:05:43Z | **PASS**: checkpoint 26,250,623 → **26,250,719** (advanced), status file `healthy: true`, `error: null`, **lag 0** (< 50), `phase: live` (`first_live` empty; the backfill had completed at 20:37:45Z, before this window), container `(healthy)`, `/metrics`: `ssv_watch_backfill 0`, `outbox_failing 0`, `rpc_consecutive_failures 0`; open incidents `m7/info` 1 (the solvency surplus, sent as `open` at the first live run); record file: `proof-api-weekly` health (sent during the backfill), digest, `m7` info | `v3.4-status-15min.json`, `v3.4-statusfile-15min.json`, `v3.4-metrics-15min.txt` |
| V3.5 | `docker network disconnect ms-cp3_default ms-cp3-eb-reconcile-1` for 5 min (every endpoint unreachable, DNS included), then `docker network connect …` (`v35-cut.sh`) | **PASS**: cut 21:05:58Z; failed steps 21:06:33, 21:07:33, 21:08:34 …; **`health:rpc` outbox row at 21:08:34Z** (3rd failure, 2.6 min, inside the 50-block lag window), delivered to the record file: "3 consecutive RPC failures; last: transport error: all 3 RPC endpoints failed (MAINNET_RPC_URL: … URLError; gateway.tenderly.co: …; rpc.mevblocker.io: …)" (labels, no URL); status file `healthy: false` with that error; restored 21:10:58Z → 21:12:05Z `healthy: true`, checkpoint 26,250,721 → 26,250,749, lag 4, **`rpc-recovered` health row** ("after 5 consecutive failures"). N-6: the lag gauge stays at its last value while the RPC is down (pre-existing) | `v3.5-cut.log` |
| V3.6 | `FORK_RPC_URL=https://mainnet.gateway.tenderly.co reconcile/.venv/bin/python reconcile/scripts/fork_e2e.py --scenario m5 --port 8554` | **PASS** exit 0, fork block 26,250,672: clean → clean → latent (630,000) → confirmed (63 matched) → clean (unchanged gate-1 numbers); alerts: latent tracked silently, `escalate` page at confirmed, `resolved` info; layout guard ok. (Before CP3 the script built every registered check and M9 refused to start without an inbox: it now builds M5 only) | `v3.6-m5.log` |
| V3.7 | `… fork_e2e.py --scenario m3 --port 8551` (own ETH cluster registered on the fork, burn 10,481,800,000 wei/block, balance withdrawn to 4 blocks above the threshold; ~47k blocks mined) | **PASS** exit 0, fork block 26,250,544: no alert before → **info** at the onset (block 26,250,555) → **warn** at N = 50 → **page** at N2 = 7,200 → computed zero at block 26,297,755 recorded on the page incident (title, `zero_block`; no separate message: N-1) → impersonated `liquidate` → **resolved** (info on the page route). A live cluster was not used: its tuple needs the API inventory over the whole fork, the code path is the same | `v3.7-m3.log` |
| V3.8 | `… fork_e2e.py --scenario m9 --port 8553` (slots 1–4 impersonated; slot 4 votes another root before slots 1–3 commit, three rounds; then one vote, no quorum; `evm_increaseTime 1900`) | **PASS** exit 0, fork block 26,250,546: round 1 `dissent:4` **warn** (open), round 2 update (warn), round 3 **page** (escalate, streak 3); no `failed-round` inside 30 min; after 30 min of chain time **`failed-round` page** (round 26,250,568). `require_inbox` false on the fork (no Monitor) | `v3.8-m9.log` |
| V3.9 | `… fork_e2e.py --scenario m12 --port 8552` (impersonated oracle wallet sends all but ~0.1 ETH away; then a top-up) | **PASS** exit 0, fork block 26,250,545: 0.6047 ETH → no balance page; 0.1100 ETH → **page** `0x16a5…:balance`; top-up → resolved. Blockscout not queried on the fork (`blockscout_url` invalid: it cannot list fork transactions; the spend is UNKNOWN, the balance keys are evaluated regardless) | `v3.9-m12.log` |
| V3.10 | `deploy/README.md` "Runbook: running the service" | written: configure, start/stop/restart, state and backup, status/export/metrics (with alert rules), backfill phase, RPC failover, rotate secrets, record → live with `TEST_*` first and a route-test one-liner, Kubernetes (one replica, RWO block PVC not NFS/EFS, one pod with Monitor + worker sharing the volume, initContainer for the inbox, ConfigMap/Secret, read-only root fs, probes, snapshots). **QA review pending** | `deploy/README.md` |

Other evidence: the Monitor's inbox bridge in the production image wrote the §6 line for the V1A.3 transaction
(`--monitor-path config/monitors/ssv_failed_tx_inbox.json --block 26239099` with `INBOX_PATH=/tmp/inbox-probe.jsonl`,
the shared inbox untouched); the worker's `status.inbox` is `ok` on the read-only `/inbox` mount; secret scan
(V0.3 regex) 2 rows, both pre-existing test placeholders in `test_framework.py` (same at the base).

## Soak handoff (OG3, A10; V4.2 evidence)

Run from the repo root of the CP3 head (no `.env` needed: public RPCs, record mode, no channel secrets). The stack
of this worktree (compose project `ms-cp3`) is **already running** in record mode since 2026-10-22T20:15Z (backfill
done 20:37Z, live since): either keep it as the soak (start evidence = the `ms-cp3` T0 files below taken now) or
start clean with the commands below (`down -v` drops its state).

```bash
export COMPOSE_FILE=docker-compose.yaml:docker-compose.reconcile.yaml
export MAINNET_RPC_URL=https://mainnet.gateway.tenderly.co MAINNET_RPC_URL_2=https://gateway.tenderly.co/public/mainnet
scripts/local-dry-run.sh --prepare && export MONITOR_CONFIG_DIR=./.local-dry/config   # no TEST_* set: the Monitor sends nothing
jq '.alerts.record_path = "/data/alerts-record.jsonl"' reconcile/config.example.json > reconcile/config.json
docker compose down -v                    # clean start only (drops worker and Monitor state)
docker compose up -d --build              # Monitor from Dockerfile.production (F-1); ~25 min to "live"
until docker compose exec -T eb-reconcile python eb_reconcile.py status | jq -e '.phase.phase == "live" and .phase.first_live == []' >/dev/null; do sleep 60; done
mkdir -p soak
docker compose exec -T eb-reconcile python eb_reconcile.py status > soak/status-T0.json
curl -s http://127.0.0.1:9464/metrics > soak/metrics-T0.txt
(while sleep 21600; do curl -s http://127.0.0.1:9464/metrics > soak/metrics-$(date -u +%Y%m%dT%H%MZ).txt; done) &
# ... 48 h later:
docker compose exec -T eb-reconcile python eb_reconcile.py status > soak/status-T48.json
docker compose exec -T eb-reconcile cat /data/eb-reconcile.status.json > soak/statusfile-T48.json
docker compose exec -T eb-reconcile python eb_reconcile.py export > soak/export-T48.json      # incidents + outbox
docker compose exec -T eb-reconcile cat /data/alerts-record.jsonl > soak/alerts-record.jsonl
docker compose exec -T monitor cat /app/data/matches.jsonl > soak/monitor-matches.jsonl
docker compose exec -T monitor cat /app/data/inbox/failed-tx.jsonl > soak/inbox.jsonl
curl -s http://127.0.0.1:9464/metrics > soak/metrics-T48.txt
docker compose logs --no-color > soak/compose.log
```

V4.2 / A10 review of that evidence: (1) every `severity = 'page'` row of `export.outbox` explained by a real
on-chain condition (cluster, wallet, round, tx in its text); (2) `status-T48` `healthy: true` (status file),
`lag_blocks` < 50, `phase.phase` live, `reorg_pending` {}; (3) health rows (`kind = 'health'`) listed with cause
(`rpc` outages, `proof-api-*`, `m3-unknown`, …); (4) the 6-hourly metrics: `ssv_watch_lag_blocks` < 50,
`ssv_watch_outbox_failing` 0, `ssv_watch_rpc_consecutive_failures` < 3, `ssv_watch_check_errors_total` flat,
`ssv_watch_input_unavailable` 0; (5) `export.incidents` with `alerts = 0` = history-only incidents of the backfill
(never sent, D17). Stop: `docker compose stop` (or `docker compose -p ms-cp3 down` for this worktree's stack).

## Completion handoff (≤ 250 words, also sent as the `worker_done` summary)

```text
Task / live dispatch: task_e6121a51fcdb / ctx_ad9a6e4a6775
Assignment outcome: CP3 items 1-9 done; service runs locally (record mode).
Base -> head: 0c388ec -> mtabasco/ms-cp3 (code e71b1cb + record commit; head in worker_done).
Changed: reconcile/** (rpc failover, silent backfill, Chain.batch/headers/request, registry.build(only),
  read-only status/export, temp_store, M11 retry gate), docker-compose*.yaml, deploy/README.md, .gitignore,
  reconcile/.dockerignore, workers/cp3/**, plan.md CP3 line. Untracked: reconcile/config.json, .local-dry/.
Verification: V3.1 PASS (7/7 ancestors); V3.2 2202 passed, ruff 0, V0.8 17/18 known mockall (serial 18/18);
  V3.3 PASS; V3.4 PASS (+15 min: cp 26250623->26250719, healthy, lag 0, live); V3.5 PASS (rpc health at
  +2.6 min, rpc-recovered); V3.6-V3.9 PASS on anvil forks 26250672/26250544/26250546/26250545;
  V3.10 runbook written (QA review).
Falsifiers: F1-F8 red as expected, each file restored, porcelain 0.
Log anchor: workers/cp3/attempts.md#attempt-1; evidence in session scratchpad ev/.
New findings: F-1 Dockerfile.development jq cannot load -> every Monitor script trigger (inbox bridge) fails;
  compose now builds Dockerfile.production; fixing Dockerfile.development is Marco's. F-4 M11 change (ruling A).
Pending human input: none; soak (OG3) commands in plan.md "Soak handoff".
Next owner: Claude Opus QA on the CP3 head (re-check M11 too).
Preservation: local stack project ms-cp3 left running (stop: docker compose -p ms-cp3 down).
```
