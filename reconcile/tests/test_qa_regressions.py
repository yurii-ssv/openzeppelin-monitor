"""Regression tests for CP1B QA attempt 1 (plans/monitoring-service/qa/cp1b-attempt-1.md, Appendix A).

The three `test_qa1_*` probes are ported unchanged in their expectations: a pending legacy outbox row
is delivered after migration, a failed parameter reload never lets a check evaluate with the old
values, and a configured inbox that cannot be read raises monitor health. The other tests pin the
surrounding behaviour of each repair.
"""

from __future__ import annotations

import json
import sqlite3

from fakechain import FakeChain
from harness import VIEWS, Capture, advance, make_worker, outbox, stub_params
from test_framework import LEGACY_SCHEMA, Stub

import params
from checks.base import Every, OnInbox
from eb_reconcile import DB
from eb_storage import u256
from metrics import render
from notify import Router
from params import GETTERS, selector

# ---------------------------------------------------------------- finding 1: legacy pending outbox


def test_qa1_migrated_pending_outbox_routes_to_telegram(tmp_path):
    path = tmp_path / "w.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_SCHEMA)
    conn.execute("INSERT INTO outbox(key,created_at,severity,text) VALUES ('pending',1,'critical','old page')")
    conn.commit()
    conn.close()

    class Transport:
        def send(self, *args):
            return "telegram:123"

    chain = FakeChain()
    chain.mine()
    _, w = make_worker(tmp_path, [], chain=chain, notifier=Router({"telegram": Transport()}))
    w.deliver()
    row = outbox(w)[0]
    assert row["sent_at"] is not None and row["delivery"] == "telegram", dict(row)


def test_migration_normalises_legacy_outbox_and_keeps_identity(tmp_path):
    """Authentic pre-framework outbox: unsent critical/warning/info rows and a sent row whose `delivery`
    held the transport receipt. Severities become page/warn/info, keys and texts are unchanged, the sent
    receipt moves to `receipt`, and each unsent row is delivered once on its default route."""
    path = tmp_path / "w.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_SCHEMA)
    rows = [("EB94775-abababab-900:confirmed:900", 1.0, "critical", "page text", None, None),
            ("EB94775-cdcdcdcd-901:confirmed:901", 2.0, "warning", "warn text", None, None),
            ("health:rpc:00", 3.0, "info", "info text", None, None),
            ("EB94775-efefefef-800:confirmed:800", 0.5, "critical", "sent text", 0.6, "telegram:7")]
    conn.executemany("INSERT INTO outbox (key, created_at, severity, text, sent_at, delivery) VALUES (?,?,?,?,?,?)",
                     rows)
    conn.execute("UPDATE outbox SET attempts=2, last_error='telegram HTTP 502' WHERE severity='warning'")
    conn.commit()
    conn.close()

    db = DB(str(path))
    got = {r["key"]: dict(r) for r in db.conn.execute("SELECT * FROM outbox")}
    assert set(got) == {r[0] for r in rows}
    assert [got[k]["severity"] for k, *_ in rows] == ["page", "warn", "info", "page"]
    assert [got[k]["text"] for k, *_ in rows] == ["page text", "warn text", "info text", "sent text"]
    sent = got["EB94775-efefefef-800:confirmed:800"]
    assert (sent["sent_at"], sent["receipt"], sent["delivery"]) == (0.6, "telegram:7", "telegram")
    assert got["EB94775-cdcdcdcd-901:confirmed:901"]["attempts"] == 2  # retry state is kept
    assert DB(str(path)).migrated_from == 1  # idempotent
    db.conn.close()

    capture = Capture()
    chain = FakeChain()
    chain.mine()
    _, w = make_worker(tmp_path, [], chain=chain, notifier=capture)
    with w.db.tx():
        w.db.conn.execute("UPDATE outbox SET next_attempt_at=0")
    w.deliver()
    w.deliver()
    assert [(s, k, t, r) for s, k, t, r in capture.sent] == [
        ("page", "EB94775-abababab-900:confirmed:900", "page text", "telegram"),
        ("warn", "EB94775-cdcdcdcd-901:confirmed:901", "warn text", "telegram"),
        ("info", "health:rpc:00", "info text", "discord")]
    assert all(r["sent_at"] is not None and r["delivery"] in ("telegram", "discord") for r in outbox(w))


# ---------------------------------------------------------------- finding 2: failed parameter reload


def test_qa1_failed_parameter_reload_does_not_use_stale_threshold(tmp_path):
    chain = FakeChain()
    stub_params(chain)
    chain.mine()
    observed = []
    stub = Stub(emit=lambda ctx, b: observed.append(ctx.params.liquidation_threshold_period) or [])
    _, w = make_worker(tmp_path, [stub], chain=chain, views=VIEWS)
    advance(w)
    chain.stub_call(VIEWS, selector(GETTERS["liquidation_threshold_period"]), revert="0x12345678")
    chain.emit_event("LiquidationThresholdPeriodUpdated", [], u256(50000))
    chain.mine()
    w.step(force=True)
    assert observed == [42960], f"check ran using stale parameters after failed reload: {observed}"


class CachingStub(Stub):
    """The README pattern: `prepare` copies a parameter into the check; `run` uses the copy."""

    def prepare(self, ctx):
        super().prepare(ctx)
        self.threshold = ctx.params.liquidation_threshold_period


def test_failed_reload_blocks_cached_values_degrades_health_and_retries(tmp_path):
    chain = FakeChain()
    stub_params(chain)
    chain.mine()
    observed, plain_runs = [], []
    caching = CachingStub(check_id="caching", schedule=Every(60))
    caching.emit = lambda ctx, b: observed.append(caching.threshold) or []
    plain = Stub(check_id="plain", schedule=Every(60), emit=lambda ctx, b: plain_runs.append(b) or [])
    plain.prepare = lambda ctx: None  # a check that never reads parameters
    _, w = make_worker(tmp_path, [caching, plain], chain=chain, views=VIEWS)
    advance(w)
    assert observed == [42_960] and w.params.ok

    # the parameter changes, but the reload fails: neither the cached copy nor ctx.params may be used
    chain.stub_call(VIEWS, selector(GETTERS["liquidation_threshold_period"]), revert="0x12345678")
    chain.emit_event("LiquidationThresholdPeriodUpdated", [], u256(50_000))
    chain.mine()
    w.step(force=True)
    assert observed == [42_960]
    assert not w.params.ok and w.params.block is not None
    try:
        w.params.liquidation_threshold_period
    except params.ParamsUnavailable as exc:
        assert "unavailable" in str(exc)
    else:
        raise AssertionError("a stale parameter was readable")
    assert len(plain_runs) == 2  # checks that do not read parameters keep running
    status = json.loads((tmp_path / "w.status.json").read_text())
    assert status["healthy"] is False and "parameters" in status["error"] and status["params"]["ok"] is False
    assert 'ssv_watch_input_unavailable{input="params"} 1' in render(w.db.conn, list(w.checks))
    assert any(r["key"].startswith("health:params") for r in outbox(w))
    run = w.db.meta("run:caching")
    assert run["errors"] == 1 and "ParamsUnavailable" in run["last_error"]
    assert not any(r["key"].startswith("health:check-error") for r in outbox(w))  # one params health, not two

    w.step(force=True)  # still failing: retried every step, still blocked
    assert observed == [42_960] and not w.params.ok

    # the getter answers again: the next step reloads, re-prepares and evaluates with the new value
    stub_params(chain, liquidation_threshold_period=50_000)
    chain.mine()
    w.step()
    assert w.params.ok and w.params.liquidation_threshold_period == 50_000
    assert observed == [42_960, 50_000]
    status = json.loads((tmp_path / "w.status.json").read_text())
    assert status["healthy"] is True and status["params"]["ok"] is True


def test_initial_parameter_read_failure_blocks_parameter_readers(tmp_path):
    chain = FakeChain()
    chain.mine()  # no stubs: every getter reverts
    observed = []
    stub = Stub(schedule=Every(60), emit=lambda ctx, b: observed.append(ctx.params["network_fee"]) or [])
    _, w = make_worker(tmp_path, [stub], chain=chain, views=VIEWS)
    w.step()
    assert observed == [] and len(stub.runs) == 1 and not w.params.ok
    assert any(r["key"].startswith("health:params") for r in outbox(w))
    stub_params(chain)
    chain.mine()
    w.step()
    assert observed == [3_366_600_000] and w.params.ok


# ---------------------------------------------------------------- finding 3: unreadable inbox


def test_qa1_missing_inbox_raises_health(tmp_path):
    chain = FakeChain()
    chain.mine()
    _, w = make_worker(tmp_path, [Stub(schedule=(Every(60), OnInbox()))], chain=chain,
                       inbox={"path": str(tmp_path / "missing.jsonl")})
    w.step(force=True)
    assert any(r["kind"] == "health" for r in outbox(w)), "missing configured inbox produced no health; worker reports healthy"


def test_inbox_missing_unreadable_and_empty_are_distinguished(tmp_path):
    chain = FakeChain()
    chain.mine()
    path = tmp_path / "failed-tx.jsonl"
    stub = Stub(schedule=(Every(60), OnInbox()))
    _, w = make_worker(tmp_path, [stub], chain=chain, inbox={"path": str(path)})
    status = tmp_path / "w.status.json"

    w.step(force=True)  # missing
    assert [r["key"].split(":")[1] for r in outbox(w, "kind='health'")] == ["inbox-unavailable"]
    s = json.loads(status.read_text())
    assert s["healthy"] is False and "inbox" in s["error"] and s["inbox"]["ok"] is False
    assert 'ssv_watch_input_unavailable{input="inbox"} 1' in render(w.db.conn, list(w.checks))
    assert len(stub.runs) == 1  # the other schedules still run
    w.step(force=True)
    assert len(outbox(w, "kind='health'")) == 1  # de-duplicated within the re-alert window

    path.write_text("")  # an existing empty file is a healthy, empty input
    w.step(force=True)
    s = json.loads(status.read_text())
    assert s["healthy"] is True and s["inbox"]["ok"] is True and s["inbox"]["error"] is None
    assert 'ssv_watch_input_unavailable{input="inbox"} 0' in render(w.db.conn, list(w.checks))

    path.unlink()
    path.mkdir()  # unreadable as a file
    w.step(force=True)
    s = json.loads(status.read_text())
    assert s["healthy"] is False and s["inbox"]["ok"] is False
    assert len(outbox(w, "kind='health'")) == 2
