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
