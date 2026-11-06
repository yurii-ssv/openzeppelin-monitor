import hashlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import xml.etree.ElementTree as ET

root = Path.cwd()
out = root / 'plans/check-switches/qa/cp1-round-1'
python = str(root / 'reconcile/.venv/bin/python')
scratch = Path(tempfile.mkdtemp(prefix='cs-cp1-qa1-', dir='/private/tmp'))

def run(args, cwd, name):
    p = subprocess.run(args, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (out / name).write_text('$ ' + ' '.join(args) + '\nexit=' + str(p.returncode) + '\n' + p.stdout)
    return p

try:
    for label, rev in [('candidate', '7639cff'), ('base', '61307dd'), ('regression-base', '9e94a1a')]:
        target = scratch / label
        target.mkdir()
        data = subprocess.check_output(['git', 'archive', rev, 'reconcile'])
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            archive.extractall(target, filter='data')
        if label != 'candidate':
            shutil.copy2(root / 'reconcile/tests/test_switches.py', target / 'reconcile/tests/test_switches.py')
        if label == 'candidate':
            shutil.copy2(out / 'test_cp1_independent.py', target / 'reconcile/tests/test_cp1_independent.py')
            run([python, '-m', 'pytest', '-vv', 'reconcile/tests/test_cp1_independent.py'], target, 'independent.txt')
        else:
            xml = out / (label + '.xml')
            run([python, '-m', 'pytest', '-q', 'reconcile/tests/test_switches.py', '--junitxml=' + str(xml)], target, label + '-red.txt')
            tree = ET.parse(xml)
            lines = []
            for case in tree.iter('testcase'):
                failure = case.find('failure')
                lines.append(('PASS ' if failure is None else 'FAIL ') + case.attrib['name'])
                if failure is not None:
                    lines.append(failure.attrib.get('message', ''))
            (out / (label + '-per-test.txt')).write_text('\n'.join(lines) + '\n')
    # Exclude the copied candidate tests from the base count by running all original tests explicitly.
    tests = sorted(str(p.relative_to(scratch / 'base')) for p in (scratch / 'base/reconcile/tests').glob('test_*.py') if p.name != 'test_switches.py')
    run([python, '-m', 'pytest', '-q', *tests], scratch / 'base', 'base-original-suite.txt')
finally:
    shutil.rmtree(scratch)
    (out / 'scratch-restoration.txt').write_text(f'Scratch archive exports: {scratch}\nRemoved: {not scratch.exists()}\nCandidate implementation was never edited.\n')
