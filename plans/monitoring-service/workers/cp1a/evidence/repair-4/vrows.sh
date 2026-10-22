#!/usr/bin/env bash
# CP1A repair 4: re-run of V1A.1-V1A.8 (+ falsifiers), F8b, F9, D11 and the F10 rows on the repaired head. Read-only against mainnet.
# Needs a committed, clean tree (falsifiers mutate tracked templates and restore them with git checkout).
# Output: one "ROW" line per measurement (exit code + counts); the full run log is stdout.
#   RPC_RECENT=https://ethereum-rpc.publicnode.com   RPC_ARCHIVE=https://eth.drpc.org
#   optional: SSV_OUT=<forge out at the pin> SAFE_ABI=<gnosis_safe.json> OLD_ABI_CHECK=<repair-1 check-abi.sh> for derive --check and the red baseline
set -uo pipefail
cd "$(git rev-parse --show-toplevel)"
export PATH="$HOME/.foundry/bin:$PATH"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
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
    (cd $D && MAINNET_RPC_URL=$rpc ../$BIN --monitor-path config/monitors/$mon.json --network evm_ethereum_mainnet --block $blk >$T/vrows-one.log 2>&1 </dev/null); rc=$?
  else
    MAINNET_RPC_URL=$rpc bash scripts/local-dry-run.sh --monitor-path config/monitors/$mon.json --network evm_ethereum_mainnet --block $blk >$T/vrows-one.log 2>&1 </dev/null; rc=$?
  fi
  out=$(strip <$T/vrows-one.log)
  row "$label | endpoint $rpc | $mon @ $blk | exit $rc | $(grep -o 'Found matches, total: [0-9]*' <<<"$out" | grep -o '[0-9]*$') match(es) | matches.jsonl +$(( $(lines $D/data/matches.jsonl) - m0 )) | inbox +$(( $(lines $D/data/inbox/failed-tx.jsonl) - i0 )) | $(grep -c ' ERROR ' <<<"$out") ERROR"
}

echo "HEAD $(git rev-parse HEAD)"; git status --porcelain | sed 's/^/pre-status: /'
[ -x $BIN ] || { echo "build first"; exit 2; }

echo "### V1A.1 render gate + raw check"
MAINNET_RPC_URL=$RPC_RECENT bash deploy/render.sh --out $D/config > $T/vrows-render.log 2>&1; rc=$?
row "V1A.1 render | exit $rc | $(grep -o 'Found [0-9]* active monitor' $T/vrows-render.log) | $(grep -c ' ERROR ' $T/vrows-render.log) ERROR | $(grep -c 'abi-check: OK' $T/vrows-render.log) abi-check OK"
(cd $D && env $(env_for_config config) ../$BIN --check >$T/vrows-raw.log 2>&1); rc=$?; out=$(strip <$T/vrows-raw.log)
row "V1A.1 raw --check | exit $rc | success-line $(grep -c 'Configuration validation completed successfully' <<<"$out") | $(grep -c ' ERROR ' <<<"$out") ERROR | $(grep -o 'Found [0-9]* active monitor' <<<"$out")"

echo "### V1A.1f falsifier: brace removed from a rendered monitor (raw exit 0 + ERROR; gate = render of a mutated template)"
m=$D/config/monitors/ssv_seed_stake_monitor.json; cp $m $T/vrows-seed.bak
python3 - "$m" <<'PY'
import sys; p=sys.argv[1]; s=open(p).read().rstrip(); assert s.endswith('}'); open(p,'w').write(s[:-1])
PY
(cd $D && env $(env_for_config config) ../$BIN --check >$T/vrows-raw.log 2>&1); rc=$?; out=$(strip <$T/vrows-raw.log)
row "V1A.1f raw --check, brace removed | exit $rc | success-line $(grep -c 'Configuration validation completed successfully' <<<"$out") | $(grep -c ' ERROR ' <<<"$out") ERROR"
cp $T/vrows-seed.bak $m
t=deploy/config/monitors/ssv_seed_stake_monitor.json; cp $t $T/vrows-tpl.bak
python3 - "$t" <<'PY'
import sys; p=sys.argv[1]; s=open(p).read().rstrip(); open(p,'w').write(s[:-1])
PY
MAINNET_RPC_URL=$RPC_RECENT bash deploy/render.sh --out $D/config > $T/vrows-render-f.log 2>&1; rc=$?
row "V1A.1f render.sh, template brace removed | exit $rc | $(grep -m1 -E 'FAIL|ERROR' $T/vrows-render-f.log | cut -c1-140)"
cp $T/vrows-tpl.bak $t; git checkout -q -- $t
MAINNET_RPC_URL=$RPC_RECENT bash deploy/render.sh --out $D/config > $T/vrows-render-r.log 2>&1; rc=$?
row "V1A.1f restored render.sh | exit $rc | porcelain '$(git status --porcelain)'"

# the Monitor-ERROR half of the render.sh gate (event-0013 semantics: success line + zero ERROR lines) must
# still trip on its own: a dangling trigger is valid JSON, valid against the ABI, and only the Monitor rejects it
jq '.triggers=["no_such_trigger"]' $t > $t.tmp && mv $t.tmp $t
MAINNET_RPC_URL=$RPC_RECENT bash deploy/render.sh --out $D/config > $T/vrows-render-d.log 2>&1; rc=$?
row "V1A.1f render.sh, dangling trigger (abi-check passes, Monitor ERROR trips the gate) | exit $rc | abi-check OK $(grep -c 'abi-check: OK' $T/vrows-render-d.log) | $(grep -c ' ERROR ' $T/vrows-render-d.log) ERROR line(s) | success-line $(grep -c 'Configuration validation completed successfully' $T/vrows-render-d.log)"
git checkout -q -- $t
echo "### V1A.1 ABI falsifier (F8): one-field mutation Nonexistent(uint256) in the TRACKED template, restored by git checkout"
t=deploy/config/monitors/ssv_seed_stake_monitor.json
jq '.match_conditions.events[0].signature="Nonexistent(uint256)"' $t > $t.tmp && mv $t.tmp $t
row "F8 mutation diff | $(git diff --stat -- $t | tail -1)"
MAINNET_RPC_URL=$RPC_RECENT bash deploy/render.sh --out $D/config > $T/vrows-abi-red.log 2>&1; rc=$?
row "F8 render.sh mutated (must be non-zero) | exit $rc | $(grep -m1 'A3 event' $T/vrows-abi-red.log)"
bash deploy/check-templates.sh > $T/vrows-abi-red2.log 2>&1; rc=$?; row "F8 check-templates.sh mutated | exit $rc | $(grep -m1 'A3 event' $T/vrows-abi-red2.log)"
git checkout -q -- $t
MAINNET_RPC_URL=$RPC_RECENT bash deploy/render.sh --out $D/config > $T/vrows-abi-green.log 2>&1; rc=$?
row "F8 render.sh restored | exit $rc | $(grep -o 'Found [0-9]* active monitor' $T/vrows-abi-green.log) | porcelain '$(git status --porcelain)'"

echo "### F8b ABI layout gate: one-field mutations in the TRACKED template, each restored by git checkout"
t=deploy/config/monitors/ssv_seed_stake_monitor.json
UNST='.addresses[0].contract_spec[] | select(.type=="event" and .name=="UnstakeRequested") | .inputs'
f8b() { # f8b <label> <jq filter on the seed monitor>
  jq "$2" $t > $t.tmp && mv $t.tmp $t
  row "F8b $1 | diff $(git diff --stat -- $t | tail -1 | sed 's/^ *//')"
  MAINNET_RPC_URL=$RPC_RECENT bash deploy/render.sh --out $D/config > $T/vrows-f8b.log 2>&1; rc=$?
  row "F8b $1 render.sh (must be non-zero) | exit $rc | $(grep -m1 -oE 'A[89] [a-z]+ [A-Za-z]+\([^ ]*' $T/vrows-f8b.log)"
  bash deploy/check-templates.sh > $T/vrows-f8b2.log 2>&1; rc=$?; row "F8b $1 check-templates.sh (must be non-zero) | exit $rc | $(grep -m1 -c 'S7 FAIL' $T/vrows-f8b2.log) S7 FAIL line(s)"
  git checkout -q -- $t
}
f8b "indexed swapped user<->amount (QA case)" "($UNST) |= (.[0].indexed=false | .[1].indexed=true)"
f8b "input type changed amount uint256->uint64" "($UNST) |= (.[1].type=\"uint64\")"
f8b "input order swapped (amount,unlockTime)" "($UNST) |= ([.[0], .[2], .[1]])"
f8b "extra input" "($UNST) += [{\"name\":\"extra\",\"type\":\"uint256\",\"indexed\":false}]"
f8b "missing input" "($UNST) |= .[0:2]"
f8b "function output changed (registerOperator in the seed spec: outputs[0] uint64->uint32)" '(.addresses[0].contract_spec[] | select(.type=="function" and .name=="registerOperator") | .outputs[0].type) = "uint32"'
MAINNET_RPC_URL=$RPC_RECENT bash deploy/render.sh --out $D/config > $T/vrows-f8b-green.log 2>&1; rc=$?
row "F8b restored render.sh | exit $rc | $(grep -o 'Found [0-9]* active monitor' $T/vrows-f8b-green.log) | $(grep -o 'abi-check: OK ([^;]*; [0-9]* contract_spec[^)]*' $T/vrows-f8b-green.log) | porcelain '$(git status --porcelain -- deploy)'"
bash deploy/tests/abi-mutation-sweep.sh > $T/vrows-sweep.log 2>&1; row "F8b mutation sweep, all monitors x 16 kinds | exit $? | $(tail -1 $T/vrows-sweep.log)"
if [ -n "${OLD_ABI_CHECK:-}" ]; then
  ABI_CHECK=$OLD_ABI_CHECK bash deploy/tests/abi-mutation-sweep.sh > $T/vrows-sweep-old.log 2>&1; rc=$?
  row "F8b RED BASELINE: same sweep against the repair-1 gate | exit $rc | $(tail -1 $T/vrows-sweep-old.log) | cells where the old gate exited 0 despite a mutation: $(grep -c 'exit 0,' $T/vrows-sweep-old.log)"
fi
if [ -n "${SSV_OUT:-}" ] && [ -n "${SAFE_ABI:-}" ]; then
  bash deploy/abi/derive.sh --ssv-out "$SSV_OUT" --safe-abi "$SAFE_ABI" --check > $T/vrows-derive.log 2>&1; row "F8b reference.json reproducible from its sources | exit $? | $(tail -1 $T/vrows-derive.log | sed "s#$PWD/##")"
fi

echo "### V1A.2"
plain=$(jq -r '..|objects|select(.type?=="plain")|.value' deploy/config/triggers/*.json deploy/config/networks/*.json | wc -l | tr -d ' ')
git grep -nE 'discord\.com/api/webhooks/[0-9]+/|[0-9]{8,10}:AA[A-Za-z0-9_-]{30,}' -- . ':!tests/integration/fixtures' >$T/vrows-grep.log; grc=$?
row "V1A.2 plain values $plain | secret-shape grep exit $grc ($(wc -l <$T/vrows-grep.log | tr -d ' ') rows)"
bash deploy/check-templates.sh > $T/vrows-chk.log 2>&1; row "V1A.2 checker | exit $? | $(tail -1 $T/vrows-chk.log)"

echo "### F2 the QA's three exact missing-input commands (must be non-zero) + monitor-field deletions"
mkdir -p $T/vrows-empty
bash deploy/check-templates.sh --config-dir $T/vrows-absent > $T/vrows-f2a.log 2>&1; row "F2 absent --config-dir | exit $? | $(head -1 $T/vrows-f2a.log | cut -c1-90)"
bash deploy/check-templates.sh --config-dir $T/vrows-empty > $T/vrows-f2b.log 2>&1; row "F2 empty --config-dir | exit $? | $(head -1 $T/vrows-f2b.log | cut -c1-90)"
bash deploy/check-templates.sh --root $T/vrows-missing-root --config-dir "$PWD/deploy/config" > $T/vrows-f2c.log 2>&1; row "F2 missing --root | exit $? | $(head -1 $T/vrows-f2c.log | cut -c1-90)"
for k in networks match_conditions; do
  t=deploy/config/monitors/ssv_seed_stake_monitor.json; jq "del(.$k)" $t > $t.tmp && mv $t.tmp $t
  bash deploy/check-templates.sh > $T/vrows-f2d.log 2>&1; row "F2 monitor without $k | exit $? | $(head -1 $T/vrows-f2d.log | cut -c1-110)"
  git checkout -q -- $t
done
bash deploy/check-templates.sh > $T/vrows-f2e.log 2>&1; row "F2 restored | exit $? | $(tail -1 $T/vrows-f2e.log) | porcelain '$(git status --porcelain -- deploy)'"

echo "### V1A.3 inbox"
oneshot "V1A.3 reverted liquidate" $RPC_ARCHIVE ssv_failed_tx_inbox 26239237
oneshot "V1A.3 control (successful liquidate)" $RPC_ARCHIVE ssv_failed_tx_inbox 26239259
oneshot "V1A.3 implementer block" $RPC_ARCHIVE ssv_failed_tx_inbox 26239255
tail -n 1 $D/data/inbox/failed-tx.jsonl | jq -c 'keys|length' | sed 's/^/ROW | V1A.3 last inbox line key count (expect 11): /'

echo "### V1A.4 / V1A.4f other-sender"
oneshot "V1A.4 other sender 0x28bE update" $RPC_ARCHIVE ssv_updater_other_sender_monitor 26177496
oneshot "V1A.4f own-sender block" $RPC_ARCHIVE ssv_updater_other_sender_monitor 26239694
f=$D/config/monitors/ssv_updater_other_sender_monitor.json; cp $f $T/vrows-m11.bak
jq '.match_conditions.transactions[0].expression=null' $T/vrows-m11.bak > $f
oneshot "V1A.4f sender predicate removed (control must go red)" $RPC_ARCHIVE ssv_updater_other_sender_monitor 26239694 direct
cp $T/vrows-m11.bak $f
oneshot "V1A.4f restored" $RPC_ARCHIVE ssv_updater_other_sender_monitor 26239694 direct

echo "### V1A.5 retired addresses (A) + F4 outgoing"
oneshot "V1A.5 incoming to retired slot 4 (4192795588647 wei)" $RPC_ARCHIVE ssv_retired_addresses_monitor 25991146
oneshot "F4 outgoing from retired slot 4 (must be 0)" $RPC_ARCHIVE ssv_retired_addresses_monitor 26169289
oneshot "M13 old slot-4 key commitRoot attempt" $RPC_ARCHIVE ssv_retired_updater_attempt_monitor 26167892
oneshot "M13 attempt monitor control (other senders)" $RPC_ARCHIVE ssv_retired_updater_attempt_monitor 26239655

echo "### V1A.6 M8 FeesSynced pipeline proof"
f=$D/config/monitors/ssv_seed_stake_monitor.json; cp $f $T/vrows-seed.bak
oneshot "V1A.6 baseline (== 0)" $RPC_ARCHIVE ssv_seed_stake_monitor 26240350 direct
jq '.match_conditions.events[0].expression="newFeesWei > 0"' $T/vrows-seed.bak > $f
oneshot "V1A.6 mutated (> 0, expect 1)" $RPC_ARCHIVE ssv_seed_stake_monitor 26240350 direct
cp $T/vrows-seed.bak $f
oneshot "V1A.6 restored (== 0)" $RPC_ARCHIVE ssv_seed_stake_monitor 26240350 direct
row "V1A.6 porcelain after | '$(git status --porcelain)'"

echo "### V1A.7 / F3 / F9"
bash deploy/check-m15-coverage.sh > $T/vrows-m15.log 2>&1; row "V1A.7 check-m15-coverage | exit $? | $(grep -c 'extra' $T/vrows-m15.log) extras | $(head -1 $T/vrows-m15.log | sed 's/^== //') | $(grep -E 'all M15|no M15/M10' $T/vrows-m15.log | tr '\n' ';')"
mon=deploy/config/monitors/ssv_dao_discord_success_monitor.json
names=$( { awk '/^### M15 /{on=1; next} /^### M16 /{on=0} on' docs/ssv-specs/monitoring-service-spec.md | grep -o '`[A-Z][A-Za-z0-9]*`' | tr -d '`'; echo ModuleUpgraded; } | sort -u)
row "F9 required names (spec M15 backticks + ModuleUpgraded) | $(printf '%s\n' "$names" | wc -l | tr -d ' ')"
# red baseline: the pre-repair-3 gate (frozen head 204d528) accepts the ModuleUpgraded deletion
mkdir -p $T/old/deploy $T/old/docs/ssv-specs && cp -R deploy/config $T/old/deploy/config && cp docs/ssv-specs/monitoring-service-spec.md $T/old/docs/ssv-specs/
git show 204d528:deploy/check-m15-coverage.sh > $T/old/deploy/check-m15-coverage.sh
jq '.match_conditions.events |= map(select(.signature != "ModuleUpgraded(uint8,address)"))' $mon > $T/old/deploy/config/monitors/ssv_dao_discord_success_monitor.json
bash $T/old/deploy/check-m15-coverage.sh >$T/vrows-old.log 2>&1; row "F9 RED BASELINE: old gate (204d528), ModuleUpgraded deleted (expect 0 = defect) | exit $? | $(grep -E 'all M15|missing' $T/vrows-old.log | head -1)"
n=0
for ev in $names; do
  sig=$(jq -r --arg e "$ev" '.match_conditions.events[]|select(.signature|split("(")[0]==$e)|.signature' $mon | head -1)
  jq --arg s "$sig" '.match_conditions.events |= map(select(.signature != $s))' $mon > $mon.tmp && mv $mon.tmp $mon
  bash deploy/check-m15-coverage.sh >$T/vrows-del.log 2>&1; rc=$?
  row "F9 delete $sig from tracked Discord DAO monitor (must be non-zero) | exit $rc | $(grep -m1 '^missing' $T/vrows-del.log)"
  git checkout -q -- $mon; n=$((n+1))
done
bash deploy/check-m15-coverage.sh >$T/vrows-del.log 2>&1; row "F9 restored after $n deletions | exit $? | $(grep -E 'all M15' $T/vrows-del.log) | porcelain '$(git status --porcelain)'"
tg=deploy/config/monitors/ssv_dao_success_monitor.json
row "D2 Telegram DAO monitor unconditional M15 events | $(jq --argjson names "$(printf '%s\n' $names | jq -R . | jq -s -c .)" '[.match_conditions.events[]?|select(.expression==null)|select((.signature|split("(")[0]) as $e|$names|index($e))]|length' $tg)"
jq '.match_conditions.events += [{"signature":"ModuleUpgraded(uint8,address)","expression":null}]' $tg > $tg.tmp && mv $tg.tmp $tg
bash deploy/check-m15-coverage.sh >$T/vrows-tg.log 2>&1; row "D2 ModuleUpgraded added to Telegram DAO monitor (must be non-zero) | exit $? | $(grep -m1 'ModuleUpgraded' $T/vrows-tg.log)"
git checkout -q -- $tg
B=$((0x18f4eea))
oneshot "F3 real OracleReplaced block, Telegram DAO monitor (must be 0)" $RPC_ARCHIVE ssv_dao_success_monitor $B direct
oneshot "F3 real OracleReplaced block, Discord DAO monitor (must be 1)" $RPC_ARCHIVE ssv_dao_discord_success_monitor $B direct

echo "### D11 Discord routing"
for n in cluster dao liquidation operator oracle validator; do
  row "D11 discord_${n}_success -> $(jq -r ".discord_${n}_success.config.discord_url.value" deploy/config/triggers/discord_${n}_success.json) | name $(jq -r ".discord_${n}_success.name" deploy/config/triggers/discord_${n}_success.json)"
done
row "D11 kept per-type variables (9) | $(jq -r 'to_entries[]|select(.value.trigger_type=="discord" and (.key|test("_success$|_other_sender$")|not))|.value.config.discord_url.value' deploy/config/triggers/*.json | tr '\n' ' ')"
row "D11 duplicate Discord trigger names | $(jq -r 'to_entries[]|select(.value.trigger_type=="discord")|.value.name' deploy/config/triggers/*.json | sort | uniq -d | wc -l | tr -d ' ')"
gone='DISCORD_WEBHOOK_(CLUSTER|DAO|LIQUIDATION|OPERATOR|ORACLE|VALIDATOR)_SUCCESS'
git grep -nE "$gone" -- . ':!plans' > $T/vrows-gone.log; grc=$?
row "D11 removed variable names in tracked files outside plans/ (exit 1 = none) | exit $grc | $(wc -l < $T/vrows-gone.log | tr -d ' ') rows"
git grep -nE "$gone" -- plans ':!plans/monitoring-service/qa' | cut -d: -f1 | sort | uniq -c | sed 's/^/ROW | D11 historical mentions (append-only records) | /'
t=deploy/config/triggers/discord_cluster_success.json
jq 'del(.discord_cluster_success.config.discord_url)' $t > $t.tmp && mv $t.tmp $t
bash deploy/check-templates.sh > $T/vrows-d11.log 2>&1; row "D11 falsifier: discord_cluster_success without discord_url (must be non-zero) | exit $? | $(grep -m1 FAIL $T/vrows-d11.log | cut -c1-120)"
git checkout -q -- $t
MAINNET_RPC_URL=$RPC_RECENT bash deploy/render.sh --out $D/config > $T/vrows-d11r.log 2>&1; row "D11 render.sh restored | exit $? | $(grep -o 'Found [0-9]* active monitor' $T/vrows-d11r.log) | porcelain '$(git status --porcelain)'"

echo "### V1A.8 compose"
docker compose -f docker-compose.yaml config --format json > $T/vrows-compose.json 2>$T/vrows-compose.err; rc=$?
row "V1A.8 docker compose config | exit $rc | volumes $(jq -c '[.services[].volumes[]?|select(.target=="/app/data")|.source]' $T/vrows-compose.json)"
jq -c '{max_past_blocks, recovery_config, rpc_urls:(.rpc_urls|length)}' $D/config/networks/evm_ethereum_mainnet.json | sed 's/^/ROW | V1A.8 rendered network: /'

echo "### F10 reproducer (QA attempt 4) and the frozen-head red baseline"
t=deploy/config/triggers/discord_dao_success.json
for v in '"qa_invalid_config"' '[]'; do
  jq ".discord_dao_success.config = $v" $t > $t.tmp && mv $t.tmp $t
  bash deploy/check-templates.sh > $T/vrows-f10.log 2>&1; rc=$?; row "F10 checker, config = $v (must be non-zero) | exit $rc | $(grep -m1 FAIL $T/vrows-f10.log | cut -c1-120)"
  MAINNET_RPC_URL=$RPC_RECENT bash deploy/render.sh --out $D/config > $T/vrows-f10r.log 2>&1; row "F10 render.sh, config = $v (must be non-zero) | exit $?"
  git checkout -q -- $t
done
bash deploy/check-templates.sh > $T/vrows-f10.log 2>&1; row "F10 restored | exit $? | $(tail -1 $T/vrows-f10.log) | porcelain '$(git status --porcelain -- deploy)'"
mkdir -p $T/frozen && git archive 72029c0 deploy docs/ssv-specs/monitoring-service-spec.md | tar -x -C $T/frozen
jq '.discord_dao_success.config = "qa_invalid_config"' $T/frozen/$t > $T/frozen/x && mv $T/frozen/x $T/frozen/$t
bash $T/frozen/deploy/check-templates.sh > $T/vrows-f10old.log 2>&1; row "F10 RED BASELINE: frozen head 72029c0 checker, string config (expect 0 = the defect) | exit $? | $(tail -1 $T/vrows-f10old.log)"
echo "### fork regression"
FORK_URL=$RPC_ARCHIVE bash deploy/tests/m13-fork-regression.sh | tail -1 | sed 's/^/ROW | fork regression: /'
git status --porcelain | sed 's/^/post-status: /'; echo done
