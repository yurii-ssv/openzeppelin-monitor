FAIL

# CP4 repair 1 QA, attempt 1 (D20: Monitor endpoints, 60 s polling, Monitor stall and skipped-range health)

Independent QA of [plan.md](../plan.md) CP4 repair 1 under [QA-CONTRACT.md](QA-CONTRACT.md), decision §2 D20 (with D15, D16, D17, D18), diagnosis [execution-log.md](../execution-log.md) event 0084 and the coordinator ruling event 0085 (on `plan/monitoring-service`, `d1edda2`). Implementer record: [workers/cp4-repair1/plan.md](../workers/cp4-repair1/plan.md), [attempts.md](../workers/cp4-repair1/attempts.md).

- **Frozen head under test:** `5625e48a223dd94d27d74f3aa1e76b9d1df2046a` (branch `mtabasco/ms-cp4-repair1`, base `e4c6502`, code `2abdf14`). Worktree `ms-qa-cp4-r1`, branch `mtabasco/ms-qa-cp4-r1`; `HEAD` = the frozen head at start; initial porcelain empty.
- **Diff of record:** `git diff e4c6502..5625e48`: 22 files, +1,538/−40.
- **QA model:** Claude Opus 5.5 (`claude-opus-5-5`), effort high (D13). Task `task_11e70d96a87d`, dispatch `ctx_4de45b66e8a2`.
- **Measured:** 2026-10-24T02:15Z–02:50Z host UTC.
- **Policy:** D15. BLOCKING = missed or lost page, false clean, false page, unsafe action, data loss, fail-open gate, secret leak. Everything else is a NOTE with a one-line reason and an owner. Any BLOCKING finding means FAIL.
- **Scratch:** `$S` = `/private/tmp/claude-501/-Users-marco-orca-workspaces-ssv-network-monitor-ms-qa-cp4-r1/135d4e70-8259-48a8-9dc8-2fe67513e21f/scratchpad` (outside the repo, not committed). Own scenarios: `$S/qa/test_qa_cp4r1.py`. Watch-on suite plugin: `$S/qa/qa_watch_on.py`. Live evidence: `$S/live/`.

**Summary.**
- **One BLOCKING finding: F1 = residual R1, measured live.** The repaired Monitor (D16 env, the soak-2 target `ms-cp4r`) often marks a block as processed when its filter failed (`eth_getLogs` 429 on `rpc.mevblocker.io`, then the single rotation probe to Tenderly also gets a 429, R5). No rule evaluated that block. The last-block file still advances, so the worker's stall watch, the status file, the metrics and the Monitor healthcheck all stay green. Counts: 5 of 5 blocks at 02:20Z during this QA (steady state, no outage), and 50 of the 51 blocks of my outage control's catch-up at 02:42Z, which was followed by `monitor-recovered`. In the implementer's own logs: 26 of 106 blocks between 01:15Z and 01:37Z, and 35 of the 51 blocks of the post-outage catch-up at 01:55Z. That catch-up is the same cycle whose `monitor-skipped` message named 26,259,255–26,259,294, which implies the blocks after it were evaluated; most were not. D20 requires that "no block range the Monitor skips is silent". This is a silent unevaluated range, i.e. a false clean and a possible lost page for every Monitor rule. Classified **BLOCKING; the repair FAILs on it. It is not a recorded owner gap**: no D-row accepts it (unlike D10), it is frequent in the target environment, and it can be detected inside the repair's write scope (worker + compose) without Rust. Routes are in §1.
- Everything else re-measured green. Suite **2278 passed** (+ **2242 passed** with the watch forced on in every test, so `between_checks` really ran 39,998 times). Ruff clean. `check-templates`/`check-abi`/`check-m15-coverage` exit 0. Selftest exit 0, **303 passed, 0 failed, 0 skipped** (incl. the D20 cases and "rendered rotation without MAINNET_RPC_URL_2: MAINNET_RPC_URL,https://rpc.mevblocker.io,https://rpc-eth.blockmachine.io"). Composed compose config valid. Template rotation meets D20. No keyed URL in the diff. A missing `MAINNET_RPC_URL_2` does not stop the Monitor from loading. Cron 60 s.
- Watch: fail-closed (garbage, frozen, unknown head, worker restart mid-stall, not-bootstrapped restart, three stalls in one window): 32 own scenarios, all green (two of them are characterisations that assert the NOTE behaviour of N2 and N3). Timing: a Monitor that stops dead is announced 246–324 s after its last cycle at a Monitor gap of ≤ 27 blocks with 30–50 s steps, and ≤ 469 s / ≤ 39 blocks with steps up to 150 s (< `max_past_blocks` 50). Live: outage at 02:29:00Z, `monitor-stall` (stall 3) at 02:33:16Z, 314 s after the last successful cycle, **lag 28 (head − last), Monitor gap ≈ 26 < 50**; restore 02:41:42Z; `monitor-skipped` 26,259,508–26,259,526 (19) **exactly equal** to the Monitor's own `skipped 19 blocks`; `monitor-recovered` 02:42:58Z — while that same 51-block catch-up had **50 filter failures** (F1, reproducer 3).
- Skipped ranges: the Rust behaviour table matches the source on every row I checked. The "never too short" property holds in 20 own seeds (multi-cycle catch-up, lagging-head writes, worker gaps) except when the Monitor's file is lost while the worker is not observing (R2, broader than recorded: N2).
- NOTEs: N1 R5, N2 R2 broadened, N3 false `monitor-skipped` after a long worker downtime, N4 R4 (stall delivery waits for the current check run; worker RPC outage doubles as a Monitor stall), N5 R3, N6 O1, N7 S5 cron gate accepts any once-a-minute-or-slower cron, N8 record wording (status-file staleness during a long step not fixed by `320c86b`; `scripts/local-dry-run.sh` comment).

## 1. Findings

### F1 — BLOCKING (D20 "no skipped range silent"; false clean, possible lost page): a block whose filter fails is silently counted as processed, measured frequent on the repaired stack

- **Source (read-only, unchanged Rust).**
  - `src/services/filter/filters/evm/filter.rs:693-694` (`get_logs_for_blocks(...).await?`) and `:758` (`get_transaction_receipt(...).await?`) return the first error of a block.
  - `src/bootstrap/mod.rs:270-271` maps it to `result.ok()` = `None`, then `:238` to `processing_results = matches.unwrap_or_default()`: the block has no matches.
  - `src/services/blockwatcher/service.rs:747-750` then writes `last_processed_block` = the confirmed head regardless.
  - Rotation: `endpoint_manager.rs:117-146` probes exactly one fallback (the first queued URL ≠ active) and returns an error if that probe fails (R5). `transport.rs:74-148` builds the queue in weight order without the URL active at start.
- **Reproducer 1 (live, this QA, no outage).** `cd ms-cp4-repair1 && source .local-dry/cp4r.env.sh && docker compose logs --since 2026-10-24T02:20:00Z monitor | sed -E 's/\x1b\[[0-9;]*m//g' > $S/mon-0220.txt`.
  - The cycle `Processing blocks` at 02:20:00.33Z ends with `Processed 5 blocks (range 26259463-26259467) in 8146ms`.
  - Before it there are exactly 5 `ERROR filter_block:get_logs_for_blocks … HTTP error: status 429 … rpc.mevblocker.io … URL rotation failed: Failed to connect to new URL 'https://mainnet.gateway.tenderly.co'` (02:20:02.6, 03.9, 05.4, 06.8, 08.41Z; the last one 10 ms before `Processed`).
  - So all 5 blocks were unevaluated. The outbox has no health row after 02:06:12Z (`$S/live/outbox-pre.txt`). Metrics at 02:28:44Z: `ssv_watch_monitor_stalled 0`, lag 4. The Monitor container is `healthy`.
  - Expected under D20: a health message naming 26,259,463–26,259,467 (or the cycle range). Actual: silence.
- **Reproducer 2 (the implementer's own evidence, `ms-cp4-repair1/.local-dry/evidence/`).** I counted `URL rotation failed` filter errors per `Processed` cycle (awk in this report's §4 command list).
  - `monitor-log-0140.txt`: cycles 01:20Z (6 of 6 blocks), 01:22Z (5/5), 01:24Z (6/6), 01:25Z (4/4) and 01:34Z (5/5) failed every filter, i.e. **26 of the 106 blocks** processed 01:16–01:37Z.
  - `monitor-log-restore.txt`: `Processed 51 blocks (range 26259289-26259339)` after **35** filter failures.
  - In the same restore, the worker's `monitor-skipped` said only 26,259,255–26,259,294 were not evaluated.
- **Reproducer 3 (live, my outage control, §3.1).** The catch-up after restore: `Processed 51 blocks (range 26259527-26259577) in 55591ms` after **50** `filter_block … URL rotation failed` errors (`$S/live/monitor-restore.txt`). Seconds later the worker sent `monitor-recovered` ("The Monitor processes blocks again") and a `monitor-skipped` naming only the 19 truly skipped blocks. So 50 of 51 "recovered" blocks were not evaluated, silently. The worker log shows the cause of the 429s: at 02:20:00.579Z the worker itself was on `rpc.mevblocker.io`, got a 429 and switched to blockmachine; all through the outage it flapped mevblocker ↔ blockmachine on 429s (O1).
- **Impact.** Every Monitor rule loses those blocks silently: M8 events, M10, M11 info, M13 retired-key interaction (page), M15, transfers, DAO/Safe, oracle transactions, and the failed-tx inbox that feeds M1/M2. No SSV proxy log fell into the affected windows: eth_getLogs of the proxy over 26,259,165–26,259,467 returned 0 logs on blockmachine and pocket, and over 26,259,527–26,259,577 returned 0 on blockmachine. The control window 26,254,070–26,259,069 returned 160 logs, so the endpoints were answering. No proxy-event page was actually lost here (not checked: wallet/transfer rules). The class is nevertheless live and silent at a high rate in the soak-2 environment: **~116 unevaluated blocks** (26 + 35 in the implementer's logs, 5 + 50 in mine) in ~90 min of Monitor operation; 2 of 3 post-outage catch-ups lost most of their blocks. Soak 2 would read green throughout, which is the soak-1 failure mode (event 0084, false clean) at block granularity.
- **Why BLOCKING and not a recorded owner gap.**
  1. D15 lists a false clean and a lost page. Both are measured here, not hypothetical.
  2. D20 states the requirement directly ("no block range the Monitor skips is silent"), and this repair claims D20.
  3. No owner decision accepts the gap (contrast D10's "ACCEPTED GAP (Marco)").
  4. It is detectable in scope without Rust. The Monitor can log to a file (`LOG_MODE=file`, `/app/logs`, `docker-compose.yaml:28-31,63`), and its RPC metrics are initialised per network (`transport.rs` `init_rpc_metrics_for_network`). Either could feed a worker check that names the cycle's range as unevaluated, which is never too short.
  5. The rotation and rate-limit causes are partly in scope (O1/N6).

  The diff did not introduce R1: it exists on the base too. It is the remaining part of D20 item "no silent skipped range".
- **Routes (for the coordinator / Marco; QA does not repair).**
  - (a) In scope: a worker `monitor-unevaluated` health message from the Monitor's file log or metrics, naming the cycle range. Plus O1: the worker prefers `rpc-eth.blockmachine.io` before `rpc.mevblocker.io`, so the worker's bursts stop rate-limiting the Monitor's serving endpoint. Plus, for the D16 local env, a non-Tenderly `MAINNET_RPC_URL` for the Monitor so that R5's single probe is not always a 429.
  - (b) Owner-approved Rust exception to §1: record a block whose filter failed as missed, so that `recovery_config` retries it.
  - (c) An explicit owner decision accepting R1 as a gap with this measured rate (then a re-QA could pass with notes).
- **Owner:** Marco (decision), coordinator (repair 2 dispatch).

### N1 — NOTE (R5, Rust, coordinator ruling A+B+C): one fallback probe per failed request
Verified in source (above). Live: every `rotation failed` error probes Tenderly, never blockmachine. Reason for NOTE: the cycles it fails outright are not silent (the stall watch announces a persisting failure, and the skipped range is named). Its silent part is F1. Owner: Marco (runbook production `MAINNET_RPC_URL` non-Tenderly, `_2` unset; Rust change only by decision).

### N2 — NOTE (R2, broader than recorded): a lost Monitor file that is recreated while the worker is not observing makes the announced range too short by up to 50 blocks
- Reproducers:
  - `test_qa_cp4r1.py::test_q3_file_lost_and_recreated_between_two_reads_is_too_short`: 50 of 99 unevaluated blocks are unannounced.
  - The first run of `test_q3_every_unevaluated_block_is_inside_an_announced_range[4]` (before I excluded the overlap): the file was lost during a 300 s worker gap. Jump 1948 → 1973 (≤ 51), nothing announced, 14 blocks (1959–1972) silently unevaluated.
- The record says the window is "between two worker reads (≤ 30 s)". In fact it is any period without observation: a worker restart, or a single long check run (between-check observation happens only between runs). Under the D18 shared PVC, losing the volume also loses the worker DB, so nothing at all is announced.
- Reason for NOTE: it needs loss of the Monitor's data volume, which is an operator or storage incident, plus a coincident worker gap. Single faults are covered.
- Cheap fix: also treat a changed inode / `st_ctime` of the file as a reset.
- Owner: coordinator (next repair) / Marco.

### N3 — NOTE: after a long worker downtime with a healthy Monitor, the worker announces a false `monitor-skipped` range
- `test_q3_long_worker_downtime_with_a_healthy_monitor_announces_a_false_skip`: worker down 30 min, Monitor evaluated every block. The worker sends "blocks 1013-1111 (99) were NOT evaluated by its rules and will not be … upper bound".
- Reason for NOTE: a false health message (not a page), and the text does say "upper bound". It is over-reporting in the safe direction.
- Owner: coordinator (wording: lead with "may not have been evaluated" when the previous read is old).

### N4 — NOTE (R4)
- The stall is queued and delivered between check runs. A single long check run delays it (my model: ≤ 469 s / gap ≤ 39 with 150 s steps, still < 50).
- A worker RPC outage ≥ 240 s also opens a Monitor stall (`test_unknown_head_is_a_stall_never_clean`, `test_q1_restart_while_not_bootstrapped_still_opens_a_stall`), in addition to `rpc`.
- Reason for NOTE: delayed or duplicated health, never silent. Owner: coordinator.

### N5 — NOTE (R3)
Only the example config is checked against the template (`test_example_config_and_templates_agree`). An operator's `monitor.max_past_blocks` larger than the Monitor's would shorten announced ranges. Reason: a configuration hazard, not the default. Owner: Marco (runbook).

### N6 — NOTE (O1, shared IP)
Live: the 02:20Z mevblocker 429s (Cloudflare 1015) hit while Tenderly answers 429 to this host; the worker's failover lands on mevblocker. Reason for NOTE alone: rate only. As the main cause of F1's frequency it is part of route (a). Owner: coordinator / Marco.

### N7 — NOTE (gate): S5 accepts any 6-field cron with a fixed second
`second()` in `deploy/check-templates.sh` checks only that field 0 is numeric, so `0 0 * * * *` (hourly) passes although D20 says every 60 s. Reason for NOTE: the tracked template is `0 * * * * *` (gate green on the real value), and an hourly Monitor would be announced as a stall within 4 min (fail-closed). Owner: coordinator.

### N8 — NOTE (record wording)
- attempts.md "status file older than 10 min, container unhealthy → finding fixed by `320c86b`": `between_checks` (`eb_reconcile.py`) observes and delivers but does not write the status file, so the worker healthcheck still turns red during a > 10 min step. The 30 min K8s startup budget (`deploy/README.md` Probes) covers the first backfill.
- The `scripts/local-dry-run.sh` header comment is stale (the implementer noted this).
- Reason for NOTE: docs only. Owner: coordinator.

### Residual classification (requested)
| Residual | Class | One-line reason | Owner |
|---|---|---|---|
| R1 | **BLOCKING** (F1) | measured silent unevaluated blocks with all health green: false clean / possible lost page, against D20; detectable in scope; no owner acceptance | Marco + coordinator |
| R2 | NOTE (N2) | needs Monitor data-volume loss plus a worker gap; window broader than recorded | coordinator |
| R3 | NOTE (N5) | operator config hazard; the default is consistent | Marco |
| R4 | NOTE (N4) | delayed/duplicated health, never silent | coordinator |
| R5 | NOTE (N1) | failed cycles are announced; its silent share is F1 | Marco |
| O1 | NOTE (N6) | rate driver; fixing it is part of F1 route (a) | coordinator / Marco |

## 2. Claims I could NOT confirm

- "Four endpoints across two providers make it rarer" (R1, record): contradicted in this environment (F1). R5 means blockmachine is never reached once Tenderly was 429 at start.
- "announced skipped blocks = exactly the blocks the model never evaluated" (sweep): true for the max_past_blocks mechanism. It is not a statement about evaluated blocks, because the 51-block "evaluated" catch-up had 35 filter failures (F1).
- Endpoint and burst probes (`probe_logs.py`, `probe_burst.py`, 50/100 concurrent full blocks): not re-run (time; scripts in the implementer's scratchpad). I re-ran one proxy `eth_getLogs` window: 160 logs over 26,254,070–26,259,069 on blockmachine and pocket, matching the claimed 160.
- Request-rate table: an estimate; not re-measured.
- The implementer's 15 min V3.4 run and outages 1/2: not repeated; I re-measured the current state and ran one outage of my own (§3).
- Selftest "303 passed": confirmed (303/0/0, same binary).

## 3. Verification matrix

| Row | Command | Exit / result |
|---|---|---|
| preflight | `git rev-parse HEAD`; `git status --porcelain`; `df -h .`; `uv venv -p 3.12 reconcile/.venv && uv pip install -r reconcile/requirements-dev.txt` | `5625e48a…` = frozen head; porcelain empty; 286 GiB free; Python 3.12.15 |
| suite | `reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider reconcile/tests` | exit 0, **2278 passed**, 0 failed, 0 skipped (52 s) |
| suite, watch forced on | `PYTHONPATH=reconcile:reconcile/tests:$S/qa … pytest -p qa_watch_on reconcile/tests --ignore reconcile/tests/test_monitor_watch.py` | exit 0, **2242 passed**; plugin: 62,989 watch reads, 39,998 `between_checks` calls. Check evaluation, D17 go-live and delivery assertions are unchanged by the between-check observe + deliver |
| ruff | `reconcile/.venv/bin/ruff check reconcile` | exit 0, All checks passed |
| templates | `deploy/check-templates.sh` | exit 0 `checker: OK (32 monitors, 30 triggers, 1 network)` |
| abi / m15 | `deploy/check-abi.sh --config-dir deploy/config`; `deploy/check-m15-coverage.sh` | exit 0 / exit 0 |
| selftest | `MONITOR_BIN=…/ms-cp1a/target/release/openzeppelin-monitor deploy/tests/selftest.sh` | exit 0, **303 passed, 0 failed, 0 skipped** (incl. the D20 cases and "rendered rotation without MAINNET_RPC_URL_2: MAINNET_RPC_URL,https://rpc.mevblocker.io,https://rpc-eth.blockmachine.io") |
| V3.3 (Monitor + worker) | `docker compose -f docker-compose.yaml -f docker-compose.reconcile.yaml config` (+ `--format json`) | exit 0. Worker `read_only: true`; `watch-data` → `/data`; `monitor-data` subpath `inbox` → `/inbox` ro; `monitor-data` → `/monitor` ro (new); secrets by env name only (`MAINNET_RPC_URL: null`). Monitor healthcheck `python3 … st_mtime < 300` on `/app/data/evm_ethereum_mainnet_last_block.txt`, interval 60 s, retries 2, start 3 min |
| V3.4 (re-measured on `ms-cp4r`) | `docker compose exec -T eb-reconcile python eb_reconcile.py status`; Monitor file; status file; metrics | 02:18Z: phase live, checkpoint 26,259,449, lag 0, undelivered 0, open `m7/info` 1; `monitor`: last 26,259,457, head 26,259,461, lag 4, stalled false, stalls 2, skipped 40 `[26259255, 26259294]`; Monitor file 26,259,457. After my control (02:45:00Z): phase live, checkpoint 26,259,581, lag 1, undelivered 0, open `m7/info` 1; `monitor`: last 26,259,587, head 26,259,594, lag 7, stalled false, stalls 3, skipped 59, last `[26259508, 26259526]`; status file `healthy: true`; both containers `healthy`; record file 10 lines. **V3.4 met** (checkpoint advanced, healthy, lag < 50) |
| V3.5 Monitor outage control | §3.1 | stall announced 314 s after the last Monitor cycle at lag 28 (< 50), sent (record); recovered after restore; skipped range exact. **Met** for the stall/skip/recovery path; the catch-up's silent filter failures are F1 |
| V3.5 worker RPC cut | own scenarios `test_q1_*`, `test_unknown_head_is_a_stall_never_clean` (suite) | head unknown → stall at 240 s, never clean; recovery only after a measured lag. Live worker RPC cut not run: one live control by brief; CP3 measured the worker's `rpc` path |
| D20 item 1 (rotation) | template + `transport.rs`/`endpoint_manager.rs` read | weight order 100 env `MAINNET_RPC_URL` → 90 mevblocker (plain) → 80 env `_2` (optional) → 70 blockmachine (plain). After a successful rotation the queue is old minus new plus old active, so Tenderly → non-Tenderly next in both renders (`_2` unset: T→mev→bm→T; set: T→mev→T2→bm→T). **D20 met.** Its limit is R5 (N1) |
| D20 item 1 (no keyed URL) | URL scan of `git diff e4c6502..5625e48` | only the 4 public URLs, `monitor-outage-{a,b}.invalid` (doc) and the selftest negative `https://eth-mainnet.example/v1/k3y`. No `.env`, `config/`, sqlite or `.local-dry` path in the diff |
| D20 item 1 (missing `_2`) | `deploy/render.sh:73-75` read; `.local-dry/config` rendered without `_2` and loaded by the running Monitor | drops the env entry; literals cannot be unset; the Monitor processes blocks with it. **Met** |
| D20 item 2 (cron) | tracked template; rendered config | `0 * * * * *`; recovery `30 */5 * * * *`; `max_past_blocks` 50; `store_blocks` false. Gate weakness N7 |
| D20 item 3 (stall) | own scenarios Q1 (7), Q2 (2), Q5 (1) + live | all green; see §1 summary for timing |
| D20 item 4 (skipped) | Rust table vs source; Q3 (20 seeds + 2 characterisations) | table rows verified: `service.rs:468-520` start/skip/L=0/L≥C, `:559-565` missed only inside the fetched set, `:735-750` store_blocks + write C; `recovery.rs:43-51,116`, `service.rs:286` `try_lock`; `storage.rs:242-252` missing → `None` → 0, garbage → error. Never too short except N2. **Not met for filter failures: F1** |
| D20 item 5 (healthcheck) | compose config + K8s note | as V3.3; README: readiness only, no liveness or ≥ 30 min; worker mounts the PVC root ro at `/monitor` |
| D20 item 6 (tests) | suite + selftest | present and green; the R1 path has no test (F1) |
| V4.1 restricted (cross-module) | read of diff + scenarios | Config: `monitor` key in `TOP_LEVEL_KEYS`, unknown keys fail, `_comment` ignored, example ↔ template ↔ compose path agree. Health kinds `monitor-stall` (`realert`, key per stall), `monitor-recovered`, `monitor-skipped` (key per range) do not collide with `rpc`/`lag`/`inbox`/`params`/`check-error` (`health:<kind>:<hash>`). No masking: each kind has its own key; `degraded()` lists the stall next to the inbox and params; a failed step writes `healthy: false` with the RPC error. Routing: health → telegram (`alerts.py:36`). Silent backfill unaffected (suppression at enqueue). Metrics: `input_unavailable{monitor}`, 4 new gauges. K8s note consistent |

### 3.1 Monitor outage control on `ms-cp4r` (the soak-2 stack, restored to the D16 env)

- Mode: on the running soak-2 stack itself (not a separate project), restored to the D16 env. Commands from `ms-cp4-repair1` with `source .local-dry/cp4r.env.sh` (`MAINNET_RPC_URL=https://mainnet.gateway.tenderly.co`, `MAINNET_RPC_URL_2` unset, `MONITOR_CONFIG_DIR=./.local-dry/config`).
- `.local-dry/outage/config` differs from `.local-dry/config` only in the network `rpc_urls` (`https://monitor-outage-{a,b}.invalid`): `diff -r`.
- Sampler every 15 s (metrics 127.0.0.1:9474, `/monitor` file, container health): `$S/live/samples.log`. Outbox before/after: `$S/live/outbox-{pre,post}.txt`. Monitor log: `$S/live/monitor-restore.txt`.

| UTC | Event | Measured |
|---|---|---|
| 02:28:00–02:28:02 | last successful Monitor cycle | file 26,259,507 |
| 02:29:00 | `MONITOR_CONFIG_DIR=./.local-dry/outage/config docker compose up -d monitor` | exit 0 |
| 02:33:16 | `health:monitor-stall:e8db86b8be8e9a5c:83000` sent (record) | "its last block 26259507 has not changed for 292 s … (stall 3)" (own key: third stall in the same 6 h window). Metrics: `monitor_stalled 1`, lag **28**; Monitor gap C−L ≈ 26 < `max_past_blocks` 50. Status file `healthy: false`, "Monitor stall 3: …"; `input_unavailable{monitor} 1` from 02:33:31 |
| 02:34:16 / 02:36:02 | Monitor / worker containers `unhealthy` | as designed (reported only) |
| 02:32:15–02:33:16 | worker metrics unchanged for 61 s | one long step (worker RPC flapping mevblocker ↔ blockmachine, both 429 at times: O1) |
| 02:41:42 | `docker compose up -d monitor` (D16 env) | exit 0; lag at restore 70 |
| 02:42:00–02:42:55 | Monitor first cycle | `Last processed 26259507 … Start block: 26259527 (skipped 19 blocks)`; `Processed 51 blocks (range 26259527-26259577) in 55591ms` after **50** `filter_block … URL rotation failed` errors (F1) |
| 02:42:58 | `health:monitor-skipped` + `health:monitor-recovered` | "blocks 26259508-26259526 (19) were NOT evaluated" = Monitor log exactly; "stall 3 lasted 874 s" |
| 02:43:02 | next cycle | `Processed 5 blocks (26259578-26259582)`, 0 filter failures |
| 02:45:00 | both containers `healthy` | final state in §3 V3.4 |

- Restoration proof: `docker inspect ms-cp4r-monitor-1` env (secrets redacted) and image are identical before and after (`$S/live/monitor-inspect-{pre,post}.txt`). The mounts are the same three: `.local-dry/config` ro, `ms-cp4r_monitor-data` → `/app/data`, `.local-dry/logs`. The only difference is Docker Desktop's `/host_mnt` prefix on the config bind. The rendered network is again `MAINNET_RPC_URL, https://rpc.mevblocker.io, https://rpc-eth.blockmachine.io`, cron `0 * * * * *`, 50. The stack is left **running** for soak 2 (after a repair), worker image unchanged.
- Side note: `input_unavailable{monitor}` stays 1 until the step after the recovery (02:44:22). It is written at step end; cosmetic.

## 4. Falsifiers

- No mutation testing (owner scope note). The implementer's four falsifiers were not re-run in bulk.
- Own falsifiers, each chosen outside the implementer's model:
  1. **Watch forced on in the whole existing suite** (plugin above): green. The between-check delivery path is exercised under every go-live, reorg and delivery test.
  2. **Rust cycle with multi-cycle catch-up, lagging-endpoint lower writes and worker gaps** (Q3, 20 seeds): green after excluding the R2 overlap. Red with it (seed 4, N2) — a real red, recorded.
  3. **Timing with variable step durations** (Q2): green; numbers in the summary.
  4. **Garbage after an open stall** (6 junk values), **frozen file with a stale head and a huge lag threshold**, **unknown head then head back but frozen**, **restart mid-stall with RPC down / never bootstrapped**, **prior non-zero stall counter across restarts** (`(stall 3)`), **estimated head older than 15 min → unknown** (Q1, Q5): green.
  5. **Live filter-failure evidence** (F1): red against D20.
- Restoration: the only temporary state was the `ms-cp4r` Monitor config during the outage (§3.1). No file in the worktree was modified for a falsifier.
- F1 counting command (implementer evidence): `sed -E 's/\x1b\[[0-9;]*m//g' <log> | grep -E "Processed [0-9]+ blocks|ERROR filter_block.*rotation failed|Failed to process blocks" | awk '/rotation failed/{f++;next} /Processed/{print; print "  filter failures:", f+0; f=0}'`.

## 5. Skipped (owner scope note) and environment

- Skipped: mutation testing; a second suite run beyond the watch-on variant; the implementer's seeded sweep beyond the one normal suite run; the endpoint burst probes; a live worker RPC cut; a 15 min fresh-volume V3.4 restart (the stack is the soak-2 target).
- Network: only `rpc-eth.blockmachine.io` and `eth.api.pocket.network` (public, CLAUDE.md list) for two proxy `eth_getLogs` checks. The stack itself uses Tenderly / mevblocker / blockmachine (D16/D20).
- `ms-cp3` and its volumes, `config/`, `config.zip`, `.env*` untouched. No alert sent (record mode). No mainnet transaction. No push.

## 6. Evidence-plane writes

- This report.
- plan.md CP4 Status prefixed `REPAIR 1 QA FAIL (head 5625e48)`.
- One event appended to execution-log.md.
- Untracked: `reconcile/.venv` (local).

## 7. Repo proof

Before commit, `git status --porcelain`:

```text
 M plans/monitoring-service/execution-log.md
 M plans/monitoring-service/plan.md
 A plans/monitoring-service/qa/cp4-repair1-attempt-1.md
```

`git diff --name-only 5625e48` (the frozen head):

```text
plans/monitoring-service/execution-log.md
plans/monitoring-service/plan.md
plans/monitoring-service/qa/cp4-repair1-attempt-1.md
```

`git diff --name-only e4c6502` lists the repair's 22 files (the implementer's diff of record) plus these three. No implementation, config, template, compose, test or Rust file was touched by this QA. `reconcile/.venv` is ignored. The commit on `mtabasco/ms-qa-cp4-r1` contains only the three paths above.
