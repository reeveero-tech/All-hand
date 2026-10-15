#!/usr/bin/env python3
"""R-121 evidence: does building a TLS context per request leave freed-but-held
heap memory? Compares N x ssl.create_default_context() (what urllib does for
every HTTPS request without an explicit context) against reusing one context.
Offline — no connection is made."""
import ctypes
import gc
import ssl
import sys


class MallInfo2(ctypes.Structure):
    _fields_ = [(n, ctypes.c_size_t) for n in (
        "arena", "ordblks", "smblks", "hblks", "hblkhd", "usmblks", "fsmblks", "uordblks", "fordblks", "keepcost")]


libc = ctypes.CDLL("libc.so.6")
libc.mallinfo2.restype = MallInfo2


def rss():
    return next(int(l.split()[1]) for l in open("/proc/self/status") if l.startswith("VmRSS:")) / 1024


mode, n = sys.argv[1], int(sys.argv[2])
shared = ssl.create_default_context()
keep = []
r0, f0 = rss(), libc.mallinfo2().fordblks / 1048576
for i in range(n):
    ctx = ssl.create_default_context() if mode == "per-call" else shared
    keep.append(bytearray(4096))  # a small long-lived allocation between calls (like real traffic)
    del ctx
gc.collect()
r1, f1 = rss(), libc.mallinfo2().fordblks / 1048576
print(f"{mode:9s} n={n} | rss {r0:6.1f} -> {r1:6.1f} ({r1 - r0:+6.1f}) MB | free-but-held {f0:5.1f} -> {f1:5.1f} MB")
