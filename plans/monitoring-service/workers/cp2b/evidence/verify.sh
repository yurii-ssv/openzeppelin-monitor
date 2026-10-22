#!/bin/bash
# CP2b verification rows, each run on its own (exit code + counts). Run from the worktree root:
#   bash plans/monitoring-service/workers/cp2b/evidence/verify.sh
set -u
PY=reconcile/.venv/bin/python
run() {  # label, args...
  local label=$1; shift
  echo "== $label: $PY -m pytest -q -p no:cacheprovider $*"
  $PY -m pytest -q -p no:cacheprovider "$@" 2>&1 | tail -1
  echo "exit ${PIPESTATUS[0]}"
}
echo "head $(git rev-parse HEAD) at $(date -u +%Y-%m-%dT%H:%MZ); git status --porcelain lines: $(git status --porcelain | wc -l | tr -d ' ')"
for f in m9 m10 m11; do run "V2x.1 $f" reconcile/tests/test_$f.py; done
for k in thresholds_from_chain restart rpc_budget; do
  for f in m9 m10 m11; do run "${k} $f" reconcile/tests/test_$f.py -k $k; done
done
run "V2b.7" reconcile/tests/test_m9.py -k "from_the_clock"
# repair 1: the QA attempt-1 reproducers ported as permanent tests, the adversarial sweep, and the QA file as published
for f in m9 m10 m11; do run "qa1 regressions $f" reconcile/tests/test_$f.py -k qa1; done
run "adversarial sweep" reconcile/tests/test_m9_m10_m11_sweep.py
QA=$(mktemp -d)
python3 - "$QA" <<'PY'
import pathlib, sys
s = pathlib.Path("plans/monitoring-service/qa/cp2b-attempt-1.md").read_text()
code = s.split("<!-- BEGIN test_independent.py -->\n```python\n", 1)[1].split("\n```\n<!-- END test_independent.py -->", 1)[0]
(pathlib.Path(sys.argv[1]) / "test_independent.py").write_text(code + "\n")
PY
echo "== QA attempt-1 reproducer as published (two of its setups predate F6, see plan.md Repair 1)"
PYTHONPATH=reconcile:reconcile/tests $PY -m pytest -q -p no:cacheprovider -rf "$QA/test_independent.py" 2>&1 | grep -E "^FAILED|passed|failed" | sed "s|$QA/||"
echo "exit ${PIPESTATUS[0]}"
# repair 2: the QA attempt-2 reproducers ported, and the QA attempt-2 file as published (N1, a NOTE, not repaired)
for f in m9 m11; do run "qa2 regressions $f" reconcile/tests/test_$f.py -k qa2; done
QA2=$(mktemp -d)
python3 - "$QA2" <<'PY'
import pathlib, sys
s = pathlib.Path("plans/monitoring-service/qa/cp2b-attempt-2.md").read_text()
code = s.split("<!-- BEGIN test_qa2.py -->\n```python\n", 1)[1].split("\n```\n<!-- END test_qa2.py -->", 1)[0]
(pathlib.Path(sys.argv[1]) / "test_qa2.py").write_text(code + "\n")
PY
echo "== QA attempt-2 reproducer as published (N1 second weak commit is a NOTE, not in the repair brief)"
PYTHONPATH=reconcile:reconcile/tests $PY -m pytest -q -p no:cacheprovider -rf "$QA2/test_qa2.py" 2>&1 | grep -E "^FAILED|passed|failed" | sed "s|$QA2/||"
echo "exit ${PIPESTATUS[0]}"
run "V2x.6 suite" reconcile/tests
echo "== V2x.6 ruff: reconcile/.venv/bin/ruff check reconcile"
reconcile/.venv/bin/ruff check reconcile; echo "exit $?"
