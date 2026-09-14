import pytest

from app.backtest import backtest_branch_buys, merge_results


DATES = [f"2026-01-{d:02d}" for d in range(1, 21)]   # 20 trading days, ascending


def test_no_buy_signals_gives_no_computable_result():
    r = backtest_branch_buys([("2026-01-05", -100)], {"2026-01-05": 10.0}, DATES,
                             horizon_days=5)
    assert r.n_signals == 0 and r.n_computable == 0
    assert r.win_rate is None and r.avg_return is None


def test_zero_net_is_not_a_signal():
    r = backtest_branch_buys([("2026-01-05", 0)], {"2026-01-05": 10.0}, DATES,
                             horizon_days=5)
    assert r.n_signals == 0


def test_win_when_price_higher_after_horizon():
    rows = [("2026-01-05", 100)]     # idx 4 -> +5 trading days -> idx 9 = 01-10
    closes = {"2026-01-05": 10.0, "2026-01-10": 12.0}
    r = backtest_branch_buys(rows, closes, DATES, horizon_days=5)
    assert r.n_signals == 1 and r.n_computable == 1
    assert r.win_rate == 1.0
    assert r.avg_return == pytest.approx(0.2)
    assert r.signals[0].exit_close == 12.0


def test_loss_when_price_lower_after_horizon():
    rows = [("2026-01-05", 100)]
    closes = {"2026-01-05": 10.0, "2026-01-10": 9.0}
    r = backtest_branch_buys(rows, closes, DATES, horizon_days=5)
    assert r.win_rate == 0.0
    assert r.avg_return == pytest.approx(-0.1)


def test_missing_exit_close_is_not_computable():
    rows = [("2026-01-05", 100)]
    closes = {"2026-01-05": 10.0}                     # no 01-10 price
    r = backtest_branch_buys(rows, closes, DATES, horizon_days=5)
    assert r.n_signals == 1 and r.n_computable == 0
    assert r.win_rate is None


def test_signal_too_close_to_end_of_calendar_is_not_computable():
    rows = [(DATES[-1], 100)]                          # last date, no room for +5
    closes = {DATES[-1]: 10.0}
    r = backtest_branch_buys(rows, closes, DATES, horizon_days=5)
    assert r.n_signals == 1 and r.n_computable == 0


def test_missing_entry_close_skips_the_signal_entirely():
    rows = [("2026-01-05", 100)]
    closes = {}                                        # no entry price at all
    r = backtest_branch_buys(rows, closes, DATES, horizon_days=5)
    assert r.n_signals == 0                             # never even counted


def test_multiple_signals_average_correctly():
    rows = [("2026-01-01", 50), ("2026-01-05", 100)]
    closes = {"2026-01-01": 10.0, "2026-01-06": 11.0,   # idx0+5=idx5=01-06 -> +10%
             "2026-01-05": 20.0, "2026-01-10": 18.0}    # idx4+5=idx9=01-10 -> -10%
    r = backtest_branch_buys(rows, closes, DATES, horizon_days=5)
    assert r.n_signals == 2 and r.n_computable == 2
    assert r.win_rate == pytest.approx(0.5)
    assert r.avg_return == pytest.approx(0.0)


def test_merge_results_pools_signals_across_stocks():
    r1 = backtest_branch_buys([("2026-01-05", 100)],
                              {"2026-01-05": 10.0, "2026-01-10": 12.0}, DATES, horizon_days=5)
    r2 = backtest_branch_buys([("2026-01-05", 100)],
                              {"2026-01-05": 10.0, "2026-01-10": 8.0}, DATES, horizon_days=5)
    merged = merge_results([r1, r2])
    assert merged.n_signals == 2 and merged.n_computable == 2
    assert merged.win_rate == pytest.approx(0.5)
    assert merged.horizon_days == 5


def test_merge_results_empty_list():
    m = merge_results([])
    assert m.n_signals == 0 and m.win_rate is None
