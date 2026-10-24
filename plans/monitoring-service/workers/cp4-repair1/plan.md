# Worker: CP4 repair 1 — Monitor stalled for the whole soak 1 (D20)

Task contract: [plan.md §2 D20, §8 CP4](../../plan.md) · Diagnosis: [execution-log.md event 0084](../../execution-log.md) · Attempt log: [attempts.md](attempts.md) · QA contract: [qa/QA-CONTRACT.md](../../qa/QA-CONTRACT.md)

- Actual starting worktree/branch/head/status: `/Users/marco/orca/workspaces/ssv-network-monitor/ms-cp4-repair1`,
  branch `mtabasco/ms-cp4-repair1`, head `e4c6502e14f2ee1aeeabb68e4faed178da92bda3` (= `plan/monitoring-service`),
  porcelain 0. This head is the **frozen base**.
- Live authority: Orca task `task_db92ac7953bd`, dispatch `ctx_4470cc0941c0` (injected preamble).
- Requested/effective model and effort: Claude Opus high (packet); effective model id `claude-opus-5-5` (self-report;
  the start receipt is not visible in this terminal).
- Allowed writes (packet): `deploy/config/networks/**`, `reconcile/**`, `docker-compose*.yaml`, `deploy/**`,
  `.gitignore`, this directory, the CP4 status line of plan.md. Not written: `deploy/config/monitors/**` and other
  templates, `config/`, `config.zip`, `.env*`, `Dockerfile.production`, Rust sources, `qa/**`, execution-log.md,
  state.md, other workers' records, `scripts/` (one stale comment noted below). Commits on `mtabasco/ms-cp4-repair1`
  only; no push; mainnet read-only; record mode only; network only to the D16/D20 public RPCs.
- Owned verification: packet items 1–7; V3.3–V3.5 for the Monitor; V4.2 (soak 2) is the next owner's.
- Commit capability checked: yes (`git config user.name` = Marco Tabasco; code commits `e70ef52`, `1853337`, `320c86b`,
  `1347cde`, `1ed5bea`, `2abdf14`; plan-branch merge `7961733`).

## What changed

| Item | Change | Files |
|---|---|---|
| 1 Monitor endpoints | Network template rotation (weight order, the Monitor rotates round-robin in that order): `MAINNET_RPC_URL` (env, 100, required), `https://rpc.mevblocker.io` (literal, 90), `MAINNET_RPC_URL_2` (env, 80, optional, dropped by `render.sh` when unset), `https://rpc-eth.blockmachine.io` (literal, 70): Tenderly and non-Tenderly alternate, also across the wrap. The two public URLs are **literal** (`"type": "plain"`): the Monitor resolves every `environment` URL at load and refuses to start when one is unset (`src/models/config/network_config.rs` `resolve_secrets`), a literal cannot be unset, and they carry no key. `check-templates.sh` S2 allow-lists exactly four keyless public URLs (`PUBLIC_RPC_URLS`; any other plain value, a keyed URL included, stays red); S5 adds: weights unique, ≥ 2 non-Tenderly literals, no two possibly-Tenderly entries (env refs or Tenderly hosts) adjacent in the rotation cycle. Worker default `rpc_urls` + `config.example.json` + READMEs end with `https://rpc-eth.blockmachine.io`. Both new endpoints re-verified 2026-10-24T01:0xZ: `eth_blockNumber` 0x190ae90 on both; `eth_getLogs` of the proxy over 5000 blocks 26,254,070–26,259,069: 160 logs on both (0.6 s / 0.5 s) | `deploy/config/networks/evm_ethereum_mainnet.json`, `deploy/check-templates.sh`, `deploy/tests/selftest.sh`, `reconcile/rpc.py`, `reconcile/config.example.json`, `reconcile/README.md`, `deploy/README.md` |
| 2 Polling | Monitor `cron_schedule` `0 * * * * *` (every 60 s, second 0). `recovery_config.cron_schedule` `30 */5 * * * *` (was `0 */5 * * * *`: the same second as the main job, whose lock makes the recovery run skip, `try_lock`). S5 requires a 6-field cron with one fixed second and a recovery job on another second. Request rates below | template, `check-templates.sh` |
| 3 Stall health | `reconcile/monitor_watch.py`: every worker step (also a failed one, also before bootstrap) reads `/monitor/evm_ethereum_mainnet_last_block.txt` (compose mounts the whole `monitor-data` volume read-only at `/monitor`; the `/inbox` subpath mount is unchanged). Not positively keeping up = lag > `max_lag_blocks` (30) against the head the worker reads, file missing / unreadable / not a number, head unknown, or the value unchanged for `stall_after_s`. After `stall_after_s` (**240 s**) of it (a frozen file counts from its last change): `monitor-stall` health (page route, keyed `monitor-stall:<n>`, re-sent per health window while open); `monitor-recovered` (`monitor-recovered:<n>`) at the first step that measures lag ≤ 30 with a changing value. Durable state in `meta` `monitor:watch` (onset, stall number, last value, observation time). `status.monitor`, `healthy: false` + error while a stall is open, metrics `ssv_watch_monitor_last_block`, `ssv_watch_monitor_lag_blocks` (−1 unknown), `ssv_watch_monitor_stalled`, `ssv_watch_monitor_skipped_blocks_total`, `ssv_watch_input_unavailable{input="monitor"}`. Config section `monitor` (unknown keys fail; without `last_block_path` the watch is off and `status.monitor.enabled` false, like `inbox.path`). `lag_check` now returns the head it reads (same one `eth_blockNumber` per step). **Between two check runs** of a step the watch observes again (head extrapolated from the last read at one block per `block_time_s` 12; older than 15 min = unknown) and the outbox is delivered: on ms-cp4r the silent evaluation after the fresh backfill held one step for > 10 min, during which the worker neither observed nor delivered. Timing (coordinator ruling): a Monitor that stops is announced ~4–5 min after its last successful cycle, at a lag of ~25–35 blocks (measured: 32), before `max_past_blocks` (50); the sweep asserts lag ≤ 50 at every stall onset | `reconcile/monitor_watch.py`, `reconcile/eb_reconcile.py`, `reconcile/metrics.py`, `docker-compose.reconcile.yaml`, `config.example.json` |
| 4 Skipped ranges | Behaviour table below. `max_past_blocks` 3600 → **50**; `store_blocks` true → **false**. The worker computes the range the Monitor will not evaluate from consecutive values: jump `prev → new` with `new − prev ≥ max_past_blocks + 2` → `[prev+1, new−max_past_blocks−1]`; after an observed missing file (a Monitor without it starts at the head) → `[prev+1, new−1]`. One `monitor-skipped` health message per range (`monitor-skipped:<lo>:<hi>`), naming it. The range is **never too short** (every true skip lies in `[prev+1, new−M−1]`); it is exact for a resume in one cycle and longer when the Monitor resumed in several quick cycles between two reads (measured on ms-cp4r: the 51-block catch-up took 67 s, the queued tick ran 1.5 s later; announced 26,259,255–26,259,294, Monitor log: skipped 26,259,255–26,259,288) — the message says so, and says "upper bound" when the worker's previous read is older than 1.5 cycles; S5 caps `max_past_blocks` at 100 | `monitor_watch.py`, template, `check-templates.sh` |
| 5 Monitor healthcheck | `docker-compose.yaml` (and `.local.yaml`): `python3 -c` (the production image has python3.12) — healthy when the last-block file was rewritten within 300 s; interval 60 s, retries 2, start 3 min. Reports only (compose does not restart on unhealthy). Runbook K8s: Monitor readiness = this check, no liveness on it (or ≥ 30 min), the stall is the worker's `monitor-stall` alert; the worker also mounts the PVC root read-only at `/monitor` | `docker-compose.yaml`, `docker-compose.local.yaml`, `deploy/README.md` |
| 6 Tests | `reconcile/tests/test_monitor_watch.py` (36): healthy silent + status; lag stall → recovery; short lag silent; missing; unreadable (directory); unparseable + one transient empty read silent; not advancing within the lag threshold; unknown head = stall, recovery only after a measured lag; second stall in the same window announced; long stall re-sent per window, one stall id; restart mid-stall (no repeat, recovery after); restart inside the grace keeps the onset; skipped range exact; jump ≤ M+1 silent; lost file → `[prev+1,new−1]`; unobserved gap → upper bound; resume in two quick cycles → superset; stall delivered between the check runs of a long step (red with `between_checks` disabled); watch off without the section; metrics; settings validation; example config ↔ template ↔ compose agreement; default `rpc_urls`; **seeded sweep** (12 seeds × 240 simulated minutes: a model of the Rust cycle with random Monitor RPC outages of 3–40 min, lost files, worker RPC outages, worker restarts) asserting no Monitor outage ≥ 330 s without an open stall, lag ≤ `max_past_blocks` at every stall onset, no stall kept after a proven recovery, announced skipped blocks = exactly the blocks the model never evaluated, recoveries = stalls. `deploy/tests/selftest.sh` D20 section: tracked template has ≥ 2 non-Tenderly literals and a once-a-minute cron; red for a 2 s cron, a 5-field cron, one public URL, Tenderly-only literals, adjacent Tenderly entries, a keyed plain URL, a plain primary, equal weights, `max_past_blocks` 3600, recovery on the main second; render without `MAINNET_RPC_URL_2` keeps both literals and passes `--check`. `test_rpc_failover.py`: the default list now ends with blockmachine | tests |
| 7 Local proof | compose project `ms-cp4r` (below) | — |

Not changed: Rust sources; monitor/trigger templates; `scripts/local-dry-run.sh` (its header comment "Without TEST_RPC_URL the rendered network reads MAINNET_RPC_URL (and MAINNET_RPC_URL_2)" is now incomplete: it also reads the two literals; not writable here, NOTE for its owner).

## Monitor behaviour after a long stall (Rust source, unchanged; packet item 4)

Sources: `src/services/blockwatcher/service.rs` (`start_main_watcher`, `process_new_blocks`), `recovery.rs`
(`process_missed_blocks`), `storage.rs`, `tracker.rs`, `src/services/blockchain/clients/evm/client.rs`
(`get_blocks`), `src/services/filter/filters/evm/filter.rs` (`filter_block`), `src/bootstrap/mod.rs`
(`process_block`), `src/services/blockchain/transports/http/{transport.rs,endpoint_manager.rs}`, `src/utils/http.rs`.
L = `<slug>_last_block.txt` (missing → 0), H = `eth_blockNumber`, C = H − `confirmation_blocks` (2), M = `max_past_blocks`.

| Mechanism | What the Rust code does | After a stall of S blocks | Retried? | Lost / visible |
|---|---|---|---|---|
| Main cycle (`cron_schedule`) | Holds the run lock (waits for it). Reads L and H; `start = max(L+1, C−M)`; L = 0 → fetches only C; L ≥ C → fetches nothing; else `get_blocks(start..=C)`: **one `eth_getBlockByNumber(n, true)` per block, all concurrently (`join_all`)**, the whole call fails if any request fails. Per fetched block: `eth_getLogs` of that block + receipts of transactions touching a monitored address (per receipt-needing monitor), 32 blocks at a time; triggers in block order; then writes L = C | S ≤ M: the next successful cycle reads `[L+1, C]` (S+1 blocks at once). S > M: it reads only `[C−M, C]` | a failed cycle (head or any block) changes nothing and the next tick retries the whole range | soak 1: M = 3600 → bursts of up to 3601 concurrent full-block requests failed (429, JSON parse, network) on every tick, so L never moved |
| `max_past_blocks` | lower bound of the fetch | blocks `[L+1, C−M−1]` are **skipped**: not fetched, not recorded as missed, never recovered; only a log line `(skipped N blocks)` | no | lost; **now visible**: `monitor-skipped` names `[L+1, C−M−1]` |
| Lost last-block file | L = 0: the first cycle processes only C | `[old L+1, C−1]` skipped | no | lost; visible (`monitor-skipped`, reset rule, when the worker saw the file missing) |
| `store_blocks` | true: after each successful cycle deletes `<slug>_blocks_*.json` and writes every fetched block (full transactions) to a new file; read by nothing. Also enables saving missed blocks (as does `recovery_config.enabled`) | a catch-up of 51 blocks writes ~23 MB per cycle | — | nothing; set **false** (diagnostic file only; recovery still saves missed blocks because recovery is enabled) |
| Missed blocks | dense gap detection inside the fetched set (`detect_missing_blocks`); saved to `<slug>_missed_blocks.json` when `store_blocks` or recovery is on | EVM `get_blocks` is all-or-nothing, so a successful fetch has no gap: the list stays empty in practice | — | — |
| `recovery_config` | own cron, `try_lock` (skips while the main job runs); prunes entries older than `max_block_age` (7200 blocks); takes ≤ `max_blocks_per_run` (100) `Pending` entries with `retry_count < max_retries` (3), oldest first; fetches each alone and processes it; failure → retry count + 1, `Failed` at 3 (given up, logged), `retry_delay_ms` (1000) between | **never sees the skipped range** (not recorded as missed) | only recorded gaps | does not reduce the loss; cron moved to second 30 so it is not skipped by the lock |
| Per-block filter failure | `filter_block` error (the block's `eth_getLogs` or a receipt fails after retries and rotation) → `process_block` returns `result.ok()` = None → the block counts as processed with **no matches**, L still advances | — | no | **lost silently; not visible from the last-block file** (residual R1 below) |
| Head from a lagging endpoint | L ≥ C: nothing fetched, L := C (lower) | the blocks between are evaluated again next cycle | — | duplicates possible (Monitor alerts twice), no loss |
| URL rotation | URLs sorted by weight; HTTP 429/5xx/408/410/400 → middleware retries 3× (250 ms → 10 s backoff), then rotation to the next URL in the queue after a `net_version` probe; a failed probe ends the request with an error | with both URLs on Tenderly (soak 1) the rotation stayed inside one rate limit | per request | now Tenderly → mevblocker → Tenderly 2 → blockmachine |

**Why `max_past_blocks` 50.** Once the lag exceeds M every cycle fetches exactly M+1 full blocks at once, so M must be a
burst the endpoints serve reliably, else the Monitor never resumes. Measured 2026-10-24T01:0xZ, concurrent
`eth_getBlockByNumber(n, true)`, no retries: mevblocker 25 → 25 ok, 50 → 50 ok (20.9 MB), 100 → 90 ok + 10 HTTP 429;
blockmachine 25 → 25 ok, 50 → 50 ok (20.8 MB), 100 → 56 ok + 44 HTTP 429 (Tenderly answered `rate limit exceeded`
to a single call at that moment). 50 blocks = ~10 min: an outage up to ~10 min loses nothing; beyond, the loss is
named by `monitor-skipped` (and the `monitor-stall` page has fired at ~4–5 min, lag ~25–35). `recovery_config` values kept
(enabled, 100 per run, 7200 blocks, 3 retries, 1 s): harmless, and it would recover real gaps if a client ever
returned one.

**Request rates (estimate from code and config; HTTP requests per minute, healthy, ~5 blocks/min).**

| Process | Before (cron 2 s) | After (cron 60 s) |
|---|---|---|
| Monitor main | 30 × `eth_blockNumber` + 5 × `eth_getBlockByNumber(full)` + 5 × `eth_getLogs` + receipts (≤ ~6: proxy/wallet transactions × receipt-needing monitors) ≈ **40–46** | 1 + 5 + 5 + receipts ≈ **11–17** |
| Monitor while every URL is rate-limited | each 2 s tick: `eth_blockNumber` 4 attempts per URL + a `net_version` probe; soak-1 log: ~1,700 logged failed responses/h ≈ 29/min, each after 3 middleware retries ≈ **~115 attempts/min** | ≤ ~10 attempts per 60 s tick ≈ **≤ 10/min** |
| Monitor recovery job | 1 `eth_blockNumber` / 5 min (0.2) | same (0.2) |
| Worker (unchanged) | per step (every 30 s + its duration): reorg probe 1, head 1, `eth_getLogs` 1, end hash 1, lag head 1 ≈ 10/min; checks (logical calls in JSON-RPC batches of 50; soak-1 `status` counts): M5 3,094 / 300 s, M2 2,874 / 600 s, M3 1,195 / 600 s, M13 8 / step, M9/M11/M12 a few → ≈ 1,050 calls/min ≈ 21 batches/min; total ≈ **30–35** | same ≈ **30–35** (the stall watch reads no RPC: it reuses `lag_check`'s head) |

Monitor + worker on one IP: ≈ 75 → ≈ 45 requests/min healthy, and ≈ 150 → ≈ 45 while the Monitor's endpoints fail.

## State table (DEFECT-CLASSES obligation; new persisted state and inputs)

| State / input | (a) empty | (b) error | (c) partial | (d) reorg 1 / N | (e) restart | (f) same block | (g) time window |
|---|---|---|---|---|---|---|---|
| Monitor last-block file | empty string = not a number: one observation is not OK; alert only after 240 s (`test_unparseable…one_empty_read_is_not`) | missing / unreadable (directory) / garbage = not OK → stall (`test_missing…`, `test_unreadable…`, `test_unparseable…`) | a lower value (lagging endpoint) = a change, no skip; a jump = skipped range (`test_jump_*`) | the Monitor's value is a block number at 2 confirmations; a reorg does not move it backwards beyond that; the watch does not use hashes (no state to rewind) | `meta monitor:watch` durable: open stall kept, not repeated (`test_restart_mid_stall…`), onset kept (`test_restart_before_the_threshold…`), restarts in the sweep | — | wall clock (`stall_after_s` from the first not-OK observation; not advancing from the last change); skip exactness from the previous read's age (`test_jump_after_an_unobserved_gap…`) |
| Head (worker RPC) | — | unknown = not OK, stall after 300 s; recovery only after a measured lag (`test_unknown_head…`) | a stale head can make the lag look small: the not-advancing rule still fires (`test_not_advancing…`) | — | — | — | — |
| `meta monitor:watch` stall number | first stall = 1 | — | — | — | survives restarts; a second stall in the window is a new key (`test_a_second_stall…`) | — | re-sent per health window (`test_a_long_stall…`) |
| Skipped ranges | none when the jump ≤ M+1 (`test_jump_within…`) | — | — | — | announced once per range (dedup key) | — | upper bound when unobserved > 1.5 cycles |

Seeded sweep: `test_seeded_sweep_every_skipped_block_is_announced_and_no_stall_is_silent[0..11]`. Spec trigger list
(D20 text): Tenderly + two non-Tenderly endpoints → template + S5 + selftest; cron 60 s → template + S5; stall health
alert → `monitor-stall` tests; no silent skipped range → `monitor-skipped` tests + sweep; Monitor outage/stall tests →
the above + item 7.

## Residuals and findings (for QA / owner)

- **R1 (Rust core, out of scope §1):** a block whose per-block filter fails (its `eth_getLogs` or a receipt fails
  after retries and rotation) is counted as processed with no matches and the last block advances: that block's
  Monitor rules are silently not evaluated, and the last-block file cannot show it. Four endpoints across two
  providers make it rarer; only the Monitor's ERROR log (`Error occurred, …` from the filter) shows it. Closing it
  needs a Rust change (record the block as missed) or a log-based alert. Owner: Marco (D15 class: possible lost page).
- **R2:** if the last-block file disappears and reappears between two worker reads (≤ 30 s), the reset is not
  observed and the up-to-50-block tail before the new value is not reported (the Monitor restart takes longer than
  a worker step, so a lost volume is normally seen missing first).
- **R3:** `max_past_blocks` in the worker config must not exceed the Monitor's (pytest checks the example config
  against the template; an operator's own `config.json` is not checked).
- **R4:** the stall alert comes ~4–5 min after the Monitor's last successful cycle, plus the longest single check run
  of the worker (delivery happens between check runs); an outage longer than ~10 min loses blocks (named). A worker
  RPC outage of ≥ 4 min also opens a Monitor stall (unknown = stall, by the packet's rule), in addition to the
  worker's own `rpc` message.
- **R5 (Rust core, out of scope §1; coordinator ruling A+B+C):** the Monitor's rotation tries **one** fallback per
  failed request: `try_rotate_url` (`src/services/blockchain/transports/http/endpoint_manager.rs`) takes the first
  queued URL ≠ active and, if its `net_version` probe fails, the request fails with the queue unchanged; the queue is
  built in weight order without the URL active at start (`transport.rs` `HttpTransportClient::new`). Repro
  (ms-cp4r, `.local-dry/evidence/monitor-log-0140.txt`): Tenderly was 429 at start, so active = `rpc.mevblocker.io`,
  queue head = `MAINNET_RPC_URL` (Tenderly); when mevblocker 429s (Cloudflare 1015, during the worker's backfill and
  first live runs on the same IP) every rotation probes Tenderly: 72 `Failed to connect to new URL
  'https://mainnet.gateway.tenderly.co'`, 36 mevblocker 429s, **0 requests to blockmachine**; 3 of 21 cycles failed
  and the Monitor recovered by itself. Runbook: production `MAINNET_RPC_URL` = company/non-Tenderly endpoint,
  `MAINNET_RPC_URL_2` unset; ms-cp4r runs the D16 env (`MAINNET_RPC_URL` = Tenderly, `_2` unset). The template stays as
  D20 says (a Tenderly failure rotates to a non-Tenderly endpoint next).
- **O1 (observation, shared IP):** with Tenderly answering 429 to this host most of the time, the worker's bursts
  (backfill, silent evaluation, first live full sweeps: thousands of `eth_call`) also land on `rpc.mevblocker.io`,
  the Monitor's serving endpoint, and cause its 429s. The worker list ends with blockmachine as the packet asks;
  preferring blockmachine before mevblocker in the worker (or a production RPC) would separate the two. Owner:
  coordinator/Marco.

## Item 7 — local proof on `ms-cp4r`

See [attempts.md](attempts.md#attempt-1) for commands, exit codes and results.

## Completion handoff

See [attempts.md](attempts.md#handoff).
