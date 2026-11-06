"""Per-check switches, CP1 (plan check-switches): a check with `checks.<id>.enabled: false` is disabled cleanly.

At the next start its open incidents become `disabled` (neither open nor resolved) with no message of any kind (D3,
D7), they are never re-alerted (D8), the reorg rewind keeps its event cursor and marker consistent while it is not
built (D11), and `status` and the digest list it (D12). A check that is merely not built (tests' `only=`, stub
instances) is not disabled (D6).

Regression tests put the behavioural assertion first and import new symbols inside the tests that need them, so the
file collects and fails for the behavioural reason on the base revision (V1.7).
"""

from __future__ import annotations

import json
import random
from dataclasses import replace
from pathlib import Path

import pytest
from fakechain import PROXY, FakeChain, FakeClock, FakeRpc
from harness import Capture, advance, outbox
from test_framework import Stub, finding

import checks as registry
from checks.base import Every, OnEvents
from eb_reconcile import DB, DEFAULTS, END_OF_BLOCK, Config, Worker
from eb_storage import u256

IDS = ("sw_a", "sw_b", "sw_c")
PLAN: dict[str, list] = {}  # check id -> the findings its every run returns


def _register(cid: str):
    @registry.register
    class Registered(Stub):
        id = cid

        def __init__(self, config, settings):
            Stub.__init__(self, schedule=(Every(60), OnEvents({"NetworkFeeUpdated"})))
            self.config, self.settings = config, settings
            self.emit = lambda ctx, block: [replace(f) for f in PLAN.get(self.id, [])]
            self.ancestors: list = []

        def run(self, ctx, block):
            self.ancestors.append(ctx.reorg_ancestor)
            return Stub.run(self, ctx, block)

    return Registered


@pytest.fixture
def plan():
    PLAN.clear()
    for cid in IDS:
        _register(cid)
    try:
        yield PLAN
    finally:
        for cid in IDS:
            registry.REGISTRY.pop(cid, None)
        PLAN.clear()


class Env:
    """One database, one chain, one clock and one capturing notifier across worker restarts."""

    def __init__(self, tmp_path, confirmations=0):
        self.tmp_path = tmp_path
        self.chain = FakeChain()
        self.chain.mine()
        self.start = self.chain.sealed
        self.clock = FakeClock()
        self.notifier = Capture()
        self.confirmations = confirmations
        self.w = None

    def raw(self, disabled=(), backfill=False, digest_s=0) -> dict:
        return {
            "chain_id": 1, "proxy": PROXY, "activation_block": self.start, "start_block": self.start,
            "rpc_url_env": "UNUSED", "confirmations": self.confirmations, "max_log_range": 10_000,
            "db_path": str(self.tmp_path / "w.sqlite"),
            "alerts": {"mode": "record", "label": "test", "digest_interval_s": digest_s},
            "inventory": {"api_url": None}, "backfill": {"enabled": backfill},
            "checks": {cid: {"enabled": False} for cid in disabled},
        }

    def start_worker(self, only=("sw_a",), disabled=(), backfill=False, digest_s=0, stubs=None, bootstrap=True):
        """A worker start (restart) on the shared database: `only` = the registered checks it builds (a disabled one
        is skipped by `build`), `disabled` = the ids the config switches off."""
        cfg = Config({**DEFAULTS, **self.raw(disabled, backfill, digest_s)})
        built = registry.build(cfg.raw, only=list(only)) if stubs is None else stubs
        self.w = Worker(cfg, FakeRpc(self.chain), DB(cfg.db_path), self.notifier, clock=self.clock, checks=built)
        if bootstrap:
            self.w.bootstrap()
        return self.w

    def step(self, seconds=60, mine=True):
        self.clock.advance(seconds)
        if mine:
            self.chain.mine()
        advance(self.w)

    # ---- reads (the current worker's connection)

    def rows(self, check=None) -> list[dict]:
        where, args = ("WHERE check_id=?", (check,)) if check else ("", ())
        return [dict(r) for r in self.w.db.conn.execute(f"SELECT * FROM incidents {where} ORDER BY rowid", args)]

    def n_outbox(self) -> int:
        return self.w.db.conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]

    def meta_keys(self, pattern: str) -> dict:
        return {r[0]: json.loads(r[1]) for r in self.w.db.conn.execute(
            "SELECT key, value FROM meta WHERE key GLOB ?", (pattern,))}


def open_four(env: Env) -> None:
    """Live mode, check sw_a: a sent page, a sent warn, a silent warn (delivered), then a sent info whose outbox row
    is still undelivered when the worker stops."""
    PLAN["sw_a"] = [finding("sw_a", "kpage", "page"), finding("sw_a", "kwarn", "warn"),
                    finding("sw_a", "ksilent", "warn", notify=False)]
    env.start_worker(only=("sw_a",))
    env.step()
    PLAN["sw_a"].append(finding("sw_a", "kinfo", "info"))
    env.clock.advance(60)
    env.chain.mine()
    env.w.check_reorg()
    env.w.scan()
    env.w.run_checks()  # the info incident's open message is queued, not delivered (the worker stops here)
    assert [r["state"] for r in env.rows("sw_a")] == ["open"] * 4
    assert len(outbox(env.w, "sent_at IS NULL AND kind='open'")) == 1


# ---------------------------------------------------------------- V1.1 closure, no re-alert, visibility


@pytest.mark.parametrize("backfill", [False, True])
def test_disabled_check_closes_its_incidents_and_is_never_realerted(tmp_path, plan, backfill):
    env = Env(tmp_path)
    open_four(env)
    before = env.n_outbox()
    run_before = env.w.db.meta("run:sw_a")
    cp = env.w.checkpoint()["block"]
    undelivered = outbox(env.w, "sent_at IS NULL")[0]["key"]

    env.start_worker(only=("sw_a",), disabled=("sw_a",), backfill=backfill, digest_s=1)
    created_by_start = env.n_outbox() - before
    for _ in range(26):  # past the 6 h page and the 24 h warn re-alert
        env.step(3600)
    # behavioural assertion first (red on the base revision: the page re-alerts at 6 h)
    assert outbox(env.w, "kind='realert'") == []
    assert created_by_start == 0
    rows = env.rows("sw_a")
    assert [r["state"] for r in rows] == ["disabled"] * 4
    assert {r["resolved_block"] for r in rows} == {cp}
    assert all(json.loads(r["evidence"])["disabled"]["block"] == cp for r in rows)
    assert env.w.db.meta("disabled:sw_a")["closed"] == 4 and env.w.db.meta("disabled:sw_a")["block"] == cp
    assert outbox(env.w, "kind IN ('resolved', 'health') OR key LIKE 'health:check%'") == []
    assert not [r for r in outbox(env.w) if r["check_id"] == "sw_a" and r["kind"] != "open"]
    # the row queued before the closure is still delivered (D7, by design)
    assert outbox(env.w, f"key='{undelivered}'")[0]["sent_at"] is not None
    assert undelivered in [k for _, k, _, _ in env.notifier.sent]
    assert env.w.db.meta("run:sw_a") == run_before  # the check did not run
    st = env.w.status()
    assert st["disabled"] == ["sw_a"] and not any(k.startswith("sw_a/") for k in st["open_incidents"])
    digests = outbox(env.w, "kind='digest'")
    assert digests and "Open incidents: none" in digests[-1]["text"]
    assert "Disabled checks: sw_a" in digests[-1]["text"].splitlines()


def test_a_check_that_is_merely_not_built_is_not_disabled(tmp_path, plan):
    """Control (D6): restarted with `only=` excluding the check and no `enabled: false` → its rows stay open."""
    env = Env(tmp_path)
    open_four(env)
    before = env.n_outbox()
    env.start_worker(only=("sw_b",))
    env.step(7 * 3600)
    # D8 first (red on the base revision): an incident of a check this worker does not run is not re-alerted
    assert outbox(env.w, "kind='realert'") == [] and env.n_outbox() == before
    assert [r["state"] for r in env.rows("sw_a")] == ["open"] * 4
    assert env.w.db.meta("disabled:sw_a") is None
    assert env.w.status()["disabled"] == [] and "sw_a/page" in env.w.status()["open_incidents"]


def test_digest_has_no_disabled_line_without_disabled_checks(tmp_path, plan):
    env = Env(tmp_path)
    env.start_worker(only=("sw_a",), digest_s=1)
    env.step()
    (digest,) = outbox(env.w, "kind='digest'")
    assert "Open incidents: none" in digest["text"] and "Disabled checks" not in digest["text"]


# ---------------------------------------------------------------- V1.2 no notice, marker lifecycle


@pytest.mark.parametrize("backfill", [False, True])
def test_disable_enable_disable_round_trip_sends_nothing_at_start(tmp_path, plan, backfill):
    env = Env(tmp_path)
    PLAN["sw_a"] = [finding("sw_a", "k1", "page"), finding("sw_a", "k2", "warn")]
    env.start_worker(only=("sw_a",))
    env.step()
    first_ids = [r["id"] for r in env.rows("sw_a")]

    def start(**kw):
        before = env.n_outbox()
        env.start_worker(backfill=backfill, **kw)
        assert env.n_outbox() == before, "a start created an outbox row"  # D3: no notice of any kind

    start(only=("sw_a",), disabled=("sw_a",))
    closed = env.rows("sw_a")
    assert [r["state"] for r in closed] == ["disabled", "disabled"]
    marker = env.w.db.meta("disabled:sw_a") or {}
    assert marker.get("closed") == 2

    env.clock.advance(120)
    start(only=("sw_a",), disabled=("sw_a",))  # same config again: nothing changes
    assert env.w.db.meta("disabled:sw_a") == marker and env.rows("sw_a") == closed

    start(only=("sw_a",))  # re-enabled: the marker is cleared by the start
    assert env.w.db.meta("disabled:sw_a") is None
    assert not env.meta_keys("disabled:*")
    env.step()  # the check runs again and re-finds both keys: new incidents, new ids
    reopened = [r for r in env.rows("sw_a") if r["state"] == "open"]
    assert len(reopened) == 2 and not {r["id"] for r in reopened} & set(first_ids)

    start(only=("sw_a",), disabled=("sw_a",))  # disabled again: exactly the two rows open at this start
    rows = env.rows("sw_a")
    assert [r["state"] for r in rows] == ["disabled"] * 4
    assert (env.w.db.meta("disabled:sw_a") or {}).get("closed") == 2
    assert [(r["id"], r["resolved_block"], r["evidence"]) for r in rows[:2]] == \
        [(r["id"], r["resolved_block"], r["evidence"]) for r in closed]  # never closed twice


def test_disabled_with_no_open_incident_writes_a_zero_marker(tmp_path, plan):
    env = Env(tmp_path)
    env.start_worker(only=("sw_a",))
    env.step()
    before = env.n_outbox()
    env.start_worker(only=("sw_a",), disabled=("sw_a",))
    marker = env.w.db.meta("disabled:sw_a") or {}
    assert (marker.get("closed"), marker.get("block")) == (0, env.w.checkpoint()["block"])
    assert env.n_outbox() == before


def test_disabled_on_a_brand_new_database_anchors_at_start_block_minus_one(tmp_path, plan):
    env = Env(tmp_path)
    env.start_worker(only=("sw_a",), disabled=("sw_a",))
    marker = env.w.db.meta("disabled:sw_a") or {}
    assert (marker.get("block"), marker.get("closed")) == (env.start - 1, 0) and env.n_outbox() == 0


def test_reopened_key_at_the_closure_block_gets_a_new_incident_id(tmp_path, plan):
    """`open_incident()` matches `open` only and a `disabled` row keeps its id: a key re-found at the very block it
    was opened at (no block mined in between) gets the `-2` suffix, never the disabled row."""
    env = Env(tmp_path)
    PLAN["sw_a"] = [finding("sw_a", "k1", "page")]
    env.start_worker(only=("sw_a",))
    env.step()
    (old,) = env.rows("sw_a")
    env.start_worker(only=("sw_a",), disabled=("sw_a",))
    env.start_worker(only=("sw_a",))
    env.step(mine=False)
    rows = env.rows("sw_a")
    assert [r["state"] for r in rows] == ["disabled", "open"]
    assert rows[0]["id"] == old["id"] and rows[1]["id"] == f"{old['id']}-2"


# ---------------------------------------------------------------- V1.3 the other checks are unaffected


def test_enabled_check_realerts_and_resolves_beside_a_disabled_one(tmp_path, plan):
    env = Env(tmp_path)
    PLAN["sw_a"] = [finding("sw_a", "ka", "page")]
    PLAN["sw_b"] = [finding("sw_b", "kb", "page")]
    env.start_worker(only=("sw_a", "sw_b"))
    env.step()
    env.start_worker(only=("sw_a", "sw_b"), disabled=("sw_a",))
    assert list(env.w.checks) == ["sw_b"]  # B stays built: the D8 filter must not drop its re-alert
    for _ in range(7):
        env.step(3600)
    realerts = outbox(env.w, "kind='realert'")
    assert [r["check_id"] for r in realerts] == ["sw_b"]
    PLAN["sw_b"] = [finding("sw_b", "kb", "page", resolve=True)]
    env.step()
    assert [r["check_id"] for r in outbox(env.w, "kind='resolved'")] == ["sw_b"]
    assert [r["state"] for r in env.rows("sw_a")] == ["disabled"]
    assert [r["state"] for r in env.rows("sw_b")] == ["resolved"]


def test_build_order_unchanged_and_disabled_ids(plan):
    from checks.registry import disabled
    from eb_reconcile import load_config

    base = load_config(str(Path(__file__).resolve().parent.parent / "config.example.json")).raw
    settings = {**base, "checks": {**base.get("checks", {}), "sw_b": {"enabled": False}, "sw_c": {}}}
    assert [c.id for c in registry.build(settings)] == [c for c in registry.REGISTRY if c != "sw_b"]
    assert [c.id for c in registry.build(base)] == list(registry.REGISTRY)
    assert disabled(settings) == ["sw_b"] and disabled(base) == [] and disabled({}) == []
    assert disabled({"checks": {"sw_c": {"enabled": False}, "sw_a": {"enabled": False}}}) == ["sw_a", "sw_c"]
    assert disabled({"checks": {"sw_a": {"enabled": True}}}) == []
    # only= never makes a check disabled, and does not hide one the config switches off
    assert [c.id for c in registry.build(settings, only=["sw_b", "sw_c"])] == ["sw_c"]


def test_built_check_with_the_same_id_is_never_disabled(tmp_path, plan):
    """A stub instance built under a registered id that the config switches off runs: it is not disabled."""
    env = Env(tmp_path)
    stub = Stub(check_id="sw_a")
    env.start_worker(stubs=[stub], disabled=("sw_a",))
    assert env.w.disabled == [] and env.w.db.meta("disabled:sw_a") is None


def test_example_config_with_a_disabled_check_lists_it_in_status(tmp_path):
    from checks.registry import disabled
    from eb_reconcile import load_config

    example = Path(__file__).resolve().parent.parent / "config.example.json"
    assert disabled(load_config(str(example)).raw) == []
    raw = json.loads(example.read_text())
    raw["db_path"] = str(tmp_path / "w.sqlite")
    raw.setdefault("checks", {}).setdefault("m7", {})["enabled"] = False
    path = tmp_path / "config.json"
    path.write_text(json.dumps(raw))
    cfg = load_config(str(path))
    w = Worker(cfg, None, DB(cfg.db_path))
    assert "m7" not in w.checks and list(w.checks) == [c for c in registry.REGISTRY if c != "m7"]
    assert w.status()["disabled"] == ["m7"]


# ---------------------------------------------------------------- V1.4 reorg while disabled (D11)


def _record_fork(env: Env):
    """sw_a sees one event above a recorded block; returns (fork point, ancestor the rewind will find)."""
    env.step()
    fork = env.chain.number
    env.chain.emit_event("NetworkFeeUpdated", [], u256(1) + u256(2))
    env.step()
    env.step()
    ancestor = env.w.db.conn.execute("SELECT MAX(number) FROM blocks WHERE number<?", (fork,)).fetchone()[0]
    return fork, ancestor


@pytest.mark.parametrize("depth", [1, 4])
def test_reorg_while_disabled_rewinds_the_disabled_cursor(tmp_path, plan, depth):
    env = Env(tmp_path)
    env.start_worker(only=("sw_a",))
    for _ in range(depth):
        env.step()
    fork, ancestor = _record_fork(env)
    above = env.w.db.meta("cursor:events:sw_a")[0]
    assert above > ancestor
    y = Stub(check_id="y", schedule=OnEvents({"NetworkFeeUpdated"}))  # built and unregistered: as at base
    env.start_worker(stubs=[y], disabled=("sw_a", "sw_c"))
    env.chain.reorg(fork)
    env.step()
    # behavioural assertion first (red on the base revision: the disabled check's cursor stays above the ancestor)
    assert env.w.db.meta("cursor:events:sw_a") == [ancestor, END_OF_BLOCK]
    assert env.w.db.meta("reorg:sw_a") == ancestor
    assert y.runs and "reorg" in y.runs[-1]["reasons"] and env.w.db.meta("reorg:y") is None  # y's run committed
    # no row is created for an id without state (assert on the table: _cursor() returns a default)
    assert env.meta_keys("cursor:events:sw_[bc]") == {} and env.meta_keys("reorg:sw_[bc]") == {}
    assert env.meta_keys("*sw_b*") == {} and list(env.meta_keys("*sw_c*")) == ["disabled:sw_c"]
    # not reported by status/degraded while disabled (they iterate the built checks)
    assert "sw_a" not in env.w.status()["reorg_pending"] and not env.w.degraded()


def test_two_reorgs_while_disabled_keep_the_lowest_ancestor(tmp_path, plan):
    env = Env(tmp_path)
    env.start_worker(only=("sw_a",))
    for _ in range(3):
        env.step()
    fork, ancestor = _record_fork(env)
    env.start_worker(only=(), disabled=("sw_a",))
    env.chain.reorg(fork)
    env.step()
    for _ in range(3):
        env.step()
    fork2 = env.chain.number - 1
    env.chain.reorg(fork2)
    env.step()
    assert env.w.db.meta("reorg:sw_a") == ancestor  # min of both ancestors
    assert env.w.db.meta("cursor:events:sw_a") == [ancestor, END_OF_BLOCK]


def test_reenabled_after_a_reorg_while_disabled_replays_the_new_chain(tmp_path, plan):
    """D11: the pending marker is replayed by `Worker.__init__`: the first run after the re-enable carries reason
    `reorg` and `ctx.reorg_ancestor`, sees the new chain's event and not the orphaned one."""
    env = Env(tmp_path)
    env.start_worker(only=("sw_a",))
    env.step()
    fork, ancestor = _record_fork(env)
    env.start_worker(only=(), disabled=("sw_a",))
    env.chain.reorg(fork)
    env.chain.emit_event("NetworkFeeUpdated", [], u256(7) + u256(8))  # the new chain's event
    env.step()
    env.step()
    w = env.start_worker(only=("sw_a",))
    check = w.checks["sw_a"]
    env.step()
    assert check.runs and "reorg" in check.runs[0]["reasons"] and check.ancestors[0] == ancestor
    assert check.runs[0]["events"] == ["NetworkFeeUpdated"]  # one event: the new chain's, not the orphaned one
    assert w.db.meta("reorg:sw_a") is None and w.db.meta("cursor:events:sw_a")[0] == w.checkpoint()["block"]


def test_reenabled_check_never_touches_its_disabled_rows(tmp_path, plan):
    """The framework readers select `open` only: after a reorg while disabled, the re-enabled check's reorg run
    (`reorg_keys`, the re-find rule) and `resolve_on_absence` leave the `disabled` rows as they are, and no
    resolution is sent for them."""
    env = Env(tmp_path)
    PLAN["sw_a"] = [finding("sw_a", "k1", "page")]
    env.start_worker(only=("sw_a",))
    env.step()
    fork, ancestor = _record_fork(env)  # k1 last seen above the fork
    env.start_worker(only=(), disabled=("sw_a",))
    closed = env.rows("sw_a")
    assert [r["state"] for r in closed] == ["disabled"] and closed[0]["last_seen_block"] > ancestor
    env.chain.reorg(fork)
    env.step()
    PLAN["sw_a"] = []
    w = env.start_worker(only=("sw_a",))
    w.checks["sw_a"].resolve_on_absence = True
    env.step()
    assert "reorg" in w.checks["sw_a"].runs[0]["reasons"] and w.db.meta("reorg:sw_a") is None
    assert env.rows("sw_a") == closed
    assert outbox(env.w, "kind='resolved'") == []


# ---------------------------------------------------------------- V1.5 atomicity, restart, read-only status


def test_failed_closure_commits_nothing_and_the_next_start_closes_once(tmp_path, plan, monkeypatch):
    env = Env(tmp_path)
    open_four(env)
    env.step()  # deliver the queued info
    before = env.n_outbox()
    real = DB.set_meta
    calls = {"n": 0}

    def flaky(self, key, value):
        if key.startswith("disabled:") and calls["n"] == 0:
            calls["n"] += 1
            raise OSError("disk full")  # after the incident UPDATE, before the marker write
        return real(self, key, value)

    monkeypatch.setattr(DB, "set_meta", flaky)
    with pytest.raises(OSError):
        env.start_worker(only=("sw_a",), disabled=("sw_a",))
    assert calls["n"] == 1
    assert [r["state"] for r in env.rows("sw_a")] == ["open"] * 4 and env.w.db.meta("disabled:sw_a") is None
    env.start_worker(only=("sw_a",), disabled=("sw_a",))
    assert [r["state"] for r in env.rows("sw_a")] == ["disabled"] * 4
    assert env.w.db.meta("disabled:sw_a")["closed"] == 4 and env.n_outbox() == before
    env.start_worker(only=("sw_a",), disabled=("sw_a",))
    assert env.w.db.meta("disabled:sw_a")["closed"] == 4 and env.n_outbox() == before


def test_rpc_outage_at_the_disabling_start_closes_later_and_never_realerts(tmp_path, plan):
    """Restart between disable and closure: bootstrap needs the RPC; until it succeeds the rows stay open, the step
    returns before housekeeping, and the re-alert filter (D8) would skip them anyway."""
    env = Env(tmp_path)
    open_four(env)
    env.step()
    env.start_worker(only=("sw_a",), disabled=("sw_a",), bootstrap=False)
    env.chain.down = True
    env.clock.advance(7 * 3600)
    env.w.step()
    env.w.housekeeping()  # D8 defence in depth: even if housekeeping ran before the closure
    assert outbox(env.w, "kind='realert'") == []
    assert not env.w.bootstrapped and [r["state"] for r in env.rows("sw_a")] == ["open"] * 4
    env.chain.down = False
    env.w.step()
    assert env.w.bootstrapped and [r["state"] for r in env.rows("sw_a")] == ["disabled"] * 4
    assert outbox(env.w, "kind IN ('realert', 'resolved')") == []


def _cli_config(env: Env, disabled=()) -> str:
    raw = json.loads((Path(__file__).resolve().parent.parent / "config.example.json").read_text())
    raw.update(db_path=str(env.tmp_path / "w.sqlite"))
    raw.setdefault("checks", {}).update({cid: {"enabled": False} for cid in disabled})
    path = env.tmp_path / "config.json"
    path.write_text(json.dumps(raw))
    return str(path)


def test_readonly_status_lists_the_disabled_check_and_closes_nothing(tmp_path, plan, capsys):
    import eb_reconcile

    env = Env(tmp_path)
    open_four(env)
    before = env.n_outbox()
    assert eb_reconcile.main(["status", "--config", _cli_config(env, disabled=("sw_a",))]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["disabled"] == ["sw_a"]
    assert [r["state"] for r in env.rows("sw_a")] == ["open"] * 4 and env.w.db.meta("disabled:sw_a") is None
    assert len([r for r in out["open"] if r["check_id"] == "sw_a"]) == 4 and env.n_outbox() == before


def test_status_and_export_after_the_closure(tmp_path, plan, capsys):
    import eb_reconcile

    env = Env(tmp_path)
    open_four(env)
    env.start_worker(only=("sw_a",), disabled=("sw_a",))
    cfg = _cli_config(env, disabled=("sw_a",))
    assert eb_reconcile.main(["status", "--config", cfg]) == 0
    st = json.loads(capsys.readouterr().out)
    assert not [r for r in st["open"] if r["check_id"] == "sw_a"]
    assert not any(k.startswith("sw_a/") for k in st["open_incidents"]) and st["disabled"] == ["sw_a"]
    assert eb_reconcile.main(["export", "--config", cfg]) == 0
    exported = [r for r in json.loads(capsys.readouterr().out)["incidents"] if r["check_id"] == "sw_a"]
    assert [r["state"] for r in exported] == ["disabled"] * 4


def test_metrics_open_count_excludes_disabled_rows(tmp_path, plan):
    from metrics import render

    env = Env(tmp_path)
    open_four(env)
    env.start_worker(only=("sw_a",), disabled=("sw_a",))
    open_lines = [ln for ln in render(env.w.db.conn, []).splitlines() if ln.startswith("ssv_watch_open_incidents")]
    assert open_lines and not [ln for ln in open_lines if 'check="sw_a"' in ln]


# ---------------------------------------------------------------- adversarial sweep (DEFECT-CLASSES)


@pytest.mark.parametrize("seed", range(30))
def test_seeded_switch_sweep(tmp_path, plan, seed):
    """Random restarts (enable/disable, backfill on/off), steps across the re-alert cadence, reorgs of depth 1-4,
    findings opening/escalating/resolving, an RPC outage at a start. Invariants after every action:
    - a disabled period creates no outbox row of the disabled check (no re-alert, no resolution, no notice);
    - while disabled: no `open` row of the check, the marker exists; while enabled: no marker;
    - a `disabled` row never changes again; at most one open row per key (the index)."""
    rnd = random.Random(seed)
    env = Env(tmp_path, confirmations=rnd.choice([0, 2]))
    keys = ["k1", "k2", "k3"]
    sev = ["info", "warn", "page"]
    PLAN["sw_a"] = []
    disabled = False
    env.start_worker(only=("sw_a", "sw_b"))
    frozen: dict[str, dict] = {}
    for _ in range(25):
        act = rnd.choice(["step", "step", "long", "finding", "restart", "reorg", "outage"])
        n_before = env.w.db.conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM outbox").fetchone()[0]
        if act == "step":
            env.step(rnd.choice([60, 600]))
        elif act == "long":
            env.step(7 * 3600)
        elif act == "finding":
            k = rnd.choice(keys)
            PLAN["sw_a"] = [f for f in PLAN["sw_a"] if f.key != k]
            if rnd.random() < 0.7:
                PLAN["sw_a"].append(finding("sw_a", k, rnd.choice(sev), notify=rnd.random() < 0.8))
            else:
                PLAN["sw_a"].append(finding("sw_a", k, "page", resolve=True))
            env.step()
        elif act == "restart" or act == "outage":
            disabled = rnd.random() < 0.5
            env.start_worker(only=("sw_a", "sw_b"), disabled=("sw_a",) if disabled else (),
                             backfill=rnd.random() < 0.5, bootstrap=act == "restart")
            if act == "outage":
                env.chain.down = True
                env.clock.advance(7 * 3600)
                env.w.step()
                env.chain.down = False
                env.w.step()
        elif act == "reorg" and env.chain.number - env.start > 6:
            env.chain.reorg(env.chain.number - rnd.randint(1, 4))
            env.step()
        if disabled:
            new = outbox(env.w, f"rowid > {n_before}")
            assert not [r for r in new if r["check_id"] == "sw_a"], (seed, act, new)
            assert not [r for r in env.rows("sw_a") if r["state"] == "open"], (seed, act)
            assert env.w.db.meta("disabled:sw_a") is not None
        else:
            assert env.w.db.meta("disabled:sw_a") is None
        for r in env.rows("sw_a"):
            if r["state"] == "disabled":
                assert frozen.setdefault(r["id"], r) == r, (seed, act, r["id"])
        dup = env.w.db.conn.execute(
            "SELECT ikey, COUNT(*) FROM incidents WHERE state='open' GROUP BY ikey HAVING COUNT(*) > 1").fetchall()
        assert not dup
