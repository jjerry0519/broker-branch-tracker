from pathlib import Path

from app import lookup

DB = str(Path(__file__).parent.parent / "reference.db")


def test_find_stocks_by_exact_id_sorts_first():
    rows = lookup.find_stocks("2330", db_path=DB)
    assert rows and rows[0][0] == "2330"
    assert rows[0][2] == "twse"


def test_find_stocks_by_name_substring():
    rows = lookup.find_stocks("台積電", db_path=DB)
    assert any(r[0] == "2330" for r in rows)


def test_find_stocks_empty_query():
    assert lookup.find_stocks("", db_path=DB) == []


def test_find_stocks_no_match():
    assert lookup.find_stocks("ZZZZ9", db_path=DB) == []


def test_find_branches_by_id_prefix():
    rows = lookup.find_branches("9200", db_path=DB)
    assert rows and rows[0][0].startswith("9200") or rows[0][0] == "9200"


def test_stock_market_lookup():
    assert lookup.stock_market("2330", db_path=DB) == "twse"
    assert lookup.stock_market("ZZZZ9", db_path=DB) is None
