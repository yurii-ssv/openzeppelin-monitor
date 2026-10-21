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

# blockNumber arrives as a hex quantity (0x...); the contract wants a decimal number.
block_hex=$(printf '%s' "$input" | jq -er '.monitor_match.EVM.transaction.blockNumber')
block=$(printf '%d' "$block_hex")
observed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)

line=$(printf '%s' "$input" | jq -ce \
  --argjson block "$block" --arg observed_at "$observed_at" '
  .monitor_match.EVM as $m
  | {
      schema: 1,
      tx_hash: $m.transaction.hash,
      block: $block,
      block_hash: $m.transaction.blockHash,
      from: $m.transaction.from,
      to: $m.transaction.to,
      selector: ($m.transaction.input[0:10]),
      function: ($m.matched_on.functions[0].signature),
      status: ($m.matched_on.transactions[0].status | ascii_downcase),
      monitor: $m.monitor.name,
      observed_at: $observed_at
    }')

# One short write in append mode: lines are far below PIPE_BUF, so concurrent appends do not interleave.
printf '%s\n' "$line" >> "$INBOX_PATH"
