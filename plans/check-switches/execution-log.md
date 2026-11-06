# Execution log — check-switches

Contract: [plan.md](plan.md) · Current state: [state.md](state.md)

<a id="event-0001"></a>
## Event 0001 — plan written

2026-11-06 · coordinator (Claude Fable 5.1 high) · worktree HEAD `9e94a1a`, run dir untracked
Outcome: plan.md, state.md, this log, `agents/` (3 role documents copied from the kit, hashes equal to source), `qa/DEFECT-CLASSES.md` (copy of the previous runs' file, `7daa55f6…`), `evidence/pre-plan/` (base suite 2,313 passed, ruff clean). Kit hashes re-measured equal to the handoff; Orca CLI is 1.4.250 (handoff: 1.4.246); this tab's `ORCA_WORKSPACE_ID` points at this worktree with the right repo id.
Decision: D1–D5 written as coordinator proposals for Marco's one-batch confirmation (models, QA policy, disable notice incl. the re-enable message, re-enable semantics, order with M17). Next: fresh independent readiness review, then present to Marco and stop.

<a id="event-0002"></a>
## Event 0002 — readiness review and revision

2026-11-06 · coordinator · plan.md `51cfdcda…` → `7cf0bad9222e242de760923f65568f07eb35519fbca2a11d42e999a49ff84c74`
Outcome: review 1 ([reviews/readiness-1.md](reviews/readiness-1.md), fresh general-purpose agent) READY AFTER FIXES: B1 (health dedup keys), S1–S7, N1–N6; every fix applied (plan § Readiness lists the disposition). Base suite 2,313 passed, ruff clean (F1).
Decision: readiness READY; Marco to answer D1–D5 (models, QA policy, disable/enable notices, re-enable semantics, order with M17) and whether the plan commit may stay in git history. Next: present to Marco and stop; execution awaits his instruction.

<a id="event-0003"></a>
## Event 0003 — Marco's answers applied

2026-11-06 · coordinator (Claude Opus 5.5 medium) · plan.md `7cf0bad9…` → `c396a859a9651e44cd7479a391a12df777d9f53e6c1b338043308a5becf23464`
Outcome: Marco answered: D1 coordinator Opus 5.5 medium, implementers Opus 5.5 high, QA Codex gpt-6.1-sol high; D2 policy carried over; D3 **no disable/enable notice at all**; D4 as proposed; D5 this plan first, independently of M17; plan commit may stay in history. D3, D7 (marker without counter), D9, V1.1, V1.2, V1.5, V2.1, V2.2, V2.6 and the delivery smoke revised; review 1's B1 no longer applies. No second review (the change removes behaviour).
Decision: plan READY, open decisions none. Next: Marco starts a new session to execute (plan commit with the gitignore exception, Orca guides, Run, CP1).

<a id="event-0004"></a>
## Event 0004 — plan commit, Run, CP1 dispatched

2026-11-06 · coordinator (Claude Opus 5.5 medium, execution session) · plan.md `c396a859…` (unchanged)
Outcome: `.gitignore` `plans/` → `plans/*` + `!plans/check-switches/`; plan commit `00fe823`. Orca guides loaded (orca-cli, orchestration, coordinator-loop, placement-and-remote; CLI 1.4.250); `ORCA_WORKSPACE_ID` = F6 value. Run `run_3869f8caafaa`. CP1 packet `workers/cp1/packet-implementer-1.md` (sha256 `a35734a7…`) committed as `61307dd` (frozen CP1 base). Start: Task `task_1068470b2829`, Dispatch `ctx_ef35412e7186`, worktree `/Users/marco/orca/workspaces/ssv-network-monitor/cs-cp1` (Orca branch `mtabasco/cs-cp1`; the packet tells the worker to commit on `cs/cp1`), HEAD `61307dd` = frozen base; requested claude/opus/high = `launch.effective` claude/opus/high; state ready, turn started.
Decision: CP1 IN PROGRESS. Next: wait for `worker_done`; then check head, diff scope, start Codex QA in a fresh `new-child` of the candidate head.

<a id="event-0005"></a>
## Event 0005 — CP1 implementer done; QA round 1 dispatched

2026-11-06 · coordinator
Outcome: `worker_done` (msg `msg_bf2535585641`, Dispatch `ctx_ef35412e7186`, outcome succeeded): candidate `7639cff` on `cs/cp1` (parent = frozen base `61307dd`), tree clean. Diff `61307dd..7639cff`: 10 files, only CP1 Allowed writes (`registry.py`, `eb_reconcile.py`, `alerts.py`, `checks/README.md` §4, `reconcile/README.md`, new `tests/test_switches.py`, `workers/cp1/**`); no `db.py`, `metrics.py`, `checks/m*.py`, `test_m*.py`, `test_backfill.py`, `harness.py`. Claims: suite 2,366 passed (2,313 + 53), ruff clean, `test_backfill.py` `28da7d49…`, V1.1/V1.4 red on `9e94a1a` for the behavioural reason, 7 falsifiers, 5 risks (verify-layout/once also close; `enabled` truthiness = `build`; D8 silences not-built checks; M1/M2 `_resolved_since`; queued rows delivered). QA packet `workers/cp1/packet-qa-1.md` (`8ec4b884…`, commit `6789154`). Start: Task `task_7c1f68de78c9`, Dispatch `ctx_0c5ee82f133b`, worktree `/Users/marco/orca/workspaces/ssv-network-monitor/cs-cp1-qa1`, HEAD `7639cff` = candidate; requested codex/gpt-6.1-sol/high = `launch.effective`.
Decision: CP1 AWAITING QA. Next: on `worker_done`, copy the report into `qa/` with its sha256 and decide.

<a id="event-0006"></a>
## Event 0006 — CP1 QA round 1 FAIL; D14; repair 1 dispatched

2026-11-06 · coordinator
Outcome: Codex QA round 1 report `qa/cp1-round-1.md` (sha256 `c63de8729ed96e6349cb48bfe30daf70a34770a59fd7685b2407914f9f5f7781`) and its evidence `qa/cp1-round-1/` (manifest `qa/cp1-round-1.manifest.txt`, 24 files) copied by the coordinator from `/Users/marco/orca/workspaces/ssv-network-monitor/cs-cp1-qa1` (uncommitted there: the worker's `git add` hit the sandbox and stopped at an approval prompt the coordinator may not answer; escalations `msg_stop_c2e66460…`, `msg_stop_5f5a1308…`). Verdict **FAIL**: QF1 BLOCKING — `enabled: 0|null|""` closes incidents (D6 says explicit `false`); QF2 NOTE — reader table omits `m1_stale_tuple.py` `_reported` and `fork_e2e.py` `sent` (V1.6 FAIL); QN1 NOTE — README should say `verify-layout`/`once` close too. V1.1, V1.2, V1.4, V1.5, V1.7 PASS (suite 2,366 passed, ruff clean, regressions red on `61307dd` and `9e94a1a`); V1.3, V1.6 FAIL. Implementer risks 3–5 judged no defect.
Decision: CP1 REPAIR (failed rounds: 1 of 2). New D14 (coordinator, within D6's intent): `enabled` must be a boolean, anything else is a startup configuration error; QF2 and QN1 assigned to the same repair (QN1's runbook part stays with CP2 docs). Repair packet `workers/cp1/packet-repair-1.md`, base `7639cff`, branch `cs/cp1-r1`.

<a id="event-0007"></a>
## Event 0007 — CP1 repair 1 started

2026-11-06 · coordinator
Outcome: packet `workers/cp1/packet-repair-1.md` sha256 `0b407dcb…` (commit `05a3fe0`). Task `task_5ce75920d977`, Dispatch `ctx_7a0b9bf65c45`, worktree `/Users/marco/orca/workspaces/ssv-network-monitor/cs-cp1-r1`, HEAD `7639cff` = repair base; requested claude/opus/high = `launch.effective`; turn started.
Decision: CP1 REPAIR in progress. Next: on `worker_done`, scope check, then Codex QA round 2.

<a id="event-0008"></a>
## Event 0008 — CP1 QA round 1 settled

2026-11-06 · coordinator
Outcome: `worker_done` (msg `msg_b2007470e692`, Dispatch `ctx_0c5ee82f133b`): the QA-1 worker got past its approval prompt and committed its report on `cs/cp1-qa1` as `11e08ee` (25 files, additions only on top of `7639cff`). The committed report is byte-identical to the coordinator's copy (sha256 `c63de872…`) and every evidence file matches `qa/cp1-round-1.manifest.txt`. Verdict unchanged: FAIL.
Decision: none new (repair 1 in progress, event 0007). QA-1 worktree `cs-cp1-qa1` releasable (report preserved in `qa/` and on `cs/cp1-qa1`).

<a id="event-0009"></a>
## Event 0009 — CP1 repair 1 done; QA round 2 dispatched

2026-11-06 · coordinator
Outcome: `worker_done` (msg `msg_351106e0ba65`, Dispatch `ctx_7a0b9bf65c45`): repair commit `62ed4c2` on `cs/cp1-r1` (parent `7639cff`), tree clean; diff `7639cff..62ed4c2` 8 files, all in CP1 Allowed writes (`registry.py`, `eb_reconcile.py`, `test_switches.py`, both READMEs, `workers/cp1/repair-1/**`). Claims: `validate_switches` (D14) called first in `Worker.__init__`; reader table re-derived (28 rows incl. M1 `_reported`, `fork_e2e` `sent`, by design + real-M1 pinning test); README names `run`/`once`/`verify-layout`; suite 2,395 passed (2,366 + 29), ruff clean, `test_backfill.py` unchanged; 26/29 new tests red on `7639cff`. Disclosed deviation: `export`/`audit` construct no `Worker`, so they do not validate `enabled` (they close nothing). Coordinator check: shipped configs have boolean `enabled` and object `checks.<id>` entries. QA-2 packet `workers/cp1/packet-qa-2.md` (`41b588c8…`). Codex config (Marco, 2026-11-06): `approval_policy = "on-request"`, `sandbox_mode = "workspace-write"`, `approvals_reviewer = "auto_review"` (top level of `~/.codex/config.toml`). Start: Task `task_735c4657683a`, Dispatch `ctx_bdf99c2be3a3`, worktree `/Users/marco/orca/workspaces/ssv-network-monitor/cs-cp1-qa2`, HEAD `62ed4c2` = candidate; codex/gpt-6.1-sol/high = `launch.effective`.
Decision: CP1 AWAITING QA (round 2). A second FAIL → decision by Marco (D2).

<a id="event-0010"></a>
## Event 0010 — CP1 QA round 2 FAIL (second failed round): decision by Marco

2026-11-06 · coordinator
Outcome: `worker_done` (msg `msg_f194db621105`, Dispatch `ctx_bdf99c2be3a3`): report committed on `cs/cp1-qa2` `3fb9e49`; copied to `qa/cp1-round-2.md` (sha256 `98ed5e0987a0a073efb5bf4b5cffc55d3ea88a8d699f32cc99bd4f6843a70d55`, equal to the commit) with evidence `qa/cp1-round-2/` (manifest `qa/cp1-round-2.manifest.txt`, 42 files). The Codex worker ran with `approvals_reviewer = "auto_review"`: no prompt reached Marco. Verdict **FAIL**: QF1, QF2, QN1 CLOSED; new **QF3 NOTE** — `main()` opens the writable `DB` (migration: `schema_version` upsert, or a new database file with its schema) before `Worker` validates `enabled`, so `run`/`once`/`verify-layout` with a malformed switch write schema bookkeeping before the config error (no incident, marker or outbox change; `status` clean). Kept FAIL because V1.3/V1.6 carry the packet's "before any database write" requirement. V1.1, V1.2, V1.4, V1.5, V1.7 PASS (suite 2,395 passed, ruff clean, regressions red on both bases). `export`/`audit` not validating: judged harmless (read-only, no Worker).
Decision: second failed QA round on CP1 → no third repair without Marco's decision (D2 / Review budget). CP1 status PROVISIONAL pending that decision. Options presented: (A) accept CP1 with QF3 deferred to CP2 (CP2's Allowed writes include `eb_reconcile.py`; CP2's composed QA re-measures it), (B) a third bounded repair + QA round 3, (C) accept and narrow D14 to "no incident/marker/outbox change" (schema init allowed).

<a id="event-0011"></a>
## Event 0011 — CP1 ACCEPTED WITH DEFERRALS (Marco, option A); integrated

2026-11-06 · coordinator
Outcome: Marco chose option A: accept CP1, QF3 deferred to CP2 (DF1 in plan § Decisions and closure; D14 clarification: `export`/`audit` need not validate). `git merge --no-ff cs/cp1-r1` → `16432ad` on `plan/check-switches` (contains `7639cff` and `62ed4c2`). On the merge: suite **2,395 passed** (56.9 s), `ruff check reconcile` clean, `test_backfill.py` sha256 `28da7d49…` (= base). QA verdicts remain FAIL as measured (invariant 2).
Decision: CP1 ACCEPTED WITH DEFERRALS; CP2 dispatch next, its packet adds DF1 and the CP1 docs follow-ups (runbook: every start command closes; queued rows still delivered; `enabled` must be boolean). Worktrees `cs-cp1`, `cs-cp1-r1`, `cs-cp1-qa1`, `cs-cp1-qa2` releasable (branches merged or reports copied).

<a id="event-0012"></a>
## Event 0012 — CP2 dispatched

2026-11-06 · coordinator
Outcome: packet `workers/cp2/packet-implementer-1.md` (sha256 `81d10101…`; CP2 block + DF1 + CP1 docs follow-ups) committed as `d7b3da6` = frozen CP2 base (parent `6b20457`, contains the CP1 merge `16432ad`). Start: Task `task_b2312d8a0353`, Dispatch `ctx_db6390806a7a`, worktree `/Users/marco/orca/workspaces/ssv-network-monitor/cs-cp2`, HEAD `d7b3da6` = base, branch `cs/cp2`; requested claude/opus/high = `launch.effective`; turn started.
Decision: CP2 IN PROGRESS. Next: on `worker_done`, scope check, then composed Codex QA (V2.9) over `9e94a1a` → CP2 head incl. the DF1 reproducer.

<a id="event-0013"></a>
## Event 0013 — CP2 implementer done; composed QA round 1 dispatched

2026-11-06 · coordinator
Outcome: `worker_done` (msg `msg_3d09a920794c`, Dispatch `ctx_db6390806a7a`): candidate `718606c` on `cs/cp2` (parent = base `d7b3da6`), tree clean. Diff `d7b3da6..718606c`: 11 files, all CP2 Allowed writes (`eb_reconcile.py`, new `tests/test_catchup.py`, `deploy/README.md`, `checks/README.md`, `reconcile/README.md`, `config.example.json` `_comment` only, `workers/cp2/**`). Claims: D9/D10 catch-up record, pinned silent run, forced first live run, `status.checks.<id>.catchup`, DF1 (`validate_switches` before `DB(...)` for `run`/`once`/`verify-layout`), D13 docs + CP1 follow-ups; 69 new tests; suite 2,464 passed, ruff clean, `test_backfill.py` unchanged; V2.1/V2.2 (a) red on `d7b3da6` on the burst; 12 falsifiers. Open points raised: NF1 (reorg > confirmations below the catch-up block after the record is deleted unmutes a silent incident; shared with D17), NF2 (per-check silent run repeated after a reorg below the pin: beyond D9's text), NF3 (flip inside phase `backfill` after the check's own silent run → burst). QA packet `workers/cp2/packet-qa-1.md` (`b77384ac…`). Start: Task `task_886b35cdef48`, Dispatch `ctx_1eb06d22eca7`, worktree `/Users/marco/orca/workspaces/ssv-network-monitor/cs-cp2-qa1`, HEAD `718606c` = candidate; codex/gpt-6.1-sol/high = `launch.effective`.
Decision: CP2 AWAITING QA. NF1–NF3 dispositions wait for QA's severity verdicts.

<a id="event-0014"></a>
## Event 0014 — CP2 composed QA round 1 FAIL

2026-11-06 · coordinator
Outcome: `worker_done` (msg `msg_4c71fa401d3a`, Dispatch `ctx_1eb06d22eca7`): report on `cs/cp2-qa1` `5b34409`, copied to `qa/cp2-round-1.md` (sha256 `6d23504aab2ad76796173786a2ec8f47d1b95f525fc501c7ede129e931392494`, equal to the commit) with evidence `qa/cp2-round-1/` (manifest, 52 files). Verdict **FAIL**. DF1 **CLOSED** (QF3 reproducer 107/107 on the candidate). Suite 2,464 passed, ruff clean, `test_backfill.py` unchanged. Findings: **QF1/NF1 BLOCKING** (in CP2 scope): after the catch-up record is deleted, a reorg deeper than the first live run (supported up to confirmations 12) re-finds a muted catch-up incident and `Alerts._apply_one` unmutes it → a historical event paged; the global D17 path has the same defect, KNOWN RED on `9e94a1a`. **QF2/NF2**: the per-check repeat of the silent run is correct (PASS); the global gap (reorg below `phase.history_block` before first live replays history live) is BLOCKING, KNOWN RED on `9e94a1a`, outside CP2's mandate. **QF3/NF3 BLOCKING**: a check disabled and re-enabled inside phase `backfill` after its own silent run pages its disabled interval (D9 left backfill untouched). **QN1 NOTE**: composed diff includes the coordinator's `.gitignore` plan-provisioning change (`00fe823`). V1.1–V1.6, V2.3, V2.5, V2.7 PASS; V1.7 FAIL (QN1 only), V2.1, V2.2, V2.4, V2.6, V2.8 FAIL on the extensions.
Decision: CP2 REPAIR (failed rounds 1 of 2). QN1: coordinator disposition — plan-provisioning exception, reverted at closure (`plans/` restored in `.gitignore`). NF3 and per-check NF1: repair in scope (coordinator amends D9 for NF3). Global NF1/NF2 (pre-existing D17 defect): scope question to Marco before the repair packet.

<a id="event-0015"></a>
## Event 0015 — Marco: fix the global D17 path too; D15; CP2 repair 1 packet

2026-11-06 · coordinator
Outcome: Marco answered the scope question: fix both the per-check and the global D17 path (recommended option). New **D15** (durable historical silence; global NF2 silent-run repeat; NF3 amends D9's "phase `backfill` untouched" for list contents). QN1 disposed (plan-provisioning exception, reverted at closure). Repair packet `workers/cp2/packet-repair-1.md` (base `718606c`, branch `cs/cp2-r1`; `alerts.py` allowed for the mute rule only; `test_backfill.py` byte-identical; QA's `test_cp2_independent.py` is the oracle).
Decision: start CP2 repair 1.

<a id="event-0016"></a>
## Event 0016 — CP2 repair 1 started

2026-11-06 · coordinator
Outcome: packet sha256 `56d7f6c0…` (commit `b06ec32`). Task `task_b45d25b0c809`, Dispatch `ctx_1779a0dfb3eb`, worktree `/Users/marco/orca/workspaces/ssv-network-monitor/cs-cp2-r1`, HEAD `718606c` = repair base; claude/opus/high = `launch.effective`; turn started.
Decision: CP2 REPAIR in progress. Next: on `worker_done`, scope check (incl. `alerts.py` limited to the mute rule), then composed Codex QA round 2. A second FAIL → decision by Marco.

<a id="event-0017"></a>
## Event 0017 — CP2 repair 1 done; composed QA round 2 dispatched

2026-11-06 · coordinator
Outcome: the repair worker paused once at a Claude Code safety prompt (`rm -f $E/*.txt` on its own evidence; escalation `msg_stop_2cf81147…`) and continued. `worker_done` (msg `msg_18ad489cb559`, Dispatch `ctx_1779a0dfb3eb`): commit `ff3841f` on `cs/cp2-r1` (parent `718606c`), tree clean; diff 25 files, all in the repair's Allowed writes (`alerts.py` untouched). Mechanism: durable boundary `silent:<id>` (fallback `phase.history_block`); a reorg rewinding a check below it repeats the silent run (live: `catchup` record `cause: reorg`; backfill: leaves `phase.evaluated`); the repeat defers incidents live runs saw above the boundary; pin floor `max(checkpoint, B)`; a re-enable during backfill (history_block known) leaves `evaluated`/`first_live` and gets a pending `catchup` record. Claims: QA's 11 failing cases pass as permanent tests (`test_history_silence.py`, 41 tests), 10 controls green, seeded sweep without the deep-reorg exclusion green, 11 falsifiers, suite 2,505 passed, ruff clean, `test_backfill.py` byte-identical. Open: D15 (c) reading (record visible in `status` during backfill); NOTE: real `m*` checks not exercised in a repeat after a deep reorg following a live run.
Decision: D15 (c) read as outcome-binding, mechanism free (stated in the QA-2 packet). QA-2 packet `workers/cp2/packet-qa-2.md` (`50b54ae3…`). Start: Task `task_f4d7167aa1e3`, Dispatch `ctx_5ba74e966d55`, worktree `/Users/marco/orca/workspaces/ssv-network-monitor/cs-cp2-qa2`, HEAD `ff3841f`; codex/gpt-6.1-sol/high = `launch.effective`. CP2 AWAITING QA (round 2; a second FAIL → Marco).

<a id="event-0018"></a>
## Event 0018 — CP2 composed QA round 2 FAIL (second failed round): decision by Marco

2026-11-06 · coordinator
Outcome: `worker_done` (msg `msg_39e412656d29`, Dispatch `ctx_5ba74e966d55`): report on `cs/cp2-qa2` `95403b6`, copied to `qa/cp2-round-2.md` with evidence `qa/cp2-round-2/` (manifest `qa/cp2-round-2.manifest.txt`); sha256 equal to the commit (see manifest line count and hash in this event's commit). Verdict **FAIL**. Closed: QF1/NF1, QF2/NF2 global, QF3/NF3, DF1. Suite 2,505 passed, ruff clean, `test_backfill.py` unchanged, sweep oracle not weakened. New **QF4 BLOCKING** (missed page): with real M5, a disabled-then-re-enabled check whose condition is still true never sends its page — M5 keeps its own `m5_incidents` row open and re-proposes the old `incident_id`, which the framework row (now `disabled`) holds, so `Alerts._apply_one` raises "incident id … already exists" every attempt; the catch-up stays `pending`, only `check-error` health is sent. Present already on `718606c`. M5 is the only check proposing `incident_id`. **QN2 NOTE**: after an M5 silent repeat, M5's internal id and the framework id can diverge (no message impact observed). V1.6, V2.1, V2.8 FAIL (QF4); all other rows PASS.
Decision: second failed QA round on CP2 → no third repair without Marco's decision (D2). CP2 PROVISIONAL.
