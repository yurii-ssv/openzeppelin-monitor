"""Replay a saved eth_getLogs dump (fetch_oracle_logs.py / fetch_round_logs.py) through M9 and M10 on a FakeChain at
the real block numbers (tests/fixtures_oracle.replay_mainnet) and print every finding and message. No network.

  reconcile/.venv/bin/python plans/monitoring-service/workers/cp2b/evidence/live_replay.py <logs.json> [after_blocks]
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
sys.path[:0] = [str(ROOT / "reconcile"), str(ROOT / "reconcile" / "tests")]

from fixtures_oracle import incidents, replay_mainnet, sent  # noqa: E402

WRP = "0xc0516bad4be527b56ad33b7087029ffee230e390e2f2d1076597820c9e170b67"


def rows_from_logs(logs):
    rows = []
    for lg in sorted(logs, key=lambda x: (int(x["blockNumber"], 16), int(x["logIndex"], 16))):
        b, li, tx = int(lg["blockNumber"], 16), int(lg["logIndex"], 16), lg["transactionHash"]
        root, ref = lg["topics"][1], int(lg["topics"][2], 16)
        if lg["topics"][0] == WRP:
            data = lg["data"][2:]
            w = [int(data[i:i + 64], 16) for i in range(0, 256, 64)]
            rows.append((b, li, tx, "V", root, ref, w[0], w[1], w[2], "0x%040x" % w[3]))
        else:
            rows.append((b, li, tx, "C", root, ref, 0, 0, 0, None))
    return rows


def main():
    dump = json.load(open(sys.argv[1]))
    after = int(sys.argv[2]) if len(sys.argv) > 2 else 400
    rows = rows_from_logs(dump["logs"])
    with tempfile.TemporaryDirectory() as d:
        _, w = replay_mainnet(Path(d), rows, after=after, ids=("m9", "m10"))
        out = {"source": os.path.basename(sys.argv[1]), "rpc": dump.get("rpc"), "logs": len(rows),
               "rounds": len({r[5] for r in rows}), "commits": sum(r[3] == "C" for r in rows),
               "checkpoint": w.checkpoint()["block"],
               "incidents": {c: [(i["key"], i["severity"], i["state"]) for i in incidents(w, c)] for c in ("m9", "m10")},
               "messages": {c: len(sent(w, c)) for c in ("m9", "m10")},
               "health": [r[0] for r in w.db.conn.execute("SELECT key FROM outbox WHERE severity='health'")],
               "runs": {c: w.db.meta(f"run:{c}") for c in ("m9", "m10")}}
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
