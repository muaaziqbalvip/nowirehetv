#!/usr/bin/env python3
"""Probe every channel and write status.json.

For each channel we fetch the play playlist with the current token and record:

  state        ok | dead | offline
  http         HTTP status code (0 = network error)
  latency_ms   time to first byte of the playlist
  variants     [{bandwidth, resolution}] parsed from the HLS master playlist
  bitrate_kbps best variant bandwidth
  segments     segment count of the deepest playlist we inspected
  live         true when the media playlist is a live window (no ENDLIST)
  checked_at   ISO timestamp

Writes status.json (repo root) plus an optional --out copy for the site.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    STATUS_FILE,
    build_play_url,
    http_get_text,
    load_config,
    load_token,
    load_watch_list,
    now_iso,
    save_json,
    source_base,
)

VARIANT_RE = re.compile(r"#EXT-X-STREAM-INF:(?P<attrs>[^\n]+)\n(?P<uri>[^\n#]+)")
ATTR_RE = re.compile(r'(?P<key>[A-Z0-9-]+)=(?P<value>"[^"]*"|[^,]*)')


def parse_variants(body: str) -> list[dict]:
    variants: list[dict] = []
    for match in VARIANT_RE.finditer(body.replace("\r\n", "\n")):
        attrs = {m.group("key"): m.group("value").strip('"') for m in ATTR_RE.finditer(match.group("attrs"))}
        bandwidth = attrs.get("BANDWIDTH") or attrs.get("AVERAGE-BANDWIDTH") or "0"
        try:
            bandwidth_int = int(str(bandwidth).split("x")[0])
        except (TypeError, ValueError):
            bandwidth_int = 0
        variants.append({
            "bandwidth": bandwidth_int,
            "resolution": attrs.get("RESOLUTION", ""),
            "codecs": attrs.get("CODECS", ""),
            "uri": match.group("uri").strip(),
        })
    variants.sort(key=lambda v: v["bandwidth"])
    return variants


def probe_channel(channel: dict, cfg: dict, token: str, timeout: float, retries: int, deep: bool) -> dict:
    url = build_play_url(cfg, channel["id"], token)
    record = {
        "id": channel["id"],
        "name": channel["name"],
        "group": channel.get("group", ""),
        "online": bool(channel.get("online")),
        "state": "dead",
        "http": 0,
        "latency_ms": None,
        "bitrate_kbps": None,
        "variants": [],
        "segments": 0,
        "live": False,
        "error": "",
        "checked_at": now_iso(),
    }

    status, body, latency = 0, "", None
    for attempt in range(max(1, retries)):
        status, body, latency = http_get_text(url, cfg, timeout=timeout)
        record["http"] = status
        record["latency_ms"] = latency
        if status == 200 and "#EXTM3U" in body:
            break
        record["error"] = f"HTTP {status}" if status else str(body)[:150]
    else:
        if status == 200 and "#EXTM3U" not in body:
            record["state"] = "dead"
            record["error"] = "not an HLS playlist"
        return record

    record["state"] = "ok"
    record["error"] = ""
    variants = parse_variants(body)
    record["variants"] = [{"bandwidth": v["bandwidth"], "resolution": v["resolution"]} for v in variants]
    if variants:
        record["bitrate_kbps"] = round(max(v["bandwidth"] for v in variants) / 1000)

    if deep:
        target = body
        if variants:
            base_dir = url.rsplit("/", 1)[0]
            child = variants[-1]["uri"]
            child_url = urllib.parse.urljoin(base_dir + "/", child)
            if "token=" not in child_url:
                sep = "&" if "?" in child_url else "?"
                child_url = f"{child_url}{sep}token={urllib.parse.quote(token, safe='')}"
            child_status, child_body, _ = http_get_text(child_url, cfg, timeout=timeout)
            if child_status == 200:
                target = child_body
        segments = [line for line in target.splitlines() if line and not line.startswith("#")]
        record["segments"] = len(segments)
        record["live"] = "#EXT-X-ENDLIST" not in target
        if not record["live"] and record["state"] == "ok":
            record["state"] = "ok" if segments else "dead"

    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=None)
    parser.add_argument("--out", default=str(STATUS_FILE))
    parser.add_argument("--workers", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0, help="probe only the first N channels (debug)")
    parser.add_argument("--no-deep", action="store_true", help="skip the second playlist hop")
    parser.add_argument("--seed", action="store_true",
                        help="write a provisional status.json from the panel's own online flag (no network)")
    args = parser.parse_args()

    cfg = load_config(args.config)
    watch = load_watch_list()
    channels = watch.get("channels") or []
    if not channels:
        print("::error::watch_list.json has no channels - run scripts/refresh_token.py first")
        return 1
    if args.limit:
        channels = channels[: args.limit]

    if args.seed:
        provisional = {}
        for channel in channels:
            online = bool(channel.get("online"))
            provisional[channel["id"]] = {
                "id": channel["id"], "name": channel["name"], "group": channel.get("group", ""),
                "online": online, "state": "ok" if online else "offline", "http": None,
                "latency_ms": None, "bitrate_kbps": None, "variants": [], "segments": 0,
                "live": False, "error": "", "checked_at": None,
            }
        online_count = sum(1 for c in provisional.values() if c["online"])
        save_json(args.out, {
            "generated_at": now_iso(),
            "source": source_base(cfg),
            "probe": "seed",
            "provisional": True,
            "summary": {
                "total": len(provisional), "ok": online_count, "dead": len(provisional) - online_count,
                "panel_online": online_count, "panel_offline": len(provisional) - online_count,
                "avg_latency_ms": 0, "median_latency_ms": 0,
            },
            "channels": provisional,
        })
        print(f"seeded provisional status.json for {len(provisional)} channels "
              f"({online_count} panel-online) - the _status-check workflow replaces this with real probes")
        return 0

    token = load_token()
    health = cfg.get("health", {}) or {}
    workers = args.workers or int(health.get("concurrency", 8))
    timeout = float(health.get("timeout_seconds", 12))
    retries = int(health.get("retries", 2))
    deep = bool(health.get("deep_check", True)) and not args.no_deep

    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(probe_channel, channel, cfg, token, timeout, retries, deep): channel
            for channel in channels
        }
        for future in futures:
            channel = futures[future]
            try:
                results[channel["id"]] = future.result()
            except Exception as exc:  # pragma: no cover - defensive
                results[channel["id"]] = {
                    "id": channel["id"], "name": channel["name"], "state": "dead",
                    "http": 0, "latency_ms": None, "bitrate_kbps": None, "variants": [],
                    "segments": 0, "live": False, "error": str(exc)[:200], "checked_at": now_iso(),
                }
            state = results[channel["id"]]["state"]
            print(f"  {channel['id']:<6} {channel['name'][:28]:<28} {state}")

    ok = [r for r in results.values() if r["state"] == "ok"]
    latencies = sorted(r["latency_ms"] for r in ok if r["latency_ms"]) or [0]
    data = {
        "generated_at": now_iso(),
        "source": source_base(cfg),
        "probe": "https-playlist",
        "summary": {
            "total": len(results),
            "ok": len(ok),
            "dead": len(results) - len(ok),
            "panel_online": sum(1 for r in results.values() if r.get("online")),
            "panel_offline": sum(1 for r in results.values() if not r.get("online")),
            "avg_latency_ms": round(sum(latencies) / len(latencies)),
            "median_latency_ms": latencies[len(latencies) // 2],
        },
        "channels": results,
    }
    save_json(args.out, data)
    print(f"\nstatus: {len(ok)}/{len(results)} ok - wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
