"""R-206 (D-158): the model gateway over HTTP — signed-in, workspace-scoped.

Routes (under ``/api/v2``, same identity / live-session / RLS / no-store path
as the accounts and key routes):

* ``POST /workspaces/{ws}/model-calls`` — one gateway call. Body:
  ``{messages, routes: [{provider, model, key_id?}], max_tokens?, temperature?,
  json_mode?, tools?, tool_choice?}`` (tool format: agos_v2/gateway.py, D-159;
  the answer's ``tool_calls`` are returned, never run here). For each route the caller's OWN active key in that workspace
  (the one named, else their oldest active key for that provider), else the
  platform key if the platform policy allows that route (default: none).
  Each user key is decrypted only when the gateway reaches its route, in its
  own short transaction (``byok.resolve_key``, purpose ``use``) — no key is
  held while waiting on a provider, and a key revoked/deleted before its turn
  is not used. Every call (success or failure) is recorded in
  ``agos.model_calls`` (006: metadata only) and returned with its id.
* ``POST /workspaces/{ws}/model-calls/stream`` — the same call as Server-Sent
  Events (D-160): ``route``, ``delta`` (text), ``done`` (full result + call_id).
  Retries/fallbacks only before the first text; after it a failure is
  ``stream_interrupted``; a client that leaves -> ``client_disconnected``.
  Recorded like every call.
* ``GET /workspaces/{ws}/model-calls`` — the caller's own recent calls.
* ``GET /models`` — the model catalog (OpenRouter's public list, cached).

Failures are answered with HTTP 200 and ``ok: false`` + a reason code when
the gateway itself ran (the call is recorded); 4xx/5xx are for requests the
server refused before calling any model.

Platform policy (``AGOS_PLATFORM_MODELS``): unset/empty = the platform key is
never used (owner decision pending, R-207 budgets); ``free`` = only OpenRouter
models whose catalog price is 0 for input and output; a model the loaded
catalog does not list is refused as ``model_not_found`` (no request sent).
"""
from __future__ import annotations

import asyncio
import collections
import concurrent.futures
import contextvars
import json
import logging
import os
import threading
import time
import weakref
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from agent_civilization.core import safety

from . import byok
from .gateway import (OPENROUTER_MODELS_URL, ChatRequest, Credential, Gateway, InvalidRequest, Result, Route,
                      parse_openrouter_catalog)

logger = logging.getLogger("agos.v2.gateway_api")

MAX_ROUTES = 4
CALL_LIMIT, CALL_WINDOW_S = 20, 60.0       # gateway calls per user per window
CATALOG_TTL_S = 3600.0
CATALOG_MAX_BYTES = 16 * 1024 * 1024
PLATFORM_POLICY_ENV = "AGOS_PLATFORM_MODELS"
PLATFORM_KEY_ENV = {"openrouter": "OPENROUTER_API_KEY"}
CALL_COLS = ("id, key_id, key_source, provider, model, ok, reason, http_status, finish_reason, fallback_used, "
             "attempts, tokens_in, tokens_out, cost_usd, cost_source, provider_request_id, latency_ms, created_at")


class CallLimiter:
    """In-process sliding window: ``limit`` gateway calls per user per window."""

    def __init__(self, limit: int = CALL_LIMIT, window_s: float = CALL_WINDOW_S):
        self.limit, self.window_s = limit, window_s
        self._hits: Dict[str, collections.deque] = {}
        self._lock = threading.Lock()

    def allow(self, user_id: str) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits.setdefault(user_id, collections.deque())
            while q and now - q[0] > self.window_s:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True


class Catalog:
    """OpenRouter's public model list, fetched through the SEC-05 guard and
    cached; prices feed ``catalog_estimate`` and the ``free`` platform policy.
    Unknown (not fetched / failed) = no price, never a guessed one."""

    def __init__(self, url: str = OPENROUTER_MODELS_URL, *, ttl_s: float = CATALOG_TTL_S,
                 opener: Callable = safety.safe_urlopen, allow_private: Optional[bool] = None,
                 timeout_s: float = 20.0):
        self.url, self.ttl_s, self._opener = url, ttl_s, opener
        self._allow_private, self._timeout = allow_private, timeout_s
        self._models: Dict[str, Dict[str, Any]] = {}
        self._at = 0.0
        self.fetched_at: Optional[str] = None
        self.error: Optional[str] = None
        self._lock = threading.Lock()

    def refresh(self) -> None:
        import urllib.request
        req = urllib.request.Request(self.url, headers={"User-Agent": "AGOS-Gateway/1.0",
                                                        "Accept": "application/json"})
        try:
            with self._opener(req, timeout=self._timeout, allow_private=self._allow_private) as resp:
                if resp.geturl() != self.url or int(getattr(resp, "status", 0) or resp.getcode()) != 200:
                    raise ValueError("unexpected answer")
                raw = resp.read(CATALOG_MAX_BYTES + 1)
            if len(raw) > CATALOG_MAX_BYTES:
                raise ValueError("too large")
            at = datetime.now(timezone.utc).isoformat()
            models = parse_openrouter_catalog(json.loads(raw), fetched_at=at)
        except Exception as exc:  # noqa: BLE001 - the catalog is optional; keep the last good copy
            with self._lock:
                self.error, self._at = type(exc).__name__, time.monotonic()
            logger.warning("gateway catalog: fetch failed (%s)", type(exc).__name__)
            return
        with self._lock:
            self._models, self.fetched_at, self.error, self._at = models, at, None, time.monotonic()

    def models(self) -> Dict[str, Dict[str, Any]]:
        ttl = 60.0 if self.error else self.ttl_s        # a failed fetch is retried sooner
        if not self._at or time.monotonic() - self._at > ttl:
            self.refresh()
        return self._models

    def price_for(self, provider: str, model: str):
        if provider != "openrouter":
            return None
        m = self.models().get(model)
        if not m or m["price_in_per_token"] is None or m["price_out_per_token"] is None:
            return None
        return m["price_in_per_token"], m["price_out_per_token"]

    def capability_for(self, provider: str, model: str):
        """What the catalog knows about this model (tools, structured output), or None = unknown."""
        if provider != "openrouter":
            return None
        m = self.models().get(model)
        return {"tools": m["tools"], "structured_output": m["structured_output"]} if m else None

    def has_model(self, provider: str, model: str) -> Optional[bool]:
        """True/False when a loaded catalog can say; None = unknown (not OpenRouter,
        or no good copy of the list) — never a guess."""
        if provider != "openrouter":
            return None
        models = self.models()
        if not models:
            return None
        return model in models

    def is_free(self, provider: str, model: str) -> bool:
        if provider != "openrouter":
            return False
        m = self.models().get(model)
        return bool(m and m["free"])


def platform_policy_from_env(catalog: Catalog, env: Optional[Dict[str, str]] = None) -> Callable[[Route], bool]:
    value = ((os.environ if env is None else env).get(PLATFORM_POLICY_ENV) or "").strip().lower()
    if value == "free":
        def free(route: Route):
            if catalog.has_model(route.provider, route.model) is False:
                return "model_not_found"               # M10/028 finding 3: say so, not "not allowed"
            return catalog.is_free(route.provider, route.model)
        return free
    if value:
        logger.error("gateway: unknown %s value; the platform key stays off", PLATFORM_POLICY_ENV)
    return lambda route: False


def build_gateway(env: Optional[Dict[str, str]] = None, catalog: Optional[Catalog] = None) -> Gateway:
    env = os.environ if env is None else env          # only the named variables are read
    catalog = catalog or Catalog()
    keys = {p: (env.get(name) or "").strip() for p, name in PLATFORM_KEY_ENV.items()}
    gw = Gateway(platform_keys={p: k for p, k in keys.items() if k},
                 platform_policy=platform_policy_from_env(catalog, env), price_for=catalog.price_for,
                 capability_for=catalog.capability_for)
    gw.catalog = catalog  # type: ignore[attr-defined]
    return gw


def _iso(row: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            v = v.isoformat()
        elif isinstance(v, Decimal):
            v = str(v)
        elif k in ("id", "key_id") and v is not None:
            v = str(v)
        out[k] = v
    return out


def _parse_call(body: Any):
    if not isinstance(body, dict):
        raise InvalidRequest("body must be a JSON object")
    extra = set(body) - {"messages", "routes", "max_tokens", "temperature", "json_mode", "tools", "tool_choice"}
    if extra:
        raise InvalidRequest("unknown field(s): " + ", ".join(sorted(extra)))
    kw = {k: body[k] for k in ("max_tokens", "temperature", "json_mode", "tools", "tool_choice") if k in body}
    if "json_mode" in kw and not isinstance(kw["json_mode"], bool):
        raise InvalidRequest("json_mode must be true or false")
    req = ChatRequest(messages=body.get("messages"), **kw)
    raw = body.get("routes")
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_ROUTES:
        raise InvalidRequest(f"routes must be a list of 1..{MAX_ROUTES} items")
    routes, key_ids = [], []
    for item in raw:
        if not isinstance(item, dict) or not {"provider", "model"} <= set(item) \
                or set(item) - {"provider", "model", "key_id"}:
            raise InvalidRequest("each route is {provider, model, key_id?}")
        routes.append(Route(provider=item["provider"], model=item["model"]))
        kid = item.get("key_id")
        if kid is not None and not isinstance(kid, str):
            raise InvalidRequest("key_id must be a key id")
        key_ids.append(kid)
    return req, routes, key_ids


_END = object()
_BACKGROUND: set = set()          # strong refs: records finished after a client left
MAX_STREAMS = 16                  # open streams per server process (D-160)
# Each open stream waits on its provider in a worker thread; its own pool keeps
# the default pool (plain calls, catalog, key lookups) free, and a full pool is
# an honest 503 before any provider is called — never a silent queue.
_STREAM_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=MAX_STREAMS, thread_name_prefix="agos-stream")
_STREAM_SLOTS = threading.BoundedSemaphore(MAX_STREAMS)


class _Slot:
    """One open-stream slot, released exactly once."""

    def __init__(self):
        self._lock, self._held, self.started = threading.Lock(), True, False

    def release(self) -> None:
        with self._lock:
            if self._held:
                self._held = False
                _STREAM_SLOTS.release()

    def release_if_unstarted(self) -> None:
        if not self.started:
            self.release()


def _sse(event: str, data: Dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode()


async def _sse_body(stream, finish, slot):
    """Stream events from the (blocking) gateway stream; record the call at the end.
    If the client leaves, the provider stream is stopped and the call is still
    recorded (``client_disconnected``) by a background task — never lost."""
    slot.started = True               # from here the body itself releases the slot
    fut = None
    handed_off = False
    try:
        while True:
            ctx = contextvars.copy_context()
            fut = asyncio.get_running_loop().run_in_executor(_STREAM_POOL, ctx.run, next, stream, _END)
            try:
                ev = await asyncio.shield(fut)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - e.g. the key lookup failed mid-call
                logger.error("gateway stream: stopped by %s", type(exc).__name__)
                ev = _END
                if stream.result is None:
                    stream.result = Result(ok=False, reason="gateway_error", attempts=stream.attempts)
            fut = None
            if ev is _END:
                break
            yield _sse(ev["type"], ev)
        handed_off = True
        result = stream.result
        record = asyncio.ensure_future(finish(result))
        _BACKGROUND.add(record)              # kept alive even if the client leaves now
        record.add_done_callback(_BACKGROUND.discard)
        try:
            call_id = await asyncio.shield(record)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - say so honestly; never the body
            logger.error("gateway stream: call not recorded (%s)", type(exc).__name__)
            call_id = None
            yield _sse("error", {"type": "error", "reason": "call_not_recorded"})
        out = result.to_dict()
        out["type"], out["call_id"] = "done", call_id
        yield _sse("done", out)
    finally:
        if handed_off:
            slot.release()
        else:
            stream.cancel()
            task = asyncio.get_running_loop().create_task(_after_disconnect(stream, fut, finish, slot))
            _BACKGROUND.add(task)
            task.add_done_callback(_BACKGROUND.discard)


def _stream_body(stream, finish):
    """The SSE body holding one acquired slot. The slot is released when the body
    ends (after a disconnect: when the call is recorded); a body never started (the
    client left first) sent nothing to any provider — the stream is lazy — and its
    slot is released when it is collected."""
    slot = _Slot()
    body = _sse_body(stream, finish, slot)
    weakref.finalize(body, slot.release_if_unstarted)
    return body


async def _after_disconnect(stream, fut, finish, slot) -> None:
    try:
        if fut is not None:
            try:
                await fut                          # the worker thread sees the cancel at its next line
            except Exception:  # noqa: BLE001 - the stream records its own reason
                pass
        await asyncio.to_thread(stream.close)
        await finish(stream.result)
    except Exception as exc:  # noqa: BLE001
        logger.error("gateway stream: call not recorded after disconnect (%s)", type(exc).__name__)
    finally:
        slot.release()


def add_gateway_routes(r, *, svc, run, signed_in, fetchall, fetchone, uuid_of) -> None:
    """Mount the gateway routes on the v2 accounts router (see add_byok_routes)."""
    b = APIRouter(prefix="/api/v2", tags=["v2 model gateway"], route_class=r.route_class)
    if r.prefix != b.prefix:
        raise RuntimeError("the gateway routes must live under the accounts router prefix")

    def gateway_of(s) -> Gateway:
        gw = getattr(s, "gateway", None)
        if gw is None:
            raise HTTPException(503, "model gateway is not configured on this server")
        return gw

    @b.get("/models")
    async def list_models(request: Request):
        await signed_in(request)
        gw = gateway_of(svc())
        cat: Catalog = getattr(gw, "catalog", None)
        if cat is None:
            return {"source": None, "fetched_at": None, "models": []}
        models = await asyncio.to_thread(cat.models)
        return {"source": cat.url, "fetched_at": cat.fetched_at, "error": cat.error,
                "models": [_iso(dict(m)) for m in models.values()]}

    @b.get("/workspaces/{workspace_id}/model-calls")
    async def list_calls(request: Request, workspace_id: str):
        ident0 = await signed_in(request)
        ws = uuid_of(workspace_id, "workspace")

        async def q(conn, ident):
            if (await fetchone(conn, "select agos.member_role(%s) as r", (ws,)))["r"] is None:
                raise HTTPException(404, "workspace not found")
            rows = await fetchall(conn, f"select {CALL_COLS} from agos.model_calls where workspace_id = %s "
                                        "order by created_at desc, id limit 100", (ws,))
            return [_iso(x) for x in rows]
        return await run(request, q, ident=ident0)

    async def prepare(request: Request, workspace_id: str):
        """Every check that can refuse a call, BEFORE any provider is called (4xx/503)."""
        ident0 = await signed_in(request)  # 401 first, before any other answer
        ws = uuid_of(workspace_id, "workspace")
        s = svc()
        gw = gateway_of(s)
        vault = getattr(s, "vault", None)
        try:
            raw = await request.json()
        except Exception:  # noqa: BLE001 - never echo the body
            raise HTTPException(422, "body must be JSON") from None
        try:
            req, routes, key_ids = _parse_call(raw)
        except InvalidRequest as e:
            raise HTTPException(422, str(e)) from None
        del raw
        limiter: CallLimiter = s.gateway_limiter

        # 1) membership + which of the caller's keys each provider will use (metadata only)
        async def plan(conn, ident):
            if (await fetchone(conn, "select agos.member_role(%s) as r", (ws,)))["r"] is None:
                raise HTTPException(404, "workspace not found")
            # no call log (migration 006 not applied) -> refuse BEFORE any provider is
            # called: a call that cannot be recorded must not be made (cost, honesty)
            if not (await fetchone(conn, "select exists (select 1 from pg_proc p join pg_namespace n "
                                         "on n.oid = p.pronamespace where n.nspname = 'agos' "
                                         "and p.proname = 'model_call_record') as ok", ()))["ok"]:
                raise HTTPException(503, "model call log is not installed on this server (migration 006)")
            if not limiter.allow(ident.user_id):
                raise HTTPException(429, "too many model calls; wait a minute", headers={"Retry-After": "60"})
            chosen: Dict[str, Optional[str]] = {}
            for route, kid in zip(routes, key_ids):
                if kid is not None:
                    kid = uuid_of(kid, "key")
                    row = await fetchone(conn, "select id, provider, status from agos.provider_keys "
                                               "where id = %s and workspace_id = %s", (kid, ws))
                    if row is None:
                        raise HTTPException(404, "key not found")
                    if row["provider"] != route.provider:
                        raise HTTPException(422, "key_id belongs to another provider")
                    if row["status"] != "active":
                        raise HTTPException(410, "key is revoked")
                else:
                    row = await fetchone(conn, "select id from agos.provider_keys where workspace_id = %s "
                                               "and provider = %s and status = 'active' "
                                               "order by created_at, id limit 1", (ws, route.provider))
                    kid = str(row["id"]) if row else None
                if route.provider in chosen and chosen[route.provider] != kid:
                    raise HTTPException(422, "one key per provider per call")
                chosen[route.provider] = kid
            return chosen

        chosen = await run(request, plan, ident=ident0)
        if any(chosen.values()) and vault is None:
            raise HTTPException(503, f"key vault is not configured on this server ({byok.KEK_ENV})")
        loop = asyncio.get_running_loop()

        # 2) the gateway (blocking network) in a worker thread; each user key is
        #    decrypted only when its route is reached, in its own transaction
        def user_credential(provider: str) -> Optional[Credential]:
            kid = chosen.get(provider)
            if kid is None:
                return None

            async def resolve(conn, ident):
                p, base_url, secret = await byok.resolve_key(conn, vault, kid, "use")
                return Credential(provider=p.id, secret=secret, source="byok", key_id=kid, base_url=base_url)
            try:
                return asyncio.run_coroutine_threadsafe(run(request, resolve, ident=ident0), loop).result(30)
            except HTTPException as e:
                if e.status_code in (404, 410):       # revoked/deleted since the plan: not used
                    return None
                raise

        return ident0, ws, gw, req, routes, chosen, user_credential

    def recorder(request: Request, ident0, ws, chosen):
        async def finish(result) -> str:
            """The record (metadata only) — success and failure alike."""
            last = result.attempts[-1] if result.attempts else {}

            async def record(conn, ident):
                row = await fetchone(conn, "select agos.model_call_record(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,"
                                           "%s,%s,%s,%s,%s,%s) as id",
                                     (ws, result.key_id, result.key_source if result.ok else last.get("key_source"),
                                      result.provider or last.get("provider"), result.model or last.get("model"),
                                      result.ok, result.reason, result.http_status, result.finish_reason,
                                      result.fallback_used, json.dumps(result.attempts), result.tokens_in,
                                      result.tokens_out, result.cost_usd, result.cost_source,
                                      result.provider_request_id, result.latency_ms))
                return str(row["id"])

            if not result.ok and last.get("key_source") == "byok":
                # a failed byok call names no answering key; record it as the key that was tried
                result.key_id = chosen.get(last.get("provider"))
            return await run(request, record, ident=ident0)
        return finish

    @b.post("/workspaces/{workspace_id}/model-calls")
    async def model_call(request: Request, workspace_id: str):
        ident0, ws, gw, req, routes, chosen, user_credential = await prepare(request, workspace_id)
        try:
            result = await asyncio.to_thread(gw.complete, req, routes, user_credential)
        except HTTPException:
            raise
        except InvalidRequest as e:
            raise HTTPException(422, str(e)) from None
        del req
        call_id = await recorder(request, ident0, ws, chosen)(result)
        out = result.to_dict()
        out["call_id"] = call_id
        return out

    @b.post("/workspaces/{workspace_id}/model-calls/stream")
    async def model_call_stream(request: Request, workspace_id: str):
        """The same call, answered as Server-Sent Events (D-160): ``route`` (which
        provider/model is streaming; a later one after a fallback), ``delta``
        (text pieces), then ``done`` (the full result + call_id, as the plain
        route answers). Refusals before any model call are plain HTTP errors."""
        ident0, ws, gw, req, routes, chosen, user_credential = await prepare(request, workspace_id)
        try:
            stream = gw.stream(req, routes, user_credential)
        except InvalidRequest as e:
            raise HTTPException(422, str(e)) from None
        del req
        if not _STREAM_SLOTS.acquire(blocking=False):
            raise HTTPException(503, "too many open streams on this server; retry shortly",
                                headers={"Retry-After": "5"})
        body = _stream_body(stream, recorder(request, ident0, ws, chosen))
        return StreamingResponse(body, media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    r.routes.extend(b.routes)
