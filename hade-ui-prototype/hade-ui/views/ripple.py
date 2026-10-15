import re

import pandas as pd
import streamlit as st

from hade import glue, ui
from hade.state import load_value, store_value

ui.page_header(
    "Ripple",
    "Application service dependency graph. If two processes interact through TCP sockets, unix sockets or pipes, "
    "their dependency appears here.",
    "Analysis",
)
ui.require_db()
ss = st.session_state


def bootstrap_selection_cb():
    match = re.search(r"\d+", ss.ripple_selection)
    if match is not None:
        ss.bootstrap = ss.service_list.iloc[int(match.group()), :]


if "service_list" not in ss:
    try:
        with st.spinner("Listing services…"):
            ss.service_list = glue.load_service_list()
    except Exception as exc:
        ui.show_error("Could not list services in this database.", exc)
        st.stop()
    ss.setdefault("bootstrap", None)
    ss.setdefault("ripple_graph", None)
ss.setdefault("ripple_max_nodes", 20)
ss.setdefault("ripple_use_compare", False)

service_list = ss.service_list
if service_list.empty:
    st.info("No services were found in this database.", icon=":material/search_off:")
    st.stop()

options = (service_list.index.astype(str) + " - " + service_list["service_name"] + " ("
           + service_list["machine_id"].astype(str) + " : " + service_list["pid"].astype(str) + ")")
index = None
if ss.bootstrap is not None:
    match = service_list.index[(service_list["machine_id"] == ss.bootstrap["machine_id"])
                               & (service_list["pid"] == ss.bootstrap["pid"])]
    index = int(match[0]) if len(match) else None

compare = glue.ranges_frame("compare")
with st.container(border=True):
    left, middle, right = st.columns([0.6, 0.15, 0.25], vertical_alignment="bottom")
    left.selectbox("Start from service", options, placeholder="[index] - [service_name] ([machine_id] : [pid])",
                   index=index, key="ripple_selection", on_change=bootstrap_selection_cb)
    load_value("ripple_max_nodes")
    middle.number_input("Max nodes", min_value=1, step=1, key="_ripple_max_nodes",
                        on_change=store_value, args=["ripple_max_nodes"])
    load_value("ripple_use_compare")
    right.toggle("Use compare ranges", disabled=compare.empty, key="_ripple_use_compare",
                 on_change=store_value, args=["ripple_use_compare"],
                 help="Define *compare* ranges on the KPI page to restrict connections to those periods.")

if ss.bootstrap is None:
    ui.callout("Pick a starting service to build its dependency graph. The services are the processes found in the database.")
    st.stop()

boot = ss.bootstrap
try:
    with st.spinner("Building dependency graph…"):
        graph = glue.build_ripple_graph(boot["machine_id"], boot["pid"], int(ss.ripple_max_nodes), bool(ss.ripple_use_compare))
except Exception as exc:
    ui.show_error("The Ripple graph could not be built.", exc)
    st.stop()
ss.ripple_graph = graph

if len(graph) == 0:
    st.info("This service has no recorded connections in the selected period.", icon=":material/link_off:")
    st.stop()

machines = {a["machine"] for _, a in graph.nodes.data()}
k1, k2, k3, k4 = st.columns(4)
with k1: ui.kpi_card("Services", len(graph), f"limit {ss.ripple_max_nodes}")
with k2: ui.kpi_card("Connections", graph.number_of_edges(), "edges in graph")
with k3: ui.kpi_card("Machines", len({m for m in machines if m != -1}), "excluding unknown")
with k4: ui.kpi_card("Time filter", "compare ranges" if ss.ripple_use_compare and not compare.empty else "all data", "for connections")
if len(graph) >= int(ss.ripple_max_nodes):
    st.caption("The node limit was reached; raise **Max nodes** to include more services.")

st.write("")
fig_col, info_col = st.columns([1.5, 1], gap="large")
with fig_col:
    glue.draw_ripple(graph, boot["pid"])
with info_col:
    st.markdown("**How to read the graph**")
    st.markdown(
        "- Each **node** is a service, labelled `service (machine:pid)`.\n"
        "- The **green** node is the service you started from.\n"
        "- **Shaded wedges** group services by machine; `?` (dashed) marks destinations with no known machine.\n"
        "- A **line** means the two processes interact through sockets or pipes."
    )
    edges = pd.DataFrame(
        [{"from": a, "to": b, "connections": d.get("connections")} for a, b, d in graph.edges(data=True)]
    )
    st.markdown("**Connections**")
    if edges.empty:
        st.caption("No edges between the services shown.")
    else:
        st.dataframe(edges.sort_values("connections", ascending=False), hide_index=True, width="stretch", height=300)

st.write("")
ui.callout("Next: open Thread dynamics to inspect a service in this graph, or Degradation to score every service.")
n1, n2 = st.columns(2)
n1.page_link("views/dynamics.py", label="Thread dynamics", icon=":material/account_tree:")
n2.page_link("views/degradation.py", label="Degradation", icon=":material/trending_down:")
