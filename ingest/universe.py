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

import re
import time
from dataclasses import dataclass

import httpx

_UA = "broker-branch-tracker/1.0 (+personal research)"

_TWSE_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
_TPEX_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"


def _get_json(client: httpx.Client, url: str, *, tries: int = 4):
    """GET + parse JSON with retry/backoff. The TWSE/TPEx OpenAPI feeds
    intermittently return a truncated body (JSONDecodeError) or a transient 5xx,
    especially from datacenter IPs."""
    last: Exception | None = None
    for i in range(tries):
        try:
            r = client.get(url, timeout=60, headers={"User-Agent": _UA})
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, ValueError) as e:   # ValueError covers JSONDecodeError
            last = e
            time.sleep(1.5 * (i + 1))
    raise last  # type: ignore[misc]


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
    return parse_twse_stock_day_all(_get_json(client, _TWSE_URL))


def tpex_traded(client: httpx.Client) -> list[Stock]:
    return parse_tpex_daily_close(_get_json(client, _TPEX_URL))


def resolved_trading_date(client: httpx.Client) -> str | None:
    """Latest trading day for which TWSE has published daily close data, as ISO
    ``YYYY-MM-DD`` — taken from the ``Date`` field of STOCK_DAY_ALL (ROC form
    ``1150907`` -> ``2026-09-07``). ``None`` only if the endpoint is unreachable
    or no row carries a parseable date.

    On the 18:00 Asia/Taipei cron this equals "today" on a trading day and the
    previous trading day on a holiday; the orchestrator treats an already-ingested
    date as a skip, so a holiday cron fire is a no-op. The BSR menu page no longer
    carries a data date, which is why this uses the OpenAPI feed instead.
    """
    try:
        rows = _get_json(client, _TWSE_URL)
    except (httpx.HTTPError, ValueError):
        return None
    for row in rows:
        m = re.fullmatch(r"(\d{3})(\d{2})(\d{2})", str(row.get("Date", "")).strip())
        if m:
            y, mo, d = (int(x) for x in m.groups())
            return f"{y + 1911:04d}-{mo:02d}-{d:02d}"
    return None
