"""Found on production (run 9658eefdff3f): a long calculate call was cut off, was not valid JSON, and the
raw tool call was handed in as the task's output. And a per-task verifier judged one subtask against the
whole request ("no competitor analysis" in the financial task)."""
import asyncio
import json

from app import orchestrator
from app.agents import ROLES, _looks_like_call, run_agent


def test_looks_like_call():
    assert _looks_like_call('{"action":"tool","tool":"calculate","input":{"program":"a = 1\\nb = ')
    assert _looks_like_call('```json\n{"action": "tool", "tool": "x"')
    assert not _looks_like_call("# تقرير\nنص عادي")
    assert not _looks_like_call('{"action": "final", "output": "x"')


def test_truncated_tool_call_is_not_the_answer():
    replies = ['{"action":"tool","tool":"calculate","input":{"program":"a = 1\\nb = ',  # chat_json fails
               '{"action":"tool","tool":"calculate","input":{"program":"a = 1\\nb = ',  # raw retry: still a call
               json.dumps({"action": "final", "output": "التقرير الكامل", "confidence": 0.8})]

    class L:
        async def chat_json(self, messages, **k):
            r = replies.pop(0)
            return json.loads(r)  # raises ValueError on the cut-off call

        async def chat(self, messages, **k):
            return replies.pop(0)

    async def emit(*a):
        pass

    res = asyncio.run(run_agent(L(), ROLES["writer"], "t", "", emit, False))
    assert res["output"] == "التقرير الكامل"


def test_per_task_verifier_is_scoped_to_its_task():
    seen = []

    class L:
        async def chat_json(self, messages, **k):
            seen.append(messages[-1]["content"])
            return {"pass": True, "score": 8, "issues": []}

    asyncio.run(orchestrator.verify(L(), {"instructions": "احسب التكاليف", "acceptance": "جدول"}, "x", goal="ادرس السوق"))
    assert "مهمة فرعية واحدة" in seen[0]
