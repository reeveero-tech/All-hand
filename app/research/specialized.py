"""Specialized source connectors. Each is independent, keyless unless stated, and verified to answer from a server:

- Crossref (api.crossref.org, public REST API, no key; a `mailto` puts requests in the "polite" pool):
  scholarly works with DOI, publisher and publication date.
- arXiv (export.arxiv.org/api, public Atom API, no key; asks for at most one request every 3 seconds).
- GitHub repositories (api.github.com/search, no key: 10 searches/minute per IP; GITHUB_TOKEN raises it).
- SearXNG (self-hosted metasearch, AGPL-3.0): only when SEARXNG_URL points to an instance with JSON output
  enabled. Not hosted by default; see docs/research/RESEARCH_ENGINE.md.
- OpenAlex: only with OPENALEX_API_KEY (its keyless budget is shared per IP and was exhausted when checked).

Results carry `published` when the source states it, so freshness can be judged without guessing.
"""
from __future__ import annotations

import asyncio
import html
import os
import re
import time

import httpx

from . import registry

UA = "Mozilla/5.0 (compatible; All-hand-agent/1.0; +https://github.com/reeveero-tech/All-hand)"
MAILTO = os.environ.get("RESEARCH_MAILTO", "")


def _strip(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


async def crossref(query: str) -> list[dict]:
    params = {"query": query, "rows": 6, "select": "DOI,title,container-title,published,publisher,URL,abstract,type"}
    if MAILTO:
        params["mailto"] = MAILTO
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": UA}) as c:
        r = await c.get("https://api.crossref.org/works", params=params)
    r.raise_for_status()
    out = []
    for it in r.json().get("message", {}).get("items", []):
        parts = ((it.get("published") or {}).get("date-parts") or [[None]])[0]
        date = "-".join(f"{p:02d}" if i else str(p) for i, p in enumerate(parts) if p) if parts and parts[0] else ""
        venue = (it.get("container-title") or [""])[0]
        out.append({"title": _strip((it.get("title") or [""])[0]), "url": it.get("URL") or f"https://doi.org/{it.get('DOI')}",
                    "snippet": " — ".join(x for x in (it.get("publisher", ""), venue, date, _strip(it.get("abstract", ""))[:300]) if x),
                    "published": date, "publisher": it.get("publisher", ""), "source_type": "academic"})
    return out


_arxiv_gate = {"next": 0.0, "lock": None}


async def arxiv(query: str) -> list[dict]:
    if _arxiv_gate["lock"] is None:
        _arxiv_gate["lock"] = asyncio.Lock()
    async with _arxiv_gate["lock"]:  # arXiv's terms: no more than one request every three seconds
        wait = _arxiv_gate["next"] - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            async with httpx.AsyncClient(timeout=20, headers={"User-Agent": UA}) as c:
                r = await c.get("https://export.arxiv.org/api/query",
                                params={"search_query": f"all:{query}", "max_results": 5, "sortBy": "relevance"})
        finally:
            _arxiv_gate["next"] = time.monotonic() + 3.0
    r.raise_for_status()
    out = []
    for e in re.findall(r"<entry>(.*?)</entry>", r.text, re.S):
        def tag(n):
            m = re.search(rf"<{n}[^>]*>(.*?)</{n}>", e, re.S)
            return _strip(m.group(1)) if m else ""
        link = re.search(r'<link href="([^"]+)"[^>]*rel="alternate"', e)
        out.append({"title": tag("title"), "url": link.group(1) if link else tag("id"),
                    "snippet": f"{tag('published')[:10]} — {tag('summary')[:300]}", "published": tag("published")[:10],
                    "publisher": "arXiv", "source_type": "academic"})
    return out


async def github_repos(query: str) -> list[dict]:
    headers = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    async with httpx.AsyncClient(timeout=20, headers=headers) as c:
        r = await c.get("https://api.github.com/search/repositories", params={"q": query, "per_page": 6})
    r.raise_for_status()
    return [{"title": x["full_name"], "url": x["html_url"],
             "snippet": f"{(x.get('license') or {}).get('spdx_id') or 'no license'} — {x.get('stargazers_count', 0)}★ — "
                        f"updated {str(x.get('pushed_at', ''))[:10]} — {(x.get('description') or '')[:200]}",
             "published": str(x.get("pushed_at", ""))[:10], "publisher": "GitHub", "source_type": "code"}
            for x in r.json().get("items", [])]


async def searxng(query: str) -> list[dict]:
    base = os.environ.get("SEARXNG_URL", "").rstrip("/")
    lang = "ar" if re.search(r"[\u0600-\u06FF]", query) else "en"
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": UA}) as c:
        r = await c.get(f"{base}/search", params={"q": query, "format": "json", "language": lang})
    r.raise_for_status()
    return [{"title": x.get("title", ""), "url": x.get("url", ""), "snippet": _strip(x.get("content", ""))[:400],
             "published": str(x.get("publishedDate") or "")[:10], "engines": x.get("engines", [])}
            for x in r.json().get("results", [])[:8]]


async def openalex(query: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": UA}) as c:
        r = await c.get("https://api.openalex.org/works",
                        params={"search": query, "per-page": 6, "api_key": os.environ["OPENALEX_API_KEY"]})
    r.raise_for_status()
    return [{"title": x.get("display_name", ""), "url": x.get("doi") or x.get("id", ""),
             "snippet": f"{x.get('publication_date', '')} — cited {x.get('cited_by_count', 0)}",
             "published": x.get("publication_date", ""), "source_type": "academic"}
            for x in r.json().get("results", [])]


KINDS = {"academic": ["Crossref", "arXiv", "OpenAlex"], "code": ["GitHub"], "web": ["SearXNG"]}


def register_all() -> None:
    registry.register("Crossref", "academic", crossref, note="api.crossref.org, keyless")
    registry.register("arXiv", "academic", arxiv, note="export.arxiv.org, keyless, 1 request / 3 s")
    registry.register("GitHub", "code", github_repos, keyless=not os.environ.get("GITHUB_TOKEN"),
                      note="10 searches/min keyless")
    if os.environ.get("SEARXNG_URL"):
        registry.register("SearXNG", "web", searxng, note="self-hosted instance")
    if os.environ.get("OPENALEX_API_KEY"):
        registry.register("OpenAlex", "academic", openalex, keyless=False)


def available(kind: str) -> list[str]:
    return [n for n in KINDS.get(kind, []) if n in registry.REGISTRY]
