"""Storage layer: Parquet writes, GitHub Releases upload, manifest merge, retention.

The pipeline emits one Snappy Parquet file per trading day. Files are shipped as
assets on monthly GitHub Releases (`data-YYYY-MM`, and `raw-YYYY-MM` for scraped
source snapshots). `manifest.json` is the small index consumers read to discover
which days exist. Retention: daily data releases are kept ``keep_months`` (13)
months; raw snapshots only 2 months.

The ``gh``-calling functions go through the ``_gh`` / ``_gh_json`` subprocess
wrappers so tests can monkeypatch them without a real ``gh`` CLI.
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from ingest.schema import BranchFlow, flows_to_table, table_to_flows

__all__ = [
    "write_parquet",
    "release_tag",
    "upload_assets",
    "download_asset",
    "asset_url",
    "prune_old_releases",
    "merge_manifest",
    "load_manifest",
    "save_manifest",
    "read_flows",
    "dedup_flows",
    "LATEST_SCHEMA",
    "write_latest",
]


def write_parquet(flows: list[BranchFlow], path: str) -> int:
    """Write ``flows`` to ``path`` as a Snappy-compressed Parquet file.

    Returns the number of rows written.
    """
    table = flows_to_table(flows)
    pq.write_table(table, path, compression="snappy")
    return table.num_rows


def release_tag(date_iso: str) -> str:
    """``"2026-09-03"`` -> ``"data-2026-09"`` (the monthly release the day lives on)."""
    return "data-" + date_iso[:7]


def _month_key(tag: str) -> str | None:
    """Return the ``YYYY-MM`` portion of a ``data-``/``raw-`` tag, else ``None``."""
    for pfx in ("data-", "raw-"):
        if tag.startswith(pfx):
            return tag[len(pfx):]
    return None


def _cutoff(today: str, months: int) -> str:
    """The ``YYYY-MM`` that is ``months`` calendar months before ``today``.

    Month keys strictly less than this string are outside the retention window.
    ``today`` may be ``YYYY-MM`` or ``YYYY-MM-DD``.
    """
    y, m = int(today[:4]), int(today[5:7])
    total = y * 12 + (m - 1) - months
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def _gh(*args: str) -> None:
    """Run ``gh <args>`` and raise on non-zero exit. Monkeypatched in tests."""
    subprocess.run(["gh", *args], check=True, capture_output=True, text=True)


def _gh_json(*args: str):
    """Run ``gh <args>`` expecting JSON on stdout; return the parsed value."""
    out = subprocess.run(
        ["gh", *args], check=True, capture_output=True, text=True
    ).stdout
    return json.loads(out)


def upload_assets(tag: str, paths: list[str], *, repo: str) -> None:
    """Upload ``paths`` as assets on release ``tag`` in ``repo``.

    Creates the release first if it does not exist, then uploads with
    ``--clobber`` so re-runs overwrite same-named assets.
    """
    existing = _gh_json("release", "list", "--repo", repo, "--json", "tagName")
    if not any(r["tagName"] == tag for r in existing):
        _gh("release", "create", tag, "--repo", repo,
            "--title", tag, "--notes", f"broker-branch data {tag}")
    _gh("release", "upload", tag, *paths, "--repo", repo, "--clobber")


def download_asset(tag: str, filename: str, dest_dir: str, *, repo: str) -> str | None:
    """Download asset ``filename`` from release ``tag`` into ``dest_dir``.

    Returns the local path, or ``None`` when the release or asset does not exist
    (a first-write for that day). Any other ``gh`` failure propagates.
    """
    Path(dest_dir).mkdir(parents=True, exist_ok=True)
    out = str(Path(dest_dir) / filename)
    try:
        _gh("release", "download", tag, "--repo", repo,
            "--pattern", filename, "--dir", dest_dir, "--clobber")
    except subprocess.CalledProcessError as e:
        blob = f"{e.stderr or ''}{e.stdout or ''}".lower()
        if "release not found" in blob or "no assets" in blob or "not found" in blob:
            return None
        raise
    return out if Path(out).exists() else None


def asset_url(tag: str, filename: str, *, repo: str) -> str:
    """Public download URL for asset ``filename`` on release ``tag``."""
    return f"https://github.com/{repo}/releases/download/{tag}/{filename}"


def read_flows(path: str) -> list[BranchFlow]:
    """Load a day-partition Parquet back into ``BranchFlow`` rows."""
    return table_to_flows(pq.read_table(path))


def dedup_flows(flows: list[BranchFlow]) -> list[BranchFlow]:
    """Collapse to one row per ``(date, stock_id, branch_id)`` -- the *last*
    occurrence wins, so callers append freshly fetched rows after any rows read
    back from the existing partition. Result is sorted by
    ``(date, -net_shares, branch_id)`` for stable, diff-friendly files.
    """
    keep: dict[tuple[str, str, str], BranchFlow] = {}
    for f in flows:
        keep[(f.date, f.stock_id, f.branch_id)] = f
    return sorted(keep.values(),
                  key=lambda f: (f.date, -f.net_shares, f.branch_id))


LATEST_SCHEMA = pa.schema([
    ("data_date", pa.string()),
    ("market", pa.string()),
    ("stock_id", pa.string()),
    ("branch_id", pa.string()),
    ("branch_name", pa.string()),
    ("side", pa.string()),               # "buy" (net buyer) | "sell" (net seller)
    ("buy_lots", pa.int64()),
    ("sell_lots", pa.int64()),
    ("net_lots", pa.int64()),
    ("avg_buy_cost", pa.float64()),
    ("avg_sell_cost", pa.float64()),
])


def write_latest(records: list[dict], path: str) -> int:
    """Write the daily ``zco`` top-15/15 board snapshot (overwritten each run)."""
    cols = {f.name: [] for f in LATEST_SCHEMA}
    for r in records:
        for name in cols:
            cols[name].append(r.get(name))
    pq.write_table(pa.table(cols, schema=LATEST_SCHEMA), path, compression="snappy")
    return len(records)


def prune_old_releases(*, repo: str, keep_months: int = 13,
                       today: str | None = None) -> list[str]:
    """Delete releases outside the retention window; return deleted tags in order.

    ``data-YYYY-MM`` older than ``keep_months`` months and ``raw-YYYY-MM`` older
    than 2 months are removed (tag included, via ``--cleanup-tag``). Tags without
    a ``data-``/``raw-`` prefix are left untouched.
    """
    today = today or dt.date.today().isoformat()
    data_cut = _cutoff(today, keep_months)
    raw_cut = _cutoff(today, 2)
    deleted: list[str] = []
    for r in _gh_json("release", "list", "--repo", repo, "--json", "tagName"):
        tag = r["tagName"]
        month_key = _month_key(tag)
        if month_key is None:
            continue
        cutoff = raw_cut if tag.startswith("raw-") else data_cut
        if month_key < cutoff:
            _gh("release", "delete", tag, "--repo", repo, "--yes", "--cleanup-tag")
            deleted.append(tag)
    return deleted


def merge_manifest(manifest: dict, date_iso: str, entry: dict,
                   keep_months: int = 13) -> dict:
    """Return a new manifest with ``date_iso`` set to ``entry``, old days dropped.

    Pure — does not mutate ``manifest``. Days older than ``keep_months`` months
    before ``date_iso`` are trimmed. ``updated`` is set to the current UTC time.
    """
    days = dict(manifest.get("days", {}))
    days[date_iso] = entry
    cutoff = _cutoff(date_iso, keep_months) + "-01"
    days = {d: v for d, v in days.items() if d >= cutoff}
    return {
        "updated": dt.datetime.now(dt.UTC).isoformat(),
        "days": days,
    }


def load_manifest(path: str = "manifest.json") -> dict:
    """Read the manifest JSON; return an empty skeleton if the file is missing."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {"days": {}}


def save_manifest(manifest: dict, path: str = "manifest.json") -> None:
    """Write ``manifest`` to ``path`` as pretty-printed UTF-8 JSON."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
