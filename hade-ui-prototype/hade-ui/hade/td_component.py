"""Streamlit v2 wrapper for the ORIGINAL thread-dynamics React frontend.

The bundle in ``hade/assets/thread_dynamics`` is built from
``analysis/src/components/thread_dynamics/frontend`` (see
``scripts/build_frontends.sh``). The data contract is identical to the
original ``thread_dynamics()`` wrapper; only the registration differs
(inline assets, so the analysis package need not be pip-installed).
"""
from __future__ import annotations

import streamlit as st

from hade.prism_bridge import ASSETS

_DIR = ASSETS / "thread_dynamics"
_component = None


def available() -> bool:
    return (_DIR / "index.js").is_file()


def _get():
    global _component
    if _component is None:
        # A leading comment + newline makes Streamlit treat the strings as inline
        # content rather than as file paths/globs.
        css = None
        if (_DIR / "index.css").is_file():
            css = "/* thread_dynamics */\n" + (_DIR / "index.css").read_text(encoding="utf-8")
        _component = st.components.v2.component(
            "hade.thread_dynamics",
            js="// thread_dynamics\n" + (_DIR / "index.js").read_text(encoding="utf-8"),
            css=css,
            html='<div class="react-root"></div>',
        )
    return _component


def thread_dynamics(graph_data, on_request_cb, key=None):
    response = st.session_state.get(key, {}).get("response")
    return _get()(
        key=key,
        default={},
        data={"graph_data": graph_data, "response": response},
        height=900,
        width="stretch",
        on_request_change=on_request_cb,
        on_response_change=lambda: None,
    )
