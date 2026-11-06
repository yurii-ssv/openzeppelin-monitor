"""Reproducible archive-only QA; scratch data and experiments stay under /private/tmp."""
import io
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
PYTHON = ROOT / "reconcile/.venv/bin/python"
CANDIDATE = "ff3841ffd1bfc2b7b6478f8c3941aefaa5d27833"


def run(rev, label, names, extra=(), select=None):
    scratch = Path(tempfile.mkdtemp(prefix=f"cs-cp2-qa2-{label}-", dir="/private/tmp"))
    try:
        with tarfile.open(fileobj=io.BytesIO(subprocess.check_output(["git", "archive", rev], cwd=ROOT))) as archive:
            archive.extractall(scratch, filter="data")
        for source in extra:
            shutil.copyfile(source, scratch / "reconcile/tests" / source.name)
        cmd = [str(PYTHON), "-m", "pytest", "-vv", "-s", *[f"reconcile/tests/{n}" for n in names],
               "--junitxml", str(OUT / f"{label}.xml"), "--basetemp", str(scratch / "test-tmp")]
        if select:
            cmd.extend(["-k", select])
        with (OUT / f"{label}.txt").open("w") as stream:
            stream.write(f"revision={rev}\nscratch={scratch}\ncommand={cmd!r}\n")
            stream.flush()
            result = subprocess.run(cmd, cwd=scratch, stdout=stream, stderr=subprocess.STDOUT, timeout=180)
            stream.write(f"\nexit={result.returncode}\n")
        tree = ET.parse(OUT / f"{label}.xml")
        lines = []
        for case in tree.findall(".//testcase"):
            failed = case.find("failure")
            if failed is None:
                failed = case.find("error")
            lines.append(f'{case.get("classname")}::{case.get("name")}: {"FAIL" if failed is not None else "PASS"}')
            if failed is not None:
                lines.append(failed.get("message", ""))
        (OUT / f"{label}-per-test.txt").write_text("\n".join(lines) + "\n")
        print(f"{label}: exit={result.returncode}; cases={len(tree.findall('.//testcase'))}; "
              f"failures={len(tree.findall('.//failure'))}; errors={len(tree.findall('.//error'))}", flush=True)
    finally:
        shutil.rmtree(scratch)
        with (OUT / "scratch-restoration.txt").open("a") as stream:
            stream.write(f"{scratch}: removed={not scratch.exists()}\n")


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["base"]:
        run("d7b3da6a353b2e5f54ff95af00d099cacf291e4c", "cp2-red-on-cp1", ["test_catchup.py"], [ROOT / "reconcile/tests/test_catchup.py"])
        run("9e94a1a", "cp1-red-on-code-base", ["test_switches.py"], [ROOT / "reconcile/tests/test_switches.py"])
        run("718606c5ee2d43c260240101c06642340cabc1a0", "round1-repro-red", ["test_cp2_independent.py"], [OUT / "test_cp2_independent.py"])
    elif sys.argv[1:] == ["new"]:
        run(CANDIDATE, "new-probes-complete", ["test_qa2_deep.py"], [OUT / "test_qa2_deep.py"])
    elif sys.argv[1:] == ["guard-real"]:
        run(CANDIDATE, "m5-restart-proof", ["test_qa2_deep.py"], [OUT / "test_qa2_deep.py"], select="disabled_key")
    elif sys.argv[1:] == ["m5-prior"]:
        run("718606c5ee2d43c260240101c06642340cabc1a0", "m5-prior-candidate", ["test_qa2_deep.py"], [OUT / "test_qa2_deep.py"], select="disabled_key")
    elif sys.argv[1:] == ["nf-final"]:
        run(CANDIDATE, "cp2-independent-final", ["test_cp2_independent.py"], [OUT / "test_cp2_independent.py"])
        run("718606c5ee2d43c260240101c06642340cabc1a0", "round1-repro-red-final", ["test_cp2_independent.py"], [OUT / "test_cp2_independent.py"])
    else:
        run(CANDIDATE, "cp1-independent", ["test_cp1_independent.py", "test_cp1_siblings.py"], [OUT / "test_cp1_independent.py", OUT / "test_cp1_siblings.py"])
        run(CANDIDATE, "cp2-independent", ["test_cp2_independent.py"], [OUT / "test_cp2_independent.py"])
        run(CANDIDATE, "real-check-composition", ["test_cp2_real.py"], [OUT / "test_cp2_real.py"])
