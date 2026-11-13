"""R-204 (D-153): the Supabase Auth (GoTrue) HTTP calls the web server makes.

Only the project's PUBLIC key (``SUPABASE_ANON_KEY`` — the anon/publishable
key) is used here, exactly like a browser would; the service key is never
sent to the auth endpoints and users are never created with admin rights.
Responses are returned as ``(status, json)``; nothing is logged.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import aiohttp

TIMEOUT_S = 10


class AuthUnavailable(Exception):
    """The auth server could not be reached / answered garbage."""


class GoTrueClient:
    def __init__(self, auth_url: str, public_key: str):
        self.auth_url = auth_url.rstrip("/")
        self._key = public_key

    def _headers(self, bearer: Optional[str] = None) -> Dict[str, str]:
        h = {"apikey": self._key, "Content-Type": "application/json"}
        h["Authorization"] = f"Bearer {bearer or self._key}"
        return h

    async def _call(self, method: str, path: str, body: Optional[dict] = None,
                    bearer: Optional[str] = None) -> Tuple[int, Dict[str, Any]]:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT_S)) as s:
                async with s.request(method, f"{self.auth_url}{path}", json=body,
                                     headers=self._headers(bearer)) as r:
                    try:
                        data = await r.json(content_type=None)
                    except (ValueError, aiohttp.ContentTypeError):
                        data = {}
                    return r.status, (data if isinstance(data, dict) else {})
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise AuthUnavailable(type(exc).__name__) from None

    async def settings(self):
        return await self._call("GET", "/settings")

    async def jwks(self) -> Dict[str, Any]:
        status, data = await self._call("GET", "/.well-known/jwks.json")
        if status != 200:
            raise AuthUnavailable(f"jwks status {status}")
        return data

    async def signup(self, email: str, password: str, redirect_to: Optional[str]):
        path = "/signup"
        if redirect_to:
            from urllib.parse import quote
            path += f"?redirect_to={quote(redirect_to, safe='')}"
        return await self._call("POST", path, {"email": email, "password": password})

    async def password_login(self, email: str, password: str):
        return await self._call("POST", "/token?grant_type=password",
                                {"email": email, "password": password})

    async def refresh(self, refresh_token: str):
        return await self._call("POST", "/token?grant_type=refresh_token",
                                {"refresh_token": refresh_token})

    async def pkce_exchange(self, auth_code: str, code_verifier: str):
        return await self._call("POST", "/token?grant_type=pkce",
                                {"auth_code": auth_code, "code_verifier": code_verifier})

    async def logout(self, access_token: str):
        return await self._call("POST", "/logout?scope=local", None, bearer=access_token)
