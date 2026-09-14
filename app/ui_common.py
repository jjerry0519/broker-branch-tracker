"""Shared Streamlit page helpers: cached data loading, stock/branch pickers,
the FIFO disclaimer block."""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from app import config, data, lookup
from app.fifo import DISCLAIMER

INSTA_GRADIENT = "linear-gradient(135deg, #833AB4 0%, #E1306C 50%, #F77737 78%, #FCAF45 100%)"


def inject_style() -> None:
    """Site-wide visual theme -- vibrant gradient accents, rounded cards, soft
    shadows. Purely cosmetic (CSS only); no widget/feature is touched."""
    st.markdown(
        f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@500;600;700&family=Noto+Sans+TC:wght@400;500;700&display=swap');

        html, body, [class*="css"], [data-testid="stAppViewContainer"] {{
            font-family: 'Poppins', 'Noto Sans TC', sans-serif;
        }}

        /* gradient page-header banner */
        .ins-header {{
            display: flex; align-items: center; gap: 16px;
            background: {INSTA_GRADIENT};
            border-radius: 20px;
            padding: 22px 28px;
            margin-bottom: 22px;
            box-shadow: 0 8px 24px rgba(225, 48, 108, 0.22);
        }}
        .ins-header-icon {{ font-size: 2.2rem; line-height: 1; }}
        .ins-header-title {{
            color: #fff; font-weight: 700; font-size: 1.5rem; letter-spacing: .01em;
        }}
        .ins-header-sub {{
            color: rgba(255,255,255,.92); font-size: .92rem; margin-top: 2px;
        }}

        /* nav card icon on the home page */
        .nav-card-icon {{
            font-size: 1.8rem;
            background: {INSTA_GRADIENT};
            -webkit-background-clip: text; background-clip: text;
            -webkit-text-fill-color: transparent;
            width: fit-content; margin-bottom: 2px;
        }}

        /* metric cards */
        [data-testid="stMetric"] {{
            background: #fff;
            border-radius: 16px;
            padding: 14px 16px 10px;
            border: 1px solid rgba(131, 58, 180, 0.12);
            box-shadow: 0 2px 10px rgba(131, 58, 180, 0.08);
            transition: transform .12s ease, box-shadow .12s ease;
        }}
        [data-testid="stMetric"]:hover {{
            transform: translateY(-2px);
            box-shadow: 0 6px 16px rgba(225, 48, 108, 0.16);
        }}
        [data-testid="stMetricValue"] {{ color: #C1246B; }}

        /* bordered containers (used for grouping query controls) */
        [data-testid="stVerticalBlockBorderWrapper"] {{
            border-radius: 16px !important;
        }}

        /* dataframes / tables */
        [data-testid="stDataFrame"] {{
            border-radius: 14px; overflow: hidden;
            border: 1px solid rgba(131, 58, 180, 0.12);
        }}

        /* buttons: pill shape with gradient */
        .stButton>button, .stDownloadButton>button {{
            background: {INSTA_GRADIENT};
            color: #fff; border: none; border-radius: 999px;
            font-weight: 600; padding: 8px 20px;
            box-shadow: 0 3px 10px rgba(225, 48, 108, 0.25);
            transition: filter .12s ease, transform .12s ease;
        }}
        .stButton>button:hover, .stDownloadButton>button:hover {{
            filter: brightness(1.08); transform: translateY(-1px); color: #fff;
        }}

        /* tabs underline -> gradient */
        .stTabs [aria-selected="true"] {{
            color: #C1246B !important;
        }}
        .stTabs [data-baseweb="tab-highlight"] {{
            background: {INSTA_GRADIENT} !important;
        }}

        /* sidebar */
        [data-testid="stSidebar"] {{
            background: linear-gradient(180deg, #FBF4FF 0%, #FFF 60%);
        }}

        /* divider */
        hr {{ border-color: rgba(131, 58, 180, 0.18) !important; }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def page_header(icon: str, title: str, subtitle: str = "") -> None:
    """Gradient banner used in place of ``st.title`` on every page."""
    sub_html = f'<div class="ins-header-sub">{subtitle}</div>' if subtitle else ""
    st.markdown(
        f"""
        <div class="ins-header">
          <div class="ins-header-icon">{icon}</div>
          <div>
            <div class="ins-header-title">{title}</div>
            {sub_html}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


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


@st.cache_data(ttl=3600)
def refresh_state() -> dict[str, str]:
    return data.fetch_refresh_state(config.CODE_REPO)


def rotation_note(stock_id: str) -> str:
    """One-line note on when this stock's full daily detail was last synced and
    when the rolling shard is expected to update it next."""
    est = data.rotation_estimate(stock_id, refresh_state())
    if est["last_synced"] is None:
        return "此股票尚未被滾動排程處理過，預計很快會被排入（下一輪次）。"
    if est["est_days"] <= 0:
        return f"資料庫更新到 **{est['last_synced']}**，預計**今天或明天**就會補到最新。"
    return (f"資料庫更新到 **{est['last_synced']}**，"
           f"依目前排程預計約 **{est['est_days']} 天後（{est['est_date']}）** 補到最新"
           f"（估計值，非保證）。")


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


def download_csv_button(df, filename: str, *, label: str = "⬇️ 下載 CSV",
                        key: str | None = None) -> None:
    """utf-8-sig so Excel opens the Chinese column headers without mangling
    them (plain utf-8 shows up as garbled text in Excel on Windows)."""
    st.download_button(label, df.to_csv(index=False).encode("utf-8-sig"),
                       file_name=filename, mime="text/csv", key=key)
