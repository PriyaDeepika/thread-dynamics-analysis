"""Hade — a friendlier interface for the PRISM performance analysis system.

Run:  streamlit run app.py     (from this folder)
"""
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))  # make `hade` importable

st.set_page_config(page_title="Hade | PRISM analysis", page_icon="◈", layout="wide")

from hade import prism_bridge, session, ui  # noqa: E402

try:
    prism_bridge.ensure_importable()
except prism_bridge.AnalysisNotFound as exc:
    st.error(str(exc), icon=":material/folder_off:")
    st.stop()

session.init_state()
ui.inject_theme()
ui.sidebar_status()

pages = {
    "Workspace": [
        st.Page("views/overview.py", title="Overview", icon=":material/home:", default=True),
    ],
    "Analysis": [
        st.Page("views/kpi.py", title="KPI & time ranges", icon=":material/show_chart:", url_path="kpi"),
        st.Page("views/ripple.py", title="Ripple", icon=":material/hub:", url_path="ripple"),
        st.Page("views/dynamics.py", title="Thread dynamics", icon=":material/account_tree:", url_path="dynamics"),
        st.Page("views/degradation.py", title="Degradation", icon=":material/trending_down:", url_path="degradation"),
    ],
    "Advanced": [
        st.Page("views/advanced.py", title="SQL explorer", icon=":material/terminal:", url_path="advanced"),
    ],
}
page = st.navigation(pages)
ui.sidebar_database_card()
page.run()
