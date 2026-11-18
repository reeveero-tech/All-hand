"""R-206a (D-165): the tools hub — registry and mirror state for /api/v2/tools.

The registry (``tools_upstreams.json``) lists the tools the owner asked for.
Open-source upstreams are kept as full copies in this repository's GitHub
releases by ``.github/workflows/tools-mirror.yml``; that workflow writes
``status.json`` to the release ``mirror-status``. This module reads it (public
release download URL, no token), caches it, and merges it with the registry.

Rules:
  * the route is public metadata only (no user data, no secrets);
  * a failed status read is reported (``status.error``), never hidden, and the
    last good copy is kept;
  * the server never calls the GitHub API (rate-limited per shared IP).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

REGISTRY_PATH = Path(__file__).with_name("tools_upstreams.json")
DEFAULT_REPO = "reeveero-tech/All-hand"
CACHE_TTL_S = 600.0
MAX_STATUS_BYTES = 256 * 1024


def load_registry(path: Path = REGISTRY_PATH) -> List[Dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))["tools"]


def mirror_repo(env: Optional[Dict[str, str]] = None) -> str:
    # os.environ.get(...) spelled out so the R-156 static map sees the env read
    raw = os.environ.get("AGOS_TOOLS_MIRROR_REPO") if env is None else env.get("AGOS_TOOLS_MIRROR_REPO")
    return (raw or DEFAULT_REPO).strip()


def status_url(repo: str) -> str:
    return f"https://github.com/{repo}/releases/download/mirror-status/status.json"


def _http_get(url: str, timeout: float = 5.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "agos-tools-hub"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https URL
        body = resp.read(MAX_STATUS_BYTES + 1)
    if len(body) > MAX_STATUS_BYTES:
        raise ValueError("status.json too large")
    return body


class StatusCache:
    def __init__(self, url: str, fetch: Callable[[str], bytes] = _http_get,
                 ttl: float = CACHE_TTL_S, clock: Callable[[], float] = time.monotonic):
        self.url, self._fetch, self._ttl, self._clock = url, fetch, ttl, clock
        self._data: Optional[Dict[str, Any]] = None
        self._at: Optional[float] = None
        self.error: Optional[str] = None
        self._lock = asyncio.Lock()

    async def get(self) -> Optional[Dict[str, Any]]:
        async with self._lock:
            now = self._clock()
            if self._at is not None and now - self._at < self._ttl:
                return self._data
            try:
                raw = await asyncio.to_thread(self._fetch, self.url)
                data = json.loads(raw.decode("utf-8"))
                if not isinstance(data, dict) or not isinstance(data.get("tools"), list):
                    raise ValueError("unexpected status.json shape")
                self._data, self.error = data, None
            except Exception as exc:  # reported in the response, last good copy kept
                self.error = f"{type(exc).__name__}: {str(exc)[:120]}"
                logger.warning("tools hub: mirror status unavailable (%s)", self.error)
            self._at = now
            return self._data


def merge(registry: List[Dict[str, Any]], status: Optional[Dict[str, Any]], repo: str) -> List[Dict[str, Any]]:
    by_id = {t.get("id"): t for t in (status or {}).get("tools", []) if isinstance(t, dict)}
    out = []
    for t in registry:
        item = {k: t.get(k) for k in ("id", "item", "name", "kind", "source", "data_terms",
                                       "repo", "license", "page")}  # page: R-206b (D-166) web tab id
        if t.get("repo"):
            s = by_id.get(t["id"]) or {}
            base = f"https://github.com/{repo}/releases"
            item["upstream_url"] = f"https://github.com/{t['repo']}"
            item["mirror"] = {
                "state": "copied" if s.get("snapshot_at") else "not_copied_yet",
                "upstream_state": s.get("upstream_state"),
                "upstream_release": s.get("upstream_release"),
                "snapshot_head": s.get("snapshot_head"),
                "snapshot_at": s.get("snapshot_at"),
                "checked_at": s.get("checked_at"),
                "error": s.get("error"),
                "release_page": f"{base}/tag/mirror-{t['id']}",
                "bundle_url": f"{base}/download/mirror-{t['id']}/{t['id']}.bundle",
                "source_url": f"{base}/download/mirror-{t['id']}/{t['id']}-src.tar.gz",
                "mirrored_releases": [
                    {"tag": tag, "page": f"{base}/tag/mirror-{t['id']}-{tag}"}
                    for tag in (s.get("mirrored_releases") or [])],
            }
        out.append(item)
    return out


def build_router(cache: Optional[StatusCache] = None, repo: Optional[str] = None,
                 registry: Optional[List[Dict[str, Any]]] = None,
                 rate_limiter: Optional[Callable] = None) -> APIRouter:
    repo = repo or mirror_repo()
    cache = cache or StatusCache(status_url(repo))
    reg = registry if registry is not None else load_registry()
    r = APIRouter(prefix="/api/v2", tags=["v2 tools"])

    @r.get("/tools")
    async def list_tools(request: Request):
        if rate_limiter is not None:
            await rate_limiter(request)
        status = await cache.get()
        return {"tools": merge(reg, status, repo),
                "status": {"source": cache.url, "generated_at": (status or {}).get("generated_at"),
                           "error": cache.error}}

    return r
