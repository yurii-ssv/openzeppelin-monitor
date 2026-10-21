FAIL

Independent CP1A QA, attempt 2 after repair 1, on frozen head `9b3e7499be32035c70aabe29adf0aba9021ffb4c` (repair code `10c7a8f9dddde10a42ede04a1a86528030ee07a1`), 2026-10-21. Branch `mtabasco/ms-qa-cp1a-3`; task `task_52e3a6bc2048`, dispatch `ctx_ff308425cb6e`, terminal `term_9efcc885-6424-4329-a71c-1beaedcc777e`; dispatched Codex gpt-6.1-sol high. I implemented none of this checkpoint and repaired no implementation.

All ten assigned V rows and the CP0-QA follow-up have explicit measurements below. Repair 1 fixes the original direct-call, missing-input, named-event/function, outgoing-transfer and M10/M15 routing reproducers. Nevertheless, M13 still misses actual incoming internal transfers and attacker interactions through intermediate contracts/constructors, and the required ABI gate accepts an incorrect indexed-argument layout that suppresses the seed-unstake page. CP1A remains QA FAILED.

## 1. Findings, ranked by severity

### F1b — HIGH: M13's transaction-level implementation misses internal interactions and incoming value

Owner: CP1A/coordinator. This is the remaining scope of original F1/F4, independently demonstrated after the direct-call repairs. Spec M13 says **any interaction** of either attacker with SSV contracts/our wallets and **any incoming value** to either retired key. A transaction's outer `from`, `to`, and `value` cannot describe its internal calls. Restricting these guarantees to direct transactions is not an accepted deviation in the dispatch.

Minimal reproduction actually rerun:

```sh
export PATH=$HOME/.foundry/bin:$PATH
anvil --fork-url https://eth.drpc.org --fork-block-number 26240350 --port 28547 --silent
# In a second shell in this QA tree; external build adapter described in section 3:
python3 /tmp/ms-qa-cp1a/attempt2/minimal-internal.py
```

The script prepares notification-free `.local-dry/config`, impersonates attacker `0xaeE058cCA5EED5470b4b48c159045b7C25ddb80f` locally, funds it locally, and installs this runtime at helper `0x00000000000000000000000000000000000dead2`:

```text
0x6000600060006000600173<TARGET: 20 address bytes>5af15000
```

This runtime CALLs TARGET with 1 wei and returns. Exact local RPC sequence, with TARGET first our oracle wallet `0x16A503C901615E6e21AA78cFcaFf11822fc2b617`, then retired slot 4 `0x3A98EE5f80268Ed91F8A5880d93468b76a9F3bB4`:

```text
save eth_getCode(helper, "latest")
anvil_setCode(helper, runtime above)
anvil_impersonateAccount(attacker)
anvil_setBalance(attacker, hex(10**20))
eth_getBalance(TARGET, "latest")
eth_sendTransaction({from: attacker, to: helper, value: "0x1", gas: "0x7a120"})
poll eth_getTransactionReceipt(hash) until mined; require status == "0x1"
eth_getBalance(TARGET, "latest"); require after == before + 1
anvil_setCode(helper, original_code) in finally; verify exact restoration
```

Replay commands actually used after preparation:

```sh
(cd .local-dry && MAINNET_RPC_URL=http://127.0.0.1:28547 \
 /tmp/ms-qa-cp1a/target/release/openzeppelin-monitor \
 --monitor-path config/monitors/ssv_attacker_interaction_monitor.json \
 --network evm_ethereum_mainnet --block 26240430)
(cd .local-dry && MAINNET_RPC_URL=http://127.0.0.1:28547 \
 /tmp/ms-qa-cp1a/target/release/openzeppelin-monitor \
 --monitor-path config/monitors/ssv_retired_addresses_monitor.json \
 --network evm_ethereum_mainnet --block 26240431)
```

Expected: one page match each. Actual: **0 / 0 matches**, both exit **0**, both **0 ERROR**. The first tx is `0xfe6a601b5ff72c803cf97467092a4979883e702157b3f5e6dbe5980de8e206a1`; the second is `0xac5c2226203966885abef3de3676cfd7ac38c549629b9c8ad2b8270f07f8d572`. Recipient balances increased by exactly 1 wei in each case, independently of the Monitor result. These are LOCAL fork txs, not mainnet sends.

Broader independent harness `python3 /tmp/ms-qa-cp1a/attempt2/fork.py` collected 70 M13 cases; `python3 /tmp/ms-qa-cp1a/attempt2/audit.py` exits **1**, reporting six mismatches:

| Local block | Scenario | Expected / actual | Receipt / replay exit / ERROR |
|---:|---|---|---|
| 26240404 | attacker 1 → helper → our oracle wallet, 1 wei | 1 / 0 | success / 0 / 0 |
| 26240405 | attacker 2 → helper → our oracle wallet, 1 wei | 1 / 0 | success / 0 / 0 |
| 26240406 | attacker 1 constructor → our liquidator wallet, 1 wei | 1 / 0 | success / 0 / 0 |
| 26240407 | attacker 2 constructor → our liquidator wallet, 1 wei | 1 / 0 | success / 0 / 0 |
| 26240413 | unrelated sender → helper → retired slot 4, 1 wei | 1 / 0 | success / 0 / 0 |
| 26240418 | unrelated sender → helper → retired liquidator, 1 wei | 1 / 0 | success / 0 / 0 |

The 64 other cases satisfy their expectations. In particular, **each attacker × all seven targets** (proxy, Views, SSV, cSSV, DAO Safe, both current operational wallets) was exercised with zero value, one wei, and forced receipt failure: **42/42 direct cases match once**, 30 reverted and 12 successful. Both wallets sending zero/one wei to each attacker give **8/8 matches**. Unrelated counterparty controls and a constructor without a watched interaction give zero. Direct incoming/outgoing/zero/self transfers to each retired key give **1/0/0/1**; a literal self-transfer fits the current `to` + positive-value rule, although it is not a net top-up. Old-key `commitRoot` attempt and other-sender control give **1/0**.

Evidence: `/tmp/ms-qa-cp1a/attempt2/evidence/fork/{checks.json,fork.json,*trace.json}`, `minimal-internal.json`, and corresponding logs. Traces show the actual internal CALL; balance deltas verify funds moved. The collection harness itself exits 0 to save all observations, so its exit is **not** used as a passing assertion; the separate audit is explicitly red. An initial compact-reproducer run did not wait for receipt mining and stopped on a null receipt; after adding polling, it reproduced the counterexample. ANSI normalization was added before the compact script's string assertion. The original 70-case evidence already normalized logs and is unaffected.

**Judgment on implementer decision (b): unacceptable against the current spec.** Transaction-level coverage is correctly disclosed but materially narrows M13, and a public helper/constructor bypasses it. A coordinator-approved scope change or a repair that covers the missing observations is required; disclosure alone does not earn VERIFIED. No Rust or implementation repair was made by QA.

### F8b — HIGH: render gate accepts an ABI indexed-layout mismatch that loses the M8 seed page

Owner: CP1A/coordinator. Original F8's nonexistent-name repro is repaired, but ABI consistency is still incomplete. The gate compares canonical `name(type,...)` and expression names; those comparisons omit `indexed` layout.

Minimal isolated mutation and gate commands (external copy only):

```sh
mkdir -p /tmp/ms-qa-cp1a/attempt2/abi-repro
cp -R deploy /tmp/ms-qa-cp1a/attempt2/abi-repro/deploy
python3 - <<'PY'
import json,pathlib
p=pathlib.Path('/tmp/ms-qa-cp1a/attempt2/abi-repro/deploy/config/monitors/ssv_seed_stake_monitor.json')
j=json.loads(p.read_text())
e=next(x for x in j['addresses'][0]['contract_spec'] if x.get('name')=='UnstakeRequested')
e['inputs'][0]['indexed']=False
e['inputs'][1]['indexed']=True
p.write_text(json.dumps(j))
PY
bash /tmp/ms-qa-cp1a/attempt2/abi-repro/deploy/check-templates.sh
MONITOR_BIN=/tmp/ms-qa-cp1a/target/release/openzeppelin-monitor \
 MAINNET_RPC_URL=https://eth.drpc.org \
 bash /tmp/ms-qa-cp1a/attempt2/abi-repro/deploy/render.sh \
 --out /tmp/ms-qa-cp1a/attempt2/abi-repro-output/config
```

The actually measured equivalent scratch root was `attempt2/mirror`, case `indexed-order` in `static.py`. Expected: nonzero for ABI layout inconsistent with the contract/event. Actual: checker **0 / OK (32 monitors)**; render **0 / --check passed / 0 ERROR**. Event name, canonical types, monitored address, predicates and input names remain unchanged; only which two arguments are indexed is exchanged.

Execution proof: on Anvil, emit an ABI-correct `UnstakeRequested(address,uint256,uint256)` from the proxy with indexed `user` = DAO Safe and data words 1, 2. Replay block **26240425** through the seed monitor: original ABI **1 match**, the same indexed-layout mutation **0**, byte-for-byte restoration **1**; all three exits **0**, **0 ERROR**. The runtime used was `60016000526002602052` + `PUSH32(DAO Safe)` + `PUSH32(keccak256(event signature))` + `60406000a200`. Original proxy code was saved and restored in `finally`, then compared exactly. Commands:

```sh
python3 /tmp/ms-qa-cp1a/attempt2/static.py
python3 /tmp/ms-qa-cp1a/attempt2/fork.py
# raw replays are B --monitor-path config/monitors/ssv_seed_stake_monitor.json
# --network evm_ethereum_mainnet --block 26240425, from .local-dry
```

Evidence: `evidence/{indexed-order.log,indexed-order-render.log}` and `evidence/fork/{unstake-dao,unstake-indexed-swapped,unstake-indexed-restored}.log`. A current unmodified template has the correct indexing; this is a required validation falsifier failure, not a claim that today's template already has the swapped layout. It prevents credit for V1A.1's full ABI-mismatch requirement.

### Owned non-gating deferrals F5/F6/F7 — unchanged and re-confirmed

These do not contribute to this attempt's FAIL, per the dispatch.

- **F5 LOW, legacy Discord names, coordinator/CP3 pending owner decision.** `jq -r '..|objects|select(.type?=="environment")|.value' deploy/config/triggers/*.json | sort -u` still yields **15 legacy Discord names** besides `DISCORD_WEBHOOK_INFO`. `.env.example`, compose and existing Discord definitions retain the legacy names. No value/secret was printed. For example the DAO info twin still references `DISCORD_WEBHOOK_DAO_SUCCESS`.
- **F6 LOW, Rust Success label, Rust owner/CP3.** Replay `ssv_updater_other_sender_monitor` at mainnet **26177500**: **1 match**, exit **0**, **0 ERROR**, matched status `Success`. Independent `eth_getTransactionReceipt` for `0x8563fbf3473c699aaddad6acde375b649e75309ac29474072ec829171c396f99` returns **`0x0`** on drpc. The raw label is still wrong. Spec M11 asks for other-sender info regardless of outcome; this inherited behavior is explicitly non-gating here.
- **F7 LOW, runtime inbox ignore, coordinator/CP3.** `git check-ignore data/inbox/failed-tx.jsonl` returns **1**, no rule; `.local-dry/` is ignored. No production inbox file was created.

`git diff b4b15a4063874ac4bdf3eb751ea8c1a255e76ee0 HEAD -- src .gitignore .env.example docker-compose.yaml` was empty before QA writes; all three underlying deferred areas remain unchanged. The new attacker trigger and retired-trigger wording are intended repair changes, so no claim is made that all trigger files are unchanged.

## 2. Claims I could NOT confirm

- **Full M13 coverage** and **full ABI-mismatch rejection** are not confirmed: F1b/F8b are concrete counterexamples. The exact narrower original reproducers now behave correctly, as section 4 records.
- **Historical live `config/`, real notification delivery, container startup, catch-up over an outage, and production persistence** were not inspected/exercised. They are not earned by compose syntax validation. User-owned `config/`, `config.zip`, and `.env*` were not edited. CP3 owns composed operation.
- **The exact intermediate red-baseline results on the old implementation** were not rerun by checking out/mutating the old tree. I reran the attempt-1 reproducers against the repaired frozen head and ran both current executable suites. The implementer's 31/0/0 self-test and 15/0 fork-suite claims are independently reproduced, but do not prove the broader guarantees.
- **RPC completeness from a successful empty response** cannot be confirmed by the Monitor. A local proxy returned the real block but replaced `eth_getLogs` with `[]`: positive FeesSynced control dropped **1 → 0 matches**, exit **0**, **0 ERROR**; `null` instead returned exit **1**, **2 ERROR**; restoring real logs returned **1**, exit 0. This falsifier exposes reliance on the RPC's truthful data, not evidence that an empty result proves no events. Mainnet rows below used independently fetched nonempty blocks/logs and transaction/receipt checks, not a stub returning arbitrary success. No live row is credited from the injected empty response.
- No required V row is NOT RUN after network access was enabled. Initial sandbox RPC probes were environment failures, recorded separately below. No venv was needed per dispatch.

## 3. Verification matrix

Preflight: HEAD exactly `9b3e7499be32035c70aabe29adf0aba9021ffb4c`; initial porcelain empty; **353 GiB** available. Cargo, anvil, cast, Docker Compose, jq and Python present. `CARGO_TARGET_DIR=/tmp/ms-qa-cp1a/target cargo build --release --locked` exited **0**, producing `/tmp/ms-qa-cp1a/target/release/openzeppelin-monitor`. No install occurred. Orca's sandbox connection initially returned `runtime_access_denied`; the same executable worked with allowed runtime access. RPC sandbox probes for the five live row families returned **curl 6**, `Could not resolve host: eth.drpc.org`; these are **NOT RUN due to environment**, never passes. All full live rows then ran with network allowed.

**Command/path adaptation.** `B` below means `/tmp/ms-qa-cp1a/target/release/openzeppelin-monitor`. `D` means `MAINNET_RPC_URL=https://eth.drpc.org bash /tmp/ms-qa-cp1a/attempt2/local-dry-run.sh`. This is an external byte-equivalent copy of `scripts/local-dry-run.sh` except its ROOT assignment names this worktree and its BIN assignment names B. Every replay includes `--network evm_ethereum_mainnet --block BNUMBER`; rendered mutations run raw B from `.local-dry`. All production/TEST notification variables were removed for replay, and only `local_log` and the inbox script remained. `MONITOR_BIN=B` is used for render. The committed fork script was independently run from an external copy with only ROOT, binary and adapted dry-run paths changed; its 15 assertions were unchanged. No implementation file was edited to accommodate the external build.

| V-ID | Actual command / measurement | Exit | Outcome |
|---|---|---:|---|
| V1A.1 | `MONITOR_BIN=B MAINNET_RPC_URL=https://eth.drpc.org deploy/render.sh --out .local-dry/config`; then B `--check` with safe shape-valid child-only placeholders | 0 / 0 | **FAIL required ABI falsifier; positive PASS.** 32 active monitors, 1 network, success marker, 0 ERROR. Nonexistent name/type/function and garbage controls reject; indexed-layout mismatch still exits 0 and loses a real-shaped seed event, F8b. |
| V1A.1f | `python3 /tmp/ms-qa-cp1a/attempt2/gate.py`: remove final rendered closing brace and run raw B; equivalent scratch-template render; `{}` and `null` template controls; restore | raw 0; gates 1; restored 0 | **PASS under accepted deviation.** Malformed raw config has **2 ERROR**, no success marker. Dangling-trigger render has raw binary exit 0, **3 ERROR**, no success marker, and gate exit **1**, so log gating is independently exercised past the ABI layer. |
| V1A.2 | V0.6 jq plain scan on all trigger/network templates; exact V0.3 `git grep -nE` from the plan | 0 / 1 | **PASS:** empty plain scan; grep exit 1 = **0 matching rows**. Required-field/schema follow-up separately green below. |
| V1A.3 | D `--monitor-path config/monitors/ssv_failed_tx_inbox.json --network evm_ethereum_mainnet --block 26239235` | 0 | **PASS:** 1 match, inbox +1, **11 exact §6 keys**, selector **0xbf0f2fb2**, schema 1, block 26239235, failed status and all transaction/hash/address fields verified. Tx `0xc0d9c1a9ff6958cf15bb5a448ceb5ac04b9104d9175a7ad001d8557da8aeb684`, independent receipt 0x0. Success control 26239259: 0/+0. |
| V1A.4 | D `--monitor-path config/monitors/ssv_updater_other_sender_monitor.json --network evm_ethereum_mainnet --block 26177494` | 0 | **PASS:** 1 match, 0 ERROR; successful tx `0xa496ebcc8af80e68f2c0ea74d2317287790d3b25b73eb4e5aa1db58798865c84`, sender `0x28bE…`, independently verified receipt 0x1. |
| V1A.4f | D same monitor at **26239693**; rendered sender expression set to null; restore bytes and repeat raw B | 0 / 0 / 0 | **PASS:** **0 → 1 → 0** matches, 0 ERROR. Independent block read has exactly one proxy update, from `0x16A5…`, input prefix `0x4f4d1209`. Mutation makes control red as required. |
| V1A.5 | D `--monitor-path config/monitors/ssv_retired_addresses_monitor.json --network evm_ethereum_mainnet --block 25986346` | 0 | **PASS positive row:** 1 match, 0 ERROR; successful incoming **3073946110471 wei**, tx `0x8bb7b675de58a419274c05320100d3b234683c30023fc335af4dcad57db98db3`, to retired slot 4. Broader internal-value coverage fails F1b. |
| V1A.6 | Discover real FeesSynced by topic in blocks 26240310–26240349; D seed monitor at **26240310**; rendered expression `newFeesWei > 0`; restore `== 0` and repeat B | 0 / 0 / 0 | **PASS:** **0 → 1 → 0**, 0 ERROR, byte restoration verified. Actual newFeesWei **1106021537900000**, tx `0x0705a6767588d0548b01bed2c4f0049a8209d7cd8cfdcd995f14bec28adcd620`. Synthetic zero-fee event and DAO/non-DAO unstake controls also 1/1/0. |
| V1A.7 | `bash deploy/check-m15-coverage.sh`; independent spec extraction + canonical ABI comparison; literal signature diffs for both DAO monitors; remove QuorumUpdated and rerun coverage | 0; literal diffs 1; removal 1 | **PASS against higher-authority spec/D2, with filename adaptation.** Discord has all **16 named events**, all signatures ABI-exact, plus 9 extras including ModuleUpgraded. Literal plan Telegram filename has all 16 intentionally absent and 8 extras after repair; this is recorded, not silently credited as literal command success. See judgment below. |
| V1A.8 | `docker compose -f docker-compose.yaml config --format json`, also with `MONITOR_DATA_DIR=/srv/qa-monitor-data`; render without/with secondary RPC | all 0 | **PASS:** `/app/data` bind source is worktree `data`, override follows `/srv/qa-monitor-data`, no `/dev/null`. RPC count **1 / 2**. `max_past_blocks=3600`; recovery enabled, cron `0 */5 * * * *`, blocks/run 100, max age 7200, retries 3, delay 1000ms. |
| CP0-QA follow-up | `bash deploy/check-templates.sh`; missing credentials, absent/empty/partial dirs, missing root/options, unreadable file/scan dir and dangling symlink controls | baseline 0; invalid controls 1 (missing option 2); restored 0 | **PASS:** checker correctly reports **32 monitors / 30 triggers / 1 network** and fails the intended invalid inputs. Valid readable symlink passes, dangling symlink fails; details in section 4. |

**Block selection and endpoint accounting.** Blockscout v2 address transactions freshly identified reverted liquidations and retired-key incoming transfers; I chose 26239235 and 25986346, different from both the implementer's and attempt 1's positive blocks. I probed neighboring blocks and selected 26177494/26239693, then fetched blocks, logs and receipts myself. FeesSynced block 26240310 came from a new topic-range query. **Every successful mainnet replay above used `https://eth.drpc.org`**, including outgoing/old-key/Success-label controls. No publicnode empty/history response was credited. Block/log counts for V1A.3/.4/.4f/.5 were respectively **324/1025, 243/503, 243/708, 254/658**; all nonempty. Anvil used that endpoint as its archive source at 26240350, then only local writes at `http://127.0.0.1:28547` (independent suite used port 28549). RPC fault proxy was local port 28548 with read-only drpc backing.

**Judgment on accepted raw-exit deviation.** Independently confirmed `src/main.rs:205–209` awaits validation and returns `Ok(())`; malformed input actually exits 0. Event-0013's substitute is sound **for logged validation failures**: valid config earns the success marker + zero ERROR; dangling references fail the gate even when raw exit stays 0. Rust core is excluded, so credit V1A.1f to this measured gate. This does not waive ABI consistency: F8b is an independent validation omission that the marker cannot detect.

**Judgment on implementer decision (a).** Removing the M15-emitting functions from the Telegram DAO success monitor is acceptable and necessary to satisfy D2; otherwise removing only events leaves the function route paging unconditionally. I measured an OracleReplaced event **Telegram 0 / Discord 1**, and a successful calldata-only `replaceOracle` **0 / 1**, all exit 0, 0 ERROR. `acceptOwnership` remains on Telegram and matched once. The eight remaining legacy event extras are outside the named M15/M10 list; preserving their existing routing is acceptable within this repair scope, without assigning them new severity. Discord retains all named M15 events and ModuleUpgraded. This justifies the V1A.7 target adaptation despite the stale Telegram filename in the plan command. Legacy Discord credential naming remains the explicitly owned F5 decision, not a hidden new pass/fail condition.

The literal Discord signature diff contains only these nine extra `<` entries and **no missing spec signature**: `AdminChanged(address,address)`, `BeaconUpgraded(address)`, `ERC20Rescued(address,address,uint256)`, `Initialized(uint8)`, `ModuleUpgraded(uint8,address)`, `NetworkEarningsWithdrawn(uint256,address)`, `OwnershipTransferStarted(address,address)`, `OwnershipTransferred(address,address)`, `SSVNetworkUpgradeBlock(string,uint256)`. The literal Telegram diff has `>` entries for all 16 named M15 events listed in the spec. Complete diff outputs are in scratch `evidence/m15-{telegram,discord}-diff.log`.

## 4. Falsifiers and repair regression measurements

Commands live in `/tmp/ms-qa-cp1a/attempt2/`; this directory is external scratch, not a new repository deliverable. `static.py`, `gate.py`, `live.py`, `fork.py`, `rpc-fault.py`, `extras.py` collect command logs/JSON; `audit.py` independently returns **1** on measured semantic failures. Successful collectors alone are never treated as assertions. Rendered byte mutations use `finally` restoration; tracked template experiments use a copied `deploy/` tree.

| Falsifier / original reproducer | Command or precise scratch mutation | Red/green evidence and restoration |
|---|---|---|
| Attempt-1 F1 zero-value attacker commitRoot | `fork.py`: each attacker locally funded/impersonated; direct zero-value reverted commitRoot to proxy | New attacker rule **1**, old two M13 rules 0; original miss repaired. 42 target/value/failure cases and 8 reverse wallet cases pass; helper/constructor extension RED F1b. Local code restored exactly. |
| Attempt-1 F2 absent/empty config and missing root | Exact `check-templates.sh --config-dir …/absent`, `…/empty-config`, `--root …/missing-root --config-dir "$PWD/deploy/config"` | **1 / 1 / 1**, explicit S0, no OK; original false greens repaired. Missing option value **2**. Removing all JSON from each of monitors/triggers/networks gives **1** separately. |
| Unreadable files / symlinks / scan errors | `static.py`: chmod 000 one JSON, replace it with dangling symlink, then valid readable symlink; selftest also chmod 000 scan directory | Unreadable/dangling **1**, readable link **0**; scan directory **1** on S3, not swallowed. Permissions/bytes restored. A readable link is acceptable as input; a missing target is not. |
| CP0 credential deletion beside plain scan | Remove token, chat_id, Discord URL one at a time in scratch | V0.6 stays empty on deletions; checker each **1** with S4. Missing networks/conditions each **1**. Restored checker **0**, 32/30/1. |
| Attempt-1 F8 nonexistent event | One field `FeesSynced` signature → `Nonexistent(uint256)` | Checker/render **1/1**, A3 diagnostic; restored green. The original named-event hole is repaired. |
| Other ABI one-field mutations | Function → `commitRoot(bytes32)`; contract_spec → absent ABI filename; UnstakeRequested arg type uint256 → uint64; signature → foreign-contract `Transfer(address,address,uint256)` | Each checker/render **1/1**, explicit A2/A3/A4; unchanged full JSON shape. Indexed-layout permutation **0/0** then event match **1→0→1**, RED F8b. |
| Expression/schema controls | Seed expression → `bogus == 0`; no match_conditions; M11 sender removed | Invalid seed parameter checker/render **1/1**; absent conditions **1**; M11 own-sender match **0→1→0**. Restored. |
| Malformed JSON / `{}` / `null` | `gate.py`: closing brace removed, empty object, JSON null | Gates **1/1/1**, restored **0**. Raw malformed **0**, **2 ERROR**, no success marker: accepted raw-exit deviation proven. |
| Dangling trigger reference past ABI gate | `static.py`: triggers = `["qa_absent"]` | ABI passes, Rust logs missing trigger (3 ERROR), raw exit 0; render **1**. Checker **1** independently. Restored gate **0**. |
| Attempt-1 F3 OracleReplaced Telegram route | Local ABI-correct OracleReplaced event; calldata-only replaceOracle success after code set to STOP; raw DAO replays | Event **0 Telegram / 1 Discord**; function **0 / 1**. acceptOwnership **1 Telegram** proves remaining function path still works. Local code restored. Original repro repaired; decision (a) accepted above. |
| M15 missing coverage | Remove QuorumUpdated only from scratch Discord monitor | Generic checker still **0** (it checks structure/ABI, not complete policy); named `check-m15-coverage.sh` **1**, explicit missing event. Restore **0**. Re-adding M15 event/function to Telegram is rejected by executable selftest. |
| Attempt-1 F4 outgoing retired transfer | D retired rule, mainnet **26169289**, plus each retired address's local incoming/outgoing/zero/self cases | Original outgoing **0**, exit0, 0ERROR; direct **1/0/0/1**. Internal incoming to both retired keys **0 despite balance +1**, RED F1b. |
| Old updater rule sender/function | D block **26167892**, control **26239655**; local old4 versus other sender commitRoot | Live **1/0**, local **1/0**, all 0 ERROR. Reverted old-key attempt still pages; wrong sender stays silent. |
| M8 independent event/user controls | Real FeesSynced expression flip at 26240310; synthetic zero-fee and unstake from DAO/other | Real **0→1→0**; synthetic **1 / 1 / 0**. Correct event user predicate works; indexed-layout control separately RED. |
| Failed-tx inbox success, concurrency, invalid input | Live failure vs success; `extras.py`: 40 concurrent valid script invocations, invalid JSON and incomplete match | Failure +1 / success +0; **40 valid JSON lines**, all selectors correct; invalid/incomplete each exit **5**, no file. Repeated observations append as specified; worker de-dup belongs to CP1B. |
| Empty/null RPC response | `python3 /tmp/ms-qa-cp1a/attempt2/rpc-fault.py` | Normal **1/0ERROR/exit0** → injected [] **0/0ERROR/exit0** → null **0/2ERROR/exit1** → restored **1/0ERROR/exit0**. [] is not credited as clean evidence; real responses verified separately. Proxy shut down in finally. |
| Strict render / second RPC / compose persistence | `gate.py`; `docker compose … config --format json` | Missing strict chat **1**, all safe shape-valid required refs **0**; secondary **1→2** RPC entries; primary/recovery preserved. Data bind override validated, no /dev/null. Scratch only. |
| Executable implementer suites | `MONITOR_BIN=B bash deploy/tests/selftest.sh`; `PORT=28549 bash /tmp/ms-qa-cp1a/attempt2/m13-fork-regression.sh` | **31 passed / 0 failed / 0 skipped**, exit **0**; fork **15 cases / 0 failures**, exit **0**. Larger independent set finds six omissions. Both suites actually run, not inferred from implementer logs. |

**Restoration proof:** initial and post-experiment `git status --porcelain` were empty; `git diff --name-only 9b3e7499be32035c70aabe29adf0aba9021ffb4c` was empty before evidence writes. Every rendered expression/ABI mutation was restored byte-for-byte; every Anvil code override was restored and compared. Scratch copies never became tracked templates. No mutation to `deploy/`, `src/`, `config/`, `config.zip`, compose, `.env*`, or `.gitignore` exists in the QA diff.

## 5. Evidence-plane writes

- This report: `plans/monitoring-service/qa/cp1a-attempt-2.md`.
- Only CP1A's **Status:** line in `plans/monitoring-service/plan.md`: IMPLEMENTED — AWAITING QA → **QA FAILED**, frozen hash and attempt-2 report linked.
- One appended event `event-cp1a-qa-attempt-2` in `plans/monitoring-service/execution-log.md`.
- External scratch: `/tmp/ms-qa-cp1a/attempt2/` (scripts, logs, RPC responses and local fixtures), external Cargo target `/tmp/ms-qa-cp1a/target/`. Ignored QA render/data only under this worktree's `.local-dry/` as the rows require. No mainnet send, notification delivery, install, push or implementation repair.

## 6. Repo proof

Compared to frozen lane head `9b3e7499be32035c70aabe29adf0aba9021ffb4c`, before evidence writes:

```text
$ git status --porcelain
(empty)
$ git diff --name-only 9b3e7499be32035c70aabe29adf0aba9021ffb4c
(empty)
```

Final staged evidence-write proof, measured after updating the three allowed files:

```text
$ git status --porcelain
M  plans/monitoring-service/execution-log.md
M  plans/monitoring-service/plan.md
A  plans/monitoring-service/qa/cp1a-attempt-2.md
$ git diff --name-only 9b3e7499be32035c70aabe29adf0aba9021ffb4c
plans/monitoring-service/execution-log.md
plans/monitoring-service/plan.md
plans/monitoring-service/qa/cp1a-attempt-2.md
$ git diff --cached --check
(empty; exit 0)
```

No implementation file appears in either list. This evidence is committed locally on the QA branch. Coordinator next action: fresh repair/scope decision on F1b/F8b followed by independent QA; do not start the next checkpoint from this verdict.
