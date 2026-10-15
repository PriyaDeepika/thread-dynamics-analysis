import html
from pathlib import Path

import streamlit as st

from hade import glue, session, ui
from hade.glossary import glossary_markdown

ui.page_header(
    "Overview",
    "Load a PRISM database, then work through the analysis from time ranges to service dependencies "
    "to per-service degradation.",
    "PRISM · performance analysis",
)


# ------------------------------------------------------------------ loader
def load_from_upload():
    uploaded = st.file_uploader("Upload a PRISM database (.db3)", type=["db3", "duckdb", "db"], key="hade_upload")
    if st.button("Connect", type="primary", disabled=uploaded is None, key="hade_connect_upload"):
        _connect(uploaded.getbuffer(), uploaded.name)


def load_from_data_folder():
    samples = session.sample_databases()
    if not samples:
        st.caption("No `.db3` files were found in the PRISM `data/` folder.")
        return
    labels = {str(p): f"{p.parent.name}/{p.name}  ({p.stat().st_size / 1e6:,.1f} MB)" for p in samples}
    choice = st.selectbox("Databases in the PRISM data folder", list(labels), format_func=labels.get, key="hade_sample")
    if st.button("Connect", key="hade_connect_sample"):
        _connect(Path(choice), Path(choice).name)


def _connect(source, name):
    with st.spinner(f"Opening {name}…"):
        try:
            session.connect(source, name)
        except session.DatabaseLoadError as exc:
            ui.show_error(str(exc))
            return
        except Exception as exc:
            ui.show_error("Unexpected error while loading the database.", exc)
            return
    st.rerun()


if not session.is_connected():
    left, right = st.columns([1.3, 1], gap="large")
    with left:
        st.subheader("Connect a database")
        tab_up, tab_local = st.tabs(["Upload file", "From PRISM data folder"])
        with tab_up:
            load_from_upload()
        with tab_local:
            load_from_data_folder()
        ui.callout("Hade copies the file to a private working copy; your original database is never opened or modified.")
    with right:
        st.subheader("How it works")
        st.markdown(
            '<div class="hd-card">'
            + "".join(
                f'<div class="hd-step"><div class="hd-dot">{n}</div><div><b>{t}</b><br><span>{d}</span></div></div>'
                for n, t, d in [
                    (1, "Connect a database", "Upload the .db3 from the metric collector."),
                    (2, "Set time ranges", "Mark baseline and compare windows on a KPI chart."),
                    (3, "Build the Ripple graph", "Pick a service to see what it talks to."),
                    (4, "Drill down", "Thread dynamics and degradation scores per service."),
                ]
            )
            + "</div>",
            unsafe_allow_html=True,
        )
    st.stop()

# --------------------------------------------------------------- connected
db_name = st.session_state.db_name
inv = None
try:
    inv = session.inventory()
except Exception as exc:
    ui.show_error("Connected, but the table inventory could not be read.", exc)

taskstats_rows = None
if inv is not None and (inv["table"] == "taskstats").any():
    taskstats_rows = int(inv.loc[inv["table"] == "taskstats", "rows"].iloc[0] or 0)

if taskstats_rows == 0:
    st.warning(
        "This database has no `taskstats` rows, so there is nothing to analyse. "
        "Make sure the metric collector ran long enough before it was stopped.",
        icon=":material/inbox:",
    )

st.success(f"Connected to **{db_name}**", icon=":material/check_circle:")

try:
    services = glue.load_service_list()
except Exception as exc:
    services = None
    ui.show_error("Could not list services in this database.", exc)

compare_s = glue.calculate_total_range_time("compare")
baseline_s = glue.calculate_total_range_time("baseline")
coverage = session.time_coverage()

c1, c2, c3, c4 = st.columns(4)
with c1:
    ui.kpi_card("Thread samples", f"{taskstats_rows:,}" if taskstats_rows is not None else "—", "rows in taskstats")
with c2:
    ui.kpi_card("Services", len(services) if services is not None else "—",
                f"{services['machine_id'].nunique()} machine(s)" if services is not None else "")
with c3:
    ui.kpi_card("Compare range", f"{compare_s:,.0f} s", "total selected time")
with c4:
    ui.kpi_card("Baseline range", f"{baseline_s:,.0f} s", "total selected time")
if coverage:
    st.caption(f"Data covers {coverage[0]:%Y-%m-%d %H:%M:%S} → {coverage[1]:%Y-%m-%d %H:%M:%S} (taskstats).")

st.write("")
left, right = st.columns([1.2, 1], gap="large")
with left:
    st.subheader("Your analysis path")
    has_target = st.session_state.get("target_data") is not None
    n_ranges = len(glue.ranges_frame())
    has_graph = st.session_state.get("ripple_graph") is not None
    steps = [
        (True, "Database connected", db_name, None),
        (n_ranges > 0, "Baseline / compare ranges", f"{n_ranges} range(s) defined" if n_ranges else "Open KPI & time ranges", "views/kpi.py"),
        (has_graph, "Ripple graph", f"{len(st.session_state.ripple_graph)} services in graph" if has_graph else "Pick a starting service", "views/ripple.py"),
        (has_graph, "Thread dynamics", "Explore a service's threads and resources", "views/dynamics.py"),
        (has_graph and compare_s > 0, "Degradation scores", "Needs a Ripple graph and a compare range", "views/degradation.py"),
    ]
    for i, (done, title, detail, _) in enumerate(steps, 1):
        st.markdown(
            f'<div class="hd-step"><div class="hd-dot {"done" if done else ""}">{"✓" if done else i}</div>'
            f'<div><b>{html.escape(title)}</b><br><span>{html.escape(detail)}</span></div></div>',
            unsafe_allow_html=True,
        )
    st.write("")
    n1, n2, n3 = st.columns(3)
    n1.page_link("views/kpi.py", label="KPI & ranges", icon=":material/show_chart:")
    n2.page_link("views/ripple.py", label="Ripple", icon=":material/hub:")
    n3.page_link("views/degradation.py", label="Degradation", icon=":material/trending_down:")
with right:
    st.subheader("Database contents")
    if inv is not None:
        st.dataframe(inv, hide_index=True, width="stretch", height=300,
                     column_config={"rows": st.column_config.NumberColumn("rows", format="%d")})

st.write("")
c_a, c_b = st.columns([1, 1])
with c_a:
    with st.expander("Glossary"):
        st.markdown(glossary_markdown())
with c_b:
    with st.expander("Connect a different database"):
        tab_up, tab_local = st.tabs(["Upload file", "From PRISM data folder"])
        with tab_up:
            load_from_upload()
        with tab_local:
            load_from_data_folder()
        if st.button("Disconnect"):
            session.disconnect()
            st.rerun()
