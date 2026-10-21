# Attempts — <CPx>

Same entry shape as execution-log.md events, worker-side. Append only. Measured UTC (`date -u +%Y-%m-%dT%H:%MZ`).

<a id="attempt-1"></a>
## Attempt 1 — <intent>

- Observed UTC: <…>
- Task / Dispatch: <…>
- Frozen base: <head>
- Action and concrete outcome: <…>

| Verification ID | Exact command | Exit / result / counts / skips | Environment | Evidence path/hash |
|---|---|---|---|---|
| <V> | <…> | <explicit; NOT RUN if not executed> | <…> | <…> |

- Falsifiers: <mutation → named test red → restored → `git status --porcelain` clean>
- Deviations and findings: <fact versus hypothesis; affected owners>
- Resulting head: <hash>
- Next action: <…>
