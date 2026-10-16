"""BUG-02 (R-141) evidence probe: one real failing /api/chat execution (the
real test_runner, message without code), stored through the real
SupabaseStorage into a minimal local PostgREST. Prints the status in memory
(TASKS), in each HTTP write sent to the database, in the stored row, in the
stored chat_completed event, and as read back by GET /api/tasks.
Production mode with one random tenant key (hash only in AGOS_TENANT_KEYS).
Run: PYTHONPATH=. python3 scripts/bug02/probe_chat_status.py"""
import hashlib
import json
import os
import secrets
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

KEY = "probe-" + secrets.token_urlsafe(18)
os.environ["AGOS_ENV"] = "production"
os.environ["AGOS_TENANT_KEYS"] = json.dumps(
    [{"tenant": "probe_tenant", "key_sha256": hashlib.sha256(KEY.encode()).hexdigest(), "role": "user"}])
for k in ("OPENROUTER_API_KEY", "AGOS_API_KEY", "AGOS_ALLOW_CODE_EXEC"):
    os.environ.pop(k, None)
os.environ["AGOS_LLM_KEYLESS"] = "0"  # deterministic keyword routing, no network

ROWS = {"tasks": [], "events": [], "agent_stats": []}
LOG = []


class PostgREST(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _tq(self):
        u = urllib.parse.urlsplit(self.path)
        return u.path.rsplit("/", 1)[-1], urllib.parse.parse_qs(u.query)

    def _send(self, code, body=None):
        data = json.dumps(body).encode() if body is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):  # noqa: N802
        t, _ = self._tq()
        row = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        LOG.append(("POST", t, dict(row)))
        row.setdefault("created_at", f"2026-10-16T00:00:{len(LOG):02d}")
        ROWS[t].append(row)
        self._send(201)

    def do_PATCH(self):  # noqa: N802
        t, q = self._tq()
        patch = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        LOG.append(("PATCH", t, dict(patch)))
        key = next(iter(q))
        for r in ROWS[t]:
            if str(r.get(key)) == q[key][0].removeprefix("eq."):
                r.update(patch)
        self._send(204)

    def do_GET(self):  # noqa: N802
        t, q = self._tq()
        out = list(ROWS[t])
        for col, vals in q.items():
            if col not in ("order", "limit", "select"):
                out = [r for r in out if str(r.get(col)) == vals[0].removeprefix("eq.")]
        out.sort(key=lambda r: r["created_at"], reverse=True)
        self._send(200, out)


httpd = ThreadingHTTPServer(("127.0.0.1", 0), PostgREST)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
os.environ["SUPABASE_URL"] = f"http://127.0.0.1:{httpd.server_address[1]}"
os.environ["SUPABASE_SERVICE_KEY"] = "probe-not-a-real-key"

import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

H = {"X-API-Key": KEY}
with TestClient(server.app) as c:
    r = c.post("/api/chat", json={"message": "run unit tests"}, headers=H)
    body = r.json()
    tid = body["task_id"]
    print("POST /api/chat ->", r.status_code, "| response keys:", sorted(body),
          "| routed_to:", body["routed_to"], "| outcome.ok:", body["outcome"]["ok"],
          "| outcome error:", body["outcome"]["result"].get("error"))
    print("memory  TASKS[...].status           :", server.TASKS[tid]["status"])
    for m, t, b in LOG:
        if t == "tasks":
            print(f"storage {m:5s} /rest/v1/tasks status :", b.get("status"))
    (row,) = [x for x in ROWS["tasks"] if x["task_id"] == tid]
    print("database stored task row status     :", row["status"])
    (ev,) = [e for e in ROWS["events"] if e["event_type"] == "chat_completed"]
    print("database chat_completed event_data  :", json.dumps(ev["event_data"], sort_keys=True))
    listed = c.get("/api/tasks", headers=H).json()
    print("GET /api/tasks (source=%s) status   :" % listed["source"],
          [t["status"] for t in listed["tasks"] if t["task_id"] == tid])
httpd.shutdown()
