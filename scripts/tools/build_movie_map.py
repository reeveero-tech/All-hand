#!/usr/bin/env python3
"""R-206c (D-165): build our own movie map from Wikidata (CC0).

The owner asked for something like mikeyguinness.com/movies. That site's data
and code are not open, so we do not copy it (D-165): we build our own map from
Wikidata, whose data is CC0.

Pipeline (pure Python, no numpy — the project has no numeric dependency):
  1. fetch   — SPARQL: films (Q11424 and the film types in FILM_TYPES) that have
               an IMDb id, a publication date and at least ``--min-sitelinks``
               Wikipedia sitelinks (a popularity proxy); then, in batches,
               their labels (ar, en), genres, directors, cast, screenwriters,
               composers, countries and original languages.
  2. vectors — one sparse TF-IDF vector per film; feature weights by kind
               (director 3, screenwriter 2, cast 1, genre 1.5, composer 1,
               country 0.5, language 0.5, decade 0.75), L2-normalised.
  3. kNN     — the 20 most similar films by cosine, through an inverted index
               (features shared by too many films only score, they do not
               generate candidates — so "drama" alone never makes neighbours).
  4. islands — weighted label propagation on the kNN graph with a size
               penalty, small islands merged into their nearest large one; each
               island is named after its two most frequent genres.
  5. layout  — islands placed on a spiral by size (spaced by cumulative area),
               films start around their island centre and are pulled toward
               their same-island neighbours (deterministic, seeded).

Output: ``movies.json.gz`` (compact arrays) + ``build.json`` (counts, timings,
the SPARQL queries' sizes). The workflow ``movies-map.yml`` uploads both to the
release ``movies-map``; the server (``agos_v2/movies.py``) serves the file.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import random
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = "AGOS-All-hand-movie-map/1.0 (+https://github.com/reeveero-tech/All-hand)"
K = 20
WEIGHTS = {"d": 3.0, "w": 2.0, "c": 1.0, "g": 1.5, "m": 1.0, "n": 0.5, "l": 0.5, "y": 0.75}
PROPS = {"g": "P136", "d": "P57", "c": "P161", "w": "P58", "m": "P86", "n": "P495", "l": "P364"}
MAX_CANDIDATE_DF = 1500
BATCH = 300
FORMAT_VERSION = 1


# ---------------------------------------------------------------- fetch ----

def sparql(query: str, retries: int = 4, pause: float = 1.0) -> list[dict]:
    data = urllib.parse.urlencode({"query": query}).encode()
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(ENDPOINT, data=data, headers={
            "User-Agent": USER_AGENT, "Accept": "application/sparql-results+json",
            "Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:  # noqa: S310 - fixed https URL
                body = json.loads(resp.read().decode("utf-8"))
            time.sleep(pause)  # polite: one query at a time, a pause between them
            return body["results"]["bindings"]
        except Exception as exc:  # retried, then raised (never silently empty)
            last = exc
            time.sleep(pause * (2 ** attempt) * 3)
    raise RuntimeError(f"SPARQL failed after {retries} attempts: {type(last).__name__}: {last}")


def _qid(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


# Feature-length film types. Many well-known films are typed only as one of
# these (e.g. Spirited Away = anime film), not as "film" (Q11424) itself.
# Short films (Q24862, Q17517379) and film projects (Q18011172) stay out.
FILM_TYPES = ("Q11424",     # film
              "Q202866",    # animated film
              "Q20650540",  # anime film
              "Q29168811",  # animated feature film
              "Q506240")    # television film


def fetch_query(film_type: str, min_sitelinks: int, limit: int | None) -> str:
    # one query per type: a single VALUES query over all types hits the
    # Wikidata 60 s limit (HTTP 504, measured 2026-11-18)
    return f"""SELECT ?f ?imdb (MIN(?d) AS ?date) (MAX(?sl) AS ?links) WHERE {{
  ?f wdt:P31 wd:{film_type}; wdt:P345 ?imdb; wdt:P577 ?d; wikibase:sitelinks ?sl .
  FILTER(?sl >= {int(min_sitelinks)})
}} GROUP BY ?f ?imdb ORDER BY DESC(?links)""" + (f" LIMIT {int(limit)}" if limit else "")


def fetch_films(min_sitelinks: int, limit: int | None) -> dict[str, dict]:
    bindings = [b for t in FILM_TYPES for b in sparql(fetch_query(t, min_sitelinks, limit))]
    bindings.sort(key=lambda b: -int(b["links"]["value"]))
    films: dict[str, dict] = {}
    for b in bindings:
        qid = _qid(b["f"]["value"])
        if qid in films:
            continue
        if limit and len(films) >= int(limit):
            break
        year = b["date"]["value"][:5].lstrip("+")[:4]
        films[qid] = {"imdb": b["imdb"]["value"], "year": int(year) if year.isdigit() else None,
                      "links": int(b["links"]["value"]), "en": None, "ar": None,
                      "f": defaultdict(set)}
    return films


def fetch_details(films: dict[str, dict], log=print) -> dict[str, str]:
    """Fill labels and features in place; returns {genre qid: label}."""
    ids = list(films)
    genre_labels: dict[str, str] = {}
    for start in range(0, len(ids), BATCH):
        chunk = ids[start:start + BATCH]
        values = " ".join(f"wd:{q}" for q in chunk)
        for q, lab in labels(chunk).items():
            films[q]["en"], films[q]["ar"] = lab.get("en"), lab.get("ar")
        props = " ".join(f"(wdt:{p} '{k}')" for k, p in PROPS.items())
        for b in sparql(f"""SELECT ?f ?k ?v WHERE {{ VALUES ?f {{ {values} }}
  VALUES (?p ?k) {{ {props} }} ?f ?p ?v . FILTER(isIRI(?v)) }}"""):
            films[_qid(b["f"]["value"])]["f"][b["k"]["value"]].add(_qid(b["v"]["value"]))
        log(f"details {min(start + BATCH, len(ids))}/{len(ids)}")
    gids = sorted({g for f in films.values() for g in f["f"].get("g", ())})
    for start in range(0, len(gids), BATCH):
        for g, lab in labels(gids[start:start + BATCH]).items():
            genre_labels[g] = lab.get("ar") or lab.get("en") or g
    return genre_labels


def labels(qids: list[str]) -> dict[str, dict[str, str]]:
    """{qid: {"en": ..., "ar": ...}}; "mul" (Wikidata's language-neutral label,
    used by many items since 2025) fills a missing English label."""
    values = " ".join(f"wd:{q}" for q in qids)
    out: dict[str, dict[str, str]] = defaultdict(dict)
    for b in sparql(f"""SELECT ?f ?lang ?label WHERE {{ VALUES ?f {{ {values} }}
  ?f rdfs:label ?label . BIND(LANG(?label) AS ?lang) FILTER(?lang IN ("en", "ar", "mul")) }}"""):
        out[_qid(b["f"]["value"])][b["lang"]["value"]] = b["label"]["value"]
    for lab in out.values():
        if "en" not in lab and "mul" in lab:
            lab["en"] = lab["mul"]
        lab.pop("mul", None)
    return out


# ---------------------------------------------------------------- model ----

def feature_sets(films: dict[str, dict]) -> dict[str, set[str]]:
    out = {}
    for qid, f in films.items():
        s = {f"{k}:{v}" for k, vals in f["f"].items() for v in vals}
        if f.get("year"):
            s.add(f"y:{f['year'] // 10 * 10}")
        out[qid] = s
    return out


def vectors(feats: dict[str, set[str]]) -> tuple[dict[str, dict[str, float]], Counter]:
    n = len(feats)
    df = Counter(x for s in feats.values() for x in s)
    vecs = {}
    for qid, s in feats.items():
        v = {}
        for x in s:
            if df[x] < 2:
                continue  # a feature only this film has links it to nothing
            v[x] = WEIGHTS[x[0]] * math.log(1 + n / df[x])
        norm = math.sqrt(sum(w * w for w in v.values())) or 1.0
        vecs[qid] = {x: w / norm for x, w in v.items()}
    return vecs, df


def knn(vecs: dict[str, dict[str, float]], df: Counter, k: int = K) -> dict[str, list[tuple[str, float]]]:
    postings: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for qid, v in vecs.items():
        for x, w in v.items():
            postings[x].append((qid, w))
    out = {}
    for qid, v in vecs.items():
        acc: dict[str, float] = defaultdict(float)
        rare = [x for x in v if df[x] <= MAX_CANDIDATE_DF]
        for x in rare:
            w = v[x]
            for other, w2 in postings[x]:
                if other != qid:
                    acc[other] += w * w2
        common = [x for x in v if df[x] > MAX_CANDIDATE_DF]
        if acc and common:  # common features only score existing candidates
            for other in acc:
                ov = vecs[other]
                acc[other] += sum(v[x] * ov.get(x, 0.0) for x in common)
        best = sorted(acc.items(), key=lambda t: (-t[1], t[0]))[:k]
        out[qid] = [(o, round(s, 4)) for o, s in best if s > 0]
    return out


def islands(nn: dict[str, list[tuple[str, float]]], rounds: int = 12, seed: int = 7,
            min_size: int = 12, alpha: float = 0.5) -> dict[str, int]:
    """Weighted label propagation with a size penalty: a label's pull is
    divided by size**alpha, so one island cannot swallow the catalogue
    through popular hub films (without it, 9k films gave one 4k island)."""
    label = {q: i for i, q in enumerate(sorted(nn))}
    size = Counter(label.values())
    order = sorted(nn)
    rnd = random.Random(seed)
    for _ in range(rounds):
        rnd.shuffle(order)
        changed = 0
        for q in order:
            score: dict[int, float] = defaultdict(float)
            for o, s in nn[q]:
                score[label[o]] += s
            if not score:
                continue
            cur = label[q]
            best = max(score.items(), key=lambda t: (
                t[1] / ((size[t[0]] - (1 if t[0] == cur else 0) + 1) ** alpha), -t[0]))[0]
            if best != cur:
                size[cur] -= 1
                size[best] += 1
                label[q], changed = best, changed + 1
        if changed == 0:
            break
    label = merge_small(nn, label, min_size)
    remap = {lab: i for i, (lab, _) in enumerate(Counter(label.values()).most_common())}
    return {q: remap[lab] for q, lab in label.items()}


def merge_small(nn: dict[str, list[tuple[str, float]]], label: dict[str, int], min_size: int,
                passes: int = 6) -> dict[str, int]:
    """Label propagation leaves many tiny islands (a director's three films).
    Move each member of an island smaller than ``min_size`` to the large
    island its neighbours weigh most toward; repeat until stable."""
    label = dict(label)
    for _ in range(passes):
        size = Counter(label.values())
        moved = 0
        for q in sorted(label):
            if size[label[q]] >= min_size:
                continue
            score: dict[int, float] = defaultdict(float)
            for o, s in nn[q]:
                if size[label[o]] >= min_size:
                    score[label[o]] += s
            if score:
                best = max(score.items(), key=lambda t: (t[1], -t[0]))[0]
                size[label[q]] -= 1
                size[best] += 1
                label[q], moved = best, moved + 1
        if moved == 0:
            break
    return label


def layout(nn: dict[str, list[tuple[str, float]]], isl: dict[str, int], iters: int = 40,
           seed: int = 11) -> dict[str, tuple[float, float]]:
    """Islands on a golden-angle spiral, spaced by their cumulative area so
    they do not overlap; inside an island, films are pulled toward their
    same-island neighbours (cross-island links do not drag films away)."""
    rnd = random.Random(seed)
    sizes = Counter(isl.values())
    centres = {}
    cum = 0
    for rank, (i, size) in enumerate(sizes.most_common()):
        spread = 2.2 * math.sqrt(size)
        cum += size
        a = rank * 2.399963
        r = 0.0 if rank == 0 else 7.5 * math.sqrt(cum)
        centres[i] = (r * math.cos(a), r * math.sin(a), spread)
    pos = {}
    for q in sorted(nn):
        cx, cy, spread = centres[isl[q]]
        pos[q] = [cx + rnd.gauss(0, spread), cy + rnd.gauss(0, spread)]
    for _ in range(iters):
        new = {}
        for q, (x, y) in pos.items():
            sx = sy = sw = 0.0
            for o, s in nn[q]:
                if isl[o] != isl[q]:
                    continue
                ox, oy = pos[o]
                sx, sy, sw = sx + ox * s, sy + oy * s, sw + s
            if sw:
                x, y = 0.6 * x + 0.4 * sx / sw, 0.6 * y + 0.4 * sy / sw
            cx, cy, spread = centres[isl[q]]
            x += (cx - x) * 0.05 + rnd.gauss(0, 0.5)  # keep islands compact; jitter avoids collapse
            y += (cy - y) * 0.05 + rnd.gauss(0, 0.5)
            new[q] = [x, y]
        pos = new
    # smoothing shrinks islands; rescale each one around its centre back to its
    # target spread (keeps the inner arrangement, restores readable distances)
    members: dict[int, list[str]] = defaultdict(list)
    for q in pos:
        members[isl[q]].append(q)
    for i, qs in members.items():
        cx, cy, spread = centres[i]
        mx = sum(pos[q][0] for q in qs) / len(qs)
        my = sum(pos[q][1] for q in qs) / len(qs)
        sd = math.sqrt(sum((pos[q][0] - mx) ** 2 + (pos[q][1] - my) ** 2 for q in qs) / len(qs)) or 1.0
        k = spread / sd
        for q in qs:
            pos[q] = [cx + (pos[q][0] - mx) * k, cy + (pos[q][1] - my) * k]
    return {q: (round(x, 1), round(y, 1)) for q, (x, y) in pos.items()}


def build(films: dict[str, dict], genre_labels: dict[str, str]) -> dict:
    feats = feature_sets(films)
    vecs, df = vectors(feats)
    nn = knn(vecs, df)
    isl = islands(nn)
    pos = layout(nn, isl)
    order = sorted(films, key=lambda q: (-films[q]["links"], q))
    index = {q: i for i, q in enumerate(order)}
    genres = sorted({g for q in order for g in films[q]["f"].get("g", ())},
                    key=lambda g: (-df.get(f"g:{g}", 0), g))
    gidx = {g: i for i, g in enumerate(genres)}
    rows = []
    for q in order:
        f = films[q]
        x, y = pos[q]
        rows.append([q, f.get("ar") or "", f.get("en") or "", f.get("year"), f["imdb"],
                     sorted(gidx[g] for g in f["f"].get("g", ())), x, y, isl[q], f["links"]])
    neigh = [[[index[o], s] for o, s in nn[q]] for q in order]
    by_island: dict[int, list[str]] = defaultdict(list)
    for q in order:
        by_island[isl[q]].append(q)
    isl_rows = []
    for i, members in sorted(by_island.items()):
        gc = Counter(g for q in members for g in films[q]["f"].get("g", ()))
        top = [genre_labels.get(g, g) for g, _ in gc.most_common(2)]
        xs = [pos[q][0] for q in members]
        ys = [pos[q][1] for q in members]
        isl_rows.append({"id": i, "label": " / ".join(top) or "—", "size": len(members),
                         "x": round(sum(xs) / len(xs), 1), "y": round(sum(ys) / len(ys), 1)})
    return {
        "format": FORMAT_VERSION,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "Wikidata (https://www.wikidata.org), CC0 1.0",
        "license": "CC0-1.0",
        "fields": ["qid", "title_ar", "title_en", "year", "imdb", "genres", "x", "y", "island", "sitelinks"],
        "genres": [[g, genre_labels.get(g, g)] for g in genres],
        "films": rows, "neighbours": neigh, "islands": isl_rows,
    }


def save_raw(path: Path, films: dict[str, dict], genre_labels: dict[str, str]) -> None:
    out = {q: {**f, "f": {k: sorted(v) for k, v in f["f"].items()}} for q, f in films.items()}
    path.write_text(json.dumps({"films": out, "genres": genre_labels}, ensure_ascii=False), encoding="utf-8")


def load_raw(path: Path) -> tuple[dict[str, dict], dict[str, str]]:
    d = json.loads(path.read_text(encoding="utf-8"))
    films = {q: {**f, "f": defaultdict(set, {k: set(v) for k, v in f["f"].items()})}
             for q, f in d["films"].items()}
    return films, d["genres"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default="movie-map-out")
    ap.add_argument("--min-sitelinks", type=int, default=25)
    ap.add_argument("--limit", type=int, default=None, help="cap the film count (testing)")
    ap.add_argument("--save-raw", default=None, help="also write the fetched data (local tuning)")
    ap.add_argument("--from-raw", default=None, help="rebuild from a --save-raw file, no network")
    a = ap.parse_args(argv)
    t0 = time.time()
    if a.from_raw:
        films, genre_labels = load_raw(Path(a.from_raw))
        t1 = t2 = time.time()
    else:
        films = fetch_films(a.min_sitelinks, a.limit)
        t1 = time.time()
        print(f"films: {len(films)} ({t1 - t0:.1f}s)", flush=True)
        if len(films) < 50:
            print("too few films — refusing to publish a near-empty map", file=sys.stderr)
            return 1
        genre_labels = fetch_details(films, log=lambda m: print(m, flush=True))
        t2 = time.time()
        if a.save_raw:
            save_raw(Path(a.save_raw), films, genre_labels)
    data = build(films, genre_labels)
    t3 = time.time()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    (out / "movies.json.gz").write_bytes(gzip.compress(raw, 9, mtime=0))
    no_nn = sum(1 for n in data["neighbours"] if not n)
    report = {"generated_at": data["generated_at"], "films": len(data["films"]),
              "islands": len(data["islands"]), "genres": len(data["genres"]),
              "films_without_neighbours": no_nn, "json_bytes": len(raw),
              "gzip_bytes": (out / "movies.json.gz").stat().st_size,
              "seconds": {"fetch_films": round(t1 - t0, 1), "fetch_details": round(t2 - t1, 1),
                          "build": round(t3 - t2, 1)},
              "min_sitelinks": None if a.from_raw else a.min_sitelinks, "limit": a.limit,
              "from_raw": bool(a.from_raw)}
    (out / "build.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
