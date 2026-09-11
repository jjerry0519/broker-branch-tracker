"""Daily pipeline -- Architecture A over the SysJust ``.djhtm`` mirrors.

Plain HTTP, no browser, no CAPTCHA. One run does:

1. **Guard** -- resolve the latest published trading day (TWSE ``STOCK_DAY_ALL``
   ``Date``). Skip (rc 0, logged) if unresolvable.
2. **zco sweep** -- one ``zco.djhtm`` per stock -> that day's top-15 buyers /
   top-15 sellers -> ``latest.parquet`` (overwritten; the web app's "today"
   layer, always same-day for the 30 biggest players per stock).
3. **Rolling zco0 shard** -- ``schedule.next_shard`` picks 1/``cycle_days`` of the
   market; for each of those stocks, one ``zco0`` *range* request per branch in
   ``reference.db`` over ``[refreshed_through+1 .. target]``. Rows (張 x 1000 ->
   shares) are merged into each affected ``<date>.parquet`` day-partition and
   re-uploaded. ``mark_done`` advances those stocks to ``target``.
4. **Retention / manifest** -- prune releases outside 13 months; persist the
   manifest and ``refresh_state.json``.

``--mode backfill`` runs step 3 only with a 15-month window and ``--shard i/N``
to split the one-time ~1.7M-cell first fill across dispatched runs.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, TypeVar

import httpx

from ingest.djhtm_client import fetch_zco, fetch_zco0, new_client
from ingest.reference import load_branches
from ingest.schedule import (load_state, mark_done, next_shard, save_state,
                             window_for)
from ingest.schema import BranchFlow
from ingest.storage import (asset_url, dedup_flows, delete_asset, download_asset,
                            list_release_assets, load_manifest, merge_manifest,
                            prune_old_releases, read_flows, release_tag,
                            save_manifest, upload_assets, write_latest,
                            write_parquet)
from ingest.universe import (Stock, resolved_trading_date, tpex_traded,
                             twse_traded)

_LOG = Path("logs/ingest_log.jsonl")
_WORK = Path("out")
_LOTS = 1000                              # 1 張 = 1000 shares
_DEAD_BRANCHES = Path("dead_branches.json")


def _branch_axis() -> list[tuple[str, str]]:
    """``reference.db`` branches minus the codes that carry no data in the zco0
    feed (``dead_branches.json`` -- ~7% of TWSE codes: 複委託 / 海外 / 特殊帳務
    desks). Excluding them keeps the daily run from being flagged ``partial`` on
    a structural gap and trims the matrix by the same fraction.
    """
    dead: set[str] = set()
    if _DEAD_BRANCHES.exists():
        try:
            dead = set(json.loads(_DEAD_BRANCHES.read_text("utf-8")).get("codes", []))
        except (ValueError, OSError):
            dead = set()
    return [(bid, name) for bid, name in load_branches() if bid not in dead]

T = TypeVar("T")
R = TypeVar("R")


# --------------------------------------------------------------------------- #
# generic concurrent map with per-item failure capture
# --------------------------------------------------------------------------- #
def fetch_market(items: list[T], fetch_one: Callable[[T], list[R]], *,
                 workers: int = 8,
                 key: Callable[[T], str] = str) -> tuple[list[R], list[str]]:
    """Map ``fetch_one`` over ``items`` on a thread pool. Returns
    ``(flattened_results, failed_keys_sorted)``; an exception for one item is
    non-fatal and lands its ``key`` in ``failed``.
    """
    out: list[R] = []
    failed: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(fetch_one, it): it for it in items}
        for fut, it in futs.items():
            try:
                out.extend(fut.result())
            except Exception:
                failed.append(key(it))
    failed.sort()
    return out, failed


def _append_log(rec: dict) -> None:
    _LOG.parent.mkdir(exist_ok=True)
    with _LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


# --------------------------------------------------------------------------- #
# zco0 rolling shard
# --------------------------------------------------------------------------- #
def _zco0_pair(client: httpx.Client, stock: Stock, branch_id: str,
               branch_name: str, d_from: str, d_to: str) -> list[BranchFlow]:
    """One ``(stock, branch)`` pair -> ``BranchFlow`` rows for its active days in
    ``[d_from, d_to]``. All-zero days are dropped. Close price is left 0.0 here
    (joined per-day by the web app / a later close backfill)."""
    page = fetch_zco0(client, stock.stock_id, branch_id, d_from, d_to)
    rows: list[BranchFlow] = []
    for r in page.rows:
        if not (r.buy_lots or r.sell_lots):
            continue
        rows.append(BranchFlow(
            date=r.date, market=stock.market, stock_id=stock.stock_id,
            branch_id=branch_id, branch_name=branch_name,
            buy_shares=r.buy_lots * _LOTS, sell_shares=r.sell_lots * _LOTS,
            net_shares=r.net_lots * _LOTS, close_price=0.0))
    return rows


def _run_shard(client: httpx.Client, shard: list[Stock], branches: list[tuple[str, str]],
               state: dict[str, str], *, target: str, max_lookback: int,
               workers: int, fail_ratio: float = 0.10,
               flush: "Callable[[dict[str, list[BranchFlow]], list[str]], None] | None" = None,
               flush_every: int = 0
               ) -> tuple[dict[str, list[BranchFlow]], list[str]]:
    """Fetch every branch for every stock in ``shard``. Good rows are always
    kept; a stock is flagged in ``failed`` only when more than ``fail_ratio`` of
    its branch calls errored (isolated transient misses are expected at ~820
    calls/stock).

    If ``flush`` is given, it is called with ``(by_date_so_far, stock_ids_done)``
    every ``flush_every`` stocks (and the accumulator is cleared) so a long
    backfill checkpoints instead of losing everything on a timeout. Returns
    ``({date: [flows]} still un-flushed, failed_stock_ids)``.
    """
    by_date: dict[str, list[BranchFlow]] = defaultdict(list)
    failed: list[str] = []
    pending: list[str] = []
    for idx, stock in enumerate(shard, 1):
        d_from, d_to = window_for(stock.stock_id, state, target=target,
                                  max_lookback_days=max_lookback)
        if d_from <= d_to:
            pair_items = list(branches)

            def one(item: tuple[str, str], _s=stock, _f=d_from, _t=d_to) -> list[BranchFlow]:
                return _zco0_pair(client, _s, item[0], item[1], _f, _t)

            flows, bad = fetch_market(pair_items, one, workers=workers,
                                      key=lambda it: f"{stock.stock_id}/{it[0]}")
            for f in flows:
                by_date[f.date].append(f)
            if pair_items and len(bad) / len(pair_items) > fail_ratio:
                failed.append(stock.stock_id)
        pending.append(stock.stock_id)
        if flush and flush_every and idx % flush_every == 0:
            flush(by_date, pending)
            by_date = defaultdict(list)
            pending = []
    if flush and pending:
        flush(by_date, pending)
        by_date = defaultdict(list)
    failed.sort()
    return by_date, failed


def _merge_and_upload(by_date: dict[str, list[BranchFlow]], *, repo: str,
                      manifest: dict) -> dict[str, int]:
    """Daily path only: for each affected date, download the existing
    ``<date>.parquet``, append, dedup on ``(date, stock, branch)``, re-upload.
    Race-free because exactly one daily job runs at a time. Updates ``manifest``.
    Returns ``{date: row_count}``.
    """
    _WORK.mkdir(exist_ok=True)
    counts: dict[str, int] = {}
    for date_iso, new_flows in sorted(by_date.items()):
        canon = f"{date_iso}.parquet"
        path = str(_WORK / canon)
        tag = release_tag(date_iso)
        existing: list[BranchFlow] = []
        if repo:
            got = download_asset(tag, canon, str(_WORK), repo=repo)
            if got:
                existing = read_flows(got)
        merged = dedup_flows(existing + new_flows)
        n = write_parquet(merged, path)
        counts[date_iso] = n
        if repo:
            upload_assets(tag, [path], repo=repo)
        _set_day(manifest, date_iso,
                 asset_url(tag, canon, repo=repo) if repo else path, n)
    return counts


def _set_day(manifest: dict, date_iso: str, url: str, n: int) -> None:
    """Record one day-partition in the manifest without trimming (retention is
    the daily path's job, via ``merge_manifest`` on the target day)."""
    days = manifest.setdefault("days", {})
    entry = days.get(date_iso, {})
    entry["flows"] = url
    entry.setdefault("rows", {})["flows"] = n
    days[date_iso] = entry
    manifest["updated"] = _now()


_STAGING_TAG = "backfill-staging"


def _stage_flush(by_date: dict[str, list[BranchFlow]], done_ids: list[str],
                 target: str, *, repo: str, stage_tag: str, flush_idx: int) -> int:
    """Backfill checkpoint: pack this flush's rows -- however many months they
    span -- into ONE parquet, and the stocks it finished into ONE small JSON,
    uploaded to a single shared ``backfill-staging`` release (~2 API calls per
    flush, not one per affected date -- a per-date design made 24 parallel
    shards exhaust the token's 5000/hr quota in minutes on 2026-09-11).

    No local ``manifest.json`` / ``refresh_state.json`` write and no git commit
    here: many shards run at once and a git-level merge of those shared files
    is exactly as racy as the upload was. ``--mode compact`` (single job, run
    once after every shard finishes) folds all staging into the canonical day
    files and ``refresh_state.json`` in one serialized pass. Returns the row
    count staged.
    """
    _WORK.mkdir(exist_ok=True)
    flows = [f for fl in by_date.values() for f in fl]
    data_path = str(_WORK / f"data_{stage_tag}_{flush_idx:05d}.parquet")
    state_path = str(_WORK / f"state_{stage_tag}_{flush_idx:05d}.json")
    n = write_parquet(flows, data_path)
    with open(state_path, "w", encoding="utf-8") as fh:
        json.dump({"target": target, "stocks": done_ids}, fh)
    if repo:
        upload_assets(_STAGING_TAG, [data_path, state_path], repo=repo)
    return n


def _compact(*, repo: str, manifest: dict, month: str = "") -> dict[str, int]:
    """Fold every staged backfill flush on ``backfill-staging`` into the
    canonical per-day partitions (dedup union) and ``refresh_state.json``, then
    delete the staging assets. ``month`` is accepted for CLI symmetry but has no
    effect -- staging is not month-partitioned (see ``_stage_flush``). Returns
    ``{date: final_row_count}``.
    """
    _WORK.mkdir(exist_ok=True)
    assets = list_release_assets(_STAGING_TAG, repo=repo)
    data_assets = sorted(a for a in assets if a.startswith("data_"))
    state_assets = sorted(a for a in assets if a.startswith("state_"))
    if not data_assets and not state_assets:
        return {}

    state = load_state()
    for sa in state_assets:
        got = download_asset(_STAGING_TAG, sa, str(_WORK), repo=repo)
        if not got:
            continue
        with open(got, encoding="utf-8") as fh:
            delta = json.load(fh)
        state = mark_done(state, delta.get("stocks", []), delta.get("target", ""))
    save_state(state)

    by_date: dict[str, list[BranchFlow]] = defaultdict(list)
    for da in data_assets:
        got = download_asset(_STAGING_TAG, da, str(_WORK), repo=repo)
        if not got:
            continue
        for f in read_flows(got):
            by_date[f.date].append(f)

    by_tag: dict[str, list[str]] = defaultdict(list)
    for date_iso in by_date:
        by_tag[release_tag(date_iso)].append(date_iso)

    counts: dict[str, int] = {}
    for tag, dates in sorted(by_tag.items()):
        tag_assets = set(list_release_assets(tag, repo=repo))   # 1 call/month
        for date_iso in sorted(dates):
            canon = f"{date_iso}.parquet"
            existing: list[BranchFlow] = []
            if canon in tag_assets:
                got = download_asset(tag, canon, str(_WORK), repo=repo)
                if got:
                    existing = read_flows(got)
            merged = dedup_flows(existing + by_date[date_iso])
            path = str(_WORK / canon)
            n = write_parquet(merged, path)
            counts[date_iso] = n
            upload_assets(tag, [path], repo=repo)
            _set_day(manifest, date_iso, asset_url(tag, canon, repo=repo), n)

    for a in assets:
        delete_asset(_STAGING_TAG, a, repo=repo)
    return counts


# --------------------------------------------------------------------------- #
# zco sweep (top-15 / top-15 board for `target`)
# --------------------------------------------------------------------------- #
def _zco_sweep(client: httpx.Client, stocks: list[Stock],
               name_map: dict[str, str], *, workers: int
               ) -> tuple[list[dict], list[str]]:
    def one(stock: Stock) -> list[dict]:
        page = fetch_zco(client, stock.stock_id)
        recs: list[dict] = []
        for b in page.branches:
            recs.append({
                "data_date": page.data_date, "market": stock.market,
                "stock_id": stock.stock_id, "branch_id": "",
                "branch_name": b.branch_name,
                "side": "buy" if b.net_lots >= 0 else "sell",
                "buy_lots": b.buy_lots, "sell_lots": b.sell_lots,
                "net_lots": b.net_lots,
                "avg_buy_cost": page.avg_buy_cost, "avg_sell_cost": page.avg_sell_cost,
            })
        return recs

    return fetch_market(stocks, one, workers=workers, key=lambda s: s.stock_id)


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #
def _universe(skip_tpex: bool) -> list[Stock]:
    with httpx.Client() as uc:
        stocks = twse_traded(uc)
        if not skip_tpex:
            stocks = stocks + tpex_traded(uc)
    return stocks


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("daily", "backfill", "compact"),
                    default="daily")
    ap.add_argument("--date", default="")
    ap.add_argument("--month", default="",
                    help="compact mode: only fold staging for data-<YYYY-MM>")
    ap.add_argument("--flush-every", type=int, default=10,
                    help="backfill mode: checkpoint (upload staging + save state) "
                         "every N stocks")
    ap.add_argument("--repo", default=os.environ.get("GH_REPO", ""))
    ap.add_argument("--skip-tpex", action="store_true",
                    default=os.environ.get("TPEX_ENABLED", "1") == "0")
    ap.add_argument("--skip-sweep", action="store_true",
                    help="daily mode: skip the zco top-15/15 board sweep")
    ap.add_argument("--force", action="store_true",
                    help="daily mode: re-run even if target is already in the manifest")
    ap.add_argument("--cycle-days", type=int,
                    default=int(os.environ.get("CYCLE_DAYS", "20")))
    ap.add_argument("--max-lookback", type=int, default=45)
    ap.add_argument("--months", type=int, default=15,
                    help="backfill mode: how far back the window reaches")
    ap.add_argument("--shard", default="",
                    help='backfill mode: "i/N" -- process only the i-th of N '
                         "stock slices (1-based)")
    ap.add_argument("--workers", type=int,
                    default=int(os.environ.get("WORKERS", "8")))
    ap.add_argument("--stock-limit", type=int,
                    default=int(os.environ.get("STOCK_LIMIT", "0")))
    args = ap.parse_args(argv)
    started = _now()

    if args.mode == "compact":
        manifest = load_manifest()
        counts = _compact(repo=args.repo, manifest=manifest, month=args.month)
        save_manifest(manifest)
        _append_log({"mode": "compact", "status": "ok",
                     "dates_compacted": len(counts),
                     "row_count": sum(counts.values()),
                     "started_at": started, "finished_at": _now()})
        print(f"compacted {len(counts)} day-partitions")
        return 0

    with httpx.Client() as probe:
        target = resolved_trading_date(probe)
    if target is None:
        _append_log({"date": args.date or "?", "mode": args.mode, "status": "skipped",
                     "note": "could not resolve latest trading date",
                     "started_at": started, "finished_at": _now()})
        print("could not resolve latest trading date (STOCK_DAY_ALL); skip")
        return 0
    if args.date and args.date != target and args.mode == "daily":
        print(f"--date {args.date} != latest published {target}; using {target}")
    if args.date and args.mode == "backfill":
        target = args.date

    manifest = load_manifest()
    if (args.mode == "daily" and not args.force
            and target in manifest.get("days", {})
            and manifest["days"][target].get("sweep_done")):
        _append_log({"date": target, "mode": "daily", "status": "skipped",
                     "note": "already ingested", "started_at": started,
                     "finished_at": _now()})
        print(f"{target} already ingested; skip (use --force)")
        return 0

    stocks = _universe(args.skip_tpex)
    if args.stock_limit > 0:
        stocks = stocks[:args.stock_limit]
    branches = _branch_axis()
    name_map = dict(branches)

    client = new_client()
    try:
        # ---- zco sweep -------------------------------------------------------
        if args.mode == "daily" and not args.skip_sweep:
            recs, sweep_failed = _zco_sweep(client, stocks, name_map,
                                            workers=args.workers)
            _WORK.mkdir(exist_ok=True)
            latest_path = str(_WORK / "latest.parquet")
            n_latest = write_latest(recs, latest_path)
            if args.repo:
                upload_assets("meta", [latest_path], repo=args.repo)
            _append_log({"date": target, "mode": "daily", "leg": "sweep",
                         "status": "ok" if not sweep_failed else "partial",
                         "stock_count": len(stocks), "row_count": n_latest,
                         "failed": sweep_failed, "started_at": started,
                         "finished_at": _now()})

        # ---- rolling / backfill zco0 shard --------------------------------- #
        state = load_state()
        total = {"dates": 0, "rows": 0}

        if args.mode == "backfill":
            lookback = args.months * 31
            shard = _apply_shard(stocks, args.shard)
            legname = f"backfill {args.shard or 'all'}"
            stage_tag = "s" + (args.shard or "all").replace("/", "-")
            flush_idx = {"n": 0}

            def _flush(bd: dict[str, list[BranchFlow]], done_ids: list[str],
                       _tag=stage_tag) -> None:
                # state (`state` var here) is read-only during backfill -- it is
                # NOT advanced/saved locally. Each flush's progress is staged as
                # its own file; `--mode compact` is the only thing that ever
                # writes refresh_state.json / manifest.json for backfilled data,
                # in one serialized pass after every shard is done.
                flush_idx["n"] += 1
                n = _stage_flush(bd, done_ids, target, repo=args.repo,
                                 stage_tag=stage_tag, flush_idx=flush_idx["n"])
                total["dates"] += 1
                total["rows"] += n

            _, shard_failed = _run_shard(
                client, shard, branches, state, target=target,
                max_lookback=lookback, workers=args.workers,
                flush=_flush, flush_every=max(1, args.flush_every))
        else:
            lookback = args.max_lookback
            shard = [s for s in stocks
                     if s.stock_id in set(next_shard(
                         [x.stock_id for x in stocks], state,
                         cycle_days=args.cycle_days))]
            legname = "shard"
            by_date, shard_failed = _run_shard(
                client, shard, branches, state, target=target,
                max_lookback=lookback, workers=args.workers)
            c = _merge_and_upload(by_date, repo=args.repo, manifest=manifest)
            total["dates"], total["rows"] = len(c), sum(c.values())
            state = mark_done(state, [s.stock_id for s in shard], target)
            save_state(state)

        _append_log({"date": target, "mode": args.mode, "leg": legname,
                     "status": "ok" if not shard_failed else "partial",
                     "shard_stocks": len(shard), "dates_written": total["dates"],
                     "row_count": total["rows"], "failed": shard_failed,
                     "started_at": started, "finished_at": _now()})
    finally:
        client.close()

    if args.mode == "daily":
        entry = manifest.get("days", {}).get(target, {})
        entry["sweep_done"] = not args.skip_sweep
        manifest.update(merge_manifest(manifest, target, entry))
        # Retention only on the daily path -- backfill deliberately reaches past
        # the 13-month window and must not prune what it is filling.
        if args.repo:
            prune_old_releases(repo=args.repo)
    save_manifest(manifest)
    return 0


def _apply_shard(stocks: list[Stock], spec: str) -> list[Stock]:
    """``spec`` == ``"i/N"`` -> the i-th (1-based) contiguous slice of ``stocks``."""
    if not spec:
        return stocks
    i, n = (int(x) for x in spec.split("/"))
    per = max(1, -(-len(stocks) // n))
    lo = (i - 1) * per
    return stocks[lo:lo + per]


if __name__ == "__main__":
    sys.exit(main())
