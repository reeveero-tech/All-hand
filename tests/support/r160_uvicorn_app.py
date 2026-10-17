"""R-160 local-integration launcher: the real server app under real uvicorn
on 127.0.0.1 (started by tests/test_r160_local_integration.py, never in
production). One test-only instrumentation: a prompt containing ``R160-SLOW``
makes the analyst agents sleep ``R160_SLOW_S`` seconds first, so a client can
abort while the request is still being served. Everything else is unchanged."""
import asyncio
import os
import sys

import uvicorn

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import server  # noqa: E402

SLOW_S = float(os.environ.get("R160_SLOW_S", "2"))
for agent in server.CIVILIZATION["analyst"]:
    original = agent.execute

    async def slow(task, _orig=original):
        if "R160-SLOW" in str((task or {}).get("prompt", "")):
            await asyncio.sleep(SLOW_S)
        return await _orig(task)
    agent.execute = slow

if __name__ == "__main__":
    uvicorn.run(server.app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
