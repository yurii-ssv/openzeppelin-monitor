"""Shared test harness for checks (checks/README.md "Test helpers").

  chain, w = make_worker(tmp_path, only=["m3"], checks={"m3": {"n_blocks": 50}})   # registered checks
  chain, w = make_worker(tmp_path, [MyStub()])                                      # explicit instances
  advance(w)                      # one worker step without the RPC-failure wrapper: reorg, scan, inbox, checks,
                                  # re-alerts/digest, delivery; exceptions surface in the test
  outbox(w, "severity='page'")    # outbox rows as dicts, in insertion order
  stub_params(chain, liquidation_threshold_period=50_000)   # SSVNetworkViews getters on the fake chain

The worker starts at the fake chain's sealed block, confirmations 0, record mode, a capturing
notifier, a FakeClock, and no SSV API (inventory from logs + storage unless `inventory=` says otherwise).
"""

from __future__ import annotations

from fakechain import PROXY, FakeChain, FakeClock, FakeRpc

import checks as registry
from eb_reconcile import DB, DEFAULTS, Config, Worker
from eb_storage import EB_LATEST_COMMITTED, cluster_id, u256

VIEWS = "0xafe830b6ee262ba11cce5f32fdcd760ffe6a66e4"


class Capture:
    """Notifier that keeps every delivered message: (severity, key, text, route)."""

    def __init__(self):
        self.sent: list[tuple[str, str, str, str]] = []

    def send(self, severity, text, key, route=None):
        self.sent.append((severity, key, text, route))
        return "captured"


def make_worker(tmp_path, stubs=None, *, only=None, chain=None, clock=None, notifier=None, **over):
    """A bootstrapped Worker on a FakeChain. `stubs`: check instances; `only`: ids of registered checks
    to build from the registry (config from `checks=`); neither: every registered check."""
    chain = chain or FakeChain()
    raw = {
        "chain_id": 1, "proxy": PROXY, "activation_block": chain.sealed, "start_block": chain.sealed,
        "rpc_url_env": "UNUSED", "confirmations": 0, "max_log_range": 10_000, "db_path": str(tmp_path / "w.sqlite"),
        "alerts": {"mode": "record", "label": "test", "digest_interval_s": 0},
        "inventory": {"api_url": None},
    }
    raw.update(over)
    if only is not None:
        checks_cfg = {cid: dict(c) for cid, c in raw.get("checks", {}).items()}
        for cid in registry.REGISTRY:
            checks_cfg[cid] = {**checks_cfg.get(cid, {}), "enabled": cid in only}
        raw["checks"] = checks_cfg
    cfg = Config({**DEFAULTS, **raw})
    w = Worker(cfg, FakeRpc(chain), DB(cfg.db_path), notifier or Capture(), clock=clock or FakeClock(),
               checks=stubs)
    w.bootstrap()
    return chain, w


def advance(w):
    w.check_reorg()
    w.scan()
    w.poll_inbox()
    w.run_checks()
    w.housekeeping()
    w.deliver()


def outbox(w, where="1=1"):
    return [dict(r) for r in w.db.conn.execute(f"SELECT * FROM outbox WHERE {where} ORDER BY created_at, rowid")]


def stub_params(chain, views=VIEWS, oracles=(1, 2, 3, 4), **over):
    """Answer every params.GETTERS call (spec §5 dated values unless overridden), effective from the
    pending block on. Call again with new values and emit the matching parameter event to model a change."""
    from params import GET_ORACLE, GETTERS, selector

    values = {"network_fee": 3_366_600_000, "network_fee_ssv": 1, "liquidation_threshold_period": 42_960,
              "liquidation_threshold_period_ssv": 100_240, "minimum_liquidation_collateral": 325_000_000_000_000,
              "minimum_liquidation_collateral_ssv": 1, "operator_fee_increase_limit": 1000,
              "maximum_operator_fee": 1, "minimum_operator_eth_fee": 1, "validators_per_operator_limit": 3000,
              "quorum_bps": 7500, "cooldown_duration": 1}
    values.update(over)
    ids = list(oracles) + [0] * (4 - len(oracles))
    for name, sig in GETTERS.items():
        if name == "active_oracle_ids":
            chain.stub_call(views, selector(sig), b"".join(u256(i) for i in ids))
        else:
            chain.stub_call(views, selector(sig), u256(values[name]))
    for i in oracles:
        chain.stub_call(views, selector(GET_ORACLE) + u256(i), u256(0xC0 + i))
    chain.write(EB_LATEST_COMMITTED, values.get("min_blocks_between_updates", 0) << 64)


def api_row(owner, ops, count, nfi, index, active, balance, migrated=True):
    """One SSV API v4 /clusters row (the fields inventory.py reads)."""
    return {"clusterId": "0x" + cluster_id(owner, ops).hex(), "ownerAddress": owner, "operators": list(ops),
            "validatorCount": count, "networkFeeIndex": str(nfi), "index": str(index), "active": active,
            "ethBalance": str(balance) if migrated else "0", "balance": "0" if migrated else str(balance),
            "migrated": migrated, "isLiquidated": not active}
