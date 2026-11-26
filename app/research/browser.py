"""Browser Worker interface (design + client; no browser runs inside this service).

Some pages only render with JavaScript. Playwright and Chromium are NOT installed in the Render service (memory,
image size, attack surface). Instead, a separate worker — not hosted yet; hosting it needs the owner's approval —
would receive signed requests and return what it rendered. This module is the contract and the client, plus a
mock worker for tests. With BROWSER_WORKER_URL / BROWSER_WORKER_SECRET unset, it is disabled and says so.

Rules the contract enforces, whatever the worker does:
- Only public http(s) URLs; the worker must apply the same SSRF rules (no private, loopback or metadata
  addresses) — the client checks the URL before sending it.
- No CAPTCHA solving, no MFA bypass, no paywall or login circumvention. A page behind a login is opened only in
  an authorized session the owner created by logging in themselves (human approval), and the request says so.
- Sessions are isolated per project (`session_scope` = project id); one project can never use another's cookies.
- No passwords, tokens or cookies are ever sent by this client or written to logs; `redact()` removes anything
  that looks like a secret from what the worker returns before it is stored.
- Requests are signed (HMAC-SHA256 of the body with the shared secret, plus a timestamp) so the worker rejects
  forged or replayed requests.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field

ACTIONS = ("render", "screenshot_text")  # read-only; no form filling, no clicking through consent or login walls
MAX_WAIT_S = 30
_SECRET_LIKE = re.compile(r"(?i)(password|passwd|pwd|token|secret|api[_-]?key|authorization|cookie|set-cookie|session[_-]?id)"
                          r"(\s*[:=]\s*|\"\s*:\s*\")([^\s\"&;,]{4,})")


@dataclass
class BrowserRequest:
    url: str
    action: str = "render"
    wait_s: float = 10.0
    session_scope: str | None = None  # project id; None = a fresh anonymous context, discarded after the request
    use_authorized_session: bool = False  # only true when the owner logged in themselves (human approval recorded)
    approval_id: str | None = None  # the owner's approval of that session, required when use_authorized_session
    run_id: str | None = None

    def validate(self) -> str | None:
        from urllib.parse import urlparse
        p = urlparse(self.url or "")
        if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
            return "invalid_url"
        if self.action not in ACTIONS:
            return "action_not_allowed"
        if not 0 < float(self.wait_s) <= MAX_WAIT_S:
            return "bad_wait"
        if self.use_authorized_session and not (self.session_scope and self.approval_id):
            return "needs_human_approval"  # a login-only page is never opened without the owner's own session
        return None


@dataclass
class BrowserResult:
    ok: bool
    url: str
    status: int | None = None
    html: str = ""
    text: str = ""
    failure: str | None = None  # blocked, login_required, captcha, mfa_required, timeout, worker_unavailable, ...
    needs_human: str | None = None  # "login" | "mfa" | "captcha": shown to the owner; never solved automatically
    notes: list = field(default_factory=list)


def redact(s: str) -> str:
    return _SECRET_LIKE.sub(lambda m: m.group(1) + m.group(2) + "[REDACTED]", s or "")


def sign(secret: str, body: bytes, ts: int) -> str:
    return hmac.new(secret.encode(), str(ts).encode() + b"." + body, hashlib.sha256).hexdigest()


def verify_signature(secret: str, body: bytes, ts: int, sig: str, now: float | None = None, skew: int = 120) -> bool:
    """What the worker does on every request: reject old (replayed) or forged ones."""
    if abs((now or time.time()) - ts) > skew:
        return False
    return hmac.compare_digest(sign(secret, body, ts), sig or "")


def configured() -> bool:
    return bool(os.environ.get("BROWSER_WORKER_URL") and os.environ.get("BROWSER_WORKER_SECRET"))


def status() -> dict:
    return {"enabled": configured(), "note": "" if configured() else "غير مفعّل: لا يوجد عامل متصفح مستضاف (يحتاج موافقة المالك)"}


class BrowserClient:
    def __init__(self, url: str | None = None, secret: str | None = None, transport=None):
        self.url = url or os.environ.get("BROWSER_WORKER_URL", "")
        self.secret = secret or os.environ.get("BROWSER_WORKER_SECRET", "")
        self.transport = transport  # httpx transport (tests use a mock worker)

    async def render(self, req: BrowserRequest) -> BrowserResult:
        if not (self.url and self.secret):
            return BrowserResult(False, req.url, failure="worker_unavailable")
        bad = req.validate()
        if bad:
            return BrowserResult(False, req.url, failure=bad, needs_human="login" if bad == "needs_human_approval" else None)
        from .. import tools  # the same public-address rule as every other request
        from urllib.parse import urlparse
        import asyncio
        if not await asyncio.to_thread(tools._resolve_public, urlparse(req.url).hostname):
            return BrowserResult(False, req.url, failure="unsafe_address")
        import httpx
        body = json.dumps(asdict(req), ensure_ascii=False).encode()
        ts = int(time.time())
        headers = {"content-type": "application/json", "x-agos-ts": str(ts), "x-agos-signature": sign(self.secret, body, ts)}
        try:
            async with httpx.AsyncClient(timeout=MAX_WAIT_S + 15, transport=self.transport) as c:
                r = await c.post(self.url.rstrip("/") + "/render", content=body, headers=headers)
        except Exception as e:  # noqa: BLE001
            return BrowserResult(False, req.url, failure="worker_unavailable", notes=[type(e).__name__])
        if r.status_code != 200:
            return BrowserResult(False, req.url, failure=f"worker_http_{r.status_code}")
        d = r.json()
        res = BrowserResult(bool(d.get("ok")), d.get("url") or req.url, d.get("status"), redact(d.get("html", ""))[:3_000_000],
                            redact(d.get("text", ""))[:500_000], d.get("failure"), d.get("needs_human"))
        if res.needs_human in ("login", "mfa", "captcha"):
            res.ok, res.failure = False, res.failure or f"{res.needs_human}_required"  # handed to the owner, not solved
        return res


def mock_worker(pages: dict[str, dict], secret: str):
    """An httpx MockTransport that behaves like a compliant worker: checks the signature, refuses unknown
    sessions, reports login/CAPTCHA pages as needing a human instead of solving them."""
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        ts = int(request.headers.get("x-agos-ts", "0"))
        if not verify_signature(secret, request.content, ts, request.headers.get("x-agos-signature", "")):
            return httpx.Response(401, json={"ok": False, "failure": "bad_signature"})
        req = json.loads(request.content)
        page = pages.get(req["url"])
        if not page:
            return httpx.Response(200, json={"ok": False, "url": req["url"], "failure": "not_found", "status": 404})
        if page.get("gate") and not req.get("use_authorized_session"):
            return httpx.Response(200, json={"ok": False, "url": req["url"], "needs_human": page["gate"], "status": 200})
        if page.get("scope") and page["scope"] != req.get("session_scope"):
            return httpx.Response(200, json={"ok": False, "url": req["url"], "failure": "session_scope_mismatch"})
        return httpx.Response(200, json={"ok": True, "url": req["url"], "status": 200, "html": page.get("html", ""),
                                         "text": page.get("text", "")})
    return httpx.MockTransport(handler)
