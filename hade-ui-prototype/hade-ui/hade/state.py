"""Keep widget values when the user switches pages (same pattern as PRISM)."""
import streamlit as st


def store_value(key: str) -> None:
    st.session_state[key] = st.session_state["_" + key]


def load_value(key: str) -> None:
    st.session_state["_" + key] = st.session_state.get(key)
