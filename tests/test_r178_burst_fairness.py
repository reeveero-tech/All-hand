"""R-178 (D-140): عدالة التوزيع تحت دفعة متزامنة وسرعات غير متجانسة.

إصلاح D-139 (الأقل إنجازًا بين الخاملين) كان صحيحًا للطلبات المتتابعة، لكن
اختبار L0 يقيس **الإجمالي النهائي** لكل وكيل فقط، فلم يكشف عيبًا ثانيًا:
حالة الوكيل (`idle`) وعداده (`tasks_completed`) لا يتغيران إلا عند بدء
التنفيذ الفعلي/انتهائه، بينما بين الاختيار والتنفيذ انتظارات (كتابة
`running` والنشر). فدفعة من N مهمة متزامنة لتخصص واحد تختار **الوكيل نفسه**
N مرة: قياس قبل الإصلاح (السجل 384): 10/10 مهام على وكيل واحد بتزامن ذروته 10،
والأربعة الآخرون خاملون — ثم يتعادل الإجمالي لاحقًا فيبقى Jain النهائي مرتفعًا
ويخفي التكدس.

العقد المثبت هنا (D-140): مفتاح الاختيار الأول هو **العقود الجارية**
(`AGENT_REGISTRY.load_of` — تتحدث تزامنيًا مع `acquire` بلا أي انتظار بين
الاختيار والحجز)، ثم الخمول، ثم الأقل إنجازًا. فلا يتكدس وكيل في دفعة،
ولا يجذب الوكيل البطيء (الأقل إنجازًا لأنه بطيء) عملًا إضافيًا وهو مشغول.

حدود صادقة: مخزن ووكلاء وهميون داخل عملية واحدة (المسار الحقيقي الكامل
`server._run_task` ← `_run_task_traced` مع `_pick_agent` و`AGENT_REGISTRY` الحقيقيين)؛
لا يقيس سعة ولا كمونًا، ولا يغني عن سلّم R-178 على بيئة Q-023.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PROBE = r"""
import asyncio, collections, json, os, sys, time
import server

SPEC = "validator"
N = int(os.environ["PROBE_N"])
SLOW = os.environ.get("PROBE_SLOW") == "1"

class Store:
    def persists(self): return False
    async def transition_task(self, *a, **k):
        await asyncio.sleep(0.002); return True      # a real write yields the loop
    def __getattr__(self, n):
        async def f(*a, **k): return True
        return f
server.get_storage = lambda: Store()
async def _w(*a, **k): return True
server._write_task_terminal = _w
class SP:
    def set(self, **k): pass

pool = server.CIVILIZATION[SPEC]
live = collections.Counter(); peak = collections.Counter()
for i, a in enumerate(pool):
    dur = 0.30 if (SLOW and i == 0) else 0.03     # agent 0 is 10x slower
    async def ex(payload, a=a, dur=dur):
        a.status = "working"; live[a.name] += 1
        peak[a.name] = max(peak[a.name], live[a.name])
        await asyncio.sleep(dur)
        live[a.name] -= 1; a.status = "idle"; a.stats["tasks_completed"] += 1
        return {"ok": True, "agent": a.name, "elapsed_s": dur, "result": {}}
    a.execute = ex

async def one(tid):
    server.TASKS[tid] = {"tenant_id": "default", "status": "queued", "created_at": time.time()}
    await server._run_task(tid, SPEC, "p", None)   # full path: lease released in finally

async def main():
    if SLOW:
        # waves: a new wave of 5 arrives every 40ms while agent 0 is still busy
        runs = []
        for w in range(N // 5):
            runs += [asyncio.ensure_future(one(f"w{w}-{j}")) for j in range(5)]
            await asyncio.sleep(0.04)
        await asyncio.gather(*runs)
    else:
        await asyncio.gather(*(one(f"t{i}") for i in range(N)))
    picks = collections.Counter(r.get("agent") for r in server.TASKS.values())
    print(json.dumps({"pool": [a.name for a in pool], "picks": picks, "peak": peak,
                      "status": collections.Counter(r["status"] for r in server.TASKS.values())}))

asyncio.run(main())
"""


def _probe(n: int, slow: bool = False) -> dict:
    env = dict(os.environ)
    env.update(AGENTS_PER_SPEC="5", PROBE_N=str(n), PROBE_SLOW="1" if slow else "0")
    env.setdefault("AGOS_ENV", "test")
    r = subprocess.run([sys.executable, "-c", PROBE], capture_output=True, text=True,
                       cwd=str(ROOT), env=env, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_concurrent_burst_spreads_over_the_pool():
    d = _probe(10)
    assert d["status"] == {"completed": 10}, d
    # 10 tasks, 5 idle agents: every agent gets exactly 2 (pre-fix: one agent got 10)
    assert sorted(d["picks"].values()) == [2, 2, 2, 2, 2], d["picks"]
    assert max(d["peak"].values()) <= 2, f"an agent ran {max(d['peak'].values())} tasks at once: {d['peak']}"


def test_burst_equal_to_pool_gives_one_task_per_agent():
    d = _probe(5)
    assert sorted(d["picks"].values()) == [1, 1, 1, 1, 1], d["picks"]
    assert max(d["peak"].values()) == 1, d["peak"]


def test_slow_agent_does_not_attract_extra_work_while_busy():
    # agent 0 is 10x slower, so it has the FEWEST completed tasks for most of the
    # run: least-completed alone keeps piling new waves on it while it is busy.
    d = _probe(30, slow=True)
    assert d["status"] == {"completed": 30}, d
    slow_name = d["pool"][0]
    assert d["peak"].get(slow_name, 0) <= 1, (
        f"slow agent ran {d['peak'][slow_name]} tasks at once — slowness attracted load: {d}")
    fast_mean = sum(v for k, v in d["picks"].items() if k != slow_name) / 4
    assert d["picks"].get(slow_name, 0) < fast_mean, d["picks"]


def test_registry_load_of_is_zero_for_unknown_and_tracks_leases():
    from agent_civilization.core import agent_registry as ar
    reg = ar.AgentRegistry()
    assert reg.load_of("nobody") == 0
    reg.register_agent("a1", "a1", "validator")
    reg.register_worker("w1", "default", 4)
    reg.mark_ready("w1")
    reg.acquire("t1", "a1", worker_id="w1")
    reg.acquire("t2", "a1", worker_id="w1")
    assert reg.load_of("a1") == 2
    reg.release("t1")
    assert reg.load_of("a1") == 1
