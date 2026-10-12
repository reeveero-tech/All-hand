"""اختبارات R-051: عينة تمثيلية من القياس الفعلي لكل تخصص.

سجلات القياس الكاملة (التخصصات الـ20) تُحفظ كأدلة في docs/audit/M5/ —
هذه الاختبارات تحمي أصلا عينة صغيرة من التخصصات من الانحدار (dispatch حي
يعيد مخرجات محسوبة فعلية).
"""
import pytest

from agent_civilization.core.agents import real_dispatcher as rd


@pytest.mark.asyncio
async def test_all_20_specializations_have_real_handlers():
    cov = rd.coverage()
    assert cov["count"] == 20
    assert len(cov["real_specializations"]) == 20


@pytest.mark.asyncio
@pytest.mark.parametrize("spec,task,expected_key", [
    ("analyst", {"data": [1, 2, 3, 4]}, "mean"),
    ("db_manager", {"prompt": "SELECT 1 AS one;"}, "rows"),
    ("code_generator", {"data": "def add(a,b):\n    return a+b\n"}, "functions"),
    ("monitor", {"prompt": "check system"}, "loadavg"),
])
async def test_sample_dispatch_returns_computed_output(spec, task, expected_key):
    res = await rd.dispatch(spec, task)
    assert res is not None and res.get("ok"), res
    tool = res.get("tool_output") or {}
    assert expected_key in tool or expected_key in (res.get("answer") or "")


@pytest.mark.asyncio
async def test_db_manager_executes_real_sql():
    res = await rd.dispatch("db_manager", {
        "prompt": "CREATE TABLE t(x INTEGER); INSERT INTO t VALUES (42); SELECT * FROM t;"
    })
    assert res["ok"]
    assert (42,) in res["tool_output"]["rows"]
