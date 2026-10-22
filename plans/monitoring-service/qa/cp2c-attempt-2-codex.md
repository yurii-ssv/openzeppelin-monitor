FAIL

# CP2c independent QA — attempt 2 after repair 1

Frozen head: `c6b0a63dc590aad22910525583da0389687c2424` (`mtabasco/ms-cp2c`). QA branch: `mtabasco/ms-qa-cp2c-2`. Measured 2026-10-21–22 UTC; task `task_ddedd0e33232`, dispatch `ctx_03e7e806a26a`. Independent measurement, no implementation repair. Original attempt-1 F1 is repaired; two additional correctness findings below block this round under the owner's round policy.

## 1. Findings

### F1 — HIGH / BLOCKING — M12 uses block counts for 24-hour and seven-day windows

`M12WalletBurn.metrics` selects the last `day_blocks=7200` / `window_blocks=50400` blocks and divides their spend by their actual timestamp span. Timestamp normalization does not make those sets the last 24 hours / seven days. A missed slot expands the set and dilutes recent spend; old transactions can also remain in the seven-day set. This violates spec M12's trailing seven-day average and daily-spend trigger, and DEFECT-CLASSES §4. Thresholds remain configurable; the defect is time-window selection.

Minimal reproducer, with **one** missed 12-second slot (Appendix A supplies executable source):

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/reconcile:$PWD/reconcile/tests" reconcile/.venv/bin/python -m pytest -q /private/tmp/cp2c-qa-2/test_new.py::test_daily_single_missed_slot_at_boundary --tb=short
```

Expected: 0.040005 ETH spent in the current block is within the last 24 hours and exceeds 0.04 ETH, so `oracle:daily-spend` warns. Actual (exit 1): `day_spend_wei` = **0.03999944452159422 ETH/day**, no incident, no warning. The 7200-block span is 86,412 seconds; dividing by it hides the crossing. The larger missed-slot control (`test_daily_is_actual_24h_not_7200_blocks`) similarly measures 0.06 ETH spent now as 0.03 ETH/day and stays silent.

Independent opposite-direction reproducer:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/reconcile:$PWD/reconcile/tests" reconcile/.venv/bin/python -m pytest -q /private/tmp/cp2c-qa-2/test_new.py::test_trailing_window_excludes_transactions_older_than_seven_days --tb=short
```

Expected: the only transaction is **960,000 seconds (11.11 days)** old, so the seven-day spend set is empty and cannot support a runway page. Actual (exit 1): it remains in the 50,400-block set, whose span is **1,209,600 seconds (14 days)**; `window_txs=1`, trailing runway **2.8 days**, projected runway **2.43478 days**, producing a false **page**. Both histories use valid increasing canonical timestamps; no missing RPC/API result is involved. No repair made.

### F2 — HIGH / BLOCKING — partial fee history resolves a supported runway page and completes a reorg run

`raw_batch` now validates the number of JSON-RPC results, but `base_fee` accepts any `baseFeePerGas` list with at least two elements. It does not require the configured block sample, validate `oldestBlock`, or detect an internally truncated fee history. A one-block sample substituted for the requested 300 blocks becomes a clean projection. This is DEFECT-CLASSES §1/§2: missing input becomes recovery and clears a durable retry obligation.

Minimal reproducer and reorg variant (Appendix A):

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/reconcile:$PWD/reconcile/tests" reconcile/.venv/bin/python -m pytest -q /private/tmp/cp2c-qa-2/test_new.py -k 'partial_history_clears_real_page or partial_history_reorg_keeps_retry_marker' --tb=short
```

Expected: partial input errors/UNKNOWN, never a clean resolution; during reorg the marker remains until complete re-evaluation. Actual (exit 1; **2 failed**): complete high-fee history first opens a **page**, then the incomplete two-element fee list closes it, with `run:m12.errors=0`. Messages are `page/open`, then `info/resolved`. The reorg variant starts with a non-null `reorg:m12`, then reports **marker=None, open={}, errors=0** on the same incomplete input. The canonical replacement chain still has the high base fee. `test_partial_feehistory_is_error_never_clean` independently establishes acceptance on first observation. These tests inject missing fee-history data, not a source change; the full-input control actually pages before injection. No repair made.

## 2. Claims I could NOT confirm

- The general claim of correct time anchoring/complete partial-input handling is false: F1/F2. The seeded sweeps are green but do not cover these input/time histories.
- Publicnode's intermittent null-transaction claim remains **unconfirmed**. A fresh public read-only probe returned the attempt-1 oldest example plus ten recent Blockscout candidates: **11/11 non-null, 0 RPC errors**. This sample does not prove all backends always serve all history. Fake null transactions were exercised and remain UNKNOWN while balance rules run.
- Expensive unchanged LIVE full spend backfill, M8 live floor reads, M13 live baseline/incremental reads, and fork smoke were **NOT RUN in attempt 2**: no network-facing discovery/watch implementation changed, and the packet explicitly permits skipping unchanged expensive rows. Attempt 1 contains its independently measured Blast results; those are prior evidence, not new measurements credited here. All owned V rows below were run locally. V2x.4 is explicitly not owned.
- `deploy/README.md` is absent on this lane, so its separate deployment statement cannot be checked here. The CP2c worker README and M13 module docstring explicitly describe **accepted gap D10**: attacker/consolidation internal calls and constructor interactions, and within-block net-zero transfers, are undetectable. The within-block gap test passes. These accepted gaps are not findings; deployment/spec amendment belongs to integration.
- Repair 1 itself is independently confirmed: attempt-1 Appendix A now exits 0 with marker 164804 retained, older runway still open, and a raised reorg evaluation. Appendix B originally gives 26 pass/1 fail because one diagnostic assertion assumes a normal UNKNOWN return and now accesses the absent `last['spend']`; replacing only that diagnostic with `run:m12.errors > 0` gives **27 passed** and retains both substantive marker assertions. The supplied ports, resolved-key/restart regressions, short-batch cases and sweep controls below also pass on the repair and fail where expected on 326d7ff. No original finding is being cleared on the basis of an implementation change made by QA.

## 3. Verification matrix

Preflight: frozen HEAD matched exactly; initial porcelain empty; 350 GiB available. Authorized environment creation: `uv venv reconcile/.venv --python 3.12`; `uv pip install --python reconcile/.venv/bin/python -r reconcile/requirements-dev.txt`, both exit 0 (Python 3.12.15, pytest 9.1.1, ruff 0.17.1). `export PATH=$HOME/.foundry/bin:$PATH`; `anvil --version` exit 0, 1.8.5. No other installation; alerts record/capture only, no mainnet transaction or real messaging secret.

Frozen interface proof: `git diff --exit-code plan/monitoring-service -- reconcile/tests/harness.py reconcile/tests/test_framework.py reconcile/tests/test_worker.py` exits 0, no output; both `git merge-base --is-ancestor bd531b4 HEAD` and `... 500abe4 HEAD` exit 0. The test blobs match integration byte for byte. Intersecting changed reconcile paths with the CP1B tree at `b07d2c6` yields only these three centrally approved tests plus the authorized registration/config additions (`checks/__init__.py`, `config.example.json`); no other frozen CP1B file changed.

Command prefix `P` below expands to `PYTHONDONTWRITEBYTECODE=1 reconcile/.venv/bin/python -m pytest -q`. Each command was executed separately, with exit/output inspected. Scratch scripts/logs are under `/private/tmp/cp2c-qa-2`.

| V-ID | Command | Exit / measured result | Outcome |
|---|---|---|---|
| V2x.1 / V2c.1 M12 | `P reconcile/tests/test_m12.py` | 0; 102 passed | Supplied scenarios pass; independent F1/F2 fail |
| V2x.1 / V2c.1 M8 | `P reconcile/tests/test_m8.py` | 0; 62 passed | PASS |
| V2x.1 / V2c.1 M13 | `P reconcile/tests/test_m13.py` | 0; 79 passed | PASS, accepted gap D10 |
| V2x.2 / V2c.2 | `P reconcile/tests/test_m12.py -k thresholds_from_chain`; same for M8, M13 | each 0; 2/100, 1/61, 1/78 passed/deselected | PASS; fake fee/receipt, daily holder and address/config changes affect findings |
| V2x.3 / V2c.3 | `P reconcile/tests/test_m12.py -k restart`; same for M8, M13 | each 0; 2/100, 1/61, 4/75 | PASS |
| V2x.4 | Not owned by CP2c | NOT RUN, per dispatch | N/A |
| V2x.5 / V2c.5 | `PYTHONDONTWRITEBYTECODE=1 python3 /private/tmp/cp2c-qa-2/falsify.py /private/tmp/cp2c-qa-2/current` | 0; all 14 before=0/mutated=1/restored=0, with assertions | PASS; exact lane mutations/tests, scratch restoration adapter explained below |
| V2x.6 / V2c.6 suite | `P reconcile/tests` | sandbox: 482 passed/1 metrics socket-bind EPERM; authorized unrestricted rerun: **483 passed in 14.65s**, exit 0 | PASS; environment failure distinguished from regression |
| V2x.6 / V2c.6 lint | `reconcile/.venv/bin/ruff check reconcile` | 0; All checks passed | PASS |
| V2c.7 | `P reconcile/tests/test_m12.py -k 'runway or projection or tip'` | 0; 14 passed, 88 deselected | Supplied projection cases PASS; F1/F2 affect real input/window correctness |
| V2c.8 | `P reconcile/tests/test_m13.py` | 0; 79 passed | PASS; direct/internal transfers on both keys, reorg/restart/cap/errors/resolution |
| Repair controls + three new seeded sweeps | `P reconcile/tests/test_m12.py reconcile/tests/test_m8.py reconcile/tests/test_m13.py -k 'qa1_f1 or reorg_unknown_marker or removing_the_evidence_of_a_resolved_runway or short_batch or adversarial_reorg_sweep'` | 0; **128 passed, 115 deselected** | PASS; includes 40 cases per monitor |
| Same regression selection on frozen 326d7ff | `P /private/tmp/cp2c-qa-2/old/reconcile/tests/test_m12.py /private/tmp/cp2c-qa-2/old/reconcile/tests/test_m8.py /private/tmp/cp2c-qa-2/old/reconcile/tests/test_m13.py -k 'qa1_f1 or reorg_unknown_marker or removing_the_evidence_of_a_resolved_runway or short_batch or adversarial_reorg_sweep' --tb=short` | **1; 13 failed, 115 passed, 115 deselected** | Expected RED: four F1/resolved/restart tests, three M12 short batches, one M13 short batch, five M12 sweep seeds. M8/M13 sweeps pass on unchanged code |
| Independent prior + new scenarios | `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/reconcile:$PWD/reconcile/tests" reconcile/.venv/bin/python -m pytest -q /private/tmp/cp2c-qa-2/test_new.py /private/tmp/cp2c-qa-2/test_qa1.py --tb=short` | **1; 38 passed, 6 failed** (three warning categories from scratch marker configuration) | FAIL; six assertions reduce to F1/F2 |
| Original attempt-1 Appendix A | same env, `reconcile/.venv/bin/python /private/tmp/cp2c-qa-2/qa1_a.py` | 0; marker retained, error raised, old runway retained | PASS |

Old-control construction: copied reconcile to scratch with current regression tests, then replaced only `checks/m12_wallet_burn.py` with `git show 326d7ff:reconcile/checks/m12_wallet_burn.py`. `git diff --stat 326d7ff HEAD -- reconcile` proves this is the only changed implementation module; M8/M13 scratch bytes independently compared to HEAD. No implementation from the old control was copied into the worktree.

### Independently derived trigger coverage

Derived directly from spec M8/M12/M13 plus D3/D4/D10 and event-0040's projection ruling, not from the handoff scenario inventory. M8 event/revert rules and M13 attacker/old-slot function rules belong to CP1A/M9; this QA covers M8 floor and the two-key M13 balance watch.

| Owned spec trigger | Positive severity test | Benign/silent twin / boundary |
|---|---|---|
| Oracle balance < 0.15 ETH → page | `test_m12_oracle_balance_below_015_pages` | `...above_015_is_silent`; independent exactly 0.15 silent |
| Oracle runway < 14 days → warn | `...runway_below_14_days_warns` | `...runway_of_20_days_is_silent`; independent exactly 14 resolves/silent |
| Oracle runway < 7 days → page | `...runway_below_7_days_pages` | `...exactly_7_days_warns_not_pages`; 20-day case silent |
| Oracle daily spend > 0.04 ETH → warn | `...daily_spend_above_004_warns` | `...daily_spend_below_004_is_silent`; exactly 0.04 resolves/silent; missed-slot case FAIL F1 |
| Liquidator balance < 0.2 ETH → warn | `...liquidator_below_02_warns` | `...above_02_is_silent`; independent exactly 0.2 silent |
| Liquidator balance < 0.05 ETH → page | `...liquidator_below_005_pages` | `...at_006_warns_only`; independent exactly 0.05 warns without page; >=0.2 silent |
| cSSV supply < 2 × startup/daily Safe cSSV → warn | `test_m8_supply_below_twice_the_seed_warns` | `...supply_at_the_floor_is_silent`, `...well_above_the_floor_is_silent` |
| Either retired key increases → page | direct test parametrized for both, helper internal transfer with/logless attribution tests | zero delta/outgoing silent, first observation silent; one wei positive; within-block net-zero accepted gap D10 |

Projection measurements: both runways and their input evidence are asserted; default `projection_tip='observed'`, alarm takes the minimum, empty observed sample uses the positive dated config fallback with its source flagged, `none` is quieter or equal. Config-example builds all checks; production registry construction rejects each of the six missing required settings. Thresholds are read from config/chain. Inspection finds no direct notifier use by these checks; scenarios run through the shared incident/outbox engine. Warn routes/cadence independently tested at 24h, pages at 6h in M13; no duplicate message on unchanged restart.

### State/input attacks and extra scenarios

- M12 `m12_tx`, `m12_sync`, UNKNOWN marker, balance/runway/daily keys: benign persisted spend, older open incidents below ancestor, resolved runway clearance orphaned, first backfill, zero nonce/zero transactions, same-block two transactions, empty/partial/duplicated/orphaned Blockscout reorg pages, null txs, RPC errors, short batches, restart before handling, reorg below window start, and shorter chain. Partial/empty/orphan listing retains marker and errors; duplicates count both nonces exactly once. F2 additionally attacks the fee-history input used by those persisted evaluations and defeats the obligation.
- M8 floor meta and `supply-floor`: silent first measurement, Safe transfer in/out with daily remeasurement, mid-day restart with changed holder balance, recovery orphaned for a previously resolved key, measurement orphaned across daily boundary with/without restart, older open key below ancestor, malformed/empty calls and reorg read failure. Independent daily/reorg test returns to the canonical **2×SEED** seed after deleting a **SEED** measurement and reopens the warning, both with/without restart.
- M13 `m13_watch`, `m13_topup`, `<address>:<block>` incidents/resolution blocks: baseline silence, one wei, resolved-drain reorg for both addresses plus a different replacement top-up, same-block top-up/spend in both orders with the correct positive net delta and transaction gas, shorter chain, first observation beyond ancestor, depth 1/deep beyond confirmations, crash between detection/handling, RPC error/empty/short read. A 2000-block restart gap with cap 7 reads at most **14** balances across two keys, pages for both internal top-ups and emits two gap health notices. Resolved-key reorg legitimately reopens the old incident plus the new replacement top-up: the extra open message is required recovery from the orphaned resolution, not a duplicate.
- Each monitor's shorter-chain scenario is sensitive to an empty or health-only reorg return: process-only plugin returns `[]`, optionally `ctx.health`; each mode is **3 failed**, normal code is green. No source mutation in the worktree.

### LIVE and budget accounting

Only cheap independent public probes were repeated. Command: `reconcile/.venv/bin/python /private/tmp/cp2c-qa-2/live_probe.py` (authorized network access), exit 0, results inspected individually:

| Step | Endpoint | Measured outcome |
|---|---|---|
| Oldest known window example lookup | `https://ethereum-rpc.publicnode.com` | hash `0xf73a001052a8c2c780d5ff224f3f4b9b7fbc7c6e0a26e8cb1bec9486601da1a7` returned non-null |
| Candidate discovery GET | `https://eth.blockscout.com/api/v2/addresses/0x16A503C901615E6e21AA78cFcaFf11822fc2b617/transactions?filter=from` | 50 items returned; selected ten distinct transaction hashes |
| Ten candidate lookups | `https://ethereum-rpc.publicnode.com` | 10 returned, 0 null, 0 errors; first hash `0xa6d0f0a1d9c4f85a39b3a3c2cc68623d41379ee08a3c44b80ba31d7d7167a2c4` |

No expensive LIVE backfill repeated; no required V row skipped except explicitly unowned V2x.4. Only one successful full-suite rerun after the sandbox socket failure; thereafter targeted runs were for independent findings, repair RED/GREEN and falsification. Mainnet was read-only, no alert route enabled.

## 4. Falsifiers

All mutations ran in scratch, one at a time; source bytes restored and asserted before moving on. The worktree implementation was never mutated. `PYTHONDONTWRITEBYTECODE=1` throughout avoids stale bytecode. The lane's exact `MUT` list/test commands were used, with only the runner adapted: write saved source bytes instead of `git checkout`, assert every before/red/restored exit and byte restoration instead of accepting printed results. Its original output label `porcelain=''` in the scratch log represents that adapter's restoration sentinel; actual root porcelain was measured separately empty before evidence writes. All three scratch check modules equal frozen HEAD after mutations.

| ID | Mutation/test purpose | Before / mutated / restored exits; mutated counts |
|---|---|---|
| F12a | runway <7 → <=7 | 0 / 1 / 0; 1 fail |
| F12b | remove nonce-contiguity guard | 0 / 1 / 0; 1 fail |
| F12c | spend denominator seconds → blocks | 0 / 1 / 0; 1 fail |
| F12d | min runway → max | 0 / 1 / 0; 1 fail |
| F8 | remove ×2 floor | 0 / 1 / 0; 1 fail |
| F13a | increase > → >= | 0 / 1 / 0; 1 fail |
| F13b | checkpoint-only instead of every block | 0 / 1 / 0; 1 fail |
| F13c | keep orphaned top-up rows | 0 / 1 / 0; 1 fail |
| F12e | restore pre-repair reorg_keys-only condition | 0 / 1 / 0; 1 fail |
| F12e-sweep | same mutation against M12 sweep | 0 / 1 / 0; 5 fail, 35 pass |
| F12f | omit short-batch guard | 0 / 1 / 0; 1 fail |
| F12g-sweep | keep orphaned M12 tx rows | 0 / 1 / 0; 37 fail, 3 pass |
| F8b-sweep | keep orphaned floor measurement | 0 / 1 / 0; 13 fail, 27 pass |
| F13d-sweep | keep orphaned resolutions | 0 / 1 / 0; 18 fail, 22 pass |
| Q12 (independent) | balance < → <=; prior QA exact-boundary tests | 0 / 1 / 0; 2 fail, 1 pass; restored 3 pass |
| Q8 (independent) | mark startup floor already measured; independent changed-Safe restart test | 0 / 1 / 0; 1 fail |
| Q13 (independent) | require >1 wei delta; independent 1-wei top-up test | 0 / 1 / 0; 1 fail |
| Q-empty / Q-health | process-only reorg returns [] / health then [] for all checks, against shorter-chain tests | each mutation exit 1; 3 fail; normal 3 pass |

Independent mutation command: `reconcile/.venv/bin/python /private/tmp/cp2c-qa-2/falsify_own.py`, exit 0; each byte restoration true. Plugin commands: same independent PYTHONPATH plus scratch directory, `QA_MODE=empty` / `health`, pytest `-p reorg_plugin .../test_qa1.py -k shorter --tb=no`; each exit 1. F1/F2 use unmodified source and remain red; no attempted repair or manufactured green.

## 5. Evidence-plane writes

- CP2c status: `IMPLEMENTED — AWAITING QA` → **QA FAILED**, citing frozen head and this report.
- Wrote only `plans/monitoring-service/qa/cp2c-attempt-2.md`, CP2c's Status line in `plans/monitoring-service/plan.md`, and one appended event in `plans/monitoring-service/execution-log.md`.
- Authorized ignored `reconcile/.venv` plus scratch contain dependencies, scripts and logs. No implementation, config directory, config.zip, .env file or another lane's record changed. No push, deployment or real alert. Next owner: fresh repair worker for F1/F2, then independent QA.

## 6. Repo proof

Before evidence writes: both `git status --porcelain` and `git diff --name-only c6b0a63dc590aad22910525583da0389687c2424` produced no output. All scratch source mutations restored to frozen bytes.

Immediately before commit, staged authorized files:

```text
$ git status --porcelain
M  plans/monitoring-service/execution-log.md
M  plans/monitoring-service/plan.md
A  plans/monitoring-service/qa/cp2c-attempt-2.md

$ git diff --name-only c6b0a63dc590aad22910525583da0389687c2424
plans/monitoring-service/execution-log.md
plans/monitoring-service/plan.md
plans/monitoring-service/qa/cp2c-attempt-2.md
```

**No implementation file appears in either output.** After commit porcelain is empty; the same frozen-head name diff contains only these three evidence files.

## Appendix A — independent attempt-2 scenarios

Save this block as `/private/tmp/cp2c-qa-2/test_new.py`; the Findings commands execute it against the unmodified checkout. Passing new cases and failing finding cases are intentionally in one file; the full command gives 11 pass/6 fail, and the prior adapted QA file adds 27 passes. Only fixtures/setup imports come from the lane; expectations and adversarial histories are independently written here.

```python
import json
from pathlib import Path
import pytest
import test_m12 as t12
import test_m8 as t8
import test_m13 as t13
from fixtures_wallets import *
from fixtures_wallets import ORACLE, LIQUIDATOR, FUNDER, HELPER, RETIRED_SLOT4, RETIRED_LIQUIDATOR, WalletChain, eth, gwei, stub_cssv, wallet_worker
from harness import advance, outbox


def test_daily_is_actual_24h_not_7200_blocks(tmp_path):
    c=WalletChain(start_block=100000)
    c.timestamp=lambda n: c.genesis_ts + 24*n  # one missed slot between consecutive canonical blocks
    c.set_balance(ORACLE,eth(10)); c.set_balance(LIQUIDATOR,eth(1))
    c.send(ORACLE,PROXY,gas_used=60_000_000,gas_price=gwei(1)) # .06 ETH spent now
    c.mine()
    c,w=wallet_worker(tmp_path,['m12'],chain=c)
    advance(w)
    m=w.checks['m12'].last[ORACLE]
    print('actual_daily',float(m['day_spend_wei'])/1e18,'expected_daily',.06)
    assert t12.open_incidents(w).get(f'{ORACLE}:daily-spend')=='warn'


def test_partial_feehistory_is_error_never_clean(tmp_path):
    c,w,_=t12.setup(tmp_path,balance=eth(.333),checks={'projection_tip':'none'})
    c.set_base_fee(gwei(10));c.mine()
    handle=c._handle
    c._handle=lambda method,p: {'baseFeePerGas':[hex(gwei(.01)),hex(gwei(.01))]} if method=='eth_feeHistory' else handle(method,p)
    advance(w)
    print('partial_history_run_errors',w.db.meta('run:m12')['errors'],'open',t12.open_incidents(w))
    assert w.db.meta('run:m12')['errors'] > 0, 'partial requested fee sample was accepted'

@pytest.mark.parametrize('mode',['empty','partial','duplicate','orphan'])
def test_reorg_listing_variants(tmp_path,mode):
    c,w,_=t12.setup(tmp_path,balance=eth(10));advance(w)
    c.mine(3);advance(w);fork=c.number
    c.send(ORACLE,PROXY,gas_used=100);t12.tick(w,c)
    c.reorg(fork)
    h1=c.send(ORACLE,PROXY,gas_used=200);h2=c.send(ORACLE,PROXY,gas_used=300)
    get=w.bs
    if mode=='partial': w.bs.hide.add(h2)
    if mode=='empty': w.checks['m12'].http_get=lambda url:{'items':[],'next_page_params':None}
    if mode=='duplicate':
        def dup(url):
            d=get(url);d['items']*=2;return d
        w.checks['m12'].http_get=dup
    if mode=='orphan':
        rc=c._receipt
        c._receipt=lambda t:dict(rc(t),blockHash='0x'+'ab'*32) if t['hash']==h2 else rc(t)
    t12.tick(w,c)
    if mode!='duplicate':
        assert w.db.meta('reorg:m12') is not None
        assert w.db.meta('run:m12')['errors'] > 0
    else:
        assert w.db.meta('reorg:m12') is None
        rows=w.db.conn.execute('SELECT gas_used FROM m12_tx WHERE tx_hash IN (?,?)',(h1,h2)).fetchall()
        assert sorted(int(r[0]) for r in rows)==[200,300]

@pytest.mark.parametrize('restart',[False,True])
def test_daily_floor_reorg_resolved_key(tmp_path,restart):
    c,w=t8.setup(tmp_path,supply=3*t8.SEED);advance(w)
    stub_cssv(c,3*t8.SEED,2*t8.SEED);t8.tick(w,c,seconds=86401)
    assert len(t8.open_incidents(w))==1
    fork=c.number
    stub_cssv(c,3*t8.SEED,t8.SEED);t8.tick(w,c,seconds=86401)
    assert not t8.open_incidents(w)
    c.reorg(fork);c.mine()
    w.check_reorg()
    if restart:
        clock=w.clock;start=w.cfg.start_block;activation=w.cfg.activation_block;w.db.conn.close()
        c,w=wallet_worker(tmp_path,['m8'],chain=c,clock=clock,start_block=start,activation_block=activation)
    advance(w)
    assert len(t8.open_incidents(w))==1
    assert t8.state(w)['seed']==str(2*t8.SEED)

@pytest.mark.parametrize('addr',[RETIRED_SLOT4,RETIRED_LIQUIDATOR])
def test_resolved_topup_reorg_replacement(tmp_path,addr):
    c,w=t13.setup(tmp_path)
    t13.top_up(c,addr,value=eth(.6));t13.step(w,c);b=c.sealed
    fork=c.number
    t13.drain(c,addr,value=eth(.6)-21000*c.base_fee(c.number));t13.step(w,c)
    assert not t13.open_keys(w)
    c.reorg(fork);t13.top_up(c,addr,value=eth(.2));t13.step(w,c)
    assert set(t13.open_keys(w))=={f'{addr}:{b}',f'{addr}:{fork}'}
    assert len([x for x in t13.sent(w) if x[0]=='page' and x[1]=='open'])==3

@pytest.mark.parametrize('out_first',[False,True])
def test_m13_same_block_topup_and_spend(tmp_path,out_first):
    c,w=t13.setup(tmp_path)
    c.set_balance(RETIRED_SLOT4,eth(2));t13.step(w,c)
    old=set(t13.open_keys(w))
    if out_first:
        t13.drain(c,value=eth(.2));t13.top_up(c,value=eth(.5))
    else:
        t13.top_up(c,value=eth(.5));t13.drain(c,value=eth(.2))
    b=c.number;t13.step(w,c)
    assert f'{RETIRED_SLOT4}:{b}' in set(t13.open_keys(w))-old
    ev=json.loads(w.db.conn.execute('SELECT evidence FROM incidents WHERE key=?',(f'{RETIRED_SLOT4}:{b}',)).fetchone()[0])
    assert int(ev['delta_wei'])==eth(.3)-21000*c.base_fee(b)


def test_m13_restart_gap_bounded_two_keys(tmp_path):
    c,w=t13.setup(tmp_path,checks={'max_blocks_per_run':7})
    clock=w.clock;start=w.cfg.start_block;activation=w.cfg.activation_block;w.db.conn.close()
    c.mine(1000);c.internal_transfer(RETIRED_SLOT4,eth(.2));c.internal_transfer(RETIRED_LIQUIDATOR,eth(.4));c.mine(1000)
    c,w=wallet_worker(tmp_path,['m13'],chain=c,clock=clock,start_block=start,activation_block=activation,checks={'m13':{'max_blocks_per_run':7}})
    advance(w)
    assert len(t13.open_keys(w))==2
    assert w.db.meta('run:m13')['rpc_calls']['eth_getBalance']<=14
    assert len(t13.gaps(w))==2


def test_daily_single_missed_slot_at_boundary(tmp_path):
    c=WalletChain(start_block=100000)
    c.timestamp=lambda n:c.genesis_ts+12*n+(12 if n>=99999 else 0)
    c.set_balance(ORACLE,eth(10));c.set_balance(LIQUIDATOR,eth(1))
    c.send(ORACLE,PROXY,gas_used=40_005_000,gas_price=gwei(1))
    c.mine();c,w=wallet_worker(tmp_path,['m12'],chain=c);advance(w)
    print('actual_daily',float(w.checks['m12'].last[ORACLE]['day_spend_wei'])/1e18,'expected',.040005)
    assert t12.open_incidents(w).get(f'{ORACLE}:daily-spend')=='warn'


def test_partial_history_clears_real_page(tmp_path):
    c,w,_=t12.setup(tmp_path,balance=eth(.333),checks={'projection_tip':'none'})
    advance(w);assert t12.open_incidents(w)=={}
    c.set_base_fee(gwei(10));t12.tick(w,c,blocks=300)
    assert t12.open_incidents(w).get(f'{ORACLE}:runway')=='page'
    handle=c._handle
    c._handle=lambda method,p: {'baseFeePerGas':[hex(gwei(.01)),hex(gwei(.01))]} if method=='eth_feeHistory' else handle(method,p)
    t12.tick(w,c)
    print('errors',w.db.meta('run:m12')['errors'],'open',t12.open_incidents(w),'alerts',t12.alerts(w))
    assert t12.open_incidents(w).get(f'{ORACLE}:runway')=='page', 'partial input resolved a supported runway page'


def test_trailing_window_excludes_transactions_older_than_seven_days(tmp_path):
    c=WalletChain(start_block=60000)
    c.timestamp=lambda n:c.genesis_ts+24*n
    c.set_balance(ORACLE,eth(2));c.set_balance(LIQUIDATOR,eth(1))
    c.send(ORACLE,PROXY,gas_used=1_000_000_000,gas_price=gwei(1))
    c.mine(40000);c.set_balance(ORACLE,eth(.2));c.mine()
    c,w=wallet_worker(tmp_path,['m12'],chain=c);advance(w)
    print('old_tx_age',c.timestamp(c.sealed)-c.timestamp(60000),'metrics',w.checks['m12'].last[ORACLE])
    assert w.checks['m12'].last[ORACLE]['window_txs']==0
    assert f'{ORACLE}:runway' not in t12.open_incidents(w)


def test_partial_history_reorg_keeps_retry_marker(tmp_path):
    c,w,_=t12.setup(tmp_path,balance=eth(.333),checks={'projection_tip':'none'})
    advance(w);c.set_base_fee(gwei(10));t12.tick(w,c,blocks=300)
    assert t12.open_incidents(w).get(f'{ORACLE}:runway')=='page'
    fork=c.number;c.mine(3);advance(w);c.reorg(fork);c.mine(3)
    w.check_reorg();assert w.db.meta('reorg:m12') is not None
    handle=c._handle
    def short(method,p):
        result=handle(method,p)
        if method=='eth_feeHistory':result['baseFeePerGas']=[hex(gwei(.01)),hex(gwei(.01))]
        return result
    c._handle=short
    advance(w)
    print('marker',w.db.meta('reorg:m12'),'open',t12.open_incidents(w),'errors',w.db.meta('run:m12')['errors'])
    assert w.db.meta('reorg:m12') is not None,'partial fee history committed and cleared the reorg obligation'

```
