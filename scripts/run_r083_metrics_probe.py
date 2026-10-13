#!/usr/bin/env python3
"""R-083 probe: HTTP request metrics in /metrics, histogram validity, memory bound.

Usage: python scripts/run_r083_metrics_probe.py   (logs: docs/audit/M6/logs/61-62)
"""
import re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from agos_kernel.infrastructure.metrics import MetricsCollector
import server

with TestClient(server.app) as c:
    for p in ["/api", "/api/agents", "/api/agents", "/api/tasks/abc123", "/api/tasks/def456", "/no/such/page", "/another/random/page", "/health"]:
        c.get(p)
    t = c.get("/metrics").text
lines = t.splitlines()
req = [l for l in lines if l.startswith("agos_http_requests_total")]
dur = [l for l in lines if l.startswith("agos_http_request_duration_seconds")]
print("http_requests_total lines:", len(req))
for l in req: print("   ", l)
print("duration lines:", len(dur), "(first 3)", dur[:3])
valid = re.compile(r'^[a-zA-Z_:][a-zA-Z0-9_:]*(\{([a-zA-Z_][a-zA-Z0-9_]*="[^"]*",?)*\})? -?[0-9.e+Inf-]+$')
bad = [l for l in lines if l and not l.startswith("#") and not valid.match(l)]
print("invalid exposition lines:", len(bad), bad[:3])

m = MetricsCollector(namespace="p83")
for v in (0.003, 0.003):
    m.observe_histogram("lat", v)
out = [l for l in m.export_prometheus().splitlines() if l.startswith("p83_lat")]
print("histogram (2 obs of 0.003s):", out[:3], "... count line:", [l for l in out if "_count" in l])
for _ in range(10000):
    m.observe_histogram("lat", 0.1)
print("raw values retained after 10002 observations:", len(m._histograms["p83_lat"]))
