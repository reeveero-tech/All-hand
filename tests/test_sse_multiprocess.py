"""SSE/EventBus admission across processes (log 299 residual class).

The rate limiter was moved to a shared store (log 303); the SSE admission
state is still PROCESS-LOCAL memory: ``EventBus._subs`` / ``_per_client`` /
``_opens`` live in each process. Behind N workers each process admits its
own full caps, so the effective caps multiply — the same defect class the
red rate-limit test proved for ``request_counts``.

These tests run REAL server processes (subprocess uvicorn) and hold REAL
SSE connections open against them.
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
import pytest

REPO = Path(__file__).resolve().parents[1]
TENANT_CAP = 3      # AGOS_SSE_MAX_PER_TENANT for every spawned process


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _tenant_key() -> str:
    return secrets.token_hex(16)


class _SSEServer:
    """One real server process with a small per-tenant SSE cap. The rate
    limiter is per-server (own DB) — these tests are about the SSE state."""

    def __init__(self, key: str):
        self.port = _free_port()
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("OPENROUTER", "SUPABASE", "AGOS_", "LLM_"))}
        env.update({
            "AGOS_TENANT_KEYS": json.dumps(
                [{"tenant": "sse_test", "key_sha256": hashlib.sha256(key.encode()).hexdigest()}]),
            "AGOS_RATE_LIMIT_REQUESTS": "1000",
            "AGOS_RATE_LIMIT_WINDOW": "60",
            "AGOS_RATE_LIMIT_DB": f"/tmp/rl_sse_{self.port}.sqlite3",
            "AGENTS_PER_SPEC": "1",
            "AGOS_SSE_MAX_PER_TENANT": str(TENANT_CAP),
            "AGOS_SSE_MAX_PER_CLIENT": "50",
            "AGOS_SSE_MAX_CONNECTIONS": "100",
            "AGOS_SSE_OPEN_RATE": "1000",
        })
        self.log_path = Path(f"/tmp/sse_multiproc_{self.port}.log")
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
            try:
                async with session.get(f"{self.base}/health") as r:
                    if r.status in (200, 401):
                        return
            except aiohttp.ClientError:
                pass
            await asyncio.sleep(0.1)
        raise RuntimeError(f"server never became ready: {self.log_path}")

    def stop(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=10)
        self.log.close()


class _OpenStreams:
    """Hold real SSE connections open (enter the response contexts, keep
    them, close them all at the end)."""

    def __init__(self, session: aiohttp.ClientSession, server: _SSEServer,
                 headers: dict):
        self.session, self.server, self.headers = session, server, headers
        self._cms: list = []

    async def open(self, expect: int) -> int:
        """Open ONE stream; return its status (200 admitted, 429 refused)."""
        cm = self.session.get(f"{self.server.base}/api/events/stream",
                              headers=self.headers)
        resp = await cm.__aenter__()
        if resp.status == 200:
            self._cms.append(cm)          # admitted: hold it open
            return 200
        await cm.__aexit__(None, None, None)  # refused: release the context
        return resp.status

    async def close_all(self) -> None:
        for cm in self._cms:
            try:
                await cm.__aexit__(None, None, None)
            except Exception:
                pass
        self._cms.clear()


async def _open_n(streams: _OpenStreams, n: int) -> list:
    return [await streams.open(0) for _ in range(n)]


async def test_control_single_process_enforces_the_tenant_cap(tmp_path):
    """CONTROL (the in-process behavior is sound): one server, the tenant
    opens TENANT_CAP streams (all 200) and the next is refused (429)."""
    key = _tenant_key()
    srv = _SSEServer(key)
    try:
        async with aiohttp.ClientSession() as s:
            await srv.wait_ready(s)
            headers = {"X-API-Key": key}
            streams = _OpenStreams(s, srv, headers)
            try:
                codes = await _open_n(streams, TENANT_CAP + 1)
                assert codes[:TENANT_CAP] == [200] * TENANT_CAP, codes
                assert codes[TENANT_CAP] == 429, codes
            finally:
                await streams.close_all()
    finally:
        srv.stop()


async def test_contract_sse_admission_does_not_multiply_across_processes(tmp_path):
    """CONTRACT (the log-299 residual, red on the current code): the
    per-tenant SSE cap is ONE cap for the TENANT, no matter how many
    processes serve it. With the state in process memory each worker admits
    its own full cap and the effective cap multiplies — this test asserts
    the CORRECT contract (the tenant reaches the cap after TENANT_CAP
    admissions ACROSS processes) and is RED until the admission state is
    shared. The fix (lease-based shared admission with liveness) is a
    separate batch; this red test is its acceptance test, kept like
    b061663e was for the rate limiter (merged only WITH the fix)."""
    key = _tenant_key()
    a = _SSEServer(key)
    b = _SSEServer(key)
    try:
        async with aiohttp.ClientSession() as s:
            await a.wait_ready(s)
            await b.wait_ready(s)
            headers = {"X-API-Key": key}
            sa = _OpenStreams(s, a, headers)
            sb = _OpenStreams(s, b, headers)
            try:
                # fill A's own cap: TENANT_CAP admitted on server A
                codes_a = await _open_n(sa, TENANT_CAP)
                assert codes_a == [200] * TENANT_CAP, codes_a
                # THE CONTRACT: the tenant's cap is reached — the next open
                # ANYWHERE (here on B, the other process) must be refused.
                # On process-local state B admits its own full cap too and
                # the effective cap doubles (this is the defect).
                code_b = await sb.open(0)
                assert code_b == 429, (
                    f"SSE per-tenant cap multiplied across processes: "
                    f"{TENANT_CAP} admitted on one process, another admitted "
                    f"on the second (status {code_b}) — effective cap "
                    f"{TENANT_CAP + 1} > {TENANT_CAP}")
            finally:
                await sa.close_all()
                await sb.close_all()
    finally:
        a.stop()
        b.stop()
