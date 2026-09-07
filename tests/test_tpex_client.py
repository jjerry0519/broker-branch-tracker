import json
from pathlib import Path

import pytest

from ingest import tpex_client
from ingest.tpex_parse import BrokerBsPage, TpexRetry, parse_brokerbs

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "tpex_sample.json").read_text("utf-8"))


def test_build_body_shape_and_key_order():
    b = tpex_client.build_body("TOK123", "6488")
    assert b == {"cf-turnstile-response": "TOK123", "code": "6488",
                 "id": "", "response": "json"}
    assert list(b) == ["cf-turnstile-response", "code", "id", "response"]


def test_parse_path_shared_with_tpex_parse():
    page = parse_brokerbs(FIXTURE)
    assert isinstance(page, BrokerBsPage) and page.stock_id == "6488"


def test_parse_brokerbs_timeout_is_retry():
    with pytest.raises(TpexRetry):
        parse_brokerbs({"stat": "操作逾時，請重新整理頁面後再試，謝謝。"})


class _FakeBrowser(tpex_client.TpexBrowser):
    """Subclass that stubs the browser layer so fetch_stock's retry logic is testable."""
    def __init__(self, queries):
        super().__init__()
        self._queries = list(queries)   # each item: dict payload OR an exception to raise
        self.reload_calls = 0

    def reload(self):
        self.reload_calls += 1

    def _query(self, stock_id):
        item = self._queries.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_fetch_stock_retries_once_then_succeeds():
    ok = FIXTURE
    br = _FakeBrowser([TpexRetry("timeout"), ok])
    page = br.fetch_stock("6488")
    assert page.stock_id == "6488"
    assert br.reload_calls == 2          # once before the 1st query, once on retry


def test_fetch_stock_reraises_after_second_failure():
    br = _FakeBrowser([TpexRetry("t1"), TpexRetry("t2")])
    with pytest.raises(TpexRetry):
        br.fetch_stock("6488")


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
