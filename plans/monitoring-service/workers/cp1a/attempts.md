# Attempts — CP1A

Same entry shape as execution-log.md events, worker-side. Append only. Measured UTC (`date -u +%Y-%m-%dT%H:%MZ`).

<a id="attempt-1"></a>
## Attempt 1 — Semantics discovery, templates, render.sh, checker, V1A.1/V1A.1f

- Observed UTC: 2026-10-21T10:53Z–11:06Z (approx.)
- Task / Dispatch: `task_cadc69413a4e` / `ctx_42eae2d9dfb0`
- Frozen base: `4fbb9f161005dd7634a7eaabee2b022d2cc96c2e` (confirmed equal to `git rev-parse HEAD` before any write; branch `mtabasco/ms-cp1a`; `git check-ignore -v config.zip .local-dry/` prints both rules)
- Action and concrete outcome: built `target/release/openzeppelin-monitor` (`cargo build --release --locked`, exit 0). Measured the matching semantics with scratch one-shots at block 26,239,655 (contains four `commitRoot` txs, one reverted: `0xfa9ac44e2e…` from `0xC61f…`): function-only matches all 4; `transactions[].expression` (`from == X`, `from != X`) ANDs with `functions` (a function condition and a transaction condition must both match; events and functions are OR'd with each other, then ANDed with transactions when any transaction condition exists); address literals compare case-insensitively; **`status: Success` with no expression labels the reverted tx as Success** when the block has logs (the receipt is not fetched: `needs_receipt` only fetches for Failure or when the block has no logs or when the expression names `gas_used`); adding `gas_used > 0` forces the receipt and excludes the reverted tx (3 matches instead of 4); `Failure` always fetches receipts (1 match, the reverted one). **`openzeppelin-monitor --check` exits 0 on every failure** (`src/main.rs:205-209`), see ask 2. Public RPC depth limit measured (ask 1). Coordinator replies: ask 1 → archive endpoints (`eth.drpc.org`, `eth-pokt.nodies.app`, `eth-mainnet.public.blastapi.io`, `rpc.mevblocker.io`) for rows needing old blocks, record the endpoint per row; ask 2 → **A**: accepted recorded deviation, V1A.1/V1A.1f credited against the render.sh gate (success line + zero ERROR lines), V1A.1f must show both the raw binary exit 0 with the ERROR line and render.sh non-zero.
- Committed `58240a5`: `deploy/render.sh`, `deploy/check-templates.sh`, `deploy/check-m15-coverage.sh`; templates `ssv_retired_addresses_monitor` (M13a: the four addresses, `value > 0`), `ssv_retired_updater_attempt_monitor` (M13b: proxy, `commitRoot`/`updateClusterBalance` AND `from == 0x3A98…`; a separate monitor because functions AND transactions), `ssv_seed_stake_monitor` (M8), `ssv_updater_other_sender_monitor` (M11 info, Success + `gas_used > 0`), `ssv_failed_tx_inbox` + `script_inbox_append` + `inbox_append.sh`; 4 triggers; network template with `max_past_blocks` 3600 (12 h), `recovery_config` (5 min), optional `MAINNET_RPC_URL_2` (weight 50); `docker-compose.yaml`, `scripts/local-dry-run.sh`, `.env.example`. Two pre-existing duplicate trigger display names (`DAO Safe Failed Alert`, `Oracle Dissent Alert`; the live `config/` has the same duplicates) made `--check` log `Duplicate trigger name found` (a real Monitor load failure); the two discord triggers were renamed `Discord …` in the templates.

| Verification ID | Exact command | Exit / result / counts / skips | Environment | Evidence path/hash |
|---|---|---|---|---|
| V1A.1a | `deploy/render.sh --out .local-dry/config` | exit 0; gate printed `✓ Found 31 active monitor(s)`, `✓ Found 1 network(s) with active monitors` | `MAINNET_RPC_URL` public RPC; other referenced env names placeholdered by render.sh | 58240a5 |
| V1A.1b | `(cd .local-dry && ../target/release/openzeppelin-monitor --check)` (dummy env for every referenced name) | exit 0; `Configuration validation completed successfully!`; 0 ERROR lines; 31 monitors, 1 network | dummy env | 58240a5 |
| V1A.1f (raw) | remove the closing `}` of the rendered `monitors/ssv_seed_stake_monitor.json`, rerun the raw `--check` | **raw binary exit 0 with `ERROR … Failed to load monitors`** (exit code is not a gate); restored (`cmp` identical) | dummy env | — |
| V1A.1f (gate) | same mutation in the template `deploy/config/monitors/ssv_seed_stake_monitor.json`, `deploy/render.sh --out .local-dry/config`; restore with `git checkout --` | render.sh exit **1** (`render: --check failed`); after restore `git status --porcelain` shows only the untracked worker record; re-render exit 0 | public RPC | — |
| extra falsifier | template `triggers` set to `["no_such_trigger"]`, render.sh and `deploy/check-templates.sh` | render.sh exit 1 (`references non-existent trigger`); checker exit 1 (`S6 … undefined trigger`); restored, re-render exit 0, tree clean | — | — |

- Falsifiers: see rows; every mutation restored and tree shown clean.
- Deviations and findings: (1) `--check` exit code vacuous (accepted deviation, ask 2). (2) Success-status mislabel (finding for CP3/Rust owner; the new M11 rule works around it with `gas_used`). (3) duplicate trigger names in live `config/` (finding for Marco, live `config/` untouched). (4) The `.gitignore` does not ignore `data/inbox/*.jsonl` (`data/**/*.json` does not match `.jsonl`); `.gitignore` is outside this lane's writes. Owner coordinator/CP3.
- Resulting head: `58240a5`
- Next action: checker row (CP0 follow-up), V1A.2, V1A.3–V1A.6, V1A.7, V1A.8.

<a id="attempt-2"></a>
## Attempt 2 — Grammar finding, V1A.2–V1A.8, falsifiers, final pass at `9de75ec`

- Observed UTC: 2026-10-21T11:00Z–11:13Z (approx.)
- Task / Dispatch: `task_cadc69413a4e` / `ctx_42eae2d9dfb0`
- Frozen base: `58240a5` → fixes `ad7ce32`, `4eee016`, `9de75ec`
- Action and concrete outcome: (a) First M11 expression (`from != 0x16A5… && gas_used > 0`) produced no match and an ERROR per transaction: **the Monitor expression grammar has no `&&`/`||`; the keywords are `AND`/`OR`** (`src/services/filter/expression/parsing.rs:335,365`; measured: 678 `Failed to parse expression` ERROR lines for one block with every `&&` form, `pe` with `and` parses). Further, `gas_used` is an empty string for any transaction whose receipt was not fetched (those not touching a monitored address), so any expression that names `gas_used` logs a `Failed to parse LHS value ''` ERROR for every other transaction in the block (444 in block 26,239,655). The M11 rule therefore uses the single comparison `from != 0x16A5…` with `status: Success`; combined with the Success mislabel (attempt 1) a reverted `updateClusterBalance` from another sender also matches (measured: block 26,177,500, reverted tx `0x8563fbf347…` from `0x28bE…` is labelled Success) which is acceptable for an `info` rule and is recorded as a caveat. (b) Rows below were each run on their own, no `&&` chains credited.
- RPC endpoints (coordinator reply to ask 1): public node `https://ethereum-rpc.publicnode.com` serves `eth_getLogs` only for the last ~10,000–20,000 blocks (exact error for depth ≥ 20,000: code -32602 "Archive requests require a personal token. Get one at: https://www.allnodes.com/publicnode"). Rows that need old blocks used `https://eth.drpc.org` (served `eth_getLogs`/blocks/receipts for blocks 17,661,857, 26,022,191, 26,167,892, 26,177,498/500 without error). Per row below.

| Verification ID | Exact command | Exit / result / counts / skips | Environment | Evidence path/hash |
|---|---|---|---|---|
| V1A.1 | `deploy/render.sh --out .local-dry/config`; then `(cd .local-dry && ../target/release/openzeppelin-monitor --check)` | render exit 0 (gate: `Configuration validation completed successfully`, 0 ERROR, 31 monitors, 1 network); raw `--check` exit 0, same success line, 0 ERROR lines | publicnode `MAINNET_RPC_URL`; raw run with dummy env for every referenced name | head `9de75ec` |
| V1A.1f | see attempt 1 and this pass: (1) mutate rendered monitor, raw `--check`; (2) mutate template, `deploy/render.sh --out .local-dry/config` | (1) **raw exit 0 + `ERROR … Failed to load monitors`** (2) render.sh exit **1**; template restored with `git checkout --`, `git status --porcelain` = only untracked worker record; re-render exit 0. Credited per coordinator reply A (accepted recorded deviation: raw exit code is not a gate) | as above | `9de75ec` |
| extra (reply A) | template trigger reference set to a non-existent key | render.sh exit 1 (`references non-existent trigger`), `deploy/check-templates.sh` exit 1 (`S6`); restored | — | `ad7ce32` |
| V1A.2 / V0.6 | `jq -r '.. \| objects \| select(.type? == "plain") \| .value' deploy/config/triggers/*.json deploy/config/networks/*.json` | 0 lines (exit 0) | — | `9de75ec` |
| V1A.2 / V0.3 | `git grep -nE "discord\.com/api/webhooks/[0-9]+/\|[0-9]{8,10}:AA[A-Za-z0-9_-]{30,}" -- . ':!tests/integration/fixtures'` | 0 rows (git grep exit 1) | — | `9de75ec` |
| V1A.2 / CP0 QA follow-up | `deploy/check-templates.sh` (committed, read-only: S1 JSON, S2 plain scan, S3 secret-shape scan under `deploy/`, S4 trigger required fields incl. credential env refs and the `${TELEGRAM_CHAT_ID}` placeholder, S5 network, S6 monitor→trigger/network/script resolution) | exit 0: `checker: OK (31 monitors, 29 triggers, 1 network)` | — | `9de75ec` |
| checker falsifiers | (a) plain token in `telegram_oracle_failed`; (b) token deleted (the exact CP0 QA finding 2 repro: V0.6 stays 0 rows exit 0); (c) `chat_id` deleted; (d) `discord_url` deleted; (e) a run-time-built dummy token line appended to tracked `deploy/render.sh` (V0.3f form) | (a) V0.6 1 row, checker exit 1 (S2+S4); (b) V0.6 0 rows, checker exit 1 (S4); (c) exit 1 (S4); (d) exit 1 (S4); (e) V0.3 1 row, checker exit 1 (S3); each restored with `git checkout --`; after restore V0.3 0 rows, checker exit 0, porcelain clean | — | `ad7ce32` |
| V1A.3 | `scripts/local-dry-run.sh --monitor-path config/monitors/ssv_failed_tx_inbox.json --network evm_ethereum_mainnet --block 26239255` (reverted `liquidate` `0x004dda433d…` from `0x0431…`, receipt status false, found with Blockscout v2) | exit 0; 1 match; `.local-dry/data/inbox/failed-tx.jsonl` 0 → 1 line; 11 fields exactly as §6 (`schema,tx_hash,block,block_hash,from,to,selector,function,status,monitor,observed_at`), `selector` `0xbf0f2fb2` = first 4 bytes of the input read back with `cast tx … input` and = `cast sig liquidate(…)`; `block` 26239255, `status` failure; 0 ERROR lines. Control block 26239145 (successful `liquidate`): 0 matches, inbox unchanged | publicnode | `9de75ec` |
| inbox script extras | 40 concurrent appends via `sh -c "$(cat inbox_append.sh)"`; invalid JSON input; incomplete match | 40 lines, 40 valid JSON; invalid input exit 5, no file created; incomplete match exit 5, no file | local | `4eee016` |
| V1A.4 | `scripts/local-dry-run.sh --monitor-path config/monitors/ssv_updater_other_sender_monitor.json --network evm_ethereum_mainnet --block 26177498` | exit 0; **1 match** `0x0bdd3bf694…` from `0x28bE…` (status Success, function `updateClusterBalance(…)`), 0 ERROR lines. The same block also contains a `0x16A5…` `updateClusterBalance` (`0x16c76eb989…`, read by `eth_getBlockByNumber`): sender discrimination inside one block | `https://eth.drpc.org` | `ad7ce32` |
| V1A.4f | same one-shot at block 26239696 (the only proxy transaction is one `updateClusterBalance` from `0x16A5…`, read by `eth_getBlockByNumber`) | exit 0; **0 matches**, 0 ERROR | drpc | `ad7ce32` |
| V1A.4f falsifier | template `transactions[0].expression` set to `null` (expression ignores `from`), same block | **1 match** (control red); expression restored; control back to 0 matches; porcelain as before | drpc | `ad7ce32` |
| V1A.5 | `scripts/local-dry-run.sh --monitor-path config/monitors/ssv_retired_addresses_monitor.json --network evm_ethereum_mainnet --block <B>` | B=26022191 (incoming 1,933,324,399,558 wei from `0x2650…` to `0x3A98…`): **1 match**; B=17661857 (1 ETH from `0x3019…` to `0x4BC6…`): **1 match**; control B=26239696: 0 matches; 0 ERROR lines in all | drpc | `ad7ce32` |
| V1A.5 extras | M13 old-key rule `ssv_retired_updater_attempt_monitor` at 26167892 (`commitRoot` from `0x3A98…`, 2026-10-11) and at control 26239655 (4 `commitRoot` txs from other senders); `ssv_retired_addresses_monitor` at 26169289 (outgoing transfer from `0x3A98…`) | 1 match / 0 matches / 1 match | drpc | `ad7ce32` |
| V1A.6 | `FeesSynced` pipeline proof at block 26240359 (tx `0x1c618a1d3f…`, `FeesSynced` newFeesWei 4977096920800000, confirmed by `cast logs` topic0 `0x9386d77e…`) | baseline `== 0`: 0 matches; template mutated to `newFeesWei > 0`: **1 match**, args show `newFeesWei` 4977096920800000; restored with `git checkout --` (`== 0`): 0 matches; porcelain clean | publicnode | `ad7ce32` |
| V1A.6 extra | `UnstakeRequested` positive control: expression temporarily `user ==` the staker of that block | 1 match; restored, clean | publicnode | `ad7ce32` |
| V1A.7 | `deploy/check-m15-coverage.sh` (spec M15 names extracted from the spec section, compared with `.match_conditions.events[].signature`; also the literal `diff <(jq …) <(spec list)`) | exit 0: 16 spec names, all present in both DAO success monitors (25 events; `diff` shows only `<` lines = 9 extras: `AdminChanged BeaconUpgraded ERC20Rescued Initialized ModuleUpgraded NetworkEarningsWithdrawn OwnershipTransferStarted OwnershipTransferred SSVNetworkUpgradeBlock`); nothing to add; every signature also exists in the ABI. Falsifier (not named in the plan): remove `QuorumUpdated` → `missing: QuorumUpdated`, exit 1; restored, exit 0 | — | `ad7ce32` |
| V1A.8 | `docker compose -f docker-compose.yaml config` | exit 0 with no env; `/app/data` bind source = `<worktree>/data` (0 occurrences of `/dev/null`); with `MONITOR_DATA_DIR=/srv/ssv-data` the source follows it; `MAINNET_RPC_URL`, `MAINNET_RPC_URL_2`, `TELEGRAM_TOKEN`, `DISCORD_WEBHOOK_*` pass through (`null` when unset, value when set); rendered network (`deploy/render.sh --out …/config`): `max_past_blocks` 3600, `recovery_config` `{enabled:true, cron "0 */5 * * * *", max_blocks_per_run 100, max_block_age 7200, max_retries 3, retry_delay_ms 1000}`, 2nd RPC present only when `MAINNET_RPC_URL_2` is set (weight 50) | Docker Compose v5.6.0 | `9de75ec` |
| render.sh modes | `--strict` with `TELEGRAM_CHAT_ID` unset; with all names set; with one webhook unset; `TELEGRAM_CHAT_ID` substitution | strict: exit 1 / 0 / 1 (`unset environment variables: DISCORD_WEBHOOK_INFO`); the chat id replaces only the exact `${TELEGRAM_CHAT_ID}` string, message bodies keep `${transaction.hash}`; rendered tree contains only `environment` references (no secret value) | — | `9de75ec` |
| local-dry-run | `scripts/local-dry-run.sh --check` with dummy `TEST_*` values and without | both: success line; the dry config references only `TEST_DISCORD_WEBHOOK` / `TEST_TELEGRAM_TOKEN` (no production name); without TEST channels only `local_log` and the inbox script trigger remain | publicnode | `9de75ec` |

- Falsifiers: all rows above; every mutation restored (`git checkout --` or by-value restore of the committed content) and `git status --porcelain` shown equal to the pre-mutation state (only the untracked `workers/cp1a/` record until the final commit).
- Deviations and findings (affected owners):
  1. **`--check` exit code vacuous** (accepted by the coordinator, reply A): V1A.1/V1A.1f credited against the render.sh gate only. Owner: Rust core, outside this plan.
  2. **Expression grammar uses `AND`/`OR`, not `&&`/`||`**, and **`gas_used` is empty without a receipt** (ERROR per transaction). A rule written with `&&` silently never matches. Owner: whoever writes later Monitor rules (CP3, Marco); the committed checker does not parse expressions.
  3. **`status: Success` mislabels reverted transactions** in blocks that contain logs (measured at 26,239,655 and 26,177,500). The existing `*_success` monitors (oracle, liquidation, DAO, …) therefore also alert on reverted calls. Fix needs a `src/` change or `status: Any` rewrite; owner CP3/Marco. The new M11 rule inherits it (info only).
  4. **Live `config/` has two duplicate trigger-name pairs** (`DAO Safe Failed Alert`, `Oracle Dissent Alert`); the Monitor refuses to load triggers (`Duplicate trigger name found`). Templates fixed (two Discord names prefixed); Marco's live `config/` untouched. Owner Marco (deploy step).
  5. **`.gitignore` lacks `data/**/*.jsonl`**: the inbox file would show as untracked in a dev checkout using `./data`. `.gitignore` is outside this lane. Owner coordinator/CP3.
  6. **M13 split**: `functions` AND `transactions` means the plan's single M13 monitor cannot hold both "incoming value" and "proxy calls from the old key"; delivered as `ssv_retired_addresses_monitor` (the four addresses, `value > 0`) and `ssv_retired_updater_attempt_monitor` (proxy, `commitRoot`/`updateClusterBalance`, `from == 0x3A98…`). Not covered: a zero-value call by the two attacker addresses to an SSV contract (spec M13 row 3); owner coordinator (add a monitor per contract if wanted).
  7. M10/M15: all 16 M15 names and `OracleReplaced` are already matched by both DAO success monitors; no events added. Routing is unchanged from CP0 (the Telegram DAO monitor and the Discord twin `DISCORD_WEBHOOK_DAO_SUCCESS`); D2 puts info on Discord only, so re-pointing is a DEF-2 decision for Marco.
  8. `max_past_blocks` 3600 (≈ 12 h, one full oracle cadence) is a sizing choice recorded in the template; larger gaps skip blocks (Monitor logs `skipped N blocks`).
- Resulting head: `9de75ec` (+ the commit holding this record and the status line)
- Next action: completion handoff, status line, commit, worker_done.


<a id="repair-1"></a>
## Repair 1 — fixes for CP1A QA attempt 1 (F1, F2, F3, F4, F8; F5/F6/F7 recorded, not touched)

- Observed UTC: 2026-10-21T12:42Z (end of repair); work 2026-10-21 from about 12:10Z
- Task / Dispatch: `task_2888efd1d913` / `ctx_01667b707874` (fresh implementer, Claude Sonnet 5.5 high); coordinator terminal `term_89e2fa6f-482b-4551-995f-1f0e41c726a2`, worker terminal `term_e62ecf44-ace4-4dfc-9a21-d8708d3208e4`
- Frozen start head: `3f9bf7ade84fd963dd5fc6956c46c8a9c7731147` (`git rev-parse HEAD` confirmed before any write; porcelain clean). Input: [qa/cp1a-attempt-1.md](../../qa/cp1a-attempt-1.md) §1–§4 and the harness in `/tmp/ms-qa-cp1a/` (read; rebuilt as committed scripts below, not reused).
- Implementation head: `10c7a8f9dddde10a42ede04a1a86528030ee07a1` (+ a docs-only record commit with this entry and the status line). Not touched: `src/`, `qa/**`, `execution-log.md`, `.gitignore`, `config/`, `config.zip`, `.env`.

### What changed (one line per finding)

| Finding | Change | Regression |
|---|---|---|
| F1 HIGH (M13 row 3) | New monitor `ssv_attacker_interaction_monitor` + trigger `telegram_attacker_interaction` (page, existing `TELEGRAM_TOKEN`/`TELEGRAM_CHAT_ID`, no new variable). Addresses: proxy, Views, SSV token, cSSV, DAO Safe, our wallets `0x16A5…`, `0x0431…` and both attacker addresses. One `transactions` condition, `status: Any`, no `value` predicate: `((from == A1 OR from == A2) AND (to == <contracts> OR to == <wallets>)) OR ((to == A1 OR to == A2) AND (from == <wallets>))`. Zero-value and reverted interactions page; an attacker transfer to an unrelated address does not (spec row 3 is literal: SSV contracts or our wallets). Attackers were removed from the value monitor. | `deploy/tests/m13-fork-regression.sh` (anvil fork, local transactions only): 15 cases, 8 attacker/wallet cases incl. QA's `commitRoot` zero-value revert; red on the old tree, green on the new |
| F4 MEDIUM | `ssv_retired_addresses_monitor`: addresses = the two retired keys only; expression `(to == 0x3A98… OR to == 0x4BC6…) AND value > 0` (incoming only). `to` is an empty string for contract creations; measured 0 ERROR lines at block 26240350 (374 txs, 1 creation). Old slot-4 `commitRoot`/`updateClusterBalance` attempts stay in `ssv_retired_updater_attempt_monitor` (unchanged, status Any). | Real block 26169289 (QA's outgoing transfer): old 1 match → new 0; 25991146 (incoming): 1; fork cases in/out |
| F3 MEDIUM | `ssv_dao_success_monitor` (Telegram) drops every M15/M10 event (the 16 spec names + `ModuleUpgraded`) and the governance functions that emit them (`replaceOracle`, `upgradeTo`, `upgradeToAndCall`, all `update*`). The Discord twin keeps all 25 events/functions. `deploy/check-m15-coverage.sh` now checks coverage on the Discord monitor and fails if any non-Failure monitor routed to a Telegram trigger carries those events/functions unconditionally. Any M10 page decision stays with the stateful worker. | Real `OracleReplaced` block 26169066 on `https://eth.drpc.org`: old Telegram monitor 1 match → new 0; Discord monitor 1; `selftest.sh` re-adds the event/function to a copy and expects red |
| F2 HIGH | `deploy/check-templates.sh`: S0 input validation (missing/empty `--config-dir`, missing `--root` or `deploy/`, missing option value), grep exit codes handled (0 found = fail, 1 clean, >1 = "secret scan failed"), jq failures not swallowed, monitors must have `networks` and a `match_conditions` with a non-empty list, counts come from the checked directory (`find`, no `ls`), S7 calls the ABI check. | `selftest.sh` (12 F2 cases) and the QA's three exact commands: old exit 0 / `checker: OK` → new exit 1 |
| F8 HIGH | New `deploy/check-abi.sh --config-dir <dir>` (A1–A7: non-empty conditions, ABI present, every event/function signature canonical-matches an ABI entry incl. tuples, expression parameter names exist on that event/function, tx expression fields known, no `&&`/`\|\|`). `render.sh` runs it on the rendered config before the Monitor `--check` (skipped with `--no-check`, like the Monitor check) and exits 1 on failure; `check-templates.sh` runs it as S7. The render gate semantics are unchanged (event-0013: success line + zero ERROR lines); the Rust `--check` boundary is untouched. | Tracked-template mutation `Nonexistent(uint256)` in `ssv_seed_stake_monitor.json`: render.sh exit 1, restored by `git checkout`, exit 0, porcelain empty (rows in `evidence/repair-1/vrows-run.log`) |

### Deviations, decisions and notes for the coordinator

1. **F3 scope decision (judgment call):** I removed the *functions* that emit the M15 events from the Telegram DAO monitor as well as the events, otherwise a successful `replaceOracle` call would still reach Telegram unconditionally. Consequence: Telegram no longer gets governance *Success* function pings for `update*`/`upgradeTo*`/`replaceOracle`; Failure pages (`ssv_dao_failure_monitor`) are unchanged. The non-M15 extras stay on Telegram (events `OwnershipTransferStarted`, `OwnershipTransferred`, `AdminChanged`, `BeaconUpgraded`, `Initialized`, `SSVNetworkUpgradeBlock`, `NetworkEarningsWithdrawn`, `ERC20Rescued`; functions `acceptOwnership`, `initialize*`, `renounceOwnership`, `rescueERC20`, `transferOwnership`, `withdrawNetworkSSVEarnings`): the spec does not classify them; coordinator/Marco decide. The Discord twin still posts to the legacy variable `DISCORD_WEBHOOK_DAO_SUCCESS`, not `DISCORD_WEBHOOK_INFO` (F5, CP3).
2. **F1 target set** is my reading of "SSV contracts or our wallets": spec §4/§6 addresses (proxy, Views, SSV token, cSSV, DAO Safe) and the two M12 wallets. Calls that reach the proxy through another contract (internal calls) are invisible to a transaction-level rule. Attackers no longer page on a plain value transfer with an unrelated counterparty (previously `value > 0` in either direction); say so if the spec intends that.
3. `check-abi.sh` A5 (expression parameter names) is a regex heuristic on `NAME <operator>`; A1–A4 are exact. It accepts the 32 tracked monitors; a future expression form it cannot parse would be reported by its operator-less identifiers only if they precede a comparison.
4. `render.sh --no-check` (used by `scripts/local-dry-run.sh --prepare`) skips both the ABI and the Monitor check, as before.
5. Not repaired, per the brief: F5 legacy Discord variable names (CP3; the coordinator asks Marco), F6 Rust `status: Success` mislabel (unchanged; `ssv_dao_success_monitor` and the other `*_success` monitors still also match reverts), F7 `.gitignore` for `data/**/*.jsonl` (CP3).
6. Archive endpoints: replays on `https://eth.drpc.org` (nonempty blocks/logs); `https://ethereum-rpc.publicnode.com` only as `MAINNET_RPC_URL` for the render/`--check` rows. The drpc free tier rejected `eth_getLogs` ranges of 1000+ blocks ("ranges over 10000 blocks are not supported on free plan"); the OracleReplaced block 26169066 was found with the Blockscout v1 `module=logs&action=getLogs` API (read-only GET) and confirmed by the one-shot itself (1 match on the Discord monitor).

### Measurements on the repaired head (`evidence/repair-1/vrows-run.log`, produced by `evidence/repair-1/vrows.sh`; every row has exit code and counts)

| Row | Result |
|---|---|
| V1A.1 | render exit 0, abi-check OK, success line, 0 ERROR, 32 active monitors / 1 network (publicnode); raw `--check` exit 0 + success line + 0 ERROR |
| V1A.1f | raw `--check` after removing the closing brace of a rendered monitor: exit 0, no success line, 2 ERROR (the accepted deviation: the raw exit code is not a gate); render.sh with the same brace removed in the template: exit 1 (now first caught by `check-abi.sh` ABI-1 unparseable); render.sh with a dangling trigger (valid JSON, ABI-clean): exit 1 with abi-check OK, 3 ERROR lines, no success line, i.e. the event-0013 Monitor-log gate still trips on its own; restored: exit 0, porcelain empty |
| V1A.1 ABI falsifier (F8) | mutation shown with `git diff --stat` (1 file, 1+/1-); render.sh exit **1** (`A3 event Nonexistent(uint256) is not an event in the ABI`), `check-templates.sh` exit 1 (S7); restored by `git checkout`: render exit 0, porcelain empty |
| V1A.2 | 0 plain values; `git grep` secret shapes exit 1 / 0 rows; checker `OK (32 monitors, 30 triggers, 1 network)` |
| F2 | absent / empty `--config-dir` and missing `--root`: exit 1 each with an S0 message; monitor without `networks` / `match_conditions`: exit 1; restored exit 0, porcelain empty |
| V1A.3 | 26239237 (reverted `liquidate`): 1 match, inbox +1, 11 keys, 0 ERROR; 26239259 control: 0 / +0; 26239255 (implementer block): 1 / +1 |
| V1A.4 / V1A.4f | 26177496: 1 match; 26239694 own sender: 0; sender predicate removed 1; restored 0; all 0 ERROR |
| V1A.5 | 25991146: 1 match; F4 26169289 outgoing: 0; old-key attempt 26167892: 1; other-sender control 26239655: 0 |
| V1A.6 | 26240350: `== 0` 0 → `> 0` 1 → restored 0, porcelain empty |
| V1A.7 | all 16 M15 names on the Discord DAO monitor (9 extras listed); no M15/M10 info routed to Telegram |
| V1A.8 | `docker compose config` exit 0, `/app/data` → worktree `data`; `max_past_blocks` 3600, `recovery_config` present, 1 RPC |
| selftest / fork | `deploy/tests/selftest.sh`: 31 passed, 0 failed, 0 skipped; `deploy/tests/m13-fork-regression.sh` (fork of `https://eth.drpc.org` @ 26240350, anvil, local txs only): 15 cases, 0 failures; the same script on `3f9bf7a` templates: 15/15 failures (`evidence/repair-1/red-baseline-3f9bf7a.log`, also old F3/F4 1 match each and old checker exit 0 on the QA's missing-input commands) |

- Falsifiers: all red then restored; `git status --porcelain` after the run shows only the evidence log written by the run.
- Resulting head: see the status line in plan.md (docs-only commit after `10c7a8f`).
- Next action: fresh Codex QA; I clear no finding.


<a id="repair-2"></a>
## Repair 2 — F8b (ABI layout gate) fixed; F1b recorded as an M13 coverage boundary (F5/F6/F7 untouched)

- Observed UTC: 2026-10-21 (afternoon); Task / Dispatch: `task_8f80ac8c19a9` / `ctx_da0703b996d4` (fresh implementer, Claude Sonnet 5.5 high); worker terminal `term_d3ab588f-8395-4f97-961a-3bb665e0bb8f`.
- Frozen start head: `6c94b27f904818e85242a7dc91c69f078fe4c813` (`git rev-parse HEAD` confirmed before any write; porcelain clean). Input: [qa/cp1a-attempt-2.md](../../qa/cp1a-attempt-2.md) §1 F1b/F8b, §2.
- Implementation head: `f04c79ec046c96311185199cedf4778957a43d0b` (+ a docs-only record commit with this entry, the evidence logs and the status line). Not touched: `src/`, `qa/**`, `execution-log.md`, `.gitignore`, `config/`, `config.zip`, `.env`, the monitor/trigger templates themselves (no template changed: every `contract_spec` already equals the reference).

### F8b (HIGH) — fixed

`deploy/check-abi.sh` compared canonical `name(types)` and expression names, never `indexed`, and compared the monitor with its own copied ABI. New checks **A8/A9** compare every event and function of every `contract_spec` (all 30 monitors that carry one, 3356 entries) with a committed reference, `deploy/abi/reference.json`, in full: name, `anonymous`, each input's name/type/tuple components/order and (events) `indexed`, each function's outputs. The shared normalizer is `deploy/abi/normalize.jq`.

| Reference | Derivation (never from the monitor under check) |
|---|---|
| SSV proxy `0xDD9B…a4E1` (122 events/functions) | `.abi` of `SSVNetworkSSVStakingUpgrade` from `forge build --skip test --skip script` of `ssvlabs/ssv-network` at `f7ac4870074b9408855a7912f9c6e1f959996be8` (plan pin `v2.0.0+f7ac487`, checkout via `git archive`). The deployed implementation is that contract: on-chain code hash of `0xa72a…08E9` = `0xf518bf4b…076b` = `contracts.lock.json`; selector `0xd0937f03` (`initializeSSVStaking`) occurs in the deployed code. The repo's committed `abis/SSVNetwork.json` at the pin lacks `initializeSSVStaking`, so it was not used. |
| DAO Safe `0xb350…E6b6` (46) | `.abi` of `@safe-global/safe-deployments@1.37.0` `v1.3.0/gnosis_safe.json`; its mainnet 1.3.0 address `0xd9Db…09552` is the Safe proxy's slot-0 singleton, `VERSION()` = `1.3.0`. |

On-chain verification of the layouts (`abi/provenance.json`; endpoint `https://eth.drpc.org`, read at block 26241166): real `UnstakeRequested` log, block 24920762, tx `0x17db7982…95a7`: 2 topics, 64 data bytes (= one indexed `user`); real `FeesSynced`, block 24920823: 1 topic, 64 data bytes; real Safe `ExecutionSuccess`, block 24060370: 1 topic, 64 data bytes (txHash not indexed in 1.3.0). The Safe has emitted no `ExecutionFailure`; its layout is the 1.3.0 ABI's. The monitors' existing specs are byte-for-byte (normalized) equal to both references; no template changed. `deploy/abi/derive.sh --check` reproduces `reference.json` from the two sources (exit 0).

Mutations proven red then restored green (tracked template mutated, `git checkout` restore, porcelain empty): indexed flags swapped user/amount (QA's case), input type changed, input order swapped, extra input, missing input, function output changed. `render.sh` exit 1 with `A9 …` each, `check-templates.sh` exit 1 (S7), restored `render.sh` exit 0 (rows in `evidence/repair-2/vrows-run.log`). The sweep `deploy/tests/abi-mutation-sweep.sh` applies 16 mutation kinds (indexed flipped/swapped, type, order, extra/missing input, rename, anonymous; function input and output variants) to **every contract_spec entry of all 30 specced monitors** at once and requires exit non-zero and exactly one `A9` per mutated entry: 510 checker runs, 0 failures. The same sweep against the repair-1 gate (`evidence/repair-2/red-baseline-abi-sweep-repair1-gate.log`): 480 failing cells, and in 359 of them the old gate exited 0 on a mutated ABI.

### F1b (HIGH) — NOT repaired, by design: documented

`deploy/README.md` section "M13 coverage boundary" lists exactly what the three M13 templates cover (direct transactions: from/to/value/function, incoming direct value to the retired keys, attacker direct interactions incl. reverted and zero-value) and what the Monitor cannot see (internal CALLs to the retired keys, attacker paths through intermediate contracts, constructor-created transfers, SELFDESTRUCT value), citing QA attempt 2's reproducers (blocks 26240404–26240418 and the minimal helper repro). It states that no template can close it and that CP1A does not deliver "any interaction / any incoming value" for M13. No scope change; the coordinator is asking Marco whether balance-delta watching of the retired keys moves into the stateful worker.

### Measurements on `f04c79e` (`evidence/repair-2/vrows.sh` → `vrows-run.log`, every row has exit and counts; endpoints per row)

| Row | Result |
|---|---|
| V1A.1 | render exit 0 (`https://ethereum-rpc.publicnode.com`), abi-check OK (32 monitors, 3356 entries equal the reference), 32 active monitors, 0 ERROR; raw `--check` exit 0, success line, 0 ERROR |
| V1A.1f | brace removed: raw exit 0, no success line, 2 ERROR (accepted deviation); render.sh exit 1 (ABI-1 unparseable); dangling trigger: render exit 1, abi-check OK, 3 ERROR, no success line; restored 0 |
| V1A.1 ABI falsifiers | `Nonexistent(uint256)`: render 1, check-templates 1, restored 0; F8b six mutations above |
| V1A.2 | 0 plain values; secret-shape grep exit 1 (0 rows); checker OK (32 / 30 / 1) |
| V1A.3 (`eth.drpc.org`) | 26239237: 1 match, inbox +1, 11 keys; 26239259 control 0/+0; 26239255: 1/+1; 0 ERROR |
| V1A.4/4f | 26177496: 1; 26239694 own sender: 0; predicate removed: 1; restored 0; 0 ERROR |
| V1A.5 | 25991146 incoming: 1; 26169289 outgoing: 0; 26167892 old-key attempt: 1; 26239655 control: 0 |
| V1A.6 | 26240350 `== 0`: 0 → `> 0`: 1 → restored 0 |
| V1A.7 | all 16 M15 events on Discord (9 extras); none routed to Telegram; real OracleReplaced 26169066: Telegram 0 / Discord 1 |
| V1A.8 | `docker compose config` exit 0, `/app/data` → worktree `data`; `max_past_blocks` 3600, recovery config present, 1 RPC |
| selftest | 51 passed, 0 failed, 0 skipped (31 baseline + 20 new F8b cases incl. the sweep and render.sh) |
| fork suite | `deploy/tests/m13-fork-regression.sh`: 15 cases, 0 failures (anvil fork of `https://eth.drpc.org` @ 26240350, local txs only) |

### Notes

1. A8/A9 do not cover `error`/`constructor`/`fallback` entries, `stateMutability` or `internalType`, or expression semantics (A5 stays a name check). A reference entry missing from a spec is tolerated; a spec entry missing from or different to the reference is red.
2. The reference is only as authoritative as its derivation: re-derive (and re-verify on chain) whenever the proxy implementation or the Safe changes; `contracts.lock.json` drift is the trigger.
3. F5/F6/F7 unchanged. Fresh QA decides; I clear no finding.

- Resulting head: see the status line in plan.md (docs-only commit after `f04c79e`).
- Next action: fresh Codex QA.


<a id="repair-3"></a>
## Repair 3 — F9 fixed; F1b closed by owner decision D10 (documented); D11 Discord routing (F5 resolved for the collapsed six)

- Observed UTC: 2026-10-21 (evening); Task / Dispatch: `task_a4c6f85f4579` / `ctx_b8fbc82c69b7` (fresh implementer, Claude Sonnet 5.5 high); worker terminal `term_41c7f252-6a26-459c-afc0-c2ca4adcbfb3`.
- Frozen start head: `204d52822b35af4bd8eec1e6b1ce59e5103d878e` (`git rev-parse HEAD` confirmed before any write; porcelain clean). Input: [qa/cp1a-attempt-3.md](../../qa/cp1a-attempt-3.md) §1 F1b, F9, §2; D10 and D11 as recorded in plan.md §2 on the CP2 lane branches (the lane's own plan.md §2 has no D10/D11 rows yet; I may write only the status line, so the coordinator carries them).
- Implementation head: `e981687df50b5e0efc72bcf76372636a155c78d7` (+ a docs-only record commit with this entry, the evidence log and the status line). Not touched: `src/`, `qa/**`, `execution-log.md`, `.gitignore`, `config/`, `config.zip`, `.env`, monitor templates (no monitor template changed, only six trigger templates).

### F9 (MEDIUM) — fixed

`deploy/check-m15-coverage.sh` required only the 16 backticked spec names; `ModuleUpgraded` (spec "module updates") was added only to the Telegram check, so deleting it from the Discord monitor exited 0. Now the required set is the spec's backticked M15 names plus `ModuleUpgraded` (17), each resolved to its full `name(types)` through the committed reference ABI (`deploy/abi/reference.json`, proxy entry; the run fails with exit 2 if a name is not exactly one event there or if M15 stops saying "module updates"). A monitor must carry every one of the 17 signatures, so deleting any one, or changing its types, is red. The Telegram check (no unconditional M15 event or governance-update function on a Telegram route, D2) uses the same 17 names.

Proof (rows in `evidence/repair-3/vrows-run.log`, `selftest.sh` F9/D2 sections): the old gate (`204d528`) on a Discord monitor without `ModuleUpgraded`: exit **0** (the defect, red baseline); the new gate on each of the 17 individual deletions from the tracked template: exit 1 each with the `missing:` line, restored exit 0, porcelain clean; selftest repeats all 17 deletions (scratch copies), a wrong-type case (`QuorumUpdated(uint32)`), and all 17 additions to the Telegram DAO monitor (each red); the tracked Telegram DAO monitor has 0 unconditional M15 events.

### F1b — closed by owner decision D10; README only

`deploy/README.md` "M13 coverage boundary" now cites D10 and gives, row by row (retired slot 4, retired liquidator, attackers), what the Monitor covers, what CP2c's worker per-block balance-delta watch will cover (retired keys only, not delivered here), and the ACCEPTED gap (within-block net-zero on the retired keys; attacker internal/constructor/SELFDESTRUCT/third-party interactions and internal value into our wallets). The measured direct cases and the reproduced misses (QA attempts 2 and 3 blocks) are kept. No template, expression or rule changed for F1b; the fork suite is unchanged.

### D11 — Discord routing (F5 resolved for the collapsed six)

The six `*_SUCCESS` triggers (cluster, dao, liquidation, operator, oracle, validator) now read `DISCORD_WEBHOOK_INFO`; the nine others keep their own variable (six `*_FAILED`, `ORACLE_DISSENT`, `DAO_SAFE_FAILED`, `LARGE_ETH_TRANSFER`); display names stay unique. Edited: the six trigger templates, `.env.example` (six names removed, comments), `docker-compose.yaml` passthrough (six removed). `render.sh` has no variable list of its own: it derives the required variables from the rendered config, so `--strict` now needs `DISCORD_WEBHOOK_INFO` and no longer the six names (selftest: strict with INFO unset red, strict with exactly the referenced set green). `check-templates.sh` S4 gains D11 assertions (info triggers `_success`/`_other_sender` must use INFO, no other Discord trigger may, every `discord_url` stays an environment reference). Selftest: 51 → 119 cases (17 + 17 + 2 F9/D2 cases, 6 × 3 + 1 + 9 D11 template cases, 2 render --strict cases, a compose/`.env.example` passthrough case). `git grep` of the six removed names outside `plans/` and `qa/`: exit 1, 0 rows; the only remaining mentions are the append-only history in this file (`attempts.md`) and the QA records, which this lane may not edit.

### Measurements on `e981687` (`evidence/repair-3/vrows.sh` → `vrows-run.log`; endpoints per live row in the log)

| Row | Result |
|---|---|
| V1A.1 | render exit 0 (`https://ethereum-rpc.publicnode.com`), abi-check OK (3356 entries equal the reference), 32 active monitors, 0 ERROR; raw `--check` exit 0, success line, 0 ERROR |
| V1A.1f | brace removed: raw exit 0, no success line, 2 ERROR; render.sh exit 1 (ABI-1); dangling trigger: render exit 1, abi-check OK, 3 ERROR, no success line; restored 0 |
| V1A.1 falsifiers | F8 `Nonexistent(uint256)`: render 1, check-templates 1, restored 0; six F8b mutations red/restored; sweep 510 runs 0 failures (exit 0) |
| V1A.2 | 0 plain values; secret-shape `git grep` exit 1 (0 rows); checker OK (32 monitors / 30 triggers / 1 network) |
| F2 | absent/empty `--config-dir`, missing `--root`, monitor without `networks`/`match_conditions`: exit 1 each; restored 0 |
| V1A.3 (`eth.drpc.org`) | 26239237: 1 match, inbox +1, 11 keys; control 26239259: 0/+0; 26239255: 1/+1; 0 ERROR |
| V1A.4 / V1A.4f (`eth.drpc.org`) | 26177496: 1; own sender 26239694: 0; predicate removed 1; restored 0 |
| V1A.5 (`eth.drpc.org`) | 25991146 incoming: 1; 26169289 outgoing: 0; 26167892 old-key attempt: 1; 26239655 control: 0 |
| V1A.6 (`eth.drpc.org`) | 26240350: `== 0` 0 → `> 0` 1 → restored 0 |
| V1A.7 | 17 required events all present (8 extras); no M15/M10 info routed to Telegram; 17 deletions red; old-gate baseline exit 0; real OracleReplaced 26169066 (`eth.drpc.org`): Telegram 0 / Discord 1 |
| D11 | six on INFO, nine own variables, 0 duplicate names, 0 removed names outside plans/; missing `discord_url` falsifier exit 1; restored render 0 |
| V1A.8 | `docker compose config` exit 0, `/app/data` → worktree `data`; `max_past_blocks` 3600, recovery config present, 1 RPC |
| selftest | 119 passed, 0 failed, 0 skipped (baseline 51) |
| fork suite | `deploy/tests/m13-fork-regression.sh`: 15 cases, 0 failures (anvil fork of `https://eth.drpc.org` @ 26240350, local txs only) |

### Notes

1. Not touched: F6 (inherited `Success` label), F7 (inbox `.gitignore`), the nine kept Discord names (intentional).
2. D10/D11 rows are missing from this lane's plan.md §2; they exist on the CP2 lane branches. Reconcile at integration.
3. I clear no finding; fresh QA decides.

- Resulting head: see the status line in plan.md (docs-only commit after `e981687`).
- Next action: fresh Codex QA.



## Repair 4 — F10: the structural checker (and its siblings) failed open

- Observed UTC: 2026-10-21; Task / Dispatch: `task_1e49252c142c` / `ctx_8a8217f82c12` (fresh implementer, Claude Sonnet 5.5 high).
- Frozen start head (`git rev-parse HEAD` before any write; porcelain clean): `72029c05a0e08feff67f9ed585638f98c459f7c8` (`git rev-parse HEAD` at start; QA evidence commit over `45fe9403`). Code head after repair: `f4eef81fb4b98260f908c6626e80d82280d6c087` (docs-only record commits follow). QA report: [cp1a-attempt-4](../../qa/cp1a-attempt-4.md) §1 F10.

## Root cause and the class

F10: S4 ran `msgs=$(jq ... "$f")` and fed `emit`; jq's exit status was never read, so a jq runtime error (`Cannot index string with string "discord_url"`) left `msgs` empty, which `emit` reads as "no violations", and the script printed `checker: OK`, exit 0. The same shape (a command substitution or a `for x in $(jq ...)` whose status is dropped, so an empty capture means "clean") occurred at many sites; the audit below lists every one. Fix, in three layers:

1. **Status**: every jq in the gates goes through `jqo` (check-templates) or an explicit `|| FAIL` / `|| exit`; a failing jq is a FAIL line with jq's own message, never an empty result.
2. **Shape**: new `deploy/shape.jq` (`monitor_shape`, `trigger_file_shape`, `network_shape`) turns a wrong type (config a string/array/number/null/true, events an object, addresses a string, ...) into one named FAIL line (S1b in check-templates, A0 in check-abi, `file_ok` in check-m15-coverage). It never raises.
3. **Inputs**: empty, unreadable, binary, looping or dangling files, and stray non-JSON files in `monitors/ triggers/ networks/`, are failures; jq missing or non-working (`jq -n 1+1` must print 2) exits non-zero with "jq is required".

## Audit: every site, its disposition, its regression

Legend: RED = the new regression fails on the frozen head `72029c0` (evidence/repair-4/red-frozen-72029c0.log: 49 ok / 125 FAIL, the 49 ok are the closed controls and the three baseline cases); CLOSED = already failed closed on the frozen head (by luck or `set -e`), kept and pinned by a regression anyway.

### deploy/check-templates.sh
| Site | Frozen behaviour | Disposition |
|---|---|---|
| S0 `find -print -quit` non-empty test; glob vs find mismatch (`.hidden.json`) | find error read as "no .json files" (CLOSED, wrong message); hidden file passed S0 but glob skipped it | listing status checked; every entry classified (readable regular `.json`, `.gitkeep`, `triggers/scripts`, else FAIL); count of regular files must be > 0. RED (empty/binary/stray/unreadable/loop/dangling x3 dirs) |
| S1 `jq -e . f >/dev/null 2>&1` | passes `5`, `"s"`, `[]`; message hides jq error; empty/`null` only red through `-e` | empty → "is empty"; exactly one JSON value; jq message shown. RED (empty, binary) |
| (new) S1b shape | wrong-typed config/trigger/monitor/network passed (string/array/number/true config OK, exit 0; triggers=string, networks=string, name=5, trigger_conditions=string, max_past_blocks="3600", rpc_urls[0]=5 all OK) | shape gate. RED (trigger config x5, entry x6, file x5, message, discord_url, 14 monitor shapes x2 gates, 4 monitor-file types, 7 network shapes) |
| S2 `plain=$(jq ...) \|\| fail`; `[ -z "$plain" ]` | status checked (CLOSED) but an EMPTY plain value printed an empty line → passed | counts `type=="plain"` objects with `-s length`. RED (empty plain), CLOSED (injection) |
| S3 `grep -rElI` | `-I` skipped binary files; `grep -r` does not follow symlinks (secret behind a symlink unscanned); `find`/grep error text handled | `-a` (binary scanned as text, LC_ALL=C), `find -H ! -type f ! -type d` symlink/special files are FAIL, grep rc 0/1/other kept. RED (binary with webhook, symlink) |
| S4 `trigger_keys=$(jq keys[] \| sort)`, `dup`, `dupname` | statuses never read; keys injection exited 1 only because S6 then found every trigger reference undefined (CLOSED by luck); `dupname` RED | `jqo`; "no trigger defined" FAIL added. CLOSED (keys), RED (names) |
| S4 per-file required-field jq (**F10**) | status never read → OK | `jqo`. RED (30 calls injected, exit 0 on frozen) |
| S4 script paths `for p in $(jq ...)` | status dropped, word-splitting, `basename` of a `-x` path | `jqo` + `while read`, `${p##*/}`. RED |
| S5 networks `msgs=$(jq ...)` | status never read | `jqo`. RED |
| S6 slugs / monitor names `$(jq ... \| sort)` | status dropped | `jqo`. RED (names), CLOSED-by-luck (slugs: `null` row) |
| S6 `[ "$(jq '.addresses \| length')" -gt 0 ]` (addresses, networks, triggers length) | jq error → `[: integer expression expected` → rc 2 → FAIL with an ugly message | `jqo` + numeric test. CLOSED, message fixed |
| S6 `for t in $(jq -r '.triggers[]?')`, `.networks[]?`, `.trigger_conditions[]?.script_path` | **fail open**: `?` swallows type errors and a failing jq leaves an empty loop, so `triggers: "x"` / `networks: "x"` passed unchecked | `jqo` + shape gate. RED |
| S6 `printf keys \| grep -qx "$t"` | regex (`.` matches any char: `telegram.seed.stake` passed against `telegram_seed_stake`), `-`-prefixed names are options, SIGPIPE under pipefail | `grep -qxF --` on a here-string. RED (literal-match case) |
| S6 match_conditions `jq -e` | CLOSED | `jqo` + `= true`. CLOSED |
| S7 `$(check-abi.sh) \|\| fail` | CLOSED | unchanged |
| OK line `count()` find/wc | cosmetic | array lengths |
| jq absence | `command -v jq` only (a stub that exits 0 would turn every scan into a pass) | `command -v jq` **and** `jq -n '1+1' == 2`. RED for a non-working jq; no-jq CLOSED |

### deploy/check-abi.sh
| Site | Frozen behaviour | Disposition |
|---|---|---|
| `for f in monitors/*.json; do [ -f "$f" ] \|\| continue` | **fail open**: a symlink loop, dangling link, directory or unreadable file was silently skipped (not counted, not failed) | non-regular / unreadable → FAIL; stray entries FAIL. RED (loop, dangling, unreadable) |
| empty monitor file | **fail open**: jq prints nothing for empty input, exit 0 → file passed | `[ -s ]` → "is empty". RED |
| wrong types (`.events[]?`, `.addresses[]?`) | mostly red by jq error or A2; `addresses=[5]`, `contract_spec="x"` red by accident; events as object depended on `+` erroring | A0 shape lines. RED (message) |
| `compared=$(jq -s ...)` then prints `OK (... $compared ...)` | **fail open**: jq failure printed OK with an empty count | status checked → exit 1. RED (injection exit 0 on frozen) |
| reference-validity jq, per-monitor jq | CLOSED | unchanged, pinned by injection |
| jq absence | as check-templates | as check-templates |

### deploy/check-m15-coverage.sh
| Site | Frozen behaviour | Disposition |
|---|---|---|
| `spec=$( { grep -o ...; echo ModuleUpgraded; } \| sort -u)`; `[ -n "$spec" ]` | **fail open**: `ModuleUpgraded` makes the set never empty, so a truncated/edited M15 section silently shrank the required set to 2 and the gate passed | named-event count must equal 16 (plan pin; +ModuleUpgraded = 17), else exit 2. RED |
| `names_json=$(... \| jq -R . \| jq -s -c .)` unchecked | CLOSED by luck (later error) | checked. CLOSED |
| `req=$(jq ... 2>/dev/null \| sort -u)` | stderr discarded; a mid-stream failure could leave a partial list | checked, stderr kept, count must equal the required-name count |
| `have=$(jq ... \| sort -u)` | failure → empty → "all missing" (CLOSED, but wrong reason); `events` as an object iterated its values and passed | status + shape. RED (events object) |
| `comm -23 <(...) <(...)` | process-substitution statuses unchecked | replaced by `grep -qxF` loops |
| `tg=$(jq -s 'add \| ...')` | **fail open**: `add` merged by key, so a later file redefining `telegram_dao_success` as Discord hid the Telegram route → the whole D2 check passed | per-file `to_entries`, shape-checked. RED |
| `bad=$(for f in monitors/*.json; do jq ... "$f"; done)` | **fail open**: a monitor on which jq failed (malformed, `triggers` a string, binary) was silently skipped from the D2 routing check | each file shape-checked and its jq status checked → FAIL. RED (triggers string, binary, injection) |
| SPEC missing | `awk` error → "no longer mentions module updates" (CLOSED, wrong message) | explicit check |
| jq absence | none (message was "cannot read the proxy ABI") | RED |

### deploy/render.sh
| Site | Disposition |
|---|---|
| `rewrite()` `jq > tmp && mv` | CLOSED by `set -e` (the function's status trips it) but left `*.tmp` and no message → explicit check, tmp removed, message |
| `.rpc_urls \|= map(...)` | **coerced** a malformed `rpc_urls` object `{}` into `[]` and rendered green (exit 0 on frozen) → now errors unless it is an array. RED |
| Telegram `map_values` on a non-object file | now an explicit error. CLOSED→pinned |
| `refs=$(jq ... \| sort -u)` | CLOSED by set -e/pipefail, silent → message |
| `printf \| grep 'Found N' \| sed` at the end | CLOSED (silent exit 1) → explicit message, and the "Found N active monitor(s)" count must equal the number of unpaused rendered monitors (a file the binary silently skipped is not a pass). New |
| binary `--check` | unchanged: exit 0 is no gate; success line + 0 ERROR decide |
| jq absence | message kept; non-working jq now caught |

### deploy/abi/derive.sh
`derived=$(jq ...)` CLOSED by `set -e`; `--check`'s `$(jq . "$OUT")` failure compared an empty string (CLOSED, wrong message). Added: jq-required check, explicit read failure, non-empty `derived`. jq-required RED.

### deploy/tests/abi-mutation-sweep.sh
`specced=$(jq ...)` unchecked then `[ "" -gt 0 ] \|\| continue` — **a monitor whose jq failed was skipped silently** (RED: one-monitor injection exit 0 on frozen); `n=$(jq ...)` unchecked, `[ "$a9" -ne "" ]` errors inside `if A \|\| B` read as false → the cell passed (RED); mutated-file jq status; `[ -f ] \|\| continue` on non-regular entries; `mktemp -d`; `cp`. All now counted failures.

### deploy/tests/selftest.sh (the harness)
`mutate` swallowed a failing jq (the "mutated" file stayed pristine and the case was judged on something else) → now a FAIL; `fresh` ignored `cp` failures → harness-failed flag; the D11 "removed names absent" grep read exit 2 (error) as "none" → only exit 1 passes.

### deploy/tests/m13-fork-regression.sh
`matches()` (empty count ≠ expected, CLOSED), receipts (no receipt = FAIL, CLOSED). `anvil_impersonateAccount` / `anvil_setBalance` statuses were ignored (a failed impersonation shows up later as "no receipt", CLOSED) → now abort with exit 2.

### deploy/config/triggers/scripts/inbox_append.sh (runtime)
CLOSED by `set -eu` for jq failure and `error("incomplete match")`; a jq that exits 0 without output appended an **empty line** (RED) → `[ -n "$line" ]` guard. Pinned: empty stdin, `{}`, garbage, stub jq, no jq → exit non-zero, 0 bytes.

### scripts/local-dry-run.sh
| Site | Frozen behaviour | Disposition |
|---|---|---|
| `jq ... > tmp && mv` (TEST_RPC_URL network rewrite) | **fail open**: `set -e` ignores a failing first command of `&&`; the production RPC reference stayed (RED) | `rewrite()` aborts |
| `jq ... > tmp && mv` (Discord / Telegram trigger rewrite to TEST_*) | **fail open**: a failed rewrite left the rendered trigger on the production webhook / token variable, so a configured `DISCORD_WEBHOOK_*` would receive real deliveries (RED) | `rewrite()` aborts |
| `to_entries[0].value.trigger_type` decides the whole file | **fail open**: a file whose first trigger was the inbox script kept every other trigger untouched, including a production Discord one (RED) | decided per trigger; empty files removed |
| monitors' `.triggers` rewrite `&& mv` | fail open (RED) | `rewrite()` |
| `available=$(jq -s ...)` | CLOSED (set -e, silent) | message |
| (new) post-condition | none | refuses to run if any rewritten trigger still references a non-`TEST_*` variable |
| jq absence | `command -v` only | + working check |

### Not changed (out of scope, no gate): scripts/docker_compose.sh, scripts/validate_network_config.sh, scripts/rust_docs.sh, scripts/linux/* — not part of the deploy gates. `docker-compose.yaml`, `.env.example`: no scripts.

## New regressions (selftest 119 → 289)

Section F10 of `deploy/tests/selftest.sh` (`SELFTEST_ONLY=f10` runs it alone): trigger config string / array / number / null / true; trigger entry and file wrong types; message/discord_url wrong types; empty plain value; 14 wrong-typed monitor fields through check-templates and check-abi; monitor files `[]`/`"x"`/5/null; M15 events-as-object / triggers-string / bad trigger file; 8 network shapes; render rejects config x5, `rpc_urls` object, trigger file array; empty / binary / symlink-loop / dangling / unreadable / stray-binary file in each of the three config dirs; check-abi empty, loop, dangling, unreadable; M15 empty and binary monitor; secret scan binary + symlink; jq absent and jq stub for six scripts plus render and local-dry-run; a jq failure injected at 41 sites (shim `jq` fails calls containing a marker; the case is vacuous-red if the marker never hit); sweep one-monitor injections and loop; M15 truncated spec, missing spec, duplicate trigger key; literal reference matching; local-dry-run (baseline, test channels, multi-trigger file, other script, five injections); inbox bridge x6.

RED/GREEN: `evidence/repair-4/red-frozen-72029c0.log` (the same section run against `git archive 72029c0`: 49 ok / 125 FAIL, `SELFTEST_ONLY=f10`); `evidence/repair-4/f10-section-green.log` (174 ok / 0 FAIL on the repaired tree); full selftest `evidence/repair-4/selftest-full.log`.

### Measurements on `f4eef81` (`evidence/repair-4/vrows.sh` → `vrows-run.log`, run on committed head `6f01879`, a docs-only commit over `f4eef81`; endpoints per live row in the log)

| Row | Result |
|---|---|
| V1A.1 | render exit 0 (`https://ethereum-rpc.publicnode.com`), abi-check OK (3356 entries), 32 active monitors (now cross-checked against the 32 unpaused templates), 0 ERROR; raw `--check` exit 0, success line, 0 ERROR |
| V1A.1f | brace removed: raw exit 0, no success line, 2 ERROR; render.sh exit 1 (ABI-1); dangling trigger: render exit 1, abi-check OK, 3 ERROR; restored 0 |
| F8 / F8b falsifiers | `Nonexistent(uint256)`: render 1, check-templates 1, restored 0; six F8b mutations red, restored 0; sweep **510 runs, 0 failures** |
| V1A.2 | 0 plain values; secret-shape `git grep` exit 1 (0 rows); checker OK (32 / 30 / 1) |
| F2 | absent/empty `--config-dir`, missing `--root`, no `networks`, no `match_conditions`: exit 1 each; restored 0 |
| V1A.3 (`eth.drpc.org`) | 26239237: 1 match, inbox +1, 11 keys; control 26239259: 0/+0; 26239255: 1/+1; 0 ERROR |
| V1A.4 / V1A.4f (`eth.drpc.org`) | 26177496: 1; own sender 26239694: 0; predicate removed 1; restored 0 |
| V1A.5 (`eth.drpc.org`) | 25991146 incoming: 1; 26169289 outgoing: 0; 26167892 old-key attempt: 1; 26239655 control: 0 |
| V1A.6 (`eth.drpc.org`) | 26240350: `== 0` 0 → `> 0` 1 → restored 0 |
| V1A.7 | 17 required events present (8 extras); no M15/M10 info routed to Telegram; 17 deletions each exit 1; old-gate baseline (204d528) exit 0; real OracleReplaced 26169066 (`eth.drpc.org`): Telegram 0 / Discord 1 |
| D11 | six on INFO, nine own variables, 0 duplicate names, 0 removed names outside plans/; falsifier exit 1; restored render 0 |
| V1A.8 | `docker compose config` exit 0, `/app/data` → worktree `data`; `max_past_blocks` 3600, recovery config, 1 RPC |
| **F10** | QA reproducer, config `"qa_invalid_config"` and `[]`: checker exit **1** (`S1b ... config must be an object, got string/array`), render.sh exit 1; restored 0. Same reproducer on `git archive 72029c0`: exit **0**, `checker: OK` (the defect, red baseline) |
| selftest (`evidence/repair-4/selftest-full.log`) | **289 passed, 0 failed, 0 skipped** (baseline 119; +170 in section F10) |
| F10 section RED/GREEN | frozen `72029c0` + new selftest, `SELFTEST_ONLY=f10`: 49 ok / **125 FAIL** (`red-frozen-72029c0.log`; the 49 ok are the sites that already failed closed and the baseline); repaired tree: 174 ok / 0 FAIL (`f10-section-green.log`) |
| fork suite | `m13-fork-regression.sh`: 15 cases, 0 failures (anvil fork of `https://eth.drpc.org` @ 26240350, local txs only) |

### Notes

1. `local-dry-run.sh --prepare` renders byte-identical `.local-dry/config` on the frozen and repaired trees with and without TEST_* channels, except the intended `inbox_append.sh` guard (diffed).
2. Behaviour changes a reviewer should know: S0 now rejects stray non-JSON files in `monitors/ triggers/ networks/` (the Monitor ignores them, a binary blob there is not configuration); the M15 gate exits 2 if the spec's M15 section names other than 16 events (revisit the required set; same spirit as the existing "module updates" guard); render.sh additionally requires the Monitor's active-monitor count to equal the unpaused templates.
3. Not touched: F5 (resolved per D11 in repair 3), F6, F7, `src/`, `qa/**`, `execution-log.md`. `scripts/docker_compose.sh`, `validate_network_config.sh`, `rust_docs.sh`, `scripts/linux/*` are not deploy gates and were not audited.
4. Process note: bash reads a running script incrementally; I edited `selftest.sh` during one run and it died with a syntax error. That run was discarded and re-run from a stable tree; no result in this record comes from it.
5. I clear no finding; fresh QA decides.

- Resulting head: see the status line in plan.md (docs-only commit after `f4eef81`).
- Next action: fresh Codex QA.
