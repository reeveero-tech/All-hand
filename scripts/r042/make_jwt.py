#!/usr/bin/env python3
"""R-042 test harness: mint an HS256 JWT ``{"role": "anon"}`` (Supabase anon-key shape).

Usage: make_jwt.py <secret> [role]   (role: anon | service_role; secret is a throwaway test value, never a real key)
"""

import base64
import hashlib
import hmac
import json
import sys


def b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def make(secret: str, role: str = "anon") -> str:
    head = b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = b64(json.dumps({"role": role, "iss": "r042-local"}, separators=(",", ":")).encode())
    sig = hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{b64(sig)}"


if __name__ == "__main__":
    print(make(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "anon"))
