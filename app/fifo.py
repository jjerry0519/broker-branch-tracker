"""FIFO average-cost estimation -- design spec Sec.6, verbatim algorithm.

Input is one ``(stock, branch)`` pair's daily net flow over a date range:
each day's ``net_shares`` (buy_shares - sell_shares) is treated as one trade at
that day's ``close_price``. This is a **simplified estimate**, not a real
average cost -- see :data:`DISCLAIMER`, which every page showing FIFO output
must display verbatim.

Pure module: no I/O, no network. ``ingest`` never imports this (FIFO is a
query-time view over the stored flows, not something we bake into storage).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


DISCLAIMER = (
    "此為簡化估算：以每日買賣超 × 當日收盤價作為交易紀錄，並以 FIFO（先進先出）配對計算。"
    "券商分點買賣超為該分點多個帳戶淨額相抵後的結果，無法反映真實單筆成交價格，"
    "也看不到本工具上線前既有的部位。此數字僅供相對比較與趨勢觀察，非真實成交均價。"
)


@dataclass(frozen=True, slots=True)
class DayTrace:
    date: str
    net_shares: int
    close_price: float
    oversold: bool
    inventory_shares: int      # cumulative inventory AFTER this day
    running_avg_cost: float | None    # avg cost of the remaining lots AFTER this day


@dataclass(frozen=True, slots=True)
class FifoResult:
    avg_cost: float | None
    est_inventory_shares: int
    period_buy_shares: int
    period_sell_shares: int
    period_net_shares: int
    had_oversell: bool
    trace: list[DayTrace] = field(default_factory=list)


def compute_fifo(rows: list[tuple[str, int, float]]) -> FifoResult:
    """``rows``: ``[(date, net_shares, close_price), ...]`` for one (stock,
    branch), *any* order -- sorted by date ascending internally. Zero-net days
    are skipped (per spec Sec.6 step 2). A sell that exceeds the queue's current
    total is a no-op past the available shares -- the excess is discarded and
    that day's ``oversold`` flag is set (the tool has no visibility into
    pre-launch inventory).
    """
    ordered = sorted(rows, key=lambda r: r[0])
    lots: deque[list[float]] = deque()   # each: [qty, price], mutated in place
    buy_total = sell_total = 0
    had_oversell = False
    trace: list[DayTrace] = []

    for date, net, price in ordered:
        oversold = False
        if net > 0:
            lots.append([float(net), price])
            buy_total += net
        elif net < 0:
            sell_total += -net
            remaining = -net
            while remaining > 0 and lots:
                lot = lots[0]
                take = min(lot[0], remaining)
                lot[0] -= take
                remaining -= take
                if lot[0] <= 0:
                    lots.popleft()
            if remaining > 0:
                oversold = had_oversell = True
        # net == 0 -> nothing to do, but still emit a trace row

        inv = sum(lot[0] for lot in lots)
        cost_sum = sum(lot[0] * lot[1] for lot in lots)
        avg = (cost_sum / inv) if inv > 0 else None
        trace.append(DayTrace(date, net, price, oversold, int(round(inv)), avg))

    inv = sum(lot[0] for lot in lots)
    cost_sum = sum(lot[0] * lot[1] for lot in lots)
    avg_cost = (cost_sum / inv) if inv > 0 else None

    return FifoResult(
        avg_cost=avg_cost,
        est_inventory_shares=int(round(inv)),
        period_buy_shares=buy_total,
        period_sell_shares=sell_total,
        period_net_shares=buy_total - sell_total,
        had_oversell=had_oversell,
        trace=trace,
    )
