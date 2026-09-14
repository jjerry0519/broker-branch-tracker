from app import close_price as cp


def test_ticker_suffix_by_market():
    assert cp._ticker("2330", "twse") == "2330.TW"
    assert cp._ticker("6488", "tpex") == "6488.TWO"


def test_get_closes_cache_miss_then_hit(tmp_path):
    db = str(tmp_path / "close.db")
    calls = []

    def fake_downloader(ticker, start, end):
        calls.append((ticker, start, end))
        return {"2026-09-08": 1000.0, "2026-09-09": 1010.0}

    got = cp.get_closes("2330", "twse", ["2026-09-08", "2026-09-09"],
                        db_path=db, downloader=fake_downloader)
    assert got == {"2026-09-08": 1000.0, "2026-09-09": 1010.0}
    assert calls == [("2330.TW", "2026-09-08", "2026-09-09")]

    # second call: fully cached, no downloader call at all
    calls.clear()
    got2 = cp.get_closes("2330", "twse", ["2026-09-08", "2026-09-09"],
                         db_path=db, downloader=fake_downloader)
    assert got2 == got
    assert calls == []


def test_get_closes_partial_cache_only_fetches_missing_span(tmp_path):
    db = str(tmp_path / "close.db")
    cp.fetch_and_cache_closes("2330", "twse", "2026-09-08", "2026-09-08",
                              db_path=db,
                              downloader=lambda t, s, e: {"2026-09-08": 1000.0})
    calls = []

    def fake(ticker, start, end):
        calls.append((start, end))
        return {"2026-09-09": 1010.0, "2026-09-10": 1015.0}

    got = cp.get_closes("2330", "twse",
                        ["2026-09-08", "2026-09-09", "2026-09-10"],
                        db_path=db, downloader=fake)
    assert got == {"2026-09-08": 1000.0, "2026-09-09": 1010.0, "2026-09-10": 1015.0}
    assert calls == [("2026-09-09", "2026-09-10")]   # only the missing span


def test_get_closes_empty_dates():
    assert cp.get_closes("2330", "twse", []) == {}


def test_get_closes_downloader_returns_nothing(tmp_path):
    db = str(tmp_path / "close.db")
    got = cp.get_closes("9999", "twse", ["2026-09-08"], db_path=db,
                        downloader=lambda t, s, e: {})
    assert got == {}


def test_cache_is_keyed_per_stock(tmp_path):
    db = str(tmp_path / "close.db")
    cp.fetch_and_cache_closes("2330", "twse", "2026-09-08", "2026-09-08",
                              db_path=db,
                              downloader=lambda t, s, e: {"2026-09-08": 1000.0})
    other = cp.cached_closes("2317", ["2026-09-08"], db_path=db)
    assert other == {}


# --------------------------------------------------------------------------- #
# volume -- independent cache, same pattern
# --------------------------------------------------------------------------- #
def test_get_volumes_cache_miss_then_hit(tmp_path):
    db = str(tmp_path / "close.db")
    calls = []

    def fake(ticker, start, end):
        calls.append((ticker, start, end))
        return {"2026-09-08": 12_345_000, "2026-09-09": 9_876_000}

    got = cp.get_volumes("2330", "twse", ["2026-09-08", "2026-09-09"],
                         db_path=db, downloader=fake)
    assert got == {"2026-09-08": 12_345_000, "2026-09-09": 9_876_000}
    assert calls == [("2330.TW", "2026-09-08", "2026-09-09")]

    calls.clear()
    got2 = cp.get_volumes("2330", "twse", ["2026-09-08", "2026-09-09"],
                          db_path=db, downloader=fake)
    assert got2 == got and calls == []


def test_volume_and_close_caches_are_independent(tmp_path):
    db = str(tmp_path / "close.db")
    cp.fetch_and_cache_closes("2330", "twse", "2026-09-08", "2026-09-08",
                              db_path=db, downloader=lambda t, s, e: {"2026-09-08": 1000.0})
    # volume cache untouched by the close fetch
    assert cp.cached_volumes("2330", ["2026-09-08"], db_path=db) == {}


def test_get_volumes_empty_dates():
    assert cp.get_volumes("2330", "twse", []) == {}
