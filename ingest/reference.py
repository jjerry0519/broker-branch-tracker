"""Weekly refresh of ``reference.db`` — the ``company`` and ``broker_branch`` tables.

Endpoint shapes observed live 2026-09-07
---------------------------------------

* Companies (上市) — TWSE OpenAPI ``opendata/t187ap03_L`` ("上市公司基本資料"),
  ~1094 rows.  Row keys (subset): ``公司代號`` (4-digit, plus 6 foreign TDR
  ``9103xx`` codes), ``公司名稱`` (full), ``公司簡稱`` (short).  We store 公司代號
  / 公司簡稱, market ``twse``.

* Companies (上櫃) — TPEx OpenAPI ``tpex_mainboard_daily_close_quotes``,
  ~10984 rows of *daily quotes* for every OTC security.  Row keys (subset):
  ``SecuritiesCompanyCode``, ``CompanyName``.  Only ~887 rows carry a 4-digit
  numeric code (common stock); the remaining ~10k are 6-digit ETF / warrant /
  bond lines.  We keep 4-digit numeric codes only, market ``tpex``.

* Brokers — the brief's ``www.twse.com.tw/rwd/zh/brokerService/brokerList?
  response=json`` JSON feed is DEAD (returns a Grails redirect stub to
  ``http://www.twse.com.tw``).  Replaced with two TWSE OpenAPI feeds:
    - ``opendata/OpenData_BRK02`` ("證券商分公司基本資料"), ~814 rows.
      Keys: ``證券商代號`` (4 chars, e.g. ``1021``, ``102A``), ``證券商名稱``.
    - ``brokerService/brokerList`` ("證券商總公司基本資料"), ~64 rows.
      Keys: ``Code`` (4 chars, always ``<prefix>0``), ``Name``.
  Union (no code overlap) -> ~878 ``broker_branch`` rows.

Parent derivation
-----------------
A TWSE broker code is a 3-char firm prefix + a 1-char branch slot.  The head
office occupies slot ``0`` (``1020``); branches occupy ``1``..``9`` / ``A``..
(``102A``).  So ``parent_broker_id = code[:3] + "0"`` and a head office is its
own parent.  ``parent_broker_name`` is that head-office row's name when present
in the same batch, else the branch's own name (foreign / IB brokers whose head
office has no row).
"""

from __future__ import annotations

import sqlite3

import httpx

_UA = "broker-branch-tracker/1.0 (+personal research)"

_TWSE_COMPANY = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
_TPEX_COMPANY = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
_BROKER_BRANCHES = "https://openapi.twse.com.tw/v1/opendata/OpenData_BRK02"
_BROKER_HEADS = "https://openapi.twse.com.tw/v1/brokerService/brokerList"


def parse_broker_list(rows: list) -> list[tuple[str, str, str, str]]:
    """``[[code, name, ...], ...]`` -> ``[(branch_id, branch_name,
    parent_broker_id, parent_broker_name), ...]`` (pure, no network).

    Later rows win on a duplicate code.  ``parent_broker_id`` is the firm
    prefix + slot ``0``; ``parent_broker_name`` is that code's own name if the
    batch contains it, else the row's own name.
    """
    by_id: dict[str, str] = {}
    for r in rows:
        code, name = str(r[0]).strip(), str(r[1]).strip()
        if code:
            by_id[code] = name
    out: list[tuple[str, str, str, str]] = []
    for code, name in by_id.items():
        parent_id = code[:3] + "0"
        parent_name = by_id.get(parent_id, name)
        out.append((code, name, parent_id, parent_name))
    return out


def _is_equity(code: str) -> bool:
    return len(code) == 4 and code.isdigit()


def refresh_reference_db(path: str = "reference.db") -> tuple[int, int]:
    """(Re)build ``company`` + ``broker_branch`` in ``path``; return
    ``(company_count, broker_branch_count)``.  Tables are created if missing
    and rows are upserted (``INSERT OR REPLACE`` on the primary key)."""
    headers = {"User-Agent": _UA}
    with httpx.Client(timeout=90, headers=headers, follow_redirects=True) as c:
        twse_co = c.get(_TWSE_COMPANY).json()
        tpex_co = c.get(_TPEX_COMPANY).json()
        branches = c.get(_BROKER_BRANCHES).json()
        heads = c.get(_BROKER_HEADS).json()

    companies: list[tuple[str, str, str]] = []
    for r in twse_co:
        cid = str(r.get("公司代號") or r.get("Code") or "").strip()
        if cid:
            name = str(r.get("公司簡稱") or r.get("公司名稱") or r.get("Name") or "").strip()
            companies.append((cid, name, "twse"))
    for r in tpex_co:
        cid = str(r.get("SecuritiesCompanyCode") or r.get("Code") or "").strip()
        if _is_equity(cid):
            name = str(r.get("CompanyName") or r.get("Name") or "").strip()
            companies.append((cid, name, "tpex"))

    broker_rows = [[str(r.get("Code") or "").strip(), str(r.get("Name") or "").strip()]
                   for r in heads]
    broker_rows += [[str(r.get("證券商代號") or "").strip(),
                     str(r.get("證券商名稱") or "").strip()] for r in branches]
    brokers = parse_broker_list(broker_rows)

    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS company(
                stock_id TEXT PRIMARY KEY, name TEXT, market TEXT);
            CREATE TABLE IF NOT EXISTS broker_branch(
                branch_id TEXT PRIMARY KEY, branch_name TEXT,
                parent_broker_id TEXT, parent_broker_name TEXT);
            """
        )
        conn.executemany("INSERT OR REPLACE INTO company VALUES (?,?,?)", companies)
        conn.executemany(
            "INSERT OR REPLACE INTO broker_branch VALUES (?,?,?,?)", brokers)
        conn.commit()
        n_c = conn.execute("SELECT COUNT(*) FROM company").fetchone()[0]
        n_b = conn.execute("SELECT COUNT(*) FROM broker_branch").fetchone()[0]
    finally:
        conn.close()
    return n_c, n_b
