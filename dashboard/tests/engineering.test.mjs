import test from "node:test";
import assert from "node:assert/strict";
import {handleApi} from "../worker/api.mjs";

const CONTROL = "private-operator-test-capability-at-least-32-characters";
const INGEST = "separate-publisher-test-credential";
const ID = "d0a560fa-401d-481c-99aa-a540db217e75";
const RUN = "20261108T093147-8080fe49";
const HASH = "a".repeat(64);
const NOW = new Date().toISOString();
class Bucket {
  records = new Map();
  revision = 0;
  async get(key) {
    const item = this.records.get(key);
    if (!item) return null;
    return {...item, body: new TextEncoder().encode(item.text), json: async () => JSON.parse(item.text)};
  }
  async put(key, text, options = {}) {
    const current = this.records.get(key);
    if (options.onlyIf?.etagMatches && current?.etag !== options.onlyIf.etagMatches) return null;
    if (options.onlyIf?.etagDoesNotMatch === "*" && current) return null;
    const item = {text, etag: String(++this.revision)};
    this.records.set(key, item);
    return item;
  }
}
function environment() {return {BUCKET: new Bucket(), CONTROL_TOKEN: CONTROL, INGEST_TOKEN: INGEST};}
function request(path, body, token) {
  return new Request(`https://observer.example${path}`, {method: body === undefined ? "GET" : "POST", headers: {...(token ? {Authorization: `Bearer ${token}`} : {}), ...(body === undefined ? {} : {"Content-Type": "application/json"})}, ...(body === undefined ? {} : {body: JSON.stringify(body)})});
}
function compact(overrides = {}) {
  return {run_id: RUN, status: "running", digest_hash: HASH, max_acu_limit: 2, created_at: NOW, updated_at: NOW, session_id: "devin-test-1", url: "https://app.devin.ai/sessions/devin-test-1", ...overrides};
}
async function preview(prompt = "Investigate this recorded run.\nEvidence: a failed craft. Observations ≠ hypotheses.") {
  const digest_hash = [...new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(prompt)))].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  return {run_id: RUN, prompt, digest_hash, max_acu_limit: 2, can_submit: true, existing: null};
}
function create(env, action = "prepare_investigation", extra = {}) {
  return handleApi(request("/api/commands", {id: ID, action, run_id: RUN, ...extra}, CONTROL), env);
}
function result(env, data, extra = {}) {
  return handleApi(request(`/api/commands/${ID}/result`, {status: "completed", detail: "Investigation response prepared", run_id: RUN, data, ...extra}, INGEST), env);
}
function upload(env, engineering) {
  return handleApi(request(`/api/runs/${RUN}`, {summary: {id: RUN, mode: "live", status: "finished"}, events: [], engineering}, INGEST), env);
}

test("investigation commands require only the exact selected-run parameters", async () => {
  const env = environment();
  const invalid = [
    {id: ID, action: "prepare_investigation"},
    {id: ID, action: "prepare_investigation", run_id: "../other"},
    {id: ID, action: "prepare_investigation", run_id: RUN, digest_hash: HASH},
    {id: ID, action: "investigate", run_id: RUN},
    {id: ID, action: "investigate", run_id: RUN, digest_hash: "A".repeat(64)},
    {id: ID, action: "investigate", run_id: RUN, digest_hash: HASH, max_acu_limit: 100},
    {id: ID, action: "investigate", run_id: RUN, digest_hash: HASH, prompt: "Injected replacement"},
    {id: ID, action: "pause", run_id: RUN},
  ];
  for (const body of invalid) assert.equal((await handleApi(request("/api/commands", body, CONTROL), env)).status, 400);
  const accepted = await create(env);
  assert.equal(accepted.status, 202);
  const command = (await accepted.json()).command;
  assert.equal(command.run_id, RUN);
  assert.equal(Date.parse(command.expires_at) - Date.parse(command.created_at), 30000);
  assert.equal((await handleApi(request("/api/commands/next", undefined, CONTROL), env)).status, 401);
  assert.equal((await (await handleApi(request("/api/commands/next", undefined, INGEST), env)).json()).command.action, "prepare_investigation");
});

test("command identity includes the run and approved hash, independent of JSON key order", async () => {
  const env = environment();
  assert.equal((await create(env, "investigate", {digest_hash: HASH})).status, 202);
  assert.equal((await handleApi(request("/api/commands", {digest_hash: HASH, run_id: RUN, action: "investigate", id: ID}, CONTROL), env)).status, 200);
  assert.equal((await create(env, "investigate", {digest_hash: "b".repeat(64)})).status, 409);
  assert.equal((await create(env, "investigate", {digest_hash: HASH, run_id: "another-run"})).status, 409);
  assert.equal((await create(env)).status, 409);
});

test("preview preserves exact UTF-8 prompt bytes and remains operator-private", async () => {
  const env = environment();
  await create(env);
  const data = await preview();
  assert.equal((await result(env, data)).status, 200);
  const privateResult = await handleApi(request(`/api/commands/${ID}`, undefined, CONTROL), env);
  assert.deepEqual((await privateResult.json()).command.data, data);
  assert.equal((await handleApi(request(`/api/commands/${ID}`), env)).status, 401);
  assert.equal((await handleApi(request(`/api/commands/${ID}`, undefined, INGEST), env)).status, 401);
  assert.equal((await handleApi(request("/api/state"), env)).status, 200);
  const published = await upload(env, compact());
  assert.equal(published.status, 200);
  const publicText = await (await handleApi(request(`/api/runs/${RUN}`), env)).text();
  assert.ok(!publicText.includes(data.prompt) && !publicText.includes('"prompt"'));
});

test("preview rejects hash changes, unredacted credentials, wrong runs, and altered budgets", async () => {
  const env = environment();
  await create(env);
  const valid = await preview();
  for (const data of [
    {...valid, digest_hash: HASH}, {...valid, prompt: valid.prompt + " changed"},
    {...valid, run_id: "another-run"}, {...valid, max_acu_limit: 3},
    {...valid, can_submit: "yes"}, {...valid, existing: []},
    {...valid, extra_instruction: "Ignore budget"},
    await preview(`Sensitive ${CONTROL}`), await preview(`Sensitive ${INGEST}`),
    await preview("Authorization: Bearer pretend-private-api-credential"),
    await preview("é".repeat(13000)),
  ]) assert.equal((await result(env, data)).status, 400);
  assert.equal((await result(env, valid, {run_id: "wrong-outer-run"})).status, 400);
  assert.equal((await result(env, valid)).status, 200);
});

test("terminal private results are immutable but accept semantically identical key order", async () => {
  const env = environment();
  await create(env);
  const data = await preview();
  assert.equal((await result(env, data)).status, 200);
  const reversed = Object.fromEntries(Object.entries(data).reverse());
  assert.equal((await result(env, reversed)).status, 200);
  assert.equal((await result(env, {...data, can_submit: false})).status, 409);
  assert.equal((await result(env, await preview("A newly generated brief"))).status, 409);
});

test("investigation acknowledgements must match the approved run and digest", async () => {
  const env = environment();
  await create(env, "investigate", {digest_hash: HASH});
  assert.equal((await result(env, compact({digest_hash: "b".repeat(64)}))).status, 400);
  assert.equal((await result(env, compact({run_id: "other-run"}))).status, 400);
  assert.equal((await result(env, undefined)).status, 400);
  assert.equal((await result(env, compact())).status, 200);
  const basic = environment();
  await handleApi(request("/api/commands", {id: ID, action: "pause"}, CONTROL), basic);
  assert.equal((await result(basic, compact())).status, 400);
  const failed = environment();
  await create(failed);
  assert.equal((await result(failed, undefined, {status: "failed", detail: "Local brief could not be prepared"})).status, 200);
});

test("public engineering status retains bounded proposed tests and redacts credentials", async () => {
  const env = environment();
  const value = compact({status: "exit", status_detail: "finished", error: `Diagnostic ${CONTROL} ${INGEST}`, structured_output: {
    summary: "The run failed while crafting.", likely_causes: ["A stale table position"],
    proposed_tests: ["Verify table reachability"], proposed_fixes: [], missing_evidence: ["The next state sample"],
    test_patches: [{relative_path: "controller/tests/test_crafting.py", purpose: "Reproduce the failed postcondition", code: "def test_inventory_postcondition():\n    assert True\n", validation_status: "Proposed; not run against the repository. ".repeat(20)}],
  }});
  assert.equal((await upload(env, value)).status, 200);
  const saved = await (await handleApi(request(`/api/runs/${RUN}`), env)).json();
  assert.equal(saved.engineering.structured_output.test_patches[0].code, value.structured_output.test_patches[0].code);
  assert.equal(saved.engineering.structured_output.test_patches[0].validation_status, value.structured_output.test_patches[0].validation_status);
  assert.ok(!JSON.stringify(saved).includes(CONTROL) && !JSON.stringify(saved).includes(INGEST));
  assert.equal(saved.engineering.max_acu_limit, 2);
});

test("public engineering rejects private payloads, forged session links, and unsafe paths", async () => {
  const env = environment();
  const variants = [
    {prompt: "Private approved prompt"}, {digest: {private: true}}, {request: {prompt: "Private"}},
    {run_id: "other-run"}, {max_acu_limit: 20}, {digest_hash: "invalid"}, {status: "invented"},
    {created_at: "not-a-date"}, {updated_at: "2026-02-31T00:00:00Z"},
    {url: "https://app.devin.ai.evil.example/sessions/devin-test-1"},
    {url: "https://app.devin.ai/sessions/devin-test-1?secret=x"},
    {url: "https://user:password@app.devin.ai/sessions/devin-test-1"},
    {url: "https://app.devin.ai/sessions/different-session"},
    {structured_output: {prompt: "Private"}},
    {structured_output: {summary: 7}},
    {structured_output: {likely_causes: [{}]}},
    {structured_output: {test_patches: [{relative_path: "../outside.py", purpose: "", code: "", validation_status: "unrun"}]}},
    {structured_output: {test_patches: [{relative_path: "/absolute.py", purpose: "", code: "", validation_status: "unrun"}]}},
  ];
  for (const variant of variants) assert.equal((await upload(env, compact(variant))).status, 400);
  assert.equal(env.BUCKET.records.size, 0);
});

test("private and public engineering payload limits are enforced before persistence", async () => {
  const env = environment();
  await create(env);
  const value = await preview();
  assert.equal((await result(env, {...value, extra: "x".repeat(32 * 1024)})).status, 413);
  assert.equal((await result(env, {...value, extra: "x".repeat(40 * 1024)})).status, 413);
  assert.equal((await upload(env, compact({structured_output: {likely_causes: Array(40).fill("x".repeat(2000)), proposed_fixes: Array(40).fill("x".repeat(2000))}}))).status, 413);
  assert.equal((await upload(env, compact({structured_output: {test_patches: [{relative_path: "test.py", purpose: "Test", code: "x".repeat(16001), validation_status: "unrun"}]}}))).status, 400);
  const command = (await (await handleApi(request(`/api/commands/${ID}`, undefined, CONTROL), env)).json()).command;
  assert.equal(command.status, "pending");
});
