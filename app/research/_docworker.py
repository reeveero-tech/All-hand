"""Document parsing in a child process. Run as: python -I _docworker.py <kind> '<limits json>' < bytes > json

This file is executed by path with `-I` (isolated mode), so it must import nothing from `app`. The parent
(`documents.py`) sets CPU, memory and file-size limits before exec and kills it after a wall-clock timeout, so a
hostile or broken file can at worst kill this child, never the server. Nothing here executes document content:
no macros (openpyxl `keep_vba=False`, python-docx reads XML only), no JavaScript (pypdf/pdfplumber parse only),
no external links (openpyxl `keep_links=False`).

Output: one JSON object {"ok": bool, "error": str, "text": str, "tables": [[[cell]]], "pages_total": int,
"pages_read": int, "ocr_pages": [int], "sheets": [...], "notes": [str], "method": str, "meta": {...}}.
"""
from __future__ import annotations

import io
import json
import re
import sys


def _clean(s) -> str:
    return re.sub(r"\s+", " ", str("" if s is None else s)).strip()


def _cap_table(rows, max_rows, max_cols, max_cell):
    out = []
    for r in rows[:max_rows]:
        cells = [_clean(c)[:max_cell] for c in list(r)[:max_cols]]
        if any(cells):
            out.append(cells)
    return out if len(out) >= 2 and max(len(r) for r in out) >= 2 else None


def pdf(data: bytes, lim: dict) -> dict:
    from pypdf import PdfReader
    out = {"ok": True, "text": "", "tables": [], "ocr_pages": [], "notes": [], "method": "pypdf", "meta": {}}
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        try:
            if not reader.decrypt(""):
                return {"ok": False, "error": "encrypted"}
        except Exception:  # noqa: BLE001
            return {"ok": False, "error": "encrypted"}
    total = len(reader.pages)
    n = min(total, lim["max_pages"])
    out["pages_total"], out["pages_read"] = total, n
    try:
        info = reader.metadata or {}
        out["meta"] = {"title": _clean(info.get("/Title", ""))[:300], "author": _clean(info.get("/Author", ""))[:120],
                       "created": _clean(info.get("/CreationDate", ""))[:40], "producer": _clean(info.get("/Producer", ""))[:80]}
    except Exception:  # noqa: BLE001 - metadata is optional
        pass
    parts, size = [], 0
    for i in range(n):
        page = reader.pages[i]
        try:
            txt = page.extract_text() or ""
        except Exception:  # noqa: BLE001 - one bad page must not lose the others
            txt = ""
            out["notes"].append(f"page {i + 1}: text extraction failed")
        has_image = False
        try:
            res = page.get("/Resources") or {}
            xo = res.get("/XObject") if hasattr(res, "get") else None
            if xo:
                xo = xo.get_object()
                has_image = any((xo[k].get_object().get("/Subtype") == "/Image") for k in xo)
        except Exception:  # noqa: BLE001
            pass
        if len(txt.strip()) < 25 and has_image:
            out["ocr_pages"].append(i + 1)  # scanned page: an image with no text layer; we do not claim to read it
        if txt.strip():
            block = f"[صفحة {i + 1}]\n{txt.strip()}"
            if size + len(block) > lim["max_chars"]:
                out["notes"].append(f"text cut at page {i + 1}")
                break
            parts.append(block)
            size += len(block)
    out["text"] = "\n\n".join(parts)
    if lim.get("tables", True):
        try:
            import pdfplumber
            with pdfplumber.open(io.BytesIO(data)) as doc:
                for i, page in enumerate(doc.pages[:lim["max_table_pages"]]):
                    for t in page.extract_tables() or []:
                        rows = _cap_table(t, lim["max_rows"], lim["max_cols"], lim["max_cell"])
                        if rows:
                            out["tables"].append({"page": i + 1, "rows": rows})
                        if len(out["tables"]) >= lim["max_tables"]:
                            break
                    if len(out["tables"]) >= lim["max_tables"]:
                        break
            out["method"] = "pypdf+pdfplumber"
        except Exception as e:  # noqa: BLE001 - text stays usable without tables
            out["notes"].append(f"tables: {type(e).__name__}")
    if total > n:
        out["notes"].append(f"read {n} of {total} pages")
    return out


def docx(data: bytes, lim: dict) -> dict:
    import docx as _docx
    d = _docx.Document(io.BytesIO(data))
    parts, size, notes = [], 0, []
    for p in d.paragraphs:
        t = p.text.strip()
        if not t:
            continue
        style = (p.style.name or "").lower() if p.style is not None else ""
        if style.startswith("heading"):
            t = "## " + t
        if size + len(t) > lim["max_chars"]:
            notes.append("text cut")
            break
        parts.append(t)
        size += len(t)
    tables = []
    for t in d.tables[:lim["max_tables"]]:
        rows = _cap_table([[c.text for c in r.cells] for r in t.rows], lim["max_rows"], lim["max_cols"], lim["max_cell"])
        if rows:
            tables.append({"rows": rows})
    meta = {}
    try:
        cp = d.core_properties
        meta = {"title": _clean(cp.title)[:300], "author": _clean(cp.author)[:120],
                "created": str(cp.created or "")[:40], "modified": str(cp.modified or "")[:40]}
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "text": "\n".join(parts), "tables": tables, "notes": notes, "method": "python-docx", "meta": meta}


def xlsx(data: bytes, lim: dict) -> dict:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True, keep_links=False, keep_vba=False)
    tables, sheets, notes, cells = [], [], [], 0
    names = wb.sheetnames
    for name in names[:lim["max_sheets"]]:
        ws = wb[name]
        rows = []
        for r in ws.iter_rows(max_row=lim["max_rows"], max_col=lim["max_cols"], values_only=True):
            cells += len(r)
            if cells > lim["max_cells"]:
                notes.append(f"cell cap reached in sheet {name}")
                break
            row = ["" if v is None else str(v) for v in r]
            while row and row[-1] == "":
                row.pop()  # read-only sheets report the declared width; trailing empty cells are not data
            if row:
                rows.append(row)
        width = max((len(x) for x in rows), default=0)
        rows = [x + [""] * (width - len(x)) for x in rows]
        capped = _cap_table(rows, lim["max_rows"], lim["max_cols"], lim["max_cell"])
        sheets.append({"name": name[:80], "rows": len(rows)})
        if capped:
            tables.append({"sheet": name[:80], "rows": capped})
        if cells > lim["max_cells"]:
            break
    if len(names) > lim["max_sheets"]:
        notes.append(f"read {lim['max_sheets']} of {len(names)} sheets")
    wb.close()
    text = "\n".join(f"ورقة: {s['name']} ({s['rows']} صفًا)" for s in sheets)
    return {"ok": True, "text": text, "tables": tables, "sheets": sheets, "notes": notes, "method": "openpyxl", "meta": {}}


def html(data: bytes, lim: dict) -> dict:
    import trafilatura
    raw = data.decode(lim.get("charset") or "utf-8", errors="replace")
    txt = trafilatura.extract(raw, include_tables=False, include_comments=False, favor_precision=True,
                              output_format="txt") or ""
    return {"ok": True, "text": txt[:lim["max_chars"]], "tables": [], "notes": [], "method": "trafilatura", "meta": {}}


KINDS = {"pdf": pdf, "docx": docx, "xlsx": xlsx, "html": html}


def main() -> None:
    kind, lim = sys.argv[1], json.loads(sys.argv[2])
    data = sys.stdin.buffer.read(lim["max_bytes"] + 1)
    if len(data) > lim["max_bytes"]:
        print(json.dumps({"ok": False, "error": "too_large"}))
        return
    try:
        out = KINDS[kind](data, lim)
    except MemoryError:
        out = {"ok": False, "error": "memory_limit"}
    except Exception as e:  # noqa: BLE001 - a corrupt file is a result, not a crash
        out = {"ok": False, "error": f"corrupt: {type(e).__name__}: {str(e)[:160]}"}
    sys.stdout.write(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
