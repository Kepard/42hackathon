import test from "node:test";
import assert from "node:assert/strict";
import { handleApi } from "./api.mjs";

const TOKEN = "test-observer-token-with-enough-entropy";
const JPEG = new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 0x00, 0x02, 0xff, 0xd9]);
const ORIGIN = "https://observer.example";

class FakeBucket {
  records = new Map();
  operations = [];
  revision = 0;
  failNextStateCommit = false;

  async get(key) {
    const entry = this.records.get(key);
    if (!entry) return null;
    const body = entry.bytes.slice();
    return {
      etag: entry.etag, body,
      json: async () => JSON.parse(new TextDecoder().decode(body)),
      text: async () => new TextDecoder().decode(body),
    };
  }

  async put(key, value, options = {}) {
    this.operations.push({ type: "put", key, options });
    const previous = this.records.get(key);
    if (options.onlyIf?.etagMatches && previous?.etag !== options.onlyIf.etagMatches) return null;
    if (options.onlyIf?.etagDoesNotMatch === "*" && previous) return null;
    if (key === "observer/state.json" && this.failNextStateCommit) {
      this.failNextStateCommit = false;
      return null;
    }
    const bytes = typeof value === "string" ? new TextEncoder().encode(value) : new Uint8Array(value);
    const etag = `revision-${++this.revision}`;
    this.records.set(key, { bytes: bytes.slice(), etag });
    return { key, etag };
  }

  async delete(key) { this.operations.push({ type: "delete", key }); this.records.delete(key); }
}

function env() { return { BUCKET: new FakeBucket(), INGEST_TOKEN: TOKEN }; }
function get(path) { return new Request(ORIGIN + path); }
function metadata(seq = Date.now() - 1000, overrides = {}) {
  return {
    state: { mode: "live", status: "paused", connected: true, run_id: "run-1", elapsed_ms: 1000, objective: "Find wood", strategy: "Inspect the tree", expected_effect: "A different view", keys: [], action: null, holo_latency_ms: 120, jev_latency_ms: null, metrics: { actions: 1, observations: 2, recoveries: 0, errors: 0, input_tokens: 50, output_tokens: 10 }, milestones: [], events: [], frame_url: "/api/runs/run-1/frames/local.png", latency_history: { holo: [120], jev: [] } },
    runs: [{ id: "run-1", mode: "live", status: "paused", duration_ms: 1000, metrics: { actions: 1 }, milestones: [] }],
    updated_at: new Date(seq + 100).toISOString(), captured_at: new Date(seq).toISOString(),
    capture_seq: seq, feed_status: "online", source: "minecraft-window", ...overrides,
  };
}
function ingest(body, { jpeg = JPEG, token = TOKEN, blobMetadata = false } = {}) {
  const form = new FormData();
  form.append("metadata", blobMetadata ? new Blob([JSON.stringify(body)], { type: "application/json" }) : JSON.stringify(body));
  if (jpeg !== null) form.append("frame", new Blob([jpeg], { type: "image/jpeg" }), "frame.jpg");
  return new Request(ORIGIN + "/api/ingest", { method: "POST", headers: token ? { Authorization: `Bearer ${token}` } : {}, body: form });
}
function postRun(id, body, token = TOKEN) {
  return new Request(ORIGIN + "/api/runs/" + id, { method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` }, body: JSON.stringify(body) });
}

test("waiting state is honest and control routes do not exist", async () => {
  const configuration = env();
  const response = await handleApi(get("/api/state"), configuration);
  assert.deepEqual(await response.json(), { state: null, runs: [], updated_at: null, captured_at: null, capture_seq: null, feed_status: "offline", source: "minecraft-window" });
  assert.equal(response.headers.get("Cache-Control"), "no-store");
  assert.equal((await handleApi(get("/api/frame.jpg"), configuration)).status, 404);
  assert.equal(await handleApi(new Request(ORIGIN + "/api/start", { method: "POST" }), configuration), null);
  assert.equal(await handleApi(get("/assets/app.js"), configuration), null);
  assert.equal((await handleApi(new Request(ORIGIN + "/api/state", { method: "POST" }), configuration)).status, 405);
});

test("route discriminator survives live metadata and archived summary configuration", async () => {
  for (const route of ["from_scratch", "ruined_portal"]) {
    const configuration = env();
    const input = metadata();
    input.state.route = route;
    input.runs[0].config = {route};
    assert.equal((await handleApi(ingest(input), configuration)).status, 200);
    const saved = await (await handleApi(get("/api/state"), configuration)).json();
    assert.equal(saved.state.route, route);
    assert.equal(saved.runs[0].config.route, route);
    const archive = {summary: input.runs[0], events: []};
    assert.equal((await handleApi(postRun("run-1", archive), configuration)).status, 200);
    assert.equal((await (await handleApi(get("/api/runs/run-1"), configuration)).json()).summary.config.route, route);
  }
});

test("invalid route values never enter public state or archives", async () => {
  for (const route of ["unknown_route", null, 1, {name: "from_scratch"}]) {
    const configuration = env();
    const input = metadata();
    input.state.route = route;
    assert.equal((await handleApi(ingest(input), configuration)).status, 400);
    assert.equal((await handleApi(postRun("run-1", {summary: {id: "run-1", config: {route}}, events: []}), configuration)).status, 400);
    assert.equal(configuration.BUCKET.records.size, 0);
  }
});

test("lightweight capture metadata omits telemetry and preserves actual capture freshness", async () => {
  const configuration = env();
  const input = metadata(Date.now() - 30000);
  await handleApi(ingest(input), configuration);
  const response = await handleApi(get("/api/capture"), configuration);
  const value = await response.json();
  assert.deepEqual(Object.keys(value).sort(), ["capture_seq", "captured_at", "feed_status", "source", "updated_at"]);
  assert.equal(value.capture_seq, input.capture_seq);
  assert.equal(value.captured_at, input.captured_at);
  assert.equal(value.feed_status, "stale");
  assert.equal(response.headers.get("Cache-Control"), "no-store");
});

test("publisher authentication rejects missing and wrong credentials without leaking secrets", async () => {
  const configuration = env();
  for (const token of [null, "wrong-secret"] ) {
    const response = await handleApi(ingest(metadata(), { token }), configuration);
    assert.equal(response.status, 401);
    assert.ok(!(await response.text()).includes(TOKEN));
  }
  assert.equal(configuration.BUCKET.records.size, 0);
  const response = await handleApi(ingest(metadata()), { BUCKET: configuration.BUCKET });
  assert.equal(response.status, 503);
});

test("ingest commits the immutable JPEG before metadata, and serves the matching capture", async () => {
  const configuration = env();
  const input = metadata();
  assert.equal((await handleApi(ingest(input, { blobMetadata: true }), configuration)).status, 200);
  assert.match(configuration.BUCKET.operations[0].key, new RegExp(`/frames/${input.capture_seq}-`));
  assert.equal(configuration.BUCKET.operations[1].key, "observer/state.json");
  const saved = await (await handleApi(get("/api/state"), configuration)).json();
  assert.equal(saved.capture_seq, input.capture_seq);
  assert.equal(saved.captured_at, input.captured_at);
  assert.equal(saved.feed_status, "online");
  assert.equal(saved.state.frame_url, null);
  assert.ok(saved.received_at);
  assert.equal(saved._frame, undefined);
  const response = await handleApi(get(`/api/frame.jpg?seq=${input.capture_seq}`), configuration);
  assert.deepEqual(new Uint8Array(await response.arrayBuffer()), JPEG);
  assert.equal(response.headers.get("X-Captured-At"), input.captured_at);
  assert.equal(response.headers.get("X-Capture-Sequence"), String(input.capture_seq));
  assert.equal(response.headers.get("Cache-Control"), "no-store");
});

test("old capture cannot be relabeled fresh by metadata heartbeat", async () => {
  const configuration = env();
  const input = metadata(Date.now() - 40_000);
  assert.equal((await handleApi(ingest(input), configuration)).status, 200);
  const heartbeat = { ...input, updated_at: new Date().toISOString() };
  assert.equal((await handleApi(ingest(heartbeat, { jpeg: null }), configuration)).status, 200);
  const saved = await (await handleApi(get("/api/state"), configuration)).json();
  assert.equal(saved.feed_status, "stale");
  assert.equal(saved.captured_at, input.captured_at);
  assert.equal(saved.capture_seq, input.capture_seq);
});

test("hybrid skill state and game-state evidence fit the existing snapshot contract", async () => {
  const configuration = env();
  const input = metadata();
  input.state.status = "running";
  input.state.keys = ["forward", "attack"];
  input.state.action = { skill: "mine", item: "minecraft:birch_log", target_count: 4 };
  input.state.events = [{ seq: 21, type: "skill_progress", elapsed_ms: 1200, data: {
    candidate_id: "mine_birch", tick: 90, inventory_count: 2,
    skill: { skill_id: "skill-1", status: "running", phase: "mining", keys: ["attack"] },
  } }];
  input.state.milestones = [{ id: "wood", elapsed_ms: 1100, verification: "game_state",
    state_id: "state-3", tick: 88, evidence: { inventory: { "minecraft:birch_log": 2 } },
  }];
  input.runs[0].milestones = input.state.milestones;
  assert.equal((await handleApi(ingest(input), configuration)).status, 200);
  const saved = await (await handleApi(get("/api/state"), configuration)).json();
  assert.deepEqual(saved.state.action, input.state.action);
  assert.deepEqual(saved.state.keys, input.state.keys);
  assert.deepEqual(saved.state.events, input.state.events);
  assert.deepEqual(saved.state.milestones, input.state.milestones);
  assert.equal(saved.state.metrics.actions, 1);
});

test("restarted offline publisher can update state without restamping the previous frame", async () => {
  const configuration = env();
  const input = metadata(Date.now() - 40_000);
  await handleApi(ingest(input), configuration);
  const heartbeat = metadata(undefined, { updated_at: new Date().toISOString(), capture_seq: null, captured_at: null, feed_status: "offline", state: { mode: "live", status: "unavailable", connected: false } });
  const response = await handleApi(ingest(heartbeat, { jpeg: null }), configuration);
  assert.equal(response.status, 200);
  const ack = await response.json();
  assert.equal(ack.capture_seq, input.capture_seq);
  assert.equal(ack.captured_at, input.captured_at);
  const saved = await (await handleApi(get("/api/state"), configuration)).json();
  assert.equal(saved.state.status, "unavailable");
  assert.equal(saved.feed_status, "offline");
  assert.equal(saved.captured_at, input.captured_at);
  const frame = await handleApi(get("/api/frame.jpg"), configuration);
  assert.equal(frame.headers.get("X-Capture-Sequence"), String(input.capture_seq));
  const invalid = { ...heartbeat, feed_status: "online", updated_at: new Date(Date.parse(heartbeat.updated_at) + 1).toISOString() };
  assert.equal((await handleApi(ingest(invalid, { jpeg: null }), configuration)).status, 409);
});

test("future capture and metadata cannot claim a fresh live feed", async () => {
  const configuration = env();
  assert.equal((await handleApi(ingest(metadata(Date.now() + 60_000)), configuration)).status, 400);
  assert.equal((await handleApi(ingest(metadata(Date.now() + 4000)), configuration)).status, 200);
  assert.equal((await (await handleApi(get("/api/state"), configuration)).json()).feed_status, "stale");
});

test("rejects stale sequences, stale metadata, timestamp restamping, and missing JPEGs", async () => {
  const configuration = env();
  const input = metadata();
  await handleApi(ingest(input), configuration);
  const variants = [
    [{ ...input, updated_at: new Date(Date.parse(input.updated_at) - 1).toISOString() }, 409],
    [{ ...input, capture_seq: input.capture_seq - 1, updated_at: new Date(Date.parse(input.updated_at) + 1).toISOString() }, 409],
    [{ ...input, captured_at: new Date(Date.parse(input.captured_at) + 1).toISOString(), updated_at: new Date(Date.parse(input.updated_at) + 1).toISOString() }, 409],
    [metadata(input.capture_seq + 1), 400],
  ];
  for (const [payload, status] of variants) assert.equal((await handleApi(ingest(payload, { jpeg: null }), configuration)).status, status);
  assert.equal((await (await handleApi(get("/api/state"), configuration)).json()).capture_seq, input.capture_seq);
});

test("same capture cannot be overwritten with different JPEG bytes", async () => {
  const configuration = env();
  const input = metadata();
  await handleApi(ingest(input), configuration);
  const altered = JPEG.slice(); altered[4] = 1;
  const result = await handleApi(ingest({ ...input, updated_at: new Date(Date.parse(input.updated_at) + 10).toISOString() }, { jpeg: altered }), configuration);
  assert.equal(result.status, 409);
  assert.deepEqual(new Uint8Array(await (await handleApi(get("/api/frame.jpg"), configuration)).arrayBuffer()), JPEG);
});

test("keeps current plus two previous captures and never substitutes a newer frame for a requested old one", async () => {
  const configuration = env();
  const start = Date.now() - 5000;
  for (let i = 0; i < 4; i++) assert.equal((await handleApi(ingest(metadata(start + i * 1000)), configuration)).status, 200);
  assert.equal((await handleApi(get(`/api/frame.jpg?seq=${start}`), configuration)).status, 404);
  assert.equal((await handleApi(get(`/api/frame.jpg?seq=${start + 1000}`), configuration)).status, 200);
  const latest = await handleApi(get("/api/frame.jpg"), configuration);
  assert.equal(latest.headers.get("X-Capture-Sequence"), String(start + 3000));
  const operations = configuration.BUCKET.operations;
  assert.equal(operations.at(-1).type, "delete");
  assert.equal(operations.at(-2).key, "observer/state.json");
});

test("conditional metadata failure preserves the prior frame and metadata", async () => {
  const configuration = env();
  const first = metadata();
  await handleApi(ingest(first), configuration);
  configuration.BUCKET.failNextStateCommit = true;
  assert.equal((await handleApi(ingest(metadata(first.capture_seq + 1)), configuration)).status, 409);
  assert.equal((await (await handleApi(get("/api/state"), configuration)).json()).capture_seq, first.capture_seq);
  assert.equal((await handleApi(get("/api/frame.jpg"), configuration)).headers.get("X-Capture-Sequence"), String(first.capture_seq));
});

test("malformed metadata and invalid image payloads fail before any writes", async () => {
  const configuration = env();
  const invalid = [
    metadata(undefined, { capture_seq: "123" }),
    metadata(undefined, { source: "made-up" }),
    metadata(undefined, { feed_status: "live-but-unverified" }),
    metadata(undefined, { updated_at: "2026-02-31T00:00:00Z" }),
    metadata(undefined, { captured_at: "not-a-date" }),
    metadata(undefined, { surprise: "unexpected" }),
    metadata(undefined, { state: { mode: "live", status: "running", objective: { text: "invalid" } } }),
  ];
  for (const payload of invalid) assert.equal((await handleApi(ingest(payload), configuration)).status, 400);
  assert.equal((await handleApi(ingest(metadata(), { jpeg: new Uint8Array([1, 2, 3, 4]) }), configuration)).status, 400);
  assert.equal(configuration.BUCKET.records.size, 0);
});

test("request bounds cover declared and streamed payload sizes", async () => {
  const configuration = env();
  const oversized = new Request(ORIGIN + "/api/ingest", { method: "POST", headers: { Authorization: `Bearer ${TOKEN}`, "Content-Type": "multipart/form-data; boundary=abc", "Content-Length": String(2 * 1024 * 1024 + 1) }, body: "x" });
  assert.equal((await handleApi(oversized, configuration)).status, 413);
  const run = new Request(ORIGIN + "/api/runs/run-1", { method: "POST", headers: { Authorization: `Bearer ${TOKEN}`, "Content-Type": "application/json" }, body: "x".repeat(8 * 1024 * 1024 + 1) });
  assert.equal((await handleApi(run, configuration)).status, 413);
});

test("run upload retains safe requests, responses, decisions and metrics while removing credential and image payloads", async () => {
  const configuration = env();
  const payload = {
    summary: { id: "run-1", mode: "live", status: "finished", duration_ms: 1000, config: { holo_model: "holo4-35b-a3b", bridge_token: TOKEN } },
    events: [{ seq: 1, type: "model_call", elapsed_ms: 123, timestamp: new Date().toISOString(), data: {
      provider: "holo", latency_ms: 123, request: { headers: { Authorization: `Bearer ${TOKEN}` }, messages: [{ role: "user", content: [{ type: "text", text: "Gather wood" }, { type: "image_url", image_url: { url: "data:image/png;base64,private" } }] }], assets: { secret: "image-data" } },
      response: { choices: [{ message: { content: "api_key=secret-value hk-fake-key cog_fake_pat apikey_fake_key " + TOKEN } }], usage: { input_tokens: 50, output_tokens: 10 } },
      image_base64: "private-image-payload", decision: { objective: "Find wood", action: { keys: ["forward"], duration_ticks: 5 } },
    } }],
  };
  assert.equal((await handleApi(postRun("run-1", payload), configuration)).status, 200);
  const response = await handleApi(get("/api/runs/run-1"), configuration);
  const text = await response.text();
  for (const secret of [TOKEN, "secret-value", "hk-fake-key", "cog_fake_pat", "apikey_fake_key", "private-image-payload", "data:image", "image-data"]) assert.ok(!text.includes(secret), `leaked ${secret}`);
  const saved = JSON.parse(text);
  assert.equal(saved.events[0].data.request.messages[0].content.length, 1);
  assert.equal(saved.events[0].data.request.messages[0].content[0].text, "Gather wood");
  assert.deepEqual(saved.events[0].data.response.usage, { input_tokens: 50, output_tokens: 10 });
  assert.equal(saved.events[0].data.latency_ms, 123);
  assert.deepEqual(saved.events[0].data.decision.action.keys, ["forward"]);
});

test("run IDs and event shapes are validated, including encoded path escapes", async () => {
  const configuration = env();
  const payload = { summary: { id: "run-1" }, events: [] };
  assert.equal((await handleApi(postRun("run-1", payload, "incorrect"), configuration)).status, 401);
  assert.equal((await handleApi(get("/api/runs/%2e%2e%2fprivate"), configuration)).status, 400);
  assert.equal((await handleApi(get("/api/runs/%ZZ"), configuration)).status, 400);
  assert.equal((await handleApi(postRun("run-2", payload), configuration)).status, 400);
  assert.equal((await handleApi(postRun("run-1", { ...payload, events: [{ seq: "bad", type: "action", elapsed_ms: 1, data: {} }] }), configuration)).status, 400);
  assert.equal((await handleApi(get("/api/runs/unknown"), configuration)).status, 404);
  assert.equal((await handleApi(get("/api/frame.jpg?seq=not-a-number"), configuration)).status, 400);
});
