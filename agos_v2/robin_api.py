"""R-206e (D-165): Robin (dark-web OSINT) inside All-hand, for signed-in users.

Robin runs as its own service (services/robin, built from our full copy of
apurvsinghgautam/robin). All-hand never runs Tor or Robin itself; these
routes forward to that service with a shared secret:

* ``GET  /api/v2/robin/status`` — public: is the service configured and
  reachable, did Tor bootstrap (no secret, no user data).
* ``GET  /api/v2/robin/models`` — signed-in + live session: the models the
  service can use (set by the owner on the service), allowed presets, limits.
* ``POST /api/v2/robin/investigations`` — signed-in + live session, at most
  ``PER_USER_PER_HOUR`` per user: one investigation. Body
  ``{query, model, preset?, max_results?, max_scrape?, threads?}``.

Configuration (All-hand side): ``ROBIN_SERVICE_URL`` (https://...) and
``ROBIN_SHARED_SECRET`` (>= 32 characters, the same value as on the service).
Missing either -> 503 "not configured" (fail closed). The secret is only sent
to the service, never logged or returned. Every answer is ``no-store``.
"""
from __future__ import annotations

import asyncio
import collections
import json
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Optional, Tuple

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

PER_USER_PER_HOUR = 5
STATUS_TIMEOUT = 8
MODELS_TIMEOUT = 30
INVESTIGATE_TIMEOUT = 900
NO_STORE = {"Cache-Control": "no-store"}


class UserLimiter:
    """At most ``n`` calls per user in a sliding hour (in memory, per process)."""

    def __init__(self, n: int = PER_USER_PER_HOUR, window: float = 3600.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.n, self.window, self.clock = n, window, clock
        self._hits: Dict[str, collections.deque] = {}
        self._lock = threading.Lock()

    def allow(self, user_id: str) -> bool:
        now = self.clock()
        with self._lock:
            q = self._hits.setdefault(str(user_id), collections.deque())
            while q and now - q[0] >= self.window:
                q.popleft()
            if len(q) >= self.n:
                return False
            q.append(now)
            return True


def robin_config(env: Optional[Dict[str, str]] = None) -> Optional[Tuple[str, str]]:
    env = os.environ if env is None else env
    url = (env.get("ROBIN_SERVICE_URL") or "").strip().rstrip("/")
    secret = env.get("ROBIN_SHARED_SECRET") or ""
    if not url.startswith("https://") or len(secret) < 32:
        return None
    return url, secret


def _call(url: str, secret: Optional[str], method: str, path: str,
          body: Optional[Dict[str, Any]], timeout: float) -> Tuple[int, Any]:
    """One request to the Robin service -> (status, json). Network errors -> (0, None)."""
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url + path, data=data, method=method)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if secret:
        req.add_header("X-Robin-Token", secret)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - https only (robin_config)
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"null")
        except Exception:  # noqa: BLE001
            return e.code, None
    except Exception:  # noqa: BLE001 - timeout, DNS, TLS: the service is unreachable
        return 0, None


def _detail(payload: Any) -> str:
    if isinstance(payload, dict) and isinstance(payload.get("detail"), str):
        return payload["detail"][:300]
    return ""


def add_robin_routes(r, *, run, signed_in, env: Optional[Dict[str, str]] = None,
                     call: Callable = _call, limiter: Optional[UserLimiter] = None) -> None:
    """Mount the Robin routes on the v2 accounts router (see add_gateway_routes)."""
    b = APIRouter(prefix="/api/v2", tags=["v2 robin"], route_class=r.route_class)
    if r.prefix != b.prefix:
        raise RuntimeError("the robin routes must live under the accounts router prefix")
    lim = limiter or UserLimiter()

    def conf() -> Tuple[str, str]:
        c = robin_config(env)
        if c is None:
            raise HTTPException(503, "Robin service is not configured on this server")
        return c

    async def live(request: Request):
        ident = await signed_in(request)  # 401 first

        async def q(conn, ident):
            return ident
        return await run(request, q, ident=ident)  # live-session check

    def relay(status: int, payload: Any) -> JSONResponse:
        if status == 0:
            raise HTTPException(502, "Robin service unreachable")
        if status == 429:
            return JSONResponse({"detail": _detail(payload) or "busy"}, status_code=429,
                                headers={"Retry-After": "60", **NO_STORE})
        if status in (401, 503):
            # our secret refused, or the service has none: an operator problem
            raise HTTPException(503, "Robin service is not configured correctly")
        if status >= 400:
            raise HTTPException(502 if status >= 500 else status, _detail(payload) or "Robin service error")
        return JSONResponse(payload, headers=NO_STORE)

    @b.get("/robin/status")
    async def status():
        c = robin_config(env)
        if c is None:
            return JSONResponse({"configured": False, "reachable": False}, headers=NO_STORE)
        st, payload = await asyncio.to_thread(call, c[0], None, "GET", "/health", None, STATUS_TIMEOUT)
        h = payload if (st == 200 and isinstance(payload, dict)) else {}
        return JSONResponse({"configured": True, "reachable": st == 200,
                             "service_configured": bool(h.get("configured")),
                             "tor_bootstrapped": bool(h.get("tor_bootstrapped")),
                             "busy": bool(h.get("busy")),
                             "robin_src_sha256": h.get("robin_src_sha256")}, headers=NO_STORE)

    @b.get("/robin/models")
    async def models(request: Request):
        await live(request)
        url, secret = conf()
        return relay(*await asyncio.to_thread(call, url, secret, "GET", "/models", None, MODELS_TIMEOUT))

    @b.post("/robin/investigations")
    async def investigate(request: Request):
        ident = await live(request)
        url, secret = conf()
        try:
            raw = await request.json()
        except Exception:  # noqa: BLE001 - never echo the body
            raise HTTPException(422, "body must be JSON") from None
        if not isinstance(raw, dict):
            raise HTTPException(422, "body must be a JSON object")
        body = {k: raw[k] for k in ("query", "model", "preset", "max_results", "max_scrape", "threads")
                if k in raw}
        if not lim.allow(ident.user_id):
            raise HTTPException(429, f"at most {lim.n} investigations per hour",
                                headers={"Retry-After": "600"})
        return relay(*await asyncio.to_thread(call, url, secret, "POST", "/investigate", body,
                                              INVESTIGATE_TIMEOUT))

    r.routes.extend(b.routes)
