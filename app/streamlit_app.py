"""台股分點籌碼追蹤 -- 首頁。四個查詢頁面在左側導覽列（``pages/`` 目錄）。

只讀我們自己的私有資料庫（見 docs/specs 設計文件）；資料來自 SysJust .djhtm 聚合器
的每日彙整，非官方逐筆成交，僅供研究與趨勢參考。
"""
from __future__ import annotations

import sys
from pathlib import Path

# Streamlit Cloud execs this file without the repo root on sys.path (unlike a
# local `python -m streamlit run app/streamlit_app.py` from the repo root, where
# `-m` adds the CWD) -- add it explicitly so `from app import ...` resolves.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

from app import config, data, ui_common

st.set_page_config(page_title="台股分點籌碼追蹤", page_icon="📊", layout="wide")
ui_common.inject_style()


@st.cache_data(ttl=6 * 3600)
def _manifest() -> dict:
    return data.fetch_manifest(config.CODE_REPO)


ui_common.page_header(
    "📊", "台股分點籌碼追蹤",
    "全市場（TWSE＋TPEx）・全分點・每日自動更新 — 給交易員與研究員的籌碼儀表板",
)

st.markdown("#### 🚀 快速導覽")
nav_cards = [
    ("📈", "個股籌碼", "搜尋股票 → 近 N 日各分點買超／賣超排行，選定分點的每日走勢",
     "pages/1_個股籌碼.py"),
    ("💰", "個股 × 分點成本", "選股＋選分點＋選區間 → FIFO 估算平均成本、股價×成交量×庫存疊圖",
     "pages/2_個股分點成本.py"),
    ("🏢", "分點總覽", "選分點＋選區間 → 所有進出個股一覽，含絕對／相對大盤勝率回測",
     "pages/3_分點總覽.py"),
    ("📊", "多股比較", "同時比較多檔股票的籌碼流向與股價表現，適合同業族群比對",
     "pages/4_多股比較.py"),
]
cols = st.columns(4)
for col, (icon, title, desc, target) in zip(cols, nav_cards):
    with col:
        with st.container(border=True):
            st.markdown(f"<div class='nav-card-icon'>{icon}</div>", unsafe_allow_html=True)
            st.markdown(f"**{title}**")
            st.caption(desc)
            st.page_link(target, label="前往 →")

st.markdown("")

try:
    manifest = _manifest()
    days = sorted(manifest.get("days", {}))
    if days:
        m1, m2, m3 = st.columns(3)
        m1.metric("資料起始", days[0])
        m2.metric("資料結束", days[-1])
        m3.metric("累積交易日數", f"{len(days):,}")
    else:
        st.warning("資料庫目前是空的（管線尚未產出任何一天的資料）。")
except Exception as e:  # noqa: BLE001
    st.error(f"讀取資料索引失敗：{e}")

with st.container(border=True):
    st.markdown(
        """
⚠️ **資料性質**：分點買賣超彙整自第三方聚合站台（非交易所官方逐筆資料），
FIFO 成本為簡化估算、**非真實成交均價**，回測結果為歷史統計、不代表未來績效，
僅供相對比較與趨勢觀察。
"""
    )
