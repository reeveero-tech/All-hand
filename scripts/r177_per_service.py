#!/usr/bin/env python3
"""R-177 (M8 P6 — ثاني دفعة من R-193، D-133): عدّاء خط أساس لكل خدمة.

الاستعمال:
  python scripts/r177_per_service.py --ensure-k6            # تثبيت الأداة والخروج (R-176)
  python scripts/r177_per_service.py --service api-health    # خدمة واحدة
  python scripts/r177_per_service.py --all                   # كل الخدمات المقيسة (SUT واحد)
  python scripts/r177_per_service.py --service api-chat --sut-url http://host:port

جرد الخدمات والعتبات المسودة في benchmarks/r177/services.json — العتبات
مسودة صريحة (slo_status=draft): الخارطة تشترط أن يحدد مالك الاختبار SLO
وstop limits قبل التشغيل؛ لا حمل stress/spike/soak هنا البتة. الأحمال
الكبيرة لكل خدمة تُبنى بعد قرار المالك (Q-023 للبيئة المعزولة).

فصل المولد عن الخدمة (روح R-176): k6 في عملية OS منفصلة يخاطب SUT عبر
HTTP فقط؛ التقرير يحمل config-SHA + النسخة + النتائج الخام + فحص العتبات
صراحة (النجاح الجوفاء ممنوع — دلالة summary-export القديمة موثقة في R-176).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import pathlib
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, Optional

REPO = pathlib.Path(__file__).resolve().parent.parent
SERVICES_JSON = REPO / "benchmarks/r177/services.json"
SERVICE_JS = REPO / "benchmarks/r177/service_load.js"
R176 = REPO / "scripts/r176_load_framework.py"

DEFAULT_API_KEY = "test:phase1"


# ------------------------------------------------- إعادة استخدام إطار R-176

def _r176():
    import importlib.util
    spec = importlib.util.spec_from_file_location("r176", R176)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_R176 = None


def r176():
    global _R176
    if _R176 is None:
        _R176 = _r176()
    return _R176


def ensure_k6() -> pathlib.Path:
    return r176().ensure_k6()


def start_sut(api_key: str = DEFAULT_API_KEY):
    return r176().start_sut(api_key=api_key)


# ----------------------------------------------------------------- الإعداد

def load_services() -> Dict[str, Any]:
    return json.loads(SERVICES_JSON.read_text(encoding="utf-8"))


def config_sha256() -> str:
    """بصمة إعداد R-177 (services.json + service_load.js) — حتمية."""
    h = hashlib.sha256()
    h.update(SERVICES_JSON.read_bytes())
    h.update(SERVICE_JS.read_bytes())
    return h.hexdigest()


def _guard_dedicated_env(svc: Dict[str, Any], allow: bool) -> None:
    """Q-023: رفض أي خدمة تتطلب البيئة المعزولة بلا تجاوز مزدوج صريح."""
    if not svc.get("requires_dedicated_env"):
        return
    if allow and os.environ.get("AGOS_ALLOW_BIG_LOAD") == "1":
        return
    raise SystemExit(
        f"service entry requires the DEDICATED isolated environment "
        "(Q-023 — owner decision). Refusing to run on the documented/CI "
        "environment. Override (owner only): --allow-dedicated-env AND "
        "AGOS_ALLOW_BIG_LOAD=1.")


def _sse_ttfb_probe(sut_url: str, api_key: str, probes: int = 4) -> Dict[str, Any]:
    """زمن اتصال تيار SSE حتى الرؤوس (TTFB) — مسبار منفصل عن المولد.

    k6 ينتظر جسم الرد كاملًا (مثبت تجريبيًا في السجل 366: تيار حتى
    timeout) فلا يصلح لقياس الاتصال فقط؛ المسبار هنا عملية/اتصال مستقلان
    عن SUT (HTTP فقط، نفس فصل R-176) يغلق بعد الرؤوس مباشرة — عينات
    قليلة بالتصميم لاحترام قيود قبول SSE (max_per_client=5).
    """
    import http.client
    from urllib.parse import urlparse
    u = urlparse(sut_url)
    samples: list = []
    statuses: list = []
    for _ in range(probes):
        conn = http.client.HTTPConnection(u.hostname, u.port, timeout=10)
        try:
            t0 = time.perf_counter()
            conn.request("GET", "/api/events/stream", headers={
                "X-API-Key": api_key, "Accept": "text/event-stream"})
            resp = conn.getresponse()  # يكتمل عند وصول الرؤوس
            ttfb_ms = (time.perf_counter() - t0) * 1000.0
            statuses.append(resp.status)
            resp.close()
            samples.append(round(ttfb_ms, 3))
        finally:
            conn.close()
        time.sleep(0.5)
    p95 = sorted(samples)[max(0, math.ceil(0.95 * len(samples)) - 1)] if samples else None
    return {"samples_ms": samples, "statuses": statuses,
            "p95_ms": p95, "note": "connect-to-headers only; few samples by design (SSE admission limits)"}


# ------------------------------------------------------------------- التشغيل

def run_service_baseline(service_name: str, sut_url: Optional[str] = None,
                         api_key: str = DEFAULT_API_KEY,
                         out_dir: Optional[pathlib.Path] = None,
                         extra_thresholds: Optional[Dict[str, str]] = None,
                         allow_dedicated_env: bool = False) -> Dict[str, Any]:
    """قياس خط أساس خدمة واحدة ضد SUT وإنتاج تقرير الدليل الخام.

    التصنيف صريح (قاعدة قبول R-177): measured / skip — لا شيء يمر صامتًا.
    """
    data = load_services()
    if service_name in data.get("skipped", {}):
        entry = data["skipped"][service_name]
        return {"service": service_name, "classification": "skip",
                "ok": False, "reason": entry["reason"],
                "config_sha256": config_sha256()}
    if service_name not in data["services"]:
        raise SystemExit(
            f"unknown service '{service_name}' — see benchmarks/r177/services.json")
    svc = data["services"][service_name]
    _guard_dedicated_env(svc, allow_dedicated_env)
    if not sut_url:
        raise SystemExit(
            "a measured service needs --sut-url (or run via --all which "
            "starts a local SUT in a separate OS process)")

    k6 = ensure_k6()
    b = svc["baseline"]
    thresholds = dict(b["thresholds"])
    if extra_thresholds:
        thresholds.update(extra_thresholds)

    out = pathlib.Path(out_dir) if out_dir else pathlib.Path(tempfile.mkdtemp(
        prefix=f"r177_{service_name}_"))
    out.mkdir(parents=True, exist_ok=True)
    raw = out / f"k6_summary_{service_name}.json"
    stdout_log = out / f"k6_stdout_{service_name}.log"

    env = {**os.environ,
           "TARGET_URL": sut_url,
           "DURATION": f"{b['duration_s']}s",
           "RATE": str(b["arrival_rate"]),
           "MAX_VUS": str(b["max_vus"]),
           "API_KEY": api_key,
           "SERVICE": service_name,
           "PROFILE": "baseline",
           "THRESHOLDS": json.dumps(thresholds)}
    proc = subprocess.run(
        [str(k6), "run", "--summary-export", str(raw), str(SERVICE_JS)],
        env=env, capture_output=True, text=True, cwd=str(REPO),
        timeout=b["duration_s"] + 240)
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

    # فحص العتبات صراحة (دلالة summary-export القديمة موثقة في R-176:
    # {"expr": bool} حيث true = مخروقة؛ والصيغة الحديثة ok=نجاح)
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
    if failed is None:
        rate = (metrics.get("http_req_failed") or {}).get("rate", 0.0)
        failed = round(rate * http_reqs)
    dur = metrics.get("http_req_duration") or {}

    report = {
        "service": service_name,
        "classification": "measured",
        "ok": ok,
        "profile": "baseline",
        "k6_version": load_services()["k6_version"],
        "k6_exit_code": proc.returncode,
        "config_sha256": config_sha256(),
        "thresholds": checks,
        "thresholds_declared": thresholds,
        "slo_status": svc.get("slo_status"),
        "metrics": {
            "http_reqs": http_reqs,
            "requests_failed": failed,
            "duration_avg_ms": dur.get("avg"),
            "duration_med_ms": dur.get("med"),
            "duration_p90_ms": dur.get("p(90)"),
            "duration_p95_ms": dur.get("p(95)"),
            "duration_max_ms": dur.get("max"),
        },
        "baseline_params": {k: b[k] for k in
                           ("duration_s", "arrival_rate", "max_vus")},
        "generator_sut_separated": True,
        "raw_results_path": str(raw),
        "stdout_log_path": str(stdout_log),
    }
    if service_name == "api-sse":
        # زمن اتصال التيار حتى الرؤوس — مسبار منفصل (k6 ينتظر الجسم كاملًا)
        report["sse_stream_ttfb"] = _sse_ttfb_probe(sut_url, api_key)
        report["ok"] = ok and all(
            s == 200 for s in report["sse_stream_ttfb"]["statuses"])
    (out / f"report_{service_name}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _run_each(names, sut_url, api_key, out):
    reports: Dict[str, Any] = {}
    for name in names:
        t0 = time.time()
        reports[name] = run_service_baseline(
            name, sut_url=sut_url, api_key=api_key, out_dir=out)
        m = reports[name].get("metrics") or {}
        print(f"[{name}] ok={reports[name]['ok']} "
              f"p95={m.get('duration_p95_ms')}ms ({time.time() - t0:.0f}s)",
              flush=True)
    return reports


def run_all_baselines(sut_url: Optional[str] = None,
                      api_key: str = DEFAULT_API_KEY,
                      out_dir: Optional[pathlib.Path] = None) -> Dict[str, Any]:
    """كل الخدمات المقيسة — SUT واحد، حمل تلو الآخر، ملخص مصنف."""
    data = load_services()
    names = [n for n, s in data["services"].items() if s.get("measured")]
    out = pathlib.Path(out_dir) if out_dir else pathlib.Path(
        tempfile.mkdtemp(prefix="r177_all_"))
    out.mkdir(parents=True, exist_ok=True)

    reports: Dict[str, Any] = {}
    if sut_url is not None:
        reports = _run_each(names, sut_url, api_key, out)
    else:
        with start_sut(api_key) as url:  # SUT محلي في عملية منفصلة
            reports = _run_each(names, url, api_key, out)

    summary = {
        "config_sha256": config_sha256(),
        "services": reports,
        "counts": {
            "measured": sum(1 for r in reports.values()
                            if r.get("classification") == "measured"),
            "failed": sum(1 for r in reports.values() if not r.get("ok")),
        },
        "skipped": {n: e["reason"] for n, e in
                    data.get("skipped", {}).items()},
    }
    (out / "summary_all.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"counts": summary["counts"],
                     "summary_path": str(out / "summary_all.json")},
                     ensure_ascii=False))
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ensure-k6", action="store_true",
                    help="تثبيت k6 المثبت (R-176) والخروج")
    ap.add_argument("--service", default=None,
                    help="قياس خدمة واحدة (انظر benchmarks/r177/services.json)")
    ap.add_argument("--all", action="store_true",
                    help="كل الخدمات المقيسة على SUT واحد")
    ap.add_argument("--sut-url", default=None,
                    help="SUT قائم (الافتراضي: تشغيل SUT محلي منفصل)")
    ap.add_argument("--out", default=None, help="مجلد التقارير الخام")
    ap.add_argument("--api-key", default=DEFAULT_API_KEY)
    ap.add_argument("--allow-dedicated-env", action="store_true",
                    help="تجاوز حرس Q-023 (المالك فقط — مع AGOS_ALLOW_BIG_LOAD=1)")
    args = ap.parse_args()

    if args.ensure_k6:
        k6 = ensure_k6()
        print(f"k6 ready: {k6}")
        return 0
    if args.all:
        out = pathlib.Path(args.out) if args.out else None
        summary = run_all_baselines(sut_url=args.sut_url,
                                    api_key=args.api_key, out_dir=out)
        return 0 if summary["counts"]["failed"] == 0 else 1
    if args.service:
        out = pathlib.Path(args.out) if args.out else None
        if args.sut_url:
            rep = run_service_baseline(
                args.service, sut_url=args.sut_url, api_key=args.api_key,
                out_dir=out, allow_dedicated_env=args.allow_dedicated_env)
        else:
            with start_sut(args.api_key) as url:  # SUT محلي في عملية منفصلة
                rep = run_service_baseline(
                    args.service, sut_url=url, api_key=args.api_key,
                    out_dir=out, allow_dedicated_env=args.allow_dedicated_env)
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0 if rep.get("ok") else 1
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
