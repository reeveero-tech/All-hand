#!/usr/bin/env python3
"""R-131 (RISK-028): real concurrent HTTP load against a real server process.

Starts `uvicorn server:app` as a separate process on a free local port (the
same app a deployment runs), then for each concurrency level C runs C client
loops for --level-s seconds; each loop POSTs /api/chat messages back-to-back
(one in flight per client) and records, per request: HTTP status, latency,
outcome.ok, the routing method (llm / keywords), the specialization, whether
the request class needs an LLM, and the error text when it failed.

Message mix (fixed, round-robin from a per-client offset):
  * needs an LLM : explain / draft a message / generate code from a description
  * no LLM needed: sum numbers, validate JSON, colour palette, reachability,
                   SQL schema, review code
The keyless LLM is the only LLM (no OPENROUTER_API_KEY), exactly the default.
The per-IP rate limit (100/60 s) is raised for the run — every client here
shares 127.0.0.1, unlike real users — and the value is printed.

Usage: python scripts/run_r131_http_load.py --levels 1,10,25,50 --level-s 120 --out report.json
"""
import argparse
import asyncio
import json
import os
import socket
import statistics
import subprocess
import sys
import time
from pathlib import Path

import aiohttp

REPO = Path(__file__).resolve().parent.parent

MESSAGES = [
    # (message, needs_llm)
    ("explain the difference between REST and GraphQL", True),
    ("sum these numbers: [1, 2, 3, 4, 5]", False),
    ("draft a message to the team about the outage", True),
    ('validate this JSON: {"name": "string", "age": 3}', False),
    ("pick a dark mode color palette from #3366ff for the dashboard", False),
    ("generate a python function that returns the nth prime", True),
    ("check if example.com is reachable", False),
    ("design a table schema for orders with line items", False),
    ("review this code for bugs: def f(x): return x*2", False),
]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def pct(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))], 2)


def summarise(rows):
    out = {}
    for cls in ("needs_llm", "no_llm", "all"):
        rs = [r for r in rows if cls == "all" or r["needs_llm"] == (cls == "needs_llm")]
        lat = [r["latency_s"] for r in rs]
        out[cls] = {
            "requests": len(rs),
            "http_200": sum(1 for r in rs if r["status"] == 200),
            "http_other": sorted({r["status"] for r in rs if r["status"] != 200}),
            "ok": sum(1 for r in rs if r["ok"]),
            "p50_s": pct(lat, 50), "p95_s": pct(lat, 95), "max_s": round(max(lat), 2) if lat else None,
            "mean_s": round(statistics.mean(lat), 2) if lat else None,
            "routed_by_llm": sum(1 for r in rs if r["routing"] == "llm"),
        }
    errs = {}
    for r in rows:
        if not r["ok"]:
            k = (r["error"] or "")[:90]
            errs[k] = errs.get(k, 0) + 1
    out["errors"] = dict(sorted(errs.items(), key=lambda kv: -kv[1]))
    return out


async def client(session, base, cid, deadline, rows):
    i = cid
    while time.monotonic() < deadline:
        msg, needs = MESSAGES[i % len(MESSAGES)]
        i += 1
        t0 = time.monotonic()
        row = {"client": cid, "message": msg, "needs_llm": needs, "status": None, "ok": False,
               "routing": None, "spec": None, "error": None}
        try:
            async with session.post(f"{base}/api/chat", json={"message": msg},
                                    timeout=aiohttp.ClientTimeout(total=300)) as resp:
                row["status"] = resp.status
                body = await resp.json(content_type=None)
                if resp.status == 200:
                    oc = body.get("outcome") or {}
                    row["ok"] = bool(oc.get("ok"))
                    row["routing"] = (body.get("routing") or {}).get("method")
                    row["spec"] = body.get("routed_to")
                    res = oc.get("result") if isinstance(oc.get("result"), dict) else oc
                    if not row["ok"]:
                        row["error"] = str(res.get("error") or oc.get("error") or "")[:240]
                else:
                    row["error"] = f"HTTP {resp.status}: {str(body)[:200]}"
        except Exception as e:  # noqa: BLE001
            row["error"] = f"{type(e).__name__}: {e}"[:240]
        row["latency_s"] = round(time.monotonic() - t0, 3)
        row["t_s"] = round(t0 - T0, 1)
        rows.append(row)


async def run_level(base, c, level_s):
    rows = []
    deadline = time.monotonic() + level_s
    conn = aiohttp.TCPConnector(limit=0)
    async with aiohttp.ClientSession(connector=conn) as session:
        await asyncio.gather(*(client(session, base, k, deadline, rows) for k in range(c)))
    return rows


async def stats(base):
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{base}/api/system/status") as r:
            return r.status


async def main_async(args):
    global T0
    port = free_port()
    env = dict(os.environ)
    env.pop("OPENROUTER_API_KEY", None)
    env.setdefault("AGOS_RATE_LIMIT_REQUESTS", "1000000")
    env.pop("AGOS_API_KEY", None)
    log = open(args.server_log, "w", encoding="utf-8") if args.server_log else subprocess.DEVNULL
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "server:app", "--host", "127.0.0.1",
                             "--port", str(port), "--log-level", "warning"],
                            cwd=str(REPO), env=env, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                if await stats(base) == 200:
                    break
            except Exception:  # noqa: BLE001
                await asyncio.sleep(0.2)
        else:
            raise SystemExit("server did not start")
        print(f"server pid={proc.pid} {base} | keyless LLM on (no OPENROUTER_API_KEY) | "
              f"AGOS_RATE_LIMIT_REQUESTS={env['AGOS_RATE_LIMIT_REQUESTS']} | "
              f"AGOS_LLM_KEYLESS_MAX_WAIT_S={env.get('AGOS_LLM_KEYLESS_MAX_WAIT_S', '(default)')}", flush=True)
        report = {"levels": [], "level_s": args.level_s, "messages": MESSAGES}
        T0 = time.monotonic()
        for c in [int(x) for x in args.levels.split(",")]:
            t = time.monotonic()
            rows = await run_level(base, c, args.level_s)
            s = summarise(rows)
            wall = round(time.monotonic() - t, 1)
            report["levels"].append({"concurrency": c, "wall_s": wall, "summary": s, "rows": rows})
            print(f"\n== concurrency {c}: {len(rows)} requests in {wall}s", flush=True)
            for cls in ("needs_llm", "no_llm", "all"):
                x = s[cls]
                print(f"  {cls:9s} n={x['requests']:4d} http200={x['http_200']:4d} other={x['http_other']} "
                      f"ok={x['ok']:4d} p50={x['p50_s']}s p95={x['p95_s']}s max={x['max_s']}s "
                      f"routed_by_llm={x['routed_by_llm']}", flush=True)
            for e, n in list(s["errors"].items())[:6]:
                print(f"  fail x{n}: {e}", flush=True)
            await asyncio.sleep(args.pause_s)
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nreport: {args.out}", flush=True)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


T0 = time.monotonic()

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--levels", default="1,10,25,50")
    ap.add_argument("--level-s", type=float, default=120)
    ap.add_argument("--pause-s", type=float, default=10)
    ap.add_argument("--out", required=True)
    ap.add_argument("--server-log", default=None)
    asyncio.run(main_async(ap.parse_args()))
