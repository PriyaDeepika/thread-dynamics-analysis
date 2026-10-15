"""Compare Hade against the ORIGINAL PRISM pages on the same database.

    python tests/verify_parity.py [path/to/database.db3]

Checks (read-only on your files; works on temporary copies):
  1. the source database file is byte-identical afterwards
  2. the analysis folder was not modified (no new/changed files)
  3. Ripple graph from Hade == graph from analysis/src/pages/ripple.py
  4. Degradation scores from Hade == analysis/src/pages/degradation.py
  5. Thread-dynamics graph data from Hade == the query/graph code in dynamics.py
Uses Streamlit's AppTest to execute the original pages unmodified.
"""
import hashlib
import logging
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import pandas as pd

logging.disable(logging.CRITICAL)
HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from hade import prism_bridge  # noqa: E402

prism_bridge.ensure_importable()
from BTrees.OOBTree import OOBTree  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

from src.database import DatabaseClient  # noqa: E402

ANALYSIS = prism_bridge.analysis_dir()
PAGES = ANALYSIS / "src" / "pages"


def tree_digest(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts and "node_modules" not in p.parts:
            h.update(str(p.relative_to(root)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def file_digest(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def make_tree(ranges):
    t = OOBTree()
    for s, e, k in ranges:
        t[pd.Timestamp(s)] = (pd.Timestamp(e), k)
    return t


def setstate(at, **kwargs):
    for k, v in kwargs.items():
        at.session_state[k] = v


def graph_key(g):
    nodes = sorted(((n, tuple(sorted(a.items()))) for n, a in g.nodes(data=True)), key=str)
    edges = sorted(((tuple(sorted(map(str, (a, b)))), tuple(sorted(d.items()))) for a, b, d in g.edges(data=True)), key=str)
    return nodes, edges


def main(src: Path) -> int:
    ok = True
    before_db, before_analysis = file_digest(src), tree_digest(ANALYSIS)
    tmp = Path(tempfile.mkdtemp(prefix="hade-parity-"))
    copies = []
    for i in range(3):
        c = tmp / f"c{i}.db3"
        shutil.copyfile(src, c)
        copies.append(c)

    probe = DatabaseClient(str(copies[2]))
    first, last = probe.custom_query("SELECT MIN(ts) a, MAX(ts) b FROM taskstats").iloc[0]
    span = last - first
    ranges = [(first + span * 0.1, first + span * 0.4, "baseline"), (first + span * 0.55, first + span * 0.9, "compare")]
    services = probe.custom_query((prism_bridge.sql_dir() / "service_list.sql").read_text())

    # --- original ripple page: find a start service that yields a real graph
    orig = AppTest.from_file(str(PAGES / "ripple.py"), default_timeout=600)
    orig.session_state["db"] = DatabaseClient(str(copies[0]))
    orig.session_state["tree"] = make_tree(ranges)
    orig.run()
    options = orig.selectbox[0].options
    chosen = None
    for i in range(min(len(options), 40)):
        orig.selectbox[0].select(options[i]).run()
        g = orig.session_state["ripple_graph"]
        if g is not None and len(g) >= 3:
            chosen, orig_graph = i, g
            break
    if chosen is None:
        print("No service with >=3 connected services found; cannot compare.")
        return 2
    print(f"Start service: {options[chosen]}  ->  original graph has {len(orig_graph)} nodes")

    # --- Hade
    hade = AppTest.from_file(str(HERE / "app.py"), default_timeout=600)
    hade.run()
    setstate(hade, db=DatabaseClient(str(copies[1])), db_name="parity", db_token=uuid.uuid4().hex, db_size=1,
             tree=make_tree(ranges))
    hade.switch_page("views/ripple.py").run()
    hade.selectbox[0].select(options[chosen]).run()
    hade_graph = hade.session_state["ripple_graph"]
    same = graph_key(hade_graph) == graph_key(orig_graph)
    print("3. Ripple graph identical:", same); ok &= same

    # --- degradation
    o2 = AppTest.from_file(str(PAGES / "degradation.py"), default_timeout=1200)
    setstate(o2, db=DatabaseClient(str(copies[0])), ripple_graph=orig_graph, tree=make_tree(ranges))
    o2.run()
    hade.switch_page("views/degradation.py").run()
    a, b = o2.dataframe[0].value.reset_index(drop=True), hade.dataframe[0].value.reset_index(drop=True)
    a["service"] = a["service"].astype(str).str.replace("\n", " ", regex=False)
    num = [c for c in a.columns if c != "service"]
    same = a["service"].tolist() == b["service"].tolist() and all(
        (pd.to_numeric(a[c]) - pd.to_numeric(b[c])).abs().max() < 1e-12 for c in num)
    print("4. Degradation scores identical:", same, f"({len(a)} services)"); ok &= same

    # --- dynamics graph data
    node = next((n, d) for n, d in orig_graph.nodes(data=True) if d["machine"] != -1)[1]
    pid_filter = f"((machine_id = {node['machine']}) AND (pid = {node['pid']}))"
    script = f'''
import sys; sys.path.insert(0, {str(HERE)!r})
import streamlit as st
from hade import glue
from jinja2 import Template
from pathlib import Path
from src.variables import template_variables
gd = glue.build_dynamics_graph({node["machine"]}, {node["pid"]})
q = Template(Path({str(prism_bridge.sql_dir() / "dynamics_edges.sql")!r}).read_text()).render(
    {{**template_variables(), "pid_filter": {pid_filter!r}, "pid": {node["pid"]}, "machine_id": {node["machine"]}}})
r = st.session_state.db.custom_query(q)
nodes = sorted(set(r["source"].tolist() + r["target"].tolist()))
edges = sorted(r.to_dict(orient="records"), key=lambda x: (x["source"], x["target"], x["edge_type"]))
st.session_state.same = gd == {{"nodes": nodes, "edges": edges}}
st.session_state.n = len(nodes)
'''
    t = AppTest.from_string(script, default_timeout=600)
    setstate(t, db=DatabaseClient(str(copies[2])), tree=make_tree(ranges))
    t.run()
    same = bool(t.session_state["same"]) if not t.exception else False
    print("5. Thread-dynamics graph data identical:", same, f"({t.session_state['n'] if not t.exception else '?'} nodes)"); ok &= same

    # --- integrity
    unchanged_db = file_digest(src) == before_db
    unchanged_analysis = tree_digest(ANALYSIS) == before_analysis
    print("1. Source database unchanged:", unchanged_db); print("2. Analysis folder unchanged:", unchanged_analysis)
    ok &= unchanged_db and unchanged_analysis
    shutil.rmtree(tmp, ignore_errors=True)
    print("\nALL CHECKS PASSED" if ok else "\nSOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    default = ANALYSIS.parent / "data" / "oboutique-k8s.db3"
    sys.exit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else default))
