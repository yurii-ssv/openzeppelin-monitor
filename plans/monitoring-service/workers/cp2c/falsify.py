import subprocess, sys
ROOT = sys.argv[1]
MUT = [
 ("F12a", "reconcile/checks/m12_wallet_burn.py", "runway < w.runway_page_days", "runway <= w.runway_page_days",
  "reconcile/tests/test_m12.py::test_m12_oracle_runway_exactly_7_days_warns_not_pages"),
 ("F12b", "reconcile/checks/m12_wallet_burn.py", "        if missing or extra:\n", "        if False:\n",
  "reconcile/tests/test_m12.py::test_m12_indexer_gap_is_unknown_not_clean"),
 ("F12c", "reconcile/checks/m12_wallet_burn.py", "avg_spend = Fraction(fees_w * 86_400, span_w)",
  "avg_spend = Fraction(fees_w * 86_400, block - lo_w)",
  "reconcile/tests/test_m12.py::test_m12_runway_trailing_spend_drives_when_the_base_fee_fell"),
 ("F12d", "reconcile/checks/m12_wallet_burn.py", "runway = min((r for r", "runway = max((r for r",
  "reconcile/tests/test_m12.py::test_m12_runway_from_trailing_spend_and_from_current_base_fee"),
 ("F8", "reconcile/checks/m8_supply_floor.py", 'str(seed * self.config["floor_multiplier"])', "str(seed)",
  "reconcile/tests/test_m8.py::test_m8_supply_below_twice_the_seed_warns"),
 ("F13a", "reconcile/checks/m13_retired_keys.py", "if bal > prev:", "if bal >= prev:",
  "reconcile/tests/test_m13.py::test_m13_zero_delta_blocks_stay_silent"),
 ("F13b", "reconcile/checks/m13_retired_keys.py",
  'plans[a] = sample(last, block, self.config["every_n_blocks"], self.config["max_blocks_per_run"])',
  "plans[a] = ([block], False)",
  "reconcile/tests/test_m13.py::test_m13_multi_block_net_zero_between_checkpoints_is_caught"),
 ("F13c", "reconcile/checks/m13_retired_keys.py", 'conn.execute("DELETE FROM m13_topup WHERE block > ?", (ancestor,))',
  "pass",
  "reconcile/tests/test_m13.py::test_m13_reorg_depth_1_removing_the_top_up_resolves_and_never_re_pages"),
 # repair 1 (QA attempt 1 F1 and the generalised audit)
 ("F12e", "reconcile/checks/m12_wallet_burn.py", "                if ctx.reorg_ancestor is not None:  # README §6.2 item 4",
  "                if {f'{w.address}:runway', f'{w.address}:daily-spend'} & ctx.reorg_keys:  # README §6.2 item 4",
  "reconcile/tests/test_m12.py::test_m12_qa1_f1_reorg_unknown_with_an_older_open_runway_keeps_the_marker"),
 ("F12e-sweep", "reconcile/checks/m12_wallet_burn.py", "                if ctx.reorg_ancestor is not None:  # README §6.2 item 4",
  "                if {f'{w.address}:runway', f'{w.address}:daily-spend'} & ctx.reorg_keys:  # README §6.2 item 4",
  "reconcile/tests/test_m12.py::test_m12_adversarial_reorg_sweep"),
 ("F12f", "reconcile/checks/m12_wallet_burn.py", "if not isinstance(res, list) or len(res) != len(params):", "if False:",
  "reconcile/tests/test_m13.py::test_m13_short_batch_response_raises_never_a_partial_read"),
 ("F12g-sweep", "reconcile/checks/m12_wallet_burn.py", '        conn.execute("DELETE FROM m12_tx WHERE block > ?", (ancestor,))\n',
  "        pass\n", "reconcile/tests/test_m12.py::test_m12_adversarial_reorg_sweep"),
 ("F8b-sweep", "reconcile/checks/m8_supply_floor.py",
  ' or (ctx.reorg_ancestor is not None and st["block"] > ctx.reorg_ancestor))', ")",
  "reconcile/tests/test_m8.py::test_m8_adversarial_reorg_sweep"),
 ("F13d-sweep", "reconcile/checks/m13_retired_keys.py",
  'conn.execute("UPDATE m13_topup SET resolved_block=NULL, resolved_balance=NULL WHERE resolved_block > ?",',
  'conn.execute("SELECT ?",', "reconcile/tests/test_m13.py::test_m13_adversarial_reorg_sweep"),
 # repair 2 (QA attempt 2 F1-F5)
 ("F13e", "reconcile/checks/m13_retired_keys.py", "            if block >= closed:\n", "            if True:\n",
  "reconcile/tests/test_m13.py::test_m13_shorter_chain_orphaning_only_the_drain_waits_for_the_drain_block"),
 ("F13f", "reconcile/checks/m13_retired_keys.py", "            if block >= b:\n                return True, [self.left_chain",
  "            if True:\n                return True, [self.left_chain",
  "reconcile/tests/test_m13.py::test_m13_qa2_f1b_reorg_run_below_the_top_up_block_keeps_the_key_open_and_pages_once"),
 ("F13g-sweep", "reconcile/checks/m13_retired_keys.py", '        conn.execute("INSERT OR IGNORE INTO m13_orphan',
  '        conn.execute("SELECT ? OR ?" or "INSERT OR IGNORE INTO m13_orphan',
  "reconcile/tests/test_m13.py::test_m13_adversarial_reorg_sweep"),
 ("F13h", "reconcile/checks/m13_retired_keys.py",
  '            if self.read_failures < self.config["state_fallback_after"]:\n                raise\n            window',
  '            if True:\n                raise\n            window',
  "reconcile/tests/test_m13.py::test_m13_qa2_f2_pruned_rpc_restart_gap_recovers_after_bounded_failures_and_says_so"),
 ("F13i", "reconcile/checks/m13_retired_keys.py", "        except Exception as exc:  # noqa: BLE001",
  "        except (RpcError, KeyError, TypeError, ValueError) as exc:  # noqa: BLE001",
  "reconcile/tests/test_m13.py::test_qa2_m13_malformed_receipt_entry_must_not_block_the_page"),
 ("F8c", "reconcile/checks/m8_supply_floor.py", 'st.get("process") != self.process', "False",
  "reconcile/tests/test_m8.py::test_qa2_m8_restart_whose_first_run_fails_after_the_seed_read_still_measures_at_start"),
 ("F12h", "reconcile/checks/m12_wallet_burn.py", 'if hdr["hash"].lower() == t["block_hash"]:', "if True:",
  "reconcile/tests/test_m12.py::test_qa2_m12_transaction_from_a_non_canonical_block_is_not_counted"),
 # repair 3 (QA attempt 2 (Codex) F1/F2 and the generalised rows)
 ("F12i", "reconcile/checks/m12_wallet_burn.py", "            lo = self._boundary(ctx, ts, block, t - seconds)\n",
  "            lo = max(block - seconds // SLOT_S, 0)\n            self._ts(ctx, ts, lo)\n",
  "reconcile/tests/test_m12.py::test_m12_r3_daily_spend_excludes_a_transaction_older_than_24h_inside_7200_blocks"),
 ("F12i-sweep", "reconcile/checks/m12_wallet_burn.py", "            lo = self._boundary(ctx, ts, block, t - seconds)\n",
  "            lo = max(block - seconds // SLOT_S, 0)\n            self._ts(ctx, ts, lo)\n",
  "reconcile/tests/test_m12.py::test_m12_adversarial_reorg_sweep"),
 ("F12j", "reconcile/checks/m12_wallet_burn.py", "    if (got != oldest or not isinstance(fees, list)",
  "    if False and (got != oldest or not isinstance(fees, list)",
  "reconcile/tests/test_m12.py::test_qa2c_m12_partial_history_clears_real_page"),
 ("F12j-sweep", "reconcile/checks/m12_wallet_burn.py", "    if (got != oldest or not isinstance(fees, list)",
  "    if False and (got != oldest or not isinstance(fees, list)",
  "reconcile/tests/test_m12.py::test_m12_adversarial_reorg_sweep"),
 ("F12k", "reconcile/checks/m12_wallet_burn.py", 'keep = max(lo_window - self.config["prune_margin_blocks"], 0)',
  "keep = lo_window",
  "reconcile/tests/test_m12.py::test_m12_r3_shorter_chain_moving_the_window_back_still_counts_the_edge_transaction"),
 ("F12l", "reconcile/checks/m12_wallet_burn.py", " or n_now < row[1] or lo_window < row[2])", " or n_now < row[1])",
  "reconcile/tests/test_m12.py::test_m12_r3_window_below_the_stored_set_is_backfilled_never_undercounted"),
]
py = f"{ROOT}/reconcile/.venv/bin/python"
for name, path, old, new, test in MUT:
    full = f"{ROOT}/{path}"
    src = open(full).read()
    assert src.count(old) == 1, (name, old)
    green = subprocess.run([py, "-m", "pytest", "-q", test], cwd=ROOT, capture_output=True, text=True)
    open(full, "w").write(src.replace(old, new))
    red = subprocess.run([py, "-m", "pytest", "-q", test], cwd=ROOT, capture_output=True, text=True)
    subprocess.run(["git", "checkout", "--", path], cwd=ROOT, check=True)
    after = subprocess.run([py, "-m", "pytest", "-q", test], cwd=ROOT, capture_output=True, text=True)
    st = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout
    tail = lambda r: r.stdout.strip().splitlines()[-1]
    print(f"{name}: before exit {green.returncode} ({tail(green)}) | mutated exit {red.returncode} ({tail(red)}) | "
          f"restored exit {after.returncode} ({tail(after)}) | porcelain={st!r}")
