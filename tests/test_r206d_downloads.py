"""R-206d (D-165): Mister Download Manager downloads from our verified copies.

In-process tests with fake fetchers and fake gh — NOT live evidence (the live
evidence is the tools-mirror run and the production check, log M10/04x).
Proves: the upstream SHA256SUMS parser; the manifest marks each file
verified / mismatched / not listed and is not ok on any mismatch or missing
file; verify_copy hashes OUR copy, prefers the upstream sums, falls back to the
copy's sums when the upstream release is gone, uploads the manifest and fails
on a mismatch; the mirror backfills releases copied before R-206d; the route
serves only mirror_releases tools, picks the newest tag, groups by platform,
links only to our releases, and reports missing / failed verification.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agos_v2 import downloads, tools_hub

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("mirror_upstreams", ROOT / "scripts/tools/mirror_upstreams.py")
mu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mu)

ENTRY = {"id": "mdm", "name": "Mister Download Manager", "repo": "anefzaoui/misterdownloadmanager",
         "license": "GPL-3.0", "mirror_releases": True}
REAL_NAMES = [  # the 24 assets of upstream v0.5.2 (gh release view, 2026-11-18)
    "countries.txt", "flags.png", "ipcountry.bin.xz", "LICENSE-flag-icons",
    "MisterDownloadManager-0.5.2-chrome-web-store.zip", "MisterDownloadManager-0.5.2-extension-chromium.zip",
    "MisterDownloadManager-0.5.2-extension-firefox.xpi", "MisterDownloadManager-0.5.2-linux-x86_64.AppImage",
    "MisterDownloadManager-0.5.2-linux-x86_64.AppImage.zsync", "MisterDownloadManager-0.5.2-linux-x86_64.deb",
    "MisterDownloadManager-0.5.2-linux-x86_64.rpm", "MisterDownloadManager-0.5.2-linux-x86_64.tar.gz",
    "MisterDownloadManager-0.5.2-macos-arm64-experimental.dmg",
    "MisterDownloadManager-0.5.2-macos-x86_64-experimental.dmg", "MisterDownloadManager-0.5.2-portable.zip",
    "MisterDownloadManager-0.5.2-setup.exe", "MisterDownloadManager-0.5.2-symbols-linux-x86_64.zip",
    "MisterDownloadManager-0.5.2-symbols-macos-arm64.zip", "MisterDownloadManager-0.5.2-symbols-macos-x86_64.zip",
    "MisterDownloadManager-0.5.2-symbols-windows-x64.zip", "MisterDownloadManager-0.5.2-x64.zip",
    "SHA256SUMS.txt", "SHA256SUMS.txt.sig", "SOURCE.txt"]


def h(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ------------------------------------------------------------ mirror side ----

def test_parse_sums_text_and_binary_mode_and_paths():
    a, b = "a" * 64, "B" * 64
    got = mu.parse_sums(f"{a}  setup.exe\n{b} *dist/app.deb\n\nnot a line\n{'z' * 64}  bad.hex\n")
    assert got == {"setup.exe": a, "app.deb": "b" * 64}


def test_manifest_marks_verified_mismatched_unlisted_and_missing():
    files = {"setup.exe": (3, h(b"exe")), "app.deb": (3, h(b"deb")), "flags.png": (3, h(b"png"))}
    sums = {"setup.exe": h(b"exe"), "app.deb": h(b"DIFFERENT")}
    m = mu.build_manifest(ENTRY, "v1", files, sums, "upstream:x", "t")
    by = {a["name"]: a for a in m["assets"]}
    assert by["setup.exe"]["verified"] is True and by["app.deb"]["verified"] is False
    assert by["flags.png"]["verified"] is None and by["flags.png"]["upstream_sha256"] is None
    assert m["mismatched"] == ["app.deb"] and m["ok"] is False
    ok = mu.build_manifest(ENTRY, "v1", files, {"setup.exe": h(b"exe")}, "s", "t")
    assert ok["ok"] is True and ok["missing"] == []
    gone = mu.build_manifest(ENTRY, "v1", files, {"setup.exe": h(b"exe"), "x.rpm": "c" * 64}, "s", "t")
    assert gone["missing"] == ["x.rpm"] and gone["ok"] is False   # a listed file we did not copy
    assert mu.build_manifest(ENTRY, "v1", files, None, None, "t")["ok"] is False   # no sums = not verified


def test_to_verify_backfills_releases_copied_before_r206d():
    assert mu.to_verify({"mirrored_releases": ["v0.5.2"]}) == ["v0.5.2"]
    assert mu.to_verify({"mirrored_releases": ["v0.5.2", "v0.6"], "verified_releases": ["v0.5.2"]}) == ["v0.6"]
    assert mu.to_verify(None) == [] and mu.to_verify({}) == []


class FakeGh:
    """gh release download/upload on dicts: ours[tag] / upstream[tag] = {name: bytes}."""

    def __init__(self, ours, upstream):
        self.ours, self.upstream, self.uploads = ours, upstream, []

    def run(self, cmd, capture_output=True, text=True, **kw):
        assert cmd[:3] == ["gh", "release", "download"] or cmd[:3] == ["gh", "release", "upload"]
        repo = cmd[cmd.index("-R") + 1]
        if cmd[2] == "upload":
            self.uploads.append((cmd[3], [Path(p).name for p in cmd[cmd.index("--clobber") + 1:]],
                                 {Path(p).name: Path(p).read_text() for p in cmd[cmd.index("--clobber") + 1:]}))
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        tag, d = cmd[3], Path(cmd[cmd.index("-D") + 1])
        src = (self.ours if repo == "me/repo" else self.upstream).get(tag)
        if src is None:
            return type("R", (), {"returncode": 1, "stdout": "", "stderr": "release not found"})()
        pat = cmd[cmd.index("-p") + 1] if "-p" in cmd else None
        for name, data in src.items():
            if pat is None or pat == name:
                (d / name).write_bytes(data)
        if pat is not None and pat not in src:
            return type("R", (), {"returncode": 1, "stdout": "", "stderr": "no asset"})()
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()


@pytest.fixture
def gh(monkeypatch):
    def make(ours, upstream):
        g = FakeGh(ours, upstream)
        monkeypatch.setattr(mu.subprocess, "run", g.run)
        monkeypatch.setattr(mu, "_run", lambda *cmd, **kw: (g.run(list(cmd)), "")[1])
        return g
    return make


def test_verify_copy_hashes_our_copy_against_upstream_sums_and_uploads_manifest(gh, tmp_path):
    files = {"setup.exe": b"exe", "app.deb": b"deb"}
    sums = f"{h(b'exe')}  setup.exe\n{h(b'deb')}  app.deb\n"
    g = gh({"mirror-mdm-v1": {**files, "SHA256SUMS.txt": sums.encode()}},
           {"v1": {**files, "SHA256SUMS.txt": sums.encode()}})
    m = mu.verify_copy("me/repo", ENTRY, "v1", tmp_path, "2026-11-18T00:00:00Z")
    assert m["ok"] and m["sums_source"] == "upstream:anefzaoui/misterdownloadmanager@v1/SHA256SUMS.txt"
    tag, names, content = g.uploads[-1]
    assert tag == "mirror-mdm-v1" and names == ["manifest.json"]
    up = json.loads(content["manifest.json"])
    assert {a["name"]: a["verified"] for a in up["assets"]} == {"SHA256SUMS.txt": None, "app.deb": True,
                                                                   "setup.exe": True}


def test_verify_copy_detects_a_changed_file_in_our_copy_and_still_uploads_the_manifest(gh, tmp_path):
    sums = f"{h(b'exe')}  setup.exe\n".encode()
    g = gh({"mirror-mdm-v1": {"setup.exe": b"TAMPERED", "SHA256SUMS.txt": sums}},
           {"v1": {"setup.exe": b"exe", "SHA256SUMS.txt": sums}})
    with pytest.raises(RuntimeError, match="checksum verification failed"):
        mu.verify_copy("me/repo", ENTRY, "v1", tmp_path, "t")
    up = json.loads(g.uploads[-1][2]["manifest.json"])
    assert up["ok"] is False and up["mismatched"] == ["setup.exe"]


def test_verify_copy_uses_the_copys_sums_when_upstream_release_is_gone(gh, tmp_path):
    sums = f"{h(b'exe')}  setup.exe\n".encode()
    gh({"mirror-mdm-v1": {"setup.exe": b"exe", "SHA256SUMS.txt": sums}}, {})
    m = mu.verify_copy("me/repo", ENTRY, "v1", tmp_path, "t")
    assert m["ok"] and m["sums_source"].startswith("copy:mirror-mdm-v1/SHA256SUMS.txt")


def test_verify_copy_ignores_an_older_manifest_in_the_copy(gh, tmp_path):
    sums = f"{h(b'exe')}  setup.exe\n".encode()
    gh({"mirror-mdm-v1": {"setup.exe": b"exe", "SHA256SUMS.txt": sums, "manifest.json": b"{}"}},
       {"v1": {"SHA256SUMS.txt": sums}})
    m = mu.verify_copy("me/repo", ENTRY, "v1", tmp_path, "t")
    assert "manifest.json" not in {a["name"] for a in m["assets"]}


# ------------------------------------------------------------ server side ----

def test_every_real_asset_name_lands_in_the_expected_group():
    g = {n: downloads.group_of(n) for n in REAL_NAMES}
    assert g["MisterDownloadManager-0.5.2-setup.exe"] == "windows"
    assert g["MisterDownloadManager-0.5.2-x64.zip"] == "windows"
    assert g["MisterDownloadManager-0.5.2-portable.zip"] == "windows"
    assert g["MisterDownloadManager-0.5.2-macos-arm64-experimental.dmg"] == "macos"
    for n in ("AppImage", "AppImage.zsync", "deb", "rpm", "tar.gz"):
        assert g[f"MisterDownloadManager-0.5.2-linux-x86_64.{n}"] == "linux"
    for n in ("chrome-web-store.zip", "extension-chromium.zip", "extension-firefox.xpi"):
        assert g[f"MisterDownloadManager-0.5.2-{n}"] == "extensions"
    assert all(g[n] == "symbols" for n in REAL_NAMES if "-symbols-" in n)
    assert g["SHA256SUMS.txt"] == g["SHA256SUMS.txt.sig"] == "checksums"
    assert {g[n] for n in ("countries.txt", "flags.png", "ipcountry.bin.xz", "LICENSE-flag-icons",
                           "SOURCE.txt")} == {"other"}


def test_version_key_orders_numerically():
    assert sorted(["v0.9.1", "v0.10.0", "v0.5.2"], key=downloads.version_key, reverse=True)[0] == "v0.10.0"


@pytest.mark.parametrize("bad", [
    [], {"format": 2}, {"format": 1, "id": "robin", "tag": "v1", "assets": []},
    {"format": 1, "id": "mdm", "tag": "v1", "assets": [{"name": "../x", "sha256": "a" * 64}]},
    {"format": 1, "id": "mdm", "tag": "v1", "assets": [{"name": "x", "sha256": "short"}]}])
def test_validate_manifest_rejects_malformed(bad):
    with pytest.raises(ValueError):
        downloads.validate_manifest(bad, "mdm", "v1")


def _status(releases, verified=()):
    return json.dumps({"tools": [{"id": "mdm", "upstream_state": "active", "mirrored_releases": list(releases),
                                  "verified_releases": list(verified), "snapshot_head": "abc"}]}).encode()


def _manifest(tag, names, bad=()):
    assets = [{"name": n, "size": 10, "sha256": h(n.encode()),
               "upstream_sha256": h(n.encode()) if n not in bad else "f" * 64,
               "verified": n not in bad} for n in names]
    return json.dumps({"format": 1, "id": "mdm", "tag": tag, "verified_at": "t", "sums_source": "upstream:x",
                       "ok": not bad, "mismatched": list(bad), "missing": [], "assets": assets}).encode()


def _client(status_bytes, manifests):
    calls = []

    def mfetch(url):
        calls.append(url)
        if url not in manifests:
            raise OSError("HTTP Error 404: Not Found")
        return manifests[url]
    reg = tools_hub.load_registry()
    app = FastAPI()
    app.include_router(downloads.build_router(
        status_cache=tools_hub.StatusCache("s", fetch=lambda u: status_bytes),
        manifests=downloads.ManifestCache(fetch=mfetch), repo="me/repo", registry=reg))
    return TestClient(app), calls


MURL = "https://github.com/me/repo/releases/download/mirror-mdm-{}/manifest.json"


def test_route_serves_newest_release_grouped_from_our_copy_only():
    names = ["MisterDownloadManager-0.10.0-setup.exe", "MisterDownloadManager-0.10.0-linux-x86_64.deb",
             "SHA256SUMS.txt"]
    c, calls = _client(_status(["v0.9.1", "v0.10.0"], ["v0.10.0"]),
                       {MURL.format("v0.10.0"): _manifest("v0.10.0", names)})
    r = c.get("/api/v2/downloads/mdm")
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] and d["release"]["tag"] == "v0.10.0" and calls == [MURL.format("v0.10.0")]
    assert d["verification"]["state"] == "verified" and d["license"] == "GPL-3.0"
    assert d["license_url"] == "https://www.gnu.org/licenses/gpl-3.0.html"
    assert [g["id"] for g in d["groups"]] == ["windows", "linux", "checksums"]
    urls = [f["url"] for g in d["groups"] for f in g["files"]]
    assert all(u.startswith("https://github.com/me/repo/releases/download/mirror-mdm-v0.10.0/") for u in urls)
    assert not any("anefzaoui" in u for u in urls)
    assert [x["tag"] for x in d["releases"]] == ["v0.10.0", "v0.9.1"]
    assert d["releases"][1]["verified"] is False


def test_route_reports_a_mismatch():
    c, _ = _client(_status(["v1"]), {MURL.format("v1"): _manifest("v1", ["a-setup.exe"], bad=["a-setup.exe"])})
    d = c.get("/api/v2/downloads/mdm").json()
    assert d["verification"]["state"] == "failed" and d["verification"]["mismatched"] == ["a-setup.exe"]
    assert d["groups"][0]["files"][0]["verified"] is False


def test_route_without_manifest_says_not_verified_yet_with_the_reason():
    c, _ = _client(_status(["v0.5.2"]), {})
    r = c.get("/api/v2/downloads/mdm")
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is False and d["verification"]["state"] == "not_verified_yet"
    assert "404" in d["verification"]["error"] and d["groups"] == []
    assert d["release"]["mirror_page"] == "https://github.com/me/repo/releases/tag/mirror-mdm-v0.5.2"


def test_route_503_when_nothing_copied_and_404_for_tools_without_downloads():
    c, _ = _client(_status([]), {})
    r = c.get("/api/v2/downloads/mdm")
    assert r.status_code == 503 and r.json()["error"] == "no release copied yet"
    assert c.get("/api/v2/downloads/robin").status_code == 404   # robin: mirror_releases false
    assert c.get("/api/v2/downloads/facecheck").status_code == 404


def test_manifest_cache_keeps_last_good_copy_and_retries_later():
    t = [0.0]
    good = _manifest("v1", ["x.exe"])
    answers = [good, OSError("down")]

    def fetch(url):
        a = answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a
    mc = downloads.ManifestCache(fetch=fetch, ttl=10, clock=lambda: t[0])
    d, err = asyncio.run(mc.get("u", "mdm", "v1"))
    assert d and err is None
    t[0] = 11
    d2, err2 = asyncio.run(mc.get("u", "mdm", "v1"))
    assert d2 == d and "down" in err2
    t[0] = 12
    assert asyncio.run(mc.get("u", "mdm", "v1"))[1] == err2   # no refetch before the retry delay


def test_registry_points_the_hub_at_the_downloads_page():
    m = {t["id"]: t for t in tools_hub.load_registry()}["mdm"]
    assert m["page"] == "downloads" and m["mirror_releases"] is True and m["license"] == "GPL-3.0"
