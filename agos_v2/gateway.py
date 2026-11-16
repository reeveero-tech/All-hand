"""R-206 (D-157): the unified model gateway — one layer every agent calls.

One request shape (``ChatRequest``) for every provider of R-205:

* OpenAI-compatible wire format (``POST {base}/chat/completions``) for
  openai, openrouter, groq, mistral, google (Gemini's OpenAI-compatible
  endpoint) and ollama (the user's own base URL + ``/v1``);
* Anthropic's native Messages API (``POST {base}/messages``).

Tool calls (D-159): one provider-neutral format — ``tools`` [{name,
description?, parameters}], ``tool_choice`` (auto | none | required |
{name}), assistant messages with ``tool_calls`` [{id, name, arguments}] and
``tool`` messages {tool_call_id, content} — mapped to OpenAI ``function``
tools and to Anthropic ``tool_use``/``tool_result`` blocks; the answer's tool
calls come back in ``Result.tool_calls`` in the same neutral shape. The
gateway never runs a tool (R-211 does); arguments that are not a JSON object
are reported (``arguments_valid: false``), never repaired.

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
import re
import socket
import ssl
import threading
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
STREAM_DEADLINE_S = 300.0       # per streamed call (D-160): long answers arrive over time
MAX_SSE_LINE = 1024 * 1024      # one Server-Sent Events line
MAX_BODY = 4 * 1024 * 1024
MAX_TOKENS_LIMIT = 32768
MAX_MESSAGES = 200
MAX_CONTENT_CHARS = 400_000
ROLES = ("system", "user", "assistant", "tool")
MAX_TOOLS = 64
MAX_TOOL_CALLS = 64
MAX_TOOL_SCHEMA_CHARS = 64_000
MAX_TOOL_DESCRIPTION = 1024
TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
TOOL_CHOICES = ("auto", "none", "required")
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
           "malformed_response", "deadline_exceeded", "gateway_error",
           "tools_not_supported", "structured_output_not_supported",
           "stream_interrupted", "client_disconnected")
# OpenAI-compatible providers that report usage in a stream only when asked (D-160).
STREAM_USAGE = frozenset({"openai", "openrouter", "groq", "google", "ollama"})


class InvalidRequest(ValueError):
    """A request the gateway refuses before any network call (message is safe to show)."""


# ------------------------------------------------------------------ inputs
@dataclass(frozen=True)
class ChatRequest:
    messages: List[Dict[str, str]]
    max_tokens: int = 1024
    temperature: Optional[float] = None
    json_mode: bool = False
    tools: Optional[List[Dict[str, Any]]] = None
    tool_choice: Any = None

    def __post_init__(self):
        names = _check_tools(self.tools)
        if self.tool_choice is not None:
            if names is None:
                raise InvalidRequest("tool_choice needs tools")
            tc = self.tool_choice
            if not (tc in TOOL_CHOICES if isinstance(tc, str) else
                    isinstance(tc, dict) and set(tc) == {"name"} and tc["name"] in names):
                raise InvalidRequest("tool_choice must be auto, none, required or {name} of a listed tool")
        if not isinstance(self.messages, list) or not 1 <= len(self.messages) <= MAX_MESSAGES:
            raise InvalidRequest(f"messages must be a list of 1..{MAX_MESSAGES} items")
        total = 0
        open_calls: set = set()          # tool calls asked for and not yet answered
        seen_ids: set = set()
        for m in self.messages:
            if not isinstance(m, dict) or m.get("role") not in ROLES:
                raise InvalidRequest(f"each message has a role, one of {', '.join(ROLES)}")
            role = m["role"]
            if role == "tool":
                if set(m) != {"role", "tool_call_id", "content"}:
                    raise InvalidRequest("a tool message is exactly {role, tool_call_id, content}")
                if m["tool_call_id"] not in open_calls:
                    raise InvalidRequest("a tool result must answer an earlier, unanswered tool call")
                open_calls.discard(m["tool_call_id"])
            elif role == "assistant" and "tool_calls" in m:
                if set(m) != {"role", "content", "tool_calls"}:
                    raise InvalidRequest("an assistant tool turn is {role, content, tool_calls}")
                if names is None:
                    raise InvalidRequest("a history with tool calls needs tools")
                calls = m["tool_calls"]
                if not isinstance(calls, list) or not 1 <= len(calls) <= MAX_TOOL_CALLS:
                    raise InvalidRequest(f"tool_calls must be a list of 1..{MAX_TOOL_CALLS} items")
                for c in calls:
                    if not isinstance(c, dict) or set(c) != {"id", "name", "arguments"}:
                        raise InvalidRequest("each tool call is exactly {id, name, arguments}")
                    if not isinstance(c["id"], str) or not 1 <= len(c["id"]) <= 200 or c["id"] in seen_ids:
                        raise InvalidRequest("each tool call needs its own id")
                    if c["name"] not in names:
                        raise InvalidRequest("a tool call names a tool that is not listed")
                    if _json_object(c["arguments"]) is None:
                        raise InvalidRequest("tool call arguments must be a JSON object (as text)")
                    seen_ids.add(c["id"])
                    open_calls.add(c["id"])
                    total += len(c["arguments"])
                if m["content"] is not None and not isinstance(m["content"], str):
                    raise InvalidRequest("content must be text")
                total += len(m["content"] or "")
                continue
            elif set(m) != {"role", "content"}:
                raise InvalidRequest("each message is exactly {role, content}")
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


def _json_object(text: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(text, str):
        return None
    try:
        v = json.loads(text)
    except ValueError:
        return None
    return v if isinstance(v, dict) else None


def _check_tools(tools: Any) -> Optional[set]:
    """None = no tools; else the set of tool names (each tool validated)."""
    if tools is None:
        return None
    if not isinstance(tools, list) or not 1 <= len(tools) <= MAX_TOOLS:
        raise InvalidRequest(f"tools must be a list of 1..{MAX_TOOLS} items")
    names: set = set()
    for t in tools:
        if not isinstance(t, dict) or "name" not in t or set(t) - {"name", "description", "parameters"}:
            raise InvalidRequest("each tool is {name, description?, parameters?}")
        if not isinstance(t["name"], str) or not TOOL_NAME_RE.match(t["name"]) or t["name"] in names:
            raise InvalidRequest("each tool needs its own name (letters, digits, _ or -, up to 64)")
        if "description" in t and (not isinstance(t["description"], str)
                                   or len(t["description"]) > MAX_TOOL_DESCRIPTION):
            raise InvalidRequest(f"tool description must be text up to {MAX_TOOL_DESCRIPTION}")
        if "parameters" in t:
            p = t["parameters"]
            if not isinstance(p, dict) or p.get("type") != "object":
                raise InvalidRequest("tool parameters must be a JSON schema of type object")
            if len(json.dumps(p)) > MAX_TOOL_SCHEMA_CHARS:
                raise InvalidRequest("tool parameters are too large")
        names.add(t["name"])
    return names


def _params(t: Dict[str, Any]) -> Dict[str, Any]:
    return t.get("parameters") or {"type": "object", "properties": {}}


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
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)   # [{id, name, arguments, arguments_valid}]

    def to_dict(self) -> Dict[str, Any]:
        d = dict(self.__dict__)
        d["cost_usd"] = None if self.cost_usd is None else str(self.cost_usd)
        d["attempts"] = [dict(a) for a in self.attempts]
        d["tool_calls"] = [dict(c) for c in self.tool_calls]
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


def _fail_from(exc: BaseException) -> Optional[_Fail]:
    """The honest reason for a network error (``None`` = not a network error)."""
    if isinstance(exc, _Fail):
        return exc
    if isinstance(exc, urllib.error.HTTPError):
        status = int(exc.code)
        ra = _retry_after(exc.headers)
        exc.close()                                        # the body is never read
        return _Fail(_reason_for_status(status), status, ra)
    if isinstance(exc, safety.UnsafeURLError):
        return _Fail("blocked_address")
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return _Fail("timeout")
    if isinstance(exc, urllib.error.URLError):
        inner = exc.reason
        if isinstance(inner, (socket.timeout, TimeoutError)):
            return _Fail("timeout")
        if isinstance(inner, safety.UnsafeURLError):
            return _Fail("blocked_address")
        if isinstance(inner, ssl.SSLError):
            return _Fail("tls_error")
        return _Fail("unreachable")
    if isinstance(exc, ssl.SSLError):
        return _Fail("tls_error")
    if isinstance(exc, (OSError, ValueError, http.client.HTTPException)):
        return _Fail("unreachable")
    return None


def _call(cid: str, name: str, arguments: str) -> Dict[str, Any]:
    return {"id": cid, "name": name, "arguments": arguments,
            "arguments_valid": _json_object(arguments) is not None}


def _openai_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for m in messages:
        if m["role"] == "assistant" and "tool_calls" in m:
            out.append({"role": "assistant", "content": m["content"], "tool_calls": [
                {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}}
                for c in m["tool_calls"]]})
        else:
            out.append(m)
    return out


def _anthropic_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for m in messages:
        role = m["role"]
        if role == "system":
            continue
        if role == "assistant" and "tool_calls" in m:
            blocks = [{"type": "text", "text": m["content"]}] if m["content"] else []
            blocks += [{"type": "tool_use", "id": c["id"], "name": c["name"], "input": json.loads(c["arguments"])}
                       for c in m["tool_calls"]]
            out.append({"role": "assistant", "content": blocks})
        elif role == "tool":
            block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
            prev = out[-1] if out else None
            if prev and prev["role"] == "user" and isinstance(prev["content"], list) \
                    and all(b.get("type") == "tool_result" for b in prev["content"]):
                prev["content"].append(block)            # consecutive results: one user turn
            else:
                out.append({"role": "user", "content": [block]})
        else:
            out.append(m)
    return out


# ------------------------------------------------------------------ gateway
class Gateway:
    def __init__(self, *, endpoints: Optional[Mapping[str, str]] = None,
                 platform_keys: Optional[Mapping[str, str]] = None,
                 platform_policy: Optional[Callable[[Route], bool]] = None,
                 price_for: Optional[Callable[[str, str], Optional[Tuple[Decimal, Decimal]]]] = None,
                 capability_for: Optional[Callable[[str, str], Optional[Mapping[str, Any]]]] = None,
                 allow_private: Optional[bool] = None, timeout_s: float = TIMEOUT_S,
                 deadline_s: float = DEADLINE_S, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic,
                 opener: Callable = safety.safe_urlopen, stream_deadline_s: float = STREAM_DEADLINE_S):
        self._endpoints = dict(endpoints or {})
        self._platform = {k: v for k, v in (platform_keys or {}).items() if v}
        self._policy = platform_policy or (lambda route: False)
        self._price_for = price_for or (lambda p, m: None)
        self._caps = capability_for or (lambda p, m: None)     # None = unknown -> not refused
        self._allow_private = allow_private
        self._timeout, self._deadline, self._stream_deadline = timeout_s, deadline_s, stream_deadline_s
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
                                    "messages": _anthropic_messages(req.messages)}
            if system:
                body["system"] = system
            if req.tools:
                body["tools"] = [dict({"name": t["name"]}, **({"description": t["description"]}
                                                             if "description" in t else {}),
                                      input_schema=_params(t)) for t in req.tools]
            if req.tool_choice is not None:
                tc = req.tool_choice
                body["tool_choice"] = ({"type": "tool", "name": tc["name"]} if isinstance(tc, dict) else
                                       {"type": {"required": "any"}.get(tc, tc)})
        else:
            body = {"model": route.model, "messages": _openai_messages(req.messages),
                    "max_tokens": req.max_tokens}
            if req.tools:
                body["tools"] = [{"type": "function", "function": dict(
                    {"name": t["name"]}, **({"description": t["description"]} if "description" in t else {}),
                    parameters=_params(t))} for t in req.tools]
            if req.tool_choice is not None:
                tc = req.tool_choice
                body["tool_choice"] = ({"type": "function", "function": {"name": tc["name"]}}
                                       if isinstance(tc, dict) else tc)
            if req.json_mode:
                body["response_format"] = {"type": "json_object"}
            if route.provider == "openrouter":
                body["usage"] = {"include": True}      # OpenRouter then reports the real cost
        if req.temperature is not None:
            body["temperature"] = req.temperature
        return body

    def _open(self, route: Route, cred: Credential, url: str, body: Dict[str, Any],
              accept: str = "application/json"):
        """Send the request; return ``(response, status)`` for a 2xx answer from the
        URL asked for (the caller closes it), else raise ``_Fail``."""
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"User-Agent": "AGOS-Gateway/1.0", "Accept": accept,
                                              "Content-Type": "application/json"})
        if ADAPTERS[route.provider][0] == "anthropic":
            req.add_header("anthropic-version", ANTHROPIC_VERSION)
            if cred.secret:
                req.add_unredirected_header("x-api-key", cred.secret)
        elif cred.secret:
            req.add_unredirected_header("Authorization", f"Bearer {cred.secret}")
        try:
            resp = self._opener(req, timeout=self._timeout, allow_private=self._allow_private)
        except Exception as exc:
            f = _fail_from(exc)
            if f is None:
                raise
            raise f from None
        try:
            status = int(getattr(resp, "status", 0) or resp.getcode())
            if resp.geturl() != url:
                # urllib followed a 301/302/303 as a GET without the body and
                # without the (unredirected) key header; the answer is refused.
                raise _Fail("unexpected_redirect", status)
            if not 200 <= status < 300:
                raise _Fail(_reason_for_status(status), status)
        except BaseException as exc:
            resp.close()
            f = None if isinstance(exc, _Fail) or not isinstance(exc, Exception) else _fail_from(exc)
            if f is None:
                raise
            raise f from None
        return resp, status

    def _post(self, route: Route, cred: Credential, url: str, body: Dict[str, Any]) -> Dict[str, Any]:
        resp, status = self._open(route, cred, url, body)
        try:
            raw = resp.read(MAX_BODY + 1)
        except Exception as exc:
            f = _fail_from(exc)
            if f is None:
                raise
            raise f from None
        finally:
            resp.close()
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
            calls = []
            for b in blocks:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    if not isinstance(b.get("id"), str) or not isinstance(b.get("name"), str) \
                            or not isinstance(b.get("input"), dict):
                        raise _Fail("malformed_response")
                    calls.append(_call(b["id"], b["name"], json.dumps(b["input"], separators=(",", ":"))))
            return {"text": text, "finish_reason": data.get("stop_reason"), "tool_calls": calls,
                    "tokens_in": _int(usage.get("input_tokens")), "tokens_out": _int(usage.get("output_tokens")),
                    "cost": None, "id": data.get("id")}
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise _Fail("malformed_response")
        msg = choices[0].get("message")
        if not isinstance(msg, dict) or not isinstance(msg.get("content"), (str, type(None))):
            raise _Fail("malformed_response")
        calls = []
        raw_calls = msg.get("tool_calls")
        if raw_calls is not None:
            if not isinstance(raw_calls, list):
                raise _Fail("malformed_response")
            for c in raw_calls:
                fn = c.get("function") if isinstance(c, dict) else None
                if not isinstance(c, dict) or not isinstance(c.get("id"), str) or not isinstance(fn, dict) \
                        or not isinstance(fn.get("name"), str):
                    raise _Fail("malformed_response")
                args = fn.get("arguments", "")
                if isinstance(args, dict):
                    args = json.dumps(args, separators=(",", ":"))
                if not isinstance(args, str):
                    raise _Fail("malformed_response")
                calls.append(_call(c["id"], fn["name"], args))
        return {"text": msg.get("content") or "", "finish_reason": choices[0].get("finish_reason"),
                "tool_calls": calls,
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
            missing = self._missing_capability(route, req)
            if missing:
                # the catalog says this model cannot do what the request needs: not called
                att["reason"] = missing
                self._log(route, att)
                last = Result(ok=False, reason=missing)
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
                              latency_ms=att["latency_ms"], fallback_used=idx > 0, attempts=attempts,
                              tool_calls=parsed["tool_calls"])
            att["latency_ms"] = int((time.monotonic() - t0) * 1000)
            self._log(route, att)
            last = Result(ok=False, reason=att["reason"], http_status=att["http_status"])
        last.attempts = attempts
        return last

    # -- streaming (D-160)
    def stream(self, req: ChatRequest, routes: List[Route],
               user_credential: Callable[[str], Optional[Credential]]) -> "Stream":
        """A blocking iterator of ``route``/``delta`` events; ``.result`` after the end.

        Retries and fallbacks only before the first text reached the caller; after
        that any failure is ``stream_interrupted`` (``text=None``, nothing glued on)."""
        if not routes:
            raise InvalidRequest("at least one route is required")
        return Stream(self, req, list(routes), user_credential)

    def _stream_body(self, route: Route, req: ChatRequest) -> Dict[str, Any]:
        body = self._body(route, req)
        body["stream"] = True
        if route.provider in STREAM_USAGE:
            body["stream_options"] = {"include_usage": True}
        return body

    def _run_stream(self, s: "Stream", req: ChatRequest, routes: List[Route],
                    user_credential: Callable[[str], Optional[Credential]]):
        start = self._clock()
        attempts: List[Dict[str, Any]] = []
        s._attempts = attempts
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
            missing = self._missing_capability(route, req)
            if missing:
                att["reason"] = missing
                self._log(route, att)
                last = Result(ok=False, reason=missing)
                continue
            s._route, s._cred, s._att = route, cred, att
            t0 = time.monotonic()
            for n in range(MAX_RETRIES + 1):
                s._check()
                if self._clock() - start > self._stream_deadline:
                    att["reason"] = "deadline_exceeded"
                    self._log(route, att)
                    s.result = Result(ok=False, reason="deadline_exceeded", attempts=attempts)
                    return
                att["retries"] = n
                acc = _AnthropicStream() if ADAPTERS[route.provider][0] == "anthropic" else _OpenAIStream()

                def check():
                    s._check()
                    if self._clock() - start > self._stream_deadline:
                        raise _Fail("deadline_exceeded")
                try:
                    resp, status = self._open(route, cred, self._url(route, cred),
                                              self._stream_body(route, req), accept="text/event-stream")
                    s._resp = resp
                    att["http_status"] = status
                    ctype = (resp.headers.get("Content-Type") or "") if getattr(resp, "headers", None) else ""
                    if not ctype.lower().startswith("text/event-stream"):
                        raise _Fail("malformed_response", status)
                    yield {"type": "route", "provider": route.provider, "model": route.model,
                           "key_source": cred.source, "fallback_used": idx > 0}
                    for event, data in _sse_events(resp, check):
                        piece = acc.feed(event, data)
                        if piece:
                            s._sent = True
                            yield {"type": "delta", "text": piece}
                        if acc.done:
                            break
                    if not acc.complete():
                        raise _Fail("stream_interrupted", status)
                    parsed = acc.parsed()
                except _Fail as f:
                    s._close_resp()
                    att.update(reason=f.reason, http_status=f.status or att["http_status"])
                    if s._sent:
                        att["latency_ms"] = int((time.monotonic() - t0) * 1000)
                        self._log(route, att)
                        s.result = s._partial("stream_interrupted")
                        return
                    if (f.reason in RETRYABLE or f.reason == "stream_interrupted") and n < MAX_RETRIES:
                        wait = BACKOFF_S[min(n, len(BACKOFF_S) - 1)]
                        if f.retry_after is not None:
                            wait = min(max(wait, f.retry_after), RETRY_AFTER_CAP_S)
                        self._sleep(wait)
                        continue
                    break
                except _Cancelled:
                    raise
                except Exception as exc:  # noqa: BLE001 - never surface text that may hold input
                    s._close_resp()
                    logger.error("gateway: unexpected %s provider=%s", type(exc).__name__, route.provider)
                    att.update(reason="gateway_error")
                    if s._sent:
                        self._log(route, att)
                        s.result = s._partial("stream_interrupted")
                        return
                    break
                s._close_resp()
                att.update(ok=True, reason="ok", http_status=200,
                           latency_ms=int((time.monotonic() - t0) * 1000))
                self._log(route, att, parsed)
                cost, src = self._cost(route, parsed)
                s.result = Result(ok=True, text=parsed["text"], provider=route.provider, model=route.model,
                                  key_source=cred.source, key_id=cred.key_id, reason="ok", http_status=200,
                                  finish_reason=parsed["finish_reason"], tokens_in=parsed["tokens_in"],
                                  tokens_out=parsed["tokens_out"], cost_usd=cost, cost_source=src,
                                  provider_request_id=parsed["id"] if isinstance(parsed["id"], str) else None,
                                  latency_ms=att["latency_ms"], fallback_used=idx > 0, attempts=attempts,
                                  tool_calls=parsed["tool_calls"])
                return
            att["latency_ms"] = int((time.monotonic() - t0) * 1000)
            self._log(route, att)
            last = Result(ok=False, reason=att["reason"], http_status=att["http_status"])
            s._route = s._cred = s._att = None
        last.attempts = attempts
        s.result = last

    def _missing_capability(self, route: Route, req: ChatRequest) -> Optional[str]:
        if not req.tools and not req.json_mode:
            return None
        caps = self._caps(route.provider, route.model)
        if not caps:
            return None                                    # unknown: the provider decides
        if req.tools and caps.get("tools") is False:
            return "tools_not_supported"
        if req.json_mode and caps.get("structured_output") is False:
            return "structured_output_not_supported"
        return None

    @staticmethod
    def _log(route: Route, att: Dict[str, Any], parsed: Optional[Dict[str, Any]] = None) -> None:
        # metadata only: never the key, the prompt, the answer or tool arguments
        logger.info("gateway call provider=%s model=%s source=%s ok=%s reason=%s http=%s retries=%s "
                    "tokens_in=%s tokens_out=%s tool_calls=%s latency_ms=%s", route.provider, route.model,
                    att["key_source"], att["ok"], att["reason"], att["http_status"], att["retries"],
                    parsed and parsed["tokens_in"], parsed and parsed["tokens_out"],
                    parsed and len(parsed["tool_calls"]), att["latency_ms"])


# ------------------------------------------------------------------ streaming (D-160)
class _Cancelled(Exception):
    """The caller cancelled the stream (client disconnected)."""


class Stream:
    """Iterate for ``{"type": "route"|"delta", ...}`` events; ``result`` is set at the end.

    ``cancel()`` may be called from any thread (checked between lines);
    ``close()`` stops at once. Both end with ``reason=client_disconnected``."""

    def __init__(self, gw: Gateway, req: ChatRequest, routes: List[Route],
                 user_credential: Callable[[str], Optional[Credential]]):
        self.result: Optional[Result] = None
        self._cancel = threading.Event()
        self._gw = gw
        self._route: Optional[Route] = None
        self._cred: Optional[Credential] = None
        self._att: Optional[Dict[str, Any]] = None
        self._attempts: List[Dict[str, Any]] = []
        self._resp = None
        self._sent = False
        self._t0 = time.monotonic()
        self._gen = self._wrap(gw._run_stream(self, req, routes, user_credential))

    def __repr__(self) -> str:  # never the key, the prompt or the answer
        return f"Stream(done={self.result is not None})"

    def __iter__(self):
        return self

    def __next__(self) -> Dict[str, Any]:
        return next(self._gen)

    @property
    def attempts(self) -> List[Dict[str, Any]]:
        return self._attempts

    def cancel(self) -> None:
        self._cancel.set()

    def close(self) -> None:
        self._cancel.set()
        self._gen.close()
        if self.result is None:
            self._disconnected()

    def _check(self) -> None:
        if self._cancel.is_set():
            raise _Cancelled()

    def _close_resp(self) -> None:
        resp, self._resp = self._resp, None
        if resp is not None:
            try:
                resp.close()
            except Exception:  # noqa: BLE001 - closing is best effort
                pass

    def _partial(self, reason: str) -> Result:
        """An unfinished answer: names the route that streamed; text, tokens and cost unknown."""
        route, cred = self._route, self._cred
        return Result(ok=False, reason=reason, provider=route and route.provider, model=route and route.model,
                      key_source=cred and cred.source, key_id=cred and cred.key_id,
                      http_status=self._att and self._att["http_status"],
                      latency_ms=int((time.monotonic() - self._t0) * 1000), attempts=self._attempts)

    def _disconnected(self) -> None:
        self._close_resp()
        if self._att is not None and not self._att["ok"]:
            self._att["reason"] = "client_disconnected"
            self._att["latency_ms"] = int((time.monotonic() - self._t0) * 1000)
            Gateway._log(self._route, self._att)
        self.result = self._partial("client_disconnected")

    def _wrap(self, gen):
        try:
            yield from gen
        except _Cancelled:
            self._disconnected()
        finally:
            self._close_resp()
            gen.close()


def _sse_events(resp, check: Callable[[], None]):
    """(event, data) pairs from a Server-Sent Events body: comments skipped, size capped."""
    total, event, data = 0, None, []
    while True:
        check()
        try:
            line = resp.readline(MAX_SSE_LINE + 1)
        except (socket.timeout, TimeoutError):
            raise _Fail("timeout") from None
        except (OSError, ValueError, http.client.HTTPException):
            raise _Fail("stream_interrupted") from None
        if not line:
            if data:
                yield event, "\n".join(data)
            return
        total += len(line)
        if total > MAX_BODY or (len(line) > MAX_SSE_LINE and not line.endswith(b"\n")):
            raise _Fail("malformed_response")
        try:
            text = line.decode("utf-8").rstrip("\r\n")
        except UnicodeDecodeError:
            raise _Fail("malformed_response") from None
        if not text:
            if data:
                yield event, "\n".join(data)
            event, data = None, []
            continue
        if text.startswith(":"):
            continue
        name, _, value = text.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if name == "event":
            event = value
        elif name == "data":
            data.append(value)


def _chunk(data: str) -> Dict[str, Any]:
    try:
        obj = json.loads(data)
    except ValueError:
        raise _Fail("malformed_response") from None
    if not isinstance(obj, dict):
        raise _Fail("malformed_response")
    return obj


class _OpenAIStream:
    """OpenAI-compatible ``chat.completion.chunk`` events -> one answer."""

    def __init__(self):
        self.text: List[str] = []
        self.calls: Dict[int, Dict[str, Any]] = {}
        self.finish = None
        self.usage: Dict[str, Any] = {}
        self.id = None
        self.done = False

    def feed(self, event, data: str) -> Optional[str]:
        if data.strip() == "[DONE]":
            self.done = True
            return None
        obj = _chunk(data)
        if "error" in obj:
            raise _Fail("provider_unavailable")            # the error text is never read
        if self.id is None and isinstance(obj.get("id"), str):
            self.id = obj["id"]
        if isinstance(obj.get("usage"), dict):
            self.usage = obj["usage"]
        choices = obj.get("choices")
        if choices is None or choices == []:
            return None
        if not isinstance(choices, list) or not isinstance(choices[0], dict):
            raise _Fail("malformed_response")
        c = choices[0]
        delta = c.get("delta") if c.get("delta") is not None else {}
        if not isinstance(delta, dict):
            raise _Fail("malformed_response")
        content = delta.get("content")
        if content is not None and not isinstance(content, str):
            raise _Fail("malformed_response")
        tcs = delta.get("tool_calls")
        if tcs is not None:
            if not isinstance(tcs, list):
                raise _Fail("malformed_response")
            for pos, tc in enumerate(tcs):
                fn = tc.get("function") if isinstance(tc, dict) else None
                if not isinstance(tc, dict) or not isinstance(fn if fn is not None else {}, dict):
                    raise _Fail("malformed_response")
                i = tc.get("index", pos)
                if not isinstance(i, int) or isinstance(i, bool):
                    raise _Fail("malformed_response")
                slot = self.calls.setdefault(i, {"id": None, "name": None, "args": []})
                if len(self.calls) > MAX_TOOL_CALLS:
                    raise _Fail("malformed_response")
                fn = fn or {}
                for k, dest in (("id", tc.get("id")), ("name", fn.get("name"))):
                    if dest is not None:
                        if not isinstance(dest, str):
                            raise _Fail("malformed_response")
                        slot[k] = slot[k] or dest
                args = fn.get("arguments")
                if args is not None:
                    if not isinstance(args, str):
                        raise _Fail("malformed_response")
                    slot["args"].append(args)
        if c.get("finish_reason"):
            self.finish = c["finish_reason"]
        self.text.append(content or "")
        return content or None

    def complete(self) -> bool:
        return self.done or self.finish is not None

    def parsed(self) -> Dict[str, Any]:
        calls = []
        for i in sorted(self.calls):
            c = self.calls[i]
            if not c["id"] or not c["name"]:
                raise _Fail("malformed_response")
            calls.append(_call(c["id"], c["name"], "".join(c["args"])))
        return {"text": "".join(self.text), "finish_reason": self.finish, "tool_calls": calls,
                "tokens_in": _int(self.usage.get("prompt_tokens")),
                "tokens_out": _int(self.usage.get("completion_tokens")),
                "cost": _dec(self.usage.get("cost")), "id": self.id}


class _AnthropicStream:
    """Anthropic Messages API stream events -> one answer."""

    def __init__(self):
        self.text: List[str] = []
        self.blocks: Dict[int, Dict[str, Any]] = {}
        self.finish = None
        self.tokens_in = self.tokens_out = None
        self.id = None
        self.done = False

    def feed(self, event, data: str) -> Optional[str]:
        obj = _chunk(data)
        kind = obj.get("type") or event
        if kind == "error" or event == "error":
            raise _Fail("provider_unavailable")            # the error text is never read
        if kind == "message_start":
            msg = obj.get("message")
            if not isinstance(msg, dict):
                raise _Fail("malformed_response")
            self.id = msg.get("id") if isinstance(msg.get("id"), str) else None
            usage = msg.get("usage") if isinstance(msg.get("usage"), dict) else {}
            self.tokens_in = _int(usage.get("input_tokens"))
        elif kind == "content_block_start":
            b, i = obj.get("content_block"), obj.get("index")
            if not isinstance(b, dict) or not isinstance(i, int) or isinstance(i, bool):
                raise _Fail("malformed_response")
            if b.get("type") == "tool_use":
                if not isinstance(b.get("id"), str) or not isinstance(b.get("name"), str):
                    raise _Fail("malformed_response")
                if len([x for x in self.blocks.values() if x["tool"]]) >= MAX_TOOL_CALLS:
                    raise _Fail("malformed_response")
                self.blocks[i] = {"tool": True, "id": b["id"], "name": b["name"], "args": []}
            else:
                self.blocks[i] = {"tool": False}
        elif kind == "content_block_delta":
            d = obj.get("delta")
            if not isinstance(d, dict):
                raise _Fail("malformed_response")
            if d.get("type") == "text_delta":
                if not isinstance(d.get("text"), str):
                    raise _Fail("malformed_response")
                self.text.append(d["text"])
                return d["text"] or None
            if d.get("type") == "input_json_delta":
                blk = self.blocks.get(obj.get("index"))
                if not blk or not blk["tool"] or not isinstance(d.get("partial_json"), str):
                    raise _Fail("malformed_response")
                blk["args"].append(d["partial_json"])
        elif kind == "message_delta":
            d = obj.get("delta") if isinstance(obj.get("delta"), dict) else {}
            if d.get("stop_reason"):
                self.finish = d["stop_reason"]
            usage = obj.get("usage") if isinstance(obj.get("usage"), dict) else {}
            if _int(usage.get("output_tokens")) is not None:
                self.tokens_out = _int(usage.get("output_tokens"))
        elif kind == "message_stop":
            self.done = True
        return None                                        # ping and unknown events: ignored

    def complete(self) -> bool:
        return self.done

    def parsed(self) -> Dict[str, Any]:
        calls = []
        for i in sorted(self.blocks):
            b = self.blocks[i]
            if b["tool"]:
                raw = "".join(b["args"]) or "{}"
                obj = _json_object(raw)
                calls.append(_call(b["id"], b["name"],
                                   raw if obj is None else json.dumps(obj, separators=(",", ":"))))
        return {"text": "".join(self.text), "finish_reason": self.finish, "tool_calls": calls,
                "tokens_in": self.tokens_in, "tokens_out": self.tokens_out, "cost": None, "id": self.id}


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
        params = m.get("supported_parameters") if isinstance(m.get("supported_parameters"), list) else None
        mods = arch.get("input_modalities") if isinstance(arch.get("input_modalities"), list) else []
        pin, pout = _dec(pricing.get("prompt")), _dec(pricing.get("completion"))
        out[m["id"]] = {
            "provider": "openrouter", "id": m["id"],
            "context_length": _int(m.get("context_length")),
            "vision": "image" in mods,
            # no supported_parameters list = unknown (None): the provider decides, D-159
            "tools": None if params is None else "tools" in params,
            "structured_output": None if params is None else
            ("response_format" in params or "structured_outputs" in params),
            "price_in_per_token": pin, "price_out_per_token": pout,
            "free": pin == 0 and pout == 0 if pin is not None and pout is not None else False,
            "source": "openrouter:/api/v1/models", "fetched_at": fetched_at,
        }
    return out
