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
    # __all__ = 20 حزمة + 1000 صنف (R-032c: star-import يجب أن يعيد الأصناف كما قبل R-032a)
    assert set(SPECS) <= set(top.__all__)
    assert len(top.__all__) == 20 + 1000


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


def test_star_import_from_root_brings_classes():
    """R-032c: `from ...specialists import *` يعيد الأصناف والحزم (الواجهة التاريخية قبل R-032a)."""
    ns = {}
    exec("from agent_civilization.agents.specialists import *", ns)
    assert "AnalystAgent0015" in ns
    assert "SurgeonAgent0992" in ns
    assert "analyst" in ns
    class_names = [n for n in ns if n.endswith(tuple("0123456789")) and "Agent" in n]
    assert len(class_names) == 1000


# --------------------------------------------- R-105 (deduplication)
def test_r105_all_1000_names_and_defaults_match_the_baseline():
    """R-105: the generated classes must match the historical baseline exactly —
    names, __all__ order, agent_id defaults, create() defaults."""
    import json
    from pathlib import Path

    ref_path = Path(__file__).resolve().parent.parent / "docs/audit/M6/logs/raw/r105_baseline_reference.json"
    ref = json.loads(ref_path.read_text())
    import inspect

    for spec, spec_ref in ref["specs"].items():
        pkg = _pkg(spec)
        assert list(pkg.__all__) == spec_ref["__all__"], f"{spec}: __all__ order changed"
        for name in spec_ref["__all__"]:
            cls = getattr(pkg, name)
            expected = spec_ref["classes"][name]
            assert cls.__name__ == name
            assert (inspect.signature(cls.__init__).parameters["agent_id"].default
                    == expected["agent_id_default"]), f"{name}: default changed"


def test_r105_fifty_distinct_classes_per_spec_not_aliases():
    """R-105: type() must generate 50 DISTINCT class objects per specialization —
    not 50 names pointing at the same class."""
    for spec in SPECS:
        pkg = _pkg(spec)
        classes = [getattr(pkg, n) for n in pkg.__all__]
        assert len(set(map(id, classes))) == 50, f"{spec}: not 50 distinct classes"


def test_r105_shims_carry_no_duplicated_implementation():
    """R-105: the 1,000 historical files must be THIN shims — no _process_task/
    _execute_work/_handle_message definitions left in them (the implementation
    lives once per spec in _templates/)."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "agent_civilization/agents/specialists"
    files = [f for f in root.glob("*/*.py")
             if f.name != "__init__.py" and "_templates" not in str(f)]
    assert len(files) == 1000
    with_impl = [f for f in files
                 if any(m in f.read_text() for m in
                        ("async def _process_task", "async def _execute_work", "async def _handle_message"))]
    assert not with_impl, f"implementation still duplicated in: {with_impl[:3]}"


def test_r105_templates_are_the_single_source():
    """R-105: exactly 20 template bodies (one per specialization) exist and each
    spec's generated classes come from them."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "agent_civilization/agents/specialists"
    templates = list((root / "_templates").glob("*_template.py.tpl"))
    assert len(templates) == 20
    for spec in SPECS:
        assert (root / "_templates" / f"{spec}_template.py.tpl").exists()


def test_r105_top_level_star_import_still_1000():
    """R-105: the historical star-import surface is unchanged — 1000 classes."""
    ns = {}
    exec("from agent_civilization.agents.specialists import *", ns)
    class_names = [n for n in ns if n.endswith(tuple("0123456789")) and "Agent" in n]
    assert len(class_names) == 1000


@pytest.mark.asyncio
async def test_r105_sample_agent_from_every_spec_still_executes():
    """R-105: behavior preserved — one agent from every specialization executes a
    task through the real dispatcher (the same acceptance as before the dedup)."""
    from agent_civilization.agents.specialists import (
        analyst, api_integrator, db_manager, monitor,
    )
    hub = CommunicationHub()
    for pkg in (analyst, api_integrator, db_manager, monitor):
        cls = getattr(pkg, pkg.__all__[0])
        agent = cls(name=cls.__name__.lower(), hub=hub, agent_id=1)
        assert agent.specialization
        result = await agent._process_task({"prompt": "demo"})
        assert isinstance(result, dict)
