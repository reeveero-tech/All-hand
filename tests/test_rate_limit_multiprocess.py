"""Rate-limit multiplication across processes (log 293 residual; log 294 risk
list): ``check_rate_limit`` counts in process-local memory
(``server.request_counts``). Behind several workers / instances the effective
per-client limit multiplies — each process enforces its own window, so a
client served round-robin by N processes gets ~N x the configured limit.

Reproduction (this file): real server subprocesses (the load tool's pattern),
one tenant key shared by all of them = one deployment scaled out; the client
alternates between the processes — exactly what a balancer without sticky
sessions does. Contract under test: the per-client limit is GLOBAL across the
processes of one deployment — requests beyond the configured limit must be
refused with 429 no matter which process serves them.

  * control: a SINGLE process with the same config refuses request LIMIT+1
    (the limiter itself works; proves the red below is the topology, not the
    harness);
  * contract: two processes, round-robin 2 x LIMIT requests — on per-process
    memory every request is accepted (each process saw only LIMIT), which
    violates the global contract (red on main, the defect reproduced).

Multi-instance (several hosts) is the same defect with the counter further
away; any real fix needs shared coordination or centralized admission — a
topology/infrastructure decision, not made in this batch.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
from pathlib import Path

import aiohttp

REPO = Path(__file__).resolve().parents[1]
LIMIT = 5      # AGOS_RATE_LIMIT_REQUESTS for every spawned process
WINDOW = 60    # AGOS_RATE_LIMIT_WINDOW


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _Server:
    """One real server process (subprocess uvicorn — same pattern as
    scripts/r171_local_load.py: stderr to a file so a full pipe can never
    freeze the server, credentials via env, fast small civilization)."""

    def __init__(self, key: str):
        self.port = _free_port()
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("OPENROUTER", "SUPABASE", "AGOS_", "LLM_"))}
        env.update({
            "AGOS_TENANT_KEYS": json.dumps(
                [{"tenant": "rl_test", "key_sha256": hashlib.sha256(key.encode()).hexdigest()}]),
            "AGOS_RATE_LIMIT_REQUESTS": str(LIMIT),
            "AGOS_RATE_LIMIT_WINDOW": str(WINDOW),
            "AGENTS_PER_SPEC": "1",
        })
        self.log_path = Path(f"/tmp/rl_multiproc_{self.port}.log")
        self.log = open(self.log_path, "wb")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "server:app", "--host", "127.0.0.1",
             "--port", str(self.port), "--log-level", "warning"],
            cwd=REPO, env=env, stdout=subprocess.DEVNULL, stderr=self.log)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def wait_ready(self, session: aiohttp.ClientSession) -> None:
        for _ in range(300):
            if self.proc.poll() is not None:
                raise SystemExit(f"server exited; see {self.log_path}")
            try:
                async with session.get(f"{self.base}/health/ready") as r:
                    if r.status == 200:
                        return
            except aiohttp.ClientError:
                pass
            await asyncio.sleep(0.1)
        raise TimeoutError(f"server on {self.port} not ready in 30s; see {self.log_path}")

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
        self.log.close()


async def _round_robin(servers, n, session, key):
    """n requests alternating between the processes (what a balancer without
    sticky sessions does); returns the statuses in order."""
    statuses = []
    for i in range(n):
        srv = servers[i % len(servers)]
        async with session.get(f"{srv.base}/api/events",
                               headers={"X-API-Key": key}) as r:
            await r.read()
            statuses.append(r.status)
    return statuses


def test_single_process_enforces_the_configured_limit():
    """Control (passes on main): one process, LIMIT accepted then 429."""
    key = "rl-" + secrets.token_urlsafe(16)

    async def go():
        srv = _Server(key)
        try:
            async with aiohttp.ClientSession() as s:
                await srv.wait_ready(s)
                return await _round_robin([srv], LIMIT + 1, s, key)
        finally:
            srv.stop()
    statuses = asyncio.run(go())
    assert statuses[:LIMIT] == [200] * LIMIT, statuses
    assert statuses[LIMIT] == 429, statuses


def test_limit_is_global_across_processes():
    """Contract: two processes of one deployment share the per-client limit —
    at most LIMIT requests accepted within the window. RED on main (per-process
    memory): round-robin 2 x LIMIT requests are ALL accepted."""
    key = "rl-" + secrets.token_urlsafe(16)

    async def go():
        servers = [_Server(key), _Server(key)]
        try:
            async with aiohttp.ClientSession() as s:
                for srv in servers:
                    await srv.wait_ready(s)
                return await _round_robin(servers, 2 * LIMIT, s, key)
        finally:
            for srv in servers:
                srv.stop()
    statuses = asyncio.run(go())
    accepted = statuses.count(200)
    refused = statuses.count(429)
    assert accepted <= LIMIT, \
        f"rate limit multiplied across processes: {accepted} accepted " \
        f"(limit {LIMIT}, {len(statuses)} round-robin requests), statuses={statuses}"
    assert refused >= 2 * LIMIT - LIMIT, statuses
