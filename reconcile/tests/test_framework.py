"""Framework tests (plan §7 V1B.3–V1B.8): scheduler, incidents and restart idempotency, routing,
inbox, metrics, parameters, inventory, schema migration. Stub checks stand in for wave-2 checks.
"""

from __future__ import annotations

import json
import sqlite3
import urllib.request

import pytest
from fakechain import PROXY, FakeChain, FakeClock, FakeRpc, MiniSSV, keys

import checks as registry
from alerts import incident_key
from checks.base import Check, Every, Finding, NearSetEveryBlock, OnEvents, OnInbox
from eb_reconcile import DB, DEFAULTS, Config, Worker
from eb_storage import EB_LATEST_COMMITTED, cluster_id, eth_cluster_slot, keccak, u256
from inbox import ERRORS, decode_revert
from notify import RecordOnly, Router

VIEWS = "0xafe830b6ee262ba11cce5f32fdcd760ffe6a66e4"
OWNER = "0x" + "aa" * 20
OPS = (11, 12, 13, 14)


class Capture:
    def __init__(self):
        self.sent: list[tuple[str, str, str, str]] = []

    def send(self, severity, text, key, route=None):
        self.sent.append((severity, key, text, route))
        return "captured"


class Stub(Check):
    """A configurable stand-in for a wave-2 check: records each run and returns `emit(ctx, block)`."""

    id = "stub"
    schedule = (Every(60), OnEvents({"NetworkFeeUpdated"}))

    def __init__(self, emit=None, check_id=None, schedule=None, **attrs):
        super().__init__({}, {})
        if check_id:
            self.id = check_id
        if schedule is not None:
            self.schedule = schedule
        for k, v in attrs.items():
            setattr(self, k, v)
        self.emit = emit or (lambda ctx, block: [])
        self.runs: list[dict] = []
        self.prepared: list[dict] = []

    def prepare(self, ctx):
        self.prepared.append(dict(ctx.params.values) if ctx.params.enabled else {})

    def run(self, ctx, block):
        self.runs.append({"reasons": set(ctx.reasons), "events": [e.kind for e in ctx.events()],
                          "inbox": [e.tx_hash for e in ctx.inbox], "block": block})
        return self.emit(ctx, block)


def fwmake(tmp_path, stubs, chain=None, clock=None, notifier=None, **over):
    chain = chain or FakeChain()
    raw = {
        "chain_id": 1, "proxy": PROXY, "activation_block": chain.sealed, "start_block": chain.sealed,
        "rpc_url_env": "UNUSED", "confirmations": 0, "max_log_range": 10_000, "db_path": str(tmp_path / "w.sqlite"),
        "alerts": {"mode": "record", "label": "test", "digest_interval_s": 0},
        "inventory": {"api_url": None},
    }
    raw.update(over)
    cfg = Config({**DEFAULTS, **raw})
    w = Worker(cfg, FakeRpc(chain), DB(cfg.db_path), notifier or Capture(), clock=clock or FakeClock(),
               checks=stubs)
    w.bootstrap()
    return chain, w


def advance(w):
    w.check_reorg()
    w.scan()
    w.poll_inbox()
    w.run_checks()
    w.housekeeping()
    w.deliver()


def finding(check="stub", key="k1", severity="page", **kw):
    return Finding(check=check, key=key, severity=severity, title=kw.pop("title", f"{check} {key}"),
                   body=kw.pop("body", "body"), **kw)


def outbox(w, where="1=1"):
    return [dict(r) for r in w.db.conn.execute(f"SELECT * FROM outbox WHERE {where} ORDER BY created_at, rowid")]


# ---------------------------------------------------------------- V1B.3 scheduler


@pytest.fixture
def registered_stub():
    @registry.register
    class Registered(Stub):
        id = "stub_registered"
        defaults = {"threshold": 5}

        def __init__(self, config, settings):
            Stub.__init__(self)
            self.config, self.settings = config, settings

    try:
        yield Registered
    finally:
        registry.REGISTRY.pop("stub_registered")


def test_scheduler_dispatches_registered_check(tmp_path, registered_stub):
    clock = FakeClock()
    chain = FakeChain()
    chain.mine()
    _, w = fwmake(tmp_path, None, chain=chain, clock=clock,
                  checks={"m5": {"enabled": False}, "stub_registered": {"threshold": 7}})
    stub = w.checks["stub_registered"]
    assert list(w.checks) == ["stub_registered"] and stub.config == {"threshold": 7}

    advance(w)  # first run: the interval has never elapsed
    assert len(stub.runs) == 1 and stub.runs[0]["reasons"] == {"interval"}

    clock.advance(30)
    chain.mine()
    advance(w)
    assert len(stub.runs) == 1  # interval not elapsed, no event

    clock.advance(30)
    advance(w)
    assert len(stub.runs) == 2 and stub.runs[1]["reasons"] == {"interval"}

    # one matching event -> exactly one run, which sees the event; an unrelated event does not trigger
    chain.emit_event("NetworkFeeUpdated", [], u256(1) + u256(2))
    chain.mine()
    advance(w)
    assert len(stub.runs) == 3 and stub.runs[2]["reasons"] == {"events"}
    assert stub.runs[2]["events"] == ["NetworkFeeUpdated"]
    advance(w)
    assert len(stub.runs) == 3
    chain.emit_event("QuorumUpdated", [], u256(7_500))
    chain.mine()
    advance(w)
    assert len(stub.runs) == 3

    clock.advance(60)
    advance(w)
    assert len(stub.runs) == 4 and stub.runs[3]["reasons"] == {"interval"} and stub.runs[3]["events"] == []


def test_scheduler_near_set_runs_every_new_block(tmp_path):
    clock = FakeClock()
    stub = Stub(schedule=(Every(3600), NearSetEveryBlock()))
    chain = FakeChain()
    chain.mine()
    _, w = fwmake(tmp_path, [stub], chain=chain, clock=clock)
    advance(w)
    assert len(stub.runs) == 1
    chain.mine()
    advance(w)
    assert len(stub.runs) == 1  # empty near set: interval only
    stub.near_set = frozenset({"0xcluster"})
    chain.mine()
    advance(w)
    advance(w)  # same block: no second run
    chain.mine()
    advance(w)
    assert [r["reasons"] for r in stub.runs[1:]] == [{"block"}, {"block"}]


def test_scheduler_isolates_a_failing_check(tmp_path):
    def boom(ctx, block):
        raise ZeroDivisionError("bad math")

    bad = Stub(emit=boom, check_id="bad")
    good = Stub(emit=lambda ctx, b: [finding(check="good")], check_id="good")
    chain = FakeChain()
    chain.mine()
    _, w = fwmake(tmp_path, [bad, good], chain=chain)
    w.step()
    assert len(good.runs) == 1 and w.alerts.open_incident("good", "k1") is not None
    assert any(r["key"].startswith("health:check-error") for r in outbox(w))
    assert w.db.meta("run:bad")["errors"] == 1 and w.db.meta("cursor:events:bad") is None
    assert w.rpc_failures == 0


def test_config_rejects_unknown_check_ids_and_keys(tmp_path, registered_stub):
    with pytest.raises(SystemExit, match="unknown check id"):
        fwmake(tmp_path, None, checks={"m99": {}})
    with pytest.raises(SystemExit, match="unknown key"):
        fwmake(tmp_path, None, checks={"stub_registered": {"treshold": 1}})


def test_finding_validates_severity_and_json():
    with pytest.raises(ValueError):
        Finding(check="x", key="k", severity="critical", title="t", body="b")
    with pytest.raises(TypeError):
        Finding(check="x", key="k", severity="page", title="t", body="b", evidence={"n": object()})


# ---------------------------------------------------------------- V1B.4 incidents, restart idempotency


def test_restart_idempotent_single_outbox_row(tmp_path):
    chain = FakeChain()
    chain.mine()
    clock = FakeClock()
    emit = lambda ctx, b: [finding(key="0xc1", severity="page")]  # noqa: E731
    _, w = fwmake(tmp_path, [Stub(emit=emit)], chain=chain, clock=clock)
    w.step(force=True)
    assert len(outbox(w)) == 1

    # simulated restart: a new process on the same database re-finds the same open incident
    clock.advance(600)
    chain.mine()
    _, w2 = fwmake(tmp_path, [Stub(emit=emit)], chain=chain, clock=clock, start_block=w.cfg.start_block,
                   activation_block=w.cfg.activation_block)
    w2.step(force=True)
    w2.step(force=True)
    rows = outbox(w2)
    assert len(rows) == 1 and rows[0]["incident_id"] and rows[0]["kind"] == "open"
    assert w2.db.conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 1


def test_restart_idempotent_two_checks_same_key(tmp_path):
    """Two checks reporting the same key keep two incidents (the incident key joins the check id)."""
    chain = FakeChain()
    chain.mine()
    a = Stub(emit=lambda ctx, b: [finding(check="ca", key="0xsame")], check_id="ca")
    b = Stub(emit=lambda ctx, b: [finding(check="cb", key="0xsame")], check_id="cb")
    _, w = fwmake(tmp_path, [a, b], chain=chain)
    w.step(force=True)
    w.step(force=True)
    inc = w.db.conn.execute("SELECT check_id, ikey FROM incidents ORDER BY check_id").fetchall()
    assert [tuple(r) for r in inc] == [("ca", incident_key("ca", "0xsame")), ("cb", incident_key("cb", "0xsame"))]
    assert sorted(r["check_id"] for r in outbox(w)) == ["ca", "cb"]


def test_incident_escalation_revision_resolution_same_id(tmp_path):
    chain = FakeChain()
    chain.mine()
    script = {"f": [finding(key="w1", severity="info")]}
    _, w = fwmake(tmp_path, [Stub(emit=lambda ctx, b: script["f"])], chain=chain)
    w.step(force=True)
    script["f"] = [finding(key="w1", severity="warn")]
    w.step(force=True)  # escalates
    script["f"] = [finding(key="w1", severity="info")]
    w.step(force=True)  # lower severity: recorded silently
    script["f"] = [finding(key="w1", severity="info", revision="r2")]
    w.step(force=True)  # new revision: re-sent
    script["f"] = [finding(key="w1", severity="info", revision="r2")]
    w.step(force=True)  # same revision: nothing
    script["f"] = [finding(key="w1", severity="info", resolve=True)]
    w.step(force=True)
    rows = outbox(w)
    assert [r["kind"] for r in rows] == ["open", "escalate", "update"]
    assert len({r["incident_id"] for r in rows}) == 1
    inc = w.db.conn.execute("SELECT * FROM incidents").fetchone()
    assert inc["state"] == "resolved" and inc["severity"] == "info"
    # an info incident is not resolved loudly; a warn/page one is (as info, on its route)
    script["f"] = [finding(key="p1", severity="page")]
    w.step(force=True)
    script["f"] = [finding(key="p1", severity="page", resolve=True)]
    w.step(force=True)
    res = outbox(w, "kind='resolved'")
    assert len(res) == 1 and res[0]["severity"] == "info" and res[0]["delivery"] == "telegram"


def test_silent_findings_and_absence(tmp_path):
    chain = FakeChain()
    chain.mine()
    script = {"f": [finding(key="a", severity="info", notify=False), finding(key="b", severity="warn")]}
    stub = Stub(emit=lambda ctx, b: script["f"], resolve_on_absence=True)
    _, w = fwmake(tmp_path, [stub], chain=chain)
    w.step(force=True)
    assert [r["kind"] for r in outbox(w)] == ["open"]  # only b is sent
    script["f"] = [finding(key="a", severity="info", notify=False)]
    w.step(force=True)
    assert w.alerts.open_incident("stub", "b") is None
    assert [r["kind"] for r in outbox(w)] == ["open", "resolved"]


def test_absence_never_resolves_by_default(tmp_path):
    chain = FakeChain()
    chain.mine()
    script = {"f": [finding(key="b", severity="warn")]}
    _, w = fwmake(tmp_path, [Stub(emit=lambda ctx, b: script["f"])], chain=chain)
    w.step(force=True)
    script["f"] = []
    w.step(force=True)
    assert w.alerts.open_incident("stub", "b") is not None


def test_realert_cadence_page_6h_warn_24h_info_once(tmp_path):
    chain = FakeChain()
    chain.mine()
    clock = FakeClock()
    emit = lambda ctx, b: [finding(key="p", severity="page"), finding(key="w", severity="warn"),  # noqa: E731
                           finding(key="i", severity="info")]
    _, w = fwmake(tmp_path, [Stub(emit=emit)], chain=chain, clock=clock)
    w.step(force=True)
    assert len(outbox(w)) == 3
    clock.advance(6 * 3600 - 1)
    w.step(force=True)
    assert len(outbox(w, "kind='realert'")) == 0
    clock.advance(1)
    w.step(force=True)
    assert [r["severity"] for r in outbox(w, "kind='realert'")] == ["page"]
    clock.advance(18 * 3600)
    w.step(force=True)
    assert sorted(r["severity"] for r in outbox(w, "kind='realert'")) == ["page", "page", "warn"]
    assert not outbox(w, "kind='realert' AND severity='info'")


# ---------------------------------------------------------------- V1B.5 routing


def test_routing_page_telegram_info_discord(tmp_path):
    chain = FakeChain()
    chain.mine()
    emit = lambda ctx, b: [finding(key="p", severity="page"), finding(key="w", severity="warn"),  # noqa: E731
                           finding(key="i", severity="info")]
    _, w = fwmake(tmp_path, [Stub(emit=emit)], chain=chain,
                  alerts={"mode": "record", "label": "test", "digest_interval_s": 1})
    w.step(force=True)
    with w.db.tx():
        w.alerts.health("lag", "behind", dedup="lag")
    route = {r["severity"]: r["delivery"] for r in outbox(w)}
    assert route == {"page": "telegram", "warn": "telegram", "info": "discord", "digest": "discord",
                     "health": "telegram"}
    assert {r[3] for r in w.notifier.sent} == {"telegram", "discord"}


def test_routing_record_mode_writes_json_lines(tmp_path):
    chain = FakeChain()
    chain.mine()
    path = tmp_path / "alerts.jsonl"
    emit = lambda ctx, b: [finding(key="p", severity="page"), finding(key="i", severity="info")]  # noqa: E731
    _, w = fwmake(tmp_path, [Stub(emit=emit)], chain=chain, notifier=RecordOnly(str(path)))
    w.step(force=True)
    lines = [json.loads(x) for x in path.read_text().splitlines()]
    assert [(x["severity"], x["route"]) for x in lines] == [("page", "telegram"), ("info", "discord")]
    assert all(r["receipt"] == "recorded" and r["sent_at"] for r in outbox(w))


def test_routing_live_router_sends_each_route_to_its_transport():
    tg, dc = Capture(), Capture()
    r = Router({"telegram": tg, "discord": dc})
    assert r.send("page", "t", "k1", "telegram") == "captured" and r.send("info", "t", "k2", "discord")
    assert [x[1] for x in tg.sent] == ["k1"] and [x[1] for x in dc.sent] == ["k2"]
    with pytest.raises(RuntimeError):
        Router({"telegram": tg}).send("info", "t", "k3", "discord")


def test_routing_live_mode_requires_secrets(monkeypatch):
    from notify import make_notifier

    for v in ("T_TOK", "T_CHAT", "T_HOOK"):
        monkeypatch.delenv(v, raising=False)
    cfg = {"mode": "live", "telegram_token_env": "T_TOK", "telegram_chat_id_env": "T_CHAT",
           "discord_webhook_env": "T_HOOK"}
    with pytest.raises(SystemExit):
        make_notifier(cfg)
    monkeypatch.setenv("T_TOK", "1:abc")
    monkeypatch.setenv("T_CHAT", "42")
    with pytest.raises(SystemExit):
        make_notifier(cfg)
    monkeypatch.setenv("T_HOOK", "https://discord.invalid/hook")
    assert set(make_notifier(cfg).transports) == {"telegram", "discord"}


def test_discord_errors_never_contain_the_webhook(monkeypatch):
    import urllib.request as ur

    from notify import Discord

    def boom(*a, **k):
        raise ValueError("bad url https://discord.com/api/webhooks/1/SECRET")

    monkeypatch.setattr(ur, "urlopen", boom)
    with pytest.raises(RuntimeError) as exc:
        Discord("https://discord.com/api/webhooks/1/SECRET").send("info", "x", "k", "discord")
    assert "SECRET" not in str(exc.value)


# ---------------------------------------------------------------- V1B.7 inbox

TX = "0x" + "ab" * 32
BH = "0x" + "cd" * 32


def inbox_line(tx=TX, block=1005, block_hash=BH, **kw):
    d = {"schema": 1, "tx_hash": tx, "block": block, "block_hash": block_hash, "from": "0x" + "11" * 20,
         "to": PROXY, "selector": "0xbc26e7e5",
         "function": "liquidate(address,uint64[],(uint32,uint64,uint64,bool,uint256))", "status": "failure",
         "monitor": "SSV Failed Tx Inbox", "observed_at": "2026-10-21T00:00:00Z"}
    d.update(kw)
    return json.dumps(d) + "\n"


def inbox_setup(tmp_path, checks=(), **over):
    chain = FakeChain()
    path = tmp_path / "failed-tx.jsonl"
    chain.mine()
    _, w = fwmake(tmp_path, list(checks), chain=chain, inbox={"path": str(path)}, **over)
    return chain, w, path


def test_inbox_parses_fixture_persists_offset_rereads_on_shrink(tmp_path):
    chain, w, path = inbox_setup(tmp_path)
    tx = chain.add_tx("0x" + "11" * 20, PROXY, "0xbc26e7e5" + "00" * 64)
    chain.stub_call(PROXY, "0xbc26e7e5", revert="0x12e04c87")
    path.write_text(inbox_line(tx=tx) + '{"schema":1,"tx_hash":"0xpartial')  # trailing partial line waits
    w.poll_inbox()
    rows = w.db.conn.execute("SELECT * FROM inbox").fetchall()
    assert len(rows) == 1 and rows[0]["tx_hash"] == tx and rows[0]["block"] == 1005 and rows[0]["resolved"] == 1
    offset = w.db.meta("inbox:offset")["offset"]
    assert offset == len(inbox_line(tx=tx).encode())

    # a restart resumes from the persisted offset: nothing is re-ingested
    _, w2 = fwmake(tmp_path, [], chain=chain, inbox={"path": str(path)}, start_block=w.cfg.start_block,
                   activation_block=w.cfg.activation_block)
    w2.poll_inbox()
    assert w2.db.conn.execute("SELECT COUNT(*) FROM inbox").fetchone()[0] == 1

    # the same transaction twice in one file is one entry (dedup on tx_hash, block_hash)
    with open(path, "a") as fh:
        fh.write('","block":1}\n' + inbox_line(tx=tx))
    w2.poll_inbox()
    assert w2.db.conn.execute("SELECT COUNT(*) FROM inbox").fetchone()[0] == 1
    assert any(r["key"].startswith("health:inbox-malformed") for r in outbox(w2))

    # rotation: the file shrinks -> re-read from 0; a new transaction is ingested, old ones stay deduplicated
    tx2 = "0x" + "ef" * 32
    path.write_text(inbox_line(tx=tx2, block=1006, block_hash="0x" + "99" * 32))
    w2.poll_inbox()
    assert [r["tx_hash"] for r in w2.db.conn.execute("SELECT tx_hash FROM inbox ORDER BY id")] == [tx, tx2]
    assert w2.db.meta("inbox:offset")["offset"] == path.stat().st_size


def test_inbox_revert_selector_decodes_incorrect_cluster_state(tmp_path):
    chain, w, path = inbox_setup(tmp_path)
    tx = chain.add_tx("0x" + "11" * 20, PROXY, "0xbc26e7e5" + "00" * 64)
    chain.stub_call(PROXY, "0xbc26e7e5", revert="0x12e04c87")
    chain.mine()
    path.write_text(inbox_line(tx=tx, block=chain.number))
    w.poll_inbox()
    r = w.db.conn.execute("SELECT * FROM inbox").fetchone()
    assert (r["revert_selector"], r["revert_name"], r["revert_source"]) == ("0x12e04c87", "IncorrectClusterState",
                                                                            "eth_call")
    # replayed at block - 1 with the transaction's own calldata
    assert (PROXY, "0xbc26e7e5" + "00" * 64, chain.number - 1) in chain.calls
    assert decode_revert("0x12e04c87") == ("0x12e04c87", "IncorrectClusterState")


def test_inbox_error_constants_match_keccak():
    assert len(ERRORS) >= 70
    for sel, sig in ERRORS.items():
        assert sel == "0x" + keccak(sig.encode())[:4].hex(), sig
    assert decode_revert("0x08c379a0" + (u256(32) + u256(4) + b"nope".ljust(32, b"\0")).hex()) == (
        "0x08c379a0", "Error: nope")
    assert decode_revert("0xdeadbeef") == ("0xdeadbeef", None)


def test_inbox_blockscout_fallback_and_unresolved(tmp_path):
    chain = FakeChain()
    chain.mine()
    path = tmp_path / "failed-tx.jsonl"
    seen = []

    def http_get(url):
        seen.append(url)
        return {"revert_reason": {"raw": "0x95a0cf33"}}

    _, w = fwmake(tmp_path, [], chain=chain, inbox={"path": str(path), "blockscout": "https://bs.invalid"})
    w.inbox.http_get = http_get
    tx = chain.add_tx("0x" + "11" * 20, PROXY, "0x12345678")
    chain.stub_call(PROXY, "0x12345678", result=b"")  # does not revert at block - 1 (state changed in-block)
    chain.mine()
    path.write_text(inbox_line(tx=tx, block=chain.number))
    w.poll_inbox()
    r = w.db.conn.execute("SELECT * FROM inbox").fetchone()
    assert (r["revert_name"], r["revert_source"]) == ("ClusterIsLiquidated", "blockscout")
    assert seen == [f"https://bs.invalid/api/v2/transactions/{tx}"]


def test_inbox_check_receives_resolved_entries_once(tmp_path):
    stub = Stub(schedule=OnInbox())
    chain, w, path = inbox_setup(tmp_path, [stub])
    tx = chain.add_tx("0x" + "11" * 20, PROXY, "0xbc26e7e5")
    chain.stub_call(PROXY, "0xbc26e7e5", revert="0x12e04c87")
    chain.mine()
    advance(w)
    assert stub.runs == []  # no entries yet
    path.write_text(inbox_line(tx=tx, block=chain.number))
    advance(w)
    advance(w)
    assert len(stub.runs) == 1 and stub.runs[0]["inbox"] == [tx] and stub.runs[0]["reasons"] == {"inbox"}


def test_inbox_transport_errors_keep_entries_pending(tmp_path):
    stub = Stub(schedule=OnInbox())
    chain, w, path = inbox_setup(tmp_path, [stub])
    tx = chain.add_tx("0x" + "11" * 20, PROXY, "0xbc26e7e5")
    path.write_text(inbox_line(tx=tx, block=chain.number))
    w.check_reorg()
    w.scan()
    chain.down = True
    with w.db.tx():
        w.inbox.read()
        w.inbox.resolve(w.rpc)
    chain.down = False
    assert tuple(w.db.conn.execute("SELECT resolved, attempts FROM inbox").fetchone()) == (0, 1)
    assert w.inbox.pending(0) == []


# ---------------------------------------------------------------- V1B.8 metrics


def test_metrics_endpoint_exposes_required_series(tmp_path):
    from metrics import MetricsServer

    chain = FakeChain()
    chain.mine()
    _, w = fwmake(tmp_path, [Stub(emit=lambda ctx, b: [finding(key="p", severity="page")])], chain=chain)
    w.step(force=True)
    srv = MetricsServer(w.cfg.db_path, list(w.checks), "127.0.0.1", 0).start()
    try:
        body = urllib.request.urlopen(f"http://127.0.0.1:{srv.port}/metrics", timeout=5).read().decode()
    finally:
        srv.stop()
    for series in ("ssv_watch_checkpoint_block", "ssv_watch_lag_blocks", "ssv_watch_open_incidents",
                   "ssv_watch_outbox_undelivered", 'ssv_watch_check_last_run_seconds{check="stub"}'):
        assert series in body, series
    assert f"ssv_watch_checkpoint_block {chain.number - 1}" in body
    assert 'ssv_watch_open_incidents{check="stub",severity="page"} 1' in body


# ---------------------------------------------------------------- live parameters


def stub_params(chain, **over):
    from params import GET_ORACLE, GETTERS, selector

    values = {"network_fee": 3_366_600_000, "network_fee_ssv": 1, "liquidation_threshold_period": 42_960,
              "liquidation_threshold_period_ssv": 100_240, "minimum_liquidation_collateral": 325_000_000_000_000,
              "minimum_liquidation_collateral_ssv": 1, "operator_fee_increase_limit": 1000,
              "maximum_operator_fee": 1, "minimum_operator_eth_fee": 1, "validators_per_operator_limit": 3000,
              "quorum_bps": 7500, "cooldown_duration": 1}
    values.update(over)
    for name, sig in GETTERS.items():
        if name == "active_oracle_ids":
            chain.stub_call(VIEWS, selector(sig), b"".join(u256(i) for i in (1, 2, 3, 4)))
        else:
            chain.stub_call(VIEWS, selector(sig), u256(values[name]))
    for i in (1, 2, 3, 4):
        chain.stub_call(VIEWS, selector(GET_ORACLE) + u256(i), u256(0xC0 + i))
    chain.write(EB_LATEST_COMMITTED, 0)


def test_params_reload_on_parameter_event_calls_prepare(tmp_path):
    chain = FakeChain()
    stub_params(chain)
    chain.mine()
    stub = Stub(schedule=Every(3600))
    _, w = fwmake(tmp_path, [stub], chain=chain, views=VIEWS)
    advance(w)
    assert stub.prepared[-1]["liquidation_threshold_period"] == 42_960
    assert stub.prepared[-1]["oracles"] == {i: "0x%040x" % (0xC0 + i) for i in (1, 2, 3, 4)}
    assert len(stub.runs) == 1

    # the DAO changes the threshold: the event triggers a reload, prepare and a run without a code change
    stub_params(chain, liquidation_threshold_period=50_000)
    chain.emit_event("LiquidationThresholdPeriodUpdated", [], u256(50_000))
    chain.mine()
    advance(w)
    assert stub.prepared[-1]["liquidation_threshold_period"] == 50_000 and w.params.block == chain.number - 1
    assert len(stub.runs) == 2 and "params" in stub.runs[1]["reasons"]
    advance(w)
    assert len(stub.prepared) == 2  # no further reload without a new event


def test_params_read_failure_is_a_health_message_not_a_crash(tmp_path):
    chain = FakeChain()
    chain.mine()  # no stubs: every getter reverts
    stub = Stub(schedule=Every(60))
    _, w = fwmake(tmp_path, [stub], chain=chain, views=VIEWS)
    w.step()
    assert len(stub.runs) == 1 and any(r["key"].startswith("health:params") for r in outbox(w))


# ---------------------------------------------------------------- inventory


def api_row(owner, ops, count, nfi, index, active, balance, migrated=True):
    return {"clusterId": "0x" + cluster_id(owner, ops).hex(), "ownerAddress": owner, "operators": list(ops),
            "validatorCount": count, "networkFeeIndex": str(nfi), "index": str(index), "active": active,
            "ethBalance": str(balance) if migrated else "0", "balance": "0" if migrated else str(balance),
            "migrated": migrated, "isLiquidated": not active}


def test_inventory_validates_tuples_against_storage(tmp_path):
    chain = FakeChain()
    ssv = MiniSSV(chain)
    owner2, owner3 = "0x" + "bb" * 20, "0x" + "cc" * 20
    seen = []
    stub = Stub(schedule=Every(60), emit=lambda ctx, b: seen.append(ctx.inventory()) or [], check_id="inv")
    _, w = fwmake(tmp_path, [stub], chain=chain, inventory={"api_url": "https://api.invalid/api/v4/mainnet"})
    ssv.register(OWNER, OPS, keys(2, 1))  # logs + storage
    ssv.legacy_ssv_cluster(owner2, OPS, 3)  # storage only (pre-activation legacy cluster)
    chain.mine()
    st2 = ssv.clusters[cluster_id(owner2, OPS)]
    st1 = ssv.clusters[cluster_id(OWNER, OPS)]
    pages = {1: [api_row(OWNER, OPS, 1, 0, 0, True, 1)],  # stale API tuple; the log tuple validates
             2: [api_row(owner2, OPS, 3, st2["nfi"], st2["index"], True, st2["balance"], migrated=False),
                 api_row(owner3, OPS, 1, 0, 0, True, 5)]}  # not in storage
    w.inventory.http_get = lambda url: {"clusters": pages[int(url.rsplit("=", 1)[1])],
                                        "pagination": {"pages": 2}}
    advance(w)
    inv = seen[-1]
    assert inv.block == w.checkpoint()["block"]
    c1 = inv.clusters["0x" + cluster_id(OWNER, OPS).hex()]
    c2 = inv.clusters["0x" + cluster_id(owner2, OPS).hex()]
    c3 = inv.clusters["0x" + cluster_id(owner3, OPS).hex()]
    assert (c1.valid, c1.tuple_source, c1.version, c1.cluster.validator_count) == (True, "logs", "ETH", 2)
    assert c1.cluster.balance == st1["balance"] and c1.sources == {"api", "logs"}
    assert (c2.valid, c2.tuple_source, c2.version, c2.legacy, c2.api_migrated) == (True, "api", "SSV", True, False)
    assert (c3.valid, c3.version) == (False, None) and inv.invalid == []
    assert inv.operators[11] == {"clusters": 2, "validators": 5}
    assert w.inventory.get(inv.block) is inv  # cached per block

    # a cluster whose every candidate tuple is stale is listed as invalid (M1 input, D9)
    chain.write(eth_cluster_slot(cluster_id(OWNER, OPS)), 12345)
    chain.mine()
    advance(w)
    inv2 = w.inventory.get(w.checkpoint()["block"])
    assert [c.cluster_id for c in inv2.invalid] == ["0x" + cluster_id(OWNER, OPS).hex()]


def test_chain_counts_calls_per_run(tmp_path):
    chain = FakeChain()
    chain.mine()
    for a in ("0x" + "01" * 20, "0x" + "02" * 20):
        chain.set_balance(a, 10**18)

    def emit(ctx, block):
        ctx.rpc.balances(["0x" + "01" * 20, "0x" + "02" * 20])
        return []

    _, w = fwmake(tmp_path, [Stub(emit=emit)], chain=chain)
    w.step(force=True)
    assert w.db.meta("run:stub")["rpc_calls"] == {"eth_getBalance": 2}


# ---------------------------------------------------------------- schema migration (pre-framework databases)

LEGACY_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE logs (block INTEGER NOT NULL, log_index INTEGER NOT NULL, tx_index INTEGER NOT NULL,
  tx_hash TEXT NOT NULL, block_hash TEXT NOT NULL, kind TEXT NOT NULL, cluster_id TEXT, owner TEXT, operator_ids TEXT,
  cluster TEXT, pubkey TEXT, shares_len INTEGER, ref_block INTEGER, eb INTEGER, root TEXT, extra TEXT,
  PRIMARY KEY (block, log_index));
CREATE TABLE blocks (number INTEGER PRIMARY KEY, hash TEXT NOT NULL);
CREATE TABLE evaluations (cluster_id TEXT PRIMARY KEY, block INTEGER NOT NULL, status TEXT NOT NULL, ref_block INTEGER,
  reference_vunits INTEGER, observed_vunits INTEGER, billed_vunits INTEGER, discrepancy_vunits INTEGER,
  matched_keys INTEGER, active INTEGER, validator_count INTEGER, reasons TEXT);
CREATE TABLE incidents (id TEXT PRIMARY KEY, cluster_id TEXT NOT NULL, kind TEXT NOT NULL, severity TEXT NOT NULL,
  state TEXT NOT NULL, first_ref_block INTEGER NOT NULL, last_ref_block INTEGER NOT NULL,
  first_seen_block INTEGER NOT NULL, last_seen_block INTEGER NOT NULL, shortfall_vunits INTEGER NOT NULL,
  max_shortfall_vunits INTEGER NOT NULL, resolved_block INTEGER, evidence TEXT NOT NULL, created_at REAL NOT NULL,
  updated_at REAL NOT NULL);
CREATE INDEX incidents_cluster ON incidents (cluster_id, state);
CREATE TABLE outbox (key TEXT PRIMARY KEY, created_at REAL NOT NULL, severity TEXT NOT NULL, text TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at REAL NOT NULL DEFAULT 0, sent_at REAL, delivery TEXT,
  last_error TEXT);
"""


def test_migration_from_pre_framework_m5_db(tmp_path):
    """A database written by the pre-framework worker upgrades in place and does not re-page."""
    path = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_SCHEMA)
    cid = "ab" * 32
    conn.execute("INSERT INTO incidents VALUES ('EB94775-abababab-900', ?, 'confirmed', 'critical', 'open', 900, 900, "
                 "1000, 1001, 630000, 630000, NULL, '{}', 1.0, 2.0)", (cid,))
    conn.execute("INSERT INTO outbox (key, created_at, severity, text, sent_at, delivery) VALUES "
                 "('EB94775-abababab-900:confirmed:900', 1.0, 'critical', 'x', 1.0, 'telegram:7')")
    conn.commit()
    conn.close()

    db = DB(str(path))
    assert db.migrated_from == 0 and db.meta("schema_version") == 1
    tables = {r[0] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"m5_incidents", "incidents", "outbox", "inbox"} <= tables
    m5 = db.conn.execute("SELECT * FROM m5_incidents").fetchone()
    assert (m5["id"], m5["max_shortfall_vunits"]) == ("EB94775-abababab-900", 630000)
    inc = db.conn.execute("SELECT * FROM incidents").fetchone()
    assert (inc["id"], inc["check_id"], inc["key"], inc["severity"], inc["state"], inc["alerts"]) == (
        "EB94775-abababab-900", "m5", cid, "page", "open", 1)
    assert {"check_id", "incident_id", "kind", "receipt"} <= {r[1] for r in db.conn.execute("PRAGMA table_info(outbox)")}
    assert DB(str(path)).migrated_from == 1  # idempotent


def test_m5_runs_on_a_migrated_database_without_repaging(tmp_path):
    """Open pre-framework incident + same confirmed state after upgrade -> no new message."""
    from test_worker import Capture as M5Capture
    from test_worker import make, poc_setup, tick

    chain, w, ssv = poc_setup(tmp_path)
    s = chain.number
    chain.mine()
    d = keys(63, 9_000)
    ssv.register(OWNER, OPS, d)
    ssv.commit(s)
    ssv.update(OWNER, OPS, 2048)
    ssv.remove(OWNER, OPS, d)
    tick(w)
    assert len(w.notifier.keys("EB94775")) == 1
    # downgrade the framework rows to what the pre-framework worker would have left, then upgrade
    with w.db.tx():
        w.db.conn.execute("DELETE FROM incidents")
        w.db.conn.execute("UPDATE m5_incidents SET severity='critical'")
        w.db.conn.execute("ALTER TABLE m5_incidents RENAME TO incidents_old")
        w.db.conn.execute("DROP TABLE incidents")
        w.db.conn.execute("ALTER TABLE incidents_old RENAME TO incidents")
        w.db.conn.execute("DELETE FROM meta WHERE key='schema_version'")
    cap = M5Capture()
    chain.mine(3)
    _, w2 = make(tmp_path, chain=chain, notifier=cap, activation_block=w.cfg.activation_block,
                 start_block=w.cfg.start_block)
    assert w2.db.migrated_from == 0
    tick(w2)
    tick(w2)
    assert cap.sent == []
    assert w2.db.conn.execute("SELECT COUNT(*) FROM incidents WHERE state='open'").fetchone()[0] == 1


def test_scheduler_reorg_resets_event_cursor(tmp_path):
    chain = FakeChain()
    chain.mine()
    stub = Stub(schedule=OnEvents({"NetworkFeeUpdated"}))
    _, w = fwmake(tmp_path, [stub], chain=chain)
    chain.mine()
    advance(w)  # records a block hash below the fork point
    fork_point = chain.number
    chain.emit_event("NetworkFeeUpdated", [], u256(1) + u256(2))
    chain.mine()
    advance(w)
    assert [r["events"] for r in stub.runs] == [["NetworkFeeUpdated"]]
    chain.reorg(fork_point)  # the event is dropped; a different one lands on the new chain
    chain.emit_event("NetworkFeeUpdated", [], u256(2) + u256(3))
    chain.mine()
    advance(w)
    assert len(stub.runs) == 2 and {"reorg", "events"} <= stub.runs[1]["reasons"]
    assert stub.runs[1]["events"] == ["NetworkFeeUpdated"]
    assert any(r["key"].startswith("health:reorg") for r in outbox(w))


def test_config_example_loads_and_builds_every_check(tmp_path):
    from pathlib import Path

    from eb_reconcile import load_config

    cfg = load_config(str(Path(__file__).resolve().parent.parent / "config.example.json"))
    built = registry.build(cfg.raw)
    assert [c.id for c in built] == list(registry.REGISTRY)
    m5 = next(c for c in built if c.id == "m5")
    assert m5.guards_from == (25037749, 218) and m5.config["layout"]["implementation"].startswith("0xa72a")
    assert cfg.alerts["mode"] == "record" and cfg.rpc_url_env == "MAINNET_RPC_URL"
