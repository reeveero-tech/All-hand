#!/usr/bin/env python3
"""R-132: run `server:app` under uvicorn (same app, same settings as the CLI)
and sample the event loop's thread pools once per second.

Writes one JSON line per sample to --out:
  {"t": seconds since start, "pools": {name: {"workers": max, "threads": started,
   "queued": work items waiting for a free thread}}, "lag_ms": event-loop lag}
"default" is the loop's default executor (asyncio.to_thread before R-132);
named pools are read from agent_civilization.core.pools when that module
exists (R-132). Measurement tool only — no product behaviour changes.
"""
import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def pool_state(ex):
    if ex is None:
        return None
    return {"workers": ex._max_workers, "threads": len(ex._threads), "queued": ex._work_queue.qsize()}


async def sampler(out_path, period=1.0):
    loop = asyncio.get_running_loop()
    t0 = time.monotonic()
    try:
        from agent_civilization.core import pools as named  # R-132
    except ImportError:
        named = None
    with open(out_path, "w", encoding="utf-8") as f:
        while True:
            t = time.monotonic()
            await asyncio.sleep(period)
            lag = (time.monotonic() - t - period) * 1000
            row = {"t": round(time.monotonic() - t0, 1), "lag_ms": round(lag, 1),
                   "pools": {"default": pool_state(getattr(loop, "_default_executor", None))}}
            if named is not None:
                for name, ex in named.snapshot().items():
                    row["pools"][name] = pool_state(ex)
            f.write(json.dumps(row) + "\n")
            f.flush()


async def main(port, out):
    import uvicorn
    import server  # noqa: F401 — same module the CLI loads
    config = uvicorn.Config("server:app", host="127.0.0.1", port=port, log_level="warning")
    srv = uvicorn.Server(config)
    task = asyncio.create_task(sampler(out))
    try:
        await srv.serve()
    finally:
        task.cancel()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    print(f"cpu_count={os.cpu_count()} python={sys.version.split()[0]}", flush=True)
    asyncio.run(main(a.port, a.out))
