#!/usr/bin/env python3
"""R-082 probe: is a runtime storage failure visible in /health and /metrics?

A fake PostgREST runs on 127.0.0.1 (loopback only — no external network) and is
switched between three modes while the real server.app (FastAPI TestClient)
submits and lists tasks:
  phase 1 "down"  : nothing listens on the storage port   -> connection refused
  phase 2 "401"   : every request answered 401 (key revoked) -> HTTP rejection
  phase 3 "ok"    : 201 for writes, 200 [] for reads       -> recovery
After each phase it prints /health storage status/message and storage lines of /metrics.
Exit code 0 always (it is a probe; the before/after logs are the evidence).
"""
import json
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MODE = {"value": "401"}


class Fake(BaseHTTPRequestHandler):
    def _reply(self):
        n = int(self.headers.get("content-length") or 0)
        if n:
            self.rfile.read(n)
        if MODE["value"] == "401":
            body, code = b'{"code":"PGRST301","message":"JWT expired"}', 401
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
for v in ("SUPABASE_ANON_KEY", "VITE_SUPABASE_ANON_KEY", "AGOS_API_KEY"):
    os.environ.pop(v, None)
os.environ["SUPABASE_URL"] = f"http://127.0.0.1:{port}"
os.environ["SUPABASE_SERVICE_KEY"] = "sb_secret_" + "z" * 24  # fake, service-shaped (R-078 → no startup warning)

from fastapi.testclient import TestClient  # noqa: E402
import server  # noqa: E402


def report(c, phase):
    c.post("/api/tasks", json={"prompt": "r082 probe task", "specialization": "analyst"})
    c.get("/api/tasks")
    h = c.get("/health").json()
    s = h.get("checks", {}).get("storage", {})
    m = [ln for ln in c.get("/metrics").text.splitlines() if "storage" in ln and not ln.startswith("#")]
    print(f"[{phase}] /health={h.get('status')} storage={s.get('status')} msg={s.get('message')!r}")
    det = s.get("details", {})
    print(f"    details: last_ok={det.get('last_ok')} last_error={json.dumps(det.get('last_error'))}")
    print(f"    /metrics storage lines: {len(m)}" + ("".join(f"\n      {ln}" for ln in m[:6])))


with TestClient(server.app) as c:
    report(c, "1 down (connection refused)")
    srv = HTTPServer(("127.0.0.1", port), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    MODE["value"] = "401"
    report(c, "2 HTTP 401 (key revoked)")
    MODE["value"] = "ok"
    report(c, "3 recovered (201/200)")
    srv.shutdown()
