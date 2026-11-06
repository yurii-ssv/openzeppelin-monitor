"""Bounded archive-only QA. Experimental source files never enter the audited tree."""
import io
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import xml.etree.ElementTree as ET

root = Path(__file__).resolve().parents[4]
out = Path(__file__).resolve().parent
python = root/'reconcile/.venv/bin/python'
cleanup = (out/'scratch-restoration.txt').read_text().splitlines() if (out/'scratch-restoration.txt').exists() else []

def run(rev, label, *, independent=False, select=None):
    scratch = Path(tempfile.mkdtemp(prefix=f'cp1-qa2-{label}-', dir='/private/tmp'))
    try:
        data = subprocess.check_output(['git', 'archive', rev], cwd=root)
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            tar.extractall(scratch, filter='data')
        if independent:
            names = ['test_cp1_independent.py', 'test_cp1_siblings.py']
            for name in names:
                shutil.copyfile(out/name, scratch/'reconcile/tests'/name)
        else:
            names = ['test_switches.py']
            shutil.copyfile(root/'reconcile/tests/test_switches.py', scratch/'reconcile/tests/test_switches.py')
        cmd = [str(python), '-m', 'pytest', '-vv', *[f'reconcile/tests/{n}' for n in names], '--junitxml', str(out/f'{label}.xml'), '--basetemp', str(scratch/'test-tmp')]
        if select:
            cmd.extend(['-k', select])
        with (out/f'{label}.txt').open('w') as f:
            f.write(f'revision={rev}\nscratch={scratch}\ncommand={cmd!r}\n'); f.flush()
            p = subprocess.run(cmd, cwd=scratch, stdout=f, stderr=subprocess.STDOUT, timeout=180)
            f.write(f'\nexit={p.returncode}\n')
        tree = ET.parse(out/f'{label}.xml')
        cases = tree.findall('.//testcase')
        summary = []
        for case in cases:
            failure = case.find('failure')
            error = case.find('error')
            bad = failure if failure is not None else error
            summary.append(f'{case.attrib.get("classname")}::{case.attrib["name"]}: {"FAIL" if bad is not None else "PASS"}\n'+ (bad.attrib.get('message','')+'\n' if bad is not None else ''))
        (out/f'{label}-per-test.txt').write_text('\n'.join(summary))
        print(f'{label}: exit={p.returncode}; cases={len(cases)}; failures={len(tree.findall(".//failure"))}; errors={len(tree.findall(".//error"))}', flush=True)
    finally:
        shutil.rmtree(scratch)
        cleanup.append(f'{scratch}: removed={not scratch.exists()}')
        (out/'scratch-restoration.txt').write_text('\n'.join(cleanup)+'\n')

if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        run('62ed4c243edf451915b7c652d571b8294a315285', 'independent-final', independent=True)
    else:
        run('62ed4c243edf451915b7c652d571b8294a315285', 'independent', independent=True)
        run('7639cffb4991ce6bd536cfc607b27ce1aa78032d', 'repair-red', select='non_boolean_enabled or boolean_enabled_controls or validate_switches')
        run('61307ddf3f813cfbe38ef062490224862ccd2b91', 'frozen-base-red')
        run('9e94a1a', 'code-base-red')
