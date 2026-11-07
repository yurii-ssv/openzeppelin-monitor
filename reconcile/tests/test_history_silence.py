"""Durable historical silence, CP2 repair 1 (plan check-switches, D15; QA cp2-round-1 QF1-QF3).

An event, or a condition that appeared and healed, at or below a check's silent boundary (its per-check catch-up
block, durable as `silent:<id>`, or the global `phase.history_block`) is never sent: also after the catch-up record is
deleted or `phase.first_live` is consumed, and after any reorg the worker handles. A reorg that rewinds a check's
event cursor below its boundary makes the check repeat its silent evaluation up to the boundary (a catch-up record
with cause `reorg` when live; leaving `phase.evaluated` while backfilling), then run live, forced. Incidents a live run
saw above the boundary are not judged by that silent repeat (no resolution, no re-find applied); the live run after it
judges them as a reorg run from the boundary. A check re-enabled while the database backfills, once the caught-up
checkpoint is known, is evaluated again silently by the global path, pinned at the checkpoint of its first step.

The first eleven cases (`test_nf1_*`, `test_nf2_*[global]`, `test_nf3_*[True]`) are QA's reproducers
(`qa/cp2-round-1/test_cp2_independent.py`) made permanent; the other QA cases are its controls. Behavioural
assertions come first, so a revision without the mechanism fails on the message it exists to prevent.
"""

from __future__ import annotations

import pytest
from fakechain import PROXY
from test_catchup import CTL, IDS, Env, _register

import checks as registry
from checks.base import Finding
from rpc import RpcError


@pytest.fixture
def ctl():
    """test_catchup's checks cu_a/cu_b (a level rule and an event rule each), registered while a test runs."""
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


def keys(env, after=0, cid="cu_a"):
    return [m["ikey_"] for m in env.msgs(cid, after=after)]


def restore_head(env, old_head, extra=1):
    """Mine the replacing chain past the old head, then step once on it (the reorg is found and handled)."""
    env.chain.mine(max(old_head - env.chain.sealed + extra, 1))
    env.step(mine=False)


# ---------------------------------------------------------------- QA reproducers (QF1/NF1, QF2/NF2, QF3/NF3)


@pytest.mark.parametrize("confirmations", [0, 2, 12])
@pytest.mark.parametrize("cause", ["new", "reenabled", "global"])
def test_nf1_history_event_stays_muted_after_record_deleted_and_deep_reorg(tmp_path, ctl, confirmations, cause):
    env = Env(tmp_path, confirmations=confirmations)
    if cause == "global":
        event = env.event("cu_a")
        env.chain.mine(4 + confirmations)
        env.start(only=("cu_a",))
        env.w.cfg.raw["max_log_range"] = 1  # retain a recorded common ancestor below the replay
        env.step()
        assert env.w.phase()["phase"] == "live"
        pin = env.w.phase()["history_block"]
    else:
        env.go_live(only=("cu_b",) if cause == "new" else IDS)
        if cause == "reenabled":
            env.start(disabled=("cu_a",))
        event = env.event("cu_a")
        env.chain.mine(2 + confirmations)
        env.step()
        env.start()
        env.step()
        pin = env.rec("cu_a")["history_block"]
    assert event <= pin and keys(env) == []
    original = next(r for r in env.rows("cu_a") if r["key"] == f"ev:{event}")
    assert original["notify"] == original["alerts"] == 0
    env.step()  # first live commit deletes record / clears first_live
    assert env.rec("cu_a") is None and keys(env) == []
    assert "cu_a" not in env.w.phase()["first_live"]
    env.start(only=("cu_a",) if cause == "global" else IDS)  # no lost in-memory state excuse
    old_head = env.chain.sealed
    env.chain.reorg(event)  # common ancestor precedes history event and pin
    assert env.event("cu_a") == event  # same event rule/data, re-included at its historical height
    restore_head(env, old_head)
    actual = keys(env)
    assert actual == [], f"historical catch-up event sent after deep reorg: {actual}"
    replayed = next(r for r in env.rows("cu_a") if r["id"] == original["id"])
    assert replayed["state"] == "open" and replayed["notify"] == replayed["alerts"] == 0  # not unmuted
    assert env.rec("cu_a") is None  # the repeat's record is gone after its forced live run
    if cause != "global":
        assert env.w.db.meta("silent:cu_a") == pin  # the boundary is durable and unchanged


@pytest.mark.parametrize("path", ["global", "percheck"])
def test_nf2_reorg_before_first_live_keeps_history_silent_and_later_event_live(tmp_path, ctl, path):
    env = Env(tmp_path)
    if path == "global":
        env.start(only=("cu_a",))
        env.w.cfg.raw["max_log_range"] = 1  # scan records a usable common ancestor
        env.chain.mine(3)
        env.step()
        pin = env.w.phase()["history_block"]
    else:
        env.go_live(only=("cu_a",))
        env.start(only=("cu_a",), disabled=("cu_a",))
        env.step()
        env.start(only=("cu_a",))
        env.step()
        pin = env.rec("cu_a")["history_block"]
    old_head = env.chain.sealed
    env.start(only=("cu_a",))
    env.chain.reorg(pin)
    historical = env.event("cu_a")
    env.chain.mine()
    live = env.event("cu_a")
    restore_head(env, old_head)
    actual = keys(env)
    assert actual == [f"ev:{live}"], f"history replay escaped silence: {actual}"
    runs = env.check("cu_a").runs
    assert {"backfill", "force", "reorg"} <= runs[0]["reasons"] and runs[0]["block"] == pin
    assert historical in runs[0]["events"] and {"live", "force"} <= runs[1]["reasons"]


@pytest.mark.parametrize("reenable_before_live", [True, False])
def test_nf3_disabled_interval_stays_silent_after_flip_in_global_backfill(tmp_path, ctl, reenable_before_live):
    env = Env(tmp_path)
    ctl["cu_b"]["fail"] = 30
    env.start()
    env.step()
    assert env.w.backfilling and "cu_a" in env.w.phase()["evaluated"]
    env.start(disabled=("cu_a",))
    disabled_event = env.event("cu_a")
    env.step()
    if reenable_before_live:
        env.start()
        assert env.w.backfilling and env.w.db.meta("disabled:cu_a") is None
        ph, rec = env.w.phase(), env.rec("cu_a")  # asserted after the behavioural oracle (D15 (c), below)
    else:  # benign control: re-enable after go-live uses the per-check path
        ctl["cu_b"]["fail"] = 0
        env.step()
        assert not env.w.backfilling
        env.start()
        assert env.rec("cu_a")["state"] == "pending"
    env.step()
    actual = keys(env)
    assert f"ev:{disabled_event}" not in actual, f"disabled-interval event sent on first live run: {actual}"
    if reenable_before_live:  # evaluated again silently, pinned at the checkpoint of that step; live after it
        # D15 (c): at the re-enabling start it left `evaluated`/`first_live` and got a pending record that pins its
        # new silent evaluation (QA's reproducer asserted the defective candidate's state there: no record)
        assert "cu_a" not in ph["evaluated"] and "cu_a" not in ph["first_live"]
        assert rec is not None and rec["state"] == "pending" and rec["cause"] == "reenabled"
        pin = env.w.checkpoint()["block"]
        assert env.rec("cu_a") is None and env.w.db.meta("silent:cu_a") == pin
        assert len(env.check("cu_a").runs) == 1  # no live run at its pin
        assert {"backfill", "force"} <= env.check("cu_a").runs[0]["reasons"]
        assert env.check("cu_a").runs[0]["events"] == [disabled_event]
        assert "cu_a" in env.w.phase()["evaluated"] and env.w.backfilling
        live = env.event("cu_a")
        env.step()
        assert keys(env) == [f"ev:{live}"] and {"live", "force"} <= env.check("cu_a").runs[1]["reasons"]
        ctl["cu_b"]["fail"] = 0
        env.step()
        env.step()
        assert keys(env) == [f"ev:{live}"] and not env.w.backfilling


@pytest.mark.parametrize("stage", ["pending", "first_live"])
@pytest.mark.parametrize("failure", ["runtime", "rpc", "partial"])
def test_partial_or_failed_run_is_atomic_and_first_live_retries_after_restart(tmp_path, ctl, monkeypatch, stage,
                                                                               failure):
    env = Env(tmp_path)
    env.go_live(only=("cu_b",))
    old = env.event("cu_a")
    env.level("cu_a", 0, 1)
    env.step()
    env.start()
    if stage == "first_live":
        env.step()
    before = env.rec("cu_a")
    original_run = type(env.check("cu_a")).run

    def broken(self, ctx, block):
        if failure == "partial":
            yield Finding(check=self.id, key="uncommitted", severity="page", title="partial", body="")
        if failure == "rpc":
            raise RpcError("independent transport failure", transport=True)
        raise RuntimeError("independent failure after partial input")

    monkeypatch.setattr(type(env.check("cu_a")), "run", broken)
    env.event("cu_b")
    env.step()
    pinned = env.rec("cu_a")
    assert pinned["state"] == before["state"]
    assert not any(r["key"] == "uncommitted" for r in env.rows("cu_a"))
    assert keys(env) == []
    assert len(keys(env, cid="cu_b")) == 1  # unrelated live page is never held
    if stage == "pending":
        assert pinned["history_block"] == env.w.checkpoint()["block"]
    else:
        assert pinned == before
    monkeypatch.setattr(type(env.check("cu_a")), "run", original_run)
    env.start()
    assert env.rec("cu_a") == pinned
    env.step()
    assert keys(env) == ["lv0"] and f"ev:{old}" not in keys(env)
    assert env.rec("cu_a") is None
    env.step()
    assert keys(env) == ["lv0"]


def test_empty_success_is_silent_then_forces_live_and_removes_record(tmp_path, ctl):
    env = Env(tmp_path)
    env.go_live(only=("cu_b",))
    env.start()
    env.step()
    pin = env.rec("cu_a")["history_block"]
    assert env.rec("cu_a")["state"] == "first_live" and keys(env) == []
    env.start()
    env.step(mine=False)
    assert env.rec("cu_a")["history_block"] == pin and keys(env) == []
    env.level("cu_a", 0, 1)
    env.step()
    assert keys(env) == ["lv0"] and env.rec("cu_a") is None


def test_catchup_planning_and_d7_marker_clear_roll_back_together(tmp_path, ctl, monkeypatch):
    env = Env(tmp_path)
    env.go_live()
    env.level("cu_a", 0, 1)
    env.step()
    env.start(disabled=("cu_a",))
    marker = env.w.db.meta("disabled:cu_a")
    frozen = env.rows("cu_a")
    w = env.start(bootstrap=False)
    original = w.db.set_meta

    def broken(key, value):
        original(key, value)
        if key == "catchup:cu_a":
            raise RuntimeError("crash after catchup write, before marker clear")

    monkeypatch.setattr(w.db, "set_meta", broken)
    with pytest.raises(RuntimeError):
        w.bootstrap()
    assert w.db.meta("disabled:cu_a") == marker and env.rec("cu_a") is None
    assert env.rows("cu_a") == frozen
    env.start()
    assert env.rec("cu_a")["state"] == "pending" and env.w.db.meta("disabled:cu_a") is None
    env.step()
    assert keys(env) == ["lv0"]  # only the page before disable


# ---------------------------------------------------------------- the repeat after the first live run (D15 (a))


def _boundary(env, path):
    """cu_a with a history event `hist` at or below its silent boundary `pin`, before its first live run: per check
    (cu_a new on a live database where cu_b runs; boundary = its catch-up block) or global (both evaluated by the
    global backfill; boundary = `phase.history_block`). Returns (hist, pin)."""
    if path == "global":
        hist = env.event("cu_a")
        env.chain.mine(2)
        env.start()
        env.step()
        assert env.w.phase()["phase"] == "live"
        return hist, env.w.phase()["history_block"]
    env.go_live(only=("cu_b",))
    hist = env.event("cu_a")
    env.step()
    env.start()
    env.step()
    return hist, env.rec("cu_a")["history_block"]


@pytest.mark.parametrize("path", ["percheck", "global"])
@pytest.mark.parametrize("level_at", ["above_the_boundary", "history_height"])
def test_repeat_does_not_resolve_or_resend_what_a_live_run_already_sent(tmp_path, ctl, level_at, path):
    """After cu_a's first live run sent a live event above its boundary and a level, a reorg below the boundary
    replays the history event, the live event at its height and the level (written above the boundary: false at it,
    or at the history event's height: true at it). The silent repeat at the boundary does not judge the incidents last
    seen above it (no "evidence left the canonical chain" resolution, no "resolved" for the level, no re-find applied);
    the live run after it re-finds them: nothing is sent at all."""
    env = Env(tmp_path, max_log_range=1)
    hist, pin = _boundary(env, path)
    live = env.event("cu_a")
    env.level("cu_a", 1, 1)  # above the boundary
    env.step()
    assert sorted(keys(env)) == sorted([f"ev:{live}", "lv1"]) and env.rec("cu_a") is None
    env.step()
    open_before = {r["key"]: r["id"] for r in env.rows("cu_a", "open")}
    mark, old_head = env.last_rowid(), env.chain.sealed
    env.start()
    env.chain.reorg(hist)
    assert env.event("cu_a") == hist
    if level_at == "history_height":
        env.level("cu_a", 1, 1)
    env.chain.mine(live - hist)
    assert env.event("cu_a") == live
    env.level("cu_a", 1, 1)
    restore_head(env, old_head)
    # behavioural assertion first (red without the deferral: the repeat resolves the live incidents and the live
    # run opens them again)
    assert env.msgs(after=mark) == [], [(m["kind"], m["ikey_"]) for m in env.msgs(after=mark)]
    runs = env.check("cu_a").runs
    assert runs[-2]["block"] == pin and {"backfill", "force", "reorg"} <= runs[-2]["reasons"]
    assert {"live", "force", "reorg"} <= runs[-1]["reasons"] and runs[-1]["ancestor"] == pin
    assert {r["key"]: r["id"] for r in env.rows("cu_a", "open")} == open_before  # the same incidents, still open
    assert env.w.db.meta("reorg:cu_a") is None and env.rec("cu_a") is None


@pytest.mark.parametrize("path", ["percheck", "global"])
def test_live_event_orphaned_by_a_reorg_below_the_boundary_is_resolved_once_by_the_live_run(tmp_path, ctl, path):
    """The live event above the boundary is not on the replacing chain: the silent repeat leaves it (a deferred key,
    `reorg:<id>` = the boundary), the live run after it resolves it as "evidence left the canonical chain", once."""
    env = Env(tmp_path, max_log_range=1)
    hist, pin = _boundary(env, path)
    live = env.event("cu_a")
    env.step()
    assert keys(env) == [f"ev:{live}"]
    (live_row,) = [r for r in env.rows("cu_a") if r["key"] == f"ev:{live}"]
    mark, old_head = env.last_rowid(), env.chain.sealed
    env.start()
    env.chain.reorg(hist)
    assert env.event("cu_a") == hist
    restore_head(env, old_head)
    sent = env.msgs(after=mark)
    # behavioural assertion first (red when the repeat clears the reorg marker: the orphaned incident stays open)
    assert [(m["kind"], m["incident_id"]) for m in sent] == [("resolved", live_row["id"])]
    assert "left the canonical chain" in sent[0]["text"]
    assert env.w.db.meta("reorg:cu_a") is None
    env.step(7 * 3600)
    assert len(env.msgs(after=mark)) == 1  # no re-alert of the orphaned evidence


@pytest.mark.parametrize("path", ["percheck", "global"])
def test_condition_new_on_the_replacing_chain_below_the_boundary_and_still_true_is_sent_once(tmp_path, ctl, path):
    """D15 keeps D4's rule for a repeat: a level the replacing chain turns on below the boundary and that is still on
    at the live run after the repeat is sent once there; a history event stays silent."""
    env = Env(tmp_path, max_log_range=1)
    hist, pin = _boundary(env, path)
    env.step()
    env.step()
    mark, old_head = env.last_rowid(), env.chain.sealed
    env.start()
    env.chain.reorg(hist)
    assert env.event("cu_a") == hist
    env.level("cu_a", 2, 1)
    restore_head(env, old_head)
    assert keys(env, after=mark) == ["lv2"]
    env.step()
    env.step()
    assert keys(env, after=mark) == ["lv2"]


@pytest.mark.parametrize("path", ["percheck", "global"])
def test_condition_healed_by_the_first_live_run_stays_silent_after_a_deep_reorg(tmp_path, ctl, path):
    """A resolved key (DEFECT-CLASSES 2): lv0 on at the boundary (opened silently) and off by the first live run
    (resolved silently, never sent). A reorg below the boundary replays the same history: the silent repeat opens it
    again silently, the live run after it resolves it silently; the history event stays silent too: nothing is sent."""
    env = Env(tmp_path, max_log_range=1)
    if path == "global":
        hist = env.event("cu_a")
        env.level("cu_a", 0, 1)
        env.chain.mine(2)
        env.start()
        env.step()
        pin = env.w.phase()["history_block"]
    else:
        env.go_live(only=("cu_b",))
        hist = env.event("cu_a")
        env.level("cu_a", 0, 1)
        env.step()
        env.start()
        env.step()
        pin = env.rec("cu_a")["history_block"]
    assert [r["state"] for r in env.rows("cu_a") if r["key"] == "lv0"] == ["open"]
    env.level("cu_a", 0, 0)
    env.step()  # the first live run: lv0 resolved silently
    env.step()
    assert [r["state"] for r in env.rows("cu_a") if r["key"] == "lv0"] == ["resolved"] and keys(env) == []
    mark, old_head = env.last_rowid(), env.chain.sealed
    env.start()
    env.chain.reorg(hist)
    assert env.event("cu_a") == hist
    env.level("cu_a", 0, 1)
    env.chain.mine(pin - hist + 1)
    env.level("cu_a", 0, 0)
    restore_head(env, old_head)
    assert keys(env, after=mark) == [], keys(env, after=mark)
    lv0 = [r for r in env.rows("cu_a") if r["key"] == "lv0"]
    assert [r["state"] for r in lv0] == ["resolved", "resolved"] and all(r["alerts"] == 0 for r in lv0)
    assert env.rec("cu_a") is None


@pytest.mark.parametrize("path", ["percheck", "global"])
def test_repeat_survives_a_failure_and_a_restart_and_never_delays_another_check(tmp_path, ctl, path):
    """The repeat's record (cause `reorg`) is durable: a failing repeat keeps it pinned at the boundary, retried every
    step; a restart keeps it; another check's live page is sent in the same steps (A4)."""
    env = Env(tmp_path, max_log_range=1)
    hist, pin = _boundary(env, path)
    env.step()
    env.step()
    mark, old_head = env.last_rowid(), env.chain.sealed
    env.start()
    env.chain.reorg(hist)
    assert env.event("cu_a") == hist
    env.chain.mine(pin - hist + 1)
    b_event = env.event("cu_b")  # above the boundary: live for cu_b
    ctl["cu_a"].update(fail=2, rpc=False)
    restore_head(env, old_head)
    # behavioural assertions first: nothing for cu_a, cu_b's page sent in the step
    assert keys(env, after=mark) == [] and keys(env, after=mark, cid="cu_b") == [f"ev:{b_event}"]
    rec, shown = env.rec("cu_a"), env.w.status()["checks"]["cu_a"].get("catchup")
    env.start()
    restarted = env.rec("cu_a")
    env.step(mine=False)  # fails again, same block
    retried = env.rec("cu_a")
    assert keys(env, after=mark) == []
    env.step()
    assert keys(env, after=mark) == [] and env.rec("cu_a") is None
    assert rec is not None and rec["state"] == "pending" and rec["history_block"] == pin and rec["cause"] == "reorg"
    assert shown == rec and restarted == rec and retried == rec
    assert env.w.db.meta("silent:cu_a") == pin and env.w.db.meta("reorg:cu_a") is None
    runs = env.check("cu_a").runs
    assert runs[0]["block"] == pin and hist in runs[0]["events"] and {"live", "force"} <= runs[1]["reasons"]
    assert [h for h in env.health() if "check-error" in h["text"] and "cu_a" in h["text"]]


@pytest.mark.parametrize("path", ["percheck", "global"])
def test_reorg_above_the_boundary_needs_no_repeat(tmp_path, ctl, path):
    """Control: a reorg whose ancestor is at or above the boundary leaves the history alone: no record, no silent
    run; a new-chain event above the boundary is sent once by the ordinary reorg run."""
    env = Env(tmp_path, max_log_range=1)
    hist, pin = _boundary(env, path)
    env.step()
    env.step()
    mark, old_head = env.last_rowid(), env.chain.sealed
    env.start()
    env.chain.reorg(pin + 1)
    new = env.event("cu_a")
    restore_head(env, old_head)
    assert keys(env, after=mark) == [f"ev:{new}"]
    assert env.rec("cu_a") is None and not {"backfill", "force"} & env.check("cu_a").runs[0]["reasons"]


def test_never_run_check_with_backfill_off_alerts_as_at_base_on_a_database_with_history(tmp_path, ctl):
    """D9 (d) on a database that has a caught-up checkpoint: a check that never completed a run, started with
    `backfill.enabled: false`, gets no record and no silent run (the repeat rule concerns checks that ran): its first
    run sends every event since its cursor."""
    env = Env(tmp_path)
    env.go_live(only=("cu_b",))
    evs = [env.event("cu_a")]
    env.step()
    evs.append(env.event("cu_a"))
    env.step()
    env.start(backfill=False)
    env.step()
    assert keys(env) == [f"ev:{b}" for b in evs]
    assert env.rec("cu_a") is None and not {"backfill", "live"} & env.check("cu_a").runs[0]["reasons"]


# ---------------------------------------------------------------- while the phase is backfill (D15 (b), (c))


def test_reorg_below_the_caught_up_checkpoint_while_backfilling_repeats_the_silent_run(tmp_path, ctl):
    """cu_a evaluated and alerting while cu_b keeps the backfill: a reorg below `history_block` takes cu_a out of
    `phase.evaluated`; the global path repeats its silent run (the replayed history event stays silent), its live
    event re-included at its height is neither resolved nor re-sent, a new event is sent once."""
    env = Env(tmp_path)
    ctl["cu_b"]["fail"] = 50
    env.start()
    env.w.cfg.raw["max_log_range"] = 1
    env.chain.mine(3)
    env.step()
    h = env.w.phase()["history_block"]
    assert env.w.backfilling and "cu_a" in env.w.phase()["evaluated"]
    env.step()
    live = env.event("cu_a")
    env.step()
    assert keys(env) == [f"ev:{live}"]
    mark, old_head = env.last_rowid(), env.chain.sealed
    env.start()
    env.w.cfg.raw["max_log_range"] = 1
    env.chain.reorg(h)
    historical = env.event("cu_a")
    env.chain.mine(live - h)
    assert env.event("cu_a") == live
    env.chain.mine()
    fresh = env.event("cu_a")
    restore_head(env, old_head)
    # behavioural assertion first (red on the candidate: the live run reads the replayed history event)
    assert keys(env, after=mark) == [f"ev:{fresh}"], [(m["kind"], m["ikey_"]) for m in env.msgs(after=mark)]
    runs = env.check("cu_a").runs
    silent, first = runs[-2], runs[-1]
    assert silent["block"] == h and silent["events"] == [historical] and {"backfill", "force"} <= silent["reasons"]
    assert {"live", "force", "reorg"} <= first["reasons"] and first["events"][-1] == fresh
    assert env.w.backfilling and "cu_a" in env.w.phase()["evaluated"] and env.rec("cu_a") is None


def test_check_disabled_before_the_caught_up_checkpoint_and_reenabled_while_backfilling(tmp_path, ctl):
    """Sibling of NF3: cu_a disabled before the caught-up checkpoint is known (never evaluated), an event while
    disabled after it, re-enabled while cu_b still keeps the backfill: its silent evaluation is pinned at the
    checkpoint of its first step (not at `history_block`), so the disabled-period event is never sent."""
    env = Env(tmp_path)
    ctl["cu_b"]["fail"] = 50
    env.start(disabled=("cu_a",))
    env.step()
    assert env.w.backfilling and env.w.phase()["history_block"] is not None
    disabled_event = env.event("cu_a")
    env.step()
    env.start()
    env.step()
    # behavioural assertion first (red without the backfill-phase record: the live run sends the disabled event)
    assert keys(env) == [], keys(env)
    (silent,) = env.check("cu_a").runs
    assert silent["events"] == [disabled_event] and silent["block"] == env.w.checkpoint()["block"]
    live = env.event("cu_a")
    env.step()
    ctl["cu_b"]["fail"] = 0
    env.step()
    env.step()
    assert keys(env) == [f"ev:{live}"] and not env.w.backfilling


def test_reenabled_while_backfilling_and_disabled_again_lets_the_backfill_go_live(tmp_path, ctl):
    """Control (state): the backfill-phase record of a re-enabled check is dropped when it is disabled again (D7): the
    go-live proceeds without it; re-enabled after the go-live it catches up per check, silently."""
    env = Env(tmp_path)
    ctl["cu_b"]["fail"] = 2
    env.start()
    env.step()
    env.start(disabled=("cu_a",))
    d1 = env.event("cu_a")
    env.step()
    env.start()
    planned = env.rec("cu_a")
    env.start(disabled=("cu_a",))
    dropped = env.rec("cu_a")
    env.step()
    env.step()
    assert not env.w.backfilling and "cu_a" not in env.w.phase().get("evaluated", ())
    d2 = env.event("cu_a")
    env.step()
    env.start()
    env.step()
    env.step()
    assert keys(env) == [] and env.check("cu_a").runs[0]["events"] == [d1, d2]
    assert planned is not None and planned["state"] == "pending" and dropped is None


def test_reenable_after_a_reorg_onto_a_shorter_chain_is_pinned_at_the_earlier_boundary(tmp_path, ctl):
    """cu_a caught up at `pin`, went live, was disabled; a reorg while disabled replaces the chain from below its
    history event with a shorter one (checkpoint below `pin`). Re-enabled, its catch-up block is never below its
    earlier boundary: the silent run stays pending (pinned at `pin`) while the checkpoint is below it, so the history
    event re-included on the new chain is never sent; an event above `pin` is sent once."""
    env = Env(tmp_path, max_log_range=1)
    hist, pin = _boundary(env, "percheck")
    env.step()
    env.step()
    env.start(disabled=("cu_a",))
    mark = env.last_rowid()
    env.chain.reorg(hist)  # the new chain ends below the history event's height
    env.step(mine=False)  # the reorg is handled while cu_a is disabled: its cursor is rewound
    assert env.w.checkpoint()["block"] < hist <= pin
    env.start()
    env.step(mine=False)  # the catch-up's first attempt, at a checkpoint below `pin`
    assert env.event("cu_a") == hist  # the history event, re-included at its height
    env.step()
    # behavioural assertion first (red without the floor: the catch-up pins below `pin` and the next live run reads
    # the history event)
    assert keys(env, after=mark) == []
    rec = env.rec("cu_a")
    assert rec["history_block"] == pin and rec["cause"] == "reenabled"
    while env.w.checkpoint()["block"] <= pin:
        env.step()
    live = env.event("cu_a")
    env.step()
    env.step()
    assert keys(env, after=mark) == [f"ev:{live}"] and env.rec("cu_a") is None
    assert env.w.db.meta("silent:cu_a") == pin


SLOT_R = 0xCC0


@pytest.fixture
def absent_check(ctl):
    """`cu_r`: one level `lv` (page while word SLOT_R == 1), resolved by absence (`resolve_on_absence`)."""
    from checks.base import Check, Every

    @registry.register
    class Absent(Check):
        id = "cu_r"
        schedule = (Every(3600),)
        resolve_on_absence = True

        def run(self, ctx, block):
            (word,) = ctx.rpc.storage(PROXY, [SLOT_R])
            return [Finding(check=self.id, key="lv", severity="page", title="cu_r lv", body="")] if word == 1 else []

    try:
        yield "cu_r"
    finally:
        registry.REGISTRY.pop("cu_r", None)


def test_repeat_keeps_an_absence_resolved_incident_seen_above_the_boundary(tmp_path, ctl, absent_check):
    """`resolve_on_absence` in the silent repeat: `lv` (sent by a live run, last seen above the boundary) is false at
    the boundary on the replacing chain and true again above it. The repeat does not resolve it by absence (a deferred
    key); the live run after it re-finds it: no "resolved", no second `open`."""
    env = Env(tmp_path, max_log_range=1)
    env.go_live(only=("cu_b",))
    env.start(only=("cu_b", "cu_r"))
    env.step()
    pin = env.rec("cu_r")["history_block"]
    env.chain.write(SLOT_R, 1)  # above the boundary
    env.step()
    assert [(m["kind"], m["ikey_"]) for m in env.msgs("cu_r")] == [("open", "lv")]
    env.step()
    (row,) = env.rows("cu_r", "open")
    mark, old_head = env.last_rowid(), env.chain.sealed
    env.start(only=("cu_b", "cu_r"))
    env.chain.reorg(pin)  # below the boundary (ancestor pin - 1): the write above it is dropped
    env.chain.mine()
    env.chain.write(SLOT_R, 1)  # on again above the boundary
    restore_head(env, old_head)
    # behavioural assertion first (red without the deferral: resolved by absence at the boundary, then opened again)
    assert env.msgs("cu_r", after=mark) == [], [(m["kind"], m["ikey_"]) for m in env.msgs("cu_r", after=mark)]
    assert [r["id"] for r in env.rows("cu_r", "open")] == [row["id"]] and env.rec("cu_r") is None
