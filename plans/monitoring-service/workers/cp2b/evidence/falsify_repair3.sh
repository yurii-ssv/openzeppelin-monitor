#!/bin/bash
# Repair-3 falsifiers (QA attempt 3 findings and the repair-3 generalisation): each mutation must turn its named test(s) red, and the restored file must pass with a clean
# tree. Every pytest process gets its own fresh bytecode cache (a same-size mutation must not reuse stale .pyc).
#   bash plans/monitoring-service/workers/cp2b/evidence/falsify_repair3.sh
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
one R3-F1-lead $C/m11_updater_liveness.py 'episodes = self.liveness(ctx, self.zero_lead(ctx, commits, closed, rounds, block) + rounds, block)' \
  'episodes = self.liveness(ctx, rounds, block)' \
  $T/test_m11.py::test_qa3_f1_two_zero_rounds_sliding_out_of_history_are_not_reported_again \
  $T/test_m11.py::test_qa3_f1_long_zero_outage_recovery_sends_no_further_pages \
  $T/test_m11.py::test_qa3_f1_ongoing_outage_longer_than_history_keeps_one_incident_and_counts_every_round
one R3-F1-walk $C/m11_updater_liveness.py '        while i > 0:' '        while i > 0 and len(lead) < 1:' \
  $T/test_m11.py::test_qa3_f1_long_zero_outage_recovery_sends_no_further_pages
one R3-F2-lone $C/m9_oracle_quorum.py 'reference = leaders[0] if len(leaders) == 1 and top >= 2 else None' \
  'reference = leaders[0] if len(leaders) == 1 else None' \
  $T/test_m9.py::test_qa3_f2_stray_single_vote_round_is_not_positive_proof_that_a_dissenting_slot_agrees \
  $T/test_m9.py::test_qa3_f2_timed_out_round_with_only_the_dissenter_voting_does_not_heal_its_page
one R3-F2-self $C/m9_oracle_quorum.py 'if not any(s != slot and st.reference in r for s, r in by_slot.items()):' 'if False:' \
  $T/test_m9.py::test_qa3_f2_self_referential_committed_root_does_not_heal
one R3-F2-slots $C/m9_oracle_quorum.py 'backers.setdefault(v.root, set()).add(v.slot)' \
  'backers.setdefault(v.root, set()).add((v.slot, v.oracle))' \
  $T/test_m9.py::test_qa3_f2_one_slot_voting_one_root_twice_is_no_reference
one R3-RR-zero $C/m11_updater_liveness.py 'stats.append((counted, benign, total, len(counted) / total if total else None))' \
  'stats.append((counted, benign, total, len(counted) / total if total else 0.0))' \
  $T/test_m11.py::test_r3_revert_rate_is_not_healed_by_a_round_without_any_updater_transaction
one R3-RR-lead $C/m11_updater_liveness.py 'episodes += self.revert_rate(ctx, self.revert_lead(ctx, commits, closed, block) + rounds)' \
  'episodes += self.revert_rate(ctx, rounds)' \
  $T/test_m11.py::test_r3_revert_rate_open_while_it_slides_out_of_history_is_kept
one SWEEP3-m11-F1 $C/m11_updater_liveness.py 'episodes = self.liveness(ctx, self.zero_lead(ctx, commits, closed, rounds, block) + rounds, block)' \
  'episodes = self.liveness(ctx, rounds, block)' \
  $T/test_m9_m10_m11_sweep.py -k sweep_m11
one SWEEP3-m9-F2 $C/m9_oracle_quorum.py 'reference = leaders[0] if len(leaders) == 1 and top >= 2 else None' \
  'reference = leaders[0] if len(leaders) == 1 else None' \
  $T/test_m9_m10_m11_sweep.py -k sweep_m9_m10
one SWEEP3-m9-F2-self $C/m9_oracle_quorum.py 'if not any(s != slot and st.reference in r for s, r in by_slot.items()):' 'if False:' \
  $T/test_m9_m10_m11_sweep.py -k sweep_m9_m10
one SWEEP3-m11-RR $C/m11_updater_liveness.py 'stats.append((counted, benign, total, len(counted) / total if total else None))' \
  'stats.append((counted, benign, total, len(counted) / total if total else 0.0))' \
  $T/test_m9_m10_m11_sweep.py -k sweep_m11
