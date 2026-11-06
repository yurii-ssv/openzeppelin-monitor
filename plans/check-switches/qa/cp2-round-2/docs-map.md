# Independent V2.8 claim map — candidate ff3841ffd1bfc2b7b6478f8c3941aefaa5d27833

Read the composed docs diff and the repair diff directly; the two implementer checklists are claims. All named
repository tests ran on this candidate (2505 PASS, 0 skip); S/C/H/B also ran together (259 PASS).
S = `reconcile/tests/test_switches.py`, C = `test_catchup.py`, H = `test_history_silence.py`, B = `test_backfill.py`,
F = `test_framework.py`. I = this report's independent archive probes. Source facts have a named read-only check;
operator Docker commands, deployed config and historical public RPC availability remain the coordinator's delivery
smoke, not a simulated deployment claim.

| Source and claim | Evidence / result |
|---|---|
| Deploy switch: `checks.<id>.enabled`, M1–M14, absent/true enables, config read at start/restart, no reload | S `test_build_order_unchanged_and_disabled_ids`, example-config copy; F example-build. Worker constructor/registry read once; `docs-facts.txt` records imports. PASS. |
| Boolean only; malformed run/once/verify-layout rejected before DB open/create; status refuses | Original independent CP1 siblings current/fresh/pre-version DB probes all PASS; C `test_malformed_switch_*` and reader controls PASS. DF1 closed. Export/audit construct no worker and need not validate. |
| All start commands close; status/export read only | C `test_every_start_command_closes_and_status_export_never_do` five command variants; S read-only status; I malformed/read-only controls. PASS. |
| Disable: all open incidents become disabled atomically, no resolved/disable notice; repeated restart idempotent | V1.1/1.2/1.5; S closure, empty, roundtrip, rollback, RPC outage tests; I multi-check invalid-evidence rollback. PASS. |
| Queued outbox before disabling start still delivered | S V1.1 two variants actually deliver queued info after closure; state-agnostic deliver(). PASS. |
| Disabled rows never re-alert; excluded from status/open/digest/metric open counts; export preserves state | S V1.1 26h and status/export/metrics tests. PASS for terminal disabled rows. |
| Status disabled from current CLI config before any closing start; digest line after Open incidents; omitted if none | S read-only status/example-copy/digest-no-disabled-line tests. PASS. |
| Disabled check has no last-run/error series and no disabled metric | C `test_metrics_have_no_run_series_for_a_disabled_check`; metrics.render loops check_ids for both series, CLI passes built ids. Error-series absence is additionally source-inspected. PASS. |
| Reorg while off rewinds cursor and durable ancestor; replay at re-enable | S depth1/4/minimum-ancestor/init replay/no-state tests; C V2.4; I real M1/M2 original D11 composition. PASS. |
| Backfill true is default and production instructions use it | BACKFILL_DEFAULTS enabled=True; example JSON true; deploy Configure copies it (`docs-facts.txt`). Actual deployed config value NOT RUN here, delivery-smoke binding. |
| Re-enable live+backfill: cursor-to-first-attempt checkpoint silently, pinned across retries; no notice | C V2.1/2.3; I stage×partial/runtime/RPC tests and planning rollback. Generic path PASS; real M5 never commits that silent run (QF4). |
| First later run forced, still-true condition once as open under new id; disabled row unchanged | C V2.1 and I real M1/M2 PASS. **FAIL for real M5 QF4:** its retained m5_incidents id collides with terminal disabled framework id; no first-live page. |
| Disabled-period event/healed condition never sent; historical event export alerts=0, healed level no row | C V2.1, all 30 sweep seeds, I original NF1/NF3 message oracles PASS; H healed-key tests and I real-M1 historical transient replay PASS. Export serializes these rows. No unqualified success for a still-true M5 condition (previous row). |
| Catchup state pending/first_live, history_block/since/cause; null pin before first attempt; record disappears at successful first live commit | C V2.1 and readonly CLI status; I restart/failure tests. Cause new/reenabled/reorg checked by C plus H repeat-failure test. PASS as record semantics, M5 does not reach commit. |
| Failure keeps pin, retries each step; runtime check-error vs transport accounting; other checks keep alerting | C V2.3/2.5; I 6 original failure controls, 12 new repeat/live partial/runtime/RPC boundary cases. PASS. Health on M5 failure is real but is no replacement for its missed page. |
| Reorg below boundary before/after first live, any handled depth, repeats silence; durable silent:<id> after record deletion | H NF1/NF2; I original 9 confirmation×cause probes (0/2/12), NF2; H shorter-chain pin. PASS. Fallback phase.history_block tested with global checks that have no silent rows before repeat. |
| Re-enable pin never below earlier boundary | H `test_reenable_after_a_reorg_onto_a_shorter_chain_is_pinned_at_the_earlier_boundary`; C sweep's independently derived max(checkpoint, previous floor). PASS. |
| Repeat does not resend supported live alerts, resolves orphaned support once with canonical-chain explanation; newly true surviving condition sent once | H repeat retained/orphaned/new/healed cases; I real M1/M2/M5 exact same tx/log replays and orphaned branches (14 cases) PASS. M5 internal id divergence NOTE QN2 separately documented. |
| Global D17 repeat while backfill removes affected evaluated/first_live id, once live creates cause=reorg catchup, then forced live | H global NF1/NF2 and backfill-phase-repeat; frozen 67 B cases, including another check blocking global go-live. I repeat boundary failures PASS; no phase key-shape change. |
| While global backfill, disabled check not waited for; after go-live re-enable catches up | C `test_check_disabled_during_the_backfill_is_caught_up_after_go_live`; H disabled-again control. PASS. |
| Re-enable during backfill once global pin known: leave evaluated/first_live, pending per-check record until global silent evaluation pinned at first step; disabled event unsent, next block live | I NF3 True with approved setup correction; H same plus previously-never-evaluated sibling. PASS. |
| Before global history pin exists: global path handles check with no per-check record | C `test_reenabled_while_backfilling_clears_the_marker_without_a_record`. PASS. |
| Backfill false: no new/reenable catchup; historical first run sends; keep enabled in production | C `test_backfill_disabled_keeps_the_base_behaviour[new/reenabled]`; H never-run check on DB with global history control. PASS for gate; no blanket M5 re-enable success implied. |
| Monitor rules separate: M8/M10/M11/M13 dual-sided; M15 Monitor-only; own paused fields | Source fact: 32 templates with paused=false, worker imports M1–M14; all Monitor code/config byte-unchanged, no worker path changes Monitor. `docs-facts.txt`, scope proof. No external Monitor deployment performed. |
| M9 dissent and M11 revert-rate/zero-round-proof sub-switches unchanged; health/resolve behavior | Check source unchanged; six existing test_r4_*disabled* controls in test_m9.py/test_m11.py ran green. PASS. |
| Adding check: no completed run, start_block−1 default cursor, every stored event silent, next later live block; needs backfill | C V2.2(a/d), failed-new-check restart, A4[new] and I NF1[new]. PASS. |
| Upgraded DB checks that already ran get no catchup/forced run/message; normal cadence | C `test_upgraded_live_database_gives_no_catch_up_to_a_check_that_ran`; H no-repeat-above-boundary controls. PASS outside D15 deep-reorg case. |
| Late catchup retry reads pinned state; archive retention needed | C V2.3/2.5 block-specific words via FakeRpc; Chain pins reads. Public archive retention NOT RUN; unchanged operational prerequisite. |
| checks §3 existing schedules / cursor commits, params, reorg, force | F scheduler/event/inbox/near-set/params/cursor tests; B global tests; I partial-generator rollback. PASS. |
| checks §3 added backfill/per-check/repeat/firstlive rows, silent floor=max(silent,global), never-run exception | C V2.1–2.5; H NF/shorter-chain/above-boundary/never-run tests; I 12 repeat/live failure boundary tests. PASS as framework behavior; M5 interaction QF4. |
| checks §4 generic findings/open/escalate/revision/resolve/absence/re-alert/routing and terminal disabled state | F validation/escalation/revision/absence/cadence/routing/restart; S lifecycle/new-id tests. Generic PASS. State reader table's M5 reserved-id reasoning misses reuse of existing own id: **FAIL QF4**, V1.6. |
| checks §4 muted historical event/condition never unmuted by reorg re-find | H NF1 same incident notify=alerts=0; I NF1/NF2 and real transient. PASS. |
| checks §6.2 item6 excludes deferred keys from ctx.reorg_keys, apply and resolve_absent; reorg marker handed to B; live reasons live/force/reorg; retained id vs orphan resolution | H retained/orphan/absence tests; I 12 partial/runtime/RPC repeat/live cases, real replay probes. PASS for operator messages; M5 internal row id can change, NOTE QN2. |
| checks §7 precedence/unknown-id/key/bool/start/false/true/not-built, global-backfill amendment | F config validation; S boolean/not-built tests; I CP1 sibling config and NF3. PASS except promised universal re-enable completion for M5 (QF4). |
| reconcile README status fields, disable/digest/export/reorg, DF1 no writable DB before bad switch | Same V1/DF1/status controls above. PASS; broad still-true re-enable promise **FAIL QF4**. |
| reconcile README re-enable/new silent path and later-reorg repeat, backfill-phase pin | I NF1/NF2/NF3; H repeat/floor/sibling tests PASS; M5 still-true condition not sent, QF4. |
| config.example _comment boolean/start/silent closure/new-reenable requires backfill | Only _comment differs; F example-load/build PASS. Re-enable message claim **FAIL QF4** for real M5. |
| No secrets in tracked files | All 735 tracked files textual, scanned; 10 redacted hits in unchanged fixture/error-test files manually rechecked. No credential found; static heuristic, not a revocation check. |

V2.8: **FAIL**, because the universal still-true/new-id re-enable claim fails for a real M5 page (QF4).
All newly changed D15 historical-silence claims have passing behavioral probes on the candidate. No required docs
claim is accepted merely from green stub coverage when contrary real-check evidence exists.
