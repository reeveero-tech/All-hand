"""R-206b (D-165, D-166): the flights proxy.

These tests prove the proxy's own logic with an injected fetcher: input checks,
provider order and fallback, honest failures, the shared throttle and cache, and
the route's status codes. They are NOT evidence that adsb.lol/adsb.fi answer —
that is shown by scripts/tools/flights_live_check.py (outside pytest; the R-072
network guard blocks the suite) and by the deployed /api/v2/flights, both logged
in docs/audit/M10/logs/raw/.
"""
import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agos_v2 import flights as fl

AC = {"ac": [{"hex": "4007f5", "flight": "BAW6MC  ", "r": "G-YMMJ", "t": "B772", "lat": 51.4, "lon": -0.4,
              "alt_baro": 11200, "gs": 306.2, "track": 271.9, "dbFlags": 0, "noise": "dropped"},
             {"hex": "ae1234", "flight": "RCH1", "dbFlags": 1, "lat": 1, "lon": 2},
             {"flight": "nohex"}]}


class Fake:
    def __init__(self, answers):
        self.answers, self.urls = answers, []

    def __call__(self, url):
        self.urls.append(url)
        for prefix, ans in self.answers.items():
            if url.startswith(prefix):
                if isinstance(ans, Exception):
                    raise ans
                return ans
        raise AssertionError(f"unexpected url {url}")


class Clock:
    def __init__(self):
        self.t, self.slept = 100.0, []

    def __call__(self):
        return self.t

    async def sleep(self, s):
        self.slept.append(round(s, 3))
        self.t += s


def client(answers, **kw):
    clk = Clock()
    fake = Fake(answers)
    return fl.FlightsClient(fetch=fake, clock=clk, sleep=clk.sleep, **kw), fake, clk


def run(coro):
    return asyncio.run(coro)


LOL, FI = "https://api.adsb.lol", "https://opendata.adsb.fi/api"
OK = (200, json.dumps(AC).encode())


@pytest.mark.parametrize("args, msg", [
    (("nope", None, None, None, None), "mode must be"),
    (("point", None, None, 3.0, None), "needs lat and lon"),
    (("point", None, 91.0, 0.0, None), "lat must be"),
    (("point", None, 0.0, 0.0, 251), "radius must be"),
    (("point", None, 0.0, 0.0, 0), "radius must be"),
    (("hex", "zzzzzz", None, None, None), "invalid hex"),
    (("callsign", "BAW 1", None, None, None), "invalid callsign"),
    (("callsign", "../mil", None, None, None), "invalid callsign"),
    (("squawk", "7800", None, None, None), "invalid squawk"),
    (("reg", "", None, None, None), "invalid reg"),
])
def test_bad_input_rejected_before_upstream(args, msg):
    with pytest.raises(fl.FlightQueryError, match=msg):
        fl.parse_query(*args)


def test_query_normalised():
    assert fl.parse_query("point", None, 51.4712, -0.4543, None) == ("point", {"lat": 51.47, "lon": -0.45, "radius": 50})
    assert fl.parse_query("HEX", " 4007F5 ", None, None, None) == ("hex", {"q": "4007f5"})
    assert fl.parse_query("callsign", "baw6mc", None, None, None) == ("callsign", {"q": "BAW6MC"})
    assert fl.parse_query("mil", None, None, None, None) == ("mil", {})


def test_primary_used_and_fields_trimmed():
    c, fake, _ = client({LOL: OK})
    r = run(c.query("point", {"lat": 51.47, "lon": -0.45, "radius": 40}))
    assert fake.urls == [LOL + "/v2/point/51.47/-0.45/40"]
    assert r["ok"] and r["provider"] == "adsb.lol" and not r["fallback_used"] and r["errors"] == []
    assert r["count"] == 2  # the row without hex is dropped
    a = r["aircraft"][0]
    assert a["flight"] == "BAW6MC" and "noise" not in a and a["military"] is False
    assert r["aircraft"][1]["military"] is True
    assert r["attribution"]["license"] == "ODbL 1.0" and r["attribution"]["home"] == "https://adsb.lol"


def test_fallback_to_adsb_fi_with_its_own_paths_and_attribution():
    c, fake, _ = client({LOL: (403, b'{"error":"blocked"}'), FI: OK})
    r = run(c.query("reg", {"q": "G-YMMJ"}))
    assert fake.urls == [LOL + "/v2/reg/G-YMMJ", FI + "/v2/registration/G-YMMJ"]
    assert r["ok"] and r["provider"] == "adsb.fi" and r["fallback_used"]
    assert r["errors"][0]["provider"] == "adsb.lol" and "HTTP 403" in r["errors"][0]["error"]
    assert "non-commercial" in r["attribution"]["license"]
    c2, fake2, _ = client({LOL: TimeoutError("timed out"), FI: OK})
    r2 = run(c2.query("point", {"lat": 1.0, "lon": 2.0, "radius": 30}))
    assert fake2.urls[1] == FI + "/v3/lat/1.0/lon/2.0/dist/30" and r2["provider"] == "adsb.fi"


def test_mode_only_on_primary_is_reported_when_primary_fails():
    c, fake, _ = client({LOL: (500, b"")})
    r = run(c.query("type", {"q": "A388"}))
    assert not r["ok"] and len(fake.urls) == 1
    assert [e["provider"] for e in r["errors"]] == ["adsb.lol", "adsb.fi"]
    assert "not offered" in r["errors"][1]["error"]


def test_both_fail_is_an_error_not_an_empty_list():
    c, _, _ = client({LOL: (502, b""), FI: (200, b"<html>")})
    r = run(c.query("mil", {}))
    assert r["ok"] is False and "aircraft" not in r and len(r["errors"]) == 2


def test_cache_then_shared_one_per_second_throttle():
    c, fake, clk = client({LOL: OK})
    p = {"lat": 1.0, "lon": 2.0, "radius": 30}
    assert run(c.query("point", p))["cached"] is False
    assert run(c.query("point", p))["cached"] is True
    assert len(fake.urls) == 1 and clk.slept == []
    run(c.query("mil", {}))  # different key, same provider, same instant -> waits 1 s
    assert clk.slept == [1.0] and c.upstream_calls["adsb.lol"] == 2
    clk.t += fl.CACHE_TTL_S + 1
    assert run(c.query("point", p))["cached"] is False  # expired


def test_busy_guard():
    c, fake, _ = client({LOL: OK})
    c._waiting = fl.MAX_WAITERS
    r = run(c.query("mil", {}))
    assert r.get("busy") and fake.urls == []


def _app(answers):
    c, fake, _ = client(answers)
    app = FastAPI()
    app.include_router(fl.build_router(client=c))
    return TestClient(app), fake


def test_route_status_codes():
    tc, fake = _app({LOL: OK})
    r = tc.get("/api/v2/flights", params={"mode": "point", "lat": 51.47, "lon": -0.45, "radius": 40})
    assert r.status_code == 200 and r.json()["count"] == 2
    assert tc.get("/api/v2/flights", params={"mode": "callsign", "q": "a/b"}).status_code == 400
    assert tc.get("/api/v2/flights", params={"mode": "point", "lat": "x", "lon": 1}).status_code == 422
    assert len(fake.urls) == 1  # rejected input never reached upstream
    tc2, _ = _app({LOL: (500, b""), FI: (500, b"")})
    r2 = tc2.get("/api/v2/flights", params={"mode": "mil"})
    assert r2.status_code == 502 and r2.json()["ok"] is False and len(r2.json()["errors"]) == 2


def test_route_registered_on_the_real_app_and_public():
    import server
    tc = TestClient(server.app)
    # the real app answers with the proxy's own validation (no auth, no upstream call)
    r = tc.get("/api/v2/flights", params={"mode": "nope"})
    assert r.status_code == 400 and "mode must be" in r.json()["detail"]


def test_registry_points_the_hub_at_the_flights_page():
    from agos_v2 import tools_hub
    f = {t["id"]: t for t in tools_hub.load_registry()}["flights"]
    assert f["page"] == "flights" and f["source"] == "https://adsb.lol"
    assert "airplanes.live" not in f["source"] and "D-166" in f["data_terms"]
    merged = {t["id"]: t for t in tools_hub.merge(tools_hub.load_registry(), None, "o/r")}
    assert merged["flights"]["page"] == "flights" and merged["movies"]["page"] is None
