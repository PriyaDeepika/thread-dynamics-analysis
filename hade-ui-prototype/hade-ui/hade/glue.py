"""Thin glue that mirrors what the original page scripts do *around* the
analysis functions (query rendering, parameter passing). All analysis logic
(graph building, SQL, share/score maths, request handlers) is imported from
the original PRISM code via ``prism_bridge``.
"""
from __future__ import annotations

import duckdb
import pandas as pd
import streamlit as st
from jinja2 import Template

from hade import data, prism_bridge

prism_bridge.ensure_importable()
from src.variables import calculate_total_range_time, template_variables  # noqa: E402,F401

RANGE_COLUMNS = ["start", "end", "range_type"]


# --------------------------------------------------------------------- ranges
def ranges_frame(range_type: str | None = None) -> pd.DataFrame:
    """Baseline/compare ranges stored in ``st.session_state.tree`` (as in KPI page)."""
    rows = []
    tree = st.session_state.get("tree")
    if tree is not None:
        for start in tree:
            end, rtype = tree[start]
            if range_type is None or rtype == range_type:
                rows.append([start, end, rtype])
    frame = pd.DataFrame(rows, columns=RANGE_COLUMNS)
    return frame.astype({"start": "datetime64[ns]", "end": "datetime64[ns]", "range_type": "object"})


# --------------------------------------------------------------------- ripple
def _ripple():
    return prism_bridge.load_page_symbols(
        "ripple.py",
        ["bootstrap_undirected_graph", "add_missing_discovery", "add_disconnected", "draw_network"],
    )


def load_service_list() -> pd.DataFrame:
    return data.query((prism_bridge.sql_dir() / "service_list.sql").read_text())


def build_ripple_graph(machine_id, pid, max_nodes: int, use_compare: bool):
    """Same sequence as ``ripple.py::main`` once a start service is chosen."""
    sym, sql = _ripple(), prism_bridge.sql_dir()
    compare = ranges_frame("compare")
    if not use_compare or compare.empty:
        compare_filter = "true"
    else:
        compare_filter = "(" + " OR ".join(
            f"(ts_s >= '{r.start}' AND ts_s <= '{r.end}')" for r in compare.itertuples()
        ) + ")"
    pid_connections = data.query(
        Template((sql / "pid_connections.sql").read_text()).render(vfs_ts_filter=compare_filter)
    )
    pid_connections = pid_connections.sort_values(list(pid_connections.columns))
    missing = data.query((sql / "missing_discovery.sql").read_text())
    missing = missing.sort_values(list(missing.columns))
    unconnected = data.query((sql / "unconnected_pids.sql").read_text())

    graph = sym["bootstrap_undirected_graph"](pid_connections, (machine_id, pid), max_nodes=max_nodes)
    sym["add_missing_discovery"](graph, missing, max_nodes=max_nodes)
    sym["add_disconnected"](graph, unconnected, pid)
    return graph


def draw_ripple(graph, bootstrap_pid) -> None:
    _ripple()["draw_network"](graph, bootstrap_pid)


# ------------------------------------------------------------------- dynamics
def _dynamics():
    return prism_bridge.load_page_symbols(
        "dynamics.py", ["request_dispatcher", "on_request_cb"], constants=["SQL_DIR"],
        skip_imports=["src.components.thread_dynamics"],
    )


def on_request_cb(machine_id, pid):
    return _dynamics()["on_request_cb"](machine_id, pid)


def build_dynamics_graph(machine_id, pid) -> dict:
    """Same query/graph-data construction as ``dynamics.py::main``."""
    pid_filter = f"((machine_id = {machine_id}) AND (pid = {pid}))"
    sql = Template((prism_bridge.sql_dir() / "dynamics_edges.sql").read_text()).render(
        {**template_variables(), "pid_filter": pid_filter, "pid": pid, "machine_id": machine_id}
    )
    result = st.session_state.db.custom_query(sql)
    nodes = sorted(set(result["source"].tolist() + result["target"].tolist()))
    edges = result.to_dict(orient="records")
    edges.sort(key=lambda x: (x["source"], x["target"], x["edge_type"]))
    return {"nodes": nodes, "edges": edges}


# ---------------------------------------------------------------- degradation
def _degradation():
    return prism_bridge.load_page_symbols(
        "degradation.py", ["compute_scores", "draw_network"], constants=["SHARES"],
        globals_override={"np": prism_bridge.numpy_float_norm()},
    )


def degradation_signature(graph) -> tuple:
    tree = st.session_state.tree
    ranges = tuple((str(s), str(tree[s][0]), tree[s][1]) for s in tree)
    return (st.session_state.db_token, tuple(sorted(graph.nodes)), ranges)


def compute_degradation(graph, status) -> pd.DataFrame | None:
    """Run the original (cached) ``compute_scores``.

    The original cache key only hashes range *start* times, so editing a range's
    end or type could return stale scores. We therefore clear that cache whenever
    the complete (database, graph, ranges) signature changes.
    """
    sym = _degradation()
    sig = degradation_signature(graph)
    if st.session_state.get("hade_degradation_sig") != sig:
        sym["compute_scores"].clear()
        st.session_state["hade_degradation_sig"] = sig
    return sym["compute_scores"](st.session_state.db, graph, st.session_state.tree, status)


def draw_degradation(graph, scores) -> None:
    _degradation()["draw_network"](graph, scores.loc[:, ["service", "score"]])


# ---------------------------------------------------------------------- debug
def check_read_only(sql: str) -> str | None:
    """Return an error message unless ``sql`` is a single SELECT/EXPLAIN."""
    try:
        statements = st.session_state.db.conn.extract_statements(sql)
    except Exception as exc:
        return f"This is not valid SQL: {exc}"
    if len(statements) != 1:
        return "Run one statement at a time."
    kind = statements[0].type
    if kind not in (duckdb.StatementType.SELECT, duckdb.StatementType.EXPLAIN):
        return f"Hade only runs read-only queries (SELECT / WITH / DESCRIBE / EXPLAIN); got {kind.name}."
    return None


def render_user_sql(sql: str, variables: dict) -> str:
    return Template(sql).render(**{**template_variables(), **variables})
