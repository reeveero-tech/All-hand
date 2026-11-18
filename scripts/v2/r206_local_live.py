"""R-206 — live calls through the REAL gateway code to a REAL local model (Ollama).

Operator tool, never run in CI. Needs a local Ollama (``ollama serve``) with a
small model pulled (default ``qwen3:0.6b``: tools + thinking). No key, no
account, no cost: the "local" route of R-206's acceptance ("three providers,
one of them local or free").

What it proves (and what it does NOT):
* real model, real HTTP, real Server-Sent Events through ``agos_v2.gateway``
  (``Gateway.complete`` / ``Gateway.stream``) — not a scripted server;
* a long streamed answer in many chunks: order kept, nothing lost (joined
  deltas == final text), proper end, usage reported;
* tools: the model asks for a tool, the gateway returns the request and runs
  nothing; the caller's result goes back and the model answers;
* M10/028 finding 1 live: a thinking model given a tiny ``max_tokens`` spends
  it before writing -> ``empty_output`` (not ``ok`` with empty text);
* honest failures: unknown model (404), unreachable server, client leaving
  mid-stream (``client_disconnected``);
* no prompt, answer or tool argument in the gateway's log lines.
NOT proven here: the production server path (session, vault, call log) —
that is ``scripts/v2/r206_live_check.py`` — and any cloud provider.

Usage: python scripts/v2/r206_local_live.py [--base http://127.0.0.1:11434] [--model qwen3:0.6b]
Prints one JSON report (no secrets exist on this path).
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agos_v2.gateway import ChatRequest, Credential, Gateway, Route  # noqa: E402

MARK = "MARK-" + uuid.uuid4().hex[:12]      # a canary in every prompt: must never reach a log line


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:11434")
    ap.add_argument("--model", default="qwen3:0.6b")
    a = ap.parse_args()

    logbuf = io.StringIO()
    h = logging.StreamHandler(logbuf)
    h.setLevel(logging.DEBUG)
    lg = logging.getLogger("agos.v2.gateway")
    lg.addHandler(h)
    lg.setLevel(logging.DEBUG)

    gw = Gateway(allow_private=True, timeout_s=300, deadline_s=600, stream_deadline_s=600)
    cred = lambda p: Credential(provider="ollama", secret=None, source="byok", key_id="local",  # noqa: E731
                                base_url=a.base) if p == "ollama" else None
    route = Route("ollama", a.model)
    report = {"base": a.base, "model": a.model, "checks": []}

    def check(name, ok, detail):
        report["checks"].append({"name": name, "pass": bool(ok), "detail": detail})
        print(("PASS " if ok else "FAIL ") + name, file=sys.stderr)

    # 1. plain call
    t = time.monotonic()
    r = gw.complete(ChatRequest(messages=[{"role": "user", "content": f"/no_think Reply with exactly: pong ({MARK})"}],
                                max_tokens=400), [route], cred)
    check("plain call: ok, real text, tokens, cost unknown (local)",
          r.ok and r.text and r.tokens_in and r.tokens_out and r.cost_usd is None and r.cost_source is None,
          {"ok": r.ok, "reason": r.reason, "text_len": len(r.text or ""), "finish_reason": r.finish_reason,
           "tokens_in": r.tokens_in, "tokens_out": r.tokens_out, "cost_usd": r.to_dict()["cost_usd"],
           "provider_request_id": r.provider_request_id, "latency_ms": r.latency_ms,
           "wall_s": round(time.monotonic() - t, 1)})

    # 2. long stream, many chunks
    t = time.monotonic()
    s = gw.stream(ChatRequest(messages=[{"role": "user", "content":
                                         f"/no_think Count from 1 to 80, one number per line, nothing else. ({MARK})"}],
                              max_tokens=1500), [route], cred)
    kinds, deltas, first = [], [], None
    for ev in s:
        kinds.append(ev["type"])
        if ev["type"] == "delta":
            deltas.append(ev["text"])
            first = first or round(time.monotonic() - t, 2)
    res = s.result
    joined = "".join(deltas)
    nums = [ln.strip() for ln in (res.text or "").splitlines() if ln.strip().isdigit()]
    check("long stream: route first, many deltas, joined deltas == final text, ended ok",
          kinds[:1] == ["route"] and kinds.count("route") == 1 and len(deltas) >= 50 and res.ok
          and joined == res.text and res.finish_reason in ("stop", "length"),
          {"events": len(kinds), "n_delta": len(deltas), "first_delta_s": first, "chars": len(joined),
           "finish_reason": res.finish_reason, "tokens_in": res.tokens_in, "tokens_out": res.tokens_out,
           "numbers_found": len(nums), "first_numbers": nums[:3], "last_numbers": nums[-3:],
           "in_order": nums == [str(i) for i in range(1, len(nums) + 1)],
           "wall_s": round(time.monotonic() - t, 1)})

    # 3. tools: request -> (caller runs it) -> answer; the gateway runs nothing
    tools = [{"name": "get_weather", "description": "Current weather for a city",
              "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}]
    ask = [{"role": "user", "content": f"/no_think What is the weather in Jeddah? Use the tool. ({MARK})"}]
    r1 = gw.complete(ChatRequest(messages=ask, tools=tools, tool_choice="auto", max_tokens=600), [route], cred)
    call = r1.tool_calls[0] if r1.tool_calls else None
    args = json.loads(call["arguments"]) if call and call["arguments_valid"] else None
    check("tools: model asks for get_weather with valid JSON arguments; nothing executed by the gateway",
          r1.ok and call and call["name"] == "get_weather" and call["arguments_valid"] and isinstance(args, dict)
          and "city" in args,
          {"ok": r1.ok, "reason": r1.reason, "finish_reason": r1.finish_reason, "n_calls": len(r1.tool_calls),
           "name": call and call["name"], "arguments_valid": call and call["arguments_valid"],
           "arg_keys": sorted(args) if isinstance(args, dict) else None})
    if call:
        follow = ask + [{"role": "assistant", "content": r1.text or None,
                         "tool_calls": [{"id": call["id"], "name": call["name"], "arguments": call["arguments"]}]},
                        {"role": "tool", "tool_call_id": call["id"],
                         "content": json.dumps({"city": "Jeddah", "temp_c": 31, "sky": "clear"})}]
        r2 = gw.complete(ChatRequest(messages=follow, tools=tools, max_tokens=600), [route], cred)
        check("tools: the caller's tool result goes back and the model answers in text using it",
              r2.ok and r2.text and "31" in r2.text,
              {"ok": r2.ok, "reason": r2.reason, "finish_reason": r2.finish_reason, "text_len": len(r2.text or ""),
               "mentions_31": "31" in (r2.text or ""), "n_calls": len(r2.tool_calls)})
        # streamed tool request
        st = gw.stream(ChatRequest(messages=ask, tools=tools, max_tokens=600), [route], cred)
        list(st)
        sr = st.result
        check("tools in a stream: the tool request arrives whole in the result",
              sr.ok and sr.tool_calls and sr.tool_calls[0]["name"] == "get_weather" and sr.tool_calls[0]["arguments_valid"],
              {"ok": sr.ok, "reason": sr.reason, "finish_reason": sr.finish_reason, "n_calls": len(sr.tool_calls)})

    # 4. finding 1 live: thinking model, tiny max_tokens
    r = gw.complete(ChatRequest(messages=[{"role": "user", "content": f"Explain why the sky is blue. ({MARK})"}],
                                max_tokens=8), [route], cred)
    check("finding 1 live: thinking spends max_tokens before any text -> empty_output, billed fields kept",
          not r.ok and r.reason == "empty_output" and r.text is None and r.tokens_out is not None,
          {"ok": r.ok, "reason": r.reason, "finish_reason": r.finish_reason, "tokens_in": r.tokens_in,
           "tokens_out": r.tokens_out, "attempts": [x["reason"] for x in r.attempts]})
    s = gw.stream(ChatRequest(messages=[{"role": "user", "content": f"Explain why the sky is blue. ({MARK})"}],
                              max_tokens=8), [route], cred)
    kinds = [e["type"] for e in s]
    check("finding 1 live (stream): no delta, empty_output",
          s.result.reason == "empty_output" and "delta" not in kinds,
          {"events": kinds, "reason": s.result.reason, "finish_reason": s.result.finish_reason,
           "tokens_out": s.result.tokens_out})

    # 5. honest failures
    r = gw.complete(ChatRequest(messages=[{"role": "user", "content": MARK}], max_tokens=8),
                    [Route("ollama", "agos-r206-does-not-exist")], cred)
    check("unknown model: 404 -> model_or_endpoint_not_found, no text",
          not r.ok and r.reason == "model_or_endpoint_not_found" and r.http_status == 404 and r.text is None,
          {"reason": r.reason, "http_status": r.http_status})
    dead = lambda p: Credential(provider="ollama", secret=None, source="byok", key_id="x",  # noqa: E731
                                base_url="http://127.0.0.1:9") if p == "ollama" else None
    r = Gateway(allow_private=True, sleep=lambda s: None).complete(
        ChatRequest(messages=[{"role": "user", "content": MARK}], max_tokens=8), [route], dead)
    check("unreachable server: unreachable after bounded retries, no text",
          not r.ok and r.reason == "unreachable" and r.text is None and r.attempts[0]["retries"] == 2,
          {"reason": r.reason, "retries": r.attempts[0]["retries"]})
    s = gw.stream(ChatRequest(messages=[{"role": "user", "content": f"/no_think Count from 1 to 300. ({MARK})"}],
                              max_tokens=3000), [route], cred)
    got = 0
    for ev in s:
        if ev["type"] == "delta":
            got += 1
            if got == 5:
                s.cancel()
    check("client leaves mid-stream: client_disconnected, no text kept",
          s.result.reason == "client_disconnected" and s.result.text is None and not s.result.ok,
          {"deltas_before_cancel": got, "reason": s.result.reason})

    logs = logbuf.getvalue()
    check("gateway log lines: metadata only (no prompt canary, no answer)", MARK not in logs and "pong" not in logs,
          {"log_lines": logs.count("\n")})
    report["passed"] = sum(c["pass"] for c in report["checks"])
    report["total"] = len(report["checks"])
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
