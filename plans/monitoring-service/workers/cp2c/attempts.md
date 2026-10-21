# Attempts — CP2c

Same entry shape as execution-log.md events, worker-side. Append only. Measured UTC (`date -u +%Y-%m-%dT%H:%MZ`).

<a id="attempt-1"></a>
## Attempt 1 — baseline, contract reading, design ask

- Observed UTC: 2026-10-21T13:42Z
- Task / Dispatch: `task_8384f0c7778e` / `ctx_113e2440cd8e`
- Frozen base: `96d1c32fe3446ec7092e849e401f851a0d9b2cf3` (`git rev-parse HEAD` in the fresh child; status clean)
- Action and concrete outcome: `export PATH=$HOME/.foundry/bin:$PATH`; `uv venv reconcile/.venv --python 3.12`; `uv pip install --python reconcile/.venv/bin/python -r reconcile/requirements-dev.txt` (pytest 9.1.1, ruff 0.17.1). Read plan §1, §2, §6, §7 CP2 rows, §8 CP2c; `reconcile/checks/README.md` in full; spec M8, M12, M13, §4, §5; the frozen framework sources the checks call (`checks/base.py`, `registry.py`, `rpc.py`, `alerts.py` apply path, `eb_reconcile.py` scheduler, `tests/fakechain.py`, `tests/harness.py`).
- One Blockscout GET (LIVE, `https://eth.blockscout.com/api/v2/addresses/0x16A5…/transactions?filter=from`) to learn the v2 item shape: `hash`, `block_number`, `nonce`, `from.hash`, `fee.value`, `gas_used`, `gas_price`, `status`; 50 items per page; `next_page_params` = `{block_number, index, hash, fee, filter, inserted_at, value, items_count}`. Newest item nonce 1016 at block 26,239,696; 50 items span 2,401 blocks (≈ 150 txs/day, so a 7-day backfill is ≈ 21 pages).
- Framework gaps found (no shared edit; coordinator accepted lane-local handling): `rpc.Chain` has no `eth_getTransactionReceipt`, `eth_getTransactionCount`, block header/full-block read, `eth_feeHistory`, `eth_getBlockReceipts`; `FakeChain` serves none of them and its `reorg()` keeps `set_balance` entries above the fork. → lane-local helper `checks/m12_wallet_burn.py::raw_batch` (pinned, counted) and test-local `fixtures_wallets.WalletChain(FakeChain)`. CP3 promotion item.
- Design ask to the coordinator (Orca `ask`, answer "proceed, with three additions", recorded in [plan.md](plan.md)).
- Resulting head: `96d1c32` (no commit yet)
- Next action: S2 fixtures.
