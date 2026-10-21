#!/usr/bin/env bash
# Run the SSV monitors locally WITHOUT posting to the real Discord/Telegram channels.
#
# Renders the tracked templates (deploy/config, via deploy/render.sh) into ./.local-dry/config,
# swaps every notification trigger for a TEST_* channel or drops it, adds a log-only script
# trigger, and runs the native binary from ./.local-dry. Production secrets are never read:
# the only secret names this script lets the Monitor resolve are the TEST_* ones below.
# Every match is appended to ./.local-dry/data/matches.jsonl (one JSON object per line); the
# failed-tx inbox bridge writes ./.local-dry/data/inbox/failed-tx.jsonl.
#
# Optional test channels: set these (in the environment or in ./.env):
#   TEST_DISCORD_WEBHOOK=https://discord.com/api/webhooks/...
#   TEST_TELEGRAM_TOKEN=123456789:ABC...   TEST_TELEGRAM_CHAT_ID=123456789
#   TEST_RPC_URL=https://ethereum-rpc.publicnode.com   (replaces the network RPC endpoints)
# Without TEST_RPC_URL the rendered network reads MAINNET_RPC_URL (and MAINNET_RPC_URL_2).
#
# Usage (any openzeppelin-monitor flag is passed through):
#   scripts/local-dry-run.sh --prepare   # only generate ./.local-dry (for docker-compose.local.yaml)
#   scripts/local-dry-run.sh --check
#   scripts/local-dry-run.sh
#   scripts/local-dry-run.sh --monitor-path config/monitors/ssv_oracle_success_monitor.json \
#     --network evm_ethereum_mainnet --block 26105000
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
DRY="$ROOT/.local-dry"
BIN="$ROOT/target/release/openzeppelin-monitor"

PREPARE_ONLY=false
[ "${1:-}" = "--prepare" ] && { PREPARE_ONLY=true; shift; }

# Load ./.env without overriding variables already set in the environment.
if [ -f "$ROOT/.env" ]; then
  while IFS='=' read -r key value || [ -n "$key" ]; do
    case "$key" in '' | \#*) continue ;; esac
    value=${value%\"}; value=${value#\"}; value=${value%\'}; value=${value#\'}
    [ -n "${!key+x}" ] || export "$key=$value"
  done < "$ROOT/.env"
fi

$PREPARE_ONLY || [ -x "$BIN" ] || { echo "Build first: cargo build --release --locked" >&2; exit 1; }
command -v jq >/dev/null || { echo "jq is required (brew install jq)" >&2; exit 1; }

mkdir -p "$DRY/data" "$DRY/logs"
# Render without --check: the production secret names are not set here and the triggers using
# them are replaced below; `--check` against the final dry config is `scripts/local-dry-run.sh --check`.
"$ROOT/deploy/render.sh" --out "$DRY/config" --no-check >/dev/null

# Optional TEST_RPC_URL replaces the networks' RPC endpoints (e.g. when the production node is down).
if [ -n "${TEST_RPC_URL:-}" ]; then
  for f in "$DRY"/config/networks/*.json; do
    jq --arg url "$TEST_RPC_URL" \
      '.rpc_urls = [{"type_": "rpc", "url": {"type": "plain", "value": $url}, "weight": 100}]' \
      "$f" > "$f.tmp" && mv "$f.tmp" "$f"
  done
fi

# Keep only trigger definitions that are safe locally; the rendered ones refer to production
# secret names, so they are rewritten to TEST_* or removed.
#   discord  -> TEST_DISCORD_WEBHOOK (if set), else dropped
#   telegram -> TEST_TELEGRAM_TOKEN + TEST_TELEGRAM_CHAT_ID (if both set), else dropped
#   script   -> kept only for the local inbox bridge (inbox_append.sh); other scripts dropped
for f in "$DRY"/config/triggers/*.json; do
  type=$(jq -r 'to_entries[0].value.trigger_type' "$f")
  case "$type" in
    discord)
      if [ -n "${TEST_DISCORD_WEBHOOK:-}" ]; then
        jq 'map_values(.config.discord_url = {"type": "environment", "value": "TEST_DISCORD_WEBHOOK"})' \
          "$f" > "$f.tmp" && mv "$f.tmp" "$f"
      else rm -f "$f"; fi ;;
    telegram)
      if [ -n "${TEST_TELEGRAM_TOKEN:-}" ] && [ -n "${TEST_TELEGRAM_CHAT_ID:-}" ]; then
        jq --arg chat "$TEST_TELEGRAM_CHAT_ID" \
          'map_values(.config.token = {"type": "environment", "value": "TEST_TELEGRAM_TOKEN"} | .config.chat_id = $chat)' \
          "$f" > "$f.tmp" && mv "$f.tmp" "$f"
      else rm -f "$f"; fi ;;
    script)
      jq -e 'to_entries[0].value.config.script_path | endswith("/inbox_append.sh")' "$f" >/dev/null || rm -f "$f" ;;
    *) rm -f "$f" ;;
  esac
done

cat > "$DRY/config/triggers/local_log.json" <<'EOF'
{
  "local_log": {
    "name": "Local log only",
    "trigger_type": "script",
    "config": {
      "language": "Bash",
      "script_path": "./config/triggers/scripts/log_match.sh",
      "timeout_ms": 5000
    }
  }
}
EOF

cat > "$DRY/config/triggers/scripts/log_match.sh" <<'EOF'
#!/bin/sh
input=$(cat)
echo "$input" | jq -c '.monitor_match' >> ./data/matches.jsonl
EOF
chmod +x "$DRY/config/triggers/scripts/log_match.sh"

# Keep each monitor's triggers that were kept above, and always log locally.
available=$(jq -s -c 'map(keys[])' "$DRY"/config/triggers/*.json)
for f in "$DRY"/config/monitors/*.json; do
  jq --argjson avail "$available" \
    '.triggers = ([.triggers[] | select(. as $t | $avail | index($t))] + ["local_log"])' \
    "$f" > "$f.tmp" && mv "$f.tmp" "$f"
done

$PREPARE_ONLY && { echo "Prepared $DRY/config"; exit 0; }

cd "$DRY"
exec "$BIN" "$@"
