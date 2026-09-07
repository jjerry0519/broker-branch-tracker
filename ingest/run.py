"""Daily pipeline orchestrator.

Ties the whole ingest together:

1. ``resolved_trading_date`` guard -- if the BSR site is not showing the target
   date (weekend / holiday / not-yet-published) log a ``skipped`` row and exit 0.
2. Build the traded universe (TWSE + TPEx OpenAPI).
3. TWSE leg: concurrent BSR fetch (``ThreadPoolExecutor``), aggregate to
   ``BranchFlow`` rows, write + upload the ``<date>_twse.parquet`` asset.
4. TPEx leg (unless skipped): sequential fetch through one shared headed
   ``TpexBrowser`` (``workers=1`` -- the browser owns a single page), write +
   upload the ``<date>_tpex.parquet`` asset.
5. Prune releases outside the 13-month window and persist ``manifest.json``.

One ``ingest_log`` row per market is appended to ``logs/ingest_log.jsonl``.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import httpx

from ingest.aggregate import aggregate
from ingest.schema import BranchFlow
from ingest.storage import (asset_url, load_manifest, merge_manifest,
                            prune_old_releases, release_tag, save_manifest,
                            upload_assets, write_parquet)
from ingest.twse_client import fetch_stock as twse_fetch, new_client as twse_new
from ingest.tpex_client import TpexBrowser
from ingest.universe import Stock, resolved_trading_date, tpex_traded, twse_traded

_TW = ZoneInfo("Asia/Taipei")
_LOG = Path("logs/ingest_log.jsonl")


def fetch_market(stocks: list[Stock],
                 fetch_one: Callable[[Stock], list[BranchFlow]],
                 *, workers: int = 8) -> tuple[list[BranchFlow], list[str]]:
    """Concurrently map ``fetch_one`` over ``stocks``.

    Returns ``(all_flows, failed_stock_ids)``. An exception from ``fetch_one``
    for a stock is not fatal -- that ``stock_id`` lands in ``failed`` (sorted).
    """
    flows: list[BranchFlow] = []
    failed: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(fetch_one, s): s for s in stocks}
        for fut, s in futs.items():
            try:
                flows.extend(fut.result())
            except Exception:
                failed.append(s.stock_id)
    failed.sort()
    return flows, failed


def _twse_one(client, date_iso):
    def inner(s: Stock) -> list[BranchFlow]:
        page = twse_fetch(client, s.stock_id, max_attempts=30)
        # BsrPage has no close price / date -> always from universe.Stock
        return aggregate(page.rows, date=date_iso, market="twse",
                         stock_id=s.stock_id, close_price=s.close_price)
    return inner


def _tpex_one(browser: TpexBrowser, date_iso):
    # NOTE: one shared headed browser, one page, token-per-request -> MUST run
    # sequentially (fetch_market with workers=1). browser.fetch_stock already
    # reloads once on TpexRetry.
    def inner(s: Stock) -> list[BranchFlow]:
        page = browser.fetch_stock(s.stock_id)
        close = page.close_price or s.close_price   # brokerBS carries close; fall back
        return aggregate(page.rows, date=date_iso, market="tpex",
                         stock_id=s.stock_id, close_price=close)
    return inner


def _append_log(rec: dict) -> None:
    _LOG.parent.mkdir(exist_ok=True)
    with _LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=dt.datetime.now(_TW).date().isoformat())
    ap.add_argument("--repo", default=os.environ.get("GH_REPO", ""))
    ap.add_argument("--skip-tpex", action="store_true",
                    default=os.environ.get("TPEX_ENABLED", "1") == "0")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args(argv)
    started = dt.datetime.now(dt.UTC).isoformat()

    with httpx.Client() as probe:
        site_date = resolved_trading_date(probe)
    if site_date != args.date:
        _append_log({"date": args.date, "market": "*", "status": "skipped",
                     "note": f"site date {site_date}", "started_at": started,
                     "finished_at": dt.datetime.now(dt.UTC).isoformat()})
        print(f"non-trading day or not published ({site_date}); skip")
        return 0

    with httpx.Client() as uc:
        twse_stocks = twse_traded(uc)
        tpex_stocks = [] if args.skip_tpex else tpex_traded(uc)

    all_flows: dict[str, list[BranchFlow]] = {"twse": [], "tpex": []}
    manifest = load_manifest()

    with twse_new() as tc:
        flows, failed = fetch_market(twse_stocks, _twse_one(tc, args.date),
                                     workers=args.workers)
    all_flows["twse"] = flows
    _write_upload("twse", flows, failed, twse_stocks, args, started, manifest)

    if not args.skip_tpex:
        with TpexBrowser() as br:                       # headed; sequential (workers=1)
            flows, failed = fetch_market(tpex_stocks, _tpex_one(br, args.date), workers=1)
        all_flows["tpex"] = flows
        _write_upload("tpex", flows, failed, tpex_stocks, args, started, manifest)

    if args.repo:
        prune_old_releases(repo=args.repo)
    save_manifest(manifest)
    return 0


def _write_upload(market, flows, failed, stocks, args, started, manifest):
    fname = f"{args.date}_{market}.parquet"
    path = str(Path("out") / fname)
    Path("out").mkdir(exist_ok=True)
    n = write_parquet(flows, path)
    tag = release_tag(args.date)
    if args.repo:
        upload_assets(tag, [path], repo=args.repo)
        url = asset_url(tag, fname, repo=args.repo)
    else:
        url = path
    entry = manifest.get("days", {}).get(args.date, {})
    entry[market] = url
    entry.setdefault("rows", {})
    entry["rows"][market] = n
    manifest.update(merge_manifest(manifest, args.date, entry))
    status = "ok" if not failed else "partial"
    _append_log({"date": args.date, "market": market, "status": status,
                 "stock_count": len(stocks), "row_count": n,
                 "failed": failed, "started_at": started,
                 "finished_at": dt.datetime.now(dt.UTC).isoformat()})


if __name__ == "__main__":
    sys.exit(main())
