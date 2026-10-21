PASS WITH NOTES

Independent CP1A QA, attempt 5 after repair 4, on frozen head `f5e1fc4f168031b21df257b5f7907c57bb2484da` (code head `f4eef81fb4b98260f908c6626e80d82280d6c087`), 2026-10-21. Claude Opus 5.5 high (D13), branch `mtabasco/ms-qa-cp1a-6`, task `task_719eaa1cfec4`, dispatch `ctx_0e9977842541`, terminal `term_e9306357-ef60-4dc5-b753-eef8b651cddd`. I repaired nothing.

I re-measured all ten V1A rows on blocks I chose myself, plus the CP0-QA follow-up and my own falsifiers. All of them are green. F10 is fixed: the attempt-4 reproducer and four more wrong-typed `config` values now exit 1 on HEAD, and the same probes exit 0 with `checker: OK` on `72029c0`. The attempt 1-4 reproducers that I re-ran (F2, F3, F4, F8, F8b, F9, F10) stay red. F1 is covered by the direct-transaction fork cases. F1b is the accepted gap D10, reproduced again. I fed every jq call of every gate a failure in turn (981 runs in total). None of these runs exited 0, apart from one site in the dry-run helper, and that site has no safety consequence. Under the round policy (four failed rounds) nothing I found is BLOCKING. Nine NOTEs are recorded below, each with an owner and the reason for its class. F6 and F7 are unchanged and stay non-gating deferrals.

## 1. Findings, ranked by severity

No BLOCKING finding. Every finding below is a **NOTE**. I give the reason for each class. Per the round policy the coordinator may raise a class to BLOCKING, but not lower one. The coordinator should copy N1–N9 into plan §8 "Recorded notes". My write scope is only the Status line.

Shared setup for the reproducers: `S=/tmp/ms-qa-cp1a-a5`, `B=/tmp/ms-qa-cp1a/target/release/openzeppelin-monitor` (an external build), `$S/pristine` = `git archive HEAD`. Each mutation runs in its own fresh copy of `$S/pristine`. The QA harnesses named here live in `$S` (scratch, outside the repo).

### N1 — NOTE: one unchecked jq left in `scripts/local-dry-run.sh` (the repair-4 audit missed it)

`scripts/local-dry-run.sh:90` contains `[ "$(jq 'length' "$f")" != 0 ] || rm -f "$f"`. If that jq fails, the substitution is empty, `"" != 0` is true, and the script keeps a trigger file that it would otherwise delete. The script still prints `Prepared …` and exits 0. The README's statement that every jq in `scripts/local-dry-run.sh` "has its status checked" is therefore not quite true.

- Reproducer: `$S/failsweep.sh dryprep 98 -- $S/dry.sh`. This uses a jq shim that fails exactly call n, for n = 1..98, and runs `local-dry-run.sh --prepare` once per n in a copy of HEAD. Expected: every run exits non-zero. Actual: 30 of 98 runs exit 0. All 30 are this `length` call, once per trigger file. With TEST_* channels set I get the same result: 30 of 99.
- Consequence, measured: with call 6 failed, `discord_cluster_failed.json` stays behind as `{}`. The post-condition audit still passes, because no non-TEST reference remains. `--check` on that dry config reports 32 monitors, completes successfully and logs 0 ERROR lines.
- Why NOTE: the only effect is an empty trigger map left in the dry-run directory. Nothing is delivered, no secret is referenced, and no gate's outcome changes. Owner: CP3 integrator (fix: `n=$(jq length "$f") || exit 1`).

### N2 — NOTE: no gate parses Monitor expressions, so a malformed one renders green and its rule silently never matches

- Reproducer: in a copy, set the seed-stake `FeesSynced` expression to `newFeesWei === 0` (`$S/render-garbage.sh` case `bad_expression`). Result: render.sh exits 0 and check-templates.sh exits 0.
- Runtime check: I replayed the real FeesSynced block 26234100 through `$S/direct.sh` with three broken expressions (`=== 0`, `> 0 )` and `&& …`). Each gave 0 matches, binary exit 0 and 3 ERROR lines (`Failed to parse expression`). The valid control `newFeesWei >= 1` gave 1 match.
- A7 rejects only `&&` and `||`.
- Why NOTE: no tracked template is affected. I proved every new monitor's expression matches real or fork transactions (§3, §4). `deploy/README.md` already says expression semantics are not covered. A future typo is a coverage gap of a regression guard, not a false claim. Owner: CP3 (add a per-monitor one-shot smoke replay or a grammar check).

### N3 — NOTE: the M15 gate checks that signatures are present, not that they can match (class 8)

`check-m15-coverage.sh` exits 0 when the Discord DAO monitor gets an expression on `OracleReplaced` (`oracleId == 99`). It also exits 0 when the monitor's transactions status is `Failure`, which can never match emitted events, and when the monitor is `paused: true` (`$S/m15-deletions.sh`). Each of these silently drops some or all M15/M10 info. check-templates.sh and render.sh do not catch the expression or status cases either. More generally, a paused page monitor (`paused_page_monitor`) passes render, check-templates and the count check, because the count compares only unpaused templates.

- Why NOTE: the shipped template is right, and I checked it directly:
  - all 17 independently derived events are present with `expression: null`;
  - status is `Success`, `paused: false`, and the trigger is `DISCORD_WEBHOOK_INFO`;
  - an M15 event emitted on a fork by the real contract code (`updateMinBlocksBetweenUpdates(0)` from the impersonated DAO Safe) matched Discord 1 / Telegram success 0 / Telegram failure 0;
  - deleting any one of the 17 events, or changing its types, is red.
- The plan's own V1A.7 contract is a signature diff, the severity is info, and `paused` is an explicit operator switch.
- The coordinator may raise this one, because it is the class-8 pattern. Owner: coordinator → CP3. Fix: assert that `expression == null`, status is in {Success, Any} and `paused == false` for the 17 events, and flag paused page monitors.

### N4 — NOTE: the secret-scan pattern misses legacy `discordapp.com` webhooks

- Reproducer (`$S/input-probes.sh` case `discordapp_webhook_in_body`): put `https://discordapp.com/api/webhooks/<18 digits>/<token>` into a Telegram trigger's message body. check-templates exits **0**. A `discord.com` URL in the README and a Telegram-shaped token in a filter are both caught (exit 1).
- Why NOTE: the regex is the plan's frozen V0.3 pattern. I ran a wider scan of my own over every tracked file (`discord(app)?\.com/api/webhooks/[0-9]`, `[0-9]{6,12}:A…{30,}`, `api.telegram.org/bot[0-9]`). It found only the upstream dummy fixtures (`123-456-789`, `ABCdef…`), so no secret is in the tree. Owner: coordinator (widen V0.3/S3 to `discord(app)?`).

### N5 — NOTE: `render.sh` accepts a network with `rpc_urls: []`

- Reproducer: `render-garbage.sh` case `network_no_rpc` gives render exit 0 and `--check passed`. check-templates.sh rejects the same input with exit 1 (`S5 … no rpc_urls`, `primary RPC … must be MAINNET_RPC_URL`).
- Why NOTE: render.sh is documented as ABI + `--check` only. The structural checker catches this case, and a Monitor with no RPC fails loudly at runtime. Owner: coordinator. The deploy step should run check-templates.sh before render.sh, or render.sh should call it.

### N6 — NOTE: `check-abi.sh` run alone prints OK with an empty count when jq passes the health probe but is otherwise broken

- Reproducer: a stub jq that prints `2` for `jq -n '1 + 1'` and exits 0 with no output for everything else.
- Result: check-abi.sh exits 0 and prints `abi-check: OK (32 monitors … ;  contract_spec …)`, with the count missing.
- The composed gates under the same stub stay red: check-templates exits 1 (130 failures), render exits 1, check-m15 exits 2, and inbox_append exits 1 with 0 bytes written. dry-run `--prepare` exits 0 but leaves 0-byte trigger files, which the Monitor cannot load.
- Why NOTE: this needs a jq that answers the probe correctly but lies for real programs. That is outside the gates' threat model (toolchain integrity), and no deploy-gate outcome flips. Owner: CP3 (require `compared` to be a positive integer).

### N7 — NOTE: the repair-4 red-baseline count does not reproduce exactly (evidence accuracy)

The record says the F10 section has 125 reds on `72029c0` (49 ok / 125 FAIL). I ran the committed `deploy/tests/selftest.sh` (`SELFTEST_ONLY=f10`) against `git archive 72029c0` and got **51 ok / 123 FAIL / 0 skipped**. The two differences are `no jq on PATH: deploy/check-abi.sh` and `…/abi-mutation-sweep.sh`. Both already failed closed at 72029c0: they have `command -v jq … exit 2` guards at `check-abi.sh:40` and `abi-mutation-sweep.sh:22`. The committed red log shows those two cases failing on a malformed command string from an earlier harness revision. Why NOTE: the substance holds — 123 genuine reds on the frozen head, all green now. Owner: CP1A record (coordinator).

### N8 — NOTE (out of scope, Rust core): an empty RPC result is read as "no logs"

- Reproducer: `$S/rpcproxy.py`, a local proxy in front of `https://eth.drpc.org`, replaying the real FeesSynced block 26234100 with the expression mutated to `> 0`.
- Results:
  - pass-through: 1 match, exit 0;
  - `eth_getLogs` → `[]`: 0 matches, exit 0, 0 ERROR (indistinguishable from a quiet block);
  - `eth_getLogs` → `null`: exit 1, 2 ERROR;
  - receipt → `null` on the inbox block 26239099: exit 1, 2 ERROR (no line).
- Why NOTE: this is the Monitor core's trust in its RPC. Rust is out of scope (§1), it matches attempt 4, and no CP1A script can see it. Owner: CP3 (RPC cross-check or health finding in the worker).

### N9 — NOTE (spec interpretation): retired-key → attacker transfers do not page

The real drain block **26130420** moved value from `0x4BC6…` and `0x3A98…` to attacker `0xaeE0…` in direct transactions (tx `0x41811ef6…`, `0x18f62b61…`). Replaying it gives retired monitor 0 and attacker monitor 0, both exit 0 and 0 ERROR. The attacker rule reads "our wallets" as the current ones (`0x16A5…`, `0x0431…`, DAO Safe), and F4 removed outgoing retired-key pages. Why NOTE: any value that reaches a retired key already pages on arrival, so its later sweep to an attacker adds no new signal. This matches the decisions made in attempts 1–4 and the D10 README table. Owner: Marco (confirm the reading).

### Carried items (re-confirmed, unchanged)

- **F6** (non-gating, inherited Rust): the live 0x28bE `updateClusterBalance` at **26177500** (receipt `0x0`) still matches the `Success` monitor once and is labelled `Transaction Status: Success`, exit 0, 0 ERROR, on drpc. New observation: on a fork, an *early-reverting* 0x28bE `updateClusterBalance` (block 26241514) is **not** matched (0), so the mislabel depends on the input. The M11 info rule does not exclude reverts either way.
- **F7** (non-gating): `git check-ignore -v data/inbox/failed-tx.jsonl` exits 1 (no rule). `git diff 72029c0 HEAD -- src tests .gitignore` is empty.
- **F1b / D10**: one more boundary case on a fork at 26241600. A contract-creation transaction whose constructor CALLs 1 wei into retired slot 4 (block 26241601, tx `0x566509bd…`) adds +1 wei to its balance, but the retired monitor shows 0 matches and 0 ERROR. This is the **accepted gap D10** (CP2c balance watch), not a CP1A failure.

## 2. Claims I could NOT confirm

- **"125 red on 72029c0"**: only 123 reproduce with the committed selftest; see N7. Everything else in the repair-4 numbers was reproduced: selftest **289 passed / 0 failed / 0 skipped**; ABI sweep standalone **510 checker runs, 0 failure(s)**; fork suite **15 cases, 0 failure(s)**; F10 reproducer red→green.
- **"every jq in local-dry-run.sh has its status checked"**: disproved at one site; see N1.
- Not exercised and not credited: production delivery, container start-up, restart and catch-up behaviour of `max_past_blocks` / `recovery_config` (V1A.8 is a config check only), the CP2c balance watch, and `deploy/abi/derive.sh --check`. The last one needs the forge build output and the Safe ABI sources. I did not rebuild them, and `git diff 72029c0 HEAD -- deploy/abi/reference.json` is empty.
- For V1A.7, only one real M15 event exists on mainnet in roughly 455k recent blocks: OracleReplaced at 26169066, which the implementer used. I searched 26.0M–26.24M and an earlier 250k-block window with eth_getLogs on rpc.mevblocker.io and found nothing else. So my own M15 event comes from a fork (§4), not from mainnet.

## 3. Verification matrix

Preflight: `git rev-parse HEAD` = `f5e1fc4f…` (equal to the dispatch). Initial `git status --porcelain` was empty. 349 GiB free. Tools: jq 1.7.1, anvil and cast 1.8.5, Docker Compose v5.6.0, `/bin/bash` 3.2.57. No venv was needed. `CARGO_TARGET_DIR=/tmp/ms-qa-cp1a/target cargo build --release --locked` exited 0. Network was allowed in this session: all five endpoints answered `eth_blockNumber` = `0x1906b14`, so no sandbox rerun was needed.

Endpoints: every credited mainnet replay was served by **https://eth.drpc.org**. Range `eth_getLogs` discovery ran on **https://rpc.mevblocker.io**: drpc refused the 5,000-block range and nodies capped it at 50. Address histories came from Blockscout v2.

Path adaptations: binary `B` as above. Live rows use `$S/local-dry-run.sh`, which differs from the tracked script only in `ROOT` (this worktree) and `BIN` (= `B`); `diff` shows exactly those 2 lines. Rows are driven through `$S/oneshot.sh <rpc> <monitor> <block>`. Mutation rows run `B` directly on the prepared `.local-dry` (`$S/direct.sh`), so a re-prepare cannot overwrite the mutation. No TEST_* or production delivery variable was set (`env -i`), and the prepared dry config held only 2 script triggers (inbox, local log) and 0 environment references, so nothing could be delivered.

| V-ID | Command (block = my own choice unless noted) | Exit | Result |
|---|---|---:|---|
| V1A.1 | `MONITOR_BIN=$B MAINNET_RPC_URL=https://eth.drpc.org deploy/render.sh --out .local-dry/config`; then `(cd .local-dry && env -i … <child-only placeholders> $B --check)` | render 0 / raw 0 | **PASS under the accepted deviation.** ABI check OK (32 monitors, 3,356 entries); `Found 32 active monitor(s)`, 1 network, success line, 0 ERROR. A raw run with a missing variable gives exit **0** with 3 ERROR lines. |
| V1A.1f | Remove the last `}` from the rendered `ssv_seed_stake_monitor.json`, run raw `--check`, restore (sha1 identical) | raw 0 / restored raw 0 | **PASS under the deviation**: no success line and 2 ERROR (`Failed to load monitors`); after restore, success line and 0 ERROR. The gate side is in §4 "deviation". |
| V1A.2 | V0.6 jq over `deploy/config/triggers/*.json deploy/config/networks/*.json` (and over every deploy JSON); V0.3 `git grep -nE 'discord\.com/api/webhooks/[0-9]+/\|[0-9]{8,10}:AA[A-Za-z0-9_-]{30,}' -- . ':!tests/integration/fixtures'`; `deploy/check-templates.sh` | jq 0 / grep 1 / checker 0 | **PASS**: 0 plain values; 0 secret rows; `checker: OK (32 monitors, 30 triggers, 1 network)`. Wider scan: see N4. |
| V1A.3 | `oneshot.sh drpc ssv_failed_tx_inbox.json 26239099` | 0 | **PASS**: 1 match and inbox +1, 0 ERROR. Line: `schema 1, tx_hash 0x08da03c1…, block 26239099, block_hash 0x2953a376…, from 0x0431…, to proxy, selector 0xbf0f2fb2, function liquidate(…), status failure, monitor "SSV Failed Tx Inbox", observed_at`. That is 11 keys, the §6 set. The block hash and selector match my own `eth_getBlockByNumber` read. The same block's *successful* liquidate by `0x4d59…` (idx 0) was not matched. |
| V1A.3 (own extra) | same monitor, **26239017** (4 reverted liquidates, 3 senders, plus 1 success); control **26232619** (only a successful liquidate) | 0 / 0 | 4 matches / inbox +4 in block order (`0xf9ae…`, `0xcb86…`, `0xc555…`, `0x3b90…`); control 0 / +0. |
| V1A.4 | `oneshot.sh drpc ssv_updater_other_sender_monitor.json 26153569` (0x28bE `updateClusterBalance`, 2026-10-09 08:10Z; the only proxy update in the block, receipt `0x1`) | 0 | **PASS**: 1 match (`0x68cf730c…`), 0 ERROR. |
| V1A.4f | same monitor at **26230095** (the only update is from `0x16A5…`, receipt `0x1`); then the sender predicate removed in the rendered JSON; then restored (sha1 identical) | 0 / 0 / 0 | **PASS**: 0 → 1 → 0, all 0 ERROR. |
| V1A.5 | `oneshot.sh drpc ssv_retired_addresses_monitor.json 26022191` (1,933,324,399,558 wei from `0x2650…` → `0x3A98…`); second positive **25925539** (2.5 ETH → `0x3A98…`) | 0 / 0 | **PASS**: 1 / 1, 0 ERROR. In 26022191 the same block's outgoing `updateClusterBalance` *from* `0x3A98…` is not matched by this monitor; the retired-updater monitor matches it once (`0x585e20f8…`). |
| V1A.6 | seed-stake monitor at **26234100** (real FeesSynced, newFeesWei `0x11988a6de03480` > 0): `== 0` → `> 0` → restored | 0 / 0 / 0 | **PASS**: 0 → 1 → 0, 0 ERROR; restored sha1 identical. |
| V1A.7 | `diff <(jq -r '.match_conditions.events[].signature' deploy/config/monitors/ssv_dao_discord_success_monitor.json \| sort) $S/m15-own.txt`; `deploy/check-m15-coverage.sh` | diff 1 (extras only) / 0 | **PASS**: no required event missing; 8 extras (AdminChanged, BeaconUpgraded, ERC20Rescued, Initialized, NetworkEarningsWithdrawn, OwnershipTransferred, OwnershipTransferStarted, SSVNetworkUpgradeBlock). The 17-signature list comes from the spec M15 text plus event declarations read from `ssvlabs/ssv-network` at `f7ac487` (`git grep 'event '`); `SSVModules` → `uint8`, OZ `Upgraded(address)`. It equals the checker's list. |
| V1A.8 | `docker compose -f docker-compose.yaml config` (and `--format json`); rendered network | 0 | **PASS**: `/app/data` binds `<worktree>/data` (not `/dev/null`); config read-only; `max_past_blocks` 3600; `recovery_config` enabled, `0 */5 * * * *`, 100/7200/3/1000; only `MAINNET_RPC_URL` when `_2` is unset. Every referenced variable is in compose env and `.env.example` (`comm` empty). |
| CP0-QA follow-up | check-templates on the tracked tree; F2/F10 reproducers; 33 own input probes (`$S/input-probes.sh`); my fail-at-every-jq-call sweep | 0 / see §4 | **PASS with N1/N4/N6**: every malformed, missing, wrong-typed or partial input is red except the `discordapp.com` pattern (N4). |

Routing (D2/D11), derived from the plan text, not from the scripts:

- **page → Telegram (`TELEGRAM_TOKEN`)**: retired addresses, retired-key attempt, attacker interaction, seed stake.
- **info → `DISCORD_WEBHOOK_INFO`**: other-sender, DAO Discord success (M15/M10), and the six `*_success` triggers.
- **Nine dedicated Discord variables**: six `*_FAILED`, `ORACLE_DISSENT`, `DAO_SAFE_FAILED`, `LARGE_ETH_TRANSFER`.
- **Script trigger**: the inbox.

Checks: 0 duplicate trigger names; 0 occurrences of the removed `*_SUCCESS` names outside `plans/`.

## 4. Falsifiers, red/green and restoration

**Accepted deviation (V1A.1/1f), judged sound.**

- `src/main.rs:205-209` returns `Ok(())` after `validate_configuration()`, and `validate_configuration` (`src/main.rs:757-797`) only logs `error!`. I measured raw exit 0 three times: missing variable (3 ERROR), brace removed (2 ERROR), restored (0 ERROR).
- render.sh gates on four conditions:
  - binary exit 0;
  - the success line;
  - zero ` ERROR ` lines;
  - `Found N active monitor(s)` equal to the number of unpaused templates.
- `render-garbage.sh` ran 28 cases in fresh copies. Every malformed input is red, and each case that reached the binary was decided by the ERROR/success gate with the binary still exiting 0. Red cases:
  - binary garbage, empty file, `[]`, trailing garbage, two JSON values, a `.json` directory, a symlink loop, a stray `.txt`, bad status, unknown parameter, `&&`, `Nonexistent(uint256)` (F8), an `indexed` swap (F8b), and `rpc_urls` as an object: render 1 via check-abi or a jq rewrite;
  - dangling trigger, unknown network, unknown trigger type, missing token, `config` as a string, missing filter, missing script, duplicate JSON key, unknown field, and every monitor paused: render 1, binary 0, ERROR ≥ 2.
- Green on render: `network_no_rpc` (N5), `bad_expression` (N2) and `paused_page_monitor` (N3).
- Raising LOG_LEVEL or using LOG_MODE=file removes the success line, so the gate fails closed.
- Judgment: sound for V1A.1/1f. The binary's exit code is no signal, and render.sh's own gate carries the "must fail on malformed JSON or ABI mismatch" requirement.

**Matrix falsifiers.** V1A.1f, V1A.4f and V1A.6 are in §3: each was red, then restored byte-identical and green.

**Prior reproducers.**

- F10 (`$S/f10-repro.log`): `discord_dao_success.config` ∈ {`"qa_invalid_config"`, `[]`, `5`, `null`, `true`} → HEAD exit 1 (`S1b … config must be an object`); `{}` → exit 1 (3 failures). On `72029c0`, `string`, `[]`, `5` and `true` give **exit 0, `checker: OK`**, confirming the red baseline.
- F2: an absent dir, an empty dir, a missing `--root`, and a dir with no triggers each exit 1 with S0 lines.
- F3: an independent scan of Telegram-routed monitors found M15 setters only on `SSV DAO Monitor (Failure)`, which is `status: Failure` (a failed-governance page, by design). `SSV DAO Monitor (Success)` (Telegram) has no M15 event.
- F4: own outgoing block 26130420 gives retired monitor 0; the fork suite's two outgoing cases give 0.
- F8 / F8b: render 1 (cases above); ABI sweep **510/0**.
- F9: deleting each of my 17 signatures exits 1 with `missing: <sig>`, 17 of 17; `QuorumUpdated(uint16)` → `(uint8)` exits 1.
- F1: fork suite 15/0 plus my fork cases below.

**My fail-at-every-jq-call sweep** (`$S/failsweep.sh`; shim `$S/shim/jq` claims a unique index with `mkdir` and fails exactly call n with exit 5). The number of calls comes from a baseline run of each script on a copy of HEAD.

| Script (copy of HEAD) | jq calls | runs exit 0 | indices not reached |
|---|---:|---:|---:|
| `check-templates.sh` | 484 | **0** | 0 |
| `check-abi.sh --config-dir deploy/config` | 67 | **0** | 0 |
| `check-m15-coverage.sh` | 132 | **0** | 0 |
| `render.sh --out … ` (real `--check`, child-only placeholders) | 101 | **0** | 0 |
| `local-dry-run.sh --prepare` | 98 | **30** (N1, all at `:90`) | 0 |
| same, with TEST_* channels and TEST_RPC_URL | 99 | **30** (N1) | 0 |

**My input-class probes** (`$S/input-probes.sh`). All exit non-zero except N4:

- FIFO `.json` in monitors/: check-templates and check-abi both red, no hang;
- a whitespace-only file, a UTF-8 BOM, a NUL inside JSON;
- `paused: "false"`, `chat_id` as a number, a lower-case env name, the token as a plain string, `weight: "100"`, `url` as a plain string, `contract_spec: "x"`;
- a symlink to a valid file (S3), `triggers/scripts` missing, `filters/` missing, `monitors` as a file;
- `trigger_type: "Telegram"`, `status: "any"`, a trigger reference with a leading space, a literal `chat_id`;
- a Telegram token in a filter file and a `discord.com` webhook in the README (S3 red);
- an unreadable spec (m15 exit 2), `events: null`, a numeric signature, missing `reference.json`;
- a truncated reference, an empty `contracts`, the proxy missing from the reference (A8), a missing `normalize.jq` or `shape.jq`.

The missing-jq and stub-jq cases are in the selftest and are confirmed green there; the "smart stub" case is N6. `inbox_append.sh` with the smart stub exits 1 and writes 0 bytes.

**Own live falsifiers** (drpc; each block independently read with `eth_getBlockByNumber` + receipts):

- attacker `0x0144…` → SSV token `approve`, real tx `0xb0643397…` at **26130629**: attacker monitor **1**. The same attacker's USDC `approve` + router call at **26130635**: **0**.
- retired-key attempt monitor at 26230095 (own sender only): 0.
- other user's real UnstakeRequested at **26240359** (`0xbfce…`): seed-stake monitor 0; with the expression retargeted to that user: 1; restored: 0.
- inbox success-only control 26232619: 0.

**Own fork falsifiers** (`$S/fork-own.sh`; anvil fork of drpc at **26241500**, a different block from the implementer's 26240350; local impersonated transactions only). 21 of 22 matched my expectation. The one mismatch was my F6 guess, not a defect (§1, F6).

- attacker → SSV token `approve` 1; attacker → cSSV 1; attacker → DAO Safe 1; our oracle wallet → attacker 1 wei 1.
- **Same block** (automine off): 2 attacker transactions plus 1 unrelated → exactly **2**.
- Old slot-4 key: garbage `updateClusterBalance` (reverts) 1; `commitRoot` with 1 wei (reverts) 1, with retired-incoming 0; `deposit` attempt 0.
- Inbox from an unrelated sender:
  - failed `liquidate`, `liquidateSSV`, `reactivate`, `migrateClusterToETH` and `commitRoot`: each 1 match / +1 line, 11 keys, selector equal to `cast sig`;
  - failed `deposit` (not listed): 0.
- DAO Safe `updateMinBlocksBetweenUpdates(0)` (success): Discord DAO success **1**, Telegram DAO success 0, Telegram DAO failure 0. The same call from a non-owner (revert): Discord 0, Telegram failure 1.
- DAO Safe `requestUnstake(1)`: seed stake **1**. Unrelated `requestUnstake` (reverts): 0. `syncFees()`: 0.

Every run had 0 ERROR lines. The implementer's fork suite on my copy (port 39611): **15 cases, 0 failures**. The D10 case is in §1.

**Selftests.**

- `MONITOR_BIN=$B deploy/tests/selftest.sh` on HEAD: **289 passed, 0 failed, 0 skipped**, exit 0, including "tracked deploy/ tree unchanged".
- The committed selftest with `SELFTEST_ONLY=f10` on `git archive 72029c0`: **51 ok / 123 FAIL**, exit 1 (N7).
- `deploy/tests/abi-mutation-sweep.sh`: `510 checker runs, 0 failure(s)`.

**Restoration.** Every mutation was in `$S/…` scratch copies, except the rendered `.local-dry/config` mutations allowed for V1A.1f, V1A.4f, V1A.6 and the M8 rows. Those were restored from a backup, checked with sha1/`cmp` as identical, and re-run green. A harness bug of mine (`cat f >> f` in a scratch copy) grew a sparse scratch file; I killed it and deleted the file. It touched nothing in the repo. Final porcelain is in §6.

## 5. Evidence-plane writes

- CP1A transitions from `IMPLEMENTED — AWAITING QA` to **`VERIFIED — PASS WITH NOTES`** on frozen head `f5e1fc4f`, linking this report. There is no BLOCKING finding. N1–N9 are NOTEs with owners, and F6/F7 stay owned non-gating deferrals.
- Paths written and committed on `mtabasco/ms-qa-cp1a-6`:
  - `plans/monitoring-service/qa/cp1a-attempt-5.md` (this report);
  - the CP1A `**Status:**` line in `plans/monitoring-service/plan.md`;
  - one appended event in `plans/monitoring-service/execution-log.md`.
- Ignored, allowed runtime output: `.local-dry/` (rendered config, dry data and inbox lines). Scratch: `/tmp/ms-qa-cp1a-a5`, `/tmp/ms-qa-cp1a/target`.
- Not done: no push, no install, no notification delivery, no mainnet transaction (anvil only), no `config/`, `config.zip` or `.env*` edit in any checkout.

## 6. Repo proof

Before the evidence writes (after all experiments):

```text
$ git status --porcelain
<empty>
$ git diff --name-only f5e1fc4f168031b21df257b5f7907c57bb2484da
<empty>
$ git status --porcelain --ignored
!! .local-dry/
```

After the evidence writes, before the commit:

```text
$ git status --porcelain
 M plans/monitoring-service/execution-log.md
 M plans/monitoring-service/plan.md
?? plans/monitoring-service/qa/cp1a-attempt-5.md
$ git diff --name-only f5e1fc4f168031b21df257b5f7907c57bb2484da
plans/monitoring-service/execution-log.md
plans/monitoring-service/plan.md
$ git diff --exit-code f5e1fc4f168031b21df257b5f7907c57bb2484da -- deploy scripts docker-compose.yaml .env.example src tests config
<empty>, exit 0
```

No implementation file appears in either list. The report is untracked until it is staged.
