FAIL

# CP3 (Integration, compose, fork e2e, soak start) — independent QA, attempt 1 (D13, D15)

- QA: Claude Opus 5.5 (`claude-opus-5-5`), effort high (D13). Task `task_984b1183ea51`, dispatch `ctx_ca931025aa41`. Observed 2026-10-22T21:17Z–21:50Z (host clock, UTC).
- Frozen head under test: `91ca3a737957e885cc886c82547818014433934c` (branch `mtabasco/ms-cp3`, frozen base `0c388ec`); worktree `ms-qa-cp3` HEAD = that hash at preflight; initial `git status --porcelain` empty.
- Packet: `qa/QA-CONTRACT.md`, `qa/DEFECT-CLASSES.md` (D15 BLOCKING definition), plan §2 D14–D19, §7 CP3 rows, worker record `workers/cp3/plan.md` + `attempts.md`. Owner scope note honoured: no mutation campaign, the implementer's falsifiers F1–F8 not re-run, no second fresh stack backfill.
- Environment: disk 303 GiB free; `reconcile/.venv` created (`uv venv --python 3.12`, Python 3.12.15, `requirements-dev.txt`); anvil 1.8.5; Docker stack `ms-cp3` (implementer's) running; network only to the D16 public RPCs (anvil fork source `https://mainnet.gateway.tenderly.co`). Evidence files: session scratchpad `ev/` and `qa/` (not tracked).

## 1. Findings (ranked)

### F1 — BLOCKING (lost page): live blocks processed after the catch-up stay silent while any check has not yet committed its silent run; one transient RPC error suffices

D17: "alerting starts at the first live block after catch-up". The implementation starts alerting only when **every** check has committed its silent run (`run_checks` → `_go_live`). Until then `run_check` silences every finding (`silent = ph["phase"] == "backfill"`, `eb_reconcile.py:570`), including the findings of checks that already finished their silent evaluation and now run on live blocks (their schedules keep firing; the scan keeps the checkpoint at the head). An event-derived finding produced in that window is opened with `notify=0, alerts=0`; the first live forced run does not re-derive it (its event cursor has passed it), and `realert()` skips `notify=0` incidents, so it is never sent. The window lasts as long as one check keeps failing its silent run: one step for a single `RpcError` (public-RPC 429s were measured during the CP3 catch-up, F-5), unbounded for a persistent check error (only a `check-error` health message says anything, and it does not say alerting is suppressed for every check). This happens on every fresh deployment (new database: the soak and production start).

Reproducers (scratch test file, run from `reconcile/`; code in Appendix A):

```bash
cd reconcile && .venv/bin/python -m pytest -q -p no:cacheprovider -p no:logging --rootdir=. -o pythonpath="tests ." \
  <session scratchpad>/qa/test_qa_backfill.py -s
```

| Test | Expected (D17) | Actual on `91ca3a7` |
|---|---|---|
| `test_one_transient_rpc_error_in_a_silent_run_silences_the_next_live_event` (one `RpcError` in one check's silent run; a page-worthy event mined in the next block after the checkpoint caught up) | the event pages once | **never sent**: incident `ev:1030` `notify 0, alerts 0, open`; `caught_up_block` 1030 |
| `test_live_event_while_another_check_blocks_go_live_is_paged` (a check keeps failing for 3 steps; event after the catch-up at 1029) | page | **never sent**: `ev:1030` `notify 0, alerts 0, open`; go-live at 1034 |
| `test_m13_top_up_and_drain_after_catch_up_while_another_check_blocks_go_live` (**real M13**: a retired key topped up with 0.5 ETH and drained in the next block, both after the catch-up) | page (the live-mode control of the same sequence in the same test sends `page open` + `info resolved`) | **no message**: incident `…:1021` `page, notify 0, alerts 0, resolved` |
| `test_m13_top_up_after_catch_up_while_another_check_blocks_go_live` (real M13, value still sitting at go-live) | page | page sent (M13 re-reports a top-up while the value sits): the gap is the event-derived findings that are not re-reported, as above |

Severity: a missed page (D15 BLOCKING), not a duplicated/transient warn. The implementer's sweep (`test_seeded_backfill_sweep`) never makes a check fail its silent run, and `deploy/README.md` "Backfill phase" documents "a check that keeps failing its silent run keeps the phase at `backfill`" without saying that live alerts of every other check are suppressed meanwhile. Repair direction (not applied): silence a check's findings only for its own silent evaluation at the caught-up checkpoint (e.g. `silent = backfill and check.id not in evaluated`), or end the silent phase at the first caught-up checkpoint and keep only blocks at or below it as history; add the failing-check case to the sweep.

### F2 — NOTE (failover defeated, no false clean): an HTTP error whose JSON body is not a JSON-RPC error is accepted as the answer; the next endpoint is never asked

`Rpc._post_one` returns any JSON body of an HTTP 4xx/5xx (except 429) as the answer; `_post` only fails over when the body carries an `error` member. A gateway answering `502 {"message":"Bad Gateway"}` or `403 {"message":"invalid api key"}` (or `200` with neither `result` nor `error`) is therefore "the answer": `call` raises `RpcError(transport=False, "<method>: no result")`, the healthy fallback receives 0 requests, `get_logs` splits down to one block and raises. Reproducer `qa/rpc_probe.py` (local HTTP endpoints, Appendix B):

```
A 502 + non-JSON-RPC JSON body  | call: RpcError(transport=False): eth_blockNumber: no result | B hits 0 | logs RpcError(transport=False)
A 403 + JSON body without error | call: RpcError(transport=False): eth_blockNumber: no result | B hits 0 | logs RpcError(transport=False)
A 200 + body without result/error | same, B hits 0
A 500 + JSON-RPC error body | answer from B (B hits 2) ; A 429 | B ; A 200 + HTML | B ; A range error | B   (correct)
```

Expected (D16): next endpoint on error. Classification NOTE: the error is raised (never `[]`/clean), the step fails and the `rpc` health message fires after 3 failed steps, so it is a loss of availability with an alert, not a false clean; but while the primary answers this way the worker is stalled although the fallbacks are healthy. Owner CP4 / repair worker (treat a non-JSON-RPC body of an HTTP error, and a body with neither `result` nor `error`, as a transport failure of that endpoint).

### F3 — NOTE (message text): M11 deferral health text still says "retried every run"

`m11_updater_liveness.py` `ctx.health("proof-api-weekly", "… DEFERRED: …; retried every run.")` while the CP3 gate retries the weekly comparison once per `full_state_retry_s` (1,800 s). Seen in the running stack's outbox (2026-10-22T20:36:03Z row). Text only (D15 NOTE). Owner CP2b/CP4.

### F4 — NOTE (docs): Kubernetes probe recommendation restarts the pod on any upstream RPC outage

`deploy/README.md` Kubernetes: "liveness/readiness = the compose healthcheck command". The healthcheck is red while steps fail (RPC outage, `healthy: false`), so a liveness probe restarts the pod (Monitor included) during every public-RPC outage longer than the probe budget. State is durable (no data loss); recommend readiness (or a staleness-only liveness test) instead. Owner DevOps/CP4.

### F5 — NOTE (pre-existing since CP1B; V3.5 must-fail condition "silent" met): a second RPC outage inside one `health_realert_s` window (6 h) is never announced

`step` queues the outage message with `dedup=f"rpc:{self.rpc_failures}", realert=True`, so the key is `health:rpc:<keccak("rpc:3")>:<now // 21600>`: every outage reaching 3 failed steps in the same 6 h window maps to the same key and `enqueue` drops it (`INSERT OR IGNORE`, not `unique`). Measured on `ms-cp3` (V3.5 above: my 5-min cut at 21:35Z after the implementer's at 21:08Z: no `rpc` row; only the CP3 `rpc-recovered` row, which announces the end of an outage nobody was told about). Deterministic reproducer `qa/test_qa_rpc_health.py::test_second_outage_in_the_same_window_is_announced` (two 5-step outages 30 min apart on a FakeChain): expected 2 `rpc` health rows, actual 1 — red on the frozen head **and on base `0c388ec`** (pre-existing). The 20th and 100th failures use other keys, so a long second outage is announced at ~20 min. Classification NOTE under D15: no page is lost (the scan resumes from the durable checkpoint and evaluates the outage blocks live afterwards), the status file, Docker healthcheck and `ssv_watch_rpc_consecutive_failures` go red; but the only page-route signal that monitoring stopped is suppressed, and a flapping RPC never announces again within 6 h. Recommended for the F1 repair round (key the message on the outage, e.g. its first failure time or checkpoint, as `rpc-recovered` already does). Owner CP1B/CP3 repair.

### Carried implementer NOTEs (re-observed, classification agreed)

- N-1 (V3.7): computed zero after the N2 page is recorded on the same page incident (title, `zero_block`), no separate message — re-observed (fork block 26,250,800: "sent 3 rows"). NOTE: the cluster paged at N2.
- N-4 / V0.8: known mockall race (this run the parallel red test is `test_handle_match`, panic at `tests/integration/mocks/services.rs:20:1`; serial 18/18). No Rust file changed in CP3.
- N-6: lag gauge frozen during an all-endpoint outage — re-observed in V3.5 (`lag_blocks` 4 throughout the cut).

## 2. Claims I could NOT confirm

- **F-1 (Dockerfile.development's jq cannot load, GLIBC 2.43)**: not reproduced. Rebuilding that image needs pulls from `cgr.dev`/Wolfi apk mirrors, outside the network allowed to this QA; no development-built image is left on the host (`docker images`: only `ssv-network-monitor:cp3-prod` / `openzeppelin/openzeppelin-monitor:latest` from the production build). What I did confirm: the compose default is `Dockerfile.production` (`docker compose config`: `build.dockerfile: Dockerfile.production`), the running Monitor's `jq --version` = `jq-1.8.2`, and the only remaining `Dockerfile.development` users are upstream CI (`.github/workflows/ci.yaml:260`, an image build check, no runtime) and upstream docs; `docker-compose.local.yaml` builds `Dockerfile.production`. Nothing in the deployment path needs the development image.
- **V3.4 fresh-start timings** (backfill 1.33M blocks in ~2.5 min, ~25 min to live): not re-measured (owner scope: no second fresh backfill); I re-measured the running stack instead.
- Implementer falsifiers F1–F8: not re-run (owner scope); the corresponding tests are green in my suite run.
- The tested live behaviour of the Monitor's inbox bridge in the production image (V1A.3 transaction replay): not re-run (CP1A row; the inbox file exists, `status.inbox.ok = true`).

## 3. Verification matrix

| V-ID | Command (exact) | Exit | Result |
|---|---|---|---|
| V3.1 | `for p in "cp1a f5e1fc4" "cp1b b07d2c6" "cp2a 2b434cf" "cp2b 48e997d" "cp2c dd9522b" "cp2d 24b1599" "cp2e 45cc774"; do a=(${=p}); git merge-base --is-ancestor ${a[2]} HEAD; done`; same for every `mtabasco/ms-<lane>` head; `git diff --name-only 0c388ec..HEAD` filtered against CP3's allowed writes | 0 ×7, 0 ×7 | **PASS**: every verified lane head (and every lane branch head, CP1B `d872350`) is an ancestor; 31 files changed by CP3, none outside `reconcile/`, `docker-compose*.yaml`, `deploy/` (none under `deploy/config/`), `.gitignore`, `workers/cp3/`, `plan.md`; `plan/monitoring-service` moved to `6a5d456` after the base with plan files only |
| V3.2 (V1B.1) | `reconcile/.venv/bin/python -m pytest -q -p no:cacheprovider reconcile/tests` | 0 | **PASS**: 2202 passed, 0 skipped (71 s). Base `0c388ec` (git archive, same venv): 2138 passed → +64, count not lower |
| V3.2 (V1B.2) | `reconcile/.venv/bin/ruff check reconcile` | 0 | **PASS** (ruff 0.17.1) |
| V3.2 (V0.8) | `cargo test --locked --test integration -- filters::evm::filter`; control `… -- filters::evm::filter --test-threads=1` | 101 / 0 | **KNOWN RED** (N-4): 17/18 parallel (`test_handle_match`, mockall `services.rs:20:1`), serial 18/18; `test_failure_monitor_only_fetches_receipts_for_monitored_transactions` ok in both |
| V3.3 | `docker compose -p qa-cp3-cfg -f docker-compose.yaml -f docker-compose.reconcile.yaml config --format json` (shell without channel secrets) | 0 | **PASS**: worker `read_only: true`, `tmpfs /tmp:size=64m`, `watch-data:/data` (named), `monitor-data` subpath `inbox` → `/inbox` `read_only: true`, healthcheck present, `depends_on inbox-init: service_completed_successfully`, `inbox-init` `network_mode: none`; environment values all empty/null (names only); Monitor `build.dockerfile: Dockerfile.production`, `monitor-data:/app/data`, config mount read-only. Running container: `touch /inbox/x` and `touch /app/x` → "Read-only file system", `/tmp` writable, uid 10001 |
| V3.4 | running stack `ms-cp3` (worker recreated 21:13:09Z, Monitor since 20:50:28Z): `docker exec ms-cp3-eb-reconcile-1 python eb_reconcile.py status`, status file, `/metrics` at A = 21:19:56Z and B = 21:35:01Z | 0 / 0 | **PASS**: A: checkpoint 26,250,791, lag 1 (status) / 0 (file), `phase: live`, `first_live: []`, status file `healthy: true`, container `(healthy)`, metrics `ssv_watch_backfill 0`, `outbox_failing 0`, `rpc_consecutive_failures 0`, every `check_errors_total` 0, `input_unavailable` 0; B (+15 min): checkpoint **26,250,863** (+72, advanced), lag 4 / 0, live, `healthy: true`, `(healthy)`. `export`: 1 incident ever (m7 info, sent), 0 history-only incidents; outbox: `proof-api-weekly` health, digest at go-live, m7 info, the implementer's V3.5 rows. Inbox `ok` on the read-only mount; Monitor advancing (last block 26,250,828 at 21:25Z), `jq-1.8.2` |
| V3.5 | on `ms-cp3` (stated: no second stack), `docker network disconnect ms-cp3_default ms-cp3-eb-reconcile-1` for 5 min, then `docker network connect …` (script `qa/v35.sh`); restored | 0 / 0 | **FAIL as measured** (cause F5, NOTE under D15): cut 21:35:01Z → failures 1–5 at 21:35:43, 21:36:43, 21:37:43, 21:38:43, 21:39:44 (status file `healthy: false`, error names labels only); **no `rpc` health row for this outage** (its dedup key `health:rpc:aa1dbfe8ff629925:82995` was already taken by the implementer's cut at 21:08:34Z, same 6 h window); restored 21:40:04Z → `rpc-recovered` row 21:42:38Z ("after 5 consecutive failures; checkpoint 26250894"), status file `healthy: true` 21:43:05Z, checkpoint 26,250,863 → 26,250,907, container back to `(healthy)` (21:43:27Z; network `ms-cp3_default` re-attached). N-6 re-observed: `lag_blocks` stayed 4 for the whole cut. The implementer's own cut (first outage of the window) did produce the row (21:08:34Z, visible in `export`) |
| V3.6 | `FORK_RPC_URL=https://mainnet.gateway.tenderly.co reconcile/.venv/bin/python reconcile/scripts/fork_e2e.py --scenario m5 --port 8561` | 0 | **PASS**, fork 26,250,798: clean → clean → latent 630,000 → confirmed (63 matched) → clean; latent silent, `escalate` page at confirmed, resolution; layout guard `[]` |
| V3.7 | `… --scenario m3 --port 8563` | 0 | **PASS**, fork 26,250,800: no alert before → info at onset (26,250,811) → warn at N = 50 → page at N2 = 7,200 → computed zero at 26,298,011 recorded on the page incident (N-1) → resolved (info) after the impersonated liquidation |
| V3.8 | `… --scenario m9 --port 8564` | 0 | **PASS**, fork 26,250,799: `dissent:4` warn (round 1), update (round 2), escalate page (round 3, streak); no failed-round inside 30 min; failed-round page after 30 min of chain time (round 26,250,821) |
| V3.9 | `… --scenario m12 --port 8562` | 0 | **PASS**, fork 26,250,798: 0.6047 ETH → no page; 0.1100 ETH → page `0x16a5…:balance`; top-up → resolved |
| V3.10 | review of `deploy/README.md` "Runbook: running the service" | — | **PASS WITH NOTES**: start/stop/restart, state and backup, status/export/metrics with alert rules, backfill phase, RPC failover, rotate secrets, record → live with `TEST_*` first and a route test, Kubernetes (one replica, RWO block PVC, one pod, initContainer, ConfigMap/Secret, read-only root, probes, snapshots) all present; F4 (probe), and the backfill section documents F1's behaviour without its consequence |

## 4. Targeted attacks (DEFECT-CLASSES) and falsifiers

| # | Attack | Result |
|---|---|---|
| 1a | RPC failover, local HTTP endpoints (`qa/rpc_probe.py`): A answers 500 JSON-RPC error / 429 / HTML / range error, B healthy | B answers every time; `get_logs` returns B's `[]` (a result) — correct |
| 1b | same, A answers an HTTP error with a non-JSON-RPC JSON body, or 200 without result/error | **F2** (no failover; error raised, never clean) |
| 1c | all endpoints down, URLs carrying keys in path and query (`/v3/SECRETKEY123`, `?apikey=SECRETKEY456`) | `RpcError(transport=True)` "all 2 RPC endpoints failed (127.0.0.1: …URLError; …)"; key in message: **False** |
| 1d | chunking/halving (`qa/rpc_probe2.py`): A caps 1,000, B caps 2,000, logs at 7, 4,999, 5,000, 12,345, 19,999 over 0–19,999 | every log returned in order; largest window asked of either endpoint 5,000 |
| 1e | secrets in the running stack: worker log line "RPC endpoints in failover order: MAINNET_RPC_URL, gateway.tenderly.co, rpc.mevblocker.io"; record file / status file / export grep for the env value's host `mainnet.gateway.tenderly.co` | 0 hits; the outage message names `MAINNET_RPC_URL` (label), no URL; Monitor log 0 URL lines; fork logs 0 hits |
| 1f | `rpc_urls` chunk size in code: every `Chain` built with `cfg.max_log_range` (5,000) or the 5,000 default; scan `step = max_log_range` | ≤ 5,000 everywhere |
| 2a | F1 scenarios (Appendix A) | **F1** |
| 2b | restart mid-scan and again between two checks' silent runs (`test_restart_mid_scan_and_mid_silent_evaluation_stays_silent_then_live_once`) | PASS: nothing before go-live, the still-true level once at the first live run, a live event once, no history event |
| 2c | backfill/failover suites `tests/test_backfill.py tests/test_rpc_failover.py` | 63 passed |
| 3 | registry independence: pytest plugin registering an extra check `zz_poison` that raises when built (`-p qa_poison`, whole suite) | 2200 passed, 2 failed, both legitimate production-path builds (`test_config_example_loads_and_builds_every_check`, `test_status_and_export_answer_while_a_run_holds_the_write_lock` builds via the CLI); no lane test builds the global registry |
| 4 | promoted helpers: diff of m3_views / m9_oracle_common / m12 / m13 (same methods, same counts; `Chain.batch` adds a short-answer guard to m3/m9 reads, stricter); lane subsets `-k 'rpc_budget or thresholds_from_chain or restart'` | 116 passed; whole suite green |
| 5 | M11 (CP2b re-check): `tests/test_m11.py tests/test_m9_m10_m11_sweep.py tests/test_m9.py tests/test_m10.py` | 343 passed |
| 5b | own: reported `state-mismatch` open, cluster fixed on chain, API lagging (`api.ref = 1`), weekly due; 20 runs inside the retry interval; then API catches up (`qa/test_qa_m11.py`) | PASS: one probe, DEFERRED, 0 further requests, incident kept (never clean), resolved only after the caught-up comparison; API down ×10 weeks: kept |
| 1g | second RPC outage in the same 6 h window (`qa/test_qa_rpc_health.py`, and V3.5 live) | **F5** (red on head and base) |
| 6 | compose: see V3.3; `Dockerfile.development` users: `git grep` → upstream CI and docs only | see §2 |

Matrix falsifiers F1–F8 of the implementer: not re-run (owner scope). My own falsifiers are the scenarios above (2a red on the frozen head = F1; 1b red = F2; 1g red = F5, also red on the base; the rest green). No source file was mutated.

## 5. Evidence-plane writes

- `plans/monitoring-service/qa/cp3-attempt-1.md` (this report).
- `plans/monitoring-service/plan.md`: CP3 `**Status:**` → `QA FAILED` on `91ca3a7` with this report.
- `plans/monitoring-service/execution-log.md`: one appended event.
- Untracked: `reconcile/.venv`. Scratch (session scratchpad): `ev/*` (logs, status/metrics/export JSON, compose config), `qa/test_qa_backfill.py`, `qa/test_qa_m11.py`, `qa/test_qa_rpc_health.py`, `qa/rpc_probe.py`, `qa/rpc_probe2.py`, `qa/v35.sh`, `qaplug/qa_poison.py`, `base/` (git archive of `0c388ec` `reconcile/`).

## 6. Repo proof

Before commit (`git status --porcelain`):

```
 M plans/monitoring-service/execution-log.md
 M plans/monitoring-service/plan.md
?? plans/monitoring-service/qa/cp3-attempt-1.md
```

`git diff --name-only HEAD` (against the frozen head `91ca3a7`): `plans/monitoring-service/execution-log.md`, `plans/monitoring-service/plan.md`. No implementation file (reconcile source, compose, deploy, Dockerfile, config) appears in either; `git diff --name-only 0c388ec..91ca3a7` is the implementer's set (§3 V3.1), unchanged by QA. `reconcile/.venv` is ignored. Docker: `ms-cp3` stack left running (worker `(healthy)` 21:43:27Z, network re-attached); scratch compose project `qa-cp3-cfg` was `config` only (nothing created); anvil instances of `fork_e2e.py` exited with their scripts.

## Appendix A — F1 reproducer (scratch `qa/test_qa_backfill.py`, run from `reconcile/`)

```python
"""QA CP3 attempt 1: targeted backfill scenarios (scratch, not part of the repo)."""
from __future__ import annotations

from fakechain import FakeChain, FakeClock
from harness import make_worker
from test_backfill import Watch, fee_event, history, sent

from checks.base import Check, Every


class Flaky(Check):
    id = "flaky"

    def __init__(self):
        super().__init__({}, {})
        self.schedule = (Every(60),)
        self.fail = True

    def run(self, ctx, block):
        if self.fail:
            raise RuntimeError("cannot evaluate (e.g. an input that stays broken for a while)")
        return []


def build(tmp_path, chain, start, checks):
    _, w = make_worker(tmp_path, checks, chain=chain, clock=FakeClock(), start_block=start, activation_block=start,
                       max_log_range=4, backfill={"enabled": True, "live_within_blocks": 0, "blocks_per_step": 10})
    return w


def test_live_event_while_another_check_blocks_go_live_is_paged(tmp_path):
    """D17: alerting starts at the first live block after catch-up. A page-worthy event mined AFTER the checkpoint
    caught up, while one other check keeps failing its silent run, must still be sent once go-live happens."""
    chain = FakeChain()
    start = chain.number
    history(chain, n=30)
    watch, flaky = Watch(), Flaky()
    w = build(tmp_path, chain, start, [watch, flaky])
    for _ in range(10):
        w.step()
    assert w.backfilling and "watch" in w.phase()["evaluated"] and "flaky" not in w.phase()["evaluated"]
    caught = w.checkpoint()["block"]
    # live blocks after the catch-up: a page-worthy event
    fee_event(chain)
    ev_block = chain.number
    chain.mine()
    for _ in range(3):
        chain.mine()
        w.clock.advance(60)
        w.step()
    assert w.checkpoint()["block"] > ev_block > caught
    flaky.fail = False  # the broken input recovers
    for _ in range(5):
        chain.mine()
        w.clock.advance(3600)
        w.step()
    assert not w.backfilling
    rows = w.db.conn.execute("SELECT key, notify, alerts, state FROM incidents").fetchall()
    print("incidents:", [dict(r) for r in rows], "caught_up_block:", w.phase()["caught_up_block"], "event:", ev_block)
    assert [r["key"] for r in sent(w)] if False else any(f"ev:{ev_block}" in (r["incident_id"] or "") or
                                                          f"event at {ev_block}" in r["text"] for r in sent(w)), \
        f"live event at {ev_block} (after catch-up at {caught}) was never sent: {[dict(r) for r in sent(w)]}"


def test_m13_top_up_after_catch_up_while_another_check_blocks_go_live(tmp_path):
    """The same with the real M13 check: a retired-key top-up mined after the checkpoint caught up."""
    from fixtures_wallets import FUNDER, RETIRED_SLOT4, WalletChain, eth, wallet_worker
    from harness import outbox

    chain = WalletChain()
    chain.set_balance(FUNDER, eth(100))
    chain.set_balance(RETIRED_SLOT4, eth(0.01))
    chain.mine()
    start = chain.number
    for _ in range(20):
        chain.mine()
    chain, w = wallet_worker(tmp_path, ["m13"], chain=chain, start_block=start, activation_block=start,
                             backfill={"enabled": True, "live_within_blocks": 0, "blocks_per_step": 10})
    flaky = Flaky()
    w.checks["flaky"] = flaky
    for _ in range(6):
        w.clock.advance(60)
        w.step()
    assert w.backfilling and "m13" in w.phase()["evaluated"], w.phase()
    tx = chain.send(FUNDER, RETIRED_SLOT4, gas_used=21_000, value=eth(0.5))
    b = chain.number
    for _ in range(3):
        chain.mine()
        w.clock.advance(60)
        w.step()
    flaky.fail = False
    for _ in range(5):
        chain.mine()
        w.clock.advance(600)
        w.step()
    assert not w.backfilling
    inc = [dict(r) for r in w.db.conn.execute("SELECT key, severity, notify, alerts, state FROM incidents")]
    pages = [r for r in outbox(w, "severity='page'")]
    print("M13 incidents:", inc, "caught_up_block:", w.phase()["caught_up_block"], "top-up block:", b, "tx", tx)
    assert pages, f"M13 top-up page at block {b} never sent; incidents {inc}"


def test_m13_top_up_and_drain_after_catch_up_while_another_check_blocks_go_live(tmp_path):
    """Live-mode control of the same sequence pages (open, resolved); here the top-up and drain happen after the
    checkpoint caught up but before go-live."""
    from fixtures_wallets import FUNDER, RETIRED_SLOT4, WalletChain, eth, wallet_worker
    from harness import outbox

    def run(blocked):
        chain = WalletChain()
        chain.set_balance(FUNDER, eth(100))
        chain.set_balance(RETIRED_SLOT4, eth(0.01))
        chain.mine()
        start = chain.number
        for _ in range(20):
            chain.mine()
        p = tmp_path / ("b" if blocked else "l")
        p.mkdir()
        chain, w = wallet_worker(p, ["m13"], chain=chain, start_block=start, activation_block=start,
                                 backfill={"enabled": True, "live_within_blocks": 0, "blocks_per_step": 10})
        flaky = Flaky()
        flaky.fail = blocked
        w.checks["flaky"] = flaky
        for _ in range(6):
            w.clock.advance(60)
            w.step()
        assert w.backfilling == blocked
        b = chain.number + 1
        chain.send(FUNDER, RETIRED_SLOT4, gas_used=21_000, value=eth(0.5))
        chain.mine()
        chain.send(RETIRED_SLOT4, "0xaee058cca5eed5470b4b48c159045b7c25ddb80f", gas_used=21_000, value=eth(0.505))
        for _ in range(3):
            chain.mine()
            w.clock.advance(60)
            w.step()
        flaky.fail = False
        for _ in range(5):
            chain.mine()
            w.clock.advance(600)
            w.step()
        assert not w.backfilling
        inc = [dict(r) for r in w.db.conn.execute("SELECT key, severity, notify, alerts, state FROM incidents")]
        msgs = [(r["severity"], r["kind"]) for r in outbox(w, "kind NOT IN ('health','digest')")]
        print("blocked" if blocked else "control", "top-up block", b, "caught_up", w.phase().get("caught_up_block"),
              "incidents", inc, "messages", msgs)
        return msgs

    assert ("page", "open") in run(False)  # control: live mode pages
    assert ("page", "open") in run(True), "top-up after catch-up never paged"


def test_restart_mid_scan_and_mid_silent_evaluation_stays_silent_then_live_once(tmp_path):
    """Restart (new Worker on the same database) mid-scan and again between two checks' silent runs; history has
    events and a level that turned on and stays on: nothing before go-live, the level once at the first live run,
    a live event once, no history event ever."""
    from test_backfill import LEVEL_SLOT

    chain = FakeChain()
    start = chain.number
    history(chain, n=45, events_at={4, 20, 33}, level_at={10})

    def new(fail_flaky):
        watch, flaky = Watch(), Flaky()
        flaky.fail = fail_flaky
        w = build(tmp_path, chain, start, [watch, flaky])
        return w

    w = new(True)
    w.step(); w.step()
    assert w.backfilling and w.checkpoint()["block"] < chain.sealed
    w = new(True)  # restart mid-scan
    for _ in range(6):
        w.clock.advance(60); w.step()
    assert w.backfilling and "watch" in w.phase()["evaluated"] and "flaky" not in w.phase()["evaluated"]
    assert sent(w) == []
    w = new(False)  # restart between the silent runs; flaky now works
    w.clock.advance(60); w.step()
    assert not w.backfilling and sent(w) == []
    chain.mine(); w.clock.advance(3600); w.step()
    msgs = [(r["severity"], r["kind"], r["text"].split("\n")[0][-40:]) for r in sent(w)]
    assert len(msgs) == 1 and "level" in msgs[0][2], msgs
    fee_event(chain); chain.mine(); w.clock.advance(60); w.step()
    for _ in range(3):
        chain.mine(); w.clock.advance(3600); w.step()
    msgs = [(r["severity"], r["kind"], r["text"].split("\n")[0][-40:]) for r in sent(w)]
    print(msgs)
    assert len(msgs) == 2 and "event at" in msgs[1][2], msgs
    assert chain.storage(LEVEL_SLOT, chain.number) == 1


def test_one_transient_rpc_error_in_a_silent_run_silences_the_next_live_event(tmp_path):
    """A single RpcError in one check's silent run (public-RPC 429s were seen in the CP3 local run): the event mined
    in the next block, after the catch-up, is evaluated silently and never sent."""
    from rpc import RpcError

    class OnceRpcFail(Flaky):
        id = "oncerpc"

        def run(self, ctx, block):
            if self.fail:
                self.fail = False
                raise RpcError("transport error: all 3 RPC endpoints failed (HTTP 429)", transport=True)
            return []

    chain = FakeChain()
    start = chain.number
    history(chain, n=30)
    watch, once = Watch(), OnceRpcFail()
    w = build(tmp_path, chain, start, [watch, once])
    while w.checkpoint()["block"] < chain.sealed or "watch" not in w.phase().get("evaluated", ()):
        w.step()
    assert w.backfilling and w.rpc_failures == 1
    fee_event(chain)  # mined after the checkpoint caught up
    ev = chain.number
    chain.mine()
    w.clock.advance(30)
    w.step()  # oncerpc now succeeds: go-live at this step's checkpoint (above the event)
    assert not w.backfilling and w.phase()["caught_up_block"] >= ev
    for _ in range(3):
        chain.mine(); w.clock.advance(3600); w.step()
    rows = [dict(r) for r in w.db.conn.execute("SELECT key, notify, alerts, state FROM incidents")]
    print("incidents", rows, "event block", ev, "caught_up_block", w.phase()["caught_up_block"])
    assert any(f"event at {ev}" in r["text"] for r in sent(w)), f"event at {ev} never sent; incidents {rows}"
```

## Appendix B — F2 reproducer (scratch `qa/rpc_probe.py`; `python -I rpc_probe.py <repo>/reconcile`)

```python
"""QA CP3: real HTTP endpoints (local) against rpc.Rpc failover. Usage: python -I rpc_probe.py <reconcile dir>"""
import json, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
sys.path.insert(0, sys.argv[1])
import logging
logging.basicConfig(level=logging.INFO, format="LOG %(levelname)s %(message)s")
from rpc import Rpc, RpcError

def server(behaviour):
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
    return f"http://127.0.0.1:{s.server_address[1]}/v3/SECRETKEY123", hits

def ok(body):
    if isinstance(body, list):
        return 200, [{"jsonrpc": "2.0", "id": b["id"], "result": "0x10"} for b in body]
    if body["method"] == "eth_getLogs":
        return 200, {"jsonrpc": "2.0", "id": body["id"], "result": []}
    return 200, {"jsonrpc": "2.0", "id": body["id"], "result": "0x10"}

cases = {
    "A 502 + non-JSON-RPC JSON body": lambda b: (502, {"message": "Bad Gateway"}),
    "A 403 + JSON body without error": lambda b: (403, {"message": "invalid api key"}),
    "A 200 + body without result/error": lambda b: (200, {"jsonrpc": "2.0", "id": b["id"] if isinstance(b, dict) else 0}),
    "A 500 + JSON-RPC error body": lambda b: (500, {"jsonrpc": "2.0", "id": 1, "error": {"code": -32603, "message": "internal"}}),
    "A 429": lambda b: (429, b"too many"),
    "A 200 + HTML": lambda b: (200, b"<html>oops</html>"),
    "A range error on getLogs": lambda b: (200, {"jsonrpc": "2.0", "id": b["id"], "error": {"code": -32005, "message": "query exceeds max block range 1000"}}),
}
for name, beh in cases.items():
    a, ha = server(beh)
    b, hb = server(ok)
    r = Rpc([a, b], timeout=5, retries=1, primary_retry_s=300)
    try:
        res = r.call("eth_blockNumber", [])
        out = f"answer {res!r}"
    except RpcError as e:
        out = f"RpcError(transport={e.transport}): {e}"
    try:
        logs = r.get_logs("0x" + "11" * 20, 0, 4999, 5000, ["0x" + "22" * 32])
        lout = f"logs {logs!r}"
    except RpcError as e:
        lout = f"logs RpcError(transport={e.transport}): {str(e)[:120]}"
    print(f"{name:40s} | call: {out[:140]} | B hits {len(hb)} | {lout}")

# all endpoints down: error must not include the key path
a = "http://127.0.0.1:1/v3/SECRETKEY123"; b = "http://127.0.0.1:2/?apikey=SECRETKEY456"
r = Rpc([a, b], timeout=2, retries=0)
try:
    r.call("eth_blockNumber", [])
except RpcError as e:
    print("all down:", e, "| leaks key:", "SECRETKEY" in str(e))
```

## Appendix C — F5 reproducer (scratch `qa/test_qa_rpc_health.py`, run from `reconcile/` as Appendix A)

```python
"""QA CP3 attempt 1 (F5): a second RPC outage inside the same health_realert_s window raises no `rpc` health row."""
from fakechain import FakeChain, FakeClock
from harness import make_worker, outbox

from rpc import RpcError


def test_second_outage_in_the_same_window_is_announced(tmp_path):
    chain = FakeChain()
    chain.mine()
    _, w = make_worker(tmp_path, chain=chain, clock=FakeClock())
    real = w.rpc.block_number

    def down():
        raise RpcError("transport error: all 3 RPC endpoints failed", transport=True)

    def outage(steps):
        w.rpc.block_number = down
        for _ in range(steps):
            w.clock.advance(60); w.step()
        w.rpc.block_number = real
        chain.mine(); w.clock.advance(60); w.step()

    outage(5)
    first = [r for r in outbox(w, "kind='health'") if ": rpc\n" in r["text"] or ": rpc —" in r["text"] or "consecutive RPC failures" in r["text"]]
    w.clock.advance(1800)  # 30 min later, a second 5-step outage
    outage(5)
    rows = [r for r in outbox(w, "kind='health'") if "consecutive RPC failures" in r["text"]]
    print([(r["key"], r["text"].split("\n")[-1][:60]) for r in outbox(w, "kind='health'")])
    assert len(first) == 1
    assert len(rows) == 2, f"second outage not announced: {len(rows)} rpc health row(s)"
```
