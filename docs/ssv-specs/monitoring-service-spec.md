# SSV monitoring service — alert specification

**Date:** 2026-10-21 · **Contracts:** mainnet `validatedAgainst: v2.0.0+f7ac487` (v2.0.0 + PR #614 hotfix)
**Sources:** `ledger.json`, `RESOLUTIONS.md` (2026-09-29 decisions), `GRANTS-SUMMARY.md`, `KNOWN_ISSUES.md`,
`analyses/`, `extra/eb-oracle-daily-cadence.md`, `extra/20261020-issue/`, the 2026-10-05 oracle key incident.

## 1. Purpose

On the 2026-09-23 call we set a rule: change a contract only for a critical impact, loss of funds or
downtime. On 2026-09-29 we applied it to the open findings. None got a contract change; each closes
on docs, a known-issue entry, or monitoring. This file lists every monitor that came out of those
decisions, out of other accepted known issues, and out of operational incidents. It is written as a
spec for the monitoring service.

Two constraints apply to every alert here:

- **Monitors are an operational control, not a bounty defense.** Immunefi's Pre-Impact Bug
  Monitoring policy does not accept "our bots/monitors catch it" as a rebuttal (#66410 mediation).
  Known-issue texts and replies argue from on-chain mechanisms (permissionless, bountied
  liquidation). Do not cite this service as the reason a report is invalid.
- **Read live parameters from the chain at startup and on change events.** Do not hard-code them.
  The values in §5 are dated references for sizing thresholds. The DIP values in the docs are not
  the live ones (precedents §4.1).

### Status of each monitor

| Status | Meaning |
|---|---|
| **DECIDED** | Agreed on the 2026-09-29 call, or promised to a reporter in a sent reply |
| **RECOMMENDED** | Proposed in a triage analysis for an issue we accepted without a contract change; no explicit team decision yet |
| **OPERATIONAL** | Required by the oracle/liquidator operation or by the 2026-10 key incident |

## 2. Inventory: where each monitor comes from

| ID | Monitor | Origin | Status |
|---|---|---|---|
| M1 | Stale final cluster event in a transaction | #91419 family (#92681, #92703, #93179, #93212, #93595, #94319, #79255); RESOLUTIONS §1 | DECIDED |
| M2 | Operator capacity blocking legacy migration | #88326 / KI #6 (dups #89354, #90044); reply to #88326 promises it | DECIDED |
| M3 | ETH cluster liquidation zone, fallback lag, zero balance | KI #5; #89748 (dups #90296, #90903), #66410, #81842, #84754, #87431, #94187 | DECIDED |
| M4 | ETH conservation-deficit scan | #89748 work item | DECIDED |
| M5 | EB desync via register/remove around a root | #94775 (reply: "we will keep monitoring this behaviour on mainnet"); v2.0.0 SSV-4 | DECIDED |
| M6 | Legacy SSV clusters: liquidation coverage | KI #5 SSV path; #91046, #81952, #79383 | RECOMMENDED |
| M7 | SSV-side solvency invariant (1 SSV tripwire) | #91046 | RECOMMENDED |
| M8 | Zero cSSV supply / seed stake | SPEC BUG-6, audit v2.0.0 SSV-6; #91876, #94971 (11 reports in the family) | RECOMMENDED |
| M9 | Oracle quorum health | `extra/eb-oracle-daily-cadence.md`; Aug 7–10 72 h outage; #93157 | RECOMMENDED (cadence review) / OPERATIONAL |
| M10 | Oracle slot rotation during an open round | #92676 (docs: rotate between rounds only) | RECOMMENDED |
| M11 | EB updater liveness | 2026-10 rotation; Aug 29 zero-update day | OPERATIONAL |
| M12 | Operational wallet balances and burn rate | 2026-10-05 Vault compromise; rotation runbook step 9 | OPERATIONAL (step 9 still open) |
| M13 | Compromised and retired addresses | 2026-10-05 Vault compromise | OPERATIONAL |
| M14 | Deposit front-running of liquidations | KI #3; #94187; `extra/20261020-issue/intent-analysis.md` | RECOMMENDED |
| M15 | Governance and parameter changes | KI #4 and KI #5 (re-evaluate on parameter change); #92676 | RECOMMENDED |
| M16 | Liquidator keeper health | `extra/20261020-issue/intent-analysis.md` §7, §12 | RECOMMENDED |

### Decided **not** to monitor (for completeness)

These were closed on docs only. The 2026-09-29 call explicitly ruled out notifications, outreach and
web-app/indexer changes. Do not build alerts for them unless the decision changes.

| Report | Why no monitor | Detection would be possible via |
|---|---|---|
| #78689 legacy partial removal keeps stored EB (8 candidate clusters) | Call decision: no notification to cluster owners; owner over-funds for one root window, self-corrects | `ValidatorRemoved` on a `VERSION_SSV` cluster followed by `ClusterMigratedToETH` before a fresh root is consumed |
| #72603 legacy whitelist slot survives revocation (19 stale grants, 2 owners) | Call decision: docs only, no outreach | `getOperatorById(id).whitelistedAddress` still set after `OperatorMultipleWhitelistRemoved` |
| #83363 promoted address survives whitelisting-contract removal | Zero live instances; docs only | — |
| #83904 / KI #7 fee-cap ceiling rounding | Call decision: no web-app or indexer change; 14-day declaration notice | `OperatorFeeDeclared` with fee = exact cap + 1 raw unit |
| KI #1 / KI #2 | Owner-controlled; operational guidance covers them | — |

## 3. Alert catalog

Severity scale: **page** = act now; **warn** = act within the working day; **info** = log and digest.

---

### M1 — Stale final cluster event (#91419 family) · DECIDED

**Behaviour.** If the cluster owner or the liquidator is a contract, a callback during an ETH transfer
can re-enter (for example `deposit` or `bulkRegisterValidator`) and emit a newer cluster tuple. The
outer function then emits its cached, older tuple **last**. Last-log-wins readers (our oracle updater,
liquidator, subgraph, web app) keep the stale tuple. Their calls then revert with
`IncorrectClusterState` (`0x12e04c87`). The cluster avoids EB fees and auto-liquidation until someone
re-emits the canonical tuple.

**Trigger.**
1. For every proxy transaction, group events that carry a `Cluster` tuple by
   `clusterId = keccak256(abi.encodePacked(owner, operatorIds))`. Those events are `ClusterDeposited`,
   `ClusterWithdrawn`, `ClusterLiquidated`, `ClusterReactivated`, `ValidatorAdded`, `ValidatorRemoved`,
   `ClusterBalanceUpdated` and `ClusterMigratedToETH`.
2. For groups with ≥ 2 events, validate the **last** tuple against storage at that block. A
   `SSVNetworkViews.isLiquidated(owner, operatorIds, cluster)` staticcall reverts on a hash mismatch.
3. **Alert** if the last tuple does not validate. Record which earlier tuple in the same receipt does
   validate; that is the repair tuple.
4. Also **alert** on every `updateClusterBalance` or `liquidate` tx from our addresses (§5) that
   reverts with `IncorrectClusterState`.
5. **Re-poison:** the same cluster is flagged again after a repair.

**Severity.** warn; page on re-poison or on a poisoned cluster that is also liquidatable.
**Baseline.** 0 poisoned clusters on mainnet; about 1,300 receipts with several events for one
cluster in history (scan `evidences/#91419/report-family-stale-event-scan.ts`).
**Runbook.** Take the canonical tuple from the same receipt and send a zero-value `deposit` with it.
Anyone can do this, and the repair re-emits the correct tuple. If the owner re-poisons, repair again
and escalate (the residual risk accepted on the call is this gas contest). No oracle, liquidator,
subgraph or web-app code change (call decision).
**Contract fix, parked:** five emit moves in SSVClusters (option A), next audited release.

---

### M2 — Operator capacity blocks legacy migration (#88326 / KI #6) · DECIDED

**Behaviour.** `validatorsPerOperatorLimit` (3,000) is checked against `ethValidatorCount` only.
Filling a public operator's ETH capacity blocks `migrateClusterToETH` and `reactivate` for legacy
clusters on that operator (`ExceedValidatorLimitWithData(operatorId)`, `0x639f5851`).

**Trigger.** For each operator with legacy SSV validators (`validatorCount > 0`):
- **Hard:** some active legacy cluster on the operator cannot migrate now
  (`ethValidatorCount + cluster.validatorCount > limit`) → **warn**.
- **Early:** ETH headroom (`limit − ethValidatorCount`) minus the operator's legacy validators
  < **900** (the shock size used by the scanner) → **info**. Raise it to warn if the operator is public.
- **Velocity:** registrations to the operator in the last 24 h exceed its remaining headroom → **info**.
- Any reverted `migrateClusterToETH` or `reactivate` with `ExceedValidatorLimitWithData` → **warn**.

**Data.** `getOperatorById` (counts, `isPrivate`), `getValidatorsPerOperatorLimit`, `ValidatorAdded`
logs, the SSV API v4 clusters list for legacy membership. Scanner: `evidences/#88326/report-operator-capacity-impact.ts`.
**Baseline (block 25,907,586).** 151 active legacy clusters / 418 validators; 0 blocked migrations;
no operator within 900 registrations; the closest target needed 2,562.
**Runbook.** Tell the operator it can go private until its legacy clusters migrate (KI #6 guidance).
Tell the cluster owner to re-shard or to self-liquidate (`liquidateSSV` as owner) if blocked. No
web-app change (call decision). The surface shrinks as legacy clusters migrate.

---

### M3 — ETH cluster liquidation zone (KI #5, #89748 family) · DECIDED

**Behaviour.** The contract never liquidates by itself. If no one calls `liquidate` /
`updateClusterBalance` after a cluster crosses its threshold, the balance floors at 0. Operator and
DAO earnings keep accruing on its vUnits ("phantom earnings"), and staking can pay them out.

**Trigger.** Every block or every few blocks, for every active ETH cluster:
- `isLiquidatable` turns true → **info** (onset; record the block).
- Still liquidatable after **N blocks** (default 50, per the 2026-10-20 actor analysis; the
  competitive market liquidates at 0–1 blocks) → **warn**. This is the "fallback lag" signal.
- Still liquidatable after **N2 blocks** (suggest 7,200 ≈ 1 day) → **page**.
- Active and `getBalance == 0` (computed zero) → **page**. Treat it as an incident: phantom accrual
  has started.
- **Pre-alert:** a cluster projected to cross within the next 24 h → **info** (digest).

**Data.** `SSVNetworkViews.isLiquidatable`, `getBalance`, `getBurnRate`. Use **view-computed**
balances; stored balances are stale between interactions (KI #5). Cluster set: SSV API v4 plus
`ValidatorAdded` / `ClusterLiquidated` logs.
**Live window.** `getLiquidationThresholdPeriod` = 42,960 blocks (≈ 6 days) between onset and zero.
The bounty is the whole remaining balance.
**Baseline (block 25,907,794).** 351 active ETH clusters / 61,931 validators; 0 liquidatable; 0 at
computed zero.
**Runbook.** Check that our keeper `0x16A5…` attempted the liquidation; if not, liquidate manually.  
Watch list: the self-operator cohort `0x2eD3FfB18DD32965c83eAD6e6ee35A5dE10DD637` (operators
2339–2351). Its liquidation waves run through late October, and its +10 % fee declarations can be
executed 2026-11-01 → 11-08. Re-run with `REFRESH=1 node evidences/mainnet-self-operators/followup-20261020/followup.mjs`.

---

### M4 — ETH conservation-deficit scan (#89748) · DECIDED

**Behaviour.** Phantom earnings show up as liabilities larger than the ETH the proxy holds.

**Trigger.** On a schedule (suggest after every committed root, i.e. 3×/day), compute

```
deficit = (Σ active ETH cluster getBalance + Σ operator getOperatorEarnings
           + getNetworkEarnings + staking ETH owed) − proxy ETH balance
```

**Any `deficit > 0` → page.** Log the surplus trend as info.

**Data / script.** `evidences/#89748/report-staking-insolvency-impact.ts` (public-view version of the
equation). **Baseline:** no deficit at block 25,907,794.
**Runbook.** Find the computed-zero or long-liquidatable clusters (M3) and liquidate them; that stops
the growth. Escalate: accrued phantom earnings are not clawed back.

---

### M5 — EB desync via register/remove around a root (#94775 / SSV-4) · DECIDED

**Behaviour.** A root is a snapshot at reference block S. An owner registers dummy keys after S, lets
the root be consumed (EB overwrites the snapshot with the real EB), then removes the dummies. Each
removal subtracts the 32 ETH baseline, so the cluster is billed below its real EB until the next root.
That is fee avoidance for one round. Repeating it every round costs about 3.1 M gas per run.

**Trigger.** For clusters with an explicit EB (stored EB > 32 ETH × validatorCount):
- `ValidatorAdded` events after a root's reference block S and before that root is consumed for the
  cluster (`ClusterBalanceUpdated` with `blockNum = S`), then `ValidatorRemoved` of about the same
  count after consumption → **warn**.
- The same cluster repeats the pattern in a later round (sustained form) → **page**.
- `ValidatorAdded` with malformed or empty `shares` → **info**. Hold it for correlation with the
  above (the PoC used empty shares).

**Baseline.** Expected 0 (exploitation-history scan, `evidences/#94775/`).
**Runbook.** One round self-corrects at the next root. On repetition, compute the fees avoided and
escalate to the team: the contract fix (bind the leaf to the validator count at S) is parked.

---

### M6 — Legacy SSV clusters: liquidation coverage (KI #5 SSV path) · RECOMMENDED

**Behaviour.** Legacy `VERSION_SSV` clusters cannot be topped up (`deposit` is ETH-only) and drain
monotonically. Unbacked SSV operator credit appears **only after** a cluster hits zero.
`liquidateSSV` opens ≈ 13.9 days (100,240 blocks) before zero and pays the whole balance.
Liquidated inside that window, the lifetime loss is **0 SSV**. At 30 / 90 / 365 days of lag it is
176 / 844 / 3,905 SSV.

**Trigger.** For every active legacy cluster:
- `isLiquidatableSSV` true for more than **7 days** → **warn** (half the window).
- `getBalanceSSV == 0` while active → **page**.
- Weekly digest: count of legacy clusters, validators and SSV/yr burn. The surface only shrinks.

**Baseline (2026-09-02).** 198 clusters with balance (155 burning, 424 validators; 43 inert),
4,062.82 SSV/yr aggregate burn.
**Coverage note.** Most `liquidateSSV` calls come from the third party `0x24e9a54d…dc18`; our keeper
had 1 success and 15 lost races. If that party stops, this monitor is the only signal.
**Runbook.** Liquidate with our keeper. The owner always has a self-exit (`liquidateSSV` as owner;
zero-value `migrateClusterToETH` when vc = 0).

---

### M7 — SSV-side solvency invariant (#91046) · RECOMMENDED

**Behaviour.** SPEC Global Invariant #2 on the SSV side reconciles to a **1.0 SSV** surplus (the
activation seed). Staker principal shares the same ERC-20 balance that pays SSV operator and DAO
earnings, and there is no SSV reserve guard. Any SSV-side leak, from any mechanism, lowers the surplus.

**Trigger.** Daily:

```
surplus = proxy SSV balance − ( cSSV.totalSupply()
                              + Σ outstanding unstake requests
                              + Σ live VERSION_SSV cluster balances
                              + Σ operator SSV earnings
                              + getNetworkEarningsSSV() )
```

**`surplus < 1 SSV` → page.** Any change in the surplus → info.

**Careful.** `requestUnstake` burns cSSV immediately, while the SSV stays until `withdrawUnlocked`.
So outstanding unstakes must be added (from `UnstakeRequested` / `UnstakedWithdrawn` logs or
`pendingUnstake`). `proxy SSV − cSSV supply` is **not** free headroom.
**Script.** `evidences/#91046/ssv-solvency-snapshot.mjs` (cluster set from `sum-ssv-cluster-balances.mjs`;
that needs `SSV_SUBGRAPH_API_KEY`).

---

### M8 — Zero cSSV supply / seed stake (SPEC BUG-6, SSV-6; #91876, #94971) · RECOMMENDED

**Behaviour.** `_syncFees` credits `accEthPerShare` only when `cSSV.totalSupply() != 0`
(`SSVStaking.sol:199-205`). ETH network fees synced while supply is zero are lost to stakers. Also,
`commitRoot` reverts with `ZeroCSSVSupply` when supply is zero, so the **EB oracle stops**.
Governance's seed staking position from the activation batch prevents both.

**Trigger.**
- `FeesSynced(newFeesWei == 0, …)` → **page**. It is emitted with 0 only on this branch, because the
  function returns early when there are no new fees.
- The seed holder emits `UnstakeRequested` → **page**.
- `cSSV.totalSupply()` below a floor (suggest the seed amount × 2) → **warn**.
- Any `commitRoot` revert with `ZeroCSSVSupply` → **page** (also caught by M9).

**Runbook.** Any `stake()` closes the window immediately (there is no time gate). Ask governance to
restore the seed position.

---

### M9 — Oracle quorum health · RECOMMENDED / OPERATIONAL

**Behaviour.** Roots commit 3×/day (75-epoch phases; commits land at 00:03 / 08:03 / 16:03 UTC,
about 65 blocks after the reference block) with 3-of-4 agreement (`quorumBps` 7500). The fourth vote is
normally skipped once quorum is on-chain. The oracle has **no retry**: a failed target waits for the
next one. Mainnet record: 83 rounds committed with a dissenting slot (slot 3, 2026-07-15 → 08-29),
then 8 failed rounds and a **72 h outage** (2026-08-07 16:03 → 08-10 16:03). The quorum margin was
zero for six weeks before it.

**Trigger.**
- `WeightedRootProposed` for a `blockNum` with no `RootCommitted` within 30 min → **page** (failed round).
- No `RootCommitted` for > 9 h → **page** (cadence 8 h + margin).
- Two different `merkleRoot` values proposed for the same `blockNum` (dissent) → **warn**. Three
  dissenting rounds in a row from the same slot → **page**: the quorum margin is gone.
- A round committed with fewer than 3 distinct slots voting for the committed root → **page**
  (cannot happen unless `quorumBps` changed).
- A slot that has not voted for the committed root in any of the last 6 rounds (48 h) → **warn**.
  This tolerates the routine skipped fourth vote.
- `commitRoot` from a slot address reverts (`NotOracle`, `AlreadyVoted`, `StaleBlockNumber`,
  `ZeroCSSVSupply`) → **warn**.

**Why it matters.** All slots must run the same tree construction (duplicate-last, **not** OZ
`StandardMerkleTree`; #93157). A slot on a different build produces a valid-looking but different
root and silently blocks quorum; this monitor catches that as dissent.
**Data.** `WeightedRootProposed(merkleRoot, blockNum, accumulatedWeight, quorum, oracleId, oracle)`,
`RootCommitted(merkleRoot, blockNum)`, `getActiveOracleIds`, `getOracle`, `getQuorumBps`. Scripts:
`evidences/eb-oracle-cadence/scan*.mjs`.
**Note.** If the cadence moves to daily (`extra/eb-oracle-daily-cadence.md`), this monitor and an
oracle retry are preconditions, and the 9 h threshold becomes 25 h.

---

### M10 — Oracle slot rotation during an open round (#92676) · RECOMMENDED

**Behaviour.** `hasVoted` is keyed by slot id, not address (`SSVDAO.sol:188-189`), and `replaceOracle`
does not migrate in-flight votes. A mid-round reassignment can let one address vote twice on the
same `(blockNum, root)`, or lock a new key out of the open round.

**Trigger.** `OracleReplaced(oracleId, oldOracle, newOracle)` while a `WeightedRootProposed` round is
open and not yet committed → **page**. Also, `newOracle` already holds another slot → **page**.
Every `OracleReplaced` → **info**, posted to the team channel.
**Runbook.** Governance rotates only between rounds and avoids 00:03 / 08:03 / 16:03 UTC (2026-10
rotation runbook). Confirm `getCommittedRoot(blockNum)` for the open round.

---

### M11 — EB updater liveness · OPERATIONAL

**Behaviour.** Our updater (sender `0x16A5…`, the slot-4 key) runs when it sees `RootCommitted`. It
sends `updateClusterBalance` only for clusters whose on-chain EB differs from the leaf (27–43 per
round). It keeps `lastProcessedBlock` in memory, so there is no backfill; it self-heals at the next
root. On 2026-08-29 it sent 0 updates for three rounds.

**Trigger.**
- A `RootCommitted` with **0** `updateClusterBalance` txs from `0x16A5…` within 60 min → **warn**.
  First check the oracle API proof endpoint for clusters whose on-chain EB differs from the leaf. If
  any exist → **page**.
- Two consecutive committed rounds with 0 updates → **page**.
- Revert rate on our updates > 10 % in a round → **warn**. `IncorrectClusterState` reverts go to M1.
  Reverts because another sender already set the same EB are benign (seen 10-12, while Ethernodes
  was also sending).
- `updateClusterBalance` from a sender other than `0x16A5…` → **info** (Ethernodes `0x28bE…` covered
  2026-10-06 → 10-12).
- Weekly full-state check: every active ETH cluster's `getEffectiveBalance` (Views) equals its leaf
  EB from `https://ssv-oracle.ssv.network/api/v1/proof/{clusterId}` at the latest root →
  any mismatch older than one round → **warn**.

**Baseline.** 2026-10-12: 506/506 clusters matched the leaf. Since 10-13 we are the only sender
(about 100 updates/day, 0 reverts).

---

### M12 — Operational wallet balances and burn rate · OPERATIONAL (rotation step 9)

| Wallet | Role | Burn (measured) | Alerts |
|---|---|---|---|
| `0x16A503C901615E6e21AA78cFcaFf11822fc2b617` | Oracle slot 4 (`commitRoot`) + EB updater (`updateClusterBalance`) | 15.5 Mgas/day ≈ **0.0177 ETH/day** at ~1.15 gwei (10-13 → 10-20; max day 0.0202). About 98 % is updates (~150k gas each), about 3 `commitRoot`/day | **warn** runway < 14 days; **page** runway < 7 days; **page** balance < 0.15 ETH (rotation doc); **warn** daily spend > 0.04 ETH |
| `0x0431371AD8eDA67BA2EF6489aB003B3A7F0d95E0` | Liquidator (ETH and SSV pods share it) | bursty; worst case all legacy clusters ≈ 0.165 ETH at 10 gwei | **warn** < 0.2 ETH (funding recommendation); **page** < 0.05 ETH |

**Runway = balance ÷ trailing 7-day average daily spend.** Compute it from gas used × the current
base fee as well, because gas price dominates. At 1.15 gwei the oracle wallet lasts 19.5 days; at
5 gwei, 4.5 days.
**Status 2026-10-20 22:00 UTC.** `0x16A5…` balance 0.3454 ETH → about 11 days to 0.15 ETH (≈ Oct 31), about 19.5 days to 0 (≈ Nov 9).
Its only funding so far was 0.5 ETH on 10-07, from `0xD63983cEA76a13d7B01d8F7F14D5837330382d42`.

---

### M13 — Compromised and retired addresses · OPERATIONAL

The 2026-10-05 Vault compromise leaked the old slot-4 key; the old liquidator key was drained on 10-06.

| Address | Was | Alert |
|---|---|---|
| `0x3A98EE5f80268Ed91F8A5880d93468b76a9F3bB4` | old slot 4 + updater (key leaked) | any **incoming** value → **page** (a stale top-up would be stolen); any `commitRoot`/`updateClusterBalance` attempt → **page** |
| `0x4BC656B34De23896fa6069C9862F355b740401aF` | old liquidator (drained) | any incoming value → **page** |
| `0xaeE058cCA5EED5470b4b48c159045b7C25ddb80f`, `0x0144aE2456FbCF38Ba11e4ECF25F03A80A69F134` | attacker / consolidation | any interaction with SSV contracts or our wallets → **page** |

Mute old addresses in every other monitor (rotation step 9).

---

### M14 — Deposit front-running of liquidations (KI #3, #94187) · RECOMMENDED

**Behaviour.** `deposit` is permissionless; a 1-wei deposit changes the cluster tuple, so a pending
liquidation carrying the old tuple reverts. Sustained, this can keep a cluster alive past zero (feeds M3/M4).

**Trigger.** In the same or previous block as a reverted `liquidate` / `liquidateSSV` on a liquidatable
cluster, a `ClusterDeposited` for that cluster with a value below its burn for 1 block → **info**.
Three or more times on the same cluster within 24 h → **warn**. Raise to **page** if the cluster
also reaches computed zero.

---

### M15 — Governance and parameter changes · RECOMMENDED

KI #4 requires the `uint64` headroom to be re-checked when the validator cap or fees change. KI #5
requires parameter changes to keep enough time and bounty for liquidators. KI #6 notes there is no
setter for the per-operator limit.

**Trigger (info, posted to the team channel; warn if not announced):** `NetworkFeeUpdated`,
`NetworkFeeUpdatedSSV`, `LiquidationThresholdPeriodUpdated`, `LiquidationThresholdPeriodSSVUpdated`,
`MinimumLiquidationCollateralUpdated`, `MinimumLiquidationCollateralSSVUpdated`,
`OperatorFeeIncreaseLimitUpdated`, `OperatorMaximumFeeUpdated`, `MinimumOperatorEthFeeUpdated`,
`DeclareOperatorFeePeriodUpdated`, `ExecuteOperatorFeePeriodUpdated`, `QuorumUpdated`,
`MinBlocksBetweenUpdatesUpdated`, `CooldownDurationUpdated`, `OracleReplaced` (M10), proxy
`Upgraded` / module updates.
On any of these, re-read the live parameters used by M3, M6 and M12. A module or implementation change
also means `scripts/check-deployment.mjs` will report drift and the triage pin must be updated.

---

### M16 — Liquidator keeper health · RECOMMENDED

From the 2026-10-20 review of the keeper (`extra/20261020-issue/intent-analysis.md` §7, §12). Our keeper
is the last resort, and it loses public-mempool races (MEV searchers copy its transactions).

**Trigger.**
- Keeper error log: a liquidation send that returned `undefined` / swallowed revert → **warn**.
  Today reverts are swallowed, so a stale-tuple cluster is never rescheduled and never alerts.
- Duplicate sends for the same cluster in one block → **info**.
- The keeper's tracked cluster count differs from the active ETH cluster count by more than 0 →
  **warn**. If `SSV_CLUSTER_MIGRATION_BLOCK` is unset, natively registered ETH clusters are never
  tracked.
- Daily: number of liquidations won, lost and reverted → info digest.

The real safety net is M3 (liquidatable for > N blocks), not the keeper winning races.

## 4. Implementation notes

- **Event source:** proxy `0xDD9BC35aE942eF0cFa76930954a156B3fF30a4E1` (all modules emit from it).
  Views `0xafE830B6Ee262ba11cce5F32fDCd760FFE6a66e4`.
- **Cluster set:** SSV API v4 (`https://api.ssv.network/api/v4/mainnet/clusters`), reconciled against
  logs. Never trust the last log of a transaction for the tuple (M1).
- **RPC:** historical `eth_call` needs an archive endpoint (publicnode fails a few thousand blocks
  back; drpc works). Blockscout v2 for address histories; v1 `txlist` is rate-limited.
- **Reorgs:** confirm alerts on blocks ≥ 2 deep, except M13 and M12 pages.
- **Reproducible baselines:** every number above comes from a script under `evidences/`. Rerun it
  before changing a threshold.

## 5. Reference: addresses and live parameters

| Item | Value | As of |
|---|---|---|
| DAO Safe (owner of proxy, Views, SSV token) | `0xb35096b074fdb9bBac63E3AdaE0Bbde512B2E6b6` (5 of 9) | 2026-10-05 |
| SSV token / cSSV | `0x9D65fF81a3c488d585bBfb0Bfe3c7707c7917f54` / `0xe018D31F120A637828F46aFD6c64EC099d960546` | — |
| Oracle slot 1 / 2 / 3 / 4 | `0xC61f7bd9EE5a3D011cAf47aa0e5411f720593920` / `0xc07332E05CEc1c4896555a6D10361233fdf14422` / `0x28bEa5B242362974d5DDb8f17a1E0e525446960B` (Ethernodes) / `0x16A503C901615E6e21AA78cFcaFf11822fc2b617` (SSV) | 2026-10-13 |
| Quorum | 7500 bps (3 of 4) | 2026-10-13 |
| Network fee | 3.3666 gwei / block per 32 ETH | block 25,933,264 |
| Default operator fee | 1.7788 gwei / block | block 25,933,264 |
| Liquidation threshold (ETH) | 42,960 blocks (≈ 6 d) | block 25,933,264 |
| Minimum liquidation collateral (ETH) | 0.000325 ETH | block 25,933,264 |
| Liquidation threshold (SSV) | 100,240 blocks (≈ 13.9 d) | 2026-09-02 |
| Validators per operator | 3,000 (no setter) | — |
| `minBlocksBetweenUpdates` | 0 | 2026-09-08 |
| Oracle cadence | 75 epochs; commits 00:03 / 08:03 / 16:03 UTC | 2026-09-08 |

## 6. Open questions

- **M8:** which address holds the governance seed stake, and how much is it? (UPGRADE_PLAYBOOK "Initial SSV stake".)
- **M9 / M11:** who gets paged for the oracle (ssv-oracle owner) and for the liquidator (Nir)? Slot 3 belongs to Ethernodes; do we alert them directly?
- **M12:** the rotation doc says 0.15 ETH and an earlier note said 0.05 ETH for the low-balance alert. This spec uses 0.15 plus runway-based alerts. Confirm.
- **M6, M7, M8, M10, M14, M15, M16** are recommendations from analyses, not call decisions. Confirm which ones to build.
- **M3:** confirm N = 50 blocks for the fallback-lag warning.
