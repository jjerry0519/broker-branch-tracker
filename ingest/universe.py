"""Daily traded-stock universe (TWSE + TPEx OpenAPI) and the non-trading-day guard.

Fixture key names observed 2026-09 (tests/fixtures/*.json):

* TWSE  https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL
  row keys: Date, Code, Name, TradeVolume, TradeValue, OpeningPrice,
  HighestPrice, LowestPrice, ClosingPrice, Change, Transaction
  -> we read Code / Name / ClosingPrice.  Non-equity lines carry codes like
  "00408A" (ETF) or "1101B" (corporate bond); blank close is "" for
  untraded lines.

* TPEx  https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes
  row keys: Date, SecuritiesCompanyCode, CompanyName, Close, Change, Open,
  High, Low, Average, TradingShares, TransactionAmount, TransactionNumber,
  LatestBidPrice, LatesAskPrice, Capitals, NextReferencePrice, NextLimitUp,
  NextLimitDown
  -> we read SecuritiesCompanyCode / CompanyName / Close.  Untraded lines
  carry Close " ---".  (`.get(... ) or .get(...)` fallbacks are kept so the
  parser still works if TPEx reverts to Code / Name / ClosingPrice keys.)

Only 4-digit numeric codes are kept (common-stock heuristic; this also keeps
numeric-coded ETFs such as 0050, which is acceptable for a traded-universe
membership test).
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup

_TW = ZoneInfo("Asia/Taipei")
_UA = "broker-branch-tracker/1.0 (+personal research)"

_TWSE_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
_TPEX_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
_BSR_MENU_URL = "https://bsr.twse.com.tw/bshtm/bsMenu.aspx"


@dataclass(frozen=True, slots=True)
class Stock:
    stock_id: str
    name: str
    market: str
    close_price: float


def _f(x) -> float | None:
    s = str(x or "").strip().replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def _is_equity(code: str) -> bool:
    return len(code) == 4 and code.isdigit()


def parse_twse_stock_day_all(rows: list[dict]) -> list[Stock]:
    out: list[Stock] = []
    for r in rows:
        code = str(r.get("Code", "")).strip()
        close = _f(r.get("ClosingPrice"))
        if not _is_equity(code) or close is None:
            continue
        out.append(Stock(code, str(r.get("Name", "")).strip(), "twse", close))
    return out


def parse_tpex_daily_close(rows: list[dict]) -> list[Stock]:
    out: list[Stock] = []
    for r in rows:
        code = str(r.get("SecuritiesCompanyCode") or r.get("Code") or "").strip()
        name = str(r.get("CompanyName") or r.get("Name") or "").strip()
        close = _f(r.get("Close") or r.get("ClosingPrice"))
        if not _is_equity(code) or close is None:
            continue
        out.append(Stock(code, name, "tpex", close))
    return out


def twse_traded(client: httpx.Client) -> list[Stock]:
    r = client.get(_TWSE_URL, timeout=60, headers={"User-Agent": _UA})
    r.raise_for_status()
    return parse_twse_stock_day_all(r.json())


def tpex_traded(client: httpx.Client) -> list[Stock]:
    r = client.get(_TPEX_URL, timeout=60, headers={"User-Agent": _UA})
    r.raise_for_status()
    return parse_tpex_daily_close(r.json())


def resolved_trading_date(client: httpx.Client) -> str | None:
    """ISO date shown as 資料日期 on the BSR menu page, iff it equals today
    (Asia/Taipei).  Otherwise None -> non-trading day, or not yet published.
    """
    r = client.get(_BSR_MENU_URL, timeout=30, headers={"User-Agent": _UA})
    r.raise_for_status()
    m = re.search(r"資料日期[:：]?\s*(\d{4})/(\d{1,2})/(\d{1,2})", r.text)
    if not m:
        soup = BeautifulSoup(r.text, "lxml")
        m = re.search(r"(\d{4})/(\d{1,2})/(\d{1,2})", soup.get_text())
    if not m:
        return None
    y, mo, d = (int(x) for x in m.groups())
    site_date = f"{y:04d}-{mo:02d}-{d:02d}"
    today = dt.datetime.now(_TW).date().isoformat()
    return site_date if site_date == today else None
