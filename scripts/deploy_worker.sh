#!/usr/bin/env bash
# Deploy the Cloudflare Worker HTTPS relay and wire it into config.json.
#
#   bash scripts/deploy_worker.sh
#
# Requires: node/npx, a Cloudflare account, `npx wrangler login` once.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

command -v npx >/dev/null 2>&1 || { echo "!! node/npx is required (https://nodejs.org)"; exit 1; }
[ -f wrangler.toml ] || { echo "!! wrangler.toml not found"; exit 1; }

echo "==> 1/3  Cloudflare login (skip with Ctrl-C if you already ran wrangler login)"
npx --yes wrangler@3 whoami >/dev/null 2>&1 || npx --yes wrangler@3 login

echo "==> 2/3  KV namespace for the token (optional, press Enter to skip on error)"
npx --yes wrangler@3 kv namespace create TOKEN_KV || \
  echo "    (KV not created - the worker will fetch the token on every request instead)"

echo "==> 3/3  Deploy"
npx --yes wrangler@3 deploy

cat <<'TXT'

------------------------------------------------------------------
Copy the printed URL (https://<name>.<subdomain>.workers.dev) and:

  1. health check:  curl https://<name>.<sub>.workers.dev/health
  2. wire it in:    config.json -> "proxy": { "base_url": "https://..." }
  3. rebuild:       python3 scripts/build_site.py
  4. commit:        git add -A docs config.json && git commit -m "chore: enable https relay" && git push

Every player page, M3U and README badge now uses HTTPS.
You can also paste the URL into the box on any channel page (stored in
localStorage) to test it before committing.
------------------------------------------------------------------
TXT
