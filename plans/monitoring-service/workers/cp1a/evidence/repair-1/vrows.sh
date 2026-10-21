#!/usr/bin/env bash
# CP1A repair 1: re-run of V1A.1-V1A.8 (+ falsifiers) on the repaired head. Read-only against mainnet.
# Output: one "ROW" line per measurement (exit code + counts); the full run log is stdout.
#   RPC_RECENT=https://ethereum-rpc.publicnode.com   RPC_ARCHIVE=https://eth.drpc.org
set -uo pipefail
cd "$(git rev-parse --show-toplevel)"
export PATH="$HOME/.foundry/bin:$PATH"
RPC_RECENT=${RPC_RECENT:-https://ethereum-rpc.publicnode.com}
RPC_ARCHIVE=${RPC_ARCHIVE:-https://eth.drpc.org}
BIN=target/release/openzeppelin-monitor
D=.local-dry
strip() { sed $'s/\x1b\\[[0-9;]*m//g'; }
row() { echo "ROW | $*"; }
zero=0 # keeps a webhook-shaped literal out of this tracked file (the V1A.2 grep matches webhooks/<digits>/)
env_for_config() { # dummy values for every environment name the rendered config references
  local cfg=$1 v
  for v in $(jq -r '..|objects|select(.type?=="environment")|.value' $cfg/networks/*.json $cfg/triggers/*.json | sort -u); do
    case $v in *RPC_URL*) echo "$v=https://rpc.invalid.example";; *WEBHOOK*) echo "$v=https://discord.com/api/webhooks/${zero}/placeholder";; *TOKEN*) echo "$v=0000000000:$(printf 'x%.0s' {1..35})";; *) echo "$v=placeholder";; esac
  done
}
lines() { [ -f "$1" ] && wc -l < "$1" | tr -d ' ' || echo 0; }
# oneshot <label> <rpc> <monitor> <block> [direct]: direct = run the binary on the (possibly mutated) rendered config
oneshot() {
  local label=$1 rpc=$2 mon=$3 blk=$4 direct=${5:-} out rc m0 i0
  m0=$(lines $D/data/matches.jsonl); i0=$(lines $D/data/inbox/failed-tx.jsonl)
  if [ -n "$direct" ]; then
    (cd $D && MAINNET_RPC_URL=$rpc ../$BIN --monitor-path config/monitors/$mon.json --network evm_ethereum_mainnet --block $blk >/tmp/vrows-one.log 2>&1 </dev/null); rc=$?
  else
    MAINNET_RPC_URL=$rpc bash scripts/local-dry-run.sh --monitor-path config/monitors/$mon.json --network evm_ethereum_mainnet --block $blk >/tmp/vrows-one.log 2>&1 </dev/null; rc=$?
  fi
  out=$(strip </tmp/vrows-one.log)
  row "$label | endpoint $rpc | $mon @ $blk | exit $rc | $(grep -o 'Found matches, total: [0-9]*' <<<"$out" | grep -o '[0-9]*$') match(es) | matches.jsonl +$(( $(lines $D/data/matches.jsonl) - m0 )) | inbox +$(( $(lines $D/data/inbox/failed-tx.jsonl) - i0 )) | $(grep -c ' ERROR ' <<<"$out") ERROR"
}

echo "HEAD $(git rev-parse HEAD)"; git status --porcelain | sed 's/^/pre-status: /'
[ -x $BIN ] || { echo "build first"; exit 2; }

echo "### V1A.1 render gate + raw check"
MAINNET_RPC_URL=$RPC_RECENT bash deploy/render.sh --out $D/config > /tmp/vrows-render.log 2>&1; rc=$?
row "V1A.1 render | exit $rc | $(grep -o 'Found [0-9]* active monitor' /tmp/vrows-render.log) | $(grep -c ' ERROR ' /tmp/vrows-render.log) ERROR | $(grep -c 'abi-check: OK' /tmp/vrows-render.log) abi-check OK"
(cd $D && env $(env_for_config config) ../$BIN --check >/tmp/vrows-raw.log 2>&1); rc=$?; out=$(strip </tmp/vrows-raw.log)
row "V1A.1 raw --check | exit $rc | success-line $(grep -c 'Configuration validation completed successfully' <<<"$out") | $(grep -c ' ERROR ' <<<"$out") ERROR | $(grep -o 'Found [0-9]* active monitor' <<<"$out")"

echo "### V1A.1f falsifier: brace removed from a rendered monitor (raw exit 0 + ERROR; gate = render of a mutated template)"
m=$D/config/monitors/ssv_seed_stake_monitor.json; cp $m /tmp/vrows-seed.bak
python3 - "$m" <<'PY'
import sys; p=sys.argv[1]; s=open(p).read().rstrip(); assert s.endswith('}'); open(p,'w').write(s[:-1])
PY
(cd $D && env $(env_for_config config) ../$BIN --check >/tmp/vrows-raw.log 2>&1); rc=$?; out=$(strip </tmp/vrows-raw.log)
row "V1A.1f raw --check, brace removed | exit $rc | success-line $(grep -c 'Configuration validation completed successfully' <<<"$out") | $(grep -c ' ERROR ' <<<"$out") ERROR"
cp /tmp/vrows-seed.bak $m
t=deploy/config/monitors/ssv_seed_stake_monitor.json; cp $t /tmp/vrows-tpl.bak
python3 - "$t" <<'PY'
import sys; p=sys.argv[1]; s=open(p).read().rstrip(); open(p,'w').write(s[:-1])
PY
MAINNET_RPC_URL=$RPC_RECENT bash deploy/render.sh --out $D/config > /tmp/vrows-render-f.log 2>&1; rc=$?
row "V1A.1f render.sh, template brace removed | exit $rc | $(grep -m1 -E 'FAIL|ERROR' /tmp/vrows-render-f.log | cut -c1-140)"
cp /tmp/vrows-tpl.bak $t; git checkout -q -- $t
MAINNET_RPC_URL=$RPC_RECENT bash deploy/render.sh --out $D/config > /tmp/vrows-render-r.log 2>&1; rc=$?
row "V1A.1f restored render.sh | exit $rc | porcelain '$(git status --porcelain)'"

# the Monitor-ERROR half of the render.sh gate (event-0013 semantics: success line + zero ERROR lines) must
# still trip on its own: a dangling trigger is valid JSON, valid against the ABI, and only the Monitor rejects it
jq '.triggers=["no_such_trigger"]' $t > $t.tmp && mv $t.tmp $t
MAINNET_RPC_URL=$RPC_RECENT bash deploy/render.sh --out $D/config > /tmp/vrows-render-d.log 2>&1; rc=$?
row "V1A.1f render.sh, dangling trigger (abi-check passes, Monitor ERROR trips the gate) | exit $rc | abi-check OK $(grep -c 'abi-check: OK' /tmp/vrows-render-d.log) | $(grep -c ' ERROR ' /tmp/vrows-render-d.log) ERROR line(s) | success-line $(grep -c 'Configuration validation completed successfully' /tmp/vrows-render-d.log)"
git checkout -q -- $t
echo "### V1A.1 ABI falsifier (F8): one-field mutation Nonexistent(uint256) in the TRACKED template, restored by git checkout"
t=deploy/config/monitors/ssv_seed_stake_monitor.json
jq '.match_conditions.events[0].signature="Nonexistent(uint256)"' $t > $t.tmp && mv $t.tmp $t
row "F8 mutation diff | $(git diff --stat -- $t | tail -1)"
MAINNET_RPC_URL=$RPC_RECENT bash deploy/render.sh --out $D/config > /tmp/vrows-abi-red.log 2>&1; rc=$?
row "F8 render.sh mutated (must be non-zero) | exit $rc | $(grep -m1 'A3 event' /tmp/vrows-abi-red.log)"
bash deploy/check-templates.sh > /tmp/vrows-abi-red2.log 2>&1; rc=$?; row "F8 check-templates.sh mutated | exit $rc | $(grep -m1 'A3 event' /tmp/vrows-abi-red2.log)"
git checkout -q -- $t
MAINNET_RPC_URL=$RPC_RECENT bash deploy/render.sh --out $D/config > /tmp/vrows-abi-green.log 2>&1; rc=$?
row "F8 render.sh restored | exit $rc | $(grep -o 'Found [0-9]* active monitor' /tmp/vrows-abi-green.log) | porcelain '$(git status --porcelain)'"

echo "### V1A.2"
plain=$(jq -r '..|objects|select(.type?=="plain")|.value' deploy/config/triggers/*.json deploy/config/networks/*.json | wc -l | tr -d ' ')
git grep -nE 'discord\.com/api/webhooks/[0-9]+/|[0-9]{8,10}:AA[A-Za-z0-9_-]{30,}' -- . ':!tests/integration/fixtures' >/tmp/vrows-grep.log; grc=$?
row "V1A.2 plain values $plain | secret-shape grep exit $grc ($(wc -l </tmp/vrows-grep.log | tr -d ' ') rows)"
bash deploy/check-templates.sh > /tmp/vrows-chk.log 2>&1; row "V1A.2 checker | exit $? | $(tail -1 /tmp/vrows-chk.log)"

echo "### F2 the QA's three exact missing-input commands (must be non-zero) + monitor-field deletions"
mkdir -p /tmp/vrows-empty
bash deploy/check-templates.sh --config-dir /tmp/vrows-absent > /tmp/vrows-f2a.log 2>&1; row "F2 absent --config-dir | exit $? | $(head -1 /tmp/vrows-f2a.log | cut -c1-90)"
bash deploy/check-templates.sh --config-dir /tmp/vrows-empty > /tmp/vrows-f2b.log 2>&1; row "F2 empty --config-dir | exit $? | $(head -1 /tmp/vrows-f2b.log | cut -c1-90)"
bash deploy/check-templates.sh --root /tmp/vrows-missing-root --config-dir "$PWD/deploy/config" > /tmp/vrows-f2c.log 2>&1; row "F2 missing --root | exit $? | $(head -1 /tmp/vrows-f2c.log | cut -c1-90)"
for k in networks match_conditions; do
  t=deploy/config/monitors/ssv_seed_stake_monitor.json; jq "del(.$k)" $t > $t.tmp && mv $t.tmp $t
  bash deploy/check-templates.sh > /tmp/vrows-f2d.log 2>&1; row "F2 monitor without $k | exit $? | $(head -1 /tmp/vrows-f2d.log | cut -c1-110)"
  git checkout -q -- $t
done
bash deploy/check-templates.sh > /tmp/vrows-f2e.log 2>&1; row "F2 restored | exit $? | $(tail -1 /tmp/vrows-f2e.log) | porcelain '$(git status --porcelain -- deploy)'"

echo "### V1A.3 inbox"
oneshot "V1A.3 reverted liquidate" $RPC_ARCHIVE ssv_failed_tx_inbox 26239237
oneshot "V1A.3 control (successful liquidate)" $RPC_ARCHIVE ssv_failed_tx_inbox 26239259
oneshot "V1A.3 implementer block" $RPC_ARCHIVE ssv_failed_tx_inbox 26239255
tail -n 1 $D/data/inbox/failed-tx.jsonl | jq -c 'keys|length' | sed 's/^/ROW | V1A.3 last inbox line key count (expect 11): /'

echo "### V1A.4 / V1A.4f other-sender"
oneshot "V1A.4 other sender 0x28bE update" $RPC_ARCHIVE ssv_updater_other_sender_monitor 26177496
oneshot "V1A.4f own-sender block" $RPC_ARCHIVE ssv_updater_other_sender_monitor 26239694
f=$D/config/monitors/ssv_updater_other_sender_monitor.json; cp $f /tmp/vrows-m11.bak
jq '.match_conditions.transactions[0].expression=null' /tmp/vrows-m11.bak > $f
oneshot "V1A.4f sender predicate removed (control must go red)" $RPC_ARCHIVE ssv_updater_other_sender_monitor 26239694 direct
cp /tmp/vrows-m11.bak $f
oneshot "V1A.4f restored" $RPC_ARCHIVE ssv_updater_other_sender_monitor 26239694 direct

echo "### V1A.5 retired addresses (A) + F4 outgoing"
oneshot "V1A.5 incoming to retired slot 4 (4192795588647 wei)" $RPC_ARCHIVE ssv_retired_addresses_monitor 25991146
oneshot "F4 outgoing from retired slot 4 (must be 0)" $RPC_ARCHIVE ssv_retired_addresses_monitor 26169289
oneshot "M13 old slot-4 key commitRoot attempt" $RPC_ARCHIVE ssv_retired_updater_attempt_monitor 26167892
oneshot "M13 attempt monitor control (other senders)" $RPC_ARCHIVE ssv_retired_updater_attempt_monitor 26239655

echo "### V1A.6 M8 FeesSynced pipeline proof"
f=$D/config/monitors/ssv_seed_stake_monitor.json; cp $f /tmp/vrows-seed.bak
oneshot "V1A.6 baseline (== 0)" $RPC_ARCHIVE ssv_seed_stake_monitor 26240350 direct
jq '.match_conditions.events[0].expression="newFeesWei > 0"' /tmp/vrows-seed.bak > $f
oneshot "V1A.6 mutated (> 0, expect 1)" $RPC_ARCHIVE ssv_seed_stake_monitor 26240350 direct
cp /tmp/vrows-seed.bak $f
oneshot "V1A.6 restored (== 0)" $RPC_ARCHIVE ssv_seed_stake_monitor 26240350 direct
row "V1A.6 porcelain after | '$(git status --porcelain)'"

echo "### V1A.7 / F3"
bash deploy/check-m15-coverage.sh > /tmp/vrows-m15.log 2>&1; row "V1A.7 check-m15-coverage | exit $? | $(grep -c 'extra' /tmp/vrows-m15.log) extras | $(grep -E 'all M15|no M15/M10' /tmp/vrows-m15.log | tr '\n' ';')"
B=$((0x18f4eea))
oneshot "F3 real OracleReplaced block, Telegram DAO monitor (must be 0)" $RPC_ARCHIVE ssv_dao_success_monitor $B direct
oneshot "F3 real OracleReplaced block, Discord DAO monitor (must be 1)" $RPC_ARCHIVE ssv_dao_discord_success_monitor $B direct

echo "### V1A.8 compose"
docker compose -f docker-compose.yaml config --format json > /tmp/vrows-compose.json 2>/tmp/vrows-compose.err; rc=$?
row "V1A.8 docker compose config | exit $rc | volumes $(jq -c '[.services[].volumes[]?|select(.target=="/app/data")|.source]' /tmp/vrows-compose.json)"
jq -c '{max_past_blocks, recovery_config, rpc_urls:(.rpc_urls|length)}' $D/config/networks/evm_ethereum_mainnet.json | sed 's/^/ROW | V1A.8 rendered network: /'

echo "### selftest + fork regression"
bash deploy/tests/selftest.sh | tail -2 | sed 's/^/ROW | selftest: /'
FORK_URL=$RPC_ARCHIVE bash deploy/tests/m13-fork-regression.sh | tail -1 | sed 's/^/ROW | fork regression: /'
git status --porcelain | sed 's/^/post-status: /'; echo done
