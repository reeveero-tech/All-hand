#!/usr/bin/env python3
"""R-055: تشغيل السيناريوهات الشاملة الثلاثة وطباعة تقرير موثق.

يشغل السيناريوهات الثلاثة من tests/test_e2e_scenarios.py عبر المسار الحقيقي
(route → execute_plan عبر create_civilization) ويطبع تقريراً يُحفظ في
docs/audit/M6/logs/. لا يعتمد على pytest — تشغيل مستقل قابل للتكرار.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_civilization.agents.real import create_civilization
from agent_civilization.pipeline.orchestrator import execute_plan
from agent_civilization.routing.auto_router import route


async def run_scenario(name, goal, data):
    civ = create_civilization(5)
    plan = route(goal)
    result = await execute_plan(plan, civ, data=data)
    print(f"\n=== السيناريو: {name} ===")
    print(f"الهدف: {goal}")
    print(f"التخصص الأساسي: {plan['primary']}")
    print(f"عدد المراحل: {len(plan['stages'])}")
    for stage in result["stages"]:
        ok_mark = "✓" if stage["ok"] else "✗"
        print(f"  {ok_mark} {stage['name']}@{stage['specialization']} ({stage['agent']}) {stage['elapsed_s']:.3f}s")
        print(f"     {stage['output'][:100]}")
    print(f"النتيجة النهائية: {result['final'][:120]}")
    print(f"الزمن الكلي: {result['elapsed_s']:.3f}s")
    return result


async def main():
    r1 = await run_scenario("1: برمجي — مراجعة واختبار كود",
                            "review and test this code: print(1+1)", "print(1+1)")
    r2 = await run_scenario("2: تحليلي — تحليل بيانات",
                            "analyze the data for trends", [1, 2, 3, 4, 5])
    r3 = await run_scenario("3: بناء معماري — هيكل مشروع",
                            "build a Python project structure", 'print("hello")')

    # التحقق النهائي: كل السيناريوهات نجحت
    all_ok = all(s["ok"] for r in (r1, r2, r3) for s in r["stages"])
    all_final = all(r["final"] for r in (r1, r2, r3))
    print("\n" + "=" * 50)
    print(f"كل المراحل ناجحة: {all_ok}")
    print(f"كل النتائج النهائية موجودة: {all_final}")
    if not (all_ok and all_final):
        sys.exit(1)
    print("✓ السيناريوهات الثلاثة ناجحة")


if __name__ == "__main__":
    asyncio.run(main())
