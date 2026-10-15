import random
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st
from BTrees.OOBTree import OOBTree

from hade import glue, prism_bridge, ui
from hade.state import load_value, store_value

# Original range-merging logic from analysis/src/pages/kpi.py (unchanged).
_kpi = prism_bridge.load_page_symbols("kpi.py", ["add_entry"])
add_entry = _kpi["add_entry"]

ui.page_header(
    "KPI & time ranges",
    "Upload a target metric (CSV/XLSX), then select the windows where the application behaved normally "
    "(baseline) and where it degraded (compare). These ranges drive Ripple, Thread dynamics and Degradation.",
    "Analysis",
)


def new_editor_key() -> str:
    return f"edited_rows{random.randint(0, int(1e6))}"


ss = st.session_state
for k, v in {
    "target_data": None, "target_name": None, "selection": None, "compare_chart_axis": None,
    "target_x": None, "target_y": None,
}.items():
    ss.setdefault(k, v)
if "tree" not in ss:
    ss.tree = OOBTree()
ss.setdefault("edited_rows_key", new_editor_key())


def on_file_change():
    ss.compare_chart_axis = ss.target_x = ss.target_y = None
    ss.selection = None


# ------------------------------------------------------------ 1. target metric
st.subheader("1 · Target metric")
st.file_uploader("Upload target metric", type=["csv", "xlsx"], key="kpi_target_file", on_change=on_file_change,
                 label_visibility="collapsed")
uploaded = ss.get("kpi_target_file")
if uploaded:
    try:
        ss.target_data = pd.read_excel(uploaded) if Path(uploaded.name).suffix == ".xlsx" else pd.read_csv(uploaded)
        ss.target_name = uploaded.name
    except Exception as exc:
        ui.show_error(f"Could not read '{uploaded.name}' as a table.", exc)

if ss.target_data is None:
    ui.callout("Upload a CSV or Excel file with a time column and a metric column (for example an exported run-share series).")
else:
    cols = list(ss.target_data.columns)
    st.caption(f"{ss.target_name or 'Target metric'} — {len(ss.target_data):,} rows, {len(cols)} columns")
    a, b, _ = st.columns([1, 1, 1.4])
    load_value("target_x")
    a.selectbox("Time column", cols, index=None, key="_target_x", on_change=store_value, args=["target_x"])
    load_value("target_y")
    b.selectbox("Metric column", cols, index=None, key="_target_y", on_change=store_value, args=["target_y"])
    if ss.target_x and ss.target_y:
        try:
            ss.target_data[ss.target_x] = pd.to_datetime(ss.target_data[ss.target_x])
            ss.compare_chart_axis = {"x_axis": ss.target_x, "y_axis": ss.target_y}
        except Exception as exc:
            ss.compare_chart_axis = None
            ui.show_error(f"Column '{ss.target_x}' could not be read as timestamps.", exc)

# ------------------------------------------------------------ 2. choose ranges
st.subheader("2 · Select ranges")
if ss.target_data is None or not ss.compare_chart_axis:
    ui.callout("Choose a time column and a metric column to draw the chart.")
else:
    with st.container(border=True):
        b1, b2, info = st.columns([0.22, 0.22, 0.56], vertical_alignment="center")
        mark = None
        if b1.button("Mark as compare", type="primary", width="stretch"):
            mark = "compare"
        if b2.button("Mark as baseline", width="stretch"):
            mark = "baseline"
        sel = ss.selection
        info.caption(
            f"Selected window: {pd.to_datetime(sel['x0']):%Y-%m-%d %H:%M:%S} → {pd.to_datetime(sel['x1']):%Y-%m-%d %H:%M:%S}"
            if sel else "Drag horizontally on the chart (box select) to choose a window."
        )
        if mark:
            if sel is None:
                st.warning("Select a window on the chart first.")
            else:
                add_entry(ss.tree, (pd.to_datetime(sel["x0"]), pd.to_datetime(sel["x1"]), mark))
                st.toast(f"Added {mark} range")

        axis = ss.compare_chart_axis
        fig = px.line(ss.target_data, x=axis["x_axis"], y=axis["y_axis"], markers=True,
                      template="plotly_white", color_discrete_sequence=["#0F9D8E"])
        for start in ss.tree:
            end, rtype = ss.tree[start]
            fig.add_vrect(x0=start, x1=end, line_width=0, opacity=0.2,
                          fillcolor="green" if rtype == "baseline" else "red")
        fig.update_layout(dragmode="select", selectdirection="h", margin=dict(l=10, r=10, t=10, b=10),
                          height=380, xaxis_title=None)
        events = st.plotly_chart(fig, on_select="rerun", key="line_chart", width="stretch")
        if events and "selection" in events:
            box = events["selection"].get("box")
            if box:
                x0, x1 = box[0]["x"][0], box[0]["x"][1]
                ss.selection = {"x0": min(x0, x1), "x1": max(x0, x1)}
        st.caption("Green = baseline · Red = compare")

# --------------------------------------------------------------- 3. ranges
st.subheader("3 · Ranges in use")
compare_s = glue.calculate_total_range_time("compare")
baseline_s = glue.calculate_total_range_time("baseline")
k1, k2, k3 = st.columns(3)
with k1:
    ui.kpi_card("Compare ranges", len(glue.ranges_frame("compare")), f"{compare_s:,.1f} s total")
with k2:
    ui.kpi_card("Baseline ranges", len(glue.ranges_frame("baseline")), f"{baseline_s:,.1f} s total")
with k3:
    ui.kpi_card("Metric", ss.compare_chart_axis["y_axis"] if ss.compare_chart_axis else "—",
                ss.target_name or "no file loaded")

st.write("")
tree = ss.tree
range_entries = glue.ranges_frame()
st.data_editor(
    range_entries, key=ss.edited_rows_key, num_rows="dynamic", width="stretch",
    column_config={
        "start": st.column_config.DatetimeColumn("start", format="YYYY-MM-DD HH:mm:ss.SSS"),
        "end": st.column_config.DatetimeColumn("end", format="YYYY-MM-DD HH:mm:ss.SSS"),
        "range_type": st.column_config.SelectboxColumn("range_type", options=["baseline", "compare"]),
    },
)
edits = ss[ss.edited_rows_key]

# Same edit/delete/add handling as the original KPI page.
if edits["deleted_rows"]:
    for idx in edits["deleted_rows"]:
        del tree[range_entries.loc[idx]["start"]]
    ss.edited_rows_key = new_editor_key()
    st.rerun()

if edits["edited_rows"]:
    try:
        for rowidx, changes in edits["edited_rows"].items():
            row = range_entries.loc[rowidx, :].copy()
            del tree[row["start"]]
            for column, value in changes.items():
                row[column] = value
            add_entry(tree, (pd.to_datetime(row["start"]), pd.to_datetime(row["end"]), row["range_type"]))
    except Exception as exc:
        ui.show_error("That edit could not be applied.", exc)
    ss.edited_rows_key = new_editor_key()
    st.rerun()

if edits["added_rows"]:
    added = False
    for row in edits["added_rows"]:
        if all(c in row for c in ("start", "end", "range_type")):
            add_entry(tree, (pd.to_datetime(row["start"]), pd.to_datetime(row["end"]), row["range_type"]))
            added = True
    if added:
        ss.edited_rows_key = new_editor_key()
        st.rerun()

if len(tree) and st.button("Clear all ranges"):
    tree.clear()
    ss.selection = None
    ss.edited_rows_key = new_editor_key()
    st.rerun()

with st.expander("What are baseline and compare ranges?"):
    st.markdown(
        "If an application degraded at some moment, the **baseline** is a window where it performed well and "
        "**compare** is a window where performance was degraded. Overlapping ranges are merged by the original "
        "PRISM logic. Ripple can optionally restrict its connections to the compare ranges; Thread dynamics and "
        "Degradation use both."
    )
