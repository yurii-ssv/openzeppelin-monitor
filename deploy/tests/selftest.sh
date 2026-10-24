#!/usr/bin/env bash
# Offline self-test for the deploy-side gates (CP1A repair 1: F2, F3, F8, template structure of F1/F4;
# repair 2: F8b ABI layout gate against the committed reference; repair 4: F10, the fail-open class).
# Every mutation is made in a throw-away copy of deploy/ (never in the tracked tree); the script
# asserts that the tracked tree is byte-identical before and after.
#
#   deploy/tests/selftest.sh
#
# Needs jq. The render.sh cases also need target/release/openzeppelin-monitor (or MONITOR_BIN) and
# run the real Monitor --check with placeholder environment values (no network, no secrets); they
# are reported SKIPPED, not passed, when the binary is absent.
# Exit 0 only if every case behaves as stated.
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
BIN=${MONITOR_BIN:-$ROOT/target/release/openzeppelin-monitor}
{ command -v jq >/dev/null && [ "$(jq -n '1 + 1' 2>/dev/null)" = 2 ]; } || { echo "selftest: jq is required (not on PATH, or not working)" >&2; exit 2; }
REAL_JQ=$(command -v jq)

TMP=$(mktemp -d) || { echo "selftest: cannot create a temporary directory" >&2; exit 2; }
trap 'chmod -R u+rwx "$TMP" 2>/dev/null; rm -rf "$TMP"' EXIT
before=$(cd "$ROOT" && find deploy -type f -exec shasum {} + | sort | shasum)
pass=0; failn=0; skipped=0

ok()   { pass=$((pass + 1)); printf 'ok    %s\n' "$1"; }
bad()  { failn=$((failn + 1)); printf 'FAIL  %s\n' "$1"; }
fresh() { # fresh <name>: a pristine copy of deploy/ + scripts/ + the M15 spec under $TMP/<name>; prints the root
  rm -rf "${TMP:?}/$1"; mkdir -p "$TMP/$1/docs/ssv-specs"
  { cp -R "$ROOT/deploy" "$TMP/$1/deploy" && cp -R "$ROOT/scripts" "$TMP/$1/scripts" \
      && cp "$ROOT/docs/ssv-specs/monitoring-service-spec.md" "$TMP/$1/docs/ssv-specs/"; } \
    || { echo "selftest: harness failure: cannot copy the tree for $1" >&2; : > "$TMP/.harness-failed"; }
  echo "$TMP/$1"
}
mutate() { # mutate <file> <jq filter>: a mutation that did not apply must fail the run, not leave a pristine file to be "rejected"
  if jq "$2" "$1" > "$1.tmp"; then mv "$1.tmp" "$1"; else rm -f "$1.tmp"; bad "harness: mutation did not apply to ${1#"$TMP"/}: $2"; fi
}
# expect <label> <want-exit: zero|nonzero> <pattern-or-empty> -- cmd...
expect() {
  local label=$1 want=$2 pat=$3; shift 4
  local out rc; out=$("$@" 2>&1); rc=$?
  if [ "$want" = zero ] && [ $rc -ne 0 ]; then bad "$label (exit $rc, wanted 0): $(printf '%s' "$out" | tail -2 | tr '\n' ' ')"; return; fi
  if [ "$want" = nonzero ] && [ $rc -eq 0 ]; then bad "$label (exit 0, wanted non-zero): $(printf '%s' "$out" | tail -1)"; return; fi
  if [ -n "$pat" ] && ! grep -Eq -- "$pat" <<<"$out"; then bad "$label (output lacks /$pat/): $(printf '%s' "$out" | tail -2 | tr '\n' ' ')"; return; fi
  ok "$label (exit $rc)"
}

finish() { # the shared end of every run
  [ ! -e "$TMP/.harness-failed" ] || bad "harness: a pristine copy could not be made"
  after=$(cd "$ROOT" && find deploy -type f -exec shasum {} + | sort | shasum)
  [ "$before" = "$after" ] && ok "tracked deploy/ tree unchanged by the self-test" || bad "tracked deploy/ tree changed during the self-test"
  echo "selftest: $pass passed, $failn failed, $skipped skipped"
  [ $failn -eq 0 ]
}
echo "== baseline"
expect "checker on the tracked templates" zero 'checker: OK \(' -- bash "$ROOT/deploy/check-templates.sh"
expect "abi check on the tracked templates" zero 'abi-check: OK' -- bash "$ROOT/deploy/check-abi.sh" --config-dir "$ROOT/deploy/config"
expect "M15 coverage + D2 routing on the tracked templates" zero 'no M15/M10 info event' -- bash "$ROOT/deploy/check-m15-coverage.sh"

echo "== F10: the checkers fail closed (a jq error, a malformed or unreadable file, a missing jq are red, never OK)"
# Shim PATH directories: the tools the scripts use (no jq), a jq that exits 0 without output, and a jq
# that fails (exit 5) for every call whose arguments contain $JQ_FAIL_MATCH [and $JQ_FAIL_AND].
mkshim() { # mkshim <dir> [jq-shim-body]
  local d=$1 t p; mkdir -p "$d"
  for t in bash env sh dirname basename cat find grep sed tr sort uniq wc mktemp rm head tail cp mv ln mkdir chmod awk comm date cut paste touch; do
    p=$(command -v "$t" 2>/dev/null) && case "$p" in /*) ln -sf "$p" "$d/$t" ;; esac
  done
  if [ -n "${2:-}" ]; then printf '%s\n' "$2" > "$d/jq"; chmod +x "$d/jq"; fi
}
NOJQ=$TMP/shim-nojq; mkshim "$NOJQ"
STUBJQ=$TMP/shim-stubjq; mkshim "$STUBJQ" '#!/bin/sh
exit 0'
FLAKY=$TMP/shim-flaky; mkshim "$FLAKY" '#!/bin/sh
case "$*" in *"$JQ_FAIL_MATCH"*) case "$*" in *"${JQ_FAIL_AND:-}"*) echo x >> "$JQ_HITS"; echo "jq: error: injected failure" >&2; exit 5 ;; esac ;; esac
exec "$REAL_JQ" "$@"'
jq_fails() { # jq_fails <label> <marker> [and-marker] -- cmd...: every jq call containing the marker fails; the command must exit non-zero
  local label=$1 marker=$2 and="" out rc n hits="$TMP/jq-hits"
  shift 2; if [ "${1:-}" != -- ]; then and=$1; shift; fi; shift
  : > "$hits"
  out=$(env PATH="$FLAKY" REAL_JQ="$REAL_JQ" JQ_FAIL_MATCH="$marker" JQ_FAIL_AND="$and" JQ_HITS="$hits" "$@" 2>&1); rc=$?
  n=$(wc -l < "$hits" | tr -d ' ')
  if [ "$n" -eq 0 ]; then bad "$label: no jq call contained the marker, so nothing was injected"
  elif [ $rc -eq 0 ]; then bad "$label: jq failed $n time(s) here but the command exited 0: $(printf '%s' "$out" | tail -1 | cut -c1-120)"
  else ok "$label (jq failed $n time(s); exit $rc)"; fi
}
cfgtrig=deploy/config/triggers/discord_dao_success.json
cfgmon=deploy/config/monitors/ssv_seed_stake_monitor.json
cfgnet=deploy/config/networks/evm_ethereum_mainnet.json
dropf() { rm -f "${1:?}/${2:?}"; } # dropf <root> <relative path>

echo "-- a trigger whose config (or the trigger, or the file) has the wrong type: the QA reproducer and its relatives"
for v in '"qa_invalid_config"' '[]' '5' 'null' 'true'; do
  r=$(fresh f10cfg); mutate "$r/$cfgtrig" ".discord_dao_success.config = $v"
  expect "trigger config = $v is red" nonzero 'config must be an object' -- bash "$r/deploy/check-templates.sh"
  if [ -x "$BIN" ]; then expect "render.sh also rejects trigger config = $v" nonzero '' -- env MONITOR_BIN="$BIN" bash "$r/deploy/render.sh" --out "$TMP/f10cfg-out/config"; fi
done
for v in '"s"' '[]' '5' 'null' '[1]' '{}'; do
  r=$(fresh f10trig); mutate "$r/$cfgtrig" ".discord_dao_success = $v"
  case "$v" in '{}') pat='name must be a string' ;; *) pat='trigger must be an object' ;; esac
  expect "trigger entry = $v is red" nonzero "$pat" -- bash "$r/deploy/check-templates.sh"
done
for v in '"s"' '[]' '5' 'null' 'true'; do
  r=$(fresh f10file); echo "$v" > "$r/$cfgtrig"
  expect "trigger file that is just $v is red" nonzero 'S1|trigger file must be a JSON object' -- bash "$r/deploy/check-templates.sh"
done
r=$(fresh f10msg); mutate "$r/$cfgtrig" '.discord_dao_success.config.message = "just a string"'
expect "trigger message = string is red" nonzero 'config.message must be an object' -- bash "$r/deploy/check-templates.sh"
r=$(fresh f10url); mutate "$r/$cfgtrig" '.discord_dao_success.config.discord_url = "https-looking-string"'
expect "trigger discord_url = string is red" nonzero 'config.discord_url must be an object' -- bash "$r/deploy/check-templates.sh"
r=$(fresh f10plain); mutate "$r/$cfgtrig" '.discord_dao_success.config.extra = {"type":"plain","value":""}'
expect "an EMPTY plain value is still a plain value (S2 counts objects, not non-empty strings)" nonzero 'S2 plain value present' -- bash "$r/deploy/check-templates.sh"

echo "-- a monitor with wrong-typed fields (the checker, the ABI gate and the M15 routing gate each)"
mon_cases=(
  'events as an object|.match_conditions.events = {"a":{"signature":"FeesSynced(uint256,uint256)"}}|match_conditions.events must be an array'
  'events as a string|.match_conditions.events = "x"|match_conditions.events must be an array'
  'addresses as a string|.addresses = "0xabc"|addresses must be an array'
  'addresses holding a number|.addresses = [5]|addresses\[0\] must be an object'
  'contract_spec as a string|.addresses[0].contract_spec = "x"|contract_spec must be an array'
  'triggers as a string|.triggers = "telegram_seed_stake"|triggers must be an array'
  'triggers holding a number|.triggers = [5]|triggers\[0\] must be a string'
  'networks as a string|.networks = "evm_ethereum_mainnet"|networks must be an array'
  'name as a number|.name = 5|name must be a string'
  'trigger_conditions as a string|.trigger_conditions = "x"|trigger_conditions must be an array'
  'match_conditions as an array|.match_conditions = []|match_conditions must be an object'
  'functions as an object|.match_conditions.functions = {"a":1}|match_conditions.functions must be an array'
  'an event that is a string|.match_conditions.events[0] = "x"|match_conditions.events\[0\] must be an object'
  'a transaction status that is not Any/Success/Failure|.match_conditions.transactions = [{"status":"Maybe","expression":null}]|is not one of Any'
)
for c in "${mon_cases[@]}"; do
  IFS='|' read -r label flt pat <<<"$c"
  r=$(fresh f10mon); mutate "$r/$cfgmon" "$flt"
  expect "monitor with $label: check-templates red" nonzero "S1b .*$pat" -- bash "$r/deploy/check-templates.sh"
  expect "monitor with $label: check-abi red" nonzero "A0 .*$pat" -- bash "$r/deploy/check-abi.sh" --config-dir "$r/deploy/config"
done
for v in '[]' '"x"' '5' 'null'; do
  r=$(fresh f10monfile); echo "$v" > "$r/$cfgmon"
  expect "monitor file that is just $v: check-templates red" nonzero 'S1' -- bash "$r/deploy/check-templates.sh"
  expect "monitor file that is just $v: check-abi red" nonzero 'FAIL' -- bash "$r/deploy/check-abi.sh" --config-dir "$r/deploy/config"
done
r=$(fresh f10m15)
mutate "$r/deploy/config/monitors/ssv_dao_discord_success_monitor.json" '.match_conditions.events |= (to_entries | map({key: (.key | tostring), value: .value}) | from_entries)'
expect "check-m15-coverage: the covering monitor's events as an object is red" nonzero 'match_conditions.events must be an array' -- bash "$r/deploy/check-m15-coverage.sh"
r=$(fresh f10m15r)
mutate "$r/deploy/config/monitors/ssv_dao_success_monitor.json" '.match_conditions.events += [{"signature":"OracleReplaced(uint32,address,address)","expression":null}] | .triggers = "telegram_dao_success"'
expect "check-m15-coverage: a Telegram-routed monitor whose triggers is a string cannot be skipped" nonzero 'triggers must be an array' -- bash "$r/deploy/check-m15-coverage.sh"
r=$(fresh f10m15t); mutate "$r/deploy/config/triggers/telegram_dao_success.json" '.telegram_dao_success.config = "x"'
expect "check-m15-coverage: a malformed trigger file is red, not an empty Telegram list" nonzero 'config must be an object' -- bash "$r/deploy/check-m15-coverage.sh"
r=$(fresh f10net); mutate "$r/$cfgnet" '.rpc_urls = "x"'
expect "network rpc_urls = string is red" nonzero 'rpc_urls must be an array' -- bash "$r/deploy/check-templates.sh"
for f in '. = []' '. = "x"' '.slug = 5' '.max_past_blocks = "3600"' '.rpc_urls[0] = 5' '.rpc_urls[0].url = "x"' '.recovery_config = "x"'; do
  r=$(fresh f10net2); mutate "$r/$cfgnet" "$f"
  expect "network mutated by '$f' is red" nonzero 'S1b|S5' -- bash "$r/deploy/check-templates.sh"
done
if [ -x "$BIN" ]; then
  r=$(fresh f10netr); mutate "$r/$cfgnet" '.rpc_urls = {}'
  expect "render.sh does not coerce a network whose rpc_urls is an object into an empty array" nonzero 'rpc_urls must be an array' -- env MONITOR_BIN="$BIN" bash "$r/deploy/render.sh" --out "$TMP/f10netr-out/config"
  r=$(fresh f10trr); mutate "$r/deploy/config/triggers/telegram_dao_success.json" '. = ["x"]'
  expect "render.sh with a trigger file that is an array is red" nonzero '' -- env MONITOR_BIN="$BIN" TELEGRAM_CHAT_ID=-100123 bash "$r/deploy/render.sh" --out "$TMP/f10trr-out/config"
fi

echo "-- unreadable, empty, binary, looping and stray files in the config dir"
for kind in monitors triggers networks; do
  case $kind in monitors) f=ssv_seed_stake_monitor.json ;; triggers) f=discord_dao_success.json ;; networks) f=evm_ethereum_mainnet.json ;; esac
  r=$(fresh f10empty); : > "$r/deploy/config/$kind/$f"
  expect "empty $kind file is red and says so" nonzero "S1 .*$f is empty" -- bash "$r/deploy/check-templates.sh"
  r=$(fresh f10bin); head -c 300 /dev/urandom > "$r/deploy/config/$kind/$f"
  expect "binary $kind file named .json is red" nonzero "S1 " -- bash "$r/deploy/check-templates.sh"
  r=$(fresh f10loop); dropf "$r" "deploy/config/$kind/$f"; ln -s "$f" "$r/deploy/config/$kind/$f"
  expect "$kind file that is a symlink loop is red" nonzero "S0 .*$f is not a readable regular file" -- bash "$r/deploy/check-templates.sh"
  r=$(fresh f10dangle); dropf "$r" "deploy/config/$kind/$f"; ln -s /nonexistent-for-selftest "$r/deploy/config/$kind/$f"
  expect "$kind file that is a dangling symlink is red" nonzero "S0 .*$f is not a readable regular file" -- bash "$r/deploy/check-templates.sh"
  r=$(fresh f10unr); chmod 000 "$r/deploy/config/$kind/$f"
  if [ -r "$r/deploy/config/$kind/$f" ]; then echo "SKIP  unreadable $kind file (running as a user that reads mode 000)"; skipped=$((skipped + 1))
  else expect "unreadable $kind file is red and says so" nonzero "S0 .*$f is not a readable regular file" -- bash "$r/deploy/check-templates.sh"; fi
  chmod -R u+rwx "$r"
  r=$(fresh f10stray); head -c 300 /dev/urandom > "$r/deploy/config/$kind/blob.bin"
  expect "binary non-JSON file in $kind/ is red" nonzero "S0 unexpected file .*blob.bin" -- bash "$r/deploy/check-templates.sh"
done
r=$(fresh f10abiempty); : > "$r/$cfgmon"
expect "check-abi: an empty monitor file is red (jq prints nothing for empty input)" nonzero 'is empty' -- bash "$r/deploy/check-abi.sh" --config-dir "$r/deploy/config"
r=$(fresh f10abiloop); dropf "$r" "$cfgmon"; ln -s ssv_seed_stake_monitor.json "$r/$cfgmon"
expect "check-abi: a monitor that is a symlink loop is red, not skipped" nonzero 'not a readable regular file' -- bash "$r/deploy/check-abi.sh" --config-dir "$r/deploy/config"
r=$(fresh f10abidang); dropf "$r" "$cfgmon"; ln -s /nonexistent-for-selftest "$r/$cfgmon"
expect "check-abi: a dangling-symlink monitor is red, not skipped" nonzero 'not a readable regular file' -- bash "$r/deploy/check-abi.sh" --config-dir "$r/deploy/config"
r=$(fresh f10abiunr); chmod 000 "$r/$cfgmon"
if [ -r "$r/$cfgmon" ]; then skipped=$((skipped + 1)); echo "SKIP  check-abi unreadable monitor"; else expect "check-abi: an unreadable monitor is red" nonzero 'not a readable regular file' -- bash "$r/deploy/check-abi.sh" --config-dir "$r/deploy/config"; fi
chmod -R u+rwx "$r"
r=$(fresh f10m15empty); : > "$r/deploy/config/monitors/ssv_dao_discord_success_monitor.json"
expect "check-m15-coverage: an empty covering monitor is red" nonzero 'missing, unreadable or empty' -- bash "$r/deploy/check-m15-coverage.sh"
r=$(fresh f10m15bin); head -c 300 /dev/urandom > "$r/deploy/config/monitors/ssv_dao_success_monitor.json"
expect "check-m15-coverage: a binary monitor in the routing scan is red, not skipped" nonzero 'jq failed' -- bash "$r/deploy/check-m15-coverage.sh"
echo "-- secret scan: binary and symlinked files are scanned or refused, not skipped"
zero=0; hook="https://discord.com/api/webhooks/${zero}/abcdef"
r=$(fresh f10sec); printf '\000\001\002%s\000\377' "$hook" > "$r/deploy/config/triggers/scripts/blob.bin"
expect "a webhook inside a binary file is found (grep -a)" nonzero 'S3 webhook- or token-shaped' -- bash "$r/deploy/check-templates.sh"
r=$(fresh f10sym); ln -s ../../../abi/provenance.json "$r/deploy/config/triggers/scripts/link.txt"
expect "a symlink under deploy/ that grep -r would not follow is red" nonzero 'S3 symlink or special file' -- bash "$r/deploy/check-templates.sh"

echo "-- jq absent or not working: a clear 'jq is required', never a pass"
r=$(fresh f10nojq)
for c in "check-templates.sh" "check-abi.sh --config-dir $r/deploy/config" "check-m15-coverage.sh" "abi/derive.sh --ssv-out $r --safe-abi $r/x" "tests/abi-mutation-sweep.sh --config-dir $r/deploy/config"; do
  set -- $c; script=$1; shift
  expect "no jq on PATH: deploy/$script" nonzero 'jq is required' -- env PATH="$NOJQ" bash "$r/deploy/$script" "$@"
  expect "a jq that exits 0 without output: deploy/$script" nonzero 'jq is required' -- env PATH="$STUBJQ" bash "$r/deploy/$script" "$@"
done
expect "no jq on PATH: deploy/render.sh" nonzero 'jq is required' -- env PATH="$NOJQ" bash "$r/deploy/render.sh" --out "$TMP/f10nojq-out/config"
expect "a jq that exits 0 without output: deploy/render.sh" nonzero 'jq is required' -- env PATH="$STUBJQ" bash "$r/deploy/render.sh" --out "$TMP/f10nojq-out/config"
expect "no jq on PATH: scripts/local-dry-run.sh" nonzero 'jq is required' -- env PATH="$NOJQ" bash "$r/scripts/local-dry-run.sh" --prepare
expect "a jq that exits 0 without output: scripts/local-dry-run.sh" nonzero 'jq is required' -- env PATH="$STUBJQ" bash "$r/scripts/local-dry-run.sh" --prepare

echo "-- every jq call whose failure could read as 'no violations' is checked (each site fails by injection)"
r=$(fresh f10inj); c=("$BASH" "$r/deploy/check-templates.sh")
jq_fails "check-templates S1 (JSON validity)" 'expected exactly one JSON value' -- "${c[@]}"
jq_fails "check-templates S1b (shape, monitors)" 'monitor_shape[]' -- "${c[@]}"
jq_fails "check-templates S1b (shape, triggers)" 'trigger_file_shape[]' -- "${c[@]}"
jq_fails "check-templates S1b (shape, networks)" 'network_shape[]' -- "${c[@]}"
jq_fails "check-templates S2 (plain values)" '.type? == "plain"' -- "${c[@]}"
jq_fails "check-templates S4 (trigger keys)" 'keys[]' -- "${c[@]}"
jq_fails "check-templates S4 (trigger names)" 'to_entries[].value.name' -- "${c[@]}"
jq_fails "check-templates S4 (required fields; the F10 site)" 'def env_ref' -- "${c[@]}"
jq_fails "check-templates S4 (script paths)" 'select(.trigger_type == "script") | .config.script_path' -- "${c[@]}"
jq_fails "check-templates S5 (networks)" 'recovery_config missing' -- "${c[@]}"
jq_fails "check-templates S6 (network slugs)" '-r .slug' -- "${c[@]}"
jq_fails "check-templates S6 (monitor names)" '-r .name' -- "${c[@]}"
jq_fails "check-templates S6 (addresses)" '.addresses | length' -- "${c[@]}"
jq_fails "check-templates S6 (networks length)" '(.networks // []) | length' -- "${c[@]}"
jq_fails "check-templates S6 (match_conditions)" '(.match_conditions | type) == "object"' -- "${c[@]}"
jq_fails "check-templates S6 (trigger references)" '.triggers[]?' -- "${c[@]}"
jq_fails "check-templates S6 (triggers length)" '.triggers | length' -- "${c[@]}"
jq_fails "check-templates S6 (network references)" '.networks[]?' -- "${c[@]}"
jq_fails "check-templates S6 (trigger_conditions scripts)" '.trigger_conditions[]?.script_path' -- "${c[@]}"
c=("$BASH" "$r/deploy/check-abi.sh" --config-dir "$r/deploy/config")
jq_fails "check-abi (reference validity)" '.contracts | type == "object"' -- "${c[@]}"
jq_fails "check-abi (shape)" 'monitor_shape[]' -- "${c[@]}"
jq_fails "check-abi (per-monitor rules)" 'include "normalize"' -- "${c[@]}"
jq_fails "check-abi (compared count; used to print OK)" '[.[] | .addresses[]? | .contract_spec?' -- "${c[@]}"
c=("$BASH" "$r/deploy/check-m15-coverage.sh")
jq_fails "check-m15-coverage (name list)" '-R .' -- "${c[@]}"
jq_fails "check-m15-coverage (required signatures)" '$names[] as $n' -- "${c[@]}"
jq_fails "check-m15-coverage (the monitor's events)" '.match_conditions.events[]?.signature' -- "${c[@]}"
jq_fails "check-m15-coverage (Telegram trigger keys)" '.value.trigger_type == "telegram"' -- "${c[@]}"
jq_fails "check-m15-coverage (the D2 routing scan; was skipped per file)" 'select(any(' -- "${c[@]}"
jq_fails "check-m15-coverage (shape)" 'monitor_shape[]' -- "${c[@]}"
if [ -x "$BIN" ]; then
  c=(env MONITOR_BIN="$BIN" TELEGRAM_CHAT_ID=-100123 "$BASH" "$r/deploy/render.sh" --out "$TMP/f10inj-out/config")
  jq_fails "render.sh (optional second RPC rewrite)" 'MAINNET_RPC_URL_2' -- "${c[@]}"
  jq_fails "render.sh (Telegram chat id rewrite)" 'a trigger file must be an object' -- "${c[@]}"
  jq_fails "render.sh (referenced environment variables)" '.. | objects | select(.type? == "environment") | .value' -- "${c[@]}"
  jq_fails "render.sh (rendered monitor count)" 'select(.paused != true)' -- "${c[@]}"
else skipped=$((skipped + 4)); echo "SKIP  render.sh injection cases (no binary at $BIN)"; fi
# the sweep: a monitor whose jq fails must be a counted failure, not a skipped one
r=$(fresh f10sweep); mkdir -p "$r/sweepcfg/monitors"
cp "$r/deploy/config/monitors/ssv_seed_stake_monitor.json" "$r/deploy/config/monitors/ssv_oracle_success_monitor.json" "$r/sweepcfg/monitors/"
jq_fails "abi-mutation-sweep (contract_spec count of one monitor)" 'select(type == "array" and length > 0)' ssv_seed_stake_monitor -- "$BASH" "$r/deploy/tests/abi-mutation-sweep.sh" --config-dir "$r/sweepcfg"
jq_fails "abi-mutation-sweep (mutated-entry count of one monitor)" 'norm_entry' ssv_seed_stake_monitor -- "$BASH" "$r/deploy/tests/abi-mutation-sweep.sh" --config-dir "$r/sweepcfg"
jq_fails "abi-mutation-sweep (writing the mutated monitor)" '|= map(' ssv_seed_stake_monitor -- "$BASH" "$r/deploy/tests/abi-mutation-sweep.sh" --config-dir "$r/sweepcfg"
dropf "$r" sweepcfg/monitors/ssv_oracle_success_monitor.json; dropf "$r" sweepcfg/monitors/ssv_seed_stake_monitor.json
ln -s ssv_oracle_success_monitor.json "$r/sweepcfg/monitors/ssv_oracle_success_monitor.json"
expect "abi-mutation-sweep: a monitor that is a symlink loop is a counted failure, not skipped" nonzero 'not a readable regular file' -- bash "$r/deploy/tests/abi-mutation-sweep.sh" --config-dir "$r/sweepcfg"

echo "-- check-m15-coverage: the required set cannot shrink silently"
r=$(fresh f10spec)
sed -E '/^### M15 /,/^### M16 /{/OracleReplaced/!s/`([A-Z][A-Za-z0-9]*)`/\1/g;}' "$ROOT/docs/ssv-specs/monitoring-service-spec.md" > "$r/docs/ssv-specs/monitoring-service-spec.md"
expect "an M15 section that names one event instead of 16 is red (was: required set of 2, green)" nonzero 'M15 section names [0-9]+ events, the plan pin has 16' -- bash "$r/deploy/check-m15-coverage.sh"
r=$(fresh f10nospec); dropf "$r" docs/ssv-specs/monitoring-service-spec.md
expect "a missing spec is red and says so" nonzero 'spec .* not found' -- bash "$r/deploy/check-m15-coverage.sh"
r=$(fresh f10dup)
mutate "$r/deploy/config/monitors/ssv_dao_success_monitor.json" '.match_conditions.events += [{"signature":"OracleReplaced(uint32,address,address)","expression":null}]'
jq '{telegram_dao_success: .discord_dao_success}' "$r/$cfgtrig" > "$r/deploy/config/triggers/zz_dup.json"
expect "a later file that redefines telegram_dao_success as Discord cannot hide the Telegram route (was merged with add: green)" nonzero 'M15/M10 info on a Telegram route' -- bash "$r/deploy/check-m15-coverage.sh"
expect "trigger key defined twice is red in check-templates too" nonzero 'duplicate trigger key' -- bash "$r/deploy/check-templates.sh"

echo "-- trigger and monitor reference matching is literal"
r=$(fresh f10lit); mutate "$r/$cfgmon" '.triggers = ["telegram.seed.stake"]'
expect "a trigger reference that only matches as a regex (dots) is undefined" nonzero "references undefined trigger 'telegram.seed.stake'" -- bash "$r/deploy/check-templates.sh"

echo "-- scripts/local-dry-run.sh: rewrites cannot fail silently, every trigger of a file is handled"
dry_refs() { jq -r '.. | objects | select(.type? == "environment") | .value' "$1"/.local-dry/config/triggers/*.json | sort -u | tr '\n' ' '; }
r=$(fresh f10dry)
out=$(env -u TEST_DISCORD_WEBHOOK -u TEST_TELEGRAM_TOKEN -u TEST_TELEGRAM_CHAT_ID -u TEST_RPC_URL bash "$r/scripts/local-dry-run.sh" --prepare 2>&1); rc=$?
refs=$(dry_refs "$r")
[ $rc -eq 0 ] && [ -z "$refs" ] && [ "$(find "$r/.local-dry/config/triggers" -maxdepth 1 -name '*.json' | wc -l | tr -d ' ')" = 2 ] \
  && ok "no test channel: only the inbox bridge and the local log remain, no environment reference at all" || bad "local-dry-run baseline (exit $rc, refs '$refs')"
r=$(fresh f10dry2)
out=$(env TEST_DISCORD_WEBHOOK="https://example.invalid/hook" TEST_TELEGRAM_TOKEN=t TEST_TELEGRAM_CHAT_ID=1 TEST_RPC_URL=http://127.0.0.1:1 bash "$r/scripts/local-dry-run.sh" --prepare 2>&1); rc=$?
refs=$(dry_refs "$r")
[ $rc -eq 0 ] && [ "$refs" = "TEST_DISCORD_WEBHOOK TEST_TELEGRAM_TOKEN " ] && ok "with test channels every trigger reads only TEST_DISCORD_WEBHOOK / TEST_TELEGRAM_TOKEN" || bad "local-dry-run with test channels (exit $rc, refs '$refs')"
r=$(fresh f10dry3)
mutate "$r/deploy/config/triggers/script_inbox_append.json" '. + {"sneaky": {"name":"Sneaky","trigger_type":"discord","config":{"discord_url":{"type":"environment","value":"DISCORD_WEBHOOK_INFO"},"message":{"title":"t","body":"b"}}}}'
out=$(env -u TEST_DISCORD_WEBHOOK bash "$r/scripts/local-dry-run.sh" --prepare 2>&1); rc=$?
refs=$(dry_refs "$r")
[ $rc -eq 0 ] && [ -z "$refs" ] && jq -e 'has("inbox_append") and (has("sneaky") | not)' "$r/.local-dry/config/triggers/script_inbox_append.json" >/dev/null \
  && ok "a second (production Discord) trigger in the inbox file is dropped, not kept because the first entry was a script" || bad "local-dry-run multi-trigger file (exit $rc, refs '$refs')"
r=$(fresh f10dry4)
mutate "$r/deploy/config/triggers/script_inbox_append.json" '.inbox_append.config.script_path = "./config/triggers/scripts/other.sh"'
out=$(env -u TEST_DISCORD_WEBHOOK bash "$r/scripts/local-dry-run.sh" --prepare 2>&1); rc=$?
[ $rc -eq 0 ] && [ ! -e "$r/.local-dry/config/triggers/script_inbox_append.json" ] && ok "a script trigger other than the inbox bridge is dropped" || bad "local-dry-run other script (exit $rc)"
r=$(fresh f10dry5); c=("$BASH" "$r/scripts/local-dry-run.sh" --prepare)
jq_fails "local-dry-run (TEST_RPC_URL rewrite; was '&& mv', not caught by set -e)" '"type_": "rpc"' -- env TEST_RPC_URL=http://127.0.0.1:1 "${c[@]}"
jq_fails "local-dry-run (Discord trigger rewrite; the production reference stayed)" '"value": "TEST_DISCORD_WEBHOOK"' -- env TEST_DISCORD_WEBHOOK="https://example.invalid/hook" "${c[@]}"
jq_fails "local-dry-run (monitor trigger lists; was '&& mv')" '.triggers = ([.triggers[]' -- "${c[@]}"
jq_fails "local-dry-run (kept trigger list)" 'map(keys[])' -- "${c[@]}"
jq_fails "local-dry-run (production-secret audit)" 'select(startswith("TEST_") | not)' -- "${c[@]}"

echo "-- the inbox bridge never appends an empty or partial line"
IB="$ROOT/deploy/config/triggers/scripts/inbox_append.sh"
inbox_case() { # inbox_case <label> <stdin> <want: nonzero|zero> [PATH]
  local label=$1 in=$2 want=$3 p=${4:-$PATH} f="$TMP/inbox-case.jsonl" rc size=0
  rm -f "$f"; printf '%s' "$in" | env PATH="$p" INBOX_PATH="$f" sh "$IB" >/dev/null 2>&1; rc=$?
  [ -f "$f" ] && size=$(wc -c < "$f" | tr -d ' ')
  if [ "$want" = nonzero ]; then { [ $rc -ne 0 ] && [ "$size" = 0 ]; } && ok "$label (exit $rc, inbox $size bytes)" || bad "$label (exit $rc, inbox $size bytes)"
  else { [ $rc -eq 0 ] && [ "$(wc -l < "$f" | tr -d ' ')" = 1 ]; } && ok "$label (exit 0, one line)" || bad "$label (exit $rc, inbox $size bytes)"; fi
}
good='{"monitor_match":{"EVM":{"transaction":{"hash":"0xaa","blockNumber":"0x10","blockHash":"0xbb","from":"0x01","to":"0x02","input":"0xdeadbeef00"},"matched_on":{"functions":[{"signature":"commitRoot(bytes32,uint64)"}],"transactions":[{"status":"Failure"}]},"monitor":{"name":"m"}}}}'
inbox_case "a complete match is appended as one line" "$good" zero
inbox_case "empty stdin appends nothing" "" nonzero
inbox_case "{} appends nothing" '{}' nonzero
inbox_case "not JSON appends nothing" 'garbage' nonzero
inbox_case "a jq that exits 0 without output appends nothing (was: an empty line)" "$good" nonzero "$STUBJQ"
inbox_case "no jq at all appends nothing" "$good" nonzero "$NOJQ"

[ "${SELFTEST_ONLY:-}" != f10 ] || { finish; exit $?; }

echo "== F2: missing or empty inputs are red"
mkdir -p "$TMP/empty-config"
expect "missing --config-dir" nonzero 'S0 .* is not a directory' -- bash "$ROOT/deploy/check-templates.sh" --config-dir "$TMP/absent"
expect "empty --config-dir" nonzero 'S0 .*monitors is missing' -- bash "$ROOT/deploy/check-templates.sh" --config-dir "$TMP/empty-config"
expect "--config-dir without a value" nonzero 'needs a directory' -- bash "$ROOT/deploy/check-templates.sh" --config-dir
expect "missing --root" nonzero 'S0 --root .* is not a directory' -- bash "$ROOT/deploy/check-templates.sh" --root "$TMP/missing-root" --config-dir "$ROOT/deploy/config"
mkdir -p "$TMP/root-no-deploy"
expect "--root without deploy/" nonzero 'S0 --root .* has no deploy/' -- bash "$ROOT/deploy/check-templates.sh" --root "$TMP/root-no-deploy" --config-dir "$ROOT/deploy/config"
r=$(fresh unreadable); chmod 000 "$r/deploy/config/filters"
expect "secret scan that cannot read a directory is red (no swallowed grep error)" nonzero 'S3 secret scan failed' -- bash "$r/deploy/check-templates.sh"
chmod -R u+rwx "$r"
r=$(fresh nomon); rm -f "$r"/deploy/config/monitors/*.json
expect "config dir with no monitors" nonzero 'S0 .*monitors has no .json files' -- bash "$r/deploy/check-templates.sh"
r=$(fresh counts); rm -f "$r"/deploy/config/monitors/ssv_oracle_*.json
out=$(bash "$r/deploy/check-templates.sh" 2>&1); want=$(find "$r/deploy/config/monitors" -name '*.json' | wc -l | tr -d ' ')
case "$out" in *"checker: OK ($want monitors,"*) ok "counts come from the checked directory ($want monitors after deleting some)" ;; *) bad "counts: $out" ;; esac
r=$(fresh nonet); mutate "$r/deploy/config/monitors/ssv_seed_stake_monitor.json" 'del(.networks)'
expect "monitor without networks" nonzero 'has no networks' -- bash "$r/deploy/check-templates.sh"
r=$(fresh nomc); mutate "$r/deploy/config/monitors/ssv_seed_stake_monitor.json" 'del(.match_conditions)'
expect "monitor without match_conditions" nonzero 'has no match_conditions' -- bash "$r/deploy/check-templates.sh"
r=$(fresh emptymc); mutate "$r/deploy/config/monitors/ssv_seed_stake_monitor.json" '.match_conditions = {"events":[],"functions":[],"transactions":[]}'
expect "monitor with empty match_conditions (matches everything)" nonzero 'has no match_conditions' -- bash "$r/deploy/check-templates.sh"

echo "== F8: ABI membership (one-field mutation Nonexistent(uint256))"
r=$(fresh abi); mutate "$r/deploy/config/monitors/ssv_seed_stake_monitor.json" '.match_conditions.events[0].signature = "Nonexistent(uint256)"'
expect "check-abi rejects the event" nonzero 'A3 event Nonexistent\(uint256\) is not an event in the ABI' -- bash "$r/deploy/check-abi.sh" --config-dir "$r/deploy/config"
expect "check-templates rejects the event (S7)" nonzero 'S7' -- bash "$r/deploy/check-templates.sh"
if [ -x "$BIN" ]; then
  expect "render.sh exits non-zero on the mutated template" nonzero 'A3 event Nonexistent' -- env MONITOR_BIN="$BIN" bash "$r/deploy/render.sh" --out "$TMP/abi-out/config"
  rr=$(fresh abi-ok)
  expect "render.sh exits 0 on the restored (pristine) template" zero '--check passed' -- env MONITOR_BIN="$BIN" bash "$rr/deploy/render.sh" --out "$TMP/abi-ok-out/config"
else
  skipped=$((skipped + 2)); echo "SKIP  render.sh cases (no binary at $BIN)"
fi
r=$(fresh abi-fn); mutate "$r/deploy/config/monitors/ssv_retired_updater_attempt_monitor.json" '.match_conditions.functions[0].signature = "commitRoot(bytes32)"'
expect "function signature absent from the ABI" nonzero 'A4 function commitRoot\(bytes32\) is not a function' -- bash "$r/deploy/check-abi.sh" --config-dir "$r/deploy/config"
r=$(fresh abi-param); mutate "$r/deploy/config/monitors/ssv_seed_stake_monitor.json" '.match_conditions.events[0].expression = "bogusParam == 0"'
expect "expression parameter absent from the event" nonzero 'A5 event FeesSynced.*bogusParam' -- bash "$r/deploy/check-abi.sh" --config-dir "$r/deploy/config"
r=$(fresh abi-and); mutate "$r/deploy/config/monitors/ssv_updater_other_sender_monitor.json" '.match_conditions.transactions[0].expression = "from != 0x16A503C901615E6e21AA78cFcaFf11822fc2b617 && gas_used > 0"'
expect "&& in an expression" nonzero 'A7 expression uses' -- bash "$r/deploy/check-abi.sh" --config-dir "$r/deploy/config"
r=$(fresh abi-tx); mutate "$r/deploy/config/monitors/ssv_large_eth_transfer_telegram_monitor.json" '.match_conditions.transactions[0].expression = "amount > 1"'
expect "unknown transaction field" nonzero 'A6 transaction expression names amount' -- bash "$r/deploy/check-abi.sh" --config-dir "$r/deploy/config"
expect "missing config dir for check-abi" nonzero 'is not a directory' -- bash "$ROOT/deploy/check-abi.sh" --config-dir "$TMP/absent"

echo "== F3: M10/M15 info is Discord only"
r=$(fresh m15)
mutate "$r/deploy/config/monitors/ssv_dao_success_monitor.json" \
  '.match_conditions.events += [{"signature":"OracleReplaced(uint32,address,address)","expression":null}]'
expect "OracleReplaced on the Telegram DAO monitor is red" nonzero 'M15/M10 info on a Telegram route' -- bash "$r/deploy/check-m15-coverage.sh"
r=$(fresh m15fn)
mutate "$r/deploy/config/monitors/ssv_dao_success_monitor.json" \
  '.match_conditions.functions += [{"signature":"replaceOracle(uint32,address)"}]'
expect "replaceOracle on the Telegram DAO monitor is red" nonzero 'function replaceOracle' -- bash "$r/deploy/check-m15-coverage.sh"
r=$(fresh m15disc)
mutate "$r/deploy/config/monitors/ssv_dao_discord_success_monitor.json" \
  '.match_conditions.events |= map(select(.signature | startswith("OracleReplaced") | not))'
expect "OracleReplaced missing from the Discord DAO monitor is red" nonzero 'missing: OracleReplaced' -- bash "$r/deploy/check-m15-coverage.sh"
m=$ROOT/deploy/config/monitors
n=$(jq '[.match_conditions.events[] | select(.signature | startswith("OracleReplaced"))] | length' "$m/ssv_dao_success_monitor.json")
[ "$n" = 0 ] && ok "tracked Telegram DAO success monitor has no OracleReplaced" || bad "tracked Telegram DAO success monitor still has OracleReplaced"
n=$(jq '[.match_conditions.events[] | select(.signature | startswith("OracleReplaced"))] | length' "$m/ssv_dao_discord_success_monitor.json")
[ "$n" = 1 ] && ok "tracked Discord DAO success monitor has OracleReplaced" || bad "tracked Discord DAO success monitor lacks OracleReplaced"

echo "== F1/F4: structure of the M13 templates (behaviour is in m13-fork-regression.sh)"
A1=0xaeE058cCA5EED5470b4b48c159045b7C25ddb80f; A2=0x0144aE2456FbCF38Ba11e4ECF25F03A80A69F134
jq -e --arg a1 "$A1" --arg a2 "$A2" '
  (.addresses | map(.address | ascii_downcase) | index($a1 | ascii_downcase)) != null
  and (.addresses | map(.address | ascii_downcase) | index($a2 | ascii_downcase)) != null
  and (.match_conditions.transactions[0].status == "Any")
  and (.match_conditions.transactions[0].expression | (contains($a1) and contains($a2) and (contains("value") | not)))' \
  "$m/ssv_attacker_interaction_monitor.json" >/dev/null \
  && ok "attacker monitor: both addresses, status Any, no value predicate (zero-value and reverted interactions page)" \
  || bad "attacker monitor structure"
jq -e '(.addresses | length) == 2
  and (.match_conditions.transactions[0].expression | (contains("to == 0x3A98") and contains("to == 0x4BC6") and contains("value > 0")))' \
  "$m/ssv_retired_addresses_monitor.json" >/dev/null \
  && ok "retired-address monitor: two retired keys, incoming (to ==) and value > 0 only" \
  || bad "retired-address monitor structure"

echo "== F8b: ABI layout (indexed, type, order, arity, outputs) against the committed reference"
SEED=deploy/config/monitors/ssv_seed_stake_monitor.json
UNST='.addresses[0].contract_spec[] | select(.type=="event" and .name=="UnstakeRequested") | .inputs'
abi_red() { # abi_red <label> <monitor file under deploy/config/monitors> <jq filter> <pattern>
  local r; r=$(fresh abi-layout)
  mutate "$r/deploy/config/monitors/$2" "$3"
  expect "$1" nonzero "$4" -- bash "$r/deploy/check-abi.sh" --config-dir "$r/deploy/config"
}
abi_red "indexed flags swapped between user and amount (QA's F8b case)" ssv_seed_stake_monitor.json \
  "($UNST) |= (.[0].indexed = false | .[1].indexed = true)" 'A9 event UnstakeRequested\(address,uint256,uint256\) .*differs from the reference'
abi_red "indexed dropped from user" ssv_seed_stake_monitor.json \
  "($UNST) |= (.[0].indexed = false)" 'A9 event UnstakeRequested\(address,uint256,uint256\) .*differs from the reference'
abi_red "input type changed (amount uint256 -> uint64)" ssv_seed_stake_monitor.json \
  "($UNST) |= (.[1].type = \"uint64\")" 'A9 event UnstakeRequested\(address,uint64,uint256\) .*not in the reference ABI \(reference has: UnstakeRequested\(address,uint256,uint256\)\)'
abi_red "input order swapped (same types, names reordered)" ssv_seed_stake_monitor.json \
  "($UNST) |= ([.[0], .[2], .[1]])" 'A9 event UnstakeRequested\(address,uint256,uint256\) .*differs from the reference'
abi_red "input order swapped (types reordered)" ssv_seed_stake_monitor.json \
  "($UNST) |= ([.[1], .[0], .[2]])" 'A9 event UnstakeRequested\(uint256,address,uint256\) .*not in the reference ABI'
abi_red "extra input" ssv_seed_stake_monitor.json \
  "($UNST) += [{\"name\":\"extra\",\"type\":\"uint256\",\"indexed\":false}]" 'A9 event UnstakeRequested\(address,uint256,uint256,uint256\) .*not in the reference ABI'
abi_red "missing input" ssv_seed_stake_monitor.json \
  "($UNST) |= .[0:2]" 'A9 event UnstakeRequested\(address,uint256\) .*not in the reference ABI'
abi_red "input renamed (expressions refer to input names)" ssv_seed_stake_monitor.json \
  "($UNST) |= (.[0].name = \"account\")" 'A9 event UnstakeRequested\(address,uint256,uint256\) .*differs from the reference'
abi_red "event anonymous flag flipped" ssv_seed_stake_monitor.json \
  '(.addresses[0].contract_spec[] | select(.type=="event" and .name=="FeesSynced") | .anonymous) = true' 'A9 event FeesSynced\(uint256,uint256\) .*differs from the reference'
abi_red "function output changed (Safe execTransaction success bool -> uint8)" ssv_dao_safe_reverted_monitor.json \
  '(.addresses[0].contract_spec[] | select(.type=="function" and .name=="execTransaction") | .outputs[0].type) = "uint8"' 'A9 function execTransaction\(.*differs from the reference'
abi_red "function output removed (proxy registerOperator)" ssv_oracle_success_monitor.json \
  '(.addresses[0].contract_spec[] | select(.type=="function" and .name=="registerOperator") | .outputs) = []' 'A9 function registerOperator\(.*differs from the reference'
abi_red "function input type changed on an unmonitored entry still fails (sweep covers the whole contract_spec)" ssv_dao_success_monitor.json \
  '(.addresses[0].contract_spec[] | select(.type=="function" and .name=="getVersion")) |= (.inputs = [{"name":"x","type":"bool"}])' 'A9 function getVersion\(bool\) .*not in the reference ABI'
abi_red "an extra entry that is not in the contract at all" ssv_oracle_success_monitor.json \
  '.addresses[0].contract_spec += [{"type":"event","name":"Sneaky","anonymous":false,"inputs":[]}]' 'A9 event Sneaky\(\) .*not in the reference ABI'
abi_red "contract_spec at an address the reference does not know" ssv_oracle_success_monitor.json \
  '.addresses[0].address = "0x000000000000000000000000000000000000dEaD"' 'A8 address 0x000000000000000000000000000000000000dEaD carries a contract_spec but has no entry'
r=$(fresh abi-ref); mutate "$r/deploy/abi/reference.json" \
  '(.contracts["0xdd9bc35ae942ef0cfa76930954a156b3ff30a4e1"].abi[] | select(.type=="event" and .name=="UnstakeRequested") | .inputs[1].indexed) = true'
expect "the reference, not the monitor, decides: a changed reference turns the tracked monitors red" nonzero 'A9 event UnstakeRequested' -- bash "$r/deploy/check-abi.sh" --config-dir "$r/deploy/config"
expect "missing reference file" nonzero 'reference ABI .* is missing, unparsable or empty' -- bash "$ROOT/deploy/check-abi.sh" --config-dir "$ROOT/deploy/config" --reference "$TMP/no-such-reference.json"
echo '{"contracts":{}}' > "$TMP/empty-ref.json"
expect "empty reference" nonzero 'reference ABI .* is missing, unparsable or empty' -- bash "$ROOT/deploy/check-abi.sh" --config-dir "$ROOT/deploy/config" --reference "$TMP/empty-ref.json"
r=$(fresh abi-restored)
expect "restored (unmutated) copy is green and compares every contract_spec entry" zero 'abi-check: OK \(32 monitors.*[0-9]+ contract_spec events/functions equal the reference layout' -- bash "$r/deploy/check-abi.sh" --config-dir "$r/deploy/config"
if [ -x "$BIN" ]; then
  r=$(fresh abi-render); mutate "$r/deploy/config/monitors/ssv_seed_stake_monitor.json" "($UNST) |= (.[0].indexed = false | .[1].indexed = true)"
  expect "render.sh exits non-zero on the swapped indexed layout" nonzero 'A9 event UnstakeRequested' -- env MONITOR_BIN="$BIN" bash "$r/deploy/render.sh" --out "$TMP/abi-layout-out/config"
else
  skipped=$((skipped + 1)); echo "SKIP  render.sh indexed-layout case (no $BIN)"
fi
expect "mutation sweep: every kind x every contract_spec entry of every monitor is red, exactly one A9 per mutated entry" zero 'abi-mutation-sweep: [0-9]+ checker runs, 0 failure' -- bash "$ROOT/deploy/tests/abi-mutation-sweep.sh"

echo "== F9: every one of the 17 M15 events is individually required (spec names + ModuleUpgraded)"
# Independent of the utility: the same 17 names read straight from the spec text.
m15names=$( { awk '/^### M15 /{on=1; next} /^### M16 /{on=0} on' "$ROOT/docs/ssv-specs/monitoring-service-spec.md" | grep -o '`[A-Z][A-Za-z0-9]*`' | tr -d '`'; echo ModuleUpgraded; } | sort -u)
[ "$(printf '%s\n' "$m15names" | wc -l | tr -d ' ')" = 17 ] && ok "spec M15 names plus ModuleUpgraded are 17" || bad "expected 17 M15 names, got: $(printf '%s ' $m15names)"
for ev in $m15names; do
  sig=$(jq -r --arg e "$ev" '.match_conditions.events[] | select(.signature | split("(")[0] == $e) | .signature' "$m/ssv_dao_discord_success_monitor.json" | head -1)
  if [ -z "$sig" ]; then bad "M15 event $ev is missing from the tracked Discord DAO monitor"; continue; fi
  r=$(fresh "m15del-$ev")
  mutate "$r/deploy/config/monitors/ssv_dao_discord_success_monitor.json" ".match_conditions.events |= map(select(.signature != \"$sig\"))"
  expect "deleting $sig from the Discord DAO monitor is red" nonzero "missing: $ev\\(" -- bash "$r/deploy/check-m15-coverage.sh"
  rm -rf "$r"
done
r=$(fresh m15types)
mutate "$r/deploy/config/monitors/ssv_dao_discord_success_monitor.json" '(.match_conditions.events[] | select(.signature == "QuorumUpdated(uint16)") | .signature) = "QuorumUpdated(uint32)"'
expect "an M15 event under the wrong types (QuorumUpdated(uint32)) is red" nonzero 'missing: QuorumUpdated\(uint16\)' -- bash "$r/deploy/check-m15-coverage.sh"
echo "== D2: the Telegram DAO monitor carries no unconditional M15 info event"
n=$(jq --argjson names "$(printf '%s\n' $m15names | jq -R . | jq -s -c .)" '[.match_conditions.events[]? | select(.expression == null) | select((.signature | split("(")[0]) as $e | $names | index($e))] | length' "$m/ssv_dao_success_monitor.json")
[ "$n" = 0 ] && ok "tracked Telegram DAO success monitor has 0 unconditional M15 events" || bad "tracked Telegram DAO success monitor has $n unconditional M15 events"
for ev in $m15names; do
  sig=$(jq -r --arg e "$ev" '.match_conditions.events[] | select(.signature | split("(")[0] == $e) | .signature' "$m/ssv_dao_discord_success_monitor.json" | head -1)
  [ -n "$sig" ] || continue
  r=$(fresh "m15tg-$ev")
  mutate "$r/deploy/config/monitors/ssv_dao_success_monitor.json" ".match_conditions.events += [{\"signature\":\"$sig\",\"expression\":null}]"
  expect "adding $sig to the Telegram DAO monitor is red" nonzero "M15/M10 info on a Telegram route" -- bash "$r/deploy/check-m15-coverage.sh"
  rm -rf "$r"
done

echo "== D11: the six *_SUCCESS Discord triggers use DISCORD_WEBHOOK_INFO; the nine others keep their own variable"
T=$ROOT/deploy/config/triggers
SIX="cluster dao liquidation operator oracle validator"
for n in $SIX; do
  v=$(jq -r ".discord_${n}_success.config.discord_url.value" "$T/discord_${n}_success.json")
  [ "$v" = DISCORD_WEBHOOK_INFO ] && ok "discord_${n}_success -> DISCORD_WEBHOOK_INFO" || bad "discord_${n}_success uses $v"
done
own=$(jq -r 'to_entries[] | select(.value.trigger_type == "discord" and (.key | test("_success$|_other_sender$") | not)) | .value.config.discord_url.value' "$T"/*.json | sort)
[ "$(printf '%s\n' "$own" | wc -l | tr -d ' ')" = 9 ] && [ "$(printf '%s\n' "$own" | uniq -d | wc -l | tr -d ' ')" = 0 ] && ! grep -qx DISCORD_WEBHOOK_INFO <<<"$own" \
  && ok "nine other Discord triggers: nine distinct own variables, none is the info one" || bad "other Discord trigger variables: $(printf '%s ' $own)"
n=$(jq -r 'to_entries[] | select(.value.trigger_type == "discord") | .value.name' "$T"/*.json | sort | uniq -d | wc -l | tr -d ' ')
[ "$n" = 0 ] && ok "Discord trigger display names stay unique" || bad "duplicate Discord trigger names"
gone="DISCORD_WEBHOOK_(CLUSTER|DAO|LIQUIDATION|OPERATOR|ORACLE|VALIDATOR)_SUCCESS"
# grep: 0 = found, 1 = none, anything else = the scan failed (that is not "none")
grep -rEn "$gone" "$ROOT/deploy" "$ROOT/.env.example" "$ROOT/docker-compose.yaml" "$ROOT/docker-compose.local.yaml" "$ROOT/scripts" >/dev/null 2>&1; rc=$?
case $rc in
  1) ok "no removed *_SUCCESS webhook variable name anywhere in deploy/, .env.example, docker-compose*.yaml, scripts/" ;;
  0) bad "a removed *_SUCCESS webhook variable name still appears in deploy/, .env.example, docker-compose*.yaml or scripts/" ;;
  *) bad "the search for removed *_SUCCESS webhook variable names failed (grep exit $rc), which is not a pass" ;;
esac
for n in $SIX; do
  r=$(fresh "d11del-$n")
  mutate "$r/deploy/config/triggers/discord_${n}_success.json" "del(.discord_${n}_success.config.discord_url)"
  expect "discord_${n}_success without discord_url is red" nonzero "discord_${n}_success: config.discord_url must be" -- bash "$r/deploy/check-templates.sh"
  r=$(fresh "d11plain-$n")
  mutate "$r/deploy/config/triggers/discord_${n}_success.json" ".discord_${n}_success.config.discord_url = {\"type\":\"plain\",\"value\":\"x\"}"
  expect "discord_${n}_success with a plain discord_url is red" nonzero "discord_${n}_success: config.discord_url must be" -- bash "$r/deploy/check-templates.sh"
  r=$(fresh "d11own-$n")
  mutate "$r/deploy/config/triggers/discord_${n}_success.json" ".discord_${n}_success.config.discord_url.value = \"DISCORD_WEBHOOK_$(printf '%s' "$n" | tr a-z A-Z)_SUCCESS\""
  expect "discord_${n}_success back on its own variable is red (D11)" nonzero "info trigger must use DISCORD_WEBHOOK_INFO" -- bash "$r/deploy/check-templates.sh"
done
r=$(fresh d11fail)
mutate "$r/deploy/config/triggers/discord_cluster_failed.json" '.discord_cluster_failed.config.discord_url.value = "DISCORD_WEBHOOK_INFO"'
expect "a *_FAILED trigger on the info variable is red (D11)" nonzero "only info triggers" -- bash "$r/deploy/check-templates.sh"
envfile=$(jq -r '.. | objects | select(.type? == "environment") | .value' "$T"/*.json "$ROOT"/deploy/config/networks/*.json | sort -u)
zero=0
strict_env() { # strict_env <skip-var>: every referenced variable with a shape-valid value, except <skip-var>
  local v; for v in $envfile; do [ "$v" = "${1:-}" ] && continue
    case "$v" in *RPC_URL*) echo "$v=https://rpc.invalid.example" ;; *WEBHOOK*) echo "$v=https://discord.com/api/webhooks/${zero}/placeholder" ;; *TOKEN*) echo "$v=0000000000:$(printf 'x%.0s' {1..35})" ;; *) echo "$v=placeholder" ;; esac
  done; echo "TELEGRAM_CHAT_ID=-100123"; }
for v in $envfile; do
  grep -qE "^      $v:\$" "$ROOT/docker-compose.yaml" || bad "docker-compose.yaml has no passthrough for $v"
  grep -qE "^$v=" "$ROOT/.env.example" || bad "\.env.example does not list $v"
done
extra=$(grep -E '^      (MAINNET|TELEGRAM|DISCORD)[A-Z_0-9]*:$' "$ROOT/docker-compose.yaml" | tr -d ' :' | grep -vxF "$envfile" || true)
[ -z "$extra" ] && ok "docker-compose.yaml passes through exactly the $(printf '%s\n' "$envfile" | wc -l | tr -d ' ') referenced variables (and .env.example lists each)" || bad "docker-compose.yaml passes through unreferenced variable(s): $extra"
if [ -x "$BIN" ]; then
  expect "render --strict with DISCORD_WEBHOOK_INFO unset is red" nonzero 'unset environment variables: DISCORD_WEBHOOK_INFO$' -- env -i PATH="$PATH" MONITOR_BIN="$BIN" $(strict_env DISCORD_WEBHOOK_INFO) bash "$ROOT/deploy/render.sh" --out "$TMP/d11-strict-red/config" --strict
  expect "render --strict with exactly the listed variables set is green (no removed variable is required)" zero '--check passed$' -- env -i PATH="$PATH" MONITOR_BIN="$BIN" $(strict_env) bash "$ROOT/deploy/render.sh" --out "$TMP/d11-strict-ok/config" --strict
else
  skipped=$((skipped + 2)); echo "SKIP  render --strict D11 cases (no binary at $BIN)"
fi

echo "== D20: Monitor endpoints and polling (soak-1 Monitor stall, event-0084)"
net=deploy/config/networks/evm_ethereum_mainnet.json
n=$(jq '[.rpc_urls[] | select(.url.type == "plain" and (.url.value | test("tenderly") | not))] | length' "$ROOT/$net")
[ "${n:-0}" -ge 2 ] && ok "the tracked network lists $n non-Tenderly literal endpoints (>= 2)" || bad "the tracked network lists ${n:-?} non-Tenderly literal endpoints"
second=$(jq -r '.cron_schedule | split(" ") | if length == 6 then .[0] else "bad" end' "$ROOT/$net")
case "$second" in [0-9]|[0-5][0-9]) ok "the tracked cron_schedule fires once a minute (second $second)" ;; *) bad "the tracked cron_schedule is not once a minute: $second" ;; esac
d20() { # d20 <name> <jq mutation> <expected FAIL pattern>
  local r; r=$(fresh "d20-$1"); mutate "$r/$net" "$2"
  expect "D20 $1 is red" nonzero "$3" -- bash "$r/deploy/check-templates.sh"
}
d20 cron2s '.cron_schedule = "*/2 * * * * *"' 'cron_schedule must be a 6-field cron'
d20 cron5field '.cron_schedule = "* * * * *"' 'cron_schedule must be a 6-field cron'
d20 onepublic 'del(.rpc_urls[] | select(.url.value == "https://rpc-eth.blockmachine.io"))' 'at least two non-Tenderly'
d20 tenderlyonly '(.rpc_urls[] | select(.url.type == "plain") | .url.value) = "https://gateway.tenderly.co/public/mainnet"' 'at least two non-Tenderly'
d20 adjacent '(.rpc_urls[] | select(.url.value == "https://rpc.mevblocker.io") | .weight) = 75' 'both possibly Tenderly'
d20 keyedplain '(.rpc_urls[] | select(.url.value == "https://rpc.mevblocker.io") | .url.value) = "https://eth-mainnet.example/v1/k3y"' 'S2 plain value present'
d20 plainprimary '.rpc_urls[0].url = {"type":"plain","value":"https://rpc.mevblocker.io"}' 'primary RPC \(weight 100\) must be MAINNET_RPC_URL'
d20 sameweight '(.rpc_urls[] | select(.url.value == "https://rpc.mevblocker.io") | .weight) = 70' 'weights must be unique'
d20 pastblocks '.max_past_blocks = 3600' 'max_past_blocks must be <= 100'
d20 recoverysecond '.recovery_config.cron_schedule = "0 */5 * * * *"' 'fires on the same second'
if [ -x "$BIN" ]; then
  # a literal (plain) URL cannot be unset: with only MAINNET_RPC_URL set the render keeps both public endpoints
  expect "render with MAINNET_RPC_URL_2 unset keeps the two literal endpoints and passes --check" zero '--check passed' -- env -u MAINNET_RPC_URL_2 MONITOR_BIN="$BIN" TELEGRAM_CHAT_ID=-100123 bash "$ROOT/deploy/render.sh" --out "$TMP/d20-render/config"
  n=$(jq '[.rpc_urls[] | .url.value] | join(",")' "$TMP/d20-render/config/networks/evm_ethereum_mainnet.json")
  [ "$n" = '"MAINNET_RPC_URL,https://rpc.mevblocker.io,https://rpc-eth.blockmachine.io"' ] && ok "rendered rotation without MAINNET_RPC_URL_2: $n" || bad "rendered rotation without MAINNET_RPC_URL_2: $n"
else
  skipped=$((skipped + 2)); echo "SKIP  render D20 cases (no binary at $BIN)"
fi

finish
