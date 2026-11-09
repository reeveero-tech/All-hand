"""R-177 (D-144): in-harness resource sampler and leak indicators.

Regression: in the log-390 soak an external sidecar wrote fds=0 for every
sample (``ls /proc/<pid>/fd 2>/dev/null | wc -l`` turned a read failure into
0). An unreadable fd directory must be ``None`` with a reason, never 0.
"""
import importlib.util
import os
import pathlib
import subprocess
import sys
import time

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
LINUX_PROC = pathlib.Path("/proc/self/status").exists()


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, mod)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def rs():
    return _load("r177_resource_sampler", "scripts/r177_resource_sampler.py")


@pytest.fixture(scope="module")
def ps():
    return _load("r177_per_service_d144", "scripts/r177_per_service.py")


# ------------------------------------------------------------ fd counting

def test_unreadable_fd_dir_is_none_not_zero(rs, tmp_path):
    """The D-144 regression: a failed read is None + reason, never 0."""
    fake = tmp_path / "4242"
    fake.mkdir()  # no fd/ subdirectory ⇒ FileNotFoundError
    got = rs.count_fds(4242, proc_root=str(tmp_path))
    assert got["fds"] is None
    assert got["fds"] != 0
    assert "FileNotFoundError" in got["fds_error"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses directory permissions")
def test_permission_denied_fd_dir_is_none_not_zero(rs, tmp_path):
    d = tmp_path / "77" / "fd"
    d.mkdir(parents=True)
    (d / "0").write_text("x")
    d.chmod(0)
    try:
        got = rs.count_fds(77, proc_root=str(tmp_path))
    finally:
        d.chmod(0o755)
    assert got["fds"] is None and "PermissionError" in got["fds_error"]


def test_empty_readable_fd_dir_is_a_real_zero(rs, tmp_path):
    (tmp_path / "5" / "fd").mkdir(parents=True)
    assert rs.count_fds(5, proc_root=str(tmp_path)) == {"fds": 0, "fds_error": ""}


@pytest.mark.skipif(not LINUX_PROC, reason="needs Linux /proc")
def test_child_process_fds_are_counted_and_positive(rs):
    """The harness is the SUT's parent: it must read a child's fd table."""
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        time.sleep(0.3)
        s = rs.read_sample(child.pid)
        assert s["fds"] is not None and s["fds"] >= 3, s
        assert s["fds_error"] == ""
        assert s["rss_kb"] and s["rss_kb"] > 0
        assert s["threads"] and s["threads"] >= 1
        assert s["utime_ticks"] is not None
    finally:
        child.kill()
        child.wait()


@pytest.mark.skipif(not LINUX_PROC, reason="needs Linux /proc")
def test_fd_growth_in_child_is_visible(rs):
    code = ("import os,sys,time\n"
            "fs=[]\n"
            "for line in sys.stdin:\n"
            "    fs += [os.open(os.devnull, os.O_RDONLY) for _ in range(int(line))]\n"
            "    print(len(fs), flush=True)\n")
    child = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, text=True)
    try:
        child.stdin.write("1\n")
        child.stdin.flush()
        child.stdout.readline()
        before = rs.count_fds(child.pid)["fds"]
        child.stdin.write("25\n")
        child.stdin.flush()
        child.stdout.readline()
        after = rs.count_fds(child.pid)["fds"]
        assert after - before == 25
    finally:
        child.kill()
        child.wait()


# ------------------------------------------------------------ leak indicators

def _rows(fn, n=200, step=30.0):
    return [{"t_s": i * step, "pid": 1, "rss_kb": fn(i), "pss_kb": fn(i),
             "private_dirty_kb": fn(i), "anonymous_kb": fn(i), "threads": 20,
             "fds": 40, "fds_error": ""} for i in range(n)]


def test_steady_series_is_not_significant_growth(rs):
    li = rs.leak_indicators(_rows(lambda i: 80000 + (7 if i % 2 else -7)))
    rss = li["series"]["rss_kb"]
    assert li["status"] == "measured" and rss["status"] == "measured"
    assert rss["growth_significant"] is False
    assert rss["slope_per_h_ci95"][0] <= 0 <= rss["slope_per_h_ci95"][1]
    assert li["series"]["fds"]["growth_significant"] is False


def test_linear_growth_is_detected_with_correct_slope(rs):
    # +10 kB per 30 s sample = 1200 kB/h
    li = rs.leak_indicators(_rows(lambda i: 80000 + 10 * i + (3 if i % 3 else -3)))
    rss = li["series"]["rss_kb"]
    assert rss["growth_significant"] is True
    assert abs(rss["slope_per_h"] - 1200) < 20
    assert rss["slope_per_h_ci95"][0] > 0


def test_warmup_growth_is_excluded(rs):
    # steep growth only in the first 25% (warm-up), then flat
    li = rs.leak_indicators(_rows(lambda i: 50000 + min(i, 40) * 500 + (5 if i % 2 else -5)))
    assert li["warmup_cut_s"] >= 0.25 * 199 * 30 - 1
    assert li["series"]["rss_kb"]["growth_significant"] is False


def test_unmeasured_fds_are_not_measured_not_zero(rs):
    rows = _rows(lambda i: 80000)
    for r in rows:
        r["fds"], r["fds_error"] = None, "PermissionError: Permission denied"
    li = rs.leak_indicators(rows)
    fds = li["series"]["fds"]
    assert fds["status"] == "not_measured"
    assert "growth_significant" not in fds
    assert fds["errors"] == ["PermissionError: Permission denied"]


def test_no_samples_is_not_measured(rs):
    assert rs.leak_indicators([])["status"] == "not_measured"


def test_indicator_never_claims_no_leak(rs):
    li = rs.leak_indicators(_rows(lambda i: 80000))
    assert "No approved leak threshold" in li["note"]
    assert "verdict" not in li and "leak_free" not in li


def test_pid_changes_are_reported(rs):
    rows = _rows(lambda i: 80000)
    for r in rows[100:]:
        r["pid"] = 2
    li = rs.leak_indicators(rows)
    assert li["pids"] == [1, 2] and li["pid_changes"] == 1


# ------------------------------------------------------------ live sampler

@pytest.mark.skipif(not LINUX_PROC, reason="needs Linux /proc")
def test_sampler_follows_pid_and_writes_csv(rs, tmp_path):
    procs = [subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])]
    csv_path = tmp_path / "res.csv"
    smp = rs.ResourceSampler(lambda: procs[-1].pid, interval_s=0.1, csv_path=csv_path).start()
    try:
        time.sleep(0.6)
        procs.append(subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"]))
        time.sleep(0.6)
    finally:
        res = smp.stop()
        for p in procs:
            p.kill()
            p.wait()
    li = res["leak_indicators"]
    assert li["pid_changes"] == 1
    lines = csv_path.read_text().splitlines()
    assert lines[0].startswith("utc,ts,t_s,pid,rss_kb")
    assert len(lines) >= 6
    fd_col = lines[0].split(",").index("fds")
    assert all(int(row.split(",")[fd_col]) >= 3 for row in lines[1:])


def test_sampler_skips_dead_pid_without_fake_rows(rs):
    smp = rs.ResourceSampler(lambda: None, interval_s=0.05).start()
    time.sleep(0.2)
    res = smp.stop()
    assert smp.rows == [] and res["leak_indicators"]["status"] == "not_measured"


# ------------------------------------------------------------ harness wiring

def test_main_passes_service_to_failure_recovery(ps, monkeypatch):
    seen = {}

    def fake(scenario, **kw):
        seen.update(kw, scenario=scenario)
        return {"ok": True}

    monkeypatch.setattr(ps, "run_failure_recovery", fake)
    monkeypatch.setattr(sys, "argv", ["x", "--failure-recovery", "crash",
                                      "--service", "api-health"])
    assert ps.main() == 0
    assert seen["service"] == "api-health" and seen["scenario"] == "crash"


def test_main_failure_recovery_default_service_unchanged(ps, monkeypatch):
    seen = {}
    monkeypatch.setattr(ps, "run_failure_recovery",
                        lambda scenario, **kw: seen.update(kw) or {"ok": True})
    monkeypatch.setattr(sys, "argv", ["x", "--failure-recovery", "crash"])
    assert ps.main() == 0
    assert seen["service"] == "api-tasks-submit"


def test_sut_pid_provider(ps):
    sut = ps.SutProcess.__new__(ps.SutProcess)
    sut.proc = None
    assert sut.pid() is None


def test_run_service_signature_accepts_sampler(ps):
    import inspect
    params = inspect.signature(ps.run_service).parameters
    assert "sample_pid" in params and params["sample_pid"].default is None


def test_res_brief_handles_unmeasured(ps):
    assert ps._res_brief(None)["status"] == "not_measured"
    assert ps._res_brief({"status": "not_measured"})["status"] == "not_measured"


# ------------------------------------------------- non-dumpable root cause (D-144)

_NONDUMP_CHILD = r"""
import ctypes, sys, time
sys.path.insert(0, sys.argv[1])
import r177_resource_sampler as rs
libc = ctypes.CDLL(None, use_errno=True)
libc.prctl(4, 0, 0, 0, 0)            # PR_SET_DUMPABLE=0, as harden_server_process
assert libc.prctl(3, 0, 0, 0, 0) == 0
rs.start_inprocess(sys.argv[2], 0.1)
print("ready", flush=True)
time.sleep(30)
"""


@pytest.mark.skipif(not LINUX_PROC or os.geteuid() == 0,
                    reason="needs Linux /proc and a non-root user")
def test_nondumpable_process_external_none_inprocess_measured(rs, tmp_path):
    """Root cause of fds=0: outside reads fail on a non-dumpable process
    (recorded as None + PermissionError); its own in-process reads work."""
    csv_path = tmp_path / "inproc.csv"
    child = subprocess.Popen([sys.executable, "-c", _NONDUMP_CHILD,
                              str(REPO / "scripts"), str(csv_path)],
                             stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "ready"
        time.sleep(0.5)
        ext = rs.read_sample(child.pid)
        assert ext["fds"] is None and "PermissionError" in ext["fds_error"]
        assert ext["pss_kb"] is None          # smaps_rollup is unreadable too
        assert ext["rss_kb"] and ext["rss_kb"] > 0  # status stays readable
        rows = rs.load_rows(csv_path, 0, time.time() + 1)
        assert len(rows) >= 2
        assert all(r["fds"] >= 3 and r["fds_error"] == "" for r in rows)
        assert all(r["pss_kb"] and r["pss_kb"] > 0 for r in rows)
    finally:
        child.kill()
        child.wait()


def test_load_rows_windows_and_parses(rs, tmp_path):
    p = tmp_path / "r.csv"
    for ts in (100.0, 105.0, 110.0, 200.0):
        rs._append_csv(p, {"ts": ts, "pid": 9, "rss_kb": 1, "fds": None,
                           "fds_error": "PermissionError: x"})
    rows = rs.load_rows(p, 104.0, 150.0)
    assert [r["t_s"] for r in rows] == [1.0, 6.0]
    assert rows[0]["fds"] is None and rows[0]["pss_kb"] is None
    assert rs.load_rows(tmp_path / "missing.csv", 0, 1) == []


@pytest.mark.skipif(not LINUX_PROC, reason="needs Linux /proc")
def test_launcher_sut_in_process_fds_measured(ps, tmp_path):
    """The real SUT (server.py, hardened) through the launcher: fds/PSS measured."""
    csv_path = tmp_path / "sut.csv"
    with ps.sut_process(res_csv=csv_path, res_interval_s=0.2) as sut:
        t0 = time.time()
        time.sleep(1.0)
        ext = ps.rsamp().read_sample(sut.pid())
        rows = ps.rsamp().load_rows(csv_path, t0, time.time())
        cmd = pathlib.Path(f"/proc/{sut.pid()}/cmdline").read_bytes().split(b"\0")
    assert any(b"r177_sut_launcher.py" in c for c in cmd)
    assert b"server:app" in cmd
    assert rows, "no in-process samples"
    assert all(r["fds"] >= 5 for r in rows)   # sockets, epoll, std streams, sqlite
    assert all(r["pss_kb"] > 0 for r in rows)
    # the hardening is still active: the parent cannot read fds
    assert ext["fds"] is None and "PermissionError" in ext["fds_error"]


def test_default_sut_command_unchanged(ps):
    sut = ps.SutProcess()
    assert sut.res_csv is None and "R177_RES_CSV" not in sut.env
