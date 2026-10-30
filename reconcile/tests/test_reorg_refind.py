"""Persisted keyed state after a reorg (CP1B repair 3, QA attempt 3; checks/README.md §6.1 "reorg re-find rule").

State that is only re-evaluated when its key is enumerated from surviving data is never revisited once a
reorg deletes that data. The framework closes it: in a run with reason `reorg`, every open incident of the
check last seen above the common ancestor (`ctx.reorg_keys`) must be reported again, or it is resolved as
"evidence left the canonical chain". M5 enumerates its own persisted keys (`evaluations`, `m5_incidents`).

`Flag` is a second check module (not M5): it pages key "flag" while a storage word is non-zero at the run's
block, i.e. on chain evidence that FakeChain.reorg deletes with the blocks above the fork.
"""

from __future__ import annotations

import json

from fakechain import PROXY, FakeChain, MiniSSV, keys
from harness import make_worker, outbox
from test_framework import Stub, finding, inbox_line
from test_worker import OPS, OWNER, ev_for, make, poc_setup, tick

import eb_model as M
from checks.base import Every, OnInbox
from eb_storage import cluster_id

SLOT = 0xF1A6
REORG_TEXT = "Resolved because its evidence left the canonical chain"


class Flag(Stub):
    """Pages "flag" while storage[SLOT] != 0 at the block. `naive`: reports nothing in a reorg run (violates the
    contract; the framework rule closes the key). `fail_reorg`: raise in that many reorg runs."""

    id = "flag"
    schedule = Every(60)

    def __init__(self, naive=False, fail_reorg=0, severity="page", reorg_severity=None):
        super().__init__(check_id="flag", schedule=Every(60))
        self.naive, self.fail_reorg, self.severity, self.reorg_severity = naive, fail_reorg, severity, reorg_severity
        self.reorg_keys: list[frozenset] = []

    def run(self, ctx, block):
        super().run(ctx, block)
        if ctx.reorg_ancestor is not None:
            self.reorg_keys.append(ctx.reorg_keys)
            if self.fail_reorg:
                self.fail_reorg -= 1
                raise RuntimeError("cannot evaluate on the new chain yet")
            if self.naive:
                return []
        on = ctx.rpc.storage(PROXY, [SLOT])[0]
        sev = self.reorg_severity if ctx.reorg_ancestor is not None and self.reorg_severity else self.severity
        return [finding(check=self.id, key="flag", severity=sev, resolve=not on)]


def incidents(w, check=None):
    q = "SELECT * FROM incidents" + (" WHERE check_id=?" if check else "") + " ORDER BY opened_at, rowid"
    return [dict(r) for r in w.db.conn.execute(q, (check,) if check else ())]


def status(tmp_path) -> dict:
    return json.loads((tmp_path / "w.status.json").read_text())


def orphan_flag(tmp_path, check):
    """`check` opens "flag" on a block that the reorg then deletes; returns (chain, worker, fork)."""
    chain = FakeChain()
    chain.mine()
    _, w = make_worker(tmp_path, [check], chain=chain)
    w.step(force=True)
    fork = chain.number
    chain.write(SLOT, 1)
    chain.mine(2)
    w.step(force=True)
    assert [r["state"] for r in incidents(w)] == ["open"]
    chain.reorg(fork)
    chain.mine(3)
    return chain, w, fork


# ---------------------------------------------------------------- framework: the reorg re-find rule


def test_reorg_key_not_reported_is_resolved_as_evidence_left_the_canonical_chain(tmp_path):
    chain, w, fork = orphan_flag(tmp_path, Flag(naive=True))
    w.step(force=True)
    check = w.checks["flag"]
    assert check.reorg_keys == [frozenset({"flag"})]
    (inc,) = incidents(w)
    assert inc["state"] == "resolved" and inc["alerts"] == 1
    assert json.loads(inc["evidence"])["resolved_by_reorg"] == {"ancestor": fork - 1, "block": chain.sealed}
    (res,) = outbox(w, "kind='resolved'")
    assert (res["severity"], res["delivery"], res["incident_id"]) == ("info", "telegram", inc["id"])
    assert REORG_TEXT in res["text"] and f"blocks after {fork - 1}" in res["text"]
    assert "Evidence left the canonical chain: flag flag" in res["text"]
    # never re-paged: no re-alert of the closed incident, no new incident for the deleted condition
    w.clock.advance(21_601)
    w.step(force=True)
    assert [r["kind"] for r in outbox(w, "severity IN ('page','warn')")] == ["open"]
    assert status(tmp_path)["healthy"] is True and w.db.meta("reorg:flag") is None


def test_reorg_key_with_surviving_evidence_reported_nothing_is_resolved_then_reopened(tmp_path):
    """Coordinator condition 2: a check that skips a surviving key in its reorg run loses the incident (info
    resolution) and opens it again, alerted, at its next run; no alert is dropped."""
    chain = FakeChain()
    chain.mine()
    _, w = make_worker(tmp_path, [Flag(naive=True)], chain=chain)
    chain.write(SLOT, 1)  # canonical evidence, below the fork
    chain.mine()
    w.step(force=True)
    fork = chain.number
    chain.mine(2)
    w.step(force=True)  # re-reported: last seen above the coming ancestor
    chain.reorg(fork)
    chain.mine(3)
    w.step(force=True)  # reorg run: nothing reported
    first, = incidents(w)
    assert first["state"] == "resolved"
    assert REORG_TEXT in outbox(w, "kind='resolved'")[0]["text"]
    w.clock.advance(61)
    w.step()  # the next interval run finds the condition again
    first, second = incidents(w)
    assert second["state"] == "open" and second["id"] != first["id"] and second["alerts"] == 1
    opens = outbox(w, "kind='open'")
    assert [r["incident_id"] for r in opens] == [first["id"], second["id"]]
    assert all(r["sent_at"] is not None and r["severity"] == "page" for r in opens)


def test_reorg_run_that_raises_keeps_incidents_marker_and_defers_realerts(tmp_path):
    chain, w, fork = orphan_flag(tmp_path, Flag(fail_reorg=3))
    for _ in range(2):
        w.step(force=True)
    (inc,) = incidents(w)
    assert inc["state"] == "open" and not outbox(w, "kind='resolved'")  # rolled back
    assert w.db.meta("reorg:flag") == fork - 1
    st = status(tmp_path)
    assert st["healthy"] is False and "reorg re-evaluation pending for check(s) flag" in st["error"]
    assert outbox(w, "key LIKE 'health:check-error:%'")
    w.clock.advance(21_601)
    w.step(force=True)  # third failure, past the page re-alert cadence: the orphan incident is not re-alerted
    assert not outbox(w, "kind='realert'") and incidents(w)[0]["alerts"] == 1
    w.step(force=True)  # the run succeeds on the new chain: storage word 0 -> the check resolves it itself
    (inc,) = incidents(w)
    assert inc["state"] == "resolved" and w.db.meta("reorg:flag") is None
    assert w.checks["flag"].reorg_keys == [frozenset({"flag"})] * 4
    assert status(tmp_path)["healthy"] is True
    assert [r["kind"] for r in outbox(w, "severity IN ('page','warn')")] == ["open"]


def test_reorg_re_find_rule_survives_a_restart_before_the_reorg_run(tmp_path):
    chain, w, fork = orphan_flag(tmp_path, Flag())
    w.check_reorg()  # rewind committed, the process dies before the checks run
    w.db.conn.close()
    _, w = make_worker(tmp_path, [Flag(naive=True)], chain=chain, start_block=w.cfg.start_block,
                       activation_block=w.cfg.activation_block)
    w.step()
    assert w.checks["flag"].reorg_keys == [frozenset({"flag"})]
    assert incidents(w)[0]["state"] == "resolved" and REORG_TEXT in outbox(w, "kind='resolved'")[0]["text"]


def test_reorg_key_reported_again_is_kept_and_a_lower_severity_is_recorded_silently(tmp_path):
    chain = FakeChain()
    chain.mine()
    _, w = make_worker(tmp_path, [Flag(reorg_severity="warn")], chain=chain)
    chain.write(SLOT, 1)
    chain.mine()
    w.step(force=True)
    fork = chain.number
    chain.mine(2)
    w.step(force=True)
    chain.reorg(fork)
    chain.mine(3)
    w.step(force=True)
    (inc,) = incidents(w)
    assert (inc["state"], inc["severity"], inc["alerts"]) == ("open", "warn", 1)
    assert [r["kind"] for r in outbox(w, "kind!='health'")] == ["open"]


def test_reorg_keys_are_only_incidents_last_seen_above_the_ancestor(tmp_path):
    """Every other absence rule is unchanged: an open key not touched by the deleted blocks stays open."""
    chain = FakeChain()
    chain.mine()
    once = [finding(check="stub", key="old")]
    seen = []

    def emit(ctx, block):
        seen.append(ctx.reorg_keys)
        out = list(once)
        once.clear()
        if block >= fork_box[0]:
            out.append(finding(check="stub", key="new"))
        return out

    fork_box = [1 << 60]
    _, w = make_worker(tmp_path, [Stub(emit=emit)], chain=chain)
    w.step(force=True)  # opens "old" at this block, never reported again
    fork_box[0] = fork = chain.number + 1
    chain.mine()
    w.step(force=True)
    chain.mine(2)
    w.step(force=True)  # "new" on blocks above the fork
    chain.reorg(fork)
    chain.mine(4)
    fork_box[0] = 1 << 60  # the new chain does not have the "new" condition
    w.step(force=True)
    assert seen[-1] == frozenset({"new"})
    state = {r["key"]: r["state"] for r in incidents(w)}
    assert state == {"old": "open", "new": "resolved"}


def test_reorg_re_find_text_wins_over_resolve_on_absence(tmp_path):
    check = Flag(naive=True)
    check.resolve_on_absence = True
    chain, w, fork = orphan_flag(tmp_path, check)
    w.step(force=True)
    (res,) = outbox(w, "kind='resolved'")
    assert REORG_TEXT in res["text"] and "Condition no longer observed" not in res["text"]


def test_undelivered_alert_of_an_orphaned_incident_is_followed_by_its_resolution(tmp_path):
    """Outbox rows are history as of their enqueue: an alert still queued when its evidence is orphaned is
    delivered (in order), then the resolution that says the evidence left the canonical chain."""
    chain = FakeChain()
    chain.mine()

    class Down:
        def __init__(self):
            self.up, self.sent = False, []

        def send(self, severity, text, key, route=None):
            if not self.up:
                raise RuntimeError("telegram HTTP 502")
            self.sent.append((severity, key, text))
            return "captured"

    notifier = Down()
    _, w = make_worker(tmp_path, [Flag(naive=True)], chain=chain, notifier=notifier)
    w.step(force=True)
    fork = chain.number
    chain.write(SLOT, 1)
    chain.mine(2)
    w.step(force=True)
    chain.reorg(fork)
    chain.mine(3)
    w.step(force=True)
    notifier.up = True
    w.clock.advance(3600)
    w.deliver()
    kinds = [k.split(":")[-2] if ":resolved:" in k else k.split(":")[-1] for _, k, _ in notifier.sent
             if not k.startswith("health:")]
    assert kinds == ["open", "resolved"] and REORG_TEXT in notifier.sent[-1][2]


# ---------------------------------------------------------------- inbox-derived keys (documented pattern)


class FailedTxWatch(Stub):
    """Pages one key per delivered failed transaction. In a reorg run it re-derives its reorg keys from the
    inbox rows still canonical (README §6.1): a delivered entry is not delivered again."""

    def __init__(self, follow_contract=True):
        super().__init__(check_id="ftx", schedule=OnInbox())
        self.follow_contract = follow_contract

    def run(self, ctx, block):
        super().run(ctx, block)
        out = [finding(check=self.id, key=e.tx_hash, severity="page") for e in ctx.inbox]
        if ctx.reorg_ancestor is not None and self.follow_contract:
            canonical = {r[0] for r in ctx.db.conn.execute("SELECT tx_hash FROM inbox WHERE canonical=1")}
            out += [finding(check=self.id, key=k, severity="page") for k in sorted(ctx.reorg_keys & canonical)]
        return out


def test_inbox_derived_incidents_after_a_reorg(tmp_path):
    chain = FakeChain()
    chain.stub_call(PROXY, "0xbc26e7e5", revert="0x12e04c87")
    chain.mine()
    path = tmp_path / "in.jsonl"
    path.write_text("")
    _, w = make_worker(tmp_path, [FailedTxWatch()], chain=chain, inbox={"path": str(path)})
    w.step()
    kept = chain.add_tx("0x" + "11" * 20, PROXY, "0xbc26e7e5")
    chain.mine()
    with open(path, "a") as fh:
        fh.write(inbox_line(tx=kept, block=chain.sealed, block_hash=chain.hash(chain.sealed)))
    fork = chain.number
    gone = chain.add_tx("0x" + "11" * 20, PROXY, "0xbc26e7e5")
    chain.mine()
    with open(path, "a") as fh:
        fh.write(inbox_line(tx=gone, block=chain.sealed, block_hash=chain.hash(chain.sealed)))
    w.step()
    w.step()
    assert {r["key"] for r in incidents(w) if r["state"] == "open"} == {kept, gone}
    chain.reorg(fork)  # `gone`'s block is replaced; `kept`'s block is canonical
    chain.mine(3)
    w.step()
    state = {r["key"]: r["state"] for r in incidents(w)}
    assert state == {kept: "open", gone: "resolved"}
    assert w.status()["inbox"]["orphaned"] == 1
    assert REORG_TEXT in outbox(w, "kind='resolved'")[0]["text"]


# ---------------------------------------------------------------- M5: persisted keys (evaluations, m5_incidents)


def m5_rows(w):
    return {r["id"]: (r["state"], r["kind"]) for r in w.db.conn.execute("SELECT * FROM m5_incidents")}


def test_m5_reorg_removing_only_the_confirming_removal_resolves_plainly_and_tracks_latent(tmp_path):
    chain, w, ssv = poc_setup(tmp_path)
    s = chain.number
    chain.mine()
    d = keys(63, 6_000)
    ssv.register(OWNER, OPS, d)
    ssv.commit(s)
    ssv.update(OWNER, OPS, 2048)
    tick(w)  # latent (silent) on canonical blocks
    fork_point = chain.number
    ssv.remove(OWNER, OPS, d)
    chain.mine(3)
    assert ev_for(tick(w)).status == M.CONFIRMED
    chain.reorg(fork_point)
    chain.mine(4)
    assert ev_for(tick(w)).status == M.LATENT
    rows = incidents(w, "m5")
    assert [(r["state"], r["severity"], r["notify"]) for r in rows] == [("resolved", "page", 1), ("open", "info", 0)]
    assert sorted(m5_rows(w).values()) == [("open", "latent"), ("resolved", "confirmed")]
    (res,) = outbox(w, "kind='resolved'")
    assert REORG_TEXT in res["text"] and "removals that made this a confirmed" in res["text"]
    assert res["delivery"] == "telegram"
    w.clock = w.alerts.clock = lambda: 1e12  # far past every cadence
    tick(w)
    w.housekeeping()
    assert not outbox(w, "kind='realert'")
    assert [r["kind"] for r in outbox(w, "severity IN ('page','warn')")] == ["escalate"]  # latent -> confirmed


def test_m5_reorg_unknown_re_evaluation_keeps_the_incident_open(tmp_path):
    chain, w, ssv = poc_setup(tmp_path)
    s = chain.number
    chain.mine()
    d = keys(63, 7_000)
    ssv.register(OWNER, OPS, d)
    ssv.commit(s)
    ssv.update(OWNER, OPS, 2048)
    ssv.remove(OWNER, OPS, d)
    assert ev_for(tick(w)).status == M.CONFIRMED  # canonical, below the fork
    fork = chain.number
    chain.mine(3)
    tick(w)
    chain.reorg(fork)
    blk = chain.number
    ssv.register(OWNER, OPS, keys(1, 7_500))  # on the new chain, but the provider omits its logs
    chain.drop_logs_in = (blk, blk)
    chain.mine(2)
    assert ev_for(tick(w)).status == M.UNKNOWN
    (inc,) = incidents(w, "m5")
    assert inc["state"] == "open" and inc["severity"] == "page" and "UNKNOWN" in inc["body"]
    assert not outbox(w, "kind='resolved'")
    assert any(k.startswith("health:unknown") for k in w.notifier.keys())


def test_m5_layout_guard_failure_in_the_reorg_run_raises_and_retries(tmp_path):
    chain, w = make(tmp_path)
    chain.mine()
    tick(w)
    fork = chain.number
    ssv = MiniSSV(chain)
    ssv.register(OWNER, OPS, keys(1, 1))
    s = chain.number
    chain.mine()
    ssv.commit(s)
    ssv.update(OWNER, OPS, 2048)
    s = chain.number
    chain.mine()
    d = keys(63, 8_000)
    ssv.register(OWNER, OPS, d)
    ssv.commit(s)
    ssv.update(OWNER, OPS, 2048)
    ssv.remove(OWNER, OPS, d)
    assert ev_for(tick(w)).status == M.CONFIRMED
    height = chain.number
    chain.reorg(fork)
    chain.mine(height - chain.number)
    m5 = w.checks["m5"]
    m5.config = {**m5.config, "layout": {"implementation": "0x" + "12" * 20}}
    for _ in range(2):
        tick(w)
    assert incidents(w, "m5")[0]["state"] == "open" and w.db.meta("reorg:m5") == fork - 1
    assert any(k.startswith("health:check-error") for k in w.notifier.keys())
    m5.config = {**m5.config, "layout": {}}
    tick(w)
    assert incidents(w, "m5")[0]["state"] == "resolved" and w.db.meta("reorg:m5") is None
    assert REORG_TEXT in outbox(w, "kind='resolved'")[0]["text"]


def test_m5_reorg_resolution_of_an_incident_raised_on_deleted_blocks_says_so(tmp_path):
    chain, w, ssv = poc_setup(tmp_path)  # clean cluster on canonical blocks
    tick(w)
    fork = chain.number
    s = chain.number
    chain.mine()
    d = keys(63, 9_000)
    ssv.register(OWNER, OPS, d)
    ssv.commit(s)
    ssv.update(OWNER, OPS, 2048)
    ssv.remove(OWNER, OPS, d)
    assert ev_for(tick(w)).status == M.CONFIRMED
    height = chain.number
    chain.reorg(fork)
    chain.mine(height - chain.number)
    assert ev_for(tick(w)).status == M.CLEAN
    (res,) = outbox(w, "kind='resolved'")
    assert "#94775 incident resolved: evidence left the canonical chain" in res["text"]
    assert REORG_TEXT in res["text"]
    assert incidents(w, "m5")[0]["state"] == "resolved" and set(m5_rows(w).values()) == {("resolved", "confirmed")}


def test_m5_evaluation_of_a_cluster_whose_history_was_deleted_is_dropped(tmp_path):
    chain, w = make(tmp_path)
    chain.mine()
    tick(w)
    fork = chain.number
    MiniSSV(chain).register(OWNER, OPS, keys(2, 1))  # clean, never an incident
    chain.mine(2)
    tick(w)
    cid = cluster_id(OWNER, OPS).hex()
    assert [r[0] for r in w.db.conn.execute("SELECT status FROM evaluations WHERE cluster_id=?", (cid,))]
    chain.reorg(fork)
    chain.mine(3)
    tick(w)
    assert not w.db.conn.execute("SELECT 1 FROM evaluations").fetchone()
    assert not incidents(w) and not outbox(w, "kind!='health'")


def test_m5_reorg_run_is_a_full_sweep_so_the_digest_forgets_deleted_clusters(tmp_path):
    chain, w = make(tmp_path, sweep_interval_s=300, full_sweep_interval_s=3600)
    chain.mine()
    w.step()
    fork = chain.number
    MiniSSV(chain).register(OWNER, OPS, keys(2, 1))
    chain.mine()
    m5 = w.checks["m5"]
    m5.last_full_sweep = 0  # the hourly full sweep falls on the block with the cluster
    w.step()
    assert m5.digest(None)[0] == f"Block {chain.sealed}: implicit 1"
    chain.reorg(fork)
    chain.mine(3)
    w.step()  # not forced, inside the hour: the reorg run is a full sweep anyway
    assert m5.last_full_evals == [] and m5.digest(None) == []
