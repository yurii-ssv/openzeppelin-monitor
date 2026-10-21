"""Reorg semantics of every piece of state the worker keeps across blocks (checks/README.md §6.1).

One test (or parametrised pair, depth 1 and depth N) per row of the README table that was wrong or
untested before CP1B repair 2. FakeChain.reorg drops logs and storage writes at and above the fork block
and changes those blocks' hashes; it leaves `eth_call` stubs in place, so each test re-stubs the
canonical values of the new chain explicitly.
"""

from __future__ import annotations

import json

import pytest
from fakechain import PROXY, FakeChain, MiniSSV, keys
from harness import VIEWS, make_worker, outbox, stub_params
from test_framework import Stub, finding, inbox_line
from test_worker import OPS, OWNER, ev_for, poc_setup, tick

import eb_model as M
from checks.base import Every, NearSetEveryBlock, OnEvents, OnInbox
from eb_storage import u256
from metrics import render
from params import GETTERS, ParamsUnavailable, selector
from rpc import RpcError


def status(tmp_path) -> dict:
    return json.loads((tmp_path / "w.status.json").read_text())


def fee_event(chain, old, new):
    chain.emit_event("NetworkFeeUpdated", [], u256(old) + u256(new))


# ---------------------------------------------------------------- live parameters and prepare copies


def test_reorg_params_reread_failure_blocks_consumers_until_it_succeeds(tmp_path):
    chain = FakeChain()
    stub_params(chain)
    chain.mine()
    seen = []
    stub = Stub(emit=lambda ctx, b: seen.append(ctx.params.liquidation_threshold_period) or [])
    _, w = make_worker(tmp_path, [stub], chain=chain, views=VIEWS)
    w.step()
    fork = chain.number
    stub_params(chain, liquidation_threshold_period=50_000)
    chain.emit_event("LiquidationThresholdPeriodUpdated", [], u256(50_000))
    chain.mine()
    w.step()
    assert seen == [42_960, 50_000]
    chain.reorg(fork)  # the new chain has no parameter event, and its getter cannot be read yet
    chain.stub_call(VIEWS, selector(GETTERS["liquidation_threshold_period"]), revert="0x12345678")
    chain.mine()
    for _ in range(3):
        w.step(force=True)
        assert seen == [42_960, 50_000]  # never evaluated with the orphan value (nor anything else)
        with pytest.raises(ParamsUnavailable):
            w.params.liquidation_threshold_period
        assert status(tmp_path)["healthy"] is False and status(tmp_path)["params"]["ok"] is False
        assert w.db.meta("inputs_unavailable")["params"] is True
    assert len(outbox(w, "key LIKE 'health:params:%'")) == 1
    stub_params(chain)
    chain.mine()
    w.step()  # no force: the pending reorg and the recovered parameters schedule the run
    assert seen == [42_960, 50_000, 42_960]
    assert {"reorg", "params"} <= stub.runs[-1]["reasons"] and status(tmp_path)["healthy"] is True


def test_reorg_depth_n_rereads_params_and_every_prepare_copy(tmp_path):
    chain = FakeChain()
    stub_params(chain)
    chain.mine()
    seen = []

    class Cached(Stub):
        def prepare(self, ctx):
            self.copy = ctx.params.network_fee

        def run(self, ctx, block):
            seen.append((block, self.copy, ctx.params.network_fee))
            return []

    _, w = make_worker(tmp_path, [Cached(schedule=Every(60))], chain=chain, views=VIEWS)
    w.step()
    fork = chain.number
    for fee in (10, 20, 30):  # three orphan blocks, each changing the fee
        stub_params(chain, network_fee=fee)
        fee_event(chain, fee - 1, fee)
        chain.mine()
        w.step()
    assert [s[1:] for s in seen] == [(3_366_600_000,) * 2, (10, 10), (20, 20), (30, 30)]
    chain.reorg(fork)  # new chain of the same height: one change, to 7, in its second block
    stub_params(chain)
    chain.mine()
    stub_params(chain, network_fee=7)
    fee_event(chain, 3_366_600_000, 7)
    chain.mine(2)
    w.step()
    assert seen[-1] == (fork + 2, 7, 7) and w.params.block == fork + 2
    w.step(force=True)
    assert seen[-1] == (fork + 2, 7, 7)


# ---------------------------------------------------------------- inventory cache


@pytest.mark.parametrize("depth", [1, 3])
def test_reorg_invalidates_the_inventory_cache(tmp_path, depth):
    chain = FakeChain()
    chain.mine()
    ssv = MiniSSV(chain)
    ssv.register(OWNER, OPS, keys(1, 1))  # MiniSSV mines the block it writes
    seen = []
    stub = Stub(emit=lambda ctx, b: seen.append(ctx.inventory()) or [])
    _, w = make_worker(tmp_path, [stub], chain=chain)
    w.step(force=True)
    fork = chain.number
    ssv.register(OWNER, OPS, keys(1, 2))  # orphaned: the cluster grows to 2 validators
    chain.mine(depth - 1)
    w.step(force=True)
    cid = next(iter(seen[-1].clusters))
    assert seen[-1].clusters[cid].cluster.validator_count == 2
    chain.reorg(fork)
    chain.mine(depth)  # the same checkpoint block number on the new chain
    w.step(force=True)
    assert seen[-1].block == seen[-2].block
    assert seen[-1].clusters[cid].valid and seen[-1].clusters[cid].cluster.validator_count == 1


# ---------------------------------------------------------------- event cursors, metrics checkpoint


def test_reorg_depth_n_replays_only_new_chain_events_once(tmp_path):
    chain = FakeChain()
    chain.mine()
    stub = Stub(schedule=OnEvents({"NetworkFeeUpdated"}))
    _, w = make_worker(tmp_path, [stub], chain=chain)
    w.step()
    fork = chain.number
    for i in range(3):
        fee_event(chain, i, i + 1)
        chain.mine()
        w.step()
    assert len(stub.runs) == 3
    chain.reorg(fork)
    chain.mine()
    fee_event(chain, 7, 8)
    chain.mine(2)
    w.step()
    w.step()
    assert len(stub.runs) == 4 and stub.runs[3]["events"] == ["NetworkFeeUpdated"]
    assert {"reorg", "events"} <= stub.runs[3]["reasons"]
    rows = w.db.conn.execute("SELECT block, block_hash FROM logs").fetchall()
    assert [(r["block"], r["block_hash"]) for r in rows] == [(fork + 1, chain.hash(fork + 1))]
    cp = w.checkpoint()
    assert cp == {"block": chain.sealed, "hash": chain.hash(chain.sealed)}
    assert f"ssv_watch_checkpoint_block {cp['block']}" in render(w.db.conn, ["stub"])


def test_scan_rejects_logs_of_a_block_reorganised_during_the_scan(tmp_path):
    chain = FakeChain()
    chain.mine()
    stub = Stub(schedule=OnEvents({"NetworkFeeUpdated"}))
    _, w = make_worker(tmp_path, [stub], chain=chain)
    w.step()
    fork = chain.number
    fee_event(chain, 1, 2)
    chain.mine()
    real = w.rpc.get_logs

    def racing(*args):
        out = real(*args)
        del w.rpc.get_logs
        chain.reorg(fork)  # between eth_getLogs and the hash read: same height, other fork, other event
        fee_event(chain, 5, 6)
        chain.mine()
        return out

    w.rpc.get_logs = racing
    w.step()
    assert w.rpc_failures == 1 and stub.runs == []
    assert w.db.conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0] == 0
    w.step()
    rows = w.db.conn.execute("SELECT block, block_hash, extra FROM logs").fetchall()
    assert [(r["block"], r["block_hash"]) for r in rows] == [(fork, chain.hash(fork))]
    assert "06" in json.loads(rows[0]["extra"])["data"][-4:]
    assert len(stub.runs) == 1 and w.rpc_failures == 0


# ---------------------------------------------------------------- durable reorg marker, ctx.reorg_ancestor


def test_reorg_marker_survives_a_restart_and_gives_the_lowest_ancestor(tmp_path):
    chain = FakeChain()
    chain.mine()

    class Rec(Stub):
        def on_reorg(self, ancestor):
            self.reorgs.append(ancestor)

    def make_stub():
        return Rec(emit=lambda ctx, b: seen.append((b, ctx.reorg_ancestor, set(ctx.reasons))) or [], reorgs=[])

    seen = []
    _, w = make_worker(tmp_path, [make_stub()], chain=chain)
    s = chain.sealed
    w.step()
    for _ in range(2):
        chain.mine()
        w.step(force=True)
    assert w.checkpoint()["block"] == s + 2
    chain.reorg(s + 2)  # the new chain is one block shorter: block s+2 is not found
    w.check_reorg()  # rewound and committed; the process dies before any check runs
    assert w.db.meta("reorg:stub") == s + 1
    chain.reorg(s + 1)  # a second, deeper reorg while the worker is down
    stub = make_stub()
    _, w2 = make_worker(tmp_path, [stub], chain=chain, start_block=w.cfg.start_block,
                        activation_block=w.cfg.activation_block)
    assert stub.reorgs == [s + 1]  # the restart replays the uncommitted reorg
    w2.step()
    assert stub.reorgs == [s + 1, s] and seen[-1][:2] == (s, s) and "reorg" in seen[-1][2]
    assert w2.db.meta("reorg:stub") is None and w2.rpc_failures == 0
    chain.mine()
    w2.step(force=True)
    assert seen[-1][:2] == (s + 1, None)


def test_near_set_check_runs_after_a_reorg_onto_a_shorter_chain(tmp_path):
    chain = FakeChain()
    chain.mine()
    stub = Stub(schedule=NearSetEveryBlock(), near_set=frozenset({"x"}))
    _, w = make_worker(tmp_path, [stub], chain=chain)
    s = chain.sealed
    w.step()
    for _ in range(2):
        chain.mine()
        w.step()
    chain.reorg(s + 1)  # the checkpoint block s+2 and s+1 are gone; the new head is s
    w.step()
    assert w.rpc_failures == 0 and w.checkpoint() == {"block": s, "hash": chain.hash(s)}
    for _ in range(2):
        chain.mine()
        w.step()
    assert [r["block"] for r in stub.runs] == [s, s + 1, s + 2, s, s + 1, s + 2]
    assert "reorg" in stub.runs[3]["reasons"] and stub.runs[4]["reasons"] == {"block"}


# ---------------------------------------------------------------- incidents and outbox keyed by block


def scripted(*results):
    """A stub returning the next list of findings at each run."""
    todo = list(results)
    return Stub(emit=lambda ctx, b: todo.pop(0))


def test_default_incident_id_reused_by_a_reorganised_block_gets_a_suffix(tmp_path):
    chain = FakeChain()
    chain.mine()
    stub = scripted([], [finding(severity="warn")], [finding(severity="warn", resolve=True)],
                    [finding(severity="warn")])
    _, w = make_worker(tmp_path, [stub], chain=chain)
    w.step(force=True)
    fork = chain.number
    chain.mine()
    w.step(force=True)  # opens stub-k1-<fork> on the orphan block ...
    w.step(force=True)  # ... and resolves it there
    chain.reorg(fork)
    chain.mine()
    w.step(force=True)  # the new block <fork> reports the key again
    assert not outbox(w, "key LIKE 'health:check-error:%'")
    ids = [r["id"] for r in w.db.conn.execute("SELECT id FROM incidents ORDER BY opened_at, rowid")]
    assert ids == [f"stub-k1-{fork}", f"stub-k1-{fork}-2"]
    assert [r["incident_id"] for r in outbox(w, "kind='open'")] == ids


def test_escalation_replayed_on_a_reorganised_block_is_sent_again(tmp_path):
    chain = FakeChain()
    chain.mine()
    stub = scripted([finding(severity="warn")], [finding(severity="page")], [finding(severity="warn")],
                    [finding(severity="page")])
    _, w = make_worker(tmp_path, [stub], chain=chain)
    w.step(force=True)
    fork = chain.number
    chain.mine()
    w.step(force=True)  # page on the orphan block <fork>
    chain.reorg(fork)
    chain.mine()
    w.step(force=True)  # the new block <fork>: warn (recorded silently) ...
    w.step(force=True)  # ... then page again at the same block number
    rows = outbox(w, "kind='escalate'")
    assert [r["key"] for r in rows] == [f"stub-k1-{fork - 1}:escalate:page:{fork}",
                                        f"stub-k1-{fork - 1}:escalate:page:{fork}#2"]
    assert [s for s, k, _, _ in w.notifier.sent if ":escalate:" in k] == ["page", "page"]


# ---------------------------------------------------------------- inbox gate (checks/README.md §5.2)


def inbox_worker(tmp_path, stub, **over):
    chain = FakeChain()
    chain.stub_call(PROXY, "0xbc26e7e5", revert="0x12e04c87")
    chain.mine()
    path = tmp_path / "in.jsonl"
    path.write_text("")
    _, w = make_worker(tmp_path, [stub], chain=chain, inbox={"path": str(path)}, **over)
    return chain, w, path


def failed_tx(chain) -> tuple[str, int]:
    tx = chain.add_tx("0x" + "11" * 20, PROXY, "0xbc26e7e5")
    chain.mine()
    return tx, chain.sealed


def append(path, chain, tx, block, block_hash=None):
    with open(path, "a") as fh:
        fh.write(inbox_line(tx=tx, block=block, block_hash=block_hash or chain.hash(block)))


def inbox_rows(w):
    return [dict(r) for r in w.db.conn.execute("SELECT tx_hash, block, canonical FROM inbox ORDER BY id")]


def test_inbox_entry_above_the_checkpoint_waits_then_is_delivered_once(tmp_path):
    stub = Stub(schedule=OnInbox())
    chain, w, path = inbox_worker(tmp_path, stub, confirmations=3)
    tx, block = failed_tx(chain)
    append(path, chain, tx, block)  # the Monitor writes at 2 confirmations; the checkpoint is 3 deep here
    for _ in range(3):
        w.step()
        assert stub.runs == [] and w.status()["inbox"]["held"] == 1
        assert w.checkpoint()["block"] < block
        chain.mine()
    w.step()
    w.step()
    assert w.checkpoint()["block"] == block
    assert [r["inbox"] for r in stub.runs] == [[tx]] and stub.runs[0]["reasons"] == {"inbox"}
    assert inbox_rows(w) == [{"tx_hash": tx, "block": block, "canonical": 1}]
    assert status(tmp_path)["healthy"] is True and w.status()["inbox"]["held"] == 0


def test_inbox_entry_of_an_orphaned_block_is_never_delivered_and_its_reinclusion_is(tmp_path):
    stub = Stub(schedule=OnInbox())
    chain, w, path = inbox_worker(tmp_path, stub, confirmations=4)
    w.step()
    tx, block = failed_tx(chain)
    chain.mine(2)
    append(path, chain, tx, block)  # 2 confirmations, not yet below the worker's checkpoint
    w.step()
    assert stub.runs == []
    chain.reorg(block)  # 3 blocks deep: the Monitor's block is replaced
    chain.mine(5)
    w.step()
    w.step()
    assert stub.runs == [] and inbox_rows(w) == [{"tx_hash": tx, "block": block, "canonical": 0}]
    assert w.status()["inbox"]["orphaned"] == 1 and status(tmp_path)["healthy"] is True
    # the transaction is re-included in a later block of the new chain: a new (tx_hash, block_hash) line
    chain.txs[tx]["blockNumber"] = hex(chain.number)
    chain.mine(5)
    append(path, chain, tx, chain.sealed - 4)
    for _ in range(3):
        w.step()
    assert [r["inbox"] for r in stub.runs] == [[tx]]
    assert [r["canonical"] for r in inbox_rows(w)] == [0, 1]


def test_inbox_hash_check_is_one_batch(tmp_path):
    stub = Stub(schedule=OnInbox())
    chain, w, path = inbox_worker(tmp_path, stub)
    txs = []
    for _ in range(5):
        tx, block = failed_tx(chain)
        txs.append(tx)
        append(path, chain, tx, block)
    posts = []
    real = w.rpc._post

    def spy(payload):
        posts.append(payload)
        return real(payload)

    w.rpc._post = spy
    w.step()
    lookups = [p for p in posts if isinstance(p, list) and p[0]["method"] == "eth_getBlockByNumber"]
    assert len(lookups) == 1 and len(lookups[0]) == 5
    assert [r["inbox"] for r in stub.runs] == [txs]


def test_inbox_hash_check_failure_holds_entries_and_degrades_health(tmp_path):
    stub = Stub(schedule=OnInbox())
    chain, w, path = inbox_worker(tmp_path, stub)
    tx, block = failed_tx(chain)
    append(path, chain, tx, block)

    def down(numbers):
        raise RpcError("transport error: URLError", transport=True)

    w.rpc.block_hashes = down
    for _ in range(3):
        w.step()
        assert stub.runs == [] and inbox_rows(w)[0]["canonical"] is None
        s = status(tmp_path)
        assert s["healthy"] is False and s["inbox"]["ok"] is False and "block hashes" in s["error"]
        assert w.db.meta("inputs_unavailable")["inbox"] is True
    del w.rpc.block_hashes
    w.step()
    assert [r["inbox"] for r in stub.runs] == [[tx]] and status(tmp_path)["healthy"] is True
    assert w.db.meta("inbox:unverified") is None


def test_reorg_below_the_checkpoint_rechecks_undelivered_inbox_entries(tmp_path):
    fail = {"on": True}

    def emit(ctx, b):
        if fail["on"] and ctx.inbox:
            raise RuntimeError("consumer bug")
        return []

    stub = Stub(schedule=OnInbox(), emit=emit)
    chain, w, path = inbox_worker(tmp_path, stub)
    w.step()
    fork = chain.number
    tx, block = failed_tx(chain)
    append(path, chain, tx, block)
    w.step()  # confirmed canonical, but the run fails: the entry is still undelivered
    assert inbox_rows(w)[0]["canonical"] == 1 and w.db.meta("cursor:inbox:stub", 0) == 0
    fail["on"] = False
    chain.reorg(fork)
    chain.mine()
    w.step()
    w.step()
    assert inbox_rows(w)[0]["canonical"] == 0
    assert all(r["inbox"] == [] for r in stub.runs[1:]) and "reorg" in stub.runs[1]["reasons"]


# ---------------------------------------------------------------- M5 (depth N; depth 1 is test_worker.py)


def test_m5_reorg_depth_n_reevaluates_on_the_new_chain(tmp_path):
    chain, w, ssv = poc_setup(tmp_path)
    s = chain.number
    chain.mine()
    d = keys(63, 6_000)
    ssv.register(OWNER, OPS, d)
    ssv.commit(s)
    ssv.update(OWNER, OPS, 2048)
    fork_point = chain.number
    ssv.remove(OWNER, OPS, d)
    chain.mine(3)
    assert ev_for(tick(w)).status == M.CONFIRMED
    chain.reorg(fork_point)  # 4 blocks: the removal and the blocks after it
    chain.mine(4)
    e = ev_for(tick(w))
    assert e.status == M.LATENT and w.checkpoint()["block"] == chain.sealed
    assert not w.db.conn.execute("SELECT 1 FROM logs WHERE block>=? AND kind='ValidatorRemoved'",
                                 (fork_point,)).fetchone()


def test_reorg_deeper_than_the_search_stops_the_worker(tmp_path):
    chain = FakeChain()
    chain.mine()
    _, w = make_worker(tmp_path, [], chain=chain, reorg_search_depth=2)
    w.step()
    fork = chain.number
    for _ in range(3):
        chain.mine()
        w.step()
    cp = w.checkpoint()
    chain.reorg(fork)  # the 2 newest recorded blocks are both replaced
    chain.mine(3)
    with pytest.raises(SystemExit, match="deeper than 2 recorded blocks"):
        w.step()
    assert w.checkpoint() == cp and w.db.conn.execute("SELECT COUNT(*) FROM blocks").fetchone()[0] == 4
