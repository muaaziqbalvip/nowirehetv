#!/usr/bin/env python3
"""Build the GitHub Pages portal into docs/ (index, per-channel players, M3U).

Inputs : config.json, watch_list.json, status.json, token.json (optional)
Outputs: docs/index.html, docs/ch/<id>.html, docs/playlist.m3u,
         docs/playlist/<id>.m3u, docs/assets/*, docs/data/channels.json,
         docs/status.json, docs/watch_list.json, docs/.nojekyll, docs/404.html

Every internal link is relative, so the same output works on
https://<user>.github.io/<repo>/, on a custom domain, or from file://.
"""

from __future__ import annotations

import argparse
import html
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    ROOT,
    TOKEN_FILE,
    build_m3u,
    build_play_url,
    detect_repo,
    human_age,
    load_config,
    load_status,
    load_token,
    load_watch_list,
    now_iso,
    pages_base,
    proxy_base,
    public_stream_url,
    rel,
    save_json,
    site_cfg,
    source_base,
)

TEMPLATES = ROOT / "templates"

FAVICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="{brand}"/><stop offset="1" stop-color="{accent}"/>
  </linearGradient></defs>
  <rect width="64" height="64" rx="16" fill="url(#g)"/>
  <g fill="none" stroke="#041016" stroke-width="5" stroke-linecap="round">
    <path d="M12 26a28 28 0 0 1 40 0"/><path d="M21 36a17 17 0 0 1 22 0"/>
    <circle cx="32" cy="47" r="4" fill="#041016"/>
  </g>
</svg>
"""

STATE_LABEL = {"ok": "Live", "dead": "Dead", "offline": "Offline", "unknown": "Unknown"}


def read_template(name: str) -> str:
    return (TEMPLATES / name).read_text(encoding="utf-8")


def render(template: str, mapping: dict) -> str:
    for key, value in mapping.items():
        template = template.replace("{{" + key + "}}", str(value))
    return template


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def initials(name: str) -> str:
    cleaned = " ".join(str(name or "?").replace("_", " ").split())
    parts = [p for p in cleaned.split(" ") if p]
    if not parts:
        return "TV"
    if parts[0][:1].isdigit():
        return cleaned[:2].upper()
    letters = "".join(p[0] for p in parts[:2])
    return (letters or "TV").upper()


def human_bitrate(kbps) -> str:
    if not kbps:
        return "-"
    if kbps >= 1000:
        return f"{kbps / 1000:.1f} Mbps"
    return f"{int(kbps)} kbps"


def state_badge(state: str, online: bool) -> str:
    if state == "ok":
        return '<span class="badge live"><span class="dot on"></span>Live</span>'
    if state == "offline" or not online:
        return '<span class="badge offline"><span class="dot off"></span>Offline</span>'
    return '<span class="badge dead"><span class="dot off"></span>Dead</span>'


def build_notice(cfg: dict, watch: dict, status: dict) -> str:
    parts: list[str] = []
    base = proxy_base(cfg)
    if not TOKEN_FILE.exists() and not base:
        parts.append(
            '<div class="notice"><span>&#9888;</span><div>'
            "<strong>No live token yet.</strong> The links below use the bootstrap token from "
            "<code>token.example.json</code>, which expires quickly. Run the <code>_token-refresh</code> "
            "workflow (or <code>python3 scripts/refresh_token.py</code>) to bake in a fresh one - or deploy "
            "the relay, which keeps the token server-side.</div></div>"
        )
    if base:
        parts.append(
            '<div class="notice ok"><span>&#10003;</span><div>'
            f"Public stream links are served through the HTTPS relay <code>{esc(base)}</code>. "
            "Browsers, VLC and every IPTV app can play them, and the origin stays hidden.</div></div>"
        )
    else:
        parts.append(
            '<div class="notice"><span>&#9888;</span><div>'
            "<strong>HTTPS relay not configured.</strong> The panel is plain HTTP, so browsers block "
            "direct playback on this HTTPS page (VLC, mpv and TiviMate still work). Deploy "
            "<code>scripts/proxy_worker.js</code> to Cloudflare Workers, put its URL in "
            "<code>config.json &rarr; proxy.base_url</code>, then paste it in the field on any channel page. "
            "Full steps: <a href=\"{{README_URL}}\" style=\"color:inherit\">README &rarr; HTTPS relay</a>."
            "</div></div>"
        )
    if watch.get("stale"):
        parts.append(
            '<div class="notice"><span>&#9888;</span><div>'
            f"Catalog is stale - the panel was unreachable at the last refresh "
            f"({esc(watch.get('last_error', 'unknown error'))}). Last known channels and token are kept "
            "until the next successful run.</div></div>"
        )
    if status.get("provisional"):
        parts.append(
            '<div class="notice"><span>&#9888;</span><div>'
            "<strong>Provisional status.</strong> This build uses the panel's own online flags; the "
            "<code>_status-check</code> workflow replaces them with real probe results (latency, bitrate, "
            "live-window) on its next run.</div></div>"
        )
    if not status.get("channels"):
        parts.append(
            '<div class="notice"><span>&#9888;</span><div>'
            "No health check has run yet - statuses show the panel's own online flag. Run the "
            "<code>_status-check</code> workflow to populate status.json.</div></div>"
        )
    return "\n  ".join(parts)


def build_ticker(channels: list[dict], status_map: dict, limit: int = 60) -> str:
    live = [c for c in channels if status_map.get(c["id"], {}).get("state") == "ok"][:limit]
    pool = live or channels[:limit]
    items = []
    for channel in pool:
        state = status_map.get(channel["id"], {}).get("state", "offline")
        dot = '<span class="dot on"></span>' if state == "ok" else '<span class="dot off"></span>'
        items.append(
            f'<span class="ticker-item">{dot}<b>{esc(channel["name"])}</b> {esc(channel.get("group", ""))}</span>'
        )
    if not items:
        return '<span class="ticker-item">No channels loaded yet - run the token-refresh bot.</span>'
    # duplicated once for a seamless -50% translate loop
    return "".join(items) + "".join(items)


def build_cards(channels: list[dict], status_map: dict) -> str:
    cards = []
    for channel in channels:
        info = status_map.get(channel["id"], {})
        state = info.get("state") or ("offline" if not channel.get("online") else "unknown")
        resolution = ""
        variants = info.get("variants") or []
        if variants:
            resolution = str(variants[-1].get("resolution") or "").replace("x", "&times;")
        meta = [state_badge(state, channel.get("online", False))]
        if resolution:
            meta.append(f'<span class="badge">{resolution}</span>')
        if info.get("bitrate_kbps"):
            meta.append(f'<span class="badge">{human_bitrate(info["bitrate_kbps"])}</span>')
        if info.get("latency_ms"):
            meta.append(f'<span class="badge">{int(info["latency_ms"])} ms</span>')
        cards.append(f"""    <article class="card" data-id="{esc(channel['id'])}">
      <div class="card-head">
        <div class="avatar" aria-hidden="true">{esc(initials(channel['name']))}</div>
        <div>
          <h2 class="card-title">{esc(channel['name'])}</h2>
          <div class="card-sub">{esc(channel['id'])} &middot; {esc(channel.get('group', ''))}</div>
        </div>
      </div>
      <div class="card-meta">{''.join(meta)}</div>
      <div class="card-actions">
        <a class="btn primary" href="ch/{esc(channel['id'])}.html">&#9654; Watch</a>
        <a class="btn ghost" href="playlist/{esc(channel['id'])}.m3u" title="M3U playlist for this channel">M3U</a>
        <button class="btn ghost" type="button" data-copy="stream" data-id="{esc(channel['id'])}" title="Copy stream URL">&#10697;</button>
      </div>
    </article>""")
    return "\n".join(cards)


def direct_row(cfg: dict, stream_url: str, direct_url: str, show_direct: bool) -> str:
    if stream_url == direct_url:
        return (
            '      <p class="mono" style="color:var(--muted);margin-top:10px">'
            "No relay configured: the stream URL above is the panel URL itself. "
            "Paste it into VLC, mpv, TiviMate or OTT Navigator.</p>"
        )
    if not show_direct:
        return (
            '      <p class="mono" style="color:var(--muted);margin-top:10px">'
            "Origin URL hidden - traffic goes through the relay.</p>"
        )
    return f"""      <div class="link-box">
        <input id="vlc-url" readonly value="{esc(direct_url)}" aria-label="Direct panel URL">
        <button class="btn ghost" type="button" data-copy="{esc(direct_url)}" title="Copy the direct panel URL">&#10697; Copy</button>
        <a class="btn ghost" href="{esc(direct_url)}" title="Open the playlist in VLC">VLC</a>
      </div>"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=None)
    parser.add_argument("--out", default=None)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    cfg = load_config(args.config)
    watch = load_watch_list()
    status = load_status()
    token = load_token()
    repo = detect_repo()
    pages = pages_base(cfg, repo)
    readme_url = f"https://github.com/{repo}#readme" if repo else "https://github.com"

    out = Path(args.out) if args.out else ROOT / str(site_cfg(cfg).get("output_dir", "docs"))
    out.mkdir(parents=True, exist_ok=True)
    for sub in ("ch", "playlist", "assets", "data"):
        (out / sub).mkdir(parents=True, exist_ok=True)

    channels = watch.get("channels") or []
    if not channels:
        print("::warning::watch_list.json has no channels - building an empty portal")
    status_map = status.get("channels") or {}
    stats = status.get("summary") or {}
    base = proxy_base(cfg)
    show_direct = bool(site_cfg(cfg).get("publish_direct_urls", True))
    brand = site_cfg(cfg).get("brand_color", "#22d3ee")
    accent = site_cfg(cfg).get("accent_color", "#a855f7")
    title = site_cfg(cfg).get("title", "Restream Portal")
    short_title = site_cfg(cfg).get("short_title", title)
    label = f'{cfg.get("panel", {}).get("name", "panel")} v{cfg.get("panel", {}).get("version", "")}'.strip()

    # ---------------------------------------------------------------- playlists
    all_entries: list[dict] = []
    per_channel_entries: dict[str, list[dict]] = {}
    for channel in channels:
        stream = public_stream_url(cfg, channel["id"], token, repo)
        entry = {
            "tvg_id": channel["id"],
            "tvg_name": channel["name"],
            "tvg_logo": channel.get("logo", ""),
            "group": channel.get("group", ""),
            "name": channel["name"],
            "url": stream,
        }
        all_entries.append(entry)
        per_channel_entries[channel["id"]] = [entry]

    (out / "playlist.m3u").write_text(build_m3u(all_entries), encoding="utf-8")
    for cid, entries in per_channel_entries.items():
        (out / "playlist" / f"{cid}.m3u").write_text(build_m3u(entries), encoding="utf-8")

    # drop stale generated pages for channels that no longer exist
    valid = set(per_channel_entries)
    for folder, suffix in ((out / "ch", ".html"), (out / "playlist", ".m3u")):
        for path in folder.glob(f"*{suffix}"):
            if path.stem not in valid:
                path.unlink()

    # ------------------------------------------------------------------ assets
    (out / "assets" / "site.css").write_text(
        read_template("site.css").replace("{{BRAND}}", brand).replace("{{ACCENT}}", accent),
        encoding="utf-8",
    )
    shutil.copyfile(TEMPLATES / "site.js", out / "assets" / "site.js")
    (out / "assets" / "favicon.svg").write_text(
        FAVICON.replace("{brand}", brand).replace("{accent}", accent), encoding="utf-8"
    )
    (out / ".nojekyll").write_text("", encoding="utf-8")

    # --------------------------------------------------------------- index page
    groups: dict[str, int] = {}
    for channel in channels:
        groups[channel.get("group", "Other")] = groups.get(channel.get("group", "Other"), 0) + 1
    group_options = "\n    ".join(
        f'<option value="{esc(name)}">{esc(name)} ({count})</option>'
        for name, count in sorted(groups.items())
    )

    top_bitrate = max((status_map.get(c["id"], {}).get("bitrate_kbps") or 0) for c in channels) if channels else 0
    ok_count = stats.get("ok", sum(1 for v in status_map.values() if v.get("state") == "ok"))
    dead_count = stats.get("dead", len(channels) - ok_count)
    latency = stats.get("avg_latency_ms", 0)

    client_channels = [
        {
            "id": c["id"],
            "name": c["name"],
            "group": c.get("group", ""),
            "online": bool(c.get("online")),
            "state": (status_map.get(c["id"], {}) or {}).get("state", "unknown"),
            "stream_url": public_stream_url(cfg, c["id"], token, repo),
        }
        for c in channels
    ]
    config_json = json.dumps(
        {
            "proxy": base,
            "pages_base": pages,
            "generated_at": now_iso(),
            "channels": client_channels,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).replace("</", "<\\/")

    updated_at = watch.get("fetched_at") or status.get("generated_at") or now_iso()
    mapping = {
        "TITLE": esc(title),
        "SHORT_TITLE": esc(short_title),
        "TAGLINE": esc(site_cfg(cfg).get("tagline", "")),
        "DESCRIPTION": esc(site_cfg(cfg).get("description", "")),
        "STAT_TOTAL": len(channels),
        "STAT_OK": ok_count,
        "STAT_DEAD": dead_count,
        "STAT_LATENCY": int(latency or 0),
        "STAT_BITRATE": int(top_bitrate or 0),
        "TICKER_ITEMS": build_ticker(channels, status_map),
        "GROUP_OPTIONS": group_options,
        "CARDS": build_cards(channels, status_map),
        "NOTICE": build_notice(cfg, watch, status),
        "CONFIG_JSON": config_json,
        "UPDATED_AT": esc(updated_at),
        "UPDATED_HUMAN": esc(human_age(updated_at)),
        "SOURCE_LABEL": esc(label),
        "README_URL": readme_url,
        "FOOTER_NOTE": esc(
            "Unofficial portal generated from a third-party panel - "
            "you must be authorised to redistribute every channel listed here."
        ),
    }
    (out / "index.html").write_text(render(read_template("index.html"), mapping), encoding="utf-8")

    # --------------------------------------------------------- channel players
    channel_tpl = read_template("channel.html")
    for channel in channels:
        info = status_map.get(channel["id"], {}) or {}
        state = info.get("state") or ("offline" if not channel.get("online") else "unknown")
        stream = public_stream_url(cfg, channel["id"], token, repo)
        direct = build_play_url(cfg, channel["id"], token)
        variants = info.get("variants") or []
        variant_label = ", ".join(
            filter(None, [
                (f"{v.get('resolution')}@{round((v.get('bandwidth') or 0) / 1000)}k" if v.get("resolution")
                 else (f"{round((v.get('bandwidth') or 0) / 1000)}k" if v.get("bandwidth") else ""))
                for v in variants
            ])
        ) or "-"
        badges = [state_badge(state, channel.get("online", False))]
        if channel.get("online"):
            badges.append('<span class="badge brand">panel: online</span>')
        if info.get("bitrate_kbps"):
            badges.append(f'<span class="badge">{human_bitrate(info["bitrate_kbps"])}</span>')

        channel_config = json.dumps(
            {"proxy": base, "channels": [entry for entry in client_channels if entry["id"] == channel["id"]]},
            ensure_ascii=False, separators=(",", ":"),
        ).replace("</", "<\\/")
        ctx = {
            "TITLE": esc(title),
            "SHORT_TITLE": esc(short_title),
            "NAME": esc(channel["name"]),
            "ID": esc(channel["id"]),
            "GROUP": esc(channel.get("group", "")),
            "INITIALS": esc(initials(channel["name"])),
            "SOURCE_LABEL": esc(label),
            "STATUS_BADGES": "".join(badges),
            "STREAM_URL": esc(stream),
            "DIRECT_ROW": direct_row(cfg, stream, direct, show_direct),
            "VLC_URL": esc(direct),
            "STATE": esc(STATE_LABEL.get(state, state)),
            "HTTP": esc(info.get("http") or "-"),
            "LATENCY": esc(f'{int(info["latency_ms"])} ms' if info.get("latency_ms") else "-"),
            "BITRATE": esc(human_bitrate(info.get("bitrate_kbps"))),
            "VARIANTS": esc(len(variants) or "-"),
            "VARIANT_LABEL": esc(variant_label),
            "SEGMENTS": esc(info.get("segments") or "-"),
            "CHECKED_HUMAN": esc(human_age(info.get("checked_at"))),
            "UPDATED_AT": esc(updated_at),
            "FOOTER_NOTE": esc("unofficial portal, authorised use only"),
            "README_URL": readme_url,
            "CONFIG_JSON": channel_config,
        }
        (out / "ch" / f"{channel['id']}.html").write_text(render(channel_tpl, ctx), encoding="utf-8")

    # -------------------------------------------------------------- json + 404
    public_channels = []
    for c in channels:
        info = status_map.get(c["id"], {}) or {}
        variants = info.get("variants") or []
        public_channels.append({
            "id": c["id"],
            "name": c["name"],
            "group": c.get("group", ""),
            "logo": c.get("logo", ""),
            "panel_online": bool(c.get("online")),
            "state": info.get("state") or ("offline" if not c.get("online") else "unknown"),
            "http": info.get("http"),
            "latency_ms": info.get("latency_ms"),
            "bitrate_kbps": info.get("bitrate_kbps"),
            "resolution": (variants[-1].get("resolution") if variants else ""),
            "live": info.get("live", False),
            "stream_url": public_stream_url(cfg, c["id"], token, repo),
            "m3u": f"playlist/{c['id']}.m3u",
            "page": f"ch/{c['id']}.html",
        })

    save_json(out / "data" / "channels.json", {
        "generated_at": now_iso(),
        "name": watch.get("name") or cfg.get("panel", {}).get("name"),
        "version": watch.get("version"),
        "pages_base": pages,
        "relay": base,
        "counts": {
            "total": len(public_channels),
            "panel_online": sum(1 for c in public_channels if c["panel_online"]),
            "live": sum(1 for c in public_channels if c["state"] == "ok"),
        },
        "channels": public_channels,
    })

    public_watch = {
        "name": watch.get("name"),
        "version": watch.get("version"),
        "fetched_at": watch.get("fetched_at"),
        "stale": watch.get("stale", False),
        "channel_count": len(channels),
        "online_count": watch.get("online_count"),
        "channels": [
            {"id": c["id"], "name": c["name"], "group": c.get("group", ""), "online": bool(c.get("online"))}
            for c in channels
        ],
        "_note": "Public catalog copy - the live token is never published.",
    }
    save_json(out / "watch_list.json", public_watch)
    if status:
        save_json(out / "status.json", status)

    notfound = render(read_template("404.html"), {
        "TITLE": esc(title),
        "SHORT_TITLE": esc(short_title),
        "README_URL": readme_url,
    })
    (out / "404.html").write_text(notfound, encoding="utf-8")

    if not args.quiet:
        print(f"built {rel(out)}/: index ({len(channels)} cards), {len(per_channel_entries)} player pages, "
              f"{len(per_channel_entries) + 1} M3U files")
        print(f"       relay: {base or 'NOT CONFIGURED (direct http links)'}")
        print(f"       pages: {pages or 'unknown (no git remote / GITHUB_REPOSITORY)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
