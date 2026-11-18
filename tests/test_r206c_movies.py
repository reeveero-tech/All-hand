"""R-206c (D-165): our own movie map — builder logic, the served route, the workflow.

The builder is tested on a small synthetic catalogue (no network): similar films
must be each other's neighbours, a feature shared by every film must not create
neighbours on its own, islands must group the families, and the output must
round-trip through the server's validator. The route is tested with an
in-process fetcher; the live build is evidence in the M10 log, not here.
"""
from __future__ import annotations

import asyncio
import gzip
import importlib.util
import json
from collections import defaultdict
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agos_v2 import movies

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_movie_map", ROOT / "scripts/tools/build_movie_map.py")
bmm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bmm)


def _film(year, links, en, **feats):
    f = defaultdict(set)
    for k, v in feats.items():
        f[k] |= set(v)
    return {"imdb": f"tt{sum(map(ord, en)):07d}", "year": year, "links": links,
            "en": en, "ar": None, "f": f}


def catalogue():
    films = {}
    # three families: a mafia trilogy (same director, cast), space films, cartoons
    for i in range(3):
        films[f"Q{100 + i}"] = _film(1972 + i * 2, 100 - i, f"Mafia {i}", d={"D1"}, c={"A1", "A2", f"A{3 + i}"},
                                     g={"G_crime", "G_drama"}, n={"US"})
    for i in range(3):
        films[f"Q{200 + i}"] = _film(1999 + i, 90 - i, f"Space {i}", d={"D2"}, c={"B1", "B2"},
                                     g={"G_scifi", "G_drama"}, n={"US"})
    for i in range(3):
        films[f"Q{300 + i}"] = _film(1995 + i * 3, 80 - i, f"Toon {i}", d={f"D3{i}"}, c={"C1", "C2"},
                                     g={"G_anim"}, m={"M1"}, n={"US"})
    return films


GENRES = {"G_crime": "جريمة", "G_drama": "دراما", "G_scifi": "خيال علمي", "G_anim": "رسوم متحركة"}


def test_neighbours_follow_shared_people_not_shared_country():
    films = catalogue()
    vecs, df = bmm.vectors(bmm.feature_sets(films))
    nn = bmm.knn(vecs, df, k=2)
    for fam in ("Q10", "Q20", "Q30"):
        for i in range(3):
            q = f"{fam}{i}"
            assert {o for o, _ in nn[q]} <= {f"{fam}{j}" for j in range(3)} - {q}, (q, nn[q])
    # "US" is in every film: it is dropped by IDF weighting (log(1 + n/df) still > 0 but tiny)
    assert all(s > 0 for v in nn.values() for _, s in v)


def test_feature_only_one_film_has_is_ignored():
    films = catalogue()
    vecs, _ = bmm.vectors(bmm.feature_sets(films))
    assert not any(x == "c:A3" for x in vecs["Q100"])  # A3 appears in Q100 only


def test_common_features_score_but_do_not_create_candidates(monkeypatch):
    monkeypatch.setattr(bmm, "MAX_CANDIDATE_DF", 1)
    films = {"Q1": _film(2000, 1, "a", g={"G"}), "Q2": _film(2001, 1, "b", g={"G"})}
    vecs, df = bmm.vectors(bmm.feature_sets(films))
    nn = bmm.knn(vecs, df)
    assert nn["Q1"] == [] and nn["Q2"] == []  # only shared feature is "common"


def test_islands_group_families_and_small_islands_merge():
    films = catalogue()
    vecs, df = bmm.vectors(bmm.feature_sets(films))
    nn = bmm.knn(vecs, df, k=2)
    isl = bmm.islands(nn, min_size=1)
    for fam in ("Q10", "Q20", "Q30"):
        assert len({isl[f"{fam}{i}"] for i in range(3)}) == 1
    merged = bmm.merge_small({"a": [("b", 1.0)], "b": [("c", 1.0)], "c": [("b", 1.0)]},
                             {"a": 0, "b": 1, "c": 1}, min_size=2)
    assert merged == {"a": 1, "b": 1, "c": 1}


def test_build_output_validates_and_is_deterministic():
    d1 = bmm.build(catalogue(), GENRES)
    d2 = bmm.build(catalogue(), GENRES)
    d1.pop("generated_at"), d2.pop("generated_at")
    assert d1 == d2
    d1["generated_at"] = "x"
    gz = gzip.compress(json.dumps(d1).encode())
    s = movies.validate(gz)
    assert s["films"] == 9 and s["license"] == "CC0-1.0"
    assert d1["fields"] == movies.FIELDS
    assert [r[0] for r in d1["films"]][:1] == ["Q100"]  # most sitelinks first
    labels = {i["label"] for i in d1["islands"]}
    assert any("جريمة" in x or "دراما" in x for x in labels)


@pytest.mark.parametrize("mutate, msg", [
    (lambda d: d.update(format=99), "format"),
    (lambda d: d.update(fields=["x"]), "fields"),
    (lambda d: d["neighbours"].pop(), "different length"),
    (lambda d: d["neighbours"][0].append([999, 0.1]), "out of range"),
    (lambda d: d.update(films=[], neighbours=[]), "empty"),
])
def test_validate_rejects_bad_maps(mutate, msg):
    d = bmm.build(catalogue(), GENRES)
    mutate(d)
    with pytest.raises(ValueError, match=msg):
        movies.validate(gzip.compress(json.dumps(d).encode()))


def _client(fetch):
    cache = movies.MapCache("https://example.invalid/movies.json.gz", fetch=fetch)
    app = FastAPI()
    app.include_router(movies.build_router(cache=cache))
    return TestClient(app), cache


def test_route_serves_gzip_as_is_and_plain_when_not_accepted():
    gz = gzip.compress(json.dumps(bmm.build(catalogue(), GENRES)).encode())
    c, cache = _client(lambda url: gz)
    r = c.get("/api/v2/movies/map", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200 and r.headers["content-encoding"] == "gzip"
    assert len(r.json()["films"]) == 9  # httpx decodes it
    etag = r.headers["etag"]
    assert c.get("/api/v2/movies/map", headers={"If-None-Match": etag}).status_code == 304
    r2 = c.get("/api/v2/movies/map", headers={"Accept-Encoding": "identity"})
    assert r2.status_code == 200 and "content-encoding" not in r2.headers and len(r2.json()["films"]) == 9
    st = c.get("/api/v2/movies/status").json()
    assert st["ok"] and st["map"]["films"] == 9 and st["error"] is None


def test_route_reports_failure_not_empty():
    def boom(url):
        raise OSError("HTTP 404")
    c, _ = _client(boom)
    r = c.get("/api/v2/movies/map")
    assert r.status_code == 503 and "HTTP 404" in r.json()["detail"]
    assert r.headers["retry-after"] == "120"
    assert c.get("/api/v2/movies/status").json()["ok"] is False


def test_bad_download_keeps_last_good_copy_and_says_stale():
    good = gzip.compress(json.dumps(bmm.build(catalogue(), GENRES)).encode())
    calls = {"n": 0}

    def fetch(url):
        calls["n"] += 1
        return good if calls["n"] == 1 else b"not gzip"
    t = {"now": 0.0}
    cache = movies.MapCache("u", fetch=fetch, ttl=10, clock=lambda: t["now"])
    assert asyncio.run(cache.get()) == good
    t["now"] = 11
    assert asyncio.run(cache.get()) == good and cache.error
    app = FastAPI()
    app.include_router(movies.build_router(cache=cache))
    r = TestClient(app).get("/api/v2/movies/map")
    assert r.status_code == 200 and "x-map-stale" in r.headers


def test_map_url_and_registry_page():
    assert movies.map_url("o/r") == "https://github.com/o/r/releases/download/movies-map/movies.json.gz"
    reg = json.loads((ROOT / "agos_v2/tools_upstreams.json").read_text(encoding="utf-8"))["tools"]
    assert next(t for t in reg if t["id"] == "movies")["page"] == "movies"


def test_workflow_is_scheduled_publishes_and_never_runs_on_pr():
    wf = (ROOT / ".github/workflows/movies-map.yml").read_text(encoding="utf-8")
    assert "schedule:" in wf and "workflow_dispatch:" in wf
    assert "pull_request" not in wf
    assert "build_movie_map.py" in wf and "gh release upload movies-map" in wf
    assert "contents: write" in wf


def test_server_registers_movies_routes():
    src = (ROOT / "server.py").read_text(encoding="utf-8")
    assert "from agos_v2.movies import build_router as _build_v2_movies" in src
    assert "app.include_router(_build_v2_movies(rate_limiter=_v2_rate_limit))" in src
