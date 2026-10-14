#!/usr/bin/env python3
"""R-102: print what each changed handler answers for the same realistic inputs.

Run on two commits to compare (docs/audit/M6/logs/raw/66 = before, 67 = after).
Deterministic, offline; code_generator's description path is shown without an LLM.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.pop("OPENROUTER_API_KEY", None)

from agent_civilization.core.agents import real_dispatcher as rd  # noqa: E402

CASES = [
    ("builder", {"prompt": "build a FastAPI service called shop"}),
    ("builder", {"prompt": "scaffold", "data": ["app.py", "tests/test_app.py"]}),
    ("architect", {"prompt": "design system", "data": ["web ui", "api", "db"]}),
    ("architect", {"prompt": "SaaS with payments and email notifications"}),
    ("designer", {"prompt": "palette", "data": "brand: forest"}),
    ("designer", {"prompt": "dark mode #ff5722"}),
    ("modifier", {"prompt": "rename foo to bar", "data": "def foo():\n    return 'foo'\n\nprint(foo())\n"}),
    ("code_generator", {"prompt": "gen", "data": {"function": "mean", "params": ["xs"], "returns": "sum(xs) / len(xs)"}}),
    ("code_generator", {"prompt": "اكتب كود Python لحساب متوسط قائمة أرقام"}),
    ("analyst", {"prompt": "حلل أداء النظام الحالي وما سبب البطء؟"}),
    ("strategist", {"prompt": "plan", "data": ["ship mvp", "hire", "raise"]}),
    ("educator", {"prompt": "Recursion calls itself. It needs a base case."}),
]


async def main():
    for spec, work in CASES:
        r = await rd.dispatch(spec, work)
        out = r.get("tool_output")
        print(f"== {spec} {json.dumps(work, ensure_ascii=False)}")
        print(f"   ok={r.get('ok')} answer={r.get('answer') or r.get('error')}")
        print(f"   tool_output={json.dumps(out, ensure_ascii=False)[:600]}")


if __name__ == "__main__":
    asyncio.run(main())
