// Public fMP4 playback; only the local publisher may upload camera data.
const MANIFEST_KEY = "observer/video/manifest.json";
const PREFIX = "observer/video/assets/";
const MAX_ASSET = 4 * 1024 * 1024;
const MAX_PLAYLIST = 64 * 1024;
const RETAIN_SEGMENTS = 12;
const PLAYLIST_GRACE_MS = 10_000;
const ORPHAN_TTL_MS = 30_000;
const CLEANUP_INTERVAL = 32;
const GENERATION = "(?:[a-f0-9]{32}|[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12})";
const INIT = new RegExp(`^init-(${GENERATION})\\.mp4$`);
const SEGMENT = new RegExp(`^segment-(${GENERATION})-(\\d{1,12})\\.m4s$`);

class VideoError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}
function json(value, status = 200) {
  return new Response(JSON.stringify(value), {status, headers: {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}});
}
function assetName(name) {
  let match = typeof name === "string" && name.match(INIT);
  if (match) return {name, generation: match[1], kind: "init"};
  match = typeof name === "string" && name.match(SEGMENT);
  if (match && Number.isSafeInteger(Number(match[2]))) return {name, generation: match[1], kind: "segment", sequence: Number(match[2])};
  throw new VideoError(400, "Invalid video asset name");
}
async function authorize(request, env) {
  if (typeof env.INGEST_TOKEN !== "string" || env.INGEST_TOKEN.length < 16) throw new VideoError(503, "Publisher authentication is not configured");
  const header = request.headers.get("Authorization") || "";
  const supplied = header.startsWith("Bearer ") ? header.slice(7) : "";
  const encoder = new TextEncoder();
  const values = await Promise.all([supplied, env.INGEST_TOKEN].map(async (value) => new Uint8Array(await crypto.subtle.digest("SHA-256", encoder.encode(value)))));
  let difference = 0;
  for (let index = 0; index < 32; index++) difference |= values[0][index] ^ values[1][index];
  if (difference) throw new VideoError(401, "Invalid publisher credentials");
}
async function bytes(request, maximum) {
  const declared = request.headers.get("Content-Length");
  if (declared !== null && (!/^\d+$/.test(declared) || Number(declared) > maximum)) throw new VideoError(413, "Video upload exceeds limit");
  const reader = request.body?.getReader();
  if (!reader) throw new VideoError(400, "Empty video upload");
  const chunks = [];
  let length = 0;
  while (true) {
    const result = await reader.read();
    if (result.done) break;
    length += result.value.byteLength;
    if (length > maximum) { await reader.cancel(); throw new VideoError(413, "Video upload exceeds limit"); }
    chunks.push(result.value);
  }
  if (!length) throw new VideoError(400, "Empty video upload");
  const value = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) { value.set(chunk, offset); offset += chunk.byteLength; }
  return value;
}
async function digest(value) {
  return [...new Uint8Array(await crypto.subtle.digest("SHA-256", value))].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}
async function stored(bucket, key) {
  const item = await bucket.get(key);
  return {item, value: item ? await item.json() : null};
}
function conditional(item) { return item ? {etagMatches: item.etag} : {etagDoesNotMatch: "*"}; }
async function info(bucket, name) {
  return typeof bucket.head === "function" ? bucket.head(PREFIX + name) : bucket.get(PREFIX + name);
}
function stamp(text) {
  if (!/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|\+00:?00)$/.test(text)) throw new VideoError(400, "Invalid segment capture timestamp");
  const value = Date.parse(text);
  if (!Number.isFinite(value) || new Date(value).toISOString().slice(0, 19) !== text.slice(0, 19)) throw new VideoError(400, "Invalid segment capture timestamp");
  return value;
}
function parsePlaylist(text) {
  const lines = text.trim().split(/\r?\n/);
  if (lines[0] !== "#EXTM3U" || lines.length > 160 || lines.some((line) => line.length > 2048)) throw new VideoError(400, "Invalid media playlist");
  let init = null;
  let duration = null;
  let captured = null;
  let mediaSequence = null;
  let targetDuration = null;
  const segments = [];
  for (const line of lines.slice(1)) {
    if (!line) continue;
    if (line.startsWith("#EXT-X-MAP:")) {
      const match = line.match(/^#EXT-X-MAP:URI="([^"]+)"$/);
      if (!match || init) throw new VideoError(400, "Invalid initialization reference");
      init = assetName(match[1]);
      if (init.kind !== "init") throw new VideoError(400, "Invalid initialization reference");
    } else if (line.startsWith("#EXTINF:")) {
      const match = line.match(/^#EXTINF:(\d+(?:\.\d+)?),[^\r\n]*$/);
      if (!match || duration !== null || Number(match[1]) <= 0 || Number(match[1]) > 10) throw new VideoError(400, "Invalid segment duration");
      duration = Number(match[1]);
    } else if (line.startsWith("#EXT-X-PROGRAM-DATE-TIME:")) {
      if (captured !== null) throw new VideoError(400, "Duplicate segment timestamp");
      captured = stamp(line.slice(25));
    } else if (line.startsWith("#EXT-X-MEDIA-SEQUENCE:")) {
      const value = line.slice(22);
      if (mediaSequence !== null || !/^\d{1,12}$/.test(value)) throw new VideoError(400, "Invalid media sequence");
      mediaSequence = Number(value);
    } else if (line.startsWith("#EXT-X-TARGETDURATION:")) {
      const value = line.slice(22);
      if (targetDuration !== null || !/^\d{1,2}$/.test(value) || Number(value) < 1 || Number(value) > 10) throw new VideoError(400, "Invalid target duration");
      targetDuration = Number(value);
    } else if (/^#EXT-X-VERSION:[6-9]$/.test(line) || ["#EXT-X-INDEPENDENT-SEGMENTS", "#EXT-X-ENDLIST", "#EXT-X-DISCONTINUITY"].includes(line)) {
      continue;
    } else if (line.startsWith("#")) {
      throw new VideoError(400, "Unsupported media playlist tag");
    } else {
      const segment = assetName(line);
      if (segment.kind !== "segment" || !init || segment.generation !== init.generation || duration === null || captured === null) throw new VideoError(400, "Incomplete segment reference");
      const previous = segments.at(-1);
      if (previous && (segment.sequence !== previous.sequence + 1 || captured < previous.end - 2)) throw new VideoError(400, "Segments must advance in sequence and capture time");
      segments.push({...segment, captured, end: captured + duration * 1000, duration});
      duration = captured = null;
      if (segments.length > 12) throw new VideoError(400, "Too many segment references");
    }
  }
  if (!init || !segments.length || duration !== null || captured !== null || mediaSequence !== segments[0].sequence || targetDuration === null || segments.some((segment) => Math.round(segment.duration) > targetDuration)) throw new VideoError(400, "Incomplete media playlist");
  const end = segments.at(-1).end;
  if (end > Date.now() + 2000) throw new VideoError(400, "Future capture timestamp");
  return {init, segments, generation: init.generation, sequence: segments.at(-1).sequence, captured_at: new Date(end).toISOString()};
}

async function orphanCandidates(env, retained, cursor) {
  // A bounded sweep belongs to occasional playlist commits, not every segment.
  // R2's object metadata replaces the former contended global upload ledger.
  if (typeof env.BUCKET.list !== "function") return {keys: [], cursor: null};
  try {
    const page = await env.BUCKET.list({prefix: PREFIX, limit: 128, include: ["customMetadata"], ...(cursor ? {cursor} : {})});
    const protectedNames = new Set(retained);
    const cutoff = Date.now() - ORPHAN_TTL_MS;
    const keys = page.objects.filter((item) => {
      const uploaded = Date.parse(item.customMetadata?.uploaded_at || item.uploaded);
      return Number.isFinite(uploaded) && uploaded <= cutoff && !protectedNames.has(item.key.slice(PREFIX.length));
    }).map((item) => item.key);
    return {keys, cursor: page.truncated ? page.cursor : null};
  } catch { return {keys: [], cursor: cursor || null}; }
}
function playlistReferences(value) {
  if (Array.isArray(value?.references)) return value.references;
  if (!value?.text) return [];
  // Migration from manifests written before explicit references were stored.
  const parsed = parsePlaylist(value.text);
  return [parsed.init.name, ...parsed.segments.map((segment) => segment.name)];
}
async function uploadAsset(request, env, name) {
  const asset = assetName(name);
  if (request.headers.get("Content-Type")?.split(";")[0].trim().toLowerCase() !== "video/mp4") throw new VideoError(415, "Expected video/mp4");
  const body = await bytes(request, MAX_ASSET);
  const sha256 = await digest(body);
  const manifest = asset.kind === "segment" ? (await stored(env.BUCKET, MANIFEST_KEY)).value : null;
  if (asset.kind === "segment" && asset.generation === manifest?.generation && asset.sequence <= manifest.sequence) {
    const existing = await info(env.BUCKET, name);
    if (!existing) throw new VideoError(409, "Segment sequence has already passed");
    if (existing.customMetadata?.sha256 !== sha256) throw new VideoError(409, "Video asset identity is immutable");
    return json({ok: true, name, bytes: body.byteLength});
  }
  // Fresh assets need one conditional write (and the segment rollback read).
  // Different fragments have no shared writable object and can upload in parallel.
  const saved = await env.BUCKET.put(PREFIX + name, body, {onlyIf: {etagDoesNotMatch: "*"}, httpMetadata: {contentType: "video/mp4", cacheControl: "public, max-age=60, immutable"}, customMetadata: {sha256, uploaded_at: new Date(Date.now()).toISOString()}});
  if (!saved) {
    const existing = await info(env.BUCKET, name);
    if (existing?.customMetadata?.sha256 !== sha256) throw new VideoError(409, "Video asset identity is immutable");
  }
  if (asset.kind === "segment" && asset.sequence > 0 && asset.sequence % CLEANUP_INTERVAL === 0) {
    // Failed playlist publication must not leave an ever-growing orphan set.
    // This infrequent sweep needs no shared write or upload-ledger lock.
    const cleanup = await orphanCandidates(env, manifest?.retained || [], null);
    if (cleanup.keys.length) { try { await env.BUCKET.delete(cleanup.keys); } catch { /* Retry on a later sweep. */ } }
  }
  return json({ok: true, name, bytes: body.byteLength});
}
async function uploadPlaylist(request, env) {
  if (request.headers.get("Content-Type")?.split(";")[0].trim().toLowerCase() !== "application/vnd.apple.mpegurl") throw new VideoError(415, "Expected an HLS media playlist");
  const body = await bytes(request, MAX_PLAYLIST);
  let text;
  try { text = new TextDecoder("utf-8", {fatal: true}).decode(body); } catch { throw new VideoError(400, "Invalid UTF-8 playlist"); }
  const parsed = parsePlaylist(text);
  const fpsHeader = request.headers.get("X-HoloSpeed-Target-Fps") ?? "60";
  if (!/^\d{1,3}(?:\.\d{1,6})?$/.test(fpsHeader) || Number(fpsHeader) < 1 || Number(fpsHeader) > 240) throw new VideoError(400, "Invalid target frame rate");
  const sha256 = await digest(body);
  const previous = await stored(env.BUCKET, MANIFEST_KEY);
  if (previous.value) {
    if (previous.value.sha256 === sha256) return json({ok: true, captured_at: previous.value.captured_at, updated_at: previous.value.updated_at});
    if (Date.parse(parsed.captured_at) <= Date.parse(previous.value.captured_at) || (parsed.generation === previous.value.generation && parsed.sequence <= previous.value.sequence)) throw new VideoError(409, "Stale video playlist");
  }
  const references = [parsed.init.name, ...parsed.segments.map((segment) => segment.name)];
  const available = await Promise.all(references.map((name) => info(env.BUCKET, name)));
  if (available.some((item) => !item)) throw new VideoError(409, "Upload every referenced video asset before its playlist");
  const segments = [...parsed.segments.map((segment) => segment.name).reverse(), ...(previous.value?.retained || []).filter((name) => SEGMENT.test(name))].filter((name, index, items) => items.indexOf(name) === index).slice(0, RETAIN_SEGMENTS);
  const now = Date.now();
  const grace = [
    ...(previous.value ? [{references: playlistReferences(previous.value), expires_at: now + PLAYLIST_GRACE_MS}] : []),
    ...(previous.value?.grace || []).filter((item) => item.expires_at > now),
  ].slice(0, 4);
  const retained = [...new Set([parsed.init.name, ...segments, ...segments.map((name) => `init-${assetName(name).generation}.mp4`), ...grace.flatMap((item) => item.references)])];
  const sweep = !previous.value || parsed.generation !== previous.value.generation || Math.floor(parsed.sequence / CLEANUP_INTERVAL) > Math.floor(previous.value.sequence / CLEANUP_INTERVAL);
  const cleanup = sweep ? await orphanCandidates(env, retained, previous.value?.cleanup_cursor) : {keys: [], cursor: previous.value?.cleanup_cursor || null};
  const value = {text, sha256, generation: parsed.generation, sequence: parsed.sequence, captured_at: parsed.captured_at, updated_at: new Date(now).toISOString(), target_fps: Number(fpsHeader), references, retained, grace, cleanup_cursor: cleanup.cursor};
  if (!await env.BUCKET.put(MANIFEST_KEY, JSON.stringify(value), {onlyIf: conditional(previous.item), httpMetadata: {contentType: "application/json", cacheControl: "no-store"}})) throw new VideoError(409, "A newer playlist was committed first");
  const obsolete = [...new Set([...cleanup.keys, ...(previous.value?.retained || []).filter((name) => !retained.includes(name)).map((name) => PREFIX + name)])];
  if (obsolete.length) { try { await env.BUCKET.delete(obsolete); } catch { /* One batched cleanup must not lose a committed playlist. */ } }
  return json({ok: true, captured_at: value.captured_at, updated_at: value.updated_at});
}

export async function handleVideo(request, env) {
  const url = new URL(request.url);
  if (!url.pathname.startsWith("/api/live/")) return null;
  const name = url.pathname.slice("/api/live/".length);
  const upload = name === "upload";
  if (request.method !== (upload ? "POST" : "GET")) return json({error: "Method not allowed"}, 405);
  try {
    if (!env.BUCKET) throw new VideoError(503, "Video storage is unavailable");
    if (upload) {
      await authorize(request, env);
      if ([...url.searchParams.keys()].length !== 1 || !url.searchParams.has("name")) throw new VideoError(400, "Supply one video asset name");
      const filename = url.searchParams.get("name");
      return filename === "index.m3u8" ? await uploadPlaylist(request, env) : await uploadAsset(request, env, filename);
    }
    const manifest = (await stored(env.BUCKET, MANIFEST_KEY)).value;
    if (name === "status") {
      const age = manifest ? Date.now() - Date.parse(manifest.captured_at) : null;
      return json({status: !manifest ? "waiting" : age >= -2000 && age < 15000 ? "live" : "stale", captured_at: manifest?.captured_at ?? null, updated_at: manifest?.updated_at ?? null, playlist_url: "/api/live/index.m3u8", target_fps: manifest?.target_fps ?? null, generation: manifest?.generation ?? null, sequence: manifest?.sequence ?? null});
    }
    if (name === "index.m3u8") {
      if (!manifest) throw new VideoError(404, "No video playlist is available");
      return new Response(manifest.text, {headers: {"Content-Type": "application/vnd.apple.mpegurl", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}});
    }
    assetName(name);
    if (!manifest?.retained.includes(name)) throw new VideoError(404, "Video asset is unavailable");
    const item = await env.BUCKET.get(PREFIX + name);
    if (!item) throw new VideoError(404, "Video asset is unavailable");
    return new Response(item.body, {headers: {"Content-Type": "video/mp4", "Cache-Control": "public, max-age=60, immutable", "X-Content-Type-Options": "nosniff"}});
  } catch (error) {
    return json({error: error instanceof VideoError ? error.message : "Video request failed"}, error instanceof VideoError ? error.status : 500);
  }
}
