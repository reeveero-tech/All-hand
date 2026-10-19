#!/usr/bin/env python3
"""
External Agent Adapter — attach real open-source agents to the civilization.

Any agent exposing an OpenAI-compatible chat endpoint (Ollama, LM Studio,
vLLM, OpenRouter, LocalAI) can join the civilization as a first-class citizen.

Register via env var EXTERNAL_AGENTS (JSON list) or the /agents/external API:

    EXTERNAL_AGENTS='[{"name":"my-ollama","url":"http://host:11434/v1/chat/completions","model":"llama3","api_key":""}]'

R-100 (D-044): every call re-checks the URL with ``safety.check_url``.  Agents
from EXTERNAL_AGENTS are operator configuration, so internal targets (a local
Ollama) are allowed.  Agents registered through the API may target internal
addresses only when the server runs with AGOS_API_KEY or
AGOS_ALLOW_PRIVATE_NETWORK=1 — otherwise an anonymous client could make the
server call its own private network (SSRF).

SEC-01: aiohttp follows redirects by default, which skipped that check for
every hop after the first (a public URL answering ``302 Location:
http://169.254.169.254/...`` reached the target).  Redirects are now followed
manually (``allow_redirects=False``) and ``safety.check_url`` is applied to
every target before it is contacted — the same policy ``safety.safe_urlopen``
applies to urllib.

SEC-05 (R-146): the session's ``TCPConnector`` uses ``GuardedResolver`` —
the addresses aiohttp connects to are exactly the ones the outbound policy
checked.  The resolver performs the DNS resolution, applies the policy to
EVERY address of that answer and hands the vetted IPs to the connector;
aiohttp opens the socket to a vetted result without resolving the hostname
again, so a DNS answer that changes between the early ``check_url`` and the
connection (rebinding) cannot reach an unvetted address.  Literal-IP URLs
never involve DNS (aiohttp bypasses the resolver for them) — the address is
deterministic and the early ``check_url`` already vets it.  The URL hostname
stays in the request, the ``Host`` header and TLS (SNI + certificate
verification).  DNS answers are not cached (``use_dns_cache=False``) so every
new connection resolves through the guarded resolver; a keep-alive reuse
inside one session only reuses a connection that was itself vetted.
"""

import asyncio
import json
import logging
import os
import socket
import time
from typing import Any, Dict, List
from urllib.parse import urljoin, urlsplit

import aiohttp

from agent_civilization.core import safety

logger = logging.getLogger("external_agents")


class GuardedResolver(aiohttp.abc.AbstractResolver):
    """SEC-05 (R-146): resolve once, vet EVERY address, connect to vetted IPs.

    ``resolve`` is the last security decision before the socket: it performs
    the single DNS resolution for the connection, applies the outbound
    address policy (``safety.check_resolved``) to ALL addresses of that answer
    and returns the vetted addresses to the connector — the connector then
    connects without resolving again.  A mixed answer (one public + one
    internal address) is rejected as a whole; DNS failure or an empty answer
    fails closed.  The URL hostname is untouched: aiohttp keeps it for the
    ``Host`` header and for TLS SNI + certificate verification.
    """

    def __init__(self, allow_private: bool):
        self._allow_private = allow_private

    async def resolve(self, host: str, port: int = 0,
                       family: int = 0) -> list:
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(
                host, port or 0, family=family or 0, type=socket.SOCK_STREAM,
                proto=socket.IPPROTO_TCP)
        except asyncio.CancelledError:
            raise
        except Exception:
            # R-146b: a fixed policy reason — a resolver failure of ANY kind
            # (its text is not ours to forward to logs or responses) fails
            # closed; cancellation still propagates as cancellation.
            # R-149 review round 4: raised as a _FixedClientError — the family
            # check below trusts only reasons THIS module formulates.
            raise _FixedClientError(
                f"DNS resolution failed for {host}") from None
        if not infos:
            raise _FixedClientError(f"no addresses for {host}")
        # R-146b: the SAME shared gate as the urllib path (safety.
        # validate_getaddrinfo) — the whole answer is policy-checked and every
        # entry validated (numeric connect target, integer port, family
        # match); a malformed entry fails closed with the fixed reason, never
        # with a raw extraction error.  UnsafeURLError (the policy rejection)
        # propagates as-is: it is already a clean, fixed reason.
        targets = safety.validate_getaddrinfo(host, infos, self._allow_private)
        # The connector gets the vetted IP literal as "host" (never a name to
        # re-resolve), the requested port, the socket family and the protocol
        # from the validated answer.  The sockaddr is consumed WITHOUT
        # assuming two elements — an IPv6 entry may carry flowinfo/scope_id.
        # R-146b review round 6 — the representability line, read from the
        # actual source of every supported Python (3.11/3.12/3.14): the
        # agent transport rebuilds its connect sockaddr from the host
        # literal alone.  A PLAIN literal takes asyncio's inet_pton fast
        # path (socket.connect, no resolution call — structurally safe).
        # A SCOPED literal ("ip%N") FAILS that fast path ("'%' in host" is
        # rejected in _ipaddr_info) and falls into loop.getaddrinfo(...,
        # flags=0) — an unguarded post-gate resolution call, which the
        # SEC-05 structural rule forbids.  Scoped IPv6 entries (numeric
        # scope_id, a "%" in the literal, or nonzero flowinfo) are therefore
        # REFUSED with this fixed reason instead of re-represented as a
        # string that opens that path; the urllib path keeps the vetted
        # NUMERIC sockaddr natively and needs no re-representation.
        results = []
        for family, proto, sockaddr in targets:
            ip, port = sockaddr[0], sockaddr[1]
            extras = sockaddr[2:]
            if extras:
                flowinfo, scope_id = extras
                if flowinfo or scope_id:
                    raise safety.UnsafeURLError(
                        f"IPv6 flowinfo/scope is not representable by the "
                        f"agent transport for {host}")
            if "%" in ip:
                raise safety.UnsafeURLError(
                    f"IPv6 scope is not representable by the agent "
                    f"transport for {host}")
            results.append({"hostname": host, "host": ip, "port": port,
                           "family": family, "proto": proto, "flags": 0})
        return results

    async def close(self) -> None:  # aiohttp resolver interface
        return None


class _HTTPStatusError(Exception):
    """The external backend answered a non-200 status. The status is the
    safe, sufficient reason (R-149); the backend's error body never enters
    the message — it becomes the tenant-visible result and the log."""

    def __init__(self, status: int):
        self.status = status
        super().__init__(f"HTTP {status}")


class _FixedReasonError(Exception):
    """An error whose message is OUR OWN fixed, safe text (R-149): it is
    reported as-is. Everything else unexpected is reduced to its TYPE."""


class _FixedClientError(_FixedReasonError, aiohttp.ClientConnectionError):
    """A connection-layer error with OUR OWN fixed text (the crafted DNS
    reasons). It is still an aiohttp.ClientConnectionError for the transport
    (R-146b tests assert that type from the resolver), but the reason below
    trusts only errors this module formulates itself — NOT the whole
    ClientConnectionError family (any code can raise that family with
    arbitrary text, e.g. a wrapped backend body)."""


async def _refuse_redirect(session, ctx, params) -> None:
    """R-161b: a guarded session does not follow redirects at all — the
    allowlisted endpoint must answer itself (no new hop, no new host)."""
    params.response.release()
    raise _FixedClientError("redirect refused by the outbound policy")


def guarded_session_kwargs(allow_private: bool, limit: int = 100) -> Dict[str, Any]:
    """R-161b (G-161-3): the SEC-05 guarantees for OTHER aiohttp clients
    (storage, LLM) — not a parallel mechanism: the same ``GuardedResolver``
    (one resolution per connection, EVERY address vetted by
    ``safety.validate_getaddrinfo``, the connector gets the vetted IP literal
    and never resolves again), no DNS cache, the verifying
    ``safety.tls_context()`` (SNI + certificate checked against the ORIGINAL
    hostname, which also stays in ``Host``), and redirects refused.

    Returns keyword arguments for ``aiohttp.ClientSession``.
    """
    trace = aiohttp.TraceConfig()
    trace.on_request_redirect.append(_refuse_redirect)
    # R-164: ``limit`` = the caller's connection bound (aiohttp's default 100).
    connector = aiohttp.TCPConnector(resolver=GuardedResolver(allow_private), limit=limit,
                                     use_dns_cache=False, ssl=safety.tls_context())
    return {"connector": connector, "trace_configs": [trace]}


class ExternalAgentAdapter:
    """Wraps a real remote agent behind the same execute() contract."""

    # SEC-01: same bound as safety._CheckedRedirectHandler (urllib path).
    MAX_REDIRECTS = 5

    def __init__(self, name: str, url: str, model: str = "", api_key: str = "",
                 allow_private: bool = False):
        self.name = name
        self.allow_private = allow_private
        self.url = url
        self.model = model
        self.api_key = api_key
        self.specialization = "external"
        self.status = "idle"
        self.stats = {"tasks_completed": 0, "tasks_failed": 0, "total_time_s": 0.0}

    async def execute(self, task: Dict[str, Any]) -> Dict[str, Any]:
        # R-159: one `agent` span (impl=external); the admin-given name is
        # recorded only if identifier-like (else "<redacted>"), never the URL/key
        from agent_civilization.core import telemetry
        with telemetry.span("agent", "external", impl="external", agent=self.name,
                            specialization="external") as sp:
            out = await self._execute(task)
            sp.set(outcome="ok" if out.get("ok") else "fail")
            return out

    async def _execute(self, task: Dict[str, Any]) -> Dict[str, Any]:
        prompt = str(task.get("prompt") or task.get("description") or "")
        started = time.time()
        self.status = "working"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload: Dict[str, Any] = {
            "messages": [{"role": "user", "content": prompt}],
        }
        if self.model:
            payload["model"] = self.model
        try:
            connector = aiohttp.TCPConnector(
                resolver=GuardedResolver(self.allow_private), use_dns_cache=False)
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120),
                                             connector=connector) as s:
                status, body = await self._post_checked(s, headers, payload)
            if status != 200:
                raise _HTTPStatusError(status)
            data = json.loads(body)
            # R-160 (handler result contract v1): the same completion rule as
            # the LLM client — a null/empty/structurally wrong answer is a
            # failure with a FIXED reason (it used to be ok=True, answer=None).
            from agent_civilization.core.llm import completion_content
            content = completion_content(data)
            self.stats["tasks_completed"] += 1
            ok, result = True, {"answer": content, "external_agent": self.name}
        except Exception as e:
            # R-149 (Security QA, review round 4): only a fixed safe reason is
            # reported. Trusted as-is: the backend's STATUS, UnsafeURLError's
            # refusal message, and OUR OWN fixed texts (_FixedReasonError — the
            # crafted DNS reasons and the redirect-loop bound). A TLS
            # certificate verification failure gets a fixed reason we formulate
            # (mismatch keeps its distinct text — the SEC-05 TLS test). The
            # ClientConnectionError FAMILY is NOT trusted by class: any code
            # can raise it with arbitrary text (a wrapped backend body can echo
            # the adapter's own credential); those are reduced to their TYPE.
            if isinstance(e, _HTTPStatusError):
                error = f"HTTP {e.status}"
            elif isinstance(e, (safety.UnsafeURLError, _FixedReasonError)):
                error = str(e)
            elif type(e).__name__ == "MalformedCompletion":
                error = e.reason          # one of two fixed strings (llm.py)
            elif isinstance(e, aiohttp.ClientConnectorCertificateError):
                reason = str(getattr(e, "certificate_error", "")).lower()
                if "hostname" in reason and "match" in reason:
                    error = "TLS certificate verification failed: hostname " \
                           "mismatch"
                else:
                    error = "TLS certificate verification failed"
            else:
                error = type(e).__name__
            logger.warning("external agent %s failed: %s", self.name, error)
            self.stats["tasks_failed"] += 1
            ok, result = False, {"error": error}
        elapsed = time.time() - started
        self.stats["total_time_s"] += elapsed
        self.status = "idle"
        return {
            "ok": ok,
            "agent": self.name,
            "specialization": "external",
            "elapsed_s": round(elapsed, 2),
            "result": result,
        }

    async def _post_checked(self, session, headers: Dict[str, str],
                            payload: Dict[str, Any]):
        """POST ``payload``, following redirects manually (SEC-01).

        ``safety.check_url`` runs on the first URL and on every redirect
        target BEFORE it is contacted (an early, redundant pre-check; the
        binding security decision is the ``GuardedResolver`` in the session's
        connector, SEC-05).  Method semantics match aiohttp's own
        redirect handling: 307/308 keep POST and the body; 303, and 301/302
        after a POST, switch to GET without a body.  The Authorization header
        is dropped when the redirect leaves the original origin (aiohttp does
        the same), so the agent's key is not sent to another host.
        """
        from agent_civilization.core import pools  # R-132: DNS check on the net pool
        url, method, body_json = self.url, "POST", payload
        headers = dict(headers)
        origin = urlsplit(self.url)[:2]
        for _hop in range(self.MAX_REDIRECTS + 1):
            await pools.run("net", safety.check_url, url, self.allow_private)
            # R-146b review round 7: a literal IPv6 address carrying a scope
            # in the URL ("%42") never reaches this transport's resolver —
            # the connector short-circuits IP literals — and the scoped
            # string would fall into an unguarded getaddrinfo(flags=0)
            # inside the connect path (the representability line read from
            # the asyncio source).  It is refused with the same fixed
            # reason here, for the first URL and EVERY redirect target,
            # BEFORE the request is sent.  The GuardedResolver remains the
            # final decision-maker for DNS answers.
            host = urlsplit(url).hostname or ""
            if "%" in host:
                raise safety.UnsafeURLError(
                    "IPv6 scope is not representable by the agent "
                    f"transport for {host}")
            kwargs: Dict[str, Any] = {"headers": headers, "allow_redirects": False}
            if body_json is not None:
                kwargs["json"] = body_json
            async with session.request(method, url, **kwargs) as r:
                status = r.status
                location = r.headers.get("Location")
                if status not in (301, 302, 303, 307, 308) or not location:
                    return status, await r.text()
            url = urljoin(url, location)
            if status == 303 or (status in (301, 302) and method == "POST"):
                method, body_json = "GET", None
                headers.pop("Content-Type", None)
            if urlsplit(url)[:2] != origin:
                headers.pop("Authorization", None)
        raise _FixedReasonError(f"too many redirects (> {self.MAX_REDIRECTS})")

    def snapshot(self) -> Dict[str, Any]:
        return {
            "id": -1,
            "name": self.name,
            "specialization": "external",
            "status": self.status,
            "url": self.url,
            "model": self.model,
            "stats": dict(self.stats),
        }


def load_external_agents_from_env() -> List[ExternalAgentAdapter]:
    raw = os.environ.get("EXTERNAL_AGENTS", "")
    if not raw:
        return []
    try:
        entries = json.loads(raw)
        return [
            ExternalAgentAdapter(
                name=e["name"], url=e["url"],
                model=e.get("model", ""), api_key=e.get("api_key", ""),
                allow_private=True,  # operator-provided configuration
            )
            for e in entries
        ]
    except Exception as e:
        # R-160 (G-159-1): type (and JSON position, if any) only — the message
        # of a decode/key/type error can echo the env value (urls, api keys).
        logger.error("Invalid EXTERNAL_AGENTS JSON: %s at line %s column %s",
                     type(e).__name__, getattr(e, "lineno", "?"), getattr(e, "colno", "?"))
        return []
