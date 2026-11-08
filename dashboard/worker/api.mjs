import {handleVideo} from "./video.mjs";

// All dashboard features are public. Only the local publisher can deliver,
// execute, or acknowledge queued commands and update recorded evidence.
const STATE_KEY = "observer/state.json";
const COMMAND_QUEUE_KEY = "observer/commands/current.json";
const COMMAND_TTL = 30_000;
const VOICE_CREATE_KEY = "observer/voice/current.json";
const COMMAND_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const COMMAND_ACTIONS = new Set(["start_new", "resume", "pause", "stop", "prepare_investigation", "investigate", "voice"]);
const ENGINEERING_ACTIONS = new Set(["prepare_investigation", "investigate"]);
const ENGINEERING_STATUSES = new Set(["submitting", "unknown", "failed", "new", "claimed", "running", "exit", "error", "suspended", "resuming", "created"]);
const DIGEST_HASH = /^[a-f0-9]{64}$/;
const MAX_COMMAND_DATA = 32 * 1024;
const MAX_ENGINEERING = 96 * 1024;
const MAX_INGEST = 2 * 1024 * 1024;
const MAX_METADATA = 256 * 1024;
const MAX_RUN = 8 * 1024 * 1024;
const RUN_ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$/;
const FEED_STATUSES = new Set(["online", "stale", "offline", "controller-offline"]);
const ROUTES = new Set(["from_scratch", "ruined_portal"]);
const STATE_FIELDS = new Set(["mode", "route", "launch", "status", "connected", "run_id", "elapsed_ms", "objective", "strategy", "expected_effect", "keys", "action", "holo_latency_ms", "jev_latency_ms", "metrics", "milestones", "events", "frame_url", "latency_history"]);
const SUMMARY_FIELDS = new Set(["id", "mode", "started_at", "status", "duration_ms", "metrics", "milestones", "config", "protocol", "updated_at"]);
const ENVELOPE_FIELDS = new Set(["state", "runs", "updated_at", "captured_at", "capture_seq", "feed_status", "source"]);
const EVENT_FIELDS = new Set(["seq", "type", "elapsed_ms", "timestamp", "data"]);
const SECRET_KEY = /^(?:authorization|headers|api[_-]?key|.*[_-]api[_-]?key|apikey|password|secret|.*[_-]secret|token|.*[_-]token|credentials|cookie|set-cookie)$/i;
const IMAGE_KEY = /^(?:image_base64|data_base64|assets|image_url|images?|screenshot_base64|frame_base64)$/i;
const SECRET_TEXT = /(?:\bBearer\s+[^\s,;"']+|\b(?:hk-|cog_|apikey_|sk[-_])[A-Za-z0-9_-]+)/gi;
const ASSIGNMENT_TEXT = /\b(?:[A-Za-z_]*api[_-]?key|(?:access_|refresh_|bridge_|ingest_)?token|password|authorization|secret)\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\s,;]+)/gi;

class ApiError extends Error {
  constructor(status, message, headers = {}) { super(message); this.status = status; this.headers = headers; }
}

function json(value, status = 200, extraHeaders = {}) {
  return new Response(JSON.stringify(value), { status, headers: {
    "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff", ...extraHeaders,
  } });
}

function object(value, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new ApiError(400, `Invalid ${label}`);
  return value;
}

function knownFields(value, fields, label) {
  object(value, label);
  if (Object.keys(value).some((key) => !fields.has(key))) throw new ApiError(400, `Unexpected ${label} field`);
}

function finiteNumber(value, label, { nullable = false, integer = false } = {}) {
  if (nullable && value === null) return null;
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || (integer && !Number.isSafeInteger(value))) throw new ApiError(400, `Invalid ${label}`);
  return value;
}

function timestamp(value, label, nullable = false) {
  if (nullable && value === null) return null;
  if (typeof value !== "string" || value.length > 40 || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|\+00:00)$/.test(value) || !Number.isFinite(Date.parse(value))) throw new ApiError(400, `Invalid ${label}`);
  if (new Date(Date.parse(value)).toISOString().slice(0, 19) !== value.slice(0, 19)) throw new ApiError(400, `Invalid ${label}`);
  return value; // Preserve the capture's original timestamp, not upload time.
}

function safeText(value, token) {
  if (value.length > 500_000) throw new ApiError(400, "Text field too large");
  let text = value.replace(SECRET_TEXT, "[REDACTED]").replace(ASSIGNMENT_TEXT, "[REDACTED]");
  text = text.replace(/data:image\/[^\s"']+/gi, "[image removed]");
  if (/^[A-Za-z0-9+/=\r\n]{1024,}$/.test(text)) return "[binary payload removed]";
  if (token) text = text.split(token).join("[REDACTED]");
  return text;
}

function sanitize(value, token, depth = 0, budget = { nodes: 0 }) {
  if (++budget.nodes > 180_000 || depth > 24) throw new ApiError(400, "Payload is too complex");
  if (value === null || typeof value === "boolean") return value;
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new ApiError(400, "Invalid numeric value");
    return value;
  }
  if (typeof value === "string") return safeText(value, token);
  if (Array.isArray(value)) return value.map((item) => sanitize(item, token, depth + 1, budget)).filter((item) => item !== undefined);
  object(value, "payload value");
  if (["image", "image_url", "input_image"].includes(value.type)) return undefined;
  const clean = Object.create(null);
  for (const [key, item] of Object.entries(value)) {
    if (["__proto__", "constructor", "prototype"].includes(key) || IMAGE_KEY.test(key)) continue;
    if (SECRET_KEY.test(key)) { clean[key] = "[REDACTED]"; continue; }
    const result = sanitize(item, token, depth + 1, budget);
    if (result !== undefined) clean[key] = result;
  }
  return clean;
}

function metrics(value) {
  object(value, "metrics");
  for (const item of Object.values(value)) finiteNumber(item, "metric");
}

function boundedJson(value, maximum, label) {
  if (new TextEncoder().encode(JSON.stringify(value)).byteLength > maximum) throw new ApiError(413, `${label} exceeds size limit`);
}
function boundedText(value, maximum, label) {
  if (typeof value !== "string" || value.length > maximum) throw new ApiError(400, `Invalid ${label}`);
}
function redactBoth(value, env) {
  return sanitize(sanitize(value, env.INGEST_TOKEN), env.CONTROL_TOKEN);
}
function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
  return JSON.stringify(value);
}
function engineering(value, env, runId, findings = true) {
  const fields = new Set(["run_id", "status", "digest_hash", "max_acu_limit", "created_at", "updated_at", "session_id", "url", "status_detail", "error", ...(findings ? ["structured_output"] : [])]);
  knownFields(value, fields, "engineering status");
  boundedJson(value, findings ? MAX_ENGINEERING : MAX_COMMAND_DATA, "Engineering status");
  if (value.run_id !== runId || !ENGINEERING_STATUSES.has(value.status) || typeof value.digest_hash !== "string" || !DIGEST_HASH.test(value.digest_hash) || value.max_acu_limit !== 2) throw new ApiError(400, "Invalid engineering identity or budget");
  timestamp(value.created_at, "engineering creation timestamp");
  timestamp(value.updated_at, "engineering update timestamp");
  if (value.session_id !== undefined && (typeof value.session_id !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(value.session_id))) throw new ApiError(400, "Invalid Devin session ID");
  if (value.url !== undefined) {
    const match = typeof value.url === "string" && value.url.match(/^https:\/\/app\.devin\.ai\/sessions\/([A-Za-z0-9_-]{1,128})$/);
    if (!match || (value.session_id !== undefined && match[1] !== value.session_id)) throw new ApiError(400, "Invalid Devin session URL");
  }
  if (value.status_detail !== undefined) boundedText(value.status_detail, 100, "engineering status detail");
  if (value.error !== undefined) boundedText(value.error, 500, "engineering error");
  if (value.structured_output !== undefined) {
    const output = value.structured_output;
    knownFields(output, new Set(["summary", "likely_causes", "proposed_tests", "proposed_fixes", "missing_evidence", "test_patches"]), "engineering findings");
    if (output.summary !== undefined) boundedText(output.summary, 8000, "engineering summary");
    for (const key of ["likely_causes", "proposed_tests", "proposed_fixes", "missing_evidence"]) {
      if (output[key] === undefined) continue;
      if (!Array.isArray(output[key]) || output[key].length > 40) throw new ApiError(400, "Invalid engineering findings list");
      for (const item of output[key]) boundedText(item, 2000, "engineering finding");
    }
    if (output.test_patches !== undefined) {
      if (!Array.isArray(output.test_patches) || output.test_patches.length > 2) throw new ApiError(400, "Invalid proposed test patches");
      for (const patch of output.test_patches) {
        knownFields(patch, new Set(["relative_path", "purpose", "code", "validation_status"]), "proposed test patch");
        if (typeof patch.relative_path !== "string" || patch.relative_path.length > 240 || !/^[A-Za-z0-9_./-]+$/.test(patch.relative_path) || patch.relative_path.startsWith("/") || patch.relative_path.split("/").some((part) => !part || part === "." || part === "..")) throw new ApiError(400, "Invalid proposed test path");
        boundedText(patch.purpose, 2000, "proposed test purpose");
        boundedText(patch.code, 16000, "proposed test code");
        boundedText(patch.validation_status, 2000, "proposed test validation status");
      }
    }
  }
  return redactBoth(value, env);
}
function commandParameters(body, env) {
  if (body.action === "voice") {
    knownFields(body, new Set(["id", "action", "text", "session_id", "utterance_id", "run_id"]), "voice command");
    if (typeof body.text !== "string" || !body.text.trim() || body.text.length > 1000 || typeof body.session_id !== "string" || (body.session_id !== "typed" && !COMMAND_ID.test(body.session_id)) || typeof body.utterance_id !== "string" || !/^[A-Za-z0-9_.:-]{1,128}$/.test(body.utterance_id) || (body.run_id !== null && (typeof body.run_id !== "string" || !RUN_ID.test(body.run_id)))) throw new ApiError(400, "Invalid voice command");
    return {text: redactBoth(body.text, env), session_id: body.session_id.toLowerCase(), utterance_id: body.utterance_id, run_id: body.run_id};
  }
  if (!ENGINEERING_ACTIONS.has(body.action)) {
    knownFields(body, new Set(["id", "action"]), "command");
    return {};
  }
  knownFields(body, new Set(["id", "action", "run_id", ...(body.action === "investigate" ? ["digest_hash"] : [])]), "command");
  if (typeof body.run_id !== "string" || !RUN_ID.test(body.run_id)) throw new ApiError(400, "Invalid investigation run ID");
  if (body.action === "investigate" && (typeof body.digest_hash !== "string" || !DIGEST_HASH.test(body.digest_hash))) throw new ApiError(400, "Invalid approved digest hash");
  return {run_id: body.run_id, ...(body.action === "investigate" ? {digest_hash: body.digest_hash} : {})};
}
async function commandData(value, command, env) {
  if (command.action === "voice") {
    knownFields(value, new Set(["intent", "reply", "guidance_id"]), "voice result");
    if (!["pause", "stop", "resume", "guidance", "question"].includes(value.intent) || typeof value.reply !== "string" || value.reply.length > 2000) throw new ApiError(400, "Invalid voice result");
    if (value.guidance_id !== undefined) commandId(value.guidance_id);
    return redactBoth(value, env);
  }
  if (!ENGINEERING_ACTIONS.has(command.action)) throw new ApiError(400, "This command cannot return investigation data");
  boundedJson(value, MAX_COMMAND_DATA, "Command data");
  if (command.action === "investigate") {
    const result = engineering(value, env, command.run_id, false);
    if (result.digest_hash !== command.digest_hash) throw new ApiError(400, "Investigation result does not match the approved digest");
    return result;
  }
  knownFields(value, new Set(["run_id", "prompt", "digest_hash", "max_acu_limit", "can_submit", "existing"]), "investigation preview");
  if (value.run_id !== command.run_id || typeof value.prompt !== "string" || !value.prompt.trim() || new TextEncoder().encode(value.prompt).byteLength > 24 * 1024 || typeof value.digest_hash !== "string" || !DIGEST_HASH.test(value.digest_hash) || value.max_acu_limit !== 2 || typeof value.can_submit !== "boolean" || value.existing === undefined) throw new ApiError(400, "Invalid investigation preview");
  // The approved text must be byte-for-byte the controller's persisted brief.
  // Reject an unsafe preview instead of silently changing the text behind its hash.
  if (redactBoth(value.prompt, env) !== value.prompt) throw new ApiError(400, "Investigation preview must be redacted before approval");
  const actual = [...new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value.prompt)))].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  if (actual !== value.digest_hash) throw new ApiError(400, "Investigation preview does not match its digest");
  return {...value, existing: value.existing === null ? null : engineering(value.existing, env, command.run_id, false)};
}

function events(value, token, max = 30_000) {
  if (!Array.isArray(value) || value.length > max) throw new ApiError(400, "Invalid events");
  return value.map((event) => {
    knownFields(event, EVENT_FIELDS, "event");
    finiteNumber(event.seq, "event sequence", { integer: true });
    finiteNumber(event.elapsed_ms, "event elapsed time");
    if (typeof event.type !== "string" || !/^[A-Za-z0-9_.-]{1,80}$/.test(event.type)) throw new ApiError(400, "Invalid event type");
    if (event.timestamp !== undefined) timestamp(event.timestamp, "event timestamp");
    object(event.data, "event data");
    return sanitize(event, token);
  });
}

function summary(value, token, expectedId) {
  knownFields(value, SUMMARY_FIELDS, "summary");
  if (typeof value.id !== "string" || !RUN_ID.test(value.id) || (expectedId && value.id !== expectedId)) throw new ApiError(400, "Invalid summary run ID");
  if (value.mode !== undefined && !["demo", "live"].includes(value.mode)) throw new ApiError(400, "Invalid summary mode");
  if (value.status !== undefined && !["idle", "running", "paused", "error", "finished", "interrupted"].includes(value.status)) throw new ApiError(400, "Invalid summary status");
  if (value.started_at !== undefined) timestamp(value.started_at, "run timestamp");
  if (value.updated_at !== undefined) timestamp(value.updated_at, "run update timestamp");
  if (value.duration_ms !== undefined) finiteNumber(value.duration_ms, "run duration");
  if (value.metrics !== undefined) metrics(value.metrics);
  if (value.milestones !== undefined && (!Array.isArray(value.milestones) || value.milestones.length > 100)) throw new ApiError(400, "Invalid milestones");
  if (value.config?.route !== undefined && !ROUTES.has(value.config.route)) throw new ApiError(400, "Invalid run route");
  return sanitize(value, token);
}

function launchState(value) {
  if (value === null) return;
  const fields = new Set(["id", "status", "stage", "started_at", "updated_at", "world_name", "run_id", "detail"]);
  knownFields(value, fields, "launch state");
  if ([...fields].some((key) => !Object.hasOwn(value, key))) throw new ApiError(400, "Incomplete launch state");
  commandId(value.id);
  if (!["preparing", "running", "failed", "cancelled", "interrupted"].includes(value.status) || !["saving", "copying", "loading", "verifying", "starting", "running", "failed", "cancelled", "interrupted"].includes(value.stage)) throw new ApiError(400, "Invalid launch stage");
  timestamp(value.started_at, "launch timestamp");
  timestamp(value.updated_at, "launch update timestamp");
  if (Date.parse(value.updated_at) < Date.parse(value.started_at)) throw new ApiError(400, "Invalid launch timestamp order");
  if (value.world_name !== null && (typeof value.world_name !== "string" || !value.world_name.trim() || value.world_name.length > 160 || /[/\\\u0000-\u001f]/.test(value.world_name))) throw new ApiError(400, "Invalid public world name");
  if (value.run_id !== null && (typeof value.run_id !== "string" || !RUN_ID.test(value.run_id))) throw new ApiError(400, "Invalid launch run identity");
  boundedText(value.detail, 300, "launch detail");
  // This public payload is presentation state, never an execution plan or
  // private save location. The launcher supplies generic, user-facing errors.
  if (/(?:^|\s)(?:\/(?:workspace|home|tmp|var|Users|mnt)\/|[A-Za-z]:\\)/.test(value.detail)) throw new ApiError(400, "Launch detail must not contain private paths");
}

function state(value, token) {
  if (value === null) return null;
  knownFields(value, STATE_FIELDS, "state");
  if (!["demo", "live"].includes(value.mode)) throw new ApiError(400, "Invalid state mode");
  if (value.route !== undefined && !ROUTES.has(value.route)) throw new ApiError(400, "Invalid state route");
  if (value.launch !== undefined) launchState(value.launch);
  if (!["idle", "running", "paused", "error", "unavailable"].includes(value.status)) throw new ApiError(400, "Invalid state status");
  if (value.connected !== undefined && typeof value.connected !== "boolean") throw new ApiError(400, "Invalid connection state");
  for (const key of ["objective", "strategy", "expected_effect"]) if (value[key] !== undefined && (typeof value[key] !== "string" || value[key].length > 5000)) throw new ApiError(400, "Invalid state text");
  if (value.keys !== undefined && (!Array.isArray(value.keys) || value.keys.length > 20 || value.keys.some((key) => typeof key !== "string" || key.length > 30))) throw new ApiError(400, "Invalid input state");
  if (value.action !== undefined && value.action !== null) object(value.action, "action state");
  if (value.latency_history !== undefined) {
    knownFields(value.latency_history, new Set(["holo", "jev"]), "latency history");
    for (const samples of Object.values(value.latency_history)) {
      if (!Array.isArray(samples) || samples.length > 1000) throw new ApiError(400, "Invalid latency history");
      for (const sample of samples) finiteNumber(sample, "latency sample");
    }
  }
  if (value.run_id !== undefined && value.run_id !== null && (typeof value.run_id !== "string" || !RUN_ID.test(value.run_id))) throw new ApiError(400, "Invalid state run ID");
  for (const key of ["elapsed_ms", "holo_latency_ms", "jev_latency_ms"]) if (value[key] !== undefined) finiteNumber(value[key], key, { nullable: key !== "elapsed_ms" });
  if (value.metrics !== undefined) metrics(value.metrics);
  if (value.events !== undefined) events(value.events, token, 2000);
  if (value.milestones !== undefined && (!Array.isArray(value.milestones) || value.milestones.length > 100)) throw new ApiError(400, "Invalid milestones");
  const result = sanitize(value, token);
  // Local frame paths are not available remotely. The observer uses the captured window.
  if ("frame_url" in result) result.frame_url = null;
  return result;
}

function envelope(value, token) {
  knownFields(value, ENVELOPE_FIELDS, "metadata");
  if (value.source !== "minecraft-window") throw new ApiError(400, "Invalid frame source");
  if (!FEED_STATUSES.has(value.feed_status)) throw new ApiError(400, "Invalid feed status");
  timestamp(value.updated_at, "update timestamp");
  timestamp(value.captured_at, "capture timestamp", true);
  finiteNumber(value.capture_seq, "capture sequence", { nullable: true, integer: true });
  if ((value.capture_seq === null) !== (value.captured_at === null)) throw new ApiError(400, "Incomplete capture identity");
  const now = Date.now();
  if (Date.parse(value.updated_at) > now + 5000 || (value.captured_at && (Date.parse(value.captured_at) > now + 5000 || Date.parse(value.captured_at) > Date.parse(value.updated_at) + 1000))) throw new ApiError(400, "Invalid future timestamp");
  if (!Array.isArray(value.runs) || value.runs.length > 256) throw new ApiError(400, "Invalid run list");
  return {
    state: state(value.state, token), runs: value.runs.map((run) => summary(run, token)),
    updated_at: value.updated_at, captured_at: value.captured_at, capture_seq: value.capture_seq,
    feed_status: value.feed_status, source: value.source,
  };
}

async function authorized(request, env) {
  const secret = env.INGEST_TOKEN;
  if (typeof secret !== "string" || secret.length < 16) throw new ApiError(503, "Publisher authentication is not configured");
  const header = request.headers.get("Authorization") || "";
  const supplied = header.startsWith("Bearer ") ? header.slice(7) : "";
  const encode = new TextEncoder();
  const [actual, expected] = await Promise.all([supplied, secret].map(async (token) => new Uint8Array(await crypto.subtle.digest("SHA-256", encode.encode(token)))));
  let difference = 0;
  for (let index = 0; index < 32; index++) difference |= actual[index] ^ expected[index];
  if (difference !== 0) throw new ApiError(401, "Invalid publisher credentials");
}

function sameOrigin(request) {
  const site = request.headers.get("Sec-Fetch-Site");
  if (request.headers.get("Origin") !== new URL(request.url).origin || (site && site !== "same-origin")) throw new ApiError(403, "Dashboard actions require a same-origin request");
}

function commandId(value) {
  if (typeof value !== "string" || !COMMAND_ID.test(value)) throw new ApiError(400, "Invalid command ID");
  return value.toLowerCase();
}
function commandStatus(command) {
  return command?.status === "pending" && Date.now() >= Date.parse(command.expires_at) ? {...command, status: "expired"} : command;
}
async function commandRecord(env, id) {
  const item = await env.BUCKET.get(`observer/commands/${id}.json`);
  if (item) return {item, command: await item.json()};
  const queue = await env.BUCKET.get(COMMAND_QUEUE_KEY);
  const command = queue ? await queue.json() : null;
  return {item: null, command: command?.id === id ? command : null};
}
async function commandBody(request, maximum = 4096) {
  if (!request.headers.get("Content-Type")?.toLowerCase().startsWith("application/json")) throw new ApiError(415, "Expected JSON");
  const raw = await bytes(request, maximum);
  return parseJson(new TextDecoder().decode(raw));
}

async function voiceSession(request, env) {
  sameOrigin(request);
  if (typeof env.GLADIA_API_KEY !== "string" || env.GLADIA_API_KEY.length < 16) throw new ApiError(503, "Voice transcription is not configured");
  const body = await commandBody(request, 1024);
  knownFields(body, new Set(["id"]), "voice session request");
  const id = commandId(body.id);
  const key = `observer/voice/requests/${id}.json`;
  if (await env.BUCKET.get(key)) throw new ApiError(409, "This voice session was already requested. Do not retry it automatically.");
  const previous = await env.BUCKET.get(VOICE_CREATE_KEY);
  const active = previous ? await previous.json() : null;
  const now = Date.now();
  if (active && now < active.expires_at) throw new ApiError(429, "Voice session creation is busy. Try again shortly.", {"Retry-After": String(Math.max(1, Math.ceil((active.expires_at - now) / 1000)))});
  const lease = {id, status: "requested", created_at: now, expires_at: now + 30_000};
  const metadata = {httpMetadata: {contentType: "application/json", cacheControl: "no-store"}};
  const admitted = await env.BUCKET.put(VOICE_CREATE_KEY, JSON.stringify(lease), {...metadata, onlyIf: previous ? {etagMatches: previous.etag} : {etagDoesNotMatch: "*"}});
  if (!admitted) throw new ApiError(409, "Another voice session request was accepted first");
  // Persist the claim before the billable call. A timeout, restart, or failed
  // response delivery cannot cause this request ID to create another session.
  const receipt = await env.BUCKET.put(key, JSON.stringify(lease), {...metadata, onlyIf: {etagDoesNotMatch: "*"}});
  if (!receipt) throw new ApiError(409, "This voice session was already requested");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 8000);
  let result, failure;
  let stage = "provider_fetch", providerStatus = null, diagnostic = null;
  try {
    // Workers rejects redirect:"error" before sending a request. Manual mode
    // preserves the same credential boundary: every 3xx is rejected below.
    const response = await fetch("https://api.gladia.io/v2/live", {method: "POST", redirect: "manual", signal: controller.signal, headers: {"Content-Type": "application/json", "x-gladia-key": env.GLADIA_API_KEY}, body: JSON.stringify({
      encoding: "wav/pcm", bit_depth: 16, sample_rate: 16000, channels: 1, model: "solaria-1", endpointing: 0.3, maximum_duration_without_endpointing: 5,
      messages_config: {receive_partial_transcripts: true, receive_final_transcripts: true, receive_lifecycle_events: true, receive_errors: true},
    })});
    providerStatus = response.status;
    stage = "provider_http";
    if (!response.ok) throw new Error("Transcription provider rejected session creation");
    stage = "provider_body";
    const raw = await bytes(response, 64 * 1024);
    stage = "provider_json";
    const data = parseJson(new TextDecoder().decode(raw));
    stage = "provider_id";
    if (!COMMAND_ID.test(data.id || "")) throw new Error("Invalid transcription session identity");
    stage = "provider_url";
    const url = typeof data.url === "string" && data.url.length <= 4096 ? new URL(data.url) : null;
    if (!url || url.protocol !== "wss:" || (url.hostname !== "gladia.io" && !url.hostname.endsWith(".gladia.io")) || url.username || url.password || url.port || url.hash) throw new Error("Invalid transcription session response");
    stage = "provider_url_credentials";
    if ([env.GLADIA_API_KEY, env.INGEST_TOKEN, env.CONTROL_TOKEN].some((secret) => typeof secret === "string" && secret.length && data.url.includes(secret))) throw new Error("Transcription URL contains a permanent credential");
    result = {id: data.id.toLowerCase(), url: url.href, sample_rate: 16000};
  } catch {
    const code = controller.signal.aborted ? "provider_timeout" : stage === "provider_http" ? `provider_http_${providerStatus}` : stage;
    diagnostic = {stage: code, provider_status: providerStatus};
    // Only allowlisted stages and numeric status leave this catch. Provider
    // response bodies, exception messages, headers, and URLs can hold secrets.
    console.warn("voice_session_unconfirmed", JSON.stringify(diagnostic));
    failure = new ApiError(controller.signal.aborted ? 504 : 502, `Voice session creation was not confirmed (${code}). It will not be retried automatically.`);
  } finally { clearTimeout(timer); }
  // No WebSocket URL, API key, raw provider payload, or audio is persisted.
  const completed = {...lease, status: result ? "created" : "unconfirmed", completed_at: Date.now(), expires_at: Date.now() + 10_000, ...(diagnostic ? {diagnostic} : {})};
  await env.BUCKET.put(key, JSON.stringify(completed), {...metadata, onlyIf: {etagMatches: receipt.etag}});
  await env.BUCKET.put(VOICE_CREATE_KEY, JSON.stringify(completed), {...metadata, onlyIf: {etagMatches: admitted.etag}});
  if (failure) throw failure;
  return json(result);
}
async function saveCommand(env, command, item) {
  return env.BUCKET.put(`observer/commands/${command.id}.json`, JSON.stringify(command), {
    httpMetadata: {contentType: "application/json", cacheControl: "no-store"},
    onlyIf: item ? {etagMatches: item.etag} : {etagDoesNotMatch: "*"},
  });
}
async function createCommand(request, env) {
  sameOrigin(request);
  const body = await commandBody(request);
  object(body, "command");
  const id = commandId(body.id);
  if (!COMMAND_ACTIONS.has(body.action)) throw new ApiError(400, "Invalid command action");
  const parameters = commandParameters(body, env);
  const existing = await commandRecord(env, id);
  if (existing.command) {
    if (existing.command.action !== body.action || Object.entries(parameters).some(([key, value]) => existing.command[key] !== value)) throw new ApiError(409, "Command ID already belongs to another action or parameters");
    return json({command: commandStatus(existing.command)});
  }
  const queue = await env.BUCKET.get(COMMAND_QUEUE_KEY);
  const previous = queue ? await queue.json() : null;
  if (previous) {
    const current = (await commandRecord(env, previous.id)).command || previous;
    if (commandStatus(current).status === "pending") throw new ApiError(409, "Another command is awaiting a result");
    // Preserve identity if an earlier request stopped after committing its queue
    // pointer. Never overwrite an acknowledgement arriving at the same time.
    await saveCommand(env, current, null);
  }
  const now = Date.now();
  const command = {id, action: body.action, ...parameters, status: "pending", created_at: new Date(now).toISOString(), expires_at: new Date(now + COMMAND_TTL).toISOString()};
  const committed = await env.BUCKET.put(COMMAND_QUEUE_KEY, JSON.stringify(command), {
    httpMetadata: {contentType: "application/json", cacheControl: "no-store"},
    onlyIf: queue ? {etagMatches: queue.etag} : {etagDoesNotMatch: "*"},
  });
  if (!committed) throw new ApiError(409, "Another command was accepted first");
  await saveCommand(env, command, null);
  return json({command}, 202);
}
async function nextCommand(request, env) {
  await authorized(request, env);
  const queue = await env.BUCKET.get(COMMAND_QUEUE_KEY);
  if (!queue) return json({command: null});
  const pointer = await queue.json();
  const current = commandStatus((await commandRecord(env, pointer.id)).command || pointer);
  return json({command: current.status === "pending" ? current : null});
}
async function readCommand(request, env, id) {
  const {command} = await commandRecord(env, commandId(id));
  if (!command) throw new ApiError(404, "Command is unavailable");
  return json({command: commandStatus(command)});
}
async function commandResult(request, env, id) {
  await authorized(request, env);
  id = commandId(id);
  const body = await commandBody(request, MAX_COMMAND_DATA + 4096);
  knownFields(body, new Set(["status", "detail", "run_id", "data"]), "command result");
  if (!["completed", "failed"].includes(body.status) || typeof body.detail !== "string" || body.detail.length > 500 || (body.run_id != null && (typeof body.run_id !== "string" || !RUN_ID.test(body.run_id)))) throw new ApiError(400, "Invalid command result");
  const {item, command} = await commandRecord(env, id);
  if (!command) throw new ApiError(404, "Command is unavailable");
  if (ENGINEERING_ACTIONS.has(command.action) && body.run_id != null && body.run_id !== command.run_id) throw new ApiError(400, "Investigation result belongs to another run");
  if (ENGINEERING_ACTIONS.has(command.action) && body.status === "completed" && body.data == null) throw new ApiError(400, "Completed investigation command requires result data");
  if (command.action === "voice" && body.run_id !== undefined && body.run_id !== command.run_id) throw new ApiError(400, "Voice result belongs to another run");
  if (command.action === "voice" && body.status === "completed" && body.data == null) throw new ApiError(400, "Completed voice command requires result data");
  const data = body.data === undefined ? undefined : await commandData(body.data, command, env);
  const detail = safeText(body.detail, env.INGEST_TOKEN).split(env.CONTROL_TOKEN || "\u0000").join("[REDACTED]");
  const run_id = ENGINEERING_ACTIONS.has(command.action) || command.action === "voice" ? command.run_id : body.run_id ?? null;
  if (["completed", "failed"].includes(command.status)) {
    if (command.status !== body.status || command.detail !== detail || command.run_id !== run_id || canonical(command.data) !== canonical(data)) throw new ApiError(409, "A command result is immutable");
    return json({ok: true, command});
  }
  // Expiry prevents new execution, but a delayed acknowledgement may still
  // report the actual outcome of an operation that began before its deadline.
  const completed = {...command, status: body.status, detail, run_id, ...(data === undefined ? {} : {data}), completed_at: new Date().toISOString()};
  if (!await saveCommand(env, completed, item)) throw new ApiError(409, "Command result changed concurrently");
  return json({ok: true, command: completed});
}

async function bytes(request, maximum) {
  const declared = request.headers.get("Content-Length");
  if (declared !== null && (!/^\d+$/.test(declared) || Number(declared) > maximum)) throw new ApiError(413, "Request exceeds upload limit");
  const reader = request.body?.getReader();
  if (!reader) throw new ApiError(400, "Empty request");
  const chunks = [];
  let length = 0;
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    length += value.byteLength;
    if (length > maximum) { await reader.cancel(); throw new ApiError(413, "Request exceeds upload limit"); }
    chunks.push(value);
  }
  const result = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) { result.set(chunk, offset); offset += chunk.byteLength; }
  return result;
}

function parseJson(value) {
  try { return JSON.parse(value); } catch { throw new ApiError(400, "Invalid JSON"); }
}

async function storedState(bucket) {
  const item = await bucket.get(STATE_KEY);
  return item ? { item, value: await item.json() } : { item: null, value: null };
}

function publicState(value) {
  if (!value) return { state: null, runs: [], updated_at: null, captured_at: null, capture_seq: null, feed_status: "offline", source: "minecraft-window" };
  const result = Object.fromEntries([...ENVELOPE_FIELDS].map((key) => [key, value[key]]));
  result.received_at = value.received_at;
  const now = Date.now();
  const captureAge = now - Date.parse(value.captured_at);
  const updateAge = now - Date.parse(value.updated_at);
  if (result.feed_status === "online" && (!value.captured_at || captureAge > 15_000 || updateAge > 15_000 || captureAge < -2000 || updateAge < -2000)) result.feed_status = value.captured_at ? "stale" : "offline";
  return result;
}

async function ingest(request, env) {
  await authorized(request, env);
  if (!request.headers.get("Content-Type")?.toLowerCase().startsWith("multipart/form-data;")) throw new ApiError(415, "Expected multipart form data");
  const raw = await bytes(request, MAX_INGEST);
  let form;
  try { form = await new Request(request.url, { method: "POST", headers: { "Content-Type": request.headers.get("Content-Type") }, body: raw }).formData(); }
  catch { throw new ApiError(400, "Invalid multipart form"); }
  if ([...form.keys()].some((key) => key !== "metadata" && key !== "frame") || form.getAll("metadata").length !== 1 || form.getAll("frame").length > 1) throw new ApiError(400, "Invalid multipart fields");
  const metadata = form.get("metadata");
  const metadataText = typeof metadata === "string" ? metadata : metadata && metadata.size <= MAX_METADATA ? await metadata.text() : null;
  if (metadataText === null || new TextEncoder().encode(metadataText).length > MAX_METADATA) throw new ApiError(413, "Metadata exceeds upload limit");
  const next = envelope(parseJson(metadataText), env.INGEST_TOKEN);
  const current = await storedState(env.BUCKET);
  const upload = form.get("frame");
  if (current.value) {
    if (Date.parse(next.updated_at) <= Date.parse(current.value.updated_at)) throw new ApiError(409, "Stale metadata update");
    // A restarted publisher may have no local capture yet. Preserve the last
    // displayed capture without treating its new offline heartbeat as a frame.
    if (next.capture_seq === null && upload === null && ["offline", "controller-offline"].includes(next.feed_status)) {
      next.capture_seq = current.value.capture_seq;
      next.captured_at = current.value.captured_at;
    }
    if (current.value.capture_seq !== null && (next.capture_seq === null || next.capture_seq < current.value.capture_seq)) throw new ApiError(409, "Stale frame capture");
    if (next.capture_seq === current.value.capture_seq && next.captured_at !== current.value.captured_at) throw new ApiError(409, "Capture identity cannot be restamped");
    if (next.capture_seq !== null && next.capture_seq > current.value.capture_seq && Date.parse(next.captured_at) <= Date.parse(current.value.captured_at)) throw new ApiError(409, "Stale capture timestamp");
  }
  let selected = current.value?._frame || null;
  if (upload !== null) {
    if (typeof upload === "string" || upload.type !== "image/jpeg" || upload.size < 4 || next.capture_seq === null) throw new ApiError(400, "Invalid JPEG upload");
    const frame = new Uint8Array(await upload.arrayBuffer());
    if (frame[0] !== 0xff || frame[1] !== 0xd8 || frame[2] !== 0xff || frame[frame.length - 2] !== 0xff || frame[frame.length - 1] !== 0xd9) throw new ApiError(400, "Invalid JPEG data");
    const hash = [...new Uint8Array(await crypto.subtle.digest("SHA-256", frame))].map((byte) => byte.toString(16).padStart(2, "0")).join("");
    if (next.capture_seq === current.value?.capture_seq && selected?.sha256 !== hash) throw new ApiError(409, "Capture sequence already contains a different frame");
    selected = { key: `observer/frames/${next.capture_seq}-${hash.slice(0, 16)}.jpg`, capture_seq: next.capture_seq, captured_at: next.captured_at, sha256: hash };
    await env.BUCKET.put(selected.key, frame, { httpMetadata: { contentType: "image/jpeg", cacheControl: "no-store" } });
  } else if (next.capture_seq !== null && next.capture_seq !== current.value?.capture_seq) {
    throw new ApiError(400, "A new capture requires its JPEG");
  }
  const previous = [current.value?._frame, ...(current.value?._previous_frames || [])].filter((item, index, items) => item && item.key !== selected?.key && items.findIndex((candidate) => candidate?.key === item.key) === index);
  const saved = { ...next, received_at: new Date().toISOString(), _frame: selected, _previous_frames: previous.slice(0, 2) };
  const committed = await env.BUCKET.put(STATE_KEY, JSON.stringify(saved), {
    httpMetadata: { contentType: "application/json", cacheControl: "no-store" },
    onlyIf: current.item ? { etagMatches: current.item.etag } : { etagDoesNotMatch: "*" },
  });
  if (!committed) throw new ApiError(409, "A newer publisher update won the race");
  // Old frame cleanup happens only after metadata references the committed version.
  for (const obsolete of previous.slice(2)) {
    try { await env.BUCKET.delete(obsolete.key); } catch { /* Retention cleanup must not lose a committed update. */ }
  }
  return json({ ok: true, capture_seq: saved.capture_seq, captured_at: saved.captured_at, updated_at: saved.updated_at });
}

async function frame(request, env) {
  const { value } = await storedState(env.BUCKET);
  const requested = new URL(request.url).searchParams.get("seq");
  let selected = value?._frame;
  if (requested !== null) {
    if (!/^\d{1,16}$/.test(requested) || !Number.isSafeInteger(Number(requested))) throw new ApiError(400, "Invalid capture sequence");
    selected = [selected, ...(value?._previous_frames || [])].find((item) => item?.capture_seq === Number(requested));
  }
  if (!selected) throw new ApiError(404, "No captured frame is available");
  const item = await env.BUCKET.get(selected.key);
  if (!item) throw new ApiError(404, "Captured frame is unavailable");
  return new Response(item.body, { headers: { "Content-Type": "image/jpeg", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "X-Capture-Sequence": String(selected.capture_seq), "X-Captured-At": selected.captured_at } });
}

async function run(request, env, id) {
  if (!RUN_ID.test(id)) throw new ApiError(400, "Invalid run ID");
  const key = `observer/runs/${id}.json`;
  if (request.method === "GET") {
    const item = await env.BUCKET.get(key);
    if (!item) throw new ApiError(404, "Run is unavailable");
    return new Response(item.body, { headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" } });
  }
  await authorized(request, env);
  if (!request.headers.get("Content-Type")?.toLowerCase().startsWith("application/json")) throw new ApiError(415, "Expected JSON");
  const raw = await bytes(request, MAX_RUN);
  let text;
  try { text = new TextDecoder("utf-8", { fatal: true }).decode(raw); } catch { throw new ApiError(400, "Invalid UTF-8 JSON"); }
  const payload = parseJson(text);
  knownFields(payload, new Set(["summary", "events", "engineering"]), "run");
  const clean = { summary: summary(payload.summary, env.INGEST_TOKEN, id), events: events(payload.events, env.INGEST_TOKEN) };
  if (payload.engineering !== undefined) clean.engineering = engineering(payload.engineering, env, id);
  await env.BUCKET.put(key, JSON.stringify(clean), { httpMetadata: { contentType: "application/json", cacheControl: "no-store" } });
  return json({ ok: true, id, events: clean.events.length });
}

export async function handleApi(request, env) {
  const path = new URL(request.url).pathname;
  // Retired completely: old passwords/configuration never issue a capability.
  if (path === "/api/operator/login") return json({error: "Not found"}, 404);
  if (path.startsWith("/api/live/")) return handleVideo(request, env);
  const runMatch = path.match(/^\/api\/runs\/([^/]+)$/);
  const commandMatch = path.match(/^\/api\/commands\/([^/]+)(\/result)?$/);
  if (!["/api/state", "/api/capture", "/api/frame.jpg", "/api/ingest", "/api/commands", "/api/commands/next", "/api/voice/session"].includes(path) && !runMatch && !commandMatch) return null;
  const allowed = ["/api/ingest", "/api/commands", "/api/voice/session"].includes(path) || commandMatch?.[2] ? ["POST"] : runMatch ? ["GET", "POST"] : ["GET"];
  if (!allowed.includes(request.method)) return json({ error: "Method not allowed" }, 405, { Allow: allowed.join(", ") });
  try {
    if (!env.BUCKET) throw new ApiError(503, "Observer storage is unavailable");
    if (path === "/api/voice/session") return await voiceSession(request, env);
    if (path === "/api/state") return json(publicState((await storedState(env.BUCKET)).value));
    if (path === "/api/capture") {
      const value = publicState((await storedState(env.BUCKET)).value);
      return json(Object.fromEntries(["updated_at", "captured_at", "capture_seq", "feed_status", "source"].map((key) => [key, value[key]])));
    }
    if (path === "/api/frame.jpg") return await frame(request, env);
    if (path === "/api/ingest") return await ingest(request, env);
    if (path === "/api/commands") return await createCommand(request, env);
    if (path === "/api/commands/next") return await nextCommand(request, env);
    if (commandMatch) return commandMatch[2] ? await commandResult(request, env, commandMatch[1]) : await readCommand(request, env, commandMatch[1]);
    let id;
    try { id = decodeURIComponent(runMatch[1]); } catch { throw new ApiError(400, "Invalid run ID"); }
    return await run(request, env, id);
  } catch (error) {
    return json({ error: error instanceof ApiError ? error.message : "Observer request failed" }, error instanceof ApiError ? error.status : 500, error instanceof ApiError ? error.headers : {});
  }
}
