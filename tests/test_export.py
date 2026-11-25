"""Report export: only the owner can download; the file reflects what the run recorded, including limits."""
from fastapi.testclient import TestClient

from app import export, main
from app.orchestrator import Run

A = {"X-Client-Key": "cli_" + "a" * 32}
B = {"X-Client-Key": "cli_" + "b" * 32}


def _run():
    r = Run(goal="دراسة سوق | القهوة\nسطر ثان", llm_public={"provider": "free", "model": "m"}, owner=main._owner(A["X-Client-Key"]))
    r.status, r.result, r.verdict = "partial", "الخلاصة", {"score": 6, "issues": ["رقم الطلب غير مسند"]}
    r.tasks = {"t1": {"id": "t1", "title": "بحث", "role": "researcher", "status": "done", "output": "ناتج البحث",
                      "attempts": [{"sources": ["https://example.com/a", "javascript:alert(1)", "https://example.com/a"],
                                    "verdict": {"score": 8}}]},
               "t2": {"id": "t2", "title": "حساب", "role": "analyst", "status": "failed", "output": "", "attempts": []}}
    return r


def test_markdown_content():
    md = export.to_markdown(_run().full())
    assert md.startswith("# دراسة سوق | القهوة\n")
    assert "اكتملت جزئيًا" in md and "6/10" in md
    assert "| t1 — بحث | researcher | مُتحقق منها | 8/10 | 1 |" in md
    assert md.count("https://example.com/a") == 1 and "javascript:" not in md  # only http(s), deduplicated
    assert "المهمة t2 (حساب): لم تجتز التحقق." in md and "رقم الطلب غير مسند" in md
    assert "ناتج البحث" in md


def test_export_is_owner_only():
    main.RUNS.clear()
    r = _run()
    main.RUNS[r.id] = r
    c = TestClient(main.app)
    ok = c.get(f"/api/runs/{r.id}/export", headers=A)
    assert ok.status_code == 200 and ok.headers["content-type"].startswith("text/markdown")
    assert f'all-hand-{r.id}.md' in ok.headers["content-disposition"] and "الخلاصة" in ok.text
    assert r.owner not in ok.text
    assert c.get(f"/api/runs/{r.id}/export", headers=B).status_code == 404
    assert c.get("/api/runs/zzz/export", headers=A).status_code == 404
