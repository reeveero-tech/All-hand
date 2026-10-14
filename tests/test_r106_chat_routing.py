"""R-106: /api/chat routes by intent, not to a fixed specialist.

Before R-106, without an LLM (no OPENROUTER_API_KEY — the default), every chat
message went to ``communicator``: "حلل 4 8 15 16 23 42" was answered
"tone=neutral" (docs/audit/M6/logs/raw/54..55).  The deterministic router
``agent_civilization.routing.route`` existed but chat never called it.
Also fixed here: intent is read from the instruction, not from attached data
("تحقق من صحة هذا JSON: {"name":"test"}" went to test_runner because of the
word "test" inside the JSON).
"""
import pytest
from fastapi.testclient import TestClient

import server
from agent_civilization.routing import route

# The five quick prompts shipped in web/src/components/Chat.jsx, with the
# specialist each one is asking for.
UI_QUICK_PROMPTS = [
    ("حلل أداء النظام الحالي", "analyst"),
    ("ابحث عن أحدث تقنيات الذكاء الاصطناعي", "researcher"),
    ("اكتب كود Python لحساب متوسط قائمة أرقام", "code_generator"),
    ('تحقق من صحة هذا JSON: {"name":"test","value":42}', "validator"),
    ("صمم استراتيجية لتطوير الحضارة", "strategist"),
]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with TestClient(server.app, raise_server_exceptions=False) as c:
        yield c


def test_ui_quick_prompts_exist_in_the_ui():
    src = open("web/src/components/Chat.jsx", encoding="utf-8").read()
    for prompt, _ in UI_QUICK_PROMPTS:
        assert prompt.replace('"', '\\"') in src or prompt in src, prompt


@pytest.mark.parametrize("prompt,expected", UI_QUICK_PROMPTS)
def test_chat_without_llm_routes_ui_prompts_to_the_right_specialist(client, monkeypatch, prompt, expected):
    monkeypatch.setattr(server, "get_llm", lambda: _FakeLLM(available=False))
    r = client.post("/api/chat", json={"message": prompt})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["routed_to"] == expected
    assert d["agent"].startswith(expected)
    assert d["routing"]["method"] == "keywords" and d["routing"]["reason"]


def test_chat_analysis_is_answered_by_the_analyst_with_real_numbers(client, monkeypatch):
    monkeypatch.setattr(server, "get_llm", lambda: _FakeLLM(available=False))
    d = client.post("/api/chat", json={"message": "حلل 4 8 15 16 23 42"}).json()
    assert d["routed_to"] == "analyst"
    assert "tone=" not in d["outcome"]["result"]["answer"]
    stats = d["outcome"]["result"]["tool_output"]
    assert stats["n"] == 6 and stats["mean"] == 18.0 and stats["min"] == 4.0 and stats["max"] == 42.0


class _FakeLLM:
    def __init__(self, available=True, reply=None):
        self.available = available
        self._reply = reply

    async def complete(self, system, prompt, max_tokens=1024):
        return self._reply


def test_llm_choice_wins_when_it_names_a_specialization(client, monkeypatch):
    monkeypatch.setattr(server, "get_llm", lambda: _FakeLLM(reply={"ok": True, "content": " db-manager\n"}))
    d = client.post("/api/chat", json={"message": "حلل 4 8 15"}).json()
    assert d["routed_to"] == "db_manager" and d["routing"]["method"] == "llm"


@pytest.mark.parametrize("reply", [{"ok": True, "content": "I am not sure"},
                                   {"ok": False, "error": "HTTP 429"}])
def test_llm_unusable_answer_falls_back_to_the_keyword_router(client, monkeypatch, reply):
    monkeypatch.setattr(server, "get_llm", lambda: _FakeLLM(reply=reply))
    d = client.post("/api/chat", json={"message": "حلل 4 8 15"}).json()
    assert d["routed_to"] == "analyst" and d["routing"]["method"] == "keywords"


def test_router_reads_intent_from_instruction_not_from_data():
    assert route('تحقق من صحة هذا JSON: {"name":"test","value":42}')["primary"] == "validator"
    assert route('validate {"a": {"b": "test"}}')["primary"] == "validator"   # nested object
    assert route('translate "run the unit test" to Arabic')["primary"] == "communicator"


def test_router_still_looks_inside_data_when_the_instruction_has_no_keyword():
    assert route("```python\nprint(1)\n```")["primary"] == "code_generator"


def test_every_router_target_is_a_live_specialization():
    from agent_civilization.routing.auto_router import _INTENT_RULES
    assert {spec for _, spec, _ in _INTENT_RULES} <= set(server.SPECIALIZATIONS)
