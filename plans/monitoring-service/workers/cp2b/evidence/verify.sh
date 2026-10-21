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
run "V2x.6 suite" reconcile/tests
echo "== V2x.6 ruff: reconcile/.venv/bin/ruff check reconcile"
reconcile/.venv/bin/ruff check reconcile; echo "exit $?"
