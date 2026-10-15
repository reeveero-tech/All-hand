#!/usr/bin/env python3
"""R-121: before/after table from the two full-workload diag runs (same seed,
same 19 templates, same pacing; base = code before the fix, fixed = after)."""
import json
import sys

runs = {}
for path in sys.argv[1:]:
    d = json.load(open(path, encoding="utf-8"))
    runs[d["label"]] = {round(s["t_s"] / 60): s for s in d["samples"]}
labels = list(runs)
print("minute | " + " | ".join(f"{lb}: rss / in-use / free-held / traced (MB), verified/fail" for lb in labels))
for m in sorted(set().union(*[set(r) for r in runs.values()])):
    if m % 5 and m != 45:
        continue
    cells = []
    for lb in labels:
        s = runs[lb].get(m)
        cells.append("-" if not s else f"{s['vmrss_mb']:6.1f} / {s['malloc_inuse_mb']:6.1f} / {s['malloc_free_held_mb']:6.1f} / "
                     f"{s['traced_mb']:5.1f}, {s['verified']}/{s['fail']}")
    print(f"{m:6d} | " + " | ".join(cells))
for lb in labels:
    last = runs[lb][max(runs[lb])]
    print(f"final {lb}: rss={last['vmrss_mb']} MB, requests={last['requests']}, verified={last['verified']}, "
          f"fail={last['fail']}, exc={last['exc']}, llm={last['llm']}")
