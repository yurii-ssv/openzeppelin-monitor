FAIL

CP1 — Clean disable, independent QA round 1, 2026-11-06. Candidate `7639cffb4991ce6bd536cfc607b27ce1aa78032d`; frozen base `61307ddf3f813cfbe38ef062490224862ccd2b91` (implementation identical to `9e94a1a`). QA implemented and repaired none of the candidate. One BLOCKING defect violates D6; the required reader inventory also has omissions.

## Identity, environment and boundaries

- Initial `git rev-parse HEAD` exactly matched the candidate and `git status --porcelain` was empty. Initial branch was `mtabasco/cs-cp1-qa1`; the report branch is `cs/cp1-qa1`.
- Role: `agents/checkpoint-qa.md`, SHA256 `ec15481953f5defa91e6da9089b51a7e7ed53a8a54d8554ac6f970ba64c35cbf`; defect catalog: `qa/DEFECT-CLASSES.md`, SHA256 `7daa55f69bdd3218f83283a0bd752075a84141888196869977c046819def5876`. Both rehashed and matched. Authority and severity: packet Global rules / D2; oracle: D3, D6–D8, D11–D12 and V1.1–V1.7.
- Read the implementer handoff and its suite, red-on-base and falsifier claims; none substituted for QA measurements. Requested QA identity in the dispatch: Codex GPT Sol 6.1 high. QA did not launch agents or change the requested model; the launch receipt/effective-model verification belongs to the coordinator.
- macOS arm64; Python 3.12.15, pytest 9.1.1, ruff 0.17.1, uv 0.13.4 (`7456b60bc`), Git 2.39.5, Orca 1.4.250. Every shell prepended `$HOME/.foundry/bin`. Used only the authorized venv and development-requirements installation; uv initially failed on its sandboxed cache lock, then succeeded with escalation.
- All behavioral checks used FakeChain, FakeRpc and record/capturing notification. No production RPC, live notification, deployment, push or mainnet transaction occurred. No external documentation lookup was needed: this reviews repository behavior against frozen decisions, not third-party API syntax.

## Findings

### QF1 — BLOCKING: falsy non-boolean values silently close incidents without D6's explicit disable

Affected: A1; D6; V1.1's not-disabled control and V1.3's registry/config boundary. `reconcile/checks/registry.py:66` implements `not configured.get(cid, {}).get("enabled", True)` rather than checking the literal boolean `False`. `Worker.__init__` consumes that result and `_apply_switches` irreversibly changes open rows to `disabled` at bootstrap.

Expected: only `checks.<id>.enabled: false` authorizes closure. Values `0`, `null` and `""` must not be interpreted as that explicit switch; rejecting a malformed setting at startup would also avoid the unsafe closure. Actual: all three values close all four incidents, write a disabled marker, remove them from open counts and make the closure permanent. This is an unsafe state mutation/false clean against the frozen D6 oracle, not a message-text or coverage-only note. Existing `build()` truthiness does not authorize widening D6; `build` was required to remain unchanged.

Reproducer: export the candidate's `reconcile/` into a scratch directory; copy [test_cp1_independent.py](cp1-round-1/test_cp1_independent.py) into its `reconcile/tests/`; run the QA venv's `python -m pytest -vv reconcile/tests/test_cp1_independent.py`. `test_d6_only_literal_false_may_close[0]`, `[None]` and `[]` (empty-string parameter id) fail:

```
AssertionError: D6: enabled=0 must yield open, actual=['disabled', 'disabled', 'disabled', 'disabled']
AssertionError: D6: enabled=None must yield open, actual=['disabled', 'disabled', 'disabled', 'disabled']
AssertionError: D6: enabled='' must yield open, actual=['disabled', 'disabled', 'disabled', 'disabled']
```

The literal `False` and `True` controls pass. [Independent final output](cp1-round-1/independent-final.txt): exit 1, **3 failed, 10 passed**, zero skipped. No implementation modification was used. Coordinator action: request a scoped repair preserving D6, or obtain an explicit owner decision changing D6; QA grants no waiver.

### QF2 — NOTE: required incidents-reader table omits two consumers

Affected: V1.6 (required inventory incomplete), A1/A4 assurance. QA independently searched `FROM incidents` and then `JOIN incidents`, updates/inserts and migration accesses. The handoff table omits:

| Reader | Actual behavior with disabled rows | Why it matters |
|---|---|---|
| `checks/m1_stale_tuple.py:568–571`, `_reported` | Joins outbox to incidents with **no state filter**; an old message linked to a disabled row can still establish that a poison transaction was reported. | Feeds transient-event suppression in `_transient`; the blanket claim that check readers ignore disabled rows is incomplete. |
| `scripts/fork_e2e.py:151–156`, `sent` | Joins outbox to incidents with **no state filter**; disabled incidents' queued/delivered messages remain visible in evidence. | Evidence-only reader, analogous to the listed `incident()` reader. |

All listed production readers were otherwise found with the described predicates. No incorrect alert from these omissions was demonstrated, so severity is NOTE under D2, while V1.6's explicit completeness requirement is FAIL. Proposed owner: CP1 implementer/coordinator; revisit trigger: before CP1 acceptance, add both rows with behavior and evidence/design rationale and correct the blanket prose. QA's supplemental inventory does not silently repair the issued implementer handoff. Search evidence: [production accesses](cp1-round-1/incidents-readers.txt), [all FROM matches including tests](cp1-round-1/from-incidents-all.txt).

### QN1 — NOTE: document verify-layout's closure side effect

Affected: A4 documentation / bootstrap entry points; no failing nominal V row. Both `once` and `verify-layout` call bootstrap and commit disabled closures, as required by D7's placement. The new `reconcile/README.md` paragraph names `run`/`once` only. Independent CLI probes for both commands passed with four rows closed, correct marker, zero new outbox rows. Proposed owner: CP2 docs; revisit trigger: D13 runbook review before closure. This is a documentation omission; no unauthorized command behavior against D7 was established.

## Results by verification row

The candidate-owned checks below were independently rerun in one targeted invocation: `python -m pytest -q reconcile/tests/test_switches.py reconcile/tests/test_backfill.py reconcile/tests/test_framework.py::test_scheduler_reorg_resets_event_cursor reconcile/tests/test_framework.py::test_config_example_loads_and_builds_every_check` → exit 0, **122 passed**, zero skipped ([output](cp1-round-1/targeted.txt)). This is 53 switch cases + 67 D17 cases + 2 framework controls. Full-suite and scratch evidence are additional measurements.

| V | Result | Measured action, oracle and rejected wrong outcome |
|---|---|---|
| V1.1 | PASS | Both `test_disabled_check_closes_its_incidents_and_is_never_realerted[False/True]` passed: four severities/notification states, atomic disabled closure at checkpoint, closed=4 marker, no start/resolve/health messages, unchanged run metadata, 26 hourly steps, no 6h/24h re-alert, status/open counts and digest line. Pending info outbox row delivered after closure. `test_a_check_that_is_merely_not_built_is_not_disabled` passed: excluded check's rows remain open, no disabled marker, no re-alert. Nominal explicit-false behavior passes; the broader D6 malformed-value boundary fails under V1.3/QF1. Red-base controls below prove the 6h wrong outcome is rejected. |
| V1.2 | PASS | Both round-trip/backfill variants, zero-open and new-database tests, same-block new-id test passed. Repeated disabled start preserves row/marker bytes; enable clears marker; second disable closes only new rows, zero notices at every start. Scratch `test_resolved_rows_and_times_are_preserved` additionally proves resolved rows remain byte-identical and updated_at/evidence.at equal the closing clock. |
| V1.3 | FAIL | Nominal sibling control, registration order and example-config tests passed: B remains built, re-alerts and resolves while A stays disabled; load_config+Worker lists disabled m7. Independent non-boolean config cases close rows contrary to D6 (QF1), while true/false controls pass. No unknown-id behavior changed in build. |
| V1.4 | PASS | Depth-1/depth-4 disabled cursor tests, two-reorg minimum-ancestor test, re-enable canonical-event replay, and built/unregistered scheduler control passed. No cursor/marker row created for stateless excluded ids; disabled pending state excluded from degraded/status pending. Independent marker-only **unregistered** id test passed, retaining marker across restart without creating an unnecessary cursor. The base cursor failures below establish effectiveness. |
| V1.5 | PASS | Marker-write exception test passed: post-UPDATE/pre-marker failure rolls back every closure, next start closes exactly once, no message. Read-only CLI status before a start exits 0 and changes neither incidents nor marker/outbox. Independent malformed evidence in the second disabled check rolls back the first check's changes too; mismatched RPC identity closes nothing; once/verify-layout probes both confirm silent closure. |
| V1.6 | FAIL | Export/status/metrics tests pass and the six-row persisted-state table covers empty/error/partial, depth 1/N, restart, same-block and time anchoring using tests or design rationales. Direct resolved-row/time and marker-only probes strengthen weak cells. However, independent reader discovery finds the two missing table rows in QF2; the explicit must-reject condition “a reader not listed” is met. No implementation repair or handoff rewrite by QA. |
| V1.7 | PASS | Required candidate full suite outside sandbox: exit 0, **2,366 passed**, zero skipped, 67.79s ([output](cp1-round-1/suite-unrestricted.txt)); ruff exit 0, “All checks passed!” ([output](cp1-round-1/ruff.txt)). Base suite contains 2,313 cases; candidate adds 53. Frozen-base and 9e94a1a regression measurements each collect 53 and yield 51 FAIL / 2 PASS for expected missing behavior (details below). Guarded files and build unchanged; all candidate diff paths allowed; backfill SHA256 matches base. |

## Regression controls, failures retained and scope proof

The named closure and disabled-reorg regressions were measured on **both** `61307dd` and `9e94a1a`, using isolated archive exports with only the candidate's new test file copied in. Both runs exit 1: **51 failed, 2 passed**, zero skips. Per-case assertion text is preserved in [frozen-base results](cp1-round-1/base-per-test.txt) and [9e94a1a results](cp1-round-1/regression-base-per-test.txt); full failures and JUnit XML are adjacent.

- V1.1, both backfill variants: `assert outbox(env.w, "kind='realert'") == []` fails on five rows, beginning with `sw_a-kpage-1001:realert:1`, `created_at=1800021720.0`, “Still open (alert 2)” (clock start + 6h02m).
- V1.4, depth 1: `assert [1004, 2147483648] == [1002, 2147483648]`; depth 4: `assert [1007, 2147483648] == [1005, 2147483648]`. These are behavioral failures, not import or status-field failures.
- Four API/visibility tests fail on the base only for missing helper/attribute/status field, as disclosed by the implementer. They are not counted as behavioral regression proof. Both base-green controls are `test_digest_has_no_disabled_line_without_disabled_checks` and sweep seed 14; the latter's benign trace does not falsify a mechanism by itself. The required named regressions do fail behaviorally.

Candidate's first full-suite attempt inside the sandbox: exit 1, **10 failed, 2,356 passed**, all failures from forbidden local socket binding ([retained output](cp1-round-1/suite.txt)). Reran the required command outside the sandbox with approval; all 2,366 passed. These environment failures are not candidate defects and are not erased from the record.

An extra baseline-count run initially exported only `reconcile/`: **12 failed, 2,301 passed** (10 sandbox socket failures plus 2 missing `deploy/config/networks/evm_ethereum_mainnet.json` fixtures), retained in [base original suite](cp1-round-1/base-original-suite.txt). The [unrestricted baseline run](cp1-round-1/base-suite-unrestricted.txt) removes the socket restriction: exit 1, **2 failed, 2,311 passed**, 61.69s, only those missing export fixtures. The two config cases were separately rerun in a **complete** frozen-base export: exit 0, **2 passed** ([config controls](cp1-round-1/base-config-controls.txt)). Thus the 2,313 baseline cases are all accounted for with passing measurements, but there was no single all-green baseline suite invocation; missing export fixtures were QA setup errors, not baseline regressions.

`test_backfill.py` SHA256 on candidate and frozen base is `28da7d49e950a0bdc51ee7d4f6993ad757fa72d60524c39a7c28efcc24702d35`. `git diff 61307dd..7639cff -- reconcile/tests/test_backfill.py 'reconcile/tests/test_m*.py' 'reconcile/checks/m*.py' reconcile/db.py reconcile/metrics.py reconcile/tests/harness.py` is empty. `registry.build` is byte-unchanged. The actual candidate diff has 10 allowed paths, 1,046 insertions / 11 deletions:

```
plans/check-switches/workers/cp1/{handoff.md,suite.txt,red-on-base.txt,falsifiers.txt}
reconcile/{README.md,alerts.py,eb_reconcile.py}
reconcile/checks/{README.md,registry.py}
reconcile/tests/test_switches.py
```

No deletions, out-of-scope changes, or pre-existing untracked candidate files. The checks README edits remain in §4. No db/schema/check implementation or m*/backfill test changes.

## Verdicts on all five implementer-raised risks

| Risk | Verdict and severity | Evidence / remaining boundary |
|---|---|---|
| Every bootstrap caller closes, including verify-layout/once | Behavior PASS under D7; NOTE QN1 for docs | Independent CLI probes pass. A layout inspection can commit the closure before doing layout work. Document that fact in D13; status/export do not bootstrap. |
| Truthiness vs explicit false | FAIL, BLOCKING QF1 | 0/null/empty-string independently cause permanent closure; boolean controls pass. |
| Not-built, not-disabled open incidents become silent | PASS, no defect severity | D8 explicitly requires this; D6 leaves rows open. The control measures that exact distinction. Production's normal build path builds enabled checks. |
| M1/M2 resolved_since excludes disabled rows | CP1 PASS, no demonstrated defect; CP2 composition remains NOT RUN here | D7 explicitly requires the exclusion. Independent **real M1/M2** probes pass: M1 disabled transient poisoning survives a reorg orphaning its repair and is found from rewound detection tables; M2 disabled hard condition is re-found after reorg with an empty API using persisted validated cluster state. Old disabled rows remain byte-identical and reorg markers clear only after the successful run. These probes run with backfill disabled; they do not establish D9 silent catch-up / first-live behavior. CP2 owns V2.4 and the composed semantics. |
| Pending outbox delivered after closure | PASS, no defect severity | V1.1 captures actual delivery of the pre-closure queued info row. D7 expressly preserves state-agnostic delivery. New closure itself queues nothing; the runbook must explain pre-existing messages may still arrive. |

## Supplemental state/reader judgment and limitations

Classes 1, 2, 10 and 12 were attacked through empty/no-state controls, partial multi-check closure rollback, identity failure, cursor-only/marker-only and unregistered state, depth 1/N reorgs, restart, marker idempotency, same-block id reuse, real M1/M2 state recovery and behavioral red-base comparison. Persisted disabled rows are framework-terminal; open-only readers exclude them, resolved-only readers exclude them, id-reservation readers retain their ids, export includes them. The two omitted any-state joins are described under QF2. DB migration code inserts legacy rows and does not enumerate current disabled rows; no schema change is needed. Incident writes select an open row/id before updating it, with the new closure as the explicit exception.

No mutation campaign or slow check campaign was run, per D2. The implementer's seven mutation/falsifier logs were inspected but not independently rerun; targeted adversarial scenarios and wrong-revision controls supplied QA's effectiveness evidence. No delivery smoke, real-chain lookup, metrics change, CP2 catch-up test or CP2 acceptance claim is made. All CP1 V rows were run/inspected; none is NOT RUN. Unconfirmed required claims remain D6 compliance and complete implementer reader inventory; both are findings above.

## Writes, restoration and next decision

Only this report and `qa/cp1-round-1/` evidence were written in the candidate checkout. Scratch exports and experimental tests were under `/private/tmp`; archive runner cleanup confirms removal, and the separate risk/config exports were also removed. [Candidate restoration diff](cp1-round-1/candidate-restoration.diff) is empty: no implementation/test/config under review was edited. The ignored venv is the authorized installation. QA source probes and archive scripts are evidence, not a permanent test repair; scripts preserve their original setup limitations.

Commit only these QA paths on `cs/cp1-qa1`, with attribution; never push. The coordinator must disposition QF1 and request repair/owner decision, address the V1.6 inventory gap, and assign QN1's docs owner/trigger. QA sets no checkpoint workflow state and starts no subsequent CP.
