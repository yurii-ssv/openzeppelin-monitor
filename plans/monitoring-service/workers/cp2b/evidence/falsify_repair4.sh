#!/bin/bash
# Repair-4 falsifiers (QA attempt 4 findings, the repair-4 generalisation R4-G1 and the owner D14 switches): each mutation must turn its named test(s) red, and the restored file must pass with a clean
# tree. Every pytest process gets its own fresh bytecode cache (a same-size mutation must not reuse stale .pyc).
#   bash plans/monitoring-service/workers/cp2b/evidence/falsify_repair4.sh
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
echo "head $(git rev-parse HEAD) at $(date -u +%Y-%m-%dT%H:%MZ)"
T=reconcile/tests
M11=$C/m11_updater_liveness.py
one R4-F1-window $M11 'total = len(rd["ours_all"]) + len(counted)' 'total = len(rd["ours"]) + len(counted)' \
  $T/test_m11.py::test_qa4_f1_late_successful_updates_count_in_the_denominator
one R4-F1-blocknum $M11 '        if ref is not None and ref in by_ref:' '        if False:' \
  $T/test_m11.py::test_qa4_f1_a_late_revert_for_the_older_root_after_the_next_commit_counts_in_the_older_round
one R4-F1-older $M11 '            older = min(owner, default=start)' '            older = start' \
  $T/test_m11.py::test_qa4_f1_a_revert_for_a_round_older_than_the_evaluated_rounds_is_not_lost
one R4-F1-position $M11 '            if (c.block, c.tx_index) >= pos:' '            if c.block > row["block"]:' \
  $T/test_m11.py::test_r4_round_of_orders_an_undecodable_revert_by_block_and_transaction_index
one R4-F1-drop-guard $M11 'if open_eps.get(f"revert-rate:{c.ref_block}", {""}) <= {r["tx_hash"] for r in counted}:' \
  'if f"revert-rate:{c.ref_block}" in open_eps:' \
  $T/test_m11.py::test_r4_reorg_orphaning_the_late_reverts_of_an_open_round_resolves_with_the_reorg_text
one R4-F1-drop $M11 '                    out.append(Episode(
                        key="revert-rate", episode=f"revert-rate:{c.ref_block}", active=False, occurred=False,' \
  '                    (lambda e: None)(Episode(
                        key="revert-rate", episode=f"revert-rate:{c.ref_block}", active=False, occurred=False,' \
  $T/test_m11.py::test_qa4_f1_a_run_inside_a_slow_round_warns_then_resolves_on_the_full_count
# with the API down every cluster is UNKNOWN too, so a proven page is kept by either guard: the error guard alone is
# shown by the warn-level row, both guards together by the QA reproducer
one R4-F2-error $M11 '        if raw and not saved and (result.get("error") or' '        if raw and not saved and (False or' \
  $T/test_m11.py::test_qa4_f2_reorg_run_with_the_api_down_keeps_a_zero_round_warn_unchanged
two R4-F2-both $M11 '        if raw and not saved and (result.get("error") or' '        if raw and not saved and (False or' \
  '                                                          and result["unknown"])):' \
  '                                                          and False)):' \
  $T/test_m11.py::test_qa4_f2_reorg_run_with_proof_api_down_keeps_the_zero_round_page \
  $T/test_m11.py::test_qa4_f2_restart_between_the_failed_reorg_run_and_its_retry
one R4-F2-partial $M11 '                                                          and result["unknown"])):' \
  '                                                          and False)):' \
  $T/test_m11.py::test_qa4_f2_reorg_run_with_a_partial_answer_omitting_the_mismatching_cluster_keeps_the_page
one R4-G1 $M11 '1 << 62 if ctx.reorg_ancestor is None else' '1 << 62 if True else' \
  $T/test_m11.py::test_r4_reorg_removing_the_clean_round_of_a_resolved_revert_rate_older_than_history_reopens_it
one R4-SWEEP-rate $M11 'total = len(rd["ours_all"]) + len(counted)' 'total = len(rd["ours"]) + len(counted)' \
  $T/test_m9_m10_m11_sweep.py -k "test_sweep_m11"
one R4-SWEEP-G1 $M11 '1 << 62 if ctx.reorg_ancestor is None else' '1 << 62 if True else' \
  "$T/test_m9_m10_m11_sweep.py::test_sweep_m11[92]" "$T/test_m9_m10_m11_sweep.py::test_sweep_m11[569]"
one R4-D14-dissent $C/m9_oracle_quorum.py '(self.dissent(states) if self.config["dissent_enabled"]' '(self.dissent(states) if True' \
  $T/test_m9.py::test_r4_dissent_disabled_is_silent_and_says_so
one R4-D14-revert $M11 '        if not self.config["revert_rate_enabled"]:' '        if False:' \
  $T/test_m11.py::test_r4_revert_rate_disabled_is_silent_and_says_so
one R4-D14-proof $M11 '        if active and not self.config["zero_round_proof_enabled"]:' '        if False:' \
  $T/test_m11.py::test_r4_zero_round_proof_disabled_asks_nothing_and_stays_warn
one R4-D14-resolve-once $C/m9_oracle_common.py '            out.append(e)
        return out' '        return out' \
  $T/test_m9.py::test_r4_dissent_disabled_with_an_open_incident_resolves_it_once \
  $T/test_m11.py::test_r4_revert_rate_disabled_with_an_open_incident_resolves_it_once
# re-guards of repair-3 falsifiers whose anchors repair 4 rewrote (R3-RR-lead, R3-RR-attr), same tests
one R3-RR-lead-r4 $M11 '        start = self.revert_lead(ctx, commits, first)' '        start = first' \
  $T/test_m11.py::test_r3_revert_rate_open_while_it_slides_out_of_history_is_kept
one R3-RR-attr-r4 $M11 '        if ref is not None and ref in by_ref:' '        if False:' \
  $T/test_m11.py::test_r3_a_revert_counts_only_in_the_round_of_its_blocknum_when_windows_overlap \
  $T/test_m11.py::test_r3_a_late_revert_for_the_older_root_counts_in_the_older_round_only
