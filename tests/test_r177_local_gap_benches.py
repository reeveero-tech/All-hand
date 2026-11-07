"""R-177 local gaps (D-136): the tool-family and SSE fan-out benches stay runnable
and honest. These are smoke/contract tests — the measurements themselves are in
docs/audit/M8/logs/raw/373_* and 374_* (no stress, Q-023)."""
from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
RAW = REPO / "docs" / "audit" / "M8" / "logs" / "raw" / "r177_b3"
EXTERNAL = {"api_integrator", "communicator", "educator", "network_client", "researcher"}


def _load(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "benchmarks" / "r177" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_tool_families_bench_dispatches_every_non_network_tool_through_the_gate(tmp_path):
    out = tmp_path / "tools.json"
    r = subprocess.run([sys.executable, str(REPO / "benchmarks" / "r177" / "tool_families_bench.py"),
                        "--n", "2", "--concurrency", "2", "--json", str(out)],
                       capture_output=True, text=True, timeout=600, cwd=REPO)
    assert r.returncode == 0, r.stderr[-2000:]
    rep = json.loads(out.read_text())
    assert rep["registered"] == 100
    assert rep["benched"] == 95
    assert {s["name"] if isinstance(s, dict) else s for s in rep["skipped"]} >= EXTERNAL or len(rep["skipped"]) == 5
    after = rep["gate_stats_after"]
    for k in ("refused", "timed_out", "net_blocked", "malformed"):
        if k in after:
            assert after[k] == rep["gate_stats_before"].get(k, 0), (k, after)


def test_recorded_tool_families_evidence_matches_the_claims():
    rep = json.loads((RAW / "tool_families_bench.json").read_text())
    assert rep["benched"] == 95 and rep["n_per_tool"] == 200
    assert sum(f.get("tools", 0) for f in rep["families"].values()) in (0, 95)


def test_sse_bench_percentile_and_loopback_helpers():
    m = _load("sse_fanout_bench")
    assert m._pct([], 95) is None
    assert m._pct(list(range(1, 101)), 50) == 50
    assert m._pct(list(range(1, 101)), 95) == 95
    assert m._loopback_ok("127.0.0.1") is True


def test_recorded_sse_evidence_has_zero_leaks_and_sufficient_samples():
    for name in ("sse_fanout_volatile.json", "sse_fanout_live_local.json"):
        rep = json.loads((RAW / name).read_text())
        assert rep["cross_tenant_leaks"] == 0, name
        assert rep["stream_gap_events"] == 0, name
        assert rep["incomplete_streams"] == [], name
        assert rep["caps"]["6th_stream_same_ip"]["status"] == 429
        assert rep["caps"]["11th_stream_same_tenant_new_ip"]["status"] == 429
        assert rep["delivery_latency_ms"]["task_created"]["n"] >= 299, name  # D-134 p99 sample floor


def test_nearest_rank_percentile_is_exact_in_every_bench_tool():
    """Red-first (log 375): `int(round(p/100*n + 0.5)) - 1` uses banker's rounding, so
    p95 of 1..100 was 96 and p95 of n=20 was the max. Nearest-rank = ceil(p/100*n)."""
    sec10 = importlib.util.spec_from_file_location("ready_perf", REPO / "benchmarks" / "sec10" / "ready_perf.py")
    rp = importlib.util.module_from_spec(sec10)
    sec10.loader.exec_module(rp)
    for fn in (_load("sse_fanout_bench")._pct, _load("tool_families_bench")._pct, rp._pct):
        assert fn(list(range(1, 101)), 95) == 95
        assert fn(list(range(1, 101)), 99) == 99
        assert fn(list(range(1, 101)), 50) == 50
        assert fn(list(range(1, 21)), 95) == 19
        assert fn(list(range(1, 1501)), 95) == 1425
        assert fn([7.0], 99) == 7.0
