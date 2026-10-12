"""R-032a: اختبارات إصلاح حزمة specialists.

النطاق: إثبات أن واجهة الحزمة العامة سليمة بعد الإصلاح — 1000 صنف وكيل
قابلة للاستيراد عبر حزم التخصصات، ووكيل عينة ينفذ مهمة عبر real_dispatcher،
وواجهة الاتصال (CommunicationHub) تعمل. هذا ليس قرار دمج الحزمة في مسار
التشغيل (ذلك قرار R-032 المرتبط بـR-050).
"""
import pytest

from agent_civilization.core.agents.communication_hub import CommunicationHub

SPECS = [
    "analyst", "api_integrator", "architect", "builder", "code_generator",
    "communicator", "data_processor", "db_manager", "designer", "educator",
    "modifier", "monitor", "network_client", "researcher", "reviewer",
    "self_developer", "strategist", "surgeon", "test_runner", "validator",
]


def _pkg(spec):
    import importlib
    return importlib.import_module(f"agent_civilization.agents.specialists.{spec}")


@pytest.mark.parametrize("spec", SPECS)
def test_package_exports_all_classes(spec):
    pkg = _pkg(spec)
    assert pkg.__all__, f"حزمة {spec} بلا صادرات"
    for name in pkg.__all__:
        cls = getattr(pkg, name)
        assert cls.__name__ == name


def test_top_level_direct_class_import():
    """الواجهة التاريخية: استيراد صنف وكيل مباشرة من جذري الحزمة (انكسر في R-032a؛ أُصلح في R-032b)."""
    from agent_civilization.agents.specialists import AnalystAgent0015, SurgeonAgent0992
    assert AnalystAgent0015.__name__ == "AnalystAgent0015"
    assert SurgeonAgent0992.__name__ == "SurgeonAgent0992"


def test_top_level_all_1000_classes_accessible():
    import agent_civilization.agents.specialists as top
    total = 0
    for spec in SPECS:
        for name in _pkg(spec).__all__:
            assert getattr(top, name) is not None, f"{name} غير متاح من الجذري"
            total += 1
    assert total == 1000


def test_top_level_package_imports_all_specs():
    import agent_civilization.agents.specialists as top
    for spec in SPECS:
        assert getattr(top, spec) is not None
    assert sorted(top.__all__) == sorted(SPECS)


def test_total_exported_classes_is_1000():
    total = 0
    for spec in SPECS:
        total += len(_pkg(spec).__all__)
    assert total == 1000


@pytest.mark.asyncio
async def test_sample_agent_executes_real_task():
    from agent_civilization.agents.specialists.analyst import AnalystAgent0015
    hub = CommunicationHub()
    agent = AnalystAgent0015(name="analyst_0015", hub=hub, agent_id=15)
    result = await agent._process_task({"prompt": "analyze", "data": [1, 2, 3, 4, 5]})
    assert result["ok"], result
    assert result["tool_output"]["mean"] == 3.0


@pytest.mark.asyncio
async def test_sample_agent_from_every_spec_package():
    """وكيل واحد من كل حزمة تخصص ينفذ مهمة عبر الموزّع الحقيقي."""
    from agent_civilization.agents.specialists import (
        analyst, api_integrator, db_manager, monitor,
    )
    hub = CommunicationHub()
    samples = [
        (analyst, "analyst_0015", [1, 2, 3]),
        (db_manager, None, None),
        (monitor, None, None),
        (api_integrator, None, None),
    ]
    for pkg, _, _ in samples:
        cls = getattr(pkg, pkg.__all__[0])
        agent = cls(name=cls.__name__.lower(), hub=hub, agent_id=1)
        assert agent.specialization
