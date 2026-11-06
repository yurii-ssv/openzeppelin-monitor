"""CP2 repair 1 evidence runner: every run in a complete `git archive` copy under /private/tmp, removed afterwards.

  python run_evidence.py candidate   # QA's original probes + CP1 probes on the candidate tree
  python run_evidence.py red         # new/extended tests on the CP2 candidate 718606c and (global subset) on 9e94a1a
  python run_evidence.py falsifiers  # one sabotage per new mechanism on the candidate tree
The candidate tree = `git archive 718606c` + the repair's changed files from the worktree (OVERLAY).
"""
import io
import shutil
import subprocess
import sys
import tarfile
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
OUT = Path(__file__).resolve().parent
PYTHON = ROOT / "reconcile/.venv/bin/python"
CP2 = "718606c5ee2d43c260240101c06642340cabc1a0"
CODE_BASE = "9e94a1a"
QA1 = "plans/check-switches/qa/cp2-round-1"
CP1QA2 = "plans/check-switches/qa/cp1-round-2"
OVERLAY = ["reconcile/eb_reconcile.py", "reconcile/tests/test_catchup.py", "reconcile/tests/test_history_silence.py",
           "deploy/README.md", "reconcile/README.md", "reconcile/checks/README.md"]
TESTS = ["reconcile/tests/test_catchup.py", "reconcile/tests/test_history_silence.py"]


def run(rev, label, files, overlay=(), extra=(), select=None, sabotage=None):
    scratch = Path(tempfile.mkdtemp(prefix=f"cs-cp2-r1-{label}-", dir="/private/tmp"))
    try:
        data = subprocess.check_output(["git", "archive", rev], cwd=ROOT)
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            archive.extractall(scratch, filter="data")
        for rel in overlay:
            shutil.copyfile(ROOT / rel, scratch / rel)
        for name, text in extra:
            (scratch / "reconcile/tests" / name).write_text(text)
        if sabotage:
            path, old, new = sabotage
            src = (scratch / path).read_text()
            assert src.count(old) == 1, f"sabotage {label} does not apply exactly once"
            (scratch / path).write_text(src.replace(old, new))
        xml = scratch / "junit.xml"
        cmd = [str(PYTHON), "-m", "pytest", "-q", "-p", "no:cacheprovider", *files, "--junitxml", str(xml),
               "--basetemp", str(scratch / "test-tmp")]
        if select:
            cmd += ["-k", select]
        res = subprocess.run(cmd, cwd=scratch, capture_output=True, text=True, timeout=600)
        tree = ET.parse(xml)
        lines = [f"label={label}", f"revision={rev}", f"overlay={list(overlay)}",
                 f"extra={[n for n, _ in extra]}", f"select={select!r}",
                 f"sabotage={(sabotage[0], sabotage[1][:120], sabotage[2][:120]) if sabotage else None}",
                 f"exit={res.returncode}", "summary=" + res.stdout.strip().splitlines()[-1], ""]
        for case in tree.findall(".//testcase"):
            bad = case.find("failure") if case.find("failure") is not None else case.find("error")
            lines.append(f'{case.get("name")}: {"FAIL" if bad is not None else "PASS"}')
            if bad is not None:
                lines.append("    " + (bad.get("message") or "").splitlines()[0][:300])
        (OUT / f"{label}.txt").write_text("\n".join(lines) + "\n")
        print(label, lines[7], lines[6], flush=True)
    finally:
        shutil.rmtree(scratch)
        with (OUT / "scratch-restoration.txt").open("a") as fh:
            fh.write(f"{scratch}: removed={not scratch.exists()}\n")


def git_show(path):
    return subprocess.check_output(["git", "show", f"plan/check-switches:{path}"], cwd=ROOT, text=True)


E = "reconcile/eb_reconcile.py"
FALSIFIERS = {
    "F1-no-live-repeat": (E, '''        with self.db.tx():
            self.db.set_meta(f"catchup:{check_id}", {"state": "pending", "history_block": floor,''',
                          '''        return
        with self.db.tx():
            self.db.set_meta(f"catchup:{check_id}", {"state": "pending", "history_block": floor,'''),
    "F2-no-backfill-repeat": (E, '''            if check_id in ph.get("evaluated", ()) and floor is not None and cursor < floor:''',
                              '''            if False:'''),
    "F3-no-backfill-reenable-record": (E, '''            if ph.get("history_block") is None:
                return  # its silent evaluation''', '''            if True:
                return  # its silent evaluation'''),
    "F4-no-deferral": (E, '''            deferred = frozenset(r[0] for r in self.db.conn.execute(
                "SELECT key FROM incidents WHERE check_id=? AND state='open' AND last_seen_block>?",
                (check.id, block)))''', '''            deferred = frozenset()'''),
    "F5-marker-cleared-not-handed-over": (E, '''self.db.set_meta(f"reorg:{check.id}", block if deferred else None)''',
                                          '''self.db.set_meta(f"reorg:{check.id}", None)'''),
    "F6-no-floor-on-the-pin": (E, '''"history_block": max(block, self._silent_floor(check_id) or block)}''',
                               '''"history_block": block}'''),
    "F7-repeat-for-never-run-checks": (E, '''        if floor is None or cursor >= floor or (self.db.meta(f"run:{check_id}") or {}).get("block") is None:''',
                                       '''        if floor is None or cursor >= floor:'''),
    "F8-no-durable-boundary": (E, '''                    self.db.set_meta(f"silent:{check.id}", pinned)''', '''                    pass'''),
    "F9-backfill-pin-ignores-record": (E, '''        if self._catchup(check_id) is not None:
            pin = max(pin, self._catchup_block(check_id, block))''', '''        if False:
            pin = max(pin, self._catchup_block(check_id, block))'''),
    "F11-absence-resolves-deferred-keys": (E, "{f.key for f in findings if not f.resolve} | deferred, block)",
                                           "{f.key for f in findings if not f.resolve}, block)"),
    "F10-no-boundary-live-gate": (E, '''                if (floor := self._silent_floor(check.id)) is not None and block <= floor:''',
                                  '''                if False:'''),
}

if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "candidate":
        extra = [("test_cp2_independent.py", git_show(f"{QA1}/test_cp2_independent.py")),
                 ("test_cp2_real.py", git_show(f"{QA1}/test_cp2_real.py")),
                 ("test_cp1_independent.py", git_show(f"{CP1QA2}/test_cp1_independent.py")),
                 ("test_cp1_siblings.py", git_show(f"{CP1QA2}/test_cp1_siblings.py"))]
        run(CP2, "candidate-qa-probes", [f"reconcile/tests/{n}" for n, _ in extra], OVERLAY, extra)
    elif mode == "red":
        run(CP2, "red-on-718606c", TESTS, overlay=TESTS)
        run(CODE_BASE, "known-red-global-on-9e94a1a", ["reconcile/tests/test_history_silence.py"], overlay=TESTS,
            select="(nf1 or nf2 or repeat_does or orphaned or condition_new or healed or survives or above_the) and global"
                   " or while_backfilling_repeats")
    elif mode == "falsifiers":
        for label, sab in FALSIFIERS.items():
            run(CP2, f"falsifier-{label}", TESTS, OVERLAY, sabotage=sab)
    else:
        raise SystemExit(f"unknown mode {mode}")
