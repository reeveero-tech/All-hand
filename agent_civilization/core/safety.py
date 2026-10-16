#!/usr/bin/env python3
"""
Runtime safety policy for real agent tools (R-100, D-044).

Three real capabilities of the dispatcher can harm the host when exposed to an
unauthenticated client (the server's default "open" mode, R-004):

* ``test_runner`` executes submitted Python.  Before R-100 the child inherited
  the server environment (``SUPABASE_SERVICE_KEY``, ``OPENROUTER_API_KEY``) and
  could read the parent's ``/proc/<ppid>/environ`` (evidence in
  ``docs/audit/M6/logs/raw/27_r100_probe_before_main.log``).
* ``api_integrator`` / ``network_client`` / external agents open URLs taken from
  the request: loopback, private networks and cloud metadata endpoints
  (169.254.169.254) were reachable (SSRF).
* ``db_manager`` runs arbitrary SQL in SQLite: a recursive CTE could pin a
  worker thread forever, and ``ATTACH`` / ``VACUUM INTO`` touch the filesystem.

This module is the single place that decides what is allowed.  It is pure
stdlib and has no side effects at import time.
"""

from __future__ import annotations

import errno
import http.client
import ipaddress
import os
import signal
import socket
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from typing import Dict, Iterable, List, Optional, Tuple

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _env_flag(name: str) -> Optional[bool]:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    v = raw.strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    return None


def auth_enabled() -> bool:
    """Same rule as ``server.ENABLE_AUTH``, read from the environment at call time."""
    return bool(os.environ.get("AGOS_API_KEY", ""))


# ---------------------------------------------------------------------------
# 1. code execution (test_runner)
# ---------------------------------------------------------------------------

def code_execution_policy() -> Tuple[bool, str]:
    """Return ``(allowed, reason)``.

    * ``AGOS_ALLOW_CODE_EXEC=1`` -> allowed (explicit operator opt-in).
    * ``AGOS_ALLOW_CODE_EXEC=0`` -> denied, even with an API key.
    * unset -> allowed only when ``AGOS_API_KEY`` is set (every task-creating
      route then requires the key); denied in open mode.
    """
    flag = _env_flag("AGOS_ALLOW_CODE_EXEC")
    if flag is True:
        return True, "AGOS_ALLOW_CODE_EXEC=1"
    if flag is False:
        return False, "code execution disabled by AGOS_ALLOW_CODE_EXEC=0"
    if auth_enabled():
        return True, "authenticated mode (AGOS_API_KEY set)"
    return False, ("code execution is disabled in open mode: set AGOS_API_KEY "
                   "(recommended) or AGOS_ALLOW_CODE_EXEC=1 to enable test_runner")


# Limits applied inside the child before user code runs.  hard == soft, so a
# non-root child cannot raise them again (the Docker image runs as non-root).
SANDBOX_LIMITS = {
    "cpu_seconds": 5,
    "address_space_bytes": 512 * 1024 * 1024,
    "file_size_bytes": 10 * 1024 * 1024,
    "open_files": 64,
}
SANDBOX_WALL_TIMEOUT_S = 8.0
SANDBOX_OUTPUT_LIMIT = 2000

_BOOTSTRAP_TEMPLATE = """
import resource, sys
def _lim(name, value):
    r = getattr(resource, name, None)
    if r is None:
        return
    try:
        resource.setrlimit(r, (value, value))
    except (ValueError, OSError):
        pass
_lim("RLIMIT_CPU", {cpu_seconds})
_lim("RLIMIT_AS", {address_space_bytes})
_lim("RLIMIT_FSIZE", {file_size_bytes})
_lim("RLIMIT_NOFILE", {open_files})
_lim("RLIMIT_CORE", 0)
_src = sys.stdin.read()
del resource, _lim
exec(compile(_src, "<agos-task>", "exec"), {{"__name__": "__main__"}})
"""


def _bootstrap(limits: Dict[str, int]) -> str:
    return _BOOTSTRAP_TEMPLATE.format(**{k: int(v) for k, v in limits.items()})


_HARDENED = False


def harden_server_process() -> bool:
    """Mark this process non-dumpable (Linux ``prctl(PR_SET_DUMPABLE, 0)``).

    A non-dumpable process's ``/proc/<pid>/environ`` and ``/proc/<pid>/mem``
    become unreadable to other processes of the same user — including the
    sandboxed ``test_runner`` child — so submitted code cannot read the
    secrets the host injected at start-up.  Idempotent; returns True when the
    protection is active.  The server calls it at start-up and
    ``run_sandboxed_python`` calls it before every run.
    """
    global _HARDENED
    if _HARDENED:
        return True
    if not sys.platform.startswith("linux"):
        return False
    try:
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        pr_set_dumpable, pr_get_dumpable = 4, 3
        libc.prctl(pr_set_dumpable, 0, 0, 0, 0)
        _HARDENED = libc.prctl(pr_get_dumpable, 0, 0, 0, 0) == 0
    except (OSError, AttributeError):
        _HARDENED = False
    return _HARDENED


def _sandbox_env(workdir: str) -> Dict[str, str]:
    # Deliberately NOT derived from os.environ: no secret can leak via the env.
    return {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": workdir, "TMPDIR": workdir,
            "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"}


def run_sandboxed_python(code: str, timeout_s: Optional[float] = None,
                         limits: Optional[Dict[str, int]] = None) -> Dict[str, object]:
    """Run ``code`` in a separate, isolated interpreter and return its outcome.

    Isolation applied (each verified by tests/test_r100_runtime_safety.py):
      * ``python -I``: ignores PYTHON* variables and the user site directory;
      * empty environment (``_sandbox_env``) instead of the server's;
      * private temporary working directory, deleted afterwards;
      * rlimits for CPU, address space, file size, open files, core dumps;
      * new session + kill of the whole process group on wall-clock timeout.

      * server process marked non-dumpable first (``harden_server_process``),
        so the child cannot read the server's ``/proc/<pid>/environ``.

    Residual (documented in SECURITY.md): the child runs as the same OS user as
    the server, so it can still read files that user can read and open network
    connections.  That is why execution itself is gated by
    ``code_execution_policy``.
    """
    harden_server_process()
    timeout_s = SANDBOX_WALL_TIMEOUT_S if timeout_s is None else timeout_s
    limits = {**SANDBOX_LIMITS, **(limits or {})}
    with tempfile.TemporaryDirectory(prefix="agos-sbx-") as workdir:
        started = time.monotonic()
        proc = subprocess.Popen(
            [sys.executable, "-I", "-c", _bootstrap(limits)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=workdir, env=_sandbox_env(workdir), start_new_session=True,
            # Child output is untrusted bytes: never let invalid UTF-8 raise here.
            encoding="utf-8", errors="replace",
        )
        try:
            out, err = proc.communicate(code, timeout=timeout_s)
            timed_out = False
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
            try:
                # A grandchild that left the process group (setsid) may still
                # hold the pipes open; never wait for it indefinitely.
                out, err = proc.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                for stream in (proc.stdin, proc.stdout, proc.stderr):
                    try:
                        if stream:
                            stream.close()
                    except OSError:
                        pass
                proc.kill()
                proc.wait(timeout=2)
                out, err = "", "output abandoned: a descendant process kept the pipes open"
            timed_out = True
        return {
            "returncode": proc.returncode,
            "timed_out": timed_out,
            "stdout": (out or "")[-SANDBOX_OUTPUT_LIMIT:],
            "stderr": (err or "")[-SANDBOX_OUTPUT_LIMIT:],
            "elapsed_s": round(time.monotonic() - started, 3),
        }


# ---------------------------------------------------------------------------
# 2. outbound URLs (api_integrator, network_client, researcher, external agents)
# ---------------------------------------------------------------------------

# Documentation ranges (RFC 5737 / RFC 3849) are not routable on the internet;
# they are allowed so the deterministic test transport (192.0.2.x) keeps working.
_DOC_NETS = [ipaddress.ip_network(n) for n in
             ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "2001:db8::/32")]
# IPv6 prefixes that carry an IPv4 address inside them: the embedded IPv4 is
# what a translator would actually reach, so it is checked too.
_NAT64_NETS = [ipaddress.ip_network(n) for n in ("64:ff9b::/96", "64:ff9b:1::/48")]


class UnsafeURLError(ValueError):
    """The URL targets a scheme or address the policy does not allow."""


def private_network_allowed() -> bool:
    return _env_flag("AGOS_ALLOW_PRIVATE_NETWORK") is True


def is_public_address(ip: str) -> bool:
    """True for globally routable addresses (and the documentation ranges)."""
    addr = ipaddress.ip_address(ip.split("%", 1)[0])
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        addr = mapped
    if addr.version == 6:
        embedded = addr.sixtofour
        if embedded is None and any(addr in net for net in _NAT64_NETS):
            embedded = ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)
        if embedded is not None:
            return is_public_address(str(embedded))
    if any(addr in net for net in _DOC_NETS):
        return True
    return bool(addr.is_global) and not addr.is_multicast


def resolve_host(host: str, port: Optional[int] = None) -> List[str]:
    """All addresses ``host`` resolves to (literal IPs are returned as-is)."""
    try:
        ipaddress.ip_address(host.strip("[]"))
        return [host.strip("[]")]
    except ValueError:
        pass
    infos = socket.getaddrinfo(host, port or 0, proto=socket.IPPROTO_TCP)
    return sorted({info[4][0] for info in infos})


def check_resolved(host: str, addrs: List[str],
                   allow_private: Optional[bool] = None) -> None:
    """SEC-05: apply the outbound address policy to an ALREADY-RESOLVED set.

    The single policy decision shared by every transport layer: ALL addresses
    of the answer are checked (a mixed IPv4/IPv6 answer with one internal
    address is rejected, not cherry-picked), an empty or unusable answer is
    rejected, and the caller gets the addresses it may connect to.  This
    function never resolves anything itself — the transport hands it the exact
    results it is about to connect to, so the check and the connection cannot
    drift apart (DNS rebinding).

    Every entry must be a usable IP literal: a hostname or garbage inside the
    answer would make ``socket.connect`` resolve it AGAIN — outside this guard
    — so anything that does not parse as an address fails closed.  The
    ``allow_private`` exception only reclassifies an address (public vs
    internal); it never disables this usability check.
    """
    if not host:
        raise UnsafeURLError("missing host")
    if not addrs:
        raise UnsafeURLError(f"no usable addresses for {host}")
    for a in addrs:
        try:
            ipaddress.ip_address(a.split("%", 1)[0])
        except ValueError:
            raise UnsafeURLError(
                f"unusable address in DNS answer for {host}") from None
    if allow_private is None:
        allow_private = private_network_allowed()
    if not allow_private:
        bad = [a for a in addrs if not is_public_address(a)]
        if bad:
            raise UnsafeURLError(
                f"blocked non-public address for {host}: {', '.join(bad)} "
                "(set AGOS_ALLOW_PRIVATE_NETWORK=1 to allow internal targets)")


def check_host(host: str, port: Optional[int] = None, allow_private: Optional[bool] = None) -> List[str]:
    """Resolve ``host`` and raise UnsafeURLError if any address is non-public."""
    if not host:
        raise UnsafeURLError("missing host")
    if allow_private is None:
        allow_private = private_network_allowed()
    try:
        addrs = resolve_host(host, port)
    except OSError:
        # R-146b: a raw resolver error (its text is not ours to forward) is
        # converted to a fixed policy reason — the request fails closed.
        raise UnsafeURLError(f"DNS resolution failed for {host}") from None
    check_resolved(host, addrs, allow_private)
    return addrs


def check_url(url: str, allow_private: Optional[bool] = None) -> str:
    """Validate scheme + every resolved address of ``url``; return the URL."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise UnsafeURLError(f"scheme not allowed: {parts.scheme or '(none)'}")
    try:
        port = parts.port
    except ValueError as e:
        raise UnsafeURLError(f"invalid port: {e}") from None
    check_host(parts.hostname or "", port, allow_private)
    return url


class _CheckedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-applies ``check_url`` to every redirect target."""

    max_redirections = 5

    def __init__(self, allow_private: Optional[bool]):
        super().__init__()
        self._allow_private = allow_private

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl, self._allow_private)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# R-121: ONE verifying TLS context for every outbound HTTPS request. Without an
# explicit context, urllib builds a new default context per connection, and the
# system CA bundle is parsed again on every handshake; the freed certificate
# memory stayed held by the allocator (~0.7 MB per request; VmRSS 361 MB after
# the R-120 hour, `docs/audit/M7/logs/raw/133_r121_root_cause.log`). Same
# verification as before (ssl.create_default_context: CERT_REQUIRED +
# check_hostname); an SSLContext is safe to share across threads.
_TLS_CONTEXT: Optional[ssl.SSLContext] = None
_TLS_LOCK = threading.Lock()


def tls_context() -> ssl.SSLContext:
    global _TLS_CONTEXT
    if _TLS_CONTEXT is None:
        with _TLS_LOCK:
            if _TLS_CONTEXT is None:
                _TLS_CONTEXT = ssl.create_default_context()
    return _TLS_CONTEXT


def vetted_sockaddrs(host: str, port: Optional[int] = None,
                      allow_private: Optional[bool] = None) -> List[Tuple[int, tuple]]:
    """SEC-05: resolve ``host`` ONCE and return the vetted connect targets.

    ``socket.getaddrinfo`` runs a single time; the outbound address policy is
    applied to EVERY address of that answer (``check_resolved``); the caller
    receives the exact ``(family, sockaddr)`` pairs it may open a socket to.
    A DNS failure raises ``UnsafeURLError`` (fail closed) and an empty answer
    is rejected — the request never falls back to an unguarded transport.
    """
    if not host:
        raise UnsafeURLError("missing host")
    try:
        infos = socket.getaddrinfo(host, port or 0, 0, socket.SOCK_STREAM,
                                   socket.IPPROTO_TCP)
    except OSError:
        raise UnsafeURLError(f"DNS resolution failed for {host}") from None
    addrs = sorted({info[4][0] for info in infos})
    check_resolved(host, addrs, allow_private)
    return [(info[0], info[4]) for info in infos]


def _connect_vetted(host: str, port: Optional[int], timeout, source_address,
                    allow_private: Optional[bool]) -> socket.socket:
    """Open a socket directly to a vetted sockaddr (SEC-05).

    ``socket.create_connection`` is deliberately NOT used: it re-resolves the
    hostname, which is exactly the gap this closes.  Every candidate address
    comes from ``vetted_sockaddrs`` — the same resolution the policy checked.
    """
    sockaddrs = vetted_sockaddrs(host, port, allow_private)
    last_exc: Optional[OSError] = None
    for family, sockaddr in sockaddrs:
        s = socket.socket(family, socket.SOCK_STREAM)
        try:
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                s.settimeout(timeout)
            if source_address:
                s.bind(source_address)
            s.connect(sockaddr)
            return s
        except OSError as e:
            last_exc = e
            try:
                s.close()
            except OSError:
                pass
    raise last_exc if last_exc is not None else UnsafeURLError(
        f"no usable addresses for {host}")


class _GuardedHTTPConnection(http.client.HTTPConnection):
    """``HTTPConnection`` that connects only to policy-vetted addresses.

    The hostname in the request line and the ``Host`` header stays the ORIGINAL
    URL hostname — only the socket target is pinned to a vetted sockaddr.
    """

    def __init__(self, host, **kwargs):
        self._allow_private = kwargs.pop("allow_private", None)
        super().__init__(host, **kwargs)

    def connect(self):
        sys.audit("http.client.connect", self, self.host, self.port)
        self.sock = _connect_vetted(self.host, self.port, self.timeout,
                                    self.source_address, self._allow_private)
        try:
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError as e:
            if e.errno != errno.ENOPROTOOPT:
                raise


class _GuardedHTTPSConnection(http.client.HTTPSConnection):
    """``HTTPSConnection`` that connects only to policy-vetted addresses.

    TLS keeps using the ORIGINAL URL hostname for SNI and certificate
    verification (``server_hostname``); connecting to a vetted IP never
    weakens the identity check.
    """

    def __init__(self, host, **kwargs):
        self._allow_private = kwargs.pop("allow_private", None)
        super().__init__(host, **kwargs)

    def connect(self):
        sys.audit("http.client.connect", self, self.host, self.port)
        self.sock = _connect_vetted(self.host, self.port, self.timeout,
                                    self.source_address, self._allow_private)
        try:
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError as e:
            if e.errno != errno.ENOPROTOOPT:
                raise
        if self._tunnel_host:
            self._tunnel()
            server_hostname = self._tunnel_host
        else:
            server_hostname = self.host
        self.sock = self._context.wrap_socket(self.sock,
                                              server_hostname=server_hostname)


class _GuardedHTTPHandler(urllib.request.HTTPHandler):
    """HTTP handler whose connections are pinned to vetted addresses."""

    def __init__(self, allow_private: Optional[bool]):
        super().__init__()
        self._allow_private = allow_private

    def http_open(self, req):
        return self.do_open(
            lambda *a, **k: _GuardedHTTPConnection(
                *a, allow_private=self._allow_private, **k), req)


class _GuardedHTTPSHandler(urllib.request.HTTPSHandler):
    """HTTPS handler whose connections are pinned to vetted addresses.

    Same shared, verifying TLS context as before (R-121); only the socket
    target is pinned.  SNI and certificate verification keep the URL hostname.
    """

    def __init__(self, allow_private: Optional[bool], context=None):
        super().__init__(context=context)
        self._allow_private = allow_private

    def https_open(self, req):
        return self.do_open(
            lambda *a, **k: _GuardedHTTPSConnection(
                *a, allow_private=self._allow_private, **k), req,
            context=self._context)


def safe_urlopen(req: urllib.request.Request, timeout: float, allow_private: Optional[bool] = None):
    """``urlopen`` with the outbound policy pinned to the actual connection.

    SEC-05: the address policy is applied inside the connection layer — the
    socket is opened directly to an address from the SAME resolution the
    policy checked, so a DNS answer that changes between a check and the
    connection (rebinding) cannot make this path reach an unvetted address.
    The early ``check_url`` is kept as a fast pre-check; the security decision
    is the vetted connection itself.  Redirects are re-checked per hop
    (R-100/SEC-01) and every redirected request opens a new guarded connection.

    Proxies from the environment are NOT honored for these protected requests:
    a proxy would resolve the destination itself and the vetted-address
    guarantee could not be kept (documented in OPERATIONS.md).
    """
    check_url(req.full_url, allow_private)
    # R-146b: an explicit proxy on the Request (set_proxy rewrites req.host and
    # sets a tunnel for HTTPS) would make the PROXY resolve the destination —
    # voiding the vetted-address guarantee.  No trusted-proxy design exists,
    # so such requests are refused before any connection.
    if getattr(req, "_tunnel_host", None) is not None:
        raise UnsafeURLError("explicit proxies are not supported for protected requests")
    if req.host != urllib.parse.urlsplit(req.full_url).netloc:
        raise UnsafeURLError("explicit proxies are not supported for protected requests")
    opener = urllib.request.build_opener(
        _CheckedRedirectHandler(allow_private),
        _GuardedHTTPHandler(allow_private),
        _GuardedHTTPSHandler(allow_private, context=tls_context()),
        urllib.request.ProxyHandler({}))
    handlers = list(opener.handlers)
    try:
        return opener.open(req, timeout=timeout)
    finally:
        # R-124: the opener and its handlers form a reference cycle
        # (opener tables -> handler.parent -> opener) that only the cyclic GC
        # frees; before R-121 each cycle also pinned a fresh TLS context (~0.7 MB),
        # which is why C memory piled up "in use" and was freed in bulk
        # (docs/audit/M7/logs/raw/142_r124_late_release.log). Cutting each
        # handler's back-link frees the opener by refcount on return; redirects
        # and error handling have already finished inside open(), and the
        # response does not reference the opener.
        for h in handlers:
            h.parent = None
        opener.close()


# ---------------------------------------------------------------------------
# 3. SQLite (db_manager)
# ---------------------------------------------------------------------------

SQLITE_TIME_BUDGET_S = 3.0
SQLITE_MAX_ROWS = 50
# Largest string/blob a statement may build (default SQLite allows ~1 GB, which
# a single ``randomblob(1000000000)`` would allocate inside the server process).
SQLITE_MAX_LENGTH = 1_000_000
SQLITE_MAX_SQL_LENGTH = 100_000
_SQLITE_DENY = {getattr(sqlite3, n) for n in ("SQLITE_ATTACH", "SQLITE_DETACH") if hasattr(sqlite3, n)}
_DENIED_PRAGMAS = {"temp_store_directory", "data_store_directory"}


def _sqlite_authorizer(action, arg1, arg2, dbname, source):
    if action in _SQLITE_DENY:
        return sqlite3.SQLITE_DENY
    if action == getattr(sqlite3, "SQLITE_PRAGMA", -1) and (arg1 or "").lower() in _DENIED_PRAGMAS:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def run_sqlite_statements(statements: Iterable[str], time_budget_s: float = SQLITE_TIME_BUDGET_S):
    """Execute statements in a private in-memory DB with a time budget.

    ``ATTACH``/``DETACH`` are denied by an authorizer (``VACUUM INTO`` attaches
    internally, so it is denied too — verified by tests); a progress handler aborts statements that exceed the
    budget, so a runaway recursive CTE cannot hold a worker thread.
    Returns ``(rows, columns)`` of the last statement.
    """
    conn = sqlite3.connect(":memory:")
    try:
        conn.set_authorizer(_sqlite_authorizer)
        if hasattr(conn, "setlimit"):  # Python >= 3.11 (the supported floor)
            conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, SQLITE_MAX_LENGTH)
            conn.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, SQLITE_MAX_SQL_LENGTH)
        deadline = time.monotonic() + time_budget_s
        conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
        cur = conn.cursor()
        for stmt in statements:
            cur.execute(stmt)
        rows = cur.fetchmany(SQLITE_MAX_ROWS) if cur.description else []
        cols = [c[0] for c in cur.description] if cur.description else []
        return rows, cols
    finally:
        conn.close()


def policy_snapshot() -> Dict[str, object]:
    """What the running process allows — surfaced by /api/system/status."""
    allowed, reason = code_execution_policy()
    return {
        "code_execution": {"allowed": allowed, "reason": reason, "limits": dict(SANDBOX_LIMITS),
                           "wall_timeout_s": SANDBOX_WALL_TIMEOUT_S,
                           "server_process_non_dumpable": _HARDENED},
        "outbound_network": {"private_targets_allowed": private_network_allowed()},
        "sqlite": {"time_budget_s": SQLITE_TIME_BUDGET_S, "attach": "denied",
                   "max_value_bytes": SQLITE_MAX_LENGTH},
    }
