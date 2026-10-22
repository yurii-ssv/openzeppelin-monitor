"""M11 — EB updater liveness (spec §3 M11).

Our updater sends `updateClusterBalance` after each `RootCommitted`, only for clusters whose on-chain EB differs
from the leaf. A round's *window* is the `update_window_s` after the commit block's chain timestamp; a transaction
counts when its block's timestamp is inside it, and a round is evaluated once the horizon (m9_oracle_common
`BlockTimes`: the checkpoint's timestamp, or the clock when blocks stop) is past the window: missed slots do not
stretch it and a stalled chain still closes it. The updater address is `config.updater` or, when null, the
address of oracle slot `updater_slot` (spec: "sender 0x16A5…, the slot-4 key"): the live one, and for a
transaction before a stored `OracleReplaced` of that slot the address of its time, ordered by (block, transaction
index), so a rotation earlier in the same block already moves the expectation.

| Trigger (spec) | Key | Severity | Rule here |
|---|---|---|---|
| a `RootCommitted` with 0 updater txs within 60 min | `liveness` | warn | the latest closed round has no `ClusterBalanceUpdated` (its `blockNum`) inside the window in a tx sent by the updater of that tx's position; re-sent per zero round (`revision`) |
| ... and the proof API shows clusters whose on-chain EB differs from the leaf | `liveness` | page | the zero round's leaves (`{proof_api}/proof/{clusterId}` at the same `referenceBlock`) vs `getEffectiveBalance` (Views) for every active ETH cluster with validators of the inventory. A cluster without a usable leaf (no answer, `cluster not found`, another `referenceBlock`, an unvalidated tuple) is UNKNOWN: a partial answer never reads as "all match"; the unknown clusters are retried every run until compared. API unreachable: stays warn, says so, health message, retried |
| two consecutive committed rounds with 0 updates | `liveness` | page | trailing zero rounds >= `zero_rounds_page`; a run reaching back past the evaluated rounds is followed back to its first zero round (the episode id and the round count stay those of the whole outage while it slides out of `history_rounds`) |
| revert rate on our updates > 10 % in a round | `revert-rate` | warn | every updater transaction of the round counts, at any time (the spec line has no window; the 60 min belong to the liveness line: QA4-F1): gated inbox `updateClusterBalance` rows from the updater (any key the slot held in that block), each counted in one round only, the round of its call's `blockNum` (decoded from the transaction, cached in `m11_reverted`), else (input not decoded, or a blockNum no stored commit has) the round whose [commit, next commit) holds it by (block, transaction index) / (those + the updater's successful updates of that `blockNum`, at any time). A round is evaluated once its window closed and re-derived every run while evaluated (later transactions change its rate; a revert of an older round's blockNum brings that round back into the evaluation); a `StaleUpdate` revert is benign only when another sender had already set the same EB for that root before it (earlier block, or earlier transaction index in the same block); `IncorrectClusterState` reverts are listed for M1 (`evidence.m1_handoff`; M1 raises its own incidents from the inbox); healed by a later round in which the updater sent transactions at a rate at or under the threshold (a round without any updater transaction proves nothing), or by later successful updates of the same round bringing it to or under the threshold while every revert that established it is still there; the open round staying evaluated while it is older than `history_rounds` |
| weekly full-state leaf comparison, mismatch older than one round | `state-mismatch` | warn | every `full_state_interval_s` the comparison over all such clusters at the latest closed root; each mismatching or unknown cluster is compared again at every later closed root (and unknown ones every run). A cluster mismatching at two consecutive evaluated roots is older than one round: warn. Resolved only by positive proof (every reported cluster compared again and matching); an unknown or unevaluated reported cluster keeps the incident (API unreachable, no leaf at the chain's root, or an empty inventory: DEFERRED, health message, retried every `full_state_retry_s`; each retry asks one cluster first and stops when the API answers for another `referenceBlock`, i.e. it is behind the chain) |

Bounded cost: one run asks the proof API at most once per cluster; after `proof_api_abort_after` consecutive transport
failures or `proof_api_run_budget_s` of waiting it asks nothing more in that run, and the clusters not asked are
UNKNOWN (retried next run, never read as a match). Every closed round no finished run evaluated
(`check:m11:evaluated_upto`, lowered to the ancestor in a reorg run) is evaluated in addition to the last
`history_rounds`, so a condition that occurred and healed during a long downtime is still reported once.

`updateClusterBalance` from other senders -> info is the Monitor's (CP1A template). Persisted state and its reorg
rule: `m11_senders` (tx hash -> sender, block) and `m11_reverted` (tx hash -> decoded call of a reverted update, block)
(rows above the ancestor deleted in the reorg run and re-read),
`m11_block_times` (BlockTimes), `check:m11:zero_proof`, `check:m11:state_evals` (the last three evaluated
roots) and `check:m11:state_full` (when the last weekly comparison ran) (proof results; each carries
its commit block and the block its chain reads were made at, and is discarded when that commit is no longer the
stored one or a reorg run's ancestor is below that block, then recomputed on the new chain; QA4-F2: a reorg run
that discarded the zero-round proof and cannot evaluate it again, API unreachable or the clusters that differed now
UNKNOWN, raises: README 6.2 rule 4, rolled back and retried with the marker and the persisted proof kept). Episodes:
m9_oracle_common (`m11_episodes`).
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from types import SimpleNamespace

from checks.base import Check, Ctx, Every, Finding, OnEvents, OnInbox
from checks.m9_oracle_common import (
    FIRST,
    UPDATE_CLUSTER_BALANCE_SELECTOR,
    BlockTimes,
    Episode,
    EpisodeBook,
    book_schema,
    inbox_enabled,
    inbox_gate_pending,
    inbox_rows,
    inbox_unavailable,
    short,
    times_schema,
    utc,
)
from checks.registry import register
from eb_storage import cluster_id, keccak, u256
from rpc import RpcError

TABLE = "m11_episodes"
GET_EB = keccak(b"getEffectiveBalance(address,uint64[],(uint32,uint64,uint64,bool,uint256))")[:4]
TIMES = "m11_block_times"
SCHEMA = book_schema(TABLE) + """;
CREATE TABLE IF NOT EXISTS m11_senders (tx_hash TEXT PRIMARY KEY, block INTEGER NOT NULL, sender TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS m11_senders_block ON m11_senders (block);
CREATE TABLE IF NOT EXISTS m11_reverted (tx_hash TEXT PRIMARY KEY, block INTEGER NOT NULL, call TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS m11_reverted_block ON m11_reverted (block);
""" + times_schema(TIMES)


class ProofApiError(Exception):
    """The proof API is unreachable or answered with an unexpected shape."""


def http_get_json(url: str, timeout: float) -> dict:
    """GET a JSON object; an HTTP error with a JSON body (e.g. 404 {"error": "cluster not found"}) is returned."""
    req = urllib.request.Request(url, headers={"user-agent": "ssv-watch/m11", "accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read())
        except (ValueError, OSError):
            raise ProofApiError(f"HTTP {exc.code}") from None
        if isinstance(body, dict):
            return body
        raise ProofApiError(f"HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError) as exc:
        raise ProofApiError(f"{type(exc).__name__}: {exc}") from None


def decode_update_input(data: str) -> dict | None:
    """updateClusterBalance(blockNum, owner, operatorIds, cluster, effectiveBalance, proof) calldata, or None."""
    try:
        raw = bytes.fromhex(data[2:])
        if "0x" + raw[:4].hex() != UPDATE_CLUSTER_BALANCE_SELECTOR:
            return None
        body = raw[4:]
        w = [int.from_bytes(body[i : i + 32], "big") for i in range(0, 32 * 10, 32)]
        off = w[2]
        n = int.from_bytes(body[off : off + 32], "big")
        if n > 64:
            return None
        ops = [int.from_bytes(body[off + 32 * (i + 1) : off + 32 * (i + 2)], "big") for i in range(n)]
        owner = "0x%040x" % w[1]
        return {"ref": w[0], "owner": owner, "operator_ids": ops, "eb": w[8],
                "cluster_id": cluster_id(owner, ops).hex()}
    except (ValueError, IndexError):
        return None


@register
class M11UpdaterLiveness(Check):
    id = "m11"
    defaults = {
        "interval_s": 300,
        "update_window_s": 3600,
        "zero_rounds_page": 2,
        "revert_rate": 0.10,
        "updater": None,  # null: the live address of oracle slot `updater_slot`
        "updater_slot": 4,
        "history_rounds": 6,  # closed rounds re-derived per run (+ any closed round not evaluated yet, e.g. downtime)
        "proof_api": "https://ssv-oracle.ssv.network/api/v1",
        "proof_api_timeout_s": 15,
        "proof_api_abort_after": 3,  # consecutive transport failures that make the API "unreachable" for the run
        "proof_api_run_budget_s": 600,  # wall time one run may spend waiting for the proof API (rest: UNKNOWN)
        "full_state_interval_s": 604_800,
        # CP3 (coordinator ruling): an undone weekly comparison (DEFERRED: API unreachable or behind the chain) is
        # retried at most once per this many seconds, not every run (each retry asked every cluster)
        "full_state_retry_s": 1_800,
        "seconds_per_block": 12,
        "require_inbox": True,  # the revert-rate rule needs the failed-transaction inbox (inbox.path)
        "revert_rate_enabled": True,  # owner D14: false = the revert-rate rule is not evaluated (open one resolved)
        "zero_round_proof_enabled": True,  # owner D14: false = no proof API comparison for a zero round (page only
        # by zero_rounds_page consecutive zero rounds)
    }
    schema = SCHEMA

    def __init__(self, config: dict, settings: dict):
        super().__init__(config, settings)
        if not config["updater"] and not settings.get("views"):
            raise SystemExit("checks.m11: set checks.m11.updater or settings.views (the updater is the live "
                             "address of oracle slot updater_slot)")
        if not config["proof_api"]:
            raise SystemExit("checks.m11: proof_api is required (spec: the oracle proof endpoint)")
        if (config["require_inbox"] and config["revert_rate_enabled"]
                and not inbox_enabled(SimpleNamespace(settings=settings))):
            raise SystemExit("checks.m11: the revert-rate rule needs the failed-transaction inbox (inbox.path); set "
                             "checks.m11.require_inbox false to run without it")
        self.schedule = (Every(config["interval_s"]), OnEvents({"RootCommitted"}), OnInbox())
        self.book = EpisodeBook(self.id, TABLE)
        self.times = BlockTimes(TIMES)
        self.window = config["update_window_s"]
        self.http_get = http_get_json
        self.monotonic = time.monotonic
        self.reset_api()

    def reset_api(self) -> None:
        """Per-run proof API state: the answers of this run (shared by the zero-round proof and the weekly
        comparison) and its bound (QA2-F2): after `proof_api_abort_after` consecutive transport failures, or once
        the run has waited `proof_api_run_budget_s` for the API, nothing more is asked in this run; the clusters
        not asked are UNKNOWN (kept, retried next run), so the single-threaded worker is never held for hours."""
        self.answers: dict[str, object] = {}
        self.api_failures, self.api_spent, self.api_stop = 0, 0.0, None

    def prepare(self, ctx: Ctx) -> None:
        if self.config["updater"]:
            self.updater = self.config["updater"].lower()
        else:
            addr = ctx.params.oracles.get(self.config["updater_slot"])
            if not addr or int(addr, 16) == 0:
                raise RuntimeError(f"oracle slot {self.config['updater_slot']} is empty: no updater address")
            self.updater = addr.lower()

    def updater_at(self, pos: tuple[int, int]) -> str:
        """The updater of the transaction at `pos` = (block, transaction index): the configured address, or the
        slot's address at that position from the stored OracleReplaced history (current address when the slot was
        never rotated in the history), so a round before a rotation is judged by the key of its time and a
        rotation earlier in the same block already counts."""
        if self.config["updater"] or not self.rotations:
            return self.updater
        before = [ev for ev in self.rotations if (ev.block, ev.tx_index) < pos]
        return before[-1].extra["new"] if before else self.rotations[0].extra["old"]

    def updaters_in(self, block: int) -> set[str]:
        """Every key the slot held during `block` (a reverted transaction carries no transaction index here)."""
        return {self.updater_at((block, -1))} | {ev.extra["new"] for ev in self.rotations if ev.block == block}

    # ---- run

    def run(self, ctx: Ctx, block: int) -> list[Finding]:
        for _ in ctx.events():
            pass
        for _ in ctx.inbox:
            pass
        if ctx.reorg_ancestor is not None:
            ctx.db.conn.execute("DELETE FROM m11_senders WHERE block>?", (ctx.reorg_ancestor,))
            ctx.db.conn.execute("DELETE FROM m11_reverted WHERE block>?", (ctx.reorg_ancestor,))
        self.times.begin(ctx, self.config["seconds_per_block"])
        self.now = self.times.horizon()
        self.reset_api()
        commits = list(ctx.db.events_after(FIRST, block, frozenset({"RootCommitted"})))
        self.rotations = [ev for ev in ctx.db.events_after(FIRST, block, frozenset({"OracleReplaced"}))
                          if ev.extra["oracleId"] == self.config["updater_slot"]]
        # the last `history_rounds` closed rounds, and every closed round no finished run has evaluated yet (QA2-F4:
        # a zero round that occurred and healed while the worker was down longer than history_rounds is still
        # reported); before the first run only history_rounds (earlier rounds are history: the book's baseline)
        mark = ctx.state("evaluated_upto")
        if mark is not None and ctx.reorg_ancestor is not None:
            mark = min(mark, ctx.reorg_ancestor)  # rounds re-included above the ancestor are new
        closed: list = []
        for c in reversed(commits):  # commits are in chain order: the closed ones are a prefix
            if self.now <= self.times(c.block) + self.window:
                continue
            if len(closed) >= self.config["history_rounds"] and (mark is None or c.block <= mark):
                break
            closed.append(c)
        closed.reverse()
        # the newest closed commit evaluated; at the first run without one, the checkpoint (later commits are new)
        ctx.set_state("evaluated_upto", closed[-1].block if closed else block if mark is None else mark)
        rounds = [self.round_stats(ctx, c, block) for c in closed]
        episodes = self.liveness(ctx, self.zero_lead(ctx, commits, closed, rounds, block) + rounds, block)
        unknown: set[str] = set()
        if not self.config["revert_rate_enabled"]:
            episodes += self.book.disabled(ctx, "revert-rate", "revert_rate_enabled", "revert-rate")
        elif not inbox_enabled(ctx):  # require_inbox false: an explicit opt-out, still said once
            ctx.health("m11-inbox", "M11 revert-rate rule not evaluated: the failed-transaction inbox is not configured "
                       "(inbox.path).", dedup="m11-inbox-disabled")
        elif inbox_unavailable(ctx) or inbox_gate_pending(ctx, UPDATE_CLUSTER_BALANCE_SELECTOR, "updateClusterBalance"):
            unknown.add("revert-rate")
        else:
            episodes += self.revert_rate(ctx, commits, closed, rounds, block)
        full, full_unknown = self.state_mismatch(ctx, commits, closed[-1] if closed else None, block)
        episodes += full
        if full_unknown:
            unknown.add("state-mismatch")
        return self.book.apply(ctx, episodes, frozenset(unknown))

    # ---- per round

    def senders(self, ctx: Ctx, events) -> dict[str, str]:
        hashes = sorted({ev.tx_hash for ev in events})
        known = {}
        for i in range(0, len(hashes), 500):
            chunk = hashes[i : i + 500]
            known.update({r[0]: r[1] for r in ctx.db.conn.execute(
                f"SELECT tx_hash, sender FROM m11_senders WHERE tx_hash IN ({','.join('?' * len(chunk))})", chunk)})
        block_of = {ev.tx_hash: ev.block for ev in events}
        for h in hashes:
            if h in known:
                continue
            tx = ctx.rpc.transaction(h)
            if not tx or not tx.get("from"):
                raise RpcError(f"eth_getTransactionByHash({h}) returned no transaction")
            known[h] = tx["from"].lower()
            ctx.db.conn.execute("INSERT OR REPLACE INTO m11_senders VALUES (?,?,?)", (h, block_of[h], known[h]))
        return known

    def round_stats(self, ctx: Ctx, commit, block: int) -> dict:
        """One round: every successful update of its `blockNum` up to the checkpoint (`events`, `ours_all`: the
        revert-rate rule has no window, QA4-F1), and those inside the 60 min window (`ours`, `others`: the
        liveness rule)."""
        start = self.times(commit.block)
        end = start + self.window
        events = [ev for ev in ctx.db.events_after((commit.block - 1, 1 << 62), block, frozenset({"ClusterBalanceUpdated"}))
                  if ev.ref_block == commit.ref_block]
        at = self.times.get([ev.block for ev in events])
        senders = self.senders(ctx, events)
        mine_all = {ev.tx_hash for ev in events if senders[ev.tx_hash] == self.updater_at((ev.block, ev.tx_index))}
        inside = {ev.tx_hash for ev in events if at[ev.block] <= end}
        return {"commit": commit, "start": start, "end": end, "events": events, "senders": senders,
                "ours": sorted(mine_all & inside), "others": sorted(inside - mine_all), "ours_all": sorted(mine_all)}

    def zero_lead(self, ctx: Ctx, commits: list, closed: list, rounds: list[dict], block: int) -> list[dict]:
        """The zero rounds before the evaluated ones that belong to the same run (QA3-F1): when the earliest evaluated
        round has no update, the run is followed back through the stored commits to its first zero round, so the
        episode keeps the id of the outage's first round while the window slides (a resolved outage is never
        derived again as a new, shorter one) and the text counts every round of it. Bounded by the outage length
        (block times and senders are cached)."""
        lead: list[dict] = []
        if not rounds or rounds[0]["ours"]:
            return lead
        i = next(k for k, c in enumerate(commits) if c is closed[0])
        while i > 0:
            i -= 1
            r = self.round_stats(ctx, commits[i], block)
            if r["ours"]:
                break
            lead.insert(0, r)
        return lead

    def liveness(self, ctx: Ctx, rounds: list[dict], block: int) -> list[Episode]:
        out = []
        run: list[dict] = []  # current run of zero rounds
        for r in rounds + [None]:
            if r is not None and not r["ours"]:
                run.append(r)
                continue
            if run:
                out.append(self._zero(ctx, run, active=r is None, healed=r, block=block))
                run = []
        return out

    def _zero(self, ctx: Ctx, run: list[dict], active: bool, healed, block) -> Episode:
        last = run[-1]["commit"]
        refs = [r["commit"].ref_block for r in run]
        sev, proof_note, evidence = "warn", "", {}
        if len(run) >= self.config["zero_rounds_page"]:
            sev = "page"
        if active and not self.config["zero_round_proof_enabled"]:
            ctx.health("m11-zero_round_proof_enabled", "M11 zero-round proof comparison disabled by config "
                       "(checks.m11.zero_round_proof_enabled false): not evaluated.",
                       dedup="m11-zero_round_proof_enabled-off")
            proof_note = " Proof API comparison disabled by config."
        elif active:
            result = self.zero_round_proof(ctx, last, block)
            unknown = result.get("unknown") or []
            if result.get("error"):
                proof_note = f" Proof API check not possible: {result['error']} (retried every run)."
            elif result["mismatches"]:
                sev = "page"
                proof_note = (f" Proof API: {len(result['mismatches'])} of {result['checked']} compared clusters differ "
                              f"from their leaf: {fmt_mismatches(result['mismatches'])}."
                              + (f" {len(unknown)} cluster(s) not comparable yet (retried every run)." if unknown
                                 else ""))
            elif unknown:
                proof_note = (f" Proof API incomplete: {result['checked']} compared cluster(s) match their leaf, "
                              f"{len(unknown)} UNKNOWN ({fmt_unknown(unknown)}): no conclusion, retried every run.")
            else:
                proof_note = f" Proof API: all {result['checked']} checked clusters match their leaf."
            evidence["proof"] = {**result, "unknown": unknown[:50]}
        others = sorted({tx for r in run for tx in r["others"]})
        who = self.updater_at((last.block, last.tx_index))
        return Episode(
            key="liveness", episode=f"liveness:{run[0]['commit'].ref_block}", active=active,
            # an absence: established at this checkpoint while it holds, healed by the next round's commit
            evidence_block=block if active else healed["commit"].block, severity=sev,
            title=f"EB updater sent 0 updates for {len(run)} committed round(s)",
            body=(f"No updateClusterBalance from the updater {who} within {self.window // 60} min of RootCommitted for "
                  f"blockNum {', '.join(map(str, refs))} (last committed at block {last.block}, "
                  f"{utc(run[-1]['start'])}). The updater keeps lastProcessedBlock in memory: no backfill until the "
                  f"next root." + (f" Other senders updated in these windows ({len(others)} tx)." if others else "")
                  + proof_note),
            subject={"updater": who}, revision=str(last.ref_block),
            evidence={"zero_rounds": refs, "commit_block": last.block, "other_sender_txs": others[:20], **evidence},
            resolution=(f"The updater sent {len(healed['ours'])} update(s) for blockNum {healed['commit'].ref_block}."
                        if healed else ""))

    def revert_lead(self, ctx: Ctx, commits: list, first: int) -> int:
        """The index of an open revert-rate incident's round when it is older than the evaluated rounds (from
        `commits[first]`): it stays derived (and open) until a later round proves a rate under the threshold, instead
        of reading its slide out of `history_rounds` as a recovery; and in a reorg run every revert-rate round
        reported after the ancestor, open or already resolved (repair 4, class 2): its resolution may rest on a clean
        round the reorg removed, so it is derived again on the new chain."""
        rows = ctx.db.conn.execute(f"SELECT finding FROM {TABLE} WHERE key='revert-rate' AND (closed=0 OR "
                                   f"reported_block>?)", (1 << 62 if ctx.reorg_ancestor is None else
                                                          ctx.reorg_ancestor,)).fetchall()
        refs = {json.loads(r["finding"])["subject"].get("ref_block") for r in rows}
        idx = [k for k, c in enumerate(commits) if c.ref_block in refs]
        return min(idx + [first])  # a round no longer stored (a reorg): the book's rule

    @staticmethod
    def round_of(row, call: dict, commits: list, by_ref: dict[int, int]) -> int | None:
        """The index in `commits` of the round a reverted update belongs to: the round of its call's `blockNum`
        (at any time, QA4-F1: the revert-rate rule has no window), else (input not decoded, or a blockNum no
        stored commit has) the round whose [commit, next commit) holds it, by (block, transaction index); None
        before the first stored commit."""
        ref = call.get("ref")
        if ref is not None and ref in by_ref:
            return by_ref[ref]
        idx = call.get("index")
        pos = (row["block"], 1 << 62 if idx is None else idx)
        k = None
        for i, c in enumerate(commits):
            if (c.block, c.tx_index) >= pos:
                break
            k = i
        return k

    def revert_rate(self, ctx: Ctx, commits: list, closed: list, rounds: list[dict], block: int) -> list[Episode]:
        """Per round, every updater transaction of the round counts (QA4-F1; spec: "revert rate on our updates
        > 10 % in a round", no window): its successful updates of the round's `blockNum` at any time and its
        reverts attributed by `round_of`. A round is evaluated once its 60 min window closed and re-derived every
        run while evaluated, so later transactions of the round change its rate."""
        if not closed:
            return []
        at = {id(c): k for k, c in enumerate(commits)}
        first, last = at[id(closed[0])], at[id(closed[-1])]
        by_ref = {c.ref_block: k for k, c in enumerate(commits)}
        start = self.revert_lead(ctx, commits, first)
        mine = [r for r in inbox_rows(ctx, UPDATE_CLUSTER_BALANCE_SELECTOR, "updateClusterBalance")
                if (r["sender"] or "").lower() in self.updaters_in(r["block"])]
        while True:  # a revert of an older round's blockNum brings that round (and the ones after it) in
            rows = [r for r in mine if r["block"] >= commits[start].block]
            calls = self.reverted_calls(ctx, rows)
            owner: dict[int, list] = {}  # commit index -> its reverts: each revert counts in one round only
            for r in rows:
                k = self.round_of(r, calls[r["tx_hash"]], commits, by_ref)
                if k is not None and k <= last:  # a later round's window is still open: evaluated when closed
                    owner.setdefault(k, []).append(r)
            older = min(owner, default=start)
            if older >= start:
                break
            start = older
        rounds = [self.round_stats(ctx, c, block) for c in commits[start:first]] + rounds
        open_eps = {r[0]: set(json.loads(r[1])["evidence"].get("reverted", [])) for r in ctx.db.conn.execute(
            f"SELECT episode, finding FROM {TABLE} WHERE key='revert-rate' AND closed=0")}
        stats = []  # per round: (counted reverts, benign reverts, total, rate or None when nothing was sent)
        for i, rd in enumerate(rounds):
            benign, counted = [], []
            for r in owner.get(start + i, []):
                (benign if self.benign(r, calls[r["tx_hash"]], rd) else counted).append(r)
            total = len(rd["ours_all"]) + len(counted)
            stats.append((counted, benign, total, len(counted) / total if total else None))
        out = []
        for i, rd in enumerate(rounds):
            c = rd["commit"]
            counted, benign, total, rate = stats[i]
            if rate is None:
                continue
            if rate <= self.config["revert_rate"]:
                if open_eps.get(f"revert-rate:{c.ref_block}", {""}) <= {r["tx_hash"] for r in counted}:
                    # later successful updates of the same round brought it back under the threshold while every
                    # revert that established it is still there: positive proof (every updater transaction of the
                    # round counted), never "no longer observed". Reverts that left the chain: the book's reorg rule
                    out.append(Episode(
                        key="revert-rate", episode=f"revert-rate:{c.ref_block}", active=False, occurred=False,
                        evidence_block=max([c.block] + [r["block"] for r in counted]
                                           + [ev.block for ev in rd["events"] if ev.tx_hash in rd["ours_all"]]),
                        severity="warn", title=f"EB updater revert rate for blockNum {c.ref_block}", body="",
                        subject={"ref_block": c.ref_block}, revision=str(c.ref_block),
                        resolution=(f"Revert rate for blockNum {c.ref_block} is now {len(counted)} of {total} "
                                    f"({rate:.0%}), at or under {self.config['revert_rate']:.0%}: later updates "
                                    f"of the same round succeeded.")))
                continue
            # healed only by a later round in which the updater sent something at a rate under the threshold: a
            # round without any updater transaction proves nothing (0/0, the liveness rule's case)
            clean = next((j for j in range(i + 1, len(rounds)) if stats[j][3] is not None
                          and stats[j][3] <= self.config["revert_rate"]), None)
            m1 = [r["tx_hash"] for r in counted if r["revert_name"] == "IncorrectClusterState"]
            names: dict[str, int] = {}
            for r in counted:
                names[r["revert_name"] or r["revert_selector"] or "unresolved"] = names.get(
                    r["revert_name"] or r["revert_selector"] or "unresolved", 0) + 1
            who = self.updater_at((c.block, c.tx_index))
            out.append(Episode(
                key="revert-rate", episode=f"revert-rate:{c.ref_block}", active=clean is None,
                evidence_block=max([c.block] + [r["block"] for r in counted]),  # the reverts establish it
                severity="warn",
                title=f"EB updater revert rate {rate:.0%} for blockNum {c.ref_block}",
                body=(f"{len(counted)} of {total} updateClusterBalance txs from {short(who)} reverted in the "
                      f"round of blockNum {c.ref_block} (threshold {self.config['revert_rate']:.0%}): "
                      + ", ".join(f"{n} x{k}" for n, k in sorted(names.items()))
                      + (f". {len(benign)} benign StaleUpdate revert(s) (another sender had already set the same EB) "
                         f"excluded" if benign else "")
                      + (f". IncorrectClusterState reverts go to M1 (stale cluster tuple): {', '.join(m1[:5])}"
                         if m1 else "") + "."),
                subject={"updater": who, "ref_block": c.ref_block}, revision=str(c.ref_block),
                evidence={"reverted": [r["tx_hash"] for r in counted][:50], "benign": [r["tx_hash"] for r in benign][:50],
                          "successful": len(rd["ours_all"]), "m1_handoff": m1, "reasons": names},
                resolution=("" if clean is None else
                            f"Revert rate for blockNum {rounds[clean]['commit'].ref_block} is under "
                            f"{self.config['revert_rate']:.0%} ({len(stats[clean][0])} of {stats[clean][2]} reverted).")))
        return out

    def reverted_calls(self, ctx: Ctx, rows) -> dict[str, dict]:
        """tx hash -> the decoded call of each reverted update (`ref`, `cluster_id`, `eb`, `index`; only `index` when
        the input does not decode), cached in `m11_reverted` (rows above a reorg's ancestor deleted and re-read). A transaction
        the node does not return raises (the run is retried; never read as "no call")."""
        hashes = sorted({r["tx_hash"] for r in rows})
        known: dict[str, dict] = {}
        for i in range(0, len(hashes), 500):
            chunk = hashes[i : i + 500]
            known.update({r[0]: json.loads(r[1]) for r in ctx.db.conn.execute(
                f"SELECT tx_hash, call FROM m11_reverted WHERE tx_hash IN ({','.join('?' * len(chunk))})", chunk)})
        block_of = {r["tx_hash"]: r["block"] for r in rows}
        for h in hashes:
            if h in known:
                continue
            tx = ctx.rpc.transaction(h)
            if not tx:
                raise RpcError(f"eth_getTransactionByHash({h}) returned no transaction")
            call = decode_update_input(tx.get("input", "0x")) or {}
            call["index"] = int(tx["transactionIndex"], 16) if tx.get("transactionIndex") else None
            known[h] = call
            ctx.db.conn.execute("INSERT OR REPLACE INTO m11_reverted VALUES (?,?,?)",
                                (h, block_of[h], json.dumps(call, sort_keys=True)))
        return known

    def benign(self, row, call: dict, rd: dict) -> bool:
        """StaleUpdate because another sender had *already* set the same EB for the same root (spec: benign): that
        sender's update must precede the revert (an earlier block, or the same block with a lower transaction index
        when the node reports the revert's index); a later update cannot explain an earlier failure."""
        if row["revert_name"] != "StaleUpdate":
            return False
        if call.get("ref") != rd["commit"].ref_block:
            return False
        index = call.get("index")

        def before(ev) -> bool:
            return ev.block < row["block"] or (ev.block == row["block"] and index is not None and ev.tx_index < index)

        return any(ev.cluster_id and ev.cluster_id.hex() == call["cluster_id"] and ev.eb == call["eb"] and before(ev)
                   and rd["senders"][ev.tx_hash] != self.updater_at((ev.block, ev.tx_index)) for ev in rd["events"])

    # ---- proof API

    def zero_round_proof(self, ctx: Ctx, commit, block: int) -> dict:
        raw = ctx.state("zero_proof")
        if raw and (raw["ref"] != commit.ref_block or raw["commit_block"] != commit.block):
            raw = None
        saved = fresh(ctx, raw)
        if saved and not saved["result"]["unknown"]:
            return saved["result"]
        only = {u["cluster"] for u in saved["result"]["unknown"]} if saved else None
        result = self.compare_leaves(ctx, commit.ref_block, block, only=only)
        if raw and not saved and (result.get("error") or (raw["result"]["mismatches"] and not result["mismatches"]
                                                          and result["unknown"])):
            # QA4-F2 (README 6.2 rule 4): this reorg run discarded the proof of this zero round (its chain reads may
            # come from deleted blocks) and cannot evaluate it again (API unreachable, or the clusters that differed
            # are UNKNOWN now): raise. The run is rolled back and retried, the marker and the persisted proof stay;
            # never a silent downgrade of what was proven, never a second page from the discarded proof later.
            raise ProofApiError(f"zero round {commit.ref_block}: the proof discarded by the reorg (ancestor "
                                f"{ctx.reorg_ancestor}) cannot be evaluated again: "
                                f"{result.get('error') or fmt_unknown(result['unknown'])}")
        if result.get("error"):
            ctx.health("proof-api", f"M11: oracle proof API unavailable ({result['error']}); the zero-update round "
                       f"{commit.ref_block} cannot be checked for EB mismatches (retried every run).",
                       dedup=f"proof-api:{commit.ref_block}")
            return saved["result"] if saved else result
        if saved:
            result = merge_results(saved["result"], result, only)
        ctx.set_state("zero_proof", {"ref": commit.ref_block, "commit_block": commit.block,
                                     "block": max(block, saved["block"]) if saved else block, "result": result})
        if result["unknown"]:
            ctx.health("proof-api-partial", f"M11: the proof comparison for the zero-update round {commit.ref_block} "
                       f"is incomplete: {len(result['unknown'])} cluster(s) UNKNOWN ({fmt_unknown(result['unknown'])}); "
                       f"retried every run.", dedup=f"proof-api-partial:{commit.ref_block}")
        return result

    def state_mismatch(self, ctx: Ctx, commits, latest, block: int) -> tuple[list[Episode], bool]:
        """The weekly full-state comparison with the one-round age condition (module docstring). Returns the
        episode(s) and whether the key is unknown this run (an open incident without positive proof either way)."""
        stored = {(c.block, c.ref_block) for c in commits}
        raw = ctx.state("state_evals") or []
        evals = [e for e in raw if (e["commit_block"], e["ref"]) in stored and fresh(ctx, e)]
        changed = len(evals) != len(raw)
        full = ctx.state("state_full")  # the last weekly full comparison (its evaluation may be trimmed from evals)
        if full and not ((full["commit_block"], full["ref"]) in stored and fresh(ctx, full)):
            full = None  # it left the canonical chain or read deleted blocks: due again
        row = ctx.db.conn.execute(f"SELECT finding FROM {TABLE} WHERE key='state-mismatch' AND closed=0").fetchone()
        reported = {m["cluster"] for m in json.loads(row["finding"])["evidence"]["mismatches"]} if row else set()
        if latest is not None:
            cur = evals[-1] if evals and evals[-1]["ref"] == latest.ref_block else None
            if full is None or ctx.clock() - full["at"] >= self.config["full_state_interval_s"]:
                scope = None  # the weekly full comparison
            else:
                scope = set(reported)
                if cur is not None:
                    scope |= {u["cluster"] for u in cur["unknown"]}  # retried every run
                    scope -= {c for c in reported if status(cur, c) is not None and status(cur, c) != "unknown"}
                elif evals:  # a later root closed: compare again what mismatched or was unknown at the last one
                    scope |= {m["cluster"] for m in evals[-1]["mismatches"]} | {u["cluster"] for u in evals[-1]["unknown"]}
            tried = ctx.state("state_full_tried") if scope is None else None
            waiting = tried is not None and ctx.clock() - tried["at"] < self.config["full_state_retry_s"]
            if not waiting and (scope is None or scope):
                result = self.compare_leaves(ctx, latest.ref_block, block, only=scope)
                if result.get("error"):
                    what = "weekly full-state leaf comparison" if scope is None else "leaf re-comparison"
                    if scope is None:  # retried once per full_state_retry_s, not every run (CP3)
                        ctx.set_state("state_full_tried", {"at": ctx.clock(), "ref": latest.ref_block})
                    ctx.health("proof-api-weekly", f"M11 {what} at blockNum {latest.ref_block} DEFERRED: "
                               f"{result['error']}; retried every run.", dedup="proof-api-weekly")
                else:
                    ev = {"ref": latest.ref_block, "commit_block": latest.block, "block": block,
                          "scope": None if scope is None else sorted(scope), **result}
                    if scope is None:
                        ctx.set_state("state_full", {"ref": latest.ref_block, "commit_block": latest.block,
                                                     "block": block, "at": ctx.clock()})
                        ctx.set_state("state_full_tried", None)
                    if cur is not None:
                        ev = merge_eval(cur, ev)
                        evals[-1] = ev
                    else:
                        evals.append(ev)
                    evals = evals[-3:]
                    changed = True
        if changed:
            ctx.set_state("state_evals", evals)
        newest = evals[-1] if evals else None
        prev = evals[-2] if len(evals) > 1 else None
        aged = [m for m in (newest["mismatches"] if newest else [])
                if prev is not None and status(prev, m["cluster"]) == "mismatch"]
        if newest is not None and newest["unknown"]:
            ctx.health("proof-api-weekly-partial", f"M11 leaf comparison at blockNum {newest['ref']} incomplete: "
                       f"{len(newest['unknown'])} cluster(s) UNKNOWN ({fmt_unknown(newest['unknown'])}); retried "
                       f"every run.", dedup=f"proof-api-weekly-partial:{newest['ref']}")
        proven = newest is not None and all(status(newest, c) in ("match", "gone") for c in reported)
        # a reported cluster seen mismatching at the newest root only, with no earlier evaluated root on the canonical
        # chain or a definite match there, the newest being the latest closed root (an evaluation that could not be
        # redone after a reorg leaves the latest root unevaluated: unknown): "older than one round" is definitively
        # not established (QA2-F3: a reorg removed the round that aged it). The key is not derived: a reorg run leaves
        # it to the re-find rule ("evidence left the canonical chain"); a later round that still mismatches warns again
        definite = newest is not None and latest is not None and newest["ref"] == latest.ref_block and all(
            status(newest, c) in ("match", "gone") or (status(newest, c) == "mismatch" and (
                prev is None or status(prev, c) in ("match", "gone"))) for c in reported)
        if not aged and reported and not definite:
            return [], True  # an open incident whose clusters are not all compared again: unknown, never clean
        if not aged and (not reported or not proven):
            return [], False
        since = {m["cluster"]: prev["ref"] for m in aged} if prev else {}
        digest = hashlib.sha1(",".join(sorted(m["cluster"] for m in aged)).encode()).hexdigest()[:12]
        ref = newest["ref"]
        return [Episode(
            key="state-mismatch", episode="state-mismatch", active=bool(aged), occurred=bool(aged),
            evidence_block=newest["commit_block"], severity="warn",
            title=f"{len(aged)} cluster(s) with on-chain EB != leaf for more than one round (blockNum {ref})",
            body=(f"Leaf comparison at blockNum {ref} (after its update window): {len(aged)} cluster(s) still differ "
                  f"from their leaf after mismatching at the previous evaluated root {prev['ref'] if prev else '?'}: "
                  f"{fmt_mismatches(aged)}; {len(newest['unknown'])} not comparable."),
            subject={"ref_block": ref}, revision=digest,
            evidence={"mismatches": aged[:50], "since_ref": since, "unknown": newest["unknown"][:50],
                      "checked": newest["checked"], "compared_again": [] if aged else sorted(reported)},
            resolution=(f"Leaf comparison at blockNum {ref}: every reported cluster ({len(reported)}) was compared "
                        f"again and matches its leaf (or is no longer an active ETH cluster)."))], False

    @staticmethod
    def _use(ci, body, ref: int, leaves: dict, unknown: list) -> None:
        """One proof API answer: a usable leaf at blockNum `ref`, or an UNKNOWN cluster with the reason."""
        if isinstance(body, ProofApiError):
            unknown.append({"cluster": ci.cluster_id, "reason": str(body)[:80]})
        elif not isinstance(body, dict) or "effectiveBalance" not in body:
            unknown.append({"cluster": ci.cluster_id, "reason": str(body.get("error") if isinstance(body, dict)
                                                                 else "unexpected shape")[:80]})
        elif int(body.get("referenceBlock", -1)) != ref:
            unknown.append({"cluster": ci.cluster_id, "reason": f"API at referenceBlock {body.get('referenceBlock')}"})
        else:
            leaves[ci.cluster_id] = (ci, int(body["effectiveBalance"]))

    def _ask(self, ci):
        """One proof API request for a cluster not asked in this run (answer cached in `answers`), within the run's
        bound: after `proof_api_abort_after` consecutive failures or `proof_api_run_budget_s`, `api_stop` is set."""
        if ci.cluster_id in self.answers:
            return self.answers[ci.cluster_id]
        base = self.config["proof_api"].rstrip("/")
        t0 = self.monotonic()
        try:
            self.answers[ci.cluster_id] = self.http_get(f"{base}/proof/{ci.cluster_id}",
                                                        self.config["proof_api_timeout_s"])
            self.api_failures = 0
        except ProofApiError as exc:
            self.answers[ci.cluster_id] = exc
            self.api_failures += 1
            if self.api_failures >= self.config["proof_api_abort_after"]:
                self.api_stop = f"API unreachable: {self.api_failures} consecutive failures, last {exc}"[:120]
        finally:
            self.api_spent += self.monotonic() - t0
        if self.api_stop is None and self.api_spent >= self.config["proof_api_run_budget_s"]:
            self.api_stop = f"run budget of {self.config['proof_api_run_budget_s']} s for the proof API spent"
        return self.answers[ci.cluster_id]

    def compare_leaves(self, ctx: Ctx, ref: int, block: int, only: set[str] | None = None) -> dict:
        """Leaf EB (proof API, at blockNum `ref`) vs getEffectiveBalance at `block` for every active ETH cluster with
        validators (or the clusters of `only`). {"checked", "mismatches", "unknown", "gone", "error"}: a cluster
        without a usable leaf or tuple is UNKNOWN (never a match); `gone`: an `only` cluster that is no longer an
        active ETH cluster with validators; `error` set when nothing could be compared (API unreachable, no leaf at
        the root for any cluster, or an empty inventory)."""
        inv = ctx.inventory(block)
        infos = [ci for _, ci in sorted(inv.clusters.items()) if ci.version == "ETH"
                 and (only is None or ci.cluster_id in only)]
        unknown = [{"cluster": ci.cluster_id, "reason": "tuple not validated against storage"} for ci in infos
                   if not ci.valid]
        clusters = [ci for ci in infos if ci.valid and ci.cluster.active and ci.cluster.validator_count > 0]
        gone = sorted((only or set()) - {ci.cluster_id for ci in clusters} - {u["cluster"] for u in unknown})
        if not clusters and only is None:  # an empty inventory is a missing input, never "every cluster matches"
            return {"checked": 0, "mismatches": [], "unknown": unknown, "gone": [],
                    "error": "no active ETH cluster in the inventory"}
        leaves = {}
        answers = self.answers
        if only is None and clusters and self.api_stop is None:
            # Probe first (CP3): an API that answers for another root (behind the chain) has no leaf at `ref` for any
            # cluster; asking all of them would only cost time. DEFERRED, never a match.
            probe = self._ask(clusters[0])
            if (isinstance(probe, dict) and "effectiveBalance" in probe
                    and int(probe.get("referenceBlock", -1)) != ref):
                return {"checked": 0, "mismatches": [], "unknown": unknown, "gone": [],
                        "error": f"the proof API answers for referenceBlock {probe.get('referenceBlock')}, not "
                                 f"blockNum {ref} (probe of one cluster); not compared"}
        for ci in clusters:
            if ci.cluster_id not in answers:
                if self.api_stop is not None:  # bounded run: UNKNOWN, retried next run (never dropped)
                    unknown.append({"cluster": ci.cluster_id, "reason": f"not asked ({self.api_stop})"[:120]})
                    continue
                self._ask(ci)
            self._use(ci, answers[ci.cluster_id], ref, leaves, unknown)
        if not leaves and clusters:
            return {"checked": 0, "mismatches": [], "unknown": unknown, "gone": gone,
                    "error": self.api_stop or f"no leaf at blockNum {ref} for any of {len(clusters)} clusters"}
        items = list(leaves.values())
        calls = [(self.settings["views"], get_eb_calldata(ci)) for ci, _ in items]
        res = ctx.rpc.call_many(calls, block) if calls else []
        mismatches = []
        for (ci, leaf), out in zip(items, res):
            if len(out) < 32:
                raise RpcError(f"getEffectiveBalance for {ci.cluster_id} returned {len(out)} bytes")
            onchain = int.from_bytes(out[:32], "big")
            if onchain != leaf:
                mismatches.append({"cluster": ci.cluster_id, "owner": ci.owner, "onchain_eb": onchain, "leaf_eb": leaf})
        return {"checked": len(items), "mismatches": mismatches, "unknown": unknown, "gone": gone, "error": None}


def status(ev: dict, cluster: str) -> str | None:
    """A cluster's outcome in one evaluation: mismatch / unknown / gone / match, or None when not evaluated."""
    if any(m["cluster"] == cluster for m in ev["mismatches"]):
        return "mismatch"
    if any(u["cluster"] == cluster for u in ev["unknown"]):
        return "unknown"
    if cluster in ev.get("gone", []):
        return "gone"
    if ev.get("scope") is None or cluster in ev["scope"]:
        return "match"
    return None


def merge_results(old: dict, new: dict, only: set[str] | None) -> dict:
    """`new` re-compared the clusters of `only` (None: all): it replaces their outcome in `old`."""
    if only is None:
        return new
    keep = lambda rows: [r for r in rows if r["cluster"] not in only]  # noqa: E731
    return {"checked": old["checked"] + new["checked"], "mismatches": keep(old["mismatches"]) + new["mismatches"],
            "unknown": keep(old["unknown"]) + new["unknown"], "gone": sorted(set(old.get("gone", [])) |
                                                                            set(new.get("gone", []))),
            "error": None}


def merge_eval(old: dict, new: dict) -> dict:
    """Two evaluations at the same root: the newer outcome wins for every cluster it covered."""
    if new["scope"] is None:
        return new
    only = set(new["scope"])
    merged = merge_results(old, new, only)
    scope = None if old["scope"] is None else sorted(set(old["scope"]) | only)
    return {**old, **merged, "scope": scope, "block": max(old["block"], new["block"])}


def fresh(ctx: Ctx, saved: dict | None) -> dict | None:
    """A persisted proof result, or None when a reorg run's ancestor is below the block it was evaluated at (its
    chain reads may come from deleted blocks)."""
    if saved and ctx.reorg_ancestor is not None and saved.get("block", 1 << 62) > ctx.reorg_ancestor:
        return None
    return saved


def get_eb_calldata(ci) -> bytes:
    ops = ci.operator_ids
    return (GET_EB + bytes.fromhex(ci.owner[2:]).rjust(32, b"\0") + u256(7 * 32)
            + b"".join(u256(int(x)) for x in ci.cluster.as_tuple()) + u256(len(ops)) + b"".join(u256(o) for o in ops))


def fmt_unknown(us: list[dict]) -> str:
    return ", ".join(f"{u['cluster'][:12]} {u['reason']}" for u in us[:3]) + (f" (+{len(us) - 3})" if len(us) > 3 else "")


def fmt_mismatches(ms: list[dict]) -> str:
    return ", ".join(f"{m['cluster'][:12]} chain {m['onchain_eb']} leaf {m['leaf_eb']}" for m in ms[:5]) + (
        f" (+{len(ms) - 5})" if len(ms) > 5 else "")
