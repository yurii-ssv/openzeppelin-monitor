FAIL

CP2 composed independent QA, round 3 / V2.9, 2026-11-06. Audited candidate
`4205b00d510e3e2e84ccd370836473d2b20d7855`, repair-2 base
`ff3841ffd1bfc2b7b6478f8c3941aefaa5d27833`, CP2 base `d7b3da6`, composed code base `9e94a1a`.
**QF4 is closed. All earlier BLOCKING findings remain closed. A required additional M5 suffix/reorg probe fails:
QN3, a missing info resolution, classified NOTE under D2.** No missed/false page was demonstrated in this round.
The FAIL preserves the required composed probe's actual result; the QA role forbids passing an unresolved required
failure. Its severity remains NOTE, and the coordinator owns D2 disposition/acceptance rather than QA converting
the failed assertion to a pass or granting another repair.

Preflight HEAD matched exactly and the tree was clean. Repair 2 is one commit whose parent is exactly repair 1.
Orca's initial branch was `mtabasco/cs-cp2-qa3`; only the report branch `cs/cp2-qa3` was created. Role/catalog were read
first and hash-verified: `ec15481953f5defa91e6da9089b51a7e7ed53a8a54d8554ac6f970ba64c35cbf` /
`7daa55f69bdd3218f83283a0bd752075a84141888196869977c046819def5876`. Read CP1 original/repair handoffs, CP2
original/repair-1/repair-2 handoffs and evidence, earlier QA reports/probes, reader/state tables, docs checklists,
and relevant code/tests. The round-2 report retrieved from `plan/check-switches` hashes to
`a026b88f340108db8d43aaf7342fedf31548df11967391f8ec4c64c4e70ef9e8` as dispatched.
[Identity](cp2-round-3/identity.txt), [source facts](cp2-round-3/source-facts.txt),
[Orca identity](cp2-round-3/orca-receipt.json).

Requested model/effort: Codex GPT-6.1 Sol high. Orca confirms `codex/gpt-6.1-sol`, this worktree and this dispatch;
worker-list exposes no effective effort, so the coordinator's start receipt remains the authority for that gate.
Environment: macOS 26.7.1 arm64, Python 3.12.15, pytest 9.1.1, ruff 0.17.1, uv 0.13.4; Orca 1.4.250 version-matched
guides. Every shell prefixed `$HOME/.foundry/bin`. Only the authorized venv/dev-requirements install was performed.
Orca identity initially hit sandbox runtime-access denial; the authorized unrestricted read succeeded without
restarting Orca. Full-suite local socket tests ran outside the sandbox on their first invocation.

Measurements on the exact candidate (zero skipped in every listed pytest run):

| Action | Exit / result | Evidence |
|---|---|---|
| Full `reconcile/tests` suite | 0; **2519 PASS** (2505 + 14 repair-2 cases) | [suite](cp2-round-3/suite.txt), [per-test](cp2-round-3/suite-per-test.txt) |
| `ruff check reconcile` | 0; all checks passed | [ruff](cp2-round-3/ruff.txt) |
| S82 + C69 + H41 + B67 + IDs14 | 0; **273 PASS** | [targeted](cp2-round-3/targeted.txt) |
| Original round-2 `test_qa2_deep.py`, including real-M5 QF4 | 0; **28 PASS** | [output](cp2-round-3/qa2-deep.txt), [per-test](cp2-round-3/qa2-deep-per-test.txt) |
| Round-1 reproducers, approved round-2 NF3 setup, unchanged message oracles | 0; **21 PASS** | [output](cp2-round-3/cp2-independent.txt) |
| Original CP1 independent + sibling probes, including DF1 | 0; **107 PASS** | [output](cp2-round-3/cp1-independent.txt) |
| Original real M1/M2 disabled reorg + re-enable | 0; **2 PASS** | [output](cp2-round-3/real-composition.txt) |
| New independent D16 boundaries | 1; **9 PASS / 1 FAIL** (QN3) | [output](cp2-round-3/qa3-boundaries.txt), [per-test](cp2-round-3/qa3-boundaries-per-test.txt) |
| Final QN3 diagnostic, real re-enable and global no-flip sibling | 1; **2 FAIL**, same missing-resolution assertion | [output](cp2-round-3/m5-reorg-diagnostic-verified.txt) |
| QN3 global no-flip sibling on repair-1 `ff3841f` | 1; **1 FAIL**, same behavioral reason | [output](cp2-round-3/m5-reorg-prior-verified.txt) |

S/C/H/B/IDs denote `test_switches.py`, `test_catchup.py`, `test_history_silence.py`, frozen `test_backfill.py`, and
`test_switch_ids.py`. No implementer execution log was substituted for a measurement. The archive runner uses
`pytest -vv -s` to retain individual evidence for the same required test set; its own collection exit is not a
pass gate: child exits are in each log, XML and [summary](cp2-round-3/measurement-summary.txt).

**QF4 — CLOSED.** The original independent real-M5 reproducer passes, unchanged. The old row remains byte-identical
and `disabled`; catch-up commits silently, the first later live run sends exactly one page/open with `<old>-2`,
the record disappears, retries/restart do not duplicate, and no check-error occurs. Its control on `ff3841f`
remains red: `surviving M5 condition missed its first-live page`, zero messages and the original duplicate-id
ValueError. [Original faulty-head run](cp2-round-3/qf4-red.txt).

D16 boundaries were remeasured beyond that reproducer: IDs14 rejects proposed ids held by open/resolved rows,
skips occupied `-2` regardless of state, preserves disabled bytes, rolls back failed allocations, and takes `-3` on
a second real round trip. Independent cross-check-id collisions preserve the same open/resolved rejection and full
dump; batches with two/five prior disabled ids skip every suffix, and a later collision rolls back the earlier
allocation and its outbox. M5 confirmed/update, latent, candidate-to-confirmed and resolve paths were exercised:
latent re-tracks silently then escalates, a candidate emits nothing on ordinary UNKNOWN then opens a suffixed page
when confirmed, a healed disabled-period condition opens/sends nothing, and a live resolution addresses the new
framework id. Candidate fresh-id allocation still checks both tables; the existing-candidate UNKNOWN path proposes
no id. These source paths were distinguished rather than claiming every branch always proposes an id.

The handoff's missing **real-M5 D11 + UNKNOWN re-enable** case is now measured: a canonical confirmed condition
survives below the fork; a new-chain registration's logs are omitted while its storage changes remain, producing
real model UNKNOWN (hash mismatch/replayed-vUnits mismatch). While disabled, the ancestor marker persists; enabling
runs actual M5 and silently creates `<old>-2` with UNKNOWN body, commits first_live, and leaves the disabled row
untouched. After restart and complete canonical replay, one page/open is sent under that suffix with no
check-error. Input fault injection for the separate candidate-path probe changes only the evaluation input result
in scratch, not the implementation or message oracle. All these probes assert unique outbox keys.

**QN2 — OPEN NOTE, original observation remeasured.** All six original clean/latent/confirmed × retained/orphaned
real-M5 deep-repeat probes pass their message oracles. The latent/retained case still splits M5's own id from the
framework id without an extra/lost message in that particular scenario. D16's accepted additional split is also
measured: M5 keeps `<old>`, framework/messages use `<old>-2`; normal resolution reaches the framework row by key.
Owner proposed: coordinator, assign M5 lifecycle owner; revisit trigger: QN3 disposition or any operator workflow
that joins these tables by id. This is not a general proof of “no message impact” for all silent-repeat siblings:
QN3 below demonstrates an omitted one. A differing id alone is not established as its cause.

**QN3 — NEW NOTE: a live M5 page receives no resolution when a silent repeat changes its own confirmed state to
latent.** Affected A2/A4/A5/A6 composition; V1.6 state-table completeness, V2.4 expanded real-check/reorg boundary,
V2.8 supporting coverage, V2.9. Severity is NOTE under D2 because the demonstrated missing message is an **info
resolution of an already-sent live page**, outside historical/disabled-event false-message cases. After the repeat
the framework row has severity info, so it does not re-page, even after seven hours; no false clean, lost page or
false page is claimed. Owner proposed: coordinator with M5/CP2 lifecycle implementer; trigger: disposition of this
required composed probe before acceptance, with Marco's authorization before any check-code repair.

Expected oracle: M5's explicit confirmed-to-latent reorg semantics, documented in its module and independently
pinned by unchanged `test_reorg_refind::test_m5_reorg_removing_only_the_confirming_removal_resolves_plainly_and_tracks_latent`
(PASS in this candidate's full suite): resolve the unsupported confirmed incident once, plainly as reorg evidence
lost, then track latent silently. The new probe extends that same message oracle across a D15 silent repeat after
D16 re-enable. No implementation-derived state was used to decide whether confirming evidence survives: the replay
retains the registration/root/update but removes the confirming removal's logs AND storage writes.

Exact reproducers: [test_qa3_boundaries.py](cp2-round-3/test_qa3_boundaries.py),
`test_real_m5_reorg_around_suffixed_incident[False-live]`; final diagnostic
[test_qa3_diagnostic.py](cp2-round-3/test_qa3_diagnostic.py), both parameters. Execute
`reconcile/.venv/bin/python plans/check-switches/qa/cp2-round-3/run_archives.py new` (9 pass/1 fail) or `diagnostic`
(candidate 2 fail; prior-head global control 1 fail). Runner creates/removes all candidate copies under `/private/tmp`.

1. Establish a real live confirmed M5 page; disable, enable, silent catch-up, then page/open under
   `EB94775-228316cb1e-1005-2` at the first later live run.
2. Restart; reorg below the re-enable pin, keeping the latent register/root/update but orphaning the confirming
   removal. A supported replay and a pre-first-live orphaned replay are benign passing twins.
3. The silent repeat closes M5's own original confirmed row and creates its latent row under `…-3`, but the
   framework deliberately defers applying that resolution to protect the later live page. The forced live run now
   sees only M5's already-latent own state, reports an info finding for the same key, and silently downgrades the
   still-open framework `…-2`; it no longer emits the explicit resolution.
4. Actual new messages: **none**, expected one info/resolved for `…-2`. Catch-up/reorg markers clear with errors=0;
   the old disabled row is untouched, outbox keys stay unique, and the framework row is `open/info/notify=1/alerts=1`.
   Seven hours sends no re-alert; a later clean root finally sets it resolved but still sends no resolution, because
   its current severity is info. Thus this is a durable missing info, not a delayed delivery.

The global no-flip sibling independently uses a latent global historical pin, a later live confirming removal,
and a deep replay retaining latent support. It fails identically on this candidate and on `ff3841f`: the defect
predates D16 and does not require suffixed ids. Cause is the composition of `run_check`'s deferred-key filter
(`eb_reconcile.py:775–800`) with M5's own transition (`m5_eb_desync.py:320–332`): the silent pass updates the check's
state but suppresses its framework resolution, so live reevaluation cannot reconstruct that transition.
This is DEFECT-CLASSES 2/10/12 sibling coverage missing from the prior clean-versus-wholly-orphaned QN2 probes.
No oracle was changed and no implementation repair was made.

**QN4 — NEW NOTE, wording outside repair scope.** `checks/README.md` §6.1 still says a proposed id “must still be
unique,” without a D16 cross-reference; M5's unchanged module doc says the two incident rows share the id. §4 now
correctly specifies the disabled-id exception and possible differing ids. These older phrases need contextual
cleanup, not a behavior change; collisions with open/resolved remain rejected. Owner proposed: coordinator/docs
owner; trigger: closure documentation pass. The module is an excluded check file: QA did not edit it. This does
not itself turn V2.8 red; QN3's missing backing lifecycle behavior does.

Earlier finding results remain separately measured:

| Finding | Result |
|---|---|
| QF1/NF1 | CLOSED: original 9 new/reenabled/global × confirmations 0/2/12 deep-reorg history-message oracles PASS. |
| QF2/NF2 | CLOSED: global repeat sends only above-boundary live events; per-check control and held-backfill variants PASS. |
| QF3/NF3 | CLOSED: original disabled-interval message oracle PASS with the approved D15(c) setup, plus after-live control. |
| DF1 / CP1-QA2 QF3 | CLOSED: all 107 original independent/sibling probes PASS, including current/fresh/pre-version writable DB paths. |
| CP1 QF1/QF2 | CLOSED: malformed switches reject without writes; complete reader inventory includes both any-state JOIN readers. |
| QN1 | Disposed provisioning exception, closure action OPEN: `.gitignore` still differs literally; coordinator restores `plans/` at closure. Owner coordinator, trigger closure commit. |

One result per V row; counts/exits above are measured here, not inherited acceptance:

| Row | Result | Action / independent oracle and control |
|---|---|---|
| V1.1 | PASS | S both gates closes sent page/warn/info and silent rows, checkpoint/evidence/closed=4, zero closure notices/resolutions and no re-alert through 26h; queued row delivered; run metadata unchanged; status/digest/open exclude. Not-built control keeps rows open. Named base red catches 6h re-alert. |
| V1.2 | PASS | S repeated-start/roundtrip/zero-marker/same-block cases; no startup messages, marker unchanged on repeated disable, cleared on enable even gate=false; next transition counts only new rows. |
| V1.3 | PASS | S enabled sibling re-alert/resolve/order/example-copy; F example-build; all independent malformed CLI siblings including DF1 PASS. `registry.build` source hash unchanged. |
| V1.4 | PASS | S depth1/4/minimum ancestor/stateless-id/unregistered-stub, CP1 marker-only retired id, original real M1/M2 composition; disabled cursors rewind and markers replay. Behavioral frozen-code red retained. |
| V1.5 | PASS | S injected post-UPDATE/pre-marker rollback and readonly status; CP1 independent bad-evidence multi-check rollback; D7/catchup planning transaction rollback. No partial closure or readonly write. |
| V1.6 | PASS | Re-derived production SQL and both any-state JOIN readers; compared corrected 28-reader table plus repair-1/2 state cells. D16 proposed/held state cells now backed by real M5 and rule probes; readonly/export/metrics states PASS. QN3 records the additional own-state/framework repeat gap; no id-reader omission remains. |
| V1.7 | PASS | Full2519, ruff0, S82/B67 green; frozen SHA identical, guarded diff empty; S on code base 80 FAIL/2 PASS with required named behavioral reds. QN1 disposed exception recorded. |
| V2.1 | PASS | C nominal silent/history/healed/still-on/new-id assertions, NF1, QF4 real-M5 all PASS; D16 confirmed/latent/candidate/resolve controls PASS, no switch notices or duplicate first-live page. |
| V2.2 | PASS | C (a)–(f) new/history/upgrade/global/gate=false/stale-first_live/disabled-during-backfill; H sibling re-enable; NF1/NF3 controls. New check history silent, existing completed check not replanned, no forced upgrade message. |
| V2.3 | PASS | C runtime/RPC/restart/same-block; original partial-yield/rollback probes; QA2 repeat/forced-live errors both paths all PASS, independent batch rollback deterministic, suffix survives restart and keys unique. |
| V2.4 | FAIL (QN3, NOTE severity) | Original C/real-M1/M2 and QA2 deep M1/M2/M5 PASS; new real-M5 UNKNOWN, retained suffix replay, and orphaned replay before first-live PASS. After a suffixed live page, orphan only confirming removal while retaining latent support: required explicit M5 resolution is lost; 9/10 new cases PASS, one fails. |
| V2.5 | PASS | C live-A/failing-B both causes, H failure repeats, QA2 12 partial/runtime/RPC repeat/live controls assert other check's same-step page. Recovery remains pinned and sends once. |
| V2.6 | PASS | C no-step/no-block flips, second closure and readonly catchup CLI; H held-backfill quick flips. Extra M5 rounds/restarts preserve disabled rows and unique keys. |
| V2.7 | PASS | B67 same SHA, S82 unchanged, C69/H41/IDs14/full2519/ruff green; allowed union scope verified. C69 on CP1 has40 FAIL/29 PASS; named V2.1/V2.2(a) forbidden burst assertions behavioral. Phase shape unchanged. |
| V2.8 | FAIL (QN3 backing behavior) | Actual runbook/README/comment claims mapped to rerun tests/source facts in docs-map.md. Disable/re-enable/new-id/boolean/metrics/Monitor/sub-switch claims PASS. Composed M5 explicit reorg resolution guarantee lacks passing backing in QN3 sibling; QN4 is a separate wording NOTE. No tracked credential observed in bounded redacted scan. |

V2.9 / A6 verdict **FAIL**, without reclassifying NOTE as BLOCKING or erasing the passing V rows. Proposed next
owner is the coordinator: present the exact missing-info result for disposition under D2 and the strict QA report
rule. This report does not authorize a third repair or broaden Allowed writes.

Scope and effectiveness: repair has 9 paths, CP2+repairs 39, composed 135. The repair's non-record paths are
alerts.py, new test_switch_ids.py, and checks README §4. The code diff also extracts `_id_state` beside
`_id_taken`, which delegates to it: a lookup-only helper for the authorized allocation, not an unrelated behavior
change; the Boolean id-reservation result is preserved. No repair README hunk leaves §4. Composed production/test
paths are within CP1 ∪ CP2 ∪ approved repairs, with the literal QN1 provisioning exception. Guarded code/tests,
db.py, metrics.py and harness.py are unchanged; config example differs only in `_comment`. Frozen backfill SHA is
`28da7d49e950a0bdc51ee7d4f6993ad757fa72d60524c39a7c28efcc24702d35` at all measured revisions, and
registry.build hashes match. [Scope proof](cp2-round-3/scope-proof.txt), [repair diff](cp2-round-3/repair.diff),
[composed paths](cp2-round-3/composed-paths.txt), [guarded diff](cp2-round-3/guarded.diff),
[reader inventory](cp2-round-3/incidents-readers.txt), [docs map](cp2-round-3/docs-map.md),
[redacted scan](cp2-round-3/secret-scan.txt).

Wrong-revision controls were rerun, not reused: S82 on `9e94a1a` =80 FAIL/2 PASS; C69 on `d7b3da6` =40 FAIL/29
PASS; NF21 on `718606c` =11 FAIL/10 PASS; QF4 on `ff3841f` =1 FAIL. Named failures are the intended 6h re-alert,
unrewound depth1/4 cursor, disabled-period burst/history event, deep-replay historical message, and missed M5 page;
missing-symbol/setup-only failures elsewhere in the broad files are not counted as behavioral proof.
[Exact named assertions](cp2-round-3/named-red-assertions.txt). DEFECT-CLASSES 1/2/10/12 attacks include partial
findings/errors/empty success/atomic pin writes, UNKNOWN storage-log mismatch, resolved/orphaned keys, restarts at
all boundaries, same-block rollback/order, repeated ids, and the seeded C/S sweeps. No mutation campaign was run.

Initial diagnostic-only setup failures are retained, not hidden: the first no-flip diagnostic manually seeded a
silent floor, then the public global-path control replaced that setup; both first diagnostic variants attempted
`.advance` on this M5 helper's ordinary clock function. The final source uses a fixed seven-hour clock wrapper,
and the public global path/repair-1 comparison now fail only the unchanged missing-info assertion. Earlier
outputs/XML and source snapshots are kept as setup history, not additional candidate defects or acceptance proof.
The original 10-case boundary file never had that setup problem and independently fails the same message oracle.

No mutation/slow/performance campaign, helper worker/subagent, public-chain RPC, mainnet transaction, Docker,
deployment smoke, external notification, push or Slack/GitHub post. External production backfill settings and
endpoint historical availability remain coordinator delivery-smoke bindings. Untested real m3–m14 combinations
are outside this bounded sample, not an exhaustive claim. All required measurements ran; the outstanding
acceptance claims are QN3's required reorg lifecycle/control and supporting docs coverage, with NOTE severity.

Only this report and its evidence directory were written, plus authorized ignored venv/tool caches; every
experimental source/test database stayed in a disposable `/private/tmp` archive and was removed in `finally`.
[Scratch restoration](cp2-round-3/scratch-restoration.txt), [restoration diff](cp2-round-3/candidate-restoration.diff),
[QA inventory](cp2-round-3/qa-write-inventory.txt). No implementation/test/config/shared plan/state/log or prior
report was edited. Commit only these QA paths on cs/cp2-qa3, never push; the coordinator owns all next decisions.

Co-Authored-By: GPT-6.1 Sol <noreply@anthropic.com>
