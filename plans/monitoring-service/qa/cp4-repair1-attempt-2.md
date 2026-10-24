PASS WITH NOTES

# CP4 repair QA, attempt 2 (repair 2: D20 + D21, Monitor filter failures never silent, worker endpoint order)

Independent QA of [plan.md](../plan.md) CP4 repair 2 under [QA-CONTRACT.md](QA-CONTRACT.md) and decisions §2 D20, D21 (with D15, D16). Inputs I read: [attempt 1](cp4-repair1-attempt-1.md) (F1 BLOCKING, N1–N8), events 0084–0088, the implementer record [workers/cp4-repair1/plan.md § Repair 2](../workers/cp4-repair1/plan.md) and [attempts.md "Repair 2"](../workers/cp4-repair1/attempts.md).

- **Frozen head under test:** `eeefc58dabe8f59bc0c102aea3b375632210cc58` (branch `mtabasco/ms-cp4-repair1`). Repair 2 starts at `e0b8fab`. Code commits: `58635cb` (Rust) and `44884e5` (worker, template, gate). QA worktree `ms-qa-cp4-r2`, branch `mtabasco/ms-qa-cp4-r2`. `git rev-parse HEAD` = the frozen head at start; initial porcelain empty.
- **Diffs of record:**
  - repair 2, `git diff e0b8fab..eeefc58`: 31 files, +3,388/−120;
  - whole repair, `git diff e4c6502..eeefc58`: 41 files, +5,108/−118.
  - Rust files changed, whole repair: only `src/bootstrap/mod.rs` and `src/services/blockwatcher/{mod,recovery,service,unevaluated}.rs`. That is within the D21 exception.
- **QA model:** Claude Opus 5.5 (`claude-opus-5-5`), effort high (D13). Task `task_d628978298a3`, dispatch `ctx_28a134fe15b9`.
- **Measured:** 2026-10-24T07:29Z–08:32Z (host UTC).
- **Policy:** D15, second QA round of this repair. BLOCKING = missed or lost page, false clean, false page, unsafe action, data loss, fail-open gate, secret leak. Everything else is a NOTE with a reason and an owner.
- **Scratch:** `$S` = `/private/tmp/claude-501/-Users-marco-orca-workspaces-ssv-network-monitor-ms-qa-cp4-r2/e2a98544-bdfd-43db-9a07-41aec1564a26/scratchpad`, outside the repo and not committed. It holds:
  - own Rust scenarios: `$S/rustqa` (a `git archive` copy of the head plus a `qa_d21` test module), log `$S/rust/qa.log`;
  - own worker scenarios: `$S/qa/test_qa_cp4r2.py`;
  - the injection proxy: `$S/inject/qa_inject.py`;
  - live evidence: `$S/live/`.

## Summary

- **Attempt-1 F1 (R1, BLOCKING) is closed, and I re-measured it live.**
  - Every block whose filter failed was recorded as missed before the last block moved past it. Each one was then either recovered by the recovery job, with a log line, or given up and announced by `monitor-unevaluated`.
  - Accounting on `ms-cp4r`:
    - **38 min on the real endpoints:** 07:21:22–07:59:34, 38 cycles, 190 contiguous blocks 26,260,969–26,261,158. There were **0** `filter_block` ERRORs, so there was nothing to account for.
    - **23 min of my fault injection, plus 6 min after the restore:** 180 `ERROR filter_block` lines and **65 blocks reported not evaluated**. Of these:
      - 64 were my injected blocks. The other 2 failed for real, on blockmachine 429s to `eth_getTransactionReceipt`, so the receipt path was exercised too. One injected block was never reported: its injected failures hit a trigger script's range query, not a block filter (§3.1).
      - All 65 were recorded. 61 were recovered, each with a log line. 4 were given up after exactly 10 attempts and announced: 26,261,159, 26,261,170, 26,261,181, 26,261,192.
      - **0 unaccounted. 0 recovered blocks announced. 0 given-up blocks unannounced.**
- **A match found on a recovery retry reaches the trigger exactly once. Measured live with real matches.**
  - During the injection, a burst of SSV cluster and oracle activity produced 47 matches.
  - **19 matched blocks had a failed main-cycle filter** and were delivered through recovery: 26,261,186 … 26,261,227. matches.jsonl has 56 lines and **0 duplicate (tx, monitor) pairs**.
  - The implementer's run is consistent: block 26,260,905 (`claimEthRewards`) failed at 07:09:01, was recovered at 07:10:32, and appears exactly once.
  - Own Rust scenario QA-R3: the main cycle delivers 0, recovery delivers 1, and later recovery runs and main cycles deliver 0.
- **A crash between a filter failure and the save loses nothing (live).**
  - My kill-watcher sent SIGKILL to the Monitor at 08:03:00.968, 138 ms after `Block 26261174 was not evaluated`. That was inside the cycle, before its record/last-block write.
  - The last block stayed 26,261,173, and the missed file held none of that cycle's blocks.
  - After `docker start`, the next cycle started at 26,261,174. It recorded the failing blocks again, and 26,261,174/176/178 were all recovered.
  - Offline (QA-R1a, R1b): a failed last-block write after the record, and a panic in the processing stage, both lose nothing. Both can deliver a duplicate (N3).
- **Other error types are not read as "no logs".** I injected HTTP 200 with a JSON-RPC error, HTTP 200 with `"result": null`, and HTTP 503. Every one was reported `not evaluated` (`Failed to parse logs` for null) and recorded.
- **Healthy blocks are unchanged.**
  - The touched-module cargo tests pass 120/120 (no mockall flake). Own scenarios QA-R6 and the baseline window show it too.
  - The only change on the healthy path is the task-local scope around the processing stage. The join result is used only for the reported set.
- **Recovery load (attack 5) measured.**
  - On the single injected endpoint the rate was 15–93 req/min (median 52), against about 11/min for the main job alone.
  - blockmachine itself returned 429 twice in 23 min. Those 429s aborted 2 of 23 recovery runs and caused the 2 real receipt failures. All were recorded and recovered.
  - The main job is never skipped: it waits on the lock. Main cycles took ≤ 5.3 s, recovery runs ≤ 24 s, and no recovery run was skipped by the lock.
  - NOTE N6 covers the backlog and the alert rate.
- **The worker side fails closed.** Garbage, empty, permission-denied or wrong-typed missed-blocks files and an unknown status all produce health (own QW4 plus the suite). Pruning and restart are classified correctly (QW5).
- **Live, the worker endpoint order is the D21 order:** `MAINNET_RPC_URL, rpc-eth.blockmachine.io, gateway.tenderly.co, rpc.mevblocker.io` (N7 covers what happens in practice).
- **Everything re-measured green:**
  - suite **2313 passed**;
  - ruff clean;
  - check-templates, check-abi and check-m15 exit 0;
  - selftest **310 passed, 0 failed, 0 skipped**, including N7 `cronhourly`/`cronsecondlist` red and D21 ×5 red;
  - composed compose config exit 0;
  - no keyed URL in the diff;
  - running config byte-identical to a fresh render of the frozen head;
  - worker image files identical to the head.
- **No BLOCKING finding.** There are eight NOTEs, N1–N8. Three of them are config or crash-window gaps where a block can go unnamed: N1 (wrong `missed_blocks_path`), N4 (empty file after a crash mid-write) and N5 (inode reuse).

## 1. Findings

No BLOCKING finding. Every NOTE has a reproducer, a one-line reason and an owner.

### N1 — NOTE: a `missed_blocks_path` that points to a missing directory reads as "no missed block" forever
- **Reproducer:** `PYTHONPATH=reconcile:reconcile/tests reconcile/.venv/bin/python -m pytest -s $S/qa/test_qa_cp4r2.py -k qw3`.
  - The real file holds two `Failed` entries, and `missed_blocks_path` points to `<tmp>/nowhere/x_missed_blocks.json`.
  - Expected: health. Actual: 0 messages, `missed.ok: true`.
- **Cause:** `read_missed_blocks` maps `FileNotFoundError` to `{}`. That is correct for the real file, which the Monitor creates lazily, but a mistyped path is indistinguishable from it.
- **Reason for NOTE:** it needs operator misconfiguration. The example config, the compose mount and the derived default agree; `test_example_config_template_and_recovery_tuning_agree` checks the example. Same class as R3/N5.
- **Cheap fix:** fail closed when the file's directory does not exist, or differs from the directory of `last_block_path`, whose file must exist.
- **Owner:** coordinator.

### N2 — NOTE: an entry left `Recovering` by a Monitor crash during a recovery run is never retried; the worker's text says it is
- **Source:** `recovery.rs` marks `Recovering` before the attempt. `storage.rs` `get_missed_blocks` selects only `Pending`. So a kill or restart mid-run leaves the block unevaluated until it is pruned after `max_block_age` (about 24 h).
- **Reproducer:** `-k qw1`.
  - The worker announces it after 1200 s ("… It keeps retrying them …"). It announces it again, as final, after the prune.
  - So it is not silent, but the first text is wrong for this case.
  - On `ms-cp4r` no entry is left `Recovering`; the sampler saw `Rec` only during runs.
- **Reason for NOTE:** the block is announced, so no page is lost. The wording is misleading.
- **Owner:** coordinator for the wording ("may not be retried if the Monitor restarted during a recovery run"). Marco for the optional Rust change: treat a `Recovering` entry as `Pending` at load.

### N3 — NOTE: duplicate delivery in two crash/error paths (never loss)
- **QA-R1a:** `save_last_processed_block` fails after the unevaluated block was saved. The next cycle evaluates the range again and delivers the match. The recovery also delivers it.
  - Measured: `QA-R1a delivered per block: {85: 2}`.
  - Reproducer: `cd $S/rustqa && CARGO_TARGET_DIR=<worktree>/target cargo test --lib -- qa_d21 --nocapture`.
- **QA-R1b:** a panic in the processing stage records every fetched block, including the evaluated ones whose triggers already ran (`recorded=[81..88]`). The recovery then re-delivers their matches.
- **Reason for NOTE:** a duplicated real page is not in D15's BLOCKING list. It needs a disk-write failure or a panic, and the direction is safe.
- **Owner:** Marco (Rust, optional).

### N4 — NOTE: an empty missed-blocks file left by a crash in the middle of a write drops the recovery queue
- **Mechanism:**
  - `storage.rs` `load_missed_blocks_json` reads a whitespace file as `[]`. Its next save keeps only the new entries, written in place, so the inode is unchanged.
  - The worker sends "cannot be read" while the file is empty. That message says "since then".
  - The dropped Pending entries were recorded earlier. Once the file is rewritten in place they vanish young and are classified as recovered, so they are never named.
- **Reproducer:** `-k qw2` (`QW2 block b named: False`).
- **Reason for NOTE:** the window is a kill between `O_TRUNC` and `write` (microseconds), and a health message is sent.
- **Owner:** coordinator. Fix: on a readable file after an unreadable episode, announce every known Pending entry that is gone as "may not have been evaluated".

### N5 — NOTE: the inode identity (attempt-1 N2 fix, and D21 "replaced") is defeated by inode reuse on Docker's ext4
- **Measured in a throwaway container with an anonymous volume** (`docker run --rm -v /d …`):
  - deleting and re-creating a file in the same directory: inode 31196 → **31196**;
  - replacing its directory: 31187 → **31187**.
- **Reproducer:** `-k qw6`. A young Pending entry that vanishes on the same (dev, inode) is classified as recovered: 0 messages.
- **Limits:**
  - Missing-file observations are still caught (a worker read during the gap).
  - An in-place rewrite is not a reset, which is correct.
- **Reason for NOTE:** it needs the Monitor's data volume to be lost or recreated while the worker is not observing. The implementer records this limit; I measured that it is the common case on this file system, not a corner case.
- **Owner:** coordinator / Marco. Options: also persist a content fingerprint, or write a Monitor-side marker.

### N6 — NOTE (attack 5): recovery load, backlog and alert rate
- **Load and timing during my injection:**
  - The single endpoint served 15–93 req/min (median 52) while recovery retried up to 10 blocks a minute. The main job alone needs about 11.
  - blockmachine itself answered 429 at 08:09:30 and 08:13:30 (`eth_blockNumber`), which aborted those recovery runs ("Failed to process missed blocks" ×3 in total). It also failed 2 receipt fetches; those blocks were recorded and recovered.
  - The main job was never skipped (`run_lock.lock().await`). Main cycles took ≤ 5,321 ms, and 20 recovery runs completed, each ≤ 24 s.
- **Backlog (QA-R4):** blocks are retried oldest first, 10 per run, given up at 10. With 12 blocks that never evaluate, blocks 11 and 12 get their first attempt in run 11. Each block gets exactly 10 attempts and is then `Failed`, with no 11th attempt.
- **Alert rate (QW7, a persistent filter-only outage of 60 min):** 300 blocks recorded, 55 given up, 148 waiting ≥ 1200 s. All are announced, in **84 `monitor-unevaluated` messages in one hour** on the page route.
- **Reason for NOTE:** this is a performance observation and an alert-volume issue, never silent.
- **Owner:** coordinator. Options: aggregate per health window, and back off recovery when `eth_blockNumber` returns 429.

### N7 — NOTE (O1 in practice): the worker still reaches `rpc.mevblocker.io` often in this environment
- **Measured:** worker log 07:21–07:59Z:
  - 40 endpoint switches, 17 of them onto mevblocker;
  - each time Tenderly, blockmachine and Tenderly public had all answered 429 to this host (shared IP).
- The order is the D21 order (log line `RPC endpoints in failover order: MAINNET_RPC_URL, rpc-eth.blockmachine.io, gateway.tenderly.co, rpc.mevblocker.io`; `test_default_endpoints_are_the_d21_list_in_order`).
- In the same window the Monitor had 0 filter errors, so no Monitor impact was measured.
- **Reason for NOTE:** a rate observation only.
- **Owner:** Marco (production primary RPC, already in the R5 runbook note).

### N8 — NOTE (record): one deviation and one observation
- **Deviation (mine):** for N5 I pulled `alpine:latest` from Docker Hub. That is network outside the D16/D20/D21 RPCs. I removed the image afterwards (`docker rmi`, 0 left).
- **Observation:**
  - Given-up block 26,261,192 (my permanent 503) holds 1 SSV proxy log (Tenderly `eth_getLogs`).
  - D21's designed outcome is that `monitor-unevaluated` names it ("check by hand") instead of a per-rule alert, and that happened at 08:21:57Z.
  - The trigger script `oracle_dissent_check.py` also met my injected `eth_getLogs` error on a range starting at 26,261,120 and kept its match, which is designed fail-closed (CP2b).
- **Owner:** coordinator (record).

### Attempt-1 notes: status after repair 2
| Attempt-1 | Status at `eeefc58` |
|---|---|
| F1 (R1) | **closed** (above, live and offline) |
| N1 R5 | unchanged, NOTE (R5 measured again by the implementer) |
| N2 R2 | fixed with an inode reset (`test_file_created_again_between_two_reads_is_a_reset` green); limit measured as N5 |
| N3 false skip after a long worker downtime | fixed: suite test asserts "may not have been evaluated" and not "were NOT evaluated" |
| N4 R4, N5 R3, N6 O1 | unchanged / O1 addressed by D21 order (N7) |
| N7 cron gate | fixed: selftest `D20 cronhourly is red`, `D20 cronsecondlist is red` |
| N8 record wording | fixed: correction in workers/cp4-repair1/plan.md (repair 2, N8), `scripts/local-dry-run.sh:15-16` |

## 2. Claims I could NOT confirm

- **"cargo lib 1069 passed" (full lib):** not re-run (owner scope: touched modules only). I ran `cargo test --lib -- bootstrap services::blockwatcher`: 120 passed.
- **Implementer RED runs (6 Rust, 39 worker) and the worker falsifiers W1–W5:** not re-run (no mutation testing). My own scenarios replace them, §4.
- **clippy/fmt:** not re-run.
- **The implementer's injection 1:** log partly lost, as stated in the record. I did not rely on it.
- **The implementer's "injection 2 — 0 unaccounted":** not recomputed from their files. I ran my own accounting over my own injection.
- **The implementer's outage control superset (19 ⊇ 14):** not repeated. My fault control was the injection plus a kill.
- **"Recovery runs: 13, no run skipped":** in my run 2 of 23 runs were aborted by real blockmachine 429s (N6). The claim concerns the lock, and on the lock I agree: none was skipped by it.

## 3. Verification matrix

| Row | Command | Exit / result |
|---|---|---|
| preflight | `git rev-parse HEAD`; `git status --porcelain`; `df -h .`; `uv venv -p 3.12 reconcile/.venv && uv pip install -r reconcile/requirements-dev.txt` | `eeefc58…` = frozen head; porcelain empty; 277 GiB free; Python 3.12.15 |
| suite (one normal run) | `reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider reconcile/tests` | **2313 passed**, 0 failed (58 s; piped to `tail`, so summary line read, exit not captured separately) |
| D21 worker tests | `pytest reconcile/tests/test_monitor_unevaluated.py reconcile/tests/test_monitor_watch.py reconcile/tests/test_rpc_failover.py` | 106 passed |
| ruff | `reconcile/.venv/bin/ruff check reconcile` | exit 0 |
| templates | `deploy/check-templates.sh` | exit 0 `checker: OK (32 monitors, 30 triggers, 1 network)` |
| abi / m15 | `deploy/check-abi.sh --config-dir deploy/config`; `deploy/check-m15-coverage.sh` | exit 0 / exit 0 |
| selftest | `MONITOR_BIN=../ms-cp1a/target/release/openzeppelin-monitor deploy/tests/selftest.sh` | exit 0, **310 passed, 0 failed, 0 skipped**; D21 `recoveryoff`/`recovery5min`/`retries3`/`perrun100`/`perrun0` red; N7 `cronhourly`/`cronsecondlist` red |
| Rust touched modules | `cargo test --lib -- bootstrap services::blockwatcher` | exit 0, **120 passed**, 0 failed (no mockall flake, no serial rerun needed) |
| Rust own scenarios | `$S/rustqa`: `cargo test --lib -- qa_d21 --nocapture` | exit 0, 5 passed (QA-R1a, R1b, R3, R4, R6; §4) |
| worker own scenarios | `PYTHONPATH=reconcile:reconcile/tests pytest -s $S/qa/test_qa_cp4r2.py` | exit 0, 7 passed (QW1–QW7, of which QW2/3/6/7 are characterisations → N1, N4, N5, N6) |
| V3.3 Monitor + worker | `docker compose -f docker-compose.yaml -f docker-compose.reconcile.yaml config -q`; `docker inspect` | exit 0. Live: Monitor config `.local-dry/config` ro, `ms-cp4r_monitor-data` → `/app/data`, healthcheck `st_mtime < 300` interval 60 s; worker `/monitor` and `/inbox` ro, `/data` rw. No compose change in repair 2 |
| live config (no leftovers) | `git archive eeefc58` → `scripts/local-dry-run.sh --prepare` (env D16, no TEST_*) in scratch; `diff -r` against `ms-cp4-repair1/.local-dry/config`; md5 inside the container | **identical**; network `MAINNET_RPC_URL(100) → mevblocker(90) → blockmachine(70)`, cron `0 * * * * *`, `max_past_blocks` 50, recovery `30 * * * * *`/10/7200/10/1000; env `MAINNET_RPC_URL=https://mainnet.gateway.tenderly.co`, `_2` empty; watch config = example + `record_path` only |
| live images | `docker inspect`; md5 of `/app/*.py` and `checks/*.py` against the head; `grep -c "was not evaluated"` in the Monitor binary | Monitor `9ec675a2a69e` (has the D21 text); worker `fb4b692e4a7a`, 34 files identical to the head; `44884e5..eeefc58` changes only `plans/` |
| V3.4 (`ms-cp4r`) | worker `status`; Monitor file; status file; metrics | 07:59Z: phase live, checkpoint 26,261,149, undelivered 0, monitor last 26,261,158, head 26,261,162, not stalled. **Final 08:29Z:** phase live, checkpoint 26,261,293, lag 0, undelivered 0; monitor last 26,261,298, head 26,261,305 (lag 7), stalled false; `missed` ok, pending 0, failed 8, `unevaluated_blocks_total` 8 (= the 4 earlier and my 4, all announced); `input_unavailable{monitor}` 0; status file `healthy: true`; both containers `healthy`. **Met** |
| V3.5 Monitor fault control | §3.1 | **Met:** 65 failures, 0 unaccounted; kill mid-cycle: no loss |
| V3.5 worker RPC cut | suite (`test_unknown_head_is_a_stall_never_clean` etc.) | green; not run live (one live control by brief) |
| F1 reproducers (offline form) | the counting rule of attempt 1 (filter failures per `Processed` cycle) is now checked by `qa_account.py` over the logs: every failure must be recovered or announced; QA-R6 = reproducer 3 (51-block catch-up all failing) | 0 unaccounted; QA-R6: all 51 recorded, last block 1070 |
| F1 live counting (D16 env) | `$S/live/qa_account.py` over `monitor-A.clean` | 38 cycles, 0 filter errors, 0 not evaluated; nothing to account |
| D21 Rust items | source read + QA-R1a/R1b/R3/R4/R6 + live | recorded before the last-block write (`service.rs` `save_missed_blocks` before `save_last_processed_block`, error → last block kept); shutdown and no-matches report nothing (`test_shutdown_reports_nothing`, `test_no_matches_is_evaluated`); recovery retries and delivers once (QA-R3, live 19 blocks); gives up after exactly `max_retries` (QA-R4, live 10 attempts) |
| D21 worker items | suite + QW1–QW7 + live | given up → announced (live ×4); pending > 1200 s → announced (QW1, QW7); vanished: pruned → announced (QW5), file removed → announced (suite), same-inode replace → silent (N5); unreadable/garbage/empty/permission → health, `healthy: false`, metrics −1 (suite + QW4); RPC `last_error` never sent (suite) |
| D21 worker order | example config, `rpc.py` `DEFAULT_RPC_URLS`, live log | Tenderly → blockmachine → Tenderly public → mevblocker. **Met** (practice: N7) |
| V4.1 restricted (cross-module) | read of both diffs + scenarios | (a) **health kinds:** `monitor-unevaluated` keys `health:monitor-unevaluated:<hash>` from `monitor-unevaluated:<reason>:<lo>:<hi>` / `:unreadable:<n>` (realert per window) cannot collide with `monitor-stall`/`monitor-skipped`/`rpc`; outbox insert happens before the meta write (crash → re-announce, deduplicated); no backfill suppression of health (`alerts.enqueue`); `problem()` lists stall, then the unreadable file. (b) **recovery cron vs main cron and the lock:** main `0 * * * * *` waits on `run_lock`; recovery `30 * * * * *` `try_lock`s and skips; gate S5 rejects the same second. Live: no skip; 2 aborts on 429 (N6). (c) **`max_block_age`:** template 7200 = worker 7200 (test); worker "young" = above `ref − 3600`, Rust prune below `cur − 7200`; pending threshold 1200 s ≪ 24 h and > the 10-run give-up (~10 min) unless backlogged. (d) **template ↔ example ↔ gate:** agree (suite test, gate S5 green on the tracked file, selftest red cases). (e) **inbox:** a late failed-tx row from a recovered block is still delivered (`inbox.confirm` takes rows at or below the checkpoint); live `SSV Failed Tx Inbox` match recorded once |
| keyed URL / sensitive paths | URL scan of `git diff e0b8fab..eeefc58`; `git diff --name-only e4c6502..eeefc58` | URLs only the 4 public ones and the test `https://k.example/v1/S3CRET`; evidence files: no token/key pattern; no `.env`, `config/`, sqlite, `.local-dry` path |

### 3.1 Fault control on `ms-cp4r` (injection + a SIGKILL mid-cycle), restored exactly

- **Mode:** the running soak-2 stack. Commands ran from `ms-cp4-repair1` with `source .local-dry/cp4r.env.sh`.
- **Proxy:** `$S/inject/qa_inject.py https://rpc-eth.blockmachine.io 8548`. It forwards everything and logs requests per method per minute. For `eth_getLogs` of block n it answers:
  - `n % 4 == 0`: HTTP 200 + JSON-RPC error `-32005`, first 4 requests of the block;
  - `n % 4 == 2`: HTTP 200 + `"result": null`, first 4 requests;
  - `n % 11 == 1`: HTTP 503, always.
- **Injection config:** `$S/inject/config` = `.local-dry/config` except the network `rpc_urls` = `[http://host.docker.internal:8548]` (`diff -r`). Nothing under `ms-cp4-repair1/.local-dry` was changed.

| UTC | Event | Measured |
|---|---|---|
| 07:21:22–07:59:34 | real endpoints (D16), log streamed | 38 cycles, 190 contiguous blocks, **0** filter errors, 10 recoveries of the implementer's injected blocks at 07:21:30 |
| 07:59:34 | `MONITOR_CONFIG_DIR=$S/inject/config docker compose up -d monitor` | exit 0 |
| 08:00:00–08:00:02 | first cycle | rpcerror → `Block 26261160 was not evaluated`; null → `Block 26261162 … Failed to parse logs`; 503 ×4 → `26261159`; `3 blocks were not evaluated (filter failed), recorded as missed for recovery` **before** `Processed 5 blocks (26261159-26261163)` |
| 08:03:00.968 | kill-watcher (`$S/live/killer.sh 3`): `docker kill -s KILL` 138 ms after `Block 26261174 was not evaluated` inside the cycle | Monitor `Exited (137)`; last block **26,261,173** (unchanged); missed file without 26,261,174+ (`$S/live/missed-after-kill.txt`) |
| 08:03:08 | `docker start ms-cp4r-monitor-1` (same container) | recovery 08:03:30: attempted 8, recovered 2; cycle 08:04:00 `Last processed block: 26261173 / Start block: 26261174`; blocks of the killed cycle recorded again, later recovered |
| 08:10:47, 08:12:59, 08:16:36, 08:21:57 | `monitor-unevaluated` "gave up on blocks …" (record mode, sent) | 26,261,159 / 170 / 181 / 192, each after 10 attempts |
| 08:09:30, 08:13:30 | real blockmachine 429 on `eth_blockNumber` | two recovery runs aborted (N6); 2 real receipt 429s → blocks 26,261,197 and 26,261,227 recorded, recovered |
| 08:22:52 | restore: `docker compose up -d monitor` (D16 env); proxy stopped | exit 0; first cycle `Start block: 26261274` (last before = 26,261,273, contiguous) |
| 08:23–08:26:38 | recovery on the real endpoints | 10 + 10 + 10 + 7 recovered, 0 failed; file: 8 `Failed`, 0 Pending/Recovering |
| 08:29 | final | see V3.4; 0 ERROR lines since restore |

- **Accounting** (`python3 -I $S/live/qa_account.py monitor-BC.clean outbox-BC.txt missed-final.json 1200 <now>`, run at 08:27:34):
  - 27 main cycles, 26,261,159–26,261,298;
  - 180 `ERROR filter_block` lines;
  - 65 blocks reported not evaluated, all in a `recorded as missed` line;
  - recovered (log line) 61, gave up (log line) 4, announced 4;
  - recovered AND announced: none; gave up but not announced: none;
  - **UNACCOUNTED: 0**.
  - The other 89 ERROR lines are `get_blocks`/`get_latest_block_number` 429s from blockmachine. No `Failed to process blocks`, so no main cycle was lost, and the ranges are contiguous.
  - Injected blocks: 64. The two real failures (26,261,197 and 26,261,227, receipts) are counted among the 65 reported blocks.
  - One injected block was never reported: 26,261,120. Its four requests were the range query of the trigger script `oracle_dissent_check.py`, not a block filter. The script kept its match (N8).
- **Exactly-once:** `$S/live/matches-final.jsonl` has 56 lines, 47 of them new during the window, from 36 blocks.
  - 19 matched blocks had a failed main-cycle filter, and all 19 were recovered.
  - Duplicate (tx, monitor) pairs: none. Matches in given-up blocks: none possible (not evaluated).
  - Proxy logs in the given-up blocks, by hand (Tenderly): 26,261,192 holds 1. The others, and the implementer's 4, hold 0.
- **Restoration proof:**
  - `docker inspect` before and after (`$S/live/monitor-inspect-{pre,post}.txt`) is identical after sorting the env list and removing Docker Desktop's `/host_mnt` prefix: same image `9ec675a2a69e`, env D16, mounts `.local-dry/config` ro, `ms-cp4r_monitor-data`, `.local-dry/logs`, same healthcheck and restart policy.
  - The mounted network file has md5 `2163ae29…` in the container, the same as before.
  - The proxy is stopped (port 8548 free). The worker container was not touched, `ms-cp3` was not touched, and the stack is left **running** for soak 2.

## 4. Falsifiers

- **No mutation testing** (owner scope note). The implementer's RED runs and W1–W5 were not re-run.
- **Own scenarios, chosen outside the implementer's model:**
  1. **Error types other than 429 (live):** JSON-RPC error, `result: null`, 503. All reported, recorded and accounted. Probe of the class "an empty RPC result read as no logs": `null` is a parse error, not "no logs".
  2. **A crash between the filter failure and the save (live SIGKILL; offline QA-R1a/R1b):** no loss. Duplicates possible (N3).
  3. **A match on the recovery retry (QA-R3; live 19 blocks):** exactly once.
  4. **Backlog beyond `max_blocks_per_run` with blocks that never evaluate (QA-R4):** exactly 10 attempts, then `Failed`; oldest-first wait measured.
  5. **The whole 51-block catch-up failing (QA-R6):** all 51 recorded.
  6. **Worker:**
     - stuck `Recovering` (QW1, announced; wording N2);
     - empty file after a crash mid-write (QW2 → N4);
     - wrong path (QW3 → N1);
     - permission denied (QW4, health);
     - worker down while one entry is pruned and one recovered (QW5: only the pruned one is announced);
     - same-inode replace (QW6 → N5, inode reuse measured);
     - a 60-min filter outage (QW7: every block announced, 84 messages → N6).
  7. **Live counting on the real endpoints over 38 min:** 0 failures, so this window is a control and holds no evidence about the recovery path.
- **Restoration:** the temporary state was only the Monitor's config during the injection, plus the one kill (§3.1). My Rust and worker scenarios ran in scratch copies. No worktree file was modified.

## 5. Skipped (owner scope note) and environment

- **Skipped:** mutation testing; full `cargo test --lib`; clippy/fmt; a second suite run; the implementer's falsifiers and RED runs; a live Monitor outage (the attempt-1 outage path is unchanged by repair 2); a live worker RPC cut.
- **Network:**
  - the stack's own endpoints (Tenderly, mevblocker, blockmachine);
  - my proxy to `rpc-eth.blockmachine.io`;
  - 8 `eth_getLogs` to `mainnet.gateway.tenderly.co` (given-up blocks, by hand);
  - **one deviation:** the Docker Hub pull of `alpine:latest` for the inode test (N8), since removed.
- **Untouched:** `ms-cp3` and its volumes, `config/`, `config.zip`, `.env*`.
- No alert was sent: record mode, no TEST_* channel. No mainnet transaction. No push.

## 6. Evidence-plane writes

- This report.
- plan.md CP4 Status line prefixed `REPAIR 2 QA PASS WITH NOTES (head eeefc58)`.
- One event appended to execution-log.md.
- Untracked and ignored: `reconcile/.venv`, `target/`.

## 7. Repo proof

Before commit (staged), `git status --porcelain`:

```text
M  plans/monitoring-service/execution-log.md
M  plans/monitoring-service/plan.md
A  plans/monitoring-service/qa/cp4-repair1-attempt-2.md
```

`git diff --name-only eeefc58` (the frozen head), with the report added:

```text
plans/monitoring-service/execution-log.md
plans/monitoring-service/plan.md
plans/monitoring-service/qa/cp4-repair1-attempt-2.md
```

`git diff --name-only e4c6502` lists the repair's 41 files plus these three. No implementation, config, template, compose, test or Rust file was touched by this QA. The commit on `mtabasco/ms-qa-cp4-r2` contains only the three paths above.
