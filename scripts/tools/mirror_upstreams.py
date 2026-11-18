#!/usr/bin/env python3
"""R-206a (D-165): keep full copies of the open-source tools' upstreams.

For every registry entry with a ``repo`` (agos_v2/tools_upstreams.json):
  * read the upstream state (archived? gone? default-branch head, latest release);
  * when the head changed since the last run (or no copy exists yet): clone with
    full history, write ``<id>.bundle`` (``git bundle --all`` — every branch and
    tag, restorable with ``git clone <id>.bundle``) and ``<id>-src.tar.gz``
    (``git archive`` of the head), and upload both to the release
    ``mirror-<id>`` in THIS repository (assets replaced, the release stays);
  * when ``mirror_releases`` is true and the upstream published a release tag
    not copied yet: copy all its assets to the release ``mirror-<id>-<tag>``
    (kept forever; one release per upstream tag);
  * write ``status.json`` to the release ``mirror-status`` (read by the server).

An upstream that is archived or deleted is never an error: the previous copy
stays and its state is reported (``archived`` / ``gone``). A failed copy is
reported in status.json and makes the run exit 1 — it is never hidden.

Usage (CI): GH_TOKEN=... python3 scripts/tools/mirror_upstreams.py --repo OWNER/REPO
Pure decision logic (``plan``) is unit-tested in tests/test_r206a_tools_hub.py.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "agos_v2" / "tools_upstreams.json"
STATUS_TAG = "mirror-status"


def load_registry(path: Path = REGISTRY) -> list[dict]:
    return [t for t in json.loads(path.read_text(encoding="utf-8"))["tools"] if t.get("repo")]


def plan(entry: dict, upstream: dict, prev: dict | None) -> dict:
    """Decide what to copy for one entry. ``upstream`` = {"state", "head",
    "latest_release"}; ``prev`` = this entry's record from the last status.json.
    Returns {"snapshot": bool, "release": tag-or-None}."""
    prev = prev or {}
    if upstream.get("state") == "gone":
        return {"snapshot": False, "release": None}
    snapshot = bool(upstream.get("head")) and (
        upstream.get("head") != prev.get("snapshot_head") or not prev.get("snapshot_at"))
    tag = upstream.get("latest_release")
    release = None
    if entry.get("mirror_releases") and tag and tag not in (prev.get("mirrored_releases") or []):
        release = tag
    return {"snapshot": snapshot, "release": release}


def _run(*cmd: str, cwd: str | None = None, capture: bool = True) -> str:
    r = subprocess.run(list(cmd), cwd=cwd, capture_output=capture, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{cmd[0]} {cmd[1] if len(cmd) > 1 else ''} failed: "
                           f"{(r.stderr or '').strip()[:300]}")
    return r.stdout if capture else ""


def _gh_json(path: str):
    r = subprocess.run(["gh", "api", path], capture_output=True, text=True)
    if r.returncode != 0:
        if "Not Found" in r.stdout + r.stderr or "HTTP 404" in r.stderr:
            return None
        raise RuntimeError(f"gh api {path}: {r.stderr.strip()[:300]}")
    return json.loads(r.stdout)


def read_upstream(repo: str) -> dict:
    meta = _gh_json(f"repos/{repo}")
    if meta is None:
        return {"state": "gone", "head": None, "latest_release": None}
    branch = meta.get("default_branch") or "main"
    commit = _gh_json(f"repos/{repo}/commits/{branch}") or {}
    rel = _gh_json(f"repos/{repo}/releases/latest") or {}
    return {"state": "archived" if meta.get("archived") else "active",
            "head": commit.get("sha"), "branch": branch,
            "pushed_at": meta.get("pushed_at"),
            "latest_release": rel.get("tag_name"),
            "description": meta.get("description")}


def _ensure_release(self_repo: str, tag: str, title: str, notes: str) -> None:
    r = subprocess.run(["gh", "release", "view", tag, "-R", self_repo], capture_output=True, text=True)
    if r.returncode != 0:
        _run("gh", "release", "create", tag, "-R", self_repo, "--title", title,
             "--notes", notes, "--latest=false")


def snapshot(self_repo: str, entry: dict, work: Path) -> dict:
    url = f"https://github.com/{entry['repo']}.git"
    mirror = work / f"{entry['id']}.git"
    _run("git", "clone", "--quiet", "--bare", url, str(mirror))
    bundle = work / f"{entry['id']}.bundle"
    _run("git", "-C", str(mirror), "bundle", "create", str(bundle), "--all")
    head = _run("git", "-C", str(mirror), "rev-parse", "HEAD").strip()
    tarball = work / f"{entry['id']}-src.tar.gz"
    _run("git", "-C", str(mirror), "archive", "--format=tar.gz",
         f"--prefix={entry['id']}/", "-o", str(tarball), "HEAD")
    tag = f"mirror-{entry['id']}"
    _ensure_release(self_repo, tag, f"Mirror: {entry['name']}",
                    f"Full copy of https://github.com/{entry['repo']} ({entry.get('license')}). "
                    "Assets are replaced on each upstream change (D-165).")
    _run("gh", "release", "upload", tag, "-R", self_repo, "--clobber", str(bundle), str(tarball))
    shutil.rmtree(mirror, ignore_errors=True)
    return {"snapshot_head": head, "bundle_bytes": bundle.stat().st_size}


def copy_release(self_repo: str, entry: dict, tag: str, work: Path) -> None:
    d = work / f"rel-{entry['id']}"
    d.mkdir(exist_ok=True)
    _run("gh", "release", "download", tag, "-R", entry["repo"], "-D", str(d), "--clobber")
    files = sorted(str(p) for p in d.iterdir() if p.is_file())
    mtag = f"mirror-{entry['id']}-{tag}"
    _ensure_release(self_repo, mtag, f"Mirror: {entry['name']} {tag}",
                    f"Copy of the assets of https://github.com/{entry['repo']}/releases/tag/{tag} "
                    f"({entry.get('license')}); kept even if the upstream release is removed (D-165).")
    if files:
        _run("gh", "release", "upload", mtag, "-R", self_repo, "--clobber", *files)
    shutil.rmtree(d, ignore_errors=True)


def read_prev_status(self_repo: str, work: Path) -> dict:
    r = subprocess.run(["gh", "release", "download", STATUS_TAG, "-R", self_repo, "-p", "status.json",
                        "-D", str(work), "--clobber"], capture_output=True, text=True)
    p = work / "status.json"
    if r.returncode != 0 or not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    ap.add_argument("--only", default="")
    a = ap.parse_args(argv)
    if not a.repo:
        print("--repo or GITHUB_REPOSITORY is required", file=sys.stderr)
        return 2
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    failed = False
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        prev_all = read_prev_status(a.repo, work)
        prev_tools = {t["id"]: t for t in prev_all.get("tools", [])}
        out = []
        for entry in load_registry():
            if a.only and entry["id"] != a.only:
                if entry["id"] in prev_tools:
                    out.append(prev_tools[entry["id"]])
                continue
            rec = dict(prev_tools.get(entry["id"], {}))
            rec.update({"id": entry["id"], "repo": entry["repo"], "license": entry.get("license"),
                        "checked_at": now, "error": None})
            try:
                up = read_upstream(entry["repo"])
                rec.update({"upstream_state": up["state"], "upstream_head": up.get("head"),
                            "upstream_pushed_at": up.get("pushed_at"),
                            "upstream_release": up.get("latest_release")})
                p = plan(entry, up, prev_tools.get(entry["id"]))
                if p["snapshot"]:
                    rec.update(snapshot(a.repo, entry, work))
                    rec["snapshot_at"] = now
                if p["release"]:
                    copy_release(a.repo, entry, p["release"], work)
                    rec["mirrored_releases"] = sorted(set(rec.get("mirrored_releases") or []) | {p["release"]})
                print(f"{entry['id']}: upstream={up['state']} snapshot={'new' if p['snapshot'] else 'unchanged'}"
                      f" release={p['release'] or '-'}")
            except Exception as exc:  # reported, never hidden
                failed = True
                rec["error"] = str(exc)[:400]
                print(f"{entry['id']}: ERROR {rec['error']}", file=sys.stderr)
            out.append(rec)
        status = {"generated_at": now, "repo": a.repo, "tools": out}
        sp = work / "status.json"
        sp.write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
        _ensure_release(a.repo, STATUS_TAG, "Mirror status",
                        "Machine-written state of the tools mirrors (D-165). Read by /api/v2/tools.")
        _run("gh", "release", "upload", STATUS_TAG, "-R", a.repo, "--clobber", str(sp))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
