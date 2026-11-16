"""R-206 live verification (scripts/v2/r206_live_check.py — evidence: docs/audit/M10/logs/raw/028_r206_live.log)

Run by the operator only, never in CI (it talks to production and to real model providers).
Uses only what production already has: the platform OpenRouter key under the owner-approved
policy AGOS_PLATFORM_MODELS=free (models whose catalog price is 0) — nothing here can spend money,
and the gateway itself refuses any priced model on the platform key.

  AGOS_R206_USER   JSON {"run", "email", "pw"} of one confirmed test user created beforehand
                   (mode 600, outside the repo); deleted by the operator afterwards
  AGOS_R206_OUT    where the call ids / provider request ids go for the DB and billing checks

Phases:
  calls     sign in, create a workspace, plain + streamed calls, honest failures, list
  cleanup   delete the test workspace (the operator deletes the user by SQL afterwards)
Prints statuses, reasons, ids, tokens and cost only — never a token or password.
"""
import json, os, sys, time
import httpx

BASE = os.environ.get("AGOS_BASE", "https://all-hand.onrender.com")
API = BASE + "/api/v2"
USER = json.load(open(os.environ.get("AGOS_R206_USER", "/tmp/r206live/user.json")))
OUT = os.environ.get("AGOS_R206_OUT", "/tmp/r206live/out.json")
FREE = os.environ.get("AGOS_R206_FREE", "google/gemma-4-31b-it:free")
FREE2 = os.environ.get("AGOS_R206_FREE2", "nvidia/nemotron-3.5-lightning:free")
PAID = os.environ.get("AGOS_R206_PAID", "openai/gpt-4o-mini")
PROMPT = "Reply with exactly the word: pong"
MAX_TOKENS = int(os.environ.get("AGOS_R206_MAX_TOKENS", "256"))
fails = 0


def check(name, ok, detail=""):
    global fails
    fails += 0 if ok else 1
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""), flush=True)


def save(st):
    fd = os.open(OUT, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(st, f, indent=1)


def brief(r):
    keep = ("ok", "reason", "provider", "model", "key_source", "fallback_used", "tokens_in", "tokens_out",
            "cost_usd", "cost_source", "provider_request_id", "latency_ms", "call_id", "finish_reason")
    out = {k: r.get(k) for k in keep}
    out["attempts"] = [{k: a.get(k) for k in ("provider", "model", "reason", "http_status")}
                       for a in r.get("attempts") or []]
    out["text"] = (r.get("text") or "")[:60] if r.get("text") is not None else None
    return out


def sign_in(c):
    r = c.post(API + "/auth/login", json={"email": USER["email"], "password": USER["pw"]}, timeout=60)
    check("sign-in 200", r.status_code == 200, f"status={r.status_code}")
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def call(c, h, ws, routes, **kw):
    body = {"messages": [{"role": "user", "content": PROMPT}], "routes": routes, "max_tokens": MAX_TOKENS, **kw}
    for _ in range(4):
        r = c.post(f"{API}/workspaces/{ws}/model-calls", json=body, headers=h, timeout=120)
        if r.status_code != 429:
            break
        time.sleep(min(60, int(r.headers.get("retry-after", "15"))))
    return r


def stream(c, h, ws, routes):
    body = {"messages": [{"role": "user", "content": PROMPT}], "routes": routes, "max_tokens": MAX_TOKENS}
    events, t0, first = [], time.monotonic(), None
    with c.stream("POST", f"{API}/workspaces/{ws}/model-calls/stream", json=body, headers=h, timeout=120) as r:
        ctype, status = r.headers.get("content-type", ""), r.status_code
        ev = None
        for line in r.iter_lines():
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: ") and ev:
                if ev == "delta" and first is None:
                    first = round((time.monotonic() - t0) * 1000)
                events.append((ev, json.loads(line[6:])))
                ev = None
    return status, ctype, events, first


def phase_calls():
    c = httpx.Client()
    st = {"run": USER["run"], "calls": {}}
    h = sign_in(c)
    r = c.get(API + "/models", headers=h, timeout=60)
    cat = r.json() if r.status_code == 200 else {}
    models = cat.get("models", cat) if isinstance(cat, dict) else cat
    ids = {m.get("id"): m for m in models} if isinstance(models, list) else {}
    check("catalog 200 and lists the free model", r.status_code == 200 and FREE in ids,
          f"status={r.status_code} n={len(ids)} entry={json.dumps(ids.get(FREE), ensure_ascii=False)[:300]}")
    r = c.post(API + "/workspaces", headers=h, json={"name": f"r206-live-{USER['run']}"}, timeout=60)
    check("workspace created 201", r.status_code in (200, 201), f"status={r.status_code}")
    ws = st["workspace_id"] = r.json()["id"]
    save(st)

    # 1. plain call, free model on the platform key
    r = call(c, h, ws, [{"provider": "openrouter", "model": FREE}])
    b = brief(r.json()) if r.status_code == 200 else {"status": r.status_code, "body": r.text[:200]}
    st["calls"]["plain_free"] = b
    check("plain call on a free OpenRouter model: ok, platform key, cost 0",
          b.get("ok") is True and b.get("key_source") == "platform" and b.get("call_id")
          and b.get("provider_request_id") and str(b.get("cost_usd")) in ("0", "0.0", "0E-8", "0.00000000"),
          json.dumps(b, ensure_ascii=False))
    save(st)

    # 2. streamed call, same model
    status, ctype, events, first = stream(c, h, ws, [{"provider": "openrouter", "model": FREE}])
    kinds = [e for e, _ in events]
    done = events[-1][1] if events and events[-1][0] == "done" else {}
    text = "".join(d.get("text", "") for e, d in events if e == "delta")
    st["calls"]["stream_free"] = {"status": status, "content_type": ctype, "events": kinds if len(kinds) <= 8 else kinds[:3] + ["..."] + kinds[-2:],
                                  "n_delta": kinds.count("delta"), "first_delta_ms": first,
                                  "joined_text": text[:60], "done": brief(done) if done else None}
    check("streamed call: 200 text/event-stream, route -> delta(s) -> done ok, recorded",
          status == 200 and ctype.startswith("text/event-stream") and kinds[:1] == ["route"]
          and "delta" in kinds and done.get("ok") is True and done.get("call_id")
          and (done.get("text") or "") == text,
          json.dumps(st["calls"]["stream_free"], ensure_ascii=False))
    save(st)

    # 3. honest failures
    r = call(c, h, ws, [{"provider": "openrouter", "model": PAID}])
    b = brief(r.json()) if r.status_code == 200 else {"status": r.status_code}
    st["calls"]["paid_on_platform"] = b
    check("priced model on the platform key refused before any provider call",
          b.get("ok") is False and b.get("reason") == "platform_key_not_allowed" and b.get("text") is None
          and b.get("provider_request_id") is None, json.dumps(b, ensure_ascii=False))
    r = call(c, h, ws, [{"provider": "groq", "model": "llama-3.1-8b-instant"}])
    b = brief(r.json()) if r.status_code == 200 else {"status": r.status_code}
    st["calls"]["no_key"] = b
    check("provider without a key: honest no_key", b.get("ok") is False and b.get("reason") == "no_key",
          json.dumps(b, ensure_ascii=False))
    r = call(c, h, ws, [{"provider": "openrouter", "model": "agos-r206/does-not-exist:free"}])
    b = brief(r.json()) if r.status_code == 200 else {"status": r.status_code, "body": r.text[:200]}
    st["calls"]["unknown_model"] = b
    check("unknown model: refused honestly, no text", b.get("ok") is False and b.get("text") is None,
          json.dumps(b, ensure_ascii=False))
    r = call(c, h, ws, [{"provider": "openrouter", "model": PAID}, {"provider": "openrouter", "model": FREE2}])
    b = brief(r.json()) if r.status_code == 200 else {"status": r.status_code}
    st["calls"]["fallback"] = b
    check("fallback: refused priced route, answered by the second free model, said so",
          b.get("ok") is True and b.get("fallback_used") is True and b.get("model") == FREE2
          and (b.get("attempts") or [{}])[0].get("reason") == "platform_key_not_allowed",
          json.dumps(b, ensure_ascii=False))
    save(st)

    r = c.get(f"{API}/workspaces/{ws}/model-calls", headers=h, timeout=60)
    rows = r.json() if r.status_code == 200 else []
    rows = rows.get("calls", rows) if isinstance(rows, dict) else rows
    st["listed"] = len(rows)
    check("my calls are listed (6 recorded)", r.status_code == 200 and len(rows) == 6,
          f"status={r.status_code} n={len(rows)} fields={sorted(rows[0]) if rows else []}")
    save(st)


def phase_cleanup():
    c = httpx.Client()
    st = json.load(open(OUT))
    h = sign_in(c)
    r = c.delete(f"{API}/workspaces/{st['workspace_id']}", headers=h, timeout=60)
    check("test workspace deleted", r.status_code in (200, 204), f"status={r.status_code}")


if __name__ == "__main__":
    {"calls": phase_calls, "cleanup": phase_cleanup}[sys.argv[1]]()
    print("phase", sys.argv[1] + ":", "all passed" if not fails else f"{fails} failed")
    sys.exit(1 if fails else 0)
