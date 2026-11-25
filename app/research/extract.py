"""Structured extraction from HTML with the standard library only (no new dependency, no network).

`extract(html)` returns what the page itself states, verbatim, never a model summary:
- title, language, publisher (og:site_name / JSON-LD publisher), author;
- published / modified dates from meta tags, JSON-LD or <time datetime>;
- JSON-LD objects (Schema.org) and product offers (name, price, currency) found in them;
- tables as rows of cell text (header row first when the page marks one), capped in count and size.

Arabic and English pages work the same way: text is kept as written, with right-to-left content untouched.
"""
from __future__ import annotations

import json
import re
from html.parser import HTMLParser

MAX_TABLES = 8
MAX_ROWS = 60
MAX_CELL = 200

_META_DATES = {"article:published_time": "published", "og:published_time": "published", "datePublished": "published",
               "pubdate": "published", "publish-date": "published", "date": "published", "dc.date": "published",
               "dc.date.issued": "published", "article:modified_time": "modified", "og:updated_time": "modified",
               "dateModified": "modified", "last-modified": "modified"}


class _Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta: dict = {}
        self.lang = ""
        self.title = ""
        self.times: list[str] = []
        self.jsonld_raw: list[str] = []
        self.tables: list[list[list[str]]] = []
        self._in_title = self._in_ld = False
        self._ld = []
        self._stack: list[list[list[str]]] = []  # nested tables
        self._cell: list[str] | None = None
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "html" and a.get("lang"):
            self.lang = a["lang"][:10]
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            k = (a.get("property") or a.get("name") or a.get("itemprop") or a.get("http-equiv") or "").strip()
            if k and a.get("content") and k not in self.meta:
                self.meta[k] = a["content"].strip()
        elif tag == "time" and a.get("datetime"):
            self.times.append(a["datetime"][:40])
        elif tag == "script" and "ld+json" in a.get("type", "").lower():
            self._in_ld, self._ld = True, []
        elif tag in ("script", "style", "noscript", "svg"):
            self._skip += 1
        elif tag == "table":
            self._stack.append([])
        elif tag == "tr" and self._stack:
            self._stack[-1].append([])
        elif tag in ("td", "th") and self._stack:
            if not self._stack[-1]:
                self._stack[-1].append([])
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "script" and self._in_ld:
            self._in_ld = False
            self.jsonld_raw.append("".join(self._ld))
        elif tag in ("script", "style", "noscript", "svg") and self._skip:
            self._skip -= 1
        elif tag in ("td", "th") and self._cell is not None and self._stack:
            txt = re.sub(r"\s+", " ", "".join(self._cell)).strip()[:MAX_CELL]
            self._stack[-1][-1].append(txt)
            self._cell = None
        elif tag == "table" and self._stack:
            rows = [r for r in self._stack.pop() if any(c for c in r)]
            if len(rows) >= 2 and max(len(r) for r in rows) >= 2:
                self.tables.append(rows[:MAX_ROWS])

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif self._in_ld:
            self._ld.append(data)
        elif self._cell is not None and not self._skip:
            self._cell.append(data)


def _walk(o):
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)


def _name(v) -> str:
    if isinstance(v, dict):
        return str(v.get("name") or "")
    if isinstance(v, list) and v:
        return _name(v[0])
    return str(v or "")


def _types(o: dict) -> list[str]:
    t = o.get("@type")
    return [str(x) for x in (t if isinstance(t, list) else [t]) if x]


def extract(raw_html: str) -> dict:
    p = _Parser()
    try:
        p.feed(raw_html or "")
        p.close()
    except Exception:  # noqa: BLE001 - malformed HTML: keep what was parsed
        pass
    jsonld = []
    for blob in p.jsonld_raw[:10]:
        try:
            jsonld.append(json.loads(blob.strip()))
        except ValueError:
            continue
    out = {"title": re.sub(r"\s+", " ", p.meta.get("og:title") or p.title).strip()[:300], "lang": p.lang,
           "publisher": p.meta.get("og:site_name", ""), "author": p.meta.get("author", ""),
           "published": "", "modified": "", "description": p.meta.get("description") or p.meta.get("og:description", ""),
           "offers": [], "jsonld_types": [], "tables": p.tables[:MAX_TABLES]}
    for k, field in _META_DATES.items():
        if p.meta.get(k) and not out[field]:
            out[field] = p.meta[k][:40]
    for o in (x for doc in jsonld for x in _walk(doc)):
        types = _types(o)
        out["jsonld_types"] += [t for t in types if t not in out["jsonld_types"]]
        if o.get("datePublished") and not out["published"]:
            out["published"] = str(o["datePublished"])[:40]
        if o.get("dateModified") and not out["modified"]:
            out["modified"] = str(o["dateModified"])[:40]
        if o.get("publisher") and not out["publisher"]:
            out["publisher"] = _name(o["publisher"])[:120]
        if o.get("author") and not out["author"]:
            out["author"] = _name(o["author"])[:120]
        if "Product" in types:
            offers = o.get("offers")
            for off in (offers if isinstance(offers, list) else [offers] if offers else []):
                if not isinstance(off, dict):
                    continue
                price = off.get("price") or off.get("lowPrice")
                if price is not None and len(out["offers"]) < 20:
                    out["offers"].append({"name": str(o.get("name", ""))[:160], "price": str(price),
                                          "currency": str(off.get("priceCurrency", "")),
                                          "availability": str(off.get("availability", "")).rsplit("/", 1)[-1]})
    if not out["published"] and p.times:
        out["published"] = p.times[0]
    out["jsonld_types"] = out["jsonld_types"][:15]
    return out


def tables_markdown(tables: list, max_chars: int = 2500) -> str:
    """Tables as Markdown, verbatim cells, stopping before `max_chars`."""
    parts, size = [], 0
    for n, rows in enumerate(tables, 1):
        width = max(len(r) for r in rows)
        lines = [f"جدول {n} ({len(rows)} صفًا):"]
        for i, r in enumerate(rows):
            cells = [c.replace("|", "/") for c in r] + [""] * (width - len(r))
            lines.append("| " + " | ".join(cells) + " |")
            if i == 0:
                lines.append("|" + "---|" * width)
        block = "\n".join(lines)
        if size + len(block) > max_chars:
            if not parts:
                parts.append(block[:max_chars] + "\n...[قُطع الجدول]")
            break
        parts.append(block)
        size += len(block)
    return "\n\n".join(parts)


def summary_line(info: dict) -> str:
    """One line of what the page states about itself, for the agent and the evidence record."""
    bits = []
    for label, k in (("الناشر", "publisher"), ("تاريخ النشر", "published"), ("آخر تحديث", "modified"), ("اللغة", "lang")):
        if info.get(k):
            bits.append(f"{label}: {info[k]}")
    return " | ".join(bits)
