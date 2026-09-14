import pytest

from app.backtest import backtest_branch_buys, merge_results


DATES = [f"2026-01-{d:02d}" for d in range(1, 21)]   # 20 trading days, ascending
# idx: 01-01=0 01-02=1 ... 01-20=19


def test_no_buy_signals_gives_no_computable_result():
    r = backtest_branch_buys([("2026-01-05", -100)], {}, DATES, horizon_days=5)
    assert r.n_signals == 0 and r.n_computable == 0
    assert r.win_rate is None and r.avg_return is None


def test_zero_net_is_not_a_signal():
    r = backtest_branch_buys([("2026-01-05", 0)], {}, DATES, horizon_days=5)
    assert r.n_signals == 0


def test_entry_is_lagged_past_the_signal_day_by_default():
    # signal 01-05 (idx4); default entry_lag_days=1 -> entry 01-06 (idx5);
    # horizon 5 -> exit idx10 = 01-11
    rows = [("2026-01-05", 100)]
    closes = {"2026-01-05": 999.0,      # signal-day close -- must NEVER be used
             "2026-01-06": 10.0, "2026-01-11": 12.0}
    r = backtest_branch_buys(rows, closes, DATES, horizon_days=5)
    assert r.n_signals == 1 and r.n_computable == 1
    s = r.signals[0]
    assert s.entry_date == "2026-01-06" and s.entry_close == 10.0
    assert s.exit_date == "2026-01-11" and s.exit_close == 12.0
    assert s.forward_return == pytest.approx(0.2)
    assert r.entry_lag_days == 1


def test_entry_lag_zero_uses_signal_day_as_entry():
    rows = [("2026-01-05", 100)]
    closes = {"2026-01-05": 10.0, "2026-01-10": 12.0}
    r = backtest_branch_buys(rows, closes, DATES, horizon_days=5, entry_lag_days=0)
    assert r.signals[0].entry_date == "2026-01-05"
    assert r.signals[0].exit_date == "2026-01-10"
    assert r.win_rate == 1.0


def test_missing_entry_or_exit_close_is_not_computable_but_still_a_signal():
    rows = [("2026-01-05", 100)]
    closes = {}                                          # nothing available
    r = backtest_branch_buys(rows, closes, DATES, horizon_days=5)
    assert r.n_signals == 1 and r.n_computable == 0
    assert r.win_rate is None
    assert r.signals[0].entry_date == "2026-01-06"        # still computed
    assert r.signals[0].entry_close is None


def test_signal_too_close_to_end_of_calendar_is_not_computable():
    rows = [(DATES[-1], 100)]                              # last date, no room to lag+horizon
    r = backtest_branch_buys(rows, {}, DATES, horizon_days=5)
    assert r.n_signals == 1 and r.n_computable == 0
    assert r.signals[0].entry_date is None


def test_loss_when_price_lower_after_horizon():
    rows = [("2026-01-05", 100)]
    closes = {"2026-01-06": 10.0, "2026-01-11": 9.0}
    r = backtest_branch_buys(rows, closes, DATES, horizon_days=5)
    assert r.win_rate == 0.0
    assert r.avg_return == pytest.approx(-0.1)


def test_multiple_signals_average_correctly():
    rows = [("2026-01-01", 50), ("2026-01-05", 100)]
    # 01-01 idx0 -> entry idx1=01-02, exit idx6=01-07
    # 01-05 idx4 -> entry idx5=01-06, exit idx10=01-11
    closes = {"2026-01-02": 10.0, "2026-01-07": 11.0,      # +10%
             "2026-01-06": 20.0, "2026-01-11": 18.0}       # -10%
    r = backtest_branch_buys(rows, closes, DATES, horizon_days=5)
    assert r.n_signals == 2 and r.n_computable == 2
    assert r.win_rate == pytest.approx(0.5)
    assert r.avg_return == pytest.approx(0.0)


# --------------------------------------------------------------------------- #
# benchmark / excess return
# --------------------------------------------------------------------------- #
def test_excess_return_vs_benchmark():
    rows = [("2026-01-05", 100)]       # entry 01-06, exit 01-11
    closes = {"2026-01-06": 10.0, "2026-01-11": 11.0}      # stock +10%
    bench = {"2026-01-06": 100.0, "2026-01-11": 104.0}     # market +4%
    r = backtest_branch_buys(rows, closes, DATES, horizon_days=5,
                             benchmark_closes=bench)
    assert r.n_excess_computable == 1
    assert r.win_rate_excess == 1.0
    assert r.avg_excess_return == pytest.approx(0.06, abs=1e-9)
    assert r.signals[0].benchmark_return == pytest.approx(0.04)
    assert r.signals[0].excess_return == pytest.approx(0.06)


def test_beats_absolute_but_loses_to_benchmark():
    rows = [("2026-01-05", 100)]
    closes = {"2026-01-06": 10.0, "2026-01-11": 10.5}       # stock +5%
    bench = {"2026-01-06": 100.0, "2026-01-11": 110.0}      # market +10%
    r = backtest_branch_buys(rows, closes, DATES, horizon_days=5,
                             benchmark_closes=bench)
    assert r.win_rate == 1.0                                # positive absolute return
    assert r.win_rate_excess == 0.0                         # but trailed the market
    assert r.avg_excess_return == pytest.approx(-0.05)


def test_no_benchmark_given_leaves_excess_fields_empty():
    rows = [("2026-01-05", 100)]
    closes = {"2026-01-06": 10.0, "2026-01-11": 11.0}
    r = backtest_branch_buys(rows, closes, DATES, horizon_days=5)
    assert r.n_excess_computable == 0
    assert r.win_rate_excess is None and r.avg_excess_return is None
    assert r.signals[0].excess_return is None


def test_missing_benchmark_price_leaves_this_signal_unexcessed_but_still_computable():
    rows = [("2026-01-05", 100)]
    closes = {"2026-01-06": 10.0, "2026-01-11": 11.0}
    bench = {"2026-01-06": 100.0}                            # missing exit price
    r = backtest_branch_buys(rows, closes, DATES, horizon_days=5,
                             benchmark_closes=bench)
    assert r.n_computable == 1                                # raw return still fine
    assert r.n_excess_computable == 0
    assert r.signals[0].forward_return is not None
    assert r.signals[0].excess_return is None


# --------------------------------------------------------------------------- #
# merge_results
# --------------------------------------------------------------------------- #
def test_merge_results_pools_signals_across_stocks():
    r1 = backtest_branch_buys([("2026-01-05", 100)],
                              {"2026-01-06": 10.0, "2026-01-11": 12.0}, DATES,
                              horizon_days=5)
    r2 = backtest_branch_buys([("2026-01-05", 100)],
                              {"2026-01-06": 10.0, "2026-01-11": 8.0}, DATES,
                              horizon_days=5)
    merged = merge_results([r1, r2])
    assert merged.n_signals == 2 and merged.n_computable == 2
    assert merged.win_rate == pytest.approx(0.5)
    assert merged.horizon_days == 5 and merged.entry_lag_days == 1


def test_merge_results_pools_excess_return_too():
    bench = {"2026-01-06": 100.0, "2026-01-11": 105.0}      # market +5%
    r1 = backtest_branch_buys([("2026-01-05", 100)],
                              {"2026-01-06": 10.0, "2026-01-11": 12.0}, DATES,
                              horizon_days=5, benchmark_closes=bench)   # +20% -> +15% excess
    r2 = backtest_branch_buys([("2026-01-05", 100)],
                              {"2026-01-06": 10.0, "2026-01-11": 9.0}, DATES,
                              horizon_days=5, benchmark_closes=bench)   # -10% -> -15% excess
    merged = merge_results([r1, r2])
    assert merged.n_excess_computable == 2
    assert merged.avg_excess_return == pytest.approx(0.0, abs=1e-9)
    assert merged.win_rate_excess == pytest.approx(0.5)


def test_merge_results_empty_list():
    m = merge_results([])
    assert m.n_signals == 0 and m.win_rate is None
