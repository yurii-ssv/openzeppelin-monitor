"""Pure replay and classification for the #94775 reference-adjusted accounting check.

No I/O here: the worker feeds ordered events for one cluster plus a storage read
at block B, and gets back a classification. Rules mirror the pinned contracts:

  SSVValidators._bulkRegisterValidator   vUnits += n*10_000 only if vUnits > 0
  SSVValidators._bulkRemoveValidator     ETH: vUnits -= n*10_000 if vUnits > 0, then 0 when count hits 0
                                         SSV: vUnits = 0 when count hits 0 (PR #614 guard only)
  SSVClusters._updateEBSnapshot          vUnits = ebToVUnits(E), lastRootBlockNum = S, lastUpdateBlock = block
  SSVClusters.migrateClusterToETH        version -> ETH; vUnits = 0 if count == 0 (PR #614 guard only)
  The PR #614 guards exist only from the hotfix module upgrade on (`guards_from`, a log position);
  before it, a legacy SSV removal or migration left a zero-validator snapshot untouched.
  liquidate / reactivate / deposit / withdraw leave vUnits unchanged
"""

from __future__ import annotations

from dataclasses import dataclass, field

from eb_events import Event
from eb_storage import BPS_DENOMINATOR, EBSnapshot, eb_to_vunits

ETH = "ETH"
SSV = "SSV"

# Classification values.
CLEAN = "clean"  # reference-adjusted == observed
LATENT = "latent"  # overwritten baseline, added keys still registered (candidate only)
CONFIRMED = "confirmed"  # matched add(S..U) -> update U -> remove(after U), positive discrepancy
OVERBILLED = "overbilled"  # negative discrepancy (honest removal between S and U); not #94775
IMPLICIT = "implicit"  # no EB update in history: vUnits must be 0, billed at count*10_000
IMPLICIT_RESET = "implicit_reset"  # updated once, then count hit 0: snapshot back to 0
NOT_ETH = "not_eth"  # legacy SSV cluster: EB snapshot is not billed
UNKNOWN = "unknown"  # incomplete or incoherent inputs: never treat as clean


@dataclass
class StorageView:
    """Raw words read at block B."""

    block: int
    eb_word: int
    eth_cluster_word: bytes  # s.ethClusters[id]
    ssv_cluster_word: bytes  # s.clusters[id]


@dataclass
class Evaluation:
    cluster_id: bytes
    owner: str
    operator_ids: tuple[int, ...]
    block: int
    status: str
    reasons: list[str] = field(default_factory=list)
    version: str | None = None
    active: bool | None = None
    validator_count: int | None = None
    ref_block: int | None = None  # S of the last consumed root
    update: Event | None = None
    eb_at_ref: int | None = None
    reference_vunits: int | None = None
    replay_vunits: int | None = None
    observed_vunits: int | None = None
    billed_vunits: int | None = None  # observed, or count*10_000 when observed == 0
    discrepancy_vunits: int | None = None  # signed: reference - observed
    matched_keys: list[str] = field(default_factory=list)
    adds_in_window: list[Event] = field(default_factory=list)  # adds in (S, U)
    removes_after_update: list[Event] = field(default_factory=list)  # matched removals after U

    @property
    def shortfall_vunits(self) -> int:
        return max(0, self.discrepancy_vunits or 0)


def version_at(pos: tuple[int, int], events: list[Event], current_version: str | None) -> str | None:
    """Migration (SSV -> ETH) is the only version transition, so a migration event fixes it."""
    for ev in events:
        if ev.kind == "ClusterMigratedToETH":
            return SSV if pos < ev.pos else ETH
    for ev in events:
        if ev.kind == "ValidatorAdded" and ev.pos <= pos:
            return ETH  # registration is only accepted into ETH clusters
    return current_version


def apply(v: int, version: str | None, ev: Event, anomalies: list[str],
          guards_from: tuple[int, int] = (0, 0)) -> tuple[int, str | None]:
    count = ev.cluster.validator_count if ev.cluster else None
    guards = ev.pos >= guards_from
    if ev.kind == "ValidatorAdded":
        if version == SSV:
            anomalies.append(f"ValidatorAdded on SSV cluster at {ev.pos}")
        version = ETH
        if v > 0:
            v += BPS_DENOMINATOR
    elif ev.kind == "ValidatorRemoved":
        if version == ETH:
            if v > 0:
                v -= BPS_DENOMINATOR
                if v < 0:
                    anomalies.append(f"vUnits underflow at {ev.pos}")
                    v = 0
                if count == 0:
                    v = 0
        elif version == SSV:
            if count == 0 and guards:
                v = 0
        else:
            anomalies.append(f"unknown version at removal {ev.pos}")
    elif ev.kind == "ClusterBalanceUpdated":
        v = eb_to_vunits(ev.eb)
    elif ev.kind == "ClusterMigratedToETH":
        version = ETH
        if count == 0 and guards:
            v = 0
    return v, version


def simulate(start_v: int, events: list[Event], all_events: list[Event], current_version: str | None,
             anomalies: list[str], skip_updates: bool, guards_from: tuple[int, int] = (0, 0)) -> int:
    v = start_v
    for ev in events:
        if skip_updates and ev.kind == "ClusterBalanceUpdated":
            continue
        version = version_at(ev.pos, all_events, current_version)
        if ev.kind == "ValidatorRemoved" and version is None:
            version = current_version
        v, _ = apply(v, version, ev, anomalies, guards_from)
    return v


def evaluate(events: list[Event], storage: StorageView | None, history_start_block: int,
             complete_from_activation: bool, guards_from: tuple[int, int] = (0, 0)) -> Evaluation:
    """Classify one cluster at storage.block.

    events: every logged event for this cluster up to and including storage.block, any order.
    history_start_block: first block whose logs are known to be complete.
    complete_from_activation: history starts at the v2.0.0 activation (all EB snapshots were 0 there).
    guards_from: (block, logIndex) of the PR #614 module upgrade; (0, 0) when the guards always applied.
    """
    events = sorted((e for e in events if storage is None or e.block <= storage.block), key=lambda e: e.pos)
    first = events[0]
    ev_out = Evaluation(first.cluster_id, first.owner, first.operator_ids,
                        storage.block if storage else events[-1].block, UNKNOWN)
    latest = events[-1].cluster
    ev_out.active = latest.active
    ev_out.validator_count = latest.validator_count

    if storage is None:
        ev_out.reasons.append("storage unavailable")
        _matched(ev_out, events)
        return ev_out

    eth_set = any(storage.eth_cluster_word)
    ssv_set = any(storage.ssv_cluster_word)
    current_version = ETH if eth_set else SSV if ssv_set else None
    ev_out.version = current_version
    if eth_set and ssv_set:
        ev_out.reasons.append("both ETH and SSV cluster records set")
    if current_version is None:
        ev_out.reasons.append("cluster record missing")
    else:
        stored = storage.eth_cluster_word if eth_set else storage.ssv_cluster_word
        if latest.hash_data() != stored:
            ev_out.reasons.append("latest event Cluster does not hash to stored cluster data (missing logs?)")

    snap = EBSnapshot.decode(storage.eb_word)
    ev_out.observed_vunits = snap.v_units
    ev_out.billed_vunits = snap.v_units if snap.v_units > 0 else latest.validator_count * BPS_DENOMINATOR

    updates = [e for e in events if e.kind == "ClusterBalanceUpdated"]
    anomalies: list[str] = []
    if not updates:
        if not complete_from_activation:
            ev_out.reasons.append("no EB update in a partial history")
        elif snap.v_units or snap.last_root_block or snap.last_update_block:
            ev_out.reasons.append("snapshot set but no ClusterBalanceUpdated in history")
        if ev_out.reasons:
            return ev_out
        ev_out.status = IMPLICIT if current_version == ETH else NOT_ETH
        return ev_out

    u = updates[-1]
    ev_out.update = u
    ev_out.ref_block = u.ref_block
    ev_out.eb_at_ref = u.eb
    if u.ref_block < history_start_block:
        ev_out.reasons.append(f"reference block {u.ref_block} precedes history start {history_start_block}")
    if snap.last_root_block != u.ref_block or snap.last_update_block != u.block:
        ev_out.reasons.append(
            f"snapshot (root {snap.last_root_block}, update {snap.last_update_block}) "
            f"!= last ClusterBalanceUpdated (root {u.ref_block}, block {u.block})"
        )

    start_v = eb_to_vunits(u.eb)
    after_u = [e for e in events if e.pos > u.pos]
    after_s = [e for e in events if e.block > u.ref_block and e.pos != u.pos]
    ev_out.replay_vunits = simulate(start_v, after_u, events, current_version, anomalies, False, guards_from)
    ev_out.reference_vunits = simulate(start_v, after_s, events, current_version, anomalies, True, guards_from)
    ev_out.reasons.extend(anomalies)
    if ev_out.replay_vunits != snap.v_units:
        ev_out.reasons.append(f"replayed vUnits {ev_out.replay_vunits} != stored {snap.v_units}")

    ev_out.discrepancy_vunits = ev_out.reference_vunits - snap.v_units
    _matched(ev_out, events)

    if ev_out.reasons:
        return ev_out  # UNKNOWN
    if current_version != ETH:
        ev_out.status = NOT_ETH
    elif snap.v_units == 0 and latest.validator_count > 0:
        ev_out.status = IMPLICIT_RESET
    elif ev_out.discrepancy_vunits > 0:
        ev_out.status = CONFIRMED if ev_out.matched_keys else LATENT
    elif ev_out.discrepancy_vunits < 0:
        ev_out.status = OVERBILLED
    else:
        ev_out.status = CLEAN
    return ev_out


def _matched(ev_out: Evaluation, events: list[Event]) -> None:
    """Keys added after S and before the update, removed after the update."""
    updates = [e for e in events if e.kind == "ClusterBalanceUpdated"]
    if not updates:
        return
    u = updates[-1]
    adds = [e for e in events if e.kind == "ValidatorAdded" and e.block > u.ref_block and e.pos < u.pos]
    added = {e.pubkey for e in adds}
    removes = [e for e in events if e.kind == "ValidatorRemoved" and e.pos > u.pos and e.pubkey in added]
    ev_out.adds_in_window = adds
    ev_out.removes_after_update = removes
    ev_out.matched_keys = sorted({e.pubkey for e in removes})
