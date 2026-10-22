"""CP2b seeded adversarial sweep (repair 1, qa/DEFECT-CLASSES.md): random oracle and updater histories with random
worker schedules (steps, downtime, clock-only stalls, restarts, restarts between reorg detection and handling, RPC
outages, reorgs of depth 1..N across the confirmation window, partial and failing proof API answers).

After every worker step the open incidents are compared with an *independent* evaluation of the spec rules on the
canonical chain the worker has stored (this file re-derives the rules from the spec text, it does not call the
checks), and the message history is checked at the end:

- no open incident without supporting evidence (open keys and severities = the independent evaluation);
- no dropped page for surviving evidence (the same equality, the other way round);
- no resolve without positive proof (a key resolves only when the independent evaluation no longer holds it, and
  an M11 weekly mismatch only when every reported cluster had a matching leaf at that step);
- no window anchored on stale data (deadlines from chain timestamps and the horizon, clock-only stalls included);
- no event lost between sweeps (every failed round, rotation and duplicate assignment of the final chain was sent);
- health flag consistent (an RPC outage step changes no incident; a partial/failed proof answer leaves a health
  message).
"""

from __future__ import annotations

import copy
import json
import random
import re

import pytest
from fixtures_oracle import (
    SLOTS,
    UpdaterWorld,
    open_keys,
    oracle_worker,
    restart,
    sent,
    tick,
)

from eb_events import TOPIC0

ZERO = "0x" + "00" * 20
REORG_TEXT = "Evidence left the canonical chain"


# ---------------------------------------------------------------- independent evaluation (spec §3 M9, M10)


def stored_logs(w, oc):
    """The proxy logs of the canonical chain up to the worker's checkpoint, in chain order."""
    cp = w.checkpoint()["block"]
    out = [lg for lg in oc.c.logs if int(lg["blockNumber"], 16) <= cp]
    return sorted(out, key=lambda lg: (int(lg["blockNumber"], 16), int(lg["logIndex"], 16))), cp


def horizon(w, oc, cp, confirmations):
    return max(oc.c.ts(cp), w.clock() - (confirmations + 2) * 12)


def expected_m9_m10(w, oc, start, confirmations, timeout=1800, max_gap=32_400):
    logs, cp = stored_logs(w, oc)
    now = horizon(w, oc, cp, confirmations)
    wrp, rc, rep = TOPIC0["WeightedRootProposed"], TOPIC0["RootCommitted"], TOPIC0["OracleReplaced"]
    rounds, commits, rotations = {}, [], []
    for lg in logs:
        pos = (int(lg["blockNumber"], 16), int(lg["logIndex"], 16))
        t0 = lg["topics"][0]
        if t0 == wrp:
            words = [int(lg["data"][2 + 64 * i: 2 + 64 * (i + 1)], 16) for i in range(4)]
            ref = int(lg["topics"][2], 16)
            rounds.setdefault(ref, []).append({"pos": pos, "root": lg["topics"][1], "slot": words[2]})
        elif t0 == rc:
            commits.append({"pos": pos, "root": lg["topics"][1], "ref": int(lg["topics"][2], 16)})
        elif t0 == rep:
            rotations.append({"pos": pos, "slot": int(lg["topics"][1], 16), "old": "0x" + lg["topics"][2][-40:],
                              "new": "0x" + lg["topics"][3][-40:]})
    exp = {}

    def own(ref):
        return next((c for c in commits if c["ref"] == ref), None)

    def closing(ref, before=None):
        cands = [c for c in commits if c["ref"] >= ref and (before is None or c["pos"] < before)]
        return min(cands, key=lambda c: c["pos"]) if cands else None

    settled_rounds = []
    for ref in sorted(rounds):
        votes = rounds[ref]
        deadline = oc.c.ts(votes[0]["pos"][0]) + timeout
        c_own, close = own(ref), closing(ref)
        # spec M9 line 1, literally: no RootCommitted of this blockNum within 30 min (any stored vote, start_block or
        # not); open while no commit closed the round (a later blockNum's commit makes it un-committable)
        failed = not (c_own and oc.c.ts(c_own["pos"][0]) <= deadline) and now > deadline
        if failed and close is None:
            exp["failed-round"] = "page"
        if ref < start:
            continue
        settled = close is not None or now > deadline
        roots = [v["root"] for v in votes]
        if c_own:
            reference = c_own["root"]
        elif settled:  # the root most distinct slots voted, when unique and voted by >= 2 slots (QA3-F2)
            slots_of = {r: {v["slot"] for v in votes if v["root"] == r} for r in set(roots)}
            top = max(len(b) for b in slots_of.values())
            leaders = {r for r, b in slots_of.items() if len(b) == top}
            reference = leaders.pop() if len(leaders) == 1 and top >= 2 else None
        else:
            reference = None
        superseded = c_own is None and any(c["ref"] > ref for c in commits)
        if len(set(roots)) > 1 and reference is None and not superseded:
            exp["split"] = "warn"
        if settled:
            settled_rounds.append((votes, reference))
    state = {}  # slot -> [run, longest]
    for votes, reference in settled_rounds:
        if reference is None:
            for st in state.values():
                st[0] = 0
            continue
        by = {}
        for v in votes:
            by.setdefault(v["slot"], set()).add(v["root"])
        for slot in set(by) | set(state):
            if slot in by and by[slot] == {reference}:
                if any(o != slot and reference in r for o, r in by.items()):
                    state.pop(slot, None)  # positive proof: another slot voted the same root
                elif slot in state:
                    state[slot][0] = 0  # agreeing with itself only: neither heals nor extends (QA3-F2)
            elif slot in by:
                st = state.setdefault(slot, [0, 0])
                st[0] += 1
                st[1] = max(st[1], st[0])
            else:
                state[slot][0] = 0
    for slot, (_, longest) in state.items():
        exp[f"dissent:{slot}"] = "page" if longest >= 3 else "warn"
    last = commits[-1]["pos"][0] if commits else start
    if now - oc.c.ts(last) > max_gap:
        exp["cadence"] = "page"
    committed = [(rounds.get(c["ref"], []), c) for c in commits if c["ref"] >= start and own(c["ref"]) is c]
    if len(committed) >= 6:
        for slot in (1, 2, 3, 4):
            missed = 0
            for votes, c in committed:
                missed = 0 if any(v["slot"] == slot and v["root"] == c["root"] for v in votes) else missed + 1
            if missed >= 6:
                exp[f"absent:{slot}"] = "warn"
    m10 = {}
    for r in rotations:
        opened = [ref for ref, votes in rounds.items() if any(v["pos"] < r["pos"] for v in votes)
                  and closing(ref, before=r["pos"]) is None]
        if any(closing(ref) is None for ref in opened):
            m10[f"rotation:{r['pos'][0]}:{r['pos'][1]}"] = "page"
    slots = dict(SLOTS)
    for r in rotations:
        slots[r["slot"]] = r["new"]
    for addr in set(slots.values()):
        if addr != ZERO and sum(a == addr for a in slots.values()) > 1:
            m10[f"dup:{addr}"] = "page"
    return exp, m10, rounds, rotations, commits


# ---------------------------------------------------------------- the M9 + M10 sweep


class Sweep:
    def __init__(self, tmp_path, seed, confirmations):
        self.rng = random.Random(seed)
        self.tmp, self.seed, self.conf = tmp_path, seed, confirmations
        self.oc, self.w = oracle_worker(tmp_path, ids=("m9", "m10"), confirmations=confirmations)
        tick(self.w, self.oc, confirmations + 2)
        self.start = self.w.cfg.start_block
        self.floor = self.oc.c.number  # reorgs never go below the worker's first blocks
        self.log = []
        self.fresh = 0
        self.min_fork = 0
        self.resolved_seen = 0
        self.bad_build = False
        self.snaps = [(self.oc.c.number, copy.deepcopy(self.state()))]
        mine = self.oc.c.mine

        def mine_and_snapshot(n=1):  # the model state at the start of every block whose state changed
            for _ in range(n):
                mine(1)
                st = self.state()
                if st != self.snaps[-1][1]:
                    self.snaps.append((self.oc.c.number, copy.deepcopy(st)))
            return self.oc.c.number

        self.oc.c.mine = mine_and_snapshot

    def state(self):
        oc = self.oc
        return (oc.acc, oc.voted, oc.latest, oc.slots)

    def new_key(self):
        self.fresh += 1
        return "0x%040x" % (0x5EED0000 + 1000 * self.seed + self.fresh)

    # -- worker schedule

    def step(self, blocks=0, seconds=0.0):
        self.log.append(("step", blocks, seconds))
        before = (open_keys(self.w, "m9"), open_keys(self.w, "m10"))
        self.runs_before = {cid: self.w.db.meta(f"run:{cid}") for cid in ("m9", "m10")}
        mode = self.rng.random()
        if mode < 0.06:  # RPC outage: the step fails; no incident may change
            self.oc.c.mine(blocks)
            self.w.clock.advance(seconds + 12 * blocks)
            self.oc.c.down = True
            self.w.step()
            self.oc.c.down = False
            assert (open_keys(self.w, "m9"), open_keys(self.w, "m10")) == before, self.trace("outage changed state")
            return
        if mode < 0.12:
            self.log.append(("restart",))
            self.w = restart(self.tmp, self.w, self.oc, ids=("m9", "m10"))
        tick(self.w, self.oc, blocks, seconds=seconds)
        self.check(before)

    def check(self, before):
        """After a run of the check the open keys are the independent evaluation; a check that was not due this
        step (its interval has not passed and nothing it subscribes to arrived) changed nothing."""
        exp9, exp10, *_ = expected_m9_m10(self.w, self.oc, self.start, self.conf)
        for cid, exp, prev in (("m9", exp9, before[0]), ("m10", exp10, before[1])):
            got = open_keys(self.w, cid)
            # a run happened in this step iff its record changed (a step of 0 blocks and 0 s keeps the clock)
            if self.w.db.meta(f"run:{cid}") != self.runs_before[cid]:
                assert got == exp, self.trace(f"{cid} open {got} != expected {exp}")
            else:
                assert got == prev, self.trace(f"{cid} changed without a run")
        self.check_dissent_proof()

    def check_dissent_proof(self):
        """No resolve without positive proof (QA3-F2): a dissent resolution names a round of the canonical chain in
        which the slot voted one root only and another slot voted that same root (or it carries the reorg text)."""
        rows = sent(self.w, "m9", "kind='resolved'")
        logs, _ = stored_logs(self.w, self.oc)
        wrp = TOPIC0["WeightedRootProposed"]
        for r in rows[self.resolved_seen:]:
            if not r["ikey"].startswith("dissent:") or REORG_TEXT in r["text"]:
                continue
            slot, ref = int(r["ikey"].split(":")[1]), int(re.search(r"in round (\d+)", r["text"]).group(1))
            by: dict[int, set[str]] = {}
            for lg in logs:
                if lg["topics"][0] == wrp and int(lg["topics"][2], 16) == ref:
                    by.setdefault(int(lg["data"][2 + 128: 2 + 192], 16), set()).add(lg["topics"][1])
            own = by.get(slot, set())
            assert len(own) == 1 and any(o != slot and own <= roots for o, roots in by.items()), self.trace(
                f"dissent:{slot} resolved by round {ref} without another slot voting its root: {by}")
        self.resolved_seen = len(rows)

    def trace(self, msg):
        return f"seed {self.seed} conf {self.conf}: {msg}\nactions: {self.log[-25:]}"

    def maybe_step(self):
        r = self.rng.random()
        if r < 0.45:
            self.step(self.rng.choice([0, 1, 1, self.conf + 1]))
        elif r < 0.55:
            self.maybe_reorg()

    def maybe_reorg(self):
        oc = self.oc
        depth = self.rng.choice([1, 2, self.conf + 1, self.conf + 5, 40])
        fork = oc.c.number - depth
        if fork <= max(self.floor, self.min_fork):
            return
        prior = [s for s in self.snaps if s[0] <= fork]
        self.log.append(("reorg", fork, depth))
        oc.c.reorg(fork)
        oc.acc, oc.voted, oc.latest, oc.slots = copy.deepcopy(prior[-1][1])
        self.snaps = prior
        oc.stub()
        if self.rng.random() < 0.3:  # the process dies between detection and the checks' reorg run
            self.w.check_reorg()
            self.w = restart(self.tmp, self.w, oc, ids=("m9", "m10"))
            self.log.append(("crash-after-detection",))
        self.step(self.conf + 1)

    # -- oracle actions

    def act(self):
        oc, rng = self.oc, self.rng
        kind = rng.choice(["ok", "ok", "dissent", "dissent", "skip", "skip", "fail", "late", "tie", "split", "build",
                           "rotate_open", "rotate_between", "dup_transient", "dup_persist", "stall", "downtime",
                           "stray_early", "stray_late", "lone"])
        self.log.append(("act", kind))
        self.min_fork = oc.c.number  # no reorg below the round being voted (its blockNum would be in the future)
        ref = oc.c.number - 1
        slots = [1, 2, 3, 4]
        rng.shuffle(slots)
        if ref <= oc.latest:
            oc.c.mine()
            ref = oc.c.number - 1
        bad = {3: 1} if self.bad_build else {}  # slot 3 on another tree build: dissents whenever it votes

        def vote(slot, variant=0):
            oc.vote(ref, slot, variant=variant or bad.get(slot, 0))

        if kind == "build":
            self.bad_build = not self.bad_build
        elif kind == "ok":
            oc.round(ref=ref, voters=tuple(slots), variants=bad)
        elif kind == "dissent":
            oc.round(ref=ref, voters=tuple(slots), variants={rng.choice([3, 3, 1]): 1})
        elif kind == "skip":
            oc.round(ref=ref, voters=tuple(s for s in slots if s != 3), variants=bad)
        elif kind in ("fail", "late", "tie"):
            vote(slots[0])
            self.maybe_step()
            vote(slots[1], variant=1 if kind == "tie" else 0)
            self.maybe_step()
            self.step(rng.choice([150, 160, 0]), seconds=rng.choice([0, 31 * 60]))
            if kind == "late" and not oc.committed(ref) and ref > oc.latest:
                vote(slots[2])
        elif kind == "split":
            vote(slots[0], variant=1)
            vote(slots[1])
            self.maybe_step()
            vote(slots[2])
            if not oc.committed(ref):
                vote(slots[3])
        elif kind == "rotate_open":
            vote(slots[0])
            vote(slots[1])
            self.maybe_step()
            oc.replace(slots[3], self.new_key())
            self.maybe_step()
            vote(slots[2])
        elif kind == "rotate_between":
            oc.replace(rng.choice(slots), self.new_key())
        elif kind == "dup_transient":
            a, b = slots[:2]
            oc.replace(a, oc.slots[b], mine=False)
            oc.replace(a, self.new_key(), mine=False)
            oc.c.mine()
        elif kind == "dup_persist":
            a, b = slots[:2]
            oc.replace(a, oc.slots[b])
            self.maybe_step()
            oc.replace(a, self.new_key())
        elif kind in ("stray_early", "stray_late"):  # QA2-F1: one slot proposes a blockNum the quorum does not use
            stray = ref - 1 if kind == "stray_early" else ref + 1
            if kind == "stray_late":
                oc.c.mine(2)
            if stray > oc.latest:
                oc.vote(stray, slots[3])
            self.maybe_step()
            oc.round(ref=ref, voters=tuple(slots[:3]), variants=bad)
        elif kind == "lone":  # QA3-F2: one slot votes, the others are down; the round times out
            vote(rng.choice([3, 3, slots[0]]))
            self.step(rng.choice([150, 160]), seconds=rng.choice([0, 31 * 60]))
        elif kind == "stall":  # no block, the clock moves (a chain that stopped)
            self.step(0, seconds=rng.choice([20 * 60, 40 * 60, 5 * 3600]))
        elif kind == "downtime":  # blocks and time pass while the worker is not stepping
            oc.c.elapse(rng.choice([50, 400, 2400]), seconds_per_block=rng.choice([12, 12, 14]))
        self.min_fork = 0
        self.maybe_step()

    def run(self, n):
        for _ in range(n):
            self.act()
            if self.rng.random() < 0.5:
                self.step(self.rng.choice([1, self.conf + 1, 300, 2400]))
        self.step(self.conf + 1)
        self.final()

    def final(self):
        """No event lost between sweeps: every failed round, rotation during an open round and duplicate assignment
        on the final chain was sent; every resolution was either the reorg text or of a key no longer expected."""
        tick(self.w, self.oc, self.conf + 1, seconds=60)  # one healthy run of both checks at the final state first
        _, _, rounds, rotations, commits = expected_m9_m10(self.w, self.oc, self.start, self.conf)
        text9 = "\n".join(r["text"] for r in sent(self.w, "m9", "kind != 'resolved'") if r["ikey"] == "failed-round")
        text10 = "\n".join(r["text"] for r in sent(self.w, "m10", "kind != 'resolved'")
                           if r["ikey"].startswith("dup:"))
        oc = self.oc
        cp = self.w.checkpoint()["block"]
        now = horizon(self.w, oc, cp, self.conf)
        for ref, votes in rounds.items():
            own = next((c for c in commits if c["ref"] == ref), None)
            deadline = oc.c.ts(votes[0]["pos"][0]) + 1800
            if not (own and oc.c.ts(own["pos"][0]) <= deadline) and now > deadline:  # literal rule (QA2-F1)
                assert str(ref) in text9, self.trace(f"failed round {ref} never sent")
        held = dict(SLOTS)
        for r in rotations:
            if r["new"] != ZERO and any(s != r["slot"] and a == r["new"] for s, a in held.items()):
                assert f"at block {r['pos'][0]}" in text10, self.trace(f"duplicate at {r['pos']} never sent")
            held[r["slot"]] = r["new"]


@pytest.mark.parametrize("confirmations", [0, 3])
@pytest.mark.parametrize("seed", range(12))
def test_sweep_m9_m10(tmp_path, seed, confirmations):
    Sweep(tmp_path, seed, confirmations).run(20)


# ---------------------------------------------------------------- the M11 sweep


class UpdaterSweep:
    """Rounds with updater / other-sender / no updates, reverts, same-block rotations of slot 4, leaf mismatches,
    complete / partial / failing proof answers, stalls, restarts and reorgs. Independent rules: the latest closed
    round (chain time) decides `liveness` (open iff zero updates by the key of each tx's position; page iff two
    trailing zero rounds or a mismatching leaf the API returned), the weekly key resolves only on positive proof."""

    def __init__(self, tmp_path, seed):
        self.rng = random.Random(1000 + seed)
        self.seed = seed
        self.u = UpdaterWorld(tmp_path, n=4)
        tick(self.u.w)
        self.rounds = []  # [(commit block, [(block, tx_index, sender, ok)])]
        self.log = []
        self.updater = SLOTS[4]
        self.fresh = 0
        self.liveness_refs: set[int] = set()  # zero rounds named by every liveness message sent so far
        self.chain_rewritten = False  # a deep reorg since the last liveness message (it may legitimately re-open)

    def trace(self, msg):
        return f"seed {self.seed}: {msg}\nactions: {self.log[-25:]}"

    def step(self, blocks=0, seconds=0.0):
        u = self.u
        mode = self.rng.random()
        self.log.append(("step", blocks, seconds, round(mode, 2)))
        before = open_keys(u.w, "m11")
        self.run_before = u.w.db.meta("run:m11")
        resolved_before = len(sent(u.w, "m11", "kind='resolved'"))
        self.sent_before = len(sent(u.w, "m11"))
        row = u.w.db.conn.execute("SELECT evidence FROM incidents WHERE check_id='m11' AND key='state-mismatch' "
                                  "AND state='open'").fetchone()
        self.reported = [m["cluster"] for m in json.loads(row[0])["mismatches"]] if row else []
        api_mode = self.rng.random()
        removed = {}
        failures = u.api.failures
        if api_mode < 0.15:
            u.api.down = True  # (counted as partial below: nothing may resolve on it)
        elif api_mode < 0.3:  # partial: one leaf missing
            cid = self.rng.choice(u.cids)
            if cid in u.api.leaves:
                removed[cid] = u.api.leaves.pop(cid)
        elif api_mode < 0.4:  # QA2-F2: the API answers a few requests, then every request times out
            u.api.fail_after = u.api.calls + self.rng.choice([0, 1, 2])
        try:
            if mode < 0.06:
                u.c.mine(blocks)
                u.w.clock.advance(seconds + 12 * blocks)
                u.c.down = True
                u.w.step()
                u.c.down = False
                assert open_keys(u.w, "m11") == before, self.trace("RPC outage changed state")
                return
            if mode < 0.12:
                u.w = u.restart()
                self.log.append(("restart",))
            tick(u.w, u.oc, blocks, seconds=seconds)
        finally:
            u.api.down = False
            u.api.fail_after = None
            u.api.leaves.update(removed)
        # bounded cost (QA2-F2): one run stops asking after proof_api_abort_after consecutive transport failures
        assert u.api.failures - failures <= 3, self.trace(f"{u.api.failures - failures} failed proof GETs in a step")
        self.check(before, resolved_before, partial=api_mode < 0.4, down=api_mode < 0.15, removed=set(removed))

    def closed_rounds(self):
        u = self.u
        cp = u.w.checkpoint()["block"]
        now = max(u.c.ts(cp), u.w.clock() - 2 * 12)
        return [r for r in self.rounds if r["commit"] <= cp and now > u.c.ts(r["commit"]) + 3600], cp

    def check(self, before, resolved_before, partial, down, removed):
        u = self.u
        got = open_keys(u.w, "m11")
        # a run happened in this step iff its record changed (a step of 0 blocks and 0 s keeps the clock)
        if u.w.db.meta("run:m11") == self.run_before:
            assert got == before, self.trace("m11 changed without a run")
            return
        closed, cp = self.closed_rounds()
        zero, mismatch_seen = 0, False
        for r in closed:
            mine = [t for t in r["txs"] if t["ok"] and t["block"] <= cp and u.c.ts(t["block"]) <= u.c.ts(r["commit"])
                    + 3600 and t["sender"] == t["updater"]]
            zero = 0 if mine else zero + 1
            mismatch_seen = r["bad_seen"] if zero == 1 else mismatch_seen or r["bad_seen"]
        if zero and not partial and u.api.ref == closed[-1]["ref"]:  # the API serves that root and answers all:
            # nothing stays UNKNOWN (retried, not kept)
            res = (u.w.db.meta("check:m11:zero_proof") or {}).get("result")
            assert res is None or not res["unknown"], self.trace(f"unknown kept with the API complete: {res}")
        if zero:
            assert "liveness" in got, self.trace(f"zero round not open: {got}")
            if zero >= 2:
                assert got["liveness"] == "page", self.trace("two zero rounds without a page")
            else:  # a one-round page needs a leaf the API returned that differs from the chain
                assert got["liveness"] == "warn" or mismatch_seen, self.trace("page without a mismatching leaf")
        else:
            assert "liveness" not in got, self.trace(f"liveness open without a zero round: {got}")
        # no duplicated alert (QA3-F1): a new liveness incident names a zero round no earlier liveness message named,
        # unless a deep reorg rewrote the chain since (its healing round may have left the canonical chain)
        for r in sent(u.w, "m11")[self.sent_before:]:
            if r["ikey"] != "liveness" or r["kind"] == "resolved":
                continue
            refs = {int(x) for x in re.search(r"for blockNum ([\d, ]+) \(last", r["text"]).group(1).split(", ")}
            if r["kind"] == "open" and not self.chain_rewritten:
                assert refs - self.liveness_refs, self.trace(f"resolved outage {sorted(refs)} reported again")
            self.liveness_refs |= refs
            self.chain_rewritten = False
        if "revert-rate" in got:
            assert any(r["reverts"] for r in closed), self.trace("revert-rate open without a revert")
        elif closed and closed[-1]["reverts"] and u.w.db.conn.execute(
                "SELECT COUNT(*) FROM inbox WHERE canonical IS NULL").fetchone()[0] == 0:
            assert False, self.trace("the latest closed round has a counted revert but no revert-rate incident")
        for r in sent(u.w, "m11", "kind='resolved'")[resolved_before:]:
            if r["ikey"] == "revert-rate" and REORG_TEXT not in r["text"]:
                # positive proof: a closed round after the last one with a revert in which the updater sent a
                # transaction and none reverted (a round with nothing sent proves nothing)
                bad = max((i for i, rd in enumerate(closed) if rd["reverts"]), default=-1)
                assert any(not rd["reverts"] and any(t["ok"] and t["sender"] == t["updater"] for t in rd["txs"])
                           for rd in closed[bad + 1:]), self.trace("revert-rate resolved without a clean round")
            if r["ikey"] == "state-mismatch" and REORG_TEXT not in r["text"]:
                # positive proof: the API answered every reported cluster at this step and it matches the chain
                assert self.reported and not down and all(c not in removed and u.api.leaves.get(c) == self.eb
                                                          for c in self.reported), self.trace(
                    "state-mismatch resolved without a matching answer for every reported cluster")
        if "state-mismatch" in got:
            run = longest = 0
            for r in self.rounds:
                run = run + 1 if r["bad_seen"] else 0
                longest = max(longest, run)
            assert longest >= 2, self.trace("state-mismatch open without a mismatch at two consecutive roots")
        if partial and (u.w.db.meta("check:m11:zero_proof") or {}).get("result", {}).get("unknown"):
            assert u.w.db.conn.execute("SELECT 1 FROM outbox WHERE key LIKE 'health:proof-api%'").fetchone(), \
                self.trace("partial proof answer without a health message")

    def act(self):
        u, rng = self.u, self.rng
        kind = rng.choice(["ours", "ours", "zero", "other", "revert", "rotate_same_block", "mismatch", "fix",
                           "stall", "reorg", "deep_reorg", "downtime", "outage"])
        self.log.append(("act", kind))
        if kind == "reorg":
            return self.reorg()
        if kind == "deep_reorg":
            return self.deep_reorg()
        if kind == "downtime":  # QA2-F4: more rounds than history_rounds pass while the worker is not stepping
            for _ in range(rng.choice([3, 7, 8])):
                u.c.elapse(2000)
                self.new_round(rng.choice(["ours", "ours", "zero", "other"]))
                u.c.elapse(400)
            return self.step(1)
        if kind == "outage":  # QA3-F1: a multi-round outage, then normal rounds slide it out of history_rounds
            for _ in range(rng.choice([2, 4, 7])):
                self.new_round("zero")
                self.step(305)
            for _ in range(rng.choice([1, 7])):
                self.new_round("ours")
                self.step(310)
            return
        if kind == "stall":
            return self.step(0, seconds=rng.choice([30 * 60, 61 * 60, 3 * 3600]))
        if kind == "mismatch":
            self.bad = rng.choice(u.cids[1:])
            u.api.leaves[self.bad] = 99
            if self.rounds:
                self.rounds[-1]["bad_seen"] = True
            return
        if kind == "fix":
            u.api.leaves = {c: self.eb for c in u.cids}
            self.bad = None
            return
        self.new_round(kind)
        if rng.random() < 0.5:
            self.step(rng.choice([1, 100]))

    def new_round(self, kind):
        u = self.u
        ref = u.commit()
        rnd = {"commit": u.c.sealed, "ref": ref, "txs": [], "reverts": 0, "bad_seen": bool(self.bad), "rotated": False}
        self.rounds.append(rnd)
        if kind in ("ours", "revert"):
            u.update(0, eb=32 + 1 + ref % 7, sender=self.updater)
            rnd["txs"].append({"block": u.c.sealed, "sender": self.updater, "updater": self.updater, "ok": True})
        if kind == "revert":
            u.fail("MustUseLatestRoot", ref, i=1, sender=self.updater)
            rnd["txs"].append({"block": u.c.sealed, "sender": self.updater, "updater": self.updater, "ok": False})
            rnd["reverts"] += 1
        if kind == "other":
            u.update(0, eb=40, sender=SLOTS[3])
            rnd["txs"].append({"block": u.c.sealed, "sender": SLOTS[3], "updater": self.updater, "ok": True})
        if kind == "rotate_same_block":
            self.fresh += 1
            new = "0x%040x" % (0xFEED0000 + self.fresh)
            u.oc.replace(4, new, mine=False)
            u.update(0, eb=32 + 1 + ref % 7, sender=new)
            rnd["txs"].append({"block": u.c.sealed, "sender": new, "updater": new, "ok": True})
            self.updater = new
            rnd["rotated"] = True
        u.set_eb(self.eb, leaves={self.bad: 99} if self.bad else None)

    def deep_reorg(self):
        """QA2-F3 class: a reorg (beyond any confirmation window) that removes the last round(s) with their updates
        and the evaluations they aged; the model is rewound with the chain (the rounds left the canonical chain)."""
        u = self.u
        n = self.rng.choice([1, 2])
        if len(self.rounds) < n + 1 or any(r["rotated"] for r in self.rounds[-n:]):
            return
        fork = self.rounds[-n]["commit"]
        self.log.append(("deep_reorg", fork, n))
        self.chain_rewritten = True
        u.c.reorg(fork)
        del self.rounds[-n:]
        u.oc.stub()
        u.ssv.latest_root = self.rounds[-1]["ref"]
        for st in u.ssv.clusters.values():
            st["root"] = min(st["root"], u.ssv.latest_root)
        u.api.ref = u.ssv.latest_root
        u.set_eb(self.eb, leaves={self.bad: 99} if self.bad else None)
        if self.bad:  # the API now serves the mismatching leaf at the root that is the latest again
            self.rounds[-1]["bad_seen"] = True
        self.step(1)

    def reorg(self):
        """A reorg of the empty blocks after the last round (the rounds' own blocks are kept: the model of
        MiniSSV cannot rewind them): the worker re-derives on the new chain and must not resolve anything."""
        u = self.u
        last = max([r["commit"] for r in self.rounds] + [t["block"] for r in self.rounds for t in r["txs"]] +
                   [self.floor])
        depth = self.rng.choice([1, 2, 30])
        fork = max(u.c.number - depth, last + 1)
        if fork > u.c.number:
            return
        self.log.append(("reorg", fork))
        u.c.reorg(fork)
        u.oc.stub()
        u.set_eb(self.eb, leaves={self.bad: 99} if self.bad else None)
        self.step(1)

    def run(self, n):
        self.eb, self.bad = 32, None
        self.floor = self.u.c.number
        for _ in range(n):
            self.act()
            self.step(self.rng.choice([0, 1, 305, 310]), seconds=self.rng.choice([0, 0, 120]))
            self.step(self.rng.choice([1, 26]), seconds=self.rng.choice([0, 300]))
        self.step(310)
        self.final()

    def final(self):
        """No event lost between sweeps: every closed zero-update round of the final chain was sent."""
        u = self.u
        tick(u.w, u.oc, 1, seconds=300)  # one healthy run first: the last random step may have been an RPC outage
        text = "\n".join(r["text"] for r in sent(u.w, "m11", "kind != 'resolved'") if r["ikey"] == "liveness")
        closed, cp = self.closed_rounds()
        for r in closed:
            mine = [t for t in r["txs"] if t["ok"] and u.c.ts(t["block"]) <= u.c.ts(r["commit"]) + 3600
                    and t["sender"] == t["updater"]]
            if not mine:
                assert str(r["ref"]) in text, self.trace(f"zero round {r['ref']} never sent")


@pytest.mark.parametrize("seed", range(60))
def test_sweep_m11(tmp_path, seed):
    UpdaterSweep(tmp_path, seed).run(16)
