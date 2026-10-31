"""Tenant-visible failure text without credentials (G-160-1, R-170 batch 20, D-105).

R-149 reduces an *unexpected exception* to its type (``runtime_contract.
public_error``). But handlers and the LLM client catch exceptions themselves
and embed their text in ``result["error"]`` — an HTTP error body, a URL with
credentials, a transport message. That text is useful and stays; only the
credentials inside it are replaced by ``<redacted>``:

1. values of secret-named environment variables (``*KEY*``, ``*TOKEN*``,
   ``*SECRET*``, ``*PASSWORD*``/``*PASSWD*``, ``*CREDENTIAL*``; length >= 8)
   and the username/password of any URL-valued environment variable;
2. URL userinfo (``scheme://user:pw@host``);
3. secret query parameters (``api_key=``, ``key=``, ``token=``, ``secret=``,
   ``password=``, ``signature=`` …) and ``key: value`` / ``key=value`` pairs
   with such names whose value looks like a credential (>= 12 chars, has a digit);
4. ``Bearer``/``Basic`` credentials;
5. known key formats (``sk-…``, GitHub ``ghp_``/``github_pat_``…, AWS
   ``AKIA…``, Google ``AIza…``, Slack ``xox?-…``) and JWTs.

6. (batch 24, D-110) local filesystem prefixes — the repository root,
   ``sys.prefix``/``sys.base_prefix``, the temp dir and the home dir — become
   ``<path>`` (the relative tail is kept), but only where the prefix starts a
   path token (not inside a URL path);
7. (batch 24, D-110) the hosts of configured internal services
   (``SUPABASE_URL``, ``DATABASE_URL``, ``REDIS_URL``) become ``<internal-host>``
   (whole-label match; the port and the surrounding text are kept).

Limits (declared): secrets that are neither in the environment nor in a known
shape (e.g. a per-tenant secret-store value echoed by a remote service in an
unknown format) can still pass; hosts of services NOT listed above and paths
outside the listed prefixes are kept; a user-supplied URL whose host equals a
configured internal host is redacted too; successful results are not rewritten.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import threading
import urllib.parse
from typing import Any, List, Optional, Tuple

REDACTED = "<redacted>"
_SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)", re.I)
_MIN_LEN = 8

_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/@\s?#]+@")
_PARAM_NAMES = (r"api[_-]?key|apikey|key|access[_-]?token|refresh[_-]?token|id[_-]?token|token|"
                r"client[_-]?secret|secret|password|passwd|pwd|sig|signature|x-amz-signature|"
                r"x-amz-security-token|auth|authorization")
_QUERY = re.compile(r"(?i)([?&;](?:" + _PARAM_NAMES + r")=)[^&\s#'\"<>]+")
_PAIR = re.compile(r"(?i)(\b(?:x-api-key|api[_-]?key|apikey|access[_-]?token|client[_-]?secret|"
                   r"secret|password|passwd|token|key)[\"']?\s*[:=]\s*[\"']?)"
                   r"(?!<redacted>)(?=[A-Za-z0-9._~+/=-]*\d)[A-Za-z0-9._~+/=-]{12,}")
_AUTH = re.compile(r"(?i)\b((?:bearer|basic)\s+)[A-Za-z0-9._~+/=-]{8,}")
_FORMATS = re.compile(
    r"\bsk-[A-Za-z0-9_-]{16,}"
    r"|\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}"
    r"|\bAKIA[0-9A-Z]{16}\b|\bAIza[0-9A-Za-z_-]{30,}"
    r"|\bxox[abprs]-[A-Za-z0-9-]{10,}"
    r"|\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")

PATH = "<path>"
INTERNAL_HOST = "<internal-host>"
INTERNAL_SERVICE_ENV = ("SUPABASE_URL", "DATABASE_URL", "REDIS_URL")
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_lock = threading.Lock()
_cache: Optional[Tuple[Tuple[Tuple[str, str], ...], List[str]]] = None
_loc_cache: Optional[Tuple[Tuple[Tuple[str, str], ...], Optional["re.Pattern[str]"],
                           Optional["re.Pattern[str]"]]] = None


def _local_prefixes() -> List[str]:
    cands = {_REPO_ROOT, sys.prefix, getattr(sys, "base_prefix", sys.prefix),
             tempfile.gettempdir(), os.path.expanduser("~")}
    out = set()
    for c in cands:
        if not c:
            continue
        c = c.rstrip("/\\")
        if len(c) >= 4 and c not in ("/", ""):
            out.add(c)
            real = os.path.realpath(c)
            if len(real) >= 4:
                out.add(real.rstrip("/\\"))
    return sorted(out, key=len, reverse=True)


def _internal_hosts(snap) -> List[str]:
    env = dict(snap)
    hosts = set()
    for name in INTERNAL_SERVICE_ENV:
        v = env.get(name) or ""
        if "://" not in v:
            continue
        try:
            h = urllib.parse.urlsplit(v).hostname
        except ValueError:
            h = None
        if h and len(h) >= 4:
            hosts.add(h)
    return sorted(hosts, key=len, reverse=True)


def _location_patterns():
    """(path_re, host_re) for the current environment (cached per snapshot)."""
    global _loc_cache
    snap = tuple(sorted(os.environ.items()))
    with _lock:
        if _loc_cache is not None and _loc_cache[0] == snap:
            return _loc_cache[1], _loc_cache[2]
    prefixes = _local_prefixes()
    # a prefix counts only at the start of a path token: not preceded by a
    # word char, '/', '.', ':' or '-' (so ``https://x.com/tmp/a`` is kept) and
    # followed by a separator, a quote/space or the end.
    path_re = (re.compile(r"(?<![\w/.:~-])(?:" + "|".join(re.escape(p) for p in prefixes)
                          + r")(?=[/\\\s'\",:;)\]]|$)") if prefixes else None)
    hosts = _internal_hosts(snap)
    host_re = (re.compile(r"(?i)(?<![\w.-])(?:" + "|".join(re.escape(h) for h in hosts)
                          + r")(?![\w-]|\.[\w-])") if hosts else None)
    with _lock:
        _loc_cache = (snap, path_re, host_re)
    return path_re, host_re


def _env_secret_values() -> List[str]:
    """Secret values from the CURRENT environment, longest first (cached per
    environment snapshot — a changed env recomputes)."""
    global _cache
    snap = tuple(sorted(os.environ.items()))
    with _lock:
        if _cache is not None and _cache[0] == snap:
            return _cache[1]
    vals = set()
    for k, v in snap:
        if not v:
            continue
        if _SECRET_NAME.search(k) and len(v) >= _MIN_LEN:
            vals.add(v)
        if "://" in v and "@" in v:
            try:
                p = urllib.parse.urlsplit(v)
                for part in (p.password, p.username):
                    if part and len(part) >= _MIN_LEN:
                        vals.add(part)
            except ValueError:
                pass
    out = sorted(vals, key=len, reverse=True)
    with _lock:
        _cache = (snap, out)
    return out


def reset_cache() -> None:
    """Tests: forget the cached environment snapshot."""
    global _cache, _loc_cache
    with _lock:
        _cache = None
        _loc_cache = None


def redact(text: Any) -> Any:
    """``text`` with every recognised credential replaced by ``<redacted>``.
    Non-strings are returned unchanged."""
    if not isinstance(text, str) or not text:
        return text
    s = text
    for v in _env_secret_values():
        if v in s:
            s = s.replace(v, REDACTED)
    s = _USERINFO.sub(r"\1" + REDACTED + "@", s)
    s = _QUERY.sub(r"\1" + REDACTED, s)
    s = _AUTH.sub(r"\1" + REDACTED, s)
    s = _FORMATS.sub(REDACTED, s)
    s = _PAIR.sub(r"\1" + REDACTED, s)
    path_re, host_re = _location_patterns()
    if host_re is not None:
        s = host_re.sub(INTERNAL_HOST, s)
    if path_re is not None:
        s = path_re.sub(PATH, s)
    return s


def _walk(o: Any) -> Any:
    if isinstance(o, str):
        return redact(o)
    if isinstance(o, dict):
        return {k: _walk(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_walk(v) for v in o]
    if isinstance(o, tuple):
        return tuple(_walk(v) for v in o)
    return o


def public_failure_result(result: Any) -> Any:
    """A copy of a FAILED result with every string value redacted (keys and
    non-string values kept). The input is not mutated."""
    return _walk(result)


__all__ = ["INTERNAL_HOST", "INTERNAL_SERVICE_ENV", "PATH", "REDACTED",
           "public_failure_result", "redact", "reset_cache"]
