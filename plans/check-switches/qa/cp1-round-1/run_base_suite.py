import io
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

root = Path.cwd()
scratch = Path(tempfile.mkdtemp(prefix='cs-cp1-base-suite-', dir='/private/tmp'))
try:
    data = subprocess.check_output(['git', 'archive', '61307dd', 'reconcile'])
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        archive.extractall(scratch, filter='data')
    p = subprocess.run([str(root / 'reconcile/.venv/bin/python'), '-m', 'pytest', '-q', 'reconcile/tests'],
        cwd=scratch, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    (root / 'plans/check-switches/qa/cp1-round-1/base-suite-unrestricted.txt').write_text('base=61307dd\nexit=' + str(p.returncode) + '\n' + p.stdout)
finally:
    shutil.rmtree(scratch)
    print('base scratch removed', not scratch.exists())
