"""Findings -> incidents -> outbox (plan §6 "Incident lifecycle" and "Alert routing").

Checks return findings; only this module writes incidents and the outbox. Every function that
writes runs inside the caller's database transaction.

Lifecycle, keyed by (check, key):
  - the first finding opens an incident and sends it (unless `notify=False`: tracked silently);
  - a higher severity escalates under the same id and sends; a lower one is recorded silently;
  - a new `revision` re-sends under the same id (a check-defined material change);
  - `resolve=True` closes it and sends a resolution (info, on the incident's route) for page/warn
    incidents that were sent;
  - absence of a finding never resolves, unless the check declares `resolve_on_absence`, with one exception:
    in a run with reason `reorg`, an open incident last seen above the reorg's common ancestor that the run
    does not report again is resolved as "evidence left the canonical chain" (`resolve_not_refound`);
  - while open, page re-alerts every 6 h and warn every 24 h; info is sent once plus the digest. An incident
    last seen above a pending reorg's ancestor is not re-alerted until the check's reorg run commits.
Restart idempotency: incidents and outbox keys are durable, so an open incident re-found after a
restart produces nothing new before its re-alert is due.
"""

from __future__ import annotations

import json
import logging

from eb_storage import keccak

log = logging.getLogger("eb-reconcile")

SEVERITIES = ("info", "warn", "page")
RANK = {s: i for i, s in enumerate(SEVERITIES)}
SEVERITY_FROM_LEGACY = {"info": "info", "warning": "warn", "critical": "page"}

# D2: page/warn -> the Telegram group; info and digests -> Discord. Monitor-health messages page the
# operators like a warn. Overridable with `alerts.routes`.
DEFAULT_ROUTES = {"page": "telegram", "warn": "telegram", "health": "telegram", "info": "discord", "digest": "discord"}
DEFAULT_REALERT_S = {"page": 6 * 3600, "warn": 24 * 3600}
ICONS = {"page": "🚨", "warn": "⚠️", "info": "ℹ️", "health": "🩺", "digest": "📋", "resolved": "✅"}


def incident_key(check: str, key: str) -> str:
    """The join of check id and finding key that identifies an incident; two checks never share one."""
    return ":".join((check, key))


class Alerts:
    def __init__(self, db, settings: dict, clock, label: str):
        self.db, self.clock, self.label = db, clock, label
        self.routes = {**DEFAULT_ROUTES, **settings.get("routes", {})}
        self.realert_s = {**DEFAULT_REALERT_S, **settings.get("realert_s", {})}
        self.health_realert_s = settings.get("health_realert_s", 21600)
        self.digest_interval_s = settings.get("digest_interval_s", 86400)
        self.name = settings.get("name", "SSV watch")

    # ---- formatting

    def head(self, sev: str, title: str) -> str:
        return f"{ICONS.get(sev, '')} [{sev.upper()}] {title} — {self.label}"

    def _text(self, sev: str, title: str, body: str, inc_id: str | None) -> str:
        parts = [self.head(sev, title)]
        if body:
            parts.append(body)
        if inc_id:
            parts.append(f"Incident: {inc_id}")
        return "\n".join(parts)

    # ---- outbox

    def route(self, severity: str) -> str:
        return self.routes[severity]

    def enqueue(self, key: str, severity: str, text: str, kind: str, check_id: str | None = None,
                incident_id: str | None = None, route: str | None = None, unique: bool = False) -> bool:
        """Queue one message; an existing key de-duplicates it. With `unique` (a send the incident state
        machine already decided, e.g. a block-keyed escalation replayed on a reorganised chain) a taken key
        gets a `#n` suffix instead of being dropped."""
        base, n = key, 1
        while True:
            cur = self.db.conn.execute(
                "INSERT OR IGNORE INTO outbox(key, created_at, severity, text, delivery, check_id, incident_id, kind) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (key, self.clock(), severity, text, route or self.route(severity), check_id, incident_id, kind),
            )
            if cur.rowcount == 1 or not unique:
                return cur.rowcount == 1
            n += 1
            key = f"{base}#{n}"

    # ---- incidents

    def open_incident(self, check: str, key: str):
        return self.db.conn.execute(
            "SELECT * FROM incidents WHERE ikey=? AND state='open'", (incident_key(check, key),)
        ).fetchone()

    def apply(self, findings, block: int) -> int:
        """Apply one run's findings; returns the number of outbox rows written."""
        sent = 0
        for f in findings:
            sent += self._apply_one(f, block)
        return sent

    def _apply_one(self, f, block: int) -> int:
        now = self.clock()
        row = self.open_incident(f.check, f.key)
        subject, evidence = json.dumps(f.subject, sort_keys=True), json.dumps(f.evidence, sort_keys=True)

        if f.resolve:
            if row is None:
                return 0
            self.db.conn.execute(
                "UPDATE incidents SET state='resolved', resolved_block=?, last_seen_block=?, updated_at=?, "
                "evidence=? WHERE id=?",
                (block, block, now, evidence, row["id"]),
            )
            if row["notify"] and row["alerts"] and row["severity"] in ("page", "warn"):
                # An info-severity message, delivered where the incident was sent.
                text = self._text("resolved", f.title, f.body, row["id"])
                return int(self.enqueue(f"{row['id']}:resolved:{block}", "info", text, "resolved",
                                        f.check, row["id"], route=self.route(row["severity"]), unique=True))
            return 0

        if row is None:
            if f.incident_id:
                inc_id = f.incident_id
                if self._id_taken(inc_id):
                    raise ValueError(f"incident id {inc_id} already exists; a check must propose unique ids")
            else:  # the block may repeat: a key reopened at a block number reused by a reorganised chain
                base = inc_id = f"{f.check}-{_short(f.key)}-{block}"
                n = 1
                while self._id_taken(inc_id):
                    n += 1
                    inc_id = f"{base}-{n}"
            self.db.conn.execute(
                "INSERT INTO incidents (id, ikey, check_id, key, severity, state, notify, title, body, subject, "
                "evidence, revision, opened_block, last_seen_block, opened_at, updated_at, last_alert_at, alerts) "
                "VALUES (?,?,?,?,?, 'open', ?,?,?,?,?,?,?,?,?,?,?,?)",
                (inc_id, incident_key(f.check, f.key), f.check, f.key, f.severity, int(f.notify), f.title, f.body,
                 subject, evidence, f.revision, block, block, now, now, now if f.notify else None, int(f.notify)),
            )
            if not f.notify:
                return 0
            key = f"{inc_id}:{f.revision}" if f.revision else f"{inc_id}:open"
            return int(self.enqueue(key, f.severity, self._text(f.severity, f.title, f.body, inc_id), "open",
                                    f.check, inc_id, unique=True))

        inc_id = row["id"]
        escalated = RANK[f.severity] > RANK[row["severity"]]
        revised = f.revision is not None and f.revision != row["revision"]
        unmuted = f.notify and not row["notify"]
        send = f.notify and (escalated or revised or unmuted)
        self.db.conn.execute(
            "UPDATE incidents SET severity=?, notify=?, title=?, body=?, subject=?, evidence=?, revision=?, "
            "last_seen_block=?, updated_at=?, last_alert_at=?, alerts=? WHERE id=?",
            (f.severity, int(row["notify"] or f.notify), f.title, f.body, subject, evidence,
             f.revision if f.revision is not None else row["revision"], block, now,
             now if send else row["last_alert_at"], row["alerts"] + int(send), inc_id),
        )
        if not send:
            return 0
        # an incident tracked without a message until now (notify=False, or a silent backfill) is opened by it
        kind = "escalate" if escalated else ("open" if not row["alerts"] else "update")
        key = f"{inc_id}:{f.revision}" if revised else f"{inc_id}:{kind}:{f.severity}:{block}"
        return int(self.enqueue(key, f.severity, self._text(f.severity, f.title, f.body, inc_id), kind,
                                f.check, inc_id, unique=True))

    def _id_taken(self, inc_id: str) -> bool:
        return self.db.conn.execute("SELECT 1 FROM incidents WHERE id=?", (inc_id,)).fetchone() is not None

    def resolve_absent(self, check: str, seen_keys: set[str], block: int) -> int:
        """`resolve_on_absence`: close the check's open incidents that this run did not report."""
        from checks.base import Finding

        rows = self.db.conn.execute(
            "SELECT * FROM incidents WHERE check_id=? AND state='open'", (check,)).fetchall()
        n = 0
        for r in rows:
            if r["key"] not in seen_keys:
                n += self._apply_one(Finding(check=check, key=r["key"], severity=r["severity"], title=r["title"],
                                             body="Condition no longer observed.", resolve=True), block)
        return n

    def resolve_not_refound(self, check: str, keys, block: int, ancestor: int) -> int:
        """The reorg re-find rule (checks/README.md §6.2): close the open incidents of `keys` (last seen above the
        reorg's common ancestor) that the check's reorg run did not report again. Their evidence came from
        blocks the canonical chain no longer has; the resolution says so (info, on the incident's route)."""
        from checks.base import Finding

        n = 0
        for key in sorted(keys):
            row = self.open_incident(check, key)
            if row is None:
                continue
            evidence = {**json.loads(row["evidence"]), "resolved_by_reorg": {"ancestor": ancestor, "block": block}}
            body = (f"Resolved because its evidence left the canonical chain: a chain reorganisation replaced the "
                    f"blocks after {ancestor}, and check {check} did not find this condition again on the new chain "
                    f"(re-evaluated at block {block}). The earlier alert(s) for this incident were based on blocks "
                    f"that are no longer canonical.")
            n += self._apply_one(Finding(check=check, key=key, severity=row["severity"],
                                         title=f"Evidence left the canonical chain: {row['title']}", body=body,
                                         evidence=evidence, resolve=True), block)
        return n

    def realert(self) -> int:
        now = self.clock()
        n = 0
        reorg: dict[str, int | None] = {}
        for r in self.db.conn.execute(
                "SELECT * FROM incidents WHERE state='open' AND notify=1 AND last_alert_at IS NOT NULL").fetchall():
            every = self.realert_s.get(r["severity"])
            if not every or now - r["last_alert_at"] < every:
                continue
            if r["check_id"] not in reorg:
                reorg[r["check_id"]] = self.db.meta(f"reorg:{r['check_id']}")
            if reorg[r["check_id"]] is not None and r["last_seen_block"] > reorg[r["check_id"]]:
                continue  # possibly orphaned evidence: re-alerted only once the check's reorg run kept it open
            title = f"Still open (alert {r['alerts'] + 1}): {r['title']}"
            if self.enqueue(f"{r['id']}:realert:{r['alerts']}", r["severity"],
                            self._text(r["severity"], title, r["body"], r["id"]), "realert", r["check_id"], r["id"]):
                n += 1
            self.db.conn.execute("UPDATE incidents SET last_alert_at=?, alerts=alerts+1 WHERE id=?", (now, r["id"]))
        return n

    # ---- monitor health and digest

    def health(self, kind: str, text: str, dedup: str, realert: bool = False, check_id: str | None = None) -> bool:
        """Monitor-health messages are distinct from findings: deduplicated, optionally re-sent per window."""
        key = f"health:{kind}:{keccak(dedup.encode()).hex()[:16]}"
        if realert:
            key += f":{int(self.clock() // self.health_realert_s)}"
        return self.enqueue(key, "health", self.head("health", f"{self.name}: {kind}") + "\n" + text, "health",
                            check_id)

    def digest_due(self) -> bool:
        return bool(self.digest_interval_s) and self.clock() - (self.db.meta("last_digest") or 0) >= self.digest_interval_s

    def digest(self, block: int, sections: dict[str, list[str]]) -> bool:
        counts = self.db.conn.execute(
            "SELECT check_id, severity, COUNT(*) FROM incidents WHERE state='open' GROUP BY check_id, severity "
            "ORDER BY check_id, severity").fetchall()
        lines = [self.head("digest", f"{self.name} daily digest"), f"Block {block}."]
        lines.append("Open incidents: " + (", ".join(f"{c} {s} {n}" for c, s, n in counts) if counts else "none"))
        for check, extra in sections.items():
            if extra:
                lines.append(f"[{check}]")
                lines += extra
        self.db.set_meta("last_digest", self.clock())
        return self.enqueue(f"digest:{block}", "digest", "\n".join(lines), "digest", unique=True)


def _short(key: str) -> str:
    k = key[2:] if key.startswith("0x") else key
    return k[:16]
