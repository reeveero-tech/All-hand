"""R-204 (D-153): /api/v2 accounts — sign-up, sign-in, sessions, workspaces,
roles and invitations.

Identity: ``Authorization: Bearer <Supabase access token>`` only. The server
API key (``X-API-Key``, SEC-02) is NOT an identity here — it stays for v1
routes and automation; a v2 route ignores it (tests prove a valid admin key
gets 401 on v2).

Sessions:
* sign-in returns the short-lived access token in the body (the web client
  keeps it in memory) and puts the refresh token in an ``HttpOnly; Secure;
  SameSite=Strict`` cookie limited to ``/api/v2/auth`` — script code (XSS)
  cannot read it and another site cannot make the browser send it;
* every authenticated request re-checks that the session still exists in
  ``auth.sessions`` (``agos.session_is_active()``), so sign-out ends access
  immediately — not when the access token expires;
* auth responses carry ``Cache-Control: no-store``. No token is logged.

Isolation: every data query runs inside ``AccountsDB.user_tx`` — as role
``authenticated`` with the verified claims — so Postgres RLS (docs/sql/v2/)
decides visibility. Not-a-member and does-not-exist give the same 404.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Awaitable, Callable, Dict, Optional

from fastapi import APIRouter, Body, HTTPException, Request, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from .auth import Identity, JwksVerifier, TokenError
from .byok import KeyTester, KeyVault, TestLimiter, add_byok_routes
from .db import AccountsDB, SessionInactive
from .gotrue import AuthUnavailable, GoTrueClient

logger = logging.getLogger("agos.v2.accounts")

REFRESH_COOKIE = "agos_rt"
REFRESH_COOKIE_PATH = "/api/v2/auth"
REFRESH_COOKIE_MAX_AGE = 30 * 24 * 3600
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
BEARER = {"WWW-Authenticate": "Bearer"}
EMAIL_RE = re.compile(r"^[^@\s]{1,200}@[^@\s]{1,200}$")
INVITE_TTL_DEFAULT_H = 72

# SQLSTATE raised by docs/sql/v2/002_accounts.sql -> HTTP status
SQLSTATE_HTTP = {"AG401": 401, "AG403": 403, "AG404": 404, "AG409": 409,
                 "AG410": 410, "AG422": 422}


# ------------------------------------------------------------------ models

class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Credentials(_Strict):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=256)


class LoginIn(_Strict):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class PkceExchange(_Strict):
    auth_code: str = Field(min_length=1, max_length=2048)
    code_verifier: str = Field(min_length=43, max_length=128)


class WorkspaceIn(_Strict):
    name: str = Field(min_length=1, max_length=200)


class RoleIn(_Strict):
    role: str = Field(pattern=r"^(owner|admin|member)$")


class InvitationIn(_Strict):
    email: str = Field(min_length=3, max_length=320)
    role: str = Field(default="member", pattern=r"^(admin|member)$")
    ttl_hours: int = Field(default=INVITE_TTL_DEFAULT_H, ge=1, le=720)


class AcceptIn(_Strict):
    token: str = Field(min_length=20, max_length=200)


# ----------------------------------------------------------------- service

@dataclass
class AccountsConfig:
    supabase_url: str
    public_key: str
    db_url: str
    public_url: Optional[str] = None
    # tests only: a standalone Supabase Auth server serves at its root
    auth_url_override: Optional[str] = None

    @property
    def auth_url(self) -> str:
        if self.auth_url_override:
            return self.auth_url_override.rstrip("/")
        return self.supabase_url.rstrip("/") + "/auth/v1"

    @classmethod
    def from_env(cls) -> Optional["AccountsConfig"]:
        url = (os.environ.get("SUPABASE_URL") or "").strip()
        key = (os.environ.get("SUPABASE_ANON_KEY") or "").strip()
        db = (os.environ.get("SUPABASE_DB_URL") or "").strip()
        if not (url and key and db):
            return None
        pub = (os.environ.get("AGOS_PUBLIC_URL") or "").strip() or None
        return cls(url, key, db, pub)


class AccountsService:
    def __init__(self, config: AccountsConfig, *, gotrue: Optional[GoTrueClient] = None,
                 db: Optional[AccountsDB] = None, verifier: Optional[JwksVerifier] = None,
                 vault: Optional[KeyVault] = None, key_tester: Optional[KeyTester] = None):
        self.config = config
        # R-205 (D-154): the BYOK vault (None = AGOS_BYOK_KEK unset -> 503 on key routes)
        self.vault = vault
        self.key_tester = key_tester or KeyTester()
        self.byok_test_limiter = TestLimiter()
        self.gotrue = gotrue or GoTrueClient(config.auth_url, config.public_key)
        self.db = db or AccountsDB(config.db_url)
        self.verifier = verifier or JwksVerifier(self.gotrue.jwks, issuer=config.auth_url)
        self._settings: Optional[Dict[str, Any]] = None
        self._settings_at = 0.0

    async def settings(self) -> Dict[str, Any]:
        if self._settings is None or time.monotonic() - self._settings_at > 60:
            status, data = await self.gotrue.settings()
            if status != 200:
                raise AuthUnavailable(f"settings status {status}")
            self._settings, self._settings_at = data, time.monotonic()
        return self._settings

    async def close(self) -> None:
        await self.db.close()


# ----------------------------------------------------------------- helpers

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _norm_email(email: str) -> str:
    e = email.strip().lower()
    if not EMAIL_RE.match(e):
        raise HTTPException(422, "invalid email address")
    return e


def _uuid(value: str, what: str = "resource") -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError):
        # a malformed id is simply not found (no difference to probe on)
        raise HTTPException(404, f"{what} not found") from None


def _bearer(request: Request) -> Optional[str]:
    h = request.headers.get("authorization") or ""
    if h[:7].lower() == "bearer ":
        return h[7:].strip() or None
    return None


def _db_error(exc: Exception) -> HTTPException:
    """Map a psycopg error to HTTP — message text only for our AGxxx codes."""
    state = getattr(exc, "sqlstate", None) or ""
    if state in SQLSTATE_HTTP:
        diag = getattr(exc, "diag", None)
        msg = (getattr(diag, "message_primary", None) or "request refused") if diag else "request refused"
        status = SQLSTATE_HTTP[state]
        return HTTPException(status, msg, headers=BEARER if status == 401 else None)
    if state.startswith("23") or state.startswith("22"):
        return HTTPException(422, "invalid input")
    if state == "57014":
        return HTTPException(503, "database timeout; retry later", headers={"Retry-After": "2"})
    logger.error("v2 accounts: database error %s (sqlstate %s)", type(exc).__name__, state or "-")
    return HTTPException(503, "database unavailable; retry later", headers={"Retry-After": "5"})


def _session_body(data: Dict[str, Any]) -> Dict[str, Any]:
    user = data.get("user") or {}
    return {
        "access_token": data.get("access_token"),
        "token_type": "bearer",
        "expires_at": data.get("expires_at"),
        "expires_in": data.get("expires_in"),
        "user": {"id": user.get("id"), "email": user.get("email")},
    }


def _set_refresh_cookie(response: Response, refresh_token: Optional[str]) -> None:
    if refresh_token:
        response.set_cookie(REFRESH_COOKIE, refresh_token, max_age=REFRESH_COOKIE_MAX_AGE,
                            path=REFRESH_COOKIE_PATH, secure=True, httponly=True, samesite="strict")


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_COOKIE_PATH, secure=True,
                           httponly=True, samesite="strict")


def _gotrue_code(data: Dict[str, Any]) -> str:
    return str(data.get("error_code") or data.get("error") or "")


# ------------------------------------------------------------------ router

RateLimiter = Callable[[Request], Awaitable[None]]


class _NoStoreRoute(APIRoute):
    """Every /api/v2 answer — success or HTTP error — is ``no-store``: these
    responses carry sessions, invitation links and membership lists."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def no_store_handler(request: Request) -> Response:
            try:
                resp = await handler(request)
            except HTTPException as exc:
                exc.headers = {**NO_STORE, **(exc.headers or {})}
                raise
            for k, v in NO_STORE.items():
                resp.headers.setdefault(k, v)
            return resp
        return no_store_handler


def build_router(get_service: Callable[[], Optional[AccountsService]],
                 rate_limiter: Optional[RateLimiter] = None) -> APIRouter:
    r = APIRouter(prefix="/api/v2", tags=["v2 accounts"], route_class=_NoStoreRoute)

    def svc() -> AccountsService:
        s = get_service()
        if s is None:
            raise HTTPException(503, "accounts are not configured on this server "
                                     "(SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_DB_URL)")
        return s

    async def limit(request: Request) -> None:
        if rate_limiter is not None:
            await rate_limiter(request)

    async def identity(request: Request, s: AccountsService) -> Identity:
        token = _bearer(request)
        try:
            return await s.verifier.verify(token)
        except TokenError as e:
            detail = "session expired" if e.reason == "expired" else (
                "sign-in required" if e.reason == "missing" else "invalid session")
            raise HTTPException(401, detail, headers=BEARER) from None
        except AuthUnavailable:
            raise HTTPException(503, "auth service unavailable; retry later",
                                headers={"Retry-After": "5"}) from None

    async def signed_in(request: Request) -> Identity:
        """Rate limit + token check only — for routes that must answer 401
        before saying anything else about the server (R-205)."""
        s = svc()
        await limit(request)
        return await identity(request, s)

    async def run(request: Request, fn, ident: Optional[Identity] = None):
        """Verify the caller (unless ``signed_in`` already did), then run
        ``fn(conn, ident)`` as that user; the live session is checked here."""
        s = svc()
        if ident is None:
            await limit(request)
            ident = await identity(request, s)
        import psycopg  # noqa: WPS433 - optional dependency, needed only when configured
        try:
            async with s.db.user_tx(ident) as conn:
                return await fn(conn, ident)
        except SessionInactive:
            raise HTTPException(401, "session ended", headers=BEARER) from None
        except psycopg.Error as exc:
            raise _db_error(exc) from None
        except (OSError, TimeoutError) as exc:
            logger.error("v2 accounts: database unreachable (%s)", type(exc).__name__)
            raise HTTPException(503, "database unavailable; retry later",
                                headers={"Retry-After": "5"}) from None

    async def fetchall(conn, sql: str, params=()):
        cur = await conn.execute(sql, params)
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row)) for row in await cur.fetchall()]

    async def fetchone(conn, sql: str, params=()):
        rows = await fetchall(conn, sql, params)
        return rows[0] if rows else None

    def _iso(rows):
        for row in rows:
            for k, v in list(row.items()):
                if hasattr(v, "isoformat"):
                    row[k] = v.isoformat()
                elif isinstance(v, uuid.UUID):
                    row[k] = str(v)
        return rows

    # ----------------------------------------------------------- auth

    @r.get("/auth/config")
    async def auth_config():
        s = get_service()
        if s is None:
            return {"configured": False, "email": False,
                    "providers": {"google": False, "github": False}, "authorize_url": None}
        try:
            st = await s.settings()
        except AuthUnavailable:
            raise HTTPException(503, "auth service unavailable; retry later") from None
        ext = st.get("external") or {}
        return {"configured": True, "email": bool(ext.get("email")),
                "signup_enabled": not st.get("disable_signup", False),
                "email_confirmation_required": not st.get("mailer_autoconfirm", False),
                "providers": {"google": bool(ext.get("google")), "github": bool(ext.get("github"))},
                "authorize_url": s.config.auth_url + "/authorize"}

    @r.post("/auth/signup")
    async def signup(request: Request, response: Response, body: Credentials):
        s = svc()
        await limit(request)
        response.headers.update(NO_STORE)
        email = _norm_email(body.email)
        try:
            status, data = await s.gotrue.signup(email, body.password, s.config.public_url)
        except AuthUnavailable:
            raise HTTPException(503, "auth service unavailable; retry later") from None
        code = _gotrue_code(data)
        if status in (200, 201):
            if data.get("access_token"):
                _set_refresh_cookie(response, data.get("refresh_token"))
                return {"confirmation_required": False, "session": _session_body(data)}
            # email confirmation pending. The same answer for a new or an
            # already-registered address (no account enumeration).
            return {"confirmation_required": True, "session": None}
        if status == 429:
            raise HTTPException(429, "too many attempts; retry later")
        if code == "weak_password" or status == 422:
            raise HTTPException(422, str(data.get("msg") or "password rejected"))
        if code in ("signup_disabled", "email_provider_disabled"):
            raise HTTPException(403, "sign-up is disabled")
        if code in ("email_address_invalid", "validation_failed") or status == 400:
            raise HTTPException(422, "invalid email or password")
        raise HTTPException(502, "auth service error")

    @r.post("/auth/login")
    async def login(request: Request, response: Response, body: LoginIn):
        s = svc()
        await limit(request)
        response.headers.update(NO_STORE)
        try:
            status, data = await s.gotrue.password_login(_norm_email(body.email), body.password)
        except AuthUnavailable:
            raise HTTPException(503, "auth service unavailable; retry later") from None
        if status == 200 and data.get("access_token"):
            _set_refresh_cookie(response, data.get("refresh_token"))
            return _session_body(data)
        code = _gotrue_code(data)
        if status == 429:
            raise HTTPException(429, "too many attempts; retry later")
        if code == "email_not_confirmed":
            raise HTTPException(403, "email not confirmed — open the link sent to your email")
        if status in (400, 401, 422):
            raise HTTPException(401, "invalid email or password", headers=BEARER)
        raise HTTPException(502, "auth service error")

    @r.post("/auth/refresh")
    async def refresh(request: Request, response: Response):
        s = svc()
        await limit(request)
        response.headers.update(NO_STORE)
        rt = request.cookies.get(REFRESH_COOKIE)
        if not rt:
            raise HTTPException(401, "sign-in required", headers=BEARER)
        try:
            status, data = await s.gotrue.refresh(rt)
        except AuthUnavailable:
            raise HTTPException(503, "auth service unavailable; retry later") from None
        if status == 200 and data.get("access_token"):
            _set_refresh_cookie(response, data.get("refresh_token"))
            return _session_body(data)
        if status == 429:
            raise HTTPException(429, "too many attempts; retry later")
        # revoked / reused / expired refresh token: the cookie is dropped
        resp = Response(status_code=401, content=b'{"detail":"session ended"}',
                        media_type="application/json", headers={**NO_STORE, **BEARER})
        _clear_refresh_cookie(resp)
        return resp

    @r.post("/auth/oauth/exchange")
    async def oauth_exchange(request: Request, response: Response, body: PkceExchange):
        s = svc()
        await limit(request)
        response.headers.update(NO_STORE)
        try:
            status, data = await s.gotrue.pkce_exchange(body.auth_code, body.code_verifier)
        except AuthUnavailable:
            raise HTTPException(503, "auth service unavailable; retry later") from None
        if status == 200 and data.get("access_token"):
            _set_refresh_cookie(response, data.get("refresh_token"))
            return _session_body(data)
        if status == 429:
            raise HTTPException(429, "too many attempts; retry later")
        raise HTTPException(401, "sign-in with the provider failed", headers=BEARER)

    @r.post("/auth/logout", status_code=204)
    async def logout(request: Request):
        s = svc()
        await limit(request)
        token = _bearer(request)
        resp = Response(status_code=204, headers=NO_STORE)
        _clear_refresh_cookie(resp)
        if not token:
            return resp
        try:
            await s.verifier.verify(token)
        except TokenError:
            return resp  # nothing to end; the cookie is cleared anyway
        except AuthUnavailable:
            raise HTTPException(503, "auth service unavailable; retry later") from None
        try:
            status, _ = await s.gotrue.logout(token)
        except AuthUnavailable:
            raise HTTPException(503, "auth service unavailable; sign-out not confirmed") from None
        if status not in (200, 204, 401, 403, 404):
            raise HTTPException(502, "sign-out not confirmed by the auth service")
        return resp

    # ------------------------------------------------------------- me

    @r.get("/me")
    async def me(request: Request):
        async def q(conn, ident: Identity):
            rows = await fetchall(conn,
                "select w.id, w.name, m.role, w.created_at from agos.workspaces w "
                "join agos.workspace_members m on m.workspace_id = w.id "
                "where m.user_id = auth.uid() order by w.created_at, w.id")
            return {"user": {"id": ident.user_id, "email": ident.email},
                    "workspaces": _iso(rows)}
        return await run(request, q)

    # ------------------------------------------------------ workspaces

    @r.get("/workspaces")
    async def list_workspaces(request: Request):
        async def q(conn, ident):
            return _iso(await fetchall(conn,
                "select w.id, w.name, m.role, w.created_at from agos.workspaces w "
                "join agos.workspace_members m on m.workspace_id = w.id "
                "where m.user_id = auth.uid() order by w.created_at, w.id"))
        return await run(request, q)

    @r.post("/workspaces", status_code=201)
    async def create_workspace(request: Request, body: WorkspaceIn):
        async def q(conn, ident):
            row = await fetchone(conn, "select agos.create_workspace(%s) as id", (body.name.strip(),))
            ws = await fetchone(conn, "select w.id, w.name, 'owner' as role, w.created_at "
                                      "from agos.workspaces w where w.id = %s", (row["id"],))
            return _iso([ws])[0]
        return await run(request, q)

    @r.get("/workspaces/{workspace_id}")
    async def get_workspace(request: Request, workspace_id: str):
        ws = _uuid(workspace_id, "workspace")

        async def q(conn, ident):
            row = await fetchone(conn,
                "select w.id, w.name, agos.member_role(w.id) as role, w.created_at, w.updated_at "
                "from agos.workspaces w where w.id = %s", (ws,))
            if row is None:
                raise HTTPException(404, "workspace not found")
            return _iso([row])[0]
        return await run(request, q)

    @r.patch("/workspaces/{workspace_id}")
    async def rename_workspace(request: Request, workspace_id: str, body: WorkspaceIn):
        ws = _uuid(workspace_id, "workspace")

        async def q(conn, ident):
            await conn.execute("select agos.rename_workspace(%s, %s)", (ws, body.name.strip()))
            return _iso([await fetchone(conn, "select w.id, w.name, agos.member_role(w.id) as role "
                                              "from agos.workspaces w where w.id = %s", (ws,))])[0]
        return await run(request, q)

    @r.delete("/workspaces/{workspace_id}", status_code=204)
    async def delete_workspace(request: Request, workspace_id: str):
        ws = _uuid(workspace_id, "workspace")

        async def q(conn, ident):
            await conn.execute("select agos.delete_workspace(%s)", (ws,))
            return Response(status_code=204)
        return await run(request, q)

    # --------------------------------------------------------- members

    @r.get("/workspaces/{workspace_id}/members")
    async def list_members(request: Request, workspace_id: str):
        ws = _uuid(workspace_id, "workspace")

        async def q(conn, ident):
            return _iso(await fetchall(conn, "select * from agos.list_members(%s)", (ws,)))
        return await run(request, q)

    @r.patch("/workspaces/{workspace_id}/members/{user_id}")
    async def set_role(request: Request, workspace_id: str, user_id: str, body: RoleIn):
        ws, uid = _uuid(workspace_id, "workspace"), _uuid(user_id, "member")

        async def q(conn, ident):
            await conn.execute("select agos.set_member_role(%s, %s, %s)", (ws, uid, body.role))
            return {"workspace_id": ws, "user_id": uid, "role": body.role}
        return await run(request, q)

    @r.delete("/workspaces/{workspace_id}/members/{user_id}", status_code=204)
    async def remove_member(request: Request, workspace_id: str, user_id: str):
        ws, uid = _uuid(workspace_id, "workspace"), _uuid(user_id, "member")

        async def q(conn, ident):
            await conn.execute("select agos.remove_member(%s, %s)", (ws, uid))
            return Response(status_code=204)
        return await run(request, q)

    # ----------------------------------------------------- invitations

    @r.get("/workspaces/{workspace_id}/invitations")
    async def list_invitations(request: Request, workspace_id: str):
        ws = _uuid(workspace_id, "workspace")

        async def q(conn, ident):
            role = (await fetchone(conn, "select agos.member_role(%s) as r", (ws,)))["r"]
            if role is None:
                raise HTTPException(404, "workspace not found")
            if role == "member":
                raise HTTPException(403, "only an owner or admin can see invitations")
            return _iso(await fetchall(conn,
                "select id, email, role, invited_by, created_at, expires_at, accepted_at, "
                "accepted_by, revoked_at, case when revoked_at is not null then 'revoked' "
                "when accepted_at is not null then 'accepted' when expires_at <= now() then 'expired' "
                "else 'pending' end as state from agos.workspace_invitations "
                "where workspace_id = %s order by created_at desc", (ws,)))
        return await run(request, q)

    @r.post("/workspaces/{workspace_id}/invitations", status_code=201)
    async def create_invitation(request: Request, workspace_id: str, body: InvitationIn,
                                response: Response):
        ws = _uuid(workspace_id, "workspace")
        email = _norm_email(body.email)
        token = secrets.token_urlsafe(32)
        response.headers.update(NO_STORE)

        async def q(conn, ident):
            row = await fetchone(conn,
                "select * from agos.create_invitation(%s, %s, %s, %s, %s)",
                (ws, email, body.role, _token_hash(token), timedelta(hours=body.ttl_hours)))
            base = (svc().config.public_url or str(request.base_url)).rstrip("/")
            out = _iso([row])[0]
            # the token is shown ONCE (only its hash is stored); in the URL
            # fragment, so it never reaches a server log on the way back
            return {**out, "email": email, "role": body.role, "token": token,
                    "accept_url": f"{base}/#invite={token}"}
        return await run(request, q)

    @r.delete("/workspaces/{workspace_id}/invitations/{invitation_id}", status_code=204)
    async def revoke_invitation(request: Request, workspace_id: str, invitation_id: str):
        ws, inv = _uuid(workspace_id, "workspace"), _uuid(invitation_id, "invitation")

        async def q(conn, ident):
            row = await fetchone(conn, "select workspace_id from agos.workspace_invitations "
                                       "where id = %s", (inv,))
            if row is None or str(row["workspace_id"]) != ws:
                raise HTTPException(404, "invitation not found")
            await conn.execute("select agos.revoke_invitation(%s)", (inv,))
            return Response(status_code=204)
        return await run(request, q)

    @r.post("/invitations/accept")
    async def accept_invitation(request: Request, body: AcceptIn = Body(...)):
        async def q(conn, ident):
            row = await fetchone(conn, "select * from agos.accept_invitation(%s)",
                                 (_token_hash(body.token.strip()),))
            return _iso([row])[0]
        return await run(request, q)

    # R-205 (D-154): the user's AI provider keys — same identity/RLS/no-store path
    add_byok_routes(r, svc=svc, run=run, signed_in=signed_in, fetchall=fetchall,
                    fetchone=fetchone, uuid_of=_uuid)
    return r
