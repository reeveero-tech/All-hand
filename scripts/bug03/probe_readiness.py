"""BUG-03 (R-142) evidence probe: GET /health/ready (status code + body) in the
four readiness states, and GET /health/live in each. Production mode with one
random tenant key (hash only) so the server starts as deployed (SEC-03).
Run: PYTHONPATH=. python3 scripts/bug03/probe_readiness.py"""
import hashlib
import json
import os
import secrets

os.environ["AGOS_ENV"] = "production"
os.environ["AGOS_TENANT_KEYS"] = json.dumps([{"tenant": "probe", "role": "user",
                                              "key_sha256": hashlib.sha256(secrets.token_bytes(16)).hexdigest()}])

import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


class Exploding(dict):
    def values(self):
        raise RuntimeError("readiness-internal-detail SUPABASE_KEY=sb_secret_probe")


real = server.CIVILIZATION
states = [
    ("ready (initialised, agents present)", real),
    ("CIVILIZATION empty", {}),
    ("pools present, zero agents", {name: [] for name in real}),
    ("exception while counting agents", Exploding(analyst=["x"])),
]
with TestClient(server.app) as c:
    for label, civ in states:
        server.CIVILIZATION = civ
        r = c.get("/health/ready")
        live = c.get("/health/live")
        print(f"{label:38s} | /health/ready -> {r.status_code} {json.dumps(r.json(), sort_keys=True)}"
              f" | /health/live -> {live.status_code} {live.json()['status']}"
              f" | secret in body: {'sb_secret_probe' in r.text}")
    server.CIVILIZATION = real
