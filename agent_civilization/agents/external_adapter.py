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
"""

import json
import logging
import os
import time
from typing import Any, Dict, List
from urllib.parse import urljoin, urlsplit

import aiohttp

from agent_civilization.core import safety

logger = logging.getLogger("external_agents")


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
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as s:
                status, body = await self._post_checked(s, headers, payload)
            if status != 200:
                raise RuntimeError(f"HTTP {status}: {body[:300]}")
            data = json.loads(body)
            content = data["choices"][0]["message"]["content"]
            self.stats["tasks_completed"] += 1
            ok, result = True, {"answer": content, "external_agent": self.name}
        except Exception as e:
            logger.warning("external agent %s failed: %s", self.name, e)
            self.stats["tasks_failed"] += 1
            ok, result = False, {"error": str(e)}
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
        target BEFORE it is contacted.  Method semantics match aiohttp's own
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
        raise RuntimeError(f"too many redirects (> {self.MAX_REDIRECTS})")

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
        logger.error("Invalid EXTERNAL_AGENTS JSON: %s", e)
        return []
