"""CP2b fixtures: an oracle model on FakeChain (votes, commits, rotations, inbox lines) and worker builders.

`OracleChain` follows SSVDAO.commitRoot at the pin (f7ac487): a vote adds totalStaked/oracleCount to the
(blockNum, root) commitment, `WeightedRootProposed(root, blockNum, accumulatedWeight, threshold, oracleId,
oracle)` is emitted, and the same transaction emits `RootCommitted` once the weight reaches
threshold = totalStaked * quorumBps / 10000; a vote for a blockNum at or below the latest committed one, or a
second vote of a slot on the same (blockNum, root), would revert (asserted here: a test never emits those logs).
Dated values (spec §5, 2026-10-13): the four slot addresses and quorumBps 7500.
"""

from __future__ import annotations

import json

from fakechain import PROXY, FakeChain, FakeClock, MiniSSV, _topic_addr, keys  # noqa: F401
from harness import VIEWS, make_worker, stub_params

from checks.m9_oracle_common import (
    COMMIT_ROOT_SELECTOR,
    UPDATE_CLUSTER_BALANCE_SELECTOR,
    WRP,
    WRP_SIGNATURE,
)
from eb_events import register_event
from eb_storage import keccak, u256
from params import GET_ORACLE, selector

register_event(WRP, WRP_SIGNATURE)  # what registry.build does for an enabled M9/M10

SLOTS = {  # spec §5 (2026-10-13)
    1: "0xc61f7bd9ee5a3d011caf47aa0e5411f720593920",
    2: "0xc07332e05cec1c4896555a6d10361233fdf14422",
    3: "0x28bea5b242362974d5ddb8f17a1e0e525446960b",  # Ethernodes
    4: "0x16a503c901615e6e21aa78cfcaff11822fc2b617",  # SSV (also the EB updater)
}
QUORUM_BPS = 7500
SUPPLY = 4 * 10**24  # cSSV totalSupply (any multiple of the oracle count)
HOUR = 3600
ERR = {name: "0x" + keccak(sig.encode())[:4].hex() for name, sig in {
    "NotOracle": "NotOracle()", "AlreadyVoted": "AlreadyVoted()", "StaleBlockNumber": "StaleBlockNumber()",
    "ZeroCSSVSupply": "ZeroCSSVSupply()", "StaleUpdate": "StaleUpdate()",
    "IncorrectClusterState": "IncorrectClusterState()", "MustUseLatestRoot": "MustUseLatestRoot()"}.items()}


class TimedChain(FakeChain):
    """FakeChain whose headers carry a `timestamp` (the oracle rules use chain time). A block is stamped when it is
    sealed: 12 s after its parent, or later when the shared clock says more time has passed (missed slots, a chain
    that stopped). So mining without moving the clock is still 12 s per block, `tick` keeps chain and worker time
    together, and a clock-only `tick` is a chain that produces no block. `slot_s` > 12 models missed slots."""

    def __init__(self, start_block: int = 1000, clock=None, chain_id: int = 1):
        super().__init__(start_block, chain_id)
        self.clock = clock or FakeClock()
        self.slot_s = 12
        self.times = {self.sealed: int(self.clock())}  # older blocks: 12 s apart below the first stamp
        self.replaced: dict[int, int] = {}  # heights a reorg replaced: the new block reuses the height's slot time

    def ts(self, n: int) -> int:
        if n in self.times:
            return self.times[n]
        first = min(self.times)
        assert n < first, f"block {n} is not sealed"
        return self.times[first] - 12 * (first - n)

    def mine(self, n: int = 1) -> int:
        for _ in range(n):
            sealed = self.number
            slot = self.replaced.pop(sealed, None)
            self.times[sealed] = max(self.ts(sealed - 1) + 1, slot) if slot is not None else max(
                self.ts(sealed - 1) + self.slot_s, int(self.clock()))
            super().mine(1)
        return self.number

    def reorg(self, from_block: int) -> None:
        keep = {b: t for b, t in self.times.items() if b < from_block}
        self.replaced.update({b: t for b, t in self.times.items() if b >= from_block})
        super().reorg(from_block)  # re-seals from_block - 1 through mine(): keep its stamp
        self.times = keep

    def _handle(self, m: str, p: list):
        res = super()._handle(m, p)
        if m == "eth_getBlockByNumber" and res is not None:
            res = dict(res, timestamp=hex(self.ts(int(res["number"], 16))))
        return res

    def elapse(self, blocks: int, seconds_per_block: float = 12) -> None:
        """Blocks with the clock moving `seconds_per_block` per block (a worker that is not stepping: downtime)."""
        for _ in range(blocks):
            self.clock.advance(seconds_per_block)
            self.mine()


class OracleChain:
    def __init__(self, chain: FakeChain | None = None, slots=None, quorum_bps=QUORUM_BPS, active=(1, 2, 3, 4)):
        self.c = chain or TimedChain()
        self.slots = dict(SLOTS if slots is None else slots)
        self.quorum_bps = quorum_bps
        self.active = tuple(active)
        self.acc: dict[tuple[int, str], int] = {}
        self.voted: set[tuple[int, str, int]] = set()
        self.latest = 0
        self.stub()

    def stub(self) -> None:
        """Answer the Views getters (live parameters) from the pending block on."""
        stub_params(self.c, quorum_bps=self.quorum_bps, oracles=self.active)
        for i, addr in self.slots.items():
            self.c.stub_call(VIEWS, selector(GET_ORACLE) + u256(i), u256(int(addr, 16)))

    def set_quorum(self, bps: int) -> None:
        """The DAO changes quorumBps: new getter answers plus the QuorumUpdated event (a parameter event)."""
        self.quorum_bps = bps
        self.stub()
        self.c.emit_event("QuorumUpdated", [], u256(bps))
        self.c.mine()

    def set_active(self, active) -> None:
        self.active = tuple(active)
        self.stub()
        self.c.emit_event("QuorumUpdated", [], u256(self.quorum_bps))  # any parameter event triggers the reload
        self.c.mine()

    @staticmethod
    def root(ref: int, variant: int = 0) -> str:
        return "0x%064x" % ((0xA11CE << 200) + ref * 16 + variant)

    def vote(self, ref: int, slot: int, variant: int = 0, oracle: str | None = None, mine: bool = True) -> str:
        root = self.root(ref, variant)
        assert ref > self.latest, "would revert StaleBlockNumber"
        assert ref <= self.c.number, "would revert FutureBlockNumber"
        assert (ref, root, slot) not in self.voted, "would revert AlreadyVoted"
        self.voted.add((ref, root, slot))
        total = SUPPLY - SUPPLY % 4
        acc = self.acc[(ref, root)] = self.acc.get((ref, root), 0) + total // 4
        threshold = total * self.quorum_bps // 10_000
        sender = oracle or self.slots[slot]
        tx = self.c.new_tx(sender)
        self.c.emit_event(WRP, [root, "0x%064x" % ref], u256(acc) + u256(threshold) + u256(slot) + u256(int(sender, 16)),
                          tx=tx)
        if acc >= threshold:
            self.c.emit_event("RootCommitted", [root, "0x%064x" % ref], b"", tx=tx)
            self.latest = ref
        if mine:
            self.c.mine()
        return tx

    def reorg(self, from_block: int, latest: int, refs=()) -> None:
        """chain.reorg(from_block) plus the model's rewind: latest committed blockNum, votes of `refs` forgotten."""
        self.c.reorg(from_block)
        self.latest = latest
        self.voted = {v for v in self.voted if v[0] not in refs}
        self.acc = {k: a for k, a in self.acc.items() if k[0] not in refs}
        self.stub()

    def committed(self, ref: int) -> bool:
        return self.latest >= ref

    def round(self, ref: int | None = None, voters=(1, 2, 3, 4), variants: dict | None = None, gap: int = 1) -> int:
        """Votes in slot order until the round commits (the routine fourth vote is skipped once quorum is on chain).
        `variants`: slot -> root variant (a dissenting build). Returns the round's blockNum."""
        if ref is None:
            ref = self.c.number - 1
        variants = variants or {}
        for slot in voters:
            if self.committed(ref):
                break
            self.vote(ref, slot, variants.get(slot, 0))
            if gap > 1:
                self.c.mine(gap - 1)
        return ref

    def replace(self, slot: int, new: str, mine: bool = True) -> str:
        old = self.slots.get(slot, "0x" + "00" * 20)
        tx = self.c.emit_event("OracleReplaced", ["0x%064x" % slot, _topic_addr(old), _topic_addr(new)])
        self.slots[slot] = new
        self.stub()
        if mine:
            self.c.mine()
        return tx


def oracle_worker(tmp_path, ids=("m9",), oc: OracleChain | None = None, clock=None, checks=None, inbox=True,
                  **over):
    """A worker started at the chain's head: the worker clock is the chain's clock, moved up to the head's
    timestamp (the chain ran ahead while blocks were mined without the clock)."""
    oc = oc or OracleChain()
    if clock is not None:
        oc.c.clock = clock
    oc.c.mine(100)
    clock = oc.c.clock
    clock.now = max(clock.now, oc.c.ts(oc.c.sealed))
    kw = {}
    if inbox:
        path = tmp_path / "failed-tx.jsonl"
        if not path.exists():
            path.write_text("")
        kw["inbox"] = {"path": str(path)}
    _, w = make_worker(tmp_path, only=list(ids), chain=oc.c, clock=clock, views=VIEWS,
                       checks=checks or {}, **kw, **over)
    return oc, w


def restart(tmp_path, w, oc: OracleChain, ids=("m9",), checks=None, inbox=True, **over):
    """A new process on the same database (README §8 V2x.3 pattern)."""
    kw = {}
    if inbox:
        kw["inbox"] = {"path": str(tmp_path / "failed-tx.jsonl")}
    _, w2 = make_worker(tmp_path, only=list(ids), chain=oc.c, clock=w.clock, views=VIEWS, checks=checks or {},
                        start_block=w.cfg.start_block, activation_block=w.cfg.activation_block,
                        confirmations=w.cfg.confirmations, **kw, **over)
    return w2


def inbox_line(chain: FakeChain, tx: str, sender: str, sel: str, function: str, block: int | None = None) -> str:
    block = chain.sealed if block is None else block
    return json.dumps({"schema": 1, "tx_hash": tx, "block": block, "block_hash": chain.hash(block), "from": sender,
                       "to": PROXY, "selector": sel, "function": function, "status": "failure",
                       "monitor": "SSV Failed Tx Inbox", "observed_at": "2026-10-21T00:00:00Z"}) + "\n"


def failed_commit_root(oc: OracleChain, path, sender: str, error: str, ref: int = 1) -> str:
    """A reverted commitRoot from `sender` in the pending block, appended to the inbox file once sealed."""
    data = COMMIT_ROOT_SELECTOR + "%064x" % (0xBAD << 200) + "%064x" % ref
    oc.c.stub_call(PROXY, data, revert=ERR[error])
    oc.c.mine()  # the inbox resolves the revert with eth_call at block - 1
    tx = oc.c.add_tx(sender, PROXY, data)
    oc.c.mine()
    with open(path, "a") as fh:
        fh.write(inbox_line(oc.c, tx, sender, COMMIT_ROOT_SELECTOR, "commitRoot(bytes32,uint64)"))
    return tx


def update_calldata(ref: int, owner: str, ops, cluster, eb: int) -> str:
    """updateClusterBalance(blockNum, owner, operatorIds, cluster, effectiveBalance, proof=[])."""
    head = [u256(ref), bytes.fromhex(owner[2:]).rjust(32, b"\0"), u256(10 * 32)]
    head += [u256(int(x)) for x in cluster.as_tuple()] + [u256(eb), u256(10 * 32 + 32 * (1 + len(ops)))]
    tail = u256(len(ops)) + b"".join(u256(o) for o in ops) + u256(0)
    return UPDATE_CLUSTER_BALANCE_SELECTOR + (b"".join(head) + tail).hex()


def failed_update(chain: FakeChain, path, sender: str, error: str, ref: int, owner="0x" + "aa" * 20, ops=(1, 2, 3, 4),
                  cluster=None, eb: int = 64) -> str:
    from eb_storage import Cluster

    cluster = cluster or Cluster(1, 0, 0, True, 10**18)
    data = update_calldata(ref, owner, ops, cluster, eb)
    chain.stub_call(PROXY, data, revert=ERR[error])
    chain.mine()
    tx = chain.add_tx(sender, PROXY, data)
    chain.mine()
    with open(path, "a") as fh:
        fh.write(inbox_line(chain, tx, sender, UPDATE_CLUSTER_BALANCE_SELECTOR,
                            "updateClusterBalance(uint64,address,uint64[],(uint32,uint64,uint64,bool,uint256),uint32,"
                            "bytes32[])"))
    return tx


def tick(w, oc: OracleChain | None = None, blocks: int = 0, seconds: float = 0):
    """Mine `blocks` (12 s of worker clock each), move the clock by `seconds` more, then run one worker step."""
    if oc is not None and blocks:
        oc.c.mine(blocks)
    if seconds or blocks:
        w.clock.advance(seconds + 12 * blocks)
    w.step()
    return w


def incidents(w, check):
    return [dict(r) for r in w.db.conn.execute("SELECT * FROM incidents WHERE check_id=? ORDER BY rowid", (check,))]


def open_keys(w, check) -> dict[str, str]:
    return {r["key"]: r["severity"] for r in incidents(w, check) if r["state"] == "open"}


def sent(w, check, where="1=1"):
    """Outbox rows of a check (health messages excluded), each with `ikey` = the incident key it belongs to."""
    return [dict(r) for r in w.db.conn.execute(
        f"SELECT o.*, i.key AS ikey FROM outbox o LEFT JOIN incidents i ON i.id = o.incident_id "
        f"WHERE o.check_id=? AND o.severity != 'health' AND "
        f"{where.replace('kind', 'o.kind').replace('severity', 'o.severity')} "
        f"ORDER BY o.created_at, o.rowid", (check,))]


# ---------------------------------------------------------------- M11: clusters, updater, proof API


OPS = (1, 2, 3, 4)


class FakeProofApi:
    """`{proof_api}/proof/{clusterId}`: the leaf EB at `ref`, `{"error": "cluster not found"}` for unknown ids
    (the live API's answer, measured 2026-10-21), ProofApiError while `down`."""

    def __init__(self):
        self.leaves: dict[str, int] = {}
        self.ref: int | None = None
        self.down = False
        self.calls = 0

    def get(self, url: str, timeout: float) -> dict:
        from checks.m11_updater_liveness import ProofApiError

        self.calls += 1
        if self.down:
            raise ProofApiError("URLError: [Errno 61] Connection refused")
        cid = url.rsplit("/", 1)[1]
        if cid not in self.leaves:
            return {"error": "cluster not found"}
        return {"clusterId": cid, "effectiveBalance": self.leaves[cid], "proof": [], "merkleRoot": "0x" + "11" * 32,
                "referenceBlock": self.ref}


class UpdaterWorld:
    """`n` active ETH clusters (MiniSSV, one validator each), the oracle slots, an M11 worker with a fake proof API.
    getEffectiveBalance answers `eb` for every cluster (selector stub); the API's leaves default to the same."""

    def __init__(self, tmp_path, n=3, eb=32, checks=None, confirmations=0, **over):
        self.oc = OracleChain()
        self.ssv = MiniSSV(self.oc.c)
        self.owners = ["0x%040x" % (0x3000 + i) for i in range(n)]
        for i, owner in enumerate(self.owners):
            self.ssv.register(owner, OPS, keys(1, 7000 + 10 * i))
        self.cids = ["0x" + cid.hex() for cid in self.ssv.clusters]
        self.api = FakeProofApi()
        self.set_eb(eb)
        cfg = self.cfg = {"proof_api": "https://proof.invalid/api/v1", **(checks or {})}
        self.oc, self.w = oracle_worker(tmp_path, ids=("m11",), oc=self.oc, inbox=True, checks={"m11": cfg},
                                        start_block=1000, activation_block=1000, confirmations=confirmations, **over)
        self.w.checks["m11"].http_get = self.api.get
        self.inbox = tmp_path / "failed-tx.jsonl"
        self.tmp_path = tmp_path

    @property
    def c(self):
        return self.oc.c

    def set_eb(self, eb: int, leaves: dict | None = None) -> None:
        from checks.m11_updater_liveness import GET_EB

        self.c.stub_call(VIEWS, GET_EB, u256(eb))
        self.api.leaves = {cid: eb for cid in self.cids}
        self.api.leaves.update(leaves or {})

    def commit(self) -> int:
        ref = self.c.number - 1
        self.ssv.commit(ref)
        self.api.ref = ref
        return ref

    def update(self, i: int, eb: int = 64, sender: str | None = None) -> str:
        return self.ssv.update(self.owners[i], OPS, eb, sender=sender or SLOTS[4])

    def round(self, updates=(0,), sender=None) -> int:
        ref = self.commit()
        for i in updates:
            self.update(i, eb=32 + 1 + (self.ssv.latest_root % 7), sender=sender)
        return ref

    def close(self, extra: int = 5):
        """Mine past the 60 min window (300 blocks) and step."""
        tick(self.w, self.oc, 300 + extra)
        return self.w

    def fail(self, error: str, ref: int, i: int = 0, eb: int = 64, sender: str | None = None) -> str:
        st = self.ssv.clusters[bytes.fromhex(self.cids[i][2:])]
        return failed_update(self.c, self.inbox, sender or SLOTS[4], error, ref, owner=self.owners[i], ops=OPS,
                             cluster=self.ssv._cluster(st), eb=eb)

    def restart(self, w=None):
        w2 = restart(self.tmp_path, w or self.w, self.oc, ids=("m11",), inbox=True,
                     checks={"m11": self.cfg})
        w2.checks["m11"].http_get = self.api.get
        return w2


# ---------------------------------------------------------------- recorded mainnet rounds (2026-08 outage)

# eth_getLogs of the proxy (WeightedRootProposed, RootCommitted) in [ref+1, ref+200] for the 21 rounds blockNum
# 25,680,166 .. 25,727,993, fetched 2026-10-21 from eth-pokt.nodies.app (archive, 50-block ranges) by
# plans/monitoring-service/workers/cp2b/evidence/fetch_round_logs.py (raw JSON next to it). Rows:
# (block, log index, tx hash, kind, merkleRoot, blockNum, accumulatedWeight, quorum, oracleId, oracle); kind "V" =
# WeightedRootProposed, "C" = RootCommitted (the last four fields 0 / None).
MAINNET_2026_08 = [
    (25680231, 65, "0x41c842b2d008bbe634298e97a9beb30583895248e8158ee91d44432536efd348", "V", "0x9fa5cd1182222e8ff57a9a9c5c5f747d97851a84413e2f166db99b996e1d694e", 25680166, 858563527273615227555075, 2575690581820845682665225, 1, "0xc61f7bd9ee5a3d011caf47aa0e5411f720593920"),
    (25680231, 67, "0x5d6f6c59a3431d81372d7c4475165b4a6a974c9aba9446260b4394ce5369f867", "V", "0x2ff5dbc6446c63d6c1392284af325aa6955695badc92d01f3170061e443a423e", 25680166, 858563527273615227555075, 2575690581820845682665225, 3, "0x28bea5b242362974d5ddb8f17a1e0e525446960b"),
    (25680231, 68, "0x6348886c534fc8a105015fbba7dd010bb351ffaa27bcdf6c9126b96e2a10b072", "V", "0x9fa5cd1182222e8ff57a9a9c5c5f747d97851a84413e2f166db99b996e1d694e", 25680166, 1717127054547230455110150, 2575690581820845682665225, 4, "0x3a98ee5f80268ed91f8a5880d93468b76a9f3bb4"),
    (25680231, 82, "0x80d8f790fe63bf4509aa51310c86eb04b10ed233a44810f98dae6032b17908b8", "V", "0x9fa5cd1182222e8ff57a9a9c5c5f747d97851a84413e2f166db99b996e1d694e", 25680166, 2575690581820845682665225, 2575690581820845682665225, 2, "0xc07332e05cec1c4896555a6d10361233fdf14422"),
    (25680231, 83, "0x80d8f790fe63bf4509aa51310c86eb04b10ed233a44810f98dae6032b17908b8", "C", "0x9fa5cd1182222e8ff57a9a9c5c5f747d97851a84413e2f166db99b996e1d694e", 25680166, 0, 0, 0, None),
    (25682626, 144, "0xf24c7424a1b0438611a6243d1f258fc93cfbaa85e4295ea23d5b8a755621e619", "V", "0xb39409935599caf64ae8a97e4ad84c57b220c3ddd832729d58c759a4446f16fa", 25682561, 861674796204669407368726, 2585024388614008222106178, 4, "0x3a98ee5f80268ed91f8a5880d93468b76a9f3bb4"),
    (25682626, 185, "0x41d094ccd10013295195868050e2d1298bc1d2b7a34e591ac4c479474c0dfc62", "V", "0xb39409935599caf64ae8a97e4ad84c57b220c3ddd832729d58c759a4446f16fa", 25682561, 1723349592409338814737452, 2585024388614008222106178, 1, "0xc61f7bd9ee5a3d011caf47aa0e5411f720593920"),
    (25682626, 186, "0xd3e55b8fd9af8572147e7a5939a4c765096f9b6bd4d21f0f107a658f5dea02c2", "V", "0x27b1d926cbee9f18ab687820ebb056d325c3017a8ae9ad6f393e0abef5090bd2", 25682561, 861674796204669407368726, 2585024388614008222106178, 3, "0x28bea5b242362974d5ddb8f17a1e0e525446960b"),
    (25682626, 187, "0xde0cb08b9991418ae5765111a65ce74433086915e263c8678b4a8762801096f8", "V", "0xb39409935599caf64ae8a97e4ad84c57b220c3ddd832729d58c759a4446f16fa", 25682561, 2585024388614008222106178, 2585024388614008222106178, 2, "0xc07332e05cec1c4896555a6d10361233fdf14422"),
    (25682626, 188, "0xde0cb08b9991418ae5765111a65ce74433086915e263c8678b4a8762801096f8", "C", "0xb39409935599caf64ae8a97e4ad84c57b220c3ddd832729d58c759a4446f16fa", 25682561, 0, 0, 0, None),
    (25685018, 899, "0x4b0f9f44f8eac9c61f26875de405a37ba59dab2f040a9c7f745a7f89600b431f", "V", "0xa91c0efadabd2bde965cc7aaa0afc4668719ea2bdb7271cffd1805f4e2d57a23", 25684953, 861930471266776262366558, 2585791413800328787099674, 3, "0x28bea5b242362974d5ddb8f17a1e0e525446960b"),
    (25685018, 1795, "0x11c09cd7e3fb0aea5e6178379ad649de3ce4d1c7bd4811f084919f14a07ee6a7", "V", "0xb8d49d7576bf6504e4744869125862010337cb8334af5919115acdc620e44920", 25684953, 861930471266776262366558, 2585791413800328787099674, 2, "0xc07332e05cec1c4896555a6d10361233fdf14422"),
    (25685018, 1796, "0xd4439bd884d9b0394c3a5e73e2cac57ae11bc09d66e87119a692f1ae4817f538", "V", "0xb8d49d7576bf6504e4744869125862010337cb8334af5919115acdc620e44920", 25684953, 1723860942533552524733116, 2585791413800328787099674, 1, "0xc61f7bd9ee5a3d011caf47aa0e5411f720593920"),
    (25685018, 1797, "0xddb3d5de0e590552c9940cf3f879e6a9dfc22b005bfe1f4986ade08740b174ef", "V", "0xb8d49d7576bf6504e4744869125862010337cb8334af5919115acdc620e44920", 25684953, 2585791413800328787099674, 2585791413800328787099674, 4, "0x3a98ee5f80268ed91f8a5880d93468b76a9f3bb4"),
    (25685018, 1798, "0xddb3d5de0e590552c9940cf3f879e6a9dfc22b005bfe1f4986ade08740b174ef", "C", "0xb8d49d7576bf6504e4744869125862010337cb8334af5919115acdc620e44920", 25684953, 0, 0, 0, None),
    (25687409, 97, "0x1b32b934cf285291300002af3ed209fec1f17ad9e8098814138bcc071844b323", "V", "0x2f3e23cd622fe7e4ae0e1871e953b52f1b3505bb648458c83296f5a453aef9c9", 25687345, 861936897298276262366558, 2585810691894828787099674, 1, "0xc61f7bd9ee5a3d011caf47aa0e5411f720593920"),
    (25687409, 98, "0x79866610722c49b11550f50d0da4fd325202ca4c4e36c5bbaad52196725a470f", "V", "0xde6880b1a56080c71849348f4a43d7d5f646709924164bddff27039af80ee693", 25687345, 861936897298276262366558, 2585810691894828787099674, 3, "0x28bea5b242362974d5ddb8f17a1e0e525446960b"),
    (25687409, 144, "0x468f16e9d33dd91ef4035426ea4b40918dcba33c9efb82ca492d13a63c931647", "V", "0x2f3e23cd622fe7e4ae0e1871e953b52f1b3505bb648458c83296f5a453aef9c9", 25687345, 1723873794596552524733116, 2585810691894828787099674, 4, "0x3a98ee5f80268ed91f8a5880d93468b76a9f3bb4"),
    (25687409, 145, "0x1e78a54fff873ed43defa0de68007fbf84f107cd212cb108fce7038fc2ee2849", "V", "0x2f3e23cd622fe7e4ae0e1871e953b52f1b3505bb648458c83296f5a453aef9c9", 25687345, 2585810691894828787099674, 2585810691894828787099674, 2, "0xc07332e05cec1c4896555a6d10361233fdf14422"),
    (25687409, 146, "0x1e78a54fff873ed43defa0de68007fbf84f107cd212cb108fce7038fc2ee2849", "C", "0x2f3e23cd622fe7e4ae0e1871e953b52f1b3505bb648458c83296f5a453aef9c9", 25687345, 0, 0, 0, None),
    (25689798, 164, "0xd54c74cb9dba5a3a25da62a0dcaa04c8b1519b15516e32cacfbd5227585d020d", "V", "0xe241d98bd00292dd299151b79fc253b630aca541a72547d2e413d6965f107e96", 25689733, 862197399596458031244448, 2586592198789374093733344, 3, "0x28bea5b242362974d5ddb8f17a1e0e525446960b"),
    (25689798, 165, "0xcc87cf9f494575ebe3784737ad0ae2599d14dc83b3d5c920989a055a2a12d646", "V", "0x76f8f8be573197c72c36c80f7774b412fc704d01871bee4fb06fef7a96917c3b", 25689733, 862197399596458031244448, 2586592198789374093733344, 2, "0xc07332e05cec1c4896555a6d10361233fdf14422"),
    (25689798, 502, "0x4af2eb2bc2a18e7313e3c7fd78d55085d5e3fb54607a110acadceaaf67b5b00a", "V", "0x76f8f8be573197c72c36c80f7774b412fc704d01871bee4fb06fef7a96917c3b", 25689733, 1724394799192916062488896, 2586592198789374093733344, 1, "0xc61f7bd9ee5a3d011caf47aa0e5411f720593920"),
    (25689798, 503, "0x180abe3871cad3401b1763f7d3dc1a60856a919050599e8daca8807ec8d4382f", "V", "0x76f8f8be573197c72c36c80f7774b412fc704d01871bee4fb06fef7a96917c3b", 25689733, 2586592198789374093733344, 2586592198789374093733344, 4, "0x3a98ee5f80268ed91f8a5880d93468b76a9f3bb4"),
    (25689798, 504, "0x180abe3871cad3401b1763f7d3dc1a60856a919050599e8daca8807ec8d4382f", "C", "0x76f8f8be573197c72c36c80f7774b412fc704d01871bee4fb06fef7a96917c3b", 25689733, 0, 0, 0, None),
    (25692191, 60, "0x78372aa27b5b7c7e0c6c5eba64acf17f2c14e1b568da76834d8bf71ae3338b30", "V", "0x1948f309c89bf883546c98d65985176fb5de0496e0ea2fd7d922d9d5ea189427", 25692127, 862952260379879703853619, 2588856781139639111560857, 3, "0x28bea5b242362974d5ddb8f17a1e0e525446960b"),
    (25692191, 67, "0xcc1cfea050ee2b1ee4b751aabd2acbb1c17839d35ec962cc6e175ecca96bc8bc", "V", "0xa8a5a8de85e99652a8df961900415a2e889b294e140ebb5b8461696df088e58c", 25692127, 862952260379879703853619, 2588856781139639111560857, 2, "0xc07332e05cec1c4896555a6d10361233fdf14422"),
    (25692191, 68, "0xc7b52e71d1f958497cc1f7d413a65b3a288c0330156ebf7548d63049f648b2c5", "V", "0xa8a5a8de85e99652a8df961900415a2e889b294e140ebb5b8461696df088e58c", 25692127, 1725904520759759407707238, 2588856781139639111560857, 4, "0x3a98ee5f80268ed91f8a5880d93468b76a9f3bb4"),
    (25692191, 69, "0x9093cfaf19492213f30ffe27e568df22d24a085311c2796c7fd8f34aa1a44b98", "V", "0xa8a5a8de85e99652a8df961900415a2e889b294e140ebb5b8461696df088e58c", 25692127, 2588856781139639111560857, 2588856781139639111560857, 1, "0xc61f7bd9ee5a3d011caf47aa0e5411f720593920"),
    (25692191, 70, "0x9093cfaf19492213f30ffe27e568df22d24a085311c2796c7fd8f34aa1a44b98", "C", "0xa8a5a8de85e99652a8df961900415a2e889b294e140ebb5b8461696df088e58c", 25692127, 0, 0, 0, None),
    (25694588, 200, "0x278f9f37cabf14161979fed912093ef087fc6b1a9c2785f4aed0be3f7c9c7bba", "V", "0x53e1a5edd636b2d48304ed018d4009a2157c6ea3d7962624f42fdcef513beaa1", 25694523, 862878316663708262893334, 2588634949991124788680002, 3, "0x28bea5b242362974d5ddb8f17a1e0e525446960b"),
    (25694588, 201, "0x92eb69239249c346e9bf7b43b195883da96b7af3697c0ee011002f67f5ed0eb1", "V", "0xc6bd5b452fa0e15a14fa2f6bffecf14bd7ba16da7db4a27689b5613b0d4b63a0", 25694523, 862878316663708262893334, 2588634949991124788680002, 2, "0xc07332e05cec1c4896555a6d10361233fdf14422"),
    (25694588, 202, "0xa456454f1147082e0f2096bce81fe456c4d01d90ba882a0c7e8931c7356e97b5", "V", "0xc6bd5b452fa0e15a14fa2f6bffecf14bd7ba16da7db4a27689b5613b0d4b63a0", 25694523, 1725756633327416525786668, 2588634949991124788680002, 1, "0xc61f7bd9ee5a3d011caf47aa0e5411f720593920"),
    (25694588, 203, "0xc20ce3f5e8d04187d7258fb81fd1419236bd2c7061c8aaf63bd1109566d09f4f", "V", "0xc6bd5b452fa0e15a14fa2f6bffecf14bd7ba16da7db4a27689b5613b0d4b63a0", 25694523, 2588634949991124788680002, 2588634949991124788680002, 4, "0x3a98ee5f80268ed91f8a5880d93468b76a9f3bb4"),
    (25694588, 204, "0xc20ce3f5e8d04187d7258fb81fd1419236bd2c7061c8aaf63bd1109566d09f4f", "C", "0xc6bd5b452fa0e15a14fa2f6bffecf14bd7ba16da7db4a27689b5613b0d4b63a0", 25694523, 0, 0, 0, None),
    (25696986, 264, "0x75906e73351c83cb47927a5f102f19a611b965657ae50a71f45815bd1fa74252", "V", "0x3e4a2a249362483e01cd975765b00fa1d904a18a2d6f325e0abba2b02b306a44", 25696921, 869837246252180611018715, 2609511738756541833056145, 3, "0x28bea5b242362974d5ddb8f17a1e0e525446960b"),
    (25696986, 265, "0x42389aa1fe2d8035b434c08126c3904ed73e3095374b669ad916356ac69d824f", "V", "0xc393cc087400b58061dbfb16326ebb160ca9be79354a70a12dce8ac4cbc7ac23", 25696921, 869837246252180611018715, 2609511738756541833056145, 1, "0xc61f7bd9ee5a3d011caf47aa0e5411f720593920"),
    (25696986, 266, "0x794a6083ecf2b9ebf1820a69a6d9ad9ffdd72769ecf240f39144c9a667905e9e", "V", "0xc393cc087400b58061dbfb16326ebb160ca9be79354a70a12dce8ac4cbc7ac23", 25696921, 1739674492504361222037430, 2609511738756541833056145, 2, "0xc07332e05cec1c4896555a6d10361233fdf14422"),
    (25696986, 267, "0xb87b3e18100c84f98066bc26ba0171a6d23588a097091ef953840504cb14876a", "V", "0xc393cc087400b58061dbfb16326ebb160ca9be79354a70a12dce8ac4cbc7ac23", 25696921, 2609511738756541833056145, 2609511738756541833056145, 4, "0x3a98ee5f80268ed91f8a5880d93468b76a9f3bb4"),
    (25696986, 268, "0xb87b3e18100c84f98066bc26ba0171a6d23588a097091ef953840504cb14876a", "C", "0xc393cc087400b58061dbfb16326ebb160ca9be79354a70a12dce8ac4cbc7ac23", 25696921, 0, 0, 0, None),
    (25699380, 133, "0xd7b8a2aa50a15c1b7e65c40c3142e9e6ee5ca2625186cb6639ac1b4ce45d0188", "V", "0x84b009ea98bf7e63b57e3ce39e7846159219518bc8527fa4fd19e22dcbd84ba1", 25699315, 869837246252180611018715, 2609511738756541833056145, 3, "0x28bea5b242362974d5ddb8f17a1e0e525446960b"),
    (25699380, 134, "0x52d84199093c4ea233a5ca11c787502d9a70a8074e6b0646868e4c8ed37f6e5d", "V", "0x6a2ca009132e4e69c3ccc207ec015e2f1c7bb1704ce2556f87903600d3735aee", 25699315, 869837246252180611018715, 2609511738756541833056145, 2, "0xc07332e05cec1c4896555a6d10361233fdf14422"),
    (25699380, 152, "0x4bb367a4b6916a066c2e014e74799a260e4bc79904375af1c83928ffbc05ad8e", "V", "0x6a2ca009132e4e69c3ccc207ec015e2f1c7bb1704ce2556f87903600d3735aee", 25699315, 1739674492504361222037430, 2609511738756541833056145, 1, "0xc61f7bd9ee5a3d011caf47aa0e5411f720593920"),
    (25699380, 153, "0x2dde7e4fbe332cde2151a12d86cd5d25bc09fe485c6b315d47c6e2f587539f29", "V", "0x6a2ca009132e4e69c3ccc207ec015e2f1c7bb1704ce2556f87903600d3735aee", 25699315, 2609511738756541833056145, 2609511738756541833056145, 4, "0x3a98ee5f80268ed91f8a5880d93468b76a9f3bb4"),
    (25699380, 154, "0x2dde7e4fbe332cde2151a12d86cd5d25bc09fe485c6b315d47c6e2f587539f29", "C", "0x6a2ca009132e4e69c3ccc207ec015e2f1c7bb1704ce2556f87903600d3735aee", 25699315, 0, 0, 0, None),
    (25701771, 602, "0xaf466196df89c3177a0c87677ff18827385244ea150d0e9493606c7bc4eadac5", "V", "0x2491e065739832b8b4063e62562c1f280266a37ad4bbae0eba2710e7690ac7c5", 25701706, 870411054383250611018715, 2611233163149751833056145, 4, "0x3a98ee5f80268ed91f8a5880d93468b76a9f3bb4"),
    (25701771, 603, "0xf4800aa969b840d60fc2fcf17e285e4827dae6d1d5295238f4dfcf8d31d3e4a0", "V", "0x3383e7ca454b8b0b1a2d2f6d4a00b9106a34422a3fc2a6af384a2238beb0ef65", 25701706, 870411054383250611018715, 2611233163149751833056145, 3, "0x28bea5b242362974d5ddb8f17a1e0e525446960b"),
    (25701771, 613, "0x6ad0c8b6d8e93e6009e5531605703e7bf17048ddc771803e942c119204317ea1", "V", "0x2491e065739832b8b4063e62562c1f280266a37ad4bbae0eba2710e7690ac7c5", 25701706, 1740822108766501222037430, 2611233163149751833056145, 1, "0xc61f7bd9ee5a3d011caf47aa0e5411f720593920"),
    (25701771, 614, "0x0c69efd65d3b6c79d347c8aff4fa7504c4fccd8c575d81058ec3b1f4f1b1aa66", "V", "0x2491e065739832b8b4063e62562c1f280266a37ad4bbae0eba2710e7690ac7c5", 25701706, 2611233163149751833056145, 2611233163149751833056145, 2, "0xc07332e05cec1c4896555a6d10361233fdf14422"),
    (25701771, 615, "0x0c69efd65d3b6c79d347c8aff4fa7504c4fccd8c575d81058ec3b1f4f1b1aa66", "C", "0x2491e065739832b8b4063e62562c1f280266a37ad4bbae0eba2710e7690ac7c5", 25701706, 0, 0, 0, None),
    (25704160, 72, "0xbf470e16524f92e74f20b01b1d5a2bb39411465363966719b07f2893820e88b4", "V", "0x7425b6e6e003d790dbeeb036c0d8903788f6a292b725e557f1b6ef19a7bbd85e", 25704098, 870426054383250611018715, 2611278163149751833056145, 1, "0xc61f7bd9ee5a3d011caf47aa0e5411f720593920"),
    (25704160, 76, "0x87f265af489f3153335493f69071a10f2bde5d0293b9a8d25d10fac5cc75cc9e", "V", "0x7425b6e6e003d790dbeeb036c0d8903788f6a292b725e557f1b6ef19a7bbd85e", 25704098, 1740852108766501222037430, 2611278163149751833056145, 2, "0xc07332e05cec1c4896555a6d10361233fdf14422"),
    (25704160, 84, "0x26f0db70782fd8aabcae859077522c3319c040e376673e2505d356b0f8d8116d", "V", "0x356be363da846ac4fb3b12d0e63450553b1221ba299dd4c6c99e562a37cc5530", 25704098, 870426054383250611018715, 2611278163149751833056145, 3, "0x28bea5b242362974d5ddb8f17a1e0e525446960b"),
    (25704160, 92, "0x129c15ff9cf73dc560a8e826f104a86931d5c152b65e292f4a71f1e349819a79", "V", "0x7425b6e6e003d790dbeeb036c0d8903788f6a292b725e557f1b6ef19a7bbd85e", 25704098, 2611278163149751833056145, 2611278163149751833056145, 4, "0x3a98ee5f80268ed91f8a5880d93468b76a9f3bb4"),
    (25704160, 93, "0x129c15ff9cf73dc560a8e826f104a86931d5c152b65e292f4a71f1e349819a79", "C", "0x7425b6e6e003d790dbeeb036c0d8903788f6a292b725e557f1b6ef19a7bbd85e", 25704098, 0, 0, 0, None),
    (25706550, 46, "0xadd395dc1dfda2fbf681dc1ce62904a1ec1dffb613d11f397c0e49dd876041fe", "V", "0x2f5f8dfdcff5c839ca0c751e9cea2feb9f94bc83c4cfd749637632ffbab8f21e", 25706485, 900754810494805098333521, 2702264431484415295000563, 1, "0xc61f7bd9ee5a3d011caf47aa0e5411f720593920"),
    (25706550, 47, "0x8e8cb00773c940c93d4cfef2db1aecf77c9c9a022465439b19f6a51a1e506ed0", "V", "0x2f5f8dfdcff5c839ca0c751e9cea2feb9f94bc83c4cfd749637632ffbab8f21e", 25706485, 1801509620989610196667042, 2702264431484415295000563, 4, "0x3a98ee5f80268ed91f8a5880d93468b76a9f3bb4"),
    (25706550, 49, "0x815128f6153c43d3b9f8389660b6535ed93a0ab5d603de4a75ed763e27dc0678", "V", "0xf87047ca59909a329d6b00e1c907ac8e1cbf13fe9c0127689946ac474e5fb96f", 25706485, 900754810494805098333521, 2702264431484415295000563, 3, "0x28bea5b242362974d5ddb8f17a1e0e525446960b"),
    (25708928, 13, "0x40e2af016edce2210027d2dcd9ef5005b83e8113a595fdceb1f8444221659405", "V", "0xb68e611d3ee60e86a116c9f78994dbb8d154394af491cc621cf332fb4593c7f1", 25708863, 900971805864460366238293, 2702915417593381098714879, 3, "0x28bea5b242362974d5ddb8f17a1e0e525446960b"),
    (25708928, 81, "0x3971270d28823e1f27b7090965e8795a85b4ec30bb0b4bcba25e576dbb8c4441", "V", "0x1c848c952094bd73da9b4ed050b0b60c54e81b8f8fa3eefb67c6a541f80218fe", 25708863, 900971805864460366238293, 2702915417593381098714879, 4, "0x3a98ee5f80268ed91f8a5880d93468b76a9f3bb4"),
    (25708928, 82, "0xb7ca19c0df9743e847a72dd9a307e5f6b0e2c07f0f890b51e985d95b56a1230b", "V", "0x1c848c952094bd73da9b4ed050b0b60c54e81b8f8fa3eefb67c6a541f80218fe", 25708863, 1801943611728920732476586, 2702915417593381098714879, 1, "0xc61f7bd9ee5a3d011caf47aa0e5411f720593920"),
    (25711316, 156, "0x0a26eff05be501ce08caca15e5c244f2e947ec8ab99348a13f72ff511f52050f", "V", "0xb73596ef07bd1cd43be60ffbf059aa2352cccc6502da38c20ea0b4bcc71b301b", 25711251, 903130799147080110215733, 2709392397441240330647199, 4, "0x3a98ee5f80268ed91f8a5880d93468b76a9f3bb4"),
    (25711316, 157, "0x0d25eff95b502cab3d5c8bb9460e4abb316b800f5688f46453030e8b21b367d7", "V", "0xb73596ef07bd1cd43be60ffbf059aa2352cccc6502da38c20ea0b4bcc71b301b", 25711251, 1806261598294160220431466, 2709392397441240330647199, 1, "0xc61f7bd9ee5a3d011caf47aa0e5411f720593920"),
    (25711316, 158, "0x8cdc37bc0e67bb95f36bae8375117c56c1a6e0449505f4e2fbd950e954e87a7d", "V", "0xe3b4aa4d5d1bfe41e2f0380e60ec684d6d7d74dc4a11737daf364d7406130ea5", 25711251, 903130799147080110215733, 2709392397441240330647199, 3, "0x28bea5b242362974d5ddb8f17a1e0e525446960b"),
    (25713708, 127, "0xd8e6a4d65adab58618a3c1f2598a9eee4d21fcecccb085838a393785deb89cb7", "V", "0xfbd7228cf1c37a2e8a524aba2f984f32f4dd01299ea4f9c871035b20ac6f8142", 25713643, 903612638923552645215733, 2710837916770657935647199, 3, "0x28bea5b242362974d5ddb8f17a1e0e525446960b"),
    (25713708, 128, "0xa4923570dcace8a3f200fd85792b6637968fe34004598af5af6d15c0240af534", "V", "0x1de39278e71c3b6faa9ab314d134faba14f02220032cf4f16b1a984a683071dd", 25713643, 903612638923552645215733, 2710837916770657935647199, 1, "0xc61f7bd9ee5a3d011caf47aa0e5411f720593920"),
    (25713708, 134, "0x5835127dea0c6ddd25a0548b3ffa5d712770cd27a257438b5fec0b461b876350", "V", "0x1de39278e71c3b6faa9ab314d134faba14f02220032cf4f16b1a984a683071dd", 25713643, 1807225277847105290431466, 2710837916770657935647199, 4, "0x3a98ee5f80268ed91f8a5880d93468b76a9f3bb4"),
    (25716105, 67, "0x5195a6eb0191ff4ca1bad8c860af29eb9874d28ebf17586c37c9c13e61ce48d7", "V", "0x237d5973910d8d4b40ec1c57f4294aebfcfd90e53b035fa1023cb0816375c7b0", 25716040, 903618856761943060479007, 2710856570285829181437021, 4, "0x3a98ee5f80268ed91f8a5880d93468b76a9f3bb4"),
    (25716105, 70, "0xd839b4e38479672bd7e488d8ed22996a6ff46da889cdb54483cf6a15bab51b92", "V", "0x42b3b3315fa6188415108a4b01cfea79a6e51cef0b859172ea6389ccdb0e5860", 25716040, 903618856761943060479007, 2710856570285829181437021, 3, "0x28bea5b242362974d5ddb8f17a1e0e525446960b"),
    (25716105, 71, "0xddf7b14951afd72d3085ac9d537e567bc4557ea603a2bd8b8d22c3089c913f02", "V", "0x237d5973910d8d4b40ec1c57f4294aebfcfd90e53b035fa1023cb0816375c7b0", 25716040, 1807237713523886120958014, 2710856570285829181437021, 1, "0xc61f7bd9ee5a3d011caf47aa0e5411f720593920"),
    (25718495, 84, "0x3facc11e17242869220f7449be4c3c5df22674e54a8ecc4c38a63f4672938dbc", "V", "0xa4b2e1e7582cf6412487de77875cf745419cb8ab25ffb898e9342a937c1d37a2", 25718430, 906388798278352420710599, 2719166394835057262131797, 3, "0x28bea5b242362974d5ddb8f17a1e0e525446960b"),
    (25718495, 85, "0x2b122989bf821a1c251674f3a2f4cabb645a495e51b16f81c7a78a6e5e0ee1ea", "V", "0x115c91f208881b0a393929d35eae11480ebff334c71602326ccf899c4df90e05", 25718430, 906388798278352420710599, 2719166394835057262131797, 1, "0xc61f7bd9ee5a3d011caf47aa0e5411f720593920"),
    (25718495, 86, "0x633f869ea7925ff6d6326be33bbb58844d7fe033bbb4de5d84c8fba9ec32045d", "V", "0x64967cbdb6000e6ea98587898de3585c955a3be545728f112c732f8ba0b81fd9", 25718430, 906388798278352420710599, 2719166394835057262131797, 4, "0x3a98ee5f80268ed91f8a5880d93468b76a9f3bb4"),
    (25720886, 68, "0x3c3c5290e22db8ff8c6fb1f67cbb6f9a4d459772e7611351fc2cc823b2184618", "V", "0x8a8417d1a90778c0ee38bc83ea78cf1d3484b9b04fe55127ffb292a7babf7bfd", 25720821, 906577559251634148811163, 2719732677754902446433489, 1, "0xc61f7bd9ee5a3d011caf47aa0e5411f720593920"),
    (25720886, 69, "0x75962cca0b5e2d5f66b6ca1911ca6a5db9a1ba048c0929a8cb05a039f0800607", "V", "0xafefe2f7b1d8b10f4b5c1ec277f9b38c7b213005aea02d31b46aba9d2ec5817e", 25720821, 906577559251634148811163, 2719732677754902446433489, 3, "0x28bea5b242362974d5ddb8f17a1e0e525446960b"),
    (25720886, 70, "0xb1e490f85ff0bf5c4d4624a816b494c1c5fc34bb15068df1fe98d8d49029657b", "V", "0x8a8417d1a90778c0ee38bc83ea78cf1d3484b9b04fe55127ffb292a7babf7bfd", 25720821, 1813155118503268297622326, 2719732677754902446433489, 4, "0x3a98ee5f80268ed91f8a5880d93468b76a9f3bb4"),
    (25723278, 115, "0x9f212e15060932572f23c9cb0064ab1d9fe11608f5e21f5bf4babee08e1fdf5d", "V", "0xcf7a92fc80747fc7825b1cc9cc2ca77f06cef3eb9f559f454ae06114d64bfd91", 25723213, 906577559251634148811163, 2719732677754902446433489, 3, "0x28bea5b242362974d5ddb8f17a1e0e525446960b"),
    (25723278, 116, "0xcecb20fbaaa114f80d863cfbcaba56808de653fea799af87b48c477ca0069448", "V", "0x9b08d0541e96a8644d1b4cbb2d7df91938416103eccf75576b8db9d5a06317fc", 25723213, 906577559251634148811163, 2719732677754902446433489, 4, "0x3a98ee5f80268ed91f8a5880d93468b76a9f3bb4"),
    (25723278, 117, "0xd5401afd235be732df334ac5c6161900a2dc739046157547dc081f3da7df3c64", "V", "0x9b08d0541e96a8644d1b4cbb2d7df91938416103eccf75576b8db9d5a06317fc", 25723213, 1813155118503268297622326, 2719732677754902446433489, 1, "0xc61f7bd9ee5a3d011caf47aa0e5411f720593920"),
    (25725668, 203, "0xac0bde8dc58fb20dc5882c7fe489f729f0ced53b681d7c373ab7f281c4b283ce", "V", "0x9d845e3d16712cf567870dd3a9ab26245b635f28921caa3cc5cf0ffaf308ae07", 25725603, 906604843067870453562995, 2719814529203611360688985, 4, "0x3a98ee5f80268ed91f8a5880d93468b76a9f3bb4"),
    (25725668, 204, "0x91989395e87b45b200dea22bdac5a795cf57be1136c8ce9178eab980d4439f71", "V", "0x1de17e755a6a279ee8f614deb52fee1e291f8500b73273a00bde47e170f1a1f2", 25725603, 906604843067870453562995, 2719814529203611360688985, 3, "0x28bea5b242362974d5ddb8f17a1e0e525446960b"),
    (25725668, 236, "0x7b4cf00870a43992e44d6cb182f906b3d5523643d4e23f742fca3d8ace8ff479", "V", "0x9d845e3d16712cf567870dd3a9ab26245b635f28921caa3cc5cf0ffaf308ae07", 25725603, 1813209686135740907125990, 2719814529203611360688985, 1, "0xc61f7bd9ee5a3d011caf47aa0e5411f720593920"),
    (25725668, 237, "0x50fd3004e643e13d33e9cb0fa55882286bb8443e33e3ca43186c6f7987d11cbf", "V", "0x9d845e3d16712cf567870dd3a9ab26245b635f28921caa3cc5cf0ffaf308ae07", 25725603, 2719814529203611360688985, 2719814529203611360688985, 2, "0xc07332e05cec1c4896555a6d10361233fdf14422"),
    (25725668, 238, "0x50fd3004e643e13d33e9cb0fa55882286bb8443e33e3ca43186c6f7987d11cbf", "C", "0x9d845e3d16712cf567870dd3a9ab26245b635f28921caa3cc5cf0ffaf308ae07", 25725603, 0, 0, 0, None),
    (25728058, 29, "0xed9ec5f0b9800d1e56a215758b9fede2423fc04212d14526611ad35f585b7b2d", "V", "0x5f50c3a3a44d1c8e57ff06395a27fabdc95c8f40d3faa18850280b698576383a", 25727993, 909123419480359179111759, 2727370258441077537335277, 1, "0xc61f7bd9ee5a3d011caf47aa0e5411f720593920"),
    (25728058, 30, "0xd23bca443740b34cf17fb5f8f4bbd8ac611b5e6edd5c7a7e0f408f23d2de1ed8", "V", "0x37c77d45717598bbf287ae4bafad0f4725ce28ff5ec56c6ec0a03508ab72670d", 25727993, 909123419480359179111759, 2727370258441077537335277, 3, "0x28bea5b242362974d5ddb8f17a1e0e525446960b"),
    (25728058, 31, "0xa791d855cec3b883ad3c63e265f0bab911e480add2048eef43eb2bc9b372a095", "V", "0x5f50c3a3a44d1c8e57ff06395a27fabdc95c8f40d3faa18850280b698576383a", 25727993, 1818246838960718358223518, 2727370258441077537335277, 4, "0x3a98ee5f80268ed91f8a5880d93468b76a9f3bb4"),
    (25728058, 32, "0x4c6aef9afc3a96a7d34577c64320ec29492b5d34cb85e6de026d14f41704df36", "V", "0x5f50c3a3a44d1c8e57ff06395a27fabdc95c8f40d3faa18850280b698576383a", 25727993, 2727370258441077537335277, 2727370258441077537335277, 2, "0xc07332e05cec1c4896555a6d10361233fdf14422"),
    (25728058, 33, "0x4c6aef9afc3a96a7d34577c64320ec29492b5d34cb85e6de026d14f41704df36", "C", "0x5f50c3a3a44d1c8e57ff06395a27fabdc95c8f40d3faa18850280b698576383a", 25727993, 0, 0, 0, None),
]


def replay_mainnet(tmp_path, rows=None, step_every: int = 50, after: int = 400, ids=("m9",), **over):
    """Replay recorded mainnet rounds on a FakeChain at their real block numbers, 12 s of worker clock per block,
    one worker step every `step_every` blocks and after every block that carries a log."""
    from eb_events import TOPIC0

    rows = MAINNET_2026_08 if rows is None else rows
    chain = TimedChain(start_block=rows[0][0] - 300)
    slots = {}
    for r in rows:
        if r[3] == "V":
            slots.setdefault(r[8], r[9])
    oc = OracleChain(chain, slots=slots, active=sorted(slots))
    chain.mine()
    (tmp_path / "failed-tx.jsonl").write_text("")
    _, w = make_worker(tmp_path, only=list(ids), chain=chain, clock=chain.clock, views=VIEWS,
                       start_block=chain.sealed, activation_block=chain.sealed,
                       inbox={"path": str(tmp_path / "failed-tx.jsonl")}, **over)
    w.step()
    by_block: dict[int, list] = {}
    for r in rows:
        by_block.setdefault(r[0], []).append(r)
    end = rows[-1][0] + after
    while chain.number <= end:
        events = by_block.get(chain.number, [])
        for blk, _li, tx, kind, root, ref, weight, quorum, slot, oracle in events:
            if kind == "V":
                chain.emit_event(TOPIC0[WRP], [root, "0x%064x" % ref],
                                 u256(weight) + u256(quorum) + u256(slot) + u256(int(oracle, 16)), tx=tx)
            else:
                chain.emit_event(TOPIC0["RootCommitted"], [root, "0x%064x" % ref], b"", tx=tx)
        chain.mine()
        w.clock.advance(12)
        if events or chain.number % step_every == 0:
            w.step()
    return oc, w
