"""CP2e fixtures: legacy SSV clusters, their Views answers and the M7 ledger on the FakeChain.

Legacy clusters live in storage only (pre-activation, `MiniSSV.legacy_ssv_cluster`) and are listed by a fake
SSV API v4 (`migrated=false` rows), as on mainnet. Views answers are stubbed per exact calldata, encoded here
with the fakechain's own ABI encoder (independent of the check's), effective from the pending block.

Spec dated values (spec §5, 2026-09-02): SSV liquidation threshold 100,240 blocks; the M6 baseline had
198 clusters with balance (155 burning). Nothing here is read by a check except through the chain.
"""

from __future__ import annotations

from dataclasses import dataclass

from fakechain import PROXY, FakeChain, FakeClock, MiniSSV, _enc, _topic_addr
from harness import VIEWS, advance, api_row, make_worker, stub_params

from eb_storage import MAIN_BASE, cluster_id, keccak, namespace, u256

CLUSTER_T = "(uint32,uint64,uint64,bool,uint256)"
SEL = {name: "0x" + keccak(f"{name}(address,uint64[],{CLUSTER_T})".encode())[:4].hex()
       for name in ("getBalanceSSV", "isLiquidatableSSV", "getBurnRateSSV")}
SSV_TOKEN = "0x9d65ff81a3c488d585bbfb0bfe3c7707c7917f54"  # spec §5
CSSV = "0xe018d31f120a637828f46afd6c64ec099d960546"  # spec §5
PERIOD_SSV = 100_240  # spec §5: liquidation threshold (SSV), 2026-09-02
SSV = 10**18
BURN = 10**12  # SSV wei per block for a test cluster (≈ 2.6 SSV/yr)
MIN_COLL_SSV = 10**16  # test value, below PERIOD_SSV × BURN so the period rule sets the threshold
API = "https://api.invalid/api/v4/mainnet"
TOKEN_SLOT = MAIN_BASE + 7
LAST_OP_SLOT = MAIN_BASE + 8
DAO_SLOT = namespace("ssv.network.storage.protocol")  # StorageProtocol word 0: daoValidatorCount in bits 32..63


class LegacySSV(MiniSSV):
    """MiniSSV that also keeps the SSV-side `daoValidatorCount` in storage, as ProtocolLib.updateDAOSSV does on
    chain: the validators of the active legacy clusters (a liquidation, migration or validator removal lowers it).
    `dao_offset` models a cluster the fixture's API and logs do not know about."""

    dao_offset = 0

    def _persist(self, cid, st) -> None:
        super()._persist(cid, st)
        self.sync_dao()

    def sync_dao(self) -> None:
        count = sum(st["count"] for st in self.clusters.values() if st["version"] == "SSV" and st["active"])
        self.c.write(DAO_SLOT, (count + self.dao_offset) << 32)


@dataclass
class Ref:
    owner: str
    ops: tuple
    count: int

    @property
    def cid(self) -> str:
        return "0x" + cluster_id(self.owner, self.ops).hex()


class LegacyWorld:
    """A FakeChain with legacy clusters, an SSV API serving them, live parameters and a worker."""

    def __init__(self, tmp_path, only=("m6",), checks=None, clock=None, chain=None, params=None, **over):
        self.tmp_path = tmp_path
        self.chain = chain or FakeChain()
        self.ssv = LegacySSV(self.chain)
        self.clock = clock or FakeClock()
        self.rows: list[dict] = []
        self.api_total: int | None = None  # pagination.total the fake API reports (None: the rows it serves)
        self.api_fault: str | None = None  # None | "empty" | "empty_total0" | "short" | "error" | "no_total"
        self.refs: list[Ref] = []
        self.only, self.checks, self.over = list(only), checks or {}, over
        stub_params(self.chain, **{"liquidation_threshold_period_ssv": PERIOD_SSV,
                                   "minimum_liquidation_collateral_ssv": MIN_COLL_SSV, **(params or {})})
        self.chain.mine()
        self.w = None

    def start(self, **extra):
        """Build the worker (after the clusters exist); `extra` overrides make_worker settings."""
        kw = {"views": VIEWS, "inventory": {"api_url": API}, **self.over, **extra}
        _, self.w = make_worker(self.tmp_path, only=self.only, chain=self.chain, clock=self.clock,
                                checks=self.checks, **kw)
        self.w.inventory.http_get = self.http_get
        return self.w

    def restart(self):
        """A new process on the same database (V2x.3)."""
        self.w = self.start(start_block=self.w.cfg.start_block, activation_block=self.w.cfg.activation_block)
        return self.w

    def http_get(self, url):
        """The fake SSV API v4: every row on one page with its `pagination.total`; `api_fault` serves an empty
        listing, a listing short of its first row (both with the true total), a self-consistent empty listing
        (total 0), an error, or no total."""
        rows = [r for r in self.rows if r is not None]
        total = len(rows) if self.api_total is None else self.api_total
        if self.api_fault == "error":
            raise OSError("SSV API unreachable")
        if self.api_fault in ("empty", "empty_total0"):
            rows, total = [], 0 if self.api_fault == "empty_total0" else total
        elif self.api_fault == "short":
            rows = rows[1:]
        pagination = {"pages": 1} if self.api_fault == "no_total" else {"pages": 1, "total": total}
        return {"clusters": rows, "pagination": pagination}

    # ---- clusters

    def legacy(self, owner: str, ops=(1, 2, 3, 4), count: int = 1, api: bool = True) -> Ref:
        ref = Ref(owner.lower(), tuple(ops), count)
        self.ssv.legacy_ssv_cluster(ref.owner, ref.ops, count)
        st = self.ssv.clusters[cluster_id(ref.owner, ref.ops)]
        if api:
            self.rows.append(api_row(ref.owner, ref.ops, count, st["nfi"], st["index"], st["active"], st["balance"],
                                     migrated=False))
        self.refs.append(ref)
        return ref

    def add_validators(self, ref: Ref, n: int = 1) -> str:
        """ValidatorAdded on a legacy cluster with its new tuple (logs + storage), as `MiniSSV.register` does for an
        ETH cluster; the API row is left stale, so the log tuple is the one that validates."""
        raw = cluster_id(ref.owner, ref.ops)
        st = self.ssv.clusters[raw]
        st["count"] += n
        ref.count = st["count"]
        self.ssv._persist(raw, st)
        tx = self.chain.new_tx(ref.owner)
        for i in range(n):
            self.chain.emit(tx, "ValidatorAdded", [_topic_addr(ref.owner)],
                            _enc(["uint64[]", "bytes", "bytes", "cluster"],
                                 [list(ref.ops), (7_000 + st["count"] + i).to_bytes(48, "big"), b"", self.tuple(ref)]))
        return tx

    def tuple(self, ref: Ref):
        st = self.ssv.clusters[cluster_id(ref.owner, ref.ops)]
        return self.ssv._cluster(st)

    def calldata(self, name: str, ref: Ref) -> str:
        return SEL[name] + _enc(["uint", "uint64[]", "cluster"],
                                [int(ref.owner, 16), list(ref.ops), self.tuple(ref)]).hex()

    def views(self, ref: Ref, balance: int, liquidatable: bool = False, burn: int = BURN, revert: bool = False):
        """Views answers for this cluster's current tuple, from the pending block on."""
        for name, val in (("getBalanceSSV", balance), ("isLiquidatableSSV", int(liquidatable)),
                          ("getBurnRateSSV", burn)):
            if revert and name == "getBalanceSSV":
                self.chain.stub_call(VIEWS, self.calldata(name, ref), revert="0x12e04c87")
            else:
                self.chain.stub_call(VIEWS, self.calldata(name, ref), u256(val))

    def deficit(self, blocks: int, burn: int = BURN, period: int = PERIOD_SSV) -> int:
        """A live balance `blocks` blocks of burn below the liquidation threshold period × burn."""
        return period * burn - blocks * burn

    # ---- M7 ledger

    def ledger(self, proxy_ssv: int, cssv_supply: int, network: int = 0, operators: dict | None = None,
               op_default: int = 0, last_op: int | None = None, token: str = SSV_TOKEN):
        operators = operators or {}
        last_op = max(operators, default=0) if last_op is None else last_op
        self.chain.write(TOKEN_SLOT, int(token, 16))
        self.chain.write(LAST_OP_SLOT, last_op)
        self.chain.stub_call(token, "0x70a08231" + "00" * 12 + PROXY[2:], u256(proxy_ssv))
        self.chain.stub_call(CSSV, "0x18160ddd", u256(cssv_supply))
        self.chain.stub_call(VIEWS, "0x" + keccak(b"getNetworkEarningsSSV()")[:4].hex(), u256(network))
        sel = "0x" + keccak(b"getOperatorEarningsSSV(uint64)")[:4].hex()
        self.chain.stub_call(VIEWS, sel, u256(op_default))
        for i, v in operators.items():
            self.chain.stub_call(VIEWS, sel + u256(i).hex(), u256(v))

    def unstake(self, user: str, amount: int, withdraw: bool = False) -> str:
        if withdraw:
            return self.chain.emit_event("0x" + keccak(b"UnstakedWithdrawn(address,uint256)").hex(),
                                         [_topic_addr(user)], u256(amount))
        return self.chain.emit_event("0x" + keccak(b"UnstakeRequested(address,uint256,uint256)").hex(),
                                     [_topic_addr(user)], u256(amount) + u256(1_900_000_000))

    # ---- time and chain

    def sweep(self, seconds: float = 3600, blocks: int = 1):
        """Let the check's interval elapse, seal `blocks` blocks and run one worker step."""
        self.clock.advance(seconds)
        self.chain.mine(blocks)
        advance(self.w)

    def fork(self, from_block: int):
        """A reorg replacing every block from `from_block`: logs, storage writes and the call stubs and
        balances that took effect there (the FakeChain keeps stubs across a reorg; the new chain does not)."""
        for k, entries in list(self.chain.call_stubs.items()):
            self.chain.call_stubs[k] = [e for e in entries if e[0] < from_block]
        for a, entries in list(self.chain.balances.items()):
            self.chain.balances[a] = [e for e in entries if e[0] < from_block]
        self.chain.reorg(from_block)


def owner(i: int) -> str:
    return "0x" + f"{i:040x}"


def param_change(chain, **values):
    """M15-style parameter change: new getter values plus the event that makes the worker re-read them."""
    stub_params(chain, **{"liquidation_threshold_period_ssv": PERIOD_SSV,
                          "minimum_liquidation_collateral_ssv": MIN_COLL_SSV, **values})
    chain.emit_event("LiquidationThresholdPeriodSSVUpdated", [], u256(values.get("liquidation_threshold_period_ssv",
                                                                                 PERIOD_SSV)))
