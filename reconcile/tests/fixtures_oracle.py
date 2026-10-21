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
    tx = chain.add_tx(sender, PROXY, data)
    chain.mine()
    with open(path, "a") as fh:
        fh.write(inbox_line(chain, tx, sender, UPDATE_CLUSTER_BALANCE_SELECTOR,
                            "updateClusterBalance(uint64,address,uint64[],(uint32,uint64,uint64,bool,uint256),uint32,"
                            "bytes32[])"))
    return tx


def tick(w, oc: OracleChain | None = None, blocks: int = 0, seconds: float = 0):
    """Mine `blocks`, move the worker clock by `seconds`, then run one worker step."""
    if oc is not None and blocks:
        oc.c.mine(blocks)
    if seconds:
        w.clock.advance(seconds)
    w.step()
    return w


def incidents(w, check):
    return [dict(r) for r in w.db.conn.execute("SELECT * FROM incidents WHERE check_id=? ORDER BY rowid", (check,))]


def open_keys(w, check) -> dict[str, str]:
    return {r["key"]: r["severity"] for r in incidents(w, check) if r["state"] == "open"}


def sent(w, check, where="1=1"):
    return [dict(r) for r in w.db.conn.execute(
        f"SELECT * FROM outbox WHERE check_id=? AND {where} ORDER BY created_at, rowid", (check,))]
