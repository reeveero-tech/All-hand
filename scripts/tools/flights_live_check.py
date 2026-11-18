#!/usr/bin/env python3
"""R-206b (D-166): live check of the flights proxy against the real providers.

Runs OUTSIDE pytest (the suite's R-072 network guard blocks outbound calls).
Calls the same FlightsClient the server uses, against adsb.lol and adsb.fi, and
prints one JSON line per check. A fallback check points the primary at a path
that does not exist on adsb.lol, so the real adsb.fi answers.

Usage:
    python3 scripts/tools/flights_live_check.py            # in-process client
    python3 scripts/tools/flights_live_check.py --url https://all-hand.onrender.com

Exit 1 if any check fails.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from agos_v2 import flights as fl  # noqa: E402

CHECKS = [("point", {"lat": 51.47, "lon": -0.45, "radius": 40}), ("mil", {}), ("type", {"q": "A388"})]


def line(**kw):
    print(json.dumps(kw, ensure_ascii=False), flush=True)


async def in_process() -> int:
    bad = 0
    c = fl.FlightsClient()
    for mode, params in CHECKS:
        r = await c.query(mode, params)
        line(check=f"{mode} via primary", ok=r.get("ok"), provider=r.get("provider"), count=r.get("count"),
             errors=r.get("errors"), sample=(r.get("aircraft") or [{}])[0].get("hex"))
        bad += not (r.get("ok") and r.get("provider") == "adsb.lol")
    r = await c.query("point", CHECKS[0][1])
    line(check="repeat served from cache", cached=r.get("cached"), upstream_calls=c.upstream_calls)
    bad += not r.get("cached")
    saved = fl.PROVIDERS["adsb.lol"]["paths"]["point"]
    fl.PROVIDERS["adsb.lol"]["paths"]["point"] = "/v2/no-such-endpoint/{lat}/{lon}/{radius}"
    try:
        c2 = fl.FlightsClient()
        r = await c2.query("point", {"lat": 60.32, "lon": 24.95, "radius": 60})
    finally:
        fl.PROVIDERS["adsb.lol"]["paths"]["point"] = saved
    line(check="fallback when primary answers an error", ok=r.get("ok"), provider=r.get("provider"),
         fallback_used=r.get("fallback_used"), count=r.get("count"), errors=r.get("errors"))
    bad += not (r.get("ok") and r.get("provider") == "adsb.fi" and r.get("fallback_used"))
    return bad


def deployed(base: str) -> int:
    bad = 0
    for qs, want in [("mode=point&lat=51.47&lon=-0.45&radius=40", 200), ("mode=mil", 200),
                     ("mode=callsign&q=a%2Fb", 400), ("mode=point&lat=95&lon=0", 400)]:
        url = f"{base.rstrip('/')}/api/v2/flights?{qs}"
        req = urllib.request.Request(url, headers={"User-Agent": "agos-live-check"})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
                code, body = resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            code, body = exc.code, json.loads(exc.read() or b"{}")
        line(check=f"GET {qs}", status=code, ms=int((time.time() - t0) * 1000), provider=body.get("provider"),
             count=body.get("count"), fallback_used=body.get("fallback_used"),
             errors=body.get("errors"), detail=body.get("detail"))
        bad += code != want or (want == 200 and not body.get("ok"))
        time.sleep(1.1)
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url")
    a = ap.parse_args()
    bad = deployed(a.url) if a.url else asyncio.run(in_process())
    line(result="pass" if bad == 0 else "fail", failures=bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
