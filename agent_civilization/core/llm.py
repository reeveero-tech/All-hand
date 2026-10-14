#!/usr/bin/env python3
"""
Real LLM client for the Agent Civilization.
Uses OpenRouter (supports free models). Reads OPENROUTER_API_KEY from env.
"""

import json
import logging
import os

import aiohttp

logger = logging.getLogger("llm")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = os.environ.get("LLM_MODEL", "meta-llama/llama-3.3-70b-instruct:free")


class LLMClient:
    """Async LLM client. One shared instance per process."""

    def __init__(self, api_key: str = None, model: str = None):
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        self.model = model or DEFAULT_MODEL
        self._session: aiohttp.ClientSession | None = None

    @property
    def provider(self) -> str:
        """R-116: which backend answers — ``openrouter`` (key set), ``keyless``
        (public no-key endpoint, opt-out with AGOS_LLM_KEYLESS=0) or ``none``."""
        if self.api_key or os.environ.get("OPENROUTER_API_KEY"):
            return "openrouter"
        if os.environ.get("AGOS_LLM_KEYLESS", "1").strip() not in ("0", "false", "no", ""):
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

    async def complete(self, system: str, prompt: str, max_tokens: int = 1024) -> dict:
        """Return {"ok": bool, "content": str, "error": str|None, "model": str}."""
        if not self.api_key and os.environ.get("OPENROUTER_API_KEY"):
            self.api_key = os.environ["OPENROUTER_API_KEY"]
        if self.provider == "keyless":
            return await _keyless_complete(system, prompt, max_tokens)
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
                OPENROUTER_URL,
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
                    logger.error("LLM error %s: %s", resp.status, body[:500])
                    return {
                        "ok": False,
                        "content": "",
                        "error": f"LLM HTTP {resp.status}: {body[:300]}",
                        "model": self.model,
                    }
                data = json.loads(body)
                content = data["choices"][0]["message"]["content"]
                return {
                    "ok": True,
                    "content": content,
                    "error": None,
                    "model": data.get("model", self.model),
                }
        except Exception as e:
            logger.exception("LLM call failed")
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
_keyless_lock: "_asyncio.Lock | None" = None
_keyless_last = 0.0
keyless_stats = {"calls": 0, "ok": 0, "rate_limited": 0, "errors": 0}


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


async def _keyless_complete(system: str, prompt: str, max_tokens: int) -> dict:
    global _keyless_lock, _keyless_last
    if _keyless_lock is None:
        _keyless_lock = _asyncio.Lock()
    deadline = _time.monotonic() + KEYLESS_BUDGET_S
    last_err = "no attempt"
    async with _keyless_lock:
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
                data = await _asyncio.to_thread(_keyless_post, system, prompt, max_tokens, remaining)
                _keyless_last = _time.monotonic()
                content = data["choices"][0]["message"]["content"] or ""
                if not content.strip():
                    last_err = "empty completion"
                    keyless_stats["errors"] += 1
                    continue
                keyless_stats["ok"] += 1
                return {"ok": True, "content": content, "error": None,
                        "model": f"keyless:{data.get('model', KEYLESS_MODEL)}",
                        "provider": "keyless", "attempts": attempt}
            except _urlerr.HTTPError as e:
                _keyless_last = _time.monotonic()
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
