"""CP2d: deterministic adversarial sweep of M1 and M2 (seeded random walks on FakeChain).

Each walk mixes cluster/operator changes, same-block orderings (two and three transactions, unverifiable
differing-tuple transactions), poisonings repaired inside one run, reorgs (depth 1..5, also below the confirmation
window) also orphaning resolutions, crashes between the rewind and the reorg run, empty/partial/failed SSV API
answers (also on the first sweep), storage changes without logs, inbox reverts, registration bursts and restarts.
After every step an independent model computed from the walk's own action log (never from the check's tables)
asserts:

  no resolve without positive proof   an incident resolved in a step is disproven by the model at that block, and
                                      never by an empty/partial/failed API answer alone;
  no open incident without evidence   every open incident is supported by the model at the checkpoint;
  no dropped page / warn              every condition the model holds at the checkpoint is open at its severity;
                                      same-block re-poison: brute force over every reading of the unverifiable
                                      transactions (coordinator ruling on QA attempt 2 F2);
  no event lost between runs          a poisoning repaired before the run that processed it is reported;
  no silent change of meaning         the last message of an open incident states its current meaning (transient
                                      'repaired before this run' or live stale tuple) at its current severity, also
                                      after a reorg orphans the repair of a transient report (QA attempt 3 F1);
  no window on stale data             M2 velocity counts the window ending at the run's checkpoint;
  never silent on missing input       an empty API answer and an unknown hard rule are health messages.

A step whose run could not commit (reorg re-evaluation pending: README §6.2 (4)) must leave every incident as it
was. Run: reconcile/.venv/bin/python -m pytest -q reconcile/tests/test_m1_m2_sweep.py
"""

from __future__ import annotations

import copy
import itertools
import json
import random

import pytest
from fakechain import MiniSSV, keys
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
from harness import VIEWS, advance, api_row, make_worker, outbox
from test_m1 import two_deposit_helper
from test_m2 import ETH_OPS, ETH_OWNER, LEG, LEG_OPS, setup

pytestmark = pytest.mark.m1

K1, K2 = key_of(OWNER, OPS), key_of(OWNER2, OPS2)
OWNER3, OPS3 = "0x" + "e5" * 20, (13, 14, 15, 16)  # an unrelated, always valid cluster: the "partial" API lists it
STEPS = 14


def incident_rows(w, check):
    return {r["id"]: dict(r) for r in w.db.conn.execute("SELECT * FROM incidents WHERE check_id = ?", (check,))}


def open_by_key(w, check):
    return {r["key"]: r for r in incident_rows(w, check).values() if r["state"] == "open"}


def resolved_in(before, after):
    return [r for i, r in after.items() if r["state"] == "resolved" and before.get(i, {}).get("state") == "open"]


def run_block(w, check):
    return (w.db.meta(f"run:{check}") or {}).get("block")


def health_keys(w, check):
    return [r["key"] for r in outbox(w, f"kind='health' AND check_id='{check}'")]


def unverified_re_poison(pre_poisoned: bool, tags: list[str]) -> bool:
    """Brute force: does some reading of the block's unverifiable transactions (each a poisoning or a repair) give
    poison -> repair -> poison, ending with the block's verified final poisoning?"""
    slots = [i for i, t in enumerate(tags[:-1]) if t == "unverified"]
    for reading in itertools.product("PC", repeat=len(slots)):
        seq = ["P"] if pre_poisoned else []
        it = iter(reading)
        seq += [next(it) if t == "unverified" else "C" for t in tags[:-1]] + ["P"]
        body = "".join(seq[:-1])  # a poisoning, then a repair, before the final poisoning
        if "P" in body and "C" in body[body.index("P") + 1:]:
            return True
    return False


# ---------------------------------------------------------------- M1


class M1Walk:
    """K1: a cluster with logs (poison / repair / benign / unverifiable helper transactions); K2: a cluster in storage
    with no log, known only through the SSV API, whose API tuple is valid or not and whose storage tuple may change
    without a log; K3: an unrelated valid cluster, the only one a partial API answer lists."""

    def __init__(self, tmp_path, seed):
        self.rng = random.Random(seed)
        self.tmp_path = tmp_path
        self.confirmations = self.rng.choice([0, 2])
        chain = ClusterChain()
        ssv = MiniSSV(chain)
        for owner, ops in ((OWNER2, OPS2), (OWNER3, OPS3)):
            cid, st = ssv._get(owner, ops)
            st["count"] = 2
            ssv._persist(cid, st)
        chain.mine()
        self.k2_invalid = self.rng.random() < 0.7
        self.api = self.rng.choice(["ok", "ok", "empty"])  # also an empty FIRST inventory (QA attempt 2 F4)
        self.inv = {"api_url": "https://api.invalid", "api_refresh_s": 0}
        self.chain, self.w, self.ssv = make_m1(tmp_path, chain=chain, ssv=ssv, inventory=self.inv,
                                               confirmations=self.confirmations)
        self._attach()
        deposit(ssv, value=10**18)
        self.chain.mine(self.confirmations + 1)
        self.elems: list[tuple[int, list[str]]] = []  # (block, K1 transactions' tags in log order), canonical
        self.snap = {self.chain.number: self._state()}
        self.lo = -1  # the next committed run processes the blocks above this one
        self.ancestor = None  # lowest ancestor of the reorgs since the last committed run
        self.queue: list[str] = []  # follow-up actions of a composite attack
        self.k2_write = 0
        self.repair_block = 0
        self.tick()
        self.lo = run_block(self.w, "m1")
        self.first = self.chain.number  # forks stay above the worker's first checkpoint

    def _state(self):
        return copy.deepcopy((self.ssv._get(OWNER, OPS)[1], self.ssv._get(OWNER2, OPS2)[1]))

    def _attach(self):
        def http_get(url):
            if self.api == "error":
                raise OSError("sweep: API down")
            rows = []
            if self.api in ("ok", "partial"):
                t3 = current_tuple(self.ssv, OWNER3, OPS3)
                rows.append(api_row(OWNER3, OPS3, t3.validator_count, t3.network_fee_index, t3.index, True,
                                    t3.balance))
            if self.api == "ok":
                t = current_tuple(self.ssv, OWNER2, OPS2)
                rows.append(api_row(OWNER2, OPS2, t.validator_count, t.network_fee_index, t.index, True,
                                    t.balance + (1 if self.k2_invalid else 0)))
            return {"clusters": rows, "pagination": {"pages": 1}}

        self.w.inventory.http_get = http_get

    def tick(self):
        self.w.clock.advance(601)
        self.chain.mine()
        advance(self.w)

    def remember(self):
        self.snap[self.chain.number] = self._state()

    def model(self):
        """(kind, severity) of K1 at the checkpoint: ("stale", sev), ("transient", sev) or None."""
        cp = self.w.checkpoint()["block"]
        active, repairs, episode_page, transient = False, 0, False, []
        for b, tags in self.elems:
            if b > cp:
                break
            pre = active
            for t in tags[:-1]:
                if t == "clean" and active:  # one tuple: written and emitted, a verified repair at its position
                    active, repairs, episode_page = False, repairs + 1, False
            if tags[-1] == "poison":
                re_poisoned = repairs > 0 or unverified_re_poison(pre, tags)
                episode_page = (episode_page and active) or re_poisoned
                active = True
                if b > self.lo:
                    transient.append(re_poisoned)
            elif active:
                active, repairs, episode_page = False, repairs + 1, False
        if active:
            return "stale", "page" if episode_page else "warn"
        if transient:
            return "transient", "page" if any(transient) else "warn"
        return None

    def act(self, name):
        c, s, here = self.chain, self.ssv, self.chain.number
        if name == "deposit":
            deposit(s, value=self.rng.choice([0, 10**15]))
            self.elems.append((here, ["clean"]))
        elif name == "multi":
            multi_benign(s, n=self.rng.randint(2, 4))
            self.elems.append((here, ["clean"]))
        elif name == "poison":
            poison(s, nested_n=self.rng.randint(1, 3))
            self.elems.append((here, ["poison"]))
        elif name == "repair_then_poison":
            deposit(s, value=0, mine=False)
            poison(s)
            self.elems.append((here, ["clean", "poison"]))
        elif name == "poison_then_repair":
            poison(s, mine=False)
            deposit(s, value=0)
            self.elems.append((here, ["unverified", "clean"]))
        elif name == "poison_repair_poison":  # QA attempt 2 F3
            poison(s, mine=False)
            deposit(s, value=0, mine=False)
            poison(s)
            self.elems.append((here, ["unverified", "clean", "poison"]))
        elif name == "helper_poison_then":  # QA attempt 2 F2: two-tuple helper, poisoning, then poison or repair
            two_deposit_helper(s)
            poison(s, mine=False)
            last = self.rng.choice(["poison", "clean"])
            if last == "poison":
                poison(s)
            else:
                deposit(s)
            self.elems.append((here, ["unverified", "unverified", last]))
        elif name == "helper_then_poison":
            two_deposit_helper(s)
            poison(s)
            self.elems.append((here, ["unverified", "poison"]))
        elif name == "poison_next_block_repair":  # both blocks processed by one run (events between runs)
            poison(s)
            self.elems.append((here, ["poison"]))
            self.remember()
            deposit(s)
            self.elems.append((here + 1, ["clean"]))
            if self.rng.random() < 0.5:
                # QA attempt 3 F1: this step's run reports it as transient (both blocks confirmed, also further than
                # the confirmation window), then the next step's reorg orphans the repair; the poisoning survives
                self.repair_block = here + 1
                c.mine(self.confirmations + self.rng.randint(0, 3))
                self.queue.append("reorg_repair")
        elif name == "k2_fix":  # QA attempt 2 F1, step 1: K2's storage changes and the API gives it validly
            self.api, self.k2_invalid = "ok", False
            cid, st = s._get(OWNER2, OPS2)
            self.k2_write = c.number
            st["balance"] += 9
            s._persist(cid, st)
            c.mine(self.confirmations + 1)
            self.queue.append("reorg_partial")
        elif name in ("reorg", "crash", "reorg_partial", "reorg_repair"):
            depth = self.rng.randint(1, 5)
            fork = max(self.first, c.number - depth)
            if name == "reorg_partial":  # step 2: orphan the change and its resolution; the API omits K2
                self.api = self.rng.choice(["partial", "empty"])
                fork = max(self.first, self.k2_write)
            if name == "reorg_repair":  # orphan exactly the repair (and what follows); the poisoning survives
                fork = max(self.first, self.repair_block)
                if self.rng.random() < 0.5:
                    name = "crash"
            ancestor = self.w.db.conn.execute("SELECT MAX(number) FROM blocks WHERE number < ?", (fork,)).fetchone()[0]
            c.reorg(fork)
            base = self.snap[max(k for k in self.snap if k <= fork)]
            for (owner, ops), st in zip(((OWNER, OPS), (OWNER2, OPS2)), copy.deepcopy(base)):
                s._get(owner, ops)[1].clear()
                s._get(owner, ops)[1].update(st)
            self.snap = {k: v for k, v in self.snap.items() if k <= fork}
            self.elems = [(b, t) for b, t in self.elems if b < fork]
            if ancestor is not None and ancestor < self.w.checkpoint()["block"]:
                self.lo = min(self.lo, ancestor)
                self.ancestor = ancestor if self.ancestor is None else min(self.ancestor, ancestor)
            c.mine(self.rng.randint(1, 3))
            if name == "crash":  # the rewind commits, the process dies before the reorg run
                self.w.check_reorg()
                self.w.scan()
                self.restart()
        elif name in ("api_ok", "api_empty", "api_error", "api_partial"):
            self.api = name[4:]
        elif name == "k2_flip":
            self.k2_invalid = not self.k2_invalid
        elif name == "k2_storage":  # K2's tuple changes in storage, no log (QA attempt 2 F1 resolution shape)
            cid, st = s._get(OWNER2, OPS2)
            st["balance"] += 9
            s._persist(cid, st)
            c.mine(self.confirmations + 1)  # the API answers live; the model compares it at the checkpoint
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
        expect = self.model()
        self.lo = run_block(w, "m1")
        if self.ancestor is not None and "reorg" in (w.db.meta("run:m1") or {}).get("reasons", []):
            # README §6.2 (5): a resolution above the ancestor may be orphaned; K2 is in storage with no log, so without
            # API data its committed reorg run cannot have re-evaluated it
            orphaned = [r for r in before.values() if r["key"] == K2 and r["state"] == "resolved"
                        and r["resolved_block"] > self.ancestor]
            assert self.api == "ok" or not orphaned, ("orphaned K2 resolution accepted without proof", orphaned)
            self.ancestor = None
        # K1: evidence <=> incident, at the model's severity; a poisoning repaired inside the run is still reported
        assert (K1 in opened) == (expect is not None), f"K1 open={K1 in opened}, model={expect}"
        if expect is not None:
            assert opened[K1]["severity"] == expect[1], (opened[K1]["severity"], expect)
            # no silent change of meaning: the last message sent for the incident says what it means now
            sent = outbox(w, f"incident_id = '{opened[K1]['id']}' AND kind IN ('open', 'update', 'escalate', 'realert')")
            assert sent, "an open K1 incident was never sent"
            assert ("repaired before this run" in sent[-1]["text"]) == (expect[0] == "transient"), (sent[-1], expect)
            assert sent[-1]["severity"] == expect[1], (sent[-1]["severity"], expect)
        # K2: known only through the API; without it the incident is kept as it was, never opened or resolved
        if self.api == "ok":
            assert (K2 in opened) == self.k2_invalid
        else:
            assert (K2 in opened) == k2_before, "an empty, partial or failed API answer neither opens nor resolves K2"
        if self.api == "empty":
            assert any(k.startswith("health:m1-inventory-empty") for k in health_keys(w, "m1")), "never silent"
        for r in resolved_in(before, after):
            ev = json.loads(r["evidence"] or "{}")
            if r["key"] == K2:
                assert self.api == "ok" and not self.k2_invalid and "resolved_by_reorg" not in ev, r
            else:
                assert expect is None, r
        return K2 in opened


M1_ACTIONS = ["deposit", "multi", "poison", "poison", "repair_then_poison", "poison_then_repair",
              "poison_repair_poison", "helper_poison_then", "helper_then_poison", "poison_next_block_repair", "idle",
              "reorg", "reorg", "crash", "api_ok", "api_ok", "api_empty", "api_error", "api_partial", "k2_flip",
              "k2_storage", "k2_fix", "restart"]


@pytest.mark.parametrize("seed", range(60))
def test_m1_adversarial_sweep(tmp_path, seed):
    walk = M1Walk(tmp_path, seed)
    k2_open = K2 in open_by_key(walk.w, "m1")
    assert k2_open == (walk.k2_invalid and walk.api == "ok")
    if walk.api == "empty":
        assert any(k.startswith("health:m1-inventory-empty") for k in health_keys(walk.w, "m1"))
    for _ in range(STEPS):
        before = incident_rows(walk.w, "m1")
        walk.act(walk.queue.pop(0) if walk.queue else walk.rng.choice(M1_ACTIONS))
        walk.tick()
        k2_open = walk.check(before, k2_open)
    walk.api = "ok"  # inputs recover: everything converges to the model
    for _ in range(2):
        before = incident_rows(walk.w, "m1")
        walk.tick()
        k2_open = walk.check(before, k2_open)
    assert walk.w.db.meta("reorg:m1") is None


# ---------------------------------------------------------------- M2


OTHER, OTHER_OPS = "0x" + "b2" * 20, (5, 6, 7, 8)  # an unrelated legacy cluster: the "partial" API lists it


class M2Walk:
    """Legacy cluster LEG (20 validators) on operators 1-4; operator 1's ETH count moves between blocked
    (2,981 + 20 > 3,000, also below the early headroom 1) and fitting (2,900); LEG's tuple may change in storage
    without a log (a tuple only the API can tell); reactivate reverts arrive through the inbox; registration bursts
    on operator 1 (velocity window 20 blocks); the SSV API answers, answers empty, lists only OTHER, or fails."""

    def __init__(self, tmp_path, seed):
        self.rng = random.Random(seed)
        self.tmp_path = tmp_path
        self.confirmations = self.rng.choice([0, 2])
        self.path = tmp_path / "failed-tx.jsonl"
        self.path.write_text("")
        self.eth = self.rng.choice([2_900, 2_981])
        self.chain, self.w, self.ssv, rows = setup(tmp_path, eth=self.eth, private=True, m2={"early_headroom": 1},
                                                   inbox={"path": str(self.path)}, confirmations=self.confirmations)
        cid = self.ssv.legacy_ssv_cluster(OTHER, OTHER_OPS, 1)
        assert cid
        for op in OTHER_OPS:
            set_operator(self.chain, op, eth=0, ssv=1, private=True)
        self.api = "ok"
        self._attach()
        self.chain.mine(self.confirmations + 1)
        self.eth_log = [(0, self.eth)]  # (block from which it holds, operator 1 ETH count)
        self.leg_ver = 0  # version of LEG's storage tuple
        self.leg_snap = {0: (0, copy.deepcopy(self.ssv._get(LEG, LEG_OPS)[1]))}  # block of the write -> version
        self.validated = set()  # LEG tuple versions M2 has seen validated (by a committed run with the API listing it)
        self.ingested_vers: set[int] = set()  # LEG tuple versions M2 persisted from an ingested revert's calldata
        self.bursts: list[tuple[int, int]] = []  # (block, registrations to operator 1)
        self.pubkeys = 0
        self.lines: list[int] = []  # the inbox file: one revert block per line, in file order
        self.line_ver: list[int] = []  # LEG's tuple version in each line's calldata
        self.dead: set[int] = set()  # lines whose block a reorg deleted
        self.ingested: dict[int, int] = {}  # line -> checkpoint of the run that ingested it
        self.chances: list[int] = []  # committed run blocks where a settlement is provable
        self.queue: list[str] = []  # follow-up actions of a composite attack
        self.leg_write = 0
        self.tick()
        self.validated.add(0)
        self.first = self.chain.number  # forks stay above the worker's first checkpoint

    def _attach(self):
        def http_get(url):
            if self.api == "error":
                raise OSError("sweep: API down")
            rows = []
            for owner, ops in ((LEG, LEG_OPS), (OTHER, OTHER_OPS)):
                if self.api == "ok" or (self.api == "partial" and owner == OTHER):
                    st = self.ssv._get(owner, ops)[1]
                    rows.append(api_row(owner, ops, st["count"], st["nfi"], st["index"], st["active"], st["balance"],
                                        migrated=False))
            return {"clusters": rows, "pagination": {"pages": 1}}

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
            self.line_ver.append(self.leg_ver)  # the reverted call's own tuple: a candidate M2 persists
            c.mine()
        elif name == "leg_storage":  # QA attempt 2 F1: the tuple changes without a log; only the API can tell it
            cid, st = self.ssv._get(LEG, LEG_OPS)
            st["balance"] += 7
            self.ssv._persist(cid, st)
            self.leg_ver = max(v for v, _ in self.leg_snap.values()) + 1
            self.leg_snap[c.number] = (self.leg_ver, copy.deepcopy(st))
        elif name == "burst":
            n = self.rng.choice([10, 25])
            self.bursts.append((c.number, n))
            self.ssv.register(ETH_OWNER, ETH_OPS, keys(n, self.pubkeys))  # mines its block
            self.pubkeys += n
        elif name == "leg_fix":  # QA attempt 2 F1, step 1: LEG's tuple changes, operator 1 fits, the API validates it
            self.api = "ok"
            self.act("leg_storage")
            self.leg_write = c.number
            self.act("eth_low")
            c.mine(self.confirmations + 1)
            self.queue.append("reorg_partial")
        elif name in ("reorg", "crash", "reorg_partial"):
            fork = max(self.first, c.number - self.rng.randint(1, 5))
            if name == "reorg_partial":  # step 2: orphan both and the resolution; the API omits LEG
                self.api = self.rng.choice(["partial", "empty"])
                fork = max(self.first, self.leg_write)
            # the worker rewinds to its newest recorded block below the fork and undoes every settlement above it
            ancestor = self.w.db.conn.execute("SELECT MAX(number) FROM blocks WHERE number < ?", (fork,)).fetchone()[0]
            c.reorg(fork)
            c.call_stubs = {k: [e for e in v if e[0] < fork] for k, v in c.call_stubs.items()}  # operator counts too
            self.eth_log = [(b, e) for b, e in self.eth_log if b < fork]
            self.eth = self.eth_log[-1][1]
            self.leg_snap = {b: v for b, v in self.leg_snap.items() if b < fork}
            self.leg_ver, st = copy.deepcopy(self.leg_snap[max(self.leg_snap)])
            self.ssv._get(LEG, LEG_OPS)[1].clear()
            self.ssv._get(LEG, LEG_OPS)[1].update(st)
            self.bursts = [(b, n) for b, n in self.bursts if b < fork]
            self.dead |= {i for i, b in enumerate(self.lines) if b >= fork}
            if fork <= self.w.checkpoint()["block"]:
                self.chances = [b for b in self.chances if b <= ancestor]
            c.mine(self.rng.randint(1, 3))
            if name == "crash":
                self.w.check_reorg()
                self.w.scan()
                self.restart()
        elif name in ("api_ok", "api_empty", "api_error", "api_partial"):
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
        cp = w.checkpoint()["block"]
        blocked = self.eth_at(cp) + 20 > 3_000
        ver = self.leg_snap[max(b for b in self.leg_snap if b <= cp)][0]  # LEG's storage tuple at the checkpoint
        # README §5.2: a line is delivered once the checkpoint reached it and every line before it (file order)
        checked = [max(self.lines[:i + 1]) <= cp for i in range(len(self.lines))]
        delivered = [i for i in range(len(self.lines)) if checked[i] and i not in self.dead]
        candidates = self.validated | {self.line_ver[i] for i in delivered} | self.ingested_vers
        known = ver in candidates
        if w.db.meta("reorg:m2") is not None or self.api == "error":
            assert self.api == "error" or (not known and blocked), \
                "only a failed inventory read or an unproven hard rule may leave the run uncommitted"
            assert {i: (r["state"], r["severity"]) for i, r in after.items()} == \
                {i: (r["state"], r["severity"]) for i, r in before.items()}, "an uncommitted run changes nothing"
            return
        if self.api == "ok" and ver == self.leg_ver:  # the API answers live: it validates only once cp reached it
            self.validated.add(ver)
            known = True
        self.ingested_vers |= {self.line_ver[i] for i in delivered}  # persisted; kept even if the line dies later
        for b in delivered:
            self.ingested.setdefault(b, cp)
        if not blocked and known and self.api in ("ok", "partial"):
            self.chances.append(cp)
        expect_revert = any(not any(ch > self.ingested[b] for ch in self.chances) for b in delivered)
        # a delivered line of a deleted block above the new checkpoint is not re-checked yet (README §5.2 gate): its
        # revert may stay open until the checkpoint reaches the block, then it is dropped
        pending = [i for i in self.dead if i in self.ingested and self.lines[i] > cp]
        self.ingested = {i: v for i, v in self.ingested.items() if i in delivered or i in pending}
        before_open = {r["key"] for r in before.values() if r["state"] == "open"}
        if known:
            assert ("hard:1" in opened) == blocked, ("hard", blocked, self.api)
        elif blocked:  # unknown tuple: kept as it was (never opened without a validated tuple), and reported
            assert ("hard:1" in opened) == ("hard:1" in before_open), ("hard unknown", self.api)
            assert any(k.startswith("health:m2-hard-unknown") for k in health_keys(w, "m2")), "never silent"
        else:  # the on-chain count proves no cluster on the operator is blocked
            assert "hard:1" not in opened
        assert ("early:1" in opened) == blocked, ("early", blocked)
        regs = sum(n for b, n in self.bursts if cp - 20 < b <= cp)  # the window ends at the run's checkpoint
        assert ("velocity:1" in opened) == (regs > 3_000 - self.eth_at(cp)), ("velocity", regs, self.bursts, cp)
        if self.api == "empty":
            assert any(k.startswith("health:m2-inventory-empty") for k in health_keys(w, "m2")), "never silent"
        if not (pending and "revert:1" in opened):
            assert ("revert:1" in opened) == expect_revert, ("revert", delivered, self.ingested, self.chances, self.api)
        for r in resolved_in(before, after):
            if r["key"] in ("hard:1", "early:1"):
                assert not blocked, r
            elif r["key"] == "revert:1":
                # settled at a surviving chance (proof at that block), or its inbox block was deleted
                assert not expect_revert, (r, json.loads(r["evidence"] or "{}"))


M2_ACTIONS = ["eth_high", "eth_low", "revert", "revert", "leg_storage", "leg_fix", "burst", "idle", "reorg", "reorg",
              "crash", "api_ok", "api_ok", "api_empty", "api_error", "api_partial", "restart"]


@pytest.mark.m2
@pytest.mark.parametrize("seed", range(60))
def test_m2_adversarial_sweep(tmp_path, seed):
    walk = M2Walk(tmp_path, seed)
    walk.check({})
    for _ in range(STEPS):
        before = incident_rows(walk.w, "m2")
        walk.act(walk.queue.pop(0) if walk.queue else walk.rng.choice(M2_ACTIONS))
        walk.tick()
        walk.check(before)
    walk.api = "ok"
    walk.chain.mine(walk.confirmations + 6)  # the checkpoint passes every orphaned inbox entry
    before = incident_rows(walk.w, "m2")
    walk.tick()
    walk.check(before)
    assert walk.w.db.meta("reorg:m2") is None
