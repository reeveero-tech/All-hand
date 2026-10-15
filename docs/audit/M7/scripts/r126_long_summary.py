#!/usr/bin/env python3
"""R-126: summary of the 150-minute runs (VmRSS per 15 min, slope in the last
hour, failures with their attributed reason)."""
import json
import sys

for path in sys.argv[1:]:
    d = json.load(open(path, encoding="utf-8"))
    s = d["samples"]
    print(f"== {d['label']} ({path.rsplit('/', 1)[-1]}) templates={d.get('templates')}")
    for x in s:
        m = round(x["t_s"] / 60)
        if m % 15 == 0 or x is s[-1]:
            print(f"  min {m:4d}: rss {x['vmrss_mb']:6.1f} MB | in-use {x['malloc_inuse_mb']:5.1f} | free-held "
                  f"{x['malloc_free_held_mb']:5.1f} | traced {x['traced_mb']:5.1f} | exp {x['experience_entries']} | "
                  f"requests {x['requests']} verified {x['verified']} fail {x['fail']} exc {x['exc']}")
    last_hour = [x for x in s if x["t_s"] >= s[-1]["t_s"] - 3600]
    a, b = last_hour[0], last_hour[-1]
    print(f"  peak rss {max(x['vmrss_mb'] for x in s)} MB; last hour {a['vmrss_mb']} -> {b['vmrss_mb']} MB "
          f"({(b['vmrss_mb'] - a['vmrss_mb']) / ((b['t_s'] - a['t_s']) / 60):+.3f} MB/min)")
    print(f"  llm {s[-1]['llm']}")
    print(f"  failures attributed: {len(d.get('failures', []))} / {s[-1]['fail'] + s[-1]['exc']}")
    for f in d.get("failures", []):
        print("   ", json.dumps(f, ensure_ascii=False))
