FAIL

CP2 composed independent QA, round 1 (V2.9), 2026-11-06. Audited candidate
`718606c5ee2d43c260240101c06642340cabc1a0`, CP2 base
`d7b3da6a353b2e5f54ff95af00d099cacf291e4c`, composed code base `9e94a1a`.
The nominal suite passes, but independently reproduced historical/disabled-period pages are BLOCKING under D2.
**DF1 is closed.** QA repaired no implementation, test or config.

## Identity and measurements

Initial HEAD equalled the exact dispatched candidate and `git status --short` was empty. Initial branch
`mtabasco/cs-cp2-qa1`; report branch `cs/cp2-qa1`. Role SHA256
`ec15481953f5defa91e6da9089b51a7e7ed53a8a54d8554ac6f970ba64c35cbf`, defect catalog SHA256
`7daa55f69bdd3218f83283a0bd752075a84141888196869977c046819def5876`, coordinator handoff SHA256
`cad51e5cee272e36449221bb260b88f5d2fb573e985839a16dd3703510a4ecbc` all match.
Read the required role/catalog first, both CP1 handoffs, both CP1 QA reports, QF3 source, CP2 handoff/checklist/
suite/red/falsifier records, implementation and relevant tests/docs. Input hashes and tool identity are in
[identity.txt](cp2-round-1/identity.txt). Implementer outputs were claims, not reused measurements.

Requested identity: Codex GPT Sol 6.1 high, per dispatch; the coordinator owns the launch.effective receipt and this
worker did not launch/change models or agents. Environment: macOS arm64, Python 3.12.15, pytest 9.1.1, ruff 0.17.1,
uv 0.13.4, Orca 1.4.250. Every shell prepended `$HOME/.foundry/bin`. Only the authorized venv and
requirements-dev install were performed. uv's initial sandbox cache-lock failure was followed by an approved
installation; full suite used approved execution outside the sandbox for existing localhost socket tests.

| Action on candidate | Exit / measured result | Evidence |
|---|---|---|
| `reconcile/.venv/bin/python -m pytest -q reconcile/tests` | 0; **2,464 passed**, 60.56s; zero skipped | [suite.txt](cp2-round-1/suite.txt) |
| `reconcile/.venv/bin/ruff check reconcile` | 0; all checks passed | [ruff.txt](cp2-round-1/ruff.txt) |
| `pytest -q` on `test_catchup.py test_switches.py test_backfill.py` | 0; **218 passed** = 69 + 82 + 67; zero skipped | [targeted.txt](cp2-round-1/targeted.txt) |
| Original CP1 independent/sibling probes, copied unchanged into candidate archive | 0; **107 passed**, zero skipped | [output](cp2-round-1/cp1-independent.txt), [per-test](cp2-round-1/cp1-independent-per-test.txt) |
| Independent CP2 adversarial probes, candidate archive | 1; **11 failed, 10 passed**, zero skipped; failures are NF1 ×9, NF2 global ×1, NF3 ×1 | [final output](cp2-round-1/cp2-independent-final.txt), [per-test](cp2-round-1/cp2-independent-final-per-test.txt) |
| Real M1/M2 D11 + D9 composition, candidate archive | 0; **2 passed**, zero skipped | [final output](cp2-round-1/real-check-composition-final.txt) |
| Candidate CP2 test file on frozen CP1 base `d7b3da6` | 1; **41 failed, 28 passed**, zero skipped; 69 collect | [output](cp2-round-1/cp2-red-on-cp1.txt), [per-test](cp2-round-1/cp2-red-on-cp1-per-test.txt) |
| Candidate CP1 test file on code base `9e94a1a` | 1; **80 failed, 2 passed**, zero skipped; 82 collect | [output](cp2-round-1/cp1-red-on-code-base.txt), [per-test](cp2-round-1/cp1-red-on-code-base-per-test.txt) |
| Independent global NF1/NF2 probes on `9e94a1a` | 1; **4 failed**, zero skipped; behavioral failures establish KNOWN RED provenance | [output](cp2-round-1/global-known-red-final.txt), [per-test](cp2-round-1/global-known-red-final-per-test.txt) |

Archive runner [source](cp2-round-1/run_archives.py) captures each pytest subprocess exit; its own successful
evidence-collection exit is not an acceptance gate. Experiments run in complete `git archive` copies under
`/private/tmp`, with test data inside each copy, removed in `finally`. No delegated evidence runner was used.

## Findings and NF judgments

### QF1 / NF1 — BLOCKING: deleting the catch-up record loses durable historical silence

**In CP2 scope for the per-check path**, affects A2/A3, V2.1/V2.2/V2.4/V2.8 and composed V2.9. Global D17 shares
the problem; its occurrence is KNOWN RED on `9e94a1a`, not a CP2 regression. The per-check implementation inherits
an existing unmute mechanism without preserving its new catch-up boundary after record deletion.

Reproducer: copy [test_cp2_independent.py](cp2-round-1/test_cp2_independent.py) into a candidate archive's
`reconcile/tests/`; run `python -m pytest -vv -s reconcile/tests/test_cp2_independent.py -k nf1` using this QA venv.
The supplied runner's `final` mode performs that archive preparation and runs all probes. Nine cases fail for
new/re-enabled/global × confirmations 0/2/12:

1. Catch up an event silently; verify its incident has `notify=0, alerts=0` and no message.
2. Commit the first live run; verify catch-up record deleted / global first_live cleared. Restart.
3. Reorg below the event and catch-up boundary; re-include the event at its historical height; restore the head so
   the replacement chain is scanned and the reorg run commits.
4. The **same incident id** is unmuted to `notify=1, alerts=1`, and an `open` page is delivered for that old event.

Concrete re-enabled case with production-like confirmations=12: event `1015`, pin `1018`, completed live checkpoint
`1019`, ancestor `1014`; incident `cu_a-ev:1015-1018` becomes notifying and the message keys are `['ev:1015']`,
expected `[]`. The old head is checkpoint+12: this is a supported deep reorg within the 256-record search budget,
not an impossible unconfirmed-log case. New-check case fails identically. Global cases send `ev:1001` at all three
confirmation values. Full printed state and exact failed assertions are in the final output/per-test artifacts.

Cause: `run_check` deletes `catchup:<id>` on the first live commit (`eb_reconcile.py:742`); later `check_reorg` can
only re-arm a record that still exists (`:414`), and `Alerts._apply_one` treats a notifying re-find of a muted open
row as an unmute (`alerts.py:155`). No durable per-check history boundary remains to distinguish a historical event
from a surviving level condition. Event cursor rewind/D11 and restart both work; the false page is their composition
with record deletion/unmute. Benign controls pass: pre-first-live per-check replay remains silent at the pin and
delivers the post-pin event, and nominal next-run surviving levels send once.

D2 explicitly makes **any message for a catch-up/disabled-period event** a false page. Reorg rarity or the global
path sharing it cannot make this NOTE. Proposed repair owner: CP2 implementer, with coordinator authorization if
the fix needs `alerts.py` or a global-path change outside CP2 Allowed writes. QA does not widen writes or repair it.

### QF2 / NF2 — per-check replay PASS; global gap BLOCKING, KNOWN RED, outside CP2 implementation scope

The **per-check extension is in CP2 scope and correct**, not a severity-bearing defect: setting first_live back to
pending after a reorg below the pin preserves D4/D9's silence, stays within `eb_reconcile.py` Allowed writes, and
does not change the global phase shape. It is a justified implementation of the reorg contract beyond D9's literal
record-lifecycle description. Independent restart/reorg control passes: replayed event at `1004` silent, event at
`1005` delivered once; runs at `1004` carry backfill/force/reorg and at `1005` live/force.

The **global gap is outside CP2's implementation mandate to leave D17 unchanged**, but inside this assigned
composed assurance review. Its D2 consequence is **BLOCKING**, not NOTE. Affects A4's D17 assurance, A5's historical
silence claims, V2.8/V2.9; this is a matrix gap, not a claim that the frozen 67-case oracle regressed.

Reproducer: the same source, `-k 'nf2 and global'`. After global silent evaluation at history block `1004`, restart,
reorg that block, insert one event at `1004` and one at `1005`, restore head and step. Expected only `['ev:1005']`;
actual `['ev:1004', 'ev:1005']`, both page/open. The matching `percheck` case is the benign twin and passes.
The global case fails for exactly that message on `9e94a1a` too. Source: global phase.evaluated/first_live is not
re-armed by `check_reorg`; its next forced live run reads the rewound history cursor without muting that history.

Proposed owner: coordinator/Marco for the scope/contract decision, then an authorized implementer. Preserving a
frozen test byte-for-byte is compatible with adding coverage, but QA cannot silently authorize a global repair or
reclassify a demonstrated false page merely because the existing oracle misses it. Any owner-approved disposition
must keep the measured KNOWN RED / BLOCKING result visible.

### QF3 / NF3 — BLOCKING: a flip after a silent run inside global backfill pages the disabled interval

**In CP2's switch-composition scope**, affects A2/A4/A5, V2.2's backfill boundary, V2.6 and V2.8/V2.9. There is an
explicit contract conflict: D9's instruction to leave the backfill path untouched does not by itself satisfy A2/D2's
unconditional prohibition of disabled-period messages. This is a required matrix-gap finding, not a waiver inferred
from that implementation constraint.

Reproducer: same source, `-k nf3`. Keep check B failing its silent run so phase remains backfill; A completes its
silent run (`phase.evaluated=['cu_a']`, first_live includes A). Disable A and restart, insert its event at `1002`,
step while disabled, then re-enable and restart while B still fails. The re-enabling bootstrap clears `disabled:A`
but does not write catchup or remove its evaluated/first_live state. Its next run is live and delivers `ev:1002`.
Expected no message from the disabled interval; actual `['ev:1002']` page/open. The printed phase remains backfill,
the first_live entry was consumed and catchup is absent.

The benign sibling disables in the same setup but re-enables **after** go-live: it gets a per-check pending record,
silently processes the disabled event, and sends nothing. Thus this is not B's failure silencing A; it is lost
silence on A's switch transition. Nominal disabled-before-evaluation and disabled-then-enabled-after-go-live cases
pass and do not cover it. D2 makes the stale message BLOCKING even if it had been warn/info. Proposed owner:
coordinator to resolve the D9/backfill constraint, then CP2 implementer with the necessary scoped authorization.

### QN1 — NOTE: composed literal write-scope comparison includes plan-provisioning .gitignore change

CP2 `d7b3da6..718606c`: **11 paths**, all Allowed writes. Composed `9e94a1a..718606c`: **108 paths**, with exactly
one path outside CP1 ∪ CP2 Allowed writes plus `plans/check-switches/**`: `.gitignore`. Commit `00fe823`
(`chore(plan): check-switches run directory (plan READY, tracked during execution)`) changes `plans/` into
`plans/*` plus `!plans/check-switches/`. It predates CP1 and is coordinator plan provisioning, not an implementer
runtime change. No false page, unsafe action, secret leak or data loss follows from this narrow ignore exception,
so D2 severity is NOTE, while the literal “only Allowed writes” check is FAIL until explicitly dispositioned.
Proposed owner: coordinator; trigger: record its plan-provisioning scope exception before composed acceptance.
[Scope proof and exact diff](cp2-round-1/scope-proof.txt), [composed stat](cp2-round-1/composed-stat.txt),
[CP2 stat](cp2-round-1/CP2-stat.txt). No other NOTE is proposed.

## DF1 and prior CP1 findings

**DF1 CLOSED, PASS on the exact candidate.** Copied the original `qa/cp1-round-2/test_cp1_siblings.py` unchanged,
along with its independent sibling file, into the candidate archive. All 107 pass. QF3's exact cases pass:
current DB migration tracing for run/once/verify-layout/status (4), no DB for run/once/verify-layout (3), pre-version
DB unchanged for run/once/verify-layout/status (4). No INSERT/UPDATE/DELETE/ALTER, new DB file, version upsert,
incident/marker/outbox change occurs before the malformed-switch error. status is the read-only control;
export/audit controls also pass. C's nine permanent DF1 start cases pass on candidate and fail behaviorally on the
CP1 base (schema upsert, new DB, changed pre-version dump). `main()` validates switches before writable `DB` opens.
The CP2 README wording and handoff D14 cell reflect the corrected boundary.

CP1 QF1's malformed-switch siblings remain fixed, including mixed ids, invalid objects and bool controls. Re-derived
all incidents SQL plus helper indirection: the repaired 28-entry reader/writer table includes M1 `_reported` and
fork_e2e's sent join; predicates and any-state id reservations still match. [Inventory](cp2-round-1/incidents-readers.txt).
Export includes disabled rows; open and resolved readers exclude them. `m7` delegates to the listed M6 helper.
Real M1/M2 supplemental tests now **also exercise the CP2 silent path** after D11 replay (orphaned repair / empty
API backed by persisted tuples): silent commit clears reorg, first live run emits each surviving condition once,
old disabled rows remain byte-identical. CP1's verify-layout closure-doc note is addressed in all runbook commands.

## Results by V row

Every row was re-measured/inspected on the candidate. The extended failures below do not change nominal passing
subcase measurements. No row is NOT RUN; no delegated or implementer-only claim supplies a PASS.

| V | Result | Action, oracle/control and measured outcome |
|---|---|---|
| V1.1 | PASS | S disabled closure `[False/True]`, not-built control, digest control; 4 rows including sent page/warn/info and silent finding close with correct checkpoint/evidence/closed=4; zero closure messages/re-alerts over 26h; queued info delivered; run metadata unchanged, open counts exclude, disabled digest line. Candidate targeted/full suites exit 0. Code-base behavioral red rejects 6h re-alert. |
| V1.2 | PASS | S round-trip variants, empty/new DB marker and same-block id tests; each start writes no notice, repeated disable byte-stable, enable clears marker even backfill=false, next disable counts only new rows, zero-open closed=0. C quick-flip tests also pass nominally. |
| V1.3 | PASS | S enabled B re-alert/resolution beside disabled A, build order/example-copy, D14 bool/error cases; F example builds every check in full suite. registry.build source SHA identical at code base/CP1 base/candidate. Original independent malformed-config siblings all PASS. |
| V1.4 | PASS | S depth1/4 disabled cursor rewind, lowest ancestor across two forks, init replay, unregistered built stub/no-state controls; F scheduler reorg test in full suite. Original independent marker-only retired-id probe PASS. Red on code base is the un-rewound cursor, not missing API. |
| V1.5 | PASS | S failed marker write after UPDATE rolls everything back; next start closes once; readonly status lists/does not close; identity/RPC failure controls. Original independent bad-evidence multi-check rollback PASS. I catchup planning + D7 clear atomicity also PASS. |
| V1.6 | PASS | Re-derived 28-entry repaired reader/state table; export and metrics/open/resolved predicates and id reservations verified, all relevant candidate S tests and independent probes PASS. DF1's outstanding D14 write-boundary cell now demonstrated true by original QF3 tests. |
| V1.7 | FAIL (scope NOTE QN1) | Full suite 2,464 / ruff PASS; guarded hashes/diffs PASS; code-base 82 tests collect, 80 fail / 2 pass. Literal composed Allowed-writes check finds coordinator's .gitignore provisioning exception; no CP implementation scope breach. No passing behavioral result is relabeled by this disposition issue. |
| V2.1 | FAIL (NF1 extension) | Nominal C silent re-enable/then surviving levels/live event PASS: disabled rows untouched/new ids, forced reasons, correct record lifecycle, no own health notice. But “no message ever for disabled-period events” is false after completed catchup + deep reorg, 3 confirmations variants; QF1. V2.1 red on d7b3da6 is the forbidden burst. |
| V2.2 | FAIL (NF1/NF3 extensions) | All owned C (a)–(f) PASS: new history silent/default cursor; upgrade no forced run; global no record; disabled gate false alerts/clears marker; stale first_live removal; disable-before-evaluation then re-enable-after-live. New check NF1 shows silence lost after record deletion; previously evaluated backfill check NF3 is the omitted switch boundary. |
| V2.3 | PASS | C RuntimeError/RpcError/restart boundaries and same-block retries PASS. I 6 stage×failure controls (pending/first_live × runtime/rpc/partial generator) PASS: no partial incident/outbox, pin survives, error record not evaluated, first live failure retains record, restart retries forced once; independent B live page in same failed step. |
| V2.4 | FAIL (NF1 depth/lifetime extension) | Required disabled-reorg nominal C test PASS: on_reorg init replay, correct ancestor/new chain only, reorg marker cleared on successful silent commit, new-chain still-true live once. I real M1/M2 PASS. Pre-first-live percheck reorg PASS. After first-live record deletion, deep reorg replays historical events live (QF1). |
| V2.5 | PASS | C new/reenabled catching check fails 3 times while A's live events all send in their own steps; pin stable, eventual silent/live same-step recovery correct. I 6 failure tests enforce unrelated live delivery. Frozen B global tests (live-event-while-another-blocks, M13 top-up/drain, transient RPC retry, seeded controls) all PASS. |
| V2.6 | FAIL (NF3 phase extension) | Nominal C no-step flips, no-new-block catchup/new id, readonly CLI pending/first_live/absent fields PASS; no message/duplicate closure in nominal flips. I flip inside backfill after prior silent run sends disabled event; after-go-live benign twin PASS. |
| V2.7 | PASS | Frozen test_backfill SHA identical and 67 green; CP1 S 82 green; C 69 green; full suite/ruff green; CP2 11 paths all allowed; config _comment-only and checks README §3/4/7 only; no phase key-shape change. Behavioral reds on CP1 base reproduced. Additional global KNOWN RED does not allege a regression in this frozen oracle. |
| V2.8 | FAIL | Independently mapped every assigned runbook/README/comment claim to candidate tests or source facts in [docs-map.md](cp2-round-1/docs-map.md). Nominal lifecycle/visibility/gates/Monitor/sub-switches/metrics backed; unconditional historical/disabled-period silence contradicted by NF1/NF2/NF3. All 715 tracked textual files scanned; no actual secret found, 10 fixture hits reviewed/redacted. |

V2.9 composed verdict: **FAIL**. A6's PASS/PASS WITH NOTES prerequisite is not met. The coordinator owns checkpoint
status and any decision on repairs or dispositions; this report grants no waiver or acceptance.

## State attacks, regression effectiveness and scope

Attacked classes 1, 2, 10 and 12 on catchup before accepting implementer tests: empty successful evaluation,
partial generator/error rollback, pending and first_live failures, new-check run without prior completed block,
durable pin across same-block/restart/RPC errors, catchup planning versus D7 marker clear transaction, stale
phase.first_live removal, D11 replay while disabled, depth beyond confirmations, and record lifetime after deletion.
The six independent partial/error cases and empty/transaction controls PASS. The lifetime/reorg attacks find the
three issues above. Original CP1 empty/partial/identity/malformed sibling controls all pass. Time anchoring remains
checkpoint/pin for evaluation, clock for informational since/disable timestamps and re-alert cadence; same-height
new incident ids and outbox keys remain unique in the nominal switches/sweep tests. The inherited global path keeps
other checks live despite one failing (frozen oracle and independent failure controls PASS); NF3 changes the disabled
check's silence, not that isolation result.

Independent defective-revision controls are behavioral: on `d7b3da6`, V2.1 first assertion sees
`['ev:1004', 'ev:1006', 'lv1', 'lv2']`; V2.2(a) sees `['ev:1003', 'ev:1004', 'ev:1005', 'ev:1006', 'lv1']`,
both expected `[]`. Two CP2 tests fail only because no catchup record exists on base; excluded as effectiveness
evidence. Original V1.1 fails on `sw_a-kpage-1001:realert:1` at clock+6h02m; V1.4 depth1 is
`[1004,2147483648] != [1002,2147483648]`, depth4 `[1007,2147483648] != [1005,2147483648]`.
Import-only new-symbol failures in the broader red files are likewise not used as proof. Passing baseline cases are
controls, not hidden failures. Global NF1/NF2 are independently red on both candidate and code base for the same
history-message reason. C's seeded sweep explicitly excludes reorgs below deleted catchup boundaries, so its green
seeds cannot establish NF1 safety.

`test_backfill.py` SHA256 is `28da7d49e950a0bdc51ee7d4f6993ad757fa72d60524c39a7c28efcc24702d35` at all three
revisions. Guarded paths (`db.py`, `metrics.py`, `harness.py`, checks/m*.py, test_m*.py, test_backfill.py) are unchanged.
registry.build bytes match; example JSON differs only in _comment; checks README diff stays §3/4/7. Phase keys remain
phase/since/evaluated/first_live/history_block/caught_up_block; only existing first_live entries are removed in CP2.
[Scope proof](cp2-round-1/scope-proof.txt), [empty guarded diff](cp2-round-1/guarded.diff),
[CP2 paths](cp2-round-1/CP2-paths.txt), [composed paths](cp2-round-1/composed-paths.txt).

## Limits, corrections and restoration

No mutation campaign or other slow-check campaign, no external/library documentation query, no public-chain call,
no Docker/deployment smoke, no external alert, no push, no mainnet transaction, and no helper agent was used.
Only the requested suite, bounded wrong-revision comparisons and targeted adversarial cases were run. Implementer
mutation/falsifier logs were read but not rerun under D2. Actual deployed production backfill value and endpoint
historical retention remain delivery-smoke bindings for the coordinator, not claimed as measured here.

Initial independent evidence is retained, not overwritten. Four global cases first hit a QA setup issue: with a
single large scan window only its endpoint hash had been recorded, leaving no recorded common ancestor. The final
global setup uses max_log_range=1 so the existing reorg contract can be exercised, and all four now fail on history
messages. Initial real-M2 probe incorrectly expected one total surviving-condition message; M2 correctly reports both
hard:1 and early:1. Corrected independent expectation requires both distinct keys once, and passes. Neither change
alters candidate code or an agreed oracle. [Correction record](cp2-round-1/qa-setup-corrections.txt), original
[independent output](cp2-round-1/cp2-independent.txt), original [real-check output](cp2-round-1/real-check-composition.txt).

No required measurement is unexecuted. Required claims not established are the demonstrated silence/lifetime/phase
guarantees and the literal composed write-scope rule identified above. The global failures' pre-existing provenance
does not erase their results. No secret found by a static heuristic scan is not a cryptographic guarantee; all 715
tracked files were textual, all 10 pattern hits were fixture/error-test values, with no candidate-added credential.
[Redacted scan](cp2-round-1/secret-pattern-scan.txt), [manual classifications](cp2-round-1/secret-review.txt).

Only this report and `qa/cp2-round-1/` evidence were written in the audited tree, plus the authorized ignored venv.
All archive scratch directories/test data were removed ([cleanup](cp2-round-1/scratch-restoration.txt)).
[Candidate restoration diff](cp2-round-1/candidate-restoration.diff) is empty outside these QA paths;
[write inventory](cp2-round-1/qa-write-inventory.txt) records only authorized evidence/report paths. No shared plan,
state, log, implementation, test, config, user-owned directory or existing volume was edited. The branch and commit
contain only the authorized report/evidence paths and are not pushed. Next: coordinator dispositions/repair decision
for QF1–QF3 and explicit ownership of QN1; this QA starts no further checkpoint.

Co-Authored-By: GPT-6.1 Sol <noreply@anthropic.com>
