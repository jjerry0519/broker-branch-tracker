"""分點勝率回測 -- 一個分點過去淨買超之後，股價往後 N 個交易日平均漲跌幅／上漲機率。

純函式：不碰網路，只吃「已經抓好的」逐日買賣超 + 收盤價序列。呼叫端（app/pages）
負責把 close price 抓到比查詢區間更後面（見 pages/3_分點總覽.py），這裡只做比對。

**進場時機（避免 look-ahead bias）**：券商分點買賣超是「當天收盤後才公布」的資料 --
你不可能用訊號當天的收盤價成交，因為那天收盤時你根本還看不到這筆資料。所以進場價預設
是訊號日 **+ entry_lag_days 個交易日**（預設 1 = 隔天）的收盤價，出場價再往後
horizon_days 個交易日，兩者都是「公布後才可能執行」的價格，回測結果才是可執行的。

**大盤基準**：給 ``benchmark_closes``（例如 0050 同一批日期的收盤價）時，額外算出
「超額報酬」= 個股報酬 - 大盤同期報酬，因為絕對報酬高不代表跑贏大盤。

這不是保證未來績效的工具，只是「這個分點過去買了之後，股價統計上通常怎麼走」的
描述性統計 -- 跟 FIFO 頁一樣要顯示免責聲明。
"""
from __future__ import annotations

from dataclasses import dataclass, field


DISCLAIMER = (
    "此為歷史統計，不代表未來績效：進場價為訊號公布後可執行的價格（非訊號當天收盤價），"
    "樣本數少時（例如個股單獨回測）結果不具代表性；分點買賣超為該分點多個帳戶淨額相抵後"
    "的結果，不代表單一操作者的真實決策。"
)


@dataclass(frozen=True, slots=True)
class Signal:
    date: str                       # 訊號日（分點淨買超當天）
    net_shares: int
    entry_date: str | None          # 訊號日 + entry_lag_days 個交易日
    entry_close: float | None
    exit_date: str | None           # entry_date + horizon_days 個交易日
    exit_close: float | None
    forward_return: float | None
    benchmark_return: float | None = None
    excess_return: float | None = None      # forward_return - benchmark_return


@dataclass(frozen=True, slots=True)
class BacktestResult:
    horizon_days: int
    entry_lag_days: int
    n_signals: int                  # 淨買超訊號日數（net_shares > 0）
    n_computable: int               # 其中進場/出場價都能取得、能算報酬的天數
    win_rate: float | None          # n_computable 中，正報酬的比例
    avg_return: float | None        # n_computable 的平均報酬率
    n_excess_computable: int = 0    # 其中連大盤基準都能算的天數
    win_rate_excess: float | None = None    # 贏過大盤的比例
    avg_excess_return: float | None = None  # 平均超額報酬
    signals: list[Signal] = field(default_factory=list)


def backtest_branch_buys(rows: list[tuple[str, int]], closes: dict[str, float],
                         all_trading_dates: list[str], *,
                         horizon_days: int = 5, entry_lag_days: int = 1,
                         benchmark_closes: dict[str, float] | None = None
                         ) -> BacktestResult:
    """``rows``: ``[(date, net_shares), ...]`` for one (stock, branch) pair (any
    order). ``closes``: ``{date: close_price}`` -- must cover the entry/exit
    dates (``entry_lag_days`` / ``horizon_days`` trading days after each signal),
    not the signal date itself (that price is never used). ``all_trading_dates``:
    the full ingested trading-day calendar (ascending), used to step forward in
    *trading* days, not calendar days. ``benchmark_closes``: same-shape map for
    a market proxy (e.g. 0050) -- when given, also computes excess return.
    """
    date_index = {d: i for i, d in enumerate(all_trading_dates)}
    signals: list[Signal] = []
    for date, net in sorted(rows):
        if net <= 0:
            continue
        entry_date = entry_close = exit_date = exit_close = None
        fwd = bret = excess = None
        idx = date_index.get(date)
        if idx is not None:
            entry_idx = idx + entry_lag_days
            if entry_idx < len(all_trading_dates):
                entry_date = all_trading_dates[entry_idx]
                entry_close = closes.get(entry_date)
                exit_idx = entry_idx + horizon_days
                if exit_idx < len(all_trading_dates):
                    exit_date = all_trading_dates[exit_idx]
                    exit_close = closes.get(exit_date)
        if entry_close is not None and exit_close is not None and entry_close > 0:
            fwd = exit_close / entry_close - 1
            if benchmark_closes is not None:
                b_entry = benchmark_closes.get(entry_date)
                b_exit = benchmark_closes.get(exit_date)
                if b_entry is not None and b_exit is not None and b_entry > 0:
                    bret = b_exit / b_entry - 1
                    excess = fwd - bret
        signals.append(Signal(date, net, entry_date, entry_close, exit_date,
                              exit_close, fwd, bret, excess))

    computable = [s for s in signals if s.forward_return is not None]
    n = len(computable)
    win_rate = (sum(1 for s in computable if s.forward_return > 0) / n) if n else None
    avg_return = (sum(s.forward_return for s in computable) / n) if n else None

    excess_computable = [s for s in signals if s.excess_return is not None]
    ne = len(excess_computable)
    win_rate_excess = ((sum(1 for s in excess_computable if s.excess_return > 0) / ne)
                       if ne else None)
    avg_excess_return = ((sum(s.excess_return for s in excess_computable) / ne)
                         if ne else None)

    return BacktestResult(horizon_days, entry_lag_days, len(signals), n,
                          win_rate, avg_return, ne, win_rate_excess,
                          avg_excess_return, signals)


def merge_results(results: list[BacktestResult]) -> BacktestResult:
    """Pool several stocks' :class:`BacktestResult` (same horizon/entry_lag) into
    one branch-level statistic -- page 3 runs one backtest per candidate stock
    and combines them into a single 分點-level win rate."""
    if not results:
        return BacktestResult(0, 0, 0, 0, None, None)
    horizon, lag = results[0].horizon_days, results[0].entry_lag_days
    all_signals = [s for r in results for s in r.signals]
    computable = [s for s in all_signals if s.forward_return is not None]
    n = len(computable)
    win_rate = (sum(1 for s in computable if s.forward_return > 0) / n) if n else None
    avg_return = (sum(s.forward_return for s in computable) / n) if n else None

    excess_computable = [s for s in all_signals if s.excess_return is not None]
    ne = len(excess_computable)
    win_rate_excess = ((sum(1 for s in excess_computable if s.excess_return > 0) / ne)
                       if ne else None)
    avg_excess_return = ((sum(s.excess_return for s in excess_computable) / ne)
                         if ne else None)

    return BacktestResult(horizon, lag, len(all_signals), n, win_rate, avg_return,
                          ne, win_rate_excess, avg_excess_return, all_signals)
