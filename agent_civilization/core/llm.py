#!/usr/bin/env python3
"""
Real LLM client for the Agent Civilization.
Uses OpenRouter (supports free models). Reads OPENROUTER_API_KEY from env.
"""

import contextvars
import json
import logging
import os

import aiohttp

logger = logging.getLogger("llm")


# ---------------------------------------------------------------------------
# SEC-06 (R-145): the public keyless provider is opt-in at two independent
# levels, and BOTH are required before any text leaves this process:
#
# 1. Operator opt-in (``AGOS_LLM_KEYLESS=1``) — the server-side configuration
#    gate. Off by default: an absent variable, an empty value or ``0``/``false``
#    keeps the provider disabled, so with no ``OPENROUTER_API_KEY`` the provider
#    is ``none``. Only the explicit values ``1``/``true`` enable it; anything
#    else is a configuration error (``KeylessConfigError``) — it never silently
#    means "on" (validated at import, so an invalid value fails startup).
# 2. Request consent (``allow_public_llm`` on the request that carries the
#    text) — carried in a ContextVar that every entry point (HTTP handler,
#    background task) sets from the request and that ``LLMClient.complete``
#    requires before it will build or dispatch a keyless request. The default
#    is False: a direct ``get_llm().complete()`` call, a background job with no
#    consent, or a forgotten flag cannot send content to the public provider.
#
# A consenting request can never override the operator's opt-out: both gates
# are checked independently. A valid ``OPENROUTER_API_KEY`` keeps its existing
# behaviour and priority (``openrouter``) and is not affected by either gate.
# ---------------------------------------------------------------------------


class KeylessConfigError(RuntimeError):
    """AGOS_LLM_KEYLESS is set to a value that is not a supported boolean."""


_KEYLESS_ON = ("1", "true")
_KEYLESS_OFF = ("", "0", "false", "no")


def keyless_enabled() -> bool:
    """Parse AGOS_LLM_KEYLESS strictly (SEC-06, R-145).

    Unset/empty, ``0``, ``false`` and ``no`` (case-insensitive) disable the
    public provider; ``1`` and ``true`` enable it. Any other value raises
    ``KeylessConfigError`` — an ambiguous setting must never be interpreted as
    consent to send text to an external public provider.
    """
    raw = os.environ.get("AGOS_LLM_KEYLESS")
    value = (raw or "").strip().lower()
    if value in _KEYLESS_OFF:
        return False
    if value in _KEYLESS_ON:
        return True
    raise KeylessConfigError(
        "AGOS_LLM_KEYLESS must be one of 1/true (enable) or 0/false/no/unset "
        "(disable); got an unsupported value (not echoed — it is operator input, "
        "not user content, and stays out of logs) — the public LLM provider stays "
        "disabled")


# Import-time validation: an invalid value fails startup (the same fail-fast
# style as SEC-03 / AGENTS_PER_SPEC / AGOS_SSE_*), in every environment.
keyless_enabled()


_public_llm_consent: "contextvars.ContextVar[bool]" = contextvars.ContextVar(
    "agos_public_llm_consent", default=False)


PUBLIC_LLM_NO_CONSENT_ERROR = (
    "public LLM provider not used: this request did not consent to sending its "
    "text to the external public provider (send allow_public_llm=true with the "
    "request, or set OPENROUTER_API_KEY for the keyed provider)")


def set_public_llm_consent(consent: bool) -> contextvars.Token:
    """Mark THIS request's context as consenting to the public provider.

    Consent is per request: a ContextVar set here is visible only to the code
    running in this context (and copied into background tasks started from it —
    ``asyncio.create_task`` copies the context); it is never stored on the
    shared client, in a task record, or across tenants, and it does not persist
    beyond the request. Reset with the returned token in a ``finally``.
    """
    if not isinstance(consent, bool):
        raise TypeError(f"public LLM consent must be a bool, got {type(consent).__name__}")
    return _public_llm_consent.set(consent)


def reset_public_llm_consent(token: contextvars.Token) -> None:
    _public_llm_consent.reset(token)


def public_llm_consent() -> bool:
    """Whether the context that entered this text consented to the public provider."""
    return _public_llm_consent.get()

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = os.environ.get("LLM_MODEL", "meta-llama/llama-3.3-70b-instruct:free")


class LLMClient:
    """Async LLM client. One shared instance per process."""

    def __init__(self, api_key: str = None, model: str = None, url: str = None):
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        self.model = model or DEFAULT_MODEL
        # R-161: the OpenAI-compatible endpoint (default OpenRouter, unchanged).
        # Only the integration runner passes another URL, after its own host
        # allowlist / https / loopback checks (scripts/r161_live_integration.py).
        self.url = url or OPENROUTER_URL
        self._session: aiohttp.ClientSession | None = None

    @property
    def provider(self) -> str:
        """R-116: which backend answers — ``openrouter`` (key set), ``keyless``
        (public no-key endpoint) or ``none``. SEC-06 (R-145): the public
        endpoint is opt-in — only an explicit ``AGOS_LLM_KEYLESS=1``/``true``
        enables it; unset/``0``/``false``/``no`` or a missing OPENROUTER_API_KEY
        with no opt-in means ``none``. A missing key is never consent to use
        another provider."""
        if self.api_key or os.environ.get("OPENROUTER_API_KEY"):
            return "openrouter"
        if keyless_enabled():
            return "keyless"
        return "none"

    @property
    def available(self) -> bool:
        return self.provider != "none"

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=120)
            )
        return self._session

    async def complete(self, system: str, prompt: str, max_tokens: int = 1024,
                       max_wait_s: float | None = None) -> dict:
        """Return {"ok": bool, "content": str, "error": str|None, "model": str}.

        R-159: one `llm` span (provider, model, HTTP status, reason class —
        never the system/user text, the answer, the key or the error body)."""
        from agent_civilization.core import telemetry
        if not self.api_key and os.environ.get("OPENROUTER_API_KEY"):
            self.api_key = os.environ["OPENROUTER_API_KEY"]
        with telemetry.span("llm", "none") as sp:
            prov = self.provider
            sp.op = prov
            sp.set(provider=prov)
            out = await self._complete(system, prompt, max_tokens, max_wait_s)
            ok = bool(out.get("ok"))
            reason = None
            if not ok:
                if out.get("busy"):
                    reason = "busy"
                elif out.get("error") == PUBLIC_LLM_NO_CONSENT_ERROR:
                    reason = "no_consent"
                elif prov == "none":
                    reason = "not_configured"
                elif sp.err:
                    reason = "exception"
                else:
                    reason = "provider_error"
            sp.set(outcome="ok" if ok else ("error" if sp.err else "fail"), model=out.get("model"), reason=reason)
            return out

    async def _complete(self, system: str, prompt: str, max_tokens: int = 1024,
                        max_wait_s: float | None = None) -> dict:
        if not self.api_key and os.environ.get("OPENROUTER_API_KEY"):
            self.api_key = os.environ["OPENROUTER_API_KEY"]
        if self.provider == "keyless":
            # SEC-06 (R-145): the second, per-request gate. The operator's
            # opt-in alone is not the user's consent to send THIS text to an
            # external public provider: without explicit request consent no
            # keyless request is built or dispatched (no _keyless_post, no
            # alternative transport, no fabricated answer) — the failure is
            # returned in the normal LLM result contract.
            if not public_llm_consent():
                return {
                    "ok": False,
                    "content": "",
                    "error": PUBLIC_LLM_NO_CONSENT_ERROR,
                    "model": self.model,
                    "provider": "keyless",
                }
            return await _keyless_complete(system, prompt, max_tokens, max_wait_s)
        if not self.available:
            return {
                "ok": False,
                "content": "",
                "error": "OPENROUTER_API_KEY is not configured. Set it in the environment to enable real agent intelligence.",
                "model": self.model,
            }
        try:
            session = await self._get_session()
            async with session.post(
                self.url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://github.com/reeveero-tech/All-hand",
                    "X-Title": "AGOS Agent Civilization",
                },
                json={
                    "model": self.model,
                    "max_tokens": max_tokens,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                },
            ) as resp:
                body = await resp.text()
                if resp.status != 200:
                    # SEC-06 (R-145): provider error bodies may echo user text —
                    # log the status only, never the body content.
                    logger.error("LLM error: HTTP %s (provider response body not logged)", resp.status)
                    from agent_civilization.core import telemetry
                    telemetry.annotate(kind="llm", http_status=resp.status)
                    return {
                        "ok": False,
                        "content": "",
                        "error": f"LLM HTTP {resp.status}: {body[:300]}",
                        "model": self.model,
                    }
                data = json.loads(body)
                try:
                    content = completion_content(data)
                except MalformedCompletion as e:
                    # R-160 (llm result contract v1): a 200 whose body is not an
                    # OpenAI-style completion, or whose content is null/empty, is
                    # a failure — before, content=None/"" came back as ok=True.
                    telemetry_reason = e.reason
                    logger.error("LLM %s (provider body not logged)", telemetry_reason)
                    return {"ok": False, "content": "", "error": f"LLM {telemetry_reason}",
                            "model": self.model}
                return {
                    "ok": True,
                    "content": content,
                    "error": None,
                    "model": data.get("model", self.model),
                }
        except Exception as e:
            # SEC-06 (R-145): exception messages can carry provider response
            # text — log the exception type only. The message itself is returned
            # to the calling tenant, never logged.
            logger.error("LLM call failed: %s", type(e).__name__)
            from agent_civilization.core import telemetry
            telemetry.annotate(kind="llm", error=e)
            return {"ok": False, "content": "", "error": str(e), "model": self.model}

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


# ---------------------------------------------------------------------------
# R-116: keyless provider (no API key) — a real public LLM endpoint.
# Honest by construction: every answer is labelled provider="keyless" and the
# endpoint's own model name; rate-limit answers (HTTP 402/429) are retried
# with back-off inside a bounded budget, then surfaced as errors (never faked).
# Calls are serialised (one in flight per process) with a minimum spacing,
# because the anonymous tier rejects bursts.
# ---------------------------------------------------------------------------
import asyncio as _asyncio
import time as _time
import urllib.error as _urlerr
import urllib.request as _urlreq

KEYLESS_URL = os.environ.get("AGOS_LLM_KEYLESS_URL", "https://text.pollinations.ai/openai")
KEYLESS_MODEL = os.environ.get("AGOS_LLM_KEYLESS_MODEL", "openai")
KEYLESS_MIN_SPACING_S = float(os.environ.get("AGOS_LLM_KEYLESS_SPACING_S", "6"))
KEYLESS_BUDGET_S = float(os.environ.get("AGOS_LLM_KEYLESS_BUDGET_S", "90"))
KEYLESS_CALL_TIMEOUT_S = 45.0
# R-131 (D-054): admission control. A caller waits at most this long for its
# turn; when the requests already ahead (in flight + queued, one spacing slot
# each) cannot start within it, the call fails at once with an explicit
# "busy" error so the handler's own fallback runs immediately. Before R-131
# the 90 s budget started before the queue: callers deep in the queue spent
# it all waiting and failed with "no attempt" (RISK-028).
KEYLESS_MAX_WAIT_S = float(os.environ.get("AGOS_LLM_KEYLESS_MAX_WAIT_S", "30"))
_keyless_lock: "_asyncio.Lock | None" = None
_keyless_last = 0.0
_keyless_waiting = 0
keyless_stats = {"calls": 0, "ok": 0, "rate_limited": 0, "errors": 0, "busy": 0}


class MalformedCompletion(ValueError):
    """R-160: the provider answered 200 but not with a usable completion.
    ``reason`` is one of two FIXED strings (never provider text)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def completion_content(data: object) -> str:
    """R-160 (llm result contract v1): the text of an OpenAI-style completion
    body — ``{"choices": [{"message": {"content": "<str>"}}]}`` — or raise
    MalformedCompletion("malformed completion" | "empty completion")."""
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise MalformedCompletion("malformed completion") from None
    if content is None or (isinstance(content, str) and not content.strip()):
        raise MalformedCompletion("empty completion")
    if not isinstance(content, str):
        raise MalformedCompletion("malformed completion")
    return content


def _keyless_post(system: str, prompt: str, max_tokens: int, timeout: float) -> dict:
    from agent_civilization.core import safety  # outbound policy (R-100)
    body = json.dumps({
        "model": KEYLESS_MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
    }).encode()
    req = _urlreq.Request(KEYLESS_URL, data=body, method="POST",
                          headers={"Content-Type": "application/json",
                                   "User-Agent": "AGOS-Civilization/1.0"})
    with safety.safe_urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _keyless_busy(ahead: int, est: float, limit: float) -> dict:
    keyless_stats["busy"] += 1
    return {"ok": False, "content": "", "busy": True,
            "error": (f"keyless LLM busy: {ahead} request(s) ahead, ~{est:.0f}s estimated wait "
                      f"> {limit:.0f}s limit (AGOS_LLM_KEYLESS_MAX_WAIT_S; set OPENROUTER_API_KEY for capacity)"),
            "model": f"keyless:{KEYLESS_MODEL}", "provider": "keyless"}


async def _keyless_complete(system: str, prompt: str, max_tokens: int,
                            max_wait_s: float | None = None) -> dict:
    global _keyless_lock, _keyless_last, _keyless_waiting
    if _keyless_lock is None:
        _keyless_lock = _asyncio.Lock()
    limit = KEYLESS_MAX_WAIT_S if max_wait_s is None else max(0.0, max_wait_s)
    ahead = _keyless_waiting + (1 if _keyless_lock.locked() else 0)
    est = ahead * KEYLESS_MIN_SPACING_S + max(0.0, _keyless_last + KEYLESS_MIN_SPACING_S - _time.monotonic())
    if ahead and est > limit:
        return _keyless_busy(ahead, est, limit)
    _keyless_waiting += 1
    t_queue = _time.monotonic()
    try:
        await _asyncio.wait_for(_keyless_lock.acquire(), timeout=max(0.001, limit))
    except (TimeoutError, _asyncio.TimeoutError):
        return _keyless_busy(ahead, _time.monotonic() - t_queue, limit)
    finally:
        _keyless_waiting -= 1
    try:
        return await _keyless_attempts(system, prompt, max_tokens)
    finally:
        _keyless_lock.release()


async def _keyless_attempts(system: str, prompt: str, max_tokens: int) -> dict:
    """The bounded retry loop; runs with the lock held. The budget starts now,
    when it is this caller's turn (R-131), not when it joined the queue."""
    global _keyless_last
    deadline = _time.monotonic() + KEYLESS_BUDGET_S
    last_err = "no attempt"
    attempt = 0
    while _time.monotonic() < deadline:
        wait = KEYLESS_MIN_SPACING_S * (2 ** min(attempt, 3)) if attempt else 0.0
        wait = max(wait, _keyless_last + KEYLESS_MIN_SPACING_S - _time.monotonic())
        if wait > 0:
            if _time.monotonic() + wait >= deadline:
                break
            await _asyncio.sleep(wait)
        attempt += 1
        keyless_stats["calls"] += 1
        remaining = max(1.0, min(KEYLESS_CALL_TIMEOUT_S, deadline - _time.monotonic()))
        try:
            from agent_civilization.core import pools  # R-132: own pool
            data = await pools.run("llm", _keyless_post, system, prompt, max_tokens, remaining)
            _keyless_last = _time.monotonic()
            try:
                content = completion_content(data)
            except MalformedCompletion as e:
                keyless_stats["errors"] += 1
                if e.reason == "empty completion":
                    last_err = "empty completion"      # may be transient: retry
                    continue
                # R-160: a structurally wrong answer is deterministic — fail now
                # instead of re-sending the prompt until the 90 s budget ends
                # (it used to be retried as if it were a network error).
                return {"ok": False, "content": "", "error": "keyless LLM malformed completion",
                        "model": f"keyless:{KEYLESS_MODEL}", "provider": "keyless"}
            keyless_stats["ok"] += 1
            return {"ok": True, "content": content, "error": None,
                    "model": f"keyless:{data.get('model', KEYLESS_MODEL)}",
                    "provider": "keyless", "attempts": attempt}
        except _urlerr.HTTPError as e:
            _keyless_last = _time.monotonic()
            from agent_civilization.core import telemetry
            telemetry.annotate(kind="llm", http_status=e.code)   # R-159: status only
            if e.code in (402, 429, 503):
                keyless_stats["rate_limited"] += 1
                last_err = f"HTTP {e.code} (rate limited)"
                continue
            keyless_stats["errors"] += 1
            return {"ok": False, "content": "", "error": f"keyless LLM HTTP {e.code}",
                    "model": f"keyless:{KEYLESS_MODEL}", "provider": "keyless"}
        except Exception as e:  # noqa: BLE001 — network failure: retry within budget
            _keyless_last = _time.monotonic()
            keyless_stats["errors"] += 1
            last_err = f"{type(e).__name__}: {e}"
    return {"ok": False, "content": "",
            "error": f"keyless LLM unavailable within {KEYLESS_BUDGET_S:.0f}s budget ({last_err})",
            "model": f"keyless:{KEYLESS_MODEL}", "provider": "keyless"}


_client: LLMClient | None = None


def get_llm() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
