"""R-204 (D-151 §3, D-153): direct Postgres access for v2 (psycopg 3 + pool).

Every user request runs in ONE transaction that is the user, not the server:

    begin;
      select set_config('request.jwt.claims', <verified claims>, true);  -- local
      set local role authenticated;                                     -- RLS applies
      set local statement_timeout = ...;
      select agos.session_is_active();   -- signed-out / expired / banned -> refused
      ... the route's queries ...
    commit;

The connection itself belongs to the server (``SUPABASE_DB_URL``), but RLS is
evaluated for ``authenticated`` with the user's ``auth.uid()``: a route that
forgets a ``where workspace_id = ...`` still cannot see another workspace.
``set local`` / ``set_config(..., true)`` end with the transaction, so nothing
leaks to the next request that reuses the pooled connection.

Pooler-safe: ``prepare_threshold=None`` (no server-side prepared statements),
so the Supabase pooler (Supavisor, session or transaction mode) works.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from .auth import Identity

STATEMENT_TIMEOUT_MS = 5000


class SessionInactive(Exception):
    """The token is validly signed but its session no longer exists (logout),
    is past ``not_after``, or its user is deleted/banned."""


class AccountsDB:
    def __init__(self, dsn: str, min_size: int = 1, max_size: int = 4):
        from psycopg_pool import AsyncConnectionPool  # imported lazily: optional at import time

        self._pool = AsyncConnectionPool(
            dsn, min_size=min_size, max_size=max_size, open=False, timeout=10,
            kwargs={"prepare_threshold": None, "connect_timeout": 10,
                    "application_name": "agos-web-v2"},
        )
        self._opened = False

    async def open(self) -> None:
        if not self._opened:
            await self._pool.open(wait=True, timeout=15)
            self._opened = True

    async def close(self) -> None:
        if self._opened:
            await self._pool.close()
            self._opened = False

    @asynccontextmanager
    async def user_tx(self, ident: Identity) -> AsyncIterator["object"]:
        """A transaction running as the verified user (role authenticated)."""
        await self.open()
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await conn.execute("select set_config('request.jwt.claims', %s, true)",
                                   (json.dumps(ident.claims),))
                await conn.execute("set local role authenticated")
                await conn.execute(f"set local statement_timeout = {int(STATEMENT_TIMEOUT_MS)}")
                cur = await conn.execute("select agos.session_is_active()")
                row = await cur.fetchone()
                if not row or row[0] is not True:
                    raise SessionInactive()
                yield conn

    async def ping(self) -> Optional[str]:
        """Readiness: None when the database answers, else the exception type."""
        try:
            await self.open()
            async with self._pool.connection() as conn:
                await conn.execute("select 1")
            return None
        except Exception as exc:  # noqa: BLE001 - reported by type only
            return type(exc).__name__
