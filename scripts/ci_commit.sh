#!/usr/bin/env bash
# Commit the bot output and push it with rebase retries.
#   bash scripts/ci_commit.sh "chore(bot): message"
# Safe to call from any workflow: exits 0 when there is nothing to push.
set -uo pipefail

MSG="${1:-chore(bot): refresh}"
BRANCH="${GITHUB_REF_NAME:-main}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

git config user.name "restream-bot"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

for path in watch_list.json status.json README.md docs .state .github/workflows-manifest.json; do
  [ -e "$path" ] && git add -A "$path"
done

if git diff --cached --quiet; then
  echo "nothing to commit - bot output is already up to date"
  exit 0
fi

git commit -m "$MSG [skip ci]" || exit 0
echo "committed: $(git log -1 --pretty=%s)"

for attempt in 1 2 3 4 5; do
  if git pull --rebase --autostash origin "$BRANCH" >/dev/null 2>&1 && git push origin "HEAD:$BRANCH"; then
    echo "pushed to $BRANCH"
    exit 0
  fi
  echo "push attempt $attempt failed - retrying in $((attempt * 4))s"
  sleep $((attempt * 4))
done

echo "::warning::could not push after 5 attempts - the next scheduled run will retry"
exit 0
