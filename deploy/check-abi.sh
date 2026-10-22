#!/usr/bin/env bash
# Read-only static ABI/expression check for the Monitor templates. Writes nothing.
#
#   deploy/check-abi.sh --config-dir <dir> [--reference <file>]   (<dir> holds monitors/*.json)
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
#   A8  every address that carries a contract_spec has an entry in the committed reference ABI
#   A9  every event and function in every contract_spec equals the reference entry for its address in
#       full: name, anonymous flag, and each input's name, type, tuple components, order and (events)
#       `indexed`, plus each function's outputs. A9 is what catches an `indexed` swap, a changed type,
#       reordered / extra / missing inputs and a changed output: the A3/A4 name(types) comparison cannot
#       see `indexed` and compares the monitor with itself. The reference is deploy/abi/reference.json,
#       derived from the contracts at the plan pin v2.0.0+f7ac487 (see deploy/abi/provenance.json and
#       deploy/README.md), never from the monitor being checked.
#   A0  the monitor's type-level shape (deploy/shape.jq): events as an object, addresses as a string, ...
#       is one FAIL line, not a jq error that skips the file
# Tuples are expanded: (uint32,uint64)[] etc. Exit 0 only if every assertion holds; prints one FAIL
# line per violation, and fails on a missing directory, no monitor files, an empty, unreadable, looping
# or unparsable file, a jq that fails, and a missing or non-working jq ("jq is required", exit 2).
set -uo pipefail

DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"
ABI_DIR="$DEPLOY_DIR/abi"
CFG=""
REF="$ABI_DIR/reference.json"
while [ $# -gt 0 ]; do
  case "$1" in
    --config-dir) CFG=${2:?--config-dir needs a directory}; shift 2 ;;
    --reference) REF=${2:?--reference needs a file}; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ -n "$CFG" ] || { echo "usage: deploy/check-abi.sh --config-dir <dir> [--reference <file>]" >&2; exit 2; }
{ command -v jq >/dev/null && [ "$(jq -n '1 + 1' 2>/dev/null)" = 2 ]; } || { echo "abi-check: jq is required (not on PATH, or not working)" >&2; exit 2; }
[ -f "$DEPLOY_DIR/shape.jq" ] || { echo "abi-check: FAIL: $DEPLOY_DIR/shape.jq is missing" >&2; exit 1; }
[ -f "$ABI_DIR/normalize.jq" ] || { echo "abi-check: FAIL: $ABI_DIR/normalize.jq is missing" >&2; exit 1; }
jq -e '(.contracts | type == "object" and length > 0) and ([.contracts[].abi | type == "array" and length > 0] | all)' "$REF" >/dev/null 2>&1 \
  || { echo "abi-check: FAIL: reference ABI $REF is missing, unparsable or empty" >&2; exit 1; }
[ -d "$CFG/monitors" ] || { echo "abi-check: FAIL: $CFG/monitors is not a directory" >&2; exit 1; }

errors=0
n=0
shopt -s nullglob
files=("$CFG"/monitors/*.json)
# Every directory entry must be a readable regular .json file; a skipped entry is a failure, not a pass.
listing=$(find "$CFG/monitors" -mindepth 1 -maxdepth 1 2>&1) || { echo "abi-check: FAIL: cannot list $CFG/monitors: $listing" >&2; exit 1; }
while IFS= read -r e; do
  case "${e##*/}" in "" | .gitkeep) ;; .*) echo "FAIL: ${e#"$CFG"/} unexpected hidden entry in monitors/"; errors=$((errors + 1)) ;; *.json) ;;
    *) echo "FAIL: ${e#"$CFG"/} unexpected entry in monitors/ (only *.json files)"; errors=$((errors + 1)) ;; esac
done <<<"$listing"
for f in ${files[@]+"${files[@]}"}; do
  n=$((n + 1))
  if [ ! -f "$f" ] || [ ! -r "$f" ]; then echo "FAIL: ${f#"$CFG"/} is not a readable regular file (unreadable, a directory, or a dangling or looping symlink)"; errors=$((errors + 1)); continue; fi
  if [ ! -s "$f" ]; then echo "FAIL: ${f#"$CFG"/} is empty"; errors=$((errors + 1)); continue; fi
  shape_out=$(jq -r -L "$DEPLOY_DIR" 'include "shape"; if length == 1 then .[0] | monitor_shape[] else "expected exactly one JSON value, found \(length)" end' -s "$f" 2>&1) \
    || { echo "FAIL: ABI-1 ${f#"$CFG"/}: not parseable by jq: $shape_out"; errors=$((errors + 1)); continue; }
  if [ -n "$shape_out" ]; then
    while IFS= read -r line; do echo "FAIL: ${f#"$CFG"/} A0 $line"; errors=$((errors + 1)); done <<<"$shape_out"
    continue
  fi
  out=$(jq -r -L "$ABI_DIR" --slurpfile ref "$REF" '
    include "normalize";
    def idents: [match("(?:^|[\\s(])([A-Za-z_][A-Za-z0-9_]*)(?:\\[[^\\]]*\\]|\\.[A-Za-z0-9_]+)*\\s*(?:==|!=|>=|<=|>|<|(?i:contains)|(?i:starts_with)|(?i:ends_with))"; "g").captures[0].string];
    def txfields: ["value","from","to","hash","gas_price","max_fee_per_gas","max_priority_fee_per_gas","gas_limit","nonce","input","gas_used","transaction_index"];
    def sym: if . == null then empty else (select(test("&&|[|][|]")) | "A7 expression uses && or || (use AND / OR): \(.)") end;

    def layout_check($refs):
      (.addresses[]? | select((.contract_spec | type) == "array" and (.contract_spec | length) > 0)) as $a
      | ($refs[$a.address | ascii_downcase]) as $r
      | if $r == null then "A8 address \($a.address) carries a contract_spec but has no entry in the reference ABI"
        else
          ($r.abi | map({key: (.type + " " + sig), value: .}) | from_entries) as $idx
          | ($a.contract_spec | norm_abi)[] as $e
          | ($e.type + " " + ($e | sig)) as $k
          | if $idx[$k] == null then
              "A9 \($e.type) \($e | sig) at \($a.address) is not in the reference ABI"
              + ((($r.abi | map(select(.type == $e.type and .name == $e.name) | sig)) | if length > 0 then " (reference has: " + join(" | ") + ")" else "" end))
            elif $idx[$k] != $e then
              "A9 \($e.type) \($e | sig) at \($a.address) differs from the reference: monitor \($e | tojson) vs reference \($idx[$k] | tojson)"
            else empty end
        end;

    . as $m
    | ([.addresses[]?.contract_spec? | select(type == "array") | .[]]) as $abi
    | ([$abi[] | select(.type == "event")]) as $events
    | ([$abi[] | select(.type == "function")]) as $functions
    | (.match_conditions // {}) as $mc
    | layout_check($ref[0].contracts),
      (if (($mc.events // []) + ($mc.functions // []) + ($mc.transactions // []) | length) == 0
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
compared=$(jq -s '[.[] | .addresses[]? | .contract_spec? | select(type == "array") | .[] | select(.type == "event" or .type == "function")] | length' "${files[@]}") \
  || { echo "abi-check: FAIL: could not count the compared contract_spec entries (jq failed)" >&2; exit 1; }
echo "abi-check: OK ($n monitors: every event/function signature and expression parameter exists in the ABI; $compared contract_spec events/functions equal the reference layout)"
