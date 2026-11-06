"""Independent QA probes. Copy into a candidate archive; never into audited tests."""
import pytest

from test_catchup import Env, IDS, KIND, DATA, ctl  # noqa: F401
from checks.base import Finding
from rpc import RpcError


def keys(env, after=0, cid="cu_a"):
    return [m["ikey_"] for m in env.msgs(cid, after=after)]


def restore_head(env, old_head, extra=1):
    env.chain.mine(max(old_head - env.chain.sealed + extra, 1))
    env.step(mine=False)


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
    old_cp = env.w.checkpoint()["block"]
    env.chain.reorg(event)  # common ancestor precedes history event and pin
    assert env.event("cu_a") == event  # same event rule/data, re-included at its historical height
    restore_head(env, old_head)
    actual = keys(env)
    replayed = next(r for r in env.rows("cu_a") if r["id"] == original["id"])
    print({"cause": cause, "confirmations": confirmations, "event": event, "pin": pin,
           "old_checkpoint": old_cp, "ancestor": event - 1, "replayed_incident": replayed["id"],
           "notify": replayed["notify"], "alerts": replayed["alerts"], "sent": actual})
    assert actual == [], f"historical catch-up event sent after deep reorg: {actual}"


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
    print({"path": path, "pin": pin, "history_event": historical, "live_event": live, "sent": actual,
           "runs": env.check("cu_a").runs})
    assert actual == [f"ev:{live}"], f"history replay escaped silence: {actual}"


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
        assert env.rec("cu_a") is None
    else:  # benign control: re-enable after go-live uses the per-check path
        ctl["cu_b"]["fail"] = 0
        env.step()
        assert not env.w.backfilling
        env.start()
        assert env.rec("cu_a")["state"] == "pending"
    env.step()
    actual = keys(env)
    print({"reenable_before_live": reenable_before_live, "disabled_event": disabled_event,
           "sent": actual, "phase": env.w.phase(), "catchup": env.rec("cu_a")})
    assert f"ev:{disabled_event}" not in actual, f"disabled-interval event sent on first live run: {actual}"


@pytest.mark.parametrize("stage", ["pending", "first_live"])
@pytest.mark.parametrize("failure", ["runtime", "rpc", "partial"])
def test_partial_or_failed_run_is_atomic_and_first_live_retries_after_restart(tmp_path, ctl, monkeypatch, stage, failure):
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

