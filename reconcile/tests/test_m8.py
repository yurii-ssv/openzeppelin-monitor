"""M8 floor — cSSV.totalSupply() below 2 × the seed holder's cSSV balance (spec §3 M8, D3; plan §7 V2x.*)."""

from __future__ import annotations

import json
import random
import re

import pytest
from fixtures_wallets import BALANCE_OF, CSSV, DAO_SAFE, TOTAL_SUPPLY, WalletChain, stub_cssv, wallet_worker
from harness import advance, outbox

from rpc import RpcError

pytestmark = pytest.mark.m8

SEED = 1_000 * 10**18


def setup(tmp_path, supply, seed=SEED, checks=None, **worker):
    chain = WalletChain()
    stub_cssv(chain, supply, seed)
    chain.mine()
    chain, w = wallet_worker(tmp_path, ["m8"], chain=chain, checks={"m8": checks or {}}, **worker)
    return chain, w


def tick(w, chain, blocks=1, seconds=301):
    chain.mine(blocks)
    w.clock.advance(seconds)
    advance(w)


def alerts(w):
    return [(r["severity"], r["kind"]) for r in outbox(w, "kind NOT IN ('health', 'digest')")]


def state(w):
    return w.db.meta("check:m8:floor")


def open_incidents(w):
    return [dict(r) for r in w.db.conn.execute("SELECT * FROM incidents WHERE state='open'")]


# ---------------------------------------------------------------- V2x.1


def test_m8_supply_below_twice_the_seed_warns(tmp_path):
    chain, w = setup(tmp_path, supply=2 * SEED - 1)
    advance(w)
    assert alerts(w) == [("warn", "open")]
    (inc,) = open_incidents(w)
    ev = json.loads(inc["evidence"])
    assert inc["key"] == "supply-floor" and ev["floor"] == str(2 * SEED) and ev["seed_balance"] == str(SEED)
    assert ev["total_supply"] == str(2 * SEED - 1) and ev["floor_measured_block"] == chain.sealed


def test_m8_supply_at_the_floor_is_silent(tmp_path):
    chain, w = setup(tmp_path, supply=2 * SEED)
    advance(w)
    assert alerts(w) == [] and state(w)["floor"] == str(2 * SEED)


def test_m8_supply_well_above_the_floor_is_silent(tmp_path):
    chain, w = setup(tmp_path, supply=50 * SEED)
    advance(w)
    assert alerts(w) == []


def test_m8_zero_supply_warns_even_when_the_seed_is_gone(tmp_path):
    """Seed 0 makes the floor 0; zero supply (BUG-6's window) still warns."""
    chain, w = setup(tmp_path, supply=0, seed=0)
    advance(w)
    assert alerts(w) == [("warn", "open")]
    assert "is zero" in outbox(w)[0]["text"]


def test_m8_supply_recovers_and_resolves(tmp_path):
    chain, w = setup(tmp_path, supply=SEED)
    advance(w)
    stub_cssv(chain, 3 * SEED, SEED)
    tick(w, chain)
    assert alerts(w) == [("warn", "open"), ("info", "resolved")]


# ---------------------------------------------------------------- V2x.2


def test_m8_thresholds_from_chain_daily_remeasure_moves_the_floor(tmp_path):
    """D3: the floor is re-measured daily from the seed holder's balance on the chain; a change of that balance
    changes the alert without a code or config change, but only at the re-measurement."""
    chain, w = setup(tmp_path, supply=3 * SEED)
    advance(w)
    stub_cssv(chain, 3 * SEED, 2 * SEED)  # the seed holder doubled its stake: floor 4 × SEED after re-measure
    tick(w, chain, seconds=3_600)
    assert alerts(w) == [] and state(w)["seed"] == str(SEED)  # not re-measured within the day
    tick(w, chain, seconds=86_400)
    assert alerts(w) == [("warn", "open")] and state(w)["floor"] == str(4 * SEED)
    assert state(w)["block"] == chain.sealed


def test_m8_thresholds_from_config_multiplier(tmp_path):
    chain, w = setup(tmp_path, supply=3 * SEED, checks={"floor_multiplier": 4})
    advance(w)
    assert alerts(w) == [("warn", "open")] and state(w)["floor"] == str(4 * SEED)


def test_m8_required_addresses_raise_at_construction(tmp_path):
    with pytest.raises(SystemExit, match="checks.m8.cssv is required"):
        wallet_worker(tmp_path, ["m8"], checks={"m8": {"cssv": None}})


# ---------------------------------------------------------------- V2x.3


def test_m8_restart_does_not_duplicate_and_remeasures_at_start(tmp_path):
    chain, w = setup(tmp_path, supply=SEED)
    advance(w)
    stub_cssv(chain, SEED, SEED // 4)  # floor would be SEED / 2 at the next measurement
    chain.mine()
    _, w2 = wallet_worker(tmp_path, ["m8"], chain=chain, start_block=w.cfg.start_block,
                          activation_block=w.cfg.activation_block, clock=w.clock)
    tick(w2, chain, blocks=0, seconds=10)
    assert state(w2)["seed"] == str(SEED // 4)  # D3: measured at worker start
    assert alerts(w2) == [("warn", "open"), ("info", "resolved")]
    stub_cssv(chain, SEED, SEED)
    chain.mine()
    _, w3 = wallet_worker(tmp_path, ["m8"], chain=chain, start_block=w.cfg.start_block,
                          activation_block=w.cfg.activation_block, clock=w.clock)
    tick(w3, chain, seconds=10)
    _, w4 = wallet_worker(tmp_path, ["m8"], chain=chain, start_block=w.cfg.start_block,
                          activation_block=w.cfg.activation_block, clock=w.clock)
    tick(w4, chain, seconds=10)
    assert alerts(w4) == [("warn", "open"), ("info", "resolved"), ("warn", "open")]
    assert w4.db.conn.execute("SELECT COUNT(*) FROM incidents WHERE state='open'").fetchone()[0] == 1


# ---------------------------------------------------------------- inputs: missing is an error, never "fine"


def test_m8_empty_call_result_is_an_error_not_clean(tmp_path):
    chain = WalletChain()
    stub_cssv(chain, 0, SEED)
    chain.stub_call(CSSV, TOTAL_SUPPLY, b"")  # "0x": e.g. a wrong address with no code
    chain.mine()
    chain, w = wallet_worker(tmp_path, ["m8"], chain=chain)
    advance(w)
    assert alerts(w) == [] and w.db.conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 0
    (h,) = outbox(w, "kind='health'")
    assert "Check m8 raised ValueError: cSSV.totalSupply() returned 0 bytes" in h["text"]


def test_m8_revert_raises_an_rpc_error(tmp_path):
    chain = WalletChain()
    stub_cssv(chain, SEED, SEED)
    chain.stub_call(CSSV, TOTAL_SUPPLY, revert="0x")
    chain.mine()
    chain, w = wallet_worker(tmp_path, ["m8"], chain=chain)
    with pytest.raises(RpcError):
        advance(w)
    assert state(w) is None  # rolled back with the run


# ---------------------------------------------------------------- reorgs


def _drop_below_floor_then_reorg(tmp_path, **worker):
    chain, w = setup(tmp_path, supply=3 * SEED, **worker)
    advance(w)
    tick(w, chain, blocks=3)
    fork = chain.number
    stub_cssv(chain, SEED, SEED)  # supply drops below the floor in the block `fork`
    return chain, w, fork


def test_m8_reorg_depth_1_removing_the_drop_resolves(tmp_path):
    chain, w, fork = _drop_below_floor_then_reorg(tmp_path)
    tick(w, chain)
    assert alerts(w) == [("warn", "open")]
    chain.reorg(fork)
    tick(w, chain)
    assert alerts(w) == [("warn", "open"), ("info", "resolved")] and open_incidents(w) == []
    w.clock.advance(90_000)
    tick(w, chain)
    assert len(alerts(w)) == 2


def test_m8_reorg_depth_n_remeasures_a_floor_read_on_deleted_blocks(tmp_path):
    """The daily re-measurement happened on a block the reorg deleted: it is re-taken at the new checkpoint."""
    chain, w = setup(tmp_path, supply=3 * SEED, confirmations=2)
    tick(w, chain, blocks=3)
    tick(w, chain, blocks=3)
    fork = chain.number
    stub_cssv(chain, 3 * SEED, 2 * SEED)  # on the deleted chain the seed doubles: floor 4 × SEED → warn
    tick(w, chain, blocks=5, seconds=86_401)
    assert alerts(w) == [("warn", "open")] and state(w)["seed"] == str(2 * SEED)
    chain.reorg(fork)
    tick(w, chain, blocks=6)
    assert state(w)["seed"] == str(SEED) and state(w)["block"] > fork - 1
    assert alerts(w) == [("warn", "open"), ("info", "resolved")]


def test_m8_reorg_marker_survives_a_crash_before_the_run(tmp_path):
    chain, w, fork = _drop_below_floor_then_reorg(tmp_path)
    tick(w, chain)
    chain.reorg(fork)
    chain.mine()
    w.check_reorg()
    _, w2 = wallet_worker(tmp_path, ["m8"], chain=chain, start_block=w.cfg.start_block,
                          activation_block=w.cfg.activation_block, clock=w.clock)
    advance(w2)
    assert open_incidents(w2) == [] and w2.db.meta("reorg:m8") is None
    assert alerts(w2) == [("warn", "open"), ("info", "resolved")]


def test_m8_reorg_keeps_a_drop_the_new_chain_still_has(tmp_path):
    chain, w = setup(tmp_path, supply=SEED)
    advance(w)
    tick(w, chain, blocks=2)
    fork = chain.number
    tick(w, chain, blocks=2)
    chain.reorg(fork)
    tick(w, chain)
    assert alerts(w) == [("warn", "open")] and len(open_incidents(w)) == 1


def test_m8_digest_line(tmp_path):
    chain, w = setup(tmp_path, supply=3 * SEED)
    advance(w)
    (line,) = w.checks["m8"].digest(None)
    assert line.startswith("M8 cSSV supply 3000.0000") and "2 × seed 1000.0000" in line


def test_m8_reads_the_configured_holder(tmp_path):
    chain, w = setup(tmp_path, supply=3 * SEED)
    advance(w)
    calls = {(to, data[:10]) for to, data, _ in chain.calls}
    assert calls == {(CSSV, TOTAL_SUPPLY), (CSSV, "0x70a08231")}
    assert any(data.endswith(DAO_SAFE[2:]) for _, data, _ in chain.calls)


# ---------------------------------------------------------------- repair 1: generalised audit (README §6.2)


def test_m8_reorg_removing_the_recovery_of_a_resolved_key_reopens_it(tmp_path):
    """(c) the evidence of an ALREADY RESOLVED key leaves the chain: the stake that restored the supply was on the
    deleted blocks; the reorg run evaluates the key again and the warning comes back (a new incident)."""
    chain, w = setup(tmp_path, supply=SEED)
    advance(w)
    tick(w, chain, blocks=2)
    fork = chain.number
    stub_cssv(chain, 3 * SEED, SEED)
    tick(w, chain)
    assert alerts(w) == [("warn", "open"), ("info", "resolved")]
    chain.reorg(fork)
    tick(w, chain)
    assert alerts(w) == [("warn", "open"), ("info", "resolved"), ("warn", "open")] and len(open_incidents(w)) == 1
    assert w.db.meta("reorg:m8") is None


def test_m8_reorg_run_that_cannot_re_measure_raises_and_keeps_the_marker(tmp_path):
    """(b) error input in the reorg run: the floor measured on a deleted block cannot be re-taken; the run rolls
    back (incident, marker and orphan floor untouched), retried every step, then commits."""
    chain, w = setup(tmp_path, supply=3 * SEED, confirmations=2)
    tick(w, chain, blocks=3)
    fork = chain.number
    stub_cssv(chain, 3 * SEED, 2 * SEED)
    tick(w, chain, blocks=5, seconds=86_401)  # daily re-measure on the soon-deleted chain: floor 4 × SEED → warn
    assert alerts(w) == [("warn", "open")]
    chain.reorg(fork)
    chain.fail_methods.add("eth_call")
    with pytest.raises(RpcError):
        tick(w, chain, blocks=6)
    assert w.db.meta("reorg:m8") is not None and len(open_incidents(w)) == 1 and state(w)["seed"] == str(2 * SEED)
    chain.fail_methods.clear()
    tick(w, chain)
    assert w.db.meta("reorg:m8") is None and open_incidents(w) == [] and state(w)["seed"] == str(SEED)
    assert alerts(w) == [("warn", "open"), ("info", "resolved")]


def test_m8_reorg_re_evaluates_an_open_incident_last_seen_below_the_ancestor(tmp_path):
    """An open incident NOT in ctx.reorg_keys (m8 failed for a few steps, so it was last seen below the ancestor)
    is still evaluated by the reorg run: the new chain restored the supply → resolved with the check's text."""
    chain, w = setup(tmp_path, supply=SEED)
    advance(w)
    (inc,) = open_incidents(w)
    chain.fail_methods.add("eth_call")
    for _ in range(3):
        with pytest.raises(RpcError):
            tick(w, chain)
    fork = chain.number - 1
    assert fork - 1 > inc["last_seen_block"]
    chain.reorg(fork)
    stub_cssv(chain, 3 * SEED, SEED)
    chain.fail_methods.clear()
    tick(w, chain)
    assert open_incidents(w) == [] and w.db.meta("reorg:m8") is None
    (res,) = outbox(w, "kind='resolved'")
    assert "cSSV supply above the floor" in res["text"] and "evidence left the canonical chain" not in res["text"]


@pytest.mark.parametrize("size", [31, 64])
def test_m8_partial_or_oversized_call_result_raises(tmp_path, size):
    """(g) partial responses: anything but one 32-byte word is an error, never a supply."""
    chain = WalletChain()
    stub_cssv(chain, 3 * SEED, SEED)
    chain.stub_call(CSSV, TOTAL_SUPPLY, b"\x01" * size)
    chain.mine()
    chain, w = wallet_worker(tmp_path, ["m8"], chain=chain)
    advance(w)
    assert alerts(w) == [] and w.db.conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 0
    (h,) = outbox(w, "kind='health'")
    assert f"returned {size} bytes" in h["text"]


# ---------------------------------------------------------------- repair 1: deterministic adversarial sweep
# Seeded cases (40): supply and DAO Safe balance change at random blocks (zero included), clock jumps that make the
# daily re-measurement fall on any block (also on the blocks the reorg deletes), restarts (re-measure at start),
# eth_call outages, then one reorg 1..24 blocks below the checkpoint, sometimes with a crash between its detection
# and the run and an outage in the reorg run, on a shorter or longer replacement chain with other values.
# Invariants: no open incident without supporting evidence and no dropped warning (open ⇔ the canonical supply at
# the checkpoint is below the floor or zero); every message after the reorg (opens, re-alerts, resolutions) quotes
# the canonical supply of its block and agrees with it (no resolve without positive proof, no warning for orphaned
# evidence); the reorg rule never resolves by absence (the key is evaluated every run); the floor in use was
# measured on a canonical block (no window anchored on stale data); nothing pending, healthy.

WARN_RE = re.compile(r"cSSV\.totalSupply\(\) = (\d+) at block (\d+).*?'s ([\d.]+) cSSV measured at block (\d+)", re.S)
RES_RE = re.compile(r"cSSV\.totalSupply\(\) = ([\d.]+) at block (\d+), at or above the floor (\d+\.\d+)")


class M8Sweep:
    def __init__(self, tmp_path, seed, confirmations):
        self.rng = random.Random(f"cp2c-m8:{seed}:{confirmations}")
        self.tmp, self.c = tmp_path, confirmations
        self.chain = WalletChain()
        self.restub()
        self.chain.mine(1 + confirmations)
        self.start = self.chain.sealed - confirmations
        self.clock = None
        self.w = self.worker()
        self.w.step()

    def worker(self):
        _, w = wallet_worker(self.tmp, ["m8"], chain=self.chain, start_block=self.start, activation_block=self.start,
                             confirmations=self.c, **({"clock": self.clock} if self.clock else {}))
        self.clock = w.clock
        return w

    def restub(self):
        rng = self.rng
        stub_cssv(self.chain, rng.choice((0, 1, 2, 3, 4, 5)) * SEED, rng.choice((0, 1, 2)) * SEED)

    def blocks(self, n):
        for _ in range(n):
            if self.rng.random() < 0.4:
                self.restub()
            self.chain.mine()

    def wstep(self, fail=False, seconds=None):
        if fail:
            self.chain.fail_methods.add("eth_call")
        self.clock.advance(seconds or self.rng.choice((12, 301, 301, 86_401)))
        self.w.step()
        self.chain.fail_methods.discard("eth_call")

    def run(self):
        rng = self.rng
        for _ in range(rng.randint(6, 14)):
            self.blocks(rng.randint(1, 3))
            if rng.random() < 0.15:
                self.w = self.worker()
            self.wstep(fail=rng.random() < 0.15)
        self.wstep()
        fork = self.chain.number
        self.blocks(rng.randint(1, 24))
        if rng.random() < 0.6:  # the daily re-measurement falls on a block the reorg deletes, with another seed
            stub_cssv(self.chain, self.supply(self.chain.sealed), rng.choice((3, 4)) * SEED)
            self.chain.mine()
        self.chain.mine(self.c)
        self.wstep(seconds=86_401)
        self.depth = self.w.checkpoint()["block"] - fork + 1
        self.fork = fork
        pre = self.w.db.conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM outbox").fetchone()[0]
        self.chain.reorg(fork)
        self.blocks(rng.randint(1, 4))
        if rng.random() < 0.3:
            self.w.check_reorg()
            self.w = self.worker()
        if rng.random() < 0.3:
            self.wstep(fail=True)
            assert self.w.db.meta("reorg:m8") is not None
        self.reorg_cp = None
        for _ in range(rng.randint(1, 4)):
            self.blocks(1)
            self.wstep()
            if self.reorg_cp is None and self.w.db.meta("reorg:m8") is None:
                self.reorg_cp = self.w.checkpoint()["block"]
        self.chain.mine()
        self.clock.advance(301)
        self.w.step()
        return pre

    def at(self, data, block):
        return int(self.chain._eth_call({"to": CSSV, "data": data}, block), 16)

    def supply(self, block):
        return self.at(TOTAL_SUPPLY, block)

    def seed(self, block):
        return self.at(BALANCE_OF + "0" * 24 + DAO_SAFE[2:], block)


@pytest.mark.parametrize("confirmations", [0, 2])
@pytest.mark.parametrize("seed", range(20))
def test_m8_adversarial_reorg_sweep(tmp_path, seed, confirmations):
    s = M8Sweep(tmp_path, seed, confirmations)
    pre = s.run()
    w, cp = s.w, s.w.checkpoint()["block"]
    assert s.depth >= 1 and s.reorg_cp is not None
    st = state(w)
    f = st["block"]
    assert int(st["seed"]) == s.seed(f) and st["floor"] == str(2 * s.seed(f)), "floor measured on a deleted block"
    assert f < s.fork or f >= s.reorg_cp
    supply = s.supply(cp)
    want = supply < 2 * int(st["seed"]) or supply == 0
    assert [(i["key"], i["severity"]) for i in open_incidents(w)] == ([("supply-floor", "warn")] if want else [])
    for r in outbox(w, "kind NOT IN ('health', 'digest')"):
        assert "evidence left the canonical chain" not in r["text"]
    for r in outbox(w, f"rowid > {pre} AND kind NOT IN ('health', 'digest')"):
        if r["kind"] == "resolved":
            sup, b, floor = RES_RE.search(r["text"]).groups()
            assert float(sup) * 1e18 == s.supply(int(b)) and float(sup) >= float(floor) and float(sup) > 0
        else:
            sup, b, seed_eth, fb = WARN_RE.search(r["text"]).groups()
            assert int(sup) == s.supply(int(b)), f"{r['kind']} quotes a supply the canonical block {b} does not have"
            assert float(seed_eth) * 1e18 == s.seed(int(fb))
            assert int(sup) < 2 * s.seed(int(fb)) or int(sup) == 0
    assert w.degraded() == [] and w.db.meta("reorg:m8") is None


# ---------------------------------------------------------------- repair 2: QA attempt 2 scenarios (Appendix C1, ported verbatim
# except the module prefix; expected outcomes from the spec, D3/D4/D10 and README §6.2)

def test_qa2_m8_restart_whose_first_run_fails_after_the_seed_read_still_measures_at_start(tmp_path):
    """D3 'measured at worker start' + class 10: restart; the Safe's cSSV moved while the worker was down; the
    first run reads balanceOf, then totalSupply fails (rolled back). The next run must use a floor measured in
    THIS process, not the previous process's floor."""
    chain, w = setup(tmp_path, supply=3 * SEED)
    advance(w)
    assert state(w)["seed"] == str(SEED)
    stub_cssv(chain, 3 * SEED, 2 * SEED)  # Safe staked more while down: floor 4 x SEED > supply
    chain.mine()
    _, w2 = wallet_worker(tmp_path, ["m8"], chain=chain, start_block=w.cfg.start_block,
                          activation_block=w.cfg.activation_block, clock=w.clock)
    orig = chain._handle
    fail = {"n": 1}

    def handle(m, p):
        if m == "eth_call" and p[0].get("data", p[0].get("input", "")).startswith("0x18160ddd") and fail["n"]:
            fail["n"] -= 1
            raise ValueError("totalSupply transport error (test)")
        return orig(m, p)

    chain._handle = handle
    w2.step()  # balanceOf ok, totalSupply fails: rolled back
    assert fail["n"] == 0 and w2.db.meta("run:m8")["errors"] >= 1
    chain.mine()
    w2.clock.advance(301)
    w2.step()
    assert state(w2)["seed"] == str(2 * SEED), state(w2)
    assert alerts(w2)[-1:] == [("warn", "open")], alerts(w2)


def test_qa2_m8_daily_remeasure_on_a_deleted_block_then_reorg_with_a_different_safe_balance(tmp_path):
    chain, w = setup(tmp_path, supply=3 * SEED, confirmations=2)
    advance(w)
    tick(w, chain, blocks=3)
    fork = chain.number
    stub_cssv(chain, 3 * SEED, SEED // 2)  # deleted chain: Safe sold half
    tick(w, chain, blocks=5, seconds=86_400)  # daily re-measure on the deleted chain
    assert state(w)["seed"] == str(SEED // 2)
    chain.reorg(fork)
    stub_cssv(chain, 3 * SEED, 2 * SEED)  # new chain: Safe staked more → floor 4 x SEED > supply
    tick(w, chain, blocks=6)
    assert state(w)["seed"] == str(2 * SEED) and state(w)["block"] > fork
    assert [a for a in alerts(w)] == [("warn", "open")]


def test_qa2_m8_resolved_key_reorg_where_the_new_chain_still_recovers_is_silent(tmp_path):
    chain, w = setup(tmp_path, supply=SEED, confirmations=2)
    advance(w)
    tick(w, chain, blocks=3)
    fork = chain.number
    stub_cssv(chain, 3 * SEED, SEED)
    tick(w, chain, blocks=5)
    assert alerts(w) == [("warn", "open"), ("info", "resolved")]
    chain.reorg(fork)
    stub_cssv(chain, 3 * SEED, SEED)  # the recovery re-included
    tick(w, chain, blocks=6)
    assert alerts(w) == [("warn", "open"), ("info", "resolved")] and w.db.meta("reorg:m8") is None
