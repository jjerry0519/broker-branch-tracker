from __future__ import annotations

import time

from ingest.tpex_parse import BrokerBsPage, TpexRetry, parse_brokerbs

_PAGE = "https://www.tpex.org.tw/zh-tw/mainboard/trading/info/brokerBS.html"
_ENDPOINT = "https://www.tpex.org.tw/www/zh-tw/afterTrading/brokerBS"
_TOKEN_SEL = "[name=cf-turnstile-response]"
PLAYWRIGHT_HEADED = True

_FETCH_JS = """
async ({endpoint, body}) => {
  const r = await fetch(endpoint, {
    method: 'POST',
    headers: {'Content-Type': 'application/x-www-form-urlencoded',
              'X-Requested-With': 'XMLHttpRequest'},
    body: new URLSearchParams(body).toString(),
  });
  return await r.json();
}
"""


def build_body(token: str, stock_id: str) -> dict[str, str]:
    return {"cf-turnstile-response": token, "code": stock_id, "id": "", "response": "json"}


class TpexBrowser:
    """Owns one headed Chromium page against the brokerBS form.

    The brokerBS data endpoint authorises each POST with a single-use Cloudflare
    Turnstile token that the managed widget re-issues into the live DOM after a
    short delay. So we keep one page open, read a fresh token per query, and POST
    from page context via ``page.evaluate`` (same-origin fetch). On ``TpexRetry``
    we reload the page once to force a brand-new widget, then retry.
    """

    def __init__(self, *, headed: bool = PLAYWRIGHT_HEADED):
        self._headed = headed
        self._pw = self._browser = self._page = None

    def __enter__(self) -> "TpexBrowser":
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=not self._headed)
        self._page = self._browser.new_page()
        self.reload()
        return self

    def __exit__(self, *exc) -> None:
        try:
            if self._browser:
                self._browser.close()
        finally:
            if self._pw:
                self._pw.stop()

    def reload(self) -> None:
        self._page.goto(_PAGE, wait_until="domcontentloaded")
        self._page.wait_for_function(
            f"document.querySelector('{_TOKEN_SEL}')?.value.length > 100", timeout=45000)

    def _fresh_token(self, *, timeout_s: float = 30.0) -> str:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            tok = self._page.evaluate(
                f"document.querySelector('{_TOKEN_SEL}')?.value || ''")
            if len(tok) > 100:
                return tok
            time.sleep(1.0)
        raise TpexRetry("no fresh turnstile token")

    def _query(self, stock_id: str) -> dict:
        body = build_body(self._fresh_token(), stock_id)
        return self._page.evaluate(_FETCH_JS, {"endpoint": _ENDPOINT, "body": body})

    def fetch_stock(self, stock_id: str) -> BrokerBsPage:
        try:
            return parse_brokerbs(self._query(stock_id))
        except TpexRetry:
            self.reload()
            return parse_brokerbs(self._query(stock_id))
