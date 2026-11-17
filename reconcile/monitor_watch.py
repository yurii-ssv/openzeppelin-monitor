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
  The range is never too short: when the Monitor resumed in several cycles between two reads (a catch-up cycle can
  outlast the cron, and the queued tick then runs at once: ms-cp4r, 51 blocks in 67 s then 6 blocks 1.5 s later) the
  true range ends earlier, so it is exact only for a resume in one cycle; the message says so, and calls it an
  upper bound outright when the worker's previous read is older than `1.5 x cycle_s`.

  A recreated file (new inode: the volume was replaced) is a reset like a missing one, also when the worker did not
  see it missing;
- `monitor-unevaluated` (D21) naming every block the Monitor fetched but did not evaluate and will not evaluate in
  time. Since D21 a block whose filter fails (its `eth_getLogs` or a receipt still fails after retries and URL
  rotation) is recorded in the Monitor's missed-blocks file (`<monitor data>/<network>_missed_blocks.json`, written
  by `FileBlockStorage`) and retried by its `recovery_config` job; the worker reads that file at every observation
  and announces, once per block range, the blocks the recovery gave up on (`Failed`), the blocks still waiting
  (`Pending`/`Recovering`) after `unevaluated_after_s`, and the blocks that left the file without a provable
  successful retry: the recovery removes an entry only after it evaluated the block (`remove_recovered_blocks`) or
  when the block is older than `max_block_age` (`prune_old_missed_blocks`), so an entry that disappears while its
  block is younger than half of `max_block_age` was evaluated; an older one was pruned, and every entry of a file
  that was removed or replaced (new inode) is lost. An unreadable or unparseable file is a `monitor-unevaluated`
  health message (re-sent per health window while it lasts), never "no missed block".

State is durable (`meta` keys `monitor:watch`, `monitor:missed`), so a restart neither forgets an open stall nor
re-announces it. Behaviour of the Monitor itself: plans/monitoring-service/workers/cp4-repair1/plan.md.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger("eb-reconcile")

# Defaults for the Monitor template's 60 s cron (deploy/config/networks/evm_ethereum_mainnet.json): a healthy Monitor
# is at most ~2 confirmations + 5 blocks of one cycle + a few blocks of read skew (~10) behind the head; 30 blocks
# is ~6 min, i.e. about five failed cycles. `stall_after_s` 240 (four failed cycles): a Monitor that stops is
# announced (value unchanged for 240 s, seen within one worker step) at a lag of about 2 + 270/12 = ~25 blocks plus
# the delivery delay, well before it reaches `max_past_blocks` (50) and starts losing blocks. `max_past_blocks` must
# not exceed the Monitor's own value (a smaller one over-reports the skipped range, a larger one would hide blocks).
# `missed_blocks_path` defaults to the file next to `last_block_path`. `unevaluated_after_s` 1200 (20 min): the
# template's recovery job runs every minute and retries a block up to 10 times, so a block it evaluates within ~10
# recovery runs (+ a backlog of 10 blocks per run) is silent; 20 min is far below `max_block_age` (7200 blocks, ~24 h,
# after which the Monitor drops the entry). `max_block_age` must equal the template's `recovery_config.max_block_age`.
DEFAULTS = {
    "enabled": True,
    "last_block_path": None,
    "missed_blocks_path": None,
    "max_lag_blocks": 30,
    "stall_after_s": 240,
    "max_past_blocks": 50,
    "cycle_s": 60,
    "block_time_s": 12,
    "unevaluated_after_s": 1200,
    "max_block_age": 7200,
}

META = "monitor:watch"
META_MISSED = "monitor:missed"
LAST_BLOCK_SUFFIX, MISSED_BLOCKS_SUFFIX = "_last_block.txt", "_missed_blocks.json"
MISSED_STATUSES = ("Pending", "Recovering", "Recovered", "Failed")  # storage.rs MissedBlockStatus
# The Monitor rewrites its missed-blocks file in place (truncate + write, not atomic): a read that does not parse is
# read again before it counts as unreadable.
READ_ATTEMPTS, READ_RETRY_S = 3, 0.05
RULES = ("M8 events, M10, M11 info, M13, M15, transfers, DAO/Safe, oracle transactions, the failed-tx inbox for "
         "M1/M2")
BY_HAND = "Check them by hand (proxy logs, failed transactions to the proxy, transfers to the watched addresses)."


def settings(section: dict | None) -> dict | None:
    """The watch settings, or None when it is off (no `monitor` section or no `last_block_path`)."""
    cfg = {**DEFAULTS, **{k: v for k, v in (section or {}).items() if not k.startswith("_")}}  # `_comment` keys
    unknown = set(cfg) - set(DEFAULTS)
    if unknown:
        raise SystemExit(f"monitor: unknown key(s) {sorted(unknown)}; accepted: {sorted(DEFAULTS)}")
    if not cfg["enabled"] or not cfg["last_block_path"]:
        return None
    for k in ("max_lag_blocks", "stall_after_s", "max_past_blocks", "cycle_s", "block_time_s", "unevaluated_after_s",
              "max_block_age"):
        if not isinstance(cfg[k], (int, float)) or isinstance(cfg[k], bool) or cfg[k] <= 0:
            raise SystemExit(f"monitor.{k} must be a positive number, got {cfg[k]!r}")
    if not cfg["missed_blocks_path"]:  # D21: the watch never runs without the missed blocks
        if not str(cfg["last_block_path"]).endswith(LAST_BLOCK_SUFFIX):
            raise SystemExit(f"monitor.missed_blocks_path is required when last_block_path does not end with "
                             f"{LAST_BLOCK_SUFFIX}")
        cfg["missed_blocks_path"] = str(cfg["last_block_path"])[:-len(LAST_BLOCK_SUFFIX)] + MISSED_BLOCKS_SUFFIX
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


def file_identity(path: str) -> list[int] | None:
    """(device, inode) of the file, None when it cannot be stat-ed. The Monitor rewrites its files in place, so a new
    inode means the file was removed and created again (ctime changes at every write and proves nothing)."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return [st.st_dev, st.st_ino]


def _read_text(path: str) -> str:
    return Path(path).read_text()


def _parse_missed(text: str, path: str) -> tuple[dict[int, dict] | None, str | None]:
    bad = f"{path} does not hold the Monitor's missed blocks"
    try:
        data = json.loads(text)
    except ValueError:
        return None, f"{bad} (not JSON: {text[:40]!r})"
    if not isinstance(data, list):
        return None, f"{bad} (a JSON {type(data).__name__}, not a list)"
    out = {}
    for e in data:
        b = e.get("block_number") if isinstance(e, dict) else None
        if (not isinstance(b, int) or isinstance(b, bool) or b < 0 or e.get("status") not in MISSED_STATUSES
                or not isinstance(e.get("first_missed_at"), (int, float)) or isinstance(e.get("first_missed_at"), bool)
                or not isinstance(e.get("retry_count", 0), int)):
            return None, f"{bad} (entry {str(e)[:80]!r})"
        out[b] = {"status": e["status"], "first_missed_at": e["first_missed_at"], "retry_count": e.get("retry_count", 0)}
    return out, None


def read_missed_blocks(path: str) -> tuple[dict[int, dict] | None, list[int] | None, str | None]:
    """(entries by block, file identity, None); ({}, None, None) for a missing file (the Monitor has not recorded a
    missed block yet: it reads a missing file as none); (None, None, problem) when the file cannot be read or parsed
    after READ_ATTEMPTS reads. An empty file is a problem: the Monitor never leaves one (no entry is `[]`)."""
    problem = None
    for attempt in range(READ_ATTEMPTS):
        if attempt:
            time.sleep(READ_RETRY_S)
        identity = file_identity(path)
        try:
            text = _read_text(path)
        except FileNotFoundError:
            return {}, None, None
        except (OSError, UnicodeDecodeError) as exc:
            problem = f"{path} is unreadable ({type(exc).__name__}: {getattr(exc, 'strerror', None) or exc})"
            continue
        entries, problem = _parse_missed(text, path)
        if problem is None:
            return entries, identity, None
    return None, None, problem


def _ranges(blocks) -> list[tuple[int, int]]:
    out = []
    for b in sorted(blocks):
        if out and b == out[-1][1] + 1:
            out[-1] = (out[-1][0], b)
        else:
            out.append((b, b))
    return out


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
        identity = file_identity(cfg["last_block_path"]) if block is not None else None
        prev, prev_at = st.get("block"), st.get("observed_at")
        if block is not None:
            if identity is not None and st.get("identity") not in (None, identity):
                st["reset"] = True  # the file was created again (the volume was replaced) while the worker did not look
            if identity is not None:
                st["identity"] = identity
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
        if frozen and st["bad_since"] > st["changed_at"]:
            st["bad_since"] = st["changed_at"]  # not keeping up since its last change
        if not reasons:
            st["bad_since"] = None
        st["reason"] = "; ".join(reasons) or None

        stall = st.get("stall")
        due = reasons and now - st["bad_since"] >= cfg["stall_after_s"]
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
        self._missed(head, block, now)
        return st

    def _missed(self, head: int | None, block: int | None, now: float) -> None:
        """The Monitor's missed-blocks file (D21): announce, once per block range, every block it gave up on, every block
        waiting longer than `unevaluated_after_s`, and every block that left the file without a provable successful
        retry. Unreadable is a health message, never "no missed block"."""
        cfg = self.cfg
        mb = dict(self.db.meta(META_MISSED) or {})
        known = {int(b): e for b, e in (mb.get("known") or {}).items()}
        announced = {int(b): set(t) for b, t in (mb.get("announced") or {}).items()}
        # the newest head seen (the worker's or the Monitor's own last block): an entry younger than half of
        # max_block_age below it cannot have been pruned
        mb["ref"] = ref = max([x for x in (mb.get("ref"), head, block) if x is not None], default=None)
        mb["checked_at"] = now
        entries, identity, problem = read_missed_blocks(cfg["missed_blocks_path"])
        if problem:
            bad = mb.get("unreadable")
            if not bad:
                mb["unreadables"] = mb.get("unreadables", 0) + 1
                bad = mb["unreadable"] = {"n": mb["unreadables"], "since": now}
                log.error("Monitor missed-blocks file unreadable (%d): %s", bad["n"], problem)
            mb["error"] = problem
            self.alerts.health(
                "monitor-unevaluated",
                f"The Monitor's missed-blocks file cannot be read: {problem}. Since {int(now - bad['since'])} s the "
                f"worker cannot see which blocks the Monitor fetched but did not evaluate (a block whose filter fails "
                f"is recorded there and retried by its recovery job): treat every block since then as possibly not "
                f"evaluated by its rules ({RULES}) until the file is readable again.",
                dedup=f"monitor-unevaluated:unreadable:{bad['n']}", realert=True)
            self.db.set_meta(META_MISSED, mb)
            return
        if mb.get("unreadable"):
            log.info("Monitor missed-blocks file readable again (%d)", mb["unreadable"]["n"])
        mb["unreadable"] = mb["error"] = None
        replaced = identity is None or mb.get("identity") not in (None, identity)  # removed, or created again
        mb["identity"] = identity

        new = {"failed": [], "pending": [], "replaced": [], "pruned": []}
        for b, e in known.items():
            if b in entries or e["status"] == "Recovered" or "final" in announced.get(b, ()):
                continue  # still there, evaluated (marked Recovered before its removal), or already announced
            if replaced:
                new["replaced"].append(b)
            elif e["status"] != "Failed" and ref is not None and b >= ref - cfg["max_block_age"] // 2:
                continue  # only the recovery's successful retry removes an entry this young
            else:
                new["pruned"].append(b)
        oldest = {}
        for b, e in entries.items():
            first_seen = known[b]["first_seen"] if b in known else now
            since = min(first_seen, e["first_missed_at"])
            known_e = {**e, "first_seen": first_seen}
            tags = announced.get(b, set())
            if e["status"] == "Failed" and "final" not in tags:
                new["failed"].append(b)
            elif e["status"] in ("Pending", "Recovering") and not tags and now - since >= cfg["unevaluated_after_s"]:
                new["pending"].append(b)
                oldest[b] = since
            known[b] = known_e
        known = {b: known[b] for b in entries}

        for reason, blocks in new.items():
            for lo, hi in _ranges(blocks):
                n = hi - lo + 1
                if reason == "failed":
                    text = (f"The Monitor gave up on blocks {lo}-{hi} ({n}): it fetched them but their filter failed "
                            f"(an eth_getLogs or receipt request failed after retries and URL rotation), and every "
                            f"retry of its recovery job failed too (recovery_config.max_retries). They were NOT "
                            f"evaluated by its rules ({RULES}) and will not be. {BY_HAND}")
                elif reason == "pending":
                    age = int(now - max(oldest[b] for b in range(lo, hi + 1)))
                    text = (f"Blocks {lo}-{hi} ({n}) have not been evaluated by the Monitor's rules ({RULES}) for "
                            f"{age} s or more: their filter failed and its recovery job has not evaluated them yet. "
                            f"It keeps retrying them (recovery_config.max_retries); a block it gives up on is "
                            f"announced again. {BY_HAND}")
                elif reason == "replaced":
                    text = (f"Blocks {lo}-{hi} ({n}) were waiting for the Monitor's recovery job (their filter had "
                            f"failed) when its missed-blocks file was removed or created again: they may not have "
                            f"been evaluated by its rules ({RULES}) and will not be retried. {BY_HAND}")
                else:
                    text = (f"Blocks {lo}-{hi} ({n}) left the Monitor's missed-blocks file without a provable "
                            f"successful retry (older than max_block_age {cfg['max_block_age']} blocks: pruned by "
                            f"its recovery job): they may not have been evaluated by its rules ({RULES}). {BY_HAND}")
                log.error("Monitor blocks %d-%d not evaluated (%s)", lo, hi, reason)
                self.alerts.health("monitor-unevaluated", text, dedup=f"monitor-unevaluated:{reason}:{lo}:{hi}")
                for b in range(lo, hi + 1):
                    announced.setdefault(b, set()).add("pending" if reason == "pending" else "final")
                if reason != "pending":  # blocks that will never be evaluated
                    mb["unevaluated_total"] = mb.get("unevaluated_total", 0) + n
                mb["last_announced"] = [lo, hi, reason]
        if ref is not None:  # forget announcements long after their block left the file
            announced = {b: t for b, t in announced.items() if b in known or b >= ref - 2 * cfg["max_block_age"]}
        mb["known"] = {str(b): e for b, e in known.items()}
        mb["announced"] = {str(b): sorted(t) for b, t in announced.items()}
        mb["pending"] = sum(1 for e in known.values() if e["status"] in ("Pending", "Recovering"))
        mb["failed"] = sum(1 for e in known.values() if e["status"] == "Failed")
        self.db.set_meta(META_MISSED, mb)

    def _skipped(self, st: dict, prev: int, new: int, prev_at: float | None, now: float) -> None:
        m = int(self.cfg["max_past_blocks"])
        if st.get("reset"):
            lo, hi, why = prev + 1, new - 1, "its last-block file was missing (a Monitor without it starts at the head)"
        else:
            lo, hi, why = prev + 1, new - m - 1, f"it was more than max_past_blocks ({m}) behind when it resumed"
        if hi < lo:
            return
        seen = prev_at is not None and now - prev_at <= 1.5 * self.cfg["cycle_s"]
        st["skipped_total"] = st.get("skipped_total", 0) + (hi - lo + 1)
        st["last_skipped"] = [lo, hi]
        log.error("Monitor skipped blocks %d-%d (%s)", lo, hi, why)
        bound = (" If it resumed in several quick cycles between two reads of the worker (a catch-up cycle can outlast "
                 "the cron), the true range ends earlier: this range is never too short." if seen else
                 f" The worker last read the Monitor's value {_ago(now, prev_at)} (more than one Monitor cycle), so this "
                 f"is an upper bound: the Monitor may have passed through intermediate values the worker did not see, "
                 f"and evaluated some or all of these blocks.")
        verdict = ("were NOT evaluated by its rules and will not be" if seen else
                   "may not have been evaluated by its rules (those it skipped will not be)")
        self.alerts.health(
            "monitor-skipped",
            f"The Monitor's last processed block jumped from {prev} to {new}: blocks {lo}-{hi} ({hi - lo + 1}) "
            f"{verdict} ({why}; such blocks are never recorded as missed, so recovery_config does not retry them). "
            f"{BY_HAND}{bound}",
            dedup=f"monitor-skipped:{lo}:{hi}")

    def problem(self) -> str | None:
        """The open stall, for `healthy: false` in the status file."""
        st = self.state()
        if st.get("stall"):
            return f"Monitor stall {st['stall']['n']}: {st.get('reason')}"
        mb = self.db.meta(META_MISSED) or {}
        if mb.get("unreadable"):
            return f"Monitor missed-blocks file unreadable: {mb.get('error')}"
        return None

    def status(self) -> dict:
        st = self.state()
        return {"enabled": True, "path": self.cfg["last_block_path"], "last_block": st.get("block"),
                "head": st.get("head"), "head_estimated": st.get("head_estimated", False), "lag_blocks": st.get("lag_blocks"), "changed_at": st.get("changed_at"),
                "observed_at": st.get("observed_at"), "stalled": bool(st.get("stall")), "stall": st.get("stall"),
                "reason": st.get("reason"), "stalls": st.get("stalls", 0),
                "skipped_blocks_total": st.get("skipped_total", 0), "last_skipped": st.get("last_skipped"),
                "max_lag_blocks": self.cfg["max_lag_blocks"], "stall_after_s": self.cfg["stall_after_s"],
                "missed": self.missed_status()}

    def missed_status(self) -> dict:
        mb = self.db.meta(META_MISSED) or {}
        return {"path": self.cfg["missed_blocks_path"], "ok": not mb.get("unreadable") and "checked_at" in mb,
                "error": mb.get("error"), "pending": mb.get("pending", 0), "failed": mb.get("failed", 0),
                "unevaluated_blocks_total": mb.get("unevaluated_total", 0), "last_announced": mb.get("last_announced"),
                "unevaluated_after_s": self.cfg["unevaluated_after_s"]}
