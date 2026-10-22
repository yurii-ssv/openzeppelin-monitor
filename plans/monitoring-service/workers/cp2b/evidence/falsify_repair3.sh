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
two() {  # id file old1 new1 old2 new2 test-args...: both guards of one rule mutated together
  local id=$1 f=$2 o1=$3 n1=$4 o2=$5 n2=$6; shift 6
  $PY - "$f" "$o1" "$n1" "$o2" "$n2" <<'PY' || { echo "[$id] anchor error"; return; }
import sys
f, o1, n1, o2, n2 = sys.argv[1:]
s = open(f).read()
for old, new in ((o1, n1), (o2, n2)):
    assert s.count(old) == 1, f"mutation anchor found {s.count(old)} times"
    s = s.replace(old, new)
open(f, "w").write(s)
PY
  echo "[$id] mutated $f: $(git diff --stat | tail -1)"
  pt "$@"; echo "[$id] mutated exit: $?"
  git checkout -- "$f"
  pt "$@"; echo "[$id] restored exit: $?"
  echo "[$id] git status --porcelain: '$(git status --porcelain)'"
}
LONE_OLD='reference = leaders[0] if len(leaders) == 1 and top >= 2 else None'
LONE_NEW='reference = leaders[0] if len(leaders) == 1 else None'
SELF_OLD='if not any(s != slot and st.reference in r for s, r in by_slot.items()):'
echo "head $(git rev-parse HEAD) at $(date -u +%Y-%m-%dT%H:%MZ)"
T=reconcile/tests
one R3-F1-lead $C/m11_updater_liveness.py 'episodes = self.liveness(ctx, self.zero_lead(ctx, commits, closed, rounds, block) + rounds, block)' \
  'episodes = self.liveness(ctx, rounds, block)' \
  $T/test_m11.py::test_qa3_f1_two_zero_rounds_sliding_out_of_history_are_not_reported_again \
  $T/test_m11.py::test_qa3_f1_long_zero_outage_recovery_sends_no_further_pages \
  $T/test_m11.py::test_qa3_f1_ongoing_outage_longer_than_history_keeps_one_incident_and_counts_every_round
one R3-F1-walk $C/m11_updater_liveness.py '        while i > 0:' '        while i > 0 and len(lead) < 1:' \
  $T/test_m11.py::test_qa3_f1_long_zero_outage_recovery_sends_no_further_pages
# a lone-voter round is refused by two guards (no one-slot reference; no self-referential agreement), each sufficient
# for the heal: mutating one alone is an equivalent mutant, so the falsifier removes both
two R3-F2-both $C/m9_oracle_quorum.py "$LONE_OLD" "$LONE_NEW" "$SELF_OLD" 'if False:' \
  $T/test_m9.py::test_qa3_f2_stray_single_vote_round_is_not_positive_proof_that_a_dissenting_slot_agrees \
  $T/test_m9.py::test_qa3_f2_timed_out_round_with_only_the_dissenter_voting_does_not_heal_its_page
one R3-F2-self $C/m9_oracle_quorum.py 'if not any(s != slot and st.reference in r for s, r in by_slot.items()):' 'if False:' \
  $T/test_m9.py::test_qa3_f2_self_referential_committed_root_does_not_heal
one R3-F2-slots $C/m9_oracle_quorum.py 'backers.setdefault(v.root, set()).add(v.slot)' \
  'backers.setdefault(v.root, set()).add((v.slot, v.oracle))' \
  $T/test_m9.py::test_r3_one_slot_voting_twice_does_not_make_another_slot_a_dissenter
one R3-RR-zero $C/m11_updater_liveness.py 'stats.append((counted, benign, total, len(counted) / total if total else None))' \
  'stats.append((counted, benign, total, len(counted) / total if total else 0.0))' \
  $T/test_m11.py::test_r3_revert_rate_is_not_healed_by_a_round_without_any_updater_transaction
one R3-RR-lead $C/m11_updater_liveness.py 'episodes += self.revert_rate(ctx, self.revert_lead(ctx, commits, closed, block) + rounds)' \
  'episodes += self.revert_rate(ctx, rounds)' \
  $T/test_m11.py::test_r3_revert_rate_open_while_it_slides_out_of_history_is_kept
one SWEEP3-m11-F1 $C/m11_updater_liveness.py 'episodes = self.liveness(ctx, self.zero_lead(ctx, commits, closed, rounds, block) + rounds, block)' \
  'episodes = self.liveness(ctx, rounds, block)' \
  $T/test_m9_m10_m11_sweep.py -k sweep_m11
two SWEEP3-m9-F2 $C/m9_oracle_quorum.py "$LONE_OLD" "$LONE_NEW" "$SELF_OLD" 'if False:' \
  $T/test_m9_m10_m11_sweep.py -k sweep_m9_m10
one SWEEP3-m11-RR $C/m11_updater_liveness.py 'stats.append((counted, benign, total, len(counted) / total if total else None))' \
  'stats.append((counted, benign, total, len(counted) / total if total else 0.0))' \
  $T/test_m9_m10_m11_sweep.py -k sweep_m11
# found by the wider sweep / generalisation (repair 3)
one R3-RR-attr $C/m11_updater_liveness.py '            if ref is not None:  # the round of the call' '            if False:  # the round of the call' \
  $T/test_m11.py::test_r3_a_revert_counts_only_in_the_round_of_its_blocknum_when_windows_overlap \
  $T/test_m11.py::test_r3_a_late_revert_for_the_older_root_counts_in_the_older_round_only
one R3-RR-evidence $C/m11_updater_liveness.py 'evidence_block=max([c.block] + [r["block"] for r in counted]),' \
  'evidence_block=c.block,' \
  $T/test_m11.py::test_r3_reorg_orphaning_the_reverts_of_an_open_revert_rate_resolves_with_the_reorg_text
one R3-dissent-evidence $C/m9_oracle_quorum.py 'evidence_block=self.settled_block(first), severity=sev,' \
  'evidence_block=first.last_vote_block, severity=sev,' \
  $T/test_m9.py::test_r3_reorg_removing_the_commit_that_settled_a_dissent_resolves_with_the_reorg_text
one R3-failed-revision $C/m9_oracle_quorum.py 'revision=",".join(map(str, refs)),' 'revision=str(rnd.ref),' \
  $T/test_m9.py::test_r3_a_lower_blocknum_failing_after_a_higher_one_is_sent_before_the_heal
one R3-book-regrown $C/m9_oracle_common.py '        return (bool(r["closed"]) and e.occurred' '        return (False and e.occurred' \
  $T/test_m9.py::test_r3_reorg_growing_an_already_resolved_failed_round_run_sends_the_new_member
one R3-horizon-cap $C/m9_oracle_common.py '            h = min(h, max(at, int(nxt["timestamp"], 16) - 1))' '            h = h' \
  $T/test_m9.py::test_r3_commit_seconds_before_the_deadline_in_an_unconfirmed_block_is_not_a_failed_round \
  $T/test_m9.py::test_r3_seed_295_late_confirmed_votes_do_not_retract_an_attribution
one SWEEP3-m9-horizon $C/m9_oracle_common.py '            h = min(h, max(at, int(nxt["timestamp"], 16) - 1))' '            h = h' \
  $T/test_m9_m10_m11_sweep.py -k sweep_m9_m10
# re-guard of repair 1's F8-clock (its anchor line was rewritten by the horizon cap)
one R3-F8-clock $C/m9_oracle_common.py 'h = max(at, self.ctx.clock() - lag)' 'h = at' \
  $T/test_m9.py::test_qa1_f8_30_minute_timeout_fires_without_a_new_block \
  $T/test_m11.py::test_qa1_f8_60_minute_window_closes_without_a_new_block \
  $T/test_m9.py::test_no_root_committed_for_9h_pages_from_the_clock_without_any_event
