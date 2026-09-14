from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import datetime as dt
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import streamlit as st

from app import close_price, config, data, lookup, ui_common
from app.backtest import DISCLAIMER as BACKTEST_DISCLAIMER
from app.backtest import backtest_branch_buys, merge_results
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

horizon = st.select_slider("勝率回測：買超後幾個交易日看報酬", options=[5, 10, 20], value=5)


def _one(row: tuple[str, int, int, int]) -> dict:
    stock_id, buy, sell, net = row
    name_rows = lookup.find_stocks(stock_id, db_path=config.REFERENCE_DB)
    name = next((n for sid, n, _ in name_rows if sid == stock_id), "")
    market = next((m for sid, n, m in name_rows if sid == stock_id), "twse")
    series = histories.get(stock_id, [])
    # request through the *latest available* trading day (not just the user's
    # chosen end date) so forward-return exit prices exist for recent signals
    request_dates = [d for d, _ in series] + [all_dates[-1]]
    closes = close_price.get_closes(stock_id, market, request_dates,
                                    db_path=config.CLOSE_DB)
    rows = [(d, n2, closes[d]) for d, n2 in series if d in closes]
    r = compute_fifo(rows)
    bt = backtest_branch_buys(series, closes, all_dates, horizon_days=horizon)
    return {
        "股號": stock_id, "股名": name,
        "買超張": buy // 1000, "賣超張": sell // 1000, "淨張": net // 1000,
        "FIFO平均成本": round(r.avg_cost, 2) if r.avg_cost else None,
        "估計庫存(張)": r.est_inventory_shares // 1000,
        "超賣": "是" if r.had_oversell else "",
        f"買超後{horizon}日勝率": (f"{bt.win_rate * 100:.0f}%"
                              if bt.win_rate is not None else None),
        f"買超後{horizon}日平均報酬": (f"{bt.avg_return * 100:+.1f}%"
                                if bt.avg_return is not None else None),
        "_bt": bt,
    }


with st.spinner(f"計算 {len(candidates)} 檔個股的 FIFO 成本與勝率回測中…"):
    with ThreadPoolExecutor(max_workers=8) as pool:
        out_rows = list(pool.map(_one, candidates))

merged_bt = merge_results([r["_bt"] for r in out_rows])
for r in out_rows:
    del r["_bt"]

st.subheader(f"🎯 {branch_id} 分點整體勝率（買超後 {horizon} 個交易日）")
b1, b2, b3 = st.columns(3)
b1.metric("買超訊號數", f"{merged_bt.n_signals:,}（可計算 {merged_bt.n_computable:,}）")
b2.metric("勝率", f"{merged_bt.win_rate * 100:.1f}%" if merged_bt.win_rate is not None else "—")
b3.metric("平均報酬", f"{merged_bt.avg_return * 100:+.2f}%"
         if merged_bt.avg_return is not None else "—")
st.caption(f"⚠️ {BACKTEST_DISCLAIMER}")

df = pd.DataFrame(out_rows)
st.caption(f"統計區間：{dates[0]} ～ {dates[-1]}（{len(dates)} 個交易日，{len(df)} 檔個股）")
st.dataframe(df.sort_values("淨張", key=abs, ascending=False).reset_index(drop=True),
            hide_index=True, use_container_width=True)
ui_common.download_csv_button(
    df.sort_values("淨張", key=abs, ascending=False).reset_index(drop=True),
    f"{branch_id}_分點總覽_{dates[0]}_{dates[-1]}.csv")

ui_common.render_disclaimer()
