FAIL

Independent CP1A QA, attempt 4 after repair 3, on frozen head `45fe9403b1d6b76aeeded3d0d24be4a0d3f40e6b` (code head `e981687df50b5e0efc72bcf76372636a155c78d7`), 2026-10-21. Codex gpt-6.1-sol high; branch `mtabasco/ms-qa-cp1a-5`; task `task_6af97fb3eacc`, dispatch `ctx_b6cfc136a033`, terminal `term_284b8429-75c8-4666-b23b-68758d7178e1`. No implementation was repaired.

All ten assigned V rows were re-measured successfully. F9 is fixed, D11 is correctly applied, and F1b is closed for CP1A by the explicit D10 scope decision read from integration. **A new falsifier, F10, prevents VERIFIED:** the required structural checker prints OK and exits 0 after a jq schema error skips validation of a collapsed Discord trigger's configuration. The render gate rejects the same malformed configuration; this limits the defect's impact but does not make the checker result true.

## 1. Findings, ranked by severity

### F10 — MEDIUM: the required-field/schema checker fails open on a malformed trigger config

Owner: CP1A/coordinator for a fresh repair assignment. A valid JSON trigger whose `config` is a string or array has no usable `discord_url` or message. S4's jq operation errors, but its exit status is not checked; the empty captured output is treated as no violations. The checker then prints `checker: OK (32 monitors, 30 triggers, 1 network)` and exits **0**. This falsifies the CP0-QA required-field/schema follow-up and the repaired D11 checker's claim that a missing credential reference is always red.

Minimal reproducer, run in an external copy, with no tracked implementation changes:

```sh
qa_case=$(mktemp -d /tmp/ms-cp1a-f10.XXXXXX)
mkdir -p "$qa_case/docs/ssv-specs"
cp -R deploy "$qa_case/deploy"
cp docs/ssv-specs/monitoring-service-spec.md "$qa_case/docs/ssv-specs/"
qa_trigger="$qa_case/deploy/config/triggers/discord_dao_success.json"
jq '.discord_dao_success.config = "qa_invalid_config"' "$qa_trigger" > "$qa_trigger.tmp"
mv "$qa_trigger.tmp" "$qa_trigger"
bash "$qa_case/deploy/check-templates.sh"
echo "checker exit: $?"
```

Expected: nonzero; malformed config / absent required fields. Actual:

```text
jq: error (at .../discord_dao_success.json:0): Cannot index string with string "discord_url"
checker: OK (32 monitors, 30 triggers, 1 network)
checker exit: 0
```

Replacing the value with `[]` independently gives the same exit 0 / OK, with `Cannot index array with string "discord_url"`. `python3 /tmp/ms-qa-cp1a-a4/fresh-schema.py` measured both cases and restored the original bytes in `finally`. `python3 /tmp/ms-qa-cp1a-a4/schema-render.py` confirms **render exit 1**, ABI check OK, raw binary exit 0, **2 ERROR lines**, no success marker: `TriggerTypeConfig` deserialization rejects this configuration. Restoring the trigger gives checker exit 0 with no jq error. Thus this is a false-green structural checker, rather than a successful malformed deployment.

An ordinary deleted `discord_url` is correctly rejected on each of the six collapsed success triggers. That test alone does not expose the schema-error path above. No repair was made.

### Prior findings and accepted boundaries

- **F9 fixed:** the independently derived 17-event set is present, and deleting each individual event now exits **1** with its exact missing signature. Wrong-type mutations of `ModuleUpgraded` and `QuorumUpdated` also exit 1. Original bytes restored, baseline exit 0.
- **F1b closed for this checkpoint by D10:** integration's decision explicitly limits CP1A to direct transactions. The README accurately separates direct coverage, retired-key balance watching assigned to CP2c, and accepted attacker/internal/constructor/net-zero gaps. This QA does not credit a CP2c implementation or amend the spec. Its promised closure amendment is still a coordinator responsibility.
- **F5 resolved per D11:** six success webhooks collapse to INFO; the nine retained dedicated variables are intentional. Full audit below.
- **F6 remains owned, non-gating, unchanged:** at mainnet block **26177500**, updater-other-sender replay gives 1 match / exit 0 / 0 ERROR and labels the condition Success; independent receipt `0x8563fbf3473c699aaddad6acde375b649e75309ac29474072ec829171c396f99` is `0x0`. Endpoint: `https://eth.drpc.org`. This is the inherited Rust classification issue; M11 other-sender info itself does not exclude reverts.
- **F7 remains owned, non-gating, unchanged:** `git check-ignore data/inbox/failed-tx.jsonl` exits **1**, no rule. No inbox was created at the repository's production `data/` path. `git diff e981687 HEAD -- src .gitignore` is empty / exit 0.

## 2. Claims I could NOT confirm

- The structural checker's complete required-field/schema guarantee is **disproved by F10**, although its existing 119-case selftest passes and the deployment render gate contains this particular malformed-config failure.
- The standalone **510/0 numeric summary** was not separately captured: the full ABI sweep ran once inside `selftest.sh`, whose wrapper reports its passing assertion and suppresses the child summary. I observed **119 passed, 0 failed, 0 skipped**, including that sweep. I did not spend another sweep to repeat unchanged expensive coverage.
- The separate committed **15-case fork script** was not redundantly rerun. Instead, I reran the earlier independent **70-case** reproducer and its explicit assertion audit, plus ten event/calldata/layout controls and six additional boundary transfers. This covers the 15-case script's direct positive/negative behaviors with broader target/value/revert coverage. The original script also hardcodes the repository target path; this run uses the required external build.
- No fresh Solidity compilation, source download, or historical old-gate comparison was needed: `git diff 2e0447b 45fe9403 -- deploy/abi deploy/config/monitors` is empty. The unchanged source-derived ABI was checked again with `deploy/abi/derive.sh --ssv-out /tmp/ms-qa-cp1a/attempt3/source/out --safe-abi /tmp/ms-qa-cp1a/attempt3/gnosis_safe.json --check` (**exit 0**), using the previous independent QA's source artifacts. This is a current equality check against cached sources, not a claim to have rebuilt or downloaded them again.
- Production delivery, container startup, durable restart/catch-up, worker balance watching and trace coverage were not exercised or credited. Notifications were local records only. D10 does not make CP1A provide internal-value coverage.
- An RPC returning a successful empty log array remains a trust limit: a known-positive FeesSynced block gives **1 → 0 → 1** when a local read-only RPC proxy answers normally, substitutes `[]`, then restores truth. All three binary runs exit 0 with 0 ERROR. Substituting `null` instead gives exit **1**, **2 ERROR**. This is not evidence that the real block was empty; positive rows require independently inspected real block/log/receipt data.
- No required V row remains NOT RUN. Sandbox DNS failure and failed discovery requests were never read as clean negative results. The independent collection harness initially stopped at a missing Python import after 37 successful static measurements; I corrected that scratch-only harness and resumed the remaining 27 measurements. The interrupted harness exit is not counted as a code failure or an unrun row.

## 3. Verification matrix

Preflight: HEAD exactly the frozen hash; initial porcelain empty; approximately **352 GiB free**; cargo, jq, cast, anvil, Python and Docker Compose available. No venv was needed, as explicitly dispatched. `CARGO_TARGET_DIR=/tmp/ms-qa-cp1a/target cargo build --release --locked` exited **0**, finished in 21.68 s. No installation. `export PATH=$HOME/.foundry/bin:$PATH` was used for Foundry.

Initial sandbox RPC probe: **exit 6**, `curl: (6) Could not resolve host: eth.drpc.org`. All live rows were rerun with network allowed. Some speculative FeesSynced discovery reads exited **56** (`CalledProcessError`, exact request and status retained in `choose.log` / `select-more.log`); no result from them was credited. Single-block archive probes on all four dispatched endpoints succeeded for known positive blocks. **Every credited mainnet row below was served by `https://eth.drpc.org`.** Orca initially returned `runtime_access_denied`; its prescribed escalated retry succeeded.

Path adaptations: `B=/tmp/ms-qa-cp1a/target/release/openzeppelin-monitor`, `S=/tmp/ms-qa-cp1a-a4`, `R=this worktree`, `D=MAINNET_RPC_URL=https://eth.drpc.org bash "$S/local-dry-run.sh"`. This external dry-run script differs from the tracked script only in ROOT pointing to R and BIN pointing to B. Replays append `--network evm_ethereum_mainnet --block N`. Render/selftest use `MONITOR_BIN="$B"`. Rendered mutations run B directly from `.local-dry` so preparation cannot overwrite the mutation. `common.py` removes all production and TEST delivery variables; preparation explicitly asserts that every retained trigger is a script. No secret values were used or printed.

| V-ID | Command / independent measurement | Exit | Result |
|---|---|---:|---|
| V1A.1 | `MONITOR_BIN="$B" MAINNET_RPC_URL=https://eth.drpc.org deploy/render.sh --out .local-dry/config`; `(cd .local-dry && B --check)` with child-only shape placeholders | 0 / 0 | **PASS under accepted deviation**: 32 active monitors, 1 network, success marker, 0 ERROR; 3356 ABI entries agree. Root render/raw commands measured in `row1.py`. |
| V1A.1f | Remove final rendered closing brace; run raw B; restore. Separately mutate the external template and run its render gate; also use a valid-JSON dangling trigger. | raw 0; render 1 / 1; restored 0 | **PASS under accepted deviation**: raw malformed check has no success marker / 2 ERROR; dangling-trigger render fails with 3 ERROR, raw exit 0. |
| V1A.2 | V0.6 jq scan on trigger/network templates; V0.3 `git grep -nE 'discord\.com/api/webhooks/[0-9]+/\|[0-9]{8,10}:AA[A-Za-z0-9_-]{30,}' -- . ':!tests/integration/fixtures'` | jq 0, grep 1 | **PASS**: 0 plain values / 0 secret rows. Structural checker's distinct follow-up fails below. |
| V1A.3 | `D --monitor-path config/monitors/ssv_failed_tx_inbox.json --network evm_ethereum_mainnet --block 26239190` | 0 | **PASS**: 1 match, inbox +1, 0 ERROR; 11 exact fields, schema 1, receipt status 0x0, selector equals native input prefix, addresses/hash/block and timestamp checked. Success control 26239259: 0 matches / inbox +0 / exit 0. |
| V1A.4 | `D --monitor-path config/monitors/ssv_updater_other_sender_monitor.json --network evm_ethereum_mainnet --block 26177495` | 0 | **PASS**: 1 match / 0 ERROR. Full block independently contains both the 0x28bE sender and our updater; only the other sender matches. |
| V1A.4f | Same monitor, **26239693**; remove sender predicate in rendered JSON, replay directly, restore and replay | 0 each | **PASS**: baseline 0 → predicate disabled 1 → restored 0; no ERROR. Independent full block confirms only our updater's relevant call. |
| V1A.5 | `D --monitor-path config/monitors/ssv_retired_addresses_monitor.json --network evm_ethereum_mainnet --block 17661857` | 0 | **PASS**: 1 direct incoming match (1 ETH to retired liquidator), 0 ERROR. Discovered via its Blockscout inbound history. Outgoing block 26169289: 0 / exit 0. Local fork also checks both retired keys. |
| V1A.6 | Seed-stake monitor at **26240350**, real nonzero FeesSynced; expression `== 0` → `> 0` → restored `== 0` | 0 each | **PASS**: 0 → 1 → 0 matches, 0 ERROR. Known anchor reused after bounded fresh discovery failed; real logs were independently read. |
| V1A.7 | Independent spec/source signature diff; `deploy/check-m15-coverage.sh`; 17 individual deletion mutations in external copies | baseline 0; each deletion 1; restored 0 | **PASS**: missing diff empty; 17 required / 25 actual / 8 extras. Wrong-type cases red. Real OracleReplaced 26169066: Telegram 0 / Discord 1, both exit 0 / no ERROR. |
| V1A.8 | `docker compose -f docker-compose.yaml config --format json`; inspect rendered network | 0 | **PASS**: `/app/data` bind source is this worktree's `data`, not `/dev/null`; read-only config bind; max_past_blocks 3600; recovery enabled, cron every 5 min; primary RPC only when secondary unset. |
| CP0-QA follow-up | `deploy/check-templates.sh`, missing-input/required-field/schema mutations and F10 | baseline 0; ordinary missing fields 1; **F10 0** | **FAIL**: all normal required-field deletions are red, but malformed trigger config produces a jq error then false OK. |

Independent positive blocks were chosen from read-only Blockscout history and actual native block data: reverted liquidate **26239190** (`0x56397a8ae970b135de09a26f3083bd468a23aad397de877de39712eb267a4695`); other updater **26177495** (`0xf10f6b389bcf88d0b611e078c97c566a62bfedd8e4c865263f183dd4aaf49277`); own updater **26239693** (`0xfab8f2cb0d2af9c8dc7d172baea6bca5457e7b21a927226d93881493704ac2ab`); retired liquidator inbound **17661857** (`0x84398417c7029d71214311885187e8e8a829c55b01b5904d17a3b33243c99cb0`). These differ from the repair-3 implementer's positive/control choices. V1A.6 deliberately reuses its known FeesSynced anchor, with a new read of actual nonempty logs.

**Accepted-deviation judgment:** sound for V1A.1/1f as scoped. `src/main.rs:205–209` awaits validation and then returns `Ok(())`; raw malformed runs independently confirm exit 0. `render.sh` requires a zero process exit, the explicit success marker and no ERROR lines. Garbage and dangling-reference experiments demonstrate that the latter two actually gate failure. This does not establish a complete schema checker or waive F10; its containment by render is recorded separately.

### Independent M15 set and D11 audit

I extracted the 16 named events from the spec's M15 prose and resolved “module updates” to `ModuleUpgraded` in the independently sourced pin artifact, without using the coverage script to supply the expected set. Canonical types came from that artifact. Each of these 17 deletions independently exits 1:

```text
CooldownDurationUpdated(uint64)
DeclareOperatorFeePeriodUpdated(uint64)
ExecuteOperatorFeePeriodUpdated(uint64)
LiquidationThresholdPeriodSSVUpdated(uint64)
LiquidationThresholdPeriodUpdated(uint64)
MinBlocksBetweenUpdatesUpdated(uint32)
MinimumLiquidationCollateralSSVUpdated(uint256)
MinimumLiquidationCollateralUpdated(uint256)
MinimumOperatorEthFeeUpdated(uint256)
ModuleUpgraded(uint8,address)
NetworkFeeUpdated(uint256,uint256)
NetworkFeeUpdatedSSV(uint256,uint256)
OperatorFeeIncreaseLimitUpdated(uint64)
OperatorMaximumFeeUpdated(uint256)
OracleReplaced(uint32,address,address)
QuorumUpdated(uint16)
Upgraded(address)
```

Missing diff: **empty**. Eight extras: AdminChanged, BeaconUpgraded, ERC20Rescued, Initialized, NetworkEarningsWithdrawn, OwnershipTransferStarted, OwnershipTransferred, SSVNetworkUpgradeBlock. No unconditional required M15 events or their governance functions remain on Telegram. Fork controls independently give OracleReplaced event **Telegram 0 / Discord 1**, successful calldata-only replaceOracle **0 / 1**, remaining acceptOwnership **Telegram 1**; all exit 0 with 0 ERROR.

D11 measurements (`static.py` / `static-rest.py`, plus selftest):

- All six `discord_{cluster,dao,liquidation,operator,oracle,validator}_success` definitions resolve to `DISCORD_WEBHOOK_INFO`; updater-other-sender does too. All **30 trigger display names** are unique.
- Exact dedicated-name set, equal across templates, compose and `.env.example`: `DISCORD_WEBHOOK_CLUSTER_FAILED`, `DISCORD_WEBHOOK_DAO_FAILED`, `DISCORD_WEBHOOK_LIQUIDATION_FAILED`, `DISCORD_WEBHOOK_OPERATOR_FAILED`, `DISCORD_WEBHOOK_ORACLE_FAILED`, `DISCORD_WEBHOOK_VALIDATOR_FAILED`, `DISCORD_WEBHOOK_ORACLE_DISSENT`, `DISCORD_WEBHOOK_DAO_SAFE_FAILED`, `DISCORD_WEBHOOK_LARGE_ETH_TRANSFER`.
- `git grep -nE 'DISCORD_WEBHOOK_(CLUSTER|DAO|LIQUIDATION|OPERATOR|ORACLE|VALIDATOR)_SUCCESS'` returns **five historical lines**, all in the append-only `plans/monitoring-service/qa/cp1a-attempt-{1,2,3}.md` and `workers/cp1a/attempts.md`. No operational occurrence. No history was edited.
- Delete `discord_url`, delete message, or switch INFO to a wrong variable on **each of six collapsed triggers**: **18/18 checker exits 1**. A dangling monitor trigger reference also exits 1. These restore byte-for-byte. F10 separately exposes schema errors bypassing this logic.
- `render.sh --strict` with exactly the remaining referenced set succeeds (**0**); INFO unset fails (**1**). It does not require any removed success variable. Variable values were child-only safe placeholders; no delivery happened.

## 4. Falsifiers and restoration proof

All code mutations were in `$S/mirror`, isolated scratch configs or the allowed `.local-dry/config`; none was in tracked implementation. Every mutation used original-byte restoration in `finally`, or the selftest's scratch copies plus its full deploy-tree hash check. After experiments, every mirrored deploy source file was compared byte-for-byte with the worktree; no differences. Root dry-run preparation restored clean record-only templates. Final tracked implementation diff is empty.

| Falsifier / independent control | Measured outcome |
|---|---|
| Matrix V1A.1f, brace removed | raw exit 0 / 2 ERROR / no success; render exit 1; restored render/raw green. |
| Valid JSON dangling trigger | render exit 1 / 3 ERROR / raw exit 0; structural checker also rejects a missing reference; restore green. |
| Prior F8 nonexistent event | render exit 1, A3; restored green. |
| Prior F8b indexed layout swap | render exit 1, A9; native local DAO unstake 1 → incorrectly indexed 0 → restored 1; other-user unstake 0. |
| Fresh nested tuple input type | change validatorCount type inside updateClusterBalance's tuple ABI: render exit 1, A9; restored green. |
| Prior F2 missing directory/root and empty input | checker exit 1 with S0; no missing-input result credited as clean. |
| Required fields and secret/plain scans | missing networks/match_conditions/triggers/addresses, token/chat_id, six trigger URL/message refs and wrong routing all nonzero; injected plain config rejected with S2; restored green. |
| **Fresh F10 wrong-schema trigger config** | **FAIL**: string and array config each cause jq error but checker exit 0 / OK. The string-case render exits 1 and contains the demonstrated deployment path. |
| Prior F9 plus fresh wrong-type M15 variants | all 17 single deletions and 2 type changes exit 1; restored coverage exit 0. |
| M11 sender predicate | own-sender mainnet block 0 → predicate removed 1 → restored 0, all exit 0 / no ERROR. |
| M8 FeesSynced expression | mainnet positive-value event 0 → `> 0` 1 → restored 0. Local genuinely zero-fee log matches 1; DAO seed unstake 1 / other user 0. |
| Inbox missing input | actual native match accepted (exit 0, one line); remove input/hash/blockNumber/blockHash/from/to individually: each exit **5**, adds **0 bytes**. |
| M13 direct attacker and outgoing/incoming controls | 70-case assertion audit **exit 0**, **0 mismatches under D10**: all 42 attacker × 7 targets × {0, 1 wei, forced revert}, 8 current-wallet-to-attacker direct cases and other direct/control cases agree; unrelated and outgoing controls stay silent. |
| M13 internal/constructor boundary | original six uncovered cases reproduced with receipt success / recipient +1 wei / replay 0 matches. Four attacker cases are **accepted gap D10**; two internal retired-key transfers belong to **CP2c per D10**, outside delivered CP1A. Six added constructor/SELFDESTRUCT cases also give +1 wei and 0 matches: four retired-key cases belong to CP2c; two current-wallet attacker cases are accepted gap D10. All 12 replay exits 0 / 0 ERROR; this is expected-uncovered, not a CP1A failure. |
| RPC empty/null result | known-positive `1 → []:0 → restored:1`, all exit 0; null exit 1 / 2 ERROR. Truthful data required for positive-row credit. |

The M13 reproduction commands were `anvil --fork-url https://eth.drpc.org --fork-block-number 26240350 --port 39547 --silent`, then `python3 "$S/fork.py"` and `python3 "$S/boundary.py"`. Those are audited copies of the earlier independent QA reproducers, adapted for scratch path/port and the explicit D10 expectations. The assertion audit separately requires 70 rows, each expected match count, exit 0 and 0 ERROR; it also asserts all ten extra event/calldata/layout controls. Boundary audit requires each receipt's +1 wei effect and 0 matches. Local helper/proxy code and helper balance were restored and checked. No transaction was sent to mainnet. The local Anvil process was stopped after testing.

Commands/evidence retained outside the repo: `$S/{build,selftest,static,static-rest,fresh-schema,live,fork,boundary,rpc-fault,inbox-fault,row1}.log`; harnesses of the same names; `static-combined.json` (**64** explicit static measurements); `evidence/live.json` (**17** asserted live replays); native blocks/logs/receipts, match/inbox records and boundary traces. Durable minimal reproduction, outcomes and required evidence are recorded here, so the verdict does not depend on scratch files surviving.

## 5. Evidence-plane writes

CP1A transitions from IMPLEMENTED — AWAITING QA to **QA FAILED**, frozen head `45fe9403`, linking this report. The only new gating defect is F10; no implementation repair was made. F9/D11 are independently confirmed; F1b is closed in this checkpoint by D10; F6/F7 retain their existing non-gating ownership. Coordinator action: assign the checker fail-open repair and a fresh QA dispatch.

Only these tracked paths were written and committed on this QA branch:

- `plans/monitoring-service/qa/cp1a-attempt-4.md`
- CP1A's single `**Status:**` line in `plans/monitoring-service/plan.md`
- One appended event in `plans/monitoring-service/execution-log.md`

No push, notification delivery, installation, production config change or mainnet transaction. No `config/`, `config.zip` or `.env*` edit in any checkout. Runtime dry rendering/records remain ignored in this QA worktree.

## 6. Repo proof

Lane base for this QA evidence diff is the dispatched frozen head `45fe9403b1d6b76aeeded3d0d24be4a0d3f40e6b`. Before evidence writes, both commands were empty:

```text
git status --porcelain
<empty>
git diff --name-only 45fe9403b1d6b76aeeded3d0d24be4a0d3f40e6b
<empty>
```

Measured after the authorized evidence writes: `git diff --exit-code 45fe9403b1d6b76aeeded3d0d24be4a0d3f40e6b -- deploy scripts docker-compose.yaml .env.example src tests` is empty / **exit 0**. No implementation file appears in QA porcelain or the QA base diff; only the three authorized evidence paths are committed.


```text
git status --porcelain
 M plans/monitoring-service/execution-log.md
 M plans/monitoring-service/plan.md
?? plans/monitoring-service/qa/cp1a-attempt-4.md

git diff --name-only 45fe9403b1d6b76aeeded3d0d24be4a0d3f40e6b
plans/monitoring-service/execution-log.md
plans/monitoring-service/plan.md
```

The new report is initially untracked and appears in porcelain; staging includes it in the base diff. The committed evidence set is these three paths only. Final commit hash and clean porcelain are sent to the coordinator with completion. **No implementation file appears in either proof.**
