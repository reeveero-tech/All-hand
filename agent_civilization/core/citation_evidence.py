"""R-170 (batch 10): citation evidence beyond format — three levels, kept apart.

Level 1 — FORMAT (``output_validators.validate_citations``, unchanged): every
item is an http(s) URL with a host. Says nothing about the web.

Level 2 — EXISTENCE (this module, opt-in ``{"citations": {"exists": True}}``):
each cited URL is fetched through the SSRF policy (``safety.safe_urlopen``).
HTTP 404/410 ⇒ **missing** (the result is rejected). Anything that does not
prove absence — timeout, DNS failure, 403/429/5xx, policy refusal — ⇒
**unverifiable**: recorded, never counted as a pass and never as a rejection
(a flaky network must not fail an honest answer, and must not "prove" a
source either). Existence of a page is NOT support for a claim.

Level 3 — QUOTE PRESENCE (opt-in ``{"citations": {"quote": True}}``): when a
citation carries the text it quotes (``quote``/``snippet``/``text``), that text
must appear in the fetched page after HTML stripping and whitespace/case
normalisation (an ellipsis splits it into fragments; each fragment of ≥ 20
characters must appear). This proves the words are AT the source — it does
NOT prove the source SUPPORTS the claim, and it is not semantic entailment.
Pages rendered by JavaScript, paywalls and paraphrased snippets fail this
check honestly as "quote not found" or "unverifiable"; that is why it is
opt-in and not a default.

What no level here guarantees: that the source is reliable, current, or that
it entails the answer's claim. That needs a human or a separately evaluated
entailment model and is documented as NOT provided.
"""
from __future__ import annotations

import html
import re
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

MAX_URLS = 5
TIMEOUT_S = 6.0
MAX_BYTES = 512 * 1024
MIN_FRAGMENT = 20
QUOTE_KEYS = ("quote", "snippet", "text")
URL_KEYS = ("url", "href", "link")

#: fetcher(url, timeout) -> (http_status, body_text). Raises on transport errors.
Fetcher = Callable[[str, float], Tuple[int, str]]


def default_fetcher(url: str, timeout: float) -> Tuple[int, str]:
    from agent_civilization.core import safety
    req = urllib.request.Request(url, headers={"User-Agent": "AGOS-Evidence/1.0",
                                               "Accept": "text/html,application/json,text/*"})
    try:
        with safety.safe_urlopen(req, timeout=timeout) as resp:
            return int(resp.status), resp.read(MAX_BYTES).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return int(e.code), ""


def _norm(text: str) -> str:
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", text)
    text = html.unescape(re.sub(r"(?s)<[^>]+>", " ", text))
    return re.sub(r"\s+", " ", text).strip().casefold()


def quote_present(page: str, quote: str) -> bool:
    """Every ≥ MIN_FRAGMENT-char fragment of ``quote`` (split at ellipses) is in ``page``."""
    frags = [f.strip() for f in re.split(r"\.\.\.|…", _norm(quote))]
    frags = [f for f in frags if len(f) >= MIN_FRAGMENT]
    if not frags:
        return False          # too short to be evidence of anything
    body = _norm(page)
    return all(f in body for f in frags)


def citation_items(result: Mapping[str, Any]) -> List[Tuple[str, Optional[str]]]:
    """(url, quoted_text) pairs from the declared citation block (same block
    the format check judges)."""
    from agent_civilization.core.output_validators import _citation_blocks
    out: List[Tuple[str, Optional[str]]] = []
    for _, block in _citation_blocks(result):
        items = list(block) if isinstance(block, (list, tuple)) else [block]
        for it in items:
            if isinstance(it, str):
                out.append((it, None))
            elif isinstance(it, Mapping):
                url = next((it[k] for k in URL_KEYS if isinstance(it.get(k), str)), None)
                quote = next((it[k] for k in QUOTE_KEYS if isinstance(it.get(k), str) and it[k].strip()), None)
                if url:
                    out.append((url, quote))
    return out


def check_sources(result: Mapping[str, Any], *, exists: bool = True, quote: bool = False,
                  fetcher: Optional[Fetcher] = None, max_urls: int = MAX_URLS,
                  timeout: float = TIMEOUT_S) -> Dict[str, Any]:
    """Level 2/3 verdict: {"reasons": [...], "sources": [{url, status, ...}]}.

    status ∈ {"exists", "missing", "unverifiable", "not_checked"}; with
    ``quote`` also quote ∈ {"present", "absent", "none_given", "unverifiable"}.
    Only "missing" and (quote) "absent" produce reasons.
    """
    fetch = fetcher or default_fetcher
    items = citation_items(result)
    rows: List[Dict[str, Any]] = []
    reasons: List[str] = []
    for i, (url, q) in enumerate(items):
        row: Dict[str, Any] = {"url": url}
        if i >= max_urls:
            row["status"] = "not_checked"
            rows.append(row)
            continue
        try:
            code, body = fetch(url, timeout)
        except Exception as e:  # noqa: BLE001 — any transport failure is "unverifiable"
            row.update(status="unverifiable", reason=type(e).__name__)
            if quote:
                row["quote"] = "unverifiable"
            rows.append(row)
            continue
        row["http"] = code
        if code in (404, 410):
            row["status"] = "missing"
            reasons.append(f"citations.exists: source #{i} returned HTTP {code} ({url})")
        elif 200 <= code < 400:
            row["status"] = "exists"
        else:
            row["status"] = "unverifiable"
        if quote:
            if q is None:
                row["quote"] = "none_given"
            elif row["status"] != "exists":
                row["quote"] = "unverifiable"
            elif quote_present(body, q):
                row["quote"] = "present"
            else:
                row["quote"] = "absent"
                reasons.append(f"citations.quote: text quoted for source #{i} not found at {url}")
        rows.append(row)
    if not exists and not quote:
        return {"reasons": [], "sources": []}
    return {"reasons": reasons, "sources": rows}
