#!/usr/bin/env python3
"""R-131: compare two run_r131_http_load.py reports by message class.

Classes are by what the handler really does (measured at concurrency 1 after
the fix: 6.2-6.3 s = one keyless LLM call; 0.00 s = local):
  local      : sum / validate JSON / colour palette / review code (no network)
  network    : reachability check (real DNS/TCP/HTTP to example.com)
  llm_first  : explain (educator -> web fallback), table schema (db_manager -> local fallback)
  llm_only   : draft a message, generate code from a description
"""
import json
import sys

CLASSES = {
    "local": ("sum these", "validate this", "pick a dark", "review this"),
    "network": ("check if",),
    "llm_first": ("explain", "design a table"),
    "llm_only": ("draft a message", "generate a python"),
}


def cls_of(msg):
    for c, pre in CLASSES.items():
        if msg.startswith(pre):
            return c
    return "?"


def q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else None


def table(path):
    d = json.load(open(path, encoding="utf-8"))
    out = {}
    for lv in d["levels"]:
        for c in CLASSES:
            rs = [r for r in lv["rows"] if cls_of(r["message"]) == c]
            lat = [r["latency_s"] for r in rs]
            ok = sum(r["ok"] for r in rs)
            out[(lv["concurrency"], c)] = (len(rs), ok, q(lat, .5), q(lat, .95), max(lat) if lat else None,
                                           round(ok / lv["wall_s"] * 60, 1), lv["wall_s"])
    return out


b, a = table(sys.argv[1]), table(sys.argv[2])
print(f"{'C':>3} {'class':10} | {'before n/ok':>12} {'p50':>7} {'p95':>7} {'max':>7} {'ok/min':>6} | "
      f"{'after n/ok':>12} {'p50':>7} {'p95':>7} {'max':>7} {'ok/min':>6}")
for key in sorted(b):
    x, y = b[key], a.get(key)
    def f(t):
        if not t or not t[0]:
            return f"{'0/0':>12} {'-':>7} {'-':>7} {'-':>7} {'-':>6}"
        return f"{t[0]:>5}/{t[1]:<6} {t[2]:7.2f} {t[3]:7.2f} {t[4]:7.2f} {t[5]:6.1f}"
    print(f"{key[0]:>3} {key[1]:10} | {f(x)} | {f(y)}")
print("\nwall seconds per level (before / after):",
      {k[0]: (b[k][6], a[k][6]) for k in b if k[1] == "local"})
