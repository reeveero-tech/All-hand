"""Q-023 (D-140): the environment probe measures and classifies HONESTLY.

The verdict rules are the point: a single machine never qualifies for the
three-separate-nodes spec whatever its size; a 2 vCPU / 8 GB box is
``reduced_R177_only`` (never closes R-178/R-179); smaller is not_qualified.
The end-to-end run uses --quick (bounded seconds, no load test).
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("q023_env_probe", ROOT / "scripts" / "q023_env_probe.py")
P = importlib.util.module_from_spec(spec)
sys.modules["q023_env_probe"] = P
spec.loader.exec_module(P)


def _res(vcpu, ram, quota=None, mem_limit=None):
    return {"cpu_count": vcpu, "cpu_affinity": vcpu, "cgroup_cpu_quota": quota,
            "ram_total_gib": ram, "cgroup_mem_limit_gib": mem_limit}


def test_single_huge_machine_never_qualifies_full_spec():
    c = P.classify(_res(64, 256))
    assert c["verdict"] == "reduced_R177_only"
    assert any("separate_nodes" in g for g in c["gaps"])
    assert c["closes"] == {"R-177_full": False, "R-178": False, "R-179": False}


def test_three_big_nodes_qualify_full_spec():
    c = P.classify(_res(16, 32), nodes=3)
    assert c["verdict"] == "qualified_full_spec" and c["gaps"] == []
    assert c["closes"]["R-178"] is True


def test_three_nodes_too_small_for_sut_do_not_qualify():
    c = P.classify(_res(8, 32), nodes=3)          # meets generator/storage, not SUT
    assert c["verdict"] != "qualified_full_spec"
    assert c["role_minimum_met"]["sut"] is False


def test_github_runner_shape_is_reduced_only():
    c = P.classify(_res(4, 15.6))                 # ubuntu-latest public: 4 vCPU / 16 GB
    assert c["verdict"] == "reduced_R177_only"


def test_tiny_machine_not_qualified():
    assert P.classify(_res(1, 5))["verdict"] == "not_qualified"


def test_cgroup_quota_and_limit_win_over_host_numbers():
    res = _res(32, 128, quota=2.0, mem_limit=4.0)  # container on a big host
    assert P.effective_vcpu(res) == 2.0 and P.effective_ram_gib(res) == 4.0
    assert P.classify(res)["verdict"] == "not_qualified"


def test_nearest_rank_matches_p6_rule():
    vals = [float(i) for i in range(1, 101)]
    assert P.nearest_rank(vals, 0.95) == 95.0
    assert P.nearest_rank(vals, 0.99) == 99.0
    assert P.nearest_rank([], 0.95) is None


def test_quick_run_end_to_end(tmp_path):
    out = tmp_path / "p.json"
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "q023_env_probe.py"),
                        "--label", "pytest", "--json", str(out), "--quick"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    d = json.loads(out.read_text())
    assert d["resources"]["cpu_count"] >= 1
    assert d["loopback_http"]["errors"] == 0 and d["loopback_http"]["p95_ms"] is not None
    assert d["loopback_http"]["sequential_single_client"] is True
    assert d["parallel"]["effective_cores"] > 0
    assert d["classification"]["closes"]["R-178"] is False   # one machine, never
    assert "not_a_load_test" in d
