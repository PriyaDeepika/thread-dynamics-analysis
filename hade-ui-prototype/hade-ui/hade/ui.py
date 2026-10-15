"""Shared look & feel (light content area, navy sidebar, teal accents)."""
from __future__ import annotations

import html

import streamlit as st

from hade import session

CSS = """
<style>
:root { --navy:#0B1F33; --slate:#5B6B7C; --line:#DDE5ED; --teal:#0F9D8E; --teal-soft:#E3F6F3; --card:#FFFFFF; }
html, body, [class*="css"] { font-family: Inter, "Segoe UI", system-ui, -apple-system, Roboto, sans-serif; }
.block-container { padding-top: 2.2rem; max-width: 1280px; }
h1, h2, h3 { letter-spacing: -0.02em; color: var(--navy); }
.hd-eyebrow { color: var(--teal); font-size: .72rem; font-weight: 700; letter-spacing: .14em; text-transform: uppercase; margin-bottom: .2rem; }
.hd-sub { color: var(--slate); font-size: 1rem; margin: -.4rem 0 1.2rem; max-width: 860px; }
.hd-card { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 16px 18px; box-shadow: 0 1px 2px rgba(11,31,51,.04); height: 100%; }
.hd-kpi-label { color: var(--slate); font-size: .78rem; font-weight: 600; text-transform: uppercase; letter-spacing: .06em; }
.hd-kpi-value { color: var(--navy); font-size: 1.7rem; font-weight: 700; line-height: 1.25; }
.hd-kpi-hint { color: var(--slate); font-size: .8rem; }
.hd-callout { border-left: 3px solid var(--teal); background: var(--teal-soft); border-radius: 6px; padding: 10px 14px; color: #124B45; font-size: .9rem; margin: .4rem 0 1rem; }
.hd-step { display:flex; gap:12px; align-items:flex-start; padding:10px 0; border-bottom:1px solid var(--line); }
.hd-step:last-child { border-bottom: none; }
.hd-dot { width:22px; height:22px; border-radius:50%; flex:none; display:grid; place-items:center; font-size:.75rem; font-weight:700; background:#E9EFF5; color:var(--slate); }
.hd-dot.done { background: var(--teal); color:#fff; }
.hd-step b { color: var(--navy); } .hd-step span { color: var(--slate); font-size:.85rem; }
.hd-brand { display:flex; align-items:center; gap:10px; padding:4px 0 14px; }
.hd-logo { width:34px; height:34px; border-radius:10px; background:linear-gradient(145deg,#2DD4BF,#3B82F6); color:#06202B; font-weight:900; display:grid; place-items:center; }
.hd-brand-name { color:#fff; font-weight:700; font-size:1.15rem; line-height:1.1; }
.hd-brand-sub { color:#8FA6BC; font-size:.68rem; letter-spacing:.12em; text-transform:uppercase; }
.hd-status { border:1px solid #1F4163; border-radius:10px; padding:10px 12px; background:#0F2A44; font-size:.82rem; color:#DCE6F0; }
.hd-status .t { font-weight:600; word-break:break-all; }
.hd-pill { display:inline-block; font-size:.68rem; font-weight:700; padding:2px 8px; border-radius:99px; }
.hd-pill.ok { background:#12453F; color:#7FF0DC; } .hd-pill.off { background:#3A2B1B; color:#F5C98A; }
div[data-testid="stDataFrame"] { border:1px solid var(--line); border-radius:10px; }
</style>
"""


def inject_theme() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def page_header(title: str, subtitle: str = "", eyebrow: str = "") -> None:
    if eyebrow:
        st.markdown(f'<div class="hd-eyebrow">{html.escape(eyebrow)}</div>', unsafe_allow_html=True)
    st.title(title)
    if subtitle:
        st.markdown(f'<div class="hd-sub">{html.escape(subtitle)}</div>', unsafe_allow_html=True)


def kpi_card(label: str, value, hint: str = "") -> None:
    st.markdown(
        f'<div class="hd-card"><div class="hd-kpi-label">{html.escape(label)}</div>'
        f'<div class="hd-kpi-value">{html.escape(str(value))}</div>'
        f'<div class="hd-kpi-hint">{html.escape(hint)}</div></div>',
        unsafe_allow_html=True,
    )


def callout(text: str) -> None:
    st.markdown(f'<div class="hd-callout">{html.escape(text)}</div>', unsafe_allow_html=True)


def sidebar_status() -> None:
    with st.sidebar:
        st.markdown(
            '<div class="hd-brand"><div class="hd-logo">H</div><div>'
            '<div class="hd-brand-name">Hade</div><div class="hd-brand-sub">PRISM analysis</div></div></div>',
            unsafe_allow_html=True,
        )
    # Status card is added after navigation so it appears below the page list.


def sidebar_database_card() -> None:
    with st.sidebar:
        st.divider()
        if session.is_connected():
            size = st.session_state.db_size or 0
            st.markdown(
                f'<div class="hd-status"><span class="hd-pill ok">CONNECTED</span>'
                f'<div class="t">{html.escape(st.session_state.db_name)}</div>'
                f'<div>{size / 1e6:,.1f} MB · working copy</div></div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<div class="hd-status"><span class="hd-pill off">NO DATABASE</span>'
                '<div>Load a PRISM .db3 on Overview.</div></div>',
                unsafe_allow_html=True,
            )


def require_db() -> None:
    """Friendly empty state for pages that need a database."""
    if session.is_connected():
        return
    st.markdown(
        '<div class="hd-card"><div class="hd-kpi-label">Nothing to analyse yet</div>'
        '<div class="hd-kpi-value" style="font-size:1.2rem">Connect a PRISM database to get started</div>'
        '<div class="hd-kpi-hint">Upload a .db3 file produced by the metric collector.</div></div>',
        unsafe_allow_html=True,
    )
    st.write("")
    st.page_link("views/overview.py", label="Go to Overview", icon=":material/home:")
    st.stop()


def show_error(message: str, exc: Exception | None = None) -> None:
    st.error(message, icon=":material/error:")
    if exc is not None:
        with st.expander("Technical details"):
            st.exception(exc)
