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
    Clusters,
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
from harness import advance, outbox, stub_params

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


def test_v2a7_benign_liquidate_ssv_on_an_eth_cluster_reverts_by_version(tmp_path):
    # liquidateSSV validates VERSION_SSV: on an ETH cluster it reverts whatever the tuple (not a front-run);
    # the legacy cluster case is test_qa1_h3_* below
    chain, w, cl, path = make(tmp_path)
    frontrun(chain, cl, path, fn="liquidateSSV")
    advance(w)
    assert not hits(w) and not incidents(w, "m14")
    assert not [r for r in outbox(w) if r["key"].startswith("health:m14")]


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
    chain.mine(7_199)  # 7,199 full 12 s slots = 86,388 s of chain time
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
    chain, w, cl, path = make(tmp_path, cfg={"m14": {"window_s": 1_200, "repeat_count": 2}})
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
    assert calls["eth_getBlockByNumber"] <= 2 and calls["eth_getStorageAt"] <= 2 + 2


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


# ---------------------------------------------------------------- QA cp2a attempt 1 regressions (repair 1)
# Each was red on the frozen head 098c2cd (attempts.md "Repair 1"), green after.


def unknown_health(w):
    return [r["text"] for r in outbox(w) if r["key"].startswith("health:m14-unknown")]


def test_qa1_h2_reorg_missing_replacement_transaction_does_not_clean_resolve(tmp_path):
    """QA H2 (its scenario): the canonical replacement's transaction is unreadable in the reorg run."""
    chain, w, cl, path = make(tmp_path, cfg={"m14": {"repeat_count": 1}})
    fork, old = chain.number, cl.tuple()
    frontrun(chain, cl, path)
    chain.mine(2)
    advance(w)
    assert sent(w, "m14") == [("open", "warn")]
    chain.reorg(fork)
    cl.state[cluster_id(OWNER, OPS)].update(index=old.index, nfi=old.network_fee_index, balance=old.balance)
    chain.mine()
    cl.deposit(value=1, mine=False)
    tx, _ = failed_liquidation(chain, cl, path, tuple_=old)
    chain.mine(2)
    w.check_reorg()
    w.scan()
    w.poll_inbox()
    saved = chain.txs.pop(tx)
    w.run_checks()  # the check raises: rolled back, the reorg marker is kept
    assert w.db.meta("reorg:m14") is not None and open_incidents(w, "m14")
    assert not outbox(w, "kind='resolved'")
    w.clock.advance(7 * 3600)
    w.housekeeping()
    assert not outbox(w, "kind='realert'")  # incidents of a pending reorg run are not re-alerted
    chain.txs[tx] = saved
    chain.mine()
    w.clock.advance(600)
    advance(w)
    assert open_incidents(w, "m14") and not outbox(w, "kind='resolved'")
    assert w.db.meta("reorg:m14") is None and [h["block"] for h in hits(w)] == [fork + 1]


def test_qa1_h2_unreadable_transaction_is_retried_until_evaluated(tmp_path):
    """Outside a reorg run an UNKNOWN entry is pending, named in health and retried; it becomes a hit later."""
    chain, w, cl, path = make(tmp_path)
    tx = frontrun(chain, cl, path)
    saved = chain.txs.pop(tx)
    advance(w)
    assert not hits(w) and not incidents(w, "m14")
    assert w.db.conn.execute("SELECT cluster_id FROM m14_pending").fetchall()[0][0] is None
    assert "retried every run" in unknown_health(w)[-1]
    chain.txs[tx] = saved
    chain.mine()
    w.clock.advance(600)
    advance(w)
    assert len(hits(w)) == 1 and sent(w, "m14") == [("open", "info")]
    assert not w.db.conn.execute("SELECT * FROM m14_pending").fetchall()


def test_qa1_h2_pending_entry_keeps_an_open_incident_past_the_window(tmp_path):
    """No resolve without positive proof: a pending entry in the window could be a hit on the open cluster."""
    chain, w, cl, path = make(tmp_path, cfg={"m14": {"repeat_count": 1}})
    frontrun(chain, cl, path)
    advance(w)
    assert sent(w, "m14") == [("open", "warn")]
    chain.mine(7_000)
    tx = frontrun(chain, cl, path)
    chain.txs.pop(tx)
    advance(w)
    chain.mine(300)  # the first hit leaves the window; the unreadable second may be a hit
    w.clock.advance(600)
    advance(w)
    assert [r["severity"] for r in open_incidents(w, "m14").values()] == ["warn"]
    assert sent(w, "m14") == [("open", "warn")]
    chain.mine(7_200)  # the pending entry ages out of the window unevaluated: nothing can support it now
    w.clock.advance(600)
    advance(w)
    assert sent(w, "m14") == [("open", "warn"), ("resolved", "info")]
    assert any("left the 86400 s window unevaluated" in t for t in unknown_health(w))


def test_qa1_h2_reorg_run_with_a_known_cluster_unknown_keeps_its_key(tmp_path):
    """A re-derived entry whose cluster is known but whose views fail keeps that cluster's key open."""
    chain, w, cl, path = make(tmp_path, cfg={"m14": {"repeat_count": 1}})
    fork = chain.number
    frontrun(chain, cl, path)
    chain.mine(2)
    advance(w)
    assert sent(w, "m14") == [("open", "warn")]
    chain.reorg(fork + 1)  # above the hit's block: the hit survives, its incident is a reorg key
    chain.view_faults[cluster_id(OWNER, OPS)] = "revert"
    chain.mine(2)
    w.clock.advance(600)
    advance(w)
    assert [r["severity"] for r in open_incidents(w, "m14").values()] == ["warn"]
    assert not outbox(w, "kind='resolved'") and w.db.meta("reorg:m14") is None


def legacy(tmp_path, balance=None, **kw):
    """make() plus a modelled legacy SSV cluster (SSV storage slot, SSV views, SSV parameters)."""
    chain, w, cl, path = make(tmp_path, **kw)
    burn = 10**9  # SSV wei per block
    cl.create(OWNER2, OPS, balance=100_240 * burn - 1_000 * burn if balance is None else balance, burn=burn,
              version="SSV")
    return chain, w, cl, path, burn


def test_qa1_h3_legacy_ssv_cluster_liquidate_ssv_front_run_is_info(tmp_path):
    chain, w, cl, path, burn = legacy(tmp_path)
    frontrun(chain, cl, path, owner=OWNER2, ops=OPS, fn="liquidateSSV")
    advance(w)
    info = w.inventory.get(chain.sealed).clusters[cid_hex(OWNER2, OPS)]
    assert info.version == "SSV" and info.valid
    (h,) = hits(w)
    assert h["function"] == "liquidateSSV" and h["version"] == "SSV" and h["burn_rate"] == str(burn)
    assert sent(w, "m14") == [("open", "info")] and "of 1 SSV" in outbox(w, "kind='open'")[0]["text"]


def test_qa1_h3_legacy_benign_one_block_of_ssv_burn_and_eth_liquidate_are_silent(tmp_path):
    chain, w, cl, path, burn = legacy(tmp_path)
    frontrun(chain, cl, path, owner=OWNER2, ops=OPS, fn="liquidateSSV", value=burn)  # not below one block of burn
    frontrun(chain, cl, path, owner=OWNER2, ops=OPS, fn="liquidate")  # liquidate validates VERSION_ETH
    advance(w)
    assert not hits(w) and not incidents(w, "m14") and not unknown_health(w)


def test_qa1_h3_legacy_benign_healthy_cluster_is_silent(tmp_path):
    chain, w, cl, path, burn = legacy(tmp_path, balance=10**30)
    frontrun(chain, cl, path, owner=OWNER2, ops=OPS, fn="liquidateSSV")
    advance(w)
    assert not hits(w) and not incidents(w, "m14")


def test_qa1_h3_legacy_cluster_at_computed_zero_pages(tmp_path):
    chain, w, cl, path, burn = legacy(tmp_path, balance=50 * 10**9)
    frontrun(chain, cl, path, owner=OWNER2, ops=OPS, fn="liquidateSSV")
    chain.mine(60)
    advance(w)
    assert sent(w, "m14") == [("open", "page")] and "Computed zero" in outbox(w, "kind='open'")[0]["text"]


def test_qa1_h3_legacy_minissv_stub_scenario(tmp_path):
    """QA H3 (its scenario): a MiniSSV legacy cluster whose SSV views are generic stubs."""
    from fakechain import MiniSSV, _enc, _topic_addr
    from harness import VIEWS

    from checks.m3_views import selector
    from eb_storage import u256

    chain, w, cl, path = make(tmp_path)
    owner, ops = "0x" + "bb" * 20, (31, 32, 33, 34)
    mini = MiniSSV(chain)
    cid = mini.legacy_ssv_cluster(owner, ops, 1)
    st = mini.clusters[cid]
    st["balance"] = 100
    mini._persist(cid, st)
    old = mini._cluster(st)
    chain.mine()
    st["balance"] += 1
    mini._persist(cid, st)
    tx = chain.new_tx(owner)
    chain.emit(tx, "ClusterDeposited", [_topic_addr(owner)], _enc(["uint64[]", "uint", "cluster"],
                                                                  [list(ops), 1, mini._cluster(st)]))
    for name, val in [("isLiquidatableSSV", 1), ("getBalanceSSV", 100), ("getBurnRateSSV", BURN)]:
        chain.stub_call(VIEWS, selector(name + "(address,uint64[],(uint32,uint64,uint64,bool,uint256))"), u256(val))
    failed_liquidation(chain, cl, path, owner=owner, ops=ops, fn="liquidateSSV", tuple_=old)
    chain.mine()
    advance(w)
    assert sent(w, "m14") == [("open", "info")]


def test_qa1_m1_three_hits_over_26_hours_of_chain_time_do_not_warn(tmp_path):
    """QA M1 (its scenario): 3,500 produced blocks per 13 hours (missed slots); three hits span 26 h."""
    chain, w, cl, path = make(tmp_path)
    for i in range(3):
        if i:
            chain.mine(3500)
            w.clock.advance(13 * 3600)
        frontrun(chain, cl, path)
        advance(w)
    assert not [s for _, s in sent(w, "m14") if s in {"warn", "page"}]
    assert json.loads(incidents(w, "m14")[-1]["evidence"])["count"] == 2


def test_qa1_m1_window_is_header_time_not_the_worker_clock(tmp_path):
    """Chain time alone decides: missed slots (no worker clock involved) stretch ~7,000 blocks over 26 h, and a
    worker clock that jumps a day per hit does not age out hits that are seconds apart in chain time."""
    chain, w, cl, path = make(tmp_path)
    chain.clock = None
    for i in range(3):
        if i:
            chain.mine(3500)
            chain.elapse(13 * 3600 - 3501 * 12)
        frontrun(chain, cl, path)
        advance(w)
    assert [s for _, s in sent(w, "m14")] == ["info", "info", "info"]
    (tmp_path / "b").mkdir()
    chain2, w2, cl2, path2 = make(tmp_path / "b")
    chain2.clock = None
    for _ in range(3):
        frontrun(chain2, cl2, path2)
        w2.clock.advance(86_400)
        advance(w2)
    assert sent(w2, "m14")[-1] == ("escalate", "warn")


def test_qa1_m2_old_hits_expire_at_the_current_checkpoint(tmp_path):
    """QA M2 (its scenario): the count is anchored at the checkpoint, so the first hit expires before the last."""
    chain, w, cl, path = make(tmp_path)
    frontrun(chain, cl, path)
    advance(w)
    for _ in range(2):
        chain.mine(3500)
        w.clock.advance(3500 * 12)
        frontrun(chain, cl, path)
        advance(w)
    assert list(open_incidents(w, "m14").values())[0]["severity"] == "warn"
    chain.mine(300)
    w.clock.advance(300 * 12 + 600)
    advance(w)
    assert json.loads(incidents(w, "m14")[-1]["evidence"])["count"] == 2
    assert list(open_incidents(w, "m14").values())[0]["severity"] == "info"
    assert sent(w, "m14") == [("open", "info"), ("update", "info"), ("escalate", "warn")]  # silent downgrade


def test_qa1_m3_later_same_block_top_up_does_not_hide_the_attack(tmp_path):
    """QA M3 (its scenario): dust deposit, failed liquidation, then a large top-up, all in one block."""
    chain, w, cl, path = make(tmp_path)
    old = cl.tuple()
    cl.deposit(value=1, mine=False)
    failed_liquidation(chain, cl, path, tuple_=old)
    cl.deposit(value=10**18, mine=False)
    chain.mine()
    advance(w)
    assert sent(w, "m14") == [("open", "info")]
    (h,) = hits(w)
    assert h["value"] == "1" and h["deposit_block"] == h["block"]


def test_qa1_m3_previous_block_dust_with_a_later_same_block_top_up_is_a_hit(tmp_path):
    chain, w, cl, path = make(tmp_path)
    old = cl.tuple()
    cl.deposit(value=1)  # previous block
    failed_liquidation(chain, cl, path, tuple_=old)
    cl.deposit(value=10**18, mine=False)  # after the failed transaction
    chain.mine()
    advance(w)
    assert sent(w, "m14") == [("open", "info")]


def test_qa1_m3_benign_top_up_before_the_failed_transaction_is_silent(tmp_path):
    """The twin: the top-up precedes the failed liquidation, so the cluster it met was healthy."""
    chain, w, cl, path = make(tmp_path)
    old = cl.tuple()
    cl.deposit(value=1, mine=False)
    cl.deposit(value=10**18, mine=False)
    failed_liquidation(chain, cl, path, tuple_=old)
    chain.mine()
    advance(w)
    assert not hits(w) and not incidents(w, "m14") and not unknown_health(w)


def test_qa1_m3_benign_healthy_cluster_with_a_later_top_up_is_silent(tmp_path):
    chain, w, cl, path = make(tmp_path)
    old = cl.tuple(OWNER2, OPS2)
    cl.deposit(OWNER2, OPS2, value=BURN, mine=False)  # Σ ≥ one block of burn on a healthy cluster: proof
    failed_liquidation(chain, cl, path, OWNER2, OPS2, tuple_=old)
    cl.deposit(OWNER2, OPS2, value=10**18, mine=False)
    chain.mine()
    advance(w)
    assert not hits(w) and not incidents(w, "m14") and not unknown_health(w)


def test_qa1_m3_undeterminable_position_state_is_unknown_not_dismissed(tmp_path):
    """A withdrawal before the failed transaction and a top-up after it: the state it met is not derivable."""
    chain, w, cl, path = make(tmp_path)
    old = cl.tuple()
    cl.deposit(value=1, mine=False)
    cl.withdraw(value=1, mine=False)
    failed_liquidation(chain, cl, path, tuple_=old)
    cl.deposit(value=10**18, mine=False)
    chain.mine()
    advance(w)
    assert not incidents(w, "m14")
    assert "not derivable" in unknown_health(w)[-1]
    assert w.db.conn.execute("SELECT cluster_id FROM m14_pending").fetchall()[0][0] == cid_hex()


def test_qa1_m3_unknown_order_with_several_events_in_the_block_is_unknown(tmp_path):
    chain, w, cl, path = make(tmp_path)
    old = cl.tuple()
    cl.deposit(value=1, mine=False)
    failed_liquidation(chain, cl, path, tuple_=old, tx_index=False)
    cl.deposit(value=10**18, mine=False)
    chain.mine()
    advance(w)
    assert not incidents(w, "m14") and "order against the failed transaction is unknown" in unknown_health(w)[-1]


def test_qa1_h1_class_inventory_omission_does_not_lower_a_zero_page(tmp_path):
    """The H1 class in M14: an inventory that stops listing the cluster is not 'not in storage'."""
    import dataclasses

    chain, w, cl, path = make(tmp_path)
    cl.create(OWNER, OPS2, balance=50 * BURN)
    frontrun(chain, cl, path, owner=OWNER, ops=OPS2)
    chain.mine(60)
    advance(w)
    assert sent(w, "m14") == [("open", "page")]
    get = w.inventory.get
    w.inventory.get = lambda b: dataclasses.replace(get(b), clusters={
        k: v for k, v in get(b).clusters.items() if k != cid_hex(OWNER, OPS2)})  # an API page without it
    chain.mine()
    w.clock.advance(600)
    advance(w)
    assert [r["severity"] for r in open_incidents(w, "m14").values()] == ["page"]
    assert "Computed zero" in incidents(w, "m14")[-1]["body"]


def test_qa1_old_database_hits_without_block_time_are_filled_from_headers(tmp_path):
    chain, w, cl, path = make(tmp_path)
    frontrun(chain, cl, path)
    advance(w)
    w.db.conn.execute("UPDATE m14_hits SET block_time = NULL")
    chain.mine()
    w.clock.advance(600)
    advance(w)
    assert w.db.conn.execute("SELECT block_time FROM m14_hits").fetchone()[0] == chain.timestamp(hits(w)[0]["block"])


def test_sweep_m14_cluster_without_earlier_logs_later_top_up_uses_the_deposit_tuple_minus_value(tmp_path):
    """Sweep seeds 3, 5, …: a cluster registered before the scan (no log before B) gets a dust deposit, the failed
    liquidation and a later top-up in one block. `deposit` only adds msg.value to the validated tuple, so the
    tuple at the end of B-1 is the deposit log's minus its value (validated by the views at B-1)."""
    chain = LiquidationChain()
    stub_liquidation_reverts(chain)
    stub_params(chain)
    chain.mine()
    cl = Clusters(chain)
    cl.create(OWNER, OPS, balance=THRESHOLD - 1_000 * BURN)  # before the worker's start block: no stored log
    chain.mine()
    path = tmp_path / "failed-tx.jsonl"
    path.write_text("")
    chain, w, _ = setup(tmp_path, checks=M14, chain=chain, inbox={"path": str(path)})
    old = cl.tuple()
    cl.deposit(value=1, mine=False)
    failed_liquidation(chain, cl, path, tuple_=old)
    cl.deposit(value=10**18, mine=False)
    chain.mine()
    advance(w)
    assert sent(w, "m14") == [("open", "info")] and not unknown_health(w)


def test_sweep_m14_no_deposit_below_one_block_of_burn_is_decided_before_the_position_state(tmp_path):
    """Sweep seed 21: a deposit of one block of burn, the failed liquidation, then a top-up: no deposit qualifies,
    so the entry is no hit whatever the state at the failed transaction (not UNKNOWN, holds nothing open)."""
    chain, w, cl, path = make(tmp_path)
    old = cl.tuple()
    cl.deposit(value=BURN, mine=False)
    failed_liquidation(chain, cl, path, tuple_=old)
    cl.deposit(value=10**18, mine=False)
    chain.mine()
    advance(w)
    assert not hits(w) and not incidents(w, "m14") and not unknown_health(w)
    assert not w.db.conn.execute("SELECT * FROM m14_pending").fetchall()


# ---------------------------------------------------------------- DEFECT-CLASSES.md gaps found by the state table


def test_class2_window_resolution_on_deleted_blocks_reopens_on_a_shorter_chain(tmp_path):
    """The resolution came from the chain time of blocks a reorg replaces by a shorter chain: the hit is inside
    the window again at the new checkpoint and the incident is found again."""
    chain, w, cl, path = make(tmp_path, cfg={"m14": {"repeat_count": 1}})
    frontrun(chain, cl, path)
    advance(w)
    chain.mine(7_200)
    w.clock.advance(600)
    advance(w)
    assert sent(w, "m14") == [("open", "warn"), ("resolved", "info")]
    chain.reorg(chain.number - 100)
    chain.mine(2)
    advance(w)
    assert [r["state"] for r in incidents(w, "m14")] == ["resolved", "open"]
    assert sent(w, "m14") == [("open", "warn"), ("resolved", "info"), ("open", "warn")]


def test_class10_restart_while_an_entry_is_pending_retries_it(tmp_path):
    chain, w, cl, path = make(tmp_path)
    tx = frontrun(chain, cl, path)
    saved = chain.txs.pop(tx)
    advance(w)
    w2 = restart(tmp_path, w, checks=M14, inbox={"path": str(path)})
    chain.txs[tx] = saved
    chain.mine()
    w2.clock.advance(600)
    advance(w2)
    assert len(hits(w2)) == 1 and sent(w2, "m14") == [("open", "info")]


def test_class10_restart_while_the_reorg_run_raises_keeps_the_marker_until_it_can_evaluate(tmp_path):
    chain, w, cl, path = make(tmp_path, cfg={"m14": {"repeat_count": 1}})
    fork, old = chain.number, cl.tuple()
    frontrun(chain, cl, path)
    chain.mine(2)
    advance(w)
    chain.reorg(fork)
    cl.state[cluster_id(OWNER, OPS)].update(index=old.index, nfi=old.network_fee_index, balance=old.balance)
    chain.mine()
    cl.deposit(value=1, mine=False)
    tx, _ = failed_liquidation(chain, cl, path, tuple_=old)
    saved = chain.txs.pop(tx)
    chain.mine(2)
    advance(w)
    assert w.db.meta("reorg:m14") is not None
    w2 = restart(tmp_path, w, checks=M14, cfg={"m14": {"repeat_count": 1}}, inbox={"path": str(path)})
    advance(w2)
    assert w2.db.meta("reorg:m14") is not None and open_incidents(w2, "m14")
    chain.txs[tx] = saved
    chain.mine()
    advance(w2)
    assert w2.db.meta("reorg:m14") is None and open_incidents(w2, "m14")
    assert sent(w2, "m14") == [("open", "warn")]


@pytest.mark.parametrize("cfg", [{"window_s": 0}, {"repeat_count": 0}, {"sweep_interval_s": 0}])
def test_class11_invalid_configuration_is_a_startup_error(tmp_path, cfg):
    with pytest.raises(SystemExit, match="checks.m14"):
        make(tmp_path, cfg={"m14": cfg})
