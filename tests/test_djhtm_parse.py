from pathlib import Path

import pytest

from ingest.djhtm_parse import (DjhtmError, ZcoPage, Zco0Page, looks_blocked,
                                parse_zco, parse_zco0)

FIX = Path(__file__).parent / "fixtures"


# --------------------------------------------------------------------------- #
# zco0 -- per (stock, branch) daily history
# --------------------------------------------------------------------------- #
def test_parse_zco0_sample():
    page = parse_zco0((FIX / "djhtm_zco0_2330_9200.html").read_bytes())
    assert isinstance(page, Zco0Page)
    assert len(page.rows) == 49
    # newest first, ISO-normalised
    assert page.rows[0].date == "2025-09-05"
    assert page.rows[-1].date == "2025-07-01"
    r0 = page.rows[0]
    assert (r0.buy_lots, r0.sell_lots, r0.net_lots) == (162, 816, -654)
    # published checksum matches the row sum
    assert page.period_net_lots == 4040
    assert page.checksum_ok is True
    assert sum(r.net_lots for r in page.rows) == 4040
    assert page.empty is False


def test_parse_zco0_checksum_mismatch_still_parses_but_flags():
    html = (
        '<table><tr><td class="t2">日期</td><td>買進(張)</td><td>賣出(張)</td>'
        '<td>買賣總額</td><td>買賣超(張)</td></tr>'
        '<tr><TD class="t4n0">2026/01/05</TD><TD>10</TD><TD>3</TD><TD>13</TD>'
        '<TD>7</TD></tr>'
        '<tr><TD class="t4n0">2026/01/06</TD><TD>0</TD><TD>4</TD><TD>4</TD>'
        '<TD>-4</TD></tr></table>'
        '<font>期間累計買賣超張數： 999</font>'
    )
    page = parse_zco0(html)
    assert [r.net_lots for r in page.rows] == [7, -4]
    assert page.period_net_lots == 999
    assert page.checksum_ok is False           # 3 != 999


def test_parse_zco0_all_zero_window_is_empty():
    html = (
        '<table><tr><td class="t2">日期</td><td>買</td><td>賣</td><td>總</td>'
        '<td>超</td></tr>'
        '<tr><TD class="t4n0">2026/02/02</TD><TD>0</TD><TD>0</TD><TD>0</TD>'
        '<TD>0</TD></tr></table>'
        '<font>期間累計買賣超張數： 0</font>'
    )
    page = parse_zco0(html)
    assert page.empty is True
    assert page.checksum_ok is True


def test_parse_zco0_rejects_blocked_and_empty():
    with pytest.raises(DjhtmError):
        parse_zco0(b"<html><head><title>Just a moment...</title></head></html>")
    with pytest.raises(DjhtmError):
        parse_zco0(b"<html><body><p>no table here</p></body></html>")


def test_thousands_separators_and_negative_reds():
    html = (
        '<table><tr><td class="t2">日期</td><td>買</td><td>賣</td><td>總</td>'
        '<td>超</td></tr>'
        '<tr><TD class="t4n0">2026/03/03</TD><TD class="t3n1">2,651</TD>'
        '<TD class="t3n1">283</TD><TD class="t3n1">2,934</TD>'
        '<TD class="t3r1">2,368</TD></tr></table>'
    )
    page = parse_zco0(html)
    assert (page.rows[0].buy_lots, page.rows[0].sell_lots,
            page.rows[0].net_lots) == (2651, 283, 2368)


# --------------------------------------------------------------------------- #
# zco -- latest-day top-15 / top-15 board
# --------------------------------------------------------------------------- #
def test_parse_zco_twse_sample():
    page = parse_zco((FIX / "djhtm_zco_2330.html").read_bytes(), stock_id="2330")
    assert isinstance(page, ZcoPage)
    assert page.stock_id == "2330"
    assert page.data_date == "2026-09-09"
    assert len(page.branches) == 30                     # 15 buy + 15 sell
    top = page.branches[0]
    assert top.branch_name == "美林"
    assert (top.buy_lots, top.sell_lots, top.net_lots) == (1541, 839, 702)
    # a sell-side row carries a negative net
    assert any(b.net_lots < 0 for b in page.branches)
    assert page.total_buy_lots == 2823
    assert page.total_sell_lots == 4564
    assert page.avg_buy_cost == pytest.approx(2474.0)
    assert page.avg_sell_cost == pytest.approx(2473.3)


def test_parse_zco_tpex_sample_same_shape():
    page = parse_zco((FIX / "djhtm_zco_6488.html").read_bytes(), stock_id="6488")
    assert page.data_date == "2026-09-09"
    assert len(page.branches) == 30
    assert page.avg_buy_cost and page.avg_sell_cost


def test_looks_blocked():
    assert looks_blocked("... Just a moment ...")
    assert looks_blocked("<title>404 - blah")
    assert not looks_blocked("<table><tr><td>2026/01/01</td></tr></table>")
