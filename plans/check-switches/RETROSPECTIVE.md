# Retrospective — check-switches

**The numbers:** 2 checkpoints, 5 QA rounds, 3 repairs, 3 escalations to Marco. 3 of the 16 decisions (D14, D15, D16) were written during execution, and they were the heaviest ones.

## Where each QA failure came from

| Finding | What it was | Root cause |
|---|---|---|
| CP1 QF1 | `enabled: 0/null/""` silently closed incidents | **Plan gap:** D6 said "explicit false" but never defined which values are valid |
| CP1 QF2 | Reader inventory missed 2 readers that join incidents in any state | **Plan gap:** the planner's grep was too narrow, and the readiness review checked the list against that same grep |
| CP1 QF3 | DB opened before the switch validation | **Repair imprecision:** D14 said "before any DB write", but the repair didn't know `main()` opens the DB first |
| CP2 NF1/NF2/NF3 | Historical events paged after a deep reorg, or after a flip inside backfill | **Plan gap plus an existing bug:** D9 never covered a reorg after the catch-up ends. The global path was already broken on the base commit (QA confirmed it failed there before any change) |
| CP2 QF4 | Re-enabled M5 never pages, because the id is still held by the disabled row | **Missed earlier:** already present in the first CP2 implementation. Round 1 tested with stub checks; round 2 used real M5. The plan even recorded the fact ("a disabled id stays taken") but no test row followed from it |
| CP2 QN3 (DF2) | Missing "resolved" message after a deep reorg | **Repair side effect:** introduced by repair 1's D15 change. Partly hidden by QF4: the re-enable version only became reachable once QF4 was fixed |

## Is failing 2–3 times a sign the repair worker lacks context?

Only partly. Only **2 of about 8** findings came from a repair being imprecise (QF3, QN3). The main driver was **QA finishing the spec**: it explored combinations the plan's state table didn't list (reorg × catch-up lifecycle × global backfill phase × a real check's private state). The other hypothesis did happen too: QN3's re-enable version could only be reached after QF4 was fixed.

The pattern to act on: **both repair-caused defects came from decisions written mid-run (D14, D15)**. Those went straight into a repair packet without the fresh-eyes readiness review that the original D rows got. The original D rows held up better.

So repeated FAILs are a useful signal, but it helps to know which kind each one is. Tag every finding with:

- **where it was introduced:** base, original implementation, or repair N;
- **why it surfaced:** newly reachable or simply missed before.

Repeated failures from repairs mean a context problem. Repeated failures from the spec or the base mean a planning problem. Here it was mostly planning.

## The state table as "TDD in prose"

It is the strongest part of the method. QA used it as its test oracle, and its failing reproducers then became each repair's acceptance tests, which is why repair 2 closed in a single round. To make it stronger:

1. **Make it a transition matrix, not a list of states.** Rows are states. Columns are events: start, disable, enable, a reorg shallow or deep relative to each boundary, restart, RPC failure, backfill vs live phase. Each cell says what message, if any, is expected, and empty cells say "N/A because …". Every reorg-related finding came from cells that were never written.
2. **Write the tests before the code.** Turn the matrix into failing tests at the start, frozen and hash-pinned like `test_backfill.py`. This run did it by accident with QA's probes. Doing it on purpose moves discovery from QA round 2 to day 0.
3. **Require real checks with private state in V rows** (M1, M5), not only stubs. QF4 was missed for this reason.
4. **Generate inventories with a command written in the plan,** not as prose lists. QF2 came from a hand-maintained list.

## Other improvements for the next plan

- **Fresh review of new D rows.** Any decision written during execution gets a short independent review before its repair packet goes out.
- **Repair packets name every place the invariant can break.** For example, "before any DB write" should list every place that opens the DB. The packet should also make the repair run the sibling sweep from the defect-class catalog before handing off.
- **Escalation policy for NOTE-only rounds.** QA must report FAIL whenever a required check fails, even when the finding is NOTE severity. That escalated two NOTE-only outcomes to Marco (CP1 round 2, CP2 round 3). One option is to let the coordinator accept NOTE-only rounds that have owners and triggers, and escalate only BLOCKING findings or a third round. Marco's call.
- **Provisioning noise.** The `.gitignore` exception was reported as QN1 in every round. Declare it in the plan's allowed diff up front, or have workers read the plan from its absolute ssv-research path.
- **Preflight for workers.** Set Codex to `auto_review` before dispatch, since QA1 stalled on an approval prompt. Write packets to use literal paths in `rm`. Tell subagents to return report content as text, because their writes can be refused.
- **Smoke test design.** Choose the check to switch by querying the source DB for open incidents first; this run's `m3` had none, so the closure and send-once paths ran empty. Also list RPC needs in the plan: the internal RPC isn't an archive node.
- **Plan editing.** Edit by anchor text, not line number; the status sed hit the wrong line once after rows shifted.
