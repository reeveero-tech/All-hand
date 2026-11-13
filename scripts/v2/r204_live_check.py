"""R-204 live verification (scripts/v2/r204_live_check.py — evidence: docs/audit/M10/logs/raw/013_r204_live_acceptance.log)

Run by the owner/operator only, never in CI (it talks to production):
  AGOS_SB_KEYS_FILE   JSON of `GET /v1/projects/{ref}/api-keys?reveal=true` (mode 600, outside the repo)
  AGOS_SB_MGMT_TOKEN_FILE  file holding a Supabase personal access token (mode 600, outside the repo)
  AGOS_RUN_SECRETS_OUT     where to write this run's generated secrets for the log scan (default /tmp)

R-204 live verification against the DEPLOYED service (https://all-hand.onrender.com)
and the live Supabase project. Test users are created confirmed through the Auth admin
API (no e-mail is sent), everything they create is deleted at the end.
Prints no token, password or key: only statuses, booleans and short fragments of ids."""
import base64, json, os, secrets, sys, time
import httpx

BASE = os.environ.get("AGOS_BASE", "https://all-hand.onrender.com")
API = BASE + "/api/v2"
REF = "qehloiuyzksnijstkfmn"
SB = f"https://{REF}.supabase.co"
keys = {k["name"]: k["api_key"] for k in json.load(open(os.environ.get("AGOS_SB_KEYS_FILE", "/tmp/.keys.json"))) if k.get("type") == "legacy"}
SERVICE, ANON = keys["service_role"], keys["anon"]
MGMT = open(os.environ.get("AGOS_SB_MGMT_TOKEN_FILE", "/tmp/.sbp")).read().strip()
RUN = secrets.token_hex(3)
OUT = os.environ.get("AGOS_RUN_SECRETS_OUT", "/tmp/.r204_run_secrets.json")
results, secrets_seen = [], []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""), flush=True)


def sql(q):
    r = httpx.post(f"https://api.supabase.com/v1/projects/{REF}/database/query",
                   headers={"Authorization": f"Bearer {MGMT}"}, json={"query": q}, timeout=60)
    r.raise_for_status()
    return r.json()


def admin(method, path, **kw):
    return httpx.request(method, f"{SB}/auth/v1/admin{path}", timeout=30,
                         headers={"apikey": SERVICE, "Authorization": f"Bearer {SERVICE}"}, **kw)


class U:
    def __init__(self, tag, confirmed=True):
        self.email = f"agos-r204-{RUN}-{tag}@example.test"
        self.password = secrets.token_urlsafe(18)
        r = admin("POST", "/users", json={"email": self.email, "password": self.password,
                                          "email_confirm": confirmed})
        assert r.status_code in (200, 201), (r.status_code, r.text[:200])
        self.id = r.json()["id"]
        self.c = httpx.Client(base_url=API, timeout=60)
        self.token = None
        secrets_seen.append(self.password)

    def req(self, method, path, token=..., **kw):
        tok = self.token if token is ... else token
        h = {"Authorization": f"Bearer {tok}"} if tok else {}
        for attempt in range(6):
            r = self.c.request(method, path, headers=h, **kw)
            if r.status_code != 429:
                return r
            time.sleep(min(60, int(r.headers.get("retry-after", "10"))))
        return r

    def login(self):
        r = self.req("POST", "/auth/login", token=None, json={"email": self.email, "password": self.password})
        if r.status_code == 200:
            self.token = r.json()["access_token"]
            secrets_seen.append(self.token)
            rt = self.c.cookies.get("agos_refresh") or next(iter(self.c.cookies.values()), None)
            if rt:
                secrets_seen.append(rt)
        return r


def no_secret_in(text):
    return not any(s and s in text for s in secrets_seen)


users, created_ws = [], []
try:
    # ------------------------------------------------------------ config
    cfg = httpx.get(API + "/auth/config", timeout=90).json()
    check("config: configured, e-mail on, no provider enabled",
          cfg.get("configured") is True and cfg.get("email") is True and not any(cfg.get("providers", {}).values()), json.dumps(cfg)[:160])

    # ------------------------------------------------------------ sign-up probe (public route, real e-mail path)
    probe = f"agos-r204-{RUN}-signup@example.test"
    r = httpx.post(API + "/auth/signup", json={"email": probe, "password": secrets.token_urlsafe(18)}, timeout=60)
    check("sign-up through the public route answers (status recorded)", r.status_code in (200, 201, 422, 429, 500, 502),
          f"status={r.status_code} body={r.text[:160]}")
    pu = sql(f"select id, email_confirmed_at is not null as confirmed from auth.users where email = '{probe}'")
    check("sign-up never yields a confirmed account without e-mail confirmation",
          all(not x["confirmed"] for x in pu), f"rows={len(pu)}")
    for x in pu:
        admin("DELETE", f"/users/{x['id']}")

    A, B, C, D = U("a"), U("b"), U("c"), U("d", confirmed=False)
    users += [A, B, C, D]

    # ------------------------------------------------------------ sign-in, session shape
    r = A.login()
    sc = r.headers.get("set-cookie", "")
    body = r.text
    check("A sign-in 200", r.status_code == 200, f"status={r.status_code}")
    check("refresh token only in an HttpOnly; Secure; SameSite=Strict cookie on /api/v2/auth",
          all(x in sc.lower() for x in ("httponly", "secure", "samesite=strict", "path=/api/v2/auth")), sc.split("=")[0])
    check("refresh token not in the JSON body", "refresh_token" not in r.json())
    check("sign-in response Cache-Control: no-store", "no-store" in r.headers.get("cache-control", ""))
    for u in (B, C):
        check(f"{u.email.split('-')[-1].split('@')[0].upper()} sign-in 200", u.login().status_code == 200)
    r = D.login()
    check("unconfirmed user refused (403)", r.status_code == 403, f"status={r.status_code}")
    bad = httpx.post(API + "/auth/login", json={"email": A.email, "password": "wrong-" + A.password}, timeout=60)
    unk = httpx.post(API + "/auth/login", json={"email": f"nobody-{RUN}@example.test", "password": "x" * 12}, timeout=60)
    check("wrong password and unknown e-mail look the same (no enumeration)",
          bad.status_code == unk.status_code == 401 and bad.json() == unk.json(), f"{bad.status_code}/{unk.status_code}")
    r = A.req("GET", "/me")
    check("/me returns A", r.status_code == 200 and r.json()["user"]["id"] == A.id)

    # ------------------------------------------------------------ workspaces + isolation
    wa = A.req("POST", "/workspaces", json={"name": f"r204-live-A-{RUN}"}).json()["id"]
    wb = B.req("POST", "/workspaces", json={"name": f"r204-live-B-{RUN}"}).json()["id"]
    created_ws += [(A, wa), (B, wb)]
    check("A's list has WA only", [w["id"] for w in A.req("GET", "/workspaces").json()] == [wa])
    check("B's list has WB only", [w["id"] for w in B.req("GET", "/workspaces").json()] == [wb])
    fake_uuid = "00000000-0000-4000-8000-000000000000"
    for actor, other, label in ((B, wa, "B→WA"), (A, wb, "A→WB")):
        st = {
            "get": actor.req("GET", f"/workspaces/{other}").status_code,
            "rename": actor.req("PATCH", f"/workspaces/{other}", json={"name": "pwned"}).status_code,
            "delete": actor.req("DELETE", f"/workspaces/{other}").status_code,
            "members": actor.req("GET", f"/workspaces/{other}/members").status_code,
            "set_role": actor.req("PATCH", f"/workspaces/{other}/members/{actor.id}", json={"role": "owner"}).status_code,
            "remove": actor.req("DELETE", f"/workspaces/{other}/members/{A.id if actor is B else B.id}").status_code,
            "invitations": actor.req("GET", f"/workspaces/{other}/invitations").status_code,
            "invite": actor.req("POST", f"/workspaces/{other}/invitations", json={"email": actor.email, "role": "admin"}).status_code,
            "revoke": actor.req("DELETE", f"/workspaces/{other}/invitations/{fake_uuid}").status_code,
        }
        check(f"isolation {label}: every route 404 (9 operations)", set(st.values()) == {404}, json.dumps(st))
    still = sql(f"select name, (select count(*) from agos.workspace_members m where m.workspace_id = w.id) as n from agos.workspaces w where id in ('{wa}','{wb}') order by name")
    check("database unchanged after the cross attempts", [x["n"] for x in still] == [1, 1] and all("pwned" not in x["name"] for x in still), json.dumps(still))

    # ------------------------------------------------------------ forged / invalid tokens
    hdr, pay, sig = A.token.split(".")
    p = json.loads(base64.urlsafe_b64decode(pay + "=" * (-len(pay) % 4)))
    p["sub"] = B.id
    forged_sub = ".".join([hdr, base64.urlsafe_b64encode(json.dumps(p).encode()).decode().rstrip("="), sig])
    none_hdr = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').decode().rstrip("=")
    alg_none = ".".join([none_hdr, pay, ""])
    st = {name: A.req("GET", "/workspaces", token=t).status_code for name, t in
          (("none", None), ("garbage", "abc.def.ghi"), ("sub_swapped", forged_sub), ("alg_none", alg_none),
           ("anon_key", ANON), ("service_key", SERVICE))}
    check("forged/invalid/foreign tokens refused (401), incl. the service and anon keys", set(st.values()) == {401}, json.dumps(st))
    xk = httpx.get(API + "/workspaces", headers={"X-API-Key": "not-an-identity"}, timeout=60).status_code
    check("X-API-Key is not an identity on /api/v2", xk == 401, f"status={xk}")

    # ------------------------------------------------------------ Data API cannot reach agos
    r = httpx.get(f"{SB}/rest/v1/workspaces", headers={"apikey": ANON, "Authorization": f"Bearer {A.token}",
                                                         "Accept-Profile": "agos"}, timeout=30)
    check("Supabase Data API does not expose schema agos", r.status_code in (400, 404, 406), f"status={r.status_code} {r.text[:90]}")

    # ------------------------------------------------------------ invitations, roles
    inv = A.req("POST", f"/workspaces/{wa}/invitations", json={"email": B.email, "role": "member"})
    tok_b = inv.json().get("token")
    secrets_seen.append(tok_b)
    check("owner creates an invitation (201, token once)", inv.status_code == 201 and bool(tok_b), f"status={inv.status_code}")
    lst = A.req("GET", f"/workspaces/{wa}/invitations").text
    check("invitation list never contains the token", tok_b not in lst)
    r = C.req("POST", "/invitations/accept", json={"token": tok_b})
    check("someone else's e-mail cannot accept (C with B's token)", r.status_code in (403, 404), f"status={r.status_code}")
    r = B.req("POST", "/invitations/accept", json={"token": tok_b})
    check("invited B accepts", r.status_code == 200, f"status={r.status_code}")
    r = B.req("POST", "/invitations/accept", json={"token": tok_b})
    check("the same token cannot be used twice", r.status_code in (404, 409, 410), f"status={r.status_code}")
    roles = {w["id"]: w["role"] for w in B.req("GET", "/workspaces").json()}
    check("B now sees WA as member (and WB as owner)", roles.get(wa) == "member" and roles.get(wb) == "owner", json.dumps(roles))
    st = {"set_role": B.req("PATCH", f"/workspaces/{wa}/members/{B.id}", json={"role": "owner"}).status_code,
          "invite": B.req("POST", f"/workspaces/{wa}/invitations", json={"email": C.email}).status_code,
          "rename": B.req("PATCH", f"/workspaces/{wa}", json={"name": "x"}).status_code,
          "delete": B.req("DELETE", f"/workspaces/{wa}").status_code,
          "remove_owner": B.req("DELETE", f"/workspaces/{wa}/members/{A.id}").status_code}
    check("member B cannot change roles, invite, rename, delete or remove the owner (403)", set(st.values()) == {403}, json.dumps(st))

    inv = A.req("POST", f"/workspaces/{wa}/invitations", json={"email": C.email, "role": "admin"})
    tok_c = inv.json().get("token"); secrets_seen.append(tok_c)
    check("C accepts an admin invitation", C.req("POST", "/invitations/accept", json={"token": tok_c}).status_code == 200)
    st = {"invite_admin": C.req("POST", f"/workspaces/{wa}/invitations", json={"email": f"x-{RUN}@example.test", "role": "admin"}).status_code,
          "set_role": C.req("PATCH", f"/workspaces/{wa}/members/{B.id}", json={"role": "admin"}).status_code,
          "remove_owner": C.req("DELETE", f"/workspaces/{wa}/members/{A.id}").status_code,
          "delete_ws": C.req("DELETE", f"/workspaces/{wa}").status_code}
    check("admin C cannot invite admins, change roles, remove the owner or delete (403)", set(st.values()) == {403}, json.dumps(st))
    r = C.req("POST", f"/workspaces/{wa}/invitations", json={"email": f"m-{RUN}@example.test", "role": "member"})
    check("admin C can invite a member", r.status_code == 201, f"status={r.status_code}")
    check("admin C can remove member B", C.req("DELETE", f"/workspaces/{wa}/members/{B.id}").status_code == 204)
    check("B no longer reaches WA", B.req("GET", f"/workspaces/{wa}").status_code == 404)
    check("C cannot reach WB (never a member)", C.req("GET", f"/workspaces/{wb}").status_code == 404)

    # revoked + expired
    inv = A.req("POST", f"/workspaces/{wa}/invitations", json={"email": B.email}).json()
    secrets_seen.append(inv.get("token"))
    A.req("DELETE", f"/workspaces/{wa}/invitations/{inv['id']}")
    r = B.req("POST", "/invitations/accept", json={"token": inv["token"]})
    check("revoked invitation refused (410)", r.status_code == 410, f"status={r.status_code}")
    inv = A.req("POST", f"/workspaces/{wa}/invitations", json={"email": B.email}).json()
    secrets_seen.append(inv.get("token"))
    sql(f"update agos.workspace_invitations set created_at = now() - interval '2 hours', expires_at = now() - interval '1 minute' where id = '{inv['id']}'")
    r = B.req("POST", "/invitations/accept", json={"token": inv["token"]})
    check("expired invitation refused (410)", r.status_code == 410, f"status={r.status_code}")
    r = B.req("POST", "/invitations/accept", json={"token": secrets.token_urlsafe(32)})
    check("unknown invitation token refused", r.status_code in (404, 410), f"status={r.status_code}")

    # ------------------------------------------------------------ refresh, logout, banned
    r = A.req("POST", "/auth/refresh", token=None)
    ok = r.status_code == 200 and r.json().get("access_token")
    if ok:
        A.token = r.json()["access_token"]; secrets_seen.append(A.token)
    check("refresh with the cookie gives a new access token", ok, f"status={r.status_code}")
    old = A.token
    r = A.req("POST", "/auth/logout")
    check("logout 204", r.status_code == 204, f"status={r.status_code}")
    check("the access token is dead right after logout (401)", A.req("GET", "/workspaces", token=old).status_code == 401)
    check("refresh after logout refused (401)", A.req("POST", "/auth/refresh", token=None).status_code == 401)
    r = A.login()
    check("A can sign in again", r.status_code == 200)
    btok = B.token
    admin("PUT", f"/users/{B.id}", json={"ban_duration": "1h"})
    check("a banned user's live token is refused (401)", B.req("GET", "/workspaces", token=btok).status_code == 401)

    # ------------------------------------------------------------ leaks
    all_text = json.dumps([r[2] for r in results])
    check("no secret in this report", no_secret_in(all_text))
finally:
    for owner, ws in created_ws:
        try:
            owner.login() if owner.token is None else None
            owner.req("DELETE", f"/workspaces/{ws}")
        except Exception:
            pass
    sql(f"delete from agos.workspaces where name like 'r204-live-%-{RUN}'")
    for u in users:
        admin("DELETE", f"/users/{u.id}")
    left = sql(f"select (select count(*) from auth.users where email like 'agos-r204-{RUN}-%') as users, "
               f"(select count(*) from agos.workspaces) as workspaces, (select count(*) from agos.workspace_members) as members, "
               f"(select count(*) from agos.workspace_invitations) as invitations")
    print("cleanup:", json.dumps(left))
    json.dump({"run": RUN, "secrets_for_log_scan": secrets_seen}, open(OUT, "w"))
    os.chmod(OUT, 0o600)

fails = [r for r in results if not r[1]]
print(f"\nSUMMARY: {len(results) - len(fails)} passed, {len(fails)} failed (run {RUN})")
sys.exit(1 if fails else 0)
