"""HTTP client for the SysJust ``.djhtm`` broker-branch pages, with mirror
failover.

The same ``/z/zc/zco/...`` path tree is served by MoneyDJ and by a number of
broker white-label front-ends. Fubon-eBrokerDJ is the primary because it answers
cleanly from datacenter / GitHub-Actions IPs (plain Microsoft-IIS, no Cloudflare,
no login) -- verified from a runner. MoneyDJ itself is kept as a fallback: its
``zco0`` still works when both ``A`` (stock) and ``BHID`` (branch) are supplied,
though its branch-only variants sit behind an SSO wall and its TLS chain needs a
lenient verify. Extra mirrors can be appended to ``MIRRORS`` freely -- failover
just walks the list.

Everything here is plain ``httpx`` -- no browser, no cookies, no CAPTCHA.
"""
from __future__ import annotations

import ssl
import time

import httpx

from ingest.djhtm_parse import DjhtmError, Zco0Page, ZcoPage, parse_zco, parse_zco0

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# Ordered; index 0 is tried first. Verified reachable from a GitHub-Actions
# runner on 2026-09-10: fubon-ebrokerdj 8/8, moneydj blocked only by a local
# OpenSSL cert-chain quirk (handled by the lenient context below).
MIRRORS: tuple[str, ...] = (
    "https://fubon-ebrokerdj.fbs.com.tw",
    "https://www.moneydj.com",
    "https://sjmain.djnews.com.tw",
    "https://jsjustweb.jihsun.com.tw",
)

_ZCO0_PATH = "/z/zc/zco/zco0/zco0.djhtm"
_ZCO_PATH = "/z/zc/zco/zco.djhtm"


class DjhtmClientError(RuntimeError):
    """Every mirror failed for one request."""


def _lenient_ssl() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    # MoneyDJ's intermediate is missing a Subject Key Identifier, which
    # OpenSSL 3.x rejects by default; we still want that mirror as a fallback.
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def new_client(*, timeout: float = 30.0) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": _UA, "Accept-Language": "zh-TW,zh;q=0.9"},
        timeout=timeout,
        follow_redirects=True,
        verify=_lenient_ssl(),
    )


def _get(client: httpx.Client, base: str, path: str, params: dict) -> bytes:
    r = client.get(base + path, params=params)
    r.raise_for_status()
    return r.content


def fetch_zco0(client: httpx.Client, stock_id: str, branch_id: str,
               d_from: str, d_to: str, *, attempts: int = 2,
               mirrors: tuple[str, ...] = MIRRORS) -> Zco0Page:
    """One ``(stock, branch)`` pair's daily history for ``[d_from, d_to]``
    (ISO dates). Walks ``mirrors``; retries each ``attempts`` times with a short
    backoff. Raises :class:`DjhtmClientError` if none yield a parseable page.
    """
    params = {"A": stock_id, "BHID": branch_id, "b": branch_id, "C": "1",
              "D": d_from, "E": d_to}
    last: Exception | None = None
    for base in mirrors:
        for i in range(attempts):
            try:
                page = parse_zco0(_get(client, base, _ZCO0_PATH, params))
                if not page.checksum_ok:
                    raise DjhtmError("row sum != published checksum")
                return page
            except (httpx.HTTPError, DjhtmError) as e:
                last = e
                time.sleep(0.4 * (i + 1))
    raise DjhtmClientError(
        f"zco0 {stock_id}/{branch_id} {d_from}..{d_to}: all mirrors failed "
        f"({type(last).__name__}: {last})")


def fetch_zco(client: httpx.Client, stock_id: str, *, attempts: int = 2,
              mirrors: tuple[str, ...] = MIRRORS) -> ZcoPage:
    """The latest-day top-15/top-15 branch board for ``stock_id``."""
    params = {"a": stock_id}
    last: Exception | None = None
    for base in mirrors:
        for i in range(attempts):
            try:
                return parse_zco(_get(client, base, _ZCO_PATH, params),
                                 stock_id=stock_id)
            except (httpx.HTTPError, DjhtmError) as e:
                last = e
                time.sleep(0.4 * (i + 1))
    raise DjhtmClientError(
        f"zco {stock_id}: all mirrors failed ({type(last).__name__}: {last})")
