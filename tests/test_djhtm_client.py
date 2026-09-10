from pathlib import Path

import httpx
import pytest

from ingest import djhtm_client
from ingest.djhtm_client import (DjhtmClientError, fetch_zco, fetch_zco0,
                                 MIRRORS)

FIX = Path(__file__).parent / "fixtures"
ZCO0_OK = (FIX / "djhtm_zco0_2330_9200.html").read_bytes()
ZCO_OK = (FIX / "djhtm_zco_2330.html").read_bytes()

M0, M1 = MIRRORS[0], MIRRORS[1]


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler),
                        follow_redirects=True)


def test_fetch_zco0_happy_path(monkeypatch):
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        assert req.url.path.endswith("/zco0.djhtm")
        q = dict(req.url.params)
        assert q["A"] == "2330" and q["BHID"] == "9200" and q["b"] == "9200"
        assert q["C"] == "1" and q["D"] == "2025-07-01" and q["E"] == "2025-09-05"
        return httpx.Response(200, content=ZCO0_OK)

    with _client(handler) as c:
        page = fetch_zco0(c, "2330", "9200", "2025-07-01", "2025-09-05")
    assert len(page.rows) == 49 and page.checksum_ok
    assert seen["url"].startswith(M0)


def test_fetch_zco0_fails_over_to_second_mirror():
    def handler(req: httpx.Request) -> httpx.Response:
        if str(req.url).startswith(M0):
            return httpx.Response(503)
        return httpx.Response(200, content=ZCO0_OK)

    with _client(handler) as c:
        page = fetch_zco0(c, "2330", "9200", "2025-07-01", "2025-09-05",
                          attempts=1)
    assert len(page.rows) == 49


def test_fetch_zco0_treats_block_page_as_failure():
    block = b"<html><head><title>Just a moment...</title></head></html>"

    def handler(req: httpx.Request) -> httpx.Response:
        if str(req.url).startswith(M0):
            return httpx.Response(200, content=block)     # 200 but not data
        return httpx.Response(200, content=ZCO0_OK)

    with _client(handler) as c:
        page = fetch_zco0(c, "2330", "9200", "2025-01-01", "2025-02-01",
                          attempts=1)
    assert len(page.rows) == 49


def test_fetch_zco0_checksum_mismatch_rejected_then_next_mirror():
    bad = (
        '<table><tr><td class="t2">日期</td><td>買</td><td>賣</td><td>總</td>'
        '<td>超</td></tr><tr><TD class="t4n0">2026/01/05</TD><TD>1</TD>'
        '<TD>0</TD><TD>1</TD><TD>1</TD></tr></table>'
        '<font>期間累計買賣超張數： 500</font>'
    ).encode("big5")

    def handler(req: httpx.Request) -> httpx.Response:
        if str(req.url).startswith(M0):
            return httpx.Response(200, content=bad)
        return httpx.Response(200, content=ZCO0_OK)

    with _client(handler) as c:
        page = fetch_zco0(c, "2330", "9200", "2026-01-01", "2026-02-01",
                          attempts=1)
    assert page.checksum_ok and len(page.rows) == 49


def test_fetch_zco0_all_mirrors_dead_raises():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with _client(handler) as c:
        with pytest.raises(DjhtmClientError):
            fetch_zco0(c, "2330", "9200", "2025-01-01", "2025-02-01",
                       attempts=1)


def test_fetch_zco_happy_path():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path.endswith("/zco.djhtm")
        assert dict(req.url.params)["a"] == "2330"
        return httpx.Response(200, content=ZCO_OK)

    with _client(handler) as c:
        page = fetch_zco(c, "2330")
    assert page.stock_id == "2330" and len(page.branches) == 30


def test_new_client_has_lenient_tls_and_ua():
    c = djhtm_client.new_client()
    try:
        assert "Chrome" in c.headers["user-agent"]
    finally:
        c.close()
