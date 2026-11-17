"""Oracle-family helpers shared by M9, M10 and M11 (CP2b): the round model, the episode book, chain block times.

Not a check (nothing is registered here). Everything is derived from the stored proxy logs of the canonical
chain (`logs`, rewound by the framework on a reorg) and the gated inbox (`inbox.canonical = 1`), so a run
re-derives every condition from scratch; the only state a check keeps is its episode book.

Rounds. A round is keyed by its reference block (`blockNum`). `WeightedRootProposed(merkleRoot indexed,
blockNum indexed, accumulatedWeight, quorum, oracleId, oracle)` is one vote; `RootCommitted(merkleRoot,
blockNum)` closes it. At the pin (SSVDAO.commitRoot) a vote for a `blockNum` at or below the latest committed
one reverts (`StaleBlockNumber`), so a committed round with a higher `blockNum` *supersedes* every older open
round: it can never commit afterwards.

Episodes. One condition instance (round 123 failed, slot 3 dissenting since round 456, ...) is an `Episode`
with an incident `key` (several episodes may share one key, e.g. successive cadence gaps), a stable `episode`
id, `active` (the condition holds at the checkpoint) and `evidence_block` (where it was established). The book
(`EpisodeBook`, one table per check) turns the episodes of a run into findings:

| Episode state at this run | Finding(s) |
|---|---|
| active | its finding (opens / keeps / escalates / re-sends on a new `revision`); an earlier episode of the same key that occurred and healed without being reported (after the baseline), or the reported one that changed (new `revision`) and healed between two sweeps, is sent first |
| healed, reported and still open in the book | `resolve=True` with the episode's resolution text; preceded by its finding when it changed (new `revision`) since it was reported, so a change that appeared and healed between two sweeps is still sent |
| healed, never reported, `evidence_block` > baseline | its finding then `resolve=True` (reported once, e.g. after downtime); not for a clean evaluation (`occurred=False`) |
| healed, already resolved, derived again with another `revision` and an `evidence_block` above the row's last report | its finding then `resolve=True`, once (a reorg replaced its evidence and the new chain holds more, e.g. a failed round that joined a resolved run); a shrunk or unchanged episode sends nothing |
| healed, never reported, at or below the baseline | nothing (history before the check's first run) |
| unknown (an input is unavailable) | the last reported finding again, unchanged (stays open; nothing sent) |
| book row whose episode is no longer derived | non-reorg run: resolved; reorg run (evidence above the ancestor): row deleted and the key left to the framework's re-find rule ("evidence left the canonical chain", README §6.2) |

The baseline is the checkpoint of the check's first run (lowered to the ancestor by a reorg below it).

Reorg rule of the book tables (README §6.1/§6.2): rows are derived state. In the run with reason `reorg` every
episode is re-derived from the rewound logs; a row whose episode is still derived is kept (same episode: not
reported twice), a row whose episode vanished and whose evidence is above the ancestor is deleted and its key
is not reported (the framework resolves it with the reorg text), any other row is handled as in a normal run.
The table commits in the run's transaction, so a crash between detection and handling replays both.
"""

from __future__ import annotations

import bisect
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

from checks.base import Ctx, Finding
from eb_storage import keccak
from rpc import RpcError

WRP = "WeightedRootProposed"
WRP_SIGNATURE = "WeightedRootProposed(bytes32,uint64,uint256,uint256,uint32,address)"
ORACLE_EVENTS = {WRP: WRP_SIGNATURE}
ROUND_KINDS = frozenset({WRP, "RootCommitted"})

COMMIT_ROOT_SELECTOR = "0x" + keccak(b"commitRoot(bytes32,uint64)")[:4].hex()
UPDATE_CLUSTER_BALANCE_SELECTOR = "0x" + keccak(
    b"updateClusterBalance(uint64,address,uint64[],(uint32,uint64,uint64,bool,uint256),uint32,bytes32[])")[:4].hex()

FIRST = (-1, 0)  # events_after cursor before every stored event


# ---------------------------------------------------------------- round model


@dataclass(frozen=True)
class Vote:
    slot: int
    root: str
    oracle: str
    block: int
    log_index: int
    tx_hash: str
    weight: int  # accumulatedWeight after this vote
    quorum: int  # threshold in weight units at this round

    @property
    def pos(self) -> tuple[int, int]:
        return (self.block, self.log_index)


@dataclass
class Round:
    ref: int
    votes: list[Vote] = field(default_factory=list)
    commit: object | None = None  # the RootCommitted Event for this blockNum

    @property
    def first_vote(self) -> Vote | None:
        return self.votes[0] if self.votes else None


def decode_vote(ev) -> Vote:
    """A stored raw WeightedRootProposed log -> Vote; a malformed log raises (never read as no vote)."""
    topics, data = ev.extra.get("topics"), ev.extra.get("data", "0x")
    raw = bytes.fromhex(data[2:])
    if not topics or len(topics) != 2 or len(raw) != 4 * 32:
        raise ValueError(f"malformed {WRP} log at {ev.block}:{ev.log_index}: topics {topics}, {len(raw)} data bytes")
    words = [int.from_bytes(raw[i : i + 32], "big") for i in range(0, 128, 32)]
    if words[2] >> 32 or words[3] >> 160:
        raise ValueError(f"malformed {WRP} log at {ev.block}:{ev.log_index}: oracleId/oracle out of range")
    return Vote(slot=words[2], root=topics[0].lower(), oracle="0x%040x" % words[3], block=ev.block,
                log_index=ev.log_index, tx_hash=ev.tx_hash, weight=words[0], quorum=words[1])


@dataclass
class History:
    rounds: dict[int, Round]
    commits: list  # RootCommitted Events in chain order
    replaced: list  # OracleReplaced Events in chain order
    _refs: list | None = None

    def superseded_by(self, ref: int, before: tuple[int, int] | None = None):
        """The first commit of a higher blockNum (optionally strictly before position `before`), or None.
        Committed blockNums strictly increase in chain order (StaleBlockNumber), so this is a bisection."""
        if self._refs is None:
            self._refs = [c.ref_block for c in self.commits]
        i = bisect.bisect_right(self._refs, ref)
        if i == len(self.commits) or (before is not None and self.commits[i].pos >= before):
            return None
        return self.commits[i]

    def closing_event(self, rnd: Round, before: tuple[int, int] | None = None):
        """The commit that closed `rnd` (its own, or a superseding one), optionally strictly before `before`."""
        own = rnd.commit if rnd.commit is not None and (before is None or rnd.commit.pos < before) else None
        sup = self.superseded_by(rnd.ref, before)
        cands = [c for c in (own, sup) if c is not None]
        return min(cands, key=lambda c: c.pos) if cands else None

    def oracle_addresses(self) -> set[str]:
        """Every address that voted or was assigned/unassigned a slot in the stored history."""
        out = {v.oracle for r in self.rounds.values() for v in r.votes}
        for ev in self.replaced:
            out |= {ev.extra["old"], ev.extra["new"]}
        return out - {"0x" + "00" * 20}


def load_history(db, upto: int) -> History:
    rounds: dict[int, Round] = {}
    commits, replaced = [], []
    for ev in db.events_after(FIRST, upto, frozenset({WRP, "RootCommitted", "OracleReplaced"})):
        if ev.kind == WRP:
            v = decode_vote(ev)
            ref = int(ev.extra["topics"][1], 16)
            rounds.setdefault(ref, Round(ref)).votes.append(v)
        elif ev.kind == "RootCommitted":
            commits.append(ev)
            rnd = rounds.setdefault(ev.ref_block, Round(ev.ref_block))
            if rnd.commit is None:
                rnd.commit = ev
        else:
            replaced.append(ev)
    return History(rounds, commits, replaced)


def times_schema(table: str) -> str:
    return f"CREATE TABLE IF NOT EXISTS {table} (block INTEGER PRIMARY KEY, hash TEXT NOT NULL, ts INTEGER NOT NULL)"


class BlockTimes:
    """Chain timestamps (`eth_getBlockByNumber`) of the blocks a check's time rules use, cached in `table`.

    Every minute- or hour-based rule compares chain timestamps of its evidence blocks with `horizon()`, never
    block distances: missed slots do not stretch a window and a chain that stops producing blocks still lets the
    clock close it. A header the node does not return, one without a timestamp, or one whose hash differs from the
    hash the scan recorded for that block (a reorg the framework has not processed yet) raises: a time rule is
    never evaluated on a guessed time. Reorg rule: in a reorg run the cached rows above the ancestor are deleted
    (re-read from the new chain)."""

    def __init__(self, table: str):
        self.table = table

    def begin(self, ctx: Ctx, seconds_per_block: float) -> None:
        self.ctx, self.spb, self.mem = ctx, seconds_per_block, {}
        if ctx.reorg_ancestor is not None:
            ctx.db.conn.execute(f"DELETE FROM {self.table} WHERE block>?", (ctx.reorg_ancestor,))

    def get(self, blocks) -> dict[int, int]:
        want = sorted({int(b) for b in blocks} - set(self.mem))
        conn = self.ctx.db.conn
        for i in range(0, len(want), 500):
            chunk = want[i : i + 500]
            self.mem.update({r[0]: r[1] for r in conn.execute(
                f"SELECT block, ts FROM {self.table} WHERE block IN ({','.join('?' * len(chunk))})", chunk)})
        missing = [b for b in want if b not in self.mem]
        if missing:
            if max(missing) > self.ctx.block:
                raise ValueError(f"block time asked above the checkpoint {self.ctx.block}: {max(missing)}")
            headers = self.ctx.rpc.batch("eth_getBlockByNumber", [[hex(b), False] for b in missing])
            recorded = {}
            for i in range(0, len(missing), 500):
                chunk = missing[i : i + 500]
                recorded.update({r[0]: r[1] for r in conn.execute(
                    f"SELECT number, hash FROM blocks WHERE number IN ({','.join('?' * len(chunk))})", chunk)})
            for b, hdr in zip(missing, headers):
                if not hdr or hdr.get("timestamp") in (None, "", "0x"):
                    raise RpcError(f"eth_getBlockByNumber({b}) returned no header timestamp")
                h = (hdr.get("hash") or "").lower()
                if b in recorded and h != recorded[b]:
                    raise RpcError(f"block {b}: header hash {h[:12]} differs from the scanned {recorded[b][:12]} "
                                   f"(reorg not processed yet)")
                ts = int(hdr["timestamp"], 16)
                self.mem[b] = ts
                conn.execute(f"INSERT OR REPLACE INTO {self.table} VALUES (?,?,?)", (b, h, ts))
        return {b: self.mem[int(b)] for b in blocks}

    def __call__(self, block: int) -> int:
        return self.get([block])[block]

    def horizon(self) -> float:
        """The time up to which the stored chain is complete: every block with a timestamp at or before it is at or
        below the checkpoint, or does not exist yet. The checkpoint's own timestamp, or, when blocks stop arriving,
        the worker clock less the confirmation depth (+2 slots of margin for the head read), but never at or past the
        timestamp of the block after the checkpoint when that block exists (read every run, not cached: it is not
        confirmed): an event in the unconfirmed tail is never judged absent (missed slots let the clock pass it).
        Accepted limitation (coordinator ruling, repair 3): during a total chain halt with confirmations > 0 the
        unconfirmed blocks never confirm, so the clock rules (30 min, 60 min, 9 h) wait for blocks again."""
        conf = int(self.ctx.settings.get("confirmations", 0))
        lag = (conf + 2) * self.spb if conf >= 0 else 96 * self.spb  # finalized: 64..95 slots behind the head
        at = float(self(self.ctx.block))
        h = max(at, self.ctx.clock() - lag)
        nxt = self.ctx.rpc.request("eth_getBlockByNumber", [hex(self.ctx.block + 1), False])
        if nxt and nxt.get("timestamp") not in (None, "", "0x"):  # never past a block that exists unprocessed
            h = min(h, max(at, int(nxt["timestamp"], 16) - 1))
        return h


_FN = "(lower(selector)=? OR function LIKE ?)"


def inbox_rows(ctx: Ctx, selector: str, name: str) -> list:
    """Inbox rows of one function that passed the framework's gate (canonical at or below the checkpoint, §5.2),
    whether or not their revert reason is resolved yet (an unresolved reason is still a failed transaction)."""
    return ctx.db.conn.execute(
        f"SELECT * FROM inbox WHERE canonical=1 AND block<=? AND {_FN} ORDER BY block, id",
        (ctx.block, selector, name + "(%")).fetchall()


def inbox_gate_pending(ctx: Ctx, selector: str, name: str) -> bool:
    """Rows of the function at or below the checkpoint whose canonical hash has not been compared yet."""
    return ctx.db.conn.execute(
        f"SELECT 1 FROM inbox WHERE canonical IS NULL AND block<=? AND {_FN} LIMIT 1",
        (ctx.block, selector, name + "(%")).fetchone() is not None


def inbox_enabled(ctx: Ctx) -> bool:
    inbox = ctx.settings.get("inbox") or {}
    return bool(inbox.get("enabled", True) and inbox.get("path"))


def inbox_unavailable(ctx: Ctx) -> bool:
    """The framework's record of the last step: the inbox file was missing/unreadable or its gate could not run."""
    return bool((ctx.db.meta("inputs_unavailable", {}) or {}).get("inbox"))


# ---------------------------------------------------------------- episodes


@dataclass
class Episode:
    key: str
    episode: str
    active: bool
    evidence_block: int
    severity: str
    title: str
    body: str
    subject: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    revision: str | None = None
    resolution: str = "Condition no longer holds."
    occurred: bool = True  # False: a clean evaluation that only closes an open episode, never reported


def book_schema(table: str) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {table} (
  episode TEXT PRIMARY KEY, key TEXT NOT NULL, evidence_block INTEGER NOT NULL, reported_block INTEGER NOT NULL,
  closed INTEGER NOT NULL DEFAULT 0, finding TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS {table}_key ON {table} (key, closed)
"""


class EpisodeBook:
    """Per-check episode table (see the module docstring for the state table and its reorg rule)."""

    def __init__(self, check_id: str, table: str):
        self.check_id, self.table = check_id, table

    def baseline(self, ctx: Ctx) -> int:
        base = ctx.state("baseline")
        if base is None:
            base = ctx.block
        if ctx.reorg_ancestor is not None:
            base = min(base, ctx.reorg_ancestor)
        ctx.set_state("baseline", base)
        return base

    def _finding(self, e: Episode, resolve: bool = False) -> Finding:
        if resolve:
            return Finding(check=self.check_id, key=e.key, severity=e.severity, title=e.title, body=e.resolution,
                           subject=e.subject, evidence=e.evidence, resolve=True)
        return Finding(check=self.check_id, key=e.key, severity=e.severity, title=e.title, body=e.body,
                       subject=e.subject, evidence=e.evidence, revision=e.revision)

    def _store(self, e: Episode, block: int, closed: bool) -> None:
        f = {"severity": e.severity, "title": e.title, "body": e.body, "subject": e.subject, "evidence": e.evidence,
             "revision": e.revision, "resolution": e.resolution}
        self.conn.execute(
            f"INSERT INTO {self.table} (episode, key, evidence_block, reported_block, closed, finding) "
            f"VALUES (?,?,?,?,?,?) ON CONFLICT(episode) DO UPDATE SET key=excluded.key, "
            f"evidence_block=excluded.evidence_block, reported_block=excluded.reported_block, "
            f"closed=excluded.closed, finding=excluded.finding",
            (e.episode, e.key, e.evidence_block, block, int(closed), json.dumps(f, sort_keys=True)))

    @staticmethod
    def _from_row(r) -> Episode:
        f = json.loads(r["finding"])
        return Episode(key=r["key"], episode=r["episode"], active=False, evidence_block=r["evidence_block"],
                       severity=f["severity"], title=f["title"], body=f["body"], subject=f["subject"],
                       evidence=f["evidence"], revision=f["revision"], resolution=f["resolution"])

    @staticmethod
    def _regrown(e: Episode, r) -> bool:
        """A resolved (closed) row whose episode is derived again with another revision, established after the row
        was last reported: a reorg replaced its evidence and the canonical chain now holds more (e.g. a failed round
        that joined an already resolved run of failed rounds). What it became was never sent."""
        return (bool(r["closed"]) and e.occurred and e.revision is not None
                and e.revision != json.loads(r["finding"])["revision"] and e.evidence_block > r["reported_block"])

    def disabled(self, ctx: Ctx, prefix: str, switch: str, rule: str) -> list[Episode]:
        """A rule switched off by config (owner D14, `checks.<id>.<switch>` false): not evaluated, said by one health
        line per run, and each of its incidents still open is resolved once with the plain text "rule disabled by
        config" (never left dangling)."""
        ctx.health(f"{self.check_id}-{switch}", f"{self.check_id.upper()} {rule} rule disabled by config "
                   f"(checks.{self.check_id}.{switch} false): not evaluated.", dedup=f"{self.check_id}-{switch}-off")
        out = []
        for r in ctx.db.conn.execute(f"SELECT * FROM {self.table} WHERE closed=0 AND key LIKE ?", (prefix + "%",)):
            e = self._from_row(r)
            e.occurred, e.resolution = False, f"Rule disabled by config (checks.{self.check_id}.{switch} false)."
            out.append(e)
        return out

    def apply(self, ctx: Ctx, episodes: list[Episode], unknown_keys: frozenset[str] = frozenset()) -> list[Finding]:
        """Findings for this run's episodes. `unknown_keys`: keys the run could not evaluate (an input is
        unavailable): their open episodes are re-reported unchanged, never resolved."""
        self.conn = ctx.db.conn
        base = self.baseline(ctx)
        rows = {r["episode"]: r for r in self.conn.execute(f"SELECT * FROM {self.table}")}
        derived = {e.episode for e in episodes}
        by_key: dict[str, list[Episode]] = {}
        for e in sorted(episodes, key=lambda e: (e.evidence_block, e.episode)):
            by_key.setdefault(e.key, []).append(e)
        out: list[Finding] = []
        handled: set[str] = set()  # keys reported (or resolved) by this run

        for key, eps in by_key.items():
            active = [e for e in eps if e.active]
            if active:
                cur = active[-1]
                for e in eps:  # an earlier episode of the key that occurred and healed unseen: sent first
                    if e is cur or not e.occurred:
                        continue
                    r = rows.get(e.episode)
                    if r is None and e.evidence_block > base and e.revision != cur.revision:
                        out.append(self._finding(e))
                    elif (r is not None and not r["closed"]
                          and e.revision != json.loads(r["finding"])["revision"]):
                        out.append(self._finding(e))  # the reported one grew and healed while a later one began
                    elif r is not None and self._regrown(e, r):
                        out.append(self._finding(e))  # a resolved one re-established with more evidence
                out.append(self._finding(cur))
                self._store(cur, ctx.block, closed=False)
                for e in eps:  # older episodes of the same key are covered by the active one
                    if e is not cur:
                        self._store(e, ctx.block, closed=True)
                handled.add(key)
                continue
            resolved = False
            for e in eps:
                r = rows.get(e.episode)
                if r is not None and not r["closed"]:
                    if not resolved:
                        if e.occurred and e.revision != json.loads(r["finding"])["revision"]:
                            out.append(self._finding(e))  # grew and healed between two sweeps: send what it became
                        out.append(self._finding(e, resolve=True))
                        resolved = True
                    self._store(e, ctx.block, closed=True)
                elif (r is None and e.occurred and e.evidence_block > base) or (r is not None and self._regrown(e, r)):
                    out += [self._finding(e), self._finding(e, resolve=True)]
                    resolved = True
                    self._store(e, ctx.block, closed=True)
            if resolved:
                handled.add(key)

        for ep, r in rows.items():
            if ep in derived:
                continue
            key = r["key"]
            if key in unknown_keys and not r["closed"]:
                if key not in handled:
                    out.append(self._finding(self._from_row(r)))
                    handled.add(key)
                continue
            if ctx.reorg_ancestor is not None and r["evidence_block"] > ctx.reorg_ancestor:
                # the episode left the canonical chain: forget it; an open key goes to the re-find rule
                self.conn.execute(f"DELETE FROM {self.table} WHERE episode=?", (ep,))
                continue
            if not r["closed"]:
                if key not in handled:
                    e = self._from_row(r)
                    e.resolution = "Condition no longer observed on the canonical chain."
                    out.append(self._finding(e, resolve=True))
                    handled.add(key)
                self.conn.execute(f"UPDATE {self.table} SET closed=1 WHERE episode=?", (ep,))
        return out


def utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def short(addr: str) -> str:
    return addr[:6] + "…" + addr[-4:] if addr and len(addr) > 12 else str(addr)
