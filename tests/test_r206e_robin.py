"""R-206e (D-165): Robin service wrapper + All-hand routes.

Mock tests only: they prove the rules of the wrapper and of the proxy (secret,
fail-closed, presets, depth caps, one at a time, abuse filter re-applied,
signed-in + live session, per-user limit). They are NOT proof that the live
service on Render works; that evidence is in the R-206e audit log.
"""
from __future__ import annotations

import importlib.util
import pathlib
import threading
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from agos_v2 import robin_api

ROOT = pathlib.Path(__file__).resolve().parents[1]
SECRET = "s" * 40
BAD_LINK = "http://x.onion/" + "c" + "sam-archive"   # matches the abuse pattern (fake)


def _load_service():
    spec = importlib.util.spec_from_file_location("robin_service_app", ROOT / "services/robin/app.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


svc = _load_service()


class FakeRobin:
    def __init__(self, sources=None, fail=None, gate=None):
        self.calls = []
        self.sources = sources if sources is not None else [
            {"title": "Leak forum", "link": "http://a.onion/x"},
            {"title": "bad", "link": BAD_LINK},
        ]
        self.fail, self.gate = fail, gate

    def models(self):
        return ["openrouter/test-model"]

    def investigate(self, query, model, preset, depth):
        self.calls.append((query, model, preset, dict(depth)))
        if self.gate is not None:
            self.gate.wait(5)
        if self.fail:
            return {"ok": False, "stage": "search", "error": self.fail}
        return {"ok": True, "record": {"query": query, "summary": "report", "status": "ok",
                                       "sources": list(self.sources), "pivots": []}}

    def is_abuse_material(self, result):
        return "sam-archive" in (result.get("link") or "")

    def tor_ready(self):
        return True


def service(fake=None, secret=SECRET):
    fake = fake or FakeRobin()
    app = svc.create_app(lambda: fake, env={"ROBIN_SHARED_SECRET": secret} if secret else {})
    return TestClient(app), fake


H = {"X-Robin-Token": SECRET}
BODY = {"query": "ransomware group leak site", "model": "openrouter/test-model"}


# ------------------------------------------------------------ the service
def test_health_is_public_and_reveals_no_secret():
    c, _ = service()
    j = c.get("/health").json()
    assert j["ok"] and j["configured"] and j["tor_bootstrapped"]
    assert SECRET not in str(j)


@pytest.mark.parametrize("secret", [None, "short"])
def test_fail_closed_without_a_real_secret(secret):
    c, fake = service(secret=secret)
    assert c.get("/models", headers={"X-Robin-Token": secret or ""}).status_code == 503
    assert c.post("/investigate", json=BODY, headers={"X-Robin-Token": secret or ""}).status_code == 503
    assert fake.calls == []


def test_wrong_or_missing_token_is_401():
    c, fake = service()
    assert c.post("/investigate", json=BODY).status_code == 401
    assert c.post("/investigate", json=BODY, headers={"X-Robin-Token": "x" * 40}).status_code == 401
    assert fake.calls == []


def test_models_lists_only_the_allowed_presets():
    c, _ = service()
    j = c.get("/models", headers=H).json()
    assert j["models"] == ["openrouter/test-model"]
    assert "personal_identity" not in j["presets"]
    assert set(j["presets"]) == set(svc.ALLOWED_PRESETS)


def test_personal_identity_preset_is_refused():
    c, fake = service()
    r = c.post("/investigate", json={**BODY, "preset": "personal_identity"}, headers=H)
    assert r.status_code == 422 and fake.calls == []


@pytest.mark.parametrize("bad", [{}, {"query": "ab", "model": "m"}, {"query": "x" * 301, "model": "m"},
                                 {"query": "valid query"}, {**BODY, "max_results": "9"},
                                 {**BODY, "threads": True}])
def test_invalid_bodies_are_422(bad):
    c, fake = service()
    assert c.post("/investigate", json=bad, headers=H).status_code == 422
    assert fake.calls == []


def test_depth_is_capped_below_robins_limits():
    c, fake = service()
    r = c.post("/investigate", json={**BODY, "max_results": 100, "max_scrape": 20, "threads": 16}, headers=H)
    assert r.status_code == 200
    _, _, preset, depth = fake.calls[0]
    assert preset == "threat_intel"
    assert depth == {"max_results": 50, "max_scrape": 10, "threads": 4}


def test_abuse_material_filter_is_applied_again_to_sources():
    c, _ = service()
    j = c.post("/investigate", json=BODY, headers=H).json()
    assert j["ok"] is True
    links = [s["link"] for s in j["record"]["sources"]]
    assert links == ["http://a.onion/x"]
    assert j["record"]["sources_dropped"] == 1


def test_pipeline_failure_is_reported_not_raised():
    c, _ = service(FakeRobin(fail="Failed to search the dark web: tor down"))
    j = c.post("/investigate", json=BODY, headers=H).json()
    assert j["ok"] is False and j["stage"] == "search"


def test_one_investigation_at_a_time():
    gate = threading.Event()
    c, fake = service(FakeRobin(gate=gate))
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("a", c.post("/investigate", json=BODY, headers=H)))
    t.start()
    for _ in range(100):
        if fake.calls:
            break
        threading.Event().wait(0.02)
    r2 = c.post("/investigate", json=BODY, headers=H)
    gate.set()
    t.join(5)
    assert r2.status_code == 429
    assert out["a"].status_code == 200


def test_service_uses_robins_own_pipeline_unsaved_and_redacted():
    src = (ROOT / "services/robin/app.py").read_text()
    assert "pipeline.run_investigation(" in src and "save=False" in src
    assert "redact_secrets" in src and "search.is_abuse_material" in src


def test_start_refreshes_from_our_mirror_and_falls_back_to_the_image_copy():
    sh = (ROOT / "services/robin/start.sh").read_text()
    assert '"$ROBIN_SRC_URL"' in sh and "import pipeline, search, llm_utils, config" in sh
    assert "ROBIN_CODE=/app/robin ROBIN_CODE_FROM=image" in sh   # fallback first
    assert "ROBIN_CODE_FROM=mirror-at-start" in sh
    assert 'tor --Log' in sh


def test_dockerfile_builds_from_our_mirror_without_patching_robin():
    d = (ROOT / "services/robin/Dockerfile").read_text()
    assert "reeveero-tech/All-hand/releases/download/mirror-robin/robin-src.tar.gz" in d
    assert "apt-get install" in d and " tor " in d
    assert "sed " not in d and "patch " not in d   # Robin's files are not modified
    reqs = [ln.strip() for ln in (ROOT / "services/robin/requirements.txt").read_text().splitlines()
            if ln.strip() and not ln.startswith("#")]
    assert "streamlit" not in reqs and "mcp==1.30.0" not in reqs


# ------------------------------------------------------------ All-hand routes
class FakeCall:
    def __init__(self, answers=None):
        self.answers = answers or {}
        self.seen = []

    def __call__(self, url, secret, method, path, body, timeout):
        self.seen.append((url, secret, method, path, body))
        return self.answers.get(path, (200, {"ok": True}))


def allhand(env=None, call=None, signed=True, live=True, limiter=None):
    r = APIRouter(prefix="/api/v2", route_class=APIRoute)
    events = []

    async def signed_in(request):
        events.append("signed_in")
        if not signed:
            raise HTTPException(401, "sign in")
        return SimpleNamespace(user_id="u1")

    async def run(request, fn, ident=None):
        events.append("run")
        if not live:
            raise HTTPException(401, "session ended")
        return await fn(None, ident)

    call = call or FakeCall()
    env = {"ROBIN_SERVICE_URL": "https://robin.example", "ROBIN_SHARED_SECRET": SECRET} if env is None else env
    robin_api.add_robin_routes(r, run=run, signed_in=signed_in, env=env, call=call, limiter=limiter)
    app = FastAPI()
    app.include_router(r)
    return TestClient(app), call, events


def test_status_is_public_and_says_not_configured():
    c, call, _ = allhand(env={})
    j = c.get("/api/v2/robin/status").json()
    assert j == {"configured": False, "reachable": False} and call.seen == []


def test_status_reports_health_without_sending_the_secret():
    call = FakeCall({"/health": (200, {"configured": True, "tor_bootstrapped": True, "busy": False})})
    c, call, _ = allhand(call=call)
    j = c.get("/api/v2/robin/status").json()
    assert j["reachable"] and j["tor_bootstrapped"] and j["service_configured"]
    assert call.seen[0][1] is None


@pytest.mark.parametrize("env", [{"ROBIN_SERVICE_URL": "http://robin.example", "ROBIN_SHARED_SECRET": SECRET},
                                 {"ROBIN_SERVICE_URL": "https://robin.example", "ROBIN_SHARED_SECRET": "x"}])
def test_bad_config_is_refused(env):
    assert robin_api.robin_config(env) is None
    c, call, _ = allhand(env=env)
    assert c.post("/api/v2/robin/investigations", json=BODY).status_code == 503
    assert call.seen == []


def test_signed_out_is_401_before_anything():
    c, call, _ = allhand(signed=False)
    assert c.post("/api/v2/robin/investigations", json=BODY).status_code == 401
    assert c.get("/api/v2/robin/models").status_code == 401
    assert call.seen == []


def test_ended_session_is_401():
    c, call, events = allhand(live=False)
    assert c.post("/api/v2/robin/investigations", json=BODY).status_code == 401
    assert events == ["signed_in", "run"] and call.seen == []


def test_investigation_is_forwarded_with_secret_and_only_known_fields():
    c, call, _ = allhand()
    r = c.post("/api/v2/robin/investigations", json={**BODY, "preset": "threat_intel", "evil": 1})
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    url, secret, method, path, body = call.seen[0]
    assert (url, secret, method, path) == ("https://robin.example", SECRET, "POST", "/investigate")
    assert "evil" not in body and body["query"] == BODY["query"]
    assert SECRET not in r.text


def test_per_user_limit():
    c, call, _ = allhand(limiter=robin_api.UserLimiter(n=2))
    codes = [c.post("/api/v2/robin/investigations", json=BODY).status_code for _ in range(3)]
    assert codes == [200, 200, 429] and len(call.seen) == 2


@pytest.mark.parametrize("st,expect", [(0, 502), (401, 503), (503, 503), (429, 429), (422, 422), (500, 502)])
def test_service_errors_are_mapped(st, expect):
    c, _, _ = allhand(call=FakeCall({"/investigate": (st, {"detail": "x"} if st else None)}))
    assert c.post("/api/v2/robin/investigations", json=BODY).status_code == expect


def test_limiter_window_slides():
    t = [0.0]
    lim = robin_api.UserLimiter(n=1, window=10, clock=lambda: t[0])
    assert lim.allow("a") and not lim.allow("a") and lim.allow("b")
    t[0] = 10.0
    assert lim.allow("a")
