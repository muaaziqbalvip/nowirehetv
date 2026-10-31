"""Regression tests for the restream pipeline (no network required).

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_site  # noqa: E402
import common  # noqa: E402
import generate_workflows  # noqa: E402
import health_check  # noqa: E402
import update_readme  # noqa: E402

CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))

FIXTURE_WATCH = {
    "name": "Test Panel",
    "version": "9.9.9",
    "fetched_at": "2026-10-31T00:00:00Z",
    "stale": False,
    "channel_count": 3,
    "online_count": 2,
    "channels": [
        {"id": "aaa1", "name": "Alpha, One", "group": "News", "logo": "", "online": True},
        {"id": "bbb2", "name": "Beta Two", "group": "Sports", "logo": "", "online": True},
        {"id": "ccc3", "name": "Gamma Three", "group": "Movies", "logo": "", "online": False},
    ],
}

FIXTURE_STATUS = {
    "generated_at": "2026-10-31T00:05:00Z",
    "probe": "https-playlist",
    "summary": {"total": 3, "ok": 2, "dead": 1, "panel_online": 2, "panel_offline": 1,
                "avg_latency_ms": 210, "median_latency_ms": 200},
    "channels": {
        "aaa1": {"id": "aaa1", "name": "Alpha, One", "state": "ok", "http": 200, "latency_ms": 120,
                 "bitrate_kbps": 2400, "variants": [{"bandwidth": 2400000, "resolution": "1280x720"}],
                 "segments": 6, "live": True, "checked_at": "2026-10-31T00:05:00Z"},
        "bbb2": {"id": "bbb2", "name": "Beta Two", "state": "ok", "http": 200, "latency_ms": 300,
                 "bitrate_kbps": 800, "variants": [{"bandwidth": 800000, "resolution": "640x360"}],
                 "segments": 5, "live": True, "checked_at": "2026-10-31T00:05:00Z"},
        "ccc3": {"id": "ccc3", "name": "Gamma Three", "state": "offline", "http": 403, "latency_ms": 60,
                 "bitrate_kbps": None, "variants": [], "segments": 0, "live": False,
                 "checked_at": "2026-10-31T00:05:00Z"},
    },
}

MASTER = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=464000,RESOLUTION=426x240,CODECS="avc1.42c01e,mp4a.40.2"
chunklist_b464000.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2628000,RESOLUTION=1920x1080,CODECS="avc1.640028,mp4a.40.2"
chunklist_b2628000.m3u8
"""


class TempRepo:
    """Patch the module-level paths used by every script."""

    def __init__(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        self.watch = self.path / "watch_list.json"
        self.status = self.path / "status.json"
        self.token = self.path / "token.json"
        common.save_json(self.watch, FIXTURE_WATCH)
        common.save_json(self.status, FIXTURE_STATUS)
        common.save_json(self.token, {"token": "w.1793478329.abc", "expires": 1793478329})
        self._saved: dict = {}

    def __enter__(self) -> "TempRepo":
        for attr, value in (("WATCH_LIST", self.watch), ("STATUS_FILE", self.status), ("TOKEN_FILE", self.token)):
            self._saved[attr] = getattr(common, attr)
            setattr(common, attr, value)
        self._env = mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "example/restream"})
        self._env.start()
        return self

    def __exit__(self, *exc) -> None:
        self._env.stop()
        for attr, value in self._saved.items():
            setattr(common, attr, value)
        self.tmp.cleanup()


class TestHelpers(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(common.slugify("Sony Sports  Ten 1"), "sony-sports-ten-1")
        self.assertEqual(common.slugify("UDP 228.2.2.2:1053 #1907"), "udp-228-2-2-2-1053-1907")
        self.assertEqual(common.slugify(""), "channel")
        self.assertEqual(common.slugify("★"), "channel")

    def test_m3u_sanitises_commas(self):
        self.assertEqual(common.m3u_attr('WWE, 24*7 "HD"'), "WWE 24*7 'HD'")
        self.assertEqual(common.m3u_attr(None), "")

    def test_build_m3u_is_two_lines_per_channel(self):
        text = common.build_m3u([
            {"tvg_id": "aaa1", "tvg_name": "Alpha, One", "group": "News", "name": "Alpha, One", "url": "https://x/y.m3u8"},
            {"tvg_id": "bbb2", "tvg_name": "Beta", "group": "Sports", "name": "Beta", "url": "https://x/z.m3u8"},
        ])
        lines = text.strip().split("\n")
        self.assertEqual(lines[0], "#EXTM3U")
        self.assertEqual(len(lines), 5)
        extinf = [line for line in lines if line.startswith("#EXTINF")]
        self.assertEqual(len(extinf), 2)
        for line in extinf:
            for attr in re.findall(r'="([^"]*)"', line):
                self.assertNotIn(",", attr, f"comma inside attribute: {line}")
            self.assertRegex(line, r"^#EXTINF:-1 tvg-id=\"[^\"]+\" tvg-name=\".*\" tvg-logo=\"[^\"]*\" group-title=\".*\",.+$")

    def test_play_url_token_query(self):
        url = common.build_play_url(CONFIG, "be7u", "w.1793478329.mkk")
        self.assertEqual(url, "http://103.151.60.162:2122/play/be7u/index.m3u8?token=w.1793478329.mkk")

    def test_play_url_without_auth(self):
        cfg = json.loads(json.dumps(CONFIG))
        cfg["auth_mode"] = "token-header"
        url = common.build_play_url(cfg, "be7u", "secret")
        self.assertTrue(url.endswith("/play/be7u/index.m3u8"))
        self.assertNotIn("secret", url)
        self.assertNotIn("token=", url)

    def test_absolute_template(self):
        cfg = json.loads(json.dumps(CONFIG))
        cfg["play_url_template"] = "https://cdn.example/smil:<NAME>.smil/playlist.m3u8"
        self.assertEqual(common.build_play_url(cfg, "news", ""),
                         "https://cdn.example/smil:news.smil/playlist.m3u8")

    def test_token_expiry(self):
        self.assertEqual(common.token_expiry("w.1793478329.mkkNkz93oHpmAmUESwlnG8"), 1793478329)
        self.assertIsNone(common.token_expiry("nope"))

    def test_pages_base(self):
        cfg = json.loads(json.dumps(CONFIG))
        cfg["site"]["pages_base"] = ""
        self.assertEqual(common.pages_base(cfg, "me/myrepo"), "https://me.github.io/myrepo")
        self.assertEqual(common.pages_base(cfg, "me/me.github.io"), "https://me.github.io")
        cfg["site"]["pages_base"] = "https://tv.example.com/"
        self.assertEqual(common.pages_base(cfg, "me/myrepo"), "https://tv.example.com")
        # an explicit pages_base (site served from /docs) always wins
        self.assertEqual(common.pages_base(CONFIG, "me/myrepo"),
                         (CONFIG["site"]["pages_base"] or "https://me.github.io/myrepo").rstrip("/"))

    def test_public_stream_url_priority(self):
        cfg = json.loads(json.dumps(CONFIG))
        cfg["proxy"]["base_url"] = ""
        self.assertTrue(common.public_stream_url(cfg, "aaa1", "tok").startswith("http://103."))
        cfg["proxy"]["base_url"] = "https://relay.example/"
        self.assertEqual(common.public_stream_url(cfg, "aaa1", "tok"), "https://relay.example/live/aaa1.m3u8")

    def test_classify_group(self):
        self.assertEqual(common.classify_group("Star Sports Select 1 HD"), "Sports")
        self.assertEqual(common.classify_group("Cartoon Network"), "Kids")
        self.assertEqual(common.classify_group("DBC NEWS"), "News")
        self.assertEqual(common.classify_group("UDP 228.2.2.2:1087 #2813"), "Other")

    def test_normalise_channels_keeps_groups_and_vanished_channels(self):
        previous = {"channels": [{"id": "old1", "name": "Old One", "group": "News", "online": True}]}
        payload = {"channels": [
            {"id": "n1", "name": "New One", "group": "Network", "online": True},
            {"id": "n2", "name": "New Two", "group": "", "online": False},
            {"id": "n1", "name": "Duplicate", "group": "X", "online": True},
        ]}
        channels = common.normalise_channels(payload, previous)
        by_id = {c["id"]: c for c in channels}
        self.assertEqual(set(by_id), {"n1", "n2", "old1"})
        self.assertEqual(len(channels), 3)  # duplicate id collapsed
        self.assertEqual(by_id["n1"]["group"], "Network")   # panel group wins
        self.assertEqual(by_id["n2"]["group"], "Other")     # classified when the panel has none
        self.assertFalse(by_id["old1"]["online"])           # vanished channels stay, marked offline
        self.assertEqual(channels[0]["id"], "n1")           # online channels sort first

    def test_parse_variants(self):
        variants = health_check.parse_variants(MASTER)
        self.assertEqual(len(variants), 2)
        self.assertEqual(variants[-1]["bandwidth"], 2628000)
        self.assertEqual(variants[-1]["resolution"], "1920x1080")


class TestSiteBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = TempRepo()
        cls.ctx.__enter__()
        cls.out = cls.ctx.path / "docs"
        with mock.patch.object(sys, "argv", ["build_site.py", "--out", str(cls.out), "--quiet"]):
            build_site.main()

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)

    def test_required_files(self):
        for name in ("index.html", "playlist.m3u", ".nojekyll", "status.json", "watch_list.json",
                     "data/channels.json", "assets/site.css", "assets/site.js", "assets/favicon.svg",
                     "404.html", "ch/aaa1.html", "playlist/aaa1.m3u"):
            self.assertTrue((self.out / name).exists(), f"missing {name}")

    def test_playlists(self):
        text = (self.out / "playlist.m3u").read_text(encoding="utf-8")
        lines = text.strip().split("\n")
        self.assertEqual(lines[0], "#EXTM3U")
        self.assertEqual(len(lines), 1 + 2 * 3)
        for line in lines:
            if line.startswith("#EXTINF"):
                for attr in re.findall(r'="([^"]*)"', line):
                    self.assertNotIn(",", attr)

    def test_dashboard_lists_every_channel(self):
        index = (self.out / "index.html").read_text(encoding="utf-8")
        for channel in FIXTURE_WATCH["channels"]:
            self.assertIn(f'data-id="{channel["id"]}"', index)
            self.assertIn(f'href="ch/{channel["id"]}.html"', index)
        self.assertNotIn("{{", index, "unrendered template placeholder left in index.html")

    def test_player_pages(self):
        page = (self.out / "ch" / "aaa1.html").read_text(encoding="utf-8")
        self.assertIn("hls.js", page)
        self.assertIn('id="video"', page)
        self.assertIn("play/aaa1/index.m3u8", page)
        self.assertNotIn("{{", page)

    def test_internal_links_resolve(self):
        """Every relative href/src in the generated site must exist on disk."""
        broken = []
        for page in [self.out / "index.html", *sorted((self.out / "ch").glob("*.html"))]:
            text = page.read_text(encoding="utf-8")
            for target in re.findall(r'(?:href|src)="([^"]+)"', text):
                target = html.unescape(target)
                if target.startswith(("http://", "https://", "#", "mailto:", "data:")):
                    continue
                clean = target.split("#")[0].split("?")[0]
                if not clean:
                    continue
                resolved = (page.parent / clean).resolve()
                if not resolved.exists():
                    broken.append(f"{page.name}: {target}")
        self.assertEqual(broken, [], f"broken relative links: {broken}")

    def test_channels_json(self):
        data = json.loads((self.out / "data" / "channels.json").read_text(encoding="utf-8"))
        self.assertEqual(len(data["channels"]), 3)
        self.assertEqual(data["counts"]["live"], 2)
        self.assertTrue(all("stream_url" in c for c in data["channels"]))

    def test_public_watch_list_has_no_token(self):
        text = (self.out / "watch_list.json").read_text(encoding="utf-8")
        self.assertNotIn("w.1793478329.abc", text)


class TestWorkflowGeneration(unittest.TestCase):
    def _run(self, mode: str):
        ctx = TempRepo()
        ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)
        if True:
            cfg_path = ctx.path / "config.json"
            cfg = json.loads(json.dumps(CONFIG))
            cfg["mode"] = mode
            common.save_json(cfg_path, cfg)
            wf = ctx.path / "workflows"
            disabled = ctx.path / "workflows.disabled"
            with mock.patch.object(generate_workflows, "ROOT", ctx.path), \
                 mock.patch.object(sys, "argv", [
                     "generate_workflows.py", "--config", str(cfg_path),
                     "--workflows-dir", str(wf), "--disabled-dir", str(disabled)]):
                generate_workflows.main()
            return wf, disabled

    def test_selfhosted_writes_one_workflow_per_channel(self):
        wf, disabled = self._run("selfhosted")
        files = sorted(p.name for p in wf.glob("ch-*.yml"))
        self.assertEqual(files, ["ch-aaa1-alpha-one.yml", "ch-bbb2-beta-two.yml", "ch-ccc3-gamma-three.yml"])
        body = (wf / "ch-aaa1-alpha-one.yml").read_text(encoding="utf-8")
        self.assertIn("runs-on: [self-hosted, linux]", body)
        self.assertIn("timeout-minutes: 355", body)
        self.assertIn("cron:", body)
        self.assertIn("repository_dispatch", body)
        self.assertNotIn("{cid}", body)
        self.assertTrue((wf / "_matrix-rotation.yml").exists())
        self.assertFalse(list(disabled.glob("ch-*.yml")))

    def test_github_free_has_no_cron_per_channel(self):
        wf, _ = self._run("github-free")
        body = (wf / "ch-aaa1-alpha-one.yml").read_text(encoding="utf-8")
        self.assertNotIn("schedule:", body)
        self.assertIn("workflow_dispatch:", body)
        rotation = (wf / "_matrix-rotation.yml").read_text(encoding="utf-8")
        self.assertIn("cron:", rotation)
        self.assertIn("fromJson(needs.plan.outputs.channels)", rotation)

    def test_github_hosted_is_active_with_cron_and_kick(self):
        wf, disabled = self._run("github-hosted")
        files = sorted(p.name for p in wf.glob("ch-*.yml"))
        self.assertEqual(len(files), 3)
        body = (wf / "ch-aaa1-alpha-one.yml").read_text(encoding="utf-8")
        self.assertIn("runs-on: ubuntu-latest", body)
        self.assertIn("timeout-minutes: 355", body)
        self.assertIn("cron:", body)
        self.assertIn('"data/trigger/aaa1"', body)
        self.assertIn("workflow_dispatch", body)
        self.assertEqual(list(disabled.glob("ch-*.yml")), [])

    def test_portal_only_is_inert(self):
        wf, disabled = self._run("portal-only")
        self.assertEqual(list(wf.glob("ch-*.yml")), [])
        self.assertEqual(len(list(disabled.glob("ch-*.yml"))), 3)
        self.assertTrue((disabled / "README.md").exists())

    def test_rotation_plan_wraps(self):
        import rotation_plan
        with TempRepo() as ctx:
            out = ctx.path / "out.txt"
            with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(out)}), \
                 mock.patch.object(sys, "argv", ["rotation_plan.py", "--per-slot", "2", "--slot", "0"]):
                rotation_plan.main()
            text = out.read_text()
            self.assertIn('channels=[{"id":"aaa1"', text)


class TestReadme(unittest.TestCase):
    def test_generated_blocks_are_filled(self):
        with TempRepo() as ctx:
            readme = ctx.path / "README.md"
            readme.write_text(
                "<!-- AUTO:HEADER:START -->\n<!-- AUTO:HEADER:END -->\n\n"
                "<!-- AUTO:STATS:START -->\n<!-- AUTO:STATS:END -->\n\n"
                "<!-- AUTO:CATALOG:START -->\n<!-- AUTO:CATALOG:END -->\n",
                encoding="utf-8",
            )
            cfg = json.loads(json.dumps(CONFIG))
            cfg["site"]["pages_base"] = ""   # let the repo derive it
            with mock.patch.object(update_readme, "load_config", lambda *a, **k: cfg), \
                 mock.patch.object(sys, "argv", ["update_readme.py", "--readme", str(readme), "--branch", "main"]):
                self.assertEqual(update_readme.main(), 0)
            text = readme.read_text(encoding="utf-8")
            self.assertIn("capsule-render", text)
            self.assertIn("readme-typing-svg", text)
            self.assertIn("img.shields.io/badge/dynamic/json", text)
            self.assertIn("example.github.io/restream", text)
            self.assertIn("| 🟢 |", text)
            self.assertIn("Beta Two", text)
            self.assertNotIn("<!-- AUTO:STATS:START -->\n<!-- AUTO:STATS:END -->", text)


class TestHealthSeed(unittest.TestCase):
    def test_seed_writes_provisional_status(self):
        with TempRepo() as ctx:
            out = ctx.path / "seed.json"
            with mock.patch.object(sys, "argv", ["health_check.py", "--out", str(out), "--seed"]):
                self.assertEqual(health_check.main(), 0)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertTrue(data["provisional"])
            self.assertEqual(data["summary"]["total"], 3)
            self.assertEqual(data["summary"]["ok"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
