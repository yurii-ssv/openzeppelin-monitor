"""M13 — retired-key balance watch (spec §3 M13, D10; plan §7 V2c.8, V2x.*)."""

from __future__ import annotations

import json
import random

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
PENDING_TEXT = "the new chain has not reached it yet"


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


# ---------------------------------------------------------------- repair 1: generalised audit (README §6.2)


def test_m13_reorg_removing_a_top_up_and_its_drain_stays_silent(tmp_path):
    """(c) the evidence of an ALREADY RESOLVED key leaves the chain (top-up and drain both above the ancestor): no
    page, no second resolution, no row, nothing open."""
    chain, w, fork = _top_up_then(tmp_path)
    step(w, chain)
    drain(chain)
    step(w, chain)
    assert [k for _, k, _ in sent(w)] == ["open", "resolved"] and open_keys(w) == []
    chain.reorg(fork)
    step(w, chain, blocks=3)
    assert [k for _, k, _ in sent(w)] == ["open", "resolved"] and open_keys(w) == []
    assert w.db.conn.execute("SELECT COUNT(*) FROM m13_topup").fetchone()[0] == 0 and w.db.meta("reorg:m13") is None


def test_m13_reorg_run_that_cannot_re_read_raises_keeps_the_marker_and_survives_a_restart(tmp_path):
    """(b)+(d) the re-read at the ancestor fails: the run rolls back (rows, incident and marker kept, nothing
    re-paged), also in a restarted process; once the RPC answers, the orphan top-up resolves by the §6.2 rule."""
    chain, w, fork = _top_up_then(tmp_path)
    step(w, chain)
    chain.reorg(fork)
    chain.fail_methods.add("eth_getBalance")
    with pytest.raises(RpcError):
        step(w, chain)
    assert w.db.meta("reorg:m13") is not None and open_keys(w) == [f"{RETIRED_SLOT4}:{fork}"]
    assert w.db.conn.execute("SELECT COUNT(*) FROM m13_topup").fetchone()[0] == 1
    _, w2 = wallet_worker(tmp_path, ["m13"], chain=chain, start_block=w.cfg.start_block,
                          activation_block=w.cfg.activation_block, clock=w.clock)
    with pytest.raises(RpcError):
        step(w2, chain, seconds=6 * 3600)
    assert w2.db.meta("reorg:m13") is not None and [k for _, k, _ in sent(w2)] == ["open"]
    chain.fail_methods.clear()
    step(w2, chain)
    assert [k for _, k, _ in sent(w2)] == ["open", "resolved"] and open_keys(w2) == []
    assert REORG_TEXT in outbox(w2, "kind='resolved'")[0]["text"] and w2.db.meta("reorg:m13") is None


def _open_then_m13_fails(tmp_path, steps=3):
    chain, w = setup(tmp_path)
    top_up(chain)
    b = chain.number
    step(w, chain)
    (seen,) = [r[0] for r in w.db.conn.execute("SELECT last_seen_block FROM incidents WHERE state='open'")]
    chain.fail_methods.add("eth_getBalance")
    for _ in range(steps):
        with pytest.raises(RpcError):
            step(w, chain)
    chain.fail_methods.clear()
    return chain, w, b, seen


def test_m13_reorg_re_evaluates_an_open_top_up_last_seen_below_the_ancestor(tmp_path):
    """An open top-up NOT in ctx.reorg_keys (m13 failed for a few steps after it paged) is still evaluated by the
    reorg run on the new chain: a drain there resolves it with the value-left text; nothing is re-paged."""
    chain, w, b, seen = _open_then_m13_fails(tmp_path)
    fork = chain.number - 1
    assert fork - 1 > seen
    chain.reorg(fork)
    drain(chain)
    step(w, chain)
    assert [k for _, k, _ in sent(w)] == ["open", "resolved"] and open_keys(w) == []
    res = outbox(w, "kind='resolved'")[0]["text"]
    assert "Value LEFT" in res and "NOT an all-clear" in res and REORG_TEXT not in res


def test_m13_reorg_keeps_an_open_top_up_last_seen_below_the_ancestor_without_a_second_page(tmp_path):
    chain, w, b, seen = _open_then_m13_fails(tmp_path)
    chain.reorg(chain.number - 1)
    step(w, chain)
    assert [k for _, k, _ in sent(w)] == ["open"] and open_keys(w) == [f"{RETIRED_SLOT4}:{b}"]
    assert w.db.meta("reorg:m13") is None


def test_m13_short_batch_response_raises_never_a_partial_read(tmp_path):
    """(g) partial responses: fewer eth_getBalance results than reads is an RPC error (never a skipped block)."""
    chain, w = setup(tmp_path)
    top_up(chain)
    batch = w.rpc.batch
    w.rpc.batch = lambda calls: batch(calls)[:-1] if calls and calls[0][0] == "eth_getBalance" else batch(calls)
    with pytest.raises(RpcError, match="results for"):
        step(w, chain, blocks=3)
    assert sent(w) == [] and w.db.conn.execute("SELECT COUNT(*) FROM m13_topup").fetchone()[0] == 0
    w.rpc.batch = batch
    step(w, chain)
    assert [k for _, k, _ in sent(w)] == ["open"]


def test_m13_two_top_ups_in_one_block_are_one_page_with_the_sum(tmp_path):
    """(e) same-block ordering (QA independent case): one key, the summed delta; then the value-left wording."""
    chain, w = setup(tmp_path)
    top_up(chain, value=eth(0.1))
    top_up(chain, value=eth(0.2))
    step(w, chain)
    (row,) = outbox(w, "kind='open'")
    assert "+0.300000 ETH" in row["text"]
    drain(chain, value=eth(0.3))
    step(w, chain)
    assert "NOT an all-clear" in outbox(w, "kind='resolved'")[0]["text"]


def test_m13_reorg_below_the_baseline_reads_the_new_chain_from_the_ancestor(tmp_path):
    """(c) m13 enabled on a running worker's database, then a reorg deletes its baseline block: the address is
    re-read at the ancestor and (A, checkpoint] of the new chain is read, so a top-up there pages (conservative:
    value reached the retired key after the ancestor; the baseline block itself is not stored)."""
    chain = WalletChain()
    chain.set_balance(FUNDER, eth(100))
    chain.mine()
    chain, w0 = wallet_worker(tmp_path, [], chain=chain)  # the worker runs without m13 first
    for _ in range(3):
        chain.mine()
        advance(w0)
    fork = chain.number
    chain.mine(2)
    _, w = wallet_worker(tmp_path, ["m13"], chain=chain, start_block=w0.cfg.start_block,
                         activation_block=w0.cfg.activation_block, clock=w0.clock)
    advance(w)
    assert {r[0] for r in w.db.conn.execute("SELECT block FROM m13_watch")} == {chain.sealed} and sent(w) == []
    chain.reorg(fork)
    top_up(chain)
    step(w, chain, blocks=2)
    assert sent(w) == [("page", "open", f"{RETIRED_SLOT4}:{fork}")] and w.db.meta("reorg:m13") is None


# ---------------------------------------------------------------- repair 1: deterministic adversarial sweep
# Seeded cases (40): random direct/internal top-ups, partial and full drains, several transfers in one block, RPC
# outages, restarts and 0..7 h clock jumps, then one reorg 1..24 blocks below the checkpoint (beyond the
# confirmation window), sometimes with a crash between its detection and the check's run and an RPC outage in the
# reorg run, a replacement chain that re-includes some of the deleted transfers (same or another block), adds new
# ones and is shorter or longer. Oracle: the final canonical chain read block by block from the baseline.
# Invariants: no open incident without supporting canonical evidence and no dropped page for surviving evidence
# (open keys = the oracle's unresolved top-ups; every canonical top-up paged at least once); no resolve without
# positive proof (after the reorg: a value-left resolution only for a canonical drain, an evidence-left one only
# for a key the canonical chain does not have); no page for orphaned evidence after the reorg (re-alerts
# included); no window anchored on stale data (m13_watch at the checkpoint with its canonical balance); no event
# lost between sweeps (m13_topup rows = the oracle's top-ups, with their balances); nothing pending, healthy.
# Repair 2 (QA attempt 2 F1): the replacement chain then regrows past the deleted blocks (restarts drawn while keys
# are pending); at most one page per canonical top-up unless its value-left resolution came from a deleted block
# and the value sat on the address past it; orphaned evidence paged at most once (before the reorg); a 6 h
# reminder of a still-pending key must say so; no orphan left unsettled.


class M13Sweep:
    ADDRS = (RETIRED_SLOT4, RETIRED_LIQUIDATOR)

    def __init__(self, tmp_path, seed, confirmations):
        self.rng = random.Random(f"cp2c-m13:{seed}:{confirmations}")
        self.tmp, self.c = tmp_path, confirmations
        chain = self.chain = WalletChain()
        chain.set_balance(FUNDER, eth(1000))
        chain.set_balance(RETIRED_SLOT4, eth(0.01))
        chain.mine(1 + confirmations)
        self.start = chain.sealed - confirmations  # the first checkpoint: the baseline run
        self.clock = None
        self.w = self.worker()
        self.w.step()
        (self.baseline,) = {r[0] for r in self.w.db.conn.execute("SELECT block FROM m13_watch")}
        self.orphans: list[tuple] = []  # transfers of the deleted blocks: (kind, address, value) for re-inclusion

    def worker(self):
        _, w = wallet_worker(self.tmp, ["m13"], chain=self.chain, start_block=self.start,
                             activation_block=self.start, confirmations=self.c,
                             **({"clock": self.clock} if self.clock else {}))
        self.clock = w.clock
        return w

    def transfers(self, n, record=False):
        rng, chain = self.rng, self.chain
        for _ in range(n):
            a = rng.choice(self.ADDRS)
            r = rng.random()
            if r < 0.3:
                v = eth(rng.choice((0.05, 0.1, 0.25)))
                top_up(chain, a, v) if rng.random() < 0.5 else chain.internal_transfer(a, v)
                kind = ("in", a, v)
            elif r < 0.55 and chain.balance_now(a) > eth(0.01):
                full = rng.random() < 0.5
                v = chain.balance_now(a) - eth(0.001) if full else chain.balance_now(a) // 3
                drain(chain, a, v)
                kind = ("out", a, v)
            else:
                continue
            if record:
                self.orphans.append(kind)

    def wstep(self, fail=False):
        if fail:
            self.chain.fail_methods.add("eth_getBalance")
        self.w.step()
        self.chain.fail_methods.discard("eth_getBalance")

    def run(self):
        rng, chain = self.rng, self.chain
        for _ in range(rng.randint(6, 14)):  # history before the fork
            self.transfers(rng.randint(0, 2))
            chain.mine(rng.randint(1, 3))
            self.clock.advance(rng.choice((12, 60, 3600, 6 * 3600 + 1)))
            if rng.random() < 0.15:
                self.w = self.worker()  # restart
            self.wstep(fail=rng.random() < 0.15)
        self.wstep()
        # the blocks the reorg deletes: 1..24 of them from `fork`, with recorded transfers, seen (paged) by the
        # worker at its checkpoint
        fork = chain.number
        for _ in range(rng.randint(1, 24)):
            self.transfers(rng.randint(0, 2), record=True)
            chain.mine()
        chain.mine(self.c)
        self.wstep()
        self.fork, self.old_cp = fork, self.w.checkpoint()["block"]
        self.depth = self.old_cp - fork + 1
        pre = self.w.db.conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM outbox").fetchone()[0]
        # the replacement chain: shorter or longer, some deleted transfers re-included, new ones
        chain.reorg(fork)
        for kind, a, v in self.orphans:
            if rng.random() < 0.5:
                if rng.random() < 0.5:
                    chain.mine()
                if kind == "in":
                    top_up(chain, a, v)
                elif chain.balance_now(a) > v:
                    drain(chain, a, v)
        self.transfers(rng.randint(0, 2))
        chain.mine(rng.randint(1, 4))
        if rng.random() < 0.3:  # crash between the reorg's detection and the check's run
            self.w.check_reorg()
            self.w = self.worker()
        if rng.random() < 0.3:  # the reorg run cannot read: rolled back, retried
            self.wstep(fail=True)
            assert self.w.db.meta("reorg:m13") is not None
        for _ in range(rng.randint(1, 4)):
            self.transfers(rng.randint(0, 1))
            chain.mine()
            self.clock.advance(rng.choice((12, 6 * 3600 + 1)))
            self.wstep()
        while self.w.checkpoint()["block"] < self.old_cp:  # repair 2: the new chain regrows past every deleted block
            chain.mine()
            self.clock.advance(12)
            if rng.random() < 0.2:
                self.w = self.worker()  # restart while orphaned keys are pending
            self.wstep()
        self.clock.advance(6 * 3600 + 1)  # every open incident re-alerts: none may be an orphan
        chain.mine()
        self.wstep()
        return pre

    def oracle(self, cp):
        """Top-ups (address, block) -> (before, after, resolved_block) read from the canonical chain."""
        out = {}
        for a in self.ADDRS:
            prev = self.chain._balance(a, self.baseline)
            for b in range(self.baseline + 1, cp + 1):
                bal = self.chain._balance(a, b)
                for (ta, tb), (before, after, res) in out.items():
                    if ta == a and res is None and bal <= before:
                        out[(ta, tb)] = (before, after, b)
                if bal > prev:
                    out[(a, b)] = (prev, bal, None)
                prev = bal
        return out


@pytest.mark.parametrize("confirmations", [0, 2])
@pytest.mark.parametrize("seed", range(100))  # repair 2: 20 -> 100 (QA attempt 2 found F1(b) on seeds 20..203)
def test_m13_adversarial_reorg_sweep(tmp_path, seed, confirmations):
    s = M13Sweep(tmp_path, seed, confirmations)
    pre = s.run()
    assert s.depth >= 1
    w, cp = s.w, s.w.checkpoint()["block"]
    truth = s.oracle(cp)
    keys = {f"{a}:{b}": v for (a, b), v in truth.items()}
    assert open_keys(w) == sorted(k for k, (_, _, res) in keys.items() if res is None)
    paged = {k for sev, kind, k in sent(w) if kind == "open"}
    assert set(keys) <= paged, "a canonical top-up was never paged"
    inc_key = {r[0]: r[1] for r in w.db.conn.execute("SELECT id, key FROM incidents")}
    for r in outbox(w, f"rowid > {pre} AND kind NOT IN ('health', 'digest')"):
        k = inc_key[r["incident_id"]]
        if r["kind"] == "resolved" and REORG_TEXT in r["text"]:
            assert k not in keys, f"evidence-left resolution of a canonical top-up {k}"
        elif r["kind"] == "resolved":
            assert k in keys and keys[k][2] is not None, f"value-left resolution without a canonical drain: {k}"
        elif r["kind"] == "realert" and PENDING_TEXT in r["text"]:
            pass  # the 6 h reminder of a key whose replaced block the new chain had not reached yet (says so)
        else:
            assert k in keys, f"{r['kind']} for orphaned evidence {k}"
    # repair 2 (QA attempt 2 F1): at most one page per canonical top-up, unless the drain it was resolved by left the
    # canonical chain and the value then sat on the address past that block; orphaned evidence paged at most once
    incs: dict[str, list] = {}
    for k, state, ev in w.db.conn.execute("SELECT key, state, evidence FROM incidents ORDER BY rowid"):
        incs.setdefault(k, []).append((state, json.loads(ev)))
    for k, chain_of in incs.items():
        if k not in keys:
            assert len(chain_of) == 1, f"orphaned evidence {k} paged {len(chain_of)} times"
            continue
        assert len(chain_of) <= 2, f"canonical top-up {k} paged {len(chain_of)} times"
        if len(chain_of) == 2:
            (state, ev), res = chain_of[0], keys[k][2]
            old = ev.get("resolved_block")
            assert state == "resolved" and old is not None and old >= s.fork and (res is None or res > old), \
                f"second page for identical evidence {k}: first {state} {old}, canonical drain {res}"
    assert w.db.conn.execute("SELECT COUNT(*) FROM m13_orphan").fetchone()[0] == 0
    rows = {f"{r[0]}:{r[1]}": (int(r[2]), int(r[3]), r[4])
            for r in w.db.conn.execute("SELECT address, block, before, after, resolved_block FROM m13_topup")}
    assert rows == keys
    for a, b, bal in w.db.conn.execute("SELECT address, block, balance FROM m13_watch"):
        assert b == cp and int(bal) == s.chain._balance(a, cp)
    assert w.degraded() == [] and w.db.meta("reorg:m13") is None


# ---------------------------------------------------------------- repair 2: QA attempt 2 scenarios (Appendix C1, ported verbatim
# except the module prefix; expected outcomes from the spec, D3/D4/D10 and README §6.2)

def test_qa2_m13_reorg_reincluding_a_resolved_top_up_and_its_drain_does_not_page_again(tmp_path):
    """Class 2/10: top-up at T and drain at R, both above the ancestor A, both paged/resolved. A deep reorg
    re-includes the SAME two transfers at the SAME blocks (the usual outcome of a reorg). The canonical evidence is
    identical to what was already paged and resolved: no second page, no second resolution."""
    chain, w = setup(tmp_path, confirmations=2)
    step(w, chain, blocks=3)
    fork = chain.number
    top_up(chain)
    chain.mine()
    drain(chain)
    step(w, chain, blocks=4)
    assert [k for _, k, _ in sent(w)] == ["open", "resolved"]
    chain.reorg(fork)
    top_up(chain)  # the same top-up, re-included at the same block (new tx hash)
    chain.mine()
    drain(chain)  # the same drain, at the same block
    step(w, chain, blocks=4)
    assert w.db.meta("reorg:m13") is None
    assert [k for _, k, _ in sent(w)] == ["open", "resolved"], sent(w)


def test_qa2_m13_reorg_reincluding_only_the_drain_of_an_older_top_up_does_not_resolve_twice(tmp_path):
    """Class 2: top-up at T <= A survives; drain at R > A re-included at R. One resolution in total."""
    chain, w = setup(tmp_path, confirmations=2)
    step(w, chain, blocks=3)  # baseline taken (checkpoint reached start_block)
    top_up(chain)
    step(w, chain, blocks=4)
    step(w, chain, blocks=2)
    fork = chain.number
    drain(chain)
    step(w, chain, blocks=4)
    assert [k for _, k, _ in sent(w)] == ["open", "resolved"]
    chain.reorg(fork)
    drain(chain)
    step(w, chain, blocks=4)
    assert [k for _, k, _ in sent(w)] == ["open", "resolved"], sent(w)
    assert open_keys(w) == []


@pytest.mark.parametrize("out_value,expected", [(eth(0.2), "+0.29997"), (eth(0.6), None)])
def test_qa2_m13_same_block_top_up_and_spend(tmp_path, out_value, expected):
    """Class 3: a top-up and a spend by the leaked key in ONE block. Net +0.3 pages with the net delta; net -0.1
    is silent — D10 accepted gap (within-block transfers are only seen by their net)."""
    chain, w = setup(tmp_path)
    chain.set_balance(RETIRED_SLOT4, eth(1))  # value present before (baseline is 0.01: make a new baseline)
    step(w, chain)
    base = len(sent(w))
    top_up(chain, value=eth(0.5))
    drain(chain, value=out_value)
    step(w, chain)
    msgs = outbox(w, "kind='open'")[base:]
    if expected is None:
        assert msgs == []
    else:
        assert len(msgs) == 1 and expected in msgs[0]["text"]


def test_qa2_m13_long_restart_gap_bounded_reads_and_page(tmp_path):
    """Class 6: a 10,000-block restart gap with a top-up in the middle (kept): one page with the sampled-gap wording,
    an m13-gap health message, eth_getBalance per address bounded by the cap."""
    chain, w = setup(tmp_path)
    chain.mine(4_000)
    top_up(chain, RETIRED_LIQUIDATOR, eth(0.3))
    chain.mine(6_000)
    _, w2 = wallet_worker(tmp_path, ["m13"], chain=chain, start_block=w.cfg.start_block,
                          activation_block=w.cfg.activation_block, clock=w.clock)
    advance(w2)
    pages = outbox(w2, "kind='open'")
    assert len(pages) == 1 and "sampled" in pages[0]["text"] and "+0.300000 ETH" in pages[0]["text"]
    assert any(": m13-gap" in r["text"] for r in outbox(w2, "kind='health'"))
    run = w2.db.meta("run:m13")
    assert run["rpc_calls"].get("eth_getBalance", 0) <= 2 * 256, run


def test_qa2_m13_long_gap_top_up_then_drain_inside_the_gap_is_the_sampled_d10_gap(tmp_path):
    """Class 6 beyond the cap: a top-up and its drain between two sampled blocks is not seen (documented: the
    page/health says netting could not be excluded; accepted gap D10)."""
    chain, w = setup(tmp_path)
    chain.mine(4_000)
    top_up(chain, RETIRED_SLOT4, eth(0.3))
    chain.mine(2)
    drain(chain, RETIRED_SLOT4, eth(0.3))
    chain.mine(6_000)
    _, w2 = wallet_worker(tmp_path, ["m13"], chain=chain, start_block=w.cfg.start_block,
                          activation_block=w.cfg.activation_block, clock=w.clock)
    advance(w2)
    assert outbox(w2, "kind='open'") == []
    gap = [r for r in outbox(w2, "kind='health'") if ": m13-gap" in r["text"]]
    assert gap and "could not be excluded" in gap[0]["text"]


def test_qa2_m13_malformed_receipt_entry_must_not_block_the_page(tmp_path):
    """Best-effort attribution must never block the page: an eth_getBlockReceipts list with a null entry."""
    chain, w = setup(tmp_path)
    orig = chain._handle

    def handle(m, p):
        r = orig(m, p)
        if m == "eth_getBlockReceipts" and isinstance(r, list):
            return [None, *r]
        return r

    chain._handle = handle
    top_up(chain)
    step(w, chain)
    assert [k for _, k, _ in sent(w)] == ["open"], [r["text"][:200] for r in outbox(w)]


def test_qa2_m13_internal_transfer_from_helper_on_both_keys_in_one_block(tmp_path):
    chain, w = setup(tmp_path)
    chain.internal_transfer(RETIRED_SLOT4, eth(0.1))
    chain.internal_transfer(RETIRED_LIQUIDATOR, eth(0.2))
    b = chain.number
    step(w, chain)
    assert sorted(k for _, _, k in sent(w)) == sorted([f"{RETIRED_SLOT4}:{b}", f"{RETIRED_LIQUIDATOR}:{b}"])
    assert all(HELPER[2:] in r["text"] or "log of" in r["text"] for r in outbox(w, "kind='open'"))


def test_qa2_m13_empty_reorg_result_on_shorter_chain_never_clean(tmp_path):
    """Class 1 in a reorg run: eth_getBalance null at the ancestor re-read → raise, marker kept, page kept."""
    chain, w, fork = _top_up_then(tmp_path)
    step(w, chain)
    chain.reorg(fork)
    chain.empty_methods.add("eth_getBalance")
    w.step()
    assert w.db.meta("reorg:m13") is not None and open_keys(w) == [f"{RETIRED_SLOT4}:{fork}"]
    chain.empty_methods.discard("eth_getBalance")
    chain.mine()
    advance(w)
    assert w.db.meta("reorg:m13") is None and open_keys(w) == []


# ---------------------------------------------------------------- repair 2: orphaned keys (QA attempt 2 F1)


def _restart(tmp_path, w, chain, checks=None):
    _, w2 = wallet_worker(tmp_path, ["m13"], chain=chain, checks={"m13": checks or {}}, start_block=w.cfg.start_block,
                          activation_block=w.cfg.activation_block, clock=w.clock)
    return w2


def orphans(w):
    return [tuple(r) for r in w.db.conn.execute("SELECT address, block, closed_block FROM m13_orphan ORDER BY block")]


def test_m13_qa2_f1a_reorg_reincluding_a_resolved_top_up_and_its_drain_is_silent(tmp_path):
    """QA attempt 2 F1(a), Appendix A1: top-up at T and drain at T+1, both above the ancestor, paged and resolved;
    the reorg re-includes both at the same blocks (new hashes): no new message (same canonical evidence)."""
    chain, w = setup(tmp_path)
    step(w, chain, blocks=3)
    fork = chain.number
    top_up(chain)
    chain.mine()
    drain(chain)
    step(w, chain)
    key = f"{RETIRED_SLOT4}:{fork}"
    assert sent(w) == [("page", "open", key), ("info", "resolved", key)]
    chain.reorg(fork)
    top_up(chain)
    chain.mine()
    drain(chain)
    step(w, chain)
    assert sent(w) == [("page", "open", key), ("info", "resolved", key)]
    assert w.db.meta("reorg:m13") is None and orphans(w) == [] and open_keys(w) == []


def test_m13_qa2_f1b_reorg_run_below_the_top_up_block_keeps_the_key_open_and_pages_once(tmp_path):
    """QA attempt 2 F1(b), Appendix A2: the shorter new chain puts the reorg run's checkpoint below the top-up's
    block. No proof the top-up left: the key stays open (re-reported, says it is pending), nothing is sent; once the
    checkpoint passes the re-included top-up, it is the same incident (one page in total)."""
    chain, w = setup(tmp_path, confirmations=2)
    step(w, chain, blocks=3)
    fork = chain.number
    top_up(chain)
    step(w, chain, blocks=5)
    key = f"{RETIRED_SLOT4}:{fork}"
    assert sent(w) == [("page", "open", key)]
    chain.reorg(fork)  # 4 blocks deep, beyond the 2-block confirmation window
    top_up(chain)  # the same top-up re-included at the same block
    step(w, chain, blocks=1)  # the reorg run: checkpoint fork - 2
    assert w.checkpoint()["block"] < fork and w.db.meta("reorg:m13") is None
    assert sent(w) == [("page", "open", key)] and open_keys(w) == [key]
    assert orphans(w) == [(RETIRED_SLOT4, fork, None)]
    (ev,) = w.db.conn.execute("SELECT evidence FROM incidents WHERE key=?", (key,)).fetchone()
    assert json.loads(ev)["pending_reorg_recheck"]["block"] == fork
    step(w, chain, blocks=3)
    assert w.checkpoint()["block"] >= fork
    assert sent(w) == [("page", "open", key)] and open_keys(w) == [key] and orphans(w) == []
    assert outbox(w, f"kind='resolved' AND text LIKE '%{REORG_TEXT}%'") == []


def test_m13_shorter_chain_without_the_top_up_resolves_only_once_the_checkpoint_passed_its_block(tmp_path):
    """No re-inclusion: the key is kept while the checkpoint is below its block, then resolved as "evidence left
    the canonical chain" (the check's own text, outside the reorg run) and never re-paged."""
    chain, w = setup(tmp_path, confirmations=2)
    step(w, chain, blocks=3)
    fork = chain.number
    top_up(chain)
    step(w, chain, blocks=5)
    key = f"{RETIRED_SLOT4}:{fork}"
    chain.reorg(fork)
    step(w, chain, blocks=1)
    assert w.checkpoint()["block"] < fork and open_keys(w) == [key] and sent(w) == [("page", "open", key)]
    step(w, chain, blocks=1)
    assert w.checkpoint()["block"] < fork and open_keys(w) == [key]
    step(w, chain, blocks=2)
    assert w.checkpoint()["block"] >= fork
    assert sent(w) == [("page", "open", key), ("info", "resolved", key)] and open_keys(w) == []
    res = outbox(w, "kind='resolved'")[0]["text"]
    assert REORG_TEXT in res and "Value LEFT" not in res
    step(w, chain, blocks=5, seconds=86_400)
    assert len(sent(w)) == 2 and orphans(w) == []


@pytest.mark.parametrize("reincluded", [True, False])
def test_m13_shorter_chain_orphaning_only_the_drain_waits_for_the_drain_block(tmp_path, reincluded):
    """Top-up at or below the ancestor, its drain (resolution sent) above it, the new chain shorter than the drain's
    block: nothing is reported until the checkpoint passes that block; a re-included drain stays silent (one page,
    one resolution); without it the value sits on the canonical chain: paged again, once."""
    chain, w = setup(tmp_path, confirmations=2)
    step(w, chain, blocks=3)  # baseline taken (the checkpoint reached start_block)
    top_up(chain)
    t = chain.number
    step(w, chain, blocks=3)
    step(w, chain, blocks=3)
    fork = chain.number
    drain(chain)
    step(w, chain, blocks=5)
    key = f"{RETIRED_SLOT4}:{t}"
    assert sent(w) == [("page", "open", key), ("info", "resolved", key)]
    chain.reorg(fork)
    if reincluded:
        drain(chain)
    step(w, chain, blocks=1)
    assert w.checkpoint()["block"] < fork and len(sent(w)) == 2 and open_keys(w) == []
    assert orphans(w) == [(RETIRED_SLOT4, t, fork)]
    step(w, chain, blocks=4)
    assert w.checkpoint()["block"] >= fork and orphans(w) == []
    if reincluded:
        assert len(sent(w)) == 2 and open_keys(w) == []
    else:
        assert sent(w)[2:] == [("page", "open", key)] and open_keys(w) == [key]
    step(w, chain, blocks=3)
    assert len(sent(w)) == (2 if reincluded else 3)


def test_m13_reorg_reincluding_a_resolved_top_up_without_its_drain_pages_again_once(tmp_path):
    """Top-up and drain both above the ancestor; the new chain has the top-up (same block) but not the drain: the
    value is at risk on the canonical chain, so it is paged again (a new incident), once."""
    chain, w = setup(tmp_path)
    step(w, chain, blocks=3)
    fork = chain.number
    top_up(chain)
    chain.mine()
    drain(chain)
    step(w, chain)
    chain.reorg(fork)
    top_up(chain)
    step(w, chain, blocks=2)
    key = f"{RETIRED_SLOT4}:{fork}"
    assert sent(w) == [("page", "open", key), ("info", "resolved", key), ("page", "open", key)]
    assert open_keys(w) == [key] and orphans(w) == []
    step(w, chain, blocks=3)
    assert len(sent(w)) == 3


def test_m13_pending_orphan_survives_a_restart(tmp_path):
    """(d) restart between the reorg run (key pending) and its settlement: the orphan is durable; the new process
    settles it once the checkpoint passes its block (here: evidence left; never re-paged)."""
    chain, w = setup(tmp_path, confirmations=2)
    step(w, chain, blocks=3)
    fork = chain.number
    top_up(chain)
    step(w, chain, blocks=5)
    key = f"{RETIRED_SLOT4}:{fork}"
    chain.reorg(fork)
    step(w, chain, blocks=1)
    assert orphans(w) == [(RETIRED_SLOT4, fork, None)]
    w2 = _restart(tmp_path, w, chain)
    step(w2, chain, blocks=4)
    assert sent(w2) == [("page", "open", key), ("info", "resolved", key)] and open_keys(w2) == []
    assert REORG_TEXT in outbox(w2, "kind='resolved'")[0]["text"] and orphans(w2) == []


def test_m13_second_reorg_while_an_orphan_is_pending_keeps_what_the_operator_was_told(tmp_path):
    """Two reorgs before the key is settled: the first orphan entry (operator told "value arrived") wins; the final
    chain re-includes the top-up at the same block: one page in total, still open."""
    chain, w = setup(tmp_path, confirmations=2)
    step(w, chain, blocks=3)
    fork = chain.number
    top_up(chain)
    step(w, chain, blocks=5)
    key = f"{RETIRED_SLOT4}:{fork}"
    chain.reorg(fork)
    step(w, chain, blocks=1)
    assert orphans(w) == [(RETIRED_SLOT4, fork, None)]
    chain.reorg(fork - 1)
    chain.mine()
    top_up(chain)
    step(w, chain, blocks=1)
    step(w, chain, blocks=4)
    assert w.checkpoint()["block"] >= fork
    assert sent(w) == [("page", "open", key)] and open_keys(w) == [key] and orphans(w) == []


def test_m13_pending_orphan_6h_reminder_says_it_is_pending(tmp_path):
    """Accepted behaviour (documented): if the new chain does not reach the replaced block for 6 h, the open
    incident's 6 h reminder carries the "not reached yet" statement (the incident is kept, nothing new is claimed)."""
    chain, w = setup(tmp_path, confirmations=2)
    step(w, chain, blocks=3)
    fork = chain.number
    top_up(chain)
    step(w, chain, blocks=5)
    chain.reorg(fork)
    step(w, chain, blocks=1)
    step(w, chain, blocks=0, seconds=6 * 3600 + 1)
    (re,) = outbox(w, "kind='realert'")
    assert PENDING_TEXT in re["text"]


# ---------------------------------------------------------------- repair 2: best-effort attribution (QA attempt 2 F4)


@pytest.mark.parametrize("mangle", ["null_receipt", "junk_receipt", "null_log", "junk_logs", "junk_block_txs"])
def test_m13_malformed_attribution_input_never_blocks_the_page(tmp_path, mangle):
    """Attribution is best effort: any malformed provider answer (the QA case: a null entry in the receipts list)
    is noted in the page, which is sent at the first run."""
    chain, w = setup(tmp_path)
    orig = chain._handle

    def handle(m, p):
        r = orig(m, p)
        if m == "eth_getBlockReceipts" and isinstance(r, list):
            r = {"null_receipt": [None, *r], "junk_receipt": ["0x", *r],
                 "null_log": [dict(rc, logs=[None]) for rc in r], "junk_logs": [dict(rc, logs=7) for rc in r]
                 }.get(mangle, r)
        if m == "eth_getBlockByNumber" and p[1] and isinstance(r, dict) and mangle == "junk_block_txs":
            r = dict(r, transactions=5)
        return r

    chain._handle = handle
    top_up(chain)
    step(w, chain)
    assert [k for _, k, _ in sent(w)] == ["open"]
    assert "lookup failed" in outbox(w, "kind='open'")[0]["text"]
    assert not [r for r in outbox(w, "kind='health'") if "check-error" in r["text"]]


# ---------------------------------------------------------------- repair 2: non-archive RPC (QA attempt 2 F2)


def _pruned_gap(tmp_path, gap=300, window=100, top_up_at=None, **checks):
    """A restart after a `gap`-block outage on a node that serves state only `window` blocks deep (publicnode
    serves about 100, LIVE in QA attempt 2); a top-up `top_up_at` blocks into the gap (None: none)."""
    chain, w = setup(tmp_path, checks=checks or None)
    t = None
    if top_up_at is not None:
        chain.mine(top_up_at)
        top_up(chain, RETIRED_LIQUIDATOR, eth(0.3))
        t = chain.number
    chain.mine(gap - (top_up_at or 0))
    chain.state_window = window
    return chain, _restart(tmp_path, w, chain, checks), t


def test_m13_qa2_f2_pruned_rpc_restart_gap_recovers_after_bounded_failures_and_says_so(tmp_path):
    """QA attempt 2 F2: the first `state_fallback_after` - 1 runs raise (an RPC failure, retried: maybe transient);
    the next one reads the newest `state_window_blocks`, compares them with the last read balance (the top-up in
    the unread range still pages, located there), says archive state is needed (`m13-state`) and advances."""
    chain, w, t = _pruned_gap(tmp_path, top_up_at=50)
    last = {r[0]: r[1] for r in w.db.conn.execute("SELECT address, block FROM m13_watch")}[RETIRED_LIQUIDATOR]
    for _ in range(2):
        with pytest.raises(RpcError, match="Archive requests"):
            step(w, chain)
    step(w, chain)
    cp = w.checkpoint()["block"]
    lo = cp - 64
    assert [r[1] for r in w.db.conn.execute("SELECT address, block FROM m13_watch")] == [cp, cp]
    (page,) = outbox(w, "kind='open'")
    assert "+0.300000 ETH" in page["text"] and f"blocks {last + 1}..{lo + 1}" in page["text"]
    assert "did not serve the state" in page["text"] and "archive state needed" in page["text"]
    assert open_keys(w) == [f"{RETIRED_LIQUIDATOR}:{lo + 1}"] and last < t <= lo
    states = [r for r in outbox(w, "kind='health'") if ": m13-state" in r["text"]]
    assert len(states) == 2 and all(f"{last + 1}..{lo}" in r["text"] and "Archive state is needed" in r["text"]
                                    for r in states)
    # bounded: the refused full read (cap 256 per address) plus the window read (64 per address)
    assert w.db.meta("run:m13")["rpc_calls"].get("eth_getBalance", 0) <= 2 * 256 + 2 * 64
    step(w, chain, blocks=3)  # back to normal: every block read, no new health message
    assert len([r for r in outbox(w, "kind='health'") if ": m13-state" in r["text"]]) == 2


def test_m13_pruned_rpc_gap_without_increase_is_a_health_notice_only(tmp_path):
    chain, w, _ = _pruned_gap(tmp_path)
    for _ in range(2):
        with pytest.raises(RpcError):
            step(w, chain)
    step(w, chain)
    assert sent(w) == [] and len([r for r in outbox(w, "kind='health'") if ": m13-state" in r["text"]]) == 2


def test_m13_pruned_rpc_state_window_config_moves_the_fallback(tmp_path):
    """`state_fallback_after` 1 falls back in the first run; a 200-block configured window the node cannot serve
    is narrowed (÷4) to one it can."""
    chain, w, t = _pruned_gap(tmp_path, top_up_at=250, state_fallback_after=1, state_window_blocks=200)
    step(w, chain)
    cp = w.checkpoint()["block"]
    assert open_keys(w) == [f"{RETIRED_LIQUIDATOR}:{cp - 50 + 1}"] and t <= cp - 50


def test_m13_archive_rpc_long_gap_never_uses_the_state_fallback(tmp_path):
    chain, w, t = _pruned_gap(tmp_path, top_up_at=50, window=None)
    step(w, chain)
    assert [k for _, _, k in sent(w)] == [f"{RETIRED_LIQUIDATOR}:{t}"]
    assert not [r for r in outbox(w, "kind='health'") if ": m13-state" in r["text"]]


def test_m13_rpc_down_never_uses_the_state_fallback(tmp_path):
    """An RPC that serves no state at all keeps raising (RPC failure, retried), the watch does not move."""
    chain, w = setup(tmp_path)
    top_up(chain)
    before = {r[0] for r in w.db.conn.execute("SELECT block FROM m13_watch")}
    chain.fail_methods.add("eth_getBalance")
    for _ in range(5):
        with pytest.raises(RpcError):
            step(w, chain)
    assert sent(w) == [] and {r[0] for r in w.db.conn.execute("SELECT block FROM m13_watch")} == before
    chain.fail_methods.clear()
    step(w, chain)
    assert [k for _, k, _ in sent(w)] == ["open"]
    assert not [r for r in outbox(w, "kind='health'") if ": m13-state" in r["text"]]


def test_m13_transient_read_errors_below_the_threshold_lose_no_block(tmp_path):
    """Fewer consecutive failures than `state_fallback_after` (and a success resets the count): every block is read,
    so a top-up swept two blocks later is still seen (no fallback, no m13-state)."""
    chain, w = setup(tmp_path)
    for _ in range(3):
        top_up(chain)
        b = chain.number
        chain.mine()
        drain(chain)
        chain.fail_methods.add("eth_getBalance")
        for _ in range(2):
            with pytest.raises(RpcError):
                step(w, chain, blocks=40)
        chain.fail_methods.clear()
        step(w, chain)
        assert sent(w)[-2:] == [("page", "open", f"{RETIRED_SLOT4}:{b}"), ("info", "resolved", f"{RETIRED_SLOT4}:{b}")]
    assert not [r for r in outbox(w, "kind='health'") if ": m13-state" in r["text"]]


def test_m13_reorg_deeper_than_a_pruned_rpc_state_raises_a_check_error_naming_archive(tmp_path):
    """A reorg run that cannot re-read the ancestor's state: RPC failures first (maybe transient), then a
    `check-error` that names the archive requirement (re-sent every 6 h); marker, rows and incident kept, nothing
    resolved; an archive RPC settles it."""
    chain, w, fork = _top_up_then(tmp_path)
    step(w, chain, blocks=10)
    key = f"{RETIRED_SLOT4}:{fork}"
    chain.reorg(fork)
    chain.state_window = 4
    for _ in range(2):
        with pytest.raises(RpcError):
            step(w, chain, blocks=12)
    step(w, chain, blocks=12)  # a RuntimeError: the framework records it as check-error (does not surface)
    err = [r for r in outbox(w, "kind='health'") if "check-error" in r["text"] or "raised" in r["text"]]
    assert err and "configure an archive RPC" in err[-1]["text"]
    assert w.db.meta("reorg:m13") is not None and open_keys(w) == [key] and sent(w) == [("page", "open", key)]
    chain.state_window = None
    step(w, chain)
    assert sent(w) == [("page", "open", key), ("info", "resolved", key)] and w.db.meta("reorg:m13") is None


def test_m13_state_settings_are_validated_at_construction(tmp_path):
    for k in ("state_window_blocks", "state_fallback_after"):
        with pytest.raises(SystemExit, match=k):
            setup(tmp_path / k, checks={k: 0})


# ---------------------------------------------------------------- repair 3: QA attempt 2 (Codex) Appendix A, M13 part
# (ported verbatim except the module prefix; green on f779b45 and after)


@pytest.mark.parametrize('addr', [RETIRED_SLOT4, RETIRED_LIQUIDATOR])
def test_qa2c_m13_resolved_topup_reorg_replacement(tmp_path, addr):
    c, w = setup(tmp_path)
    top_up(c, addr, value=eth(.6))
    step(w, c)
    b = c.sealed
    fork = c.number
    drain(c, addr, value=eth(.6) - 21000 * c.base_fee(c.number))
    step(w, c)
    assert not open_keys(w)
    c.reorg(fork)
    top_up(c, addr, value=eth(.2))
    step(w, c)
    assert set(open_keys(w)) == {f'{addr}:{b}', f'{addr}:{fork}'}
    assert len([x for x in sent(w) if x[0] == 'page' and x[1] == 'open']) == 3


@pytest.mark.parametrize('out_first', [False, True])
def test_qa2c_m13_same_block_topup_and_spend(tmp_path, out_first):
    c, w = setup(tmp_path)
    c.set_balance(RETIRED_SLOT4, eth(2))
    step(w, c)
    old = set(open_keys(w))
    if out_first:
        drain(c, value=eth(.2))
        top_up(c, value=eth(.5))
    else:
        top_up(c, value=eth(.5))
        drain(c, value=eth(.2))
    b = c.number
    step(w, c)
    assert f'{RETIRED_SLOT4}:{b}' in set(open_keys(w)) - old
    ev = json.loads(w.db.conn.execute('SELECT evidence FROM incidents WHERE key=?', (f'{RETIRED_SLOT4}:{b}',)).fetchone()[0])
    assert int(ev['delta_wei']) == eth(.3) - 21000 * c.base_fee(b)


def test_qa2c_m13_restart_gap_bounded_two_keys(tmp_path):
    c, w = setup(tmp_path, checks={'max_blocks_per_run': 7})
    clock = w.clock
    start = w.cfg.start_block
    activation = w.cfg.activation_block
    w.db.conn.close()
    c.mine(1000)
    c.internal_transfer(RETIRED_SLOT4, eth(.2))
    c.internal_transfer(RETIRED_LIQUIDATOR, eth(.4))
    c.mine(1000)
    c, w = wallet_worker(tmp_path, ['m13'], chain=c, clock=clock, start_block=start, activation_block=activation,
                         checks={'m13': {'max_blocks_per_run': 7}})
    advance(w)
    assert len(open_keys(w)) == 2
    assert w.db.meta('run:m13')['rpc_calls']['eth_getBalance'] <= 14
    assert len(gaps(w)) == 2
