"""Search provider registry: one interface for every source, with a circuit breaker and real counters per provider.

A provider is an async function `query -> list[{"title", "url", "snippet", ...}]`. Running it through `call()`:
- skips it while its breaker is open (after `FAILS_TO_OPEN` consecutive errors), so a dead or blocking source
  stops costing time and requests, and retries it once (half-open) after the cooldown;
- counts requests, results, empty answers, errors and skips, and keeps the last error (no secrets, no bodies).

An empty answer is not a failure (the source worked and had nothing); an exception or timeout is.
No retry loops here: a provider that fails is skipped and the caller moves to the next one.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

FAILS_TO_OPEN = 3
COOLDOWN_S = 300.0
TIMEOUT_S = 30.0

ProviderFn = Callable[[str], Awaitable[list]]


@dataclass
class Provider:
    name: str
    kind: str  # web | news | academic | code | encyclopedia | data
    fn: ProviderFn
    keyless: bool = True
    langs: tuple = ("ar", "en")
    note: str = ""
    reraise: tuple = ()  # exceptions that mean "refused, handled by the caller" (e.g. DuckDuckGo's bot gate)
    # state
    fails: int = 0
    open_until: float = 0.0
    stats: dict = field(default_factory=lambda: {"requests": 0, "with_results": 0, "empty": 0, "errors": 0,
                                                 "skipped_open": 0, "refused": 0, "results": 0})
    last_error: str = ""

    def state(self, now: float | None = None) -> str:
        now = time.monotonic() if now is None else now
        if self.open_until and now < self.open_until:
            return "open"
        return "half_open" if self.open_until else "closed"

    def public(self) -> dict:
        return {"name": self.name, "kind": self.kind, "keyless": self.keyless, "state": self.state(),
                **self.stats, "last_error": self.last_error, "note": self.note}


REGISTRY: dict[str, Provider] = {}


def register(name: str, kind: str, fn: ProviderFn, **kw) -> Provider:
    p = REGISTRY.get(name)
    if p is None:
        p = REGISTRY[name] = Provider(name, kind, fn, **kw)
    else:  # re-registration (reload, tests) keeps counters but takes the new function
        p.fn, p.kind = fn, kind
    return p


class Skipped(Exception):
    """The provider's breaker is open."""


async def call(p: Provider, query: str, timeout: float = TIMEOUT_S) -> list:
    """Run one provider through its breaker. Raises Skipped when open; returns [] on error (recorded)."""
    now = time.monotonic()
    if p.state(now) == "open":
        p.stats["skipped_open"] += 1
        raise Skipped(p.name)
    p.stats["requests"] += 1
    try:
        out = await asyncio.wait_for(p.fn(query), timeout=timeout)
    except Skipped:
        raise
    except p.reraise:
        p.stats["refused"] += 1
        raise
    except Exception as e:  # noqa: BLE001 - any provider failure is recorded, never raised to the agent
        p.stats["errors"] += 1
        p.fails += 1
        p.last_error = f"{type(e).__name__}: {str(e)[:120]}"
        if p.fails >= FAILS_TO_OPEN or p.state(now) == "half_open":
            p.open_until = time.monotonic() + COOLDOWN_S
        return []
    p.fails, p.open_until = 0, 0.0
    out = [x for x in (out or []) if isinstance(x, dict) and str(x.get("url", "")).startswith("http")]
    p.stats["with_results" if out else "empty"] += 1
    p.stats["results"] += len(out)
    for x in out:
        x.setdefault("provider", p.name)
    return out


async def fan_out(names: list[str], query: str, timeout: float = TIMEOUT_S) -> dict[str, list]:
    """Run several providers at the same time; each answers or fails on its own."""
    async def one(n):
        try:
            return n, await call(REGISTRY[n], query, timeout)
        except Skipped:
            return n, None
    got = await asyncio.gather(*[one(n) for n in names if n in REGISTRY])
    return {n: r for n, r in got}


_TRACKING = re.compile(r"^(utm_|fbclid$|gclid$|mc_|ref$|ref_src$|igshid$|spm$)")


def normalize_url(url: str) -> str:
    """Same page, same key: lower-case host without www, no fragment, no tracking parameters, no trailing slash."""
    try:
        p = urlparse(url.strip())
    except ValueError:
        return url.strip()
    host = (p.hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    q = urlencode(sorted((k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not _TRACKING.match(k.lower())))
    path = re.sub(r"/+$", "", p.path) or ""
    return urlunparse(("https" if p.scheme in ("http", "https") else p.scheme, host, path, "", q, ""))


def merge(groups: list[list]) -> list:
    """Interleave results from several providers (round-robin) and drop duplicates by normalized URL.
    A duplicate keeps the first entry and records the other providers that also found it."""
    out, seen = [], {}
    longest = max((len(g) for g in groups), default=0)
    for i in range(longest):
        for g in groups:
            if i >= len(g):
                continue
            x = g[i]
            k = normalize_url(x["url"])
            if k in seen:
                also = seen[k].setdefault("also_found_by", [])
                if x.get("provider") and x["provider"] not in also and x["provider"] != seen[k].get("provider"):
                    also.append(x["provider"])
                continue
            seen[k] = x
            out.append(x)
    return out


def content_hash(text: str) -> str:
    """Fingerprint of extracted content (whitespace-insensitive) to spot the same text under two URLs."""
    return hashlib.sha256(re.sub(r"\s+", " ", text or "").strip().encode()).hexdigest()[:16]


def health() -> list[dict]:
    return [p.public() for p in REGISTRY.values()]
