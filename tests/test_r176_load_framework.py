"""R-176 (أول دفعة من R-193) — أدوات قياس الحمل: إطار حمل قابل للتكرار.

معيار القبول (خارطة M8 P6): config/version/workload profile/raw results/SHA؛
لا يختلط ضغط generator مع ضغط SUT. الإطار: k6 مثبت النسخة (مفتوح النموذج —
open-model arrival rate) مع ملفات تعريف حمل (profiles) صغيرة للدخان وCI،
والكبيرة محجوزة للبيئة المعزولة (Q-023 — قرار المالك، لا تُشغَّل هنا).

التصنيف: شيفرة حقيقية بلا محاكاة — السجلات هنا تُثبت:
  * الأداة مثبتة النسخة وقابلة للتحقق (SHA256)؛
  * المولد (k6) والخدمة (SUT) عمليتان منفصلتان — الحمل يصيب HTTP حقيقي؛
  * القياس يفشل صراحة عند خرق العتبات (سلبي: عتبة مستحيلة ⇒ فشل مكتشف)؛
  * التقرير الخام: نتائج k6 الخام + SHA الإعداد + النسخ — قابل لإعادة
    الإنتاج (نفس الإعداد ⇒ نفس القياس على نفس العتاد).
"""
import json
import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROFILES = ROOT / "benchmarks" / "r176" / "profiles.json"
LOAD_JS = ROOT / "benchmarks" / "r176" / "load.js"
RUNNER = ROOT / "scripts" / "r176_load_framework.py"

sys.path.insert(0, str(ROOT / "scripts"))

# ---------------------------------------------------------------- الإطار


def _framework():
    import r176_load_framework as fw  # noqa: PLC0415
    return fw


def test_r176_profiles_config_exists_and_valid():
    """الإعداد: ملف تعريف الحمل موجود وصالح — ستة ملفات تعريف، الدخان صغير
    ومحدود (يعمل في CI بلا موارد مدفوعة)، والكبيرة موسومة Q-023."""
    assert PROFILES.exists(), "benchmarks/r176/profiles.json is missing"
    cfg = json.loads(PROFILES.read_text(encoding="utf-8"))
    assert cfg["framework"] == "k6"
    assert cfg["k6_version"].startswith("v"), "k6 version must be pinned"
    assert re.fullmatch(r"[0-9a-f]{64}", cfg["k6_tarball_sha256"]), \
        "the k6 tarball must be SHA256-pinned (reproducible install)"
    profiles = cfg["profiles"]
    assert set(profiles) >= {"smoke", "baseline", "normal", "stress", "spike", "soak"}
    # الدخان: صغير بالتصميم — يمر في CI على العدّاد المشترك بلا موارد مدفوعة
    smoke = profiles["smoke"]
    assert smoke["duration_s"] <= 30 and smoke["arrival_rate"] <= 10, \
        "the smoke profile must stay small (CI-safe, Q-023 default)"
    assert smoke.get("requires_dedicated_env") is False
    # الأحمال الكبيرة: محجوزة للبيئة المعزولة — لا تُشغَّل هنا (قرار المالك)
    for name in ("stress", "spike", "soak"):
        assert profiles[name].get("requires_dedicated_env") is True, \
            f"{name} must be flagged for the dedicated environment"
        assert profiles[name].get("blocked_on") == "Q-023"
    # كل ملف تعريف يحمل عتباته الصريحة (SLO معلن قبل التشغيل — شرط R-177 لاحقًا)
    for name, p in profiles.items():
        assert p["thresholds"], f"profile {name} must declare thresholds"
        assert p["duration_s"] > 0 and p["arrival_rate"] > 0


def test_r176_load_script_open_model():
    """مولد الحمل: سيناريو k6 بنموذج مفتوح (constant-arrival-rate — الوصول
    بمعدل مستقل عن زمن الاستجابة) يقرأ المعاملات من الإعداد، لا قيمًا معلقة."""
    assert LOAD_JS.exists(), "benchmarks/r176/load.js is missing"
    js = LOAD_JS.read_text(encoding="utf-8")
    assert "constant-arrival-rate" in js, \
        "the executor must be open-model arrival rate (per the M8 P6 roadmap)"
    for param in ("TARGET_URL", "DURATION", "RATE", "MAX_VUS", "API_KEY"):
        assert param in js, f"load.js must read {param} from the environment"
    # مولد الحمل يخاطب الخدمة عبر HTTP فقط — لا استدعاءات داخلية (فصل المولد عن SUT)
    assert "/api/chat" in js and "/health" in js


def test_r176_k6_pinned_and_verified():
    """الأداة: نسخة k6 المثبتة تطابق النسخة المثبتة في الإعداد — التنزيل
    (عند الحاجة) يتحقق بـSHA256 قبل الاستخدام (تثبيت قابل لإعادة الإنتاج)."""
    fw = _framework()
    k6 = fw.ensure_k6()
    out = subprocess.run([str(k6), "version"], capture_output=True, text=True,
                         timeout=30)
    assert out.returncode == 0
    pinned = json.loads(PROFILES.read_text(encoding="utf-8"))["k6_version"]
    assert pinned in out.stdout, f"k6 on disk is not the pinned {pinned}"


def test_r176_config_sha_stable():
    """القبول يتطلب SHA للإعداد: بصمة الإعداد (profiles + load.js) حتمية
    ومستقرة داخل نفس الشيفرة."""
    fw = _framework()
    sha = fw.config_sha256()
    assert re.fullmatch(r"[0-9a-f]{64}", sha)
    assert fw.config_sha256() == sha, "the config fingerprint must be stable"


# ------------------------------------------------- الحمل الحقيقي (الدخان)


@pytest.fixture(scope="module")
def sut_server():
    """الخدمة الحقيقية (SUT) في عملية منفصلة — المولد يصيبها عبر HTTP فقط
    (فصل ضغط المولد عن ضغط الخدمة: عمليتان OS منفصلتان)."""
    fw = _framework()
    with fw.start_sut() as url:
        yield url


def test_r176_smoke_load_real_and_reported(sut_server):
    """الدخان الحقيقي: تشغيل ملف تعريف الدخان ضد الخدمة الحية — العتبات
    تمر، والتقرير يحمل النتائج الخام + SHA الإعداد + نسخ الأداة (شرط
    القبول: config/version/profile/raw results/SHA)."""
    fw = _framework()
    report = fw.run_load(profile_name="smoke", sut_url=sut_server)
    assert report["ok"] is True, json.dumps(report.get("thresholds"), ensure_ascii=False)
    # العتبات المعلنة فُحصت فعلًا (ليست ناجحة جوفاء)
    assert report["thresholds"]["http_req_failed"] is True
    assert report["thresholds"]["checks"] is True
    # القياس وقع فعلًا: طلبات حقيقية عبر HTTP
    assert report["metrics"]["http_reqs"] >= 50
    assert report["metrics"]["requests_failed"] == 0
    # التقرير الخام قابل للتحقق: SHA الإعداد + نسخة الأداة + مسار النتائج الخام
    assert report["config_sha256"] == fw.config_sha256()
    assert report["k6_version"].startswith("v")
    assert pathlib.Path(report["raw_results_path"]).exists()
    # فصل المولد عن الخدمة موثق في التقرير (عمليتان منفصلتان)
    assert report["generator_sut_separated"] is True


def test_r176_threshold_breach_is_detected(sut_server):
    """سلبي: خرق العتبة يُكتشف ويُفشل القياس صراحة — عتبة مدة مستحيلة
    (p95 < 1µs) يجب أن تُرسب القياس؛ النجاح الجوفاء ممنوع."""
    fw = _framework()
    report = fw.run_load(profile_name="smoke", sut_url=sut_server,
                         extra_thresholds={"http_req_duration": "p(95)<0.000001"})
    assert report["ok"] is False, "an impossible threshold must fail the run"
    assert report["thresholds"]["http_req_duration"] is False
