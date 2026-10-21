#!/usr/bin/env bash
# Offline self-test for the deploy-side gates (CP1A repair 1: F2, F3, F8, template structure of F1/F4;
# repair 2: F8b ABI layout gate against the committed reference).
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
command -v jq >/dev/null || { echo "jq is required" >&2; exit 2; }

TMP=$(mktemp -d)
trap 'chmod -R u+rwx "$TMP" 2>/dev/null; rm -rf "$TMP"' EXIT
before=$(cd "$ROOT" && find deploy -type f -exec shasum {} + | sort | shasum)
pass=0; failn=0; skipped=0

ok()   { pass=$((pass + 1)); printf 'ok    %s\n' "$1"; }
bad()  { failn=$((failn + 1)); printf 'FAIL  %s\n' "$1"; }
fresh() { # fresh <name>: a pristine copy of deploy/ + the M15 spec under $TMP/<name>; prints the root
  rm -rf "${TMP:?}/$1"; mkdir -p "$TMP/$1/docs/ssv-specs"
  cp -R "$ROOT/deploy" "$TMP/$1/deploy"
  cp "$ROOT/docs/ssv-specs/monitoring-service-spec.md" "$TMP/$1/docs/ssv-specs/"
  echo "$TMP/$1"
}
mutate() { # mutate <file> <jq filter>
  jq "$2" "$1" > "$1.tmp" && mv "$1.tmp" "$1"
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

echo "== baseline"
expect "checker on the tracked templates" zero 'checker: OK \(' -- bash "$ROOT/deploy/check-templates.sh"
expect "abi check on the tracked templates" zero 'abi-check: OK' -- bash "$ROOT/deploy/check-abi.sh" --config-dir "$ROOT/deploy/config"
expect "M15 coverage + D2 routing on the tracked templates" zero 'no M15/M10 info event' -- bash "$ROOT/deploy/check-m15-coverage.sh"

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
if grep -rEn "$gone" "$ROOT/deploy" "$ROOT/.env.example" "$ROOT/docker-compose.yaml" "$ROOT/docker-compose.local.yaml" "$ROOT/scripts" >/dev/null 2>&1; then
  bad "a removed *_SUCCESS webhook variable name still appears in deploy/, .env.example, docker-compose*.yaml or scripts/"
else ok "no removed *_SUCCESS webhook variable name anywhere in deploy/, .env.example, docker-compose*.yaml, scripts/"; fi
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

after=$(cd "$ROOT" && find deploy -type f -exec shasum {} + | sort | shasum)
[ "$before" = "$after" ] && ok "tracked deploy/ tree unchanged by the self-test" || bad "tracked deploy/ tree changed during the self-test"

echo "selftest: $pass passed, $failn failed, $skipped skipped"
[ $failn -eq 0 ]
