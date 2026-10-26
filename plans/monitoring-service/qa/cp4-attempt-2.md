PASS WITH NOTES

# CP4 final QA, attempt 2, part B (V4.2 soak 2, part A delta re-check, V4.4 refresh)

Independent final QA of [plan.md](../plan.md) CP4, part B. Part A ([cp4-attempt-1.md](cp4-attempt-1.md)) passed on `047e9ad`. Soak 1 failed (event 0084). Repairs 1 and 2 (D20, D21) passed QA ([cp4-repair1-attempt-2.md](cp4-repair1-attempt-2.md)). This attempt reviews soak 2 and re-checks the code delta `047e9ad..b681181`.

- **Candidate:** `plan/monitoring-service` code at `b681181`. Worktree `ms-qa-cp4-b` (branch `mtabasco/ms-qa-cp4-b`), HEAD `4147788` = `b681181` + plan records only. `git diff b681181 HEAD -- . ':!plans'` is empty. Initial porcelain was empty.
- **Soak 2 evidence (read-only, untracked):** `/Users/marco/orca/workspaces/ssv-network-monitor/ms-cp4-repair1/soak2/`. T0 2026-10-24T08:34Z, T24 2026-10-25T08:37Z, T48 2026-10-26T08:37Z. Stack `ms-cp4r` is still RUNNING. I ran read-only commands only: `docker exec` reads, `docker inspect`, and a `mode=ro` SQLite read. I did not restart, stop or modify the stack. I did not touch `ms-cp3`.
- **QA model:** Claude Opus 5.5 (`claude-opus-5-5`), effort high (D13). Task `task_72cc96a65468`, dispatch `ctx_b443ebead270`. Measured 2026-10-26T09:05Z–09:45Z UTC.
- **Policy:** QA-CONTRACT.md and the D15 round policy. BLOCKING applies only to a missed or lost page, a false clean, a false page, an unsafe action, data loss, a fail-open gate or a secret leak. Everything else is a NOTE with a reason and an owner. Owner scope: no mutation testing and no long rows.
- **Network:** only the public RPCs of D16/D20/D21 (`mainnet.gateway.tenderly.co`, `rpc-eth.blockmachine.io`, `gateway.tenderly.co/public/mainnet`) and `eth.api.pocket.network` as a read fallback (user CLAUDE.md list). No Blockscout, no SSV API, no Docker Hub. The venv and cargo used local caches (`--offline`).
- **Scratch:** `$S` = `/private/tmp/claude-501/-Users-marco-orca-workspaces-ssv-network-monitor-ms-qa-cp4-b/d0761323-c084-448c-96e6-11cb12501809/scratchpad`. It is outside the repo and not committed.

## Summary

- **A10 met.** There was no page in 48 h. Every warn has an on-chain explanation. Worker health was green at T0, T24 and T48 (`healthy: true`, phase `live`, lag 0/4/3 < 50). The outbox delivered every row (37 rows = 37 record lines in the same order, all `sent_at` set, 0 attempts failed, undelivered/failing 0 in all 11 metric snapshots).
- **Continuity: the Monitor never stalled and skipped no range.** It ran 2,884 cycles at intervals of 28.6–91.2 s; none was longer than 240 s. Each cycle started at last+1, with no regression. It advanced from 26,261,333 (mined 08:33:23Z on T0) to 26,275,703 (mined 08:35:23Z on T48), the full 14,370-block chain span. Its maximum lag at cycle start was 10 blocks. The stall, skipped and unevaluated counters stayed at 4/78/8 from T0 to T48; all of those come from QA control runs before T0.
- **Filter failures: 0 unaccounted.** There were 9 events covering 35 blocks. All 35 blocks were recorded as missed and all 35 were recovered: the sets are equal, there were no duplicate recoveries, and the latency was 21–88 s (median 83). None was given up and none was pending. The missed store was `[]` at T24 and at T48. One recovered block (26,272,548) held a match, delivered exactly once.
- **Independent on-chain cross-check: 0 Monitor misses over the whole soak range.**
  - 569 proxy logs and 330 txs (302 sent directly to the proxy).
  - Function-based success monitors: Cluster 239/239, Operator 39/39, Validator 6/6, Liquidation 0/0, Oracle 18/18 plus 6 extra.
  - Failure monitors and the failed-tx inbox: exactly the 6 reverted txs. Oracle nonce deltas independently give 6 failed txs.
  - Event monitors: DAO 0/0, seed stake 0/0, dissent 0/0.
  - Other-sender 0/0, large transfer 0/0. Retired keys: balances and nonces unchanged.
- **No laptop gap.** The last `pmset` Sleep/Wake event is 2026-10-23 16:18 +0200, before T0. The largest Monitor cycle gap was 91 s. Both containers ran with 0 restarts: the Monitor since 08:22:55Z and the worker since 06:28:21Z on 2026-10-24.
- **The worker image runs the candidate code** (sha256 of all 34 `.py` files equal to `b681181`). The live Monitor config equals the templates, except for the dry-run triggers and the dropped unset `MAINNET_RPC_URL_2`.
- **Six M9 `commitRoot reverted` warns: all real reverts and all benign.** In each of the 6 rounds the fourth oracle's vote landed after the other three had committed the same root, so it reverted with `StaleBlockNumber` (`0x305c3e93`).
  - The reason reads "unknown" in 4 of 6 because the worker replays the tx at `block−1`, which cannot see the commit earlier in the same block (decoding gap).
  - Neither the noise nor the gap can create or hide a page.
- **The M1 "Cluster tuple unknown" warn on 0x6355…d699 was a transient false warn (a race).** The cluster is legacy and untouched since before activation (same SSV storage word at 24,920,727 and 26,273,277). It was migrated to ETH in 26,273,278. The SSV API listing already showed the migrated tuple while the worker evaluated 26,273,277.
  - The `unknown` path is warn-only, so it cannot create a page.
  - It cannot hide one either: `unknown` applies only to a cluster with no stored log, and a poisoning needs one.
- **Part A delta re-check: green.**
  - Suite 2313 passed, ruff clean.
  - check-templates, check-abi and check-m15 exit 0.
  - Selftest 310/0/0 after building the release binary (`cargo build --release --locked --offline` exit 0). The first run, without the binary, was 292/0/11 skipped.
  - Compose config exit 0. Cargo touched modules: lib 120/0, integration `blockwatcher bootstrap` serial 53/0. Parallel runs show an upstream mockall race in `tests/integration/bootstrap` (NOTE S9). V0.8 18/18, both parallel and serial.
  - V0.3: the same 2 dummy rows as part A (N2). Keyed-URL scan: 0 rows. `git log --stat 047e9ad..b681181` contains no forbidden path.
- **No BLOCKING finding.** Nine soak NOTEs (S1–S9) and two delta NOTEs (D1, D2), each with a reproducer, a reason and an owner.

## 1. Findings (all NOTE, D15)

### S1 — NOTE: every oracle round produces an M9 `commitRoot reverted` warn for a benign late vote (6 of 6 rounds)
- **Reproducer** (`$S/revert.py`, `$S/roots.py`): `python3 -I $S/revert.py $S <RootCommitted topic> <WRP topic> <the 6 tx hashes>`. It prints the round's `WeightedRootProposed`/`RootCommitted` logs and `eth_call` of the tx at `block−1` and at `block`.

  | tx | block (idx) | slot | round blockNum | RootCommitted at | late root == committed | eth_call @ N−1 | eth_call @ N | worker reason |
  |---|---|---|---|---|---|---|---|---|
  | 0x29f6225b | 26,263,581 (67) | 4 | 26,263,516 | same block, idx 55 | yes | succeeds `0x` | `0x305c3e93` | unknown |
  | 0x4d66370b | 26,265,977 (46) | 2 | 26,265,912 | same block, idx 45 | yes | `0x` | `0x305c3e93` | unknown |
  | 0x2e45cc73 | 26,268,373 (15) | 4 | 26,268,309 | same block, idx 14 | yes | `0x` | `0x305c3e93` | unknown |
  | 0x185940ed | 26,270,767 (78) | 2 | 26,270,702 | 26,270,766 idx 25 | yes | `0x305c3e93` | `0x305c3e93` | StaleBlockNumber |
  | 0x12995920 | 26,273,156 (26) | 4 | 26,273,091 | 26,273,155 idx 30 | yes | `0x305c3e93` | `0x305c3e93` | StaleBlockNumber |
  | 0xa8a9fd23 | 26,275,545 (166) | 4 | 26,275,480 | same block, idx 165 | yes | `0x` | `0x305c3e93` | unknown |
  | (pre-T0) 0x597736ec | 26,261,185 (15) | 3 | 26,261,120 | same block, idx 14 | yes | `0x` | `0x305c3e93` | unknown |

  Receipts show status 0, gasUsed 36,211/36,223. Three `WeightedRootProposed` logs per round reach `accumulatedWeight == quorum` and the third commits. The fourth vote, for the identical root, then reverts `StaleBlockNumber()`. No dissent: the dissent monitors' script returned false on all 6 `RootCommitted` logs (0 matches).
- **Real or false:** real reverts by spec M9 ("commitRoot reverts from slot addresses → warn"). No vote is lost: the round is already committed with the same root.
- **Why it is a NOTE:** noise, not a page. With D2 it is about 3 warns per day on the Telegram group, plus 3 resolutions, plus the Monitor's own oracle-failure messages. The spec wording says "the slot's vote is lost this round", which overstates a late vote for the committed root.
- **Owner:** Marco (spec M9 rule). Option: a `StaleBlockNumber` for a `blockNum` already committed with the same root becomes info, or is folded into the digest. Implementation goes to later repair.

### S2 — NOTE: revert-reason resolution at `block−1` cannot see same-block predecessors; 4 of 6 soak reverts read "unknown"
- **Reproducer:** the table in S1, plus a read-only query of the live inbox table:

  ```text
  docker exec ms-cp4r-eb-reconcile-1 python -c "import sqlite3; c=sqlite3.connect('file:/data/eb-reconcile.sqlite?mode=ro',uri=True); ..."
  ```

  Rows 1–4 and 7 have `revert_selector None, revert_source unresolved`. Rows 5–6 have `0x305c3e93 StaleBlockNumber eth_call`. Code: `reconcile/inbox.py:258-270` (`eth_call` at `hex(block - 1)`, then the Blockscout v2 fallback).
- **Effect per consumer** (code read):
  - **M9:** text only (`m9_oracle_quorum.py:460`). Severity is warn regardless of the reason.
  - **M1 / M2:** an unresolved revert from our senders becomes health (`m1-inbox-unresolved`, `m2-inbox-unresolved`), never clean. A same-block `IncorrectClusterState` or `ExceedValidatorLimit` would arrive as "check by hand" health instead of the incident warn.
  - **M11:** the benign test needs `revert_name == "StaleUpdate"` (`m11_updater_liveness.py:502`). A same-block `StaleUpdate` therefore counts as a real revert, and the revert-rate warn can fire falsely. The over-report goes in the warn direction.
  - **M14:** records the name only.
- **Why it is a NOTE:** no consumer derives a page from a revert name, so the gap can neither create nor hide a page. The over-reports go in the warn or health direction.
- **Cheap fix:** when the `block−1` replay succeeds, replay at `block`. In all 7 cases above `eth_call` at N returns the true selector. Optionally decode Blockscout's decoded form.
- **Not confirmed:** why the Blockscout fallback resolved none of the 4 (`blockscout: https://eth.blockscout.com` is configured, and no lookup-failure warning was logged). I made no Blockscout call (network scope).
- **Owner:** later repair (coordinator).

### S3 — NOTE: transient false M1 "Cluster tuple unknown" warn when the SSV API listing is newer than the evaluated block (dormant legacy cluster, first touch since activation)
- **Reproducer:** `reconcile/.venv/bin/python -I $S/m1.py $S <repo>`. It prints:
  - **The transaction:** `0xd2d9c919…` in block 26,273,278 (idx 21, status 1), sent by the owner `0x6ee8…6bb2`, selector `0x36e87b12` (`migrateClusterToETH`). Its log is `ClusterMigratedToETH`, tuple (1, 45534981966, 95984860424, true, 0.0288 ETH).
  - **Storage of the cluster** (ETH slot / SSV slot, via `eb_storage.eth_cluster_slot`/`ssv_cluster_slot`):

    | block | ETH word | SSV word |
    |---|---|---|
    | 24,920,727 (activation) | 0 | `0x6d64051f…` |
    | 26,273,276 | 0 | `0x6d64051f…` |
    | 26,273,277 | 0 | `0x6d64051f…` |
    | 26,273,278 | `0xd8a76c2f…` | 0 |
    | 26,273,281 | `0xd8a76c2f…` | 0 |
- **Mechanism** (`m1_stale_tuple.py:456-471`, `inventory.py:107-159`):
  - At 26,273,277 the cluster had no stored log, because it was untouched since before `start_block`. The inventory read storage at the evaluated block, but the API row it validated was the post-migration row (`migrated: true`, ETH balance), so the hash did not match and the status was `unknown`, a warn.
  - The next run (26,273,281) had the stored log from 26,273,278. That log validated, so the incident resolved 4 blocks later.
  - The window is the time between the transaction and the checkpoint passing it: `confirmations` 12 plus scan, about 2.5–3 min. The API is fetched at most every 600 s.
- **Real or false:** a transient false warn. It recurs for any first touch of a pre-activation dormant cluster (for example legacy-to-ETH migrations) whose API refresh lands in that window.
- **Could the same mechanism produce a false PAGE or hide one?** It produces neither:
  - **No false page:** `_finding` raises severity to page only in the `stale` branch (re-poison, unverified intra-block re-poison, poisoned and liquidatable) and the transient re-poison branch. All of them need a stored poisoning. The `unknown` branch stays warn (`m1_stale_tuple.py:579-650`).
  - **No hidden page:** `unknown` is set only when `latest is None` (no stored log). A poisoning is defined on a stored last log and is evaluated from the next run on, after the receipt-level validation of the touching block. The other inventory consumers (M3 `m3_liquidation_zone.py:348`, M6 `m6_legacy_coverage.py:294`, M2, M14) treat a non-validating tuple as `unknown` (kept, or health), not clean.
- **Owner:** later repair. Options: hold `unknown` for one run, or skip API rows whose state is newer than the evaluated block (for example, re-validate against storage at the API fetch head).

### S4 — NOTE: DT-1 trigger fired live: the 6 reverted `commitRoot` txs also matched both Oracle *Success* monitors
- **Reproducer:** `reconcile/.venv/bin/python -I $S/compare.py <repo>` prints `SSV Oracle Monitor (Success) expected 18 matched 24 … extra_are_failed_txs 6` (the same for the Discord monitor). `matched_on.transactions = [{"status": "Success"}]` for `0x29f6225b` and `0xa8a9fd23`.
- **Production effect:** each late vote sends a "success" message to Telegram (`telegram_oracle_success`) and to Discord info, alongside the correct failure messages. This is a changed or misleading text, not a missed or false page.
- **Owner:** Marco (DT-1, Rust). The trigger "an M11 info found mislabelled in the soak" fired, in the same class, for the oracle success monitors.

### S5 — NOTE: public-endpoint load (N7/O1 class), measured over 48 h
- **Measurements:**
  - Worker: 3,396 endpoint switches (2,990 of them at WARNING), 23 "endpoint changed while reading" re-reads, and 11 step failures with all 4 endpoints at 429. All 11 are M2 runs: `check_errors_total{m2}` went 0 → 11, and the maximum run of consecutive failing steps was 2, below the `rpc` health threshold of 3. Each failure was retried and completed.
  - Monitor: 249 lines with "429 Too Many Requests" (407 lines containing "429"; event 0094 says 336, counting method not stated). 28 recovery runs were skipped because `eth_blockNumber` returned 429, and the next minute covered them. 3 whole cycles failed in `get_blocks` and were repeated at last+1.
- **Why it is a NOTE:** no loss and no silence: every filter failure was recovered within 88 s.
- **Owner:** Marco (D22 intent: company RPC as production `MAINNET_RPC_URL`).

### S6 — NOTE: resources and retention (growth.log, T24–T48 hourly; no memory samples T0–T24)
- **Measurements** (`growth.log`, 24 samples):
  - Monitor RSS: 822.1–860.3 MiB, not monotonic, fitted slope +0.4 MiB/h. The second half drifts 836 → 849 MiB. No compose memory limit is set.
  - Worker RSS: 180.8–190.1 MiB, flat.
  - SQLite: 75,920 → 76,212 KiB (+12.7 KiB/h, about 0.3 MB/day). WAL flat at 5.2 MB.
  - `matches.jsonl`: 1,196 → 2,508 KiB (1.34 MB/day, unrotated). It is a dry-run recorder file only (S7).
  - Compose sets no logging limits; the Monitor log was 2.5 MB in 48 h. The inbox file is not rotated (445 B per failed tx, about 3 per day).
  - The missed-blocks store is bounded: 8 entries were pruned at about 24 h age, between 06:54:30Z and 08:08:30Z on 2026-10-25.
- **Why it is a NOTE:** slow growth, with no OOM or disk risk inside months. No data loss.
- **Owner:** DevOps (log rotation and memory limits in the K8s manifests, DT-5) and later repair (SQLite outbox/log retention). Re-measure the Monitor RSS over 7 days in production.

### S7 — NOTE: the dry-run match recorder interleaves records longer than 4 KiB under concurrent triggers (78 of 406 lines)
- **Reproducer:** `python3 -c "import json; [json.loads(l) for l in open('monitor-matches.jsonl')]"` fails on line 65 at char 4096. `$S/reassemble2.py` recovers all 406 records by re-splitting at 4096-byte boundaries: 29 groups of 2 interleaved records, 4 of 3 and 2 of 4.
- **Cause:** `scripts/local-dry-run.sh` `log_match.sh` (`echo "$input" | jq -c … >> matches.jsonl`) writes records of 6.5–10 KiB in 4 KiB chunks, and concurrent trigger processes interleave them.
- **Scope:** evidence only. Production triggers send to channels. The inbox bridge writes one 444-byte `printf` (`inbox_append.sh`, below PIPE_BUF), and all 7 inbox lines parse.
- **Owner:** later repair (coordinator: write each record with a single `write`, or one file per match).

### S8 — NOTE: what record mode did and did not exercise during the soak
- **Exercised live:**
  - Monitor: block processing, filter failure → missed → recovery (9 events / 35 blocks; 14 blocks needed a second attempt), pruning of the 8 pre-T0 failed entries at about 24 h with no duplicate announcement, and the `inbox_append` script trigger (6 lines).
  - Worker: inbox resolution and the canonical gate (held 0, orphaned 0, pending 0 in every snapshot) feeding M9; incident open, re-send on a new revision (`(2x)`), and resolve; daily digest ×2 (26,266,409 and 26,273,584, including M4's 24 h change line); outbox → record file.
- **Not exercised during T0–T48:**
  - No page and no page re-alert. No warn re-alert: no warn stayed open for 24 h, the longest was about 8.5 h at T48, so D22's "warn re-alert twice live" did not happen.
  - No health message of any kind, so `monitor-stall`/`skipped`/`unevaluated`, `rpc`, `lag` and `check-error` ran only in the pre-T0 QA controls.
  - No recovery give-up, no reorg (`reorg_pending {}`), no M13 detection, no channel HTTP send, retry or backoff (all `attempts 0`), and no Monitor Telegram/Discord trigger: the dry config replaces them with `local_log`.
  - The fresh-database backfill → live transition happened before T0, at 2026-10-24T01:32:32Z (`phase.since`, caught up at 26,259,174). See V4.4 DT-11.
- **Owner:** coordinator / Marco: the TEST_* channel phase of DT-6 should exercise send and retry, re-alert and health on real channels.

### S9 — NOTE: upstream mockall race in `tests/integration/bootstrap` (new observation, test harness only)
- **Reproducer:**

  ```text
  cargo test --locked --offline --test integration -- bootstrap      # 3 runs: 1, 3, 1 failures
  cargo test --locked --offline --test integration -- blockwatcher bootstrap --test-threads=1   # 53 passed, 0 failed, 1 ignored
  ```

  The first parallel run of `-- blockwatcher bootstrap` was 50 passed / 3 failed. The failures are in `test_create_trigger_handler*` with `MockTriggerExecutionService::execute … No matching expectation found`. Two tests share the static `MockTriggerExecutionService::new_context()` (`tests/integration/bootstrap/main.rs:215,447`).
- **Why it is a NOTE:** serial green. The test files are unchanged since upstream (`git log` shows only upstream commits `b091939`, `a0e2838`). Repair 2 changed `create_block_handler`, not `create_trigger_handler`. Same class as DT-14.
- **Not established:** whether it also reddens on `047e9ad`/`main`. I did not build the base (owner scope).
- **Owner:** later repair (extend DT-14).

### D1 — NOTE (delta, status wording): `checks.<id>.errors`/`last_error` are cumulative and never cleared
- **Reproducer:** `status-T0/T24/T48.json` all show `m4: errors 1, last_error "RpcError: … HTTP 429"`, from one failure before T0, although M4 ran cleanly through the soak (`eb_reconcile.py:630` copies `prev["last_error"]` forward on success).
- **Why it is a NOTE:** an operator reading `status` sees a stale error without a timestamp. No alert effect.
- **Owner:** later repair (add `last_error_at`, or clear it on success).

### D2 — NOTE (delta, runbook wording)
`deploy/README.md:140` says "a persisting failure is a `monitor-stall` page". It is a health message on the Telegram route (severity `health`), not a page. Owner: coordinator (records).

## 2. Claims I could NOT confirm

- **The Monitor image is built from `b681181`'s Rust code.** I did not rebuild or diff the image. I rely on QA attempt 2 ("images match the head") plus `git diff eeefc58 b681181 -- src reconcile deploy docker-compose*.yaml scripts` being empty, and the live config matching the templates (verified here). The worker image is verified directly (34/34 file hashes).
- **Event 0094's "Monitor 336 429 lines":** my counts are 249 lines with "429 Too Many Requests" and 407 lines containing "429". The method was not stated. NOTE-level only.
- **Event 0094's "no monotonic climb" for Monitor memory:** confirmed non-monotonic, but the T24–T48 fitted slope is +0.4 MiB/h (S6). There are no hourly memory samples T0–T24, only `mem-T24` and `mem-T48`.
- **Why the Blockscout fallback resolved none of the 4 unknown reverts** (S2; no Blockscout call under network scope).
- **Whether the S9 mockall race exists on the base** (not built).
- **Repair 2's "N2/N3/N7/N8 done" for repair QA 1 notes:** taken from event 0089 and QA attempt 2. Not re-measured here: no change since, and outside part B scope.
- **Every other claim of events 0093/0094 was reproduced from the evidence:** the counts above, the prune times, 0 sleep, 0 restarts, 37 = 37 outbox and record, the inbox states, and the second digest.

## 3. Verification matrix

| Row | Command / source | Exit | Result |
|---|---|---|---|
| Preflight | `git log -1 b681181`; `git diff b681181 HEAD -- . ':!plans'` empty; `git status --porcelain` empty; disk 291 GiB free; `uv venv reconcile/.venv --python 3.12` and `uv pip install --offline -r reconcile/requirements-dev.txt`; Python 3.12.15 | 0 | OK |
| V4.2 continuity | `$S/monparse.py` over `monitor-log-T0-T48.txt` (ANSI stripped) | 0 | 2,884 cycles; start = last+1 always (0 skips, 0 regressions); interval 28.6–91.2 s (0 > 240 s); max confirmed − last at cycle start 10; 26,261,328 → 26,275,703 |
| V4.2 chain pace | `eth_getBlockByNumber` 26,261,333 / 26,275,703 / 26,275,709 (Tenderly) | 0 | Timestamps 1792830803 (08:33:23Z T0) / 1793003723 (08:35:23Z T48) / 1793003795. Monitor read at 08:36:42Z, 6 behind head |
| V4.2 worker health | `status-T*.json`, `statusfile-T*.json`, 11 `metrics-*.txt` | — | `healthy: true`, `live`, lag 0/4/3, `error` null; `degraded()` empty (params ok, inbox ok, Monitor not stalled, no reorg pending); undelivered 0 / failing 0 / rpc_consecutive_failures ≤ 1 at every snapshot; open incidents m7 info 1 and m9 warn 1 throughout |
| V4.2 filter failures | `$S/monparse.py`, recovery latency script | 0 | 35 recorded = 35 recovered (sets equal), 0 duplicate, 0 given up, 21–88 s; missed store `[]` at T24/T48; worker `missed{failed}` 8 → 0 (pruned) and `unevaluated_total` 8 unchanged (pre-T0) |
| V4.2 Monitor vs chain | `$S/xcheck.py` (getLogs ≤ 5000-block chunks, batched `eth_getTransactionByHash`, oracle nonces) and `$S/compare.py`, `$S/compare2.py` | 0 | All exact (see Summary); oracle nonce deltas slot1 6/6/0, slot2 6/4/2, slot3 6/6/0, slot4 210/206/4 → 6 failed = 6 inbox |
| V4.2 Monitor vs worker | inbox file 7 lines (1 pre-T0) = DB `inbox` 7 rows (canonical 1, resolved 1) = 6 M9 warns + 1 pre-T0; Monitor `Cluster` 204 × `updateClusterBalance` (all from `0x16A5`) | — | Agree; Other Sender 0 = worker M11 quiet; held 0, orphaned 0 |
| V4.2 outbox | `export-T48.json` vs `alerts-record.jsonl` | — | 37 = 37 keys, same order; all `sent_at`, 0 attempts, `last_error` null; soak rows 23–37: 7 warn, 6 info (resolutions), 2 digests, 0 page, 0 health |
| V4.2 laptop | `pmset -g log` Sleep/Wake rows 2026-10-23..26 | 0 | Last event 2026-10-23 16:18 +0200 (before T0) |
| V4.2 containers | `docker inspect` | 0 | Monitor started 2026-10-24T08:22:55Z, worker 06:28:21Z, RestartCount 0 both |
| V4.2 image/config | `docker exec … sha256sum` vs worktree; `docker exec … tar` of Monitor config vs `deploy/config` | 0 | Worker 34/34 equal. Monitor monitors/networks/filters equal except triggers (dry-run) and `MAINNET_RPC_URL_2` (unset, dropped) |
| A10 warns | S1, S3 reproducers (public RPC) | 0 | Each warn explained on chain; 0 page |
| Resources | `growth.log`, `mem-T*`, `volumes-T*` | — | S6 |
| Δ suite | `reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider reconcile/tests` | 0 | **2313 passed** in 68.97 s |
| Δ ruff | `reconcile/.venv/bin/ruff check reconcile` | 0 | All checks passed |
| Δ templates | `bash deploy/check-templates.sh` | 0 | OK (32 monitors, 30 triggers, 1 network) |
| Δ abi | `bash deploy/check-abi.sh --config-dir deploy/config` | 0 | OK (32 monitors, 3356 entries) |
| Δ m15 | `bash deploy/check-m15-coverage.sh` | 0 | All M15 events present; no info event routed to Telegram |
| Δ release build | `cargo build --release --locked --offline` | 0 | `target/release/openzeppelin-monitor` |
| Δ selftest | `bash deploy/tests/selftest.sh` | 0 | **310 passed, 0 failed, 0 skipped** (with the binary; a first run without it was 292/0/11) |
| Δ compose | `docker compose -f docker-compose.yaml -f docker-compose.reconcile.yaml config` | 0 | Validates; nothing started; stderr empty |
| Δ cargo lib | `cargo test --locked --offline --lib -- bootstrap services::blockwatcher` | 0 | 120 passed, 950 filtered |
| Δ cargo integration | `cargo test --locked --offline --test integration -- blockwatcher bootstrap` | 101 / 0 | Parallel 50/3 (mockall, S9); serial `--test-threads=1` 53/0 (1 ignored) |
| V0.8 | `cargo test --locked --offline --test integration -- filters::evm::filter` (and `--test-threads=1`) | 0 / 0 | 18/0 parallel, 18/0 serial (DT-14 flake did not fire this time) |
| V0.3 | `git grep -nE "discord\.com/api/webhooks/[0-9]+/\|[0-9]{8,10}:AA[A-Za-z0-9_-]{30,}" b681181 -- . ':!tests/integration/fixtures'` | — | 2 rows, both the synthetic dummy at `reconcile/tests/test_framework.py:345,349` (part A N2 / DT-20); no new row |
| Keyed RPC | `git grep -nIE "<infura/alchemy/nodereal/quiknode/drpc dkey/ankr/tenderly key/blast/chainstack/getblock/api_key=…>" b681181` | — | 0 rows. The delta's URLs are only the D16/D20/D21 endpoints plus two synthetic `*.example` test fixtures (`selftest.sh` d20 keyedplain case, `test_monitor_unevaluated.py`) |
| Δ git log | `git log --stat --name-only 047e9ad..b681181` | 0 | 43 paths. No `.env*`, `config/`, `config.zip`, sqlite/wal/shm, `.local-dry/`, `soak*/` or `*.jsonl`. `deploy/config/networks/…` is the tracked template. Evidence under `plans/…/evidence/` is covered by the clean V0.3 and keyed scans |
| Δ cross-module | `alerts.health` keys `health:<kind>:<hash>` (`alerts.py:229`); kinds listed by grep | — | No collision. `monitor-stall/skipped/recovered/unevaluated` are documented in both READMEs. Worker `rpc_urls` (config.example) = README = D21; Monitor template = README = D20 (`_2` optional); D22 production text (README:36,140) consistent with `rpc_url_env` replacing the first URL. D2 wording NOTE |
| V3.7 tail (DT-12) | — | — | NOT RUN (owner scope; not in the part B packet) |
| V4.4 | §4 below | — | Drafted |

## 4. V4.4: completion statement (draft for the coordinator; nothing promoted)

> The SSV monitoring service on `plan/monitoring-service` (code `b681181`) is delivered as an integration branch for Marco to push and open a PR; neither has happened. CP0, CP1A, CP1B, CP2a–e and CP3 are VERIFIED by independent QA. CP4: part A PASS WITH NOTES (`047e9ad`, qa/cp4-attempt-1.md); soak 1 FAILED (event 0084; Monitor stall), repaired by D20/D21 (repair QA 2 PASS WITH NOTES); part B PASS WITH NOTES (`b681181`, soak 2 2026-10-24T08:34Z–2026-10-26T08:37Z, qa/cp4-attempt-2.md): A10 met — 0 pages, every warn explained on chain, worker healthy and live with lag < 50 at T0/T24/T48, the Monitor never stalled and missed 0 proxy transactions against an independent on-chain recount, every filter-failed block recovered, outbox fully delivered in record mode.
>
> **Remaining failures and open items (none promoted):**
> - **Known red / flaky:** Rust mockall races: V0.8 filter (DT-14; green twice here) and `tests/integration/bootstrap` (S9, new; serial green).
> - **V0.3 literal:** 2 dummy rows (part A N2, DT-20).
> - **NOT RUN / PARTIAL:**
>   - V3.7 computed-zero and liquidation tail (DT-12).
>   - Subgraph cross-check (DT-2).
>   - M1 fork PoC (DT-13).
>   - Dev-image jq (DT-4).
>   - Live channel sends, retries, re-alerts and health on real channels (S8, DT-6).
>   - Fresh-database go-live was observed on `ms-cp4r` before T0 (2026-10-24T01:32:32Z, caught up at 26,259,174) but not recorded as DT-11 evidence by QA.
> - **Accepted gaps:** D10 internal calls and same-block netting (DT-9); Rust `Success` label on reverts (DT-1, seen live: S4); pruned-RPC M13 fallback (DT-3).
> - **Not done, owned:**
>   - Production routes record-only (DT-6).
>   - K8s manifests incl. memory and log limits (DT-5, S6).
>   - Live `config/` render (DT-7).
>   - D14 switches on (DT-10).
>   - Company RPC as production `MAINNET_RPC_URL` (D22 intent, S5).
>   - Worktree cleanup (DT-16).
>   - M16, unattended recovery, upstream sync (DT-17).
> - **NOTE backlog:**
>   - Part A N1, N3 (DT-18), N4 (DT-19), N2, N5 (DT-20), N6.
>   - Repair QA 1: N1 = R5, N4 = R4, N5 = R3, N6 = O1. N2/N3/N7/N8 were reported fixed by repair 2; N2's fix is defeated by inode reuse (repair QA 2 N5).
>   - Repair QA 2 N1–N8.
>   - Soak and delta: S1 (M9 late-vote warn noise), S2 (revert decode at block−1), S3 (M1 API-ahead race), S4 (DT-1 live), S5 (public RPC load), S6 (retention, rotation, memory), S7 (dry-run recorder interleave), S8 (paths not exercised live), S9 (bootstrap mockall race), D1 (stale `last_error`), D2 (runbook "page" wording).
>   - Every §8 "later repair" row (DT-15).

### Open notes and residuals with owners

| ID | Item | Owner |
|---|---|---|
| A-N1 / A-N3 | M5 layout health not re-alerted; digest held while a check fails its silent run | later repair (DT-18) |
| A-N2 / A-N5 | V0.3 dummy rows; `docker-compose.watch.yaml` naming in §7/§8 | later repair (DT-20) |
| A-N4 | CP4-owned records (runbook reorg caveat, V0.4, F14/F15, counts, M4 exactness) | coordinator (DT-19) |
| A-N6 | CP2c-QA4-N5 wording | coordinator |
| R1-N1 = R5 | One fallback probe per failed request (Rust) | Marco |
| R1-N4 = R4 | Stall delivered between check runs; worker RPC outage also opens a stall | coordinator |
| R1-N5 = R3 | Operator `monitor.max_past_blocks` > template shortens ranges | Marco (runbook) |
| R1-N6 = O1 | Shared-IP rate driver | coordinator / Marco |
| R1-N2/N3/N7/N8 | Reported fixed by repair 2 (event 0089); N2's fix weakened by R2-N5 | coordinator (verify at closure) |
| R2-N1 | Wrong `missed_blocks_path` reads as clean | coordinator |
| R2-N2 | `Recovering` entry after a crash never retried (wording; optional Rust) | coordinator / Marco |
| R2-N3 | Duplicate delivery on save failure or panic | Marco (Rust, optional) |
| R2-N4 | Empty missed file after a crash mid-write drops the queue | coordinator |
| R2-N5 | Inode reuse defeats the reset identity | coordinator / Marco |
| R2-N6 | Recovery load, backlog and alert rate | coordinator |
| R2-N7 | Worker still reaches mevblocker | Marco |
| R2-N8 | QA Docker Hub pull (record) | coordinator |
| DT-18..20 | As in §9 | as in §9 |
| S1 | M9 late-vote `StaleBlockNumber` warn every round | Marco (spec) → later repair |
| S2 | Revert decode at block−1 misses same-block predecessors | later repair |
| S3 | M1 transient unknown, API newer than evaluated block | later repair |
| S4 | DT-1 seen live on the oracle success monitors | Marco |
| S5 | Public-endpoint load | Marco (D22) |
| S6 | Retention, rotation, memory limits | DevOps / later repair |
| S7 | Dry-run recorder interleave | later repair |
| S8 | Paths not exercised live in record mode | coordinator / Marco (DT-6 TEST_* phase) |
| S9 | Bootstrap mockall race | later repair (DT-14) |
| D1 | Cumulative `last_error` without time | later repair |
| D2 | Runbook "monitor-stall page" wording | coordinator |

### Plan §8/§9 rows to add or correct (coordinator)

- **DT-8** still describes soak 1 on `ms-cp3` (T0 2026-10-22T22:51Z, T48 due 2026-10-24T22:51Z). Replace it with soak 1 FAILED (event 0084) plus soak 2 on `ms-cp4r` (T0 2026-10-24T08:34Z, T48 2026-10-26T08:37Z, evidence `/Users/marco/orca/workspaces/ssv-network-monitor/ms-cp4-repair1/soak2/`, reviewed in this report: A10 met), and close or adjust the row.
- **DT-1:** record that the trigger fired (S4).
- **DT-3:** add the D22 owner intent: company RPC, non-archive, as production `MAINNET_RPC_URL`, plus a pre-deploy pruned-state probe that must error and never read zero.
- **DT-6:** the A10 part of the trigger is met with this report. The TEST_* phase should cover S8.
- **DT-11:** the `ms-cp4r` fresh-volume go-live (phase `since` 2026-10-24T01:32:32Z, `caught_up_block` 26,259,174, first outbox `digest:26259174`, `m7-surplus` info at 26,259,225) is candidate evidence. The coordinator records or verifies it.
- **DT-14:** extend to S9.
- **New §9 rows** (or §8 Recorded notes with a DT-15 pointer): R5, R4, R3, O1 (repair QA 1); repair QA 2 N1–N8; S1–S9; D1, D2.
- **§8 CP4:** the Recorded notes need the repair QA 1/2 notes and the soak notes. The CP4 Status gets PART B (this attempt).
- **§1 Models, §5 CP4 row and A12** still say Codex for the final QA. D13 supersedes this (record correction).
- **§7 V3.3 / §8 CP3** naming of `docker-compose.watch.yaml`: still open (DT-20).

## 5. Falsifiers and own scenarios

These checks were run by me, are read-only, and need no restoration.

- **Monitor-match completeness falsifier.** An exact set comparison, not a count, against the chain. A single silently unevaluated block holding a proxy tx would show up as `missing ≥ 1`; observed 0 across 302 direct txs. Control: the comparison is sensitive. It flagged the 6 extra Oracle Success matches (S4), so it does not answer "equal" by construction.
- **Failed-tx completeness without the Monitor.** Oracle nonce deltas minus successful txs gave 6, which equals the inbox's 6, independent of any Monitor output.
- **Recovery set equality.** `recorded − recovered = ∅` and `recovered − recorded = ∅` on block sets, not counts. Duplicate check: none.
- **Revert reason ground truth.** `eth_call` at N reverts `StaleBlockNumber` in 7/7 cases. At N−1 it succeeds exactly in the 5 same-block cases and reverts in the 2 previous-block cases, which matches the worker's 5 unresolved and 2 named rows one-to-one. This falsifies "the oracle actually failed for another reason".
- **M1 race ground truth.** Storage words at five blocks show the SSV word unchanged since activation and the ETH word appearing exactly at the migration block.
- **Evidence parse falsifier.** `json.loads` per line found the 78 interleaved lines (S7). Reassembly restored exactly 406 records from 406 lines (2-, 3- and 4-way groups), so no record is lost from the evidence.

Skipped under the owner scope: mutation campaigns, the implementers' falsifiers in bulk, V3.6–V3.9 fork scenarios, the V3.7 tail (DT-12), a fresh-database go-live, building the base for S9, and any Blockscout or SSV API call.

## 6. Evidence-plane writes

- This report: `plans/monitoring-service/qa/cp4-attempt-2.md`.
- plan.md CP4 `**Status:**` line: prefixed `PART B PASS WITH NOTES (code b681181; soak 2)`, rest kept.
- execution-log.md: one appended event.
- Local only: `reconcile/.venv` (gitignored) and `target/` (gitignored; release and test builds). Scratch under `$S`.
- **Deviation (record):** my public-RPC reads, about 30 getLogs and 330 batched tx reads between 09:10Z and 09:35Z, came from the same host IP as the still-running `ms-cp4r` stack, after T48. They may add 429 lines to its logs after 08:37Z. The soak evidence was captured at T48 and is unaffected.

## 7. Repo proof

Commands run before the commit of this report:

```text
$ git status --porcelain
 M plans/monitoring-service/execution-log.md
 M plans/monitoring-service/plan.md
?? plans/monitoring-service/qa/cp4-attempt-2.md
$ git diff --name-only b681181
plans/monitoring-service/execution-log.md
plans/monitoring-service/plan.md
plans/monitoring-service/state.md
```

`state.md` differs from `b681181` because of the coordinator's commits `9f523e4`..`4147788`, which were already on HEAD before this QA; I did not modify it. I touched no implementation, config, template, compose or generated file. `config/`, `config.zip`, `.env*` and `.local-dry/` were not touched in any checkout. The `ms-cp4r` stack was only read and `ms-cp3` was not touched. I sent no alert and made no mainnet transaction.
