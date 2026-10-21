# SSV watch worker (grown from the #94775 EB reconciliation worker)

The stateful half of the monitoring service ([plan §6](../plans/monitoring-service/plan.md)): it runs
next to the OpenZeppelin Monitor, not inside it, because its checks need cross-transaction state and
storage reads that a per-transaction filter script cannot hold. One process hosts one check module
per monitor family ([`checks/`](checks/README.md)) on a shared SQLite database, with an incident
lifecycle, a retrying outbox (Telegram for page/warn, Discord for info/digest), the Monitor's
failed-transaction inbox, live protocol parameters, a validated cluster inventory, health alerts
and a `/metrics` endpoint.

The worker is read-only against the chain. Each step:

1. Rewinds on a reorg below the checkpoint; scans confirmed SSVNetwork proxy logs into SQLite and
   advances a durable checkpoint (`eth_getLogs` in bounded windows; a window the RPC rejects is split,
   and an error is never read as "no logs").
2. Ingests new lines of the failed-transaction inbox and resolves their revert selectors
   (`eth_call` at `block − 1`, Blockscout as fallback).
3. Reloads the live parameters after a parameter event (spec M15), then runs every due check
   (interval, event, inbox, near-set-per-block) and turns its findings into incidents and outbox rows.
4. Re-alerts open incidents (page 6 h, warn 24 h), sends the daily digest, delivers the outbox.

| Module | Role |
|---|---|
| `eb_reconcile.py` | config, `Worker` (scan, reorg, scheduler, delivery, health, status), CLI |
| `checks/` | check interface (`base.py`), registration (`registry.py`), checks; [README](checks/README.md) |
| `alerts.py` | findings → incidents → outbox, routing, re-alert, health, digest |
| `notify.py` | record mode (JSON lines), Telegram, Discord webhook, router |
| `db.py` | SQLite schema, `schema_version` and in-place migration from the pre-framework worker |
| `rpc.py` | JSON-RPC client and the block-pinned `Chain` view checks read through |
| `params.py` | live protocol parameters from SSVNetworkViews |
| `inventory.py` | SSV API v4 + logs, every tuple validated against storage |
| `inbox.py` | failed-transaction inbox and revert decoding |
| `metrics.py` | Prometheus text exposition (stdlib HTTP server) |
| `eb_events.py`, `eb_storage.py`, `eb_model.py` | event decoding, pinned storage layout, M5 replay model |

## M5 (#94775) — `checks/m5_eb_desync.py`

Detects the register-after-root effective-balance collapse described in
[`plans/94775-eb-collapse-monitoring.md`](../plans/94775-eb-collapse-monitoring.md). For each cluster,
it takes the last `ClusterBalanceUpdated` (root reference block **S**, leaf value **E**), replays the
validator changes after S (`reference`) and after the update (`replay`) with the pinned contract
rules, reads `clusterEB[id]`, `ethClusters[id]` and `clusters[id]` at the same block B and classifies:

| Status | Meaning | Alert |
| --- | --- | --- |
| `confirmed` | positive `reference − stored` and keys added in (S, update) were removed after the update | page (warn while liquidated; escalates on reactivation) |
| `latent` | positive gap, added keys still registered | none; tracked silently, daily digest |
| `clean` / `overbilled` / `implicit` / `implicit_reset` / `not_eth` | no #94775 shortfall | none; resolves an open incident |
| `unknown` | missing logs, stored snapshot ≠ replay, cluster hash ≠ last event, layout drift | monitor-health alert, never treated as clean |

The replay must reproduce the stored vUnits exactly (and the latest event's `Cluster` must hash to the
stored cluster record), or the cluster is `unknown`. That self-check is what makes a gap in the logs visible.
The pre-framework severities map `critical → page`, `warning → warn`; incident ids (`EB94775-…`) and
outbox keys are unchanged.

## Run

```bash
cp reconcile/config.example.json reconcile/config.json        # gitignored
# .env (repo root), names only: MAINNET_RPC_URL; for alerts.mode "live" also
#                   TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, DISCORD_WEBHOOK_INFO (or point the config at TEST_*)
docker compose -f docker-compose.local.yaml -f docker-compose.reconcile.yaml up -d --build
docker compose -f docker-compose.local.yaml -f docker-compose.reconcile.yaml logs -f eb-reconcile
```

Start with `"alerts": {"mode": "record"}`: alerts are written as JSON lines and kept in the outbox.
Switch to `"live"` after the record-only soak. The first start backfills from `start_block` (the
v2.0.0 activation, 24,920,727): about 1.2M blocks, a few minutes on an RPC that accepts 50k-block
`eth_getLogs` windows. State lives in `reconcile/data/` (override with `EB_RECONCILE_DATA_DIR`); on
Linux, make that directory writable by uid 10001. A database written by the pre-framework worker is
migrated in place on start (its open M5 incidents keep their ids and are not re-sent).

Commands (also usable locally with `.venv/bin/python eb_reconcile.py <cmd> --config <file>`):

- `run`: the loop (default container command); serves `/metrics` when `metrics.enabled`
- `once`: one step with every check forced, then exit (`--serve-metrics --hold N` keeps `/metrics` up N s)
- `status`: checkpoint, per-check last run, open incidents, undelivered alerts
- `audit`: M5 event-only history of every root window with post-S additions and matched removals
- `verify-layout`: implementation, module addresses and codehashes against the pin

Metrics: `ssv_watch_checkpoint_block`, `ssv_watch_lag_blocks`, `ssv_watch_open_incidents{check,severity}`,
`ssv_watch_outbox_undelivered`, `ssv_watch_outbox_failing`, `ssv_watch_check_last_run_seconds{check}`,
`ssv_watch_check_last_run_block{check}`, `ssv_watch_check_errors_total{check}`,
`ssv_watch_rpc_consecutive_failures`, `ssv_watch_inbox_entries{state}`.

LIVE probes (read-only): `.venv/bin/python inventory.py --probe --rpc <url>` (API v4 pages, ≥ 5 sampled
tuples validated against storage) and `.venv/bin/python params.py --probe --rpc <url>`.

## Config notes

- Per-check settings live under `checks.<id>`; see [checks/README.md §7](checks/README.md). Unknown keys fail at start.
- `checks.m5.pr614_guards_from`: log position of the hotfix module upgrade (block 25,037,749, log 218).
  Before it, emptying a legacy SSV cluster left its snapshot set; the replay applies the rule by position.
- `checks.m5.layout`: pinned from `ssv-network-triage/contracts.lock.json`. Any drift suspends M5
  with a health alert. After an upgrade, revalidate `eb_storage.py`, then update the pin.
- `confirmations`: blocks behind head (default 12). A negative value uses the `finalized` tag.
- RPC: archive state is not needed (storage is read at the checkpoint block), but `eth_getLogs` over
  history is. Public endpoints differ: `rpc.mevblocker.io` accepted 50k-block windows on 2026-10-05;
  drpc's free tier and publicnode did not.

## Tests

```bash
uv venv reconcile/.venv --python 3.12
uv pip install --python reconcile/.venv/bin/python -r reconcile/requirements-dev.txt
reconcile/.venv/bin/python -m pytest -q reconcile/tests          # framework + M5 gates 1-3 on an in-memory chain
reconcile/.venv/bin/ruff check reconcile
FORK_RPC_URL=<mainnet RPC> reconcile/.venv/bin/python reconcile/scripts/fork_e2e.py              # gate 1 on an anvil fork
FORK_RPC_URL=<mainnet RPC> reconcile/.venv/bin/python reconcile/scripts/fork_e2e.py --telegram   # + gate 5 to TEST_*
```

`fork_e2e.py` replays the triage PoC (63 empty-shares dummy keys, a 2,048 ETH root, removal) on a
local fork with impersonated live oracles, and checks the worker after every step. Nothing is sent to mainnet.
