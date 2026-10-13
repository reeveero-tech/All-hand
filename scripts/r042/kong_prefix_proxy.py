#!/usr/bin/env python3
"""R-042 test harness: minimal stand-in for Supabase's API gateway path prefix.

Supabase exposes PostgREST under ``/rest/v1/`` (behind Kong). A bare PostgREST
serves at ``/``. This proxy strips ``/rest/v1`` and forwards method, query,
headers and body unchanged, so ``supabase_store.py`` can be exercised against a
real PostgREST + PostgreSQL without modification.

It is a TEST HARNESS ONLY (never used in production). It does not emulate Kong's
``apikey`` check; authorization is enforced by PostgREST itself via the
``Authorization: Bearer <JWT>`` header, exactly as on Supabase.

Usage: kong_prefix_proxy.py <listen_port> <upstream_base_url>
"""

import sys

import aiohttp
from aiohttp import web

PREFIX = "/rest/v1"
HOP = {"host", "content-length", "transfer-encoding", "connection"}


def make_app(upstream: str) -> web.Application:
    async def handler(request: web.Request) -> web.StreamResponse:
        path = request.rel_url.path
        if not path.startswith(PREFIX):
            return web.Response(status=404, text="only /rest/v1 is proxied")
        target = upstream.rstrip("/") + (path[len(PREFIX):] or "/")
        if request.rel_url.query_string:
            target += "?" + request.rel_url.query_string
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP}
        body = await request.read()
        async with aiohttp.ClientSession() as s:
            async with s.request(request.method, target, headers=headers, data=body) as r:
                payload = await r.read()
                out = {k: v for k, v in r.headers.items() if k.lower() not in HOP | {"content-encoding"}}
                return web.Response(status=r.status, body=payload, headers=out)

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    return app


if __name__ == "__main__":
    port, upstream = int(sys.argv[1]), sys.argv[2]
    web.run_app(make_app(upstream), host="127.0.0.1", port=port, print=None)
