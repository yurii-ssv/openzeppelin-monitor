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
# Without TEST_RPC_URL the rendered network keeps the template rotation (D20): MAINNET_RPC_URL, the keyless
# public literals https://rpc.mevblocker.io and https://rpc-eth.blockmachine.io, and MAINNET_RPC_URL_2 when set.
#
# Fail-closed (CP1A repair 4): every rewrite below goes through rewrite(), which aborts the script when jq
# fails (`jq ... && mv ...` does not trip `set -e`, so a failed rewrite used to leave the production
# trigger or RPC reference in place); every trigger of a file is rewritten or dropped, not just the first;
# and after the rewrites the script refuses to run unless no trigger refers to an environment variable
# outside TEST_* (the "production secrets are never read" promise, checked rather than assumed).
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
{ command -v jq >/dev/null && [ "$(jq -n '1 + 1' 2>/dev/null)" = 2 ]; } || { echo "jq is required (brew install jq; not on PATH, or not working)" >&2; exit 1; }

rewrite() { # rewrite <file> <jq args...>: in-place jq edit; a failing jq aborts the script
  local f=$1; shift
  if ! jq "$@" "$f" > "$f.tmp"; then rm -f "$f.tmp"; echo "local-dry-run: jq failed rewriting ${f#"$DRY"/}" >&2; exit 1; fi
  mv "$f.tmp" "$f"
}

mkdir -p "$DRY/data" "$DRY/logs"
# Render without --check: the production secret names are not set here and the triggers using
# them are replaced below; `--check` against the final dry config is `scripts/local-dry-run.sh --check`.
"$ROOT/deploy/render.sh" --out "$DRY/config" --no-check >/dev/null

# Optional TEST_RPC_URL replaces the networks' RPC endpoints (e.g. when the production node is down).
if [ -n "${TEST_RPC_URL:-}" ]; then
  for f in "$DRY"/config/networks/*.json; do
    rewrite "$f" --arg url "$TEST_RPC_URL" \
      '.rpc_urls = [{"type_": "rpc", "url": {"type": "plain", "value": $url}, "weight": 100}]'
  done
fi

# Keep only trigger definitions that are safe locally; the rendered ones refer to production
# secret names, so they are rewritten to TEST_* or removed. Decided per trigger (a file may hold
# several), by this one filter:
#   discord  -> TEST_DISCORD_WEBHOOK (if set), else dropped
#   telegram -> TEST_TELEGRAM_TOKEN + TEST_TELEGRAM_CHAT_ID (if both set), else dropped
#   script   -> kept only for the local inbox bridge (inbox_append.sh); other scripts dropped
#   anything else (or a malformed entry) -> dropped
# A file left with no trigger is deleted.
for f in "$DRY"/config/triggers/*.json; do
  rewrite "$f" --arg dh "${TEST_DISCORD_WEBHOOK:+yes}" --arg tt "${TEST_TELEGRAM_TOKEN:+yes}" --arg chat "${TEST_TELEGRAM_CHAT_ID:-}" '
    if type != "object" then error("a trigger file must be an object, got \(type)") else . end
    | with_entries(
        select(.value | type == "object" and (.config | type) == "object")
        | .value as $t
        | if $t.trigger_type == "discord" then
            if $dh == "yes" then .value.config.discord_url = {"type": "environment", "value": "TEST_DISCORD_WEBHOOK"} else empty end
          elif $t.trigger_type == "telegram" then
            if $tt == "yes" and $chat != "" then .value.config.token = {"type": "environment", "value": "TEST_TELEGRAM_TOKEN"} | .value.config.chat_id = $chat else empty end
          elif $t.trigger_type == "script" then
            if ($t.config.script_path | type == "string" and endswith("/inbox_append.sh")) then . else empty end
          else empty end)'
  [ "$(jq 'length' "$f")" != 0 ] || rm -f "$f"
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
available=$(jq -s -c 'map(keys[])' "$DRY"/config/triggers/*.json) \
  || { echo "local-dry-run: jq failed listing the kept triggers" >&2; exit 1; }
for f in "$DRY"/config/monitors/*.json; do
  rewrite "$f" --argjson avail "$available" \
    '.triggers = ([.triggers[] | select(. as $t | $avail | index($t))] + ["local_log"])'
done

# Post-condition: no trigger may still read a production secret.
foreign=$(jq -r '.. | objects | select(.type? == "environment") | .value | select(startswith("TEST_") | not)' "$DRY"/config/triggers/*.json) \
  || { echo "local-dry-run: jq failed auditing the rewritten triggers" >&2; exit 1; }
[ -z "$foreign" ] || { echo "local-dry-run: refusing to run: rewritten triggers still reference non-TEST variables: $(printf '%s\n' "$foreign" | sort -u | tr '\n' ' ')" >&2; exit 1; }

$PREPARE_ONLY && { echo "Prepared $DRY/config"; exit 0; }

cd "$DRY"
exec "$BIN" "$@"
