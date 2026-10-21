"""Failed-transaction inbox (Monitor -> worker; plan §6 "Failed-transaction inbox").

The Monitor appends one JSON line per matched failed transaction to `data/inbox/failed-tx.jsonl`,
mounted read-only in the worker. The worker:
  - reads complete lines from a byte offset kept in `meta` (a trailing partial line waits),
  - raises InboxUnavailable when the configured file is missing or unreadable: the worker reports that
    as monitor health, never as an empty inbox (an existing empty file is a valid, empty inbox; the
    deployment creates it before the Monitor's first append),
  - restarts from offset 0 when the file shrinks or is replaced (log rotation),
  - de-duplicates on (tx_hash, block_hash),
  - delivers an entry only once the worker's checkpoint has reached its block and its `block_hash` is the
    canonical hash there (the Monitor writes at 2 confirmations, the checkpoint is 12 deep): an entry of an
    orphaned block is marked `canonical = 0` and never delivered (a re-included transaction arrives as a new
    (tx_hash, block_hash) line); a reorg below the checkpoint re-opens the check for entries above the ancestor,
  - resolves the revert selector with `eth_call` of the same transaction at `block - 1`
    (Blockscout v2 `revert_reason` as fallback), then names it from ERRORS.
Checks scheduled `OnInbox` receive the resolved entries in order (`ctx.inbox`).
"""

from __future__ import annotations

import json
import logging
import os
import stat
import urllib.request
from dataclasses import dataclass

from rpc import RpcError

log = logging.getLogger("eb-reconcile")

# Custom errors of ISSVNetworkCore at the pin (ssv-network f7ac487), selector -> canonical signature.
# Constants, not computed: tests/test_framework.py checks each against keccak256(signature).
ERRORS = {
    "0x8907fc65": "CallerNotOwnerWithData(address,address)",
    "0xb7f529fe": "CallerNotWhitelistedWithData(uint64)",
    "0x732f9413": "FeeTooLow()",
    "0x958065d9": "FeeExceedsIncreaseLimit()",
    "0x1d226c30": "NoFeeDeclared()",
    "0x97e4b518": "ApprovalNotWithinTimeframe()",
    "0x961e3e8c": "OperatorDoesNotExist()",
    "0xf4d678b8": "InsufficientBalance()",
    "0xe51315d2": "ValidatorDoesNotExist()",
    "0x60300a8d": "ClusterNotLiquidatable()",
    "0x637297a4": "InvalidPublicKeyLength()",
    "0x38186224": "InvalidOperatorIdsLength()",
    "0x3babafd2": "ClusterAlreadyEnabled()",
    "0x95a0cf33": "ClusterIsLiquidated()",
    "0x25d92f88": "ClusterDoesNotExist()",
    "0x12e04c87": "IncorrectClusterState()",
    "0xdd020e25": "UnsortedOperatorsList()",
    "0x6e6c9cac": "NewBlockPeriodIsBelowMinimum()",
    "0x639f5851": "ExceedValidatorLimitWithData(uint64)",
    "0x045c4b02": "TokenTransferFailed()",
    "0xc81272f8": "SameFeeChangeNotAllowed()",
    "0x410a2b6c": "FeeIncreaseNotAllowed()",
    "0xa5a1ff5d": "OperatorsListNotUnique()",
    "0x289c9494": "OperatorAlreadyExists()",
    "0x208bb85d": "TargetModuleDoesNotExistWithData(uint8)",
    "0x91aa3017": "MaxValueExceeded()",
    "0x24756546": "MaxPrecisionExceeded()",
    "0xcd4e6167": "FeeTooHigh()",
    "0x9ad467b8": "PublicKeysSharesLengthMismatch()",
    "0x89307938": "IncorrectValidatorStateWithData(bytes)",
    "0x75106a26": "ValidatorAlreadyRegistered(bytes,address)",
    "0xdf83e679": "EmptyPublicKeysList()",
    "0x71cadba7": "AddressIsWhitelistingContract(address)",
    "0x886e6a03": "InvalidWhitelistingContract(address)",
    "0xcbb362dc": "InvalidWhitelistAddressesLength()",
    "0x8579befe": "ZeroAddressNotAllowed()",
    "0xf222e863": "IncorrectOperatorVersion(uint8)",
    "0xf6749746": "IncorrectClusterVersion()",
    "0xb12d13eb": "ETHTransferFailed()",
    "0x9e593e76": "LegacyOperatorFeeDeclarationInvalid()",
    "0x305c3e93": "StaleBlockNumber()",
    "0x252f8a0e": "FutureBlockNumber()",
    "0x3033b0ff": "RootNotFound()",
    "0x53f7a6ee": "UpdateTooFrequent()",
    "0x666a2814": "StaleUpdate()",
    "0x4b3a0f08": "MustUseLatestRoot()",
    "0x09bde339": "InvalidProof()",
    "0xf5ca7cb9": "EBExceedsMaximum()",
    "0x9fecdce5": "EBBelowMinimum()",
    "0xcc1b9e26": "ZeroCSSVSupply()",
    "0x49c43735": "InsufficientCSSVSupply()",
    "0x1598959e": "NotCSSV()",
    "0xd92e233d": "ZeroAddress()",
    "0xd1735779": "InvalidQuorum()",
    "0x602d89dd": "InvalidOperatorFeeIncreaseLimit()",
    "0x44b0758c": "InvalidOperatorFeeRange()",
    "0x1f2a2005": "ZeroAmount()",
    "0xc1ab6dc1": "InvalidToken()",
    "0x969bf728": "NothingToClaim()",
    "0xd0d04f60": "NothingToWithdraw()",
    "0x02a19f57": "UnstakeAmountExceedsBalance()",
    "0x1cc3b37b": "StakeTooLow()",
    "0x1bc2178f": "NotOracle()",
    "0x7c9a1cf9": "AlreadyVoted()",
    "0xa97938cb": "OracleAlreadyAssigned()",
    "0xd2850a75": "SameOracleAddressNotAllowed()",
    "0x87265dc1": "InvalidOracleId()",
    "0xee0e82ff": "MaxRequestsAmountReached()",
    "0x8d09a73e": "ValidatorAlreadyExists()",
    "0x388e7999": "ValidatorAlreadyExistsWithData(bytes)",
    "0x2feda3c1": "IncorrectValidatorState()",
    "0x8ddf7de4": "ExceedValidatorLimit(uint64)",
    "0x5cd83192": "CallerNotOwner()",
    "0x8f9195fb": "TargetModuleDoesNotExist()",
    "0x8c6e5d71": "CallerNotWhitelisted()",
    # Solidity built-ins
    "0x08c379a0": "Error(string)",
    "0x4e487b71": "Panic(uint256)",
}


class InboxUnavailable(OSError):
    """The configured inbox file is missing or cannot be read."""


@dataclass(frozen=True)
class InboxEntry:
    id: int
    tx_hash: str
    block_hash: str
    block: int
    sender: str | None
    target: str | None
    selector: str | None
    function: str | None
    monitor: str | None
    observed_at: str | None
    revert_selector: str | None  # 4-byte selector, None when it could not be resolved
    revert_name: str | None  # error name ("IncorrectClusterState"), None when unknown or unresolved
    revert_source: str | None  # eth_call | blockscout | unresolved

    @classmethod
    def from_row(cls, r) -> InboxEntry:
        return cls(r["id"], r["tx_hash"], r["block_hash"], r["block"], r["sender"], r["target"], r["selector"],
                   r["function"], r["monitor"], r["observed_at"], r["revert_selector"], r["revert_name"],
                   r["revert_source"])


def decode_revert(data: str | None) -> tuple[str | None, str | None]:
    """Revert data -> (selector, error name); the name is None for a selector not in ERRORS."""
    if not data or not data.startswith("0x") or len(data) < 10:
        return None, None
    sel = data[:10].lower()
    sig = ERRORS.get(sel)
    if sig is None:
        return sel, None
    name = sig.split("(", 1)[0]
    if sig == "Error(string)":
        try:
            raw = bytes.fromhex(data[10:])
            n = int.from_bytes(raw[32:64], "big")
            name = "Error: " + raw[64 : 64 + n].decode("utf-8", "replace")
        except ValueError:
            pass
    return sel, name


def _blockscout_get(url: str, timeout: float = 15) -> dict:
    req = urllib.request.Request(url, headers={"accept": "application/json", "user-agent": "ssv-watch/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


class Inbox:
    REQUIRED = ("schema", "tx_hash", "block", "block_hash")

    def __init__(self, db, path: str, blockscout: str | None = None, http_get=_blockscout_get,
                 max_attempts: int = 5, batch: int = 50):
        self.db, self.path, self.blockscout, self.http_get = db, path, blockscout, http_get
        self.max_attempts, self.batch = max_attempts, batch

    # ---- reading

    def read(self) -> tuple[int, list[str]]:
        """Ingest new complete lines; returns (new entries, malformed lines). Must run inside a transaction.
        Raises InboxUnavailable (nothing written) when the file is missing, not a regular file or unreadable."""
        try:
            st = os.stat(self.path)
            if not stat.S_ISREG(st.st_mode):
                raise IsADirectoryError(f"not a regular file: {self.path}")
        except OSError as exc:
            raise InboxUnavailable(f"failed-transaction inbox {self.path} cannot be read: "
                                   f"{type(exc).__name__}: {exc.strerror or exc}") from exc
        pos = self.db.meta("inbox:offset", {"offset": 0, "inode": None})
        offset = pos["offset"]
        if st.st_size < offset or (pos["inode"] is not None and pos["inode"] != st.st_ino):
            log.info("inbox %s shrank or was replaced (size %d < offset %d); re-reading from 0",
                     self.path, st.st_size, offset)
            offset = 0
        try:
            with open(self.path, "rb") as fh:
                fh.seek(offset)
                chunk = fh.read()
        except OSError as exc:
            raise InboxUnavailable(f"failed-transaction inbox {self.path} cannot be read: "
                                   f"{type(exc).__name__}: {exc.strerror or exc}") from exc
        end = chunk.rfind(b"\n") + 1
        new, bad = 0, []
        for raw in chunk[:end].splitlines():
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                new += self._insert(line)
            except (ValueError, KeyError, TypeError) as exc:
                bad.append(f"{exc}: {line[:200]}")
        self.db.set_meta("inbox:offset", {"offset": offset + end, "inode": st.st_ino})
        return new, bad

    def _insert(self, line: str) -> int:
        d = json.loads(line)
        if not isinstance(d, dict) or any(k not in d for k in self.REQUIRED):
            raise ValueError("missing required field")
        if d["schema"] != 1:
            raise ValueError(f"unsupported schema {d['schema']!r}")
        tx_hash, block_hash = d["tx_hash"].lower(), d["block_hash"].lower()
        if len(tx_hash) != 66 or len(block_hash) != 66:
            raise ValueError("malformed hash")
        cur = self.db.conn.execute(
            "INSERT OR IGNORE INTO inbox (tx_hash, block_hash, block, sender, target, selector, function, status, "
            "monitor, observed_at, line) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (tx_hash, block_hash, int(d["block"]), _lower(d.get("from")), _lower(d.get("to")), _lower(d.get("selector")),
             d.get("function"), d.get("status"), d.get("monitor"), d.get("observed_at"), line),
        )
        return cur.rowcount

    # ---- revert resolution

    def resolve(self, rpc) -> int:
        """Resolve pending entries (bounded per call). Transport errors leave them pending. Inside a transaction."""
        rows = self.db.conn.execute(
            "SELECT * FROM inbox WHERE resolved=0 AND canonical IS NOT 0 ORDER BY id LIMIT ?", (self.batch,)).fetchall()
        done = 0
        for r in rows:
            try:
                data, source = self._revert_data(rpc, r)
            except RpcError as exc:
                attempts = r["attempts"] + 1
                final = attempts >= self.max_attempts
                self.db.conn.execute(
                    "UPDATE inbox SET attempts=?, resolved=?, revert_source=? WHERE id=?",
                    (attempts, int(final), "unresolved" if final else None, r["id"]))
                log.warning("inbox %s: revert resolution failed (attempt %d): %s", r["tx_hash"], attempts, exc)
                done += int(final)
                continue
            sel, name = decode_revert(data)
            self.db.conn.execute(
                "UPDATE inbox SET resolved=1, attempts=attempts+1, revert_selector=?, revert_name=?, revert_source=?, "
                "revert_data=? WHERE id=?", (sel, name, source if sel else "unresolved", data, r["id"]))
            done += 1
        return done

    def _revert_data(self, rpc, r) -> tuple[str | None, str | None]:
        tx = rpc.call("eth_getTransactionByHash", [r["tx_hash"]])
        if tx:
            call = {"from": tx["from"], "to": tx.get("to"), "data": tx.get("input", "0x"), "value": tx.get("value", "0x0"),
                    "gas": tx.get("gas")}
            try:
                rpc.call("eth_call", [{k: v for k, v in call.items() if v is not None}, hex(r["block"] - 1)])
            except RpcError as exc:
                if exc.transport:
                    raise
                if exc.data:
                    return exc.data, "eth_call"
        return self._blockscout(r["tx_hash"]), "blockscout"

    def _blockscout(self, tx_hash: str) -> str | None:
        if not self.blockscout:
            return None
        try:
            out = self.http_get(f"{self.blockscout.rstrip('/')}/api/v2/transactions/{tx_hash}")
        except Exception as exc:  # noqa: BLE001 - the fallback is best effort
            log.warning("blockscout revert lookup for %s failed: %s", tx_hash, type(exc).__name__)
            return None
        reason = out.get("revert_reason") if isinstance(out, dict) else None
        if isinstance(reason, dict):
            reason = reason.get("raw")
        return reason.lower() if isinstance(reason, str) and reason.startswith("0x") else None

    # ---- confirmation

    def confirm(self, rpc, upto: int) -> list:
        """Compare the block hash of every unchecked entry at or below the checkpoint `upto` with the canonical
        chain (one batched lookup); returns the rows found orphaned. Inside a transaction. An RpcError
        propagates with nothing marked: the entries stay unchecked and are not delivered."""
        rows = self.db.conn.execute(
            "SELECT id, tx_hash, block, block_hash FROM inbox WHERE canonical IS NULL AND block<=? ORDER BY id",
            (upto,)).fetchall()
        if not rows:
            return []
        hashes = rpc.block_hashes([r["block"] for r in rows])
        missing = sorted({r["block"] for r in rows if hashes[r["block"]] is None})
        if missing:
            raise RpcError(f"blocks {missing[:5]} at or below checkpoint {upto} not found")
        orphaned = [r for r in rows if hashes[r["block"]] != r["block_hash"]]
        self.db.conn.executemany("UPDATE inbox SET canonical=? WHERE id=?",
                                 [(int(hashes[r["block"]] == r["block_hash"]), r["id"]) for r in rows])
        return orphaned

    # ---- consumption

    def pending(self, cursor: int) -> list[InboxEntry]:
        """Resolved, canonical entries after `cursor`, stopping at the first one not yet resolved or
        confirmed (order is preserved); orphaned entries are skipped."""
        out = []
        for r in self.db.conn.execute("SELECT * FROM inbox WHERE id > ? ORDER BY id", (cursor,)):
            if r["canonical"] == 0:
                continue
            if not r["resolved"] or r["canonical"] is None:
                break
            out.append(InboxEntry.from_row(r))
        return out


def _lower(v):
    return v.lower() if isinstance(v, str) else v
