#!/bin/bash
# usage (from the worktree): bash plans/monitoring-service/workers/cp2b/evidence/falsify.sh <id> <file> <python-literal-old> <python-literal-new> <test node>
set -u
id=$1; f=$2; old=$3; new=$4; node=$5
cd "$(git rev-parse --show-toplevel)"
reconcile/.venv/bin/python - "$f" "$old" "$new" <<'PY'
import sys
f, old, new = sys.argv[1:]
s = open(f).read()
assert s.count(old) == 1, f"mutation anchor found {s.count(old)} times"
open(f, "w").write(s.replace(old, new))
PY
echo "[$id] mutated: $(git diff --stat | tail -1)"
reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider "$node" 2>&1 | tail -1
echo "[$id] mutated test exit: ${PIPESTATUS[0]}"
git checkout -- "$f"
reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider "$node" 2>&1 | tail -1
echo "[$id] restored test exit: ${PIPESTATUS[0]}"
echo "[$id] git status --porcelain: '$(git status --porcelain)'"
