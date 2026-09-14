"""Data access layer for the query web app -- reads ONLY our own store.

``manifest.json`` lives in the (public) code repo and is fetched over plain
HTTPS, no auth. The actual day-partition Parquet files live as GitHub Releases
assets on the *private* data repo and need an authenticated request (a PAT with
read access, `GH_TOKEN`); each downloaded file is cached to disk forever (day
partitions are immutable once written) so a repeat visit re-downloads nothing.
Queries run in DuckDB directly over the cached local Parquet files.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import duckdb
import httpx

MANIFEST_URL = (
    "https://raw.githubusercontent.com/{repo}/main/manifest.json"
)
_UA = "broker-branch-tracker-app/1.0"


def fetch_manifest(repo: str = "jjerry0519/broker-branch-tracker",
                   *, client: httpx.Client | None = None) -> dict:
    """The code repo's ``manifest.json`` -- public, no auth needed."""
    own = client is None
    client = client or httpx.Client(timeout=20, headers={"User-Agent": _UA})
    try:
        r = client.get(MANIFEST_URL.format(repo=repo))
        r.raise_for_status()
        return r.json()
    finally:
        if own:
            client.close()


def trading_dates(manifest: dict, *, start: str | None = None,
                  end: str | None = None, n: int | None = None) -> list[str]:
    """Available (ingested) dates, ascending, matching the window.

    - ``start``/``end`` given: inclusive range, clipped to what is available.
    - ``n`` given (no ``start``): the last ``n`` available trading dates
      up to and including ``end`` (default: the most recent available date).
    - Neither: every available date.
    """
    days = sorted(manifest.get("days", {}))
    if not days:
        return []
    if start is not None:
        hi = end or days[-1]
        return [d for d in days if start <= d <= hi]
    if n is not None:
        hi = end or days[-1]
        window = [d for d in days if d <= hi]
        return window[-n:]
    return days


def _asset_info(manifest: dict, date_iso: str) -> tuple[str, str] | None:
    """``(url, filename)`` for ``date_iso``'s day-partition, or ``None`` if that
    date is not in the manifest."""
    entry = manifest.get("days", {}).get(date_iso)
    if not entry or "flows" not in entry:
        return None
    url = entry["flows"]
    return url, url.rsplit("/", 1)[-1]


def _asset_id_from_url(url: str) -> str | None:
    """GitHub Releases download URLs don't carry the numeric asset id; resolve
    it via the release-by-tag API so we can hit the authenticated
    ``/releases/assets/{id}`` download endpoint (works for private repos)."""
    # url shape: https://github.com/<owner>/<repo>/releases/download/<tag>/<file>
    parts = url.split("/")
    try:
        i = parts.index("download")
    except ValueError:
        return None
    owner, repo, tag, fname = parts[i - 3], parts[i - 2], parts[i + 1], parts[i + 2]
    return f"{owner}/{repo}", tag, fname


def _download_by_tag(owner_repo: str, tag: str, asset_fname: str, dest: Path,
                     *, token: str, client: httpx.Client | None = None) -> Path | None:
    """Download one named asset off release ``tag`` in ``owner_repo`` (works for
    private repos via ``token``). Returns ``dest``, or ``None`` if the release or
    asset doesn't exist."""
    own = client is None
    client = client or httpx.Client(timeout=60, headers={"User-Agent": _UA},
                                    follow_redirects=True)
    try:
        api = f"https://api.github.com/repos/{owner_repo}/releases/tags/{tag}"
        r = client.get(api, headers={"Authorization": f"Bearer {token}",
                                     "Accept": "application/vnd.github+json"})
        if r.status_code == 404:
            return None
        r.raise_for_status()
        asset = next((a for a in r.json().get("assets", [])
                     if a["name"] == asset_fname), None)
        if asset is None:
            return None
        dl = client.get(
            f"https://api.github.com/repos/{owner_repo}/releases/assets/{asset['id']}",
            headers={"Authorization": f"Bearer {token}",
                    "Accept": "application/octet-stream"})
        dl.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(dl.content)
        return dest
    finally:
        if own:
            client.close()


def download_day(date_iso: str, manifest: dict, *, token: str,
                 cache_dir: str, client: httpx.Client | None = None) -> Path | None:
    """Download (or reuse the cached copy of) one day's Parquet partition.
    Returns the local path, or ``None`` if that date has no entry."""
    info = _asset_info(manifest, date_iso)
    if info is None:
        return None
    url, fname = info
    dest = Path(cache_dir) / fname
    if dest.exists():
        return dest
    resolved = _asset_id_from_url(url)
    if resolved is None:
        return None
    owner_repo, tag, asset_fname = resolved
    return _download_by_tag(owner_repo, tag, asset_fname, dest, token=token, client=client)


DATA_REPO = "jjerry0519/broker-branch-tracker-data"


def download_latest_board(*, token: str, cache_dir: str, repo: str = DATA_REPO,
                          client: httpx.Client | None = None) -> Path | None:
    """The daily ``zco`` top-15/top-15 sweep (``meta`` release, ``latest.parquet``)
    -- every stock's biggest movers, same trading day, independent of the 20-day
    rolling shard that the historical per-day store depends on. Always
    re-downloaded (the file is overwritten daily); callers should wrap with a
    short-TTL cache (``st.cache_data``), not treat it as permanently cacheable
    like a day-partition."""
    dest = Path(cache_dir) / "latest.parquet"
    return _download_by_tag(repo, "meta", "latest.parquet", dest, token=token,
                            client=client)


def latest_board_for_stock(path: Path | None, stock_id: str):
    """``[(branch_name, side, buy_lots, sell_lots, net_lots, data_date), ...]``
    for one stock from the daily top-15/top-15 sweep -- same trading day, but
    only the biggest movers (see :func:`stock_branch_rankings` for the complete,
    every-branch view, which can lag up to the rolling cycle length)."""
    if path is None or not path.exists():
        return []
    con = duckdb.connect()
    return con.execute("""
        SELECT branch_name, side, buy_lots, sell_lots, net_lots, data_date
        FROM read_parquet($file)
        WHERE stock_id = $stock_id
        ORDER BY net_lots DESC
    """, {"file": str(path), "stock_id": stock_id}).fetchall()


def ensure_days_cached(dates: list[str], manifest: dict, *, token: str,
                       cache_dir: str, workers: int = 8) -> list[Path]:
    """Download every date in ``dates`` not already cached, concurrently
    (a query can span 20-60+ day-partitions; one-at-a-time made the FIFO page
    take ~50s even on a warm connection)."""
    already: list[Path] = []
    todo: list[str] = []
    for d in dates:
        info = _asset_info(manifest, d)
        if info is None:
            continue
        dest = Path(cache_dir) / info[1]
        if dest.exists():
            already.append(dest)
        else:
            todo.append(d)
    if not todo:
        return already

    def one(d: str) -> Path | None:
        with httpx.Client(timeout=60, headers={"User-Agent": _UA},
                          follow_redirects=True) as c:
            return download_day(d, manifest, token=token, cache_dir=cache_dir,
                                client=c)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        fetched = [p for p in pool.map(one, todo) if p is not None]
    return already + fetched


def _con(paths: list[Path]) -> tuple[duckdb.DuckDBPyConnection, list[str]] | None:
    if not paths:
        return None
    return duckdb.connect(), [str(p) for p in paths]


def stock_branch_rankings(paths: list[Path], stock_id: str):
    """One row per branch that traded ``stock_id`` across ``paths``: summed
    buy/sell/net shares, ready to split into top-buy / top-sell tables."""
    got = _con(paths)
    if got is None:
        return []
    con, files = got
    return con.execute("""
        SELECT branch_id, any_value(branch_name) AS branch_name,
               sum(buy_shares) AS buy_shares, sum(sell_shares) AS sell_shares,
               sum(net_shares) AS net_shares
        FROM read_parquet($files)
        WHERE stock_id = $stock_id
        GROUP BY branch_id
        HAVING sum(net_shares) != 0
        ORDER BY net_shares DESC
    """, {"files": files, "stock_id": stock_id}).fetchall()


def branch_daily_series(paths: list[Path], stock_id: str, branch_id: str):
    """``[(date, net_shares), ...]`` ascending -- one (stock, branch) pair's
    daily net flow, for the trend chart / FIFO input (close price joined
    separately by the caller -- see :mod:`app.close_price`)."""
    got = _con(paths)
    if got is None:
        return []
    con, files = got
    return con.execute("""
        SELECT date, sum(net_shares) AS net_shares
        FROM read_parquet($files)
        WHERE stock_id = $stock_id AND branch_id = $branch_id
        GROUP BY date
        ORDER BY date
    """, {"files": files, "stock_id": stock_id, "branch_id": branch_id}).fetchall()


def branch_history_multi(paths: list[Path], branch_id: str
                         ) -> dict[str, list[tuple[str, int]]]:
    """One query for *every* stock a branch traded across ``paths`` (not just
    the capped candidate list) -- the 分點總覽 page used to run one DuckDB
    query per candidate stock (up to 200); this replaces that with one."""
    got = _con(paths)
    if got is None:
        return {}
    con, files = got
    rows = con.execute("""
        SELECT stock_id, date, sum(net_shares) AS net_shares
        FROM read_parquet($files)
        WHERE branch_id = $branch_id
        GROUP BY stock_id, date
        ORDER BY stock_id, date
    """, {"files": files, "branch_id": branch_id}).fetchall()
    out: dict[str, list[tuple[str, int]]] = {}
    for stock_id, date_iso, net in rows:
        out.setdefault(stock_id, []).append((date_iso, net))
    return out


def branch_overview_candidates(paths: list[Path], branch_id: str, *, top: int = 200):
    """Stocks a branch traded across ``paths``, summed net/buy/sell shares,
    sorted by |net| descending, capped to ``top`` (design spec Sec.7.3 --
    FIFO is O(days) per stock, so a huge candidate list is capped with a
    UI notice rather than computed in full)."""
    got = _con(paths)
    if got is None:
        return []
    con, files = got
    # DuckDB won't bind a $-named param inside LIMIT together with a list-typed
    # $files param in the same query; `top` is always our own int, not
    # user text, so a validated f-string is safe here.
    limit = int(top)
    return con.execute(f"""
        SELECT stock_id, sum(buy_shares) AS buy_shares, sum(sell_shares) AS sell_shares,
               sum(net_shares) AS net_shares
        FROM read_parquet($files)
        WHERE branch_id = $branch_id
        GROUP BY stock_id
        HAVING sum(net_shares) != 0
        ORDER BY abs(sum(net_shares)) DESC
        LIMIT {limit}
    """, {"branch_id": branch_id, "files": files}).fetchall()


def stock_branch_history(paths: list[Path], stock_id: str, branch_id: str):
    """``[(date, net_shares), ...]`` for one pair -- same as
    :func:`branch_daily_series`, named separately for the FIFO page's intent."""
    return branch_daily_series(paths, stock_id, branch_id)
