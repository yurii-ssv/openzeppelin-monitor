"""Bounded independent QA in disposable git archives, no audited source edits."""
import io
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import xml.etree.ElementTree as ET
ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
PYTHON = ROOT / 'reconcile/.venv/bin/python'
CANDIDATE = '4205b00d510e3e2e84ccd370836473d2b20d7855'

def run(rev, label, names=(), extra=(), select=None, lint=False):
    scratch = Path(tempfile.mkdtemp(prefix=f'cs-cp2-qa3-{label}-', dir='/private/tmp'))
    try:
        with tarfile.open(fileobj=io.BytesIO(subprocess.check_output(['git','archive',rev],cwd=ROOT))) as tar:
            tar.extractall(scratch, filter='data')
        for source in extra:
            shutil.copyfile(source,scratch/'reconcile/tests'/source.name)
        cmd = [str(ROOT/'reconcile/.venv/bin/ruff'),'check','reconcile'] if lint else [str(PYTHON),'-m','pytest','-vv','-s',*[f'reconcile/tests/{n}' for n in names], '--junitxml',str(OUT/f'{label}.xml'),'--basetemp',str(scratch/'test-tmp')]
        if select: cmd.extend(['-k',select])
        with (OUT/f'{label}.txt').open('w') as stream:
            stream.write(f'revision={rev}\nscratch={scratch}\ncommand={cmd!r}\n');stream.flush()
            proc=subprocess.run(cmd,cwd=scratch,stdout=stream,stderr=subprocess.STDOUT,timeout=180)
            stream.write(f'\nexit={proc.returncode}\n')
        if not lint:
            tree=ET.parse(OUT/f'{label}.xml');lines=[]
            for case in tree.findall('.//testcase'):
                bad=case.find('failure')
                if bad is None: bad=case.find('error')
                lines.append(f'{case.get("classname")}::{case.get("name")}: {"FAIL" if bad is not None else "PASS"}')
                if bad is not None: lines.append(bad.get('message',''))
            (OUT/f'{label}-per-test.txt').write_text('\n'.join(lines)+'\n')
            print(f'{label}: exit={proc.returncode}; cases={len(tree.findall(".//testcase"))}; failures={len(tree.findall(".//failure"))}; errors={len(tree.findall(".//error"))}; skipped={len(tree.findall(".//skipped"))}',flush=True)
        else: print(f'{label}: exit={proc.returncode}',flush=True)
    finally:
        shutil.rmtree(scratch)
        with (OUT/'scratch-restoration.txt').open('a') as stream:stream.write(f'{scratch}: removed={not scratch.exists()}\n')

if __name__=='__main__':
    mode=sys.argv[1]
    if mode=='suite':
        run(CANDIDATE,'suite',[''])
        run(CANDIDATE,'ruff',lint=True)
    elif mode=='prior':
        for label,names in [('qa2-deep',['test_qa2_deep.py']),('cp2-independent',['test_cp2_independent.py']),('cp1-independent',['test_cp1_independent.py','test_cp1_siblings.py']),('real-composition',['test_cp2_real.py'])]:
            run(CANDIDATE,label,names,[OUT/n for n in names])
        run(CANDIDATE,'targeted',['test_switches.py','test_catchup.py','test_history_silence.py','test_backfill.py','test_switch_ids.py'])
    elif mode=='red':
        run('9e94a1a','cp1-red',['test_switches.py'],[ROOT/'reconcile/tests/test_switches.py'])
        run('d7b3da6','cp2-red',['test_catchup.py'],[ROOT/'reconcile/tests/test_catchup.py'])
        run('718606c','nf-red',['test_cp2_independent.py'],[OUT/'test_cp2_independent.py'])
        run('ff3841f','qf4-red',['test_qa2_deep.py'],[OUT/'test_qa2_deep.py'],select='disabled_key')
    elif mode=='diagnostic':
        extras=[OUT/'test_qa3_diagnostic.py',OUT/'test_qa2_deep.py']
        run(CANDIDATE,'m5-reorg-diagnostic-replay',['test_qa3_diagnostic.py'],extras)
        run('ff3841f','m5-reorg-prior-replay',['test_qa3_diagnostic.py'],extras+[ROOT/'reconcile/tests/test_switch_ids.py'],select='False')
    elif mode=='new':
        run(CANDIDATE,'qa3-boundaries',['test_qa3_boundaries.py'],[OUT/'test_qa3_boundaries.py',OUT/'test_qa2_deep.py'])
