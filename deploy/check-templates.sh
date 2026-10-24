#!/usr/bin/env bash
# Read-only checker for the tracked Monitor templates under deploy/config. Writes nothing.
#
#   deploy/check-templates.sh [--config-dir <dir>] [--root <dir>]
#
# Pairs the "no plain secret" scan with required-field assertions, so a template that loses its
# credentials or chat id reference is red, not just a template that gains a plain value:
#   S1  every file under monitors/ triggers/ networks/ parses as JSON
#   S2  V0.6: no {"type":"plain"} value anywhere in triggers/ or networks/, except a network RPC URL that is
#       one of the keyless public endpoints in PUBLIC_RPC_URLS (D20: no API key, so not a secret; a literal
#       cannot be unset, so it can never stop the Monitor from loading)
#   S3  V0.3: no webhook URL or bot-token shaped string anywhere under deploy/ (secret scan)
#   S4  triggers: unique keys and names; per type the required credential / destination fields
#         telegram  token = {type:environment, value: NAME}; chat_id = "${TELEGRAM_CHAT_ID}"; message title+body
#         discord   discord_url = {type:environment, value: NAME}; message title+body; D11: a trigger
#                   whose key ends _success or _other_sender (info) uses DISCORD_WEBHOOK_INFO, every
#                   other Discord trigger (failed, dissent, safe, large transfer) its own variable
#         script    language, script_path to an existing file under triggers/scripts, timeout_ms > 0
#   S5  networks: every RPC is an environment reference or an allow-listed public URL; the first (weight 100)
#       is MAINNET_RPC_URL; weights unique (the Monitor rotates in weight order); D20: at least two
#       non-Tenderly public URLs and no two possibly-Tenderly endpoints (an environment reference or a
#       Tenderly host) next to each other in the rotation cycle; cron_schedule is a 6-field cron that fires
#       at most once a minute (one fixed second); 0 < max_past_blocks <= 100 (a cycle fetches up to
#       max_past_blocks + 1 full blocks concurrently); recovery_config complete, its cron on another second
#   S6  monitors: unique names; every triggers[] entry resolves to a defined trigger key;
#       every network slug resolves; trigger_conditions scripts exist; addresses non-empty;
#       networks[] non-empty; match_conditions present with at least one non-empty
#       events/functions/transactions list (an empty set matches every transaction)
#   S7  deploy/check-abi.sh on the same config dir: every event/function signature and
#       expression parameter exists in the monitor's ABI, and every contract_spec event/function
#       equals the committed reference layout incl. `indexed` (the Monitor's own --check does neither)
#   S1b the type-level shape of every file (deploy/shape.jq): a trigger whose config is a string, a
#       monitor whose events are an object, a file that is a bare number, ... is a FAIL line, not a jq error
# Inputs are validated first: a missing or empty --config-dir (no monitors, triggers or networks),
# a missing --root or deploy/ directory, an unreadable, empty or looping file, a stray non-JSON file in
# monitors/ triggers/ networks/, a failing secret scan and a failing sibling script are all
# failures, never "OK". Every jq call goes through jqo(): a jq that fails (runtime error, bad input)
# is a FAIL of its own and its empty output is never read as "no violations". A missing or
# non-working jq exits 2 with "jq is required". Counts come from the checked directory only.
# Exit 0 only if every assertion holds. Prints one FAIL line per violation.
set -uo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
CFG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --config-dir) [ $# -ge 2 ] || { echo "checker: --config-dir needs a directory" >&2; exit 2; }; CFG=$2; shift 2 ;;
    --root) [ $# -ge 2 ] || { echo "checker: --root needs a directory" >&2; exit 2; }; ROOT=$2; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
CFG=${CFG:-$ROOT/deploy/config}
# A jq that is absent or does not work would turn every scan below into an empty result.
{ command -v jq >/dev/null && [ "$(jq -n '1 + 1' 2>/dev/null)" = 2 ]; } || { echo "checker: jq is required (not on PATH, or not working)" >&2; exit 2; }
JQ_ERR=$(mktemp) || { echo "checker: cannot create a temporary file" >&2; exit 2; }
trap 'rm -f "$JQ_ERR"' EXIT

# Keyless public mainnet RPCs (D16/D20) a network template may name literally. Anything else, a keyed URL
# included, must stay an environment reference.
PUBLIC_RPC_URLS='["https://rpc.mevblocker.io","https://rpc-eth.blockmachine.io","https://mainnet.gateway.tenderly.co","https://gateway.tenderly.co/public/mainnet"]'

errors=0
fail() { echo "FAIL: $*"; errors=$((errors + 1)); }
JQ_OUT=""
jqo() { # jqo <label> <jq args...>: stdout -> JQ_OUT. A failing jq is a FAIL (and returns 1), never an empty result.
  local label=$1 rc; shift
  JQ_OUT=$(jq "$@" 2>"$JQ_ERR"); rc=$?
  if [ $rc -ne 0 ]; then
    JQ_OUT=""; fail "$label: jq failed (exit $rc): $(head -c 300 "$JQ_ERR" | tr '\n' ' ')"; return 1
  fi
}

# S0 -- inputs exist and are not empty (before anything is counted or scanned).
[ -d "$ROOT" ] || { echo "FAIL: S0 --root $ROOT is not a directory"; echo "checker: 1 failure(s)"; exit 1; }
[ -d "$ROOT/deploy" ] || fail "S0 --root $ROOT has no deploy/ directory to scan"
[ -d "$CFG" ] || { fail "S0 --config-dir $CFG is not a directory"; echo "checker: $errors failure(s)"; exit 1; }
for d in monitors triggers networks; do
  [ -d "$CFG/$d" ] || { fail "S0 $CFG/$d is missing"; continue; }
  listing=$(find "$CFG/$d" -mindepth 1 -maxdepth 1 2>"$JQ_ERR") || { fail "S0 cannot list $CFG/$d: $(head -c 200 "$JQ_ERR" | tr '\n' ' ')"; continue; }
  njson=0
  while IFS= read -r e; do
    [ -n "$e" ] || continue
    case "${e##*/}" in
      .gitkeep) ;;
      .*) fail "S0 unexpected hidden entry ${e#"$ROOT"/}" ;;
      *.json)
        if [ -f "$e" ] && [ -r "$e" ]; then njson=$((njson + 1))
        else fail "S0 ${e#"$ROOT"/} is not a readable regular file (unreadable, a directory, or a dangling or looping symlink)"; fi ;;
      scripts) { [ "$d" = triggers ] && [ -d "$e" ]; } || fail "S0 unexpected entry ${e#"$ROOT"/}" ;;
      *) fail "S0 unexpected file ${e#"$ROOT"/} (only *.json, .gitkeep and triggers/scripts belong in $d/; a stray or binary file is not configuration)" ;;
    esac
  done <<<"$listing"
  [ $njson -gt 0 ] || fail "S0 $CFG/$d has no .json files"
done
[ $errors -eq 0 ] || { echo "checker: $errors failure(s)"; exit 1; }
emit() { # emit <prefix> <multi-line messages>: one FAIL per non-empty line
  local line
  while IFS= read -r line; do [ -z "$line" ] || fail "$1 $line"; done <<<"$2"
}
shopt -s nullglob
mon_files=("$CFG"/monitors/*.json); trig_files=("$CFG"/triggers/*.json); net_files=("$CFG"/networks/*.json)

# S1 -- well-formed JSON: non-empty, exactly one JSON value.
for f in "${mon_files[@]}" "${trig_files[@]}" "${net_files[@]}"; do
  rel=${f#"$ROOT"/}
  [ -s "$f" ] || { fail "S1 $rel is empty"; continue; }
  jqo "S1 $rel is not valid JSON" -s 'if length == 1 then empty else error("expected exactly one JSON value, found \(length)") end' "$f"
done
# S1b -- type-level shape (deploy/shape.jq); a wrong-typed value is one FAIL line, not a jq runtime error.
shape() { # shape <function> <file...>
  local fn=$1 f; shift
  for f in "$@"; do
    if jqo "S1b ${f#"$ROOT"/}" -r -L "$HERE" "include \"shape\"; $fn[]" "$f"; then emit "S1b ${f#"$ROOT"/}:" "$JQ_OUT"; fi
  done
}
[ $errors -eq 0 ] || { echo "checker: $errors failure(s)"; exit 1; }
shape monitor_shape "${mon_files[@]}"
shape trigger_file_shape "${trig_files[@]}"
shape network_shape "${net_files[@]}"
[ $errors -eq 0 ] || { echo "checker: $errors failure(s)"; exit 1; }

# S2 -- V0.6 plain-value scan. Counts the objects, so an empty plain value is a violation too.
if jqo "S2 plain-value scan" -s --argjson allow "$PUBLIC_RPC_URLS" '[.[] | . as $doc
    | paths(type == "object" and .type? == "plain") as $p
    | select((($doc | type) == "object" and ($doc | has("network_type")) and ($p | length) == 3
              and $p[0] == "rpc_urls" and $p[2] == "url" and ($doc | getpath($p).value | IN($allow[]))) | not)]
    | length' "${trig_files[@]}" "${net_files[@]}"; then
  [ "$JQ_OUT" = 0 ] || fail "S2 plain value present in triggers/networks ($JQ_OUT value(s); not printed)"
fi

# S3 -- V0.3 secret-shaped strings anywhere under deploy/ (and the checked config dir). grep exit
# 0 = found, 1 = none, anything else = the scan itself failed, which is a failure, not a pass. Binary
# files are scanned as text (-a), and a symlink or special file, which grep -r would skip, is a failure.
secret_re='discord\.com/api/webhooks/[0-9]+/|[0-9]{8,10}:AA[A-Za-z0-9_-]{30,}'
scan_dirs=("$ROOT/deploy")
case "$CFG/" in "$ROOT/deploy/"*) ;; *) scan_dirs+=("$CFG") ;; esac
unscanned=$(find -H "${scan_dirs[@]}" ! -type f ! -type d -print 2>&1); rc=$?
if [ $rc -ne 0 ]; then fail "S3 cannot walk the scanned directories (find exit $rc): $(printf '%s' "$unscanned" | head -c 300 | tr '\n' ' ')"
elif [ -n "$unscanned" ]; then fail "S3 symlink or special file the secret scan does not follow: $(printf '%s\n' "$unscanned" | sed "s#$ROOT/##" | tr '\n' ' ')"; fi
secret_files=$(LC_ALL=C grep -rEla "$secret_re" "${scan_dirs[@]}" 2>&1); rc=$?
if [ $rc -eq 0 ]; then
  fail "S3 webhook- or token-shaped string (files: $(printf '%s\n' "$secret_files" | sed "s#$ROOT/##" | tr '\n' ' '))"
elif [ $rc -ne 1 ]; then
  fail "S3 secret scan failed (grep exit $rc): $secret_files"
fi

# S4 -- triggers.
trigger_keys=""
if jqo "S4 trigger keys" -r 'keys[]' "${trig_files[@]}"; then
  trigger_keys=$(printf '%s\n' "$JQ_OUT" | sort)
  [ -n "$trigger_keys" ] || fail "S4 no trigger is defined under $CFG/triggers"
  dup=$(printf '%s\n' "$trigger_keys" | uniq -d)
  [ -z "$dup" ] || fail "S4 duplicate trigger key(s): $dup"
fi
if jqo "S4 trigger names" -r 'to_entries[].value.name' "${trig_files[@]}"; then
  dupname=$(printf '%s\n' "$JQ_OUT" | sort | uniq -d)
  [ -z "$dupname" ] || fail "S4 duplicate trigger name(s) (the Monitor refuses to load them): $dupname"
fi

for f in "${trig_files[@]}"; do
  rel=${f#"$ROOT"/}
  jqo "S4 $rel" -r '
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
  ' "$f" && emit "S4 $rel" "$JQ_OUT"
done

# Script trigger files must exist under triggers/scripts (config paths are relative to the Monitor cwd).
for f in "${trig_files[@]}"; do
  jqo "S4 ${f#"$ROOT"/} script paths" -r 'to_entries[].value | select(.trigger_type == "script") | .config.script_path' "$f" || continue
  while IFS= read -r p; do
    [ -z "$p" ] || [ -f "$CFG/triggers/scripts/${p##*/}" ] || fail "S4 ${f#"$ROOT"/}: script $p has no file under triggers/scripts"
  done <<<"$JQ_OUT"
done

# S5 -- networks.
for f in "${net_files[@]}"; do
  rel=${f#"$ROOT"/}
  jqo "S5 $rel" -r --argjson allow "$PUBLIC_RPC_URLS" '
    def envref: (.url.type == "environment") and ((.url.value | type) == "string") and (.url.value | test("^[A-Z][A-Z0-9_]*$"));
    def public: (.url.type == "plain") and (.url.value | IN($allow[]));
    def maybe_tenderly: envref or ((.url.value | type) == "string" and (.url.value | test("tenderly")));
    def second($c): ($c | type) == "string" and ($c | split(" ") | length) == 6 and ($c | split(" ")[0] | test("^[0-9]{1,2}$"));
    (if (.rpc_urls | length) == 0 then "no rpc_urls" else empty end),
    (.rpc_urls[]? | select((envref or public) | not) | "rpc url is neither an environment reference nor an allow-listed keyless public URL"),
    (if ([.rpc_urls[]? | select(.weight == 100 and .url.value == "MAINNET_RPC_URL")] | length) != 1 then "primary RPC (weight 100) must be MAINNET_RPC_URL" else empty end),
    (if ([.rpc_urls[]?.weight] | length) != ([.rpc_urls[]?.weight] | unique | length) then "rpc_urls weights must be unique (the Monitor rotates in weight order)" else empty end),
    (if ([.rpc_urls[]? | select(public and (maybe_tenderly | not))] | length) < 2 then "D20: at least two non-Tenderly public RPC URLs are required" else empty end),
    ([.rpc_urls[]?] | sort_by(-.weight) as $r | ($r | length) as $n
     | range(0; if $n > 1 then $n else 0 end) as $i
     | select(($r[$i] | maybe_tenderly) and ($r[($i + 1) % $n] | maybe_tenderly))
     | "D20: rotation goes from \($r[$i].url.value) to \($r[($i + 1) % $n].url.value), both possibly Tenderly (one rate limit); interleave a non-Tenderly endpoint"),
    (if second(.cron_schedule) | not then "cron_schedule must be a 6-field cron with one fixed second (at most once a minute, D20), got \(.cron_schedule)" else empty end),
    (if ((.max_past_blocks // 0) <= 0) then "max_past_blocks must be > 0" else empty end),
    (if ((.max_past_blocks // 0) > 100) then "max_past_blocks must be <= 100 (a cycle fetches up to max_past_blocks + 1 full blocks at once; the public endpoints rate-limit such bursts, D20), got \(.max_past_blocks)" else empty end),
    (if (.recovery_config | type) != "object" then "recovery_config missing"
     else (.recovery_config | (if .enabled != true then "recovery_config.enabled must be true" else empty end),
           (["cron_schedule", "max_blocks_per_run", "max_block_age", "max_retries", "retry_delay_ms"][] as $k | if has($k) | not then "recovery_config.\($k) missing" else empty end)),
          (if second(.cron_schedule) and second(.recovery_config.cron_schedule)
              and (.cron_schedule | split(" ")[0]) == (.recovery_config.cron_schedule | split(" ")[0])
           then "recovery_config.cron_schedule fires on the same second as cron_schedule (the recovery run is skipped while the main run holds the lock)" else empty end) end)
  ' "$f" && emit "S5 $rel" "$JQ_OUT"
done

# S6 -- monitors.
slugs=""
if jqo "S6 network slugs" -r '.slug' "${net_files[@]}"; then slugs=$(printf '%s\n' "$JQ_OUT" | sort -u); fi
if jqo "S6 monitor names" -r '.name' "${mon_files[@]}"; then
  dupmon=$(printf '%s\n' "$JQ_OUT" | sort | uniq -d)
  [ -z "$dupmon" ] || fail "S6 duplicate monitor name(s): $dupmon"
fi
for f in "${mon_files[@]}"; do
  rel=${f#"$ROOT"/}
  if jqo "S6 $rel addresses" '.addresses | length' "$f"; then [ "$JQ_OUT" -gt 0 ] || fail "S6 $rel has no addresses"; fi
  if jqo "S6 $rel networks" '(.networks // []) | length' "$f"; then [ "$JQ_OUT" -gt 0 ] || fail "S6 $rel has no networks"; fi
  if jqo "S6 $rel match_conditions" '(.match_conditions | type) == "object"
         and ([.match_conditions.events, .match_conditions.functions, .match_conditions.transactions]
              | map(select(type == "array") | length) | add // 0) > 0' "$f"; then
    [ "$JQ_OUT" = true ] || fail "S6 $rel has no match_conditions with a non-empty events/functions/transactions list"
  fi
  if jqo "S6 $rel triggers" -r '.triggers[]?' "$f"; then
    while IFS= read -r t; do
      [ -z "$t" ] || grep -qxF -- "$t" <<<"$trigger_keys" || fail "S6 $rel references undefined trigger '$t'"
    done <<<"$JQ_OUT"
  fi
  if jqo "S6 $rel triggers" '.triggers | length' "$f"; then [ "$JQ_OUT" -gt 0 ] || fail "S6 $rel has no triggers"; fi
  if jqo "S6 $rel networks" -r '.networks[]?' "$f"; then
    while IFS= read -r n; do
      [ -z "$n" ] || grep -qxF -- "$n" <<<"$slugs" || fail "S6 $rel references undefined network '$n'"
    done <<<"$JQ_OUT"
  fi
  if jqo "S6 $rel trigger_conditions" -r '.trigger_conditions[]?.script_path' "$f"; then
    while IFS= read -r p; do
      [ -z "$p" ] || [ -f "$CFG/filters/${p##*/}" ] || fail "S6 $rel: trigger_conditions script $p has no file under filters"
    done <<<"$JQ_OUT"
  fi
done

# S7 -- ABI membership (sibling script; its own failure lines are passed through).
abi_out=$("$HERE/check-abi.sh" --config-dir "$CFG" 2>&1) || {
  printf '%s\n' "$abi_out" | sed 's/^/S7 /'
  fail "S7 deploy/check-abi.sh failed"
}

if [ $errors -ne 0 ]; then
  echo "checker: $errors failure(s)"
  exit 1
fi
echo "checker: OK (${#mon_files[@]} monitors, $(printf '%s\n' "$trigger_keys" | grep -c .) triggers, ${#net_files[@]} network)"
