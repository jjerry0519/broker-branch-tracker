from pathlib import Path

import httpx
import pytest

from app import data
from ingest.schema import BranchFlow
from ingest.storage import write_parquet


# --------------------------------------------------------------------------- #
# trading_dates -- pure window selection
# --------------------------------------------------------------------------- #
MANIFEST = {"days": {d: {} for d in
           ["2026-08-31", "2026-09-01", "2026-09-02", "2026-09-03",
            "2026-09-04", "2026-09-07", "2026-09-08", "2026-09-09"]}}


def test_trading_dates_no_args_returns_all_sorted():
    assert data.trading_dates(MANIFEST) == sorted(MANIFEST["days"])


def test_trading_dates_last_n_ending_at_latest():
    got = data.trading_dates(MANIFEST, n=3)
    assert got == sorted(MANIFEST["days"])[-3:]


def test_trading_dates_last_n_ending_at_given_end():
    got = data.trading_dates(MANIFEST, n=3, end="2026-09-02")
    assert got == ["2026-08-31", "2026-09-01", "2026-09-02"]


def test_trading_dates_explicit_range_clips_to_available():
    got = data.trading_dates(MANIFEST, start="2026-09-01", end="2026-09-04")
    assert got == ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"]


def test_trading_dates_empty_manifest():
    assert data.trading_dates({"days": {}}) == []


# --------------------------------------------------------------------------- #
# _asset_id_from_url
# --------------------------------------------------------------------------- #
def test_asset_id_from_url_parses_owner_repo_tag_file():
    url = ("https://github.com/jjerry0519/broker-branch-tracker-data/"
          "releases/download/data-2026-09/2026-09-09.parquet")
    owner_repo, tag, fname = data._asset_id_from_url(url)
    assert owner_repo == "jjerry0519/broker-branch-tracker-data"
    assert tag == "data-2026-09"
    assert fname == "2026-09-09.parquet"


def test_asset_id_from_url_rejects_non_release_url():
    assert data._asset_id_from_url("https://example.com/foo.parquet") is None


# --------------------------------------------------------------------------- #
# download_day -- mocked GitHub API, verifies caching
# --------------------------------------------------------------------------- #
MANIFEST_ONE = {"days": {"2026-09-09": {
    "flows": ("https://github.com/o/data-repo/releases/download/"
             "data-2026-09/2026-09-09.parquet")}}}


def test_download_day_fetches_and_caches(tmp_path):
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        if "releases/tags/" in str(req.url):
            return httpx.Response(200, json={
                "assets": [{"name": "2026-09-09.parquet", "id": 999}]})
        if "releases/assets/999" in str(req.url):
            return httpx.Response(200, content=b"PARQUETBYTES")
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    p = data.download_day("2026-09-09", MANIFEST_ONE, token="tok",
                          cache_dir=str(tmp_path), client=client)
    assert p is not None and p.read_bytes() == b"PARQUETBYTES"
    assert len(calls) == 2                     # tag lookup + asset download

    # second call: served from cache, no HTTP at all
    calls.clear()
    p2 = data.download_day("2026-09-09", MANIFEST_ONE, token="tok",
                           cache_dir=str(tmp_path), client=client)
    assert p2 == p
    assert calls == []


def test_ensure_days_cached_skips_existing_and_fetches_rest(tmp_path, monkeypatch):
    manifest = {"days": {
        "2026-09-08": {"flows": "https://github.com/o/r/releases/download/data-2026-09/2026-09-08.parquet"},
        "2026-09-09": {"flows": "https://github.com/o/r/releases/download/data-2026-09/2026-09-09.parquet"},
    }}
    (tmp_path / "2026-09-08.parquet").write_bytes(b"CACHED")   # already there

    fetched_dates = []

    def fake_download_day(date_iso, manifest, *, token, cache_dir, client=None):
        fetched_dates.append(date_iso)
        p = Path(cache_dir) / f"{date_iso}.parquet"
        p.write_bytes(b"NEW")
        return p

    monkeypatch.setattr(data, "download_day", fake_download_day)
    paths = data.ensure_days_cached(["2026-09-08", "2026-09-09"], manifest,
                                    token="tok", cache_dir=str(tmp_path))
    assert fetched_dates == ["2026-09-09"]        # only the missing one hit the network
    assert {p.name for p in paths} == {"2026-09-08.parquet", "2026-09-09.parquet"}


def test_download_day_missing_date_returns_none(tmp_path):
    p = data.download_day("2099-01-01", MANIFEST_ONE, token="tok",
                          cache_dir=str(tmp_path))
    assert p is None


# --------------------------------------------------------------------------- #
# download_latest_board / latest_board_for_stock -- today's top-15/15 sweep
# --------------------------------------------------------------------------- #
def test_download_latest_board_hits_meta_release(tmp_path):
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        if "releases/tags/meta" in str(req.url):
            return httpx.Response(200, json={
                "assets": [{"name": "latest.parquet", "id": 555}]})
        if "releases/assets/555" in str(req.url):
            return httpx.Response(200, content=b"LATESTBYTES")
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    p = data.download_latest_board(token="tok", cache_dir=str(tmp_path), client=client)
    assert p is not None and p.name == "latest.parquet"
    assert p.read_bytes() == b"LATESTBYTES"
    assert any("releases/tags/meta" in c for c in calls)


def test_download_latest_board_missing_release_returns_none(tmp_path):
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(404)))
    p = data.download_latest_board(token="tok", cache_dir=str(tmp_path), client=client)
    assert p is None


def test_latest_board_for_stock(tmp_path):
    from ingest.storage import write_latest
    write_latest([
        {"data_date": "2026-09-14", "market": "twse", "stock_id": "3008",
         "branch_id": "", "branch_name": "某大分點", "side": "buy",
         "buy_lots": 100, "sell_lots": 0, "net_lots": 100,
         "avg_buy_cost": 2000.0, "avg_sell_cost": None},
        {"data_date": "2026-09-14", "market": "twse", "stock_id": "3008",
         "branch_id": "", "branch_name": "另一分點", "side": "sell",
         "buy_lots": 0, "sell_lots": 50, "net_lots": -50,
         "avg_buy_cost": None, "avg_sell_cost": 1990.0},
        {"data_date": "2026-09-14", "market": "twse", "stock_id": "9999",
         "branch_id": "", "branch_name": "別檔股票", "side": "buy",
         "buy_lots": 10, "sell_lots": 0, "net_lots": 10,
         "avg_buy_cost": 10.0, "avg_sell_cost": None},
    ], str(tmp_path / "latest.parquet"))

    rows = data.latest_board_for_stock(tmp_path / "latest.parquet", "3008")
    assert len(rows) == 2
    assert rows[0][0] == "某大分點" and rows[0][4] == 100     # net desc
    assert rows[1][0] == "另一分點" and rows[1][4] == -50


def test_latest_board_for_stock_missing_file_returns_empty(tmp_path):
    assert data.latest_board_for_stock(tmp_path / "nope.parquet", "3008") == []
    assert data.latest_board_for_stock(None, "3008") == []


# --------------------------------------------------------------------------- #
# DuckDB query helpers -- real Parquet fixtures, no mocking
# --------------------------------------------------------------------------- #
@pytest.fixture
def two_day_fixture(tmp_path) -> list[Path]:
    d1 = [
        BranchFlow("2026-09-08", "twse", "2330", "9200", "富邦", 5000, 1000, 4000, 1000.0),
        BranchFlow("2026-09-08", "twse", "2330", "1440", "美林", 1000, 3000, -2000, 1000.0),
        BranchFlow("2026-09-08", "twse", "2317", "9200", "富邦", 2000, 0, 2000, 200.0),
    ]
    d2 = [
        BranchFlow("2026-09-09", "twse", "2330", "9200", "富邦", 3000, 500, 2500, 1010.0),
        BranchFlow("2026-09-09", "twse", "2330", "1440", "美林", 0, 1000, -1000, 1010.0),
    ]
    p1 = tmp_path / "2026-09-08.parquet"
    p2 = tmp_path / "2026-09-09.parquet"
    write_parquet(d1, str(p1))
    write_parquet(d2, str(p2))
    return [p1, p2]


def test_stock_branch_rankings(two_day_fixture):
    rows = data.stock_branch_rankings(two_day_fixture, "2330")
    by_branch = {r[0]: r for r in rows}
    assert by_branch["9200"][4] == 6500          # net_shares summed across both days
    assert by_branch["1440"][4] == -3000
    assert "2317" not in [r[0] for r in rows]    # other stock excluded
    assert rows[0][0] == "9200"                  # sorted net desc


def test_stock_branch_rankings_no_files_returns_empty():
    assert data.stock_branch_rankings([], "2330") == []


def test_branch_daily_series(two_day_fixture):
    rows = data.branch_daily_series(two_day_fixture, "2330", "9200")
    assert rows == [("2026-09-08", 4000), ("2026-09-09", 2500)]


def test_branch_history_multi(two_day_fixture):
    got = data.branch_history_multi(two_day_fixture, "9200")
    assert got == {
        "2330": [("2026-09-08", 4000), ("2026-09-09", 2500)],
        "2317": [("2026-09-08", 2000)],
    }


def test_branch_history_multi_no_files_returns_empty():
    assert data.branch_history_multi([], "9200") == {}


def test_branch_overview_candidates(two_day_fixture):
    rows = data.branch_overview_candidates(two_day_fixture, "9200")
    stocks = {r[0]: r for r in rows}
    assert stocks["2330"][3] == 6500
    assert stocks["2317"][3] == 2000
    assert rows[0][0] == "2330"                  # |net| desc


def test_branch_overview_candidates_top_cap(two_day_fixture):
    rows = data.branch_overview_candidates(two_day_fixture, "9200", top=1)
    assert len(rows) == 1 and rows[0][0] == "2330"
