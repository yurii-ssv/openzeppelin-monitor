"""M9 — Oracle quorum health (spec §3 M9; evidence `ssv-network-triage/evidences/eb-oracle-cadence/scan*.mjs`).

Every run re-derives the rounds from the stored `WeightedRootProposed` / `RootCommitted` logs (m9_oracle_common)
and reports, through the episode book (`m9_episodes`):

| Trigger (spec) | Key | Severity | Rule here |
|---|---|---|---|
| proposed, no `RootCommitted` within 30 min | `round:<blockNum>` | page | no commit of that `blockNum` by first vote + `round_timeout_s` (in blocks); healed by a late commit or a superseding commit |
| no `RootCommitted` for > 9 h | `cadence` | page | clock-driven (`Every`): worker clock − estimated time of the last commit block (persisted at first sight) > `max_commit_gap_s`; healed by the next commit. A gap that opened and closed while the worker was not evaluating is reported once from block distances |
| two roots for one `blockNum` | `dissent:<slot>` | warn | per settled round, a slot whose vote is not the reference root (committed root; for an uncommitted round the unique plurality root); re-sent per new dissenting round (`revision`); healed when the slot votes the reference root again |
| three dissenting rounds in a row from one slot | `dissent:<slot>` | page | the same incident escalates at `dissent_streak` consecutive dissents (rounds the slot did not vote in neither count nor reset) |
| (two roots, no unique reference) | `split` | warn | an uncommitted settled round with tied roots: dissent not attributable to a slot; healed by the next commit |
| committed with < 3 distinct slots for the committed root | `weak-commit` | page | distinct `oracleId`s voting the committed root < `min_committing_slots`, or < the votes the round's own `quorum`/weight data require; healed by the next adequate commit |
| a slot absent from the committed root in the last 6 rounds | `absent:<slot>` | warn | active slot ids from the live parameters; needs `absent_rounds` committed rounds of history (else not evaluated) |
| `commitRoot` revert from a slot address (inbox) | `revert:<address>` | warn | gated inbox rows of `commitRoot` whose sender is a current or historical slot address; re-sent per revert; healed by that address's next successful vote in a later block |

A round is *settled* when committed, superseded by a higher committed `blockNum`, or past its timeout. Blocks
are converted with `seconds_per_block`. Live parameters used: `active_oracle_ids`, `oracles`. Absence of logs
is evidence (the scan is complete and reorg-checked by the framework); a malformed log or unreadable parameters
raise. Reorg: the book's rule (m9_oracle_common); the persisted last-commit time estimate is recomputed when its
block is no longer the latest stored commit.
"""

from __future__ import annotations

import math

from checks.base import Check, Ctx, Every, Finding, OnEvents, OnInbox
from checks.m9_oracle_common import (
    COMMIT_ROOT_SELECTOR,
    ORACLE_EVENTS,
    ROUND_KINDS,
    Episode,
    EpisodeBook,
    History,
    Round,
    blocks_for,
    book_schema,
    estimate_time,
    inbox_enabled,
    inbox_gate_pending,
    inbox_rows,
    inbox_unavailable,
    load_history,
    short,
)
from checks.registry import register

TABLE = "m9_episodes"


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
    }
    events = ORACLE_EVENTS
    schema = book_schema(TABLE)

    def __init__(self, config: dict, settings: dict):
        super().__init__(config, settings)
        if not settings.get("views"):
            raise SystemExit("checks.m9: settings.views (SSVNetworkViews) is required: the oracle slots are live "
                             "parameters")
        self.schedule = (Every(config["interval_s"]), OnEvents(ROUND_KINDS | {"OracleReplaced"}), OnInbox())
        self.book = EpisodeBook(self.id, TABLE)
        self.spb = config["seconds_per_block"]
        # votes precede their round's commit by ~65 blocks and follow its reference block: a round whose
        # blockNum is below start_block may have votes before the stored history, so vote-based rules skip it
        self.complete_from = settings["start_block"]

    def prepare(self, ctx: Ctx) -> None:
        self.slots = [i for i in ctx.params.active_oracle_ids if i]
        self.oracles = {i: a.lower() for i, a in ctx.params.oracles.items()}

    # ---- run

    def run(self, ctx: Ctx, block: int) -> list[Finding]:
        for _ in ctx.events():  # the history is re-derived below; the cursor only says "something changed"
            pass
        hist = load_history(ctx.db, block)
        timeout = blocks_for(self.config["round_timeout_s"], self.spb)
        settled = self.settled_rounds(hist, block, timeout)
        episodes = (self.failed_rounds(hist, block, timeout) + self.cadence(ctx, hist, block)
                    + self.dissent(hist, settled) + self.weak_commits(hist) + self.absent(hist))
        unknown: set[str] = set()
        if not inbox_enabled(ctx):
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

    def settled_rounds(self, hist: History, block: int, timeout: int) -> list[tuple[Round, int, str | None]]:
        """(round, settle block, reference root) for every settled round with votes, by blockNum."""
        out = []
        for ref in sorted(hist.rounds):
            rnd = hist.rounds[ref]
            if not rnd.votes or ref < self.complete_from:
                continue
            close = hist.closing_event(rnd)
            deadline = rnd.first_vote.block + timeout
            if close is None and block < deadline:
                continue  # still open: votes may arrive
            at = min(close.block, deadline) if close is not None else deadline
            if rnd.commit is not None:
                reference = rnd.commit.root
            else:
                counts: dict[str, int] = {}
                for v in rnd.votes:
                    counts[v.root] = counts.get(v.root, 0) + 1
                top = max(counts.values())
                leaders = [r for r, n in counts.items() if n == top]
                reference = leaders[0] if len(leaders) == 1 else None
            out.append((rnd, at, reference))
        return out

    def failed_rounds(self, hist: History, block: int, timeout: int) -> list[Episode]:
        out = []
        for ref, rnd in sorted(hist.rounds.items()):
            if not rnd.votes or ref < self.complete_from:
                continue
            deadline = rnd.first_vote.block + timeout
            if block < deadline or (rnd.commit is not None and rnd.commit.block <= deadline):
                continue
            close = hist.closing_event(rnd)
            if close is None:
                resolution = ""
            elif close is rnd.commit:
                resolution = f"Round {ref} committed late at block {close.block} (tx {close.tx_hash})."
            else:
                resolution = (f"Round {ref} can no longer commit: round {close.ref_block} committed at block "
                              f"{close.block} (tx {close.tx_hash}); the oracle has recovered.")
            votes = "; ".join(f"slot {v.slot} {short(v.oracle)} root {v.root[:10]} (block {v.block})" for v in rnd.votes)
            roots = len({v.root for v in rnd.votes})
            out.append(Episode(
                key=f"round:{ref}", episode=f"round:{ref}", active=close is None, evidence_block=deadline,
                severity="page", title=f"Oracle round {ref} not committed within "
                f"{self.config['round_timeout_s'] // 60} min",
                body=(f"WeightedRootProposed for blockNum {ref} (first vote at block {rnd.first_vote.block}) and no "
                      f"RootCommitted by block {deadline}. Votes: {votes}. {roots} distinct root(s). The oracle has "
                      f"no retry: the next target waits for the next round. Check every slot's build (#93157)."),
                subject={"ref_block": ref},
                evidence={"votes": [vote_json(v) for v in rnd.votes], "deadline_block": deadline,
                          "distinct_roots": roots},
                resolution=resolution))
        return out

    def cadence(self, ctx: Ctx, hist: History, block: int) -> list[Episode]:
        max_gap = self.config["max_commit_gap_s"]
        gap_blocks = blocks_for(max_gap, self.spb)
        commits = hist.commits
        out = []
        for prev, nxt in zip(commits, commits[1:]):  # gaps that opened and closed (reported once after downtime)
            if (nxt.block - prev.block) * self.spb > max_gap:
                hours = (nxt.block - prev.block) * self.spb / 3600
                out.append(self._gap(prev.block, prev.ref_block, False, prev.block + gap_blocks,
                                     f"about {hours:.1f} h (block distance)",
                                     f"RootCommitted for blockNum {nxt.ref_block} at block {nxt.block} ended the gap.",
                                     {"next_commit_block": nxt.block, "next_ref_block": nxt.ref_block}))
        last_block = commits[-1].block if commits else ctx.settings["start_block"]
        last_ref = commits[-1].ref_block if commits else None
        mark = ctx.state("last_commit")
        if not mark or mark.get("block") != last_block:  # new commit, first run, or a reorg replaced it
            mark = {"block": last_block, "at": estimate_time(ctx, last_block, self.spb)}
            ctx.set_state("last_commit", mark)
        age = ctx.clock() - mark["at"]
        if age > max_gap:
            since = (f"the last RootCommitted (blockNum {last_ref}, block {last_block})" if commits else
                     f"start_block {last_block} (no RootCommitted stored since)")
            out.append(self._gap(last_block, last_ref, True, block, f"{age / 3600:.1f} h (worker clock)", "",
                                 {"age_s": round(age), "since": since, "estimated_at": mark["at"]}))
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

    def dissent(self, hist: History, settled) -> list[Episode]:
        streak_n = self.config["dissent_streak"]
        out = []
        streaks: dict[int, list] = {}  # slot -> [(round, settle block, vote root), ...] of the current streak

        def episode(slot, rounds, active, healed_by=None):
            first, last = rounds[0], rounds[-1]
            n = len(rounds)
            sev = "page" if n >= streak_n else "warn"
            listing = "; ".join(f"round {r.ref}: voted {root[:10]}" for r, _, root in rounds[-6:])
            body = (f"Slot {slot} voted a different merkleRoot than the reference root in {n} consecutive round(s) "
                    f"({listing}). A slot on another tree build silently blocks quorum (#93157).")
            if n >= streak_n:
                body += f" {n} >= {streak_n} in a row: the quorum margin is gone."
            return Episode(
                key=f"dissent:{slot}", episode=f"dissent:{slot}:{first[0].ref}", active=active,
                evidence_block=first[1], severity=sev,
                title=f"Oracle slot {slot} dissent ({n} round{'s' if n > 1 else ''} in a row)", body=body,
                subject={"slot": slot}, revision=f"{first[0].ref}:{last[0].ref}",
                evidence={"rounds": [{"ref_block": r.ref, "settled_block": b, "voted_root": root} for r, b, root in rounds]},
                resolution=(f"Slot {slot} voted the reference root again in round {healed_by}." if healed_by else ""))

        for rnd, at, reference in settled:
            if reference is None:
                close = hist.superseded_by(rnd.ref) if rnd.commit is None else None
                out.append(Episode(
                    key="split", episode=f"split:{rnd.ref}", active=close is None, evidence_block=at, severity="warn",
                    title=f"Oracle round {rnd.ref}: roots tied, dissent not attributable",
                    body=("Two or more merkleRoot values for blockNum %d with no committed or unique plurality root: %s."
                          % (rnd.ref, "; ".join(f"slot {v.slot} {v.root[:10]}" for v in rnd.votes))),
                    subject={"ref_block": rnd.ref}, evidence={"votes": [vote_json(v) for v in rnd.votes]},
                    resolution=f"A later round committed (blockNum {close.ref_block})." if close else ""))
                continue
            by_slot: dict[int, set[str]] = {}
            for v in rnd.votes:
                by_slot.setdefault(v.slot, set()).add(v.root)
            for slot, roots in sorted(by_slot.items()):
                if reference in roots:
                    if streaks.get(slot):
                        out.append(episode(slot, streaks.pop(slot), False, healed_by=rnd.ref))
                else:
                    streaks.setdefault(slot, []).append((rnd, at, sorted(roots)[0]))
        for slot, rounds in sorted(streaks.items()):
            out.append(episode(slot, rounds, True))
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
