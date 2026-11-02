"""R-173 batch 2 (D-119) — the live MCP client (streamable-HTTP).

What this module IS: the execution binding for imported MCP tools. It has
two jobs, both behind the same SSRF/DNS-rebinding wall:

1. **Live manifest fetch** — ``import_from_uri`` performs the real MCP
   handshake (``initialize`` → ``notifications/initialized`` → ``tools/list``)
   over streamable-HTTP against an operator-named server URL, then hands the
   manifest to the batch-1 importer (``tool_import.import_mcp_manifest``)
   with ``server_uri`` set — the imported records then carry the server
   origin in their ``network_allowlist`` (allowlist of intent).
2. **Live tool execution** — ``call_imported`` runs one imported tool via
   ``tools/call``. It is reached ONLY through the runtime gate
   (``tool_runtime.dispatch``), which enforces the record's lifecycle,
   health, approval policy, scopes, timeout and output caps before this
   code runs, and re-checks the binding here (double enforcement): the
   destination origin must be covered by the record's own
   ``network_allowlist`` — both fields are digest-protected record
   content, so the binding cannot be swapped without an auditable new
   version.

SSRF / DNS-rebinding protections (the roadmap's explicit text for this
batch): ``check_url`` refuses non-HTTP(S) schemes, userinfo, missing
hosts, fragments and non-public destinations (loopback, private ranges,
link-local — including cloud metadata, reserved, multicast) by default; a
hostname that RESOLVES to a non-public address is refused exactly like a
literal one. A local (private) server is reachable only through the
operator's explicit ``allow_private=True`` decision at import time — never
from anything the external source controls. After the socket is
established the connected peer is re-validated against the addresses the
pre-flight check approved: a name that re-resolves mid-flight (DNS
rebinding) is refused and the connection torn down. TLS (https) keeps the
stdlib's default certificate verification.

Honest limits (declared, D-119): OpenAPI imports stay records-only (no
execution adapter — the owner's directive for this batch); the MCP
``tools/call`` arguments travel as given in ``work['arguments']`` (the
record's input schema is structural, not a runtime validator — batch-1
declared limit); no authentication material is sent (Q-024 default
policy: owner-secret-free tools only); private destinations beyond an
explicitly allowlisted private origin remain unreachable (fail-closed).
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import socket
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from agent_civilization.core.tool_import import (
    DEFAULT_TIMEOUT_MS,
    LATEST_MCP_REVISION,
    MCP_PROTOCOL_REVISIONS,
    ImportReport,
    ToolRegistry,
    import_mcp_manifest,
    origin,
)

#: JSON-RPC over HTTP (MCP streamable-HTTP transport, stable revisions).
_RPC_HEADERS_BASE = {"Content-Type": "application/json",
                     "Accept": "application/json, text/event-stream"}
_CLIENT_INFO = {"name": "agos-mcp-client", "version": "1.0.0"}


class McpClientError(Exception):
    """An honest client-side failure (SSRF refusal, transport or protocol
    error). The message is policy facts only — it never carries the work
    payload, and the runtime gate re-redacts it anyway."""


# --------------------------------------------------------------------------
# SSRF / DNS-rebinding checks
# --------------------------------------------------------------------------

def _is_public(ip_text: str) -> bool:
    """A destination is reachable only if it is a global unicast address —
    loopback, private, link-local (cloud metadata), reserved, multicast and
    unspecified ranges are all refused (fail-closed against SSRF)."""
    try:
        ip = ipaddress.ip_address(ip_text)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped           # ::ffff:10.0.0.1 is 10.0.0.1
    return ip.is_global and not ip.is_reserved and not ip.is_multicast


def _resolved_ips(host: str,
                  resolver: Any = None) -> List[str]:
    """All addresses the name resolves to (a literal host is itself)."""
    try:
        ipaddress.ip_address(host)    # an IP literal needs no resolution
        return [host]
    except ValueError:
        pass
    if resolver is None:
        resolver = socket.getaddrinfo
    try:
        infos = resolver(host, None)
    except OSError as e:
        raise McpClientError(f"host {host!r} could not be resolved: "
                            f"{type(e).__name__}") from None
    ips: List[str] = []
    for info in infos:
        try:
            sockaddr = info[-1]
            ip = sockaddr[0]
        except (IndexError, TypeError):
            continue
        if ip not in ips:
            ips.append(ip)
    if not ips:
        raise McpClientError(f"host {host!r} resolved to no addresses")
    return ips


def check_url(uri: str, *, allow_private: bool = False,
              resolver: Any = None) -> List[str]:
    """Validate a server URL for fetching/execution — SSRF fail-closed.

    Returns the validated addresses (the connection is later re-validated
    against them). Raises ``McpClientError`` with an explicit reason for
    every refusal: scheme not http/https, userinfo present, no host,
    fragment present, bad port, a non-public destination (literal or
    resolved), or an unresolvable host. ``allow_private`` is the operator's
    explicit decision for a local server — never inferred from the source.
    """
    parts = urlsplit(uri)
    if parts.scheme not in ("http", "https"):
        raise McpClientError(f"scheme {parts.scheme!r} is not allowed "
                             "(http/https only)")
    if not parts.hostname:
        raise McpClientError("URL has no host")
    if parts.username or parts.password:
        raise McpClientError("URL userinfo is not allowed")
    if parts.fragment:
        raise McpClientError("URL fragment is not allowed")
    try:
        port = parts.port
    except ValueError:
        raise McpClientError("URL port is out of range") from None
    if port is not None and not (1 <= port <= 65535):
        raise McpClientError("URL port is out of range")
    ips = _resolved_ips(parts.hostname, resolver)
    for ip in ips:
        if not _is_public(ip) and not allow_private:
            raise McpClientError(
                f"host resolves to a non-public address ({ip}) — refused "
                "(SSRF protection; an explicit operator allowance is "
                "required for a local server)")
    return ips


def _assert_peer(peer_ip: str, validated: List[str]) -> None:
    """DNS-rebinding guard: the socket actually landed on an address the
    pre-flight check approved — anything else is refused and the connection
    torn down."""
    if peer_ip in validated:
        return
    raise McpClientError(
        f"the connected peer {peer_ip} is not among the validated "
        f"addresses {validated} (possible DNS rebinding) — refused")


# --------------------------------------------------------------------------
# The streamable-HTTP MCP client
# --------------------------------------------------------------------------

class McpHttpClient:
    """A minimal JSON-RPC MCP client over streamable-HTTP (stdlib only).

    Each request is a fresh, fully re-validated connection: ``check_url``
    runs again (DNS may have changed), the peer is re-checked after
    connect, and the socket carries the timeout. The server's session id
    (``mcp-session-id``), when it issues one, is echoed on every call.
    """

    def __init__(self, uri: str, *, timeout_s: float = 10.0,
                 allow_private: bool = False):
        self.uri = uri
        self.timeout_s = max(0.1, float(timeout_s))
        self.allow_private = allow_private
        self._session_id: Optional[str] = None
        self._next_id = 0

    # -- transport ---------------------------------------------------------

    def _connect(self) -> http.client.HTTPConnection:
        parts = urlsplit(self.uri)
        validated = check_url(self.uri, allow_private=self.allow_private)
        conn_cls = (http.client.HTTPSConnection
                    if parts.scheme == "https" else http.client.HTTPConnection)
        host = parts.hostname or ""
        conn = conn_cls(host, parts.port, timeout=self.timeout_s)
        try:
            conn.connect()
        except OSError as e:
            raise McpClientError(f"connection to {origin(self.uri)} failed: "
                                 f"{type(e).__name__}: {e}") from None
        try:
            peer = conn.sock.getpeername()[0]
        except (AttributeError, OSError):
            conn.close()
            raise McpClientError("the connection has no peer address to "
                                 "validate") from None
        try:
            _assert_peer(peer, validated)
        except McpClientError:
            conn.close()
            raise
        return conn

    def _post(self, payload: Dict[str, Any], *, notification: bool = False
              ) -> Optional[Dict[str, Any]]:
        body = json.dumps(payload).encode("utf-8")
        headers = dict(_RPC_HEADERS_BASE)
        headers["Content-Length"] = str(len(body))
        if self._session_id:
            headers["mcp-session-id"] = self._session_id
        # request-target is path+query only (an absolute URI in the request
        # line is a proxy form — origin servers may refuse it)
        parts = urlsplit(self.uri)
        target = parts.path or "/"
        if parts.query:
            target = f"{target}?{parts.query}"
        conn = self._connect()
        try:
            conn.request("POST", target, body=body, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            sid = resp.headers.get("mcp-session-id")
            if sid:
                self._session_id = sid
            if resp.status >= 400:
                excerpt = data[:200].decode("utf-8", "replace")
                raise McpClientError(
                    f"the MCP server answered HTTP {resp.status}: {excerpt}")
            if notification:
                return None            # 202 Accepted, no body to parse
            if not data:
                raise McpClientError("the MCP server sent an empty response")
            return self._parse_message(resp.headers.get("Content-Type", ""),
                                        data)
        except McpClientError:
            raise
        except OSError as e:
            raise McpClientError(f"transport failure: {type(e).__name__}: "
                                 f"{e}") from None
        finally:
            conn.close()

    def _parse_message(self, content_type: str, data: bytes) -> Dict[str, Any]:
        ctype = (content_type or "").split(";")[0].strip().lower()
        if ctype == "text/event-stream":
            message = self._first_sse_message(data)
        else:
            message = self._json_or_fail(data)
        if not isinstance(message, dict):
            raise McpClientError("the MCP server sent a non-object message")
        if message.get("error"):
            err = message["error"]
            raise McpClientError(
                f"the MCP server returned a protocol error: "
                f"{err.get('message', err)}")
        if "result" not in message:
            raise McpClientError("the MCP server sent no result")
        return message

    @staticmethod
    def _first_sse_message(data: bytes) -> Any:
        """The first complete ``data:`` event of an SSE body (the request
        response channel — batch replies are out of scope for this client)."""
        for line in data.decode("utf-8", "replace").splitlines():
            line = line.strip()
            if line.startswith("data:"):
                return McpHttpClient._json_or_fail(line[len("data:"):].strip())
        raise McpClientError("the SSE stream carried no message")

    @staticmethod
    def _json_or_fail(text: str) -> Any:
        try:
            return json.loads(text)
        except ValueError:
            raise McpClientError("the MCP server sent malformed JSON") from None

    def _request(self, method: str, params: Dict[str, Any]) -> Any:
        self._next_id += 1
        message = self._post({"jsonrpc": "2.0", "id": self._next_id,
                              "method": method, "params": params})
        return message["result"]

    def _notify(self, method: str) -> None:
        self._post({"jsonrpc": "2.0", "method": method}, notification=True)

    # -- protocol ----------------------------------------------------------

    def initialize(self) -> str:
        """The MCP handshake; returns the server's protocol revision."""
        result = self._request("initialize", {
            "protocolVersion": LATEST_MCP_REVISION,
            "capabilities": {},
            "clientInfo": _CLIENT_INFO,
        })
        if not isinstance(result, dict):
            raise McpClientError("initialize returned no object")
        revision = str(result.get("protocolVersion") or "")
        if revision not in MCP_PROTOCOL_REVISIONS:
            raise McpClientError(
                f"unknown MCP revision {revision!r} from the server "
                f"(known stable: {', '.join(MCP_PROTOCOL_REVISIONS)})")
        self._notify("notifications/initialized")
        return revision

    def fetch_tools_manifest(self) -> Dict[str, Any]:
        """A tools/list-shaped manifest, live (the importer's batch-1 shape)."""
        revision = self.initialize()
        result = self._request("tools/list", {})
        tools = result.get("tools") if isinstance(result, dict) else None
        if not isinstance(tools, list):
            raise McpClientError("tools/list returned no tools list")
        return {"protocolVersion": revision, "tools": tools}

    def tools_call(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """One live tool call; returns the MCP result object."""
        result = self._request("tools/call", {"name": name,
                                               "arguments": arguments})
        if not isinstance(result, dict):
            raise McpClientError("tools/call returned no object")
        return result


# --------------------------------------------------------------------------
# Live import + execution binding
# --------------------------------------------------------------------------

def import_from_uri(uri: str, *, registry: ToolRegistry,
                    allowlist: List[str], actor: str,
                    allow_private: bool = False,
                    timeout_ms: int = DEFAULT_TIMEOUT_MS) -> ImportReport:
    """Fetch an MCP manifest live (SSRF-checked) and import it (batch-1 rules).

    ``allow_private`` is the operator's explicit decision that this URL may
    be a local server; the default (False) refuses every non-public
    destination. The fetched manifest is untrusted metadata exactly as in
    batch 1 — records land ``discovered``, non-executable, allowlisted
    names only, redacted text at the boundary. The difference: the records
    now carry the server origin in their ``network_allowlist`` (the binding
    of intent; the record schema stores the full URL in ``source.uri``).
    """
    client = McpHttpClient(uri, timeout_s=timeout_ms / 1000.0,
                           allow_private=allow_private)
    manifest = client.fetch_tools_manifest()
    return import_mcp_manifest(manifest, registry=registry, allowlist=allowlist,
                              actor=actor, server_uri=uri, timeout_ms=timeout_ms)


def _text_of(result: Dict[str, Any]) -> str:
    for item in result.get("content") or []:
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            return item["text"]
    return ""


def call_imported(rec: Any, work: Dict[str, Any]) -> Dict[str, Any]:
    """Run one imported MCP tool — the execution binding (sync: the runtime
    gate owns the timeout and runs this in the event loop's thread pool
    via ``asyncio.to_thread``).

    Double enforcement of the binding (the gate already refused records
    without a covered server origin): the destination is derived ONLY from
    the record's own ``source.uri`` and must be covered by the record's
    ``network_allowlist`` — both digest-protected. A private destination is
    reachable only when the allowlisted origin itself is an explicit
    private literal (the operator typed it at import with
    ``allow_private=True``); a public NAME that resolves to a private
    address is refused regardless (SSRF).
    """
    uri = getattr(rec.source, "uri", None)
    try:
        srv = origin(str(uri))
    except ValueError as e:
        return {"ok": False, "error": f"tool {rec.tool_id} refused: "
                                     f"its server URL is invalid ({e})"}
    if srv not in set(rec.network_allowlist or ()):
        return {"ok": False, "error": (
            f"tool {rec.tool_id} refused: the record's network allowlist "
            f"does not cover its own server origin {srv}")}
    args = work.get("arguments") if isinstance(work, dict) else None
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return {"ok": False, "error": (
            "work['arguments'] must be an object (the tool's arguments)")}
    # a private origin literal is the operator's explicit local-server
    # decision recorded in the allowlist itself
    parts = urlsplit(str(uri))
    host = parts.hostname or ""
    try:
        ipaddress.ip_address(host)
        allow_private = not _is_public(host)
    except ValueError:
        allow_private = host == "localhost"
    # socket backstop: the GATE owns the timeout (its wait_for fires at the
    # budget and its message is the one that surfaces); this is only the
    # reaper for the orphaned thread, so it gets a small slack on top
    client = McpHttpClient(str(uri),
                           timeout_s=rec.timeout_ms / 1000.0 + 0.25,
                           allow_private=allow_private)
    try:
        result = client.tools_call(rec.name, args)
    except McpClientError as e:
        return {"ok": False, "error": f"tool {rec.tool_id} failed: {e}"}
    if result.get("isError"):
        text = _text_of(result) or "the MCP server reported an error"
        return {"ok": False, "error": f"tool {rec.tool_id} failed: {text}"}
    return {"ok": True, "result": result}


__all__ = ["McpClientError", "McpHttpClient", "call_imported", "check_url",
           "import_from_uri", "origin"]
