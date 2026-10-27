"""R-188 operational limits (R-164/R-166): the volatile queue's REAL cap
under a real load.

The R-164 unit tests prove the admission CONTRACT with small monkeypatched
caps (2/3) through the in-process client. What was NOT proven anywhere is
the cap the CONTRACT actually names for the volatile mode — MAX_TASKS_KEPT
(500 unfinished tasks, the real default value) — under a REAL load:

- slow deterministic tasks (a blocking external recipient) hold every
  accepted task mid-flight, so the queue genuinely fills to the cap;
- the fill and the refusals go through the REAL admission interface (HTTP
  POSTs against a real uvicorn subprocess, not the in-process client);
- the documented refusal fires with its documented codes: 503 service-busy
  + Retry-After at the GLOBAL cap, 429 + Retry-After at the per-tenant
  cap (with tenant isolation), and a refused task leaves NO row;
- NOTHING accepted is lost and NOTHING is duplicated: releasing the
  pressure completes every accepted task exactly once — one send per task,
  one result per task, all rows terminal — and the RECOVERY after the
  pressure clears is observed, not assumed: a NEW task is accepted (202),
  runs once and completes once, and the live count returns to zero (the
  cap did not leak a slot);
- CONCURRENT admission at the boundary is atomic: N simultaneous POSTs
  crossing the cap accept EXACTLY cap tasks (never cap+1 — the admission
  check is not a check-then-act race), and the rest are refused with the
  documented codes leaving no rows.

Honest limits: the recipient is a test double (the load is real HTTP
concurrency against a real server process, but the "slow" part is a
blocking test server, not a slow production backend); latency and memory
under SUSTAINED pressure are NOT measured here (R-166 scope — no claim is
made); the volatile mode's restart loss is the mode's documented limit
(the no-loss-on-restart guarantee is the DURABLE queue's, proven in
test_r188_task_loss.py); the request-rate limiter is raised via env for
this test (the target is the QUEUE cap, not the request-rate cap — its
own contract is tested elsewhere and fixed in its own batch).
"""
from __future__ import annotations

import json
import hashlib
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_r188_task_loss as loss  # the shared real-server harness

fake_backend = loss.fake_backend  # the fake PostgREST the subprocess talks to

# the REAL defaults this file targets (never monkeypatched): the volatile
# queue's global cap. Read from the same source the subprocess runs.
import server  # noqa: E402

REAL_CAP = server.MAX_TASKS_KEPT
assert REAL_CAP == 500, "the volatile cap changed — update this file's docstring"

# a SECOND tenant (isolation): its own key, registered alongside the first
BKEY = "r188-oplim-" + secrets.token_urlsafe(12)
HEADERS_B = {"X-API-Key": BKEY, "Content-Type": "application/json"}


class _LoadRecipientState:
    """ALL requests block until release (unlike the one-shot R-188 barrier
    recipient: the load test holds EVERY accepted task mid-flight)."""

    def __init__(self):
        self.release = threading.Event()
        self.requests = []          # {"key": str|None, "prompt": str} per call
        self.lock = threading.Lock()

    def record(self, key, prompt):
        with self.lock:
            self.requests.append({"key": key, "prompt": prompt})


class _LoadRecipient(BaseHTTPRequestHandler):
    state: _LoadRecipientState = None

    def log_message(self, *a):
        pass

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        key = self.headers.get("Idempotency-Key")
        prompt = ""
        try:
            prompt = str((body.get("messages") or [{}])[0].get("content", ""))
        except Exception:
            pass
        self.state.record(key, prompt)
        # hold EVERY request mid-flight until the pressure is released
        while not self.state.release.wait(0.5):
            pass
        out = json.dumps({"choices": [
            {"message": {"role": "assistant", "content": "r188 load run done"}}
        ]}).encode()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
        except (BrokenPipeError, ConnectionResetError):
            pass  # a client that died mid-hold is fine here


def _load_recipient(name: str):
    state = _LoadRecipientState()
    handler = type(f"_LoadRecipient_{name}", (_LoadRecipient,), {"state": state})

    class _Srv(ThreadingHTTPServer):
        # a LARGE listen backlog: the concurrent test holds ~500 connections
        # at once — the default backlog (5) would refuse them at the socket
        request_queue_size = 2048
        daemon_threads = True

    httpd = _Srv(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return state, f"http://127.0.0.1:{httpd.server_address[1]}/run"


def _load_server(recipient_url):
    """A real uvicorn subprocess in the VOLATILE mode (AGOS_TASK_QUEUE=0 — the
    in-memory cap under test), with the request-rate limiter raised: the
    target is the QUEUE cap, not the request-rate cap (its own contract is
    tested in its own file/batch)."""
    env = {"EXTERNAL_AGENTS": json.dumps([{"name": "r188-load", "url": recipient_url}]),
           "AGOS_TASK_QUEUE": "0",
           "AGOS_RATE_LIMIT_REQUESTS": "100000"}
    return loss._Server(env_extra=env)


def _post_task(port, prompt):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/tasks", method="POST",
        data=json.dumps({"specialization": "external", "prompt": prompt}).encode(),
        headers=loss.HEADERS)
    try:
        # the client's PATIENCE, not a server claim: on a fully saturated
        # 2-core box (external load ON TOP of this test's own ~1500 threads
        # in the test process) the admission response of a few of the 520
        # simultaneous POSTs legitimately took >30s — the timeout is the
        # harness's allowance for a starved box, and the assertions below
        # (exactly the cap accepted, everything answered) are unaffected.
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or b"{}")


def _post_task_raw(port, prompt, headers):
    """Like _post_task but returns (status, headers, body) — the refusal's
    Retry-After HEADER is part of the contract under test. The headers object
    is returned AS the library gives it (its .get is case-insensitive — uvicorn
    sends lowercase names and a plain dict would lose them)."""
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/tasks", method="POST",
        data=json.dumps({"specialization": "external", "prompt": prompt}).encode(),
        headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.headers, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.headers, json.loads(e.read().decode() or b"{}")


def _rows():
    return loss._row_rows()


def test_r188_volatile_real_cap_load_refusal_and_no_loss(fake_backend):
    """REQUIRED behavior (R-188 operational limits): the volatile queue's
    REAL cap (MAX_TASKS_KEPT = 500) under a real load — slow deterministic
    tasks hold the queue busy until the cap, the documented refusal fires
    through the real admission interface (503 + Retry-After at the global
    cap; the refused task leaves no row), and NOTHING accepted is lost:
    releasing the pressure completes every accepted task EXACTLY ONCE (one
    send per task, one result per task, every row terminal)."""
    st, url = _load_recipient("A")
    srv = _load_server(url)
    try:
        srv.start()
        accepted = []
        # ---- 1. FILL: every POST accepted (202) while the runs block
        for i in range(REAL_CAP):
            s, body = _post_task(srv.port, f"load {i}")
            assert s == 202, (s, body)
            accepted.append(body["task_id"])
        assert len(set(accepted)) == REAL_CAP  # 500 DISTINCT task ids
        # the queue is genuinely full — OBSERVABLY: every row that exists
        # is mid-flight and the set has settled (under saturation a few
        # running writes fail soft by design and self-heal at the terminal
        # write — see _wait_live_rows_settled; the FULL proof that all 500
        # are held is the admission assert above + the recipient count in
        # the no-loss phase below)
        ok, settled_rows = _wait_live_rows_settled(accepted)
        assert ok, "the live row set never settled"
        assert settled_rows <= REAL_CAP, \
            f"over-admission leaked into the rows: {settled_rows} live"
        # ---- 2. REFUSAL at the cap: 503 + Retry-After (the documented
        # service-busy answer, naming no tenant), and NO row is created
        s, hdrs, body = _post_task_raw(srv.port, "over the cap", loss.HEADERS)
        assert s == 503, (s, body)
        assert "retry later" in body["detail"]
        assert "tenant" not in body["detail"]
        assert int(hdrs.get("Retry-After")) == server.TASK_RETRY_AFTER_S
        # the refused task left no row (no record, no run, no send)
        over = [r for r in _rows() if r["prompt"] == "over the cap"]
        assert over == [], "a refused task left a row"
        # ---- 3. NO LOSS, NO DUPLICATES: release the pressure — every
        # accepted task completes EXACTLY once. The send count is by PROMPT
        # (unique per task): the volatile external send is keyless by contract
        # (a single-delivery path — no redelivery to deduplicate; the key
        # travels on the DURABLE path's redelivery-prone sends).
        st.release.set()
        assert _wait_all_completed(srv, st, accepted), \
            "an accepted task never completed after the pressure cleared"
        rows = {r["task_id"]: r for r in _rows()}
        for i, tid in enumerate(accepted):
            row = rows[tid]
            assert row["status"] == "completed"
            # exactly ONE send per accepted task (no duplicated effect)
            assert sum(1 for r in st.requests if r["prompt"] == f"load {i}") == 1, \
                f"task {tid} (load {i}) was sent to the recipient more than once"
        # no send ever happened for the refused prompt
        assert not any(r["prompt"] == "over the cap" for r in st.requests)
        # the total number of recipient requests == the accepted count
        assert len(st.requests) == REAL_CAP
        # ---- 4. RECOVERY (R-188: the gap this batch closes): the pressure
        # is gone and ADMISSION IS BACK — a NEW task is accepted (202), runs
        # once, completes once, and the live count returns to zero (the cap
        # did not leak a slot). Completing the OLD tasks alone proves nothing
        # about admission — this is the observed recovery, not an assumption.
        s, body = _post_task(srv.port, "after the pressure cleared")
        assert s == 202, (s, body)
        assert _wait_all_completed(srv, st, [body["task_id"]])
        assert sum(1 for r in st.requests
                   if r["prompt"] == "after the pressure cleared") == 1
        assert _wait_rows(lambda: not [
            r for r in _rows()
            if r["status"] not in ("completed", "failed", "cancelled")]), \
            "a live row leaked after the pressure cleared"
    finally:
        st.release.set()
        srv.stop()


def test_r188_volatile_per_tenant_429_and_isolation(fake_backend):
    """REQUIRED behavior (R-164 contract, through the REAL interface): the
    per-tenant cap refuses the next task of the SAME tenant with 429 +
    Retry-After (naming this tenant), another tenant is NOT blocked by the
    full tenant quota (isolation), and a refused task leaves no row."""
    st, url = _load_recipient("B")
    env = {"EXTERNAL_AGENTS": json.dumps([{"name": "r188-load", "url": url}]),
           "AGOS_TASK_QUEUE": "0",
           "AGOS_RATE_LIMIT_REQUESTS": "100000",
           "AGOS_TASK_QUEUE_MAX_PER_TENANT": "3",
           "AGOS_TENANT_KEYS": json.dumps([
               {"tenant": "r188_tenant",
                "key_sha256": hashlib.sha256(loss.RKEY.encode()).hexdigest(),
                "role": "user"},
               {"tenant": "r188_oplim_b",
                "key_sha256": hashlib.sha256(BKEY.encode()).hexdigest(),
                "role": "user"}])}
    srv = loss._Server(env_extra=env)
    try:
        srv.start()
        ok = [_post_task(srv.port, f"t {i}") for i in range(3)]
        assert all(s == 202 for s, _ in ok)
        s, hdrs, body = _post_task_raw(srv.port, "over tenant cap", loss.HEADERS)
        assert s == 429, (s, body)
        assert "this tenant" in body["detail"]
        assert int(hdrs.get("Retry-After")) == server.TASK_RETRY_AFTER_S
        # the refused task left no row
        assert not any(r["prompt"] == "over tenant cap" for r in _rows())
        # isolation: a SECOND tenant (its own key) is not blocked by the
        # first tenant's full quota
        s2, hdrs2, body2 = _post_task_raw(srv.port, "other tenant", HEADERS_B)
        assert s2 == 202, (s2, body2)
        # settle (bounded): release and let every accepted task complete
        st.release.set()
        assert _wait_rows(lambda: all(
            r["status"] == "completed" for r in _rows()
            if r["prompt"] in {"t 0", "t 1", "t 2", "other tenant"}))
    finally:
        st.release.set()
        srv.stop()


def test_r188_concurrent_admission_at_the_cap_is_atomic(fake_backend):
    """REQUIRED behavior (R-188 operational limits): CONCURRENT admission at
    the cap is atomic — N simultaneous POSTs crossing the cap accept EXACTLY
    cap tasks (never cap+1: the admission check is not a check-then-act
    race), the rest are refused with the documented code (503 + Retry-After
    at the global cap), no over-admission is observable in the rows, and
    nothing accepted is lost or duplicated once the pressure clears."""
    st, url = _load_recipient("C")
    srv = _load_server(url)
    try:
        srv.start()
        total = REAL_CAP + 20
        results = [None] * total
        barrier = threading.Barrier(total)

        def one(i):
            barrier.wait()
            results[i] = _post_task(srv.port, f"race {i}")

        threads = [threading.Thread(target=one, args=(i,)) for i in range(total)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)
        codes = [r[0] for r in results]
        assert all(c is not None for c in codes), "a concurrent POST never finished"
        # EXACTLY the cap accepted — the admission is atomic under the race
        assert codes.count(202) == REAL_CAP, \
            f"over/under admission: {codes.count(202)} accepted (cap {REAL_CAP})"
        assert set(codes) <= {202, 503}, sorted(set(codes))
        # the accepted set is distinct task ids
        tids = [r[1]["task_id"] for r in results if r[0] == 202]
        assert len(set(tids)) == REAL_CAP
        # never more than the cap in the rows (no over-admission leaked) —
        # SETTLE-based: under saturation a few ``running`` writes fail soft
        # by design and the row appears only at the terminal write (after
        # the release), so "exactly the cap live rows" is unsatisfiable
        # mid-flight (see _wait_live_rows_settled). The atomicity proof is
        # the codes assert above (exactly the cap accepted, never cap+1).
        ok, settled_rows = _wait_live_rows_settled(tids)
        assert ok, "the live row set never settled"
        assert settled_rows <= REAL_CAP, \
            f"over-admission leaked into the rows: {settled_rows} live"
        # ---- release: every accepted task completes exactly once (the send
        # count is by PROMPT — the volatile external send is keyless by
        # contract: a single-delivery path with no redelivery)
        st.release.set()
        assert _wait_rows(lambda: all(
            r["status"] == "completed"
            for r in _rows() if r["task_id"] in set(tids)))
        for i, tid in [(i, r[1]["task_id"]) for i, r in enumerate(results) if r[0] == 202]:
            assert sum(1 for r in st.requests if r["prompt"] == f"race {i}") == 1, \
                f"task {tid} (race {i}) was sent more than once"
        assert len(st.requests) == REAL_CAP
        # ---- RECOVERY: the pressure is gone and admission is BACK — new
        # tasks (a small concurrent burst, the race shape again) are all
        # accepted (202) and complete exactly once
        rec = [None] * 5
        barrier2 = threading.Barrier(5)

        def one_more(i):
            barrier2.wait()
            rec[i] = _post_task(srv.port, f"recovered {i}")

        threads = [threading.Thread(target=one_more, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)
        assert all(r[0] == 202 for r in rec), rec
        assert _wait_all_completed(srv, st, [r[1]["task_id"] for r in rec])
        for i in range(5):
            assert sum(1 for r in st.requests if r["prompt"] == f"recovered {i}") == 1
        assert _wait_rows(lambda: not [
            r for r in _rows()
            if r["status"] not in ("completed", "failed", "cancelled")]), \
            "a live row leaked after the pressure cleared"
    finally:
        st.release.set()
        srv.stop()


def _wait_live_rows_settled(ids, timeout=600):
    """Wait for the mid-flight row set of ``ids`` to SETTLE, and return
    (ok, settled_count). Every row that exists must be LIVE (non-terminal
    — the recipient holds guarantee nothing completes before the release)
    and the set must have stopped growing (stable for SETTLE_S).

    Why not "wait for ALL cap rows to be live": under a saturated box the
    intermediate ``running`` writes are NOT retried by design (they
    self-heal at the terminal write — the volatile mode's contract), so a
    few held tasks have NO row mid-flight: their row appears only at the
    terminal write, AFTER the release. Waiting for exactly the cap live
    rows is therefore unsatisfiable under saturation (reproduced: log 302
    round 3 — 200 CircuitOpen storage errors in the concurrent-fill
    server). The queue-FULL proof lives in the admission asserts (exactly
    the cap accepted, from the POST responses) and the post-release
    completion waits (by task id); this wait only proves the OBSERVABLE:
    the rows that exist are all mid-flight and none leaked past the cap."""
    SETTLE_S = 3.0
    state = {"n": -1, "since": None}

    def _settled():
        rows = [r for r in _rows() if r["task_id"] in set(ids)]
        if any(r["status"] in ("completed", "failed", "cancelled")
               for r in rows):
            return False      # nothing may complete before the release
        n = len(rows)
        now = time.time()
        if n > state["n"]:
            state["n"] = n
            state["since"] = now
            return False      # the set is still growing
        return state["since"] is not None and now - state["since"] >= SETTLE_S

    return _wait_rows(_settled, timeout=timeout), state["n"]


def _wait_all_completed(srv, st, ids, timeout=600):
    """Wait until EVERY accepted task's row EXISTS and is completed — with
    evidence on failure. Two hardening points over a plain "all completed"
    poll (both learned from the flaky full-suite runs this batch fixed):

    - a poll over ``rows if task_id in accepted`` PASSES on a MISSING row
      (it only checks the rows that exist) — so existence of the FULL
      accepted set is asserted here, not assumed;
    - a task that reached a terminal-FAILURE state can never complete —
      waiting out the clock on a real defect wastes the whole budget and
      hides the evidence, so the wait FAILS FAST (statuses + recipient
      count + server log tail) the moment an accepted task is failed or
      cancelled.

    On timeout the failure message carries the missing ids, the status
    histogram, the recipient request count and the server log tail — the
    failure is diagnosable from the run itself, not from a second
    reproduction. 600s is the harness's PATIENCE on a saturated 2-core box,
    not a server claim: under verified full external saturation (2 burners
    + this test's own ~1500 in-process threads on 2 cores) the 500
    recipient-held executions settle at only ~1.3-2/s (each ~1s recipient
    hold stretched to ~46s of starved wall time) — 476-483/500 complete
    within 360s and the rest settle by ~450-500s; a quiet box settles the
    same tests in 15-30s (the wait is a CEILING, not a floor — green paths
    return as soon as the predicate holds, so this costs nothing when the
    box is healthy). A real deadlock still fails fast via the dead-status
    check above."""
    ids = set(ids)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            rows = {r["task_id"]: r for r in _rows() if r["task_id"] in ids}
        except StopIteration:
            rows = {}
        dead = {t: r["status"] for t, r in rows.items()
                if r["status"] in ("failed", "cancelled")}
        if dead:
            raise AssertionError(
                f"accepted tasks reached terminal-FAILURE states (a real "
                f"defect, not a slow box): {dead}; recipient requests so "
                f"far: {len(st.requests)}; server log tail:\n{srv._tail()}")
        missing = ids - set(rows)
        if not missing and all(r["status"] == "completed" for r in rows.values()):
            return True
        time.sleep(0.1)
    try:
        rows = {r["task_id"]: r for r in _rows() if r["task_id"] in ids}
    except StopIteration:
        rows = {}
    missing = ids - set(rows)
    histogram = {}
    for r in rows.values():
        histogram[r["status"]] = histogram.get(r["status"], 0) + 1
    raise AssertionError(
        f"accepted tasks never completed within {timeout}s — {len(missing)} "
        f"rows MISSING; status histogram: {histogram}; recipient requests: "
        f"{len(st.requests)}; server log tail:\n{srv._tail()}")


def _wait_rows(pred, timeout=600):
    """Like loss._wait_for but against the fake PostgREST rows (the fake
    backend is in THIS process — the rows are directly inspectable).

    The timeout is a bounded INFRA allowance for settling a load test, not a
    measured production number and not the contract: the contract under test
    is "every accepted task completes exactly once once the pressure
    clears" — WHEN it completes depends on the box. A 60s allowance was
    observed to be insufficient twice, nondeterministically, inside the full
    suite on this 2-core runner (cause UNRESOLVED — see the diagnostics in
    _wait_all_completed, which now fail fast with evidence instead of
    waiting out the clock); standalone the same tests settle in 15-30s.
    600s keeps the assertions full-strength and the settle generous: under
    verified FULL external saturation (2 burners + this test's own ~1500
    in-process threads on 2 cores) the 500 recipient-held executions settle
    at only ~1.3-2/s (each ~1s recipient hold stretched to ~46s of starved
    wall time) — 474-483/500 were complete within 360s and the rest settled
    by ~450-500s, so 600s covers the saturated worst case too (a CEILING,
    not a floor — green paths return as soon as the predicate holds). This
    is NOT a production latency claim."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if pred():
                return True
        except StopIteration:
            pass
        time.sleep(0.25)
    return False
