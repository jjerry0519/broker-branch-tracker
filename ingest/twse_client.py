from __future__ import annotations

import re

import httpx
from bs4 import BeautifulSoup

from ingest.captcha import looks_valid, solve
from ingest.twse_parse import BsrPage, parse_bsr_csv

_BASE = "https://bsr.twse.com.tw/bshtm/"
_UA = "broker-branch-tracker/1.0 (+personal research)"
_GUID = re.compile(r"guid=([0-9a-fA-F-]+)")


class BsrError(RuntimeError):
    pass


def new_client() -> httpx.Client:
    return httpx.Client(base_url=_BASE, timeout=30, follow_redirects=True,
                        headers={"User-Agent": _UA})


def fetch_stock(client: httpx.Client, stock_id: str, *, max_attempts: int = 5) -> BsrPage:
    last = ""
    for _ in range(max_attempts):
        soup = BeautifulSoup(client.get("bsMenu.aspx").text, "lxml")
        form = {i["name"]: i.get("value", "")
                for i in soup.select("form input[name]")}
        for junk in ("RadioButton_Excd", "Button_Reset"):
            form.pop(junk, None)
        img = soup.select_one("#Panel_bshtm img") or soup.select_one("img[src*=CaptchaImage]")
        if not img:
            raise BsrError(f"{stock_id}: no captcha panel")
        guid = _GUID.search(img["src"]).group(1)
        code = solve(client.get(f"CaptchaImage.aspx?guid={guid}").content)
        if not looks_valid(code):
            last = "captcha invalid"
            continue
        form.update({"__EVENTTARGET": "", "__EVENTARGUMENT": "",
                     "RadioButton_Normal": "RadioButton_Normal",
                     "TextBox_Stkno": stock_id, "CaptchaControl1": code,
                     "btnOK": "查詢"})
        post = client.post("bsMenu.aspx", data=form)
        link = BeautifulSoup(post.text, "lxml").select_one("#HyperLink_DownloadCSV")
        href = link.get("href") if link else None
        if not href:
            last = "captcha rejected / no report"
            continue
        raw = client.get(href).content
        page = parse_bsr_csv(raw.decode("utf-8-sig", errors="replace"))
        if page.rows:
            return page
        last = "empty rows"
    raise BsrError(f"{stock_id}: {last} after {max_attempts} attempts")
