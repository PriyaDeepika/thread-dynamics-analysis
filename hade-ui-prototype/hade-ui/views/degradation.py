import pandas as pd
import streamlit as st

from hade import glue, ui
from hade.glossary import TERMS

ui.page_header(
    "Degradation",
    "For every service in the Ripple graph, how much did its thread time shift between the baseline and compare "
    "ranges? Scores come from the original PRISM computation.",
    "Analysis",
)
ui.require_db()
ss = st.session_state

missing = []
if ss.get("ripple_graph") is None:
    missing.append(("views/ripple.py", "Build a Ripple graph", ":material/hub:"))
if glue.ranges_frame("compare").empty:
    missing.append(("views/kpi.py", "Define at least one compare range (and a baseline)", ":material/show_chart:"))
if missing:
    ui.callout("Degradation needs a Ripple graph and a compare range first.")
    for path, label, icon in missing:
        st.page_link(path, label=label, icon=icon)
    st.stop()

graph = ss.ripple_graph
status = st.status("Computing shares…", expanded=False)
try:
    scores = glue.compute_degradation(graph, status)
except Exception as exc:
    status.update(label="Computation failed", state="error")
    ui.show_error("The degradation scores could not be computed.", exc)
    st.stop()
if scores is None or scores.empty:
    status.update(label="No scores produced", state="error")
    st.info("No scores were produced for the current graph and ranges.")
    st.stop()
status.update(label="Scores ready", state="complete", expanded=False)

top = scores.iloc[0]
k1, k2, k3 = st.columns(3)
with k1: ui.kpi_card("Services scored", len(scores), "from the Ripple graph")
with k2: ui.kpi_card("Highest score", f"{top['score']:.3f}", str(top["service"]).replace("\n", " "))
with k3: ui.kpi_card("Compare / baseline", f"{glue.calculate_total_range_time('compare'):,.0f} s / {glue.calculate_total_range_time('baseline'):,.0f} s", "total range time")
if not len(glue.ranges_frame("baseline")):
    st.caption("No baseline range is defined; define one on the KPI page for a meaningful comparison.")

st.write("")
table_col, graph_col = st.columns([1.15, 1], gap="large")
share_cols = [c for c in scores.columns if c not in ("service", "score")]
with table_col:
    st.markdown("**Scores by service**")
    shown = scores.copy()
    shown["service"] = shown["service"].astype(str).str.replace("\n", " ", regex=False)
    for c in ["score", *share_cols]:
        shown[c] = pd.to_numeric(shown[c], errors="coerce")
    st.dataframe(
        shown.style.background_gradient(subset=["score"], cmap="copper").format({c: "{:.3f}" for c in ["score", *share_cols]}),
        hide_index=True, width="stretch", height=420,
    )
    st.download_button("Download scores (CSV)", shown.to_csv(index=False).encode(), "degradation_scores.csv", "text/csv")
with graph_col:
    st.markdown("**Services coloured by score**")
    glue.draw_degradation(graph, scores)

with st.expander("What do the columns mean?"):
    st.markdown("\n".join(f"- **{k}** — {TERMS[k]}" for k in
                          ["Degradation score", "run", "runqueue", "uninterruptible", "blkio", "futex_schedule", "futex_contention"]))
