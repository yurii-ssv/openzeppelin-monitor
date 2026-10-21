# EB reconciliation worker (Immunefi #94775)

Detects the register-after-root effective-balance collapse described in
[`plans/94775-eb-collapse-monitoring.md`](../plans/94775-eb-collapse-monitoring.md). It runs next to
the OpenZeppelin Monitor, not inside it, because the check needs cross-transaction state and
storage reads that a per-transaction filter script cannot hold.

The worker is read-only against the chain. Each poll:

1. Scans confirmed SSVNetwork proxy logs into SQLite and advances a durable checkpoint
   (`eth_getLogs` in bounded windows; a window the RPC rejects is split, and an error is never read as "no logs").
2. For each cluster, takes its last `ClusterBalanceUpdated` (root reference block **S**, leaf value **E**)
   and replays the validator changes after S (`reference`) and after the update (`replay`) with the
   pinned contract rules.
3. Reads `clusterEB[id]`, `ethClusters[id]` and `clusters[id]` at the same block B and classifies:

| Status | Meaning | Alert |
| --- | --- | --- |
| `confirmed` | positive `reference − stored` and keys added in (S, update) were removed after the update | critical (warning while liquidated; escalates on reactivation) |
| `latent` | positive gap, added keys still registered | none; daily digest |
| `clean` / `overbilled` / `implicit` / `implicit_reset` / `not_eth` | no #94775 shortfall | none; resolves an open incident |
| `unknown` | missing logs, stored snapshot ≠ replay, cluster hash ≠ last event, layout drift | monitor-health alert, never treated as clean |

The replay must reproduce the stored vUnits exactly (and the latest event's `Cluster` must hash to the
stored cluster record), or the cluster is `unknown`. That self-check is what makes a gap in the logs visible.

## Run

```bash
cp reconcile/config.example.json reconcile/config.json        # gitignored
# .env (repo root): EB_RECONCILE_RPC_URL, and for alerts.mode "telegram":
#                   EB_TELEGRAM_TOKEN, EB_TELEGRAM_CHAT_ID
docker compose -f docker-compose.local.yaml -f docker-compose.reconcile.yaml up -d --build
docker compose -f docker-compose.local.yaml -f docker-compose.reconcile.yaml logs -f eb-reconcile
```

Start with `"alerts": {"mode": "record"}`: alerts are printed as JSON lines and kept in the outbox.
Switch to `"telegram"` after the record-only baseline (plan §7, gate 4). The first start backfills
from `start_block` (the v2.0.0 activation, 24,920,727): about 1.2M blocks, a few minutes on an RPC
that accepts 50k-block `eth_getLogs` windows. State lives in `reconcile/data/` (override with
`EB_RECONCILE_DATA_DIR`). On Linux, make that directory writable by uid 10001.

Commands (also usable locally with `.venv/bin/python eb_reconcile.py <cmd> --config <file>`):

- `run`: the loop (default container command)
- `once`: scan to head, run a full evaluation, deliver, exit
- `status`: checkpoint, last sweep counts, open incidents, undelivered alerts
- `audit`: event-only history of every root window with post-S additions and matched removals
- `verify-layout`: implementation, module addresses and codehashes against the pin

## Config notes

- `pr614_guards_from`: log position of the hotfix module upgrade (block 25,037,749, log 218).
  Before it, emptying a legacy SSV cluster left its snapshot set; the replay applies the rule by position.
- `layout`: pinned from `ssv-network-triage/contracts.lock.json`. Any drift suspends reconciliation
  with a health alert. After an upgrade, revalidate `eb_storage.py`, then update the pin.
- `confirmations`: blocks behind head (default 12). A negative value uses the `finalized` tag.
- RPC: archive state is not needed (storage is read at the checkpoint block), but `eth_getLogs` over
  history is. Public endpoints differ: `rpc.mevblocker.io` accepted 50k-block windows on 2026-10-05;
  drpc's free tier and publicnode did not.

## Tests

```bash
cd reconcile
uv venv .venv && uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/python -m pytest -q tests                                  # gates 1-3 on an in-memory chain
FORK_RPC_URL=<mainnet RPC> .venv/bin/python scripts/fork_e2e.py      # gate 1 on an anvil mainnet fork
FORK_RPC_URL=<mainnet RPC> .venv/bin/python scripts/fork_e2e.py --telegram   # + gate 5 to TEST_* channel
```

`fork_e2e.py` replays the triage PoC (63 empty-shares dummy keys, a 2,048 ETH root, removal) on a
local fork with impersonated live oracles, and checks the worker after every step. Nothing is sent to mainnet.
