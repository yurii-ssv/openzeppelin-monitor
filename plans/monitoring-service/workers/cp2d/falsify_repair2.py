"""CP2d repair 2 falsifiers (QA attempt 2; repair 1 and attempt-1 mutations re-run, re-targeted where the code moved): each mutation of the checks must redden its named tests; restored, they pass.

Mutations run on a scratch copy of reconcile/ (never the worktree). Run from the worktree root:

  reconcile/.venv/bin/python plans/monitoring-service/workers/cp2d/falsify_repair2.py <scratch dir>
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
    # repair 2 (QA attempt 2 F1-F4 and the generalisation rows)
    ("R5-M1-resolved", M1, "            keys |= self._resolved_since(db, ctx.reorg_ancestor)", "            keys |= set()",
     T1, "orphaning_the_resolution_of_an_api_only_key"),
    ("R5-M2-resolved", M2, "keys = open_keys | set(ctx.reorg_keys) | resolved_since |",
     "keys = open_keys | set(ctx.reorg_keys) |", T2, "resolved_hard_without_a_canonical_tuple"),
    ("R5-M2-history", M2, "cluster_id = ? AND block <= ?)\", (cid, cid, block - self.depth))",
     "cluster_id = ? AND block <= ?)\", (cid, cid, block))", T2,
     "restores_the_canonical_tuple or tuple_history_is_bounded"),
    ("R5-M2-unknown-health", M2, "        if unknown_hard:  #", "        if False:  #", T2, "without_an_open_incident"),
    ("R6-M1-unverified", M1, "            if maybe_by_unverified or (cls == \"unverified\" and maybe_poisoned):",
     "            if False:", T1,
     "unverified_two_tuple or poison_repair_poison_in_one_block or unreadable_transaction or unverified_re_poison_page"
     " or unverified_page_stays"),
    ("R6-M1-unverified-as-poison", M1, "            if maybe_by_unverified or (cls", "            if (cls", T1,
     "poison_repair_poison_in_one_block"),
    ("R6-M1-sticky", M1, "unverified = not repaired_before and self._episode_unverified(db, poison)",
     "unverified = not repaired_before and bool(detail.get(\"unverified_re_poison\"))", T1, "unverified_page_stays"),
    ("R6-M1-clean-repair", M1, "            if cls == \"clean\" and active is not None:", "            if False:", T1,
     "poison_repair_poison_in_one_block or same_block_repair_then_re_poison or same_block_bulk_repair"),
    ("R7-M1-transient", M1, "transient = self._transient(db, key, block) if latest is not None and not stale else None",
     "transient = None", T1, "repaired_before_the_run_still_alerts"),
    ("R7-M1-transient-dedup", M1, "if not self._reported(db, key, r[\"tx_hash\"])]", "if True]", T1,
     "not_re_alerted_after_a_reorg"),
    ("R7-M1-horizon", M1, "        horizon = first - self.config", "        horizon = block - self.config", T1,
     "downtime_backlog"),
    ("R8-M1-empty", M1, "                degraded = self.use_api and api_n == 0", "                degraded = False", T1,
     "initial_empty_inventory"),
    # the sweep must catch the same regressions without the targeted tests
    ("S-M1-resolved", M1, "            keys |= self._resolved_since(db, ctx.reorg_ancestor)", "            keys |= set()",
     TS, "m1_adversarial"),
    ("S-M1-unverified", M1, "            if maybe_by_unverified or (cls == \"unverified\" and maybe_poisoned):",
     "            if False:", TS, "m1_adversarial"),
    ("S-M1-transient", M1,
     "transient = self._transient(db, key, block) if latest is not None and not stale else None",
     "transient = None", TS, "m1_adversarial"),
    ("S-M1-empty", M1, "                degraded = self.use_api and api_n == 0", "                degraded = False", TS,
     "m1_adversarial"),
    ("S-M2-history", M2, "cluster_id = ? AND block <= ?)\", (cid, cid, block - self.depth))",
     "cluster_id = ? AND block <= ?)\", (cid, cid, block))", TS, "m2_adversarial"),
    ("S-M2-unknown-health", M2, "        if unknown_hard:  #", "        if False:  #", TS, "m2_adversarial"),
    # repair 1 (re-targeted: R1-M2-persisted to the tuple history, R2 to the per-transaction walk)
    ("R1-M1-absence", M1, "        elif not self._in_storage(ctx, key, block):",
     "        elif True:", T1, "empty_or_failed_inventory or outside_a_reorg"),
    ("R1-M2-persisted", M2, "        if history:\n            cids = sorted(history)",
     "        if False:\n            cids = sorted(history)", T2,
     "empty_api_is_not_recovery or empty_api_during or restart_with_an_empty_api"),
    ("R1-M2-bound", M2, "                if missing > 0 and e[\"validator_count\"] + missing > limit:",
     "                if False:", T2, "partial_api"),
    ("R1-M2-degraded", M2, "            elif degraded:\n", "            elif False:\n", T2, "revert_does_not_settle"),
    ("R1-M2-calldata", M2, "            if dec and cl:  #", "            if False:  #", T2, "own_calldata_tuple"),
    ("R1-M2-reorg-raise", M2, "        if stuck:  #", "        if False:  #", T2, "reorg_run_raises"),
    ("R2-M1-intra-block", M1, "        for group in ordered[:-1]:\n            last = group[-1]",
     "        for group in []:\n            last = group[-1]", T1, "same_block"),
    ("R3-M2-unsettle", M2, "            db.conn.execute(\"UPDATE m2_reverts SET cleared_block = NULL",
     "            0 and db.conn.execute(\"UPDATE m2_reverts SET cleared_block = NULL", T2, "orphaning_the_settlement"),
    ("R3-M1-unclear", M1, '        db.conn.execute("UPDATE m1_detections SET cleared_block = NULL WHERE cleared_block > ?", '
                          '(ancestor,))', "        pass", T1, "orphaning_the_clearing_run"),
    ("R4-M1-earliest", M1, "        match = next((e for e in cands if", "        match = next((e for e in reversed(cands) if",
     T1, "earliest_repair_tuple or repair_tuple_is_the_earliest or tie_is_the_earliest"),
    ("S-M1-absence", M1, "        elif not self._in_storage(ctx, key, block):", "        elif True:", TS, "m1_adversarial"),
    ("S-M1-intra-block", M1, "        for group in ordered[:-1]:\n            last = group[-1]",
     "        for group in []:\n            last = group[-1]", TS, "m1_adversarial"),
    ("S-M2-persisted", M2, "        if history:\n            cids = sorted(history)",
     "        if False:\n            cids = sorted(history)", TS, "m2_adversarial"),
    ("S-M2-unsettle", M2, "            db.conn.execute(\"UPDATE m2_reverts SET cleared_block = NULL",
     "            0 and db.conn.execute(\"UPDATE m2_reverts SET cleared_block = NULL", TS, "m2_adversarial"),
    ("S-M2-degraded", M2, "            elif degraded:\n", "            elif False:\n", TS, "m2_adversarial"),
    # attempt-1 falsifiers (F-M1a re-targeted to the severity line with the unverified term, F-M1c to the final group)
    ("F-M1a", M1, "if repaired_before or unverified or liquidatable:", "if repaired_before and liquidatable:", T1,
     "re_poison_after_repair_pages or poisoned_and_liquidatable_pages or thresholds_from_chain"),
    ("F-M1b", M1, "                return False\n            raise", "                return True\n            raise",
     f"{T1} test_m1_receipts.py", "poisoned_receipt_warns or flags_recorded"),
    ("F-M1c", M1, "        group = ordered[-1]\n", "        group = ordered[0]\n", T1, "same_block"),
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
