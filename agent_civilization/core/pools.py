"""R-132 (RISK-029, D-055): separate thread pools per kind of blocking call.

Before R-132 every blocking call (keyless LLM POST, web search, translation,
reachability probe, SQLite, the code sandbox) went through ``asyncio.to_thread``
— the loop's single default executor, min(32, CPU + 4) = 6 threads on the 2-CPU
measurement host. Under concurrent HTTP load the slow kinds (LLM up to 45 s,
web search with 10 s timeouts) held those threads and a fast reachability
check waited behind them: 0.07 s at 10 clients, 10-12 s median / 49 s max at
25-50 (docs/audit/M7/logs/raw/161_r131_compare.log).

Each kind now has its own bounded pool, so a slow kind can only delay itself.
Sizes are read once from the environment (AGOS_POOL_<NAME>, 1..64).

Optional queue cap (AGOS_POOL_<NAME>_QUEUE, default 0 = unlimited): when that
many calls are already waiting for a thread, ``run`` raises ``PoolBusy`` at
once instead of queueing. Off by default: a cap of 8 on ``web`` made 39 of 50
concurrent researcher searches fail although researcher already bounds its
wait by its own budget (docs/audit/M7/logs/raw/171_r132_web_cap_regression.log).
Callers bound their own wait instead (asyncio.wait_for); a call cancelled while
still queued never runs.
"""
import asyncio
import contextvars
import functools
import os
import threading
from concurrent.futures import ThreadPoolExecutor

# name -> (env var, default size, what runs there)
POOL_SPECS = {
    "llm": ("AGOS_POOL_LLM", 2, "keyless LLM POST (calls are already serialised, D-051)"),
    "web": ("AGOS_POOL_WEB", 4, "web search, translation, researcher page fetch"),
    "net": ("AGOS_POOL_NET", 8, "reachability probe, HEAD/GET of user URLs, DNS checks"),
    "local": ("AGOS_POOL_LOCAL", 4, "SQLite, code sandbox, scaffold, host snapshot"),
}
# default queue cap per pool (0 = unlimited); env AGOS_POOL_<NAME>_QUEUE
QUEUE_DEFAULTS = {"llm": 0, "web": 0, "net": 0, "local": 0}

_pools: dict = {}
stats = {"busy": {name: 0 for name in POOL_SPECS}}


class PoolBusy(RuntimeError):
    """Raised instead of queueing when a capped pool's queue is full."""


def queue_cap(name: str) -> int:
    env = POOL_SPECS[name][0] + "_QUEUE"
    try:
        n = int(os.environ.get(env, QUEUE_DEFAULTS[name]))
    except ValueError:
        n = QUEUE_DEFAULTS[name]
    return max(0, min(1000, n))


def size(name: str) -> int:
    env, default, _ = POOL_SPECS[name]
    try:
        n = int(os.environ.get(env, default))
    except ValueError:
        n = default
    return max(1, min(64, n))


def get(name: str) -> ThreadPoolExecutor:
    if name not in POOL_SPECS:
        raise KeyError(f"unknown pool {name!r}; known: {sorted(POOL_SPECS)}")
    ex = _pools.get(name)
    if ex is None:
        ex = _pools[name] = ThreadPoolExecutor(max_workers=size(name), thread_name_prefix=f"agos-{name}")
    return ex


# R-164 (CI finding on PR #189): calls submitted and not finished, per pool.
# "Waiting" = in flight beyond the thread count. It used to be read from the
# executor's work queue, which also holds calls an IDLE thread is about to
# pick up: in a burst the cap then refused calls while threads were free
# (premature PoolBusy, ~0.5% of bursts on a warm pool). The counter (kept on
# the executor itself, ``_agos_inflight``) is exact:
# +1 at submit, -1 when the call's future completes (a queued call cancelled
# -> at once; a running one -> when its thread really finishes).
_inflight_lock = threading.Lock()


def _inflight(ex: ThreadPoolExecutor) -> int:
    return getattr(ex, "_agos_inflight", 0)


def _done(ex: ThreadPoolExecutor) -> None:
    with _inflight_lock:
        ex._agos_inflight = max(0, _inflight(ex) - 1)


def waiting(name: str) -> int:
    """Calls waiting for a thread of pool ``name`` right now."""
    ex = _pools.get(name)
    if ex is None:
        return 0
    with _inflight_lock:
        return max(0, _inflight(ex) - ex._max_workers)


async def run(name: str, fn, *args, **kwargs):
    """Like ``asyncio.to_thread`` (context variables are carried over) but on
    the named pool."""
    ex = get(name)
    cap = queue_cap(name)
    with _inflight_lock:
        queued = max(0, _inflight(ex) - ex._max_workers)
        if cap and queued >= cap:
            stats["busy"][name] += 1
            raise PoolBusy(f"{name} pool busy: {queued} call(s) already waiting for "
                           f"{ex._max_workers} thread(s) (cap {POOL_SPECS[name][0]}_QUEUE={cap})")
        ex._agos_inflight = _inflight(ex) + 1
    ctx = contextvars.copy_context()
    try:
        cf = ex.submit(functools.partial(ctx.run, fn, *args, **kwargs))
    except BaseException:
        _done(ex)
        raise
    cf.add_done_callback(lambda _f: _done(ex))
    return await asyncio.wrap_future(cf)


def snapshot() -> dict:
    """Pools created so far (for measurement: scripts/r132_server_probe.py)."""
    return dict(_pools)


def load() -> dict:
    """R-164: per-pool load for /metrics — threads, calls waiting for a
    thread right now, and calls refused by the queue cap since start. Only
    pools created so far (an unused kind costs nothing)."""
    out = {}
    for name, ex in list(_pools.items()):
        out[name] = {"size": ex._max_workers, "waiting": waiting(name),
                     "queue_cap": queue_cap(name), "busy_rejections": stats["busy"][name]}
    return out
