from __future__ import annotations

import re
from dataclasses import dataclass

from ingest.aggregate import RawRow

_DETAIL_FIELDS = ["序號", "券商", "價格", "買進股數", "賣出股數"]


class TpexRetry(RuntimeError):
    """brokerBS did not return stat == 'ok'; caller should re-issue with a fresh token."""


@dataclass(frozen=True, slots=True)
class BrokerBsPage:
    stock_id: str
    trade_date: str
    close_price: float
    rows: list[RawRow]


def _int(cell) -> int:
    s = str(cell).strip().replace(",", "")
    return int(s) if s.lstrip("-").isdigit() else 0


def _num(cell) -> float:
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", str(cell))
    return float(m.group(0).replace(",", "")) if m else 0.0


def _roc_to_iso(s: str) -> str:
    y, m, d = (int(n) for n in re.findall(r"\d+", s)[:3])
    return f"{y + 1911:04d}-{m:02d}-{d:02d}"


def parse_brokerbs(payload: dict) -> BrokerBsPage:
    if not isinstance(payload, dict) or payload.get("stat") != "ok":
        raise TpexRetry(str(payload.get("stat") if isinstance(payload, dict) else payload))

    tables = payload.get("tables") or []
    summary = next((t for t in tables if "收盤價" in (t.get("fields") or [])), None)
    detail = next((t for t in tables if (t.get("fields") or []) == _DETAIL_FIELDS), None)
    if summary is None or detail is None:
        raise TpexRetry("unexpected tables shape")

    srow = summary["data"][0]
    sf = summary["fields"]
    stock_id = str(srow[sf.index("證券代號")]).split()[0]
    trade_date = _roc_to_iso(str(srow[sf.index("交易日期")]))
    close_price = _num(srow[sf.index("收盤價")])

    rows: list[RawRow] = []
    for rec in detail["data"]:
        broker = str(rec[1]).strip()
        if " " not in broker:
            continue
        bid, bname = broker.split(None, 1)
        rows.append(RawRow(bid, bname.strip(), _int(rec[3]), _int(rec[4])))
    return BrokerBsPage(stock_id, trade_date, close_price, rows)
