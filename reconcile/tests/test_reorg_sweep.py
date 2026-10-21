"""Adversarial reorg sweep (CP1B repair 3): fork depth 1..30 x confirmations 0/2/12, deterministic seeds.

Each case scripts, on one FakeChain, M5 clusters (a confirmed survivor, a latent cluster whose confirming
removal may land on the deleted blocks, an orphan-only confirmed cluster), failed-transaction inbox lines,
and a live-parameter change feeding `Watch`, a second check module that pages on inbox entries and on the
network fee (it follows the README §6.1 contract: in a reorg run it re-derives its inbox keys from the
canonical inbox rows). Which events land above the fork, which are re-included on the replacement chain and
when, and whether the new chain is shorter or longer are drawn from the seed.

Oracle: a second worker on a fresh database, run once on the final canonical chain, says what the chain
supports. Invariants on the worker that lived through the reorg:
  - no open incident without supporting canonical evidence, and no dropped alert for surviving evidence:
    its open incidents (check, key, severity, notify) equal the oracle's, and every open page/warn incident
    was delivered at least once;
  - no page for orphaned evidence: every page/warn message enqueued after the reorg (opens, escalations,
    updates, re-alerts, including after the 6-hour cadence) belongs to a key the oracle has open;
  - healthy flag consistent: healthy, like the oracle, with no reorg re-evaluation pending and no check error;
  - M5's own tables agree: open `m5_incidents` = open framework m5 incidents, `evaluations` only for
    clusters that still have logs.
"""

from __future__ import annotations

import copy
import json
import random

import pytest
from fakechain import PROXY, FakeChain, FakeClock, MiniSSV, keys
from harness import VIEWS, make_worker, stub_params
from test_framework import inbox_line

from checks.base import Check, Every, Finding, OnEvents, OnInbox
from checks.m5_eb_desync import M5EbDesync
from eb_storage import u256
from rpc import RpcError

OWNER, OTHER = "0x" + "aa" * 20, "0x" + "bb" * 20
OPS_S, OPS_D, OPS_O = (11, 12, 13, 14), (11, 12, 13, 15), (21, 22, 23, 24)
SENDER = "0x" + "11" * 20
FEE, FEE_LIMIT, FEE_HIGH = 3_366_600_000, 4_000_000_000, 5_000_000_000


class Watch(Check):
    id = "watch"

    def __init__(self):
        super().__init__({}, {})
        self.schedule = (Every(60), OnInbox(), OnEvents({"NetworkFeeUpdated"}))

    def prepare(self, ctx):
        self.fee = ctx.params.network_fee

    def run(self, ctx, block):
        out = {}
        for e in ctx.inbox:
            out["tx:" + e.tx_hash] = self._page("tx:" + e.tx_hash, f"failed tx {e.tx_hash} in block {e.block}")
        out["fee"] = self._page("fee", f"network fee {self.fee}", resolve=self.fee <= FEE_LIMIT)
        if ctx.reorg_ancestor is not None:  # README §6.1: re-derive reorg keys from canonical evidence
            canonical = {"tx:" + r[0] for r in ctx.db.conn.execute("SELECT tx_hash FROM inbox WHERE canonical=1")}
            for k in sorted(ctx.reorg_keys & canonical):
                out.setdefault(k, self._page(k, "re-found on the canonical chain"))
        return list(out.values())

    def _page(self, key, body, resolve=False):
        return Finding(check=self.id, key=key, severity="page", title=f"watch {key}", body=body, resolve=resolve)


def checks(start):
    settings = {"proxy": PROXY, "start_block": start, "activation_block": start, "alerts": {}}
    m5 = M5EbDesync({**M5EbDesync.defaults, "sweep_interval_s": 300, "full_sweep_interval_s": 3600}, settings)
    return [m5, Watch()]


class Scenario:
    def __init__(self, tmp_path, seed, confirmations):
        self.rng = random.Random(f"reorg-sweep:{seed}:{confirmations}")
        self.depth = seed % 30 + 1
        self.c = confirmations
        self.chain = chain = FakeChain()
        stub_params(chain, network_fee=FEE)
        chain.stub_call(PROXY, "0xbc26e7e5", revert="0x12e04c87")
        chain.mine(1 + confirmations)
        self.start = chain.sealed
        self.inbox = tmp_path / "failed-tx.jsonl"
        self.inbox.write_text("")
        self.clock = FakeClock()
        self.kw = dict(chain=chain, views=VIEWS, confirmations=confirmations, inbox={"path": str(self.inbox)},
                       start_block=self.start, activation_block=self.start)
        (tmp_path / "w").mkdir()
        _, self.w = make_worker(tmp_path / "w", checks(self.start), clock=self.clock, **self.kw)
        self.tmp = tmp_path
        self.ssv = MiniSSV(chain)
        self.fee = FEE
        self.log: list[str] = []
        self.enqueued_at: dict[int, int] = {}
        self.params_down = 0  # steps during which every live-parameter read fails (injected after the reorg)
        real_load = self.w.params.load

        def load(chain, block):
            if self.params_down:
                raise RpcError("execution reverted (injected parameter outage)")
            return real_load(chain, block)

        self.w.params.load = load

    # ---- chain actions (each seals at least one block)

    def step(self, blocks=1):
        for _ in range(blocks):
            self.chain.mine()
            self.clock.advance(12)
            self.wstep()

    def wstep(self):
        """One worker step; remembers the checkpoint at which each outbox row was enqueued."""
        before = self.w.db.conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM outbox").fetchone()[0]
        self.w.step()
        self.params_down = max(0, self.params_down - 1)
        for (rowid,) in self.w.db.conn.execute("SELECT rowid FROM outbox WHERE rowid>?", (before,)):
            self.enqueued_at[rowid] = self.w.checkpoint()["block"]

    def failed_tx(self, tx=None):
        if tx is None:
            tx = self.chain.add_tx(SENDER, PROXY, "0xbc26e7e5")
        else:  # the same transaction re-included in a block of the new chain
            self.chain.txs[tx]["blockNumber"] = hex(self.chain.number)
        self.chain.mine()
        b = self.chain.sealed
        with open(self.inbox, "a") as fh:
            fh.write(inbox_line(tx=tx, block=b, block_hash=self.chain.hash(b), **{"from": SENDER}))
        return tx

    def set_fee(self, fee):
        stub_params(self.chain, network_fee=fee)
        self.chain.emit_event("NetworkFeeUpdated", [], u256(self.fee) + u256(fee))
        self.fee = fee
        self.chain.mine()

    def orphan_cluster(self):
        s = self.ssv
        s.register(OTHER, OPS_O, keys(1, 51_000))
        ref = self.chain.number
        self.chain.mine()
        s.commit(ref)
        s.update(OTHER, OPS_O, 2048)
        ref = self.chain.number
        self.chain.mine()
        d = keys(63, 52_000)
        s.register(OTHER, OPS_O, d)
        s.commit(ref)
        s.update(OTHER, OPS_O, 2048)
        s.remove(OTHER, OPS_O, d)

    # ---- the case

    def run(self):
        rng, chain, s = self.rng, self.chain, self.ssv
        # canonical history: S confirmed, D latent, one failed tx, maybe a high fee
        s.register(OWNER, OPS_S, keys(1, 41_000))
        s.register(OWNER, OPS_D, keys(1, 42_000))
        ref = chain.number
        chain.mine()
        s.commit(ref)
        s.update(OWNER, OPS_S, 2048)
        s.update(OWNER, OPS_D, 2048)
        ref = chain.number
        chain.mine()
        self.dummies_s, self.dummies_d = keys(63, 43_000), keys(63, 44_000)
        s.register(OWNER, OPS_S, self.dummies_s)
        s.register(OWNER, OPS_D, self.dummies_d)
        s.commit(ref)
        s.update(OWNER, OPS_S, 2048)
        s.update(OWNER, OPS_D, 2048)
        s.remove(OWNER, OPS_S, self.dummies_s)
        self.failed_tx()
        if rng.random() < 0.5:
            self.set_fee(FEE_HIGH)
        self.step(self.c + 3)

        # the blocks the reorg will delete: `depth` of them from `fork`
        fork = chain.number
        snapshot = copy.deepcopy((s.clusters, s.latest_root, s.roots))
        fee_at_fork = self.fee
        ops = {"d_remove": 1, "tx": 1, "fee": 1, "orphan_cluster": 9}
        chosen, room = [], self.depth
        for op in rng.sample(sorted(ops), len(ops)):
            if ops[op] <= room and rng.random() < 0.8:
                chosen.append(op)
                room -= ops[op]
        orphan_tx = None
        for op in chosen:
            if op == "d_remove":
                s.remove(OWNER, OPS_D, self.dummies_d)
            elif op == "tx":
                orphan_tx = self.failed_tx()
            elif op == "fee":
                self.set_fee(FEE if self.fee == FEE_HIGH else FEE_HIGH)
            else:
                self.orphan_cluster()
            self.clock.advance(12)
            self.wstep()
        while chain.number - fork < self.depth:
            self.step()
        old_number = chain.number
        assert old_number - fork == self.depth
        self.log.append(f"depth={self.depth} c={self.c} fork={fork} ops={chosen} cp={self.w.checkpoint()['block']}")

        # replacement chain
        chain.reorg(fork)
        for key, rows in chain.call_stubs.items():
            chain.call_stubs[key] = [r for r in rows if r[0] < fork]
        s.clusters, s.latest_root, s.roots = snapshot
        self.fee = fee_at_fork
        self.mark = self.w.db.conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM outbox").fetchone()[0]
        again = [op for op in chosen if op in ("d_remove", "tx", "fee") and rng.random() < 0.5]
        new_len = max(1, self.depth + rng.randint(-3, 3))
        when = {op: rng.randint(0, new_len) for op in again}
        self.outage = rng.random() < 0.3  # the reorg run of checks reading parameters fails for a while
        if self.outage:
            self.params_down = rng.randint(1, 4)
            self.clock.advance(21_601)  # a page re-alert would be due while the re-evaluation is pending
        self.log.append(f"re-included={sorted(when.items())} new_len={new_len} params_down={self.params_down}")
        for i in range(new_len + 1):
            for op in [o for o, t in when.items() if t == i]:
                if op == "d_remove":
                    s.remove(OWNER, OPS_D, self.dummies_d)
                elif op == "tx":
                    self.failed_tx(orphan_tx)
                else:
                    self.set_fee(FEE if self.fee == FEE_HIGH else FEE_HIGH)
            self.clock.advance(12)
            self.wstep()
            chain.mine()
        self.step(self.c + 3)
        self.clock.advance(21_601)  # past the page re-alert cadence
        self.step()
        self.step()

    # ---- the oracle and the invariants

    def oracle(self, block=None):
        """A fresh worker whose checkpoint is `block` (default: the final checkpoint) on the final chain."""
        d = self.tmp / f"oracle-{block}"
        d.mkdir()
        kw = dict(self.kw)
        if block is not None:
            kw["confirmations"] = self.chain.sealed - block
            # Evidence at `block` is the lines of blocks up to it: a later line (e.g. the orphaned one of a
            # transaction re-included lower on the new chain) would only hold the oracle's gate.
            lines = [ln for ln in self.inbox.read_text().splitlines(keepends=True) if json.loads(ln)["block"] <= block]
            (d / "inbox.jsonl").write_text("".join(lines))
            kw["inbox"] = {"path": str(d / "inbox.jsonl")}
        _, o = make_worker(d, checks(self.start), clock=FakeClock(), **kw)
        o.step(force=True)
        o.step(force=True)
        assert block is None or o.checkpoint()["block"] == block
        return o

    def check(self):
        w, o = self.w, self.oracle()
        ctx = "\n".join(self.log)

        def open_set(x):
            return {(r["check_id"], r["key"]): (r["severity"], r["notify"]) for r in x.db.conn.execute(
                "SELECT * FROM incidents WHERE state='open'")}

        got, want = open_set(w), open_set(o)
        assert got == want, f"{ctx}\nopen incidents differ\n got  {got}\n want {want}"
        for (check, key), (sev, notify) in want.items():
            if notify and sev in ("page", "warn"):
                inc = w.db.conn.execute("SELECT id FROM incidents WHERE state='open' AND check_id=? AND key=?",
                                        (check, key)).fetchone()["id"]
                sent = w.db.conn.execute(
                    "SELECT COUNT(*) FROM outbox WHERE incident_id=? AND severity IN ('page','warn') "
                    "AND sent_at IS NOT NULL", (inc,)).fetchone()[0]
                assert sent, f"{ctx}\ndropped alert for surviving evidence {check}/{key}"
        late = w.db.conn.execute(
            "SELECT o.rowid, o.key, o.kind, i.check_id, i.key AS ikey FROM outbox o JOIN incidents i "
            "ON i.id=o.incident_id WHERE o.rowid>? AND o.severity IN ('page','warn')", (self.mark,)).fetchall()
        at: dict[int, dict] = {}
        for r in late:  # each page after the reorg must be supported by the canonical chain at its block
            b = self.enqueued_at[r["rowid"]]
            if b not in at:
                at[b] = open_set(self.oracle(b))
            sev = at[b].get((r["check_id"], r["ikey"]), ("info", 0))
            assert sev[0] in ("page", "warn") and sev[1], f"{ctx}\npage for orphaned evidence at {b}: {dict(r)}"
        self.log.append(f"late pages checked: {len(late)} at blocks {sorted(at)}")
        status = json.loads((self.tmp / "w" / "w.status.json").read_text())
        ostatus = json.loads((self.tmp / "oracle-None" / "w.status.json").read_text())
        assert status["healthy"] is True and ostatus["healthy"] is True, f"{ctx}\n{status['error']}"
        assert all(w.db.meta(f"reorg:{c}") is None for c in w.checks), ctx
        errors = w.db.conn.execute("SELECT key FROM outbox WHERE key LIKE 'health:check-error:%' "
                                   "OR key LIKE 'health:unknown:%'" + ("" if self.outage else
                                                                     " OR key LIKE 'health:params:%'")).fetchall()
        assert not errors, f"{ctx}\n{[r[0] for r in errors]}"
        m5_open = {r[0] for r in w.db.conn.execute("SELECT id FROM m5_incidents WHERE state='open'")}
        fw_open = {r[0] for r in w.db.conn.execute("SELECT id FROM incidents WHERE state='open' AND check_id='m5'")}
        assert m5_open == fw_open, ctx
        evaluated = {r[0] for r in w.db.conn.execute("SELECT cluster_id FROM evaluations")}
        assert evaluated == set(w.db.clusters(explicit_only=False)), ctx
        return got


@pytest.mark.parametrize("confirmations", [0, 2, 12])
@pytest.mark.parametrize("seed", range(30))
def test_reorg_sweep(tmp_path, seed, confirmations):
    sc = Scenario(tmp_path, seed, confirmations)
    sc.run()
    sc.check()
