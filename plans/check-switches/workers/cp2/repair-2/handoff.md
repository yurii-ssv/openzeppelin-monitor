# CP2 repair 2 — handoff (check-switches, D16, QA cp2-round-2 QF4)

Run `run_3869f8caafaa` · task `task_b7882b055c18` · worker Claude Opus 5.5 high (Orca) · 2026-11-06

## Identity

| Item | Value |
|---|---|
| Base (verified before editing, clean tree) | `ff3841ffd1bfc2b7b6478f8c3941aefaa5d27833` (`cs/cp2-r1`) |
| Branch | `cs/cp2-r2` (created from the Orca branch `mtabasco/cs-cp2-r2` at the base; no other branch moved) |
| Head | the single repair commit on `cs/cp2-r2` (sha in the `worker_done` message; `git log -1 cs/cp2-r2`) |
| Role / defect classes / QA report | `6548b79b…` / `7daa55f6…` / `a026b88f…` (re-hashed, equal to the packet) |
| Environment | macOS arm64, Python 3.12.15, pytest 9.1.1, ruff 0.17.1, `reconcile/.venv` per the packet |

## Change

`reconcile/alerts.py` — `_apply_one`, opening path with a check-proposed `incident_id` (lines 129–141), plus the
helper `_id_state` (184–187; `_id_taken` now delegates to it, same result):

- the proposed id is free → opened under it (unchanged);
- held by an `open` or `resolved` row → `ValueError` (unchanged; `incidents.id` is the primary key, so "held only by
  `disabled` rows" = the one row holding it is `disabled`);
- held by a `disabled` row → opened under the first free `<id>-<n>`, n ≥ 2, skipping every taken id whatever its
  state (the scheme of the framework's default ids). The `disabled` row is not touched.

Nothing else changed in code: no `checks/m*.py`, `eb_reconcile.py`, `db.py`, `metrics.py`, `harness.py`,
`test_backfill.py` (sha256 `28da7d49…` unchanged), `test_m*.py`, `test_switches.py`, `test_catchup.py`.

Docs: `reconcile/checks/README.md` §4 — the `Finding.incident_id` comment and the "no open incident" table cell point
to a new paragraph **Incident id rule (D16)**. `deploy/README.md` and `reconcile/README.md` unchanged: their claims
("a condition still true … is sent once, as `open`, under a new incident id"; "a key it finds again opens a new
incident") were contradicted by real M5 at `ff3841f` and are now true for it, so no claim changes. `checks/README.md`
§6.1's row "Default incident id … (a check-proposed `incident_id` must still be unique)" is outside the allowed §4 and
still reads correctly for its subject (reorg reuse of a block); flagged for the coordinator, no edit.

New tests: `reconcile/tests/test_switch_ids.py` (14 cases).

## Per-finding result

| Finding | Result |
|---|---|
| **QF4** (BLOCKING, real M5 disable → re-enable) | **Fixed.** QA's reproducer is permanent as `test_switch_ids::test_m5_real_disabled_key_gets_new_id_and_first_live_page` (QA's body verbatim, plus mechanism assertions after the behavioural one). Red on `ff3841f` with QA's text `surviving M5 condition missed its first-live page`, `assert (0 == 1)` and the logged `ValueError: incident id EB94775-228316cb1e-1005 already exists` at 1010/1011/1012; green on the candidate. QA's whole `test_qa2_deep.py` on the candidate: **28 PASS** (reproducer + its 27 controls) — [qa2-deep-on-candidate.txt](qa2-deep-on-candidate.txt). |
| **QN2** (NOTE, internal vs framework id after a silent repeat) | **Unchanged by D16** (no fix required). Measured: the six `test_m5_real_state_after_global_repeat` probe prints are identical on `ff3841f` and the candidate (latent/supported: internal `…-1004` resolved + `…-1004-2` open, framework `…-1004` open; no message). D16 only acts when no `open` framework row exists for the key and the proposed id is held by a `disabled` row; in QN2 the framework row is `open`, so findings take the update path and the proposed id is ignored. D16 adds a second, accepted divergence of the same kind (framework `<id>-2` for M5's internal `<id>`). Owner stays as recorded by QA. |

## Real-M5 behaviour verified (packet list)

| Packet requirement | Test |
|---|---|
| catch-up commits silently (record `first_live` after the first step, no message) | `test_m5_reenabled_incident_is_sent_once_then_resolved_under_the_new_id` (via `reenable()`), reproducer (`silent_record["state"] == "first_live"`) |
| first live run sends exactly one `page`/`open` under the new framework id `<id>-2` | both tests above |
| the `disabled` row untouched (full-row equality) | reproducer, `…sent_once_then_resolved…`, `…latent…` |
| nothing sent twice over later steps and restarts (3 steps + restart, and again after the resolution) | `…sent_once_then_resolved…` |
| M5's later resolution resolves the new framework incident and sends its resolution (`info`/`resolved`, once) | `…sent_once_then_resolved…` |
| no `check-error`/health, `run:m5.errors` 0, record deleted after the first live run | reproducer |
| a second round trip takes `<id>-3` (the `-2` row is then `disabled` too) | `test_m5_second_round_trip_takes_the_next_free_suffix` |

Other M5 paths that propose ids (`m5_eb_desync.py` 344–409), against a disabled-held id:

| Path | Proposed id | Result |
|---|---|---|
| confirmed, own row same kind (`sender=False` / update revision, 373–386) | own open row's id (the disabled one) | new `<id>-2`, silent in the catch-up, `open` at the first live run — reproducer |
| confirmed escalating own latent/candidate row (359–368) | own row's id | `test_m5_latent_disabled_then_confirmed_live_escalates_the_new_incident`: latent re-tracked silently under `<id>-2` (info, notify 0, no message), the live removal sends one `page`/`escalate` on `<id>-2` (as without a round trip) |
| latent, own row (403–409) | own row's id | same test (silent `<id>-2`) |
| confirmed/candidate/latent with no own open row (`_new_id`, 306–313) | fresh: `_new_id` skips ids in `m5_incidents` **and** `incidents`, any state | cannot collide (source; unchanged) |
| candidate with an own row (396) | none (returns `[]`) | — |
| resolve (`_close` + `resolve=True`, 412–417) | none (framework matches by `(check, key)`) | healed while disabled: catch-up resolves M5's own row, framework has no open row → nothing, no message, the disabled row is the only `m5` incident — `test_m5_condition_healed_while_disabled_is_never_sent_and_opens_nothing`; live resolution of `<id>-2` — `…sent_once_then_resolved…` |
| reorg run, UNKNOWN re-report (344) | own row's id | same rule: with an open framework row the id is ignored (update path); without one (catch-up after a D11 reorg) a silent `<id>-n` opens. Covered by the rule tests, no dedicated real-M5 reorg+UNKNOWN case (risk 2) |

No other check proposes `incident_id` (grep `incident_id=` in `reconcile/checks/`, `reconcile/*.py`: M5 only).

## State / reader table cells (replace the CP1 / repair-1 cells)

| Reader / key | States matched | Behaviour for `disabled` rows | Test |
|---|---|---|---|
| `alerts.py:129-141` `_apply_one`, check-proposed `incident_id` | id lookup, any state (`_id_state`) | held by `disabled` → new incident under the first free `<id>-<n>` (n ≥ 2, skipping ids in any state); held by `open`/`resolved` → `ValueError` (run rolled back, `check-error`, retried) | `test_switch_ids::test_proposed_id_held_only_by_a_disabled_incident_opens_under_the_first_free_suffix[True,False]`, `…suffix_skips_every_taken_id_whatever_its_state[open,resolved,disabled]`, `…held_by_an_open_or_resolved_incident_still_raises[open,resolved]` |
| `alerts.py:181` `_id_taken` (default ids `<check>-<key>-<block>`) | any state | unchanged: the id stays taken, a new incident at the same block gets `-2` | `test_default_ids_are_unchanged_by_the_rule`, CP1 `test_reopened_key_at_the_closure_block…` |
| `checks/m5_eb_desync.py:310` `_new_id` | id only, any state, `m5_incidents` ∪ `incidents` | a `disabled` id stays taken for **fresh** M5 ids (unchanged). M5's open own row keeps its id across the round trip and proposes it again; the framework gives it `<id>-2` (D16). Messages carry the framework id | `test_m5_real_disabled_key_gets_new_id_and_first_live_page`, `test_m5_second_round_trip_takes_the_next_free_suffix` |

DEFECT-CLASSES cells for the new rule (`incidents.id` allocation):

| (a) empty | (b) error | (c) partial | (d) reorg depth 1 / N | (e) restart between detection and handling | (f) same-block order | (g) time |
|---|---|---|---|---|---|---|
| no row → id used as proposed (unchanged) | a failing run after the allocation rolls back the new row; the retry allocates the same `<id>-2` — `test_suffix_rule_is_deterministic_across_a_rolled_back_attempt` | n/a (one row per id, primary key) | the allocation is a row in the run's transaction: a reorg run re-finds the key through `(check, key)` (open `<id>-2`) or, when re-found by a silent run, the id is muted with the incident (D15, repair 1); no new reorg state | restart after the silent run / after the first live run — reproducer and `…sent_once_then_resolved…` (no duplicate, no second id) | findings of one run apply in order; a second key proposing the same disabled id gets the next suffix (`[False]` case + skip tests) | none |

## Re-run summary (this head)

| Row | Result | Measurement |
|---|---|---|
| V1.6 | PASS (repair scope) | reader inventory unchanged except the two cells above (`_apply_one` proposed id, M5 `_new_id`); grep for `incident_id=` producers: M5 only |
| V2.1 | PASS | `test_catchup` / `test_history_silence` / `test_switches` unchanged and green (in the targeted 273); real M5 re-enable: the five `test_switch_ids::test_m5_*` cases and QA's reproducer green |
| V2.8 | PASS (repair scope) | §4 id rule added; the runbook "once, as `open`, under a new incident id" and README "a key it finds again opens a new incident" now backed for M5 by `test_m5_real_disabled_key_gets_new_id_and_first_live_page` / `…sent_once_then_resolved…` |
| Full suite | PASS | **2519 passed** (2505 + 14 new), 0 skip — [suite.txt](suite.txt) |
| ruff | PASS | `All checks passed!` (ruff 0.17.1) |
| Targeted S+C+H+B+ids | PASS | 273 passed (82 + 69 + 41 + 67 + 14) |
| `test_backfill.py` | byte-identical | `28da7d49e950a0bdc51ee7d4f6993ad757fa72d60524c39a7c28efcc24702d35` |

## Red on the faulty revision and falsifiers

- [red-on-ff3841f.txt](red-on-ff3841f.txt): `test_switch_ids.py` on `git archive ff3841f`: **10 failed / 4 passed**. The
  reproducer fails on QA's behavioural assertion (`surviving M5 condition missed its first-live page`, `0 == 1`);
  the other real-M5 cases on `the silent run must commit` (`pending`); the rule tests on the `ValueError`. The four
  passing are the intended controls (open/resolved collisions raise, default ids, healed-while-disabled silence).
- [falsifiers.txt](falsifiers.txt): F1 resolved treated like disabled → `…still_raises[resolved]` red; F2 every
  collision suffixed → both `…still_raises` red; F3 fixed `-2` without the skip loop → 4 red (second round trip,
  skip tests); F4 suffix from `-1` → 10 red; F5 reuse the disabled row's id (delete + re-open) → 10 red incl. the
  reproducer. `alerts.py` restored byte-identical (`cmp`) after each.
- Scratch (`/private/tmp/cs-cp2-r2-scratch`) removed.

## Diff stat

`git diff --stat ff3841f..HEAD` and `git diff --stat d7b3da6..HEAD`, measured on the staged commit content (nothing
left untracked) and recorded in [diffstat.txt](diffstat.txt) (which lists every path but itself).

## Risks / open points

1. **Id divergence (accepted by D16, same kind as QN2).** After a re-enable, M5's `m5_incidents` row keeps `<id>` while
   messages carry `<id>-2`. Operators correlating a message with M5's own table must look up by cluster; `export`
   shows both. Also possible: the framework's `<id>-2` and an unrelated internal M5 `<id>-2` (QN2 path) coexist with
   different meanings; no collision can result, since `_new_id` checks both tables and M5 proposes only open own-row
   ids.
2. No dedicated real-M5 case for "re-enable after a D11 reorg with an UNKNOWN re-evaluation of a disabled-held id";
   covered by the generic rule tests and the D11 tests of CP2 only.
3. `checks/README.md` §6.1 row wording ("must still be unique") not updated (outside the allowed §4); coordinator may
   amend at closure.

## Next owner

Coordinator: integrate `cs/cp2-r2` per the plan and dispatch the next composed QA round (V2.9) if Marco authorises it.
