"""Database lifecycle and shared Streamlit session state for Hade.

The uploaded / selected file is always copied to a private working file; the
original file is never opened by Hade. The original ``DatabaseClient`` then
runs on that copy (it executes ``CREATE ... IF NOT EXISTS`` DDL on connect).
"""
from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from pathlib import Path

import pandas as pd
import streamlit as st

from hade import prism_bridge

prism_bridge.ensure_importable()
from src.database import DatabaseClient  # noqa: E402  (original PRISM code)

WORK_DIR = Path(tempfile.gettempdir()) / "hade-ui"
DUCKDB_MAGIC = b"DUCK"

# Session keys that depend on the loaded database (reset when it changes).
DB_DEPENDENT_KEYS = (
    "service_list", "bootstrap", "ripple_graph", "ripple_selection", "_ripple_selection",
    "ripple_init", "thread_dynamics_state", "service_index", "_service_index",
    "hade_td_last_service", "hade_degradation_sig",
)


class DatabaseLoadError(Exception):
    """Raised with a user-presentable message."""


def init_state() -> None:
    st.session_state.setdefault("db", None)
    st.session_state.setdefault("db_name", None)
    st.session_state.setdefault("db_token", None)
    st.session_state.setdefault("db_path", None)
    st.session_state.setdefault("db_size", None)


def is_connected() -> bool:
    return st.session_state.get("db") is not None


def _looks_like_duckdb(path: Path) -> bool:
    with open(path, "rb") as f:
        return f.read(16)[8:12] == DUCKDB_MAGIC


def disconnect() -> None:
    db = st.session_state.get("db")
    if db is not None:
        try:
            db.close()
        except Exception:  # closing a broken connection must not block a reload
            pass
    old = st.session_state.get("db_path")
    if old:
        Path(old).unlink(missing_ok=True)
    for key in DB_DEPENDENT_KEYS:
        st.session_state.pop(key, None)
    st.session_state.update(db=None, db_name=None, db_token=None, db_path=None, db_size=None)


def connect(source: Path | bytes, display_name: str) -> None:
    """Copy ``source`` to a working file and open it with the original client."""
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    work = WORK_DIR / f"{uuid.uuid4().hex}.db3"
    try:
        if isinstance(source, (bytes, bytearray, memoryview)):
            work.write_bytes(bytes(source))
        else:
            shutil.copyfile(source, work)
        if not _looks_like_duckdb(work):
            raise DatabaseLoadError(
                f"'{display_name}' is not a DuckDB database file. PRISM databases are "
                "the `.db3` files written by the metric collector."
            )
        try:
            client = DatabaseClient(str(work))
        except Exception as exc:
            raise DatabaseLoadError(f"Could not open '{display_name}': {exc}") from exc
    except Exception:
        work.unlink(missing_ok=True)
        raise

    disconnect()
    st.session_state.update(
        db=client, db_name=display_name, db_token=uuid.uuid4().hex,
        db_path=str(work), db_size=os.path.getsize(work),
    )


def sample_databases() -> list[Path]:
    """`.db3` files in the PRISM repository's ``data/`` folder, if present."""
    data = prism_bridge.analysis_dir().parent / "data"
    return sorted(data.rglob("*.db3")) if data.is_dir() else []


@st.cache_data(show_spinner=False)
def _inventory(_db, token: str) -> pd.DataFrame:
    tables = _db.get_all_tables()
    counts = []
    for schema, name, kind in tables[["table_schema", "table_name", "table_type"]].itertuples(index=False):
        try:
            n = int(_db.custom_query(f'SELECT COUNT(*) AS n FROM "{schema}"."{name}"')["n"][0])
        except Exception:
            n = None
        counts.append({"table": name, "type": "view" if kind == "VIEW" else "table", "rows": n})
    return pd.DataFrame(counts)


def inventory() -> pd.DataFrame:
    """Table/view names and row counts of the loaded database (metadata only)."""
    return _inventory(st.session_state.db, st.session_state.db_token)


@st.cache_data(show_spinner=False)
def _time_coverage(_db, token: str):
    df = _db.custom_query("SELECT MIN(ts) AS first_ts, MAX(ts) AS last_ts FROM taskstats")
    return df.iloc[0]["first_ts"], df.iloc[0]["last_ts"]


def time_coverage():
    try:
        first, last = _time_coverage(st.session_state.db, st.session_state.db_token)
    except Exception:
        return None
    return None if pd.isna(first) or pd.isna(last) else (first, last)
