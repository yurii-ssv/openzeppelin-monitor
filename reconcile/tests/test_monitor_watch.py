"""Monitor progress watch (D20, CP4 repair 1; soak-1 Monitor stall, event-0084).

The worker reads the Monitor's last processed block at every step: a Monitor more than `max_lag_blocks` behind the
head, a file that is missing, unreadable or not advancing, or an unknown head for `stall_after_s` is one
`monitor-stall` health message per stall (re-sent per health window), `monitor-recovered` when it catches up, and every
block range the Monitor skips is a `monitor-skipped` message naming it. Unknown is a stall, never clean.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest
from fakechain import FakeChain, FakeClock, FakeRpc
from harness import Capture, make_worker, outbox

import metrics
import monitor_watch
from eb_reconcile import DB, DEFAULTS, Config, Worker, load_config

ROOT = Path(__file__).resolve().parents[2]
M = 50  # max_past_blocks (the template's)


def setup(tmp_path, **watch):
    chain = FakeChain()
    path = tmp_path / "monitor" / "evm_ethereum_mainnet_last_block.txt"
    path.parent.mkdir()
    cfg = {"last_block_path": str(path), "max_past_blocks": M, **watch}
    _, w = make_worker(tmp_path, [], chain=chain, clock=FakeClock(), monitor=cfg)
    return chain, w, path


def write(path, block):
    path.write_text(str(block))


def rows(w, kind=None):
    out = [r for r in outbox(w, "kind='health'") if r["key"].startswith("health:monitor-")]
    return [r for r in out if r["key"].startswith(f"health:{kind}:")] if kind else out


def tick(w, chain, path=None, monitor_behind=3, blocks=5, seconds=60, advance_monitor=True):
    """One minute: the chain mines `blocks`, a healthy Monitor writes head - `monitor_behind`, the worker steps."""
    chain.mine(blocks)
    if path is not None and advance_monitor:
        write(path, chain.sealed - monitor_behind)
    w.clock.advance(seconds)
    w.step()


def status_file(tmp_path):
    return json.loads((tmp_path / "w.status.json").read_text())


def test_a_monitor_that_keeps_up_is_silent_and_reported_in_status(tmp_path):
    chain, w, path = setup(tmp_path)
    for _ in range(20):
        tick(w, chain, path)
    assert rows(w) == []
    st = w.status()["monitor"]
    assert st["enabled"] and not st["stalled"] and st["lag_blocks"] == 3 and st["last_block"] == chain.sealed - 3
    assert status_file(tmp_path)["healthy"] is True


def test_lag_above_threshold_for_stall_after_s_is_one_stall_then_recovery(tmp_path):
    chain, w, path = setup(tmp_path)
    tick(w, chain, path)
    mon = chain.sealed - 3
    for _ in range(10):  # the Monitor advances, but slower than the chain: 12, 21, 30, 39, ... blocks behind
        mon += 1
        write(path, mon)
        chain.mine(10)
        w.clock.advance(60)
        w.step()
    stall = rows(w, "monitor-stall")
    assert len(stall) == 1 and "blocks behind the head" in stall[0]["text"]
    assert w.status()["monitor"]["stalled"] and status_file(tmp_path)["healthy"] is False
    assert "Monitor stall 1" in status_file(tmp_path)["error"]
    tick(w, chain, path)  # caught up
    rec = rows(w, "monitor-recovered")
    assert len(rec) == 1 and "stall 1 lasted" in rec[0]["text"]
    assert status_file(tmp_path)["healthy"] is True and not w.status()["monitor"]["stalled"]


def test_short_lag_within_the_grace_is_silent(tmp_path):
    chain, w, path = setup(tmp_path)
    tick(w, chain, path)
    tick(w, chain, path, monitor_behind=40, seconds=120)  # behind for 2 min only
    tick(w, chain, path)
    assert rows(w) == []


def test_missing_file_is_a_stall(tmp_path):
    chain, w, path = setup(tmp_path)
    for _ in range(6):
        tick(w, chain)
    stall = rows(w, "monitor-stall")
    assert len(stall) == 1 and "is missing" in stall[0]["text"]
    assert w.status()["monitor"]["lag_blocks"] is None


def test_unreadable_file_is_a_stall(tmp_path):
    chain, w, path = setup(tmp_path)
    path.mkdir()  # a directory where the file should be: IsADirectoryError
    for _ in range(6):
        tick(w, chain)
    stall = rows(w, "monitor-stall")
    assert len(stall) == 1 and "is unreadable" in stall[0]["text"]


def test_unparseable_file_is_a_stall_but_one_empty_read_is_not(tmp_path):
    chain, w, path = setup(tmp_path)
    tick(w, chain, path)
    path.write_text("")  # the Monitor rewrites the file in place: an empty read for one step is not an alert
    tick(w, chain, advance_monitor=False)
    tick(w, chain, path)
    assert rows(w) == []
    for _ in range(6):
        path.write_text("garbage")
        tick(w, chain, advance_monitor=False)
    assert len(rows(w, "monitor-stall")) == 1 and "does not hold a block number" in rows(w)[0]["text"]


def test_not_advancing_is_a_stall_even_within_the_lag_threshold(tmp_path):
    """A head read from a stale endpoint can make the lag look small: a value that does not change is a stall."""
    chain, w, path = setup(tmp_path, max_lag_blocks=10_000)
    tick(w, chain, path)
    for _ in range(6):
        tick(w, chain, advance_monitor=False)
    stall = rows(w, "monitor-stall")
    assert len(stall) == 1 and "has not changed for" in stall[0]["text"]
    assert "keep up since 0 s" not in stall[0]["text"]  # the onset of a frozen file is its last change


def test_unknown_head_is_a_stall_never_clean(tmp_path):
    """Fail closed: while the worker cannot read the head, the Monitor is not positively known to keep up, even if its
    file advances; recovery needs a measured lag within the threshold."""
    chain, w, path = setup(tmp_path)
    tick(w, chain, path)
    chain.down = True
    for _ in range(6):
        tick(w, chain, path)
    stall = rows(w, "monitor-stall")
    assert len(stall) == 1 and "the head is unknown" in stall[0]["text"]
    chain.down = False
    tick(w, chain, path)
    assert len(rows(w, "monitor-recovered")) == 1


def test_a_second_stall_in_the_same_health_window_is_announced(tmp_path):
    chain, w, path = setup(tmp_path)
    tick(w, chain, path)
    for n in (1, 2):
        for _ in range(7):
            tick(w, chain, advance_monitor=False)
        tick(w, chain, path)
        assert len(rows(w, "monitor-stall")) == n, f"stall {n} not announced"
        assert len(rows(w, "monitor-recovered")) == n
    assert w.status()["monitor"]["stalls"] == 2


def test_a_long_stall_is_re_sent_once_per_health_window(tmp_path):
    chain, w, path = setup(tmp_path)
    tick(w, chain, path)
    for _ in range(7):
        tick(w, chain, advance_monitor=False)
    assert len(rows(w, "monitor-stall")) == 1
    for _ in range(4):  # 4 x 3 h = two more 6 h health windows
        tick(w, chain, advance_monitor=False, seconds=3 * 3600)
    assert 2 <= len(rows(w, "monitor-stall")) <= 3
    assert len({r["text"].split("(stall ")[1][:2] for r in rows(w, "monitor-stall")}) == 1  # one stall, re-sent


def test_restart_mid_stall_neither_forgets_nor_repeats_it(tmp_path):
    chain, w, path = setup(tmp_path)
    tick(w, chain, path)
    for _ in range(7):
        tick(w, chain, advance_monitor=False)
    assert len(rows(w, "monitor-stall")) == 1
    cfg = Config({**w.cfg.raw})
    w2 = Worker(cfg, FakeRpc(chain), DB(cfg.db_path), Capture(), clock=w.clock, checks=[])
    w2.bootstrap()
    tick(w2, chain, advance_monitor=False)
    assert len(rows(w2, "monitor-stall")) == 1 and w2.status()["monitor"]["stalled"]
    tick(w2, chain, path)
    assert len(rows(w2, "monitor-recovered")) == 1


def test_restart_before_the_threshold_keeps_the_onset(tmp_path):
    """The onset is durable: a restart inside the grace period does not restart the clock."""
    chain, w, path = setup(tmp_path)
    tick(w, chain, path)
    for _ in range(3):
        tick(w, chain, advance_monitor=False)
    cfg = Config({**w.cfg.raw})
    w2 = Worker(cfg, FakeRpc(chain), DB(cfg.db_path), Capture(), clock=w.clock, checks=[])
    w2.bootstrap()
    for _ in range(3):
        tick(w2, chain, advance_monitor=False)
    assert len(rows(w2, "monitor-stall")) == 1


def test_jump_beyond_max_past_blocks_names_the_skipped_range(tmp_path):
    chain, w, path = setup(tmp_path)
    tick(w, chain, path)
    prev = chain.sealed - 3
    chain.mine(200)  # the Monitor resumes 200 blocks later within one cycle: it reads [new - M, new] only
    new = chain.sealed - 2
    write(path, new)
    w.clock.advance(60)
    w.step()
    sk = rows(w, "monitor-skipped")
    assert len(sk) == 1
    lo, hi = prev + 1, new - M - 1
    assert f"blocks {lo}-{hi} ({hi - lo + 1})" in sk[0]["text"] and "upper bound" not in sk[0]["text"]
    st = w.status()["monitor"]
    assert st["skipped_blocks_total"] == hi - lo + 1 and st["last_skipped"] == [lo, hi]
    m = metrics.render(w.db.conn, [])
    assert f"ssv_watch_monitor_skipped_blocks_total {hi - lo + 1}" in m


def test_resume_in_two_quick_cycles_is_reported_as_a_superset(tmp_path):
    """ms-cp4r 2026-10-24T01:55Z: the catch-up cycle read 51 blocks in 67 s, the queued tick ran 1.5 s later; the
    worker saw one jump. The reported range covers the true one (never too short) and says it may end earlier."""
    chain, w, path = setup(tmp_path)
    tick(w, chain, path)
    prev = chain.sealed - 3
    chain.mine(80)
    first = chain.sealed - 2  # first cycle: reads [first - M, first], skips [prev + 1, first - M - 1]
    chain.mine(6)
    write(path, chain.sealed - 2)  # the second cycle followed at once; the worker only sees this value
    w.clock.advance(60)
    w.step()
    sk = rows(w, "monitor-skipped")
    lo, hi = map(int, sk[0]["text"].split(": blocks ")[1].split(" ")[0].split("-"))
    assert lo == prev + 1 and hi >= first - M - 1 and "never too short" in sk[0]["text"]


def test_jump_within_max_past_blocks_skips_nothing(tmp_path):
    chain, w, path = setup(tmp_path)
    tick(w, chain, path)
    prev = chain.sealed - 3
    chain.mine(M - 1)
    write(path, prev + M + 1)  # [prev + 1, new] = M + 1 blocks: one Monitor cycle reads all of them
    w.clock.advance(60)
    w.step()
    assert rows(w, "monitor-skipped") == []


def test_file_lost_and_recreated_at_the_head_names_everything_after_the_previous_block(tmp_path):
    chain, w, path = setup(tmp_path)
    tick(w, chain, path)
    prev = chain.sealed - 3
    path.unlink()
    tick(w, chain)
    tick(w, chain, path, monitor_behind=2)  # a Monitor without its file processes only the confirmed head
    new = chain.sealed - 2
    sk = rows(w, "monitor-skipped")
    assert len(sk) == 1 and f"blocks {prev + 1}-{new - 1}" in sk[0]["text"] and "file was missing" in sk[0]["text"]


def test_jump_after_an_unobserved_gap_is_reported_as_an_upper_bound(tmp_path):
    chain, w, path = setup(tmp_path)
    tick(w, chain, path)
    chain.mine(300)
    write(path, chain.sealed - 2)
    w.clock.advance(1800)  # the worker did not step for 30 min (a long run, a restart)
    w.step()
    sk = rows(w, "monitor-skipped")
    assert len(sk) == 1 and "upper bound" in sk[0]["text"]
    # QA cp4-repair1 N3: a healthy Monitor may have evaluated every block meanwhile; the message leads with that
    assert "may not have been evaluated" in sk[0]["text"] and "were NOT evaluated" not in sk[0]["text"]


def test_file_created_again_between_two_reads_is_a_reset(tmp_path):
    """QA cp4-repair1 N2 (test_q3_file_lost_and_recreated_between_two_reads_is_too_short): the Monitor's volume was
    replaced while the worker did not look; the Monitor restarted at the head (L = 0 reads only C), so everything
    after the previous value is skipped although the jump is shorter than max_past_blocks. The new file has a new
    inode (the Monitor rewrites its file in place otherwise)."""
    chain, w, path = setup(tmp_path)
    tick(w, chain, path)
    prev = chain.sealed - 3
    chain.mine(20)
    new = chain.sealed - 2
    fresh = path.with_name("fresh")
    fresh.write_text(str(new))  # created while the old file still exists: a different inode
    fresh.replace(path)
    w.clock.advance(30)
    w.step()
    sk = rows(w, "monitor-skipped")
    assert len(sk) == 1 and f"blocks {prev + 1}-{new - 1}" in sk[0]["text"] and "file was missing" in sk[0]["text"]
    tick(w, chain, path)
    assert len(rows(w, "monitor-skipped")) == 1  # an in-place rewrite is no reset


def test_a_stall_is_delivered_between_check_runs_of_a_long_step(tmp_path):
    """A step can run for many minutes (the silent evaluation after the backfill took > 10 min on ms-cp4r): the watch
    observes between two check runs with the head extrapolated from the last read, and delivers what it queued."""
    from test_framework import Stub

    from checks.base import Every

    chain = FakeChain()
    path = tmp_path / "evm_ethereum_mainnet_last_block.txt"
    seen = []

    slow_now = []

    def slow(ctx, block):
        if slow_now:
            for _ in range(8):  # 8 minutes inside one check run; the Monitor writes nothing meanwhile
                ctx.clock.advance(60)
        return []

    def probe(ctx, block):
        seen.append([k for _, k, _, _ in w.notifier.sent if k.startswith("health:monitor-stall:")])
        return []

    checks = [Stub(slow, check_id="slow", schedule=(Every(1),)), Stub(probe, check_id="probe", schedule=(Every(1),))]
    _, w = make_worker(tmp_path, checks, chain=chain, clock=FakeClock(),
                       monitor={"last_block_path": str(path), "max_past_blocks": M})
    tick(w, chain, path)  # a healthy observation first
    assert seen[-1] == []
    slow_now.append(True)
    tick(w, chain, advance_monitor=False)  # the slow check runs 8 min with a frozen Monitor; the probe right after
    assert seen[-1], "monitor-stall not delivered before the next check of the same step"
    stall = rows(w, "monitor-stall")
    assert len(stall) == 1 and "has not changed for" in stall[0]["text"]


def test_watch_is_off_without_a_monitor_section(tmp_path):
    chain = FakeChain()
    _, w = make_worker(tmp_path, [], chain=chain, clock=FakeClock())
    for _ in range(10):
        tick(w, chain)
    assert w.status()["monitor"] == {"enabled": False} and rows(w) == []


def test_metrics_expose_lag_and_stall(tmp_path):
    chain, w, path = setup(tmp_path)
    tick(w, chain, path)
    m = metrics.render(w.db.conn, [])
    assert "ssv_watch_monitor_lag_blocks 3" in m and "ssv_watch_monitor_stalled 0" in m
    assert f"ssv_watch_monitor_last_block {chain.sealed - 3}" in m
    for _ in range(7):
        tick(w, chain, advance_monitor=False)
    m = metrics.render(w.db.conn, [])
    assert "ssv_watch_monitor_stalled 1" in m and 'ssv_watch_input_unavailable{input="monitor"} 1' in m


def test_unknown_monitor_setting_fails_at_start(tmp_path):
    with pytest.raises(SystemExit, match="unknown key"):
        monitor_watch.settings({"last_block_path": "/x", "max_lag": 3})
    with pytest.raises(SystemExit, match="positive"):
        monitor_watch.settings({"last_block_path": "/x", "stall_after_s": 0})


# ---- the deployed configuration matches the watch (D20)


def test_example_config_and_templates_agree():
    net = json.loads((ROOT / "deploy/config/networks/evm_ethereum_mainnet.json").read_text())
    cfg = load_config(str(ROOT / "reconcile/config.example.json"))
    mon = cfg.raw["monitor"]
    assert mon["max_past_blocks"] <= net["max_past_blocks"], "the worker would hide skipped blocks"
    assert mon["last_block_path"].endswith(f"/{net['slug']}_last_block.txt")
    assert net["cron_schedule"].split()[0].isdigit() and len(net["cron_schedule"].split()) == 6  # once a minute
    assert mon["cycle_s"] == 60
    plain = [u["url"]["value"] for u in net["rpc_urls"] if u["url"]["type"] == "plain"]
    assert len([u for u in plain if "tenderly" not in u]) >= 2
    compose = (ROOT / "docker-compose.reconcile.yaml").read_text()
    assert "target: /monitor" in compose and mon["last_block_path"].startswith("/monitor/")
    # D21: the worker prefers blockmachine and keeps the Monitor's serving endpoint (mevblocker) for last
    assert cfg.raw["rpc_urls"][1] == "https://rpc-eth.blockmachine.io"
    assert cfg.raw["rpc_urls"][-1] == "https://rpc.mevblocker.io"


# ---- seeded adversarial sweep: a Monitor model (the Rust cycle) against the worker's announcements


class MonitorModel:
    """The Monitor's EVM cycle (src/services/blockwatcher/service.rs process_new_blocks): every 60 s, when its RPC
    works, it evaluates [max(last + 1, confirmed - M), confirmed] (only `confirmed` when it has no last block) and
    writes `confirmed`; a failed cycle changes nothing."""

    def __init__(self, path: Path):
        self.path, self.last, self.evaluated = path, None, set()

    def cycle(self, head: int, ok: bool) -> None:
        if not ok:
            return
        confirmed = head - 2
        if self.last is None:
            lo = confirmed
        elif self.last >= confirmed:
            lo = confirmed + 1
        else:
            lo = max(self.last + 1, confirmed - M)
        self.evaluated |= set(range(lo, confirmed + 1))
        self.last = confirmed
        self.path.write_text(str(confirmed))

    def lose_file(self) -> None:
        self.last = None
        self.path.unlink(missing_ok=True)


@pytest.mark.parametrize("seed", range(12))
def test_seeded_sweep_every_skipped_block_is_announced_and_no_stall_is_silent(tmp_path, seed):
    rnd = random.Random(seed)
    chain, w, path = setup(tmp_path)
    mon = MonitorModel(path)
    mon.cycle(chain.sealed, True)
    first = mon.last
    down_until = worker_down_until = -1
    was_stalled = False
    bad_since = None  # oracle: start of the current period in which the Monitor is not positively keeping up
    t = 0
    for minute in range(240):
        if rnd.random() < 0.03:
            down_until = minute + rnd.choice([3, 8, 15, 40])  # every Monitor endpoint fails for a while
        if rnd.random() < 0.01:
            mon.lose_file()  # its volume was replaced: the Monitor restarts (misses this cycle) at the head
            down_until = max(down_until, minute + 1)
        if rnd.random() < 0.02:
            worker_down_until = minute + rnd.choice([2, 7])  # the worker's own RPC fails: head unknown
        for half in (0, 1):  # the worker steps every 30 s, the Monitor cycles every 60 s
            if half == 0:
                chain.mine(5)
                mon.cycle(chain.sealed, minute >= down_until)
            chain.down = minute < worker_down_until
            w.clock.advance(30)
            t += 30
            if rnd.random() < 0.02:  # restart: a new process on the same database
                cfg = Config({**w.cfg.raw})
                w = Worker(cfg, FakeRpc(chain), DB(cfg.db_path), w.notifier, clock=w.clock, checks=[])
            w.step()  # bootstraps lazily after a restart
            ok = (not chain.down and mon.last is not None and chain.sealed - mon.last <= 30)
            bad_since = None if ok else (t if bad_since is None else bad_since)
            st = w.status()["monitor"]
            stall_s = monitor_watch.DEFAULTS["stall_after_s"]
            if bad_since is not None and t - bad_since >= stall_s + 30:
                assert st["stalled"], f"seed {seed} minute {minute}: Monitor not keeping up since {bad_since}, silent"
            if st["stalled"] and not was_stalled and mon.last is not None and minute >= worker_down_until:
                # D20 (coordinator): a stopped Monitor is announced before its lag can reach max_past_blocks
                assert chain.sealed - mon.last <= M, f"seed {seed} minute {minute}: stall announced only at lag " \
                    f"{chain.sealed - mon.last}"
            was_stalled = st["stalled"]
            advancing = st["changed_at"] is not None and w.clock() - st["changed_at"] < stall_s
            if ok and advancing:
                assert not st["stalled"], f"seed {seed} minute {minute}: stall kept after a proven recovery"
    chain.down = False
    for _ in range(12):  # end healthy
        chain.mine(5)
        mon.cycle(chain.sealed, True)
        w.clock.advance(30)
        w.step()
    skipped = set(range(first + 1, mon.last + 1)) - mon.evaluated
    announced = set()
    for r in rows(w, "monitor-skipped"):
        lo, hi = map(int, r["text"].split(": blocks ")[1].split(" ")[0].split("-"))
        assert not set(range(lo, hi + 1)) & mon.evaluated, f"seed {seed}: announced evaluated blocks {lo}-{hi}"
        announced |= set(range(lo, hi + 1))
    assert announced == skipped, f"seed {seed}: unannounced skipped blocks {sorted(skipped - announced)[:5]}"
    assert not w.status()["monitor"]["stalled"]
    stalls = len({r["text"].split("(stall ")[1].split(")")[0] for r in rows(w, "monitor-stall")})
    assert len({r["key"] for r in rows(w, "monitor-recovered")}) == stalls == w.status()["monitor"]["stalls"]


def test_defaults_are_stated_for_a_60_s_cron():
    assert monitor_watch.DEFAULTS["max_lag_blocks"] == 30 and monitor_watch.DEFAULTS["stall_after_s"] == 240
    # announced before any block is lost: ~2 blocks behind at the last write, + the grace and one worker step
    lag_at_alert = 2 + (monitor_watch.DEFAULTS["stall_after_s"] + DEFAULTS["poll_interval_s"]) / 12
    assert lag_at_alert < monitor_watch.DEFAULTS["max_past_blocks"] / 2
    assert DEFAULTS["poll_interval_s"] <= monitor_watch.DEFAULTS["cycle_s"]  # the worker sees every Monitor write
