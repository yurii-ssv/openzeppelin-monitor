# Attempts — CP1B

Same entry shape as execution-log.md events, worker-side. Append only. Measured UTC (`date -u +%Y-%m-%dT%H:%MZ`).

<a id="attempt-1"></a>
## Attempt 1 — baseline and environment

- Observed UTC: 2026-10-21T10:57Z
- Task / Dispatch: `task_3a2ee3e27b00` / `ctx_98a50f4c4736`
- Frozen base: `4fbb9f161005dd7634a7eaabee2b022d2cc96c2e` (`git rev-parse HEAD` at start; branch `mtabasco/ms-cp1b`; porcelain 0 lines)
- Action and concrete outcome: created the venv per plan §7 (`uv venv reconcile/.venv --python 3.12`; `uv pip install --python reconcile/.venv/bin/python -r reconcile/requirements-dev.txt`: pycryptodome 3.24.0, pytest 9.1.1, pygments 2.21.0); re-ran the V0.9 baseline. `anvil` present at `~/.foundry/bin/anvil` after `export PATH=$HOME/.foundry/bin:$PATH`.

| Verification ID | Exact command | Exit / result / counts / skips | Environment | Evidence path/hash |
|---|---|---|---|---|
| V0.9 (baseline re-run) | `reconcile/.venv/bin/python -m pytest -q reconcile/tests` | exit 0; **30 passed**, 0 skipped | Python 3.12, fresh venv | this entry |

- Deviations and findings: none.
- Resulting head: `4fbb9f1` (no commit)
- Next action: write the framework modules (S2).
