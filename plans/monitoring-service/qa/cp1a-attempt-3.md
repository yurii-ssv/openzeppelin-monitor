FAIL

Independent CP1A QA, attempt 3 after repair 2, on frozen head `2e0447bb72561dcbb28455f5cdb4003ec88c5db6` (repair code `f04c79ec046c96311185199cedf4778957a43d0b`), 2026-10-21. Branch `mtabasco/ms-qa-cp1a-4`; task `task_c7652d6d114a`, dispatch `ctx_5f15524fe594`, terminal `term_8b5cb6ee-38ba-4bb0-8e5f-8d429548c095`; dispatched Codex gpt-6.1-sol high. I implemented none of this checkpoint and repaired no implementation.

All ten assigned V rows and the CP0-QA follow-up were re-measured. **F8b is fixed:** independent source/chain verification supports the reference, and the original indexed-layout mutation now fails the render gate. **F1b remains HIGH and alone prevents VERIFIED:** documentation accurately identifies a real limitation, but no owner-approved scope change or delivered alternative covers the remaining M13 requirements. A new M15 coverage-check falsifier also exposes a missing module-event assertion (F9); the unmodified template itself contains that event.

## 1. Findings, ranked by severity

### F1b — HIGH, still open: CP1A cannot provide all three M13 rows' full guarantees

Owner: coordinator/Marco for the coverage decision and assignment of implementation. The new `deploy/README.md` boundary correctly describes the measured direct cases and the internal CALL/constructor/SELFDESTRUCT blind spots. It is a disclosure, not a spec amendment, and there is no scope change in this dispatch.

I reran attempt 2's 70-case reproduction against this frozen head. Its collection script exits 0 to retain all evidence; **the independent assertion audit exits 1**, with the same six spec mismatches. All six transactions succeeded, recipients gained 1 wei, replay exited 0 with 0 ERROR, and expected/actual page matches were **1/0**:

| Local block | Actual observation missed | Local tx |
|---:|---|---|
| 26240404 | attacker 1 → helper → current oracle wallet | `0xb60e2b2f009fa88ec48fd2d8a2983f58776fc577e46c2a5352dc5855a288bd04` |
| 26240405 | attacker 2 → helper → current oracle wallet | `0x98efa95161193a30ce4f011af6929a0dc55a535ee8cc30cc4e76f21f01d62180` |
| 26240406 | attacker 1 constructor → current liquidator wallet | `0x9660722356f42b1793a8205c984c6fb1ea5283ca4451bd7ce3123c2a1d136869` |
| 26240407 | attacker 2 constructor → current liquidator wallet | `0xdaa7bea6c691dbacdc8adcc5b1daa9204e3fdcfc17e940368fd14162d4b94329` |
| 26240413 | unrelated sender → helper → retired slot 4 | `0xa687fa0cae86285dad80485fbc323580ab6a20b52d84d5d4dfbec1bd4a9732a7` |
| 26240418 | unrelated sender → helper → retired liquidator | `0xd39c14707573f6d9dd142a6a8ca0df719686149401a0500ae278a7fa28d3bbc3` |

Commands actually run, all transactions LOCAL to Anvil:

```sh
export PATH=$HOME/.foundry/bin:$PATH
anvil --fork-url https://eth.drpc.org --fork-block-number 26240350 --port 38547 --silent
# Separate shell, from this QA worktree:
python3 /tmp/ms-qa-cp1a/attempt3/fork.py
python3 /tmp/ms-qa-cp1a/attempt3/boundary.py
python3 /tmp/ms-qa-cp1a/attempt3/audit.py  # exit 1, explicit spec mismatches
```

Minimal reproducible mechanism, independent of those harnesses: impersonate and locally fund attacker `0xaeE058cCA5EED5470b4b48c159045b7C25ddb80f`; save helper `0x00000000000000000000000000000000000dead2`'s code; use `anvil_setCode` to install runtime `0x6000600060006000600173<TARGET_20_ADDRESS_BYTES>5af15000`; send `eth_sendTransaction` with `{from:attacker,to:helper,value:"0x1",gas:"0x7a120"}` to the LOCAL RPC. With TARGET = current wallet `0x16A503C901615E6e21AA78cFcaFf11822fc2b617`, replay the receipt's block through `ssv_attacker_interaction_monitor`; with TARGET = either retired key, replay through `ssv_retired_addresses_monitor`. Poll the receipt, require status `0x1`, and compare `eth_getBalance(TARGET)` before/after (+1). Actual replay gives 0, expected 1. Restore the helper's saved code and compare it exactly. A typical replay is:

```sh
(cd .local-dry && MAINNET_RPC_URL=http://127.0.0.1:38547 \
 /tmp/ms-qa-cp1a/target/release/openzeppelin-monitor \
 --monitor-path config/monitors/ssv_retired_addresses_monitor.json \
 --network evm_ethereum_mainnet --block 26240413)
```

My additional six boundary cases independently confirm the README's previously unmeasured SELFDESTRUCT claim and constructor transfers to both retired keys:

| Local block | Transfer mechanism / target | Balance delta | Expected / actual |
|---:|---|---:|---|
| 26240427 | constructor CALL → retired slot 4 | +1 wei | 1 / 0 |
| 26240428 | SELFDESTRUCT → retired slot 4 | +1 wei | 1 / 0 |
| 26240429 | constructor CALL → retired liquidator | +1 wei | 1 / 0 |
| 26240430 | SELFDESTRUCT → retired liquidator | +1 wei | 1 / 0 |
| 26240431 | attacker constructor CALL → current oracle wallet | +1 wei | 1 / 0 |
| 26240432 | attacker → helper SELFDESTRUCT → current oracle wallet | +1 wei | 1 / 0 |

Constructor init code is the CALL runtime above, sent as contract-creation `data`, without `to`, with value 1. For SELFDESTRUCT, install `0x73<TARGET_20_ADDRESS_BYTES>ff` at the helper, set its LOCAL balance to 1, and call it with outer value 0. Every receipt succeeded; traces and balances confirm the effect; every replay exited 0 with 0 ERROR. Helper code and balance were restored in `finally` and compared exactly.

**Exactly what CP1A alone cannot meet:** M13 row 1's **any incoming value** to the retired slot-4 key; row 2's **any incoming value** to the retired liquidator; row 3's **any interaction** between either attacker and SSV contracts/current operational wallets when the interaction is internal or in a constructor. Direct old-key `commitRoot`/`updateClusterBalance` attempts are covered; that does not close row 1's value requirement. The other 64 of the original 70 cases pass, including all 42 attacker × seven target × {zero value, positive value, forced revert} direct cases and eight wallet-to-attacker direct cases.

**Verdict with the owner decision pending:** F1b alone prevents VERIFIED. The authoritative spec remains unchanged and disclosure does not satisfy a required behavior. A worker balance-delta watch of the retired keys can address part of rows 1–2, but cannot by itself detect zero-value/reverted attacker interactions in row 3 or every within-block transfer that nets to zero. The owner decision needs to address that distinction. This QA makes no scope change and does not prescribe a Rust repair.

### F9 — MEDIUM: the M15 utility accepts a missing module-update event

Owner: CP1A/coordinator. Spec M15 explicitly includes module updates; the utility's header says it includes `ModuleUpgraded`. Its extraction/check loop requires only the 16 explicitly backticked spec event names, while `ModuleUpgraded` is added only to the Telegram routing check. The current Discord monitor includes it, so this is a **coverage-gate defect**, not a missing event in today's template.

Minimal command, measured in the external mirror:

```sh
# mirror/deploy and mirror/docs are pristine copies of this frozen tree's deploy/ and docs/.
python3 - <<'PY'
import json,pathlib
p=pathlib.Path('/tmp/ms-qa-cp1a/attempt3/mirror/deploy/config/monitors/ssv_dao_discord_success_monitor.json')
j=json.loads(p.read_text())
j['match_conditions']['events']=[e for e in j['match_conditions']['events']
                               if e['signature']!='ModuleUpgraded(uint8,address)']
p.write_text(json.dumps(j))
PY
bash /tmp/ms-qa-cp1a/attempt3/mirror/deploy/check-m15-coverage.sh
```

Expected nonzero for a missing required module-update event. Actual **exit 0**, `all M15 events present`. Removing `QuorumUpdated(uint16)` instead exits **1**. The independent source-derived comparison includes all **17** events and detects either deletion; the frozen monitor has all 17. `final-checks.py` restored bytes in `finally`; all **79 deploy files** in the mirror were then compared byte-for-byte equal to the worktree. No implementation repair was made.

### Owned non-gating deferrals F5/F6/F7 — unchanged, independently re-confirmed

Per the dispatch these do not cause this verdict:

- **F5, legacy Discord variables, coordinator/CP3:** `python3 /tmp/ms-qa-cp1a/attempt3/extras.py` enumerates **15 legacy names** alongside `DISCORD_WEBHOOK_INFO`; DAO info still uses `DISCORD_WEBHOOK_DAO_SUCCESS`. No values were disclosed.
- **F6, inherited Success label, Rust owner/CP3:** replay updater monitor at **26177500** gives **1 match / exit 0 / 0 ERROR**, labelled `Success`; independent drpc receipt for `0x8563fbf3473c699aaddad6acde375b649e75309ac29474072ec829171c396f99` is **0x0**. Spec M11 other-sender info does not exclude failures.
- **F7, inbox ignore, coordinator/CP3:** `git check-ignore data/inbox/failed-tx.jsonl` exits **1**, no rule. No production inbox was created.

`git diff 9b3e7499be32035c70aabe29adf0aba9021ffb4c HEAD -- src .gitignore .env.example docker-compose.yaml` exits 0 with empty output, confirming the deferred areas stayed unchanged since repair 1.

## 2. Claims I could NOT confirm

- **Full M13 spec coverage** is disproved by F1b. The narrower README boundary is supported, now including actual SELFDESTRUCT and constructor-to-retired-key measurements. Documentation does not establish an approved scope exception.
- **The utility's complete M15 event-presence guarantee** is disproved by F9. Today's actual template coverage was confirmed independently, including module updates.
- **Repair 1's claimed 359 accepted mutation cells / historical repair-2 comparison** was not rerun against an old gate. It is not evidence for the current verdict. The current full mutation sweep ran once inside the independently executed selftest; I avoided a duplicate standalone sweep and the old-gate sweep. Selftest's wrapper suppresses the sweep's numeric summary, so I reproduce its passing assertion and 51/0/0 total, rather than independently claiming to have observed the standalone 510-run summary.
- **Real alert delivery, live user-owned config identity, container startup, production persistence, catch-up across an outage and worker/trace coverage** were not exercised. Compose syntax and direct one-shots do not establish those claims; later checkpoints/owner decisions retain them.
- **A successful empty RPC response is not proof of no events.** Against a known positive real FeesSynced block, a local read-only RPC proxy replacing `eth_getLogs` with `[]` changed **1 → 0 matches**, exit 0 / 0 ERROR. Returning `null` gave **exit 1 / 2 ERROR**; restoring truthful logs returned **1 / exit 0 / 0 ERROR**. No positive row is credited from a fake empty result.
- No required V row remains NOT RUN. Initial sandbox DNS failure was an environment failure, then rerun with network allowed. No venv was needed, as the dispatch explicitly specified. No production/test notification delivery was attempted.

**Independent reference verification, not a comparison with the monitor's own ABI:**

1. `git -C /Users/marco/ssv/repos/ssv-network archive f7ac4870074b9408855a7912f9c6e1f959996be8` was extracted into `/tmp/ms-qa-cp1a/attempt3/source`; its existing dependency directory was linked read-only in practice, with no install. `forge build --root /tmp/ms-qa-cp1a/attempt3/source --skip test --skip script` exited **0**. No implementer scratch ABI was used.
2. An independent Python normalizer in `reference.py` (not `deploy/abi/normalize.jq`) compared every event/function, names/types/order, recursive tuple components, indexed/anonymous flags and outputs from the newly built `SSVNetworkSSVStakingUpgrade` artifact: **122/122 equal** to the reference. M8 source declares `user` indexed for UnstakeRequested and neither FeesSynced argument indexed; M10/M15 OracleReplaced has all three arguments indexed. M11/M13 function signatures and tuple field order come from this source/artifact.
3. Safe ABI and source were fetched separately from [Safe deployments 1.37.0, v1.3.0 artifact](https://unpkg.com/@safe-global/safe-deployments@1.37.0/dist/assets/v1.3.0/gnosis_safe.json) and [Safe v1.3.0 GnosisSafe source](https://raw.githubusercontent.com/safe-global/safe-smart-account/v1.3.0/contracts/GnosisSafe.sol). Independent normalization gives **46/46 equal**. Source confirms ExecutionSuccess/ExecutionFailure data fields are non-indexed; singleton slot 0 and `VERSION()` independently return `0xd9db270c1b5e3bd161e8c8503c55ceabee709552` and **1.3.0**.
4. `python3 /tmp/ms-qa-cp1a/attempt3/reference.py` exited **0**, reading drpc at **26241249**. The proxy implementation pointer and **all seven module pointers/code hashes** match `contracts.lock.json`; implementation hash is `0xf518bf4bb8fa630874f0c6cc737b963750d3ce081575b508da337f2d740f076b`. Module mapping slots were read as `keccak256(abi.encode(moduleId, keccak256("ssv.network.storage.main") - 1 + 3))`.
5. Independent real logs: UnstakeRequested **24920762: 2 topics / 64 data bytes**; FeesSynced **26240308: 1 / 64**; OracleReplaced **26169066: 4 / 0**; Safe ExecutionSuccess **24060370: 1 / 64**. These observations agree with the source/reference indexing. No historical Safe ExecutionFailure occurrence is claimed.
6. Twenty source-derived selectors were recalculated with `cast sig`, including `commitRoot(bytes32,uint64)` **0x00ebb5f0**, `updateClusterBalance(uint64,address,uint64[],(uint32,uint64,uint64,bool,uint256),uint32,bytes32[])` **0x4f4d1209**, `replaceOracle(uint32,address)` **0x5d756b1d**, and the M15 update/upgrade functions. Mainnet blocks **26177498** and **26239655** independently contain the first two relevant call types. `deploy/abi/derive.sh --ssv-out /tmp/ms-qa-cp1a/attempt3/source/out --safe-abi /tmp/ms-qa-cp1a/attempt3/gnosis_safe.json --check` also exits **0** against these independently obtained inputs.

This verifies the reference's decoding contract at this pin/read block. It does not promise automatic detection of a later on-chain upgrade; the documented re-verification obligation remains necessary.

## 3. Verification matrix

Preflight: worktree HEAD exactly equals the dispatched frozen hash; initial `git status --porcelain` empty; **353 GiB** available; cargo/cast/anvil/jq/Python/Docker Compose available. `CARGO_TARGET_DIR=/tmp/ms-qa-cp1a/target cargo build --release --locked` exited **0**. No installation. Initial sandbox archive-RPC probe exited **6**, `curl: (6) Could not resolve host: eth.drpc.org`; the same public endpoint worked with network allowed. One Orca follow-up check returned `runtime_access_denied`; the prescribed escalated retry worked. Neither failure was interpreted as a code failure or pass.

**Path adaptations:** `B` = `/tmp/ms-qa-cp1a/target/release/openzeppelin-monitor`. `D` = `MAINNET_RPC_URL=https://eth.drpc.org bash /tmp/ms-qa-cp1a/attempt3/local-dry-run.sh`. The latter is a byte-equivalent external copy of the tracked dry-run script except ROOT points to this worktree and BIN to B. All replays include `--network evm_ethereum_mainnet --block N`. Notifications are removed, verified as script triggers only; match records and inbox lines are local. `MONITOR_BIN=B` selects the external build for render/selftest. Rendered mutations run B directly from `.local-dry` so preparation cannot overwrite them.

| V-ID | Exact command / measurement | Exit | Outcome |
|---|---|---:|---|
| V1A.1 | `MONITOR_BIN=B MAINNET_RPC_URL=https://eth.drpc.org deploy/render.sh --out .local-dry/config`; `(cd .local-dry && B --check)` with child-only safe shape placeholders | 0 / 0 | **PASS**, accepted log-gate deviation. 32 monitors, 1 network, raw success marker and 0 ERROR. Original nonexistent-event and indexed-layout ABI mutations, plus new decoding-field mutations, reject at render with exit 1; restored 0. |
| V1A.1f | `python3 /tmp/ms-qa-cp1a/attempt3/static.py`: remove final rendered closing brace; raw B `--check`; scratch-template equivalents `{}`, `null`, missing brace; dangling trigger past ABI layer | raw 0; gates 1; restored 0 | **PASS under accepted deviation.** Raw malformed config: 2 ERROR / no success marker; dangling trigger: ABI clean, raw exit 0 / 3 ERROR / no success, render exit 1. |
| V1A.2 | exact V0.6 jq plain scan on all trigger/network templates; exact V0.3 `git grep -nE` from plan | 0 / 1 | **PASS**, 0 plain values / 0 secret-shape rows. Runtime-built dummy plain token in scratch is found by both structural and secret-shape scans; restore checker 0. |
| V1A.3 | D `--monitor-path config/monitors/ssv_failed_tx_inbox.json --network evm_ethereum_mainnet --block 26239209` | 0 | **PASS**, 1 match, inbox +1, 0 ERROR. Tx `0x43669861363f8b69dca5d09d745b29053c8844c3667115e2408899ebddd6d0f9`; independent receipt 0x0; all 11 exact §6 keys and values verified, selector **0xbf0f2fb2**, correct function/block/hash/from/to/schema/status. Success control 26239259: 0 matches / +0 lines. |
| V1A.4 | D `--monitor-path config/monitors/ssv_updater_other_sender_monitor.json --network evm_ethereum_mainnet --block 26177498` | 0 | **PASS**, 1 match, 0 ERROR; tx `0x0bdd3bf694eeb8bf58f25d21264cad9e4f9809764ed09c5e661fe92991b9fc96`, from `0x28bE…`, receipt 0x1. This block also has an own-sender call; only the other sender matches. |
| V1A.4f | D updater monitor at **26239692**; rendered transaction sender expression set to null; restore bytes, repeat B | 0 / 0 / 0 | **PASS**, **0 → 1 → 0**, 0 ERROR. Independent block has exactly one proxy update, from `0x16A5…`. |
| V1A.5 | D `--monitor-path config/monitors/ssv_retired_addresses_monitor.json --network evm_ethereum_mainnet --block 26022191` | 0 | **PASS direct-positive row**, 1 match / 0 ERROR. Incoming **1933324399558 wei** to retired slot 4, tx `0x1383aab8d511acb90f2e356df8dd67063153d187526363620f58fae305ad9f51`, receipt 0x1. Full M13 guarantee fails F1b. |
| V1A.6 | Fresh FeesSynced topic query 26240280–26240309 selects **26240308**; D seed monitor; mutate rendered expression `newFeesWei > 0`; restore `== 0`; repeat B | 0 / 0 / 0 | **PASS**, **0 → 1 → 0**, 0 ERROR; real tx `0x7f5a91847b08f018dc582b11e9827af117c7adbfd521dc76857487dc3ee30018`. Synthetic zero-fee and DAO/non-DAO seed controls also 1 / 1 / 0. |
| V1A.7 | `bash deploy/check-m15-coverage.sh`; independent source-derived 17-signature spec comparison; literal diff for both DAO files; delete QuorumUpdated and ModuleUpgraded separately | baseline 0; literal diffs 1; deletions 1 / **0** | **Current coverage PASS with filename adaptation; utility falsifier FAIL (F9).** Discord has all 16 explicitly named events plus ModuleUpgraded; 8 extras. Independent missing set empty and all 17 individual deletion controls red. The utility misses module-event deletion. Telegram intentionally excludes all 17; literal plan filename is not silently credited. |
| V1A.8 | `docker compose -f docker-compose.yaml config --format json`; repeat with `MONITOR_DATA_DIR=/srv/qa-monitor-data`; render without/with secondary RPC | all 0 | **PASS**, `/app/data` binds worktree `data`, override `/srv/qa-monitor-data`, never `/dev/null`; RPC counts 1 / 2; max_past_blocks 3600; recovery enabled, cron `0 */5 * * * *`, max_blocks_per_run 100, max_block_age 7200, retries 3, delay 1000 ms. |
| CP0-QA follow-up | `bash deploy/check-templates.sh`; independent absent/empty config, absent root/option, missing networks/conditions and credential deletion mutations; full selftest | baseline 0; invalids 1, option 2; restored 0 | **PASS**, 32 monitors / 30 triggers / 1 network. Required token/chat_id/Discord URL deletions reject even though the plain-value scan alone stays empty. |

**Endpoint/block accounting:** every mainnet replay/log/receipt above and reference verification used **https://eth.drpc.org**. Fresh Blockscout v2 address-transaction GETs discovered candidate failed liquidations and retired-key incoming transfers; chosen positives **26239209**, **26177498**, **26022191**, and own-sender control **26239692** differ from the implementer's and prior QA's choices. V1A.3/.4/.4f inputs had **114/646**, **517/1378**, **295/1123** transactions/logs respectively (raw saved block/log files are authoritative for these counts); V1A.5 had **382/1186**; every credited input had an independently fetched nonempty block/log set. No publicnode historical empty response was credited. Anvil's archive source was drpc @26240350; writes were only to local ports **38547** and **38549**. RPC fault injection used local **38548**, with read-only drpc backing.

**Accepted raw-exit deviation judgment:** independently confirmed `src/main.rs:205–209` awaits validation then returns `Ok(())`; malformed input actually exits 0. The event-0013 replacement is sound for errors the binary logs: require its success marker, zero ERROR lines, and no nonzero process exit. Dangling-reference testing exercised that gate after a green ABI layer. It does not establish semantic coverage or RPC completeness. Repair 2 now supplies the previously missing decoding-layout check, and my new falsifiers support that portion of V1A.1.

**M10/M15 routing judgment:** removing unconditional info events and their emitting functions from Telegram remains correct under higher-authority spec/D2. Local OracleReplaced event: Telegram **0**, Discord **1**; successful calldata-only replaceOracle: **0/1**; retained non-M15 acceptOwnership function: Telegram **1**. All exits 0 / 0 ERROR. Literal V1A.7 Telegram-filename diffs are intentionally nonempty; coverage is assessed on the Discord twin. The complete signature diff is saved under `/tmp/ms-qa-cp1a/attempt3/evidence/*.diff`; Discord extras are AdminChanged, BeaconUpgraded, ERC20Rescued, Initialized, NetworkEarningsWithdrawn, OwnershipTransferStarted, OwnershipTransferred, SSVNetworkUpgradeBlock.

Executable suites: `MONITOR_BIN=B bash deploy/tests/selftest.sh` **exit 0, 51 passed / 0 failed / 0 skipped**, including the full current ABI mutation sweep once. `PORT=38549 bash /tmp/ms-qa-cp1a/attempt3/m13-fork-regression.sh` **exit 0, 15 cases / 0 failures**; adapter changes only ROOT/build/dry-run paths. The 70-case broader audit is **known red**, exit 1, six mismatches; six added boundary cases also contradict the spec. No suite green overrides those falsifiers.

## 4. Falsifiers and restoration

All template/gate mutations were in `/tmp/ms-qa-cp1a/attempt3/mirror`, isolated RPC configs, or allowed `.local-dry/config`; no tracked implementation mutation occurred. Scratch deploy restoration was checked across **79/79 byte-identical files**; worktree porcelain was empty before QA evidence writes. On-chain code changes were local Anvil only; original code was restored and compared exactly.

| Falsifier | Actual result / restoration |
|---|---|
| Matrix malformed JSON, `{}`, `null` | raw brace exit 0 with 2 ERROR; render cases 1; restored render 0. |
| Dangling trigger | ABI check 0; render 1 / raw exit 0 / 3 ERROR; restored green. This isolates the log gate. |
| Original F8 nonexistent signature | ABI/render 1; restores 0. |
| Original F8b user/amount indexed swap | ABI/render **1**; synthetic genuine DAO UnstakeRequested still demonstrates raw decoder **1 → 0 → 1**. Gate now rejects that mutation before deployment. |
| Other decoding ABI fields | input type/order, extra/missing input, event name case, anonymous flag, recursive tuple component order, function outputs, unknown ABI entry, Safe ABI at proxy address, proxy address typo all ABI/render **1**; bytes restored. |
| Function stateMutability `nonpayable → view` | ABI/render **0**, as explicitly documented. Accepted exclusion: mutability does not alter the selector or input/log decoding fields used here. This is not evidence that those metadata fields are compared. |
| Reference blank/empty/stale | render **1** for blank or empty reference and for reference missing UnstakeRequested (26 mismatch lines). Direct `--reference` pointing at an altered layout: **1** against intact templates. Render `--reference` option: **2**, unknown argument; REF/ABI_REFERENCE environment overrides do not replace its fixed reference. Both reference and templates restored. The standalone utility deliberately trusts a caller-specified reference; its provenance must be verified separately. |
| Missing input vs clean scan | absent/empty config and missing root **1**, missing option **2**; missing networks/empty conditions reject. Selftest also checks unreadable inputs and scan errors. No missing input was counted clean. |
| Required credentials / plain secret | deleted token, chat_id, Discord destination each **1**; scratch plain dummy token yields checker **1**, S2/S3/S4 failures; secret-shape grep **0 = found**; restored checker **0**. Dummy value built at runtime, not stored in this report. |
| M11 own sender | zero baseline; predicate removed 1; restored 0; 0 ERROR throughout. |
| M13 direct direction/value/sender | outgoing retired transfer mainnet26169289 **0**; incoming each key **1**, zero **0**, outgoing **0**; old-key attempt **1**, other sender **0**. Attacker zero-value/reverted direct interactions now pass (attempt-1 reproducer fixed). |
| M13 internal/constructor/SELFDESTRUCT | **Known red F1b:** 12 positive spec counterexamples in the combined 70-case run plus six-case extension, each actual 0. Successful receipts, traces and +1 balances are independent of monitor output. |
| M8 fee and seed-holder expressions | real nonzero fee 0/1/0 after fee-predicate mutation; zero fee 1; correct DAO seed 1, non-DAO 0; indexed mutation suppresses raw page and new gate rejects. |
| M10/M15 routing and completeness | event and calldata-only routing controls 0/1; retained non-M15 function 1. Quorum deletion utility 1; module-event deletion utility **0 (F9)**; independent complete 17-event presence comparison detects all deletions. Restored utilities 0. |
| Inbox positive/success/missing payload/repetition | reverted liquidation +1 exact schema; successful liquidation +0. Forty concurrent identical appends: all exit 0, exactly 40 independently parseable complete lines; malformed/incomplete payloads exit **5/5**, no output file. Bridge append-only repetition is expected; worker deduplication is outside CP1A. |
| RPC empty/null vs true events | known-positive FeesSynced **1 → empty 0 → null failure → restored 1**; exits **0/0/1/0**, ERROR **0/0/2/0**. Successful empty result cannot earn a true-negative claim. |

Budget/skip accounting: no mandatory V row skipped. I did not repeat the old repair-1 sweep, the implementer's exact chosen positive blocks, an additional standalone copy of the full sweep already executed inside selftest, production deliveries or later-checkpoint integration/soak work. I reran both prior attempts' concrete reproducers against this head, rather than rebuilding old lane heads. An initial scratch static-run formatting assertion failed after successful gate measurements because a restored JSON file had equivalent minified bytes; I corrected byte restoration, independently compared all 79 files, and ran the remaining compose checks separately. This harness issue did not change tracked files or convert a red product result to green.

Raw scripts, JSON inputs, traces, receipts, logs and assertion audit are under `/tmp/ms-qa-cp1a/attempt3/`; the durable findings, exact commands and sufficient minimal reproduction mechanisms are in this report.

## 5. Evidence-plane writes

CP1A transitions from IMPLEMENTED — AWAITING QA to **QA FAILED** on the frozen head, linking this report. F8b is independently cleared; F1b remains open pending an owner coverage/scope decision; F9 records the module-event coverage utility gap. F5/F6/F7 retain their non-gating ownership. Only these tracked paths are written and committed on the QA branch:

- `plans/monitoring-service/qa/cp1a-attempt-3.md`
- CP1A's single `**Status:**` line in `plans/monitoring-service/plan.md`
- One appended event in `plans/monitoring-service/execution-log.md`

No implementation repair, installation, mainnet write, push or notification delivery. No `config/`, `config.zip` or `.env*` edit in any checkout. Allowed dry rendering and record files remain gitignored in this QA worktree.

## 6. Repo proof

For this QA child's evidence diff, lane base means the dispatched frozen lane HEAD `2e0447bb72561dcbb28455f5cdb4003ec88c5db6`, not the implementation lane's earlier development base.

Before evidence writes, both commands produced empty output:

```text
git status --porcelain
<empty>
git diff --name-only 2e0447bb72561dcbb28455f5cdb4003ec88c5db6
<empty>
```

After evidence writes, measured immediately before commit:

```text
git status --porcelain
 M plans/monitoring-service/execution-log.md
 M plans/monitoring-service/plan.md
?? plans/monitoring-service/qa/cp1a-attempt-3.md

git diff --name-only 2e0447bb72561dcbb28455f5cdb4003ec88c5db6
plans/monitoring-service/execution-log.md
plans/monitoring-service/plan.md
```

The untracked report is listed by porcelain; after staging/commit it also appears in the base-to-HEAD diff. `git diff --exit-code 2e0447bb72561dcbb28455f5cdb4003ec88c5db6 -- deploy scripts docker-compose.yaml .env.example src tests` is empty / exit 0. **No implementation file appears in either QA porcelain or the QA base diff.** Final commit/status proof is reported to the coordinator; no implementation file is included in the evidence commit.
