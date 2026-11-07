"""R-177 دفعة 3 (D-134): حدود الإيقاف — حالات إيجابية وسلبية لكل قاعدة، ومنع التجاوز.

قرار المالك (2026-11-07): أخطاء > 1% ⇒ إيقاف؛ فحوص < 99% ⇒ إيقاف؛ p95 > 2×SLO
لنافذتين متتاليتين ⇒ إيقاف؛ p99 الدردشة > 2× عتبته لنافذتين ⇒ إيقاف؛ فشل
الصحة/الاستعداد أو انتهاك العزل أو خطر على سلامة البيانات ⇒ إيقاف فوري.

المحرك نقي هنا (لا شبكة)؛ والربط الحي بعطل حقيقي في tests/test_r177_per_service.py.
أحمر أولًا (السجل 367) ثم أخضر.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
MOD = ROOT / "scripts/r177_stop_limits.py"
SERVICES_JSON = ROOT / "benchmarks/r177/services.json"


_SL_CACHE = {}


def _sl():
    """وحدة واحدة لكل الجلسة (هوية PolicyError ثابتة)."""
    if "m" in _SL_CACHE:
        return _SL_CACHE["m"]
    spec = importlib.util.spec_from_file_location("r177_stop_limits_t", MOD)
    m = importlib.util.module_from_spec(spec)
    sys.modules["r177_stop_limits_t"] = m  # dataclasses تحتاج الوحدة مسجلة
    spec.loader.exec_module(m)
    _SL_CACHE["m"] = m
    return m


def _raw():
    return json.loads(SERVICES_JSON.read_text(encoding="utf-8"))["stop_limits"]


def _policy(p95=200.0, p99=None, **over):
    raw = dict(_raw())
    raw.update(over)
    return _sl().validate_policy(raw, p95, p99)


# ------------------------------------------------------- السياسة والأرضية

def test_configured_policy_matches_owner_decision_exactly():
    raw = _raw()
    assert raw["error_rate_max"] == 0.01
    assert raw["checks_rate_min"] == 0.99
    assert raw["latency_multiplier"] == 2.0
    assert raw["consecutive_windows"] == 2
    assert set(raw["immediate"]) == {"health_failure", "readiness_failure",
                                     "tenant_isolation_violation",
                                     "data_integrity_violation"}
    p = _policy(2000.0, 1000.0)
    assert p.p95_stop_ms == 4000.0 and p.p99_stop_ms == 2000.0


@pytest.mark.parametrize("field,value", [
    ("error_rate_max", 0.02), ("error_rate_max", 0.5),
    ("checks_rate_min", 0.98), ("checks_rate_min", 0.5),
    ("latency_multiplier", 2.5), ("latency_multiplier", 100),
    ("consecutive_windows", 3), ("consecutive_windows", 1000),
    ("min_requests_for_rate", 10_000), ("min_window_samples", 10_000),
    ("window_s", 3600),
])
def test_weaker_than_owner_floor_is_refused(field, value):
    """منع التجاوز: لا يمكن إرخاء أي حد من الإعداد — يُرفض قبل التشغيل."""
    with pytest.raises(_sl().PolicyError):
        _policy(**{field: value})


@pytest.mark.parametrize("drop", ["health_failure", "readiness_failure",
                                  "tenant_isolation_violation", "data_integrity_violation"])
def test_immediate_rules_cannot_be_removed(drop):
    imm = [r for r in _raw()["immediate"] if r != drop]
    with pytest.raises(_sl().PolicyError):
        _policy(immediate=imm)


def test_missing_or_empty_policy_refused_and_slo_required():
    sl = _sl()
    with pytest.raises(sl.PolicyError):
        sl.validate_policy({}, 200.0)
    with pytest.raises(sl.PolicyError):
        sl.validate_policy(None, 200.0)
    raw = dict(_raw())
    raw.pop("consecutive_windows")
    with pytest.raises(sl.PolicyError):
        sl.validate_policy(raw, 200.0)
    with pytest.raises(sl.PolicyError):
        sl.validate_policy(_raw(), None)


def test_stricter_policy_is_allowed():
    p = _policy(error_rate_max=0.005, checks_rate_min=0.995, latency_multiplier=1.5,
                consecutive_windows=1)
    assert p.error_rate_max == 0.005 and p.consecutive_windows == 1


# --------------------------------------------------------------- مساعدات

def _feed(ev, n, t_start, t_end, ms=1.0, failed_every=0, check_fail_every=0):
    for i in range(n):
        t = t_start + (t_end - t_start) * i / max(1, n)
        ev.add_duration(t, ms)
        ev.add_failed_flag(t, bool(failed_every) and i % failed_every == 0)
        ev.add_check(t, not (bool(check_fail_every) and i % check_fail_every == 0))


# ------------------------------------------------------------------- S1

def test_s1_error_rate_positive_stops_with_measurements():
    sl = _sl()
    ev = sl.StopEvaluator(_policy(), t0=0)
    _feed(ev, 200, 0, 4.9, failed_every=50)  # 4/200 = 2%
    assert ev.decision.stopped and ev.decision.rule == "error_rate"
    assert ev.decision.rule_id == "S1"
    m = ev.decision.measurements
    assert m["rate"] > 0.01 and m["limit"] == 0.01 and m["total"] >= 100


def test_s1_error_rate_negative_at_one_percent_does_not_stop():
    """بالضبط 1% ليست «أكثر من 1%» — لا إيقاف (والحد لا يُطلق زيفًا)."""
    sl = _sl()
    ev = sl.StopEvaluator(_policy(), t0=0)
    for i in range(1000):
        t = 0.01 * i
        ev.add_duration(t, 1.0)
        ev.add_failed_flag(t, i % 100 == 99)  # 10/1000 = 1.0% تمامًا وتراكميًا ≤ 1%
        ev.add_check(t, True)
    ev.close_windows(100, final=True)
    assert not ev.decision.stopped, ev.decision.as_dict()


def test_s1_late_burst_not_diluted_by_cumulative():
    """انفجار أخطاء متأخر في نافذة واحدة يوقف رغم أن التراكمي تحت 1%."""
    sl = _sl()
    ev = sl.StopEvaluator(_policy(), t0=0)
    _feed(ev, 5000, 0, 49.9)                 # 5000 نظيفة
    _feed(ev, 200, 50.0, 54.9, failed_every=20)  # 10/200 = 5% في النافذة 10
    ev.close_windows(70)
    assert ev.decision.stopped and ev.decision.rule == "error_rate"
    assert ev.decision.measurements["scope"] == "window"


# ------------------------------------------------------------------- S2

def test_s2_checks_positive_and_negative():
    sl = _sl()
    ev = sl.StopEvaluator(_policy(), t0=0)
    _feed(ev, 300, 0, 4.9, check_fail_every=20)  # 95%
    assert ev.decision.stopped and ev.decision.rule == "checks_rate"
    ev2 = sl.StopEvaluator(_policy(), t0=0)
    _feed(ev2, 300, 0, 4.9, check_fail_every=300)  # 1/300 ⇒ 99.67%
    ev2.close_windows(10, final=True)
    assert not ev2.decision.stopped


# ------------------------------------------------------------------- S3

def test_s3_p95_two_consecutive_windows_positive():
    sl = _sl()
    ev = sl.StopEvaluator(_policy(p95=200.0), t0=0)
    _feed(ev, 50, 0, 4.9, ms=450)    # النافذة 0: p95=450 > 400
    _feed(ev, 50, 5, 9.9, ms=450)    # النافذة 1: p95=450 > 400
    ev.close_windows(20)
    assert ev.decision.stopped and ev.decision.rule == "p95_window"
    assert ev.decision.rule_id == "S3"
    w = ev.decision.measurements["windows"]
    assert [x["window"] for x in w] == [0, 1]
    assert ev.decision.measurements["limit_ms"] == 400.0


def test_s3_single_window_or_non_consecutive_does_not_stop():
    sl = _sl()
    ev = sl.StopEvaluator(_policy(p95=200.0), t0=0)
    _feed(ev, 50, 0, 4.9, ms=450)    # خرق
    _feed(ev, 50, 5, 9.9, ms=10)     # سليمة ⇒ تكسر السلسلة
    _feed(ev, 50, 10, 14.9, ms=450)  # خرق
    _feed(ev, 50, 15, 19.9, ms=10)
    ev.close_windows(40, final=True)
    assert not ev.decision.stopped


def test_s3_exactly_2x_is_not_over():
    sl = _sl()
    ev = sl.StopEvaluator(_policy(p95=200.0), t0=0)
    _feed(ev, 50, 0, 4.9, ms=400)
    _feed(ev, 50, 5, 9.9, ms=400)
    ev.close_windows(20, final=True)
    assert not ev.decision.stopped


def test_s3_insufficient_window_cannot_reset_streak():
    """منع الإخفاء: نافذة بعينات قليلة (إبطاء حتى تقل الردود) لا تكسر سلسلة الخرق."""
    sl = _sl()
    ev = sl.StopEvaluator(_policy(p95=200.0), t0=0)
    _feed(ev, 50, 0, 4.9, ms=450)
    _feed(ev, 3, 5, 9.9, ms=1)        # غير كافية (< 10)
    _feed(ev, 50, 10, 14.9, ms=450)
    ev.close_windows(30)
    assert ev.decision.stopped and ev.decision.rule == "p95_window"


# ------------------------------------------------------------------- S4

def test_s4_chat_p99_positive_independent_of_p95():
    """p99 > 2000ms لنافذتين يوقف حتى وp95 تحت حدها (العتبتان مستقلتان)."""
    sl = _sl()
    ev = sl.StopEvaluator(_policy(p95=2000.0, p99=1000.0), t0=0)
    for w in (0, 1):
        for i in range(100):
            t = w * 5 + i * 0.04
            ev.add_duration(t, 2500.0 if i >= 97 else 5.0)  # 3% ذيل ⇒ p99=2500
            ev.add_failed_flag(t, False)
            ev.add_check(t, True)
    ev.close_windows(30)
    assert ev.decision.stopped and ev.decision.rule == "p99_window"
    assert ev.decision.rule_id == "S4"
    assert not any(b["rule"] == "p95_window" for b in ev.breaches)


def test_s4_p99_over_slo_but_under_stop_limit_does_not_stop():
    """p99=1500ms يخرق عتبة SLO (1000) لكنه دون حد الإيقاف (2000): لا إيقاف —
    عتبة p99 ≠ حد الإيقاف (طلب المالك صراحة)."""
    sl = _sl()
    ev = sl.StopEvaluator(_policy(p95=2000.0, p99=1000.0), t0=0)
    for w in (0, 1, 2):
        for i in range(100):
            t = w * 5 + i * 0.04
            ev.add_duration(t, 1500.0 if i >= 97 else 5.0)
            ev.add_failed_flag(t, False)
            ev.add_check(t, True)
    ev.close_windows(40, final=True)
    assert not ev.decision.stopped
    a = sl.quantile_assessment([1500.0] * 3 + [5.0] * 297, 0.99, 1000.0)
    assert a["verdict"] == "fail"  # لكن حكم SLO على التشغيل فشل


def test_s4_not_applied_to_services_without_p99():
    sl = _sl()
    ev = sl.StopEvaluator(_policy(p95=2000.0, p99=None), t0=0)
    for w in (0, 1):
        for i in range(100):
            ev.add_duration(w * 5 + i * 0.04, 2500.0 if i >= 97 else 5.0)
    ev.close_windows(30, final=True)
    assert not ev.decision.stopped


# ------------------------------------------------------------------- S5

@pytest.mark.parametrize("kind", ["health_failure", "readiness_failure",
                                  "tenant_isolation_violation", "data_integrity_violation"])
def test_s5_immediate_positive(kind):
    sl = _sl()
    ev = sl.StopEvaluator(_policy(), t0=0)
    ev.add_probe(1.5, kind, False, {"status": 503})
    assert ev.decision.stopped and ev.decision.rule == kind
    assert ev.decision.rule_id == "S5" and ev.decision.at_s == 1.5
    assert ev.decision.measurements["detail"] == {"status": 503}


def test_s5_negative_passing_probes_never_stop():
    sl = _sl()
    ev = sl.StopEvaluator(_policy(), t0=0)
    for k in ("health_failure", "readiness_failure", "tenant_isolation_violation",
              "data_integrity_violation"):
        for t in range(50):
            ev.add_probe(t, k, True)
    assert not ev.decision.stopped


def test_unknown_probe_kind_is_an_error_not_ignored():
    with pytest.raises(ValueError):
        _sl().StopEvaluator(_policy(), t0=0).add_probe(0, "made_up", False)


# --------------------------------------------------------- القرار اللاصق

def test_decision_is_sticky_and_all_breaches_logged():
    sl = _sl()
    ev = sl.StopEvaluator(_policy(), t0=0)
    ev.add_probe(1.0, "readiness_failure", False, {"status": 503})
    _feed(ev, 200, 1, 4.9, failed_every=10)
    for i in range(500):  # كم كبير من النجاح بعدها لا يلغي القرار
        ev.add_failed_flag(5 + i * 0.001, False)
        ev.add_probe(5 + i * 0.001, "readiness_failure", True)
    assert ev.decision.rule == "readiness_failure"
    rules = [b["rule"] for b in ev.breaches]
    assert "readiness_failure" in rules and "error_rate" in rules
    rep = ev.report()
    assert rep["decision"]["stopped"] is True
    assert rep["policy"]["owner_floor"]["error_rate_max"] == 0.01


# --------------------------------------------------------------- الإحصاء

def test_p99_sample_sufficiency_math():
    sl = _sl()
    assert sl.min_samples_for_quantile(0.99, 0.95) == 299
    a = sl.quantile_assessment([5.0] * 150, 0.99, 1000.0)   # عينات السجل 366
    assert a["verdict"] == "insufficient_samples" and a["passed"] is False
    b = sl.quantile_assessment([5.0] * 299 + [750.8], 0.99, 1000.0)
    assert b["verdict"] == "pass" and b["upper_bound_ms"] == 750.8
    c = sl.quantile_assessment([5.0] * 299 + [1200.0], 0.99, 1000.0)
    assert c["verdict"] == "fail"  # الحد الأعلى 1200 ≥ 1000 — لا نجاح بالنقطة وحدها


def test_quantile_upper_bound_is_conservative():
    sl = _sl()
    vals = sorted(float(i) for i in range(1, 1001))
    ub = sl.quantile_upper_bound(vals, 0.99, 0.95)
    assert ub >= sl.nearest_rank(vals, 0.99)


def test_tail_review_reports_top_samples():
    sl = _sl()
    s = [{"ms": 5.0, "t_s": i * 0.2, "seq": i, "endpoint": "chat"} for i in range(299)]
    s.append({"ms": 750.8, "t_s": 0.0, "seq": 299, "endpoint": "chat"})
    tr = sl.tail_review(s, 1000.0)
    assert tr["max_ms"] == 750.8 and tr["top"][0]["ms"] == 750.8
    assert tr["over_threshold"] == 0 and tr["over_half_threshold"] == 1


def test_parse_k6_time_nanoseconds():
    t = _sl().parse_k6_time("2026-11-07T05:12:13.123456789+03:00")
    assert abs(t - 1794017533.123456) < 1e-3


# ------------------------------------------- المسابير ضد خادم يسرّب (إيجابي حقيقي)

class _LeakyHandler(BaseHTTPRequestHandler):
    mode = "leak"

    def log_message(self, *a):
        pass

    def _send(self, code, body):
        b = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        key = self.headers.get("X-API-Key", "")
        if self.path.startswith("/api/tasks/"):
            tid = self.path.rsplit("/", 1)[-1]
            if key.startswith("r177b-"):
                return self._send(200 if self.mode == "leak" else 404, {"id": tid})
            if self.mode == "lost":
                return self._send(404, {"detail": "task not found"})
            prompt = "TAMPERED" if self.mode == "tamper" else \
                'validate this JSON: {"ok": true, "probe": "r177"}'
            return self._send(200, {"id": tid, "specialization": "validator",
                                    "prompt": prompt, "status": "completed", "result": {}})
        return self._send(200, {"tasks": []})


def _probes_against(mode, volatile_retention=None):
    runner = ROOT / "scripts/r177_per_service.py"
    spec = importlib.util.spec_from_file_location("r177_runner_probe_t", runner)
    r = importlib.util.module_from_spec(spec)
    sys.modules["r177_runner_probe_t"] = r
    spec.loader.exec_module(r)
    handler = type("H", (_LeakyHandler,), {"mode": mode})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    return r, r.Probes(url, r.Tenants(), volatile_retention=volatile_retention), srv


def test_isolation_probe_detects_cross_tenant_read_and_stops():
    r, p, srv = _probes_against("leak")
    try:
        ok, det = p.isolation(["t1"])
        assert ok is False and "tenant B" in det["leak"]
        ev = r.sl().StopEvaluator(_policy(), t0=0)
        ev.add_probe(0.5, "tenant_isolation_violation", ok, det)
        assert ev.decision.stopped and ev.decision.rule == "tenant_isolation_violation"
    finally:
        srv.shutdown()


def test_isolation_probe_negative_on_correct_404():
    r, p, srv = _probes_against("ok")
    try:
        assert p.isolation(["t1"])[0] is True
        assert p.integrity(["t1"])[0] is True
    finally:
        srv.shutdown()


@pytest.mark.parametrize("mode,needle", [("lost", "lost"), ("tamper", "fields changed")])
def test_integrity_probe_detects_loss_and_tampering(mode, needle):
    r, p, srv = _probes_against(mode)
    try:
        ok, det = p.integrity(["t1"])
        assert ok is False and needle in det["violation"]
    finally:
        srv.shutdown()


def test_integrity_probe_detects_terminal_regression():
    r, p, srv = _probes_against("ok")
    try:
        assert p.integrity(["t1"])[0] is True        # تسجيل البصمة النهائية
        p.terminal_fp["t1"] = "different-fingerprint"  # محاكاة تبدل النتيجة المخزنة
        ok, det = p.integrity(["t1"])
        assert ok is False and "regressed or changed" in det["violation"]
    finally:
        srv.shutdown()


# ------------------- عقد الاحتفاظ المتطاير (R-164) — مُعلن، لا يخفي فقدانًا حقيقيًا
# اكتُشف في قياس الدفعة 3: الحارس (أقدم مهمة منتهية) حُذف بحد MAX_TASKS_KEPT=500
# الموثق في الوضع المتطاير، فأوقف المسبار الحمل «فقدانًا». الإصلاح ضيق ومختبر:

def _flip_to_lost(srv):
    srv.RequestHandlerClass.mode = "lost"


def test_volatile_eviction_of_observed_terminal_task_is_reported_not_stop():
    r, p, srv = _probes_against("ok", volatile_retention=500)
    try:
        assert p.integrity(["t1"])[0] is True       # رُصدت منتهية
        _flip_to_lost(srv)
        ok, det = p.integrity(["t1"])
        assert ok is True
        assert len(p.retention_evictions) == 1       # مُعلن في التقرير — لا يُخفى
        ev = p.retention_evictions[0]
        assert ev["task"] == "t1" and ev["last_seen_terminal"] is True
        assert "MAX_TASKS_KEPT=500" in ev["contract"]
        assert p.integrity(["t1"])[0] is True and len(p.retention_evictions) == 1
    finally:
        srv.shutdown()


def test_volatile_loss_of_never_terminal_task_still_stops():
    """مهمة لم تُرصد منتهية تختفي = فقدان حقيقي حتى في الوضع المتطاير."""
    r, p, srv = _probes_against("lost", volatile_retention=500)
    try:
        ok, det = p.integrity(["t1"])
        assert ok is False and "lost" in det["violation"]
        assert det["last_seen_terminal"] is False and not p.retention_evictions
    finally:
        srv.shutdown()


def test_durable_storage_never_tolerates_404():
    """بلا إعلان تخزين متطاير (الافتراض الصارم/الدائم) أي 404 = انتهاك."""
    r, p, srv = _probes_against("ok")                # volatile_retention=None
    try:
        assert p.integrity(["t1"])[0] is True
        _flip_to_lost(srv)
        ok, det = p.integrity(["t1"])
        assert ok is False and det["last_seen_terminal"] is True
        assert det["storage"] == "durable_or_unknown" and not p.retention_evictions
    finally:
        srv.shutdown()


def test_volatile_retention_read_from_server_source():
    runner = ROOT / "scripts/r177_per_service.py"
    spec = importlib.util.spec_from_file_location("r177_runner_ret_t", runner)
    r = importlib.util.module_from_spec(spec)
    sys.modules["r177_runner_ret_t"] = r
    spec.loader.exec_module(r)
    import re as _re
    src = (ROOT / "server.py").read_text(encoding="utf-8")
    assert r.volatile_task_retention() == int(
        _re.search(r"^MAX_TASKS_KEPT\s*=\s*(\d+)", src, _re.M).group(1))


# --------------------- نافذة محجوبة (censored) — ثغرة كشفها قياس انقطاع التخزين
# الطلب المعلق لا يُنتج عينة زمن حتى يكتمل، فتصبح نوافذ الانقطاع «غير كافية»
# وتتجاهلها S3 — فلا إيقاف رغم تعليق 11s (السجل 368). الإصلاح: بوصول ثابت
# مجدول، اكتمال < 50% من التكرارات المجدولة = نافذة مقطوعة تُحتسب خرقًا.

def _policy_rate(rate, p95=500.0, p99=None):
    return _sl().validate_policy(dict(_raw()), p95, p99, expected_iter_rate=rate)


def _feed_iters(ev, n, t_start, t_end, ms=1.0):
    for i in range(n):
        t = t_start + (t_end - t_start) * i / max(1, n)
        ev.add_duration(t, ms)
        ev.add_failed_flag(t, False)
        ev.add_check(t, True)
        ev.add_iteration(t)


def test_stall_two_censored_windows_stop_s3():
    sl = _sl()
    ev = sl.StopEvaluator(_policy_rate(20), t0=0)       # 100 مجدول/نافذة
    _feed_iters(ev, 100, 0, 4.9)                         # سليمة
    _feed_iters(ev, 5, 5, 9.9)                           # 5% فقط اكتمل
    ev.add_dropped(7, 40)
    _feed_iters(ev, 0, 10, 14.9)                         # لا شيء اكتمل
    ev.close_windows(20)
    d = ev.decision
    assert d.stopped and d.rule == "p95_window" and d.rule_id == "S3"
    assert "censored" in d.reason
    ws = d.measurements["windows"]
    assert [w["window"] for w in ws] == [1, 2] and all(w["censored"] for w in ws)
    assert ws[0]["dropped"] == 40 and ws[0]["expected_iters"] == 100


def test_stall_then_late_slow_completions_still_stop():
    """النمط الحقيقي: نافذة محجوبة ثم تكتمل المعلقات دفعة واحدة ببطء."""
    sl = _sl()
    ev = sl.StopEvaluator(_policy_rate(20), t0=0)
    _feed_iters(ev, 100, 0, 4.9)
    _feed_iters(ev, 2, 5, 9.9)                           # محجوبة
    _feed_iters(ev, 60, 10, 10.5, ms=9000)               # معلقات تكتمل: p95=9000
    ev.close_windows(20)
    assert ev.decision.stopped and ev.decision.rule_id == "S3"
    ws = ev.decision.measurements["windows"]
    assert ws[0].get("censored") and ws[1]["value_ms"] == 9000


def test_single_stalled_window_does_not_stop():
    sl = _sl()
    ev = sl.StopEvaluator(_policy_rate(20), t0=0)
    _feed_iters(ev, 100, 0, 4.9)
    _feed_iters(ev, 10, 5, 9.9)                          # محجوبة وحيدة
    _feed_iters(ev, 100, 10, 14.9)                       # تعافٍ سريع
    _feed_iters(ev, 100, 15, 19.9)
    ev.close_windows(40, final=True)
    assert not ev.decision.stopped
    assert ev.windows[1].get("stalled") is True          # لكنها مسجلة في التقرير


def test_half_completion_is_not_a_stall():
    sl = _sl()
    ev = sl.StopEvaluator(_policy_rate(20), t0=0)
    for k in range(4):
        _feed_iters(ev, 50, 5 * k, 5 * k + 4.9)          # 50% بالضبط ليست < 50%
    ev.close_windows(40, final=True)
    assert not ev.decision.stopped
    assert not any(w.get("stalled") for w in ev.windows)


def test_stall_not_inferred_for_low_rate_or_without_rate():
    sl = _sl()
    for pol in (_policy_rate(0.1), _sl().validate_policy(dict(_raw()), 500.0)):
        ev = sl.StopEvaluator(pol, t0=0)
        _feed_iters(ev, 0, 0, 30)
        ev.close_windows(60, final=True)
        assert not ev.decision.stopped
        assert ev.report()["policy"]["stall_detection_applicable"] is False


def test_final_partial_window_and_first_window_not_stall():
    """بداية k6 والنافذة الأخيرة الجزئية لا تُستدل منهما حجب (لا إيقاف زائف)."""
    sl = _sl()
    ev = sl.StopEvaluator(_policy_rate(20), t0=0)
    _feed_iters(ev, 30, 3.5, 4.9)                        # بدء متأخر
    _feed_iters(ev, 100, 5, 9.9)
    _feed_iters(ev, 10, 10, 10.4)                        # نهاية جزئية
    ev.close_windows(11, final=True)
    assert not ev.decision.stopped


def test_stall_also_counts_for_chat_p99_rule():
    sl = _sl()
    ev = sl.StopEvaluator(_policy_rate(20, p95=2000.0, p99=1000.0), t0=0)
    _feed_iters(ev, 100, 0, 4.9)
    _feed_iters(ev, 1, 5, 9.9)
    _feed_iters(ev, 1, 10, 14.9)
    ev.close_windows(20)
    rules = {b["rule"] for b in ev.breaches}
    assert {"p95_window", "p99_window"} <= rules


def test_expected_rate_must_be_positive():
    with pytest.raises(_sl().PolicyError):
        _policy_rate(0)


def test_k6_trend_quantile_matches_k6_linear_interpolation():
    """D-135 (CI 42379128155): k6's Trend p(q) interpolates linearly at q*(n-1); the
    runner reports nearest-rank. Comparing the two within 0.01ms was luck, not a
    contract. The runner now also computes k6's method from the raw samples so the
    "runner does not invent numbers" check compares like with like."""
    sl = _sl()
    k6q = sl.k6_trend_quantile
    assert k6q([], 0.95) is None
    assert k6q([5.0], 0.95) == 5.0
    vals = [float(i) for i in range(1, 101)]
    assert abs(k6q(vals, 0.95) - 95.05) < 1e-9      # 1 + 0.95*99
    assert abs(k6q(vals, 0.5) - 50.5) < 1e-9
    assert k6q(vals, 1.0) == 100.0 and k6q(vals, 0.0) == 1.0
    # nearest-rank and k6 differ by up to one inter-sample gap — the CI case:
    gap = [1.0] * 94 + [4.0529, 4.106] + [9.0] * 4
    assert sl.nearest_rank(gap, 0.95) == 4.0529      # rank ceil(95) = 95th sample
    assert 4.0529 < k6q(gap, 0.95) < 4.106            # position 94.05 → between 95th and 96th
