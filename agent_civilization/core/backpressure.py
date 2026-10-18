"""R-164 (D-083): backpressure primitives shared by every service boundary.

Three small, dependency-free pieces:

- ``backoff_delay`` — bounded exponential back-off with "equal jitter": the
  delay for attempt ``n`` is drawn from ``[exp/2, exp]`` where
  ``exp = min(cap, base * 2**(n-1))``. Never 0 (a retry always waits), never
  above ``cap``, and two callers that failed together do not retry together.
- ``parse_retry_after`` — the HTTP ``Retry-After`` header (delta-seconds or an
  HTTP-date), clamped to a cap; garbage is ``None`` (the caller falls back to
  its own back-off — a hostile header can never stall it unboundedly).
- ``CircuitBreaker`` — per *service* (``llm``, ``web``, ``translate``,
  ``storage``): ``closed`` -> ``open`` after ``threshold`` CONSECUTIVE
  failures, ``open`` rejects at once for ``reset_s`` (callers fail fast with a
  retry hint instead of each burning a full timeout), then ``half_open`` lets
  exactly ONE probe through: success closes, failure re-opens. A provider's
  explicit ``Retry-After`` ``hold``s the breaker open for that long.

What a breaker is NOT used for (documented decision, D-083): the ``net`` pool
(reachability probes of USER-supplied URLs) and the ``local`` pool (SQLite,
sandbox). A failing user URL is a property of that URL, not an outage of a
shared service — one breaker for all of them would let one user's dead host
deny every other user's probe. Those pools stay bounded by their thread
count, their optional queue cap (``pools.PoolBusy``) and the caller's own
timeout.

Thread-safe: the web/translate breakers are used from pool threads.
Configuration (read once per breaker; an invalid value is a startup error,
same rule as AGOS_SSE_* / AGOS_WORKER_CAPACITY):
``AGOS_BREAKER_<NAME>_THRESHOLD`` (0 disables the breaker) and
``AGOS_BREAKER_<NAME>_RESET_S``.
"""
from __future__ import annotations

import math
import os
import random
import threading
import time
from email.utils import parsedate_to_datetime
from typing import Callable, Dict, Optional

CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"
STATE_CODE = {CLOSED: 0, HALF_OPEN: 1, OPEN: 2}   # for /metrics gauges

# name -> (default consecutive-failure threshold, default open period seconds)
BREAKER_SPECS: Dict[str, tuple] = {
    "llm": (5, 30),         # model provider (keyed OpenRouter or keyless)
    "web": (5, 30),         # web search provider (DuckDuckGo HTML)
    "translate": (5, 30),   # translation provider (MyMemory)
    "storage": (5, 10),     # Supabase/PostgREST
}

RETRY_AFTER_CAP_S = 300.0


class CircuitOpen(RuntimeError):
    """Raised (or reported) instead of calling a service whose breaker is open."""

    def __init__(self, service: str, retry_after: float):
        self.service = service
        self.retry_after = max(1, int(math.ceil(retry_after)))
        super().__init__(f"{service} circuit open: the service is failing, retry in ~{self.retry_after}s")


def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, got {raw!r}") from exc
    if not lo <= value <= hi:
        raise RuntimeError(f"{name} must be between {lo} and {hi}, got {value}")
    return value


def _float_env(name: str, default: float, lo: float, hi: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number, got {raw!r}") from exc
    if not (lo <= value <= hi) or math.isnan(value):
        raise RuntimeError(f"{name} must be between {lo} and {hi}, got {value}")
    return value


def backoff_delay(attempt: int, base: float, cap: float,
                  rng: Optional[random.Random] = None) -> float:
    """Delay before retry number ``attempt`` (1 = first retry): equal jitter
    in ``[exp/2, exp]``, ``exp = min(cap, base * 2**(attempt-1))``."""
    base = max(0.001, float(base))
    cap = max(base, float(cap))
    n = max(1, int(attempt))
    exp = min(cap, base * (2 ** min(n - 1, 30)))
    r = (rng or random).random()
    return exp / 2 + r * (exp / 2)


def parse_retry_after(value: Optional[str], cap: float = RETRY_AFTER_CAP_S,
                      now: Optional[float] = None) -> Optional[float]:
    """Seconds to wait from a ``Retry-After`` header value, clamped to
    ``[0, cap]``; ``None`` when absent or unparseable."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return float(min(cap, int(text)))
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if when is None:
        return None
    current = time.time() if now is None else now
    try:
        delta = when.timestamp() - current
    except (OverflowError, OSError, ValueError):
        return None
    return float(min(cap, max(0.0, delta)))


class Permit:
    """The right to report ONE call's outcome to a breaker (R-164 review).

    ``allow()`` hands one out; ``success``/``failure``/``release_probe`` act
    only when given the permit of the CURRENT breaker generation — every
    state change (closed -> open, open -> half-open, half-open -> closed or
    open, any ``hold``) starts a new generation, so the outcome of a call
    that was admitted before that change is stale and ignored. In half-open
    only the probe's own permit can close (or re-open) the breaker. A permit
    reports once; later reports with it are no-ops (so a ``finally`` that
    releases it is always safe)."""

    __slots__ = ("gen", "probe", "settled")

    def __init__(self, gen: int, probe: bool):
        self.gen, self.probe, self.settled = gen, probe, False

    def __bool__(self) -> bool:      # ``if not brk.allow():`` keeps working
        return True


class CircuitBreaker:
    def __init__(self, name: str, threshold: int, reset_s: float,
                 clock: Callable[[], float] = time.monotonic):
        self.name = name
        self.threshold = max(0, int(threshold))
        self.reset_s = max(0.1, float(reset_s))
        self._clock = clock
        self._lock = threading.Lock()
        self._state = CLOSED
        self._failures = 0
        self._open_until = 0.0
        self._probe_since: Optional[float] = None
        self._probe: Optional[Permit] = None     # the current half-open probe
        self._gen = 0
        self.stats = {"opened": 0, "rejected": 0, "held": 0, "stale": 0}

    # -- queries ----------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self.threshold > 0

    def state(self) -> str:
        with self._lock:
            self._tick()
            return self._state

    def retry_after(self) -> float:
        """Seconds until the next call may go through (0 when closed)."""
        with self._lock:
            self._tick()
            if self._state == OPEN:
                return max(0.0, self._open_until - self._clock())
            if self._state == HALF_OPEN and self._probe_since is not None:
                return max(0.0, self._probe_since + self.reset_s - self._clock())
            return 0.0

    def is_open(self) -> bool:
        """True while calls are refused (open, or half-open with its probe
        already in flight) — does NOT consume the half-open probe."""
        with self._lock:
            self._tick()
            if self._state == OPEN:
                return True
            return self._state == HALF_OPEN and self._probe_since is not None

    def snapshot(self) -> dict:
        with self._lock:
            self._tick()
            return {"state": self._state, "failures": self._failures,
                    "threshold": self.threshold, "reset_s": self.reset_s, **self.stats}

    # -- transitions (all under self._lock) -------------------------------
    def _new_gen(self) -> None:
        self._gen += 1
        self._probe, self._probe_since = None, None

    def _tick(self) -> None:
        now = self._clock()
        if self._state == OPEN and now >= self._open_until:
            self._state = HALF_OPEN
            self._new_gen()
        elif self._state == HALF_OPEN and self._probe_since is not None \
                and now >= self._probe_since + self.reset_s:
            # the probe never reported (cancelled/lost): allow another one;
            # the lost probe's permit no longer closes anything
            self._probe, self._probe_since = None, None

    def _current(self, permit: Optional[Permit], count: bool = True) -> bool:
        """Is ``permit`` entitled to report now? (settles it either way;
        ``count`` = record a refused report in stats["stale"])"""
        if permit is None or permit.settled:
            return False
        permit.settled = True
        ok = permit.gen == self._gen and (
            (self._state == CLOSED) or
            (self._state == HALF_OPEN and permit is self._probe))
        # (OPEN: opening always starts a new generation, so a
        # same-generation permit cannot exist there — refused defensively)
        if not ok and count:
            self.stats["stale"] += 1
        return ok

    def allow(self) -> Optional[Permit]:
        """May a call go through now? Returns its ``Permit`` (truthy) or
        ``None``. In half-open, the first caller gets the single probe
        permit; the others are refused until it reports."""
        with self._lock:
            self._tick()
            if self._state == CLOSED:
                return Permit(self._gen, False)
            if self._state == HALF_OPEN and self._probe is None:
                self._probe_since = self._clock()
                self._probe = Permit(self._gen, True)
                return self._probe
            self.stats["rejected"] += 1
            return None

    def check(self) -> Permit:
        """``allow()`` or raise ``CircuitOpen``."""
        p = self.allow()
        if p is None:
            raise CircuitOpen(self.name, self.retry_after())
        return p

    def success(self, permit: Optional[Permit]) -> None:
        """The call admitted with ``permit`` reached a healthy service. Only
        a current-generation permit counts: closed -> resets the consecutive
        failures; half-open -> only the probe closes the breaker. A stale
        success (the call started before an open/hold) changes nothing — it
        can neither close the breaker nor cut a Retry-After hold short."""
        with self._lock:
            self._tick()
            if not self._current(permit):
                return
            if self._state == HALF_OPEN:
                self._state = CLOSED
                self._new_gen()
            self._failures = 0

    def failure(self, permit: Optional[Permit]) -> None:
        with self._lock:
            self._tick()
            if not self._current(permit) or not self.enabled:
                return
            if self._state == HALF_OPEN:
                self._open(self.reset_s)        # the probe failed
                return
            self._failures += 1
            if self._failures >= self.threshold:
                self._open(self.reset_s)

    def release_probe(self, permit: Optional[Permit]) -> None:
        """The call ended with an outcome that says nothing about the
        service's health (a 4xx of the request itself, a rate limit without
        Retry-After, a cancellation): neither success nor failure. Frees the
        half-open probe slot if ``permit`` is that probe; idempotent."""
        with self._lock:
            self._tick()
            if not self._current(permit, count=False):
                return
            if self._state == HALF_OPEN:
                self._probe, self._probe_since = None, None

    def hold(self, seconds: float) -> None:
        """The service said when to come back (``Retry-After``): refuse calls
        until then — honoured from ANY call (fresh information from the
        provider, never stale), even when the failure threshold is disabled.
        Starts a new generation: no call admitted before it can lift it."""
        seconds = min(RETRY_AFTER_CAP_S, max(0.0, float(seconds)))
        if seconds <= 0:
            return
        with self._lock:
            self._tick()
            self.stats["held"] += 1
            until = self._clock() + seconds
            if self._state == OPEN and self._open_until >= until:
                return
            if self._state != OPEN:
                self.stats["opened"] += 1
            self._state, self._open_until = OPEN, until
            self._new_gen()

    def _open(self, seconds: float) -> None:
        self._state, self._open_until = OPEN, self._clock() + seconds
        self._failures = 0
        self._new_gen()
        self.stats["opened"] += 1


_breakers: Dict[str, CircuitBreaker] = {}
_breakers_lock = threading.Lock()


def _breaker_config(name: str) -> tuple:
    """(threshold, reset_s) from the environment. Every variable is read by
    its literal name — greppable, and visible to the R-156 entry-point map
    (a name built at runtime would be invisible to both)."""
    t, r = BREAKER_SPECS[name]
    if name == "llm":
        return (_int_env("AGOS_BREAKER_LLM_THRESHOLD", t, 0, 1000),
                _float_env("AGOS_BREAKER_LLM_RESET_S", float(r), 0.1, 3600.0))
    if name == "web":
        return (_int_env("AGOS_BREAKER_WEB_THRESHOLD", t, 0, 1000),
                _float_env("AGOS_BREAKER_WEB_RESET_S", float(r), 0.1, 3600.0))
    if name == "translate":
        return (_int_env("AGOS_BREAKER_TRANSLATE_THRESHOLD", t, 0, 1000),
                _float_env("AGOS_BREAKER_TRANSLATE_RESET_S", float(r), 0.1, 3600.0))
    return (_int_env("AGOS_BREAKER_STORAGE_THRESHOLD", t, 0, 1000),
            _float_env("AGOS_BREAKER_STORAGE_RESET_S", float(r), 0.1, 3600.0))


def breaker(name: str) -> CircuitBreaker:
    if name not in BREAKER_SPECS:
        raise KeyError(f"unknown breaker {name!r}; known: {sorted(BREAKER_SPECS)}")
    with _breakers_lock:
        b = _breakers.get(name)
        if b is None:
            threshold, reset_s = _breaker_config(name)
            b = _breakers[name] = CircuitBreaker(name, threshold, reset_s)
        return b


def validate_env() -> None:
    """Startup check: build every breaker so an invalid AGOS_BREAKER_* value
    fails the start, not the first call that needs it."""
    for name in BREAKER_SPECS:
        breaker(name)


def reset_all() -> None:
    """Test helper: forget every breaker (re-read from the environment)."""
    with _breakers_lock:
        _breakers.clear()


def snapshot() -> Dict[str, dict]:
    return {name: breaker(name).snapshot() for name in BREAKER_SPECS}


def guarded_call(name: str, fn: Callable, *args, **kwargs):
    """Call a blocking provider function behind breaker ``name`` (used from
    pool threads for the web search / translation providers).

    Outcome classification (the same rule everywhere, D-083): a transport
    failure (``OSError`` — URLError, timeout, connection reset) or an HTTP
    5xx is a provider failure; HTTP 429/503 with ``Retry-After`` holds the
    breaker for that long; any other exception (bad input, a policy refusal,
    a parse error) says nothing about the provider and only frees a
    half-open probe slot. Raises ``CircuitOpen`` without calling ``fn``
    while the breaker is open.
    """
    import urllib.error
    b = breaker(name)
    p = b.check()
    try:
        out = fn(*args, **kwargs)
    except urllib.error.HTTPError as e:
        ra = parse_retry_after(e.headers.get("Retry-After") if e.headers else None)
        if e.code in (429, 503) and ra:
            b.hold(ra)
        elif e.code >= 500:
            b.failure(p)
        raise
    except OSError:
        b.failure(p)
        raise
    else:
        b.success(p)
        return out
    finally:
        b.release_probe(p)      # no-op once the permit has reported
