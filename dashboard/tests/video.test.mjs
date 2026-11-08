import test from "node:test";
import assert from "node:assert/strict";
import { handleVideo } from "../worker/video.mjs";

const TOKEN = "test-private-publisher-token-with-enough-entropy";
const ORIGIN = "https://observer.example";
const GENERATION = "dcf08302cded409ba4efb356b0cd18a6";
const OTHER = "e3a0d73131b945c38d042cb8817fbf9f";
const MP4 = new Uint8Array([0, 0, 0, 8, 102, 116, 121, 112]);
const ASSETS = "observer/video/assets/";

class FakeBucket {
  records = new Map();
  revision = 0;
  failNextManifest = false;
  manifestBarrier = null;
  operations = [];

  async get(key) {
    this.operations.push({method: "get", key});
    const entry = this.records.get(key);
    if (!entry) return null;
    const bytes = entry.bytes.slice();
    if (key === "observer/video/manifest.json" && this.manifestBarrier) {
      const barrier = this.manifestBarrier;
      if (--barrier.remaining === 0) { this.manifestBarrier = null; barrier.release(); }
      await barrier.wait;
    }
    return {etag: entry.etag, body: bytes, customMetadata: entry.customMetadata, uploaded: entry.uploaded,
      json: async () => JSON.parse(new TextDecoder().decode(bytes)),
      text: async () => new TextDecoder().decode(bytes),
      arrayBuffer: async () => bytes.buffer,
    };
  }
  async head(key) {
    this.operations.push({method: "head", key});
    const entry = this.records.get(key);
    return entry ? {etag: entry.etag, customMetadata: entry.customMetadata, uploaded: entry.uploaded} : null;
  }
  async put(key, value, options = {}) {
    this.operations.push({method: "put", key, options});
    const previous = this.records.get(key);
    if (options.onlyIf?.etagMatches && previous?.etag !== options.onlyIf.etagMatches) return null;
    if (options.onlyIf?.etagDoesNotMatch === "*" && previous) return null;
    if (key === "observer/video/manifest.json" && this.failNextManifest) { this.failNextManifest = false; return null; }
    const bytes = typeof value === "string" ? new TextEncoder().encode(value) : new Uint8Array(value);
    const etag = `revision-${++this.revision}`;
    this.records.set(key, {bytes: bytes.slice(), etag, customMetadata: options.customMetadata, uploaded: new Date(Date.now())});
    return {key, etag};
  }
  async delete(key) {
    this.operations.push({method: "delete", key});
    for (const name of Array.isArray(key) ? key : [key]) this.records.delete(name);
  }
  async list(options = {}) {
    this.operations.push({method: "list", ...options});
    const keys = [...this.records.keys()].filter((key) => key.startsWith(options.prefix || "")).sort();
    const offset = Number(options.cursor || 0);
    const selected = keys.slice(offset, offset + (options.limit || 1000));
    const objects = selected.map((key) => {const entry = this.records.get(key); return {key, etag: entry.etag, uploaded: entry.uploaded, customMetadata: entry.customMetadata, size: entry.bytes.byteLength};});
    const truncated = offset + selected.length < keys.length;
    return {objects, truncated, ...(truncated ? {cursor: String(offset + selected.length)} : {})};
  }
  raceNextManifestReads() {
    const barrier = {remaining: 2};
    barrier.wait = new Promise((resolve) => {barrier.release = resolve;});
    this.manifestBarrier = barrier;
  }
}

function env() { return {BUCKET: new FakeBucket(), INGEST_TOKEN: TOKEN}; }
function init(generation = GENERATION) { return `init-${generation}.mp4`; }
function segment(sequence, generation = GENERATION) { return `segment-${generation}-${String(sequence).padStart(9, "0")}.m4s`; }
function get(path) { return new Request(ORIGIN + path); }
function upload(name, body = MP4, {token = TOKEN, type = name === "index.m3u8" ? "application/vnd.apple.mpegurl" : "video/mp4", headers = {}} = {}) {
  return new Request(`${ORIGIN}/api/live/upload?name=${encodeURIComponent(name)}`, {method: "POST", body, headers: {"Content-Type": type, ...(token ? {Authorization: `Bearer ${token}`} : {}), ...headers}});
}
function playlist({generation = GENERATION, first = 0, count = 6, base = Date.now() - 3500, duration = 0.5} = {}) {
  const lines = ["#EXTM3U", "#EXT-X-VERSION:7", "#EXT-X-TARGETDURATION:1", `#EXT-X-MEDIA-SEQUENCE:${first}`, "#EXT-X-INDEPENDENT-SEGMENTS", `#EXT-X-MAP:URI="${init(generation)}"`];
  for (let offset = 0; offset < count; offset++) lines.push(`#EXTINF:${duration.toFixed(6)},`, `#EXT-X-PROGRAM-DATE-TIME:${new Date(base + offset * duration * 1000).toISOString()}`, segment(first + offset, generation));
  return lines.join("\n") + "\n";
}
async function assets(configuration, {generation = GENERATION, first = 0, count = 6} = {}) {
  assert.equal((await handleVideo(upload(init(generation)), configuration)).status, 200);
  for (let offset = 0; offset < count; offset++) assert.equal((await handleVideo(upload(segment(first + offset, generation)), configuration)).status, 200);
}
async function publish(configuration, options = {}, headers = {}) {
  await assets(configuration, options);
  const text = playlist(options);
  const response = await handleVideo(upload("index.m3u8", text, {headers}), configuration);
  return {text, response};
}

test("video waits honestly and publisher authentication is mandatory for writes", async () => {
  const configuration = env();
  assert.equal(await handleVideo(get("/api/unrelated"), configuration), null);
  const status = await (await handleVideo(get("/api/live/status"), configuration)).json();
  assert.deepEqual(status, {status: "waiting", captured_at: null, updated_at: null, playlist_url: "/api/live/index.m3u8", target_fps: null, generation: null, sequence: null});
  assert.equal((await handleVideo(get("/api/live/index.m3u8"), configuration)).status, 404);
  for (const token of [null, "wrong-credential"]) {
    const result = await handleVideo(upload(init(), MP4, {token}), configuration);
    assert.equal(result.status, 401);
    assert.ok(!(await result.text()).includes(TOKEN));
  }
  assert.equal(configuration.BUCKET.records.size, 0);
  assert.equal((await handleVideo(upload(init()), {...configuration, INGEST_TOKEN: "short"})).status, 503);
});

test("committed fMP4 references are playable and capture age derives from segment time", async () => {
  const configuration = env();
  const base = Date.now() - 4000;
  const {response, text} = await publish(configuration, {base}, {"X-HoloSpeed-Target-Fps": "59.94"});
  assert.equal(response.status, 200);
  assert.equal(await (await handleVideo(get("/api/live/index.m3u8"), configuration)).text(), text);
  const status = await (await handleVideo(get("/api/live/status"), configuration)).json();
  assert.equal(status.status, "live");
  assert.equal(status.target_fps, 59.94);
  assert.equal(status.generation, GENERATION);
  assert.equal(status.sequence, 5);
  assert.equal(status.captured_at, new Date(base + 3000).toISOString());
  const asset = await handleVideo(get(`/api/live/${segment(0)}`), configuration);
  assert.equal(asset.headers.get("Content-Type"), "video/mp4");
  assert.deepEqual(new Uint8Array(await asset.arrayBuffer()), MP4);
  assert.equal((await handleVideo(get(`/api/live/${init()}`), configuration)).status, 200);
});

test("playlist publication requires every referenced asset and does not expose uncommitted chunks", async () => {
  const configuration = env();
  await handleVideo(upload(init()), configuration);
  await handleVideo(upload(segment(0)), configuration);
  assert.equal((await handleVideo(get(`/api/live/${segment(0)}`), configuration)).status, 404);
  assert.equal((await handleVideo(upload("index.m3u8", playlist()), configuration)).status, 409);
  assert.equal((await handleVideo(get("/api/live/index.m3u8"), configuration)).status, 404);
  await assets(configuration);
  assert.equal((await handleVideo(upload("index.m3u8", playlist()), configuration)).status, 200);
});

test("asset identity is immutable while identical upload retries are harmless", async () => {
  const configuration = env();
  assert.equal((await handleVideo(upload(init()), configuration)).status, 200);
  assert.equal((await handleVideo(upload(init()), configuration)).status, 200);
  assert.equal((await handleVideo(upload(init(), new Uint8Array([1, 2, 3])), configuration)).status, 409);
  assert.deepEqual((await configuration.BUCKET.get(ASSETS + init())).body, MP4);
});

test("six parallel fragments upload without a shared storage write or hot-path coordination", async () => {
  const configuration = env();
  assert.equal((await handleVideo(upload(init()), configuration)).status, 200);
  const initialization = configuration.BUCKET.operations.splice(0);
  assert.ok(initialization.length <= 2, "A new initialization asset needs only a conditional PUT");
  const responses = await Promise.all(Array.from({length: 6}, (_, sequence) => handleVideo(upload(segment(sequence)), configuration)));
  assert.deepEqual(responses.map((response) => response.status), [200, 200, 200, 200, 200, 200]);
  const operations = configuration.BUCKET.operations;
  assert.ok(operations.length <= 12, "Normal fragments need at most a manifest GET and an immutable PUT each");
  const writes = operations.filter((operation) => operation.method === "put");
  assert.equal(writes.length, 6);
  assert.ok(writes.every(({key}) => key.startsWith(ASSETS)));
  assert.ok(!operations.some(({key}) => key?.endsWith("uploads.json")));
  assert.equal((await handleVideo(upload(segment(0)), configuration)).status, 200);
  assert.equal((await handleVideo(upload(segment(0), new Uint8Array([1, 2, 3])), configuration)).status, 409);
});

test("filenames and playlist references reject traversal, queries, external URLs, and mixed generations", async () => {
  const configuration = env();
  for (const name of ["../init.mp4", "../../private", `https://evil.example/${init()}`, `${init()}?token=x`, `${init()}#fragment`, "init-not-a-generation.mp4", `segment-${GENERATION}-Infinity.m4s`]) {
    assert.equal((await handleVideo(upload(name), configuration)).status, 400);
  }
  const valid = playlist();
  for (const value of [
    valid.replace(init(), `https://evil.example/${init()}`),
    valid.replace(segment(0), `../${segment(0)}`),
    valid.replace(segment(0), segment(0, OTHER)),
    valid.replace(segment(0), `${segment(0)}?auth=secret`),
    valid.replace("#EXT-X-INDEPENDENT-SEGMENTS", '#EXT-X-KEY:METHOD=AES-128,URI="https://evil.example/key"'),
  ]) assert.equal((await handleVideo(upload("index.m3u8", value), configuration)).status, 400);
  const duplicateName = new Request(`${ORIGIN}/api/live/upload?name=${init()}&name=${segment(0)}`, {method: "POST", headers: {Authorization: `Bearer ${TOKEN}`}, body: MP4});
  assert.equal((await handleVideo(duplicateName, configuration)).status, 400);
  assert.equal(configuration.BUCKET.records.size, 0);
});

test("all segment timestamps are required and frame rate metadata must be a bounded decimal", async () => {
  const configuration = env();
  const text = playlist();
  for (const value of [
    text.replace(/#EXT-X-PROGRAM-DATE-TIME:[^\n]+\n/, ""),
    text.replace(/#EXT-X-PROGRAM-DATE-TIME:[^\n]+/, "#EXT-X-PROGRAM-DATE-TIME:2026-02-31T00:00:00Z"),
    playlist({count: 13, base: Date.now() - 10_000}),
    text.replace("#EXTINF:0.500000", "#EXTINF:0.000000"),
    text.replace("#EXT-X-MEDIA-SEQUENCE:0", "#EXT-X-MEDIA-SEQUENCE:100"),
  ]) assert.equal((await handleVideo(upload("index.m3u8", value), configuration)).status, 400);
  await assets(configuration);
  for (const fps of ["0", "-1", "241", "NaN", "Infinity", "1e2", "59.1234567", ".5", "5."]) assert.equal((await handleVideo(upload("index.m3u8", text, {headers: {"X-HoloSpeed-Target-Fps": fps}}), configuration)).status, 400);
  assert.equal((await handleVideo(upload("index.m3u8", text.replaceAll("Z", "+0000")), configuration)).status, 200, "FFmpeg's UTC offset format is supported");
});

test("old captures stay stale through upload retries and future timestamps are rejected", async (context) => {
  const configuration = env();
  const now = Date.now();
  context.mock.method(Date, "now", () => now);
  const {response, text} = await publish(configuration, {base: now - 40_000});
  assert.equal(response.status, 200);
  const first = await (await handleVideo(get("/api/live/status"), configuration)).json();
  assert.equal(first.status, "stale");
  context.mock.method(Date, "now", () => now + 1000);
  assert.equal((await handleVideo(upload("index.m3u8", text), configuration)).status, 200);
  const repeated = await (await handleVideo(get("/api/live/status"), configuration)).json();
  assert.equal(repeated.captured_at, first.captured_at);
  assert.equal(repeated.updated_at, first.updated_at);
  assert.equal(repeated.status, "stale");
  assert.equal((await handleVideo(upload("index.m3u8", playlist({base: now + 60_000})), configuration)).status, 400);
});

test("a new encoder generation may restart sequence numbers but never move capture time backward", async () => {
  const configuration = env();
  const base = Date.now() - 6000;
  const previous = await publish(configuration, {first: 100, base});
  assert.equal(previous.response.status, 200);
  const current = await publish(configuration, {generation: OTHER, first: 0, base: base + 2000});
  assert.equal(current.response.status, 200);
  assert.equal((await handleVideo(upload("index.m3u8", previous.text), configuration)).status, 409);
  assert.equal(await (await handleVideo(get("/api/live/index.m3u8"), configuration)).text(), current.text);
  assert.equal((await handleVideo(get(`/api/live/${init(OTHER)}`), configuration)).status, 200);
  assert.equal((await handleVideo(get(`/api/live/${init()}`), configuration)).status, 200, "Prior retained segments keep their matching initialization asset");
});

test("same-generation sequence rollback and rewritten captures cannot replace a committed playlist", async () => {
  const configuration = env();
  const base = Date.now() - 6000;
  const initial = await publish(configuration, {first: 10, base});
  assert.equal(initial.response.status, 200);
  assert.equal((await handleVideo(upload("index.m3u8", playlist({first: 10, base: base + 500})), configuration)).status, 409);
  assert.equal((await handleVideo(upload(segment(5)), configuration)).status, 409);
  assert.equal(await (await handleVideo(get("/api/live/index.m3u8"), configuration)).text(), initial.text);
});

test("retention keeps twelve recent segments after prior playlist grace periods expire", async (context) => {
  const configuration = env();
  let now = Date.now();
  context.mock.method(Date, "now", () => now);
  const base = now - 12_000;
  await handleVideo(upload(init()), configuration);
  for (let sequence = 0; sequence < 20; sequence++) {
    await handleVideo(upload(segment(sequence)), configuration);
    if (sequence >= 5) {
      now += 11_000;
      assert.equal((await handleVideo(upload("index.m3u8", playlist({first: sequence - 5, base: base + (sequence - 5) * 500})), configuration)).status, 200);
    }
  }
  assert.equal((await handleVideo(get(`/api/live/${segment(7)}`), configuration)).status, 404);
  assert.equal((await handleVideo(get(`/api/live/${segment(8)}`), configuration)).status, 200);
  assert.equal((await handleVideo(get(`/api/live/${segment(19)}`), configuration)).status, 200);
  assert.equal((await handleVideo(get(`/api/live/${init()}`), configuration)).status, 200);
  assert.equal([...configuration.BUCKET.records.keys()].filter((key) => key.startsWith(ASSETS)).length, 13);
});

test("periodic bounded garbage collection removes old orphans but preserves fresh and referenced uploads", async (context) => {
  const configuration = env();
  let now = Date.now();
  context.mock.method(Date, "now", () => now);
  assert.equal((await publish(configuration, {base: now - 3500})).response.status, 200);
  assert.equal((await handleVideo(upload(segment(900, OTHER)), configuration)).status, 200);
  assert.equal((await handleVideo(upload(segment(901, OTHER)), configuration)).status, 200);
  // Older uploads from before this metadata field existed use R2's upload date.
  configuration.BUCKET.records.get(ASSETS + segment(901, OTHER)).customMetadata = {};
  now += 31_000;
  assert.equal((await handleVideo(upload(segment(902, OTHER)), configuration)).status, 200);
  assert.equal((await publish(configuration, {first: 27, base: now - 3500})).response.status, 200);
  assert.equal(await configuration.BUCKET.head(ASSETS + segment(900, OTHER)), null);
  assert.equal(await configuration.BUCKET.head(ASSETS + segment(901, OTHER)), null);
  assert.ok(await configuration.BUCKET.head(ASSETS + segment(902, OTHER)), "A fresh not-yet-advertised fragment is not an orphan eligible for deletion");
  assert.ok(await configuration.BUCKET.head(ASSETS + segment(0)), "Referenced fragments survive age-based cleanup");
  assert.ok(await configuration.BUCKET.head(ASSETS + init()));
  const listings = configuration.BUCKET.operations.filter(({method}) => method === "list");
  assert.ok(listings.length >= 2, "Initial and sequence-32 publication trigger bounded cleanup");
  assert.ok(listings.every(({prefix, limit}) => prefix === ASSETS && limit <= 128));
  assert.ok(!configuration.BUCKET.operations.some(({method, key}) => method === "put" && key.endsWith("uploads.json")));
});

test("periodic fragment upload cleans old orphans even when no playlist ever publishes", async (context) => {
  const configuration = env();
  let now = Date.now();
  context.mock.method(Date, "now", () => now);
  assert.equal((await handleVideo(upload(segment(0)), configuration)).status, 200);
  now += 31_000;
  assert.equal((await handleVideo(upload(segment(32)), configuration)).status, 200);
  assert.equal(await configuration.BUCKET.head(ASSETS + segment(0)), null);
  assert.ok(await configuration.BUCKET.head(ASSETS + segment(32)), "The newly uploaded fragment is too fresh for orphan cleanup");
  assert.equal(configuration.BUCKET.records.has("observer/video/manifest.json"), false);
  assert.equal((await (await handleVideo(get("/api/live/status"), configuration)).json()).status, "waiting");
  const listings = configuration.BUCKET.operations.filter(({method}) => method === "list");
  assert.ok(listings.length >= 1);
  assert.ok(listings.every(({prefix, limit}) => prefix === ASSETS && limit <= 128));
});

test("a batch sequence jump preserves the previous playlist for ten seconds", async (context) => {
  const configuration = env();
  let now = Date.now();
  context.mock.method(Date, "now", () => now);
  assert.equal((await publish(configuration, {base: now - 3500})).response.status, 200);
  now += 1000;
  assert.equal((await publish(configuration, {first: 20, count: 12, base: now - 6500})).response.status, 200);
  for (let sequence = 0; sequence < 6; sequence++) assert.equal((await handleVideo(get(`/api/live/${segment(sequence)}`), configuration)).status, 200, "A client reading the immediately preceding playlist must still fetch its chunks");
  now += 11_000;
  assert.equal((await publish(configuration, {first: 21, count: 12, base: now - 6500})).response.status, 200);
  assert.equal((await handleVideo(get(`/api/live/${segment(0)}`), configuration)).status, 404);
  assert.equal(await configuration.BUCKET.head(ASSETS + segment(0)), null);
  assert.equal((await handleVideo(get(`/api/live/${segment(20)}`), configuration)).status, 200, "The immediately preceding large window has its own grace period");
});

test("failed or racing manifest commits leave one coherent advertised capture", async () => {
  const configuration = env();
  const base = Date.now() - 6000;
  const initial = await publish(configuration, {base});
  await assets(configuration, {first: 6, count: 2});
  configuration.BUCKET.failNextManifest = true;
  assert.equal((await handleVideo(upload("index.m3u8", playlist({first: 1, base: base + 500})), configuration)).status, 409);
  assert.equal(await (await handleVideo(get("/api/live/index.m3u8"), configuration)).text(), initial.text);
  const candidates = [playlist({first: 1, base: base + 500}), playlist({first: 2, base: base + 1000})];
  configuration.BUCKET.raceNextManifestReads();
  const results = await Promise.all(candidates.map((text) => handleVideo(upload("index.m3u8", text), configuration)));
  assert.deepEqual(results.map((response) => response.status).sort(), [200, 409]);
  const saved = await (await handleVideo(get("/api/live/index.m3u8"), configuration)).text();
  assert.ok(candidates.includes(saved));
});

test("uploads enforce methods, media types, declared sizes, and streamed size limits", async () => {
  const configuration = env();
  assert.equal((await handleVideo(get("/api/live/upload"), configuration)).status, 405);
  assert.equal((await handleVideo(upload(init(), MP4, {type: "text/plain"}), configuration)).status, 415);
  assert.equal((await handleVideo(upload("index.m3u8", playlist(), {type: "video/mp4"}), configuration)).status, 415);
  assert.equal((await handleVideo(upload(init(), MP4, {headers: {"Content-Length": String(4 * 1024 * 1024 + 1)}}), configuration)).status, 413);
  assert.equal((await handleVideo(upload(init(), new Uint8Array(4 * 1024 * 1024 + 1)), configuration)).status, 413);
  assert.equal((await handleVideo(upload("index.m3u8", "x".repeat(64 * 1024 + 1)), configuration)).status, 413);
  assert.equal((await handleVideo(upload("index.m3u8", new Uint8Array([255, 254])), configuration)).status, 400);
  assert.equal(configuration.BUCKET.records.size, 0);
});
