#!/usr/bin/env python3
"""R-071: functional check of the web/ build after the vite upgrade.

Opens the UI served by a running AGOS server in headless Chromium and checks
that React actually mounted (not just that files exist):
  * HTTP 200 for "/" and every asset referenced by index.html
  * <div id="root"> has rendered children
  * the Arabic title/nav rendered, and the dashboard fetched /api/* successfully
  * no uncaught page errors / console errors

Usage: run_r071_web_verify.py <base_url>     (e.g. http://127.0.0.1:8000)
Exit code: 0 if every check passes, 1 otherwise.
Requires: pip install playwright && python -m playwright install chromium-headless-shell
"""

import re
import sys
import urllib.request

from playwright.sync_api import sync_playwright

base = sys.argv[1].rstrip("/")
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


html = urllib.request.urlopen(base + "/", timeout=10).read().decode()
check("GET / -> index.html", "<div id=\"root\">" in html)
for asset in re.findall(r'(?:src|href)="(/assets/[^"]+)"', html):
    with urllib.request.urlopen(base + asset, timeout=10) as r:
        check(f"GET {asset}", r.status == 200, f"{r.headers.get('content-type')} {len(r.read())} B")

errors, api_calls = [], []
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("console", lambda m: m.type == "error" and errors.append(f"console: {m.text}"))
    page.on("response", lambda r: "/api/" in r.url and api_calls.append((r.status, r.url.split(base)[-1])))
    page.goto(base + "/", wait_until="networkidle")
    page.wait_for_timeout(1500)
    children = page.evaluate("document.getElementById('root').children.length")
    text = page.inner_text("#root")
    check("React mounted (#root has children)", children > 0, f"children={children}")
    check("UI text rendered", len(text.strip()) > 50, f"{len(text.strip())} chars; sample={text.strip()[:60]!r}")
    check("title", page.title() == "AGOS - حضارة الوكلاء", page.title())
    ok_api = [c for c in api_calls if c[0] == 200]
    check("UI fetched /api/* successfully", len(ok_api) > 0, f"{api_calls[:5]}")
    check("no page/console errors", not errors, "; ".join(errors[:3]))
    browser.close()

print(f"\nR-071 web verification: {sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
