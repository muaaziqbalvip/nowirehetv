#!/usr/bin/env bash
# Start channel workflows immediately, without the Actions API.
#
#   bash scripts/kick.sh be7u              # one channel
#   bash scripts/kick.sh be7u b8d3 bkys    # a few channels
#   bash scripts/kick.sh --all             # every channel (170 jobs - be sure!)
#
# Each per-channel workflow listens on push to data/trigger/<id>, so touching
# that file and pushing is all it takes to launch the 24/7 restream job.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ $# -lt 1 ]; then
  echo "usage: bash scripts/kick.sh <channel-id ...> | --all"
  echo "example: bash scripts/kick.sh be7u"
  exit 2
fi

ids=()
if [ "$1" = "--all" ]; then
  mapfile -t ids < <(python3 -c 'import json;print("\n".join(c["id"] for c in json.load(open("watch_list.json"))["channels"]))')
else
  ids=("$@")
fi

mkdir -p data/trigger
started=0
for id in "${ids[@]}"; do
  if ! python3 -c "import json,sys;d=json.load(open('watch_list.json'));sys.exit(0 if any(c['id']==sys.argv[1] for c in d['channels']) else 1)" "$id"; then
    echo "skip: unknown channel id '$id'"
    continue
  fi
  date -u +"%Y-%m-%dT%H:%M:%SZ" > "data/trigger/$id"
  started=$((started + 1))
done

[ "$started" -gt 0 ] || { echo "nothing to kick"; exit 1; }

git add data/trigger
git commit -m "chore(kick): start $started channel workflow(s)" >/dev/null || true
git push
echo "kicked $started workflow(s) - watch them under Actions -> ch-*"
