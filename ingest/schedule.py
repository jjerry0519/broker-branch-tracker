"""The 20-day rolling-shard scheduler for Architecture A.

The full ``(stock, branch)`` matrix (~1.9k stocks x ~880 branches ~= 1.7M cells)
is too large to re-pull every day against a free third-party mirror. Instead the
stock list is split into ``cycle_days`` shards; each daily run refreshes one
shard, so every cell is revisited once per cycle. Because ``zco0`` is a *range*
query, one request per (stock, branch) backfills every trading day since that
stock was last refreshed -- the store never has daily gaps, only a bounded
staleness (<= ``cycle_days``) on the newest day of cells nobody has looked at.

State is a flat ``{stock_id: "YYYY-MM-DD"}`` map -- the date through which *every*
branch of that stock is complete in the store. It rides as a ``meta`` release
asset on the data repo (JSON, ~40 KB), not git, so daily churn stays out of
history.

Pure module: selection + window math + load/save. The network sweep is in
:mod:`ingest.run`.
"""
from __future__ import annotations

import datetime as dt
import json

_ISO = "%Y-%m-%d"


def _d(iso: str) -> dt.date:
    return dt.datetime.strptime(iso, _ISO).date()


def _plus(iso: str, days: int) -> str:
    return (_d(iso) + dt.timedelta(days=days)).strftime(_ISO)


def shard_size(n_stocks: int, cycle_days: int) -> int:
    """How many stocks one daily run handles (ceil division, >= 1)."""
    cycle_days = max(1, cycle_days)
    return max(1, -(-n_stocks // cycle_days))


def next_shard(stock_ids: list[str], state: dict[str, str], *,
               cycle_days: int = 20) -> list[str]:
    """The stocks to refresh on this run: those never refreshed first, then the
    ones whose ``refreshed_through`` is oldest. Deterministic; ties break on the
    stock id so shards are stable across runs.
    """
    size = shard_size(len(stock_ids), cycle_days)
    ordered = sorted(stock_ids, key=lambda s: (state.get(s, ""), s))
    return ordered[:size]


def window_for(stock_id: str, state: dict[str, str], *, target: str,
               max_lookback_days: int = 45) -> tuple[str, str]:
    """``(d_from, d_to)`` ISO range to request for ``stock_id`` this run.

    ``d_to`` is always ``target``. ``d_from`` is the day after the stock's
    ``refreshed_through``; for a stock never refreshed (or one stale beyond
    ``max_lookback_days``) it is clamped to ``target - max_lookback_days`` so a
    single daily run can never ask for a giant range (that is the backfill job's
    task).
    """
    floor = _plus(target, -max_lookback_days)
    through = state.get(stock_id, "")
    d_from = _plus(through, 1) if through else floor
    if d_from < floor:
        d_from = floor
    if d_from > target:
        d_from = target
    return d_from, target


def mark_done(state: dict[str, str], stock_ids: list[str], target: str) -> dict[str, str]:
    """Return a new state with each id in ``stock_ids`` advanced to ``target``
    (never moved backwards)."""
    out = dict(state)
    for s in stock_ids:
        if out.get(s, "") < target:
            out[s] = target
    return out


def stale_stocks(stock_ids: list[str], state: dict[str, str], *, target: str,
                 cycle_days: int = 20) -> list[str]:
    """Stocks whose newest complete day is more than ``cycle_days`` behind
    ``target`` -- i.e. the cycle is falling behind and the daily run should be
    given a bigger shard (or a catch-up run dispatched)."""
    cutoff = _plus(target, -cycle_days)
    return sorted(s for s in stock_ids if state.get(s, "") < cutoff)


def load_state(path: str = "refresh_state.json") -> dict[str, str]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    return {str(k): str(v) for k, v in data.get("refreshed_through", data).items()}


def save_state(state: dict[str, str], path: str = "refresh_state.json") -> None:
    payload = {
        "updated": dt.datetime.now(dt.UTC).isoformat(),
        "refreshed_through": dict(sorted(state.items())),
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=0)
        fh.write("\n")
