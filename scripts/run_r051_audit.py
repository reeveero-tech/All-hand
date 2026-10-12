#!/usr/bin/env python3
"""R-051: قياس التنفيذ الفعلي لكل تخصص.

يشغل مهمة تمثيلية لكل تخصص من التخصصات الـ20 عبر الموزّع الحقيقي، ويسجل
لكل تخصص: حقيقي / محاكى / فاشل، ويخرج جدولاً يحل محل ادعاء INTEGRITY-REPORT.
"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_civilization.core.agents import real_dispatcher as rd  # noqa: E402

# مهام تمثيلية. آمنة دون اتصال حيث أمكن لقياس قدرة التوجيه لا الاتصال
# الخارجي. استجابة 404 من example.com تثبت أن المعالج ينفذ طلب HTTP حياً
# (لبيئة التشغيل خروج شبكي)؛ فحص DNS فقط مستخدم لـnetwork_client.
TASKS = {
    "analyst": {"prompt": "analyze", "data": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]},
    "api_integrator": {"prompt": "GET https://api.github.com/repos/python/cpython"},
    "architect": {"prompt": "design system", "data": ["web ui", "api", "db"]},
    "builder": {"prompt": "scaffold", "data": ["app.py", "utils.py", "tests/test_app.py"]},
    "code_generator": {"prompt": "generate code", "data": "def add(a,b):\n    return a+b\n"},
    "communicator": {"prompt": "draft message", "data": "hello team, meeting at 3"},
    "data_processor": {"prompt": "process", "data": [1, 2, 3, 4, 5, 6]},
    "db_manager": {"prompt": "CREATE TABLE t(x INTEGER); INSERT INTO t VALUES (1); SELECT * FROM t;"},
    "designer": {"prompt": "palette", "data": "brand: forest"},
    "educator": {"prompt": "explain recursion"},
    "modifier": {"prompt": "rename", "data": "def foo():\n    pass\n"},
    "monitor": {"prompt": "check system"},
    "network_client": {"prompt": "check connectivity to github.com"},
    "researcher": {"prompt": "Python programming language"},
    "reviewer": {"prompt": "review", "data": "def f(x):\n    return x*2\n"},
    "self_developer": {"prompt": "coverage report"},
    "strategist": {"prompt": "plan", "data": ["a", "b", "c"]},
    "surgeon": {"prompt": "fix code", "data": "x=1\n\n\n\ny=2\n"},
    "test_runner": {"prompt": "test", "data": "assert 1+1 == 2\n"},
    "validator": {"prompt": "validate", "data": {"type": "json", "value": [1, 2, 3]}},
}


def classify(res):
    """حقيقي = معالج حقيقي اشتغل وأعاد مخرجات محسوبة."""
    if res is None:
        return "simulated", "no handler — unmapped specialization"
    if not res.get("ok"):
        return "failed", str(res.get("error", "unknown"))[:200]
    return "real", json.dumps(res.get("tool_output") or res.get("answer") or {},
                              ensure_ascii=False)[:160]


async def main():
    cov = rd.coverage()
    rows = []
    for spec in sorted(TASKS):
        t0 = time.monotonic()
        try:
            res = await rd.dispatch(spec, TASKS[spec])
        except Exception as e:  # noqa: BLE001
            res = {"ok": False, "error": f"dispatch raised {type(e).__name__}: {e}"}
        ms = round((time.monotonic() - t0) * 1000, 1)
        status, detail = classify(res)
        rows.append({"specialization": spec, "status": status,
                     "elapsed_ms": ms, "detail": detail})
    report = {
        "run_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "coverage_self_report": cov,
        "rows": rows,
        "summary": {
            "total": len(rows),
            "real": sum(1 for r in rows if r["status"] == "real"),
            "simulated": sum(1 for r in rows if r["status"] == "simulated"),
            "failed": sum(1 for r in rows if r["status"] == "failed"),
        },
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
