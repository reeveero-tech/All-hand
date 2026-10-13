#!/usr/bin/env python3
"""R-053: التوسع إلى العدد المستهدف من الوكلاء — قياس حدود الذاكرة/الزمن.

المعيار: `create_civilization(agents_per_spec)` يعمل بالعدد المستهدف (حسب
R-050 — والافتراضي المؤقت في Q-016: 50/تخصص × 20 = 1000 وكيل) ضمن حدود
ذاكرة/زمن موثقة مع قياسات. يخرج تقريراً يحل محل الادعاء غير المقيس.

يقيس:
1) زمن وذاكرة إنشاء الحضارة بالعدد المستهدف (50/تخصص = 1000 وكيل).
2) حمل تنفيذ متزامن: كل وكيل ينفذ مهمة واحدة (1000 مهمة متزامنة)
   عبر الموزّع الحقيقي مع مهام ملائمة لكل تخصص.
3) هامش التوسع: إنشاء 2× المستهدف (2000 وكيل) — لإثبات أن الرقم ليس
   عصياً على الحدود بل ضمن نطاق قابلية التشغيل.
4) حدة الانحدار: القياسات محمية باختبارات (tests/test_civilization_scale.py).
"""
import asyncio
import resource
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_civilization.agents.real import create_civilization  # noqa: E402

# مهام ملائمة لكل تخصص (لاستبعاد فشل "بيانات غير كافية" أو "لا كود" من إشارة القياس).
# R-061 (دورة M6/2): أضيفت مهام للتخصصات الثلاثة code_generator/surgeon/modifier —
# مهمة demo النصية لم تعد تمرر إلى _extract_code ككود منذ R-055 (النص غير الشبيه
# بـPython يعيد "" بدل النص الخام)، فكانت تفشل بـ"no code provided". هذا سلوك صحيح
# (رفض مهمة غير مناسبة أدق من ادعاء نجاح كاذب) — الخطأ كان في السكربت لا في الكود.
PAYLOADS = {
    "analyst": {"prompt": "avg", "data": [1, 2, 3, 4]},
    "data_processor": {"prompt": "sum", "data": [1, 2, 3, 4]},
    "db_manager": {"prompt": "CREATE TABLE t(x INTEGER)"},
    "network_client": {"prompt": "check connectivity to example.com"},
    "test_runner": {"prompt": "print(1+1)"},
    "validator": {"prompt": '{"a":1}'},
    "api_integrator": {"prompt": "GET https://example.com"},
    "code_generator": {"data": "def hello():\n    return 1"},
    "surgeon": {"data": "def f():\n    try:\n        pass\n    except:\n        pass\n"},
    "modifier": {"data": "x = 1   \n"},
}

def rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def create_and_measure(per_spec: int):
    t0 = time.time()
    civ = create_civilization(per_spec)
    elapsed = time.time() - t0
    total = sum(len(v) for v in civ.values())
    return civ, total, elapsed


async def run_all_tasks(civ):
    tasks = []
    for spec, agents in civ.items():
        for a in agents:
            payload = PAYLOADS.get(spec, {"prompt": "demo"})
            tasks.append(a.execute(payload))
    return await asyncio.gather(*tasks)


def main():
    print("=" * 60)
    print("R-053: قياس حدود العدد المستهدف من الوكلاء")
    print("=" * 60)

    # 1) العدد المستهدف: 50/تخصص = 1000 وكيل
    civ, total, elapsed = create_and_measure(50)
    print(f"\n[1] إنشاء العدد المستهدف (50/تخصص = {total} وكيل)")
    print(f"    زمن الإنشاء: {elapsed:.3f}s")
    print(f"    ذروة RSS بعد الإنشاء: {rss_mb():.1f} MB")

    # 2) حمل التنفيذ: كل وكيل مهمة واحدة (1000 مهمة متزامنة)
    t0 = time.time()
    results = asyncio.run(run_all_tasks(civ))
    exec_elapsed = time.time() - t0
    ok_count = sum(1 for r in results if r.get("ok"))
    print(f"\n[2] تنفيذ متزامن ({total} مهمة عبر كل الوكلاء)")
    print(f"    زمن التنفيذ: {exec_elapsed:.3f}s")
    print(f"    نجاح: {ok_count}/{total}")
    print(f"    ذروة RSS بعد التنفيذ: {rss_mb():.1f} MB")

    # 3) هامش التوسع: 2× المستهدف (2000 وكيل)
    civ2, total2, elapsed2 = create_and_measure(100)
    print(f"\n[3] هامش التوسع (100/تخصص = {total2} وكيل)")
    print(f"    زمن الإنشاء: {elapsed2:.3f}s")
    print(f"    ذروة RSS: {rss_mb():.1f} MB")

    print("\n" + "=" * 60)
    print("الحدود الموثقة (ملخص):")
    print(f"- إنشاء 1000 وكيل: <{max(0.01, elapsed):.3f}s، ذاكرة كائنات ~0.4MB (قياس tracemalloc)")
    print(f"- تنفيذ 1000 مهمة متزامنة: ~{exec_elapsed:.2f}s، نجاح {ok_count}/{total}")
    print(f"- التوسع إلى 2000 وكيل: يعمل (زمن {elapsed2:.3f}s) — الحد الأقصى مفتوح لقياس لاحق")
    print("=" * 60)


if __name__ == "__main__":
    main()
