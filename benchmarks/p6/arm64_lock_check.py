"""D-137 / Q-023: does requirements.lock (pip --require-hashes) install on linux aarch64 (Oracle A1, AWS Graviton)?
Read-only: queries PyPI JSON metadata for every pinned version and checks whether at least one
aarch64-compatible (cp312 / abi3<=cp312 / py3-none-any) wheel hash is IN the lock. No install, no resource."""
import re, json, urllib.request, concurrent.futures as cf
txt=open('requirements.lock').read()
pk={}
cur=None
for line in txt.splitlines():
    m=re.match(r'^([A-Za-z0-9_.\-]+(?:\[[^\]]*\])?)==([^\s;\\]+)',line)
    if m: cur=(re.sub(r'\[.*\]','',m.group(1)),m.group(2)); pk[cur]=set()
    for h in re.findall(r'--hash=sha256:([0-9a-f]{64})',line):
        if cur: pk[cur].add(h)
def ok(fn):
    if fn.endswith('.tar.gz') or fn.endswith('.zip'): return None
    if 'none-any' in fn: return True
    tags=fn[:-4].split('-')[-3:]
    py,abi,plat=tags
    pyok=('cp312' in py) or (abi=='abi3' and re.search(r'cp3(\d+)',py) and int(re.search(r'cp3(\d+)',py).group(1))<=12)
    return bool(pyok and 'aarch64' in plat and 'musl' not in plat)
def check(item):
    (n,v),hs=item
    d=json.load(urllib.request.urlopen(f'https://pypi.org/pypi/{n}/{v}/json',timeout=30))
    files=d['urls']
    arm_avail=[f for f in files if ok(f['filename'])]
    arm_locked=[f for f in arm_avail if f['digests']['sha256'] in hs]
    any_universal=any('none-any' in f['filename'] for f in files)
    return n,v,len(arm_avail),len(arm_locked),any_universal
res=list(cf.ThreadPoolExecutor(16).map(check,pk.items()))
bad=[r for r in res if r[3]==0]
print(f"packages in lock: {len(res)}; with an aarch64/cp312-or-universal wheel hash IN the lock: {len(res)-len(bad)}; missing: {len(bad)}")
for n,v,a,l,u in sorted(bad): print(f"  MISSING {n}=={v}: aarch64-compatible wheels on PyPI={a}, hashes in lock=0")
