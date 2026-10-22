import sys, pathlib
ROOT = pathlib.Path('/Users/marco/orca/workspaces/ssv-network-monitor/ms-cp2d')
sys.path[:0] = [str(ROOT/'reconcile'), str(ROOT/'reconcile/tests')]
from fakechain import MiniSSV
from fixtures_cluster import *
from harness import advance
from test_m1 import api_m1, api_row_now, incidents, tick

def test_api_ahead_of_the_checkpoint_on_a_cluster_without_logs(tmp_path):
    """Observation (pre-existing, not a QA finding): an API-only cluster changed above the checkpoint."""
    chain = ClusterChain(); ssv = MiniSSV(chain)
    cid, st = ssv._get(OWNER2, OPS2); st["count"] = 2; ssv._persist(cid, st); chain.mine()
    rows = []
    chain, w, ssv = api_m1(tmp_path, rows, chain=chain, ssv=ssv, confirmations=3)
    rows.append(api_row_now(ssv, OWNER2, OPS2)); chain.mine(4); tick(w, chain)
    assert incidents(w, "open") == []
    deposit(ssv, OWNER2, OPS2, value=5)          # a real tx with a log, above the checkpoint for 3 blocks
    rows[0] = api_row_now(ssv, OWNER2, OPS2)     # the API indexes it at once
    tick(w, chain)
    print("open after the race:", [(i["severity"], i["title"]) for i in incidents(w, "open")])
    chain.mine(4); tick(w, chain)
    print("open after the checkpoint passed it:", [(i["severity"], i["title"]) for i in incidents(w, "open")])
