"""R-191 — the connectors framework: ONE reusable layer ABOVE the existing
point integrations (``external_adapter``, storage, LLM) — not a replacement
for them. The gap the roadmap names is the FRAMEWORK («الفجوة إطار قابل
لإعادة الاستخدام، لا «لا موصلات»»): every connector built here gets the
same documented review standards for free, so the next terminal costs
review, not re-invention.

The standards this framework enforces (each has a test in
``tests/test_r191_connectors.py``):
  * fixed-reason errors (R-149 discipline): a connector failure surfaces its
    TYPE and a safe reason — never the payload, the credential, or a raw
    exception text from the terminal;
  * credential invalidation: a 401 REVOKES the stored secret (R-189) and the
    next call fails FAST (no terminal hit); a new secret recovers the
    connector with no restart;
  * timeouts and retries are bounded and honest: the timeout is the
    connector's own budget (never retried — the effect may have happened);
    only a connection-level interruption (no response received) is retried,
    and ONLY when the call carries a stable task identity (R-188-b);
  * side-effect safety on retry: the identity produces an Idempotency-Key
    (``{task_id}:{op}``) the terminal honors — a duplicate applies once;
  * tenant isolation (SEC-02): the credential is read from the per-tenant
    secret store under the AMBIENT tenant — a tenantless call or another
    tenant's call never uses someone else's secret (fail-closed);
  * observability: per-connector stats (executed/failed/refused/
    auth_invalidated/retries) + a telemetry span per call;
  * the host allowlist: a connector refuses to talk to a host its own
    policy does not allow (the record's ``network_allowlist`` mirrors it).

How a connector tool reaches the gate: its spec name is NOT added to the
frozen dispatcher's ``_HANDLERS`` (the R-171 ``==100`` invariant is a test
the roadmap keeps); ``real_dispatcher.dispatch`` falls back to
``connectors.handler_for(spec)`` — the same documented FREEZE_DRIFT pattern
the tools_stdlib merge used. The registry record is NOT auto-seeded: an
operator registers it (``reg.register(github.tool_record(), actor=...)``)
— the D-129 phase-1 ceiling and the certify runbook stay untouched.
"""
from __future__ import annotations

import asyncio
import os
import threading

from typing import Any, Callable, Dict, List, Optional

__all__ = [
    "ConnectorError", "TransportInterrupted", "ConnectorTimeout",
    "PermissionRefused", "AuthInvalidated", "ConnectorPolicy", "Connector",
    "register", "get", "stats", "reset", "handler_for", "handler_specs",
]


# ---------------------------------------------------------------------------
# the fixed-reason error family (R-149: type + safe reason, never the payload)
# ---------------------------------------------------------------------------
class ConnectorError(RuntimeError):
    """A connector failure. The message is the TYPE + a FIXED safe reason —
    never the credential, the request payload, or raw terminal text. The
    framework only ever interpolates its OWN fixed strings here (R-149)."""
    safe_reason = "connector failure"

    def __init__(self, reason: Optional[str] = None):
        super().__init__(reason or type(self).safe_reason)
        self.reason = reason or type(self).safe_reason

    def __str__(self) -> str:
        return f"{type(self).__name__}: {self.reason}"


class TransportInterrupted(ConnectorError):
    """Connection-level: NO response was received, so the effect is UNKNOWN.
    The only retryable class — and only with a stable task identity."""
    safe_reason = "connection-level interruption (no response received)"


class ConnectorTimeout(ConnectorError):
    """The terminal did not answer within the connector's own bounded
    budget. Never retried — the effect may already have happened."""
    safe_reason = "the terminal did not answer within the bounded timeout"


class PermissionRefused(ConnectorError):
    """The terminal refused the credential's permissions (403). The caller
    must fix the grant, not retry."""
    safe_reason = "the terminal refused the credential's permissions"


class AuthInvalidated(ConnectorError):
    """The credential is missing, was revoked, or was invalidated by a 401.
    The next call fails fast (no terminal hit) until a new secret is put."""
    safe_reason = "the credential is missing or was invalidated (401 revoked it)"


# ---------------------------------------------------------------------------
# the bounded policy (review standard: timeouts and retries)
# ---------------------------------------------------------------------------
class ConnectorPolicy:
    """Bounded by construction: an unbounded policy is REFUSED, not clamped.

    ``timeout_s``   0 < x <= 120   — the connector's own budget per attempt
    ``max_attempts`` 1 <= x <= 5   — total attempts (1 = never retry)
    ``backoff_base_s`` >= 0        — fixed base delay between attempts
    """

    def __init__(self, *, timeout_s: float = 30.0, max_attempts: int = 3,
                 backoff_base_s: float = 0.1):
        if not (0.0 < float(timeout_s) <= 120.0):
            raise ValueError(f"timeout_s must be 0 < x <= 120, got {timeout_s!r}")
        if not (1 <= int(max_attempts) <= 5):
            raise ValueError(f"max_attempts must be 1 <= x <= 5, got {max_attempts!r}")
        if float(backoff_base_s) < 0.0:
            raise ValueError(f"backoff_base_s must be >= 0, got {backoff_base_s!r}")
        self.timeout_s = float(timeout_s)
        self.max_attempts = int(max_attempts)
        self.backoff_base_s = float(backoff_base_s)


# ---------------------------------------------------------------------------
# per-connector stats (observability)
# ---------------------------------------------------------------------------
_stats_lock = threading.Lock()
_stats: Dict[str, Dict[str, int]] = {}

_STAT_FIELDS = ("executed", "failed", "refused", "auth_invalidated", "retries")


def _bump(connector_id: str, field: str, by: int = 1) -> None:
    with _stats_lock:
        row = _stats.setdefault(connector_id,
                                {f: 0 for f in _STAT_FIELDS})
        row[field] = row.get(field, 0) + by


def stats() -> Dict[str, Dict[str, int]]:
    """A deep copy of the per-connector counters (executed, failed,
    refused, auth_invalidated, retries) — every REGISTERED connector has a
    row, zeroed, even before its first call."""
    with _stats_lock:
        out = {cid: {f: 0 for f in _STAT_FIELDS}
               for cid in _connectors.keys()}
        for cid, row in _stats.items():
            out.setdefault(cid, {f: 0 for f in _STAT_FIELDS})
            out[cid].update(row)
        return out


# ---------------------------------------------------------------------------
# the connector registry (per process; tests reset it)
# ---------------------------------------------------------------------------
_connectors: Dict[str, "Connector"] = {}
_tool_handlers: Dict[str, Callable] = {}


def register(connector: "Connector") -> None:
    """Register a connector instance under its id (and its tool handler)."""
    _connectors[connector.connector_id] = connector


def get(connector_id: str) -> Optional["Connector"]:
    return _connectors.get(connector_id)


def handler_for(spec: str) -> Optional[Callable]:
    """The dispatcher's fallback lookup: the async tool handler for a
    registered connector tool spec, or None (the frozen contract holds)."""
    return _tool_handlers.get(spec)


def handler_specs() -> List[str]:
    return sorted(_tool_handlers.keys())


def reset() -> None:
    """Tests: clear connectors, tool handlers, and stats."""
    with _stats_lock:
        _stats.clear()
    _connectors.clear()
    _tool_handlers.clear()


# ---------------------------------------------------------------------------
# the lazy per-tenant secret store (R-189; env-configured, fail-closed)
# ---------------------------------------------------------------------------
_store_lock = threading.Lock()
_secret_store_instance = None
_secret_store_key: Optional[tuple] = None


def _secret_store():
    """The process singleton over AGOS_SECRETS_DB / AGOS_MASTER_KEY_FILE.
    The store is file-backed and opens a connection per operation, so
    rotation and revocation from any process sharing the file apply
    immediately (no cache). The singleton is KEYED on the env pair: a changed
    configuration rebuilds it (tests point each test at its own db file)."""
    global _secret_store_instance, _secret_store_key
    db = (os.environ.get("AGOS_SECRETS_DB") or "").strip()
    key = (os.environ.get("AGOS_MASTER_KEY_FILE") or "").strip()
    if not db or not key:
        raise AuthInvalidated(
            "no secret store is configured (AGOS_SECRETS_DB/AGOS_MASTER_KEY_FILE)")
    with _store_lock:
        if _secret_store_instance is not None and _secret_store_key == (db, key):
            return _secret_store_instance
        from agent_civilization.core.secret_store import SecretStore
        _secret_store_instance = SecretStore(db, key)
        _secret_store_key = (db, key)
    return _secret_store_instance


# ---------------------------------------------------------------------------
# the base connector
# ---------------------------------------------------------------------------
class Connector:
    """A terminal connector. Subclasses set ``connector_id`` and implement
    ``build_request`` (op -> method/path/body from the work item); the base
    class owns the WHOLE review-standard envelope: credentials, timeouts,
    retries, idempotency, tenant isolation, and stats.

    ``allowlist_hosts``: the connector refuses any target host not in this
    list (fail-closed) — the tool record's ``network_allowlist`` mirrors it.
    """
    connector_id: str = "connector"
    kind: str = "terminal"

    def __init__(self, *, base_url: str, policy: ConnectorPolicy,
                 credential_name: str, allowlist_hosts: List[str]):
        from urllib.parse import urlparse
        if not allowlist_hosts:
            raise ValueError("allowlist_hosts must name the terminal hosts")
        host = urlparse(base_url).hostname or ""
        if host not in allowlist_hosts:
            raise ValueError(
                f"target host {host!r} is not in the connector's allowlist")
        self.base_url = base_url.rstrip("/")
        self.policy = policy
        self.credential_name = credential_name
        self.allowlist_hosts = list(allowlist_hosts)

    # -- request shape (the subclass's only job) --------------------------
    def build_request(self, op: str, data: Dict[str, Any]):
        """Return (method, path, json_body) for an op. Raise ConnectorError
        for an unknown op (a contract, not a crash)."""
        raise ConnectorError("unknown connector op")

    # -- the reviewed call -------------------------------------------------
    async def call(self, op: str, data: Dict[str, Any], *, tenant: str,
                   task_id: Optional[str] = None) -> Any:
        """One reviewed terminal call. See the module docstring for the
        standards this envelope enforces."""
        from agent_civilization.core import telemetry
        cid = self.connector_id
        _bump(cid, "executed")
        # telemetry kind "http": the connector call IS an HTTP call to a
        # terminal; the op names the connector and the op, the fields carry
        # the outcome class (the framework's own counters keep the detail).
        with telemetry.span("http", f"{cid}.{op}",
                           connector=cid) as span:
            try:
                out = await self._call_with_retry(op, data, tenant, task_id)
                span.set(outcome="ok")
                return out
            except AuthInvalidated:
                _bump(cid, "auth_invalidated")
                span.set(outcome="fail", auth_invalidated=True)
                raise
            except PermissionRefused:
                _bump(cid, "refused")
                span.set(outcome="fail", refused=True)
                raise
            except ConnectorError:
                _bump(cid, "failed")
                span.set(outcome="fail")
                raise

    async def _call_with_retry(self, op: str, data: Dict[str, Any],
                               tenant: str, task_id: Optional[str]) -> Any:
        method, path, body = self.build_request(op, data)
        headers: Dict[str, str] = {}
        if task_id:
            # R-188-b: the stable task identity makes retries and duplicate
            # deliveries SAFE — the terminal honors this key (applies once).
            headers["Idempotency-Key"] = f"{task_id}:{op}"
        last: Optional[ConnectorError] = None
        for attempt in range(1, self.policy.max_attempts + 1):
            try:
                return await self._one_attempt(method, path, body, headers,
                                               tenant)
            except TransportInterrupted as e:
                last = e
                # retry ONLY a connection-level interruption, and ONLY with a
                # stable identity (no key = a retry could duplicate the
                # effect — fail-closed, never guess). Timeouts and refusals
                # are never retried (the effect may have happened / the grant
                # is wrong).
                if task_id and attempt < self.policy.max_attempts:
                    _bump(self.connector_id, "retries")
                    if self.policy.backoff_base_s > 0:
                        await asyncio.sleep(self.policy.backoff_base_s)
                    continue
                raise
        raise last if last else ConnectorError()

    async def _one_attempt(self, method: str, path: str, body, headers,
                           tenant: str) -> Any:
        if not tenant:
            # SEC-02: a connector call is tenant-scoped, always.
            raise AuthInvalidated("no ambient tenant (SEC-02)")
        try:
            token = _secret_store().get_secret(tenant, self.credential_name)
        except AuthInvalidated:
            raise
        except Exception:  # noqa: BLE001 — the store is unusable: fail-closed
            raise AuthInvalidated() from None
        headers = dict(headers)
        headers["Authorization"] = f"Bearer {token}"
        headers["Accept"] = "application/json"
        import aiohttp
        timeout = aiohttp.ClientTimeout(total=self.policy.timeout_s)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.request(
                        method, self.base_url + path, json=body,
                        headers=headers) as resp:
                    if resp.status == 401:
                        # the documented invalidation: revoke + fail-fast.
                        try:
                            _secret_store().revoke_secret(
                                tenant, self.credential_name)
                        except Exception:  # noqa: BLE001 — revocation is
                            pass  # best-effort; the 401 already decided it
                        raise AuthInvalidated()
                    if resp.status == 403:
                        raise PermissionRefused()
                    if resp.status >= 400:
                        raise ConnectorError(
                            f"the terminal answered {resp.status}")
                    return await _bounded_json(resp)
        except (AuthInvalidated, PermissionRefused, ConnectorError):
            raise
        except asyncio.TimeoutError:
            raise ConnectorTimeout() from None
        except aiohttp.ClientError:
            # connection-level: no response was received — the only
            # retryable class (with a stable identity).
            raise TransportInterrupted() from None
        except OSError:
            raise TransportInterrupted() from None


async def _bounded_json(resp) -> Any:
    """Read the response body capped (a terminal is untrusted input)."""
    raw = await resp.content.read(1_000_000)
    if not raw:
        return None
    import json
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except (ValueError, UnicodeDecodeError):
        return raw.decode("utf-8", "replace")
