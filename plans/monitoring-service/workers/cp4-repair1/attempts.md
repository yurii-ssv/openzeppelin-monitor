# Attempts — CP4 repair 1 (D20)

Same entry shape as execution-log.md events, worker-side. Append only. Measured UTC.

<a id="attempt-1"></a>
## Attempt 1 — Monitor endpoints, 60 s polling, stall and skipped-range health, local proof on `ms-cp4r`

- Observed UTC: 2026-10-24T00:55Z – 02:15Z
- Task / Dispatch: `task_db92ac7953bd` / `ctx_4470cc0941c0`
- Frozen base: `e4c6502e14f2ee1aeeabb68e4faed178da92bda3`; `plan/monitoring-service` (`a9c1611`, state.md only) merged
  as `7961733`.
- Action and concrete outcome: [plan.md](plan.md) "What changed"; code commits `e70ef52`, `1853337`, `320c86b`,
  `1347cde`, `1ed5bea`, `2abdf14`; records commit = the head in `worker_done`.

| Verification ID | Exact command | Exit / result / counts / skips | Environment | Evidence path/hash |
|---|---|---|---|---|
| endpoints | `eth_blockNumber` POST to each URL; `python3 -I probe_logs.py https://rpc.mevblocker.io https://rpc-eth.blockmachine.io` (proxy logs, 5000 blocks) | both `0x190ae90`; 26,254,070–26,259,069: **160 logs on both** (0.6 s / 0.5 s); Tenderly ×2 answered `-32005 rate limit exceeded` | host, 2026-10-24T01:0xZ | scratchpad scripts |
| burst | `python3 -I probe_burst.py <url> <n>` (n concurrent `eth_getBlockByNumber(n,true)`, no retry) | mevblocker 25/25, 50/50 (20.9 MB), 100: 90 ok + 10×429; blockmachine 25/25, 50/50 (20.8 MB), 100: 56 ok + 44×429 | host | — |
| suite | `reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider reconcile/tests` | **2278 passed**, 0 failed, 0 skipped (base 2242 + 36 new watch tests) | Python 3.12, uv venv | — |
| ruff | `reconcile/.venv/bin/ruff check reconcile` | All checks passed | — | — |
| templates | `deploy/check-templates.sh` | exit 0 `checker: OK (32 monitors, 30 triggers, 1 network)` | jq | — |
| templates red on base | `deploy/check-templates.sh --config-dir <copy with the e4c6502 network>` | exit 1, 5 FAIL: two non-Tenderly missing, two adjacent-Tenderly pairs, cron `*/2`, `max_past_blocks` 3600 | — | — |
| abi | `deploy/check-abi.sh --config-dir deploy/config` | exit 0 (32 monitors, 3356 entries equal the reference) | — | — |
| m15 | `deploy/check-m15-coverage.sh` | exit 0 | — | — |
| selftest | `MONITOR_BIN=…/ms-cp1a/target/release/openzeppelin-monitor deploy/tests/selftest.sh` | exit 0, **303 passed, 0 failed, 0 skipped** (incl. 14 D20 cases); binary = the ms-cp1a release build of the unchanged Rust source (0ced62d) | — | — |
| compose | `docker compose -f docker-compose.yaml -f docker-compose.reconcile.yaml config` | exit 0 | Docker | — |
| render | `scripts/local-dry-run.sh --prepare` (= `deploy/render.sh` into `.local-dry/config`, no `TEST_*`: only the inbox bridge + local log trigger), then `--check` in `.local-dry` | exit 0; rotation `MAINNET_RPC_URL, https://rpc.mevblocker.io, https://rpc-eth.blockmachine.io` (`_2` unset), cron `0 * * * * *`, `max_past_blocks` 50; `Found 32 active monitor(s)`, validation successful | — | — |

**Item 7 — `ms-cp4r` (record mode, new project, ports: worker metrics 127.0.0.1:9474, Prometheus profile off; `ms-cp3`
and its volumes untouched).** Environment (`.local-dry/cp4r.env.sh`, no secret): `COMPOSE_PROJECT_NAME=ms-cp4r`,
`COMPOSE_FILE=docker-compose.yaml:docker-compose.reconcile.yaml`, `MONITOR_CONFIG_DIR=./.local-dry/config`,
`WATCH_CONFIG=./.local-dry/watch-config.json` (= `config.example.json` + `alerts.mode` record +
`record_path /data/alerts-record.jsonl`), `WATCH_METRICS_PORT=9474`, `LOG_DATA_DIR=./.local-dry/logs`,
`MAINNET_RPC_URL=https://mainnet.gateway.tenderly.co` (D16), `MAINNET_RPC_URL_2` unset (coordinator ruling C; set
during the first 37 min). Images: Monitor built from this tree (`docker compose build monitor`, exit 0; Rust
unchanged), worker rebuilt at each code commit; final worker image = `2abdf14`.

| Row | Command | Result |
|---|---|---|
| start | `docker compose up -d` at 01:15:17Z | exit 0; fresh volumes `ms-cp4r_monitor-data`, `ms-cp4r_watch-data` |
| 15 min, Monitor | per-minute sampler (`evidence/samples.log`); Monitor log | Monitor last block 26,259,149 (01:16:00, first cycle) → 26,259,224 (01:31:58): **+75 blocks in 15 min, lag 4–10** against the worker's head (threshold 30); 16 cycles processed, 0 failed by 01:32; Monitor container `healthy` |
| 15 min, worker | `docker compose exec -T eb-reconcile python eb_reconcile.py status` | fresh database (DT-11): backfill 24,920,727 → caught up at **26,259,174** (01:22:53Z), silent evaluation, phase **live** at 01:32:32Z (`caught_up_block` 26,259,174); first live messages: daily digest, M7 info (surplus unchanged); live and `healthy` at 01:32:58 (the 01:32:00 read still showed the silent-evaluation step: status file older than 10 min, container `unhealthy` → finding fixed by `320c86b`, observe + deliver between check runs) |
| outage 1 | `MONITOR_CONFIG_DIR=./.local-dry/outage/config docker compose up -d monitor` at 01:38:04Z (network rotation replaced by `https://monitor-outage-{a,b}.invalid`; `Failed to get EVM client`) | **`health:monitor-stall:…` at 01:42:55Z**, sent (record): "last block 26259254 is 32 blocks behind the head 26259286 … has not changed for 317 s … (stall 1)" (build `1347cde`, 300 s config; its "since 0 s" text fixed in `1ed5bea`) |
| restore 1 | config re-rendered without `_2`, `docker compose up -d` at 01:52:11Z (final worker logic) | Monitor: first catch-up attempt failed (R5), then `Processed 51 blocks (range 26259289-26259339) in 66906ms`, `Processed 6 blocks (26259340-26259345)` 1.5 s later (log: skipped 34 blocks = 26,259,255–26,259,288). **`health:monitor-skipped` 01:55:36Z**: "jumped from 26259254 to 26259345: blocks 26259255-26259294 (40) were NOT evaluated" (superset of the true 34; wording fixed in `2abdf14`), **`health:monitor-recovered` 01:55:36Z**: "last block 26259345, 4 blocks behind the head 26259349; stall 1 lasted 760 s" |
| outage 2 (final build `2abdf14`, `stall_after_s` 240) | same outage config at 02:00:22Z | **`health:monitor-stall:…` 02:03:33Z** (second stall in the same 6 h health window, own key): "its last block 26259364 has not changed for 263 s … (stall 2)", lag 25 at the alert (< `max_past_blocks` 50); status file `healthy: false`, "Monitor stall 2: …"; metrics `ssv_watch_monitor_stalled 1`, `ssv_watch_input_unavailable{input="monitor"} 1`, `ssv_watch_monitor_lag_blocks 25`; Monitor container `unhealthy` at 02:05:33 |
| restore 2 | `docker compose up -d monitor` at 02:05:34Z | `Processed 35 blocks (range 26259365-26259399)`; **`health:monitor-recovered` 02:06:12Z**: "last block 26259399, 4 blocks behind the head 26259403; stall 2 lasted 422 s"; no skip (35 ≤ 51) |
| final state | `status` (`evidence/status-final.json`, 02:06:44Z) | phase live, checkpoint 26,259,393, lag 0, undelivered 0, open incidents `m7/info` 1; `monitor`: last block 26,259,399, head 26,259,405, lag 6, stalled false, stalls 2, skipped 40 `[26259255, 26259294]`; status file `healthy: true`; Monitor last block file 26,259,399 |
| outbox | `export` → `evidence/outbox-health.json` | 5 health rows, all delivered to the record file: stall 1, skipped, recovered 1, stall 2, recovered 2 (+ digest and M7 info outside health); record file 7 lines |

`ms-cp4r` is left **running** (QA target, then soak 2). Stop: `source .local-dry/cp4r.env.sh && docker compose down`
(volumes kept). The per-minute sampler was stopped at the end.

- Falsifiers (each restored, `git status --porcelain` clean after): `monitor_watch.py` head-unknown branch disabled →
  `test_unknown_head_is_a_stall_never_clean` + sweep seeds 0–5 red; skip upper bound `new−M−2` →
  `test_jump_beyond_max_past_blocks…` + sweep red; missing-file reset disabled → `test_file_lost…` + sweep seeds red;
  `between_checks()` call removed → `test_a_stall_is_delivered_between_check_runs_of_a_long_step` red. New S5 gate red
  on the base template (above).
- Deviations and findings: R1 (per-block filter failure counts as processed, Rust), R5 (one fallback per failed
  request, Rust; coordinator ruling A+B+C), O1 (worker bursts rate-limit the Monitor's endpoint on the shared IP),
  long silent-evaluation step blinded the watch (fixed `320c86b`), multi-cycle resume over-reports the range (safe
  direction; text fixed `2abdf14`). Details in [plan.md](plan.md#residuals-and-findings-for-qa--owner).
- Resulting head: the records commit (in `worker_done`).
- Next action: fresh independent QA of CP4 repair 1 on that head, target stack `ms-cp4r`; after PASS, soak 2 (48 h).

<a id="handoff"></a>
## Completion handoff (≤ 250 words, also the `worker_done` summary)

```text
Task / live dispatch: task_db92ac7953bd / ctx_4470cc0941c0
Assignment outcome: D20 items 1-7 implemented and measured; QA pending.
Base -> resulting commit: e4c6502 -> mtabasco/ms-cp4-repair1 head (records commit; code 2abdf14).
Changed paths: deploy/config/networks/evm_ethereum_mainnet.json, deploy/{check-templates.sh,tests/selftest.sh,README.md},
  docker-compose{,.reconcile,.local}.yaml, reconcile/{monitor_watch.py,eb_reconcile.py,metrics.py,rpc.py,config.example.json,
  README.md,tests/test_monitor_watch.py,tests/test_rpc_failover.py}, workers/cp4-repair1/**, plan.md CP4 status line.
Verification: suite 2278 passed; ruff clean; check-templates/check-abi/check-m15-coverage 0; selftest 303/0/0; compose config 0.
Item 7 ms-cp4r: Monitor +75 blocks/15 min, lag 4-10; worker fresh backfill -> live 01:32Z; outage 1 -> stall, skipped
  26259255-26259294, recovered; outage 2 -> stall (lag 25), recovered; RUNNING.
Falsifiers: 4 red/green, tree clean.
New findings: R1 per-block filter failure silently counted (Rust); R5 one rotation fallback per request (Rust,
  ruling A+B+C); O1 worker bursts hit the Monitor's endpoint.
Next owner: fresh QA on the head, target ms-cp4r; then soak 2.
```
