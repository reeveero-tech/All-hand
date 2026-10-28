"""R-170 (batch 12) — task fit: no success without testable evidence that the
REQUESTED task was done (strategist / architect / designer / reviewer).

Positive (a real completion is accepted), negative (a hollow success is
refused, with or without the gate), invalid outputs (malformed evidence), and
Arabic cases. Offline: no network, no model, no code execution.
"""
from __future__ import annotations

import asyncio

import pytest

from agent_civilization.core import task_fit as tf
from agent_civilization.core.agents import real_dispatcher as rd
from agent_civilization.core.output_validators import apply_output_verification, requirements_for


def run(spec, work):
    return asyncio.run(rd._HANDLERS[spec](dict(work)))


def gate(spec, work, result, monkeypatch):
    monkeypatch.setenv("AGOS_OUTPUT_VALIDATORS", "1")
    req = requirements_for(spec, work)
    return apply_output_verification(req, bool(result.get("ok")), result)


# ------------------------------------------------------------ handlers: positive

@pytest.mark.parametrize("work", [
    {"prompt": "Turn these into an ordered roadmap, in this order: write docs; fix the login bug; add tests"},
    {"prompt": "حوّل هذه إلى خارطة طريق مرتبة: كتابة الدليل؛ إصلاح الخطأ؛ إضافة اختبارات"},
    {"prompt": "Order these as a roadmap:\n- hire\n- train\n- ship"},
    {"prompt": "Make a roadmap from the objectives given.", "data": ["a", "b", "c"]},
])
def test_strategist_orders_given_objectives(work):
    r = run("strategist", work)
    assert r["ok"] is True, r
    assert [s["goal"] for s in r["tool_output"]["roadmap"]] == tf.parse_objectives(work["prompt"], work.get("data"))


@pytest.mark.parametrize("prompt,comps", [
    ("Design an architecture with a web dashboard, a REST API and a Postgres database.", {"ui", "api", "database"}),
    ("صمّم معمارية فيها واجهة المستخدم ومصادقة وقاعدة بيانات.", {"ui", "auth", "database"}),
])
def test_architect_designs_named_components(prompt, comps):
    r = run("architect", {"prompt": prompt})
    assert r["ok"] is True, r
    assert comps <= {c["name"] for c in r["tool_output"]["components"]}


@pytest.mark.parametrize("prompt,src", [
    ("Create a colour palette based on #1e88e5.", "#1e88e5"),
    ("لوحة ألوان من الكحلي بتباين جيد.", "كحلي"),          # definite article (was missed)
    ("لوحة ألوان مستوحاة من البحر للوضع الليلي.", "بحر"),
    ("dark mode", "default"),                                # no colour, no purpose: neutral is fair
])
def test_designer_builds_requested_palette(prompt, src):
    r = run("designer", {"prompt": prompt})
    assert r["ok"] is True, r
    assert src in r["tool_output"]["source"]


@pytest.mark.parametrize("code,kind", [
    ("def avg(xs):\n    return sum(xs) / len(xs)\n", "empty_division"),
    ("def first(xs):\n    return xs[0]\n", "empty_index"),
    ("def f(cur, n):\n    cur.execute(\"SELECT * FROM t WHERE n = '\" + n + \"'\")\n", "sql_injection"),
    ("def f(cur, n):\n    cur.execute(f\"SELECT * FROM t WHERE n = {n}\")\n", "sql_injection"),
    ("def f(cur, n):\n    cur.execute(\"SELECT * FROM t WHERE n = '%s'\" % n)\n", "sql_injection"),
    ("def c(e):\n    return eval(e)\n", "eval"),
    ("def t(xs):\n    s = 0\n    for i in range(len(xs)):\n        s += xs[i]\n    return s\n", "range_len"),
    ("def a(u):\n    if u.admin == True:\n        return 1\n    return 0\n", "bool_compare"),
])
def test_reviewer_finds_known_defects_with_line_and_suggestion(code, kind):
    r = run("reviewer", {"prompt": "Review and suggest an improvement:", "data": code})
    assert r["ok"] is True, r
    f = [x for x in r["tool_output"]["findings"] if x["kind"] == kind]
    assert f and isinstance(f[0]["line"], int) and f[0]["suggestion"]


@pytest.mark.parametrize("code", [
    "def add(a, b):\n    return a + b\n",
    "def head(xs):\n    if not xs:\n        return None\n    return xs[0]\n",
    "def find(cur, name):\n    cur.execute(\"SELECT * FROM users WHERE name = ?\", (name,))\n    return cur.fetchall()\n",
])
def test_reviewer_clean_code_generic_review_is_complete_with_no_findings(code):
    r = run("reviewer", {"prompt": "Review this code:", "data": code})
    assert r["ok"] is True and r["tool_output"]["findings"] == [], r


def test_reviewer_reviews_the_previous_stage_artifact():
    """A DAG review stage gets the code from the artifact, not the prompt."""
    work = {"prompt": "review the output", "artifact": {"kind": "code", "code": "def f(xs):\n    return xs[0]\n"}}
    r = run("reviewer", work)
    assert r["ok"] is True and r["tool_output"]["findings"][0]["kind"] == "empty_index"


# ------------------------------------------------------------ handlers: negative (hollow)

@pytest.mark.parametrize("spec,prompt,why", [
    ("strategist", "Give me a 3-step plan to launch a mobile app.", "no objectives listed"),
    ("strategist", "اذكر خمسة مؤشرات أداء لفريق المبيعات.", "no objectives listed"),
    ("strategist", "Rank these by business impact: a; b; c", "ranking by a criterion"),
    ("architect", "Explain caching layers briefly.", "explanation"),
    ("architect", "متى يختار الفريق طابور رسائل بدل استدعاءات HTTP؟", "explanation"),
    ("architect", "Describe an architecture for a URL shortener.", "assumed baseline"),
    ("designer", "Suggest a font pairing for a law firm website.", "fonts requested"),
    ("designer", "اقترح تخطيطًا بسيطًا لصفحة هبوط لمطعم.", "layout requested"),
    ("designer", "Suggest a colour palette for a meditation app and say why.", "neutral default"),
    ("reviewer", "Review my essay about climate change for clarity.", "no code"),
    ("reviewer", "هل استعلام SQL الخاص بي آمن؟ أبنيه بدمج النصوص.", "no code"),
])
def test_hollow_success_becomes_honest_failure_without_the_gate(spec, prompt, why, monkeypatch):
    monkeypatch.setenv("AGOS_OUTPUT_VALIDATORS", "0")
    r = run(spec, {"prompt": prompt})
    assert r["ok"] is False and r["error"].startswith("task_not_completed:"), r
    assert why in r["error"]
    assert r["task_fit"]["completed"] is False
    assert "partial_answer" in r or "partial_tool_output" in r   # partial output kept, not hidden


def test_reviewer_improvement_requested_on_clean_code_is_not_completed():
    r = run("reviewer", {"prompt": "Suggest an improvement:", "data": "def add(a, b):\n    return a + b\n"})
    assert r["ok"] is False and "improvement was requested" in r["error"]


# ------------------------------------------------------------ gate: requirement binding

def test_requirement_is_bound_to_the_request_before_the_handler():
    req = requirements_for("designer", {"prompt": "Palette from teal"})
    assert req == {"task_fit": {"spec": "designer", "request": "Palette from teal", "data": None, "plan_stages": 0}}


def test_unbound_task_fit_requirement_is_rejected_not_skipped(monkeypatch):
    monkeypatch.setenv("AGOS_OUTPUT_VALIDATORS", "1")
    ok, res = apply_output_verification({"task_fit": {"spec": "nope"}}, True, {"ok": True, "answer": "x"})
    assert ok is False and any("not bound" in r for r in res["verification"]["reasons"])


def test_gate_rejects_forged_structured_evidence(monkeypatch):
    """Invalid output: the palette claims contrast it does not have (recomputed)."""
    work = {"prompt": "Palette from #1e88e5"}
    bad = {"ok": True, "answer": "palette", "tool_output": {
        "palette": {"primary": "#eeeeee", "secondary": "#eeeeee", "accent": "#eeeeee", "text": "#eeeeee",
                    "background": "#ffffff"}, "wcag": {"primary_on_background": {"ratio": 9, "pass": True}},
        "theme": "light", "source": "hex #1e88e5"}}
    ok, res = gate("designer", work, bad, monkeypatch)
    assert ok is False and any("recomputed" in r for r in res["verification"]["reasons"])


def test_gate_rejects_untraceable_roadmap(monkeypatch):
    work = {"prompt": "Turn into a roadmap: write docs; fix bug"}
    bad = {"ok": True, "tool_output": {"roadmap": [{"step": 1, "goal": "conquer the market"},
                                                   {"step": 2, "goal": "write docs"}]}}
    ok, res = gate("strategist", work, bad, monkeypatch)
    assert ok is False and any("not traceable" in r for r in res["verification"]["reasons"])


def test_gate_rejects_bad_build_order_and_out_of_range_findings(monkeypatch):
    a = {"ok": True, "tool_output": {"components": [
        {"name": "api", "depends_on": ["database"], "why": "requested"},
        {"name": "database", "depends_on": [], "why": "requested"}], "build_order": ["api", "database"],
        "assumed_baseline": False}}
    ok, res = gate("architect", {"prompt": "Design an API with a database."}, a, monkeypatch)
    assert ok is False and any("before its dependency" in r for r in res["verification"]["reasons"])
    rv = {"ok": True, "tool_output": {"code_reviewed": True, "issues": ["x"], "checks": ["sql injection"],
                                      "findings": [{"line": 99, "kind": "x"}]}}
    ok, res = gate("reviewer", {"prompt": "Review:", "data": "def f():\n    return 1\n"}, rv, monkeypatch)
    assert ok is False and any("outside the code" in r for r in res["verification"]["reasons"])


@pytest.mark.parametrize("result", [None, "ok", {"ok": True}, {"ok": True, "answer": ""},
                                    {"ok": True, "tool_output": {"roadmap": "not a list"}}])
def test_gate_rejects_malformed_results(result, monkeypatch):
    monkeypatch.setenv("AGOS_OUTPUT_VALIDATORS", "1")
    reasons, _ = tf.check_requirement(tf.bind("strategist", {"prompt": "Roadmap: a; b"}), result)
    assert reasons


# ------------------------------------------------------------ prose (another implementation)

@pytest.mark.parametrize("spec,prompt,answer", [
    ("strategist", "أعطني خطة من ثلاث خطوات لإطلاق متجر إلكتروني.",
     "1. حدّد المنتجات والجمهور المستهدف وجهّز صفحات المتجر.\n2. فعّل الدفع والشحن واختبر الطلبات التجريبية.\n"
     "3. أطلق المتجر بحملة تسويقية وتابع المبيعات وملاحظات العملاء."),
    ("designer", "Suggest a font pairing for a law firm website.",
     "Pair a classic serif for headings, such as Libre Baskerville, with a clean sans-serif for body text, such as "
     "Source Sans 3; the serif signals trust and the sans-serif keeps paragraphs readable."),
    ("reviewer", "Review this code:\n```python\ndef avg(xs):\n    return sum(xs) / len(xs)\n```",
     "avg() raises ZeroDivisionError when xs is empty (line 2). Guard it with `if not xs: return 0.0` "
     "or raise a clear ValueError before dividing."),
])
def test_correct_prose_is_accepted_structurally(spec, prompt, answer, monkeypatch):
    ok, res = gate(spec, {"prompt": prompt}, {"ok": True, "answer": answer}, monkeypatch)
    assert ok is True, res
    assert "task_fit.prose-structural" in res["verification"]["checks_run"]


@pytest.mark.parametrize("spec,prompt,answer,why", [
    ("strategist", "Give me a 3-step plan to launch a mobile app.", "Task completed successfully.", "bare success"),
    ("strategist", "أعطني خطة من ثلاث خطوات لإطلاق متجر إلكتروني.",
     "Step 1: plan the shop. Step 2: build the shop and test it well. Step 3: launch it to customers today.",
     "language"),
    ("strategist", "List five KPIs for a support team.",
     "- First response time, measured weekly across every queue\n- CSAT from the post-ticket survey", "requested 5"),
    ("architect", "اشرح طبقات التخزين المؤقت باختصار.", "INSUFFICIENT_EVIDENCE", "abstention"),
    ("reviewer", "Review this code:\n```python\ndef f():\n    return 1\n```",
     "The code looks fine overall and there is nothing more that needs to be changed in it at all.", "cites no line"),
])
def test_hollow_or_wrong_form_prose_is_rejected(spec, prompt, answer, why, monkeypatch):
    ok, res = gate(spec, {"prompt": prompt}, {"ok": True, "answer": answer}, monkeypatch)
    assert ok is False and any(why in r for r in res["verification"]["reasons"]), res


def test_prose_check_is_documented_as_structural_not_semantic():
    assert "NOT correctness" in tf.__doc__ and "STRUCTURAL" in tf.__doc__
