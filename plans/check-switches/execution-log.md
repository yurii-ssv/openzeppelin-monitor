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
