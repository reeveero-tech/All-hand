"""R-206b (D-165, D-166): live aircraft positions for /api/v2/flights.

The owner asked for Airplanes.live. Its free API was closed in 2026 (every call
returns 403 "please contact us"; access now needs an emailed approval, a feeder
or a paid sponsorship — none of which this project may do on its own). The owner
chose (D-166) two keyless sources that answer with the same ADS-B Exchange v2
JSON:

  * primary  — adsb.lol   https://api.adsb.lol        data under ODbL 1.0;
  * fallback — adsb.fi    https://opendata.adsb.fi/api personal, non-commercial
                                                       use, cite + link adsb.fi.

Rules (both providers' published terms):
  * at most one upstream request per second per provider, for the whole server
    (one lock, not per user), and a short cache so repeated views cost nothing;
  * a fixed, identifying User-Agent;
  * input is validated before any upstream call (adsb.fi counts invalid
    requests toward its limit);
  * a failure is reported with each provider's error — never an empty list
    that looks like "no aircraft";
  * every response carries the attribution the UI must show.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

USER_AGENT = "AGOS-All-hand/1.0 (+https://github.com/reeveero-tech/All-hand)"
MAX_BODY_BYTES = 8 * 1024 * 1024
MAX_AIRCRAFT = 3000
CACHE_TTL_S = 8.0
MIN_INTERVAL_S = 1.0
MAX_WAITERS = 8
TIMEOUT_S = 8.0
MAX_RADIUS_NM = 250

PROVIDERS: Dict[str, Dict[str, Any]] = {
    "adsb.lol": {
        "base": "https://api.adsb.lol",
        "home": "https://adsb.lol",
        "license": "ODbL 1.0",
        "license_url": "https://opendatacommons.org/licenses/odbl/1-0/",
        "notice": "Data © adsb.lol contributors, ODbL 1.0",
        "paths": {
            "point": "/v2/point/{lat}/{lon}/{radius}",
            "hex": "/v2/hex/{q}",
            "callsign": "/v2/callsign/{q}",
            "reg": "/v2/reg/{q}",
            "type": "/v2/type/{q}",
            "squawk": "/v2/sqk/{q}",
            "mil": "/v2/mil",
            "ladd": "/v2/ladd",
            "pia": "/v2/pia",
        },
    },
    "adsb.fi": {
        "base": "https://opendata.adsb.fi/api",
        "home": "https://adsb.fi",
        "license": "adsb.fi open data terms: personal, non-commercial use",
        "license_url": "https://github.com/adsbfi/opendata",
        "notice": "Data from adsb.fi (personal, non-commercial use)",
        "paths": {
            "point": "/v3/lat/{lat}/lon/{lon}/dist/{radius}",
            "hex": "/v2/hex/{q}",
            "callsign": "/v2/callsign/{q}",
            "reg": "/v2/registration/{q}",
            "squawk": "/v2/sqk/{q}",
            "mil": "/v2/mil",
        },
    },
}
ORDER = ("adsb.lol", "adsb.fi")

QUERY_RULES = {
    "hex": re.compile(r"^~?[0-9a-f]{6}$"),
    "callsign": re.compile(r"^[A-Z0-9]{2,8}$"),
    "reg": re.compile(r"^[A-Z0-9-]{2,10}$"),
    "type": re.compile(r"^[A-Z0-9]{2,4}$"),
    "squawk": re.compile(r"^[0-7]{4}$"),
}
MODES = ("point", "hex", "callsign", "reg", "type", "squawk", "mil", "ladd", "pia")
KEEP = ("hex", "flight", "r", "t", "desc", "lat", "lon", "alt_baro", "alt_geom", "gs", "track",
        "baro_rate", "squawk", "category", "emergency", "dbFlags", "seen", "seen_pos", "type")


class FlightQueryError(ValueError):
    """Invalid user input — rejected before any upstream call."""


def parse_query(mode: str, q: Optional[str], lat: Optional[float], lon: Optional[float],
                radius: Optional[float]) -> Tuple[str, Dict[str, Any]]:
    mode = (mode or "").strip().lower()
    if mode not in MODES:
        raise FlightQueryError(f"mode must be one of {', '.join(MODES)}")
    if mode == "point":
        if lat is None or lon is None:
            raise FlightQueryError("point needs lat and lon")
        if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
            raise FlightQueryError("lat must be in [-90, 90] and lon in [-180, 180]")
        r = 50 if radius is None else radius
        if not (1 <= r <= MAX_RADIUS_NM):
            raise FlightQueryError(f"radius must be 1..{MAX_RADIUS_NM} nautical miles")
        # rounded so neighbouring views share one cache entry and one upstream call
        return mode, {"lat": round(lat, 2), "lon": round(lon, 2), "radius": int(round(r))}
    if mode in QUERY_RULES:
        v = (q or "").strip()
        v = v.lower() if mode == "hex" else v.upper()
        if not QUERY_RULES[mode].match(v):
            raise FlightQueryError(f"invalid {mode} value")
        return mode, {"q": v}
    return mode, {}


def _http_get(url: str, timeout: float = TIMEOUT_S) -> Tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https hosts
            body = resp.read(MAX_BODY_BYTES + 1)
            status = resp.status
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(2048) if exc.fp else b""
    if len(body) > MAX_BODY_BYTES:
        raise ValueError("upstream body too large")
    return status, body


def normalize(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = raw.get("ac")
    if rows is None:
        rows = raw.get("aircraft")
    if not isinstance(rows, list):
        raise ValueError("unexpected upstream shape (no ac/aircraft list)")
    out = []
    for a in rows[:MAX_AIRCRAFT]:
        if not isinstance(a, dict) or not a.get("hex"):
            continue
        item = {k: a[k] for k in KEEP if k in a}
        if isinstance(item.get("flight"), str):
            item["flight"] = item["flight"].strip()
        item["military"] = bool(int(a.get("dbFlags") or 0) & 1)
        out.append(item)
    return out


class FlightsClient:
    """One instance per server: shared throttle + cache across all users."""

    def __init__(self, fetch: Callable[[str], Tuple[int, bytes]] = _http_get,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Any] = asyncio.sleep,
                 ttl: float = CACHE_TTL_S, min_interval: float = MIN_INTERVAL_S):
        self._fetch, self._clock, self._sleep = fetch, clock, sleep
        self._ttl, self._min = ttl, min_interval
        self._cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._locks = {p: asyncio.Lock() for p in PROVIDERS}
        self._last: Dict[str, float] = {}
        self._waiting = 0
        self.upstream_calls: Dict[str, int] = {p: 0 for p in PROVIDERS}

    async def _call(self, provider: str, url: str) -> List[Dict[str, Any]]:
        async with self._locks[provider]:
            last = self._last.get(provider)
            if last is not None:
                wait = self._min - (self._clock() - last)
                if wait > 0:
                    await self._sleep(wait)
            self._last[provider] = self._clock()
            self.upstream_calls[provider] += 1
            status, body = await asyncio.to_thread(self._fetch, url)
        if status != 200:
            snippet = body[:160].decode("utf-8", "replace").strip()
            raise RuntimeError(f"HTTP {status}" + (f": {snippet}" if snippet else ""))
        return normalize(json.loads(body.decode("utf-8")))

    async def query(self, mode: str, params: Dict[str, Any]) -> Dict[str, Any]:
        key = mode + "|" + json.dumps(params, sort_keys=True)
        hit = self._cache.get(key)
        now = self._clock()
        if hit and now - hit[0] < self._ttl:
            return {**hit[1], "cached": True}
        if self._waiting >= MAX_WAITERS:
            return {"ok": False, "busy": True, "errors": [{"provider": "*", "error": "server busy, retry shortly"}]}
        self._waiting += 1
        try:
            errors = []
            for i, name in enumerate(ORDER):
                p = PROVIDERS[name]
                path = p["paths"].get(mode)
                if path is None:
                    errors.append({"provider": name, "error": f"mode {mode} not offered"})
                    continue
                url = p["base"] + path.format(**params)
                try:
                    aircraft = await self._call(name, url)
                except Exception as exc:  # reported, then the next provider is tried
                    msg = f"{type(exc).__name__}: {str(exc)[:200]}"
                    logger.warning("flights: %s failed (%s)", name, msg)
                    errors.append({"provider": name, "error": msg})
                    continue
                result = {
                    "ok": True, "mode": mode, "params": params, "provider": name,
                    "fallback_used": i > 0, "errors": errors, "count": len(aircraft),
                    "aircraft": aircraft, "fetched_at": time.time(),
                    "attribution": {k: p[k] for k in ("home", "license", "license_url", "notice")},
                }
                self._cache[key] = (self._clock(), result)
                if len(self._cache) > 512:
                    self._cache.pop(next(iter(self._cache)))
                return {**result, "cached": False}
            return {"ok": False, "mode": mode, "params": params, "errors": errors}
        finally:
            self._waiting -= 1


def build_router(client: Optional[FlightsClient] = None,
                 rate_limiter: Optional[Callable] = None) -> APIRouter:
    client = client or FlightsClient()
    r = APIRouter(prefix="/api/v2", tags=["v2 flights"])

    @r.get("/flights")
    async def flights(request: Request, mode: str = "point", q: Optional[str] = None,
                      lat: Optional[float] = None, lon: Optional[float] = None,
                      radius: Optional[float] = None):
        if rate_limiter is not None:
            await rate_limiter(request)
        try:
            m, params = parse_query(mode, q, lat, lon, radius)
        except FlightQueryError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        res = await client.query(m, params)
        if not res.get("ok"):
            code = 503 if res.get("busy") else 502
            return JSONResponse(status_code=code, content={
                "ok": False, "error": "no flight data source answered", "errors": res.get("errors", [])})
        return res

    return r
