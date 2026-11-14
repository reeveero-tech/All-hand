"""R-205 live verification (scripts/v2/r205_live_check.py — evidence: docs/audit/M10/logs/raw/018_r205_live.log)

Run by the operator only, never in CI (it talks to production). Uses FAKE provider keys only:
the honest key test calls the provider's free model-list/key endpoint, a fake key is answered
401 (invalid_key) and costs nothing. No owner key is used.

  AGOS_R205_USERS   JSON {"run", "users": [{"email", "pw", ...}, ...]} of two confirmed test users
                    created beforehand (mode 600, outside the repo); deleted by the operator afterwards
  AGOS_R205_STATE   where this script keeps ids/tokens between phases (mode 600, default /tmp)

Phases (the operator checks the database between them, via SQL, without the key):
  add      sign in A and B, create a workspace each, add fake keys, list, test, cross-user probes
  revoke   revoke A's key, test again (must be refused), delete it, list again
  cleanup  delete the test workspaces
Prints statuses, booleans and last4 only — never a token, password or full key.
"""
import json, os, secrets, sys, time
import httpx

BASE = os.environ.get("AGOS_BASE", "https://all-hand.onrender.com")
API = BASE + "/api/v2"
USERS = json.load(open(os.environ.get("AGOS_R205_USERS", "/tmp/r205/live_users.json")))
STATE = os.environ.get("AGOS_R205_STATE", "/tmp/r205/live_state.json")
fails = 0


def check(name, ok, detail=""):
    global fails
    fails += 0 if ok else 1
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""), flush=True)


def req(c, method, path, tok=None, **kw):
    h = {"Authorization": f"Bearer {tok}"} if tok else {}
    for _ in range(6):
        r = c.request(method, API + path, headers=h, timeout=90, **kw)
        if r.status_code != 429 or "/test" in path:
            return r
        time.sleep(min(60, int(r.headers.get("retry-after", "10"))))
    return r


def save(st):
    fd = os.open(STATE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(st, f)


def phase_add():
    c = httpx.Client()
    st = {"run": USERS["run"], "secrets": []}
    r = req(c, "GET", "/provider-keys/providers")
    ids = [p["id"] for p in r.json()]
    check("providers list 200 with 7 providers", r.status_code == 200 and len(ids) == 7, ",".join(ids))
    toks = []
    for u in USERS["users"]:
        r = req(c, "POST", "/auth/login", json={"email": u["email"], "password": u["pw"]})
        check(f"{u['tag'].upper()} sign-in 200", r.status_code == 200, f"status={r.status_code}")
        toks.append(r.json()["access_token"])
    A, B = toks
    st["secrets"] += toks
    wa = req(c, "POST", "/workspaces", A, json={"name": f"r205-live-A-{st['run']}"}).json()["id"]
    wb = req(c, "POST", "/workspaces", B, json={"name": f"r205-live-B-{st['run']}"}).json()["id"]
    st.update(A=A, B=B, wa=wa, wb=wb)

    fake = "sk-proj-agosfake" + secrets.token_hex(20)
    st["fake"] = fake
    st["secrets"].append(fake)
    r = req(c, "POST", f"/workspaces/{wa}/provider-keys", A, json={"provider": "openai", "api_key": fake, "label": "r205 fake"})
    body = r.text
    check("A adds a fake OpenAI key: 201", r.status_code == 201, f"status={r.status_code}")
    check("add response has last4 only, not the key", fake not in body and r.json().get("last4") == fake[-4:]
          and "api_key" not in r.json() and "key_ct" not in r.json(), f"last4={r.json().get('last4')}")
    kid = r.json()["id"]
    st["kid"] = kid
    r = req(c, "GET", f"/workspaces/{wa}/provider-keys", A)
    check("A's list: 1 key, last4 only, no secret material", r.status_code == 200 and len(r.json()) == 1
          and fake not in r.text and not any(k in r.text for k in ("key_ct", "dek_wrapped", "key_nonce")),
          f"fields={sorted(r.json()[0].keys()) if r.status_code == 200 and r.json() else r.text[:80]}")
    r = req(c, "POST", f"/workspaces/{wa}/provider-keys/{kid}/test", A)
    j = r.json()
    check("honest test of a fake key: ok=false reason=invalid_key (provider said 401)",
          r.status_code == 200 and j.get("ok") is False and j.get("reason") == "invalid_key" and fake not in r.text,
          json.dumps({k: j.get(k) for k in ("ok", "reason", "http_status")}))

    r = req(c, "POST", f"/workspaces/{wa}/provider-keys", None, json={"provider": "openai", "api_key": fake})
    check("no session -> 401", r.status_code == 401, f"status={r.status_code}")
    st_codes = {
        "list": req(c, "GET", f"/workspaces/{wa}/provider-keys", B).status_code,
        "add": req(c, "POST", f"/workspaces/{wa}/provider-keys", B, json={"provider": "groq", "api_key": "gsk_" + "x" * 40, "label": "x"}).status_code,
        "test": req(c, "POST", f"/workspaces/{wa}/provider-keys/{kid}/test", B).status_code,
        "edit": req(c, "PATCH", f"/workspaces/{wa}/provider-keys/{kid}", B, json={"label": "pwned"}).status_code,
        "revoke": req(c, "POST", f"/workspaces/{wa}/provider-keys/{kid}/revoke", B).status_code,
        "delete": req(c, "DELETE", f"/workspaces/{wa}/provider-keys/{kid}", B).status_code,
        "via_own_ws": req(c, "POST", f"/workspaces/{wb}/provider-keys/{kid}/test", B).status_code,
    }
    check("isolation: B cannot list/add/test/edit/revoke/delete A's key (all 404)",
          all(v == 404 for v in st_codes.values()), json.dumps(st_codes))
    r = req(c, "GET", f"/workspaces/{wb}/provider-keys", B)
    check("B's own list is empty", r.status_code == 200 and r.json() == [], r.text[:80])
    # an Ollama URL may be saved, but the honest test goes through the SEC-05 outbound guard:
    # loopback / link-local (cloud metadata) must be refused before any connection
    for url in ("http://127.0.0.1:11434", "http://169.254.169.254", "http://localhost:11434"):
        r = req(c, "POST", f"/workspaces/{wa}/provider-keys", A, json={"provider": "ollama", "base_url": url, "label": "r205 guard"})
        oid = r.json().get("id") if r.status_code == 201 else None
        t = req(c, "POST", f"/workspaces/{wa}/provider-keys/{oid}/test", A) if oid else r
        check(f"ollama {url}: test refused by the outbound guard (blocked_address)",
              t.status_code == 200 and t.json().get("reason") == "blocked_address", f"status={t.status_code} {t.text[:100]}")
        if oid:
            req(c, "DELETE", f"/workspaces/{wa}/provider-keys/{oid}", A)
    r = req(c, "POST", f"/workspaces/{wa}/provider-keys", A, json={"provider": "openai", "api_key": "short", "label": "x"})
    check("malformed key refused (422) and not echoed", r.status_code == 422 and "short" not in r.text.replace("too short", ""),
          f"status={r.status_code}")
    save(st)


def phase_revoke():
    st = json.load(open(STATE))
    c = httpx.Client()
    A, wa, kid, fake = st["A"], st["wa"], st["kid"], st["fake"]
    r = req(c, "POST", f"/workspaces/{wa}/provider-keys/{kid}/revoke", A)
    check("A revokes: 200 status=revoked, last4 cleared or kept, no key", r.status_code == 200
          and r.json().get("status") == "revoked" and fake not in r.text, f"status={r.status_code} last4={r.json().get('last4')}")
    r = req(c, "POST", f"/workspaces/{wa}/provider-keys/{kid}/test", A)
    check("test after revoke is refused (410)", r.status_code == 410, f"status={r.status_code} {r.text[:100]}")
    r = req(c, "DELETE", f"/workspaces/{wa}/provider-keys/{kid}", A)
    check("A deletes: 204", r.status_code == 204, f"status={r.status_code}")
    r = req(c, "GET", f"/workspaces/{wa}/provider-keys", A)
    check("A's list is empty after delete", r.status_code == 200 and r.json() == [], r.text[:80])
    r = req(c, "POST", f"/workspaces/{wa}/provider-keys/{kid}/test", A)
    check("test after delete: 404", r.status_code == 404, f"status={r.status_code}")


def phase_cleanup():
    st = json.load(open(STATE))
    c = httpx.Client()
    for tok, ws in ((st["A"], st["wa"]), (st["B"], st["wb"])):
        r = req(c, "DELETE", f"/workspaces/{ws}", tok)
        check("test workspace deleted", r.status_code in (200, 204), f"status={r.status_code}")


{"add": phase_add, "revoke": phase_revoke, "cleanup": phase_cleanup}[sys.argv[1]]()
print(f"phase {sys.argv[1]}: {'all passed' if fails == 0 else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
