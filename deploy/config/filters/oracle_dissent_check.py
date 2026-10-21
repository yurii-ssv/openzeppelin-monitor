"""
Trigger condition for the oracle dissent monitors (matches on RootCommitted).

For every RootCommitted in the matched transaction, reads the WeightedRootProposed
votes for the same snapshot block (from the snapshot block up to the commit block)
and checks whether any oracle voted a different root.

Output contract (last stdout line): "true" drops the match, "false" keeps it.
  - dissent found       -> false (alert)
  - every vote agrees   -> true  (no alert)
  - RPC/parsing failure -> exit 1, which the monitor treats as "keep" (alert)

The RPC URL comes from the network config matching the match's network_slug
(./config/networks/*.json, relative to the monitor's working directory).
"""

import glob
import json
import os
import sys
import time
import urllib.request

ROOT_COMMITTED = "0x6965b9c8509097ad68ba0eb17fcb9436f4a72a057fe5ac556737580bce80d840"
WEIGHTED_ROOT_PROPOSED = "0xc0516bad4be527b56ad33b7087029ffee230e390e2f2d1076597820c9e170b67"


def rpc_url(network_slug):
    for path in glob.glob("./config/networks/*.json"):
        with open(path) as f:
            network = json.load(f)
        if network.get("slug") != network_slug:
            continue
        url = sorted(network["rpc_urls"], key=lambda u: -u.get("weight", 0))[0]["url"]
        if url["type"].lower() == "environment":
            return os.environ[url["value"]]
        return url["value"]
    raise RuntimeError(f"no network config for {network_slug}")


def get_logs(url, params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_getLogs", "params": [params]}).encode()
    last_error = None
    for attempt in range(2):
        try:
            request = urllib.request.Request(
                url, body, {"Content-Type": "application/json", "User-Agent": "ssv-network-monitor"}
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                reply = json.load(response)
            if "error" in reply:
                raise RuntimeError(reply["error"])
            return reply["result"]
        except Exception as e:  # noqa: BLE001 - retried, then reported
            last_error = e
            time.sleep(1 + attempt)
    raise RuntimeError(f"eth_getLogs failed: {last_error}")


def get_logs_range(url, params, from_block, to_block, chunk=10):
    """Whole range first; some providers cap eth_getLogs ranges, so fall back to small chunks."""
    try:
        return get_logs(url, {**params, "fromBlock": hex(from_block), "toBlock": hex(to_block)})
    except RuntimeError:
        logs = []
        for start in range(from_block, to_block + 1, chunk):
            end = min(start + chunk - 1, to_block)
            logs += get_logs(url, {**params, "fromBlock": hex(start), "toBlock": hex(end)})
        return logs


def main():
    match = json.load(sys.stdin)["monitor_match"]["EVM"]
    url = rpc_url(match["network_slug"])
    commit_block = int(match["transaction"]["blockNumber"], 16)

    dissent = []
    for log in match["logs"] or []:
        if log["topics"][0].lower() != ROOT_COMMITTED:
            continue
        committed_root, snapshot = log["topics"][1].lower(), log["topics"][2]
        votes = get_logs_range(
            url,
            {"address": log["address"], "topics": [WEIGHTED_ROOT_PROPOSED, None, snapshot]},
            int(snapshot, 16),
            commit_block,
        )
        for vote in votes:
            if vote["topics"][1].lower() != committed_root:
                oracle_id = int(vote["data"][2 + 128:2 + 192], 16)
                dissent.append(f"oracle {oracle_id} voted {vote['topics'][1]} in tx {vote['transactionHash']}")
        print(f"snapshot {int(snapshot, 16)}: {len(votes)} votes, committed {committed_root}", file=sys.stderr)

    for line in dissent:
        print(f"DISSENT {line}", file=sys.stderr)
    print("false" if dissent else "true")


if __name__ == "__main__":
    main()
