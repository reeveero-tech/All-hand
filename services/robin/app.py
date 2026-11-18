"""R-206e (D-165): Robin as a separate service, called only by All-hand.

Robin (https://github.com/apurvsinghgautam/robin, MIT) is an OSINT tool: it
refines a query with an LLM, searches dark-web search engines through Tor,
reads the pages, and writes a report. This wrapper runs Robin's OWN pipeline
(``pipeline.run_investigation``) unmodified; Robin's code comes from our full
copy (see Dockerfile). Rules added here, on top of Robin's:

* Every route except ``/health`` needs ``X-Robin-Token`` equal to
  ``ROBIN_SHARED_SECRET`` (constant-time compare). No secret set = 503 for
  everything (fail closed). Only All-hand knows the secret; All-hand lets only
  signed-in users with a live session through (agos_v2/robin_api.py).
* Robin's child-abuse-material filter (``search.is_abuse_material``, applied by
  ``get_search_results_detailed``) is kept, and applied AGAIN to the sources
  returned, so nothing it names can leave this service.
* The ``personal_identity`` preset (investigating a person's exposed data) is
  refused: like FaceCheck.ID (refused under D-165) it would let anyone profile
  a private person. Allowed presets: ALLOWED_PRESETS.
* Depth is capped well below Robin's own limits (free 512 MB service), one
  investigation at a time (429 when busy), nothing is saved to disk
  (``save=False``), and error texts pass through Robin's ``redact_secrets``.
* The LLM key is set by the owner in the service's environment (Robin reads
  OPENROUTER_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY, GOOGLE_API_KEY,
  MISTRAL_API_KEY or CUSTOM_API_*); it never passes through All-hand.
"""
from __future__ import annotations

import hmac
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

ALLOWED_PRESETS = ("threat_intel", "ransomware_malware", "corporate_espionage")
LIMITS = {"max_results": (10, 50), "max_scrape": (3, 10), "threads": (1, 4)}
DEFAULTS = {"max_results": 30, "max_scrape": 6, "threads": 3}
QUERY_MIN, QUERY_MAX = 3, 300
CONTENT_CHARS = 6000


class Robin:
    """The parts of Robin this wrapper calls; tests pass a fake."""

    def __init__(self) -> None:
        import config  # Robin's modules (PYTHONPATH=/app/robin)
        import llm_utils
        import pipeline
        import search
        self.config, self.llm_utils, self.pipeline, self.search = config, llm_utils, pipeline, search

    def cfg(self):
        return self.config.RobinConfig.from_env()

    def models(self) -> List[str]:
        return list(self.llm_utils.get_model_choices(self.cfg()))

    def investigate(self, query: str, model: str, preset: str, depth: Dict[str, int]) -> Dict[str, Any]:
        cfg = self.cfg()
        try:
            inv = self.pipeline.run_investigation(
                cfg, query, model, preset=preset, max_results=depth["max_results"],
                max_scrape=depth["max_scrape"], content_chars=CONTENT_CHARS,
                threads=depth["threads"], save=False)
        except self.pipeline.PipelineError as e:
            return {"ok": False, "stage": e.stage,
                    "error": self.config.redact_secrets(str(e), cfg)[:500]}
        rec = inv.to_record()
        rec["search_stats"] = inv.search_stats
        return {"ok": True, "record": rec}

    def is_abuse_material(self, result: Dict[str, Any]) -> bool:
        return bool(self.search.is_abuse_material(result))

    def tor_ready(self) -> bool:
        return bool(self.search.tor_bootstrapped(os.environ.get("ROBIN_TOR_LOG")))


def _clamp(body: Dict[str, Any], name: str) -> int:
    lo, hi = LIMITS[name]
    v = body.get(name, DEFAULTS[name])
    if not isinstance(v, int) or isinstance(v, bool):
        raise HTTPException(422, f"{name} must be an integer")
    return max(lo, min(hi, v))


def parse_request(body: Any) -> Dict[str, Any]:
    if not isinstance(body, dict):
        raise HTTPException(422, "body must be a JSON object")
    q = body.get("query")
    if not isinstance(q, str) or not (QUERY_MIN <= len(q.strip()) <= QUERY_MAX):
        raise HTTPException(422, f"query must be {QUERY_MIN}-{QUERY_MAX} characters")
    model = body.get("model")
    if not isinstance(model, str) or not model.strip():
        raise HTTPException(422, "model is required")
    preset = body.get("preset", ALLOWED_PRESETS[0])
    if preset not in ALLOWED_PRESETS:
        raise HTTPException(422, "preset not allowed")
    return {"query": q.strip(), "model": model.strip(), "preset": preset,
            "depth": {k: _clamp(body, k) for k in LIMITS}}


def create_app(robin_factory: Callable[[], Robin] = Robin,
               env: Optional[Dict[str, str]] = None) -> FastAPI:
    env = os.environ if env is None else env
    app = FastAPI(title="AGOS Robin service", docs_url=None, redoc_url=None, openapi_url=None)
    busy = threading.Lock()
    state: Dict[str, Any] = {"robin": None, "last": None}

    def robin() -> Robin:
        if state["robin"] is None:
            state["robin"] = robin_factory()
        return state["robin"]

    def check(request: Request) -> None:
        secret = env.get("ROBIN_SHARED_SECRET") or ""
        if len(secret) < 32:
            raise HTTPException(503, "service not configured")
        got = request.headers.get("x-robin-token") or ""
        if not hmac.compare_digest(got.encode(), secret.encode()):
            raise HTTPException(401, "unauthorized")

    @app.get("/health")
    def health():
        try:
            r = robin()
            tor = r.tor_ready()
            loaded = True
        except Exception:  # noqa: BLE001 - health must answer
            tor, loaded = False, False
        src = None
        for path in ("/home/robin/ROBIN_SRC_SHA256", "/app/ROBIN_SRC_SHA256"):
            try:
                with open(path) as f:
                    src = f.read().strip()
                break
            except OSError:
                continue
        return {"ok": loaded, "robin_loaded": loaded, "tor_bootstrapped": tor,
                "configured": len(env.get("ROBIN_SHARED_SECRET") or "") >= 32,
                "busy": busy.locked(), "robin_src_sha256": src,
                "robin_src_url": env.get("ROBIN_SRC_URL"),
                "robin_code_from": env.get("ROBIN_CODE_FROM"), "last": state["last"]}

    @app.get("/models")
    def models(request: Request):
        check(request)
        return {"models": robin().models(), "presets": list(ALLOWED_PRESETS),
                "limits": LIMITS, "defaults": DEFAULTS}

    @app.post("/investigate")
    async def investigate(request: Request):
        check(request)
        try:
            raw = await request.json()
        except Exception:  # noqa: BLE001
            raise HTTPException(422, "body must be JSON") from None
        req = parse_request(raw)
        if not busy.acquire(blocking=False):
            return JSONResponse({"detail": "another investigation is running; retry later"},
                                status_code=429, headers={"Retry-After": "60"})
        t0 = time.time()
        try:
            import asyncio
            r = robin()
            out = await asyncio.to_thread(r.investigate, req["query"], req["model"],
                                          req["preset"], req["depth"])
            if out.get("ok"):
                rec = out["record"]
                kept = [s for s in rec.get("sources") or [] if not r.is_abuse_material(s)]
                rec["sources_dropped"] = len(rec.get("sources") or []) - len(kept)
                rec["sources"] = kept
            out["seconds"] = round(time.time() - t0, 1)
            state["last"] = {"at": int(t0), "ok": bool(out.get("ok")), "seconds": out["seconds"]}
            return out
        finally:
            busy.release()

    return app


app = create_app()
