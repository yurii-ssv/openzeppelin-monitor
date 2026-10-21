"""Live protocol parameters (spec §1: read them from the chain at start and on change events).

Read through SSVNetworkViews (ISSVViews at the pin) plus one proxy storage word, all at one block.
The worker reloads them, and calls every check's `prepare`, when the scan stores a parameter event
(eb_events.PARAM_KINDS, spec M15). Values are ints; `oracles` maps active oracle id -> address.

  reconcile/.venv/bin/python reconcile/params.py --probe --rpc "$MAINNET_RPC_URL"   # LIVE read
"""

from __future__ import annotations

import argparse
import json
import sys

from eb_storage import EB_LATEST_COMMITTED, decode_latest_committed, keccak, u256

# name -> Views getter (no arguments, one static word unless noted)
GETTERS = {
    "network_fee": "getNetworkFee()",
    "network_fee_ssv": "getNetworkFeeSSV()",
    "liquidation_threshold_period": "getLiquidationThresholdPeriod()",
    "liquidation_threshold_period_ssv": "getLiquidationThresholdPeriodSSV()",
    "minimum_liquidation_collateral": "getMinimumLiquidationCollateral()",
    "minimum_liquidation_collateral_ssv": "getMinimumLiquidationCollateralSSV()",
    "operator_fee_increase_limit": "getOperatorFeeIncreaseLimit()",
    "maximum_operator_fee": "getMaximumOperatorFee()",
    "minimum_operator_eth_fee": "getMinimumOperatorEthFee()",
    "validators_per_operator_limit": "getValidatorsPerOperatorLimit()",
    "quorum_bps": "getQuorumBps()",
    "cooldown_duration": "cooldownDuration()",
    "active_oracle_ids": "getActiveOracleIds()",  # uint32[4]
}
GET_ORACLE = "getOracle(uint32)"


def selector(signature: str) -> bytes:
    return keccak(signature.encode())[:4]


class Params:
    """Attribute and item access to the last loaded values; `block` is where they were read."""

    def __init__(self, views: str | None, proxy: str):
        self.views, self.proxy = views, proxy
        self.values: dict = {}
        self.block: int | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.views)

    def load(self, chain, block: int) -> dict:
        names = list(GETTERS)
        raw = chain.call_many([(self.views, selector(GETTERS[n])) for n in names], block)
        values = {}
        for n, out in zip(names, raw):
            words = [int.from_bytes(out[i : i + 32], "big") for i in range(0, len(out), 32)]
            if not words:
                raise ValueError(f"{GETTERS[n]} returned no data")
            values[n] = words[:4] if n == "active_oracle_ids" else words[0]
        ids = [i for i in values["active_oracle_ids"] if i]
        addrs = chain.call_many([(self.views, selector(GET_ORACLE) + u256(i)) for i in ids], block) if ids else []
        values["oracles"] = {i: "0x" + a[12:32].hex() for i, a in zip(ids, addrs)}
        latest, min_blocks = decode_latest_committed(chain.storage(self.proxy, [EB_LATEST_COMMITTED], block)[0])
        values["min_blocks_between_updates"] = min_blocks
        self.values, self.block = values, block
        return values

    def __getattr__(self, name):
        try:
            return self.__dict__["values"][name]
        except KeyError:
            raise AttributeError(f"parameter {name!r} is not loaded") from None

    def __getitem__(self, name):
        return self.values[name]

    def get(self, name, default=None):
        return self.values.get(name, default)


def main(argv=None) -> int:
    from rpc import Chain, Rpc

    ap = argparse.ArgumentParser(description="Read the live protocol parameters once (read-only).")
    ap.add_argument("--probe", action="store_true", required=True)
    ap.add_argument("--rpc", required=True)
    ap.add_argument("--views", default="0xafE830B6Ee262ba11cce5F32fDCd760FFE6a66e4")
    ap.add_argument("--proxy", default="0xDD9BC35aE942eF0cFa76930954a156B3fF30a4E1")
    args = ap.parse_args(argv)
    rpc = Rpc(args.rpc, timeout=30, retries=3, batch_size=10)
    block = rpc.block_number()
    chain = Chain(rpc, block)
    p = Params(args.views, args.proxy)
    p.load(chain, block)
    print(json.dumps({"block": block, "params": {k: v for k, v in p.values.items()},
                      "rpc_calls": dict(chain.counts)}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
