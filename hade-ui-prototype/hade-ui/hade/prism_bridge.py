"""Read-only bridge to the original PRISM analysis package.

Hade never copies or edits the analysis code. Instead it:

* puts the analysis directory on ``sys.path`` so ``src.database`` and
  ``src.variables`` can be imported directly, and
* loads *function definitions* from the original Streamlit page scripts
  (``analysis/src/pages/*.py``) without executing their module-level UI code,
  so the original algorithms are reused verbatim.
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path
from typing import Iterable

HADE_ROOT = Path(__file__).resolve().parent.parent
ASSETS = HADE_ROOT / "hade" / "assets"
ENV_VAR = "PRISM_ANALYSIS_DIR"


class AnalysisNotFound(RuntimeError):
    pass


def analysis_dir() -> Path:
    """Directory that contains the original ``src`` package."""
    candidates = []
    if os.environ.get(ENV_VAR):
        candidates.append(Path(os.environ[ENV_VAR]).expanduser())
    candidates.append(HADE_ROOT.parents[1] / "analysis")
    for c in candidates:
        if (c / "src" / "database.py").is_file():
            return c.resolve()
    raise AnalysisNotFound(
        "Could not find the PRISM analysis directory. Expected "
        f"'{candidates[-1]}' or set {ENV_VAR} to the folder containing 'src/database.py'."
    )


def sql_dir() -> Path:
    return analysis_dir() / "src" / "sql"


def pages_dir() -> Path:
    return analysis_dir() / "src" / "pages"


def ensure_importable() -> None:
    # Never write bytecode caches into the (read-only by policy) analysis folder.
    sys.dont_write_bytecode = True
    path = str(analysis_dir())
    if path not in sys.path:
        sys.path.insert(0, path)
    # The Monaco wrapper reads this variable at import time (original behaviour).
    monaco = ASSETS / "monaco_sql_editor"
    if monaco.is_dir():
        os.environ.setdefault("SQL_EDITOR", str(monaco))
    _matplotlib_compat()


def _matplotlib_compat() -> None:
    """``matplotlib.cm.get_cmap`` was removed in matplotlib 3.9 but the original
    degradation page still calls it. Restore it with the equivalent lookup."""
    import matplotlib
    import matplotlib.cm as cm

    if not hasattr(cm, "get_cmap"):
        def get_cmap(name=None, lut=None):
            cmap = matplotlib.colormaps[name or matplotlib.rcParams["image.cmap"]]
            return cmap.resampled(lut) if lut else cmap

        cm.get_cmap = get_cmap  # type: ignore[attr-defined]


def numpy_float_norm():
    """numpy proxy for the original pages: ``np.linalg.norm`` first converts a
    DataFrame to float. Newer pandas versions keep object dtype when concatenating
    the original's empty seed frame, which makes ``np.linalg.norm`` raise
    ``TypeError ... no callable sqrt method``. The computed values are unchanged."""
    import numpy as np
    import pandas as pd

    class _Linalg:
        def __getattr__(self, name):
            return getattr(np.linalg, name)

        @staticmethod
        def norm(x, *args, **kwargs):
            if isinstance(x, pd.DataFrame):
                x = x.apply(pd.to_numeric, errors="raise").astype(float)
            return np.linalg.norm(x, *args, **kwargs)

    class _Np:
        linalg = _Linalg()

        def __getattr__(self, name):
            return getattr(np, name)

    return _Np()


_CACHE: dict[tuple, dict] = {}


def load_page_symbols(
    page: str,
    names: Iterable[str],
    *,
    constants: Iterable[str] = (),
    skip_imports: Iterable[str] = (),
    globals_override: dict | None = None,
) -> dict:
    """Return selected functions/constants from an original page script.

    Only imports, function/class definitions and explicitly listed constants
    are executed; page-level Streamlit calls (``st.set_page_config``,
    ``main()`` ...) are skipped. The source file is never modified.
    """
    names, constants, skip = tuple(names), tuple(constants), tuple(skip_imports)
    key = (page, names, constants, skip)
    if key in _CACHE:
        return _CACHE[key]

    ensure_importable()
    path = pages_dir() / page
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    body: list[ast.stmt] = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module in skip:
            continue
        if isinstance(node, ast.Import) and any(a.name in skip for a in node.names):
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.ClassDef)):
            body.append(node)
        elif isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in constants for t in node.targets
        ):
            body.append(node)

    namespace: dict = {"__name__": f"prism_page_{path.stem}", "__file__": str(path)}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(path), "exec"), namespace)
    namespace.update(globals_override or {})  # e.g. dtype-safe numpy proxy (see numpy_float_norm)
    missing = [n for n in (*names, *constants) if n not in namespace]
    if missing:
        raise AnalysisNotFound(f"{page} no longer defines: {', '.join(missing)}")
    result = {n: namespace[n] for n in (*names, *constants)}
    _CACHE[key] = result
    return result
