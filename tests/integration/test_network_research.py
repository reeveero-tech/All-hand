"""NETWORK integration tests — real Crossref, arXiv and public pages. Not run in CI.

Run on purpose:  RUN_NETWORK_TESTS=1 pytest -m network tests/integration
They depend on third-party services and can fail for reasons outside this code (rate limits, outages).
"""
from __future__ import annotations

import asyncio
import os

import pytest

from app import tools
from app.research import manager, specialized

pytestmark = [pytest.mark.network,
              pytest.mark.skipif(os.environ.get("RUN_NETWORK_TESTS") != "1", reason="network tests: set RUN_NETWORK_TESTS=1")]


@pytest.fixture(autouse=True)
def _real(monkeypatch):
    monkeypatch.setattr(manager, "ENABLED", True)
    monkeypatch.setattr(tools, "_tavily", getattr(tools, "_real_tavily", tools._tavily))


def test_crossref_live_and_auto_open():
    res = asyncio.run(specialized.crossref("coffee consumption Saudi Arabia"))
    assert res and res[0]["url"].startswith("https://doi.org/")
    m = manager.ResearchManager("لخص الدراسات العلمية عن استهلاك القهوة في السعودية",
                                budget=manager.Budget(max_searches=4, max_opens=4, max_rounds=1, max_seconds=120))
    st = asyncio.run(m.run())
    assert st["readings"] or st.get("failed")  # every attempt recorded, read or with its reason


def test_arxiv_live_pdf():
    res = asyncio.run(specialized.arxiv("coffee roasting"))
    assert res and res[0]["fulltext"]
    rd = asyncio.run(tools.read_url(res[0]["fulltext"][0]))
    assert rd["kind"] == "pdf" and (rd["failure"] is None or rd["failure"] in ("needs_ocr", "too_large"))


def test_gov_page_live():
    rd = asyncio.run(tools.read_url("https://www.stats.gov.sa/en/home"))
    assert rd["status"] is not None  # read, or refused with a recorded status
