"""CP2d repair 3 falsifiers (QA attempt 3 F1 and its generalisation): each mutation of the checks must redden its named tests; restored, they pass. The earlier mutations stay in falsify_repair2.py (re-run unchanged).

Mutations run on a scratch copy of reconcile/ (never the worktree). Run from the worktree root:

  reconcile/.venv/bin/python plans/monitoring-service/workers/cp2d/falsify_repair3.py <scratch dir>
"""

import os
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path.cwd()
scratch = pathlib.Path(sys.argv[1])
shutil.rmtree(scratch, ignore_errors=True)
shutil.copytree(ROOT / "reconcile", scratch / "reconcile",
                ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache"))
python = str(ROOT / "reconcile/.venv/bin/python")
M1, M2 = "m1_stale_tuple.py", "m2_operator_capacity.py"
T1, T2, TS = "test_m1.py", "test_m2.py", "test_m1_m2_sweep.py"

CASES = [
    # F1 root cause: the transient report carries the revision a stale report of the same poisoning would carry
    ("R9-M1-transient-revision", M1,
     "            parts.append(f\"{'transient-re-poison' if re_poisoned else 'transient'}:{last['tx_hash']}:\"\n"
     "                         f\"{last['log_index']}\")\n",
     "            parts.append(f\"{'re-poison' if re_poisoned else 'poison'}:{last['tx_hash']}:{last['log_index']}\")\n"
     "            parts.append(\"repair:\" + keccak(last[\"repair_tuple\"].encode()).hex()[:12])\n",
     T1, "orphaning_the_repair_sends_the_live_poisoning"),
    ("R9-M1-transient-revision-sweep", M1,
     "            parts.append(f\"{'transient-re-poison' if re_poisoned else 'transient'}:{last['tx_hash']}:\"\n"
     "                         f\"{last['log_index']}\")\n",
     "            parts.append(f\"{'re-poison' if re_poisoned else 'poison'}:{last['tx_hash']}:{last['log_index']}\")\n"
     "            parts.append(\"repair:\" + keccak(last[\"repair_tuple\"].encode()).hex()[:12])\n",
     TS, "m1_adversarial"),
    # generalisation: the repair tuple and the state stay in the revision when a revert is newer
    ("R10-M1-repair-part", M1, "            parts.append(\"repair:\" + (keccak(", "            parts.append(\"repair\" or (keccak(",
     T1, "repair_tuple_found_after_the_alert"),
    ("R11-M1-revert-anchor", M1, "            parts.append(f\"revert:{reverts[-1]['tx_hash']}\")",
     "            parts[:] = [f\"revert:{reverts[-1]['tx_hash']}\"]", T1, "with_a_revert_then_a_reorg_orphaning"),
]

env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
ok = True
for name, file, old, new, tests, expr in CASES:
    p = scratch / "reconcile/checks" / file
    original = p.read_bytes()
    assert original.decode().count(old) == 1, (name, original.decode().count(old))
    cmd = [python, "-m", "pytest", "-q", "-p", "no:cacheprovider", *[f"reconcile/tests/{t}" for t in tests.split()],
           "-k", expr]
    try:
        p.write_text(original.decode().replace(old, new))
        red = subprocess.run(cmd, cwd=scratch, env=env, capture_output=True, text=True)
    finally:
        p.write_bytes(original)
    assert p.read_bytes() == (ROOT / "reconcile/checks" / file).read_bytes()
    green = subprocess.run(cmd, cwd=scratch, env=env, capture_output=True, text=True)
    last = lambda r: (r.stdout.strip().splitlines() or ["(no output)"])[-1]  # noqa: E731
    print(f"### {name}: {file} -k '{expr}' ({tests})")
    print(f"mutated exit={red.returncode} {last(red)}")
    print(f"restored exit={green.returncode} {last(green)}", flush=True)
    ok &= red.returncode == 1 and green.returncode == 0
sys.exit(0 if ok else 1)
