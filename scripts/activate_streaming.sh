#!/usr/bin/env bash
# One-command switch for the restream modes.
#
#   bash scripts/activate_streaming.sh github-free rtmp     # rotating matrix -> your RTMP server
#   bash scripts/activate_streaming.sh selfhosted hls-rehost # every channel on your own runner
#   bash scripts/activate_streaming.sh portal-only proxy-only # back to zero minutes
#
# It edits config.json, regenerates the workflows, rebuilds the portal and then
# prints exactly what is left for you to do (secrets, runner, destination).
set -euo pipefail

MODE="${1:-github-free}"
DEST="${2:-proxy-only}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

case "$MODE" in
  selfhosted|github-free|portal-only) ;;
  *) echo "mode must be selfhosted | github-free | portal-only"; exit 2 ;;
esac
case "$DEST" in
  rtmp|hls-rehost|proxy-only) ;;
  *) echo "dest_mode must be rtmp | hls-rehost | proxy-only"; exit 2 ;;
esac

python3 - "$MODE" "$DEST" <<'PY'
import json, sys
mode, dest = sys.argv[1], sys.argv[2]
with open("config.json", encoding="utf-8") as fh:
    cfg = json.load(fh)
cfg["mode"] = mode
cfg["dest_mode"] = dest
with open("config.json", "w", encoding="utf-8") as fh:
    json.dump(cfg, fh, indent=2, ensure_ascii=False)
    fh.write("\n")
print(f"config.json -> mode={mode} dest_mode={dest}")
PY

python3 scripts/generate_workflows.py --clean
python3 scripts/build_site.py
python3 scripts/update_readme.py

echo
echo "----------------------------------------------------------------"
echo "mode=$MODE dest_mode=$DEST"
echo "----------------------------------------------------------------"
case "$MODE" in
  portal-only)
    echo "No streaming jobs are active. The 6-hourly token/status bots keep the"
    echo "portal, playlists and badges fresh for ~0 Actions minutes."
    ;;
  github-free)
    echo "Active: _matrix-rotation.yml (cron) + dispatch-only per-channel workflows."
    echo "Budget: ~2 channels per 6-hour slot inside 2,000 min/month."
    ;;
  selfhosted)
    echo "Active: one cron workflow per channel on [self-hosted, linux]."
    echo "Start a runner on your VM first:"
    echo "  ./config.sh --url https://github.com/<you>/<repo> --token <RUNNER_TOKEN>"
    echo "  ./run.sh"
    ;;
esac

if [ "$DEST" = "rtmp" ]; then
  echo "Set the destination under Settings -> Secrets -> Actions -> RTMP_KEY,"
  echo "then put the ingest URL in config.json -> rtmp.url (use <ID> and <KEY>)."
fi

echo "Commit the regenerated files:"
echo "  git add -A .github config.json docs README.md && git commit -m 'chore: activate streaming' && git push"
