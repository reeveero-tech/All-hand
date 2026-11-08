#!/usr/bin/env python3
"""R-177 (M8 P6 — R-193، D-133 + D-134): عدّاء لكل خدمة بعتبات معتمدة وحدود إيقاف حية.

الاستعمال:
  python scripts/r177_per_service.py --ensure-k6              # تثبيت الأداة والخروج (R-176)
  python scripts/r177_per_service.py --service api-health      # خدمة واحدة (baseline)
  python scripts/r177_per_service.py --all --profile baseline  # كل الخدمات المقيسة (SUT واحد)
  python scripts/r177_per_service.py --all --profile normal
  python scripts/r177_per_service.py --failure-recovery crash  # تعطل SUT أثناء الحمل ثم التعافي
  python scripts/r177_per_service.py --failure-recovery storage-outage --storage live-local
  python scripts/r177_per_service.py --all --storage live-local   # تخزين PostgreSQL+PostgREST محلي

الدفعة 3 (D-134 — قرار المالك 2026-11-07):
  * العتبات **معتمدة** (slo_status=approved): p95 لكل خدمة كما اقتُرحت دون
    تغيير، http_req_failed < 1%، checks > 99%، وp99 مستقل للدردشة 1000ms
    بشرط كفاية العينات (n >= 299 لحد أعلى توزيعي-حر بثقة 95%).
  * حدود الإيقاف (scripts/r177_stop_limits.py) تُقيَّم **أثناء** الحمل من
    مخرجات k6 JSON الحية + مسابير الصحة/الاستعداد/عزل المستأجرين/سلامة
    البيانات؛ عند الخرق يُقطع k6 ويُكتب سبب الإيقاف والقياسات المسببة،
    والتشغيل الموقوف لا يكون ok أبدًا.
  * العتبات لا تُرخى: extra_thresholds تُضاف (اتحاد) ولا تستبدل، وأي تعبير
    أضعف من المعتمد يُرفض؛ والإعداد المتلاعب به (عتبة لا تطابق SLO) يُرفض.
  * الواجهة تُقاس فعليًا (web/dist مبني ⇒ 200 فقط)؛ بلا بناء التصنيف
    not_measured — الرد 404 ليس قياس واجهة.
  * الأحمال الكبيرة (stress/spike/soak) حرس Q-023 كما في D-133.

فصل المولد عن الخدمة (روح R-176): k6 في عملية OS منفصلة يخاطب SUT عبر HTTP
فقط؛ المسابير عميل HTTP ثالث خفيف (معلن في التقرير، خارج مقاييس k6).
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
import math
import os
import pathlib
import re
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

REPO = pathlib.Path(__file__).resolve().parent.parent
SERVICES_JSON = REPO / "benchmarks/r177/services.json"
SERVICE_JS = REPO / "benchmarks/r177/service_load.js"
R176 = REPO / "scripts/r176_load_framework.py"
STOP_LIMITS = REPO / "scripts/r177_stop_limits.py"
WEB_DIST = REPO / "web" / "dist"

DEFAULT_API_KEY = "test:phase1"
PUBLIC_STATUSES = ("queued", "running", "completed", "failed")
TERMINAL = ("completed", "failed")


# ------------------------------------------------- إعادة استخدام إطار R-176

def _load_module(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, mod)
    spec.loader.exec_module(mod)
    return mod


_R176 = None
_SL = None


def r176():
    global _R176
    if _R176 is None:
        _R176 = _load_module("r176", R176)
    return _R176


def sl():
    """محرك حدود الإيقاف (scripts/r177_stop_limits.py)."""
    global _SL
    if _SL is None:
        _SL = _load_module("r177_stop_limits", STOP_LIMITS)
    return _SL


def ensure_k6() -> pathlib.Path:
    return r176().ensure_k6()


def start_sut(api_key: str = DEFAULT_API_KEY):
    """توافق الدفعة 2: SUT محلي (مستأجرا المسابير مُهيآن)."""
    return _sut_context(api_key)


# ----------------------------------------------------------------- الإعداد

def load_services() -> Dict[str, Any]:
    return json.loads(SERVICES_JSON.read_text(encoding="utf-8"))


def config_sha256() -> str:
    """بصمة إعداد R-177 (services.json + service_load.js + محرك الإيقاف) — حتمية."""
    h = hashlib.sha256()
    h.update(SERVICES_JSON.read_bytes())
    h.update(SERVICE_JS.read_bytes())
    h.update(STOP_LIMITS.read_bytes())
    return h.hexdigest()


def _guard_dedicated_env(svc: Dict[str, Any], allow: bool) -> None:
    """Q-023: رفض أي خدمة/ملمح يتطلب البيئة المعزولة بلا تجاوز مزدوج صريح."""
    if not svc.get("requires_dedicated_env"):
        return
    if allow and os.environ.get("AGOS_ALLOW_BIG_LOAD") == "1":
        return
    raise SystemExit(
        "service entry requires the DEDICATED isolated environment "
        "(Q-023 — owner decision). Refusing to run on the documented/CI "
        "environment. Override (owner only): --allow-dedicated-env AND "
        "AGOS_ALLOW_BIG_LOAD=1.")


# ---------------------------------------------------- العتبات المعتمدة

_PQ = re.compile(r"^p\((\d+(?:\.\d+)?)\)<(\d+(?:\.\d+)?)$")
_RATE = re.compile(r"^rate([<>])(\d+(?:\.\d+)?)$")


def _as_list(expr) -> List[str]:
    return list(expr) if isinstance(expr, (list, tuple)) else [expr]


def approved_thresholds(svc: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, List[str]]:
    """العتبات المعتمدة من قرار المالك — مصدرها SLO الخدمة وسياسة الأخطاء."""
    pol = data["error_policy"]
    dur = [f"p(95)<{_num(svc['slo_p95_ms'])}"]
    if svc.get("slo_p99_ms") is not None:
        dur.append(f"p(99)<{_num(svc['slo_p99_ms'])}")
    return {"http_req_failed": [pol["http_req_failed"]],
            "checks": [pol["checks"]],
            "http_req_duration": dur}


def _num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else str(v)


def verify_declared_thresholds(name: str, svc: Dict[str, Any], data: Dict[str, Any]) -> None:
    """حرس التلاعب: العتبات المكتوبة في baseline يجب أن تطابق المعتمد حرفيًا."""
    if svc.get("slo_status") != "approved" or data.get("slo_status") != "approved":
        raise SystemExit(f"{name}: SLO not approved — no load runs on draft thresholds")
    declared = {k: sorted(_as_list(v)) for k, v in svc["baseline"]["thresholds"].items()}
    expected = {k: sorted(v) for k, v in approved_thresholds(svc, data).items()}
    if declared != expected:
        raise SystemExit(f"{name}: declared thresholds {declared} != approved {expected} "
                         "(services.json tampered or out of sync with the owner decision)")


def merge_tighten_only(base: Dict[str, List[str]],
                       extra: Optional[Dict[str, Any]]) -> Dict[str, List[str]]:
    """extra_thresholds: إضافة فقط (اتحاد)، وكل تعبير أضعف من المعتمد يُرفض."""
    out = {k: list(v) for k, v in base.items()}
    if not extra:
        return out
    for metric, exprs in extra.items():
        for e in _as_list(exprs):
            e = str(e).replace(" ", "")
            if metric == "http_req_duration":
                m = _PQ.match(e)
                if not m:
                    raise ValueError(f"unsupported duration threshold {e!r}")
                q, v = float(m.group(1)), float(m.group(2))
                for b in out.get(metric, []):
                    mb = _PQ.match(b)
                    if mb and float(mb.group(1)) == q and v > float(mb.group(2)):
                        raise ValueError(f"loosening refused: {e} is weaker than approved {b}")
            elif metric in ("http_req_failed", "checks"):
                m = _RATE.match(e)
                if not m:
                    raise ValueError(f"unsupported rate threshold {e!r}")
                op, v = m.group(1), float(m.group(2))
                for b in out.get(metric, []):
                    mb = _RATE.match(b)
                    if not mb:
                        continue
                    bv = float(mb.group(2))
                    if (op == "<" and (mb.group(1) != "<" or v > bv)) or \
                       (op == ">" and (mb.group(1) != ">" or v < bv)):
                        raise ValueError(f"loosening refused: {metric} {e} vs approved {b}")
            else:
                raise ValueError(f"unknown threshold metric {metric!r}")
            if e not in out.setdefault(metric, []):
                out[metric].append(e)
    return out


def stop_policy_for(svc: Dict[str, Any], data: Dict[str, Any],
                    arrival_rate: Optional[float] = None):
    return sl().validate_policy(data.get("stop_limits") or {}, svc.get("slo_p95_ms"),
                                svc.get("slo_p99_ms"), expected_iter_rate=arrival_rate)


def profile_params(svc: Dict[str, Any], data: Dict[str, Any], profile: str,
                   allow_dedicated_env: bool) -> Dict[str, Any]:
    if profile in ("baseline", "normal"):
        p = svc.get(profile)
        if not p:
            raise SystemExit(f"profile {profile} not defined for this service")
        return {k: p[k] for k in ("duration_s", "arrival_rate", "max_vus")}
    heavy = (data.get("heavy_profiles") or {}).get(profile)
    if not heavy:
        raise SystemExit(f"unknown profile {profile}")
    _guard_dedicated_env(heavy, allow_dedicated_env)
    n = svc["normal"]
    return {"duration_s": heavy["duration_s"],
            "arrival_rate": round(n["arrival_rate"] * heavy["rate_multiplier_of_normal"], 3),
            "max_vus": int(n["max_vus"] * heavy["rate_multiplier_of_normal"])}


# --------------------------------------------------------------- HTTP مساعد

def _http(url: str, method: str = "GET", key: Optional[str] = None,
          body: Optional[dict] = None, timeout: float = 3.0) -> Tuple[Optional[int], Any]:
    """(status, json|None) — status=None عند خطأ اتصال (لا استثناء)."""
    headers = {"Content-Type": "application/json"}
    if key:
        headers["X-API-Key"] = key
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, method=method, headers=headers, data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            status = r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read(), e.code
    except (OSError, urllib.error.URLError) as e:
        return None, f"{type(e).__name__}"
    try:
        return status, json.loads(raw) if raw else None
    except (ValueError, UnicodeDecodeError):
        return status, None


# ---------------------------------------------------------------- SUT

class Tenants:
    """مستأجران مستخدمان لمسابير العزل — مفاتيح عشوائية لكل جلسة (لا أسرار ثابتة)."""

    def __init__(self):
        self.a_key = "r177a-" + secrets.token_hex(16)
        self.b_key = "r177b-" + secrets.token_hex(16)

    def env(self) -> Dict[str, str]:
        return {"AGOS_TENANT_KEYS": json.dumps([
            {"tenant": "r177-a", "key_sha256": hashlib.sha256(self.a_key.encode()).hexdigest(),
             "role": "user"},
            {"tenant": "r177-b", "key_sha256": hashlib.sha256(self.b_key.encode()).hexdigest(),
             "role": "user"}])}


def storage_env(mode: str) -> Dict[str, str]:
    """volatile: بلا تخزين (المسار الذاكري). live-local: PostgreSQL+PostgREST محلي فقط."""
    if mode == "volatile":
        return {"SUPABASE_URL": "", "SUPABASE_SERVICE_KEY": "", "SUPABASE_ANON_KEY": "",
                "SUPABASE_KEY": ""}
    if mode == "live-local":
        url = os.environ.get("R177_STORAGE_URL", "")
        key = os.environ.get("R177_STORAGE_KEY", "")
        host = urlparse(url).hostname or ""
        if host not in ("127.0.0.1", "localhost") or not key:
            raise SystemExit("live-local storage needs R177_STORAGE_URL on loopback + "
                             "R177_STORAGE_KEY (the throwaway local stack — never hosted "
                             "Supabase from this runner)")
        return {"SUPABASE_URL": url, "SUPABASE_SERVICE_KEY": key}
    raise SystemExit(f"unknown storage mode {mode}")


class SutProcess:
    """SUT حقيقي في عملية OS منفصلة، قابل للقتل وإعادة التشغيل على المنفذ نفسه."""

    def __init__(self, api_key: str = DEFAULT_API_KEY, tenants: Optional[Tenants] = None,
                 storage: str = "volatile", startup_timeout_s: float = 180.0):
        self.api_key = api_key
        self.tenants = tenants or Tenants()
        self.storage = storage
        self.port = r176()._free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.timeout = startup_timeout_s
        self.proc: Optional[subprocess.Popen] = None
        self.env = {**os.environ, **r176().SUT_ENV, "AGOS_API_KEY": api_key,
                    **self.tenants.env(), **storage_env(storage)}

    def start(self) -> float:
        t0 = time.monotonic()
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "server:app", "--port", str(self.port)],
            cwd=str(REPO), env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = t0 + self.timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("the SUT process died during startup")
            if _http(self.url + "/health", timeout=2)[0] == 200:
                return time.monotonic() - t0
            time.sleep(0.2)
        raise RuntimeError("the SUT did not become healthy in time")

    def wait_status(self, path: str, want: int = 200, timeout: float = 120.0) -> Optional[float]:
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            if _http(self.url + path, timeout=2)[0] == want:
                return round(time.monotonic() - t0, 3)
            time.sleep(0.1)
        return None

    def kill(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGKILL)
            self.proc.wait(timeout=10)

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()


@contextlib.contextmanager
def _sut_context(api_key: str = DEFAULT_API_KEY, storage: str = "volatile"):
    sut = SutProcess(api_key, storage=storage)
    try:
        sut.start()
        yield sut.url
    finally:
        sut.stop()


@contextlib.contextmanager
def sut_process(api_key: str = DEFAULT_API_KEY, storage: str = "volatile"):
    sut = SutProcess(api_key, storage=storage)
    try:
        sut.start()
        yield sut
    finally:
        sut.stop()


# ----------------------------------------------------------- المسابير (S5)

TRACK_PROMPT = 'validate this JSON: {"ok": true, "probe": "r177"}'


def volatile_task_retention() -> int:
    """حد الاحتفاظ المتطاير كما في server.py (يُقرأ من المصدر — لا رقم منسوخ)."""
    m = re.search(r"^MAX_TASKS_KEPT\s*=\s*(\d+)", (REPO / "server.py").read_text(
        encoding="utf-8"), re.M)
    if not m:
        raise SystemExit("MAX_TASKS_KEPT not found in server.py")
    return int(m.group(1))


def _fingerprint(task: Dict[str, Any]) -> str:
    keep = {k: task.get(k) for k in ("id", "specialization", "prompt", "status", "result")}
    return hashlib.sha256(json.dumps(keep, sort_keys=True, default=str).encode()).hexdigest()


class Probes:
    """صحة/استعداد/عزل/سلامة — كل مسبار يعيد (ok, detail). خطأ الاتصال ليس
    «عزلًا منتهكًا» ولا «سلامة» — يلتقطه مسبار الصحة (لا اتهام زائف)."""

    def __init__(self, url: str, tenants: Optional[Tenants],
                 volatile_retention: Optional[int] = None):
        self.url, self.t = url, tenants
        self.sentinel: Optional[str] = None
        self.terminal_fp: Dict[str, str] = {}
        self.counts = {"health": 0, "ready": 0, "isolation": 0, "integrity": 0}
        # عقد الاحتفاظ المتطاير الموثق (server.py MAX_TASKS_KEPT، R-164): في
        # الوضع المتطاير فقط تُحذف السجلات **المنتهية** الأقدم حين يتجاوز العدد
        # الحد. حذف مهمة رُصدت منتهية = احتفاظ موثق يُسجَّل في التقرير (لا يُخفى)،
        # لا فقدان. أي 404 لمهمة لم تُرصد منتهية، أو أي 404 على تخزين دائم
        # (volatile_retention=None — الافتراض الصارم) = انتهاك سلامة فوري.
        self.volatile_retention = volatile_retention
        self.retention_evictions: List[Dict[str, Any]] = []
        self._evicted: set = set()

    def setup_sentinel(self) -> Optional[str]:
        if not self.t:
            return None
        st, body = _http(self.url + "/api/tasks", "POST", self.t.a_key,
                         {"specialization": "validator", "prompt": TRACK_PROMPT}, timeout=10)
        if st == 202 and isinstance(body, dict):
            self.sentinel = body.get("task_id")
        return self.sentinel

    def health(self):
        self.counts["health"] += 1
        st, _ = _http(self.url + "/health", timeout=2)
        return st == 200, {"status": st}

    def ready(self):
        self.counts["ready"] += 1
        st, body = _http(self.url + "/health/ready", timeout=2)
        return st == 200, {"status": st,
                           "reason": body.get("reason") if isinstance(body, dict) else None}

    def isolation(self, ids: List[str]):
        """مستأجر B لا يرى مهام A: GET ⇒ 404 والقائمة لا تحتويها."""
        self.counts["isolation"] += 1
        if not self.t or not ids:
            return True, {"skipped": "no sentinel"}
        for tid in ids:
            st, body = _http(f"{self.url}/api/tasks/{tid}", key=self.t.b_key)
            if st == 200:
                return False, {"task": tid, "leak": "tenant B read tenant A task (200)"}
        st, body = _http(self.url + "/api/tasks?limit=200", key=self.t.b_key)
        if st == 200 and isinstance(body, dict):
            seen = {x.get("id") or x.get("task_id") for x in body.get("tasks") or []}
            leaked = sorted(seen & set(ids))
            if leaked:
                return False, {"leak": "tenant B list contains tenant A tasks", "tasks": leaked}
        return True, {"checked": len(ids)}

    def integrity(self, ids: List[str]):
        """مهام A موجودة، بحقولها، وبمفردات الحالة العامة، والنهائية لا ترتد ولا تتبدل."""
        self.counts["integrity"] += 1
        if not self.t:
            return True, {"skipped": "no tenants"}
        for tid in ids:
            if tid in self._evicted:
                continue
            st, body = _http(f"{self.url}/api/tasks/{tid}", key=self.t.a_key)
            if st is None or (st is not None and st >= 500):
                continue  # تعذر الاتصال/خطأ خادم: الصحة/الأخطاء تحكمه لا السلامة
            if st == 404:
                if self.volatile_retention and tid in self.terminal_fp:
                    self._evicted.add(tid)
                    self.retention_evictions.append(
                        {"task": tid, "at": round(time.time(), 3),
                         "last_seen_terminal": True,
                         "contract": f"volatile MAX_TASKS_KEPT={self.volatile_retention} "
                                     "(finished records evicted oldest-first, R-164)"})
                    continue
                return False, {"task": tid, "violation": "task lost (404 for its own tenant)",
                               "last_seen_terminal": tid in self.terminal_fp,
                               "storage": "volatile" if self.volatile_retention else
                                          "durable_or_unknown"}
            if st != 200 or not isinstance(body, dict):
                return False, {"task": tid, "violation": f"unexpected status {st}"}
            if body.get("id") != tid or body.get("specialization") != "validator" \
                    or body.get("prompt") != TRACK_PROMPT:
                return False, {"task": tid, "violation": "stored fields changed"}
            status = body.get("status")
            if status not in PUBLIC_STATUSES:
                return False, {"task": tid, "violation": f"status outside API vocabulary: {status}"}
            fp = _fingerprint(body)
            if tid in self.terminal_fp:
                if status not in TERMINAL or fp != self.terminal_fp[tid]:
                    return False, {"task": tid,
                                   "violation": "terminal task regressed or changed"}
            elif status in TERMINAL:
                self.terminal_fp[tid] = fp
        return True, {"checked": len(ids)}


class TaskTracker:
    """طابور/عاملون مباشرة: مهام متتبعة (مستأجر A) من الإرسال حتى الحالة النهائية."""

    def __init__(self, url: str, tenants: Tenants, interval_s: float = 2.0,
                 timeout_s: float = 30.0):
        self.url, self.t = url, tenants
        self.interval, self.timeout = interval_s, timeout_s
        self.records: List[Dict[str, Any]] = []
        self._stop = threading.Event()
        self._threads: List[threading.Thread] = []
        self._lock = threading.Lock()

    def ids(self) -> List[str]:
        with self._lock:
            return [r["id"] for r in self.records if r.get("id")]

    def _one(self):
        t0 = time.monotonic()
        st, body = _http(self.url + "/api/tasks", "POST", self.t.a_key,
                         {"specialization": "validator", "prompt": TRACK_PROMPT}, timeout=10)
        rec = {"submit_status": st, "id": None, "final": None, "e2e_ms": None}
        if st == 202 and isinstance(body, dict):
            rec["id"] = body.get("task_id")
        with self._lock:
            self.records.append(rec)
        if not rec["id"]:
            return
        while time.monotonic() - t0 < self.timeout:
            s2, b2 = _http(f"{self.url}/api/tasks/{rec['id']}", key=self.t.a_key)
            if s2 == 200 and isinstance(b2, dict) and b2.get("status") in TERMINAL:
                rec["final"] = b2["status"]
                rec["e2e_ms"] = round((time.monotonic() - t0) * 1000, 3)
                return
            time.sleep(0.05)
        rec["final"] = "timeout"

    def _loop(self):
        while not self._stop.is_set():
            th = threading.Thread(target=self._one, daemon=True)
            th.start()
            self._threads.append(th)
            self._stop.wait(self.interval)

    def start(self):
        self._main = threading.Thread(target=self._loop, daemon=True)
        self._main.start()

    def stop(self) -> Dict[str, Any]:
        self._stop.set()
        self._main.join(timeout=5)
        for th in self._threads:
            th.join(timeout=self.timeout + 5)
        e2e = sorted(r["e2e_ms"] for r in self.records if r["e2e_ms"] is not None)
        nr = sl().nearest_rank
        return {
            "submitted": len(self.records),
            "accepted": sum(1 for r in self.records if r["id"]),
            "completed": sum(1 for r in self.records if r["final"] == "completed"),
            "failed": sum(1 for r in self.records if r["final"] == "failed"),
            "timeout": sum(1 for r in self.records if r["final"] == "timeout"),
            "e2e_p50_ms": nr(e2e, 0.5), "e2e_p95_ms": nr(e2e, 0.95),
            "e2e_max_ms": e2e[-1] if e2e else None,
            "all_completed": bool(self.records) and all(
                r["final"] == "completed" for r in self.records),
        }


# ----------------------------------------------------------- المراقب الحي

class LiveMonitor:
    """يذيّل مخرجات k6 JSON، يغذي المقيّم، يشغل المسابير، ويقطع k6 عند الخرق."""

    def __init__(self, evaluator, json_path: pathlib.Path, probes: Probes,
                 proc: subprocess.Popen, t0: float, tracker: Optional[TaskTracker] = None,
                 probe_interval_s: float = 1.0, deep_probe_every: int = 3,
                 inject: Optional[Callable[[float], None]] = None):
        self.ev, self.path, self.probes, self.proc = evaluator, json_path, probes, proc
        self.t0, self.tracker = t0, tracker
        self.interval, self.deep_every = probe_interval_s, deep_probe_every
        self.inject = inject
        self.samples: List[Dict[str, Any]] = []
        self.stop_sent_at: Optional[float] = None
        self.probe_failures: List[Dict[str, Any]] = []
        self._done = threading.Event()
        self._pos = 0
        self._buf = ""

    def _ids(self) -> List[str]:
        ids = [self.probes.sentinel] if self.probes.sentinel else []
        if self.tracker:
            ids += self.tracker.ids()[-10:]
        return ids

    def _drain(self) -> None:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as f:
            f.seek(self._pos)
            chunk = f.read()
            self._pos = f.tell()
        if not chunk:
            return
        data = self._buf + chunk
        lines = data.split("\n")
        self._buf = lines.pop()  # سطر غير مكتمل
        for line in lines:
            if not line.strip():
                continue
            try:
                p = json.loads(line)
            except ValueError:
                continue
            if p.get("type") != "Point":
                continue
            m = p.get("metric")
            d = p.get("data") or {}
            t = sl().parse_k6_time(d.get("time")) if d.get("time") else time.time()
            v = d.get("value")
            if m == "http_req_duration":
                self.ev.add_duration(t, v)
                self.samples.append({"ms": round(v, 3), "t_s": round(t - self.t0, 3),
                                     "endpoint": (d.get("tags") or {}).get("endpoint"),
                                     "seq": len(self.samples)})
            elif m == "http_req_failed":
                self.ev.add_failed_flag(t, bool(v))
            elif m == "checks":
                self.ev.add_check(t, bool(v))
            elif m == "iterations":
                self.ev.add_iteration(t)
            elif m == "dropped_iterations":
                self.ev.add_dropped(t, v or 1)

    def _probe(self, tick: int) -> None:
        now = time.time()
        for kind, fn in (("health_failure", self.probes.health),
                         ("readiness_failure", self.probes.ready)):
            ok, det = fn()
            if not ok:
                self.probe_failures.append({"kind": kind, "t_s": round(now - self.t0, 3),
                                            "detail": det})
            self.ev.add_probe(now, kind, ok, det)
        if tick % self.deep_every == 0:
            ids = self._ids()
            for kind, fn in (("tenant_isolation_violation", self.probes.isolation),
                             ("data_integrity_violation", self.probes.integrity)):
                ok, det = fn(ids)
                if not ok:
                    self.probe_failures.append({"kind": kind, "t_s": round(now - self.t0, 3),
                                                "detail": det})
                self.ev.add_probe(now, kind, ok, det)

    def _halt_k6(self) -> None:
        if self.stop_sent_at is None and self.proc.poll() is None:
            self.stop_sent_at = time.time()
            try:
                self.proc.send_signal(signal.SIGINT)  # k6: إيقاف مُنظم
            except ProcessLookupError:
                pass

    def run(self) -> None:
        tick = 0
        while not self._done.is_set():
            tick += 1
            if self.inject:
                self.inject(time.time() - self.t0)
            self._drain()
            self._probe(tick)
            self.ev.close_windows(time.time())
            if self.ev.decision.stopped:
                self._halt_k6()
            if self.stop_sent_at and self.proc.poll() is None \
                    and time.time() - self.stop_sent_at > 15:
                self.proc.kill()
            if self.proc.poll() is not None:
                break
            self._done.wait(self.interval)
        self._drain()
        self.ev.close_windows(time.time(), final=True)

    def finish(self) -> None:
        self._done.set()


# ------------------------------------------------------------------- SSE

def _sse_ttfb_probe(sut_url: str, api_key: str, probes: int = 4) -> Dict[str, Any]:
    """زمن اتصال تيار SSE حتى الرؤوس (TTFB) — مسبار منفصل عن المولد.

    k6 ينتظر جسم الرد كاملًا (مثبت تجريبيًا في السجل 366: تيار حتى
    timeout) فلا يصلح لقياس الاتصال فقط؛ المسبار هنا عملية/اتصال مستقلان
    عن SUT (HTTP فقط، نفس فصل R-176) يغلق بعد الرؤوس مباشرة — عينات
    قليلة بالتصميم لاحترام قيود قبول SSE (max_per_client=5).
    """
    import http.client
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


# ---------------------------------------------------------------- الواجهة

def frontend_paths() -> Optional[List[str]]:
    """مسارات الواجهة الحقيقية من web/dist المبني — None إن لم يُبن (لا قياس 404)."""
    idx = WEB_DIST / "index.html"
    if not idx.is_file():
        return None
    assets = sorted(p.name for p in (WEB_DIST / "assets").glob("*") if p.is_file())
    return ["/"] + [f"/assets/{a}" for a in assets] + ["/agents"]


# ------------------------------------------------------------------- التشغيل

def run_service(service_name: str, sut_url: Optional[str] = None,
                api_key: str = DEFAULT_API_KEY,
                out_dir: Optional[pathlib.Path] = None,
                extra_thresholds: Optional[Dict[str, Any]] = None,
                allow_dedicated_env: bool = False, profile: str = "baseline",
                tenants: Optional[Tenants] = None,
                inject: Optional[Callable[[float], None]] = None,
                monitor: bool = True,
                storage: Optional[str] = None) -> Dict[str, Any]:
    """قياس خدمة واحدة بملمح محدد مع حدود الإيقاف الحية وإنتاج تقرير الدليل الخام.

    التصنيف صريح: measured / skip / not_measured — لا شيء يمر صامتًا. والتشغيل
    الموقوف بحد إيقاف ok=False دائمًا (لا يمكن إخفاء الإيقاف).
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
    params = profile_params(svc, data, profile, allow_dedicated_env)
    verify_declared_thresholds(service_name, svc, data)
    # يرفض التشغيل بلا حدود/بحدود أضعف؛ المعدل المجدول لكشف الحجب (censored)
    policy = stop_policy_for(svc, data, params["arrival_rate"])
    thresholds = merge_tighten_only(approved_thresholds(svc, data), extra_thresholds)

    extra_env: Dict[str, str] = {}
    if svc.get("requires_web_dist"):
        paths = frontend_paths()
        if paths is None:
            return {"service": service_name, "classification": "not_measured",
                    "ok": False, "profile": profile,
                    "reason": "web/dist is not built — a 404 is not a frontend measurement "
                              "(build web/ first: npm ci && npm run build)",
                    "config_sha256": config_sha256()}
        extra_env["FRONTEND_PATHS"] = json.dumps(paths)
    if not sut_url:
        raise SystemExit(
            "a measured service needs --sut-url (or run via --all which "
            "starts a local SUT in a separate OS process)")

    k6 = ensure_k6()
    out = pathlib.Path(out_dir) if out_dir else pathlib.Path(tempfile.mkdtemp(
        prefix=f"r177_{service_name}_"))
    out.mkdir(parents=True, exist_ok=True)
    tag = f"{service_name}_{profile}"
    raw = out / f"k6_summary_{tag}.json"
    stream = out / f"k6_points_{tag}.jsonl"
    stdout_log = out / f"k6_stdout_{tag}.log"
    for f in (raw, stream):
        if f.exists():
            f.unlink()

    env = {**os.environ,
           "TARGET_URL": sut_url,
           "DURATION": f"{params['duration_s']}s",
           "RATE": str(params["arrival_rate"]),
           "MAX_VUS": str(params["max_vus"]),
           "API_KEY": api_key,
           "SERVICE": service_name,
           "PROFILE": profile,
           "THRESHOLDS": json.dumps(thresholds),
           **extra_env}

    # الاحتفاظ المتطاير يُسمح به فقط حين يُعلن صراحة أن SUT متطاير
    probes = Probes(sut_url, tenants,
                    volatile_retention=volatile_task_retention() if storage == "volatile"
                    else None)
    probes.setup_sentinel()
    tracker = None
    if tenants and svc.get("track_tasks"):
        tracker = TaskTracker(sut_url, tenants)
    t0 = time.time()
    ev = sl().StopEvaluator(policy, t0=t0)
    with stdout_log.open("w", encoding="utf-8") as logf:
        proc = subprocess.Popen(
            [str(k6), "run", "--summary-export", str(raw), "--out", f"json={stream}",
             "--summary-trend-stats", "avg,min,med,max,p(90),p(95),p(99)",
             str(SERVICE_JS)],
            env=env, stdout=logf, stderr=subprocess.STDOUT, cwd=str(REPO))
        if tracker:
            tracker.start()
        mon = LiveMonitor(ev, stream, probes, proc, t0, tracker=tracker, inject=inject)
        th = threading.Thread(target=mon.run, daemon=True)
        if monitor:
            th.start()
        try:
            proc.wait(timeout=params["duration_s"] + 240)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        mon.finish()
        if monitor:
            th.join(timeout=30)
        else:
            mon.run()  # تفريغ نهائي فقط (اختبارات)
    tracked = tracker.stop() if tracker else None
    # مسح نهائي للسلامة/العزل على كل المهام المتتبعة (بعد انتهاء الحمل)
    final_ids = ([probes.sentinel] if probes.sentinel else []) + (tracker.ids() if tracker else [])
    if tenants and not ev.decision.stopped:
        for kind, fn in (("tenant_isolation_violation", probes.isolation),
                         ("data_integrity_violation", probes.integrity)):
            ok_p, det = fn(final_ids)
            ev.add_probe(time.time(), kind, ok_p, det)
            if not ok_p:
                mon.probe_failures.append({"kind": kind, "final_sweep": True, "detail": det})

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
        th_ = m.get("thresholds") or {}
        checks[metric_name] = bool(th_) and len(th_) >= len(thresholds[metric_name]) and all(
            (not v if isinstance(v, bool) else bool(v.get("ok")))
            for v in th_.values())

    http_reqs = (metrics.get("http_reqs") or {}).get("count", 0)
    failed = (metrics.get("http_req_failed") or {}).get("count", None)
    if failed is None:
        rate = (metrics.get("http_req_failed") or {}).get("rate", 0.0)
        failed = round(rate * http_reqs)
    dur = metrics.get("http_req_duration") or {}

    vals = sorted(s["ms"] for s in mon.samples)
    nr = sl().nearest_rank
    raw_q = {"n": len(vals), "p50_ms": nr(vals, 0.5), "p95_ms": nr(vals, 0.95),
             "p99_ms": nr(vals, 0.99), "max_ms": vals[-1] if vals else None,
             # طريقة k6 نفسها (استيفاء خطي) — للتحقق من أن العدّاء لا يخترع أرقامًا (D-135)
             "p95_ms_k6_method": sl().k6_trend_quantile(vals, 0.95)}

    stop_rep = ev.report()
    stopped = ev.decision.stopped
    ok = (proc.returncode == 0 and bool(checks) and all(checks.values()) and not stopped)

    report = {
        "service": service_name,
        "classification": "measured",
        "ok": ok,
        "profile": profile,
        "k6_version": data["k6_version"],
        "k6_exit_code": proc.returncode,
        "config_sha256": config_sha256(),
        "thresholds": checks,
        "thresholds_declared": thresholds,
        "slo_status": svc.get("slo_status"),
        "slo_p95_ms": svc.get("slo_p95_ms"),
        "slo_p99_ms": svc.get("slo_p99_ms"),
        "metrics": {
            "http_reqs": http_reqs,
            "requests_failed": failed,
            "duration_avg_ms": dur.get("avg"),
            "duration_med_ms": dur.get("med"),
            "duration_p90_ms": dur.get("p(90)"),
            "duration_p95_ms": dur.get("p(95)"),
            "duration_p99_ms": dur.get("p(99)"),
            "duration_max_ms": dur.get("max"),
            "dropped_iterations": (metrics.get("dropped_iterations") or {}).get("count", 0),
        },
        "raw_quantiles": raw_q,
        "params": params,
        "baseline_params": params,
        "stop": stop_rep,
        "stopped": stopped,
        "probe_failures": mon.probe_failures,
        "sentinel_id": probes.sentinel,
        "storage": storage,
        "retention_evictions": probes.retention_evictions,
        "tracked_ids": tracker.ids() if tracker else [],
        "probe_counts": probes.counts,
        "probes_note": "light third HTTP client (1 req/s health+ready, isolation/integrity "
                       "every 3s) — outside k6 metrics",
        "generator_sut_separated": True,
        "raw_results_path": str(raw),
        "stdout_log_path": str(stdout_log),
    }
    if svc.get("slo_p99_ms") is not None:
        report["p99_assessment"] = sl().quantile_assessment(
            [s["ms"] for s in mon.samples], 0.99, float(svc["slo_p99_ms"]))
        report["tail_review"] = sl().tail_review(mon.samples, float(svc["slo_p99_ms"]))
        # p99 بلا عينات كافية لا يُعلن نجاحًا: الحكم الإحصائي جزء من ok
        report["ok"] = report["ok"] and report["p99_assessment"]["passed"]
    if tracked is not None:
        report["tracked_tasks"] = tracked
        report["ok"] = report["ok"] and tracked["all_completed"]
    if extra_env.get("FRONTEND_PATHS"):
        report["frontend_paths"] = json.loads(extra_env["FRONTEND_PATHS"])
    if service_name == "api-sse":
        report["sse_stream_ttfb"] = _sse_ttfb_probe(sut_url, api_key)
        report["ok"] = report["ok"] and all(
            s == 200 for s in report["sse_stream_ttfb"]["statuses"])
    if stopped:
        report["ok"] = False  # لا يمكن إخفاء الإيقاف
    (out / f"samples_{tag}.json").write_text(json.dumps(
        [[s["t_s"], s["ms"], s["endpoint"]] for s in mon.samples]), encoding="utf-8")
    (out / f"report_{tag}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def run_service_baseline(service_name: str, sut_url: Optional[str] = None,
                         api_key: str = DEFAULT_API_KEY,
                         out_dir: Optional[pathlib.Path] = None,
                         extra_thresholds: Optional[Dict[str, Any]] = None,
                         allow_dedicated_env: bool = False,
                         tenants: Optional[Tenants] = None) -> Dict[str, Any]:
    """توافق الدفعة 2: خط الأساس = run_service(profile='baseline')."""
    return run_service(service_name, sut_url=sut_url, api_key=api_key, out_dir=out_dir,
                       extra_thresholds=extra_thresholds,
                       allow_dedicated_env=allow_dedicated_env, profile="baseline",
                       tenants=tenants)


def _brief(rep: Dict[str, Any]) -> str:
    m = rep.get("metrics") or {}
    s = rep.get("stop", {}).get("decision", {})
    extra = ""
    if rep.get("p99_assessment"):
        a = rep["p99_assessment"]
        extra = f" p99={a['point_ms']}ms ucb95={a['upper_bound_ms']} n={a['n']} [{a['verdict']}]"
    if rep.get("tracked_tasks"):
        t = rep["tracked_tasks"]
        extra += f" tracked={t['completed']}/{t['submitted']} e2e_p95={t['e2e_p95_ms']}ms"
    return (f"ok={rep['ok']} class={rep.get('classification')} "
            f"p95={m.get('duration_p95_ms')}ms reqs={m.get('http_reqs')} "
            f"failed={m.get('requests_failed')} stopped={s.get('stopped')}"
            f"{' rule=' + str(s.get('rule')) if s.get('stopped') else ''}{extra}")


def coverage_snapshot(url: str, api_key: str) -> Dict[str, Any]:
    """دليل التغطية الضمنية: الأدوات المنفذة فعلًا (بوابة الأدوات) وعدادات قاطع llm."""
    st, body = _http(url + "/api/admin/tools", key=api_key, timeout=10)
    gate = (body or {}).get("gate_stats") if isinstance(body, dict) else None
    req = urllib.request.Request(url + "/metrics")
    llm_lines: List[str] = []
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            llm_lines = [ln for ln in r.read().decode().splitlines()
                         if 'service="llm"' in ln]
    except OSError:
        pass
    return {"tools_status": st, "tool_count": (body or {}).get("count")
            if isinstance(body, dict) else None,
            "executed": (gate or {}).get("executed"), "refused": (gate or {}).get("refused"),
            "llm_breaker_metrics": llm_lines}


def _diff_counts(a: Optional[Dict[str, int]], b: Optional[Dict[str, int]]) -> Dict[str, int]:
    a, b = a or {}, b or {}
    return {k: b.get(k, 0) - a.get(k, 0) for k in sorted(set(a) | set(b))
            if b.get(k, 0) - a.get(k, 0)}


def _run_each(names, sut_url, api_key, out, profile="baseline", tenants=None,
              storage=None, allow_dedicated_env=False):
    reports: Dict[str, Any] = {}
    for name in names:
        t0 = time.time()
        reports[name] = run_service(name, sut_url=sut_url, api_key=api_key, out_dir=out,
                                    profile=profile, tenants=tenants, storage=storage,
                                    allow_dedicated_env=allow_dedicated_env)
        print(f"[{name}:{profile}] {_brief(reports[name])} ({time.time() - t0:.0f}s)",
              flush=True)
    return reports


def run_all_baselines(sut_url: Optional[str] = None,
                      api_key: str = DEFAULT_API_KEY,
                      out_dir: Optional[pathlib.Path] = None,
                      profile: str = "baseline", storage: str = "volatile",
                      services: Optional[List[str]] = None,
                      allow_dedicated_env: bool = False) -> Dict[str, Any]:
    """كل الخدمات المقيسة — SUT واحد، حمل تلو الآخر، ملخص مصنف.

    D-141: تجاوز Q-023 المزدوج (`allow_dedicated_env` + `AGOS_ALLOW_BIG_LOAD=1`) يُمرَّر
    إلى كل خدمة؛ ويُفحص الحرس مرة قبل تشغيل أي SUT فلا يبدأ شيء ثم يُرفض.
    """
    data = load_services()
    if profile not in ("baseline", "normal"):
        heavy = (data.get("heavy_profiles") or {}).get(profile)
        if not heavy:
            raise SystemExit(f"unknown profile {profile}")
        _guard_dedicated_env(heavy, allow_dedicated_env)
    names = services or [n for n, s in data["services"].items() if s.get("measured")]
    out = pathlib.Path(out_dir) if out_dir else pathlib.Path(
        tempfile.mkdtemp(prefix="r177_all_"))
    out.mkdir(parents=True, exist_ok=True)

    reports: Dict[str, Any] = {}
    if sut_url is not None:
        reports = _run_each(names, sut_url, api_key, out, profile,
                            allow_dedicated_env=allow_dedicated_env)
    else:
        with sut_process(api_key, storage=storage) as sut:  # SUT محلي منفصل
            before = coverage_snapshot(sut.url, api_key)
            reports = _run_each(names, sut.url, api_key, out, profile, sut.tenants,
                                storage=sut.storage,
                                allow_dedicated_env=allow_dedicated_env)
            after = coverage_snapshot(sut.url, api_key)
            coverage = {"tools_executed_during_run": _diff_counts(before.get("executed"),
                                                                   after.get("executed")),
                        "tools_refused_during_run": _diff_counts(before.get("refused"),
                                                                  after.get("refused")),
                        "tool_count": after.get("tool_count"),
                        "llm_breaker_metrics_after": after.get("llm_breaker_metrics")}

    summary = {
        "config_sha256": config_sha256(),
        "coverage_evidence": locals().get("coverage"),
        "profile": profile,
        "storage": storage,
        "services": reports,
        "counts": {
            "measured": sum(1 for r in reports.values()
                            if r.get("classification") == "measured"),
            "not_measured": sum(1 for r in reports.values()
                                if r.get("classification") == "not_measured"),
            "stopped": sum(1 for r in reports.values() if r.get("stopped")),
            "failed": sum(1 for r in reports.values() if not r.get("ok")),
        },
        "skipped": {n: e["reason"] for n, e in
                    data.get("skipped", {}).items()},
    }
    (out / f"summary_all_{profile}_{storage}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"counts": summary["counts"],
                      "summary_path": str(out / f"summary_all_{profile}_{storage}.json")},
                     ensure_ascii=False))
    return summary


# ------------------------------------------------------- الأعطال والتعافي

def _local_stack_pids() -> Dict[str, Optional[int]]:
    """عمليتا PostgREST والوكيل المحليتان (لحقن عطل التخزين) — قراءة فقط."""
    out = {}
    for name, pat in (("postgrest", "postgrest"), ("proxy", "kong_prefix_proxy.py")):
        r = subprocess.run(["pgrep", "-f", pat], capture_output=True, text=True)
        pids = [int(x) for x in r.stdout.split()] if r.returncode == 0 else []
        out[name] = pids[0] if pids else None
    return out


def run_failure_recovery(scenario: str, out_dir: Optional[pathlib.Path] = None,
                         storage: str = "volatile", api_key: str = DEFAULT_API_KEY,
                         service: str = "api-tasks-submit",
                         fault_at_s: float = 10.0, outage_s: float = 10.0) -> Dict[str, Any]:
    """عطل حقيقي أثناء حمل normal صغير ⇒ يجب أن يُوقف حدُّ الإيقاف الحمل، ثم يُقاس التعافي.

    crash           : SIGKILL لعملية SUT ⇒ متوقع S5 (health/readiness) ⇒ إعادة تشغيل على
                      المنفذ نفسه: زمن /health و/health/ready، وبقاء المهام قبل العطل
                      (live-local: من التخزين؛ volatile: فقدان موثق بالتصميم)، ثم خط أساس.
    storage-outage  : SIGSTOP لـPostgREST (live-local فقط) لمدة outage_s ثم SIGCONT ⇒
                      يُسجل أي حد أوقف الحمل، وحالة الاستعداد أثناء العطل، ثم التعافي.
    """
    out = pathlib.Path(out_dir) if out_dir else pathlib.Path(
        tempfile.mkdtemp(prefix=f"r177_fr_{scenario}_"))
    out.mkdir(parents=True, exist_ok=True)
    if scenario == "storage-outage" and storage != "live-local":
        raise SystemExit("storage-outage needs --storage live-local")
    result: Dict[str, Any] = {"scenario": scenario, "storage": storage, "service": service,
                              "fault_at_s": fault_at_s}
    sut = SutProcess(api_key, storage=storage)
    try:
        result["cold_start_s"] = round(sut.start(), 3)
        state = {"fired": False, "fired_at": None, "resumed": False, "ready_during": []}
        stack = _local_stack_pids() if scenario == "storage-outage" else {}

        def inject(t: float):
            if not state["fired"] and t >= fault_at_s:
                state["fired"], state["fired_at"] = True, round(t, 3)
                if scenario == "crash":
                    sut.kill()
                elif scenario == "storage-outage" and stack.get("postgrest"):
                    os.kill(stack["postgrest"], signal.SIGSTOP)
            if scenario == "storage-outage" and state["fired"] and not state["resumed"]:
                state["ready_during"].append(_http(sut.url + "/health/ready", timeout=2)[0])
                if t >= fault_at_s + outage_s:
                    os.kill(stack["postgrest"], signal.SIGCONT)
                    state["resumed"] = True

        rep = run_service(service, sut_url=sut.url, api_key=api_key, out_dir=out,
                          profile="normal", tenants=sut.tenants, inject=inject,
                          storage=sut.storage)
        if scenario == "storage-outage" and state["fired"] and not state["resumed"]:
            os.kill(stack["postgrest"], signal.SIGCONT)
            state["resumed"] = True
        dec = rep["stop"]["decision"]
        result["load_report_ok"] = rep["ok"]
        result["fault_fired_at_s"] = state["fired_at"]
        result["stop_decision"] = dec
        result["detection_latency_s"] = (None if not dec.get("stopped") or state["fired_at"] is None
                                         else round(dec["at_s"] - state["fired_at"], 3))
        result["probe_failures_first"] = rep["probe_failures"][:5]
        result["metrics_during"] = rep["metrics"]
        result["tracked_before_fault"] = rep.get("tracked_tasks")
        # ---- التعافي
        if scenario == "crash":
            t_r = time.monotonic()
            sut.proc = None
            sut.start()
            result["restart_to_health_s"] = round(time.monotonic() - t_r, 3)
            result["restart_to_ready_s"] = sut.wait_status("/health/ready", 200, 60)
        else:
            result["ready_statuses_during_outage"] = sorted(
                {str(s) for s in state["ready_during"]})
            result["post_resume_to_ready_s"] = sut.wait_status("/health/ready", 200, 60)
        # بقاء مهام ما قبل العطل، واستكمال اليتيمة (lease/reaper — R-163) حتى 150s
        ids = [i for i in ([rep.get("sentinel_id")] + list(rep.get("tracked_ids") or [])) if i]
        final: Dict[str, Any] = {}
        t_o = time.monotonic()
        while time.monotonic() - t_o < 150:
            pending = 0
            for tid in ids:
                if final.get(tid) in ("completed", "failed", "lost"):
                    continue
                st, body = _http(f"{sut.url}/api/tasks/{tid}", key=sut.tenants.a_key)
                if st == 404:
                    final[tid] = "lost"
                elif st == 200 and isinstance(body, dict):
                    final[tid] = body.get("status")
                    if final[tid] not in TERMINAL:
                        pending += 1
                else:
                    pending += 1
            if pending == 0:
                break
            time.sleep(1.0)
        vals = list(final.values())
        result["pre_fault_tasks"] = {
            "checked": len(ids), "lost": vals.count("lost"),
            "completed": vals.count("completed"), "failed": vals.count("failed"),
            "still_pending": sum(1 for v in vals if v not in ("lost", "completed", "failed")),
            "settle_s": round(time.monotonic() - t_o, 1)}
        # خط أساس بعد التعافي: يجب أن يمر بالعتبات المعتمدة
        t_b = time.monotonic()
        post = run_service(service, sut_url=sut.url, api_key=api_key, out_dir=out / "post",
                           profile="baseline", tenants=sut.tenants, storage=sut.storage)
        result["post_recovery_baseline"] = {"ok": post["ok"], "brief": _brief(post),
                                            "elapsed_s": round(time.monotonic() - t_b, 1)}
    finally:
        if scenario == "storage-outage":
            st = _local_stack_pids()
            if st.get("postgrest"):
                with contextlib.suppress(ProcessLookupError):
                    os.kill(st["postgrest"], signal.SIGCONT)
        sut.stop()
    dec = result["stop_decision"]
    result["ok"] = bool(
        result["fault_fired_at_s"] is not None and dec.get("stopped")
        and result["post_recovery_baseline"]["ok"]
        and (scenario != "crash" or result["restart_to_ready_s"] is not None))
    (out / f"failure_recovery_{scenario}_{storage}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ensure-k6", action="store_true",
                    help="تثبيت k6 المثبت (R-176) والخروج")
    ap.add_argument("--service", default=None,
                    help="قياس خدمة واحدة (انظر benchmarks/r177/services.json)")
    ap.add_argument("--all", action="store_true",
                    help="كل الخدمات المقيسة على SUT واحد")
    ap.add_argument("--services", default=None,
                    help="قائمة خدمات مفصولة بفواصل مع --all")
    ap.add_argument("--profile", default="baseline",
                    help="baseline | normal | stress | spike | soak (الثلاثة الأخيرة Q-023)")
    ap.add_argument("--storage", default="volatile", choices=["volatile", "live-local"])
    ap.add_argument("--failure-recovery", default=None, choices=["crash", "storage-outage"])
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
    out = pathlib.Path(args.out) if args.out else None
    if args.profile not in ("baseline", "normal"):
        heavy = (load_services().get("heavy_profiles") or {}).get(args.profile)
        if not heavy:
            raise SystemExit(f"unknown profile {args.profile}")
        _guard_dedicated_env(heavy, args.allow_dedicated_env)
    if args.failure_recovery:
        res = run_failure_recovery(args.failure_recovery, out_dir=out, storage=args.storage,
                                   api_key=args.api_key)
        return 0 if res["ok"] else 1
    if args.all:
        names = args.services.split(",") if args.services else None
        summary = run_all_baselines(sut_url=args.sut_url, api_key=args.api_key, out_dir=out,
                                    profile=args.profile, storage=args.storage,
                                    services=names,
                                    allow_dedicated_env=args.allow_dedicated_env)
        return 0 if summary["counts"]["failed"] == 0 else 1
    if args.service:
        if args.sut_url:
            rep = run_service(args.service, sut_url=args.sut_url, api_key=args.api_key,
                              out_dir=out, allow_dedicated_env=args.allow_dedicated_env,
                              profile=args.profile)
        else:
            with sut_process(args.api_key, storage=args.storage) as sut:
                rep = run_service(args.service, sut_url=sut.url, api_key=args.api_key,
                                  out_dir=out, allow_dedicated_env=args.allow_dedicated_env,
                                  profile=args.profile, tenants=sut.tenants,
                                  storage=sut.storage)
        print(json.dumps({k: v for k, v in rep.items() if k != "stop"} | {
            "stop_decision": (rep.get("stop") or {}).get("decision")},
            ensure_ascii=False, indent=2))
        return 0 if rep.get("ok") else 1
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
