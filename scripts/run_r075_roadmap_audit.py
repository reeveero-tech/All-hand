#!/usr/bin/env python3
"""R-075 roadmap audit — read-only checks over docs/governance/ROADMAP.md.

Usage: python scripts/run_r075_roadmap_audit.py [COMMIT_RANGE] [R-ID ...]
Defaults reproduce the cycle-3 run (R-075): a2ba8de..HEAD and R-073..R-078.
Cycle 4 (R-081): 3d07b9b..HEAD R-079 R-080 R-081 R-082 R-083.
"""
import collections
import re
import subprocess
import sys

road = open("docs/governance/ROADMAP.md", encoding="utf-8").read()
heads = [(m.start(), m.group(1), m.group(0)) for m in re.finditer(r"^#{3,4} (R-\d{3}[a-z]?)\b.*$", road, re.M)]
print(f"item headings: {len(heads)}")
ids = [h[1] for h in heads]
dups = [k for k, v in collections.Counter(ids).items() if v > 1]
print(f"duplicate ids: {dups or 'none'}")
status = collections.Counter()
missing = []
for i, (pos, rid, _) in enumerate(heads):
    end = heads[i + 1][0] if i + 1 < len(heads) else len(road)
    block = road[pos:end]
    m = re.search(r"^- الحالة: \*\*(\w+)\*\*", block, re.M)
    if not m:
        missing.append(rid)
    else:
        status[m.group(1)] += 1
        if m.group(1) != "done":
            print(f"  not done: {rid} -> {m.group(1)}")
print(f"status counts: {dict(status)}")
print(f"items without status: {missing or 'none'}")
frozen = [m.start() for m in re.finditer(r"\(هذه الدفعة\)", road)]
print(f"'(هذه الدفعة)' occurrences: {len(frozen)}")
for p in frozen:
    print("   ...", road[max(0, p - 60):p + 20].replace("\n", " "))
order_start = road.index("## ترتيب التنفيذ الموصى به")
order = road[order_start:road.index("\n## ", order_start + 5)]
print("order section length:", len(order))
for phrase in ["Q-020 للمالك. Q-020 للمالك", "أو فتح R-042 بقرار المالك", "البند `blocked` الوحيد", "التالي: دورة M6 القادمة"]:
    print(f"  stale phrase in order section: {phrase!r} -> {phrase in order}")
# every R-id mentioned in commit subjects since cycle start must be a roadmap item
RANGE = sys.argv[1] if len(sys.argv) > 1 else "a2ba8de..HEAD"
CYCLE_IDS = sys.argv[2:] or ["R-073", "R-074", "R-075", "R-076", "R-077", "R-078"]
log = subprocess.run(["git", "log", "--format=%s", RANGE], capture_output=True, text=True).stdout
mentioned = sorted(set(re.findall(r"R-\d{3}[a-z]?", log)))
print(f"R-ids in commit subjects since cycle start ({RANGE}): {mentioned}")
print(f"  not in ROADMAP: {[r for r in mentioned if r not in ids] or 'none'}")
chg = open("docs/governance/CHANGELOG.md", encoding="utf-8").read()
for rid in CYCLE_IDS:
    print(f"  {rid}: roadmap={'yes' if rid in ids else 'NO'} changelog={'yes' if rid in chg else 'no'}")
sys.exit(1 if dups or missing else 0)
