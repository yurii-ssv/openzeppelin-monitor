"""M12 — operational wallet balances and burn rate (spec §3 M12, D4; plan §7 V2x.*, V2c.7).

Scenario numbers are the spec's measured burn (15.5 Mgas/day ≈ 0.0177 ETH/day at ~1.15 gwei; balance 0.3454 ETH
→ ≈ 19.5 days; at 5 gwei ≈ 4.5 days), carried by the fixtures, never by the check.
"""

from __future__ import annotations

import json
import random
from fractions import Fraction

import pytest
from fixtures_wallets import (
    DAY,
    FUNDER,
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
    chain, w, _ = setup(tmp_path, balance=eth(10), checks={"window_s": 72, "day_s": 36})
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
# Repair 3 (QA attempt 2 (Codex) F1/F2): each case draws a block-timestamp profile (12 s slots, a missed slot every
# 7 or 50 blocks, or a burst of 50-3,000 missed slots somewhere in the history) and some runs (also the reorg run)
# get a partial eth_feeHistory answer (short, shifted, short gas ratios, a missing field, a junk entry). Added
# invariants: the window and the last 24 h equal an INDEPENDENT computation from the chain's timestamps
# ((T - 7 d, T] and (T - 24 h, T] of the checkpoint block; no window anchored on stale data or on block counts); a
# run with a partial fee history raises (errors + 1), sends nothing and changes no incident (no resolve without
# positive proof), and a reorg run keeps its marker; m12_tx = the canonical transactions of (history_from,
# checkpoint] with history_from at or below the window's start (no event lost between sweeps).

REORG_TEXT = "Resolved because its evidence left the canonical chain"
WEEK_S, DAY_S = 604_800, 86_400


def slot_profile(genesis_ts, profile, rng=None):
    """Block timestamps: 12 s slots, a missed slot every k blocks ("every<k>"), or a burst of missed slots
    ("burst": 50-3,000 slots at a block drawn in the history). Strictly increasing, multiples of 12 s."""
    if profile == "12s":
        return lambda n: genesis_ts + 12 * n
    if profile.startswith("every"):
        k = int(profile[5:])
        return lambda n: genesis_ts + 12 * n + 12 * (n // k)
    at, extra = rng.randint(110_000, 170_000), rng.choice((50, 600, 3_000))
    return lambda n: genesis_ts + 12 * n + (12 * extra if n >= at else 0)


def time_window(chain, cp, seconds, wallet=ORACLE):
    """Independent: the wallet's canonical transactions whose block timestamp is in (T - seconds, T], T = the
    checkpoint block's timestamp."""
    t = chain.timestamp(cp)
    return {h: x for h, x in chain.wtxs.items()
            if x["from"] == wallet and x["block"] <= cp and chain.timestamp(x["block"]) > t - seconds}


def fees(txs):
    return sum(x["gas_used"] * x["gas_price"] for x in txs.values())


FEE_FAULTS = {  # partial eth_feeHistory answers (QA attempt 2 (Codex) F2 and its generalisation)
    "short": lambda r: dict(r, baseFeePerGas=r["baseFeePerGas"][-2:]),
    "shift": lambda r: dict(r, oldestBlock=hex(int(r["oldestBlock"], 16) + 1)),
    "ratios": lambda r: dict(r, gasUsedRatio=r["gasUsedRatio"][1:]),
    "missing": lambda r: {"baseFeePerGas": r["baseFeePerGas"]},
    "junk": lambda r: dict(r, baseFeePerGas=r["baseFeePerGas"][:-1] + [None]),
    "long": lambda r: dict(r, baseFeePerGas=r["baseFeePerGas"] + r["baseFeePerGas"][-1:]),
}


def fault_fee_history(chain, mode):
    """Answer eth_feeHistory with FEE_FAULTS[mode] of the real answer (`del chain._handle` restores)."""
    handle = type(chain)._handle.__get__(chain)

    def faulty(method, params):
        res = handle(method, params)
        return FEE_FAULTS[mode](res) if method == "eth_feeHistory" else res

    chain._handle = faulty
MEASURED = ("avg_daily_spend_wei", "day_spend_wei", "window_txs", "window_fees_wei", "window_gas", "base_fee_wei",
            "avg_priority_fee_wei", "runway_trailing_days", "runway_base_fee_days", "balance_wei")


class M12Sweep:
    def __init__(self, tmp_path, seed, confirmations):
        rng = self.rng = random.Random(f"cp2c-m12:{seed}:{confirmations}")
        self.tmp, self.c = tmp_path, confirmations
        chain = self.chain = WalletChain(start_block=100_000)
        self.profile = rng.choice(("12s", "every7", "every50", "burst", "burst"))
        chain.timestamp = slot_profile(chain.genesis_ts, self.profile, rng)
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
        """One worker step with Blockscout knobs (down / lag / hide) and a fee-history fault (feehist); asserts
        that no spend resolution comes from a run that did not measure the spend, and that a run with a partial
        fee history raises without sending or changing anything."""
        self.knobs = knobs or {}
        bs = self.w.bs
        bs.down = OSError("sweep outage") if self.knobs.get("down") else None
        bs.lag = self.knobs.get("lag", 0)
        bs.hide = set(self.knobs.get("hide", ()))
        before = self.w.db.conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM outbox").fetchone()[0]
        fault = self.knobs.get("feehist")
        if fault:
            seconds = max(seconds, 301)  # the run is due
            errors = (self.w.db.meta("run:m12") or {}).get("errors", 0)
            incidents = [tuple(r) for r in self.w.db.conn.execute("SELECT * FROM incidents ORDER BY id")]
            marker = self.w.db.meta("reorg:m12")
            fault_fee_history(self.chain, fault)
        self.clock.advance(seconds)
        try:
            self.w.step()
        finally:
            if fault:
                del self.chain._handle
        if fault:
            assert self.w.db.meta("run:m12")["errors"] == errors + 1, f"partial fee history ({fault}) accepted"
            assert outbox(self.w, f"rowid > {before} AND kind != 'health'") == [], "a run with a partial input sent"
            assert [tuple(r) for r in self.w.db.conn.execute("SELECT * FROM incidents ORDER BY id")] == incidents
            assert self.w.db.meta("reorg:m12") == marker
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
        if r < 0.40:
            return {"feehist": rng.choice(sorted(FEE_FAULTS))}
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
        # independent of the check: the windows by the chain's timestamps at the checkpoint
        cp = self.w.checkpoint()["block"]
        week, day = time_window(self.chain, cp, WEEK_S), time_window(self.chain, cp, DAY_S)
        assert "spend" not in mine, mine["spend"]
        assert (mine["window_txs"], mine["window_fees_wei"], mine["day_fees_wei"]) == (len(week), fees(week), fees(day))
        assert mine["avg_daily_spend_wei"] == Fraction(fees(week) * DAY_S, WEEK_S)
        assert mine["day_spend_wei"] == fees(day) and mine["window_seconds"] == WEEK_S
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
        if rng.random() < 0.3:  # a partial fee history in the reorg run: raises, marker and incidents kept
            self.wstep(knobs={"feehist": rng.choice(sorted(FEE_FAULTS))})
            assert self.w.db.meta("reorg:m12") == ancestor and open_incidents(self.w) == self.before_reorg_open
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
@pytest.mark.parametrize("seed", range(100))
def test_m12_adversarial_reorg_sweep(tmp_path, seed, confirmations):
    s = M12Sweep(tmp_path, seed, confirmations)
    pre = s.run()
    w, cp = s.w, s.w.checkpoint()["block"]
    assert s.depth >= 1
    o = s.assert_agrees()
    assert not [r for r in outbox(w, "kind='resolved'") if REORG_TEXT in r["text"]], "a key was resolved by absence"
    sync = w.db.conn.execute("SELECT synced_block, next_nonce, history_from FROM m12_sync WHERE wallet=?",
                             (ORACLE,)).fetchone()
    assert tuple(sync)[:2] == (cp, sum(1 for t in s.chain.wtxs.values() if t["from"] == ORACLE and t["block"] <= cp))
    window_start = w.checks["m12"].last[ORACLE]["window_from_block"]
    assert sync[2] < window_start  # the stored set covers the window
    canon = s.canonical_txs(sync[2], cp)
    assert set(time_window(s.chain, cp, WEEK_S)) <= set(canon)
    rows = {r[0]: (r[1], r[2], int(r[3]), int(r[4])) for r in
            w.db.conn.execute("SELECT tx_hash, block, block_hash, gas_used, gas_price FROM m12_tx WHERE wallet=?",
                              (ORACLE,))}
    assert rows == {h: (t["block"], s.chain.hash(t["block"]), t["gas_used"], t["gas_price"]) for h, t in canon.items()}
    open_now = set(open_incidents(o))
    keys = {r[0]: r[1] for r in w.db.conn.execute("SELECT id, key FROM incidents")}
    for r in outbox(w, f"rowid > {pre} AND kind IN ('realert', 'escalate')"):
        assert keys[r["incident_id"]] in open_now or r["kind"] == "escalate"
    assert w.degraded() == []


# ---------------------------------------------------------------- repair 2: QA attempt 2 scenarios (Appendix C1, ported verbatim
# except the module prefix; expected outcomes from the spec, D3/D4/D10 and README §6.2)

def _m12_reorg(tmp_path, page_mutator):
    chain, w, _ = setup(tmp_path, balance=eth(10))
    advance(w)
    tick(w, chain, blocks=5)
    fork = chain.number
    chain.send(ORACLE, PROXY, gas_used=1_000_000, gas_price=gwei(40))  # 0.04 ETH daily spend → warn
    tick(w, chain)
    assert open_incidents(w) == {f"{ORACLE}:daily-spend": "warn"}
    chain.reorg(fork)
    chain.send(ORACLE, PROXY, gas_used=150_000)  # the new chain: a cheap transaction instead
    real = w.bs

    def bs(url):
        return page_mutator(real(url))

    w.checks["m12"].http_get = bs
    tick(w, chain)
    return chain, w, real


def test_qa2_m12_reorg_run_with_an_empty_blockscout_page_raises(tmp_path):
    chain, w, real = _m12_reorg(tmp_path, lambda page: {"items": [], "next_page_params": None})
    assert w.db.meta("reorg:m12") is not None
    assert open_incidents(w) == {f"{ORACLE}:daily-spend": "warn"}
    w.checks["m12"].http_get = real
    tick(w, chain)
    assert w.db.meta("reorg:m12") is None and open_incidents(w) == {}


def test_qa2_m12_reorg_run_with_a_partial_page_raises(tmp_path):
    chain, w, real = _m12_reorg(tmp_path, lambda page: dict(page, items=page["items"][1:]))
    assert w.db.meta("reorg:m12") is not None
    assert open_incidents(w) == {f"{ORACLE}:daily-spend": "warn"}


def test_qa2_m12_reorg_run_with_duplicated_items_commits_and_counts_once(tmp_path):
    chain, w, real = _m12_reorg(tmp_path, lambda page: dict(page, items=page["items"] * 2))
    assert w.db.meta("reorg:m12") is None and open_incidents(w) == {}
    rows = w.db.conn.execute("SELECT COUNT(*), COUNT(DISTINCT nonce) FROM m12_tx").fetchone()
    assert rows[0] == rows[1]


def test_qa2_m12_reorg_run_with_a_stale_orphan_item_only_raises(tmp_path):
    """Blockscout still lists the orphaned costly tx (stale index) and not the new chain's tx: the orphan fails
    on-chain verification, the nonce is missing → raise, never 'clean'."""
    chain, w, _ = setup(tmp_path, balance=eth(10))
    advance(w)
    tick(w, chain, blocks=5)
    fork = chain.number
    orphan = chain.send(ORACLE, PROXY, gas_used=1_000_000, gas_price=gwei(40))
    tick(w, chain)
    orphan_item = w.bs.item(chain.wtxs[orphan])
    chain.reorg(fork)
    new = chain.send(ORACLE, PROXY, gas_used=150_000)
    w.bs.hide.add(new)
    w.bs.forged.append(orphan_item)
    tick(w, chain)
    assert w.db.meta("reorg:m12") is not None
    assert open_incidents(w) == {f"{ORACLE}:daily-spend": "warn"}


def test_qa2_m12_same_block_top_up_and_spend_balance_is_end_of_block(tmp_path):
    chain, w, _ = setup(tmp_path, balance=eth(0.149), gas_per_day=1_000_000)
    advance(w)
    assert open_incidents(w).get(f"{ORACLE}:balance") == "page"
    chain.send(FUNDER, ORACLE, gas_used=21_000, value=0)  # noise
    chain.credit(ORACLE, eth(0.5))  # funding and a spend in one block
    chain.send(ORACLE, PROXY, gas_used=150_000)
    tick(w, chain)
    assert f"{ORACLE}:balance" not in open_incidents(w)


def test_qa2_m12_liquidator_page_while_a_reorg_run_waits_on_blockscout(tmp_path):
    """Observation (documented accepted consequence): during a pending M12 reorg run with Blockscout down, balance
    pages are not evaluated; the operator sees check-error health on Telegram and healthy: false."""
    chain, w, real = _m12_reorg(tmp_path, lambda page: (_ for _ in ()).throw(OSError("down")))
    chain.set_balance(LIQUIDATOR, eth(0.01))
    tick(w, chain)
    assert f"{LIQUIDATOR}:balance" not in open_incidents(w)
    hs = [r for r in outbox(w, "kind='health'") if "Check m12 raised" in r["text"]]
    assert hs and hs[0]["delivery"] == "telegram"
    assert w.degraded()
    w.checks["m12"].http_get = real
    tick(w, chain)
    assert open_incidents(w).get(f"{LIQUIDATOR}:balance") == "page"


def test_qa2_m12_none_tip_is_never_louder_than_observed(tmp_path):
    """RUNWAY DECISION: for the same chain, 'none' gives a runway >= 'observed'."""
    out = {}
    for mode in ("observed", "none"):
        d = tmp_path / mode
        d.mkdir()
        chain, w, _ = setup(d, balance=eth(0.3), checks={"projection_tip": mode}, gas_price=gwei(1.5))
        advance(w)
        m = w.checks["m12"].last[ORACLE]
        out[mode] = (m["runway_trailing_days"], m["runway_base_fee_days"], m["tip_source"])
    assert out["none"][1] >= out["observed"][1] and out["none"][0] == out["observed"][0]
    assert out["none"][2].startswith("none") and out["observed"][2].startswith("observed")


def test_qa2_m12_runway_evidence_carries_both_runways_and_inputs(tmp_path):
    chain, w, _ = setup(tmp_path, balance=eth(0.17825))
    advance(w)
    inc = w.db.conn.execute("SELECT evidence FROM incidents WHERE key=?", (f"{ORACLE}:runway",)).fetchone()
    ev = json.loads(inc[0])
    for k in ("runway_trailing_days", "runway_base_fee_days", "avg_daily_spend_wei", "base_fee_wei",
              "avg_priority_fee_wei", "tip_source", "avg_daily_gas", "balance_wei", "window_txs"):
        assert k in ev, k


def test_qa2_m12_transaction_from_a_non_canonical_block_is_not_counted(tmp_path):
    """Untrusted-input rule 'canonical block hash': a node (e.g. a load-balanced backend on another fork) answers
    the tx AND its receipt with a block hash that is not the canonical one at that height → not verified → the
    nonce is missing → UNKNOWN, never counted."""
    chain, w, _ = setup(tmp_path)
    tx_obj, receipt = chain._tx_obj, chain._receipt
    victim = sorted(chain.wtxs.values(), key=lambda t: t["block"])[-1]["hash"]
    fork_hash = "0x" + "fa" * 32
    chain._tx_obj = lambda t: dict(tx_obj(t), blockHash=fork_hash) if t["hash"] == victim else tx_obj(t)
    chain._receipt = lambda t: dict(receipt(t), blockHash=fork_hash) if t["hash"] == victim else receipt(t)
    advance(w)
    assert "UNKNOWN" in w.checks["m12"].last[ORACLE].get("spend", ""), w.checks["m12"].last[ORACLE]
    assert w.db.conn.execute("SELECT COUNT(*) FROM m12_tx WHERE tx_hash=?", (victim,)).fetchone()[0] == 0


# ---------------------------------------------------------------- repair 3: QA attempt 2 (Codex) Appendix A, M12 part
# (ported verbatim except the module prefix; F1 = the first, fourth and fifth, F2 = the others; RED on f779b45)


def test_qa2c_m12_daily_is_actual_24h_not_7200_blocks(tmp_path):
    c = WalletChain(start_block=100000)
    c.timestamp = lambda n: c.genesis_ts + 24 * n  # one missed slot between consecutive canonical blocks
    c.set_balance(ORACLE, eth(10))
    c.set_balance(LIQUIDATOR, eth(1))
    c.send(ORACLE, PROXY, gas_used=60_000_000, gas_price=gwei(1))  # .06 ETH spent now
    c.mine()
    c, w = wallet_worker(tmp_path, ['m12'], chain=c)
    advance(w)
    assert open_incidents(w).get(f'{ORACLE}:daily-spend') == 'warn'


def test_qa2c_m12_partial_feehistory_is_error_never_clean(tmp_path):
    c, w, _ = setup(tmp_path, balance=eth(.333), checks={'projection_tip': 'none'})
    c.set_base_fee(gwei(10))
    c.mine()
    handle = c._handle
    c._handle = lambda method, p: ({'baseFeePerGas': [hex(gwei(.01)), hex(gwei(.01))]} if method == 'eth_feeHistory'
                                   else handle(method, p))
    advance(w)
    assert w.db.meta('run:m12')['errors'] > 0, 'partial requested fee sample was accepted'


def test_qa2c_m12_daily_single_missed_slot_at_boundary(tmp_path):
    c = WalletChain(start_block=100000)
    c.timestamp = lambda n: c.genesis_ts + 12 * n + (12 if n >= 99999 else 0)
    c.set_balance(ORACLE, eth(10))
    c.set_balance(LIQUIDATOR, eth(1))
    c.send(ORACLE, PROXY, gas_used=40_005_000, gas_price=gwei(1))
    c.mine()
    c, w = wallet_worker(tmp_path, ['m12'], chain=c)
    advance(w)
    assert open_incidents(w).get(f'{ORACLE}:daily-spend') == 'warn'


def test_qa2c_m12_partial_history_clears_real_page(tmp_path):
    c, w, _ = setup(tmp_path, balance=eth(.333), checks={'projection_tip': 'none'})
    advance(w)
    assert open_incidents(w) == {}
    c.set_base_fee(gwei(10))
    tick(w, c, blocks=300)
    assert open_incidents(w).get(f'{ORACLE}:runway') == 'page'
    handle = c._handle
    c._handle = lambda method, p: ({'baseFeePerGas': [hex(gwei(.01)), hex(gwei(.01))]} if method == 'eth_feeHistory'
                                   else handle(method, p))
    tick(w, c)
    assert open_incidents(w).get(f'{ORACLE}:runway') == 'page', 'partial input resolved a supported runway page'


def test_qa2c_m12_trailing_window_excludes_transactions_older_than_seven_days(tmp_path):
    c = WalletChain(start_block=60000)
    c.timestamp = lambda n: c.genesis_ts + 24 * n
    c.set_balance(ORACLE, eth(2))
    c.set_balance(LIQUIDATOR, eth(1))
    c.send(ORACLE, PROXY, gas_used=1_000_000_000, gas_price=gwei(1))
    c.mine(40000)
    c.set_balance(ORACLE, eth(.2))
    c.mine()
    c, w = wallet_worker(tmp_path, ['m12'], chain=c)
    advance(w)
    assert w.checks['m12'].last[ORACLE]['window_txs'] == 0
    assert f'{ORACLE}:runway' not in open_incidents(w)


def test_qa2c_m12_partial_history_reorg_keeps_retry_marker(tmp_path):
    c, w, _ = setup(tmp_path, balance=eth(.333), checks={'projection_tip': 'none'})
    advance(w)
    c.set_base_fee(gwei(10))
    tick(w, c, blocks=300)
    assert open_incidents(w).get(f'{ORACLE}:runway') == 'page'
    fork = c.number
    c.mine(3)
    advance(w)
    c.reorg(fork)
    c.mine(3)
    w.check_reorg()
    assert w.db.meta('reorg:m12') is not None
    handle = c._handle

    def short(method, p):
        result = handle(method, p)
        if method == 'eth_feeHistory':
            result['baseFeePerGas'] = [hex(gwei(.01)), hex(gwei(.01))]
        return result

    c._handle = short
    advance(w)
    assert w.db.meta('reorg:m12') is not None, 'partial fee history committed and cleared the reorg obligation'


@pytest.mark.parametrize('mode', ['empty', 'partial', 'duplicate', 'orphan'])
def test_qa2c_m12_reorg_listing_variants(tmp_path, mode):
    c, w, _ = setup(tmp_path, balance=eth(10))
    advance(w)
    c.mine(3)
    advance(w)
    fork = c.number
    c.send(ORACLE, PROXY, gas_used=100)
    tick(w, c)
    c.reorg(fork)
    h1 = c.send(ORACLE, PROXY, gas_used=200)
    h2 = c.send(ORACLE, PROXY, gas_used=300)
    get = w.bs
    if mode == 'partial':
        w.bs.hide.add(h2)
    if mode == 'empty':
        w.checks['m12'].http_get = lambda url: {'items': [], 'next_page_params': None}
    if mode == 'duplicate':
        def dup(url):
            d = get(url)
            d['items'] *= 2
            return d
        w.checks['m12'].http_get = dup
    if mode == 'orphan':
        rc = c._receipt
        c._receipt = lambda t: dict(rc(t), blockHash='0x' + 'ab' * 32) if t['hash'] == h2 else rc(t)
    tick(w, c)
    if mode != 'duplicate':
        assert w.db.meta('reorg:m12') is not None
        assert w.db.meta('run:m12')['errors'] > 0
    else:
        assert w.db.meta('reorg:m12') is None
        rows = w.db.conn.execute('SELECT gas_used FROM m12_tx WHERE tx_hash IN (?,?)', (h1, h2)).fetchall()
        assert sorted(int(r[0]) for r in rows) == [200, 300]


# ---------------------------------------------------------------- repair 3: generalised regressions (time windows by
# chain timestamps, the stored set's lower bound, partial fee history). "RED" = fails on f779b45.


def _missed_slot_chain(profile="every1", start_block=100_000, oracle=eth(10)):
    """A chain whose block timestamps skip slots ("every1": 24 s per block, half the slots missed)."""
    c = WalletChain(start_block=start_block)
    c.timestamp = slot_profile(c.genesis_ts, profile)
    c.set_balance(ORACLE, oracle)
    c.set_balance(LIQUIDATOR, eth(1))
    c.mine()
    return c


def test_m12_r3_daily_spend_excludes_a_transaction_older_than_24h_inside_7200_blocks(tmp_path):
    """RED. 4,500 blocks of 24 s = 30 h: the 0.1 ETH spend is not in the last 24 h (block counting kept it and
    warned at 0.05 ETH/day)."""
    c = _missed_slot_chain()
    c.send(ORACLE, PROXY, gas_used=100_000_000, gas_price=gwei(1))
    c.mine(4_500)
    c, w = wallet_worker(tmp_path, ["m12"], chain=c)
    advance(w)
    m = w.checks["m12"].last[ORACLE]
    assert m["day_spend_wei"] == 0 and m["window_txs"] == 1
    assert f"{ORACLE}:daily-spend" not in open_incidents(w)


def test_m12_r3_missed_slots_never_dilute_the_trailing_average(tmp_path):
    """RED. 0.14 ETH over the last 3 days of a chain with 24 s blocks: 0.02 ETH/day over 7 days of chain time →
    0.2 ETH lasts 10 days → warn (block counting spread it over 14 days: 20 days, silent)."""
    c = _missed_slot_chain()
    c.mine(60_000)
    for _ in range(7):
        c.send(ORACLE, PROXY, gas_used=20_000_000, gas_price=gwei(1))
        c.mine(1_000)
    c.set_balance(ORACLE, eth(0.2))
    c.mine()
    c, w = wallet_worker(tmp_path, ["m12"], chain=c, checks={"m12": {"projection_tip": "none"}})
    advance(w)
    m = w.checks["m12"].last[ORACLE]
    assert m["avg_daily_spend_wei"] == Fraction(7 * 20_000_000 * gwei(1), 7) and m["window_seconds"] == WEEK_S
    assert m["runway_trailing_days"] == 10
    assert open_incidents(w)[f"{ORACLE}:runway"] == "warn"


def test_m12_r3_daily_spend_resolves_exactly_when_the_spend_is_24h_old_by_chain_time(tmp_path):
    """RED. Anchored at the checkpoint block's timestamp: the warn holds while the spend's block is within the
    last 86,400 s and resolves at the first checkpoint 86,400 s after it (a missed slot every 7 blocks: ≈ 6,300
    blocks, where block counting waited for 7,200)."""
    c = _missed_slot_chain("every7")
    c.send(ORACLE, PROXY, gas_used=50_000_000, gas_price=gwei(1))
    spent = c.number
    c.mine()
    c, w = wallet_worker(tmp_path, ["m12"], chain=c)
    advance(w)
    assert open_incidents(w) == {f"{ORACLE}:daily-spend": "warn"}
    last = next(n for n in range(spent, spent + DAY) if c.timestamp(n + 1) - c.timestamp(spent) >= DAY_S)
    assert last - spent < 6_400
    tick(w, c, blocks=last - c.sealed)
    assert c.sealed == last and open_incidents(w) == {f"{ORACLE}:daily-spend": "warn"}
    tick(w, c)
    assert open_incidents(w) == {} and alerts(w)[-1] == ("info", f"{ORACLE}:daily-spend", "resolved")


def test_m12_r3_window_search_is_exact_and_bounded_under_a_burst_of_missed_slots(tmp_path):
    """A 3,000-slot outage inside the window (10 h): both windows equal the timestamps' and the header search
    stays a few dozen reads."""
    c = WalletChain(start_block=100_000)
    c.timestamp = lambda n: c.genesis_ts + 12 * n + (36_000 if n >= 140_000 else 0)
    c.set_balance(ORACLE, eth(5))
    c.set_balance(LIQUIDATOR, eth(1))
    c.mine()
    spend_history(c)
    c.mine()
    c, w = wallet_worker(tmp_path, ["m12"], chain=c)
    advance(w)
    m, cp = w.checks["m12"].last[ORACLE], c.sealed
    week, day = time_window(c, cp, WEEK_S), time_window(c, cp, DAY_S)
    assert (m["window_txs"], m["window_fees_wei"], m["day_fees_wei"]) == (len(week), fees(week), fees(day))
    assert c.timestamp(m["window_from_block"] - 1) <= c.timestamp(cp) - WEEK_S < c.timestamp(m["window_from_block"])
    assert c.timestamp(m["day_from_block"] - 1) <= c.timestamp(cp) - DAY_S < c.timestamp(m["day_from_block"])
    reads = w.db.meta("run:m12")["rpc_calls"]["eth_getBlockByNumber"]
    assert reads <= m["window_txs"] + 2 + 40, reads  # verification headers + the two searches


def test_m12_r3_non_increasing_timestamps_in_a_normal_run_are_unknown_and_keep_the_warn(tmp_path):
    chain, w, _ = setup(tmp_path, balance=eth(10), gas_per_day=40_000_000)
    advance(w)
    assert open_incidents(w) == {f"{ORACLE}:daily-spend": "warn"}
    ts = chain.timestamp
    chain.timestamp = lambda n: ts(min(n, 100_000))  # no time passes after block 100,000
    tick(w, chain, blocks=DAY)
    assert "non-increasing block timestamps" in w.checks["m12"].last[ORACLE]["spend"]
    assert open_incidents(w) == {f"{ORACLE}:daily-spend": "warn"} and health(w)


def _edge_of_the_window(tmp_path, checks=None):
    """A 1 ETH spend at block E; the worker runs from E + 10 to C1 = E + 50,402 (12 s slots), where E has left
    the 7-day window; then a reorg from C1 - 2 leaves a shorter chain whose window (to C1 - 3) includes E again."""
    c = WalletChain(start_block=100_000)
    c.set_balance(ORACLE, eth(5))
    c.set_balance(LIQUIDATOR, eth(1))
    c.mine()
    edge = c.number
    c.send(ORACLE, PROXY, gas_used=1_000_000_000, gas_price=gwei(1))
    c.mine(10)
    c, w = wallet_worker(tmp_path, ["m12"], chain=c, checks={"m12": checks or {}})
    advance(w)
    assert w.checks["m12"].last[ORACLE]["window_txs"] == 1
    for _ in range(4):
        tick(w, c, blocks=12_600)
    tick(w, c, blocks=edge + WINDOW + 2 - c.sealed)
    assert w.checks["m12"].last[ORACLE]["window_txs"] == 0  # E is 7 days and 24 s old
    fork = c.sealed - 2
    c.reorg(fork)  # the new chain's checkpoint: fork - 1 = E + 50,399, T - ts(E) = 604,788 s
    assert c.sealed == edge + WINDOW - 1
    return c, w, edge


def test_m12_r3_shorter_chain_moving_the_window_back_still_counts_the_edge_transaction(tmp_path):
    """RED. The margin kept below the window holds E: the reorg run counts it, with no Blockscout listing (the
    pruned set undercounted it before)."""
    c, w, edge = _edge_of_the_window(tmp_path)
    calls = len(w.bs.calls)
    w.clock.advance(301)
    advance(w)
    m = w.checks["m12"].last[ORACLE]
    assert w.db.meta("reorg:m12") is None and m["window_txs"] == 1 and m["window_fees_wei"] == eth(1)
    assert len(w.bs.calls) == calls


def test_m12_r3_restart_between_the_shorter_chain_reorg_and_its_run_still_counts_the_edge_transaction(tmp_path):
    """RED. (d) a crash after the reorg's detection: the new process's reorg run counts E from the kept margin."""
    c, w, edge = _edge_of_the_window(tmp_path)
    w.check_reorg()
    assert w.db.meta("reorg:m12") is not None
    clock, start, activation = w.clock, w.cfg.start_block, w.cfg.activation_block
    w.db.conn.close()
    c, w = wallet_worker(tmp_path, ["m12"], chain=c, clock=clock, start_block=start, activation_block=activation)
    clock.advance(301)
    advance(w)
    m = w.checks["m12"].last[ORACLE]
    assert w.db.meta("reorg:m12") is None and m["window_txs"] == 1 and m["window_fees_wei"] == eth(1)
    week = time_window(c, c.sealed, WEEK_S)
    assert set(week) == {h for h, x in c.wtxs.items() if x["block"] == edge}


def test_m12_r3_window_below_the_stored_set_is_backfilled_never_undercounted(tmp_path):
    """Without the margin (prune_margin_blocks 0), the shorter chain's window starts below history_from: the
    reorg run backfills; with Blockscout down it raises and keeps the marker, then counts E."""
    c, w, edge = _edge_of_the_window(tmp_path, {"prune_margin_blocks": 0})
    real = w.bs
    w.checks["m12"].http_get = lambda url: (_ for _ in ()).throw(OSError("down"))
    w.clock.advance(301)
    advance(w)
    assert w.db.meta("reorg:m12") is not None
    w.checks["m12"].http_get = real
    w.clock.advance(301)
    advance(w)
    m = w.checks["m12"].last[ORACLE]
    assert w.db.meta("reorg:m12") is None and m["window_txs"] == 1 and m["window_fees_wei"] == eth(1)


@pytest.mark.parametrize("fault", sorted(FEE_FAULTS))
@pytest.mark.parametrize("reorg", [False, True])
def test_m12_r3_every_partial_fee_history_raises_and_keeps_the_page(tmp_path, fault, reorg):
    """RED, all six: five were accepted; junk raised an RpcError out of the step. The run fails (check-error health,
    errors + 1), the runway page stays, nothing is sent; in a reorg run the marker stays; the full answer then
    resolves."""
    c, w, _ = setup(tmp_path, balance=eth(.333), checks={"projection_tip": "none"})
    advance(w)
    c.set_base_fee(gwei(10))
    tick(w, c, blocks=300)
    assert open_incidents(w) == {f"{ORACLE}:runway": "page"}
    if reorg:
        fork = c.number
        c.mine(3)
        advance(w)
        c.reorg(fork)
    c.set_base_fee(gwei(1.15))  # the fee falls: a complete answer resolves the page
    c.mine(300)
    w.check_reorg()
    marker, errors, n = w.db.meta("reorg:m12"), w.db.meta("run:m12")["errors"], len(outbox(w))
    assert (marker is not None) == reorg
    fault_fee_history(c, fault)
    w.clock.advance(301)
    advance(w)
    del c._handle
    assert w.db.meta("run:m12")["errors"] == errors + 1 and w.db.meta("reorg:m12") == marker
    assert open_incidents(w) == {f"{ORACLE}:runway": "page"}
    assert [r for r in outbox(w)[n:] if r["kind"] != "health"] == []
    assert "eth_feeHistory" in health(w, "check-error")[-1]["text"]
    tick(w, c)
    assert w.db.meta("reorg:m12") is None and open_incidents(w) == {}


def test_m12_r3_fee_history_of_a_chain_younger_than_the_sample_is_complete(tmp_path):
    """Control: at block 100 the 300-block sample is blocks 0..100 (oldestBlock 0, 101 entries): accepted."""
    c = WalletChain(start_block=100)
    c.set_balance(ORACLE, eth(10))
    c.set_balance(LIQUIDATOR, eth(1))
    c.mine()
    c, w = wallet_worker(tmp_path, ["m12"], chain=c)
    advance(w)
    assert w.db.meta("run:m12")["errors"] == 0 and w.checks["m12"].last[ORACLE]["base_fee_wei"] == gwei(1.15)


def test_m12_r3_time_window_settings_are_validated(tmp_path):
    for bad in ({"window_blocks": 50_400}, {"day_s": 2 * WEEK_S}, {"window_s": 0}, {"prune_margin_blocks": -1}):
        with pytest.raises(SystemExit):
            wallet_worker(tmp_path / str(len(str(bad))), ["m12"], checks={"m12": bad})
