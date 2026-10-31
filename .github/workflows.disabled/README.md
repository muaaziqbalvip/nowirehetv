# Disabled restream workflows

`config.json -> mode` is currently **portal-only**, so no streaming job runs and
no GitHub Actions minutes are spent on video.

These are the fully generated per-channel workflows. To turn streaming on:

1. Edit `config.json`:
   * `"dest_mode": "rtmp"` + `rtmp.url` (nginx-rtmp / MediaMTX / any RTMP ingest), or
   * `"dest_mode": "hls-rehost"` (segments written per job), or
   * `"dest_mode": "proxy-only"` (cloud relay only, no ffmpeg fan-out).
2. Set `"mode"` to `"selfhosted"` (all channels, self-hosted runner) or
   `"github-free"` (rotation matrix inside the free minute budget).
3. Run `python3 scripts/generate_workflows.py` and commit the result.

The generator moves files between `.github/workflows/` (active) and
`.github/workflows.disabled/` (inert) automatically.
