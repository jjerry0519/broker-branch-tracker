from __future__ import annotations

import json
from pathlib import Path

import ingest.run as run
from ingest.djhtm_parse import Zco0Page, Zco0Row, ZcoBranchRow, ZcoPage
from ingest.run import fetch_market, main
from ingest.schema import BranchFlow
from ingest.storage import read_flows
from ingest.universe import Stock


# --------------------------------------------------------------------------- #
# fetch_market -- generic concurrent map
# --------------------------------------------------------------------------- #
def test_fetch_market_collects_and_reports_failures():
    items = ["2330", "2317", "9999"]

    def fake(sid: str) -> list[int]:
        if sid == "9999":
            raise RuntimeError("boom")
        return [int(sid)]

    out, failed = fetch_market(items, fake, workers=2)
    assert sorted(out) == [2317, 2330]
    assert failed == ["9999"]


def test_fetch_market_empty():
    out, failed = fetch_market([], lambda x: [], workers=2)
    assert out == [] and failed == []


def test_branch_axis_drops_dead_codes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(run, "load_branches",
                        lambda *a, **k: [("9200", "富邦"), ("102T", "複委託"),
                                         ("1440", "美林")])
    assert run._branch_axis() == [("9200", "富邦"), ("102T", "複委託"),
                                  ("1440", "美林")]          # no file -> keep all
    (tmp_path / "dead_branches.json").write_text(
        '{"codes": ["102T"]}', encoding="utf-8")
    assert run._branch_axis() == [("9200", "富邦"), ("1440", "美林")]


def test_fetch_market_custom_key():
    items = [("2330", "9200"), ("2330", "BAD")]

    def fake(it):
        if it[1] == "BAD":
            raise ValueError
        return [it]

    _, failed = fetch_market(items, fake, workers=2, key=lambda it: f"{it[0]}/{it[1]}")
    assert failed == ["2330/BAD"]


# --------------------------------------------------------------------------- #
# _zco0_pair -- lots -> shares, drop empty days
# --------------------------------------------------------------------------- #
def test_zco0_pair_converts_lots_and_drops_zero_days(monkeypatch):
    page = Zco0Page(
        rows=[Zco0Row("2026-09-09", 10, 3, 7),
              Zco0Row("2026-09-08", 0, 0, 0),      # dropped
              Zco0Row("2026-09-05", 0, 4, -4)],
        period_net_lots=3)
    monkeypatch.setattr(run, "fetch_zco0", lambda *a, **k: page)
    stock = Stock("2330", "台積電", "twse", 1000.0)

    flows = run._zco0_pair(None, stock, "9200", "富邦-台北",
                           "2026-09-01", "2026-09-09")

    assert [f.date for f in flows] == ["2026-09-09", "2026-09-05"]
    f0 = flows[0]
    assert (f0.buy_shares, f0.sell_shares, f0.net_shares) == (10000, 3000, 7000)
    assert f0.market == "twse" and f0.stock_id == "2330"
    assert f0.branch_id == "9200" and f0.branch_name == "富邦-台北"
    assert flows[1].net_shares == -4000


# --------------------------------------------------------------------------- #
# _merge_and_upload -- dedup existing + new, manifest bookkeeping (repo="")
# --------------------------------------------------------------------------- #
def test_merge_and_upload_local_dedups_and_indexes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    d = "2026-09-09"
    by_date = {d: [
        BranchFlow(d, "twse", "2330", "9200", "富邦", 5000, 1000, 4000, 0.0),
        BranchFlow(d, "twse", "2330", "9200", "富邦", 7000, 0, 7000, 0.0),  # newer dup
        BranchFlow(d, "twse", "2317", "1440", "美林", 2000, 0, 2000, 0.0),
    ]}
    manifest = {"days": {}}

    counts = run._merge_and_upload(by_date, repo="", manifest=manifest)

    assert counts == {d: 2}                       # dup collapsed
    rows = read_flows(str(tmp_path / "out" / f"{d}.parquet"))
    pair = next(r for r in rows if r.stock_id == "2330")
    assert pair.net_shares == 7000               # last write won
    assert d in manifest["days"]
    assert manifest["days"][d]["rows"]["flows"] == 2


def test_merge_and_upload_staging_mode_writes_shard_file_no_manifest(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    d = "2026-08-15"
    by_date = {d: [BranchFlow(d, "twse", "2330", "9200", "富邦", 5000, 0, 5000, 0.0)]}
    manifest = {"days": {}}

    counts = run._merge_and_upload(by_date, repo="", manifest=manifest,
                                   stage_tag="s3-20")

    assert counts == {d: 1}
    assert (tmp_path / "out" / f"{d}__bfs3-20.parquet").exists()
    assert not (tmp_path / "out" / f"{d}.parquet").exists()
    assert manifest == {"days": {}}                    # staging never touches manifest


def test_compact_folds_staging_into_canonical(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    d = "2026-08-15"
    (tmp_path / "out").mkdir()
    # a canonical file with one row + a staging file with a different branch
    from ingest.storage import write_parquet as _wp
    _wp([BranchFlow(d, "twse", "2330", "9200", "富邦", 1000, 0, 1000, 0.0)],
        str(tmp_path / "canon.parquet"))
    _wp([BranchFlow(d, "twse", "2330", "1440", "美林", 2000, 0, 2000, 0.0)],
        str(tmp_path / "stage.parquet"))

    monkeypatch.setattr(run, "_data_months", lambda **k: ["2026-08"])
    monkeypatch.setattr(run, "list_release_assets",
                        lambda tag, **k: [f"{d}.parquet", f"{d}__bfs1-4.parquet"])

    def fake_dl(tag, fname, dest, **k):
        src = tmp_path / ("canon.parquet" if fname == f"{d}.parquet" else "stage.parquet")
        out = tmp_path / dest / fname
        out.write_bytes(src.read_bytes())
        return str(out)

    uploaded, deleted = [], []
    monkeypatch.setattr(run, "download_asset", fake_dl)
    monkeypatch.setattr(run, "upload_assets", lambda tag, paths, **k: uploaded.extend(paths))
    monkeypatch.setattr(run, "delete_asset", lambda tag, fn, **k: deleted.append(fn))

    manifest: dict = {"days": {}}
    counts = run._compact(repo="r", manifest=manifest, month="2026-08")

    assert counts == {d: 2}                            # both branches survived
    assert deleted == [f"{d}__bfs1-4.parquet"]         # staging removed
    assert manifest["days"][d]["rows"]["flows"] == 2


# --------------------------------------------------------------------------- #
# main -- guard / skip
# --------------------------------------------------------------------------- #
def _read_log(tmp_path: Path) -> list[dict]:
    p = tmp_path / "logs" / "ingest_log.jsonl"
    return ([json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x]
            if p.exists() else [])


def test_main_skips_when_trading_date_unresolvable(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(run, "resolved_trading_date", lambda c: None)
    monkeypatch.setattr(run, "_universe", lambda skip: (_ for _ in ()).throw(
        AssertionError("must not build universe past the guard")))

    assert main([]) == 0
    log = _read_log(tmp_path)
    assert len(log) == 1 and log[0]["status"] == "skipped"
    assert not (tmp_path / "manifest.json").exists()


def test_main_skips_when_already_ingested(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "manifest.json").write_text(json.dumps(
        {"days": {"2026-09-09": {"sweep_done": True}}}), encoding="utf-8")
    monkeypatch.setattr(run, "resolved_trading_date", lambda c: "2026-09-09")
    monkeypatch.setattr(run, "_universe", lambda skip: (_ for _ in ()).throw(
        AssertionError("must not run past the skip")))

    assert main([]) == 0
    log = _read_log(tmp_path)
    assert log[-1]["status"] == "skipped" and log[-1]["note"] == "already ingested"


# --------------------------------------------------------------------------- #
# main -- daily happy path (fully stubbed network)
# --------------------------------------------------------------------------- #
def test_main_daily_happy_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    stocks = [Stock("2330", "台積電", "twse", 1000.0),
              Stock("6488", "環球晶", "tpex", 900.0)]

    monkeypatch.setattr(run, "resolved_trading_date", lambda c: "2026-09-09")
    monkeypatch.setattr(run, "_universe", lambda skip: stocks)
    monkeypatch.setattr(run, "load_branches",
                        lambda *a, **k: [("9200", "富邦-台北"), ("1440", "美林")])
    monkeypatch.setattr(run, "new_client", lambda *a, **k: _NoClient())

    zco_page = ZcoPage("X", "2026-09-09",
                       branches=[ZcoBranchRow("富邦-台北", 10, 2, 8),
                                 ZcoBranchRow("美林", 1, 9, -8)],
                       avg_buy_cost=999.0, avg_sell_cost=998.0)
    monkeypatch.setattr(run, "fetch_zco", lambda c, sid, **k: zco_page)

    def fake_zco0(c, sid, bid, d_from, d_to, **k):
        return Zco0Page(rows=[Zco0Row("2026-09-09", 4, 1, 3)], period_net_lots=3)
    monkeypatch.setattr(run, "fetch_zco0", fake_zco0)

    rc = main(["--repo", "", "--cycle-days", "20", "--workers", "2"])
    assert rc == 0

    # zco sweep -> latest.parquet
    assert (tmp_path / "out" / "latest.parquet").exists()
    # rolling shard (size ceil(2/20)=1 stock) -> one day partition
    day = read_flows(str(tmp_path / "out" / "2026-09-09.parquet"))
    assert day and all(r.date == "2026-09-09" for r in day)
    assert day[0].buy_shares == 4000                 # 4 張 -> shares

    # state advanced for the shard stock only
    state = json.loads((tmp_path / "refresh_state.json").read_text("utf-8"))
    assert list(state["refreshed_through"].values()) == ["2026-09-09"]

    manifest = json.loads((tmp_path / "manifest.json").read_text("utf-8"))
    assert manifest["days"]["2026-09-09"]["sweep_done"] is True

    log = _read_log(tmp_path)
    assert any(r.get("leg") == "sweep" and r["status"] == "ok" for r in log)
    assert any(r.get("leg") == "shard" and r["status"] == "ok" for r in log)


class _NoClient:
    def close(self):
        pass
