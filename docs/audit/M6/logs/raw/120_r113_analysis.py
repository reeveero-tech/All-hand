import json,collections,hashlib,subprocess
P="docs/audit/M6/logs/raw/r113_live_hour_20261014T194055Z.json"
raw=open(P,"rb").read(); r=json.loads(raw)
print("# R-113 analysis of",P); print("sha256",hashlib.sha256(raw).hexdigest())
print("git_head",r["git_head"],"started",r["started_utc"],"target_s",r["duration_s_target"])
print("summary",json.dumps(r["summary"],ensure_ascii=False))
print("rss_mb per 5-min snapshot",[s["rss_mb"] for s in r["periodic_snapshots"]])
print("idle per snapshot",[s["idle"] for s in r["periodic_snapshots"]])
by=collections.defaultdict(lambda:[0,0]); fails=collections.defaultdict(collections.Counter); agents=collections.Counter(); prim=collections.Counter()
for q in r["requests"]:
    t=q["request"]; by[t][0]+=1; by[t][1]+=q["plan_ok"]; prim[(t,q["plan_primary"])]+=1
    for s in q["stages"]:
        agents[s["agent"]]+=1
        if not s["ok"]: fails[t][s["spec"]]+=1
print("\n## per request type: plan_ok/total | primary | failing stages")
for t,(n,ok) in sorted(by.items(),key=lambda x:x[1][1]/x[1][0]):
    ps=sorted({p for (tt,p) in prim if tt==t})
    print(f"{ok}/{n} | {ps} | {dict(fails[t])} | {t}")
print("\ntypes 100% ok:",sum(1 for n,ok in by.values() if ok==n),"| types 0% ok:",sum(1 for n,ok in by.values() if ok==0),"| total types:",len(by))
print("\n## distinct agents that executed stages:",len(agents),"of",r["agents"])
for a,c in agents.most_common(): print(a,c)
