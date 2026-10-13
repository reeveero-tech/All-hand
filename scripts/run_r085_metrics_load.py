#!/usr/bin/env python3
"""R-085/R-092: /metrics (R-082/R-083) under HTTP load — series bounded, exposition valid, memory flat.

Usage: python scripts/run_r085_metrics_load.py   (10000 HTTP requests via TestClient; logs/75, logs/83)
"""
import re, sys, time, resource, logging
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
logging.disable(logging.CRITICAL)
from fastapi.testclient import TestClient
import server
from agos_kernel.infrastructure.metrics import get_metrics
VALID = re.compile(r'^[a-zA-Z_:][a-zA-Z0-9_:]*(\{([a-zA-Z_][a-zA-Z0-9_]*="[^"]*",?)*\})? -?[0-9.e+Inf-]+$')
def rss(): return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
with TestClient(server.app) as c:
    paths = ["/api", "/api/agents", "/health"] + [f"/api/tasks/id-{i}" for i in range(50)] + [f"/x/{i}" for i in range(50)]
    def round_(n):
        t = time.perf_counter()
        for i in range(n): c.get(paths[i % len(paths)])
        return time.perf_counter() - t
    round_(200)
    r0 = rss(); t1 = round_(5000); r1 = rss(); t2 = round_(5000); r2 = rss()
    text = c.get("/metrics").text
lines = text.splitlines()
req = [l for l in lines if l.startswith("agos_http_requests_total{")]
total = sum(float(l.rsplit(" ", 1)[1]) for l in req)
bad = [l for l in lines if l and not l.startswith("#") and not VALID.match(l)]
m = get_metrics()
print(f"10000 requests (2x5000) over {len(paths)} distinct raw paths: {t1:.2f}s + {t2:.2f}s")
print(f"agos_http_requests_total series: {len(req)} (sum={total:.0f}, includes 200 warm-up)")
print(f"raw path labels leaked: {sum(1 for l in req if 'id-' in l or '/x/' in l)}")
print(f"histogram series: {len(m._hist_agg)}, max raw window: {max(len(v) for v in m._histograms.values())}")
print(f"invalid exposition lines: {len(bad)} of {len(lines)}")
print(f"peak RSS MB: before={r0:.1f} after 5000={r1:.1f} after 10000={r2:.1f} (growth second half={r2-r1:.1f})")
print(f"storage series: {[l for l in lines if l.startswith('agos_storage_')]}")
