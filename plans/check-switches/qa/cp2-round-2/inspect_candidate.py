"""Static scope, identity and redacted secret-pattern inventory for the pinned candidate."""
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
HEAD = "ff3841ffd1bfc2b7b6478f8c3941aefaa5d27833"
BASE = "d7b3da6a353b2e5f54ff95af00d099cacf291e4c"


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def blob(rev, path):
    return git("show", f"{rev}:{path}")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def build_source(rev):
    src = blob(rev, "reconcile/checks/registry.py").decode()
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "build")
    return "\n".join(src.splitlines()[fn.lineno - 1:fn.end_lineno]).encode()


cp2 = {"reconcile/eb_reconcile.py", "reconcile/tests/test_catchup.py", "reconcile/tests/test_switches.py",
       "reconcile/tests/harness.py", "deploy/README.md", "reconcile/checks/README.md", "reconcile/README.md",
       "reconcile/config.example.json", "reconcile/tests/test_history_silence.py"}
cp1 = {"reconcile/checks/registry.py", "reconcile/alerts.py"} | cp2
with (OUT / "scope-proof.txt").open("w") as stream:
    for base, allowed, label in [("718606c", {"reconcile/eb_reconcile.py", "reconcile/alerts.py", "reconcile/tests/test_catchup.py", "reconcile/tests/test_history_silence.py", "deploy/README.md", "reconcile/checks/README.md", "reconcile/README.md"}, "repair"), (BASE, cp2, "CP2"), ("9e94a1a", cp1, "composed")]:
        paths = git("diff", "--name-only", base, HEAD).decode().splitlines()
        (OUT / f"{label}-paths.txt").write_text("\n".join(paths) + "\n")
        (OUT / f"{label}-stat.txt").write_bytes(git("diff", "--stat", base, HEAD))
        unexpected = [p for p in paths if p not in allowed and not p.startswith("plans/check-switches/workers/") and p != "plans/check-switches/qa/DEFECT-CLASSES.md" and not p.startswith("plans/check-switches/")]
        stream.write(f"{label}: changed={len(paths)}; outside literal allowed paths={unexpected}\n")
    stream.write(".gitignore provenance:\n" + git("log", "--format=%h %s", "9e94a1a.." + HEAD, "--", ".gitignore").decode())
    stream.write(git("diff", "9e94a1a", HEAD, "--", ".gitignore").decode())
    old = json.loads(blob(BASE, "reconcile/config.example.json"))
    new = json.loads(blob(HEAD, "reconcile/config.example.json"))
    old.pop("_comment"); new.pop("_comment")
    assert old == new
    stream.write("config.example.json: all values except _comment byte-equivalent after JSON decoding\n")
    stream.write("registry.build hashes:\n")
    for rev in ["9e94a1a", BASE, HEAD]:
        stream.write(f"{rev} {sha(build_source(rev))}\n")
    assert len({sha(build_source(rev)) for rev in ["9e94a1a", BASE, HEAD]}) == 1
    guarded = ["reconcile/db.py", "reconcile/metrics.py", "reconcile/tests/harness.py",
               "reconcile/tests/test_backfill.py", "reconcile/checks/m*.py", "reconcile/tests/test_m*.py"]
    diff = git("diff", "9e94a1a", HEAD, "--", *guarded)
    (OUT / "guarded.diff").write_bytes(diff)
    assert not diff
    stream.write("All guarded paths unchanged against code base.\n")
    for rev in ["9e94a1a", BASE, HEAD]:
        stream.write(f"test_backfill.py {rev} {sha(blob(rev, 'reconcile/tests/test_backfill.py'))}\n")
    for path in ["plans/check-switches/agents/checkpoint-qa.md", "plans/check-switches/qa/DEFECT-CLASSES.md",
                 "plans/check-switches/HANDOFF-coordinator.md"]:
        stream.write(f"{path}: {sha(blob(HEAD, path))}\n")
    stream.write("README diff for manual section-scope review:\n")
    stream.write(git("diff", "--unified=2", "9e94a1a", HEAD, "--", "reconcile/checks/README.md").decode())

# Scan every tracked textual file; never print a matched value. Review findings by path/line only.
patterns = {
    "telegram-token": r"\b[0-9]{8,12}:[A-Za-z0-9_-]{30,}\b",
    "discord-webhook": r"https://(?:discord(?:app)?\.com)/api/webhooks/[0-9]+/[A-Za-z0-9_-]{25,}",
    "private-key-header": r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    "aws-access-key": r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
    "github-token": r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b",
    "url-credential": r"https?://[^\s/\"']+:[^\s/@\"']+@",
    "credential-assignment": r'''(?i)["']?(?:api[_-]?key|secret[_-]?key|private[_-]?key|password|telegram_token|discord_webhook)["']?\s*[:=]\s*["'][^"'\n]{8,}["']''',
}
hits = []
text_count, binary_count = 0, 0
paths = git("ls-tree", "-r", "--name-only", HEAD).decode().splitlines()
for path in paths:
    data = blob(HEAD, path)
    if b"\0" in data:
        binary_count += 1
        continue
    try:
        value = data.decode()
    except UnicodeDecodeError:
        binary_count += 1
        continue
    text_count += 1
    for lineno, line in enumerate(value.splitlines(), 1):
        for label, pattern in patterns.items():
            if re.search(pattern, line):
                hits.append(f"{path}:{lineno}: {label}")
(OUT / "secret-pattern-scan.txt").write_text(
    f"candidate={HEAD}; tracked files={len(paths)}; text={text_count}; binary/non-UTF8={binary_count}\n"
    + "Patterns=" + ", ".join(patterns) + "\n"
    + "\n".join(hits) + f"\npattern hits={len(hits)} (values redacted; manual review required)\n")

monitor_files = sorted((ROOT / "deploy/config/monitors").glob("*.json"))
with (OUT / "docs-facts.txt").open("w") as stream:
    for path in monitor_files:
        raw = json.loads(path.read_text())
        stream.write(f"{path.relative_to(ROOT)}: paused={raw.get('paused', '<absent>')}\n")
    stream.write("\nRegistered imports:\n" + (ROOT / "reconcile/checks/__init__.py").read_text())
    example = json.loads(blob(HEAD, "reconcile/config.example.json"))
    stream.write("\nexample backfill=" + json.dumps(example["backfill"]) + "\n")
    stream.write("example _comment=" + example["_comment"] + "\n")
print(f"scope inspected; secret-pattern scan: {text_count} text files, {len(hits)} redacted hits")
