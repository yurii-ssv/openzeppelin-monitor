# deploy/

Tracked Monitor config templates and the offline gates that protect them. Nothing here is a secret;
secrets stay `{"type":"environment","value":"<NAME>"}` references.

| Path | What it is |
|---|---|
| `config/` | monitor, trigger, network and filter templates, rendered by `render.sh` |
| `render.sh` | copies `config/` into a config directory, then runs `check-abi.sh` and the Monitor's `--check` |
| `check-templates.sh` | structural checker (S0–S7); S7 runs `check-abi.sh` |
| `check-abi.sh` | ABI gate A1–A9, see below |
| `check-m15-coverage.sh` | M15/M10 events are on the Discord monitor and on no Telegram route |
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

## M13 coverage boundary

**Status: transaction-level only. This is a limit of the OpenZeppelin Monitor, not a template defect, and
no template can close it.** The Monitor evaluates `transactions` conditions on the transaction's outer
`from`, `to`, `value`, `hash`, gas fields and `input`, and `events`/`functions` conditions on the
monitored contract's own logs and calldata. It has no trace or balance data, so value that moves through an
internal call is invisible to it. Spec M13 says "any incoming value" to the retired keys and "any
interaction" of the attackers with SSV contracts or our wallets; that is only met for the direct cases below.
QA attempt 2 (`plans/monitoring-service/qa/cp1a-attempt-2.md`, F1b) demonstrated the gap on a local fork.

### Covered (monitors `ssv_retired_addresses_monitor`, `ssv_retired_updater_attempt_monitor`, `ssv_attacker_interaction_monitor`)

| M13 row | What fires | Template condition |
|---|---|---|
| retired slot 4 `0x3A98…3bB4`, retired liquidator `0x4BC6…01aF` | a **direct** transaction whose `to` is the key and `value > 0` (success or revert) | `(to == 0x3A98… OR to == 0x4BC6…) AND value > 0`, status `Any` |
| old slot-4 key attempts | a direct transaction `from == 0x3A98…` calling `commitRoot` / `updateClusterBalance` on the proxy, reverted or not | `functions` on the proxy + `from == 0x3A98…`, status `Any` |
| attackers `0xaeE0…b80f`, `0x0144…F134` | a **direct** transaction between either attacker and any of: proxy, Views, SSV token, cSSV, DAO Safe, our two operational wallets (`0x16A5…`, `0x0431…`), in either direction as listed in the template, including zero value and reverted ones | one `transactions` expression on `from`/`to`, no `value` predicate, status `Any` |

Measured: 42/42 attacker × SSV-contract/wallet × {0, 1 wei, reverted} direct cases fire once; wallets sending
0/1 wei to an attacker 8/8; direct transfers to each retired key 1 (in) / 0 (out) / 0 (zero); an old-key
`commitRoot` attempt 1, other sender 0 (QA attempt 2 §1 F1b; `tests/m13-fork-regression.sh`, 15 cases, all pass).

### Not covered (cannot be matched by any Monitor template)

| Missed observation | Why | QA reproducer (attempt 2, §1 F1b) |
|---|---|---|
| **Internal CALL carrying value to a retired key** (a contract forwards ETH to `0x3A98…` or `0x4BC6…`) | outer `to` is the contract, outer `value` is not the forwarded amount | unrelated sender → helper → retired slot 4, 1 wei: block 26240413, expected 1, actual 0 (tx receipt success, key balance +1 wei); same to the retired liquidator, block 26240418 |
| **Internal CALL by an attacker-controlled path** (attacker → intermediate contract → SSV contract or our wallet) | outer `to` is the intermediate contract, which is not on the target list | attacker 1 → helper → our oracle wallet 1 wei, block 26240404; attacker 2, block 26240405; minimal repro `0xaeE0…b80f` → helper `0x…dead2` (runtime `0x6000600060006000600173<TARGET>5af15000`) → `0x16A5…2b617` and → `0x3A98…3bB4`, 0 / 0 matches, txs `0xfe6a601b…06a1` and `0xac5c2226…d572` |
| **Constructor-created transfers** (a contract creation, `to` empty, whose constructor sends value to a watched address, or that calls an SSV contract) | the creation has no `to`; its constructor's CALLs are not transaction fields | attacker 1 constructor → our liquidator wallet 1 wei, block 26240406; attacker 2, block 26240407 |
| **`SELFDESTRUCT` value** to a retired key or wallet (a contract destroyed with the key as beneficiary) | no call, no log, no transaction-level `value` | same class as the internal CALL rows; not separately reproduced by QA |
| Interactions that reach SSV contracts only through a third party (a router, a multisig, a bundler) | outer `from`/`to` are the third party | same helper class as above |

None of these is detectable from `from`/`to`/`value`/logs/calldata of the transaction. A page for them needs
a component that sees balances or traces, e.g. watching the balance deltas of the two retired keys and of
our wallets in the stateful worker, or a trace-capable RPC (`debug_traceTransaction` / `trace_block`)
behind a custom filter. Whether that moves into the worker is a scope decision for the coordinator and Marco; this
directory changes no scope for it, and CP1A must not be read as delivering "any interaction / any incoming
value" for M13.
