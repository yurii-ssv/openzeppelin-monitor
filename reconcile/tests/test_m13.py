"""M13 — retired-key balance watch (spec §3 M13, D10; plan §7 V2c.8, V2x.*)."""

from __future__ import annotations

import pytest
from fixtures_wallets import (
    FUNDER,
    HELPER,
    RETIRED_LIQUIDATOR,
    RETIRED_SLOT4,
    WalletChain,
    eth,
    wallet_worker,
)
from harness import advance, outbox

from rpc import RpcError

pytestmark = pytest.mark.m13

ATTACKER = "0xaee058cca5eed5470b4b48c159045b7c25ddb80f"
REORG_TEXT = "Resolved because its evidence left the canonical chain"


def setup(tmp_path, checks=None, **worker):
    chain = WalletChain()
    chain.set_balance(FUNDER, eth(100))
    chain.set_balance(RETIRED_SLOT4, eth(0.01))  # dust present before the worker starts
    chain.mine()
    chain, w = wallet_worker(tmp_path, ["m13"], chain=chain, checks={"m13": checks or {}}, **worker)
    advance(w)  # first observation: the baseline
    return chain, w


def step(w, chain, blocks=1, seconds=0):
    chain.mine(blocks)
    w.clock.advance(seconds)
    advance(w)


def top_up(chain, addr=RETIRED_SLOT4, value=eth(0.5)):
    return chain.send(FUNDER, addr, gas_used=21_000, value=value)


def drain(chain, addr=RETIRED_SLOT4, value=eth(0.5)):
    return chain.send(addr, ATTACKER, gas_used=21_000, value=value)


def sent(w):
    """(severity, kind, key) of every incident message, in order."""
    rows = outbox(w, "kind NOT IN ('health', 'digest')")
    keys = {r["id"]: r["key"] for r in w.db.conn.execute("SELECT id, key FROM incidents")}
    return [(r["severity"], r["kind"], keys[r["incident_id"]]) for r in rows]


def gaps(w):
    return [r for r in outbox(w, "kind='health'") if ": m13-gap" in r["text"]]


def open_keys(w):
    return sorted(r[0] for r in w.db.conn.execute("SELECT key FROM incidents WHERE state='open'"))


# ---------------------------------------------------------------- V2c.8 / V2x.1


@pytest.mark.parametrize("addr", [RETIRED_SLOT4, RETIRED_LIQUIDATOR])
def test_m13_direct_top_up_of_either_retired_key_pages_at_the_checkpoint(tmp_path, addr):
    chain, w = setup(tmp_path)
    tx = top_up(chain, addr)
    b = chain.number
    step(w, chain)
    assert sent(w) == [("page", "open", f"{addr}:{b}")]
    text = outbox(w)[0]["text"]
    assert "+0.500000 ETH" in text and tx in text and "direct transfer to the address" in text


def test_m13_internal_transfer_from_a_helper_contract_pages(tmp_path):
    chain, w = setup(tmp_path)
    tx = chain.internal_transfer(RETIRED_SLOT4, eth(0.2))
    b = chain.number
    step(w, chain)
    assert sent(w) == [("page", "open", f"{RETIRED_SLOT4}:{b}")]
    text = outbox(w)[0]["text"]
    assert tx in text and f"log of {HELPER} names it" in text and "direct transfer" not in text


def test_m13_internal_transfer_without_any_log_still_pages(tmp_path):
    chain, w = setup(tmp_path)
    chain.credit(RETIRED_SLOT4, eth(0.3))  # e.g. SELFDESTRUCT / a contract call with no event
    step(w, chain)
    (row,) = outbox(w)
    assert row["severity"] == "page" and "none found" in row["text"] and "internal transfer" in row["text"]


def test_m13_zero_delta_blocks_stay_silent(tmp_path):
    chain, w = setup(tmp_path)
    for _ in range(5):
        chain.set_balance(RETIRED_SLOT4, eth(0.01))  # rewritten to the same value
        step(w, chain)
    chain.send(FUNDER, HELPER, value=eth(1))  # unrelated activity
    step(w, chain)
    assert sent(w) == [] and outbox(w) == []


def test_m13_outgoing_value_stays_silent(tmp_path):
    chain, w = setup(tmp_path)
    drain(chain, value=eth(0.005))
    step(w, chain)
    assert outbox(w) == []


def test_m13_first_observation_is_a_silent_baseline(tmp_path):
    chain, w = setup(tmp_path)
    assert outbox(w) == []
    rows = dict(w.db.conn.execute("SELECT address, balance FROM m13_watch").fetchall())
    assert rows == {RETIRED_SLOT4: str(eth(0.01)), RETIRED_LIQUIDATOR: "0"}


def test_m13_multi_block_net_zero_between_checkpoints_is_caught(tmp_path):
    """Top-up in block b, swept in b+1, the checkpoint jumps two blocks: every block is read, so it pages, then
    says the value LEFT (not an all-clear)."""
    chain, w = setup(tmp_path)
    top_up(chain)
    b = chain.number
    chain.mine()
    drain(chain)
    step(w, chain)
    assert sent(w) == [("page", "open", f"{RETIRED_SLOT4}:{b}"), ("info", "resolved", f"{RETIRED_SLOT4}:{b}")]
    res = outbox(w, "kind='resolved'")[0]["text"]
    assert "Value LEFT" in res and "NOT an all-clear" in res and f"at block {b + 1}" in res


def test_m13_d10_accepted_gap_within_block_net_zero_is_not_detected(tmp_path):
    """ACCEPTED GAP (D10): value in and out within one block nets to zero at every block boundary."""
    chain, w = setup(tmp_path)
    top_up(chain)
    drain(chain)
    step(w, chain)
    assert outbox(w) == []


def test_m13_open_while_the_value_sits_then_re_paged_every_6h(tmp_path):
    chain, w = setup(tmp_path)
    top_up(chain)
    step(w, chain)
    step(w, chain, seconds=3_600)
    step(w, chain, blocks=3, seconds=21_600)
    assert [k for _, k, _ in sent(w)] == ["open", "realert"]
    drain(chain, value=eth(0.505))  # back to (below) the pre-top-up balance
    step(w, chain)
    assert [k for _, k, _ in sent(w)] == ["open", "realert", "resolved"] and open_keys(w) == []


def test_m13_second_top_up_is_its_own_incident(tmp_path):
    chain, w = setup(tmp_path)
    top_up(chain)
    b1 = chain.number
    step(w, chain)
    top_up(chain, value=eth(0.1))
    b2 = chain.number
    step(w, chain)
    assert open_keys(w) == sorted([f"{RETIRED_SLOT4}:{b1}", f"{RETIRED_SLOT4}:{b2}"])
    drain(chain, value=eth(0.05))  # 0.56 - fee: above both pre-top-up balances (0.01 and 0.51)
    step(w, chain)
    assert len(open_keys(w)) == 2
    drain(chain, value=eth(0.2))  # 0.36 - fees: below 0.51 (the second top-up's before), above 0.01
    step(w, chain)
    assert open_keys(w) == [f"{RETIRED_SLOT4}:{b1}"]


# ---------------------------------------------------------------- RPC: an error or an empty result raises


def test_m13_rpc_error_raises(tmp_path):
    chain, w = setup(tmp_path)
    top_up(chain)
    chain.fail_methods = {"eth_getBalance"}
    with pytest.raises(RpcError):
        step(w, chain)
    assert outbox(w) == []
    chain.fail_methods = set()
    step(w, chain)
    assert [k for _, k, _ in sent(w)] == ["open"]  # nothing was lost: the cursor did not advance


def test_m13_empty_balance_result_raises_never_no_change(tmp_path):
    chain, w = setup(tmp_path)
    chain.empty_methods = {"eth_getBalance"}
    with pytest.raises(RpcError, match="eth_getBalance"):
        step(w, chain)
    before = w.db.conn.execute("SELECT block FROM m13_watch").fetchall()
    assert all(r[0] < chain.sealed for r in before)


@pytest.mark.parametrize("mode", ["fail", "empty"])
def test_m13_attribution_failure_still_pages(tmp_path, mode):
    """A failed or empty receipts lookup is reported as such ("lookup failed"), never as "none found"."""
    chain, w = setup(tmp_path)
    tx = top_up(chain)
    setattr(chain, f"{mode}_methods", {"eth_getBlockReceipts"})
    step(w, chain)
    (row,) = outbox(w)
    assert row["severity"] == "page" and tx in row["text"] and "lookup failed" in row["text"]


# ---------------------------------------------------------------- cap: bounded reads, explicit beyond-cap behaviour


def test_m13_cap_samples_a_long_gap_pages_and_says_netting_was_not_excluded(tmp_path):
    chain, w = setup(tmp_path, checks={"max_blocks_per_run": 10})
    chain.mine(40)
    top_up(chain)
    chain.mine(59)
    step(w, chain)
    run = w.db.meta("run:m13")
    assert run["rpc_calls"]["eth_getBalance"] <= 2 * 10
    (row,) = outbox(w, "severity='page'")
    assert "was read at 10 sampled blocks (cap)" in row["text"] and "could not be excluded" in row["text"]
    assert len(gaps(w)) == 2  # one per address, even the one with no increase
    assert w.db.conn.execute("SELECT block FROM m13_watch").fetchall()[0][0] == chain.sealed


def test_m13_cap_gap_without_increase_is_a_health_notice_only(tmp_path):
    chain, w = setup(tmp_path, checks={"max_blocks_per_run": 10})
    step(w, chain, blocks=50)
    assert sent(w) == [] and len(gaps(w)) == 2
    assert "transfers netting to zero between the read blocks could not be excluded" in gaps(w)[0]["text"]


def test_m13_within_the_cap_every_block_is_read(tmp_path):
    chain, w = setup(tmp_path, checks={"max_blocks_per_run": 10})
    step(w, chain, blocks=10)
    assert w.db.meta("run:m13")["rpc_calls"]["eth_getBalance"] == 2 * 10 and gaps(w) == []


def test_m13_long_restart_gap_is_sampled_and_still_pages(tmp_path):
    chain, w = setup(tmp_path)
    chain.mine(500)
    top_up(chain)
    chain.mine(2_000)
    _, w2 = wallet_worker(tmp_path, ["m13"], chain=chain, start_block=w.cfg.start_block,
                          activation_block=w.cfg.activation_block, clock=w.clock)
    advance(w2)
    assert w2.db.meta("run:m13")["rpc_calls"]["eth_getBalance"] <= 2 * 256
    assert [s for s, _, _ in sent(w2)] == ["page"] and len(gaps(w2)) == 2


def test_m13_every_n_blocks(tmp_path):
    chain, w = setup(tmp_path, checks={"every_n_blocks": 5})
    step(w, chain, blocks=12)
    assert w.db.meta("run:m13")["rpc_calls"]["eth_getBalance"] == 2 * 3  # +5, +10, +12 (the checkpoint)
    top_up(chain)
    step(w, chain)
    assert [k for _, k, _ in sent(w)] == ["open"]


def test_m13_sample_helper_is_bounded_and_ends_at_the_checkpoint():
    from checks.m13_retired_keys import sample

    assert sample(10, 13, 1, 5) == ([11, 12, 13], False)
    blocks, sampled = sample(0, 10_000, 1, 256)
    assert sampled and len(blocks) <= 256 and blocks[-1] == 10_000 and blocks == sorted(set(blocks))
    assert sample(0, 12, 5, 10) == ([5, 10, 12], False)


def test_m13_thresholds_from_chain_and_config_addresses(tmp_path):
    """No constant in the check: the reference is the chain's balance at the previous read block (a top-up smaller
    than the pre-existing dust still pages), and the watched set comes from config (a third address, no code
    change)."""
    extra = "0x" + "77" * 20
    chain = WalletChain()
    chain.set_balance(FUNDER, eth(100))
    chain.set_balance(RETIRED_SLOT4, eth(0.01))
    chain.mine()
    addrs = {RETIRED_SLOT4: "old slot 4", extra: "another retired key"}
    chain, w = wallet_worker(tmp_path, ["m13"], chain=chain, checks={"m13": {"addresses": addrs}})
    advance(w)
    top_up(chain, RETIRED_SLOT4, value=10**9)  # 1 gwei
    top_up(chain, extra, value=1)  # 1 wei
    step(w, chain)
    assert sorted(k for _, _, k in sent(w)) == sorted([f"{RETIRED_SLOT4}:{chain.sealed}", f"{extra}:{chain.sealed}"])


def test_m13_required_addresses_raise_at_construction(tmp_path):
    with pytest.raises(SystemExit, match="checks.m13.addresses is required"):
        wallet_worker(tmp_path, ["m13"], checks={"m13": {"addresses": {}}})


# ---------------------------------------------------------------- V2x.3 restart


def test_m13_restart_does_not_duplicate_the_page(tmp_path):
    chain, w = setup(tmp_path)
    top_up(chain)
    step(w, chain)
    chain.mine(3)
    _, w2 = wallet_worker(tmp_path, ["m13"], chain=chain, start_block=w.cfg.start_block,
                          activation_block=w.cfg.activation_block, clock=w.clock)
    advance(w2)
    step(w2, chain)
    assert [k for _, k, _ in sent(w2)] == ["open"]
    step(w2, chain, seconds=21_601)
    assert [k for _, k, _ in sent(w2)] == ["open", "realert"]


def test_m13_restart_with_a_prior_non_zero_balance_is_silent(tmp_path):
    """The baseline is durable: dust present before the first start does not page after a restart."""
    chain, w = setup(tmp_path)
    chain.mine(5)
    _, w2 = wallet_worker(tmp_path, ["m13"], chain=chain, start_block=w.cfg.start_block,
                          activation_block=w.cfg.activation_block, clock=w.clock)
    advance(w2)
    step(w2, chain)
    assert outbox(w2) == []
    assert w2.db.conn.execute("SELECT balance FROM m13_watch WHERE address=?", (RETIRED_SLOT4,)).fetchone()[0] == \
        str(eth(0.01))


# ---------------------------------------------------------------- reorgs (README §6.1, §6.2)


def _top_up_then(tmp_path, **worker):
    chain, w = setup(tmp_path, **worker)
    step(w, chain, blocks=3)  # a recorded checkpoint below the fork
    fork = chain.number
    top_up(chain)
    return chain, w, fork


def test_m13_reorg_depth_1_removing_the_top_up_resolves_and_never_re_pages(tmp_path):
    chain, w, fork = _top_up_then(tmp_path)
    step(w, chain)
    assert sent(w) == [("page", "open", f"{RETIRED_SLOT4}:{fork}")]
    chain.reorg(fork)
    step(w, chain)
    assert sent(w)[-1] == ("info", "resolved", f"{RETIRED_SLOT4}:{fork}") and open_keys(w) == []
    assert REORG_TEXT in outbox(w, "kind='resolved'")[0]["text"]
    assert w.db.conn.execute("SELECT COUNT(*) FROM m13_topup").fetchone()[0] == 0
    step(w, chain, blocks=5, seconds=86_400)
    assert [k for _, k, _ in sent(w)] == ["open", "resolved"]


def test_m13_reorg_depth_n_beyond_confirmations_removing_the_top_up_resolves(tmp_path):
    chain, w, fork = _top_up_then(tmp_path, confirmations=2)
    step(w, chain, blocks=6)
    assert [k for _, k, _ in sent(w)] == ["open"]
    chain.reorg(fork)  # the checkpoint was fork + 3: 4 blocks deep, beyond the 2-block window
    step(w, chain, blocks=8)
    assert [k for _, k, _ in sent(w)] == ["open", "resolved"] and open_keys(w) == []
    assert REORG_TEXT in outbox(w, "kind='resolved'")[0]["text"]
    step(w, chain, blocks=3, seconds=86_400)
    assert len(sent(w)) == 2


def test_m13_reorg_marker_survives_a_crash_before_the_run(tmp_path):
    chain, w, fork = _top_up_then(tmp_path)
    step(w, chain)
    chain.reorg(fork)
    chain.mine()
    w.check_reorg()  # rewound and committed; the process dies before the check runs
    assert w.db.meta("reorg:m13") is not None
    _, w2 = wallet_worker(tmp_path, ["m13"], chain=chain, start_block=w.cfg.start_block,
                          activation_block=w.cfg.activation_block, clock=w.clock)
    advance(w2)
    assert [k for _, k, _ in sent(w2)] == ["open", "resolved"] and w2.db.meta("reorg:m13") is None
    assert REORG_TEXT in outbox(w2, "kind='resolved'")[0]["text"]


def test_m13_reorg_that_keeps_the_top_up_keeps_the_incident_without_a_second_page(tmp_path):
    chain, w, fork = _top_up_then(tmp_path)
    step(w, chain, blocks=2)
    chain.reorg(fork)
    top_up(chain)  # re-included in the same block of the new chain (new hash)
    step(w, chain)
    assert sent(w) == [("page", "open", f"{RETIRED_SLOT4}:{fork}")] and open_keys(w) == [f"{RETIRED_SLOT4}:{fork}"]


def test_m13_reorg_moving_the_top_up_to_another_block(tmp_path):
    """Re-included one block later: the deleted block's incident resolves (evidence left the chain), the new
    block's top-up opens its own incident."""
    chain, w, fork = _top_up_then(tmp_path)
    step(w, chain)
    chain.reorg(fork)
    chain.mine()
    top_up(chain)
    step(w, chain)
    assert sent(w) == [("page", "open", f"{RETIRED_SLOT4}:{fork}"), ("page", "open", f"{RETIRED_SLOT4}:{fork + 1}"),
                       ("info", "resolved", f"{RETIRED_SLOT4}:{fork}")]  # the §6.2 rule runs after the findings


def test_m13_reorg_removing_the_drain_reopens_the_value_at_risk(tmp_path):
    chain, w, fork = _top_up_then(tmp_path)
    step(w, chain, blocks=2)
    drain_block = chain.number
    drain(chain)
    step(w, chain)
    assert [k for _, k, _ in sent(w)] == ["open", "resolved"]
    chain.reorg(drain_block)
    step(w, chain)
    assert [k for _, k, _ in sent(w)] == ["open", "resolved", "open"]
    assert open_keys(w) == [f"{RETIRED_SLOT4}:{fork}"]


def test_m13_reorg_below_an_older_top_up_keeps_it(tmp_path):
    """The top-up's block survives; the incident (last seen above the ancestor) is re-reported, not resolved."""
    chain, w, fork = _top_up_then(tmp_path)
    step(w, chain, blocks=3)
    later = chain.number
    step(w, chain, blocks=3)
    chain.reorg(later)
    step(w, chain)
    assert [k for _, k, _ in sent(w)] == ["open"] and open_keys(w) == [f"{RETIRED_SLOT4}:{fork}"]
