"""M12 — operational wallet balances and burn rate (spec §3 M12, D4; plan §7 V2x.*, V2c.7).

Scenario numbers are the spec's measured burn (15.5 Mgas/day ≈ 0.0177 ETH/day at ~1.15 gwei; balance 0.3454 ETH
→ ≈ 19.5 days; at 5 gwei ≈ 4.5 days), carried by the fixtures, never by the check.
"""

from __future__ import annotations

import json

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
    chain, w = wallet_worker(tmp_path, ["m12"], chain=chain, checks={"m12": checks or {}}, **(worker or {}))
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
    """The week was paid at 5 gwei (4.5 days of runway); the base fee is back at 1.15 gwei (19.4 days): the
    trailing computation pages."""
    chain, w, _ = setup(tmp_path, gas_price=gwei(5))
    advance(w)
    ev = evidence(w, f"{ORACLE}:runway")
    assert 4.4 < float(ev["runway_trailing_days"]) < 4.5 and 19.3 < float(ev["runway_base_fee_days"]) < 19.5
    (row,) = outbox(w, "severity='page'")
    assert "from trailing spend" in row["text"]


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
