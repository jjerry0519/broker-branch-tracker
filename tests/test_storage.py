from ingest.schema import BranchFlow
from ingest.storage import merge_manifest, prune_old_releases, release_tag, write_parquet


def test_release_tag():
    assert release_tag("2026-09-03") == "data-2026-09"
    assert release_tag("2026-12-31") == "data-2026-12"


def test_write_parquet_roundtrip(tmp_path):
    import duckdb

    flows = [BranchFlow("2026-09-03", "twse", "2330", "9200", "凱基台北",
                        3000, 500, 2500, 1085.0)]
    p = tmp_path / "d.parquet"
    n = write_parquet(flows, str(p))
    assert n == 1
    got = duckdb.sql(f"select * from read_parquet('{p.as_posix()}')").fetchall()
    assert got[0][2] == "2330" and got[0][7] == 2500


def test_merge_manifest_inserts_and_trims():
    man = {"days": {"2025-06-02": {"rows": 1}}}
    out = merge_manifest(man, "2026-09-03", {"rows": 42}, keep_months=13)
    assert out["days"]["2026-09-03"]["rows"] == 42
    assert "2025-06-02" not in out["days"]        # >13 months old, trimmed
    assert "updated" in out


def test_prune_selects_old_tags(monkeypatch):
    calls = []
    monkeypatch.setattr("ingest.storage._gh_json",
                        lambda *a, **k: [{"tagName": "data-2024-01"},
                                         {"tagName": "data-2026-08"},
                                         {"tagName": "raw-2026-07"},
                                         {"tagName": "v1.0"}])
    monkeypatch.setattr("ingest.storage._gh", lambda *a, **k: calls.append(a))
    deleted = prune_old_releases(repo="me/x", keep_months=13, today="2026-09-03")
    # data-*: keep 13 months -> cutoff 2025-08; data-2024-01 is older -> deleted,
    # data-2026-08 kept. raw-*: keep 2 months -> cutoff 2026-06; raw-2026-07 kept.
    # v1.0 has no data-/raw- prefix -> left alone.
    assert deleted == ["data-2024-01"]
    assert calls == [("release", "delete", "data-2024-01", "--repo", "me/x",
                      "--yes", "--cleanup-tag")]
