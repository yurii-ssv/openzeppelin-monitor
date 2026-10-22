# SSV watch worker (grown from the #94775 EB reconciliation worker)

The stateful half of the monitoring service ([plan §6](../plans/monitoring-service/plan.md)): it runs
next to the OpenZeppelin Monitor, not inside it, because its checks need cross-transaction state and
storage reads that a per-transaction filter script cannot hold. One process hosts one check module
per monitor family ([`checks/`](checks/README.md)) on a shared SQLite database, with an incident
lifecycle, a retrying outbox (Telegram for page/warn, Discord for info/digest), the Monitor's
failed-transaction inbox, live protocol parameters, a validated cluster inventory, health alerts
and a `/metrics` endpoint.

The worker is read-only against the chain. Each step:

1. Rewinds on a reorg below the checkpoint (logs, cursors, inventory, live parameters and every other
   piece of block-derived state: [checks/README.md §6.1](checks/README.md)); scans confirmed SSVNetwork
   proxy logs into SQLite and advances a durable checkpoint (`eth_getLogs` in bounded windows; a window
   the RPC rejects is split, an error is never read as "no logs", and logs whose block hash is not
   canonical are rejected).
2. Ingests new lines of the failed-transaction inbox and resolves their revert selectors
   (`eth_call` at `block − 1`, Blockscout as fallback). A configured inbox file that is missing or
   unreadable is a monitor-health alert, never an empty inbox (create the empty file at deployment).
   Entries reach checks only once the checkpoint has passed their block and their block hash is
   canonical (the Monitor writes at 2 confirmations: inbox evidence waits for the worker's 12).
3. Reloads the live parameters after a parameter event (spec M15) or a reorg, then runs every due check
   (interval, event, inbox, near-set-per-block) and turns its findings into incidents and outbox rows.
   After a reorg, an open incident last seen above the common ancestor that the check's re-evaluation run
   does not report again is resolved as "evidence left the canonical chain" ([checks/README.md
   §6.2](checks/README.md)).
   A failed read invalidates the parameters (checks that read them do not run) and is retried every
   step; see [checks/README.md §5.1](checks/README.md).
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

The runbook (start, stop, status, metrics, backfill, RPC failover, secrets, routes, Kubernetes) is
[`deploy/README.md`](../deploy/README.md#runbook-running-the-service). In short:

```bash
cp reconcile/config.example.json reconcile/config.json        # gitignored
# .env (repo root), names only: MAINNET_RPC_URL (optional, replaces the first of rpc_urls); for alerts.mode
#                   "live" also TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, DISCORD_WEBHOOK_INFO (or point the config at TEST_*)
docker compose -f docker-compose.yaml -f docker-compose.reconcile.yaml up -d --build
docker compose -f docker-compose.yaml -f docker-compose.reconcile.yaml logs -f eb-reconcile
```

Start with `"alerts": {"mode": "record"}`: alerts are written as JSON lines and kept in the outbox.
Switch to `"live"` after the record-only soak. The first start backfills from `start_block` (the
v2.0.0 activation, 24,920,727) **silently** (D17, `backfill`): about 1.3M blocks in 5,000-block `eth_getLogs`
windows, then one silent evaluation of the history by every check at the caught-up checkpoint; each check alerts
from its first run at a later block, whatever the others do (one failing its silent run does not hold them back). State
lives in the named volume `watch-data` (D18). A database written by the pre-framework worker is
migrated in place on start (its open M5 incidents keep their ids and are not re-sent; messages it
queued but had not sent keep their keys, take the framework severity names and are delivered on their
severity's route).

Commands (also usable locally with `.venv/bin/python eb_reconcile.py <cmd> --config <file>`):

- `run`: the loop (default container command); serves `/metrics` when `metrics.enabled`
- `once`: one step with every check forced, then exit (`--serve-metrics --hold N` keeps `/metrics` up N s)
- `status`: phase (`backfill` with progress, or `live`), checkpoint, per-check last run, open incidents,
  undelivered alerts (read-only: answers while a check runs)
- `export`: every incident and outbox message as JSON (soak evidence)
- `audit`: M5 event-only history of every root window with post-S additions and matched removals
- `verify-layout`: implementation, module addresses and codehashes against the pin

Metrics: `ssv_watch_checkpoint_block`, `ssv_watch_lag_blocks`, `ssv_watch_backfill`,
`ssv_watch_backfill_remaining_blocks`, `ssv_watch_open_incidents{check,severity}`,
`ssv_watch_outbox_undelivered`, `ssv_watch_outbox_failing`, `ssv_watch_check_last_run_seconds{check}`,
`ssv_watch_check_last_run_block{check}`, `ssv_watch_check_errors_total{check}`,
`ssv_watch_rpc_consecutive_failures`, `ssv_watch_inbox_entries{state}`,
`ssv_watch_input_unavailable{input="params"|"inbox"}`. `<db_path>.status.json` has `healthy: false`
while a step fails or a configured input (parameters, inbox) cannot be read.

LIVE probes (read-only): `.venv/bin/python inventory.py --probe --rpc <url>` (API v4 pages, ≥ 5 sampled
tuples validated against storage) and `.venv/bin/python params.py --probe --rpc <url>`.

## Config notes

- Per-check settings live under `checks.<id>`; see [checks/README.md §7](checks/README.md). Unknown keys fail at start.
- `checks.m5.pr614_guards_from`: log position of the hotfix module upgrade (block 25,037,749, log 218).
  Before it, emptying a legacy SSV cluster left its snapshot set; the replay applies the rule by position.
- `checks.m5.layout`: pinned from `ssv-network-triage/contracts.lock.json`. Any drift suspends M5
  with a health alert. After an upgrade, revalidate `eb_storage.py`, then update the pin.
- `confirmations`: blocks behind head (default 12). A negative value uses the `finalized` tag.
- RPC (D16): `rpc_urls`, tried in order with failover (default `https://mainnet.gateway.tenderly.co`,
  `https://gateway.tenderly.co/public/mainnet`, `https://rpc.mevblocker.io`); entries are URLs or environment
  variable names; `rpc_url_env` (`MAINNET_RPC_URL`), when set, replaces the first. Archive state is not needed
  (state is read at the checkpoint block), `eth_getLogs` over history is; `max_log_range` 5,000 (the cap of all
  three), halved when an endpoint rejects a window.

## Tests

```bash
uv venv reconcile/.venv --python 3.12
uv pip install --python reconcile/.venv/bin/python -r reconcile/requirements-dev.txt
reconcile/.venv/bin/python -m pytest -q reconcile/tests          # framework + M5 gates 1-3 on an in-memory chain
reconcile/.venv/bin/ruff check reconcile
export PATH=$HOME/.foundry/bin:$PATH FORK_RPC_URL=https://mainnet.gateway.tenderly.co
reconcile/.venv/bin/python reconcile/scripts/fork_e2e.py --scenario m5      # gate 1 (#94775 PoC) on an anvil fork
reconcile/.venv/bin/python reconcile/scripts/fork_e2e.py --scenario m3      # liquidation zone: info, warn, page, zero, resolve
reconcile/.venv/bin/python reconcile/scripts/fork_e2e.py --scenario m9      # oracle dissent streak, failed round
reconcile/.venv/bin/python reconcile/scripts/fork_e2e.py --scenario m12     # oracle wallet below 0.15 ETH
```

`fork_e2e.py` drives each scenario on a local fork at the RPC's head with impersonated accounts and checks the
worker after every step (`--telegram` sends to `TEST_*`, `--port` runs scenarios side by side). M3 mines ~50k
empty blocks (about 35 min on anvil). Nothing is sent to mainnet.
