"""Real tools the agents can call. Each returns plain text (truncated) and never raises."""
from __future__ import annotations

import asyncio
import html
import ipaddress
import os
import re
import resource
import socket
import sys
import tempfile
from urllib.parse import quote_plus, urlparse, parse_qs, unquote

import httpx

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


def _is_public_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


async def fetch_url(url: str) -> str:
    p = urlparse(url or "")
    if p.scheme not in ("http", "https") or not p.hostname:
        return "خطأ: رابط غير صالح (http/https فقط)."
    if not await asyncio.to_thread(_is_public_host, p.hostname):
        return "خطأ: العناوين الداخلية/الخاصة ممنوعة."
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent": UA}) as c:
            r = await c.get(url)
        ctype = r.headers.get("content-type", "")
        body = r.text if ("text" in ctype or "json" in ctype or "xml" in ctype) else f"[محتوى غير نصي: {ctype}]"
        text = _html_to_text(body) if "html" in ctype else body
        return _clip(f"HTTP {r.status_code} — {r.url}\n\n{text}")
    except Exception as e:  # noqa: BLE001
        return f"خطأ في الجلب: {e}"


async def _ddg(query: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers={"User-Agent": UA}) as c:
        r = await c.get("https://lite.duckduckgo.com/lite/", params={"q": query})
    if r.status_code != 200:
        return []
    links = re.findall(r"<a[^>]+href=\"([^\"]+)\"[^>]*class='result-link'[^>]*>(.*?)</a>", r.text, re.S)
    snippets = re.findall(r"<td class='result-snippet'>(.*?)</td>", r.text, re.S)
    out = []
    for i, (href, title) in enumerate(links[:6]):
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


async def web_search(query: str) -> str:
    if not query:
        return "خطأ: استعلام فارغ."
    results, source = [], ""
    try:
        results, source = await _ddg(query), "DuckDuckGo"
    except Exception:  # noqa: BLE001
        results = []
    if not results:
        lang = "ar" if re.search(r"[\u0600-\u06FF]", query) else "en"
        try:
            results, source = await _wikipedia(query, lang), f"Wikipedia ({lang})"
        except Exception as e:  # noqa: BLE001
            return f"خطأ في البحث: {e}"
    if not results:
        return "لا نتائج."
    lines = [f"المصدر: {source}"] + [f"{i}. {r['title']}\n   {r['url']}\n   {r['snippet']}" for i, r in enumerate(results, 1)]
    return _clip("\n".join(lines))


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


TOOLS = {
    "web_search": {"fn": web_search, "arg": "query", "desc": "بحث في الويب. المدخل: {\"query\": \"...\"}"},
    "fetch_url": {"fn": fetch_url, "arg": "url", "desc": "قراءة صفحة ويب كنص. المدخل: {\"url\": \"https://...\"}"},
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
    return await spec["fn"](str(val or ""))
