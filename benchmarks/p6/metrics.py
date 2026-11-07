"""P6/P7 (R-178..R-184) acceptance metrics — D-137.

Every acceptance criterion in ``benchmarks/p6/acceptance_criteria.json``
names ONE function here (``metric``) that computes its value from raw run
data, a comparison operator and a threshold. ``evaluate`` turns a criterion
plus raw data into ``pass`` / ``fail`` / ``not_measured`` — never ``pass``
on missing or insufficient data, and never ``accepted`` while the
threshold is only ``proposed`` (owner approval, like the SLOs of D-133).

Pure functions, no I/O, no network: they run in CI on synthetic data and on
the real raw files after a run. They measure nothing by themselves.
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, Iterable, Optional, Sequence

# ------------------------------------------------------------ quantiles

def nearest_rank(values: Sequence[float], q: float) -> Optional[float]:
    """Reported quantile method of the project (ceil nearest-rank, log 375)."""
    if not values:
        return None
    s = sorted(values)
    k = max(1, math.ceil(q * len(s)))
    return s[k - 1]


def min_samples_for_quantile(q: float, confidence: float = 0.95) -> int:
    """Same rule as scripts/r177_stop_limits.py: 1 - q^n >= confidence."""
    return math.ceil(math.log(1 - confidence) / math.log(q))


def p95_ms(latencies_ms: Sequence[float]) -> Optional[float]:
    return nearest_rank(latencies_ms, 0.95)


def p99_ms(latencies_ms: Sequence[float]) -> Optional[float]:
    return nearest_rank(latencies_ms, 0.99)


# ------------------------------------------------------------ ratios / counts

def error_rate(statuses: Sequence[int]) -> Optional[float]:
    """Fraction of responses that are errors: transport failure (0) or 5xx.
    Structured refusals (429/503 with Retry-After) are counted separately by
    ``unstructured_refusals`` — a 503 IS an error here (S1 semantics)."""
    if not statuses:
        return None
    bad = sum(1 for s in statuses if s == 0 or s >= 500)
    return bad / len(statuses)


def unstructured_refusals(responses: Iterable[Dict[str, Any]]) -> int:
    """Over-limit responses that are NOT a structured refusal: anything 5xx
    other than 503-with-Retry-After, or a 429 without Retry-After."""
    n = 0
    for r in responses:
        s, ra = r.get("status"), r.get("retry_after")
        if s == 500 or (s is not None and s >= 500 and s != 503):
            n += 1
        elif s in (429, 503) and ra in (None, ""):
            n += 1
    return n


def jain_index(counts: Sequence[float]) -> Optional[float]:
    """Jain fairness (sum x)^2 / (n * sum x^2) in [1/n, 1]. 1.0 = perfectly fair."""
    xs = [float(c) for c in counts]
    if not xs or sum(x * x for x in xs) == 0:
        return None
    return (sum(xs) ** 2) / (len(xs) * sum(x * x for x in xs))


def max_min_ratio(counts: Sequence[float]) -> Optional[float]:
    xs = [float(c) for c in counts]
    if not xs:
        return None
    lo = min(xs)
    return math.inf if lo == 0 else max(xs) / lo


def duplicate_side_effects(effect_keys: Iterable[str]) -> int:
    """Side effects observed at the (fake) recipient, keyed by idempotency
    key: every key applied more than once counts its extra applications."""
    seen: Dict[str, int] = {}
    for k in effect_keys:
        seen[k] = seen.get(k, 0) + 1
    return sum(v - 1 for v in seen.values() if v > 1)


def silent_loss(accepted_ids: Iterable[str], terminal_ids: Iterable[str],
                dlq_ids: Iterable[str] = (), leased_ids: Iterable[str] = ()) -> int:
    """Accepted tasks that are neither terminal, nor in the DLQ, nor under a
    still-valid lease after the drain — the «فقد صامت» of R-179."""
    accounted = set(terminal_ids) | set(dlq_ids) | set(leased_ids)
    return len(set(accepted_ids) - accounted)


def reconciliation_ratio(api_state: Dict[str, str], db_state: Dict[str, str],
                         event_state: Dict[str, str]) -> Optional[float]:
    """Fraction of task ids whose final state agrees in API, DB and events."""
    ids = set(api_state) | set(db_state) | set(event_state)
    if not ids:
        return None
    ok = sum(1 for i in ids
             if api_state.get(i) is not None
             and api_state.get(i) == db_state.get(i) == event_state.get(i))
    return ok / len(ids)


def isolation_leaks(observations: Iterable[Dict[str, Any]]) -> int:
    """Records a tenant observed that belong to another tenant."""
    return sum(1 for o in observations if o.get("owner") != o.get("observer"))


# ------------------------------------------------------------ time series

def ols_slope(ts: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    n = len(ts)
    if n < 2 or n != len(ys):
        return None
    mt, my = sum(ts) / n, sum(ys) / n
    den = sum((t - mt) ** 2 for t in ts)
    if den == 0:
        return None
    return sum((t - mt) * (y - my) for t, y in zip(ts, ys)) / den


def growth_pct_per_hour(ts_s: Sequence[float], ys: Sequence[float],
                        warmup_s: float = 0.0) -> Optional[float]:
    """OLS slope after warm-up, as % of the post-warm-up mean, per hour."""
    pts = [(t, y) for t, y in zip(ts_s, ys) if t >= warmup_s]
    if len(pts) < 2:
        return None
    slope = ols_slope([p[0] for p in pts], [p[1] for p in pts])
    mean = sum(p[1] for p in pts) / len(pts)
    if slope is None or mean == 0:
        return None
    return slope * 3600.0 / mean * 100.0


def growth_pct_total(ys: Sequence[float]) -> Optional[float]:
    """(last - first) / first, in % — for «نمو RSS خلال soak ≤ 10%»."""
    if len(ys) < 2 or ys[0] == 0:
        return None
    return (ys[-1] - ys[0]) / ys[0] * 100.0


def window_drift_pct(ts_s: Sequence[float], latencies_ms: Sequence[float],
                     window_s: float = 3600.0) -> Optional[float]:
    """p95 of the LAST window vs the FIRST window, in % (soak drift)."""
    if not ts_s:
        return None
    t0, t1 = min(ts_s), max(ts_s)
    if t1 - t0 < 2 * window_s:
        return None  # windows would overlap — not measurable
    first = [v for t, v in zip(ts_s, latencies_ms) if t < t0 + window_s]
    last = [v for t, v in zip(ts_s, latencies_ms) if t >= t1 - window_s]
    a, b = p95_ms(first), p95_ms(last)
    if not a or b is None:
        return None
    return (b - a) / a * 100.0


def monotonic_growth_fraction(ys: Sequence[float]) -> Optional[float]:
    """Fraction of consecutive steps that strictly increase (fd leak signal).
    A non-leaking fd series oscillates: the fraction stays well below 1."""
    if len(ys) < 3:
        return None
    steps = list(zip(ys, ys[1:]))
    return sum(1 for a, b in steps if b > a) / len(steps)


def recovery_time_s(fail_at_s: float, samples: Sequence[Dict[str, Any]],
                    ok_status: int = 200) -> Optional[float]:
    """RTO: first time after ``fail_at_s`` from which readiness stays OK for
    the rest of the samples (a single green blip is not a recovery)."""
    after = [s for s in samples if s["t"] >= fail_at_s]
    for i, s in enumerate(after):
        if all(x["status"] == ok_status for x in after[i:]):
            return s["t"] - fail_at_s
    return None


def retry_after_respected(retries: Iterable[Dict[str, Any]]) -> Optional[float]:
    """Fraction of client re-sends that waited at least the Retry-After."""
    rs = list(retries)
    if not rs:
        return None
    return sum(1 for r in rs if r["waited_s"] + 1e-9 >= r["retry_after_s"]) / len(rs)


def bytes_per_request(total_log_bytes: float, requests: int) -> Optional[float]:
    return None if requests <= 0 else total_log_bytes / requests


def seconds(value: float) -> float:
    """Identity for already-measured durations (creation time, rollback time)."""
    return float(value)


def count(value: float) -> float:
    return float(value)


METRICS: Dict[str, Callable[..., Any]] = {
    f.__name__: f for f in (
        p95_ms, p99_ms, error_rate, unstructured_refusals, jain_index, max_min_ratio,
        duplicate_side_effects, silent_loss, reconciliation_ratio, isolation_leaks,
        growth_pct_per_hour, growth_pct_total, window_drift_pct,
        monotonic_growth_fraction, recovery_time_s, retry_after_respected,
        bytes_per_request, seconds, count)
}

OPS: Dict[str, Callable[[float, float], bool]] = {
    "<": lambda v, t: v < t, "<=": lambda v, t: v <= t,
    ">": lambda v, t: v > t, ">=": lambda v, t: v >= t,
    "==": lambda v, t: v == t,
}

# metrics whose argument is a sample list that a minimum size applies to
_SAMPLED = {"p95_ms", "p99_ms", "error_rate", "window_drift_pct"}


def evaluate(criterion: Dict[str, Any], **data: Any) -> Dict[str, Any]:
    """Evaluate one criterion on raw data. Returns
    ``{"id", "value", "verdict": pass|fail|not_measured, "approved", "why"}``.
    ``approved`` mirrors the criterion's status — a ``pass`` on a proposed
    threshold is NOT acceptance."""
    cid = criterion["id"]
    fn = METRICS.get(criterion["metric"])
    out = {"id": cid, "value": None, "verdict": "not_measured",
           "approved": criterion.get("status") == "approved", "why": ""}
    if fn is None:
        out["why"] = f"unknown metric {criterion['metric']}"
        return out
    if not data:
        out["why"] = "no data"
        return out
    min_n = criterion.get("min_samples")
    if min_n and criterion["metric"] in _SAMPLED:
        first = next(iter(data.values()))
        n = len(first) if hasattr(first, "__len__") else 0
        if n < min_n:
            out["why"] = f"insufficient samples: {n} < {min_n}"
            return out
    try:
        value = fn(**data)
    except (TypeError, KeyError) as exc:
        out["why"] = f"bad data shape: {type(exc).__name__}"
        return out
    if value is None:
        out["why"] = "metric undefined on this data"
        return out
    out["value"] = value
    out["verdict"] = "pass" if OPS[criterion["op"]](value, criterion["threshold"]) else "fail"
    return out
