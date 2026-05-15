# Marketplace tools design (PR #2)

> **STATUS (as of PR #184 / 2026-05-15).** v1 shipped with **Polyhaven + AmbientCG** as the two backends, not Polyhaven + Sketchfab. The Sketchfab integration described below is **deferred to a future PR** (originally targeted for v1, rescoped during implementation because the AmbientCG branch was already wired and removing it would have been a regression). Read the Sketchfab sections in this file as a future-state design artefact, not as v1 normative contract. The shipped tool descriptions, API, and error envelopes for the `polyhaven` / `ambientcg` / `all` source paths are documented in [`docs/TOOLS.md`](../TOOLS.md). When Sketchfab lands, this design doc is the source of truth for how it should be wired.

## 1. Goal & scope

Two new bridge-side synthetic MCP tools — `marketplace_search` and `marketplace_import` — that let any MCP-compliant client browse public CC0 / CC-BY asset libraries and pull textures + HDRIs straight into a UE 5.7 project as `UTexture2D` assets, without leaving the editor session. Original v1 scope targeted Polyhaven (uniformly CC0) and Sketchfab (CC0 + CC-BY filtered server-side); shipped v1 swapped Sketchfab for AmbientCG (see STATUS note above). 3D model (.fbx/.glb) import is explicitly **out of scope for this PR** — the native side has no mesh-import wrapper yet, so models land in a later PR. Both tools are stdlib-only (`urllib.request` + `urllib.parse`) and add no Python runtime dependency.

> **Prior-art note.** The bridge already shipped a Polyhaven + AmbientCG implementation before this design landed (search `synthetic_marketplace_search` and `synthetic_marketplace_import` in `bridge/unreal_claude_mcp_bridge.py`, ~line 5040). The original recommendation here was to supersede AmbientCG with Sketchfab because AmbientCG's download artefacts are zipped multi-map archives that v1 cannot unpack cleanly. **Resolved during implementation by keeping AmbientCG with a clear `source_unsupported` envelope for the import path** (search-side AmbientCG works; import-side returns a descriptive error so the surface stays discoverable). Sketchfab integration is deferred — when it lands, it should be added as a third `source` value alongside `polyhaven` + `ambientcg`, not replace either.

> **Permission gate.** The bridge's auto-mode classifier previously rejected `Invoke-WebRequest` to `api.polyhaven.com` as "exfil scouting". The maintainer has since explicitly authorized "downloading plugins/textures/materials" (2026-05-13 session memory). Before merging this PR, add an explicit allow-rule in `~/.claude/settings.json` for outbound HTTPS to `api.polyhaven.com`, `dl.polyhaven.org`, `api.sketchfab.com`, and `media.sketchfab.com` so the gate doesn't fire mid-import.

## 2. Tool surface

### 2.1 `marketplace_search` — `inputSchema`

```json
{
  "type": "object",
  "properties": {
    "query":      { "type": "string",  "description": "Search keyword matched against asset name + tags + categories. Empty string returns the catalog tail (most-recent / most-downloaded). Default ''." },
    "backend":    { "type": "string",  "enum": ["polyhaven", "sketchfab", "all"], "description": "Default 'polyhaven'. 'all' fans out to both backends and concatenates results up to `limit`." },
    "asset_type": { "type": "string",  "enum": ["texture", "hdri", "model", "all"], "description": "Default 'texture'. 'model' returns metadata only in v1 — `marketplace_import` will refuse it with `not_implemented`." },
    "license":    { "type": "string",  "enum": ["cc0", "cc-by", "any"], "description": "Default 'cc0'. Polyhaven is uniformly CC0 so this filter is a no-op there; Sketchfab applies `license_user=cc0|cc-by`." },
    "limit":      { "type": "integer", "description": "Max results returned. Default 10, min 1, max 50." }
  }
}
```

### 2.2 `marketplace_import` — `inputSchema`

```json
{
  "type": "object",
  "properties": {
    "backend":         { "type": "string",  "enum": ["polyhaven", "sketchfab"], "description": "Required. Which backend the slug belongs to (returned by `marketplace_search`)." },
    "slug":            { "type": "string",  "description": "Required. Source-specific asset id. Polyhaven: slug like 'aerial_rocks_02'. Sketchfab: UUID like 'a1b2c3d4e5f6...'." },
    "asset_type":      { "type": "string",  "enum": ["texture", "hdri", "model"], "description": "Default 'texture'. 'model' returns `not_implemented` in v1." },
    "resolution":      { "type": "string",  "description": "Polyhaven only. One of the resolutions exposed for the asset (typically '1k', '2k', '4k', '8k'). Default '2k' for texture, '4k' for hdri. Sketchfab ignores this — it returns whatever resolution the uploader supplied." },
    "format":          { "type": "string",  "description": "Polyhaven only. File format. Default 'jpg' for texture, 'exr' for hdri. Other valid values per Polyhaven: 'png', 'hdr', 'tex'." },
    "dest_path":       { "type": "string",  "description": "UE content path under which the imported asset is created. Default '/Game/Marketplace/<backend>/<slug>'. Must start with '/Game'." },
    "dest_name":       { "type": "string",  "description": "Asset name. Default = sanitised slug." },
    "replace_existing":{ "type": "boolean", "description": "Default false. When true, an existing UE asset at dest_path/dest_name is overwritten." },
    "chain_import":    { "type": "boolean", "description": "Default false. When true, marketplace_import calls the native `import_texture` handler directly after download. When false (recommended), the response contains `temp_path` and the MCP client orchestrates the next `import_texture` call. The recommended path is false because it composes cleanly with the rest of the toolchain and keeps marketplace_import single-responsibility." }
  },
  "required": ["backend", "slug"]
}
```

### 2.3 Response shape

`marketplace_search` →

```json
{
  "ok": true,
  "query": "<echo>",
  "backend": "<echo>",
  "asset_type": "<echo>",
  "license": "<echo>",
  "limit": <echo>,
  "count": <int>,
  "results": [
    {
      "backend": "polyhaven|sketchfab",
      "slug": "<id>",
      "name": "<display name>",
      "asset_type": "texture|hdri|model",
      "license": "CC0|CC-BY",
      "license_url": "https://creativecommons.org/...",
      "thumbnail_url": "<https url>",
      "preview_url": "<https url, larger than thumbnail>",
      "tags": [...],
      "categories": [...],
      "description": "<text>",
      "available_resolutions": ["1k","2k","4k","8k"],   // polyhaven only
      "author": "<name>",
      "download_count": <int>
    }
  ],
  "partial_errors": [ "<backend>: <message>" ]
}
```

`marketplace_import` →

```json
{
  "ok": true,
  "backend": "<echo>",
  "slug": "<echo>",
  "asset_type": "<echo>",
  "resolution": "<echo or null>",
  "format": "<echo or null>",
  "license": "CC0|CC-BY",
  "downloaded_from": "<resolved download url>",
  "disk_path": "<absolute path to downloaded file>",
  "byte_count": <int>,
  "ue_asset_path": "<dest_path>/<dest_name> or null when chain_import=false",
  "import_result": { "...": "echo of native import_texture result, present only when chain_import=true" },
  "next_step_hint": "Call import_texture with source_path=<disk_path> dest_path=<dest_path> dest_name=<dest_name>"
}
```

### 2.4 Error codes (JSON-RPC envelope)

| Code | Symbol | When |
|---|---|---|
| `-32602` | `invalid_arguments` / `invalid_field` | Schema violations (wrong type, out-of-range limit, missing required slug, dest_path not under /Game, …). |
| `-32603` | `network_error` | DNS / TCP / TLS failure reaching the backend. |
| `-32603` | `http_error` | Backend returned 4xx/5xx (message includes status + URL). |
| `-32603` | `not_found` | Backend returned 200 but the slug is unknown (Sketchfab) or the catalog payload omits it (Polyhaven). |
| `-32603` | `resolution_unavailable` | Polyhaven exposes a list of resolutions; requested one isn't in it (message echoes the available list). |
| `-32603` | `format_unavailable` | Resolution exists but the requested format doesn't. |
| `-32603` | `auth_required` | Sketchfab download attempted with no `SKETCHFAB_API_TOKEN`. |
| `-32603` | `not_implemented` | `asset_type=model` (parked for PR #3). |
| `-32603` | `all_backends_failed` | `backend=all` and every backend errored — message concatenates per-backend reasons. |
| upstream | `ue_import_failed` | `chain_import=true` and the inner `import_texture` returned an error (code propagated). |

## 3. Polyhaven API

Polyhaven is the simpler backend. Uniformly CC0. No auth. Three endpoints in play:

### 3.1 List / search

```
GET https://api.polyhaven.com/assets?t=textures
GET https://api.polyhaven.com/assets?t=hdris
GET https://api.polyhaven.com/assets?t=models       # parked for v2
GET https://api.polyhaven.com/assets?search=<term>
```

Returns a JSON object keyed by slug:

```json
{
  "aerial_rocks_02": {
    "name": "Aerial Rocks 02",
    "type": 1,                 // 0=hdri, 1=texture, 2=model
    "date_published": 1685577600,
    "download_count": 18342,
    "thumbnail_url": "https://cdn.polyhaven.com/asset_img/thumbs/aerial_rocks_02.png",
    "tags": ["aerial","rocks","outdoor"],
    "categories": ["nature","rock"],
    "max_resolution": [8192, 8192]
  },
  "...": { ... }
}
```

The handler iterates entries, maps `type` to its string enum, and slices to `limit`.

### 3.2 Asset detail (optional — used for richer search results)

```
GET https://api.polyhaven.com/info/<slug>
```

Returns the same shape as one catalog entry plus `authors` and `description`.

### 3.3 Files endpoint (download URL resolver)

```
GET https://api.polyhaven.com/files/<slug>
```

Texture response shape:

```json
{
  "Diffuse": {
    "1k": { "jpg": { "url": "https://dl.polyhaven.org/.../aerial_rocks_02_diff_1k.jpg", "md5": "..." },
             "png": { "url": "..." } },
    "2k": { "jpg": {...}, "png": {...} },
    "4k": { ... },
    "8k": { ... }
  },
  "Normal": { ... },
  "Roughness": { ... },
  "Displacement": { ... },
  "AO": { ... }
}
```

v1 imports the diffuse map only. Full PBR multi-map import (Normal/Roughness/Displacement/AO ingested as a `UMaterialInstance` parameter set) is a v2 enhancement.

HDRI response shape:

```json
{
  "hdri": {
    "1k": { "exr": {"url":"..."}, "hdr": {"url":"..."} },
    "2k": { ... },
    "4k": { ... },
    "8k": { ... }
  }
}
```

**Default picks** — texture: `2k` `jpg`. HDRI: `4k` `exr`. Both can be overridden via the schema.

## 4. Sketchfab API

Anonymous browse is open; **download requires an API token** (Sketchfab account → Settings → Password & API → API Token).

### 4.1 Token storage convention

Lookup order:
1. Env var `SKETCHFAB_API_TOKEN`.
2. File at `~/.unreal-claude-mcp/sketchfab_token` (single-line, trimmed).
3. Absent → `marketplace_search` still works (anonymous browse); `marketplace_import` for `backend=sketchfab` returns `auth_required`.

The file form survives env-var unsetting and is the same convention several other "personal CLI" tools use.

### 4.2 Search

```
GET https://api.sketchfab.com/v3/search?type=models&q=<term>&license_user=cc0&downloadable=true&count=24
```

`license_user` valid values for free / open use: `cc0`, `cc-by`, `cc-by-sa`. Hard-code `cc0` for `license=cc0`; build `cc0,cc-by` (comma-separated) for `license=cc-by`; omit for `license=any`. Always pass `downloadable=true` so the result set only contains assets that can actually be pulled.

Response excerpt:

```json
{
  "results": [
    {
      "uid": "a1b2c3d4e5f60718",
      "name": "Desert Rock Formation",
      "license": { "slug": "cc0", "label": "CC0 — Public Domain", "url": "https://creativecommons.org/publicdomain/zero/1.0/" },
      "isDownloadable": true,
      "thumbnails": {
        "images": [
          {"url": "https://...thumb_256.jpg", "width": 256, "height": 256},
          {"url": "https://...preview_1024.jpg", "width": 1024, "height": 1024}
        ]
      },
      "user": { "username": "creator_handle" },
      "viewCount": 12345,
      "tags": [{"name": "rock"}, {"name": "desert"}]
    }
  ],
  "next": "https://api.sketchfab.com/v3/search?cursor=..."
}
```

### 4.3 Asset detail

```
GET https://api.sketchfab.com/v3/models/<uid>
```

Same shape as one search result + `description`, `embedUrl`, and `downloadCount`.

### 4.4 Download URL

```
GET https://api.sketchfab.com/v3/models/<uid>/download
Authorization: Token <SKETCHFAB_API_TOKEN>
```

Returns a **signed URL that expires** (typically 5 minutes). Must be fetched per-import — never cached:

```json
{
  "gltf":  { "url": "https://media.sketchfab.com/.../signed.zip?Signature=...&Expires=1747...", "size": 14238022, "expires": 1747600000 },
  "source": { ... },
  "usdz":  { ... }
}
```

v1 only surfaces `asset_type=texture` / `asset_type=hdri` for Sketchfab — and both are rare on Sketchfab (it's a model-first platform). The texture/HDRI surfaces remain wired for symmetry and forward-compat, but Sketchfab search results in v1 will mostly be filtered out by the asset-type filter. **The practical v1 user value of Sketchfab is search discoverability**; functional download for Sketchfab will come with model import in PR #3.

## 5. Bridge implementation outline

Both handlers live in `bridge/unreal_claude_mcp_bridge.py` immediately above the `SYNTHETIC_TOOLS = { ... }` dict (where the existing Polyhaven/AmbientCG code already sits). Pseudocode:

```python
_MARKETPLACE_USER_AGENT = "UnrealClaudeMCP/0.9 (+https://github.com/NAJEMWEHBE/UnrealClaudeMCP)"
_MARKETPLACE_TIMEOUT = 30

def _http_get_json(url: str, headers: dict | None = None) -> tuple[dict | list | None, dict | None]:
    import urllib.request, urllib.error
    hdrs = {"User-Agent": _MARKETPLACE_USER_AGENT, "Accept": "application/json"}
    if headers: hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=_MARKETPLACE_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", "replace")), None
    except urllib.error.HTTPError as e:
        return None, {"code": -32603, "message": f"http_error: status={e.code} url={url}: {e.reason}"}
    except urllib.error.URLError as e:
        return None, {"code": -32603, "message": f"network_error: url={url}: {e.reason}"}
    except Exception as e:
        return None, {"code": -32603, "message": f"fetch_failed: url={url}: {e}"}


def _sketchfab_token() -> str | None:
    tok = os.environ.get("SKETCHFAB_API_TOKEN")
    if tok: return tok.strip()
    p = os.path.expanduser("~/.unreal-claude-mcp/sketchfab_token")
    if os.path.isfile(p):
        with open(p, "r", encoding="utf-8") as f:
            return f.read().strip() or None
    return None


def _search_polyhaven(query, asset_type, limit):
    import urllib.parse
    t_map = {"texture": "textures", "hdri": "hdris", "model": "models"}
    parts = []
    if asset_type != "all":
        parts.append(f"t={t_map[asset_type]}")
    if query:
        parts.append(f"search={urllib.parse.quote(query)}")
    url = "https://api.polyhaven.com/assets" + ("?" + "&".join(parts) if parts else "")
    data, err = _http_get_json(url)
    if err: return None, err
    inv = {0: "hdri", 1: "texture", 2: "model"}
    out = []
    for slug, meta in (data or {}).items():
        out.append({
            "backend": "polyhaven",
            "slug": slug,
            "name": meta.get("name") or slug,
            "asset_type": inv.get(meta.get("type"), "unknown"),
            "license": "CC0",
            "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
            "thumbnail_url": meta.get("thumbnail_url") or "",
            "preview_url": meta.get("thumbnail_url") or "",
            "tags": meta.get("tags") or [],
            "categories": meta.get("categories") or [],
            "description": "",
            "available_resolutions": _resolution_strings(meta.get("max_resolution")),
            "author": ", ".join((meta.get("authors") or {}).keys()),
            "download_count": meta.get("download_count") or 0,
        })
        if len(out) >= limit: break
    return out, None


def _search_sketchfab(query, asset_type, license_filter, limit):
    import urllib.parse
    license_map = {"cc0": "cc0", "cc-by": "cc0,cc-by", "any": ""}
    parts = ["type=models", "downloadable=true", f"count={min(24, max(1, limit))}"]
    if query: parts.append(f"q={urllib.parse.quote(query)}")
    lf = license_map.get(license_filter, "cc0")
    if lf: parts.append(f"license_user={lf}")
    url = "https://api.sketchfab.com/v3/search?" + "&".join(parts)
    data, err = _http_get_json(url)
    if err: return None, err
    out = []
    for r in (data or {}).get("results", []):
        thumbs = (r.get("thumbnails") or {}).get("images") or []
        thumbs = sorted(thumbs, key=lambda x: x.get("width") or 0)
        thumb = thumbs[0]["url"] if thumbs else ""
        preview = thumbs[-1]["url"] if thumbs else thumb
        lic = r.get("license") or {}
        out.append({
            "backend": "sketchfab",
            "slug": r.get("uid") or "",
            "name": r.get("name") or "",
            "asset_type": "model",  # Sketchfab is model-only
            "license": (lic.get("slug") or "").upper() or "CC0",
            "license_url": lic.get("url") or "",
            "thumbnail_url": thumb,
            "preview_url": preview,
            "tags": [t.get("name") for t in (r.get("tags") or []) if t.get("name")],
            "categories": [],
            "description": r.get("description") or "",
            "available_resolutions": [],
            "author": (r.get("user") or {}).get("username") or "",
            "download_count": r.get("downloadCount") or 0,
        })
        if len(out) >= limit: break
    return out, None


def synthetic_marketplace_search(req_id, args: dict) -> dict:
    # 1. Validate (backend, query, asset_type, license, limit) — return -32602 on any mismatch.
    # 2. Dispatch: backend in ("polyhaven","all") -> _search_polyhaven; backend in ("sketchfab","all") -> _search_sketchfab.
    # 3. Concat, slice to limit, attach partial_errors[], _wrap_tool_result(...).
    ...


def synthetic_marketplace_import(req_id, args: dict) -> dict:
    # 1. Validate (backend, slug required; asset_type/resolution/format/dest_path/dest_name optional).
    # 2. If asset_type == "model": return -32603 not_implemented (PR #3 territory).
    # 3. Resolve download URL:
    #    - Polyhaven: GET /files/<slug>, pick (asset_type, resolution, format) -> url.
    #    - Sketchfab: requires _sketchfab_token(); GET /v3/models/<uid>/download with Authorization header -> url.
    # 4. Compute disk_path = <host-project>/Saved/Marketplace/<backend>/<slug>/<file>.
    #    Create parent dirs. _http_download(url, disk_path).
    # 5. If chain_import: call_ue("import_texture", {source_path: disk_path, dest_path, dest_name, replace_existing, automated: True, save: True}).
    # 6. Build response with disk_path, byte_count, ue_asset_path?, next_step_hint, license.
    ...
```

**Important: the bridge does not invoke MCP tools directly.** It exposes them. So `chain_import=true` is implemented by calling `call_ue("import_texture", {...})` directly — the same path every existing bridge synthetic uses (see `synthetic_bulk_compile_blueprints` for the canonical pattern). When `chain_import=false`, the response includes `next_step_hint` and the MCP client decides whether to invoke `import_texture` as its next `tools/call`. **Recommend defaulting `chain_import=false`** — keeps the tool single-responsibility and composes cleanly.

## 6. Disk layout

Resolve the host project's `Saved/` dir via the existing convention (the bridge does not know it directly; the maintainer wires it as an env var). Add:

```
UCMCP_HOST_PROJECT  (absolute path to .uproject's parent — e.g. F:\ax plug in\HDMediaVirtualStudio)
```

If unset, fall back to `os.path.expanduser("~/.unreal-claude-mcp/marketplace/")`.

```
<host-project>/Saved/Marketplace/
├── .cache/
│   ├── polyhaven/
│   │   └── <sha1(query + asset_type + limit)>.json   # 1-hour TTL on mtime
│   └── sketchfab/
│       └── <sha1(...)>.json
├── polyhaven/
│   └── aerial_rocks_02/
│       ├── aerial_rocks_02_diff_2k.jpg
│       └── meta.json                                 # {license, downloaded_from, byte_count, ts}
└── sketchfab/
    └── a1b2c3d4e5f60718/
        └── (model archive once PR #3 lands)
```

`meta.json` per slug preserves license + provenance — critical for CC-BY attribution audits downstream.

## 7. Manifest + TOOLS sync

Three sites must be edited in lockstep — the `tests/test_manifest_sync.py` suite catches drift.

### 7.1 `bridge/unreal_claude_mcp_bridge.py` (`TOOLS` list)

```python
{
    "name": "marketplace_search",
    "description": "Search public CC0 / CC-BY asset libraries (Polyhaven, Sketchfab) for textures / HDRIs / models matching a keyword. SYNTHETIC bridge-side handler — fetches each backend's public JSON catalog via plain HTTPS. Polyhaven is uniformly CC0 (no auth, no API key). Sketchfab requires an API token only for `marketplace_import`; search itself is anonymous. Pair with marketplace_import to download a chosen result. Models surface in search results but marketplace_import returns not_implemented for asset_type=model in v1 (parked for PR #3).",
    "inputSchema": { /* §2.1 */ },
},
{
    "name": "marketplace_import",
    "description": "Download an asset from a public CC0 / CC-BY library (Polyhaven, Sketchfab) and stage it on disk under <host-project>/Saved/Marketplace/. SYNTHETIC bridge-side handler. For Polyhaven textures + HDRIs, optionally chain into the native `import_texture` handler (chain_import=true) to round-trip into the project as a UTexture2D. For Sketchfab models, returns not_implemented in v1. Sketchfab download requires SKETCHFAB_API_TOKEN env var or ~/.unreal-claude-mcp/sketchfab_token file.",
    "inputSchema": { /* §2.2 */ },
},
```

### 7.2 `UnrealClaudeMCP/Resources/mcp_manifest.json`

```json
{
  "name": "marketplace_search",
  "description": "Search public CC0 / CC-BY asset libraries (Polyhaven, Sketchfab) for textures / HDRIs / models matching a keyword. SYNTHETIC bridge-side handler.",
  "params": {
    "query":      "string (optional, default '') - keyword matched against name + tags + categories",
    "backend":    "string (optional, default 'polyhaven') - one of polyhaven|sketchfab|all",
    "asset_type": "string (optional, default 'texture') - one of texture|hdri|model|all",
    "license":    "string (optional, default 'cc0') - one of cc0|cc-by|any",
    "limit":      "int (optional, default 10, min 1, max 50) - max results"
  },
  "returns": { "ok": "bool", "count": "int", "results": "array of asset descriptor objects", "partial_errors": "array of string (present only when some backend errored)" }
},
{
  "name": "marketplace_import",
  "description": "Download an asset from a public CC0 / CC-BY library and stage it under <host-project>/Saved/Marketplace/. SYNTHETIC bridge-side handler. Optionally chains into native import_texture (chain_import=true).",
  "params": {
    "backend":          "string (required) - polyhaven|sketchfab",
    "slug":             "string (required) - source-specific asset id from marketplace_search",
    "asset_type":       "string (optional, default 'texture') - texture|hdri|model (model returns not_implemented in v1)",
    "resolution":       "string (optional, polyhaven only, default '2k' for texture / '4k' for hdri)",
    "format":           "string (optional, polyhaven only, default 'jpg' for texture / 'exr' for hdri)",
    "dest_path":        "string (optional, default '/Game/Marketplace/<backend>/<slug>') - must start with /Game",
    "dest_name":        "string (optional, default = sanitised slug)",
    "replace_existing": "bool (optional, default false)",
    "chain_import":     "bool (optional, default false) - when true, invoke import_texture immediately after download"
  },
  "returns": { "ok": "bool", "disk_path": "string", "byte_count": "int", "ue_asset_path": "string|null", "downloaded_from": "string", "license": "string", "import_result": "object (present only when chain_import=true)", "next_step_hint": "string" }
}
```

### 7.3 `docs/TOOLS.md`

Add two sections following the `find_unused_assets` template (see §3229 of `docs/TOOLS.md`). Required sub-headers per the existing convention: **(intro paragraph)**, **Bridge-side synthetic tool.**, **Params**, **Result**, **Errors (envelope-level):**, **Example — happy path**, **Example — error case**. Update the top-of-file synthetic-tool roll-call (currently lists 29 tools) to include both new names and bump the total tool count from 100 to 102.

### 7.4 `tests/conftest.py`

Bump `EXPECTED_TOOL_COUNT` from 100 to 102. The `test_manifest_sync.py` suite then auto-covers the new entries.

## 8. Test plan

All tests stdlib-only; **no real network in CI**.

### 8.1 `tests/test_marketplace_search.py`

- Monkey-patch `urllib.request.urlopen` to return a fixture buffer (canned Polyhaven `/assets` response, canned Sketchfab `/v3/search` response).
- Assert the constructed URL contains the expected query params (`search=`, `t=`, `license_user=`, `count=`).
- Assert the parsed result list shape matches the §2.3 contract (every result has `backend`, `slug`, `license`, `thumbnail_url`).
- Assert `limit` is honoured.
- Assert `backend=all` concatenates and slices.
- Assert `backend=all` with one backend failing yields `partial_errors[]` populated but `ok=true` if the other succeeded.
- Assert all-backends-failed yields `-32603 all_backends_failed`.
- Assert validation errors (`limit=0`, `limit=99`, `backend=foo`, `license=foo`) return `-32602`.

### 8.2 `tests/test_marketplace_import.py`

- Monkey-patch `urllib.request.urlopen` for both the metadata GET and the binary download GET.
- Use a `tmp_path` fixture for `UCMCP_HOST_PROJECT`.
- Assert the file lands at `<tmp_path>/Saved/Marketplace/polyhaven/<slug>/<expected filename>`.
- Assert `meta.json` sidecar contains license + downloaded_from + byte_count.
- Assert response includes `disk_path`, `byte_count`, `next_step_hint`, `license`.
- Assert `chain_import=true` calls `call_ue("import_texture", ...)` (monkey-patch `call_ue` to record args).
- Assert `asset_type=model` returns `-32603 not_implemented`.
- Assert `backend=sketchfab` without `SKETCHFAB_API_TOKEN` returns `-32603 auth_required`.
- Assert `resolution=99k` on Polyhaven returns `-32603 resolution_unavailable` echoing the available list.
- Assert `dest_path` not starting with `/Game` returns `-32602`.

### 8.3 `tests/test_manifest_sync.py`

No changes needed. After the manifest + TOOLS entries are added and `EXPECTED_TOOL_COUNT` is bumped, the existing drift tests auto-cover both new tools (name parity, count parity, required-field parity).

## 9. Open questions

1. **Sketchfab API token storage convention.** Recommend env var first, `~/.unreal-claude-mcp/sketchfab_token` second. Both documented in `docs/INSTALLATION.md` under a new "Marketplace integration" subsection. Token file should be `chmod 600` on POSIX (no-op on Windows — note this in the docs).
2. **Rate limit handling.** Polyhaven publishes no rate limit; treat as best-effort with no client-side throttle. Sketchfab anonymous: 1000 req/hour; authenticated: 5000 req/hour. v1 doesn't implement a rate limiter — if a 429 comes back, surface it as `http_error: status=429` and let the LLM client back off. v2 can add a token-bucket if usage patterns demand it.
3. **HTTPS verify mode.** Use system CAs (Python's default `ssl.create_default_context()` behaviour with `urllib.request.urlopen`). Do **not** add `ssl._create_unverified_context()` anywhere — that's a known foot-gun. Note that on Windows the Python distribution must have `certifi` or use OS cert store; the runbook in `docs/INSTALLATION.md` should call this out.
4. **Should `marketplace_import` auto-chain `import_texture`?** **Recommend default `chain_import=false`** for v1. Rationale: keeps the synthetic single-responsibility (download to disk); composes cleanly with the existing `import_texture` C++ handler; lets the LLM client decide whether to also import a Normal map, set a `dest_name`, or batch multiple slugs into one import. Auto-chain stays available as an opt-in flag for one-shot "give me an HDRI in my level" prompts. The MCP client orchestrates the next `tools/call` based on `next_step_hint` in the response.
5. **Sketchfab model surfacing in v1.** Search returns Sketchfab results but `marketplace_import` refuses `asset_type=model`. This is intentional — surfaces discoverability ("yes, that asset exists, here's the preview") without promising download capability that isn't wired. PR #3 lands the model-import handler and lifts the restriction.
6. **CC-BY attribution.** Polyhaven is uniformly CC0 (no attribution needed). Sketchfab CC-BY assets legally require attribution. The `meta.json` sidecar preserves `author` + `license` + `license_url` so a downstream attribution-roll-up tool can generate a `THIRD-PARTY-NOTICES.md` for the project. **Out of scope for this PR** — file a follow-up issue.

---

*Implementation order suggested: (1) `_http_get_json` + `_http_download` helpers, (2) `_search_polyhaven` + `_search_sketchfab`, (3) `synthetic_marketplace_search`, (4) `synthetic_marketplace_import`, (5) wire into `SYNTHETIC_TOOLS` + `TOOLS`, (6) manifest + TOOLS.md + conftest, (7) tests. Each step is independently testable; ship as one PR but commit in that order so revert is granular.*
