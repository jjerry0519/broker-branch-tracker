import json
from pathlib import Path

import pytest

from ingest import tpex_client
from ingest.tpex_parse import BrokerBsPage, TpexRetry, parse_brokerbs

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "tpex_sample.json").read_text("utf-8"))


def test_build_body():
    b = tpex_client.build_body("TOK123", "6488")
    assert b == {"cf-turnstile-response": "TOK123", "code": "6488",
                 "id": "", "response": "json"}


def test_parse_path_shared_with_tpex_parse():
    # fetch_stock's only non-browser logic is parse_brokerbs; confirm the fixture parses
    page = parse_brokerbs(FIXTURE)
    assert isinstance(page, BrokerBsPage) and page.stock_id == "6488"


def test_parse_brokerbs_timeout_is_retry():
    with pytest.raises(TpexRetry):
        parse_brokerbs({"stat": "操作逾時，請重新整理頁面後再試，謝謝。"})


@pytest.mark.integration
def test_tpex_browser_fetches_many_stocks():
    """ACCEPTANCE: one browser session must serve many sequential stock queries
    (managed Turnstile re-issuing a token per query). If Turnstile hard-blocks
    after N, this fails and the TPEx leg moves to a self-hosted runner."""
    stocks = ["6488", "5483", "3529", "6510", "8069", "4966", "6415", "3105"]
    ok = 0
    with tpex_client.TpexBrowser() as br:
        for sid in stocks:
            try:
                page = br.fetch_stock(sid)
                ok += 1 if (page.rows and page.stock_id == sid) else 0
            except TpexRetry:
                pass
    assert ok >= len(stocks) - 1, f"only {ok}/{len(stocks)} succeeded"
