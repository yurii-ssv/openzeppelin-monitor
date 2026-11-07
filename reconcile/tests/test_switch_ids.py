"""Incident ids across a disable / re-enable round trip, CP2 repair 2 (plan check-switches, D16; QA cp2-round-2 QF4).

A check keeps its own state while it is switched off: M5's `m5_incidents` keeps the open row whose framework incident
the disabling start closed as `disabled`, and after re-enable M5 proposes that row's id again. `Alerts._apply_one`
opens the new incident under the first free deterministic suffix `<id>-2`, `<id>-3`, … when the proposed id is held
only by a `disabled` row; a collision with an `open` or `resolved` row still raises `ValueError`.

`test_m5_real_disabled_key_gets_new_id_and_first_live_page` is QA's reproducer (`qa/cp2-round-2/test_qa2_deep.py`)
made permanent: on the faulty revision every catch-up attempt raised `ValueError: incident id … already exists`, the
silent run never committed and the surviving shortfall was never paged. The behavioural assertion (exactly one
`page`/`open`) comes before any id assertion, so a revision without the rule fails on the missed page.
"""

from __future__ import annotations

import copy

import pytest
from fakechain import FakeRpc, MiniSSV
from fakechain import keys as pubkeys
from harness import advance, make_worker, outbox
from test_worker import OPS as EB_OPS
from test_worker import OWNER as EB_OWNER
from test_worker import make as make_m5

import checks as registry
from checks.base import Finding
from eb_reconcile import DB, Config, Worker

# ---------------------------------------------------------------- helpers (as QA's reproducer)


def restart(w, chain, only=None):
    built = registry.build(w.cfg.raw, only=only if only is not None else list(w.checks))
    new = Worker(Config(copy.deepcopy(w.cfg.raw)), FakeRpc(chain), DB(w.cfg.db_path), w.notifier,
                 clock=w.clock, checks=built)
    new.bootstrap()
    return new


def flip(w, chain, cid, enabled):
    raw = copy.deepcopy(w.cfg.raw)
    raw.setdefault("checks", {}).setdefault(cid, {})["enabled"] = enabled
    new = Worker(Config(raw), FakeRpc(chain), DB(w.cfg.db_path), w.notifier,
                 clock=w.clock, checks=registry.build(raw, only=[cid]))
    new.bootstrap()
    return new


def sent(w, cid, after=0):
    return outbox(w, f"check_id='{cid}' AND kind!='health'")[after:]


def msgs(w, after=0):
    return [(m["severity"], m["kind"], m["incident_id"]) for m in sent(w, "m5", after)]


def row(w, inc_id):
    r = w.db.conn.execute("SELECT * FROM incidents WHERE id=?", (inc_id,)).fetchone()
    return dict(r) if r is not None else None


def m5_rows(w):
    return [dict(r) for r in w.db.conn.execute("SELECT * FROM incidents WHERE check_id='m5' ORDER BY rowid")]


def live_m5(tmp_path):
    """Global live mode with one real validator and a clean root, then the #94775 sequence in live mode (63 dummy
    validators added, the root consumed, the dummies removed): one genuine M5 page for the confirmed shortfall."""
    chain, w = make_m5(tmp_path, backfill={"enabled": True}, max_log_range=1)
    ssv = MiniSSV(chain)
    ssv.register(EB_OWNER, EB_OPS, pubkeys(1, 1))
    ref = chain.number
    chain.mine()
    ssv.commit(ref)
    ssv.update(EB_OWNER, EB_OPS, 2048)
    advance(w)
    chain.mine()
    advance(w)
    assert w.phase()["phase"] == "live" and sent(w, "m5") == []
    ref = chain.number
    chain.mine()
    dummy = pubkeys(63, 100)
    ssv.register(EB_OWNER, EB_OPS, dummy)
    ssv.commit(ref)
    ssv.update(EB_OWNER, EB_OPS, 2048)
    ssv.remove(EB_OWNER, EB_OPS, dummy)
    advance(w)
    (page,) = [m for m in sent(w, "m5") if m["severity"] == "page"]
    assert page["kind"] == "open"
    return chain, w, ssv, page


def heal(chain, ssv):
    """A clean next-round root restores the cluster's EB: M5 resolves the shortfall."""
    ref = chain.number
    chain.mine()
    ssv.commit(ref)
    ssv.update(EB_OWNER, EB_OPS, 2048)


def reenable(w, chain):
    """Re-enable m5, run its silent catch-up (no new block) and its first live run (next block)."""
    w = flip(w, chain, "m5", True)
    before = len(sent(w, "m5"))
    advance(w)
    assert msgs(w, before) == [], "the catch-up run must be silent"
    assert w.db.meta("catchup:m5")["state"] == "first_live", "the silent run must commit"
    chain.mine()
    advance(w)
    return w, before


# ---------------------------------------------------------------- QA reproducer (QF4) and its lifecycle


def test_m5_real_disabled_key_gets_new_id_and_first_live_page(tmp_path):
    chain, w, ssv, old_page = live_m5(tmp_path)
    w = flip(w, chain, "m5", False)
    frozen = row(w, old_page["incident_id"])
    assert frozen["state"] == "disabled"
    chain.mine()
    advance(w)
    w = flip(w, chain, "m5", True)
    before = len(sent(w, "m5"))
    advance(w)
    silent_record = w.db.meta("catchup:m5")
    chain.mine()
    advance(w)
    after_two_attempts = w.db.meta("catchup:m5")
    w = restart(w, chain)
    chain.mine()
    advance(w)
    assert w.db.meta("catchup:m5") == after_two_attempts
    assert row(w, old_page["incident_id"]) == frozen
    actual = msgs(w, before)
    assert len(actual) == 1 and actual[0][:2] == ("page", "open"), "surviving M5 condition missed its first-live page"
    assert actual[0][2] != old_page["incident_id"]
    # the mechanism, after the behaviour: silent run committed, record gone after the first live run, no check-error
    assert silent_record is not None and silent_record["state"] == "first_live"
    assert after_two_attempts is None
    assert actual[0][2] == f"{old_page['incident_id']}-2"
    assert outbox(w, "kind='health'") == []
    assert (w.db.meta("run:m5") or {}).get("errors", 0) == 0


def test_m5_reenabled_incident_is_sent_once_then_resolved_under_the_new_id(tmp_path):
    chain, w, ssv, old_page = live_m5(tmp_path)
    old = old_page["incident_id"]
    w = flip(w, chain, "m5", False)
    frozen = row(w, old)
    chain.mine()
    advance(w)
    w, before = reenable(w, chain)
    new = f"{old}-2"
    assert msgs(w, before) == [("page", "open", new)]
    inc = row(w, new)
    assert (inc["state"], inc["severity"], inc["notify"], inc["alerts"]) == ("open", "page", 1, 1)
    assert inc["ikey"] == frozen["ikey"]
    # M5's own bookkeeping keeps its original id (D16's accepted consequence)
    assert [r["id"] for r in w.db.conn.execute("SELECT id FROM m5_incidents WHERE state='open'")] == [old]
    # later steps and restarts send nothing new (the 6 h re-alert cadence is not reached on the fake clock)
    for _ in range(3):
        chain.mine()
        advance(w)
    w = restart(w, chain)
    chain.mine()
    advance(w)
    assert msgs(w, before) == [("page", "open", new)]
    # M5 resolves the condition: the new framework incident closes and its resolution is sent once
    heal(chain, ssv)
    advance(w)
    assert msgs(w, before) == [("page", "open", new), ("info", "resolved", new)]
    assert row(w, new)["state"] == "resolved"
    assert row(w, old) == frozen
    chain.mine()
    advance(w)
    w = restart(w, chain)
    chain.mine()
    advance(w)
    assert msgs(w, before) == [("page", "open", new), ("info", "resolved", new)]
    assert [r["state"] for r in m5_rows(w)] == ["disabled", "resolved"]


def test_m5_second_round_trip_takes_the_next_free_suffix(tmp_path):
    chain, w, ssv, old_page = live_m5(tmp_path)
    old = old_page["incident_id"]
    for n in (2, 3):
        w = flip(w, chain, "m5", False)
        chain.mine()
        advance(w)
        w, before = reenable(w, chain)
        assert msgs(w, before) == [("page", "open", f"{old}-{n}")]
    assert [(r["id"], r["state"]) for r in m5_rows(w)] == [(old, "disabled"), (f"{old}-2", "disabled"),
                                                         (f"{old}-3", "open")]


def test_m5_latent_disabled_then_confirmed_live_escalates_the_new_incident(tmp_path):
    """The latent path: the silent latent incident is closed as `disabled`; after re-enable M5 proposes its latent
    row's id again (silently re-tracked under `-2`), and the confirming removal escalates that new incident."""
    chain, w = make_m5(tmp_path, backfill={"enabled": True}, max_log_range=1)
    ssv = MiniSSV(chain)
    ssv.register(EB_OWNER, EB_OPS, pubkeys(1, 1))
    ref = chain.number
    chain.mine()
    ssv.commit(ref)
    ssv.update(EB_OWNER, EB_OPS, 2048)
    advance(w)
    chain.mine()
    advance(w)
    ref = chain.number
    chain.mine()
    dummy = pubkeys(63, 100)
    ssv.register(EB_OWNER, EB_OPS, dummy)
    ssv.commit(ref)
    ssv.update(EB_OWNER, EB_OPS, 2048)
    advance(w)
    (latent,) = m5_rows(w)
    assert (latent["severity"], latent["notify"], latent["state"]) == ("info", 0, "open") and sent(w, "m5") == []
    w = flip(w, chain, "m5", False)
    assert row(w, latent["id"])["state"] == "disabled"
    w, before = reenable(w, chain)
    assert msgs(w, before) == []  # latent is tracked silently, as before the switch
    new = f"{latent['id']}-2"
    assert (row(w, new)["state"], row(w, new)["notify"]) == ("open", 0)
    ssv.remove(EB_OWNER, EB_OPS, dummy)
    advance(w)
    assert msgs(w, before) == [("page", "escalate", new)]  # info (silent) -> page, as without the round trip
    assert row(w, latent["id"])["state"] == "disabled"


def test_m5_condition_healed_while_disabled_is_never_sent_and_opens_nothing(tmp_path):
    """The resolve path against a disabled-held id: nothing to resolve in `incidents` (no open row), no message."""
    chain, w, ssv, old_page = live_m5(tmp_path)
    w = flip(w, chain, "m5", False)
    frozen = row(w, old_page["incident_id"])
    heal(chain, ssv)
    advance(w)
    w, before = reenable(w, chain)
    chain.mine()
    advance(w)
    assert msgs(w, before) == []
    assert m5_rows(w) == [frozen]
    assert [r["state"] for r in w.db.conn.execute("SELECT state FROM m5_incidents")] == ["resolved"]


# ---------------------------------------------------------------- the id rule itself (falsifiers and controls)


def _alerts(tmp_path):
    _, w = make_worker(tmp_path, [])
    return w


def _f(key, inc_id, **kw):
    return Finding(check="sw_ids", key=key, severity="page", title=f"t {key}", body="b", incident_id=inc_id, **kw)


def _set_state(w, inc_id, state):
    w.db.conn.execute("UPDATE incidents SET state=? WHERE id=?", (state, inc_id))


def _open(w, key, inc_id, block=100):
    with w.db.tx():
        return w.alerts.apply([_f(key, inc_id)], block)


@pytest.mark.parametrize("state", ["open", "resolved"])
def test_proposed_id_held_by_an_open_or_resolved_incident_still_raises(tmp_path, state):
    w = _alerts(tmp_path)
    _open(w, "k1", "X-1")
    if state == "resolved":
        with w.db.tx():
            w.alerts.apply([_f("k1", None, resolve=True)], 101)
    assert row(w, "X-1")["state"] == state
    before = outbox(w)
    with pytest.raises(ValueError, match="incident id X-1 already exists"):
        _open(w, "k2", "X-1")
    assert outbox(w) == before and [r["id"] for r in w.db.conn.execute("SELECT id FROM incidents")] == ["X-1"]


@pytest.mark.parametrize("same_key", [True, False])
def test_proposed_id_held_only_by_a_disabled_incident_opens_under_the_first_free_suffix(tmp_path, same_key):
    w = _alerts(tmp_path)
    _open(w, "k1", "X-1")
    with w.db.tx():
        _set_state(w, "X-1", "disabled")
    disabled = row(w, "X-1")
    assert _open(w, "k1" if same_key else "k2", "X-1", block=110) == 1
    new = row(w, "X-1-2")
    assert (new["state"], new["key"], new["opened_block"]) == ("open", "k1" if same_key else "k2", 110)
    assert row(w, "X-1") == disabled
    assert [(m["kind"], m["incident_id"]) for m in outbox(w)][-1] == ("open", "X-1-2")


@pytest.mark.parametrize("held", ["open", "resolved", "disabled"])
def test_suffix_skips_every_taken_id_whatever_its_state(tmp_path, held):
    w = _alerts(tmp_path)
    _open(w, "k1", "X-1")
    _open(w, "k9", "X-1-2")
    with w.db.tx():
        _set_state(w, "X-1", "disabled")
        _set_state(w, "X-1-2", held)
    _open(w, "k1", "X-1", block=120)
    assert row(w, "X-1-3")["state"] == "open" and row(w, "X-1-3")["key"] == "k1"
    assert row(w, "X-1-2")["state"] == held


def test_suffix_rule_is_deterministic_across_a_rolled_back_attempt(tmp_path):
    """A silent run that rolls back after the allocation leaves no row: the retry allocates the same id."""
    w = _alerts(tmp_path)
    _open(w, "k1", "X-1")
    with w.db.tx():
        _set_state(w, "X-1", "disabled")
    with pytest.raises(RuntimeError), w.db.tx():
        w.alerts.apply([_f("k1", "X-1", notify=False)], 130)
        assert row(w, "X-1-2")["notify"] == 0
        raise RuntimeError("the run fails after its findings")
    assert row(w, "X-1-2") is None
    _open(w, "k1", "X-1", block=131)
    assert row(w, "X-1-2")["opened_block"] == 131


def test_default_ids_are_unchanged_by_the_rule(tmp_path):
    w = _alerts(tmp_path)
    _open(w, "k1", None, block=140)
    (first,) = [r["id"] for r in w.db.conn.execute("SELECT id FROM incidents")]
    assert first.startswith("sw_ids-") and first.endswith("-140")
    with w.db.tx():
        _set_state(w, first, "disabled")
    _open(w, "k1", None, block=140)
    assert row(w, f"{first}-2")["state"] == "open"
