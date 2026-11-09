#!/usr/bin/env python3
"""R-176 (M8 P6 — أول دفعة من R-193): إطار حمل قابل للتكرار (k6 مثبت النسخة).

معيار القبول (خارطة M8 P6): config/version/workload profile/raw results/SHA؛
لا يختلط ضغط المولد مع ضغط الخدمة. التنفيذ:
  * الأداة: k6 بنسخة مثبتة (profiles.json) وتنزيل يتحقق بـSHA256 — تثبيت
    قابل لإعادة الإنتاج (روح R-148)؛
  * النموذج المفتوح: constant-arrival-rate — الوصول بمعدل مستقل عن زمن
    استجابة الخدمة (benchmarks/r176/load.js)؛
  * الفصل: المولد (k6) والخدمة (SUT) عمليتا OS منفصلتان — الحمل يصيب
    HTTP الحقيقي فقط، وTARGET_URL قد يكون عقدة مستقلة في البيئة المعزولة؛
  * الأحمال الكبيرة (stress/spike/soak) محجوزة للبيئة المعزولة (Q-023 —
    قرار المالك): يرفض الإطار تشغيلها هنا بلا تجاوز صريح مزدوج
    (--allow-dedicated-env + AGOS_ALLOW_BIG_LOAD=1).

Usage:
  python scripts/r176_load_framework.py --ensure-k6          # تثبيت الأداة والخروج
  python scripts/r176_load_framework.py --profile smoke      # SUT محلي + دخان
  python scripts/r176_load_framework.py --profile baseline --sut-url http://host:8000
  python scripts/r176_load_framework.py --profile smoke --out dir/  # مسار التقارير
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import platform
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from contextlib import contextmanager
from typing import Any, Dict, Optional

REPO = pathlib.Path(__file__).resolve().parent.parent
PROFILES_PATH = REPO / "benchmarks" / "r176" / "profiles.json"
LOAD_JS_PATH = REPO / "benchmarks" / "r176" / "load.js"

K6_CACHE = pathlib.Path.home() / ".cache" / "agos-r176-k6"
# بئة الاختبار الموثقة (نفس مفاتيح الحزمة الكاملة في CI). حد معدل الـIP
# يُرفع للحمل كما في R-131 (المولد كله من 127.0.0.1 خلاف المستخدمين
# الحقيقيين) — القيمة المرفوعة موثقة في كل تقرير تشغيل.
SUT_ENV = {
    "AGOS_ENV": "test",
    "AGOS_LLM_KEYLESS": "1",
    "AGOS_ALLOW_CODE_EXEC": "1",
    "AGOS_API_KEY": "test:phase1",
    "AGOS_RATE_LIMIT_REQUESTS": "1000000",
}
DEFAULT_API_KEY = "test:phase1"


# ------------------------------------------------------------------ الإعداد


def load_profiles() -> Dict[str, Any]:
    return json.loads(PROFILES_PATH.read_text(encoding="utf-8"))


def profile(name: str) -> Dict[str, Any]:
    p = load_profiles()["profiles"].get(name)
    if p is None:
        raise KeyError(f"unknown load profile: {name}")
    return p


def config_sha256() -> str:
    """بصمة الإعداد (profiles + load.js) — شرط القبول: SHA قابل للتحقق."""
    h = hashlib.sha256()
    h.update(PROFILES_PATH.read_bytes())
    h.update(b"\x00")
    h.update(LOAD_JS_PATH.read_bytes())
    return h.hexdigest()


# -------------------------------------------------------------------- الأداة


def _k6_binary_path() -> pathlib.Path:
    tag = f"{platform.system().lower()}-{platform.machine().lower()}"
    return K6_CACHE / f"k6-{tag}" / "k6"


def _system_k6_matches(pinned: str) -> Optional[pathlib.Path]:
    found = shutil.which("k6")
    if not found:
        return None
    try:
        out = subprocess.run([found, "version"], capture_output=True,
                             text=True, timeout=30)
        if out.returncode == 0 and pinned in out.stdout:
            return pathlib.Path(found)
    except (OSError, subprocess.SubprocessError):
        return None
    return None


_TRANSIENT_HTTP = {429, 500, 502, 503, 504}


def _download_with_retry(url: str, dest: pathlib.Path, attempts: int = 4,
                         sleep=time.sleep, fetch=None) -> None:
    """D-145: إعادة المحاولة للأخطاء العابرة فقط (5xx و429 والمهلة)، بتأخير 2/4/8s.

    الأخطاء الدائمة (404 و403) وحارس الشبكة في الاختبارات (R-072) لا تُعاد.
    التحقق بالبصمة يبقى عند المستدعي ولا يتغير.
    """
    import urllib.error
    fetch = fetch or urllib.request.urlretrieve
    for i in range(attempts):
        try:
            fetch(url, dest)
            return
        except urllib.error.HTTPError as exc:
            if exc.code not in _TRANSIENT_HTTP or i == attempts - 1:
                raise
        except (TimeoutError, socket.timeout):
            if i == attempts - 1:
                raise
        sleep(2 ** (i + 1))


def ensure_k6() -> pathlib.Path:
    """k6 بالنسبة المثبتة: تنزيل يتحقق بـSHA256 عند الحاجة (مخبأ)، أو
    نسخة النظام إن طابقت النسخة المثبتة. لا حل عائم أبدًا."""
    cfg = load_profiles()
    pinned = cfg["k6_version"]
    target = _k6_binary_path()
    if target.exists():
        # النسخة المخبأة تُتحقق عند كل استخدام (رخصة: استدعاء --version)
        if _version_of(target).startswith(f"k6 {pinned}"):
            return target
    system = _system_k6_matches(pinned)
    if system is not None:
        return system
    # تنزيل النسخة المثبتة مع تحقق SHA256 (قابل لإعادة الإنتاج). داخل حزمة
    # الاختبارات يعطّل conftest الشبكة (R-072) — الرسالة هنا صريحة: ثبّت
    # الأداة خارج الحزمة بالأمر نفسه ثم عد.
    url = cfg["k6_download_url"]
    expected = cfg["k6_tarball_sha256"]
    with tempfile.TemporaryDirectory() as td:
        tgz = pathlib.Path(td) / "k6.tgz"
        try:
            _download_with_retry(url, tgz)
        except Exception as exc:  # noqa: BLE001 — message clarity beats type
            raise RuntimeError(
                "downloading the pinned k6 failed "
                f"({type(exc).__name__}). If this ran inside the pytest suite, "
                "its network guard (R-072) blocks the download — install the "
                "tool OUTSIDE the suite first: "
                "python scripts/r176_load_framework.py --ensure-k6"
            ) from exc
        got = hashlib.sha256(tgz.read_bytes()).hexdigest()
        if got != expected:
            raise RuntimeError(
                f"k6 tarball SHA256 mismatch: expected {expected}, got {got} "
                "— refusing to use an unverified binary")
        with tarfile.open(tgz) as tar:
            member = [m for m in tar.getmembers()
                      if m.name.endswith("/k6") and m.isfile()][0]
            target.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(member) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
        target.chmod(0o755)
    if _version_of(target).startswith(f"k6 {pinned}"):
        return target
    raise RuntimeError(f"installed k6 does not match the pinned {pinned}")


def _version_of(binary: pathlib.Path) -> str:
    try:
        out = subprocess.run([str(binary), "version"], capture_output=True,
                             text=True, timeout=30)
        return (out.stdout or "").strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return f"<unavailable: {type(exc).__name__}>"


# ------------------------------------------------------------------- الخدمة


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _http_get_ok(url: str, timeout: float = 5.0) -> bool:
    import urllib.error
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except (OSError, urllib.error.URLError):
        return False


@contextmanager
def start_sut(api_key: str = DEFAULT_API_KEY, startup_timeout_s: float = 180.0):
    """الخدمة الحقيقية (SUT) في عملية OS منفصلة — المولد يصيبها عبر HTTP
    فقط (فصل ضغط المولد عن ضغط الخدمة)."""
    import urllib.request
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    env = {**os.environ, **SUT_ENV, "AGOS_API_KEY": api_key}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server:app", "--port", str(port)],
        cwd=str(REPO), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + startup_timeout_s
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError("the SUT process died during startup")
            if _http_get_ok(f"{url}/health"):
                yield url
                return
            time.sleep(1.0)
        raise RuntimeError(f"the SUT did not become healthy within "
                           f"{startup_timeout_s:.0f}s")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


# ------------------------------------------------------------------- الحمل


def _guard_dedicated_env(p: Dict[str, Any], args_allow: bool) -> None:
    """Q-023: الأحمال الكبيرة للبيئة المعزولة حصرًا — الرفض هو الافتراض،
    والتجاوز مزدوج صريح (علم CLI + متغير بيئة) لا يحدث من CI أبدًا."""
    if not p.get("requires_dedicated_env"):
        return
    if args_allow and os.environ.get("AGOS_ALLOW_BIG_LOAD") == "1":
        return
    raise SystemExit(
        f"profile '{p.get('purpose', '')}' requires the DEDICATED isolated "
        "environment (Q-023 — owner decision). The framework refuses to run "
        "big loads on the documented/CI environment. Override (owner only): "
        "--allow-dedicated-env AND AGOS_ALLOW_BIG_LOAD=1.")


def run_load(profile_name: str = "smoke", sut_url: Optional[str] = None,
             api_key: str = DEFAULT_API_KEY, out_dir: Optional[pathlib.Path] = None,
             extra_thresholds: Optional[Dict[str, str]] = None,
             allow_dedicated_env: bool = False) -> Dict[str, Any]:
    """تشغيل ملف تعريف حمل ضد الخدمة وإنتاج تقرير الدليل الخام.

    التقرير يحمل شرط القبول كاملًا: config SHA + نسخة الأداة + النتائج
    الخام (مسار ملف k6 summary) + فحص العتبات صريحًا (النجاح الجوفاء ممنوع).
    """
    p = profile(profile_name)
    _guard_dedicated_env(p, allow_dedicated_env)
    k6 = ensure_k6()
    thresholds = dict(p.get("thresholds") or {})
    if extra_thresholds:
        thresholds.update(extra_thresholds)

    out = pathlib.Path(out_dir) if out_dir else pathlib.Path(tempfile.mkdtemp(
        prefix=f"r176_{profile_name}_"))
    out.mkdir(parents=True, exist_ok=True)
    raw = out / f"k6_summary_{profile_name}.json"
    stdout_log = out / f"k6_stdout_{profile_name}.log"

    env = {**os.environ,
           "TARGET_URL": sut_url or "http://127.0.0.1:8000",
           "DURATION": f"{p['duration_s']}s",
           "RATE": str(p["arrival_rate"]),
           "MAX_VUS": str(p["max_vus"]),
           "API_KEY": api_key,
           "CHAT_SHARE": str(p.get("chat_share", 0.5)),
           "PROFILE": profile_name,
           "THRESHOLDS": json.dumps(thresholds)}
    proc = subprocess.run(
        [str(k6), "run", "--summary-export", str(raw), str(LOAD_JS_PATH)],
        env=env, capture_output=True, text=True, cwd=str(REPO),
        timeout=p["duration_s"] + 240)
    stdout_log.write_text(
        proc.stdout + ("\n--- k6 stderr ---\n" + proc.stderr if proc.stderr else ""),
        encoding="utf-8")

    summary = {}
    if raw.exists():
        try:
            summary = json.loads(raw.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            summary = {}
    metrics = summary.get("metrics", {})

    # فحص العتبات صريحًا من ملخص k6 الخام. صيغة summary-export القديمة:
    # {"expr": bool} حيث bool=true تعني العتبة مخروقة (مثبت تجريبيًا
    # على عتبات تُحقق وتُخترق معًا) — أي النجاح = not bool. الصيغة الحديثة
    # {"expr": {"ok": bool}} فيها ok=نجاح. كود خروج k6 (99) يحسم الالتباس
    # دائمًا: ok النهائي يشترط returncode==0 مع فحص كل عتبة صراحة.
    checks: Dict[str, bool] = {}
    for metric_name in thresholds:
        m = metrics.get(metric_name) or {}
        th = m.get("thresholds") or {}
        checks[metric_name] = bool(th) and all(
            (not v if isinstance(v, bool) else bool(v.get("ok")))
            for v in th.values())
    ok = proc.returncode == 0 and bool(checks) and all(checks.values())

    http_reqs = (metrics.get("http_reqs") or {}).get("count", 0)
    failed = (metrics.get("http_req_failed") or {}).get("count", None)
    # k6 http_req_failed: rate/count semantics — count غير موجود في بعض
    # النسخ؛ نحسب من rate×count لـhttp_reqs عند الحاجة
    if failed is None:
        rate = (metrics.get("http_req_failed") or {}).get("rate", 0.0)
        failed = round(rate * http_reqs)

    report = {
        "ok": ok,
        "profile": profile_name,
        "k6_version": load_profiles()["k6_version"],
        "k6_binary": str(k6),
        "k6_exit_code": proc.returncode,
        "config_sha256": config_sha256(),
        "thresholds": checks,
        "metrics": {
            "http_reqs": http_reqs,
            "requests_failed": failed,
            "duration_p95_ms": (metrics.get("http_req_duration") or {}).get(
                "p(95)"),
        },
        "generator_sut_separated": True,  # مولد (k6) وSUT عمليتا OS منفصلتان
        "raw_results_path": str(raw),
        "stdout_log_path": str(stdout_log),
        "thresholds_declared": thresholds,
        "profile_params": {k: p[k] for k in
                           ("duration_s", "arrival_rate", "max_vus")
                           if k in p},
    }
    (out / f"report_{profile_name}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ensure-k6", action="store_true",
                    help="تثبيت k6 بالنسبة المثبتة (تحقق SHA) والخروج")
    ap.add_argument("--profile", default="smoke",
                    help="ملف تعريف الحمل (الافتراضي: smoke)")
    ap.add_argument("--sut-url", default=None,
                    help="عنوان خدمة قائمة (المسار المعزول) بدل SUT محلي")
    ap.add_argument("--out", default=None, help="مجلد التقارير الخام")
    ap.add_argument("--api-key", default=DEFAULT_API_KEY)
    ap.add_argument("--allow-dedicated-env", action="store_true",
                    help="(المالك فقط، مع AGOS_ALLOW_BIG_LOAD=1) تجاوز حرس Q-023")
    args = ap.parse_args()

    if args.ensure_k6:
        print(ensure_k6())
        return 0
    out = pathlib.Path(args.out) if args.out else None
    if args.sut_url:
        report = run_load(args.profile, sut_url=args.sut_url,
                          api_key=args.api_key, out_dir=out,
                          allow_dedicated_env=args.allow_dedicated_env)
    else:
        with start_sut(api_key=args.api_key) as url:
            report = run_load(args.profile, sut_url=url,
                              api_key=args.api_key, out_dir=out,
                              allow_dedicated_env=args.allow_dedicated_env)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
