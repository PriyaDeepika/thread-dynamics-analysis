# Hade — a friendlier interface for PRISM

Hade is a separate Streamlit UI over the **existing PRISM analysis code** in `../../analysis`.
It reuses the original database client, SQL files, graph/score algorithms and thread-dynamics
frontend; it does not reimplement or modify any of them.

## Run (no Docker)

Requires Python 3.12+. From this folder:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Open http://localhost:8501, then **Overview → Connect a database** (upload a `.db3`, or pick one
from the PRISM `data/` folder, e.g. `oboutique-k8s.db3`).

Run from this folder so `.streamlit/config.toml` (theme, upload limit) is picked up.
If `analysis/` lives elsewhere, set `PRISM_ANALYSIS_DIR=/path/containing/src`.

## Pages

| Page | Reuses from the original |
|---|---|
| Overview | `DatabaseClient`, `service_list.sql`, range totals from `src/variables.py` |
| KPI & time ranges | `add_entry` (range merging) from `pages/kpi.py`; same `tree` session state |
| Ripple | `pid_connections.sql`, `missing_discovery.sql`, `unconnected_pids.sql`, graph builders and `draw_network` from `pages/ripple.py` |
| Thread dynamics | `dynamics_edges.sql`, all `request_handler_*` functions, original React frontend |
| Degradation | `compute_scores`, `draw_network`, `SHARES` and `distributions/*.sql` from `pages/degradation.py` |
| SQL explorer | `DatabaseClient.custom_query`, Monaco editor, Jinja template variables |

Suggested order: KPI & time ranges → Ripple → Thread dynamics / Degradation.
(Degradation needs a Ripple graph and a compare range.)

## How the original is preserved

* Hade copies the selected database to a private temp file; **your original `.db3` is never opened for writing.**
  (The original `DatabaseClient` runs `CREATE … IF NOT EXISTS` DDL on connect — that happens on the copy.)
* `hade/prism_bridge.py` loads functions from the original page scripts with `ast`, skipping their
  page-level UI code. The analysis folder is only read (bytecode writing is disabled for it).
* The SQL explorer only runs a single read-only statement (`SELECT`/`WITH`/`DESCRIBE`/`EXPLAIN`).
* Query results are cached by *database token + rendered SQL*, so they cannot go stale when the database
  or time ranges change. The original `compute_scores` cache only hashes range **start** times; Hade clears
  it whenever the full (database, graph, ranges) signature changes.

### Compatibility notes (original code untouched)
* `matplotlib.cm.get_cmap` (used by `degradation.py`) was removed in matplotlib 3.9; Hade restores it at runtime.
* `degradation.py` is not listed in the original `main.py` navigation; Hade includes it.
* The original Debug page's *Template Variables* editor calls `.trim()` on strings (not a Python method) when
  adding a row; Hade uses a plain `st.data_editor` there instead.
* Original thread-dynamics request handlers print query errors and return `None`; Hade shows a warning when that happens.

## Verify against the original

```bash
python tests/verify_parity.py                       # uses data/oboutique-k8s.db3
python tests/verify_parity.py path/to/other.db3
```

Runs the **original** `ripple.py` and `degradation.py` pages (Streamlit AppTest) next to Hade on the same
database and ranges, and checks the Ripple graph, degradation scores and thread-dynamics graph data are
identical, the source database is byte-identical, and the analysis folder is unchanged.

## Rebuilding the bundled frontends

`hade/assets/` contains builds of the original `thread_dynamics` and `monaco_sql_editor` frontends.
To rebuild from the original source (needs Node.js; builds in a temp dir): `scripts/build_frontends.sh`.

## Limits
* Thread dynamics needs a browser to render; its data path is covered by the parity test, the React view is the original build.
* Overview's table row counts and the data time span are the only Hade-authored SQL (read-only metadata).
