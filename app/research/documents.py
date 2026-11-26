"""Reading public documents (PDF, Word, Excel) and the main text of web pages, safely and within limits.

- The real type comes from the bytes (magic numbers, the parts inside an OOXML zip), not from the extension or
  the server's Content-Type alone. A ".pdf" link that returns an HTML login page is read as HTML and flagged.
- Parsing runs in a child process (`_docworker.py`) with CPU, address-space and file-size limits and a
  wall-clock timeout, at most DOC_CONCURRENCY at a time. A corrupt or hostile file fails that one document;
  the run goes on.
- Limits (env-tunable): bytes, PDF pages (text and tables), sheets, rows, columns, cells, characters.
- OOXML zips are checked before parsing: entry count, total uncompressed size, compression ratio (zip bombs).
- Nothing is executed: macro-enabled files are read as data and flagged; legacy binary Office (OLE) and
  PowerPoint are reported as unsupported instead of guessed at.
- A scanned PDF page (an image with no text layer) is listed in `ocr_pages`; its content is not claimed.
- Tables keep rows and columns.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import re
import resource
import sys
import zipfile

WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_docworker.py")

DOC_MAX_BYTES = int(os.environ.get("DOC_MAX_BYTES", str(10 * 2**20)))
LIMITS = {
    "max_bytes": DOC_MAX_BYTES,
    "max_pages": int(os.environ.get("DOC_MAX_PAGES", "40")),
    "max_table_pages": int(os.environ.get("DOC_MAX_TABLE_PAGES", "15")),
    "max_sheets": int(os.environ.get("DOC_MAX_SHEETS", "6")),
    "max_rows": int(os.environ.get("DOC_MAX_ROWS", "200")),
    "max_cols": int(os.environ.get("DOC_MAX_COLS", "30")),
    "max_cells": int(os.environ.get("DOC_MAX_CELLS", "6000")),
    "max_cell": 200,
    "max_tables": 20,
    "max_chars": int(os.environ.get("DOC_MAX_CHARS", "120000")),
}
DOC_TIMEOUT = float(os.environ.get("DOC_TIMEOUT_S", "25"))
DOC_MEMORY_MB = int(os.environ.get("DOC_MEMORY_MB", "400"))
DOC_CPU_S = int(os.environ.get("DOC_CPU_S", "20"))
DOC_CONCURRENCY = int(os.environ.get("DOC_CONCURRENCY", "1"))
ZIP_MAX_ENTRIES = 3000
ZIP_MAX_UNCOMPRESSED = 120 * 2**20
ZIP_MAX_RATIO = 120

DOC_KINDS = ("pdf", "docx", "xlsx")
_sem: dict = {"s": None}
STATS = {"parsed": 0, "failed": 0, "timeouts": 0, "unsupported": 0}


def available() -> dict:
    """Which parsers are installed in this interpreter (the worker uses the same one)."""
    import importlib.util
    return {k: importlib.util.find_spec(m) is not None for k, m in
            (("pdf", "pypdf"), ("pdf_tables", "pdfplumber"), ("docx", "docx"), ("xlsx", "openpyxl"), ("html", "trafilatura"))}


def sniff(data: bytes, ctype: str = "", url: str = "") -> dict:
    """{"kind": pdf|docx|xlsx|pptx|ole|zip|html|json|xml|text|binary|empty, "macros": bool, "mismatch": str}.
    The bytes decide; the declared type and extension only produce a `mismatch` note."""
    head = (data or b"")[:2048]
    declared = _declared(ctype, url)
    out = {"kind": "binary", "macros": False, "mismatch": ""}
    if not data:
        out["kind"] = "empty"
    elif b"%PDF-" in head[:1024]:
        out["kind"] = "pdf"
    elif head.startswith(b"PK\x03\x04"):
        out["kind"] = "zip"
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = set(z.namelist())
                ct = z.read("[Content_Types].xml")[:200_000].decode("utf-8", "replace") if "[Content_Types].xml" in names else ""
            if "word/document.xml" in names:
                out["kind"] = "docx"
            elif "xl/workbook.xml" in names:
                out["kind"] = "xlsx"
            elif "ppt/presentation.xml" in names:
                out["kind"] = "pptx"
            out["macros"] = "macroEnabled" in ct or "vbaProject" in " ".join(names)
        except (zipfile.BadZipFile, KeyError, ValueError, RuntimeError, OSError):
            out["kind"] = "zip"
    elif head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        out["kind"] = "ole"  # legacy .doc/.xls/.ppt: not parsed (no safe light reader), reported as such
    else:
        low = head.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
        if low.startswith((b"<!doctype html", b"<html")) or b"<html" in low[:600] or ("html" in ctype and b"<" in low[:200]):
            out["kind"] = "html"
        elif low.startswith((b"{", b"[")) and "json" in ctype:
            out["kind"] = "json"
        elif low.startswith(b"<?xml") or low.startswith((b"<rss", b"<feed", b"<urlset", b"<sitemapindex")):
            out["kind"] = "xml"
        elif _is_text(head):
            out["kind"] = "text"
    if declared and declared != out["kind"] and not (declared == "text" and out["kind"] in ("json", "xml", "html")):
        out["mismatch"] = f"declared {declared}, content is {out['kind']}"
    return out


def _declared(ctype: str, url: str) -> str:
    c = (ctype or "").lower()
    path = re.sub(r"[?#].*$", "", (url or "").lower())
    if "pdf" in c or path.endswith(".pdf"):
        return "pdf"
    if "wordprocessingml" in c or path.endswith((".docx", ".docm")):
        return "docx"
    if "spreadsheetml" in c or path.endswith((".xlsx", ".xlsm")):
        return "xlsx"
    if "html" in c:
        return "html"
    return ""


def is_document_hint(ctype: str, url: str) -> bool:
    """Before downloading: does the server or URL announce a document (so the larger byte cap applies)?"""
    c = (ctype or "").lower()
    return _declared(ctype, url) in DOC_KINDS or "officedocument" in c or "octet-stream" in c or "msword" in c or "ms-excel" in c


def _is_text(b: bytes) -> bool:
    if not b:
        return False
    try:
        b.decode("utf-8")
        return b.count(b"\x00") == 0
    except UnicodeDecodeError:
        return sum(1 for x in b if x < 9 or 13 < x < 32) < len(b) * 0.02


def zip_guard(data: bytes) -> str | None:
    """Reason to refuse an OOXML zip before any parser touches it, or None."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            infos = z.infolist()
            if len(infos) > ZIP_MAX_ENTRIES:
                return "zip_too_many_entries"
            total = sum(i.file_size for i in infos)
            packed = max(1, sum(i.compress_size for i in infos))
            if total > ZIP_MAX_UNCOMPRESSED:
                return "zip_too_large_uncompressed"
            if total / packed > ZIP_MAX_RATIO:
                return "zip_bomb_ratio"
    except (zipfile.BadZipFile, ValueError, RuntimeError, OSError):
        return "corrupt_zip"
    return None


def _limits():  # child, before exec
    mem = DOC_MEMORY_MB * 2**20
    resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
    resource.setrlimit(resource.RLIMIT_CPU, (DOC_CPU_S, DOC_CPU_S))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))  # the worker writes no files
    os.setsid()


async def _run_worker(kind: str, data: bytes, extra: dict | None = None) -> dict:
    if _sem["s"] is None:
        _sem["s"] = asyncio.Semaphore(DOC_CONCURRENCY)
    lim = {**LIMITS, **(extra or {})}
    async with _sem["s"]:
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-I", WORKER, kind, json.dumps(lim), stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env={"PATH": "/usr/bin:/bin"},
                preexec_fn=_limits)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"worker_start: {type(e).__name__}"}
        try:
            out, err = await asyncio.wait_for(proc.communicate(data), timeout=DOC_TIMEOUT)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
            STATS["timeouts"] += 1
            return {"ok": False, "error": "timeout"}
    try:
        res = json.loads(out.decode("utf-8", "replace") or "{}")
    except ValueError:
        res = {}
    if not res:
        why = "memory_limit" if b"MemoryError" in (err or b"") else f"worker_exit_{proc.returncode}"
        return {"ok": False, "error": why}
    return res


async def parse(data: bytes, ctype: str = "", url: str = "") -> dict:
    """Read a downloaded document. Always returns a dict with `ok`; never raises.
    {"ok", "kind", "error", "text", "tables": [{"rows", "page"|"sheet"}], "pages_total", "pages_read",
     "ocr_pages", "sheets", "notes", "method", "meta", "macros", "mismatch", "completeness"}"""
    s = sniff(data, ctype, url)
    base = {"kind": s["kind"], "macros": s["macros"], "mismatch": s["mismatch"], "tables": [], "text": "", "notes": []}
    if s["kind"] in ("pptx", "ole", "zip", "binary", "empty"):
        STATS["unsupported"] += 1
        return {**base, "ok": False, "error": f"unsupported_{s['kind']}"}
    if s["kind"] not in DOC_KINDS:
        return {**base, "ok": False, "error": "not_a_document"}
    if len(data) > LIMITS["max_bytes"]:
        return {**base, "ok": False, "error": "too_large"}
    if s["kind"] in ("docx", "xlsx"):
        why = zip_guard(data)
        if why:
            STATS["failed"] += 1
            return {**base, "ok": False, "error": why}
    res = await _run_worker(s["kind"], data)
    out = {**base, **res}
    out["kind"] = s["kind"]
    if s["macros"]:
        out["notes"] = list(out.get("notes") or []) + ["macro-enabled file: read as data, macros not run"]
    if out.get("ok"):
        STATS["parsed"] += 1
        pages_total, pages_read = out.get("pages_total") or 0, out.get("pages_read") or 0
        partial = (pages_total and pages_read < pages_total) or any("cut" in n or "cap" in n or " of " in n for n in out.get("notes") or [])
        if s["kind"] == "pdf" and pages_read and len(out.get("ocr_pages") or []) >= pages_read:
            out["completeness"] = "needs_ocr"
        else:
            out["completeness"] = "partial" if partial or out.get("ocr_pages") else "full"
    else:
        STATS["failed"] += 1
    return out


async def main_text(raw_html: bytes, charset: str = "utf-8") -> str | None:
    """Main content of a web page via Trafilatura in the worker; None if unavailable or it found nothing."""
    if not available().get("html"):
        return None
    res = await _run_worker("html", raw_html[:LIMITS["max_bytes"]], {"charset": charset})
    txt = (res.get("text") or "").strip() if res.get("ok") else ""
    return txt or None


def tables_to_rows(tables: list) -> list[list[list[str]]]:
    return [t["rows"] if isinstance(t, dict) else t for t in tables or []]
