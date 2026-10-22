"""SQLite state of the watch worker: scanned logs, checkpoint, incidents, outbox, inbox, per-check tables.

One file, WAL, `synchronous=FULL`. `meta.schema_version` records the schema; `migrate` upgrades a
database written by an older worker in place (additive only: tables and columns are added, the
pre-framework M5 incident table is renamed to `m5_incidents`, nothing is dropped).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

from alerts import SEVERITY_FROM_LEGACY, incident_key
from eb_events import Event
from eb_storage import Cluster

SCHEMA_VERSION = 1

SCHEMA = [
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    """CREATE TABLE IF NOT EXISTS logs (
      block INTEGER NOT NULL, log_index INTEGER NOT NULL, tx_index INTEGER NOT NULL,
      tx_hash TEXT NOT NULL, block_hash TEXT NOT NULL, kind TEXT NOT NULL,
      cluster_id TEXT, owner TEXT, operator_ids TEXT, cluster TEXT, pubkey TEXT, shares_len INTEGER,
      ref_block INTEGER, eb INTEGER, root TEXT, extra TEXT,
      PRIMARY KEY (block, log_index))""",
    "CREATE INDEX IF NOT EXISTS logs_cluster ON logs (cluster_id, block, log_index)",
    "CREATE INDEX IF NOT EXISTS logs_kind ON logs (kind, block)",
    "CREATE TABLE IF NOT EXISTS blocks (number INTEGER PRIMARY KEY, hash TEXT NOT NULL)",
    # Incident lifecycle (alerts.py): one open row per (check, key), joined through `ikey`.
    """CREATE TABLE IF NOT EXISTS incidents (
      id TEXT PRIMARY KEY, ikey TEXT NOT NULL, check_id TEXT NOT NULL, key TEXT NOT NULL,
      severity TEXT NOT NULL, state TEXT NOT NULL, notify INTEGER NOT NULL,
      title TEXT NOT NULL, body TEXT NOT NULL, subject TEXT NOT NULL, evidence TEXT NOT NULL, revision TEXT,
      opened_block INTEGER NOT NULL, last_seen_block INTEGER NOT NULL, resolved_block INTEGER,
      opened_at REAL NOT NULL, updated_at REAL NOT NULL, last_alert_at REAL, alerts INTEGER NOT NULL DEFAULT 0)""",
    "CREATE UNIQUE INDEX IF NOT EXISTS incidents_open ON incidents (ikey) WHERE state = 'open'",
    "CREATE INDEX IF NOT EXISTS incidents_check ON incidents (check_id, state)",
    # Durable outbox. `delivery` is the route chosen at enqueue (telegram | discord); `receipt` is the
    # transport's answer once sent (telegram:<message id>, discord:<status>, recorded).
    """CREATE TABLE IF NOT EXISTS outbox (
      key TEXT PRIMARY KEY, created_at REAL NOT NULL, severity TEXT NOT NULL, text TEXT NOT NULL,
      attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at REAL NOT NULL DEFAULT 0,
      sent_at REAL, delivery TEXT, last_error TEXT,
      check_id TEXT, incident_id TEXT, kind TEXT, receipt TEXT)""",
    # Failed-transaction inbox (inbox.py), de-duplicated on (tx_hash, block_hash). `canonical`: NULL until the
    # checkpoint passes `block` and its hash is compared, then 1 (delivered to checks) or 0 (orphaned, never).
    """CREATE TABLE IF NOT EXISTS inbox (
      id INTEGER PRIMARY KEY AUTOINCREMENT, tx_hash TEXT NOT NULL, block_hash TEXT NOT NULL,
      block INTEGER NOT NULL, sender TEXT, target TEXT, selector TEXT, function TEXT, status TEXT,
      monitor TEXT, observed_at TEXT, line TEXT NOT NULL,
      resolved INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
      revert_selector TEXT, revert_name TEXT, revert_source TEXT, revert_data TEXT, canonical INTEGER,
      UNIQUE (tx_hash, block_hash))""",
]

OUTBOX_ADDED_COLUMNS = ("check_id TEXT", "incident_id TEXT", "kind TEXT", "receipt TEXT")
INBOX_ADDED_COLUMNS = ("canonical INTEGER",)


def _columns(conn, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def migrate(conn: sqlite3.Connection) -> int:
    """Bring the schema to SCHEMA_VERSION; returns the version found before migrating (0 = new or pre-framework)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        found = 0
        if "meta" in tables:
            row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            found = json.loads(row[0]) if row else 0
        if found > SCHEMA_VERSION:
            raise SystemExit(f"database schema {found} is newer than this worker ({SCHEMA_VERSION})")
        legacy_m5 = "incidents" in tables and "cluster_id" in _columns(conn, "incidents")
        if legacy_m5:
            conn.execute("ALTER TABLE incidents RENAME TO m5_incidents")
        legacy_outbox = "outbox" in tables and "receipt" not in _columns(conn, "outbox")
        if "outbox" in tables:
            have = _columns(conn, "outbox")
            for col in OUTBOX_ADDED_COLUMNS:
                if col.split()[0] not in have:
                    conn.execute(f"ALTER TABLE outbox ADD COLUMN {col}")
        if legacy_outbox:
            _normalise_legacy_outbox(conn)
        if "inbox" in tables:
            have = _columns(conn, "inbox")
            for col in INBOX_ADDED_COLUMNS:
                if col.split()[0] not in have:
                    conn.execute(f"ALTER TABLE inbox ADD COLUMN {col}")
        for stmt in SCHEMA:
            conn.execute(stmt)
        if legacy_m5:
            _backfill_m5(conn)
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps(SCHEMA_VERSION),),
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return found


def _normalise_legacy_outbox(conn) -> None:
    """Pre-framework rows keep their key, text and retry state. Severities take the framework names
    (critical -> page, warning -> warn), so they route like any other message; the pre-framework worker
    wrote the transport receipt into `delivery` once sent, which becomes `receipt` (`delivery` is the
    route). Unsent rows keep `delivery` NULL: the worker routes them by severity at delivery."""
    for old, new in SEVERITY_FROM_LEGACY.items():
        if old != new:
            conn.execute("UPDATE outbox SET severity=? WHERE severity=?", (new, old))
    conn.execute(
        "UPDATE outbox SET receipt=delivery, delivery=CASE WHEN delivery LIKE 'telegram:%' THEN 'telegram' END "
        "WHERE sent_at IS NOT NULL AND delivery IS NOT NULL")
    conn.execute("UPDATE outbox SET delivery=NULL WHERE sent_at IS NULL")


def _backfill_m5(conn) -> None:
    """Open pre-framework M5 incidents become framework incidents under the same id, already alerted."""
    for r in conn.execute("SELECT * FROM m5_incidents WHERE state='open'").fetchall():
        cid = r["cluster_id"]
        notify = r["kind"] != "latent"
        conn.execute(
            "INSERT OR IGNORE INTO incidents (id, ikey, check_id, key, severity, state, notify, title, body, subject, "
            "evidence, revision, opened_block, last_seen_block, opened_at, updated_at, last_alert_at, alerts) "
            "VALUES (?,?,?,?,?, 'open', ?,?,?,?,?, NULL, ?,?,?,?,?,?)",
            (r["id"], incident_key("m5", cid), "m5", cid, SEVERITY_FROM_LEGACY[r["severity"]], int(notify),
             f"#94775 {r['kind']} (migrated)", "", json.dumps({"cluster_id": "0x" + cid}), r["evidence"],
             r["first_seen_block"], r["last_seen_block"], r["created_at"], r["updated_at"],
             r["updated_at"] if notify else None, int(notify)),
        )


class DB:
    def __init__(self, path: str, readonly: bool = False):
        """`readonly`: a reader beside the running worker (status, export, audit): no migration, no write lock, so
        it answers while a check's run holds the write transaction."""
        self.path, self.readonly = path, readonly
        if readonly:
            self.conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, isolation_level=None, timeout=30)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA temp_store=MEMORY")
            self.migrated_from = None
            return
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, isolation_level=None, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        # Sorts and temporary indexes stay in memory: the container's root filesystem is read-only (no /tmp file,
        # SQLITE_IOERR "disk I/O error" on the first large ORDER BY otherwise).
        self.conn.execute("PRAGMA temp_store=MEMORY")
        self.migrated_from = migrate(self.conn)

    def tx(self):
        return _Tx(self.conn)

    def meta(self, key: str, default=None):
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key: str, value) -> None:
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    def insert_event(self, ev: Event) -> None:
        c = ev.cluster
        self.conn.execute(
            "INSERT OR REPLACE INTO logs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                ev.block, ev.log_index, ev.tx_index, ev.tx_hash, ev.block_hash, ev.kind,
                ev.cluster_id.hex() if ev.cluster_id else None, ev.owner,
                json.dumps(list(ev.operator_ids)) if ev.cluster_id else None,
                json.dumps([c.validator_count, c.network_fee_index, c.index, c.active, str(c.balance)]) if c else None,
                ev.pubkey, ev.shares_len, ev.ref_block, ev.eb, ev.root,
                json.dumps(ev.extra, default=str) if ev.extra else None,
            ),
        )

    @staticmethod
    def row_event(r: sqlite3.Row) -> Event:
        c = json.loads(r["cluster"]) if r["cluster"] else None
        return Event(
            kind=r["kind"], block=r["block"], log_index=r["log_index"], tx_index=r["tx_index"],
            tx_hash=r["tx_hash"], block_hash=r["block_hash"],
            cluster_id=bytes.fromhex(r["cluster_id"]) if r["cluster_id"] else None, owner=r["owner"],
            operator_ids=tuple(json.loads(r["operator_ids"])) if r["operator_ids"] else (),
            cluster=Cluster(c[0], c[1], c[2], bool(c[3]), int(c[4])) if c else None,
            pubkey=r["pubkey"], shares_len=r["shares_len"], ref_block=r["ref_block"], eb=r["eb"], root=r["root"],
            extra=json.loads(r["extra"]) if r["extra"] else {},
        )

    def cluster_events(self, cid: str, upto: int) -> list[Event]:
        rows = self.conn.execute(
            "SELECT * FROM logs WHERE cluster_id=? AND block<=? ORDER BY block, log_index", (cid, upto)
        ).fetchall()
        return [self.row_event(r) for r in rows]

    def clusters(self, explicit_only: bool) -> list[str]:
        if explicit_only:
            q = "SELECT DISTINCT cluster_id FROM logs WHERE kind='ClusterBalanceUpdated'"
        else:
            q = "SELECT DISTINCT cluster_id FROM logs WHERE cluster_id IS NOT NULL"
        return [r[0] for r in self.conn.execute(q)]

    def latest_cluster_events(self) -> dict[str, Event]:
        """The last event carrying a Cluster tuple, per cluster id (the log-derived inventory)."""
        rows = self.conn.execute(
            "SELECT l.* FROM logs l JOIN (SELECT cluster_id, MAX(block * 1000000 + log_index) AS p FROM logs "
            "WHERE cluster_id IS NOT NULL GROUP BY cluster_id) m "
            "ON l.cluster_id = m.cluster_id AND l.block * 1000000 + l.log_index = m.p"
        ).fetchall()
        return {r["cluster_id"]: self.row_event(r) for r in rows}

    def events_after(self, cursor: tuple[int, int], upto: int, kinds: frozenset[str] | None) -> Iterator[Event]:
        """Stored events after `cursor` (block, log_index) up to block `upto`, in chain order."""
        q = "SELECT * FROM logs WHERE (block > ? OR (block = ? AND log_index > ?)) AND block <= ?"
        args: list = [cursor[0], cursor[0], cursor[1], upto]
        if kinds is not None:
            q += f" AND kind IN ({','.join('?' * len(kinds))})"
            args += sorted(kinds)
        for r in self.conn.execute(q + " ORDER BY block, log_index", args):
            yield self.row_event(r)

    def has_events_after(self, cursor: tuple[int, int], upto: int, kinds: frozenset[str] | None) -> bool:
        return next(self.events_after(cursor, upto, kinds), None) is not None


class _Tx:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        self.conn.execute("BEGIN IMMEDIATE")
        return self

    def __exit__(self, exc_type, exc, tb):
        self.conn.execute("ROLLBACK" if exc_type else "COMMIT")
        return False
