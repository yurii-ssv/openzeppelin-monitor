#!/usr/bin/env bash
# M13 regression on a LOCAL anvil fork (CP1A repair 1: F1, F4). Local transactions only: accounts are
# impersonated on the fork, nothing is ever sent to mainnet and no notification channel is used
# (scripts/local-dry-run.sh drops every delivery trigger unless TEST_* variables are set; none are).
#
#   deploy/tests/m13-fork-regression.sh            (needs anvil, cast, jq, target/release/openzeppelin-monitor)
#
# Env: FORK_URL (archive RPC, default https://eth.drpc.org), FORK_BLOCK (default 26240350),
#      PORT (default 28545).
# For every case it sends one transaction on the fork, replays the mined block through the three
# M13 monitors with the Monitor's one-shot mode and compares the match counts with the expectation.
# Exit 0 only if every case and every ERROR count is as expected. Expected counts are the spec's:
#   retired keys: incoming value pages, outgoing does not (M13 rows 1-2; F4)
#   old slot-4 key: a commitRoot/updateClusterBalance attempt pages, even reverted (row 1)
#   attacker addresses: ANY interaction with an SSV contract or our wallets pages, zero value and
#   reverted attempts included (row 3; F1)
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
export PATH="$HOME/.foundry/bin:$PATH"
FORK_URL=${FORK_URL:-https://eth.drpc.org}
FORK_BLOCK=${FORK_BLOCK:-26240350}
PORT=${PORT:-28545}
RPC=http://127.0.0.1:$PORT
for c in anvil cast jq; do command -v "$c" >/dev/null || { echo "missing: $c" >&2; exit 2; }; done
[ -x "$ROOT/target/release/openzeppelin-monitor" ] || { echo "build first: cargo build --release --locked" >&2; exit 2; }

A1=0xaeE058cCA5EED5470b4b48c159045b7C25ddb80f      # attacker
A2=0x0144aE2456FbCF38Ba11e4ECF25F03A80A69F134      # consolidation
OLD4=0x3A98EE5f80268Ed91F8A5880d93468b76a9F3bB4    # retired slot 4
OLDLIQ=0x4BC656B34De23896fa6069C9862F355b740401aF  # retired liquidator
PROXY=0xDD9BC35aE942eF0cFa76930954a156B3fF30a4E1
VIEWS=0xafE830B6Ee262ba11cce5F32fDCd760FFE6a66e4
W4=0x16A503C901615E6e21AA78cFcaFf11822fc2b617      # our oracle/updater wallet
LIQ=0x0431371AD8eDA67BA2EF6489aB003B3A7F0d95E0     # our liquidator wallet
OTHER=0x00000000000000000000000000000000000dEaD1   # unrelated EOA
OTHER2=0x00000000000000000000000000000000000dEaD2
COMMIT=$(cast calldata 'commitRoot(bytes32,uint64)' 0x0000000000000000000000000000000000000000000000000000000000000000 0)

LOG=$(mktemp -d)
anvil --fork-url "$FORK_URL" --fork-block-number "$FORK_BLOCK" --port "$PORT" --silent >"$LOG/anvil.log" 2>&1 &
ANVIL=$!
trap 'kill $ANVIL 2>/dev/null; wait $ANVIL 2>/dev/null; rm -rf "$LOG"' EXIT
for _ in $(seq 1 60); do cast chain-id --rpc-url "$RPC" >/dev/null 2>&1 && break; sleep 0.5; done
cast chain-id --rpc-url "$RPC" >/dev/null 2>&1 || { echo "anvil did not start (fork url $FORK_URL)" >&2; cat "$LOG/anvil.log" >&2; exit 2; }

for a in $A1 $A2 $OLD4 $OLDLIQ $W4 $LIQ $OTHER $OTHER2; do
  cast rpc --rpc-url "$RPC" anvil_impersonateAccount "$a" >/dev/null
  cast rpc --rpc-url "$RPC" anvil_setBalance "$a" 0xde0b6b3a7640000 >/dev/null
done

# Render once without deliveries (no TEST_* set), against the fork.
env -u TEST_DISCORD_WEBHOOK -u TEST_TELEGRAM_TOKEN -u TEST_TELEGRAM_CHAT_ID MAINNET_RPC_URL="$RPC" \
  bash "$ROOT/scripts/local-dry-run.sh" --prepare >/dev/null 2>&1 || { echo "local-dry-run --prepare failed" >&2; exit 2; }

matches() { # matches <monitor> <block> -> "<count> <errors>"
  local out
  out=$(cd "$ROOT/.local-dry" && MAINNET_RPC_URL="$RPC" ../target/release/openzeppelin-monitor \
    --monitor-path "config/monitors/$1.json" --network evm_ethereum_mainnet --block "$2" 2>&1 </dev/null \
    | sed $'s/\x1b\\[[0-9;]*m//g')
  echo "$(grep -o 'Found matches, total: [0-9]*' <<<"$out" | grep -o '[0-9]*$' || true) $(grep -c ' ERROR ' <<<"$out" || true)"
}

fail=0; n=0
# case <label> <from> <to> <value_wei> <data> <expect A retired-incoming> <expect B old-key-attempt> <expect C attacker-interaction>
case_() {
  local label=$1 from=$2 to=$3 value=$4 data=$5 eA=$6 eB=$7 eC=$8 tx blk status
  tx=$(cast rpc --rpc-url "$RPC" eth_sendTransaction \
    "{\"from\":\"$from\",\"to\":\"$to\",\"value\":\"$(printf '0x%x' "$value")\",\"data\":\"$data\",\"gas\":\"0x7a120\"}" | tr -d '"')
  blk=""; for _ in $(seq 1 40); do
    blk=$(cast rpc --rpc-url "$RPC" eth_getTransactionReceipt "$tx" | jq -r '.blockNumber // empty'); [ -n "$blk" ] && break; sleep 0.25
  done
  [ -n "$blk" ] || { echo "FAIL $label: no receipt for $tx"; fail=$((fail + 1)); return; }
  blk=$((blk)); status=$(cast rpc --rpc-url "$RPC" eth_getTransactionReceipt "$tx" | jq -r '.status')
  local ra rb rc; ra=$(matches ssv_retired_addresses_monitor "$blk"); rb=$(matches ssv_retired_updater_attempt_monitor "$blk"); rc=$(matches ssv_attacker_interaction_monitor "$blk")
  n=$((n + 1))
  local ok=ok
  [ "$ra" = "$eA 0" ] && [ "$rb" = "$eB 0" ] && [ "$rc" = "$eC 0" ] || { ok=FAIL; fail=$((fail + 1)); }
  printf '%-4s %-46s block %s status %s  A=%s B=%s C=%s (expected %s/%s/%s, each "matches errors")\n' "$ok" "$label" "$blk" "$status" "${ra% *}/${ra#* }" "${rb% *}/${rb#* }" "${rc% *}/${rc#* }" "$eA" "$eB" "$eC"
}

#     label                                              from    to       value  data     A B C
case_ "F1 attacker1 -> proxy commitRoot, 0 wei (reverts)" $A1    $PROXY   0      "$COMMIT" 0 0 1
case_ "F1 attacker2 -> proxy, empty call, 0 wei"          $A2    $PROXY   0      0x        0 0 1
case_ "F1 attacker2 -> Views, 0 wei"                      $A2    $VIEWS   0      0x        0 0 1
case_ "F1 attacker1 -> our oracle wallet, 0 wei"          $A1    $W4      0      0x        0 0 1
case_ "F1 attacker2 -> our liquidator wallet, 1 wei"      $A2    $LIQ     1      0x        0 0 1
case_ "F1 our liquidator wallet -> attacker1, 0 wei"      $LIQ   $A1      0      0x        0 0 1
case_ "F1 control: unrelated -> proxy, 0 wei"             $OTHER $PROXY   0      0x        0 0 0
case_ "F1 control: attacker1 -> unrelated, 1 wei"         $A1    $OTHER   1      0x        0 0 0
case_ "F4 retired slot 4 -> unrelated, 84457 wei (out)"   $OLD4  $OTHER   84457  0x        0 0 0
case_ "F4 retired liquidator -> unrelated, 1 wei (out)"   $OLDLIQ $OTHER  1      0x        0 0 0
case_ "M13 unrelated -> retired slot 4, 1 wei (in)"       $OTHER $OLD4    1      0x        1 0 0
case_ "M13 unrelated -> retired liquidator, 1 wei (in)"   $OTHER2 $OLDLIQ 1      0x        1 0 0
case_ "M13 unrelated -> retired liquidator, 0 wei"        $OTHER2 $OLDLIQ 0      0x        0 0 0
case_ "M13 retired slot 4 commitRoot attempt (reverts)"   $OLD4  $PROXY   0      "$COMMIT" 0 1 0
case_ "M13 other sender commitRoot (control)"             $OTHER $PROXY   0      "$COMMIT" 0 0 0

echo "m13-fork-regression: $n cases, $fail failure(s), fork $FORK_URL @ $FORK_BLOCK"
[ $fail -eq 0 ]
