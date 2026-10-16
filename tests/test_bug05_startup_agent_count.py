"""BUG-05 (R-147): the startup log reports the ACTUAL agent count.

The startup log is emitted at IMPORT time of ``server`` (module level), so
every test here runs ``import server`` in a CLEAN SUBPROCESS with a
controlled environment — the assertions read the ACTUAL captured log lines,
never a copy of the production formula, and never depend on ``server``
having been imported (or not) earlier in this process.

Conventions (conftest's offline guard does not cross into the subprocess):
the subprocess gets a minimal environment (``AGOS_ENV=test`` so the server
may start without credentials, SEC-03), no operational settings, and no
external connections.
"""

import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CIV_LINE = re.compile(
    r"Civilization initialized: (\d+) specializations, (\d+) civilization agents")
_EXT_LINE = re.compile(r"External agents loaded: (\d+)")


def _startup_log(env_extra: dict | None = None,
                 setup: str | None = None) -> dict:
    """Import ``server`` in a clean subprocess; return the parsed log lines.

    ``setup`` (optional) runs INSIDE the subprocess BEFORE ``import server``
    — it patches the civilization builder / specialization table with
    controlled data so uneven spec sizes can be proven.
    """
    script = f"""
{setup or ""}
import server
"""
    # capture stderr (server's basicConfig writes there)
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "AGOS_ENV": "test",
           "PYTHONPATH": ROOT}
    env.update(env_extra or {})
    proc = subprocess.run(
        [sys.executable, "-c", script],
        env=env, capture_output=True, text=True, timeout=120,
        cwd=ROOT)
    assert proc.returncode == 0, proc.stderr[-2000:]
    civ = _CIV_LINE.search(proc.stderr)
    ext = _EXT_LINE.search(proc.stderr)
    assert civ, proc.stderr[-2000:]
    assert ext, proc.stderr[-2000:]
    return {"specializations": int(civ.group(1)),
            "civilization_agents": int(civ.group(2)),
            "external_agents": int(ext.group(1)),
            "raw_civ": civ.group(0), "raw_ext": ext.group(0)}


def test_startup_log_counts_actual_agents_with_small_agents_per_spec():
    # AGENTS_PER_SPEC=3 with the 20 real specializations: the log must
    # report the ACTUAL 60 civilization agents — the pre-fix log claimed
    # 1000 (specializations * a hardcoded 50).
    out = _startup_log({"AGENTS_PER_SPEC": "3"})
    assert out["specializations"] == 20
    assert out["civilization_agents"] == 60
    assert "1000" not in out["raw_civ"]


def test_startup_log_counts_actual_agents_with_the_default():
    # AGENTS_PER_SPEC UNSET proves the DEFAULT (50), not an explicit 50:
    # 20 specializations * 50 = 1000 actual agents — and the log must
    # report that as the measured sum, not as the hardcoded product.
    out = _startup_log()
    assert out["specializations"] == 20
    assert out["civilization_agents"] == 1000


def test_startup_log_reflects_uneven_spec_sizes():
    # Specializations with DIFFERENT agent counts: the log must report
    # the sum of the actual lists (3 + 5 + 2 = 10), never a count of
    # specializations times a constant.  The builder and the
    # specializations table are fed controlled data inside the subprocess
    # BEFORE ``import server``.
    setup = (
        "import agent_civilization.agents.real as real\n"
        "class _A:\n"
        "    pass\n"
        "real.SPECIALIZATIONS = {'alpha': _A, 'beta': _A, 'gamma': _A}\n"
        "def _civ(n=50, impl=None):\n"
        "    return {'alpha': [_A()] * 3, 'beta': [_A()] * 5, 'gamma': [_A()] * 2}\n"
        "real.create_civilization = _civ\n"
    )
    out = _startup_log({"AGENTS_PER_SPEC": "3"}, setup=setup)
    assert out["specializations"] == 3
    assert out["civilization_agents"] == 10


def test_external_agents_stay_on_their_own_line():
    # A nonzero EXTERNAL_AGENTS configuration: the external count is
    # reported on its own separate line (== the configured entries) and is
    # NEVER added to the civilization count.
    agents = json.dumps([
        {"name": "e1", "url": "http://192.0.2.10:1"},
        {"name": "e2", "url": "http://192.0.2.11:1"},
    ])
    out = _startup_log({"AGENTS_PER_SPEC": "3", "EXTERNAL_AGENTS": agents})
    assert out["civilization_agents"] == 60      # unchanged by the externals
    assert out["external_agents"] == 2
