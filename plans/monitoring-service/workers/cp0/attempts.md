# Attempts — CP0

Same entry shape as execution-log.md events, worker-side. Append only. Measured UTC (`date -u +%Y-%m-%dT%H:%MZ`).

<a id="attempt-1"></a>
## Attempt 1 — Branch, mode revert, hygiene, SSV material, templates (steps 1–5)

- Observed UTC: 2026-10-21T09:05Z–09:35Z (approximate; commit times in `git log`)
- Task / Dispatch: `task_afe9e8ca054e` / `ctx_80ac41873894`
- Frozen base: `86a17dbe1a004713798e08378bc9d59c35e86a45` (verified equal to `git rev-parse HEAD` before any mutation; `main`, 437 porcelain lines)
- Action and concrete outcome: `git switch -c plan/monitoring-service`; reverted the 428 mode-only changes with the plan's `git diff -p -R … | grep … | git apply`; added `config.zip` and `.local-dry/` to `.gitignore` (the user's own `data/**/*.sqlite*` line in the same file went into the same commit); committed the SSV material of F2 (27 files: `reconcile/` minus ignored paths, `plans/`, `docs/ssv-specs/`, `scripts/local-dry-run.sh`, both compose files; pre-add scan found no secret, no `.venv`/data/`config.json`/sqlite); extracted `deploy/config/**` (55 files) from `config/`: 24 trigger files rewritten (15 Discord → `DISCORD_WEBHOOK_<TRIGGER>` e.g. `DISCORD_WEBHOOK_DAO_FAILED`, `…_CLUSTER_SUCCESS`, `…_ORACLE_DISSENT`; 9 Telegram → `TELEGRAM_TOKEN`), network RPC → `MAINNET_RPC_URL`; 26 monitors (F3 says 24: the ledger is stale) and the oracle filter script copied byte-identical (`cmp` over all 26 monitors); `.gitkeep` kept only where the directory would otherwise be empty (monitors, filters, triggers/scripts). No secret value was printed, copied or logged: a fixed-string scan of every original secret value and the Telegram chat id over `deploy plans docs reconcile scripts .gitignore` and `git grep -F` over tracked files returned 0.

| Verification ID | Exact command | Exit / result / counts / skips | Environment | Evidence path/hash |
|---|---|---|---|---|
| V0.1 | `zsh -lic 'for t in cargo rustc uv docker anvil; do command -v $t; done'` | exit 0; all five present (`~/.cargo/bin/cargo`, `rustc`, `~/.local/bin/uv`, `~/.docker/bin/docker`, `~/.foundry/bin/anvil`) | login zsh | — |
| V0.2 | `git diff --summary \| grep -c "mode change"` and `git diff --numstat` right after the revert | `0` (was 428); numstat lists exactly the 5 F1 content files: `.dockerignore` 9/0, `.gitignore` 1/0, `Dockerfile.production` 9/5, `src/services/filter/filters/evm/filter.rs` 16/1, `tests/integration/filters/evm/filter.rs` 62/0; porcelain 437 → 14 | branch `plan/monitoring-service` | before commit 69b905d |
| V0.4 | `git check-ignore -v config.zip .local-dry` | exit 0; `.gitignore:59:config.zip`, `.gitignore:60:.local-dry/` | — | 69b905d |
| V0.6 | `jq -r '.. \| objects \| select(.type? == "plain") \| .value' deploy/config/triggers/*.json deploy/config/networks/*.json` | 0 lines (original `config/` has 25 plain values: 24 triggers + 1 RPC) | — | 325ebfb |
| V0.3 (first run) | `git grep -nE "discord\.com/api/webhooks/[0-9]+/\|[0-9]{8,10}:AA[A-Za-z0-9_-]{30,}" -- . ':!tests/integration/fixtures'` | **1 row**: `plans/monitoring-service/plan.md:153`, the V0.3f dummy token text matched the scan regex; zero real secrets (see attempt 2) | — | 325ebfb |
| V0.5 | `git ls-files <paths> \| wc -l` | reconcile 15, plans 8, docs/ssv-specs 1, deploy 55, scripts/local-dry-run.sh 1, docker-compose.local.yaml 1, docker-compose.reconcile.yaml 1 (total 82); `git ls-files reconcile/.venv reconcile/data` = 0 | — | 3b62247, 325ebfb |

- Falsifiers: see attempt 2.
- Deviations and findings: (a) `chat_id` is a plain `String` in the Monitor trigger schema (`src/models/core/trigger.rs:122`), not a `{"type":…}` secret, so it cannot be an `environment` reference. I replaced the literal Telegram chat id (kept out of this document) with the placeholder `${TELEGRAM_CHAT_ID}` in the 9 Telegram templates so no real identifier is tracked. **Owner CP1A:** `deploy/render.sh` must substitute it (use an `envsubst` allowlist of just `${TELEGRAM_CHAT_ID}`: the message bodies contain `${transaction.hash}`-style tokens that must not be touched). (b) The user's `.gitignore` line `data/**/*.sqlite*` is part of the hygiene commit. (c) Newly tracked files inherited mode 0700 (100755 in the index): see attempt 3 for the normalisation.
- Resulting head: `5cab1d0` after attempt 3 (hygiene `69b905d`, SSV material `3b62247`, templates `325ebfb`)
- Next action: V0.3 resolution (attempt 2), then steps 6–9.

<a id="attempt-2"></a>
## Attempt 2 — V0.3 semantic surprise and falsifier

- Observed UTC: 2026-10-21T09:15Z–09:30Z
- Task / Dispatch: `task_afe9e8ca054e` / `ctx_80ac41873894`
- Frozen base: `325ebfb`
- Action and concrete outcome: the V0.3 command returned 1 row because the dummy token written literally in the V0.3f row of plan.md matches the scan. The only way to reach 0 rows was to edit coordinator-owned text or weaken the row, so the step was stopped and an `ask` sent. The coordinator answered **A**: it changed the V0.3f row so the dummy token is built at run time (`printf 'TOKEN=1234567890:AA%s\n' "$(printf 'x%.0s' {1..34})"`); I committed that plan.md change (`f9b5858`) without editing it. The dummy token's literal value is deliberately not written in any tracked document.

| Verification ID | Exact command | Exit / result / counts / skips | Environment | Evidence path/hash |
|---|---|---|---|---|
| V0.3 | the V0.3 command above, after `f9b5858` | 0 rows (`git grep -q` exit 1 = no match); real-value fixed-string scan of tracked files = 0 | — | f9b5858 |
| V0.3f | append the run-time-built dummy token line to tracked `committed.toml`, rerun V0.3, `git checkout -- committed.toml`, rerun V0.3 | red: 1 row (`committed.toml:4`, token shown only as `AA<x34>`); after restore 0 rows; porcelain identical to the pre-mutation porcelain (no residue) | — | f9b5858 |

- Falsifiers: V0.3f red → restored → porcelain identical to baseline (`diff` of before/after = empty).
- Deviations and findings: plan.md V0.3f wording defect, fixed by the coordinator.
- Resulting head: `f9b5858`
- Next action: steps 6–9.

<a id="attempt-3"></a>
## Attempt 3 — Build, tests, Rust diff, spec §6, settings, mode normalisation (steps 6–9)

- Observed UTC: 2026-10-21T09:20Z–09:45Z
- Task / Dispatch: `task_afe9e8ca054e` / `ctx_80ac41873894`
- Frozen base: `f9b5858`
- Action and concrete outcome: V0.7–V0.9 measured with the uncommitted src+tests diff in the tree; coordinator answered **A with conditions** on V0.8 (below) so the diff was committed unchanged as `0ced62d` (explicit pathspec: only the two files); spec §6 decisions D3, D2, D4, D6, D5 recorded (`9962bfc`, 5 lines each with `DECIDED 2026-10-21`, no text outside §6 touched; the spec no longer equals `ssv-network-triage/extra/monitoring-service-spec.md`); `.claude/settings.json` written (`a4cb333`); modes normalised (`5cab1d0`). `.dockerignore` and `Dockerfile.production` content diffs were NOT touched or committed (exclusion; coordinator-owned decision).

| Verification ID | Exact command | Exit / result / counts / skips | Environment | Evidence path/hash |
|---|---|---|---|---|
| V0.7 | `cargo build --release --locked` | exit 0 (2m05s); `target/release/openzeppelin-monitor` exists (60 MB); only a future-incompat warning for `trie-db v0.30.0` | cargo 1.90.0 | — |
| V0.8 (exact command) | `cargo test --locked --test integration -- filters::evm::filter` | **GREEN WITH KNOWN FLAKE (pre-existing).** 18 runs of the exact command: 16 green (18 passed, 0 failed), **2 red**: run 1 exit 101 (16 passed, 2 failed) and one later run (1 failed, `test_handle_match_with_duplicate_event_signatures`) | cargo 1.90.0, parallel test threads | — |
| V0.8-serial (separately named deterministic variant) | `cargo test --locked --test integration -- filters::evm::filter --test-threads=1` | exit 0 in 6/6 runs, each 18 passed, 0 failed | same | — |
| V0.8 target test | `test_failure_monitor_only_fetches_receipts_for_monitored_transactions` | `... ok` in run 1 (red run, confirmed in its log), in all 6 serial runs (grep) and implicitly in every 18-passed run; the later red run (1 failed) did not keep its log, so this test's result there is NOT CONFIRMED | same | — |
| V0.8 extra | direct test binary `target/debug/deps/integration-1e7ccca153842b04` | `filters::evm::filter` subset 40/40 green; whole integration suite 30/30 green | same | — |
| V0.8 baseline control | pristine `main@86a17db`: `git archive 86a17db \| tar -x -C <scratch>/mainsrc`, then in that dir `CARGO_TARGET_DIR=<scratch>/mainsrc/target cargo test --locked --test integration -- filters::evm::filter`, 60 runs | **1/60 red** (`test_handle_match` FAILED; message not captured) → the flake is pre-existing and independent of the diff | same | — |
| V0.9 | `reconcile/.venv/bin/python -m pytest -q reconcile/tests` | exit 0; **30 passed** (F5 → MEASURED, README claim of 30 confirmed); re-run after the final commit: 30 passed | Python 3.12.15 | — |
| V0.11 | `grep -n "DECIDED 2026-10-21" docs/ssv-specs/monitoring-service-spec.md` | 5 lines (445–449): M8 D3, M9/M11 D2, M12 D4, M6…M16 D6, M3 D5 | — | 9962bfc |
| V0.12 | `jq .worktree .claude/settings.json`; `jq '.permissions.allow \| length' .claude/settings.json` | `{"baseRef":"head"}`; allow length 36; `git push` in allow: 0; `deny` = `["Bash(git push:*)"]` | — | a4cb333 |

- **KNOWN RED / FLAKE signature (for the log and QA):** tests `integration::filters::evm::filter::test_handle_match_with_duplicate_event_signatures`, `…::test_handle_match_with_key_collision`, `…::test_handle_match` (any one or two of them), panic `tests/integration/mocks/services.rs:20:1: MockTriggerExecutionService::new(?, ?): No matching expectation found` (a mockall static-context race between parallel tests that the diff does not touch). Rate ≈ 2/18 with the diff, 1/60 on pristine main. QA must not retry-until-green: report every run and use V0.8-serial as the deterministic row. **New finding F14 → owner CP3 (integrator), non-blocking:** fix the race (serialise those tests or use `serial_test`); not fixed in CP0, `src/` and `tests/` otherwise untouched.
- Other findings: (a) `reconcile/.venv` already existed with a valid python symlink (3.12.15 → uv-managed CPython); `uv venv reconcile/.venv --python 3.12` refused without `--clear`, so the existing venv was used unchanged and `uv pip install -r reconcile/requirements-dev.txt` reported "Checked 2 packages": F4's "dangling symlink" note is stale. (b) Mode normalisation: the 43 newly tracked non-executable files committed with 100755 were set to 100644 (chmod on disk + index); `reconcile/eb_reconcile.py`, `reconcile/scripts/fork_e2e.py`, `scripts/local-dry-run.sh` keep the executable bit because they start with a shebang. After it `git diff --summary | grep -c "mode change"` = 0.
- Falsifiers: V0.3f in attempt 2. No falsifier is defined for V0.7–V0.12 beyond the baseline control above.
- Resulting head: `5cab1d0`
- Next action: final measurements (V0.3, V0.5, V0.10), handoff block, status line, final commit.
