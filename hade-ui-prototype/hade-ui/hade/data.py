"""Cached access to the original SQL. The cache key always contains the
loaded-database token and the full rendered SQL text, so results can never be
served for a different database or different time-range filters."""
from __future__ import annotations

import pandas as pd
import streamlit as st


@st.cache_data(show_spinner=False, max_entries=64)
def _cached(token: str, sql: str, _db) -> pd.DataFrame:
    return _db.custom_query(sql)


def query(sql: str) -> pd.DataFrame:
    return _cached(st.session_state.db_token, sql, st.session_state.db)
