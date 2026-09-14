"""Historical close-price lookup for FIFO (design spec Sec.6 needs a price per
trading day; the stored flow rows carry ``close_price`` only for the day they
were ingested "live" -- backfilled history has ``0.0``, see schema.py).

Source: Yahoo Finance via ``yfinance`` (free, no key; ``.TW`` for TWSE,
``.TWO`` for TPEx). One ``history()`` call fetches a whole date range for a
stock, cached forever to a local SQLite file -- close prices for a trading day
never change, so once fetched a stock+date is never re-fetched.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


def _ticker(stock_id: str, market: str) -> str:
    return f"{stock_id}.TW" if market == "twse" else f"{stock_id}.TWO"


def _open_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS close_price(
            stock_id TEXT, date TEXT, close REAL,
            PRIMARY KEY (stock_id, date))
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS volume(
            stock_id TEXT, date TEXT, volume INTEGER,
            PRIMARY KEY (stock_id, date))
    """)
    return conn


def cached_closes(stock_id: str, dates: list[str], *,
                  db_path: str = "close_cache.db") -> dict[str, float]:
    """Whatever of ``dates`` is already cached for ``stock_id``."""
    if not dates:
        return {}
    conn = _open_db(db_path)
    try:
        placeholders = ",".join("?" * len(dates))
        rows = conn.execute(
            f"SELECT date, close FROM close_price "
            f"WHERE stock_id = ? AND date IN ({placeholders})",
            [stock_id, *dates]).fetchall()
        return {d: c for d, c in rows}
    finally:
        conn.close()


def fetch_and_cache_closes(stock_id: str, market: str, start: str, end: str,
                           *, db_path: str = "close_cache.db",
                           downloader=None) -> dict[str, float]:
    """Fetch ``[start, end]`` closes for ``stock_id`` from Yahoo Finance and
    cache them. ``downloader(ticker, start, end) -> {date: close}`` is
    injectable for tests; defaults to a real ``yfinance`` call.
    """
    if downloader is None:
        downloader = _yfinance_download
    got = downloader(_ticker(stock_id, market), start, end)
    if not got:
        return {}
    conn = _open_db(db_path)
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO close_price VALUES (?,?,?)",
            [(stock_id, d, c) for d, c in got.items()])
        conn.commit()
    finally:
        conn.close()
    return got


def _yfinance_download(ticker: str, start: str, end: str) -> dict[str, float]:
    import datetime as dt
    import yfinance as yf
    end_excl = (dt.date.fromisoformat(end) + dt.timedelta(days=1)).isoformat()
    hist = yf.Ticker(ticker).history(start=start, end=end_excl)
    if hist.empty:
        return {}
    return {idx.strftime("%Y-%m-%d"): float(row["Close"])
           for idx, row in hist.iterrows()}


def get_closes(stock_id: str, market: str, dates: list[str], *,
               db_path: str = "close_cache.db", downloader=None) -> dict[str, float]:
    """Closes for every requested date that are available (cache-first, then
    one network call for whatever is missing across the whole span)."""
    if not dates:
        return {}
    have = cached_closes(stock_id, dates, db_path=db_path)
    missing = sorted(set(dates) - set(have))
    if missing:
        fetched = fetch_and_cache_closes(
            stock_id, market, min(missing), max(missing),
            db_path=db_path, downloader=downloader)
        have.update({d: c for d, c in fetched.items() if d in dates})
    return have


# --------------------------------------------------------------------------- #
# volume -- same cache-then-fetch-a-span pattern as closes, kept independent
# (own table, own downloader contract) so get_closes' tested behaviour is
# untouched.
# --------------------------------------------------------------------------- #
def cached_volumes(stock_id: str, dates: list[str], *,
                   db_path: str = "close_cache.db") -> dict[str, int]:
    if not dates:
        return {}
    conn = _open_db(db_path)
    try:
        placeholders = ",".join("?" * len(dates))
        rows = conn.execute(
            f"SELECT date, volume FROM volume "
            f"WHERE stock_id = ? AND date IN ({placeholders})",
            [stock_id, *dates]).fetchall()
        return {d: v for d, v in rows}
    finally:
        conn.close()


def fetch_and_cache_volumes(stock_id: str, market: str, start: str, end: str,
                            *, db_path: str = "close_cache.db",
                            downloader=None) -> dict[str, int]:
    if downloader is None:
        downloader = _yfinance_download_volume
    got = downloader(_ticker(stock_id, market), start, end)
    if not got:
        return {}
    conn = _open_db(db_path)
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO volume VALUES (?,?,?)",
            [(stock_id, d, v) for d, v in got.items()])
        conn.commit()
    finally:
        conn.close()
    return got


def _yfinance_download_volume(ticker: str, start: str, end: str) -> dict[str, int]:
    import datetime as dt
    import yfinance as yf
    end_excl = (dt.date.fromisoformat(end) + dt.timedelta(days=1)).isoformat()
    hist = yf.Ticker(ticker).history(start=start, end=end_excl)
    if hist.empty:
        return {}
    return {idx.strftime("%Y-%m-%d"): int(row["Volume"])
           for idx, row in hist.iterrows()}


def get_volumes(stock_id: str, market: str, dates: list[str], *,
                db_path: str = "close_cache.db", downloader=None) -> dict[str, int]:
    """Daily trading volume (shares) for every requested date that's available
    -- for the price-chart volume overlay. Same Yahoo Finance source as
    :func:`get_closes`, cached separately."""
    if not dates:
        return {}
    have = cached_volumes(stock_id, dates, db_path=db_path)
    missing = sorted(set(dates) - set(have))
    if missing:
        fetched = fetch_and_cache_volumes(
            stock_id, market, min(missing), max(missing),
            db_path=db_path, downloader=downloader)
        have.update({d: v for d, v in fetched.items() if d in dates})
    return have
