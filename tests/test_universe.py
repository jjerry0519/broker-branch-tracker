import json
from pathlib import Path

import httpx
import pytest

from ingest.universe import (
    Stock,
    parse_tpex_daily_close,
    parse_twse_stock_day_all,
    resolved_trading_date,
    tpex_traded,
    twse_traded,
)

FX = Path(__file__).parent / "fixtures"


def test_parse_twse_stock_day_all():
    rows = json.loads((FX / "twse_stock_day_all.json").read_text("utf-8"))
    out = parse_twse_stock_day_all(rows)
    assert out and all(isinstance(s, Stock) for s in out)
    assert all(s.market == "twse" for s in out)
    assert all(s.stock_id and s.close_price >= 0 for s in out)
    # equities only: 4-digit numeric codes
    assert any(len(s.stock_id) == 4 and s.stock_id.isdigit() for s in out)
    # non-equity codes (e.g. "00408A", "1101B") are dropped
    assert all(len(s.stock_id) == 4 and s.stock_id.isdigit() for s in out)


def test_parse_tpex_daily_close():
    rows = json.loads((FX / "tpex_daily_close.json").read_text("utf-8"))
    out = parse_tpex_daily_close(rows)
    assert out and all(s.market == "tpex" for s in out)
    assert all(s.stock_id and s.close_price >= 0 for s in out)
    assert all(len(s.stock_id) == 4 and s.stock_id.isdigit() for s in out)
    # blank-close / "---" rows are skipped, real equities survive
    ids = {s.stock_id for s in out}
    assert "1240" in ids


def test_parsers_skip_blank_close():
    assert parse_twse_stock_day_all([{"Code": "9999", "Name": "X", "ClosingPrice": "--"}]) == []
    assert parse_tpex_daily_close(
        [{"SecuritiesCompanyCode": "9999", "CompanyName": "X", "Close": " ---"}]
    ) == []


@pytest.mark.integration
def test_live_universes_nonempty():
    with httpx.Client() as c:
        twse = twse_traded(c)
        tpex = tpex_traded(c)
    assert len(twse) > 800, f"twse={len(twse)}"
    assert len(tpex) > 500, f"tpex={len(tpex)}"


@pytest.mark.integration
def test_resolved_trading_date_is_iso_or_none():
    with httpx.Client() as c:
        got = resolved_trading_date(c)
    assert got is None or (
        isinstance(got, str) and len(got) == 10 and got[4] == "-" and got[7] == "-"
    )
