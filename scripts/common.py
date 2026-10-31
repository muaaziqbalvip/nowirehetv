"""Shared helpers for the EasyISP24 -> GitHub restream pipeline.

Everything here is stdlib-only so GitHub Actions never needs `pip install`.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
WATCH_LIST = ROOT / "watch_list.json"
TOKEN_FILE = ROOT / "token.json"
STATUS_FILE = ROOT / "status.json"
CONFIG_FILE = ROOT / "config.json"

DEFAULT_UA = "VLC/3.0.20"

# --------------------------------------------------------------------------
# config / io
# --------------------------------------------------------------------------


def load_config(path: str | Path | None = None) -> dict:
    path = Path(path) if path else CONFIG_FILE
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def read_json(path: str | Path, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path: str | Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def human_age(iso_str: str | None) -> str:
    if not iso_str:
        return "unknown"
    try:
        then = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except ValueError:
        return "unknown"
    delta = (datetime.now(timezone.utc) - then).total_seconds()
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    return f"{int(delta // 86400)}d ago"


# --------------------------------------------------------------------------
# text helpers
# --------------------------------------------------------------------------


def slugify(text: str, max_len: int = 40) -> str:
    """ASCII-safe slug used for file names (ch-<id>-<slug>.yml)."""
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return (text[:max_len].strip("-")) or "channel"


def m3u_attr(value: Any) -> str:
    """Sanitize a value that goes inside a quoted M3U attribute.

    A comma inside an attribute value breaks many IPTV parsers, so commas are
    flattened to spaces and double quotes become single quotes.
    """
    text = str(value if value is not None else "")
    text = text.replace('"', "'").replace(",", " ").replace("\r", " ").replace("\n", " ")
    return " ".join(text.split())


def m3u_name(value: Any) -> str:
    """Sanitize the display name that follows the comma in #EXTINF."""
    text = str(value if value is not None else "").replace("\r", " ").replace("\n", " ")
    return " ".join(text.split()) or "Channel"


def build_m3u(entries: Iterable[dict]) -> str:
    """Clean 2-line M3U: header, then EXTINF + URL per channel."""
    lines = ["#EXTM3U"]
    for entry in entries:
        attrs = [
            f'tvg-id="{m3u_attr(entry.get("tvg_id", ""))}"',
            f'tvg-name="{m3u_attr(entry.get("tvg_name", ""))}"',
            f'tvg-logo="{m3u_attr(entry.get("tvg_logo", ""))}"',
            f'group-title="{m3u_attr(entry.get("group", ""))}"',
        ]
        lines.append(f'#EXTINF:-1 {" ".join(attrs)},{m3u_name(entry.get("name"))}')
        lines.append(str(entry.get("url", "")))
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# channel classification (used only when the panel itself has no group)
# --------------------------------------------------------------------------

_GROUP_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("Sports", (
        "sport", "ten ", "ten1", "ten2", "ten3", "ten5", "t sports", "tsports", "eurosport",
        "star sports", "skynet", "bein", "fifa", "wwe", "cricket", "ptv-sports", "ksa",
        "unite8",
    )),
    ("Kids", ("cartoon", "nick", "pogo", "disney", "junior")),
    ("Movies", ("hbo", "movies", "movie", "hits", "super hit", "cinema", "star gold", "enc ")),
    ("News", (
        "news", "24", "ekhon", "jamuna", "ekattor", "dbc", "atn", "al jazeera", "cnn",
        "bbc", "somoy", "independent", "desh", "mohona", "ananda", "ekushey", "global",
        "nexus", "channel s", "sangsad", "mytv", "rtv", "boishakhi", "ekattor", "international",
    )),
    ("Infotainment", ("discovery", "nat geo", "animal planet", "history", "travel")),
    ("Bangla", (
        "bangla", "bangla tv", "asian", "bijoy", "sa tv", "madani", "channel i", "ntv",
        "btv", "ruposhi", "jalsha", "sony aath", "star jalsha", "sun bangla", "colors bangla",
    )),
    ("Entertainment", (
        "star plus", "zee", "colors", "sony", "set ", "hum", "srk", "enter10", "enterr10",
        "sitary", "star jalsha", "megahit", "ktv", "sab", "&tv",
    )),
]


def classify_group(name: str) -> str:
    lowered = " " + str(name or "").lower().strip() + " "
    for group, needles in _GROUP_RULES:
        for needle in needles:
            if needle in lowered:
                return group
    return "Other"


# --------------------------------------------------------------------------
# urls
# --------------------------------------------------------------------------


def source_base(cfg: dict) -> str:
    return str(cfg.get("source_base", "")).rstrip("/")


def _extract_token(payload: Any, field: str) -> str:
    if not field:
        return ""
    node: Any = payload
    for part in str(field).split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return ""
    return str(node or "")


def list_url(cfg: dict) -> str:
    return source_base(cfg) + cfg.get("list_path", "/watch/list")


def build_play_url(cfg: dict, channel_id: str, token: str = "") -> str:
    """Render play_url_template for one channel, honouring auth_mode."""
    template = cfg.get("play_url_template", "/play/<ID>/index.m3u8?token=<TOKEN>")
    auth_mode = (cfg.get("auth_mode") or "token-query").lower()
    auth = cfg.get("auth", {}) or {}
    query_param = auth.get("query_param", "token")

    url = str(template).replace("<ID>", urllib.parse.quote(str(channel_id), safe=""))
    url = url.replace("<NAME>", urllib.parse.quote(str(channel_id), safe=""))

    if auth_mode == "token-query":
        url = url.replace("<TOKEN>", urllib.parse.quote(token, safe=""))
    else:
        # header / cookie / none auth: token never appears in the URL
        url = re.sub(r"([?&])" + re.escape(query_param) + r"=<TOKEN>&?", r"\1", url)
        url = url.replace("<TOKEN>", "")
        url = url.replace("?&", "?").replace("&&", "&").rstrip("?&")

    if url.startswith("http://") or url.startswith("https://"):
        return url
    return source_base(cfg) + "/" + url.lstrip("/")


def proxy_base(cfg: dict) -> str:
    return str((cfg.get("proxy") or {}).get("base_url", "") or "").strip().rstrip("/")


def site_cfg(cfg: dict) -> dict:
    return cfg.get("site", {}) or {}


def detect_repo() -> str:
    """owner/repo from GITHUB_REPOSITORY or the local git remote."""
    env = (os.environ.get("GITHUB_REPOSITORY") or "").strip()
    if "/" in env:
        return env
    try:
        out = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    match = re.search(r"github\.com[:/]([^/]+)/([^/\s]+?)(?:\.git)?$", out)
    return f"{match.group(1)}/{match.group(2)}" if match else ""


def pages_base(cfg: dict, repo: str | None = None) -> str:
    """Public GitHub Pages base URL for this repo (no trailing slash)."""
    configured = str(site_cfg(cfg).get("pages_base", "") or "").strip()
    if configured:
        return configured.rstrip("/")
    repo = repo if repo is not None else detect_repo()
    if "/" not in repo:
        return ""
    owner, name = repo.split("/", 1)
    if name.lower() == f"{owner.lower()}.github.io":
        return f"https://{owner.lower()}.github.io"
    return f"https://{owner.lower()}.github.io/{name}"


def public_stream_url(cfg: dict, channel_id: str, token: str = "", repo: str | None = None) -> str:
    """Where the outside world should pull this channel from.

    Priority: Cloudflare Worker (https) -> HLS re-host branch -> direct panel URL.
    """
    base = proxy_base(cfg)
    if base:
        return f"{base}/live/{channel_id}.m3u8"
    if (cfg.get("dest_mode") or "").lower() == "hls-rehost":
        pages = pages_base(cfg, repo)
        if pages:
            return f"{pages}/streams/{channel_id}/index.m3u8"
    return build_play_url(cfg, channel_id, token)


# --------------------------------------------------------------------------
# http
# --------------------------------------------------------------------------


def _request(url: str, cfg: dict, timeout: float = 20.0, headers: dict | None = None) -> urllib.request.Request:
    auth = (cfg or {}).get("auth", {}) or {}
    ua = auth.get("user_agent") or DEFAULT_UA
    req = urllib.request.Request(url, method="GET")
    req.add_header("User-Agent", ua)
    req.add_header("Accept", "*/*")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    return req


def http_get_text(
    url: str,
    cfg: dict | None = None,
    timeout: float = 20.0,
    headers: dict | None = None,
) -> tuple[int, str, float]:
    """GET a URL, return (status, body, latency_ms). 0 status on network error."""
    cfg = cfg or {}
    req = _request(url, cfg, timeout, headers)
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            return resp.status, body, round((time.time() - started) * 1000, 1)
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:  # pragma: no cover - defensive
            body = ""
        return exc.code, body, round((time.time() - started) * 1000, 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 0, str(exc), round((time.time() - started) * 1000, 1)


def fetch_list(cfg: dict, retries: int = 4, base_delay: float = 2.0) -> dict:
    """Fetch /watch/list with exponential backoff. Raises RuntimeError on failure."""
    url = list_url(cfg)
    last = "unknown error"
    for attempt in range(retries):
        status, body, _latency = http_get_text(url, cfg, timeout=25)
        if status == 200:
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                last = "invalid JSON from list endpoint"
        else:
            last = f"HTTP {status}: {body[:160]}"
        if attempt < retries - 1:
            time.sleep(base_delay * (2 ** attempt))
    raise RuntimeError(f"list fetch failed ({url}): {last}")


# --------------------------------------------------------------------------
# channels
# --------------------------------------------------------------------------


def normalise_channels(payload: dict, previous: dict | None = None) -> list[dict]:
    """Panel JSON -> stable channel records, keeping prior order/groups."""
    raw = payload.get("channels") or []
    prev_channels = (previous or {}).get("channels") or []
    prev_by_id = {str(c.get("id")): c for c in prev_channels}

    channels: list[dict] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("id") or "").strip()
        if not cid or cid in seen:
            continue
        seen.add(cid)
        name = str(item.get("name") or cid).strip()
        prev = prev_by_id.get(cid, {})
        group = str(item.get("group") or "").strip() or str(prev.get("group") or "").strip()
        if not group:
            group = classify_group(name)
        channels.append({
            "id": cid,
            "name": name,
            "group": group,
            "logo": str(item.get("logo") or prev.get("logo") or ""),
            "online": bool(item.get("online", prev.get("online", False))),
            "slug": slugify(f"{name}-{cid}", 48),
        })

    # keep channels that existed before but vanished from the panel (still listed)
    for prev in prev_channels:
        cid = str(prev.get("id") or "")
        if cid and cid not in seen:
            channels.append({
                "id": cid,
                "name": prev.get("name", cid),
                "group": prev.get("group") or classify_group(prev.get("name", "")),
                "logo": prev.get("logo", ""),
                "online": False,
                "slug": prev.get("slug") or slugify(f"{prev.get('name', cid)}-{cid}", 48),
            })
            seen.add(cid)

    channels.sort(key=lambda c: (not c["online"], c["group"], c["name"].lower()))
    return channels


def token_expiry(token: str) -> int | None:
    """Extract the unix expiry from a `w.<unix>.<random>` style token."""
    match = re.match(r"^[a-z]\.(\d{9,12})\.", str(token or ""))
    return int(match.group(1)) if match else None


def load_watch_list() -> dict:
    return read_json(WATCH_LIST, {}) or {}


def load_status() -> dict:
    return read_json(STATUS_FILE, {}) or {}


def load_token() -> str:
    """Live token if the bot has fetched one, else the documented bootstrap sample."""
    data = read_json(TOKEN_FILE, {}) or {}
    token = str(data.get("token") or "")
    if token:
        return token
    sample = read_json(ROOT / "token.example.json", {}) or {}
    return str(sample.get("token") or "")


def write_github_env(name: str, value: str) -> None:
    """Expose a value to later workflow steps, masked in the logs."""
    if not value:
        return
    print(f"::add-mask::{value}")
    target = os.environ.get("GITHUB_ENV")
    if target:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(f"{name}={value}\n")


def channel_by_id(channels: Iterable[dict], channel_id: str) -> dict | None:
    for channel in channels:
        if channel.get("id") == channel_id:
            return channel
    return None


def rel(path: Path) -> str:
    """Path relative to repo root, posix style."""
    return Path(path).resolve().relative_to(ROOT).as_posix()
