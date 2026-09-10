"""Parse the SysJust ``.djhtm`` broker-branch pages (BIG5).

Both pages are served by MoneyDJ / Fubon-eBrokerDJ and a dozen broker white-label
mirrors, as plain ``text/html; charset=big5`` over Microsoft-IIS (no Cloudflare,
no login, no CAPTCHA):

``zco0.djhtm?A=<stock>&BHID=<branch>&b=<branch>&C=1&D=<from>&E=<to>``
    One ``(stock, branch)`` pair's **daily** buy / sell / net over an arbitrary
    date range (up to ~15 months back), one row per trading day, followed by a
    ``期間累計買賣超張數：N`` checksum row. Quantities are in 張 (1 張 = 1000
    shares). This is the authoritative per-pair feed -- it is queried *by* the
    pair, so it is never truncated by a ranking cut-off.

``zco.djhtm?a=<stock>``
    The latest trading day only: top-15 net buyers + top-15 net sellers (branch
    display names, no codes), plus the site's own average-cost estimate. This
    view *is* truncated; the pipeline uses it only to notice which pairs were
    active on a given day and to back the ranking screen -- never as the store.

Parsing is deliberately structural-regex (no bs4) to match the stdlib style of
``twse_parse``. Every ``<tr>`` becomes a list of plain-text ``<td>/<th>`` cells;
callers pick the rows they need by shape.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape

# Response bodies that are not a real data page (edge block, mirror login wall,
# missing file). The client treats these as "try the next mirror".
_BLOCK_MARKERS = (
    "Just a moment",
    "Sign in Page",
    "Attention Required",
    "cf-browser-verification",
    "<title>404",
    "404 - ",
)

_DATE_RE = re.compile(r"^\s*(\d{4})/(\d{2})/(\d{2})\s*$")
_ROW_RE = re.compile(r"(?is)<tr[^>]*>(.*?)</tr>")
_CELL_RE = re.compile(r"(?is)<t[dh][^>]*>(.*?)</t[dh]>")
_TAG_RE = re.compile(r"(?is)<[^>]+>")
_CHECKSUM_RE = re.compile(r"期間累計買賣超[^：:]*[：:]\s*(-?[\d,]+)")
_DATADATE_RE = re.compile(r"(?:最後更新日|最新為|資料日期?)[：:\s　]*(\d{4})/(\d{2})/(\d{2})")
_TOTAL_BUY_RE = re.compile(r"合計買超[^\d\-]*(-?[\d,]+)")
_TOTAL_SELL_RE = re.compile(r"合計賣超[^\d\-]*(-?[\d,]+)")
_AVG_BUY_RE = re.compile(r"平均買超成本[^\d\-]*(-?[\d,.]+)")
_AVG_SELL_RE = re.compile(r"平均賣超成本[^\d\-]*(-?[\d,.]+)")


class DjhtmError(ValueError):
    """The body is not a parseable ``.djhtm`` data page (blocked / malformed)."""


def looks_blocked(text: str) -> bool:
    head = text[:4000]
    return any(m in head for m in _BLOCK_MARKERS)


def _cells(tr_html: str) -> list[str]:
    out: list[str] = []
    for raw in _CELL_RE.findall(tr_html):
        txt = unescape(_TAG_RE.sub("", raw)).replace("\xa0", " ")
        out.append(" ".join(txt.split()))
    return out


def _rows(html: str) -> list[list[str]]:
    return [c for c in (_cells(tr) for tr in _ROW_RE.findall(html)) if any(c)]


def _int(cell: str) -> int:
    s = (cell or "").strip().replace(",", "").replace("　", "")
    return int(s) if re.fullmatch(r"-?\d+", s) else 0


def _num(cell: str) -> float | None:
    s = (cell or "").strip().replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def _iso(y: str, m: str, d: str) -> str:
    return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"


# --------------------------------------------------------------------------- #
# zco0 -- per (stock, branch) daily history
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Zco0Row:
    date: str          # ISO YYYY-MM-DD
    buy_lots: int
    sell_lots: int
    net_lots: int      # buy_lots - sell_lots (may be negative)


@dataclass(frozen=True, slots=True)
class Zco0Page:
    rows: list[Zco0Row]
    period_net_lots: int | None      # the "期間累計買賣超張數" checksum, if present
    stock_id: str = ""               # best-effort, from the header line
    branch_name: str = ""            # best-effort, from the header line

    @property
    def checksum_ok(self) -> bool:
        """``True`` when no checksum was published, or it matches the row sum."""
        if self.period_net_lots is None:
            return True
        return sum(r.net_lots for r in self.rows) == self.period_net_lots

    @property
    def empty(self) -> bool:
        """No trading activity across the whole requested window."""
        return all(r.buy_lots == 0 and r.sell_lots == 0 for r in self.rows)


_ZCO0_HEADER_RE = re.compile(r"^(.*?)\s*»\s*(.+?)\((\d{3,4}[0-9A-Z]?)\)")


def parse_zco0(body: bytes | str) -> Zco0Page:
    html = body.decode("big5", "replace") if isinstance(body, bytes) else body
    if looks_blocked(html):
        raise DjhtmError("blocked / non-data response")

    rows: list[Zco0Row] = []
    for cells in _rows(html):
        m = _DATE_RE.match(cells[0]) if cells else None
        if not m or len(cells) < 5:
            continue
        buy, sell, net = _int(cells[1]), _int(cells[2]), _int(cells[4])
        rows.append(Zco0Row(_iso(*m.groups()), buy, sell, net))

    cs_m = _CHECKSUM_RE.search(html)
    period = _int(cs_m.group(1)) if cs_m else None

    stock_id = branch_name = ""
    hdr = _ZCO0_HEADER_RE.search(unescape(_TAG_RE.sub(" ", html)))
    if hdr:
        branch_name = " ".join(hdr.group(1).split())
        stock_id = hdr.group(3)

    if not rows and period is None:
        raise DjhtmError("no data rows and no checksum -- not a zco0 page")
    return Zco0Page(rows, period, stock_id, branch_name)


# --------------------------------------------------------------------------- #
# zco -- per stock, latest day, top-15 buy / top-15 sell
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ZcoBranchRow:
    branch_name: str
    buy_lots: int
    sell_lots: int
    net_lots: int      # buy_lots - sell_lots; negative on the sell side


@dataclass(frozen=True, slots=True)
class ZcoPage:
    stock_id: str
    data_date: str                 # ISO; "" if the page did not carry one
    branches: list[ZcoBranchRow] = field(default_factory=list)
    total_buy_lots: int | None = None
    total_sell_lots: int | None = None
    avg_buy_cost: float | None = None
    avg_sell_cost: float | None = None


def _looks_numeric(cell: str) -> bool:
    return bool(re.fullmatch(r"-?[\d,]+", (cell or "").strip()))


def parse_zco(body: bytes | str, *, stock_id: str = "") -> ZcoPage:
    html = body.decode("big5", "replace") if isinstance(body, bytes) else body
    if looks_blocked(html):
        raise DjhtmError("blocked / non-data response")

    plain = unescape(_TAG_RE.sub(" ", html))
    dm = _DATADATE_RE.search(plain)
    data_date = _iso(*dm.groups()) if dm else ""

    branches: list[ZcoBranchRow] = []
    for cells in _rows(html):
        # data rows carry two side-by-side 4-col blocks:
        # [name, buy, sell, net, pct%, name, buy, sell, net, pct%]
        if len(cells) < 9:
            continue
        if not (_looks_numeric(cells[1]) and _looks_numeric(cells[2])):
            continue
        left_name = cells[0].strip()
        if left_name and not left_name.startswith("合計"):
            b, s = _int(cells[1]), _int(cells[2])
            branches.append(ZcoBranchRow(left_name, b, s, b - s))
        if len(cells) >= 9 and _looks_numeric(cells[6]) and _looks_numeric(cells[7]):
            right_name = cells[5].strip()
            if right_name and not right_name.startswith("合計"):
                b, s = _int(cells[6]), _int(cells[7])
                branches.append(ZcoBranchRow(right_name, b, s, b - s))

    tb = _TOTAL_BUY_RE.search(plain)
    ts = _TOTAL_SELL_RE.search(plain)
    ab = _AVG_BUY_RE.search(plain)
    as_ = _AVG_SELL_RE.search(plain)
    return ZcoPage(
        stock_id=stock_id,
        data_date=data_date,
        branches=branches,
        total_buy_lots=_int(tb.group(1)) if tb else None,
        total_sell_lots=_int(ts.group(1)) if ts else None,
        avg_buy_cost=_num(ab.group(1)) if ab else None,
        avg_sell_cost=_num(as_.group(1)) if as_ else None,
    )
