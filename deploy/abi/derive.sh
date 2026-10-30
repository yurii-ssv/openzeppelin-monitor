#!/usr/bin/env bash
# Regenerate (or verify) deploy/abi/reference.json from its two sources. Needs jq; the sources are
# build outputs that this repo does not carry, so the check/render gates never call this script.
#
#   deploy/abi/derive.sh --ssv-out <forge out dir> --safe-abi <gnosis_safe.json> [--out <file>] [--check]
#
#   --ssv-out   `out/` of `forge build --skip test --skip script` run in an ssv-network checkout at
#               f7ac4870074b9408855a7912f9c6e1f959996be8 (v2.0.0+f7ac487), e.g.
#                 git -C <ssv-network> archive f7ac487 | tar -x -C <scratch> && ln -s <node_modules> <scratch>/node_modules
#                 (cd <scratch> && forge build --skip test --skip script)
#   --safe-abi  dist/assets/v1.3.0/gnosis_safe.json of `npm pack @safe-global/safe-deployments@1.37.0`
#   --out       where to write (default: deploy/abi/reference.json)
#   --check     write nothing; exit 1 unless the committed reference.json equals what the sources give
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
SSV_OUT=""; SAFE=""; OUT="$HERE/reference.json"; CHECK=false
while [ $# -gt 0 ]; do
  case "$1" in
    --ssv-out) SSV_OUT=${2:?--ssv-out needs a directory}; shift 2 ;;
    --safe-abi) SAFE=${2:?--safe-abi needs a file}; shift 2 ;;
    --out) OUT=${2:?--out needs a file}; shift 2 ;;
    --check) CHECK=true; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
{ command -v jq >/dev/null && [ "$(jq -n '1 + 1' 2>/dev/null)" = 2 ]; } || { echo "derive: jq is required (not on PATH, or not working)" >&2; exit 2; }
[ -n "$SSV_OUT" ] && [ -n "$SAFE" ] || { echo "usage: derive.sh --ssv-out <dir> --safe-abi <file> [--out <file>] [--check]" >&2; exit 2; }
IMPL="$SSV_OUT/SSVNetworkSSVStakingUpgrade.sol/SSVNetworkSSVStakingUpgrade.json"
[ -f "$IMPL" ] || { echo "derive: $IMPL not found" >&2; exit 1; }
[ -f "$SAFE" ] || { echo "derive: $SAFE not found" >&2; exit 1; }

derived=$(jq -n -L "$HERE" --slurpfile prov "$HERE/provenance.json" --slurpfile ssv "$IMPL" --slurpfile safe "$SAFE" '
  include "normalize";
  $prov[0] as $p
  | {_provenance: ($p | del(.contracts)),
     contracts: ($p.contracts
       | .["0xdd9bc35ae942ef0cfa76930954a156b3ff30a4e1"] |= (. + {abi: ($ssv[0].abi | norm_abi)})
       | .["0xb35096b074fdb9bbac63e3adae0bbde512b2e6b6"] |= (. + {abi: ($safe[0].abi | norm_abi)}))}')
[ -n "$derived" ] || { echo "derive: the sources produced no output" >&2; exit 1; }
if $CHECK; then
  committed=$(jq . "$OUT") || { echo "derive: cannot read $OUT as JSON" >&2; exit 1; }
  [ "$derived" = "$committed" ] && { echo "derive: $OUT equals its sources"; exit 0; }
  echo "derive: $OUT differs from what the sources give" >&2; exit 1
fi
printf '%s\n' "$derived" > "$OUT"
echo "derive: wrote $OUT ($(jq '[.contracts[].abi | length] | add' "$OUT") entries)"
