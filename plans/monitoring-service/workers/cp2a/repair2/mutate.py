"""CP2a repair 2 falsifiers (QA cp2a-2; repair 1's rows, re-targeted where the code moved, plus R2-*): each row mutates one exact, unique string in place, runs the named tests with a fresh
bytecode cache, restores the original bytes, and records the pytest exit code. Exit 0 only if every mutant is red
(pytest exit 1) and every file is restored byte for byte. Run from the worktree root:
    reconcile/.venv/bin/python plans/monitoring-service/workers/cp2a/repair2/mutate.py <qa_independent_test_file>
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile

QA = sys.argv[1] if len(sys.argv) > 1 else None
M3, M4, M14 = ("reconcile/checks/m3_liquidation_zone.py", "reconcile/checks/m4_conservation_deficit.py",
               "reconcile/checks/m14_deposit_frontrun.py")
V = "reconcile/checks/m3_views.py"
T3, T4, T14, SW = ("reconcile/tests/test_m3.py::", "reconcile/tests/test_m4.py::", "reconcile/tests/test_m14.py::",
                   "reconcile/tests/test_liquidation_sweep.py")
ROWS = [
    # attempt-1 falsifiers (F1-F9 unchanged code; F10-F15 re-targeted at the repaired M14)
    ("F1", M3, "elif age >= n:", "elif age > n:", [T3 + "test_still_liquidatable_after_n_blocks_warns_exactly_at_n"]),
    ("F2", M3, "elif age >= n2:", "elif age > n2:", [T3 + "test_still_liquidatable_after_n2_blocks_pages"]),
    ("F3", M3, "if view.liquidatable or view.balance == 0:", "if view.liquidatable:",
     [T3 + "test_computed_zero_pages_even_when_the_view_says_not_liquidatable"]),
    ("F4", M3, '        ctx.db.conn.execute(f"DELETE FROM m3_zone WHERE onset_block > ? AND {held}", (ancestor, block))',
     "        pass",
     [T3 + "test_reorg_onset_above_ancestor_still_liquidatable_is_reported_again"]),
    ("F5", M3, '            unknown_rows.append(("inventory", "unknown: " + gap))', "            pass",
     [T3 + "test_unknown_incomplete_inventory_is_health_on_every_full_sweep"]),
    ("F6", M4, "        if deficit > 0:", "        if deficit >= 0:",
     [T4 + "test_deficit_benign_exact_cover_and_surplus_stay_silent"]),
    ("F7", M4, "    liabilities = clusters + operators + network",
     "    liabilities = clusters + operators + network + (pool or 0)",
     [T4 + "test_staking_pool_is_not_added_as_a_separate_liability"]),
    ("F8", M4, '        if not s["complete"]:', "        if False:", ["reconcile/tests/test_m4.py"]),
    ("F9", M4, '            ctx.db.conn.execute("DELETE FROM m4_scans WHERE block > ?", (ctx.reorg_ancestor,))',
     "            pass", [T4 + "test_reorg_removing_the_deficit_resolves_and_never_repages"]),
    ("F10", M14, 'dust = [ev for ev in deposits if int(ev.extra.get("value", 0)) < burn]',
     'dust = [ev for ev in deposits if int(ev.extra.get("value", 0)) <= burn]',
     [T14 + "test_v2a7_benign_deposit_of_at_least_one_block_of_burn"]),
    ("F11", M14, "(ev.block == B - 1 or ev in before)", "(ev.block >= B - 2 or ev in before)",
     [T14 + "test_v2a7_benign_deposit_two_blocks_earlier"]),
    ("F12", M14, "before = [ev for ev in in_block if ev.tx_index < pos]", "before = list(in_block)",
     [T14 + "test_v2a7_benign_deposit_after_the_failed_liquidation_in_the_same_block"]),
    ("F13", M14, "        elif len(times) >= repeat:", "        elif len(hits) >= repeat:",
     [T14 + "test_three_hits_benign_spread_beyond_the_window_stay_info"]),
    ("F14", M14, '            ctx.db.conn.execute("DELETE FROM m14_hits WHERE block > ?", (A,))', "            pass",
     [T14 + "test_reorg_removing_the_front_run_resolves_and_never_repages"]),
    ("F15", M14, "        if not liq:\n            return None", "        if False:\n            return None",
     [T14 + "test_v2a7_benign_cluster_not_liquidatable"]),
    # QA attempt-1's own falsifiers (re-targeted where the code moved)
    ("QF14", M14, "        elif len(times) >= repeat:", "        elif len(times) > repeat:",
     [T14 + "test_three_hits_within_the_window_warn"]),
    # repair 1: one per finding and per sweep finding
    ("R1-H1", M3, "            ident = self.identity(ctx, cid, tracked)", "            ident = None",
     [T3 + "test_qa1_h1_empty_api_does_not_resolve_a_live_computed_zero_page"]),
    ("R1b-H1", M3, 'status[cid] = "unknown: not listed by the inventory, and no persisted identity to read it by"',
     'status[cid] = "is not in storage (absent)"',
     [T3 + "test_qa1_h1_reorg_key_listed_nowhere_without_identity_raises"]),
    ("R2-H2", M14, "if A is not None and any(u is not None and u.cluster is None for u in unknown):", "if False:",
     [T14 + "test_qa1_h2_reorg_missing_replacement_transaction_does_not_clean_resolve"]),
    ("R3-H2", M14, 'maybe = [p for p in pending if p["cluster_id"] in (None, cid)]', "maybe = []",
     [T14 + "test_qa1_h2_pending_entry_keeps_an_open_incident_past_the_window"]),
    ("R4-H3", V, 'if version == "ETH" else \\', 'if True else \\',
     [T14 + "test_qa1_h3_legacy_ssv_cluster_liquidate_ssv_front_run_is_info"]),
    ("R5-M1", M14, 'recent = [h for h in hits if now - h["block_time"] < window]',
     'recent = [h for h in hits if block - h["block"] < 7_200]',
     [T14 + "test_qa1_m1_three_hits_over_26_hours_of_chain_time_do_not_warn"]),
    ("R6-M2", M14, 'recent = [h for h in hits if now - h["block_time"] < window]',
     'recent = [h for h in hits if hits[-1]["block_time"] - h["block_time"] < window]',
     [T14 + "test_qa1_m2_old_hits_expire_at_the_current_checkpoint"]),
    ("R7-M3", M14, "        if after:  # the end-of-B state", "        if False:  # the end-of-B state",
     [T14 + "test_qa1_m3_later_same_block_top_up_does_not_hide_the_attack"]),
    ("R8-sweep12", M3, "        if reorg:\n            recheck |=", "        if False:\n            recheck |=",
     [T3 + "test_sweep_m3_resolution_on_a_deleted_block_is_found_again_without_the_api"]),
    ("R9-sweep13", M3, "[(\"persisted\", t) for t in persisted]", "[(\"persisted\", t) for t in persisted[:1]]",
     [T3 + "test_sweep_m3_reorg_removing_the_latest_change_uses_an_earlier_validated_tuple"]),
    ("R10-sweep3", M14, 'if first is not None and first.kind == "ClusterDeposited"',
     'if False and first.kind == "ClusterDeposited"',
     [T14 + "test_sweep_m14_cluster_without_earlier_logs_later_top_up_uses_the_deposit_tuple_minus_value"]),
    ("R12-sweep21", M14, "            return None  # no deposit below one block of burn: not this attack, whatever the "
     "cluster's state", "            pass",
     [T14 + "test_sweep_m14_no_deposit_below_one_block_of_burn_is_decided_before_the_position_state"]),
    ("R11-silent", M14, 'revision=last["deposit_tx"] if sev == "info" and new_time else None',
     'revision=last["deposit_tx"] if sev == "info" else None',
     [T14 + "test_qa1_m2_old_hits_expire_at_the_current_checkpoint"]),
    # DEFECT-CLASSES.md gaps closed in repair 1
    ("C5", M3, 'if ev.kind in ("RootCommitted", "OperatorFeeExecuted"):', 'if ev.kind == "RootCommitted":',
     [T3 + "test_class5_fee_increase_onset_is_seen_at_its_block_even_if_healed_before_the_next_sweep"]),
    ("C11-m3", M3, "        if not (1 <= c[\"n_blocks\"] <= c[\"n2_blocks\"]", "        if False and not (1 <= c[\"n_blocks\"] <= c[\"n2_blocks\"]",
     [T3 + "test_class11_invalid_configuration_is_a_startup_error"]),
    ("C11-m4", M4, '        if not config["interval_s"] > 0:', "        if False:",
     [T4 + "test_class11_invalid_configuration_is_a_startup_error"]),
    ("C11-m14", M14, '        if not (config["window_s"] > 0 and', '        if False and not (config["window_s"] > 0 and',
     [T14 + "test_class11_invalid_configuration_is_a_startup_error"]),
    # the adversarial sweep alone catches each finding class
    ("S1-H1", M3, "            ident = self.identity(ctx, cid, tracked)", "            ident = None",
     [SW + "::test_m3_sweep"]),
    ("S2-M2", M14, 'recent = [h for h in hits if now - h["block_time"] < window]',
     'recent = [h for h in hits if hits[-1]["block_time"] - h["block_time"] < window]', [SW + "::test_m14_sweep"]),
    ("S3-M1", M14, 'recent = [h for h in hits if now - h["block_time"] < window]',
     'recent = [h for h in hits if block - h["block"] < 7_200]', [SW + "::test_m14_sweep"]),
    ("S4-M3", M14, "        if after:  # the end-of-B state", "        if False:  # the end-of-B state",
     [SW + "::test_m14_sweep"]),
    ("S5-H3", V, 'if version == "ETH" else \\', 'if True else \\', [SW + "::test_m14_sweep"]),
    ("S6-H2", M14, 'maybe = [p for p in pending if p["cluster_id"] in (None, cid)]', "maybe = []",
     [SW + "::test_m14_sweep"]),
]
H1 = T14 + "test_qa2_h1_shorter_chain_keeps_the_key_and_re_derives_the_surviving_hit"
ROWS += [
    # repair 2: QA cp2a-2 H1 (entries above a lowered checkpoint), M1 ruling (a time = one deposit), the H1 shape
    ("R2-H1a", M14, "                self.await_confirmation(ctx, r)", "                pass", [H1]),
    ("R2-H1b", M14, "            elif r[\"canonical\"] is None:  # its block is not confirmed (yet): keep waiting\n"
     "                continue\n", "", [T14 + "test_qa2_h1_lagging_node_below_the_deposit_keeps_the_hit_until_its_logs_are_rescanned"]),
    ("R2-H1c", M14, "        self.near_set = frozenset(p[\"inbox_id\"] for p in pending if p[\"block_time\"] is None) | held",
     "        self.near_set = frozenset()", [H1]),
    ("R2-M1a", M14, "        elif len(times) >= repeat:", "        elif len(recent) >= repeat:",
     [T14 + "test_qa2_m1_one_deposit_reverting_three_liquidations_is_one_time"]),
    ("R2-M1b", M14, 'revision=last["deposit_tx"] if sev == "info" and new_time else None',
     'revision=last["tx_hash"] if sev == "info" and last["tx_hash"] != (row["last_hit"] if row else None) else None',
     [T14 + "test_qa2_m1_one_deposit_blocking_its_block_and_the_next_is_one_time_and_not_re_sent"]),
    ("R2-M3a", M3, "SET hold_block = MAX(COALESCE(hold_block, 0), last_block) ", "SET hold_block = hold_block ",
     [T3 + "test_qa2_class2_lowered_checkpoint_keeps_the_page_and_never_re_escalates"]),
    ("R2-M3c", M3, "        if not self.held(row, block):\n            return self.resolved",
     "        if True:\n            return self.resolved",
     [T3 + "test_qa2_class2_lowered_checkpoint_keeps_the_page_and_never_re_escalates"]),
    ("R2-M4a", M4, "                    hold = max(top, hold or 0)", "                    pass",
     [T4 + "test_qa2_class2_lagging_node_below_the_deficit_keeps_the_page_and_never_re_pages"]),
    ("R2-M4b", M4, "        self.near_set = frozenset({KEY}) if hold is not None else frozenset()",
     "        self.near_set = frozenset()",
     [T4 + "test_qa2_class2_shorter_chain_without_the_trigger_resolves_once_past_the_old_scan"]),
    ("R2-M14", M14, "SET hold_block = MAX(COALESCE(hold_block, 0), block) WHERE", "SET hold_block = hold_block WHERE",
     [T14 + "test_qa2_class2_lowered_checkpoint_keeps_a_zero_page_and_never_re_escalates"]),
    ("R2-M14c", M14, '        if (maybe or hold is not None) and row and row["last_hit"] and row["last_hit"] not in '
     '{h["tx_hash"] for h in hits}:', "        if False:", [H1]),
    ("R2-M14d", M14, "        if hold is not None and prev:\n            sev = prev", "        if False:\n            sev = prev",
     [T14 + "test_qa2_class4_lowered_checkpoint_does_not_escalate_on_an_earlier_block"]),
    ("R2-M3d", M3, '        if hold is not None and sev != row["severity"]:', "        if False:",
     [T3 + "test_qa2_class2_lowered_checkpoint_keeps_the_page_and_never_re_escalates"]),
    # the adversarial sweep alone catches each repair-2 class
    ("S7-H1", M14, "                self.await_confirmation(ctx, r)", "                pass", [SW + "::test_m14_sweep"]),
    ("S8-M1", M14, "        elif len(times) >= repeat:", "        elif len(recent) >= repeat:", [SW + "::test_m14_sweep"]),
    ("S9-M3hold", M3, "SET hold_block = MAX(COALESCE(hold_block, 0), last_block) ", "SET hold_block = hold_block ",
     [SW + "::test_m3_sweep"]),
    ("S11-freeze", M14, "        if hold is not None and prev:\n            sev = prev", "        if False:\n            sev = prev",
     [SW + "::test_m14_sweep"]),
    ("S10-M14hold", M14, "SET hold_block = MAX(COALESCE(hold_block, 0), block) WHERE", "SET hold_block = hold_block WHERE",
     [SW + "::test_m14_sweep"]),
]
if QA:
    ROWS += [
        ("QF3", M3, 'or now - self.last_full >= self.config["full_sweep_interval_s"])',
         'or now - self.last_full > self.config["full_sweep_interval_s"])',
         [QA + "::test_m3_exact_600_second_sweep_detects_unannounced_burn_change"]),
        ("QF4", M4, "    network = values[n + last_op] or 0", "    network = 0", [QA + "::test_m4_exact_wei[-1]"]),
    ]
out = []
for name, path, old, new, tests in ROWS:
    p = pathlib.Path(path)
    orig = p.read_bytes()
    src = orig.decode()
    assert src.count(old) == 1, (name, src.count(old))
    try:
        p.write_text(src.replace(old, new))
        env = {**os.environ, "PYTHONPYCACHEPREFIX": tempfile.mkdtemp(prefix=f"pyc-{name}-")}
        proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    finally:
        p.write_bytes(orig)
    row = {"id": name, "file": path, "exit": proc.returncode, "restored": p.read_bytes() == orig,
           "tail": proc.stdout.strip().splitlines()[-1]}
    out.append(row)
    print(json.dumps(row), flush=True)
porcelain = subprocess.run(["git", "status", "--porcelain", "--", "reconcile/checks"], stdout=subprocess.PIPE,
                           text=True).stdout
print("porcelain reconcile/checks:", repr(porcelain))
sys.exit(0 if all(r["exit"] == 1 and r["restored"] for r in out) and not porcelain else 1)
