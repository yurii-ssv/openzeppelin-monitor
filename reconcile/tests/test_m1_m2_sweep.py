"""CP2d repair 1: deterministic adversarial sweep of M1 and M2 (seeded random walks on FakeChain).

Each walk mixes cluster/operator changes, same-block orderings, reorgs (depth 1..5, also below the confirmation
window), empty/failed SSV API answers, inbox reverts and restarts. After every step an independent model computed
from the walk's own action log (never from the check's tables) asserts:

  no resolve without positive proof   an incident resolved in a step is disproven by the model at that block, and
                                      never by an empty/failed API answer alone;
  no open incident without evidence   every open incident is supported by the model at the checkpoint;
  no dropped page / warn              every condition the model holds at the checkpoint is open at its severity.

A step whose run could not commit (reorg re-evaluation pending: README §6.2 (4)) must leave every incident as it
was. Run: reconcile/.venv/bin/python -m pytest -q reconcile/tests/test_m1_m2_sweep.py
"""

from __future__ import annotations

import copy
import json
import random

import pytest
from fakechain import MiniSSV
from fixtures_cluster import (
    OPS,
    OPS2,
    OWNER,
    OWNER2,
    ClusterChain,
    current_tuple,
    deposit,
    exceed_limit,
    failed_tx,
    key_of,
    m1_config,
    make_m1,
    multi_benign,
    poison,
    set_operator,
)
from harness import VIEWS, advance, api_row, make_worker
from test_m2 import LEG, LEG_OPS, setup

pytestmark = pytest.mark.m1

K1, K2 = key_of(OWNER, OPS), key_of(OWNER2, OPS2)
STEPS = 14


def incident_rows(w, check):
    return {r["id"]: dict(r) for r in w.db.conn.execute("SELECT * FROM incidents WHERE check_id = ?", (check,))}


def open_by_key(w, check):
    return {r["key"]: r for r in incident_rows(w, check).values() if r["state"] == "open"}


def resolved_in(before, after):
    return [r for i, r in after.items() if r["state"] == "resolved" and before.get(i, {}).get("state") == "open"]


# ---------------------------------------------------------------- M1


class M1Walk:
    """K1: a cluster with logs (poison / repair / benign); K2: a cluster in storage with no log, known only through
    the SSV API, whose API tuple is valid or not."""

    def __init__(self, tmp_path, seed):
        self.rng = random.Random(seed)
        self.tmp_path = tmp_path
        self.confirmations = self.rng.choice([0, 2])
        chain = ClusterChain()
        ssv = MiniSSV(chain)
        cid, st = ssv._get(OWNER2, OPS2)
        st["count"] = 2
        ssv._persist(cid, st)
        chain.mine()
        self.k2_tuple = current_tuple(ssv, OWNER2, OPS2)
        self.k2_invalid = self.rng.random() < 0.7
        self.api = "ok"
        self.inv = {"api_url": "https://api.invalid", "api_refresh_s": 0}
        self.chain, self.w, self.ssv = make_m1(tmp_path, chain=chain, ssv=ssv, inventory=self.inv,
                                               confirmations=self.confirmations)
        self._attach()
        deposit(ssv, value=10**18)
        self.chain.mine(self.confirmations + 1)
        self.elems: list[tuple[int, str]] = []  # (block, valid | poison | superseded), canonical, in order
        self.snap = {self.chain.number: copy.deepcopy(self.ssv._get(OWNER, OPS)[1])}
        self.tick()
        self.first = self.chain.number  # forks stay above the worker's first checkpoint

    def _attach(self):
        def http_get(url):
            if self.api == "error":
                raise OSError("sweep: API down")
            rows = []
            if self.api == "ok":
                t = self.k2_tuple
                rows.append(api_row(OWNER2, OPS2, t.validator_count, t.network_fee_index, t.index, True,
                                    t.balance + (1 if self.k2_invalid else 0)))
            return {"clusters": rows, "pagination": {"pages": 1}}

        self.w.inventory.http_get = http_get

    def tick(self):
        self.w.clock.advance(601)
        self.chain.mine()
        advance(self.w)

    def remember(self):
        self.snap[self.chain.number] = copy.deepcopy(self.ssv._get(OWNER, OPS)[1])

    def model(self):
        cp = self.w.checkpoint()["block"]
        active, repairs, repaired_before = False, 0, False
        for b, tag in self.elems:
            if b > cp:
                break
            if tag == "poison":
                active, repaired_before = True, repairs > 0
            elif tag == "valid" and active:
                active, repairs = False, repairs + 1
        return active, ("page" if repaired_before else "warn")

    def act(self, name):
        c, s, here = self.chain, self.ssv, self.chain.number
        if name == "deposit":
            deposit(s, value=self.rng.choice([0, 10**15]))
            self.elems.append((here, "valid"))
        elif name == "multi":
            multi_benign(s, n=self.rng.randint(2, 4))
            self.elems.append((here, "valid"))
        elif name == "poison":
            poison(s, nested_n=self.rng.randint(1, 3))
            self.elems.append((here, "poison"))
        elif name == "repair_then_poison":
            deposit(s, value=0, mine=False)
            poison(s)
            self.elems += [(here, "valid"), (here, "poison")]
        elif name == "poison_then_repair":
            poison(s, mine=False)
            deposit(s, value=0)
            self.elems += [(here, "superseded"), (here, "valid")]
        elif name in ("reorg", "crash"):
            depth = self.rng.randint(1, 5)
            fork = max(self.first, c.number - depth)
            c.reorg(fork)
            base = max(k for k in self.snap if k <= fork)
            s._get(OWNER, OPS)[1].clear()
            s._get(OWNER, OPS)[1].update(copy.deepcopy(self.snap[base]))
            self.snap = {k: v for k, v in self.snap.items() if k <= fork}
            self.elems = [(b, t) for b, t in self.elems if b < fork]
            c.mine(self.rng.randint(1, 3))
            if name == "crash":  # the rewind commits, the process dies before the reorg run
                self.w.check_reorg()
                self.w.scan()
                self.restart()
        elif name in ("api_ok", "api_empty", "api_error"):
            self.api = name[4:]
        elif name == "k2_flip":
            self.k2_invalid = not self.k2_invalid
        elif name == "restart":
            self.restart()
        self.remember()

    def restart(self):
        _, self.w = make_worker(self.tmp_path, only=["m1"], chain=self.chain, views=VIEWS, clock=self.w.clock,
                                checks={"m1": m1_config()}, inventory=self.inv, confirmations=self.confirmations,
                                start_block=self.w.cfg.start_block, activation_block=self.w.cfg.activation_block)
        self._attach()

    def check(self, before, k2_before):
        w = self.w
        after = incident_rows(w, "m1")
        opened = open_by_key(w, "m1")
        if w.db.meta("reorg:m1") is not None:
            assert self.api != "ok", "a pending reorg re-evaluation needs a missing input"
            assert {i: (r["state"], r["severity"]) for i, r in after.items()} == \
                {i: (r["state"], r["severity"]) for i, r in before.items()}, "an uncommitted run changes nothing"
            return k2_before
        stale, severity = self.model()
        # K1: evidence <=> incident, at the model's severity
        assert (K1 in opened) == stale, f"K1 open={K1 in opened}, model stale={stale}"
        if stale:
            assert opened[K1]["severity"] == severity, (opened[K1]["severity"], severity)
        # K2: known only through the API; without it the incident is kept as it was, never opened or resolved
        if self.api == "ok":
            assert (K2 in opened) == self.k2_invalid
        else:
            assert (K2 in opened) == k2_before, "an empty or failed API answer neither opens nor resolves K2"
        for r in resolved_in(before, after):
            ev = json.loads(r["evidence"] or "{}")
            if r["key"] == K2:
                assert self.api == "ok" and not self.k2_invalid and "resolved_by_reorg" not in ev, r
            else:
                assert not stale, r
        return K2 in opened


M1_ACTIONS = ["deposit", "multi", "poison", "poison", "repair_then_poison", "poison_then_repair", "idle", "reorg",
              "crash", "api_ok", "api_ok", "api_empty", "api_error", "k2_flip", "restart"]


@pytest.mark.parametrize("seed", range(40))
def test_m1_adversarial_sweep(tmp_path, seed):
    walk = M1Walk(tmp_path, seed)
    k2_open = K2 in open_by_key(walk.w, "m1")
    assert k2_open == walk.k2_invalid
    for _ in range(STEPS):
        before = incident_rows(walk.w, "m1")
        walk.act(walk.rng.choice(M1_ACTIONS))
        walk.tick()
        k2_open = walk.check(before, k2_open)
    walk.api = "ok"  # inputs recover: everything converges to the model
    walk.tick()
    walk.tick()
    assert walk.w.db.meta("reorg:m1") is None
    walk.check(incident_rows(walk.w, "m1"), k2_open)


# ---------------------------------------------------------------- M2


class M2Walk:
    """Legacy cluster LEG (20 validators) on operators 1-4; operator 1's ETH count moves between blocked
    (2,981 + 20 > 3,000, also below the early headroom 1) and fitting (2,900); reactivate reverts arrive through
    the inbox; the SSV API answers, answers empty, or fails."""

    def __init__(self, tmp_path, seed):
        self.rng = random.Random(seed)
        self.tmp_path = tmp_path
        self.confirmations = self.rng.choice([0, 2])
        self.path = tmp_path / "failed-tx.jsonl"
        self.path.write_text("")
        self.eth = self.rng.choice([2_900, 2_981])
        self.chain, self.w, self.ssv, rows = setup(tmp_path, eth=self.eth, private=True, m2={"early_headroom": 1},
                                                   inbox={"path": str(self.path)}, confirmations=self.confirmations)
        self.truth = list(rows)
        self.api = "ok"
        self._attach()
        self.chain.mine(self.confirmations + 1)
        self.eth_log = [(0, self.eth)]  # (block from which it holds, operator 1 ETH count)
        self.lines: list[int] = []  # the inbox file: one revert block per line, in file order
        self.dead: set[int] = set()  # lines whose block a reorg deleted
        self.ingested: dict[int, int] = {}  # line -> checkpoint of the run that ingested it
        self.chances: list[int] = []  # committed run blocks with the API answering and nothing blocked
        self.tick()
        self.first = self.chain.number  # forks stay above the worker's first checkpoint

    def _attach(self):
        def http_get(url):
            if self.api == "error":
                raise OSError("sweep: API down")
            return {"clusters": list(self.truth) if self.api == "ok" else [], "pagination": {"pages": 1}}

        self.w.inventory.http_get = http_get

    def tick(self):
        self.w.clock.advance(601)
        self.chain.mine()
        advance(self.w)

    def eth_at(self, block):
        return [e for b, e in self.eth_log if b <= block][-1]

    def act(self, name):
        c = self.chain
        if name in ("eth_high", "eth_low"):
            self.eth = 2_981 if name == "eth_high" else 2_900
            set_operator(c, 1, eth=self.eth, ssv=20, private=True)
            self.eth_log.append((c.number, self.eth))
        elif name == "revert":
            cl = self.ssv._cluster(self.ssv._get(LEG, LEG_OPS)[1])
            failed_tx(c, self.path, self.rng.choice(["reactivate", "migrateClusterToETH"]), LEG, LEG_OPS, cl, LEG,
                      exceed_limit(1), mine=False)
            self.lines.append(c.number)
            c.mine()
        elif name in ("reorg", "crash"):
            fork = max(self.first, c.number - self.rng.randint(1, 5))
            # the worker rewinds to its newest recorded block below the fork and undoes every settlement above it
            ancestor = self.w.db.conn.execute("SELECT MAX(number) FROM blocks WHERE number < ?", (fork,)).fetchone()[0]
            c.reorg(fork)
            c.call_stubs = {k: [e for e in v if e[0] < fork] for k, v in c.call_stubs.items()}  # operator counts too
            self.eth_log = [(b, e) for b, e in self.eth_log if b < fork]
            self.eth = self.eth_log[-1][1]
            self.dead |= {i for i, b in enumerate(self.lines) if b >= fork}
            if fork <= self.w.checkpoint()["block"]:
                self.chances = [b for b in self.chances if b <= ancestor]
            c.mine(self.rng.randint(1, 3))
            if name == "crash":
                self.w.check_reorg()
                self.w.scan()
                self.restart()
        elif name in ("api_ok", "api_empty", "api_error"):
            self.api = name[4:]
        elif name == "restart":
            self.restart()

    def restart(self):
        _, self.w = make_worker(self.tmp_path, only=["m2"], chain=self.chain, views=VIEWS, clock=self.w.clock,
                                checks={"m2": {"velocity_window_blocks": 20, "early_headroom": 1}},
                                inventory={"api_url": "https://api.invalid", "api_refresh_s": 0},
                                inbox={"path": str(self.path)}, confirmations=self.confirmations,
                                start_block=self.w.cfg.start_block, activation_block=self.w.cfg.activation_block)
        self._attach()

    def check(self, before):
        w = self.w
        after = incident_rows(w, "m2")
        opened = open_by_key(w, "m2")
        if w.db.meta("reorg:m2") is not None or self.api == "error":
            assert self.api == "error", "only a failed inventory read may leave the run uncommitted"
            assert {i: (r["state"], r["severity"]) for i, r in after.items()} == \
                {i: (r["state"], r["severity"]) for i, r in before.items()}, "an uncommitted run changes nothing"
            return
        cp = w.checkpoint()["block"]
        blocked = self.eth_at(cp) + 20 > 3_000
        # README §5.2: a line is delivered once the checkpoint reached it and every line before it (file order)
        checked = [max(self.lines[:i + 1]) <= cp for i in range(len(self.lines))]
        delivered = [i for i in range(len(self.lines)) if checked[i] and i not in self.dead]
        for b in delivered:
            self.ingested.setdefault(b, cp)
        if self.api == "ok" and not blocked:
            self.chances.append(cp)
        expect_revert = any(not any(ch > self.ingested[b] for ch in self.chances) for b in delivered)
        # a delivered line of a deleted block above the new checkpoint is not re-checked yet (README §5.2 gate): its
        # revert may stay open until the checkpoint reaches the block, then it is dropped
        pending = [i for i in self.dead if i in self.ingested and self.lines[i] > cp]
        self.ingested = {i: v for i, v in self.ingested.items() if i in delivered or i in pending}
        assert ("hard:1" in opened) == blocked, ("hard", blocked, self.api)
        assert ("early:1" in opened) == blocked, ("early", blocked)
        if not (pending and "revert:1" in opened):
            assert ("revert:1" in opened) == expect_revert, ("revert", delivered, self.ingested, self.chances, self.api)
        for r in resolved_in(before, after):
            if r["key"] in ("hard:1", "early:1"):
                assert not blocked, r
            elif r["key"] == "revert:1":
                # settled at a surviving chance (API answering, nothing blocked), or its inbox block was deleted
                assert not expect_revert, (r, json.loads(r["evidence"] or "{}"))


M2_ACTIONS = ["eth_high", "eth_low", "revert", "revert", "idle", "reorg", "crash", "api_ok", "api_ok", "api_empty",
              "api_error", "restart"]


@pytest.mark.m2
@pytest.mark.parametrize("seed", range(40))
def test_m2_adversarial_sweep(tmp_path, seed):
    walk = M2Walk(tmp_path, seed)
    walk.check({})
    for _ in range(STEPS):
        before = incident_rows(walk.w, "m2")
        walk.act(walk.rng.choice(M2_ACTIONS))
        walk.tick()
        walk.check(before)
    walk.api = "ok"
    walk.chain.mine(walk.confirmations + 6)  # the checkpoint passes every orphaned inbox entry
    before = incident_rows(walk.w, "m2")
    walk.tick()
    walk.check(before)
    assert walk.w.db.meta("reorg:m2") is None
