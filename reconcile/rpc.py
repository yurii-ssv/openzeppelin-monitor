"""JSON-RPC client for the watch worker, and the block-pinned view checks read the chain through.

Read-only by construction: the client only issues the methods the worker needs, and no method
that signs or sends a transaction exists here.
"""

from __future__ import annotations

import http.client
import json
import logging
import os
import time
import urllib.error
import urllib.parse
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


DEFAULT_RPC_URLS = (
    # D16 (2026-10-22): public endpoints, in failover order. None is archive-guaranteed and all cap
    # eth_getLogs at 5000 blocks (`max_log_range`). `rpc_url_env` (MAINNET_RPC_URL), when set, replaces the first.
    "https://mainnet.gateway.tenderly.co",
    "https://gateway.tenderly.co/public/mainnet",
    "https://rpc.mevblocker.io",
)


def endpoint_label(url: str) -> str:
    """A label for logs and health messages: the host only (a path or user part may carry an API key)."""
    host = urllib.parse.urlsplit(url).hostname
    return host or "rpc"


def resolve_endpoints(entries, primary_env: str | None = None, environ=None) -> list[tuple[str, str]]:
    """[(label, url)] in failover order. An entry is a URL or the name of an environment variable holding one
    (an unset or empty variable is skipped). `primary_env`, when set and non-empty, replaces the first entry.
    Environment-sourced URLs are labelled by the variable name, never by their value."""
    environ = os.environ if environ is None else environ

    def env(name):
        return (environ.get(name) or "").strip().strip("\"'") if name else ""

    out: list[tuple[str, str]] = []
    for e in entries:
        if e.startswith(("http://", "https://")):
            out.append((endpoint_label(e), e))
        elif env(e):
            out.append((e, env(e)))
    if env(primary_env):
        url = env(primary_env)
        out = [(primary_env, url)] + [x for x in out[1:] if x[1] != url]
    seen: set[str] = set()
    return [x for x in out if not (x[1] in seen or seen.add(x[1]))]


def _deterministic(err) -> bool:
    """A JSON-RPC error every healthy endpoint gives alike: a revert (with or without revert data)."""
    if not isinstance(err, dict):
        return False
    return _revert_data(err) is not None or "revert" in str(err.get("message", "")).lower()


class Rpc:
    """JSON-RPC over HTTP with ordered failover across endpoints (D16).

    Each request goes to the active endpoint first, then to the others in configured order. A transport
    failure (unreachable, timeout, HTTP 5xx/429, a body that is not JSON) moves to the next endpoint; so does a
    JSON-RPC error that is not a revert (rate limit, unknown block, range or capacity errors), because another
    endpoint may answer it. A revert is an answer and is never retried elsewhere. Failover never invents a
    result: an endpoint answering `[]` or `null` is a result; when every endpoint fails the request raises
    (`RpcError(transport=True)`), and when every endpoint that answered gave a JSON-RPC error, the answer of the
    highest-priority one is returned (`call` raises it as before). `retries` rounds over all endpoints, with
    backoff between rounds. After a failover the worker returns to the first endpoint after `primary_retry_s`.
    `switches` counts changes of the serving endpoint (the scan re-reads a window whose reads straddled one).
    """

    def __init__(self, urls, timeout: float = 30, retries: int = 4, batch_size: int = 50,
                 primary_retry_s: float = 300):
        if isinstance(urls, str):
            urls = [urls]
        self.endpoints = [u if isinstance(u, tuple) else (endpoint_label(u), u) for u in urls]
        if not self.endpoints:
            raise ValueError("no RPC endpoint configured")
        self.url = self.endpoints[0][1]
        self.timeout, self.retries, self.batch_size = timeout, retries, batch_size
        self.primary_retry_s = primary_retry_s
        self._id = 0
        self.batch_ok = True
        self.active = 0  # index of the endpoint that answered last
        self.switched_at = 0.0
        self.switches = 0

    def _post_one(self, url: str, payload):
        """One HTTP attempt: the decoded JSON body, or RpcError(transport=True) (`.retry` False: do not retry)."""
        req = urllib.request.Request(
            url,
            json.dumps(payload).encode(),
            {"content-type": "application/json", "user-agent": "ssv-eb-reconcile/1.0"},
        )
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
            err = RpcError(f"transport error: HTTP {exc.code}", transport=True)
            err.retry = exc.code not in (401, 403, 404)
            raise err from None
        except (OSError, ValueError, http.client.HTTPException) as exc:  # URLError, timeouts, resets, bad JSON
            raise RpcError(f"transport error: {type(exc).__name__}", transport=True) from None

    def _select(self, i: int) -> None:
        if i != self.active:
            log.warning("RPC endpoint %s now serves requests (was %s)", self.endpoints[i][0],
                        self.endpoints[self.active][0])
            self.active, self.switched_at = i, time.monotonic()
            self.switches += 1

    def _post(self, payload):
        n = len(self.endpoints)
        first = self.active
        if first and time.monotonic() - self.switched_at >= self.primary_retry_s:
            first = 0  # after a failover, give the primary another chance
        order = [first] + [i for i in range(n) if i != first]
        errors: dict[int, str] = {}
        dead: set[int] = set()
        answer = None  # (index, body) of the first JSON-RPC error answer, in priority order
        delay = 1.0
        for attempt in range(self.retries + 1):
            for i in order:
                if i in dead:
                    continue
                try:
                    out = self._post_one(self.endpoints[i][1], payload)
                except RpcError as exc:
                    errors[i] = str(exc)
                    if not getattr(exc, "retry", True):
                        dead.add(i)
                    continue
                err = out.get("error") if isinstance(out, dict) else None
                if err and n > 1 and not _deterministic(err):
                    errors[i] = f"JSON-RPC error {err.get('message', err) if isinstance(err, dict) else err}"[:200]
                    dead.add(i)  # its answer is kept; another endpoint may serve the request
                    if answer is None or order.index(i) < order.index(answer[0]):
                        answer = (i, out)
                    continue
                self._select(i)
                return out
            if answer is not None or len(dead) == n:
                break
            if attempt < self.retries:
                time.sleep(delay)
                delay = min(delay * 2, 20)
        if answer is not None:
            self._select(answer[0])
            return answer[1]
        detail = "; ".join(f"{self.endpoints[i][0]}: {e}" for i, e in sorted(errors.items()))
        if n == 1:
            raise RpcError(errors.get(0, "transport error"), transport=True)
        raise RpcError(f"transport error: all {n} RPC endpoints failed ({detail})", transport=True)

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

    def block_hash(self, number: int, missing_ok: bool = False) -> str | None:
        """The canonical hash of block `number`; None (with `missing_ok`) when the node has no such block,
        e.g. above the head of a shorter chain after a reorg."""
        blk = self.call("eth_getBlockByNumber", [hex(number), False])
        if not blk:
            if missing_ok:
                return None
            raise RpcError(f"block {number} not found")
        return blk["hash"].lower()

    def block_hashes(self, numbers: list[int]) -> dict[int, str | None]:
        """Canonical hashes of several blocks in batched requests; None for a block the node does not have."""
        numbers = sorted(set(numbers))
        out = self.batch([("eth_getBlockByNumber", [hex(n), False]) for n in numbers])
        return {n: blk["hash"].lower() if blk else None for n, blk in zip(numbers, out)}

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

    def batch(self, method: str, params: list[list], blocks=()) -> list:
        """Batched read-only JSON-RPC `method`, one call per params list, for reads the helpers here do not
        cover (nonces, receipts, fee history, block receipts). `blocks`: every block number the calls name; each
        must be at or below the pinned block (never read above the checkpoint). A short or malformed answer is
        an RpcError, never fewer items."""
        for b in blocks:
            if b > self.block or b < 0:
                raise ValueError(f"{method} at block {b} is outside [0, pinned block {self.block}]")
        if not params:
            return []
        self.counts[method] += len(params)
        res = self.rpc.batch([(method, p) for p in params])
        if not isinstance(res, list) or len(res) != len(params):
            raise RpcError(f"{method}: {len(res) if isinstance(res, list) else 'no'} results for {len(params)} calls")
        return res

    def request(self, method: str, params: list):
        """One read-only JSON-RPC call, counted; the caller chooses the block (e.g. the unconfirmed next block)."""
        self.counts[method] += 1
        return self.rpc.call(method, params)

    def headers(self, blocks: list[int], full: bool = False) -> dict[int, dict]:
        """Block headers (or full blocks) at or below the pinned block; a missing block is an RpcError."""
        blocks = sorted(set(blocks))
        out = {}
        for b, r in zip(blocks, self.batch("eth_getBlockByNumber", [[hex(b), full] for b in blocks], blocks)):
            if not isinstance(r, dict) or not r.get("hash"):
                raise RpcError(f"eth_getBlockByNumber({b}): no block")
            out[b] = r
        return out

    def logs(self, address: str, topics: list[str], start: int, end: int | None = None) -> list[dict]:
        """Raw logs of any contract, topic0 in `topics`, in [start, end or the pinned block]."""
        self.counts["eth_getLogs"] += 1
        end = self.block if end is None else min(end, self.block)
        return self.rpc.get_logs(address, start, end, self.max_log_range, topics)


def _hex(data: bytes | str) -> str:
    return data if isinstance(data, str) else "0x" + data.hex()
