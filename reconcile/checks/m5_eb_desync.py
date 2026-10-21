"""M5 — EB desync via register/remove around a root (#94775 / SSV-4), ported onto the framework.

Same detection and incident semantics as the pre-framework worker (plans/94775-eb-collapse-monitoring.md):
for each cluster, replay the validator changes since its last consumed root and compare with
`clusterEB` read at the same block; classify clean / latent / confirmed / unknown (eb_model.py).

Incident semantics (unchanged; the pre-framework severities map critical -> page, warning -> warn):
  confirmed          page (warn while liquidated); repeated root, grown shortfall or reactivation re-send
  candidate          warn: add -> consumed root -> remove matched, accounting check UNKNOWN
  latent             info, tracked silently (daily digest only)
  clean/overbilled/… resolves the open incident (resolution sent for confirmed and candidate)
  unknown            keeps any open incident open; a monitor-health message names the clusters
A storage-layout drift against the pin suspends the check with a health message.

M5-specific bookkeeping (shortfall, root blocks, kind) lives in `m5_incidents` and `evaluations`;
the framework's `incidents` row shares the incident id.
"""

from __future__ import annotations

import json
from collections import Counter

import eb_model as M
from alerts import SEVERITY_FROM_LEGACY
from checks.base import Check, Ctx, Every, Finding, OnEvents
from checks.registry import register
from eb_events import CLUSTER_KINDS, Event
from eb_storage import (
    IMPLEMENTATION_SLOT,
    cluster_eb_slot,
    eth_cluster_slot,
    keccak,
    module_slot,
    ssv_cluster_slot,
    u256,
    vunits_to_eb,
    vunits_to_eth_str,
)
from rpc import RpcError

GET_EB_SELECTOR = keccak(b"getEffectiveBalance(address,uint64[],(uint32,uint64,uint64,bool,uint256))")[:4]

SCHEMA = """
CREATE TABLE IF NOT EXISTS evaluations (
  cluster_id TEXT PRIMARY KEY, block INTEGER NOT NULL, status TEXT NOT NULL, ref_block INTEGER,
  reference_vunits INTEGER, observed_vunits INTEGER, billed_vunits INTEGER, discrepancy_vunits INTEGER,
  matched_keys INTEGER, active INTEGER, validator_count INTEGER, reasons TEXT
);
CREATE TABLE IF NOT EXISTS m5_incidents (
  id TEXT PRIMARY KEY, cluster_id TEXT NOT NULL, kind TEXT NOT NULL, severity TEXT NOT NULL,
  state TEXT NOT NULL, first_ref_block INTEGER NOT NULL, last_ref_block INTEGER NOT NULL,
  first_seen_block INTEGER NOT NULL, last_seen_block INTEGER NOT NULL,
  shortfall_vunits INTEGER NOT NULL, max_shortfall_vunits INTEGER NOT NULL,
  resolved_block INTEGER, evidence TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS m5_incidents_cluster ON m5_incidents (cluster_id, state);
"""

TITLES = {
    "confirmed": "#94775 EB collapse confirmed",
    "candidate": "#94775 candidate sequence (accounting check pending)",
    "latent": "#94775 latent overwritten baseline (not a confirmed collapse)",
}


@register
class M5EbDesync(Check):
    id = "m5"
    defaults = {"sweep_interval_s": 300, "full_sweep_interval_s": 3600, "layout": {}, "pr614_guards_from": [0, 0]}
    legacy_keys = ("sweep_interval_s", "full_sweep_interval_s", "layout", "pr614_guards_from")
    schema = SCHEMA

    def __init__(self, config: dict, settings: dict):
        super().__init__(config, settings)
        self.schedule = (Every(config["sweep_interval_s"]), OnEvents(CLUSTER_KINDS))
        self.proxy = settings["proxy"].lower()
        self.views = settings.get("views")
        self.start_block = settings["start_block"]
        self.history_complete = settings["start_block"] <= settings["activation_block"]
        self.guards_from = tuple(config["pr614_guards_from"])
        self.explorer = settings.get("alerts", {}).get("explorer", "https://etherscan.io")
        self.last_sweep = 0.0
        self.last_full_sweep = 0.0
        self.layout_ok: bool | None = None
        self.dirty: set[str] = set()
        self.last_evals: list[M.Evaluation] = []
        self.last_full_evals: list[M.Evaluation] = []

    def on_reorg(self, ancestor: int) -> None:
        self.dirty.add("*")

    # ---- run

    def run(self, ctx: Ctx, block: int) -> list[Finding]:
        for ev in ctx.events():
            if ev.cluster_id:
                self.dirty.add(ev.cluster_id.hex())
        if "*" in self.dirty or "reorg" in ctx.reasons:
            self.dirty.discard("*")
            self.dirty.update(ctx.db.clusters(explicit_only=False))
        now = ctx.clock()
        if block < self.start_block:
            return []
        force = "force" in ctx.reasons
        periodic = force or now - self.last_sweep >= self.config["sweep_interval_s"]
        full = force or now - self.last_full_sweep >= self.config["full_sweep_interval_s"]
        if not (periodic or full or self.dirty):
            return []

        problems = self.verify_layout(ctx.rpc, ctx.db, block, deep=self.layout_ok is None)
        if problems:
            self.layout_ok = False
            self.last_evals = []
            ctx.health("layout", "Storage layout guard failed; reconciliation suspended:\n- " + "\n- ".join(problems),
                       dedup="layout:" + "|".join(problems))
            return []
        if self.layout_ok is False:
            ctx.health("layout-ok", "Storage layout guard passes again; reconciliation resumed.",
                       dedup=f"layout-ok:{block}")
        self.layout_ok = True

        if full:
            cids = ctx.db.clusters(explicit_only=False)
        elif periodic:
            cids = ctx.db.clusters(explicit_only=True) + list(self.dirty)
        else:
            cids = list(self.dirty)
        evals = self.evaluate(ctx.rpc, ctx.db, cids, block)
        self.dirty.clear()
        self.last_sweep = now
        if full:
            self.last_full_sweep = now
            self.last_full_evals = evals
        self.last_evals = evals
        return self.record(ctx, evals, block, full)

    # ---- layout guard

    def verify_layout(self, chain, db, block: int, deep: bool = False) -> list[str]:
        problems = []
        layout = self.config.get("layout") or {}
        expected_impl = layout.get("implementation")
        modules = layout.get("modules", {})
        slots = [IMPLEMENTATION_SLOT] + [module_slot(m) for m in modules]
        words = chain.storage(self.proxy, slots, block)
        impl = "0x%040x" % words[0]
        if expected_impl and impl != expected_impl.lower():
            problems.append(f"implementation {impl} != pinned {expected_impl.lower()}")
        for (name, spec), word in zip(modules.items(), words[1:]):
            addr = "0x%040x" % word
            if addr != spec["address"].lower():
                problems.append(f"{name} module {addr} != pinned {spec['address'].lower()}")
            elif deep and spec.get("codehash"):
                h = "0x" + keccak(chain.code(addr, block)).hex()
                if h != spec["codehash"].lower():
                    problems.append(f"{name} codehash {h} != pinned {spec['codehash'].lower()}")
        if deep and self.views:
            problems.extend(self._cross_check_views(chain, db, block))
        return problems

    def _cross_check_views(self, chain, db, block: int, sample: int = 5) -> list[str]:
        """Compare the raw-slot billing value with SSVNetworkViews.getEffectiveBalance for a few clusters."""
        problems = []
        checked = 0
        for cid in db.clusters(explicit_only=True):
            events = db.cluster_events(cid, block)
            ev = self.evaluate(chain, db, [cid], block)[0]
            if ev.status not in (M.CLEAN, M.LATENT, M.CONFIRMED, M.OVERBILLED) or not ev.active:
                continue
            latest = events[-1]
            ops = latest.operator_ids
            data = (
                GET_EB_SELECTOR
                + bytes.fromhex(latest.owner[2:]).rjust(32, b"\0")
                + u256(7 * 32)
                + b"".join(u256(int(x)) for x in latest.cluster.as_tuple())
                + u256(len(ops))
                + b"".join(u256(o) for o in ops)
            )
            try:
                res = chain.call(self.views, data, block)
            except RpcError as exc:
                problems.append(f"getEffectiveBalance call failed: {exc}")
                break
            got = int.from_bytes(res, "big")
            want = vunits_to_eb(ev.billed_vunits)
            if got != want:
                problems.append(f"cluster {cid[:10]}: views EB {got} != raw-slot EB {want}")
            checked += 1
            if checked >= sample:
                break
        return problems

    # ---- evaluation

    def evaluate(self, chain, db, cids: list[str], block: int) -> list[M.Evaluation]:
        cids = sorted(set(cids))
        slots = []
        for cid in cids:
            cb = bytes.fromhex(cid)
            slots += [cluster_eb_slot(cb), eth_cluster_slot(cb), ssv_cluster_slot(cb)]
        words = chain.storage(self.proxy, slots, block) if slots else []
        out = []
        for i, cid in enumerate(cids):
            w = words[3 * i : 3 * i + 3]
            storage = M.StorageView(block, w[0], u256(w[1]), u256(w[2]))
            events = db.cluster_events(cid, block)
            if events:
                out.append(M.evaluate(events, storage, self.start_block, self.history_complete, self.guards_from))
        return out

    # ---- incidents

    def record(self, ctx: Ctx, evals: list[M.Evaluation], block: int, full: bool) -> list[Finding]:
        findings: list[Finding] = []
        for ev in evals:
            self._store_evaluation(ctx.db, ev)
            findings += self._incident(ctx, ev, block)
        counts = Counter(e.status for e in evals)
        unknown = [e for e in evals if e.status == M.UNKNOWN]
        ctx.set_state("last_sweep", {"block": block, "at": ctx.clock(), "full": full, "evaluated": len(evals),
                                     "counts": dict(counts)})
        if unknown:
            sample = "\n".join(f"- {e.cluster_id.hex()[:10]}…: {e.reasons[0]}" for e in unknown[:5])
            ctx.health(
                "unknown",
                f"{len(unknown)} cluster(s) could not be reconciled at block {block}; they are NOT "
                f"treated as clean.\n{sample}",
                dedup="unknown:" + ",".join(sorted(e.cluster_id.hex()[:10] for e in unknown)),
                realert=True,
            )
        ctx.log.info("m5 evaluated %d clusters at %d: %s", len(evals), block, dict(counts))
        return findings

    def _store_evaluation(self, db, ev: M.Evaluation) -> None:
        db.conn.execute(
            "INSERT OR REPLACE INTO evaluations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (ev.cluster_id.hex(), ev.block, ev.status, ev.ref_block, ev.reference_vunits, ev.observed_vunits,
             ev.billed_vunits, ev.discrepancy_vunits, len(ev.matched_keys), int(bool(ev.active)),
             ev.validator_count, json.dumps(ev.reasons)),
        )

    def _finding(self, cid: str, legacy_severity: str, kind: str, body: str, ev: M.Evaluation, *,
                 title: str | None = None, revision: str | None = None, incident_id: str | None = None,
                 resolve: bool = False) -> Finding:
        return Finding(
            check=self.id, key=cid, severity=SEVERITY_FROM_LEGACY[legacy_severity], title=title or TITLES[kind],
            body=body, subject={"cluster_id": "0x" + cid, "owner": ev.owner, "operator_ids": list(ev.operator_ids)},
            evidence=self._evidence(ev), resolve=resolve, notify=kind != "latent", revision=revision,
            incident_id=incident_id,
        )

    def _new_id(self, db, cid: str, ref_block: int | None) -> str:
        base = f"EB94775-{cid[:10]}-{ref_block}"
        inc_id, n = base, 1
        while db.conn.execute("SELECT 1 FROM m5_incidents WHERE id=? UNION SELECT 1 FROM incidents WHERE id=?",
                              (inc_id, inc_id)).fetchone():
            n += 1
            inc_id = f"{base}-{n}"
        return inc_id

    def _incident(self, ctx: Ctx, ev: M.Evaluation, block: int) -> list[Finding]:
        db = ctx.db
        cid = ev.cluster_id.hex()
        now = ctx.clock()
        row = db.conn.execute(
            "SELECT * FROM m5_incidents WHERE cluster_id=? AND state='open' ORDER BY created_at DESC LIMIT 1", (cid,)
        ).fetchone()

        if ev.status == M.CONFIRMED:
            severity = "critical" if ev.active else "warning"
            evidence = json.dumps(self._evidence(ev))
            if row is None or row["kind"] != "confirmed":
                if row is not None:  # latent/candidate escalates into the same incident id
                    inc_id = row["id"]
                    db.conn.execute(
                        "UPDATE m5_incidents SET kind='confirmed', severity=?, last_ref_block=?, last_seen_block=?, "
                        "shortfall_vunits=?, max_shortfall_vunits=MAX(max_shortfall_vunits, ?), evidence=?, "
                        "updated_at=? WHERE id=?",
                        (severity, ev.ref_block, block, ev.shortfall_vunits, ev.shortfall_vunits, evidence, now, inc_id),
                    )
                else:
                    inc_id = self._new_id(db, cid, ev.ref_block)
                    self._open(db, now, inc_id, cid, "confirmed", severity, ev, block, evidence)
                return [self._finding(cid, severity, "confirmed", self._msg_confirmed(ctx, ev, block), ev,
                                      revision=f"confirmed:{ev.ref_block}", incident_id=inc_id)]
            inc_id = row["id"]
            repeated = ev.ref_block != row["last_ref_block"]
            grew = ev.shortfall_vunits > row["max_shortfall_vunits"]
            db.conn.execute(
                "UPDATE m5_incidents SET severity=?, last_ref_block=?, last_seen_block=?, shortfall_vunits=?, "
                "max_shortfall_vunits=MAX(max_shortfall_vunits, ?), evidence=?, updated_at=? WHERE id=?",
                (severity, ev.ref_block, block, ev.shortfall_vunits, ev.shortfall_vunits, evidence, now, inc_id),
            )
            escalated = severity == "critical" and row["severity"] != "critical"
            if repeated or grew or escalated:
                why = (f"repeated at root S={ev.ref_block}" if repeated else "shortfall grew" if grew
                       else "cluster reactivated; the collapsed EB is billed again")
                return [self._finding(cid, severity, "confirmed", self._msg_confirmed(ctx, ev, block), ev,
                                      title="#94775 incident update: " + why,
                                      revision=f"update:{ev.ref_block}:{ev.shortfall_vunits}:"
                                               f"{SEVERITY_FROM_LEGACY[severity]}", incident_id=inc_id)]
            return [self._finding(cid, severity, "confirmed", self._msg_confirmed(ctx, ev, block, sender=False), ev,
                                  incident_id=inc_id)]

        if ev.status == M.UNKNOWN and ev.matched_keys:
            # Fast-path candidate: the add -> consumed root -> remove sequence exists but the
            # accounting check could not run. Mark pending; monitor-health covers the cause.
            if row is None:
                inc_id = self._new_id(db, cid, ev.ref_block)
                self._open(db, now, inc_id, cid, "candidate", "warning", ev, block, json.dumps(self._evidence(ev)))
                return [self._finding(cid, "warning", "candidate", self._msg_candidate(ev, block), ev,
                                      revision=f"candidate:{ev.ref_block}", incident_id=inc_id)]
            return []

        if ev.status == M.LATENT:
            if row is None:
                inc_id = self._new_id(db, cid, ev.ref_block)
                self._open(db, now, inc_id, cid, "latent", "info", ev, block, json.dumps(self._evidence(ev)))
                return [self._finding(cid, "info", "latent", self._msg_latent(ev, block), ev, incident_id=inc_id)]
            db.conn.execute(
                "UPDATE m5_incidents SET last_ref_block=?, last_seen_block=?, shortfall_vunits=?, "
                "max_shortfall_vunits=MAX(max_shortfall_vunits, ?), updated_at=? WHERE id=?",
                (ev.ref_block, block, ev.shortfall_vunits, ev.shortfall_vunits, now, row["id"]),
            )
            return [self._finding(cid, row["severity"], row["kind"], self._msg_latent(ev, block), ev,
                                  title=TITLES[row["kind"]], incident_id=row["id"])]

        if ev.status == M.UNKNOWN or row is None:
            return []  # keep any open incident open while inputs are incomplete

        # Discrepancy closed (clean / overbilled / implicit / reset / not ETH): resolve.
        db.conn.execute(
            "UPDATE m5_incidents SET state='resolved', resolved_block=?, last_seen_block=?, shortfall_vunits=0, "
            "updated_at=? WHERE id=?",
            (block, block, now, row["id"]),
        )
        return [self._finding(cid, row["severity"], row["kind"], self._msg_resolved(row, ev, block), ev,
                              title="#94775 incident resolved", resolve=True)]

    def _open(self, db, now, inc_id, cid, kind, severity, ev, block, evidence) -> None:
        db.conn.execute(
            "INSERT INTO m5_incidents VALUES (?,?,?,?, 'open', ?,?,?,?,?,?, NULL, ?, ?, ?)",
            (inc_id, cid, kind, severity, ev.ref_block or 0, ev.ref_block or 0, block, block,
             ev.shortfall_vunits, ev.shortfall_vunits, evidence, now, now),
        )

    def _evidence(self, ev: M.Evaluation) -> dict:
        u = ev.update
        return {
            "cluster_id": "0x" + ev.cluster_id.hex(), "owner": ev.owner, "operator_ids": list(ev.operator_ids),
            "block": ev.block, "status": ev.status, "active": ev.active, "validator_count": ev.validator_count,
            "ref_block": ev.ref_block, "eb_at_ref": ev.eb_at_ref,
            "update": {"tx": u.tx_hash, "block": u.block, "log_index": u.log_index} if u else None,
            "reference_vunits": ev.reference_vunits, "observed_vunits": ev.observed_vunits,
            "billed_vunits": ev.billed_vunits, "discrepancy_vunits": ev.discrepancy_vunits,
            "matched_keys": len(ev.matched_keys),
            "add_txs": sorted({e.tx_hash for e in ev.adds_in_window}),
            "remove_txs": sorted({e.tx_hash for e in ev.removes_after_update}),
            "empty_shares_adds": sum(1 for e in ev.adds_in_window if e.shares_len == 0),
            "reasons": ev.reasons,
        }

    # ---- messages (plain text; Telegram parse_mode is not used, so no escaping)

    def _tx(self, h: str) -> str:
        return f"{self.explorer}/tx/{h}"

    def _cluster_lines(self, ev: M.Evaluation) -> list[str]:
        return [
            f"Cluster: 0x{ev.cluster_id.hex()}",
            f"Owner: {ev.owner}",
            f"Operators: {list(ev.operator_ids)}",
            f"Validators now: {ev.validator_count} · active: {ev.active} · version: {ev.version}",
        ]

    def _sender(self, ctx: Ctx, tx_hash: str) -> str:
        try:
            tx = ctx.rpc.transaction(tx_hash)
            return tx["from"] if tx else "?"
        except RpcError:
            return "?"

    def _msg_confirmed(self, ctx: Ctx, ev: M.Evaluation, block: int, sender: bool = True) -> str:
        u = ev.update
        lines = self._cluster_lines(ev)
        lines += [
            f"Root reference block S: {ev.ref_block} · consumed in block {u.block} with E = {ev.eb_at_ref} ETH",
            f"Update sent by: {self._sender(ctx, u.tx_hash) if sender else '(see first alert)'} "
            "(clue only; updates are permissionless)",
            f"Keys added after S and before the update: {len({e.pubkey for e in ev.adds_in_window})} "
            f"({sum(1 for e in ev.adds_in_window if e.shares_len == 0)} with empty shares)",
            f"Of those, removed after the update: {len(ev.matched_keys)}",
            f"Reference-adjusted: {ev.reference_vunits:,} vUnits ({vunits_to_eth_str(ev.reference_vunits)} ETH)",
            f"Stored (billed): {ev.observed_vunits:,} vUnits ({vunits_to_eth_str(ev.billed_vunits)} ETH)",
            f"Shortfall: {ev.shortfall_vunits:,} vUnits ({vunits_to_eth_str(ev.shortfall_vunits)} ETH of fee weight)",
            f"Evaluated at finalized block {block}",
        ]
        if not ev.active:
            lines.append("Cluster is liquidated: not billed now; the shortfall applies again on reactivation.")
        lines.append("Txs:")
        lines += [f"  register {self._tx(h)}" for h in sorted({e.tx_hash for e in ev.adds_in_window})[:3]]
        lines.append(f"  update   {self._tx(u.tx_hash)}")
        lines += [f"  remove   {self._tx(h)}" for h in sorted({e.tx_hash for e in ev.removes_after_update})[:3]]
        return "\n".join(lines)

    def _msg_candidate(self, ev: M.Evaluation, block: int) -> str:
        lines = self._cluster_lines(ev)
        lines += [
            f"Root reference block S: {ev.ref_block}; update tx {self._tx(ev.update.tx_hash) if ev.update else '?'}",
            f"Keys added after S, removed after the update: {len(ev.matched_keys)}",
            f"Accounting result: UNKNOWN at block {block} ({'; '.join(ev.reasons[:2])})",
        ]
        return "\n".join(lines)

    def _msg_latent(self, ev: M.Evaluation, block: int) -> str:
        return "\n".join(self._cluster_lines(ev) + [
            f"Root reference block S: {ev.ref_block}; added keys still registered",
            f"Overwritten baseline: {ev.shortfall_vunits:,} vUnits ({vunits_to_eth_str(ev.shortfall_vunits)} ETH) "
            f"at block {block}",
        ])

    def _msg_resolved(self, row, ev: M.Evaluation, block: int) -> str:
        return "\n".join([
            f"Cluster: 0x{row['cluster_id']}",
            f"Discrepancy closed at block {block}: status {ev.status}, last consumed root S={ev.ref_block}",
            f"Max shortfall seen: {row['max_shortfall_vunits']:,} vUnits "
            f"({vunits_to_eth_str(row['max_shortfall_vunits'])} ETH) from block {row['first_seen_block']} to {block}",
        ])

    # ---- digest

    def digest(self, ctx: Ctx) -> list[str]:
        """Non-paging states of the last full sweep (latent candidates, overbilling, implicit resets)."""
        evals = self.last_full_evals
        if not evals:
            return []
        counts = Counter(e.status for e in evals)
        lines = [f"Block {evals[0].block}: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))]
        latent = sorted((e for e in evals if e.status == M.LATENT), key=lambda e: -e.shortfall_vunits)
        if latent:
            lines.append("Largest latent overwritten baselines (added keys still registered; not a confirmed #94775):")
            lines += [f"- 0x{e.cluster_id.hex()[:10]}… S={e.ref_block} {e.shortfall_vunits:,} vUnits "
                      f"({vunits_to_eth_str(e.shortfall_vunits)} ETH)" for e in latent[:5]]
        return lines


# --------------------------------------------------------------------------- audit


def audit(db, start_block: int) -> dict:
    """Event-only history: every consumed root window with post-S additions, and matched removals."""
    windows = []
    for cid in db.clusters(explicit_only=True):
        events: list[Event] = db.cluster_events(cid, 1 << 62)
        updates = [e for e in events if e.kind == "ClusterBalanceUpdated"]
        for i, u in enumerate(updates):
            nxt = updates[i + 1].pos if i + 1 < len(updates) else (1 << 62, 0)
            adds = [e for e in events if e.kind == "ValidatorAdded" and e.block > u.ref_block and e.pos < u.pos]
            rems_before = [e for e in events if e.kind == "ValidatorRemoved" and e.block > u.ref_block and e.pos < u.pos]
            if not adds:
                continue
            added = {e.pubkey for e in adds}
            matched = [e for e in events if e.kind == "ValidatorRemoved" and u.pos < e.pos < nxt and e.pubkey in added]
            windows.append({
                "cluster_id": "0x" + cid, "owner": u.owner, "operator_ids": list(u.operator_ids),
                "ref_block": u.ref_block, "update_block": u.block, "update_tx": u.tx_hash, "eb": u.eb,
                "adds_between_s_and_update": len(adds), "removes_between_s_and_update": len(rems_before),
                "overwritten_baseline_vunits": 10_000 * (len(adds) - len(rems_before)),
                "empty_shares_adds": sum(1 for e in adds if e.shares_len == 0),
                "matched_removals_before_next_update": len({e.pubkey for e in matched}),
                "remove_txs": sorted({e.tx_hash for e in matched})[:5],
                "superseded": i + 1 < len(updates),
            })
    windows.sort(key=lambda w: (-w["matched_removals_before_next_update"], -w["overwritten_baseline_vunits"]))
    kinds = Counter(r[0] for r in db.conn.execute("SELECT kind FROM logs"))
    return {
        "checkpoint": db.meta("checkpoint"), "history_start": start_block, "event_counts": dict(kinds),
        "clusters_seen": len(db.clusters(explicit_only=False)),
        "clusters_with_eb_update": len(db.clusters(explicit_only=True)),
        "windows_with_post_s_adds": len(windows),
        "windows_with_matched_removal": sum(1 for w in windows if w["matched_removals_before_next_update"]),
        "windows": windows,
    }
