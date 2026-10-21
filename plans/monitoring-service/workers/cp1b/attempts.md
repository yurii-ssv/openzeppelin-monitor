# Attempts — CP1B

Same entry shape as execution-log.md events, worker-side. Append only. Measured UTC (`date -u +%Y-%m-%dT%H:%MZ`).

<a id="attempt-1"></a>
## Attempt 1 — baseline and environment

- Observed UTC: 2026-10-21T10:57Z
- Task / Dispatch: `task_3a2ee3e27b00` / `ctx_98a50f4c4736`
- Frozen base: `4fbb9f161005dd7634a7eaabee2b022d2cc96c2e` (`git rev-parse HEAD` at start; branch `mtabasco/ms-cp1b`; porcelain 0 lines)
- Action and concrete outcome: created the venv per plan §7 (`uv venv reconcile/.venv --python 3.12`; `uv pip install --python reconcile/.venv/bin/python -r reconcile/requirements-dev.txt`: pycryptodome 3.24.0, pytest 9.1.1, pygments 2.21.0); re-ran the V0.9 baseline. `anvil` present at `~/.foundry/bin/anvil` after `export PATH=$HOME/.foundry/bin:$PATH`.

| Verification ID | Exact command | Exit / result / counts / skips | Environment | Evidence path/hash |
|---|---|---|---|---|
| V0.9 (baseline re-run) | `reconcile/.venv/bin/python -m pytest -q reconcile/tests` | exit 0; **30 passed**, 0 skipped | Python 3.12, fresh venv | this entry |

- Deviations and findings: none.
- Resulting head: `4fbb9f1` (no commit)
- Next action: write the framework modules (S2).

<a id="attempt-2"></a>
## Attempt 2 — framework implementation and M5 port

- Observed UTC: 2026-10-21T11:00Z – 11:19Z
- Task / Dispatch: `task_3a2ee3e27b00` / `ctx_98a50f4c4736`
- Frozen base: `4fbb9f161005dd7634a7eaabee2b022d2cc96c2e`
- Action and concrete outcome: commits `c6f4f0c`, `09cfe26`, `02fa5d1`, `aaf5c25` on `mtabasco/ms-cp1b`. Paths changed: only `reconcile/**` and `docker-compose.reconcile.yaml` (`git diff 4fbb9f1..HEAD --name-only | grep -vE '^(reconcile/|docker-compose.reconcile.yaml|plans/monitoring-service/workers/cp1b/)'` → no output).
  - New: `checks/{__init__,base,registry,m5_eb_desync}.py`, `checks/README.md`, `alerts.py`, `notify.py`, `db.py` (schema v1 + in-place migration), `rpc.py` (client + block-pinned `Chain` with call counts), `params.py`, `inventory.py`, `inbox.py`, `metrics.py`, `ruff.toml`, `tests/{test_framework,harness,fake_once}.py`.
  - Changed: `eb_reconcile.py` (config validation, `Worker` = scan/reorg/scheduler/inbox/params/delivery/health/status, CLI `--serve-metrics/--hold`), `eb_events.py` (M15 parameter events, `register_event`, raw storage of non-cluster events), `tests/fakechain.py` (generic log injection, `eth_call` stubs with revert data, balances, mined txs, `FakeClock`), `tests/conftest.py` (m1–m16 markers), `scripts/fork_e2e.py` (framework API), `Dockerfile` (copies all modules + `checks/`), `.dockerignore`, `.gitignore` (`.ruff_cache/`), `config.example.json` (per-check `checks.m5`, D2 env names, inbox, inventory, metrics), `requirements-dev.txt` (`ruff==0.17.1`), `README.md`, `docker-compose.reconcile.yaml` (D2 env names, inbox mount read-only, metrics on 127.0.0.1).
  - `tests/test_worker.py` (the 26 pre-existing scenario functions, unchanged names): only mechanical edits — severity vocabulary `critical→page`, `warning→warn`; the `tick` helper calls `run_checks(force=True)` and reads `checks["m5"].last_evals` instead of the removed `Worker.sweep`; `Capture.send` accepts `route`; the M5-specific incident query reads `m5_incidents` (the framework owns `incidents` now) **plus one added assertion** that the framework incident is resolved; `pytestmark = pytest.mark.m5`. No expected value changed; no assertion removed.
- Design decisions (recorded for QA): M5 keeps its own bookkeeping in `m5_incidents` (the pre-framework `incidents` table, renamed by migration) and returns findings; incident ids `EB94775-…` and outbox keys `…:confirmed:<S>`, `…:update:…`, `…:resolved:<B>` are unchanged. Resolutions are sent as `info` on the incident's route (as before: the pre-framework worker sent them as `info`). Monitor-health messages keep their own outbox severity `health` routed to Telegram. Re-alert cadence (§6: page 6 h, warn 24 h) now also applies to an open M5 incident (it did not before; a framework-wide §6 rule).
- Resulting head: `aaf5c258d1c2ac1162f7692725a78882ea9f33de`
- Next action: run the V1B rows (attempt 3).

<a id="attempt-3"></a>
## Attempt 3 — verification rows

- Observed UTC: 2026-10-21T11:20Z – 11:23Z
- Task / Dispatch: `task_3a2ee3e27b00` / `ctx_98a50f4c4736`
- Frozen base: `4fbb9f1`; tree under test: `aaf5c258d1c2ac1162f7692725a78882ea9f33de`, `git status --porcelain` = 0 before each row.
- Environment: macOS, `reconcile/.venv` Python 3.12 (pytest 9.1.1, pycryptodome 3.24.0, ruff 0.17.1), `PATH=$HOME/.foundry/bin:$PATH` (anvil/cast Foundry v1.8.5), `MAINNET_RPC_URL=TEST_RPC_URL=https://ethereum-rpc.publicnode.com`. Every row was run as its own command.

| Verification ID | Exact command | Exit / result / counts / skips | Environment | Evidence path/hash |
|---|---|---|---|---|
| V1B.1 | `reconcile/.venv/bin/python -m pytest -q reconcile/tests -rs` | exit 0; **62 passed**, 0 skipped (V0.9 baseline 30 → 30 pre-existing + 32 new framework tests) | local | scratch `v1b1.txt` |
| V1B.2 | `reconcile/.venv/bin/ruff check reconcile` | exit 0; "All checks passed!" (rules E4, E7, E9, F, I pinned in `reconcile/ruff.toml`) | local | — |
| V1B.3 | `reconcile/.venv/bin/python -m pytest -q reconcile/tests/test_framework.py -k scheduler_dispatches_registered_check` | exit 0; 1 passed, 31 deselected. The test registers a stub through `@register`, `Every(60)` + `OnEvents({"NetworkFeeUpdated"})`: runs at start, not at +30 s, again at +60 s (`interval`), exactly once for one matching event (`events`, sees it), not for a re-step nor for an unsubscribed `QuorumUpdated` | local | test source |
| V1B.4 | `… -k restart_idempotent` | exit 0; 2 passed, 30 deselected: `test_restart_idempotent_single_outbox_row`, `test_restart_idempotent_two_checks_same_key` | local | — |
| V1B.4f | mutation `reconcile/alerts.py:40` `return ":".join((check, key))` → `return ":".join((key,))`, then `pytest -k restart_idempotent -v` | **red**: exit 1; `test_restart_idempotent_two_checks_same_key` FAILED (`[('ca', '0xsame')] == [('ca', …), ('cb', '0xsame')]`, one incident instead of two); restored with `git checkout -- reconcile/alerts.py`; `git status --porcelain \| wc -l` = 0; re-run `-k restart_idempotent` exit 0, 2 passed | local | this entry |
| V1B.5 | `… -k routing` | exit 0; 4 passed, 28 deselected: page/warn/health → `telegram`, info/digest → `discord` in the outbox `delivery` column; record mode writes JSON lines (`route` field) with receipt `recorded`; live router per route; live mode refuses to start without its secrets | local | — |
| V1B.6 | `reconcile/.venv/bin/python -m pytest -q reconcile/tests -k m5` | exit 0; **32 passed**, 30 deselected = the 30 pre-existing items (26 functions, one parametrised ×5) + 2 migration tests. Pre-existing function names at base vs head: `diff` of `^def test_` lists of `test_worker.py` → identical (26 names) | local | names below |
| V1B.7 | `… -k inbox` | exit 0; 6 passed, 26 deselected (fixture line parsed; partial trailing line waits; offset persisted across restart; duplicate deduplicated; malformed line → health; shrink → re-read; `eth_call` replay at `block − 1` reverting `0x12e04c87` → `IncorrectClusterState`; all 77 selector constants = keccak(signature); Blockscout fallback; transport error keeps entry pending; OnInbox check receives the entry once) | local | — |
| V1B.7f | mutation `reconcile/inbox.py:43` `"0x12e04c87": "IncorrectClusterState()"` → `"0x12e04c88": …`, then `pytest -k inbox -v` | **red**: exit 1; `test_inbox_revert_selector_decodes_incorrect_cluster_state` FAILED (`None != 'IncorrectClusterState'`) and `test_inbox_error_constants_match_keccak` FAILED; restored with `git checkout -- reconcile/inbox.py`; porcelain = 0; re-run `-k inbox` exit 0, 6 passed | local | this entry |
| V1B.8 | background `reconcile/.venv/bin/python reconcile/tests/fake_once.py --port 9464 --hold 40` (runs `eb_reconcile.main(["once", "--serve-metrics", …])` with `FakeRpc` on the PoC trace); separately `curl -s http://127.0.0.1:9464/metrics` | curl exit 0; once exit 0; exposition contains `ssv_watch_checkpoint_block 1011`, `ssv_watch_lag_blocks 0`, `ssv_watch_open_incidents{check="m5",severity="page"} 1`, `ssv_watch_outbox_undelivered 0`, `ssv_watch_check_last_run_seconds{check="m5"} 1792581699.73` (+ last_run_block, errors_total, outbox_failing, rpc_consecutive_failures, inbox_entries) | local, fake chain | scratch `v1b8_metrics.txt` sha256 `cf05113e8a1185f9…` |
| V1B.9 | `reconcile/.venv/bin/python reconcile/inventory.py --probe --rpc "$MAINNET_RPC_URL"` | exit 0; LIVE at block **26,240,630**: API v4 **1,908** rows over 2 pages of 1,000 (`pagination.pages`), 1,026 with `migrated=false`; **25/25** sampled tuples (seed 94775) validated against `ethClusters`/`clusters` storage hashes (9 ETH, 16 legacy SSV; version from storage agrees with `migrated` in every example); 0 not validated | LIVE publicnode + api.ssv.network | scratch `v1b9.json` sha256 `729fa7aa071fa0da…` |
| V1B.9 (extra) | `reconcile/.venv/bin/python reconcile/params.py --probe --rpc https://ethereum-rpc.publicnode.com` | exit 0; block 26,240,642: network fee 3,366,600,000; threshold 42,960 / SSV 100,240; min collateral 324,862,599,900,000 wei; quorum 7,500; validators/operator 3,000; oracles 1–4 = spec §5 addresses; `min_blocks_between_updates` 0; 17 `eth_call` + 1 storage read | LIVE publicnode | scratch `params_probe.json` |
| V1B.10 | `FORK_RPC_URL=https://ethereum-rpc.publicnode.com reconcile/.venv/bin/python reconcile/scripts/fork_e2e.py` | exit 0; fork block 26,240,643; **PASS** ×5: clean (640,000/640,000) → clean after 63 dummies (1,270,000) → latent (1,270,000/640,000/630,000) → confirmed (640,000/10,000/630,000, 63 matched; one `page` routed `telegram`, recorded) → clean next-round root (resolution `info` on `telegram`); M5 incident resolved, max shortfall 630,000; live params read on the fork; layout guard ok; anvil stopped (port 8547 free after) | LIVE anvil fork, record mode | scratch `v1b10.txt` sha256 `9fecc02e4816c59d…` |
| V1B.11 | `reconcile/checks/README.md` | present: lane file set and registration line (§1), check class and rules (§2), schedules (§3), Finding + lifecycle table + routing (§4), `ctx` fields incl. `rpc.Chain`, params names, inventory and inbox entry fields (§5), events and registration (§6), config schema (§7), test helpers + patterns for V2x.1–V2x.4 (§8). The coordinator's read is the row's oracle. | — | file |
| (extra) | `MAINNET_RPC_URL=http://placeholder.invalid docker compose -f docker-compose.reconcile.yaml config -q` | exit 0 | local docker | — |

V1B.6 names (all PASSED): test_poc_trace_numbers_and_alerts, test_poc_repeated_each_round_updates_same_incident, test_partial_removal_still_confirms_with_exact_gap, test_benign_post_s_removal_after_update, test_benign_removal_between_s_and_update_is_overbilling, test_benign_add_after_consumed_root, test_benign_add_remove_without_root_update, test_update_by_non_owner_is_classified_the_same, test_two_clusters_same_owner_are_independent, test_zero_snapshot_after_emptying_is_implicit_reset, test_honest_add_before_update_still_registered_is_latent_only, test_implicit_cluster_without_updates, test_liquidated_cluster_confirmed_is_a_warning, test_legacy_ssv_cluster_then_migration, test_restart_resumes_and_keeps_incident_ids, test_range_limits_are_split, test_missing_logs_are_unknown_not_clean, test_storage_layout_mismatch_suspends, test_reorg_rewinds_and_replays, test_rpc_outage_raises_health_alert, test_delivery_retries_then_succeeds, test_audit_reports_matched_window, test_eb_to_vunits_matches_ceiling[32-10000, 33-10313, 2048-640000, 0-0, 1-313], test_vunits_to_eb_floors, test_telegram_errors_never_contain_the_token, test_pre_hotfix_ssv_removal_keeps_snapshot; plus test_migration_from_pre_framework_m5_db, test_m5_runs_on_a_migrated_database_without_repaging.

- Falsifiers: V1B.4f and V1B.7f red as expected, restored, tree clean (above).
- NOT RUN: docker image build of `reconcile/Dockerfile` (CP3 V3.4 owns build/up); `fork_e2e.py --telegram` (no TEST_* secrets used).
- Deviations and findings:
  1. **Spec vs precedent (M5 severity), not changed:** spec M5 says one round → **warn**, repeated round → **page**, empty-shares add → **info**; the pre-framework worker (and therefore the port, per A2 "same incident semantics") pages on the first confirmed round (`warn` only while liquidated), re-sends on repetition, and does not alert on empty shares alone (counted in the message). Owner: coordinator/Marco — decide whether M5 should be re-graded (a config-level change in `m5_eb_desync.py` + test expectations, outside A2).
  2. F10 → MEASURED (V1B.9): the API also returns `balance`, `effectiveBalance`, `blockNumber`, `runway`, `currentBalance`, `isLiquidated`, `id`, `version`, … ; ETH tuples use `ethBalance`, legacy SSV tuples `balance`.
  3. Two selector comments in `ISSVNetworkCore.sol` at the pin are wrong (`SameOracleAddressNotAllowed` says `0xe991f7e9`, keccak gives `0xd2850a75`; `ExceedValidatorLimit(uint64)` says `0x6df5ab76`, keccak gives `0x8ddf7de4`). `inbox.ERRORS` uses keccak values (tested). Informational.
  4. Env names: the worker config and `docker-compose.reconcile.yaml` now use `MAINNET_RPC_URL`, `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`, `DISCORD_WEBHOOK_INFO` (D2) instead of `EB_RECONCILE_RPC_URL`/`EB_TELEGRAM_*`; `.env.example` (CP1A) should list them; CP3 owns the composed file.
- Resulting head: `aaf5c258d1c2ac1162f7692725a78882ea9f33de` (records commit follows)
- Next action: status line, handoff, worker_done.

<a id="repair-1"></a>
## Repair 1 — QA attempt 1 findings (fresh implementer)

- Observed UTC: 2026-10-21T13:30Z – 13:50Z
- Task / Dispatch: `task_713a86050a2e` / `ctx_d9005050e993` (run `run_96be3e0c7302`); worker terminal `term_649570f1-74fe-4cb1-af2a-d88968f27dcc`; Claude Opus high (self-identifies as claude-opus-5-5).
- Frozen start head: `c21a51fd88e69c434781800ecb16e650437c7f1e` (`git rev-parse HEAD` confirmed; porcelain 0). Input: [QA attempt 1](../../qa/cp1b-attempt-1.md) §1, §2, Appendix A.
- Code commit: `4819c9e84f456622f8c7e78283ce2129f2080649`.
- Environment: as attempt 3 (`reconcile/.venv` resolves, Python 3.12.15; Foundry anvil on PATH). Every pytest run used a fresh `PYTHONPYCACHEPREFIX` and `-p no:cacheprovider`.

Repairs (no reproducer expectation changed):

1. **HIGH legacy pending outbox** — `db.migrate` (`_normalise_legacy_outbox`, only when the pre-framework outbox lacked `receipt`): severities `critical`/`warning` → `page`/`warn`; a sent row's receipt (stored in `delivery` by the old worker) → `receipt`, `delivery` = `telegram` for `telegram:<id>` receipts; unsent rows keep `delivery` NULL. `Worker.deliver` routes a NULL-route row by `alerts.routes[severity]` (config-aware) and records the route on send. Keys, texts, created_at and retry state are unchanged.
2. **HIGH stale parameters** — `params.Params.invalidate`/`ok`/`error` + `ParamsUnavailable`: after a failed read (revert, empty data, RPC) every attribute/item/`.get` access raises; `values` is `{}`; `block` keeps the last good read. `reload_params` invalidates, clears every check's `prepare` (so copies cached in `prepare` cannot be used), sends the `params` health (re-sent per health window), re-raises transport errors, and retries every step while `not ok`; success re-prepares and adds reason `params` to every check. `run_checks` treats `ParamsUnavailable` as a skipped run (rolled back, cursors not advanced, `errors` counted, no extra `check-error`). `step` writes `healthy: false` with the reason; `status()` gains `params` and `inbox` objects; metric `ssv_watch_input_unavailable{input}`. Digest sections no longer crash the step on a check exception. Documented in `checks/README.md` §2, §4, §5.1.
3. **MEDIUM missing inbox** — `inbox.Inbox.read` raises `InboxUnavailable` for a missing, non-regular or unreadable file (an existing empty file is valid). `poll_inbox` records `meta inbox:unavailable`, sends `inbox-unavailable` health once per outage (re-sent per health window), and clears on recovery; `step` reports `healthy: false`. Documented in `checks/README.md` §5.2, `reconcile/README.md`, and a compose comment (the deployment must create the empty file before the Monitor's first append).

| Verification ID | Exact command | Exit / result / counts / skips | Environment | Evidence |
|---|---|---|---|---|
| QA probes red on frozen head | (new file only, before any source edit) `reconcile/.venv/bin/python -m pytest -q reconcile/tests/test_qa_regressions.py -k qa1` | exit 1; **3 failed**: `route None` / `sent_at` NULL; `[42960, 42960]`; "missing configured inbox produced no health" — the QA texts | local | scratch `red_frozen.txt` sha256 `58388b385a4b8cab…` |
| QA probes green | `… pytest -q reconcile/tests/test_qa_regressions.py` | exit 0; 7 passed (3 ported probes + 4 repair tests) | local | — |
| QA Appendix A verbatim | Appendix A saved unchanged except ROOT → this worktree; `pytest -q <scratch>/test_cp1b_independent.py` | exit 0; **12 passed** (QA head: 9 passed / 3 failed) | local | — |
| V1B.1 | `reconcile/.venv/bin/python -m pytest -q reconcile/tests -rs` | exit 0; **69 passed**, 0 skipped (62 + 7 new) — on committed `4819c9e`, porcelain 0 | local | — |
| V1B.2 | `reconcile/.venv/bin/ruff check reconcile` | exit 0; All checks passed | local | — |
| V1B.3 | `… test_framework.py -k scheduler_dispatches_registered_check` | exit 0; 1 passed, 31 deselected | local | — |
| V1B.4 | `… test_framework.py -k restart_idempotent` | exit 0; 2 passed, 30 deselected | local | — |
| V1B.4f | `alerts.py` `return ":".join((check, key))` → `return key`; same selection; restore from copy | red: 1 failed / 1 passed; restored porcelain of `alerts.py` empty; 2 passed | local | scratch `falsifiers.txt` sha256 `c7c3209fe480ed32…` |
| V1B.5 | `… test_framework.py -k routing` | exit 0; 4 passed, 28 deselected | local | — |
| V1B.6 | `reconcile/.venv/bin/python -m pytest -q reconcile/tests -k m5` | exit 0; 32 passed, 37 deselected (test_worker.py untouched) | local | — |
| V1B.7 | `… test_framework.py -k inbox` | exit 0; 6 passed, 26 deselected | local | — |
| V1B.7f | `inbox.py` `"0x12e04c87"` → `"0x12e04c88"`; same selection; restore from copy | red: 2 failed / 4 passed; restored (diff vs HEAD contains no `12e04c88`); 6 passed | local | scratch `falsifiers.txt` |
| V1B.8 | `reconcile/.venv/bin/python reconcile/tests/fake_once.py --port 19464 --hold 8`; separately `curl -s http://127.0.0.1:19464/metrics` | server exit 0, curl exit 0; `ssv_watch_checkpoint_block 1011`, `lag_blocks 0`, `open_incidents{check="m5",severity="page"} 1`, `outbox_undelivered 0`, `check_last_run_seconds{check="m5"}` present; new `ssv_watch_input_unavailable{input="params"} 0`, `{input="inbox"} 0` | local, fake chain | scratch `metrics.txt` sha256 `ff98ba6d45138479…` |
| V1B.10 (re-run: step/deliver path touched) | `PATH=$HOME/.foundry/bin:$PATH FORK_RPC_URL=https://ethereum-rpc.publicnode.com reconcile/.venv/bin/python reconcile/scripts/fork_e2e.py` | exit 0; fork block **26,240,758**; **PASS ×5** (clean 640000/640000; clean 1270000; latent 1270000/640000/630000; confirmed 640000/10000/630000, 63 matched, page → telegram recorded; clean next root, resolution info → telegram); live params read on the fork; the script's anvil exited (the one remaining anvil, pid 62270 on port 18545 forking drpc, belongs to another session and was left alone) | LIVE anvil fork, record mode | scratch `fork_e2e.txt` sha256 `ed2e99fe03189bf1…` |
| (extra) | `MAINNET_RPC_URL=http://placeholder.invalid docker compose -f docker-compose.reconcile.yaml config -q` | exit 0 (compose change is a comment) | local docker | — |

- NOT RE-RUN: V1B.9 (LIVE inventory probe; `inventory.py` untouched).
- Deployment dependency (new, for CP1A/CP3): the inbox file must exist (empty) before the worker starts, otherwise `inbox-unavailable` health fires until the Monitor's first append. Not editable from this lane (`deploy/`, `docker-compose.yaml`).
- Findings are not cleared here; a fresh Codex QA decides.
- Resulting head: code `4819c9e`; records commit on top (lane head, reported in worker_done).

## Repair 2 — QA attempt 2 finding (fresh implementer)

- Observed UTC: 2026-10-21 ≈12:10Z – 12:35Z (`date -u`; the frozen head was committed 12:08Z).
- Task / Dispatch: `task_f48565e119a7` / `ctx_8f265023f59d` (run `run_96be3e0c7302`); worker terminal `term_f9349af1-a3fc-49e0-951d-e14119e8c974`; Claude Opus high (self-identifies as claude-opus-5-5).
- Frozen start head: `bea73fd20ce238baf235d2d9ab0bc47c198e6412` (`git rev-parse HEAD` confirmed; porcelain 0). Input: [QA attempt 2](../../qa/cp1b-attempt-2.md) §1, §2, Appendix A/B.
- Code commit: `7bf01b4de68f62dfda18c77a2747c801cd73ef8b`.
- Environment: `reconcile/.venv` present (Python 3.12.15); Foundry anvil at `~/.foundry/bin`. Every pytest run used `-p no:cacheprovider`; falsifier and red/green runs a fresh `PYTHONPYCACHEPREFIX`.
- Coordinator decision (Orca `ask`, answered GATE): the inbox is delivered only from canonical blocks at or below the checkpoint (contract refinement of plan §6's inbox consumer, recorded by the coordinator). Conditions honoured: fixtures carry the fake chain's real hash; QA assertions unchanged; README documents the 12-conf latency; one regression test per case.

Audit first (brief: "avoid whack-a-mole"): every piece of state the worker keeps across blocks, depth 1 and depth N, is tabulated in [`reconcile/checks/README.md` §6.1](../../../../reconcile/checks/README.md) with its handling and test. Rows found **wrong** and repaired (all same class: state derived from a deleted block):

1. **HIGH (QA attempt 2) live parameters / `prepare` copies** — `Worker._after_reorg` invalidates `Params` (access raises `ParamsUnavailable`) and clears `prepared`; `reload_params` re-reads at the new checkpoint before any check runs (reason `params` + `reorg`, `prepare` re-run); a failed re-read is the §5.1 path (consumers blocked, `healthy: false`, `params` health, retried each step).
2. **scan race** — logs from `eth_getLogs` and the block hash read afterwards could come from different forks (reorg in between, or a load-balanced RPC), storing orphan logs under a canonical checkpoint hash that no later comparison detects. `_verify_log_blocks` compares every event block within `reorg_search_depth` of the target with its canonical hash (one batch, `Rpc.block_hashes`); mismatch = `RpcError`, window re-read next step.
3. **shorter new chain** — a checkpoint block the node does not have raised `RpcError` every step until the chain regrew; `block_hash(missing_ok=True)` treats it as non-canonical and rewinds.
4. **reorg notice not durable** — the `reorg` reason and `on_reorg` lived in memory only (lost by a crash between the rewind commit and the run). Durable `meta reorg:<id>` (lowest ancestor) written in the rewind transaction, cleared in the check's committing run, replayed at start; exposed as `ctx.reorg_ancestor` (additive Ctx field) so checks can correct persisted state.
5. **NearSetEveryBlock** — `last_block` reset on a reorg.
6. **default incident id `<check>-<key>-<block>`** — reopening a key at a block number reused by the new chain raised `ValueError` (check-error, run lost); now suffixed `-2`, `-3` (check-proposed ids must still be unique).
7. **block/revision-keyed outbox sends** — a send the incident state machine decided was silently dropped by `INSERT OR IGNORE` when a reorganised block reused the key (`incidents.alerts` still counted it); `enqueue(unique=True)` suffixes `#n`. Health keys still de-duplicate.
8. **inbox (GATE)** — entries were delivered before the worker scanned their block and from orphaned blocks. New `inbox.canonical` column (additive migration by column presence; `schema_version` stays 1), `Inbox.confirm` (batched hash lookup for entries at/below the checkpoint), `pending` skips orphaned and stops at unconfirmed entries, resolution skips orphaned, the rewind resets `canonical` above the ancestor; a failed lookup holds entries and degrades health (`inbox:unverified`, `healthy: false`, `input_unavailable{inbox}` 1); status `inbox.held` / `inbox.orphaned`; metric `ssv_watch_inbox_entries{state="held"|"orphaned"}`.

Rows found correct but untested now have tests (inventory cache depth 1/N, event cursor depth N, M5 depth N, metrics checkpoint after a reorg, reorg deeper than the search). Fixture changes (no expected behaviour changed): `test_framework::test_inbox_check_receives_resolved_entries_once` writes the transaction's sealed block and its canonical hash (it wrote the pending block number with the synthetic `0xcdcd…` hash); the permanent copy of QA Appendix B's `test_partial_reload_atomic_cursors_recovery` writes `block_hash=chain.hash(chain.sealed)` (diff is that one line).

| Verification ID | Exact command | Exit / result / counts / skips | Environment | Evidence |
|---|---|---|---|---|
| QA Appendix A red on frozen head | Appendix A saved unchanged except ROOT → this worktree; `PYTHONPYCACHEPREFIX=<s>/c1 reconcile/.venv/bin/python <s>/reorg_repro.py` (before any edit) | exit 1; `direct`/`cached` `[42960, 50000, 50000, 50000, 50000]`, params_ok true, healthy true | local | — |
| QA Appendix B red on frozen head | `… pytest -q -p no:cacheprovider <s>/test_repair_edges.py` | 1 failed (`test_reorged_parameter_event_never_leaves_orphan_threshold`), 14 passed | local | — |
| QA Appendix A green | same command on `7bf01b4` | exit 0; `[42960, 50000, 42960, 42960, 42960]` both consumers | local | scratch `repro_final.out` sha256 `cbf2846ab0b0e36c…` |
| QA Appendix B `-k reorged_parameter_event` | `… pytest -q -p no:cacheprovider <s>/test_repair_edges.py -k reorged_parameter_event` | exit 0; 1 passed, 14 deselected (unmodified) | local | — |
| QA Appendix B whole file, unmodified | `… <s>/test_repair_edges.py` | 13 passed, **2 failed**: both `test_partial_reload_atomic_cursors_recovery[revert|empty]` at `seen[-1][3]==1` — the synthetic `0xcdcd…` inbox hash is now orphaned (GATE); with only that fixture line changed to the canonical hash: **15 passed** | local | — |
| QA attempt-1 Appendix A, unmodified (ROOT only) | `… <s>/test_cp1b_independent.py` | exit 0; 12 passed | local | — |
| New regressions red on frozen head | `git archive bea73fd reconcile` → scratch, + `test_reorg_state.py`, `test_qa2_regressions.py`; `pytest -q` both | **14 failed, 18 passed**: red = both QA2 reorg tests + 12 repaired rows (behavioural asserts: orphan `50000`/`30` values, `RpcError: block 1002 not found`, orphan log stored, `ValueError incident id … already exists`, escalation dropped, inbox delivered before confirmation / from the orphan block; two inbox tests fail on the new `held`/`canonical` fields). Green on old = the 4 correct-but-untested rows + 14 Appendix B tests | local | — |
| V1B.1 | `reconcile/.venv/bin/python -m pytest -q reconcile/tests -rs` | exit 0; **102 passed**, 0 skipped (69 + 16 `test_qa2_regressions` + 17 `test_reorg_state`) | local | — |
| V1B.2 | `reconcile/.venv/bin/ruff check reconcile` | exit 0; All checks passed (`ruff.toml` exempts E701/E702 in `test_qa2_regressions.py` only, to keep the QA one-liners verbatim) | local | — |
| V1B.3 | `… test_framework.py -k scheduler_dispatches_registered_check` | exit 0; 1 passed, 31 deselected | local | — |
| V1B.4 | `… test_framework.py -k restart_idempotent` | exit 0; 2 passed, 30 deselected | local | — |
| V1B.4f | `alerts.py` `return ":".join((check, key))` → `return ":".join((key,))`; same selection; restore from copy | red 1 failed / 1 passed; restored (`cmp` identical) 2 passed. A first restore run reused the mutated bytecode (same size, same second) and stayed red; rerun with fresh `PYTHONPYCACHEPREFIX` per run as recorded here | local | — |
| V1B.5 | `… test_framework.py -k routing` | exit 0; 4 passed, 28 deselected | local | — |
| V1B.6 | `reconcile/.venv/bin/python -m pytest -q reconcile/tests -k m5` | exit 0; **33** passed, 69 deselected (32 + new `test_m5_reorg_depth_n_reevaluates_on_the_new_chain`; `test_worker.py` untouched) | local | — |
| V1B.7 | `… test_framework.py -k inbox` | exit 0; 6 passed, 26 deselected | local | — |
| V1B.7f | `inbox.py` `"0x12e04c87"` → `"0x12e04c88"`; same selection; restore | red 2 failed / 4 passed; restored (`cmp` identical) 6 passed; full suite after both restores 102 passed | local | — |
| V1B.8 | `reconcile/.venv/bin/python reconcile/tests/fake_once.py --port 29465 --hold 8`; separately `curl -s http://127.0.0.1:29465/metrics` | server exit 0, curl exit 0; `checkpoint_block 1011`, `lag_blocks 0`, `open_incidents{check="m5",severity="page"} 1`, `outbox_undelivered 0`, `check_last_run_seconds{check="m5"}`, `input_unavailable` 0/0, `inbox_entries` total/pending/held/orphaned 0 | local, fake chain | scratch `metrics.txt` sha256 `1eb4112b60ff6519…` |
| V1B.10 (re-run: reorg/scan/step path changed) | `PATH=$HOME/.foundry/bin:$PATH FORK_RPC_URL=https://ethereum-rpc.publicnode.com PYTHONUNBUFFERED=1 reconcile/.venv/bin/python reconcile/scripts/fork_e2e.py` | exit 0; fork block **26,240,956**; **PASS ×5** (clean 640000/640000; clean 1270000; latent 1270000/640000/630000; confirmed 640000/10000/630000, 63 matched, page → telegram `recorded`; clean next root, resolution info → telegram `recorded`); live params read; layout guard ok; `curl --max-time 2 -s http://127.0.0.1:8547` exit 7 (anvil stopped) | LIVE anvil fork, record mode | scratch `fork_e2e.out` sha256 `2341e5c72a29791e…` |

- NOT RUN: V1B.9 (LIVE inventory probe; `inventory.py` unchanged — the worker only calls its existing `invalidate`). V1B.11 is QA's inspection (README §5, §5.1, §5.2, §6.1 changed).
- `<s>` = this session's scratchpad (`/private/tmp/claude-501/…/scratchpad`).
- Interface additions for wave 2 (additive): `ctx.reorg_ancestor`; `prepare` also runs after a reorg; inbox entries arrive only at/below `ctx.block`.
- Findings are not cleared here; a fresh Codex QA decides.
- Resulting head: code `7bf01b4`; records commit on top (lane head, reported in worker_done).
