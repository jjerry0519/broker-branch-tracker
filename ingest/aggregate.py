from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

from ingest.schema import BranchFlow


@dataclass(frozen=True, slots=True)
class RawRow:
    branch_id: str
    branch_name: str
    buy_shares: int
    sell_shares: int


def aggregate(raw_rows: list[RawRow], *, date: str, market: str,
              stock_id: str, close_price: float) -> list[BranchFlow]:
    acc: "OrderedDict[str, list]" = OrderedDict()
    for r in raw_rows:
        name, buy, sell = acc.get(r.branch_id, [r.branch_name, 0, 0])
        acc[r.branch_id] = [r.branch_name or name, buy + r.buy_shares, sell + r.sell_shares]

    flows = [
        BranchFlow(date, market, stock_id, bid, name, buy, sell, buy - sell, close_price)
        for bid, (name, buy, sell) in acc.items()
        if not (buy == 0 and sell == 0)
    ]
    flows.sort(key=lambda f: (-f.net_shares, f.branch_id))
    return flows
