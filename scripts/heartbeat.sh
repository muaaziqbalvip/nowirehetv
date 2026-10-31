#!/usr/bin/env bash
# Write a per-channel heartbeat file (committed by the workflow) so the portal
# and the README can show which jobs are actually alive.
#   bash scripts/heartbeat.sh <channel-id> [status]
set -uo pipefail

CHANNEL_ID="${1:?usage: heartbeat.sh <channel-id> [status]}"
STATUS="${2:-ok}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
mkdir -p .state

python3 - "$CHANNEL_ID" "$STATUS" <<'PY'
import json, os, sys
from datetime import datetime, timezone

cid, status = sys.argv[1], sys.argv[2]
state_path = f".state/ch-{cid}.json"
heartbeat_path = f".state/heartbeat-{cid}.json"

state = {}
if os.path.exists(state_path):
    try:
        state = json.load(open(state_path, encoding="utf-8"))
    except Exception:
        state = {}

now = datetime.now(timezone.utc).replace(microsecond=0)
data = {
    "channel": cid,
    "status": status,
    "restarts": state.get("restarts", 0),
    "last_stream_state": state.get("status", "unknown"),
    "runner": os.environ.get("RUNNER_NAME", os.uname().nodename),
    "run_id": os.environ.get("GITHUB_RUN_ID", ""),
    "workflow": os.environ.get("GITHUB_WORKFLOW", ""),
    "repository": os.environ.get("GITHUB_REPOSITORY", ""),
    "updated_at": now.isoformat().replace("+00:00", "Z"),
    "epoch": int(now.timestamp()),
}
with open(heartbeat_path, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2)
    fh.write("\n")
print(f"heartbeat {cid}: {status} (restarts={data['restarts']})")
PY
