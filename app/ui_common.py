"""Shared Streamlit page helpers: cached data loading, stock/branch pickers,
the FIFO disclaimer block."""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from app import config, data, lookup
from app.fifo import DISCLAIMER


@st.cache_data(ttl=6 * 3600)
def manifest() -> dict:
    return data.fetch_manifest(config.CODE_REPO)


@st.cache_data(ttl=6 * 3600, show_spinner="下載資料中…")
def cached_days(dates: tuple[str, ...]) -> list[str]:
    """Download (or reuse) every day-partition in ``dates``; returns the local
    paths as strings (Streamlit's cache needs hashable/serialisable output)."""
    token = config.get_token()
    if not token:
        st.error("缺少 GH_TOKEN（讀取私有資料庫需要）。本機測試請設環境變數 "
                 "GH_TOKEN；部署在 Streamlit Cloud 請在 App secrets 設定。")
        st.stop()
    paths = data.ensure_days_cached(list(dates), manifest(), token=token,
                                    cache_dir=config.CACHE_DIR)
    return [str(p) for p in paths]


def days_as_paths(dates: list[str]) -> list[Path]:
    return [Path(p) for p in cached_days(tuple(dates))]


@st.cache_data(ttl=3600, show_spinner="下載今日即時榜中…")
def cached_latest_board() -> str | None:
    """Today's zco top-15/top-15 sweep -- overwritten daily by the pipeline, so
    this is re-downloaded hourly (not cached forever like a day-partition)."""
    token = config.get_token()
    if not token:
        return None
    p = data.download_latest_board(token=token, cache_dir=config.CACHE_DIR)
    return str(p) if p else None


def latest_board_for_stock(stock_id: str):
    path = cached_latest_board()
    return data.latest_board_for_stock(Path(path) if path else None, stock_id)


def pick_stock(label: str = "股票（股號或名稱）", *, key: str = "stock_q") -> str | None:
    q = st.text_input(label, key=key, placeholder="例如 2330 或 台積電")
    if not q:
        return None
    matches = lookup.find_stocks(q, db_path=config.REFERENCE_DB)
    if not matches:
        st.warning("查無符合的股票。")
        return None
    options = {f"{sid} {name}（{'上市' if mkt == 'twse' else '上櫃'}）": sid
              for sid, name, mkt in matches}
    picked = st.selectbox("符合的股票", list(options), key=f"{key}_pick")
    return options.get(picked)


def pick_branch(label: str = "券商分點（代號或名稱）", *, key: str = "branch_q") -> str | None:
    q = st.text_input(label, key=key, placeholder="例如 9200 或 富邦")
    if not q:
        return None
    matches = lookup.find_branches(q, db_path=config.REFERENCE_DB)
    if not matches:
        st.warning("查無符合的分點。")
        return None
    options = {f"{bid} {name}": bid for bid, name in matches}
    picked = st.selectbox("符合的分點", list(options), key=f"{key}_pick")
    return options.get(picked)


def render_disclaimer() -> None:
    st.caption(f"⚠️ {DISCLAIMER}")
