#!/usr/bin/env python3
"""R-103: does a later live API task see an earlier one? (before/after probe)

Runs three tasks through the real FastAPI app (POST /api/tasks):
  1. analyst  "analyze weekly sales numbers"        [10, 12, 15, 11]
  2. analyst  "analyze weekly sales numbers again"  [10, 12, 15, 30]
  3. researcher "Python programming language" twice, the second with the
     network cut (safe_urlopen raising) — offline: DuckDuckGo is answered by a
     local stub so the probe is deterministic.
Prints the experience block of each outcome and /api/learning.
"""
import io
import json
import os
import sys
import time
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.pop("AGOS_MEMORY_FILE", None)

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402
from agent_civilization.core import safety  # noqa: E402

_DDG = {"AbstractText": "Python is a high-level programming language.", "Heading": "Python",
        "AbstractURL": "https://en.wikipedia.org/wiki/Python_(programming_language)", "RelatedTopics": []}


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _stub(req, timeout=8):
    return _Resp(json.dumps(_DDG).encode())


def _down(req, timeout=8):
    raise urllib.error.URLError("connection reset by peer")


def run(c, spec, prompt, data=None):
    tid = c.post("/api/tasks", json={"specialization": spec, "prompt": prompt, "data": data}).json()["task_id"]
    for _ in range(500):
        t = c.get(f"/api/tasks/{tid}").json()
        if t["status"] in ("completed", "failed"):
            return t["result"]
        time.sleep(0.01)


def show(label, out):
    print(f"== {label}: ok={out.get('ok')} answer={(out.get('result') or {}).get('answer') or (out.get('result') or {}).get('error')}")
    print("   experience=" + json.dumps(out.get("experience", "<absent>"), ensure_ascii=False)[:700])


with TestClient(server.app) as c:
    show("analyst #1", run(c, "analyst", "analyze weekly sales numbers", [10, 12, 15, 11]))
    show("analyst #2 (similar)", run(c, "analyst", "analyze weekly sales numbers again", [10, 12, 15, 30]))
    safety.safe_urlopen = _stub
    show("researcher live", run(c, "researcher", "Python programming language"))
    safety.safe_urlopen = _down
    show("researcher, network down", run(c, "researcher", "Python programming language"))
    r = c.get("/api/learning")
    print("== GET /api/learning:", r.status_code, json.dumps(r.json(), ensure_ascii=False)[:600])
