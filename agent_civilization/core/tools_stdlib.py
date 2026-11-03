"""R-175 phase 1 (D-128): the core tool library — 80 real deterministic tools.

What this module IS: the spec's phase-1 «أدوات النواة عالية الاستخدام» — a
standard library of small, deterministic, hermetic tools (text, encoding,
data formats, validation, math/stats, collections). Every handler is a REAL
Python function doing real work: no network, no model provider, no
subprocess, no secrets — pure computation over the work item. They extend
the 20 specialist handlers to the phase-1 target of 100 executable tools.

What it deliberately is NOT:
- It does NOT touch the frozen dispatcher file's 20 specialist handlers; the
  dispatcher imports this module and merges ``HANDLERS`` (a documented
  FREEZE_DRIFT ledger entry — the freeze guards reproducibility, it does not
  forbid evolving the product).
- The tools are NOT toy stubs: each refuses malformed input with an honest
  error (a contract, not a crash) and each is dispatched through the real
  runtime gate (R-171/R-172/R-175 enforcement: timeout, network policy,
  scopes, the D-127 declared-type check).
- No tool here claims external integration: network-dependent capabilities
  belong to the specialist tools (network_client & co) and to imported
  connectors (R-173) — not to the core library.

Contract: input arrives in the work item (``text``/``prompt`` for text,
``data`` for structured values, plus tool-specific keys); the result is
``{"ok": True, "tool_output": ...}`` on success and
``{"ok": False, "error": "<what was wrong with the input>"}`` on refusal —
the same shape the 20 specialists use (RESULT_OUTPUT_SCHEMA).
"""
from __future__ import annotations

import base64
import binascii
import csv
import hashlib
import io
import json
import math
import re
import statistics
import urllib.parse
import uuid as uuid_mod
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from agent_civilization.core.tool_registry import ToolRecord

#: every handler: work -> {"ok": bool, "tool_output"/"error": ...}
HANDLERS: Dict[str, Callable[[Dict[str, Any]], Any]] = {}

_TOOL_PREFIX = "agos.internal."
_SEED_VERSION = "1.0.0"


def _text(work: Dict[str, Any]) -> Optional[str]:
    v = work.get("text", work.get("prompt"))
    return v if isinstance(v, str) else None


def _data(work: Dict[str, Any]) -> Any:
    return work.get("data")


def _ok(out: Any) -> Dict[str, Any]:
    return {"ok": True, "tool_output": out}


def _refuse(msg: str) -> Dict[str, Any]:
    return {"ok": False, "error": msg}


def _register(spec: str):
    def deco(fn):
        HANDLERS[spec] = fn  # the module attribute name IS the ref anchor
        return fn
    return deco


# ----------------------------------------------------------------------------
# TEXT (25) — deterministic text utilities
# ----------------------------------------------------------------------------

@_register("text_stats")
async def _text_stats(work):
    t = _text(work)
    if t is None:
        return _refuse("text (or prompt) must be a string")
    lines = t.splitlines()
    words = t.split()
    return _ok({"chars": len(t), "words": len(words), "lines": len(lines),
                "max_line_len": max((len(l) for l in lines), default=0)})


@_register("text_upper")
async def _text_upper(work):
    t = _text(work)
    if t is None:
        return _refuse("text (or prompt) must be a string")
    return _ok(t.upper())


@_register("text_lower")
async def _text_lower(work):
    t = _text(work)
    if t is None:
        return _refuse("text (or prompt) must be a string")
    return _ok(t.lower())


@_register("text_title")
async def _text_title(work):
    t = _text(work)
    if t is None:
        return _refuse("text (or prompt) must be a string")
    return _ok(t.title())


@_register("text_camel")
async def _text_camel(work):
    t = _text(work)
    if t is None:
        return _refuse("text (or prompt) must be a string")
    parts = re.split(r"[^a-zA-Z0-9]+", t)
    parts = [p for p in parts if p]
    if not parts:
        return _refuse("no alphanumeric words in the text")
    return _ok(parts[0].lower() + "".join(p.capitalize() for p in parts[1:]))


@_register("text_snake")
async def _text_snake(work):
    t = _text(work)
    if t is None:
        return _refuse("text (or prompt) must be a string")
    out = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", t)
    out = re.sub(r"[^a-zA-Z0-9]+", "_", out).strip("_").lower()
    if not out:
        return _refuse("no alphanumeric words in the text")
    return _ok(out)


@_register("text_kebab")
async def _text_kebab(work):
    t = _text(work)
    if t is None:
        return _refuse("text (or prompt) must be a string")
    out = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", t)
    out = re.sub(r"[^a-zA-Z0-9]+", "-", out).strip("-").lower()
    if not out:
        return _refuse("no alphanumeric words in the text")
    return _ok(out)


@_register("text_slugify")
async def _text_slugify(work):
    t = _text(work)
    if t is None:
        return _refuse("text (or prompt) must be a string")
    out = t.lower()
    out = re.sub(r"[^a-z0-9\u0600-\u06FF]+", "-", out).strip("-")
    out = re.sub(r"-{2,}", "-", out)
    if not out:
        return _refuse("nothing slugifiable in the text")
    return _ok(out)


@_register("text_reverse")
async def _text_reverse(work):
    t = _text(work)
    if t is None:
        return _refuse("text (or prompt) must be a string")
    return _ok(t[::-1])


@_register("text_truncate")
async def _text_truncate(work):
    t = _text(work)
    n = work.get("length", 100)
    if t is None or not isinstance(n, int) or n < 0:
        return _refuse("text must be a string and length a non-negative int")
    suffix = "…" if len(t) > n else ""
    return _ok(t[:n] + suffix)


@_register("text_pad_left")
async def _text_pad_left(work):
    t = _text(work)
    n, ch = work.get("length"), work.get("pad", " ")
    if t is None or not isinstance(n, int) or n < 0 or not isinstance(ch, str) or len(ch) != 1:
        return _refuse("text must be a string, length a non-negative int, pad a single char")
    return _ok(t.rjust(n, ch))


@_register("text_pad_right")
async def _text_pad_right(work):
    t = _text(work)
    n, ch = work.get("length"), work.get("pad", " ")
    if t is None or not isinstance(n, int) or n < 0 or not isinstance(ch, str) or len(ch) != 1:
        return _refuse("text must be a string, length a non-negative int, pad a single char")
    return _ok(t.ljust(n, ch))


@_register("text_strip")
async def _text_strip(work):
    t = _text(work)
    if t is None:
        return _refuse("text (or prompt) must be a string")
    chars = work.get("chars")
    return _ok(t.strip(chars if isinstance(chars, str) else None))


@_register("text_replace")
async def _text_replace(work):
    t = _text(work)
    old, new = work.get("old"), work.get("new", "")
    if t is None or not isinstance(old, str) or not isinstance(new, str) or not old:
        return _refuse("text must be a string; old/new must be non-empty strings")
    count = work.get("count", -1)
    if not isinstance(count, int):
        return _refuse("count must be an int")
    return _ok(t.replace(old, new, count))


@_register("text_split")
async def _text_split(work):
    t = _text(work)
    sep = work.get("separator")
    if t is None or not isinstance(sep, str):
        return _refuse("text must be a string and separator a string")
    return _ok(t.split(sep))


@_register("text_join")
async def _text_join(work):
    parts, sep = _data(work), work.get("separator", ",")
    if not isinstance(parts, list) or not all(isinstance(p, str) for p in parts) \
            or not isinstance(sep, str):
        return _refuse("data must be a list of strings and separator a string")
    return _ok(sep.join(parts))


@_register("text_wrap")
async def _text_wrap(work):
    t = _text(work)
    width = work.get("width", 72)
    if t is None or not isinstance(width, int) or width < 1:
        return _refuse("text must be a string and width a positive int")
    import textwrap
    return _ok("\n".join(textwrap.wrap(t, width=width)))


@_register("text_indent")
async def _text_indent(work):
    t = _text(work)
    prefix = work.get("prefix", "    ")
    if t is None or not isinstance(prefix, str):
        return _refuse("text must be a string and prefix a string")
    import textwrap
    return _ok(textwrap.indent(t, prefix))


@_register("text_dedent")
async def _text_dedent(work):
    t = _text(work)
    if t is None:
        return _refuse("text (or prompt) must be a string")
    import textwrap
    return _ok(textwrap.dedent(t))


@_register("text_levenshtein")
async def _text_levenshtein(work):
    a, b = work.get("left"), work.get("right")
    if not isinstance(a, str) or not isinstance(b, str):
        return _refuse("left and right must be strings")
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[-1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return _ok(prev[-1])


@_register("text_similarity")
async def _text_similarity(work):
    a, b = work.get("left"), work.get("right")
    if not isinstance(a, str) or not isinstance(b, str):
        return _refuse("left and right must be strings")
    if not a and not b:
        return _ok(1.0)
    # Levenshtein-based ratio in [0, 1]
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[-1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    dist = prev[-1]
    total = len(a) + len(b)
    return _ok(round(1.0 - (2.0 * dist / total if total else 0.0), 6))


@_register("text_strip_prefix")
async def _text_strip_prefix(work):
    t, p = _text(work), work.get("prefix")
    if t is None or not isinstance(p, str):
        return _refuse("text must be a string and prefix a string")
    return _ok(t[len(p):] if t.startswith(p) else t)


@_register("text_strip_suffix")
async def _text_strip_suffix(work):
    t, s = _text(work), work.get("suffix")
    if t is None or not isinstance(s, str):
        return _refuse("text must be a string and suffix a string")
    return _ok(t[:len(t) - len(s)] if s and t.endswith(s) else t)


@_register("text_center")
async def _text_center(work):
    t = _text(work)
    n, ch = work.get("length"), work.get("pad", " ")
    if t is None or not isinstance(n, int) or n < 0 or not isinstance(ch, str) or len(ch) != 1:
        return _refuse("text must be a string, length a non-negative int, pad a single char")
    return _ok(t.center(n, ch))


@_register("text_swapcase")
async def _text_swapcase(work):
    t = _text(work)
    if t is None:
        return _refuse("text (or prompt) must be a string")
    return _ok(t.swapcase())


# ----------------------------------------------------------------------------
# ENCODING & HASH (12) — deterministic codecs/digests of the given text
# ----------------------------------------------------------------------------

@_register("base64_encode")
async def _base64_encode(work):
    t = _text(work)
    if t is None:
        return _refuse("text (or prompt) must be a string")
    return _ok(base64.b64encode(t.encode("utf-8")).decode("ascii"))


@_register("base64_decode")
async def _base64_decode(work):
    t = _text(work)
    if t is None:
        return _refuse("text (or prompt) must be a string")
    try:
        return _ok(base64.b64decode(t, validate=True).decode("utf-8"))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return _refuse("not valid base64 (or not UTF-8 inside)")


@_register("url_encode")
async def _url_encode(work):
    t = _text(work)
    if t is None:
        return _refuse("text (or prompt) must be a string")
    return _ok(urllib.parse.quote(t, safe=""))


@_register("url_decode")
async def _url_decode(work):
    t = _text(work)
    if t is None:
        return _refuse("text (or prompt) must be a string")
    try:
        return _ok(urllib.parse.unquote(t, errors="strict"))
    except UnicodeDecodeError:
        return _refuse("not a valid percent-encoded string")


@_register("hex_encode")
async def _hex_encode(work):
    t = _text(work)
    if t is None:
        return _refuse("text (or prompt) must be a string")
    return _ok(t.encode("utf-8").hex())


@_register("hex_decode")
async def _hex_decode(work):
    t = _text(work)
    if t is None:
        return _refuse("text (or prompt) must be a string")
    try:
        return _ok(bytes.fromhex(t).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return _refuse("not valid hex (or not UTF-8 inside)")


@_register("sha256_text")
async def _sha256_text(work):
    t = _text(work)
    if t is None:
        return _refuse("text (or prompt) must be a string")
    return _ok(hashlib.sha256(t.encode("utf-8")).hexdigest())


@_register("md5_text")
async def _md5_text(work):
    t = _text(work)
    if t is None:
        return _refuse("text (or prompt) must be a string")
    return _ok(hashlib.md5(t.encode("utf-8"), usedforsecurity=False).hexdigest())


@_register("crc32_text")
async def _crc32_text(work):
    t = _text(work)
    if t is None:
        return _refuse("text (or prompt) must be a string")
    import zlib
    return _ok(f"{zlib.crc32(t.encode('utf-8')) & 0xFFFFFFFF:08x}")


@_register("uuid4_generate")
async def _uuid4_generate(work):
    # deterministic seeds are NOT accepted here: a real uuid4 is random; the
    # count is capped to keep it a single quick call.
    n = work.get("count", 1)
    if not isinstance(n, int) or not 1 <= n <= 100:
        return _refuse("count must be an int in 1..100")
    return _ok([str(uuid_mod.uuid4()) for _ in range(n)])


@_register("roman_to_int")
async def _roman_to_int(work):
    t = _text(work)
    if t is None:
        return _refuse("text (or prompt) must be a string")
    t = t.strip().upper()
    vals = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    if not t or not all(c in vals for c in t):
        return _refuse("not a Roman numeral")
    total = 0
    for i, c in enumerate(t):
        v = vals[c]
        if i + 1 < len(t) and vals[t[i + 1]] > v:
            total -= v
        else:
            total += v
    return _ok(total)


@_register("int_to_roman")
async def _int_to_roman(work):
    n = _data(work)
    if not isinstance(n, int) or not 1 <= n <= 3999:
        return _refuse("data must be an int in 1..3999")
    table = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"),
             (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"),
             (5, "V"), (4, "IV"), (1, "I")]
    out = []
    for v, s in table:
        while n >= v:
            out.append(s)
            n -= v
    return _ok("".join(out))


# ----------------------------------------------------------------------------
# DATA FORMATS (13) — parse/format the standard interchange shapes
# ----------------------------------------------------------------------------

@_register("json_validate")
async def _json_validate(work):
    t = _text(work)
    if t is None:
        return _refuse("text (or prompt) must be a string")
    try:
        parsed = json.loads(t)
    except json.JSONDecodeError as e:
        return _refuse(f"invalid JSON: {e.msg} at pos {e.pos}")
    return _ok({"valid": True, "type": type(parsed).__name__})


@_register("json_format")
async def _json_format(work):
    t = _text(work)
    if t is None:
        return _refuse("text (or prompt) must be a string")
    try:
        parsed = json.loads(t)
    except json.JSONDecodeError as e:
        return _refuse(f"invalid JSON: {e.msg} at pos {e.pos}")
    return _ok(json.dumps(parsed, ensure_ascii=False, indent=2, sort_keys=True))


@_register("json_minify")
async def _json_minify(work):
    t = _text(work)
    if t is None:
        return _refuse("text (or prompt) must be a string")
    try:
        parsed = json.loads(t)
    except json.JSONDecodeError as e:
        return _refuse(f"invalid JSON: {e.msg} at pos {e.pos}")
    return _ok(json.dumps(parsed, ensure_ascii=False, separators=(",", ":")))


@_register("json_path_get")
async def _json_path_get(work):
    t, path = _text(work), work.get("path")
    if t is None or not isinstance(path, str) or not path:
        return _refuse("text must be a JSON string and path a dotted key path")
    try:
        parsed = json.loads(t)
    except json.JSONDecodeError as e:
        return _refuse(f"invalid JSON: {e.msg} at pos {e.pos}")
    cur: Any = parsed
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.lstrip("-").isdigit():
            i = int(part)
            if -len(cur) <= i < len(cur):
                cur = cur[i]
            else:
                return _refuse(f"path index out of range: {part}")
        else:
            return _refuse(f"path not found: {part}")
    return _ok(cur)


@_register("json_merge")
async def _json_merge(work):
    d = _data(work)
    if not isinstance(d, list) or len(d) != 2 \
            or not all(isinstance(x, dict) for x in d):
        return _refuse("data must be a list of exactly two JSON objects")
    merged = {**d[0], **d[1]}
    return _ok(merged)


@_register("csv_parse")
async def _csv_parse(work):
    t = _text(work)
    if t is None:
        return _refuse("text (or prompt) must be a CSV string")
    delim = work.get("delimiter", ",")
    if not isinstance(delim, str) or len(delim) != 1:
        return _refuse("delimiter must be a single character")
    rows = list(csv.reader(io.StringIO(t), delimiter=delim))
    if not rows:
        return _refuse("no rows in the CSV text")
    header, body = rows[0], rows[1:]
    return _ok([dict(zip(header, r)) for r in body])


@_register("tsv_parse")
async def _tsv_parse(work):
    t = _text(work)
    if t is None:
        return _refuse("text (or prompt) must be a TSV string")
    rows = [line.split("\t") for line in t.splitlines() if line]
    if not rows:
        return _refuse("no rows in the TSV text")
    header, body = rows[0], rows[1:]
    return _ok([dict(zip(header, r)) for r in body])


@_register("kv_parse")
async def _kv_parse(work):
    t = _text(work)
    if t is None:
        return _refuse("text (or prompt) must be a string")
    sep = work.get("separator", ":")
    if not isinstance(sep, str) or len(sep) < 1:
        return _refuse("separator must be a non-empty string")
    out = {}
    for line in t.splitlines():
        line = line.strip()
        if not line:
            continue
        if sep not in line:
            return _refuse(f"line has no {sep!r}: {line[:40]}")
        k, _, v = line.partition(sep)
        out[k.strip()] = v.strip()
    return _ok(out)


@_register("html_escape")
async def _html_escape(work):
    t = _text(work)
    if t is None:
        return _refuse("text (or prompt) must be a string")
    import html
    return _ok(html.escape(t))


@_register("html_unescape")
async def _html_unescape(work):
    t = _text(work)
    if t is None:
        return _refuse("text (or prompt) must be a string")
    import html
    return _ok(html.unescape(t))


@_register("xml_escape")
async def _xml_escape(work):
    t = _text(work)
    if t is None:
        return _refuse("text (or prompt) must be a string")
    out = t.replace("&", "&" + "amp;")
    out = out.replace("<", "&" + "lt;").replace(">", "&" + "gt;")
    return _ok(out)


@_register("xml_unescape")
async def _xml_unescape(work):
    t = _text(work)
    if t is None:
        return _refuse("text (or prompt) must be a string")
    for ent, ch in (("&" + "lt;", "<"), ("&" + "gt;", ">"), ("&" + "amp;", "&")):
        t = t.replace(ent, ch)
    return _ok(t)


@_register("json_lines_parse")
async def _json_lines_parse(work):
    t = _text(work)
    if t is None:
        return _refuse("text (or prompt) must be a JSON-lines string")
    out = []
    for i, line in enumerate(t.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as e:
            return _refuse(f"invalid JSON on line {i}: {e.msg}")
    return _ok(out)


# ----------------------------------------------------------------------------
# VALIDATION (10) — honest checks of the given value, not guesses
# ----------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
_URL_RE = re.compile(r"^https?://[A-Za-z0-9.-]+(:\d+)?(/[^\s]*)?$")
_IPV4_RE = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


@_register("validate_email")
async def _validate_email(work):
    t = _text(work)
    if t is None:
        return _refuse("text (or prompt) must be a string")
    return _ok({"valid": bool(_EMAIL_RE.match(t.strip()))})


@_register("validate_url")
async def _validate_url(work):
    t = _text(work)
    if t is None:
        return _refuse("text (or prompt) must be a string")
    return _ok({"valid": bool(_URL_RE.match(t.strip()))})


@_register("validate_ipv4")
async def _validate_ipv4(work):
    t = _text(work)
    if t is None:
        return _refuse("text (or prompt) must be a string")
    m = _IPV4_RE.match(t.strip())
    valid = bool(m) and all(0 <= int(g) <= 255 for g in m.groups())
    return _ok({"valid": valid})


@_register("validate_uuid")
async def _validate_uuid(work):
    t = _text(work)
    if t is None:
        return _refuse("text (or prompt) must be a string")
    return _ok({"valid": bool(_UUID_RE.match(t.strip().lower()))})


@_register("validate_iso_date")
async def _validate_iso_date(work):
    t = _text(work)
    if t is None:
        return _refuse("text (or prompt) must be a string")
    try:
        datetime.fromisoformat(t.strip().replace("Z", "+00:00"))
        return _ok({"valid": True})
    except ValueError:
        return _ok({"valid": False})


@_register("validate_number")
async def _validate_number(work):
    v = _data(work) if _data(work) is not None else _text(work)
    if isinstance(v, bool):
        return _ok({"valid": False, "type": "bool"})
    if isinstance(v, (int, float)):
        return _ok({"valid": True, "type": type(v).__name__})
    if isinstance(v, str):
        try:
            float(v)
            return _ok({"valid": True, "type": "numeric-string"})
        except ValueError:
            return _ok({"valid": False, "type": "string"})
    return _refuse("data (or text) must be a number, numeric string or string")


@_register("validate_regex")
async def _validate_regex(work):
    t, pat = _text(work), work.get("pattern")
    if t is None or not isinstance(pat, str):
        return _refuse("text must be a string and pattern a regex string")
    try:
        m = re.search(pat, t)
    except re.error as e:
        return _refuse(f"invalid regex: {e}")
    return _ok({"matches": m is not None,
                "matched": m.group(0) if m else None})


@_register("regex_extract")
async def _regex_extract(work):
    t, pat = _text(work), work.get("pattern")
    if t is None or not isinstance(pat, str):
        return _refuse("text must be a string and pattern a regex string")
    try:
        found = re.findall(pat, t)
    except re.error as e:
        return _refuse(f"invalid regex: {e}")
    return _ok([f if isinstance(f, str) else list(f) for f in found])


@_register("regex_replace")
async def _regex_replace(work):
    t, pat = _text(work), work.get("pattern")
    repl = work.get("replacement", "")
    if t is None or not isinstance(pat, str) or not isinstance(repl, str):
        return _refuse("text must be a string; pattern/replacement strings")
    try:
        return _ok(re.sub(pat, repl, t))
    except re.error as e:
        return _refuse(f"invalid regex: {e}")


@_register("validate_luhn")
async def _validate_luhn(work):
    t = _text(work)
    if t is None:
        return _refuse("text (or prompt) must be a string")
    digits = re.sub(r"\s+", "", t)
    if not digits.isdigit():
        return _refuse("text must be digits (spaces allowed)")
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return _ok({"valid": total % 10 == 0})


# ----------------------------------------------------------------------------
# MATH & STATS (10) — deterministic computation over the given numbers
# ----------------------------------------------------------------------------

def _nums(work) -> Optional[List[float]]:
    v = _data(work)
    if isinstance(v, list) and v and all(
            isinstance(x, (int, float)) and not isinstance(x, bool) for x in v):
        return v
    return None


@_register("stats_summary")
async def _stats_summary(work):
    xs = _nums(work)
    if xs is None:
        return _refuse("data must be a non-empty list of numbers")
    try:
        return _ok({"count": len(xs), "sum": sum(xs),
                    "mean": statistics.fmean(xs),
                    "median": statistics.median(xs),
                    "stdev": statistics.stdev(xs) if len(xs) > 1 else 0.0,
                    "min": min(xs), "max": max(xs)})
    except statistics.StatisticsError as e:
        return _refuse(str(e))


@_register("list_sum")
async def _list_sum(work):
    xs = _nums(work)
    if xs is None:
        return _refuse("data must be a non-empty list of numbers")
    return _ok(sum(xs))


@_register("list_minmax")
async def _list_minmax(work):
    xs = _nums(work)
    if xs is None:
        return _refuse("data must be a non-empty list of numbers")
    return _ok({"min": min(xs), "max": max(xs)})


@_register("percentile")
async def _percentile(work):
    xs, p = _nums(work), work.get("p")
    if xs is None or not isinstance(p, (int, float)) or not 0 <= p <= 100:
        return _refuse("data must be a non-empty number list and p in 0..100")
    xs = sorted(xs)
    k = (len(xs) - 1) * (p / 100.0)
    f, c = math.floor(k), math.ceil(k)
    if f == c:
        return _ok(xs[int(k)])
    return _ok(xs[f] * (c - k) + xs[c] * (k - f))


@_register("math_round")
async def _math_round(work):
    v = _data(work)
    nd = work.get("digits", 0)
    if isinstance(v, bool) or not isinstance(v, (int, float)) \
            or not isinstance(nd, int) or not 0 <= nd <= 15:
        return _refuse("data must be a number and digits an int in 0..15")
    return _ok(round(float(v), nd))


@_register("math_clamp")
async def _math_clamp(work):
    v, lo, hi = _data(work), work.get("low"), work.get("high")
    if any(isinstance(x, bool) or not isinstance(x, (int, float))
           for x in (v, lo, hi)) or lo > hi:
        return _refuse("data/low/high must be numbers with low <= high")
    return _ok(max(lo, min(hi, v)))


@_register("math_percentage")
async def _math_percentage(work):
    part, whole = work.get("part"), work.get("whole")
    if any(isinstance(x, bool) or not isinstance(x, (int, float))
           for x in (part, whole)) or whole == 0:
        return _refuse("part/whole must be numbers and whole non-zero")
    return _ok(round(100.0 * part / whole, 6))


@_register("gcd_lcm")
async def _gcd_lcm(work):
    d = _data(work)
    if not isinstance(d, list) or len(d) != 2 \
            or not all(isinstance(x, int) and not isinstance(x, bool) for x in d):
        return _refuse("data must be a list of exactly two ints")
    a, b = abs(d[0]), abs(d[1])
    g = math.gcd(a, b)
    return _ok({"gcd": g, "lcm": (a * b) // g if g else 0})


@_register("base_convert")
async def _base_convert(work):
    v, base = _data(work), work.get("base", 10)
    if not isinstance(v, int) or isinstance(v, bool) \
            or not isinstance(base, int) or not 2 <= base <= 36:
        return _refuse("data must be an int and base an int in 2..36")
    if v < 0:
        return _refuse("negative numbers are not supported")
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = ""
    n = v
    while True:
        out = digits[n % base] + out
        n //= base
        if n == 0:
            break
    return _ok(out)


@_register("math_mean")
async def _math_mean(work):
    xs = _nums(work)
    if xs is None:
        return _refuse("data must be a non-empty list of numbers")
    return _ok(statistics.fmean(xs))


# ----------------------------------------------------------------------------
# COLLECTIONS (10) — deterministic list shaping
# ----------------------------------------------------------------------------

@_register("list_sort")
async def _list_sort(work):
    d = _data(work)
    rev = work.get("reverse", False)
    if not isinstance(d, list) or not d or not isinstance(rev, bool) \
            or not all(isinstance(x, (str, int, float)) and not isinstance(x, bool) for x in d):
        return _refuse("data must be a non-empty list of strings or numbers")
    if not all(isinstance(x, type(d[0])) for x in d):
        return _refuse("mixed types cannot be sorted")
    return _ok(sorted(d, reverse=rev))


@_register("list_unique")
async def _list_unique(work):
    d = _data(work)
    if not isinstance(d, list):
        return _refuse("data must be a list")
    out = []
    for x in d:
        if x not in out:
            out.append(x)
    return _ok(out)


@_register("list_reverse")
async def _list_reverse(work):
    d = _data(work)
    if not isinstance(d, list):
        return _refuse("data must be a list")
    return _ok(list(reversed(d)))


@_register("list_flatten")
async def _list_flatten(work):
    d = _data(work)
    if not isinstance(d, list):
        return _refuse("data must be a (possibly nested) list")
    out = []

    def flat(x):
        for item in x:
            if isinstance(item, list):
                flat(item)
            else:
                out.append(item)
    flat(d)
    return _ok(out)


@_register("list_chunk")
async def _list_chunk(work):
    d, n = _data(work), work.get("size")
    if not isinstance(d, list) or not isinstance(n, int) or n < 1:
        return _refuse("data must be a list and size a positive int")
    return _ok([d[i:i + n] for i in range(0, len(d), n)])


@_register("list_zip")
async def _list_zip(work):
    d = _data(work)
    if not isinstance(d, list) or len(d) != 2 \
            or not all(isinstance(x, list) for x in d):
        return _refuse("data must be a list of exactly two lists")
    return _ok([list(p) for p in zip(d[0], d[1])])


@_register("list_group_by")
async def _list_group_by(work):
    d = _data(work)
    if not isinstance(d, list) or not d or not all(isinstance(x, dict) for x in d):
        return _refuse("data must be a non-empty list of objects")
    key = work.get("key")
    if not isinstance(key, str) or not key or key not in d[0]:
        return _refuse("key must be a string present in every object")
    out: Dict[Any, List[Dict[str, Any]]] = {}
    for x in d:
        if key not in x:
            return _refuse(f"key {key!r} missing from an object")
        out.setdefault(x[key], []).append(x)
    return _ok(out)


@_register("list_count_by")
async def _list_count_by(work):
    d = _data(work)
    if not isinstance(d, list):
        return _refuse("data must be a list")
    out: Dict[Any, int] = {}
    for x in d:
        out[x] = out.get(x, 0) + 1
    return _ok(out)


@_register("list_index_of")
async def _list_index_of(work):
    d, item = _data(work), work.get("item")
    if not isinstance(d, list):
        return _refuse("data must be a list")
    try:
        return _ok(d.index(item))
    except ValueError:
        return _ok(-1)


@_register("list_slice")
async def _list_slice(work):
    d = _data(work)
    start, end = work.get("start", 0), work.get("end")
    if not isinstance(d, list) or not isinstance(start, int) \
            or (end is not None and not isinstance(end, int)):
        return _refuse("data must be a list; start/end ints")
    return _ok(d[start:end])


# ----------------------------------------------------------------------------
# Records: the registry metadata for the 80 tools (like internal_tools)
# ----------------------------------------------------------------------------

_STDLIB_SCHEMA_IN: Dict[str, Any] = {
    "type": "object",
    "properties": {"prompt": {"type": "string"}, "text": {"type": "string"},
                  "data": {}, "goal": {"type": "string"}},
    "additionalProperties": True,
}

_STDLIB_SCHEMA_OUT: Dict[str, Any] = {
    "type": "object",
    "required": ["ok"],
    "properties": {"ok": {"type": "boolean"}, "error": {"type": "string"},
                   "tool_output": {}},
    "additionalProperties": True,
}

#: spec -> (capabilities, risk, description) — every handler is hermetic:
#: no network, no model provider, no subprocess, no secrets.
_META: Dict[str, tuple] = {
    "text_stats": (["text.stats"], "low", "Counts chars/words/lines of the text."),
    "text_upper": (["text.case"], "low", "Uppercases the text."),
    "text_lower": (["text.case"], "low", "Lowercases the text."),
    "text_title": (["text.case"], "low", "Title-cases the text."),
    "text_camel": (["text.case"], "low", "Converts the text to camelCase."),
    "text_snake": (["text.case"], "low", "Converts the text to snake_case."),
    "text_kebab": (["text.case"], "low", "Converts the text to kebab-case."),
    "text_slugify": (["text.slug"], "low", "Makes a URL slug from the text (Arabic letters kept)."),
    "text_reverse": (["text.transform"], "low", "Reverses the text."),
    "text_truncate": (["text.transform"], "low", "Truncates the text to a length with an ellipsis."),
    "text_pad_left": (["text.transform"], "low", "Pads the text on the left to a length."),
    "text_pad_right": (["text.transform"], "low", "Pads the text on the right to a length."),
    "text_strip": (["text.transform"], "low", "Strips whitespace (or given chars) from both ends."),
    "text_replace": (["text.transform"], "low", "Replaces occurrences of a substring."),
    "text_split": (["text.transform"], "low", "Splits the text on a separator."),
    "text_join": (["text.transform"], "low", "Joins a list of strings with a separator."),
    "text_wrap": (["text.transform"], "low", "Wraps the text to a width."),
    "text_indent": (["text.transform"], "low", "Indents every line of the text."),
    "text_dedent": (["text.transform"], "low", "Removes common leading whitespace."),
    "text_levenshtein": (["text.compare"], "low", "Levenshtein edit distance between two strings."),
    "text_similarity": (["text.compare"], "low", "Similarity ratio in [0,1] between two strings."),
    "text_strip_prefix": (["text.transform"], "low", "Removes a prefix if present."),
    "text_strip_suffix": (["text.transform"], "low", "Removes a suffix if present."),
    "text_center": (["text.transform"], "low", "Centers the text in a width."),
    "text_swapcase": (["text.case"], "low", "Swaps the case of every letter."),
    "base64_encode": (["encode.base64"], "low", "Base64-encodes the text."),
    "base64_decode": (["encode.base64"], "low", "Base64-decodes the text (fail-closed on bad input)."),
    "url_encode": (["encode.url"], "low", "Percent-encodes the text."),
    "url_decode": (["encode.url"], "low", "Decodes percent-encoding."),
    "hex_encode": (["encode.hex"], "low", "Hex-encodes the text."),
    "hex_decode": (["encode.hex"], "low", "Hex-decodes the text."),
    "sha256_text": (["hash.sha256"], "low", "SHA-256 hex digest of the text."),
    "md5_text": (["hash.md5"], "low", "MD5 hex digest of the text (non-security use)."),
    "crc32_text": (["hash.crc32"], "low", "CRC32 checksum of the text."),
    "uuid4_generate": (["id.uuid"], "low", "Generates one or more random UUID-4 values."),
    "roman_to_int": (["convert.roman"], "low", "Converts a Roman numeral to an int."),
    "int_to_roman": (["convert.roman"], "low", "Converts an int (1..3999) to a Roman numeral."),
    "json_validate": (["json.validate"], "low", "Validates JSON syntax (fail-closed, no guessing)."),
    "json_format": (["json.format"], "low", "Pretty-prints JSON with sorted keys."),
    "json_minify": (["json.format"], "low", "Minifies JSON."),
    "json_path_get": (["json.path"], "low", "Gets a value from JSON by a dotted path."),
    "json_merge": (["json.merge"], "low", "Shallow-merges two JSON objects (right wins)."),
    "csv_parse": (["csv.parse"], "low", "Parses CSV text into a list of row objects."),
    "tsv_parse": (["tsv.parse"], "low", "Parses TSV text into a list of row objects."),
    "kv_parse": (["kv.parse"], "low", "Parses key:value lines into an object."),
    "html_escape": (["html.escape"], "low", "Escapes HTML special characters."),
    "html_unescape": (["html.escape"], "low", "Unescapes HTML entities."),
    "xml_escape": (["xml.escape"], "low", "Escapes the three XML special characters."),
    "xml_unescape": (["xml.escape"], "low", "Unescapes basic XML entities."),
    "json_lines_parse": (["json.parse"], "low", "Parses JSON-lines text into a list."),
    "validate_email": (["validate.email"], "low", "Checks an email address shape."),
    "validate_url": (["validate.url"], "low", "Checks an http(s) URL shape."),
    "validate_ipv4": (["validate.ipv4"], "low", "Checks an IPv4 address (octet ranges enforced)."),
    "validate_uuid": (["validate.uuid"], "low", "Checks a canonical UUID string."),
    "validate_iso_date": (["validate.date"], "low", "Checks an ISO-8601 date/datetime."),
    "validate_number": (["validate.number"], "low", "Checks whether the value is numeric."),
    "validate_regex": (["validate.regex"], "low", "Searches the text with a regex (invalid regex refused)."),
    "regex_extract": (["regex.extract"], "low", "Extracts all regex matches from the text."),
    "regex_replace": (["regex.replace"], "low", "Replaces regex matches in the text."),
    "validate_luhn": (["validate.luhn"], "low", "Validates a Luhn checksum (e.g. card numbers)."),
    "stats_summary": (["stats.summary"], "low", "Count/sum/mean/median/stdev/min/max of a number list."),
    "list_sum": (["math.sum"], "low", "Sums a list of numbers."),
    "list_minmax": (["math.minmax"], "low", "Min and max of a list of numbers."),
    "percentile": (["stats.percentile"], "low", "A percentile of a number list (linear interpolation)."),
    "math_round": (["math.round"], "low", "Rounds a number to given digits."),
    "math_clamp": (["math.clamp"], "low", "Clamps a number into [low, high]."),
    "math_percentage": (["math.percent"], "low", "Percentage of part over whole."),
    "gcd_lcm": (["math.gcd"], "low", "GCD and LCM of two ints."),
    "base_convert": (["math.base"], "low", "Converts an int to another base 2..36."),
    "math_mean": (["stats.mean"], "low", "Arithmetic mean of a number list."),
    "list_sort": (["list.sort"], "low", "Sorts a homogeneous list of strings or numbers."),
    "list_unique": (["list.unique"], "low", "Removes duplicates, order preserved."),
    "list_reverse": (["list.reverse"], "low", "Reverses a list."),
    "list_flatten": (["list.flatten"], "low", "Flattens nested lists."),
    "list_chunk": (["list.chunk"], "low", "Splits a list into fixed-size chunks."),
    "list_zip": (["list.zip"], "low", "Zips two lists into pairs."),
    "list_group_by": (["list.group"], "low", "Groups a list of objects by a key."),
    "list_count_by": (["list.count"], "low", "Counts occurrences of each value."),
    "list_index_of": (["list.find"], "low", "First index of an item (-1 if absent)."),
    "list_slice": (["list.slice"], "low", "Slices a list by start/end."),
}

assert len(_META) == len(HANDLERS) == 80, (len(_META), len(HANDLERS))
assert set(_META) == set(HANDLERS)


def stdlib_tool_records() -> List[ToolRecord]:
    """The 80 registry records (one per handler, honest hermetic metadata)."""
    out = []
    for spec, (caps, risk, desc) in sorted(_META.items()):
        out.append(ToolRecord.model_validate({
            "tool_id": f"{_TOOL_PREFIX}{spec}",
            "version": _SEED_VERSION, "name": spec,
            "description": f"{desc} Deterministic, hermetic (no network, no model, no exec).",
            "source": {"kind": "internal",
                       "uri": "agent_civilization/core/tools_stdlib.py"},
            "capabilities": list(caps),
            "input_schema": _STDLIB_SCHEMA_IN,
            "output_schema": _STDLIB_SCHEMA_OUT,
            "auth_scope": [], "tenant_scope": "request_tenant",
            "risk_class": risk, "side_effects": [], "read_only": True,
            "idempotent": spec != "uuid4_generate",
            "requires_approval": False, "timeout_ms": 5_000,
            "network_allowlist": [], "health_status": "unknown",
            "test_status": "not_tested",
            "implementation_ref": f"{__name__}:{HANDLERS[spec].__name__}",
            "lifecycle_state": "registered",
        }))
    return out




#: the health checker's execution-probe input per tool (a REAL work item the
#: tool's contract accepts — a generic text prompt is NOT valid input for
#: tools that parse structured data, and a contract refusal is not a health
#: failure). This is the single source of truth for stdlib probe/smoke work:
#: tool_health.probe_work_for and the phase-1 runbook both read it.
PROBE_WORK: Dict[str, Dict[str, Any]] = {
    "text_stats": {"text": "health probe"}, "text_upper": {"text": "health probe"},
    "text_lower": {"text": "Health Probe"}, "text_title": {"text": "health probe"},
    "text_camel": {"text": "health probe work"}, "text_snake": {"text": "Health Probe"},
    "text_kebab": {"text": "Health Probe"}, "text_slugify": {"text": "Health, Probe!"},
    "text_reverse": {"text": "probe"}, "text_truncate": {"text": "health probe", "length": 6},
    "text_pad_left": {"text": "hp", "length": 4}, "text_pad_right": {"text": "hp", "length": 4},
    "text_strip": {"text": "  hp  "}, "text_replace": {"text": "aaa", "old": "a", "new": "b"},
    "text_split": {"text": "a,b", "separator": ","}, "text_join": {"data": ["a", "b"], "separator": "-"},
    "text_wrap": {"text": "health probe words", "width": 6}, "text_indent": {"text": "a\nb", "prefix": "  "},
    "text_dedent": {"text": "    a\n    b"}, "text_levenshtein": {"left": "ab", "right": "ac"},
    "text_similarity": {"left": "ab", "right": "ac"}, "text_strip_prefix": {"text": "pre_x", "prefix": "pre_"},
    "text_strip_suffix": {"text": "x_suf", "suffix": "_suf"}, "text_center": {"text": "hp", "length": 6},
    "text_swapcase": {"text": "AbC"},
    "base64_encode": {"text": "health probe"}, "base64_decode": {"text": "aGVsbG8="},
    "url_encode": {"text": "a b"}, "url_decode": {"text": "a%20b"},
    "hex_encode": {"text": "hi"}, "hex_decode": {"text": "6869"},
    "sha256_text": {"text": "health probe"}, "md5_text": {"text": "health probe"},
    "crc32_text": {"text": "health probe"}, "uuid4_generate": {},
    "roman_to_int": {"text": "XIV"}, "int_to_roman": {"data": 14},
    "json_validate": {"text": '{"a": 1}'}, "json_format": {"text": '{"a": 1}'},
    "json_minify": {"text": '{"a": 1}'}, "json_path_get": {"text": '{"a": {"b": 2}}', "path": "a.b"},
    "json_merge": {"data": [{"x": 1}, {"y": 2}]}, "csv_parse": {"text": "a,b\n1,2"},
    "tsv_parse": {"text": "a\tb\n1\t2"}, "kv_parse": {"text": "a: 1\nb: 2"},
    "html_escape": {"text": "<b>&"}, "html_unescape": {"text": "<b>"},
    "xml_escape": {"text": "<&>"}, "xml_unescape": {"text": "<"},
    "json_lines_parse": {"text": '{"a":1}\n{"b":2}'},
    "validate_email": {"text": "a@b.co"}, "validate_url": {"text": "https://a.co/x"},
    "validate_ipv4": {"text": "192.168.1.1"}, "validate_uuid": {"text": "123e4567-e89b-12d3-a456-426614174000"},
    "validate_iso_date": {"text": "2026-11-03T10:00:00Z"}, "validate_number": {"data": 1},
    "validate_regex": {"text": "abc123", "pattern": r"\d+"}, "regex_extract": {"text": "a1b2", "pattern": r"\d"},
    "regex_replace": {"text": "a1b2", "pattern": r"\d", "replacement": "#"},
    "validate_luhn": {"text": "4532015112830366"},
    "stats_summary": {"data": [1, 2, 3]}, "list_sum": {"data": [1, 2, 3]},
    "list_minmax": {"data": [1, 5, 3]}, "percentile": {"data": [1, 2, 3], "p": 50},
    "math_round": {"data": 3.14159, "digits": 2}, "math_clamp": {"data": 9, "low": 1, "high": 5},
    "math_percentage": {"part": 25, "whole": 200}, "gcd_lcm": {"data": [12, 18]},
    "base_convert": {"data": 255, "base": 2}, "math_mean": {"data": [1, 2, 3]},
    "list_sort": {"data": [3, 1, 2]}, "list_unique": {"data": [1, 1, 2]},
    "list_reverse": {"data": [1, 2, 3]}, "list_flatten": {"data": [1, [2, [3]]]},
    "list_chunk": {"data": [1, 2, 3, 4], "size": 2}, "list_zip": {"data": [[1, 2], ["a", "b"]]},
    "list_group_by": {"data": [{"k": "a", "v": 1}, {"k": "b", "v": 2}], "key": "k"},
    "list_count_by": {"data": ["a", "a", "b"]}, "list_index_of": {"data": ["a", "b"], "item": "b"},
    "list_slice": {"data": [1, 2, 3, 4], "start": 1, "end": 3},
}

assert set(PROBE_WORK) == set(HANDLERS) == set(_META)

__all__ = ["HANDLERS", "stdlib_tool_records", "PROBE_WORK"]
