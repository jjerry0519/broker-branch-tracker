from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import streamlit as st

from app import close_price, config, data, lookup, ui_common
from app.fifo import compute_fifo

st.set_page_config(page_title="分點總覽 | 台股分點籌碼追蹤", page_icon="🏢", layout="wide")
st.title("🏢 分點總覽")

branch_id = ui_common.pick_branch()
if not branch_id:
    st.stop()

manifest = ui_common.manifest()
all_dates = data.trading_dates(manifest)
if not all_dates:
    st.warning("資料庫目前沒有任何交易日資料。")
    st.stop()

default_start = all_dates[-20] if len(all_dates) >= 20 else all_dates[0]
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
TOP_CAP = 200
candidates = data.branch_overview_candidates(paths, branch_id, top=TOP_CAP)
if not candidates:
    st.info("此分點在選定區間內沒有任何個股進出資料。")
    st.stop()
if len(candidates) >= TOP_CAP:
    st.info(f"候選個股較多，僅計算 |淨買賣超張數| 前 {TOP_CAP} 檔的 FIFO 成本。")

# one query for every stock this branch touched (was: one query per candidate)
histories = data.branch_history_multi(paths, branch_id)


def _one(row: tuple[str, int, int, int]) -> dict:
    stock_id, buy, sell, net = row
    name_rows = lookup.find_stocks(stock_id, db_path=config.REFERENCE_DB)
    name = next((n for sid, n, _ in name_rows if sid == stock_id), "")
    market = next((m for sid, n, m in name_rows if sid == stock_id), "twse")
    series = histories.get(stock_id, [])
    closes = close_price.get_closes(stock_id, market, [d for d, _ in series],
                                    db_path=config.CLOSE_DB)
    rows = [(d, n2, closes[d]) for d, n2 in series if d in closes]
    r = compute_fifo(rows)
    return {
        "股號": stock_id, "股名": name,
        "買超張": buy // 1000, "賣超張": sell // 1000, "淨張": net // 1000,
        "FIFO平均成本": round(r.avg_cost, 2) if r.avg_cost else None,
        "估計庫存(張)": r.est_inventory_shares // 1000,
        "超賣": "是" if r.had_oversell else "",
    }


with st.spinner(f"計算 {len(candidates)} 檔個股的 FIFO 成本中…"):
    with ThreadPoolExecutor(max_workers=8) as pool:
        out_rows = list(pool.map(_one, candidates))

df = pd.DataFrame(out_rows)
st.caption(f"統計區間：{dates[0]} ～ {dates[-1]}（{len(dates)} 個交易日，{len(df)} 檔個股）")
st.dataframe(df.sort_values("淨張", key=abs, ascending=False).reset_index(drop=True),
            hide_index=True, use_container_width=True)

ui_common.render_disclaimer()
