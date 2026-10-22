PASS WITH NOTES

# CP4 final QA, attempt 1, part A (V4.1, V4.3, V4.4)

Independent final QA of [plan.md](../plan.md) CP4, part A. **V4.2 (soak evidence) is PENDING (soak running), separate part B**: the 48 h soak runs on compose project `ms-cp3` until about 2026-10-24T22:51Z; this QA did not stop, restart, inspect or modify that stack or its volumes.

- **Candidate:** delivery candidate head `047e9ad1dbc1b267aaab43508b56e0713e937775` of `plan/monitoring-service`. Worktree `ms-qa-cp4` (fresh child, branch `mtabasco/ms-qa-cp4`); `HEAD` equals the candidate; initial porcelain empty.
- **Diff of record:** `git diff main..HEAD` (`main` = `86a17db`): 347 files, +167,211/−10.
- **QA model:** Claude Opus 5.5 (`claude-opus-5-5`), effort high (D13). Task `task_29426dc015e8`, dispatch `ctx_a73ab17ad63c`.
- **Measured:** 2026-10-22T22:50Z–23:30Z host UTC (2026-10-23 local).
- **Policy:** QA-CONTRACT.md; D15 round policy (BLOCKING only for a missed or lost page, a false clean, a false page, an unsafe action, data loss, a fail-open gate or a secret leak; everything else is a NOTE with a reason and an owner).
- **Scratch:** `$S` = `/private/tmp/claude-501/-Users-marco-orca-workspaces-ssv-network-monitor-ms-qa-cp4/21f7b727-fab7-4069-a847-f1958af4ab3b/scratchpad` (outside the repo, not committed).

**Summary.**
- No BLOCKING finding. Six NOTEs (N1–N6), each with a reproducer, a one-line reason and an owner.
- Executable evidence is green: reconcile suite **2242 passed**, ruff clean, the three deploy gates exit 0, the composed compose config validates, the Rust filter suite is the known parallel mockall flake (17/1) with the serial control 18/18 and the receipt test green in both.
- Cross-module review found no shared incident key, no config key read but undefined, no enabled check whose required setting is absent from `config.example.json`, and no colliding health kind. The D14 switches and the D17 go-live match the code and the spec amendments.
- V4.3: no secret in the candidate tree or in `git log main..HEAD`. The frozen V0.3 pattern returns 2 rows, both a synthetic test dummy (N2).
- V4.4: the 12 spec amendments match the rulings they cite. §8 matches the final QA reports on every sampled row. The §9 deferral table omits seven open notes owned by "CP4" (N4), and one CP4-owned doc caveat is still missing from the runbook.

## 1. Findings (ranked; all NOTE under D15)

Reproducers: `$S/qa/test_cp4_cross.py` and `$S/qa/test_cp4_m5layout.py` (scratch, appended verbatim in Appendix A). Run them with `PYTHONPATH=reconcile:reconcile/tests reconcile/.venv/bin/python -m pytest -q -s -p no:cacheprovider <file>` from the worktree root.

### N1 — NOTE (M5 × framework health): a failing storage-layout guard suspends M5 with one health message, never re-alerted, while the status file stays `healthy: true`

- **Reproducer:** `test_cp4_m5layout.py::test_x4_layout_suspension_is_one_message_and_status_healthy`, exit 0. The test asserts the defect. The worker runs with a wrong `layout.implementation`, then 12 steps 6 h apart (72 h of clock).
- **Expected:** a suspended page-level monitor is reported like the other "cannot evaluate" states, which re-alert every 6 h (`m3-unknown`, `m4-unknown`, `m6-unknown`, `m7-unknown`, `check-error`: `realert=True`). It also shows in `degraded()` or the status.
- **Actual:** exactly one `health:layout:4781ebc9e54f32a0` message in 72 h; status `healthy: True, error: None`.
- **Cause:** `checks/m5_eb_desync.py:127-128` calls `ctx.health("layout", …, dedup="layout:"+problems)` without `realert=True`. `Worker.degraded()` (`eb_reconcile.py:723-734`) lists params, inbox and pending reorgs, not a suspended check.
- **Why NOTE:** the guard fails closed: it suspends instead of computing on a wrong layout, and it announces it once on the Telegram route. It fires only after a proxy or module upgrade, which M15 also reports (Discord, `Upgraded`/`ModuleUpgraded`) and which the precedent plan says needs revalidation. The behaviour is preserved from the #94775 precedent (A2), not introduced by this plan.
- **What would flip it:** if the coordinator reads a single unrepeated health line as a "missed page" for M5 desyncs during the suspension, this becomes BLOCKING.
- **Owner:** later repair (one line each: `realert=True`, and a suspended check in `degraded()`).

### N2 — NOTE (V4.3 / V0.3 literal): the frozen V0.3 scan returns 2 rows, both a synthetic test dummy

- **Reproducer:** `git grep -nE "discord\.com/api/webhooks/[0-9]+/|[0-9]{8,10}:AA[A-Za-z0-9_-]{30,}" -- . ':!tests/integration/fixtures'` → exit 0, 2 rows: `reconcile/tests/test_framework.py:345` and `:349`, the Discord webhook URL with id `1` and token `SECRET`. That is a redaction test asserting that a delivery error never echoes the webhook.
- **Expected (§7 V0.3/V4.3):** 0 rows.
- **History:** introduced by `09cfe26` (CP1B). No QA since CP0 ran the global V0.3; CP1A ran it on `deploy/` only.
- **Why NOTE:** not a secret. The webhook id is `1` and the token is the literal `SECRET`. My wider scans found no real value (§3 V4.3).
- **Owner:** later repair: build the dummy at run time, as V0.3f prescribes for the token. Alternatively the coordinator amends V0.3 to exclude `reconcile/tests`.

### N3 — NOTE (D17 × digest): one check that keeps failing its silent run holds the phase at `backfill`, so the daily digest is never sent

- **Reproducer:** `test_cp4_cross.py::test_x2_one_failing_check_does_not_mute_others_but_holds_digest`, exit 0. It uses two stub checks: one always raises, the other reports a page level. Four steps with 4,000 s each against `digest_interval_s` 3,600.
- **Outcome:** the page of the healthy check is sent (`('open','page','xlevel')`). This is correct: the CP3 F1 fix holds across modules. The phase stays `backfill`, and no digest row is written in 16,000 s of clock (`housekeeping`, `eb_reconcile.py:641`: `… and not self.backfilling`).
- **Real-world trigger:** `test_cp4_cross.py::test_x1…` builds every registered check from `config.example.json` with the SSV API unreachable. M2, M3, M4, M6 and M7 never commit their silent run, while M1, M5, M10 and M13 do.
- **Consequence:** the M6 weekly digest is withheld, including the vc=0 zero-balance clusters that the event-0042 ruling moved from a page to the digest. The M3 24 h projection and the M4 surplus trend are withheld too, for as long as one check keeps failing.
- **Why NOTE:** only digests (info) are held. The failing check raises `check-error` health every 6 h. The runbook says "the daily digest starts with phase `live`".
- **Owner:** later repair (send the digest once a check is past its silent run, or once `history_block` is set).

### N4 — NOTE (V4.4 completeness): seven open "CP4"-owned §8 notes have no row in the §9 deferral table, and the CP3-QA2-N2 runbook caveat is not written

- **§9 coverage:** the table carries DT-11 (CP3-QA2-U1) and DT-12 (V3.7), and DT-15 covers only "later repair" owners. These §8 rows owned by CP4 are neither done nor deferred:
  - CP0-QA1-3 (V0.4 form). Re-measured: `git check-ignore -v config.zip .local-dry` prints only `config.zip`; with `.local-dry/` it prints both.
  - CP1A-QA5-N7 (evidence count 123 vs 125).
  - CP1B-QA4-1 (the reorg re-find obligation for future checks).
  - CP2a-E-0037 (keep `deficit > 0` exact, no tolerance; holds in code today).
  - CP3-QA2-N2 (runbook caveat).
  - E-0008 (no F14/F15 rows; re-measured: §3 has F13 then F11, no F14/F15).
  - E-0038 (packets must allow `git merge plan/monitoring-service`).
- **Missing caveat:** `deploy/README.md:93` still says "Events up to the caught-up checkpoint are history" without the caveat. The spec D17 amendment has it.
- **Disposed by this QA:** CP1A-QA5-N4 is closed by the V4.3 wider scan, which includes `discordapp.com` and found no secret (§3). DT-12 was attempted again and is still open (§3 V3.7 rows).
- **Why NOTE:** records only, no runtime effect.
- **Owner:** coordinator (CP4 closure): add the rows or close them, and write the runbook caveat.

### N5 — NOTE (record contradicts the code): plan §7 V3.3 and the §8 CP3 deliverables name `docker-compose.watch.yaml` (service `watch`, image `ssv-watch`)

- **Reproducer:** `grep -n 'docker-compose.watch' plans/monitoring-service/plan.md` → lines 253 and 333.
- **Actual:** the delivered and runbook-documented file is `docker-compose.reconcile.yaml`, service `eb-reconcile`, image `ssv-eb-reconcile:local`. No `docker-compose.watch.yaml` exists, so the V3.3 command as written fails.
- **Why NOTE:** documentation only. The runbook, the compose header and the CP3 QA used the real file.
- **Owner:** coordinator (CP4 closure).

### N6 — NOTE (record precision): §8 CP2c-QA4-N5 "`pytest.mark.m8/m12/m13` unregistered" holds only when a test module is collected without `reconcile/tests/conftest.py`

- **Reproducer:** `reconcile/tests/conftest.py:9-12` registers `m1`…`m16` (since `09cfe26`). The full suite and `pytest reconcile/tests/test_m8.py test_m12.py test_m13.py` give 0 warnings. A module imported from outside the tests rootdir (my `test_cp4_m5layout.py` importing `test_worker`) shows `PytestUnknownMarkWarning: Unknown pytest.mark.m5`.
- **Why NOTE:** hygiene wording only.
- **Owner:** later repair, with the rest of CP2c-QA4-N5.

### Checked and found sound (no finding)

- **Two checks keying the same incident:** impossible by construction. `alerts.incident_key` joins `check:key`; `run_check` rejects a finding whose `check` differs (`eb_reconcile.py:593`); every `Finding(` in `checks/*.py` passes `check=self.id` (grep).
- **Config keys read by a check but missing:** a regex over each check's source for `config[...]`/`config.get(...)` finds 0 keys outside the class `defaults`. `registry.resolve_config` rejects unknown keys. `registry.build(config.example.json)` builds all 14 registered checks (m5, m3, m4, m14, m9, m10, m11, m8, m12, m13, m1, m2, m6, m7) without error. Top-level settings read with no example key (`routes`, `realert_s`, `health_realert_s`, `digest_interval_s`, `name`) are read from `alerts` with defaults (`alerts.py:49-53`); the example sets the last three under `alerts`.
- **Enabled by default with a required setting absent:** the defaults that are `None` or empty (m5 `layout`; m8 `cssv`, `seed_holder`; m12 wallets and `fallback_tip_wei`; m13 `addresses`; m1 `our_senders`; m7 `cssv`, `unstake_history_from`) are all set in `config.example.json`. m11 `updater: null` resolves from `updater_slot` 4 and needs `views`, which is present. Missing `views`, a disabled `inventory.api_url` (M6) or a missing inbox with `require_inbox` (M9/M11) is a `SystemExit` at construction, which is fail-closed (observed while building X1).
- **Health messages that mask each other:** `Alerts.health` keys on `kind`+`dedup`, not on the check (`test_x3…` shows a second identical kind+dedup is dropped). Every registered check uses its own kind prefix (`m1-…`, `m2-…`, …, `layout`, `proof-api*` only in M5/M11), and the framework's `check-error` dedup contains the check id. The grep of every `ctx.health(` call found no collision.
- **D14 switches:** `checks.m9.dissent_enabled`, `checks.m11.revert_rate_enabled` and `checks.m11.zero_round_proof_enabled` default true in the class defaults and in `config.example.json`. That is consistent with event-0074 ("D14 fallback NOT triggered"), DT-10 and the spec amendments. Off writes one health line and resolves open episodes with "Rule disabled by config" (`m9_oracle_common.py:329-341`), with the N4 exception the spec amendment discloses.
- **D17 go-live:** `run_checks` (`eb_reconcile.py:533-567`) pins each check's silent run at `history_block` and runs it live from the next block, whatever the others do. X1 (all 14 checks, quiet fake chain, SSV API down) sent only health rows during the backfill, no finding message. The runbook text matches the code, except the N2 caveat (N4) and the digest hold (N3).
- **Routing:** `DEFAULT_ROUTES` sends page, warn and health to Telegram and info and digest to Discord (D2). The Monitor's Discord triggers use `DISCORD_WEBHOOK_INFO` for 7 info triggers (6 `*_success` plus M11 other-sender), and each of the 9 failed/dissent/safe/large-transfer triggers has its own variable (D11).
- **Secrets in transport errors:** the RPC endpoint label is the host or the variable name (`rpc.endpoint_label`). The Telegram and Discord exceptions carry no URL (`notify.py`).
- **Compose:** see §3 V4.1 compose row.

## 2. Claims I could NOT confirm

- **V3.7 tail (DT-12):** the M3 computed-zero and liquidation steps on the repaired head. Two fresh anvil forks, deliberately not on the soak's Tenderly endpoints so the soak's rate limit is untouched:
  - `eth.api.pocket.network`, fork 26,251,349: PASS through the onset info and the N = 50 warn, then `anvil_mine` failed on beacon-roots storage with HTTP 500.
  - `rpc-eth.blockmachine.io`, fork 26,251,354: PASS through the onset info, then `anvil_mine` failed with HTTP 429.
  - Neither run reached the computed-zero step. DT-12 stays open, and the M3 logic is unchanged since CP3 QA 1 passed it.
- **CP3-QA2-U1 (DT-11), fresh-database go-live on mainnet:** not measured. It needs a new compose project and a long backfill; owner scope. The fake-chain X1 above is not a substitute.
- **The soak (V4.2):** not inspected (part B).
- **Rust behaviour beyond the filter suite:** I ran only the V0.8 filter rows. There was no Rust change after CP0 (`git log main..HEAD -- src tests/integration` = `0ced62d` only, CP0).
- **The upstream docs `discord.com/api/webhooks/123-456-789` examples** (`CHANGELOG.md`, `docs/**`) are on `main` and outside the candidate diff; I did not treat them as candidate content.

## 3. Verification matrix

| Row | Command | Exit | Result |
|---|---|---|---|
| Preflight | `git rev-parse HEAD`; `git status --porcelain`; `df -h .`; `uv venv reconcile/.venv --python 3.12` + `uv pip install -r reconcile/requirements-dev.txt`; `reconcile/.venv/bin/python --version` | 0 | HEAD = `047e9ad`; porcelain empty; 291 GiB free; Python 3.12.15 |
| V4.1 suite | `reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider reconcile/tests` | 0 | **2242 passed** in 54.6 s, 0 skipped, 0 warnings (= the CP3 QA 2 count) |
| V4.1 ruff | `reconcile/.venv/bin/ruff check --no-cache reconcile` | 0 | All checks passed |
| V4.1 templates | `deploy/check-templates.sh` | 0 | `checker: OK (32 monitors, 30 triggers, 1 network)` |
| V4.1 ABI | `deploy/check-abi.sh --config-dir deploy/config` | 0 | `abi-check: OK (32 monitors …; 3356 contract_spec events/functions equal the reference layout)` |
| V4.1 M15 | `deploy/check-m15-coverage.sh` | 0 | `all M15 events present`; `no M15/M10 info event or governance-update function is routed to Telegram`; 6 extras listed |
| V4.1 compose | `docker compose -f docker-compose.yaml -f docker-compose.reconcile.yaml config` (output to scratch only, never printed with values) | 0 | Validates. `eb-reconcile`: `read_only: true`, `no-new-privileges`, tmpfs `/tmp`, named volume `watch-data:/data`, `monitor-data` subpath `inbox` at `/inbox` read-only, `config.json` bind read-only, port bound to 127.0.0.1, healthcheck on the status file, `depends_on inbox-init: service_completed_successfully`. `monitor`: `Dockerfile.production`, `monitor-data:/app/data` (not `/dev/null`), config bind read-only, secrets as pass-through names. Nothing was started. |
| V4.1 Rust (Rust files in diff: `src/services/filter/filters/evm/filter.rs`, `tests/integration/filters/evm/filter.rs`, CP0 only) | `CARGO_TARGET_DIR=$S/target cargo test --locked --test integration -- filters::evm::filter` (target cloned from `ms-qa-cp3`) | 101 | **known red** (CP0-QA1-1 = CP3-N-4 = DT-14): 17 passed / 1 failed, `test_handle_match_with_key_collision` panicked at `tests/integration/mocks/services.rs:20:1` (mockall). `test_failure_monitor_only_fetches_receipts_for_monitored_transactions … ok` |
| V4.1 Rust serial control | same `-- filters::evm::filter --test-threads=1` | 0 | 18 passed, receipt test ok |
| V4.1 cross-module scenarios (own) | `$S/qa/test_cp4_cross.py` (X1, X2, X3), `$S/qa/test_cp4_m5layout.py` (X4) | 0 | 4 passed (X2 and X4 assert N3 and N1) |
| V4.1 V3.7 tail (DT-12, attempted) | `FORK_RPC_URL=https://eth.api.pocket.network reconcile/.venv/bin/python reconcile/scripts/fork_e2e.py --scenario m3 --port 8581` | 1 | PARTIAL: PASS no alert before → info at onset 26,251,360 → warn at N = 50; then `anvil_mine` failed with HTTP 500 from the fork RPC (environment). Retry below. |
| V4.1 V3.7 retry | `FORK_RPC_URL=https://rpc-eth.blockmachine.io … --scenario m3 --port 8582` | 1 | PARTIAL: fork 26,251,354, PASS no alert before → info at onset 26,251,365; then `anvil_mine` failed with HTTP 429 (60 req limit) from the fork RPC (environment). Not retried further (owner scope; the soak's Tenderly endpoints deliberately not used). DT-12 stays open. No anvil left running. |
| V4.3 V0.3 | `git grep -nE "discord\.com/api/webhooks/[0-9]+/\|[0-9]{8,10}:AA[A-Za-z0-9_-]{30,}" -- . ':!tests/integration/fixtures'` | 0 | 2 rows, a synthetic dummy (N2); no secret |
| V4.3 wider: webhooks/tokens | `git grep -nE "discord(app)?\.com/api/webhooks/[0-9]\|api\.telegram\.org/bot[0-9]\|bot[0-9]{8,10}:\|[0-9]{8,10}:[A-Za-z0-9_-]{35}"`; the same family over `git diff main..HEAD` added lines, including `discordapp`, `chat_id` numerics, `apikey=`, `token=` | 0 | Only placeholders: `${zero}`/`placeholder`/`abcdef` built at run time in deploy tests, `example.invalid`, `123456789:ABC...` in a comment, QA dummies (`SECRETKEY123`, `USERSECRET`), `webhooks/<18 digits>/<token>` prose; upstream `123-456-789` examples on `main` |
| V4.3 wider: RPC with a key | `git grep -nE` for infura/alchemy/nodereal/quiknode/ankr/blastapi/chainstack/getblock/drpc key paths, `?apikey=`/`key=`/`token=` query strings and `user:pass@` URLs | 0/1 | No keyed RPC URL. Hits are public keyless endpoints (`eth.drpc.org`, D16 Tenderly/MEV Blocker, upstream examples), QA dummies on 127.0.0.1 and the upstream codecov badge on `main` |
| V4.3 Telegram/Discord values | `chat_id` numeric scan; `.env.example` values | — | 0 numeric chat ids; every secret variable in `.env.example` is empty (`RUST_LOG` is the only value) |
| V4.3 history content | `git log --name-only --format='' main..HEAD \| sort -u` (347 paths) filtered for `.env`, `config/`, `config.zip`, sqlite/db/wal/shm, `.local-dry`, `soak/`, `data/`, `.jsonl`, `status.json` | — | Only `.env.example` (no values) and `plans/monitoring-service/workers/cp2a/repair{1,2}/falsifiers.jsonl` (test-run records, no URL). No `.env`, `config/`, `config.zip`, sqlite/wal, `.local-dry/` or `soak/` content. Deleted in the range: only `deploy/config/triggers/scripts/.gitkeep` |
| V4.4 spec amendments | `git diff 947ac44..HEAD -- docs/ssv-specs/monitoring-service-spec.md` against the cited events | — | 12 blocks: M1 (event-0050 ✓), M4 (0031/0037/0043 ✓), M5 (0016 ✓), M6 (0042 Q1/Q2 A ✓), M8 (D3, 0058 ✓), M9 and M11 (D14 0068, switch semantics 0069 ✓, 0074 ✓), M12 (0040 ✓), M13 (D10, 0030/0026 ✓), M14 (0053 ruling, 0043/0051 ✓), D11 (0030 ✓), D17 (0076 ✓). Code agrees: m12 `projection_tip: "observed"`; m14 `window_s` chain time; m6 page needs `validatorCount > 0`; switch texts. |
| V4.4 §8 vs final QA reports | first lines and NOTE lists of `qa/cp0-attempt-1`, `cp1a-attempt-5`, `cp1b-attempt-4`, `cp2a-attempt-3`, `cp2b-attempt-5`, `cp2c-attempt-4`, `cp2d-attempt-4`, `cp2e-attempt-4`, `cp3-attempt-2` | — | Each is PASS WITH NOTES, and every NOTE ID appears in §8 with an owner. N6 is a precision issue. |
| V4.4 BLOCKING → fixed in code (sample) | CP3 QA1 F1 (`eb_reconcile.py:546-552` per-check silent pin; X2 green), CP2e QA2 empty listing (`m6_legacy_coverage.py:213`, "an empty listing is never complete"), CP2a QA2 H1 (`test_m14.py::test_reorg_above_a_surviving_hit_keeps_it_silently`), CP2d QA1 F1 (`test_m1.py::test_m1_empty_inventory_outside_a_reorg_keeps_the_unknown_incident`) | 0 | Present in code; the regressions are green inside the 2242 |
| V4.4 "closed" notes still closed (sample) | `git check-ignore -v data/inbox/failed-tx.jsonl` (CP1A-F7); compose `inbox-init` (CP1B-E-0018); check-templates S4 D11 counts (CP1A-F5); V0.6 schema gate (CP0-QA1-2) | 0 | Still closed |
| V4.4 deferral completeness | §8 owners vs §9 rows | — | N4 |
| V4.2 | soak evidence | — | **PENDING (soak running), separate part B** |

**Skipped (owner scope):** mutation testing and falsifier campaigns, the implementers' own falsifiers in bulk, `deploy/tests/selftest.sh`, `abi-mutation-sweep.sh` and `m13-fork-regression.sh`, the V3.6/V3.8/V3.9 fork rows (PASS at CP3 QA 2 with no code change since), a fresh-database mainnet go-live (DT-11), and any compose `up`/`build`.

## 4. Falsifiers

No one-token mutation campaign (owner scope). My own probes, each red/green against a stated expectation:
- **X1:** all 14 checks through the D17 backfill. Green on "only health rows", as expected.
- **X2:** the failing silent run, both legs. Green on "the healthy check pages"; asserts the digest hold (N3).
- **X3:** the shared health namespace. Asserts that an identical kind+dedup is dropped.
- **X4:** the M5 layout suspension. Asserts N1.

None of them touches a tracked file. `git status --porcelain` stayed empty except for the evidence-plane writes below.

## 5. Evidence-plane writes

- `plans/monitoring-service/qa/cp4-attempt-1.md` (this report).
- `plans/monitoring-service/plan.md`: the CP4 `**Status:**` line set to `PART A PASS WITH NOTES; V4.2 pending soak` with this head and report.
- `plans/monitoring-service/execution-log.md`: one appended event.

## 6. Completion statement (draft for the coordinator; nothing here is promoted to done)

> The SSV monitoring service on `plan/monitoring-service` @ `047e9ad` is delivered as an integration branch for Marco to push and open a PR; neither has happened. CP0, CP1A, CP1B, CP2a–e and CP3 are VERIFIED by independent QA. CP4 part A (final review, secret scan, records) is PASS WITH NOTES (qa/cp4-attempt-1.md). The service is **not complete** until V4.2 (A10, 48 h record-only soak, T48 due 2026-10-24T22:51Z) is reviewed in part B.
>
> **Remaining failures and open items (none promoted):**
> - **Known red:** parallel Rust `filters::evm::filter` mockall race, 17/1; serial control 18/18 (DT-14).
> - **V0.3 literal:** 2 dummy rows, no secret (CP4-N2).
> - **NOT RUN / PARTIAL:**
>   - V4.2 soak (DT-8, part B).
>   - V3.7 computed-zero and liquidation steps on the repaired head (DT-12; PARTIAL again at CP4: fork RPC errors).
>   - Fresh-database go-live on mainnet (DT-11).
>   - Subgraph cross-check (DT-2).
>   - M1 fork PoC (DT-13).
>   - Dev-image jq (DT-4).
> - **Accepted gaps:** D10 internal calls and same-block netting (DT-9); Rust `Success` label on some reverts (DT-1); pruned-RPC M13 fallback (DT-3).
> - **Not done, owned:**
>   - Production routes still record-only (DT-6).
>   - Kubernetes manifests (DT-5, DevOps).
>   - Live `config/` duplicate trigger names (DT-7).
>   - D14 switches kept on, available as a lever (DT-10).
>   - Worktree cleanup (DT-16).
>   - M16, unattended recovery, upstream sync (DT-17 / DEF-1, DEF-4, DEF-5).
> - **NOTE backlog:**
>   - Every §8 "later repair" row (DT-15), plus CP4-N1 (M5 layout health not re-alerted), CP4-N3 (digest held while a check fails its silent run) and CP4-N6.
>   - The CP4-owned records of CP4-N4: the runbook reorg caveat, V0.4 form, F14/F15 rows, evidence count, packet rule, the reorg-rule obligation, M4 exactness.
>   - CP4-N5 (stale compose name in §7/§8).

## 7. Repo proof

Commands run at the end, before the commit of this report:

```text
$ git status --porcelain
 M plans/monitoring-service/execution-log.md
 M plans/monitoring-service/plan.md
?? plans/monitoring-service/qa/cp4-attempt-1.md
$ git diff --name-only 047e9ad1dbc1b267aaab43508b56e0713e937775
plans/monitoring-service/execution-log.md
plans/monitoring-service/plan.md
(plus the untracked report itself: plans/monitoring-service/qa/cp4-attempt-1.md)
```

No implementation, config, template, compose or generated file is in either list. Only the report, the plan's CP4 Status line and the execution log are touched. The untracked venv and `__pycache__` are gitignored. The `ms-cp3` stack, `config/`, `config.zip` and `.env*` were not touched; no alert was sent; no mainnet transaction was made.

## Appendix A — scratch reproducers (verbatim)

### `$S/qa/test_cp4_cross.py`

```python
"""CP4 QA cross-module scenarios (scratch; not committed).

X1  every registered check, built with config.example.json's own `checks` section (network endpoints pointed at a
    dead local port), runs through the D17 silent backfill on the fake chain: which checks commit their silent run,
    does the phase reach `live`, what is sent during backfill (expected: health only, no finding message).
X2  one check that keeps failing its silent run: the others still alert live; the phase stays backfill and the daily
    digest is held (runbook: "the daily digest starts with phase live").
X3  health dedup is keyed by kind+dedup, not by check: two checks with the same kind and dedup text mask each other.
"""

import copy
import json
from pathlib import Path

import pytest
from fakechain import FakeClock
from fakechain import FakeChain
from harness import VIEWS, Capture, make_worker, outbox, stub_params

import checks as registry
from checks.base import Check, Every, Finding

EXAMPLE = json.loads((Path(registry.__file__).parent.parent / "config.example.json").read_text())
DEAD = "http://127.0.0.1:1"


def example_checks():
    c = copy.deepcopy(EXAMPLE["checks"])
    c["m11"]["proof_api"] = DEAD
    c["m12"]["blockscout_url"] = DEAD
    return c


def test_x1_all_registered_checks_through_backfill(tmp_path):
    cap = Capture()
    chain = FakeChain()
    stub_params(chain)
    (tmp_path / "inbox.jsonl").write_text("")
    chain, w = make_worker(tmp_path, only=list(registry.REGISTRY), checks=example_checks(), notifier=cap,
                           chain=chain, views=VIEWS, inbox={"path": str(tmp_path / "inbox.jsonl"), "blockscout": DEAD}, inventory={"api_url": DEAD, "api_refresh_s": 600},
                           backfill={"enabled": True, "live_within_blocks": 0, "blocks_per_step": 10})
    # the harness bootstraps after the checkpoint exists; force the backfill phase like a new database
    w.db.set_meta("phase", {"phase": "backfill", "since": 0, "evaluated": []})
    for _ in range(3):
        chain.mine()
    for _ in range(6):
        try:
            w.step()
        except Exception as exc:  # noqa: BLE001
            print("step raised", type(exc).__name__, exc)
        chain.mine()
    ph = w.phase()
    rows = outbox(w)
    print("phase", ph["phase"], "evaluated", ph.get("evaluated"), "first_live", ph.get("first_live"))
    for r in rows:
        print("OUTBOX", r["severity"], r["kind"], r["check_id"], r["text"].splitlines()[0][:140])
    runs = {cid: w.db.meta(f"run:{cid}") for cid in registry.REGISTRY}
    for cid, r in runs.items():
        print("RUN", cid, r and {k: r.get(k) for k in ("block", "findings", "errors", "last_error")})
    assert not [r for r in rows if r["kind"] not in ("health",)], "only health messages on a quiet fake chain"


class Boom(Check):
    id = "xboom"
    schedule = Every(0)

    def run(self, ctx, block):
        raise RuntimeError("silent run keeps failing")


class Level(Check):
    id = "xlevel"
    schedule = Every(0)

    def run(self, ctx, block):
        return [Finding(check=self.id, key="k", severity="page", title="level", body="b")]


def test_x2_one_failing_check_does_not_mute_others_but_holds_digest(tmp_path):
    cap = Capture()
    clock = FakeClock()
    chain, w = make_worker(tmp_path, [Boom({}, {}), Level({}, {})], notifier=cap, clock=clock,
                           backfill={"enabled": True, "live_within_blocks": 0, "blocks_per_step": 10},
                           alerts={"mode": "record", "label": "t", "digest_interval_s": 3600})
    w.db.set_meta("phase", {"phase": "backfill", "since": 0, "evaluated": []})
    for _ in range(4):
        chain.mine()
        w.step()
        clock.advance(4000)
    kinds = [(r["kind"], r["severity"], r["check_id"]) for r in outbox(w)]
    print(kinds, w.phase()["phase"])
    assert ("open", "page", "xlevel") in kinds or any(k[1] == "page" for k in kinds)
    assert w.phase()["phase"] == "backfill"
    assert not [k for k in kinds if k[0] == "digest"]


def test_x3_health_dedup_is_not_namespaced_by_check(tmp_path):
    chain, w = make_worker(tmp_path, [Level({}, {})])
    with w.db.tx():
        a = w.alerts.health("same-kind", "from check A", dedup="d", check_id="a")
        b = w.alerts.health("same-kind", "from check B", dedup="d", check_id="b")
    print("first", a, "second", b)
    assert a and not b  # documents the shared namespace (no registered check reuses a kind; see report)
```

### `$S/qa/test_cp4_m5layout.py`

```python
"""X4: M5 layout guard failure is announced once, never re-alerted, and the status file stays healthy."""
import json
from pathlib import Path

from fakechain import FakeClock, MiniSSV, keys
from test_worker import OPS, OWNER, make, tick


def test_x4_layout_suspension_is_one_message_and_status_healthy(tmp_path):
    chain, w = make(tmp_path, layout={"implementation": "0x" + "12" * 20})
    MiniSSV(chain).register(OWNER, OPS, keys(1, 1))
    clock = FakeClock()
    w.clock = w.alerts.clock = clock
    tick(w)
    for _ in range(12):  # 12 x 6 h of wall clock, a block per step
        clock.advance(6 * 3600 + 1)
        chain.mine()
        w.step()
    layout = [k for k in w.notifier.keys() if k.startswith("health:layout")]
    status = json.loads(Path(w.cfg.db_path).with_suffix(".status.json").read_text())
    print("layout messages", layout, "healthy", status["healthy"], "error", status["error"])
    assert len(layout) == 1
    assert status["healthy"] is True
```

