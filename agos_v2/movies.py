"""R-206c (D-165): serve our own movie map (built from Wikidata, CC0).

``scripts/tools/build_movie_map.py`` builds ``movies.json.gz`` in the scheduled
workflow ``movies-map.yml`` and uploads it to the release ``movies-map`` of this
repository. This module downloads that public asset (no token), validates it,
keeps it in memory and serves it at ``GET /api/v2/movies/map``.

Rules:
  * public data only (CC0 films; no user data, no secrets);
  * the gzip bytes are served as they are to clients that accept gzip, so the
    server does not re-compress a multi-MB body per request;
  * a failed download is reported (503 with the reason, or ``X-Map-Stale`` when
    the last good copy is served), never hidden;
  * suggestions are computed in the browser from the neighbour lists — the
    server keeps no viewing history.
"""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import io
import json
import logging
import time
import urllib.request
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from agos_v2.tools_hub import mirror_repo

logger = logging.getLogger(__name__)

RELEASE_TAG = "movies-map"
ASSET = "movies.json.gz"
CACHE_TTL_S = 3600.0
RETRY_AFTER_FAIL_S = 120.0
MAX_GZ_BYTES = 32 * 1024 * 1024
MAX_JSON_BYTES = 128 * 1024 * 1024
FORMAT_VERSION = 1
FIELDS = ["qid", "title_ar", "title_en", "year", "imdb", "genres", "x", "y", "island", "sitelinks"]


def map_url(repo: str) -> str:
    return f"https://github.com/{repo}/releases/download/{RELEASE_TAG}/{ASSET}"


def _http_get(url: str, timeout: float = 30.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "agos-movies"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https URL
        body = resp.read(MAX_GZ_BYTES + 1)
    if len(body) > MAX_GZ_BYTES:
        raise ValueError("movies.json.gz too large")
    return body


def validate(gz: bytes) -> Dict[str, Any]:
    """Decompress (bounded) and check the shape; returns a small summary."""
    d = gzip.GzipFile(fileobj=io.BytesIO(gz))
    raw = d.read(MAX_JSON_BYTES + 1)
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError("movie map JSON too large")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict) or data.get("format") != FORMAT_VERSION:
        raise ValueError(f"unexpected movie map format {data.get('format') if isinstance(data, dict) else None}")
    if data.get("fields") != FIELDS:
        raise ValueError("unexpected movie map fields")
    films, neigh = data.get("films"), data.get("neighbours")
    if not isinstance(films, list) or not isinstance(neigh, list) or len(films) != len(neigh):
        raise ValueError("films/neighbours missing or of different length")
    if not films:
        raise ValueError("empty movie map")
    n = len(films)
    for row in neigh:
        for pair in row:
            if not (isinstance(pair, list) and len(pair) == 2 and isinstance(pair[0], int) and 0 <= pair[0] < n):
                raise ValueError("neighbour index out of range")
    return {"films": n, "islands": len(data.get("islands") or []), "generated_at": data.get("generated_at"),
            "source": data.get("source"), "license": data.get("license")}


class MapCache:
    def __init__(self, url: str, fetch: Callable[[str], bytes] = _http_get,
                 ttl: float = CACHE_TTL_S, clock: Callable[[], float] = time.monotonic):
        self.url, self._fetch, self._ttl, self._clock = url, fetch, ttl, clock
        self.gz: Optional[bytes] = None
        self.summary: Optional[Dict[str, Any]] = None
        self.etag: Optional[str] = None
        self.error: Optional[str] = None
        self._at: Optional[float] = None
        self._lock = asyncio.Lock()

    async def get(self) -> Optional[bytes]:
        async with self._lock:
            now = self._clock()
            wait = self._ttl if self.error is None else RETRY_AFTER_FAIL_S
            if self._at is not None and now - self._at < wait:
                return self.gz
            try:
                gz = await asyncio.to_thread(self._fetch, self.url)
                summary = await asyncio.to_thread(validate, gz)
                self.gz, self.summary, self.error = gz, summary, None
                self.etag = '"' + hashlib.sha256(gz).hexdigest()[:32] + '"'
            except Exception as exc:  # reported; the last good copy is kept
                self.error = f"{type(exc).__name__}: {str(exc)[:160]}"
                logger.warning("movies: map unavailable (%s)", self.error)
            self._at = now
            return self.gz


def build_router(cache: Optional[MapCache] = None, repo: Optional[str] = None,
                 rate_limiter: Optional[Callable] = None) -> APIRouter:
    cache = cache or MapCache(map_url(repo or mirror_repo()))
    r = APIRouter(prefix="/api/v2", tags=["v2 movies"])

    @r.get("/movies/status")
    async def movies_status(request: Request):
        if rate_limiter is not None:
            await rate_limiter(request)
        await cache.get()
        return {"ok": cache.gz is not None, "source": cache.url, "map": cache.summary, "error": cache.error}

    @r.get("/movies/map")
    async def movies_map(request: Request):
        if rate_limiter is not None:
            await rate_limiter(request)
        gz = await cache.get()
        if gz is None:
            return JSONResponse(status_code=503, headers={"Retry-After": "120"}, content={
                "ok": False, "error": "movie map not available yet", "detail": cache.error,
                "source": cache.url})
        headers = {"Cache-Control": "public, max-age=600", "ETag": cache.etag or "",
                   "Vary": "Accept-Encoding"}
        if cache.error:
            headers["X-Map-Stale"] = cache.error[:120]
        if request.headers.get("if-none-match") == cache.etag:
            return Response(status_code=304, headers=headers)
        if "gzip" in request.headers.get("accept-encoding", "").lower():
            return Response(content=gz, media_type="application/json",
                            headers={**headers, "Content-Encoding": "gzip"})
        return Response(content=gzip.decompress(gz), media_type="application/json", headers=headers)

    return r
