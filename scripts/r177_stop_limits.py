#!/usr/bin/env python3
"""R-177 دفعة 3 (D-134): حدود الإيقاف (stop limits) — محرك قابل للاختبار آليًا.

قرار المالك (2026-11-07): اعتماد p95 المقترحة، ميزانية أخطاء < 1%، نجاح
فحوص > 99%، p99 مستقل للدردشة (1000ms مبدئيًا بشرط كفاية العينات)، وحدود
إيقاف محددة مسبقًا:

  S1  أخطاء > 1%                                   ⇒ إيقاف الحمل
  S2  نجاح الفحوص < 99%                            ⇒ إيقاف الحمل
  S3  p95 > ضعفي SLO لنافذتين متتاليتين           ⇒ إيقاف
  S4  p99 الدردشة > ضعفي عتبته لنافذتين متتاليتين ⇒ إيقاف
  S5  فشل الصحة/الاستعداد، انتهاك عزل المستأجرين،
      خطر على سلامة البيانات                       ⇒ إيقاف فوري

عتبة p99 (SLO: نجاح/فشل التشغيل) ≠ حد إيقاف p99 (2× العتبة لنافذتين):
الأولى حكم على تشغيل اكتمل، والثانية صمام أمان يقطع الحمل أثناءه.

ضمانات عدم التجاوز (مختبرة في tests/test_r177_stop_limits.py):
  * الأرضية OWNER_FLOOR ثابتة في الكود: أي سياسة أضعف منها تُرفض قبل
    التشغيل (PolicyError) — لا يمكن إرخاء الحد من الإعداد.
  * القرار «لاصق»: أول خرق يجمّد الإيقاف؛ لا شيء لاحق يلغيه.
  * التقرير يحمل القاعدة والسبب والقياسات المسببة؛ والتشغيل الموقوف لا
    يكون ok أبدًا (العدّاء يفرض ذلك).
  * النوافذ ذات العينات القليلة «غير كافية»: لا تكسر سلسلة الخرق ولا
    تُحتسب نجاحًا (لا إخفاء بالإبطاء حتى تقل العينات).

المحرك نقي (بلا شبكة/عمليات) — الربط الحي (تذييل مخرجات k6 JSON ومسابير
الصحة/العزل/السلامة) في scripts/r177_per_service.py.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

# أرضية المالك — لا تُقرأ من الإعداد ولا تُرخى منه (قرار 2026-11-07)
OWNER_FLOOR: Dict[str, Any] = {
    "error_rate_max": 0.01,
    "checks_rate_min": 0.99,
    "latency_multiplier_max": 2.0,
    "consecutive_windows_max": 2,
    "min_requests_for_rate_max": 100,
    "min_window_samples_max": 20,
    "window_s_max": 30,
}

IMMEDIATE_RULES = (
    "health_failure",
    "readiness_failure",
    "tenant_isolation_violation",
    "data_integrity_violation",
)

RULE_IDS = {
    "error_rate": "S1",
    "checks_rate": "S2",
    "p95_window": "S3",
    "p99_window": "S4",
    "health_failure": "S5",
    "readiness_failure": "S5",
    "tenant_isolation_violation": "S5",
    "data_integrity_violation": "S5",
}


class PolicyError(ValueError):
    """سياسة إيقاف أضعف من أرضية المالك أو ناقصة — يرفض العدّاء التشغيل."""


@dataclass(frozen=True)
class StopPolicy:
    error_rate_max: float
    checks_rate_min: float
    latency_multiplier: float
    consecutive_windows: int
    window_s: float
    min_window_samples: int
    min_requests_for_rate: int
    immediate: tuple
    p95_slo_ms: Optional[float] = None
    p99_slo_ms: Optional[float] = None
    # معدل الوصول الثابت المجدول (تكرارات/ث) — لكشف «النافذة المحجوبة»
    expected_iter_rate: Optional[float] = None
    stall_completion_frac: float = 0.5

    @property
    def p95_stop_ms(self) -> Optional[float]:
        return None if self.p95_slo_ms is None else self.p95_slo_ms * self.latency_multiplier

    @property
    def p99_stop_ms(self) -> Optional[float]:
        return None if self.p99_slo_ms is None else self.p99_slo_ms * self.latency_multiplier


def validate_policy(raw: Dict[str, Any], p95_slo_ms: Optional[float],
                    p99_slo_ms: Optional[float] = None,
                    expected_iter_rate: Optional[float] = None) -> StopPolicy:
    """يبني السياسة ويرفض أي إرخاء دون أرضية المالك (لا تشغيل بلا حدود)."""
    if not isinstance(raw, dict) or not raw:
        raise PolicyError("stop_limits missing — no load runs without predefined stop limits")
    req = ("error_rate_max", "checks_rate_min", "latency_multiplier",
           "consecutive_windows", "window_s", "min_window_samples",
           "min_requests_for_rate", "immediate")
    missing = [k for k in req if k not in raw]
    if missing:
        raise PolicyError(f"stop_limits incomplete: missing {missing}")
    f = OWNER_FLOOR
    er = float(raw["error_rate_max"])
    if not (0 <= er <= f["error_rate_max"]):
        raise PolicyError(f"error_rate_max={er} is weaker than the owner floor {f['error_rate_max']}")
    cr = float(raw["checks_rate_min"])
    if not (f["checks_rate_min"] <= cr <= 1):
        raise PolicyError(f"checks_rate_min={cr} is weaker than the owner floor {f['checks_rate_min']}")
    lm = float(raw["latency_multiplier"])
    if not (1.0 <= lm <= f["latency_multiplier_max"]):
        raise PolicyError(f"latency_multiplier={lm} outside [1, {f['latency_multiplier_max']}]")
    cw = raw["consecutive_windows"]
    if not (isinstance(cw, int) and 1 <= cw <= f["consecutive_windows_max"]):
        raise PolicyError(f"consecutive_windows={cw} outside [1, {f['consecutive_windows_max']}]")
    ws = float(raw["window_s"])
    if not (1 <= ws <= f["window_s_max"]):
        raise PolicyError(f"window_s={ws} outside [1, {f['window_s_max']}]")
    mws = raw["min_window_samples"]
    if not (isinstance(mws, int) and 1 <= mws <= f["min_window_samples_max"]):
        raise PolicyError(f"min_window_samples={mws} outside [1, {f['min_window_samples_max']}]")
    mr = raw["min_requests_for_rate"]
    if not (isinstance(mr, int) and 1 <= mr <= f["min_requests_for_rate_max"]):
        raise PolicyError(f"min_requests_for_rate={mr} outside [1, {f['min_requests_for_rate_max']}]")
    imm = tuple(raw["immediate"])
    absent = [r for r in IMMEDIATE_RULES if r not in imm]
    if absent:
        raise PolicyError(f"immediate stop rules cannot be removed: missing {absent}")
    if p95_slo_ms is None or float(p95_slo_ms) <= 0:
        raise PolicyError("a measured service needs an approved p95 SLO to derive its stop limit")
    if p99_slo_ms is not None and float(p99_slo_ms) <= 0:
        raise PolicyError("p99 SLO must be positive")
    if expected_iter_rate is not None and float(expected_iter_rate) <= 0:
        raise PolicyError("expected_iter_rate must be positive")
    return StopPolicy(er, cr, lm, cw, ws, mws, mr, imm,
                      float(p95_slo_ms),
                      None if p99_slo_ms is None else float(p99_slo_ms),
                      None if expected_iter_rate is None else float(expected_iter_rate))


# ----------------------------------------------------------------- إحصاء

def nearest_rank(sorted_vals: List[float], q: float) -> Optional[float]:
    """المئين بطريقة الرتبة الأقرب (نفس ما يُعلن في التقارير)."""
    if not sorted_vals:
        return None
    k = max(1, math.ceil(q * len(sorted_vals)))
    return sorted_vals[k - 1]


def min_samples_for_quantile(q: float, confidence: float = 0.95) -> int:
    """أصغر n تجعل أكبر عينة حدًا أعلى (توزيعيًا-حرًا) للمئين q بثقة confidence:
    1 - q^n >= confidence  ⇒  n >= ln(1-confidence)/ln(q). لـp99@95% = 299."""
    return math.ceil(math.log(1 - confidence) / math.log(q))


def _binom_cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) لـX~Binom(n,p) — بالجمع في الفضاء اللوغاريتمي (n صغير/متوسط)."""
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    total = 0.0
    lp, lq = math.log(p), math.log1p(-p)
    for i in range(0, k + 1):
        total += math.exp(math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1)
                          + i * lp + (n - i) * lq)
    return min(1.0, total)


def quantile_upper_bound(sorted_vals: List[float], q: float,
                         confidence: float = 0.95) -> Optional[float]:
    """حد أعلى توزيعي-حر للمئين q: أصغر رتبة r بحيث P(Binom(n,q) <= r-1) >= confidence،
    فيكون x_(r) >= المئين الحقيقي باحتمال >= confidence. None = العينات غير كافية."""
    n = len(sorted_vals)
    if n == 0:
        return None
    for r in range(1, n + 1):
        if _binom_cdf(r - 1, n, q) >= confidence:
            return sorted_vals[r - 1]
    return None


def quantile_assessment(samples: List[float], q: float, threshold_ms: float,
                        confidence: float = 0.95) -> Dict[str, Any]:
    """حكم عتبة مئين مع التحقق الإحصائي من كفاية العينات (طلب المالك لـp99)."""
    s = sorted(samples)
    n = len(s)
    n_min = min_samples_for_quantile(q, confidence)
    point = nearest_rank(s, q)
    ucb = quantile_upper_bound(s, q, confidence)
    sufficient = n >= n_min and ucb is not None
    passed = bool(sufficient and point is not None and point < threshold_ms
                  and ucb < threshold_ms)
    if not sufficient:
        verdict = "insufficient_samples"
    else:
        verdict = "pass" if passed else "fail"
    return {
        "quantile": q, "threshold_ms": threshold_ms, "n": n,
        "n_min_required": n_min, "confidence": confidence,
        "point_ms": point, "upper_bound_ms": ucb,
        "sufficient": sufficient, "verdict": verdict, "passed": passed,
        "rule": "pass ⇔ n >= n_min AND point < threshold AND distribution-free "
                f"{int(confidence * 100)}% upper bound < threshold",
    }


def tail_review(samples_with_meta: List[Dict[str, Any]], threshold_ms: float,
                top_k: int = 5) -> Dict[str, Any]:
    """مراجعة الذيل العلوي (ملاحظة السجل 366: max=750.8ms مع med≈5ms)."""
    if not samples_with_meta:
        return {"n": 0}
    ordered = sorted(samples_with_meta, key=lambda d: d["ms"], reverse=True)
    vals = sorted(d["ms"] for d in samples_with_meta)
    med = nearest_rank(vals, 0.5)
    return {
        "n": len(vals),
        "max_ms": vals[-1],
        "med_ms": med,
        "max_over_med": round(vals[-1] / med, 2) if med else None,
        "over_threshold": sum(1 for v in vals if v >= threshold_ms),
        "over_half_threshold": sum(1 for v in vals if v >= threshold_ms / 2),
        "top": ordered[:top_k],
    }


# ----------------------------------------------------------------- المحرك

_FRAC = re.compile(r"(\.\d{6})\d+")


def parse_k6_time(s: str) -> float:
    """وقت k6 (RFC3339 بنانوثوانٍ) ⇒ ثوانٍ epoch."""
    s = _FRAC.sub(r"\1", s.replace("Z", "+00:00"))
    return datetime.fromisoformat(s).timestamp()


@dataclass
class StopDecision:
    stopped: bool = False
    rule: Optional[str] = None
    rule_id: Optional[str] = None
    reason: Optional[str] = None
    at_s: Optional[float] = None
    measurements: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {"stopped": self.stopped, "rule": self.rule, "rule_id": self.rule_id,
                "reason": self.reason, "at_s": self.at_s,
                "measurements": self.measurements}


class StopEvaluator:
    """يستهلك عينات الطلبات/الفحوص/المسابير ويقرر الإيقاف وفق السياسة.

    الأوقات بالثواني منذ بداية التشغيل (t0). القرار لاصق: أول خرق يُسجل
    ولا يُلغى؛ والخروقات اللاحقة تُسجل في breaches للشفافية.
    """

    def __init__(self, policy: StopPolicy, t0: float = 0.0):
        self.p = policy
        self.t0 = t0
        self.decision = StopDecision()
        self.breaches: List[Dict[str, Any]] = []
        self.total = 0
        self.failed = 0
        self.checks_total = 0
        self.checks_passed = 0
        self._win: Dict[int, Dict[str, Any]] = {}
        self._closed_upto = -1
        self._p95_streak: List[Dict[str, Any]] = []
        self._p99_streak: List[Dict[str, Any]] = []
        self.windows: List[Dict[str, Any]] = []

    # -------------------------------------------------------------- إدخال
    def _w(self, t: float) -> Dict[str, Any]:
        idx = int(max(0.0, t - self.t0) // self.p.window_s)
        return self._win.setdefault(idx, {"idx": idx, "durations": [], "failed": 0,
                                          "reqs": 0, "checks": 0, "checks_ok": 0,
                                          "iters": 0, "dropped": 0})

    def add_iteration(self, t: float) -> None:
        """تكرار k6 مكتمل (نقطة iterations) — مقام كشف الحجب."""
        self._w(t)["iters"] += 1

    def add_dropped(self, t: float, n: float = 1) -> None:
        """تكرارات أسقطها k6 لنفاد VUs (نقطة dropped_iterations) — دليل حجب."""
        self._w(t)["dropped"] += int(n)

    def stall_applicable(self) -> bool:
        """الكشف ممكن فقط حين يكفي المجدول لكل نافذة لعدّ ذي معنى."""
        r = self.p.expected_iter_rate
        return bool(r and r * self.p.window_s >= self.p.min_window_samples)

    def add_request(self, t: float, duration_ms: float, failed: bool) -> None:
        w = self._w(t)
        w["durations"].append(float(duration_ms))
        w["reqs"] += 1
        self.total += 1
        if failed:
            w["failed"] += 1
            self.failed += 1
        self._eval_rates(t)

    def add_failed_flag(self, t: float, failed: bool) -> None:
        """نقطة http_req_failed منفصلة (مخرجات k6 JSON تفصل المقياسين)."""
        w = self._w(t)
        w["reqs"] += 1
        self.total += 1
        if failed:
            w["failed"] += 1
            self.failed += 1
        self._eval_rates(t)

    def add_duration(self, t: float, duration_ms: float) -> None:
        self._w(t)["durations"].append(float(duration_ms))

    def add_check(self, t: float, passed: bool) -> None:
        w = self._w(t)
        w["checks"] += 1
        self.checks_total += 1
        if passed:
            w["checks_ok"] += 1
            self.checks_passed += 1
        self._eval_rates(t)

    def add_probe(self, t: float, kind: str, ok: bool, detail: Any = None) -> None:
        if ok:
            return
        if kind not in IMMEDIATE_RULES:
            raise ValueError(f"unknown probe kind {kind}")
        if kind in self.p.immediate:
            self._trip(kind, f"{kind}: immediate stop (S5)", t,
                       {"probe": kind, "detail": detail})

    # -------------------------------------------------------------- حكم
    def _trip(self, rule: str, reason: str, t: float, meas: Dict[str, Any]) -> None:
        rec = {"rule": rule, "rule_id": RULE_IDS[rule], "reason": reason,
               "at_s": round(t - self.t0, 3), "measurements": meas}
        self.breaches.append(rec)
        if not self.decision.stopped:  # لاصق: الأول يحكم
            self.decision = StopDecision(True, rule, RULE_IDS[rule], reason,
                                         rec["at_s"], meas)

    def _eval_rates(self, t: float) -> None:
        if self.total >= self.p.min_requests_for_rate:
            rate = self.failed / self.total
            if rate > self.p.error_rate_max and not self._has("error_rate"):
                self._trip("error_rate",
                           f"error rate {rate:.4f} > {self.p.error_rate_max} (S1)", t,
                           {"failed": self.failed, "total": self.total,
                            "rate": round(rate, 6), "limit": self.p.error_rate_max,
                            "scope": "cumulative"})
        if self.checks_total >= self.p.min_requests_for_rate:
            crate = self.checks_passed / self.checks_total
            if crate < self.p.checks_rate_min and not self._has("checks_rate"):
                self._trip("checks_rate",
                           f"checks pass rate {crate:.4f} < {self.p.checks_rate_min} (S2)", t,
                           {"passed": self.checks_passed, "total": self.checks_total,
                            "rate": round(crate, 6), "limit": self.p.checks_rate_min,
                            "scope": "cumulative"})

    def _has(self, rule: str) -> bool:
        return any(b["rule"] == rule for b in self.breaches)

    def close_windows(self, now: float, final: bool = False) -> None:
        """يغلق النوافذ المنتهية (مع مهلة 1s لتأخر التدفق) ويقيّم S1–S4 لكل نافذة."""
        last = int(max(0.0, now - self.t0 - 1.0) // self.p.window_s) - 1
        partial = None
        if final:
            last = max(self._win) if self._win else -1
            # النافذة الأخيرة عند الإغلاق النهائي جزئية زمنيًا: لا يُستدل منها حجب
            partial = last
        for idx in range(self._closed_upto + 1, last + 1):
            self._close_one(idx, partial=(idx == partial))
        self._closed_upto = max(self._closed_upto, last)

    def _close_one(self, idx: int, partial: bool = False) -> None:
        w = self._win.get(idx, {"idx": idx, "durations": [], "failed": 0,
                                "reqs": 0, "checks": 0, "checks_ok": 0,
                                "iters": 0, "dropped": 0})
        d = sorted(w["durations"])
        t_end = self.t0 + (idx + 1) * self.p.window_s
        sufficient = len(d) >= self.p.min_window_samples
        p95 = nearest_rank(d, 0.95)
        p99 = nearest_rank(d, 0.99)
        rec = {"idx": idx, "samples": len(d), "sufficient": sufficient,
               "p95_ms": p95, "p99_ms": p99, "reqs": w["reqs"],
               "failed": w["failed"], "checks": w["checks"],
               "checks_ok": w["checks_ok"], "iters": w.get("iters", 0),
               "dropped": w.get("dropped", 0)}
        # «نافذة محجوبة» (censored): بوصول ثابت مجدول، إن اكتمل أقل من نصف
        # التكرارات المجدولة فالبقية إما معلقة (زمنها لم يُرصد بعد) أو أسقطها k6
        # لأن كل VUs معلقة. توزيع الزمن في النافذة مقطوع ولا يمكن أن يُرى
        # p95 الحقيقي من المكتمل وحده — فتُحتسب خرقًا لـS3 (وS4) بتحفظ، لا
        # «عينات غير كافية» تُتجاهل. هذا يسد ثغرة كشفها قياس انقطاع التخزين:
        # طلبات معلقة 11s دون أي إيقاف (السجل 368).
        stalled = False
        if self.stall_applicable() and not partial and idx > 0:
            expected = self.p.expected_iter_rate * self.p.window_s
            if rec["iters"] < self.p.stall_completion_frac * expected:
                stalled = True
                rec.update({"stalled": True, "expected_iters": expected,
                            "completion_frac": round(rec["iters"] / expected, 4)})
        self.windows.append(rec)
        # S1/S2 على مستوى النافذة (انفجار أخطاء متأخر لا يذوب في التراكمي) —
        # فقط إن بلغت النافذة min_requests_for_rate (دقة 1% تحتاج >=100 عينة)
        if w["reqs"] >= self.p.min_requests_for_rate:
            wr = w["failed"] / w["reqs"]
            if wr > self.p.error_rate_max:
                self._trip("error_rate", f"window {idx} error rate {wr:.4f} > "
                           f"{self.p.error_rate_max} (S1)", t_end,
                           {"window": idx, "failed": w["failed"], "total": w["reqs"],
                            "rate": round(wr, 6), "limit": self.p.error_rate_max,
                            "scope": "window"})
        if w["checks"] >= self.p.min_requests_for_rate:
            wc = w["checks_ok"] / w["checks"]
            if wc < self.p.checks_rate_min:
                self._trip("checks_rate", f"window {idx} checks rate {wc:.4f} < "
                           f"{self.p.checks_rate_min} (S2)", t_end,
                           {"window": idx, "passed": w["checks_ok"], "total": w["checks"],
                            "rate": round(wc, 6), "limit": self.p.checks_rate_min,
                            "scope": "window"})
        if stalled:
            self._streak("p95_window", self._p95_streak, None, self.p.p95_stop_ms, rec,
                         t_end, censored=True)
            if self.p.p99_slo_ms is not None:
                self._streak("p99_window", self._p99_streak, None, self.p.p99_stop_ms, rec,
                             t_end, censored=True)
            return
        if not sufficient:
            return  # غير كافية: لا تكسر السلسلة ولا تُحتسب نجاحًا
        self._streak("p95_window", self._p95_streak, p95, self.p.p95_stop_ms, rec, t_end)
        if self.p.p99_slo_ms is not None:
            self._streak("p99_window", self._p99_streak, p99, self.p.p99_stop_ms, rec, t_end)

    def _streak(self, rule: str, streak: List[Dict[str, Any]], val: Optional[float],
                limit: Optional[float], rec: Dict[str, Any], t_end: float,
                censored: bool = False) -> None:
        if limit is None or (val is None and not censored):
            return
        if censored or val > limit:
            item = {"window": rec["idx"], "value_ms": val, "samples": rec["samples"]}
            if censored:
                item.update({"censored": True, "iters": rec["iters"],
                             "expected_iters": rec.get("expected_iters"),
                             "dropped": rec["dropped"],
                             "observed_p95_ms_of_completed": rec["p95_ms"]})
            streak.append(item)
            if len(streak) >= self.p.consecutive_windows:
                q = "p95" if rule == "p95_window" else "p99"
                cens = [x["window"] for x in streak[-self.p.consecutive_windows:]
                        if x.get("censored")]
                note = (f" — censored windows {cens}: < {self.p.stall_completion_frac:.0%} "
                        "of scheduled iterations completed (stalled/in-flight)") if cens else ""
                self._trip(rule, f"{q} > {self.p.latency_multiplier}x SLO{note} "
                           f"({limit:.1f}ms) for {len(streak)} consecutive windows "
                           f"({RULE_IDS[rule]})", t_end,
                           {"windows": list(streak[-self.p.consecutive_windows:]),
                            "limit_ms": limit, "consecutive_required":
                            self.p.consecutive_windows,
                            "window_s": self.p.window_s})
        else:
            streak.clear()

    # -------------------------------------------------------------- تقرير
    def report(self) -> Dict[str, Any]:
        return {
            "policy": {
                "error_rate_max": self.p.error_rate_max,
                "checks_rate_min": self.p.checks_rate_min,
                "latency_multiplier": self.p.latency_multiplier,
                "consecutive_windows": self.p.consecutive_windows,
                "window_s": self.p.window_s,
                "min_window_samples": self.p.min_window_samples,
                "min_requests_for_rate": self.p.min_requests_for_rate,
                "immediate": list(self.p.immediate),
                "p95_slo_ms": self.p.p95_slo_ms, "p95_stop_ms": self.p.p95_stop_ms,
                "p99_slo_ms": self.p.p99_slo_ms, "p99_stop_ms": self.p.p99_stop_ms,
                "owner_floor": dict(OWNER_FLOOR),
                "expected_iter_rate": self.p.expected_iter_rate,
                "stall_completion_frac": self.p.stall_completion_frac,
                "stall_detection_applicable": self.stall_applicable(),
            },
            "decision": self.decision.as_dict(),
            "breaches": list(self.breaches),
            "totals": {"requests": self.total, "failed": self.failed,
                       "checks": self.checks_total, "checks_passed": self.checks_passed},
            "windows": list(self.windows),
        }
