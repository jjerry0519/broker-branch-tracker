from pathlib import Path

from fallback.twse_parse import BsrPage, parse_bsr_csv

FIXTURE = Path(__file__).parent / "fixtures" / "bsr_sample.csv"


def test_parse_bsr_sample():
    text = FIXTURE.read_bytes().decode("utf-8-sig", errors="replace")
    page = parse_bsr_csv(text)
    assert isinstance(page, BsrPage)
    assert page.stock_id == "2330"
    assert len(page.rows) > 5000                       # ~6052 price-level records
    r = page.rows[0]
    assert r.branch_id == "1020"                       # first data row
    assert r.branch_name.replace(" ", "") == "合庫"    # full-width spaces collapsed
    assert r.buy_shares == 642 and r.sell_shares == 0
    # right-half record on the same line is parsed too
    assert page.rows[1].branch_id == "1020" and page.rows[1].buy_shares == 8285
    # case-sensitive branch ids: both 9B2z and 9B2Z appear
    ids = {x.branch_id for x in page.rows}
    assert "9B2z" in ids and "9B2Z" in ids
    assert all(x.buy_shares >= 0 and x.sell_shares >= 0 for x in page.rows)


def test_parse_bsr_synthetic_shape():
    text = (
        '券商買賣股票成交價量資訊\n'
        '股票代碼,="9999"\n'
        '序號,券商,價格,買進股數,賣出股數,,序號,券商,價格,買進股數,賣出股數\n'
        '"1","1020合　　庫","10.00","1006","0",,"2","1440美林","10.00","0","2500" \n'
        '"3","1020合　　庫","11.00","4","0",,"",,,,\n'
    )
    page = parse_bsr_csv(text)
    assert page.stock_id == "9999"
    recs = [(r.branch_id, r.buy_shares, r.sell_shares) for r in page.rows]
    assert ("1020", 1006, 0) in recs
    assert ("1020", 4, 0) in recs
    assert ("1440", 0, 2500) in recs
    assert page.rows[0].branch_name == "合 庫"        # 　 -> space, stripped
