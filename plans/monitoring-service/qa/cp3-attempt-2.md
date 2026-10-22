PASS WITH NOTES

# CP3 (Integration, compose, fork e2e, soak start) — independent QA, attempt 2 (D13, D15)

- QA: Claude Opus 5.5 (`claude-opus-5-5`), effort high (D13). Task `task_76fb1e0cd4af`, dispatch `ctx_c0f064ab122e`. Observed 2026-10-22T22:02Z–22:55Z (host clock, UTC).
- Frozen head under test: `c3e12344407e687277af1fc0088289ea493a8f6c` (branch `mtabasco/ms-cp3`, base `0c388ec`; repair 1 = code `6667248` on QA commit `8f59453` over `91ca3a7`). Worktree `ms-qa-cp3-2` HEAD = that hash at preflight; initial `git status --porcelain` empty.
- Packet: `qa/QA-CONTRACT.md`, `qa/DEFECT-CLASSES.md` (D15), plan §2 D14–D19, §7 CP3 rows, `qa/cp3-attempt-1.md`, worker record `workers/cp3/plan.md` § Repair 1 + `attempts.md`. Owner scope note honoured: no mutation campaign, the implementer's falsifiers and its 24-seed sweep not re-run beyond the suite, no fresh-stack mainnet backfill (see §2).
- Environment: disk 294 GiB free; `reconcile/.venv` created (`uv venv --python 3.12`, Python 3.12.15, `requirements-dev.txt`, ruff 0.17.1); anvil 1.8.5; Docker stack `ms-cp3` (implementer's, worker rebuilt on the repair 21:57:09Z, 0 restarts); network only to the D16 public RPCs (anvil fork source `https://mainnet.gateway.tenderly.co`). Evidence: session scratchpad `ev/` and `qa/` (not tracked).

## 1. Findings (ranked; no BLOCKING finding)

Attempt-1 findings: **F1 (BLOCKING) fixed**, F2, F3, F4, F5 fixed — each re-measured below (§3, §4). New findings are NOTEs under D15.

### N1 — NOTE (availability, no false clean): an `eth_getLogs` answer whose `result` is not a list (`null`, `{}`) is raised without failing over

`Rpc._logs_range` checks `isinstance(logs, list)` after `call` returned, outside the failover loop: a primary answering `{"result": null}` to `eth_getLogs` raises `RpcError(transport=False, "eth_getLogs returned a non-list result")` at once; the healthy fallback is never asked (A hits 1, B hits 0). Same class as attempt-1 F2 (fixed for bodies without `result`/`error`), one level up. Reproducer (local HTTP endpoints; scratch `qa/rpc_probe3.py`, Appendix B):

```
cd reconcile && .venv/bin/python -I <scratchpad>/qa/rpc_probe3.py $PWD
A getLogs result null (B returns 1 log)   -> RpcError(transport=False): eth_getLogs returned a non-list result | A hits 1 B hits 0
A getLogs result {} (object)              -> RpcError(transport=False): eth_getLogs returned a non-list result | A hits 1 B hits 0
```

Expected (D16): the next endpoint on an answer that is not a result. Classification NOTE: the error is raised, never read as `[]`; the step fails and the `rpc` health message fires at failure 3 (re-measured live in V3.5), so a primary stuck on this answer stalls the scan with an alert while the fallbacks are healthy. Owner: CP4 / a later repair (treat a non-list `eth_getLogs` result as a failure of that endpoint inside `_post`'s loop). Related, performance only: a batch answered `200 {"message": …}` (a gateway dict instead of a list) sets `batch_ok = False` for the life of the process (sequential calls afterwards, still correct; `A batch: 200 + gateway dict -> (['0x10','0x10'], False)`).

### N2 — NOTE (D17 boundary, docs): after a reorg that crosses the caught-up checkpoint, new-chain events at blocks ≤ `history_block` are sent

Measured with `qa/test_qa2_backfill.py::test_reorg_below_history_block_while_a_check_still_fails` (Appendix A): `history_block` 1019, a second check still failing its silent run, chain reorganised from 1018; the new chain carries events at 1018, 1019, 1020 → sent `ev:1018`, `ev:1019`, `ev:1020` once each; the pre-reorg history event (1004) is not sent; reorg health row present. The events are real canonical events mined after the healthy check went live, so this is not a false page; it only blurs the runbook sentence "Events up to the caught-up checkpoint are history" for the reorg case (needs a reorg deeper than `confirmations` exactly at the catch-up). Owner: docs (CP4), no code change needed.

### Carried NOTEs (re-observed, classification unchanged)

- N-1 (V3.7): computed zero after the N2 page recorded on the page incident, no separate message — not re-observed (V3.7 computed-zero step NOT RUN, Tenderly 429); carried from attempt 1.
- N-4 / V0.8: known mockall race — not re-run (no Rust file changed since `91ca3a7`, §3 V3.2).
- N-6: lag gauge frozen during an all-endpoint outage — not re-sampled during the cuts this time (metrics read before and after only); unchanged code path, carried.

## 2. Claims I could NOT confirm

- **F-1 (Dockerfile.development's jq cannot load)**: still not reproduced, same reason as attempt 1: rebuilding that image needs `cgr.dev`/Wolfi pulls outside the network allowed to QA, and no development-built image is on the host (`docker images`: `ssv-eb-reconcile:local`, `openzeppelin/openzeppelin-monitor:latest`, `ssv-network-monitor:cp3-prod`). Confirmed instead: compose default `build.dockerfile: Dockerfile.production` (`docker compose config`), and the only `Dockerfile.development` users are upstream CI (`.github/workflows/ci.yaml:49,260`, an image build check) and upstream versioned docs (`docs/1.x/quickstart.mdx` "By default, Docker Compose uses Dockerfile.development" — now stale for this repo, upstream text); `deploy/README.md:50` says not to build from it. Nothing in the deployment path needs the development image.
- **The repaired go-live on a fresh mainnet database**: not measured by anyone. The implementer restarted `ms-cp3` on a database that was already `live` (its phase has no `history_block`), so the rebuilt stack exercises only the live path of the repaired `run_checks`; the fork rows run with `backfill.enabled: false`. A fresh-stack backfill was not run here: owner scope (time-consuming) and the network rule (the worker also calls the SSV API, the proof API and Blockscout, outside the RPC-only allowance). The go-live is covered by fakes only: the implementer's 24-seed failing-check sweep (in the suite), attempt-1 Appendix A (green), and my 6 scenarios (§4). Recommend the soak's first start (OG3) records `status.phase` from `backfill` through `live` and the outbox at go-live.
- **"33 new tests RED on `91ca3a7`"**: I measured **34** red in the three files (plus 1 in `test_m11.py`): the implementer's count omits `test_lag_alert_starts_at_the_caught_up_checkpoint_while_a_check_still_fails`, which its own table lists as a regression and which is also red there. Count discrepancy only.
- V3.4 fresh-start timings (backfill ~2.5 min, ~25 min to live): not re-measured (as attempt 1).
- The implementer's falsifiers F1–F8 and the repair's red/green campaign: not re-run as a campaign (owner scope); I re-ran the repair's regression files on `91ca3a7` (§4 R2).

## 3. Verification matrix

| V-ID | Command (exact) | Exit | Result |
|---|---|---|---|
| V3.1 | `for p in "cp1a f5e1fc4" "cp1b b07d2c6" "cp2a 2b434cf" "cp2b 48e997d" "cp2c dd9522b" "cp2d 24b1599" "cp2e 45cc774"; do a=(${=p}); git merge-base --is-ancestor ${a[2]} HEAD; done`; `git diff --name-only 0c388ec..HEAD` filtered against CP3's allowed writes | 0 ×7 | **PASS**: 7/7 ancestors; no changed file outside `reconcile/`, `docker-compose*`, `deploy/` (none under `deploy/config/`), `.gitignore`, `workers/cp3/`, `plan.md`, `execution-log.md`, `qa/cp3-attempt-1.md` |
| V3.2 (V1B.1) | `cd reconcile && .venv/bin/python -m pytest -q -p no:cacheprovider tests` | 0 | **PASS**: **2242 passed**, 0 skipped (47 s). Attempt 1 on `91ca3a7`: 2202; base `0c388ec`: 2138 → count not lower (+40 over `91ca3a7`) |
| V3.2 (V1B.2) | `cd reconcile && .venv/bin/ruff check .` | 0 | **PASS** "All checks passed!" (ruff 0.17.1) |
| V3.2 (V0.8) | `cargo test --locked --test integration -- filters::evm::filter` | — | **CARRIED, not re-run**: `git diff --name-only 91ca3a7 c3e1234 -- '*.rs' 'Cargo.*'` is empty; attempt 1 measured 17/18 parallel (known mockall N-4), serial 18/18 on the same Rust tree |
| V3.3 | `env -i PATH=$PATH HOME=$HOME docker compose -p qa-cp3b-cfg -f docker-compose.yaml -f docker-compose.reconcile.yaml config --format json` | 0 | **PASS** (compose files unchanged since `91ca3a7`; re-run): worker `read_only: true`, `watch-data:/data` named, `monitor-data` subpath `inbox` → `/inbox` read-only, healthcheck present, `depends_on inbox-init: service_completed_successfully`; every secret env value empty (names only); Monitor `build.dockerfile: Dockerfile.production`; volumes `monitor-data`, `watch-data`. Nothing created (`config` only) |
| V3.4 | rebuilt `ms-cp3` (worker started 21:57:09Z on the repair image `2c3403219b85`, 0 restarts): `docker exec ms-cp3-eb-reconcile-1 python eb_reconcile.py status` at A = 22:05:32Z and B = B = 22:37:56Z; status file | 0 / 0 | **PASS**: A: checkpoint **26,251,016**, `lag_blocks` 0, `phase: live` (`caught_up_block` 26,250,543, `first_live` [], no `history_block`: a pre-repair live phase, handled), params ok, inbox ok (`held` 0, `orphaned` 0), 1 open incident (m7 info), `undelivered` 0; status file `healthy: true` 22:06:38Z, container `(healthy)`. B (after the two V3.5 cuts): checkpoint **26,251,178** (+162 since A, advanced), `lag_blocks` 1, `phase: live`, `first_live` [], params ok, inbox ok, 1 open incident (m7 info), `undelivered` 0; status file `healthy: true`, 5 s old; container `(healthy)`, 0 restarts; `/metrics`: `ssv_watch_backfill 0`, `outbox_failing 0`, `rpc_consecutive_failures 0`, `lag_blocks 1` |
| V3.5 | on `ms-cp3` (stated: no second stack), scratch `qa/v35.sh`: two cuts `docker network disconnect ms-cp3_default ms-cp3-eb-reconcile-1` of 5 min, each restored with `docker network connect --alias eb-reconcile …`, 4 min apart; status file sampled every 30 s | 0 | **PASS** (F5 fixed, measured live). Both cuts fall in the same 6 h health window as each other **and** as the two cuts of attempt 1 (21:08Z, 21:35Z; key `health:rpc:aa1dbfe8…:82995`). Cut 1 22:06:07Z → failures 1–5 at 22:06:52, 22:07:52, **22:08:52 (3: row `health:rpc:cf7d679809c60330`)**, 22:09:52, 22:10:53; status file `healthy: false` from 22:07:08Z (error names labels only: `MAINNET_RPC_URL`, `gateway.tenderly.co`, `rpc.mevblocker.io`), container `unhealthy` from 22:09:38Z; restored 22:11:09Z → `rpc-recovered` ("after 5 consecutive failures; checkpoint 26251048"), status file `healthy: true` 22:13:40Z. Cut 2 22:15:10Z → failures 1–5 at 22:15:31 … **22:17:32 (3: row `health:rpc:40cc9cd9b99f51ea`)** … 22:19:32; restored 22:20:12Z → `rpc-recovered` (checkpoint 26251091), `healthy: true` 22:20:42Z, container `(healthy)` 22:21:42Z. Record file 6 → 10 rows (2 `rpc` + 2 `rpc-recovered`, one pair per outage). Network re-attached with the same DNS names (`ms-cp3-eb-reconcile-1`, `eb-reconcile`); checkpoint advanced after each restore (26,251,021 → 26,251,048; 26,251,064 → 26,251,100) |
| V3.6 | `FORK_RPC_URL=https://mainnet.gateway.tenderly.co reconcile/.venv/bin/python reconcile/scripts/fork_e2e.py --scenario m5 --port 8571` | 0 | **PASS**, fork 26,251,025: clean → clean → latent 630,000 (silent) → confirmed (63 matched) → clean; escalate page at confirmed, then resolution; layout guard `[]` |
| V3.7 | `… --scenario m3 --port 8573` (run 1, 22:04:34Z); retries `--port 8575` (22:13:11Z), `--port 8576` (22:35:35Z) | 1 / 1 / not started | **PARTIAL PASS, remainder NOT RUN (environment)**. Run 1, fork 26,251,025: PASS no alert before the onset → `info` at onset (26,251,036) → `warn` at N = 50 → `page` at N2 = 7,200 ("M3 cluster liquidatable for 7200 blocks (≥ N2 = 7200)"); then `mine(5_000)` for the computed-zero step failed: `RuntimeError: anvil_mine: {'code': -32603, 'message': 'failed to apply beacon root contract call … failed to get storage for 0x000F3df6D732807Ef1319fB7B8bB8522d0Beac02 at 280: Max retries exceeded HTTP error 429 … "rate limit exceeded"'}` (anvil's upstream, Tenderly, rate-limited while four forks and `ms-cp3` ran). Retries: anvil never answered within the script's 120 s start loop (`urllib.error.URLError: <urlopen error [Errno 61] Connection refused>`); `curl` `eth_blockNumber` to `https://mainnet.gateway.tenderly.co` and `/public/mainnet` returned **HTTP 429** `rate limit exceeded` on every poll from 22:15Z to 22:48:11Z (60 polls), so the third retry was not started. NOT RUN: computed zero (N-1) and the liquidation resolution. These steps exercise M3 logic the repair did not change (`git diff 91ca3a7 c3e1234 -- reconcile/checks/` touches `m11_updater_liveness.py` and `README.md` only); attempt 1 ran them PASS on `91ca3a7`; the repair's shared path (`run_checks`, `Rpc`) is exercised by the m3 steps that ran and by V3.6/V3.8/V3.9 |
| V3.8 | `… --scenario m9 --port 8574` | 0 | **PASS**, fork 26,251,025: `dissent:4` warn (round 1), update, page on the 3-round streak; no failed-round page inside 30 min; failed-round page after 30 min chain time (round 26,251,047) |
| V3.9 | `… --scenario m12 --port 8572` | 0 | **PASS**, fork 26,251,025: above 0.15 ETH no page; 0.1100 ETH → page `0x16a5…:balance`; top-up → resolved |
| V3.10 | review of `deploy/README.md` (diff `8f59453..c3e1234`: "Backfill phase (D17)", Kubernetes "Probes"), `reconcile/README.md`, `checks/README.md` run-reason rows | — | **PASS**: the backfill section now states per-check alerting, the pinned retry, the archive-state requirement of a late retry and that health is always sent (attempt-1 F1 doc gap closed); K8s: readiness = healthcheck, liveness = staleness only (`time.time() - s['at'] < 600`) or none (F4 closed; checked live: during the V3.5 cut the status file was 9 s old with `healthy: false`, so that liveness test stays green while readiness goes red). N2 is a docs nuance |

Why the fork rows were re-run: the repair touched their code path — `fork_e2e.py` drives `Worker.run_checks` (loop restructured, `run_check` silent rule and inbox `takewhile`) and `Rpc._post`/`_post_one` (F2). All four started together at 22:04:34Z on fresh anvil forks of a recent (non-archive) block.

## 4. Re-runs, targeted attacks (DEFECT-CLASSES) and falsifiers

| # | Attack / re-run | Result |
|---|---|---|
| R1 | attempt-1 reproducers on the head: Appendix A (5 backfill scenarios), Appendix C (F5), Appendix B (`rpc_probe.py`, F2) — scratch copies extracted verbatim from `qa/cp3-attempt-1.md` | **green**: 6 passed; F1 `ev:1030` now `notify 1, alerts 1` (sent); F5 two `rpc` rows (`health:rpc:5eef…`, `health:rpc:5120…`) and two `rpc-recovered`; F2 probe: 502 gateway JSON / 403 JSON / 200 without result+error → answer from B (B hits 2), `get_logs` `[]` from B; controls unchanged; all-down message carries no key |
| R1b | same reproducers on `91ca3a7` (`git archive` into scratch) | **red as in attempt 1**: 4 failed / 2 passed (the 3 F1 reproducers + F5; the 2 green ones were green there too), probe: 3 cases `RpcError(transport=False) … no result`, B hits 0 |
| R2 | repair regressions on `91ca3a7`: head's `test_backfill.py test_worker.py test_rpc_failover.py test_m11.py` copied over a `git archive 91ca3a7` tree | **35 failed / 213 passed**: `test_backfill.py` 28 (3 ported F1 reproducers, the lag-alert test, 24/24 failing-check sweep seeds), `test_worker.py` 1 (F5), `test_rpc_failover.py` 5 (F2), `test_m11.py` 1 (F3 text). All green on the head (suite) |
| 2a | own: event exactly at `history_block` vs the next block, a second check failing (`test_boundary_event_at_history_block_silent_next_block_paged`) | PASS: history 1019, events 1005/1019/1020 → only `ev:1020` sent |
| 2b | own: a state (level) condition turning on after the catch-up while another check fails (`test_level_turning_on_after_catch_up_pages_while_another_check_fails`) | PASS: `level` paged by the healthy check while the phase is still `backfill` |
| 2c | own: the failing check owns the level; its silent retry pinned at `history_block` 1015 while the chain moved to 1019 (`test_failing_check_pinned_retry_level_states`) | PASS: runs `(1015, backfill+force)`, `(1019, live+force)` in the same step, then `(1020, interval)`; off-at-history/on-now → 1 page; on-at-history/off-now → nothing sent, no open incident; on/on → 1 page |
| 2d | own: silent run commits, the forced live run fails (transport `RpcError`), restart, fails again, then succeeds (`test_live_run_fails_after_silent_commit_then_restart_keeps_first_live`) | PASS: `first_live` kept across the failures and the restart; the live event `ev:1020` sent once; `first_live` [] after |
| 2e | own: every check fails at the caught-up step, two restarts with live events mined meanwhile (`test_all_checks_fail_reload_params_at_catch_up_then_restart`) | PASS: `history_block` 1024 durable and unchanged; history events 1002/1024 never sent; live 1025/1027 sent once each, no duplicates |
| 2f | own: reorg crossing the caught-up checkpoint (`test_reorg_below_history_block_while_a_check_still_fails`) | **N2** (measured, not a false page) |
| 2g | "caught up" flap: `history_block` is written once (`_history_block`, guarded `is None`), phase never returns to `backfill`; lag alert off only while `history_block` is unset | no flap path; the 24-seed sweep (suite) asserts exactly-once sends after every step |
| 1a | F2 fix, local HTTP endpoints (`rpc_probe.py`, `rpc_probe3.py`) | non-JSON-RPC answers fail over (502/403 gateway JSON, 200 without result/error, 200 `[]`, 200 JSON string); 404 + JSON-RPC error → B; deterministic revert → no failover (A only); all endpoints failing → `RpcError(transport=True)` "all 2 RPC endpoints failed", never `[]` |
| 1b | `eth_getLogs` `result: null` / `{}` | **N1** |
| 1c | secrets: endpoints `http://user:USERSECRET@127.0.0.1:1/v3/PATHSECRET?apikey=QSECRET` and env `MY_RPC=http://127.0.0.1:2/ENVSECRET` | labels `['127.0.0.1', 'MY_RPC']`; error and all 5 DEBUG log lines: 0 occurrences of `SECRET`; running stack: worker logs name `MAINNET_RPC_URL`, `gateway.tenderly.co`, `rpc.mevblocker.io` only; the V3.5 `rpc` rows carry labels only |
| 1d | chunking ≤ 5,000 / halving on range errors | `get_logs`/`_logs_range` unchanged by the repair (diff); attempt-1 probe result (every log returned, largest window 5,000) carried |
| 3 | registry independence (frozen-test patch removal) | not changed by the repair; attempt-1 poison run carried; suite count not lower (2242) |
| 4 | promoted helpers / lanes | lane suites inside the 2242 green; fork rows V3.6–V3.9 green on the repaired `run_checks` and `Rpc` |
| 5 | M11 (CP2b re-check of the CP3 change): `tests/test_m11.py tests/test_m9_m10_m11_sweep.py tests/test_m9.py tests/test_m10.py` (+ backfill/failover/worker files, one run) | 476 passed; DEFERRED text now `retried every {full_state_retry_s} s` for the weekly comparison, `every run` for the scoped re-comparison (`m11_updater_liveness.py:580`); retry gate unchanged (`:573`, attempt-1 5b scenarios green there) |
| 6 | compose / dev image | V3.3; §2 F-1 |

No source file was mutated. My scenarios 2a–2f are new tests in scratch, green on the head; on `91ca3a7` all 6 fail, but mostly because they read the repair's `phase.history_block` key (structural, not a semantic falsification); the semantic red/green evidence is R1b/R2.

## 5. Evidence-plane writes

- `plans/monitoring-service/qa/cp3-attempt-2.md` (this report).
- `plans/monitoring-service/plan.md`: CP3 `**Status:**` → `VERIFIED` (PASS WITH NOTES) on `c3e1234` with this report.
- `plans/monitoring-service/execution-log.md`: one appended event.
- Untracked: `reconcile/.venv` (ignored). Scratch (session scratchpad): `ev/*` (suite log, fork logs, status JSON, compose config, V3.5 log, probe outputs), `qa/test_qa_backfill.py`, `qa/test_qa_rpc_health.py`, `qa/rpc_probe.py` (attempt-1 appendices), `qa/test_qa2_backfill.py`, `qa/rpc_probe3.py`, `qa/v35.sh`, `base/` and `base-newtests/` (`git archive 91ca3a7 reconcile`).

## 6. Repo proof

Before commit (`git status --porcelain`):

```
 M plans/monitoring-service/execution-log.md
 M plans/monitoring-service/plan.md
?? plans/monitoring-service/qa/cp3-attempt-2.md
```

`git diff --name-only HEAD` (against the frozen head `c3e1234`): `plans/monitoring-service/execution-log.md`, `plans/monitoring-service/plan.md`. No implementation file (reconcile source, tests, compose, deploy, Dockerfile, config) appears in either; `git diff --name-only 0c388ec..c3e1234` is the implementer's set (V3.1), unchanged by QA. `reconcile/.venv` is ignored. Docker: `ms-cp3` left running (`eb-reconcile` healthy, 0 restarts, network `ms-cp3_default` re-attached with DNS names `ms-cp3-eb-reconcile-1`, `eb-reconcile`; Monitor untouched); `qa-cp3b-cfg` was `config` only (nothing created); no anvil process left (`pgrep anvil` empty).

## Appendix A — own go-live scenarios (scratch `qa/test_qa2_backfill.py`; run from `reconcile/`: `.venv/bin/python -m pytest -q -p no:cacheprovider -p no:logging --rootdir=. -o pythonpath="tests ." <file> -s`)

```python
"""QA CP3 attempt 2: adversarial go-live scenarios outside the implementer's sweep (scratch, not part of the repo).
Run from reconcile/: .venv/bin/python -m pytest -q -p no:cacheprovider -o pythonpath="tests ." <this file> -s"""
from __future__ import annotations

from fakechain import FakeChain, FakeClock
from harness import make_worker, outbox
from test_backfill import LEVEL_SLOT, Watch, fee_event, history, sent

from checks.base import Check, Every
from rpc import RpcError

BF = {"enabled": True, "live_within_blocks": 0, "blocks_per_step": 10}


class Flaky(Check):
    id = "flaky"

    def __init__(self, fail=True):
        super().__init__({}, {})
        self.schedule = (Every(60),)
        self.fail = fail

    def run(self, ctx, block):
        if self.fail:
            raise RuntimeError("broken input")
        return []


class FlakyWatch(Watch):
    """Watch that fails while `fail` (silent retries pinned at history_block); records run blocks/reasons."""
    id = "watch"


def build(tmp_path, chain, start, checks, clock=None):
    _, w = make_worker(tmp_path, checks, chain=chain, clock=clock or FakeClock(), start_block=start,
                       activation_block=start, max_log_range=4, backfill=BF)
    return w


def keys_sent(w):
    return [r[0] for r in w.db.conn.execute(
        "SELECT i.key FROM outbox o JOIN incidents i ON i.id=o.incident_id WHERE o.kind='open' ORDER BY o.rowid")]


def until_history(w, limit=40):
    for _ in range(limit):
        w.step()
        if w.phase().get("history_block") is not None:
            return w.phase()["history_block"]
    raise AssertionError("never caught up")


def test_boundary_event_at_history_block_silent_next_block_paged(tmp_path):
    """An event in the caught-up block itself is history; one in the next block is live (paged once), while
    another check still fails its silent run."""
    chain = FakeChain()
    start = chain.number
    history(chain, n=19, events_at={5})
    fee_event(chain)  # in the last history block
    chain.mine()
    w = build(tmp_path, chain, start, [Watch(), Flaky()])
    h = until_history(w)
    ev_blocks = sorted({int(lg["blockNumber"], 16) for lg in chain.logs})
    assert h in ev_blocks, (h, ev_blocks)  # the boundary event sits exactly at history_block
    fee_event(chain)
    chain.mine()
    for _ in range(3):
        w.clock.advance(60)
        w.step()
    assert w.backfilling
    print("history_block", h, "events", ev_blocks + [h + 1], "sent", keys_sent(w))
    assert keys_sent(w) == [f"ev:{h + 1}"]


def test_level_turning_on_after_catch_up_pages_while_another_check_fails(tmp_path):
    """A state condition (not an event) that turns on after the catch-up is paged by the healthy check at its
    next run although another check keeps the phase at backfill."""
    chain = FakeChain()
    start = chain.number
    history(chain, n=20)
    w = build(tmp_path, chain, start, [Watch(), Flaky()])
    until_history(w)
    w.clock.advance(60)
    w.step()  # the healthy check's forced live run (level off)
    chain.write(LEVEL_SLOT, 1)
    chain.mine()
    w.clock.advance(3600)
    w.step()
    assert w.backfilling
    print("sent", keys_sent(w))
    assert keys_sent(w) == ["level"]


def test_failing_check_pinned_retry_level_states(tmp_path):
    """The failing check is the one with the level. (a) off at history, on when it recovers: paged once by the
    live run. (b) on at history, off when it recovers: nothing sent, no open incident. (c) on at both: once."""
    out = {}
    for case, (at_h, at_now) in {"a": (0, 1), "b": (1, 0), "c": (1, 1)}.items():
        chain = FakeChain()
        start = chain.number
        history(chain, n=15)
        if at_h:
            chain.write(LEVEL_SLOT, 1)
        chain.mine()
        p = tmp_path / case
        p.mkdir()
        watch = Watch(fail=True)
        w = build(p, chain, start, [watch])
        h = until_history(w)
        for _ in range(3):
            chain.mine()
            w.clock.advance(60)
            w.step()
        chain.write(LEVEL_SLOT, at_now)
        chain.mine()
        watch.fail = False
        w.clock.advance(60)
        w.step()
        assert watch.runs[0][0] == h and "backfill" in watch.runs[0][1], watch.runs  # pinned
        assert watch.runs[1][0] > h and "live" in watch.runs[1][1], watch.runs  # same step, live
        for _ in range(2):
            chain.mine()
            w.clock.advance(3600)
            w.step()
        open_ = [dict(r) for r in w.db.conn.execute("SELECT key, notify, state FROM incidents WHERE state='open'")]
        out[case] = (keys_sent(w), open_)
        print(case, "history", h, "runs", [(b, sorted(r)) for b, r in watch.runs[:3]], "sent", keys_sent(w), open_)
    assert out["a"][0] == ["level"]
    assert out["b"][0] == [] and out["b"][1] == []
    assert out["c"][0] == ["level"]


def test_live_run_fails_after_silent_commit_then_restart_keeps_first_live(tmp_path):
    """The silent run commits, the forced live run of the same check fails (step N), the process restarts:
    the check is still in first_live, its next run is a forced live run, the live event is paged once."""
    chain = FakeChain()
    start = chain.number
    history(chain, n=20, events_at={3})

    class LiveFails(Watch):
        def run(self, ctx, block):
            if "live" in ctx.reasons and self.fail_live:
                self.fail_live -= 1
                raise RpcError("transport error: all 3 RPC endpoints failed", transport=True)
            return super().run(ctx, block)

    c = LiveFails()
    c.fail_live = 2
    w = build(tmp_path, chain, start, [c])
    h = until_history(w)
    assert not w.backfilling  # single check: live at once
    fee_event(chain)
    chain.mine()
    w.clock.advance(60)
    w.step()  # live run fails
    assert "watch" in w.phase()["first_live"]
    w.db.conn.close()
    c2 = LiveFails()
    c2.fail_live = 1
    w = build(tmp_path, chain, start, [c2])  # restart; fails once more
    chain.mine()
    w.clock.advance(60)
    w.step()
    assert "watch" in w.phase()["first_live"]
    chain.mine()
    w.clock.advance(60)
    w.step()
    for _ in range(2):
        chain.mine()
        w.clock.advance(3600)
        w.step()
    print("history", h, "sent", keys_sent(w), "first_live", w.phase()["first_live"])
    assert keys_sent(w) == [f"ev:{h + 1}"] and w.phase()["first_live"] == []


def test_reorg_below_history_block_while_a_check_still_fails(tmp_path):
    """Reorg from history_block - 1 while another check fails its silent run: the new chain has an event at a block
    <= history_block and one above. Measured, then classified (D17: blocks <= history_block are history)."""
    chain = FakeChain()
    start = chain.number
    history(chain, n=20, events_at={4})
    flaky = Flaky()
    w = build(tmp_path, chain, start, [Watch(), flaky])
    h = until_history(w)
    for _ in range(2):
        chain.mine()
        w.clock.advance(60)
        w.step()
    before = keys_sent(w)
    chain.reorg(h - 1)  # blocks h-1.. replaced
    # new chain: event at h-1 (history range), then 3 more blocks with an event at h+1
    assert chain.number == h - 1  # pending block
    fee_event(chain)
    chain.mine()  # h-1 sealed
    fee_event(chain)
    chain.mine()  # h sealed (the caught-up block number, new chain)
    fee_event(chain)
    chain.mine()  # h+1
    chain.mine(3)
    for _ in range(3):
        w.clock.advance(60)
        w.step()
    flaky.fail = False
    for _ in range(3):
        chain.mine()
        w.clock.advance(3600)
        w.step()
    ev_blocks = sorted({int(lg["blockNumber"], 16) for lg in chain.logs})
    print("history", h, "before", before, "events on new chain", ev_blocks, "sent", keys_sent(w),
          "health", [r["text"].split("\n")[-1][:80] for r in outbox(w, "kind='health'")])
    assert f"ev:{h + 1}" in keys_sent(w)
    # measured: the new chain's events at h-1 and h (block numbers <= history_block, mined after the go-live of the
    # healthy check) are sent once each; no pre-reorg history event (h-15) is sent
    assert sorted(keys_sent(w)) == [f"ev:{h - 1}", f"ev:{h}", f"ev:{h + 1}"]


def test_all_checks_fail_reload_params_at_catch_up_then_restart(tmp_path):
    """Every check fails (transport RpcError) at the caught-up step and the process restarts twice before they
    recover: history_block is durable and unchanged; history events silent; live events once."""
    chain = FakeChain()
    start = chain.number
    history(chain, n=25, events_at={2, 24})
    clock = FakeClock()
    state = {"fail": True}

    class Down(Watch):
        def run(self, ctx, block):
            if state["fail"]:
                raise RpcError("transport error: all 3 RPC endpoints failed", transport=True)
            return super().run(ctx, block)

    w = build(tmp_path, chain, start, [Down()], clock=clock)
    h = until_history(w)
    for r in range(2):
        fee_event(chain)
        chain.mine(2)
        w.db.conn.close()
        w = build(tmp_path, chain, start, [Down()], clock=clock)
        clock.advance(60)
        w.step()
        assert w.phase()["history_block"] == h and w.backfilling
    state["fail"] = False
    for _ in range(3):
        chain.mine()
        clock.advance(3600)
        w.step()
    ev_blocks = sorted({int(lg["blockNumber"], 16) for lg in chain.logs})
    print("history", h, "events", ev_blocks, "sent", keys_sent(w))
    assert sorted(keys_sent(w)) == sorted(f"ev:{b}" for b in ev_blocks if b > h)
    assert len(keys_sent(w)) == len(set(keys_sent(w)))
```

## Appendix B — failover probe (scratch `qa/rpc_probe3.py`; `.venv/bin/python -I rpc_probe3.py <repo>/reconcile`)

```python
"""QA CP3 attempt 2: more failover cases on real local HTTP endpoints. Usage: python -I rpc_probe3.py <reconcile dir>"""
import io, json, logging, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
sys.path.insert(0, sys.argv[1])
buf = io.StringIO()
logging.basicConfig(level=logging.DEBUG, stream=buf, format="%(levelname)s %(message)s")
from rpc import Rpc, RpcError, resolve_endpoints

def server(behaviour, path="/v3/SECRETKEY123"):
    hits = []
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            hits.append(body)
            code, out = behaviour(body)
            data = out if isinstance(out, bytes) else json.dumps(out).encode()
            self.send_response(code); self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data))); self.end_headers(); self.wfile.write(data)
    s = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{s.server_address[1]}{path}", hits, s.server_address[1]

def ok(body):
    if isinstance(body, list):
        return 200, [{"jsonrpc": "2.0", "id": b["id"], "result": "0x10"} for b in body]
    if body["method"] == "eth_getLogs":
        return 200, {"jsonrpc": "2.0", "id": body["id"], "result": [{"blockNumber": "0x5"}]}
    return 200, {"jsonrpc": "2.0", "id": body["id"], "result": "0x10"}

def run(name, beh, fn):
    a, ha, _ = server(beh)
    b, hb, _ = server(ok)
    r = Rpc([a, b], timeout=5, retries=1, primary_retry_s=300)
    try:
        out = f"-> {fn(r)!r}"
    except RpcError as e:
        out = f"-> RpcError(transport={e.transport}): {str(e)[:110]}"
    print(f"{name:52s} {out[:150]} | A hits {len(ha)} B hits {len(hb)}")

logs = lambda r: r.get_logs("0x" + "11" * 20, 0, 4999, 5000, ["0x" + "22" * 32])
run("A getLogs result null (B returns 1 log)", lambda b: (200, {"jsonrpc": "2.0", "id": b["id"], "result": None}) if not isinstance(b, list) else ok(b), logs)
run("A getLogs result {} (object)", lambda b: (200, {"jsonrpc": "2.0", "id": b["id"], "result": {}}), logs)
run("A 200 + body [] for a single call", lambda b: (200, []), lambda r: r.call("eth_blockNumber", []))
run("A 200 + JSON string body", lambda b: (200, "ok"), lambda r: r.call("eth_blockNumber", []))
run("A batch: 200 + gateway dict", lambda b: (200, {"message": "rate limited"}) if isinstance(b, list) else ok(b),
    lambda r: (r.batch([("eth_blockNumber", []), ("eth_chainId", [])]), r.batch_ok))
run("A batch: 502 + gateway dict", lambda b: (502, {"message": "Bad Gateway"}), lambda r: r.batch([("eth_blockNumber", []), ("eth_chainId", [])]))
run("A 404 + JSON-RPC error body (method not found)", lambda b: (404, {"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "not found"}}), lambda r: r.call("eth_blockNumber", []))
run("A 200 revert (deterministic) -> no failover", lambda b: (200, {"jsonrpc": "2.0", "id": b["id"], "error": {"code": 3, "message": "execution reverted", "data": "0x08c379a0"}}), lambda r: r.call("eth_call", [{}, "latest"]))
# all failing on getLogs: raise, never []
for n, beh in {"all 502 gateway": lambda b: (502, {"message": "Bad Gateway"}), "all 200 no result": lambda b: (200, {"jsonrpc": "2.0", "id": 1})}.items():
    a, _, _ = server(beh); c, _, _ = server(beh)
    r = Rpc([a, c], timeout=5, retries=1)
    try:
        print(f"{n:52s} -> {logs(r)!r}")
    except RpcError as e:
        print(f"{n:52s} -> RpcError(transport={e.transport}) {str(e)[:120]}")
# secrets: userinfo + path + query keys in URLs
eps = resolve_endpoints(["http://user:USERSECRET@127.0.0.1:1/v3/PATHSECRET?apikey=QSECRET", "MY_RPC"], environ={"MY_RPC": "http://127.0.0.1:2/ENVSECRET"})
r = Rpc(eps, timeout=2, retries=0)
try:
    r.call("eth_blockNumber", [])
except RpcError as e:
    msg = str(e)
    print("labels:", [l for l, _ in eps], "| error:", msg, "| leak:", any(s in msg for s in ("SECRET",)))
print("log lines with SECRET:", sum("SECRET" in l for l in buf.getvalue().splitlines()), "of", len(buf.getvalue().splitlines()))
```
