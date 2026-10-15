#!/usr/bin/env python3
"""R-124 evidence: why did C memory pile up as *in use* and get freed in bulk
before the R-121 fix? Hypothesis: the per-request TLS objects sit in reference
cycles, so they are freed only by Python's cyclic GC, not when the request ends.

Runs N real HTTPS requests through safety.safe_urlopen WITHOUT calling the GC,
then counts live ssl.SSLContext / ssl.SSLSocket / urllib OpenerDirector objects
and glibc in-use memory, then runs gc.collect() and counts again. Also prints
the referrer chain of one surviving SSLContext (what keeps it alive).
Usage: r124_late_release.py <n> [url]   (run from a checkout: old or new code)
"""
import ctypes
import gc
import ssl
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))
from agent_civilization.core import safety  # noqa: E402


class MallInfo2(ctypes.Structure):
    _fields_ = [(n, ctypes.c_size_t) for n in (
        "arena", "ordblks", "smblks", "hblks", "hblkhd", "usmblks", "fsmblks", "uordblks", "fordblks", "keepcost")]


libc = ctypes.CDLL("libc.so.6")
libc.mallinfo2.restype = MallInfo2


def inuse():
    m = libc.mallinfo2()
    return (m.uordblks + m.hblkhd) / 1048576


def live():
    objs = gc.get_objects()
    return {"SSLContext": sum(isinstance(o, ssl.SSLContext) for o in objs),
            "SSLSocket": sum(isinstance(o, ssl.SSLSocket) for o in objs),
            "OpenerDirector": sum(isinstance(o, urllib.request.OpenerDirector) for o in objs)}


def chain(obj, depth=4):
    out, cur = [], obj
    for _ in range(depth):
        refs = [r for r in gc.get_referrers(cur) if r is not out and not isinstance(r, list) or
                (isinstance(r, list) and r is not objs_holder)]
        refs = [r for r in refs if type(r).__name__ not in ("frame",)]
        if not refs:
            break
        cur = refs[0]
        out.append(type(cur).__name__ + (f"(keys={list(cur)[:6]})" if isinstance(cur, dict) else ""))
    return out


objs_holder = []
n = int(sys.argv[1])
url = sys.argv[2] if len(sys.argv) > 2 else "https://example.com/"
print(f"python {sys.version.split()[0]} gc thresholds={gc.get_threshold()} code={'shared-context' if hasattr(safety, 'tls_context') else 'per-connection'}")
gc.collect()
gc.disable()  # make the effect of the cyclic GC explicit
base = live(); u0 = inuse()
for _ in range(n):
    with safety.safe_urlopen(urllib.request.Request(url), timeout=10) as r:
        r.read()
mid = live(); u1 = inuse()
ctxs = [o for o in gc.get_objects() if isinstance(o, ssl.SSLContext)]
if len(ctxs) > 1:
    objs_holder.append(ctxs)
    print("referrer chain of one surviving SSLContext:", " <- ".join(chain(ctxs[-1])))
    objs_holder.clear(); del ctxs
freed = gc.collect()
end = live(); u2 = inuse()
print(f"n={n} before: {base} in-use {u0:.1f}MB")
print(f"after {n} requests, no GC: {mid} in-use {u1:.1f}MB ({u1 - u0:+.1f})")
print(f"after gc.collect() (freed {freed} objects): {end} in-use {u2:.1f}MB ({u2 - u0:+.1f})")
