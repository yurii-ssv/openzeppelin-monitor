"""CP2d repair 1 falsifiers: each mutation of the checks must redden its named tests; restored, they pass.

Mutations run on a scratch copy of reconcile/ (never the worktree). Run from the worktree root:

  reconcile/.venv/bin/python plans/monitoring-service/workers/cp2d/falsify_repair1.py <scratch dir>
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
    # repair 1 (F1-F4 and the generalisation rows)
    ("R1-M1-absence", M1, "        elif not self._in_storage(ctx, key, block):",
     "        elif True:", T1, "empty_or_failed_inventory or outside_a_reorg"),
    ("R1-M2-persisted", M2, "        if rows:\n            slots = []", "        if False:\n            slots = []", T2,
     "empty_api_is_not_recovery or empty_api_during or restart_with_an_empty_api"),
    ("R1-M2-bound", M2, "                if missing > 0 and e[\"validator_count\"] + missing > limit:",
     "                if False:", T2, "partial_api"),
    ("R1-M2-degraded", M2, "            elif degraded:\n", "            elif False:\n", T2, "revert_does_not_settle"),
    ("R1-M2-calldata", M2, "            if dec and cl:  #", "            if False:  #", T2, "own_calldata_tuple"),
    ("R1-M2-reorg-raise", M2, "        if stuck:  #", "        if False:  #", T2, "reorg_run_raises"),
    ("R2-M1-intra-block", M1, "                for group in ordered[:-1]:\n                    self._superseded(",
     "                for group in []:\n                    self._superseded(", T1, "same_block"),
    ("R3-M2-unsettle", M2, "        if reorg:  # README §6.2: a settlement", "        if False:  # README §6.2: a settlement",
     T2, "orphaning_the_settlement"),
    ("R3-M1-unclear", M1, '        db.conn.execute("UPDATE m1_detections SET cleared_block = NULL WHERE cleared_block > ?", '
                          '(ancestor,))', "        pass", T1, "orphaning_the_clearing_run"),
    ("R4-M1-earliest", M1, "        match = next((e for e in cands if", "        match = next((e for e in reversed(cands) if",
     T1, "earliest_repair_tuple or repair_tuple_is_the_earliest or tie_is_the_earliest"),
    # the sweep must catch the same regressions without the targeted tests
    ("S-M1-absence", M1, "        elif not self._in_storage(ctx, key, block):", "        elif True:", TS, "m1_adversarial"),
    ("S-M1-intra-block", M1, "                for group in ordered[:-1]:\n                    self._superseded(",
     "                for group in []:\n                    self._superseded(", TS, "m1_adversarial"),
    ("S-M2-persisted", M2, "        if rows:\n            slots = []", "        if False:\n            slots = []", TS,
     "m2_adversarial"),
    ("S-M2-unsettle", M2, "        if reorg:  # README §6.2: a settlement", "        if False:  # README §6.2: a settlement",
     TS, "m2_adversarial"),
    ("S-M2-degraded", M2, "            elif degraded:\n", "            elif False:\n", TS, "m2_adversarial"),
    # attempt-1 falsifiers, re-run on the repaired code (F-M1c re-targeted: the block's final transaction group)
    ("F-M1a", M1, "if repaired_before or liquidatable:", "if repaired_before and liquidatable:", T1,
     "re_poison_after_repair_pages or poisoned_and_liquidatable_pages or thresholds_from_chain"),
    ("F-M1b", M1, "                return False\n            raise", "                return True\n            raise",
     f"{T1} test_m1_receipts.py", "poisoned_receipt_warns or flags_recorded"),
    ("F-M1c", M1, "                group = ordered[-1]\n", "                group = ordered[0]\n", T1, "same_block"),
    ("F-M1d", M1, "if reorg:\n            self._rewind", "if False:\n            self._rewind", T1, "reorg"),
    ("F-M2a", M2, "> limit), key=lambda", ">= limit), key=lambda", T2, "hard_boundary"),
    ("F-M2b", M2, "if gap < self.config", "if gap <= self.config", T2, "exactly_900"),
    ("F-M2c", M2, "if regs > headroom:", "if regs >= headroom:", T2, "equal_to_headroom"),
    ("F-M2d", M2, "i.canonical = 0", "i.canonical = 9", T2, "inbox_revert_of_an_orphaned_block"),
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
