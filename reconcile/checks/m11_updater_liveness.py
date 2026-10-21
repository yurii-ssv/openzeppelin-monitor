"""M11 — EB updater liveness (spec §3 M11).

Our updater sends `updateClusterBalance` after each `RootCommitted`, only for clusters whose on-chain EB differs
from the leaf. A round's *window* is `[commit block, commit block + update_window_s]` (in blocks); a round is
evaluated once its window has closed at the checkpoint. The updater address is `config.updater` or, when null,
the address of oracle slot `updater_slot` (spec: "sender 0x16A5…, the slot-4 key"): the live one, and for a
transaction before a stored `OracleReplaced` of that slot the address of its time, so a rotation moves the
expectation without a code change and without re-judging older rounds.

| Trigger (spec) | Key | Severity | Rule here |
|---|---|---|---|
| a `RootCommitted` with 0 updater txs within 60 min | `liveness` | warn | the latest closed round has no `ClusterBalanceUpdated` (its `blockNum`) in a tx sent by the updater inside the window; re-sent per zero round (`revision`) |
| ... and the proof API shows clusters whose on-chain EB differs from the leaf | `liveness` | page | the zero round's leaves (`{proof_api}/proof/{clusterId}` at the same `referenceBlock`) vs `getEffectiveBalance` (Views) for every active ETH cluster with validators of the inventory; API unreachable: stays warn, says so, health message, retried |
| two consecutive committed rounds with 0 updates | `liveness` | page | trailing zero rounds >= `zero_rounds_page` |
| revert rate on our updates > 10 % in a round | `revert-rate` | warn | gated inbox `updateClusterBalance` rows from the updater inside the latest closed window / (those + successful updater txs); `StaleUpdate` reverts for a cluster another sender already updated to the same EB for that root are benign and excluded; `IncorrectClusterState` reverts are listed for M1 (`evidence.m1_handoff`; M1 raises its own incidents from the inbox) |
| weekly full-state leaf comparison | `state-mismatch` | warn | every `full_state_interval_s`, once the latest round's window has closed (the updater had its pass: a mismatch is then older than one round), the same comparison over all such clusters; API unreachable, no leaf at the chain's root, or an empty inventory: DEFERRED (health message, retried every run, the open incident kept) |

`updateClusterBalance` from other senders -> info is the Monitor's (CP1A template). The senders of
`ClusterBalanceUpdated` transactions are cached in `m11_senders` (tx hash -> sender, block); reorg rule: rows above
the ancestor are deleted in the reorg run and re-read from the new chain. The persisted proof results
(`ctx.set_state`) carry the commit block and the evaluated block; they are discarded when that commit is no longer
the stored one or a reorg run's ancestor is below the evaluated block (recomputed on the new chain). Episodes: m9_oracle_common (`m11_episodes`).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from checks.base import Check, Ctx, Every, Finding, OnEvents, OnInbox
from checks.m9_oracle_common import (
    FIRST,
    UPDATE_CLUSTER_BALANCE_SELECTOR,
    Episode,
    EpisodeBook,
    blocks_for,
    book_schema,
    inbox_enabled,
    inbox_gate_pending,
    inbox_rows,
    inbox_unavailable,
    short,
)
from checks.registry import register
from eb_storage import cluster_id, keccak, u256
from rpc import RpcError

TABLE = "m11_episodes"
GET_EB = keccak(b"getEffectiveBalance(address,uint64[],(uint32,uint64,uint64,bool,uint256))")[:4]
SCHEMA = book_schema(TABLE) + """;
CREATE TABLE IF NOT EXISTS m11_senders (tx_hash TEXT PRIMARY KEY, block INTEGER NOT NULL, sender TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS m11_senders_block ON m11_senders (block)
"""


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
        "history_rounds": 6,  # closed rounds re-derived per run (bounds the sender lookups)
        "proof_api": "https://ssv-oracle.ssv.network/api/v1",
        "proof_api_timeout_s": 15,
        "proof_api_abort_after": 3,  # consecutive transport failures that make the API "unreachable"
        "full_state_interval_s": 604_800,
        "seconds_per_block": 12,
    }
    schema = SCHEMA

    def __init__(self, config: dict, settings: dict):
        super().__init__(config, settings)
        if not config["updater"] and not settings.get("views"):
            raise SystemExit("checks.m11: set checks.m11.updater or settings.views (the updater is the live "
                             "address of oracle slot updater_slot)")
        if not config["proof_api"]:
            raise SystemExit("checks.m11: proof_api is required (spec: the oracle proof endpoint)")
        self.schedule = (Every(config["interval_s"]), OnEvents({"RootCommitted"}), OnInbox())
        self.book = EpisodeBook(self.id, TABLE)
        self.window = blocks_for(config["update_window_s"], config["seconds_per_block"])
        self.http_get = http_get_json

    def prepare(self, ctx: Ctx) -> None:
        if self.config["updater"]:
            self.updater = self.config["updater"].lower()
        else:
            addr = ctx.params.oracles.get(self.config["updater_slot"])
            if not addr or int(addr, 16) == 0:
                raise RuntimeError(f"oracle slot {self.config['updater_slot']} is empty: no updater address")
            self.updater = addr.lower()

    def updater_at(self, block: int) -> str:
        """The updater of a transaction in `block`: the configured address, or the slot's address at that block from
        the stored OracleReplaced history (current address when the slot was never rotated in the history), so a
        round before a rotation is judged by the key of its time."""
        if self.config["updater"] or not self.rotations:
            return self.updater
        before = [ev for ev in self.rotations if ev.block < block]
        return before[-1].extra["new"] if before else self.rotations[0].extra["old"]

    # ---- run

    def run(self, ctx: Ctx, block: int) -> list[Finding]:
        for _ in ctx.events():
            pass
        for _ in ctx.inbox:
            pass
        if ctx.reorg_ancestor is not None:
            ctx.db.conn.execute("DELETE FROM m11_senders WHERE block>?", (ctx.reorg_ancestor,))
        commits = list(ctx.db.events_after(FIRST, block, frozenset({"RootCommitted"})))
        self.rotations = [ev for ev in ctx.db.events_after(FIRST, block, frozenset({"OracleReplaced"}))
                          if ev.extra["oracleId"] == self.config["updater_slot"]]
        closed = [c for c in commits if c.block + self.window <= block][-self.config["history_rounds"]:]
        rounds = [self.round_stats(ctx, c) for c in closed]
        episodes = self.liveness(ctx, rounds, commits, block)
        unknown: set[str] = set()
        if not inbox_enabled(ctx):
            ctx.health("m11-inbox", "M11 revert-rate rule not evaluated: the failed-transaction inbox is not configured "
                       "(inbox.path).", dedup="m11-inbox-disabled")
        elif inbox_unavailable(ctx) or inbox_gate_pending(ctx, UPDATE_CLUSTER_BALANCE_SELECTOR, "updateClusterBalance"):
            unknown.add("revert-rate")
        else:
            episodes += self.revert_rate(ctx, rounds)
        full, full_unknown = self.full_state(ctx, commits, block)
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

    def round_stats(self, ctx: Ctx, commit) -> dict:
        end = commit.block + self.window
        events = [ev for ev in ctx.db.events_after((commit.block - 1, 1 << 62), end, frozenset({"ClusterBalanceUpdated"}))
                  if ev.ref_block == commit.ref_block]
        senders = self.senders(ctx, events)
        ours = sorted({ev.tx_hash for ev in events if senders[ev.tx_hash] == self.updater_at(ev.block)})
        others = sorted({ev.tx_hash for ev in events if senders[ev.tx_hash] != self.updater_at(ev.block)})
        return {"commit": commit, "end": end, "events": events, "senders": senders, "ours": ours, "others": others}

    def liveness(self, ctx: Ctx, rounds: list[dict], commits, block: int) -> list[Episode]:
        out = []
        run: list[dict] = []  # current run of zero rounds
        for r in rounds + [None]:
            if r is not None and not r["ours"]:
                run.append(r)
                continue
            if run:
                out.append(self._zero(ctx, run, active=r is None, healed=r, commits=commits, block=block))
                run = []
        return out

    def _zero(self, ctx: Ctx, run: list[dict], active: bool, healed, commits, block) -> Episode:
        last = run[-1]["commit"]
        refs = [r["commit"].ref_block for r in run]
        sev, proof_note, evidence = "warn", "", {}
        if len(run) >= self.config["zero_rounds_page"]:
            sev = "page"
        if active:
            result = self.zero_round_proof(ctx, last, block)
            if result.get("error"):
                proof_note = f" Proof API check not possible: {result['error']} (retried every run)."
            elif result["mismatches"]:
                sev = "page"
                proof_note = (f" Proof API: {len(result['mismatches'])} of {result['checked']} clusters differ from "
                              f"their leaf: {fmt_mismatches(result['mismatches'])}.")
            else:
                proof_note = f" Proof API: all {result['checked']} checked clusters match their leaf."
            evidence["proof"] = result
        others = sorted({tx for r in run for tx in r["others"]})
        return Episode(
            key="liveness", episode=f"liveness:{run[0]['commit'].ref_block}", active=active,
            evidence_block=run[0]["end"], severity=sev,
            title=f"EB updater sent 0 updates for {len(run)} committed round(s)",
            body=(f"No updateClusterBalance from the updater {self.updater_at(last.block)} within "
                  f"{self.config['update_window_s'] // 60} min of RootCommitted for blockNum {', '.join(map(str, refs))} "
                  f"(last committed at block {last.block}). The updater keeps lastProcessedBlock in memory: no backfill "
                  f"until the next root." + (f" Other senders updated in these windows ({len(others)} tx)." if others
                                             else "") + proof_note),
            subject={"updater": self.updater_at(last.block)}, revision=str(last.ref_block),
            evidence={"zero_rounds": refs, "commit_block": last.block, "other_sender_txs": others[:20], **evidence},
            resolution=(f"The updater sent {len(healed['ours'])} update(s) for blockNum {healed['commit'].ref_block}."
                        if healed else ""))

    def revert_rate(self, ctx: Ctx, rounds: list[dict]) -> list[Episode]:
        rows = [r for r in inbox_rows(ctx, UPDATE_CLUSTER_BALANCE_SELECTOR, "updateClusterBalance")
                if (r["sender"] or "").lower() == self.updater_at(r["block"])]
        out = []
        for i, rd in enumerate(rounds):
            c = rd["commit"]
            reverts = [r for r in rows if c.block <= r["block"] <= rd["end"]]
            if not reverts:
                continue
            benign, counted = [], []
            for r in reverts:
                (benign if self.benign(ctx, r, rd) else counted).append(r)
            total = len(rd["ours"]) + len(counted)
            rate = len(counted) / total if total else 0.0
            if rate <= self.config["revert_rate"]:
                continue
            m1 = [r["tx_hash"] for r in counted if r["revert_name"] == "IncorrectClusterState"]
            names: dict[str, int] = {}
            for r in counted:
                names[r["revert_name"] or r["revert_selector"] or "unresolved"] = names.get(
                    r["revert_name"] or r["revert_selector"] or "unresolved", 0) + 1
            latest = i == len(rounds) - 1
            out.append(Episode(
                key="revert-rate", episode=f"revert-rate:{c.ref_block}", active=latest, evidence_block=rd["end"],
                severity="warn",
                title=f"EB updater revert rate {rate:.0%} for blockNum {c.ref_block}",
                body=(f"{len(counted)} of {total} updateClusterBalance txs from {short(self.updater_at(c.block))} reverted in the "
                      f"window of blockNum {c.ref_block} (threshold {self.config['revert_rate']:.0%}): "
                      + ", ".join(f"{n} x{k}" for n, k in sorted(names.items()))
                      + (f". {len(benign)} benign StaleUpdate revert(s) (another sender set the same EB) excluded"
                         if benign else "")
                      + (f". IncorrectClusterState reverts go to M1 (stale cluster tuple): {', '.join(m1[:5])}"
                         if m1 else "") + "."),
                subject={"updater": self.updater_at(c.block), "ref_block": c.ref_block}, revision=str(c.ref_block),
                evidence={"reverted": [r["tx_hash"] for r in counted][:50], "benign": [r["tx_hash"] for r in benign][:50],
                          "successful": len(rd["ours"]), "m1_handoff": m1, "reasons": names},
                resolution="The updater's revert rate is back under the threshold."))
        if out and not out[-1].active:
            out[-1].resolution = (f"Revert rate for blockNum {rounds[-1]['commit'].ref_block} is under "
                                  f"{self.config['revert_rate']:.0%}.")
        return out

    def benign(self, ctx: Ctx, row, rd: dict) -> bool:
        """StaleUpdate because another sender already set the same EB for the same root (spec: benign)."""
        if row["revert_name"] != "StaleUpdate":
            return False
        tx = ctx.rpc.transaction(row["tx_hash"])
        call = decode_update_input((tx or {}).get("input", "0x"))
        if call is None or call["ref"] != rd["commit"].ref_block:
            return False
        return any(ev.cluster_id and ev.cluster_id.hex() == call["cluster_id"] and ev.eb == call["eb"]
                   and rd["senders"][ev.tx_hash] != self.updater_at(ev.block) for ev in rd["events"])

    # ---- proof API

    def zero_round_proof(self, ctx: Ctx, commit, block: int) -> dict:
        saved = fresh(ctx, ctx.state("zero_proof"))
        if saved and saved["ref"] == commit.ref_block and saved["commit_block"] == commit.block:
            return saved["result"]
        result = self.compare_leaves(ctx, commit.ref_block, block)
        if not result.get("error"):
            ctx.set_state("zero_proof", {"ref": commit.ref_block, "commit_block": commit.block, "block": block,
                                         "result": result})
        else:
            ctx.health("proof-api", f"M11: oracle proof API unavailable ({result['error']}); the zero-update round "
                       f"{commit.ref_block} cannot be checked for EB mismatches (retried every run).",
                       dedup=f"proof-api:{commit.ref_block}")
        return result

    def full_state(self, ctx: Ctx, commits, block: int) -> tuple[list[Episode], bool]:
        saved = fresh(ctx, ctx.state("full_state"))
        stored = {c.block: c for c in commits}
        if saved and (saved["commit_block"] not in stored or stored[saved["commit_block"]].ref_block != saved["ref"]):
            saved = None  # its commit left the canonical chain
        latest = next((c for c in reversed(commits) if c.block + self.window <= block), None)
        if latest is not None and (saved is None or ctx.clock() - saved["at"] >= self.config["full_state_interval_s"]):
            result = self.compare_leaves(ctx, latest.ref_block, block)
            if result.get("error"):
                ctx.health("proof-api-weekly", f"M11 weekly full-state leaf comparison DEFERRED: oracle proof API "
                           f"unreachable ({result['error']}); retried every run.", dedup="proof-api-weekly")
                return [], True
            saved = {"ref": latest.ref_block, "commit_block": latest.block, "block": block, "at": ctx.clock(),
                     "result": result}
            ctx.set_state("full_state", saved)
        if saved is None:
            return [], False
        res = saved["result"]
        return [Episode(
            key="state-mismatch", episode=f"state-mismatch:{saved['ref']}", active=bool(res["mismatches"]),
            occurred=bool(res["mismatches"]),
            evidence_block=saved["commit_block"], severity="warn",
            title=f"{len(res['mismatches'])} cluster(s) with on-chain EB != leaf (blockNum {saved['ref']})",
            body=(f"Full-state comparison at blockNum {saved['ref']} (after its update window): "
                  f"{len(res['mismatches'])} of {res['checked']} clusters differ from their leaf: "
                  f"{fmt_mismatches(res['mismatches'])}; {len(res['unknown'])} not comparable."),
            subject={"ref_block": saved["ref"]}, revision=str(saved["ref"]),
            evidence={"mismatches": res["mismatches"][:50], "unknown": res["unknown"][:50], "checked": res["checked"]},
            resolution=f"Full-state comparison at blockNum {saved['ref']}: every checked cluster matches its leaf.")], False

    def compare_leaves(self, ctx: Ctx, ref: int, block: int) -> dict:
        """Leaf EB (proof API, at blockNum `ref`) vs getEffectiveBalance at `block` for every active ETH cluster with
        validators. {"checked", "mismatches", "unknown", "error"}; `error` set when the API is unreachable."""
        inv = ctx.inventory(block)
        clusters = [ci for _, ci in sorted(inv.clusters.items()) if ci.valid and ci.version == "ETH"
                    and ci.cluster.active and ci.cluster.validator_count > 0]
        if not clusters:  # an empty inventory is a missing input, never "every cluster matches"
            return {"checked": 0, "mismatches": [], "unknown": [], "error": "no active ETH cluster in the inventory"}
        base = self.config["proof_api"].rstrip("/")
        leaves, unknown, failures = {}, [], 0
        for ci in clusters:
            try:
                body = self.http_get(f"{base}/proof/{ci.cluster_id}", self.config["proof_api_timeout_s"])
                failures = 0
            except ProofApiError as exc:
                failures += 1
                unknown.append({"cluster": ci.cluster_id, "reason": str(exc)})
                if failures >= self.config["proof_api_abort_after"] and not leaves:
                    return {"checked": 0, "mismatches": [], "unknown": unknown, "error": str(exc)}
                continue
            if not isinstance(body, dict) or "effectiveBalance" not in body:
                unknown.append({"cluster": ci.cluster_id, "reason": str(body.get("error") if isinstance(body, dict)
                                                                     else "unexpected shape")[:80]})
                continue
            if int(body.get("referenceBlock", -1)) != ref:
                unknown.append({"cluster": ci.cluster_id, "reason": f"API at referenceBlock {body.get('referenceBlock')}"})
                continue
            leaves[ci.cluster_id] = (ci, int(body["effectiveBalance"]))
        if not leaves:
            return {"checked": 0, "mismatches": [], "unknown": unknown,
                    "error": f"no leaf at blockNum {ref} for any of {len(clusters)} clusters"}
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
        return {"checked": len(items), "mismatches": mismatches, "unknown": unknown, "error": None}


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


def fmt_mismatches(ms: list[dict]) -> str:
    return ", ".join(f"{m['cluster'][:12]} chain {m['onchain_eb']} leaf {m['leaf_eb']}" for m in ms[:5]) + (
        f" (+{len(ms) - 5})" if len(ms) > 5 else "")
