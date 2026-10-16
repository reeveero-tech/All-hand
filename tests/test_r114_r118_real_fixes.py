"""R-114..R-118 — the fixes for the hollow successes and failures R-113 found.

Each test pins one real behaviour: verb-first routing, least-loaded agent
distribution, artifact passing between stages, the strategist's execution plan,
analyst sum/average, validator integrity checks, db_manager schema/migration,
modifier nested-if merge with an equivalence check, test_runner fragments and
uncalled test functions, generated smoke tests, network_client host probing,
researcher failing honestly with no sources, communicator translation, and the
keyless LLM default for offline tests. All offline except the `network` tests.
"""
import asyncio
import os

import pytest

import agent_civilization.core.agents.real_dispatcher as rd
from agent_civilization.agents.real import create_civilization
from agent_civilization.core.agents import craft
from agent_civilization.pipeline import orchestrator
from agent_civilization.pipeline.orchestrator import execute_plan
from agent_civilization.routing.auto_router import route


def run(coro):
    return asyncio.run(coro)


class _FakeLLM:
    """Deterministic LLM stand-in: returns ``reply`` for every completion."""

    def __init__(self, reply):
        self.reply = reply
        self.available = True
        self.provider = "fake"
        self.calls = 0

    async def complete(self, system, prompt, max_tokens=600):
        self.calls += 1
        r = self.reply(prompt) if callable(self.reply) else self.reply
        return {"ok": True, "content": r, "model": "fake-model"}


@pytest.fixture
def fake_llm(monkeypatch):
    def install(reply):
        llm = _FakeLLM(reply)
        monkeypatch.setattr("agent_civilization.core.llm.get_llm", lambda: llm)
        return llm
    return install


# ------------------------------------------------------------ R-115 routing
@pytest.mark.parametrize("goal,primary", [
    ("explain the difference between REST and GraphQL", "educator"),
    ("translate 'hello world' to French", "communicator"),
    ("write and run tests for: def add(a, b): return a + b", "test_runner"),
    ("review this code for bugs: def f(x): return x*2", "surgeon"),
    ("refactor this to be cleaner: if a: if b: return 1", "modifier"),
    ("design a table schema for orders with line items", "db_manager"),
    ("check if example.com is reachable", "network_client"),
    ("sum these numbers: [1, 2, 3]", "analyst"),
    ("حلل أداء النظام الحالي", "analyst"),
    ("اكتب كود Python لحساب متوسط قائمة أرقام", "code_generator"),
    ("review and test this code: print(1+1)", "reviewer"),
])
def test_verb_first_routing(goal, primary):
    plan = route(goal)
    assert plan["primary"] == primary
    assert plan["goal"] == goal


def test_plan_stage_is_the_strategist_and_review_and_test_ends_with_a_test():
    plan = route("write a function that returns the nth prime")
    specs = {s["name"]: s["specialization"] for s in plan["stages"]}
    assert specs["plan"] == "strategist"
    assert route("review and test this code: print(1+1)")["stages"][-1]["specialization"] == "test_runner"


# ------------------------------------------------------------ R-114 coordination
def test_pick_spreads_work_over_the_least_loaded_agents():
    civ = create_civilization(5)
    pool = civ["analyst"]

    async def go():
        names = []
        for _ in range(10):
            a = await orchestrator._pick(pool)
            names.append(a.name)
            a.stats["tasks_completed"] = a.stats.get("tasks_completed", 0) + 1
            a.status = "idle"
        return names

    names = run(go())
    assert len(set(names)) == 5, names          # every agent got work
    assert all(names.count(n) == 2 for n in set(names))


def test_pick_never_hands_the_same_idle_agent_to_two_concurrent_plans():
    civ = create_civilization(3)
    pool = civ["analyst"]

    async def go():
        return [await orchestrator._pick(pool) for _ in range(3)]

    picked = run(go())
    assert len({a.name for a in picked}) == 3
    assert all(a.status == "working" for a in picked)


@pytest.mark.code_exec
def test_generated_code_reaches_the_test_runner(fake_llm):
    fake_llm("```python\ndef nth_prime(n):\n    c, x = 0, 1\n    while c < n:\n        x += 1\n"
             "        if all(x % d for d in range(2, int(x ** 0.5) + 1)):\n            c += 1\n    return x\n```")
    civ = create_civilization(2)
    r = run(execute_plan(route("write a function that returns the nth prime"), civ))
    stages = {s["name"]: s for s in r["stages"]}
    assert all(s["ok"] for s in r["stages"]), r["stages"]
    assert "1 test(s) passed" in stages["test"]["output"]
    assert stages["execute"]["agent"] != stages["test"]["agent"]


def test_artifact_of_extracts_code_sql_and_analysis():
    assert orchestrator._artifact_of({"ok": True, "tool_output": {"code": "x = 1"}}) == {"code": "x = 1"}
    assert orchestrator._artifact_of({"ok": True, "tool_output": {"sql": "SELECT 1"}}) == {"sql": "SELECT 1"}
    a = {"mean": 2.0, "n": 3, "samples": [1, 2, 3]}
    assert orchestrator._artifact_of({"ok": True, "tool_output": a}) == {"analysis": a}
    assert orchestrator._artifact_of({"ok": False, "tool_output": {"code": "x"}}) is None


def test_strategist_returns_the_real_execution_plan():
    plan = route("research the best practices for API pagination")
    r = run(execute_plan(plan, create_civilization(1)))
    p = next(s for s in r["stages"] if s["name"] == "plan")
    assert p["specialization"] == "strategist"
    assert "execute(researcher)" in p["output"]


@pytest.mark.code_exec
def test_failed_stage_reports_its_error_first():
    r = run(execute_plan(route("write and run tests for: def f(): raise ValueError('boom')"),
                         create_civilization(1)))
    bad = [s for s in r["stages"] if not s["ok"]]
    assert bad and bad[0]["specialization"] == "test_runner"
    assert "1 failed" in bad[0]["output"]      # the error, not a dict of partial tool output


# ------------------------------------------------------------ R-116 handlers
def test_analyst_answers_sum_and_average_from_the_goal_only():
    r = run(execute_plan(route("sum these numbers: [1, 2, 3, 4, 5]"), create_civilization(1)))
    assert all(s["ok"] for s in r["stages"])
    assert "sum=15" in next(s for s in r["stages"] if s["name"] == "execute")["output"]
    r = run(execute_plan(route("compute the average of [12, 14, 16, 18]"), create_civilization(1)))
    assert "average=15" in next(s for s in r["stages"] if s["name"] == "execute")["output"]
    v = next(s for s in r["stages"] if s["name"] == "validate")
    assert v["specialization"] == "validator" and "integrity" in v["output"]


def test_understand_stage_analyses_the_goal_not_its_boilerplate():
    out = run(rd._analyst({"stage": "understand", "goal": "check if example.com is reachable",
                           "prompt": "Analyze this user request and list ..."}))
    assert out["ok"]
    req = out["tool_output"]["requirements"]
    assert req["endpoint"] == "example.com" and req["action"] == "check"


def test_validator_detects_a_corrupted_analysis_artifact():
    good = {"analysis": {"mean": 2.0, "n": 3, "min": 1, "max": 3, "samples": [1, 2, 3]}}
    assert run(rd._validator({"prompt": "Validate", "artifact": good}))["ok"]
    bad = {"analysis": {"mean": 9.0, "n": 3, "min": 1, "max": 3, "samples": [1, 2, 3]}}
    assert run(rd._validator({"prompt": "Validate", "artifact": bad}))["ok"] is False


def test_validator_parses_json_embedded_in_a_request():
    out = run(rd._validator({"prompt": 'validate this JSON schema: {"name": "string", "age": "number"}'}))
    assert out["ok"] and "embedded" in out["answer"]


def test_db_manager_schema_executes_in_sqlite_offline():
    out = run(rd._db_manager({"prompt": "design a table schema for orders with line items",
                              "goal": "design a table schema for orders with line items"}))
    assert out["ok"], out
    assert "executed in SQLite" in out["answer"]


def test_db_manager_migration_rehearsal_has_zero_mismatches():
    out = rd._migration_rehearsal("plan a database migration for a 10M row table", sample_rows=4000, batch=1000)
    assert out["ok"], out
    assert "0 mismatches" in out["answer"]


@pytest.mark.code_exec
def test_modifier_merges_nested_ifs_and_proves_equivalence():
    out = run(rd._modifier({"prompt": "refactor this to be cleaner: if a: if b: if c: return 1",
                            "goal": "refactor this to be cleaner: if a: if b: if c: return 1"}))
    assert out["ok"], out
    assert "and" in out["tool_output"]["new_code"]
    assert "behaviour identical" in out["answer"]
    fixed, changed = craft.normalise_one_line_compounds("if a: if b: return 1")
    assert changed and "\n" in fixed
    compile("def _f(a, b):\n" + "\n".join("    " + ln for ln in fixed.splitlines()), "<t>", "exec")


@pytest.mark.code_exec
def test_test_runner_wraps_fragments_and_runs_uncalled_tests():
    out = run(rd._test_runner({"artifact": {"code": "if a and b:\n    return 1"}}))
    assert out["ok"], out
    code = "def add(a, b):\n    return a + b\n\ndef test_add():\n    assert add(2, 3) == 5\n"
    out = run(rd._test_runner({"data": code}))
    assert out["ok"] and out["tool_output"]["tests_passed"] == 1
    bad = "def add(a, b):\n    return a - b\n\ndef test_add():\n    assert add(2, 3) == 5\n"
    out = run(rd._test_runner({"data": bad}))
    assert out["ok"] is False and out["tool_output"]["tests_failed"] == 1


def test_generated_smoke_tests_cover_top_level_only_with_guessed_args():
    src = "class U:\n    def __init__(self, email):\n        self.email = email\n    def m(self):\n        return 1\n\n" \
          "def nth_prime(n):\n    return n\n"
    gen = rd._generate_basic_tests(src)
    assert "test_U_smoke" in gen and "test_nth_prime_smoke" in gen
    assert "test_m_smoke" not in gen                       # methods are not module functions
    assert "nth_prime(5)" in gen and 'U("alice@example.com")' in gen


def test_network_client_probes_a_host_over_http(_offline_external_network):
    out = run(rd._network_client({"prompt": "check if example.org is reachable"}))
    assert out["ok"] and out["tool_output"]["http"]["status"] == 200
    assert "reachable" in out["answer"]


def test_researcher_fails_honestly_without_sources(monkeypatch):
    def empty_instant(*a, **k):
        raise AssertionError("not reached")
    monkeypatch.setattr(rd, "_web_search", lambda *a, **k: [])
    import agent_civilization.core.agents.real_dispatcher as mod
    orig = mod.safety.safe_urlopen

    class _Empty:
        def __enter__(self):
            return self

        def __exit__(self, *e):
            return False

        def read(self, *a):
            return b'{"AbstractText": "", "RelatedTopics": []}'
    monkeypatch.setattr(mod.safety, "safe_urlopen", lambda *a, **k: _Empty())
    out = run(rd._researcher({"prompt": "zzqx unknown topic"}))
    monkeypatch.setattr(mod.safety, "safe_urlopen", orig)
    assert out["ok"] is False and "no sources" in out["error"]


def test_researcher_grounding_searches_the_goal_and_returns_sources(monkeypatch):
    seen = {}

    def fake_search(q, *a, **k):
        seen["q"] = q
        return [{"title": "t", "url": "https://example.org/x", "snippet": "s"}]
    monkeypatch.setattr(rd, "_web_search", fake_search)

    class _Empty:
        def __enter__(self):
            return self

        def __exit__(self, *e):
            return False

        def read(self, *a):
            return b'{"AbstractText": "", "RelatedTopics": []}'
    monkeypatch.setattr(rd.safety, "safe_urlopen", lambda *a, **k: _Empty())
    out = run(rd._researcher({"stage": "ground", "goal": "multi-tenant SaaS",
                              "prompt": "Perform a live web search to ground ..."}))
    assert out["ok"] and seen["q"] == "multi-tenant SaaS"
    assert out["tool_output"]["sources"][0]["url"].startswith("https://")


def test_communicator_translates_through_the_translation_service(_offline_external_network):
    out = run(rd._communicator({"goal": "translate 'hello world' to French",
                                "prompt": "translate 'hello world' to French"}))
    assert out["ok"] and out["tool_output"]["method"] == "mymemory"
    assert out["answer"] == "[fr] hello world"
    assert any("api.mymemory.translated.net" in u for _, u in _offline_external_network)


def test_communicator_draft_needs_an_llm_and_says_so():
    out = run(rd._communicator({"goal": "draft a message to the team about the outage"}))
    assert out["ok"] is False and "LLM" in out["error"]


def test_communicator_draft_with_llm(fake_llm):
    fake_llm("Hi team, the outage is resolved. Thanks for your patience.")
    out = run(rd._communicator({"goal": "draft a message to the team about the outage"}))
    assert out["ok"] and out["tool_output"]["method"] == "llm"


def test_educator_refuses_to_echo_a_bare_question_offline(monkeypatch):
    monkeypatch.setattr(rd, "_web_search", lambda *a, **k: [])
    out = run(rd._educator({"prompt": "explain recursion"}))
    assert out["ok"] is False and "no explanation source" in out["error"]


def test_code_generator_retries_once_then_fails_honestly(fake_llm):
    llm = fake_llm("this is prose, not code")
    out = run(rd._code_generator({"prompt": "write a function that returns the nth prime"}))
    assert out["ok"] is False and "not valid Python" in out["error"]
    assert llm.calls == 2


def test_python_from_llm_rejects_prose_that_happens_to_parse():
    src, err = rd._python_from_llm("this is prose, not code")
    assert src == "" and "no Python statements" in err


def test_python_from_llm_tries_every_fence():
    src, err = rd._python_from_llm("text\n```text\nnot python ::\n```\n```py\nx = 1\n```")
    assert err is None and src.strip() == "x = 1"


# ------------------------------------------------------------ R-117 memory / kinds
def test_input_kind_ignores_the_previous_stages_summary():
    from agent_civilization.core import experience
    work = {"prompt": "explain recursion\n\n=== Previous stages summary ===\nmean=3 n=5 [1,2,3,4,5]"}
    assert experience.input_kind(work) != "numbers"


# ------------------------------------------------------------ keyless LLM
def test_offline_tests_disable_the_keyless_llm_by_default():
    from agent_civilization.core.llm import LLMClient
    assert os.environ.get("AGOS_LLM_KEYLESS") == "0"
    assert LLMClient(api_key="").provider == "none"


def test_keyless_provider_is_selected_without_a_key(monkeypatch):
    from agent_civilization.core.llm import LLMClient
    monkeypatch.setenv("AGOS_LLM_KEYLESS", "1")
    c = LLMClient(api_key="")
    assert c.provider == "keyless" and c.available


@pytest.mark.network
def test_live_keyless_llm_and_translation(monkeypatch):
    # SEC-06 (R-145): the public keyless provider is now opt-in on TWO gates —
    # the operator flag AND this request's explicit consent. This live check
    # exercises both, exactly like a real consenting request would.
    from agent_civilization.core import llm as _L
    from agent_civilization.core.llm import LLMClient
    monkeypatch.setenv("AGOS_LLM_KEYLESS", "1")
    token = _L.set_public_llm_consent(True)
    try:
        out = run(LLMClient(api_key="").complete("Reply with one word.", "Say hello.", max_tokens=20))
    finally:
        _L.reset_public_llm_consent(token)
    assert out["ok"] and out["content"].strip() and out["model"].startswith("keyless:")
    assert "monde" in rd._mymemory("hello world", "en", "fr").lower()


# ------------------------------------------------------------ R-118 soak truth checks
def _load_r119():
    import importlib.util
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent / "scripts/run_r119_live_hour.py"
    spec = importlib.util.spec_from_file_location("r119_script", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_r119_has_a_truth_check_for_every_request_type():
    mod = _load_r119()
    # R-122: 19 -> 24 templates (one per specialization the 19 never reached)
    assert set(mod.TRUTH) == set(mod.REQUEST_TEMPLATES) and len(mod.TRUTH) == 24


def test_r119_truth_check_rejects_the_r113_hollow_successes():
    mod = _load_r119()
    hollow = {
        "translate 'hello world' to French": [{"name": "execute", "ok": True, "output": "tone=neutral"}],
        "research the best practices for API pagination":
            [{"name": "execute", "ok": True, "output": "no direct answer; see related"}],
        "write and run tests for: def add(a, b): return a + b":
            [{"name": "execute", "ok": True, "output": "text: 12 words"}],
        "monitor the health of the system": [{"name": "execute", "ok": True, "output": "system snapshot captured"}],
    }
    for req, stages in hollow.items():
        assert mod.verify(req, stages)[0] is False, req
    real = [{"name": "execute", "ok": True, "output": "Salut, le monde"}]
    assert mod.verify("translate 'hello world' to French", real)[0] is True


def test_monitor_reports_measured_values_and_a_verdict():
    out = run(rd._monitor({}))
    assert out["ok"] and out["tool_output"]["verdict"] in ("healthy", "degraded")
    assert "rss=" in out["answer"] and "disk_free=" in out["answer"]
