from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app import close_price, config, data, lookup, ui_common

st.set_page_config(page_title="多股比較 | 台股分點籌碼追蹤", page_icon="📊", layout="wide")
ui_common.inject_style()
ui_common.page_header("📊", "多股比較",
                      "同時比較多檔股票的全市場籌碼流向（所有分點加總），適合供應鏈／同業族群比對")

MAX_STOCKS = 6
with st.container(border=True):
    q = st.text_input("股票清單（股號或名稱，逗號分隔）",
                      placeholder="例如 2330, 2317, 3008, 2454")
if not q:
    st.stop()

queries = [x.strip() for x in q.split(",") if x.strip()][:MAX_STOCKS]
if len(q.split(",")) > MAX_STOCKS:
    st.info(f"最多同時比較 {MAX_STOCKS} 檔，僅取前 {MAX_STOCKS} 個。")

resolved: list[tuple[str, str, str]] = []       # (stock_id, name, market)
unmatched: list[str] = []
for query in queries:
    matches = lookup.find_stocks(query, db_path=config.REFERENCE_DB, limit=1)
    if matches:
        resolved.append(matches[0])
    else:
        unmatched.append(query)
if unmatched:
    st.warning(f"查無符合的股票：{'、'.join(unmatched)}")
if not resolved:
    st.stop()

n = st.select_slider("回看交易日數", options=[5, 10, 20, 60], value=20)

manifest = ui_common.manifest()
dates = data.trading_dates(manifest, n=n)
if not dates:
    st.warning("資料庫目前沒有任何交易日資料。")
    st.stop()

paths = ui_common.days_as_paths(dates)
st.caption(f"統計區間：{dates[0]} ～ {dates[-1]}（{len(dates)} 個交易日，全市場所有分點加總）")

fig = go.Figure()
summary_rows = []
for stock_id, name, market in resolved:
    series = data.stock_total_daily_series(paths, stock_id)
    if not series:
        summary_rows.append({"股號": stock_id, "股名": name, "市場": "上市" if market == "twse" else "上櫃",
                             "買超張": None, "賣超張": None, "淨張": None, "期間股價漲跌%": None})
        continue
    sdf = pd.DataFrame(series, columns=["date", "net_shares"])
    sdf["累計買賣超張"] = (sdf["net_shares"] // 1000).cumsum()
    fig.add_trace(go.Scatter(x=sdf["date"], y=sdf["累計買賣超張"],
                             name=f"{stock_id} {name}", mode="lines"))

    buy = sum(n2 for _, n2 in series if n2 > 0)
    sell = sum(-n2 for _, n2 in series if n2 < 0)
    closes = close_price.get_closes(stock_id, market, [dates[0], dates[-1]],
                                    db_path=config.CLOSE_DB)
    pct = None
    if dates[0] in closes and dates[-1] in closes and closes[dates[0]] > 0:
        pct = (closes[dates[-1]] / closes[dates[0]] - 1) * 100
    summary_rows.append({
        "股號": stock_id, "股名": name, "市場": "上市" if market == "twse" else "上櫃",
        "買超張": buy // 1000, "賣超張": sell // 1000, "淨張": (buy - sell) // 1000,
        "期間股價漲跌%": round(pct, 2) if pct is not None else None,
    })

fig.update_layout(height=460, margin=dict(t=20, b=20), yaxis_title="累計買賣超（張）",
                  legend=dict(orientation="h", yanchor="bottom", y=1.02))
st.subheader("累計買賣超走勢比較")
st.plotly_chart(fig, use_container_width=True)

df = pd.DataFrame(summary_rows)
st.subheader("區間彙總")
st.dataframe(df, hide_index=True, use_container_width=True)
ui_common.download_csv_button(df, f"多股比較_{dates[0]}_{dates[-1]}.csv")

st.caption("券商分點買賣超彙整自第三方聚合站台，非交易所官方逐筆資料；股價變動來源 Yahoo Finance。")
