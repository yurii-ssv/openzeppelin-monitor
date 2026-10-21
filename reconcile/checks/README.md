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

    def prepare(self, ctx: Ctx) -> None:                 # before the first run and after a parameter event
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
  Override `on_reorg(ancestor)` to drop in-memory state derived from rewound blocks.

## 3. Schedules

`schedule` is one schedule or a tuple. The check runs when **any** of them is due; `ctx.reasons`
says which. Every run is at the current checkpoint block (intermediate blocks are not replayed).

| Schedule | Due when | `ctx.reasons` |
|---|---|---|
| `Every(seconds)` | `seconds` elapsed since the last successful run (immediately on start) | `interval` |
| `OnEvents({"Kind", …})` | a stored proxy event of one of these kinds is past the check's event cursor | `events` |
| `OnInbox()` | resolved inbox entries are past the check's inbox cursor | `inbox` |
| `NearSetEveryBlock()` | `self.near_set` (a set you maintain) is non-empty and the checkpoint advanced | `block` |
| (framework) | a parameter event was scanned, or the parameters became readable again after a failed read (after `prepare`) | `params` |
| (framework) | the chain was reorganised below the checkpoint | `reorg` |
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
| no finding for an open key | nothing — **absence never resolves**, unless the class sets `resolve_on_absence = True` (then every open key not reported by a run is resolved: only for checks whose every run is complete) | — |

Re-alerts while open (`alerts.realert_s`): page every 6 h, warn every 24 h, info never (once + digest).
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

The framework reads the parameters before the first run and again in the step that scans a parameter
event (`eb_events.PARAM_KINDS`). When a read fails (a getter reverts, returns no data, or the RPC
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
