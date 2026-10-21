# Monitoring plan: register-after-root EB collapse (Immunefi #94775)

Status: implemented, record-only on mainnet; Telegram route not yet enabled · Owner: security · Target: `ssv-network-monitor` plus a read-only reconciliation worker · No contract or oracle change

## 0. Implementation status (2026-10-05)

The worker is in [`reconcile/`](../reconcile/README.md) and runs as its own service through `docker-compose.reconcile.yaml`. It is Python with SQLite (WAL); its only dependency is `pycryptodome`, used for keccak. The plan's §3A fast path and §3B backstop share one loop. Clusters touched by new finalized logs are evaluated immediately, clusters with an explicit EB snapshot every 5 minutes, and every cluster hourly.

Changes from the text below, all found during implementation:

- **The replay is checked against storage.** Besides the reference-adjusted value, the worker replays from the last update to the stored vUnits and requires an exact match. It also requires the latest event's `Cluster` to hash to `ethClusters[id]`/`clusters[id]`. Either mismatch makes the cluster `unknown`, which is how a missing log range becomes visible (§2's "complete and coherent inputs").
- **The PR #614 boundary is position-dependent.** The hotfix modules went live at block 25,037,749, log 218. Before that point, emptying a legacy SSV cluster or migrating an empty one left its snapshot set. The first mainnet run found one such cluster (stored 10,625 vUnits, count 0). The replay now applies the guard by log position (`pr614_guards_from`).
- **The layout guard is implemented.** It checks the implementation and the SSV_CLUSTERS, SSV_VALIDATORS and SSV_DAO module addresses on every sweep. At startup it also checks module codehashes and cross-checks 5 clusters against `SSVNetworkViews.getEffectiveBalance`. Drift suspends reconciliation and raises a health alert.
- **Context alerts (§3C) were not added** to the Monitor config; they remain optional. The worker's alerts state whether shares were empty and who sent the update, and mark both as clues.

Validation results:

| Gate | Result |
| --- | --- |
| 1 · PoC trace | On an anvil mainnet fork (block 26,126,988, real contracts, impersonated live oracles), `scripts/fork_e2e.py` checks each step. After the honest root: clean 640,000/640,000. After the dummies are added: clean 1,270,000/1,270,000. After the root at S is consumed: **latent** 1,270,000 vs 640,000, no page. After removal: **confirmed** 640,000 vs 10,000, gap 630,000, 63 matched keys, one critical alert. After a clean root: resolved. The layout guard passes on the fork. The same trace runs in the unit tests. |
| 2 · benign cases | 30 unit tests on an in-memory chain. They cover post-S honest removal, removal before the update (overbilled), add after the root was consumed, add/remove with no update, an update by a third party, two clusters with one owner, an emptied-then-refilled cluster (`implicit_reset`), honest latent additions, a liquidated cluster (warning, then critical on reactivation), and legacy SSV clusters with migration. None of the benign cases pages. |
| 3 · restart, ranges, gaps, reorg, RPC outage | Covered by tests: same incident ID and no duplicate page after restart; rejected ranges split; silently truncated logs give `unknown` plus a health alert; a reorg rewinds and replays; 3 consecutive RPC failures raise a health alert; delivery is retried; Telegram errors never echo the token. |
| 4 · mainnet backfill, record-only | 24,920,727 → 26,126,990: 101,305 events across 638 clusters. All reconcile: 126 clean, 369 implicit, 143 legacy SSV, **0 latent, 0 confirmed, 0 unknown**. The views cross-check agrees on 5 clusters. The event-only `audit` found 20 honest windows with additions between S and the update (the updater lags S by about 100 blocks), all superseded by a later root, and **0 matched removals since activation**. |
| 5 · delivery | The fork run with `--telegram` delivered the critical and resolved messages to the `TEST_TELEGRAM_*` channel (messages 15 and 16). The production route is not configured. |

Before enabling the critical route: choose a production RPC with history `eth_getLogs` (the private node in the triage `.env` was unreachable on 2026-10-05, and only `rpc.mevblocker.io` of the public ones worked), run `mode: "record"` for a few days, then set `mode: "telegram"` with the incident channel's `EB_TELEGRAM_*` variables.

## 1. Objective and evidence

Detect an ETH-billed cluster whose stored effective balance (EB) is lowered by registrations after an oracle root's reference block, consumption of that root, and subsequent removals. Alert promptly on the sequence and reconcile on-chain accounting after missed monitor events or downtime. Report an observed fee-weight shortfall, not a claimed theft of principal or accrued fees.

Source: `ssv-network-triage/analyses/z_#94775_analysis.md` and `analyses/94775-explainer.html`. The analysis reports a successful live-proxy fork PoC: 63 dummy keys, a root with 2,048 ETH for one real validator, and 32 ETH billed after removal. The live exposure and gas figures dated 2026-10-02 are context, not alert thresholds. The originally supplied analysis path without `#` does not exist.

## 2. The reference-adjusted accounting check

`RootCommitted(merkleRoot, blockNum)` gives the reference block **S**. A successful `ClusterBalanceUpdated(owner, operatorIds, blockNum=S, effectiveBalance=E, cluster)` proves that this cluster consumed a leaf from that root; the event contains the actual leaf value E. The contract records `lastRootBlockNum=S` and `vUnits=ebToVUnits(E)`. For an ETH cluster with an explicit EB snapshot, each later registration adds 10,000 vUnits (32 ETH of fee weight) and each later removal subtracts 10,000. The important window starts **after S**, not after the `RootCommitted` transaction. A new commit does not by itself end the window; only another successful update of that cluster replaces its snapshot.

At a finalized block B, for the cluster's **last consumed** root S, calculate:

```
reference_adjusted_vunits(B) = ebToVUnits(E_at_S)
                             + 10_000 * (adds in blocks S+1..B - removals in blocks S+1..B)
observed_vunits(B)           = clusterEB[clusterId].vUnits
discrepancy_vunits           = max(0, reference_adjusted_vunits - observed_vunits)
```

Use the contract's exact integer `ebToVUnits` conversion (ceiling division) and integer vUnits throughout; convert to ETH only for display. Count **all** successful `ValidatorAdded` and `ValidatorRemoved` logs for the same `(owner, ordered operatorIds)`/cluster ID, including changes before the update transaction. Order transactions and logs by `(blockNumber, transactionIndex, logIndex)` when classifying the sequence. This formula handles honest post-S removals when no registrations were overwritten: their 32 ETH baseline is subtracted from both sides. It also catches the PoC: adds and removals net to zero since S, but the stale-root overwrite erased the additions from storage.

A positive discrepancy **before a matching removal** is a latent overwritten baseline, not yet proof of reduced billing for the original validator set: E can legitimately be applied while the added keys remain registered. Confirm #94775 when keys added after S and before the root update are removed after that update, the discrepancy remains positive, and the cluster is still active/ETH-billed. Report the exact stored fee weight and the reference-adjusted difference. A zero discrepancy is not evidence that current Beacon EB is correct; ordinary Beacon EB changes await a later root. The backstop is replay of durable on-chain logs plus current storage, so it survives downtime with complete RPC history but is not independent of that history.

In the PoC, after adding 63 keys but before the update, both reference-adjusted and stored values are 1,270,000 vUnits. The update resets stored to 640,000 while all 64 keys remain; classify the 630,000 difference as latent. After removing those 63 keys, reference-adjusted returns to 640,000 while stored falls to 10,000; classify the 630,000 difference as a confirmed #94775 pattern.

Reconcile only when inputs are complete and coherent: active ETH-billed cluster, a known successful update event for its `lastRootBlockNum`, complete logs from S+1 through B, and a storage read at **the same block B**. If any input is missing, mark the cluster `unknown` and alert on monitor health rather than treating it as clean. If `vUnits=0`, apply the contract's `validatorCount × 10,000` fallback for reported billing, and investigate the zero snapshot separately. Migrations, liquidation, cluster deletion/recreation and version changes require their own replay rules; mark them `unknown` until those rules are verified. Keep the raw stored value and effective billing value separately.

This is an accounting-consistency check for #94775, not an independent Beacon oracle. The worker can validate the emitted E against an oracle artifact only if that artifact is bound to the on-chain `merkleRoot` with a verified proof. The plan does not depend on an unverified `/api/v1/commit?full=true` endpoint or an assumed full cluster list from it.

## 3. Detection and alert levels

### A. Fast path: event sequence

Have a worker process finalized SSVNetwork proxy logs for `RootCommitted`, `ValidatorAdded`, `ClusterBalanceUpdated`, and `ValidatorRemoved` in block/log order. Key records by chain ID, proxy address and cluster ID (`keccak256(abi.encodePacked(owner, operatorIds))`, preserving operator order). For each successful update, retain its reference block S, E, transaction and log position. On each removal, find registrations of the **same public keys** after S and before that update, in the same cluster, followed by those removals. This is a **candidate** even when E equals the pre-update stored EB: the PoC can deliberately consume an unchanged root, and honest updaters may skip that cluster.

Read storage at the removal block after all transactions in that block, then run the check in §2. Emit one incident per cluster/root with evidence: add/update/remove tx hashes and log positions, S, B, matching key count, E, reference-adjusted and observed vUnits, and the discrepancy. Re-evaluate on later blocks until a new root is consumed and the discrepancy closes. Do not require removals to precede the next `RootCommitted`; a commit without a cluster update does not repair billing.

`transaction.from` is a clue, not proof of ownership. `updateClusterBalance` is permissionless, so an updater allowlist can annotate unusual senders but must never gate detection. A contract wallet, relayer, or oracle replacement can change the sender. Likewise, empty or short `shares` is a useful clue, not a prerequisite: the fork PoC accepted empty shares, and the contract does not enforce a fixed shares length.

### B. Backstop: continuous reconciliation

Run the same check and matched-key classification for every known active ETH cluster at least every few minutes, including just before/after each new root is committed and after a cluster consumes a root. Keep positive discrepancies without a post-update matched removal as latent candidates. Do not schedule it **only** on `RootCommitted`: the attacker removes keys after consuming that root, and the next root could be hours away. A root commit also does not guarantee that the cluster has consumed it.

Build the cluster inventory from historical proxy events and keep it current; cross-check counts and version against a trusted indexer/API if available. A cluster absent from the inventory is a coverage failure. Bootstrap by replaying history from the ETH-billing activation point, or import a verified complete snapshot and replay from its pinned block. For each cluster, retain or recover the last successful `ClusterBalanceUpdated` event and all validator changes since its S. A startup run must backfill from the last finalized checkpoint before declaring the monitor healthy. The worker should use bounded `eth_getLogs` ranges, retries and an archive-capable RPC source, then advance its checkpoint only after the block's state and alerts are durably recorded.

At each sweep, batch `eth_getStorageAt` calls at a fixed finalized block B. For `StorageEB`, the namespace base is `uint256(keccak256("ssv.network.storage.eb")) - 1`, `clusterEB` is at base + 1, the mapping slot is `keccak256(abi.encode(clusterId, base + 1))`, and `vUnits` occupies the low 64 bits. The same word holds `lastRootBlockNum` and `lastUpdateBlock`; decode and use them to check event history. Pin the proxy address and chain ID. Before deployment, verify the derivation against the fork PoC and several mainnet `getEffectiveBalance` calls using canonical `Cluster` structs; abort reconciliation if it no longer agrees with the deployed implementation. A storage layout upgrade requires revalidation.

Do not compare every cluster to a newly committed leaf: many clusters have not consumed the new root, and honest additions/removals after S change stored EB. A leaf-only comparison would misclassify those states. If a verified oracle API later becomes available, use it as an additional Beacon-versus-billing signal with a separate label, root hash and proof; do not merge that signal with the §2 invariant.

### C. Context alerts

Optional low-priority monitors can report empty/short shares, unusually large `bulkRemoveValidator` calls, and a non-usual sender of `ClusterBalanceUpdated`. They help operators inspect attempts in progress, but none is required for a confirmed shortfall. Rate-limit and summarize them; do not page on these clues alone. A `RootCommitted`/`OracleReplaced` monitor is useful for operational visibility.

## 4. Routing and triage

| Condition | Route | Message |
| --- | --- | --- |
| Matching add → consumed root → remove, invariant not yet available | Warning | Candidate with tx links, S and key count; explicitly mark the accounting result pending/unknown. |
| Matched post-update removal and `discrepancy_vunits > 0` with complete evidence | Critical | Confirmed #94775 pattern, exact reference-adjusted/observed vUnits, ETH-equivalent fee-weight difference, cluster, owner, operators, root, tx links and first-seen block. |
| Shortfall persists after the next successful update, grows, or spans multiple clusters | Critical update | Same incident ID with duration and change; do not suppress escalation. |
| Missing logs, state/API mismatch, stale checkpoint, no successful sweep, or unavailable RPC | Monitor-health alert | Coverage gap and last known good block, distinct from an exploit alert. |
| Empty/short shares, bulk removals, unusual update sender | Info digest | Context only. |

Do not wait for a 64 ETH gap or two roots: a single 32 ETH fee unit can be a real instance, and an attacker may repeat the sequence each round. Do require the matched removal for the critical #94775 classification; an overwritten baseline while the added keys are still present is a candidate. Use exact vUnits, not floating-point ETH, for thresholds and deduplication. Track resolution when a later valid snapshot closes the discrepancy; preserve the prior incident and duration. Estimated fee impact should use the interval and actual network/operator fees, with assumptions shown. It is optional for initial alerting; a simple `gap × current fee × year` is not a measured loss.

The worker should send a structured Telegram alert (or call a reviewed notification script) to the designated incident channel. Credentials come from environment or a secret manager. Telegram delivery must be retried with an idempotency key and surfaced as a health failure if retries exhaust. Never put tokens or webhook URLs in tracked config or diagnostic logs. If the existing monitor supplies auxiliary Telegram alerts, use a test channel first.

## 5. Integration with this repository

The running service is this fork of OpenZeppelin **Monitor**, with the BlockWatcher → filter → trigger → notification pipeline, rather than the Defender hosted service. The local `config/` currently contains 24 monitors. Most match successful/failed function calls; the DAO-success and Safe-execution-failure rules already match events, so event matching is supported here. The two validator rules send to Discord only; DAO and oracle rules already use both Telegram and Discord. Add new #94775 routes deliberately, without changing or duplicating those existing messages.

The filter emits a match per transaction, not per log, and has no cross-transaction state. A bulk validator call can produce many matched event entries in one transaction match. A `trigger_conditions` script receives JSON on stdin and its **last stdout line** controls filtering: `true` means suppress the match, `false` means let notifications run; a script error also leaves the match eligible for the ordinary notification path. These scripts cannot inject a computed key count, storage gap or custom message into a Telegram template. A separate `script` notification trigger can run arbitrary code on a match, but that still runs inside the monitor's notification flow and is not a durable event queue. An RPC-heavy per-removal script would block that flow and make detection depend on its timeout. Put sequence tracking, reconciliation and detailed notifications in the worker; use ordinary Monitor event rules only for simple context alerts.

If an event monitor is added, use the exact ABI signature from the deployed spec (including the tuple types), and remember that the matcher emits a match per **transaction**, with matched event arrays. A bulk removal's many logs arrive in one match. Do not pass `"${RPC_URL}"` in `trigger_conditions.arguments`: the script executor passes arguments as JSON on stdin without shell expansion. Read an environment variable in the worker instead. Verify the runtime image contains any interpreter used by a script.

`config/**/*.json`, `config/**/*.py`, etc. are gitignored here. Track deployment templates explicitly (for example under `reconcile/config/`), then install/copy them in the deployment step. Keep scripts and worker code in tracked paths. The tracked `docker-compose.yaml` mounts `./config` read-only at `/app/config` and uses `${MONITOR_DATA_DIR:-/dev/null}` for `/app/data`; that default is not a durable store for a worker. The worker needs its own persistent volume, the same RPC network reachability (the compose `monitor-network` if colocated), and its own environment-provided credentials. The existing `docker-compose.local.yaml` is an untracked local dry-run setup using `.local-dry`; it rewrites notification triggers for log-only or test-channel delivery and is not production deployment. Add a separate production deployment change and test routing.

Suggested tracked files:

```
reconcile/eb_reconcile.py           # finalized-log scanner, state check and alerts
reconcile/eb_storage.py             # pinned layout and conversion
reconcile/config.example.json       # addresses, RPC environment variable names, intervals
reconcile/Dockerfile
reconcile/tests/                    # fixture replay and storage/layout checks
```

If the worker shares a mounted `data/` volume with the monitor, give it its own subdirectory and lock. Prefer SQLite in WAL mode for cluster inventory, finalized checkpoint, root/update/log evidence and alert delivery state; a JSONL file plus an alert JSON file is fragile across crashes and concurrent writers. Persist the checkpoint and derived state in one transaction. Store `(chainId, proxy, blockHash, blockNumber, logIndex)` for idempotency and detect a changed block hash on the confirmation horizon. Keep enough history to recompute from S; archive older evidence outside the live DB as needed. Use one active worker or a lease so two instances cannot double-send.

Keep the worker database and WAL files in a persistent mounted volume and add their paths to `.gitignore`; the current ignore rules cover `data/**/*.json` and `data/**/*.txt`, not SQLite files.

## 6. Coverage and recovery

Set `max_past_blocks` for `evm_ethereum_mainnet.json` based on the **largest affordable restart catch-up**, not on the oracle round length. This repository's block watcher begins at `max(last_processed + 1, latest_confirmed - max_past_blocks)` and can skip older blocks after downtime. `recovery_config` retries blocks known to have failed processing; it is not a substitute for the worker's durable historical backfill. A large `max_past_blocks` on the shared network may cause costly full-block/receipt scans, so load-test before changing it. The worker's `eth_getLogs` checkpoint is the primary no-gap recovery path for this incident.

Use at least the monitor's two confirmations for worker decisions, and record the queried block hash. On reorg, rewind to a common finalized ancestor, invalidate affected derived state and alert keys, then replay. Monitor a maximum checkpoint lag (for example 5 minutes under normal RPC conditions) and alert if it is exceeded. A full periodic audit should verify cluster inventory, consumed-root/event consistency, and RPC result completeness. An empty result from an RPC request is not proof that there were no logs when the provider has errored or truncated a range.

## 7. Validation and rollout gates

1. Reproduce the #94775 fork trace against the worker at each step: latent discrepancy but no confirmed alert before removal; confirmed pattern on the matched removal; `reference_adjusted=640,000`, `observed=10,000`, `discrepancy=630,000` vUnits afterward; resolution only after a later consumed clean root. Include the unchanged-EB owner-consumed-root path.
2. Replay benign cases: post-S honest removal, add after the root was consumed, add/remove without a root update, updates by a non-owner, multiple roots with no cluster update, multiple clusters with one owner, reordered/multiple logs in a transaction, and zero explicit vUnits. They must not become confirmed #94775 alerts.
3. Exercise a restart across more than one round, RPC pagination/range limits, a missing log range, and a short reorg. Require a coverage alert on incomplete input and identical incident IDs after replay.
4. Backfill recent mainnet history and run the worker in record-only mode. Investigate every positive discrepancy, distinguishing latent candidates from matched-removal cases, and tune only the context digest volume. Reconcile a verified baseline at a pinned block before enabling the critical Telegram route.
5. Test delivery to a dedicated channel, including Telegram failure/retry and redaction. Enable production alerting only after the worker's checkpoint and reconciliation health are visible.

## 8. Open dependencies

- Confirm the deployed proxy implementation/storage layout still matches the pinned fork PoC before relying on raw storage reads.
- Establish a complete ETH-cluster inventory bootstrap source and an archive-capable Ethereum RPC with bounded `eth_getLogs` support. Until then, label the backstop coverage as partial.
- Identify the production runtime and alert destination. The local dry-run compose file is not a production deployment.
- Optional: obtain an oracle artifact/API that includes per-cluster leaves and proofs, verify each against the committed root, and add a separate Beacon-data comparison. It is not required for the on-chain #94775 detector.
