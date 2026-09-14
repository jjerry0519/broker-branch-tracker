from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
import streamlit as st

from app import data, ui_common

st.set_page_config(page_title="個股籌碼 | 台股分點籌碼追蹤", page_icon="📈", layout="wide")
st.title("📈 個股籌碼")

stock_id = ui_common.pick_stock()
if not stock_id:
    st.stop()

# 今日即時榜：每日全市場前15大買超/賣超掃描，當天就有，不受下方「近N日」查詢
# 20天滾動更新週期影響（見「資料更新方式」說明）。只顯示當天最大的 30 個分點；
# 較小的量要等滾動排程才會進歷史資料庫（見下方近N日區塊）。
latest_rows = ui_common.latest_board_for_stock(stock_id)
if latest_rows:
    latest_date = latest_rows[0][5]
    st.subheader(f"🔴 今日即時榜（{latest_date}，前15大買超/賣超，當天更新）")
    ldf = pd.DataFrame(latest_rows,
                       columns=["分點名稱", "方向", "買進張", "賣出張", "買賣超張", "資料日"])
    ldf["方向"] = ldf["方向"].map({"buy": "買超", "sell": "賣超"})
    st.dataframe(ldf.drop(columns=["資料日"]), hide_index=True, use_container_width=True)
    st.caption("即時榜只有前 15 大買超 + 前 15 大賣超；較小量的分點要等下方「近 N 日」"
              "資料庫滾動更新才會出現（最多 20 天）。")
    st.divider()

n = st.select_slider("回看交易日數", options=[5, 10, 20, 60], value=20)

manifest = ui_common.manifest()
dates = data.trading_dates(manifest, n=n)
if not dates:
    st.warning("資料庫目前沒有任何交易日資料。")
    st.stop()

paths = ui_common.days_as_paths(dates)
rows = data.stock_branch_rankings(paths, stock_id)

if not rows:
    st.info(f"近 {n} 個交易日（{dates[0]} ～ {dates[-1]}）查無此股票的分點進出資料。")
    st.stop()

df = pd.DataFrame(rows, columns=["分點代號", "分點名稱", "買進張", "賣出張", "買賣超張"])
df["買進張"] //= 1000
df["賣出張"] //= 1000
df["買賣超張"] //= 1000
total_abs = df["買賣超張"].abs().sum()
df["佔區間總量比重"] = (df["買賣超張"].abs() / total_abs * 100).round(2).astype(str) + "%"

st.caption(f"統計區間：{dates[0]} ～ {dates[-1]}（{len(dates)} 個交易日，資料庫完整明細，"
          f"非僅前15大）")
st.caption(ui_common.rotation_note(stock_id))

c1, c2 = st.columns(2)
with c1:
    st.subheader("前 15 大買超分點（近N日排行）")
    st.dataframe(df.sort_values("買賣超張", ascending=False).head(15)
                .reset_index(drop=True), hide_index=True, use_container_width=True)
with c2:
    st.subheader("前 15 大賣超分點（近N日排行）")
    st.dataframe(df.sort_values("買賣超張").head(15)
                .reset_index(drop=True), hide_index=True, use_container_width=True)

st.divider()
st.subheader("選定分點的每日累計買賣超")
branch_options = {f"{r[0]} {r[1]}": r[0] for r in rows}
picked = st.selectbox("分點", list(branch_options))
if picked:
    branch_id = branch_options[picked]
    series = data.branch_daily_series(paths, stock_id, branch_id)
    if series:
        sdf = pd.DataFrame(series, columns=["date", "net_shares"])
        sdf["買賣超張"] = sdf["net_shares"] // 1000
        sdf["累計買賣超張"] = sdf["買賣超張"].cumsum()
        sdf = sdf.set_index("date")
        st.line_chart(sdf["累計買賣超張"])
        st.dataframe(sdf[["買賣超張", "累計買賣超張"]], use_container_width=True)

ui_common.render_disclaimer()
st.caption("券商分點買賣超彙整自第三方聚合站台，非交易所官方逐筆資料。")
