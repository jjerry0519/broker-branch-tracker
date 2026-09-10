import json
from pathlib import Path

import pytest

from fallback.tpex_parse import BrokerBsPage, TpexRetry, parse_brokerbs

FIXTURE = Path(__file__).parent / "fixtures" / "tpex_sample.json"


def test_parse_tpex_sample():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    page = parse_brokerbs(payload)
    assert isinstance(page, BrokerBsPage)
    assert page.stock_id == "6488"
    assert page.trade_date == "2026-09-03"
    assert page.close_price == 927.0
    assert len(page.rows) > 5000                       # ~13483 detail rows
    assert page.rows[0].branch_id == "1020"
    assert page.rows[0].branch_name == "合庫"
    assert (page.rows[0].buy_shares, page.rows[0].sell_shares) == (5, 0)
    assert page.rows[1].branch_id == "1020" and page.rows[1].sell_shares == 1000
    assert all(r.buy_shares >= 0 and r.sell_shares >= 0 for r in page.rows)


@pytest.mark.parametrize("payload", [
    {"stat": "操作逾時，請重新整理頁面後再試，謝謝。"},
    {"stat": "查詢失敗"},
])
def test_parse_tpex_non_ok_stat_raises_retry(payload):
    with pytest.raises(TpexRetry):
        parse_brokerbs(payload)
