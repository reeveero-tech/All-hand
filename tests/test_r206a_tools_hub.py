"""R-206a (D-165): tools hub registry, mirror decisions and /api/v2/tools."""
import importlib.util
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agos_v2 import tools_hub

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("mirror_upstreams", ROOT / "scripts/tools/mirror_upstreams.py")
mirror = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mirror)


def test_registry_has_the_four_tools_and_no_facecheck():
    ids = [t["id"] for t in tools_hub.load_registry()]
    assert ids == ["flights", "movies", "mdm", "robin"]
    raw = (ROOT / "agos_v2/tools_upstreams.json").read_text(encoding="utf-8").lower()
    assert "facecheck" not in raw  # D-165: rejected, never integrated


def test_mirrored_entries_carry_repo_and_license():
    m = {t["id"]: t for t in mirror.load_registry()}
    assert set(m) == {"mdm", "robin"}
    assert m["mdm"]["license"] == "GPL-3.0" and m["mdm"]["mirror_releases"] is True
    assert m["robin"]["license"] == "MIT" and m["robin"]["mirror_releases"] is False


def test_plan_first_run_snapshots_and_copies_release():
    e = {"id": "mdm", "mirror_releases": True}
    p = mirror.plan(e, {"state": "active", "head": "a" * 40, "latest_release": "v1"}, None)
    assert p == {"snapshot": True, "release": "v1"}


def test_plan_unchanged_head_and_known_release_does_nothing():
    e = {"id": "mdm", "mirror_releases": True}
    prev = {"snapshot_head": "a" * 40, "snapshot_at": "t", "mirrored_releases": ["v1"]}
    p = mirror.plan(e, {"state": "active", "head": "a" * 40, "latest_release": "v1"}, prev)
    assert p == {"snapshot": False, "release": None}


def test_plan_new_head_and_new_release():
    e = {"id": "mdm", "mirror_releases": True}
    prev = {"snapshot_head": "a" * 40, "snapshot_at": "t", "mirrored_releases": ["v1"]}
    p = mirror.plan(e, {"state": "active", "head": "b" * 40, "latest_release": "v2"}, prev)
    assert p == {"snapshot": True, "release": "v2"}


def test_plan_archived_upstream_still_copies_changes():
    e = {"id": "robin", "mirror_releases": False}
    p = mirror.plan(e, {"state": "archived", "head": "c" * 40, "latest_release": "v9"}, None)
    assert p == {"snapshot": True, "release": None}


def test_plan_gone_upstream_keeps_copy_and_touches_nothing():
    e = {"id": "robin", "mirror_releases": True}
    prev = {"snapshot_head": "a" * 40, "snapshot_at": "t"}
    assert mirror.plan(e, {"state": "gone", "head": None, "latest_release": None}, prev) == \
        {"snapshot": False, "release": None}


def _app(fetch, clock=lambda: 0.0):
    cache = tools_hub.StatusCache("https://example.invalid/status.json", fetch=fetch, ttl=600, clock=clock)
    app = FastAPI()
    app.include_router(tools_hub.build_router(cache=cache, repo="o/r"))
    return TestClient(app), cache


STATUS = {"generated_at": "2026-11-18T00:00:00+00:00", "tools": [
    {"id": "robin", "upstream_state": "archived", "snapshot_head": "d" * 40,
     "snapshot_at": "2026-11-18T00:00:00+00:00", "error": None},
    {"id": "mdm", "upstream_state": "active", "mirrored_releases": ["v0.5.2"], "error": None}]}


def test_api_merges_status_and_links():
    calls = []

    def fetch(url):
        calls.append(url)
        return json.dumps(STATUS).encode()

    client, _ = _app(fetch)
    body = client.get("/api/v2/tools").json()
    by = {t["id"]: t for t in body["tools"]}
    assert body["status"]["error"] is None
    assert by["robin"]["mirror"]["state"] == "copied"
    assert by["robin"]["mirror"]["upstream_state"] == "archived"
    assert by["robin"]["mirror"]["bundle_url"] == \
        "https://github.com/o/r/releases/download/mirror-robin/robin.bundle"
    assert by["mdm"]["mirror"]["state"] == "not_copied_yet"
    assert by["mdm"]["mirror"]["mirrored_releases"][0]["page"].endswith("/tag/mirror-mdm-v0.5.2")
    assert "mirror" not in by["flights"] and "mirror" not in by["movies"]
    client.get("/api/v2/tools")
    assert len(calls) == 1  # cached


def test_api_reports_status_failure_and_keeps_last_good_copy():
    t = [0.0]
    state = {"fail": False}

    def fetch(url):
        if state["fail"]:
            raise OSError("down")
        return json.dumps(STATUS).encode()

    client, _ = _app(fetch, clock=lambda: t[0])
    assert client.get("/api/v2/tools").json()["status"]["error"] is None
    state["fail"], t[0] = True, 10_000.0
    body = client.get("/api/v2/tools").json()
    assert body["status"]["error"].startswith("OSError")
    assert {x["id"]: x for x in body["tools"]}["robin"]["mirror"]["state"] == "copied"


def test_api_without_any_status_is_honest():
    def fetch(url):
        raise OSError("404")

    client, _ = _app(fetch)
    body = client.get("/api/v2/tools").json()
    assert body["status"]["error"]
    assert all(x["mirror"]["state"] == "not_copied_yet" for x in body["tools"] if "mirror" in x)


def test_bad_status_shape_is_an_error():
    client, cache = _app(lambda url: b'{"tools": "x"}')
    assert client.get("/api/v2/tools").json()["status"]["error"].startswith("ValueError")


def test_route_is_registered_on_the_real_app():
    import server  # noqa: WPS433
    assert "/api/v2/tools" in server.app.openapi()["paths"]


def test_workflow_is_scheduled_and_scoped():
    wf = (ROOT / ".github/workflows/tools-mirror.yml").read_text(encoding="utf-8")
    assert "schedule:" in wf and "workflow_dispatch:" in wf
    assert "contents: write" in wf
    assert "pull_request" not in wf  # never runs on PR code with a write token
