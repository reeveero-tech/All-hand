"""Bounded crawler on top of the SSRF-safe reader (tools.read_url / tools.fetch_raw). No second HTTP stack.

What it does: read a sitemap (and sitemap indexes, two levels), read an RSS/Atom feed, follow internal links of
a site to a set depth, without visiting a page twice, within a page cap, a requests-per-minute cap and a
per-request timeout. Its state (frontier, visited, results) is plain data, so a crawl stopped by a restart is
continued from where it was.

What it refuses to do: leave the seed's site (unless allowed), reach a private address (the reader refuses it
on every hop), ignore robots.txt or Crawl-delay, or try to pass a login, a CAPTCHA, a block or a paywall. A
refusal is recorded with its reason, and the crawl moves on to other pages or stops for that host.
"""
from __future__ import annotations

import asyncio
import html
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

from . import access as acc
from .registry import normalize_url

MAX_PAGES = 20
MAX_DEPTH = 1
RPM = 20
TIMEOUT_S = 20.0
MAX_SITEMAP_URLS = 300
_SKIP_EXT = re.compile(r"\.(jpg|jpeg|png|gif|webp|svg|ico|css|js|mp4|mp3|zip|gz|rar|7z|exe|dmg|woff2?|ttf)(\?|$)", re.I)


def same_site(a: str, b: str) -> bool:
    ha, hb = acc.host_of(a), acc.host_of(b)
    return bool(ha) and (ha == hb or ha.endswith("." + hb) or hb.endswith("." + ha))


def parse_sitemap(xml: str) -> dict:
    """{"urls": [...], "sitemaps": [...]} from a sitemap or a sitemap index (regex: no XML entity expansion)."""
    locs = [html.unescape(x.strip()) for x in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml or "", re.I)]
    if re.search(r"<sitemapindex", xml or "", re.I):
        return {"urls": [], "sitemaps": locs[:50]}
    return {"urls": locs[:MAX_SITEMAP_URLS], "sitemaps": []}


def parse_feed(xml: str) -> list[dict]:
    """Items of an RSS 2.0 or Atom feed: title, url, published."""
    out = []
    for blk in re.findall(r"<item\b.*?</item>|<entry\b.*?</entry>", xml or "", re.S | re.I)[:100]:
        def tag(n):
            m = re.search(rf"<{n}\b[^>]*>(.*?)</{n}>", blk, re.S | re.I)
            return html.unescape(re.sub(r"<!\[CDATA\[|\]\]>|<[^>]+>", "", m.group(1))).strip() if m else ""
        link = tag("link")
        if not link:
            m = re.search(r"<link\b[^>]*href=[\"']([^\"']+)", blk, re.I)
            link = m.group(1) if m else ""
        if link.startswith("http"):
            out.append({"title": tag("title")[:200], "url": link,
                        "published": (tag("pubDate") or tag("published") or tag("updated") or tag("dc:date"))[:40]})
    return out


@dataclass
class CrawlState:
    seeds: list = field(default_factory=list)
    frontier: list = field(default_factory=list)  # [[url, depth]]
    visited: list = field(default_factory=list)  # normalized URLs already requested
    pages: list = field(default_factory=list)  # [{url, status, failure, kind, title, depth, tables, chars}]
    refused: list = field(default_factory=list)  # [{url, reason}]
    requests: int = 0
    done: bool = False

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("seeds", "frontier", "visited", "pages", "refused", "requests", "done")}

    @classmethod
    def from_dict(cls, d: dict | None) -> "CrawlState":
        s = cls()
        for k, v in (d or {}).items():
            if hasattr(s, k):
                setattr(s, k, v)
        return s


class Crawler:
    def __init__(self, read, fetch, policy: acc.Access | None = None, max_pages: int = MAX_PAGES,
                 max_depth: int = MAX_DEPTH, rpm: int = RPM, timeout: float = TIMEOUT_S, allow_offsite: bool = False,
                 clock=time.monotonic, sleep=asyncio.sleep, on_page=None):
        self.read, self.fetch = read, fetch  # read: tools.read_url; fetch: tools.fetch_raw
        self.policy = policy or acc.Access(fetch=fetch, clock=clock, sleep=sleep)
        self.max_pages, self.max_depth, self.rpm, self.timeout = max_pages, max_depth, rpm, timeout
        self.allow_offsite, self.clock, self.sleep, self.on_page = allow_offsite, clock, sleep, on_page
        self._window: list[float] = []

    async def _rate(self):
        """At most `rpm` requests in any 60 s window (all hosts together)."""
        now = self.clock()
        self._window = [t for t in self._window if now - t < 60]
        if len(self._window) >= self.rpm:
            await self.sleep(60 - (now - self._window[0]))
        self._window.append(self.clock())

    async def _get_text(self, url: str) -> str | None:
        why = await self.policy.check(url, crawl=True)
        if why:
            return None
        await self._rate()
        try:
            raw = await asyncio.wait_for(self.fetch(url), timeout=self.timeout)
        except asyncio.TimeoutError:
            return None
        if not raw.get("ok") or not raw.get("status") or not 200 <= raw["status"] < 300:
            return None
        return raw["body"][:5_000_000].decode(raw.get("charset") or "utf-8", "replace")

    async def sitemap_urls(self, site: str, limit: int = MAX_SITEMAP_URLS) -> list[str]:
        """URLs listed by the site's sitemaps (from robots.txt, else /sitemap.xml), two index levels at most."""
        p = urlparse(site)
        root = f"{p.scheme}://{p.netloc}"
        await self.policy.check(root + "/", crawl=True)  # loads robots.txt (and its Sitemap: lines)
        todo, seen, urls = (self.policy.sitemaps(root + "/") or [root + "/sitemap.xml"]), set(), []
        for level in range(2):
            nxt = []
            for sm in todo[:10]:
                if sm in seen or not same_site(sm, root):
                    continue
                seen.add(sm)
                xml = await self._get_text(sm)
                if not xml:
                    continue
                got = parse_sitemap(xml)
                urls += [u for u in got["urls"] if same_site(u, root)]
                nxt += got["sitemaps"]
                if len(urls) >= limit:
                    return urls[:limit]
            todo = nxt
        return urls[:limit]

    async def feed_items(self, feed_url: str) -> list[dict]:
        xml = await self._get_text(feed_url)
        return parse_feed(xml) if xml else []

    async def crawl(self, state: CrawlState) -> CrawlState:
        """Breadth-first over the frontier, within page, depth, rate and robots limits. Resumable."""
        if not state.frontier and not state.visited:
            state.frontier = [[u, 0] for u in state.seeds]
        visited = set(state.visited)
        while state.frontier and len(state.pages) < self.max_pages:
            url, depth = state.frontier.pop(0)
            key = normalize_url(url)
            if key in visited or _SKIP_EXT.search(url):
                continue
            if not self.allow_offsite and state.seeds and not any(same_site(url, s) for s in state.seeds):
                continue
            why = await self.policy.check(url, crawl=True)
            if why:
                state.refused.append({"url": url, "reason": why})
                visited.add(key)
                state.visited.append(key)
                continue
            await self._rate()
            visited.add(key)
            state.visited.append(key)
            state.requests += 1
            try:
                rd = await asyncio.wait_for(self.read(url), timeout=self.timeout + 5)
            except asyncio.TimeoutError:
                rd = {"url": url, "status": None, "failure": "timeout", "links": []}
            self.policy.record(url, rd.get("failure"), rd.get("retry_after"))
            page = {"url": rd.get("url") or url, "status": rd.get("status"), "failure": rd.get("failure"),
                    "kind": rd.get("kind", ""), "title": (rd.get("title") or "")[:160], "depth": depth,
                    "tables": len(rd.get("tables") or []), "chars": len(rd.get("text") or "")}
            state.pages.append(page)
            if self.on_page:
                await self.on_page(rd, page)
            if rd.get("failure") in ("access_wall", "blocked"):
                continue  # never try to get past it; other pages of the site may still be open
            if depth < self.max_depth:
                for link in rd.get("links") or []:
                    if normalize_url(link) not in visited and (self.allow_offsite or same_site(link, url)):
                        state.frontier.append([link, depth + 1])
        state.done = not state.frontier  # stopped at the page cap: not done, resumable with a larger cap
        return state
