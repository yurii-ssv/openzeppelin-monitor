# Check interface (authoritative for wave 2)

This is the frozen contract between the watch worker (`eb_reconcile.py`, CP1B) and the check modules
of the CP2 lanes. It supersedes the sketch in `plans/monitoring-service/plan.md` §6. A check reads the
chain at one block and returns **findings**; the framework owns incidents, routing, re-alerts,
delivery, scheduling, persistence of cursors, live parameters, the inventory and the inbox.

Reference implementation: [`m5_eb_desync.py`](m5_eb_desync.py) (M5, #94775). Tests:
[`../tests/test_framework.py`](../tests/test_framework.py) (framework), [`../tests/test_worker.py`](../tests/test_worker.py) (M5).

## 1. What a lane adds

| File | Content |
|---|---|
| `reconcile/checks/m<N>_<name>.py` | one `Check` subclass per monitor, decorated with `@register` |
| `reconcile/checks/__init__.py` | **one import line** in the lane's own block: `from checks import m3_liquidation_zone  # noqa: F401, E402` |
| `reconcile/tests/test_m<N>.py` | scenarios (`pytestmark = pytest.mark.m<N>`) |
| `reconcile/tests/fixtures_<lane>.py` | lane fixtures (scenario builders on `FakeChain`) |
| `reconcile/config.example.json` | the lane's own `checks.m<N>` object only |

Never edit `eb_*.py`, `alerts.py`, `db.py`, `rpc.py`, `params.py`, `inventory.py`, `inbox.py`,
`notify.py`, `metrics.py`, `checks/base.py`, `checks/registry.py`, `tests/fakechain.py`,
`tests/harness.py` or another lane's files. If the framework is missing something, escalate to the
coordinator (CP3 is the only writer of shared files after wave 2 starts).

## 2. A check

```python
from checks.base import Check, Ctx, Every, Finding, OnEvents
from checks.registry import register


@register
class M3LiquidationZone(Check):
    id = "m3"                                            # unique; also the config key checks.m3
    defaults = {"n_blocks": 50, "n2_blocks": 7_200}      # the config schema: every accepted key + default
    events = {}                                          # extra proxy events to scan (§6), name -> signature
    schema = ""                                          # additive SQL: CREATE TABLE/INDEX IF NOT EXISTS m3_…
    resolve_on_absence = False                           # see §4

    def __init__(self, config: dict, settings: dict):
        super().__init__(config, settings)               # self.config (resolved), self.settings (whole config)
        self.schedule = (Every(300), OnEvents({"ClusterLiquidated", "ClusterDeposited"}))

    def prepare(self, ctx: Ctx) -> None:                 # before the first run, after a parameter reload or a reorg
        self.threshold = ctx.params.liquidation_threshold_period

    def run(self, ctx: Ctx, block: int) -> list[Finding]:
        ...
        return [Finding(check=self.id, key=cluster_id, severity="warn", title="…", body="…",
                        subject={...}, evidence={...})]

    def digest(self, ctx: Ctx) -> list[str]:             # optional: lines for the daily Discord digest
        return []
```

Rules:

- **Read-only.** No transaction of any kind. Read only through `ctx.rpc` (pinned to `block`).
- **Return findings; never write `incidents` or `outbox`**, never send anything.
- **No hard-coded protocol parameters**: read `ctx.params` (live, reloaded on M15 events) or chain state.
  Thresholds that are decisions (D4, D5) are `defaults` keys, overridable in config. Dated spec values
  (§5 of the spec) belong in test fixtures only.
- **Never use a parameter the framework could not refresh** (§5.1): read it from `ctx.params` in
  `prepare` or `run`, let `params.ParamsUnavailable` propagate (never catch it, never fall back to a
  default or a value persisted with `ctx.set_state`), and keep copies only in attributes set by `prepare`.
- **A run is one database transaction**: `prepare` + `run` + applying the findings + advancing the
  cursors commit together, or not at all (an exception rolls everything back and the run is retried at
  the next step). Never call `db.tx()` inside a check.
- **Exceptions**: an `RpcError` counts as an RPC failure of the step (the run is retried; the other
  checks still run). `ParamsUnavailable` skips the run (rolled back, retried next step, counted in
  `ssv_watch_check_errors_total{check}`; the framework's single `params` health message covers it).
  Any other exception sends a `check-error` health message (re-sent every 6 h while it persists) and
  increments `ssv_watch_check_errors_total{check}`; the other checks still run.
- In-memory state is lost on restart; persist what matters in your own tables or with `ctx.set_state`.
  Override `on_reorg(ancestor)` to drop in-memory state derived from rewound blocks. State you persisted
  from blocks above `ctx.reorg_ancestor` (own rows, `ctx.set_state`, open incidents) came from deleted
  blocks: correct it in the run with reason `reorg` (§6.2). **Enumerate the keys to re-check from your
  persisted state, not only from surviving logs/inventory**: a key whose every supporting log was deleted
  is no longer found there. Report every key of `ctx.reorg_keys` again (a finding keeps it open, `resolve=True`
  closes it); a key you do not report is resolved by the framework (§6.2). If the run cannot evaluate,
  **raise** (rolled back, retried every step, the reorg marker kept) — never return silently.

## 3. Schedules

`schedule` is one schedule or a tuple. The check runs when **any** of them is due; `ctx.reasons`
says which. Every run is at the current checkpoint block (intermediate blocks are not replayed).

| Schedule | Due when | `ctx.reasons` |
|---|---|---|
| `Every(seconds)` | `seconds` elapsed since the last successful run (immediately on start) | `interval` |
| `OnEvents({"Kind", …})` | a stored proxy event of one of these kinds is past the check's event cursor | `events` |
| `OnInbox()` | resolved inbox entries are past the check's inbox cursor | `inbox` |
| `NearSetEveryBlock()` | `self.near_set` (a set you maintain) is non-empty and the checkpoint advanced | `block` |
| (framework) | a parameter event was scanned, the parameters were re-read after a reorg, or they became readable again after a failed read (after `prepare`) | `params` |
| (framework) | the chain was reorganised below the checkpoint (durable until the check's run commits, also across a restart; `ctx.reorg_ancestor`) | `reorg` |
| (framework) | `once` / `step(force=True)` | `force` |

Cursors (`meta` keys `cursor:events:<id>`, `cursor:inbox:<id>`) are durable and advance only when a
run commits, so no event or inbox entry is lost across a crash. A reorg rewinds event cursors to the
common ancestor. The first run of a new check on an existing database sees every stored event since
`start_block`; filter by `ev.block` if only recent ones matter.

## 4. Findings and the incident lifecycle

```python
@dataclass(frozen=True)
class Finding:
    check: str                 # must equal the check's id
    key: str                   # incident key within the check: cluster id, round block, wallet, …
    severity: "info" | "warn" | "page"
    title: str                 # one line
    body: str                  # plain text (no markup); the framework adds the header and "Incident: <id>"
    subject: dict = {}         # JSON-serialisable; big numbers (wei, indexes) as strings
    evidence: dict = {}        # JSON-serialisable: tx hashes, blocks, numbers as strings
    resolve: bool = False      # closes the open incident for (check, key)
    notify: bool = True        # False: open/track silently (digest only), e.g. M5 "latent"
    revision: str | None = None   # a value different from the open incident's re-sends it under the same id
    incident_id: str | None = None  # id to use if this finding opens an incident (must be unique)
```

Incidents are keyed by `(check, key)`; two checks never share an incident. Per run, for each finding:

| Situation | Effect | Outbox `kind` |
|---|---|---|
| no open incident, `resolve=False` | open (id = `incident_id` or `<check>-<key[:16]>-<block>`); send unless `notify=False` | `open` |
| open, higher severity | escalate under the same id; send | `escalate` |
| open, lower severity | record silently | — |
| open, new `revision` | send under the same id (a check-defined material change) | `update` |
| open, same severity and revision | update `last_seen_block`, evidence; nothing sent | — |
| open, `resolve=True` | close; if it was sent at page/warn, send a resolution (info, on the incident's route) | `resolved` |
| no finding for an open key | nothing — **absence never resolves**, unless the class sets `resolve_on_absence = True` (then every open key not reported by a run is resolved: only for checks whose every run is complete), or the reorg re-find rule below applies | — |
| **reorg run** (reason `reorg`): no finding for a key of `ctx.reorg_keys` | the one exception to "absence never resolves" (§6.2): resolved, and if it was sent at page/warn an info resolution goes to the incident's route, titled `Evidence left the canonical chain: <title>` and saying "Resolved because its evidence left the canonical chain …" | `resolved` |

Re-alerts while open (`alerts.realert_s`): page every 6 h, warn every 24 h, info never (once + digest).
An incident last seen above the ancestor of a reorg whose re-evaluation run has not committed yet is not
re-alerted until that run keeps it open (§6.2).
Restart idempotency: incidents and outbox keys are durable; an open incident re-found after a restart
sends nothing before its re-alert is due (V1B.4). Use `revision` for "escalate on repetition" rules
(e.g. M1 re-poison) instead of new keys, so the incident id stays stable.

Routing (D2, `alerts.routes`): `page`, `warn`, monitor `health` → Telegram; `info`, `digest` → Discord.
The route is stored in the outbox `delivery` column at enqueue; the transport receipt in `receipt`.
`alerts.mode: "record"` writes JSON lines (`alerts.record_path` or stdout) and sends nothing.

`ctx.health(kind, text, dedup, realert=False)` is for "this check cannot evaluate" conditions
(missing data, an unexpected API shape), never for protocol findings.

Framework health kinds a check never re-sends: `rpc` (consecutive RPC failures), `lag`, `reorg`,
`check-error`, `params` (live parameters unreadable, §5.1), `inbox-unavailable` (configured inbox file
missing or unreadable, §5.2), `inbox-malformed`. While `params` or `inbox-unavailable` holds, the
worker status file (`<db>.status.json`) has `healthy: false` with the reason in `error` and in its
`params` / `inbox` objects, and `ssv_watch_input_unavailable{input="params"|"inbox"}` is 1.

## 5. The context `ctx`

| Field | Type / content |
|---|---|
| `ctx.check_id`, `ctx.block` | the check id; the checkpoint block of this run |
| `ctx.reorg_ancestor` | the lowest common ancestor of the reorgs since the check's last successful run, or None (§6.1) |
| `ctx.reorg_keys` | reorg runs only (else empty): `frozenset` of the keys of this check's **open** incidents whose `last_seen_block` > `ctx.reorg_ancestor`. Each must be reported again by this run, or the framework resolves it (§6.2) |
| `ctx.rpc` | `rpc.Chain` pinned to `block`: `call(to, data) -> bytes`, `call_many([(to, data), …])`, `balance(addr)`, `balances([…])`, `storage(addr, [slots])`, `code(addr)`, `transaction(hash)`, `logs(address, [topic0, …], start, end=None)` (any contract, raw logs). A revert raises `RpcError` with `.data` = revert data. `ctx.rpc.counts` is a `Counter` of JSON-RPC calls made by this run (also stored as `rpc_calls` in `meta run:<id>`). |
| `ctx.params` | live parameters at the last successful reload (`params.py`; failed reads: §5.1): `network_fee`, `network_fee_ssv`, `liquidation_threshold_period`, `liquidation_threshold_period_ssv`, `minimum_liquidation_collateral`, `minimum_liquidation_collateral_ssv`, `operator_fee_increase_limit`, `maximum_operator_fee`, `minimum_operator_eth_fee`, `validators_per_operator_limit`, `quorum_bps`, `cooldown_duration`, `active_oracle_ids` (4 ids, 0 = empty slot), `oracles` ({id: address}), `min_blocks_between_updates`; `ctx.params.block` = read block; `ctx.params.enabled` is False when `views` is not configured; `ctx.params.ok` is False while the values are invalid (`ctx.params.error` says why). Attribute, item or `.get` access; ints, raw units (wei, blocks, bps). Access while invalid raises `params.ParamsUnavailable`. |
| `ctx.inventory(block=None)` | `inventory.Inventory` at the block (cached per block): `.clusters` {`0x…` id: `ClusterInfo`}, `.operators` {op id: {"clusters", "validators"}} over validated clusters, `.invalid` (exist in storage, no candidate tuple validates — M1 input), `.stats()`. `ClusterInfo`: `cluster_id`, `owner`, `operator_ids`, `version` ("ETH" / "SSV" from storage; None = not in storage), `cluster` (`eb_storage.Cluster`, storage-validated, or None), `valid`, `legacy`, `tuple_source` ("logs" / "api"), `sources`, `api_migrated`, `api` (raw API row). API pages are refetched at most every `inventory.api_refresh_s`. |
| `ctx.events()` | iterator of new stored `eb_events.Event`s of the subscribed kinds (empty without `OnEvents`) |
| `ctx.inbox` | (§5.2) list of new resolved `inbox.InboxEntry`: `tx_hash`, `block_hash`, `block`, `sender`, `target`, `selector`, `function`, `monitor`, `observed_at`, `revert_selector` (`0x12e04c87`), `revert_name` (`IncorrectClusterState`, None if unknown), `revert_source` (`eth_call` / `blockscout` / `unresolved`) |
| `ctx.config` | the check's resolved config (§7) |
| `ctx.settings` | the whole worker config (read-only): `proxy`, `views`, `chain_id`, `start_block`, … |
| `ctx.db` | `db.DB`: your own tables via `ctx.db.conn.execute(...)`; stored logs via `cluster_events(cid, upto)`, `latest_cluster_events()`, `events_after(cursor, upto, kinds)` |
| `ctx.state(key, default)`, `ctx.set_state(key, value)` | small JSON state per check (`meta` key `check:<id>:<key>`) |
| `ctx.clock()` | worker time (a `FakeClock` in tests): use it for every time rule |
| `ctx.log`, `ctx.reasons`, `ctx.health(...)` | logger `eb-reconcile.<id>`; §3; §4 |

### 5.1 Live parameters after a failed read

The framework reads the parameters before the first run, again in the step that scans a parameter
event (`eb_events.PARAM_KINDS`), and after every reorg below the checkpoint (values read above the
common ancestor may come from a deleted block, so they are invalidated at the reorg and re-read at the
new checkpoint before any check runs; §6.1). When a read fails (a getter reverts, returns no data, or the RPC
fails), the old values are **invalidated, not kept**:

- every `ctx.params.<name>`, `ctx.params[name]` and `ctx.params.get(name, default)` raises
  `params.ParamsUnavailable` (the default is not returned); `ctx.params.values` is `{}`;
  `ctx.params.block` still names the last good read;
- every check's `prepare` is due again, so a copy taken in `prepare` is refreshed or the run fails;
- a run that raises `ParamsUnavailable` is rolled back (cursors do not advance, no incident changes)
  and retried at the next step; checks that never read parameters keep running;
- the worker sends one `params` health message (re-sent every `health_realert_s`), reports
  `healthy: false`, and retries the read at every step;
- the first successful read runs every check with reason `params` after `prepare`, as for an event.

A threshold check therefore either evaluates with values read after the last parameter event or does
not evaluate at all. Do not catch `ParamsUnavailable`, and never persist a parameter value as a fallback.

### 5.2 Inbox availability

An enabled inbox (`inbox.path` set) must exist as a regular, readable file. An existing empty file is a
valid, empty inbox. A missing file, a directory or a read error is not "no failed transactions": the
worker sends an `inbox-unavailable` health message (once per outage, re-sent every
`health_realert_s`), reports `healthy: false`, and checks scheduled `OnInbox` receive only entries
ingested before the outage (their other schedules still run). The deployment creates the empty
file before the Monitor's first append (the worker mounts the inbox read-only).

**Confirmation gate.** The Monitor appends at its own depth (`confirmation_blocks: 2` in
`deploy/config/networks/evm_ethereum_mainnet.json`); the worker's checkpoint is `confirmations` (12)
deep. An entry is delivered (`ctx.inbox`) only once the checkpoint has reached its `block` **and** its
`block_hash` equals the canonical hash at that block (looked up once per entry, batched per step):

- **latency**: inbox evidence reaches checks about `confirmations − 2` blocks (≈ 2 min at 12) after the
  Monitor wrote it, together with the logs of the same block, so `entry.block <= ctx.block` always holds
  and correlations with stored events of that block are complete;
- an entry of an orphaned block is marked `canonical = 0`, logged, counted in the status file
  (`inbox.orphaned`) and `ssv_watch_inbox_entries{state="orphaned"}`, and **never delivered**; a
  transaction re-included in another block arrives as a new `(tx_hash, block_hash)` line and is
  delivered once;
- entries are delivered in file order: an entry not yet confirmed holds the ones after it
  (`inbox.held`, `state="held"`);
- if the hash lookup fails, entries stay held (never delivered unverified), the status file reports
  `healthy: false` with the reason, `ssv_watch_input_unavailable{input="inbox"}` is 1, and the lookup is
  retried at every step.

## 6. Events

The scan stores every proxy log whose topic0 is tracked (`eb_events.TOPIC0`): the cluster kinds
(`ValidatorAdded`, `ValidatorRemoved`, `ClusterBalanceUpdated`, `ClusterLiquidated`,
`ClusterReactivated`, `ClusterDeposited`, `ClusterWithdrawn`, `ClusterMigratedToETH`; decoded with
`owner`, `operator_ids`, `cluster`, `cluster_id`, and kind-specific fields), `RootCommitted`
(`root`, `ref_block`), `OracleReplaced` (`extra` = oracleId/old/new) and the parameter events
`eb_events.PARAM_KINDS` (M15 list, raw). Any other proxy event a check needs is declared in its class
`events = {"WeightedRootProposed": "WeightedRootProposed(bytes32,uint64,uint256,uint256,uint32,address)"}`
(canonical signature); it is then scanned and stored raw: `ev.extra = {"topics": [topic1, …], "data": "0x…"}`
— decode it in the check. When a new topic appears on an existing database, its history from
`start_block` is backfilled once at start (a database from before the framework records its topic set
at its first start, without backfill). Events of other contracts are read with `ctx.rpc.logs`.

`Event` fields: `kind`, `block`, `log_index`, `tx_index`, `tx_hash`, `block_hash`, `cluster_id` (bytes),
`owner`, `operator_ids`, `cluster`, `pubkey`, `shares_len`, `ref_block`, `eb`, `root`, `extra`, `pos`.

### 6.1 Reorg semantics

`confirmations` (12) keeps the checkpoint below ordinary reorgs; a deeper one is detected at the next
step (the checkpoint's recorded hash, or the block itself on a shorter chain, is no longer canonical)
and rewound to the newest recorded block whose hash is still canonical (the **ancestor**; searched over
`reorg_search_depth` recorded blocks, beyond which the worker stops with an error). One `reorg` health
message is sent per distinct (checkpoint, ancestor). Every piece of state the worker keeps across
blocks, with its handling for a reorg of depth 1 and of depth N (no row differs between the two except
where stated):

| State | Where | Handling on a reorg | Status before repair 2 → test |
|---|---|---|---|
| Stored logs | `logs` | rows above the ancestor deleted (same transaction as the rewind), the new chain is rescanned | correct → `test_worker::test_reorg_rewinds_and_replays` (1), `test_reorg_state::test_reorg_depth_n_replays_only_new_chain_events_once` (N) |
| Logs read while the chain changes | `scan` | every event block within `reorg_search_depth` of the target is compared with its canonical hash (one batched lookup); a mismatch (a reorg between `eth_getLogs` and the hash read, or a load-balanced RPC answering from another fork) fails the step and the window is re-read | **wrong** (orphan logs stored under the new checkpoint hash, never detected) → `test_scan_rejects_logs_of_a_block_reorganised_during_the_scan` |
| Recorded block hashes, checkpoint | `blocks`, `meta checkpoint` | rows above the ancestor deleted; checkpoint = ancestor; a checkpoint block the node does not have (shorter new chain) counts as non-canonical | **wrong** for a shorter chain (RPC failure every step until the chain regrew) → `test_near_set_check_runs_after_a_reorg_onto_a_shorter_chain` |
| Event cursors | `meta cursor:events:<id>` | rewound to `(ancestor, end of block)`: the new chain's events are delivered once | correct → `test_framework::test_scheduler_reorg_resets_event_cursor` (1), `test_reorg_depth_n_replays_only_new_chain_events_once` (N) |
| Live parameters | `Worker.params` (memory) | invalidated at the reorg, re-read at the new checkpoint before any check runs; a failed re-read is §5.1 (`ParamsUnavailable`, `healthy: false`, retried every step) | **wrong** (QA attempt 2: orphan value used indefinitely) → `test_qa2_regressions` (Appendix A, B), `test_reorg_params_reread_failure_blocks_consumers_until_it_succeeds` |
| `prepare` copies | check attributes, `Worker.prepared` | every check's `prepare` is due again (reasons `reorg`, `params`) | **wrong** (same finding) → Appendix A `cached`, `test_reorg_depth_n_rereads_params_and_every_prepare_copy` |
| Inventory cache | `InventoryCache` (memory, keyed by block number, which the new chain reuses) | cleared; rebuilt from the new chain's logs and storage | correct, untested → `test_reorg_invalidates_the_inventory_cache[1,3]` |
| Inventory API rows | `InventoryCache` (memory) | off-chain and untrusted: every tuple is re-validated against storage at the evaluated block | not block-derived → `test_framework::test_inventory_validates_tuples_against_storage` |
| Inbox byte offset, `(tx_hash, block_hash)` dedup | `meta inbox:offset`, `inbox` | file position, not chain-derived; a re-included transaction has a new block hash, hence a new entry | correct → `test_inbox_entry_of_an_orphaned_block_is_never_delivered_and_its_reinclusion_is` |
| Inbox delivery | `inbox.canonical` | §5.2 gate; the rewind resets the flag of entries above the ancestor, so an undelivered entry is re-checked on the new chain | **wrong** (entries delivered before their block was confirmed and from orphaned blocks) → the five `test_inbox_*` / `test_reorg_below_the_checkpoint_rechecks_undelivered_inbox_entries` tests in `test_reorg_state.py` |
| Inbox cursors | `meta cursor:inbox:<id>` | not rewound (ids, not blocks): an entry delivered before a reorg was canonical at or below the checkpoint of that time, exactly like the logs of its block; the check corrects what it derived from it via `ctx.reorg_ancestor` | by design |
| Per-check reorg notice | `meta reorg:<id>`, `Worker.pending` | durable marker written with the rewind (lowest ancestor kept), cleared when the check's run commits; replayed at start (`on_reorg` + reason `reorg`) if the process died first; `ctx.reorg_ancestor` | **wrong** (in memory only: lost by a crash between the rewind and the run) → `test_reorg_marker_survives_a_restart_and_gives_the_lowest_ancestor` |
| `NearSetEveryBlock` progress | `Worker.last_block` (memory) | reset: the check runs at the new checkpoint (`reorg`, `block`) and at every following block | was masked by the shorter-chain failure → `test_near_set_check_runs_after_a_reorg_onto_a_shorter_chain` |
| `Every` progress | `Worker.last_run` (memory, time) | not block-derived; the `reorg` reason runs the check anyway | by design |
| Check in-memory state | check attributes (M5 `dirty`, a check's `near_set`) | `on_reorg(ancestor)`, also at start for a reorg whose run had not committed | M5: `test_reorg_rewinds_and_replays` (1), `test_m5_reorg_depth_n_reevaluates_on_the_new_chain` (N) |
| Check persisted state | own tables, `ctx.set_state` | the check's job in the run with reason `reorg` (§6.2 contract): rows derived from blocks above `ctx.reorg_ancestor` came from deleted blocks; the keys to re-check are enumerated from the persisted rows themselves, not only from surviving logs | contract (§2, §6.2); M5 rows below |
| Open incidents found on deleted blocks | `incidents` | kept until the check's reorg run (a sent alert cannot be unsent; the `reorg` health message says so). **Reorg re-find rule** (§6.2): every open incident last seen above the ancestor is in `ctx.reorg_keys`; one the run does not report again is resolved ("evidence left the canonical chain", info on its route); one reported again stays open at the reported severity (a lower one recorded silently); one the run resolves closes with the check's text | **wrong** before repair 3 (QA attempt 3: absence never resolves, so a key whose evidence vanished stayed open and re-paged every 6 h) → `test_reorg_refind::test_reorg_key_not_reported_is_resolved_as_evidence_left_the_canonical_chain`, `…surviving_evidence_reported_nothing_is_resolved_then_reopened`, `…reported_again_is_kept_and_a_lower_severity_is_recorded_silently`, `…reorg_keys_are_only_incidents_last_seen_above_the_ancestor`, `…re_find_rule_survives_a_restart_before_the_reorg_run`, `…re_find_text_wins_over_resolve_on_absence` |
| Re-alert schedule of open incidents | `incidents.last_alert_at`, `alerts` | an incident last seen above the ancestor of a pending reorg marker (`meta reorg:<id>`) is not re-alerted until the check's reorg run commits; it then closes (rule above) or resumes its cadence | **wrong** before repair 3 (a reorg run that kept failing let orphan incidents re-page) → `test_reorg_refind::test_reorg_run_that_raises_keeps_incidents_marker_and_defers_realerts` |
| A check that cannot evaluate in its reorg run | `run_check` transaction | it raises: the run rolls back (no incident change, cursors and marker kept), `check-error` health, retried every step; while any marker is pending the status file is `healthy: false` (`reorg re-evaluation pending for check(s) …`, `status.reorg_pending`) | new → same test; M5: `test_reorg_refind::test_m5_layout_guard_failure_in_the_reorg_run_raises_and_retries` |
| Undelivered outbox rows of an orphaned incident | `outbox` (`sent_at` NULL) | history as of their enqueue: delivered in order, followed by the resolution that says the evidence left the canonical chain (never deleted, never re-sent) | by design → `test_reorg_refind::test_undelivered_alert_of_an_orphaned_incident_is_followed_by_its_resolution` |
| Incidents derived from inbox entries | `incidents`, `inbox.canonical` | a delivered entry is not delivered again (inbox cursor): in its reorg run the check re-derives `ctx.reorg_keys` from `inbox` rows with `canonical = 1` (re-confirmed before the run in the same step); an entry of a deleted block becomes `canonical = 0` and its key, not reported, is resolved by the rule | **wrong** before repair 3 (stayed open) → `test_reorg_refind::test_inbox_derived_incidents_after_a_reorg` |
| M5 `evaluations`, `m5_incidents` | M5 tables | the reorg run is a full sweep over the clusters with stored logs **and** every cluster with an evaluation or an open M5 incident (and `ctx.reorg_keys`): a cluster with no log left loses its evaluation and its incident is resolved ("no event of this cluster remains on the canonical chain"); a confirmed/candidate incident the new chain only supports as latent is resolved plainly and re-tracked as a silent latent one; an UNKNOWN re-evaluation re-reports the key (stays open); a resolution of an incident first seen above the ancestor says its evidence left the canonical chain | **wrong** before repair 3 (QA attempt 3 finding; also the latent case, previously asserted on the evaluation only) → `test_qa3_regressions::test_qa3_appendix_a_orphan_repro[0,12]`, `test_qa3_appendix_b::test_m5_orphan_only_cluster_incident_removed[0,12]`, `test_reorg_refind::test_m5_reorg_removing_only_the_confirming_removal_resolves_plainly_and_tracks_latent`, `…m5_reorg_unknown_re_evaluation_keeps_the_incident_open`, `…m5_reorg_resolution_of_an_incident_raised_on_deleted_blocks_says_so`, `…m5_evaluation_of_a_cluster_whose_history_was_deleted_is_dropped` |
| M5 digest input | `last_full_evals` (memory) | refreshed by the reorg run (a full sweep) | **wrong** before repair 3 (deleted clusters listed until the next hourly sweep) → `test_reorg_refind::test_m5_reorg_run_is_a_full_sweep_so_the_digest_forgets_deleted_clusters` |
| Default incident id `<check>-<key>-<block>` | `incidents.id` | an id taken by the deleted chain's incident gets `-2`, `-3`, … (a check-proposed `incident_id` must still be unique) | **wrong** (`ValueError`, check-error, the run lost) → `test_default_incident_id_reused_by_a_reorganised_block_gets_a_suffix` |
| Outbox keys with a block or revision (`…:escalate:<sev>:<block>`, `…:resolved:<block>`, `<id>:<revision>`, `digest:<block>`) | `outbox.key` | a send the incident state machine decided is never dropped: a taken key gets `#2`, `#3`, … (health keys still de-duplicate by design) | **wrong** (silently dropped while `incidents.alerts` counted it) → `test_escalation_replayed_on_a_reorganised_block_is_sent_again` |
| Status file, `/metrics` checkpoint and lag | `meta` | read at each write/scrape: follow the rewind | correct → `test_reorg_depth_n_replays_only_new_chain_events_once` (gauge) |
| Reorg deeper than the search | `check_reorg` | the worker stops (`reorg deeper than … recorded blocks`) instead of guessing an ancestor | correct, untested → `test_reorg_deeper_than_the_search_stops_the_worker` |

Whole-class check: `tests/test_reorg_sweep.py` (90 deterministic cases: fork depth 1–30 × confirmations 0/2/12;
M5 survivor / latent-then-orphaned-removal / orphan-only clusters, inbox lines, a parameter change and an
injected parameter outage after the reorg, re-inclusion on the new chain, shorter and longer new chains; M5
plus a second check module) compares the worker that lived through the reorg with a fresh worker on the final
chain: same open incidents (no open incident without canonical evidence, no dropped alert), every page after
the reorg supported by the canonical chain at its block (including the 6 h re-alerts), `healthy: true`.

### 6.2 Reorg re-find rule (contract for every check)

A key that a check only re-evaluates when it enumerates it from surviving data (logs, the inventory, new
inbox entries) is never revisited once a reorg deletes that data. The framework therefore applies one rule
to the run with reason `reorg` (`ctx.reorg_ancestor` = A, set until that run commits, also across a restart):

1. `ctx.reorg_keys` = the keys of the check's **open** incidents with `last_seen_block > A` (their latest
   support may come from a deleted block). Incidents last seen at or below A are untouched.
2. The run must report every key of `ctx.reorg_keys` again, evaluated on the new chain: a finding (any
   severity, `notify`, `revision`) keeps the incident open under its id — a lower severity is recorded
   silently, as always; `resolve=True` closes it with the check's own text.
3. A key of `ctx.reorg_keys` the run does not report is **resolved by the framework** after the findings are
   applied (before `resolve_on_absence`): state `resolved`, `evidence.resolved_by_reorg = {ancestor, block}`,
   and, if it was sent at page/warn, an info message on the incident's route titled
   `Evidence left the canonical chain: <title>`: "Resolved because its evidence left the canonical chain: a
   chain reorganisation replaced the blocks after A, and check <id> did not find this condition again on the
   new chain (re-evaluated at block B). The earlier alert(s) for this incident were based on blocks that are
   no longer canonical." It is never re-paged; if the condition is found again later it opens a new incident
   (new id, normal `open` alert).
4. A check that cannot evaluate in its reorg run (an input it needs is missing, a guard fails) must **raise**
   (or re-report the keys it must keep, e.g. an UNKNOWN evaluation): the run is rolled back, retried every
   step, the marker and `ctx.reorg_keys` stay, its incidents last seen above A are not re-alerted, and the
   worker reports `healthy: false` until the run commits. Returning `[]` instead closes every reorg key.
5. Own tables and `ctx.set_state`: enumerate the keys to re-check from what you persisted (e.g. `SELECT key
   FROM m<N>_state`) as well as from surviving data; delete or rewrite rows derived from blocks above A.
   Inbox-derived keys: re-derive from `inbox` rows with `canonical = 1` (`SELECT tx_hash FROM inbox WHERE
   canonical = 1`); delivered entries are not delivered again.

Pattern (see `tests/test_reorg_sweep.py::Watch`, `tests/test_reorg_refind.py::FailedTxWatch`):

```python
def run(self, ctx, block):
    found = {f.key: f for f in self.evaluate(ctx, block)}        # from surviving data, as usual
    if ctx.reorg_ancestor is not None:
        for key in ctx.reorg_keys | self.persisted_keys(ctx):    # what this check stored or opened
            if key not in found:
                found[key] = self.reevaluate(ctx, key, block)    # a finding, a resolve=True, or raise
    return list(found.values())
```

**Plan §6 amendment** (for the coordinator; plan.md is not edited by this lane). In "Incident lifecycle",
the sentence "absence of a finding never resolves unless the check declares `resolve_on_absence = True`"
gains: "…, except in a check's run with reason `reorg`: an open incident of that check last seen above the
reorg's common ancestor (`ctx.reorg_keys`) that the run does not report again is resolved with a resolution
saying its evidence left the canonical chain (info, on the incident's route); such incidents are not
re-alerted while that run is pending." In "Check interface", the `Ctx` offers list gains `reorg_ancestor`
and `reorg_keys`, and `prepare`'s comment gains "and after a reorg" (repair 2). `Check`, `Finding` and the
schedules are unchanged.

## 7. Configuration

```json
"checks": {
  "m3": {"enabled": true, "n_blocks": 50, "n2_blocks": 7200}
}
```

`build()` resolves a check's config as `defaults` ← (legacy top-level keys named in `legacy_keys`, M5
only) ← `checks.<id>`. A key not in `defaults` or an unknown check id is a startup error. Every
registered check is enabled unless `"enabled": false`. Other worker sections: `alerts` (mode, env
names of secrets, label, explorer, `realert_s`, `routes`, `health_realert_s`, `digest_interval_s`,
`record_path`), `inbox` (`path`, `blockscout`, `enabled`; a missing file is health, §5.2), `inventory` (`api_url` — null disables
the API —, `api_refresh_s`), `metrics` (`enabled`, `host`, `port`). See `../config.example.json`.

## 8. Test helpers

`tests/fakechain.py`: `FakeChain` (`mine`, `reorg`, `emit_event(kind_or_topic0, topics, data, address)`,
`stub_call(to, data_or_selector, result=…|revert=…)`, `set_balance(addr, wei)`, `add_tx(sender, to,
input, value)`, `write(slot, value)`, `calls` log of every `eth_call`, `down` to cut the RPC, `max_range`,
`drop_logs_in`), `MiniSSV` (register/remove/commit/update/liquidate/reactivate/migrate/legacy cluster with
real logs and storage), `FakeClock` (`advance(s)`), `keys(n, start)`. Stubs and balances take effect from
the pending block (`chain.number`); mine once before the block you read.

`tests/harness.py`: `make_worker(tmp_path, [instances] | only=["m3"], checks={...}, clock=…, views=…)`,
`advance(w)`, `outbox(w, where)`, `Capture` (notifier; `.sent` = (severity, key, text, route)),
`stub_params(chain, **values)` (every Views getter; call again + emit the parameter event to model a
change), `api_row(...)` (one SSV API row; set `w.inventory.http_get` to serve pages and pass
`inventory={"api_url": "https://api.invalid"}`).

Patterns for the V2x rows:

| Row | Pattern |
|---|---|
| V2x.1 scenarios | one test per spec trigger line asserting `outbox(w)` severity/kind, plus a benign twin asserting no page/warn |
| V2x.2 `thresholds_from_chain` | `stub_params(chain, X=new)` + `chain.emit_event("<Param>Updated", [], u256(new))` + `chain.mine()` → the alert changes with no code change (see `test_params_reload_on_parameter_event_calls_prepare`) |
| V2x.3 `restart` | second `make_worker(..., start_block=w.cfg.start_block, activation_block=w.cfg.activation_block)` on the same `tmp_path`; assert one outbox row (see `test_restart_idempotent_single_outbox_row`) |
| V2x.4 `rpc_budget` | `w.db.meta("run:m<N>")["rpc_calls"]` (or `ctx.rpc.counts`) ≤ bound for a 350-cluster inventory |
| clock rules | `FakeClock.advance(...)` then `advance(w)`; never `time.time()` in a check |

Selecting tests: run the file (`pytest reconcile/tests/test_m3.py`); `-k m3` matches the marker but
`-k` is substring matching (`-k m1` also selects m10–m16), so prefer file paths in the attempt log.

## 9. Running

```bash
reconcile/.venv/bin/python -m pytest -q reconcile/tests
reconcile/.venv/bin/ruff check reconcile
reconcile/.venv/bin/python reconcile/eb_reconcile.py once --config <cfg> --serve-metrics --hold 30
reconcile/.venv/bin/python reconcile/tests/fake_once.py --port 9464 --hold 30   # once on the fake chain
```
