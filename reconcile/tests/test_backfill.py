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
