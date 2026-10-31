#!/usr/bin/env bash
# Start one or more per-channel restream workflows right now (Actions API).
#
#   bash scripts/kick.sh be7u               # one channel
#   bash scripts/kick.sh be7u b8d3 bkys     # a few channels
#   bash scripts/kick.sh --all              # every channel (170 jobs!)
#   bash scripts/kick.sh --online           # only the channels the panel has online
#
# Needs a token with `actions: write`: either `gh auth login` locally, or run
# the _kick.yml workflow from the Actions tab (it uses the built-in token).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

REF="${REF:-main}"
REPO="${GITHUB_REPOSITORY:-$(git config --get remote.origin.url 2>/dev/null | sed -E 's#.*github\.com[:/]([^/]+/[^/.]+)(\.git)?#\1#')}"

usage() { sed -n '2,9p' "$0"; exit 2; }
[ $# -ge 1 ] || usage

ids=()
case "$1" in
  --all)
    mapfile -t ids < <(python3 -c 'import json;print("\n".join(c["id"] for c in json.load(open("watch_list.json"))["channels"]))') ;;
  --online)
    mapfile -t ids < <(python3 -c 'import json;print("\n".join(c["id"] for c in json.load(open("watch_list.json"))["channels"] if c.get("online")))') ;;
  *)
    ids=("$@") ;;
esac

echo "repo=$REPO ref=$REF channels=${#ids[@]}"
if [ "${#ids[@]}" -gt 25 ]; then
  echo "!! you are about to start ${#ids[@]} jobs - GitHub runs ~20 concurrently, the rest queue."
fi

started=0
for id in "${ids[@]}"; do
  file=$(ls .github/workflows/ch-"$id"-*.yml 2>/dev/null | head -1 || true)
  if [ -z "$file" ]; then echo "skip: no workflow for '$id' (run scripts/generate_workflows.py)"; continue; fi
  if gh workflow run "$(basename "$file")" --ref "$REF" --repo "$REPO" >/dev/null 2>&1; then
    started=$((started + 1)); echo "started $(basename "$file")"
  else
    echo "failed to start $(basename "$file") - token needs actions:write (or use _kick.yml)"
  fi
done
echo "kicked $started/${#ids[@]}"
