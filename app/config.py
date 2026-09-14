"""App-wide config: secrets (Streamlit Cloud) with env-var fallback (local dev)."""
from __future__ import annotations

import os

CODE_REPO = "jjerry0519/broker-branch-tracker"
CACHE_DIR = ".cache/parquet"
REFERENCE_DB = "reference.db"
CLOSE_DB = ".cache/close_cache.db"


def get_token() -> str:
    try:
        import streamlit as st
        if "GH_TOKEN" in st.secrets:
            return st.secrets["GH_TOKEN"]
    except Exception:
        pass
    return os.environ.get("GH_TOKEN", "")
