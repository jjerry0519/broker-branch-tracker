from ingest import schedule as sc


def test_shard_size_ceil_and_floor():
    assert sc.shard_size(1900, 20) == 95
    assert sc.shard_size(1901, 20) == 96          # ceil
    assert sc.shard_size(5, 20) == 1              # never zero
    assert sc.shard_size(100, 0) == 100           # cycle_days floored to 1


def test_next_shard_picks_oldest_then_unseen_first():
    stocks = [f"{i:04d}" for i in range(40)]
    state = {s: "2026-01-10" for s in stocks}
    state["0005"] = "2026-01-01"        # oldest
    del state["0007"]                    # never refreshed -> "" sorts first
    shard = sc.next_shard(stocks, state, cycle_days=20)   # size 2
    assert shard == ["0007", "0005"]


def test_next_shard_is_deterministic_and_rotates():
    stocks = [f"{i:03d}" for i in range(10)]
    state: dict[str, str] = {}
    seen: list[str] = []
    target = "2026-03-02"
    for _ in range(5):                       # cycle_days 5 -> shard size 2
        shard = sc.next_shard(stocks, state, cycle_days=5)
        seen += shard
        state = sc.mark_done(state, shard, target)
    assert sorted(seen) == stocks            # every stock handled exactly once


def test_window_for_never_refreshed_is_clamped():
    d_from, d_to = sc.window_for("2330", {}, target="2026-09-10",
                                 max_lookback_days=45)
    assert d_to == "2026-09-10"
    assert d_from == "2026-07-27"            # target - 45d


def test_window_for_incremental_is_day_after_through():
    st = {"2330": "2026-09-05"}
    d_from, d_to = sc.window_for("2330", st, target="2026-09-10")
    assert (d_from, d_to) == ("2026-09-06", "2026-09-10")


def test_window_for_stale_beyond_lookback_is_clamped():
    st = {"2330": "2026-01-01"}
    d_from, _ = sc.window_for("2330", st, target="2026-09-10", max_lookback_days=45)
    assert d_from == "2026-07-27"


def test_window_for_up_to_date_collapses():
    st = {"2330": "2026-09-10"}
    d_from, d_to = sc.window_for("2330", st, target="2026-09-10")
    assert d_from == d_to == "2026-09-10"


def test_mark_done_only_advances():
    st = {"a": "2026-09-10", "b": "2026-09-01"}
    out = sc.mark_done(st, ["a", "b", "c"], "2026-09-05")
    assert out == {"a": "2026-09-10", "b": "2026-09-05", "c": "2026-09-05"}
    assert st == {"a": "2026-09-10", "b": "2026-09-01"}      # input untouched


def test_stale_stocks():
    st = {"a": "2026-08-01", "b": "2026-09-08", "c": ""}
    stale = sc.stale_stocks(["a", "b", "c"], st, target="2026-09-10",
                            cycle_days=20)
    assert stale == ["a", "c"]              # b within 20d, a and never-seen are behind


def test_state_round_trip(tmp_path):
    p = str(tmp_path / "refresh_state.json")
    st = {"2330": "2026-09-10", "6488": "2026-09-08"}
    sc.save_state(st, p)
    assert sc.load_state(p) == st
    assert sc.load_state(str(tmp_path / "missing.json")) == {}
