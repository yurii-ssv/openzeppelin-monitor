"""M12 — operational wallet balances and burn rate (spec §3 M12, D4; plan §7 V2x.*, V2c.7).

Scenario numbers are the spec's measured burn (15.5 Mgas/day ≈ 0.0177 ETH/day at ~1.15 gwei; balance 0.3454 ETH
→ ≈ 19.5 days; at 5 gwei ≈ 4.5 days), carried by the fixtures, never by the check.
"""

from __future__ import annotations

import json
import random

import pytest
from fixtures_wallets import (
    DAY,
    LIQUIDATOR,
    ORACLE,
    PROXY,
    FakeBlockscout,
    WalletChain,
    eth,
    gwei,
    spend_history,
    wallet_worker,
)
from harness import advance, outbox

from checks.m12_wallet_burn import evidence_of
from rpc import RpcError

pytestmark = pytest.mark.m12

WINDOW = 50_400


def setup(tmp_path, balance=eth(0.3454), liquidator=eth(1), checks=None, page_size=50, worker=None, **history):
    chain = WalletChain(start_block=100_000)
    chain.set_balance(ORACLE, eth(5))
    chain.set_balance(LIQUIDATOR, liquidator)
    chain.mine()
    hashes = spend_history(chain, **history)
    chain.set_balance(ORACLE, balance)
    chain.mine()
    checks = {"unknown_grace_s": 0, **(checks or {})}  # health at the first UNKNOWN (grace: its own test)
    chain, w = wallet_worker(tmp_path, ["m12"], chain=chain, checks={"m12": checks}, **(worker or {}))
    w.bs.page_size = page_size
    return chain, w, hashes


def tick(w, chain, blocks=1, seconds=301):
    chain.mine(blocks)
    w.clock.advance(seconds)
    advance(w)


def alerts(w):
    """(severity, key, kind) of every page/warn/resolution row, in order."""
    return [(r["severity"], json.loads(inc_key(w, r["incident_id"])), r["kind"])
            for r in outbox(w, "kind != 'health' AND kind != 'digest'")]


def inc_key(w, inc_id):
    row = w.db.conn.execute("SELECT key FROM incidents WHERE id=?", (inc_id,)).fetchone()
    return json.dumps(row[0])


def open_incidents(w):
    return {r["key"]: r["severity"] for r in w.db.conn.execute("SELECT * FROM incidents WHERE state='open'")}


def evidence(w, key):
    """The incident's evidence, or (no incident: a resolve=True finding) the check's last spend evaluation."""
    row = w.db.conn.execute("SELECT evidence FROM incidents WHERE key=? ORDER BY rowid DESC", (key,)).fetchone()
    if row is not None:
        return json.loads(row[0])
    m = w.checks["m12"].last[key.split(":")[0]]
    return evidence_of({k: v for k, v in m.items() if k not in ("block", "balance")})


def health(w, kind="m12-spend"):
    return [r for r in outbox(w, "kind='health'") if f": {kind}" in r["text"]]


# ---------------------------------------------------------------- V2x.1: one test per trigger + its benign twin


def test_m12_benign_spec_status_is_silent_and_runway_is_measured(tmp_path):
    """Spec status 2026-10-20: 0.3454 ETH at 0.0177 ETH/day → ≈ 19.4 days; no alert of any kind."""
    chain, w, _ = setup(tmp_path)
    advance(w)
    assert alerts(w) == [] and open_incidents(w) == {} and health(w) == []
    ev = evidence(w, f"{ORACLE}:runway")  # resolve=True findings still record the evaluation
    assert ev["window_txs"] == "14" and ev["avg_daily_spend_wei"] == f"{15_500_000 * gwei(1.15):.6g}"
    assert 19.3 < float(ev["runway_trailing_days"]) < 19.5 and 19.3 < float(ev["runway_base_fee_days"]) < 19.5


def test_m12_oracle_balance_below_015_pages(tmp_path):
    chain, w, _ = setup(tmp_path, balance=eth(0.149), gas_per_day=1_000_000)
    advance(w)
    assert alerts(w) == [("page", f"{ORACLE}:balance", "open")]


def test_m12_oracle_balance_above_015_is_silent(tmp_path):
    chain, w, _ = setup(tmp_path, balance=eth(0.151), gas_per_day=1_000_000)
    advance(w)
    assert alerts(w) == []


def test_m12_oracle_runway_below_14_days_warns(tmp_path):
    chain, w, _ = setup(tmp_path, balance=eth(0.17825))  # 10 days at 0.017825 ETH/day
    advance(w)
    assert alerts(w) == [("warn", f"{ORACLE}:runway", "open")]


def test_m12_oracle_runway_of_20_days_is_silent(tmp_path):
    chain, w, _ = setup(tmp_path, balance=eth(0.3565))
    advance(w)
    assert alerts(w) == []


def test_m12_oracle_runway_below_7_days_pages(tmp_path):
    chain, w, _ = setup(tmp_path, balance=eth(0.2), gas_per_day=31_000_000)  # 0.03565 ETH/day → 5.6 days
    advance(w)
    assert alerts(w) == [("page", f"{ORACLE}:runway", "open")]


def test_m12_oracle_runway_exactly_7_days_warns_not_pages(tmp_path):
    """runway < 7 d pages; exactly 7 d (balance = 7 × the daily average, both computations) is a warn."""
    fees = 14 * (31_000_000 // 2) * gwei(1.15)  # the window's fees: 7 days × the daily average
    chain, w, _ = setup(tmp_path, balance=fees, gas_per_day=31_000_000)
    advance(w)
    ev = evidence(w, f"{ORACLE}:runway")
    assert ev["runway_trailing_days"] == "7" and ev["runway_base_fee_days"] == "7"
    assert alerts(w) == [("warn", f"{ORACLE}:runway", "open")]


def test_m12_oracle_daily_spend_above_004_warns(tmp_path):
    chain, w, _ = setup(tmp_path, balance=eth(10), gas_per_day=40_000_000)  # 0.046 ETH/day
    advance(w)
    assert alerts(w) == [("warn", f"{ORACLE}:daily-spend", "open")]


def test_m12_oracle_daily_spend_below_004_is_silent(tmp_path):
    chain, w, _ = setup(tmp_path, balance=eth(10), gas_per_day=34_000_000)  # 0.0391 ETH/day
    advance(w)
    assert alerts(w) == []


def test_m12_liquidator_below_02_warns(tmp_path):
    chain, w, _ = setup(tmp_path, liquidator=eth(0.19))
    advance(w)
    assert alerts(w) == [("warn", f"{LIQUIDATOR}:balance", "open")]


def test_m12_liquidator_above_02_is_silent(tmp_path):
    chain, w, _ = setup(tmp_path, liquidator=eth(0.21))
    advance(w)
    assert alerts(w) == []


def test_m12_liquidator_below_005_pages(tmp_path):
    chain, w, _ = setup(tmp_path, liquidator=eth(0.049))
    advance(w)
    assert alerts(w) == [("page", f"{LIQUIDATOR}:balance", "open")]


def test_m12_liquidator_at_006_warns_only(tmp_path):
    chain, w, _ = setup(tmp_path, liquidator=eth(0.06))
    advance(w)
    assert alerts(w) == [("warn", f"{LIQUIDATOR}:balance", "open")]


def test_m12_balance_recovers_and_resolves_then_reopens(tmp_path):
    chain, w, _ = setup(tmp_path, liquidator=eth(0.04))
    advance(w)
    chain.set_balance(LIQUIDATOR, eth(0.5))  # funded
    tick(w, chain)
    chain.set_balance(LIQUIDATOR, eth(0.1))
    tick(w, chain)
    assert alerts(w) == [("page", f"{LIQUIDATOR}:balance", "open"), ("info", f"{LIQUIDATOR}:balance", "resolved"),
                         ("warn", f"{LIQUIDATOR}:balance", "open")]


# ---------------------------------------------------------------- V2c.7: runway from trailing spend and from base fee


def test_m12_runway_from_trailing_spend_and_from_current_base_fee(tmp_path):
    """Spec: at 1.15 gwei 0.3454 ETH lasts ≈ 19.5 days, at 5 gwei ≈ 4.5 days. The trailing average still says
    19.4 days; the current base fee (mean of the last base_fee_blocks) pages."""
    chain, w, _ = setup(tmp_path)
    chain.set_base_fee(gwei(5))
    chain.mine(300)
    w.clock.advance(301)
    advance(w)
    ev = evidence(w, f"{ORACLE}:runway")
    assert 19.3 < float(ev["runway_trailing_days"]) < 19.5
    assert 4.4 < float(ev["runway_base_fee_days"]) < 4.5 and ev["base_fee_wei"] == str(gwei(5))
    assert alerts(w) == [("page", f"{ORACLE}:runway", "open")]
    (row,) = outbox(w, "severity='page'")
    assert "from current base fee" in row["text"] and "4.5 d" in row["text"] and "19.4 d" in row["text"]


def test_m12_runway_trailing_spend_drives_when_the_base_fee_fell(tmp_path):
    """The week was paid at a 5 gwei base fee (4.5 days of runway); the base fee is back at 1.15 gwei (19.4 days):
    the trailing computation pages."""
    chain = WalletChain(start_block=100_000, base_fee=gwei(5))
    chain.set_balance(ORACLE, eth(5))
    chain.set_balance(LIQUIDATOR, eth(1))
    chain.mine()
    spend_history(chain)
    chain.set_balance(ORACLE, eth(0.3454))
    chain.mine()
    chain.set_base_fee(gwei(1.15))
    chain.mine(300)
    chain, w = wallet_worker(tmp_path, ["m12"], chain=chain, checks={"m12": {"unknown_grace_s": 0}})
    advance(w)
    ev = evidence(w, f"{ORACLE}:runway")
    assert 4.4 < float(ev["runway_trailing_days"]) < 4.5 and 19.3 < float(ev["runway_base_fee_days"]) < 19.5
    assert ev["avg_priority_fee_wei"] == "0" and ev["tip_source"] == "observed over 14 txs"
    (row,) = outbox(w, "severity='page'")
    assert "from trailing spend" in row["text"]


@pytest.mark.parametrize("tip_mode, low, high", [("observed", 18.9, 19.1), ("none", 555, 570)])
def test_m12_base_fee_projection_includes_the_observed_priority_fee(tmp_path, tip_mode, low, high):
    """Measured 2026-10-21: the oracle wallet pays a fixed 1 gwei priority fee over a ~0.035 gwei base fee. The
    projection adds the window's gas-weighted tip ("observed", default); the spec-literal base fee alone ("none")
    would project ≈ 1.035 / 0.035 ≈ 30× the runway."""
    chain = WalletChain(start_block=100_000, base_fee=gwei(0.035))
    chain.set_balance(ORACLE, eth(5))
    chain.set_balance(LIQUIDATOR, eth(1))
    chain.mine()
    spend_history(chain, gas_price=gwei(1.035))
    fees = 14 * (15_500_000 // 2) * gwei(1.035)
    chain.set_balance(ORACLE, fees * 19 // 7)  # 19 days at the trailing rate
    chain.mine()
    chain, w = wallet_worker(tmp_path, ["m12"], chain=chain, checks={"m12": {"projection_tip": tip_mode}})
    advance(w)
    ev = evidence(w, f"{ORACLE}:runway")
    assert 18.99 < float(ev["runway_trailing_days"]) < 19.01
    assert low < float(ev["runway_base_fee_days"]) < high
    assert float(ev["avg_priority_fee_wei"]) == (gwei(1) if tip_mode == "observed" else 0)


def test_m12_observed_tip_can_only_make_the_check_louder(tmp_path):
    """Coordinator condition (2): the alarm takes the lower runway and the observed tip is >= 0, so "observed"
    is never quieter than "none". The week was paid at 0.035 gwei base + 1 gwei tip (trailing runway 16 days,
    silent); the base fee is now 0.5 gwei: base fee alone → 33 days (silent), base fee + observed tip → 11 days
    (warn)."""
    runs = {}
    for mode in ("none", "observed"):
        chain = WalletChain(start_block=100_000, base_fee=gwei(0.035))
        chain.set_balance(ORACLE, eth(5))
        chain.set_balance(LIQUIDATOR, eth(1))
        chain.mine()
        spend_history(chain, gas_price=gwei(1.035))
        chain.set_balance(ORACLE, 14 * (15_500_000 // 2) * gwei(1.035) * 16 // 7)  # 16 days at the trailing rate
        chain.mine()
        chain.set_base_fee(gwei(0.5))
        chain.mine(300)
        d = tmp_path / mode
        d.mkdir()
        chain, w = wallet_worker(d, ["m12"], chain=chain, checks={"m12": {"projection_tip": mode}})
        advance(w)
        m = w.checks["m12"].last[ORACLE]
        runs[mode] = (float(m["runway_trailing_days"]), float(m["runway_base_fee_days"]),
                      [s for s, k, _ in alerts(w) if k == f"{ORACLE}:runway"])
    assert 15.9 < runs["none"][0] == runs["observed"][0] < 16.1
    assert 32 < runs["none"][1] < 34 and 10.5 < runs["observed"][1] < 11.5
    assert runs["none"][2] == [] and runs["observed"][2] == ["warn"]


def test_m12_empty_tip_sample_uses_the_configured_fallback_never_zero(tmp_path):
    """Coordinator condition (4): no verified transaction in the window → the fallback tip, flagged."""
    chain = WalletChain(start_block=100_000)
    chain.set_balance(ORACLE, eth(1))
    chain.set_balance(LIQUIDATOR, eth(1))
    chain.mine()
    chain, w = wallet_worker(tmp_path, ["m12"], chain=chain, checks={"m12": {"fallback_tip_wei": gwei(2)}})
    advance(w)
    m = w.checks["m12"].last[ORACLE]
    assert m["avg_priority_fee_wei"] == gwei(2) and m["tip_source"].startswith("fallback_tip_wei")
    assert m["runway_base_fee_days"] is None  # no gas used in the window: nothing to project
    with pytest.raises(SystemExit, match="fallback_tip_wei is required"):
        wallet_worker(tmp_path / "x", ["m12"], checks={"m12": {"fallback_tip_wei": 0}})


def test_m12_base_fee_is_the_mean_of_the_last_base_fee_blocks(tmp_path):
    """A single-block spike does not move the "current base fee" (300-block mean) much."""
    chain, w, _ = setup(tmp_path)
    chain.set_base_fee(gwei(100))
    chain.mine()
    chain.set_base_fee(gwei(1.15))
    tick(w, chain)
    assert alerts(w) == []
    assert int(evidence(w, f"{ORACLE}:runway")["base_fee_wei"]) == (299 * gwei(1.15) + gwei(100)) // 300


# ---------------------------------------------------------------- V2x.2: thresholds from the chain / config


def test_m12_thresholds_from_chain_base_fee_change_changes_the_alert(tmp_path):
    chain, w, _ = setup(tmp_path)
    advance(w)
    assert alerts(w) == []
    chain.set_base_fee(gwei(5))  # the chain changes; no code or config change
    chain.mine(300)
    w.clock.advance(301)
    advance(w)
    chain.set_base_fee(gwei(1.15))
    chain.mine(300)
    w.clock.advance(301)
    advance(w)
    assert alerts(w) == [("page", f"{ORACLE}:runway", "open"), ("info", f"{ORACLE}:runway", "resolved")]


def test_m12_thresholds_from_chain_receipts_set_the_spend(tmp_path):
    """The spend is gasUsed × effectiveGasPrice of the receipts: one costly transaction on the chain raises the
    daily spend over 0.04 ETH."""
    chain, w, _ = setup(tmp_path, balance=eth(10))
    advance(w)
    chain.send(ORACLE, PROXY, gas_used=1_000_000, gas_price=gwei(40))  # 0.04 ETH
    tick(w, chain)
    assert alerts(w) == [("warn", f"{ORACLE}:daily-spend", "open")]
    assert evidence(w, f"{ORACLE}:daily-spend")["window_txs"] == "15"


def test_m12_thresholds_come_from_config_not_code(tmp_path):
    chain, w, _ = setup(tmp_path, checks={"oracle_balance_page_wei": eth(0.5), "oracle_runway_warn_days": 30})
    advance(w)
    assert alerts(w) == [("page", f"{ORACLE}:balance", "open"), ("warn", f"{ORACLE}:runway", "open")]


def test_m12_required_wallet_addresses_raise_at_construction(tmp_path):
    with pytest.raises(SystemExit, match="checks.m12.oracle_wallet is required"):
        wallet_worker(tmp_path, ["m12"], checks={"m12": {"oracle_wallet": None}})
    (tmp_path / "b").mkdir()
    with pytest.raises(SystemExit, match="liquidator_wallet is required"):
        wallet_worker(tmp_path / "b", ["m12"], checks={"m12": {"liquidator_wallet": "0x1234"}})


# ---------------------------------------------------------------- V2x.3: restart


def test_m12_restart_does_not_duplicate_the_incident_or_rebackfill(tmp_path):
    chain, w, _ = setup(tmp_path, balance=eth(0.1))
    advance(w)
    assert alerts(w) == [("page", f"{ORACLE}:balance", "open"), ("page", f"{ORACLE}:runway", "open")]
    chain.mine()
    _, w2 = wallet_worker(tmp_path, ["m12"], chain=chain, start_block=w.cfg.start_block,
                          activation_block=w.cfg.activation_block, clock=w.clock)
    w2.clock.advance(600)
    advance(w2)
    w2.clock.advance(600)
    tick(w2, chain)
    assert alerts(w2) == alerts(w) and len(outbox(w2, "kind != 'health'")) == 2
    assert w2.bs.calls == []  # synced state is durable: no new transaction, no Blockscout call
    w2.clock.advance(21_600)
    tick(w2, chain)
    assert [r["kind"] for r in outbox(w2, "severity='page'")] == ["open", "open", "realert", "realert"]


# ---------------------------------------------------------------- UNKNOWN, never clean


def test_m12_blockscout_down_is_unknown_not_clean_and_balance_still_alerts(tmp_path):
    chain, w, _ = setup(tmp_path, balance=eth(0.1))
    w.bs.down = OSError("connection refused")
    advance(w)
    assert alerts(w) == [("page", f"{ORACLE}:balance", "open")]  # no runway verdict either way
    (h,) = health(w)
    assert "cannot measure the spend" in h["text"] and "connection refused" in h["text"]
    assert w.db.conn.execute("SELECT COUNT(*) FROM m12_sync").fetchone()[0] == 0
    w.bs.down = None
    tick(w, chain)
    assert alerts(w)[-1] == ("page", f"{ORACLE}:runway", "open")


def test_m12_unknown_health_waits_for_the_grace_period(tmp_path):
    """An indexer lag of one run stays quiet; an UNKNOWN that lasts unknown_grace_s is reported (and the state
    clears when the spend is measurable again)."""
    chain, w, _ = setup(tmp_path, checks={"unknown_grace_s": 900})
    w.bs.down = OSError("lag")
    advance(w)
    tick(w, chain)
    tick(w, chain)
    assert health(w) == []
    tick(w, chain)
    assert len(health(w)) == 1 and "since 903 s" in health(w)[0]["text"]
    w.bs.down = None
    tick(w, chain)
    assert w.db.meta(f"check:m12:unknown_since:{ORACLE}") is None
    w.bs.down = OSError("lag again")
    chain.send(ORACLE, PROXY)
    tick(w, chain)
    assert len(health(w)) == 1


def test_m12_unknown_keeps_an_open_runway_incident_open(tmp_path):
    chain, w, _ = setup(tmp_path, balance=eth(0.17825))
    advance(w)
    assert open_incidents(w) == {f"{ORACLE}:runway": "warn"}
    chain.send(ORACLE, PROXY, gas_used=150_000)
    w.bs.down = ValueError("bad json")
    chain.set_balance(ORACLE, eth(3))
    tick(w, chain)
    assert open_incidents(w) == {f"{ORACLE}:runway": "warn"} and len(health(w)) == 1


def test_m12_indexer_gap_is_unknown_not_clean(tmp_path):
    chain, w, hashes = setup(tmp_path, balance=eth(0.17825))
    w.bs.hide = {hashes[7]}
    advance(w)
    assert alerts(w) == [] and "missing [7]" in health(w)[0]["text"]
    w.bs.hide = set()
    tick(w, chain)
    assert alerts(w) == [("warn", f"{ORACLE}:runway", "open")]


def test_m12_empty_list_with_a_nonce_is_unknown(tmp_path):
    chain, w, hashes = setup(tmp_path, balance=eth(0.17825))
    w.bs.hide = set(hashes)
    advance(w)
    assert alerts(w) == [] and "no verified transaction with nonce 15" in health(w)[0]["text"]


def test_m12_untrusted_candidates_are_verified_on_chain(tmp_path):
    """Forged items (unknown hash, another sender's transaction listed as the wallet's, a fake block number above
    the window) are ignored; the verified set is still complete and the spend exact."""
    chain, w, hashes = setup(tmp_path, balance=eth(0.17825))
    other = chain.send(LIQUIDATOR, PROXY, gas_used=10_000_000)
    chain.mine()
    w.bs.forged = [
        {"hash": "0x" + "ab" * 32, "block_number": chain.sealed - 5, "position": 0, "nonce": 3,
         "from": {"hash": ORACLE}, "fee": {"value": str(eth(5))}},
        {"hash": other, "block_number": chain.sealed - 1, "position": 0, "nonce": 3, "from": {"hash": ORACLE},
         "fee": {"value": str(eth(5))}},
    ]
    advance(w)
    assert alerts(w) == [("warn", f"{ORACLE}:runway", "open")] and health(w) == []
    assert evidence(w, f"{ORACLE}:runway")["window_txs"] == "14"


def test_m12_forged_early_stop_is_unknown(tmp_path):
    """A forged first-page item claiming a block below the window stops the pagination early; the nonce proof
    catches the transactions that were never listed."""
    chain, w, hashes = setup(tmp_path, balance=eth(0.17825), page_size=5)
    w.bs.prepend = [{"hash": "0x" + "cd" * 32, "block_number": 10, "position": 0, "nonce": 0,
                     "from": {"hash": ORACLE}}]
    advance(w)
    assert alerts(w) == [] and "nonce 11" in health(w)[0]["text"] and len(w.bs.calls) == 1


def test_m12_pagination_and_page_budget(tmp_path):
    chain, w, _ = setup(tmp_path, balance=eth(0.17825), page_size=5, checks={"max_pages": 2})
    advance(w)
    assert alerts(w) == [] and "more than 2 Blockscout pages" in health(w)[0]["text"]
    w.checks["m12"].config["max_pages"] = 4  # 16 transactions at 5 per page: the 4th page reaches the boundary
    tick(w, chain)
    assert alerts(w) == [("warn", f"{ORACLE}:runway", "open")]


def test_m12_empty_rpc_result_raises(tmp_path):
    chain, w, _ = setup(tmp_path)
    chain.empty_methods = {"eth_getTransactionCount"}
    with pytest.raises(RpcError, match="eth_getTransactionCount"):
        advance(w)
    chain.empty_methods = {"eth_getBalance"}
    w.clock.advance(301)
    with pytest.raises(RpcError):
        advance(w)
    chain.empty_methods = {"eth_feeHistory"}
    w.clock.advance(301)
    with pytest.raises(RpcError, match="eth_feeHistory"):
        advance(w)
    assert alerts(w) == [] and w.db.conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 0


def test_m12_rpc_error_raises(tmp_path):
    chain, w, _ = setup(tmp_path)
    chain.fail_methods = {"eth_getBalance"}
    with pytest.raises(RpcError):
        advance(w)


# ---------------------------------------------------------------- sync: first sweep, incremental, ordering, outage


def test_m12_first_sweep_backfills_the_whole_window_with_a_nonce_proof(tmp_path):
    chain, w, hashes = setup(tmp_path, older=3)
    advance(w)
    rows = w.db.conn.execute("SELECT nonce FROM m12_tx ORDER BY nonce").fetchall()
    assert [r[0] for r in rows] == list(range(3, 17))  # the 3 older ones proved the boundary, not stored
    sync = w.db.conn.execute("SELECT synced_block, next_nonce, history_from FROM m12_sync").fetchone()
    assert tuple(sync) == (chain.sealed, 17, chain.sealed - WINDOW)


def test_m12_backfill_without_the_boundary_transaction_is_unknown(tmp_path):
    chain, w, hashes = setup(tmp_path, older=2)
    w.bs.hide = {hashes[1]}  # nonce 1, just before the window
    advance(w)
    assert "no verified transaction with nonce 1" in health(w)[0]["text"]


def test_m12_incremental_sync_reads_one_page(tmp_path):
    chain, w, _ = setup(tmp_path)
    advance(w)
    n = len(w.bs.calls)
    chain.send(ORACLE, PROXY, gas_used=150_000)
    chain.send(ORACLE, PROXY, gas_used=150_000)  # same block, two transactions: nonce order inside the block
    tick(w, chain)
    assert len(w.bs.calls) == n + 1
    assert w.db.conn.execute("SELECT COUNT(*) FROM m12_tx WHERE block=?", (chain.sealed,)).fetchone()[0] == 2
    tick(w, chain)
    assert len(w.bs.calls) == n + 1  # nonce unchanged: no listing needed


def test_m12_outage_longer_than_the_window_rebackfills(tmp_path):
    chain, w, _ = setup(tmp_path)
    advance(w)
    spend_history(chain, days=8, older=0, gas_per_day=31_000_000)
    chain.mine()
    w.clock.advance(301)
    advance(w)
    sync = w.db.conn.execute("SELECT synced_block, history_from FROM m12_sync").fetchone()
    assert tuple(sync) == (chain.sealed, chain.sealed - WINDOW)
    ev = evidence(w, f"{ORACLE}:runway")
    assert ev["window_txs"] == "14" and ev["avg_daily_spend_wei"] == f"{31_000_000 * gwei(1.15):.6g}"


def test_m12_window_drops_old_transactions(tmp_path):
    chain, w, _ = setup(tmp_path, balance=eth(10), gas_per_day=40_000_000)
    advance(w)
    assert open_incidents(w) == {f"{ORACLE}:daily-spend": "warn"}
    chain.mine(DAY)
    tick(w, chain)
    assert open_incidents(w) == {}  # no spend in the last 24 h
    chain.mine(WINDOW)
    tick(w, chain)
    assert w.db.conn.execute("SELECT COUNT(*) FROM m12_tx").fetchone()[0] == 0


# ---------------------------------------------------------------- reorgs (README §6.1, §6.2)


def _spend_warn_then_reorg(tmp_path, **worker):
    chain, w, _ = setup(tmp_path, balance=eth(10), worker=worker)
    advance(w)
    tick(w, chain, blocks=5)  # a recorded checkpoint below the coming fork
    fork = chain.number
    chain.send(ORACLE, PROXY, gas_used=1_000_000, gas_price=gwei(40))  # 0.04 ETH: daily spend over the line
    return chain, w, fork


def test_m12_reorg_depth_1_removing_the_costly_tx_resolves(tmp_path):
    chain, w, fork = _spend_warn_then_reorg(tmp_path)
    tick(w, chain)
    assert open_incidents(w) == {f"{ORACLE}:daily-spend": "warn"}
    chain.reorg(fork)
    tick(w, chain)
    assert open_incidents(w) == {}
    assert alerts(w) == [("warn", f"{ORACLE}:daily-spend", "open"), ("info", f"{ORACLE}:daily-spend", "resolved")]
    assert w.db.conn.execute("SELECT COUNT(*) FROM m12_tx WHERE block >= ?", (fork,)).fetchone()[0] == 0
    assert w.db.conn.execute("SELECT next_nonce FROM m12_sync").fetchone()[0] == 16
    w.clock.advance(90_000)
    tick(w, chain)
    assert len(alerts(w)) == 2 and w.db.meta("reorg:m12") is None


def test_m12_reorg_depth_n_beyond_confirmations_re_syncs_the_new_chain(tmp_path):
    chain, w, fork = _spend_warn_then_reorg(tmp_path, confirmations=2)
    tick(w, chain, blocks=6)
    assert open_incidents(w) == {f"{ORACLE}:daily-spend": "warn"}
    chain.reorg(fork)  # 4 blocks below the checkpoint, deeper than the 2-block window; a cheap tx replaces it
    chain.mine(2)
    chain.send(ORACLE, PROXY, gas_used=150_000)
    tick(w, chain, blocks=8)
    assert open_incidents(w) == {}
    rows = w.db.conn.execute("SELECT nonce, gas_used FROM m12_tx WHERE nonce = 16").fetchall()
    assert [tuple(r) for r in rows] == [(16, "150000")]


def test_m12_reorg_marker_survives_a_crash_before_the_run(tmp_path):
    chain, w, fork = _spend_warn_then_reorg(tmp_path)
    tick(w, chain)
    chain.reorg(fork)
    chain.mine()
    w.check_reorg()  # rewound and committed; the process dies before the check runs
    assert w.db.meta("reorg:m12") is not None
    _, w2 = wallet_worker(tmp_path, ["m12"], chain=chain, start_block=w.cfg.start_block,
                          activation_block=w.cfg.activation_block, clock=w.clock)
    advance(w2)
    assert open_incidents(w2) == {} and w2.db.meta("reorg:m12") is None
    assert [k for _, k, kind in alerts(w2) if kind == "resolved"] == [f"{ORACLE}:daily-spend"]


def test_m12_reorg_run_that_cannot_measure_raises_and_keeps_the_incident(tmp_path):
    chain, w, fork = _spend_warn_then_reorg(tmp_path)
    tick(w, chain)
    chain.reorg(fork)
    chain.send(ORACLE, PROXY, gas_used=150_000)  # the new chain has another tx: the re-sync needs the listing
    w.bs.down = OSError("down")
    tick(w, chain)
    assert open_incidents(w) == {f"{ORACLE}:daily-spend": "warn"} and w.db.meta("reorg:m12") is not None
    assert any("Check m12 raised RuntimeError" in r["text"] for r in outbox(w, "kind='health'"))
    w.clock.advance(86_401)
    tick(w, chain)
    assert [r["kind"] for r in outbox(w, "severity='warn'")] == ["open"]  # not re-alerted while pending
    w.bs.down = None
    tick(w, chain)
    assert open_incidents(w) == {} and w.db.meta("reorg:m12") is None


def test_m12_reorg_keeps_a_condition_the_new_chain_still_has(tmp_path):
    chain, w, _ = setup(tmp_path, balance=eth(0.1))
    advance(w)
    fork = chain.number
    chain.mine(3)
    tick(w, chain)
    chain.reorg(fork)
    tick(w, chain)
    assert open_incidents(w) == {f"{ORACLE}:balance": "page", f"{ORACLE}:runway": "page"}
    assert [k for _, _, k in alerts(w)] == ["open", "open"]


def test_m12_digest_lines(tmp_path):
    chain, w, _ = setup(tmp_path)
    advance(w)
    lines = w.checks["m12"].digest(None)
    assert lines[0].startswith(f"M12 oracle slot 4 + EB updater {ORACLE}: 0.3454 ETH") and "19.4 d trailing" in lines[0]
    assert lines[1].startswith(f"M12 liquidator {LIQUIDATOR}: 1.0000 ETH")


def test_m12_fake_blockscout_matches_the_live_item_shape():
    """The parsed fields are the ones measured on the live API (attempt 1): hash, block_number, from.hash."""
    chain = WalletChain()
    chain.mine()
    chain.send(ORACLE, PROXY)
    chain.mine()
    page = FakeBlockscout(chain)(f"https://x/api/v2/addresses/{ORACLE}/transactions?filter=from")
    (item,) = page["items"]
    assert {"hash", "block_number", "nonce", "from", "fee"} <= set(item) and page["next_page_params"] is None


# ---------------------------------------------------------------- repair 1: QA attempt 1 F1 (README §6.2 item 4)
# A reorg run must establish the spend on the new chain or raise, whatever ctx.reorg_keys holds: the spend
# evaluations it persisted (benign ones too), open incidents last seen at or below the ancestor and keys resolved
# on the deleted blocks all depend on it.


def _reorg_marker_kept_while_down(w, chain):
    assert w.db.meta("reorg:m12") is not None, "an UNKNOWN reorg run committed and cleared the retry marker"
    assert any("Check m12 raised RuntimeError" in r["text"] and "after the reorg" in r["text"]
               for r in outbox(w, "kind='health'"))


def test_m12_qa1_f1_reorg_unknown_with_an_older_open_runway_keeps_the_marker(tmp_path):
    """QA attempt 1 Appendix A: the open runway incident was last seen below the ancestor (not in reorg_keys)."""
    chain, w, _ = setup(tmp_path, balance=eth(0.18))
    advance(w)
    assert open_incidents(w) == {f"{ORACLE}:runway": "warn"}
    chain.mine(3)
    advance(w)  # a newer checkpoint without a due m12 run: the incident's last_seen stays below the fork
    fork = chain.number
    chain.mine(3)
    advance(w)
    chain.reorg(fork)
    chain.send(ORACLE, PROXY, gas_used=160_000)  # the replacement chain needs the listing
    w.bs.down = OSError("replacement-chain API outage")
    chain.mine()
    advance(w)
    _reorg_marker_kept_while_down(w, chain)
    assert open_incidents(w) == {f"{ORACLE}:runway": "warn"}
    tick(w, chain)  # retried every step, still raising
    assert w.db.meta("reorg:m12") is not None
    w.bs.down = None
    tick(w, chain)
    assert w.db.meta("reorg:m12") is None and open_incidents(w) == {f"{ORACLE}:runway": "warn"}
    assert w.db.conn.execute("SELECT next_nonce FROM m12_sync WHERE wallet=?", (ORACLE,)).fetchone()[0] == 17


def test_m12_qa1_f1_reorg_unknown_with_benign_persisted_spend_keeps_the_marker(tmp_path):
    """QA attempt 1 Appendix B: no open incident at all; the persisted (benign) spend must still be re-measured."""
    chain, w, _ = setup(tmp_path, balance=eth(10))
    advance(w)
    tick(w, chain, blocks=3)
    fork = chain.number
    chain.send(ORACLE, PROXY, gas_used=150_000)
    tick(w, chain)
    assert open_incidents(w) == {}
    chain.reorg(fork)
    chain.send(ORACLE, PROXY, gas_used=160_000)
    w.bs.down = OSError("QA API outage on replacement chain")
    tick(w, chain)
    _reorg_marker_kept_while_down(w, chain)
    w.bs.down = None
    tick(w, chain)
    assert w.db.meta("reorg:m12") is None
    rows = w.db.conn.execute("SELECT nonce, gas_used FROM m12_tx WHERE nonce = 16").fetchall()
    assert [tuple(r) for r in rows] == [(16, "160000")]


def test_m12_reorg_removing_the_evidence_of_a_resolved_runway_reopens_it_and_unknown_waits(tmp_path):
    """The runway was resolved on a deleted block (a top-up); the new chain lacks the top-up. Blockscout down in the
    reorg run: raise (nothing resolved or kept on deleted evidence); once it is back: the runway warns again."""
    chain, w, _ = setup(tmp_path, balance=eth(0.17825))
    advance(w)
    tick(w, chain, blocks=2)
    fork = chain.number
    chain.set_balance(ORACLE, eth(3))
    tick(w, chain)
    assert open_incidents(w) == {} and alerts(w)[-1] == ("info", f"{ORACLE}:runway", "resolved")
    chain.reorg(fork)
    chain.send(ORACLE, PROXY, gas_used=150_000)
    w.bs.down = OSError("down")
    tick(w, chain)
    _reorg_marker_kept_while_down(w, chain)
    assert open_incidents(w) == {}
    w.bs.down = None
    tick(w, chain)
    assert w.db.meta("reorg:m12") is None and open_incidents(w) == {f"{ORACLE}:runway": "warn"}
    assert [k for _, key, k in alerts(w) if key == f"{ORACLE}:runway"] == ["open", "resolved", "open"]


def test_m12_reorg_unknown_marker_survives_a_restart_and_the_new_process_still_raises(tmp_path):
    chain, w, _ = setup(tmp_path, balance=eth(10))
    advance(w)
    tick(w, chain, blocks=3)
    fork = chain.number
    chain.send(ORACLE, PROXY, gas_used=150_000)
    tick(w, chain)
    chain.reorg(fork)
    chain.send(ORACLE, PROXY, gas_used=1_000_000, gas_price=gwei(40))  # new chain: daily spend over the line
    chain.mine()
    w.check_reorg()  # rewound and committed; the process dies before the check runs
    _, w2 = wallet_worker(tmp_path, ["m12"], chain=chain, start_block=w.cfg.start_block,
                          activation_block=w.cfg.activation_block, clock=w.clock,
                          checks={"m12": {"unknown_grace_s": 0}})
    w2.bs.down = OSError("down after the restart")
    advance(w2)
    assert w2.db.meta("reorg:m12") is not None and open_incidents(w2) == {}
    w2.bs.down = None
    tick(w2, chain)
    assert w2.db.meta("reorg:m12") is None and open_incidents(w2) == {f"{ORACLE}:daily-spend": "warn"}


def test_m12_reorg_with_no_transaction_on_the_new_chain_needs_no_listing(tmp_path):
    """Positive proof without the API: the nonce at the checkpoint equals the nonce re-read at the ancestor, so the
    new chain's (A, checkpoint] holds no transaction of the wallet; the run commits with Blockscout down."""
    chain, w, fork = _spend_warn_then_reorg(tmp_path)
    tick(w, chain)
    assert open_incidents(w) == {f"{ORACLE}:daily-spend": "warn"}
    chain.reorg(fork)
    w.bs.down = OSError("down")
    calls = len(w.bs.calls)
    tick(w, chain)
    assert w.db.meta("reorg:m12") is None and open_incidents(w) == {} and len(w.bs.calls) == calls
    assert alerts(w)[-1] == ("info", f"{ORACLE}:daily-spend", "resolved")


def test_m12_reorg_run_with_non_increasing_timestamps_raises(tmp_path):
    """A SpendUnknown from the metrics (not the listing) is the same: the reorg run raises."""
    chain, w, fork = _spend_warn_then_reorg(tmp_path)
    tick(w, chain)
    chain.reorg(fork)
    chain.mine()
    ts = chain.timestamp
    chain.timestamp = lambda n: ts(0)
    advance(w)
    assert w.db.meta("reorg:m12") is not None and open_incidents(w) == {f"{ORACLE}:daily-spend": "warn"}
    chain.timestamp = ts
    tick(w, chain)
    assert w.db.meta("reorg:m12") is None and open_incidents(w) == {}


@pytest.mark.parametrize("method", ["eth_getBalance", "eth_getTransactionCount", "eth_getBlockByNumber"])
def test_m12_short_batch_response_raises_never_a_partial_evaluation(tmp_path, method):
    """(g) partial responses: a batch answered with fewer results than calls is an RPC error."""
    chain, w, _ = setup(tmp_path, balance=eth(0.1))
    check, batch, run, armed = w.checks["m12"], w.rpc.batch, w.checks["m12"].run, []

    def short(calls):  # only inside the check's run (the worker's own block-hash batches stay whole)
        out = batch(calls)
        return out[:-1] if armed and calls and calls[0][0] == method else out

    def armed_run(ctx, block):
        armed.append(1)
        try:
            return run(ctx, block)
        finally:
            armed.clear()

    w.rpc.batch, check.run = short, armed_run
    with pytest.raises(RpcError, match="results for"):
        advance(w)
    assert open_incidents(w) == {} and w.db.conn.execute("SELECT COUNT(*) FROM m12_sync").fetchone()[0] == 0


def test_m12_two_transactions_in_one_block_are_both_counted(tmp_path):
    """(e) same-block ordering (QA independent case): both nonces, both receipts."""
    chain, w, _ = setup(tmp_path, balance=eth(10))
    advance(w)
    before = w.db.conn.execute("SELECT COUNT(*) FROM m12_tx").fetchone()[0]
    chain.send(ORACLE, PROXY, gas_used=100)
    chain.send(ORACLE, PROXY, gas_used=200)
    tick(w, chain)
    rows = w.db.conn.execute("SELECT gas_used FROM m12_tx ORDER BY nonce DESC LIMIT 2").fetchall()
    assert [r[0] for r in rows] == ["200", "100"]
    assert w.db.conn.execute("SELECT COUNT(*) FROM m12_tx").fetchone()[0] == before + 2


def test_m12_zero_transactions_is_proven_by_a_zero_nonce(tmp_path):
    """(a) empty listing with a zero nonce: a proven zero spend (no runway), the configured tip flagged; silent."""
    chain = WalletChain(start_block=100_000)
    chain.set_balance(ORACLE, eth(1))
    chain.set_balance(LIQUIDATOR, eth(1))
    chain.mine()
    chain, w = wallet_worker(tmp_path, ["m12"], chain=chain)
    advance(w)
    m = w.checks["m12"].last[ORACLE]
    assert m["window_txs"] == 0 and m["runway_trailing_days"] is None and m["runway_base_fee_days"] is None
    assert m["avg_priority_fee_wei"] == gwei(1) and m["tip_source"].startswith("fallback_tip_wei")
    assert outbox(w) == []


def test_m12_null_transaction_lookup_is_unknown_and_balances_still_alert(tmp_path):
    """(a) the RPC answers null for the listed hashes: they are not verified, so the set is incomplete (UNKNOWN,
    never "no spend"); balance keys are still evaluated."""
    chain, w, _ = setup(tmp_path, balance=eth(0.1))
    chain.empty_methods.add("eth_getTransactionByHash")
    advance(w)
    assert open_incidents(w) == {f"{ORACLE}:balance": "page"}
    assert "UNKNOWN" in w.checks["m12"].last[ORACLE]["spend"]


def test_m12_duplicated_listing_items_are_counted_once(tmp_path):
    chain, w, _ = setup(tmp_path)
    get = w.bs

    def duplicated(url):
        page = get(url)
        page["items"] *= 2
        return page

    w.checks["m12"].http_get = duplicated
    advance(w)
    assert w.checks["m12"].last[ORACLE]["window_txs"] == 14 and alerts(w) == []


def test_m12_reorg_below_the_backfill_start_re_backfills_the_whole_window(tmp_path):
    """(c) the rewind branch for a wallet whose history starts above the ancestor (only reachable with a window
    shorter than the reorg): its rows and sync state are dropped and the window is backfilled on the new chain."""
    chain, w, _ = setup(tmp_path, balance=eth(10), checks={"window_blocks": 6, "day_blocks": 3})
    advance(w)
    tick(w, chain, blocks=10)  # synced below the window's start: a fresh backfill, history_from = checkpoint - 6
    cp = chain.sealed
    assert w.db.conn.execute("SELECT history_from FROM m12_sync").fetchone()[0] == cp - 6
    fork = cp - 6  # the ancestor (the previous checkpoint, cp - 10) is below history_from
    chain.reorg(fork)
    chain.send(ORACLE, PROXY, gas_used=200_000)
    tick(w, chain, blocks=2)
    assert w.db.meta("reorg:m12") is None
    rows = w.db.conn.execute("SELECT nonce, gas_used FROM m12_tx ORDER BY nonce").fetchall()
    assert [tuple(r) for r in rows] == [(15, "7750000"), (16, "200000")]  # the history's last one is in the window
    sync = w.db.conn.execute("SELECT synced_block, next_nonce, history_from FROM m12_sync").fetchone()
    assert tuple(sync) == (chain.sealed, 17, chain.sealed - 6)


# ---------------------------------------------------------------- repair 1: deterministic adversarial sweep
# Seeded cases (40): a 7-day spend history, then random oracle transactions (gas, price, several in one block),
# balance and base-fee changes, liquidator balance changes around its thresholds; Blockscout outages, indexing lag
# and a hidden transaction (nonce gap) in random runs, restarts, runs that are and are not due. Then one reorg
# 1..24 blocks below the checkpoint (beyond the confirmation window) whose replacement chain re-includes some of
# the deleted transactions, adds others and is shorter or longer, sometimes with a crash between its detection
# and the run and a Blockscout outage in the reorg run. Oracle: a fresh worker on the final chain at the same
# checkpoint (the spend of the window is a function of the chain). Invariants: no open incident without supporting
# evidence and no dropped alert for surviving evidence (open (key, severity) = the oracle's, after the reorg run
# commits and at the end); no resolve without positive proof (a runway/daily-spend resolution only from a run
# that measured the spend; an UNKNOWN reorg run keeps the marker exactly when the new chain needs the listing; the
# §6.2 absence rule never fires: every key is evaluated or the run raises); no window anchored on stale data (the
# measurement equals the oracle's: window, last 24 h, base fee, tip, both runways); no event lost between sweeps
# (m12_tx = the canonical transactions of the window, m12_sync at the checkpoint with its nonce); healthy.

REORG_TEXT = "Resolved because its evidence left the canonical chain"
MEASURED = ("avg_daily_spend_wei", "day_spend_wei", "window_txs", "window_fees_wei", "window_gas", "base_fee_wei",
            "avg_priority_fee_wei", "runway_trailing_days", "runway_base_fee_days", "balance_wei")


class M12Sweep:
    def __init__(self, tmp_path, seed, confirmations):
        rng = self.rng = random.Random(f"cp2c-m12:{seed}:{confirmations}")
        self.tmp, self.c = tmp_path, confirmations
        chain = self.chain = WalletChain(start_block=100_000)
        chain.set_balance(ORACLE, eth(5))
        chain.set_balance(LIQUIDATOR, eth(rng.choice((0.04, 0.1, 0.3))))
        chain.mine()
        spend_history(chain, gas_per_day=rng.choice((8_000_000, 15_500_000, 31_000_000)))
        chain.set_balance(ORACLE, eth(rng.choice((0.12, 0.17825, 0.2, 0.3454, 1.0))))
        chain.mine(1 + confirmations)
        self.start = chain.sealed - confirmations
        self.clock = None
        self.knobs = {}
        self.w = self.worker(tmp_path)
        self.wstep()
        self.n_oracles = 0

    def worker(self, path, start=None, clock=True):
        start = self.start if start is None else start
        _, w = wallet_worker(path, ["m12"], chain=self.chain, start_block=start, activation_block=start,
                             confirmations=self.c, checks={"m12": {"unknown_grace_s": 0}},
                             **({"clock": self.clock} if self.clock and clock else {}))
        if clock:
            self.clock = w.clock
        return w

    def events(self, n, sends=None):
        rng, chain = self.rng, self.chain
        for _ in range(n):
            r = rng.random()
            if r < 0.35:
                gas, price = rng.choice((60_000, 150_000, 900_000)), gwei(rng.choice((0.5, 1.15, 5, 40)))
                chain.send(ORACLE, PROXY, gas_used=gas, gas_price=price)
                if sends is not None:
                    sends.append((gas, price))
            elif r < 0.45:
                chain.set_balance(ORACLE, eth(rng.choice((0.12, 0.17825, 0.2, 0.3454, 1.0))))
            elif r < 0.52:
                chain.set_balance(LIQUIDATOR, eth(rng.choice((0.04, 0.1, 0.3))))
            elif r < 0.6:
                chain.set_base_fee(gwei(rng.choice((0.3, 1.15, 5))))

    def wstep(self, seconds=301, knobs=None):
        """One worker step with Blockscout knobs (down / lag / hide); asserts that no spend resolution comes
        from a run that did not measure the spend."""
        self.knobs = knobs or {}
        bs = self.w.bs
        bs.down = OSError("sweep outage") if self.knobs.get("down") else None
        bs.lag = self.knobs.get("lag", 0)
        bs.hide = set(self.knobs.get("hide", ()))
        before = self.w.db.conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM outbox").fetchone()[0]
        self.clock.advance(seconds)
        self.w.step()
        keys = {r[0]: r[1] for r in self.w.db.conn.execute("SELECT id, key FROM incidents")}
        for r in outbox(self.w, f"rowid > {before} AND kind = 'resolved'"):
            if keys[r["incident_id"]].split(":")[1] in ("runway", "daily-spend"):
                last = self.w.checks["m12"].last[ORACLE]
                assert "spend" not in last and "runway_trailing_days" in last, "spend resolved without a measurement"

    def random_knobs(self):
        rng = self.rng
        r = rng.random()
        if r < 0.15:
            return {"down": True}
        if r < 0.25:
            return {"lag": rng.randint(1, 3)}
        if r < 0.32:
            recent = [h for h, t in self.chain.wtxs.items() if t["from"] == ORACLE][-3:]
            return {"hide": [rng.choice(recent)]}
        return {}

    def oracle(self):
        """A fresh worker on the final chain at the same checkpoint."""
        self.n_oracles += 1
        path = self.tmp / f"oracle-{self.n_oracles}"
        path.mkdir()
        o = self.worker(path, start=self.chain.sealed - self.c, clock=False)
        o.step()
        assert o.checkpoint()["block"] == self.w.checkpoint()["block"]
        return o

    def assert_agrees(self):
        o = self.oracle()
        assert open_incidents(self.w) == open_incidents(o), "open incidents differ from the canonical chain's"
        mine, theirs = self.w.checks["m12"].last[ORACLE], o.checks["m12"].last[ORACLE]
        assert {k: mine.get(k) for k in MEASURED} == {k: theirs.get(k) for k in MEASURED}
        return o

    def canonical_txs(self, lo, hi):
        return {h: t for h, t in self.chain.wtxs.items() if t["from"] == ORACLE and lo < t["block"] <= hi}

    def run(self):
        rng, chain = self.rng, self.chain
        for _ in range(rng.randint(5, 10)):
            self.events(rng.randint(0, 3))
            chain.mine(rng.randint(1, 3))
            if rng.random() < 0.1:
                self.w = self.worker(self.tmp)
            self.wstep(seconds=rng.choice((12, 301, 301)), knobs=self.random_knobs())
        chain.mine()
        self.wstep()
        self.assert_agrees()  # normal operation across UNKNOWN runs: back in agreement once measurable
        fork = chain.number
        deleted: list[tuple[int, int]] = []
        for _ in range(rng.randint(1, 24)):
            self.events(rng.randint(0, 2), sends=deleted)
            chain.mine()
        chain.mine(self.c)
        self.wstep()
        self.depth = self.w.checkpoint()["block"] - fork + 1
        self.before_reorg_open = open_incidents(self.w)
        pre = self.w.db.conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM outbox").fetchone()[0]
        chain.reorg(fork)
        for gas, price in deleted:  # some re-included (same or a later block), with their nonces in order
            if rng.random() < 0.5:
                if rng.random() < 0.5:
                    chain.mine()
                chain.send(ORACLE, PROXY, gas_used=gas, gas_price=price)
        self.events(rng.randint(0, 2))
        chain.mine(rng.randint(1, 4))
        self.w.check_reorg()
        ancestor = self.w.db.meta("reorg:m12")
        assert ancestor is not None and ancestor < fork
        if rng.random() < 0.3:  # crash between the reorg's detection and the check's run
            self.w = self.worker(self.tmp)
        if rng.random() < 0.5:  # Blockscout down in the reorg run
            self.wstep(knobs={"down": True})
            cp = self.w.checkpoint()["block"]
            needs_listing = bool(self.canonical_txs(ancestor, cp))
            assert (self.w.db.meta("reorg:m12") is not None) == needs_listing
            if needs_listing:
                assert open_incidents(self.w) == self.before_reorg_open
        committed = self.w.db.meta("reorg:m12") is None
        if not committed:
            chain.mine()
            self.wstep()
        assert self.w.db.meta("reorg:m12") is None
        self.assert_agrees()  # the reorg run's evaluation is complete and canonical
        for _ in range(rng.randint(0, 3)):
            self.events(rng.randint(0, 2))
            chain.mine()
            self.wstep(knobs=self.random_knobs())
        chain.mine()
        self.wstep(seconds=86_401)  # warn re-alert cadence: every open incident may re-alert
        return pre


@pytest.mark.parametrize("confirmations", [0, 2])
@pytest.mark.parametrize("seed", range(20))
def test_m12_adversarial_reorg_sweep(tmp_path, seed, confirmations):
    s = M12Sweep(tmp_path, seed, confirmations)
    pre = s.run()
    w, cp = s.w, s.w.checkpoint()["block"]
    assert s.depth >= 1
    o = s.assert_agrees()
    assert not [r for r in outbox(w, "kind='resolved'") if REORG_TEXT in r["text"]], "a key was resolved by absence"
    canon = s.canonical_txs(cp - WINDOW, cp)
    rows = {r[0]: (r[1], r[2], int(r[3]), int(r[4])) for r in
            w.db.conn.execute("SELECT tx_hash, block, block_hash, gas_used, gas_price FROM m12_tx WHERE wallet=?",
                              (ORACLE,))}
    assert rows == {h: (t["block"], s.chain.hash(t["block"]), t["gas_used"], t["gas_price"]) for h, t in canon.items()}
    sync = w.db.conn.execute("SELECT synced_block, next_nonce FROM m12_sync WHERE wallet=?", (ORACLE,)).fetchone()
    assert tuple(sync) == (cp, sum(1 for t in s.chain.wtxs.values() if t["from"] == ORACLE and t["block"] <= cp))
    open_now = set(open_incidents(o))
    keys = {r[0]: r[1] for r in w.db.conn.execute("SELECT id, key FROM incidents")}
    for r in outbox(w, f"rowid > {pre} AND kind IN ('realert', 'escalate')"):
        assert keys[r["incident_id"]] in open_now or r["kind"] == "escalate"
    assert w.degraded() == []
