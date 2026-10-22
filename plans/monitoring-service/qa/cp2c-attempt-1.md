FAIL

# CP2c independent QA — attempt 1

Frozen head: `a7be02266b5ed86b69095bbaf67e534d0f7d8bc1` (`mtabasco/ms-cp2c`); QA branch `mtabasco/ms-qa-cp2c`. Measured 2026-10-21 UTC. Task `task_f6573f8af6b7`, dispatch `ctx_59b1b99328ab`. Independent QA; no implementation repair.

## 1. Findings

### F1 — HIGH — M12 commits an incomplete reorg evaluation when `ctx.reorg_keys` is empty

Contract: checks/README.md §6.2 requires a reorg run to evaluate every persisted/opened key, including surviving keys, with a finding, `resolve=True`, or an exception. M12's `SpendUnknown` handler only raises if an open runway/daily-spend key is in `ctx.reorg_keys` (open incidents last seen **above** the ancestor). That subset excludes both persisted spend evaluations that were previously benign and older open incidents. If the replacement chain needs Blockscout and the API fails, M12 returns only balance resolutions and a spend health notice; the worker commits, deletes `reorg:m12`, and stops retrying each step. An older open runway incident remains open without evaluation on the replacement chain. Restart cannot recover the lost durable reorg obligation.

Minimal reproducer (unmodified frozen implementation; exit **1**). Save the Python block in Appendix A to `/private/tmp/cp2c-reorg.py`, then run:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/reconcile:$PWD/reconcile/tests" reconcile/.venv/bin/python /private/tmp/cp2c-reorg.py
```

Expected: missing spend input raises during the reorg run; transaction rolls back and `reorg:m12` remains set until complete evaluation. Actual output:

```text
reorg_ancestor= 164804 reorg_keys= []
reported= [('0x16a503c901615e6e21aa78cfcaff11822fc2b617:balance', True), ('0x0431371ad8eda67ba2ef6489ab003b3a7f0d95e0:balance', True)]
spend= UNKNOWN (Blockscout request failed (OSError: replacement-chain API outage))
marker= None
open= {'0x16a503c901615e6e21aa78cfcaff11822fc2b617:runway': 'warn'}
last_seen= [('0x16a503c901615e6e21aa78cfcaff11822fc2b617:runway', 164801)]
AssertionError: incomplete reorg evaluation must preserve marker
```

Two independently written tests reproduce it: `test_m12_reorg_unknown_without_open_spend_must_retry` (persisted but previously benign spend keys) and `test_m12_reorg_unknown_older_open_spend_must_retry` (older open runway key). Appendix B preserves both. The implementer's existing reorg-UNKNOWN test passes because its daily-spend incident is last seen above the ancestor; it does not cover this failure. Fresh repair and independent QA are required; no fix was made here.

## 2. Claims I could NOT confirm

- The broad claim that **every** M12 managed key is re-evaluated or raises on reorg is false: F1.
- The reported publicnode null-transaction behaviour was **not reproduced**: 10 recent Blockscout candidates and 30 independently verified oldest transactions from the seven-day window all returned non-null on `https://ethereum-rpc.publicnode.com` (0/40 null). This sampling does not establish that every backend always serves every old transaction. Null-response behaviour is separately proven UNKNOWN on the fake chain. The original drpc LIVE spend measurement was unavailable here (`RpcError: transport error: HTTP 429`); its replacement on the documented Blast archive endpoint independently reproduced spend, observed tip and both runways.
- No `deploy/README.md` exists at this frozen lane head, so I could not check that separate CP1A deployment document here. The CP2c lane README and M13 module docstring both explicitly document **accepted gap D10**: attacker/consolidation internal calls and constructor interactions, and within-block net-zero incoming/outgoing value, cannot be detected by this balance watch. Its within-block gap test passes. These are accepted gaps, **not findings**. Integration/CP1A owns the deployment document and eventual spec amendment.
- The frame-test patch, all eight mutation red/green claims, the 337-test suite and lint claim, fake-chain trigger results, projections, required-setting validation, and local restart tests were independently reproduced. I did not accept the implementer's prior numbers as a measurement.

## 3. Verification matrix

Preflight: HEAD matched exactly; initial `git status --porcelain` empty; 352 GiB free. Created the authorized ignored environment with `uv venv reconcile/.venv --python 3.12` and `uv pip install --python reconcile/.venv/bin/python -r reconcile/requirements-dev.txt`, both exit 0: CPython 3.12.15, pytest 9.1.1, ruff 0.17.1. Exported `$HOME/.foundry/bin` onto PATH; `anvil --version` exit 0, version 1.8.5. No other installs. All fake runs used record mode/capturing notifier; LIVE alerts went only to scratch. No mainnet transaction or real alert secret.

`git diff plan/monitoring-service -- reconcile/tests/harness.py reconcile/tests/test_framework.py reconcile/tests/test_worker.py` produced no output. Both `git merge-base --is-ancestor bd531b4 HEAD` and `... 500abe4 HEAD` returned 0. Their exact test blobs match integration, including the revised harness hunk. Comparing frozen framework/CP1B implementation paths to integration produced no differences; the only reconcile differences are CP2c modules/tests/fixtures, its registration and config entries. No other frozen CP1B file was edited.

Command prefix below: `P = PYTHONDONTWRITEBYTECODE=1 reconcile/.venv/bin/python -m pytest -q`. Each semicolon-separated row entry below was executed separately and its result inspected.

| V-ID | Exact command (after expanding P) | Exit; observed result | Outcome |
|---|---|---|---|
| V2x.1 / V2c.1 M12 | `P reconcile/tests/test_m12.py` | 0; 48 passed | Supplied scenarios PASS; complete reorg obligation FAIL, F1 |
| V2x.1 / V2c.1 M8 floor | `P reconcile/tests/test_m8.py` | 0; 17 passed | PASS |
| V2x.1 / V2c.1 M13 | `P reconcile/tests/test_m13.py` | 0; 32 passed | PASS; accepted gap D10 retained |
| V2x.2 / V2c.2 | `P reconcile/tests/test_m12.py -k thresholds_from_chain`; same command with `test_m8.py`, `test_m13.py` | Each 0; respectively 2/46, 1/16, 1/31 passed/deselected | PASS; fake base-fee/receipt changes and daily Safe balance/config changes alter outcomes |
| V2x.3 / V2c.3 | `P reconcile/tests/test_m12.py -k restart`; same command with `test_m8.py`, `test_m13.py` | Each 0; 1/47, 1/16, 3/29 passed/deselected | PASS for restart idempotency; incomplete reorg marker persistence fails separately in F1 |
| V2x.4 | Not owned by CP2c | NOT RUN, explicitly excluded by dispatch/plan | N/A; M13 bounded-read and long-gap tests were run in V2c.8 |
| V2x.5 / V2c.5 | `PYTHONDONTWRITEBYTECODE=1 python3 plans/monitoring-service/workers/cp2c/falsify.py "$PWD"` | 0; all 8: before 0, mutation 1, restored 0; porcelain empty after each | PASS; stdout individually inspected because the script itself does not assert all failures |
| V2x.6 / V2c.6 suite | `P reconcile/tests` | First sandbox run 1: 336 passed, metrics socket bind EPERM; rerun outside socket restriction 0: **337 passed in 11.11 s** | PASS; initial environment fault distinguished from code regression |
| V2x.6 / V2c.6 lint | `reconcile/.venv/bin/ruff check reconcile` | 0; All checks passed | PASS |
| V2c.7 | `P reconcile/tests/test_m12.py -k 'runway or projection or tip'` | 0; 12 passed, 36 deselected | PASS: both projections and inputs, min alarm, observed default, configured/flagged empty-sample tip fallback, none quieter |
| V2c.8 | `P reconcile/tests/test_m13.py` | 0; 32 passed | PASS: both keys, direct and helper/internal transfers, baseline/delta, gap/cap, reorg/restart/error and resolution wording |
| Independent adversarial sweep | `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/reconcile:$PWD/reconcile/tests" reconcile/.venv/bin/python -m pytest -q /private/tmp/cp2c-qa-1/test_independent.py --tb=short` | **1; 25 passed, 2 failed**, 3 harmless unknown-marker warnings because scratch is outside the repository's pytest configuration | FAIL; both failures F1; source preserved in Appendix B |

### Independently derived spec trigger coverage

I derived this table directly from spec §3 M8/M12/M13, D3/D4/D10 and the checkpoint boundary before deciding what the tests establish. M8 event triggers and M13 old-slot function calls / attacker direct interactions belong to CP1A/M9, outside this floor/balance-watch QA; I do not claim to have tested those rules here.

| Owned trigger from spec | Alert test | Benign/silent twin and exact boundary |
|---|---|---|
| M12 oracle balance < 0.15 ETH → page | `test_m12_oracle_balance_below_015_pages` | `...above_015_is_silent`; independent exactly 0.15 silent |
| M12 oracle runway < 14 d → warn | `...runway_below_14_days_warns` | `...runway_of_20_days_is_silent`; independent exactly 14 resolves/silent |
| M12 oracle runway < 7 d → page | `...runway_below_7_days_pages` | `...runway_exactly_7_days_warns_not_pages` (warn condition remains true); 20 d silent |
| M12 daily spend > 0.04 ETH → warn | `...daily_spend_above_004_warns` | `...daily_spend_below_004_is_silent`; independent exactly 0.04 resolves/silent |
| M12 liquidator balance < 0.2 ETH → warn | `...liquidator_below_02_warns` | `...liquidator_above_02_is_silent`; independent exactly 0.2 silent |
| M12 liquidator balance < 0.05 ETH → page | `...liquidator_below_005_pages` | `...liquidator_at_006_warns_only`; independent exactly 0.05 warns, never pages; >=0.2 silent |
| M8 totalSupply < 2 × start/daily Safe balance → warn | `test_m8_supply_below_twice_the_seed_warns` | `...supply_at_the_floor_is_silent`, `...well_above_the_floor_is_silent`; zero supply extra warning is documented |
| M13 either retired key increases → page | `test_m13_direct_top_up_of_either_retired_key_pages_at_the_checkpoint` (both addresses), `...internal_transfer_from_a_helper_contract_pages`, `...internal_transfer_without_any_log_still_pages` | `...zero_delta_blocks_stay_silent`, `...outgoing_value_stays_silent`, `...first_observation_is_a_silent_baseline`; independent one wei and two top-ups in one block |

Additional independently exercised cases: duplicated API candidates do not double count; orphan receipt hashes and null transactions produce UNKNOWN; null transactions preserve balance alerts; zero tx count proves a silent zero-spend window; two same-block oracle transactions count both nonces and receipt costs. Missing each of six required settings fails through production `registry.build` (not just direct check construction); the frozen config-example test builds all registered checks. M8 in/out Safe-balance changes affect only the daily measurement or restart measurement; independent mid-day restart uses the changed balance. All three checks survive a shorter replacement chain and retain their canonically supported open key. A different M13 top-up at the same address:block changes the evidence to 0.7 ETH without duplicating the incident. Warn delivery is Telegram at the exact 24 h cadence; M13 direct/internal pages and 6 h cadence were measured by its tests. M13 draining resolves with `Value LEFT` / `NOT an all-clear`.

Reorgs actually run: depth 1; depth beyond confirmations (2 in the supplied deep cases); trigger-carrying transaction/supply-state/top-up removal; crash between detection and check execution; replacement top-up at same/different blocks; retained earlier top-up; removed drain; and shorter replacement chains for all three monitors. Injecting an empty or health-only reorg return in each check made the independent surviving-key test red (3/3 each); restoring normal execution made all three green. The actual UNKNOWN-only M12 branch still violates the obligation in F1.

### LIVE measurements (public read-only)

Commands (scripts preserved in Appendix C; `PYTHONPATH="$PWD/reconcile"`, `PYTHONDONTWRITEBYTECODE=1`):

| Step | Endpoint | Command | Exit and actual result |
|---|---|---|---|
| L1 drpc initial read | `https://eth.drpc.org` | `reconcile/.venv/bin/python -u /private/tmp/cp2c-qa-1/live.py https://eth.drpc.org` | Script 0, but M12 measurement **NOT RUN to completion**: worker recorded `RpcError: transport error: HTTP 429`, not a spend verdict. First step 213.69 s, retry 3.63 s. At checkpoint 26,241,598 M8 read seed 1 cSSV / floor 2 cSSV / supply 4,409,339.865102349873044845 cSSV; M13 baseline 22,691,702,922,000 and 31,560,987,664,500 wei, silent. No fake green inferred from the script's exit code. |
| L2 replacement archive full sweep | `https://eth-mainnet.public.blastapi.io` | `reconcile/.venv/bin/python -u /private/tmp/cp2c-qa-1/live.py https://eth-mainnet.public.blastapi.io` | 0; step 1: 33.34 s, checkpoint **26,241,619**, all three run errors 0, no outbox messages. M12 **738** verified window txs (nonces **279–1016**, persisted next nonce **1017**), **0.123223177465857884 ETH** over **606,780 s**, **15.5795 Mgas/day**, **0.0175458692 ETH/day** trailing and **0.0202023252 ETH/day** last-day spend. Oracle **0.333001140643349157 ETH**, liquidator **0.301417970739720016 ETH**. Mean base fee **0.488282226 gwei**, observed tip **1 gwei**, runways **18.9788911 d trailing / 14.3617229 d projected**. Counts: 750 tx lookups + 750 receipts + 753 headers + one fee history; two balance reads and one nonce read. M8 same seed/floor/supply; M13 baseline same as drpc. |
| L3 archive incremental sweep | `https://eth-mainnet.public.blastapi.io` | Same invocation, step 2 | 0; 1.62 s, checkpoint **26,241,622**, all check errors 0, still no outbox. M12 no transaction/receipt lookup; 3 headers, 1 fee history, 2 balances, 1 nonce. Base fee **0.492537252 gwei** + observed **1 gwei**, projected **0.0232530038 ETH/day**, runway **14.3207795 d**; trailing unchanged. M13 **6** balance reads (3 blocks × 2), unchanged and silent. M8 one supply call, start baseline retained. |
| L4 publicnode older-tx probe | `https://ethereum-rpc.publicnode.com` | End of the Blast invocation: `eth_getTransactionByHash` for the 30 oldest verified hashes | 0; **30 returned, 0 null**. Example oldest hash `0xf73a001052a8c2c780d5ff224f3f4b9b7fbc7c6e0a26e8cb1bec9486601da1a7`. Claim of intermittent nulls remains unconfirmed by this QA run. |
| L5 focused recent-tx comparison | Blockscout `https://eth.blockscout.com`, then publicnode and drpc | `reconcile/.venv/bin/python -u /private/tmp/cp2c-qa-1/publicnode-probe.py` | 0 wrapper; publicnode **10/10 non-null**, candidate blocks 26,239,696 to 26,237,299. drpc comparison **NOT RUN to completion**, exact error `transport error: HTTP 429`. |

The final nonce range and sync row were also read from a memory-deserialized copy of the completed scratch DB using the QA venv SQLite (no file modification). These are the implementer's extra LIVE smoke claims, not additional mandatory V-IDs. Required fake-chain V2c.7/V2c.8 ran fully. Failed endpoint measurements are not credited as passing; the archive replacement supplied the end-to-end metrics. All endpoints used public RPC and alerts record mode. The frozen module's 12-block checkpoint latency is explicitly documented in the lane README; immediate-head exceptions to confirmations remain an integration/spec-alignment item outside this checkpoint's explicit “at the checkpoint” test.


Budget: no redundant unchanged full-suite rerun after its clean 337-test result; targeted mutation restorations were verified. One drpc backfill attempt plus its immediate retry hit rate limiting; one documented archive fallback was attempted. No full publicnode history backfill was repeated; focused transaction probes test that claim cheaply. V2x.4 is the only deliberately excluded matrix row.

## 4. Falsifiers

All source mutations were temporary, one at a time, and restored to their original bytes before the next. `PYTHONDONTWRITEBYTECODE=1` avoided the implementer's documented stale-bytecode hazard. No implementation diff remains.

| ID | Mutation; test | Before / mutated / restored exits and counts |
|---|---|---|
| F12a | `<` → `<=` at runway page boundary; `test_m12_oracle_runway_exactly_7_days_warns_not_pages` | 0 / 1 / 0; 1 pass / 1 fail / 1 pass |
| F12b | Drop nonce-contiguity check; `test_m12_indexer_gap_is_unknown_not_clean` | 0 / 1 / 0; 1 / 1 / 1 |
| F12c | Spend denominator seconds → block count; `test_m12_runway_trailing_spend_drives_when_the_base_fee_fell` | 0 / 1 / 0; 1 / 1 / 1 |
| F12d | Lower projection `min` → `max`; `test_m12_runway_from_trailing_spend_and_from_current_base_fee` | 0 / 1 / 0; 1 / 1 / 1 |
| F8 | Drop floor multiplier; `test_m8_supply_below_twice_the_seed_warns` | 0 / 1 / 0; 1 / 1 / 1 |
| F13a | Delta `>` → `>=`; `test_m13_zero_delta_blocks_stay_silent` | 0 / 1 / 0; 1 / 1 / 1 |
| F13b | Read only checkpoint, skip intervening blocks; `test_m13_multi_block_net_zero_between_checkpoints_is_caught` | 0 / 1 / 0; 1 / 1 / 1 |
| F13c | Retain orphaned top-up rows; `test_m13_reorg_depth_1_removing_the_top_up_resolves_and_never_re_pages` | 0 / 1 / 0; 1 / 1 / 1 |
| Q12 (QA) | `bal < w.balance_page` → `bal <= w.balance_page`; independent `test_balance_exact_boundaries` | 0 / 1 / 0; 3 pass / 2 fail + 1 pass / 3 pass |
| Q8 (QA) | `self.measured = False` → `True` at startup; independent `test_m8_restart_midday_uses_new_safe_balance` | 0 / 1 / 0; 1 / 1 / 1 |
| Q13 (QA) | `bal > prev` → `bal - prev > 1`; independent `test_m13_one_wei_is_incoming_value` | 0 / 1 / 0; 1 / 1 / 1 |
| Q-reorg-empty (QA) | Scratch pytest plugin returns `[]` on reorg for each monitor; independent `-k shorter` | normal 0 (3 pass); mutated 1 (3 fail); restored 0 (3 pass) |
| Q-reorg-health (QA) | Same plugin emits only health and returns `[]` on reorg | mutated 1 (3 fail); restored 0 (3 pass) |
| Q-M12-UNKNOWN (QA) | Actual API outage on replacement chain, with persisted benign keys / older surviving open key | **RED on unmodified code**, 2 independent reproductions; F1, not a source mutation |

F12/F8/F13 command is V2x.5 above. Additional source mutation runner: `PYTHONDONTWRITEBYTECODE=1 reconcile/.venv/bin/python /private/tmp/cp2c-qa-1/falsify-own.py`; exit 0. It asserted before=0, mutated=1, restored=0 and empty porcelain for **each** Q12/Q8/Q13. Both plugin modes were run with `-p failopen_plugin ... -k shorter --tb=no` and the normal run without the plugin; the plugin modifies only process memory. Appendix B supplies the independent tests used by these commands.

## 5. Evidence-plane writes

- CP2c status: `IMPLEMENTED — AWAITING QA` → `QA FAILED`, naming frozen head and this report.
- Wrote only `plans/monitoring-service/qa/cp2c-attempt-1.md`, the CP2c **Status:** line in `plans/monitoring-service/plan.md`, and one appended CP2c QA event in `plans/monitoring-service/execution-log.md`.
- Authorized ignored `reconcile/.venv` plus scratch `/private/tmp/cp2c-qa-1` contain the local environment, scripts and read-only LIVE evidence. No config directory, config.zip, .env file, implementation or other lane was changed. No production alert, push or deployment.

## 6. Repo proof

After all 11 source mutations, every restoration reported `git status --porcelain = ''`; `git diff --name-only a7be02266b5ed86b69095bbaf67e534d0f7d8bc1` also printed nothing before report writing. Frozen patch files and all implementation files match the frozen HEAD.

Immediately before commit, with the three authorized files staged:

```text
$ git status --porcelain
M  plans/monitoring-service/execution-log.md
M  plans/monitoring-service/plan.md
A  plans/monitoring-service/qa/cp2c-attempt-1.md

$ git diff --name-only a7be02266b5ed86b69095bbaf67e534d0f7d8bc1
plans/monitoring-service/execution-log.md
plans/monitoring-service/plan.md
plans/monitoring-service/qa/cp2c-attempt-1.md
```

**No implementation file appears in either output.** `git diff --exit-code a7be02266b5ed86b69095bbaf67e534d0f7d8bc1 -- reconcile` exits 0 (empty); all implementation mutation experiments were fully restored. `git diff --check` is clean. The status transition and appended event are evidence-plane changes only.

## Appendix A — minimal F1 reproducer

```python
import tempfile
from pathlib import Path
import test_m12 as t
from fixtures_wallets import ORACLE, PROXY, eth
from harness import advance
with tempfile.TemporaryDirectory() as d:
    c,w,_=t.setup(Path(d),balance=eth(.18))
    advance(w)
    c.mine(3);advance(w)
    fork=c.number
    c.mine(3);advance(w)
    check=w.checks['m12'];original=check.run
    def trace(ctx,block):
        print('reorg_ancestor=',ctx.reorg_ancestor,'reorg_keys=',sorted(ctx.reorg_keys))
        result=original(ctx,block)
        print('reported=',[(f.key,f.resolve) for f in result])
        return result
    check.run=trace
    c.reorg(fork);c.send(ORACLE,PROXY,gas_used=160000)
    w.bs.down=OSError('replacement-chain API outage')
    c.mine();advance(w)
    print('spend=',check.last[ORACLE].get('spend'))
    print('marker=',w.db.meta('reorg:m12'))
    print('open=',t.open_incidents(w))
    print('last_seen=',[tuple(r) for r in w.db.conn.execute("SELECT key,last_seen_block FROM incidents WHERE state='open'")])
    assert w.db.meta('reorg:m12') is not None, 'incomplete reorg evaluation must preserve marker'
```

## Appendix B — independent adversarial suite

Save this block to `/private/tmp/cp2c-qa-1/test_independent.py` and use the independent-sweep command above. It intentionally stays red for F1. Only normal test fixtures are imported; expected outcomes come from the spec and README obligation.

```python
import json
from fractions import Fraction
import pytest
import test_m12 as t12
import test_m8 as t8
import test_m13 as t13
from fixtures_wallets import *
from fixtures_wallets import (ORACLE, LIQUIDATOR, RETIRED_SLOT4, RETIRED_LIQUIDATOR, FUNDER, CSSV, TOTAL_SUPPLY, M12_CFG, M8_CFG, M13_CFG, WalletChain, wallet_worker, stub_cssv, eth, gwei)
from harness import advance, outbox
from checks.m12_wallet_burn import M12WalletBurn
from checks.m8_supply_floor import M8SupplyFloor
from checks.m13_retired_keys import M13RetiredKeys

@pytest.mark.parametrize('who,balance,expected', [('oracle',eth(.15),None),('liquidator',eth(.2),None),('liquidator',eth(.05),'warn')])
def test_balance_exact_boundaries(tmp_path,who,balance,expected):
    c,w,_=t12.setup(tmp_path,balance=balance if who=='oracle' else eth(10),liquidator=balance if who=='liquidator' else eth(1),gas_per_day=1_000_000)
    advance(w)
    assert [s for s,k,kind in t12.alerts(w) if k.endswith(':balance')] == ([] if expected is None else [expected])

def test_runway_exact_14_and_daily_exact_004():
    check=M12WalletBurn({**M12WalletBurn.defaults, **M12_CFG},{})
    w=check.wallets[0]
    m={'runway_trailing_days':Fraction(14),'runway_base_fee_days':Fraction(14),'balance_wei':eth(1),'avg_daily_spend_wei':Fraction(eth(1),14),'base_fee_wei':gwei(1),'avg_priority_fee_wei':gwei(1),'avg_daily_gas':100,'day_spend_wei':eth(.04),'day_blocks':7200}
    fs=check.spend_findings(w,m,100)
    assert all(f.resolve for f in fs)

@pytest.mark.parametrize('key,cls,cfg', [('oracle_wallet',M12WalletBurn,M12_CFG),('liquidator_wallet',M12WalletBurn,M12_CFG),('fallback_tip_wei',M12WalletBurn,M12_CFG),('cssv',M8SupplyFloor,M8_CFG),('seed_holder',M8SupplyFloor,M8_CFG),('addresses',M13RetiredKeys,M13_CFG)])
def test_each_required_setting_missing(key,cls,cfg):
    from checks import REGISTRY, build
    settings={'checks':{cid:{'enabled':False} for cid in REGISTRY}}
    settings['checks'][cls.id]={k:v for k,v in cfg.items() if k!=key}
    with pytest.raises(SystemExit): build(settings)

def test_duplicate_api_candidate_counted_once(tmp_path):
    c,w,_=t12.setup(tmp_path)
    get=w.bs
    def duplicated(url):
        page=get(url)
        page['items']*=2
        return page
    w.checks['m12'].http_get=duplicated
    advance(w)
    assert w.checks['m12'].last[ORACLE]['window_txs']==14
    assert t12.alerts(w)==[]

def test_null_tx_unknown_balances_still_evaluated(tmp_path):
    c,w,_=t12.setup(tmp_path,balance=eth(.1))
    c.empty_methods.add('eth_getTransactionByHash')
    advance(w)
    assert t12.open_incidents(w)=={f'{ORACLE}:balance':'page'}
    assert 'UNKNOWN' in w.checks['m12'].last[ORACLE]['spend']

def test_orphan_candidate_not_counted(tmp_path):
    c,w,_=t12.setup(tmp_path)
    receipt=c._receipt
    c._receipt=lambda t: dict(receipt(t),blockHash='0x'+'fa'*32)
    advance(w)
    assert 'UNKNOWN' in w.checks['m12'].last[ORACLE]['spend']
    assert w.db.conn.execute('SELECT COUNT(*) FROM m12_tx').fetchone()[0]==0

def test_zero_transactions_is_proven_by_zero_nonce(tmp_path):
    c=WalletChain(start_block=100000)
    c.set_balance(ORACLE,eth(1));c.set_balance(LIQUIDATOR,eth(1));c.mine()
    c,w=wallet_worker(tmp_path,['m12'],chain=c)
    advance(w)
    m=w.checks['m12'].last[ORACLE]
    assert m['window_txs']==0 and m['runway_trailing_days'] is None and m['runway_base_fee_days'] is None
    assert m['avg_priority_fee_wei']==gwei(1) and m['tip_source'].startswith('fallback_tip_wei')
    assert outbox(w)==[]

def test_two_transactions_same_block_both_counted(tmp_path):
    c,w,_=t12.setup(tmp_path,balance=eth(10));advance(w)
    before=w.db.conn.execute('SELECT COUNT(*) FROM m12_tx').fetchone()[0]
    c.send(ORACLE,PROXY,gas_used=100);c.send(ORACLE,PROXY,gas_used=200)
    t12.tick(w,c)
    rows=w.db.conn.execute('SELECT gas_used FROM m12_tx ORDER BY nonce DESC LIMIT 2').fetchall()
    assert [r[0] for r in rows]==['200','100']
    assert w.db.conn.execute('SELECT COUNT(*) FROM m12_tx').fetchone()[0]==before+2

def test_m12_reorg_unknown_without_open_spend_must_retry(tmp_path):
    c,w,_=t12.setup(tmp_path,balance=eth(10));advance(w)
    t12.tick(w,c,blocks=3)
    fork=c.number
    c.send(ORACLE,PROXY,gas_used=150000);t12.tick(w,c)
    assert t12.open_incidents(w)=={}
    c.reorg(fork);c.send(ORACLE,PROXY,gas_used=160000)
    w.bs.down=OSError('QA API outage on replacement chain')
    t12.tick(w,c)
    assert 'UNKNOWN' in w.checks['m12'].last[ORACLE]['spend']
    assert w.db.meta('reorg:m12') is not None, 'UNKNOWN reorg evaluation committed and cleared durable retry marker for persisted spend keys'

def test_m12_reorg_unknown_older_open_spend_must_retry(tmp_path):
    c,w,_=t12.setup(tmp_path,balance=eth(.18));advance(w)
    assert t12.open_incidents(w)=={f'{ORACLE}:runway':'warn'}
    # Record a newer checkpoint without running the 300-second check; incident evidence predates ancestor.
    c.mine(3);advance(w)
    fork=c.number
    c.mine(3);advance(w)
    c.reorg(fork);c.send(ORACLE,PROXY,gas_used=160000)
    w.bs.down=OSError('QA API outage on replacement chain')
    c.mine();advance(w)
    assert w.db.meta('reorg:m12') is not None, 'Open runway key was not re-evaluated yet reorg retry marker was cleared'

@pytest.mark.parametrize('cid',['m8','m12','m13'])
def test_shorter_replacement_chain(tmp_path,cid):
    if cid=='m8':
        c,w=t8.setup(tmp_path,supply=t8.SEED);advance(w)
    elif cid=='m12':
        c,w,_=t12.setup(tmp_path,balance=eth(.1));advance(w)
    else:
        c,w=t13.setup(tmp_path);t13.top_up(c);t13.step(w,c)
    c.mine(2);w.clock.advance(301);advance(w)
    fork=c.number
    c.mine(5);w.clock.advance(301);advance(w)
    c.reorg(fork);c.mine()  # new sealed height below old checkpoint
    advance(w)
    assert w.db.meta('reorg:'+cid) is None
    assert w.checkpoint()['block']==c.sealed
    assert any(r['state']=='open' for r in w.db.conn.execute('SELECT state FROM incidents WHERE check_id=?',(cid,)))

def test_m8_daily_transfer_out_then_in(tmp_path):
    c,w=t8.setup(tmp_path,supply=3*t8.SEED);advance(w)
    stub_cssv(c,3*t8.SEED,2*t8.SEED)
    t8.tick(w,c,seconds=86400)
    assert t8.alerts(w)==[('warn','open')]
    stub_cssv(c,3*t8.SEED,t8.SEED//2)
    t8.tick(w,c,seconds=86399)
    assert t8.state(w)['seed']==str(2*t8.SEED)
    t8.tick(w,c,seconds=1)
    # interval not due at one second: force the check to measure the daily boundary
    w.step(force=True)
    assert t8.state(w)['seed']==str(t8.SEED//2)
    assert [(s,k) for s,k in t8.alerts(w) if k!='realert']==[('warn','open'),('info','resolved')]

@pytest.mark.parametrize('cid',['m8','m12'])
def test_warn_route_and_24h_cadence(tmp_path,cid):
    if cid=='m8': c,w=t8.setup(tmp_path,supply=t8.SEED)
    else: c,w,_=t12.setup(tmp_path,liquidator=eth(.1))
    advance(w)
    records=outbox(w,"kind='open'")
    assert len(records)==1 and records[0]['severity']=='warn' and records[0]['delivery']=='telegram'
    c.mine();w.clock.advance(86399);advance(w)
    assert len(outbox(w,"kind='realert'"))==0
    c.mine();w.clock.advance(1);advance(w)
    assert len(outbox(w,"kind='realert'"))==1

def test_m13_two_topups_same_block_and_swept_gap_wording(tmp_path):
    c,w=t13.setup(tmp_path)
    t13.top_up(c,value=eth(.1));t13.top_up(c,value=eth(.2));b=c.number;t13.step(w,c)
    row=outbox(w,"kind='open'")[0]
    assert '+0.300000 ETH' in row['text']
    assert row['delivery']=='telegram'
    t13.drain(c,value=eth(.3));t13.step(w,c)
    assert 'NOT an all-clear' in outbox(w,"kind='resolved'")[0]['text']

def test_m13_replacement_different_topup_same_key(tmp_path):
    c,w,fork=t13._top_up_then(tmp_path);t13.step(w,c)
    c.reorg(fork);t13.top_up(c,value=eth(.7));t13.step(w,c)
    inc=w.db.conn.execute("SELECT evidence FROM incidents WHERE state='open'").fetchone()
    assert json.loads(inc[0])['delta_wei']==str(eth(.7))
    assert len(outbox(w,"kind='open'"))==1 and w.db.meta('reorg:m13') is None


def test_m13_one_wei_is_incoming_value(tmp_path):
    c,w=t13.setup(tmp_path)
    c.credit(RETIRED_SLOT4,1);t13.step(w,c)
    inc=w.db.conn.execute("SELECT evidence FROM incidents WHERE state='open'").fetchone()
    assert inc is not None and json.loads(inc[0])['delta_wei']=='1'

def test_m8_restart_midday_uses_new_safe_balance(tmp_path):
    c,w=t8.setup(tmp_path,supply=3*t8.SEED);advance(w)
    stub_cssv(c,3*t8.SEED,2*t8.SEED);c.mine()
    _,w2=wallet_worker(tmp_path,['m8'],chain=c,start_block=w.cfg.start_block,activation_block=w.cfg.activation_block,clock=w.clock)
    advance(w2)
    assert t8.state(w2)['floor']==str(4*t8.SEED) and t8.alerts(w2)==[('warn','open')]
```

## Appendix C — LIVE measurement scripts

The drpc attempt used `live.sqlite`; the subsequent preserved version gives each endpoint its own scratch database. Use a fresh scratch directory/database to reproduce a first sweep. The Python `Worker.step` wrapper can return without a completed check on RPC failure, so inspect `run_meta` and metrics as above.

```python
import json, sys, time
from pathlib import Path
from eb_reconcile import Config, DEFAULTS, DB, Worker
from rpc import Rpc
root=Path.cwd(); scratch=Path('/private/tmp/cp2c-qa-1')
endpoint=sys.argv[1]
rpc=Rpc(endpoint,timeout=20,retries=1,batch_size=20)
head=rpc.block_number()
raw=json.loads((root/'reconcile/config.example.json').read_text())
raw.update(start_block=head-12,activation_block=head-12,views=None,db_path=str(scratch/('live-'+endpoint.split('/')[2]+'.sqlite')),rpc_batch_size=20,rpc_retries=1,rpc_timeout_s=20)
raw['checks']['m5']={'enabled':False}
raw['inventory']={'api_url':None};raw['inbox']={};raw['metrics']={'enabled':False}
raw['alerts']={'mode':'record','record_path':str(scratch/'alerts.jsonl'),'digest_interval_s':0}
w=Worker(Config({**DEFAULTS,**raw}),rpc,DB(raw['db_path']))
w.bootstrap()
for i in range(2):
    started=time.time();w.step(force=True)
    metrics=w.checks['m12'].last
    print(json.dumps({'endpoint':endpoint,'step':i+1,'head':head,'checkpoint':w.checkpoint(),'seconds':time.time()-started,'m12':metrics,'m8':w.checks['m8'].last,'m13':[dict(r) for r in w.db.conn.execute('SELECT * FROM m13_watch')],'run_meta':{r[0]:r[1] for r in w.db.conn.execute("SELECT key,value FROM meta WHERE key LIKE 'run:%'")},'outbox':[dict(r) for r in w.db.conn.execute('SELECT * FROM outbox')]},default=str),flush=True)
# Reuse independently verified older hashes to test publicnode's transaction availability without a second full backfill.
hashes=[r[0] for r in w.db.conn.execute('SELECT tx_hash FROM m12_tx ORDER BY block LIMIT 30')]
probe=Rpc('https://ethereum-rpc.publicnode.com',timeout=20,retries=1,batch_size=10)
try:
    txs=probe.batch([('eth_getTransactionByHash',[h]) for h in hashes])
    print(json.dumps({'publicnode_probe':True,'endpoint':probe.url,'count':len(hashes),'null_count':sum(t is None for t in txs),'hashes':hashes}),flush=True)
except Exception as e:
    print(json.dumps({'publicnode_probe':True,'endpoint':probe.url,'error':str(e),'type':type(e).__name__}),flush=True)
```

Recent-candidate probe:

```python
import json,urllib.request
from rpc import Rpc
url='https://eth.blockscout.com/api/v2/addresses/0x16A503C901615E6e21AA78cFcaFf11822fc2b617/transactions?filter=from'
r=urllib.request.Request(url,headers={'user-agent':'cp2c-independent-qa','accept':'application/json'})
with urllib.request.urlopen(r,timeout=25) as f: page=json.load(f)
items=page['items']; chosen=items[::5]
for endpoint in ['https://ethereum-rpc.publicnode.com','https://eth.drpc.org']:
    rpc=Rpc(endpoint,timeout=15,retries=0,batch_size=5)
    try:
        txs=rpc.batch([('eth_getTransactionByHash',[i['hash']]) for i in chosen])
        print(json.dumps({'endpoint':endpoint,'hashes':[i['hash'] for i in chosen],'blocks':[i['block_number'] for i in chosen],'null_count':sum(t is None for t in txs),'count':len(txs)}),flush=True)
    except Exception as e:
        print(json.dumps({'endpoint':endpoint,'error':str(e)}),flush=True)
```

## Appendix D — additional mutation runners

Save the first block as `/private/tmp/cp2c-qa-1/falsify-own.py` and run it **before** evidence-plane writes (it asserts clean porcelain).

```python
import os, subprocess, pathlib
root=pathlib.Path.cwd(); py=str(root/'reconcile/.venv/bin/python')
env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':str(root/'reconcile')+':'+str(root/'reconcile/tests')}
rows=[('Q12', 'reconcile/checks/m12_wallet_burn.py','bal < w.balance_page','bal <= w.balance_page','test_balance_exact_boundaries'),('Q8','reconcile/checks/m8_supply_floor.py','self.measured = False','self.measured = True','test_m8_restart_midday_uses_new_safe_balance'),('Q13','reconcile/checks/m13_retired_keys.py','if bal > prev:','if bal - prev > 1:','test_m13_one_wei_is_incoming_value')]
for name,path,old,new,test in rows:
    p=root/path; original=p.read_text();assert original.count(old)==1
    def run():
        r=subprocess.run([py,'-m','pytest','-q','/private/tmp/cp2c-qa-1/test_independent.py','-k',test],env=env,capture_output=True,text=True)
        return (r.returncode, r.stdout.splitlines()[-1])
    before=run()
    try:
        p.write_text(original.replace(old,new));red=run()
    finally:
        p.write_text(original)
    restored=run()
    st=subprocess.run(['git','status','--porcelain'],capture_output=True,text=True,check=True).stdout
    print(name,before,red,restored,'porcelain='+repr(st),flush=True)
    assert before[0]==0 and red[0]==1 and restored[0]==0 and not st
```

Save the second as `/private/tmp/cp2c-qa-1/failopen_plugin.py`. For each mode run the command below; expected mutation exit 1 / three failures, then remove `-p failopen_plugin` for exit 0 / three passes.

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/reconcile:$PWD/reconcile/tests:/private/tmp/cp2c-qa-1" reconcile/.venv/bin/python -m pytest -q -p failopen_plugin /private/tmp/cp2c-qa-1/test_independent.py -k shorter --tb=no
QA_HEALTH_ONLY=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/reconcile:$PWD/reconcile/tests:/private/tmp/cp2c-qa-1" reconcile/.venv/bin/python -m pytest -q -p failopen_plugin /private/tmp/cp2c-qa-1/test_independent.py -k shorter --tb=no
```

```python
import os
import pytest
from checks.m12_wallet_burn import M12WalletBurn
from checks.m8_supply_floor import M8SupplyFloor
from checks.m13_retired_keys import M13RetiredKeys
@pytest.fixture(autouse=True)
def failopen(monkeypatch):
    for cls in [M12WalletBurn,M8SupplyFloor,M13RetiredKeys]:
        old=cls.run
        def changed(self,ctx,block,original=old):
            if ctx.reorg_ancestor is not None:
                if os.environ.get('QA_HEALTH_ONLY')=='1':
                    ctx.health('qa-unknown','Missing input in reorg',dedup='qa-unknown')
                return []
            return original(self,ctx,block)
        monkeypatch.setattr(cls,'run',changed)
```
