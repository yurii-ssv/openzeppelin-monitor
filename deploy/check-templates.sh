#!/usr/bin/env bash
# Read-only checker for the tracked Monitor templates under deploy/config. Writes nothing.
#
#   deploy/check-templates.sh [--config-dir <dir>] [--root <dir>]
#
# Pairs the "no plain secret" scan with required-field assertions, so a template that loses its
# credentials or chat id reference is red, not just a template that gains a plain value:
#   S1  every file under monitors/ triggers/ networks/ parses as JSON
#   S2  V0.6: no {"type":"plain"} value anywhere in triggers/ or networks/
#   S3  V0.3: no webhook URL or bot-token shaped string anywhere under deploy/ (secret scan)
#   S4  triggers: unique keys and names; per type the required credential / destination fields
#         telegram  token = {type:environment, value: NAME}; chat_id = "${TELEGRAM_CHAT_ID}"; message title+body
#         discord   discord_url = {type:environment, value: NAME}; message title+body; D11: a trigger
#                   whose key ends _success or _other_sender (info) uses DISCORD_WEBHOOK_INFO, every
#                   other Discord trigger (failed, dissent, safe, large transfer) its own variable
#         script    language, script_path to an existing file under triggers/scripts, timeout_ms > 0
#   S5  networks: every RPC is an environment reference; the first (weight 100) is MAINNET_RPC_URL;
#       max_past_blocks > 0; recovery_config complete
#   S6  monitors: unique names; every triggers[] entry resolves to a defined trigger key;
#       every network slug resolves; trigger_conditions scripts exist; addresses non-empty;
#       networks[] non-empty; match_conditions present with at least one non-empty
#       events/functions/transactions list (an empty set matches every transaction)
#   S7  deploy/check-abi.sh on the same config dir: every event/function signature and
#       expression parameter exists in the monitor's ABI, and every contract_spec event/function
#       equals the committed reference layout incl. `indexed` (the Monitor's own --check does neither)
# Inputs are validated first: a missing or empty --config-dir (no monitors, triggers or networks),
# a missing --root or deploy/ directory, a failing secret scan and a failing sibling script are all
# failures, never "OK". Counts come from the checked directory only.
# Exit 0 only if every assertion holds. Prints one FAIL line per violation.
set -uo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
CFG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --config-dir) [ $# -ge 2 ] || { echo "checker: --config-dir needs a directory" >&2; exit 2; }; CFG=$2; shift 2 ;;
    --root) [ $# -ge 2 ] || { echo "checker: --root needs a directory" >&2; exit 2; }; ROOT=$2; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
CFG=${CFG:-$ROOT/deploy/config}
command -v jq >/dev/null || { echo "jq is required" >&2; exit 2; }

errors=0
fail() { echo "FAIL: $*"; errors=$((errors + 1)); }

# S0 -- inputs exist and are not empty (before anything is counted or scanned).
[ -d "$ROOT" ] || { echo "FAIL: S0 --root $ROOT is not a directory"; echo "checker: 1 failure(s)"; exit 1; }
[ -d "$ROOT/deploy" ] || fail "S0 --root $ROOT has no deploy/ directory to scan"
[ -d "$CFG" ] || { fail "S0 --config-dir $CFG is not a directory"; echo "checker: $errors failure(s)"; exit 1; }
for d in monitors triggers networks; do
  [ -d "$CFG/$d" ] || { fail "S0 $CFG/$d is missing"; continue; }
  [ -n "$(find "$CFG/$d" -maxdepth 1 -name '*.json' -print -quit)" ] || fail "S0 $CFG/$d has no .json files"
done
[ $errors -eq 0 ] || { echo "checker: $errors failure(s)"; exit 1; }
emit() { # emit <prefix> <multi-line messages>: one FAIL per non-empty line
  local line
  while IFS= read -r line; do [ -z "$line" ] || fail "$1 $line"; done <<<"$2"
}
shopt -s nullglob

# S1 -- well-formed JSON.
for f in "$CFG"/monitors/*.json "$CFG"/triggers/*.json "$CFG"/networks/*.json; do
  jq -e . "$f" >/dev/null 2>&1 || fail "S1 not valid JSON: ${f#"$ROOT"/}"
done
[ $errors -eq 0 ] || { echo "checker: $errors failure(s)"; exit 1; }

# S2 -- V0.6 plain-value scan.
plain=$(jq -r '.. | objects | select(.type? == "plain") | .value' "$CFG"/triggers/*.json "$CFG"/networks/*.json) \
  || fail "S2 plain-value scan could not run (jq failed)"
[ -z "$plain" ] || fail "S2 plain value present in triggers/networks ($(printf '%s\n' "$plain" | wc -l | tr -d ' ') value(s); not printed)"

# S3 -- V0.3 secret-shaped strings anywhere under deploy/ (and the checked config dir). grep exit
# 0 = found, 1 = none, anything else = the scan itself failed, which is a failure, not a pass.
secret_re='discord\.com/api/webhooks/[0-9]+/|[0-9]{8,10}:AA[A-Za-z0-9_-]{30,}'
scan_dirs=("$ROOT/deploy")
case "$CFG/" in "$ROOT/deploy/"*) ;; *) scan_dirs+=("$CFG") ;; esac
secret_files=$(grep -rElI "$secret_re" "${scan_dirs[@]}" 2>&1); rc=$?
if [ $rc -eq 0 ]; then
  fail "S3 webhook- or token-shaped string (files: $(printf '%s\n' "$secret_files" | sed "s#$ROOT/##" | tr '\n' ' '))"
elif [ $rc -ne 1 ]; then
  fail "S3 secret scan failed (grep exit $rc): $secret_files"
fi

# S4 -- triggers.
trigger_keys=$(jq -r 'keys[]' "$CFG"/triggers/*.json | sort)
dup=$(printf '%s\n' "$trigger_keys" | uniq -d)
[ -z "$dup" ] || fail "S4 duplicate trigger key(s): $dup"
dupname=$(jq -r 'to_entries[].value.name' "$CFG"/triggers/*.json | sort | uniq -d)
[ -z "$dupname" ] || fail "S4 duplicate trigger name(s) (the Monitor refuses to load them): $dupname"

for f in "$CFG"/triggers/*.json; do
  rel=${f#"$ROOT"/}
  msgs=$(jq -r '
    def env_ref: (type == "object") and (.type == "environment") and ((.value | type) == "string") and (.value | test("^[A-Z][A-Z0-9_]*$"));
    def nonempty: (type == "string") and (length > 0);
    to_entries[] | .key as $k | .value as $t
    | (if ($t.name | nonempty | not) then "\($k): name missing" else empty end),
      (if ($t.trigger_type | IN("telegram", "discord", "script") | not) then "\($k): trigger_type \($t.trigger_type) not one of telegram|discord|script" else empty end),
      (if $t.trigger_type == "telegram" then
         (if ($t.config.token | env_ref | not) then "\($k): config.token must be {type:environment,value:NAME}" else empty end),
         (if $t.config.chat_id != "${TELEGRAM_CHAT_ID}" then "\($k): config.chat_id must be the ${TELEGRAM_CHAT_ID} placeholder" else empty end),
         (if ($t.config.message.title | nonempty | not) or ($t.config.message.body | nonempty | not) then "\($k): config.message.title/body missing" else empty end)
       elif $t.trigger_type == "discord" then
         (if ($t.config.discord_url | env_ref | not) then "\($k): config.discord_url must be {type:environment,value:NAME}" else empty end),
         (if ($k | test("_success$|_other_sender$")) and ($t.config.discord_url.value != "DISCORD_WEBHOOK_INFO") then "\($k): info trigger must use DISCORD_WEBHOOK_INFO (D11), not \($t.config.discord_url.value)" else empty end),
         (if ($k | test("_success$|_other_sender$") | not) and ($t.config.discord_url.value == "DISCORD_WEBHOOK_INFO") then "\($k): only info triggers (_success, _other_sender) may use DISCORD_WEBHOOK_INFO (D11)" else empty end),
         (if ($t.config.message.title | nonempty | not) or ($t.config.message.body | nonempty | not) then "\($k): config.message.title/body missing" else empty end)
       elif $t.trigger_type == "script" then
         (if ($t.config.language | nonempty | not) then "\($k): config.language missing" else empty end),
         (if ($t.config.script_path | nonempty | not) then "\($k): config.script_path missing" else empty end),
         (if (($t.config.timeout_ms // 0) <= 0) then "\($k): config.timeout_ms must be > 0" else empty end)
       else empty end)
  ' "$f")
  emit "S4 $rel" "$msgs"
done

# Script trigger files must exist under triggers/scripts (config paths are relative to the Monitor cwd).
for f in "$CFG"/triggers/*.json; do
  for p in $(jq -r 'to_entries[].value | select(.trigger_type == "script") | .config.script_path' "$f"); do
    [ -f "$CFG/triggers/scripts/$(basename "$p")" ] || fail "S4 ${f#"$ROOT"/}: script $p has no file under triggers/scripts"
  done
done

# S5 -- networks.
for f in "$CFG"/networks/*.json; do
  rel=${f#"$ROOT"/}
  msgs=$(jq -r '
    (if (.rpc_urls | length) == 0 then "no rpc_urls" else empty end),
    (.rpc_urls[]? | select(((.url.type == "environment") and (.url.value | test("^[A-Z][A-Z0-9_]*$"))) | not) | "rpc url is not an environment reference"),
    (if ([.rpc_urls[]? | select(.weight == 100 and .url.value == "MAINNET_RPC_URL")] | length) != 1 then "primary RPC (weight 100) must be MAINNET_RPC_URL" else empty end),
    (if ((.max_past_blocks // 0) <= 0) then "max_past_blocks must be > 0" else empty end),
    (if (.recovery_config | type) != "object" then "recovery_config missing"
     else (.recovery_config | (if .enabled != true then "recovery_config.enabled must be true" else empty end),
           (["cron_schedule", "max_blocks_per_run", "max_block_age", "max_retries", "retry_delay_ms"][] as $k | if has($k) | not then "recovery_config.\($k) missing" else empty end)) end)
  ' "$f")
  emit "S5 $rel" "$msgs"
done

# S6 -- monitors.
slugs=$(jq -r '.slug' "$CFG"/networks/*.json | sort -u)
dupmon=$(jq -r '.name' "$CFG"/monitors/*.json | sort | uniq -d)
[ -z "$dupmon" ] || fail "S6 duplicate monitor name(s): $dupmon"
for f in "$CFG"/monitors/*.json; do
  rel=${f#"$ROOT"/}
  [ "$(jq '.addresses | length' "$f")" -gt 0 ] || fail "S6 $rel has no addresses"
  [ "$(jq '(.networks // []) | length' "$f")" -gt 0 ] || fail "S6 $rel has no networks"
  jq -e '(.match_conditions | type) == "object"
         and ([.match_conditions.events, .match_conditions.functions, .match_conditions.transactions]
              | map(select(type == "array") | length) | add // 0) > 0' "$f" >/dev/null \
    || fail "S6 $rel has no match_conditions with a non-empty events/functions/transactions list"
  for t in $(jq -r '.triggers[]?' "$f"); do
    printf '%s\n' "$trigger_keys" | grep -qx "$t" || fail "S6 $rel references undefined trigger '$t'"
  done
  [ "$(jq '.triggers | length' "$f")" -gt 0 ] || fail "S6 $rel has no triggers"
  for n in $(jq -r '.networks[]?' "$f"); do
    printf '%s\n' "$slugs" | grep -qx "$n" || fail "S6 $rel references undefined network '$n'"
  done
  for p in $(jq -r '.trigger_conditions[]?.script_path' "$f"); do
    [ -f "$CFG/filters/$(basename "$p")" ] || fail "S6 $rel: trigger_conditions script $p has no file under filters"
  done
done

# S7 -- ABI membership (sibling script; its own failure lines are passed through).
abi_out=$("$(dirname "$0")/check-abi.sh" --config-dir "$CFG" 2>&1) || {
  printf '%s\n' "$abi_out" | sed 's/^/S7 /'
  fail "S7 deploy/check-abi.sh failed"
}

if [ $errors -ne 0 ]; then
  echo "checker: $errors failure(s)"
  exit 1
fi
count() { find "$CFG/$1" -maxdepth 1 -name '*.json' | wc -l | tr -d ' '; }
echo "checker: OK ($(count monitors) monitors, $(printf '%s\n' "$trigger_keys" | wc -l | tr -d ' ') triggers, $(count networks) network)"
