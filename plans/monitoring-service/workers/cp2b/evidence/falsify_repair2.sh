#!/bin/bash
# Repair-2 falsifiers (QA attempt 2 findings and the repair-2 sweep finding): each mutation must turn its named test(s) red, and the restored file must pass with a clean
# tree. Every pytest process gets its own fresh bytecode cache (a same-size mutation must not reuse stale .pyc).
#   bash plans/monitoring-service/workers/cp2b/evidence/falsify_repair2.sh
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
one R2-F1-literal $C/m9_oracle_quorum.py 'in_time = rnd.commit is not None and self.times(rnd.commit.block) <= deadline' \
  'in_time = close is not None and self.times(close.block) <= deadline' \
  $T/test_m9.py::test_qa2_f1_lone_vote_for_an_earlier_blocknum_that_never_commits_pages \
  $T/test_m9.py::test_a_round_superseded_within_its_30_minutes_still_fails_at_its_deadline
one R2-F1-start $C/m9_oracle_quorum.py 'episodes = (self.failed_rounds(every)' 'episodes = (self.failed_rounds(states)' \
  $T/test_m9.py::test_qa2_f1_lone_vote_for_an_earlier_blocknum_that_never_commits_pages
one R2-F1-own $C/m9_oracle_quorum.py 'key = ("early", st.ref) if st.early else (' 'key = (' \
  $T/test_m9.py::test_a_round_superseded_within_its_30_minutes_still_fails_at_its_deadline
one R2-F1-evidence $C/m9_oracle_quorum.py 'evidence_block=(self.block_after(last.deadline, close.block, self.block) if last.early' \
  'evidence_block=(close.block if last.early' \
  $T/test_m9.py::test_qa2_f1_superseded_round_whose_deadline_passes_after_the_first_sweep_is_reported
one R2-F2-abort $C/m11_updater_liveness.py 'if self.api_failures >= self.config["proof_api_abort_after"]:' \
  'if self.api_failures >= self.config["proof_api_abort_after"] and not self.answers:' \
  $T/test_m11.py::test_qa2_f2_proof_api_dying_mid_comparison_is_bounded
one R2-F2-budget $C/m11_updater_liveness.py 'if self.api_stop is None and self.api_spent >= ' 'if False and self.api_spent >= ' \
  $T/test_m11.py::test_qa2_f2_slow_api_is_bounded_by_the_run_budget
one R2-F3-definite $C/m11_updater_liveness.py 'if not aged and (not reported or not proven):' 'if not aged and not reported:' \
  $T/test_m11.py::test_qa2_f3_reorg_removing_the_round_that_aged_a_mismatch
one R2-F3-latest $C/m11_updater_liveness.py 'definite = newest is not None and latest is not None and newest["ref"] == latest.ref_block and all(' \
  'definite = newest is not None and all(' \
  $T/test_m11.py::test_qa2_f3_reorg_keeping_the_aging_round_with_the_api_down_keeps_the_mismatch
one R2-F4-mark $C/m11_updater_liveness.py 'if len(closed) >= self.config["history_rounds"] and (mark is None or c.block <= mark):' \
  'if len(closed) >= self.config["history_rounds"]:' \
  $T/test_m11.py::test_qa2_f4_zero_round_older_than_history_rounds_after_downtime \
  $T/test_m11.py::test_qa2_f4_revert_rate_round_older_than_history_rounds_after_downtime
one R2-F4-lower $C/m11_updater_liveness.py 'mark = min(mark, ctx.reorg_ancestor)' 'mark = mark' \
  $T/test_m11.py::test_qa2_f4_reorg_lowers_the_evaluated_mark
one R2-book $C/m9_oracle_common.py 'elif (r is not None and not r["closed"]' 'elif (False and r is not None and not r["closed"]' \
  $T/test_m11.py::test_a_reported_zero_run_that_grew_and_healed_while_a_new_one_began_is_sent
one SWEEP2-m9-literal $C/m9_oracle_quorum.py 'in_time = rnd.commit is not None and self.times(rnd.commit.block) <= deadline' \
  'in_time = close is not None and self.times(close.block) <= deadline' \
  $T/test_m9_m10_m11_sweep.py -k sweep_m9_m10
one SWEEP2-m11-abort $C/m11_updater_liveness.py 'if self.api_failures >= self.config["proof_api_abort_after"]:' \
  'if self.api_failures >= self.config["proof_api_abort_after"] and not self.answers:' \
  $T/test_m9_m10_m11_sweep.py -k sweep_m11
one SWEEP2-m11-mark $C/m11_updater_liveness.py 'if len(closed) >= self.config["history_rounds"] and (mark is None or c.block <= mark):' \
  'if len(closed) >= self.config["history_rounds"]:' \
  $T/test_m9_m10_m11_sweep.py -k sweep_m11
one SWEEP2-m11-definite $C/m11_updater_liveness.py 'if not aged and (not reported or not proven):' 'if not aged and not reported:' \
  $T/test_m9_m10_m11_sweep.py -k sweep_m11
one SWEEP2-book $C/m9_oracle_common.py 'elif (r is not None and not r["closed"]' 'elif (False and r is not None and not r["closed"]' \
  $T/test_m9_m10_m11_sweep.py -k sweep_m11
