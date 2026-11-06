# Recurring defect classes — read before implementing, before repairing, and before QA

Source: independent Codex QA rounds of CP1A, CP1B, CP2a, CP2b, CP2c, CP2d (2026-10-21). The same classes were found again and again, in different checks. An implementer who addresses each class up front removes most QA rounds. A QA that attacks each class finds the real bugs first.

## The classes (each with the bug it produced)

1. **Empty, partial or missing input read as "clean" or as recovery.** An API/proof/Blockscout/log response that is empty, short, or lists fewer items than asked must be UNKNOWN or an error: keep incidents open, keep retry markers, prove disappearance only from on-chain or persisted state. (CP2d F1 empty cluster API resolved incidents; CP2a H1 M3 resolved a live page when the API stopped listing a cluster; CP2b F1 partial proof results resolved a persisted mismatch; CP2c F1 API failure cleared the reorg marker; CP1A F10 jq error read as "no violations".)
2. **Reorg handling of state that was already resolved or is outside the obvious set.** In a reorg run, re-evaluate EVERY persisted/opened key (README §6.2), not only keys "last seen above the ancestor", and including keys whose incident was resolved before the reorg (their clearance may have been orphaned). Any input you cannot evaluate in a reorg run must raise (rollback, marker kept, retry each step). (CP1B repairs 2-3; CP2d F3; CP2a H2; CP2c F1; CP2b F1.)
3. **Same-block ordering.** Keep log/transaction order within a block; never keep "the last tx per cluster per block"; evaluate each event against the state AT ITS position. (CP2d F2 repair then re-poison; CP2a M3 later top-up hid an earlier attack; CP2b F2 same-block rotation.)
4. **Time windows anchored on the wrong thing.** Anchor windows at the current checkpoint/sweep, not at the last hit; use chain timestamps or the wall clock for time-based rules ("30 min", "24 h", "9 h"), not block counts (missed slots) and not block arrival (the clock must fire when no blocks arrive). (CP2a M1/M2; CP2b F8.)
5. **Events lost between sweeps.** Derive from logs, not from end-of-sweep state: a condition that appears and heals between two sweeps must still alert. (CP2b F4.)
6. **Lossy truncation and caps.** A cap on candidates, pages or batches must never drop the one item the spec needs (earliest validating tuple); bounded cost is fine, silent loss is not. (CP2d F4.)
7. **Fail-open tooling.** Every command whose failure could mean "nothing found" needs its exit status/empty-output checked (jq, grep, ls, python, pipelines); a gate must fail on missing input, wrong types, unreadable files. (CP1A F2, F10.)
8. **Gates that check names, not meaning.** Validate full layout (types, indexed flags, order, outputs) against an independent reference, not the file under test; required sets come from the spec list, not from the script's own list. (CP1A F8, F8b, F9.)
9. **Spec coverage by shorthand.** Derive the trigger list yourself from the spec text; one test per trigger line at its severity and a silent benign twin; the plan's one-line summary is not the spec. (CP1A F1, CP2a H3 legacy SSV path, CP2b F3 two-root warning, F7 "consecutive".)
10. **Persistence/idempotency.** Restart between detection and handling; incident id and outbox-key reuse after a reorg; duplicate or dropped alerts; first sweep with no baseline.
11. **Settings and parameters.** Thresholds and protocol parameters come from chain or config; required settings are validated at construction (fail loud); a missing setting never makes a check inert.
12. **Tests that cannot fail.** A regression must be shown RED on the frozen head and GREEN after; a falsifier must redden a named test and be restored with a clean tree.

## What every implementer does before sending worker_done

- Write the **state table** in the worker record: every persisted table/marker and every off-chain input, with its behaviour under (a) empty, (b) error, (c) partial, (d) reorg depth 1 and N beyond the confirmation window incl. resolved keys, (e) restart between detection and handling, (f) same-block ordering, (g) time-window anchoring. A cell without a test is a defect.
- Add a deterministic, seeded **adversarial sweep** (fork depth × confirmations × restart points × empty/error inputs) asserting: no resolve without positive proof; no open incident without supporting evidence; no dropped page for surviving evidence; no window anchored on stale data; no event lost between sweeps; health flag consistent.
- Derive the spec trigger list yourself and map each line to a test.

## What QA does first

Attack classes 1-6 and 10 on every persisted key before reading the implementer's tests; then the lane-specific rules.

## Round policy (owner decision, 2026-10-21)

After **two failed QA rounds** on a lane, a later QA classifies each new or open finding as BLOCKING or NOTE. BLOCKING = correctness or safety: a missed, false, duplicated or dropped alert or page; a false "clean"; wrong routing or severity of a page; data loss; fail-open gate; a secret leak; an unsafe action. NOTE = everything else (style, documentation, performance within budget, an edge limited to an accepted or deferred item, maintainability, extra coverage). NOTE findings are recorded in the report and in plan §8 "Recorded notes" with owner and do not block VERIFIED (PASS WITH NOTES). The classification is the QA's, with the reason; the coordinator may overrule only toward BLOCKING.

## Owner decision D15 (2026-10-22): stricter BLOCKING

Supersedes the BLOCKING list above for every lane and round: BLOCKING = missed or lost page, false clean, false page, unsafe action, data loss, fail-open gate, secret leak. A false or duplicated warn, a changed message text, a transient self-resolving alert, a downgraded warn or a performance observation is a NOTE (one line, reason, recorded in plan §8). No mutation testing or other time-consuming checks (QA-CONTRACT scope note).
