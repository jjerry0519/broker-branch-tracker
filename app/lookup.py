"""Fuzzy stock / branch lookup against the committed ``reference.db``."""
from __future__ import annotations

import sqlite3


def find_stocks(query: str, *, db_path: str = "reference.db", limit: int = 10
                ) -> list[tuple[str, str, str]]:
    """``[(stock_id, name, market), ...]`` matching ``query`` by id prefix or
    name substring. Exact id match always sorts first."""
    query = query.strip()
    if not query:
        return []
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            """SELECT stock_id, name, market FROM company
              WHERE stock_id LIKE ? OR name LIKE ?
              ORDER BY (stock_id = ?) DESC, stock_id LIMIT ?""",
            (f"{query}%", f"%{query}%", query, limit)).fetchall()
        return rows
    finally:
        conn.close()


def find_branches(query: str, *, db_path: str = "reference.db", limit: int = 10
                  ) -> list[tuple[str, str]]:
    """``[(branch_id, branch_name), ...]`` matching ``query`` by id prefix or
    name substring."""
    query = query.strip()
    if not query:
        return []
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            """SELECT branch_id, branch_name FROM broker_branch
              WHERE branch_id LIKE ? OR branch_name LIKE ?
              ORDER BY (branch_id = ?) DESC, branch_id LIMIT ?""",
            (f"{query}%", f"%{query}%", query, limit)).fetchall()
        return rows
    finally:
        conn.close()


def stock_market(stock_id: str, *, db_path: str = "reference.db") -> str | None:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT market FROM company WHERE stock_id = ?",
                           (stock_id,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()
