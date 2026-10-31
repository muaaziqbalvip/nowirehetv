#!/usr/bin/env python3
"""Bootstrap watch_list.json from data/seed_channels.txt.

Only needed for a fresh fork / offline first run. Once the token-refresh
workflow has run, watch_list.json is owned by the panel.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    ROOT,
    WATCH_LIST,
    load_config,
    normalise_channels,
    now_iso,
    read_json,
    save_json,
    classify_group,
    slugify,
)

SEED = ROOT / "data" / "seed_channels.txt"


def parse_seed(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) < 2:
            continue
        rows.append({
            "id": parts[0],
            "name": parts[1],
            "online": (parts[2].lower() == "online") if len(parts) > 2 else False,
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", default=str(SEED))
    parser.add_argument("--out", default=str(WATCH_LIST))
    parser.add_argument("--force", action="store_true", help="overwrite an existing watch_list.json")
    args = parser.parse_args()

    out = Path(args.out)
    if out.exists() and not args.force:
        existing = read_json(out, {}) or {}
        if existing.get("channels"):
            print(f"{out} already has {len(existing['channels'])} channels - use --force to rebuild")
            return 0

    cfg = load_config()
    rows = parse_seed(Path(args.seed))
    payload = {"name": cfg["panel"]["name"], "version": cfg["panel"]["version"], "channels": rows}
    channels = normalise_channels(payload)

    online = sum(1 for c in channels if c["online"])
    data = {
        "name": cfg["panel"]["name"],
        "version": cfg["panel"]["version"],
        "source_base": cfg["source_base"],
        "list_path": cfg["list_path"],
        "fetched_at": now_iso(),
        "stale": False,
        "token_present": False,
        "channel_count": len(channels),
        "online_count": online,
        "offline_count": len(channels) - online,
        "channels": channels,
        "seed": "data/seed_channels.txt",
        "_note": "Bootstrapped from the seed catalog. The token-refresh bot replaces this with live panel data.",
    }
    save_json(out, data)
    print(f"wrote {out}: {len(channels)} channels ({online} online / {len(channels) - online} offline)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
