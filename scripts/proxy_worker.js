/**
 * ============================================================================
 * EasyISP24 Restream - Cloudflare Worker HTTPS relay
 * ============================================================================
 * Deploy:  npx wrangler deploy            (see wrangler.toml)
 *
 * Routes
 *   GET /live/<id>.m3u8          master/media playlist, every line rewritten to
 *                                the local relay (https, CORS, fresh token)
 *   GET /live/<id>/<file>.m3u8   variant playlists inside a master
 *   GET /seg/<id>/<file>         media segments (.ts/.m4s/.aac/.key)
 *   GET /playlist.m3u            full M3U for IPTV apps (https links only)
 *   GET /ch/<id>.m3u             single-channel M3U
 *   GET /list                    /watch/list with the token stripped
 *   GET /health                  relay + panel diagnostic
 *
 * The panel token is read from KV (kept warm by the _token-refresh workflow
 * through the /admin/token route or automatically on the first request) and is
 * never returned to the browser.
 * ============================================================================
 */

const DEFAULTS = {
  SOURCE_BASE: "http://103.151.60.162:2122",
  LIST_PATH: "/watch/list",
  PLAY_TEMPLATE: "/play/<ID>/index.m3u8?token=<TOKEN>",
  USER_AGENT: "VLC/3.0.20",
  ALLOWED_ORIGINS: "*",
  TOKEN_FIELD: "token",
  TOKEN_KV_TTL: "3600",
};

const ID_RE = /^[A-Za-z0-9_-]{1,24}$/;
const SAFE_FILE_RE = /^[A-Za-z0-9._-]{1,96}$/;

const env = (e, key) => (e && e[key] !== undefined && e[key] !== "" ? e[key] : DEFAULTS[key]);

function corsHeaders(request, e) {
  const allowed = String(env(e, "ALLOWED_ORIGINS"));
  const origin = request.headers.get("Origin") || "*";
  const allow = allowed === "*" || allowed.split(",").map((s) => s.trim()).includes(origin) ? origin : allowed.split(",")[0];
  return {
    "Access-Control-Allow-Origin": allow || "*",
    "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
    "Access-Control-Allow-Headers": "Range, Content-Type, Accept",
    "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges",
    "Access-Control-Max-Age": "86400",
    Vary: "Origin",
  };
}

function json(data, status, extra) {
  return new Response(JSON.stringify(data, null, 2), {
    status: status || 200,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      ...(extra || {}),
    },
  });
}

function sanitizeId(raw) {
  return ID_RE.test(raw || "") ? raw : null;
}

function sanitizeFile(raw) {
  if (!raw || raw.includes("..") || !SAFE_FILE_RE.test(raw)) return null;
  return raw;
}

function sourceBase(e) {
  return String(env(e, "SOURCE_BASE")).replace(/\/+$/, "");
}

function playUrl(e, id, token) {
  const tpl = String(env(e, "PLAY_TEMPLATE"));
  const url = tpl
    .replace(/<ID>/g, encodeURIComponent(id))
    .replace(/<NAME>/g, encodeURIComponent(id))
    .replace(/<TOKEN>/g, encodeURIComponent(token || ""));
  return url.startsWith("http") ? url : sourceBase(e) + "/" + url.replace(/^\/+/, "");
}

/** Panel token: KV first, then one live fetch (cached back into KV). */
async function panelToken(e, { force = false } = {}) {
  const kv = e.TOKEN_KV;
  if (!force && kv) {
    const cached = await kv.get("token");
    if (cached) return cached;
  }
  if (!force && !kv && e.TOKEN) return e.TOKEN;

  const res = await fetch(sourceBase(e) + env(e, "LIST_PATH"), {
    headers: { "User-Agent": env(e, "USER_AGENT"), Accept: "application/json" },
    cf: { cacheTtl: 60, cacheEverything: false },
  });
  if (!res.ok) throw new Error(`panel list returned HTTP ${res.status}`);
  const data = await res.json();
  let node = data;
  for (const part of String(env(e, "TOKEN_FIELD")).split(".")) {
    node = node && typeof node === "object" ? node[part] : "";
  }
  const token = String(node || "");
  if (!token) throw new Error("token field not found in /watch/list response");
  if (kv) await kv.put("token", token, { expirationTtl: Number(env(e, "TOKEN_KV_TTL")) });
  return token;
}

async function panelList(e) {
  const res = await fetch(sourceBase(e) + env(e, "LIST_PATH"), {
    headers: { "User-Agent": env(e, "USER_AGENT"), Accept: "application/json" },
    cf: { cacheTtl: 120 },
  });
  if (!res.ok) throw new Error(`panel list returned HTTP ${res.status}`);
  return res.json();
}

/** Turn any playlist reference into a relay URL. */
function rewriteUri(uri, id, baseForRelative) {
  let target = String(uri || "").trim();
  if (!target) return target;
  if (/^https?:\/\//i.test(target)) {
    try {
      const parsed = new URL(target);
      target = parsed.pathname.split("/").pop() || "";
    } catch {
      return uri;
    }
  }
  const clean = target.split("?")[0].split("#")[0];
  const file = clean.split("/").pop();
  if (!file) return uri;
  if (/\.m3u8?$/i.test(file)) return `/live/${id}/${file}`;
  return `/seg/${id}/${file}`;
}

function rewritePlaylist(text, id) {
  const out = [];
  for (const rawLine of String(text).replace(/\r\n/g, "\n").split("\n")) {
    const line = rawLine.trimEnd();
    if (!line) continue;
    if (line.startsWith("#")) {
      // rewrite URI="..." inside EXT-X-KEY / EXT-X-MAP / EXT-X-MEDIA / EXT-X-I-FRAME-STREAM-INF
      if (/URI="/.test(line)) {
        out.push(line.replace(/URI="([^"]+)"/g, (_, uri) => `URI="${rewriteUri(uri, id)}"`));
      } else {
        out.push(line);
      }
      continue;
    }
    out.push(rewriteUri(line, id));
  }
  return out.join("\n") + "\n";
}

async function fetchPanelPlaylist(e, id, file, token) {
  const base = playUrl(e, id, token);
  let target;
  if (!file || /^index\.m3u8?$/i.test(file)) {
    target = base;
  } else {
    const dir = base.split("?")[0].replace(/[^/]*$/, "");
    const query = base.includes("?") ? "?" + base.split("?")[1] : "";
    target = dir + file + query;
  }
  return fetch(target, {
    headers: {
      "User-Agent": env(e, "USER_AGENT"),
      Accept: "*/*",
      ...(e.PANEL_REFERER ? { Referer: e.PANEL_REFERER } : {}),
    },
    cf: { cacheTtl: 0, cacheEverything: false },
  });
}

async function servePlaylist(request, e, id, file) {
  const cors = corsHeaders(request, e);
  for (let attempt = 0; attempt < 2; attempt++) {
    const token = await panelToken(e, { force: attempt > 0 });
    const res = await fetchPanelPlaylist(e, id, file, token);
    if (res.status === 403 && attempt === 0) continue; // token rotated - retry once
    if (!res.ok) {
      return new Response(`upstream ${res.status}: ${(await res.text()).slice(0, 200)}`, {
        status: res.status === 404 ? 404 : 502,
        headers: { ...cors, "content-type": "text/plain; charset=utf-8", "cache-control": "no-store" },
      });
    }
    const text = await res.text();
    if (!text.includes("#EXTM3U")) {
      return new Response("upstream did not return an HLS playlist", {
        status: 502,
        headers: { ...cors, "content-type": "text/plain; charset=utf-8" },
      });
    }
    return new Response(rewritePlaylist(text, id), {
      status: 200,
      headers: {
        ...cors,
        "content-type": "application/vnd.apple.mpegurl",
        "cache-control": "no-store, max-age=0",
        "x-relay": "easyisp24",
      },
    });
  }
  return new Response("upstream rejected the token twice", {
    status: 502,
    headers: { ...cors, "content-type": "text/plain; charset=utf-8" },
  });
}

async function serveSegment(request, e, id, file) {
  const cors = corsHeaders(request, e);
  const token = await panelToken(e);
  const base = playUrl(e, id, token);
  const dir = base.split("?")[0].replace(/[^/]*$/, "");
  const query = base.includes("?") ? "?" + base.split("?")[1] : "";
  const upstream = await fetch(dir + file + query, {
    headers: {
      "User-Agent": env(e, "USER_AGENT"),
      Accept: "*/*",
      ...(request.headers.get("Range") ? { Range: request.headers.get("Range") } : {}),
    },
    cf: { cacheTtl: 30, cacheEverything: true },
  });
  if (!upstream.ok) {
    return new Response(`segment ${upstream.status}`, {
      status: upstream.status === 404 ? 404 : 502,
      headers: { ...cors, "content-type": "text/plain" },
    });
  }
  const contentType =
    upstream.headers.get("content-type") ||
    (/\.(ts)$/i.test(file) ? "video/mp2t" : /\.(m4s|mp4)$/i.test(file) ? "video/mp4" : "application/octet-stream");
  return new Response(upstream.body, {
    status: upstream.status,
    headers: {
      ...cors,
      "content-type": contentType,
      "cache-control": "public, max-age=30",
      "accept-ranges": "bytes",
      "x-relay": "easyisp24",
    },
  });
}

async function serveM3U(request, e, onlyId) {
  const cors = corsHeaders(request, e);
  let data;
  try {
    data = await panelList(e);
  } catch (err) {
    return new Response(`#EXTM3U\n# panel error: ${err.message}\n`, {
      status: 502,
      headers: { ...cors, "content-type": "audio/x-mpegurl" },
    });
  }
  const origin = new URL(request.url).origin;
  const channels = (data.channels || []).filter((c) => !onlyId || c.id === onlyId);
  const lines = ["#EXTM3U"];
  for (const channel of channels) {
    const name = String(channel.name || channel.id).replace(/[\r\n,]/g, " ").replace(/"/g, "'");
    const group = String(channel.group || "").replace(/[\r\n,]/g, " ").replace(/"/g, "'");
    lines.push(`#EXTINF:-1 tvg-id="${channel.id}" tvg-name="${name}" tvg-logo="" group-title="${group}",${name}`);
    lines.push(`${origin}/live/${channel.id}.m3u8`);
  }
  if (onlyId && channels.length === 0) return new Response("channel not found", { status: 404, headers: cors });
  return new Response(lines.join("\n") + "\n", {
    headers: { ...cors, "content-type": "audio/x-mpegurl", "cache-control": "public, max-age=60" },
  });
}

export default {
  async fetch(request, envVars) {
    const e = envVars || {};
    const url = new URL(request.url);
    const cors = corsHeaders(request, e);
    const path = url.pathname.replace(/\/{2,}/g, "/");

    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
    if (request.method !== "GET" && request.method !== "HEAD") {
      return json({ error: "method not allowed" }, 405, cors);
    }

    try {
      if (path === "/" || path === "/index.html") {
        return json(
          {
            service: "easyisp24-restream relay",
            routes: [
              "/live/<id>.m3u8",
              "/live/<id>/<variant>.m3u8",
              "/seg/<id>/<segment>",
              "/playlist.m3u",
              "/ch/<id>.m3u",
              "/list",
              "/health",
            ],
            source_configured: Boolean(sourceBase(e)),
            kv_bound: Boolean(e.TOKEN_KV),
          },
          200,
          cors,
        );
      }

      if (path === "/health") {
        const started = Date.now();
        let panel = "unreachable";
        let channels = 0;
        try {
          const data = await panelList(e);
          panel = "ok";
          channels = (data.channels || []).length;
        } catch (err) {
          panel = `error: ${err.message}`;
        }
        return json(
          {
            relay: "ok",
            panel,
            channels,
            kv_bound: Boolean(e.TOKEN_KV),
            latency_ms: Date.now() - started,
            timestamp: new Date().toISOString(),
          },
          panel === "ok" ? 200 : 502,
          cors,
        );
      }

      if (path === "/list") {
        let data;
        try {
          data = await panelList(e);
        } catch (err) {
          return json({ error: err.message }, 502, cors);
        }
        const channels = (data.channels || []).map((c) => ({
          id: c.id,
          name: c.name,
          group: c.group || "",
          logo: c.logo || "",
          online: Boolean(c.online),
          stream: `${url.origin}/live/${c.id}.m3u8`,
        }));
        return json(
          {
            name: data.name,
            version: data.version,
            relay: url.origin,
            channel_count: channels.length,
            online_count: channels.filter((c) => c.online).length,
            fetched_at: new Date().toISOString(),
            channels,
          },
          200,
          { ...cors, "cache-control": "public, max-age=60" },
        );
      }

      if (path === "/playlist.m3u") return serveM3U(request, e, null);

      let match = path.match(/^\/ch\/([A-Za-z0-9_-]{1,24})\.m3u$/);
      if (match) return serveM3U(request, e, match[1]);

      match = path.match(/^\/live\/([A-Za-z0-9_-]{1,24})\.m3u8?$/i);
      if (match) {
        const id = sanitizeId(match[1]);
        if (!id) return json({ error: "bad channel id" }, 400, cors);
        return servePlaylist(request, e, id, "");
      }

      match = path.match(/^\/live\/([A-Za-z0-9_-]{1,24})\/(.+)$/i);
      if (match) {
        const id = sanitizeId(match[1]);
        const file = sanitizeFile(match[2]);
        if (!id || !file) return json({ error: "bad playlist path" }, 400, cors);
        return servePlaylist(request, e, id, file);
      }

      match = path.match(/^\/seg\/([A-Za-z0-9_-]{1,24})\/(.+)$/);
      if (match) {
        const id = sanitizeId(match[1]);
        const file = sanitizeFile(match[2]);
        if (!id || !file) return json({ error: "bad segment path" }, 400, cors);
        return serveSegment(request, e, id, file);
      }

      return json({ error: "not found", path }, 404, cors);
    } catch (err) {
      return json({ error: err.message || "relay error" }, 502, cors);
    }
  },
};
