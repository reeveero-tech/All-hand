"""R-122 — the live-run workload covers all 20 specializations, and the defects
the five new templates exposed are fixed.

R-120 showed 5 of 20 specializations (builder, designer, data_processor,
api_integrator, self_developer — 250 agents) never received work. Adding one
template per specialization exposed, before any fix:

* builder: review/test got no code ("no code to run") — the orchestrator only
  forwarded ``tool_output.code``; builder returns a file manifest;
* test_runner: code ending in ``if __name__ == "__main__": raise SystemExit(...)``
  exited before the appended tests ran and was reported as "code ran";
* data_processor answered only "processed N samples" (no result);
* api_integrator answered only "GET <url>" (no response status).

All offline except where noted (api_integrator uses a fake opener).
"""
import asyncio
import importlib.util
from pathlib import Path

import pytest

import agent_civilization.core.agents.real_dispatcher as rd
from agent_civilization.agents.real import create_civilization
from agent_civilization.core import safety
from agent_civilization.pipeline import orchestrator
from agent_civilization.pipeline.orchestrator import execute_plan
from agent_civilization.routing.auto_router import route


def run(coro):
    return asyncio.run(coro)


def _live():
    p = Path(__file__).resolve().parent.parent / "scripts/run_r119_live_hour.py"
    spec = importlib.util.spec_from_file_location("r122_live", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_templates_reach_all_20_specializations():
    mod = _live()
    specs = set(create_civilization(1))
    assert len(specs) == 20
    used = set()
    for t in mod.REQUEST_TEMPLATES:
        plan = route(t)
        used.update(s["specialization"] for s in plan["stages"])
    assert used == specs, sorted(specs - used)


def test_new_templates_route_to_their_specialization():
    mod = _live()
    assert len(mod.EXPECTED_PRIMARY) == 5
    for t, spec in mod.EXPECTED_PRIMARY.items():
        assert t in mod.REQUEST_TEMPLATES
        assert route(t)["primary"] == spec, t


def test_builder_artifact_forwards_the_main_module():
    res = {"ok": True, "tool_output": {"files": [
        {"path": "app/__init__.py", "content": '"""marker."""\n'},
        {"path": "tests/test_main.py", "content": "def test_x():\n    assert True\n"},
        {"path": "app/main.py", "content": "def main():\n    return 0\n"},
        {"path": "README.md", "content": "# p\n"}]}}
    art = orchestrator._artifact_of(res)
    assert art == {"code": "def main():\n    return 0\n", "source_file": "app/main.py"}
    # a manifest without a Python module still forwards nothing
    assert orchestrator._artifact_of({"ok": True, "tool_output": {"files": [
        {"path": "README.md", "content": "# p\n"}]}}) is None


@pytest.mark.code_exec
def test_main_guard_no_longer_skips_the_appended_tests():
    code = ('def main() -> int:\n    return 0\n\n\n'
            'if __name__ == "__main__":\n    raise SystemExit(main())\n')
    out = run(rd._test_runner({"artifact": {"code": code}}))
    assert out["ok"], out
    assert "1 test(s) passed, 0 failed" in out["answer"], out["answer"]


def test_drop_main_guard_keeps_everything_else():
    code = 'import os\n\nX = 1\n\nif __name__ == "__main__":\n    print(X)\n    raise SystemExit(0)\n\ny = 2\n'
    out = rd._drop_main_guard(code)
    assert "__main__" not in out and "X = 1" in out and "y = 2" in out and "import os" in out
    assert rd._drop_main_guard("def f(:\n") == "def f(:\n"  # syntax error: unchanged


def test_data_processor_answer_states_the_aggregates():
    out = run(rd._data_processor({"prompt": "aggregate these sales figures: [120, 80, 95, 300, 150]"}))
    assert out["ok"]
    assert "sum=745" in out["answer"] and "median=120" in out["answer"], out["answer"]


def test_api_integrator_answer_states_the_real_response(monkeypatch):
    class _Resp:
        status = 200
        headers = {"content-type": "application/json"}

        def read(self, n):
            return b'{"ok": true}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(safety, "safe_urlopen", lambda req, timeout=8, allow_private=None: _Resp())
    out = run(rd._api_integrator({"prompt": "integrate with the public endpoint https://example.com"}))
    assert out["ok"], out
    assert "-> HTTP 200 (application/json, 12 bytes," in out["answer"], out["answer"]


def test_truth_checks_reject_the_hollow_outputs_found_before_the_fix():
    mod = _live()
    hollow = {
        "scaffold a starter project for a todo web service": [
            {"name": "execute", "ok": True, "output": "scaffolded 3 file(s) (python-app); 3/3 passed verification"},
            {"name": "review", "ok": True, "output": "text metrics only (no code to review): 18 words"},
            {"name": "test", "ok": True, "output": "exit=0; code ran (no test functions found to run)"}],
        "aggregate these sales figures: [120, 80, 95, 300, 150]": [
            {"name": "execute", "ok": True, "output": "processed 5 samples"},
            {"name": "validate", "ok": True, "output": "analysis integrity: 4/4 checks passed"}],
        "integrate with the public endpoint https://example.com": [
            {"name": "execute", "ok": True, "output": "GET https://example.com"},
            {"name": "probe", "ok": True, "output": "HEAD https://example.com -> HTTP 200 in 70 ms"}],
    }
    for req, stages in hollow.items():
        assert mod.verify(req, stages)[0] is False, req


@pytest.mark.code_exec
@pytest.mark.parametrize("template", [
    "scaffold a starter project for a todo web service",
    "pick a dark mode color palette from #3366ff for the dashboard",
    "aggregate these sales figures: [120, 80, 95, 300, 150]",
    "self improve: inspect your own specializations and evolve",
])
def test_offline_templates_pass_their_truth_check_end_to_end(template):
    mod = _live()
    civ = create_civilization(2)
    res = run(execute_plan(route(template), civ))
    stages = [{"name": s.get("name"), "ok": s.get("ok"), "output": str(s.get("output"))} for s in res["stages"]]
    assert all(s["ok"] for s in stages), stages
    assert mod.verify(template, stages)[0] is True, stages
