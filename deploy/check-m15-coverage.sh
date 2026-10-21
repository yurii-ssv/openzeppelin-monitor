#!/usr/bin/env bash
# Read-only: every event named in spec M15 (governance and parameter changes; includes M10's
# OracleReplaced) must be matched by the Discord DAO success monitor (V1A.7), and no monitor that
# routes to a Telegram trigger (other than a Failure-status page) may carry those events or the
# functions that emit them unconditionally (D2: info goes to Discord; a page decision for M10 belongs to the stateful worker).
#
#   deploy/check-m15-coverage.sh [monitor.json ...]     (default: the Discord DAO success template)
#
# Spec names are the backticked identifiers starting with a capital letter in the M15 section of
# docs/ssv-specs/monitoring-service-spec.md, plus ModuleUpgraded (M15 "module updates"). Prints the
# diff (missing = in spec, not matched; extra = matched, not named in M15) and exits 1 if anything
# is missing or if any Telegram-routed template has a M15 event or governance-update function.
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
SPEC="$ROOT/docs/ssv-specs/monitoring-service-spec.md"
[ $# -gt 0 ] || set -- "$ROOT/deploy/config/monitors/ssv_dao_discord_success_monitor.json"

spec=$(awk '/^### M15 /{on=1; next} /^### M16 /{on=0} on' "$SPEC" \
  | grep -o '`[A-Z][A-Za-z0-9]*`' | tr -d '`' | sort -u)
[ -n "$spec" ] || { echo "no M15 event names found in the spec"; exit 2; }

rc=0
for m in "$@"; do
  have=$(jq -r '.match_conditions.events[].signature | split("(")[0]' "$m" | sort -u)
  missing=$(comm -23 <(printf '%s\n' "$spec") <(printf '%s\n' "$have"))
  extra=$(comm -13 <(printf '%s\n' "$spec") <(printf '%s\n' "$have"))
  echo "== ${m#"$ROOT"/}: spec $(printf '%s\n' "$spec" | wc -l | tr -d ' ') names, monitor $(printf '%s\n' "$have" | wc -l | tr -d ' ') events"
  [ -z "$missing" ] || { printf 'missing: %s\n' $missing; rc=1; }
  [ -z "$extra" ] || printf 'extra (not named in M15, kept): %s\n' $extra
  [ $rc -ne 0 ] || echo "all M15 events present"
done

# D2 routing: no Telegram-routed monitor may match a M15/M10 info event (or the function that
# emits it) unconditionally.
tg=$(jq -s -r 'add | to_entries[] | select(.value.trigger_type == "telegram") | .key' "$ROOT"/deploy/config/triggers/*.json)
[ -n "$tg" ] || { echo "no telegram triggers found under deploy/config/triggers"; exit 2; }
names=$(printf '%s\n' "$spec" ModuleUpgraded | sort -u | jq -R . | jq -s -c .)
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
