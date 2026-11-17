"""Adversarial sweep for the liquidation family (CP2a repair 1): deterministic seeds drive M3 and M14 through
API omissions, unreadable transactions, view faults, same-block orderings, missed slots, reorgs of depth
1..N below the checkpoint (shorter and longer new chains) and restarts.

Oracle: the fixture's own model (balances, burn rates, parameters, chain time), labelled when each scenario
action is scripted — never the check's output. Invariants after every step:
  1. no resolve without positive proof: a resolution is sent only for a key the oracle says is clear at the
     checkpoint (M3: not liquidatable, not at zero; M14: no qualifying failed liquidation within 24 h of chain
     time before the checkpoint, and none still unreadable);
  2. no open incident without supporting evidence: every open key is supported by the oracle at the
     checkpoint, or an input it needs is UNKNOWN in this step (a view fault, an unreadable transaction, a
     pending reorg re-evaluation);
  3. no dropped page for surviving evidence: on a step whose inputs are all known, every key the oracle
     supports is open, at the oracle's severity (M3 computed zero → page; M14 count and zero);
  4. no window anchored on stale data: M3's age is checkpoint − onset and its severity follows N/N2 from it;
     M14's count is the oracle's count of hits within 24 h of the checkpoint block's own timestamp.
At the end, with every input restored, the lived worker's open incidents equal those of a fresh worker run
once on the final chain.

Repair 2 (QA cp2a-2 H1 and its shape in every check): both sweeps also run with `confirmations` > 0 (M3 seeds
40-59, M14 seeds 60-89), where a reorg from a block near the head onto a shorter chain lowers the checkpoint
below evidence whose blocks survive, and both add a step against a node lagging behind the checkpoint (the
framework treats it as a reorg onto a shorter chain). 5. no event lost between sweeps: a liquidation the worker
already saw (its block was once at or below a checkpoint) and that is above the current checkpoint may hold its
cluster (UNKNOWN until confirmed) but never lets it resolve, and once its block is confirmed again its hit is
counted (invariant 3), so a surviving hit is never dropped. 6. a lowered checkpoint changes nothing it already
evaluated: while the run's block is below the highest block at which an incident was evaluated open, it stays open
at that severity (no resolution, no lowering, no escalation on an earlier block; M3: unless UNKNOWN there).
"""

from __future__ import annotations

import copy
import json
import random

import pytest
from fakechain import FakeClock
from fixtures_liquidation import (
    BURN,
    LTP,
    MIN_COLL,
    OPS,
    OPS2,
    OWNER,
    OWNER2,
    THRESHOLD,
    Clusters,
    LiquidationChain,
    cid_hex,
    failed_liquidation,
    lagging_node,
    open_incidents,
    restart,
    setup,
    stub_liquidation_reverts,
)
from harness import VIEWS, advance, api_row, make_worker, stub_params

from eb_storage import cluster_id

WINDOW = 86_400
SSV_LTP, SSV_BURN = 100_240, 10**9  # harness.stub_params SSV threshold period; a legacy cluster's SSV burn


def new_outbox(w, check, since):
    rows = w.db.conn.execute(
        "SELECT o.rowid, o.kind, o.severity, i.key FROM outbox o JOIN incidents i ON o.incident_id = i.id "
        "WHERE i.check_id=? AND o.rowid > ? ORDER BY o.rowid", (check, since)).fetchall()
    return [dict(r) for r in rows]


def last_rowid(w):
    return w.db.conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM outbox").fetchone()[0]


class Base:
    seen: dict[str, tuple[int, str]]  # key -> (highest run block it was evaluated open at; severity) for invariant 6
    def snapshot(self):
        self.snaps[self.chain.sealed] = copy.deepcopy(self.cl.state)

    def state_at(self, block):
        """The clusters' state at `block` (nothing changes between their creation and the first snapshot)."""
        return self.snaps[max([b for b in self.snaps if b <= block], default=min(self.snaps))]

    def reorg(self, depth, extra, top=None):
        """Replace the last `depth` blocks below `top` (default: the checkpoint); the new chain is `extra` blocks
        long."""
        frm = (self.w.checkpoint()["block"] if top is None else top) - depth + 1
        if frm <= self.start + 2:
            return False
        self.chain.reorg(frm)
        self.cl.state = copy.deepcopy(self.state_at(frm - 1))
        self.snaps = {b: s for b, s in self.snaps.items() if b < frm}
        self.chain.mine(max(extra, 1) - 1)
        self.snapshot()
        if self.chain.sealed >= frm:  # a replaced block exists: the check may prove its evidence gone (6 does not apply)
            self.seen = {k: v for k, v in self.seen.items() if v[0] < frm}
        return frm


# ---------------------------------------------------------------- M3


RANK = {"info": 0, "warn": 1, "page": 2}


class M3Sweep(Base):
    N, N2 = 4, 12

    def __init__(self, tmp_path, seed, confirmations=0):
        self.rng = random.Random(f"m3-sweep:{seed}")
        self.tmp = tmp_path
        self.conf, self.lag = confirmations, 0
        self.seen: dict[str, tuple[int, str]] = {}  # key -> (highest run block it was open at, not faulted; severity)
        chain = LiquidationChain()
        stub_params(chain)
        chain.mine()
        self.cl = cl = Clusters(chain)
        self.ids = ["0x" + f"{i + 1:040x}" for i in range(3)]
        # registered before the scan starts (no log): entering the zone, burning to zero, healthy
        for owner, bal in zip(self.ids, (THRESHOLD + 3 * BURN, 40 * BURN, 10**18)):
            cl.create(owner, OPS, balance=bal)
        chain.mine()
        self.mode = "full"
        self.cfg = {"m3": {"n_blocks": self.N, "n2_blocks": self.N2}}
        self.inv = {"api_url": "https://api.invalid", "api_refresh_s": 0}
        self.chain, self.w, _ = setup(tmp_path, chain=chain, cfg=self.cfg, inventory=self.inv,
                                      confirmations=confirmations)
        self.w.inventory.http_get = self.api
        chain.mine(confirmations)  # the first checkpoint is after the clusters' creation
        self.start = chain.sealed
        self.snaps = {}
        self.run_faults = set()
        self.snapshot()

    def owner(self, key):
        return next(o for o in self.ids if cid_hex(o, OPS) == key)

    def api(self, _url):
        listed = {"full": self.ids, "empty": [], "partial": self.ids[1:]}[self.mode]
        rows = []
        for owner in listed:
            st = self.cl.state[cluster_id(owner, OPS)]
            rows.append(api_row(owner, OPS, st["count"], st["nfi"], st["index"], st["active"], st["balance"]))
        return {"clusters": rows, "pagination": {"pages": 1}}

    def truth(self, owner, block):
        cid = cluster_id(owner, OPS)
        st = self.state_at(block)[cid]
        bal = self.chain.balance_at(cid, block)
        live = st["active"] and st["count"] > 0
        thr = max(MIN_COLL, LTP * self.chain.burn_at(cid, block))
        return {"zone": live and (bal < thr or bal == 0), "zero": live and bal == 0}

    def act(self):
        r, chain, cl = self.rng, self.chain, self.cl
        kind = r.choice(["mine", "mine", "withdraw", "deposit", "liquidate", "api", "api", "fault", "reorg",
                         "restart", "lag"])
        owner = r.choice(self.ids)
        cid = cluster_id(owner, OPS)
        st = cl.state[cid]
        bal = chain.balance_at(cid, chain.number)
        if kind == "mine":
            chain.mine(r.choice([1, 1, 2, 3, 6]))
        elif kind == "withdraw" and st["active"] and bal > THRESHOLD:
            cl.withdraw(owner, OPS, value=bal - r.choice([THRESHOLD - 2 * BURN, 3 * BURN, THRESHOLD + 2 * BURN]))
        elif kind == "deposit" and st["active"]:
            cl.deposit(owner, OPS, value=r.choice([1, 10**18]))
        elif kind == "liquidate" and st["active"] and self.truth(owner, chain.sealed)["zone"]:
            cl.liquidate(owner, OPS)
        elif kind == "api":
            self.mode = r.choice(["full", "empty", "partial"])
        elif kind == "fault":
            if cid in chain.view_faults:
                del chain.view_faults[cid]
            else:
                chain.view_faults[cid] = "revert"
        elif kind == "reorg":
            if self.conf:  # from near the head, often onto a chain whose new checkpoint is below surviving blocks
                self.reorg(r.choice([1, 2, 4, self.conf + 3]), r.choice([1, 2, 6]), top=chain.sealed)
            else:
                self.reorg(r.choice([1, 2, 4]), r.choice([1, 2, 6]))
        elif kind == "restart":
            self.w = restart(self.tmp, self.w, cfg=self.cfg, inventory=self.inv, confirmations=self.conf)
            self.w.inventory.http_get = self.api
        elif kind == "lag":
            self.lag = r.choice([2, 5, self.conf + 4])
        else:
            chain.mine()
        self.snapshot()
        return kind

    def advance(self):
        """One worker step; remembers the view faults the check's last committed run saw."""
        before = self.w.db.meta("run:m3")
        faults = {"0x" + c.hex() for c in self.chain.view_faults}
        behind, self.lag = min(self.lag, self.chain.sealed - self.start - 2), 0
        if behind > 0:
            lagging_node(self.chain, self.w, behind)
        else:
            advance(self.w)
        if self.w.db.meta("run:m3") != before:
            self.run_faults = faults

    def check(self, full, since, step):
        w = self.w
        cp = w.checkpoint()["block"]
        faulted = {"0x" + c.hex() for c in self.chain.view_faults} | self.run_faults
        marker = w.db.meta("reorg:m3") is not None
        truth = {cid_hex(o, OPS): self.truth(o, cp) for o in self.ids}
        opened = open_incidents(w, "m3")
        where = f"step {step} at block {cp}"
        for row in new_outbox(w, "m3", since):
            if row["kind"] == "resolved":  # 1. no resolve without positive proof
                assert not truth[row["key"]]["zone"], f"{where}: resolved {row['key']} still in the zone"
        rb = w.db.meta("run:m3")["block"]  # the last committed run's checkpoint
        held = {k for k, (b, _) in self.seen.items() if rb < b}  # 6: below its evidence, UNKNOWN until re-evaluated
        for key in opened:  # 2. no open incident without supporting evidence
            assert truth[key]["zone"] or key in faulted or marker or key in held, f"{where}: {key} open without evidence"
        if full and not marker:  # 3. no dropped page (the full sweep evaluates every cluster it can see)
            listed = {"full": self.ids, "empty": [], "partial": self.ids[1:]}[self.mode]
            logged = {r[0] for r in w.db.conn.execute("SELECT DISTINCT cluster_id FROM logs")}
            for owner in self.ids:
                key = cid_hex(owner, OPS)
                visible = owner in listed or key[2:] in logged or key in opened
                if truth[key]["zone"] and key not in faulted and visible:
                    assert key in opened, f"{where}: {key} in the zone but not open (mode {self.mode})"
                    if truth[key]["zero"]:
                        assert opened[key]["severity"] == "page", f"{where}: {key} at zero not paged"
        for key in held:  # 6. a lowered checkpoint never resolves or lowers an incident open at a higher run block
            b, sev = self.seen[key]
            assert key in opened and opened[key]["severity"] == sev, \
                f"{where}: {key} open at {sev} at block {b}, now {opened.get(key, {}).get('severity')}"
        for key, inc in opened.items():  # 4. the age window is anchored at the run's checkpoint
            if inc["last_seen_block"] == rb and key not in faulted and self.truth(self.owner(key), rb)["zone"]:
                ev = json.loads(inc["evidence"])
                assert ev["age_blocks"] == rb - ev["onset_block"]
                want = ("page" if self.truth(self.owner(key), rb)["zero"] or ev["age_blocks"] >= self.N2 else
                        "warn" if ev["age_blocks"] >= self.N else "info")
                if ev.get("hold_block"):  # held below its evidence: never lower (6)
                    assert RANK[inc["severity"]] >= RANK[want], f"{where}: {key} {inc['severity']} < {want} ({ev})"
                else:
                    assert inc["severity"] == want, f"{where}: {key} {inc['severity']} != {want} ({ev})"
        for key in list(self.seen):
            if key not in opened and rb >= self.seen[key][0]:
                del self.seen[key]
        for key, inc in opened.items():
            if key not in faulted and not marker and rb >= self.seen.get(key, (-1, None))[0]:
                self.seen[key] = (rb, inc["severity"])


@pytest.mark.parametrize("seed", range(60))
def test_m3_sweep(tmp_path, seed):
    s = M3Sweep(tmp_path, seed, confirmations=0 if seed < 40 else 3)
    s.advance()
    for step in range(30):
        s.act()
        since = last_rowid(s.w)
        full = s.rng.random() < 0.6
        if full:
            s.w.clock.advance(600)
        s.advance()
        s.check(full, since, step)
    s.chain.view_faults.clear()
    s.mode = "full"
    top = max([b for b, _ in s.seen.values()], default=0)
    s.chain.mine(max(s.conf + 1, top + s.conf + 1 - s.chain.sealed))  # the checkpoint passes every evidence and action
    s.w.clock.advance(600)
    s.advance()
    s.check(True, last_rowid(s.w), "final")
    fresh_path = tmp_path / "fresh"
    fresh_path.mkdir()
    _, fresh = make_worker(fresh_path, only=["m3"], chain=s.chain, views=VIEWS, checks=s.cfg, inventory=s.inv,
                           start_block=s.w.cfg.start_block, activation_block=s.w.cfg.activation_block,
                           clock=FakeClock(s.w.clock.now), confirmations=s.conf)
    fresh.inventory.http_get = s.api
    advance(fresh)
    lived, oracle = open_incidents(s.w, "m3"), open_incidents(fresh, "m3")
    assert set(lived) == set(oracle)
    assert {k for k, v in lived.items() if v["severity"] == "page"} >= {k for k, v in oracle.items()
                                                                         if v["severity"] == "page"}


# ---------------------------------------------------------------- M14


class M14Sweep(Base):
    def __init__(self, tmp_path, seed, confirmations=0):
        self.rng = random.Random(f"m14-sweep:{seed}")
        self.keepers = random.Random(f"m14-keepers:{seed}")  # competing liquidations racing one deposit (QA2 M1)
        self.tmp = tmp_path
        self.conf = confirmations
        self.path = tmp_path / "failed-tx.jsonl"
        self.path.write_text("")
        chain = LiquidationChain()
        stub_liquidation_reverts(chain)
        self.chain, self.w, self.cl = setup(tmp_path, checks=("m14",), chain=chain, inbox={"path": str(self.path)},
                                            confirmations=confirmations)
        chain.clock = None  # chain time is slots plus the scripted missed-slot gaps only
        cl = self.cl
        # liquidatable far from zero; healthy; burning to zero; a legacy SSV cluster (SSV units)
        self.clusters = [(OWNER, OPS, "ETH"), (OWNER2, OPS2, "ETH"), (OWNER, OPS2, "ETH"), (OWNER2, OPS, "SSV")]
        cl.create(OWNER, OPS, balance=THRESHOLD - 1_000 * BURN)
        cl.create(OWNER2, OPS2, balance=10**18)
        cl.create(OWNER, OPS2, balance=400 * BURN)
        cl.create(OWNER2, OPS, balance=SSV_LTP * SSV_BURN - 1_000 * SSV_BURN, burn=SSV_BURN, version="SSV")
        self.seen = {}
        self.labels: list[dict] = []  # every failed liquidation scripted: block, cluster, qualifying, tx
        self.deposits: list[tuple[int, int, bytes, int]] = []  # (block, tx position, cluster, value)
        self.unreadable: dict[str, dict] = {}
        self.orphans: list[dict] = []  # labels a reorg removed (their inbox lines stay in the file)
        self.lag = 0
        self.max_cp = 0  # the highest checkpoint so far: a label at or below it was seen by the worker
        self.start = chain.sealed  # reorgs never remove the clusters' creation
        self.snaps = {}
        self.snapshot()

    def threshold(self, version, burn):
        return max(1, SSV_LTP * burn) if version == "SSV" else max(MIN_COLL, LTP * burn)

    def deposit(self, owner, ops, value, mine):
        cid = cluster_id(owner, ops)
        self.cl.deposit(owner, ops, value=value, mine=False)
        self.deposits.append((self.chain.number, self.chain.tx_index, cid, value))
        if mine:
            self.chain.mine()

    def attack(self):
        r, chain, cl = self.rng, self.chain, self.cl
        owner, ops, version = r.choice(self.clusters)
        cid = cluster_id(owner, ops)
        if not cl.state[cid]["active"]:
            return
        mode = r.choice(["same", "prev", "same_topup", "none", "after"])
        burn = chain.burn_at(cid, chain.number)
        value = r.choice([1, 1, 1, burn])
        fn = {"ETH": "liquidate", "SSV": "liquidateSSV"}[version]
        if r.random() < 0.1:
            fn = {"liquidate": "liquidateSSV", "liquidateSSV": "liquidate"}[fn]
        old = cl.tuple(owner, ops)
        if mode in ("same", "same_topup"):
            self.deposit(owner, ops, value, mine=False)
        elif mode == "prev":
            self.deposit(owner, ops, value, mine=True)
        B = chain.number
        st = cl.state[cid]
        bal, burn = chain.balance_at(cid, B), chain.burn_at(cid, B)
        liq = st["active"] and st["count"] > 0 and bal < self.threshold(version, burn)
        tx, _ = failed_liquidation(chain, cl, self.path, owner, ops, fn=fn, tuple_=old)
        pos = chain.tx_index
        dust = [d for d in self.deposits if d[2] == cid and (d[0] == B - 1 or (d[0] == B and d[1] < pos))
                and d[3] < burn]
        q = liq and bool(dust) and fn == {"ETH": "liquidate", "SSV": "liquidateSSV"}[version]
        # README-documented limit of the check: with a later same-block change the state at the failed
        # transaction is derived from the end of B-1 and is knowable only when the deposits before it settle it
        unknown = False
        if mode in ("same_topup", "after") and dust and fn == {"ETH": "liquidate", "SSV": "liquidateSSV"}[version]:
            b0 = chain.balance_at(cid, B - 1)
            liq0 = st["active"] and st["count"] > 0 and b0 < self.threshold(version, chain.burn_at(cid, B - 1))
            total = sum(d[3] for d in self.deposits if d[2] == cid and d[0] == B and d[1] < pos)
            unknown = not ((liq0 and total < burn) or (not liq0 and total >= burn))
        # other keepers' liquidations right after it, with the same tuple: the same deposits precede them
        racers = [failed_liquidation(chain, cl, self.path, owner, ops, fn=fn, tuple_=old)[0]
                  for _ in range(self.keepers.choice([0, 0, 1, 2]))]
        if mode == "same_topup":
            self.deposit(owner, ops, 10**18, mine=False)
        elif mode == "after":
            self.deposit(owner, ops, 1, mine=False)
        if r.random() < 0.2:
            self.unreadable[tx] = chain.txs.pop(tx)
        for t in racers:
            if self.keepers.random() < 0.2:
                self.unreadable[t] = chain.txs.pop(t)
        chain.mine()
        for t in [tx] + racers:  # one time per deposit, however many liquidations it made revert
            self.labels.append({"block": B, "cid": "0x" + cid.hex(), "q": q and not unknown, "tx": t,
                                "unknown": unknown, "dep": dust[-1][:2] if dust else None,
                                "seq": len(self.labels) + len(self.orphans)})

    def act(self):
        r, chain, cl = self.rng, self.chain, self.cl
        kind = r.choice(["attack", "attack", "attack", "mine", "mine", "mine", "topup", "withdraw", "restore", "reorg",
                         "restart", "lag"])
        if kind == "attack":
            self.attack()
        elif kind == "mine":
            chain.elapse(r.choice([0, 0, 3_600, 30_000]))
            chain.mine(r.choice([1, 10, 600, 1_500, 3_000, 7_000]))
        elif kind == "topup":
            owner, ops, _ = r.choice(self.clusters)
            if cl.state[cluster_id(owner, ops)]["active"]:
                self.deposit(owner, ops, r.choice([10**18, 1]), mine=True)
        elif kind == "withdraw":
            owner, ops = OWNER, OPS
            bal = chain.balance_at(cluster_id(owner, ops), chain.number)
            if bal > THRESHOLD:
                cl.withdraw(owner, ops, value=bal - (THRESHOLD - 1_000 * BURN))
        elif kind == "restore" and self.unreadable:
            tx = r.choice(sorted(self.unreadable))
            chain.txs[tx] = self.unreadable.pop(tx)
        elif kind == "reorg":
            if self.conf:  # from near the head, often onto a chain whose new checkpoint is below surviving blocks
                frm = self.reorg(r.choice([1, 2, 4, self.conf + 3, self.conf + 8]), r.choice([1, 2, 3, 8]),
                                 top=chain.sealed)
            else:
                frm = self.reorg(r.choice([1, 2, 5]), r.choice([1, 3, 8]))
            if frm:
                self.orphans += [lb for lb in self.labels if lb["block"] >= frm]
                self.labels = [lb for lb in self.labels if lb["block"] < frm]
                self.deposits = [d for d in self.deposits if d[0] < frm]
        elif kind == "restart":
            self.w = restart(self.tmp, self.w, checks=("m14",), inbox={"path": str(self.path)},
                             confirmations=self.conf)
        elif kind == "lag":
            self.lag = r.choice([2, 5, self.conf + 4, 300, 1_000])  # also across a whole burn to zero
        else:
            chain.mine()
        self.snapshot()

    def advance(self):
        behind, self.lag = min(self.lag, self.chain.sealed - self.start - 2), 0
        if behind > 0:
            lagging_node(self.chain, self.w, behind)
        else:
            advance(self.w)

    def oracle(self, cp):
        now = self.chain.timestamp(cp)
        recent, unread, held = {}, [], set()
        # the inbox delivers in file order and stops at the first line whose block is not confirmed (above cp)
        gate = min([lb["seq"] for lb in self.labels + self.orphans if lb["block"] > cp], default=float("inf"))
        for lb in self.labels:
            if lb["seq"] > gate and lb["block"] <= cp:
                held.add(lb["cid"])  # confirmed, but queued behind an unconfirmed line: delivered later
            elif lb["block"] <= cp and now - self.chain.timestamp(lb["block"]) < WINDOW:
                if lb["tx"] in self.unreadable:
                    unread.append(lb)
                elif lb["unknown"]:
                    held.add(lb["cid"])
                elif lb["q"]:
                    recent.setdefault(lb["cid"], []).append(lb)
        for lb in self.labels + self.orphans:  # 5. seen, now above the checkpoint: UNKNOWN until confirmed again
            if cp < lb["block"] <= self.max_cp:
                if lb["tx"] in self.unreadable:
                    unread.append(lb)  # its cluster is unknown: it may hold any key
                else:
                    held.add(lb["cid"])
        want = {}
        for cid, hits in recent.items():
            owner, ops, version = next(c for c in self.clusters if cid_hex(c[0], c[1]) == cid)
            st = self.state_at(cp)[cluster_id(owner, ops)]
            zero = st["active"] and st["count"] > 0 and self.chain.balance_at(cluster_id(owner, ops), cp) == 0
            times = len({lb["dep"] for lb in hits})  # a time is one front-running deposit (QA cp2a-2 M1 ruling)
            want[cid] = ("page" if zero else "warn" if times >= 3 else "info", times)
        return want, unread, held

    def check(self, since, step):
        w = self.w
        cp = w.checkpoint()["block"]
        marker = w.db.meta("reorg:m14") is not None
        want, unread, held = self.oracle(cp)
        opened = open_incidents(w, "m14")
        where = f"step {step} at block {cp}"
        rb = w.db.meta("run:m14")["block"]
        for key, (b, sev) in self.seen.items():  # 6. a lowered checkpoint never resolves or lowers
            if rb < b:
                held.add(key)
                assert key in opened and opened[key]["severity"] == sev, \
                    f"{where}: {key} open at {sev} at block {b}, now {opened.get(key, {}).get('severity')}"
        for row in new_outbox(w, "m14", since):
            if row["kind"] == "resolved":  # 1
                assert row["key"] not in want and row["key"] not in held and not unread, \
                    f"{where}: resolved {row['key']} without proof"
        for key in opened:  # 2
            assert key in want or key in held or unread or marker, f"{where}: {key} open without a hit in the window"
        if not unread and not marker and not held:
            assert set(opened) == set(want), f"{where}: open {sorted(opened)} != oracle {sorted(want)}"  # 3
            for key, (sev, count) in want.items():
                ev = json.loads(opened[key]["evidence"])
                assert opened[key]["severity"] == sev, f"{where}: {key} {opened[key]['severity']} != {sev}"
                assert ev["count"] == count and ev["chain_time"] == self.chain.timestamp(cp), where  # 4
        for key in list(self.seen):
            if key not in opened and rb >= self.seen[key][0]:
                del self.seen[key]
        for key, inc in opened.items():
            if not marker and rb >= self.seen.get(key, (-1, None))[0]:
                self.seen[key] = (rb, inc["severity"])


@pytest.mark.parametrize("seed", range(90))
def test_m14_sweep(tmp_path, seed):
    s = M14Sweep(tmp_path, seed, confirmations=0 if seed < 60 else 4)
    advance(s.w)
    for step in range(30):
        s.act()
        since = last_rowid(s.w)
        s.w.clock.advance(600)
        s.advance()
        s.max_cp = max(s.max_cp, s.w.checkpoint()["block"])
        s.check(since, step)
    for tx in list(s.unreadable):
        s.chain.txs[tx] = s.unreadable.pop(tx)
    top = max([lb["block"] for lb in s.labels + s.orphans] + [b for b, _ in s.seen.values()], default=0)
    s.chain.mine(max(s.conf + 1, top + s.conf + 1 - s.chain.sealed))  # every inbox line's block is confirmed
    s.w.clock.advance(600)
    advance(s.w)
    s.max_cp = max(s.max_cp, s.w.checkpoint()["block"])
    s.check(last_rowid(s.w), "final")
    fresh_path = tmp_path / "fresh"
    fresh_path.mkdir()
    _, fresh = make_worker(fresh_path, only=["m14"], chain=s.chain, views=VIEWS, checks={},
                           inbox={"path": str(s.path)}, start_block=s.w.cfg.start_block,
                           activation_block=s.w.cfg.activation_block, clock=FakeClock(s.w.clock.now),
                           confirmations=s.conf)
    advance(fresh)
    lived, oracle = open_incidents(s.w, "m14"), open_incidents(fresh, "m14")
    # an entry still UNKNOWN (state at the failed transaction undeterminable) keeps an incident the lived worker had
    # open at its severity, but opens nothing on a fresh worker: the only history-dependent difference allowed
    _, _, held = s.oracle(s.w.checkpoint()["block"])
    rank = {"info": 0, "warn": 1, "page": 2}
    assert set(oracle) <= set(lived) and set(lived) - set(oracle) <= held
    for k, v in lived.items():
        if k in held:
            assert k not in oracle or rank[v["severity"]] >= rank[oracle[k]["severity"]], k
        else:
            assert v["severity"] == oracle[k]["severity"], k
