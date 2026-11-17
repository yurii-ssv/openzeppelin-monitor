"""Silent backfill (D17): history from start_block is stored and evaluated without alerting; alerting starts at
the first step after the catch-up; a condition still true at the first live evaluation alerts then, a
history-only one never does. State table: plans/monitoring-service/workers/cp3/plan.md.
"""

from __future__ import annotations

import json
import random
import sqlite3

import pytest
from fake_once import poc_chain
from fakechain import PROXY, FakeChain, FakeClock
from harness import Capture, make_worker, outbox

from checks.base import Check, Every, Finding, OnEvents
from eb_storage import u256
from metrics import render
from rpc import RpcError

LEVEL_SLOT = 0xD17  # a storage word: 1 = the level condition holds at that block


class Watch(Check):
    """A level rule (page while LEVEL_SLOT == 1, resolved when 0; every run) and an event rule (one page per
    NetworkFeeUpdated, keyed by its block; seen only once, from the event cursor)."""

    id = "watch"

    def __init__(self, fail=False):
        super().__init__({}, {})
        self.schedule = (Every(3600), OnEvents({"NetworkFeeUpdated"}))
        self.fail = fail
        self.runs: list[tuple[int, frozenset]] = []

    def run(self, ctx, block):
        if self.fail:
            raise RuntimeError("cannot evaluate")
        self.runs.append((block, ctx.reasons))
        out = [Finding(check=self.id, key=f"ev:{ev.block}", severity="page", title=f"event at {ev.block}", body="")
               for ev in ctx.events()]
        level = ctx.rpc.storage(PROXY, [LEVEL_SLOT])[0]
        out.append(Finding(check=self.id, key="level", severity="page", title="level", body="",
                           resolve=level != 1))
        return out


def fee_event(chain):
    chain.emit_event("NetworkFeeUpdated", [], u256(1) + u256(2))


def history(chain, rnd=None, n=60, level_at=(), events_at=()):
    """n blocks of history: fee events at `events_at` offsets, the level word toggled at `level_at` offsets."""
    for i in range(n):
        if i in events_at:
            fee_event(chain)
        if i in level_at:
            chain.write(LEVEL_SLOT, 1 - chain.storage(LEVEL_SLOT, chain.number))
        chain.mine()


def make(tmp_path, chain, check=None, start=None, clock=None, **bf):
    check = check or Watch()
    _, w = make_worker(tmp_path, [check], chain=chain, clock=clock or FakeClock(), start_block=start,
                       activation_block=start, max_log_range=4,
                       backfill={"enabled": True, "live_within_blocks": 0, "blocks_per_step": 10, **bf})
    return w, check


def sent(w, kind=None):
    rows = outbox(w, "kind != 'health'")
    return [r for r in rows if kind is None or r["kind"] == kind]


def steps_until_live(w, limit=50):
    n = 0
    while w.backfilling:
        w.step()
        n += 1
        assert n < limit, "never caught up"
    return n


# ---------------------------------------------------------------- the three required scenarios


def test_backfill_over_history_with_trigger_events_sends_nothing(tmp_path):
    chain = FakeChain()
    start = chain.number
    history(chain, events_at={3, 17, 40}, level_at={5, 30})  # level on at 5, off at 30: history only
    w, check = make(tmp_path, chain, start=start)
    assert w.phase()["phase"] == "backfill"

    w.step()
    assert w.checkpoint()["block"] == start + 9 and check.runs == []  # bounded scan; checks wait for the catch-up
    assert w.status()["phase"]["remaining_blocks"] == chain.sealed - (start + 9)
    steps_until_live(w)
    assert sent(w) == [] and w.notifier.sent == []
    assert check.runs and "backfill" in check.runs[0][1] and "force" in check.runs[0][1]
    opened = w.db.conn.execute("SELECT key, notify, alerts FROM incidents WHERE state='open'").fetchall()
    assert sorted(r["key"] for r in opened) == [f"ev:{start + i}" for i in (3, 17, 40)]
    assert all(r["notify"] == 0 and r["alerts"] == 0 for r in opened)  # tracked silently

    w.step()  # first live evaluation: the level is off, the events are history
    w.step()
    assert sent(w) == []

    fee_event(chain)  # a live event alerts
    chain.mine()
    w.step()
    rows = sent(w)
    assert [(r["severity"], r["kind"]) for r in rows] == [("page", "open")] and "event at" in rows[0]["text"]


def test_first_live_evaluation_of_a_still_true_condition_sends_once(tmp_path):
    chain = FakeChain()
    start = chain.number
    history(chain, events_at={2}, level_at={8})  # the level turns on in history and is still on
    w, check = make(tmp_path, chain, start=start)
    steps_until_live(w)
    assert sent(w) == []
    caught_up = w.phase()["caught_up_block"]

    chain.mine()
    w.step()  # the first live block after the catch-up
    assert w.checkpoint()["block"] > caught_up
    live_runs = [r for r in check.runs if "live" in r[1]]
    assert len(live_runs) == 1 and "force" in live_runs[0][1]
    rows = sent(w)
    assert [(r["severity"], r["incident_id"].split("-")[1]) for r in rows] == [("page", "level")]
    assert len(w.notifier.sent) == 1 and w.phase()["first_live"] == []

    for _ in range(3):  # still true: no duplicate before the re-alert cadence
        chain.mine()
        w.step()
    assert len(sent(w)) == 1

    chain.write(LEVEL_SLOT, 0)  # cleared: the resolution is sent (the incident was sent)
    chain.mine()
    w.clock.advance(3600)
    w.step()
    assert [r["kind"] for r in sent(w)] == ["open", "resolved"]


def test_restart_mid_backfill_stays_silent_and_resumes(tmp_path):
    chain = FakeChain()
    start = chain.number
    history(chain, n=80, events_at={1, 50}, level_at={4})
    w, _ = make(tmp_path, chain, start=start)
    w.step()
    w.step()
    stopped_at = w.checkpoint()["block"]
    assert w.backfilling and stopped_at == start + 19
    w.db.conn.close()

    w2, check2 = make(tmp_path, chain, start=start)  # a restart: same database
    assert w2.backfilling and w2.checkpoint()["block"] == stopped_at
    w2.step()
    assert w2.checkpoint()["block"] == stopped_at + 10  # resumed, not restarted from start_block
    steps_until_live(w2)
    assert sent(w2) == []
    assert w2.db.conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0] == len(chain.logs)
    chain.mine()
    w2.step()
    assert [r["incident_id"].split("-")[1] for r in sent(w2)] == ["level"]  # still true: sent once, live


# ---------------------------------------------------------------- state-table rows


def test_restart_between_go_live_and_the_first_live_run_still_runs_it(tmp_path):
    chain = FakeChain()
    start = chain.number
    history(chain, n=15, level_at={3})
    w, _ = make(tmp_path, chain, start=start)
    steps_until_live(w)
    assert w.phase()["first_live"] == ["watch"]
    w.db.conn.close()
    w2, check2 = make(tmp_path, chain, start=start)
    chain.mine()
    w2.step()
    assert any("live" in r[1] for r in check2.runs) and len(sent(w2)) == 1


def test_database_that_was_already_alerting_is_not_put_back_into_backfill(tmp_path):
    """A database with a checkpoint but no phase (written before CP3) was alerting: it stays live. A new database
    starts in backfill only when the backfill is enabled."""
    chain = FakeChain()
    start = chain.number
    history(chain, n=20, level_at={2})
    w, _ = make(tmp_path, chain, start=start)
    w.step()
    with w.db.tx():
        w.db.set_meta("phase", None)
        w.db.conn.execute("DELETE FROM meta WHERE key='phase'")
    w.db.conn.close()
    w2, _ = make(tmp_path, chain, start=start)
    assert w2.phase()["phase"] == "live" and w2.phase()["first_live"] == []
    _, w3 = make_worker(tmp_path / "off", [Watch()], chain=chain, start_block=start, activation_block=start,
                        backfill={"enabled": False})
    assert not w3.backfilling


def test_history_only_condition_resolved_before_live_is_never_sent(tmp_path):
    chain = FakeChain()
    start = chain.number
    history(chain, n=30, level_at={2})
    w, check = make(tmp_path, chain, start=start, live_within_blocks=0, blocks_per_step=10)
    w.step()
    w.step()
    # the level is on through history; turn it off before the catch-up run reads it
    chain.write(LEVEL_SLOT, 0)
    chain.mine()
    steps_until_live(w)
    chain.mine()
    w.step()
    assert sent(w) == []
    assert w.db.conn.execute("SELECT state FROM incidents WHERE key='level'").fetchall() == []  # resolve of nothing


def test_check_failing_in_its_silent_evaluation_keeps_the_backfill_and_sends_health(tmp_path):
    chain = FakeChain()
    start = chain.number
    history(chain, n=12, events_at={1}, level_at={2})
    w, check = make(tmp_path, chain, check=Watch(fail=True), start=start)
    for _ in range(4):
        w.step()
    assert w.backfilling  # no go-live while a check has not evaluated history
    health = outbox(w, "kind = 'health'")
    assert health and "check-error" in health[0]["text"]  # health is about the present: sent while backfilling
    assert sent(w) == []
    check.fail = False
    w.step()
    assert not w.backfilling and sent(w) == []
    chain.mine()
    w.step()
    assert len(sent(w)) == 1


def test_backfill_steps_run_back_to_back_only_while_blocks_remain(tmp_path):
    chain = FakeChain()
    start = chain.number
    history(chain, n=30)
    w, check = make(tmp_path, chain, check=Watch(fail=True), start=start)  # poll_interval_s 30 (default)
    w.step()
    assert w.backfilling and w.wait_s() == 0  # blocks left to scan: next step at once
    while not w.caught_up(w.checkpoint()["block"]):
        w.step()
    w.step()
    assert w.backfilling and w.wait_s() == 30  # caught up, a check keeps failing: no tight retry loop
    chain.down = True
    w.step()
    assert w.wait_s() == 30
    chain.down = False
    check.fail = False
    w.step()
    assert not w.backfilling and w.wait_s() == 30


def test_lag_and_digest_are_quiet_and_phase_is_exposed_while_backfilling(tmp_path):
    chain = FakeChain()
    start = chain.number
    history(chain, n=120)
    _, w = make_worker(tmp_path, [Watch()], chain=chain, clock=FakeClock(), start_block=start, activation_block=start,
                       max_log_range=4, lag_alert_blocks=5,
                       backfill={"enabled": True, "live_within_blocks": 0, "blocks_per_step": 10})
    w.alerts.digest_interval_s = 1
    w.step()
    assert w.db.meta("lag_blocks") > 5 and outbox(w) == []  # no lag health, no digest
    st = json.loads((tmp_path / "w.status.json").read_text())
    assert st["healthy"] and st["phase"]["phase"] == "backfill" and 0 < st["phase"]["progress"] < 1
    conn = sqlite3.connect(tmp_path / "w.sqlite")
    text = render(conn, ["watch"])
    assert "ssv_watch_backfill 1" in text and f"ssv_watch_backfill_remaining_blocks {chain.sealed - start - 9}" in text
    steps_until_live(w)
    text = render(conn, ["watch"])
    assert "ssv_watch_backfill 0" in text and "ssv_watch_backfill_remaining_blocks 0" in text
    w.clock.advance(10)
    w.step()
    kinds = [r["kind"] for r in outbox(w)]
    assert kinds and set(kinds) == {"digest"}  # the digest starts with the live phase


def test_rpc_outage_while_backfilling_is_reported(tmp_path):
    chain = FakeChain()
    start = chain.number
    history(chain, n=40)
    w, _ = make(tmp_path, chain, start=start)
    w.step()
    chain.down = True
    for _ in range(3):
        w.step()
    assert [r["text"].split("\n")[0] for r in outbox(w, "kind='health'")] == ["🩺 [HEALTH] SSV watch: rpc — test"]
    chain.down = False
    w.step()
    assert any("rpc-recovered" in r["text"] for r in outbox(w, "kind='health'"))


def test_reorg_while_backfilling_stays_silent(tmp_path):
    chain = FakeChain()
    start = chain.number
    history(chain, n=30, events_at={20}, level_at={1})
    w, _ = make(tmp_path, chain, start=start, blocks_per_step=100)
    w.step()  # caught up and evaluated silently in one step -> live from the next
    assert not w.backfilling
    w2_chain = FakeChain()  # a fresh database that is backfilling when the chain reorganises
    start2 = w2_chain.number
    history(w2_chain, n=30, events_at={20, 25}, level_at={1})
    w2, _ = make(tmp_path / "b", w2_chain, start=start2, blocks_per_step=24)
    w2.step()
    w2_chain.reorg(start2 + 22)
    history(w2_chain, n=10, events_at={3})
    steps_until_live(w2)
    reorg_health = [r for r in outbox(w2, "kind='health'") if "reorg" in r["text"]]
    assert sent(w2) == [] and len(reorg_health) <= 1


def test_m5_confirmed_in_history_and_still_open_pages_at_the_first_live_run(tmp_path):
    chain, start = poc_chain()  # the #94775 trace: a confirmed shortfall, still uncorrected
    _, w = make_worker(tmp_path, chain=chain, start_block=start, activation_block=start,
                       checks={"m5": {"sweep_interval_s": 0, "full_sweep_interval_s": 0}},
                       backfill={"enabled": True, "live_within_blocks": 0, "blocks_per_step": 3})
    steps_until_live(w)
    assert outbox(w) == [] and w.notifier.sent == []
    chain.mine()
    w.step()
    rows = outbox(w)
    assert [(r["check_id"], r["severity"]) for r in rows] == [("m5", "page")]
    chain.mine()
    w.step()
    assert len(outbox(w)) == 1


# ---------------------------------------------------------------- seeded sweep


@pytest.mark.parametrize("seed", range(24))
def test_seeded_backfill_sweep(tmp_path, seed):
    """Random history (events, level toggles), scan bounds, window sizes and restart points during the backfill
    and around the go-live. Invariants: nothing but health is sent before the first live run; at the first live
    run the level page is sent iff the level holds there; a history event is never sent; every live event is
    sent exactly once; the stored logs equal the chain's."""
    rnd = random.Random(seed)
    chain = FakeChain()
    start = chain.number
    n = rnd.randint(20, 90)
    history(chain, n=n, events_at=set(rnd.sample(range(n), rnd.randint(0, 6))),
            level_at=set(rnd.sample(range(n), rnd.randint(0, 4))))
    bf = {"live_within_blocks": rnd.choice([0, 2, 12]), "blocks_per_step": rnd.choice([3, 10, 40, 1000])}
    clock = FakeClock()
    w, _ = make(tmp_path, chain, start=start, clock=clock, **bf)
    restarts = set(rnd.sample(range(12), 3))
    i = 0
    while w.backfilling:
        assert sent(w) == []
        if i in restarts:
            w.db.conn.close()
            w, _ = make(tmp_path, chain, start=start, clock=clock, **bf)
        w.step()
        i += 1
        assert i < 100
    assert sent(w) == []
    caught_up = w.phase()["caught_up_block"]
    if rnd.random() < 0.5:  # restart between the go-live and the first live run
        w.db.conn.close()
        w, _ = make(tmp_path, chain, start=start, clock=clock, **bf)
    chain.mine()
    w.step()
    cp = w.checkpoint()["block"]
    level = chain.storage(LEVEL_SLOT, cp) == 1
    # events after the caught-up checkpoint are live (alerting starts at the first block after the catch-up)
    after = {f"ev:{int(lg['blockNumber'], 16)}" for lg in chain.logs if caught_up < int(lg["blockNumber"], 16) <= cp}
    keys = sorted(w.db.conn.execute("SELECT key FROM incidents WHERE id=?", (r["incident_id"],)).fetchone()[0]
                  for r in sent(w))
    assert keys == sorted(after | ({"level"} if level else set()))
    live_events = []
    for _ in range(rnd.randint(1, 3)):
        fee_event(chain)
        live_events.append(chain.number)
        chain.mine(rnd.randint(1, 3))
        w.step()
    texts = [r["text"] for r in sent(w)]
    for b in live_events:
        assert sum(f"event at {b}" in t for t in texts) == 1
    assert not any(f"event at {b}" in t for t in texts for b in range(start, caught_up + 1))
    assert w.db.conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0] == len(chain.logs)
    assert isinstance(w.notifier, Capture)


# ---------------------------------------------------------------- QA CP3 attempt 1 F1: per-check silent phase


class Flaky(Check):
    """A check whose silent run fails while `fail` is set (an input that stays broken for a while)."""

    id = "flaky"

    def __init__(self):
        super().__init__({}, {})
        self.schedule = (Every(60),)
        self.fail = True

    def run(self, ctx, block):
        if self.fail:
            raise RuntimeError("cannot evaluate (e.g. an input that stays broken for a while)")
        return []


def with_flaky(tmp_path, chain, start, checks):
    _, w = make_worker(tmp_path, checks, chain=chain, clock=FakeClock(), start_block=start, activation_block=start,
                       max_log_range=4, backfill={"enabled": True, "live_within_blocks": 0, "blocks_per_step": 10})
    return w


def test_live_event_while_another_check_blocks_go_live_is_paged(tmp_path):
    """D17: alerting starts at the first live block after catch-up. A page-worthy event mined AFTER the checkpoint
    caught up, while one other check keeps failing its silent run, is sent (red on 91ca3a7: opened silently, never
    sent)."""
    chain = FakeChain()
    start = chain.number
    history(chain, n=30)
    watch, flaky = Watch(), Flaky()
    w = with_flaky(tmp_path, chain, start, [watch, flaky])
    for _ in range(10):
        w.step()
    assert w.backfilling and "watch" in w.phase()["evaluated"] and "flaky" not in w.phase()["evaluated"]
    caught = w.checkpoint()["block"]
    assert w.phase()["history_block"] == caught
    fee_event(chain)  # live blocks after the catch-up: a page-worthy event
    ev_block = chain.number
    chain.mine()
    for _ in range(3):
        chain.mine()
        w.clock.advance(60)
        w.step()
    assert w.checkpoint()["block"] > ev_block > caught
    assert w.backfilling and any(f"event at {ev_block}" in r["text"] for r in sent(w))  # sent while flaky fails
    assert any("check-error" in r["text"] and "flaky" in r["text"] for r in outbox(w, "kind='health'"))
    flaky.fail = False  # the broken input recovers
    for _ in range(5):
        chain.mine()
        w.clock.advance(3600)
        w.step()
    assert not w.backfilling
    assert sum(f"event at {ev_block}" in r["text"] for r in sent(w)) == 1, sent(w)


def test_m13_top_up_after_catch_up_while_another_check_blocks_go_live(tmp_path):
    """The same with the real M13 check: a retired-key top-up mined after the checkpoint caught up."""
    from fixtures_wallets import FUNDER, RETIRED_SLOT4, WalletChain, eth, wallet_worker

    chain = WalletChain()
    chain.set_balance(FUNDER, eth(100))
    chain.set_balance(RETIRED_SLOT4, eth(0.01))
    chain.mine()
    start = chain.number
    for _ in range(20):
        chain.mine()
    chain, w = wallet_worker(tmp_path, ["m13"], chain=chain, start_block=start, activation_block=start,
                             backfill={"enabled": True, "live_within_blocks": 0, "blocks_per_step": 10})
    flaky = Flaky()
    w.checks["flaky"] = flaky
    for _ in range(6):
        w.clock.advance(60)
        w.step()
    assert w.backfilling and "m13" in w.phase()["evaluated"], w.phase()
    chain.send(FUNDER, RETIRED_SLOT4, gas_used=21_000, value=eth(0.5))
    for _ in range(3):
        chain.mine()
        w.clock.advance(60)
        w.step()
    flaky.fail = False
    for _ in range(5):
        chain.mine()
        w.clock.advance(600)
        w.step()
    assert not w.backfilling
    assert outbox(w, "severity='page'"), w.db.conn.execute("SELECT key, notify, state FROM incidents").fetchall()


def test_m13_top_up_and_drain_after_catch_up_while_another_check_blocks_go_live(tmp_path):
    """A retired key topped up and drained in the next block, both after the checkpoint caught up while another
    check keeps failing its silent run: paged as in live mode (red on 91ca3a7: no message)."""
    from fixtures_wallets import FUNDER, RETIRED_SLOT4, WalletChain, eth, wallet_worker

    def run(blocked):
        chain = WalletChain()
        chain.set_balance(FUNDER, eth(100))
        chain.set_balance(RETIRED_SLOT4, eth(0.01))
        chain.mine()
        start = chain.number
        for _ in range(20):
            chain.mine()
        p = tmp_path / ("b" if blocked else "l")
        p.mkdir()
        chain, w = wallet_worker(p, ["m13"], chain=chain, start_block=start, activation_block=start,
                                 backfill={"enabled": True, "live_within_blocks": 0, "blocks_per_step": 10})
        flaky = Flaky()
        flaky.fail = blocked
        w.checks["flaky"] = flaky
        for _ in range(6):
            w.clock.advance(60)
            w.step()
        assert w.backfilling == blocked
        chain.send(FUNDER, RETIRED_SLOT4, gas_used=21_000, value=eth(0.5))
        chain.mine()
        chain.send(RETIRED_SLOT4, "0xaee058cca5eed5470b4b48c159045b7c25ddb80f", gas_used=21_000, value=eth(0.505))
        for _ in range(3):
            chain.mine()
            w.clock.advance(60)
            w.step()
        flaky.fail = False
        for _ in range(5):
            chain.mine()
            w.clock.advance(600)
            w.step()
        assert not w.backfilling
        return [(r["severity"], r["kind"]) for r in outbox(w, "kind NOT IN ('health','digest')")]

    control = run(False)
    assert ("page", "open") in control  # live mode pages
    assert run(True) == control, "top-up after the catch-up not paged as in live mode"


def test_restart_mid_scan_and_mid_silent_evaluation_stays_silent_then_live_once(tmp_path):
    """Restart (new Worker on the same database) mid-scan and again between two checks' silent runs; history has
    events and a level that turned on and stays on: nothing before the first block after the catch-up, the level
    once at the first live run, a live event once, no history event ever."""
    chain = FakeChain()
    start = chain.number
    history(chain, n=45, events_at={4, 20, 33}, level_at={10})

    def new(fail_flaky):
        flaky = Flaky()
        flaky.fail = fail_flaky
        return with_flaky(tmp_path, chain, start, [Watch(), flaky])

    w = new(True)
    w.step()
    w.step()
    assert w.backfilling and w.checkpoint()["block"] < chain.sealed
    w = new(True)  # restart mid-scan
    for _ in range(6):
        w.clock.advance(60)
        w.step()
    assert w.backfilling and "watch" in w.phase()["evaluated"] and "flaky" not in w.phase()["evaluated"]
    assert sent(w) == []
    w = new(False)  # restart between the silent runs; flaky now works
    w.clock.advance(60)
    w.step()
    assert not w.backfilling and sent(w) == []  # no block after the caught-up checkpoint yet
    chain.mine()
    w.clock.advance(3600)
    w.step()
    msgs = [r["text"].split("\n")[0] for r in sent(w)]
    assert len(msgs) == 1 and "] level —" in msgs[0], msgs
    fee_event(chain)
    chain.mine()
    w.clock.advance(60)
    w.step()
    for _ in range(3):
        chain.mine()
        w.clock.advance(3600)
        w.step()
    msgs = [r["text"].split("\n")[0] for r in sent(w)]
    assert len(msgs) == 2 and "event at" in msgs[1], msgs
    assert chain.storage(LEVEL_SLOT, chain.number) == 1


def test_one_transient_rpc_error_in_a_silent_run_silences_the_next_live_event(tmp_path):
    """A single RpcError in one check's silent run (public-RPC 429s were seen in the CP3 local run): the event mined
    in the next block, after the catch-up, is sent (red on 91ca3a7: evaluated silently, never sent)."""

    class OnceRpcFail(Flaky):
        id = "oncerpc"

        def run(self, ctx, block):
            if self.fail:
                self.fail = False
                raise RpcError("transport error: all 3 RPC endpoints failed (HTTP 429)", transport=True)
            return []

    chain = FakeChain()
    start = chain.number
    history(chain, n=30)
    w = with_flaky(tmp_path, chain, start, [Watch(), OnceRpcFail()])
    while w.checkpoint()["block"] < chain.sealed or "watch" not in w.phase().get("evaluated", ()):
        w.step()
    assert w.backfilling and w.rpc_failures == 1
    fee_event(chain)  # mined after the checkpoint caught up
    ev = chain.number
    chain.mine()
    w.clock.advance(30)
    w.step()  # oncerpc now succeeds: its silent run is pinned to the caught-up checkpoint, below the event
    assert not w.backfilling and w.phase()["history_block"] < ev <= w.phase()["caught_up_block"]
    for _ in range(3):
        chain.mine()
        w.clock.advance(3600)
        w.step()
    assert sum(f"event at {ev}" in r["text"] for r in sent(w)) == 1, sent(w)


def test_lag_alert_starts_at_the_caught_up_checkpoint_while_a_check_still_fails(tmp_path):
    """The lag health alert is off only while history is still being stored, not while a caught-up backfill waits
    for a check that keeps failing its silent run."""
    chain = FakeChain()
    start = chain.number
    history(chain, n=30)
    _, w = make_worker(tmp_path, [Watch(), Flaky()], chain=chain, clock=FakeClock(), start_block=start,
                       activation_block=start, max_log_range=4, lag_alert_blocks=5,
                       backfill={"enabled": True, "live_within_blocks": 0, "blocks_per_step": 10})
    for _ in range(4):
        w.step()
    assert w.backfilling and w.phase()["history_block"] is not None
    assert not any("lag" in r["text"] for r in outbox(w, "kind='health'"))
    real = w.scan
    w.scan = lambda: 0  # the scan stalls (the checkpoint does not advance) while the chain moves on
    chain.mine(10)
    w.step()
    w.scan = real
    assert any(": lag —" in r["text"] for r in outbox(w, "kind='health'")), outbox(w, "kind='health'")


class Failing(Check):
    """Fails one run per attempt while `state["left"]` > 0: an exception, or a transport RpcError (`state["rpc"]`).
    The state outlives a restart (a new instance on the same dict)."""

    id = "failing"

    def __init__(self, state):
        super().__init__({}, {})
        self.schedule = (Every(60),)
        self.state = state
        self.runs: list[int] = []

    def run(self, ctx, block):
        if self.state["left"] > 0:
            self.state["left"] -= 1
            if self.state["rpc"]:
                raise RpcError("transport error: all 3 RPC endpoints failed (HTTP 429)", transport=True)
            raise RuntimeError("cannot evaluate")
        self.runs.append(block)
        return []


def opened(w) -> list[str]:
    """Incident keys of the `open` messages sent so far, in order."""
    return [r[0] for r in w.db.conn.execute(
        "SELECT i.key FROM outbox o JOIN incidents i ON i.id = o.incident_id WHERE o.kind = 'open' "
        "ORDER BY o.created_at, o.rowid")]


@pytest.mark.parametrize("seed", range(24))
def test_seeded_backfill_sweep_with_a_check_failing_its_silent_run(tmp_path, seed):
    """The sweep above with a second check whose silent run fails (an exception or a transport RpcError) for some
    steps after the catch-up, while live blocks with events are mined and the worker restarts. Invariants: nothing
    but health is sent until a block after the caught-up checkpoint; the healthy check alerts from its first run
    after its own silent evaluation, whatever the failing check does (after every step, every event after the
    caught-up checkpoint is sent, exactly once); a history event is never sent; the failing check raises
    check-error health, evaluates history pinned to the caught-up checkpoint, then runs live; the level is
    reported iff it still holds."""
    rnd = random.Random(1000 + seed)
    chain = FakeChain()
    start = chain.number
    n = rnd.randint(20, 70)
    history(chain, n=n, events_at=set(rnd.sample(range(n), rnd.randint(0, 5))),
            level_at=set(rnd.sample(range(n), rnd.randint(0, 3))))
    bf = {"enabled": True, "live_within_blocks": rnd.choice([0, 2, 12]), "blocks_per_step": rnd.choice([3, 10, 1000])}
    clock = FakeClock()
    fails = {"left": rnd.randint(1, 6), "rpc": rnd.random() < 0.5}
    failing_first = rnd.random() < 0.5

    def new():
        watch, failing = Watch(), Failing(fails)
        _, w = make_worker(tmp_path, [failing, watch] if failing_first else [watch, failing], chain=chain,
                           clock=clock, start_block=start, activation_block=start, max_log_range=4, backfill=bf)
        return w, failing

    def live_events_scanned(w, history_block):
        cp = w.checkpoint()["block"]
        return sorted(f"ev:{b}" for b in {int(lg["blockNumber"], 16) for lg in chain.logs} if history_block < b <= cp)

    w, failing = new()
    restarts = set(rnd.sample(range(25), 3))
    history_block = None  # the caught-up checkpoint: where the healthy check evaluated history
    i = 0
    while w.backfilling:
        if i in restarts:
            w.db.conn.close()
            w, failing = new()
        if history_block is not None and rnd.random() < 0.7:  # live blocks while the failing check holds the phase
            if rnd.random() < 0.6:
                fee_event(chain)
            chain.mine(rnd.randint(1, 2))
        clock.advance(60)
        w.step()
        i += 1
        assert i < 200
        if history_block is None and w.phase().get("evaluated"):
            history_block = w.checkpoint()["block"]
        if history_block is None:
            assert sent(w) == []
        else:  # the healthy check is live after its silent run, the failing one does not hold it back
            assert sorted(k for k in opened(w) if k.startswith("ev:")) == live_events_scanned(w, history_block)
    if not fails["rpc"]:
        assert any("check-error" in r["text"] for r in outbox(w, "kind='health'"))
    for _ in range(2):  # every check's first live run, then a full interval run
        chain.mine()
        clock.advance(3600)
        w.step()
    events = [k for k in opened(w) if k.startswith("ev:")]
    assert len(events) == len(set(events)) and sorted(events) == live_events_scanned(w, history_block)
    assert w.phase()["history_block"] == history_block
    assert failing.runs[0] == history_block < failing.runs[1]  # history pinned to the caught-up checkpoint, then live
    cp = w.checkpoint()["block"]
    level = chain.storage(LEVEL_SLOT, cp) == 1
    inc = w.db.conn.execute("SELECT notify, alerts FROM incidents WHERE key='level' AND state='open'").fetchone()
    assert (inc is not None and inc["notify"] == 1 and inc["alerts"] >= 1) if level else inc is None
    assert w.db.conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0] == len(chain.logs)


# ---------------------------------------------------------------- operator commands


def test_status_and_export_answer_while_a_run_holds_the_write_lock(tmp_path, capsys):
    """`status` and `export` open the database read-only: a check's long run (one write transaction) does not block
    them (before CP3 they waited for the lock and failed with "database is locked")."""
    import time
    from pathlib import Path

    import eb_reconcile

    raw = json.loads((Path(eb_reconcile.__file__).parent / "config.example.json").read_text())
    raw.update(db_path=str(tmp_path / "w.sqlite"))  # the inbox path need not exist: status does not read it
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(raw))
    chain = FakeChain()
    start = chain.number
    history(chain, n=40)
    w, _ = make(tmp_path, chain, start=start)
    w.step()
    writer = sqlite3.connect(tmp_path / "w.sqlite", isolation_level=None)
    writer.execute("BEGIN IMMEDIATE")  # what a running check's transaction holds
    try:
        t0 = time.monotonic()
        assert eb_reconcile.main(["status", "--config", str(cfg_path)]) == 0
        status = json.loads(capsys.readouterr().out)
        assert eb_reconcile.main(["export", "--config", str(cfg_path)]) == 0
        export = json.loads(capsys.readouterr().out)
        assert time.monotonic() - t0 < 10
    finally:
        writer.execute("ROLLBACK")
    assert status["phase"]["phase"] == "backfill" and status["checkpoint"]["block"] == w.checkpoint()["block"]
    assert set(export) == {"incidents", "outbox"}
