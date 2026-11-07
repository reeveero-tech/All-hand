"""R-177 (M8 P6 — ثاني دفعة من R-193، D-133): خط أساس لكل خدمة.

الخارطة: «يحدد مالك الاختبار SLO وstop limits قبل التشغيل» — لذلك هذه
الدفعة تقيس خط الأساس الحالي لكل خدمة HTTP (بيانات قرار المالك)،
والعتبات في benchmarks/r177/services.json مسودة صريحة (slo_status=draft)
لا تُعتمد إلا بقرار المالك. الأحمال الكبيرة لكل خدمة تُبنى بعد القرار.

أحمر أولًا (السجل 366) ثم أخضر — الدفعة الكاملة بالأرقام هناك.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SERVICES_JSON = ROOT / "benchmarks/r177/services.json"
SERVICE_JS = ROOT / "benchmarks/r177/service_load.js"
RUNNER = ROOT / "scripts/r177_per_service.py"

EXPECTED_MEASURED = (
    "api-health", "api-agents", "api-tasks-read", "api-tasks-submit",
    "api-task-lifecycle", "api-memory", "api-chat", "api-admin-tools",
    "api-sse", "api-auth", "frontend",
)


def _load_services() -> dict:
    return json.loads(SERVICES_JSON.read_text(encoding="utf-8"))


def _server_routes() -> set:
    """كل مسارات server.py — ضمان الجرد الكامل (لا خدمة بلا قياس أو تخطٍ موثق)."""
    src = (ROOT / "server.py").read_text(encoding="utf-8")
    routes = set()
    for m in re.finditer(
            r'@app\.(get|post|put|delete|websocket)\("([^"]+)"', src):
        routes.add(m.group(2))
    return routes


# ------------------------------------------------------------- الجرد والصلاحية

def test_services_file_exists_and_parses():
    data = _load_services()
    assert data["slo_status"] == "draft_pending_owner_decision"
    assert data["k6_version"].startswith("v0.57.0")


def test_all_measured_services_defined():
    data = _load_services()
    for name in EXPECTED_MEASURED:
        svc = data["services"][name]
        assert svc["measured"] is True
        b = svc["baseline"]
        for k in ("duration_s", "arrival_rate", "max_vus", "thresholds"):
            assert k in b, (name, k)
        # العتبات مسودة صريحة — لا SLO صامت
        assert svc["slo_status"] == "draft"
        assert svc["slo_proposal_p95_ms"] > 0


def test_every_server_route_is_claimed():
    """قاعدة القبول: سجل لكل نوع خدمة — كل مسار إما مقيس أو موثق تخطيه/تغطيته."""
    data = _load_services()
    claimed = set()
    for svc in data["services"].values():
        claimed.update(svc["routes"])
    for entry in data.get("skipped", {}).values():
        claimed.update(entry["routes"])
    for entry in data.get("indirect", {}).values():
        claimed.update(entry["routes"])
    missing = _server_routes() - claimed
    assert not missing, f"unclaimed server routes: {sorted(missing)}"


def test_skip_entries_have_reasons():
    data = _load_services()
    for name, entry in data.get("skipped", {}).items():
        assert entry.get("reason"), name
        assert entry["routes"], name


def test_indirect_entries_document_coverage():
    """الخدمات غير-HTTP (worker/orchestrator/LLM adapter/tools): تغطية موثقة عبر المسارات المقيسة."""
    data = _load_services()
    for name, entry in data.get("indirect", {}).items():
        assert entry.get("covered_via"), name
        assert entry["routes"], name


def test_baseline_thresholds_format():
    data = _load_services()
    for name, svc in data["services"].items():
        th = svc["baseline"]["thresholds"]
        assert set(th) == {"http_req_failed", "checks", "http_req_duration"}, name
        assert th["http_req_failed"].startswith("rate<")
        assert th["checks"].startswith("rate>")
        assert "p(95)<" in th["http_req_duration"], name


# ------------------------------------------------------------------- المولد

def test_service_js_routes_every_measured_service():
    js = SERVICE_JS.read_text(encoding="utf-8")
    for name in EXPECTED_MEASURED:
        assert f"'{name}'" in js, name
    # فصل المولد عن الخدمة + النموذج المفتوح + العتبات من البيئة (روح R-176)
    assert "constant-arrival-rate" in js
    assert "TARGET_URL" in js
    assert "THRESHOLDS" in js


def test_sse_stream_measured_by_ttfb_probe():
    """SSE: k6 يقيس الاستطلاع فقط — زمن اتصال التيار (TTFB) مسبار العدّاء."""
    js = SERVICE_JS.read_text(encoding="utf-8")
    runner_src = RUNNER.read_text(encoding="utf-8")
    assert "'api-sse'" in js
    assert "events-poll" in js
    assert "/api/events/stream" not in js  # لا انتظار جسم تيار في k6
    assert "_sse_ttfb_probe" in runner_src
    assert "getresponse" in runner_src  # يكتمل عند الرؤوس لا الجسم


# -------------------------------------------------------------------- العدّاء

def _import_runner():
    spec = importlib.util.spec_from_file_location("r177_runner", RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_runner_config_sha_stable():
    r = _import_runner()
    assert r.config_sha256() == r.config_sha256()
    assert len(r.config_sha256()) == 64


def test_runner_report_shape_and_skip(tmp_path):
    r = _import_runner()
    # خدمة موثقة التخطي: لا تشغيل — تصنيف صريح
    rep = r.run_service_baseline("api-agents-external", out_dir=tmp_path)
    assert rep["classification"] == "skip"
    assert rep["ok"] is False
    assert rep["reason"]


@pytest.fixture(scope="module")
def sut():
    r = _import_runner()
    with r.start_sut() as url:
        yield url


def test_real_baseline_health(sut):
    """دخان حقيقي داخل الحزمة (CI-safe): خدمة واحدة، صغيرة بالتصميم."""
    r = _import_runner()
    rep = r.run_service_baseline(
        "api-health", sut_url=sut,
        extra_thresholds={"http_req_duration": "p(95)<10000"})
    assert rep["classification"] == "measured"
    assert rep["ok"] is True, rep
    assert rep["metrics"]["http_reqs"] > 0


def test_negative_threshold_breach_detected(sut):
    """السلبي: عتبة مستحيلة ⇒ فشل مكتشف صراحة (لا نجاح جوفاء)."""
    r = _import_runner()
    rep = r.run_service_baseline(
        "api-health", sut_url=sut,
        extra_thresholds={"http_req_duration": "p(95)<0.001"})
    assert rep["ok"] is False
    assert rep["thresholds"]["http_req_duration"] is False
    assert rep["k6_exit_code"] != 0
