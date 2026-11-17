"""Regression tests for CP1B QA attempt 3 (plans/monitoring-service/qa/cp1b-attempt-3.md).

`qa3_orphan_repro.py` is Appendix A (the HIGH finding's reproducer) byte for byte except its ROOT line, which
names this checkout instead of the QA worktree; it runs as the report ran it, once per argument (0 and 12
confirmations), and exits 0 only when the orphan-only cluster's incident is closed after the reorg and its
6-hour re-alert. `test_qa3_appendix_b.py` is Appendix B under the same single-line change.
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPRO = Path(__file__).with_name("qa3_orphan_repro.py")


@pytest.mark.parametrize("confirmations", ["0", "12"])
def test_qa3_appendix_a_orphan_repro(confirmations):
    r = subprocess.run([sys.executable, str(REPRO), confirmations], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
