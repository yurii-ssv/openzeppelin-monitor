"""The check interface (frozen for wave 2; documented in checks/README.md).

A check is a class with an `id`, a `schedule`, optional per-check `defaults` (its config schema),
optional additive SQL `schema`, `prepare(ctx)` and `run(ctx, block) -> list[Finding]`. Checks
never send alerts and never write the outbox or the incidents table: they return findings.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, Literal

from alerts import SEVERITIES

Severity = Literal["info", "warn", "page"]


# ---------------------------------------------------------------- schedules


@dataclass(frozen=True)
class Every:
    """Run when `seconds` have passed since the check's last run (and once at start)."""

    seconds: float


@dataclass(frozen=True)
class OnEvents:
    """Run when the scan stored a new proxy event of one of these kinds (eb_events names)."""

    kinds: frozenset[str]

    def __init__(self, kinds):
        object.__setattr__(self, "kinds", frozenset(kinds))


@dataclass(frozen=True)
class OnInbox:
    """Run when the failed-transaction inbox has new resolved entries."""


@dataclass(frozen=True)
class NearSetEveryBlock:
    """Run at every new checkpoint block while the check's `near_set` is non-empty."""


Schedule = Every | OnEvents | OnInbox | NearSetEveryBlock


# ---------------------------------------------------------------- findings


@dataclass(frozen=True)
class Finding:
    check: str
    key: str  # incident key within the check (cluster id, round block, wallet, …)
    severity: Severity
    title: str
    body: str
    subject: dict = field(default_factory=dict)  # JSON-serialisable; numbers that may exceed 2**53 as strings
    evidence: dict = field(default_factory=dict)
    resolve: bool = False  # closes the open incident for (check, key)
    # Framework extensions (all optional):
    notify: bool = True  # False: open/track the incident without sending (digest only)
    revision: str | None = None  # a value different from the open incident's re-sends it under the same id
    incident_id: str | None = None  # id to use when this finding opens an incident (must be unique)

    def __post_init__(self):
        if self.severity not in SEVERITIES:
            raise ValueError(f"severity {self.severity!r} is not one of {SEVERITIES}")
        if not self.check or not self.key:
            raise ValueError("a finding needs a check id and a key")
        json.dumps(self.subject)
        json.dumps(self.evidence)


# ---------------------------------------------------------------- context


@dataclass
class Ctx:
    """What a check sees during one run. Everything chain-related is pinned to `block`."""

    check_id: str
    block: int
    rpc: Any  # rpc.Chain: call / call_many / balance / balances / storage / logs, pinned to `block`
    db: Any  # db.DB; per-check tables only (never incidents/outbox), no db.tx() (a run is one transaction)
    params: Any  # params.Params: live protocol parameters, reloaded on M15 events
    config: dict  # the check's own config: its `defaults` merged with config["checks"][id]
    settings: dict  # the whole worker config (proxy, views, chain_id, …), read-only
    clock: Callable[[], float]
    log: logging.Logger
    reasons: frozenset[str]  # why it runs: interval, events, inbox, block, force, params, reorg (digest)
    # lowest common ancestor of the reorgs since the check's last successful run (None: no reorg); state the
    # check derived from blocks above it (own tables, ctx.set_state, open incidents) came from deleted blocks
    reorg_ancestor: int | None = None
    _events: Callable[[], Iterator] = None
    _inbox: Callable[[], list] = None
    _inventory: Callable[[int | None], Any] = None
    _health: Callable[..., bool] = None

    def events(self) -> Iterator:
        """New stored proxy events of the kinds the check subscribes to (OnEvents), in chain order."""
        return self._events() if self._events else iter(())

    @property
    def inbox(self) -> list:
        """New resolved inbox entries (OnInbox), oldest first."""
        return self._inbox() if self._inbox else []

    def inventory(self, block: int | None = None):
        """Clusters with storage-validated tuples, operators and legacy flag at `block` (cached per block)."""
        return self._inventory(self.block if block is None else block)

    def health(self, kind: str, text: str, dedup: str, realert: bool = False) -> bool:
        """A monitor-health message (not an incident): the check cannot evaluate something."""
        return self._health(kind, text, dedup, realert=realert, check_id=self.check_id)

    def state(self, key: str, default=None):
        return self.db.meta(f"check:{self.check_id}:{key}", default)

    def set_state(self, key: str, value) -> None:
        self.db.set_meta(f"check:{self.check_id}:{key}", value)


# ---------------------------------------------------------------- check base class


class Check:
    id: str = ""
    schedule: Schedule | tuple[Schedule, ...] = ()
    defaults: dict = {}  # config schema: every accepted key with its default
    schema: str = ""  # additive per-check tables: CREATE TABLE/INDEX IF NOT EXISTS statements
    events: dict[str, str] = {}  # extra proxy events to scan: name -> canonical signature
    resolve_on_absence: bool = False  # open incidents not re-reported by a run are resolved
    near_set: frozenset = frozenset()  # NearSetEveryBlock: run every block while non-empty

    def __init__(self, config: dict, settings: dict):
        self.config, self.settings = config, settings

    def schedules(self) -> tuple:
        return self.schedule if isinstance(self.schedule, tuple) else (self.schedule,)

    def prepare(self, ctx: Ctx) -> None:
        """Read live parameters; called before the first run, after any parameter reload and after a reorg."""

    def run(self, ctx: Ctx, block: int) -> list[Finding]:
        raise NotImplementedError

    def digest(self, ctx: Ctx) -> list[str]:
        """Lines for the daily digest (info-level state worth a look)."""
        return []

    def on_reorg(self, ancestor: int) -> None:
        """The chain was rewound to `ancestor`; drop in-memory state derived from later blocks. Also called at
        start when a reorg's re-run had not committed before a restart."""
