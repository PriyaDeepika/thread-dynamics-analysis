import streamlit as st

from hade import glue, td_component, ui
from hade.state import load_value, store_value

ui.page_header(
    "Thread dynamics",
    "Explore how the threads of a service interact with locks, sockets, disks and files. Click a node in the "
    "graph to see its details.",
    "Analysis",
)
ui.require_db()
ss = st.session_state

if ss.get("ripple_graph") is None:
    ui.callout("Thread dynamics works on services from the Ripple graph. Build a graph first.")
    st.page_link("views/ripple.py", label="Go to Ripple", icon=":material/hub:")
    st.stop()

if not td_component.available():
    ui.show_error("The thread-dynamics frontend bundle is missing. Run `scripts/build_frontends.sh` (needs Node.js).")
    st.stop()

graph = ss.ripple_graph
services = [node for node, attr in graph.nodes().data() if attr["machine"] != -1]
ss.setdefault("service_index", None)
load_value("service_index")
service = st.selectbox("Service", services, index=None, key="_service_index", on_change=store_value,
                       args=["service_index"], placeholder="Select a service from the Ripple graph")
if service is None or service not in graph.nodes():
    ui.callout("Select one of the services found by Ripple.")
    st.stop()

if ss.get("hade_td_last_service") != service:
    ss.pop("thread_dynamics_state", None)  # avoid showing details of the previous service
    ss["hade_td_last_service"] = service

node = graph.nodes()[service]
machine_id, pid = node["machine"], node["pid"]
try:
    with st.spinner("Loading thread dynamics…"):
        graph_data = glue.build_dynamics_graph(machine_id, pid)
except Exception as exc:
    ui.show_error("The thread-dynamics graph could not be loaded.", exc)
    st.stop()

if not graph_data["edges"]:
    st.info("No thread dynamics were recorded for this service in the selected time ranges.", icon=":material/search_off:")
    st.stop()

k1, k2, k3 = st.columns(3)
with k1: ui.kpi_card("Service", node["service"], f"machine {machine_id} · pid {pid}")
with k2: ui.kpi_card("Graph elements", len(graph_data["nodes"]), "threads and resources")
with k3: ui.kpi_card("Relationships", len(graph_data["edges"]), "edges")
st.write("")

import networkx as nx
import plotly.graph_objects as go

renderer = st.radio(
    "Graph renderer",
    ["SVG", "Original interactive graph (WebGL)"],
    horizontal=True,
)

if renderer == "SVG":
    layout_graph = nx.Graph()
    layout_graph.add_nodes_from(graph_data["nodes"])
    layout_graph.add_edges_from(
        (edge["source"], edge["target"]) for edge in graph_data["edges"]
    )
    positions = nx.spring_layout(layout_graph, seed=42)

    edge_x, edge_y = [], []
    for source, target in layout_graph.edges:
        x0, y0 = positions[source]
        x1, y1 = positions[target]
        edge_x.extend([x0, x1, None])
        edge_y.extend([y0, y1, None])

    nodes = list(layout_graph.nodes)
    fig = go.Figure([
        go.Scatter(
            x=edge_x, y=edge_y, mode="lines",
            line=dict(color="#9ca3af", width=1),
            hoverinfo="skip", showlegend=False,
        ),
        go.Scatter(
            x=[positions[node][0] for node in nodes],
            y=[positions[node][1] for node in nodes],
            text=nodes, mode="markers+text", textposition="top center",
            marker=dict(
                size=14,
                color=["#3b82f6" if node.startswith("thread-") else "#f59e0b"
                       for node in nodes],
            ),
            showlegend=False,
        ),
    ])
    fig.update_layout(
        height=520,
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
    )
    st.plotly_chart(fig, width="stretch")
    st.dataframe(graph_data["edges"], hide_index=True, width="stretch")
else:
    td_component.thread_dynamics(
        graph_data, glue.on_request_cb(machine_id, pid),
        key="thread_dynamics_state",
    )


state = ss.get("thread_dynamics_state") or {}
if state.get("request") and state.get("response") is None:
    st.warning("No data could be loaded for the selected element. The original query returned an error or "
               "nothing; details are in the terminal running Hade.", icon=":material/warning:")
with st.expander("How to use this view"):
    st.markdown(
        "Nodes are threads and the resources they touch (futex *contention* / *schedule*, disk, sockets, VFS files, "
        "external hosts). Click one to load its time series. Compare and baseline ranges from the KPI page are applied."
    )
