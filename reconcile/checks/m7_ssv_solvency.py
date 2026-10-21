"""M7 — SSV-side solvency invariant (spec M7, #91046; script `evidences/#91046/ssv-solvency-snapshot.mjs`).

SPEC Global Invariant #2 on the SSV side reconciles to a 1.0 SSV surplus (the activation seed). Daily, at
one block:

  surplus = proxy SSV balance − ( cSSV.totalSupply()
                                + Σ outstanding unstake requests   (UnstakeRequested − UnstakedWithdrawn logs)
                                + Σ live VERSION_SSV cluster balances (getBalanceSSV, active legacy clusters)
                                + Σ operator SSV earnings           (getOperatorEarningsSSV, ids 1..lastOperatorId)
                                + getNetworkEarningsSSV() )

  surplus < `surplus_floor_wei` (1 SSV)  -> page   (key `surplus-floor`; resolved when it is back above)
  any change in the surplus              -> info   (key `surplus`, revision = the surplus in wei: the first
                                                   evaluation opens it with the baseline, a change re-sends it)

Inputs: the SSV token is read from proxy storage (`StorageData.token`), the operator count from
`StorageData.lastOperatorId`, the cSSV address and the first block of the staking module from config (spec §5
dated values, required). Unstake logs are the proxy events this check declares (`events`), stored by the
worker's scan from `start_block`; when `start_block` is after `unstake_history_from` the earlier range is read
once with eth_getLogs and kept as a prefix sum (blocks below `start_block` are never rewound by the worker).

UNKNOWN, never clean: a legacy cluster without a storage-validated tuple, a reverting or empty view, or an
operator count of 0 next to existing legacy clusters leaves a claim unpriced. The computed value is then an
upper bound of the surplus: below the floor it still pages (more claims can only lower it); otherwise a
health message says M7 cannot evaluate and the open incidents are re-reported unchanged.

Completeness guard: `m7_legacy` remembers every legacy cluster id the inventory listed; one that disappears
from the inventory while storage still holds it as `VERSION_SSV` is an unpriced claim (UNKNOWN), not a
smaller liability. Ids leave the table only when storage says the cluster was migrated or no longer exists.

Reorg rule (README §6.2): every run (reorg runs included) recomputes the whole ledger at its block from the
canonical chain and reports both keys (a finding, `resolve=True`, or an UNKNOWN re-report), so
`ctx.reorg_keys` is always covered. `m7_legacy` holds ids only, never values, and is not rewound: a legacy
cluster cannot be created after the v2 activation, so an id first listed on a deleted block was legacy on the
canonical chain too, and every run re-checks each id against storage at its own block. The unstake sums come from the worker's stored
logs, which the reorg rewinds; the prefix sum covers only blocks below `start_block`.
"""

from __future__ import annotations

from checks.base import Check, Ctx, Every, Finding
from checks.m6_legacy_coverage import (
    _ssv,
    call_each,
    legacy_balances,
    open_incidents,
    require_views,
    unknown_rereport,
    word,
)
from checks.registry import register
from eb_storage import MAIN_BASE, eth_cluster_slot, keccak, ssv_cluster_slot, u256

MAIN_TOKEN = MAIN_BASE + 7  # StorageData.token (IERC20), SSVStorage.sol at the pin
MAIN_LAST_OPERATOR_ID = MAIN_BASE + 8  # StorageData.lastOperatorId (Counters.Counter)

BALANCE_OF = keccak(b"balanceOf(address)")[:4]
TOTAL_SUPPLY = keccak(b"totalSupply()")[:4]
GET_NETWORK_EARNINGS_SSV = keccak(b"getNetworkEarningsSSV()")[:4]
GET_OPERATOR_EARNINGS_SSV = keccak(b"getOperatorEarningsSSV(uint64)")[:4]

UNSTAKE_EVENTS = {
    "UnstakeRequested": "UnstakeRequested(address,uint256,uint256)",  # ISSVStaking.sol at the pin
    "UnstakedWithdrawn": "UnstakedWithdrawn(address,uint256)",
}
TOPIC = {k: "0x" + keccak(v.encode()).hex() for k, v in UNSTAKE_EVENTS.items()}


# ledger entries in SSV wei (strings in findings: they exceed 2**53)
WEI = {"proxy_ssv", "cssv_supply", "outstanding_unstake", "unstake_requested", "unstake_withdrawn",
       "legacy_cluster_balances", "operator_earnings", "network_earnings", "claims", "surplus"}


def _amount(data_hex: str) -> int:
    data = bytes.fromhex(data_hex[2:])
    return word(data, "unstake event data")


@register
class M7SsvSolvency(Check):
    id = "m7"
    defaults = {
        "interval_s": 86_400,  # spec M7: daily
        "surplus_floor_wei": 10**18,  # spec M7: surplus < 1 SSV -> page (the activation seed)
        "cssv": None,  # required: cSSV token (spec §5); config.example.json carries the dated value
        "unstake_history_from": None,  # required: first block of the staking module (no unstake log before it)
    }
    events = UNSTAKE_EVENTS
    schema = "CREATE TABLE IF NOT EXISTS m7_legacy (cluster_id TEXT PRIMARY KEY, first_block INTEGER NOT NULL)"

    def __init__(self, config: dict, settings: dict):
        super().__init__(config, settings)
        self.views = require_views(settings, self.id)
        for k in ("cssv", "unstake_history_from"):
            if config.get(k) in (None, ""):
                raise SystemExit(f"checks.m7.{k} is required (see config.example.json for the mainnet value)")
        self.cssv = config["cssv"].lower()
        self.proxy = settings["proxy"].lower()
        self.schedule = Every(config["interval_s"])

    def run(self, ctx: Ctx, block: int) -> list[Finding]:
        ledger, unknown = self.ledger(ctx, block)
        opened = open_incidents(ctx)
        floor = int(self.config["surplus_floor_wei"])
        surplus = ledger["surplus"]
        evidence = {k: str(v) if k in WEI else v for k, v in ledger.items()}
        findings: list[Finding] = []
        if surplus < floor:
            bound = " at most" if unknown else ""
            findings.append(Finding(
                check=self.id, key="surplus-floor", severity="page",
                title=f"SSV solvency surplus{bound} {_ssv(surplus)} SSV, below {_ssv(floor)} SSV",
                body=(f"At block {block} the proxy holds {_ssv(ledger['proxy_ssv'])} SSV against "
                      f"{_ssv(ledger['claims'])} SSV of claims: surplus{bound} {_ssv(surplus)} SSV (floor "
                      f"{_ssv(floor)} SSV, the activation seed). An SSV-side leak lowers it; staker principal shares "
                      f"the same balance.\n{self._lines(ledger)}"
                      + (f"\nUnpriced claims (they can only lower it further): {len(unknown)}." if unknown else "")),
                subject={"proxy": self.proxy}, evidence={**evidence, "unknown": sorted(unknown)[:20]}))
        elif unknown:
            if "surplus-floor" in opened:
                findings.append(unknown_rereport(self.id, opened["surplus-floor"], f"{len(unknown)} unpriced claim(s)"))
        elif "surplus-floor" in opened:
            findings.append(Finding(
                check=self.id, key="surplus-floor", severity="page",
                title=f"Resolved: SSV solvency surplus back to {_ssv(surplus)} SSV",
                body=f"At block {block} the SSV surplus is {_ssv(surplus)} SSV (floor {_ssv(floor)} SSV).",
                evidence=evidence, resolve=True))

        if unknown:
            if "surplus" in opened:
                findings.append(unknown_rereport(self.id, opened["surplus"], f"{len(unknown)} unpriced claim(s)"))
            first = sorted(unknown)[0]
            ctx.health("m7-unknown", f"M7 cannot price {len(unknown)} SSV claim(s) at block {block} (UNKNOWN, not "
                       f"clean); the surplus is at most {_ssv(surplus)} SSV. First: {first}: {unknown[first]}",
                       dedup=f"m7-unknown:{','.join(sorted(unknown))}", realert=True)
            return findings

        prev = opened.get("surplus", {}).get("revision")
        if prev is None:
            title, change = f"SSV solvency surplus baseline: {_ssv(surplus)} SSV", "first evaluation"
        else:
            delta = surplus - int(prev)
            title = f"SSV solvency surplus {'unchanged' if not delta else 'changed'}: {_ssv(surplus)} SSV"
            change = f"previous {_ssv(int(prev))} SSV, change {'-' if delta < 0 else '+'}{_ssv(abs(delta))} SSV"
        findings.append(Finding(
            check=self.id, key="surplus", severity="info", title=title,
            body=f"Block {block}: surplus {_ssv(surplus)} SSV ({change}).\n{self._lines(ledger)}",
            subject={"proxy": self.proxy}, evidence=evidence, revision=str(surplus)))
        return findings

    # ---- the ledger

    def ledger(self, ctx: Ctx, block: int) -> tuple[dict, dict[str, str]]:
        token_word, last_op = ctx.rpc.storage(self.proxy, [MAIN_TOKEN, MAIN_LAST_OPERATOR_ID])
        token = "0x" + token_word.to_bytes(32, "big")[12:].hex()
        if not token_word:
            raise ValueError(f"proxy storage reports no SSV token at block {block}")
        unknown: dict[str, str] = {}

        head = [(token, BALANCE_OF + bytes.fromhex(self.proxy[2:]).rjust(32, b"\0")),
                (self.cssv, TOTAL_SUPPLY), (self.views, GET_NETWORK_EARNINGS_SSV)]
        ops = [(self.views, GET_OPERATOR_EARNINGS_SSV + u256(i)) for i in range(1, last_op + 1)]
        outs, errors = call_each(ctx, head + ops)
        names = ["SSV.balanceOf(proxy)", "cSSV.totalSupply()", "getNetworkEarningsSSV()"]
        got = []
        for i, name in enumerate(names):
            if i in errors:
                raise ValueError(f"{name} failed at block {block}: {errors[i]}")  # no ledger without it
            got.append(word(outs[i], name))
        proxy_ssv, cssv_supply, network = got
        operators = 0
        for j in range(len(ops)):
            i = j + len(head)
            try:
                if i in errors:
                    raise ValueError(errors[i])
                operators += word(outs[i], "getOperatorEarningsSSV")
            except ValueError as exc:
                unknown[f"operator:{j + 1}"] = str(exc)

        inv = ctx.inventory(block)
        legacy = [c for c in inv.clusters.values() if c.version == "SSV"]
        if legacy and last_op == 0:
            unknown["operators"] = f"lastOperatorId is 0 while {len(legacy)} legacy clusters exist"
        for c in legacy:
            if not c.valid:
                unknown[f"cluster:{c.cluster_id}"] = "no candidate tuple validates against storage"
        active = [c for c in legacy if c.valid and c.cluster.active]
        values, failed = legacy_balances(ctx, self.views, active, with_risk=False)
        for cid, why in failed.items():
            unknown[f"cluster:{cid}"] = why
        clusters = sum(v["balance"] for v in values.values())
        # a legacy cluster seen before that the inventory no longer lists (an API page or row missing) is an
        # unpriced claim unless storage says it left VERSION_SSV
        listed = {c.cluster_id for c in legacy}
        for c in legacy:
            ctx.db.conn.execute("INSERT OR IGNORE INTO m7_legacy (cluster_id, first_block) VALUES (?, ?)",
                                (c.cluster_id, block))
        gone = [r[0] for r in ctx.db.conn.execute("SELECT cluster_id FROM m7_legacy ORDER BY cluster_id")
                if r[0] not in listed]
        if gone:
            slots = []
            for cid in gone:
                raw = bytes.fromhex(cid[2:])
                slots += [eth_cluster_slot(raw), ssv_cluster_slot(raw)]
            words = ctx.rpc.storage(self.proxy, slots)
            for i, cid in enumerate(gone):
                if words[2 * i + 1] and not words[2 * i]:
                    unknown[f"cluster:{cid}"] = "legacy in storage but missing from the inventory"
                else:  # migrated to ETH or gone: no longer an SSV claim
                    ctx.db.conn.execute("DELETE FROM m7_legacy WHERE cluster_id=?", (cid,))

        requested, withdrawn, n_req, n_wd = self.unstakes(ctx, block)
        outstanding = requested - withdrawn
        claims = cssv_supply + outstanding + clusters + operators + network
        return {
            "block": block, "proxy_ssv": proxy_ssv, "cssv_supply": cssv_supply, "outstanding_unstake": outstanding,
            "unstake_requested": requested, "unstake_withdrawn": withdrawn, "unstake_requests": n_req,
            "unstake_withdrawals": n_wd, "legacy_cluster_balances": clusters, "legacy_clusters_priced": len(values),
            "legacy_clusters_inactive": sum(1 for c in legacy if c.valid and not c.cluster.active),
            "operator_earnings": operators, "operators": last_op, "network_earnings": network, "claims": claims,
            "surplus": proxy_ssv - claims, "ssv_token": token,
        }, unknown

    def unstakes(self, ctx: Ctx, block: int) -> tuple[int, int, int, int]:
        """Σ UnstakeRequested, Σ UnstakedWithdrawn (amounts and counts) up to `block` on the canonical chain."""
        req = wd = n_req = n_wd = 0
        start = int(ctx.settings["start_block"])
        frm = int(self.config["unstake_history_from"])
        if frm < start:  # the worker never stored this range: read it once, keep the sums
            p = ctx.state("unstake_prefix")
            if not p or p["from"] != frm or p["to"] != start - 1:
                p = {"from": frm, "to": start - 1, "requested": "0", "withdrawn": "0", "n_req": 0, "n_wd": 0}
                for lg in ctx.rpc.logs(self.proxy, list(TOPIC.values()), frm, start - 1):
                    if lg["topics"][0] == TOPIC["UnstakeRequested"]:
                        p["requested"] = str(int(p["requested"]) + _amount(lg["data"]))
                        p["n_req"] += 1
                    elif lg["topics"][0] == TOPIC["UnstakedWithdrawn"]:
                        p["withdrawn"] = str(int(p["withdrawn"]) + _amount(lg["data"]))
                        p["n_wd"] += 1
                ctx.set_state("unstake_prefix", p)
            req, wd, n_req, n_wd = int(p["requested"]), int(p["withdrawn"]), p["n_req"], p["n_wd"]
        for kind, data in ctx.db.conn.execute(
                "SELECT kind, json_extract(extra, '$.data') FROM logs WHERE kind IN (?, ?) AND block <= ?",
                ("UnstakeRequested", "UnstakedWithdrawn", block)):
            if kind == "UnstakeRequested":
                req += _amount(data)
                n_req += 1
            else:
                wd += _amount(data)
                n_wd += 1
        return req, wd, n_req, n_wd

    @staticmethod
    def _lines(g: dict) -> str:
        return (f"proxy SSV {_ssv(g['proxy_ssv'])}; cSSV supply {_ssv(g['cssv_supply'])}; outstanding unstakes "
                f"{_ssv(g['outstanding_unstake'])} ({g['unstake_requests']} requested / {g['unstake_withdrawals']} "
                f"withdrawn); legacy cluster balances {_ssv(g['legacy_cluster_balances'])} "
                f"({g['legacy_clusters_priced']} clusters); operator SSV earnings {_ssv(g['operator_earnings'])} "
                f"({g['operators']} operators); network SSV earnings {_ssv(g['network_earnings'])}.")
