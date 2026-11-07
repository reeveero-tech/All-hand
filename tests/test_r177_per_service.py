"""R-177 (M8 P6 — R-193، D-133 + D-134): خط أساس لكل خدمة، ثم (الدفعة 3) عتبات معتمدة وحدود إيقاف حية.

الخارطة: «يحدد مالك الاختبار SLO وstop limits قبل التشغيل» — لذلك هذه
الدفعة تقيس خط الأساس الحالي لكل خدمة HTTP (بيانات قرار المالك)،
والعتبات في benchmarks/r177/services.json مسودة صريحة (slo_status=draft)
لا تُعتمد إلا بقرار المالك. الأحمال الكبيرة لكل خدمة تُبنى بعد القرار.

أحمر أولًا (السجل 366) ثم أخضر — الدفعة الكاملة بالأرقام هناك.

الدفعة 3 (D-134، قرار المالك 2026-11-07): الحالة approved، p95 دون تغيير عن
الدفعة 2، p99 للدردشة، حدود إيقاف تُقيَّم أثناء الحمل، والواجهة 200 فقط —
أحمر أولًا (السجل 367) ثم أخضر (السجل 368).
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

# قيم p95 المقترحة في الدفعة 2 (السجل 366) — قرار المالك: تُعتمد كما هي
BATCH2_P95 = {
    "api-health": 200, "api-agents": 300, "api-tasks-read": 300,
    "api-tasks-submit": 500, "api-task-lifecycle": 500, "api-memory": 300,
    "api-chat": 2000, "api-admin-tools": 500, "api-sse": 500, "api-auth": 200,
    "frontend": 300,
}


def test_services_file_exists_and_parses():
    data = _load_services()
    assert data["slo_status"] == "approved"
    assert data["slo_decision"]["decision"] == "D-134"
    assert data["slo_decision"]["previous_status"] == "draft_pending_owner_decision"
    assert data["k6_version"].startswith("v0.57.0")
    assert data["error_policy"] == {"http_req_failed": "rate<0.01", "checks": "rate>0.99"}


def test_p95_adopted_unchanged_from_batch2():
    """قرار المالك: اعتماد p95 المقترحة كما هي — لا تغيير غير مبرر."""
    data = _load_services()
    for name, v in BATCH2_P95.items():
        svc = data["services"][name]
        assert svc["slo_p95_ms"] == v, name
        assert svc["slo_proposal_p95_ms"] == v, name


def test_chat_p99_separate_threshold_and_sufficient_samples():
    data = _load_services()
    chat = data["services"]["api-chat"]
    assert chat["slo_p99_ms"] == 1000
    assert "p(99)<1000" in chat["baseline"]["thresholds"]["http_req_duration"]
    assert "p(95)<2000" in chat["baseline"]["thresholds"]["http_req_duration"]
    # كفاية العينات: خط الأساس يجب أن ينتج n >= 299
    b = chat["baseline"]
    assert b["duration_s"] * b["arrival_rate"] >= data["p99_policy"]["n_min"]
    n = chat["normal"]
    assert n["duration_s"] * n["arrival_rate"] >= data["p99_policy"]["n_min"]
    # p99 لغير الدردشة غير معلن (لا عتبة صامتة)
    for name, svc in data["services"].items():
        if name != "api-chat":
            assert svc.get("slo_p99_ms") is None, name


def test_stop_limits_distinct_from_p99_threshold():
    data = _load_services()
    sl = data["stop_limits"]
    assert sl["latency_multiplier"] == 2.0 and sl["consecutive_windows"] == 2
    assert "not_the_slo" in sl


def test_heavy_profiles_guarded_by_q023():
    r = _import_runner()
    data = _load_services()
    for prof in ("stress", "spike", "soak"):
        h = data["heavy_profiles"][prof]
        assert h["requires_dedicated_env"] is True and h["blocked_on"] == "Q-023"
        with pytest.raises(SystemExit):
            r.profile_params(data["services"]["api-health"], data, prof, False)
        # --allow-dedicated-env وحده لا يكفي (تجاوز مزدوج)
        with pytest.raises(SystemExit):
            r.profile_params(data["services"]["api-health"], data, prof, True)


def test_coverage_matrix_covers_every_component_honestly():
    data = _load_services()
    cov = data["coverage"]
    assert set(cov) == {"api_auth", "task_queue_workers", "orchestrator_agent_paths",
                        "memory_storage", "llm_adapters", "tool_families", "sse",
                        "frontend"}
    allowed = {"measured_direct", "measured_direct_mock", "measured_indirect",
               "excluded", "not_measured"}
    for k, v in cov.items():
        assert v["status"] in allowed, k
        assert v["how"] and v["evidence"] and v["not_covered"], k
    # لا ادعاء مزود حي عند استخدام مزود وهمي
    assert cov["llm_adapters"]["status"] == "measured_direct_mock"
    llm = data["indirect"]["llm-provider-adapter"]["covered_via"]
    assert "مزود مزيف بلا اعتماد خارجي" not in llm
    assert "وهمي" in llm


def test_frontend_never_accepts_404():
    js = SERVICE_JS.read_text(encoding="utf-8")
    assert "'frontend': [200, 404]" not in js
    assert "r.status === 404" not in js.split("function frontendReq")[1].split("}")[0]
    assert "'task detail 200/404'" not in js
    data = _load_services()
    assert data["services"]["frontend"]["requires_web_dist"] is True


def test_frontend_unbuilt_is_not_measured(tmp_path, monkeypatch):
    r = _import_runner()
    monkeypatch.setattr(r, "WEB_DIST", tmp_path / "no-dist")
    rep = r.run_service("frontend", sut_url="http://127.0.0.1:9", out_dir=tmp_path)
    assert rep["classification"] == "not_measured" and rep["ok"] is False
    assert "404 is not a frontend measurement" in rep["reason"]


def test_thresholds_can_only_be_tightened():
    r = _import_runner()
    data = _load_services()
    base = r.approved_thresholds(data["services"]["api-health"], data)
    for loose in ({"http_req_duration": "p(95)<10000"}, {"http_req_failed": "rate<0.5"},
                  {"checks": "rate>0.5"}, {"checks": "rate<1"},
                  {"http_req_failed": "rate>0"}):
        with pytest.raises(ValueError):
            r.merge_tighten_only(base, loose)
    tight = r.merge_tighten_only(base, {"http_req_duration": "p(95)<0.001"})
    assert "p(95)<200" in tight["http_req_duration"]   # المعتمد باقٍ (اتحاد)
    assert "p(95)<0.001" in tight["http_req_duration"]


def test_tampered_declared_threshold_refused(monkeypatch, tmp_path):
    r = _import_runner()
    data = _load_services()
    data["services"]["api-health"]["baseline"]["thresholds"]["http_req_duration"] = "p(95)<9999"
    monkeypatch.setattr(r, "load_services", lambda: data)
    with pytest.raises(SystemExit):
        r.run_service("api-health", sut_url="http://127.0.0.1:9", out_dir=tmp_path)
    data2 = _load_services()
    data2["stop_limits"]["error_rate_max"] = 0.2
    monkeypatch.setattr(r, "load_services", lambda: data2)
    with pytest.raises(r.sl().PolicyError):
        r.run_service("api-health", sut_url="http://127.0.0.1:9", out_dir=tmp_path)
    data3 = _load_services()
    data3["slo_status"] = "draft"
    monkeypatch.setattr(r, "load_services", lambda: data3)
    with pytest.raises(SystemExit):
        r.run_service("api-health", sut_url="http://127.0.0.1:9", out_dir=tmp_path)


def test_all_measured_services_defined():
    data = _load_services()
    for name in EXPECTED_MEASURED:
        svc = data["services"][name]
        assert svc["measured"] is True
        b = svc["baseline"]
        for k in ("duration_s", "arrival_rate", "max_vus", "thresholds"):
            assert k in b, (name, k)
        # الدفعة 3: معتمدة بقرار المالك (D-134) — وملمح normal معرّف
        assert svc["slo_status"] == "approved"
        assert svc["slo_p95_ms"] > 0
        for k in ("duration_s", "arrival_rate", "max_vus"):
            assert k in svc["normal"], (name, k)


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
        assert th["http_req_failed"] == "rate<0.01"
        assert th["checks"] == "rate>0.99"
        dur = th["http_req_duration"]
        dur = dur if isinstance(dur, list) else [dur]
        assert f"p(95)<{svc['slo_p95_ms']}" in dur, name


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
def sut_proc():
    r = _import_runner()
    with r.sut_process() as sut:
        yield sut


@pytest.fixture(scope="module")
def sut(sut_proc):
    return sut_proc.url


def test_real_baseline_health(sut_proc):
    """دخان حقيقي داخل الحزمة (CI-safe) بالعتبات المعتمدة نفسها (لا إرخاء) —
    والسلبي لحدود الإيقاف: تشغيل سليم لا يُوقف، والمسابير الأربعة عملت."""
    r = _import_runner()
    rep = r.run_service_baseline("api-health", sut_url=sut_proc.url,
                                 tenants=sut_proc.tenants)
    assert rep["classification"] == "measured"
    assert rep["ok"] is True, rep
    assert rep["metrics"]["http_reqs"] > 0
    assert rep["stopped"] is False and rep["stop"]["decision"]["stopped"] is False
    assert all(rep["probe_counts"][k] > 0 for k in ("health", "ready", "isolation",
                                                    "integrity"))
    assert rep["sentinel_id"]
    # الكمّيات من العينات الخام تطابق ملخص k6 (العدّاء لا يخترع أرقامًا)
    assert rep["raw_quantiles"]["n"] == rep["metrics"]["http_reqs"]
    # k6 يستوفي خطيًا والتقرير يعلن الرتبة الأقرب: المقارنة الصارمة بطريقة k6 نفسها
    # (لا ترتيب ثابت بين التعريفين؛ الفرق حتى فجوة عينة واحدة).
    # (D-135: الصيغة السابقة قارنت تعريفين مختلفين بسماح 0.01ms ففشلت في CI 42379128155 مرتين بفرق 0.053ms ثم 0.102ms)
    assert abs(rep["raw_quantiles"]["p95_ms_k6_method"] - rep["metrics"]["duration_p95_ms"]) < 0.01
    assert rep["raw_quantiles"]["p95_ms"] is not None


def _fast(data, name, rate=40, vus=48, duration=20):
    data["services"][name]["baseline"].update(
        {"arrival_rate": rate, "max_vus": vus, "duration_s": duration})
    return data


def test_real_stop_s1_error_rate_cuts_the_load(sut_proc, monkeypatch, tmp_path):
    """إيجابي حقيقي S1: مفتاح خاطئ ⇒ 401 ⇒ أخطاء > 1% ⇒ يُقطع k6 قبل نهاية مدته."""
    r = _import_runner()
    data = _fast(_load_services(), "api-memory")
    monkeypatch.setattr(r, "load_services", lambda: data)
    t0 = __import__("time").monotonic()
    rep = r.run_service("api-memory", sut_url=sut_proc.url, api_key="wrong-key",
                        out_dir=tmp_path, tenants=sut_proc.tenants)
    elapsed = __import__("time").monotonic() - t0
    d = rep["stop"]["decision"]
    assert rep["stopped"] is True and rep["ok"] is False
    assert d["rule"] in ("error_rate", "checks_rate") and d["rule_id"] in ("S1", "S2")
    assert d["measurements"]["total"] >= 100
    assert elapsed < 20 + 10, elapsed  # قُطع ولم يكمل مدته


def test_real_stop_s3_p95_two_windows(sut_proc, monkeypatch, tmp_path):
    """إيجابي حقيقي S3: SLO اختباري 0.5ms (أي p95 حقيقي > 1ms) ⇒ إيقاف بعد نافذتين."""
    r = _import_runner()
    data = _fast(_load_services(), "api-health")
    data["services"]["api-health"]["slo_p95_ms"] = 0.5
    data["services"]["api-health"]["baseline"]["thresholds"]["http_req_duration"] = "p(95)<0.5"
    monkeypatch.setattr(r, "load_services", lambda: data)
    rep = r.run_service("api-health", sut_url=sut_proc.url, out_dir=tmp_path,
                        tenants=sut_proc.tenants)
    d = rep["stop"]["decision"]
    assert rep["stopped"] is True and rep["ok"] is False, d
    assert d["rule"] == "p95_window" and d["rule_id"] == "S3"
    assert len(d["measurements"]["windows"]) == 2
    assert all(w["value_ms"] > 1.0 for w in d["measurements"]["windows"])


def test_real_stop_s5_sut_crash_immediate(tmp_path):
    """إيجابي حقيقي S5: SIGKILL للخدمة أثناء الحمل ⇒ إيقاف فوري بفشل الصحة/الاستعداد."""
    r = _import_runner()
    with r.sut_process() as sut:
        fired = {}

        def inject(t):
            if t >= 4 and not fired:
                fired["at"] = t
                sut.kill()
        rep = r.run_service("api-health", sut_url=sut.url, out_dir=tmp_path,
                            tenants=sut.tenants, inject=inject)
    d = rep["stop"]["decision"]
    assert fired and rep["stopped"] is True and rep["ok"] is False
    assert d["rule"] in ("health_failure", "readiness_failure"), d
    assert d["rule_id"] == "S5"
    assert d["at_s"] - fired["at"] < 5.0  # كشف خلال ثوانٍ


def test_negative_threshold_breach_detected(sut):
    """السلبي: عتبة مستحيلة ⇒ فشل مكتشف صراحة (لا نجاح جوفاء)."""
    r = _import_runner()
    rep = r.run_service_baseline(
        "api-health", sut_url=sut,
        extra_thresholds={"http_req_duration": "p(95)<0.001"})
    assert rep["ok"] is False
    assert rep["thresholds"]["http_req_duration"] is False
    assert rep["k6_exit_code"] != 0
