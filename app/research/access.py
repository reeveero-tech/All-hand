"""Access policy shared by the research manager and the crawler: robots.txt, per-host pacing, refusals.

- robots.txt is read once per host (through the same SSRF-safe reader as every other request) and its rules for
  our user agent are applied before any automated request. Following RFC 9309: a 4xx robots.txt means no
  restrictions; a 5xx or unreachable one means "assume disallowed" for crawling.
- Crawl-delay is honoured as the minimum spacing between requests to that host. If it is longer than
  MAX_CRAWL_DELAY the host is skipped for this run (recorded), rather than ignored.
- A 429 puts the host on hold for its Retry-After (or a default); repeated 401/403, a login form or a
  CAPTCHA/challenge page stop automated requests to that host for the run. Nothing here tries to get past a
  login, a CAPTCHA, a block or a paywall: the refusal is recorded and another source is used.
"""
from __future__ import annotations

import asyncio
import re
import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

AGENT_TOKEN = "All-hand-agent"
MAX_CRAWL_DELAY = 20.0
DEFAULT_INTERVAL = 1.0
HOLD_429 = 60.0
MAX_HOLD = 300.0
BLOCKS_TO_STOP = 2

_CHALLENGE = re.compile(r"captcha|g-recaptcha|hcaptcha|cf-challenge|cf_chl_|challenge-platform|verify you are (a )?human|"
                        r"are you a robot|just a moment\.\.\.|attention required|unusual traffic|أثبت أنك لست روبوت", re.I)
_LOGIN = re.compile(r"<input[^>]+type=[\"']?password", re.I)
_PAYWALL = re.compile(r"subscribe to (continue|read)|to continue reading|purchase (this )?article|access through your institution|"
                      r"اشترك لمتابعة القراءة", re.I)


def host_of(url: str) -> str:
    try:
        h = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return h[4:] if h.startswith("www.") else h


def wall(raw_html: str) -> str | None:
    """'challenge', 'login' or 'paywall' when a 2xx page is really a gate in front of the content."""
    s = (raw_html or "")[:400_000]
    visible = re.sub(r"(?is)<(script|style|noscript|template)\b.*?</\1>", " ", s)
    visible = re.sub(r"<[^>]+>", " ", visible)
    text_len = len(re.sub(r"\s+", "", visible))
    # A contact form with reCAPTCHA or a header login box is not a wall: only a page with little else is.
    if _CHALLENGE.search(s) and text_len < 2500:
        return "challenge"
    if _LOGIN.search(s) and text_len < 1200:
        return "login"
    if _PAYWALL.search(visible) and text_len < 5000:
        return "paywall"
    return None


class Access:
    def __init__(self, fetch=None, min_interval: float = DEFAULT_INTERVAL, max_crawl_delay: float = MAX_CRAWL_DELAY,
                 respect_robots: bool = True, clock=time.monotonic, sleep=asyncio.sleep):
        self._fetch = fetch  # async (url) -> fetch_raw dict
        self.min_interval, self.max_crawl_delay, self.respect_robots = min_interval, max_crawl_delay, respect_robots
        self.clock, self.sleep = clock, sleep
        self.robots: dict[str, dict] = {}
        self.next_at: dict[str, float] = {}
        self.hold_until: dict[str, float] = {}
        self.blocks: dict[str, int] = {}
        self.stopped: dict[str, str] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self.stats = {"robots_fetched": 0, "robots_disallowed": 0, "waited_s": 0.0, "holds": 0}

    async def _robots(self, url: str) -> dict:
        p = urlparse(url)
        key = f"{p.scheme}://{p.netloc}".lower()
        if key in self.robots:
            return self.robots[key]
        entry = {"parser": None, "delay": None, "state": "none", "sitemaps": []}
        if self.respect_robots and self._fetch:
            self.stats["robots_fetched"] += 1
            raw = await self._fetch(f"{key}/robots.txt")
            status = raw.get("status")
            if raw.get("ok") and status and 200 <= status < 300:
                rp = RobotFileParser()
                lines = raw.get("body", b"")[:500_000].decode("utf-8", "replace").splitlines()
                rp.parse(lines)
                entry.update(parser=rp, state="rules", delay=rp.crawl_delay(AGENT_TOKEN),
                             sitemaps=[ln.split(":", 1)[1].strip() for ln in lines if ln.lower().startswith("sitemap:")][:10])
            elif raw.get("ok") and status and 400 <= status < 500:
                entry["state"] = "none"  # RFC 9309 2.3.1.3: unavailable -> no restrictions
            else:
                entry["state"] = "unreachable"  # RFC 9309 2.3.1.4: unreachable -> assume complete disallow
        self.robots[key] = entry
        return entry

    async def check(self, url: str, crawl: bool = False) -> str | None:
        """None if an automated request may go now (after waiting its turn), else the reason it may not."""
        host = host_of(url)
        if host in self.stopped:
            return self.stopped[host]
        if self.clock() < self.hold_until.get(host, 0):
            return "host_on_hold"
        rb = await self._robots(url)
        if rb["state"] == "unreachable" and crawl:
            return "robots_unreachable"
        if rb["parser"] is not None and not rb["parser"].can_fetch(AGENT_TOKEN, url):
            self.stats["robots_disallowed"] += 1
            return "robots_disallowed"
        delay = max(self.min_interval, float(rb["delay"] or 0))
        if delay > self.max_crawl_delay:
            return "crawl_delay_too_long"
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            wait = self.next_at.get(host, 0) - self.clock()
            if wait > 0:
                self.stats["waited_s"] += wait
                await self.sleep(wait)
            self.next_at[host] = self.clock() + delay
        return None

    def allowed_host(self, url: str) -> bool:
        """False once the host has refused access in this run (it is not asked again)."""
        return host_of(url) not in self.stopped

    def sitemaps(self, url: str) -> list[str]:
        p = urlparse(url)
        return (self.robots.get(f"{p.scheme}://{p.netloc}".lower()) or {}).get("sitemaps") or []

    def record(self, url: str, failure: str | None, retry_after: str | None = None) -> None:
        """Learn from an answer: back off after 429, stop after repeated refusals or a gate page."""
        host = host_of(url)
        if failure == "rate_limited":
            try:
                hold = min(MAX_HOLD, float(retry_after)) if retry_after else HOLD_429
            except ValueError:
                hold = HOLD_429
            self.hold_until[host] = self.clock() + hold
            self.stats["holds"] += 1
        elif failure in ("blocked", "access_wall"):
            self.blocks[host] = self.blocks.get(host, 0) + 1
            if failure == "access_wall" or self.blocks[host] >= BLOCKS_TO_STOP:
                self.stopped[host] = "host_refused_access"
        elif failure is None:
            self.blocks[host] = 0

    def to_dict(self) -> dict:
        return {"stopped": dict(self.stopped), "blocks": dict(self.blocks), "stats": dict(self.stats)}

    def load(self, d: dict | None) -> None:
        d = d or {}
        self.stopped.update(d.get("stopped") or {})
        self.blocks.update(d.get("blocks") or {})
