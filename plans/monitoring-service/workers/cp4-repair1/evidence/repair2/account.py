"""Accounting of the Monitor's unevaluated blocks on ms-cp4r (D21 live proof).

argv: monitor log (ANSI-stripped), worker outbox export (JSON lines or JSON list of rows with key/text), missed-blocks
file snapshot (JSON, may be '[]'), unevaluated_after_s, now (unix).
Every block named in a `Block N was not evaluated` ERROR must be (a) in a `recorded as missed for recovery` line and
(b) recovered (`Successfully recovered missed block` with block=N), or announced by monitor-unevaluated, or still
waiting in the file for less than unevaluated_after_s. Prints counts and every exception.
"""
import json
import re
import sys

log, outbox_path, missed_path, after, now = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4]), float(sys.argv[5])
text = open(log).read()
filter_errors = len(re.findall(r"ERROR filter_block", text))
proxy_429 = None
not_eval = [int(b) for b in re.findall(r"Block (\d+) was not evaluated", text)]
recorded = set()
for lst in re.findall(r"recorded as missed for recovery: \[([0-9, ]*)\]", text):
    recorded |= {int(x) for x in lst.split(",") if x.strip()}
def blocks_on(marker):
    return {int(m) for line in text.splitlines() if marker in line for m in re.findall(r"block[:=] ?(\d+)", line)}


recovered = blocks_on("Successfully recovered missed block")
gave_up = blocks_on("Block recovery failed after max retries")
retry_fail = len(re.findall(r"Block recovery attempt failed, will retry", text))
rows = []
raw = open(outbox_path).read().strip()
try:
    rows = json.loads(raw)
except ValueError:
    rows = [json.loads(l) for l in raw.splitlines() if l.strip()]
announced = set()
for r in rows:
    t = r.get("text") or r.get("body") or ""
    k = r.get("key", "")
    if "monitor-unevaluated" in k or "monitor-unevaluated" in t:
        for lo, hi in re.findall(r"locks (\d+)-(\d+) \(\d+\)", t):
            announced |= set(range(int(lo), int(hi) + 1))
waiting = {}
try:
    for e in json.loads(open(missed_path).read() or "[]"):
        waiting[e["block_number"]] = e
except ValueError:
    waiting = None
print(f"ERROR filter_block lines: {filter_errors}")
print(f"'Block N was not evaluated' ERRORs: {len(not_eval)} ({len(set(not_eval))} distinct blocks)")
print(f"recorded as missed: {len(recorded)}; recovered by the recovery job: {len(recovered)}; "
      f"given up: {len(gave_up)}; failed retry attempts: {retry_fail}; announced (monitor-unevaluated): {len(announced)}")
print(f"still in the missed-blocks file: {None if waiting is None else len(waiting)}")
unaccounted = []
for b in sorted(set(not_eval)):
    if b not in recorded:
        unaccounted.append((b, "not recorded"))
    elif b in recovered or b in announced:
        continue
    elif waiting is not None and b in waiting and waiting[b]["status"] in ("Pending", "Recovering") \
            and now - waiting[b]["first_missed_at"] < after:
        continue
    else:
        unaccounted.append((b, "neither recovered nor announced"))
print(f"failed-and-unaccounted: {len(unaccounted)} {unaccounted[:20]}")
