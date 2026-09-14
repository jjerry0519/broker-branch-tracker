from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from app import close_price, config, data, lookup, ui_common
from app.fifo import compute_fifo

st.set_page_config(page_title="個股 × 分點成本 | 台股分點籌碼追蹤", page_icon="💰",
                   layout="wide")
st.title("💰 個股 × 分點成本（FIFO 估算）")

col1, col2 = st.columns(2)
with col1:
    stock_id = ui_common.pick_stock()
with col2:
    branch_id = ui_common.pick_branch()

if not stock_id or not branch_id:
    st.stop()

manifest = ui_common.manifest()
all_dates = data.trading_dates(manifest)
if not all_dates:
    st.warning("資料庫目前沒有任何交易日資料。")
    st.stop()

default_start = all_dates[-60] if len(all_dates) >= 60 else all_dates[0]
c1, c2 = st.columns(2)
with c1:
    start = st.date_input("起始日", value=dt.date.fromisoformat(default_start),
                          min_value=dt.date.fromisoformat(all_dates[0]),
                          max_value=dt.date.fromisoformat(all_dates[-1]))
with c2:
    end = st.date_input("結束日", value=dt.date.fromisoformat(all_dates[-1]),
                        min_value=dt.date.fromisoformat(all_dates[0]),
                        max_value=dt.date.fromisoformat(all_dates[-1]))

dates = data.trading_dates(manifest, start=start.isoformat(), end=end.isoformat())
if not dates:
    st.warning("選定區間內沒有交易日資料。")
    st.stop()

paths = ui_common.days_as_paths(dates)
series = data.stock_branch_history(paths, stock_id, branch_id)
if not series:
    st.info("此股票 × 分點組合在選定區間內沒有進出資料。")
    st.stop()

market = lookup.stock_market(stock_id, db_path=config.REFERENCE_DB) or "twse"
closes = close_price.get_closes(stock_id, market, [d for d, _ in series],
                                db_path=config.CLOSE_DB)
missing = [d for d, _ in series if d not in closes]
if missing:
    st.warning(f"{len(missing)} 個交易日查無收盤價（來源：Yahoo Finance），"
              "這些日期的買賣超不計入 FIFO 估算。")

rows = [(d, net, closes[d]) for d, net in series if d in closes]
result = compute_fifo(rows)

st.caption(f"統計區間：{dates[0]} ～ {dates[-1]}")

m1, m2, m3, m4 = st.columns(4)
m1.metric("FIFO 平均成本", f"{result.avg_cost:,.2f}" if result.avg_cost else "—")
m2.metric("估計庫存（張）", f"{result.est_inventory_shares // 1000:,}")
m3.metric("區間買超（張）", f"{result.period_buy_shares // 1000:,}")
m4.metric("區間賣超（張）", f"{result.period_sell_shares // 1000:,}")

if result.had_oversell:
    st.warning("⚠️ 區間內至少一天賣出量超過估計庫存（可能是本工具涵蓋期間前既有的部位），"
              "超出部分已捨去、不列入估算。")

if result.trace:
    tdf = pd.DataFrame([{
        "日期": t.date, "買賣超張": t.net_shares // 1000, "收盤價": t.close_price,
        "當日累計庫存(張)": t.inventory_shares // 1000,
        "當日累計FIFO成本": round(t.running_avg_cost, 2) if t.running_avg_cost else None,
        "超賣": "是" if t.oversold else "",
    } for t in result.trace])
    st.line_chart(tdf.set_index("日期")["當日累計庫存(張)"])
    st.dataframe(tdf, hide_index=True, use_container_width=True)

ui_common.render_disclaimer()
