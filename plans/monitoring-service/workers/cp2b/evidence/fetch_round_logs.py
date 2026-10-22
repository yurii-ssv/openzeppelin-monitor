"""Read-only: fetch WeightedRootProposed / RootCommitted logs in [ref+1, ref+span] for each given round blockNum.

  python3 -I fetch_round_logs.py <rpc_url> <out.json> <chunk> <span> <ref> [<ref> ...]
Only eth_getLogs is sent. Used because the public archive endpoints cap eth_getLogs at 10-50 blocks.
"""
import json
import sys
import time

from fetch_oracle_logs import PROXY, TOPICS, call


def main():
    url, out, chunk, span = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
    refs = [int(x) for x in sys.argv[5:]]
    logs, ranges = [], []
    for ref in refs:
        for s in range(ref + 1, ref + span + 1, chunk):
            e = min(s + chunk - 1, ref + span)
            for attempt in range(6):
                try:
                    part = call(url, "eth_getLogs", [{"address": PROXY, "fromBlock": hex(s), "toBlock": hex(e),
                                                      "topics": [TOPICS]}])
                    break
                except Exception as exc:  # noqa: BLE001
                    print(f"{s}-{e} attempt {attempt}: {exc}", file=sys.stderr)
                    time.sleep(2 + 3 * attempt)
            else:
                raise SystemExit(f"range {s}-{e} failed")
            if not isinstance(part, list):
                raise SystemExit(f"range {s}-{e}: non-list result")
            logs += part
            ranges.append([s, e])
            time.sleep(0.2)
        print(f"ref {ref}: {len(logs)} logs so far", file=sys.stderr)
    with open(out, "w") as fh:
        json.dump({"rpc": url.split("//")[1].split("/")[0], "refs": refs, "ranges": ranges, "logs": logs}, fh)
    print(json.dumps({"logs": len(logs), "requests": len(ranges)}))


if __name__ == "__main__":
    main()
