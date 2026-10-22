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
