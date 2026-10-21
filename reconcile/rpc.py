"""JSON-RPC client for the watch worker, and the block-pinned view checks read the chain through.

Read-only by construction: the client only issues the methods the worker needs, and no method
that signs or sends a transaction exists here.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from collections import Counter

log = logging.getLogger("eb-reconcile")


class RpcError(Exception):
    """transport=True: the endpoint was unreachable; False: it answered with a JSON-RPC error.

    `data` carries the error's revert data (hex string) when the node returned one.
    """

    def __init__(self, msg: str, transport: bool = False, data: str | None = None):
        super().__init__(msg)
        self.transport = transport
        self.data = data


def _revert_data(err) -> str | None:
    """Revert data from a JSON-RPC error object; providers nest it differently."""
    data = err.get("data") if isinstance(err, dict) else None
    if isinstance(data, dict):
        data = data.get("data") or data.get("result")
    if isinstance(data, str) and data.startswith("0x"):
        return data.lower()
    return None


class Rpc:
    def __init__(self, url: str, timeout: float = 30, retries: int = 4, batch_size: int = 50):
        self.url, self.timeout, self.retries, self.batch_size = url, timeout, retries, batch_size
        self._id = 0
        self.batch_ok = True

    def _post(self, payload):
        req = urllib.request.Request(
            self.url,
            json.dumps(payload).encode(),
            {"content-type": "application/json", "user-agent": "ssv-eb-reconcile/1.0"},
        )
        delay = 1.0
        for attempt in range(self.retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.load(resp)
            except urllib.error.HTTPError as exc:
                # Several providers answer JSON-RPC errors (range too large, rate limits) with HTTP 4xx.
                try:
                    body = json.loads(exc.read())
                except (ValueError, OSError):
                    body = None
                if isinstance(body, (dict, list)) and exc.code != 429:
                    return body
                if attempt == self.retries or exc.code in (401, 403, 404):
                    raise RpcError(f"transport error: HTTP {exc.code}", transport=True) from None
                time.sleep(delay)
                delay = min(delay * 2, 20)
            except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as exc:
                status = getattr(exc, "code", None)
                if attempt == self.retries or status in (400, 401, 403, 404):
                    raise RpcError(f"transport error: {type(exc).__name__} {status or ''}".strip(), transport=True) from None
                time.sleep(delay)
                delay = min(delay * 2, 20)

    def call(self, method: str, params: list):
        self._id += 1
        out = self._post({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params})
        if not isinstance(out, dict):
            raise RpcError(f"{method}: malformed response")
        if out.get("error"):
            err = out["error"]
            msg = err.get("message", err) if isinstance(err, dict) else err
            raise RpcError(f"{method}: {msg}", data=_revert_data(err))
        if "result" not in out:
            raise RpcError(f"{method}: no result")
        return out["result"]

    def batch(self, calls: list[tuple[str, list]]) -> list:
        results: list = []
        for i in range(0, len(calls), self.batch_size):
            chunk = calls[i : i + self.batch_size]
            if self.batch_ok and len(chunk) > 1:
                payload = [
                    {"jsonrpc": "2.0", "id": j, "method": m, "params": p} for j, (m, p) in enumerate(chunk)
                ]
                out = self._post(payload)
                if isinstance(out, list) and len(out) == len(chunk):
                    by_id = {o.get("id"): o for o in out}
                    if all(j in by_id and "result" in by_id[j] for j in range(len(chunk))):
                        results.extend(by_id[j]["result"] for j in range(len(chunk)))
                        continue
                    errs = [by_id.get(j, {}).get("error") for j in range(len(chunk)) if "result" not in by_id.get(j, {})]
                    log.debug("batch had errors (%s); retrying sequentially", errs[:1])
                else:
                    log.info("RPC batch unsupported or rejected; falling back to sequential calls")
                    self.batch_ok = False
            results.extend(self.call(m, p) for m, p in chunk)
        return results

    # -- helpers

    def block_number(self) -> int:
        return int(self.call("eth_blockNumber", []), 16)

    def block_hash(self, number: int) -> str:
        blk = self.call("eth_getBlockByNumber", [hex(number), False])
        if not blk:
            raise RpcError(f"block {number} not found")
        return blk["hash"].lower()

    def get_logs(self, address: str, start: int, end: int, max_range: int, topics: list[str]) -> list[dict]:
        """Bounded windows; a window the endpoint rejects is split in halves.

        Transport failures are raised, never split, and an error is never read as "no logs".
        """
        out: list[dict] = []
        lo = start
        while lo <= end:
            hi = min(end, lo + max_range - 1)
            out.extend(self._logs_range(address, lo, hi, topics))
            lo = hi + 1
        return out

    def _logs_range(self, address: str, lo: int, hi: int, topics: list[str]) -> list[dict]:
        try:
            logs = self.call(
                "eth_getLogs",
                [{"address": address, "fromBlock": hex(lo), "toBlock": hex(hi), "topics": [topics]}],
            )
        except RpcError as exc:
            if exc.transport or hi == lo:
                raise
            log.debug("getLogs %d-%d rejected (%s); splitting", lo, hi, exc)
            mid = (lo + hi) // 2
            return self._logs_range(address, lo, mid, topics) + self._logs_range(address, mid + 1, hi, topics)
        if not isinstance(logs, list):
            raise RpcError("eth_getLogs returned a non-list result")
        return logs

    def storage_many(self, address: str, slots: list[int], block: int) -> list[int]:
        res = self.batch([("eth_getStorageAt", [address, hex(s), hex(block)]) for s in slots])
        return [int(r, 16) for r in res]


class Chain:
    """The chain as one check sees it during one run: every read is pinned to `block`.

    `counts` tallies the JSON-RPC methods this view issued (one per call, batched or not), so a
    test can bound the calls a check makes per sweep.
    """

    def __init__(self, rpc: Rpc, block: int, max_log_range: int = 5000):
        self.rpc, self.block, self.max_log_range = rpc, block, max_log_range
        self.counts: Counter = Counter()

    def _tag(self, block: int | None) -> str:
        return hex(self.block if block is None else block)

    def call(self, to: str, data: bytes | str, block: int | None = None) -> bytes:
        """eth_call; a revert raises RpcError with `.data` set to the revert data when the node returns it."""
        self.counts["eth_call"] += 1
        res = self.rpc.call("eth_call", [{"to": to, "data": _hex(data)}, self._tag(block)])
        return bytes.fromhex(res[2:])

    def call_many(self, calls: list[tuple[str, bytes | str]], block: int | None = None) -> list[bytes]:
        """Batched eth_call; any revert in the batch raises (use `call` to inspect one revert)."""
        self.counts["eth_call"] += len(calls)
        tag = self._tag(block)
        res = self.rpc.batch([("eth_call", [{"to": to, "data": _hex(d)}, tag]) for to, d in calls])
        return [bytes.fromhex(r[2:]) for r in res]

    def balance(self, address: str, block: int | None = None) -> int:
        self.counts["eth_getBalance"] += 1
        return int(self.rpc.call("eth_getBalance", [address, self._tag(block)]), 16)

    def balances(self, addresses: list[str], block: int | None = None) -> list[int]:
        self.counts["eth_getBalance"] += len(addresses)
        tag = self._tag(block)
        return [int(r, 16) for r in self.rpc.batch([("eth_getBalance", [a, tag]) for a in addresses])]

    def code(self, address: str, block: int | None = None) -> bytes:
        self.counts["eth_getCode"] += 1
        return bytes.fromhex(self.rpc.call("eth_getCode", [address, self._tag(block)])[2:])

    def transaction(self, tx_hash: str) -> dict | None:
        """eth_getTransactionByHash (not block-pinned: a mined transaction does not change)."""
        self.counts["eth_getTransactionByHash"] += 1
        return self.rpc.call("eth_getTransactionByHash", [tx_hash])

    def storage(self, address: str, slots: list[int], block: int | None = None) -> list[int]:
        self.counts["eth_getStorageAt"] += len(slots)
        return self.rpc.storage_many(address, slots, self.block if block is None else block)

    def logs(self, address: str, topics: list[str], start: int, end: int | None = None) -> list[dict]:
        """Raw logs of any contract, topic0 in `topics`, in [start, end or the pinned block]."""
        self.counts["eth_getLogs"] += 1
        end = self.block if end is None else min(end, self.block)
        return self.rpc.get_logs(address, start, end, self.max_log_range, topics)


def _hex(data: bytes | str) -> str:
    return data if isinstance(data, str) else "0x" + data.hex()
