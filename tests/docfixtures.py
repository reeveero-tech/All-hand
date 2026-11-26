"""Small documents built in memory for offline tests (no binary fixtures in the repo, no network)."""
from __future__ import annotations

import io


def pdf_bytes(lines: list[str], table: list[list[str]] | None = None, pages: int = 1, image_only: bool = False) -> bytes:
    """A valid PDF: text lines (Helvetica, ASCII) and optionally a ruled table pdfplumber can detect.
    image_only=True: a page with an image and no text layer (what a scan looks like)."""
    objs: list[bytes] = []

    def add(b: bytes) -> int:
        objs.append(b)
        return len(objs)

    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    img = add(b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray /BitsPerComponent 8 /Length 1 >>\nstream\n\x80\nendstream") if image_only else None
    page_ids = []
    pages_obj_index = None
    contents = []
    for p in range(pages):
        ops = []
        if image_only:
            ops.append("q 500 0 0 700 50 50 cm /Im1 Do Q")
        else:
            y = 760
            for ln in lines:
                safe = ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
                ops.append(f"BT /F1 11 Tf 50 {y} Td ({safe}) Tj ET")
                y -= 16
            if table:
                x0, w, h = 50, 150, 20
                top = y - 20
                for r, row in enumerate(table):
                    for c, cell in enumerate(row):
                        x, yy = x0 + c * w, top - r * h
                        ops.append(f"{x} {yy} {w} {h} re S")
                        ops.append(f"BT /F1 10 Tf {x + 4} {yy + 6} Td ({cell}) Tj ET")
        stream = "\n".join(ops).encode()
        contents.append(add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"))
    pages_obj_index = len(objs) + 1 + pages  # after the page objects
    for p in range(pages):
        res = f"/Resources << /Font << /F1 {font} 0 R >>" + (f" /XObject << /Im1 {img} 0 R >>" if img else "") + " >>"
        page_ids.append(add(f"<< /Type /Page /Parent {pages_obj_index} 0 R /MediaBox [0 0 612 792] {res} /Contents {contents[p]} 0 R >>".encode()))
    kids = " ".join(f"{i} 0 R" for i in page_ids)
    assert add(f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode()) == pages_obj_index
    catalog = add(f"<< /Type /Catalog /Pages {pages_obj_index} 0 R >>".encode())
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, b in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + b + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for o in offsets:
        out.write(f"{o:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root {catalog} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def xlsx_bytes(rows: list[list], sheet: str = "Prices") -> bytes:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    for r in rows:
        ws.append(r)
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def docx_bytes(paragraphs: list[str], table: list[list[str]] | None = None) -> bytes:
    import docx
    d = docx.Document()
    for p in paragraphs:
        d.add_paragraph(p)
    if table:
        t = d.add_table(rows=len(table), cols=len(table[0]))
        for i, row in enumerate(table):
            for j, cell in enumerate(row):
                t.cell(i, j).text = str(cell)
    b = io.BytesIO()
    d.save(b)
    return b.getvalue()
