import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from hade import glue, prism_bridge, ui

prism_bridge.ensure_importable()
from src.components.monaco_sql_editor import monaco_sql_editor  # original wrapper

ui.page_header(
    "SQL explorer",
    "Run read-only SQL against the loaded database. Jinja variables such as compare_filter(\"ts\") from the "
    "KPI ranges are available, exactly as in PRISM's Debug page.",
    "Advanced",
)
ui.require_db()
ss = st.session_state
for k, v in {"query_result": None, "query": None, "debug_variables": pd.DataFrame({"name": [], "value": []})}.items():
    ss.setdefault(k, v)

DEFAULT = "SELECT ts, pid, tid, run_share FROM taskstats_view ORDER BY ts LIMIT 100;"

with st.expander("Tables and views"):
    from hade import session
    st.dataframe(session.inventory(), hide_index=True, width="stretch")

try:
    schema = ss.db.get_database_schema()
except Exception:
    schema = None
ss.query = monaco_sql_editor(value=ss.query or DEFAULT, schema=schema, height="170px", theme="vs-dark", key="sql_editor")

with st.expander("Template variables (optional)"):
    st.caption("Referenced in SQL as {{ name }}, e.g. name `pid_filter`, value `(pid = 123 AND machine_id = 1)`.")
    ss.debug_variables = st.data_editor(ss.debug_variables, num_rows="dynamic", width="stretch", key="debug_vars_editor")

if st.button("Run query", type="primary"):
    variables = {r["name"]: r["value"] for _, r in ss.debug_variables.iterrows()
                 if pd.notna(r["name"]) and pd.notna(r["value"]) and str(r["name"]).strip()}
    try:
        rendered = glue.render_user_sql(ss.query, variables)
        problem = glue.check_read_only(rendered)
        if problem:
            st.error(problem, icon=":material/block:")
        else:
            with st.spinner("Running…"):
                ss.query_result = ss.db.custom_query(rendered)
    except Exception as exc:
        ss.query_result = None
        ui.show_error("The query failed.", exc)

result = ss.query_result
if result is None:
    st.stop()

st.success(f"{len(result):,} row(s) · {len(result.columns)} column(s)", icon=":material/check_circle:")
st.dataframe(result, width="stretch", height=320)
st.download_button("Download CSV", result.to_csv(index=False).encode(), "query_result.csv", "text/csv")

cols = list(result.columns)
line_tab, hist_tab, bar_tab = st.tabs(["Line", "Histogram", "Bar"])


def label_traces(df, x, y):
    """One trace per distinct combination of the remaining columns (as in the original Debug page)."""
    others = [c for c in df.columns.difference([x, y])]
    if not others:
        yield "", df
        return
    for key, part in df.groupby(others, dropna=False, sort=False):
        yield "-".join(map(str, key if isinstance(key, tuple) else (key,))), part


with line_tab:
    a, b = st.columns(2)
    x = a.selectbox("X axis", cols, index=None, key="line_x")
    y = b.selectbox("Y axis", cols, index=None, key="line_y")
    if x and y:
        fig = go.Figure([go.Scatter(x=p[x], y=p[y], mode="lines", name=n) for n, p in label_traces(result, x, y)])
        fig.update_layout(template="plotly_white", height=420, margin=dict(l=10, r=10, t=10, b=10), showlegend=len(fig.data) <= 25)
        st.plotly_chart(fig, width="stretch")

with bar_tab:
    a, b = st.columns(2)
    x = a.selectbox("X axis", cols, index=None, key="bar_x")
    y = b.selectbox("Y axis", cols, index=None, key="bar_y")
    if x and y:
        fig = go.Figure([go.Bar(x=p[x], y=p[y], name=n, opacity=0.5) for n, p in label_traces(result, x, y)])
        fig.update_layout(template="plotly_white", barmode="overlay", height=420, margin=dict(l=10, r=10, t=10, b=10),
                          showlegend=len(fig.data) <= 25)
        st.plotly_chart(fig, width="stretch")

with hist_tab:
    a, b, c = st.columns(3)
    dist = a.selectbox("Distribution", cols, index=None, key="hist_dist")
    group = b.selectbox("Group by", cols, index=None, key="hist_group")
    bins = c.number_input("Bins", min_value=1, value=20, key="hist_bins")
    d, e, f = st.columns(3)
    lo = d.number_input("Range min", value=0.0, key="hist_lo")
    hi = e.number_input("Range max", value=1.0, key="hist_hi")
    norm = f.toggle("Normalise", key="hist_norm")
    if dist:
        series = [("", result[dist])] if not group else [(str(g), result.loc[result[group] == g, dist]) for g in result[group].unique()]
        fig = go.Figure()
        for name, s in series:
            counts, edges = np.histogram(pd.to_numeric(s, errors="coerce").dropna(), bins=int(bins), range=(lo, hi))
            heights = counts / counts.sum() if norm and counts.sum() else counts
            fig.add_bar(x=edges[:-1], y=heights, width=np.diff(edges), name=name, offset=0, opacity=0.5)
        fig.update_layout(template="plotly_white", barmode="overlay", height=420, margin=dict(l=10, r=10, t=10, b=10),
                          xaxis_title=dist, yaxis_title="Probability" if norm else "Count", showlegend=bool(group))
        st.plotly_chart(fig, width="stretch")
