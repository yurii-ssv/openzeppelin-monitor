# deploy/

Tracked Monitor config templates and the offline gates that protect them. Nothing here is a secret;
secrets stay `{"type":"environment","value":"<NAME>"}` references.

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
