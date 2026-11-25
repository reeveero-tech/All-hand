"""calculate: exact arithmetic with no code execution, so agents can compute on any server."""
import asyncio

from app.agents import ROLES, system_prompt
from app.tools import call_tool, calculate


def run(p):
    return asyncio.run(calculate(p))


def test_named_steps_and_thousands():
    out = run("budget = 200,000\nusd = budget / 3.75\nunits = 5000; price = 9.5\nunits * price\nmax(3, 7, 2)")
    assert "usd = 53,333.333333" in out and "units * price = 47,500.0" in out and "max(3, 7, 2) = 7" in out


def test_rejects_anything_but_arithmetic():
    out = run('__import__("os").system("id")\nopen("/etc/passwd")\nx.y\n2**10000\n1/0\nundefined + 1')
    lines = out.splitlines()
    assert len(lines) == 6 and all("خطأ" in ln for ln in lines)


def test_available_without_code_execution():
    allowed = {t for t in ROLES["analyst"].tools if t != "run_python"}  # production free provider: no run_python
    assert asyncio.run(call_tool("calculate", {"program": "2 * 21"}, allowed)) == "2 * 21 = 42"
    assert "calculate" in system_prompt(ROLES["writer"], False) and "صحّحه" in system_prompt(ROLES["writer"], False)
