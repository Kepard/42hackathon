import test from "node:test";
import assert from "node:assert/strict";
import { handleApi } from "../worker/api.mjs";

const ORIGIN = "https://observer.example";
const CONTROL = "test-control-capability-private-at-least-32-chars";
const INGEST = "test-publisher-credential-distinct-from-control";
const FIRST = "18edca0c-bb5b-421a-8b9d-7ba33fb903b8";
const SECOND = "95d902a2-6ec8-4e9b-8454-f40ca61e3d52";

/** R2 conditional writes decide atomically against the current object version. */
class FakeBucket {
  records = new Map();
  revision = 0;
  writes = [];
  failOnceForKey = null;

  async get(key) {
    const entry = this.records.get(key);
    if (!entry) return null;
    const bytes = entry.bytes.slice();
    return {
      etag: entry.etag,
      body: bytes,
      json: async () => JSON.parse(new TextDecoder().decode(bytes)),
      text: async () => new TextDecoder().decode(bytes),
    };
  }

  async put(key, value, options = {}) {
    if (key === this.failOnceForKey) {
      this.failOnceForKey = null;
      throw new Error("Injected storage failure");
    }
    const current = this.records.get(key);
    if (options.onlyIf?.etagMatches && current?.etag !== options.onlyIf.etagMatches) return null;
    if (options.onlyIf?.etagDoesNotMatch === "*" && current) return null;
    const bytes = typeof value === "string" ? new TextEncoder().encode(value) : new Uint8Array(value);
    const etag = `revision-${++this.revision}`;
    this.records.set(key, {bytes: bytes.slice(), etag});
    this.writes.push({key, options});
    return {key, etag};
  }

  async delete(key) { this.records.delete(key); }
}

function env(overrides = {}) {
  return {BUCKET: new FakeBucket(), CONTROL_TOKEN: CONTROL, INGEST_TOKEN: INGEST, ...overrides};
}

function request(path, {method = "GET", token, body, contentType = "application/json"} = {}) {
  const headers = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined && contentType) headers["Content-Type"] = contentType;
  return new Request(ORIGIN + path, {method, headers, ...(body === undefined ? {} : {body: typeof body === "string" ? body : JSON.stringify(body)})});
}

function create(configuration, id = FIRST, action = "start_new", token = CONTROL) {
  return handleApi(request("/api/commands", {method: "POST", token, body: {id, action}}), configuration);
}
function status(configuration, id = FIRST, token = CONTROL) {
  return handleApi(request(`/api/commands/${id}`, {token}), configuration);
}
function next(configuration, token = INGEST) {
  return handleApi(request("/api/commands/next", {token}), configuration);
}
function ack(configuration, id = FIRST, body = {status: "completed", detail: "Applied", run_id: "run-1"}, token = INGEST) {
  return handleApi(request(`/api/commands/${id}/result`, {method: "POST", token, body}), configuration);
}

test("public viewers cannot create, inspect, deliver, or acknowledge commands", async () => {
  const configuration = env();
  for (const response of [
    await create(configuration, FIRST, "pause", null),
    await status(configuration, FIRST, null),
    await next(configuration, null),
    await ack(configuration, FIRST, undefined, null),
  ]) {
    assert.equal(response.status, 401);
    assert.equal(response.headers.get("Cache-Control"), "no-store");
    const body = await response.text();
    assert.ok(!body.includes(CONTROL) && !body.includes(INGEST));
  }
  assert.equal(configuration.BUCKET.records.size, 0);
});

test("operator and publisher credentials grant separate capabilities", async () => {
  const configuration = env();
  assert.equal((await create(configuration, FIRST, "pause", INGEST)).status, 401);
  assert.equal((await status(configuration, FIRST, INGEST)).status, 401);
  assert.equal((await next(configuration, CONTROL)).status, 401);
  assert.equal((await ack(configuration, FIRST, undefined, CONTROL)).status, 401);
  assert.equal((await create(configuration)).status, 202);
  assert.equal((await status(configuration)).status, 200);
  assert.equal((await next(configuration)).status, 200);
});

test("missing, short, or reused control secrets disable operator access", async () => {
  for (const token of [undefined, "too-short", INGEST]) {
    const configuration = env({CONTROL_TOKEN: token});
    const response = await create(configuration, FIRST, "pause", token || CONTROL);
    assert.equal(response.status, 503);
    assert.equal(configuration.BUCKET.records.size, 0);
  }
});

test("accepted commands have fixed identity and a 30-second delivery window", async () => {
  const configuration = env();
  const response = await create(configuration);
  assert.equal(response.status, 202);
  const {command} = await response.json();
  assert.equal(command.id, FIRST);
  assert.equal(command.action, "start_new");
  assert.equal(command.status, "pending");
  assert.equal(Date.parse(command.expires_at) - Date.parse(command.created_at), 30_000);
  assert.deepEqual((await (await next(configuration)).json()).command, command);
  assert.deepEqual((await (await next(configuration)).json()).command, command, "Delivery remains pending until the publisher acknowledges it");
  const publicSnapshot = await (await handleApi(request("/api/state"), configuration)).text();
  assert.ok(!publicSnapshot.includes(FIRST) && !publicSnapshot.includes(CONTROL));
});

test("repeated command IDs are idempotent and cannot change their action", async () => {
  const configuration = env();
  const original = (await (await create(configuration)).json()).command;
  const retry = await create(configuration);
  assert.equal(retry.status, 200);
  assert.deepEqual((await retry.json()).command, original);
  assert.equal((await create(configuration, FIRST, "stop")).status, 409);
  assert.deepEqual((await (await status(configuration)).json()).command, original);
});

test("at most one command wins concurrent creation while another is pending", async () => {
  const configuration = env();
  const responses = await Promise.all([
    create(configuration, FIRST, "pause"),
    create(configuration, SECOND, "stop"),
  ]);
  assert.deepEqual(responses.map((response) => response.status).sort(), [202, 409]);
  const accepted = (await responses.find((response) => response.status === 202).json()).command;
  const delivered = (await (await next(configuration)).json()).command;
  assert.equal(delivered.id, accepted.id);
  assert.ok(configuration.BUCKET.writes.some(({options}) => options.onlyIf), "Queue admission must use a conditional storage write");
  const rejectedId = accepted.id === FIRST ? SECOND : FIRST;
  assert.equal((await create(configuration, rejectedId, "resume")).status, 409);
});

test("terminal results are immutable and release the queue", async () => {
  const configuration = env();
  await create(configuration);
  const result = {status: "completed", detail: "Started a fresh run", run_id: "run-2"};
  const response = await ack(configuration, FIRST, result);
  assert.equal(response.status, 200);
  const first = await response.json();
  assert.equal(first.ok, true);
  assert.equal(first.command.status, "completed");
  assert.equal((await (await next(configuration)).json()).command, null);
  const replay = await ack(configuration, FIRST, result);
  assert.equal(replay.status, 200);
  assert.deepEqual((await replay.json()).command, first.command);
  assert.equal((await ack(configuration, FIRST, {...result, status: "failed"})).status, 409);
  assert.equal((await ack(configuration, FIRST, {...result, run_id: "different-run"})).status, 409);
  assert.equal((await create(configuration, SECOND, "pause")).status, 202);
});

test("concurrent conflicting acknowledgements cannot overwrite a terminal outcome", async () => {
  const configuration = env();
  await create(configuration);
  const results = await Promise.all([
    ack(configuration, FIRST, {status: "completed", detail: "Applied"}),
    ack(configuration, FIRST, {status: "failed", detail: "Rejected by local controller"}),
  ]);
  assert.deepEqual(results.map((response) => response.status).sort(), [200, 409]);
  const winner = (await results.find((response) => response.status === 200).json()).command;
  assert.deepEqual((await (await status(configuration)).json()).command, winner);
});

test("an accepted queue entry survives a failed secondary record write without duplicate execution", async () => {
  const configuration = env();
  configuration.BUCKET.failOnceForKey = `observer/commands/${FIRST}.json`;
  assert.equal((await create(configuration)).status, 500);
  const pending = (await (await next(configuration)).json()).command;
  assert.equal(pending.id, FIRST, "The durable queue pointer is still the accepted command");
  const retry = await create(configuration);
  assert.equal(retry.status, 200);
  assert.deepEqual((await retry.json()).command, pending);
  assert.equal((await create(configuration, SECOND, "pause")).status, 409);
  assert.equal((await ack(configuration)).status, 200);
  assert.equal((await (await next(configuration)).json()).command, null);
});

test("recorded command feedback redacts both publisher and operator credentials", async () => {
  const configuration = env();
  await create(configuration);
  const response = await ack(configuration, FIRST, {status: "failed", detail: `Failure ${CONTROL} ${INGEST}`});
  assert.equal(response.status, 200);
  for (const body of [await response.text(), await (await status(configuration)).text()]) {
    assert.ok(!body.includes(CONTROL) && !body.includes(INGEST));
    assert.match(body, /\[REDACTED\]/);
  }
});

test("expired commands are not delivered and a late result cannot displace a newer command", async (context) => {
  const configuration = env();
  const original = (await (await create(configuration)).json()).command;
  context.mock.method(Date, "now", () => Date.parse(original.expires_at) + 1);
  assert.equal((await (await next(configuration)).json()).command, null);
  assert.equal((await (await status(configuration)).json()).command.status, "expired");
  const retry = await create(configuration);
  assert.equal(retry.status, 200);
  assert.equal((await retry.json()).command.status, "expired");
  assert.equal((await create(configuration, SECOND, "pause")).status, 202);
  assert.equal((await ack(configuration, FIRST, {status: "completed", detail: "Execution finished after expiry"})).status, 200);
  assert.equal((await (await status(configuration)).json()).command.status, "completed");
  assert.equal((await (await next(configuration)).json()).command.id, SECOND);
});

test("all supported actions are accepted after the previous action is acknowledged", async () => {
  const configuration = env();
  for (const [index, action] of ["start_new", "resume", "pause", "stop"].entries()) {
    const id = `18edca0c-bb5b-421a-8b9d-7ba33fb903b${index}`;
    assert.equal((await create(configuration, id, action)).status, 202);
    assert.equal((await ack(configuration, id, {status: "completed", detail: "Applied"})).status, 200);
  }
});

test("invalid command bodies and identifiers cannot enter the queue", async () => {
  const configuration = env();
  const invalidBodies = [
    {}, {id: FIRST}, {id: FIRST, action: "start"}, {id: FIRST, action: "delete_world"},
    {id: "../../secret", action: "pause"}, {id: FIRST, action: "pause", token: CONTROL},
    {id: FIRST, action: "pause", run_id: "historic-run"}, "{not-json",
  ];
  for (const body of invalidBodies) {
    const response = await handleApi(request("/api/commands", {method: "POST", token: CONTROL, body}), configuration);
    assert.equal(response.status, 400);
  }
  assert.equal((await create(configuration, FIRST.toUpperCase(), "pause")).status, 202);
  assert.equal((await (await next(configuration)).json()).command.id, FIRST);
  assert.equal((await status(configuration, "not-a-uuid")).status, 400);
  assert.equal((await status(configuration, SECOND)).status, 404);
});

test("result payloads are bounded, validated, and cannot acknowledge an unknown ID", async () => {
  const configuration = env();
  await create(configuration);
  for (const result of [
    {status: "pending", detail: "No"}, {status: "completed"},
    {status: "failed", detail: "x".repeat(501)},
    {status: "completed", detail: "Done", run_id: "../../secret"},
    {status: "completed", detail: "Done", action: "stop"},
  ]) assert.equal((await ack(configuration, FIRST, result)).status, 400);
  assert.equal((await ack(configuration, SECOND)).status, 404);
  assert.equal((await (await next(configuration)).json()).command.id, FIRST);
});

test("command routes enforce methods, JSON content types, and body size before storage", async () => {
  const configuration = env();
  assert.equal((await handleApi(request("/api/commands", {token: CONTROL}), configuration)).status, 405);
  assert.equal((await handleApi(request("/api/commands/next", {method: "POST", token: INGEST}), configuration)).status, 405);
  assert.equal((await handleApi(request(`/api/commands/${FIRST}/result`, {token: INGEST}), configuration)).status, 405);
  assert.equal((await handleApi(request("/api/commands", {method: "POST", token: CONTROL, body: {id: FIRST, action: "pause"}, contentType: "text/plain"}), configuration)).status, 415);
  assert.equal((await handleApi(request("/api/commands", {method: "POST", token: CONTROL, body: "x".repeat(1024 * 1024)}), configuration)).status, 413);
  assert.equal(configuration.BUCKET.records.size, 0);
});
