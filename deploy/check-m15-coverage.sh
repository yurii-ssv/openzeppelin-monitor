#!/usr/bin/env bash
# Read-only: every event of spec M15 (governance and parameter changes; includes M10's
# OracleReplaced and "module updates") must be matched by the Discord DAO success monitor (V1A.7)
# under its full canonical signature from the committed reference ABI, and no monitor that
# routes to a Telegram trigger (other than a Failure-status page) may carry those events or the
# functions that emit them unconditionally (D2: info goes to Discord; a page decision for M10 belongs to the stateful worker).
#
#   deploy/check-m15-coverage.sh [monitor.json ...]     (default: the Discord DAO success template)
#
# Required set (17 events at the plan pin): the backticked identifiers starting with a capital letter
# in the M15 section of docs/ssv-specs/monitoring-service-spec.md, plus ModuleUpgraded, the event behind
# the spec's phrase "module updates" (the spec does not name it in backticks). Each required name is
# resolved to name(types) in deploy/abi/reference.json (the proxy's event list); a name that is not
# an event there is an error. A monitor must carry every one of those signatures: deleting any of the
# 17, or changing its types, exits non-zero. Prints the diff (missing = required, not matched; extra =
# matched, not required, kept) and exits 1 if anything is missing or if any Telegram-routed template
# has a M15 event or governance-update function.
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
SPEC="$ROOT/docs/ssv-specs/monitoring-service-spec.md"
REF="$ROOT/deploy/abi/reference.json"
PROXY=0xdd9bc35ae942ef0cfa76930954a156b3ff30a4e1
[ $# -gt 0 ] || set -- "$ROOT/deploy/config/monitors/ssv_dao_discord_success_monitor.json"

section=$(awk '/^### M15 /{on=1; next} /^### M16 /{on=0} on' "$SPEC")
grep -q 'module updates' <<<"$section" || { echo "M15 no longer mentions \"module updates\"; revisit the ModuleUpgraded requirement"; exit 2; }
spec=$( { grep -o '`[A-Z][A-Za-z0-9]*`' <<<"$section" | tr -d '`'; echo ModuleUpgraded; } | sort -u)
[ -n "$spec" ] || { echo "no M15 event names found in the spec"; exit 2; }

# Canonical name(types) of each required event, from the committed reference ABI (tuples expanded).
names_json=$(printf '%s\n' "$spec" | jq -R . | jq -s -c .)
req=$(jq -r --argjson names "$names_json" --arg a "$PROXY" '
  def t: if (.type | startswith("tuple")) then "(" + ([.components[] | t] | join(",")) + ")" + (.type | ltrimstr("tuple")) else .type end;
  [.contracts[$a].abi[]? | select(.type == "event") | {n: .name, s: (.name + "(" + ([.inputs[] | t] | join(",")) + ")")}] as $ev
  | $names[] as $n | ([$ev[] | select(.n == $n) | .s] | if length == 1 then .[0] else "UNRESOLVED:\($n)" end)' "$REF" 2>/dev/null | sort -u)
[ -n "$req" ] || { echo "cannot read the proxy ABI from $REF"; exit 2; }
if grep -q '^UNRESOLVED:' <<<"$req"; then
  echo "required M15 name(s) that are not exactly one event in the reference ABI:"; grep '^UNRESOLVED:' <<<"$req" | sed 's/^UNRESOLVED:/  /'; exit 2
fi

rc=0
for m in "$@"; do
  have=$(jq -r '.match_conditions.events[]?.signature' "$m" | sort -u)
  missing=$(comm -23 <(printf '%s\n' "$req") <(printf '%s\n' "$have"))
  extra=$(comm -13 <(printf '%s\n' "$req") <(printf '%s\n' "$have"))
  echo "== ${m#"$ROOT"/}: $(printf '%s\n' "$req" | wc -l | tr -d ' ') required events, monitor $(printf '%s\n' "$have" | wc -l | tr -d ' ') events"
  [ -z "$missing" ] || { printf 'missing: %s\n' $missing; rc=1; }
  [ -z "$extra" ] || printf 'extra (not required by M15, kept): %s\n' $extra
  [ $rc -ne 0 ] || echo "all M15 events present"
done

# D2 routing: no Telegram-routed monitor may match a M15/M10 info event (or the function that
# emits it) unconditionally.
tg=$(jq -s -r 'add | to_entries[] | select(.value.trigger_type == "telegram") | .key' "$ROOT"/deploy/config/triggers/*.json)
[ -n "$tg" ] || { echo "no telegram triggers found under deploy/config/triggers"; exit 2; }
names=$names_json
bad=$(for f in "$ROOT"/deploy/config/monitors/*.json; do
  jq -r --argjson names "$names" --argjson tg "$(printf '%s\n' "$tg" | jq -R . | jq -s -c .)" '
    select(any(.triggers[]; . as $t | $tg | index($t)))
    | select(all(.match_conditions.transactions[]?; .status != "Failure"))
    | .name as $n
    | ((.match_conditions.events[]? | select(.expression == null) | select((.signature | split("(")[0]) as $e | $names | index($e)) | "event \(.signature)"),
       (.match_conditions.functions[]? | select(.expression == null) | select(.signature | test("^(update(?!ClusterBalance)[A-Z]|replaceOracle|upgradeTo)")) | "function \(.signature)"))
    | "\($n): \(.)"' "$f"
done)
if [ -n "$bad" ]; then
  echo "M15/M10 info on a Telegram route (D2: Discord only):"; printf '%s\n' "$bad"; rc=1
else
  echo "no M15/M10 info event or governance-update function is routed to Telegram"
fi
exit $rc
