"""Real tools the agents can call. Each returns plain text (truncated) and never raises."""
from __future__ import annotations

import asyncio
import html
import json
import ipaddress
import os
import re
import resource
import socket
import sys
import tempfile
import time
from urllib.parse import quote_plus, urlparse, parse_qs, unquote

import httpx

from .research import extract as rx
from .research import access, documents, registry, specialized
from .research.registry import content_hash

UA = "Mozilla/5.0 (compatible; All-hand-agent/1.0; +https://github.com/reeveero-tech/All-hand)"
MAX_OUT = 6000


def _clip(s: str, n: int = MAX_OUT) -> str:
    s = s.strip()
    return s if len(s) <= n else s[:n] + f"\n...[قُطع، {len(s) - n} حرفًا إضافيًا]"


def _html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript|svg|head).*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h\d|tr)>", "\n", raw)
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
    return re.sub(r"\n\s*\n+", "\n\n", raw).strip()


_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_BLOCKED_V6 = (ipaddress.ip_network("2002::/16"), ipaddress.ip_network("2001::/32"),  # 6to4, Teredo
               ipaddress.ip_network("64:ff9b:1::/48"))


def _ip_is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped:
            return _ip_is_public(ip.ipv4_mapped)
        if ip in _NAT64:
            return _ip_is_public(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
        if any(ip in n for n in _BLOCKED_V6):
            return False
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast
                or ip.is_unspecified or not ip.is_global)


def _resolve_public(host: str) -> str | None:
    """Resolve once and return one address to connect to, or None if ANY address is non-public.
    The caller connects to this exact address, so DNS cannot be re-pointed (rebinding) between
    the check and the connection."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, ValueError):
        return None
    ips = []
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0].split("%")[0])
        except ValueError:
            return None
        if not _ip_is_public(ip):
            return None
        ips.append(ip)
    return str(ips[0]) if ips else None


def _is_public_host(host: str) -> bool:
    return _resolve_public(host) is not None


MAX_REDIRECTS = 5
MAX_BYTES = 2_000_000
PAGE_MARK = "[الصفحة: "
TABLES_BUDGET = 2000


def fetch_meta(result: str, asked: str = "") -> dict:
    """What a fetch_url call really got, for the evidence ledger: status, failure class, page facts."""
    from .research.evidence import classify_failure
    first = (result or "").split("\n", 1)[0]
    m = re.match(r"HTTP (\d{3}) — (\S+)", first)
    page = {"url": m.group(2) if m else str(asked or "")[:500], "status": int(m.group(1)) if m else None,
            "failure": classify_failure(result)}
    pm = re.search(re.escape(PAGE_MARK) + r"([^\n]*)\]", result or "")
    if pm:
        for part in pm.group(1).split(" | "):
            k, _, v = part.partition(": ")
            key = {"الناشر": "publisher", "تاريخ النشر": "published", "جداول": "tables", "بصمة": "hash",
                   "نوع": "kind", "صفحات": "pages", "اكتمال": "completeness"}.get(k.strip())
            if key:
                page[key] = v.strip()
    return page


async def fetch_raw(url: str, timeout: float = 20.0) -> dict:
    """The one network reader under fetch_url, the research manager and the crawler. Each hop (including
    redirects) is resolved once, checked, and the connection is pinned to the checked address. The body is
    capped at MAX_BYTES, or DOC_MAX_BYTES when the server or URL announces a document (PDF, Office).
    Returns {"ok", "error" (message, when no HTTP answer), "status", "url" (final), "ctype", "charset",
    "body" (bytes), "truncated", "headers" (retry-after, last-modified, content-length)}. Never raises."""
    out = {"ok": False, "error": None, "status": None, "url": url, "ctype": "", "charset": None, "body": b"",
           "truncated": False, "headers": {}}
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as c:
            for _ in range(MAX_REDIRECTS + 1):
                p = urlparse(url or "")
                if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
                    return {**out, "error": "خطأ: رابط غير صالح (http/https فقط)."}
                ip = await asyncio.to_thread(_resolve_public, p.hostname)
                if not ip:
                    return {**out, "error": "خطأ: العناوين الداخلية/الخاصة ممنوعة."}
                target = httpx.URL(url)
                host_header = target.host if target.port is None else f"{target.host}:{target.port}"
                req = c.build_request("GET", target.copy_with(host=ip),
                                      headers={"User-Agent": UA, "Host": host_header},
                                      extensions={"sni_hostname": target.host})
                r = await c.send(req, stream=True)
                try:
                    if r.is_redirect and r.headers.get("location"):
                        url = str(target.join(r.headers["location"]))
                        continue
                    ctype = r.headers.get("content-type", "")
                    cap = documents.DOC_MAX_BYTES if documents.is_document_hint(ctype, url) else MAX_BYTES
                    chunks, size = [], 0
                    async for chunk in r.aiter_bytes():
                        chunks.append(chunk)
                        size += len(chunk)
                        if size >= cap:
                            break
                    raw = b"".join(chunks)
                    truncated = len(raw) >= cap
                    raw = raw[:cap]
                finally:
                    await r.aclose()
                break
            else:
                return {**out, "error": "خطأ: تحويلات كثيرة."}
        hdr = {k: r.headers.get(k) for k in ("retry-after", "last-modified", "content-length") if r.headers.get(k)}
        return {**out, "ok": True, "status": r.status_code, "url": url, "ctype": ctype, "charset": r.charset_encoding,
                "body": raw, "truncated": truncated, "headers": hdr}
    except Exception as e:  # noqa: BLE001
        return {**out, "error": f"خطأ في الجلب: {type(e).__name__}: {str(e)[:200]}"}


def _status_failure(status: int | None) -> str | None:
    if status is None:
        return "network_error"
    if 200 <= status < 300:
        return None
    if status in (401, 403, 451):
        return "blocked"
    if status == 429:
        return "rate_limited"
    if status in (404, 410):
        return "not_found"
    if status >= 500:
        return "server_error"
    return f"http_{status}"


async def read_url(url: str, max_chars: int = 120_000, main_text: bool = True) -> dict:
    """Open a URL and return what was really read, as data (the research manager records it as evidence):
    {"url", "asked", "status", "failure", "error", "kind", "ctype", "method", "text", "tables" [[row]],
     "offers", "title", "publisher", "published", "modified", "lang", "hash", "pages_total", "pages_read",
     "ocr_pages", "completeness" (full|partial|needs_ocr), "notes", "retry_after", "body_text" (non-2xx bodies)}.
    `failure` is None only when readable content came out."""
    raw = await fetch_raw(url)
    rd = {"url": raw["url"], "asked": url, "status": raw["status"], "failure": None, "error": raw["error"],
          "kind": "", "ctype": raw["ctype"], "method": "", "text": "", "tables": [], "offers": [], "title": "",
          "publisher": "", "published": "", "modified": "", "lang": "", "last_modified": raw["headers"].get("last-modified", ""),
          "hash": "", "pages_total": 0, "pages_read": 0, "ocr_pages": [], "completeness": "", "notes": [],
          "retry_after": raw["headers"].get("retry-after", ""), "body_text": "", "full_text": "", "links": [], "wall": ""}
    if not raw["ok"]:
        from .research.evidence import classify_failure
        rd["failure"] = classify_failure(raw["error"] or "")
        return rd
    body, ctype = raw["body"], raw["ctype"]
    s = documents.sniff(body, ctype, raw["url"])
    rd["kind"] = s["kind"]
    if s["mismatch"]:
        rd["notes"].append(s["mismatch"])
    if not 200 <= raw["status"] < 300:
        rd["failure"] = _status_failure(raw["status"])
        if s["kind"] in ("html", "text", "json", "xml"):
            txt = body.decode(raw["charset"] or "utf-8", errors="replace")
            rd["body_text"] = _html_to_text(txt) if s["kind"] == "html" else txt
        return rd
    if s["kind"] in documents.DOC_KINDS:
        if raw["truncated"]:
            rd.update(failure="too_large", method="httpx")
            return rd
        d = await documents.parse(body, ctype, raw["url"])
        rd["method"] = "httpx+" + (d.get("method") or s["kind"])
        rd["notes"] += list(d.get("notes") or [])
        if not d.get("ok"):
            rd["failure"] = "unreadable_document"
            rd["notes"].append(str(d.get("error") or ""))
            return rd
        meta = d.get("meta") or {}
        rd.update(text=(d.get("text") or "")[:max_chars], tables=documents.tables_to_rows(d.get("tables")),
                  title=meta.get("title", ""), published=meta.get("created", "") or meta.get("modified", ""),
                  pages_total=d.get("pages_total") or 0, pages_read=d.get("pages_read") or 0,
                  ocr_pages=d.get("ocr_pages") or [], completeness=d.get("completeness") or "full")
        if rd["completeness"] == "needs_ocr" or not (rd["text"].strip() or rd["tables"]):
            rd["failure"] = "needs_ocr" if rd["ocr_pages"] else "empty_content"
        rd["hash"] = content_hash(rd["text"] + json.dumps(rd["tables"], ensure_ascii=False))
        return rd
    if s["kind"] in ("html", "text", "json", "xml"):
        txt = body.decode(raw["charset"] or "utf-8", errors="replace")
        if s["kind"] == "html" or "html" in ctype:
            info = rx.extract(txt)
            full = _html_to_text(txt)
            main = await documents.main_text(body, raw["charset"] or "utf-8") if main_text else None
            use_main = bool(main) and len(main) >= 200
            rd.update(kind="html", text=(main if use_main else full)[:max_chars], tables=info["tables"],
                      offers=info["offers"], title=info["title"], publisher=info["publisher"],
                      published=info["published"], modified=info["modified"], lang=info["lang"],
                      method="httpx+" + ("trafilatura" if use_main else "html_parser"), full_text=full)
            rd["hash"] = content_hash(full)
            rd["links"] = page_links(txt, raw["url"])
            gate = access.wall(txt)
            if gate:  # a 2xx answer that is really a login, CAPTCHA or paywall page: not content, not bypassed
                rd.update(failure="access_wall", wall=gate)
                return rd
        else:
            rd.update(text=txt[:max_chars], method="httpx")
            rd["hash"] = content_hash(txt)
        rd["completeness"] = "partial" if raw["truncated"] or len(rd["text"]) >= max_chars else "full"
        if len(re.sub(r"\s+", "", rd["text"])) < 40 and not rd["tables"] and not rd["offers"]:
            rd["failure"] = "empty_content"
        return rd
    rd["failure"] = "non_text"
    return rd


_HREF = re.compile(r"""<a\s[^>]*?href\s*=\s*["']([^"'#]+)""", re.I)


def page_links(raw_html: str, base: str, limit: int = 400) -> list[str]:
    """Absolute http(s) links of a page, in order, without duplicates (for the crawler)."""
    out, seen = [], set()
    try:
        b = httpx.URL(base)
    except Exception:  # noqa: BLE001
        return []
    for h in _HREF.findall(raw_html or ""):
        h = html.unescape(h.strip())
        if h.startswith(("mailto:", "javascript:", "tel:", "data:")):
            continue
        try:
            u = str(b.join(h))
        except Exception:  # noqa: BLE001
            continue
        if u.startswith(("http://", "https://")) and u not in seen:
            seen.add(u)
            out.append(u)
            if len(out) >= limit:
                break
    return out


DOC_LABEL = {"pdf": "PDF", "docx": "Word", "xlsx": "Excel"}


async def fetch_url(url: str, max_chars: int = MAX_OUT) -> str:
    """Read a public page or document as text for an agent (the same reader as the research manager)."""
    raw = await fetch_raw(url)
    if not raw["ok"]:
        return raw["error"]
    r_url, ctype, body = raw["url"], raw["ctype"], raw["body"]
    s = documents.sniff(body, ctype, r_url)
    if s["kind"] in documents.DOC_KINDS and 200 <= raw["status"] < 300:
        if raw["truncated"]:
            return f"HTTP {raw['status']} — {r_url}\n[مستند غير مقروء: أكبر من الحد ({documents.DOC_MAX_BYTES // 2**20} MB)]"
        d = await documents.parse(body, ctype, r_url)
        if not d.get("ok"):
            return f"HTTP {raw['status']} — {r_url}\n[مستند غير مقروء: {DOC_LABEL.get(s['kind'], s['kind'])} — {d.get('error')}]"
        text = d.get("text") or ""
        tables = documents.tables_to_rows(d.get("tables"))
        bits = [f"نوع: {DOC_LABEL[s['kind']]}"]
        if d.get("pages_total"):
            bits.append(f"صفحات: {d.get('pages_read')}/{d.get('pages_total')}")
        if d.get("ocr_pages"):
            bits.append(f"يحتاج OCR: {len(d['ocr_pages'])} صفحة")
        bits.append(f"اكتمال: {d.get('completeness')}")
        bits += [f"جداول: {len(tables)}", f"بصمة: {content_hash(text + json.dumps(tables, ensure_ascii=False))}"]
        head = [f"HTTP {raw['status']} — {r_url}", PAGE_MARK + " | ".join(bits) + "]"]
        if d.get("completeness") == "needs_ocr" or not (text.strip() or tables):
            head.append("[يحتاج OCR: صفحات ممسوحة ضوئيًا بلا طبقة نص؛ لم يُستخرج منها محتوى، فلا تنسب إليها أرقامًا]")
        if tables:
            head.append(rx.tables_markdown(tables, TABLES_BUDGET))
        return _clip("\n".join(head) + "\n\n" + text, max_chars)
    if "text" in ctype or "json" in ctype or "xml" in ctype:
        txt = body.decode(raw["charset"] or "utf-8", errors="replace")
    else:
        txt = f"[محتوى غير نصي: {ctype}]"
    if "html" not in ctype:
        return _clip(f"HTTP {raw['status']} — {r_url}\n\n{txt}", max_chars)
    text = _html_to_text(txt)
    info = rx.extract(txt)  # what the page states about itself, verbatim: dates, publisher, tables, offers
    head = [f"HTTP {raw['status']} — {r_url}"]
    line = rx.summary_line(info)
    head.append(PAGE_MARK + " | ".join([x for x in (line,) if x] + [f"جداول: {len(info['tables'])}", f"بصمة: {content_hash(text)}"]) + "]")
    if info["offers"]:
        head.append("عروض منظمة في الصفحة (Schema.org): " + "؛ ".join(
            f"{o['name']} = {o['price']} {o['currency']}".strip() for o in info["offers"][:8]))
    if info["tables"]:
        head.append(rx.tables_markdown(info["tables"], TABLES_BUDGET))
    return _clip("\n".join(head) + "\n\n" + text, max_chars)


# DuckDuckGo answers a burst of requests from one server with 202 (bot check) and then keeps refusing for
# minutes, and probing during the block extends it (measured 2026-11-25: 10/10 sequential queries spaced 1-2 s
# succeeded; about eight back-to-back ones triggered a block lasting more than 5 minutes). Parallel agents
# produced exactly that burst. So every DuckDuckGo request goes through one gate: spaced, cached, and after a
# refusal it is not called again until the cooldown passes (other providers answer meanwhile).
DDG_INTERVAL = float(os.environ.get("DDG_INTERVAL_S", "2.5"))
DDG_COOLDOWN = float(os.environ.get("DDG_COOLDOWN_S", "600"))
CACHE_TTL = 3600
_ddg_gate = {"lock": None, "next": 0.0, "cool_until": 0.0}
_cache: dict[str, tuple[float, list, str]] = {}
STATS = {"gnews_requests": 0, "ddg_requests": 0, "ddg_blocked": 0, "ddg_skipped_cooldown": 0, "cache_hits": 0}


class _Blocked(Exception):
    pass


async def _ddg_request(send):
    if _ddg_gate["lock"] is None:
        _ddg_gate["lock"] = asyncio.Lock()
    async with _ddg_gate["lock"]:
        now = time.monotonic()
        if now < _ddg_gate["cool_until"]:
            STATS["ddg_skipped_cooldown"] += 1
            raise _Blocked()
        if now < _ddg_gate["next"]:
            await asyncio.sleep(_ddg_gate["next"] - now)
        STATS["ddg_requests"] += 1
        try:
            r = await send()
        finally:
            _ddg_gate["next"] = time.monotonic() + DDG_INTERVAL
        if r.status_code in (202, 403, 429):
            STATS["ddg_blocked"] += 1
            _ddg_gate["cool_until"] = time.monotonic() + DDG_COOLDOWN
            raise _Blocked()
        return r


async def _ddg(query: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=10, follow_redirects=True, headers={"User-Agent": UA}) as c:
        r = await _ddg_request(lambda: c.get("https://lite.duckduckgo.com/lite/", params={"q": query}))
    if r.status_code != 200:
        return []
    links = re.findall(r"<a[^>]+href=\"([^\"]+)\"[^>]*class='result-link'[^>]*>(.*?)</a>", r.text, re.S)
    snippets = re.findall(r"<td class='result-snippet'>(.*?)</td>", r.text, re.S)
    out = []
    for i, (href, title) in enumerate(links[:8]):
        href = html.unescape(href)
        if "uddg=" in href:
            href = unquote(parse_qs(urlparse(href).query).get("uddg", [href])[0])
        out.append({"title": _html_to_text(title), "url": href,
                    "snippet": _html_to_text(snippets[i]) if i < len(snippets) else ""})
    return out


async def _ddg_html(query: str) -> list[dict]:
    """Second DuckDuckGo endpoint, behind the same gate."""
    async with httpx.AsyncClient(timeout=10, follow_redirects=True, headers={"User-Agent": UA}) as c:
        r = await _ddg_request(lambda: c.post("https://html.duckduckgo.com/html/", data={"q": query}))
    if r.status_code != 200:
        return []
    links = re.findall(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', r.text, re.S)
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', r.text, re.S)
    out = []
    for i, (href, title) in enumerate(links[:8]):
        href = html.unescape(href)
        if "uddg=" in href:
            href = unquote(parse_qs(urlparse(href).query).get("uddg", [href])[0])
        out.append({"title": _html_to_text(title), "url": href,
                    "snippet": _html_to_text(snippets[i]) if i < len(snippets) else ""})
    return out


async def _wikipedia(query: str, lang: str) -> list[dict]:
    url = (f"https://{lang}.wikipedia.org/w/api.php?action=query&list=search&format=json"
           f"&srlimit=5&srsearch={quote_plus(query)}")
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": UA}) as c:
        r = await c.get(url)
    if r.status_code != 200:
        return []
    return [{"title": h["title"], "url": f"https://{lang}.wikipedia.org/wiki/{quote_plus(h['title'].replace(' ', '_'))}",
             "snippet": _html_to_text(h.get("snippet", ""))} for h in r.json().get("query", {}).get("search", [])]


GNEWS_DECODE = 3  # Google News links are encoded; this many are resolved to the publisher's URL


async def _gnews_decode(c: httpx.AsyncClient, link: str) -> str | None:
    """Resolve a news.google.com/rss/articles/<id> link to the publisher's URL (keyless, two requests)."""
    gid = link.split("/articles/", 1)[-1].split("?", 1)[0]
    if not gid:
        return None
    sg = ts = None
    for path in (f"https://news.google.com/rss/articles/{gid}", f"https://news.google.com/articles/{gid}"):
        page = await c.get(path)
        h = page.text
        sg, ts = re.search(r'data-n-a-sg="([^"]+)"', h), re.search(r'data-n-a-ts="([^"]+)"', h)
        if sg and ts:
            break
        STATS["gnews_last_error"] = f"article page {page.status_code} {str(page.url)[:70]} len={len(h)}"
    if not (sg and ts):
        return None
    req = [[["Fbv4je", f'["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],'
                       f'"X","X",1,[1,1,1],1,1,null,0,0,null,0],"{gid}",{ts.group(1)},"{sg.group(1)}"]', None, "generic"]]]
    r = await c.post("https://news.google.com/_/DotsSplashUi/data/batchexecute", data={"f.req": json.dumps(req)},
                     headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
    try:
        url = json.loads(json.loads(r.text.split("\n\n", 1)[1])[0][2])[1]
    except Exception:  # noqa: BLE001
        STATS["gnews_last_error"] = f"batchexecute {r.status_code} {r.text[:80]!r}"
        if r.status_code == 429:  # stop decoding for a while instead of hammering
            STATS["gnews_429"] = STATS.get("gnews_429", 0) + 1
            STATS["gnews_cool_until"] = time.monotonic() + 300
        return None
    return url if isinstance(url, str) and url.startswith("http") else None


async def _gnews(query: str) -> list[dict]:
    """Google News RSS (keyless): recent articles, useful for markets, launches and competitors."""
    ar = bool(re.search(r"[\u0600-\u06FF]", query))
    loc = "hl=ar&gl=SA&ceid=SA:ar" if ar else "hl=en-US&gl=US&ceid=US:en"
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": UA}, follow_redirects=True,
                                 cookies={"CONSENT": "YES+cb", "SOCS": "CAI"}) as c:  # EU IPs get a consent page otherwise
        r = await c.get(f"https://news.google.com/rss/search?q={quote_plus(query)}&{loc}")
        if r.status_code != 200 or "<item>" not in r.text:
            STATS["gnews_last_error"] = f"rss {r.status_code} {str(r.url)[:60]}"
            return []
        items = re.findall(r"<item>(.*?)</item>", r.text, re.S)[:GNEWS_DECODE]

        def tag(it, name):
            m = re.search(rf"<{name}[^>]*>(.*?)</{name}>", it, re.S)
            return _html_to_text(m.group(1)) if m else ""
        urls: list = []
        for it in items[:GNEWS_DECODE]:  # one at a time: parallel decodes got HTTP 429 from Render's IP
            if STATS.get("gnews_429", 0) and time.monotonic() < STATS.get("gnews_cool_until", 0):
                break
            try:
                urls.append(await _gnews_decode(c, tag(it, "link")))
            except Exception as e:  # noqa: BLE001
                urls.append(e)
            await asyncio.sleep(1.0)
    bad = [u for u in urls if not isinstance(u, str)]
    if bad and len(bad) == len(urls) and isinstance(bad[0], Exception):
        STATS["gnews_last_error"] = f"decode: {type(bad[0]).__name__}"
    out = []
    for i, it in enumerate(items):
        u = urls[i] if i < len(urls) else None
        meta = f"{tag(it, 'source')} — {tag(it, 'pubDate')}".strip(" —")
        if isinstance(u, str):
            out.append({"title": tag(it, "title"), "url": u, "snippet": meta})
        elif tag(it, "link").startswith("https://news.google.com/"):
            # the publisher URL could not be resolved from this server (HTTP 429): the headline and publisher are
            # real and the link opens the article in a browser, but nothing of the article itself was read
            out.append({"title": tag(it, "title"), "url": tag(it, "link"),
                        "snippet": meta + f" — {HEADLINE_ONLY}؛ لم يُقرأ نص المقال، فلا تنسب إليه أرقامًا"})
    return out


_tavily_gate = {"cool_until": 0.0}
TAVILY_COOLDOWN = 600.0


async def _tavily(query: str) -> list[dict]:
    """Tavily search. With TAVILY_API_KEY it uses the owner's (free, 1,000 credits/month) key; without one it uses
    Tavily's documented keyless mode (free, rate-limited, no account: docs.tavily.com/documentation/keyless)."""
    key = os.environ.get("TAVILY_API_KEY")
    if not key and time.monotonic() < _tavily_gate["cool_until"]:
        STATS["tavily_skipped_cooldown"] = STATS.get("tavily_skipped_cooldown", 0) + 1
        return []
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    else:
        headers["X-Tavily-Access-Mode"] = "keyless"
    STATS["tavily_requests"] = STATS.get("tavily_requests", 0) + 1
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post("https://api.tavily.com/search", json={"query": query, "max_results": 6}, headers=headers)
    if r.status_code != 200:
        STATS["tavily_last_error"] = f"{r.status_code} {r.text[:120]!r}"
        if not key:  # keyless budget spent or refused: leave it alone for a while
            _tavily_gate["cool_until"] = time.monotonic() + TAVILY_COOLDOWN
        return []
    return [{"title": x.get("title", ""), "url": x.get("url", ""), "snippet": x.get("content", "")[:400]}
            for x in r.json().get("results", [])]


async def _brave(query: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get("https://api.search.brave.com/res/v1/web/search", params={"q": query, "count": 6},
                        headers={"X-Subscription-Token": os.environ["BRAVE_API_KEY"], "Accept": "application/json"})
    if r.status_code != 200:
        return []
    return [{"title": x.get("title", ""), "url": x.get("url", ""), "snippet": _html_to_text(x.get("description", ""))}
            for x in r.json().get("web", {}).get("results", [])]


def _register_general():
    """General web providers, looked up at call time so a replaced function (tests, reload) is the one used."""
    registry.register("Tavily", "web", lambda q: _tavily(q), keyless=not os.environ.get("TAVILY_API_KEY"),
                      note="keyless mode is rate-limited per IP (daily cap)")
    registry.register("Brave", "web", lambda q: _brave(q), keyless=False)
    registry.register("DuckDuckGo", "web", lambda q: _ddg(q), reraise=(_Blocked,), note="gated: spaced requests, cooldown after a refusal")
    registry.register("DuckDuckGo HTML", "web", lambda q: _ddg_html(q), reraise=(_Blocked,))
    registry.register("Google News", "news", lambda q: _gnews(q), note="headlines; publisher link decoded when allowed")
    registry.register("Wikipedia", "encyclopedia", lambda q: _wikipedia(q, "ar" if re.search(r"[\u0600-\u06FF]", q) else "en"))
    specialized.register_all()


_register_general()


AUTO_READ = int(os.environ.get("SEARCH_AUTO_READ", "2"))  # pages opened per search, so results are read, not just listed
READ_MARK = "[قُرئت: "
_STOP = set("في من على إلى عن مع the and for of in to a an سعر".split())


def _terms(query: str) -> list[str]:
    return [w for w in re.findall(r"[\w\u0600-\u06FF]{3,}", query.lower()) if w not in _STOP]


def excerpt(text: str, query: str, limit: int = 1400) -> str:
    """The parts of a page worth handing to an agent: lines with figures and the query's words, in page order."""
    terms = _terms(query)
    lines = [ln.strip() for ln in re.split(r"\n+|(?<=[.!؟?])\s+", text) if 25 <= len(ln.strip()) <= 400]
    scored = []
    for i, ln in enumerate(lines):
        low = ln.lower()
        score = sum(1 for t in terms if t in low) * 2 + (2 if re.search(r"\d", ln) else 0)
        if score >= 3:
            scored.append((score, i, ln))
    keep, size = [], 0
    for score, i, ln in sorted(scored, key=lambda x: (-x[0], x[1])):
        if size + len(ln) > limit:
            continue
        keep.append((i, ln))
        size += len(ln)
    return "\n".join(ln for _, ln in sorted(keep))


def _readable(url: str) -> bool:
    return not url.startswith("https://news.google.com/")


async def _auto_read(results: list[dict], query: str) -> list[tuple[str, str]]:
    cands = [x["url"] for x in results if x.get("url", "").startswith("http") and "wikipedia.org" not in x["url"] and _readable(x["url"])][:AUTO_READ + 2]

    async def one(u):
        try:
            return u, await asyncio.wait_for(fetch_url(u, 80000), timeout=20)
        except Exception:  # noqa: BLE001
            return u, ""
    got = await asyncio.gather(*[one(u) for u in cands])
    out = []
    for u, page in got:
        if len(out) >= AUTO_READ or not page.startswith("HTTP 2"):
            continue
        ex = excerpt(page.split("\n", 1)[-1], query)
        if len(ex) >= 40:
            out.append((u, ex))
    return out


VIA = "   — عبر: "


def _result_line(i: int, r: dict, many: bool) -> str:
    line = f"{i}. {r['title']}\n   {r['url']}\n   {r['snippet']}"
    if many and r.get("provider"):
        line += f"\n{VIA}{', '.join([r['provider']] + list(r.get('also_found_by') or []))}"
    return line


HEADLINE_ONLY = "عنوان خبر وناشره فقط"


def found_links(result: str) -> list[dict]:
    """Links a search listed (discovered, not read), with the provider that found each one."""
    m = re.match(r"المصدر: ([^\n]+)", result or "")
    if not m:
        return []
    default = m.group(1).split(" — ")[0].split(" + ")[0].strip()
    body = (result or "").split("\nمقتطفات من صفحات فتحها النظام فعلًا", 1)[0]
    out = []
    for blk in re.split(r"(?m)^(?=\d+\. )", body)[1:]:
        ls = blk.split("\n")
        url = ls[1].strip() if len(ls) > 1 else ""
        if not url.startswith("http"):
            continue
        via = next((x[len(VIA):] for x in ls if x.startswith(VIA)), "")
        provs = [p.strip() for p in via.split(",") if p.strip()] or [default]
        out.append({"url": url[:500], "title": re.sub(r"^\d+\. ", "", ls[0])[:120], "provider": provs[0],
                    "also": provs[1:], "headline_only": HEADLINE_ONLY in blk})
    return out[:20]


def search_meta(result: str) -> dict:
    """What a web_search returned, for the run's research measurements."""
    m = re.match(r"المصدر: ([^\n]+)", result or "")
    return {"provider": m.group(1).strip() if m else None,
            "results": len(re.findall(r"(?m)^\d+\. ", result or "")) if m else 0,
            "read": re.findall(re.escape(READ_MARK) + r"([^\]\s]+)\]", result or ""), "found": found_links(result)}


async def search_results(query: str) -> tuple[list, str, str]:
    """The general search chain as data: (results, source label, error). Shared by web_search (agents) and the
    research manager, so both use the same providers, breakers, gates and cache.
    Tavily (keyless, or the owner's free key), Brave if keyed, then DuckDuckGo (gated), Google News, then Wikipedia."""
    query = re.sub(r"\s+", " ", str(query or "")).strip()[:200]
    if not query:
        return [], "", "empty"
    key = query.lower()
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_TTL:
        STATS["cache_hits"] += 1
        return hit[1], hit[2], ""
    results, source = [], ""
    chain = ["Tavily"]  # keyless when no key is set
    if os.environ.get("BRAVE_API_KEY"):
        chain.append("Brave")
    chain += ["DuckDuckGo", "DuckDuckGo HTML"]
    extra = [n for n in specialized.available("web")]  # e.g. a self-hosted SearXNG, asked at the same time
    side = asyncio.ensure_future(registry.fan_out(extra, query)) if extra else None
    for name in chain:
        try:
            results = await registry.call(registry.REGISTRY[name], query, timeout=45)
        except _Blocked:
            results = []
            break  # the gate is closed for both DuckDuckGo endpoints
        except registry.Skipped:
            results = []
        if results:
            source = name.replace(" HTML", "")
            break
    if side:
        got = {n: r for n, r in (await side).items() if r}
        if got:
            results = registry.merge([results] + list(got.values()))
            source = " + ".join([source] * bool(source) + list(got))
    if not results:  # DuckDuckGo refuses many server IPs: news search is keyless and opens real articles
        try:
            gn = registry.REGISTRY["Google News"]
            results = await registry.call(gn, query, timeout=45)
            STATS["gnews_requests"] += 1
            if not results and len(_terms(query)) > 3:  # news search needs most words; long copied text finds nothing
                results = await registry.call(gn, " ".join(_terms(query)[:3]), timeout=45)
        except registry.Skipped:
            results = []
        except Exception as e:  # noqa: BLE001
            STATS["gnews_last_error"] = type(e).__name__
            results = []
        if results:
            source = "Google News"
            STATS["gnews_ok"] = STATS.get("gnews_ok", 0) + 1
    if not results:
        lang = "ar" if re.search(r"[\u0600-\u06FF]", query) else "en"
        try:
            wp = registry.REGISTRY["Wikipedia"]
            results, source = await registry.call(wp, query), f"Wikipedia ({lang})"
            if not results and len(_terms(query)) > 2:  # its search needs every word; retry with the main ones
                results = await registry.call(wp, " ".join(_terms(query)[:2]))
        except registry.Skipped:
            results = []
        except Exception as e:  # noqa: BLE001
            return [], "", f"خطأ في البحث: {e}"
        if results:
            source += " — بحث موسوعي احتياطي؛ النتائج قد لا تجيب عن السؤال"
    if results:
        _cache[key] = (time.monotonic(), results, source)
    return results, source, ""


async def web_search(query: str) -> str:
    """The general search chain, rendered for an agent. The top results are opened and their relevant lines
    appended, marked as read."""
    query = re.sub(r"\s+", " ", str(query or "")).strip()[:200]
    if not query:
        return "خطأ: استعلام فارغ."
    results, source, err = await search_results(query)
    if err:
        return err
    if not results:
        keyed = os.environ.get("TAVILY_API_KEY") or os.environ.get("BRAVE_API_KEY")
        return "لا نتائج." + ("" if keyed else " (محرك البحث المجاني يرفض الطلبات مؤقتًا؛ لا تكرر البحث نفسه. افتح رابطًا معروفًا بـ fetch_url إن كان متاحًا لك، أو استخدم world_bank للبيانات السكانية والاقتصادية.)")
    lines = [f"المصدر: {source}"] + [_result_line(i, r, " + " in source) for i, r in enumerate(results, 1)]
    read = await _auto_read(results, query) if AUTO_READ else []
    if read:
        lines.append("\nمقتطفات من صفحات فتحها النظام فعلًا (استشهد بالرابط عند استخدام رقم منها):")
        lines += [f"{READ_MARK}{u}]\n{ex}" for u, ex in read]
    return _clip("\n".join(lines), 8000)


WB_URL = "https://api.worldbank.org/v2/country/{c}/indicator/{i}?format=json&mrv=3"


async def world_bank(spec: str) -> str:
    """Official World Bank indicator values (keyless, reliable): '<ISO3 country> <indicator code>'."""
    parts = re.findall(r"[A-Za-z0-9._]+", str(spec or ""))
    if len(parts) < 2 or not re.fullmatch(r"[A-Za-z]{2,3}", parts[0]):
        return "خطأ: المدخل «رمز الدولة ورمز المؤشر»، مثل: SAU SP.POP.TOTL"
    country, ind = parts[0].upper(), parts[1].upper()
    url = WB_URL.format(c=country, i=ind)
    try:
        async with httpx.AsyncClient(timeout=15, headers={"User-Agent": UA}) as c:
            r = await c.get(url)
        data = r.json()
    except Exception as e:  # noqa: BLE001
        return f"خطأ في الجلب: {type(e).__name__}"
    rows = data[1] if isinstance(data, list) and len(data) > 1 and isinstance(data[1], list) else []
    vals = [x for x in rows if x.get("value") is not None]
    if not vals:
        return f"لا بيانات للمؤشر {ind} في {country}."
    name = vals[0]["indicator"]["value"]
    page = f"https://data.worldbank.org/indicator/{ind}?locations={vals[0]['country']['id']}"
    body = "\n".join(f"- {x['date']}: {x['value']:,}" for x in vals)
    return f"{READ_MARK}{page}]\nالبنك الدولي — {name} ({vals[0]['country']['value']}):\n{body}\nالمصدر: {page}"


def _limits():  # runs in the child before exec
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 2**20, 512 * 2**20))
    resource.setrlimit(resource.RLIMIT_FSIZE, (10 * 2**20, 10 * 2**20))
    resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
    os.setsid()


async def run_python(code: str) -> str:
    """Execute Python in an isolated child: empty env, temp dir, CPU/memory limits, 20s wall clock."""
    if not code:
        return "خطأ: لا يوجد كود."
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "main.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-I", path, cwd=d, env={"PATH": "/usr/bin:/bin", "HOME": d},
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, preexec_fn=_limits)
            try:
                out, err = await asyncio.wait_for(proc.communicate(), timeout=20)
            except asyncio.TimeoutError:
                proc.kill()
                return "خطأ: تجاوز الحد الزمني (20 ثانية)."
        except Exception as e:  # noqa: BLE001
            return f"خطأ في التشغيل: {e}"
    res = f"exit={proc.returncode}\n--- stdout ---\n{out.decode(errors='replace')}"
    if err:
        res += f"\n--- stderr ---\n{err.decode(errors='replace')}"
    return _clip(res)


_CALC_FUNCS = {"round": round, "min": min, "max": max, "abs": abs, "sum": lambda *a: sum(a[0] if len(a) == 1 else a)}
_CALC_OPS = {"Add": lambda a, b: a + b, "Sub": lambda a, b: a - b, "Mult": lambda a, b: a * b,
             "Div": lambda a, b: a / b, "FloorDiv": lambda a, b: a // b, "Mod": lambda a, b: a % b}
CALC_MAX_LINES = 60


def _calc_eval(node, env: dict):
    import ast
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in env:
            raise ValueError(f"اسم غير معرّف: {node.id}")
        return env[node.id]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        v = _calc_eval(node.operand, env)
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp):
        a, b = _calc_eval(node.left, env), _calc_eval(node.right, env)
        if isinstance(node.op, ast.Pow):
            if abs(b) > 100 or abs(a) > 1e12:
                raise ValueError("أس كبير جدًا")
            return a ** b
        op = _CALC_OPS.get(type(node.op).__name__)
        if not op:
            raise ValueError("عملية غير مسموحة")
        return op(a, b)
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_calc_eval(e, env) for e in node.elts]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _CALC_FUNCS and not node.keywords:
        return _CALC_FUNCS[node.func.id](*[_calc_eval(a, env) for a in node.args])
    raise ValueError("تعبير غير مسموح (أرقام وعمليات حسابية وround/min/max/abs/sum فقط)")


async def calculate(program: str) -> str:
    """Exact arithmetic without running code: lines of `name = expression` or bare expressions.
    Safe on any server (no code execution), so the free provider can calculate too."""
    import ast
    raw = [ln.strip() for ln in str(program or "").replace(";", "\n").splitlines() if ln.strip() and not ln.strip().startswith("#")]
    lines: list[str] = []
    for ln in raw:  # a bracket left open continues on the next line (agents write multi-line lists and dicts)
        if lines and sum(lines[-1].count(o) - lines[-1].count(c) for o, c in ("()", "[]", "{}")) > 0:
            lines[-1] += " " + ln
        else:
            lines.append(ln)
    if not lines:
        return "خطأ: لا توجد عمليات."
    env, out = {}, []
    for ln in lines[:CALC_MAX_LINES]:
        try:
            name, expr = (None, ln)
            m = re.match(r"^([A-Za-z_\u0600-\u06FF][\w\u0600-\u06FF]*)\s*=(?!=)\s*(.+)$", ln)
            if m:
                name, expr = m.group(1), m.group(2)
            if len(expr) > 400:
                raise ValueError("تعبير طويل جدًا")
            v = _calc_eval(ast.parse(re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", expr.replace("×", "*").replace("÷", "/")), mode="eval").body, env)
            if isinstance(v, float):
                v = round(v, 6)
            if name:
                env[name] = v
            out.append(f"{name or expr} = {v:,}" if not isinstance(v, list) else f"{name or expr} = {v}")
        except ZeroDivisionError:
            out.append(f"{ln} → خطأ: قسمة على صفر")
        except (ValueError, SyntaxError, TypeError, OverflowError) as e:
            out.append(f"{ln} → خطأ: {e}")
    if len(lines) > CALC_MAX_LINES:
        out.append(f"...تُجوهلت {len(lines) - CALC_MAX_LINES} سطرًا")
    return _clip("\n".join(out))


async def research_sources(spec: str) -> str:
    """Specialized sources asked at the same time: academic (Crossref, arXiv, OpenAlex if keyed) or code (GitHub).
    Input: '<kind>: <query>', e.g. 'academic: coffee consumption Saudi Arabia'. Lists results; it does not read them."""
    kind, _, q = str(spec or "").partition(":")
    kind, q = kind.strip().lower(), re.sub(r"\s+", " ", q).strip()[:200]
    if kind not in specialized.KINDS or not q:
        return "خطأ: المدخل «النوع: الاستعلام»، والأنواع: academic أو code. مثال: academic: coffee market Saudi Arabia"
    names = specialized.available(kind)
    if not names:
        return f"لا مصادر مفعّلة للنوع {kind}."
    got = await registry.fan_out(names, q)
    ok = [n for n, r in got.items() if r]
    if not ok:
        why = "؛ ".join(f"{n}: {'مُعطّل مؤقتًا بعد أخطاء متتالية' if r is None else 'لا نتائج أو خطأ'}" for n, r in got.items())
        return f"لا نتائج. ({why})"
    results = registry.merge([got[n] for n in ok])[:10]
    lines = [f"المصدر: {' + '.join(ok)}"] + [_result_line(i, r, True) for i, r in enumerate(results, 1)]
    lines.append("\nهذه قوائم من فهارس متخصصة (عناوين وملخصات)، لا نصوص قُرئت؛ افتح الرابط بـ fetch_url قبل الاستشهاد برقم منه.")
    return _clip("\n".join(lines), 6000)


TOOLS = {
    "web_search": {"fn": web_search, "arg": "query", "desc": "بحث في الويب. المدخل: {\"query\": \"...\"}"},
    "world_bank": {"fn": world_bank, "arg": "query",
                   "desc": "بيانات رسمية من البنك الدولي (سكان، دخل، تضخم...). المدخل: {\"query\": \"SAU SP.POP.TOTL\"}. رموز مفيدة: SP.POP.TOTL السكان، SP.URB.TOTL سكان المدن، NY.GDP.PCAP.CD نصيب الفرد من الناتج بالدولار، FP.CPI.TOTL.ZG التضخم، SP.POP.1564.TO السكان 15-64"},
    "research_sources": {"fn": research_sources, "arg": "query",
                         "desc": "بحث في مصادر متخصصة معًا: أوراق علمية (Crossref وarXiv) أو مستودعات برمجيات (GitHub). المدخل: {\"query\": \"academic: coffee consumption Saudi Arabia\"} أو {\"query\": \"code: web crawler\"}"},
    "fetch_url": {"fn": fetch_url, "arg": "url", "desc": "قراءة صفحة ويب كنص. المدخل: {\"url\": \"https://...\"}"},
    "calculate": {"fn": calculate, "arg": "program",
                  "desc": "حاسبة دقيقة (ليست كودًا): أسطر بصيغة name = expression أو تعبيرات، بأرقام و+ - * / // % ** وround/min/max/abs/sum. مثال: {\"program\": \"units = 5000\\nprice = 9.5\\nrevenue = units * price\"}"},
    "run_python": {"fn": run_python, "arg": "code",
                   "desc": "تشغيل كود Python 3 معزول (لا شبكة مضمونة، 20 ثانية). اطبع النتائج بـprint. المدخل: {\"code\": \"...\"}"},
}


async def call_tool(name: str, args: dict, allowed: set[str]) -> str:
    if name not in TOOLS or name not in allowed:
        return f"خطأ: الأداة '{name}' غير متاحة لهذا الوكيل. المتاح: {sorted(allowed)}"
    spec = TOOLS[name]
    val = args.get(spec["arg"]) if isinstance(args, dict) else args
    if val is None and isinstance(args, dict) and len(args) == 1:
        val = next(iter(args.values()))
    if val is None and isinstance(args, dict) and name == "world_bank":
        val = " ".join(str(args.get(k) or "") for k in ("country", "indicator"))
    return await spec["fn"](str(val or ""))
