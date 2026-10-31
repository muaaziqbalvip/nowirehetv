#!/usr/bin/env bash
# =============================================================================
# 24/7 pull -> publish supervisor for ONE channel.
#
#   bash scripts/restart_loop.sh <channel-id> [initial-token]
#
# * re-reads the panel token every RESTREAM.token_refresh_minutes (tokens rotate)
# * restarts ffmpeg with exponential backoff (5s -> 120s) on failure
# * stops itself before GitHub's 6-hour job cap (config: loop_timeout_seconds)
#   so the next scheduled run can continue the stream
#
# Destination is chosen by config.json["dest_mode"]:
#   rtmp        push FLV to rtmp.url (nginx-rtmp / MediaMTX / any ingest)
#   hls-rehost  write a rolling HLS window under out/<id>/
#   proxy-only  supervised pull test (no video leaves the runner)
# =============================================================================
set -uo pipefail

CHANNEL_ID="${1:?usage: restart_loop.sh <channel-id> [initial-token]}"
TOKEN="${2:-}"
INITIAL_TOKEN="$TOKEN"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

log() { printf '[%s] [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$CHANNEL_ID" "$*" >&2; }

cfg() {
  python3 - "$1" <<'PY'
import json, sys
try:
    node = json.load(open("config.json", encoding="utf-8"))
except Exception:
    print("")
    raise SystemExit(0)
for part in sys.argv[1].split("."):
    try:
        node = node[int(part)] if part.isdigit() else node[part]
    except (KeyError, IndexError, TypeError):
        print("")
        raise SystemExit(0)
if isinstance(node, bool):
    print("true" if node else "false")
else:
    print(node)
PY
}

BASE="$(cfg source_base)"
LIST_PATH="$(cfg list_path)"
PLAY_TEMPLATE="$(cfg play_url_template)"
AUTH_MODE="$(cfg auth_mode)"
UA="$(cfg auth.user_agent)"
[ -z "$UA" ] && UA="VLC/3.0.20"
DEST_MODE="$(cfg dest_mode)"
RTMP_URL="$(cfg rtmp.url)"
RTMP_KEY_ENV="$(cfg rtmp.key_secret)"
RTMP_KEY="${!RTMP_KEY_ENV:-}"
LOOP_TIMEOUT="$(cfg restream.loop_timeout_seconds)"; LOOP_TIMEOUT="${LOOP_TIMEOUT:-21000}"
BACKOFF_START="$(cfg restream.restart_backoff_seconds)"; BACKOFF_START="${BACKOFF_START:-5}"
BACKOFF_MAX="$(cfg restream.max_backoff_seconds)"; BACKOFF_MAX="${BACKOFF_MAX:-120}"
TOKEN_MINUTES="$(cfg restream.token_refresh_minutes)"; TOKEN_MINUTES="${TOKEN_MINUTES:-30}"
STAGGER="${STAGGER_SECONDS:-0}"

mkdir -p .state "out/$CHANNEL_ID"
STATE_FILE=".state/ch-$CHANNEL_ID.json"
OUT_DIR="$ROOT/out/$CHANNEL_ID"

token_ts=0
[ -n "$TOKEN" ] && token_ts=$(date +%s)

refresh_token() {
  local body tok
  body=$(curl -fsSL --max-time 25 -A "$UA" "$BASE$LIST_PATH" 2>/dev/null) || return 1
  tok=$(printf '%s' "$body" | python3 -c 'import json,sys
try:
    print(json.load(sys.stdin).get("token","") or "")
except Exception:
    print("")' 2>/dev/null)
  if [ -z "$tok" ]; then
    log "token refresh failed - keeping the previous token"
    return 1
  fi
  TOKEN="$tok"
  token_ts=$(date +%s)
  log "token refreshed (length ${#TOKEN})"
  return 0
}

build_url() {
  local url="$PLAY_TEMPLATE"
  url="${url//<ID>/$CHANNEL_ID}"
  url="${url//<TOKEN>/$TOKEN}"
  case "$url" in
    http://*|https://*) printf '%s' "$url" ;;
    *) printf '%s/%s' "${BASE%/}" "${url#/}" ;;
  esac
}

state_write() {
  local status="$1" restarts="$2" note="$3"
  python3 - "$STATE_FILE" "$CHANNEL_ID" "$status" "$restarts" "$note" <<'PY'
import json, os, sys
from datetime import datetime, timezone
path, cid, status, restarts, note = sys.argv[1:6]
data = {}
if os.path.exists(path):
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception:
        data = {}
data.update({
    "channel": cid,
    "status": status,
    "restarts": int(restarts),
    "note": note,
    "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    "run_id": os.environ.get("GITHUB_RUN_ID", ""),
    "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
    "host": os.environ.get("RUNNER_NAME", os.uname().nodename),
})
json.dump(data, open(path, "w", encoding="utf-8"), indent=2)
PY
}

run_pull() {
  local url="$1"
  local common=(-hide_banner -loglevel warning -nostdin -user_agent "$UA"
                -rw_timeout 15000000 -reconnect 1 -reconnect_streamed 1
                -reconnect_delay_max 10 -i "$url")
  case "$DEST_MODE" in
    rtmp)
      if [ -z "$RTMP_URL" ]; then
        log "dest_mode=rtmp but rtmp.url is empty - falling back to pull test"
        ffmpeg "${common[@]}" -t 300 -c copy -f null - >/dev/null 2>>"$OUT_DIR/ffmpeg.log"
        return $?
      fi
      local dest="${RTMP_URL//<ID>/$CHANNEL_ID}"
      dest="${dest//<KEY>/$RTMP_KEY}"
      ffmpeg "${common[@]}" -c copy -f flv -flvflags no_duration_filesize "$dest" \
        >/dev/null 2>>"$OUT_DIR/ffmpeg.log"
      ;;
    hls-rehost)
      ffmpeg "${common[@]}" -c copy -f hls -hls_time 4 -hls_list_size 8 \
        -hls_flags delete_segments+append_list \
        -hls_segment_filename "$OUT_DIR/seg_%05d.ts" "$OUT_DIR/index.m3u8" \
        >/dev/null 2>>"$OUT_DIR/ffmpeg.log"
      ;;
    *)
      ffmpeg "${common[@]}" -t 300 -c copy -f null - >/dev/null 2>>"$OUT_DIR/ffmpeg.log"
      ;;
  esac
}

started=$(date +%s)
deadline=$((started + LOOP_TIMEOUT))
backoff=$BACKOFF_START
restarts=0

if [ "$STAGGER" -gt 0 ] 2>/dev/null; then
  stagger=$((STAGGER % 300))
  log "staggering start by ${stagger}s"
  sleep "$stagger"
fi

log "start channel=$CHANNEL_ID dest_mode=$DEST_MODE budget=${LOOP_TIMEOUT}s"

while :; do
  now=$(date +%s)
  if [ "$now" -ge "$deadline" ]; then
    log "budget exhausted - exiting cleanly so the next run continues"
    state_write "handover" "$restarts" "loop budget reached"
    break
  fi

  if [ -z "$TOKEN" ] || [ $((now - token_ts)) -ge $((TOKEN_MINUTES * 60)) ]; then
    refresh_token || true
  fi
  if [ -z "$TOKEN" ]; then
    log "no token available - sleeping ${backoff}s"
    sleep "$backoff"
    backoff=$(( backoff * 2 > BACKOFF_MAX ? BACKOFF_MAX : backoff * 2 ))
    continue
  fi

  url="$(build_url)"
  log "pulling $BASE/play/$CHANNEL_ID/... (dest=$DEST_MODE)"
  state_write "pulling" "$restarts" ""

  run_start=$(date +%s)
  run_pull "$url"
  rc=$?
  run_end=$(date +%s)
  ran=$((run_end - run_start))

  if [ "$rc" -ne 0 ] || [ "$ran" -lt 20 ]; then
    restarts=$((restarts + 1))
    log "ffmpeg exited rc=$rc after ${ran}s - retrying in ${backoff}s (restart #$restarts)"
    state_write "retrying" "$restarts" "rc=$rc ran=${ran}s"
    if [ "$rc" -ne 0 ] && [ "$ran" -lt 20 ]; then
      backoff=$(( backoff * 2 > BACKOFF_MAX ? BACKOFF_MAX : backoff * 2 ))
    fi
    sleep "$backoff"
    # a fresh token may be the fix after a 403
    refresh_token || true
  else
    backoff=$BACKOFF_START
    log "stream ran ${ran}s (rc=0) - reconnecting for the next window"
    sleep 3
  fi
done

log "loop finished after $(( $(date +%s) - started ))s, $restarts restart(s) (initial token: ${INITIAL_TOKEN:+provided})"
exit 0
