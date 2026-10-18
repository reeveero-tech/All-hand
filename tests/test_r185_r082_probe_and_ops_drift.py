"""R-185 (Q-026): two documented drifts between the docs/tools and reality.

1. ``scripts/run_r082_storage_failure_probe.py`` stopped measuring what it was
   written for after SEC-02/SEC-03: production start refused it, and with
   ``AGOS_ENV=development`` every tenant route answered 401 before storage,
   so all three phases printed ``storage=healthy`` (M8 raw log 127 §E). The
   probe now configures a probe-only tenant credential, and exits 1 when it
   is not reaching storage. These tests run the real script in a fresh
   process (loopback only) and check the R-082 transitions.
2. ``OPERATIONS.md`` named ``PYTHON_VERSION=3.12.0`` while ``render.yaml``
   pins ``3.12.15``. The guard below keeps the doc equal to the file.

Evidence class: mock (fake PostgREST on 127.0.0.1 inside the probe).
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "scripts" / "run_r082_storage_failure_probe.py"


def _clean_env(**extra):
    """A fresh environment: nothing of the developer's or the test session's
    AGOS_*/SUPABASE_*/RENDER settings reaches the probe."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("AGOS_", "SUPABASE", "VITE_", "OPENROUTER", "R161_")) and k != "RENDER"}
    env.update(extra)
    return env


def _run(script_text=None, **extra):
    args = [sys.executable, str(PROBE)] if script_text is None else [sys.executable, "-c", script_text]
    return subprocess.run(args, cwd=ROOT, env=_clean_env(**extra), capture_output=True,
                          text=True, timeout=180)


def _phases(out):
    """[(header line, health line)] for the three phases."""
    lines = out.splitlines()
    res = []
    for i, ln in enumerate(lines):
        if ln.startswith("[") and "API:" in ln:
            res.append((ln, lines[i + 1]))
    return res


def test_probe_reaches_storage_and_shows_r082_transitions():
    r = _run()
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    ph = _phases(r.stdout)
    assert len(ph) == 3, r.stdout
    for head, _ in ph:
        # the credential is accepted: tenant routes answer, not 401
        assert "POST /api/tasks -> 202" in head and "GET /api/tasks -> 200" in head, head
    reached = [int(re.search(r"reaching the fake storage: (\d+)", h).group(1)) for h, _ in ph]
    assert reached[0] == 0 and reached[1] > 0 and reached[2] > 0, reached
    (_, h1), (_, h2), (_, h3) = ph
    assert "storage=degraded" in h1 and "exception: ClientConnectorError" in h1, h1
    assert "storage=degraded" in h2 and "(HTTP 401)" in h2, h2
    assert "storage=healthy" in h3, h3
    assert "probe reached storage in every phase that expects it" in r.stdout


def test_probe_output_carries_no_credential():
    r = _run()
    text = r.stdout + r.stderr
    assert "key_sha256" not in text and "AGOS_TENANT_KEYS" not in text
    assert "sb_secret_" not in text


def test_probe_self_check_fails_when_the_api_refuses_it():
    """The drift Q-026 found was silent. Run the probe with its credential
    header removed: it must now say it is not measuring and exit 1."""
    src = PROBE.read_text(encoding="utf-8").replace(", headers=HDR)", ")")
    assert src.count("headers=HDR") == 0
    r = _run(f"import sys; sys.argv=[{str(PROBE)!r}]; "
             f"exec(compile({src!r}, {str(PROBE)!r}, 'exec'), "
             f"{{'__file__': {str(PROBE)!r}, '__name__': '__main__'}})")
    assert r.returncode == 1, r.stdout[-2000:]
    assert "PROBE NOT MEASURING" in r.stdout and "refused the probe credential" in r.stdout


def test_probe_starts_in_production_mode_without_agos_env():
    """AGOS_ENV unset = production (SEC-03): the probe no longer needs the
    development escape hatch, because it configures a credential."""
    r = _run()
    assert "production start refused" not in r.stdout + r.stderr
    assert r.returncode == 0


def test_operations_python_version_matches_render_yaml():
    cfg = yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))
    env = {e["key"]: e.get("value") for svc in cfg["services"] for e in svc.get("envVars", [])}
    pinned = env["PYTHON_VERSION"]
    ops = (ROOT / "docs" / "governance" / "OPERATIONS.md").read_text(encoding="utf-8")
    named = re.findall(r"`PYTHON_VERSION=([0-9.]+)`", ops)
    assert named, "OPERATIONS.md must name PYTHON_VERSION"
    assert set(named) == {pinned}, (named, pinned)
