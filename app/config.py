"""App-wide config: secrets (Streamlit Cloud) with env-var fallback (local dev)."""
from __future__ import annotations

import os

CODE_REPO = "jjerry0519/broker-branch-tracker"
CACHE_DIR = ".cache/parquet"
REFERENCE_DB = "reference.db"
CLOSE_DB = ".cache/close_cache.db"

# 大盤基準代理：0050（元大台灣50），上市＋上櫃回測都用它當超額報酬的比較基準。
BENCHMARK_STOCK_ID = "0050"
BENCHMARK_MARKET = "twse"


def get_token() -> str:
    try:
        import streamlit as st
        if "GH_TOKEN" in st.secrets:
            return st.secrets["GH_TOKEN"]
    except Exception:
        pass
    return os.environ.get("GH_TOKEN", "")
