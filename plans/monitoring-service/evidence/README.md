# Evidence

Bulky outputs referenced from `execution-log.md` and worker attempts: command transcripts, `pytest -v` outputs, one-shot monitor match JSON, `status` JSON snapshots, metrics scrapes, soak exports, recorded mainnet receipts used as fixtures (hashed).

Rules: one subdirectory per checkpoint (`cp0/`, `cp1a/`, …); name files `<V-ID>-<short>.<ext>`; record SHA-256 in the referencing log row for anything a decision depends on; audience internal; never store secrets, `.env` contents, rendered production config or the live worker database. Record secret *names* only.
