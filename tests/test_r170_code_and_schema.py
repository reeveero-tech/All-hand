#!/usr/bin/env python3
"""R-170 (دفعة 9): متطلبا ``code`` (ترجمة الشيفرة) و``json.schema`` (مخطط) — أحمر أولًا.

معيار القبول في P4 (R-170) يذكر «JSON/schema» و«code compile/tests». قبل هذه
الدفعة: ``json`` يفحص صلاحية JSON ومفاتيح مطلوبة فقط، ولا يوجد متطلب ترجمة شيفرة
على مستوى البوابة — ادعاء ``code_generator`` أن الشيفرة «تُترجم» يُصدَّق دون إعادة
فحص مستقلة.

* ``{"code": true}``: ``tool_output.code`` نص غير فارغ يقبله
  ``compile(src, "<gate>", "exec")`` — ترجمة فقط، **لا تنفيذ**. غيابه = رفض.
* ``{"json": {"schema": {...}}}``: الحمولة تطابق مجموعة فرعية معلنة من JSON
  Schema: ``type`` (object/array/string/number/integer/boolean/null أو قائمة)،
  ``required``، ``properties``، ``items``، ``enum``، ``additionalProperties: false``.
  كلمة مخطط غير مدعومة = رفض صريح «not verified» (لا نجاح لما لم يُفحص).

التصنيف: ``RED`` يفشل سلوكيًا قبل التغيير؛ ``CHAR`` توصيف لسلوك قائم.
"""
from __future__ import annotations

import asyncio

import pytest

from agent_civilization.core.output_validators import KNOWN_REQUIREMENTS, verify_success


def _v(tool_output, reqs):
    return verify_success({"ok": True, "result": {"ok": True, "answer": "done",
                                                  "tool_output": tool_output}}, reqs)


# ------------------------------------------------------------------- code

def test_code_is_a_known_requirement():
    """RED: ``code`` متطلب معروف (كان يُرفض «unknown requirement»)."""
    assert "code" in KNOWN_REQUIREMENTS


def test_code_that_compiles_passes():
    """RED: شيفرة صحيحة ⇒ نجاح و``code`` ضمن الفحوص المنفذة."""
    v = _v({"code": "def add(a, b):\n    return a + b\n"}, {"code": True})
    assert v.ok is True and "code" in v.checks_run


@pytest.mark.parametrize("tool_output", [
    {"code": "def add(a, b)\n    return a + b\n"},    # syntax error
    {"code": "return 1\n"},                           # 'return' outside function
    {"code": ""},
    {"code": None},
    {"functions": ["add"]},                           # claim without the code
    {"code": 123},
])
def test_code_that_does_not_compile_or_is_missing_rejected(tool_output):
    """RED: شيفرة لا تُترجم أو غائبة ⇒ رفض بسبب يذكر code."""
    v = _v(tool_output, {"code": True})
    assert v.ok is False and any(r.startswith("code:") for r in v.reasons), v.reasons


def test_code_is_compiled_not_executed(tmp_path):
    """RED: الفحص لا ينفذ الشيفرة (أثر جانبي لا يحدث)."""
    marker = tmp_path / "executed"
    src = f"open({str(marker)!r}, 'w').write('x')\n"
    v = _v({"code": src}, {"code": True})
    assert v.ok is True and not marker.exists()


@pytest.mark.code_exec
def test_real_code_generator_spec_path_passes_code_requirement(monkeypatch):
    """RED (مسار حقيقي): code_generator من مواصفة منظمة + ``code`` ⇒ نجاح."""
    monkeypatch.setenv("AGOS_OUTPUT_VALIDATORS", "1")
    from agent_civilization.agents.real.real_agents import create_civilization
    agent = create_civilization(1, impl="real")["code_generator"][0]
    out = asyncio.run(agent.execute({"prompt": "make mean",
                                     "data": {"function": "mean", "params": ["xs"],
                                              "returns": "sum(xs) / len(xs)"},
                                     "verification": {"code": True}}))
    assert out["ok"] is True, out["result"]
    assert "code" in out["result"]["verification"]["checks_run"]


def _gen_agent():
    from agent_civilization.agents.real.real_agents import create_civilization
    return create_civilization(1, impl="real")["code_generator"][0]


def test_real_code_generator_supplied_code_path_now_returns_code_and_passes(monkeypatch):
    """RED → GREEN (batch 10): the supplied-code path used to return metadata
    only (no code text) ⇒ ``code`` rejected it. It now returns the text, its
    size and sha256; the gate compiles it under the DEFAULT requirement."""
    monkeypatch.setenv("AGOS_OUTPUT_VALIDATORS", "1")
    src = "def f(x):\n    return x\n"
    out = asyncio.run(_gen_agent().execute({"prompt": "check this", "data": {"code": src}}))
    assert out["ok"] is True, out
    to = out["result"]["tool_output"]
    assert to["code"] == src and to["code_chars"] == len(src) and len(to["code_sha256"]) == 64
    assert "code" in out["result"]["verification"]["checks_run"]


def test_real_code_generator_spec_path_passes_under_default(monkeypatch):
    monkeypatch.setenv("AGOS_OUTPUT_VALIDATORS", "1")
    out = asyncio.run(_gen_agent().execute({"prompt": "write mean", "data": {
        "function": "mean", "params": ["xs"], "returns": "sum(xs) / len(xs)"}}))
    assert out["ok"] is True and "code" in out["result"]["verification"]["checks_run"]


def test_real_code_generator_invalid_supplied_code_fails_honestly(monkeypatch):
    monkeypatch.setenv("AGOS_OUTPUT_VALIDATORS", "1")
    out = asyncio.run(_gen_agent().execute({"prompt": "check", "data": {"code": "def f(:\n  pass\n"}}))
    assert out["ok"] is False


class _FakeLLM:
    available = True

    def __init__(self, contents):
        self.contents = list(contents)

    async def complete(self, system, prompt, max_tokens=1024, max_wait_s=None):
        return {"ok": True, "content": self.contents.pop(0)}


def _with_llm(monkeypatch, contents):
    import agent_civilization.core.llm as llm
    fake = _FakeLLM(contents)
    monkeypatch.setattr(llm, "get_llm", lambda: fake)
    return fake


def test_llm_path_valid_code_passes_full_text(monkeypatch):
    from agent_civilization.core.agents import real_dispatcher as rd
    _with_llm(monkeypatch, ["```python\ndef add(a, b):\n    return a + b\n```"])
    out = asyncio.run(rd._code_generator({"prompt": "write add"}))
    assert out["ok"] is True and "def add" in out["tool_output"]["code"]
    from agent_civilization.core.output_validators import verify_success
    assert verify_success({"ok": True, "result": out}, {"code": True}).ok


def test_llm_path_invalid_output_twice_is_discarded(monkeypatch):
    from agent_civilization.core.agents import real_dispatcher as rd
    _with_llm(monkeypatch, ["```python\ndef broken(:\n```", "not code at all ((("])
    out = asyncio.run(rd._code_generator({"prompt": "write something"}))
    assert out["ok"] is False and "not valid Python" in out["error"]


def test_llm_path_long_code_is_not_cut_mid_way(monkeypatch):
    """The former ``src[:8000]`` could cut a valid program into one that does
    not compile. Above the bound the text is omitted (size + sha256), and the
    gate says it could not verify — it never compiles a fragment."""
    from agent_civilization.core.agents import real_dispatcher as rd
    from agent_civilization.core.output_validators import verify_success
    long_src = "".join(f"def f{i}():\n    return {i}\n" for i in range(1500))
    assert 8000 < len(long_src) <= rd.CODE_OUTPUT_MAX_CHARS
    _with_llm(monkeypatch, ["```python\n" + long_src + "```"])
    out = asyncio.run(rd._code_generator({"prompt": "many functions"}))
    assert out["tool_output"]["code"].strip() == long_src.strip()     # whole, not 8000
    assert verify_success({"ok": True, "result": out}, {"code": True}).ok
    huge = "x = 1\n" * (rd.CODE_OUTPUT_MAX_CHARS // 6 + 10)
    to = rd._code_payload(huge)
    assert "code" not in to and to["code_chars"] == len(huge) and "code_omitted" in to
    v = verify_success({"ok": True, "result": {"ok": True, "tool_output": to}}, {"code": True})
    assert not v.ok and any("not verifiable" in r for r in v.reasons)


def test_gate_compiles_but_never_executes_code(tmp_path):
    from agent_civilization.core.output_validators import verify_success
    marker = tmp_path / "executed"
    src = f"open({str(marker)!r}, 'w').write('x')\n"
    v = verify_success({"ok": True, "result": {"ok": True, "tool_output": {"code": src}}}, {"code": True})
    assert v.ok and not marker.exists()


def test_forged_code_result_that_does_not_compile_is_rejected():
    from agent_civilization.core.output_validators import verify_success
    forged = {"ok": True, "answer": "generated and verified", "tool_output": {"code": "def f(:", "verified": "ast.parse"}}
    assert not verify_success({"ok": True, "result": forged}, {"code": True}).ok


def test_code_generator_default_requirement_is_code():
    """CHAR → batch 10: was absent (supplied-code path had no text)."""
    from agent_civilization.core.output_validators import DEFAULT_REQUIREMENTS
    assert DEFAULT_REQUIREMENTS["code_generator"] == {"code": True}


# ----------------------------------------------------------------- schema

SCHEMA = {"type": "object", "required": ["name", "count"],
          "properties": {"name": {"type": "string"},
                         "count": {"type": "integer"},
                         "tags": {"type": "array", "items": {"type": "string"}},
                         "mode": {"enum": ["fast", "safe"]}},
          "additionalProperties": False}


def test_schema_valid_payload_passes():
    """RED: حمولة مطابقة ⇒ نجاح (كان: مفاتيح مخطط غير مفهومة ⇒ تجاهل/سلوك آخر)."""
    v = _v({"name": "x", "count": 2, "tags": ["a"], "mode": "fast"}, {"json": {"schema": SCHEMA}})
    assert v.ok is True and "json" in v.checks_run


@pytest.mark.parametrize("payload,needle", [
    ({"name": "x"}, "count"),                                  # required missing
    ({"name": "x", "count": "2"}, "count"),                    # wrong type
    ({"name": "x", "count": True}, "count"),                   # bool is not integer
    ({"name": "x", "count": 2, "tags": ["a", 3]}, "tags"),     # items type
    ({"name": "x", "count": 2, "mode": "slow"}, "mode"),       # enum
    ({"name": "x", "count": 2, "extra": 1}, "extra"),          # additionalProperties
    ('["not", "object"]', "type"),                             # JSON text, wrong type
])
def test_schema_violations_rejected(payload, needle):
    """RED: كل مخالفة تُرفض بسبب يذكر المسار."""
    v = _v(payload, {"json": {"schema": SCHEMA}})
    assert v.ok is False and any(needle in r for r in v.reasons), v.reasons


def test_schema_unsupported_keyword_rejected_not_ignored():
    """RED: كلمة مخطط غير مدعومة ⇒ رفض «not verified» لا تجاهل."""
    v = _v({"n": 5}, {"json": {"schema": {"type": "object",
                                          "properties": {"n": {"type": "integer", "minimum": 10}}}}})
    assert v.ok is False and any("not verified" in r for r in v.reasons), v.reasons


def test_char_json_true_and_key_list_unchanged():
    """CHAR: الصيغتان القائمتان ``json: true`` و``json: [keys]`` كما هما."""
    assert _v({"a": 1}, {"json": True}).ok is True
    assert _v({"a": 1}, {"json": ["a"]}).ok is True
    assert _v({"a": 1}, {"json": ["b"]}).ok is False


def test_dag_stage_can_declare_code_and_schema():
    """RED: خطة DAG تعلن ``code`` و``json.schema`` وتصل إلى العمل كما هي."""
    from agent_civilization.pipeline.dag import normalize_plan
    plan = normalize_plan({"stages": [
        {"name": "gen", "specialization": "code_generator", "prompt": "p",
         "verification": {"code": True}},
        {"name": "fmt", "specialization": "data_processor", "prompt": "p",
         "verification": {"json": {"schema": SCHEMA}}}]})
    by = {s["name"]: s for s in plan["stages"]}
    assert by["gen"]["verification"] == {"code": True}
    assert by["fmt"]["verification"]["json"]["schema"] == SCHEMA
    assert "code" in KNOWN_REQUIREMENTS
