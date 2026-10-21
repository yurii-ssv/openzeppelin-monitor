"""Read-only: fetch the proxy's WeightedRootProposed / RootCommitted / OracleReplaced logs for a block range.

  python3 -I fetch_oracle_logs.py <rpc_url> <from> <to> <out.json> [chunk]
Only eth_getLogs and eth_blockNumber are sent (no transaction of any kind).
"""
import json
import sys
import time
import urllib.request

PROXY = "0xDD9BC35aE942eF0cFa76930954a156B3fF30a4E1"
TOPICS = [
    "0xc0516bad4be527b56ad33b7087029ffee230e390e2f2d1076597820c9e170b67",  # WeightedRootProposed
    "0x6965b9c8509097ad68ba0eb17fcb9436f4a72a057fe5ac556737580bce80d840",  # RootCommitted
]


def call(url, method, params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(url, body, {"content-type": "application/json", "user-agent": "cp2b-evidence"})
    with urllib.request.urlopen(req, timeout=60) as r:
        out = json.load(r)
    if "error" in out:
        raise RuntimeError(out["error"])
    return out["result"]


def main():
    url, lo, hi, out = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
    chunk = int(sys.argv[5]) if len(sys.argv) > 5 else 5000
    logs = []
    for s in range(lo, hi + 1, chunk):
        e = min(s + chunk - 1, hi)
        for attempt in range(5):
            try:
                part = call(url, "eth_getLogs", [{"address": PROXY, "fromBlock": hex(s), "toBlock": hex(e),
                                                  "topics": [TOPICS]}])
                break
            except Exception as exc:  # noqa: BLE001
                print(f"{s}-{e} attempt {attempt}: {exc}", file=sys.stderr)
                time.sleep(2 + 2 * attempt)
        else:
            raise SystemExit(f"range {s}-{e} failed")
        if not isinstance(part, list):
            raise SystemExit(f"range {s}-{e}: non-list result")
        logs += part
        print(f"{s}-{e}: {len(part)} logs", file=sys.stderr)
    with open(out, "w") as fh:
        json.dump({"rpc": url.split("//")[1].split("/")[0], "from": lo, "to": hi, "logs": logs}, fh)
    print(json.dumps({"logs": len(logs)}))


if __name__ == "__main__":
    main()
