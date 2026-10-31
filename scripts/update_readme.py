#!/usr/bin/env python3
"""Refresh the generated blocks inside README.md.

Blocks are delimited by:

    <!-- AUTO:HEADER:START -->  ...  <!-- AUTO:HEADER:END -->
    <!-- AUTO:STATS:START -->   ...  <!-- AUTO:STATS:END -->
    <!-- AUTO:CATALOG:START --> ...  <!-- AUTO:CATALOG:END -->

Everything between the markers is replaced, the hand-written prose around them
is left untouched. Badges are live (shields.io reads status.json /
watch_list.json from this repo's default branch), so they animate/update
without another commit.
"""

from __future__ import annotations

import argparse
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    ROOT,
    detect_repo,
    human_age,
    load_config,
    load_status,
    load_watch_list,
    pages_base,
    proxy_base,
    site_cfg,
)

README = ROOT / "README.md"
STATUS_EMOJI = {"ok": "🟢", "dead": "🔴", "offline": "⚪", "unknown": "⚫"}


def block(source: str, name: str, body: str) -> str:
    start = f"<!-- AUTO:{name}:START -->"
    end = f"<!-- AUTO:{name}:END -->"
    if start not in source or end not in source:
        return source
    head, rest = source.split(start, 1)
    _old, tail = rest.split(end, 1)
    return f"{head}{start}\n{body.rstrip()}\n{end}{tail}"


def shield_badge(label: str, value: str, color: str, **extra) -> str:
    return f"![{label}](https://img.shields.io/badge/{urllib.parse.quote(label)}-{urllib.parse.quote(value)}-{color}?style=for-the-badge{extra.get('tail', '')})"


def dynamic_badge(repo: str, branch: str, file: str, query: str, label: str, color: str, suffix: str = "") -> str:
    url = f"https://raw.githubusercontent.com/{repo}/{branch}/{file}"
    pieces = [
        f"url={urllib.parse.quote(url, safe='')}",
        f"query={urllib.parse.quote(query, safe='')}",
        f"label={urllib.parse.quote(label)}",
        f"color={color}",
        "style=for-the-badge",
        "cacheSeconds=300",
    ]
    if suffix:
        pieces.append(f"suffix={urllib.parse.quote(suffix)}")
    alt = f"{label} badge"
    return f"![{alt}](https://img.shields.io/badge/dynamic/json?{'&'.join(pieces)})"


def img_badge(label: str, value: str, color: str, href: str | None = None, logo: str | None = None) -> str:
    src = (
        f"https://img.shields.io/badge/{urllib.parse.quote(label)}-{urllib.parse.quote(value)}-{color}"
        "?style=for-the-badge"
    )
    if logo:
        src += f"&amp;logo={logo}&amp;logoColor=white"
    tag = f'<img src="{src}" alt="{label}">'
    return f'<a href="{href}">{tag}</a>' if href else tag


def img_dynamic(repo: str, branch: str, file: str, query: str, label: str, color: str, suffix: str = "") -> str:
    url = f"https://raw.githubusercontent.com/{repo}/{branch}/{file}"
    src = (
        "https://img.shields.io/badge/dynamic/json"
        f"?url={urllib.parse.quote(url, safe='')}"
        f"&amp;query={urllib.parse.quote(query, safe='')}"
        f"&amp;label={urllib.parse.quote(label)}"
        f"&amp;color={color}&amp;style=for-the-badge&amp;cacheSeconds=300"
    )
    if suffix:
        src += f"&amp;suffix={urllib.parse.quote(suffix)}"
    return f'<img src="{src}" alt="{label}">'


def header_block(cfg: dict, repo: str, branch: str, pages: str, total: int = 0) -> str:
    site = site_cfg(cfg)
    title = urllib.parse.quote(site.get("title", "Restream Portal"))
    banner = (
        "https://capsule-render.vercel.app/api?type=waving&height=210&section=header"
        f"&color=0:05070d,45:0e7490,100:{site.get('brand_color', '#22d3ee').lstrip('#')}"
        f"&text={title}&fontSize=52&fontColor=ffffff&fontAlignY=36&animation=twinkling"
        f"&desc={urllib.parse.quote(f'{total or 0} channels · 24/7 · GitHub Actions powered')}"
        "&descAlignY=56&descSize=18"
    )
    typing = (
        "https://readme-typing-svg.demolab.com?font=JetBrains+Mono&weight=700&size=20"
        "&duration=2600&pause=700&color=22D3EE&center=true&vCenter=true&width=900"
        "&lines=Token+refresh+every+6h+%C2%B7+self-healing;170+channels+%C2%B7+one+workflow+each"
        "+%C2%B7+or+zero+minute+portal+mode;Clean+M3U+%2B+HLS+players+%2B+JSON+status;Deploy+the+"
        "Cloudflare+relay+%E2%86%92+HTTPS+links+everywhere"
    )
    actions = f"https://github.com/{repo}/actions/workflows" if repo else "#"
    rows = [f'<p align="center"><img src="{banner}" alt="{site.get("title", "")}" width="100%"></p>',
            f'<p align="center"><img src="{typing}" alt="typing animation"></p>', ""]

    if repo:
        rows.append('<p align="center">')
        rows.append("  ".join([
            img_dynamic(repo, branch, "status.json", "$.summary.ok", "LIVE", "brightgreen"),
            img_dynamic(repo, branch, "status.json", "$.summary.total", "CHANNELS", "22d3ee"),
            img_dynamic(repo, branch, "status.json", "$.summary.dead", "OFFLINE", "ef4444"),
            img_dynamic(repo, branch, "status.json", "$.summary.avg_latency_ms", "LATENCY", "f59e0b", " ms"),
        ]))
        rows.append("</p>")
        rows.append("")

    rows.append('<p align="center">')
    rows.append("  ".join(filter(None, [
        img_badge("OPEN THE PORTAL", "▶ live dashboard", "22d3ee", pages or None) if pages else "",
        img_badge("mode", str(cfg.get("mode", "portal-only")), "7c3aed"),
        img_badge("dest", str(cfg.get("dest_mode", "proxy-only")), "0ea5e9"),
        img_badge("panel", f'{cfg.get("panel", {}).get("name", "panel")} v{cfg.get("panel", {}).get("version", "")}', "22d3ee"),
        img_badge("license", "MIT", "3da639", "https://github.com/" + repo + "/blob/" + branch + "/LICENSE" if repo else None),
    ])))
    rows.append("</p>")
    rows.append("")

    if repo:
        rows.append('<p align="center">')
        rows.append("  ".join([
            f'<a href="{actions}/_token-refresh.yml"><img src="https://github.com/{repo}/actions/workflows/_token-refresh.yml/badge.svg?branch={branch}" alt="token bot"></a>',
            f'<a href="{actions}/_status-check.yml"><img src="https://github.com/{repo}/actions/workflows/_status-check.yml/badge.svg?branch={branch}" alt="status bot"></a>',
            f'<a href="{actions}/_site-build.yml"><img src="https://github.com/{repo}/actions/workflows/_site-build.yml/badge.svg?branch={branch}" alt="site build"></a>',
            f'<a href="https://github.com/{repo}/commits/{branch}"><img src="https://img.shields.io/github/last-commit/{repo}?style=for-the-badge&amp;color=22d3ee&amp;label=last%20refresh" alt="last commit"></a>',
        ]))
        rows.append("</p>")
    return "\n".join(rows)


def stats_block(cfg: dict, watch: dict, status: dict, pages: str) -> str:
    summary = status.get("summary") or {}
    channels = watch.get("channels") or []
    smap = status.get("channels") or {}
    total = summary.get("total", len(channels))
    ok = summary.get("ok", 0)
    dead = summary.get("dead", 0)
    panel_online = summary.get("panel_online", sum(1 for c in channels if c.get("online")))
    avg = summary.get("avg_latency_ms", 0)
    top = max((smap.get(c["id"], {}).get("bitrate_kbps") or 0) for c in channels) if channels else 0
    groups: dict[str, int] = {}
    for channel in channels:
        groups[channel.get("group", "Other")] = groups.get(channel.get("group", "Other"), 0) + 1

    updated = watch.get("fetched_at") or status.get("generated_at") or "-"
    health = round(100 * ok / total) if total else 0
    portal = f"[**{pages.replace('https://', '')}**]({pages})" if pages else "_enable GitHub Pages to publish the portal (see setup step 2)_"
    lines = [
        "| Metric | Value |",
        "| --- | --- |",
        f"| **Channels in catalog** | `{total}` |",
        f"| **Streaming right now** | `{ok}` ({health}% healthy) |",
        f"| **Dead / unreachable** | `{dead}` |",
        f"| **Panel reports online** | `{panel_online}` |",
        f"| **Average probe latency** | {f'`{avg} ms`' if avg else '_pending first health check_'} |",
        f"| **Best bitrate** | {f'`{round(top / 1000, 1)} Mbps`' if top else '_pending first health check_'} |",
        f"| **Catalog refreshed** | `{updated}` ({human_age(updated)}) |",
        f"| **Portal URL** | {portal} |",
        f"| **Relay (https layer)** | {('`' + proxy_base(cfg) + '`') if proxy_base(cfg) else '_not configured - direct links are plain HTTP_'} |",
        "",
        "**Groups:** " + " · ".join(f"{name} `{count}`" for name, count in sorted(groups.items(), key=lambda kv: -kv[1])),
    ]
    return "\n".join(lines)


def catalog_block(watch: dict, status: dict, pages: str) -> str:
    channels = watch.get("channels") or []
    smap = status.get("channels") or {}
    lines = [
        f"{len(channels)} channels · 🟢 live · 🔴 dead · ⚪ panel says offline. "
        "Names link to the browser player, `id` to the single-channel M3U.",
        "",
        "| | Channel | ID | Group | Links | Latency | Bitrate |",
        "|:--:| --- | --- | --- | --- | --- | --- |",
    ]
    for channel in channels:
        info = smap.get(channel["id"], {}) or {}
        state = info.get("state") or ("offline" if not channel.get("online") else "unknown")
        emoji = STATUS_EMOJI.get(state, "⚫")
        name = str(channel["name"]).replace("|", "/")
        group = str(channel.get("group", "")).replace("|", "/")
        page = f"{pages}/ch/{channel['id']}.html" if pages else f"docs/ch/{channel['id']}.html"
        m3u = f"{pages}/playlist/{channel['id']}.m3u" if pages else f"docs/playlist/{channel['id']}.m3u"
        latency = f"{int(info['latency_ms'])} ms" if info.get("latency_ms") else "-"
        bitrate = f"{round((info.get('bitrate_kbps') or 0) / 1000, 1)} Mbps" if info.get("bitrate_kbps") else "-"
        lines.append(
            f"| {emoji} | [{name}]({page}) | [`{channel['id']}`]({m3u}) | {group} | "
            f"[player]({page}) · [m3u]({m3u}) | {latency} | {bitrate} |"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--readme", default=str(README))
    parser.add_argument("--branch", default=None)
    parser.add_argument("--check", action="store_true", help="exit 1 if the file would change")
    args = parser.parse_args()

    path = Path(args.readme)
    source = path.read_text(encoding="utf-8")
    cfg = load_config()
    watch = load_watch_list()
    status = load_status()
    repo = detect_repo()
    pages = pages_base(cfg, repo)
    branch = args.branch
    if not branch:
        branch = (site_cfg(cfg).get("branch") or "main")

    total = len(watch.get('channels') or [])
    updated = block(source, "HEADER", header_block(cfg, repo, branch, pages, total))
    updated = block(updated, "STATS", stats_block(cfg, watch, status, pages))
    updated = block(updated, "CATALOG", catalog_block(watch, status, pages))

    if args.check:
        return 0 if updated == source else 1
    if updated != source:
        path.write_text(updated, encoding="utf-8")
        print(f"updated {path.name} (repo={repo or 'unknown'}, pages={pages or 'unknown'})")
    else:
        print(f"{path.name} already up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
