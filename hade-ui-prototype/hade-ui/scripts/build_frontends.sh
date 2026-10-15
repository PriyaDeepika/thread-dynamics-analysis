#!/usr/bin/env bash
# Rebuild the two original PRISM frontends (React/Vite + CRA) from the ORIGINAL
# source into Hade's assets folder. The analysis directory is only read; builds
# happen in a temporary copy.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
ANALYSIS="${PRISM_ANALYSIS_DIR:-$HERE/../../analysis}"
COMP="$ANALYSIS/src/components"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

cp -r "$COMP/thread_dynamics/frontend" "$TMP/td"
(cd "$TMP/td" && npm ci --no-audit --no-fund && NODE_ENV=production npx vite build)
mkdir -p "$HERE/hade/assets/thread_dynamics"
cp "$TMP"/td/build/index-*.js  "$HERE/hade/assets/thread_dynamics/index.js"
cp "$TMP"/td/build/index-*.css "$HERE/hade/assets/thread_dynamics/index.css"

cp -r "$COMP/monaco_sql_editor/frontend" "$TMP/mon"
(cd "$TMP/mon" && npm ci --no-audit --no-fund && npm run build)
rm -rf "$HERE/hade/assets/monaco_sql_editor"; mkdir -p "$HERE/hade/assets/monaco_sql_editor"
cp -r "$TMP/mon/build/." "$HERE/hade/assets/monaco_sql_editor/"
echo "Frontends rebuilt."
