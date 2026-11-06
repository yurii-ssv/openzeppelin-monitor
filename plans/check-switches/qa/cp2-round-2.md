FAIL

CP2 composed independent QA, round 2 (V2.9), run `run_3869f8caafaa`, task `task_f4d7167aa1e3`, dispatch
`ctx_5ba74e966d55`, 2026-11-06. Candidate **`ff3841ffd1bfc2b7b6478f8c3941aefaa5d27833`**, repair base
`718606c5ee2d43c260240101c06642340cabc1a0`, CP2 base `d7b3da6a353b2e5f54ff95af00d099cacf291e4c`, code base
`9e94a1a`. I implemented none of the candidate and repaired nothing. **The three round-1 findings are closed,
but a real M5 disable/re-enable loses the surviving condition's page (QF4, BLOCKING).** No acceptance or waiver is
granted by this report; this is the second failed CP2 QA round, requiring Marco's decision before further repair.

Preflight HEAD matched exactly and `git status --porcelain` was empty before any QA write. Orca supplied branch
`mtabasco/cs-cp2-qa2`; the report is committed on the requested new branch `cs/cp2-qa2`, without moving the candidate
or pushing. Role SHA256 `ec15481953f5defa91e6da9089b51a7e7ed53a8a54d8554ac6f970ba64c35cbf`, defect classes
`7daa55f69bdd3218f83283a0bd752075a84141888196869977c046819def5876`, round-1 report `6d23504a…` verified.
Read original and repair CP1/CP2 handoffs, repair evidence/state table/docs checklist, original independent
reproducers and CP1 QA reports via the pinned plan ref. D2 and D15 in the dispatch are the policy/oracle; the
approved backfill-phase catchup record mechanism is accepted, not mistaken for a defect.
[Identity and hashes](cp2-round-2/identity.txt), [Orca worker identity](cp2-round-2/orca-receipt.json).

Requested QA model/effort: Codex GPT-6.1 Sol high. Orca's worker projection confirms `codex/gpt-6.1-sol` at this
worktree/dispatch; its worker-list API does not expose `launch.effective` effort, so high is taken from the dispatch,
not represented as independently measured. The coordinator owns the start receipt/model gate. Environment:
macOS 26.7.1 arm64, QA Python **3.12.15**, pytest 9.1.1, ruff 0.17.1, uv 0.13.4; every shell prefixed PATH with
`$HOME/.foundry/bin`. Only the authorized venv/dev-requirements install was performed. Initial uv cache permission
failure was followed by the authorized escalated install; no package outside the specified requirements was added.

**Measurements.** Full suite: **2505 PASS, 0 skip, exit 0**, ruff PASS exit 0. The first sandboxed suite was
2495 PASS / 10 local-server socket-bind PermissionErrors; the authorized unrestricted rerun passed all 2505.
Both outputs are preserved, not silently retried away. Targeted S+C+H+B: **259 PASS** = switches 82, catchup 69,
history-silence 41, frozen backfill 67. Original CP1 independent probes including DF1: **107 PASS**. Original
round-1 CP2 probes: **21 PASS** = repaired 11 failures + 10 controls; real M1/M2 D11 composition: **2 PASS**.
New independent adversarial probes: **27 PASS / 1 FAIL**, plus a separate restart confirmation of the failing M5
case. The repository suite's green result does not cover that failed real-check interaction.
[Full suite](cp2-round-2/suite-unrestricted.txt), [initial sandbox suite](cp2-round-2/suite.txt),
[ruff](cp2-round-2/ruff.txt), [targeted](cp2-round-2/targeted.txt),
[CP1](cp2-round-2/cp1-independent-per-test.txt), [CP2](cp2-round-2/cp2-independent-final-per-test.txt),
[original real-check probes](cp2-round-2/real-check-composition-per-test.txt),
[new probes](cp2-round-2/new-probes-complete-per-test.txt), [M5 restart proof](cp2-round-2/m5-restart-proof.txt).

**QF4 — BLOCKING: M5 retains the disabled framework incident's id and can never complete re-enable catch-up.**
Affected A2, A5, A6; V1.6, V2.1, V2.8, V2.9. D2 severity is a missed page for a condition still true after re-enable,
not a warn/info NOTE and not a transient RPC outage. Expected: silent catch-up creates a new incident id, then one
`page/open` at the first later live run, leaving the disabled row untouched. Actual: zero incident messages after
re-enable; the durable catchup record stays `pending`, pinned at 1010; attempts at checkpoints 1010, 1011 and 1012
(including a fresh worker restart) fail with
`ValueError: incident id EB94775-228316cb1e-1005 already exists; a check must propose unique ids`.
`run:m5.errors` rises, the old row stays disabled, and only `check-error` health appears. That health message does
not send the surviving condition's page.

Reproducer: [test_qa2_deep.py](cp2-round-2/test_qa2_deep.py),
`test_m5_real_disabled_key_gets_new_id_and_first_live_page`, run in a disposable candidate archive by
`reconcile/.venv/bin/python plans/check-switches/qa/cp2-round-2/run_archives.py guard-real`.
It uses real M5 and MiniSSV: establish global live mode with one real validator and a clean root; in live mode add
63 dummy validators, consume the root, remove them, and observe one M5 page for the confirmed shortfall. Restart
with `m5.enabled=false` (the page's framework row becomes disabled), then true, retaining the real shortfall. The
silent attempt and later retry/restart never send a new page. Behavioral assertion fails explicitly:
`surviving M5 condition missed its first-live page`, `len(actual) == 0` rather than 1.
Before the flip the positive control sends exactly one genuine page; the 6 new real-M5 deep-reorg probes also
successfully retain or resolve canonical support. This isolates the failure to disabled-id reuse, not an inert
fixture. [Candidate details](cp2-round-2/m5-restart-proof.txt),
[also FAIL on 718606c](cp2-round-2/m5-prior-candidate.txt).

Cause: `Worker._apply_switches` (`eb_reconcile.py:233–259`) closes only framework `incidents`, while M5's own
`m5_incidents` remains open. `M5EbDesync._transition` (`m5_eb_desync.py:373–389`) reuses that own row's existing id
in the finding; `_new_id`'s any-state reservation loop at line 310 is not called. `Alerts._apply_one`
(`alerts.py:129–132`) correctly rejects the already-taken disabled framework id and rolls back the silent run.
The CP1 reader table's “M5 id stays taken / same as _id_taken” cell is syntactically true but does not establish the
required new-id lifecycle. This is an untested sibling path / state-table gap (DEFECT-CLASSES 10 and 12) across the
composed feature, already present on round-1 CP2, not caused by the D15 repair. No checks/m*.py edit or other repair
was attempted. Owner for disposition/scope: coordinator with Marco (check-code changes require his decision).

**QN2 — NOTE: an M5 silent repeat can split internal and framework ids.** In the real supported/latent-boundary
repeat, `m5_incidents` resolves the original `EB94775-…-1004` internally at the historic pin and opens `…-1004-2`,
while the deferred framework row remains open under the original id. No extra or lost message was observed in the
supported/orphaned repeat probes; the canonical supported page remains open and the orphaned page resolves once,
so the observed bookkeeping discrepancy is a NOTE under D2. It is not used to downclassify QF4, which has its own
missed page. Evidence: `test_m5_real_state_after_global_repeat[True-latent]` printed original/framework versus
internal ids in [new-probes-complete.txt](cp2-round-2/new-probes-complete.txt). Owner for coordinator confirmation: coordinator (assign the M5 lifecycle
implementer); revisit trigger: Marco's QF4 repair decision, before any claim that M5's two id lifecycles
remain synchronized through a silent repeat. Do not broaden this observation into a general missed-page claim
without a falsifier.

**Earlier finding dispositions measured on this candidate.**

| Finding | Result and evidence |
|---|---|
| QF1 / NF1 (9 new/reenabled/global × confirmations 0/2/12) | CLOSED: all 9 original message oracles PASS after record deletion/first_live consumption and restart/deep reorg. Replayed incident stays muted. |
| QF2 / NF2 global | CLOSED: original global pre-first-live replay sends only the above-boundary event; percheck control still PASS. New global/backfill repeat controls also PASS. |
| QF3 / NF3 | CLOSED: disabled-interval event stays unsent on re-enable during held global backfill; after-live benign twin PASS. The old “no record” setup line is superseded by the explicitly approved D15(c) mechanism. Final test snapshots phase before the run and checks removal after the unchanged message oracle. |
| DF1 (CP1-QA2 QF3) | CLOSED: all 107 original CP1 independent/sibling probes PASS, including writable current/fresh/pre-version run/once/verify-layout paths and read-only controls. |
| QN1 (.gitignore) | Disposed by packet as coordinator plan-provisioning exception; no implementation scope breach. Its closure action remains OPEN: coordinator restores plans/ at closure. Owner coordinator; trigger closure commit. Literal composed diff still contains it; no claim it was reverted here. |

The final original reproducers on **718606c** still produce **11 FAIL / 10 PASS** for the behavioral reasons:
forbidden history messages after deep reorg, global history replay, and disabled-interval event `ev:1002`.
The same 21 cases on the candidate all PASS. The approved setup correction does not weaken a message oracle.
[Red assertions](cp2-round-2/round1-repro-red-final-per-test.txt),
[correction record](cp2-round-2/qa-setup-corrections.txt).

**V-row results (S/C/H/B/F as defined above; all named repository tests run on this exact candidate).**

| Row | Result | Action, oracle / control and evidence |
|---|---|---|
| V1.1 | PASS | S closure both backfill values: sent page/warn/info and silent incident all disabled with block/evidence/closed=4; zero closure messages/re-alerts through 26h; undelivered info delivered, run metadata unchanged, open/status/digest exclude. Not-built control keeps open rows. Targeted/full exit0; behavioral code-base red catches 6h page re-alert. |
| V1.2 | PASS | S repeated starts/roundtrips/zero-marker/new-DB/same-block ids; every start sends zero notices, repeated disable stable, enabling marker cleared even gate=false, next disable counts only new rows. Independent planning rollback PASS. |
| V1.3 | PASS | S enabled-B re-alert/resolve, registry order, example-copy, boolean/error controls; F example-build; I all malformed CLI siblings PASS. registry.build bytes identical at code base/CP1/candidate. |
| V1.4 | PASS | S depth1/4 disabled cursor/minimum ancestor/init replay/no-state/unregistered-stub controls; F scheduler cursor test; I retired marker-only id and real M1/M2 composition PASS. Red-on-code-base cursor assertion is behavioral. |
| V1.5 | PASS | S failed marker write after UPDATE rolls back; next start closes once; readonly status closes nothing; I bad-evidence multi-check rollback and D7+catchup planning atomicity PASS. |
| V1.6 | FAIL (QF4) | Re-derived incident-reader SQL inventory, compared corrected CP1 28-entry table and repair state table; S export/metrics terminal-state tests PASS, DF1 cell now correct. But M5 id-reservation cell omits reuse of its existing own id, invalidating promised new-id re-enable state transition. Actual missed-page reproducer above. |
| V1.7 | PASS (QN1 exception recorded) | 2505 full suite and ruff PASS; 82 S cases unchanged and green. Frozen backfill SHA identical. Base S file collects: 80 FAIL/2 PASS, named 6h re-alert/unrewound-cursor regressions behavioral; no guarded edits. Composed literal .gitignore mismatch retained/disposed as specified. |
| V2.1 | FAIL (QF4) | Nominal C re-enable scenario and I real M1/M2 all PASS (silent disabled events, healed level, still-on levels once/new ids/no notices). Original NF1 fixed. Real M5 surviving page never reaches silent/first-live commit after disabling its original id: zero pages through retries/restart. |
| V2.2 | PASS | C (a)–(f) new/upgrade/global/gate=false/stale-first_live/disabled-before-evaluation PASS; original NF1 new-check and NF3 global flip now PASS; H sibling re-enable-before-ever-evaluated PASS. No blanket success inferred for QF4's disabled existing M5 id. |
| V2.3 | PASS | C original runtime/RPC/restart/same-block boundaries and I 6 partial/error initial-catchup controls PASS; I 12 new global/percheck repeat/forced-live partial/runtime/RPC failures all atomic and pinned across restart/same-block retry; boundary-write crash rolls back findings/cursor/silent key and retries. |
| V2.4 | PASS | C disabled reorg composition plus original real M1/M2 PASS; I 14 new real M1/M2/M5 retained/orphan/transient deep-repeat probes PASS. Reorg marker clears on successful live commit; no history message; QN2 internal bookkeeping noted separately. |
| V2.5 | PASS | C A4[new/reenabled] fails catching check for several steps while A's live events send; H failing-repeat A4 both paths; I 6 original and 12 new failure controls assert B's page in same step, no partial A output. Recovery sends surviving levels once. |
| V2.6 | PASS | C no-step flips/no-new-block/new-id/readonly catchup CLI; I fixed NF3 held-backfill flip; H disabled-again/backfill-go-live control PASS. These controls do not cover the real M5 id collision in V2.1. |
| V2.7 | PASS | 67 frozen B tests green/hash identical, 82 S unchanged/green, C69+H41 green, full suite/ruff green. Repair writes within packet; CP1/CP2 union scope plus QN1 checked. C on CP1 base: 40 FAIL/29 PASS; named forbidden bursts for V2.1/V2.2(a) remain behavioral reds. Phase keys unchanged. |
| V2.8 | FAIL (QF4) | Every changed runbook/README/comment claim mapped to test or source-fact V2.8 check in docs-map.md. New D15 claims pass; universal still-true/new-id re-enable assurance contradicted by real M5. No tracked secret found in redacted 735-file scan. |

V2.9 / A6 composed verdict **FAIL**. V1/V2 individual behavioral PASS results above are preserved despite the overall
failure. Candidate green suites, older accepted CP1 status and disposed QN1 do not establish composed acceptance.

**DEFECT-CLASSES 1, 2, 10, 12; new mechanisms and oracle review.** The independent 12-case sweep targets failed
repeat and failed forced-live commits, including a generator yielding a page before raising, RPC transport and
runtime errors, both percheck and global boundaries. It asserts no partial incident/outbox, unchanged pinned record,
restart/same-block retry and another check's same-step live page; all PASS. Separate injection after the actual
`silent:<id>` write proves transaction rollback of findings/cursor/record state: PASS. Original empty success and
planning rollback controls PASS. Durable floor survives record deletion, global fallback needs no silent row,
before-first-live and held-backfill repeats keep history silent, shorter-chain pin does not advance below an earlier
boundary, above-boundary reorg needs no repeat. H's 41 cases and all original independent reproducers were measured,
not accepted from handoff output.

Real-check concern from repair handoff: new tests use actual M1/M2/M5 on FakeChain/MiniSSV; canonical replays retain
the exact original transaction hashes and payloads while updating block hashes. Retained-live M1 and M2 incidents
send nothing again; orphaned support resolves once (M2 info incidents resolve silently by normal severity policy).
M1 historical poison+repair remains silent. M5 clean/latent/confirmed boundary × retained/orphaned support (6 cases)
passes the operator-message oracle; QN2 records the id divergence actually observed. The omitted real-check coverage
is therefore no longer merely untested. The additional real-M5 disable/re-enable falsifier demonstrates QF4, which
cannot be accepted as a NOTE under D2.

The repair's remaining risk candidates are accounted for: its D15(c) mechanism was explicitly approved in this
dispatch; held-backfill and upgrade fallback cases pass; before-history-pin behavior stays on the tested global
path. Source inspection confirms the extra per-step meta reads; no performance benchmark/campaign was run under
D2, and no timing failure was observed in the required suites. This is no independent performance guarantee.

The seeded C sweep changes were reviewed directly against 718606c: removing the deep-reorg exclusion and adding a
deep action broadens the domain; `floor=max(checkpoint, prior floor)` is the independently derived monotone D15
boundary, enlarging the set of messages rejected as historical, not reading the implementation's record as its
oracle. `max_log_range=1` records ancestors so these reorgs are actually detected; default large-range behavior
remains exercised elsewhere. Silence, id uniqueness, supported-live-message and independent pin assertions remain.
No oracle was weakened. D15 itself changes the specified floor; the assertion change follows that independent
contract. [Exact sweep diff](cp2-round-2/sweep-oracle-review.diff).

Regression effectiveness was remeasured in isolated archives: code-base S82 = 80 FAIL/2 PASS; C69 on CP1 =
40 FAIL/29 PASS (including new stronger sweep); original NF21 on round1 = 11 FAIL/10 PASS. V1.1 fails on
`sw_a-kpage-1001:realert:1` at clock+6h02m; V1.4 fails on cursor `[1004,END]` versus `[1002,END]` (depth1),
`[1007,END]` versus `[1005,END]` (depth4). V2.1 CP1-base first assertion sees
`['ev:1004','ev:1006','lv1','lv2']` versus `[]`; V2.2(a) sees stored history events and lv1 versus `[]`.
State-only or new-symbol failures in the broad files are not counted as those behavioral proofs.
[Named assertion evidence](cp2-round-2/named-red-assertions.txt).

Scope: repair 25 paths, CP2+repair 31 paths, composed 128 paths. Repair production changes are eb_reconcile only;
alerts.py unchanged (the permitted mute-only allowance unused); test_catchup fixtures/sweep, new history-silence
file and three READMEs lie within the specified sections (§3/§4/§6.2/§7). Other repair paths are repair-1 evidence.
No db.py/metrics.py/harness.py/checks/m*.py/test_m*.py/test_backfill.py changes. test_switches byte-identical to CP1
and round1; config.example only _comment; registry.build source identical. `test_backfill.py` SHA256
`28da7d49e950a0bdc51ee7d4f6993ad757fa72d60524c39a7c28efcc24702d35` at code base, round1 and candidate.
The only non-plan composed scope exception is `.gitignore`, accepted QN1 as instructed; plan/QA/provisioning records
are identified as workflow inputs rather than silently charged to implementation. [Scope proof](cp2-round-2/scope-proof.txt),
[repair paths](cp2-round-2/repair-paths.txt), [composed paths](cp2-round-2/composed-paths.txt),
[empty guarded diff](cp2-round-2/guarded.diff), [reader inventory](cp2-round-2/incidents-readers.txt),
[complete docs map](cp2-round-2/docs-map.md).

No mutation campaign or slow-check campaign, no helper agent, public-chain RPC, Docker/deployment smoke, external
alert, mainnet transaction, push, Slack/GitHub post or live-config edit. Only required suites, bounded wrong-revision
comparisons and targeted adversarial probes were run. Implementer falsifier logs were read, not rerun. External
deployment/backfill value and endpoint historical availability are coordinator delivery-smoke bindings; untested
real m3–m14 deep-repeat combinations beyond M5 remain outside this targeted adversarial sample, not claimed as an
exhaustive proof. High effort effective-start receipt is not exposed by the worker-list API (above). No required
behavioral measurement is unexecuted; acceptance failures are QF4 and its state/docs consequences. QN1's agreed
closure edit and QN2's proposed ownership remain coordinator actions.

QA setup corrections are retained: the approved NF3 setup replacement, the first M2 probe's fake eth_call stub
rewind omission, and local socket permission retry. None changes candidate code or an agreed message oracle.
All archive experiments and their test data were under `/private/tmp`, removed after each run; no scratch directory
remains. Only this report and cp2-round-2/ evidence are committed, with authorized ignored venv/test-tool artifacts
outside the commit. [Scratch restoration](cp2-round-2/scratch-restoration.txt),
[candidate restoration diff](cp2-round-2/candidate-restoration.diff),
[QA write inventory](cp2-round-2/qa-write-inventory.txt). No shared plan/state/log, implementation/tests/config or
previous report was edited. Next decision: coordinator presents this second FAIL and the concrete M5 reproducer to
Marco; QA starts no further repair or checkpoint.

Co-Authored-By: GPT-6.1 Sol <noreply@anthropic.com>
