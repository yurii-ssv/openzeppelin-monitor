#!/usr/bin/env bash
# Read-only static ABI/expression check for the Monitor templates. Writes nothing.
#
#   deploy/check-abi.sh --config-dir <dir>      (a directory holding monitors/*.json)
#
# `openzeppelin-monitor --check` never compares a monitor's event/function signatures with its
# ABI, so a monitor that names an event the contract does not have loads cleanly and never
# matches. This check closes that deploy-side gap. For every monitor under <dir>/monitors:
#   A1  at least one event/function/transaction condition exists (an empty set matches every tx)
#   A2  events[] and functions[] need a contract_spec (ABI) on at least one monitored address
#   A3  every event signature equals the canonical name(type,...) of an ABI `event` entry
#   A4  every function signature equals the canonical name(type,...) of an ABI `function` entry
#   A5  every event/function expression compares only parameters that exist on that event/function
#   A6  every transaction expression compares only known transaction fields
#   A7  no `&&` / `||` in any expression (the Monitor grammar spells them AND / OR; the symbols
#       parse as errors and the rule silently never matches)
# Tuples are expanded: (uint32,uint64)[] etc. Exit 0 only if every assertion holds; prints one FAIL
# line per violation, and fails on a missing directory, no monitor files or an unparsable file.
set -uo pipefail

CFG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --config-dir) CFG=${2:?--config-dir needs a directory}; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ -n "$CFG" ] || { echo "usage: deploy/check-abi.sh --config-dir <dir>" >&2; exit 2; }
command -v jq >/dev/null || { echo "jq is required" >&2; exit 2; }
[ -d "$CFG/monitors" ] || { echo "abi-check: FAIL: $CFG/monitors is not a directory" >&2; exit 1; }

errors=0
n=0
for f in "$CFG"/monitors/*.json; do
  [ -f "$f" ] || continue
  n=$((n + 1))
  out=$(jq -r '
    def canon: if (.type | startswith("tuple"))
                 then "(" + ([.components[] | canon] | join(",")) + ")" + (.type | ltrimstr("tuple"))
                 else .type end;
    def sig: .name + "(" + ([(.inputs // [])[] | canon] | join(",")) + ")";
    def idents: [match("(?:^|[\\s(])([A-Za-z_][A-Za-z0-9_]*)(?:\\[[^\\]]*\\]|\\.[A-Za-z0-9_]+)*\\s*(?:==|!=|>=|<=|>|<|(?i:contains)|(?i:starts_with)|(?i:ends_with))"; "g").captures[0].string];
    def txfields: ["value","from","to","hash","gas_price","max_fee_per_gas","max_priority_fee_per_gas","gas_limit","nonce","input","gas_used","transaction_index"];
    def sym: if . == null then empty else (select(test("&&|[|][|]")) | "A7 expression uses && or || (use AND / OR): \(.)") end;

    . as $m
    | ([.addresses[]?.contract_spec? | select(type == "array") | .[]]) as $abi
    | ([$abi[] | select(.type == "event")]) as $events
    | ([$abi[] | select(.type == "function")]) as $functions
    | (.match_conditions // {}) as $mc
    | (if (($mc.events // []) + ($mc.functions // []) + ($mc.transactions // []) | length) == 0
         then "A1 no event/function/transaction condition (would match every transaction)" else empty end),
      (if ((($mc.events // []) + ($mc.functions // []) | length) > 0) and ($abi | length) == 0
         then "A2 events/functions configured but no address has a contract_spec" else empty end),
      ($mc.events[]? as $e
        | ($events | map(select(sig == $e.signature))) as $hit
        | (if ($hit | length) == 0 then "A3 event \($e.signature) is not an event in the ABI" else empty end),
          ($e.expression // empty | sym),
          (if ($hit | length) > 0 and $e.expression != null then
             ($hit[0].inputs | map(.name)) as $names
             | ($e.expression | idents[] | select(. as $i | ($names | index($i)) == null)
                 | "A5 event \($e.signature): expression names \(.) which is not an input (inputs: \($names | join(",")))")
           else empty end)),
      ($mc.functions[]? as $fn
        | ($functions | map(select(sig == $fn.signature))) as $hit
        | (if ($hit | length) == 0 then "A4 function \($fn.signature) is not a function in the ABI" else empty end),
          ($fn.expression // empty | sym),
          (if ($hit | length) > 0 and $fn.expression != null then
             ($hit[0].inputs | map(.name)) as $names
             | ($fn.expression | idents[] | select(. as $i | ($names | index($i)) == null)
                 | "A5 function \($fn.signature): expression names \(.) which is not an input (inputs: \($names | join(",")))")
           else empty end)),
      ($mc.transactions[]? | select(.expression != null) | .expression as $x
        | ($x | sym),
          ($x | idents[] | select(. as $i | (txfields | index($i)) == null)
             | "A6 transaction expression names \(.) which is not a transaction field: \($x)"))
  ' "$f" 2>&1) || { echo "FAIL: ABI-1 ${f#"$CFG"/}: not parseable by jq: $out"; errors=$((errors + 1)); continue; }
  while IFS= read -r line; do
    [ -z "$line" ] || { echo "FAIL: ${f#"$CFG"/} $line"; errors=$((errors + 1)); }
  done <<<"$out"
done

if [ $n -eq 0 ]; then
  echo "abi-check: FAIL: no monitor files under $CFG/monitors" >&2
  exit 1
fi
if [ $errors -ne 0 ]; then
  echo "abi-check: $errors failure(s)"
  exit 1
fi
echo "abi-check: OK ($n monitors: every event/function signature and expression parameter exists in the ABI)"
