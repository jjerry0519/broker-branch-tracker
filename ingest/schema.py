"""``BranchFlow`` -- one branch's net flow in one stock on one trading day.

Populated from the SysJust ``zco0`` feed, which reports 張 (lots); the pipeline
stores shares = 張 x 1000. Because the source rounds buy / sell / net to whole
lots *independently*, ``net_shares`` (the source's own 買賣超 column x 1000) is
the most accurate figure and ``buy_shares - sell_shares`` can differ from it by
+-1000. Keep ``net_shares`` as the headline; treat buy/sell as +-1 張 detail.
``close_price`` is 0.0 on historical rows (joined per-day downstream).
"""
from __future__ import annotations

from dataclasses import astuple, dataclass, fields

import pyarrow as pa

_COLUMNS = ("date", "market", "stock_id", "branch_id", "branch_name",
           "buy_shares", "sell_shares", "net_shares", "close_price")


@dataclass(frozen=True, slots=True)
class BranchFlow:
    date: str
    market: str
    stock_id: str
    branch_id: str
    branch_name: str
    buy_shares: int
    sell_shares: int
    net_shares: int
    close_price: float


PARQUET_SCHEMA = pa.schema([
    ("date", pa.string()),
    ("market", pa.string()),
    ("stock_id", pa.string()),
    ("branch_id", pa.string()),
    ("branch_name", pa.string()),
    ("buy_shares", pa.int64()),
    ("sell_shares", pa.int64()),
    ("net_shares", pa.int64()),
    ("close_price", pa.float64()),
])


def flows_to_table(flows: list[BranchFlow]) -> pa.Table:
    cols = {name: [] for name in _COLUMNS}
    for f in flows:
        for name, value in zip(_COLUMNS, astuple(f)):
            cols[name].append(value)
    return pa.table(cols, schema=PARQUET_SCHEMA)


def table_to_flows(table: pa.Table) -> list[BranchFlow]:
    rows = table.to_pylist()
    return [BranchFlow(**{f.name: r[f.name] for f in fields(BranchFlow)}) for r in rows]
