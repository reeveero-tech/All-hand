"""R-206 (D-157): the unified model gateway — one layer every agent calls.

One request shape (``ChatRequest``) for every provider of R-205:

* OpenAI-compatible wire format (``POST {base}/chat/completions``) for
  openai, openrouter, groq, mistral, google (Gemini's OpenAI-compatible
  endpoint) and ollama (the user's own base URL + ``/v1``);
* Anthropic's native Messages API (``POST {base}/messages``).

Rules (each pinned by tests/test_r206_gateway.py):

* **Key choice**: for each route the caller's own BYOK key first; the
  platform key only if one is configured for that provider AND the platform
  policy allows that route (R-207 adds quotas). No key = ``no_key`` with no
  request sent.
* **Bounded retries**: only 429, 5xx, timeout and unreachable are retried,
  at most ``MAX_RETRIES`` times per route, with backoff; ``Retry-After`` is
  honoured but capped at ``RETRY_AFTER_CAP_S``. One overall deadline per call.
* **Fallbacks** only across the routes the caller listed, in order. The
  result names the provider/model that really answered, sets
  ``fallback_used`` and lists every attempt (provider, model, key source,
  reason, HTTP status, retries, latency) — nothing is replaced silently.
* **Failure is failure**: ``ok=False`` and ``text=None``; the gateway never
  invents an answer.
* **Tokens and cost per request**: the provider's reported cost first
  (OpenRouter ``usage.cost``), else an estimate from the catalog price marked
  ``catalog_estimate``, else ``None`` — unknown, never 0.
* **Secrets**: the key is sent only in that provider's auth header, never
  forwarded on a redirect (an "unredirected" header; urllib re-sends a
  redirected POST as a bare GET without it or the body, and the gateway
  refuses that answer as ``unexpected_redirect``), never in a result, a
  repr, an exception or a log line. A provider's error body is never read
  (some echo the key); the result carries a reason code and the status only.
  Prompts and answers are not logged.
* **Outbound policy**: every connection goes through the SEC-05 guard
  (``safety.safe_urlopen``): private/loopback addresses — a local Ollama URL
  seen from the cloud server — are ``blocked_address`` unless the operator
  allowed private networks.
"""
from __future__ import annotations

import http.client
import json
import logging
import socket
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from agent_civilization.core import safety

logger = logging.getLogger("agos.v2.gateway")

MAX_RETRIES = 2                 # per route, on retryable failures only
RETRY_AFTER_CAP_S = 10.0
BACKOFF_S = (0.5, 1.5)
TIMEOUT_S = 60.0                # per HTTP request
DEADLINE_S = 120.0              # per gateway call, all routes and retries
MAX_BODY = 4 * 1024 * 1024
MAX_TOKENS_LIMIT = 32768
MAX_MESSAGES = 200
MAX_CONTENT_CHARS = 400_000
ROLES = ("system", "user", "assistant")
ANTHROPIC_VERSION = "2023-06-01"

# provider id -> (wire format, default base URL; None = the user's base URL)
ADAPTERS: Dict[str, Tuple[str, Optional[str]]] = {
    "openai": ("openai", "https://api.openai.com/v1"),
    "openrouter": ("openai", "https://openrouter.ai/api/v1"),
    "groq": ("openai", "https://api.groq.com/openai/v1"),
    "mistral": ("openai", "https://api.mistral.ai/v1"),
    "google": ("openai", "https://generativelanguage.googleapis.com/v1beta/openai"),
    "ollama": ("openai", None),
    "anthropic": ("anthropic", "https://api.anthropic.com/v1"),
}

RETRYABLE = frozenset({"rate_limited", "provider_unavailable", "timeout", "unreachable"})
REASONS = ("ok", "no_key", "platform_key_not_allowed", "invalid_key", "forbidden", "bad_request",
           "model_or_endpoint_not_found", "rate_limited", "provider_unavailable", "unexpected_status",
           "unexpected_redirect", "timeout", "unreachable", "blocked_address", "tls_error",
           "malformed_response", "deadline_exceeded", "gateway_error")


class InvalidRequest(ValueError):
    """A request the gateway refuses before any network call (message is safe to show)."""


# ------------------------------------------------------------------ inputs
@dataclass(frozen=True)
class ChatRequest:
    messages: List[Dict[str, str]]
    max_tokens: int = 1024
    temperature: Optional[float] = None
    json_mode: bool = False

    def __post_init__(self):
        if not isinstance(self.messages, list) or not 1 <= len(self.messages) <= MAX_MESSAGES:
            raise InvalidRequest(f"messages must be a list of 1..{MAX_MESSAGES} items")
        total = 0
        for m in self.messages:
            if not isinstance(m, dict) or set(m) != {"role", "content"}:
                raise InvalidRequest("each message is exactly {role, content}")
            if m["role"] not in ROLES:
                raise InvalidRequest(f"role must be one of {', '.join(ROLES)}")
            if not isinstance(m["content"], str):
                raise InvalidRequest("content must be text")
            total += len(m["content"])
        if total > MAX_CONTENT_CHARS:
            raise InvalidRequest("messages are too long")
        if isinstance(self.max_tokens, bool) or not isinstance(self.max_tokens, int) \
                or not 1 <= self.max_tokens <= MAX_TOKENS_LIMIT:
            raise InvalidRequest(f"max_tokens must be 1..{MAX_TOKENS_LIMIT}")
        if self.temperature is not None and (isinstance(self.temperature, bool)
                                             or not isinstance(self.temperature, (int, float))
                                             or not 0 <= self.temperature <= 2):
            raise InvalidRequest("temperature must be 0..2")


@dataclass(frozen=True)
class Route:
    provider: str
    model: str

    def __post_init__(self):
        if self.provider not in ADAPTERS:
            raise InvalidRequest(f"unknown provider: {self.provider}")
        if not isinstance(self.model, str) or not 1 <= len(self.model) <= 200 or self.model.strip() != self.model:
            raise InvalidRequest("model must be a non-empty id")


@dataclass(frozen=True)
class Credential:
    provider: str
    secret: Optional[str] = field(repr=False)
    source: str                                   # byok | platform
    key_id: Optional[str] = None
    base_url: Optional[str] = None

    def __repr__(self) -> str:  # never the key
        return f"Credential(provider={self.provider!r}, source={self.source!r}, key_id={self.key_id!r})"

    __str__ = __repr__


# ------------------------------------------------------------------ result
@dataclass
class Result:
    ok: bool
    text: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    key_source: Optional[str] = None
    key_id: Optional[str] = None
    reason: str = "gateway_error"
    http_status: Optional[int] = None
    finish_reason: Optional[str] = None
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    cost_usd: Optional[Decimal] = None
    cost_source: Optional[str] = None             # provider | catalog_estimate | None
    provider_request_id: Optional[str] = None
    latency_ms: Optional[int] = None
    fallback_used: bool = False
    attempts: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = dict(self.__dict__)
        d["cost_usd"] = None if self.cost_usd is None else str(self.cost_usd)
        d["attempts"] = [dict(a) for a in self.attempts]
        return d


class _Fail(Exception):
    def __init__(self, reason: str, status: Optional[int] = None, retry_after: Optional[float] = None):
        super().__init__(reason)
        self.reason, self.status, self.retry_after = reason, status, retry_after


def _reason_for_status(status: int) -> str:
    if status in (401,):
        return "invalid_key"
    if status == 403:
        return "forbidden"
    if status in (400, 422):
        return "bad_request"
    if status == 404:
        return "model_or_endpoint_not_found"
    if status == 429:
        return "rate_limited"
    if 300 <= status < 400:
        return "unexpected_redirect"
    if status >= 500:
        return "provider_unavailable"
    return "unexpected_status"


def _retry_after(headers) -> Optional[float]:
    try:
        v = float((headers or {}).get("Retry-After"))
        return v if v >= 0 else None
    except (TypeError, ValueError):
        return None


def _int(v) -> Optional[int]:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def _dec(v) -> Optional[Decimal]:
    if v is None or isinstance(v, bool):
        return None
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() and d >= 0 else None


# ------------------------------------------------------------------ gateway
class Gateway:
    def __init__(self, *, endpoints: Optional[Mapping[str, str]] = None,
                 platform_keys: Optional[Mapping[str, str]] = None,
                 platform_policy: Optional[Callable[[Route], bool]] = None,
                 price_for: Optional[Callable[[str, str], Optional[Tuple[Decimal, Decimal]]]] = None,
                 allow_private: Optional[bool] = None, timeout_s: float = TIMEOUT_S,
                 deadline_s: float = DEADLINE_S, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic,
                 opener: Callable = safety.safe_urlopen):
        self._endpoints = dict(endpoints or {})
        self._platform = {k: v for k, v in (platform_keys or {}).items() if v}
        self._policy = platform_policy or (lambda route: False)
        self._price_for = price_for or (lambda p, m: None)
        self._allow_private = allow_private
        self._timeout, self._deadline = timeout_s, deadline_s
        self._sleep, self._clock, self._opener = sleep, clock, opener

    def __repr__(self) -> str:  # never the platform keys
        return f"Gateway(platform_providers={sorted(self._platform)!r})"

    def platform_providers(self) -> List[str]:
        return sorted(self._platform)

    # -- credential choice: the user's key first, then the platform key if allowed
    def _credential(self, route: Route, user_credential: Callable[[str], Optional[Credential]]):
        cred = user_credential(route.provider)
        if cred is not None:
            return cred, None
        if route.provider in self._platform:
            if not self._policy(route):
                return None, "platform_key_not_allowed"
            return Credential(provider=route.provider, secret=self._platform[route.provider],
                              source="platform"), None
        return None, "no_key"

    def _url(self, route: Route, cred: Credential) -> str:
        fmt, base = ADAPTERS[route.provider]
        base = self._endpoints.get(route.provider) or base
        if base is None:                                   # ollama: the user's own URL
            if not cred.base_url:
                raise _Fail("bad_request")
            base = cred.base_url.rstrip("/") + "/v1"
        return base.rstrip("/") + ("/messages" if fmt == "anthropic" else "/chat/completions")

    @staticmethod
    def _body(route: Route, req: ChatRequest) -> Dict[str, Any]:
        fmt = ADAPTERS[route.provider][0]
        if fmt == "anthropic":
            system = "\n\n".join(m["content"] for m in req.messages if m["role"] == "system")
            body: Dict[str, Any] = {"model": route.model, "max_tokens": req.max_tokens,
                                    "messages": [m for m in req.messages if m["role"] != "system"]}
            if system:
                body["system"] = system
        else:
            body = {"model": route.model, "messages": req.messages, "max_tokens": req.max_tokens}
            if req.json_mode:
                body["response_format"] = {"type": "json_object"}
            if route.provider == "openrouter":
                body["usage"] = {"include": True}      # OpenRouter then reports the real cost
        if req.temperature is not None:
            body["temperature"] = req.temperature
        return body

    def _post(self, route: Route, cred: Credential, url: str, body: Dict[str, Any]) -> Dict[str, Any]:
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"User-Agent": "AGOS-Gateway/1.0", "Accept": "application/json",
                                              "Content-Type": "application/json"})
        if ADAPTERS[route.provider][0] == "anthropic":
            req.add_header("anthropic-version", ANTHROPIC_VERSION)
            if cred.secret:
                req.add_unredirected_header("x-api-key", cred.secret)
        elif cred.secret:
            req.add_unredirected_header("Authorization", f"Bearer {cred.secret}")
        try:
            with self._opener(req, timeout=self._timeout, allow_private=self._allow_private) as resp:
                status = int(getattr(resp, "status", 0) or resp.getcode())
                if resp.geturl() != url:
                    # urllib followed a 301/302/303 as a GET without the body and
                    # without the (unredirected) key header; the answer is refused.
                    raise _Fail("unexpected_redirect", status)
                if not 200 <= status < 300:
                    raise _Fail(_reason_for_status(status), status)
                raw = resp.read(MAX_BODY + 1)
        except _Fail:
            raise
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            ra = _retry_after(exc.headers)
            exc.close()                                    # the body is never read
            raise _Fail(_reason_for_status(status), status, ra) from None
        except safety.UnsafeURLError:
            raise _Fail("blocked_address") from None
        except (socket.timeout, TimeoutError):
            raise _Fail("timeout") from None
        except urllib.error.URLError as exc:
            inner = exc.reason
            if isinstance(inner, (socket.timeout, TimeoutError)):
                raise _Fail("timeout") from None
            if isinstance(inner, safety.UnsafeURLError):
                raise _Fail("blocked_address") from None
            if isinstance(inner, ssl.SSLError):
                raise _Fail("tls_error") from None
            raise _Fail("unreachable") from None
        except ssl.SSLError:
            raise _Fail("tls_error") from None
        except (OSError, ValueError, http.client.HTTPException):
            raise _Fail("unreachable") from None
        if len(raw) > MAX_BODY:
            raise _Fail("malformed_response", status)
        try:
            data = json.loads(raw)
        except ValueError:
            raise _Fail("malformed_response", status) from None
        if not isinstance(data, dict):
            raise _Fail("malformed_response", status)
        return data

    @staticmethod
    def _parse(route: Route, data: Dict[str, Any]) -> Dict[str, Any]:
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        if ADAPTERS[route.provider][0] == "anthropic":
            blocks = data.get("content")
            if not isinstance(blocks, list):
                raise _Fail("malformed_response")
            text = "".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")
            return {"text": text, "finish_reason": data.get("stop_reason"),
                    "tokens_in": _int(usage.get("input_tokens")), "tokens_out": _int(usage.get("output_tokens")),
                    "cost": None, "id": data.get("id")}
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise _Fail("malformed_response")
        msg = choices[0].get("message")
        if not isinstance(msg, dict) or not isinstance(msg.get("content"), (str, type(None))):
            raise _Fail("malformed_response")
        return {"text": msg.get("content") or "", "finish_reason": choices[0].get("finish_reason"),
                "tokens_in": _int(usage.get("prompt_tokens")), "tokens_out": _int(usage.get("completion_tokens")),
                "cost": _dec(usage.get("cost")), "id": data.get("id")}

    def _cost(self, route: Route, parsed) -> Tuple[Optional[Decimal], Optional[str]]:
        if parsed["cost"] is not None:
            return parsed["cost"], "provider"
        price = self._price_for(route.provider, route.model)
        if price and parsed["tokens_in"] is not None and parsed["tokens_out"] is not None:
            pin, pout = price
            return (pin * parsed["tokens_in"] + pout * parsed["tokens_out"]).normalize(), "catalog_estimate"
        return None, None

    def complete(self, req: ChatRequest, routes: List[Route],
                 user_credential: Callable[[str], Optional[Credential]]) -> Result:
        """Synchronous (blocking network); call through ``asyncio.to_thread`` from async code."""
        if not routes:
            raise InvalidRequest("at least one route is required")
        start = self._clock()
        attempts: List[Dict[str, Any]] = []
        last = Result(ok=False)
        for idx, route in enumerate(routes):
            cred, why = self._credential(route, user_credential)
            att = {"provider": route.provider, "model": route.model,
                   "key_source": cred.source if cred else None, "ok": False, "reason": why,
                   "http_status": None, "retries": 0, "latency_ms": None}
            attempts.append(att)
            if cred is None:
                last = Result(ok=False, reason=why)
                continue
            t0 = time.monotonic()
            for n in range(MAX_RETRIES + 1):
                if self._clock() - start > self._deadline:
                    att["reason"] = "deadline_exceeded"
                    self._log(route, att)
                    return Result(ok=False, reason="deadline_exceeded", attempts=attempts)
                att["retries"] = n
                try:
                    data = self._post(route, cred, self._url(route, cred), self._body(route, req))
                    parsed = self._parse(route, data)
                except _Fail as f:
                    att.update(reason=f.reason, http_status=f.status)
                    if f.reason in RETRYABLE and n < MAX_RETRIES:
                        wait = BACKOFF_S[min(n, len(BACKOFF_S) - 1)]
                        if f.retry_after is not None:
                            wait = min(max(wait, f.retry_after), RETRY_AFTER_CAP_S)
                        self._sleep(wait)
                        continue
                    break
                except Exception as exc:  # noqa: BLE001 - never surface text that may hold input
                    logger.error("gateway: unexpected %s provider=%s", type(exc).__name__, route.provider)
                    att.update(reason="gateway_error")
                    break
                att.update(ok=True, reason="ok", http_status=200,
                           latency_ms=int((time.monotonic() - t0) * 1000))
                self._log(route, att, parsed)
                cost, src = self._cost(route, parsed)
                return Result(ok=True, text=parsed["text"], provider=route.provider, model=route.model,
                              key_source=cred.source, key_id=cred.key_id, reason="ok", http_status=200,
                              finish_reason=parsed["finish_reason"], tokens_in=parsed["tokens_in"],
                              tokens_out=parsed["tokens_out"], cost_usd=cost, cost_source=src,
                              provider_request_id=parsed["id"] if isinstance(parsed["id"], str) else None,
                              latency_ms=att["latency_ms"], fallback_used=idx > 0, attempts=attempts)
            att["latency_ms"] = int((time.monotonic() - t0) * 1000)
            self._log(route, att)
            last = Result(ok=False, reason=att["reason"], http_status=att["http_status"])
        last.attempts = attempts
        return last

    @staticmethod
    def _log(route: Route, att: Dict[str, Any], parsed: Optional[Dict[str, Any]] = None) -> None:
        # metadata only: never the key, the prompt or the answer
        logger.info("gateway call provider=%s model=%s source=%s ok=%s reason=%s http=%s retries=%s "
                    "tokens_in=%s tokens_out=%s latency_ms=%s", route.provider, route.model,
                    att["key_source"], att["ok"], att["reason"], att["http_status"], att["retries"],
                    parsed and parsed["tokens_in"], parsed and parsed["tokens_out"], att["latency_ms"])


# ------------------------------------------------------------------ catalog
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"   # public, no key


def parse_openrouter_catalog(doc: Any, *, fetched_at: str) -> Dict[str, Dict[str, Any]]:
    """Model id -> capabilities and per-token USD prices, from OpenRouter's public
    model list. Missing or invalid values stay ``None`` (unknown), never guessed."""
    out: Dict[str, Dict[str, Any]] = {}
    for m in (doc or {}).get("data", []) if isinstance(doc, dict) else []:
        if not isinstance(m, dict) or not isinstance(m.get("id"), str):
            continue
        pricing = m.get("pricing") if isinstance(m.get("pricing"), dict) else {}
        arch = m.get("architecture") if isinstance(m.get("architecture"), dict) else {}
        params = m.get("supported_parameters") if isinstance(m.get("supported_parameters"), list) else []
        mods = arch.get("input_modalities") if isinstance(arch.get("input_modalities"), list) else []
        pin, pout = _dec(pricing.get("prompt")), _dec(pricing.get("completion"))
        out[m["id"]] = {
            "provider": "openrouter", "id": m["id"],
            "context_length": _int(m.get("context_length")),
            "vision": "image" in mods, "tools": "tools" in params,
            "structured_output": "response_format" in params or "structured_outputs" in params,
            "price_in_per_token": pin, "price_out_per_token": pout,
            "free": pin == 0 and pout == 0 if pin is not None and pout is not None else False,
            "source": "openrouter:/api/v1/models", "fetched_at": fetched_at,
        }
    return out
