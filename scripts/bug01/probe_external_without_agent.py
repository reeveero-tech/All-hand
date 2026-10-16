"""BUG-01 (R-140) evidence probe: POST an external task with no external agent
registered and report the response, the task state 1 s later and unhandled
background exceptions. Production mode with one random tenant key (hash only
in AGOS_TENANT_KEYS); EXTERNAL_AGENTS empty.
Run: PYTHONPATH=. python3 scripts/bug01/probe_external_without_agent.py"""
import asyncio
import gc
import hashlib
import json
import os
import secrets
import time

KEY = "probe-" + secrets.token_urlsafe(18)
os.environ["AGOS_ENV"] = "production"
os.environ["AGOS_TENANT_KEYS"] = json.dumps(
    [{"tenant": "probe_tenant", "key_sha256": hashlib.sha256(KEY.encode()).hexdigest(), "role": "user"}])
os.environ.pop("EXTERNAL_AGENTS", None)

import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

H = {"X-API-Key": KEY}
errors, writes, published = [], [], []
_real_storage, _real_publish = server.get_storage(), server._publish


class _Recorder:
    def __getattr__(self, name):
        attr = getattr(_real_storage, name)
        if callable(attr) and name.startswith(("insert", "update", "upsert")):
            def rec(*a, **kw):
                writes.append(name)
                return attr(*a, **kw)
            return rec
        return attr


async def _publish(tenant_id, event):
    published.append(event.get("type"))
    return await _real_publish(tenant_id, event)


server.get_storage = lambda: _Recorder()
server._publish = _publish
print("external agents registered:", len(server.EXTERNAL_AGENTS))
with TestClient(server.app, raise_server_exceptions=False) as c:
    c.portal.call(lambda: asyncio.get_running_loop().set_exception_handler(
        lambda loop, ctx: errors.append(repr(ctx.get("exception") or ctx.get("message")))))
    r = c.post("/api/tasks", json={"specialization": "external", "prompt": "probe"}, headers=H)
    print("POST /api/tasks {specialization: external} ->", r.status_code, r.json())
    time.sleep(1.0)
    tid = r.json().get("task_id")
    if tid:
        t = c.get(f"/api/tasks/{tid}", headers=H).json()
        print("after 1 s: status =", t["status"], "| result =", t["result"], "| agent =", t.get("agent"))
    print("tasks of the tenant:", c.get("/api/tasks", headers=H).json()["count"])
    print("storage writes:", writes, "| SSE events published:", published)
    gc.collect()
    time.sleep(0.2)
gc.collect()
print("unhandled background exceptions reported by the loop:", errors)
