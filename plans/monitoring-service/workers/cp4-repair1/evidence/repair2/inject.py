"""Filter-failure injection proxy for the D21 live control (ms-cp4r): forwards JSON-RPC to one public endpoint and
answers HTTP 429 to eth_getLogs of chosen blocks, so the Monitor's filter of those blocks fails while the block itself
was fetched. Blocks with n % ALWAYS == 0 always fail (the recovery gives up); blocks with n % FIRST == 0 fail on their
first FIRST_N requests (the main cycle fails, a recovery retry succeeds). Logs every injected failure."""
import json
import sys
import time
import urllib.request
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM, PORT = sys.argv[1], int(sys.argv[2])
ALWAYS, FIRST, FIRST_N = 7, 3, 8
seen = Counter()


def inject(req):
    if req.get("method") != "eth_getLogs":
        return False
    p = (req.get("params") or [{}])[0]
    try:
        n = int(p.get("fromBlock"), 16)
    except (TypeError, ValueError):
        return False
    seen[n] += 1
    if n % ALWAYS == 0 or (n % FIRST == 0 and seen[n] <= FIRST_N):
        print(f"{time.strftime('%H:%M:%S', time.gmtime())} inject 429 eth_getLogs block {n} (request {seen[n]})",
              flush=True)
        return True
    return False


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            req = json.loads(body)
        except ValueError:
            req = None
        reqs = req if isinstance(req, list) else [req]
        if any(isinstance(r, dict) and inject(r) for r in reqs):
            self.send_response(429)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"injected 429")
            return
        r = urllib.request.Request(UPSTREAM, data=body, headers={"Content-Type": "application/json",
                                                                  "User-Agent": "cp4r-d21-inject"})
        try:
            with urllib.request.urlopen(r, timeout=30) as resp:
                data, code = resp.read(), resp.status
        except urllib.error.HTTPError as e:
            data, code = e.read(), e.code
        except Exception as e:  # upstream down: 502
            data, code = str(e).encode(), 502
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
