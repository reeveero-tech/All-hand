"""Model access: any OpenAI-compatible chat endpoint.

Three ways to get a model, chosen per request:
  - "byok":     the user's own key (OpenRouter by default, or any OpenAI-compatible base URL).
  - "platform": the server's OPENROUTER_API_KEY, only with the owner's access code (APP_ACCESS_CODE).
  - "free":     public keyless endpoint (Pollinations), rate limited, for trying the system.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass, field

import httpx

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
FREE_BASE = os.environ.get("FREE_LLM_BASE", "https://text.pollinations.ai/openai")
FREE_MODEL = os.environ.get("FREE_LLM_MODEL", "openai")
MAX_OUTPUT_TOKENS = int(os.environ.get("LLM_MAX_OUTPUT_TOKENS", "12000"))  # ceiling when a reply runs out of tokens


class LLMError(RuntimeError):
    pass


JSON_CONTINUATIONS = 3
FREE_PROMPT_CHARS = int(os.environ.get("FREE_PROMPT_CHARS", "15000"))  # the free API rejects prompts over 16,000 characters (HTTP 413)


def _clip(text: str, n: int) -> str:
    """Keep the head and the tail (instructions usually open a message, the latest data closes it)."""
    if len(text) <= n:
        return text
    if n < 200:
        return text[:n]
    head = int(n * 0.6)
    return text[:head] + "\n[...اختُصر هنا لحدود طول الطلب؛ ليس نقصًا في الأصل...]\n" + text[-(n - head - 70):]


def fit_messages(messages: list[dict], budget: int) -> list[dict]:
    """Shrink a conversation to a character budget without losing the system prompt or the latest turn:
    older middle turns are shortened first, then dropped, then the remaining messages are clipped evenly."""
    msgs = [dict(m) for m in messages]
    size = lambda: sum(len(str(m.get("content") or "")) for m in msgs)  # noqa: E731
    if size() <= budget:
        return msgs
    for i in range(1, len(msgs) - 1):  # older turns: short versions
        if size() <= budget:
            return msgs
        msgs[i]["content"] = _clip(str(msgs[i].get("content") or ""), 1200)
    while size() > budget and len(msgs) > 3:  # still too long: drop the oldest middle turn (keep the task message)
        del msgs[2]
    if size() > budget:
        share = max(300, budget // len(msgs))
        over = size() - budget
        for m in sorted(msgs, key=lambda m: -len(str(m.get("content") or ""))):
            if over <= 0:
                break
            c = str(m.get("content") or "")
            cut = min(over, max(0, len(c) - share))
            m["content"] = _clip(c, len(c) - cut)
            over -= cut
    return msgs


@dataclass
class LLMConfig:
    provider: str  # byok | platform | free
    base_url: str
    model: str
    api_key: str | None = None
    concurrency: int = 8
    min_interval: float = 0.0  # seconds between request starts, shared by every run (the free limit is per server IP)

    def public(self) -> dict:
        return {"provider": self.provider, "model": self.model, "base_url": self.base_url}


@dataclass
class Usage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    errors: int = 0

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def resolve_config(provider: str, api_key: str | None, model: str | None,
                   base_url: str | None, access_code: str | None) -> LLMConfig:
    provider = (provider or "free").lower()
    if provider == "byok":
        if not api_key:
            raise LLMError("مطلوب مفتاح API الخاص بك (BYOK).")
        return LLMConfig("byok", (base_url or OPENROUTER_BASE).rstrip("/"),
                         model or "openai/gpt-4o-mini", api_key, concurrency=8)
    if provider == "platform":
        expected = os.environ.get("APP_ACCESS_CODE") or os.environ.get("AGOS_API_KEY")
        key = os.environ.get("OPENROUTER_API_KEY")
        if not expected or not access_code or access_code != expected:
            raise LLMError("رمز الوصول غير صحيح؛ مفتاح المنصة متاح للمالك فقط.")
        if not key:
            raise LLMError("لا يوجد OPENROUTER_API_KEY على الخادم.")
        return LLMConfig("platform", OPENROUTER_BASE,
                         model or os.environ.get("LLM_MODEL", "openai/gpt-4o-mini"), key, concurrency=8)
    if provider == "free":
        if os.environ.get("FREE_LLM_ENABLED", "1") != "1":
            raise LLMError("المزود المجاني معطّل على هذا الخادم.")
        return LLMConfig("free", FREE_BASE, FREE_MODEL, None, concurrency=1,
                         min_interval=float(os.environ.get("FREE_LLM_INTERVAL", "46")))
    raise LLMError(f"مزود غير معروف: {provider}")


RETRY_CAP = 75.0  # never wait longer than this for one retry


def retry_after(r: httpx.Response, default: float) -> float:
    """Seconds to wait before retrying a rate-limited call: the Retry-After header, else a
    'retry after Ns' hint in the body (the free provider sends one), else the default."""
    for raw in (r.headers.get("retry-after"), ):
        try:
            if raw is not None:
                return min(RETRY_CAP, float(raw) + 1)
        except ValueError:
            pass
    m = re.search(r"retry after (\d+(?:\.\d+)?)\s*s", r.text or "", re.I)
    return min(RETRY_CAP, float(m.group(1)) + 1) if m else min(RETRY_CAP, default)


RATE_RETRIES = 8  # rate-limit rejections tolerated per call, on top of the ordinary attempts


class Pacer:
    """Spaces request starts to one per `interval` seconds across the whole process, and lets a
    rate-limit answer push the next slot back. Without it, queued agents fire back to back and every
    one of them is rejected by a per-IP limit."""

    def __init__(self, interval: float):
        self.interval, self.next_at, self._lock = interval, 0.0, asyncio.Lock()

    async def slot(self):
        async with self._lock:
            loop = asyncio.get_running_loop()
            delay = self.next_at - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
            self.next_at = loop.time() + self.interval

    def push_back(self, seconds: float):
        self.next_at = max(self.next_at, asyncio.get_running_loop().time() + seconds)


_PACERS: dict[tuple, Pacer] = {}


def pacer_for(cfg: LLMConfig) -> Pacer | None:
    if cfg.min_interval <= 0:
        return None
    try:
        loop = id(asyncio.get_running_loop())
    except RuntimeError:
        loop = 0
    key = (cfg.base_url, loop)  # one per endpoint and event loop (locks are loop-bound)
    if key not in _PACERS:
        _PACERS[key] = Pacer(cfg.min_interval)
    return _PACERS[key]


class LLM:
    """Thin async client with a concurrency gate, retries and usage accounting."""

    def __init__(self, cfg: LLMConfig, timeout: float = 120.0):
        self.cfg = cfg
        self.usage = Usage()
        self.last_finish = None
        self.last_text = ""
        self._sem = asyncio.Semaphore(max(1, cfg.concurrency))
        self._client = httpx.AsyncClient(timeout=timeout)

    async def close(self):
        await self._client.aclose()

    def _url(self) -> str:
        b = self.cfg.base_url
        return b if b.endswith("/openai") or b.endswith("/chat/completions") else b + "/chat/completions"

    async def chat(self, messages: list[dict], temperature: float = 0.3, max_tokens: int = 2000) -> str:
        headers = {"Content-Type": "application/json"}
        if self.cfg.api_key:
            headers["Authorization"] = f"Bearer {self.cfg.api_key}"
            headers["HTTP-Referer"] = "https://github.com/reeveero-tech/All-hand"
            headers["X-Title"] = "All-hand"
        budget = FREE_PROMPT_CHARS if self.cfg.provider == "free" else 0
        if budget:
            messages = fit_messages(messages, budget)
        body = {"model": self.cfg.model, "messages": messages,
                "temperature": temperature, "max_tokens": max_tokens}
        last, attempt, limited = "", 0, 0
        pacer = pacer_for(self.cfg)
        while attempt < 5:
            async with self._sem:
                if pacer:
                    await pacer.slot()
                try:
                    r = await self._client.post(self._url(), headers=headers, json=body)
                except httpx.HTTPError as e:
                    last = f"network: {e}"
                    r = None
            if r is not None:
                if r.status_code == 200:
                    try:
                        data = r.json()
                        choice = data["choices"][0]
                        msg = choice["message"]
                        text = msg.get("content") or ""
                        self.last_finish = choice.get("finish_reason")
                        calls = msg.get("tool_calls") or []
                        if not text.strip() and calls:  # model used native tool calling: map to our protocol
                            fn = calls[0].get("function", {})
                            try:
                                args = json.loads(fn.get("arguments") or "{}")
                            except json.JSONDecodeError:
                                args = {"query": fn.get("arguments")}
                            text = json.dumps({"action": "tool", "tool": fn.get("name"), "input": args}, ensure_ascii=False)
                    except Exception as e:  # noqa: BLE001
                        last = f"bad response: {e}"
                    else:
                        self.usage.calls += 1
                        u = data.get("usage") or {}
                        self.usage.prompt_tokens += int(u.get("prompt_tokens") or 0)
                        self.usage.completion_tokens += int(u.get("completion_tokens") or 0)
                        if text.strip():
                            return text
                        last = "empty response"
                elif r.status_code in (401, 403):
                    self.usage.errors += 1
                    raise LLMError(f"المزود رفض المفتاح ({r.status_code}): {r.text[:200]}")
                else:
                    last = f"HTTP {r.status_code}: {r.text[:200]}"
                    if r.status_code == 400 and "output_token_limit" in r.text and body["max_tokens"] < MAX_OUTPUT_TOKENS:
                        # reasoning models can spend the whole budget thinking (production: the writer failed 2/2)
                        body["max_tokens"] = min(MAX_OUTPUT_TOKENS, body["max_tokens"] * 2)
                        attempt += 1
                        continue
                    if r.status_code == 413 and budget > 4000:  # still too long for the provider: shrink and retry
                        budget = int(budget * 0.75)
                        body["messages"] = fit_messages(messages, budget)
                        attempt += 1
                        continue
                    if r.status_code in (402, 429):  # rate limited: wait as long as the provider asks
                        wait = retry_after(r, default=6 * (attempt + 1))
                        limited += 1
                        if pacer:  # everyone waits, not just this call; don't spend an ordinary attempt
                            pacer.push_back(wait)
                            if limited <= RATE_RETRIES:
                                continue
                        else:
                            await asyncio.sleep(wait)
                        attempt += 1
                        continue
            attempt += 1
            await asyncio.sleep(1.5 * attempt ** 2)
        self.usage.errors += 1
        raise LLMError(f"فشل استدعاء النموذج بعد عدة محاولات: {last}")

    async def chat_long(self, messages: list[dict], temperature: float = 0.3, max_tokens: int = 4000, parts: int = 3) -> str:
        """A long answer: when the provider stops at its output limit, ask it to continue where it stopped."""
        text = piece = await self.chat(messages, temperature, max_tokens)
        for _ in range(parts - 1):
            if getattr(self, "last_finish", None) != "length" and not looks_truncated(piece):
                break
            more = await self.chat(messages + [{"role": "assistant", "content": text[-6000:]},
                                               {"role": "user", "content": "توقف ردك قبل اكتماله. أكمل من حيث توقفت بالضبط، "
                                                                           "بلا تكرار ولا مقدمة."}], temperature, max_tokens)
            piece = more
            text = text.rstrip() + ("\n" if text.rstrip().endswith("|") else "") + more.lstrip()
        return text

    async def chat_json(self, messages: list[dict], temperature: float = 0.2, max_tokens: int = 2000) -> dict:
        text = await self.chat(messages, temperature, max_tokens)
        for _ in range(JSON_CONTINUATIONS):  # the free API stops replies at ~2,500 characters (finish_reason=length);
            if getattr(self, "last_finish", None) != "length":  # a cut JSON would parse to an inner object (a2085661bb506)
                break
            text += await self.chat(messages + [{"role": "assistant", "content": text[-6000:]},
                                                {"role": "user", "content": "توقف ردك قبل اكتماله. أكمل نص JSON من الحرف الذي توقفت عنده بالضبط، "
                                                                            "بلا تكرار ولا مقدمة ولا ```."}], 0.0, max_tokens)
        self.last_text = text
        try:
            return extract_json(text)
        except ValueError:
            repair = messages + [{"role": "assistant", "content": text},
                                 {"role": "user", "content": "ردك ليس JSON صالحًا. أعد الرد نفسه ككائن JSON صالح فقط بلا أي نص آخر."}]
            return extract_json(await self.chat(repair, 0.0, max_tokens))


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def looks_truncated(text: str) -> bool:
    """A reply that stops mid-structure: an open table row or bold marker, an unclosed code fence, or a dangling word."""
    t = (text or "").rstrip()
    if not t:
        return True
    last = t.splitlines()[-1].strip()
    if t.count("```") % 2:
        return True
    if last.startswith("|") and not last.endswith("|"):
        return True
    if last.count("**") % 2:
        return True
    return bool(re.search(r"[،,:\-–(]$", last))


def extract_objects(text: str) -> list[dict]:
    """Every complete JSON object in text, at any nesting level (salvages the finished parts of a cut reply)."""
    out, c = [], text or ""
    for start in [i for i, ch in enumerate(c) if ch == "{"]:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(c)):
            ch = c[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(c[start:i + 1])
                        if isinstance(obj, dict):
                            out.append(obj)
                    except json.JSONDecodeError:
                        pass
                    break
    return out


def extract_json(text: str) -> dict:
    """Return the first JSON object found in text (handles code fences and chatter)."""
    candidates = [m.group(1) for m in _FENCE.finditer(text)] + [text]
    for c in candidates:
        c = c.strip()
        start = c.find("{")
        while start != -1:
            depth, in_str, esc = 0, False, False
            for i in range(start, len(c)):
                ch = c[i]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                elif ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            obj = json.loads(c[start:i + 1])
                            if isinstance(obj, dict):
                                return obj
                        except json.JSONDecodeError:
                            pass
                        break
            start = c.find("{", start + 1)
    raise ValueError("no JSON object in model output")
