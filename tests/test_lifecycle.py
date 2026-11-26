"""Long-task lifecycle: a stop at any phase keeps the work, and resuming continues from that phase only.

Offline: a scripted model whose quota runs out after N replies, the real orchestrator, the real report builder.
"""
import asyncio
import json

import pytest

from app import export, llm as L
from app import report as R
from app.orchestrator import Orchestrator, Run, unreviewed
from tests.test_core import FakeLLM, T, plan_of


@pytest.fixture(autouse=True)
def _clear():
    L._HOLDS.clear()
    yield
    L._HOLDS.clear()


class Model(FakeLLM):
    """Records every call by kind; the quota is spent after `quota` replies (None: never)."""

    def __init__(self, plan, quota=None, **kw):
        super().__init__(plan, **kw)
        self.quota, self.kinds = quota, []

    async def chat(self, messages, temperature=0.3, max_tokens=2000):
        sys_ = messages[0]["content"]
        kind = ("plan" if sys_.startswith("أنت المنسق الرئيسي") else "review" if sys_.startswith("أنت مدقق")
                else "report" if sys_.startswith("أنت المنسق. اجمع") else "agent" if sys_.startswith("أنت وكيل")
                else "figures")  # plausibility check of the final report
        if self.quota is not None and self.usage.calls >= self.quota:
            raise L.ModelPaused("HTTP 402 FREE_LIMIT: 100 replies a day", kind="quota_exhausted", status=402, resume_after=1.0)
        self.kinds.append(kind)
        if kind == "agent":
            task = next((w for w in ("مهمة-a", "مهمة-b", "مهمة-c") if w in messages[1]["content"]), "?")
            self.usage.calls += 1
            return json.dumps({"action": "final", "output": f"ناتج {task}: 120 ريال (محسوب)", "confidence": 0.9})
        return await super().chat(messages, temperature, max_tokens)


def P(*ids, deps=None):
    tasks = []
    for i in ids:
        t = T(i, (deps or {}).get(i, ()))
        t["instructions"] = f"مهمة-{i}"
        tasks.append(t)
    return plan_of(*tasks)


async def _go(model, prior=None, goal="ادرس السوق واكتب تقريرًا"):
    run = Run(goal=goal, llm_public={"provider": "fake"}, max_agents=30)
    saves = []

    async def persist(final=False):
        saves.append(json.loads(json.dumps(run.full(), default=str)))
        return True
    await Orchestrator(run, model, persist=persist, prior=prior).execute()
    return run, saves


def go(model, prior=None):
    return asyncio.run(_go(model, prior))


def stored(run):
    return json.loads(json.dumps(run.full(), default=str))


# ---- 1. quota in analysis / review, then resume ----------------------------------------------------------------

def test_quota_during_review_then_resume_reviews_only_and_finishes():
    plan = P("a", "b", "c", deps={"c": ["a", "b"]})
    # plan(1) a(1) rev a(1) b(1) -> review b refused  (a, b run in parallel: order a, b agents then reviews)
    first, _ = go(Model(plan, quota=4))
    assert first.status == "paused_quota"
    done1 = [t for t in first.tasks.values() if t["status"] == "done"]
    pending_review = [t for t in first.tasks.values() if t.get("unreviewed")]
    assert first.checkpoint["phase"] in ("review", "analysis") and first.checkpoint["stop_reason"] == "quota_exhausted"

    m2 = Model(plan)
    second, saves = go(m2, prior=stored(first))
    assert second.status == "done", second.verdict
    assert "plan" not in m2.kinds  # the approved plan is reused
    agents_rerun = m2.kinds.count("agent")
    # only c (never started) and nothing already written is written again
    assert agents_rerun == 3 - len(done1) - len(pending_review)
    assert all(second.tasks[t["id"]]["reused"] for t in done1)
    ev = next(e for e in second.events if e["type"] == "resumed")
    assert set(ev["review_only"]) == {t["id"] for t in pending_review}
    assert second.files and second.files[0]["verified"] and second.deliverable  # file delivered and checked


def test_quota_during_agent_then_resume_does_not_redo_finished_tasks():
    plan = P("a", "b", deps={"b": ["a"]})
    first, _ = go(Model(plan, quota=3))  # plan, a, review a -> agent b refused
    assert first.status == "paused_quota" and first.tasks["a"]["status"] == "done"
    m2 = Model(plan)
    second, _ = go(m2, prior=stored(first))
    assert m2.kinds.count("agent") == 1 and second.tasks["a"]["reused"] and second.status == "done"
    assert second.tasks["a"]["output"] == first.tasks["a"]["output"]  # agent results are not lost or rewritten


# ---- 2. quota while writing the report ---------------------------------------------------------------------------

def test_quota_during_report_resume_finishes_report_without_any_analysis_call():
    plan = P("a", "b")
    first, saves = go(Model(plan, quota=5))  # plan + 2x(agent+review) -> summary refused
    assert first.status == "paused_quota" and first.pause["step"].startswith("finalization")
    assert all(t["status"] == "done" for t in first.tasks.values())
    saved_ids = {s["id"] for s in first.report["sections"]}
    assert {"task:a", "task:b"} <= saved_ids and "summary" not in saved_ids  # the missing section is known
    assert saves[-1]["report"]["sections"]  # and stored
    m2 = Model(plan)
    second, _ = go(m2, prior=stored(first))
    assert m2.kinds == ["report", "review", "figures"]  # summary + final audit only: no plan, no agent, no task review
    assert second.status == "done" and second.result.startswith("الجواب النهائي المجمع")
    assert "ناتج مهمة-a" in second.result and "ناتج مهمة-b" in second.result


def test_quota_during_final_review_resume_reuses_written_summary():
    plan = P("a")
    first, _ = go(Model(plan, quota=4))  # plan, a, review a, summary -> final review refused
    assert first.status == "paused_quota" and first.pause["step"] == "final_review"
    assert any(s["id"] == "summary" for s in first.report["sections"])
    m2 = Model(plan)
    second, _ = go(m2, prior=stored(first))
    assert m2.kinds == ["review", "figures"]  # only the audit that did not run
    assert second.status == "done" and second.llm_public  # noqa
    assert any(e["type"] == "section_reused" for e in second.events)


# ---- 3. old runs (production 84cca6feac24 shape) -----------------------------------------------------------------

def _old_task(tid, status, attempts, output="", deps=()):
    return {"id": tid, "title": tid, "role": "writer", "instructions": f"مهمة-{tid}", "acceptance": "ok",
            "depends_on": list(deps), "decompose": False, "status": status, "output": output, "attempts": attempts,
            "depth": 0, "parent": None, "best_attempt": 1 if output else None}


def _att(score, out, issues=("ناقص",)):
    return {"agent": "w#1", "steps": [], "confidence": 0.5, "sources": [], "read": [], "output": out,
            "verdict": {"pass": False, "score": score, "issues": list(issues)}}


QUOTA_VERDICT = ["تعذر التحقق: فشل استدعاء النموذج بعد عدة محاولات: HTTP 402: FREE_LIMIT 100 replies a day per IP"]


def test_resume_of_84cca6feac24_shape_keeps_settled_and_reviews_quota_victims():
    prior = {"id": "84cca6feac24", "goal": "ادرس السوق", "status": "failed", "events": [{"type": "plan", "scope": "root", "simple": False}],
             "research_state": None,
             "tasks": {"t1": _old_task("t1", "done", [{**_att(8, "ت1"), "verdict": {"pass": True, "score": 8, "issues": []}}], "ت1"),
                       "t2": _old_task("t2", "failed", [_att(5, "ت2-1"), _att(6, "ت2-2"), _att(4, "ت2-3")], "ت2-2", ["t1"]),
                       "t4": _old_task("t4", "failed", [_att(5, "ت4-1"), _att(0, "ت4-2", QUOTA_VERDICT), _att(0, "ت4-3", QUOTA_VERDICT)],
                                       "ت4-1", ["t1", "t2"])}}
    assert unreviewed(prior["tasks"]["t4"]["attempts"][1]) and not unreviewed(prior["tasks"]["t2"]["attempts"][0])
    m = Model(P("t1"))
    run, _ = go(m, prior=prior)
    assert "plan" not in m.kinds
    assert run.tasks["t2"]["status"] == "failed" and run.tasks["t2"]["reused"] and run.tasks["t2"]["output"] == "ت2-2"  # settled: not rerun
    t4 = run.tasks["t4"]
    assert t4["status"] == "done" and t4["output"] == "ت4-3"  # the quota victim was reviewed, not rewritten
    assert m.kinds.count("agent") == 0  # no agent call at all
    assert len([a for a in t4["attempts"] if unreviewed(a)]) == 0 and len(t4["attempts"]) == 2


# ---- 4. reviewer reuse and its limits -----------------------------------------------------------------------------

def test_unchanged_input_is_not_reviewed_twice_but_changed_input_is():
    m = Model(P("a"))
    o = Orchestrator(Run(goal="g", llm_public={}), m)
    t = {"id": "a", "instructions": "x", "acceptance": "ok"}
    asyncio.run(o.review(t, "ناتج", "g"))
    asyncio.run(o.review(t, "ناتج", "g"))
    assert m.kinds.count("review") == 1 and m.usage.avoided == 1
    asyncio.run(o.review(t, "ناتج آخر", "g"))
    asyncio.run(o.review(t, "ناتج", "g", evidence_text="دليل جديد"))  # new evidence: reviewed again
    assert m.kinds.count("review") == 3


def test_a_rejection_is_never_turned_into_a_pass_by_reuse():
    bad = {"pass": False, "score": 3, "issues": ["رقم مخمن"]}
    m = Model(P("a"), verdicts=[bad, bad])
    run, _ = go(m)
    assert run.tasks["a"]["status"] == "failed"
    assert run.status != "done"


def test_unusable_review_reply_is_asked_again_not_counted_as_rejection():
    class Flaky(Model):
        n = 0

        async def chat(self, messages, temperature=0.3, max_tokens=2000):
            if messages[0]["content"].startswith("أنت مدقق") and not Flaky.n:
                Flaky.n += 1
                self.usage.calls += 1
                return "ليس JSON"
            return await super().chat(messages, temperature, max_tokens)
    m = Flaky(P("a"))
    run, _ = go(m)
    assert run.tasks["a"]["status"] == "done" and len(run.tasks["a"]["attempts"]) == 1


# ---- 5. report from saved sections, delivery -----------------------------------------------------------------------

def test_report_is_rebuilt_from_saved_sections():
    run, _ = go(Model(P("a", "b")))
    assert run.status == "done"
    assert R.assemble(run.report["sections"]) == run.result
    kinds = [s["kind"] for s in run.report["sections"]]
    assert kinds[0] == "summary" and kinds[-2:] == ["limits", "sources"] and kinds.count("task") == 2
    assert all(s["by"] == "code" for s in run.report["sections"] if s["kind"] != "summary")  # model writes the summary only


def test_failed_section_is_kept_with_a_banner_not_lost():
    bad = {"pass": False, "score": 3, "issues": ["ينقصه مصدر"]}
    m = Model(P("a", "b"), verdicts=[{"pass": True, "score": 9, "issues": []}, bad, {**bad, "score": 2}])
    run, _ = go(m)
    sec = {s["task"]: s for s in run.report["sections"] if s["kind"] == "task"}
    assert set(sec) == {"a", "b"}
    failed = next(s for s in sec.values() if s["status"] == "unverified")
    assert "لم يجتز المراجعة" in run.result and failed["text"] in run.result


def test_rejected_report_revises_only_the_located_part():
    class Rej(Model):
        finals = 0

        async def chat(self, messages, temperature=0.3, max_tokens=2000):
            if messages[0]["content"].startswith("أنت مدقق") and "المهمة: ادرس" in messages[1]["content"]:
                Rej.finals += 1
                if Rej.finals == 1:
                    self.usage.calls += 1
                    self.kinds.append("review")
                    return json.dumps({"pass": False, "score": 5, "issues": ["الملخص لا يذكر المخاطر"]})
            if messages[0]["content"].startswith("أنت المنسق. اجمع") and len(messages) > 2:  # the revision request
                self.usage.calls += 1
                self.kinds.append("report")
                return "الجواب النهائي المجمع مع المخاطر"
            return await super().chat(messages, temperature, max_tokens)
    m = Rej(P("a", "b"))
    run, _ = go(m)
    assert run.report["revised"] == ["summary"]
    assert m.kinds.count("agent") == 2 and m.kinds.count("report") == 2  # summary rewritten once; tasks untouched
    assert run.status == "done" and run.result.startswith("الجواب النهائي المجمع مع المخاطر")


def test_identical_rewrite_keeps_the_rejection():
    """A 'revision' that changes nothing is not audited again into a pass: the stored rejection stands."""
    class Same(Model):
        async def chat(self, messages, temperature=0.3, max_tokens=2000):
            if messages[0]["content"].startswith("أنت مدقق") and "المهمة: ادرس" in messages[1]["content"]:
                self.usage.calls += 1
                self.kinds.append("review")
                return json.dumps({"pass": False, "score": 5, "issues": ["الملخص لا يذكر المخاطر"]})
            return await super().chat(messages, temperature, max_tokens)
    m = Same(P("a"))
    run, _ = go(m)
    assert run.status == "partial" and not run.verdict["pass"]
    assert m.kinds.count("review") == 2  # task review + one final review; the unchanged report was not re-asked


def test_not_done_until_the_file_exists(monkeypatch):
    def broken(run):
        raise OSError("disk full")
    monkeypatch.setattr(export, "to_markdown", broken)
    run, _ = go(Model(P("a")))
    assert run.status == "partial" and run.files and run.files[0]["verified"] is False and not run.deliverable
    assert any("الملف النهائي" in i for i in run.verdict["issues"])


def test_file_record_detects_tampering():
    rec = R.file_record("r.md", "# تقرير\nنص")
    assert R.verify_file(rec, "# تقرير\nنص", ["# تقرير"])
    assert not R.verify_file(rec, "# تقرير\nنص آخر", [])
    assert not R.verify_file(rec, "", [])


# ---- 6. checkpoint contents and accounting -------------------------------------------------------------------------

def test_checkpoint_holds_structured_state():
    first, saves = go(Model(P("a", "b"), quota=4))
    cp = first.checkpoint
    for k in ("goal", "plan", "research", "analysis", "reviews", "report", "files", "phase", "next_step", "calls",
              "stop_reason", "saved_at", "status"):
        assert k in cp, k
    assert cp["plan"] and set(cp["analysis"]) == {"a", "b"}
    assert saves[-1]["checkpoint"]["stop_reason"] == "quota_exhausted"
    assert len(saves) >= 3  # saved after each phase/task, not only at the end


def test_call_accounting_counts_avoided_work():
    plan = P("a", "b")
    first, _ = go(Model(plan, quota=5))
    m2 = Model(plan)
    m2.usage.load(first.usage)
    second, _ = go(m2, prior=stored(first))
    u = second.usage
    assert u["carried"] == first.usage["calls"] == 5
    assert u["calls"] == 5 + 3  # the resumed part cost three replies: summary, final review, figures check
