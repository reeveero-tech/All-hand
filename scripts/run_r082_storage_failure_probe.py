#!/usr/bin/env python3
"""R-082 probe: is a runtime storage failure visible in /health and /metrics?

A fake PostgREST runs on 127.0.0.1 (loopback only — no external network) and is
switched between three modes while the real server.app (FastAPI TestClient)
submits and lists tasks:
  phase 1 "down"  : nothing listens on the storage port   -> connection refused
  phase 2 "401"   : every request answered 401 (key revoked) -> HTTP rejection
  phase 3 "ok"    : 201 for writes, 200 [] for reads       -> recovery
After each phase it prints /health storage status/message and storage lines of /metrics.
R-164 (D-083): the ``storage`` circuit breaker opens during phase 1 (the
store refuses connections); while it is open, operations fail at once
WITHOUT reaching the store. The probe runs with a short open period
(``AGOS_BREAKER_STORAGE_RESET_S=0.5``), prints the breaker state per phase and
waits for the half-open probe slot before the next phase — so phases 2/3
measure the real recovery path (one probe request, then closed), not a
breaker that is still open from phase 1.
R-185 (Q-026): since SEC-02/SEC-03 the tenant routes need a credential and
production start needs one configured. The probe therefore configures a
probe-only tenant (``AGOS_TENANT_KEYS`` with the SHA-256 of a key generated at
run time, never printed) and sends that key as ``X-API-Key``. The fake honours
``Prefer: return=representation`` like real PostgREST (R-161c).

Self-check: the probe prints, per phase, the API status codes and how many
requests the fake storage received. It exits 1 if the API refused the probe
(401/403) or no request reached the fake in phases 2/3 — i.e. if it is not
measuring what it was written for (the silent drift Q-026 found). Otherwise 0
(it is a probe; the before/after logs are the evidence).
"""
import hashlib
import secrets
import json
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MODE = {"value": "401"}
SEEN = {"n": 0}


class Fake(BaseHTTPRequestHandler):
    def _reply(self):
        n = int(self.headers.get("content-length") or 0)
        if n:
            self.rfile.read(n)
        SEEN["n"] += 1
        if MODE["value"] == "401":
            body, code = b'{"code":"PGRST301","message":"JWT expired"}', 401
        elif self.command == "PATCH" and "return=representation" in (self.headers.get("Prefer") or ""):
            # real PostgREST: 200 + the updated rows when asked for them (R-161c)
            q = parse_qs(urlsplit(self.path).query)
            tid = (q.get("task_id") or ["eq."])[0].removeprefix("eq.")
            code, body = 200, json.dumps([{"task_id": tid}]).encode()
        else:
            code = 201 if self.command == "POST" else (204 if self.command == "PATCH" else 200)
            body = b"" if code == 204 else b"[]"  # 204 must not carry a body
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_PATCH = _reply

    def log_message(self, *a):
        pass


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


port = free_port()
for v in ("SUPABASE_ANON_KEY", "VITE_SUPABASE_ANON_KEY", "AGOS_API_KEY", "RENDER"):
    os.environ.pop(v, None)
# probe-only tenant credential (SEC-02 format); the key exists only in this process
PROBE_KEY = secrets.token_urlsafe(32)
os.environ["AGOS_TENANT_KEYS"] = json.dumps(
    [{"tenant": "r082-probe", "key_sha256": hashlib.sha256(PROBE_KEY.encode()).hexdigest(), "role": "user"}])
HDR = {"X-API-Key": PROBE_KEY}
os.environ["SUPABASE_URL"] = f"http://127.0.0.1:{port}"
os.environ["SUPABASE_SERVICE_KEY"] = "sb_secret_" + "z" * 24  # fake, service-shaped (R-078 → no startup warning)
os.environ["AGOS_BREAKER_STORAGE_RESET_S"] = "0.5"   # R-164: short open period (see docstring)

from fastapi.testclient import TestClient  # noqa: E402
import server  # noqa: E402


PROBLEMS = []


def report(c, phase, expect_storage_traffic):
    before = SEEN["n"]
    p = c.post("/api/tasks", json={"prompt": "r082 probe task", "specialization": "analyst"}, headers=HDR)
    g = c.get("/api/tasks", headers=HDR)
    reached = SEEN["n"] - before
    print(f"[{phase}] API: POST /api/tasks -> {p.status_code}, GET /api/tasks -> {g.status_code}; "
          f"requests reaching the fake storage: {reached}")
    if p.status_code in (401, 403) or g.status_code in (401, 403):
        PROBLEMS.append(f"{phase}: the API refused the probe credential")
    if expect_storage_traffic and reached == 0:
        PROBLEMS.append(f"{phase}: no request reached the fake storage")
    h = c.get("/health").json()
    s = h.get("checks", {}).get("storage", {})
    m = [ln for ln in c.get("/metrics").text.splitlines() if "storage" in ln and not ln.startswith("#")]
    print(f"    /health={h.get('status')} storage={s.get('status')} msg={s.get('message')!r}")
    det = s.get("details", {})
    print(f"    details: last_ok={det.get('last_ok')} last_error={json.dumps(det.get('last_error'))}")
    print(f"    /metrics storage lines: {len(m)}" + ("".join(f"\n      {ln}" for ln in m[:6])))
    b = server.backpressure.breaker("storage").snapshot()
    print(f"    storage breaker (R-164): state={b['state']} opened={b['opened']} rejected={b['rejected']}")


def wait_breaker_probe():
    """R-164: wait until the storage breaker lets its half-open probe through."""
    import time
    brk = server.backpressure.breaker("storage")
    time.sleep(brk.retry_after() + 0.05)


with TestClient(server.app) as c:
    report(c, "1 down (connection refused)", expect_storage_traffic=False)
    srv = HTTPServer(("127.0.0.1", port), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    MODE["value"] = "401"
    wait_breaker_probe()
    report(c, "2 HTTP 401 (key revoked)", expect_storage_traffic=True)
    MODE["value"] = "ok"
    wait_breaker_probe()
    report(c, "3 recovered (201/200)", expect_storage_traffic=True)
    srv.shutdown()

if PROBLEMS:
    print("PROBE NOT MEASURING: " + "; ".join(PROBLEMS))
    sys.exit(1)
print("probe reached storage in every phase that expects it")
