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

after=$(cd "$ROOT" && find deploy -type f -exec shasum {} + | sort | shasum)
[ "$before" = "$after" ] && ok "tracked deploy/ tree unchanged by the self-test" || bad "tracked deploy/ tree changed during the self-test"

echo "selftest: $pass passed, $failn failed, $skipped skipped"
[ $failn -eq 0 ]
