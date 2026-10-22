# deploy/

Tracked Monitor config templates, the offline gates that protect them, and the runbook of the service
([Runbook](#runbook-running-the-service)). Nothing here is a secret; secrets stay
`{"type":"environment","value":"<NAME>"}` references.

| Path | What it is |
|---|---|
| `config/` | monitor, trigger, network and filter templates, rendered by `render.sh` |
| `render.sh` | copies `config/` into a config directory, then runs `check-abi.sh` and the Monitor's `--check` |
| `check-templates.sh` | structural checker (S0–S7, S1b shape); S7 runs `check-abi.sh` |
| `shape.jq` | type-level shape of monitors, triggers and networks, shared by the checkers (a wrong type is a FAIL line, not a jq error) |
| `check-abi.sh` | ABI gate A1–A9, see below |
| `check-m15-coverage.sh` | each of the 17 M15/M10 events (spec names + `ModuleUpgraded`, full signature from the reference ABI) is on the Discord monitor, and none is unconditional on a Telegram route |
| `abi/` | the committed reference ABI the gate compares against |
| `tests/` | `selftest.sh`, `abi-mutation-sweep.sh`, `m13-fork-regression.sh` |

## Runbook: running the service

Two containers share one data volume: the **Monitor** (`docker-compose.yaml`, stateless rules, appends failed
transactions to `inbox/failed-tx.jsonl`) and the **watch worker** (`docker-compose.reconcile.yaml`, service
`eb-reconcile`: stateful checks M1–M14, SQLite, incidents, outbox). Every command below runs from the repo root
with both files; set this once per shell:

```bash
export COMPOSE_FILE=docker-compose.yaml:docker-compose.reconcile.yaml
```

### Configure (once)

| What | How |
|---|---|
| Monitor config | production: `deploy/render.sh --out config --strict` (secrets in the environment); local or soak without real channels: `scripts/local-dry-run.sh --prepare` (renders into `.local-dry/config` and keeps only `TEST_*` triggers, the inbox bridge and a local log) and `export MONITOR_CONFIG_DIR=./.local-dry/config` |
| Worker config | `cp reconcile/config.example.json reconcile/config.json` (gitignored); keep `"alerts": {"mode": "record"}` until routes are enabled; add `"record_path": "/data/alerts-record.jsonl"` to keep recorded alerts in the volume |
| Secrets and RPC | environment only (`.env`, untracked; names in `.env.example` and in the header of `docker-compose.reconcile.yaml`). Never a value in a tracked file. `docker compose config` prints resolved values: never paste its output |
| RPC (D16) | the worker's `rpc_urls`, in failover order: `https://mainnet.gateway.tenderly.co`, `https://gateway.tenderly.co/public/mainnet`, `https://rpc.mevblocker.io`; `MAINNET_RPC_URL`, when set, replaces the first. The Monitor reads `MAINNET_RPC_URL` (and `MAINNET_RPC_URL_2`, optional) |

### Start, stop, restart

```bash
docker compose build                     # Monitor (Rust release, Dockerfile.production, a few min) and worker images
docker compose up -d                     # inbox-init creates the empty inbox, then the worker starts
docker compose ps                        # eb-reconcile shows (healthy) once a step succeeded in the last 10 min
docker compose logs -f eb-reconcile      # or monitor
docker compose stop                      # stop, keep everything
docker compose up -d                     # start again: the worker resumes from its durable checkpoint
docker compose down                      # remove containers; the named volumes (state) are KEPT
```

The Monitor image is built from `Dockerfile.production`. Do not build it from `Dockerfile.development`
(`DOCKERFILE=…`): its pinned base has an older glibc than the `jq` it installs, `jq` cannot load, and every script
trigger fails, the failed-tx inbox bridge included (CP3 finding F-1). Check: `docker compose exec monitor jq --version`.

State lives in the named volumes `<project>_watch-data` (worker: SQLite + WAL, status file, recorded alerts) and
`<project>_monitor-data` (Monitor: last processed block, `inbox/failed-tx.jsonl`). `docker compose down -v` deletes
them: the worker then backfills from `start_block` again (silently) and the Monitor restarts at the head. Backup:
`docker run --rm -v <project>_watch-data:/d -v "$PWD":/b alpine tar czf /b/watch-data.tgz -C /d .` after
`docker compose stop eb-reconcile` (or a volume snapshot).

### Status and metrics

```bash
docker compose exec eb-reconcile python eb_reconcile.py status   # JSON: phase, checkpoint, lag, checks, inputs, open incidents
docker compose exec eb-reconcile python eb_reconcile.py export   # every incident and outbox message (JSON)
docker compose exec eb-reconcile cat /data/eb-reconcile.status.json   # what the healthcheck reads (healthy, at, error)
docker compose exec eb-reconcile tail -n 20 /data/alerts-record.jsonl # record mode: what would have been sent
curl -s http://127.0.0.1:9464/metrics                            # Prometheus text, host loopback only
```

`status` and `export` open the database read-only: they answer while a check's run holds the write lock. Read
`healthy` and `error` in the status file: `false` while a step fails (RPC), an input is unreadable (live
parameters, inbox) or a reorg re-evaluation is pending. Metrics: `ssv_watch_checkpoint_block`,
`ssv_watch_lag_blocks`, `ssv_watch_backfill` (1 while backfilling), `ssv_watch_backfill_remaining_blocks`,
`ssv_watch_open_incidents{check,severity}`, `ssv_watch_outbox_undelivered`, `ssv_watch_outbox_failing`,
`ssv_watch_check_last_run_seconds{check}`, `ssv_watch_check_last_run_block{check}`,
`ssv_watch_check_errors_total{check}`, `ssv_watch_rpc_consecutive_failures`, `ssv_watch_inbox_entries{state}`,
`ssv_watch_input_unavailable{input}`. Alert on: lag > 50 for 10 min, `rpc_consecutive_failures` ≥ 3,
`outbox_failing` > 0, `input_unavailable` = 1, a check's `last_run_seconds` older than 2× its interval.

### Backfill phase (D17)

A new database starts in phase `backfill`: the worker scans proxy logs from `start_block` (24,920,727) in steps of
`backfill.blocks_per_step` (50,000) blocks, `eth_getLogs` windows of at most `max_log_range` (5,000), without
waiting between steps (about 1.3M blocks: a few minutes, longer when the public RPCs rate-limit). Checks do not run
while the checkpoint is behind. The first checkpoint within `backfill.live_within_blocks` (12) of the scan target is
the caught-up checkpoint (`status.phase.history_block`): history ends there. Each check evaluates the whole history
once there, **silently** (a full run: incidents are opened, escalated and resolved without a message; baselines and
per-check tables are built). From the first block after the caught-up checkpoint, each check that has committed its
silent run alerts: its next run, at a later block, is forced, and from then on its alerts are sent, whatever the
other checks do. A condition still true at that first live run (a low wallet, a cluster in the liquidation zone, an
open M5 shortfall, a dissenting oracle that votes again) is sent then, as `open`; one that only existed in history
(an old failed round, a past event) is never sent and stays visible in `status`/`export` as an incident with
`alerts = 0`. Events up to the caught-up checkpoint are history, including failed transactions the Monitor wrote to
the inbox during the backfill; every later block is live. A check whose silent run fails (an RPC error, a broken
input) raises its usual `check-error` health message (an RPC error counts as an RPC failure) and does not hold the
other checks back; its silent run is retried every step, still pinned to the caught-up checkpoint (state read at
that block: after more than about 100 blocks this needs one endpoint of the failover list that serves historical
state; a pruned endpoint's error moves the read to the next one, and the D16 public endpoints served it when
checked), and the blocks after it are then evaluated live in the same step. The phase stays `backfill`
until every check has committed its silent run, then becomes `live` (`status.phase.caught_up_block` = that step's
checkpoint). While `backfill`: `status.phase` shows `progress`, `remaining_blocks` and the checks that `evaluated`
history; until the caught-up checkpoint the lag health alert is off; the daily digest starts with phase `live`;
health messages (RPC, inbox, parameters, check errors) are always sent. A restart resumes the backfill from its
checkpoint.

### RPC failover (D16)

Each request goes to the endpoint that answered last, then to the others in order; after `rpc_primary_retry_s`
(300 s) the first is tried again. A transport failure (unreachable, timeout, HTTP 429/5xx) or a JSON-RPC error
other than a revert moves to the next endpoint; a revert is an answer; an empty result (`[]`, `null`) is a result.
When all fail, the step fails: after 3 consecutive failed steps a `rpc` health message is queued (again at 20 and
100), and `rpc-recovered` when a step succeeds again. A scan window whose reads were answered by two different
endpoints is re-read at the next step. Logs name endpoints by host or by the variable name, never by URL.

### Rotate secrets

1. Change the value in `.env` (or the Kubernetes Secret). Values never go into `config/`, `reconcile/config.json`
   or a tracked file.
2. `TELEGRAM_CHAT_ID` is substituted into the Monitor's Telegram triggers at render time: re-run
   `deploy/render.sh --out config --strict` after changing it. Tokens and webhooks are read at start.
3. `docker compose up -d` recreates the containers whose environment changed (state is kept).
4. Revoke the old token/webhook at the provider after the new one delivered a message.

### Enable routes (record → live)

1. Soak in record mode first (A10, 48 h): `alerts.mode: "record"`; review `export` and the record file. Record
   mode marks every message delivered (receipt `recorded`), so switching to live never replays the soak.
2. Test channels: set `TEST_TELEGRAM_TOKEN`, `TEST_TELEGRAM_CHAT_ID`, `TEST_DISCORD_WEBHOOK` in `.env`; in
   `reconcile/config.json` set `"mode": "live"`, `"telegram_token_env": "TEST_TELEGRAM_TOKEN"`,
   `"telegram_chat_id_env": "TEST_TELEGRAM_CHAT_ID"`, `"discord_webhook_env": "TEST_DISCORD_WEBHOOK"`;
   `docker compose up -d --force-recreate eb-reconcile`. Prove each route with one message:

   ```bash
   for r in telegram discord; do docker compose exec eb-reconcile python -c "import json, notify; \
     c = json.load(open('/app/config.json')); \
     print(notify.make_notifier(c['alerts']).send('health', 'SSV watch: route test ($r)', 'route-test', '$r'))"; done
   ```

   For the Monitor, `scripts/local-dry-run.sh --prepare` with the `TEST_*` variables set routes its triggers to
   the test channels.
3. Production: point the three `*_env` names back at `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`, `DISCORD_WEBHOOK_INFO`,
   render the Monitor config with `deploy/render.sh --out config --strict`, unset `MONITOR_CONFIG_DIR`, and
   `docker compose up -d --force-recreate`; repeat the route test.

### Kubernetes (D18; DevOps owns the manifests)

- **One replica**, never two: a StatefulSet (or a Deployment with `strategy: Recreate`), so two workers never open
  the same database.
- **Storage**: one ReadWriteOnce PVC on block storage (1–5 GiB; the database is ~75 MB after the backfill of
  October 2026 and grows with the logs).
  Not NFS/EFS or another network filesystem: SQLite in WAL mode needs POSIX locks and shared memory on a local
  filesystem.
- **One pod, two containers** (Monitor and worker) sharing the PVC: the Monitor mounts it at `/app/data`, the
  worker mounts a `subPath: watch` at `/data` and `subPath: inbox` read-only at `/inbox`. An initContainer creates
  `inbox/failed-tx.jsonl` (empty, `touch`, never truncate) before both start (the compose `inbox-init` service).
- **Config**: ConfigMaps for the rendered Monitor config (`config/`, read-only at `/app/config`) and the worker's
  `config.json` (at `/app/config.json`). **Secrets**: a Secret exposed as environment variables (names as in
  `.env.example`).
- **Security**: `readOnlyRootFilesystem: true` for the worker (an `emptyDir` at `/tmp` is harmless; SQLite keeps
  temporary data in memory), non-root uid 10001, `allowPrivilegeEscalation: false`.
- **Probes**: readiness = the compose healthcheck command (status file healthy and written within 10 min). Liveness
  = staleness only (`time.time() - s['at'] < 600`: every step writes the status file, a failing one with
  `healthy: false`), or none: a liveness probe on `healthy` restarts the pod (the Monitor with it) on every upstream
  RPC outage longer than its budget, which a restart does not fix; the outage is already reported (`rpc` health
  message, `healthy: false`, `ssv_watch_rpc_consecutive_failures`). Give the first start a 30 min startup budget
  (backfill). Metrics: port 9464, path `/metrics`.
- **Backup**: volume snapshots of the PVC (stop the worker or accept a crash-consistent SQLite+WAL snapshot,
  which SQLite recovers on open).

## ABI reference gate (`check-abi.sh`, A8/A9)

`openzeppelin-monitor --check` never compares a monitor's events/functions with its ABI, and a monitor's
`contract_spec` is copied JSON that nothing else validates. A monitor whose `UnstakeRequested` has the
`indexed` flags of `user` and `amount` swapped has the same name, the same canonical `name(types)` and
the same input names as the real event; the Monitor loads it and it silently never matches.

`check-abi.sh` therefore compares every event and function of every `contract_spec` with
`abi/reference.json` (keyed by contract address) in full: name, `anonymous`, and each input's name, type,
tuple components, **order** and (events) **`indexed`**, plus each function's **outputs**. An entry that
is not in the reference, or that differs in any of those, is an `A9` failure; an address with a
`contract_spec` but no reference entry is `A8`. The reference is never read from the monitor under check.

`abi/reference.json` holds two contracts, derived (not hand-written):

- the SSV Network proxy `0xDD9BC35aE942eF0cFa76930954a156B3fF30a4E1`: `.abi` of
  `SSVNetworkSSVStakingUpgrade` built from `ssvlabs/ssv-network` at `f7ac4870074b9408855a7912f9c6e1f959996be8`
  (plan pin `v2.0.0+f7ac487`); the committed `abis/SSVNetwork.json` at that commit is not used, it lacks
  `initializeSSVStaking`, which the deployed implementation has;
- the DAO Safe `0xb35096b074fdb9bBac63E3AdaE0Bbde512B2E6b6`: Safe v1.3.0 `GnosisSafe` ABI from
  `@safe-global/safe-deployments@1.37.0`, whose mainnet address for 1.3.0 is the proxy's singleton.

`abi/provenance.json` records the on-chain verification (endpoint `https://eth.drpc.org`, block 26241166):
implementation codehash equals `contracts.lock.json`, and real logs of `UnstakeRequested`, `FeesSynced` and
`ExecutionSuccess` carry exactly the number of topics the reference's `indexed` flags imply.

Regenerate or re-verify (needs the two sources, see the header of `abi/derive.sh`):
`deploy/abi/derive.sh --ssv-out <forge out> --safe-abi <gnosis_safe.json> [--check]`.
`tests/abi-mutation-sweep.sh` mutates every kind (indexed, type, order, arity, rename, anonymous, output)
across every `contract_spec` entry of all monitors and requires one `A9` per mutated entry;
`tests/selftest.sh` runs it.

Not covered by the gate: `error`, `constructor`, `fallback` and `receive` entries (the Monitor does not
decode them), `stateMutability` and `internalType`, and the semantics of an expression (A5 only checks
that the names it compares are inputs of that event/function).

## Discord routing (D11)

The six `*_SUCCESS` triggers (cluster, dao, liquidation, operator, oracle, validator) and the M11
other-sender alert post to `DISCORD_WEBHOOK_INFO`. The six `*_FAILED` triggers, `ORACLE_DISSENT`,
`DAO_SAFE_FAILED` and `LARGE_ETH_TRANSFER` keep their own `DISCORD_WEBHOOK_<NAME>` variables. Trigger display
names stay unique (the Monitor refuses duplicates). `check-templates.sh` (S4) fails if an info trigger
uses another variable, if a non-info trigger uses `DISCORD_WEBHOOK_INFO`, or if any `discord_url` is not an
environment reference; `render.sh --strict` requires every referenced variable to be set.

## M13 coverage boundary

**Status: closed by owner decision D10 (`plans/monitoring-service/plan.md` §2, Marco, 2026-10-21). The
Monitor covers direct transactions only; the rest is split between a worker balance-delta watch (CP2c,
another lane, not delivered by CP1A) and an ACCEPTED, documented gap.** No template tries to close it.
The OpenZeppelin Monitor evaluates `transactions` conditions on the transaction's outer `from`, `to`,
`value`, `hash`, gas fields and `input`, and `events`/`functions` conditions on the monitored contract's own
logs and calldata. It has no trace or balance data (and the public RPCs have no trace API), so value that
moves through an internal call is invisible to it. Spec M13 says "any incoming value" to the retired keys and
"any interaction" of the attackers with SSV contracts or our wallets; per D10 the spec's row 3 is amended
at closure to this boundary. QA attempt 2 (`plans/monitoring-service/qa/cp1a-attempt-2.md`, F1b) and
attempt 3 (`.../cp1a-attempt-3.md`, F1b) demonstrated the gap on a local fork.

### Row by row: Monitor, CP2c worker watch, accepted gap

| M13 row | Monitor (CP1A, this directory) | CP2c worker watch (D10, not in this lane) | Accepted gap (D10) |
|---|---|---|---|
| **1. retired slot 4 `0x3A98…3bB4`** | a **direct** tx with `to == 0x3A98…` and `value > 0`, success or revert (`ssv_retired_addresses_monitor`); a direct tx `from == 0x3A98…` calling `commitRoot` / `updateClusterBalance` on the proxy, reverted or not (`ssv_retired_updater_attempt_monitor`) | per-block `eth_getBalance` delta of the key: any increase, whatever route (internal CALL, constructor, `SELFDESTRUCT`) → page with the delta | a transfer that nets to zero within a block (in and out in the same block) |
| **2. retired liquidator `0x4BC6…01aF`** | a **direct** tx with `to == 0x4BC6…` and `value > 0`, success or revert (`ssv_retired_addresses_monitor`) | same balance-delta watch on this key | same: within-block net-zero |
| **3. attackers `0xaeE0…b80f`, `0x0144…F134`** | a **direct** tx between either attacker and any of: proxy, Views, SSV token, cSSV, DAO Safe, our two operational wallets (`0x16A5…`, `0x0431…`), including zero-value and reverted ones (`ssv_attacker_interaction_monitor`: one `transactions` expression on `from`/`to`, no `value` predicate, status `Any`) | none (the watch covers the retired keys only) | **internal** interactions (attacker → intermediate contract → SSV contract or our wallet), **constructor** interactions, `SELFDESTRUCT` value to our wallets, and interactions routed through a third party (router, multisig, bundler); also internal value into our current wallets |

Whatever a watch of the retired keys does catch is outside this directory: CP1A does not read balances,
and until CP2c is verified rows 1 and 2 have only the direct-transaction coverage above.

Measured, direct cases: 42/42 attacker × SSV-contract/wallet × {0, 1 wei, reverted} fire once; wallets
sending 0/1 wei to an attacker 8/8; direct transfers to each retired key 1 (in) / 0 (out) / 0 (zero); an
old-key `commitRoot` attempt 1, other sender 0 (QA attempt 2 §1 F1b; `tests/m13-fork-regression.sh`, 15
cases, all pass).

### Reproduced misses (the accepted gap, none of these fires in the Monitor)

| Observation | Why the Monitor cannot see it | Reproducer (local fork) |
|---|---|---|
| Internal CALL carrying value to a retired key | outer `to` is the contract, outer `value` is not the forwarded amount | unrelated sender → helper → retired slot 4, 1 wei: block 26240413 (retired liquidator: 26240418); expected 1, actual 0, key balance +1 wei (CP2c's balance watch is the answer for these two) |
| Internal CALL by an attacker-controlled path | outer `to` is the intermediate contract, which is not on the target list | attacker 1 → helper → our oracle wallet, block 26240404; attacker 2, block 26240405 |
| Constructor-created transfers (contract creation whose constructor sends value to a watched address or calls an SSV contract) | the creation has no `to`; the constructor's CALLs are not transaction fields | attacker 1 constructor → our liquidator wallet, block 26240406; attacker 2, block 26240407; constructor → each retired key, blocks 26240427, 26240429 |
| `SELFDESTRUCT` value to a retired key or wallet | no call, no log, no transaction-level `value` | to retired slot 4 block 26240428, retired liquidator 26240430, our oracle wallet 26240432 (QA attempt 3) |
| Interactions that reach SSV contracts only through a third party | outer `from`/`to` are the third party | same helper class as above |

Re-open this only by a new owner decision: a page for the attacker rows needs a component with traces
(`debug_traceTransaction` / `trace_block`) or a balance watch of our own wallets, and neither is in scope
under D10.

## Fail-closed gates (repair 4, F10)

A gate must never read "the command failed" as "no violations". Every `jq`, `grep`, `find` and `ls`
in `check-templates.sh`, `check-abi.sh`, `check-m15-coverage.sh`, `render.sh`, `abi/derive.sh`,
`tests/abi-mutation-sweep.sh` and `scripts/local-dry-run.sh` has its status checked: a failing jq is a
`FAIL` line (or aborts the render), and an empty result is only "nothing found" when the command
succeeded. Inputs that cannot be read as one JSON value of the right shape (`shape.jq`) are failures:
empty, unreadable or binary files, symlink loops and dangling links, stray non-JSON files in
`monitors/ triggers/ networks/`, a trigger whose `config` is a string, a monitor whose `events` is an
object. The secret scan (S3) reads binary files as text and refuses symlinks it cannot follow. A missing
or non-working `jq` exits non-zero with `jq is required`. `tests/selftest.sh` (section F10) covers each
class and injects a jq failure at each site (`SELFTEST_ONLY=f10` runs just that section).
