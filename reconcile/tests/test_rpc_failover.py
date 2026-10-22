"""RPC endpoint failover (D16) and the promoted batch helpers (CP3).

State table rows (plans/monitoring-service/workers/cp3/plan.md): an endpoint answering `[]`/`null` is a result;
a transport failure or a non-revert JSON-RPC error moves to the next endpoint; a revert is an answer; every
endpoint failing raises; a scan window whose reads straddle an endpoint switch is re-read, never stored.
"""

from __future__ import annotations

import json
import random
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fakechain import PROXY, FakeChain, MiniSSV, keys
from harness import make_worker

import rpc as rpc_mod
from eb_reconcile import DEFAULTS, Config
from rpc import DEFAULT_RPC_URLS, Chain, Rpc, RpcError, resolve_endpoints

OWNER = "0x" + "aa" * 20
OPS = (11, 12, 13, 14)


class Endpoints(Rpc):
    """Rpc whose HTTP layer is a per-URL behaviour over one FakeChain:
    ok | down | ratelimit | auth | fn(payload) -> body."""

    def __init__(self, chain, modes: dict, **kw):
        kw.setdefault("retries", 0)
        super().__init__(list(modes), **kw)
        self.chain, self.modes = chain, modes
        self.hits: list[str] = []

    def _post_one(self, url, payload):
        self.hits.append(url)
        mode = self.modes[url]
        if callable(mode):
            return mode(payload)
        if mode == "down":
            raise RpcError("transport error: URLError", transport=True)
        if mode == "auth":
            err = RpcError("transport error: HTTP 401", transport=True)
            err.retry = False
            raise err
        if mode == "ratelimit":
            def one(r):
                return {"jsonrpc": "2.0", "id": r["id"], "error": {"code": -32005, "message": "rate limit exceeded"}}
            return [one(r) for r in payload] if isinstance(payload, list) else one(payload)
        if isinstance(payload, list):
            return [self.chain.dispatch(r) for r in payload]
        return self.chain.dispatch(payload)


A, B, C = "https://a.example/key-in-path", "https://b.example", "https://c.example"


def limited(max_range):
    chain = FakeChain()
    chain.max_range = max_range or None
    return chain


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(rpc_mod.time, "sleep", lambda s: None)


# ---------------------------------------------------------------- configuration


def test_default_endpoints_are_the_d16_list_in_order():
    assert resolve_endpoints(DEFAULT_RPC_URLS, "MAINNET_RPC_URL", environ={}) == [
        ("mainnet.gateway.tenderly.co", "https://mainnet.gateway.tenderly.co"),
        ("gateway.tenderly.co", "https://gateway.tenderly.co/public/mainnet"),
        ("rpc.mevblocker.io", "https://rpc.mevblocker.io")]


def test_mainnet_rpc_url_overrides_the_primary_and_env_names_resolve():
    env = {"MAINNET_RPC_URL": "https://own.example/v1/SECRET", "EXTRA": "https://extra.example"}
    eps = resolve_endpoints(["https://p.example", "EXTRA", "UNSET", "https://q.example"], "MAINNET_RPC_URL", env)
    assert eps == [("MAINNET_RPC_URL", "https://own.example/v1/SECRET"), ("EXTRA", "https://extra.example"),
                   ("q.example", "https://q.example")]
    assert all("SECRET" not in label for label, _ in eps)


def test_config_endpoints_default_and_empty(monkeypatch):
    monkeypatch.delenv("MAINNET_RPC_URL", raising=False)
    cfg = Config({**DEFAULTS, "rpc_url_env": "MAINNET_RPC_URL"})
    assert [u for _, u in cfg.rpc_endpoints] == list(DEFAULT_RPC_URLS)
    cfg = Config({**DEFAULTS, "rpc_url_env": "MAINNET_RPC_URL", "rpc_urls": ["UNSET_VAR"]})
    with pytest.raises(SystemExit):
        cfg.rpc_endpoints


# ---------------------------------------------------------------- failover


def test_primary_down_fails_over_and_sticks_then_returns_to_primary(monkeypatch):
    chain = FakeChain()
    rpc = Endpoints(chain, {A: "down", B: "ok", C: "ok"}, primary_retry_s=300)
    t = [1000.0]
    monkeypatch.setattr(rpc_mod.time, "monotonic", lambda: t[0])
    assert rpc.block_number() == chain.sealed
    assert rpc.hits == [A, B] and rpc.switches == 1
    rpc.hits.clear()
    rpc.block_number()
    assert rpc.hits == [B]  # sticky: the primary is not re-tried on every request
    rpc.modes[A] = "ok"
    t[0] += 301
    rpc.hits.clear()
    rpc.block_number()
    assert rpc.hits == [A] and rpc.active == 0 and rpc.switches == 2


@pytest.mark.parametrize("method,params,empty", [
    ("eth_getLogs", [{"address": PROXY, "fromBlock": "0x1", "toBlock": "0x2", "topics": [["0x" + "00" * 32]]}], []),
    ("eth_getBlockByNumber", ["0xffffff", False], None),
])
def test_an_empty_answer_is_a_result_not_a_failure(method, params, empty):
    rpc = Endpoints(FakeChain(), {A: "ok", B: "ok"})
    assert rpc.call(method, params) == empty
    assert rpc.hits == [A]


def test_rate_limit_error_moves_to_the_next_endpoint():
    chain = FakeChain()
    rpc = Endpoints(chain, {A: "ratelimit", B: "ok"})
    assert rpc.block_number() == chain.sealed and rpc.hits == [A, B]


def test_revert_is_an_answer_and_is_not_retried_elsewhere():
    chain = FakeChain()
    chain.stub_call("0x" + "11" * 20, "0x12345678", revert="0x12e04c87")
    chain.mine()
    rpc = Endpoints(chain, {A: "ok", B: "ok"})
    with pytest.raises(RpcError) as e:
        Chain(rpc, chain.sealed).call("0x" + "11" * 20, "0x12345678")
    assert e.value.data == "0x12e04c87" and not e.value.transport and rpc.hits == [A]


def test_all_endpoints_down_raises_transport_error_without_urls():
    rpc = Endpoints(FakeChain(), {A: "down", B: "auth", C: "down"}, retries=2)
    with pytest.raises(RpcError) as e:
        rpc.block_number()
    assert e.value.transport
    msg = str(e.value)
    assert "all 3 RPC endpoints failed" in msg and "key-in-path" not in msg and "a.example" in msg
    assert rpc.hits.count(B) == 1 and rpc.hits.count(A) == 3  # 401 is not retried, the others are


def test_every_endpoint_erroring_returns_the_primarys_error_so_get_logs_still_splits():
    chain = limited(4)
    ssv = MiniSSV(chain)
    for i in range(6):
        ssv.register(OWNER, OPS, keys(1, 1000 + i))
        chain.mine()
    rpc = Endpoints(chain, {A: "ok", B: "ok"})
    topics = sorted({lg["topics"][0] for lg in chain.logs})
    logs = rpc.get_logs(PROXY, 0, chain.sealed, 16, topics)
    assert len(logs) == len(chain.logs)  # split down to the 4-block limit, nothing lost


def test_transport_failure_then_json_rpc_error_returns_the_error_not_a_transport_failure():
    chain = FakeChain()
    rpc = Endpoints(chain, {A: "down", B: "ratelimit"})
    with pytest.raises(RpcError) as e:
        rpc.block_number()
    assert not e.value.transport and "rate limit" in str(e.value)


def test_batch_item_error_falls_back_to_single_calls_with_failover():
    chain = FakeChain()
    chain.mine(3)
    state = {"n": 0}

    def flaky(payload):  # the primary answers the batch with an error item, then is down
        state["n"] += 1
        if isinstance(payload, list):
            out = [chain.dispatch(r) for r in payload]
            out[1] = {"jsonrpc": "2.0", "id": out[1]["id"], "error": {"code": -32603, "message": "internal error"}}
            return out
        raise RpcError("transport error: URLError", transport=True)

    rpc = Endpoints(chain, {A: flaky, B: "ok"})
    got = rpc.block_hashes([1, 2, 3])
    assert got == {n: chain.hash(n) for n in (1, 2, 3)}


def test_chain_batch_counts_guards_and_rejects_short_answers():
    chain = FakeChain()
    chain.mine(5)
    rpc = Endpoints(chain, {A: "ok"})
    view = Chain(rpc, 3)
    assert set(view.headers([1, 2])) == {1, 2} and view.counts["eth_getBlockByNumber"] == 2
    with pytest.raises(ValueError):
        view.batch("eth_getBlockByNumber", [[hex(4), False]], [4])
    rpc.batch = lambda calls: [None]  # one answer for two calls
    with pytest.raises(RpcError):
        view.batch("eth_getBlockByNumber", [[hex(1), False], [hex(2), False]])
    with pytest.raises(RpcError):
        Chain(Endpoints(chain, {A: "ok"}), chain.sealed + 5).headers([chain.sealed + 3])  # the node lacks it


# ---------------------------------------------------------------- QA CP3 attempt 1 F2: real HTTP endpoints


@pytest.fixture
def http_endpoint():
    """A local HTTP JSON-RPC endpoint per call: `behaviour(body) -> (status, json-able or bytes)`; returns its URL
    (an API key in the path) and the request bodies it received."""
    servers = []

    def start(behaviour):
        hits = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                hits.append(body)
                code, out = behaviour(body)
                data = out if isinstance(out, bytes) else json.dumps(out).encode()
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}/v3/SECRETKEY123", hits

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def _healthy(body):
    if isinstance(body, list):
        return 200, [{"jsonrpc": "2.0", "id": b["id"], "result": "0x10"} for b in body]
    return 200, {"jsonrpc": "2.0", "id": body["id"], "result": [] if body["method"] == "eth_getLogs" else "0x10"}


@pytest.mark.parametrize("name, behaviour", [
    ("502 + a gateway's JSON", lambda b: (502, {"message": "Bad Gateway"})),
    ("403 + JSON without error", lambda b: (403, {"message": "invalid api key"})),
    ("200 + neither result nor error", lambda b: (200, {"jsonrpc": "2.0", "id": b.get("id", 0)})),
    ("500 + JSON-RPC error", lambda b: (500, {"jsonrpc": "2.0", "id": 1, "error": {"code": -32603, "message": "x"}})),
    ("429", lambda b: (429, b"too many")),
    ("200 + HTML", lambda b: (200, b"<html>oops</html>")),
    ("range error", lambda b: (200, {"jsonrpc": "2.0", "id": b.get("id", 0),
                                      "error": {"code": -32005, "message": "query exceeds max block range 1000"}})),
])
def test_an_answer_that_is_not_a_json_rpc_answer_fails_over_to_the_next_endpoint(http_endpoint, name, behaviour):
    """QA CP3 attempt 1 F2: an HTTP error whose body is not a JSON-RPC error, and a 200 body with neither `result`
    nor `error`, are transport failures of that endpoint: the next one answers (red before: `no result` raised,
    the healthy fallback never asked); never a clean result from the broken one."""
    a, _ = http_endpoint(behaviour)
    b, hits_b = http_endpoint(_healthy)
    rpc = Rpc([a, b], timeout=5, retries=1, primary_retry_s=300)
    assert rpc.block_number() == 0x10, name
    assert rpc.get_logs("0x" + "11" * 20, 0, 4999, 5000, ["0x" + "22" * 32]) == []
    assert len(hits_b) == 2


@pytest.mark.parametrize("behaviour", [
    lambda b: (502, {"message": "Bad Gateway"}),
    lambda b: (200, {"jsonrpc": "2.0", "id": b.get("id", 0)}),
])
def test_a_lone_endpoint_answering_no_json_rpc_answer_raises_a_transport_error(http_endpoint, behaviour):
    """With every endpoint broken this way the request raises a transport error (`get_logs` does not split it
    into single blocks; never `[]`), and the message names hosts only, never the key in the path."""
    a, hits = http_endpoint(behaviour)
    c, _ = http_endpoint(behaviour)
    for urls in ([a], [a, c]):
        rpc = Rpc(urls, timeout=5, retries=0)
        with pytest.raises(RpcError) as e:
            rpc.get_logs("0x" + "11" * 20, 0, 4999, 5000, ["0x" + "22" * 32])
        assert e.value.transport and "SECRETKEY" not in str(e.value)
    assert len(hits) == 2  # one request each time: no window split


# ---------------------------------------------------------------- scan through failover


def _history(chain, n=40):
    ssv = MiniSSV(chain)
    for i in range(n):
        ssv.register(OWNER, OPS, keys(1, 5000 + i))
        chain.mine()
    return ssv


def test_scan_window_straddling_an_endpoint_switch_is_reread(tmp_path):
    chain = FakeChain()
    _, w = make_worker(tmp_path, [], chain=chain)
    start = w.checkpoint()["block"]
    _history(chain, 10)
    calls = {"n": 0}

    def dies_after_logs(payload):  # answers eth_getLogs, then goes down before the window's hash read
        calls["n"] += 1
        if calls["n"] > 2:
            raise RpcError("transport error: URLError", transport=True)
        return chain.dispatch(payload) if isinstance(payload, dict) else [chain.dispatch(r) for r in payload]

    w.rpc = Endpoints(chain, {A: dies_after_logs, B: "ok"})
    with pytest.raises(RpcError, match="endpoint changed"):
        w.scan()
    assert w.checkpoint()["block"] == start and w.db.conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0] == 0
    w.scan()  # the next step reads the window from the endpoint now serving
    assert w.checkpoint()["block"] == chain.sealed
    assert w.db.conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0] == len(chain.logs)


@pytest.mark.parametrize("seed", range(12))
def test_seeded_failover_sweep_never_loses_or_invents_logs(tmp_path, seed):
    """Random per-request endpoint behaviour (down / rate-limited / ok) over a scan of real logs: whenever a step
    succeeds the stored logs equal the chain's up to the checkpoint; a failed step never advances it."""
    rnd = random.Random(seed)
    chain = limited(rnd.choice([0, 7, 13]))
    _, w = make_worker(tmp_path, [], chain=chain, max_log_range=rnd.choice([5, 9, 20]))
    _history(chain, 30)

    def flaky(p_down, p_limit):
        def answer(payload):
            r = rnd.random()
            if r < p_down:
                raise RpcError("transport error: URLError", transport=True)
            if r < p_down + p_limit:
                def one(q):
                    return {"jsonrpc": "2.0", "id": q["id"], "error": {"code": -32005, "message": "rate limit"}}
                return [one(q) for q in payload] if isinstance(payload, list) else one(payload)
            return chain.dispatch(payload) if isinstance(payload, dict) else [chain.dispatch(q) for q in payload]
        return answer

    w.rpc = Endpoints(chain, {A: flaky(0.3, 0.1), B: flaky(0.2, 0.2), C: flaky(0.1, 0.0)},
                      retries=rnd.choice([0, 1]))
    for _ in range(60):
        before = w.checkpoint()["block"]
        try:
            w.scan()
        except RpcError:
            pass
        cp = w.checkpoint()["block"]
        assert cp >= before
        stored = w.db.conn.execute("SELECT COUNT(*) FROM logs WHERE block<=?", (cp,)).fetchone()[0]
        want = sum(1 for lg in chain.logs if int(lg["blockNumber"], 16) <= cp)
        assert stored == want
        if cp == chain.sealed:
            break
    assert w.checkpoint()["block"] == chain.sealed
