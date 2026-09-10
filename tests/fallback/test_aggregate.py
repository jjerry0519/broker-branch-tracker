from fallback.aggregate import RawRow, aggregate


def test_aggregate_sums_price_levels_and_computes_net():
    raw = [
        RawRow("9200", "凱基台北", 1000, 0),
        RawRow("9200", "凱基台北", 2000, 500),
        RawRow("1440", "美林", 0, 3000),
    ]
    out = aggregate(raw, date="2026-09-03", market="twse",
                    stock_id="2330", close_price=1085.0)
    assert [(f.branch_id, f.buy_shares, f.sell_shares, f.net_shares) for f in out] == [
        ("9200", 3000, 500, 2500),
        ("1440", 0, 3000, -3000),
    ]
    assert all(f.date == "2026-09-03" and f.stock_id == "2330"
               and f.market == "twse" and f.close_price == 1085.0 for f in out)


def test_aggregate_drops_zero_activity_rows():
    raw = [RawRow("5555", "空手", 0, 0), RawRow("9200", "凱基台北", 10, 0)]
    out = aggregate(raw, date="2026-09-03", market="twse",
                    stock_id="2330", close_price=1.0)
    assert [f.branch_id for f in out] == ["9200"]


def test_aggregate_empty_input_returns_empty():
    assert aggregate([], date="2026-09-03", market="tpex",
                     stock_id="6488", close_price=1.0) == []
