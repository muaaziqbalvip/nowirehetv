#!/usr/bin/env bash
# Serve the generated portal locally, exactly like GitHub Pages does.
#   bash scripts/local_dev.sh [port]
set -euo pipefail

PORT="${1:-8080}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

[ -d docs ] || python3 scripts/build_site.py

echo "portal   -> http://localhost:$PORT/"
echo "playlist -> http://localhost:$PORT/playlist.m3u"
echo "(Ctrl-C to stop)"
exec python3 -m http.server "$PORT" --directory docs --bind 0.0.0.0
