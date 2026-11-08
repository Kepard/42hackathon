import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {handleApi} from "../worker/api.mjs";

const ORIGIN = "https://observer.example";
const CONTROL = "test-voice-private-control-capability-at-least-32-chars";
const INGEST = "test-voice-independent-publisher-credential";
const GLADIA = "sk_gladia_dummy_credential_for_mock_tests";
const ID = "d0a560fa-401d-481c-99aa-a540db217e75";
const SECOND = "95d902a2-6ec8-4e9b-8454-f40ca61e3d52";
const SESSION = "18edca0c-bb5b-421a-8b9d-7ba33fb903b8";
const URL = "wss://api.eu-west.gladia.io/v2/live?token=ephemeral-mock-capability";
class Bucket {
  records = new Map(); revision = 0;
  async get(key) {const item = this.records.get(key); return item ? {...item, body: new TextEncoder().encode(item.text), json: async () => JSON.parse(item.text)} : null;}
  async put(key, text, options = {}) {
    const current = this.records.get(key);
    if (options.onlyIf?.etagMatches && current?.etag !== options.onlyIf.etagMatches || options.onlyIf?.etagDoesNotMatch === "*" && current) return null;
    const item = {text, etag: String(++this.revision)}; this.records.set(key, item); return item;
  }
}
const environment = (extra = {}) => ({BUCKET: new Bucket(), CONTROL_TOKEN: CONTROL, INGEST_TOKEN: INGEST, GLADIA_API_KEY: GLADIA, ...extra});
function request(path, body, token = CONTROL, headers = {}) {
  return new Request(ORIGIN + path, {method: body === undefined ? "GET" : "POST", headers: {Origin: ORIGIN, "Sec-Fetch-Site": "same-origin", ...(token ? {Authorization: `Bearer ${token}`} : {}), ...(body === undefined ? {} : {"Content-Type": "application/json"}), ...headers}, ...(body === undefined ? {} : {body: typeof body === "string" ? body : JSON.stringify(body)})});
}
const session = (env, id = ID, token = CONTROL, headers = {}) => handleApi(request("/api/voice/session", {id}, token, headers), env);
const voice = (extra = {}) => ({id: ID, action: "voice", text: "Pause the agent", session_id: SESSION, utterance_id: "utterance-1", run_id: "run-1", ...extra});
const create = (env, body = voice(), token = CONTROL) => handleApi(request("/api/commands", body, token), env);
const acknowledge = (env, body = {status: "completed", detail: "Agent paused", run_id: "run-1", data: {intent: "pause", reply: "The agent is paused."}}) => handleApi(request(`/api/commands/${ID}/result`, body, INGEST), env);
function upstream(context, callback = () => Response.json({id: SESSION, url: URL})) {
  const calls = [];
  context.mock.method(globalThis, "fetch", async (url, options) => {calls.push({url, options}); return callback(url, options);});
  return calls;
}

test("voice session creation is operator-only, same-origin, bounded, and configured server-side", async (context) => {
  const calls = upstream(context);
  const env = environment();
  for (const token of [null, INGEST, "wrong"]) assert.equal((await session(env, ID, token)).status, 401);
  for (const headers of [{Origin: "https://evil.example"}, {Origin: ""}, {"Sec-Fetch-Site": "cross-site"}]) assert.equal((await session(env, ID, CONTROL, headers)).status, 403);
  assert.equal((await session(environment({GLADIA_API_KEY: undefined}))).status, 503);
  assert.equal((await handleApi(request("/api/voice/session"), env)).status, 405);
  for (const body of [{}, {id: "bad"}, {id: ID, sample_rate: 48000}, {id: ID, url: "https://evil.example"}]) assert.equal((await handleApi(request("/api/voice/session", body), env)).status, 400);
  assert.equal((await handleApi(request("/api/voice/session", "x".repeat(1025)), env)).status, 413);
  assert.equal(calls.length, 0);
  assert.equal(env.BUCKET.records.size, 0);
});

test("session exchange uses fixed PCM configuration and never stores the key or ephemeral URL", async (context) => {
  const calls = upstream(context, () => Response.json({id: SESSION, url: URL, internal_key: GLADIA, ignored: "provider metadata"}));
  const env = environment();
  const response = await session(env);
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("Cache-Control"), "no-store");
  assert.deepEqual(await response.json(), {id: SESSION, url: URL, sample_rate: 16000});
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "https://api.gladia.io/v2/live");
  assert.equal(calls[0].options.headers["x-gladia-key"], GLADIA);
  assert.equal(calls[0].options.redirect, "error");
  assert.deepEqual(JSON.parse(calls[0].options.body), {encoding: "wav/pcm", bit_depth: 16, sample_rate: 16000, channels: 1, model: "solaria-1", endpointing: 0.3, maximum_duration_without_endpointing: 5, messages_config: {receive_partial_transcripts: true, receive_final_transcripts: true, receive_lifecycle_events: true, receive_errors: true}});
  const records = [...env.BUCKET.records.values()].map((value) => value.text).join("\n");
  for (const privateValue of [GLADIA, URL, "ephemeral-mock-capability"]) assert.ok(!records.includes(privateValue));
  const publicText = await (await handleApi(request("/api/state", undefined, null), env)).text();
  for (const privateValue of [GLADIA, URL, CONTROL]) assert.ok(!publicText.includes(privateValue));
});

test("one durable request claim prevents duplicate billable calls across retries and concurrent starts", async (context) => {
  let finish;
  const calls = upstream(context, () => new Promise((resolve) => {finish = resolve;}));
  const env = environment();
  const first = session(env);
  while (!finish) await new Promise((resolve) => setImmediate(resolve));
  assert.equal((await session(env)).status, 409);
  const blocked = await session(env, SECOND);
  assert.equal(blocked.status, 429);
  assert.ok(Number(blocked.headers.get("Retry-After")) > 0);
  finish(Response.json({id: SESSION, url: URL}));
  assert.equal((await first).status, 200);
  assert.equal(calls.length, 1);
  const now = Date.now(); context.mock.method(Date, "now", () => now + 60_000);
  assert.equal((await session(env)).status, 409, "A new cooldown window cannot replay an old paid request");
  assert.equal(calls.length, 1);
});

test("unknown upstream outcome is generic and never automatically recreates a session", async (context) => {
  const calls = upstream(context, () => {throw new Error(`Network failure ${GLADIA}`);});
  const env = environment();
  const response = await session(env);
  assert.equal(response.status, 502);
  const text = await response.text();
  assert.ok(!text.includes(GLADIA));
  assert.match(text, /not be retried automatically/);
  assert.equal((await session(env)).status, 409);
  assert.equal(calls.length, 1);
});

test("upstream creation times out with a durable unknown outcome", async (context) => {
  context.mock.method(globalThis, "setTimeout", (callback) => {queueMicrotask(callback); return 0;});
  const calls = upstream(context, (_url, options) => new Promise((_resolve, reject) => {
    if (options.signal.aborted) reject(new Error("aborted"));
    else options.signal.addEventListener("abort", () => reject(new Error("aborted")));
  }));
  const env = environment();
  assert.equal((await session(env)).status, 504);
  assert.equal((await session(env)).status, 409);
  assert.equal(calls.length, 1);
});

test("provider URLs must use a Gladia secure WebSocket without returned API credentials", async (context) => {
  let payload;
  upstream(context, () => Response.json(payload));
  for (const url of ["wss://evil.example/live", "wss://gladia.io.evil.example/live", "https://api.gladia.io/live", "wss://secret@api.gladia.io/live", "wss://api.gladia.io:444/live", `wss://api.gladia.io/live?token=${GLADIA}`, `wss://api.gladia.io/live?token=${CONTROL}`]) {
    payload = {id: SESSION, url};
    assert.equal((await session(environment())).status, 502);
  }
  payload = {id: "not-a-session-uuid", url: URL};
  assert.equal((await session(environment())).status, 502);
});

test("voice commands bind text, transcript identity, and expected run to the immutable command ID", async () => {
  const env = environment();
  assert.equal((await create(env, voice(), null)).status, 401);
  assert.equal((await create(env)).status, 202);
  assert.equal((await create(env)).status, 200);
  for (const change of [{text: "Resume"}, {session_id: "typed"}, {utterance_id: "utterance-2"}, {run_id: "other-run"}, {run_id: null}]) assert.equal((await create(env, voice(change))).status, 409);
  const next = await handleApi(request("/api/commands/next", undefined, INGEST), env);
  assert.equal((await next.json()).command.run_id, "run-1");
  for (const change of [{text: ""}, {text: "x".repeat(1001)}, {session_id: "unknown"}, {utterance_id: "bad/id"}, {utterance_id: "x".repeat(129)}, {run_id: undefined}, {run_id: "../other"}, {instructions: "injected"}]) assert.equal((await create(environment(), voice(change))).status, 400);
  assert.equal((await create(environment(), voice({session_id: "typed", run_id: null}))).status, 202);
});

test("voice result data is private, bounded, redacted, and cannot change the original expected run", async () => {
  const env = environment(); await create(env);
  for (const body of [
    {status: "completed", detail: "Missing data"},
    {status: "completed", detail: "Wrong run", run_id: "other-run", data: {intent: "pause", reply: "Paused"}},
    {status: "completed", detail: "Wrong run", run_id: null, data: {intent: "pause", reply: "Paused"}},
    {status: "completed", detail: "Invalid", data: {intent: "shell", reply: "No"}},
    {status: "completed", detail: "Invalid", data: {intent: "guidance", reply: "x".repeat(2001)}},
    {status: "completed", detail: "Invalid", data: {intent: "guidance", reply: "No", guidance_id: "not-uuid"}},
    {status: "completed", detail: "Invalid", data: {intent: "question", reply: "No", extra: "private"}},
  ]) assert.equal((await acknowledge(env, body)).status, 400);
  const body = {status: "completed", detail: "Guidance saved", data: {intent: "guidance", reply: `Saved ${CONTROL} ${INGEST}`, guidance_id: SECOND}};
  assert.equal((await acknowledge(env, body)).status, 200);
  assert.equal((await acknowledge(env, body)).status, 200);
  const result = await handleApi(request(`/api/commands/${ID}`), env);
  const command = (await result.json()).command;
  assert.equal(command.run_id, "run-1");
  assert.ok(!command.data.reply.includes(CONTROL) && !command.data.reply.includes(INGEST));
  assert.equal((await handleApi(request(`/api/commands/${ID}`, undefined, null), env)).status, 401);
  assert.equal((await acknowledge(env, {...body, data: {...body.data, reply: "Changed"}})).status, 409);
});

test("dashboard CSP permits only Gladia secure WebSockets in addition to same-origin connections", async () => {
  const build = await readFile(new globalThis.URL("../scripts/build.mjs", import.meta.url), "utf8");
  assert.match(build, /connect-src 'self' wss:\/\/gladia\.io wss:\/\/\*\.gladia\.io;/);
  assert.ok(!build.includes("connect-src *"));
});
