"""Blocks the Monitor fetched but did not evaluate (D21, CP4 repair 2; QA cp4-repair1 F1).

Since D21 the Monitor records a block whose filter fails in its missed-blocks file
(`<network>_missed_blocks.json`, `FileBlockStorage`) and its `recovery_config` job retries it. The worker reads that
file at every observation and sends one `monitor-unevaluated` health message per block range for every block the
recovery gave up on (`Failed`), every block still waiting after `unevaluated_after_s`, and every block that left
the file without a provable successful retry; an unreadable file is health, never "no missed block". The fixture
below writes the file exactly as the Rust storage does (`serde_json::to_string_pretty` of `MissedBlockEntry`).
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

import pytest
from fakechain import FakeChain, FakeClock
from harness import make_worker, outbox

import metrics
import monitor_watch
from eb_reconcile import load_config

ROOT = Path(__file__).resolve().parents[2]
M = 50
AFTER = 1200  # unevaluated_after_s (default)


def setup(tmp_path, **watch):
    chain = FakeChain()
    d = tmp_path / "monitor"
    d.mkdir()
    last, missed = d / "evm_ethereum_mainnet_last_block.txt", d / "evm_ethereum_mainnet_missed_blocks.json"
    _, w = make_worker(tmp_path, [], chain=chain, clock=FakeClock(),
                       monitor={"last_block_path": str(last), "max_past_blocks": M, **watch})
    return chain, w, last, missed


def entry(block, status="Pending", first=0, retries=0, error=None):
    """storage.rs MissedBlockEntry."""
    return {"block_number": block, "first_missed_at": int(first), "retry_count": retries, "status": status,
            "last_attempt_at": None, "last_error": error}


def write_missed(path, entries):
    path.write_text(json.dumps(entries, indent=2))  # rewritten in place, like tokio::fs::write


def step(w, chain, last, seconds=30, blocks=2):
    chain.mine(blocks)
    last.write_text(str(chain.sealed - 3))
    w.clock.advance(seconds)
    w.step()


def rows(w):
    return [r for r in outbox(w, "kind='health'") if r["key"].startswith("health:monitor-unevaluated:")]


def ranges(w, word=None):
    out = []
    for r in rows(w):
        if word is None or word in r["text"]:
            out += [(int(a), int(b)) for a, b in re.findall(r"locks (\d+)-(\d+) \(\d+\)", r["text"])]
    return out


def blocks_of(rs):
    return {b for lo, hi in rs for b in range(lo, hi + 1)}


def test_settings_derive_the_missed_blocks_file_next_to_the_last_block_file():
    cfg = monitor_watch.settings({"last_block_path": "/monitor/evm_ethereum_mainnet_last_block.txt"})
    assert cfg["unevaluated_after_s"] == AFTER and cfg["max_block_age"] == 7200
    assert cfg["missed_blocks_path"] == "/monitor/evm_ethereum_mainnet_missed_blocks.json"
    with pytest.raises(SystemExit, match="missed_blocks_path is required"):
        monitor_watch.settings({"last_block_path": "/monitor/last.txt"})
    assert monitor_watch.settings({"last_block_path": "/m/l.txt", "missed_blocks_path": "/m/x.json"})[
        "missed_blocks_path"] == "/m/x.json"
    with pytest.raises(SystemExit, match="positive"):
        monitor_watch.settings({"last_block_path": "/m/a_last_block.txt", "unevaluated_after_s": 0})


def test_no_file_and_an_empty_list_are_clean(tmp_path):
    chain, w, last, missed = setup(tmp_path)
    for _ in range(4):
        step(w, chain, last)
    write_missed(missed, [])
    for _ in range(4):
        step(w, chain, last)
    assert rows(w) == []
    st = w.status()["monitor"]["missed"]
    assert st["ok"] and st["pending"] == 0 and st["failed"] == 0


def test_a_block_the_recovery_evaluates_in_time_is_silent(tmp_path):
    chain, w, last, missed = setup(tmp_path)
    step(w, chain, last)
    b = chain.sealed - 4
    write_missed(missed, [entry(b, first=w.clock())])
    for _ in range(10):
        step(w, chain, last)
    assert w.status()["monitor"]["missed"]["pending"] == 1
    write_missed(missed, [entry(b, "Recovered", first=w.clock())])  # marked, then removed at the end of the run
    step(w, chain, last)
    write_missed(missed, [])
    for _ in range(60):
        step(w, chain, last)
    assert rows(w) == []
    assert w.status()["monitor"]["missed"]["pending"] == 0


def test_given_up_blocks_are_announced_once_by_range(tmp_path):
    chain, w, last, missed = setup(tmp_path)
    step(w, chain, last)
    now = w.clock()
    write_missed(missed, [entry(b, "Failed", now, 10) for b in (101, 102, 103, 107)] + [entry(110, first=now)])
    for _ in range(5):
        step(w, chain, last)
    assert sorted(ranges(w, "gave up")) == [(101, 103), (107, 107)]
    assert "NOT evaluated" in rows(w)[0]["text"] and "Check them by hand" in rows(w)[0]["text"]
    assert w.status()["monitor"]["missed"]["failed"] == 4
    m = metrics.render(w.db.conn, [])
    assert 'ssv_watch_monitor_missed_blocks{status="failed"} 4' in m
    assert "ssv_watch_monitor_unevaluated_blocks_total 4" in m


def test_a_block_waiting_longer_than_unevaluated_after_s_is_announced_then_once_more_when_given_up(tmp_path):
    chain, w, last, missed = setup(tmp_path)
    step(w, chain, last)
    t0 = w.clock()
    write_missed(missed, [entry(200, first=t0, retries=1), entry(201, "Recovering", first=t0, retries=2)])
    while w.clock() - t0 < AFTER - 30:
        step(w, chain, last)
    assert rows(w) == []
    step(w, chain, last)
    step(w, chain, last)
    assert ranges(w) == [(200, 201)] and "have not been evaluated" in rows(w)[0]["text"]
    for _ in range(5):
        step(w, chain, last)
    assert len(rows(w)) == 1
    write_missed(missed, [entry(200, "Failed", t0, 10), entry(201, first=t0, retries=3)])
    step(w, chain, last)
    assert ranges(w, "gave up") == [(200, 200)] and len(rows(w)) == 2
    write_missed(missed, [entry(200, "Failed", t0, 10)])  # 201 evaluated after its announcement: nothing more
    for _ in range(5):
        step(w, chain, last)
    assert len(rows(w)) == 2


def test_age_counts_from_the_monitor_record_when_the_worker_saw_it_later(tmp_path):
    chain, w, last, missed = setup(tmp_path)
    step(w, chain, last)
    write_missed(missed, [entry(300, first=w.clock() - AFTER - 60)])  # recorded before the worker started
    step(w, chain, last)
    assert ranges(w) == [(300, 300)]


def test_age_counts_from_the_first_sight_when_the_monitor_clock_is_ahead(tmp_path):
    chain, w, last, missed = setup(tmp_path)
    step(w, chain, last)
    t0 = w.clock()
    write_missed(missed, [entry(300, first=t0 + 86_400)])
    while w.clock() - t0 < AFTER + 60:
        step(w, chain, last)
    assert ranges(w) == [(300, 300)]


def test_an_entry_pruned_without_a_retry_is_announced(tmp_path):
    chain, w, last, missed = setup(tmp_path, max_block_age=100)
    step(w, chain, last)
    old = chain.sealed - 80  # older than max_block_age / 2: only the prune removes it
    write_missed(missed, [entry(old, first=w.clock()), entry(chain.sealed - 5, first=w.clock())])
    step(w, chain, last)
    write_missed(missed, [])  # the young one was recovered, the old one pruned
    step(w, chain, last)
    assert ranges(w) == [(old, old)] and "may not have been evaluated" in rows(w)[0]["text"]


@pytest.mark.parametrize("how", ["removed", "replaced"])
def test_a_removed_or_replaced_file_announces_every_waiting_block(tmp_path, how):
    chain, w, last, missed = setup(tmp_path)
    step(w, chain, last)
    write_missed(missed, [entry(b, first=w.clock()) for b in (400, 401, 405)])
    step(w, chain, last)
    missed.unlink()
    if how == "replaced":
        write_missed(missed, [entry(405, first=w.clock())])  # a new file (new inode), one entry recorded again
    step(w, chain, last)
    assert ranges(w) == [(400, 401)] if how == "replaced" else [(400, 401), (405, 405)]
    assert "removed or created again" in rows(w)[0]["text"]


@pytest.mark.parametrize("content", ["", "{not json", '{"block_number": 1}', '[{"block_number": 1}]',
                                     '[{"block_number": 1, "first_missed_at": 0, "retry_count": 0, '
                                     '"status": "Lost"}]', '[{"block_number": "1", "first_missed_at": 0, '
                                     '"status": "Pending"}]', "\udcff"])
def test_an_unreadable_or_garbage_file_is_health_never_clean(tmp_path, monkeypatch, content):
    monkeypatch.setattr(monitor_watch, "READ_RETRY_S", 0)
    chain, w, last, missed = setup(tmp_path)
    step(w, chain, last)
    if content == "\udcff":
        missed.write_bytes(b"\xff\xfe[")
    else:
        missed.write_text(content)
    step(w, chain, last)
    r = rows(w)
    assert len(r) == 1 and "cannot be read" in r[0]["text"]
    assert w.status()["monitor"]["missed"]["ok"] is False
    status = json.loads((tmp_path / "w.status.json").read_text())
    assert status["healthy"] is False and "missed-blocks file unreadable" in status["error"]
    assert 'ssv_watch_monitor_missed_blocks{status="pending"} -1' in metrics.render(w.db.conn, [])
    assert 'ssv_watch_input_unavailable{input="monitor"} 1' in metrics.render(w.db.conn, [])
    write_missed(missed, [])
    step(w, chain, last)
    assert len(rows(w)) == 1 and json.loads((tmp_path / "w.status.json").read_text())["healthy"] is True


def test_a_directory_is_unreadable(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor_watch, "READ_RETRY_S", 0)
    chain, w, last, missed = setup(tmp_path)
    missed.mkdir()
    step(w, chain, last)
    assert len(rows(w)) == 1 and "is unreadable" in rows(w)[0]["text"]


def test_a_torn_read_is_read_again_and_not_announced(tmp_path, monkeypatch):
    """The Monitor rewrites the file in place (truncate + write): one read may see a prefix."""
    monkeypatch.setattr(monitor_watch, "READ_RETRY_S", 0)
    chain, w, last, missed = setup(tmp_path)
    write_missed(missed, [entry(500, first=0)])
    real, torn = monitor_watch._read_text, []

    def read(path):
        text = real(path)
        if path == str(missed) and not torn:
            torn.append(1)
            return text[:15]
        return text

    monkeypatch.setattr(monitor_watch, "_read_text", read)
    step(w, chain, last)
    assert torn and [r for r in rows(w) if "cannot be read" in r["text"]] == []


def test_a_waiting_known_entry_survives_an_unreadable_episode(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor_watch, "READ_RETRY_S", 0)
    chain, w, last, missed = setup(tmp_path)
    step(w, chain, last)
    write_missed(missed, [entry(600, first=w.clock())])
    step(w, chain, last)
    missed.write_text("garbage")
    step(w, chain, last)
    write_missed(missed, [entry(600, "Failed", w.clock(), 10)])
    step(w, chain, last)
    assert ranges(w, "gave up") == [(600, 600)]


def test_the_rpc_error_text_is_never_sent(tmp_path):
    """last_error carries the Monitor's RPC error, which can name a keyed RPC URL."""
    chain, w, last, missed = setup(tmp_path)
    step(w, chain, last)
    write_missed(missed, [entry(700, "Failed", w.clock(), 10, error="Failed to connect to https://k.example/v1/S3CRET")])
    step(w, chain, last)
    assert len(rows(w)) == 1
    assert "S3CRET" not in json.dumps([dict(r) for r in outbox(w)]) and "S3CRET" not in json.dumps(w.status())


def test_restart_does_not_repeat_announcements(tmp_path):
    from fakechain import FakeRpc

    from eb_reconcile import DB, Config, Worker

    chain, w, last, missed = setup(tmp_path)
    step(w, chain, last)
    write_missed(missed, [entry(800, "Failed", w.clock(), 10)])
    step(w, chain, last)
    cfg = Config({**w.cfg.raw})
    w2 = Worker(cfg, FakeRpc(chain), DB(cfg.db_path), w.notifier, clock=w.clock, checks=[])
    w2.bootstrap()
    for _ in range(3):
        step(w2, chain, last)
    assert len(rows(w2)) == 1


# ---- QA cp4-repair1 F1 reproducer 3, as the Monitor records it since D21


def test_f1_post_outage_catch_up_with_filter_failures_is_never_silent(tmp_path):
    """ms-cp4r 2026-10-24T02:42Z: `Processed 51 blocks (range 26259527-26259577)` after 50 filter failures, then
    `monitor-recovered`. Since D21 those 50 blocks are in the missed-blocks file; the recovery retries them (10 per
    run); the ones it evaluates are silent, every other one is announced."""
    chain, w, last, missed = setup(tmp_path)
    step(w, chain, last)
    lo = chain.sealed - 60
    failed = [b for b in range(lo, lo + 51) if b != lo + 17]
    now = w.clock()
    store = {b: entry(b, first=now) for b in failed}
    write_missed(missed, list(store.values()))
    recovered = set()
    for run in range(12):  # one recovery run a minute, 10 oldest Pending blocks a run, max_retries 10
        for b in sorted(b for b, e in store.items() if e["status"] == "Pending")[:10]:
            if b % 3 == 0:  # these keep failing
                store[b]["retry_count"] += 1
                store[b]["status"] = "Failed" if store[b]["retry_count"] >= 10 else "Pending"
            else:
                recovered.add(b)
                del store[b]
        write_missed(missed, list(store.values()))
        step(w, chain, last)
        step(w, chain, last)
    while w.clock() - now < AFTER + 120:
        step(w, chain, last)
    never = set(failed) - recovered
    assert never and blocks_of(ranges(w)) >= never
    assert not blocks_of(ranges(w, "gave up")) & recovered


# ---- seeded sweep: the Monitor's main cycle + recovery job + file loss against the announcements


class MonitorModel:
    """Main cycle every 60 s (second 0): evaluates [max(last + 1, C - M), C], a block's filter fails with probability
    `p`, a failed block is recorded Pending. Recovery every 60 s (second 30): prunes entries older than max_block_age,
    retries the 10 oldest Pending ones, success with probability `q`, Failed at max_retries."""

    def __init__(self, last, missed, clock, rnd, max_block_age):
        self.last_path, self.missed_path, self.clock, self.rnd = last, missed, clock, rnd
        self.last, self.store, self.evaluated, self.recorded = None, {}, set(), set()
        self.max_block_age = max_block_age

    def save(self):
        write_missed(self.missed_path, [self.store[b] for b in sorted(self.store)])

    def cycle(self, head, p):
        c = head - 2
        lo = c if self.last is None else max(self.last + 1, c - M)
        if self.last is not None and self.last >= c:
            return
        fails = [b for b in range(lo, c + 1) if self.rnd.random() < p]
        self.evaluated |= set(range(lo, c + 1)) - set(fails)
        for b in fails:
            self.store.setdefault(b, entry(b, first=self.clock()))
            self.recorded.add(b)
        if fails:
            self.save()
        self.last = c
        self.last_path.write_text(str(c))

    def recover(self, head, q):
        c = head - 2
        self.store = {b: e for b, e in self.store.items() if b >= c - self.max_block_age}
        for b in sorted(b for b, e in self.store.items() if e["status"] == "Pending")[:10]:
            if self.rnd.random() < q:
                self.evaluated.add(b)
                del self.store[b]
            else:
                e = self.store[b]
                e["retry_count"] += 1
                e["status"] = "Failed" if e["retry_count"] >= 10 else "Pending"
        self.save()

    def lose_file(self):
        self.store = {}
        self.missed_path.unlink(missing_ok=True)


@pytest.mark.parametrize("seed", range(10))
def test_seeded_sweep_every_block_never_evaluated_is_announced(tmp_path, seed):
    rnd = random.Random(seed)
    chain, w, last, missed = setup(tmp_path, max_block_age=600)
    mon = MonitorModel(last, missed, w.clock, rnd, 600)
    mon.cycle(chain.sealed, 0)
    p = q = 0.0
    for minute in range(300):
        if minute % 20 == 0:  # endpoint weather: calm, or a rate-limited period
            p, q = rnd.choice([(0.0, 0.9), (0.05, 0.8), (0.6, 0.3), (0.9, 0.05)])
        if rnd.random() < 0.01:
            mon.lose_file()
        chain.mine(5)
        mon.cycle(chain.sealed, p)
        w.clock.advance(30)
        w.step()
        mon.recover(chain.sealed, q)
        w.clock.advance(30)
        w.step()
    for _ in range(int(AFTER / 30) + 4):  # time for the last waiting blocks to be announced
        chain.mine(2)
        mon.cycle(chain.sealed, 0)
        w.clock.advance(30)
        w.step()
    never = mon.recorded - mon.evaluated
    announced = blocks_of(ranges(w))
    assert never <= announced, f"seed {seed}: unannounced unevaluated blocks {sorted(never - announced)[:5]}"
    final = blocks_of(ranges(w, "gave up")) | blocks_of(ranges(w, "may not have been evaluated"))
    assert not final & mon.evaluated, f"seed {seed}: blocks announced as lost were evaluated"
    assert announced <= mon.recorded


# ---- the deployed configuration (D21)


def test_example_config_template_and_recovery_tuning_agree():
    net = json.loads((ROOT / "deploy/config/networks/evm_ethereum_mainnet.json").read_text())
    rc = net["recovery_config"]
    mon = monitor_watch.settings(load_config(str(ROOT / "reconcile/config.example.json")).raw["monitor"])
    assert mon["missed_blocks_path"] == f"/monitor/{net['slug']}_missed_blocks.json"
    assert mon["max_block_age"] == rc["max_block_age"]
    assert rc["enabled"] is True and rc["max_retries"] >= 5
    # the recovery job runs every minute, away from the main job's second, and a run is bounded
    sec, rest = rc["cron_schedule"].split(" ", 1)
    assert rest == "* * * * *" and sec.isdigit() and int(sec) != int(net["cron_schedule"].split()[0])
    assert 1 <= rc["max_blocks_per_run"] <= 25
    # a block retried every run is given up (and announced) before the waiting announcement could be its first
    assert rc["max_retries"] * 60 < mon["unevaluated_after_s"] < rc["max_block_age"] * 12 / 10


def test_default_worker_endpoints_follow_d21():
    from rpc import DEFAULT_RPC_URLS

    d21 = ["https://mainnet.gateway.tenderly.co", "https://rpc-eth.blockmachine.io",
           "https://gateway.tenderly.co/public/mainnet", "https://rpc.mevblocker.io"]
    assert list(DEFAULT_RPC_URLS) == d21
    assert load_config(str(ROOT / "reconcile/config.example.json")).raw["rpc_urls"] == d21
