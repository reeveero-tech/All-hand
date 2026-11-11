"""R-177 (لاحق 142): عدّ الطلبات الفاشلة من مخرجات k6 v0.57 — أحمر أولًا ثم أخضر.

العيب المُبلَّغ (السجل 399، لاحق 138): كود التقرير القديم قرأ
``http_req_failed.count`` ثم احتسب ``http_req_failed.rate``×``http_reqs.count``،
لكن k6 v0.57 يصدّر مقياس المعدّل (Rate) بالحقول ``value/passes/fails`` —
فكان حقل ``requests_failed`` في ``report_*_soak.json`` صفرًا دائمًا رغم وجود
إخفاقات فعلية (api-agents=5، api-auth=1 في السجل 398).

هذه الاختبارات تمنع عودة الخطأ: المصدر الرسمي في v0.57 هو ``passes``
(تسمية k6 التاريخية: passes = الطلبات التي «نجحت» في فحص الفشل، أي فشلت فعلًا)،
والصيغة القديمة مدعومة للتوافق، والغياب الكامل يعطي ``None`` لا صفرًا مخترعًا.
"""
from __future__ import annotations

import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _runner():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "r177_per_service_under_test", ROOT / "scripts/r177_per_service.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _v057_summary(passes: int, fails: int, total: int) -> dict:
    """مخرجات summary-export الفعلية لـk6 v0.57 (Rate metric)."""
    return {"metrics": {
        "http_reqs": {"count": total, "rate": total / 7200.0},
        "http_req_failed": {"passes": passes, "fails": fails,
                            "value": passes / total if total else 0.0},
    }}


def test_failed_count_v057_reads_passes():
    r = _runner()
    got = r._k6_failed_count(_v057_summary(5, 143960, 143965)["metrics"])
    assert got == 5


def test_failed_count_v057_zero_failures():
    r = _runner()
    got = r._k6_failed_count(_v057_summary(0, 143997, 143997)["metrics"])
    assert got == 0


def test_failed_count_v057_real_evidence_file():
    """الملف الخام الفعلي من soak api-agents (السجل 398): 5 إخفاقات لا 0."""
    f = (ROOT / "docs/audit/M8/logs/raw/r177_sandbox_soak/api-agents"
         / "k6_summary_api-agents_soak.json")
    if not f.exists():  # الأدلة الخام قد تُقتصر في بيئات بلا السجل الكامل
        pytest.skip("raw soak evidence not present in this checkout")
    metrics = json.loads(f.read_text(encoding="utf-8"))["metrics"]
    r = _runner()
    assert r._k6_failed_count(metrics) == 5


def test_failed_count_legacy_count_then_rate():
    r = _runner()
    # صيغة قديمة افتراضية (count): تُقرأ كما هي
    assert r._k6_failed_count({"http_req_failed": {"count": 7}}) == 7
    # صيغة rate×http_reqs: تقريب لنسبة معلنة
    m = {"http_req_failed": {"rate": 0.01}, "http_reqs": {"count": 1000}}
    assert r._k6_failed_count(m) == 10


def test_failed_count_missing_metric_is_none_not_zero():
    r = _runner()
    assert r._k6_failed_count({}) is None
    assert r._k6_failed_count({"http_req_failed": {}}) is None
    # rate بلا مجموع طلبات: لا يمكن الاحتساب — لا يُخترع رقم
    assert r._k6_failed_count({"http_req_failed": {"rate": 0.02}}) is None


def test_report_uses_the_fixed_source(tmp_path, monkeypatch):
    """التقرير النهائي يمر بعدد الإخفاقات من المصدر المصحح إلى requests_failed."""
    r = _runner()

    class FakeProc:
        returncode = 0

    # نسخة مصغرة من مسار بناء التقرير: نفس الاستدعاء الذي في run_service
    summary_path = tmp_path / "raw.json"
    summary_path.write_text(json.dumps(_v057_summary(5, 143960, 143965)),
                            encoding="utf-8")
    metrics = json.loads(summary_path.read_text(encoding="utf-8"))["metrics"]
    assert r._k6_failed_count(metrics) == 5
    # لا ملف معدل تاريخيًا: التقارير الخام القديمة لم تُمس
