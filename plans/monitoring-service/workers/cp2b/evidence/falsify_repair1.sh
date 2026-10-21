#!/bin/bash
# Repair-1 falsifiers: each mutation must turn its named test(s) red, and the restored file must pass with a clean
# tree. Every pytest process gets its own fresh bytecode cache (a same-size mutation must not reuse stale .pyc).
#   bash plans/monitoring-service/workers/cp2b/evidence/falsify_repair1.sh
set -u
cd "$(git rev-parse --show-toplevel)"
PY=reconcile/.venv/bin/python
C=reconcile/checks
pt() { PYTHONPYCACHEPREFIX=$(mktemp -d) $PY -m pytest -q -p no:cacheprovider "$@" 2>&1 | tail -1; return ${PIPESTATUS[0]}; }
one() {  # id file old new test-args...
  local id=$1 f=$2 old=$3 new=$4; shift 4
  $PY - "$f" "$old" "$new" <<'PY' || { echo "[$id] anchor error"; return; }
import sys
f, old, new = sys.argv[1:]
s = open(f).read()
assert s.count(old) == 1, f"mutation anchor found {s.count(old)} times"
open(f, "w").write(s.replace(old, new))
PY
  echo "[$id] mutated $f: $(git diff --stat | tail -1)"
  pt "$@"; echo "[$id] mutated exit: $?"
  git checkout -- "$f"
  pt "$@"; echo "[$id] restored exit: $?"
  echo "[$id] git status --porcelain: '$(git status --porcelain)'"
}
echo "head $(git rev-parse HEAD) at $(date -u +%Y-%m-%dT%H:%MZ)"
T=reconcile/tests
one F1-retry $C/m11_updater_liveness.py 'if saved and not saved["result"]["unknown"]:' 'if saved:' \
  $T/test_m11.py::test_qa1_f1_partial_proof_answer_is_unknown_and_retried
one F1-proof $C/m11_updater_liveness.py 'proven = newest is not None and all(' 'proven = newest is not None or all(' \
  $T/test_m11.py::test_qa1_f1_partial_proof_answer_in_a_reorg_run_keeps_the_reported_mismatch
one F2-order $C/m11_updater_liveness.py 'before = [ev for ev in self.rotations if (ev.block, ev.tx_index) < pos]' \
  'before = [ev for ev in self.rotations if ev.block < pos[0]]' \
  $T/test_m11.py::test_qa1_f2_rotation_then_update_in_the_same_block_uses_the_new_owner
one F3-split $C/m9_oracle_quorum.py 'active = st.reference is None and sup is None' \
  'active = st.settled and st.reference is None and sup is None' \
  $T/test_m9.py::test_qa1_f3_two_roots_in_one_block_warn_before_the_timeout
one F3-sameslot $C/m9_oracle_quorum.py 'if roots and roots == {st.reference}:' 'if roots and st.reference in roots:' \
  $T/test_m9.py::test_qa1_f3_two_roots_from_one_slot_after_a_midround_owner_change
one F4-events $C/m10_oracle_rotation.py 'if not others or new == zero:' 'if True:' \
  $T/test_m10.py::test_qa1_f4_transient_duplicate_before_the_next_sweep_pages
one F5-before $C/m11_updater_liveness.py 'return ev.block < row["block"] or (' 'return True or (' \
  $T/test_m11.py::test_qa1_f5_a_later_other_sender_update_cannot_explain_an_earlier_revert
one F6-age $C/m11_updater_liveness.py 'if prev is not None and status(prev, m["cluster"]) == "mismatch"]' 'if True]' \
  $T/test_m11.py::test_qa1_f6_latest_round_mismatch_must_age_one_round
one F7-consecutive $C/m9_oracle_quorum.py 'eps[slot]["run"] = 0  # no vote: not a dissenting round' 'pass' \
  $T/test_m9.py::test_qa1_f7_streak_requires_consecutive_rounds
one F8-clock $C/m9_oracle_common.py 'return max(float(self(self.ctx.block)), self.ctx.clock() - lag)' \
  'return float(self(self.ctx.block))' \
  $T/test_m9.py::test_qa1_f8_30_minute_timeout_fires_without_a_new_block \
  $T/test_m11.py::test_qa1_f8_60_minute_window_closes_without_a_new_block
one F8-slots $C/m9_oracle_quorum.py 'deadline = first_at + self.config["round_timeout_s"]' \
  'deadline = first_at + 150 * (self.times(self.block) - first_at) / max(self.block - rnd.first_vote.block, 1)' \
  $T/test_m9.py::test_failed_round_deadline_is_chain_time_not_a_block_count
one S-superseded $C/m9_oracle_quorum.py 'in_time = close is not None and self.times(close.block) <= deadline' \
  'in_time = rnd.commit is not None and self.times(rnd.commit.block) <= deadline' \
  $T/test_m9.py::test_a_round_superseded_within_its_30_minutes_is_not_failed_and_one_superseded_later_is
one SWEEP-m9 $C/m9_oracle_quorum.py 'eps[slot]["run"] = 0  # no vote: not a dissenting round' 'pass' \
  $T/test_m9_m10_m11_sweep.py -k sweep_m9_m10
one SWEEP-m11 $C/m11_updater_liveness.py 'if saved and not saved["result"]["unknown"]:' 'if saved:' \
  $T/test_m9_m10_m11_sweep.py -k sweep_m11
one SWEEP-m10 $C/m10_oracle_rotation.py 'if not others or new == zero:' 'if True:' \
  $T/test_m9_m10_m11_sweep.py -k sweep_m9_m10
