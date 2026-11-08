"""R-178 (D-139): عدالة اختيار الوكيل في مسار /api/tasks.

أداة سلّم R-178 (benchmarks/r178/ladder.py) كشفت في تشغيل L0 أن وكيلًا واحدًا
نفذ 100/100 مهمة: `_pick_agent` كانت تعيد أول وكيل خامل دائمًا — فتتركز كل
مهام التخصص في وكيل واحد، ومعيار عدالة R-178 (Jain ≥ 0.90 داخل التخصص)
يصير مستحيلًا من المسار الرئيسي. هذا الاختبار يثبت العيب ويحمي الإصلاح
(اختيار الأقل إنجازًا بين الخاملين — نفس قاعدة مسار الوكلاء الخارجيين).

اختبار توصيل فعلي عبر عملية مستقلة (النمط المعتمد في test_agents_per_spec):
`server` يبني CIVILIZATION وقت الاستيراد فلا يعاد تحميله داخل جلسة pytest.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PROBE = """
import json, server
pool = server.CIVILIZATION["validator"]
names = [a.name for a in pool]
# محاكاة وكيل منهك: أول وكيل أنهى 5 مهام والباقي صفر
pool[0].stats["tasks_completed"] = 5
# حلقة الاختيار الحقيقية: اختر ثم نفذ (إحصاء الوكيل يتحرك بعد كل مهمة)
picks = []
by_name = {a.name: a for a in pool}
for _ in range(30):
    agent = server._pick_agent("validator")
    picks.append(agent.name)
    by_name[agent.name].stats["tasks_completed"] += 1
print(json.dumps({"names": names, "picks": picks}))
"""


def _probe():
    env = dict(os.environ)
    env["AGENTS_PER_SPEC"] = "3"
    env.setdefault("AGOS_ENV", "test")
    return subprocess.run([sys.executable, "-c", PROBE], capture_output=True,
                          text=True, cwd=str(ROOT), env=env)


def test_pick_agent_does_not_starve_the_pool_when_one_agent_leads():
    result = _probe()
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    picks = data["picks"]
    # وكيل واحد أنهى 5 والمختارون عشرة: تعليق كل الاختيارات على الوكيل
    # المتقدم (سلوك ما قبل الإصلاح: [idle or pool][0] دائمًا) يُفشل هذا.
    leader = data["names"][0]
    assert picks[:10].count(leader) == 0, (
        f"the leading agent monopolized {picks[:10].count(leader)}/10 first picks — "
        "least-completed selection is broken")


def test_pick_agent_spreads_picks_across_idle_agents():
    result = _probe()
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    picks = data["picks"]
    # توزيع فعلي بين الخاملين: أكثر من وكيل يستقبل عملًا
    assert len(set(picks)) >= 2, f"all picks went to one agent: {picks}"
    # لا وكيل يُجَّع تمامًا بينما غيره يعمل (كل الأسماء تظهر ضمن العشرة)
    assert set(picks) == set(data["names"]), (
        f"some idle agent never picked: picks={picks} names={data['names']}")


def test_unknown_specialization_still_404():
    env = dict(os.environ)
    env["AGENTS_PER_SPEC"] = "1"
    env.setdefault("AGOS_ENV", "test")
    probe = "import server, fastapi\n" \
            "try:\n    server._pick_agent('nonexistent-spec')\n" \
            "    print('no-raise')\n" \
            "except fastapi.HTTPException as e:\n    print(e.status_code)\n"
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                            text=True, cwd=str(ROOT), env=env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "404"
