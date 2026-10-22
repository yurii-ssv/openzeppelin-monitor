"""M9 — Oracle quorum health (spec §3 M9; evidence `ssv-network-triage/evidences/eb-oracle-cadence/scan*.mjs`).

Every run re-derives the rounds from the stored `WeightedRootProposed` / `RootCommitted` logs (m9_oracle_common)
and reports, through the episode book (`m9_episodes`):

| Trigger (spec) | Key | Severity | Rule here |
|---|---|---|---|
| proposed, no `RootCommitted` within 30 min | `failed-round` | page | no `RootCommitted` of the round's own `blockNum` with a chain timestamp within `round_timeout_s` of its first vote, once the horizon (m9_oracle_common `BlockTimes`: the checkpoint's timestamp, or the clock when blocks stop) is past that deadline (the literal spec rule: a later `blockNum`'s commit does not make the round committed); consecutive failed rounds are one incident re-sent per failed round (`revision`), healed by a late commit or a superseding commit; a round already superseded inside its 30 min (a slot proposed a `blockNum` the quorum did not use) is its own incident, sent at its deadline with its resolution |
| no `RootCommitted` for > 9 h | `cadence` | page | horizon − timestamp of the last commit block (or of `start_block`) > `max_commit_gap_s`: the clock keeps it growing while blocks or events stop; healed by the next commit. A gap that opened and closed while the worker was not evaluating is reported once from the commits' timestamps |
| two roots for one `blockNum` | `split` | warn | as soon as a round has two distinct roots and no reference root yet (not committed and inside its 30 min, or settled with tied roots): immediate, unattributed; healed when the round settles with a reference root (the dissent moves to `dissent:<slot>`) or a later round commits |
| two roots for one `blockNum`, attributed | `dissent:<slot>` | warn | per settled round (committed, superseded or timed out), a slot that voted any root other than the reference root (committed root; for an uncommitted round the unique plurality root), also when the same slot voted the reference root too (e.g. across an owner change); re-sent per new dissenting round (`revision`); healed only by positive proof: the slot votes the reference root and nothing else in a settled round |
| three dissenting rounds in a row from one slot | `dissent:<slot>` | page | the same incident escalates when `dissent_streak` consecutive settled rounds have a dissent from the slot; a round the slot did not vote in, or a tied round, breaks the run (it does not heal the incident) |
| committed with < 3 distinct slots for the committed root | `weak-commit` | page | distinct `oracleId`s voting the committed root < `min_committing_slots`, or < the votes the round's own `quorum`/weight data require; healed by the next adequate commit |
| a slot absent from the committed root in the last 6 rounds | `absent:<slot>` | warn | active slot ids from the live parameters; needs `absent_rounds` committed rounds of history (else not evaluated) |
| `commitRoot` revert from a slot address (inbox) | `revert:<address>` | warn | gated inbox rows of `commitRoot` whose sender is a current or historical slot address; re-sent per revert; healed by that address's next successful vote in a later block |

Block positions order events inside a block (`(block, log index)`); every time rule uses chain timestamps
(`m9_block_times`, see `BlockTimes`). Live parameters used: `active_oracle_ids`, `oracles`. Absence of logs is
evidence (the scan is complete and reorg-checked by the framework); a malformed log, unreadable parameters or a
missing header timestamp raise. Reorg: the book's rule (m9_oracle_common) and the block-time cache's.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

from checks.base import Check, Ctx, Every, Finding, OnEvents, OnInbox
from checks.m9_oracle_common import (
    COMMIT_ROOT_SELECTOR,
    ORACLE_EVENTS,
    ROUND_KINDS,
    BlockTimes,
    Episode,
    EpisodeBook,
    History,
    Round,
    book_schema,
    inbox_enabled,
    inbox_gate_pending,
    inbox_rows,
    inbox_unavailable,
    load_history,
    short,
    times_schema,
    utc,
)
from checks.registry import register

TABLE = "m9_episodes"
TIMES = "m9_block_times"


@register
class M9OracleQuorum(Check):
    id = "m9"
    defaults = {
        "interval_s": 60,  # the clock-driven cadence rule is evaluated at least this often
        "round_timeout_s": 1800,  # proposed, not committed within -> failed round
        "max_commit_gap_s": 32_400,  # 9 h (cadence 8 h + margin); 25 h if the cadence moves to daily
        "dissent_streak": 3,
        "min_committing_slots": 3,
        "absent_rounds": 6,
        "seconds_per_block": 12,
        "require_inbox": True,  # the commitRoot-revert rule needs the failed-transaction inbox (inbox.path)
    }
    events = ORACLE_EVENTS
    schema = book_schema(TABLE) + ";\n" + times_schema(TIMES)

    def __init__(self, config: dict, settings: dict):
        super().__init__(config, settings)
        if not settings.get("views"):
            raise SystemExit("checks.m9: settings.views (SSVNetworkViews) is required: the oracle slots are live "
                             "parameters")
        if config["require_inbox"] and not inbox_enabled(SimpleNamespace(settings=settings)):
            raise SystemExit("checks.m9: the commitRoot-revert rule needs the failed-transaction inbox (inbox.path); "
                             "set checks.m9.require_inbox false to run without it")
        self.schedule = (Every(config["interval_s"]), OnEvents(ROUND_KINDS | {"OracleReplaced"}), OnInbox())
        self.book = EpisodeBook(self.id, TABLE)
        self.times = BlockTimes(TIMES)
        self.spb = config["seconds_per_block"]
        # votes precede their round's commit by ~65 blocks and follow its reference block: a round whose
        # blockNum is below start_block may have votes before the stored history, so the rules that attribute votes
        # (split, dissent, weak commit, absence) skip it. The failed-round rule does not: a stored vote proves no
        # commit of that blockNum (or a later one) came before it, and an earlier unstored vote only moves the
        # deadline earlier, so the stored first vote can delay a failure but never invent one
        self.complete_from = settings["start_block"]

    def prepare(self, ctx: Ctx) -> None:
        self.slots = [i for i in ctx.params.active_oracle_ids if i]
        self.oracles = {i: a.lower() for i, a in ctx.params.oracles.items()}

    # ---- run

    def run(self, ctx: Ctx, block: int) -> list[Finding]:
        for _ in ctx.events():  # the history is re-derived below; the cursor only says "something changed"
            pass
        hist = load_history(ctx.db, block)
        self.times.begin(ctx, self.spb)
        rounds = [r for ref, r in sorted(hist.rounds.items()) if r.votes]
        self.times.get([r.first_vote.block for r in rounds] + [c.block for c in hist.commits]
                       + [ctx.settings["start_block"], block])  # one batch for the headers not cached yet
        self.now = self.times.horizon()
        self.block = block
        every = [self.round_state(hist, r) for r in rounds]
        states = [st for st in every if st.ref >= self.complete_from]
        episodes = (self.failed_rounds(every) + self.cadence(ctx, hist, block) + self.splits(hist, states)
                    + self.dissent(states) + self.weak_commits(hist) + self.absent(hist))
        unknown: set[str] = set()
        if not inbox_enabled(ctx):  # require_inbox false: an explicit opt-out, still said once
            ctx.health("m9-inbox", "M9 commitRoot-revert rule not evaluated: the failed-transaction inbox is not "
                       "configured (inbox.path).", dedup="m9-inbox-disabled")
        elif inbox_unavailable(ctx) or inbox_gate_pending(ctx, COMMIT_ROOT_SELECTOR, "commitRoot"):
            unknown.add("revert:")
        else:
            episodes += self.reverts(ctx, hist)
        unknown_keys = frozenset(r["key"] for r in ctx.db.conn.execute(f"SELECT key FROM {TABLE} WHERE closed=0")
                                 if any(r["key"].startswith(p) for p in unknown))
        return self.book.apply(ctx, episodes, unknown_keys)

    # ---- rounds

    def round_state(self, hist: History, rnd: Round) -> SimpleNamespace:
        """One round at this run: its deadline (chain time of the first vote + `round_timeout_s`), the commit that
        closed it (own or superseding, any time), whether it is settled (closed, or the horizon is past the deadline)
        and its reference root (the committed root; for a settled uncommitted round the unique plurality root;
        None while unsettled or tied)."""
        first_at = self.times(rnd.first_vote.block)
        deadline = first_at + self.config["round_timeout_s"]
        close = hist.closing_event(rnd)
        settled = close is not None or self.now > deadline
        if rnd.commit is not None:
            reference = rnd.commit.root
        elif settled:
            counts: dict[str, int] = {}
            for v in rnd.votes:
                counts[v.root] = counts.get(v.root, 0) + 1
            top = max(counts.values())
            leaders = [r for r, n in counts.items() if n == top]
            reference = leaders[0] if len(leaders) == 1 else None
        else:
            reference = None
        # spec M9: no RootCommitted *for this blockNum* within 30 min; a later blockNum's commit closes the round
        # (it can no longer commit) but does not make it committed
        in_time = rnd.commit is not None and self.times(rnd.commit.block) <= deadline
        failed = not in_time and self.now > deadline
        early = failed and close is not None and self.times(close.block) <= deadline  # superseded inside its 30 min
        return SimpleNamespace(rnd=rnd, ref=rnd.ref, first_at=first_at, deadline=deadline, close=close,
                               settled=settled, reference=reference, failed=failed, early=early,
                               last_vote_block=rnd.votes[-1].block)

    def block_after(self, ts: float, lo: int, hi: int) -> int:
        """The first block in (lo, hi] whose chain timestamp is past `ts` (where a deadline that passed was
        established); `hi` when none is (the horizon is the clock). Bisection over cached header timestamps."""
        if self.times(hi) <= ts:
            return hi
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if self.times(mid) > ts:
                hi = mid
            else:
                lo = mid
        return hi

    def failed_rounds(self, states) -> list[Episode]:
        """Failed rounds grouped into runs that end at the same commit (a late commit of the run's round or a
        superseding one): one incident per run, re-sent for every further failed round (`revision`). A round that
        was superseded inside its own 30 min fails only at its deadline, after that commit: it is an incident of
        its own (it cannot join a run already closed by the commit), established at the first block past its
        deadline."""
        runs: dict[tuple | None, list] = {}
        for st in states:
            if st.failed:
                key = ("early", st.ref) if st.early else (st.close.pos if st.close is not None else None)
                runs.setdefault(key, []).append(st)
        out = []
        timeout_min = self.config["round_timeout_s"] // 60
        for members in runs.values():
            first, last = members[0], members[-1]
            close = last.close
            early = ""
            if last.early:
                slots = ", ".join(f"slot {v.slot} {short(v.oracle)}" for v in last.rnd.votes)
                early = (f" blockNum {close.ref_block} was committed at block {close.block} inside this round's 30 min: "
                         f"{slots} proposed a reference block the quorum did not use.")
                resolution = (f"Round {last.ref} can no longer commit: blockNum {close.ref_block} was committed at block "
                              f"{close.block} (tx {close.tx_hash}) before its deadline.")
            elif close is None:
                resolution = ""
            elif close is last.rnd.commit:
                resolution = f"Round {last.ref} committed late at block {close.block} (tx {close.tx_hash})."
            else:
                resolution = (f"Round(s) {', '.join(str(m.ref) for m in members)} can no longer commit: round "
                              f"{close.ref_block} committed at block {close.block} (tx {close.tx_hash}); the oracle "
                              f"has recovered.")
            rnd = last.rnd
            votes = "; ".join(f"slot {v.slot} {short(v.oracle)} root {v.root[:10]} (block {v.block})" for v in rnd.votes)
            roots = len({v.root for v in rnd.votes})
            refs = [m.ref for m in members]
            out.append(Episode(
                key="failed-round", episode=f"failed-round:{first.ref}", active=close is None,
                # an absence: established at this checkpoint while it holds, healed by the closing commit; a round
                # superseded in time: established at its deadline (after the commit that closed it)
                evidence_block=(self.block_after(last.deadline, close.block, self.block) if last.early
                                else close.block if close is not None else self.block), severity="page",
                title=(f"Oracle round {rnd.ref} not committed within {timeout_min} min"
                       + (f" ({len(refs)} failed rounds in a row)" if len(refs) > 1 else "")),
                body=(f"WeightedRootProposed for blockNum {rnd.ref} (first vote at block {rnd.first_vote.block}, "
                      f"{utc(last.first_at)}) and no RootCommitted by {utc(last.deadline)} (chain time). Votes: "
                      f"{votes}. {roots} distinct root(s)."
                      + (f" Failed rounds since the last commit: {', '.join(map(str, refs))}." if len(refs) > 1 else "")
                      + early + " The oracle has no retry: the next target waits for the next round. Check every slot's "
                      "build (#93157)."),
                subject={"ref_block": rnd.ref}, revision=str(rnd.ref),
                evidence={"failed_rounds": refs, "votes": [vote_json(v) for v in rnd.votes],
                          "first_vote_at": last.first_at, "deadline_at": last.deadline, "distinct_roots": roots},
                resolution=resolution))
        return out

    def cadence(self, ctx: Ctx, hist: History, block: int) -> list[Episode]:
        max_gap = self.config["max_commit_gap_s"]
        commits = hist.commits
        out = []
        open_gaps = {r[0] for r in ctx.db.conn.execute(
            f"SELECT episode FROM {TABLE} WHERE key='cadence' AND closed=0")}
        for prev, nxt in zip(commits, commits[1:]):  # gaps that closed: resolve the open one, report a missed one
            gap = self.times(nxt.block) - self.times(prev.block)
            if gap > max_gap or f"cadence:{prev.block}" in open_gaps:
                out.append(self._gap(prev.block, prev.ref_block, False, nxt.block, f"{gap / 3600:.1f} h (chain time)",
                                     f"RootCommitted for blockNum {nxt.ref_block} at block {nxt.block} ended the gap.",
                                     {"next_commit_block": nxt.block, "next_ref_block": nxt.ref_block,
                                      "gap_s": gap}))
        last_block = commits[-1].block if commits else ctx.settings["start_block"]
        last_ref = commits[-1].ref_block if commits else None
        since_at = self.times(last_block)
        age = self.now - since_at
        if age > max_gap:
            since = (f"the last RootCommitted (blockNum {last_ref}, block {last_block})" if commits else
                     f"start_block {last_block} (no RootCommitted stored since)")
            out.append(self._gap(last_block, last_ref, True, block, f"{age / 3600:.1f} h (to {utc(self.now)})", "",
                                 {"age_s": round(age), "since": since, "since_at": since_at}))
        return out

    def _gap(self, since_block, since_ref, active, evidence_block, length, resolution, extra) -> Episode:
        limit = self.config["max_commit_gap_s"] / 3600
        return Episode(
            key="cadence", episode=f"cadence:{since_block}", active=active, evidence_block=evidence_block,
            severity="page", title=f"No RootCommitted for more than {limit:g} h",
            body=(f"No RootCommitted after block {since_block} (blockNum {since_ref}) for {length}; the cadence is "
                  f"3 roots/day. The oracle has no retry (72 h outage 2026-08-07 -> 08-10)."),
            subject={"since_block": since_block}, evidence={"since_ref_block": since_ref, **extra},
            resolution=resolution or "RootCommitted observed again.")

    def splits(self, hist: History, states) -> list[Episode]:
        """Two distinct roots for one blockNum with no reference root to attribute them: immediately (an open
        round inside its 30 min) and for a settled tied round. Settling with a reference root heals it without a
        report of its own (`occurred=False`): the dissent is then `dissent:<slot>`, so nothing is lost."""
        out = []
        for st in states:
            rnd = st.rnd
            seen: set[str] = set()
            second = None
            for v in rnd.votes:
                seen.add(v.root)
                if len(seen) == 2 and second is None:
                    second = v
            if second is None:
                continue
            sup = hist.superseded_by(rnd.ref) if rnd.commit is None else None
            active = st.reference is None and sup is None
            if active:
                resolution = ""
            elif st.reference is not None:
                resolution = (f"Round {rnd.ref} settled on root {st.reference[:10]}: the dissent is attributed per "
                              f"slot (dissent:<slot>).")
            else:
                resolution = f"A later round committed (blockNum {sup.ref_block})."
            state = "still open" if not st.settled else "settled with tied roots"
            out.append(Episode(
                key="split", episode=f"split:{rnd.ref}", active=active, occurred=st.reference is None,
                evidence_block=second.block, severity="warn",
                title=f"Oracle round {rnd.ref}: {len(seen)} different merkleRoots proposed",
                body=(f"Two or more merkleRoot values proposed for blockNum {rnd.ref} (round {state}, no reference root "
                      f"to attribute the dissent): %s." % "; ".join(f"slot {v.slot} {short(v.oracle)} {v.root[:10]}"
                                                                     for v in rnd.votes)),
                subject={"ref_block": rnd.ref}, revision=str(len(seen)),
                evidence={"votes": [vote_json(v) for v in rnd.votes], "settled": st.settled},
                resolution=resolution))
        return out

    def dissent(self, states) -> list[Episode]:
        """Per slot, an episode from its first dissenting settled round until positive proof of agreement (it votes
        the reference root and nothing else in a settled round). Inside it, the run of consecutive dissenting rounds
        sets the severity: any settled round in which the slot did not dissent (no vote, or tied roots) breaks it."""
        streak_n = self.config["dissent_streak"]
        out = []
        eps: dict[int, dict] = {}  # slot -> {"rounds": [(state, roots)], "run": n, "max_run": n}

        def episode(slot, e, active, healed_by=None):
            rounds = e["rounds"]
            first, last = rounds[0][0], rounds[-1][0]
            n, longest = len(rounds), e["max_run"]
            sev = "page" if longest >= streak_n else "warn"
            listing = "; ".join(f"round {st.ref}: voted {', '.join(r[:10] for r in roots)}" for st, roots in rounds[-6:])
            body = (f"Slot {slot} voted a different merkleRoot than the reference root in {n} round(s) since round "
                    f"{first.ref}, {e['run']} in a row now, longest run {longest} ({listing}). A slot on another tree "
                    f"build silently blocks quorum (#93157).")
            if sev == "page":
                body += f" {longest} >= {streak_n} in a row: the quorum margin is gone."
            return Episode(
                key=f"dissent:{slot}", episode=f"dissent:{slot}:{first.ref}", active=active,
                evidence_block=first.last_vote_block, severity=sev,
                title=f"Oracle slot {slot} dissent ({n} round{'s' if n > 1 else ''}, {e['run']} in a row)", body=body,
                subject={"slot": slot}, revision=f"{first.ref}:{last.ref}",
                evidence={"rounds": [{"ref_block": st.ref, "voted_roots": roots, "reference": st.reference}
                                     for st, roots in rounds[-24:]], "run": e["run"], "longest_run": longest},
                resolution=(f"Slot {slot} voted the reference root (and no other) in round {healed_by}."
                            if healed_by else ""))

        for st in states:
            if not st.settled:
                continue  # votes may still arrive: judged once settled (it is `split` meanwhile)
            by_slot: dict[int, set[str]] = {}
            for v in st.rnd.votes:
                by_slot.setdefault(v.slot, set()).add(v.root)
            if st.reference is None:  # tied: no slot's dissent is attributable, every run is broken
                for e in eps.values():
                    e["run"] = 0
                continue
            for slot in sorted(set(by_slot) | set(eps)):
                roots = by_slot.get(slot)
                if roots and roots == {st.reference}:
                    if slot in eps:
                        out.append(episode(slot, eps.pop(slot), False, healed_by=st.ref))
                elif roots:
                    e = eps.setdefault(slot, {"rounds": [], "run": 0, "max_run": 0})
                    e["rounds"].append((st, sorted(roots - {st.reference})))
                    e["run"] += 1
                    e["max_run"] = max(e["max_run"], e["run"])
                else:
                    eps[slot]["run"] = 0  # no vote: not a dissenting round
        for slot, e in sorted(eps.items()):
            out.append(episode(slot, e, True))
        return out

    def committed(self, hist: History) -> list[Round]:
        return [hist.rounds[c.ref_block] for c in hist.commits
                if hist.rounds[c.ref_block].commit is c and c.ref_block >= self.complete_from]

    def weak_commits(self, hist: History) -> list[Episode]:
        min_slots = self.config["min_committing_slots"]
        committed = self.committed(hist)
        out = []
        for i, rnd in enumerate(committed):
            votes = [v for v in rnd.votes if v.root == rnd.commit.root and v.pos < rnd.commit.pos]
            slots = sorted({v.slot for v in votes})
            weight = votes[0].weight if votes else 0
            needed = math.ceil(votes[-1].quorum / weight) if votes and weight else None
            if len(slots) >= min_slots and (needed is None or len(slots) >= needed) and votes:
                continue
            nxt = committed[i + 1] if i + 1 < len(committed) else None
            out.append(Episode(
                key="weak-commit", episode=f"weak-commit:{rnd.ref}", active=nxt is None,
                evidence_block=rnd.commit.block, severity="page",
                title=f"Root for blockNum {rnd.ref} committed with {len(slots)} distinct slot(s)",
                body=(f"RootCommitted {rnd.commit.root[:10]} for blockNum {rnd.ref} at block {rnd.commit.block} with "
                      f"votes from slots {slots} for the committed root (minimum {min_slots}"
                      + (f"; the round's own quorum/weight data needs {needed}" if needed else "")
                      + "). Cannot happen unless quorumBps changed or a slot's vote counted twice (#92676)."),
                subject={"ref_block": rnd.ref},
                evidence={"commit_tx": rnd.commit.tx_hash, "commit_block": rnd.commit.block, "slots": slots,
                          "votes": [vote_json(v) for v in votes], "votes_needed_by_round_data": needed},
                resolution=(f"Round {nxt.ref} committed with enough distinct slots." if nxt else "")))
        return out

    def absent(self, hist: History) -> list[Episode]:
        k = self.config["absent_rounds"]
        committed = self.committed(hist)
        if len(committed) < k:
            return []  # not evaluable yet (fresh history): never read as "every slot voted"
        out = []
        for slot in self.slots:
            missed: list[Round] = []
            start = None
            for rnd in committed:
                voted = any(v.slot == slot and v.root == rnd.commit.root for v in rnd.votes)
                if voted:
                    if start is not None:
                        out.append(self._absent(slot, start, missed, False, rnd))
                    missed, start = [], None
                    continue
                missed.append(rnd)
                if len(missed) == k:
                    start = rnd
            if start is not None:
                out.append(self._absent(slot, start, missed, True, None))
        return out

    def _absent(self, slot, start: Round, missed: list[Round], active: bool, healed: Round | None) -> Episode:
        k = self.config["absent_rounds"]
        return Episode(
            key=f"absent:{slot}", episode=f"absent:{slot}:{start.ref}", active=active,
            evidence_block=start.commit.block, severity="warn",
            title=f"Oracle slot {slot} has not voted the committed root in {len(missed)} rounds",
            body=(f"Slot {slot} ({short(self.oracles.get(slot, '?'))}) did not vote for the committed root in the last "
                  f"{len(missed)} committed rounds (blockNum {missed[0].ref} .. {missed[-1].ref}; threshold {k}). One "
                  f"skipped fourth vote per round is routine; {k} in a row is not."),
            subject={"slot": slot}, revision=str(len(missed) // k),
            evidence={"missed_rounds": [r.ref for r in missed][-24:], "address": self.oracles.get(slot)},
            resolution=f"Slot {slot} voted the committed root in round {healed.ref}." if healed else "")

    def reverts(self, ctx: Ctx, hist: History) -> list[Episode]:
        addresses = set(self.oracles.values()) | hist.oracle_addresses()
        slot_of = {a: s for s, a in self.oracles.items()}
        votes_by: dict[str, list[int]] = {}
        for rnd in hist.rounds.values():
            for v in rnd.votes:
                votes_by.setdefault(v.oracle, []).append(v.block)
        out = []
        groups: dict[str, list] = {}
        for r in inbox_rows(ctx, COMMIT_ROOT_SELECTOR, "commitRoot"):
            sender = (r["sender"] or "").lower()
            if sender in addresses:
                groups.setdefault(sender, []).append(r)
        for sender, rows in sorted(groups.items()):
            votes = sorted(votes_by.get(sender, []))
            current: list = []
            for r in rows:
                # a successful vote in a later block than the episode's last revert (and not after this revert,
                # whose block may hold both: inbox rows carry no transaction order) heals the episode
                if current and any(current[-1]["block"] < b < r["block"] for b in votes):
                    healed = min(b for b in votes if b > current[-1]["block"])
                    out.append(self._revert(sender, slot_of.get(sender), current, healed))
                    current = []
                current.append(r)
            if current:
                later = [b for b in votes if b > current[-1]["block"]]
                out.append(self._revert(sender, slot_of.get(sender), current, later[0] if later else None))
        return out

    def _revert(self, sender, slot, rows, healed_at) -> Episode:
        listing = "; ".join(f"{r['tx_hash'][:12]} block {r['block']} {r['revert_name'] or r['revert_selector'] or 'unknown'}"
                            for r in rows[-5:])
        who = f"slot {slot} " if slot else "former slot address "
        return Episode(
            key=f"revert:{sender}", episode=f"revert:{sender}:{rows[0]['tx_hash']}", active=healed_at is None,
            evidence_block=rows[0]["block"], severity="warn",
            title=f"commitRoot reverted for {who}{short(sender)} ({len(rows)}x)",
            body=(f"commitRoot from {who}{sender} reverted {len(rows)} time(s) since block {rows[0]['block']}: {listing}. "
                  f"NotOracle/AlreadyVoted/StaleBlockNumber/ZeroCSSVSupply mean the slot's vote is lost this round."),
            subject={"sender": sender, "slot": slot}, revision=rows[-1]["tx_hash"],
            evidence={"reverts": [{"tx_hash": r["tx_hash"], "block": r["block"], "revert": r["revert_name"],
                                   "selector": r["revert_selector"], "source": r["revert_source"]} for r in rows[-20:]]},
            resolution=f"{who}{sender} voted successfully again at block {healed_at}." if healed_at else "")

    def digest(self, ctx: Ctx) -> list[str]:
        hist = load_history(ctx.db, ctx.block)
        if not hist.commits:
            return ["no RootCommitted stored"]
        last = hist.commits[-1]
        return [f"last root blockNum {last.ref_block} committed at block {last.block}; {len(hist.commits)} commits stored"]


def vote_json(v) -> dict:
    return {"slot": v.slot, "oracle": v.oracle, "root": v.root, "block": v.block, "tx_hash": v.tx_hash}
