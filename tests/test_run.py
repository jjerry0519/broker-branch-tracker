from __future__ import annotations

import json
from pathlib import Path

import ingest.run as run
from ingest.run import fetch_market, main
from ingest.schema import BranchFlow
from ingest.universe import Stock


def test_fetch_market_collects_and_reports_failures():
    stocks = [Stock("2330", "台積電", "twse", 1085.0),
              Stock("2317", "鴻海", "twse", 200.0),
              Stock("9999", "壞股", "twse", 1.0)]

    def fake_fetch(s: Stock) -> list[BranchFlow]:
        if s.stock_id == "9999":
            raise RuntimeError("boom")
        return [BranchFlow("2026-09-03", "twse", s.stock_id, "9200", "凱基台北",
                           10, 0, 10, s.close_price)]

    flows, failed = fetch_market(stocks, fake_fetch, workers=2)
    assert {f.stock_id for f in flows} == {"2330", "2317"}
    assert failed == ["9999"]


def test_fetch_market_empty():
    flows, failed = fetch_market([], lambda s: [], workers=2)
    assert flows == [] and failed == []


def _read_log(tmp_path: Path) -> list[dict]:
    p = tmp_path / "logs" / "ingest_log.jsonl"
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln]


def test_main_non_trading_day_skips(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    wrote: list = []

    # Site shows a different date than the target -> guard trips.
    monkeypatch.setattr(run, "resolved_trading_date", lambda c: "2026-09-04")
    monkeypatch.setattr(run, "twse_traded", lambda c: (_ for _ in ()).throw(
        AssertionError("universe must not be built on a non-trading day")))
    monkeypatch.setattr(run, "tpex_traded", lambda c: [])
    monkeypatch.setattr(run, "fetch_market", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("fetch_market must not run")))
    monkeypatch.setattr(run, "write_parquet", lambda *a, **k: wrote.append(a) or 0)
    monkeypatch.setattr(run, "upload_assets", lambda *a, **k: None)
    monkeypatch.setattr(run, "prune_old_releases", lambda *a, **k: None)
    monkeypatch.setattr(run, "TpexBrowser", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("TpexBrowser must not be constructed")))

    rc = main(["--date", "2026-09-03"])

    assert rc == 0
    assert wrote == []                       # no parquet written
    rows = _read_log(tmp_path)
    assert len(rows) == 1
    assert rows[0]["status"] == "skipped"
    assert rows[0]["date"] == "2026-09-03"
    assert not (tmp_path / "manifest.json").exists()


def test_main_happy_path_skip_tpex(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    wrote: list = []
    built: list = []

    stocks = [Stock("2330", "台積電", "twse", 1085.0),
              Stock("2317", "鴻海", "twse", 200.0)]
    flow = BranchFlow("2026-09-03", "twse", "2330", "9200", "凱基台北",
                      3000, 500, 2500, 1085.0)

    monkeypatch.setattr(run, "resolved_trading_date", lambda c: "2026-09-03")
    monkeypatch.setattr(run, "twse_traded", lambda c: stocks)
    monkeypatch.setattr(run, "tpex_traded", lambda c: (_ for _ in ()).throw(
        AssertionError("tpex_traded must not run with --skip-tpex")))
    monkeypatch.setattr(run, "fetch_market",
                        lambda s, fn, **k: ([flow], []))

    def fake_write(flows, path):
        wrote.append((path, list(flows)))
        return len(flows)

    monkeypatch.setattr(run, "write_parquet", fake_write)
    monkeypatch.setattr(run, "upload_assets", lambda *a, **k: None)
    monkeypatch.setattr(run, "prune_old_releases", lambda *a, **k: None)
    monkeypatch.setattr(run, "TpexBrowser",
                        lambda *a, **k: built.append(a) or (_ for _ in ()).throw(
                            AssertionError("TpexBrowser must not be constructed")))

    rc = main(["--date", "2026-09-03", "--skip-tpex", "--repo", ""])

    assert rc == 0
    assert len(wrote) == 1                                  # twse only
    assert wrote[0][0].endswith("2026-09-03_twse.parquet")
    assert built == []                                      # TpexBrowser never built

    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert "2026-09-03" in manifest["days"]
    assert manifest["days"]["2026-09-03"]["rows"]["twse"] == 1

    rows = _read_log(tmp_path)
    assert len(rows) == 1
    assert rows[0]["status"] == "ok"
    assert rows[0]["market"] == "twse"
    assert rows[0]["row_count"] == 1
