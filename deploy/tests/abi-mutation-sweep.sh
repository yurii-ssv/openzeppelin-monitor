#!/usr/bin/env bash
# CP1A repair 2 (F8b): mutation sweep of the ABI layout gate over EVERY contract_spec entry of EVERY
# monitor under deploy/config. For each mutation kind the same mutation is applied to all applicable
# event/function entries of one monitor at once, written to a throw-away config dir, and
# deploy/check-abi.sh must (a) exit non-zero and (b) report exactly one A9 line per mutated entry. A
# control run with no mutation must exit 0 (proves the throw-away copy itself is green). Nothing in the
# tracked tree is written.
#
#   [ABI_CHECK=<gate script>] deploy/tests/abi-mutation-sweep.sh [--config-dir <dir>] [--reference <file>]
#
# Exit 0 only if every kind behaves as stated for every monitor. Needs jq.
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
CFG="$ROOT/deploy/config"; REF=""
while [ $# -gt 0 ]; do
  case "$1" in
    --config-dir) CFG=${2:?}; shift 2 ;;
    --reference) REF=${2:?}; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
{ command -v jq >/dev/null && [ "$(jq -n '1 + 1' 2>/dev/null)" = 2 ]; } || { echo "abi-mutation-sweep: jq is required (not on PATH, or not working)" >&2; exit 2; }
CHECK=("${ABI_CHECK:-$ROOT/deploy/check-abi.sh}") # ABI_CHECK: run the sweep against another gate (e.g. the repair-1 one, to see it red)
TMP=$(mktemp -d) || { echo "abi-mutation-sweep: cannot create a temporary directory" >&2; exit 2; }
trap 'rm -rf "$TMP"' EXIT

# kind name -> jq function body applied to ONE contract_spec entry (returns the mutated entry)
declare -a KINDS=(
  "event-indexed-flipped"
  "event-indexed-swapped-between-two-inputs"
  "event-input-type-changed"
  "event-input-order-swapped"
  "event-extra-input"
  "event-missing-input"
  "event-input-renamed"
  "event-anonymous-flipped"
  "function-input-type-changed"
  "function-input-order-swapped"
  "function-extra-input"
  "function-missing-input"
  "function-output-type-changed"
  "function-output-removed"
  "function-extra-output"
  "function-output-renamed"
)
filter_for() {
  case "$1" in
    event-indexed-flipped)   echo 'if .type=="event" then .inputs |= map(.indexed = (.indexed|not)) else . end' ;;
    event-indexed-swapped-between-two-inputs) echo 'if .type=="event" and (.inputs|length)>=2 then (.inputs[0].indexed) as $a | .inputs[0].indexed = .inputs[1].indexed | .inputs[1].indexed = $a else . end' ;;
    event-input-type-changed) echo 'if .type=="event" and (.inputs|length)>=1 then .inputs[0].type = (if .inputs[0].type=="bool" then "uint8" else "bool" end) | del(.inputs[0].components) else . end' ;;
    event-input-order-swapped) echo 'if .type=="event" and (.inputs|length)>=2 then .inputs |= ([.[1], .[0]] + .[2:]) else . end' ;;
    event-extra-input)       echo 'if .type=="event" then .inputs += [{"name":"extra","type":"uint256","indexed":false,"internalType":"uint256"}] else . end' ;;
    event-missing-input)     echo 'if .type=="event" and (.inputs|length)>=1 then .inputs |= .[0:-1] else . end' ;;
    event-input-renamed)     echo 'if .type=="event" and (.inputs|length)>=1 then .inputs[0].name += "X" else . end' ;;
    event-anonymous-flipped) echo 'if .type=="event" then .anonymous = ((.anonymous // false)|not) else . end' ;;
    function-input-type-changed) echo 'if .type=="function" and (.inputs|length)>=1 then .inputs[0].type = (if .inputs[0].type=="bool" then "uint8" else "bool" end) | del(.inputs[0].components) else . end' ;;
    function-input-order-swapped) echo 'if .type=="function" and (.inputs|length)>=2 then .inputs |= ([.[1], .[0]] + .[2:]) else . end' ;;
    function-extra-input)    echo 'if .type=="function" then .inputs += [{"name":"extra","type":"uint256","internalType":"uint256"}] else . end' ;;
    function-missing-input)  echo 'if .type=="function" and (.inputs|length)>=1 then .inputs |= .[0:-1] else . end' ;;
    function-output-type-changed) echo 'if .type=="function" and (.outputs|length)>=1 then .outputs[0].type = (if .outputs[0].type=="bool" then "uint8" else "bool" end) | del(.outputs[0].components) else . end' ;;
    function-output-removed) echo 'if .type=="function" and (.outputs|length)>=1 then .outputs |= .[0:-1] else . end' ;;
    function-extra-output)   echo 'if .type=="function" then .outputs += [{"name":"extra","type":"uint256","internalType":"uint256"}] else . end' ;;
    function-output-renamed) echo 'if .type=="function" and (.outputs|length)>=1 then .outputs[0].name += "X" else . end' ;;
    *) return 1 ;;
  esac
}

fail=0; runs=0
mutated_by_kind=(); monitors_by_kind=() # indexed like KINDS (macOS bash 3.2 has no associative arrays)
for i in "${!KINDS[@]}"; do mutated_by_kind[$i]=0; monitors_by_kind[$i]=0; done
for f in "$CFG"/monitors/*.json; do
  [ -f "$f" ] && [ -r "$f" ] || { echo "FAIL  ${f#"$ROOT"/} is not a readable regular file (monitor not swept)"; fail=$((fail + 1)); continue; }
  base=$(basename "$f")
  # a jq that fails here must not look like "nothing to mutate": every skipped monitor is a counted failure
  specced=$(jq '[.addresses[]? | .contract_spec? | select(type == "array" and length > 0)] | length' "$f") \
    || { echo "FAIL  $base: jq failed counting its contract_spec entries (monitor not swept)"; fail=$((fail + 1)); continue; }
  case "$specced" in '' | *[!0-9]*) echo "FAIL  $base: contract_spec count is not a number ('$specced')"; fail=$((fail + 1)); continue ;; esac
  [ "$specced" -gt 0 ] || continue
  d="$TMP/cfg"; rm -rf "$d"; mkdir -p "$d/monitors"
  cp "$f" "$d/monitors/$base" || { echo "FAIL  $base: cannot copy the monitor"; fail=$((fail + 1)); continue; }
  # control: unmutated copy is green
  args=(--config-dir "$d"); [ -z "$REF" ] || args+=(--reference "$REF")
  runs=$((runs + 1))
  if ! "${CHECK[@]}" "${args[@]}" >/dev/null 2>&1; then echo "FAIL  $base control (no mutation) is not green"; fail=$((fail + 1)); continue; fi
  for i in "${!KINDS[@]}"; do
    kind=${KINDS[$i]}
    flt=$(filter_for "$kind")
    # n = entries whose normalized layout changed; the mutated file replaces every contract_spec
    n=$(jq -L "$ROOT/deploy/abi" "include \"normalize\"; [.addresses[]? | .contract_spec? | select(type == \"array\") | . as \$a | (\$a | map($flt)) as \$b | [range(0; \$a | length) | select([\$a[.] | norm_entry] != [\$b[.] | norm_entry])] | length] | add // 0" "$f") \
      || { echo "FAIL  $base $kind: jq failed counting the mutated entries"; fail=$((fail + 1)); continue; }
    case "$n" in '' | *[!0-9]*) echo "FAIL  $base $kind: mutated-entry count is not a number ('$n')"; fail=$((fail + 1)); continue ;; esac
    jq "(.addresses[]? | select((.contract_spec | type) == \"array\") | .contract_spec) |= map($flt)" "$f" > "$d/monitors/$base" \
      || { echo "FAIL  $base $kind: jq failed writing the mutated monitor"; fail=$((fail + 1)); continue; }
    out=$("${CHECK[@]}" "${args[@]}" 2>&1); rc=$?
    runs=$((runs + 1))
    a9=$(grep -c ' A9 ' <<<"$out")
    if [ "$n" -eq 0 ]; then
      [ $rc -eq 0 ] || { echo "FAIL  $base $kind: nothing mutated but exit $rc"; fail=$((fail + 1)); }
      continue
    fi
    mutated_by_kind[$i]=$(( mutated_by_kind[i] + n )); monitors_by_kind[$i]=$(( monitors_by_kind[i] + 1 ))
    if [ $rc -eq 0 ] || [ "$a9" -ne "$n" ]; then
      echo "FAIL  $base $kind: $n entries mutated, exit $rc, $a9 A9 line(s)"; fail=$((fail + 1))
    fi
  done
done
for i in "${!KINDS[@]}"; do
  kind=${KINDS[$i]}
  printf '%-44s monitors %2s  entries mutated %4s\n' "$kind" "${monitors_by_kind[$i]}" "${mutated_by_kind[$i]}"
  [ "${mutated_by_kind[$i]}" -gt 0 ] || { echo "FAIL  $kind mutated nothing anywhere (sweep proves nothing for it)"; fail=$((fail + 1)); }
done
echo "abi-mutation-sweep: $runs checker runs, $fail failure(s)"
[ $fail -eq 0 ]
