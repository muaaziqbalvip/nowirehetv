#!/usr/bin/env python3
"""Refresh the panel token and the channel catalog.

Runs first in every streaming workflow and on a 6-hour cron. Writes:

  watch_list.json  public catalog (token stripped)
  token.json       {"token": ..., "expires": ...}  - 0600, gitignored
  GITHUB_ENV       TOKEN / TOKEN_EXPIRY (masked) for later workflow steps

Failure policy (section 9 of the spec): if /watch/list is unreachable keep the
last known catalog/token, mark it stale, warn loudly and exit 0 so long-running
streams are not killed. Use --strict to fail instead (used by the cron bot).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    ROOT,
    TOKEN_FILE,
    WATCH_LIST,
    fetch_list,
    load_config,
    load_watch_list,
    normalise_channels,
    now_iso,
    save_json,
    token_expiry,
    write_github_env,
)


def extract_token(payload: dict, cfg: dict) -> str:
    field = str((cfg.get("auth") or {}).get("token_field") or "token")
    node = payload
    for part in field.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            node = ""
            break
    return str(node or "").strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=None)
    parser.add_argument("--out", default=str(WATCH_LIST))
    parser.add_argument("--token-out", default=str(TOKEN_FILE))
    parser.add_argument("--strict", action="store_true", help="exit non-zero when the panel is unreachable")
    parser.add_argument("--retries", type=int, default=4)
    args = parser.parse_args()

    cfg = load_config(args.config)
    previous = load_watch_list()
    out = Path(args.out)

    try:
        payload = fetch_list(cfg, retries=args.retries)
    except RuntimeError as exc:
        print(f"::warning title=Panel unreachable::{exc}")
        if not previous.get("channels"):
            print("::error::No cached catalog available - nothing to fall back to.")
            return 1
        previous["stale"] = True
        previous["last_error"] = str(exc)
        previous["last_attempt"] = now_iso()
        previous["token_present"] = bool(previous.get("token_present")) or TOKEN_FILE.exists()
        save_json(out, previous)
        print(f"kept cached catalog with {len(previous['channels'])} channels (stale=true)")
        return 1 if args.strict else 0

    token = extract_token(payload, cfg)
    if not token and (cfg.get("auth_mode") or "").lower() != "none":
        print("::error::/watch/list responded but no token field was found - check auth.token_field")
        return 1

    channels = normalise_channels(payload, previous)
    online = sum(1 for c in channels if c["online"])
    data = {
        "name": payload.get("name") or cfg["panel"]["name"],
        "version": payload.get("version") or cfg["panel"]["version"],
        "source_base": cfg["source_base"],
        "list_path": cfg["list_path"],
        "fetched_at": now_iso(),
        "stale": False,
        "token_present": bool(token),
        "channel_count": len(channels),
        "online_count": online,
        "offline_count": len(channels) - online,
        "buffer": payload.get("buffer"),
        "channels": channels,
    }
    save_json(out, data)

    expiry = token_expiry(token)
    if token:
        save_json(args.token_out, {
            "token": token,
            "expires": expiry,
            "fetched_at": data["fetched_at"],
            "source": cfg["source_base"],
            "_note": "Secret material - gitignored. Never commit this file.",
        })
        try:
            Path(args.token_out).chmod(0o600)
        except OSError:
            pass
        write_github_env("TOKEN", token)
        write_github_env("TOKEN_EXPIRY", str(expiry or ""))

    print(f"OK {data['name']} v{data['version']}: {len(channels)} channels "
          f"({online} online / {len(channels) - online} offline), token "
          f"{'refreshed, expires ' + str(expiry) if token else 'not required'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
