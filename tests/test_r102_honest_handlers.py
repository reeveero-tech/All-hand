"""R-102 (D-045): handlers do what their answers say.

Before R-102 (docs/audit/M6/logs/raw/65):
* builder   — "N build action(s) queued": no queue, no artifact;
* architect — the same five layers for any input;
* designer  — colours from SHA-256 of the text ("forest" -> arbitrary hex);
* modifier  — whitespace only, the modified code was not returned;
* code_generator — parsed supplied code, never generated any;
* analyst (text) — word count only; strategist/educator answers overstated.

Every test goes through ``real_dispatcher.dispatch`` (the path the API uses),
and most are discriminative: different input must give different output.
"""
import ast
import asyncio

import pytest

from agent_civilization.core.agents import craft
from agent_civilization.core.agents import real_dispatcher as rd


def run(spec, work):
    return asyncio.run(rd.dispatch(spec, work))


# ---------------------------------------------------------------- builder
def test_builder_returns_real_verified_files_for_given_paths():
    r = run("builder", {"prompt": "scaffold", "data": ["app.py", "tests/test_app.py", "config.json"]})
    assert r["ok"], r
    files = {f["path"]: f for f in r["tool_output"]["files"]}
    assert set(files) == {"app.py", "tests/test_app.py", "config.json"}
    assert all(f["verified"] for f in files.values())
    ast.parse(files["app.py"]["content"])
    assert "def test_app_imports" in files["tests/test_app.py"]["content"]
    assert r["tool_output"]["persisted"] is False and "not written to the server" in r["answer"]
    assert "queued" not in r["answer"]


def test_builder_stack_follows_the_request():
    api = run("builder", {"prompt": "build a FastAPI service called shop"})["tool_output"]
    node = run("builder", {"prompt": "create a node express app"})["tool_output"]
    assert api["stack"] == "python-api" and node["stack"] == "node-app"
    assert api["project"] == "shop"
    assert {f["path"] for f in api["files"]} != {f["path"] for f in node["files"]}


def test_builder_detects_broken_supplied_content_and_refuses_unsafe_paths():
    bad = run("builder", {"prompt": "x", "data": {"a.py": "def f(:\n", "b.json": "{}"}})
    assert bad["ok"] is False and "a.py" in bad["error"]
    unsafe = run("builder", {"prompt": "x", "data": ["ok.py", "../etc/passwd"]})
    assert unsafe["ok"] is False and "../etc/passwd" in unsafe["error"]
    for path in ("/etc/passwd", "a/../../x", "..", "a b.py"):
        assert run("builder", {"prompt": "x", "data": [path]})["ok"] is False, path
    assert run("builder", {"prompt": "x", "data": [".gitignore", ".github/ci.yml"]})["ok"] is True


def test_builder_ignores_data_that_is_not_a_file_list():
    # a pipeline passes the user's data to every stage
    r = run("builder", {"prompt": "build a cli tool", "data": {"name": "x", "value": 42}})
    assert r["ok"] and r["tool_output"]["stack"] == "python-cli"
    assert "name" not in {f["path"] for f in r["tool_output"]["files"]}
    r = run("builder", {"prompt": "scaffold", "data": ["web ui", "api"]})
    assert r["tool_output"]["stack"] == "python-app" and r["tool_output"]["source"].startswith("no stack")


# -------------------------------------------------------------- architect
def test_architect_components_follow_the_request():
    a = run("architect", {"prompt": "design system", "data": ["web ui", "api", "db"]})["tool_output"]
    b = run("architect", {"prompt": "SaaS with payments and email notifications"})["tool_output"]
    names_a = {c["name"] for c in a["components"]}
    names_b = {c["name"] for c in b["components"]}
    assert names_a == {"ui", "api", "database"}
    assert {"tenancy", "payments", "notifications", "queue", "auth", "database"} <= names_b
    assert names_a != names_b


def test_architect_build_order_respects_dependencies():
    out = run("architect", {"prompt": "add payments"})["tool_output"]
    order = out["build_order"]
    for c in out["components"]:
        for dep in c["depends_on"]:
            assert order.index(dep) < order.index(c["name"]), (dep, c["name"])
    assert {"auth", "database"} <= set(order) and any(c["why"].startswith("implied") for c in out["components"])


def test_architect_arabic_and_unmatched_items():
    r = run("architect", {"prompt": "منصة متعددة المستأجرين مع دفع وإشعارات بالبريد"})
    names = {c["name"] for c in r["tool_output"]["components"]}
    assert {"tenancy", "payments", "notifications"} <= names
    r2 = run("architect", {"prompt": "design", "data": ["api", "blockchain"]})
    assert r2["tool_output"]["unmatched_items"] == ["blockchain"]


def test_architect_says_when_it_assumed_a_baseline():
    # R-170 (batch 12): an assumed baseline is not a design of the request —
    # an honest failure that keeps the labelled partial output (was ok=True).
    r = run("architect", {"prompt": "demo"})
    assert r["ok"] is False and r["partial_tool_output"]["assumed_baseline"] is True
    assert "assumed" in r["error"] and "assumed" in r["partial_answer"]


# --------------------------------------------------------------- designer
def test_designer_hue_comes_from_the_requested_colour():
    forest = run("designer", {"prompt": "palette", "data": "brand: forest"})["tool_output"]
    hexed = run("designer", {"prompt": "brand colour #ff5722"})["tool_output"]
    assert forest["hue"] == 140 and "forest" in forest["source"]
    assert hexed["hue"] == 14 and "#ff5722" in hexed["source"]
    assert forest["palette"]["primary"] != hexed["palette"]["primary"]


def test_designer_meets_wcag_targets_it_reports():
    for prompt in ("palette forest", "dark mode #ff5722", "أزرق داكن", "yellow"):
        out = run("designer", {"prompt": prompt})["tool_output"]
        p = out["palette"]
        assert craft.contrast_ratio(p["text"], p["background"]) >= 7.0, prompt
        assert craft.contrast_ratio(p["primary"], p["background"]) >= 4.5, prompt
        assert all(c["pass"] for c in out["wcag"].values()), prompt
    assert run("designer", {"prompt": "dark mode"})["tool_output"]["theme"] == "dark"


def test_designer_says_when_no_colour_was_given():
    out = run("designer", {"prompt": "make me a palette"})
    assert "no colour in the request" in out["answer"]


def test_contrast_ratio_matches_wcag_reference_values():
    assert craft.contrast_ratio("#000000", "#ffffff") == 21.0
    assert craft.contrast_ratio("#777777", "#ffffff") == 4.48  # WCAG reference value


# --------------------------------------------------------------- modifier
CODE = 'import os\nimport sys\n\n\ndef foo():\n    s = "foo"  # foo\n    return foo\n\n\nprint(sys.argv, foo)   \n'


def test_modifier_renames_identifiers_only_and_returns_the_new_code():
    r = run("modifier", {"prompt": "rename foo to bar", "data": CODE})
    assert r["ok"], r
    new = r["tool_output"]["new_code"]
    ast.parse(new)
    assert "def bar():" in new and "return bar" in new and "print(sys.argv, bar)" in new
    assert '"foo"' in new and "# foo" in new  # strings and comments untouched
    assert "-def foo():" in r["tool_output"]["diff"] and "+def bar():" in r["tool_output"]["diff"]
    assert "3 occurrence(s)" in r["answer"]


def test_modifier_arabic_rename_and_unused_imports():
    r = run("modifier", {"prompt": "غيّر اسم foo إلى bar واحذف الاستيرادات غير المستخدمة", "data": CODE})
    new = r["tool_output"]["new_code"]
    assert "import os" not in new and "import sys" in new
    assert "def bar():" in new
    assert "removed 1 unused import(s)" in r["answer"]


def test_modifier_is_honest_when_no_instruction_is_recognised():
    r = run("modifier", {"prompt": "make it better", "data": "x = 1   \n"})
    assert r["ok"] and "no supported instruction" in r["answer"]
    assert r["tool_output"]["new_code"] == "x = 1\n"


def test_modifier_refuses_invalid_target_name():
    r = run("modifier", {"prompt": "rename foo to class", "data": "foo = 1\n"})
    assert r["ok"] is False


# --------------------------------------------------------- code_generator
def test_code_generator_generates_from_a_structured_spec():
    r = run("code_generator", {"prompt": "gen", "data": {"function": "mean", "params": ["xs"],
                                                         "returns": "sum(xs) / len(xs)"}})
    assert r["ok"] and r["tool_output"]["generated"] is True
    ns = {}
    exec(compile(r["tool_output"]["code"], "<t>", "exec"), ns)  # generated code really works
    assert ns["mean"]([1, 2, 3, 6]) == 3.0
    cls = run("code_generator", {"prompt": "gen", "data": {"class": "User", "fields": ["name", "age"]}})
    ns = {}
    exec(compile(cls["tool_output"]["code"], "<t>", "exec"), ns)
    assert ns["User"](name="a", age=3).age == 3


def test_code_generator_rejects_bad_specs():
    r = run("code_generator", {"prompt": "g", "data": {"function": "f", "params": ["a"], "returns": "os.system(a)"}})
    assert r["ok"] is False and "os" in r["error"]
    r = run("code_generator", {"prompt": "g", "data": {"function": "class", "params": []}})
    assert r["ok"] is False


def test_code_generator_says_supplied_code_was_validated_not_generated():
    r = run("code_generator", {"prompt": "generate code", "data": "def add(a, b):\n    return a + b\n"})
    assert r["ok"] and r["tool_output"]["generated"] is False
    assert "not generated" in r["answer"]


class _FakeLLM:
    def __init__(self, available=True, content=""):
        self.available = available
        self.content = content
        self.calls = 0

    async def complete(self, system, prompt, max_tokens=1024):
        self.calls += 1
        return {"ok": True, "content": self.content}


def test_code_generator_without_llm_explains_instead_of_pretending(monkeypatch):
    import agent_civilization.core.llm as llm
    monkeypatch.setattr(llm, "get_llm", lambda: _FakeLLM(available=False))
    r = run("code_generator", {"prompt": "اكتب كود Python لحساب متوسط قائمة أرقام"})
    assert r["ok"] is False and "needs an LLM" in r["error"] and '"function"' in r["error"]


def test_code_generator_llm_output_must_parse(monkeypatch):
    import agent_civilization.core.llm as llm
    good = _FakeLLM(content="```python\ndef mean(xs):\n    return sum(xs) / len(xs)\n```")
    monkeypatch.setattr(llm, "get_llm", lambda: good)
    r = run("code_generator", {"prompt": "write a mean function"})
    assert r["ok"] and r["tool_output"]["method"] == "llm" and r["tool_output"]["functions"] == ["mean"]
    bad = _FakeLLM(content="```python\ndef mean(xs) return\n```")
    monkeypatch.setattr(llm, "get_llm", lambda: bad)
    r = run("code_generator", {"prompt": "write a mean function"})
    assert r["ok"] is False and "discarded" in r["error"]


# ------------------------------------------------- analyst / strategist / educator
def test_analyst_text_reports_real_measures():
    ar = run("analyst", {"prompt": "حلل أداء النظام الحالي وما سبب البطء؟"})["tool_output"]
    en = run("analyst", {"prompt": "Analyze the system performance. Why is the system slow?"})["tool_output"]
    assert ar["language"] == "ar" and en["language"] == "en"
    assert ar["questions"] == 1 and en["sentences"] == 2
    assert en["top_terms"][0][0] == "system"
    assert run("analyst", {"prompt": "avg", "data": [1, 2, 3, 4]})["tool_output"]["mean"] == 2.5


def test_strategist_uses_listed_objectives_and_does_not_overclaim():
    r = run("strategist", {"prompt": "plan", "data": ["ship mvp", "hire", "raise"]})
    assert [s["goal"] for s in r["tool_output"]["roadmap"]] == ["ship mvp", "hire", "raise"]
    assert "in the order given" in r["answer"]


def test_educator_answer_describes_what_it_did():
    r = run("educator", {"prompt": "Recursion calls itself. It needs a base case. Use it carefully."})
    assert r["answer"] == "first 3 sentence(s) as key points"


# ---------------------------------------------------------------- metadata
@pytest.mark.parametrize("spec,word", [("builder", "Scaffold"), ("designer", "WCAG"),
                                        ("modifier", "Rename"), ("architect", "component graph"),
                                        ("code_generator", "spec")])
def test_public_descriptions_match_the_new_behaviour(spec, word):
    from agent_civilization.agents.real.real_agents import SPECIALIZATIONS
    assert word in SPECIALIZATIONS[spec]
