#!/usr/bin/env python3
"""SEC-06 (R-145) evidence probe: public-LLM egress gates, before/after.

Run against a pristine tree (before) and the fixed tree (after):

    python scripts/sec06/probe_public_llm.py

What it proves, in a FRESH interpreter with a scrubbed environment (no real
internet is touched — the transport is replaced by a local counting fake):

* which provider is selected with no key and no AGOS_LLM_KEYLESS;
* whether complete() reaches the transport without per-request consent
  (a call count of 1 = the request was built and dispatched to the transport
  layer; it is NOT a real internet send — the transport is a local fake);
* that an ambiguous AGOS_LLM_KEYLESS value fails startup instead of being
  interpreted as an enable.

Exit code 0 always: the probe reports reality; the assertions live in
tests/test_sec06_public_llm_consent.py.
"""
import os
import subprocess
import sys
from pathlib import Path

# The tree under test: the repo this script lives in, or an explicit target
# (e.g. a pristine worktree of the pre-fix commit) given as argv[1].
REPO = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[2]

PROBE = r"""
import asyncio, os
calls = []
from agent_civilization.core import llm as L

def fake_post(system, prompt, max_tokens, timeout):
    calls.append({"system": system, "prompt": prompt})
    return {"choices": [{"message": {"content": "```python\ndef probe():\n    return 1\n```"}}],
            "model": "fake"}

L._keyless_post = fake_post
L.KEYLESS_MIN_SPACING_S = 0.0
L._keyless_lock = None
L._keyless_waiting = 0

print("provider:", L.get_llm().provider)
print("endpoint:", L.KEYLESS_URL if hasattr(L, "KEYLESS_URL") else getattr(L, "KEYLESS_ENDPOINT", "?"))
async def go():
    return await L.get_llm().complete("system probe", "user probe")
r = asyncio.run(go())
print("no-consent complete ok:", r["ok"])
print("transport calls without consent:", len(calls))
token = L.set_public_llm_consent(True)
try:
    r2 = asyncio.run(go())
finally:
    L.reset_public_llm_consent(token)
print("with-consent complete ok:", r2["ok"])
print("transport calls with consent:", len(calls))
"""


def _fresh_env(**extra):
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", "/tmp"),
        "AGOS_ENV": "test",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    env.update(extra)
    return env


def run_probe(label, **extra):
    r = subprocess.run([sys.executable, "-c", PROBE], env=_fresh_env(**extra),
                       cwd=str(REPO), capture_output=True, text=True, timeout=120)
    print(f"--- {label} (rc={r.returncode}) ---")
    print(r.stdout.strip())
    if r.returncode != 0:
        print("STDERR:", r.stderr.strip()[-300:])
    print()


if __name__ == "__main__":
    print(f"tree under test: {REPO}")
    run_probe("no key, no AGOS_LLM_KEYLESS (operator default)")
    run_probe("AGOS_LLM_KEYLESS=1 (explicit opt-in)", AGOS_LLM_KEYLESS="1")
    run_probe("AGOS_LLM_KEYLESS=on (ambiguous value)", AGOS_LLM_KEYLESS="on")
