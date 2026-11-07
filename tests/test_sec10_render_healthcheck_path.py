"""SEC-10 / D-138 (owner-gated): Render's health check must probe the storage-aware
readiness endpoint, not /api/system/status.

Evidence: docs/audit/M8/prep/SEC10_RENDER_HEALTHCHECK.md and raw log 378 —
/api/system/status answers 200 {"status":"healthy"} while the store is down
(get_tasks swallows the error), is rate limited and reads up to 500 rows per
probe; /health/ready answers 503 within <=1 s for the whole outage.

These tests are static (no Render call, no network). They are RED on main
(healthCheckPath: /api/system/status) and GREEN with this change.
"""

import inspect
import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _render_service():
    doc = yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))
    (svc,) = [s for s in doc["services"] if s.get("name") == "agos-civilization"]
    return svc


def test_render_probes_storage_aware_readiness():
    assert _render_service()["healthCheckPath"] == "/health/ready"


def test_readiness_route_exists_as_get():
    import server

    routes = {(r.path, m) for r in server.app.routes for m in (getattr(r, "methods", None) or ())}
    assert ("/health/ready", "GET") in routes


def test_readiness_probe_is_not_rate_limited():
    """A shared per-IP limiter must never turn the platform's probe into a 429
    (which Render counts as a failure). /api/system/status is @rate_limit."""
    import server

    src = inspect.getsource(server)
    head = src[: src.index("async def readiness_probe")].rsplit("\n\n", 1)[-1]
    assert "@rate_limit" not in head
    status_head = src[: src.index("async def system_status")].rsplit("\n\n", 1)[-1]
    assert "@rate_limit" in status_head  # the reason the old path was unsuitable


def test_pin_key_precondition_is_documented_next_to_the_switch():
    """Restarts triggered by the probe regenerate the TMPDIR PIN key unless the
    owner sets AGOS_PIN_FINGERPRINT_KEY first — the precondition must travel
    with the setting."""
    text = (ROOT / "render.yaml").read_text(encoding="utf-8")
    i = text.index("healthCheckPath")
    assert "AGOS_PIN_FINGERPRINT_KEY" in text[max(0, i - 1200): i]
