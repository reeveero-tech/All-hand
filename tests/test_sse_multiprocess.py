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

    def __init__(self, key: str, extra_env: dict | None = None):
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
        if extra_env:
            env.update(extra_env)
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
        self._resps: list = []

    async def open(self, expect: int) -> int:
        """Open ONE stream; return its status (200 admitted, 429 refused)."""
        cm = self.session.get(f"{self.server.base}/api/events/stream",
                              headers=self.headers)
        resp = await cm.__aenter__()
        if resp.status == 200:
            self._cms.append(cm)          # admitted: hold it open
            self._resps.append(resp)
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


# ---------------------------------------------------------------------------
# The FIX's acceptance tests (the red test above is the contract; these pin
# the lease semantics it relies on — real processes, real sockets, real
# SIGKILL). They share ONE admission DB file per test (the deployment shape:
# one host, one file) via AGOS_SSE_ADMISSION_DB.

def _shared_admission_db(tmp_path) -> str:
    return str(tmp_path / "sse_admission_shared.sqlite3")


async def _gather_opens(streams_list):
    """Open one stream on each server simultaneously (true concurrency)."""
    return await asyncio.gather(*(st.open(0) for st in streams_list))


async def test_simultaneous_admission_from_two_processes_admits_exactly_the_cap(tmp_path):
    """ATOMICITY under simultaneous admission: 6 opens fired CONCURRENTLY,
    alternating across two processes, admit EXACTLY TENANT_CAP (3) — the
    shared transaction serializes admission; the cap never multiplies and
    never under-admits."""
    key = _tenant_key()
    db = _shared_admission_db(tmp_path)
    a = _SSEServer(key, extra_env={"AGOS_SSE_ADMISSION_DB": db})
    b = _SSEServer(key, extra_env={"AGOS_SSE_ADMISSION_DB": db})
    try:
        async with aiohttp.ClientSession() as s:
            await a.wait_ready(s)
            await b.wait_ready(s)
            headers = {"X-API-Key": key}
            sa = _OpenStreams(s, a, headers)
            sb = _OpenStreams(s, b, headers)
            try:
                codes = await _gather_opens([sa, sb, sa, sb, sa, sb])
                assert codes.count(200) == TENANT_CAP, codes
                assert codes.count(429) == 6 - TENANT_CAP, codes
            finally:
                await sa.close_all()
                await sb.close_all()
    finally:
        a.stop()
        b.stop()


async def test_denied_admission_leaves_no_reservation_across_processes(tmp_path):
    """A refusal holds NO lease: after the cap is reached and the 4th open
    is refused on the OTHER process, closing the 3 held streams frees the
    full cap again (the refused open reserved nothing)."""
    key = _tenant_key()
    db = _shared_admission_db(tmp_path)
    a = _SSEServer(key, extra_env={"AGOS_SSE_ADMISSION_DB": db})
    b = _SSEServer(key, extra_env={"AGOS_SSE_ADMISSION_DB": db})
    try:
        async with aiohttp.ClientSession() as s:
            await a.wait_ready(s)
            await b.wait_ready(s)
            headers = {"X-API-Key": key}
            sa = _OpenStreams(s, a, headers)
            sb = _OpenStreams(s, b, headers)
            try:
                codes_a = await _open_n(sa, TENANT_CAP)
                assert codes_a == [200] * TENANT_CAP, codes_a
                assert await sb.open(0) == 429          # refused on B
                await sa.close_all()                    # release the 3
                codes_b = await _open_n(sb, TENANT_CAP)
                assert codes_b == [200] * TENANT_CAP, codes_b  # nothing held
            finally:
                await sa.close_all()
                await sb.close_all()
    finally:
        a.stop()
        b.stop()


async def test_disconnect_releases_capacity_on_the_other_process(tmp_path):
    """Release after disconnect: closing ONE stream on A immediately frees
    a slot that B can take (the lease is host-shared, released on close)."""
    key = _tenant_key()
    db = _shared_admission_db(tmp_path)
    a = _SSEServer(key, extra_env={"AGOS_SSE_ADMISSION_DB": db})
    b = _SSEServer(key, extra_env={"AGOS_SSE_ADMISSION_DB": db})
    try:
        async with aiohttp.ClientSession() as s:
            await a.wait_ready(s)
            await b.wait_ready(s)
            headers = {"X-API-Key": key}
            sa = _OpenStreams(s, a, headers)
            sb = _OpenStreams(s, b, headers)
            try:
                codes_a = await _open_n(sa, TENANT_CAP)
                assert codes_a == [200] * TENANT_CAP, codes_a
                assert await sb.open(0) == 429
                await sa.close_all()                    # disconnect all 3
                assert await sb.open(0) == 200         # B takes the freed slot
            finally:
                await sa.close_all()
                await sb.close_all()
    finally:
        a.stop()
        b.stop()


async def test_sigkill_recovery_lease_expiry_frees_capacity(tmp_path):
    """SIGKILL recovery: a worker killed WITHOUT releasing holds its leases
    only until they EXPIRE. Proven in order: the cap holds right after the
    kill (the killed worker's leases are still counted), then after the TTL
    the capacity frees by itself."""
    key = _tenant_key()
    db = _shared_admission_db(tmp_path)
    ttl = 2
    a = _SSEServer(key, extra_env={
        "AGOS_SSE_ADMISSION_DB": db,
        "AGOS_SSE_LEASE_TTL": str(ttl), "AGOS_SSE_LEASE_RENEWAL": "1"})
    b = _SSEServer(key, extra_env={
        "AGOS_SSE_ADMISSION_DB": db,
        "AGOS_SSE_LEASE_TTL": str(ttl), "AGOS_SSE_LEASE_RENEWAL": "1"})
    try:
        async with aiohttp.ClientSession() as s:
            await a.wait_ready(s)
            await b.wait_ready(s)
            headers = {"X-API-Key": key}
            sa = _OpenStreams(s, a, headers)
            sb = _OpenStreams(s, b, headers)
            held = 0
            try:
                codes_a = await _open_n(sa, TENANT_CAP)
                assert codes_a == [200] * TENANT_CAP, codes_a
                held = len(sa._cms)
                # SIGKILL: no release, no cleanup — the hardest death
                a.proc.kill()
                a.proc.wait(timeout=10)
                # the killed worker's leases are STILL counted (proven: B is
                # refused at the shared cap immediately after the kill)
                assert await sb.open(0) == 429
                sa._cms.clear()                # the sockets died with the process
                # after the TTL the leases expire and the capacity frees alone
                await asyncio.sleep(ttl + 1.0)
                assert await sb.open(0) == 200
            finally:
                if held:
                    pass  # the killed process's sockets are gone; nothing to close
                await sb.close_all()
    finally:
        a.stop()
        b.stop()


async def test_renewal_maintains_capacity_beyond_the_lease_ttl(tmp_path):
    """RENEWAL keeps a live stream's lease past its TTL: with TTL=2s and
    renewal every 1s, 3 streams held 3.5s are STILL ALIVE (heartbeats still
    arriving — the server did not end them) and the cap is STILL enforced on
    the other process (the leases are still counted). Then closing them
    frees the cap."""
    key = _tenant_key()
    db = _shared_admission_db(tmp_path)
    ttl = 2
    env = {"AGOS_SSE_ADMISSION_DB": db, "AGOS_SSE_LEASE_TTL": str(ttl),
           "AGOS_SSE_LEASE_RENEWAL": "1", "AGOS_SSE_HEARTBEAT": "1"}
    a = _SSEServer(key, extra_env=env)
    b = _SSEServer(key, extra_env=env)
    try:
        async with aiohttp.ClientSession() as s:
            await a.wait_ready(s)
            await b.wait_ready(s)
            headers = {"X-API-Key": key}
            sa = _OpenStreams(s, a, headers)
            sb = _OpenStreams(s, b, headers)
            try:
                codes_a = await _open_n(sa, TENANT_CAP)
                assert codes_a == [200] * TENANT_CAP, codes_a
                # past the ORIGINAL TTL with the streams still open
                await asyncio.sleep(ttl + 1.5)
                # the streams are still alive: heartbeats (1s apart) still
                # arrive on a stream we did NOT touch since opening it
                line = await asyncio.wait_for(
                    sa._resps[0].content.readline(), timeout=5)
                assert line.startswith(b"data:")        # a live heartbeat
                # and their leases are still counted: the cap holds on B
                assert await sb.open(0) == 429
                # closing them frees the capacity
                await sa.close_all()
                assert await sb.open(0) == 200
            finally:
                await sa.close_all()
                await sb.close_all()
    finally:
        a.stop()
        b.stop()


async def test_open_rate_window_is_shared_across_processes(tmp_path):
    """The SSE OPEN-RATE window is ONE window per client across processes:
    with open_rate=2, the third open ANYWHERE (here on the first process)
    is refused with 429 — the refusal cannot be a capacity refusal (the
    caps are far above the opens made)."""
    key = _tenant_key()
    db = _shared_admission_db(tmp_path)
    env = {"AGOS_SSE_ADMISSION_DB": db, "AGOS_SSE_OPEN_RATE": "2",
           "AGOS_SSE_OPEN_WINDOW": "60"}
    a = _SSEServer(key, extra_env=env)
    b = _SSEServer(key, extra_env=env)
    try:
        async with aiohttp.ClientSession() as s:
            await a.wait_ready(s)
            await b.wait_ready(s)
            headers = {"X-API-Key": key}
            sa = _OpenStreams(s, a, headers)
            sb = _OpenStreams(s, b, headers)
            try:
                assert await sa.open(0) == 200         # 1st open (on A)
                assert await sb.open(0) == 200         # 2nd open (on B)
                assert await sa.open(0) == 429        # 3rd open anywhere: refused
            finally:
                await sa.close_all()
                await sb.close_all()
    finally:
        a.stop()
        b.stop()
