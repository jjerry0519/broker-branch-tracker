"""台股分點籌碼追蹤 -- 首頁。三個查詢頁面在左側導覽列（``pages/`` 目錄）。

只讀我們自己的私有資料庫（見 docs/specs 設計文件）；資料來自 SysJust .djhtm 聚合器
的每日彙整，非官方逐筆成交，僅供研究與趨勢參考。
"""
from __future__ import annotations

import streamlit as st

from app import config, data

st.set_page_config(page_title="台股分點籌碼追蹤", page_icon="📊", layout="wide")


@st.cache_data(ttl=6 * 3600)
def _manifest() -> dict:
    return data.fetch_manifest(config.CODE_REPO)


st.title("📊 台股分點籌碼追蹤")

st.markdown(
    """
本工具追蹤 **全部上市（TWSE）＋ 全部上櫃（TPEx）** 個股的券商分點買賣超，
每日自動更新、保留約 13 個月歷史。左側選單有三個查詢頁面：

| 頁面 | 功能 |
|---|---|
| **個股籌碼** | 搜尋股票 → 近 N 日各分點買超／賣超排行、選定分點的每日買賣超走勢 |
| **個股 × 分點成本** | 選股 + 選分點 + 選區間 → FIFO 估算該分點的平均持股成本與估計庫存 |
| **分點總覽** | 選分點 + 選區間 → 該分點期間內所有有進出的個股一覽 |

⚠️ **資料性質**：分點買賣超彙整自第三方聚合站台（非交易所官方逐筆資料），
FIFO 成本為簡化估算、**非真實成交均價**，僅供相對比較與趨勢觀察。
"""
)

try:
    manifest = _manifest()
    days = sorted(manifest.get("days", {}))
    if days:
        st.info(f"目前資料範圍：**{days[0]} ～ {days[-1]}**（共 {len(days)} 個交易日）")
    else:
        st.warning("資料庫目前是空的（管線尚未產出任何一天的資料）。")
except Exception as e:  # noqa: BLE001
    st.error(f"讀取資料索引失敗：{e}")
