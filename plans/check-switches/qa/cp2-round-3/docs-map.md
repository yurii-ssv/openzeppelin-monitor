# Independent composed docs mapping, candidate 4205b00

All cited repository tests were rerun in `targeted.txt` or `suite.txt`, not inherited as results from handoff.
The original checklist's R1–R23 / N1–N5 / C1–C5 / M1–M3 / X1 and repair-1 D1–D11 were read against the actual docs.

| Claim group | Independent measurement / source | Judgment |
|---|---|---|
| Disable at restart; no live reload; all start commands; readonly status/export | S closure/readonly/roundtrip; C `test_every_start_command_closes_and_status_export_never_do`; CP1 independent CLI probes | PASS |
| Boolean-only enabled; writable commands validate before DB; readonly status validates; export/audit do not construct Worker | S malformed-switch tests; C malformed current/fresh/pre-version DB tests; original DF1 sibling probes | PASS |
| Disabled rows terminal, closure atomic, no notices, queued outbox still delivered, no re-alert; counts/digest/status/export | S V1.1–V1.6, independent resolved/atomicity/SQL/read-only probes | PASS |
| Cursor/reorg state kept for disabled ids and replayed on enable | S depth1/depth4/minimum ancestor, C V2.4, real M1/M2, new real-M5 UNKNOWN | PASS |
| New/re-enabled live checks silent at a durable pin; still-true sent once next block with new id; history/healed events never sent; backfill gate; upgrades unchanged | C V2.1/V2.2/V2.3; H durable-history tests; original NF21; QF4 real-M5 test, id lifecycle14 | PASS |
| Failures pinned/retried; status catchup; one check cannot hold another's live alert | C failures/readonly status; H repeat failures; independent deep failure/partial/rollback probes28 | PASS |
| Quick flips, no-block enable, bootstrap marker lifecycle | C V2.6; S roundtrips; CP1 independent rollback | PASS |
| Global D17 unchanged oracle; global re-enable and repeats | Frozen B67 hash identical; H and NF21; phase keys unchanged in code | PASS for required oracle; extra confirmed-to-latent repeat fails QN3 |
| Id allocation (new §4 rule) | IDs14 and independent foreign-open/resolved collision, multi-disabled suffix/rollback; original QF4 red on ff3841f and green here | PASS |
| Deep repeats preserve prior page until canonical live judgment, then resolve unsupported evidence once | Existing H and QA2 wholly unsupported clean controls pass; new partial orphan leaves latent support: required suffix-reorg probe FAIL (QN3) | FAIL for M5 confirmed-to-latent sibling, NOTE severity under D2 |
| Monitor switches separate; M8/M10/M11/M13 dual-sided; M15 Monitor-only | D13 scope; unchanged `deploy/config/monitors` own `paused` fields; registry m1–m14 | PASS, source facts |
| m9/m11 sub-switches unchanged | Guarded diff empty; all original m9/m11 tests in full suite | PASS |
| Metrics omit disabled check's run/error series and open incidents | S metrics tests; C `test_metrics_have_no_run_series_for_a_disabled_check`; metrics source unchanged | PASS |
| Example _comment mentions enabled/backfill gate and file still builds all checks | Only _comment JSON difference; full framework example-build test; default backfill true in worker and template | PASS |
| §6.1 proposed id must still be unique; M5 module doc says framework shares own id | Wording predates D16 and is outside repair §4. §4 now explicitly documents disabled-id suffix and differing ids | NOTE QN4: contextual/cross-reference cleanup, no behavioral control failure |

QN3 does not establish a historical/disabled-period false message: the missing message is a resolution of a page
already sent in live mode. The ordinary non-repeat M5 confirmed-to-latent control sends the resolution and passes.
Public deployment config/archive endpoints and delivery smoke are coordinator closure bindings, not exercised here.
