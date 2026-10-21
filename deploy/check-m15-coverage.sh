#!/usr/bin/env bash
# Read-only: every event named in spec M15 (governance and parameter changes; includes M10's
# OracleReplaced) must be matched by the DAO success monitors (V1A.7).
#
#   deploy/check-m15-coverage.sh [monitor.json ...]     (default: both DAO success templates)
#
# Spec names are the backticked identifiers starting with a capital letter in the M15 section of
# docs/ssv-specs/monitoring-service-spec.md. Prints the diff (missing = in spec, not matched;
# extra = matched, not named in M15) and exits 1 if anything is missing.
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
SPEC="$ROOT/docs/ssv-specs/monitoring-service-spec.md"
[ $# -gt 0 ] || set -- "$ROOT/deploy/config/monitors/ssv_dao_success_monitor.json" \
                       "$ROOT/deploy/config/monitors/ssv_dao_discord_success_monitor.json"

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
exit $rc
