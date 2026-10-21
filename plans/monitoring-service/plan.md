# Plan: SSV monitoring service — alerts M1–M15 on OpenZeppelin Monitor plus a stateful worker

Owner: Marco · Created: 2026-10-20T23:56Z · Current state: [state.md](state.md) · History: [execution-log.md](execution-log.md)
Readiness: **READY WITH OWNED DEFERRALS** (2026-10-21) — OG1 and AUTH-1..4 resolved; OG2 (RPC variables) owned by Marco before CP1A/CP1B; the Codex completion path is proven at the first real QA dispatch (CP0.0 stopped by Marco, event-0003). Review type: self-review by the coordinator.

Spec: [`docs/ssv-specs/monitoring-service-spec.md`](../../docs/ssv-specs/monitoring-service-spec.md) (identical to `ssv-network-triage/extra/monitoring-service-spec.md`). Kit: `ssv-research/checkpointed-workflows/`. Precedent: [`plans/94775-eb-collapse-monitoring.md`](../94775-eb-collapse-monitoring.md) and [`reconcile/`](../../reconcile/README.md).

## 1. Scope and authority

- **Desired outcome and consumer.** The 15 in-repo alerts of the spec (M1–M15) run continuously on an SSV server as two services: the existing OpenZeppelin Monitor fork for stateless per-transaction rules, and one stateful worker (grown from `reconcile/`) hosting one check module per monitor family, with SQLite persistence, incident lifecycle, retrying delivery, health and metrics. Consumer: SSV security/ops (Marco), paged through one Telegram group, informed through Discord. M16 (liquidator keeper internals) is out of this repo.
- **Starting base.** `main` at `86a17dbe1a004713798e08378bc9d59c35e86a45` (upstream OpenZeppelin Monitor v1.5.0 + one SSV commit). Working tree: 428 mode-only changes, 5 content diffs, 9 untracked paths (F1, F2). Source precedence: deployed contracts at pin `v2.0.0+f7ac487` > spec > triage evidence scripts under `ssv-network-triage/evidences/` > `reconcile/` code and its plan > this plan.
- **In scope.** Tracked config templates and rendering for the Monitor; new config-only rules (M13, M15 coverage check, M8 event rules, M10 info, M11 other-sender info, failed-tx inbox bridge); Monitor hardening (durable data dir, `max_past_blocks`, `recovery_config`, env-sourced secrets and RPC); worker framework (scheduler, inventory, check interface, incidents, outbox with Telegram and Discord, inbox consumer, metrics, live-parameter reload); check modules for M1–M4, M6–M7, M8 floor, M9–M12, M14; M5 ported onto the framework with its tests preserved; compose deployment; fork end-to-end tests; a 48 h record-only mainnet soak.
- **Explicitly excluded.** M16; enabling production alert routes (operational, after the soak); contract, oracle or liquidator changes; Rust core changes beyond committing the existing receipt tweak (F6); pushing or opening a PR; deploying to the production server; any mainnet transaction. Unattended coordinator recovery is **UNVERIFIED** and not promised: this run is attended (kit §7).
- **Unresolved expensive ambiguity, asked once.** None blocking. Routing of pages other than M9/M11 to the same Telegram group is ASSUMED (D2) and cheap to change.
- **Permitted local actions.** Workers commit on their lane branch inside their own worktree. Mainnet is read-only (eth_call, eth_getLogs, eth_getBalance, Blockscout GETs, SSV API GETs). anvil forks only. Alerts only to `TEST_*` channels or record mode. No installs without AUTH-1. No deletion of `config/`, `config.zip`, `.env`, `.local-dry/` (user-owned); they are ignored, not removed.
- **External or destructive actions, owner approval.** Toolchain installs (OG1/AUTH-1), production RPC credentials (OG2), the soak host and its credentials (OG3), production deployment and route enabling (out of scope), `git push` (never in this plan).
- **Models and effort (agreed 2026-10-21, no fallback).** Coordinator: this Claude Code session (Fable 5.1) in the repo root. Implementers: Claude via Orca, `--model opus --effort high` for CP1B, CP2a–e, CP3; `--model sonnet --effort high` for CP0 and CP1A. QA: **Codex GPT Sol 6.1 high** via Orca, `--agent codex --model gpt-6.1-sol --effort high`, for every checkpoint QA and the final composed QA; no other agent or model renders a QA verdict. Record `launch.effective` on every start (CP0.0 proved it: event-0003). Optional mechanical pre-QA: the `evidence-runner` Claude agent (Sonnet), never as a verdict.
- **Parallel cap.** Wave 1: 2 implementers. Wave 2: 4 implementers plus 1 queued. Wave 3: 1. QA: one Codex worker per checkpoint at a time. Each lane has disjoint write paths (§5) and a frozen base commit recorded at dispatch.

## 2. Decisions (D rows)

| ID | Decision | Source | Date |
|---|---|---|---|
| D1 | Architecture: OpenZeppelin Monitor stays stateless; one worker process with pluggable checks and shared SQLite, not N cron scripts. | Feasibility review, accepted by Marco | 2026-10-21 |
| D2 | Routing: `page` and `warn` → one Telegram group for **all** monitors (Marco: "yes all pages for now"). `info` and digests → Discord. Dev and QA use `TEST_*` channels only. | Marco | 2026-10-21 |
| D3 | M8 seed holder = DAO Safe `0xb35096b074fdb9bBac63E3AdaE0Bbde512B2E6b6`. Floor = 2 × its cSSV balance measured at worker start (re-measured daily). | Marco | 2026-10-21 |
| D4 | M12: page when the oracle wallet balance < 0.15 ETH (the rotation-doc value); runway alerts as specified. | Marco | 2026-10-21 |
| D5 | M3: fallback-lag warning at N = 50 blocks; N2 = 7,200 blocks. Both config-overridable, never hard-coded in a check. | Marco | 2026-10-21 |
| D6 | RECOMMENDED monitors M6, M7, M8, M10, M14, M15 are approved to build; M16 stays in the liquidator repo. | Marco | 2026-10-21 |
| D7 | Workers run through Orca (Claude implementers, Codex QA); the coordinator proves the Orca loop before the first real dispatch (CP0.0). Fallback if the loop cannot be driven from this terminal: implementers as Claude subagents with worktree isolation and `worktree.baseRef = "head"`, QA still Codex through `orca agent launch`. | Marco (QA on Codex), coordinator (mechanics) | 2026-10-21 |
| D8 | Legacy-cluster inventory for M6/M7 comes from SSV API v4 (`migrated=false`) reconciled with logs; the subgraph is a one-time cross-check, not a dependency. | Coordinator design choice; verify in CP2e | 2026-10-21 |
| D9 | Cluster tuples from the SSV API are untrusted input: every tuple used for a view call is validated against the stored cluster hash (M1 detector). | Spec §4, reconcile precedent | 2026-10-21 |

## 3. Measured-facts ledger (F rows)

| ID | Fact | State | Probe | Date |
|---|---|---|---|---|
| F1 | HEAD `86a17db` on `main`. `git status --porcelain` = 437 lines: 428 mode-only changes (100644→100755), content diffs in `.dockerignore`, `.gitignore`, `Dockerfile.production`, `src/services/filter/filters/evm/filter.rs` (+16/−1), `tests/integration/filters/evm/filter.rs` (+62). | MEASURED | `git diff --summary`, `git diff --numstat` | 2026-10-21 |
| F2 | Untracked: `reconcile/` (eb_storage, eb_events, eb_model, eb_reconcile, tests, scripts, Dockerfile, README, config.example), `plans/`, `docs/ssv-specs/`, `scripts/local-dry-run.sh`, `docker-compose.local.yaml`, `docker-compose.reconcile.yaml`, `config.zip` (production config with plaintext Telegram token and Discord webhooks; **not ignored**), `.env copy.example` (no secrets), `.local-dry/`. `reconcile/.gitignore` already ignores `.venv/`, `data/`, `config.json`, sqlite and status files. | MEASURED | `git status --porcelain \| grep '^??'`, `unzip -l config.zip` | 2026-10-21 |
| F3 | `config/` is gitignored: 24 monitors, 24 triggers (Discord webhooks and a Telegram bot token as `{"type":"plain"}`), 1 network (`cron */2s`, `confirmation_blocks` 2, `store_blocks` true, one plain RPC URL, no `max_past_blocks`, no `recovery_config`), 1 filter script (oracle dissent). | MEASURED | `jq` over `config/**` | 2026-10-21 |
| F4 | Host toolchain (login shell, re-measured after Marco's installs, event-0002): `cargo`/`rustc` 1.90.0 (`~/.cargo/bin`, toolchain 1.90.0 active), `uv` 0.13.4, `ruff` 0.17.1, Docker 29.9.1 with Compose v5.6.0, Foundry v1.8.5 (`anvil`, `forge`, `cast`) under `~/.foundry/bin` which is **not on the login PATH**: workers call `~/.foundry/bin/anvil` or export `PATH=$HOME/.foundry/bin:$PATH`. `python3` system = 3.9.6; `node` v26.11.1; `jq`; `codex` CLI (`model = "gpt-6.1-sol"`); Orca 1.4.232 ready, Codex OAuth account present, host `local` only. `reconcile/.venv` must be recreated with `uv venv reconcile/.venv --python 3.12` (its python symlink is dangling). | MEASURED | `zsh -lic 'command -v …; cargo --version; …'`, `ls ~/.foundry/bin` | 2026-10-21 |
| F5 | `reconcile` unit tests: **NOT RUN** on this host (F4). README and plan claim 30 tests covering gates 1–3. | ASSERTED | `reconcile/README.md` | 2026-10-21 |
| F6 | Rust tests: **NOT RUN** (F4). CI runs `cargo fmt --check`, `cargo clippy --all-features --all-targets -D warnings`, `cargo test --test integration`, `cargo test --test properties`. The uncommitted filter change adds `test_failure_monitor_only_fetches_receipts_for_monitored_transactions`. | ASSERTED | `.github/workflows/ci.yaml`, `tests/integration/filters/evm/filter.rs` | 2026-10-21 |
| F7 | Orca: repo registered 2026-10-21 as `316cdd8d-3d6f-4dd5-9b4b-34ac5792bf2a` (event-0002). A trial Run `run_62bda6681d59` was created for CP0.0 and is **retired**; the execution coordinator creates its own Run after loading the version-matched orchestration skill and never reuses IDs from this plan. `worker-start` supports `--worktree new-child --base-branch <ref> --agent claude\|codex --model <id> --effort <level>`. | MEASURED | `orca repo add --json`, `orca orchestration run-create --json`, `worker-start --help` | 2026-10-21 |
| F8 | Monitor CLI: `--check`, `--monitor-path`, `--network`, `--block`, `--metrics`, `--metrics-address`, log flags. Transaction expressions expose `value`, `from`, `to`, `hash`, `gas_price`, `nonce`, `input`, `gas_used`. `trigger_conditions` scripts return only true/false on the last stdout line. Script triggers receive `{"monitor_match": …, "args": …}` on stdin. No dedup, cooldown or revert-reason decoding. Last processed block is `data/<slug>_last_block.txt`. | MEASURED | `src/main.rs`, `src/services/filter/filters/evm/filter.rs`, `src/services/trigger/script/executor.rs`, `src/services/blockwatcher/storage.rs` | 2026-10-21 |
| F9 | `docker-compose.yaml` mounts `/app/data` from `${MONITOR_DATA_DIR:-/dev/null}`; `docker-compose.reconcile.yaml` gives the worker a persistent volume, `read_only: true`, env-sourced secrets. | MEASURED | compose files | 2026-10-21 |
| F10 | SSV API v4 `GET /api/v4/mainnet/clusters?perPage=1000&page=N` returns `clusterId`, `ownerAddress`, `operators`, `validatorCount`, `networkFeeIndex`, `index`, `active`, `isLiquidated`, `migrated`, `ethBalance`. Oracle proof API: `https://ssv-oracle.ssv.network/api/v1/proof/{clusterId}`. | ASSERTED (from triage scripts) → MEASURED at CP1B V1B.9 | `evidences/#89748/report-staking-insolvency-impact.ts:60-80` | 2026-10-21 |
| F13 | Bare `orca` on the login PATH is `/usr/local/bin/orca`, a root-owned symlink that fails with `Unable to determine Orca.app path from symlink`. The working executable is `/Applications/Orca.app/Contents/Resources/bin/orca`. The CP0.0 Codex smoke worker launched correctly (`launch.effective` codex / gpt-6.1-sol / high) and did its task, but its `worker_done` failed on the bare `orca`. Fix: `sudo ln -sfn /Applications/Orca.app/Contents/Resources/bin/orca /usr/local/bin/orca` (Marco). Until then, task specs must name the full path. | MEASURED | `/usr/local/bin/orca --version`, smoke transcript | 2026-10-21 |
| F11 | Git identity set locally on 2026-10-21: `Marco Tabasco <marco@ssvlabs.io>` (the active `gh` account `mtabasco`); a real commit is first proven by CP0 (V0.10). | MEASURED | `git config user.name`, `gh api user` | 2026-10-21 |
| F12 | Spec reference values (block 25,933,264 unless noted): liquidation threshold ETH 42,960 blocks; SSV 100,240 blocks; min collateral 0.000325 ETH; quorum 7,500 bps; validators per operator 3,000; oracle cadence 3×/day. **Dated references for tests only**; checks read live values. | ASSERTED | spec §5 | 2026-10-21 |

## 4. Acceptance (A rows)

Evidence states: `ASSERTED` (inherited) · `MEASURED` (probed once) · `GATED` (repeatable, falsifiable test) · `LIVE` (real RPC or API) · `OPERATOR` (target host) · `DEFERRED` (owned, with re-entry).

| ID | Outcome | Owner | Independent oracle | Env / state | Blocks | Evidence |
|---|---|---|---|---|---|---|
| A1 | Tracked templates render a Monitor config that passes `--check`, contain no plaintext secret, and the new rules (M13, M8 events, M11 other-sender, inbox bridge) match on replayed mainnet blocks and do not match on sender-control blocks. | CP1A | `openzeppelin-monitor --check`; one-shot `--monitor-path … --block N` on discovered blocks; secret-scan grep | local + RPC · GATED/LIVE | CP3 | V1A.1–V1A.6 |
| A2 | Worker framework: M5 behaviour preserved (all pre-existing scenarios still pass, same incident semantics), check interface frozen and documented, scheduler, inventory with tuple-hash validation, incidents, outbox with Telegram and Discord, inbox consumer with revert-selector decode, metrics endpoint, live-parameter reload. | CP1B | pytest on the in-memory chain; HTTP probe; restart-idempotency test | local · GATED | CP2a–e | V1B.1–V1B.10 |
| A3 | M3/M4/M14 alert at the specified severities on scripted scenarios and stay silent on benign ones; thresholds come from chain reads; per-sweep RPC calls bounded. | CP2a | pytest scenarios + call-count assertion | local · GATED; fork · LIVE at CP3 | CP3 | V2a.* |
| A4 | M9/M10/M11 round state machine: failed round, cadence, dissent streak, slot silence, rotation during open round, updater silence and revert rate. | CP2b | pytest scenarios with clock control | local · GATED | CP3 | V2b.* |
| A5 | M12 runway and balance alerts; M8 supply floor. | CP2c | pytest scenarios | local · GATED | CP3 | V2c.* |
| A6 | M1 stale-tuple detection with repair tuple and re-poison memory; M2 capacity hard/early/velocity rules; inbox reverts mapped. | CP2d | pytest + recorded mainnet receipt fixtures | local · GATED; fixtures built LIVE | CP3 | V2d.* |
| A7 | M6/M7 legacy liquidation coverage and SSV solvency surplus. | CP2e | pytest; one-time subgraph cross-check | local · GATED; LIVE optional | CP3 | V2e.* |
| A8 | Composed stack (`monitor` + `watch`) builds, `docker compose config` validates, starts in record mode against a test RPC, advances its checkpoint, serves `/metrics`, and raises a health alert when the RPC is cut. | CP3 | compose commands; curl; RPC-cut control | local docker · GATED | CP4 | V3.* |
| A9 | Fork end-to-end: M5 trace unchanged; M3 time-warp (onset → N warn → N2 page → computed zero); M9 dissent and failed round; M12 balance page. | CP3 | `reconcile/scripts/fork_e2e.py` on anvil mainnet fork | fork · LIVE | CP4 | V3.6–V3.9 |
| A10 | 48 h record-only mainnet soak: zero `page` findings not explained by a real on-chain condition, health green, checkpoint lag < 50 blocks, incidents list reviewed. | Marco (OG3) | worker `status` output and outbox table | target host · OPERATOR | CP4 | log anchor |
| A11 | Hygiene: 0 mode-only diffs, `config.zip` and `.local-dry/` ignored, no secret in any tracked file, SSV material tracked on the integration branch. | CP0 | `git diff --summary`, secret scan with injected-token falsifier | local · GATED | CP1A, CP1B | V0.* |
| A12 | Final composed independent QA by Codex over the whole integration diff returns PASS or PASS WITH NOTES, every note owned. | CP4 | QA report first line | local · GATED | delivery | qa/cp4-attempt-N.md |

## 5. Task graph and ownership

| Task | Observable completion | Depends on | Allowed writes | Implementer / QA | Timebox | Re-entry |
|---|---|---|---|---|---|---|
| CP0.0 Orca loop proof (coordinator) | Receipts for run-create, a trivial Codex worker_done, worker-release recorded in the log | OG1 not required | `plans/monitoring-service/*` only | coordinator | 1 h | repeat with the fallback of D7 |
| CP0 Baseline and hygiene | Integration branch `plan/monitoring-service` with SSV material tracked, modes reverted, templates extracted, spec §6 updated, baselines recorded | OG1, AUTH-1..4, CP0.0 | `.gitignore`, `.claude/settings.json`, `deploy/config/**`, `docs/ssv-specs/*.md` §6, `plans/monitoring-service/**`, git metadata; commit of the existing `src/`+`tests/` diff only | Sonnet high / Codex gpt-6.1-sol high | 3 h | continue from the last green V0 row |
| CP1A Config lane | A1 | CP0 VERIFIED | `deploy/**`, `docker-compose.yaml`, `scripts/local-dry-run.sh`, `.env.example` | Sonnet high / Codex gpt-6.1-sol high | 1 day | per V1A row |
| CP1B Worker framework | A2 | CP0 VERIFIED | `reconcile/**`, `docker-compose.reconcile.yaml` | Opus high / Codex gpt-6.1-sol high | 2 days | per V1B row |
| CP2a Liquidation family (M3, M4, M14) | A3 | CP1B VERIFIED | `reconcile/checks/m3_*.py`, `m4_*.py`, `m14_*.py`, `reconcile/tests/test_m3.py`, `test_m4.py`, `test_m14.py`, `reconcile/tests/fixtures_liquidation.py`, `reconcile/config.example.json` (own key only) | Opus high / Codex gpt-6.1-sol high | 2 days | per V2a row |
| CP2b Oracle family (M9, M10, M11) | A4 | CP1B VERIFIED | `m9_*.py`, `m10_*.py`, `m11_*.py`, their tests, `fixtures_oracle.py`, own config key | Opus high / Codex gpt-6.1-sol high | 2 days | per V2b row |
| CP2c Wallets and supply (M12, M8 floor) | A5 | CP1B VERIFIED | `m12_*.py`, `m8_*.py`, their tests, `fixtures_wallets.py`, own config key | Opus high / Codex gpt-6.1-sol high | 1 day | per V2c row |
| CP2d Cluster integrity and capacity (M1, M2) | A6 | CP1B VERIFIED | `m1_*.py`, `m2_*.py`, their tests, `fixtures_cluster.py`, `reconcile/tests/fixtures/receipts/*.json`, own config key | Opus high / Codex gpt-6.1-sol high | 2 days | per V2d row |
| CP2e Legacy SSV (M6, M7) | A7 | CP1B VERIFIED; a wave-2 slot | `m6_*.py`, `m7_*.py`, their tests, `fixtures_legacy.py`, own config key | Opus high / Codex gpt-6.1-sol high | 1.5 days | per V2e row |
| CP3 Integration, compose, fork e2e, soak start | A8, A9; A10 started | CP1A, CP2a–e VERIFIED | everything on `plan/ms/cp3-integration`; single writer | Opus high / Codex gpt-6.1-sol high | 2 days + 48 h soak | per V3 row |
| CP4 Final QA and acceptance | A10–A12 | CP3 VERIFIED, OG3 | `plans/monitoring-service/**` only | Codex gpt-6.1-sol high QA + coordinator + Marco | 1 day | per finding |

Parallelism rules: disjoint writes only; each CP2 lane adds files, it never edits `reconcile/eb_*.py`, `checks/base.py`, `checks/registry.py` or another lane's files. Registration of a new check is one line in `reconcile/checks/__init__.py`, appended in a lane-specific block so merges are textual. Shared config keys are per check (`checks.m3`, …). The integrator (CP3) is the only writer to shared files after wave 2 starts. A failing acceptance row blocks only its named dependants.

## 6. Frozen interfaces (inputs to wave 1 and wave 2)

**Branches and worktrees.** Integration branch `plan/monitoring-service` from `main@86a17db`. Lane branches `plan/ms/<cp>` created by `worker-start --worktree new-child --name ms-<cp> --base-branch plan/monitoring-service`. QA worktrees are fresh children of the lane branch head, so QA sees only committed work.

**Dispatch commands (the coordinator's own Run).** Placement precondition (F14, event-0009): Orca resolves `--worktree current` and the lineage of `new-child` from the coordinator's own Orca tab (`ORCA_WORKSPACE_ID`), not from the shell cwd. The coordinator therefore runs in an Orca tab of the `ssv-network-monitor` workspace (`ORCA_WORKSPACE_ID` = `316cdd8d-3d6f-4dd5-9b4b-34ac5792bf2a::/Users/marco/ssv/repos/ssv-network-monitor`), checked before run-create, and every `worker-start` also passes `--repo id:316cdd8d-3d6f-4dd5-9b4b-34ac5792bf2a`; the receipt's worktree effect must name that repo id and a path under the monitor repo or its Orca workspaces, else stop. The coordinator loads the orchestration skill (`orca skills get orchestration`) and creates a fresh Run; the commands below are shapes, the skill is authoritative for syntax. Before the first dispatch the coordinator checks `orca --version` works (F13); if it does not, it uses the full app path and tells workers to do the same. Every task spec says: "export PATH=$HOME/.foundry/bin:$PATH". Implementer: `orca orchestration task-create --spec "<packet>" --task-title "<CP> implement" --json`, then `orca orchestration worker-start --task <task_id> --worktree new-child --name ms-<cp> --base-branch plan/monitoring-service --agent claude --model <opus|sonnet> --effort high --json`. QA, always Codex GPT Sol 6.1 high: `orca orchestration task-create --spec "<qa/QA-CONTRACT.md + checkpoint id + lane branch head hash>" --task-title "<CP> QA" --json`, then `orca orchestration worker-start --task <qa_task_id> --worktree new-child --name ms-qa-<cp> --base-branch plan/ms/<cp> --agent codex --model gpt-6.1-sol --effort high --json`. Compare `launch.requested` with `launch.effective` and refuse to proceed on a mismatch. Wait with `orca orchestration check --wait --types "worker_done,escalation,question" --timeout-ms 900000 --json`; after settlement `worker-release --dispatch <id>`.

**Alert routing (D2).** Severity `page`, `warn` → Telegram (`TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`); `info`, `digest` → Discord (`DISCORD_WEBHOOK_INFO`). Development and QA set `TEST_TELEGRAM_TOKEN`, `TEST_TELEGRAM_CHAT_ID`, `TEST_DISCORD_WEBHOOK`, or run the worker with `alerts.mode: "record"`. Re-alert cadence while an incident stays open: page every 6 h, warn every 24 h, info once plus the daily digest. Escalation re-sends under the same incident id.

**Failed-transaction inbox (Monitor → worker).** Append-only JSONL at `data/inbox/failed-tx.jsonl` in the Monitor container, mounted read-only at `/inbox/failed-tx.jsonl` in the worker. One line per matched failed transaction:

```json
{"schema":1,"tx_hash":"0x…","block":26230085,"block_hash":"0x…","from":"0x…","to":"0x…","selector":"0x…","function":"liquidate(address,uint64[],(uint32,uint64,uint64,bool,uint256))","status":"failure","monitor":"SSV Failed Tx Inbox","observed_at":"2026-10-21T00:00:00Z"}
```

Written by `deploy/config/triggers/scripts/inbox_append.sh` from the script-trigger stdin JSON (`.monitor_match.EVM.transaction.{hash,blockNumber,blockHash,from,to,input}` and `.monitor_match.EVM.monitor.name`). The worker keeps its read offset in `meta`, de-duplicates on `(tx_hash, block_hash)`, restarts from offset 0 when the file shrinks, and resolves the revert selector with `eth_call` of the same calldata at `block - 1` (Blockscout v2 `revert_reason` as fallback). Functions in the inbox monitor: `commitRoot`, `updateClusterBalance`, `liquidate`, `liquidateSSV`, `migrateClusterToETH`, `reactivate`, any sender.

**Check interface (sketch; CP1B publishes the authoritative `reconcile/checks/README.md`).**

```python
class Check(Protocol):
    id: str                                  # "m3"
    schedule: Schedule                       # Every(seconds) | OnEvents({"RootCommitted", ...}) | OnInbox | NearSetEveryBlock
    def prepare(self, ctx: Ctx) -> None      # read live parameters; called at start and on any M15 parameter event
    def run(self, ctx: Ctx, block: int) -> list[Finding]

@dataclass(frozen=True)
class Finding:
    check: str; key: str                     # incident key within the check (cluster id, round block, wallet, …)
    severity: Literal["info", "warn", "page"]
    title: str; body: str
    subject: dict; evidence: dict            # JSON-serialisable; tx hashes, blocks, numbers as strings
    resolve: bool = False                    # closes the open incident for (check, key)
```

`Ctx` offers: `rpc` (batched `eth_call`/`eth_getBalance`/`eth_getLogs` pinned to `block`), `db`, `inventory(block)` (clusters with validated tuples, operators, legacy flag), `params` (live protocol parameters), `inbox`, `clock`, `config[check.id]`, `log`. Checks never send alerts or write the outbox directly; they return findings. Checks never hard-code protocol parameters; test fixtures carry the spec's dated values.

**Incident lifecycle.** Key `(check, key)`. First finding opens; a higher severity escalates under the same id; `resolve=True` closes and sends a resolution message for page/warn; absence of a finding never resolves unless the check declares `resolve_on_absence = True`. Restart idempotency: an open incident re-found after restart produces no new message before its re-alert cadence.

**Where the Monitor rules live.** The running Monitor reads `config/`. That directory is gitignored, holds live secrets, and does not exist in lane worktrees, so workers never edit it directly. Rules are authored and reviewed as tracked templates in `deploy/config/` and rendered into `config/` by `deploy/render.sh` with secrets from the environment. CP0 reads `config/` once to extract the templates; rendering into the production `config/` is Marco's deploy step; tests render into `.local-dry/config`.

**Config template layout (CP0 extracts, CP1A extends).** `deploy/config/{monitors,triggers,triggers/scripts,filters,networks}/` mirrors `config/`; secrets are `{"type":"environment","value":"<NAME>"}`; the network's RPC is `MAINNET_RPC_URL` with an optional second `MAINNET_RPC_URL_2`. `deploy/render.sh --out <dir>` copies templates, drops the optional second RPC if its variable is unset, and runs `openzeppelin-monitor --check` in the output's parent. `scripts/local-dry-run.sh` consumes the rendered directory.

## 7. Verification contract

Every row is run on its own (no `&&` chains credited), with exit code and counts recorded in the execution log. `NOT RUN` is a result. Falsifiers are applied as a one-token mutation, shown red, restored, and the tree shown clean. Commands assume the worktree root; Python commands use `reconcile/.venv/bin/python` created by `uv venv reconcile/.venv --python 3.12` and `uv pip install --python reconcile/.venv/bin/python -r reconcile/requirements-dev.txt`.

### CP0.0 — Orca loop proof (coordinator)

| V-ID | Command | Expected | Must fail when | Invalidation |
|---|---|---|---|---|
| V0.0a | `orca status --json` | `runtime.state == "ready"` | runtime down | Orca version |
| V0.0b | `orca repo add --path /Users/marco/ssv/repos/ssv-network-monitor --json` | repo id returned | path not a git repo | — |
| V0.0c | `orca orchestration run-create --objective "ssv-network-monitor: monitoring-service plan" --json` | run id | — | — |
| V0.0d | `orca orchestration worker-start --spec "Smoke test. Run: git rev-parse HEAD; print it; then send worker_done with --outcome succeeded." --worktree current --agent codex --model gpt-6.1-sol --effort high --json` | exit 0, `launch.effective.model == gpt-6.1-sol` | model refused | Codex auth |
| V0.0e | `orca orchestration check --wait --types "worker_done,escalation,question" --timeout-ms 900000 --json` | the smoke `worker_done` arrives and is acknowledged | nothing arrives in 15 min → fall back per D7 | — |
| V0.0f | `orca orchestration worker-release --dispatch <id> --json` | released; `worker-list --run <id> --terminal-state reclaimable` empty | — | — |

### CP0 — Baseline and hygiene

| V-ID | Command | Expected | Must fail when | Invalidation |
|---|---|---|---|---|
| V0.1 | `zsh -lic 'for t in cargo rustc uv docker anvil; do command -v $t; done'` | all present (OG1) | any missing | host change |
| V0.2 | `git diff --summary \| grep -c "mode change"` | `0` after the revert; `git diff --numstat` still lists exactly the 5 content files of F1 | a content diff lost | — |
| V0.3 | `git grep -nE "discord\.com/api/webhooks/[0-9]+/\|[0-9]{8,10}:AA[A-Za-z0-9_-]{30,}" -- . ':!tests/integration/fixtures'` | 0 rows on the integration branch | a secret is tracked | any commit |
| V0.3f | Falsifier: add a dummy token built at run time, `printf 'TOKEN=1234567890:AA%s\n' "$(printf 'x%.0s' {1..34})"` (never write its literal value into a tracked document), to a scratch tracked file, rerun V0.3, remove, `git status --porcelain` clean | V0.3 reddens, tree clean after | scan does not catch it | — |
| V0.4 | `git check-ignore -v config.zip .local-dry` | both ignored | not ignored | `.gitignore` |
| V0.5 | `git ls-files reconcile plans docs/ssv-specs deploy scripts/local-dry-run.sh docker-compose.local.yaml docker-compose.reconcile.yaml \| wc -l` | > 0 for each path; `git ls-files reconcile/.venv reconcile/data` = 0 | venv or data tracked | — |
| V0.6 | `jq -r '.. \| objects \| select(.type? == "plain") \| .value' deploy/config/triggers/*.json deploy/config/networks/*.json` | empty (all secrets and RPC are environment references) | any plain value | template edit |
| V0.7 | `cargo build --release --locked` | exit 0, `target/release/openzeppelin-monitor` exists | build error | toolchain, lockfile |
| V0.8 | `cargo test --locked --test integration -- filters::evm::filter` | exit 0; includes `test_failure_monitor_only_fetches_receipts_for_monitored_transactions` passing | test red | `src/` change |
| V0.9 | `reconcile/.venv/bin/python -m pytest -q reconcile/tests` | exit 0; record the count (F5 becomes MEASURED) | any failure | reconcile change |
| V0.10 | `git log --oneline main..plan/monitoring-service` | ≥ 3 commits: hygiene, SSV material, templates (and the Rust tweak if V0.8 green) | — | — |
| V0.11 | `grep -n "DECIDED 2026-10-21" docs/ssv-specs/monitoring-service-spec.md` | §6 lists D3, D4, D5, D6, D2 as resolved | — | spec edit |
| V0.12 | `jq .worktree .claude/settings.json; jq '.permissions.allow \| length' .claude/settings.json` | `{"baseRef":"head"}` and a non-empty allowlist; no `git push` in allow | — | settings edit |

### CP1A — Config lane

| V-ID | Command | Expected | Must fail when | Invalidation |
|---|---|---|---|---|
| V1A.1 | `deploy/render.sh --out .local-dry/config` then `(cd .local-dry && ../target/release/openzeppelin-monitor --check)` | exit 0 | malformed JSON or ABI mismatch | templates |
| V1A.1f | Falsifier: remove a closing brace in one rendered monitor, rerun `--check`, restore | non-zero exit | `--check` passes garbage | — |
| V1A.2 | V0.6 on `deploy/` plus V0.3 | empty / 0 rows | plain secret | templates |
| V1A.3 | `scripts/local-dry-run.sh --monitor-path config/monitors/ssv_failed_tx_inbox.json --network evm_ethereum_mainnet --block <B_fail>` with `B_fail` discovered by `eth_getLogs`/Blockscout as a block containing a reverted `liquidate` to the proxy | one match; `.local-dry/data/inbox/failed-tx.jsonl` gains one line with the §6 fields and `selector` equal to the first 4 bytes of the transaction input | no line or wrong fields | block choice |
| V1A.4 | Same one-shot for `ssv_updater_other_sender_monitor.json` at a block with an `updateClusterBalance` from `0x28bE…` (2026-10-06 → 10-12 window) | one match | — | — |
| V1A.4f | Sender control: one-shot at a block whose only `updateClusterBalance` is from `0x16A5…` | zero matches | expression ignores `from` | — |
| V1A.5 | One-shot for `ssv_retired_addresses_monitor.json` at a block with an incoming value transfer to `0x3A98…` or `0x4BC6…` (discover via Blockscout v2 address transactions) | one match | zero | — |
| V1A.6 | M8 event rule pipeline proof: temporarily set the `FeesSynced` expression to `newFeesWei > 0`, one-shot at a block with a real `FeesSynced`, expect a match; restore to `== 0`, rerun, expect none; `git status --porcelain` clean | as stated | expression not applied | — |
| V1A.7 | `diff <(jq -r '.match_conditions.events[].signature' deploy/config/monitors/ssv_dao_success_monitor.json \| sort) <(spec M15 list)` | every M15 event present (list the diff) | missing event | spec |
| V1A.8 | `docker compose -f docker-compose.yaml config` | validates; `/app/data` is a real path (not `/dev/null`); `max_past_blocks` and `recovery_config` present in the rendered network | — | compose |

### CP1B — Worker framework

| V-ID | Command | Expected | Must fail when | Invalidation |
|---|---|---|---|---|
| V1B.1 | `reconcile/.venv/bin/python -m pytest -q reconcile/tests` | exit 0; count ≥ V0.9 count + new framework tests; 0 skips unless named | any red | — |
| V1B.2 | `reconcile/.venv/bin/ruff check reconcile` (ruff added to `requirements-dev.txt`) | exit 0 | lint error | — |
| V1B.3 | `reconcile/.venv/bin/python -m pytest -q reconcile/tests/test_framework.py -k scheduler_dispatches_registered_check` | a stub check registered with `Every(60)` runs once per interval and once per matching event | scheduler broken | — |
| V1B.4 | `… -k restart_idempotent` | same finding before and after a simulated restart produces exactly one outbox row | duplicate row | — |
| V1B.4f | Falsifier: change the incident key join to drop `check` from the key; V1B.4 or a two-check collision test reddens; restore | red then green | dedup not keyed | — |
| V1B.5 | `… -k routing` | page → telegram, info → discord in the outbox `delivery` column; record mode writes JSON lines | wrong route | — |
| V1B.6 | `… -k m5` (the ported #94775 scenarios, enumerated by name in the attempt) | every pre-existing scenario name still present and green | a scenario removed or weakened | — |
| V1B.7 | `… -k inbox` | fixture line parsed; offset persisted; shrink → reread; `eth_call` stub returning `0x12e04c87` decodes to `IncorrectClusterState` | — | — |
| V1B.7f | Falsifier: alter the selector constant by one hex digit; V1B.7 reddens; restore | red then green | — | — |
| V1B.8 | `curl -s http://127.0.0.1:<port>/metrics` while `once --serve-metrics` runs on the fake chain | text exposition with `ssv_watch_checkpoint_block`, `ssv_watch_lag_blocks`, `ssv_watch_open_incidents`, `ssv_watch_outbox_undelivered`, per-check `ssv_watch_check_last_run_seconds` | endpoint missing | — |
| V1B.9 | `reconcile/.venv/bin/python -m reconcile.inventory --probe --rpc "$MAINNET_RPC_URL"` (or equivalent CLI) | fetches API v4 pages, validates ≥ 5 sampled tuples against storage hashes, prints counts; F10 becomes MEASURED | API shape differs | LIVE |
| V1B.10 | `FORK_RPC_URL=… reconcile/.venv/bin/python reconcile/scripts/fork_e2e.py` | same five-step outcome as the plan's gate 1 (latent → confirmed → resolved) | changed semantics | LIVE, needs anvil |
| V1B.11 | `reconcile/checks/README.md` exists and the CP2 packets can be built from it alone | coordinator reads it and finds the interface, context fields, config schema, test helpers | — | — |

### CP2 lanes (per lane; the implementer enumerates concrete scenario names)

| V-ID | Command | Expected | Must fail when |
|---|---|---|---|
| V2x.1 | `… -m pytest -q reconcile/tests/test_m<N>.py` for each monitor in the lane | exit 0; one test per spec trigger line at its severity; one benign scenario per trigger that must stay silent | missing scenario |
| V2x.2 | `… -k thresholds_from_chain` | changing the fake chain's parameter (e.g. threshold period) changes the alert without a code change | hard-coded value |
| V2x.3 | `… -k restart` | re-running the check after a simulated restart does not duplicate the open incident | duplicate |
| V2x.4 | `… -k rpc_budget` (CP2a, CP2b, CP2e) | asserted upper bound on `eth_call` count per sweep for a 350-cluster inventory | unbounded calls |
| V2x.5 | Falsifier per monitor, named in the attempt (e.g. `>` → `>=` on N; drop the distinct-slot count; swap burn-rate units) | the named test reddens; restore; clean tree | survives |
| V2x.6 | `… -m pytest -q reconcile/tests` and `ruff check reconcile` | whole suite green | regression in another lane's file (must not happen; disjoint writes) |

Lane-specific rows: CP2a V2a.7 M14 correlates an inbox revert with `ClusterDeposited` below one block of burn in the same or previous block; CP2b V2b.7 "no RootCommitted for 9 h" fires from the clock, not from an event; CP2c V2c.7 runway computed both from trailing spend and from current base fee; CP2d V2d.7 at least 10 recorded mainnet multi-event receipts replay through M1 with the expected validate/no-validate result and the repair tuple identified; CP2e V2e.7 legacy inventory from API `migrated=false` agrees with the log-derived set at one block.

### CP3 — Integration, compose, fork e2e, soak start

| V-ID | Command | Expected | Must fail when |
|---|---|---|---|
| V3.1 | `git merge --no-ff` of each lane branch into `plan/ms/cp3-integration`; `git diff --name-only plan/monitoring-service..HEAD` | union of lane paths; conflicts resolved only in shared files by the integrator | a lane edited another lane's file |
| V3.2 | V1B.1, V1B.2, V0.8 on the integrated tree | green; total test count recorded | — |
| V3.3 | `docker compose -f docker-compose.yaml -f docker-compose.watch.yaml config` | validates; worker has a persistent volume, `read_only: true`, inbox mounted read-only, secrets by env names only | — |
| V3.4 | `docker compose … build` then `up -d` with `alerts.mode: "record"` and `MAINNET_RPC_URL` from `TEST_RPC_URL`; after 15 min: `docker compose exec watch python eb_reconcile.py status` | checkpoint advanced; `healthy: true`; lag < 50 | stuck |
| V3.5 | RPC-cut control: point the worker at an unreachable RPC for 5 min | a `health` incident appears in the outbox within the configured lag window; recovers after restore | silent |
| V3.6 | `fork_e2e.py --scenario m5` | unchanged outcome | — |
| V3.7 | `fork_e2e.py --scenario m3` (anvil: pick a live ETH cluster, `evm_mine` past its threshold with no liquidation) | info at onset, warn at N=50, page at N2, page at computed zero; resolve after an impersonated liquidation | wrong severity order |
| V3.8 | `fork_e2e.py --scenario m9` (impersonated oracles: one dissenting slot three rounds; one round without commit) | warn, page (streak), page (failed round within 30 min of the clock) | — |
| V3.9 | `fork_e2e.py --scenario m12` (impersonated transfer draining the oracle wallet below 0.15 ETH) | page | — |
| V3.10 | `deploy/README.md` runbook reviewed by QA: start, stop, rotate secrets, enable routes, read status and metrics | complete | — |

### CP4 — Final QA and acceptance

| V-ID | Action | Expected |
|---|---|---|
| V4.1 | Codex QA over `git diff main..plan/ms/cp3-integration` using `qa/QA-CONTRACT.md` | first line PASS or PASS WITH NOTES; every note has an owner |
| V4.2 | Soak evidence (OG3): `status` JSON at start and end, outbox rows, incidents table export, metrics snapshots | A10 met or each page explained with on-chain evidence |
| V4.3 | V0.3 secret scan on the final tree; `git log --stat main..` | 0 rows; no `.env`, `config/`, sqlite or `.local-dry` content |
| V4.4 | Deferral table complete; completion statement enumerates remaining failures without promoting them | — |

## 8. Checkpoints

Status vocabulary: `NOT STARTED` · `READY` · `DISPATCHED` (dispatch id) · `IMPLEMENTED — AWAITING QA` · `QA FAILED` · `VERIFIED` · `BLOCKED` · `DONE` (VERIFIED and integrated). Only the Codex QA worker sets `VERIFIED` or `QA FAILED`. Each checkpoint's worker record lives at `workers/<cp>/plan.md` and `attempts.md`; QA reports at `qa/<cp>-attempt-<n>.md`.

### CP0.0 — Orca loop proof

**Status:** PARTIAL — STOPPED BY OWNER (event-0003): V0.0a–d PASS; V0.0e not credited (worker_done failed on F13); V0.0f not run. The Codex completion path is proven at the first real QA dispatch instead. **Owner:** coordinator. **Inputs:** F4, F7, F13, D7. **Stop:** V0.0a–f recorded, or fallback chosen.
Proves that this coordinator terminal can create a Run, start a Codex worker with the agreed model and effort, receive its `worker_done`, and release it. If V0.0e times out, the fallback of D7 is recorded and the Agent-tool path is used for implementers (requires V0.12's `worktree.baseRef`).

### CP0 — Baseline and hygiene

**Status:** VERIFIED — PASS WITH NOTES on frozen head `9772b2da2acd427aeaf6687c27656ee89bd895d8`; [QA attempt 1](qa/cp0-attempt-1.md). Known parallel-test flake retained per event-0007; serial control green; V0.9 equivalent green using the authorized existing venv. **Implementer:** Claude Sonnet high. **QA:** Codex gpt-6.1-sol high. **Inputs:** this plan §1–§7, F1–F12, OG1, AUTH-1..4.
Steps: (1) re-measure V0.1; (2) create `plan/monitoring-service` from `main`; revert mode-only changes with `git diff -p -R --no-ext-diff --no-color | grep -E '^(diff|(old|new) mode)' | git apply` and prove V0.2; (3) `.gitignore`: add `config.zip`, `.local-dry/`; (4) commit the SSV material of F2 (not `config.zip`, not `.env*`, not `.local-dry/`); (5) extract templates: copy `config/{monitors,filters,networks,triggers}` to `deploy/config/` rewriting every `plain` secret and RPC to environment references (`TELEGRAM_TOKEN`, `DISCORD_WEBHOOK_<NAME>` per trigger, `MAINNET_RPC_URL`), prove V0.6 and V0.3; (6) build and test (V0.7–V0.9), commit the existing Rust diff as its own commit only if V0.8 is green, otherwise leave it in the working tree and record; (7) spec §6: mark each open question with its decision and `DECIDED 2026-10-21`; (8) write `.claude/settings.json` with `worktree.baseRef: "head"` and a permissions allowlist for the verification commands (cargo, uv, the venv python, ruff, jq, node, docker compose, git local verbs; deny `git push`); (9) record baselines and any KNOWN RED signature in the log.
**Allowed writes:** §5. **Stop:** all V0 rows have an outcome; timebox 3 h, partial checkpoint at deadline.

### CP1A — Config lane

**Status:** NOT STARTED. **Implementer:** Claude Sonnet high. **QA:** Codex gpt-6.1-sol high. **Inputs:** CP0 VERIFIED, §6 (inbox contract, template layout), spec M8, M10, M11, M13, M15, §4 and §5.
Deliverables: `deploy/render.sh`; new templates `ssv_retired_addresses_monitor.json` (M13: the four addresses, `transactions: status Any, expression "value > 0"`, plus proxy functions `commitRoot`/`updateClusterBalance` with `from` restricted to the old slot-4 key), `ssv_seed_stake_monitor.json` (M8: `FeesSynced` with `newFeesWei == 0`; `UnstakeRequested` with `user == <DAO Safe>`), `ssv_updater_other_sender_monitor.json` (M11 info: `updateClusterBalance` success with `from != 0x16A5…`), `ssv_failed_tx_inbox.json` plus `triggers/scripts/inbox_append.sh` and a `script` trigger; M10 info and M15 coverage checked against the existing DAO success monitor (add missing events); page triggers to `TELEGRAM_*`, info to `DISCORD_WEBHOOK_INFO`; `docker-compose.yaml` hardening (persistent `./data`, env passthrough, `max_past_blocks` sized for a restart catch-up, `recovery_config` every 5 min, optional second RPC); `scripts/local-dry-run.sh` consumes the rendered directory; `.env.example` lists every variable name.
Verify the matching semantics with one-shot runs (V1A.3–V1A.6); do not assume how `transactions` and `functions` conditions combine.
**Stop:** V1A rows done; timebox 1 day.

### CP1B — Worker framework

**Status:** QA FAILED on frozen head `48245069514795f667f2c642062148c09327ab4b` (code `aaf5c25`); [QA attempt 1](qa/cp1b-attempt-1.md): pending legacy outbox cannot route after migration, failed parameter reload leaves stale thresholds usable, missing configured inbox is silent. **Implementer:** Claude Opus high. **QA:** Codex gpt-6.1-sol high. **Inputs:** CP0 VERIFIED, §6 (check interface, incident lifecycle, inbox contract, routing), `reconcile/` as is, F10.
Deliverables: `reconcile/checks/{__init__,base,registry}.py`; `reconcile/inventory.py` (API v4 pages + log reconciliation + storage-hash validation, cached per block, `migrated` flag); `reconcile/params.py` (live parameters, reload on M15 events); `reconcile/alerts.py` (findings → incidents → outbox, routing, cadence, escalation, digest); `reconcile/notify.py` (Telegram as today, Discord webhook, record mode); `reconcile/inbox.py`; `reconcile/metrics.py` (stdlib HTTP server, text exposition); scheduler inside `Worker.step` (interval, event, inbox, near-set-per-block hooks); schema migrations (`schema_version` in `meta`, additive per-check tables); config schema with per-check keys; M5 ported to `checks/m5_eb_desync.py` with every existing scenario kept; `tests/fakechain.py` extended with generic log injection, balances, `eth_call` stubs, clock control; `tests/test_framework.py`; `reconcile/checks/README.md` (the authoritative interface for wave 2); `requirements-dev.txt` adds `ruff`.
**Stop:** V1B rows done; timebox 2 days; V1B.10 is LIVE and may be `NOT RUN` on a host without anvil, recorded as such and owned by CP3.

### CP2a — Liquidation family (M3, M4, M14)

**Status:** NOT STARTED. **Implementer:** Claude Opus high. **QA:** Codex gpt-6.1-sol high. **Inputs:** CP1B VERIFIED, `reconcile/checks/README.md`, spec M3, M4, M14, D5, triage `evidences/#89748/report-staking-insolvency-impact.ts` and `evidences/mainnet-self-operators/followup-20261020/followup.mjs` as reference logic.
Rules: M3 onset info with block; still liquidatable after N (config, default 50) → warn; after N2 (default 7,200) → page; active with computed `getBalance == 0` → page; projected to cross within 24 h → digest info. Sweep policy: projected-near set each block, full inventory every 10 min, after every `RootCommitted` and after any event on a cluster; all view calls batched at one block. M4 after every `RootCommitted` and daily: `deficit > 0` → page, surplus trend → info. M14: inbox revert of `liquidate`/`liquidateSSV` with a `ClusterDeposited` for the same cluster in the same or previous block whose value is below one block of burn → info; three in 24 h → warn; page if the cluster is also at computed zero.
**Stop:** V2a rows; timebox 2 days.

### CP2b — Oracle family (M9, M10, M11)

**Status:** NOT STARTED. **Implementer:** Claude Opus high. **QA:** Codex gpt-6.1-sol high. **Inputs:** CP1B VERIFIED, spec M9–M11, `evidences/eb-oracle-cadence/scan*.mjs`, the existing `config/filters/oracle_dissent_check.py` logic.
Rules: a round is keyed by `blockNum`; `WeightedRootProposed` without `RootCommitted` within 30 min → page; no `RootCommitted` for > 9 h (config; 25 h if daily) → page, driven by the clock; two roots for one `blockNum` → warn; three consecutive dissenting rounds from one slot → page; committed with < 3 distinct slots voting for the committed root → page; a slot absent from the committed root in the last 6 rounds → warn; `commitRoot` reverts from slot addresses via inbox → warn. M10: `OracleReplaced` during an open round → page; `newOracle` already holds a slot → page. M11: per round, `updateClusterBalance` count from `0x16A5…` within 60 min; 0 → warn and query the proof API for mismatching clusters → page if any; two consecutive zero rounds → page; revert rate > 10 % → warn, `IncorrectClusterState` reverts handed to M1 by incident link; weekly full-state leaf comparison (LIVE, `DEFERRED` if the API is unreachable, owner Marco).
**Stop:** V2b rows; timebox 2 days.

### CP2c — Wallets and supply (M12, M8 floor)

**Status:** NOT STARTED. **Implementer:** Claude Opus high. **QA:** Codex gpt-6.1-sol high. **Inputs:** CP1B VERIFIED, spec M12, M8, D3, D4.
Rules: balances of `0x16A5…` and `0x0431…` every 5 min; spend from receipts of their transactions (`gasUsed × effectiveGasPrice`), trailing 7-day average and a base-fee-based projection; runway < 14 d warn, < 7 d page, balance < 0.15 ETH page, daily spend > 0.04 ETH warn for the oracle wallet; < 0.2 ETH warn and < 0.05 ETH page for the liquidator. M8 floor: `cSSV.totalSupply()` < 2 × the DAO Safe's cSSV balance measured at start → warn.
**Stop:** V2c rows; timebox 1 day.

### CP2d — Cluster integrity and capacity (M1, M2)

**Status:** NOT STARTED. **Implementer:** Claude Opus high. **QA:** Codex gpt-6.1-sol high. **Inputs:** CP1B VERIFIED, spec M1, M2, `evidences/#91419/report-family-stale-event-scan.ts`, `evidences/#88326/report-operator-capacity-impact.ts`.
Rules: M1 per transaction: group tuple-carrying events by cluster id; for groups ≥ 2 validate the last tuple with `SSVNetworkViews.isLiquidated` at that block; mismatch → warn with the earliest validating tuple as the repair tuple; inventory hash mismatch (`unknown` status) feeds the same incident; re-poison after a repair → page; poisoned and liquidatable → page; inbox `IncorrectClusterState` reverts from our senders → warn. Fixtures: at least 10 real multi-event receipts recorded from mainnet (built LIVE once, replayed GATED). M2 per operator with legacy validators: hard (`ethValidatorCount + cluster.validatorCount > limit`) → warn; early (headroom − legacy < 900, config) → info, warn if public; velocity (24 h registrations > headroom) → info; inbox `ExceedValidatorLimitWithData` on `migrateClusterToETH`/`reactivate` → warn.
**Stop:** V2d rows; timebox 2 days.

### CP2e — Legacy SSV (M6, M7)

**Status:** NOT STARTED. **Implementer:** Claude Opus high. **QA:** Codex gpt-6.1-sol high. **Inputs:** CP1B VERIFIED, spec M6, M7, D8, `evidences/#91046/*.mjs`.
Rules: M6 for active legacy clusters: `isLiquidatableSSV` for > 7 days → warn; `getBalanceSSV == 0` while active → page; weekly digest of count, validators, SSV/yr burn. M7 daily surplus = proxy SSV − (cSSV supply + outstanding unstakes from `UnstakeRequested`/`UnstakedWithdrawn` logs + live legacy balances + operator SSV earnings + `getNetworkEarningsSSV`); < 1 SSV → page; any change → info. One-time subgraph cross-check of the legacy set (LIVE, optional, `SSV_SUBGRAPH_API_KEY`).
**Stop:** V2e rows; timebox 1.5 days.

### CP3 — Integration, compose, fork e2e, soak start

**Status:** NOT STARTED. **Implementer:** Claude Opus high (single writer). **QA:** Codex gpt-6.1-sol high. **Inputs:** CP1A and CP2a–e VERIFIED.
Deliverables: merged `plan/ms/cp3-integration`; `docker-compose.watch.yaml` (service `watch`, image `ssv-watch`, persistent volume, `read_only`, inbox mount, healthcheck); `deploy/README.md` runbook; `fork_e2e.py` scenarios `m5`, `m3`, `m9`, `m12`; local stack run; soak handoff to Marco (OG3) with the exact commands and the evidence to collect.
**Stop:** V3 rows; timebox 2 days; soak runs 48 h after that.

### CP4 — Final QA and acceptance

**Status:** NOT STARTED. **Owner:** Codex QA, coordinator, Marco. **Inputs:** CP3 VERIFIED, OG3 evidence.
Final composed QA, soak review, secret scan, deferral table, completion statement. Delivery is the integration branch ready for Marco to push and open a PR; neither happens in this plan.

## 9. Operator gates, authority and deferrals

| ID | Item | Owner | Blocking | Re-entry |
|---|---|---|---|---|
| OG1 | Toolchain on the execution host. **RESOLVED 2026-10-21** (F4): installed by Marco; Foundry needs `~/.foundry/bin` on PATH in worker shells. | Marco | — | re-measure V0.1 at CP0 |
| OG2 | RPC endpoints: `MAINNET_RPC_URL` (archive-capable, history `eth_getLogs`, used for one-shot replays, inventory probe, forks) and `TEST_RPC_URL`. Names only in files. | Marco | V1A.3–V1A.6, V1B.9–V1B.10, V3.* | provided in the coordinator environment and passed to workers by name |
| OG3 | Soak host for 48 h record-only run and its credentials; test Telegram/Discord channels. | Marco | A10, CP4 | — |
| AUTH-1 | Toolchain install authorization. **NOT NEEDED** (Marco installed, OG1). | Marco | — | — |
| AUTH-2 | Commits on `plan/monitoring-service` as `Marco Tabasco <marco@ssvlabs.io>`. **GRANTED 2026-10-21**; identity set (F11). | Marco | — | — |
| AUTH-3 | Register the repo in Orca and create child worktrees. **GRANTED 2026-10-21**; registered (F7). | Marco | — | — |
| AUTH-4 | D2 routing for all pages to the Telegram group. **CONFIRMED 2026-10-21.** | Marco | — | cheap to change later |
| DEF-1 | M16 keeper health. | liquidator repo owner | none here | separate plan |
| DEF-2 | Enabling production routes for the worker (today record-only) and for the new Monitor rules. | Marco | none | after A10 |
| DEF-3 | M11 weekly leaf comparison and M1 fork reproduction of the reentrancy poison (needs a malicious owner contract). | CP2b / CP2d record; Marco | none | when the oracle API is reachable from the host; when a PoC contract exists |
| DEF-4 | Unattended coordinator recovery (kit §7). | coordinator | none | not promised in this run; attended operation |
| DEF-5 | Upstream sync of the OpenZeppelin Monitor fork. | Marco | none | separate maintenance |

## 10. Delivery and closure

- Final independent review boundary: `git diff main..plan/ms/cp3-integration` at the recorded head, every V row of CP3, severity bar: any `page`-path defect or any secret leak is blocking.
- Evidence destinations: `plans/monitoring-service/execution-log.md` (anchors), `plans/monitoring-service/evidence/` (bulky outputs, hashed), QA reports under `qa/`. Audience: internal. No credentials, no raw production config.
- Target-environment requirements: Linux host with Docker compose v2, persistent volumes for `data/` and the worker DB, outbound HTTPS to the RPC provider, SSV API, oracle API, Telegram and Discord.
- Preservation and cleanup: every settlement records the lane branch head; Orca worktrees are released only after their branch is merged and the head hash is in the log; the user's `config/`, `config.zip`, `.env`, `.local-dry/` are never touched.
- The completion statement enumerates deferrals DEF-1..5 and any NOT RUN row without promoting them.

## 11. Readiness review (self-review, 2026-10-21)

1. Shaping facts are measured (F1–F4, F7–F9, F11) or visibly ASSERTED (F5, F6, F10, F12) with the row that measures them.
2. Every acceptance row names owner, oracle, environment and evidence state. A10 is OPERATOR and cannot be promoted by local evidence.
3. Completeness: the M1–M15 inventory is the spec's own table; each monitor appears in exactly one lane; M16 is DEF-1.
4. Gates that must fail: each matrix carries falsifiers (V0.3f, V1A.1f, V1A.4f, V1B.4f, V1B.7f, V2x.5, V3.5).
5. Reachability: V0.7–V0.9 and V1B.10 are reachable on this host since OG1 (F4). V1A.3–V1A.6, V1B.9 and V3.* need OG2 (`MAINNET_RPC_URL`, `TEST_RPC_URL`) in the coordinator environment. **R1 resolved 2026-10-21.**
6. Lifecycle failures covered: restart idempotency (V1B.4, V2x.3), RPC loss (V3.5), inbox rotation (V1B.7), reorg handling inherited from `reconcile` (12 confirmations, rewind).
7. Destructive actions: none; forks are disposable; production is out of scope.
8. `DONE` is bounded by A10 and A12.
9. Control/evidence split: this plan plus `execution-log.md`; worker and QA records are separate files; state lives only in `state.md`.

**R2 resolved:** D2 confirmed for all pages (AUTH-4). **R3:** see CP0.0 outcome in the log (event-0003). **R4 resolved:** identity set (F11). **Open:** OG2 RPC variables must be present in the coordinator shell before CP1A/CP1B dispatch; `reconcile/.venv` is recreated by CP0.
