"""R-041: اختبار توصيل AGENTS_PER_SPEC.

القاعدة: اختبار التوصيل الفعلي لا الدالة المساعدة فقط. عملية Python
مستقلة تستورد وحدة الخادم بقيم مختلفة من AGENTS_PER_SPEC وتتأكد من حجم
الحضارة الناتج. تُستخدم عمليات مستقلة لأن `server` يبني حالة على مستوى
الوحدة (CIVILIZATION) وقت الاستيراد ولا ينبغي إعادة تحميله داخل جلسة pytest.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PROBE = """
import json
import server
specs = len(server.SPECIALIZATIONS)
total = sum(len(pool) for pool in server.CIVILIZATION.values())
print(json.dumps({"specializations": specs, "total_agents": total,
                  "per_spec": server.AGENTS_PER_SPEC}))
"""


def _run_server_probe(env_value):
    """تشغيل مسبار استيراد الخادم بقيمة متغير محددة."""
    env = dict(os.environ)
    env.pop("AGENTS_PER_SPEC", None)
    if env_value is not None:
        env["AGENTS_PER_SPEC"] = env_value
    result = subprocess.run(
        [sys.executable, "-c", PROBE],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env=env,
    )
    return result


def test_default_no_env_creates_1000_agents():
    result = _run_server_probe(None)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["specializations"] == 20
    assert data["per_spec"] == 50
    assert data["total_agents"] == 1000


def test_env_3_creates_60_agents():
    result = _run_server_probe("3")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["per_spec"] == 3
    assert data["total_agents"] == 60


def test_invalid_values_rejected():
    for bad in ("0", "-1", "not-a-number"):
        result = _run_server_probe(bad)
        assert result.returncode != 0, f"AGENTS_PER_SPEC={bad!r} was accepted"
        assert "AGENTS_PER_SPEC" in result.stderr
