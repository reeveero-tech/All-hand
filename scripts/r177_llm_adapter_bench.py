#!/usr/bin/env python3
"""R-177 دفعة 3 (D-134): قياس مهايئ LLM المفتاحي مباشرة ضد مزود وهمي محلي.

أمانة التسمية: هذا **ليس** مزودًا حيًا. المزود عملية OS منفصلة على
127.0.0.1 تحاكي واجهة OpenAI (‎/v1/chat/completions) بزمن وأخطاء مضبوطة.
المقيس هو شيفرة المهايئ الحقيقية نفسها (agent_civilization/core/llm.py —
LLMClient.complete: حد التزامن KEYED_CONCURRENCY، قاطع الدائرة `llm`،
احترام Retry-After، عقد النتيجة) — لا زمن مزود حقيقي ولا سلوك حدود معدل حقيقي.

لماذا ليس عبر /api/chat؟ قياس السجل 366 أثبت أن مزيج R-131 يُوجَّه
بالكلمات المفتاحية ويُجاب بأدوات محلية: صفر استدعاءات LLM (لا موافقة
allow_public_llm، وعدّادات قاطع llm صفر) — فادعاء «api-chat يغطي المهايئ»
كان غير دقيق ويُصحح في services.json.

الملامح (كلها على المزود الوهمي، آمنة على البيئة المشتركة):
  baseline          10 طلب/ث × 20s، زمن المزود 50ms
  normal            40 طلب/ث × 30s، زمن المزود 150ms (≈6 متزامنة < 8)
  failure-recovery  10 طلب/ث: سليم 5s ⇒ 500 لمدة 10s ⇒ سليم 15s،
                    ثم 429+Retry-After=2 لمدة 3s ⇒ سليم — يقيس فتح القاطع،
                    الرفض السريع دون إصابة المزود، والتعافي.

الاستعمال:
  python scripts/r177_llm_adapter_bench.py --profile all --out DIR
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import socket
import subprocess
import sys
import time
import urllib.request
from typing import Any, Dict, List

REPO = pathlib.Path(__file__).resolve().parent.parent

MOCK_SRC = r'''
import json, sys, threading, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
STATE = {"mode": "ok", "delay_ms": 50, "retry_after": 2, "calls": 0, "calls_by_mode": {}}
LOCK = threading.Lock()
class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def _send(self, code, body, headers=None):
        b = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        with LOCK: self._send(200, dict(STATE))
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        if self.path == "/__mode":
            with LOCK: STATE.update(json.loads(raw or b"{}"))
            return self._send(200, dict(STATE))
        with LOCK:
            mode, delay, ra = STATE["mode"], STATE["delay_ms"], STATE["retry_after"]
            STATE["calls"] += 1
            STATE["calls_by_mode"][mode] = STATE["calls_by_mode"].get(mode, 0) + 1
        time.sleep(delay / 1000.0)
        if mode == "error500":
            return self._send(500, {"error": "mock provider failure"})
        if mode == "429":
            return self._send(429, {"error": "mock rate limit"}, {"Retry-After": str(ra)})
        return self._send(200, {"id": "mock", "model": "mock-provider",
            "choices": [{"index": 0, "message": {"role": "assistant",
                         "content": "mock answer"}, "finish_reason": "stop"}]})
srv = ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H)
srv.daemon_threads = True
srv.serve_forever()
'''


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class MockProvider:
    """مزود وهمي في عملية OS منفصلة (فصل المحمول عن المزود)."""

    def __init__(self):
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.proc = subprocess.Popen([sys.executable, "-c", MOCK_SRC, str(self.port)],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                self.state()
                return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("mock provider did not start")

    def state(self) -> Dict[str, Any]:
        with urllib.request.urlopen(self.url + "/__state", timeout=3) as r:
            return json.loads(r.read())

    def set(self, **kw) -> None:
        req = urllib.request.Request(self.url + "/__mode", method="POST",
                                     data=json.dumps(kw).encode(),
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=3).read()

    def close(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def _state(brk) -> str:
    st = brk.state
    return st() if callable(st) else st


def _pct(vals: List[float], q: float):
    if not vals:
        return None
    s = sorted(vals)
    import math
    return round(s[max(1, math.ceil(q * len(s))) - 1], 3)


async def _drive(client, rate: float, duration_s: float, timeline=None,
                 t0: float = None) -> List[Dict[str, Any]]:
    """نموذج مفتوح: طلب كل 1/rate ثانية بغض النظر عن الردود (لا تنسيق مغلق)."""
    from agent_civilization.core import backpressure
    out: List[Dict[str, Any]] = []
    t0 = t0 or time.perf_counter()
    tasks = []

    async def one(i: int, t_sched: float):
        ts = time.perf_counter()
        res = await client.complete("system", f"bench prompt {i}", max_tokens=16,
                                    max_wait_s=5.0)
        te = time.perf_counter()
        out.append({"i": i, "t_s": round(ts - t0, 3), "ms": round((te - ts) * 1000, 3),
                    "ok": bool(res.get("ok")), "busy": bool(res.get("busy")),
                    "error": (res.get("error") or "")[:60],
                    "breaker": _state(backpressure.breaker("llm"))})

    n = int(rate * duration_s)
    for i in range(n):
        target = t0 + i / rate
        delay = target - time.perf_counter()
        if delay > 0:
            await asyncio.sleep(delay)
        if timeline:
            timeline(time.perf_counter() - t0)
        tasks.append(asyncio.create_task(one(i, target)))
    await asyncio.gather(*tasks)
    return sorted(out, key=lambda d: d["i"])


def _summ(samples: List[Dict[str, Any]], provider_delay_ms: float) -> Dict[str, Any]:
    oks = [s["ms"] for s in samples if s["ok"]]
    return {"requests": len(samples), "ok": sum(1 for s in samples if s["ok"]),
            "failed": sum(1 for s in samples if not s["ok"]),
            "busy": sum(1 for s in samples if s["busy"]),
            "p50_ms": _pct(oks, 0.5), "p95_ms": _pct(oks, 0.95), "p99_ms": _pct(oks, 0.99),
            "max_ms": max(oks) if oks else None,
            "provider_delay_ms": provider_delay_ms,
            "adapter_overhead_p95_ms": (None if not oks else
                                        round(_pct(oks, 0.95) - provider_delay_ms, 3))}


async def _profile(name: str, mock: MockProvider) -> Dict[str, Any]:
    from agent_civilization.core import backpressure
    from agent_civilization.core.llm import LLMClient, KEYED_CONCURRENCY
    backpressure.reset_all() if hasattr(backpressure, "reset_all") else None
    client = LLMClient(api_key="mock-not-a-real-key",
                       url=mock.url + "/v1/chat/completions", model="mock-model")
    try:
        if name == "baseline":
            mock.set(mode="ok", delay_ms=50)
            calls0 = mock.state()["calls"]
            s = await _drive(client, 10, 20)
            rep = _summ(s, 50)
            rep["provider_calls"] = mock.state()["calls"] - calls0
            rep["ok_gate"] = rep["failed"] == 0 and rep["provider_calls"] == rep["requests"]
            return {"profile": name, **rep, "keyed_concurrency": KEYED_CONCURRENCY}
        if name == "normal":
            mock.set(mode="ok", delay_ms=150)
            calls0 = mock.state()["calls"]
            s = await _drive(client, 40, 30)
            rep = _summ(s, 150)
            rep["provider_calls"] = mock.state()["calls"] - calls0
            rep["ok_gate"] = rep["failed"] == 0
            return {"profile": name, **rep, "keyed_concurrency": KEYED_CONCURRENCY}
        if name == "failure-recovery":
            mock.set(mode="ok", delay_ms=30)
            phases: List[Dict[str, Any]] = []
            plan = [(0.0, "ok"), (5.0, "error500"), (15.0, "ok"),
                    (30.0, "429"), (33.0, "ok")]
            idx = {"i": 0}

            def timeline(t: float):
                while idx["i"] < len(plan) and t >= plan[idx["i"]][0]:
                    at, mode = plan[idx["i"]]
                    st = mock.state()
                    phases.append({"at_s": at, "mode": mode, "provider_calls_before": st["calls"]})
                    mock.set(mode=mode)
                    idx["i"] += 1
            t0 = time.perf_counter()
            s = await _drive(client, 10, 40, timeline=timeline, t0=t0)
            st = mock.state()

            def window(a, b):
                return [x for x in s if a <= x["t_s"] < b]
            fail_w = window(5.0, 15.0)
            calls_fail = phases[2]["provider_calls_before"] - phases[1]["provider_calls_before"]
            rec = window(15.0, 30.0)
            first_ok = next((x for x in rec if x["ok"]), None)
            hold = window(30.0, 33.0)
            calls_hold = phases[4]["provider_calls_before"] - phases[3]["provider_calls_before"]
            after = window(33.0, 40.0)
            first_ok_after_hold = next((x for x in after if x["ok"]), None)
            rep = {
                "profile": name,
                "breaker_threshold_reset": [os.environ.get("AGOS_BREAKER_LLM_THRESHOLD", "5(default)"),
                                            os.environ.get("AGOS_BREAKER_LLM_RESET_S", "30(default)")],
                "outage_500": {
                    "requests": len(fail_w), "failed": sum(1 for x in fail_w if not x["ok"]),
                    "provider_calls_during_outage": calls_fail,
                    "fast_failed_without_provider_call": len(fail_w) - calls_fail,
                    "fast_fail_p95_ms": _pct([x["ms"] for x in fail_w[10:]], 0.95),
                },
                "recovery": {
                    "first_success_after_provider_ok_s": (None if not first_ok else
                                                          round(first_ok["t_s"] - 15.0, 3)),
                    "ok_in_last_5s": sum(1 for x in window(25.0, 30.0) if x["ok"]),
                    "requests_in_last_5s": len(window(25.0, 30.0)),
                },
                "retry_after_hold": {
                    "requests": len(hold), "provider_calls_during_hold": calls_hold,
                    "first_success_after_hold_s": (None if not first_ok_after_hold else
                                                   round(first_ok_after_hold["t_s"] - 33.0, 3)),
                },
                "provider_calls_total": st["calls"], "calls_by_mode": st["calls_by_mode"],
                "phases": phases,
            }
            r = rep["recovery"]
            rep["ok_gate"] = bool(
                rep["outage_500"]["fast_failed_without_provider_call"] > 0
                and r["first_success_after_provider_ok_s"] is not None
                and r["ok_in_last_5s"] == r["requests_in_last_5s"]
                and rep["retry_after_hold"]["provider_calls_during_hold"]
                < rep["retry_after_hold"]["requests"])
            rep["samples"] = s
            return rep
        raise SystemExit(f"unknown profile {name}")
    finally:
        await client.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="all",
                    choices=["baseline", "normal", "failure-recovery", "all"])
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    # التعافي السريع للقياس: فترة فتح القاطع 3s (الافتراضي 30s) — معلن في التقرير
    os.environ.setdefault("AGOS_BREAKER_LLM_RESET_S", "3")
    sys.path.insert(0, str(REPO))
    profiles = (["baseline", "normal", "failure-recovery"] if args.profile == "all"
                else [args.profile])
    mock = MockProvider()
    results: Dict[str, Any] = {"provider": "MOCK (local loopback, NOT a live provider)",
                               "adapter": "agent_civilization/core/llm.py LLMClient (keyed path)",
                               "profiles": {}}
    try:
        for p in profiles:
            results["profiles"][p] = asyncio.run(_profile(p, mock))
            r = results["profiles"][p]
            print(f"[llm-adapter:{p}] ok_gate={r['ok_gate']}", flush=True)
    finally:
        mock.close()
    results["ok"] = all(r["ok_gate"] for r in results["profiles"].values())
    if args.out:
        out = pathlib.Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "llm_adapter_bench.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    brief = {p: {k: v for k, v in r.items() if k != "samples"}
             for p, r in results["profiles"].items()}
    print(json.dumps({"ok": results["ok"], "profiles": brief}, ensure_ascii=False, indent=1))
    return 0 if results["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
