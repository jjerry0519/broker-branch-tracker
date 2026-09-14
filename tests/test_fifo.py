import pytest

from app.fifo import DISCLAIMER, FifoResult, compute_fifo


def test_empty_rows():
    r = compute_fifo([])
    assert r.avg_cost is None
    assert r.est_inventory_shares == 0
    assert (r.period_buy_shares, r.period_sell_shares, r.period_net_shares) == (0, 0, 0)
    assert r.had_oversell is False
    assert r.trace == []


def test_single_buy_day():
    r = compute_fifo([("2026-01-05", 1000, 100.0)])
    assert r.avg_cost == pytest.approx(100.0)
    assert r.est_inventory_shares == 1000
    assert r.period_buy_shares == 1000 and r.period_sell_shares == 0
    assert r.had_oversell is False
    assert len(r.trace) == 1
    assert r.trace[0].inventory_shares == 1000


def test_two_buys_partial_sell_consumes_oldest_first():
    rows = [
        ("2026-01-05", 1000, 100.0),   # buy 1000 @ 100
        ("2026-01-06", 500, 120.0),    # buy 500 @ 120
        ("2026-01-07", -800, 110.0),   # sell 800 -> eats 800 of the 100-lot (oldest first)
    ]
    r = compute_fifo(rows)
    # remaining: 200 @ 100 + 500 @ 120 = 700 shares
    assert r.est_inventory_shares == 700
    assert r.avg_cost == pytest.approx((200 * 100.0 + 500 * 120.0) / 700)
    assert r.period_buy_shares == 1500 and r.period_sell_shares == 800
    assert r.period_net_shares == 700
    assert r.had_oversell is False


def test_sell_exact_full_inventory_then_rebuy():
    rows = [
        ("2026-01-05", 1000, 100.0),
        ("2026-01-06", -1000, 105.0),   # exactly empties the queue
        ("2026-01-07", 500, 90.0),
    ]
    r = compute_fifo(rows)
    assert r.est_inventory_shares == 500
    assert r.avg_cost == pytest.approx(90.0)
    assert r.had_oversell is False


def test_oversell_discards_excess_and_flags_day():
    rows = [
        ("2026-01-05", 1000, 100.0),
        ("2026-01-06", -1500, 105.0),   # only 1000 available -> 500 discarded, flagged
    ]
    r = compute_fifo(rows)
    assert r.est_inventory_shares == 0
    assert r.avg_cost is None
    assert r.had_oversell is True
    assert r.trace[-1].oversold is True
    assert r.period_sell_shares == 1500          # raw magnitude, not clamped
    assert r.period_buy_shares == 1000


def test_oversell_with_nothing_in_queue_at_all():
    r = compute_fifo([("2026-01-05", -400, 100.0)])
    assert r.had_oversell is True
    assert r.est_inventory_shares == 0
    assert r.trace[0].oversold is True


def test_zero_net_days_are_skipped_but_still_traced():
    rows = [
        ("2026-01-05", 1000, 100.0),
        ("2026-01-06", 0, 999.0),        # no-op day
        ("2026-01-07", -300, 110.0),
    ]
    r = compute_fifo(rows)
    assert r.est_inventory_shares == 700
    assert len(r.trace) == 3
    assert r.trace[1].net_shares == 0 and r.trace[1].oversold is False
    assert r.trace[1].inventory_shares == 1000    # unchanged by the zero day


def test_rows_are_sorted_by_date_regardless_of_input_order():
    rows = [
        ("2026-01-07", -300, 110.0),
        ("2026-01-05", 1000, 100.0),
    ]
    r = compute_fifo(rows)
    assert [t.date for t in r.trace] == ["2026-01-05", "2026-01-07"]
    assert r.est_inventory_shares == 700


def test_weighted_avg_cost_across_two_remaining_lots():
    rows = [
        ("2026-01-01", 1000, 100.0),
        ("2026-01-02", 1000, 200.0),
        # no sells -> both lots remain: avg = (1000*100 + 1000*200) / 2000 = 150
    ]
    r = compute_fifo(rows)
    assert r.avg_cost == pytest.approx(150.0)
    assert r.est_inventory_shares == 2000


def test_disclaimer_is_the_spec_text_and_flags_simplified_and_non_real():
    assert "簡化估算" in DISCLAIMER
    assert "FIFO" in DISCLAIMER
    assert "非真實成交均價" in DISCLAIMER


def test_result_is_frozen_dataclass_shape():
    r = compute_fifo([("2026-01-01", 100, 10.0)])
    assert isinstance(r, FifoResult)
    with pytest.raises(AttributeError):
        r.avg_cost = 5.0   # type: ignore[misc]
