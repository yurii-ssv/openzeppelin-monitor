"""Prometheus text exposition on a stdlib HTTP server (GET /metrics).

Values are read from the database on each scrape through a separate read-only connection, so the
endpoint reflects durable state and never blocks the worker loop.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _label(v: str) -> str:
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def render(conn: sqlite3.Connection, check_ids: list[str]) -> str:
    def meta(key, default=None):
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    lines: list[str] = []

    def metric(name: str, kind: str, help_: str, samples: list[tuple[dict, float]]) -> None:
        lines.append(f"# HELP {name} {help_}")
        lines.append(f"# TYPE {name} {kind}")
        for labels, value in samples:
            lab = ",".join(f'{k}="{_label(v)}"' for k, v in labels.items())
            lines.append(f"{name}{{{lab}}} {value}" if lab else f"{name} {value}")

    cp = meta("checkpoint") or {}
    metric("ssv_watch_checkpoint_block", "gauge", "Last scanned and stored block.", [({}, cp.get("block", -1))])
    metric("ssv_watch_lag_blocks", "gauge", "Confirmed head minus checkpoint at the last lag check.",
           [({}, meta("lag_blocks", -1))])
    phase = meta("phase") or {"phase": "live"}
    backfilling = phase.get("phase") == "backfill"
    target = meta("scan_target")
    metric("ssv_watch_backfill", "gauge", "1 until every check has evaluated history silently (D17; a check alerts "
           "after its own silent evaluation); 0 once live.",
           [({}, int(backfilling))])
    metric("ssv_watch_backfill_remaining_blocks", "gauge", "Blocks between the checkpoint and the scan target while "
           "backfilling (0 once live).",
           [({}, max(target - cp.get("block", 0), 0) if backfilling and target is not None else 0)])
    open_counts = conn.execute(
        "SELECT check_id, severity, COUNT(*) FROM incidents WHERE state='open' GROUP BY check_id, severity").fetchall()
    metric("ssv_watch_open_incidents", "gauge", "Open incidents by check and severity.",
           [({"check": c, "severity": s}, n) for c, s, n in open_counts] or [({}, 0)])
    undelivered = conn.execute("SELECT COUNT(*) FROM outbox WHERE sent_at IS NULL").fetchone()[0]
    metric("ssv_watch_outbox_undelivered", "gauge", "Outbox messages not yet delivered.", [({}, undelivered)])
    failing = conn.execute("SELECT COUNT(*) FROM outbox WHERE sent_at IS NULL AND attempts > 0").fetchone()[0]
    metric("ssv_watch_outbox_failing", "gauge", "Undelivered messages with at least one failed attempt.",
           [({}, failing)])
    runs = {c: meta(f"run:{c}", {}) for c in check_ids}
    metric("ssv_watch_check_last_run_seconds", "gauge", "Unix time of the check's last successful run.",
           [({"check": c}, r.get("at", 0)) for c, r in runs.items()])
    metric("ssv_watch_check_last_run_block", "gauge", "Block of the check's last successful run.",
           [({"check": c}, r.get("block", -1)) for c, r in runs.items()])
    metric("ssv_watch_check_errors_total", "counter", "Runs of the check that raised.",
           [({"check": c}, r.get("errors", 0)) for c, r in runs.items()])
    metric("ssv_watch_rpc_consecutive_failures", "gauge", "Consecutive failed worker steps.",
           [({}, meta("rpc_failures", 0))])
    unavailable = meta("inputs_unavailable", {})
    metric("ssv_watch_input_unavailable", "gauge", "1 while a configured input (live parameters, inbox) cannot be read "
           "or the Monitor stalls (input monitor).",
           [({"input": k}, int(unavailable.get(k, False))) for k in ("params", "inbox", "monitor")])
    watch = meta("monitor:watch") or {}  # monitor_watch.py (D20); absent while the watch is off
    lag = watch.get("lag_blocks")
    metric("ssv_watch_monitor_last_block", "gauge", "The Monitor's last processed block as last read (-1: never read).",
           [({}, watch.get("block") if watch.get("block") is not None else -1)])
    metric("ssv_watch_monitor_lag_blocks", "gauge", "Head minus the Monitor's last processed block at the last read "
           "(-1: unknown: file unreadable or head unknown).", [({}, lag if lag is not None else -1)])
    metric("ssv_watch_monitor_stalled", "gauge", "1 while a Monitor stall is open (monitor-stall health message).",
           [({}, int(bool(watch.get("stall"))))])
    metric("ssv_watch_monitor_skipped_blocks_total", "counter", "Blocks the Monitor skipped and never evaluated "
           "(monitor-skipped health messages).", [({}, watch.get("skipped_total", 0))])
    missed = meta("monitor:missed") or {}  # D21: the Monitor's missed-blocks file
    metric("ssv_watch_monitor_missed_blocks", "gauge", "Entries of the Monitor's missed-blocks file at the last read "
           "(pending: waiting for its recovery job; failed: given up; -1: unreadable or never read).",
           [({"status": k}, -1 if missed.get("unreadable") or "checked_at" not in missed else missed.get(k, 0))
            for k in ("pending", "failed")])
    metric("ssv_watch_monitor_unevaluated_blocks_total", "counter", "Blocks the Monitor fetched but will never "
           "evaluate: given up, or gone from its missed-blocks file without a provable retry (monitor-unevaluated "
           "health messages).", [({}, missed.get("unevaluated_total", 0))])
    inbox = conn.execute("SELECT COUNT(*), COALESCE(SUM(resolved = 0), 0), COALESCE(SUM(canonical IS NULL), 0), "
                         "COALESCE(SUM(canonical = 0), 0) FROM inbox").fetchone()
    # pending: revert not yet resolved; held: block not yet confirmed canonical; orphaned: never delivered
    metric("ssv_watch_inbox_entries", "gauge", "Failed-transaction inbox entries by state.",
           [({"state": s}, n) for s, n in zip(("total", "pending", "held", "orphaned"), inbox)])
    return "\n".join(lines) + "\n"


class MetricsServer:
    def __init__(self, db_path: str, check_ids: list[str], host: str = "127.0.0.1", port: int = 9464):
        self.db_path, self.check_ids = db_path, check_ids
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - http.server API
                if self.path.split("?")[0] != "/metrics":
                    self.send_error(404)
                    return
                body = server.render().encode()
                self.send_response(200)
                self.send_header("content-type", "text/plain; version=0.0.4; charset=utf-8")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer((host, port), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="metrics", daemon=True)

    def render(self) -> str:
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=5)
        try:
            return render(conn, self.check_ids)
        finally:
            conn.close()

    def start(self) -> MetricsServer:
        self.thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
