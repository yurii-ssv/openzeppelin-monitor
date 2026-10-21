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


class OracleChain:
    def __init__(self, chain: FakeChain | None = None, slots=None, quorum_bps=QUORUM_BPS, active=(1, 2, 3, 4)):
        self.c = chain or FakeChain()
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


def oracle_worker(tmp_path, ids=("m9",), oc: OracleChain | None = None, clock=None, checks=None, inbox=False,
                  **over):
    oc = oc or OracleChain()
    oc.c.mine(100)
    kw = {}
    if inbox:
        path = tmp_path / "failed-tx.jsonl"
        if not path.exists():
            path.write_text("")
        kw["inbox"] = {"path": str(path)}
    _, w = make_worker(tmp_path, only=list(ids), chain=oc.c, clock=clock or FakeClock(), views=VIEWS,
                       checks=checks or {}, **kw, **over)
    return oc, w


def restart(tmp_path, w, oc: OracleChain, ids=("m9",), checks=None, inbox=False, **over):
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
