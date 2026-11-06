"""Independent D14 boundary tests. Run only in an isolated archive copy."""
import json
import sqlite3
from pathlib import Path

import pytest
import checks as registry
import db as db_module
import eb_reconcile
from eb_reconcile import Config, DB, DEFAULTS, Worker, load_config
from fakechain import FakeRpc
from test_switches import Env, open_four, PLAN, IDS, _register


@pytest.fixture(autouse=True)
def registered():
    PLAN.clear()
    for cid in IDS:
        _register(cid)
    yield
    for cid in IDS:
        registry.REGISTRY.pop(cid, None)
    PLAN.clear()


def snapshot(path):
    with sqlite3.connect(path) as c:
        return list(c.iterdump())


def cli(monkeypatch, env, cfg):
    real_build = registry.build
    monkeypatch.setattr(eb_reconcile, 'load_config', lambda _: cfg)
    monkeypatch.setattr(eb_reconcile, 'make_rpc', lambda _: FakeRpc(env.chain))
    monkeypatch.setattr(eb_reconcile, 'make_notifier', lambda _: env.notifier)
    monkeypatch.setattr(registry, 'build', lambda raw: real_build(raw, only=['sw_a', 'm5']))
    # Invalid config must never reach bootstrap or an infinite run.
    monkeypatch.setattr(Worker, 'bootstrap', lambda _: pytest.fail('invalid config reached bootstrap'))


@pytest.mark.parametrize('command', ['run', 'once', 'verify-layout', 'status', 'direct'])
@pytest.mark.parametrize('cid,entry', [
    ('sw_a', {'enabled': 0}), ('sw_a', {'enabled': None}), ('sw_a', {'enabled': ''}),
    ('sw_a', {'enabled': 'true'}), ('sw_a', {'enabled': []}), ('sw_a', {'enabled': {}}),
    ('sw_a', {'enabled': 0.0}), ('sw_a', {'enabled': 1.0}),
    ('sw_a', False), ('sw_a', None), ('sw_a', []), ('sw_a', ''), ('sw_a', 1),
    ('retired', {'enabled': 0}), ('retired', {'enabled': None}), ('retired', False),
])
def test_sibling_invalid_config_raises_without_changing_existing_rows(tmp_path, monkeypatch, command, cid, entry):
    env = Env(tmp_path)
    open_four(env)
    cfg = Config({**DEFAULTS, **env.raw()})
    cfg.raw['checks'] = {'sw_b': {'enabled': False}, cid: entry}
    before = snapshot(cfg.db_path)
    cli(monkeypatch, env, cfg)
    with pytest.raises(SystemExit, match=f'checks.{cid}'):
        if command == 'direct':
            Worker(cfg, FakeRpc(env.chain), env.w.db, checks=[])
        else:
            eb_reconcile.main([command, '--config', 'unused'])
    assert snapshot(cfg.db_path) == before
    assert env.w.db.meta('disabled:sw_b') is None


@pytest.mark.parametrize('command', ['export', 'audit'])
def test_read_only_nonworker_commands_accept_bad_switch_and_never_write(tmp_path, monkeypatch, capsys, command):
    env = Env(tmp_path)
    open_four(env)
    cfg = Config({**DEFAULTS, **env.raw()})
    cfg.raw['checks'] = {'sw_a': {'enabled': 0}}
    before = snapshot(cfg.db_path)
    cli(monkeypatch, env, cfg)
    monkeypatch.setattr(eb_reconcile, 'Worker', lambda *a, **kw: pytest.fail('unexpected Worker'))
    assert eb_reconcile.main([command, '--config', 'unused']) == 0
    json.loads(capsys.readouterr().out)
    assert snapshot(cfg.db_path) == before


@pytest.mark.parametrize('command', ['run', 'once', 'verify-layout', 'status'])
def test_invalid_config_causes_no_database_write_statements(tmp_path, monkeypatch, command):
    env = Env(tmp_path)
    open_four(env)
    cfg = Config({**DEFAULTS, **env.raw()})
    cfg.raw['checks'] = {'sw_a': {'enabled': 0}}
    cli(monkeypatch, env, cfg)
    writes = []
    real_migrate = db_module.migrate
    def traced(conn):
        conn.set_trace_callback(lambda sql: writes.append(sql) if sql.lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE', 'ALTER')) else None)
        return real_migrate(conn)
    monkeypatch.setattr(db_module, 'migrate', traced)
    with pytest.raises(SystemExit, match='checks.sw_a.enabled'):
        eb_reconcile.main([command, '--config', 'unused'])
    assert writes == [], f'D14 before-any-write promise violated: {writes}'


@pytest.mark.parametrize('command', ['run', 'once', 'verify-layout'])
def test_invalid_config_does_not_initialize_a_fresh_database(tmp_path, monkeypatch, command):
    env = Env(tmp_path)
    cfg = Config({**DEFAULTS, **env.raw()})
    cfg.raw['checks'] = {'sw_a': {'enabled': 0}}
    cli(monkeypatch, env, cfg)
    assert not Path(cfg.db_path).exists()
    with pytest.raises(SystemExit, match='checks.sw_a.enabled'):
        eb_reconcile.main([command, '--config', 'unused'])
    tables = []
    if Path(cfg.db_path).exists():
        with sqlite3.connect(cfg.db_path) as conn:
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    assert not Path(cfg.db_path).exists(), f'malformed config created DB with tables={tables}'


@pytest.mark.parametrize('command', ['run', 'once', 'verify-layout', 'status'])
def test_invalid_config_leaves_a_preversion_database_unchanged(tmp_path, monkeypatch, command):
    env = Env(tmp_path)
    open_four(env)
    cfg = Config({**DEFAULTS, **env.raw()})
    cfg.raw['checks'] = {'sw_a': {'enabled': 0}}
    # Supported migration input: the schema exists, but its version has not yet been persisted.
    env.w.db.conn.execute("DELETE FROM meta WHERE key='schema_version'")
    before = snapshot(cfg.db_path)
    cli(monkeypatch, env, cfg)
    with pytest.raises(SystemExit, match='checks.sw_a.enabled'):
        eb_reconcile.main([command, '--config', 'unused'])
    assert snapshot(cfg.db_path) == before


def test_shipped_configs_load_and_construct_workers(tmp_path):
    root = Path(__file__).resolve().parents[2]
    example = root/'reconcile/config.example.json'
    cfg = load_config(str(example))
    cfg.raw['db_path'] = str(tmp_path/'example.sqlite')
    w = Worker(cfg, None, DB(cfg.db_path))
    assert w.disabled == []
    assert [c for c in w.checks if c not in IDS] == [c for c in registry.REGISTRY if c not in IDS]
    print(f'{example.relative_to(root)}: load_config + Worker; {len(w.checks)-len(IDS)} shipped checks built')
    files = sorted((root/'deploy/config/networks').glob('*.json'))
    assert files
    from checks.registry import validate_switches
    for path in files:
        # Independent source: network_type/rpc_urls/recovery_config identify Monitor's network schema.
        # These are not worker configs and load_config explicitly forbids their keys.
        raw = json.loads(path.read_text())
        assert raw['network_type'] == 'EVM' and raw['chain_id'] == 1
        validate_switches(raw)
        assert 'checks' not in raw
        print(f'{path.relative_to(root)}: JSON load; no worker checks setting; switches validation accepts')
