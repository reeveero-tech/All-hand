#!/usr/bin/env python3
"""R-078 probe: what the server reports for each Supabase key kind (fake keys; SUPABASE_URL unreachable on purpose).

Usage: run_r078_key_probe.py <legacy_anon_jwt|anon_jwt_in_service_var|publishable|service_jwt|secret>
"""
import sys; from pathlib import Path; sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import base64, json, logging, os, sys, io
def b64(d): return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
case = sys.argv[1]
fake = {
  "legacy_anon_jwt": ("SUPABASE_ANON_KEY", b64({"alg":"HS256","typ":"JWT"})+"."+b64({"role":"anon","iss":"supabase"})+".sig"),
  "anon_jwt_in_service_var": ("SUPABASE_SERVICE_KEY", b64({"alg":"HS256","typ":"JWT"})+"."+b64({"role":"anon"})+".sig"),
  "publishable": ("SUPABASE_SERVICE_KEY", "sb_publishable_" + "x"*20),
  "service_jwt": ("SUPABASE_SERVICE_KEY", b64({"alg":"HS256","typ":"JWT"})+"."+b64({"role":"service_role"})+".sig"),
  "secret": ("SUPABASE_SERVICE_KEY", "sb_secret_" + "y"*20),
}[case]
for v in ("SUPABASE_SERVICE_KEY","SUPABASE_ANON_KEY","VITE_SUPABASE_ANON_KEY"): os.environ.pop(v, None)
os.environ["SUPABASE_URL"] = "http://127.0.0.1:9"   # unreachable on purpose: no data is written
os.environ[fake[0]] = fake[1]
buf = io.StringIO(); logging.basicConfig(stream=buf, level=logging.INFO)
from fastapi.testclient import TestClient
import server
with TestClient(server.app) as c:
    h = c.get("/health").json(); s = c.get("/api/system/status").json()
logs = buf.getvalue()
print(f"[{case}] var={fake[0]} | /health.status={h.get('status')} storage={h.get('checks',{}).get('storage',{}).get('status')} msg={h.get('checks',{}).get('storage',{}).get('message')!r}")
print(f"    system_status.supabase_key={s.get('supabase_key')} | startup warning={'public' in logs.lower() and 'SUPABASE_SERVICE_KEY' in logs} | key value leaked in logs={fake[1] in logs}")
