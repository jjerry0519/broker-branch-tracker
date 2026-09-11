import subprocess

import pytest

from ingest.schema import BranchFlow
from ingest.storage import (
    merge_manifest,
    prune_old_releases,
    release_tag,
    upload_assets,
    write_parquet,
)


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
    # data-2026-08 kept. raw-*: keep 2 months -> cutoff 2026-07; raw-2026-07 kept.
    # v1.0 has no data-/raw- prefix -> left alone.
    assert deleted == ["data-2024-01"]
    assert calls == [("release", "delete", "data-2024-01", "--repo", "me/x",
                      "--yes", "--cleanup-tag")]


def test_upload_assets_release_exists_skips_create(monkeypatch):
    calls = []
    monkeypatch.setattr("ingest.storage._gh_json",
                        lambda *a, **k: [{"tagName": "data-2026-09"}])
    monkeypatch.setattr("ingest.storage._gh", lambda *a, **k: calls.append(a))
    upload_assets("data-2026-09", ["/tmp/a.parquet", "/tmp/b.parquet"], repo="me/x")
    assert not any(a[:2] == ("release", "create") for a in calls)
    assert calls == [
        ("release", "upload", "data-2026-09", "/tmp/a.parquet", "/tmp/b.parquet",
         "--repo", "me/x", "--clobber"),
    ]


def test_upload_assets_release_missing_creates_then_uploads(monkeypatch):
    calls = []
    monkeypatch.setattr("ingest.storage._gh_json",
                        lambda *a, **k: [{"tagName": "data-2026-08"}])
    monkeypatch.setattr("ingest.storage._gh", lambda *a, **k: calls.append(a))
    upload_assets("data-2026-09", ["/tmp/a.parquet", "/tmp/b.parquet"], repo="me/x")
    assert len(calls) == 2
    create = calls[0]
    assert create[:3] == ("release", "create", "data-2026-09")
    assert "--repo" in create and create[create.index("--repo") + 1] == "me/x"
    assert "--title" in create and create[create.index("--title") + 1] == "data-2026-09"
    assert "--notes" in create and "data-2026-09" in create[create.index("--notes") + 1]
    assert calls[1] == (
        "release", "upload", "data-2026-09", "/tmp/a.parquet", "/tmp/b.parquet",
        "--repo", "me/x", "--clobber",
    )


def test_upload_assets_tolerates_concurrent_create_race(monkeypatch):
    """Reproduces the 2026-09-11 backfill outage: 20+ parallel shards all see
    the release missing and race to create it; every loser must NOT crash."""
    calls = []
    monkeypatch.setattr("ingest.storage._gh_json",
                        lambda *a, **k: [])                # nobody has created it yet
    def fake_gh(*a, **k):
        calls.append(a)
        if a[:2] == ("release", "create"):
            raise subprocess.CalledProcessError(
                1, a, output="", stderr="could not create release: "
                                        "already_exists")
    monkeypatch.setattr("ingest.storage._gh", fake_gh)
    upload_assets("data-2026-09", ["/tmp/a.parquet"], repo="me/x")   # must not raise
    assert calls[0][:2] == ("release", "create")
    assert calls[1][:2] == ("release", "upload")


def test_upload_assets_reraises_non_race_create_error(monkeypatch):
    monkeypatch.setattr("ingest.storage._gh_json", lambda *a, **k: [])
    def fake_gh(*a, **k):
        if a[:2] == ("release", "create"):
            raise subprocess.CalledProcessError(1, a, output="", stderr="401 bad credentials")
    monkeypatch.setattr("ingest.storage._gh", fake_gh)
    with pytest.raises(subprocess.CalledProcessError):
        upload_assets("data-2026-09", ["/tmp/a.parquet"], repo="me/x")


def test_gh_retrying_retries_transient_upload_conflicts(monkeypatch):
    attempts = []
    def flaky(*a, **k):
        attempts.append(a)
        if len(attempts) < 3:
            raise subprocess.CalledProcessError(1, a, output="", stderr="409 conflict")
    monkeypatch.setattr("ingest.storage._gh", flaky)
    monkeypatch.setattr("ingest.storage.time.sleep", lambda s: None)
    from ingest.storage import _gh_retrying
    _gh_retrying("release", "upload", "t", "f", "--repo", "r")   # must not raise
    assert len(attempts) == 3


def test_gh_retrying_reraises_after_exhausting_attempts(monkeypatch):
    def always_fails(*a, **k):
        raise subprocess.CalledProcessError(1, a, output="", stderr="boom")
    monkeypatch.setattr("ingest.storage._gh", always_fails)
    monkeypatch.setattr("ingest.storage.time.sleep", lambda s: None)
    from ingest.storage import _gh_retrying
    with pytest.raises(subprocess.CalledProcessError):
        _gh_retrying("release", "upload", "t", "f", "--repo", "r", attempts=2)
