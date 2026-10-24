"""Monitor progress watch (D20, soak-1 Monitor stall, event-0084).

The OpenZeppelin Monitor runs the stateless rules (M8 events, M10, M11 info, M13, M15, transfers, DAO/Safe, the inbox that feeds
M1/M2). A Monitor that processes no block sends nothing and writes nothing to the inbox, which reads exactly like "no
failed transaction": the worker therefore reads the Monitor's last processed block
(`<monitor data>/<network>_last_block.txt`, written after every successful Monitor cycle) at every step and raises

- `monitor-stall` when the Monitor is not positively known to keep up for `stall_after_s`: it is more than
  `max_lag_blocks` behind the head the worker reads, its value has not changed for `stall_after_s`, the file is
  missing, unreadable or unparseable, or the head is unknown (the worker's own RPC fails). Unknown is a stall, never
  clean. Keyed per stall (as the `rpc` outage of CP3 F5), re-sent every health window while it lasts;
- `monitor-recovered` when a later observation proves it caught up (readable, head known, lag <= max_lag_blocks);
- `monitor-skipped` naming every block range the Monitor did not evaluate: its cycle reads
  `[max(last + 1, head_confirmed - max_past_blocks), head_confirmed]`, so when its last block jumps by more than
  `max_past_blocks + 1` the blocks `[prev + 1, new - max_past_blocks - 1]` are skipped (never recorded as missed,
  never recovered); a Monitor that lost its last-block file restarts at the head and skips `[prev + 1, new - 1]`.
  The range is exact when the worker saw the Monitor's previous value (consecutive observations within
  `1.5 x cycle_s`) and an upper bound otherwise (stated in the message).

State is durable (`meta` key `monitor:watch`), so a restart neither forgets an open stall nor re-announces it.
Behaviour of the Monitor itself (Rust, unchanged): plans/monitoring-service/workers/cp4-repair1/plan.md.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("eb-reconcile")

# Defaults for the Monitor template's 60 s cron (deploy/config/networks/evm_ethereum_mainnet.json): a healthy Monitor
# is at most ~2 confirmations + 5 blocks of one cycle + a few blocks of read skew (~10) behind the head; 30 blocks
# is ~6 min, i.e. about five failed cycles. `stall_after_s` 240 (four failed cycles): a Monitor that stops is
# announced (value unchanged for 240 s, seen within one worker step) at a lag of about 2 + 270/12 = ~25 blocks plus
# the delivery delay, well before it reaches `max_past_blocks` (50) and starts losing blocks. `max_past_blocks` must
# not exceed the Monitor's own value (a smaller one over-reports the skipped range, a larger one would hide blocks).
DEFAULTS = {
    "enabled": True,
    "last_block_path": None,
    "max_lag_blocks": 30,
    "stall_after_s": 240,
    "max_past_blocks": 50,
    "cycle_s": 60,
    "block_time_s": 12,
}

META = "monitor:watch"


def settings(section: dict | None) -> dict | None:
    """The watch settings, or None when it is off (no `monitor` section or no `last_block_path`)."""
    cfg = {**DEFAULTS, **{k: v for k, v in (section or {}).items() if not k.startswith("_")}}  # `_comment` keys
    unknown = set(cfg) - set(DEFAULTS)
    if unknown:
        raise SystemExit(f"monitor: unknown key(s) {sorted(unknown)}; accepted: {sorted(DEFAULTS)}")
    if not cfg["enabled"] or not cfg["last_block_path"]:
        return None
    for k in ("max_lag_blocks", "stall_after_s", "max_past_blocks", "cycle_s", "block_time_s"):
        if not isinstance(cfg[k], (int, float)) or isinstance(cfg[k], bool) or cfg[k] <= 0:
            raise SystemExit(f"monitor.{k} must be a positive number, got {cfg[k]!r}")
    return cfg


def read_last_block(path: str) -> tuple[int | None, str | None]:
    """(block, None) or (None, problem). The Monitor rewrites the file in place (truncate + write), so an empty read
    is possible for an instant: it is a problem of this observation, never a value."""
    try:
        text = Path(path).read_text()
    except FileNotFoundError:
        return None, f"{path} is missing"
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"{path} is unreadable ({type(exc).__name__}: {getattr(exc, 'strerror', None) or exc})"
    s = text.strip()
    if not s.isdigit():
        return None, f"{path} does not hold a block number ({text[:40]!r})"
    return int(s), None


def _ago(now: float, t: float | None) -> str:
    return "never" if t is None else f"{int(now - t)} s ago"


class MonitorWatch:
    def __init__(self, cfg: dict, db, alerts, clock):
        self.cfg, self.db, self.alerts, self.clock = cfg, db, alerts, clock

    def state(self) -> dict:
        return self.db.meta(META) or {}

    def observe(self, head: int | None, estimated: bool = False) -> dict:
        """One observation (every worker step, whether or not its RPC reads succeeded, and between two check runs with
        an `estimated` head). Queues the health messages in the caller's transaction and returns the new state."""
        cfg, now = self.cfg, self.clock()
        st = dict(self.state())
        block, problem = read_last_block(cfg["last_block_path"])
        prev, prev_at = st.get("block"), st.get("observed_at")
        if block is not None:
            if prev is not None and block > prev:
                self._skipped(st, prev, block, prev_at, now)
            if block != prev or st.get("changed_at") is None:
                st["changed_at"] = now
            st.update(block=block, observed_at=now, reset=False)
        elif "missing" in (problem or ""):
            st["reset"] = True  # the Monitor restarts at the head without its file: everything after prev is skipped
        lag = head - block if head is not None and block is not None else None
        st.update(head=head, head_estimated=estimated, lag_blocks=lag, error=problem, checked_at=now)

        reasons = []
        if problem:
            reasons.append(problem)
        elif head is None:
            reasons.append(f"the head is unknown (the worker's RPC reads fail); the Monitor's last block {block} "
                           f"changed {_ago(now, st.get('changed_at'))}")
        elif lag > cfg["max_lag_blocks"]:
            reasons.append(f"the Monitor's last block {block} is {lag} blocks behind the head {head} "
                           f"(threshold {cfg['max_lag_blocks']})")
        frozen = st.get("changed_at") is not None and now - st["changed_at"] >= cfg["stall_after_s"]
        if frozen and not problem:
            reasons.append(f"its last block {block} has not changed for {int(now - st['changed_at'])} s")
        if reasons and st.get("bad_since") is None:
            st["bad_since"] = now
        if not reasons:
            st["bad_since"] = None
        st["reason"] = "; ".join(reasons) or None

        stall = st.get("stall")
        due = reasons and (now - st["bad_since"] >= cfg["stall_after_s"] or frozen)
        if due and stall is None:
            st["stalls"] = st.get("stalls", 0) + 1
            stall = st["stall"] = {"n": st["stalls"], "since": st["bad_since"], "block": block if block is not None
                                   else prev}
            log.error("Monitor stall %d: %s", stall["n"], st["reason"])
        if stall is not None and reasons:
            self.alerts.health(
                "monitor-stall",
                f"The Monitor is not keeping up: {st['reason']}. Not positively known to keep up since "
                f"{int(now - stall['since'])} s (stall {stall['n']}). None of its rules (M8 events, M10, M11 info, "
                f"M13, M15, transfers, DAO/Safe, oracle transactions, the failed-tx inbox for M1/M2) evaluates "
                f"anything meanwhile; an empty inbox proves nothing. Blocks it "
                f"skips when it resumes (more than max_past_blocks {cfg['max_past_blocks']} behind) are announced as "
                f"monitor-skipped.",
                dedup=f"monitor-stall:{stall['n']}", realert=True)
        elif stall is not None and not reasons:
            self.alerts.health(
                "monitor-recovered",
                f"The Monitor processes blocks again: last block {block}, {lag} blocks behind the head {head}; "
                f"stall {stall['n']} lasted {int(now - stall['since'])} s.",
                dedup=f"monitor-recovered:{stall['n']}")
            log.info("Monitor stall %d over at block %d", stall["n"], block)
            st["stall"] = None
        self.db.set_meta(META, st)
        return st

    def _skipped(self, st: dict, prev: int, new: int, prev_at: float | None, now: float) -> None:
        m = int(self.cfg["max_past_blocks"])
        if st.get("reset"):
            lo, hi, why = prev + 1, new - 1, "its last-block file was missing (a Monitor without it starts at the head)"
        else:
            lo, hi, why = prev + 1, new - m - 1, f"it was more than max_past_blocks ({m}) behind when it resumed"
        if hi < lo:
            return
        exact = prev_at is not None and now - prev_at <= 1.5 * self.cfg["cycle_s"]
        st["skipped_total"] = st.get("skipped_total", 0) + (hi - lo + 1)
        st["last_skipped"] = [lo, hi]
        log.error("Monitor skipped blocks %d-%d (%s)", lo, hi, why)
        bound = "" if exact else (
            f" The worker last read the Monitor's value {_ago(now, prev_at)} (more than one Monitor cycle), so this "
            f"is an upper bound: the Monitor may have passed through intermediate values the worker did not see.")
        self.alerts.health(
            "monitor-skipped",
            f"The Monitor's last processed block jumped from {prev} to {new}: blocks {lo}-{hi} ({hi - lo + 1}) were "
            f"NOT evaluated by its rules and will not be ({why}; such blocks are never recorded as missed, so "
            f"recovery_config does not retry them). Check them by hand (proxy logs, failed transactions to the "
            f"proxy, transfers to the watched addresses).{bound}",
            dedup=f"monitor-skipped:{lo}:{hi}")

    def problem(self) -> str | None:
        """The open stall, for `healthy: false` in the status file."""
        st = self.state()
        if st.get("stall"):
            return f"Monitor stall {st['stall']['n']}: {st.get('reason')}"
        return None

    def status(self) -> dict:
        st = self.state()
        return {"enabled": True, "path": self.cfg["last_block_path"], "last_block": st.get("block"),
                "head": st.get("head"), "head_estimated": st.get("head_estimated", False), "lag_blocks": st.get("lag_blocks"), "changed_at": st.get("changed_at"),
                "observed_at": st.get("observed_at"), "stalled": bool(st.get("stall")), "stall": st.get("stall"),
                "reason": st.get("reason"), "stalls": st.get("stalls", 0),
                "skipped_blocks_total": st.get("skipped_total", 0), "last_skipped": st.get("last_skipped"),
                "max_lag_blocks": self.cfg["max_lag_blocks"], "stall_after_s": self.cfg["stall_after_s"]}
