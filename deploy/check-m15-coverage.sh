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
# Fail-closed (CP1A repair 4): every jq call's status is checked and a failing one is a FAIL; a monitor or
# trigger file that is empty, unreadable, not JSON or of the wrong shape (deploy/shape.jq) is a FAIL, never
# skipped; the 16 spec names must be found (a truncated section cannot shrink the required set); trigger
# keys are read per file, so a duplicate key in a later file cannot hide a Telegram route; a missing or
# non-working jq exits 2 with "jq is required".
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
SPEC="$ROOT/docs/ssv-specs/monitoring-service-spec.md"
REF="$ROOT/deploy/abi/reference.json"
PROXY=0xdd9bc35ae942ef0cfa76930954a156b3ff30a4e1
EXPECTED_NAMED=16 # backticked event names in the M15 section at the plan pin (+ ModuleUpgraded = 17)
{ command -v jq >/dev/null && [ "$(jq -n '1 + 1' 2>/dev/null)" = 2 ]; } || { echo "m15-coverage: jq is required (not on PATH, or not working)" >&2; exit 2; }
[ -f "$SPEC" ] || { echo "m15-coverage: spec $SPEC not found"; exit 2; }
[ -f "$REF" ] || { echo "m15-coverage: reference ABI $REF not found"; exit 2; }
[ $# -gt 0 ] || set -- "$ROOT/deploy/config/monitors/ssv_dao_discord_success_monitor.json"

section=$(awk '/^### M15 /{on=1; next} /^### M16 /{on=0} on' "$SPEC") || { echo "m15-coverage: cannot read $SPEC"; exit 2; }
grep -q 'module updates' <<<"$section" || { echo "M15 no longer mentions \"module updates\"; revisit the ModuleUpgraded requirement"; exit 2; }
named=$(grep -o '`[A-Z][A-Za-z0-9]*`' <<<"$section" | tr -d '`' | sort -u)
[ -n "$named" ] || { echo "no M15 event names found in the spec"; exit 2; }
n_named=$(printf '%s\n' "$named" | grep -c .)
[ "$n_named" -eq "$EXPECTED_NAMED" ] || { echo "the M15 section names $n_named events, the plan pin has $EXPECTED_NAMED: revisit the required set (a truncated or edited section must not shrink it silently)"; exit 2; }
spec=$( { printf '%s\n' "$named"; echo ModuleUpgraded; } | sort -u)

# Canonical name(types) of each required event, from the committed reference ABI (tuples expanded).
names_json=$(printf '%s\n' "$spec" | jq -R . | jq -s -c .) || { echo "m15-coverage: jq failed building the required name list"; exit 2; }
req=$(jq -r --argjson names "$names_json" --arg a "$PROXY" '
  def t: if (.type | startswith("tuple")) then "(" + ([.components[] | t] | join(",")) + ")" + (.type | ltrimstr("tuple")) else .type end;
  [.contracts[$a].abi[]? | select(.type == "event") | {n: .name, s: (.name + "(" + ([.inputs[] | t] | join(",")) + ")")}] as $ev
  | $names[] as $n | ([$ev[] | select(.n == $n) | .s] | if length == 1 then .[0] else "UNRESOLVED:\($n)" end)' "$REF" | sort -u) \
  || { echo "cannot read the proxy ABI from $REF (jq failed)"; exit 2; }
[ -n "$req" ] || { echo "cannot read the proxy ABI from $REF"; exit 2; }
if grep -q '^UNRESOLVED:' <<<"$req"; then
  echo "required M15 name(s) that are not exactly one event in the reference ABI:"; grep '^UNRESOLVED:' <<<"$req" | sed 's/^UNRESOLVED:/  /'; exit 2
fi
[ "$(printf '%s\n' "$req" | grep -c .)" -eq "$(printf '%s\n' "$spec" | grep -c .)" ] || { echo "m15-coverage: $(printf '%s\n' "$req" | grep -c .) signatures resolved for $(printf '%s\n' "$spec" | grep -c .) required names"; exit 2; }

rc=0
file_ok() { # file_ok <file> <jq shape fn>: a readable, non-empty file of exactly one JSON value of the right shape
  local f=$1 fn=$2 shp
  if [ ! -f "$f" ] || [ ! -r "$f" ] || [ ! -s "$f" ]; then echo "FAIL: ${f#"$ROOT"/} is missing, unreadable or empty"; return 1; fi
  shp=$(jq -r -L "$HERE" -s "include \"shape\"; if length == 1 then .[0] | $fn[] else \"expected exactly one JSON value, found \\(length)\" end" "$f" 2>&1) \
    || { echo "FAIL: ${f#"$ROOT"/} jq failed: $shp"; return 1; }
  [ -z "$shp" ] || { printf '%s\n' "$shp" | sed "s#^#FAIL: ${f#"$ROOT"/} shape: #"; return 1; }
}
for m in "$@"; do
  file_ok "$m" monitor_shape || { rc=1; continue; }
  have=$(jq -r '.match_conditions.events[]?.signature' "$m" | sort -u) || { echo "FAIL: ${m#"$ROOT"/} jq failed reading its events"; rc=1; continue; }
  missing=""; extra=""
  while IFS= read -r sig; do grep -qxF -- "$sig" <<<"$have" || missing="$missing$sig"$'\n'; done <<<"$req"
  while IFS= read -r sig; do [ -z "$sig" ] || grep -qxF -- "$sig" <<<"$req" || extra="$extra$sig"$'\n'; done <<<"$have"
  echo "== ${m#"$ROOT"/}: $(printf '%s\n' "$req" | grep -c .) required events, monitor $(printf '%s\n' "$have" | grep -c .) events"
  [ -z "$missing" ] || { printf 'missing: %s\n' $missing; rc=1; }
  [ -z "$extra" ] || printf 'extra (not required by M15, kept): %s\n' $extra
  [ -n "$missing" ] || echo "all M15 events present"
done

# D2 routing: no Telegram-routed monitor may match a M15/M10 info event (or the function that
# emits it) unconditionally. Every trigger and monitor file is read; none is skipped on a failure.
shopt -s nullglob
trig_files=("$ROOT"/deploy/config/triggers/*.json); mon_files=("$ROOT"/deploy/config/monitors/*.json)
[ ${#trig_files[@]} -gt 0 ] && [ ${#mon_files[@]} -gt 0 ] || { echo "no trigger or monitor templates under deploy/config"; exit 2; }
tg=""
for f in "${trig_files[@]}"; do
  file_ok "$f" trigger_file_shape || { rc=1; continue; }
  t=$(jq -r 'to_entries[] | select(.value.trigger_type == "telegram") | .key' "$f") || { echo "FAIL: ${f#"$ROOT"/} jq failed reading its triggers"; rc=1; continue; }
  [ -z "$t" ] || tg="$tg$t"$'\n'
done
[ -n "$tg" ] || { echo "no telegram triggers found under deploy/config/triggers"; exit 2; }
tg_json=$(printf '%s' "$tg" | jq -R . | jq -s -c .) || { echo "m15-coverage: jq failed building the Telegram trigger list"; exit 2; }
names=$names_json
bad=""
for f in "${mon_files[@]}"; do
  file_ok "$f" monitor_shape || { rc=1; continue; }
  b=$(jq -r --argjson names "$names" --argjson tg "$tg_json" '
    select(any((.triggers // [])[]; . as $t | $tg | index($t)))
    | select(all(.match_conditions.transactions[]?; .status != "Failure"))
    | .name as $n
    | ((.match_conditions.events[]? | select(.expression == null) | select((.signature | split("(")[0]) as $e | $names | index($e)) | "event \(.signature)"),
       (.match_conditions.functions[]? | select(.expression == null) | select(.signature | test("^(update(?!ClusterBalance)[A-Z]|replaceOracle|upgradeTo)")) | "function \(.signature)"))
    | "\($n): \(.)"' "$f") || { echo "FAIL: ${f#"$ROOT"/} jq failed in the Telegram-routing check"; rc=1; continue; }
  [ -z "$b" ] || bad="$bad$b"$'\n'
done
if [ -n "$bad" ]; then
  echo "M15/M10 info on a Telegram route (D2: Discord only):"; printf '%s' "$bad"; rc=1
elif [ $rc -eq 0 ]; then
  echo "no M15/M10 info event or governance-update function is routed to Telegram"
fi
exit $rc
