"""分點勝率回測 -- 一個分點過去淨買超之後，股價往後 N 個交易日平均漲跌幅／上漲機率。

純函式：不碰網路，只吃「已經抓好的」逐日買賣超 + 收盤價序列。呼叫端（app/pages）
負責把 close price 抓到比查詢區間更後面（見 pages/3_分點總覽.py），這裡只做比對。

這不是保證未來績效的工具，只是「這個分點過去買了之後，股價統計上通常怎麼走」的
描述性統計 -- 跟 FIFO 頁一樣要顯示免責聲明。
"""
from __future__ import annotations

from dataclasses import dataclass, field


DISCLAIMER = (
    "此為歷史統計，不代表未來績效：樣本數少時（例如個股單獨回測）結果不具代表性；"
    "分點買賣超為該分點多個帳戶淨額相抵後的結果，不代表單一操作者的真實決策。"
)


@dataclass(frozen=True, slots=True)
class Signal:
    date: str
    net_shares: int
    entry_close: float
    exit_close: float | None       # None -- horizon 超出目前資料範圍，無法計算
    forward_return: float | None   # (exit/entry - 1)，None 同上


@dataclass(frozen=True, slots=True)
class BacktestResult:
    horizon_days: int
    n_signals: int                 # 淨買超訊號日數（net_shares > 0）
    n_computable: int              # 其中horizon天後收盤價可取得、能算報酬的天數
    win_rate: float | None         # n_computable 中，正報酬的比例
    avg_return: float | None       # n_computable 的平均報酬率
    signals: list[Signal] = field(default_factory=list)


def backtest_branch_buys(rows: list[tuple[str, int]], closes: dict[str, float],
                         all_trading_dates: list[str], *,
                         horizon_days: int = 5) -> BacktestResult:
    """``rows``: ``[(date, net_shares), ...]`` for one (stock, branch) pair (any
    order). ``closes``: ``{date: close_price}`` -- must cover every date in
    ``rows`` *and* ideally extend ``horizon_days`` trading days past the last
    signal date (dates beyond what's available simply can't be scored).
    ``all_trading_dates``: the full ingested trading-day calendar (ascending),
    used to step ``horizon_days`` *trading* days forward from a signal date
    (not calendar days).
    """
    date_index = {d: i for i, d in enumerate(all_trading_dates)}
    signals: list[Signal] = []
    for date, net in sorted(rows):
        if net <= 0:
            continue
        entry = closes.get(date)
        if entry is None:
            continue
        idx = date_index.get(date)
        exit_close = exit_ret = None
        if idx is not None and idx + horizon_days < len(all_trading_dates):
            exit_date = all_trading_dates[idx + horizon_days]
            exit_close = closes.get(exit_date)
            if exit_close is not None and entry > 0:
                exit_ret = exit_close / entry - 1
        signals.append(Signal(date, net, entry, exit_close, exit_ret))

    computable = [s for s in signals if s.forward_return is not None]
    n = len(computable)
    win_rate = (sum(1 for s in computable if s.forward_return > 0) / n) if n else None
    avg_return = (sum(s.forward_return for s in computable) / n) if n else None
    return BacktestResult(horizon_days, len(signals), n, win_rate, avg_return, signals)


def merge_results(results: list[BacktestResult]) -> BacktestResult:
    """Pool several stocks' :class:`BacktestResult` (same ``horizon_days``) into
    one branch-level statistic -- page 3 runs one backtest per candidate stock
    and combines them into a single 分點-level win rate."""
    if not results:
        return BacktestResult(0, 0, 0, None, None, [])
    horizon = results[0].horizon_days
    all_signals = [s for r in results for s in r.signals]
    computable = [s for s in all_signals if s.forward_return is not None]
    n = len(computable)
    win_rate = (sum(1 for s in computable if s.forward_return > 0) / n) if n else None
    avg_return = (sum(s.forward_return for s in computable) / n) if n else None
    return BacktestResult(horizon, len(all_signals), n, win_rate, avg_return, all_signals)
