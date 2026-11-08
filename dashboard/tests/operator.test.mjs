import test from "node:test";
import assert from "node:assert/strict";
import {handleApi} from "../worker/api.mjs";

const ORIGIN = "https://observer.example";
const CONTROL = "dummy-private-control-capability-at-least-32-characters";
const INGEST = "dummy-independent-publisher-credential";
const PASSWORD = "dummy test password with spaces";
const HASH = [...new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(PASSWORD)))].map((byte) => byte.toString(16).padStart(2, "0")).join("");

class Bucket {
  records = new Map();
  revision = 0;
  denyWrites = false;
  async get(key) {
    const item = this.records.get(key);
    return item ? {...item, body: new TextEncoder().encode(item.text), json: async () => JSON.parse(item.text)} : null;
  }
  async put(key, text, options = {}) {
    const previous = this.records.get(key);
    if (this.denyWrites || options.onlyIf?.etagMatches && previous?.etag !== options.onlyIf.etagMatches || options.onlyIf?.etagDoesNotMatch === "*" && previous) return null;
    const item = {text, etag: String(++this.revision)};
    this.records.set(key, item);
    return item;
  }
}
function environment(extra = {}) {return {BUCKET: new Bucket(), CONTROL_TOKEN: CONTROL, INGEST_TOKEN: INGEST, OPERATOR_PASSWORD_SHA256: HASH, ...extra};}
function loginRequest(password = PASSWORD, headers = {}, body = {password}) {
  return new Request(`${ORIGIN}/api/operator/login`, {method: "POST", headers: {Origin: ORIGIN, "Sec-Fetch-Site": "same-origin", "Content-Type": "application/json", "CF-Connecting-IP": "192.0.2.1", ...headers}, body: typeof body === "string" ? body : JSON.stringify(body)});
}
const login = (env, password, headers, body) => handleApi(loginRequest(password, headers, body), env);

test("same-origin password exchange returns only the existing operator capability without caching", async () => {
  const env = environment();
  const response = await login(env);
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("Cache-Control"), "no-store");
  assert.deepEqual(await response.json(), {token: CONTROL});
  const command = await handleApi(new Request(`${ORIGIN}/api/commands`, {method: "POST", headers: {Authorization: `Bearer ${CONTROL}`, "Content-Type": "application/json"}, body: JSON.stringify({id: "d0a560fa-401d-481c-99aa-a540db217e75", action: "pause"})}), env);
  assert.equal(command.status, 202);
  const publicState = await (await handleApi(new Request(`${ORIGIN}/api/state`), env)).text();
  for (const privateValue of [PASSWORD, HASH, CONTROL]) assert.ok(!publicState.includes(privateValue));
  const stored = [...env.BUCKET.records.values()].map((item) => item.text).join("\n");
  for (const privateValue of [PASSWORD, HASH, CONTROL, "192.0.2.1"]) assert.ok(!stored.includes(privateValue));
});

test("incorrect passwords never receive a capability or reveal configured values", async () => {
  const env = environment();
  for (const password of [PASSWORD + " ", "incorrect", PASSWORD.toUpperCase()]) {
    const response = await login(env, password);
    assert.equal(response.status, 401);
    assert.deepEqual(await response.json(), {error: "Incorrect operator password"});
  }
});

test("password exchange requires same origin, POST, bounded JSON, and exactly one password field", async () => {
  const env = environment();
  for (const headers of [{Origin: "https://evil.example"}, {Origin: "null"}, {Origin: ""}, {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"}]) assert.equal((await login(env, PASSWORD, headers)).status, 403);
  const missingOrigin = loginRequest(); missingOrigin.headers.delete("Origin");
  assert.equal((await handleApi(missingOrigin, env)).status, 403);
  assert.equal((await handleApi(new Request(`${ORIGIN}/api/operator/login`), env)).status, 405);
  assert.equal((await login(env, PASSWORD, {"Content-Type": "text/plain"})).status, 415);
  assert.equal((await login(env, PASSWORD, {}, "x".repeat(2049))).status, 413);
  for (const body of [{}, {password: ""}, {password: 123}, {password: "é".repeat(513)}, {password: PASSWORD, token: CONTROL}, [], "{"]) assert.equal((await login(env, PASSWORD, {}, body)).status, 400);
  assert.equal(env.BUCKET.records.size, 0, "Malformed and cross-origin input never reaches credential or storage operations");
});

test("missing or invalid runtime secrets disable login without affecting existing private-link commands", async () => {
  for (const extra of [{OPERATOR_PASSWORD_SHA256: undefined}, {OPERATOR_PASSWORD_SHA256: "not-a-digest"}, {CONTROL_TOKEN: "short"}, {CONTROL_TOKEN: INGEST}, {CONTROL_TOKEN: "x".repeat(300)}]) {
    const env = environment(extra);
    const response = await login(env);
    assert.equal(response.status, 503);
    assert.equal(env.BUCKET.records.size, 0);
  }
  const env = environment({OPERATOR_PASSWORD_SHA256: undefined});
  const command = await handleApi(new Request(`${ORIGIN}/api/commands`, {method: "POST", headers: {Authorization: `Bearer ${CONTROL}`, "Content-Type": "application/json"}, body: JSON.stringify({id: "d0a560fa-401d-481c-99aa-a540db217e75", action: "pause"})}), env);
  assert.equal(command.status, 202);
});

test("client attempts are atomically bounded across concurrent requests and reset after one minute", async (context) => {
  const env = environment();
  let now = 1_800_000_000_000;
  context.mock.method(Date, "now", () => now);
  const responses = await Promise.all(Array.from({length: 16}, () => login(env, "wrong")));
  assert.ok(responses.filter((response) => response.status === 401).length <= 8);
  assert.ok(responses.every((response) => [401, 429].includes(response.status)));
  const blocked = await login(env);
  assert.equal(blocked.status, 429);
  assert.ok(Number(blocked.headers.get("Retry-After")) > 0);
  assert.equal(env.BUCKET.records.size, 1);
  const record = JSON.parse([...env.BUCKET.records.values()][0].text);
  assert.equal(record.total, 8);
  assert.equal(Object.keys(record.clients).length, 1);
  now += 60_001;
  assert.equal((await login(env)).status, 200);
  assert.equal(JSON.parse([...env.BUCKET.records.values()][0].text).total, 1);
});

test("global admission and storage stay bounded even with many client addresses", async () => {
  const env = environment();
  for (let index = 1; index <= 60; index++) assert.equal((await login(env, "wrong", {"CF-Connecting-IP": `192.0.2.${index}`})).status, 401);
  assert.equal((await login(env, PASSWORD, {"CF-Connecting-IP": "198.51.100.1"})).status, 429);
  assert.equal(env.BUCKET.records.size, 1);
  const record = JSON.parse([...env.BUCKET.records.values()][0].text);
  assert.equal(record.total, 60);
  assert.equal(Object.keys(record.clients).length, 60);
  assert.ok([...env.BUCKET.records.values()][0].text.length < 5000);
});

test("uncommitted attempt counters fail closed and forwarded address chains do not bypass the cap", async () => {
  const denied = environment(); denied.BUCKET.denyWrites = true;
  assert.equal((await login(denied)).status, 429);
  const env = environment();
  for (let index = 0; index < 8; index++) assert.equal((await login(env, "wrong", {"X-Forwarded-For": `192.0.2.${index}`})).status, 401);
  assert.equal((await login(env, PASSWORD, {"X-Forwarded-For": "203.0.113.99"})).status, 429);
});
