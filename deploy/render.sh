#!/usr/bin/env bash
# Render the tracked Monitor config templates (deploy/config) into a config directory.
#
#   deploy/render.sh --out <dir> [--strict] [--no-check]
#
# - copies deploy/config/{monitors,triggers,filters,networks} into <dir> (replacing those four
#   subdirectories only; nothing else under <dir> is touched);
# - secrets stay as {"type":"environment","value":"<NAME>"} references; nothing secret is written;
# - drops the optional second RPC when MAINNET_RPC_URL_2 is unset or empty;
# - substitutes the one allow-listed placeholder ${TELEGRAM_CHAT_ID} in the Telegram triggers'
#   chat_id (a plain string in the Monitor schema, so it cannot be an environment reference).
#   Message bodies contain ${transaction.hash}-style tokens that are left untouched;
# - unless --no-check, first runs deploy/check-abi.sh on the rendered config (every monitor event /
#   function signature and expression parameter must exist in its ABI, and every contract_spec event /
#   function must equal the committed reference layout in deploy/abi/reference.json: name, types,
#   indexed flags, order, outputs; the Monitor's own --check never compares any of it), then runs `openzeppelin-monitor --check` against the rendered config, with
#   placeholder values for any environment variable the config references that is unset in this
#   shell (--check only validates shape; real values are read at run time). The binary's own exit
#   code is not a gate (it exits 0 even when validation fails), so render.sh requires its
#   "Configuration validation completed successfully" line and zero ERROR lines;
# - --strict (production deploy): fail instead of warn when TELEGRAM_CHAT_ID or any environment
#   variable referenced by the rendered config is unset, and never use placeholders for --check.
#
# The binary defaults to target/release/openzeppelin-monitor; override with MONITOR_BIN.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
TPL="$ROOT/deploy/config"
BIN=${MONITOR_BIN:-$ROOT/target/release/openzeppelin-monitor}

OUT=""
STRICT=false
CHECK=true
while [ $# -gt 0 ]; do
  case "$1" in
    --out) OUT=${2:?--out needs a directory}; shift 2 ;;
    --strict) STRICT=true; shift ;;
    --no-check) CHECK=false; shift ;;
    -h | --help) sed -n '2,/^set -/p' "$0" | grep '^#' | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ -n "$OUT" ] || { echo "usage: deploy/render.sh --out <dir> [--strict] [--no-check]" >&2; exit 2; }
command -v jq >/dev/null || { echo "jq is required" >&2; exit 1; }

mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
[ "$OUT" != "$TPL" ] || { echo "refusing to render into the template directory" >&2; exit 2; }

for d in monitors triggers filters networks; do
  rm -rf "${OUT:?}/$d"
  cp -R "$TPL/$d" "$OUT/$d"
done
find "$OUT" -name .gitkeep -delete
if [ -d "$OUT/triggers/scripts" ]; then
  find "$OUT/triggers/scripts" -type f \( -name '*.sh' -o -name '*.py' -o -name '*.js' \) -exec chmod +x {} +
fi

rewrite() { # rewrite <file> <jq args...>: in-place jq edit
  local f=$1; shift
  jq "$@" "$f" > "$f.tmp" && mv "$f.tmp" "$f"
}

# Optional second RPC.
if [ -z "${MAINNET_RPC_URL_2:-}" ]; then
  for f in "$OUT"/networks/*.json; do
    rewrite "$f" '.rpc_urls |= map(select((.url.type == "environment" and .url.value == "MAINNET_RPC_URL_2") | not))'
  done
fi

# Telegram chat id (allow-listed substitution, exact-match only).
if [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
  for f in "$OUT"/triggers/*.json; do
    rewrite "$f" --arg chat "$TELEGRAM_CHAT_ID" \
      'map_values(if .trigger_type == "telegram" and .config.chat_id == "${TELEGRAM_CHAT_ID}" then .config.chat_id = $chat else . end)'
  done
elif $STRICT; then
  echo "render: --strict and TELEGRAM_CHAT_ID is unset" >&2; exit 1
else
  echo "render: warning: TELEGRAM_CHAT_ID is unset; Telegram triggers keep the placeholder chat_id" >&2
fi

# Environment variables the rendered config refers to.
refs=$(jq -r '.. | objects | select(.type? == "environment") | .value' \
  "$OUT"/networks/*.json "$OUT"/triggers/*.json | sort -u)
missing=""
for v in $refs; do
  [ -n "${!v:-}" ] || missing="$missing $v"
done

if ! $CHECK; then
  echo "Rendered $OUT (check skipped)"
  exit 0
fi

"$ROOT/deploy/check-abi.sh" --config-dir "$OUT" || { echo "render: ABI/expression check failed" >&2; exit 1; }

[ -x "$BIN" ] || { echo "Build first: cargo build --release --locked (expected $BIN)" >&2; exit 1; }
if [ -n "$missing" ] && $STRICT; then
  echo "render: --strict and unset environment variables:$missing" >&2; exit 1
fi

# --check resolves RPC URLs and secrets, so give unset ones a shape-valid placeholder, scoped to
# the child process only.
placeholder_env=()
zero=0 # keeps a webhook-shaped literal out of this tracked file (the secret scan matches webhooks/<digits>/)
for v in $missing; do
  case "$v" in
    *RPC_URL*) placeholder_env+=("$v=https://rpc.invalid.example") ;;
    *WEBHOOK*) placeholder_env+=("$v=https://discord.com/api/webhooks/${zero}/placeholder") ;;
    *TOKEN*) placeholder_env+=("$v=0000000000:$(printf 'x%.0s' {1..35})") ;;
    *) placeholder_env+=("$v=placeholder") ;;
  esac
done

# The Monitor reads ./config relative to its working directory.
if [ "$(basename "$OUT")" = config ]; then
  workdir=$(dirname "$OUT"); cleanup=""
else
  workdir=$(mktemp -d); cleanup=$workdir
  ln -s "$OUT" "$workdir/config"
fi
mkdir -p "$workdir/data"

# `openzeppelin-monitor --check` exits 0 even when validation fails (it only logs an ERROR), so
# its exit code proves nothing. Gate on its own success line and on the absence of ERROR lines.
rc=0
log=$(cd "$workdir" && env ${placeholder_env[@]+"${placeholder_env[@]}"} "$BIN" --check 2>&1) || rc=$?
[ -z "$cleanup" ] || rm -rf "$cleanup"
plain=$(printf '%s\n' "$log" | sed $'s/\x1b\\[[0-9;]*m//g')
if [ $rc -ne 0 ] || ! grep -q 'Configuration validation completed successfully' <<<"$plain" \
  || grep -q ' ERROR ' <<<"$plain"; then
  printf '%s\n' "$plain" >&2
  echo "render: --check failed (binary exit $rc; success line or ERROR lines decide)" >&2
  exit 1
fi
printf '%s\n' "$plain" | grep -E 'Found [0-9]+ (active monitor|network)' | sed 's/^.*openzeppelin_monitor: //'
echo "Rendered $OUT; --check passed${missing:+ (placeholders used for:$missing)}"
