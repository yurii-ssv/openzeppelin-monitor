import subprocess, sys
ROOT = sys.argv[1]
MUT = [
 ("F12a", "reconcile/checks/m12_wallet_burn.py", "runway < w.runway_page_days", "runway <= w.runway_page_days",
  "reconcile/tests/test_m12.py::test_m12_oracle_runway_exactly_7_days_warns_not_pages"),
 ("F12b", "reconcile/checks/m12_wallet_burn.py", "        if missing or extra:\n", "        if False:\n",
  "reconcile/tests/test_m12.py::test_m12_indexer_gap_is_unknown_not_clean"),
 ("F12c", "reconcile/checks/m12_wallet_burn.py", "avg_spend = Fraction(fees_w * 86_400, span_w)",
  "avg_spend = Fraction(fees_w * 86_400, c['window_blocks'])",
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
