import httpx
import pytest

from ingest import twse_client
from ingest.twse_parse import BsrPage

MENU_HTML = """<html><body><form>
<input name="__VIEWSTATE" value="vs"/><input name="__EVENTVALIDATION" value="ev"/>
<input name="__VIEWSTATEGENERATOR" value="g"/>
<input name="RadioButton_Normal" value="RadioButton_Normal"/>
<input name="TextBox_Stkno" value=""/><input name="CaptchaControl1" value=""/>
<input name="btnOK" value="查詢"/>
<div id="Panel_bshtm"><img src="CaptchaImage.aspx?guid=abc-123"/></div>
</form></body></html>"""

# POST response when the captcha is accepted: carries the download link
POST_OK = ('<html><body><form>'
           '<a id="HyperLink_DownloadCSV" href="bsContent.aspx?StkNo=2330&RecCount=2">下載</a>'
           '</form></body></html>')
# POST response when the captcha is rejected: no download link, error label present
POST_BAD = '<html><body><span id="Label_ErrorMsg">驗證碼錯誤!</span></body></html>'

# real BSR CSV shape: utf-8, 11 cols, index 5 empty, broker = <4 chars><name>
CSV_OK = (
    '券商買賣股票成交價量資訊\r\n'
    '股票代碼,="2330"\r\n'
    '序號,券商,價格,買進股數,賣出股數,,序號,券商,價格,買進股數,賣出股數\r\n'
    '"1","9200凱基台北","1085.00","1000","0",,"2","1440美　　林","1085.00","0","500" \r\n'
).encode("utf-8-sig")


def _make_handler(*, good_captcha: bool):
    def handler(request: httpx.Request) -> httpx.Response:
        p, m = request.url.path, request.method
        if p.endswith("bsMenu.aspx") and m == "GET":
            return httpx.Response(200, text=MENU_HTML)
        if "CaptchaImage" in p:
            return httpx.Response(200, content=b"\x89PNG_fake")
        if p.endswith("bsMenu.aspx") and m == "POST":
            return httpx.Response(200, text=POST_OK if good_captcha else POST_BAD)
        if p.endswith("bsContent.aspx"):
            return httpx.Response(200, content=CSV_OK)
        return httpx.Response(404)
    return handler


def test_fetch_stock_happy_path(monkeypatch):
    monkeypatch.setattr(twse_client, "solve", lambda b: "ABC12")
    monkeypatch.setattr(twse_client, "looks_valid", lambda s: True)
    client = httpx.Client(transport=httpx.MockTransport(_make_handler(good_captcha=True)),
                          base_url="https://bsr.twse.com.tw")
    page = twse_client.fetch_stock(client, "2330")
    assert isinstance(page, BsrPage)
    assert page.stock_id == "2330"
    assert {r.branch_id for r in page.rows} == {"9200", "1440"}
    assert next(r for r in page.rows if r.branch_id == "1440").sell_shares == 500


def test_fetch_stock_retries_then_fails(monkeypatch):
    monkeypatch.setattr(twse_client, "solve", lambda b: "ZZZZZ")
    monkeypatch.setattr(twse_client, "looks_valid", lambda s: True)
    client = httpx.Client(transport=httpx.MockTransport(_make_handler(good_captcha=False)),
                          base_url="https://bsr.twse.com.tw")
    with pytest.raises(twse_client.BsrError):
        twse_client.fetch_stock(client, "2330", max_attempts=3)


@pytest.mark.integration
def test_fetch_stock_live_2330():
    from ingest.twse_client import new_client
    with new_client() as c:
        page = twse_client.fetch_stock(c, "2330", max_attempts=30)
    assert page.stock_id == "2330"
    assert len(page.rows) > 500
    assert all(r.branch_id and r.buy_shares >= 0 and r.sell_shares >= 0 for r in page.rows)
