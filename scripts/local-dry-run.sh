#!/usr/bin/env bash
# Run the SSV monitors locally WITHOUT posting to the real Discord/Telegram channels.
#
# Copies ./config into ./.local-dry/config, points every monitor at a single
# log-only script trigger, and runs the native binary from ./.local-dry.
# Every match is appended to ./.local-dry/data/matches.jsonl (one JSON object per line).
#
# Optional test channels: set these (in the environment or in ./.env) to send the
# real alert templates to your own Discord webhook / Telegram chat instead of the
# production ones.
#   TEST_DISCORD_WEBHOOK=https://discord.com/api/webhooks/...
#   TEST_TELEGRAM_TOKEN=123456:ABC...   TEST_TELEGRAM_CHAT_ID=123456789
#   TEST_RPC_URL=https://ethereum-rpc.publicnode.com   (replaces the network RPC endpoints)
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

rm -rf "$DRY/config"
mkdir -p "$DRY/config/triggers/scripts" "$DRY/data" "$DRY/logs"
cp -R "$ROOT/config/networks" "$ROOT/config/monitors" "$ROOT/config/filters" "$DRY/config/"

# Optional TEST_RPC_URL replaces the networks' RPC endpoints (e.g. when the production node is down).
if [ -n "${TEST_RPC_URL:-}" ]; then
  for f in "$DRY"/config/networks/*.json; do
    jq --arg url "$TEST_RPC_URL" \
      '.rpc_urls = [{"type_": "rpc", "url": {"type": "plain", "value": $url}, "weight": 100}]' \
      "$f" > "$f.tmp" && mv "$f.tmp" "$f"
  done
fi

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

# Copy production triggers with their secrets swapped for the TEST_* env vars.
# Only trigger types with test credentials set are copied; the rest are dropped.
for f in "$ROOT"/config/triggers/*.json; do
  type=$(jq -r 'to_entries[0].value.trigger_type' "$f")
  if [ "$type" = discord ] && [ -n "${TEST_DISCORD_WEBHOOK:-}" ]; then
    jq 'map_values(.config.discord_url = {"type": "environment", "value": "TEST_DISCORD_WEBHOOK"})' \
      "$f" > "$DRY/config/triggers/$(basename "$f")"
  elif [ "$type" = telegram ] && [ -n "${TEST_TELEGRAM_TOKEN:-}" ] && [ -n "${TEST_TELEGRAM_CHAT_ID:-}" ]; then
    jq --arg chat "$TEST_TELEGRAM_CHAT_ID" \
      'map_values(.config.token = {"type": "environment", "value": "TEST_TELEGRAM_TOKEN"} | .config.chat_id = $chat)' \
      "$f" > "$DRY/config/triggers/$(basename "$f")"
  fi
done

# Keep each monitor's triggers that were copied above, and always log locally.
available=$(jq -s -c 'map(keys[]) ' "$DRY"/config/triggers/*.json)
for f in "$DRY"/config/monitors/*.json; do
  jq --argjson avail "$available" \
    '.triggers = ([.triggers[] | select(. as $t | $avail | index($t))] + ["local_log"])' \
    "$f" > "$f.tmp" && mv "$f.tmp" "$f"
done

$PREPARE_ONLY && { echo "Prepared $DRY/config"; exit 0; }

cd "$DRY"
exec "$BIN" "$@"
