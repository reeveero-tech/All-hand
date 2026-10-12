"""R-041: integration test for the AGENTS_PER_SPEC wiring.

The advisor rule for this item: test the actual wiring, not just the helper.
A separate Python process imports the server module with different values of
AGENTS_PER_SPEC and asserts the resulting civilization size. Subprocesses are
used because `server` builds module-level state (CIVILIZATION) at import time
and should not be reloaded inside a pytest session.
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
