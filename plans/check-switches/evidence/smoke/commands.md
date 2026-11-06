# Smoke commands (check-switches delivery smoke, 2026-11-06)

Secrets by name only. Runner: scratchpad r.sh (logs command text, UTC start/exit).

- 2026-11-06T19:01:49Z start
```
docker ps -a --filter volume=ssv-network-monitor_watch-data --filter volume=ssv-network-monitor_monitor-data --format "{{.Names}} {{.Status}}"; docker ps -a --format "{{.Names}}" | wc -l
```
  - 2026-11-06T19:01:50Z exit 0

- 2026-11-06T19:01:50Z start
```
for v in ssv-network-monitor_watch-data ssv-network-monitor_monitor-data; do echo "## $v"; docker run --rm --network none -v $v:/src:ro python:3.12-slim sh -c "cd /src && find . -type f -print0 | sort -z | xargs -0 sha256sum; echo; find . | sort | xargs ls -ld --time-style=+%s"; done > /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/source-hashes-before.txt
```
  - 2026-11-06T19:01:54Z exit 0

- 2026-11-06T19:02:01Z start
```
docker volume inspect cs-smoke_watch-data cs-smoke_monitor-data >/dev/null 2>&1 && echo EXISTS || echo absent
```
  - 2026-11-06T19:02:01Z exit 0

- 2026-11-06T19:02:01Z start
```
docker volume create cs-smoke_watch-data && docker volume create cs-smoke_monitor-data
```
  - 2026-11-06T19:02:01Z exit 0

- 2026-11-06T19:02:01Z start
```
docker run --rm --network none -v ssv-network-monitor_watch-data:/src:ro -v cs-smoke_watch-data:/dst python:3.12-slim cp -a /src/. /dst/ && docker run --rm --network none -v ssv-network-monitor_monitor-data:/src:ro -v cs-smoke_monitor-data:/dst python:3.12-slim cp -a /src/. /dst/
```
  - 2026-11-06T19:02:01Z exit 0

- 2026-11-06T19:02:01Z start
```
for v in cs-smoke_watch-data cs-smoke_monitor-data; do echo "## $v"; docker run --rm --network none -v $v:/src:ro python:3.12-slim sh -c "cd /src && find . -type f -print0 | sort -z | xargs -0 sha256sum"; done > /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/copy-hashes.txt
```
  - 2026-11-06T19:02:02Z exit 0

- 2026-11-06T19:02:14Z start
```
docker image inspect cs-smoke-reconcile:333de80 >/dev/null 2>&1 && echo TAG_EXISTS || echo tag-absent
```
  - 2026-11-06T19:02:14Z exit 0

- 2026-11-06T19:02:14Z start
```
git -C /Users/marco/orca/workspaces/ssv-network-monitor/check-switches rev-parse HEAD; git -C /Users/marco/orca/workspaces/ssv-network-monitor/check-switches status --porcelain; git -C /Users/marco/orca/workspaces/ssv-network-monitor/check-switches diff --stat 333de80 HEAD -- reconcile
```
  - 2026-11-06T19:02:14Z exit 0

- 2026-11-06T19:02:14Z start
```
docker build -t cs-smoke-reconcile:333de80 /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/reconcile > /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/build.log 2>&1; tail -3 /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/build.log; docker image inspect cs-smoke-reconcile:333de80 --format "{{.Id}} {{.Created}}"
```
  - 2026-11-06T19:02:17Z exit 0

- 2026-11-06T19:02:28Z start
```
jq '.alerts.mode="record" | .alerts.record_path="/data/alerts-record.jsonl"' /Users/marco/ssv/repos/ssv-network-monitor/reconcile/config.json > /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/config-step1-upgrade.json
```
  - 2026-11-06T19:02:28Z exit 0

- 2026-11-06T19:02:28Z start
```
jq '.checks.m3.enabled=false' /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/config-step1-upgrade.json > /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/config-step2-m3-disabled.json
```
  - 2026-11-06T19:02:28Z exit 0

- 2026-11-06T19:02:28Z start
```
jq '.checks.m3.enabled=true' /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/config-step1-upgrade.json > /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/config-step3-m3-enabled.json
```
  - 2026-11-06T19:02:28Z exit 0

- 2026-11-06T19:02:41Z start
```
/private/tmp/claude-501/-Users-marco-orca-workspaces-ssv-network-monitor-check-switches/a95147ca-6341-451d-bef0-8d03d2b0ed2d/scratchpad/smoke/cli.sh /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/config-step1-upgrade.json status > /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/step0-status-pre.json 2> /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/step0-status-pre.stderr; /private/tmp/claude-501/-Users-marco-orca-workspaces-ssv-network-monitor-check-switches/a95147ca-6341-451d-bef0-8d03d2b0ed2d/scratchpad/smoke/cli.sh /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/config-step1-upgrade.json export > /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/step0-export-pre.json 2>> /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/step0-status-pre.stderr
```
  - 2026-11-06T19:02:42Z exit 0

- 2026-11-06T19:03:00Z start
```
/private/tmp/claude-501/-Users-marco-orca-workspaces-ssv-network-monitor-check-switches/a95147ca-6341-451d-bef0-8d03d2b0ed2d/scratchpad/smoke/start.sh /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/config-step1-upgrade.json
```
  - 2026-11-06T19:03:00Z exit 0

- 2026-11-06T19:32:32Z start
```
/private/tmp/claude-501/-Users-marco-orca-workspaces-ssv-network-monitor-check-switches/a95147ca-6341-451d-bef0-8d03d2b0ed2d/scratchpad/smoke/cli.sh /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/config-step1-upgrade.json status > /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/step1-status-live.json 2>/dev/null; /private/tmp/claude-501/-Users-marco-orca-workspaces-ssv-network-monitor-check-switches/a95147ca-6341-451d-bef0-8d03d2b0ed2d/scratchpad/smoke/meta.sh > /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/step1-meta-live.json; docker run --rm --network none -v cs-smoke_watch-data:/d:ro python:3.12-slim cat /d/alerts-record.jsonl > /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/step1-alerts-record.jsonl
```
  - 2026-11-06T19:32:33Z exit 0

- 2026-11-06T19:32:45Z start
```
/private/tmp/claude-501/-Users-marco-orca-workspaces-ssv-network-monitor-check-switches/a95147ca-6341-451d-bef0-8d03d2b0ed2d/scratchpad/smoke/cli.sh /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/config-step1-upgrade.json export > /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/step2-export-before.json 2>/dev/null; /private/tmp/claude-501/-Users-marco-orca-workspaces-ssv-network-monitor-check-switches/a95147ca-6341-451d-bef0-8d03d2b0ed2d/scratchpad/smoke/meta.sh > /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/step2-meta-before.json
```
  - 2026-11-06T19:32:45Z exit 0

- 2026-11-06T19:32:45Z start
```
docker stop -t 60 cs-smoke-worker && /private/tmp/claude-501/-Users-marco-orca-workspaces-ssv-network-monitor-check-switches/a95147ca-6341-451d-bef0-8d03d2b0ed2d/scratchpad/smoke/logs.sh > /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/step1-worker.log && docker inspect cs-smoke-worker --format '{{.State.ExitCode}} {{.State.StartedAt}} {{.State.FinishedAt}}' && docker rm cs-smoke-worker
```
  - 2026-11-06T19:32:45Z exit 0

- 2026-11-06T19:32:45Z start
```
docker run --rm --network none -v cs-smoke_watch-data:/d:ro python:3.12-slim wc -l /d/alerts-record.jsonl
```
  - 2026-11-06T19:32:45Z exit 0

- 2026-11-06T19:32:45Z start
```
/private/tmp/claude-501/-Users-marco-orca-workspaces-ssv-network-monitor-check-switches/a95147ca-6341-451d-bef0-8d03d2b0ed2d/scratchpad/smoke/start.sh /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/config-step2-m3-disabled.json
```
  - 2026-11-06T19:32:46Z exit 0

- 2026-11-06T19:40:24Z start
```
/private/tmp/claude-501/-Users-marco-orca-workspaces-ssv-network-monitor-check-switches/a95147ca-6341-451d-bef0-8d03d2b0ed2d/scratchpad/smoke/cli.sh /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/config-step2-m3-disabled.json status > /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/step2-status-after.json 2>/dev/null; /private/tmp/claude-501/-Users-marco-orca-workspaces-ssv-network-monitor-check-switches/a95147ca-6341-451d-bef0-8d03d2b0ed2d/scratchpad/smoke/cli.sh /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/config-step2-m3-disabled.json export > /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/step2-export-after.json 2>/dev/null; /private/tmp/claude-501/-Users-marco-orca-workspaces-ssv-network-monitor-check-switches/a95147ca-6341-451d-bef0-8d03d2b0ed2d/scratchpad/smoke/meta.sh > /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/step2-meta-after.json; docker run --rm --network none -v cs-smoke_watch-data:/d:ro python:3.12-slim cat /d/alerts-record.jsonl > /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/step2-alerts-record.jsonl
```
  - 2026-11-06T19:40:25Z exit 0

- 2026-11-06T19:40:32Z start
```
docker stop -t 60 cs-smoke-worker && /private/tmp/claude-501/-Users-marco-orca-workspaces-ssv-network-monitor-check-switches/a95147ca-6341-451d-bef0-8d03d2b0ed2d/scratchpad/smoke/logs.sh > /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/step2-worker.log && docker inspect cs-smoke-worker --format '{{.State.ExitCode}} {{.State.StartedAt}} {{.State.FinishedAt}}' && docker rm cs-smoke-worker
```
  - 2026-11-06T19:40:33Z exit 0

- 2026-11-06T19:40:33Z start
```
/private/tmp/claude-501/-Users-marco-orca-workspaces-ssv-network-monitor-check-switches/a95147ca-6341-451d-bef0-8d03d2b0ed2d/scratchpad/smoke/start.sh /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/config-step3-m3-enabled.json
```
  - 2026-11-06T19:40:33Z exit 0

- 2026-11-06T19:41:14Z start
```
/private/tmp/claude-501/-Users-marco-orca-workspaces-ssv-network-monitor-check-switches/a95147ca-6341-451d-bef0-8d03d2b0ed2d/scratchpad/smoke/cli.sh /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/config-step3-m3-enabled.json status > /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/step3-status-start.json 2>/dev/null; /private/tmp/claude-501/-Users-marco-orca-workspaces-ssv-network-monitor-check-switches/a95147ca-6341-451d-bef0-8d03d2b0ed2d/scratchpad/smoke/meta.sh > /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/step3-meta-start.json
```
  - 2026-11-06T19:41:15Z exit 0

- 2026-11-06T19:56:42Z start
```
/private/tmp/claude-501/-Users-marco-orca-workspaces-ssv-network-monitor-check-switches/a95147ca-6341-451d-bef0-8d03d2b0ed2d/scratchpad/smoke/cli.sh /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/config-step3-m3-enabled.json status > /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/step3-status-after.json 2>/dev/null; /private/tmp/claude-501/-Users-marco-orca-workspaces-ssv-network-monitor-check-switches/a95147ca-6341-451d-bef0-8d03d2b0ed2d/scratchpad/smoke/cli.sh /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/config-step3-m3-enabled.json export > /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/step3-export-after.json 2>/dev/null; /private/tmp/claude-501/-Users-marco-orca-workspaces-ssv-network-monitor-check-switches/a95147ca-6341-451d-bef0-8d03d2b0ed2d/scratchpad/smoke/meta.sh > /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/step3-meta-after.json; docker run --rm --network none -v cs-smoke_watch-data:/d:ro python:3.12-slim cat /d/alerts-record.jsonl > /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/step3-alerts-record.jsonl
```
  - 2026-11-06T19:56:42Z exit 0

- 2026-11-06T19:56:42Z start
```
docker stop -t 60 cs-smoke-worker && /private/tmp/claude-501/-Users-marco-orca-workspaces-ssv-network-monitor-check-switches/a95147ca-6341-451d-bef0-8d03d2b0ed2d/scratchpad/smoke/logs.sh > /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/step3-worker.log && docker inspect cs-smoke-worker --format '{{.State.ExitCode}} {{.State.StartedAt}} {{.State.FinishedAt}}' && docker rm cs-smoke-worker
```
  - 2026-11-06T19:56:44Z exit 0

- 2026-11-06T19:56:51Z start
```
docker ps -a --format "{{.Names}}" | wc -l
```
  - 2026-11-06T19:56:51Z exit 0

- 2026-11-06T19:56:51Z start
```
for v in ssv-network-monitor_watch-data ssv-network-monitor_monitor-data; do echo "## $v"; docker run --rm --network none -v $v:/src:ro python:3.12-slim sh -c "cd /src && find . -type f -print0 | sort -z | xargs -0 sha256sum; echo; find . | sort | xargs ls -ld --time-style=+%s"; done > /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/source-hashes-after.txt
```
  - 2026-11-06T19:56:51Z exit 0

- 2026-11-06T19:56:51Z start
```
diff /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/source-hashes-before.txt /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/source-hashes-after.txt > /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/source-hashes-diff.txt; echo diff-exit $?; wc -c < /Users/marco/orca/workspaces/ssv-network-monitor/check-switches/plans/check-switches/evidence/smoke/source-hashes-diff.txt
```
  - 2026-11-06T19:56:51Z exit 0

## Not logged through the runner (read-only observation only)

Between the logged commands, read-only polling/inspection ran directly (all `--network none`, volumes `:ro` or the
sqlite `mode=ro` URI on the `cs-smoke_watch-data` copy; nothing touched a source volume):
- status-file polls: `docker run --rm --network none -v cs-smoke_watch-data:/d:ro python:3.12-slim python -c "<read /d/eb-reconcile.status.json>"`
- meta/run/incident queries: scratchpad `meta.sh`, `m3state.sh` (sqlite `file:/d/eb-reconcile.sqlite?mode=ro`)
- redacted logs: scratchpad `logs.sh` (= `docker logs cs-smoke-worker` piped through sed replacing the MAINNET_RPC_URL host/path with `[REDACTED]`)
- `docker stats --no-stream cs-smoke-worker`, `docker volume ls`, `docker images`
- one host-side reachability probe of the internal RPC: `curl -m 15 ... eth_blockNumber "$MAINNET_RPC_URL"` (value from .env by name; output HTTP code + time only)
- redaction greps over this directory (report.md, section Redaction)
Helper scripts (scratchpad smoke/): r.sh (runner), cli.sh (read-only status/export, refuses non-record config),
start.sh (refuses a config that is not record mode with record_path /data/alerts-record.jsonl; passes only -e MAINNET_RPC_URL).
