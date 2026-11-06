# Delivery smoke — check-switches (record mode, 2026-11-06)

Go: Marco 2026-11-06, source volume `ssv-network-monitor_watch-data`. Run by a coordinator subagent (Opus 5.5, medium); raw results, no verdict on the plan. Saved verbatim by the coordinator (the subagent's harness refused report-file writes); coordinator spot-checks at the end.

## Identity
- Worktree HEAD `680a9de9be8505b65634c8ac74e0702f2ed8b953`; `git diff --stat 333de80 HEAD -- reconcile` is empty, so the image runs merge `333de80`.
- Image `cs-smoke-reconcile:333de80` = `sha256:0d237749a673078d92bf2c8805b5349035ab611e123f9d15aeb7b191925f403e` (built 2026-11-06T19:02:17Z, build.log). The tag did not exist before; `ssv-eb-reconcile:local` (`3d5332b473aa`) was not touched.
- Copies `cs-smoke_watch-data` and `cs-smoke_monitor-data` (`cp -a` from `:ro` mounts); their per-file sha256 matches the sources (copy-hashes.txt, `COPY_IDENTICAL`).
- Configs `config-step{1,2,3}-*.json`: they differ from the live `reconcile/config.json` only in `alerts.mode: "record"`, `alerts.record_path: "/data/alerts-record.jsonl"`, and (steps 2/3) `checks.m3.enabled` false/true. Both alert values were re-checked with `jq` before every start. Only `-e MAINNET_RPC_URL` was passed (by name).
- **`backfill.enabled` = true** (plan step 1 record). DB phase `live` (caught_up_block 26280726, history_block 26280683). Copied checkpoint 26304766 (2026-10-30 09:49Z), about 53,000 blocks behind the head.

## Results
| Step | Expected | Observed | Result | Evidence |
|---|---|---|---|---|
| 1 Upgrade | No catch-up record, no `disabled`, no switch-caused message (D10) | Live at cp 26357909, lag 0: `disabled: []`, no `checks.*.catchup`. Meta has no `catchup:*`, `disabled:*` or `silent:*` row. m3 ran with reasons `events, interval`. 8 record lines, 0 switch-caused | PASS | step1-status-live.json, step1-meta-live.json, step1-alerts-record.jsonl, step1-worker.log, step0-status-pre.json |
| 2 Disable m3 | Open m3 incidents → `disabled`, no message, `status.disabled == ["m3"]`, meta marker | m3 had 0 incidents before the step; log `check m3 disabled: 0 open incident(s) closed as disabled at block 26357909`. `status.disabled == ["m3"]`, m3 not built (last run stays 26357889). `disabled:m3 = {block 26357909, closed 0}`. 0 new record lines over 3 live steps | PASS (closure of real rows not exercised: N = 0) | step2-export-before/after.json, step2-meta-before/after.json, step2-status-after.json, step2-alerts-record.jsonl, step2-worker.log |
| 3 Re-enable m3 | `catchup` in status, no message at start, then at most still-true conditions once; record removed after first live | Start: `catchup = {pending, history_block null, cause reenabled}`, `disabled:m3` cleared. Pinned at 26357971 → silent run (`backfill, force, interval`, 0 findings, `silent:m3` = 26357971, `first_live`) → live run at 26358010 (`force, live`, 0 findings) → `catchup:m3` deleted. 0 new record lines over the step plus 2 live steps | PASS (send-once met trivially: 0 findings) | step3-status-start.json, step3-meta-start.json, step3-m3-timeline.txt, step3-status-after.json, step3-meta-after.json, step3-export-after.json, step3-alerts-record.jsonl, step3-worker.log |

## Record lines (8 in total, all written in step 1)
The record file is cumulative: `step{1,2,3}-alerts-record.jsonl` are byte-identical snapshots.

1 `health:monitor-stall`: health noise, the Monitor is not running · 2 `m9-revert:0x28bea5b-26304256:resolved`: normal live · 3 `m12-16a503c…-26357763:open`: normal live · 4 `m12-…-26301910:resolved`: normal live · 5–6 `health:m13-gap` ×2 (256 of 52997 blocks read): health noise from the copy's gap · 7 `health:lag` (123 behind): health noise from the gap · 8 `digest:26357763`: normal live.

Counts per step (switch-caused / normal live / health): step 1 0 / 4 / 4; step 2 0 / 0 / 0; step 3 0 / 0 / 0. Nothing was sent to Telegram or Discord.

## Source hashes
source-hashes-before.txt vs source-hashes-after.txt (sha256 plus mtimes, both volumes): diff empty (source-hashes-diff.txt, 0 bytes).

## Durations (UTC)
19:01:49 → about 19:58.
- **Step 1:** 19:03:00–19:32:45. The first step alone took about 24.5 minutes for the 53k-block gap (m12 about 16 min); live from 19:30.
- **Step 2:** 19:32:46–19:40:33.
- **Step 3:** 19:40:33–19:56:44, including a 4.5-minute topics backfill for m3's event topic from block 24920727 (D7, by design) and a 6-minute first step running every interval check.

## Observations and deviations
- **Internal RPC:** not an archive node and flaky. Step 1 had 20 "historical state not available" errors and 6 URLErrors, all handled by the worker's own failover to the public `rpc_urls` (with HTTP 429s). Step 2 had 0 failovers, step 3 had 8. No step failed.
- **Inbox re-read:** at the first start the log said "shrank or was replaced (size 2225 < offset 2225)". The copy changed the inode, so the worker re-read the inbox from 0, with no record line. The "shrank" wording is cosmetic.
- **Carried-over status:** `run:m3.errors = 4` and `last_error` come from the 2026-10-30 live run. `healthy: false` in step 1 came from the Monitor stall.
- **Zero-row limitation:** the step 2 closure and step 3 send-once paths ran with zero m3 rows and findings (covered by the unit/QA suites, not by this smoke).
- **Image pull:** `python:3.12-slim` was pulled (helper containers; it is also the worker's base image).
- **Commands log:** read-only observation commands are listed at the end of commands.md rather than logged through the runner.

## Cleanup
The container `cs-smoke-worker` was stopped (`-t 60`, exit 0) and removed after each step. Volumes `cs-smoke_watch-data`, `cs-smoke_monitor-data` and the image are kept until Marco decides.

## Redaction
- RPC host/path grep: 0 files. Token/webhook regex: only false positives (`<block>:<txhash>` incident keys). `token`/`webhook` words: only env-var names in the configs and the `ssv_token` contract address in the exports. No secrets.

## Coordinator spot-checks (2026-11-06)
- `diff source-hashes-before.txt source-hashes-after.txt`: identical; `source-hashes-diff.txt` 0 bytes.
- `status.disabled` / `checks.m3.catchup`: step1 `[]`/none; step2 `["m3"]`/none; step3 start `[]`/`{pending, null, reenabled}`; step3 after `[]`/none.
- Configs: all three `alerts.mode == "record"`, `record_path` `/data/alerts-record.jsonl`; `checks.m3.enabled` null/false/true.
- Record files: 8 lines each, step1 and step3 byte-identical.
- RPC hostname grep over the evidence directory (hostname read from `.env` in a subshell, not printed): 0 files. No `cs-smoke-*` container remains.
