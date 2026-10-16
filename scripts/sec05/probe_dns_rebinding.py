#!/usr/bin/env python3
"""SEC-05 probe: DNS rebinding between the policy check and the connection.

Deterministic, local-only proof (no real DNS, no public network):

* a controlled ``socket.getaddrinfo`` answers for the probe hostnames: the
  FIRST resolution returns an address the outbound policy allows
  (192.0.2.1, RFC 5737 documentation range), every LATER resolution returns an
  internal listener address (127.0.0.2, loopback);
* an HTTP listener on 127.0.0.2 counts the requests it receives.

Both real outbound paths are exercised with no mocking of the paths
themselves — only the DNS answers are controlled:

1. ``safety.safe_urlopen`` (urllib): ``check_url`` resolves the host (allowed
   answer), then ``http.client`` creates the connection — resolving the name
   a SECOND time (the rebinding answer).
2. ``ExternalAgentAdapter._post_checked`` (aiohttp): ``safety.check_url``
   resolves the host (allowed answer), then aiohttp's default resolver
   resolves it again for the actual TCP connection.

BEFORE the SEC-05 fix the internal listener is reached (the gap).  AFTER the
fix the request fails closed before any TCP connection to the rebinding
address.  Run on both trees; the exit code is 0 only when every path is
blocked (i.e. the fixed behavior).
"""
from __future__ import annotations

import asyncio
import http.server
import ipaddress
import json
import socket
import threading
import urllib.request

import sys
sys.path.insert(0, ".")

from agent_civilization.agents.external_adapter import ExternalAgentAdapter  # noqa: E402
from agent_civilization.core import safety  # noqa: E402

LISTEN_HOST = "127.0.0.2"          # loopback /8: local-only, stays non-public
FIRST_ANSWER = "192.0.2.1"         # allowed by the policy (documentation range)


def _install_connect_barrier() -> None:
    """A local connection barrier: a non-loopback connect raises.

    The probe runs outside pytest, so it does not inherit the suite's offline
    guard — this restores the same guarantee standalone.  A plain wrapper
    function is installed (NOT a pre-bound method: assigning a bound method to
    the class would leave the wrapper reading the barrier as ``sock`` and
    break the call).  ``_selfcheck_connect_barrier`` proves the barrier with a
    real loopback connect AND a refused non-loopback target before the probe
    relies on it.
    """
    orig = socket.socket.connect

    def guarded(sock, addr):
        if isinstance(addr, tuple):
            try:
                loopback = ipaddress.ip_address(
                    str(addr[0]).strip("[]")).is_loopback
            except ValueError:
                loopback = False
            if not loopback:
                raise OSError(
                    f"probe barrier: non-loopback connect to {addr!r} refused")
        return orig(sock, addr)

    socket.socket.connect = guarded  # noqa: E501


def _selfcheck_connect_barrier() -> None:
    """Positive + negative control of the barrier itself."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        ok = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ok.settimeout(3)
        try:
            ok.connect(("127.0.0.1", port))   # loopback must pass
            print("[barrier ] self-check: loopback connect allowed")
        finally:
            ok.close()
        try:
            bad = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            bad.settimeout(3)
            try:
                bad.connect(("192.0.2.254", port))  # non-loopback must refuse
                raise SystemExit("barrier self-check FAILED: connect allowed")
            finally:
                bad.close()
        except OSError as e:
            if "probe barrier" not in str(e):
                raise SystemExit(
                    f"barrier self-check FAILED: refused by something else: {e}")
            print("[barrier ] self-check: non-loopback connect refused")
    finally:
        server.close()


class _Listener(http.server.BaseHTTPRequestHandler):
    hits = 0

    def _serve(self):
        type(self).hits += 1
        body = json.dumps({"choices": [{"message": {"content": "INTERNAL"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = _serve

    def log_message(self, *a):
        pass


def _serve():
    srv = http.server.ThreadingHTTPServer((LISTEN_HOST, 0), _Listener)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class ControlledDNS:
    """First resolution per host: FIRST_ANSWER; afterwards: the listener."""

    def __init__(self):
        self._orig = socket.getaddrinfo
        self.calls: list[str] = []

    def fake(self, host, *args, **kwargs):
        host = str(host)
        port = args[0] if args else kwargs.get("port", 0)
        if host.endswith(".probe"):
            self.calls.append(host)
            answer = FIRST_ANSWER if self.calls.count(host) == 1 else LISTEN_HOST
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP,
                     "", (answer, int(port or 0)))]
        return self._orig(host, *args, **kwargs)


def main() -> int:
    print("SEC-05 DNS-rebinding probe —", end=" ")
    print("controlled DNS, listener on 127.0.0.2, no external network")
    _install_connect_barrier()
    _selfcheck_connect_barrier()
    srv = _serve()
    port = srv.server_address[1]
    dns = ControlledDNS()
    socket.getaddrinfo = dns.fake  # noqa: E501
    blocked = []
    rejections = []

    # --- path 1: safety.safe_urlopen (urllib) ------------------------------
    _Listener.hits = 0
    try:
        with safety.safe_urlopen(
                urllib.request.Request(f"http://urllib.probe:{port}/x"), timeout=5) as r:
            body = r.read().decode()
        result = f"CONNECTED to listener (HTTP {r.status}, body={body[:40]!r})"
    except safety.UnsafeURLError as e:
        result = f"failed closed: {type(e).__name__}: {e}"
        rejections.append(("urllib", str(e)))
    except Exception as e:  # noqa: BLE001 — the probe reports any failure
        result = f"UNEXPECTED failure: {type(e).__name__}: {e}"
    print(f"[urllib  ] {result}")
    print(f"[urllib  ] internal listener hits: {_Listener.hits}")
    if _Listener.hits:
        blocked.append("urllib")
    print(f"[urllib  ] resolutions of urllib.probe: "
          f"{dns.calls.count('urllib.probe')}")

    # --- path 2: ExternalAgentAdapter (aiohttp) ------------------------------
    _Listener.hits = 0
    adapter = ExternalAgentAdapter("probe", f"http://aiohttp.probe:{port}/v1/chat/completions")
    out = asyncio.run(adapter.execute({"prompt": "hi"}))
    ok = out["ok"]
    print(f"[aiohttp ] ok={ok} result={json.dumps(out['result'])[:120]}")
    print(f"[aiohttp ] internal listener hits: {_Listener.hits}")
    if _Listener.hits:
        blocked.append("aiohttp")
    if not ok:
        rejections.append(("aiohttp", str(out["result"].get("error", ""))))
    print(f"[aiohttp ] resolutions of aiohttp.probe: "
          f"{dns.calls.count('aiohttp.probe')}")

    srv.shutdown()
    srv.server_close()

    if blocked:
        print(f"GAP PRESENT (before fix): the internal listener was reached "
              f"via {', '.join(blocked)} — DNS rebinding between check and "
              "connect is live.")
        return 1
    # The fixed behavior is the EXPECTED POLICY REJECTION on both paths — not
    # just any failure — with the internal listener never reached.
    if len(rejections) != 2 or not all(
            "blocked non-public address" in r for _, r in rejections):
        print(f"UNEXPECTED: not the policy rejection. {rejections}")
        return 1
    print("BLOCKED (after fix): neither path reached the internal listener; "
          "both failed with the expected policy rejection.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
