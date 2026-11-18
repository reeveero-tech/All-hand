"""R-206d (D-165): download page data for the mirrored desktop tools (MDM).

The files are served from OUR copies (the releases ``mirror-<id>-<tag>`` of this
repository, written by ``scripts/tools/mirror_upstreams.py``), never from the
upstream. Each copy has a ``manifest.json`` with the SHA256 of every file of
our copy and the upstream sum it was checked against (``verified``).

``GET /api/v2/downloads/{tool_id}`` returns the newest copied release grouped
by platform, the other copied releases, the source copies and the licence.
Mister Download Manager is GPL-3.0: it stays a separate program with its own
licence and source; no AGOS code links to it.

Failures are reported (``error`` / 503), never hidden; a release copied before
R-206d has no manifest until the next mirror run and is shown as unverified.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from agos_v2.tools_hub import StatusCache, load_registry, mirror_repo, status_url

logger = logging.getLogger(__name__)

MANIFEST_TTL_S = 600.0
RETRY_AFTER_FAIL_S = 60.0
MAX_MANIFEST_BYTES = 1024 * 1024
LICENSE_URLS = {"GPL-3.0": "https://www.gnu.org/licenses/gpl-3.0.html"}

# (group id, Arabic label, filename pattern) — first match wins
GROUPS: List[Tuple[str, str, "re.Pattern[str]"]] = [
    ("checksums", "المجاميع والتوقيع", re.compile(r"^SHA256SUMS", re.I)),
    ("symbols", "رموز التنقيح (للمطورين)", re.compile(r"-symbols-", re.I)),
    ("windows", "ويندوز", re.compile(r"(\.exe$|-x64\.zip$|-portable\.zip$|windows)", re.I)),
    ("macos", "ماك", re.compile(r"(\.dmg$|macos)", re.I)),
    ("linux", "لينكس", re.compile(r"(\.AppImage(\.zsync)?$|\.deb$|\.rpm$|linux)", re.I)),
    ("extensions", "إضافات المتصفح", re.compile(r"(extension|chrome-web-store|\.xpi$)", re.I)),
]
OTHER = ("other", "ملفات بيانات مرافقة")
_VER_RE = re.compile(r"\d+")


def group_of(name: str) -> str:
    for gid, _label, rx in GROUPS:
        if rx.search(name):
            return gid
    return OTHER[0]


def version_key(tag: str) -> Tuple:
    return tuple(int(x) for x in _VER_RE.findall(tag)) or (0,)


def _http_get(url: str, timeout: float = 8.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "agos-downloads"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https URL
        body = resp.read(MAX_MANIFEST_BYTES + 1)
    if len(body) > MAX_MANIFEST_BYTES:
        raise ValueError("manifest too large")
    return body


def validate_manifest(m: Any, tool_id: str, tag: str) -> Dict[str, Any]:
    if not isinstance(m, dict) or m.get("format") != 1:
        raise ValueError("unexpected manifest format")
    if m.get("id") != tool_id or m.get("tag") != tag:
        raise ValueError("manifest is for another tool or release")
    assets = m.get("assets")
    if not isinstance(assets, list) or not all(
            isinstance(a, dict) and isinstance(a.get("name"), str) and "/" not in a["name"]
            and isinstance(a.get("sha256"), str) and len(a["sha256"]) == 64 for a in assets):
        raise ValueError("manifest assets malformed")
    return m


class ManifestCache:
    def __init__(self, fetch: Callable[[str], bytes] = _http_get, ttl: float = MANIFEST_TTL_S,
                 clock: Callable[[], float] = time.monotonic):
        self._fetch, self._ttl, self._clock = fetch, ttl, clock
        self._entries: Dict[str, Tuple[float, Optional[Dict[str, Any]], Optional[str]]] = {}
        self._lock = asyncio.Lock()

    async def get(self, url: str, tool_id: str, tag: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        async with self._lock:
            now = self._clock()
            hit = self._entries.get(url)
            if hit is not None:
                at, data, err = hit
                if now - at < (self._ttl if err is None else RETRY_AFTER_FAIL_S):
                    return data, err
            prev = hit[1] if hit else None
            try:
                raw = await asyncio.to_thread(self._fetch, url)
                data, err = validate_manifest(json.loads(raw.decode("utf-8")), tool_id, tag), None
            except Exception as exc:  # reported; last good copy kept
                data, err = prev, f"{type(exc).__name__}: {str(exc)[:160]}"
                logger.warning("downloads: manifest %s unavailable (%s)", url, err)
            self._entries[url] = (now, data, err)
            return data, err


def build_page(entry: Dict[str, Any], status_rec: Dict[str, Any], repo: str, tag: str,
               manifest: Optional[Dict[str, Any]], manifest_error: Optional[str]) -> Dict[str, Any]:
    base = f"https://github.com/{repo}/releases"
    mtag = f"mirror-{entry['id']}-{tag}"
    groups: Dict[str, Dict[str, Any]] = {}
    order = [g[0] for g in GROUPS] + [OTHER[0]]
    labels = {g[0]: g[1] for g in GROUPS} | {OTHER[0]: OTHER[1]}
    for a in (manifest or {}).get("assets", []):
        gid = group_of(a["name"])
        groups.setdefault(gid, {"id": gid, "label": labels[gid], "files": []})["files"].append({
            "name": a["name"], "size": a.get("size"), "sha256": a["sha256"],
            "upstream_sha256": a.get("upstream_sha256"), "verified": a.get("verified"),
            "url": f"{base}/download/{mtag}/{a['name']}"})
    platform_first = ["windows", "macos", "linux", "extensions", "other", "symbols", "checksums"]
    tags = sorted(status_rec.get("mirrored_releases") or [], key=version_key, reverse=True)
    lic = entry.get("license")
    return {
        "id": entry["id"], "name": entry.get("name"), "license": lic, "license_url": LICENSE_URLS.get(lic),
        "license_note": "برنامج مستقل برخصته وكوده؛ لا يرتبط بكود AGOS (D-165).",
        "upstream_url": f"https://github.com/{entry['repo']}",
        "upstream_state": status_rec.get("upstream_state"),
        "release": {"tag": tag, "mirror_page": f"{base}/tag/{mtag}",
                    "upstream_page": f"https://github.com/{entry['repo']}/releases/tag/{tag}"},
        "verification": {
            "state": ("verified" if manifest and manifest.get("ok") else
                      "failed" if manifest else "not_verified_yet"),
            "verified_at": (manifest or {}).get("verified_at"),
            "sums_source": (manifest or {}).get("sums_source"),
            "mismatched": (manifest or {}).get("mismatched") or [],
            "missing": (manifest or {}).get("missing") or [],
            "error": manifest_error},
        "groups": [groups[g] for g in sorted(groups, key=lambda g: platform_first.index(g)
                                             if g in platform_first else len(order))],
        "releases": [{"tag": t, "mirror_page": f"{base}/tag/mirror-{entry['id']}-{t}",
                      "verified": t in (status_rec.get("verified_releases") or [])} for t in tags],
        "source": {"bundle_url": f"{base}/download/mirror-{entry['id']}/{entry['id']}.bundle",
                   "source_url": f"{base}/download/mirror-{entry['id']}/{entry['id']}-src.tar.gz",
                   "snapshot_head": status_rec.get("snapshot_head"),
                   "snapshot_at": status_rec.get("snapshot_at")},
    }


def build_router(status_cache: Optional[StatusCache] = None, manifests: Optional[ManifestCache] = None,
                 repo: Optional[str] = None, registry: Optional[List[Dict[str, Any]]] = None,
                 rate_limiter: Optional[Callable] = None) -> APIRouter:
    repo = repo or mirror_repo()
    status_cache = status_cache or StatusCache(status_url(repo))
    manifests = manifests or ManifestCache()
    reg = registry if registry is not None else load_registry()
    tools = {t["id"]: t for t in reg if t.get("repo") and t.get("mirror_releases")}
    r = APIRouter(prefix="/api/v2", tags=["v2 downloads"])

    @r.get("/downloads/{tool_id}")
    async def downloads(tool_id: str, request: Request):
        if rate_limiter is not None:
            await rate_limiter(request)
        entry = tools.get(tool_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="no downloads for this tool")
        status = await status_cache.get()
        rec = next((t for t in (status or {}).get("tools", [])
                    if isinstance(t, dict) and t.get("id") == tool_id), None)
        tags = sorted((rec or {}).get("mirrored_releases") or [], key=version_key, reverse=True)
        if not tags:
            return JSONResponse(status_code=503, headers={"Retry-After": "600"}, content={
                "ok": False, "error": "no release copied yet",
                "detail": status_cache.error, "source": status_cache.url})
        tag = tags[0]
        url = f"https://github.com/{repo}/releases/download/mirror-{tool_id}-{tag}/manifest.json"
        manifest, err = await manifests.get(url, tool_id, tag)
        return {"ok": manifest is not None, **build_page(entry, rec, repo, tag, manifest, err),
                "status_error": status_cache.error}

    return r
