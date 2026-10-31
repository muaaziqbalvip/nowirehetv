#!/usr/bin/env python3
"""Pick which channels the rotation matrix streams in this slot.

The catalog is cut into windows of `per_slot` channels. The window index is
derived from the current UTC 6-hour slot so every scheduled run picks the next
slice and the whole catalog cycles over `ceil(total / per_slot)` slots.

Outputs `channels` (JSON array of {id, name}) and `slot` to GITHUB_OUTPUT.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import load_watch_list, now_iso  # noqa: E402


def emit(name: str, value: str) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(f"{name}={value}\n")
    print(f"{name}={value}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--per-slot", type=int, default=2)
    parser.add_argument("--slots", type=int, default=6)
    parser.add_argument("--slot-hours", type=float, default=6.0)
    parser.add_argument("--only-online", action="store_true", default=True)
    parser.add_argument("--slot", type=int, default=None, help="force a slot index")
    args = parser.parse_args()

    watch = load_watch_list()
    channels = [c for c in (watch.get("channels") or []) if c.get("online")] or (watch.get("channels") or [])
    if not channels:
        emit("channels", "[]")
        emit("slot", "0")
        print("::warning::no channels available for rotation")
        return 0

    per_slot = max(1, args.per_slot)
    window_count = max(1, math.ceil(len(channels) / per_slot))
    if args.slot is not None:
        slot = args.slot
    else:
        slot = int(time.time() // (args.slot_hours * 3600))
    index = slot % window_count
    start = index * per_slot
    picked = channels[start:start + per_slot] or channels[:per_slot]

    payload = [{"id": c["id"], "name": c["name"]} for c in picked]
    emit("channels", json.dumps(payload, separators=(",", ":")))
    emit("slot", str(index))
    print(f"slot {index}/{window_count - 1} at {now_iso()}: "
          f"{', '.join(c['name'] for c in picked)} ({len(channels)} channels in catalog)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
