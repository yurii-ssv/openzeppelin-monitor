#!/bin/sh
# Failed-transaction inbox bridge (plan section 6): append one JSON line per matched failed
# transaction to ./data/inbox/failed-tx.jsonl for the worker to consume.
#
# Run by the Monitor as a script trigger (`sh -c <content>`; cwd is the Monitor's working
# directory, /app in the container). Input on stdin: {"monitor_match": {"EVM": {...}}, "args": ...}.
# INBOX_PATH overrides the target file. Exit non-zero on any failure so the Monitor logs it.
set -eu

INBOX_PATH=${INBOX_PATH:-./data/inbox/failed-tx.jsonl}
mkdir -p "$(dirname "$INBOX_PATH")"

input=$(cat)

observed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# blockNumber arrives as a hex quantity (0x...); the contract wants a decimal number. Converted in
# jq, not with printf, so the result does not depend on which sh runs this script.
line=$(printf '%s' "$input" | jq -ce --arg observed_at "$observed_at" '
  def hex2int: ltrimstr("0x") | explode
    | reduce .[] as $c (0; . * 16 + (if $c >= 97 then $c - 87 elif $c >= 65 then $c - 55 else $c - 48 end));
  .monitor_match.EVM as $m
  | {
      schema: 1,
      tx_hash: $m.transaction.hash,
      block: ($m.transaction.blockNumber | hex2int),
      block_hash: $m.transaction.blockHash,
      from: $m.transaction.from,
      to: $m.transaction.to,
      selector: ($m.transaction.input[0:10]),
      function: ($m.matched_on.functions[0].signature),
      status: ($m.matched_on.transactions[0].status | ascii_downcase),
      monitor: $m.monitor.name,
      observed_at: $observed_at
    }
  | if (.tx_hash and .block_hash and .from and .to and .selector and .function) then . else error("incomplete match") end')

# One short write in append mode: lines are far below PIPE_BUF, so concurrent appends do not interleave.
printf '%s\n' "$line" >> "$INBOX_PATH"
