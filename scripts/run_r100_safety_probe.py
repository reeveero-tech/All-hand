#!/usr/bin/env python3
"""R-100: probe the runtime-safety vectors of the real server in open mode.

Runs the real FastAPI app (TestClient) with the production default — no
AGOS_API_KEY, no opt-in flags — and tries, as an anonymous client:

  1. test_runner printing a secret from the server environment;
  2. test_runner reading the server's start-up environment (/proc/<ppid>/environ);
  3. api_integrator GET to an internal HTTP server on 127.0.0.1 (hit counted);
  4. network_client HEAD to the cloud metadata address 169.254.169.254;
  5. db_manager runaway recursive CTE (bounded by a 10 s watchdog here);
  6. registering an external agent that points at 127.0.0.1;
  7-8. with code execution explicitly allowed (AGOS_ALLOW_CODE_EXEC=1, the
     operator opt-in): the sandboxed child must still not see the secret nor
     read the server's start-up environment.

Prints one JSON report; exit code 0 only if every vector is SAFE.
Usable on any commit (before R-100 it reports UNSAFE rows).
"""
import http.server
import json
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
for var in ("AGOS_API_KEY", "AGOS_ALLOW_CODE_EXEC", "AGOS_ALLOW_PRIVATE_NETWORK"):
    os.environ.pop(var, None)
SECRET = "r100-probe-secret-9c1e"
os.environ["R100_PROBE_SECRET"] = SECRET

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402


class _Counter(http.server.BaseHTTPRequestHandler):
    hits = 0

    def do_GET(self):
        type(self).hits += 1
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *a):
        pass


def _wait(client, tid, limit_s):
    deadline = time.time() + limit_s
    while time.time() < deadline:
        t = client.get(f"/api/tasks/{tid}").json()
        if t["status"] in ("completed", "failed"):
            return t
        time.sleep(0.1)
    return {"status": "still_running_after_watchdog", "result": None}


def _finish(rows, policy):
    report = {"auth_enabled": server.ENABLE_AUTH, "tool_policy": policy, "rows": rows,
              "summary": {"total": len(rows), "safe": sum(r["safe"] for r in rows),
                          "unsafe": sum(not r["safe"] for r in rows)}}
    print(json.dumps(report, indent=2, ensure_ascii=False))
    sys.stdout.flush()
    # os._exit: on an unpatched commit a runaway query can leave the server
    # unable to answer or shut down; a normal exit would hang.
    os._exit(0 if report["summary"]["unsafe"] == 0 else 1)


def main() -> int:
    internal = http.server.HTTPServer(("127.0.0.1", 0), _Counter)
    threading.Thread(target=internal.serve_forever, daemon=True).start()
    internal_url = f"http://127.0.0.1:{internal.server_address[1]}/admin"
    rows = []

    def task(spec, prompt, limit_s=15):
        r = c.post("/api/tasks", json={"specialization": spec, "prompt": prompt})
        return _wait(c, r.json()["task_id"], limit_s)

    with TestClient(server.app) as c:
        t = task("test_runner", "```python\nimport os; print(os.environ.get('R100_PROBE_SECRET'))\n```")
        leaked = SECRET in json.dumps(t)
        rows.append({"vector": "test_runner prints server env secret", "safe": not leaked,
                     "evidence": json.dumps(t.get("result"), ensure_ascii=False)[:400]})

        # /proc/<ppid>/environ holds the server's START-UP environment — where
        # the host (Render) injects SUPABASE_SERVICE_KEY / OPENROUTER_API_KEY.
        t = task("test_runner", "```python\nimport os; print(len(open('/proc/%d/environ' % os.getppid(), 'rb').read()))\n```")
        res = (t.get("result") or {}).get("result") or {}
        stdout = ((res.get("tool_output") or {}).get("stdout") or "").strip()
        rows.append({"vector": "test_runner reads server start-up env via /proc", "safe": not (stdout.isdigit() and int(stdout) > 0),
                     "evidence": f"bytes_read={stdout or 'none'} " + json.dumps(res, ensure_ascii=False)[:300]})

        _Counter.hits = 0
        t = task("api_integrator", f"GET {internal_url}")
        rows.append({"vector": "api_integrator reaches 127.0.0.1 server", "safe": _Counter.hits == 0,
                     "evidence": f"internal_server_hits={_Counter.hits} " + json.dumps(t.get("result"))[:300]})

        t = task("network_client", "HEAD http://169.254.169.254/latest/meta-data/", limit_s=15)
        out = json.dumps(t.get("result"))
        rows.append({"vector": "network_client to metadata 169.254.169.254", "safe": "blocked" in out,
                     "evidence": out[:300]})

        t0 = time.time()

        def _frozen():
            rows.append({"vector": "db_manager runaway recursive CTE", "safe": False,
                         "evidence": "server did not answer GET /api/tasks/<id> within 20s "
                                     "after the query was submitted (whole API frozen)"})
            rows.append({"vector": "register external agent at 127.0.0.1", "safe": False,
                         "evidence": "not measured: server frozen by the previous vector"})
            _finish(rows, None)
        watchdog = threading.Timer(20, _frozen)
        watchdog.daemon = True
        watchdog.start()
        t = task("db_manager", "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c) SELECT count(*) FROM c", limit_s=10)
        rows.append({"vector": "db_manager runaway recursive CTE", "safe": t["status"] != "still_running_after_watchdog",
                     "evidence": f"status={t['status']} waited_s={round(time.time() - t0, 1)} " + json.dumps(t.get("result"))[:200]})
        watchdog.cancel()

        r = c.post("/api/agents/external", json={"name": "r100-probe", "url": internal_url})
        rows.append({"vector": "register external agent at 127.0.0.1", "safe": r.status_code == 400,
                     "evidence": f"http={r.status_code} {r.text[:200]}"})
        if r.status_code == 201:
            c.delete("/api/agents/external/r100-probe")
        policy = c.get("/api/system/status").json().get("security", {}).get("tool_policy")

        os.environ["AGOS_ALLOW_CODE_EXEC"] = "1"
        t = task("test_runner", "```python\nimport os; print(os.environ.get('R100_PROBE_SECRET'))\n```")
        res = (t.get("result") or {}).get("result") or {}
        rows.append({"vector": "opt-in mode: sandboxed code prints server env secret",
                     "safe": SECRET not in json.dumps(t) and res.get("ok") is True,
                     "evidence": json.dumps(res, ensure_ascii=False)[:300]})
        t = task("test_runner", "```python\nimport os\ntry:\n    print(len(open('/proc/%d/environ' % os.getppid(), 'rb').read()))\nexcept PermissionError:\n    print('DENIED')\n```")
        res = (t.get("result") or {}).get("result") or {}
        stdout = ((res.get("tool_output") or {}).get("stdout") or "").strip()
        rows.append({"vector": "opt-in mode: sandboxed code reads server start-up env",
                     "safe": stdout == "DENIED", "evidence": f"stdout={stdout!r} " + json.dumps(res)[:250]})
        _finish(rows, policy)

if __name__ == "__main__":
    main()
