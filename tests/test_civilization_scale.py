"""R-053: حماية من انحدار قياسات التوسع.

لا تتحقق من أرقام مطلقة (تعتمد على الأجهزة) بل من الحدود النوعية الموثقة:
- إنشاء الحضارة بالعدد المستهدف (50/تخصص) يعطي 1000 وكيل.
- التوسع إلى 2× المستهدف يعمل (2000 وكيل).
- كل وكيل قابل للتنفيذ عبر الموزّع الحقيقي بمهمة ملائمة.
- الحد الأقصى للحضارة مفتوح (لا يوجد سقف صلب في create_civilization).
"""
import pytest
import time

from agent_civilization.agents.real import create_civilization, SPECIALIZATIONS


def test_target_is_1000_agents():
    """العدد المستهدف (الافتراضي المؤقت في Q-016): 50/تخصص × 20 = 1000 وكيل."""
    civ = create_civilization(50)
    total = sum(len(v) for v in civ.values())
    assert total == 1000
    assert len(civ) == len(SPECIALIZATIONS) == 20


def test_double_target_is_2000_agents():
    """هامش التوسع: 2× المستهدف يعمل دون خطأ."""
    civ = create_civilization(100)
    total = sum(len(v) for v in civ.values())
    assert total == 2000


def test_creation_time_within_bounds():
    """حد زمني نوعي: إنشاء 1000 وكيل يجب أن يكون سريعاً (<5s) — راجع الأدلة."""
    t0 = time.time()
    create_civilization(50)
    elapsed = time.time() - t0
    assert elapsed < 5.0


@pytest.mark.code_exec  # R-100: proves real test_runner execution (operator opt-in)
def test_agents_execute_real_dispatch():
    """كل تخصص لديه وكيل قابل للتنفيذ عبر الموزّع الحقيقي (نموذج لكل تخصص)."""
    import asyncio

    PAYLOADS = {
        "analyst": {"prompt": "avg", "data": [1, 2, 3, 4]},
        "data_processor": {"prompt": "sum", "data": [1, 2, 3, 4]},
        "db_manager": {"prompt": "CREATE TABLE t(x INTEGER)"},
        "test_runner": {"prompt": "print(1+1)"},
        "validator": {"prompt": '{"a":1}'},
        "code_generator": {"prompt": "generate code", "data": "def add(a,b):\n    return a+b"},
        "modifier": {"prompt": "normalize", "data": "import os\nprint( os.getcwd() )"},
        "surgeon": {"prompt": "diagnose", "data": "def f( x ):\n    return  x"},
        "api_integrator": {"prompt": "GET https://example.com"},
        "network_client": {"prompt": "check connectivity to example.com"},
    }
    civ = create_civilization(2)  # حضارة صغيرة للاختبار

    async def run():
        results = []
        for spec, agents in civ.items():
            payload = PAYLOADS.get(spec, {"prompt": "demo"})
            a = agents[0]
            r = await a.execute(payload)
            results.append((spec, r))
        return results

    for spec, r in asyncio.run(run()):
        assert r.get("ok"), f"{spec}: {r.get('result', {}).get('error')}"


def test_no_hard_cap_on_civilization_size():
    """لا يوجد سقف صلب في create_civilization — الحد الأقصى حسب الموارد."""
    civ = create_civilization(500)  # 10,000 وكيل
    total = sum(len(v) for v in civ.values())
    assert total == 10_000
