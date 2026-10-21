"""M8 floor — cSSV.totalSupply() below 2 × the seed holder's cSSV balance (spec §3 M8, D3; plan §7 V2x.*)."""

from __future__ import annotations

import json

import pytest
from fixtures_wallets import CSSV, DAO_SAFE, TOTAL_SUPPLY, WalletChain, stub_cssv, wallet_worker
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
