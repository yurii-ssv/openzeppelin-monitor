FAIL

Independent QA of CP1A on frozen head `b4b15a4063874ac4bdf3eb751ea8c1a255e76ee0` (implementation `9de75ec`), 2026-10-21. QA branch: `mtabasco/ms-qa-cp1a-2`. Task `task_6c62dfabd78e`, dispatch `ctx_bd98ed807b12`, terminal `term_8a3c150b-6afc-4e46-9c93-f3bf105d963a`; dispatched model/effort: Codex gpt-6.1-sol high. I implemented none of this checkpoint and repaired no implementation.

All ten assigned V rows ran. The unmodified configuration and live positive/control measurements passed, but **V1A.1 fails its required ABI-mismatch falsifier**: both raw validation and the substitute render gate report success for an event absent from the supplied ABI. Independent falsifiers also found missing M13 coverage, missing-input false greens in the new checker, and routing inconsistent with D2; these prevent VERIFIED. The CP0 follow-up covers credential deletions but accepts missing inputs.

## 1. Findings, ranked by severity

### F1 — HIGH: M13 misses zero-value attacker interactions with SSV contracts

Owner: CP1A repair worker and coordinator, who must reconcile the checkpoint's narrower prescribed `value > 0` template with higher-authority spec M13 row 3. The implementer disclosed this gap; disclosure is not an accepted scope change. Both attacker addresses are in the value monitor, but zero-value interaction is excluded. The second M13 monitor admits only the former slot-4 sender.

Minimal measured reproduction: start the external build and a local mainnet fork, impersonate the attacker, fund it locally, send `commitRoot(bytes32,uint64)` with a zero root, block 0, zero value and gas 500000 to the SSV proxy, then replay that local block through both M13 templates. Actual commands/harness:

```sh
export PATH=$HOME/.foundry/bin:$PATH
anvil --fork-url https://eth.drpc.org --fork-block-number 26240350 --port 18545 --silent
python3 /tmp/ms-qa-cp1a/fork-proxy.py
python3 /tmp/ms-qa-cp1a/final-controls.py
```

The local JSON-RPC sequence is `anvil_impersonateAccount([attacker])`, `anvil_setBalance([attacker,"0xde0b6b3a7640000"])`, and `eth_sendTransaction([{from:attacker,to:proxy,data:<cast calldata commitRoot(bytes32,uint64) 0x0000000000000000000000000000000000000000000000000000000000000000 0>,gas:"0x7a120"}])`. Attacker = `0xaeE058cCA5EED5470b4b48c159045b7C25ddb80f`; proxy = `0xDD9BC35aE942eF0cFa76930954a156B3fF30a4E1`. These were LOCAL fork transactions, never mainnet sends.

Expected: a page match for an attacker interaction with an SSV contract, including a reverted attempt. Actual: local tx `0xa3d8b6b1ef71c5ec3450d530c0da1052a22c43e97b1ee34a9783142a91747504`, block **26240364**, receipt status `0x0`; both `ssv_retired_addresses_monitor` and `ssv_retired_updater_attempt_monitor` returned **0 matches**, exit **0**, **0 ERROR** lines. The final replay commands were:

```sh
MAINNET_RPC_URL=http://127.0.0.1:18545 TEST_RPC_URL=http://127.0.0.1:18545 bash /tmp/ms-qa-cp1a/local-dry-run.sh --monitor-path config/monitors/ssv_retired_addresses_monitor.json --network evm_ethereum_mainnet --block 26240364
MAINNET_RPC_URL=http://127.0.0.1:18545 TEST_RPC_URL=http://127.0.0.1:18545 bash /tmp/ms-qa-cp1a/local-dry-run.sh --monitor-path config/monitors/ssv_retired_updater_attempt_monitor.json --network evm_ethereum_mainnet --block 26240364
```

Additional controls: zero-value sends from **each** attacker to the retired liquidator at fork blocks 26240356/26240357 also produced 0/0 matches; changing the first attacker's transfer to **1 wei** at 26240358 produced 1 match. A reverted `commitRoot` from the old slot-4 key at 26240359 produced 1 match in the separate attempt rule. This isolates the missing attacker predicate from a broken replay or function decoder.

### F2 — HIGH: the new checker accepts missing or empty inputs

Owner: CP1A. The CP0 follow-up must not report a missing input as clean.

```sh
bash deploy/check-templates.sh --config-dir /tmp/ms-qa-cp1a/absent
bash deploy/check-templates.sh --config-dir /tmp/ms-qa-cp1a/empty-config
bash deploy/check-templates.sh --root /tmp/ms-qa-cp1a/missing-root --config-dir "$PWD/deploy/config"
```

The first directory did not exist; the second existed and was empty. Expected: nonzero with an explicit missing-input diagnostic. Actual: **all exit 0**, print **`checker: OK`**. The first two even report **35 monitors, 1 triggers, 35 network** because nullglob leaves `ls` without file arguments and it counts the working directory. The third prints `grep: .../missing-root/deploy: No such file or directory`, then `checker: OK (31 monitors, 29 triggers, 1 network)` and exit 0: the secret scan failure is swallowed.

In scratch copies, deleting a monitor's `networks` or `match_conditions`, or replacing its event with `Nonexistent(uint256)`, also leaves this checker green. The isolated ABI-mismatch control also passes the full render gate (F8); monitor-field deletion and ABI membership are not comprehensively checked by this script. Required token/chat/Discord/script/RPC-field deletions and a dangling trigger *are* correctly rejected (section 4).

### F8 — HIGH: ABI-inconsistent event signature passes the required validation gate

Owner: CP1A for additional deploy-side signature checks, with coordinator ownership of the Rust boundary. Rust changes are not required to report or repair a deploy-side validation gap.

```sh
python3 /tmp/ms-qa-cp1a/abi-falsifier.py
```

The script changes **only** `.match_conditions.events[0].signature` in the scratch copy of `ssv_seed_stake_monitor.json`, from `FeesSynced(uint256,uint256)` to `Nonexistent(uint256)`. All original JSON fields, functions, transactions, addresses and supplied ABI remain intact. It runs:

```sh
MONITOR_BIN=/tmp/ms-qa-cp1a/target/release/openzeppelin-monitor MAINNET_RPC_URL=https://eth.drpc.org bash /tmp/ms-qa-cp1a/mirror/deploy/render.sh --out /tmp/ms-qa-cp1a/abi-isolated/config
```

Expected by V1A.1's “must fail when ... ABI mismatch”: nonzero validation gate. Actual: **render exit 0**, `31 active monitor(s)`, `1 network(s)`, `--check passed`; subsequent raw `--check` from that output parent (shape-valid dummy environment refs) also exits **0**, prints `Configuration validation completed successfully`, and has **0 ERROR** lines. The ABI contains no `Nonexistent` event. Restoring the original signature produces exit 0 again. The checker independently also passes this mutation.

An earlier attempted ABI mutation accidentally omitted `functions` and failed on that missing field. I discarded that inference, preserved the full shape, and ran this isolated one-field mutation twice; both render runs passed. Thus the required semantic falsifier is **FAIL**, independent of the accepted raw-exit-0 deviation. The substitute gate detects logged failures but cannot detect validation the Rust checker never performs.

### F3 — MEDIUM: unconditional M10/M15 info events also take the Telegram route

Owner: CP1A/coordinator for routing correctness; CP3 for the explicitly deferred environment-name reconciliation. D2 requires info → Discord. DEF-2 defers enabling routes; it does not establish that unconditional info should be assigned a Telegram route.

```sh
jq '{name,triggers,events:[.match_conditions.events[]|select(.signature|startswith("OracleReplaced"))]}' deploy/config/monitors/ssv_dao_success_monitor.json deploy/config/monitors/ssv_dao_discord_success_monitor.json
jq . deploy/config/triggers/telegram_dao_success.json deploy/config/triggers/discord_dao_success.json
python3 /tmp/ms-qa-cp1a/fork-events.py
```

Expected: unconditional `OracleReplaced`/M15 info goes to `DISCORD_WEBHOOK_INFO`, with any page decision made by the stateful M10 check. Actual templates: `OracleReplaced(uint32,address,address)` has no expression in **both** DAO monitors; one resolves to `telegram_dao_success` (`TELEGRAM_TOKEN`), the other to `discord_dao_success` (`DISCORD_WEBHOOK_DAO_SUCCESS`). A local fork event, emitted from the proxy at block **26240360**, matched **both monitors once**, each exit 0, 0 ERROR. There is no open-round/duplicate-slot predicate in that Telegram path.

The event test replaced proxy bytecode **only on Anvil**, emitted the ABI-correct indexed event, and restored the original bytecode in `finally`; a later `eth_getCode` compared byte-for-byte equal. Notification triggers were removed in the dry configuration, so no Telegram/Discord delivery was attempted.

### F4 — MEDIUM: outgoing retired-key transfers also page

Owner: CP1A/coordinator. Spec M13's retired-key value triggers are incoming transfers.

```sh
MAINNET_RPC_URL=https://eth.drpc.org bash /tmp/ms-qa-cp1a/local-dry-run.sh --monitor-path config/monitors/ssv_retired_addresses_monitor.json --network evm_ethereum_mainnet --block 26169289
```

Expected for an unrelated outgoing retired-key transfer: 0 matches under the specified incoming-transfer rule. Actual: **1 match**, exit 0, 0 ERROR, tx `0xe3dae8eadd8787b34f1991ee6c3e7b00d2849ba5832ef694cae652b4bd1fca8d`; sender is retired slot 4, destination `0x4aff805312982c8da3c5bfa97bd8bf1bd9623bd7`, value **84457721915513543 wei**. The Monitor address filter admits either direction and the expression has no destination restriction. This is a page-noise discrepancy in addition to F1's missed pages.

### F5 — LOW, owned deferral: legacy Discord variable names remain

Owner: **CP3 reconciliation**, per the dispatch addendum; this finding does not fail V1A.1, V1A.7 or V1A.8 by itself.

```sh
jq -r '..|objects|select(.type?=="environment")|.value' deploy/config/triggers/*.json | sort -u
rg -n 'DISCORD_WEBHOOK_' .env.example docker-compose.yaml
```

Expected reconcile names: `MAINNET_RPC_URL`, `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`, `DISCORD_WEBHOOK_INFO`. Actual additional notification names in templates, `.env.example` and compose: `DISCORD_WEBHOOK_{CLUSTER_FAILED,CLUSTER_SUCCESS,DAO_FAILED,DAO_SAFE_FAILED,DAO_SUCCESS,LARGE_ETH_TRANSFER,LIQUIDATION_FAILED,LIQUIDATION_SUCCESS,OPERATOR_FAILED,OPERATOR_SUCCESS,ORACLE_DISSENT,ORACLE_FAILED,ORACLE_SUCCESS,VALIDATOR_FAILED,VALIDATOR_SUCCESS}`. There are **15** legacy Discord names. `MAINNET_RPC_URL_2` is the explicitly permitted optional secondary; `TEST_*` are explicitly permitted QA names. Other compose logging/metrics/path variables are operational settings, not alternative reconcile credential names.

### F6 — LOW, inherited Rust behavior: reverted other-sender updates are labelled Success

Owner: coordinator/CP3 or a separately authorized Rust owner. Rust repair is outside this lane. Spec M11 asks for other-sender info regardless of outcome, so this caveat does not invalidate the successful-sender discrimination row.

```sh
MAINNET_RPC_URL=https://eth.drpc.org bash /tmp/ms-qa-cp1a/local-dry-run.sh --monitor-path config/monitors/ssv_updater_other_sender_monitor.json --network evm_ethereum_mainnet --block 26177500
```

Expected from the template's `status: Success` label: no reverted transaction labelled success. Actual: **1 match**, exit 0, 0 ERROR, tx `0x8563fbf3473c699aaddad6acde375b649e75309ac29474072ec829171c396f99` labelled Success; an independent `eth_getTransactionReceipt` on `https://eth.drpc.org` returns status **`0x0`**. This reproduces the implementer's caveat; no new Rust mutation was made.

### F7 — LOW, inherited hygiene follow-up: production inbox JSONL is not ignored

Owner: coordinator/CP3 (already disclosed by the implementer).

```sh
git check-ignore data/inbox/failed-tx.jsonl
```

Expected for generated runtime inbox data: ignored. Actual: exit **1**, no matching ignore rule. The QA inbox was under the ignored `.local-dry/`, which was independently confirmed ignored; no production data file was created.

## 2. Claims I could NOT confirm

- **Full M13 spec coverage and routing conformance:** not confirmed; F1/F3/F4 provide measured counterexamples. Coverage of the literal positive V rows is confirmed and does not remove those failures.
- **Historical live `config/` duplicate names / its byte identity to the templates:** not inspected. The task forbids touching that checkout's user-owned config; only tracked templates and QA rendering were used.
- **Publicnode's precise historical cutoff and exact error at the implementer's historical run time:** not independently remeasured. All successful archive replays here used `https://eth.drpc.org`; that endpoint served nonempty blocks/logs and the required receipts. No publicnode result is credited.
- **The implementer's `gas_used`/`AND` grammar experiments and their exact 444/678 error counts, original literal-block results not rerun here, and historical intermediate-commit tree states:** not independently reproduced. They are not used to earn a CP1A row. I reproduced the reverted-Success caveat, ABI/function/event matching, current template counts, required-field controls, strict mode, optional RPC, concurrency and dry-run safety directly.
- **Continuous catch-up capacity, actual container startup and production delivery:** not claimed by these config-only rows. V1A.8 measures compose validation and rendered values, not a running deployment; CP3 owns composed operation, and DEF-2 keeps production routes disabled.

No required V row is NOT RUN. The Anvil probes are additional synthetic controls, not assertions that their fabricated events occurred on mainnet.

## 3. Verification matrix

Preflight: HEAD exactly matched the dispatch; initial `git status --porcelain` was empty; disk available **356 GiB**. Cargo/rustc **1.90.0**, Anvil **1.8.5**, Docker Compose **v5.6.0**, jq and Python **3.9.6** were present. No venv needed, as explicitly stated in this dispatch. Build command `CARGO_TARGET_DIR=/tmp/ms-qa-cp1a/target cargo build --release --locked` exited **0** and produced `/tmp/ms-qa-cp1a/target/release/openzeppelin-monitor`; no install was performed.

**Path adaptation used for every live row:** `scripts/local-dry-run.sh` hard-codes the repository `target/release` binary. I made an external copy at `/tmp/ms-qa-cp1a/local-dry-run.sh`, replacing only its `ROOT=$(...)` assignment with this worktree's absolute path and `BIN="$ROOT/target/release/openzeppelin-monitor"` with `/tmp/ms-qa-cp1a/target/release/openzeppelin-monitor`. This keeps the committed script unchanged and renders the authorized `.local-dry/config`. Its other bytes and logic are unchanged. No `TEST_*` notification variables were set during replays; every delivery route was removed and only `local_log` and the inbox script remained.

For compactness below, **D** means the exact command prefix `MAINNET_RPC_URL=https://eth.drpc.org bash /tmp/ms-qa-cp1a/local-dry-run.sh`; **B** means `/tmp/ms-qa-cp1a/target/release/openzeppelin-monitor`. Every one-shot includes `--network evm_ethereum_mainnet --block <number>`. The raw B calls run from `.local-dry` with safe shape-valid credential placeholders when validating the production-shaped config; no notification is sent by `--check`.

| V-ID | Command actually run / adaptation | Exit | Measurement and outcome |
|---|---|---:|---|
| V1A.1 | `MONITOR_BIN=/tmp/ms-qa-cp1a/target/release/openzeppelin-monitor MAINNET_RPC_URL=https://eth.drpc.org deploy/render.sh --out .local-dry/config`; then B `--check` through `/tmp/ms-qa-cp1a/gate.py` | 0 / 0 | **FAIL required falsifier (F8); positive render PASS.** Valid templates: success marker, **0 ERROR**, **31 active monitors / 1 network**. Isolated ABI mismatch: render **0**, raw **0 + success + 0 ERROR**, contrary to the must-fail condition. |
| V1A.1f | `/tmp/ms-qa-cp1a/gate.py`: remove final closing brace of rendered seed monitor; raw B `--check`; independently mutate scratch template and run scratch `render.sh` | raw 0 / gate 1 | **PASS under accepted deviation.** Raw has **2 ERROR**, no success line; gate has 3 ERROR and exits 1. Dangling-trigger test exits 1; restored gate exits 0. The initial ABI test failed earlier on a missing field and is excluded; the isolated ABI test is F8. |
| V1A.2 | `jq -r '..|objects|select(.type? == "plain")|.value' deploy/config/triggers/*.json deploy/config/networks/*.json`; `git grep -nE 'discord\.com/api/webhooks/[0-9]+/|[0-9]{8,10}:AA[A-Za-z0-9_-]{30,}' -- . ':!tests/integration/fixtures'` | 0 / 1 | **PASS:** 0 plain values; grep exit 1 means **0 matching rows**. It does not mean scanner execution failed. |
| V1A.3 | D `--monitor-path config/monitors/ssv_failed_tx_inbox.json --network evm_ethereum_mainnet --block 26239237` | 0 | **PASS:** 1 match, inbox **0 → 1**, 0 ERROR; independent receipt `0x0`. Tx `0xbb449c381d8faa3948126ea185f88e8f397fda33dda74030c419d2df63b93324`. Exactly the 11 §6 keys; numeric block 26239237, schema 1, status failure, selector **`0xbf0f2fb2`** equals transaction input prefix, all transaction/hash/address fields match the RPC transaction. Success control 26239259: 0 matches, inbox unchanged. |
| V1A.4 | D `--monitor-path config/monitors/ssv_updater_other_sender_monitor.json --network evm_ethereum_mainnet --block 26177496` | 0 | **PASS:** 1 match, 0 ERROR; successful tx `0x9f55f19c83ecb72c1fd575a8ecc587b3913472af7a7f243ad35fb0c79ff26f61`, sender `0x28bea5b242362974d5ddb8f17a1e0e525446960b`. |
| V1A.4f | D same monitor, block **26239694**; delete only rendered `transactions[0].expression`, run raw B; restore and rerun raw B | 0 / 0 / 0 | **PASS:** baseline **0**, sender-disabled **1** (control red), restored **0** matches; 0 ERROR throughout. Independent block read confirms its sole update is from `0x16A503C901615E6e21AA78cFcaFf11822fc2b617`. |
| V1A.5 | D `--monitor-path config/monitors/ssv_retired_addresses_monitor.json --network evm_ethereum_mainnet --block 25991146` | 0 | **PASS positive row:** 1 match, 0 ERROR; incoming **4192795588647 wei**, tx `0xe89c82c5a882167cbfb3d772477105229e2a4b74d1c712f8cbaf6b008a2e129c`, to retired slot 4. The broader M13 semantics **FAIL** F1/F4. |
| V1A.6 | D seed monitor block **26240350**; change only rendered FeesSynced expression to `newFeesWei > 0`, run raw B; restore to `== 0`, rerun B | 0 / 0 / 0 | **PASS:** matches **0 → 1 → 0**, 0 ERROR. Real event's `newFeesWei` **22120430759200000**, tx `0xf47d63643f63d7952856bccbc47fbe5c671ebf41a6c2219240cfebe125b5a5b0`. RPC returned 3 proxy logs; mutation was restored byte-for-byte. |
| V1A.7 | `bash deploy/check-m15-coverage.sh`; independent spec-name extraction + canonical ABI-signature comparison against the DAO monitor; QuorumUpdated removal in scratch | 0 / 0 / 1 | **PASS coverage:** **16** required events, all ABI-exact; **25** monitored events, 9 extras, no missing event. Both DAO templates pass helper; removal fails; restoration passes. Routing separately fails F3. |
| V1A.8 | `docker compose -f docker-compose.yaml config --format json`; additionally `MONITOR_DATA_DIR=/srv/qa-monitor-data` equivalent via `/tmp/ms-qa-cp1a/extras.py`; render once without and once with `MAINNET_RPC_URL_2` | all 0 | **PASS:** `/app/data` is bind-mounted from worktree `data`, override follows `/srv/qa-monitor-data`; no `/dev/null`; primary/optional RPC counts **1 / 2**. `max_past_blocks=3600`, recovery enabled, cron `0 */5 * * * *`, max blocks 100, max age 7200, retries 3, delay 1000 ms. |
| CP0-QA follow-up | `bash deploy/check-templates.sh`; credential/RPC/script deletions and dangling-trigger scratch controls; missing/empty-input controls | baseline 0; deletion controls 1; missing/empty inputs 0 | **FAIL:** intended required credential fields are covered, but a missing directory/root becomes clean (F2). |

**Network fault and endpoint accounting.** Before allowing network, an `eth_getLogs` probe for each live row's exact block (V1A.3, .4, .4f, .5, .6) exited **6**, `curl: (6) Could not resolve host: eth.drpc.org`. These sandbox attempts are **NOT RUN due to environment**, never passes. With network allowed, all corresponding full rows above completed on **`https://eth.drpc.org`**, with nonempty block/log responses saved independently. V1A.3 block: 316 tx / 1524 logs; V1A.4: 243 / 669; V1A.4f: 207 / 651; V1A.5: 299 / 764. Synthetic controls use **`http://127.0.0.1:18545`**, an Anvil fork sourced from drpc at 26240350. The RPC-fault proxy at `http://127.0.0.1:18546` forwards only a whitelist of read methods to drpc.

**Independent block selection.** I queried Blockscout v2 proxy transactions, the other sender's history, and retired-key incoming history. Chose **26239237, 26177496, 26239694, 25991146**, respectively, instead of the implementer's literal V1A.3–V1A.5 blocks. Chose **26240350** for FeesSynced instead of 26240359. The previously identified 26240359 was reused only for an additional UnstakeRequested user-expression control. Historical fact discovery was confirmed with RPC blocks/receipts/logs, not an empty API page.

**M15 exact diff.** Comparing the actual signature list to the spec's 16 names resolved through the ABI yields no `>` (missing) lines; the only `<` (extra) lines are:

```text
< AdminChanged(address,address)
< BeaconUpgraded(address)
< ERC20Rescued(address,address,uint256)
< Initialized(uint8)
< ModuleUpgraded(uint8,address)
< NetworkEarningsWithdrawn(uint256,address)
< OwnershipTransferStarted(address,address)
< OwnershipTransferred(address,address)
< SSVNetworkUpgradeBlock(string,uint256)
```

## 4. Falsifiers and restoration

All template mutations below occurred under `/tmp/ms-qa-cp1a/mirror/deploy/config`, or in the permitted ignored `.local-dry/config`. No tracked implementation file was mutated. Scratch-template byte restoration was asserted immediately; rendered monitors were restored from saved bytes or regenerated from committed templates. `git status --porcelain` and `git diff --name-only b4b15a4063874ac4bdf3eb751ea8c1a255e76ee0` were both empty at the checkpoint before evidence writes. The isolated ABI experiment was completed afterward in scratch; final repo proof below checks the same implementation boundary again.

| Falsifier / command harness | Expected red/green and actual result | Restoration proof |
|---|---|---|
| Matrix V1A.1f / `python3 /tmp/ms-qa-cp1a/gate.py` | Malformed JSON: raw exit 0 + 2 ERROR, no success; render gate exit 1. Scratch dangling trigger exits 1. The initial ABI attempt hit a missing-field error and is not ABI evidence. | Saved bytes restored; gate exit 0. |
| Matrix V1A.1 ABI mismatch / `python3 /tmp/ms-qa-cp1a/abi-falsifier.py` | Change only event signature to absent `Nonexistent(uint256)`: expected red, **actual render 0 / raw 0 + success + 0 ERROR**, F8. | Template restored byte-for-byte; restored render 0. |
| Matrix V1A.4f / `python3 /tmp/ms-qa-cp1a/live.py` | Ignore sender on own sender-control block: **0 → 1 → 0** matches. | Rendered bytes equal original. |
| Matrix V1A.6 / same live harness | Real nonzero FeesSynced with predicate flipped: **0 → 1 → 0** matches. | Rendered bytes equal original. |
| CP0 required fields / `python3 /tmp/ms-qa-cp1a/static.py`, `python3 /tmp/ms-qa-cp1a/extras.py` | Delete Telegram token: exit 1, 1 failure; chat_id: 1/1; Discord URL: 1/1; inbox script_path: 1/2; RPC URLs: 1/2. Deleted token leaves V0.6 empty, proving the companion check adds real coverage. | Each scratch file restored; checker exit 0, **31 / 29 / 1**. |
| Plain value / runtime-built secret shape / extras harness | Plain token: checker exit 1, S2+S4 failures. Runtime-built dummy secret shape in scratch deploy file: exit 1, S3 failure; never wrote its literal value into tracked docs. | Scratch dummy removed, token restored; plain scan 0 rows and checker exit 0. |
| Dangling trigger / static harness | Replace M8 trigger with `qa_absent_trigger`: checker exit 1, S6 undefined trigger. | Restored checker exit 0. |
| Missing-input falsifiers / static + extras | Missing/empty config or missing scan root should redden; **actual green**, F2. | No tracked input changed. |
| M15 event removal / static harness | Delete `QuorumUpdated`: helper exit 1, `missing: QuorumUpdated`. | Restored bytes; helper exit 0. |
| **Own M8** / `python3 /tmp/ms-qa-cp1a/more-live.py` | UnstakeRequested at 26240359: normal DAO predicate **0**, change to actual unrelated staker **1**, restore **0**, all exit 0. | Rendered bytes restored. |
| **Own M8 positive branches** / `python3 /tmp/ms-qa-cp1a/fork-events.py` | Local proxy emits FeesSynced zero: **1**; DAO UnstakeRequested: **1**; otherwise-identical unrelated-user event: **0**; all exit 0, 0 ERROR. | Local proxy code restored byte-for-byte; no mainnet change. |
| **Own M11** / more-live harness | Reverted other sender still matches; independently cross-check receipt status → F6 rather than silently assuming success. | No mutation. |
| **Own M13 value monitor** / fork + final-controls harnesses | Both attacker zero-value transfers fail coverage, 1 wei matches; outgoing historical transfer also matches → F1/F4. | Only local fork transactions; Anvil stopped after measurements. |
| **Own M13 attempt monitor** / fork harness + live harness | Old-key `commitRoot` at independently chosen **26165500**: **1**; other-sender roots at **26239655**: **0**; old-key reverted attempt on fork: **1**; attacker proxy call **0**. | No tracked mutation, 0 ERROR. |
| **Own inbox** / more-live harness + concurrent append probe | Successful liquidation control 0; two repeated failed-block replays each append **exactly 1** further line (worker dedup is separately owned); **40** concurrent appends → **40 valid JSON** lines, all exits 0. Invalid JSON and incomplete match each exit **5**, no inbox file. | Outputs confined to `.local-dry` or `/tmp`; no tracked mutation. |
| **Own M10/M15 routing** / fork-events harness | Same unconditional ABI-correct OracleReplaced matches both DAO templates **1 / 1**, exposing F3. | Record mode only; local proxy code restored. |
| **Own RPC falsifier** / `python3 /tmp/ms-qa-cp1a/rpc-fault.py` | With FeesSynced `> 0`, known event-positive block: real logs **1**, substituted `[]` **0**, substituted `null` **0 + 2 ERROR, exit 1**, restore real logs **1**. Normal/empty/restored exits 0. | Dedicated scratch config and server; server shut down in `finally`. A valid-but-false empty response is not called a chain-negative pass; independent log evidence is necessary. |
| Strict rendering / `python3 /tmp/ms-qa-cp1a/strict.py` | Missing chat ID exits 1; all referenced names with dummy values exits **0**; unset only `DISCORD_WEBHOOK_INFO` exits **1**. Exact chat placeholder substitutes while `${transaction.hash}` remains. | Scratch rendering only, no real credentials. |
| Dry-run route safety / `python3 /tmp/ms-qa-cp1a/final-controls.py` | `--prepare` with dummy TEST values exits 0; all **12 Telegram** and **16 Discord** definitions use only `TEST_TELEGRAM_TOKEN` / `TEST_DISCORD_WEBHOOK`, correct TEST chat ID. `--prepare` without TEST values leaves **2 script triggers**, no notification definition. | Final `.local-dry/config` regenerated without TEST notification routes. Prepare never executes notifications. |

**Accepted deviation judgment.** I independently read `src/main.rs:205–209`: the `--check` branch awaits validation, then unconditionally returns `Ok(())`. I measured raw exit 0 with malformed JSON and genuine ERROR lines. The substitute gate is **sound as a replacement for the broken exit code when validation emits an error**, and rejected my brace and undefined-trigger inputs. I credit **V1A.1f** exactly as authorized. It is **insufficient for V1A.1's full required semantics**, because an isolated ABI mismatch emits success and no ERROR (F8); an additional semantic check is needed. I neither reject the authorized exit-code workaround merely for differing from the original command, nor let that exception waive an independent must-fail condition. The separate checker's missing-input F2 is another defect.

**Environment/harness corrections, not code findings.** Early local-fork harness attempts needed to accept Anvil's documented null success for mutation methods and wait for mining; a final ABI-summary lookup used the wrong JSON key. The corrected fork harness completed exit 0 and all cited final controls have explicit results. The fault proxy initially had the wrong scratch network filename, then omitted `net_version` from its read-only whitelist; those preflight failures were cleared before the **1 → 0 → error → 1** measurement. One optional TEST prepare probe overlapped my local renderer; its failed temporary-file rename and the affected fork replay were discarded and rerun sequentially by `final-controls.py` (both replays exit 0; TEST safety passes). No one of these harness/environment failures is counted as a checkpoint regression or as a pass.

## 5. Evidence-plane writes

- `plans/monitoring-service/qa/cp1a-attempt-1.md`: this report.
- CP1A's single `**Status:**` line in `plans/monitoring-service/plan.md`: **IMPLEMENTED — AWAITING QA → QA FAILED**, frozen head and report linked; other checkpoint status lines unchanged.
- One appended event in `plans/monitoring-service/execution-log.md`, anchor `event-cp1a-qa-attempt-1`.

Scratch build, harnesses, raw command logs, RPC replies and machine-readable measurements are under `/tmp/ms-qa-cp1a/`; rendered runtime records are under the ignored `.local-dry/`. These are supplementary local evidence, not additional tracked deliverables. Mainnet access was read-only; all sends/code changes were on the local Anvil fork; no push, installs, real notification secrets, production route enabling, or implementation repair occurred. Anvil and the local proxy were stopped. The coordinator owns repair dispatch and a fresh independent QA attempt; CP1A is not ready for integration as VERIFIED.

## 6. Repo proof

The QA lane base for this proof is the frozen dispatch head `b4b15a4063874ac4bdf3eb751ea8c1a255e76ee0`, the point this QA branch starts from. The implementer's earlier base `4fbb9f1` would include its intentional implementation changes and is not the QA no-touch comparison.

Before evidence writes, these commands both printed **nothing**, exit 0:

```sh
git status --porcelain
git diff --name-only b4b15a4063874ac4bdf3eb751ea8c1a255e76ee0
```

After all experiments and evidence writes, immediately before staging, actual captured output was:

```text
$ git status --porcelain
 M plans/monitoring-service/execution-log.md
 M plans/monitoring-service/plan.md
?? plans/monitoring-service/qa/cp1a-attempt-1.md
$ git diff --name-only b4b15a4063874ac4bdf3eb751ea8c1a255e76ee0
plans/monitoring-service/execution-log.md
plans/monitoring-service/plan.md
```

The report is untracked at this capture, so it appears in porcelain and is not yet included by `git diff`. `git diff --check` exited 0. The plan diff changes exactly the CP1A Status line; the execution-log diff is one appended event. The report-format check confirms line 1 is exactly FAIL and all ten V-IDs have explicit rows.

No implementation file appears in either output; no tracked `deploy/`, `scripts/`, compose, environment example, Rust, test or worker-framework file was modified by QA. User-owned `config/`, `config.zip`, and `.env*` were not written in any checkout. The evidence is committed on this QA branch; final commit identity and clean porcelain are reported to the coordinator after the commit.
