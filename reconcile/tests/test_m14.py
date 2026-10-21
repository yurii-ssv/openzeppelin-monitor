"""M14 — deposit front-running of liquidations: V2a.7 correlation (inbox revert × ClusterDeposited below one
block of burn, same or previous block), one scenario per trigger line plus benign twins (V2x.1), live inputs
(V2x.2), restart (V2x.3), rpc_budget, UNKNOWN inputs, reorg (README §6.2)."""

from __future__ import annotations

import json

import pytest
from fixtures_liquidation import (
    BURN,
    OPS,
    OPS2,
    OWNER,
    OWNER2,
    THRESHOLD,
    LiquidationChain,
    cid_hex,
    failed_liquidation,
    incidents,
    open_incidents,
    restart,
    sent,
    setup,
    stub_liquidation_reverts,
)
from harness import advance, outbox

from eb_storage import cluster_id

pytestmark = pytest.mark.m14

M14 = ("m14",)


def make(tmp_path, cfg=None, **kw):
    path = tmp_path / "failed-tx.jsonl"
    path.write_text("")
    chain = LiquidationChain()
    stub_liquidation_reverts(chain)
    chain, w, cl = setup(tmp_path, checks=M14, cfg=cfg, chain=chain, inbox={"path": str(path)}, **kw)
    cl.create(OWNER, OPS, balance=THRESHOLD - 1_000 * BURN)  # liquidatable, far from zero
    cl.create(OWNER2, OPS2, balance=10**18)  # healthy
    advance(w)
    return chain, w, cl, path


def frontrun(chain, cl, path, same_block=True, value=1, owner=OWNER, ops=OPS, **kw):
    """A dust deposit, then the liquidation carrying the tuple from before it (mined)."""
    old = cl.tuple(owner, ops)
    cl.deposit(owner, ops, value=value, mine=not same_block)
    tx, _ = failed_liquidation(chain, cl, path, owner, ops, tuple_=old, **kw)
    chain.mine()
    return tx


def hits(w):
    return [dict(r) for r in w.db.conn.execute("SELECT * FROM m14_hits ORDER BY block")]


# ---------------------------------------------------------------- V2a.7 / V2x.1: the hit (info)


@pytest.mark.parametrize("same_block", [True, False])
def test_v2a7_dust_deposit_same_or_previous_block_is_info(tmp_path, same_block):
    chain, w, cl, path = make(tmp_path)
    tx = frontrun(chain, cl, path, same_block=same_block)
    advance(w)
    assert sent(w, "m14") == [("open", "info")]
    (h,) = hits(w)
    assert h["tx_hash"] == tx and h["cluster_id"] == cid_hex() and h["revert_name"] == "IncorrectClusterState"
    assert (h["deposit_block"] == h["block"]) is same_block and h["value"] == "1" and h["burn_rate"] == str(BURN)
    assert outbox(w, "kind='open'")[0]["delivery"] == "discord"


def test_v2a7_benign_deposit_two_blocks_earlier(tmp_path):
    chain, w, cl, path = make(tmp_path)
    old = cl.tuple()
    cl.deposit(value=1)
    chain.mine()
    failed_liquidation(chain, cl, path, tuple_=old)
    chain.mine()
    advance(w)
    assert not hits(w) and not incidents(w, "m14")


def test_v2a7_benign_deposit_of_at_least_one_block_of_burn(tmp_path):
    chain, w, cl, path = make(tmp_path)
    frontrun(chain, cl, path, value=BURN)
    advance(w)
    assert not hits(w) and not incidents(w, "m14")


def test_v2a7_benign_deposit_after_the_failed_liquidation_in_the_same_block(tmp_path):
    chain, w, cl, path = make(tmp_path)
    old = cl.tuple()
    failed_liquidation(chain, cl, path, tuple_=old)  # first in the block (reverted for another reason)
    cl.deposit(value=1)
    advance(w)
    assert not hits(w)


def test_v2a7_same_block_order_unknown_is_assumed_to_precede(tmp_path):
    chain, w, cl, path = make(tmp_path)
    old = cl.tuple()
    failed_liquidation(chain, cl, path, tuple_=old, tx_index=False)  # the node reports no transactionIndex
    cl.deposit(value=1)
    advance(w)
    (h,) = hits(w)
    assert h["ordering"] == "unknown" and "order unknown" in outbox(w, "kind='open'")[0]["text"]


def test_v2a7_benign_cluster_not_liquidatable(tmp_path):
    chain, w, cl, path = make(tmp_path)
    frontrun(chain, cl, path, owner=OWNER2, ops=OPS2)
    advance(w)
    assert not hits(w) and not incidents(w, "m14")


def test_v2a7_benign_other_failed_transactions_are_ignored(tmp_path):
    chain, w, cl, path = make(tmp_path)
    cl.deposit(value=1, mine=False)
    tx = chain.add_tx("0x" + "16" * 20, "0xdd9bc35ae942ef0cfa76930954a156b3ff30a4e1", "0xbc26e7e5")
    with open(path, "a") as fh:
        fh.write(json.dumps({"schema": 1, "tx_hash": tx, "block": chain.number, "block_hash": chain.hash(chain.number),
                             "to": "0xdd9bc35ae942ef0cfa76930954a156b3ff30a4e1", "selector": "0xbc26e7e5",
                             "function": "updateClusterBalance(…)", "status": "failure"}) + "\n")
    chain.stub_call("0xdd9bc35ae942ef0cfa76930954a156b3ff30a4e1", "0xbc26e7e5", revert="0x12e04c87")
    chain.mine()
    advance(w)
    assert not hits(w) and not incidents(w, "m14")


def test_v2a7_liquidate_ssv_entry_is_correlated_too(tmp_path):
    chain, w, cl, path = make(tmp_path)
    frontrun(chain, cl, path, fn="liquidateSSV")
    advance(w)
    assert [h["function"] for h in hits(w)] == ["liquidateSSV"]


# ---------------------------------------------------------------- V2x.1: repetition (warn), zero (page), end


def test_three_hits_within_the_window_warn(tmp_path):
    chain, w, cl, path = make(tmp_path)
    for _ in range(2):
        frontrun(chain, cl, path)
        chain.mine(10)
        advance(w)
    assert sent(w, "m14") == [("open", "info"), ("update", "info")]
    frontrun(chain, cl, path)
    advance(w)
    assert sent(w, "m14") == [("open", "info"), ("update", "info"), ("escalate", "warn")]
    assert outbox(w, "kind='escalate'")[0]["delivery"] == "telegram"
    frontrun(chain, cl, path)  # a fourth: recorded, not re-sent
    advance(w)
    assert len(sent(w, "m14")) == 3 and json.loads(incidents(w, "m14")[0]["evidence"])["count"] == 4


def test_three_hits_benign_spread_beyond_the_window_stay_info(tmp_path):
    chain, w, cl, path = make(tmp_path)
    for _ in range(3):
        frontrun(chain, cl, path)
        advance(w)
        chain.mine(4_000)
    assert [s for _, s in sent(w, "m14")] == ["info", "info", "info"]
    assert len(open_incidents(w, "m14")) == 1


def test_front_run_cluster_at_computed_zero_pages(tmp_path):
    chain, w, cl, path = make(tmp_path)
    cl.create(OWNER, OPS2, balance=50 * BURN)
    frontrun(chain, cl, path, owner=OWNER, ops=OPS2)
    advance(w)
    assert sent(w, "m14") == [("open", "info")]
    chain.mine(60)
    w.clock.advance(600)
    advance(w)  # the sweep re-evaluates the open cluster
    assert sent(w, "m14") == [("open", "info"), ("escalate", "page")]
    assert "Computed zero" in outbox(w, "kind='escalate'")[0]["text"]


def test_computed_zero_benign_without_a_hit_is_silent(tmp_path):
    chain, w, cl, path = make(tmp_path)
    cl.create(OWNER, OPS2, balance=5 * BURN)
    chain.mine(10)
    w.clock.advance(600)
    advance(w)
    assert not incidents(w, "m14")


def test_no_hit_for_a_window_resolves(tmp_path):
    chain, w, cl, path = make(tmp_path, cfg={"m14": {"repeat_count": 1}})
    frontrun(chain, cl, path)
    advance(w)
    assert sent(w, "m14") == [("open", "warn")]
    chain.mine(7_199)
    w.clock.advance(600)
    advance(w)
    assert len(open_incidents(w, "m14")) == 1
    chain.mine(1)
    w.clock.advance(600)
    advance(w)
    assert sent(w, "m14") == [("open", "warn"), ("resolved", "info")]
    assert not w.db.conn.execute("SELECT * FROM m14_open").fetchall()


# ---------------------------------------------------------------- V2x.2: inputs from the chain and config


def test_thresholds_from_chain_burn_rate_decides_dust(tmp_path):
    chain, w, cl, path = make(tmp_path)
    frontrun(chain, cl, path, value=2 * BURN)
    advance(w)
    assert not hits(w)
    cl.set_burn(burn=3 * BURN)  # an operator fee increase: the same deposit is now below one block of burn
    frontrun(chain, cl, path, value=2 * BURN)
    advance(w)
    assert len(hits(w)) == 1 and hits(w)[0]["burn_rate"] == str(3 * BURN)


def test_thresholds_from_chain_liquidatable_follows_the_live_threshold_period(tmp_path):
    from harness import stub_params

    from eb_storage import u256

    chain, w, cl, path = make(tmp_path)
    frontrun(chain, cl, path, owner=OWNER2, ops=OPS2)
    advance(w)
    assert not hits(w)
    stub_params(chain, liquidation_threshold_period=2 * 10**8)  # 2e8 blocks × 1e10 wei > the 1 ETH balance
    chain.emit_event("LiquidationThresholdPeriodUpdated", [], u256(2 * 10**8))
    chain.mine()
    frontrun(chain, cl, path, owner=OWNER2, ops=OPS2)
    advance(w)
    assert [h["cluster_id"] for h in hits(w)] == [cid_hex(OWNER2, OPS2)]


def test_thresholds_from_chain_window_and_repeat_from_config(tmp_path):
    chain, w, cl, path = make(tmp_path, cfg={"m14": {"window_blocks": 100, "repeat_count": 2}})
    frontrun(chain, cl, path)
    advance(w)
    frontrun(chain, cl, path)
    advance(w)
    assert sent(w, "m14")[-1] == ("escalate", "warn")


# ---------------------------------------------------------------- V2x.3: restart


def test_restart_does_not_duplicate_or_reprocess(tmp_path):
    chain, w, cl, path = make(tmp_path, cfg={"m14": {"repeat_count": 2}})
    frontrun(chain, cl, path)
    advance(w)
    frontrun(chain, cl, path)
    advance(w)
    assert sent(w, "m14") == [("open", "info"), ("escalate", "warn")]
    w2 = restart(tmp_path, w, checks=M14, cfg={"m14": {"repeat_count": 2}}, inbox={"path": str(path)})
    advance(w2)
    chain.mine()
    advance(w2)
    assert sent(w2, "m14") == [("open", "info"), ("escalate", "warn")] and len(hits(w2)) == 2


# ---------------------------------------------------------------- rpc_budget


def test_rpc_budget_per_entry(tmp_path):
    chain, w, cl, path = make(tmp_path)
    frontrun(chain, cl, path)
    advance(w)
    calls = w.db.meta("run:m14")["rpc_calls"]
    assert calls["eth_call"] <= 3 + 1 and calls["eth_getTransactionByHash"] == 1


# ---------------------------------------------------------------- UNKNOWN inputs


def test_unknown_transaction_is_health_not_dismissed(tmp_path):
    chain, w, cl, path = make(tmp_path)
    with open(path, "a") as fh:
        fh.write(json.dumps({"schema": 1, "tx_hash": "0x" + "77" * 32, "block": chain.number,
                             "block_hash": chain.hash(chain.number), "to": "0xdd9bc35ae942ef0cfa76930954a156b3ff30a4e1",
                             "selector": "0xbc26e7e5", "function": "liquidate(address,uint64[],(uint32,uint64,"
                             "uint64,bool,uint256))", "status": "failure"}) + "\n")
    chain.mine()
    advance(w)
    (h,) = [r for r in outbox(w) if r["key"].startswith("health:m14-unknown")]
    assert "calldata unavailable" in h["text"] and not incidents(w, "m14")


def test_unknown_deposit_block_views_are_health(tmp_path):
    chain, w, cl, path = make(tmp_path)
    chain.view_faults[cluster_id(OWNER, OPS)] = "revert"
    frontrun(chain, cl, path)
    advance(w)
    assert not hits(w) and any(r["key"].startswith("health:m14-unknown") for r in outbox(w))


def test_unknown_zero_test_keeps_a_page(tmp_path):
    chain, w, cl, path = make(tmp_path)
    cl.create(OWNER, OPS2, balance=50 * BURN)
    frontrun(chain, cl, path, owner=OWNER, ops=OPS2)
    chain.mine(60)
    advance(w)
    assert sent(w, "m14")[-1] == ("open", "page")
    cl.corrupt(OWNER, OPS2)
    chain.mine()
    w.clock.advance(600)
    advance(w)
    assert [r["severity"] for r in open_incidents(w, "m14").values()] == ["page"]


def test_rpc_failure_raises_and_the_entry_is_retried(tmp_path):
    chain, w, cl, path = make(tmp_path)
    frontrun(chain, cl, path)
    w.check_reorg()
    w.scan()
    w.poll_inbox()
    chain.down = True
    with pytest.raises(Exception):
        w.run_checks()
    chain.down = False
    advance(w)
    assert len(hits(w)) == 1 and sent(w, "m14") == [("open", "info")]


# ---------------------------------------------------------------- reorg (README §6.2)


@pytest.mark.parametrize("confirmations,depth", [(0, 1), (12, 20)])
def test_reorg_removing_the_front_run_resolves_and_never_repages(tmp_path, confirmations, depth):
    chain, w, cl, path = make(tmp_path, cfg={"m14": {"repeat_count": 1}}, confirmations=confirmations)
    chain.mine(confirmations)
    advance(w)
    fork = chain.number
    frontrun(chain, cl, path)
    chain.mine(confirmations + depth)
    advance(w)
    assert sent(w, "m14") == [("open", "warn")]
    chain.reorg(fork)
    chain.mine(confirmations + depth + 2)
    advance(w)
    assert [r["state"] for r in incidents(w, "m14")] == ["resolved"] and not hits(w)
    assert sent(w, "m14") == [("open", "warn"), ("resolved", "info")]
    w.clock.advance(10 * 86_400)
    chain.mine()
    advance(w)
    w.housekeeping()
    assert not outbox(w, "kind='realert'") and len(sent(w, "m14")) == 2


@pytest.mark.parametrize("confirmations,depth", [(0, 1), (12, 20)])
def test_reorg_above_a_surviving_hit_keeps_it_silently(tmp_path, confirmations, depth):
    chain, w, cl, path = make(tmp_path, confirmations=confirmations)
    frontrun(chain, cl, path)
    chain.mine(confirmations + depth + 2)
    advance(w)
    assert sent(w, "m14") == [("open", "info")]
    chain.reorg(chain.number - depth)
    chain.mine(depth + 2)
    advance(w)
    assert "reorg" in w.db.meta("run:m14")["reasons"]
    assert sent(w, "m14") == [("open", "info")] and len(hits(w)) == 1 and len(open_incidents(w, "m14")) == 1


def test_reorg_reinclusion_on_the_new_chain_is_re_derived(tmp_path):
    chain, w, cl, path = make(tmp_path, cfg={"m14": {"repeat_count": 1}})
    fork = chain.number
    old = cl.tuple()
    frontrun(chain, cl, path)
    chain.mine(2)
    advance(w)
    assert sent(w, "m14") == [("open", "warn")]
    chain.reorg(fork)
    cl.state[cluster_id(OWNER, OPS)].update(index=old.index, nfi=old.network_fee_index, balance=old.balance)
    chain.mine()  # the same front-run lands one block later on the new chain (new block hash: a new inbox line)
    cl.deposit(value=1, mine=False)
    failed_liquidation(chain, cl, path, tuple_=old)
    chain.mine(2)
    advance(w)
    (h,) = hits(w)
    assert h["block"] == fork + 1 and [r["state"] for r in incidents(w, "m14")] == ["open"]
    assert sent(w, "m14") == [("open", "warn")]


def test_reorg_crash_between_rewind_and_reorg_run_is_replayed(tmp_path):
    chain, w, cl, path = make(tmp_path, cfg={"m14": {"repeat_count": 1}})
    fork = chain.number
    frontrun(chain, cl, path)
    advance(w)
    assert sent(w, "m14") == [("open", "warn")]
    chain.reorg(fork)
    chain.mine(3)
    w.check_reorg()
    w2 = restart(tmp_path, w, checks=M14, cfg={"m14": {"repeat_count": 1}}, inbox={"path": str(path)})
    advance(w2)
    assert [r["state"] for r in incidents(w2, "m14")] == ["resolved"] and not hits(w2)
    assert w2.db.meta("reorg:m14") is None
