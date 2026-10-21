"""M10 — Oracle slot rotation during an open round (#92676; spec §3 M10).

`hasVoted` is keyed by slot id, not address, and `replaceOracle` does not migrate in-flight votes: a mid-round
reassignment can let one address vote twice on the same `(blockNum, root)` or lock a new key out of the round.

| Trigger (spec) | Key | Severity | Rule here |
|---|---|---|---|
| `OracleReplaced` while a `WeightedRootProposed` round is open and not committed | `rotation:<block>:<log index>` | page | a round with a vote strictly before the `OracleReplaced` log (same block: lower log index) and neither its own nor a superseding `RootCommitted` before it; healed when every such round has closed (committed, or superseded by a higher committed `blockNum`) |
| `newOracle` already holds another slot | `dup:<address>` | page | derived from the logs, event by event: the slot map *at each* `OracleReplaced` (the live map `ctx.params.oracles`, re-read by the framework at every `OracleReplaced`, rewound through the later stored rotations) already has `newOracle` under another slot id; healed by the first later rotation after which the address holds one slot. A duplicate that appears and heals between two sweeps (even in one block) is still reported once. Also a live-map duplicate no stored rotation explains (history before `start_block`); healed when the live map no longer has it |

Every `OracleReplaced` → info is the Monitor's (CP1A template), not this check's. History before the check's
first run is reported only while still open (episode book, m9_oracle_common); its reorg rule applies to
`m10_episodes`. No `eth_call` of its own: everything comes from stored logs and the live parameters.
"""

from __future__ import annotations

from checks.base import Check, Ctx, Every, Finding, OnEvents
from checks.m9_oracle_common import (
    ORACLE_EVENTS,
    ROUND_KINDS,
    Episode,
    EpisodeBook,
    History,
    book_schema,
    load_history,
    short,
)
from checks.registry import register

TABLE = "m10_episodes"


@register
class M10OracleRotation(Check):
    id = "m10"
    defaults = {"interval_s": 300}
    events = ORACLE_EVENTS
    schema = book_schema(TABLE)

    def __init__(self, config: dict, settings: dict):
        super().__init__(config, settings)
        if not settings.get("views"):
            raise SystemExit("checks.m10: settings.views (SSVNetworkViews) is required: the slot map is a live "
                             "parameter")
        self.schedule = (Every(config["interval_s"]), OnEvents(ROUND_KINDS | {"OracleReplaced"}))
        self.book = EpisodeBook(self.id, TABLE)

    def prepare(self, ctx: Ctx) -> None:
        self.oracles = {i: a.lower() for i, a in ctx.params.oracles.items()}
        self.params_block = ctx.params.block

    def run(self, ctx: Ctx, block: int) -> list[Finding]:
        for _ in ctx.events():
            pass
        hist = load_history(ctx.db, block)
        return self.book.apply(ctx, self.rotations(hist) + self.duplicates(hist, block))

    def rotations(self, hist: History) -> list[Episode]:
        out = []
        for ev in hist.replaced:
            slot, old, new = ev.extra["oracleId"], ev.extra["old"], ev.extra["new"]
            open_rounds = []
            for ref, rnd in sorted(hist.rounds.items()):
                before = [v for v in rnd.votes if v.pos < ev.pos]
                if before and hist.closing_event(rnd, before=ev.pos) is None:
                    open_rounds.append((rnd, before))
            if not open_rounds:
                continue
            closes = [hist.closing_event(rnd) for rnd, _ in open_rounds]
            still = [rnd.ref for (rnd, _), c in zip(open_rounds, closes) if c is None]
            lines = []
            for rnd, before in open_rounds:
                by_slot = [v for v in before if v.slot == slot]
                lines.append(f"blockNum {rnd.ref}: {len(before)} vote(s) before the rotation"
                             + (f", slot {slot} had voted {by_slot[0].root[:10]} as {short(by_slot[0].oracle)}"
                                if by_slot else f", slot {slot} had not voted"))
            healed = ", ".join(f"blockNum {rnd.ref} closed at block {c.block}" for (rnd, _), c in zip(open_rounds, closes)
                               if c is not None)
            out.append(Episode(
                key=f"rotation:{ev.block}:{ev.log_index}", episode=f"rotation:{ev.block}:{ev.log_index}",
                active=bool(still), evidence_block=ev.block, severity="page",
                title=f"Oracle slot {slot} replaced during an open round",
                body=(f"OracleReplaced(slot {slot}, {old} -> {new}) at block {ev.block} (tx {ev.tx_hash}) while "
                      f"round(s) were open: {'; '.join(lines)}. hasVoted is keyed by slot: the new key may be locked "
                      f"out, or one address may vote twice. Confirm getCommittedRoot(blockNum) for "
                      f"{', '.join(str(r.ref) for r, _ in open_rounds)}; rotate only between rounds."),
                subject={"slot": slot, "old": old, "new": new},
                evidence={"tx_hash": ev.tx_hash, "block": ev.block, "log_index": ev.log_index,
                          "open_rounds": [rnd.ref for rnd, _ in open_rounds], "still_open": still},
                resolution=f"Every round open at the rotation has closed ({healed})."))
        return out

    def duplicates(self, hist: History, block: int) -> list[Episode]:
        zero = "0x" + "00" * 20
        maps = [dict(self.oracles)]  # maps[i]: the slot map after the i-th last rotation (maps[0] = live)
        for ev in reversed(hist.replaced):
            before = dict(maps[-1])
            before[ev.extra["oracleId"]] = ev.extra["old"]
            maps.append(before)
        maps.reverse()  # maps[0]: before the first stored rotation; maps[i + 1]: after rotation i

        def holders(m: dict, addr: str) -> list[int]:
            return sorted(s for s, a in m.items() if a == addr)

        out, explained = [], set()
        for i, ev in enumerate(hist.replaced):
            slot, new = ev.extra["oracleId"], ev.extra["new"]
            others = [s for s in holders(maps[i], new) if s != slot]
            if not others or new == zero:
                continue
            healed = next((j for j in range(i + 1, len(hist.replaced)) if len(holders(maps[j + 1], new)) < 2), None)
            if healed is None:
                explained.add(new)
            heal_ev = hist.replaced[healed] if healed is not None else None
            out.append(Episode(
                key=f"dup:{new}", episode=f"dup:{new}:{ev.block}:{ev.log_index}", active=heal_ev is None,
                evidence_block=ev.block, severity="page", title=f"Oracle address {short(new)} assigned a second slot",
                body=(f"OracleReplaced(slot {slot}, {ev.extra['old']} -> {new}) at block {ev.block} (tx {ev.tx_hash}) "
                      f"while {new} already held slot(s) {others}. One key holding two slots votes twice per round "
                      f"(replaceOracle should revert OracleAlreadyAssigned)."),
                subject={"address": new}, revision=f"{ev.block}:{ev.log_index}",
                evidence={"tx_hash": ev.tx_hash, "block": ev.block, "log_index": ev.log_index, "slot": slot,
                          "already_held": others},
                resolution=(f"OracleReplaced at block {heal_ev.block} (tx {heal_ev.tx_hash}): {short(new)} holds one "
                            f"slot again." if heal_ev else "")))
        live: dict[str, list[int]] = {}
        for slot, addr in sorted(self.oracles.items()):
            live.setdefault(addr, []).append(slot)
        for addr, slots in sorted(live.items()):
            if len(slots) < 2 or int(addr, 16) == 0 or addr in explained:
                continue
            out.append(Episode(
                key=f"dup:{addr}", episode=f"dup:{addr}", active=True, evidence_block=self.params_block or block,
                severity="page", title=f"Oracle address {short(addr)} holds slots {slots}",
                body=(f"getOracle returns {addr} for slots {slots} (live parameters read at block {self.params_block}). "
                      f"One key holding two slots votes twice per round (replaceOracle should revert "
                      f"OracleAlreadyAssigned)."),
                subject={"address": addr}, evidence={"slots": slots, "params_block": self.params_block},
                revision=",".join(map(str, slots)), resolution="The live slot map holds the address under one slot."))
        return out
