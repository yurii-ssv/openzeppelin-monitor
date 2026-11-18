"""Per-check silent catch-up, CP2 (plan check-switches): a re-enabled check, or a check new on a live database,
evaluates everything since its last committed run silently and alerts from its next run (D4, D9, D10, D11); plus DF1
(a malformed switch stops a start before the writable database is opened).

The catch-up is the D17 rule per check: at the start, a durable record `catchup:<id>` is written before any run
attempt; the check's first run is pinned to its catch-up block (the checkpoint of the first step it runs in), forced,
with reasons {backfill, force} and every finding applied with `notify=False`; its first run at a later block is
forced (reasons {live, force}) and sends what it still reports; the record is deleted when that run commits. No
message is ever sent about the catch-up itself (D3). The global backfill (`test_backfill.py`) is unchanged.

Regression tests put the behavioural assertion first, so on the CP1 head they fail on the burst they exist to remove
(V2.7). The registered checks here exist only while a test runs (fixture `ctl`).
"""

from __future__ import annotations

import json
import random
import sqlite3
from pathlib import Path

import pytest
from fakechain import PROXY, FakeChain, FakeClock, FakeRpc
from harness import Capture, outbox

import checks as registry
from checks.base import Check, Every, Finding, OnEvents
from eb_reconcile import DB, DEFAULTS, Config, Worker
from eb_storage import u256
from rpc import RpcError

IDS = ("cu_a", "cu_b")
KIND = {"cu_a": "NetworkFeeUpdated", "cu_b": "OperatorFeeIncreaseLimitUpdated"}  # each check has its own event
DATA = {"cu_a": u256(1) + u256(2), "cu_b": u256(7)}
SLOT = {"cu_a": 0xCA0, "cu_b": 0xCB0}  # level words SLOT + i: 1 = condition `lv<i>` holds at that block
CTL: dict[str, dict] = {}  # check id -> {"fail": runs left to fail, "rpc": fail with a transport RpcError}


def _register(cid: str):
    @registry.register
    class Watched(Check):
        """A level rule (page `lv<i>` while word SLOT+i == 1, resolved when 0; every run) and an event rule (one
        page per event of its kind, keyed by its block; seen once, from the event cursor), like test_backfill's
        Watch, but registered, so `checks.<id>.enabled` switches it."""

        id = cid
        schedule = (Every(3600), OnEvents({KIND[cid]}))

        def __init__(self, config, settings):
            super().__init__(config, settings)
            self.runs: list[dict] = []
            self.reorgs: list[int] = []

        def on_reorg(self, ancestor):
            self.reorgs.append(ancestor)

        def run(self, ctx, block):
            ctl = CTL[self.id]
            if ctl["fail"] > 0:
                ctl["fail"] -= 1
                if ctl["rpc"]:
                    raise RpcError("transport error: all 3 RPC endpoints failed (HTTP 429)", transport=True)
                raise RuntimeError("cannot evaluate")
            events = [ev.block for ev in ctx.events()]
            self.runs.append({"block": block, "reasons": set(ctx.reasons), "events": events,
                              "ancestor": ctx.reorg_ancestor})
            out = [Finding(check=self.id, key=f"ev:{b}", severity="page", title=f"{self.id} event at {b}", body="")
                   for b in events]
            words = ctx.rpc.storage(PROXY, [SLOT[self.id] + i for i in range(3)])
            out += [Finding(check=self.id, key=f"lv{i}", severity="page", title=f"{self.id} lv{i}", body="",
                            resolve=w != 1) for i, w in enumerate(words)]
            return out

    return Watched


@pytest.fixture
def ctl():
    CTL.clear()
    for cid in IDS:
        _register(cid)
        CTL[cid] = {"fail": 0, "rpc": False}
    try:
        yield CTL
    finally:
        for cid in IDS:
            registry.REGISTRY.pop(cid, None)
        CTL.clear()


class Env:
    """One database, chain, clock and capturing notifier across worker restarts (`start`)."""

    def __init__(self, tmp_path, confirmations=0, max_log_range=10_000):
        self.tmp_path = tmp_path
        self.max_log_range = max_log_range  # 1: every scanned block is recorded (a common ancestor below any fork)
        self.chain = FakeChain()
        self.chain.mine()
        self.start_block = self.chain.sealed
        self.clock = FakeClock()
        self.notifier = Capture()
        self.confirmations = confirmations
        self.w = None

    def raw(self, disabled=(), backfill=True, **bf) -> dict:
        return {
            "chain_id": 1, "proxy": PROXY, "activation_block": self.start_block, "start_block": self.start_block,
            "rpc_url_env": "UNUSED", "confirmations": self.confirmations, "max_log_range": self.max_log_range,
            "db_path": str(self.tmp_path / "w.sqlite"),
            "alerts": {"mode": "record", "label": "test", "digest_interval_s": 0},
            "inventory": {"api_url": None}, "backfill": {"enabled": backfill, **bf},
            "checks": {cid: {"enabled": False} for cid in disabled},
        }

    def start(self, only=IDS, disabled=(), backfill=True, bootstrap=True, **bf) -> Worker:
        """A worker start (restart) on the shared database: the registered checks in `only` that the config does
        not switch off (`disabled`) are built."""
        cfg = Config({**DEFAULTS, **self.raw(disabled, backfill, **bf)})
        built = registry.build(cfg.raw, only=list(only))
        self.w = Worker(cfg, FakeRpc(self.chain), DB(cfg.db_path), self.notifier, clock=self.clock, checks=built)
        if bootstrap:
            self.w.bootstrap()
        return self.w

    def go_live(self, only=IDS):
        """A new database with the backfill on: the first step catches up, evaluates silently and goes live; the
        second is every check's first live run."""
        self.start(only=only)
        for _ in range(1 + self.confirmations):  # with confirmations, the first steps are below start_block
            self.step()
        assert self.w.phase()["phase"] == "live"
        self.step()
        assert self.w.phase()["first_live"] == []
        return self.w

    def step(self, seconds=3600, mine=True):  # every step is past the checks' Every(3600) interval
        self.clock.advance(seconds)
        if mine:
            self.chain.mine()
        self.w.step()

    def event(self, cid: str) -> int:
        """An event of `cid`'s kind in the pending block; returns that block."""
        self.chain.emit_event(KIND[cid], [], DATA[cid])
        return self.chain.number

    def level(self, cid: str, i: int, value: int) -> None:
        self.chain.write(SLOT[cid] + i, value)

    # ---- reads (the current worker's connection)

    def check(self, cid: str):
        return self.w.checks[cid]

    def rec(self, cid: str):
        return self.w.db.meta(f"catchup:{cid}")

    def rows(self, cid: str, state=None) -> list[dict]:
        q, args = "SELECT * FROM incidents WHERE check_id=?", [cid]
        if state:
            q, args = q + " AND state=?", [*args, state]
        return [dict(r) for r in self.w.db.conn.execute(q + " ORDER BY rowid", args)]

    def msgs(self, cid=None, after=0) -> list[dict]:
        """Finding messages (not health/digest) with rowid > `after`, each with its incident key."""
        rows = self.w.db.conn.execute(
            "SELECT o.rowid AS rid, o.*, i.key AS ikey_ FROM outbox o LEFT JOIN incidents i ON i.id = o.incident_id "
            "WHERE o.kind NOT IN ('health', 'digest') AND o.rowid > ? ORDER BY o.rowid", (after,)).fetchall()
        return [dict(r) for r in rows if cid is None or r["check_id"] == cid]

    def last_rowid(self) -> int:
        return self.w.db.conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM outbox").fetchone()[0]

    def health(self) -> list[dict]:
        return outbox(self.w, "kind='health'")


def _keys(msgs) -> list[str]:
    return [m["ikey_"] for m in msgs]


# ---------------------------------------------------------------- V2.1 silent re-enable (A2; D4, D9)


def test_reenabled_check_catches_up_silently_then_sends_what_is_still_true_once(tmp_path, ctl):
    env = Env(tmp_path)
    env.go_live(only=("cu_a",))
    env.level("cu_a", 1, 1)
    ev0 = env.event("cu_a")
    env.step()
    assert sorted(_keys(env.msgs("cu_a"))) == sorted([f"ev:{ev0}", "lv1"])  # live: sent pages
    env.start(only=("cu_a",), disabled=("cu_a",))  # restart disabled: CP1 closure
    closed = env.rows("cu_a")
    assert [r["state"] for r in closed] == ["disabled"] * 2
    mark = env.last_rowid()
    # while disabled: two events that would page, lv0 on then off (healed), lv2 on and stays, lv1 stays on
    d1 = env.event("cu_a")
    env.step()
    env.level("cu_a", 0, 1)
    env.step()
    d2 = env.event("cu_a")
    env.step()
    env.level("cu_a", 0, 0)
    env.level("cu_a", 2, 1)
    env.step(7 * 3600)
    assert env.last_rowid() == mark

    w = env.start(only=("cu_a",))  # re-enabled
    pending = w.status()["checks"]["cu_a"].get("catchup")
    check = env.check("cu_a")
    env.step()
    catch_block = w.checkpoint()["block"]
    # behavioural assertion first (red on the CP1 head: the re-enabled check's first run sends the disabled period)
    assert env.msgs(after=mark) == [], _keys(env.msgs(after=mark))
    (silent,) = check.runs
    assert {"backfill", "force"} <= silent["reasons"] and "live" not in silent["reasons"]
    assert silent["block"] == catch_block and silent["events"] == [d1, d2]
    assert pending == {"state": "pending", "history_block": None, "since": pending["since"], "cause": "reenabled"}
    assert w.status()["checks"]["cu_a"]["catchup"]["state"] == "first_live"
    assert env.rec("cu_a")["history_block"] == catch_block
    opened = {r["key"]: r for r in env.rows("cu_a", "open")}
    assert sorted(opened) == sorted([f"ev:{d1}", f"ev:{d2}", "lv1", "lv2"])  # healed lv0: nothing
    assert all(r["notify"] == 0 and r["alerts"] == 0 for r in opened.values())
    assert not env.rows("cu_a", "resolved")

    env.step()  # the first live run, at a later block
    live = check.runs[1]
    assert {"live", "force"} <= live["reasons"] and "backfill" not in live["reasons"] and live["block"] > catch_block
    sent = env.msgs(after=mark)
    assert sorted((m["kind"], m["ikey_"]) for m in sent) == [("open", "lv1"), ("open", "lv2")]
    old_ids = {r["id"] for r in closed}
    assert not {m["incident_id"] for m in sent} & old_ids  # a new incident for the disabled key lv1
    assert env.rows("cu_a", "disabled") == closed  # the disabled rows untouched
    assert env.rec("cu_a") is None and "catchup" not in w.status()["checks"]["cu_a"]
    assert env.health() == []  # D3: no notice of any kind

    ev_live = env.event("cu_a")
    env.step()
    for _ in range(3):
        env.step()
    keys = _keys(env.msgs(after=mark))
    assert sorted(keys) == sorted(["lv1", "lv2", f"ev:{ev_live}"])  # the live event once; nothing twice
    assert f"ev:{d1}" not in keys and f"ev:{d2}" not in keys and "lv0" not in keys


# ---------------------------------------------------------------- V2.2 new check on a live database (A3; D9, D10)


def test_new_check_on_a_live_database_catches_up_from_start_block(tmp_path, ctl):
    """(a) History built with another check; a start that adds cu_a: its first run sees every stored event since
    start_block silently, then it alerts from the next block."""
    env = Env(tmp_path)
    env.go_live(only=("cu_b",))
    hist = []
    for i in range(4):
        hist.append(env.event("cu_a"))
        if i == 1:
            env.level("cu_a", 1, 1)
        env.step()
    mark = env.last_rowid()
    w = env.start(only=IDS)
    check = env.check("cu_a")
    env.step()
    # behavioural assertion first (red on the CP1 head: the new check's first run sends every history event)
    assert env.msgs(after=mark) == [], _keys(env.msgs(after=mark))
    assert check.runs[0]["events"] == hist and {"backfill", "force"} <= check.runs[0]["reasons"]
    assert env.rec("cu_a")["cause"] == "new" and env.rec("cu_b") is None  # cu_b completed runs before: D10
    assert not {"backfill", "live", "force"} & env.check("cu_b").runs[0]["reasons"]
    env.step()
    assert [(m["kind"], m["ikey_"]) for m in env.msgs(after=mark)] == [("open", "lv1")]
    ev = env.event("cu_a")
    env.step()
    env.step()
    assert _keys(env.msgs(after=mark)) == ["lv1", f"ev:{ev}"] and env.rec("cu_a") is None
    assert env.health() == []
    assert w.status()["checks"]["cu_a"].get("catchup") is None


def test_upgraded_live_database_gives_no_catch_up_to_a_check_that_ran(tmp_path, ctl):
    """(b) D10: a live database where cu_a completed runs and has an open sent page, restarted (the first start of
    this version): no record, no silent or forced run, no message; the page re-alerts on its cadence only."""
    env = Env(tmp_path)
    env.go_live(only=("cu_a",))
    env.level("cu_a", 1, 1)
    env.step()
    assert _keys(env.msgs("cu_a")) == ["lv1"]
    assert env.w.db.meta("run:cu_a")["block"] is not None
    mark = env.last_rowid()
    w = env.start(only=("cu_a",))
    assert env.rec("cu_a") is None and not [k for (k,) in w.db.conn.execute(
        "SELECT key FROM meta WHERE key GLOB 'catchup:*'")]
    check = env.check("cu_a")
    for _ in range(3):
        env.step()
    assert check.runs and all(not {"backfill", "live", "force"} & r["reasons"] for r in check.runs)
    assert env.msgs(after=mark) == []
    env.step(6 * 3600)
    assert [m["kind"] for m in env.msgs(after=mark)] == ["realert"]


def test_new_check_while_the_database_is_backfilling_takes_the_global_path(tmp_path, ctl):
    """(c) Phase `backfill`: a check added mid-backfill is evaluated by the global silent backfill
    (`phase.evaluated`); no per-check record is ever written."""
    env = Env(tmp_path)
    for i in range(40):
        if i in (3, 30):
            env.event("cu_a")
        env.chain.mine()
    bf = {"live_within_blocks": 0, "blocks_per_step": 10}
    env.start(only=("cu_b",), **bf)
    env.step(mine=False)
    assert env.w.backfilling
    env.start(only=IDS, **bf)
    n = 0
    while env.w.backfilling:
        assert not env.w.db.conn.execute("SELECT 1 FROM meta WHERE key GLOB 'catchup:*'").fetchall()
        env.step(mine=False)
        n += 1
        assert n < 20
    assert "cu_a" in env.w.db.meta("phase").get("first_live", []) or env.check("cu_a").runs
    assert {"backfill", "force"} <= env.check("cu_a").runs[0]["reasons"]
    env.step()
    assert env.msgs() == [] and env.rec("cu_a") is None


@pytest.mark.parametrize("case", ["new", "reenabled"])
def test_backfill_disabled_keeps_the_base_behaviour(tmp_path, ctl, case):
    """(d) `backfill.enabled: false` (the harness default): no record; the first run alerts as at base (every
    event since the cursor is sent: the documented dependency); the D7 marker is cleared at the enabling start."""
    env = Env(tmp_path)
    env.start(only=("cu_b",) if case == "new" else IDS, backfill=False)
    env.step()
    if case == "reenabled":
        env.start(only=IDS, disabled=("cu_a",), backfill=False)
    evs = [env.event("cu_a")]
    env.step()
    evs.append(env.event("cu_a"))
    env.step()
    mark = env.last_rowid()
    env.start(only=IDS, backfill=False)
    assert env.rec("cu_a") is None and env.w.db.meta("disabled:cu_a") is None
    env.step()
    assert not {"backfill", "live"} & env.check("cu_a").runs[0]["reasons"]
    assert _keys(env.msgs("cu_a", after=mark)) == [f"ev:{b}" for b in evs]


def test_check_disabled_after_go_live_before_its_first_live_run(tmp_path, ctl):
    """(e) cu_a is still listed in `phase.first_live` when it is disabled; re-enabled, its stale entry is removed:
    the catch-up run carries no `live` reason (silent), then live once."""
    env = Env(tmp_path)
    env.level("cu_a", 1, 1)
    env.chain.mine()
    env.start(only=IDS)
    env.step()
    assert env.w.phase()["phase"] == "live" and env.w.phase()["first_live"] == ["cu_a", "cu_b"]
    env.start(only=IDS, disabled=("cu_a",))
    env.step()  # cu_b's first live run; cu_a stays listed
    assert env.w.phase()["first_live"] == ["cu_a"]
    d = env.event("cu_a")
    env.level("cu_a", 2, 1)
    env.step()
    mark = env.last_rowid()
    env.start(only=IDS)
    first_live = env.w.phase()["first_live"]
    env.step()
    # behavioural assertion first (red on the CP1 head: the stale `first_live` entry makes it a live run)
    assert env.msgs("cu_a", after=mark) == [], _keys(env.msgs(after=mark))
    assert first_live == []
    (silent,) = env.check("cu_a").runs
    assert {"backfill", "force"} <= silent["reasons"] and "live" not in silent["reasons"] and silent["events"] == [d]
    env.step()
    env.step()
    assert sorted(_keys(env.msgs("cu_a", after=mark))) == ["lv1", "lv2"] and env.rec("cu_a") is None


def test_check_disabled_during_the_backfill_is_caught_up_after_go_live(tmp_path, ctl):
    """(f) cu_a disabled while the database backfills: never `evaluated`, the go-live proceeds without it;
    re-enabled after the go-live it catches up silently from start_block, then alerts."""
    env = Env(tmp_path)
    hist = []
    for i in range(40):
        if i in (3, 30):
            hist.append(env.event("cu_a"))
        env.chain.mine()
    bf = {"live_within_blocks": 0, "blocks_per_step": 10}
    env.start(only=IDS, **bf)
    env.step(mine=False)
    env.start(only=IDS, disabled=("cu_a",), **bf)
    n = 0
    while env.w.backfilling:
        env.step(mine=False)
        n += 1
        assert n < 20
    assert env.w.phase()["phase"] == "live" and "cu_a" not in env.w.phase().get("first_live", [])
    assert env.w.db.meta("run:cu_a") is None
    env.step()
    hist.append(env.event("cu_a"))
    env.level("cu_a", 0, 1)
    env.step()
    mark = env.last_rowid()
    env.start(only=IDS)
    env.step()
    assert env.msgs(after=mark) == [], _keys(env.msgs(after=mark))
    assert env.rec("cu_a")["cause"] == "reenabled"
    assert env.check("cu_a").runs[0]["events"] == hist
    env.step()
    assert _keys(env.msgs(after=mark)) == ["lv0"]


def test_reenabled_while_backfilling_clears_the_marker_without_a_record(tmp_path, ctl):
    """D9: while the phase is `backfill` the global path handles a re-enabled check: the enabling start clears the
    D7 marker and writes no catch-up record; the check is evaluated silently by the global backfill."""
    env = Env(tmp_path)
    for i in range(40):
        if i == 5:
            env.event("cu_a")
        env.chain.mine()
    bf = {"live_within_blocks": 0, "blocks_per_step": 10}
    env.start(only=IDS, disabled=("cu_a",), **bf)
    env.step(mine=False)
    assert env.w.backfilling and env.w.db.meta("disabled:cu_a") is not None
    env.start(only=IDS, **bf)
    assert env.w.db.meta("disabled:cu_a") is None and env.rec("cu_a") is None
    while env.w.backfilling:
        env.step(mine=False)
    assert "cu_a" in env.w.phase().get("first_live", []) or env.check("cu_a").runs
    env.step()
    assert env.msgs() == []


def test_backfill_switched_off_mid_catch_up_keeps_the_durable_record(tmp_path, ctl):
    """A record is decided once, durably (D9): a restart with `backfill.enabled: false` while it is pending writes
    no new record but honours the existing one, so the catch-up stays silent and ends normally."""
    env = Env(tmp_path)
    env.go_live(only=("cu_a",))
    env.start(only=("cu_a",), disabled=("cu_a",))
    d = env.event("cu_a")
    env.step()
    mark = env.last_rowid()
    env.start(only=("cu_a",))
    rec = env.rec("cu_a")
    env.start(only=("cu_a",), backfill=False)
    env.step()
    assert env.msgs(after=mark) == [] and rec["state"] == "pending"
    assert env.check("cu_a").runs[0]["events"] == [d] and env.rec("cu_a")["state"] == "first_live"
    env.step()
    assert env.msgs(after=mark) == [] and env.rec("cu_a") is None


# ---------------------------------------------------------------- V2.3 restart and failure boundaries (PF-01, PF-02)


@pytest.mark.parametrize("kind", ["runtime", "rpc"])
def test_restarts_and_a_failed_silent_run_keep_the_record_and_its_pinned_block(tmp_path, ctl, kind):
    env = Env(tmp_path)
    env.go_live(only=("cu_a",))
    env.level("cu_a", 1, 1)
    env.step()
    env.start(only=("cu_a",), disabled=("cu_a",), bootstrap=False)  # restart between disable and closure
    env.chain.down = True
    env.step()
    assert not env.w.bootstrapped and env.rows("cu_a", "open")
    env.chain.down = False
    env.step()
    assert env.rows("cu_a", "open") == [] and env.w.db.meta("disabled:cu_a")["closed"] == 1
    d = env.event("cu_a")
    env.step()
    mark = env.last_rowid()

    ctl["cu_a"].update(fail=1, rpc=kind == "rpc")
    env.start(only=("cu_a",))
    rec0 = env.rec("cu_a")
    env.step()  # the silent run fails
    pinned = env.w.checkpoint()["block"]
    rec = env.rec("cu_a")
    assert env.msgs(after=mark) == [] and env.w.db.meta("run:cu_a")["errors"] >= 1
    if kind == "rpc":
        assert env.w.rpc_failures == 1
    else:
        assert [h for h in env.health() if "check-error" in h["text"] and "cu_a" in h["text"]]
    env.start(only=("cu_a",))  # restart after the record, before the silent run commits: record kept
    restarted = env.rec("cu_a")
    env.step(mine=False)  # the same block again: the retry, pinned, silent
    # behavioural assertion first (red on the CP1 head: the retry is an ordinary run that sends the disabled period)
    assert env.msgs(after=mark) == [], _keys(env.msgs(after=mark))
    assert rec0["state"] == "pending"  # written before any run attempt
    assert rec["state"] == "pending" and rec["history_block"] == pinned  # a failed run is not "evaluated"
    assert restarted == rec
    (silent,) = env.check("cu_a").runs
    assert silent["block"] == pinned and {"backfill", "force"} <= silent["reasons"] and silent["events"] == [d]
    assert env.rec("cu_a")["state"] == "first_live" and env.msgs(after=mark) == []
    env.step(mine=False)  # stepped twice: no block after the catch-up block yet, no live run
    assert len(env.check("cu_a").runs) == 1 and env.msgs(after=mark) == []
    env.start(only=("cu_a",))  # restart between the silent run and the first live run
    env.step()
    (live,) = env.check("cu_a").runs
    assert {"live", "force"} <= live["reasons"] and live["block"] > pinned
    assert [(m["kind"], m["ikey_"]) for m in env.msgs(after=mark)] == [("open", "lv1")]
    assert env.rec("cu_a") is None
    env.start(only=("cu_a",))  # restart after the first live run: no record, no forced run, nothing re-sent
    assert env.rec("cu_a") is None
    env.step()
    env.step(mine=False)
    assert all(not {"backfill", "live", "force"} & r["reasons"] for r in env.check("cu_a").runs)
    assert len(env.msgs(after=mark)) == 1


def test_new_check_restarted_after_a_failed_silent_run_keeps_its_pinned_block(tmp_path, ctl):
    """A new check (no completed run): its record is kept across a restart (not re-planned), so the retry after
    blocks arrived is still pinned to the catch-up block: an event mined before it stays silent, one after it is
    sent once by the live run of the same step."""
    env = Env(tmp_path)
    env.go_live(only=("cu_b",))
    before = env.event("cu_a")
    env.step()
    mark = env.last_rowid()
    ctl["cu_a"]["fail"] = 2
    env.start(only=IDS)
    env.step()  # the silent run fails at the catch-up block
    pinned = env.w.checkpoint()["block"]
    rec = env.rec("cu_a")
    after = env.event("cu_a")
    env.step()  # fails again, a block later
    rec2 = env.rec("cu_a")
    env.start(only=IDS)  # restart: still no completed run, the record (and its block) is kept
    rec3 = env.rec("cu_a")
    env.step()
    # behavioural assertion first (red on the CP1 head: the event before the catch-up block is sent)
    assert _keys(env.msgs(after=mark)) == [f"ev:{after}"]
    assert rec["state"] == "pending" and rec["history_block"] == pinned and rec2 == rec3 == rec
    runs = env.check("cu_a").runs
    assert runs[0]["block"] == rec["history_block"] and runs[0]["events"] == [before]
    assert runs[1]["events"] == [after] and {"live", "force"} <= runs[1]["reasons"] and env.rec("cu_a") is None


# ---------------------------------------------------------------- V2.4 reorg while disabled, and during the catch-up


def test_reorg_while_disabled_then_reenabled_catches_up_on_the_new_chain(tmp_path, ctl):
    env = Env(tmp_path)
    env.go_live(only=("cu_a",))
    env.step()
    env.step()
    c0 = env.w.db.meta("cursor:events:cu_a")[0]
    env.start(only=("cu_a",), disabled=("cu_a",))
    env.event("cu_a")  # the orphaned branch: an event and lv0 above the old cursor
    env.level("cu_a", 0, 1)
    env.step()
    env.step()
    env.chain.reorg(c0)  # below the old cursor: block c0 is replaced, the ancestor is c0 - 1
    new_ev = env.event("cu_a")  # the new chain: another event, and lv2 that stays on
    env.level("cu_a", 2, 1)
    env.chain.mine(2)
    env.step()
    ancestor = c0 - 1
    assert env.w.db.meta("cursor:events:cu_a")[0] == ancestor and env.w.db.meta("reorg:cu_a") == ancestor
    mark = env.last_rowid()
    w = env.start(only=("cu_a",))
    check = env.check("cu_a")
    assert check.reorgs == [ancestor]  # replayed by Worker.__init__
    env.step()
    # behavioural assertion first (red on the CP1 head: the new chain's disabled-period event and lv2 are sent)
    assert env.msgs(after=mark) == [], _keys(env.msgs(after=mark))
    (silent,) = check.runs
    assert {"backfill", "force", "reorg"} <= silent["reasons"] and silent["ancestor"] == ancestor
    assert silent["events"] == [new_ev] and w.db.meta("reorg:cu_a") is None
    env.step()
    assert [(m["kind"], m["ikey_"]) for m in env.msgs(after=mark)] == [("open", "lv2")]
    env.step()
    assert _keys(env.msgs(after=mark)) == ["lv2"]


def test_reorg_below_the_catch_up_block_before_the_first_live_run_stays_silent(tmp_path, ctl):
    """A reorg that replaces the catch-up block after the silent run committed: the silent run is repeated, pinned
    to the catch-up block, so a new-chain event up to there is never sent; an event after it is sent once."""
    env = Env(tmp_path)
    env.go_live(only=("cu_a",))
    env.start(only=("cu_a",), disabled=("cu_a",))
    env.step()
    env.step()
    env.start(only=("cu_a",))
    env.step()
    pinned = env.w.checkpoint()["block"]  # the catch-up block: the checkpoint of the first step after the start
    env.step(mine=False)
    rec = env.rec("cu_a")
    mark = env.last_rowid()
    env.chain.reorg(pinned)  # the catch-up block itself is replaced
    replayed = env.event("cu_a")  # at the catch-up block's height on the new chain
    assert replayed == pinned
    env.chain.mine()
    after = env.event("cu_a")
    env.chain.mine()
    env.step()
    check = env.check("cu_a")
    assert env.w.checkpoint()["block"] > pinned
    keys = _keys(env.msgs(after=mark))
    # behavioural assertion first (red without the repeat: the replayed event at the catch-up block is sent)
    assert f"ev:{replayed}" not in keys and keys == [f"ev:{after}"], keys
    assert rec["state"] == "first_live" and rec["history_block"] == pinned
    assert {"backfill", "force", "reorg"} <= check.runs[1]["reasons"] and check.runs[1]["block"] == pinned
    assert env.rec("cu_a") is None
    env.step()
    assert _keys(env.msgs(after=mark)) == [f"ev:{after}"]


# ---------------------------------------------------------------- V2.5 one check's catch-up never holds another


@pytest.mark.parametrize("cause", ["reenabled", "new"])
def test_live_alerts_of_another_check_are_sent_while_a_catch_up_fails(tmp_path, ctl, cause):
    env = Env(tmp_path)
    env.go_live(only=IDS if cause == "reenabled" else ("cu_a",))
    if cause == "reenabled":
        env.start(only=IDS, disabled=("cu_b",))
    env.level("cu_b", 1, 1)
    old = env.event("cu_b")
    env.step()
    mark = env.last_rowid()
    ctl["cu_b"]["fail"] = 3
    env.start(only=IDS)
    a_events = [env.event("cu_a")]  # page-worthy for A, in the step of B's (failing) silent run
    env.step()
    assert _keys(env.msgs("cu_a", after=mark)) == [f"ev:{a_events[0]}"]  # sent in that step
    pinned = env.w.checkpoint()["block"]  # B's catch-up block
    late_b, recs = None, []
    for i in range(2):  # B keeps failing; A keeps alerting
        a_events.append(env.event("cu_a"))
        if i == 0:
            late_b = env.event("cu_b")  # B's event after its catch-up block: live for B
        env.step()
        assert _keys(env.msgs("cu_a", after=mark)) == [f"ev:{b}" for b in a_events]
        recs.append(env.rec("cu_b"))
    assert env.msgs("cu_b", after=mark) == []
    assert [h for h in env.health() if "check-error" in h["text"] and "cu_b" in h["text"]]
    env.step()  # B recovers: silent at its pinned block, then live at this block, in the same step
    # behavioural assertion first (red on the CP1 head: B's recovered run sends its disabled-period event)
    assert sorted(_keys(env.msgs("cu_b", after=mark))) == sorted(["lv1", f"ev:{late_b}"])
    assert f"ev:{old}" not in _keys(env.msgs(after=mark)) and env.rec("cu_b") is None
    assert [(r["state"], r["history_block"]) for r in recs] == [("pending", pinned)] * 2
    runs = env.check("cu_b").runs
    assert runs[0]["block"] == pinned and {"backfill", "force"} <= runs[0]["reasons"]
    assert runs[1]["block"] > pinned and {"live", "force"} <= runs[1]["reasons"] and runs[1]["events"] == [late_b]
    env.step()
    env.step()
    assert len(env.msgs("cu_b", after=mark)) == 2


# ---------------------------------------------------------------- V2.6 quick flips, status CLI


def test_flips_without_steps_close_once_per_disable_and_send_nothing(tmp_path, ctl):
    env = Env(tmp_path)
    env.go_live(only=("cu_a",))
    env.level("cu_a", 1, 1)
    env.step()
    mark = env.last_rowid()
    env.start(only=("cu_a",), disabled=("cu_a",))
    first = env.w.db.meta("disabled:cu_a")
    closed = env.rows("cu_a")
    assert first["closed"] == 1
    env.start(only=("cu_a",))
    assert env.w.db.meta("disabled:cu_a") is None and env.rec("cu_a")["state"] == "pending"
    env.clock.advance(10)
    env.start(only=("cu_a",), disabled=("cu_a",))
    second = env.w.db.meta("disabled:cu_a")
    assert second["closed"] == 0 and second["at"] > first["at"] and env.rec("cu_a") is None
    assert env.rows("cu_a") == closed and env.last_rowid() == mark


def test_reenable_with_no_new_block_pins_the_current_checkpoint_and_a_second_disable_closes_it(tmp_path, ctl):
    env = Env(tmp_path)
    env.go_live(only=("cu_a",))
    env.level("cu_a", 1, 1)
    env.step()
    cp = env.w.checkpoint()["block"]
    mark = env.last_rowid()
    env.start(only=("cu_a",), disabled=("cu_a",))
    env.start(only=("cu_a",))
    env.step(mine=False)  # no block arrived: the catch-up still runs, pinned at the current checkpoint
    assert env.msgs(after=mark) == []
    assert env.rec("cu_a")["history_block"] == cp and env.rec("cu_a")["state"] == "first_live"
    (silent_row,) = env.rows("cu_a", "open")
    assert silent_row["notify"] == 0
    env.start(only=("cu_a",), disabled=("cu_a",))  # closes what the enabled interval opened, same checkpoint
    assert env.w.db.meta("disabled:cu_a")["closed"] == 1 and env.rows("cu_a", "open") == []
    assert env.w.checkpoint()["block"] == cp
    env.start(only=("cu_a",))
    env.step(mine=False)
    assert env.msgs(after=mark) == []
    env.step()  # the next block: live, once
    assert [(m["kind"], m["ikey_"]) for m in env.msgs(after=mark)] == [("open", "lv1")]
    assert [r["state"] for r in env.rows("cu_a")] == ["disabled", "disabled", "open"]


def _dump(db_path: str) -> dict:
    conn = sqlite3.connect(db_path)
    try:
        tables = [t for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {t: sorted(map(repr, conn.execute(f'SELECT * FROM "{t}"'))) for t in tables}
    finally:
        conn.close()


def test_status_cli_during_a_catch_up_shows_it_and_changes_nothing(tmp_path, ctl, capsys):
    import eb_reconcile

    env = Env(tmp_path)
    env.go_live(only=("cu_a",))
    env.start(only=("cu_a",), disabled=("cu_a",))
    env.step()
    env.start(only=("cu_a",))
    raw = json.loads((Path(eb_reconcile.__file__).parent / "config.example.json").read_text())
    raw.update(db_path=str(tmp_path / "w.sqlite"))
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps(raw))

    def status():
        before = _dump(raw["db_path"])
        assert eb_reconcile.main(["status", "--config", str(cfg)]) == 0
        assert _dump(raw["db_path"]) == before
        return json.loads(capsys.readouterr().out)["checks"]["cu_a"].get("catchup")

    assert status()["state"] == "pending"
    env.step()
    assert status() == {**env.rec("cu_a"), "state": "first_live"}
    env.step()
    assert status() is None


# ---------------------------------------------------------------- adversarial sweep (DEFECT-CLASSES 1, 2, 10)


@pytest.mark.parametrize("seed", range(30))
def test_seeded_catchup_sweep(tmp_path, ctl, seed):
    """Random disable/enable restarts, events, level toggles, failing runs (exception or transport RpcError),
    plain restarts, same-block steps and reorgs of depth 1-4 (also during a catch-up), plus deep reorgs at or below
    a check's silent boundary (its catch-up block or the caught-up checkpoint), also after its catch-up record was
    deleted (D15; repair 1 removed the former exclusion), confirmations 0/2.
    The catch-up block is derived independently of the record: the checkpoint of the first step after the start
    that re-enabled the check (a restart in between keeps it), never below its previous boundary (D15). Invariants
    after every action, per check:
    - nothing is sent for it while it is disabled, in the step of its catch-up block, or while its catch-up record
      exists (D3, D4); the record's block, when set, equals the independently derived one;
    - no event at or below its catch-up block (the disabled period and the catch-up) is ever sent;
    - every canonical event of its kind after its catch-up block, up to its committed cursor, is sent (no missed
      page; a live alert of one check is never held by the other's catch-up); no incident sends `open` twice;
    - at most one open row per key."""
    rnd = random.Random(seed)
    env = Env(tmp_path, confirmations=rnd.choice([0, 2]), max_log_range=1)
    env.go_live()
    enabled = {cid: True for cid in IDS}
    floor = {cid: env.w.phase()["history_block"] for cid in IDS}  # events at or below are never sent
    armed: set[str] = set()  # re-enabled, catch-up block not known yet (the next step's checkpoint)
    for _ in range(40):
        act = rnd.choice(["step", "step", "event", "level", "flip", "restart", "fail", "same", "reorg", "deep"])
        mark = env.last_rowid()
        stepped = act in ("step", "event", "level", "fail", "same")
        if act == "step":
            env.step()
        elif act == "event":
            env.event(rnd.choice(IDS))
            env.step()
        elif act == "level":
            env.level(rnd.choice(IDS), rnd.randrange(3), rnd.randint(0, 1))
            env.step()
        elif act in ("flip", "restart"):
            if act == "flip":
                cid = rnd.choice(IDS)
                enabled[cid] = not enabled[cid]
                (armed.add if enabled[cid] else armed.discard)(cid)
            env.start(disabled=[c for c in IDS if not enabled[c]])
        elif act == "fail":
            cid = rnd.choice(IDS)
            CTL[cid].update(fail=rnd.randint(1, 3), rpc=rnd.random() < 0.5)
            env.step()
        elif act == "same":
            env.step(mine=False)
        elif act in ("reorg", "deep"):
            # "deep": at or just below a check's silent boundary, wherever its catch-up record is (none included)
            fork = env.chain.number - rnd.randint(1, 4) if act == "reorg" else \
                floor[rnd.choice(IDS)] - rnd.randint(0, 2)
            if env.start_block + 1 < fork <= env.chain.sealed:
                env.chain.reorg(fork)
                if rnd.random() < 0.5:
                    env.event(rnd.choice(IDS))
                env.chain.mine()
                env.step()
                stepped = True
        new = env.msgs(after=mark)
        for cid in IDS:
            mine_new = [m for m in new if m["check_id"] == cid]
            if stepped and cid in armed:  # the step of its catch-up block (never below its previous boundary)
                floor[cid] = max(env.w.checkpoint()["block"], floor[cid])
                armed.discard(cid)
                assert not mine_new, (seed, act, cid, "catch-up step", _keys(mine_new))
            rec = env.rec(cid) if enabled[cid] else None
            if rec is not None and rec.get("history_block") is not None:
                assert rec["history_block"] == floor[cid], (seed, act, cid, rec, floor[cid])
            assert not (mine_new and (not enabled[cid] or rec is not None)), (seed, act, cid, _keys(mine_new))
            sent = env.msgs(cid)
            assert not [m for m in mine_new if m["ikey_"].startswith("ev:") and int(m["ikey_"][3:]) <= floor[cid]], \
                (seed, act, cid, floor[cid], _keys(mine_new))
            opens = [m["incident_id"] for m in sent if m["kind"] == "open"]
            assert len(opens) == len(set(opens)), (seed, act, cid)
            if enabled[cid] and rec is None:
                cursor = env.w.db.meta(f"cursor:events:{cid}", [env.start_block - 1])[0]
                due = {b for b in (int(lg["blockNumber"], 16) for lg in env.chain.logs if _kind(lg) == KIND[cid])
                       if floor[cid] < b <= cursor}  # chain.logs: the canonical chain's
                got = {int(k[3:]) for k in _keys(sent) if k.startswith("ev:")}
                assert due <= got, (seed, act, cid, sorted(due - got))
        dup = env.w.db.conn.execute(
            "SELECT ikey FROM incidents WHERE state='open' GROUP BY ikey HAVING COUNT(*) > 1").fetchall()
        assert not dup


def _kind(lg) -> str:
    from eb_events import KIND_BY_TOPIC0

    return KIND_BY_TOPIC0.get(lg["topics"][0].lower())


# ---------------------------------------------------------------- DF1: D14 before the writable database (QF3)


def _df1(tmp_path, monkeypatch, value=0):
    """The CLI on the FakeChain environment with `checks.cu_a.enabled` malformed; bootstrap must never be reached."""
    import eb_reconcile

    env = Env(tmp_path)
    raw = env.raw()
    raw["checks"] = {"cu_a": {"enabled": value}}
    cfg = Config({**DEFAULTS, **raw})
    real_build = registry.build
    monkeypatch.setattr(eb_reconcile, "load_config", lambda _: cfg)
    monkeypatch.setattr(eb_reconcile, "make_rpc", lambda _: FakeRpc(env.chain))
    monkeypatch.setattr(eb_reconcile, "make_notifier", lambda _: env.notifier)
    monkeypatch.setattr(registry, "build", lambda settings, only=None: real_build(settings, only=["cu_a", "m5"]))
    monkeypatch.setattr(Worker, "bootstrap", lambda self: pytest.fail("a malformed switch reached bootstrap"))
    return env, cfg


@pytest.mark.parametrize("command", ["run", "once", "verify-layout"])
def test_malformed_switch_writes_nothing_to_a_current_database(tmp_path, ctl, monkeypatch, command):
    """DF1 case 1: an existing current database; no INSERT/UPDATE/DELETE/ALTER is executed (the migration's
    `schema_version` upsert included) before the configuration error."""
    import db as db_module
    import eb_reconcile

    seed_env = Env(tmp_path)
    seed_env.go_live(only=("cu_a",))
    seed_env.w.db.conn.close()
    _, cfg = _df1(tmp_path, monkeypatch)
    before = _dump(cfg.db_path)
    writes: list[str] = []
    real = db_module.migrate

    def traced(conn):
        conn.set_trace_callback(lambda sql: writes.append(sql) if sql.lstrip().upper().startswith(
            ("INSERT", "UPDATE", "DELETE", "ALTER")) else None)
        return real(conn)

    monkeypatch.setattr(db_module, "migrate", traced)
    with pytest.raises(SystemExit, match=r"checks\.cu_a\.enabled: must be true or false"):
        eb_reconcile.main([command, "--config", "unused"])
    # behavioural assertion first (red on the CP1 head: the migration upserts schema_version before the error)
    assert writes == []
    assert _dump(cfg.db_path) == before


@pytest.mark.parametrize("command", ["run", "once", "verify-layout"])
def test_malformed_switch_creates_no_database(tmp_path, ctl, monkeypatch, command):
    """DF1 case 2: no database yet: none is created (no file, no lock file)."""
    import eb_reconcile

    _, cfg = _df1(tmp_path, monkeypatch)
    assert not Path(cfg.db_path).exists()
    with pytest.raises(SystemExit, match=r"checks\.cu_a\.enabled: must be true or false"):
        eb_reconcile.main([command, "--config", "unused"])
    assert sorted(p.name for p in tmp_path.iterdir()) == []


@pytest.mark.parametrize("command", ["run", "once", "verify-layout"])
def test_malformed_switch_leaves_a_pre_version_database_unchanged(tmp_path, ctl, monkeypatch, command):
    """DF1 case 3: a supported pre-version database (schema present, no `schema_version` row): it stays so."""
    import eb_reconcile

    seed_env = Env(tmp_path)
    seed_env.go_live(only=("cu_a",))
    seed_env.w.db.conn.execute("DELETE FROM meta WHERE key='schema_version'")
    seed_env.w.db.conn.close()
    _, cfg = _df1(tmp_path, monkeypatch)
    before = _dump(cfg.db_path)
    with pytest.raises(SystemExit, match=r"checks\.cu_a\.enabled: must be true or false"):
        eb_reconcile.main([command, "--config", "unused"])
    assert _dump(cfg.db_path) == before
    assert "schema_version" not in repr(_dump(cfg.db_path)["meta"])


@pytest.mark.parametrize("command", ["status", "export", "audit"])
def test_read_only_commands_with_a_malformed_switch_are_unchanged(tmp_path, ctl, monkeypatch, capsys, command):
    """Controls: `status` still raises the D14 error when it builds the worker; `export` and `audit` build no
    worker, read no switch and answer (D14 clarification); none writes."""
    import eb_reconcile

    seed_env = Env(tmp_path)
    seed_env.go_live(only=("cu_a",))
    seed_env.w.db.conn.close()
    monkeypatch.setattr(Worker, "bootstrap", lambda self: pytest.fail("reached bootstrap"))
    _, cfg = _df1(tmp_path, monkeypatch)
    before = _dump(cfg.db_path)
    if command == "status":
        with pytest.raises(SystemExit, match=r"checks\.cu_a\.enabled"):
            eb_reconcile.main([command, "--config", "unused"])
    else:
        assert eb_reconcile.main([command, "--config", "unused"]) == 0
        json.loads(capsys.readouterr().out)
    assert _dump(cfg.db_path) == before


def test_harness_default_is_unchanged(tmp_path, ctl):
    """A check of a database created with the harness default (`backfill.enabled: false`) never gets a record."""
    from harness import make_worker

    chain = FakeChain()
    _, w = make_worker(tmp_path, only=["cu_a"], chain=chain, clock=FakeClock())
    assert w.db.meta("catchup:cu_a") is None


# ---------------------------------------------------------------- runbook claims (V2.8, docs-checklist.md)


class _Stop(Exception):
    """Ends `main(["run"])` after one step."""


@pytest.mark.parametrize("command", ["run", "once", "verify-layout", "status", "export"])
def test_every_start_command_closes_and_status_export_never_do(tmp_path, ctl, monkeypatch, capsys, command):
    """Runbook: every start command (`run`, `once`, `verify-layout`) performs the closure of a disabled check's open
    incidents, with no message; the read-only `status` and `export` never close anything."""
    import eb_reconcile

    env = Env(tmp_path)
    env.go_live(only=("cu_a",))
    env.level("cu_a", 1, 1)
    env.step()
    assert len(env.rows("cu_a", "open")) == 1
    mark = env.last_rowid()
    env.w.db.conn.close()
    raw = env.raw(disabled=("cu_a",))
    cfg = Config({**DEFAULTS, **raw})
    real_build, real_step = registry.build, Worker.step

    def one_step(self, *args, **kwargs):
        real_step(self, *args, **kwargs)
        if command == "run":
            raise _Stop

    monkeypatch.setattr(eb_reconcile, "load_config", lambda _: cfg)
    monkeypatch.setattr(eb_reconcile, "make_rpc", lambda _: FakeRpc(env.chain))
    monkeypatch.setattr(eb_reconcile, "make_notifier", lambda _: env.notifier)
    monkeypatch.setattr(eb_reconcile.signal, "signal", lambda *_: None)
    monkeypatch.setattr(registry, "build", lambda settings, only=None: real_build(settings, only=["cu_a", "m5"]))
    monkeypatch.setattr(registry.REGISTRY["m5"], "verify_layout", lambda *a, **k: [])
    monkeypatch.setattr(Worker, "step", one_step)
    try:
        eb_reconcile.main([command, "--config", "unused"])
    except _Stop:
        pass
    capsys.readouterr()
    conn = sqlite3.connect(raw["db_path"])
    try:
        states = [r[0] for r in conn.execute("SELECT state FROM incidents WHERE check_id='cu_a'")]
        new = conn.execute("SELECT COUNT(*) FROM outbox WHERE rowid > ? AND kind != 'health'", (mark,)).fetchone()[0]
        marker = conn.execute("SELECT value FROM meta WHERE key='disabled:cu_a'").fetchone()
    finally:
        conn.close()
    closes = command in ("run", "once", "verify-layout")
    assert states == (["disabled"] if closes else ["open"])
    assert (marker is not None) == closes and new == 0


def test_metrics_have_no_run_series_for_a_disabled_check(tmp_path, ctl):
    """Runbook: a disabled check is not built, so the metrics server (which renders the built checks) has no
    `ssv_watch_check_last_run_*` series for it; no metric says it is disabled (L3)."""
    from metrics import render

    env = Env(tmp_path)
    env.go_live()
    env.start(disabled=("cu_a",))
    env.step()
    text = render(env.w.db.conn, list(env.w.checks))
    runs = [ln for ln in text.splitlines() if ln.startswith("ssv_watch_check_last_run")]
    assert runs and not [ln for ln in runs if 'check="cu_a"' in ln] and [ln for ln in runs if 'check="cu_b"' in ln]
    assert "disabled" not in text
