from __future__ import annotations

from fallback.tpex_parse import BrokerBsPage, TpexRetry, parse_brokerbs

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
    Turnstile token that the managed widget issues into the live DOM. Vanilla
    Playwright is detected by TPEx's managed Turnstile (``Error: 600010``, no
    token ever), so this uses **patchright** — a stealth drop-in for Playwright.
    We keep one page open, ``reload()`` before every query to force a brand-new
    widget/token, read the token from the DOM, and POST from page context via
    ``page.evaluate`` (same-origin fetch). On ``TpexRetry`` we reload once more
    and retry.
    """

    def __init__(self, *, headed: bool = PLAYWRIGHT_HEADED):
        self._headed = headed
        self._pw = self._browser = self._page = None

    def __enter__(self) -> "TpexBrowser":
        # patchright: stealth Playwright drop-in that passes TPEx's managed Turnstile
        from patchright.sync_api import sync_playwright
        try:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=not self._headed)
            self._page = self._browser.new_page()
        except Exception:
            self._teardown()
            raise
        return self

    def _teardown(self) -> None:
        try:
            if self._browser is not None:
                self._browser.close()
        except Exception:
            pass
        finally:
            if self._pw is not None:
                self._pw.stop()
            self._pw = self._browser = self._page = None

    def __exit__(self, *exc) -> None:
        self._teardown()

    def reload(self) -> None:
        # a fresh Turnstile token per query: reload the page and wait for the widget
        self._page.goto(_PAGE, wait_until="domcontentloaded")
        self._page.wait_for_function(
            f"document.querySelector('{_TOKEN_SEL}')?.value.length > 100", timeout=45000)

    def _token(self) -> str:
        tok = self._page.evaluate(f"document.querySelector('{_TOKEN_SEL}')?.value || ''")
        if len(tok) <= 100:
            raise TpexRetry("no turnstile token after reload")
        return tok

    def _query(self, stock_id: str) -> dict:
        body = build_body(self._token(), stock_id)
        return self._page.evaluate(_FETCH_JS, {"endpoint": _ENDPOINT, "body": body})

    def fetch_stock(self, stock_id: str) -> BrokerBsPage:
        self.reload()
        try:
            return parse_brokerbs(self._query(stock_id))
        except TpexRetry:
            self.reload()
            return parse_brokerbs(self._query(stock_id))
