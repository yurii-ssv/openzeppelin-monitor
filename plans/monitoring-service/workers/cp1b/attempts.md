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
