"""SEC-09 (R-177 الدفعة 3، D-134): قراءة ملفات خارج web/dist عبر مسار الواجهة.

اكتُشف أثناء قياس مكون الواجهة (R-177): ``GET /..%2f..%2fserver.py`` بلا
مصادقة أعاد شيفرة server.py كاملة (200، 170KB) — ``WEB_DIST / full_path``
بلا تحقق من الاحتواء، و``%2f`` يُفك إلى ``/`` قبل الوصول للمعالج، والمسار
المطلق (``//etc/passwd``) يستبدل الأساس كليًا في ``pathlib``.

الإصلاح: ``_safe_frontend_file(dist, full_path)`` — يحل المسار (بما فيه
الروابط الرمزية) ويقبل فقط ملفًا داخل dist؛ أي خروج ⇒ None ⇒ 404 (لا
index.html: الخروج ليس مسار SPA). المسارات غير الموجودة داخل dist تبقى
تعود لـindex.html (سلوك SPA قائم).
أحمر أولًا: على main الدالة غير موجودة (السجل 367)، والاستغلال الحي موثق في 368.
"""
import importlib
import os
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def server():
    os.environ.setdefault("AGOS_API_KEY", "test-key-sec09")
    return importlib.import_module("server")


@pytest.fixture()
def dist(tmp_path):
    d = tmp_path / "web" / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    (d / "assets" / "app.js").write_text("js", encoding="utf-8")
    (d / "robots.txt").write_text("ok", encoding="utf-8")
    (tmp_path / "web" / "secret.py").write_text("SECRET", encoding="utf-8")
    (tmp_path / "server.py").write_text("SOURCE", encoding="utf-8")
    return d


def test_files_inside_dist_are_served(server, dist):
    assert server._safe_frontend_file(dist, "robots.txt") == (dist / "robots.txt").resolve()
    assert server._safe_frontend_file(dist, "assets/app.js") == (dist / "assets/app.js").resolve()


@pytest.mark.parametrize("p", [
    "../server.py", "../../server.py", "../secret.py", "assets/../../secret.py",
    "/etc/passwd", "//etc/passwd", "..", "../", "assets/../../../server.py",
])
def test_traversal_and_absolute_paths_are_refused(server, dist, p):
    assert server._safe_frontend_file(dist, p) is None


def test_symlink_escaping_dist_is_refused(server, dist):
    link = dist / "leak.txt"
    try:
        link.symlink_to(dist.parent.parent / "server.py")
    except OSError:
        pytest.fail("symlinks unsupported on this runner — test cannot prove the guard")
    assert server._safe_frontend_file(dist, "leak.txt") is None


def test_missing_file_is_none_so_spa_fallback_applies(server, dist):
    assert server._safe_frontend_file(dist, "dashboard/tasks") is None
    assert server._safe_frontend_file(dist, "") is None


def test_route_uses_the_guard_not_raw_join():
    src = (ROOT / "server.py").read_text(encoding="utf-8")
    body = src.split("async def serve_frontend")[1].split("\nif __name__")[0]
    assert "_safe_frontend_file(" in body
    assert "file_path = WEB_DIST / full_path" not in body
    # الخروج من dist ⇒ 404 لا index.html
    assert "_escapes_frontend_root(" in body


@pytest.mark.parametrize("p,esc", [
    ("../server.py", True), ("/etc/passwd", True), ("assets/../../x", True),
    ("dashboard/tasks", False), ("assets/../index.html", False), ("", False),
])
def test_escape_detection(server, dist, p, esc):
    assert server._escapes_frontend_root(dist, p) is esc
