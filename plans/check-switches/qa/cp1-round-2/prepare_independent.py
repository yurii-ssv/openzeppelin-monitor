"""Preserve round-1 probes; change only QF1's oracle as authorized by D14."""
from pathlib import Path
import subprocess
out = Path(__file__).resolve().parent
original = subprocess.check_output(['git', 'show', 'plan/check-switches:plans/check-switches/qa/cp1-round-1/test_cp1_independent.py'], text=True)
start = original.index("@pytest.mark.parametrize('value'")
end = original.index('\n\ndef test_partial_closure', start)
replacement = '''@pytest.mark.parametrize('value', [0, None, '', False, True])
def test_d6_only_literal_false_may_close(tmp_path, value):
    # D14 replaces only the non-boolean oracle: construction raises, all DB rows unchanged.
    import sqlite3
    env = Env(tmp_path)
    open_four(env)
    raw = env.raw()
    raw['checks'] = {'sw_a': {'enabled': value}}
    cfg = Config({**DEFAULTS, **raw})
    def dump():
        with sqlite3.connect(cfg.db_path) as conn:
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            return {t: sorted(map(repr, conn.execute(f'SELECT * FROM "{t}"'))) for t in tables}
    before = dump()
    built = registry.build(cfg.raw, only=['sw_a'])
    if type(value) is not bool:
        with pytest.raises(SystemExit, match=r'checks\\.sw_a\\.enabled: must be true or false'):
            Worker(cfg, FakeRpc(env.chain), DB(cfg.db_path), env.notifier, clock=env.clock, checks=built)
        assert dump() == before
        return
    w = Worker(cfg, FakeRpc(env.chain), DB(cfg.db_path), env.notifier, clock=env.clock, checks=built)
    w.bootstrap()
    rows = list(w.db.conn.execute("SELECT state FROM incidents WHERE check_id='sw_a'"))
    expected = 'disabled' if value is False else 'open'
    assert [r[0] for r in rows] == [expected] * 4
    assert w.disabled == (['sw_a'] if value is False else [])
'''
(out/'test_cp1_independent.py').write_text(original[:start]+replacement+original[end:])
import difflib
(out/'round1-oracle-change.diff').write_text(''.join(difflib.unified_diff(original.splitlines(True), (out/'test_cp1_independent.py').read_text().splitlines(True), fromfile='round1', tofile='round2-D14')))
