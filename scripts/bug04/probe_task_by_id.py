"""BUG-04 (R-143) evidence probe: a task persisted in storage, process memory
cleared (restart), then GET /api/tasks/{id} as its owner, another tenant, the
admin (default scope and all_tenants=true), plus a missing id and a legacy
NULL-owner row. Production mode (SEC-03) with random keys (hashes only);
the real SupabaseStorage talks HTTP to a minimal local PostgREST that applies
eq. filters like PostgREST and logs every GET query it receives.
Run: PYTHONPATH=. python3 scripts/bug04/probe_task_by_id.py"""
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROWS = {"tasks": [], "events": [], "agent_stats": []}
GETS = []


class PostgREST(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _tq(self):
        u = urllib.parse.urlsplit(self.path)
        return u.path.rsplit("/", 1)[-1], {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}

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
        row.setdefault("created_at", time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()))
        ROWS[t].append(row)
        self._send(201)

    def do_PATCH(self):  # noqa: N802
        t, q = self._tq()
        patch = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        for r in ROWS[t]:
            if r.get("task_id") == q["task_id"].removeprefix("eq."):
                r.update(patch)
        self._send(204)

    def do_GET(self):  # noqa: N802
        t, q = self._tq()
        if t == "tasks":
            GETS.append(dict(q))
        out = list(ROWS[t])
        for col, val in q.items():
            if col not in ("order", "limit", "select"):
                out = [r for r in out if r.get(col) is not None and str(r.get(col)) == val.removeprefix("eq.")]
        if "limit" in q:
            out = out[: int(q["limit"])]
        self._send(200, out)


httpd = ThreadingHTTPServer(("127.0.0.1", 0), PostgREST)
threading.Thread(target=httpd.serve_forever, daemon=True).start()

KEYS = {"tenant_a": "pa-" + secrets.token_urlsafe(16), "tenant_b": "pb-" + secrets.token_urlsafe(16),
        "ops_admin": "pd-" + secrets.token_urlsafe(16)}
os.environ.update({
    "AGOS_ENV": "production",
    "AGOS_TENANT_KEYS": json.dumps([{"tenant": t, "role": "admin" if t == "ops_admin" else "user",
                                     "key_sha256": hashlib.sha256(k.encode()).hexdigest()} for t, k in KEYS.items()]),
    "SUPABASE_URL": f"http://127.0.0.1:{httpd.server_address[1]}",
    "SUPABASE_SERVICE_KEY": "probe-not-a-real-key",
})

import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def show(label, r, gets_before):
    q = GETS[gets_before:]
    print(f"{label:44s} -> {r.status_code} {r.text[:150]}")
    print(f"{'':44s}    storage GET /rest/v1/tasks queries: {q}")


with TestClient(server.app) as c:
    H = {t: {"X-API-Key": k} for t, k in KEYS.items()}
    r = c.post("/api/tasks", json={"specialization": "analyst", "prompt": "BUG-04 probe: summarise 1 2 3"}, headers=H["tenant_a"])
    tid = r.json()["task_id"]
    for _ in range(200):
        mem = c.get(f"/api/tasks/{tid}", headers=H["tenant_a"]).json()
        row = next((x for x in ROWS["tasks"] if x["task_id"] == tid), None)
        if mem.get("status") in ("completed", "failed") and row and row["status"] == mem["status"] and row.get("result"):
            break
        time.sleep(0.05)
    print(f"created task {tid} as tenant_a; memory status={mem['status']}; stored row status={row['status']} tenant_id={row['tenant_id']}")
    ROWS["tasks"].append({"task_id": "legacy_null_1", "specialization": "analyst", "prompt": "pre-SEC-02 row",
                          "status": "completed", "tenant_id": None, "result": {"ok": True},
                          "created_at": "2026-01-01T00:00:00+00:00"})
    server.TASKS.clear()
    print(f"process memory cleared: len(server.TASKS) = {len(server.TASKS)}; {tid} in TASKS: {tid in server.TASKS}")
    listed = c.get("/api/tasks?limit=50", headers=H["tenant_a"]).json()
    print(f"GET /api/tasks as tenant_a: source={listed['source']}; {tid} listed: "
          f"{any(t.get('task_id') == tid for t in listed['tasks'])}")
    print()
    print("## retrieval after clearing process memory")
    for label, url, h in [
        ("tenant_a (owner)", f"/api/tasks/{tid}", "tenant_a"),
        ("admin all_tenants=true", f"/api/tasks/{tid}?all_tenants=true", "ops_admin"),
    ]:
        n = len(GETS)
        show(label, c.get(url, headers=H[h]), n)
    print()
    print("## cross-tenant refusal (must equal a missing id)")
    for label, url, h in [
        ("tenant_b on tenant_a's task", f"/api/tasks/{tid}", "tenant_b"),
        ("tenant_b on a missing id", "/api/tasks/doesnotexist", "tenant_b"),
        ("admin default scope on tenant_a's task", f"/api/tasks/{tid}", "ops_admin"),
        ("tenant_b all_tenants=true (not admin)", f"/api/tasks/{tid}?all_tenants=true", "tenant_b"),
        ("anonymous", f"/api/tasks/{tid}", None),
        ("tenant_a on legacy NULL-owner row", "/api/tasks/legacy_null_1", "tenant_a"),
        ("tenant_b on legacy NULL-owner row", "/api/tasks/legacy_null_1", "tenant_b"),
        ("admin all_tenants=true on legacy row", "/api/tasks/legacy_null_1?all_tenants=true", "ops_admin"),
    ]:
        n = len(GETS)
        show(label, c.get(url, headers=H[h] if h else {}), n)
    a = c.get(f"/api/tasks/{tid}", headers=H["tenant_b"])
    m = c.get("/api/tasks/doesnotexist", headers=H["tenant_b"])
    print(f"cross-tenant response == missing-id response (code+body bytes): {(a.status_code, a.content) == (m.status_code, m.content)}")
httpd.shutdown()
