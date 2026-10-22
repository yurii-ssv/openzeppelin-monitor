"""M8 floor — cSSV supply against the seed stake (spec §3 M8 trigger 3, D3; plan §8 CP2c).

`cSSV.totalSupply()` below `floor_multiplier` (2) × the seed holder's cSSV balance → **warn** (key
`supply-floor`). D3: the seed holder is the DAO Safe; the floor is measured at worker start and re-measured every
`remeasure_s` (daily), both at the checkpoint block. A supply of 0 also warns even when the measured floor is 0
(the seed position is gone, so the floor degenerates; zero supply is BUG-6's window itself). The other M8
triggers (`FeesSynced(0)`, the seed holder's `UnstakeRequested`, `ZeroCSSVSupply` reverts) are Monitor rules
(CP1A) and M9.

Every run evaluates the key (a warn finding or `resolve=True`). State: the floor measurement
(`ctx.set_state("floor")`, with its block). A measurement taken above a reorg's ancestor came from a deleted
block and is re-taken at the new checkpoint in the run with reason `reorg` (README §6.2); restart re-measures
(D3 "at worker start"): the measurement carries this process's random token, so a floor persisted by an earlier
process — or one whose run rolled back after the measurement (repair 2, QA attempt 2 F3) — is never used. An empty
`eth_call` result (no code, wrong address) raises: never "supply fine".
"""

from __future__ import annotations

import re
import secrets

from checks.base import Check, Ctx, Every, Finding
from checks.registry import register

ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
TOTAL_SUPPLY = bytes.fromhex("18160ddd")
BALANCE_OF = bytes.fromhex("70a08231")


def _word(data: bytes, what: str) -> int:
    if len(data) != 32:
        raise ValueError(f"{what} returned {len(data)} bytes, expected one 32-byte word")
    return int.from_bytes(data, "big")


@register
class M8SupplyFloor(Check):
    id = "m8"
    defaults = {
        "interval_s": 300,
        # Required (spec §5, D3): config.example.json carries the dated addresses.
        "cssv": None,
        "seed_holder": None,
        "floor_multiplier": 2,  # D3
        "remeasure_s": 86_400,  # D3: re-measured daily
    }

    def __init__(self, config: dict, settings: dict):
        super().__init__(config, settings)
        for k in ("cssv", "seed_holder"):
            v = self.config[k]
            if not isinstance(v, str) or not ADDRESS.match(v):
                raise SystemExit(f"checks.m8.{k} is required (an 0x address; spec §5, D3), got {v!r}")
        m = self.config["floor_multiplier"]
        if not isinstance(m, int) or m <= 0:
            raise SystemExit(f"checks.m8.floor_multiplier must be a positive integer, got {m!r}")
        self.cssv, self.holder = self.config["cssv"].lower(), self.config["seed_holder"].lower()
        self.schedule = Every(self.config["interval_s"])
        self.process = secrets.token_hex(8)  # D3 "measured at worker start": a floor of this process only
        self.last: dict | None = None

    def floor(self, ctx: Ctx, block: int) -> dict:
        st = ctx.state("floor")
        stale = (st is None or st.get("process") != self.process or ctx.clock() - st["at"] >= self.config["remeasure_s"]
                 or (ctx.reorg_ancestor is not None and st["block"] > ctx.reorg_ancestor))
        if not stale:
            return st
        data = BALANCE_OF + bytes(12) + bytes.fromhex(self.holder[2:])
        seed = _word(ctx.rpc.call(self.cssv, data), f"cSSV.balanceOf({self.holder})")
        st = {"seed": str(seed), "floor": str(seed * self.config["floor_multiplier"]), "block": block,
              "at": ctx.clock(), "process": self.process}
        ctx.set_state("floor", st)  # rolled back with the run: the next run of this process measures again
        return st

    def run(self, ctx: Ctx, block: int) -> list[Finding]:
        st = self.floor(ctx, block)
        supply = _word(ctx.rpc.call(self.cssv, TOTAL_SUPPLY), "cSSV.totalSupply()")
        floor = int(st["floor"])
        self.last = {**st, "measured_block": st["block"], "block": block, "supply": supply}
        subject = {"cssv": self.cssv, "seed_holder": self.holder}
        evidence = {"block": block, "total_supply": str(supply), "seed_balance": st["seed"], "floor": st["floor"],
                    "floor_measured_block": st["block"], "multiplier": self.config["floor_multiplier"]}
        if supply < floor or supply == 0:
            why = "zero" if supply == 0 else f"below the floor {floor / 1e18:.4f}"
            return [Finding(
                check=self.id, key="supply-floor", severity="warn",
                title=f"cSSV supply {supply / 1e18:.4f} is {why}",
                body=f"cSSV.totalSupply() = {supply} at block {block}, {why} (= {self.config['floor_multiplier']} × "
                     f"the seed holder {self.holder}'s {int(st['seed']) / 1e18:.4f} cSSV measured at block "
                     f"{st['block']}). At zero supply, synced ETH network fees are lost to stakers and commitRoot "
                     f"reverts (ZeroCSSVSupply). Any stake() closes the window; ask governance to restore the seed "
                     f"position (spec M8).",
                subject=subject, evidence=evidence)]
        return [Finding(check=self.id, key="supply-floor", severity="info", title="cSSV supply above the floor",
                        body=f"cSSV.totalSupply() = {supply / 1e18:.4f} at block {block}, at or above the floor "
                             f"{floor / 1e18:.4f}.", subject=subject, evidence=evidence, resolve=True)]

    def digest(self, ctx: Ctx) -> list[str]:
        if not self.last:
            return []
        m = self.last
        return [f"M8 cSSV supply {m['supply'] / 1e18:.4f} at block {m['block']}; floor {int(m['floor']) / 1e18:.4f} "
                f"({self.config['floor_multiplier']} × seed {int(m['seed']) / 1e18:.4f}, measured at block "
                f"{m['measured_block']})"]
