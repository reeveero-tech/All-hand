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


async def fetch_url(url: str) -> str:
    """Read a public page as text. Each hop (including redirects) is resolved once, checked, and
    the connection is pinned to the checked address; the body is capped at MAX_BYTES."""
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False) as c:
            for _ in range(MAX_REDIRECTS + 1):
                p = urlparse(url or "")
                if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
                    return "خطأ: رابط غير صالح (http/https فقط)."
                ip = await asyncio.to_thread(_resolve_public, p.hostname)
                if not ip:
                    return "خطأ: العناوين الداخلية/الخاصة ممنوعة."
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
                    chunks, size = [], 0
                    async for chunk in r.aiter_bytes():
                        chunks.append(chunk)
                        size += len(chunk)
                        if size >= MAX_BYTES:
                            break
                    raw = b"".join(chunks)[:MAX_BYTES]
                finally:
                    await r.aclose()
                break
            else:
                return "خطأ: تحويلات كثيرة."
        ctype = r.headers.get("content-type", "")
        if "text" in ctype or "json" in ctype or "xml" in ctype:
            body = raw.decode(r.charset_encoding or "utf-8", errors="replace")
        else:
            body = f"[محتوى غير نصي: {ctype}]"
        text = _html_to_text(body) if "html" in ctype else body
        return _clip(f"HTTP {r.status_code} — {url}\n\n{text}")
    except Exception as e:  # noqa: BLE001
        return f"خطأ في الجلب: {type(e).__name__}: {str(e)[:200]}"


async def _ddg(query: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=8, follow_redirects=True, headers={"User-Agent": UA}) as c:
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


async def _tavily(query: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post("https://api.tavily.com/search", json={"api_key": os.environ["TAVILY_API_KEY"],
                                                                "query": query, "max_results": 6})
    if r.status_code != 200:
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


async def web_search(query: str) -> str:
    """Keyed providers first (TAVILY_API_KEY, BRAVE_API_KEY), then DuckDuckGo lite, then Wikipedia."""
    if not query:
        return "خطأ: استعلام فارغ."
    results, source = [], ""
    chain = []
    if os.environ.get("TAVILY_API_KEY"):
        chain.append((_tavily, "Tavily"))
    if os.environ.get("BRAVE_API_KEY"):
        chain.append((_brave, "Brave"))
    chain.append((_ddg, "DuckDuckGo"))
    for fn, name in chain:
        try:
            results = await asyncio.wait_for(fn(query), timeout=20)
        except Exception:  # noqa: BLE001
            results = []
        if results:
            source = name
            break
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
    lines = [ln.strip() for ln in str(program or "").replace(";", "\n").splitlines() if ln.strip() and not ln.strip().startswith("#")]
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


TOOLS = {
    "web_search": {"fn": web_search, "arg": "query", "desc": "بحث في الويب. المدخل: {\"query\": \"...\"}"},
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
    return await spec["fn"](str(val or ""))
